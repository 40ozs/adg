r"""Change plans over HTTP: write one, measure it, get it approved, sign it, hand it over.

Twelve operations and **no execution route**. That absence is the design, not an omission, so
it is stated here where somebody adding a thirteenth will read it.

## There is no POST that changes Windows

``app.remediation.executor`` defines an interface. Nothing in this module constructs a
remediator except :func:`execution_policy`, which calls ``describe()`` — the half that cannot
write — so that an operator can ask the running process whether it could change anything and
be told no, with the reasons. ``tests/remediation/test_no_write_path.py`` walks this module's
syntax tree and fails if an execution call appears in it.

The consequence worth spelling out: the most dangerous thing any route here produces is a
**document**. Signing it is the last act, and the signature says the document came from this
deployment unmodified — not that anybody did what it says.

## Three capabilities, and none of them overlap by role

``remediation:read`` reads. ``remediation:plan`` writes plans and submits them.
``remediation:approve`` answers them. ``remediation:export`` produces the signed instruction.
No role holds more than one of the last three (``app/auth/roles.py``), and the service checks
the *person* as well: the requestor cannot approve, and neither the requestor nor the approver
can export. A person legitimately holding two roles is still refused, because the rule is
about hands rather than about tokens. See ADR-0038.

## Preconditions are a field, not a 404

Every plan response carries its precondition report — whether each change still describes the
estate ADG last observed. It is computed on read and never written back (the argument is
ADR-0031's, applied to a plan rather than a review item), and it is reported rather than
enforced on a read: a plan whose entries have moved is still the plan somebody wrote, and
withholding it would take away the one screen that explains why the export was refused.

Where it *is* enforced is on the three routes that lead to action: submit, approve, export.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import AwareDatetime, BaseModel, Field

from app.api.deps import PRINTABLE_IDENTIFIER, RequestSettings, Session

# PageInfo carries limit/has_more/next_cursor/total and no offset -- these two routes page
# by offset, so the caller's own offset is the only place it is recorded. Passing one here
# type-checks under no configuration and is silently dropped by Pydantic at runtime, which
# is how a response comes to describe a field it does not contain.
from app.api.pagination import PageInfo, normalize_limit
from app.auth.dependencies import CurrentPrincipal, requires
from app.auth.principal import AuthenticatedPrincipal
from app.auth.roles import Capability
from app.config import Settings
from app.domain import (
    AceSource,
    AceType,
    ApprovalDecision,
    ChangePlanStatus,
    ChangeTargetKind,
    DecisionKind,
    MembershipEdgeKind,
    PlannedChangeKind,
    PrincipalKind,
    ReviewTargetKind,
    SharePermission,
)
from app.governance.service import Actor
from app.remediation.export import render_runbook
from app.remediation.model import (
    MAX_PLAN_CHANGES,
    MAX_RATIONALE_LENGTH,
    MAX_TITLE_LENGTH,
    ApprovalRecord,
    ChangePlan,
    EntrySnapshot,
    MembershipSnapshot,
    PlanExport,
    PlannedChange,
    PreconditionReport,
)
from app.remediation.preconditions import summarize_verdict
from app.remediation.repository import PlanCandidate
from app.remediation.service import RemediationService
from app.simulation.describe import NON_DESTRUCTIVE_NOTICE

router = APIRouter(prefix="/api/v1/remediation", tags=["remediation"])

#: Reading plans, their steps, their blast radius, their approvals and their exports. Held
#: from ``auditor`` upward and deliberately not by a plain viewer: a plan says who proposed
#: taking somebody's access away and why, which is the same disclosure class as a decision
#: rationale.
READ = Depends(requires(Capability.REMEDIATION_READ))

#: Writing a plan and submitting it. Writes nothing to Windows -- a plan is a document -- but
#: it decides what somebody will be asked to approve.
PLAN = Depends(requires(Capability.REMEDIATION_PLAN))

#: Answering one. Held by no role that holds PLAN.
APPROVE = Depends(requires(Capability.REMEDIATION_APPROVE))

#: Producing the signed instruction. A third pair of hands again.
EXPORT = Depends(requires(Capability.REMEDIATION_EXPORT))

PlanIdPath = Annotated[UUID, Path(description="The change plan's identifier.")]
ExportIdPath = Annotated[UUID, Path(description="The export's identifier.")]

#: Every free-text key that reaches a query. Phase 6D found that a NUL byte survives every
#: parser and reaches psycopg as an error that renders as a 500; these are refused before the
#: round trip.
KeyString = Annotated[str, Field(min_length=1, max_length=512, pattern=PRINTABLE_IDENTIFIER)]
SidString = Annotated[str, Field(min_length=1, max_length=200, pattern=PRINTABLE_IDENTIFIER)]


def _actor(principal: AuthenticatedPrincipal) -> Actor:
    """The caller, as the audit trail will record them.

    Roles are captured here, from the verified token, rather than looked up later: "Dana
    approved this" and "Dana, who then held the remediation_approver role, approved this" are
    different claims, and only the second survives her assignments being changed.
    """
    return Actor(
        subject=principal.subject,
        display_name=principal.display_name,
        email=principal.email,
        roles=tuple(sorted(role.value for role in principal.roles)),
    )


def _service(session: Session, settings: Settings) -> RemediationService:
    return RemediationService(session, settings)


# ------------------------------------------------------------------------------ requests


class EntryBody(BaseModel):
    """The access-control entry a change acts on, as ADG observed it.

    Supplied by the caller rather than looked up by the server, deliberately. A plan is a
    statement about a specific entry that a specific person read, and a server that re-read
    the ACL at plan time would produce a plan whose before-state nobody had actually seen —
    which is precisely the gap the precondition check exists to close, reintroduced one layer
    up.
    """

    ace_key: KeyString
    trustee_sid: SidString
    trustee_key: KeyString
    ace_type: AceType
    access_mask: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    permission: SharePermission | None = None
    ace_flags: int | None = Field(default=None, ge=0, le=0xFF)
    source: Literal["explicit", "inherited"] | None = None
    inherited_from: str | None = Field(default=None, max_length=512)
    order_index: int | None = Field(default=None, ge=0)
    version_id: int | None = None
    observed_from: AwareDatetime | None = None
    last_confirmed_at: AwareDatetime | None = None


class MembershipBody(BaseModel):
    group_key: KeyString
    member_key: KeyString
    member_sid: SidString
    edge_kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
    member_kind: PrincipalKind | None = None
    group_display_name: str | None = Field(default=None, max_length=512)
    member_display_name: str | None = Field(default=None, max_length=512)


class ChangeBody(BaseModel):
    kind: PlannedChangeKind
    target_kind: ChangeTargetKind
    target_key: KeyString
    target_display: str | None = Field(default=None, max_length=512)
    principal_sid: SidString
    principal_key: KeyString
    principal_display_name: str | None = Field(default=None, max_length=512)
    entry: EntryBody | None = None
    membership: MembershipBody | None = None
    after_access_mask: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    after_permission: SharePermission | None = None
    replacement_group_key: KeyString | None = None
    replacement_group_sid: SidString | None = None
    replacement_group_display_name: str | None = Field(default=None, max_length=512)
    item_id: UUID | None = None
    decision_id: UUID | None = None
    proposal_id: UUID | None = None
    risk_finding_key: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2_000)


class CreatePlanRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    campaign_id: UUID | None = None
    changes: list[ChangeBody] = Field(min_length=1, max_length=MAX_PLAN_CHANGES)


class ReplaceChangesRequest(BaseModel):
    changes: list[ChangeBody] = Field(min_length=1, max_length=MAX_PLAN_CHANGES)


class DecisionRequest(BaseModel):
    decision: ApprovalDecision
    rationale: str | None = Field(default=None, max_length=MAX_RATIONALE_LENGTH)


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=MAX_RATIONALE_LENGTH)


# ----------------------------------------------------------------------------- responses


class EntryView(BaseModel):
    ace_key: str
    trustee_sid: str
    trustee_key: str
    ace_type: AceType
    access_mask: int | None
    permission: SharePermission | None
    ace_flags: int | None
    source: str | None
    inherited_from: str | None
    order_index: int | None
    content_digest: str
    version_id: int | None
    observed_from: dt.datetime | None
    last_confirmed_at: dt.datetime | None


class MembershipView(BaseModel):
    group_key: str
    member_key: str
    member_sid: str
    edge_kind: MembershipEdgeKind
    member_kind: PrincipalKind | None
    group_display_name: str | None
    member_display_name: str | None
    is_local_group: bool
    host: str | None
    content_digest: str


class ChangeView(BaseModel):
    change_id: UUID
    sequence_index: int
    kind: PlannedChangeKind
    target_kind: ChangeTargetKind
    target_key: str
    target_display: str | None
    principal_sid: str
    principal_key: str
    principal_display_name: str | None
    entry: EntryView | None
    membership: MembershipView | None
    after_access_mask: int | None
    after_permission: SharePermission | None
    replacement_group_key: str | None
    replacement_group_sid: str | None
    replacement_group_display_name: str | None
    item_id: UUID | None
    decision_id: UUID | None
    proposal_id: UUID | None
    risk_finding_key: str | None
    notes: str | None
    precondition_digest: str


class PreconditionView(BaseModel):
    change_id: UUID
    sequence_index: int
    verdict: str
    expected_digest: str
    observed_digest: str | None
    summary: str
    blocks_export: bool
    detail: dict[str, Any]


class PreconditionReportView(BaseModel):
    checked_at: dt.datetime
    basis_token: str
    plan_basis_token: str
    basis_moved: bool
    satisfied: bool
    summary: str
    counts: dict[str, int]
    preconditions: list[PreconditionView]


class ApprovalView(BaseModel):
    approval_id: UUID
    decision: ApprovalDecision
    approver_subject: str
    approver_display_name: str | None
    approver_roles: list[str]
    decided_at: dt.datetime
    rationale: str | None
    plan_digest: str
    basis_token: str
    simulation_id: UUID | None


class PlanView(BaseModel):
    plan_id: UUID
    title: str
    rationale: str
    status: ChangePlanStatus
    plan_digest: str
    campaign_id: UUID | None
    requested_by_subject: str
    requested_by_display_name: str | None
    requested_at: dt.datetime
    basis_token: str
    basis_run_id: str | None
    simulation_id: UUID | None
    simulation_basis_token: str | None
    has_current_simulation: bool
    impact_summary: dict[str, Any]
    changes: list[ChangeView]
    submitted_at: dt.datetime | None
    decided_at: dt.datetime | None
    approved_by_subject: str | None
    approved_plan_digest: str | None
    approval_is_current: bool
    rejection_reason: str | None
    exported_at: dt.datetime | None
    invalidated_at: dt.datetime | None
    invalidation_reason: str | None
    canceled_at: dt.datetime | None
    canceled_by_subject: str | None
    notice: str = NON_DESTRUCTIVE_NOTICE
    """Rendered on every plan payload rather than left to the client to remember. A client
    that shows it in small grey text has still been given the sentence."""


class PlanDetailView(PlanView):
    preconditions: PreconditionReportView
    approvals: list[ApprovalView]


class PlanPage(BaseModel):
    plans: list[PlanView]
    page: PageInfo


class CandidateView(BaseModel):
    """A decision asking for a change that no plan covers yet."""

    item_id: UUID
    campaign_id: UUID
    decision_id: UUID
    decision: DecisionKind
    decided_at: dt.datetime
    rationale: str | None
    target_kind: ReviewTargetKind
    target_key: str
    target_path: str | None
    principal_key: str
    principal_sid: str
    entry_count: int
    ace_keys: list[str]


class CandidatePage(BaseModel):
    candidates: list[CandidateView]
    page: PageInfo


class ExportView(BaseModel):
    export_id: UUID
    plan_id: UUID
    exported_by_subject: str
    exported_by_display_name: str | None
    exported_at: dt.datetime
    document_digest: str
    signature: str
    signature_algorithm: str
    signature_key_id: str
    basis_token: str
    audit_head_digest: str | None


class ExportDetailView(ExportView):
    document: dict[str, Any]
    runbook: str | None = None
    """The PowerShell an administrator runs. Present on the response that creates an export
    and on the one that reads it back; regenerated from the stored document each time rather
    than saved, so what somebody runs comes from the current renderer."""


class AuditEventView(BaseModel):
    chain_index: int
    event_type: str
    occurred_at: dt.datetime
    actor_subject: str
    actor_display_name: str | None
    actor_roles: list[str]
    payload: dict[str, Any]
    digest: str


class AuditView(BaseModel):
    chain_key: str
    length: int
    intact: bool
    head_digest: str | None
    broken_at: int | None
    reason: str | None
    events: list[AuditEventView]
    caveat: str = (
        "The chain makes a quiet edit impossible, not a determined one. Anybody able to "
        "rewrite rows can recompute every digest after the one they changed, and the result "
        "verifies. Compare head_digest against the value recorded in an export or a change "
        "ticket, which is outside this database."
    )


class ExecutionPolicyView(BaseModel):
    """What this deployment can do to Windows. The answer is nothing, with reasons."""

    mode: str
    can_execute: bool
    adapter: str
    reason: str
    requirements: list[str]


# -------------------------------------------------------------------------------- routes


@router.get(
    "/execution-policy",
    response_model=ExecutionPolicyView,
    summary="Whether this deployment can change Windows",
    dependencies=[READ],
)
async def execution_policy(
    session: Session,
    settings: RequestSettings,
) -> ExecutionPolicyView:
    """Ask the running process, rather than the documentation.

    An operator's real question is "could this thing modify my domain?", and a guarantee that
    can only be checked by reading source code is one most people will not check. This answers
    from the executor the process actually holds.
    """
    description = _service(session, settings).execution_policy()
    return ExecutionPolicyView(**description.document())


@router.get(
    "/candidates",
    response_model=CandidatePage,
    summary="Review decisions asking for a change that no plan covers",
    dependencies=[READ],
)
async def candidates(
    session: Session,
    settings: RequestSettings,
    campaign_id: Annotated[UUID | None, Query(description="Restrict to one campaign.")] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CandidatePage:
    """ "Decided in March, and nothing has happened since" — the outstanding work.

    Superseded decisions are excluded: a reviewer who changed their mind has not left work
    outstanding. A decision any plan cites is excluded whatever that plan's status, because a
    rejected or cancelled plan still means somebody looked, and listing the decision again
    would send the next planner to write it a second time.
    """
    capped = normalize_limit(limit)
    found, more = await _service(session, settings).candidates(
        campaign_id=campaign_id, limit=capped, offset=offset
    )
    return CandidatePage(
        candidates=[_candidate_view(item) for item in found],
        page=PageInfo(limit=capped, has_more=more),
    )


@router.post(
    "",
    response_model=PlanDetailView,
    status_code=status.HTTP_201_CREATED,
    summary="Write a change plan",
    dependencies=[PLAN],
    responses={
        422: {
            "description": (
                "A change would widen access rather than narrow it, two steps act on one "
                "object, the step numbers have a gap, or the plan has no rationale."
            )
        }
    },
)
async def create_plan(
    body: CreatePlanRequest,
    session: Session,
    principal: CurrentPrincipal,
    settings: RequestSettings,
) -> PlanDetailView:
    """A draft. Nothing is measured, nothing is approved, and nothing is changed in Windows."""
    service = _service(session, settings)
    plan = await service.create_plan(
        _actor(principal),
        title=body.title,
        rationale=body.rationale,
        changes=[_change_from(index, item) for index, item in enumerate(body.changes)],
        campaign_id=body.campaign_id,
    )
    return await _detail(service, plan)


@router.get(
    "",
    response_model=PlanPage,
    summary="List change plans",
    dependencies=[READ],
)
async def list_plans(
    session: Session,
    settings: RequestSettings,
    plan_status: Annotated[
        ChangePlanStatus | None, Query(alias="status", description="Restrict to one status.")
    ] = None,
    campaign_id: Annotated[UUID | None, Query()] = None,
    requested_by: Annotated[str | None, Query(max_length=320)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PlanPage:
    capped = normalize_limit(limit)
    plans, more = await _service(session, settings).list_plans(
        status=plan_status,
        campaign_id=campaign_id,
        requested_by=requested_by,
        limit=capped,
        offset=offset,
    )
    return PlanPage(
        plans=[_plan_view(plan) for plan in plans],
        page=PageInfo(limit=capped, has_more=more),
    )


@router.get(
    "/{plan_id}",
    response_model=PlanDetailView,
    summary="One plan, with its preconditions and approvals",
    dependencies=[READ],
    responses={404: {"description": "No such plan."}},
)
async def get_plan(
    plan_id: PlanIdPath,
    session: Session,
    settings: RequestSettings,
) -> PlanDetailView:
    """The preconditions are computed now and never written back. See the module docstring."""
    service = _service(session, settings)
    return await _detail(service, await service.get_plan(plan_id))


@router.put(
    "/{plan_id}/changes",
    response_model=PlanDetailView,
    summary="Rewrite a draft's steps",
    dependencies=[PLAN],
    responses={
        403: {"description": "The plan was written by somebody else."},
        409: {"description": "The plan is no longer a draft."},
    },
)
async def replace_changes(
    plan_id: PlanIdPath,
    body: ReplaceChangesRequest,
    session: Session,
    principal: CurrentPrincipal,
    settings: RequestSettings,
) -> PlanDetailView:
    """Editing detaches any blast-radius report: it described the steps that just went away."""
    service = _service(session, settings)
    plan = await service.replace_changes(
        _actor(principal),
        plan_id,
        [_change_from(index, item) for index, item in enumerate(body.changes)],
    )
    return await _detail(service, plan)


@router.post(
    "/{plan_id}/simulation",
    response_model=PlanDetailView,
    summary="Measure what the plan would do",
    dependencies=[PLAN],
    responses={409: {"description": "The plan has been approved, rejected or exported."}},
)
async def simulate(
    plan_id: PlanIdPath,
    session: Session,
    principal: CurrentPrincipal,
    settings: RequestSettings,
) -> PlanDetailView:
    """Runs the production access engine twice, over the plan translated to an overlay.

    The report is stored as an ordinary simulation, so the impact behind an approval is
    something somebody can open at ``/api/v1/simulations/{id}``, re-run, and compare — not a
    number copied into a plan (ADR-0037).
    """
    service = _service(session, settings)
    simulated = await service.simulate(_actor(principal), plan_id)
    return await _detail(service, simulated.plan)


@router.post(
    "/{plan_id}/submission",
    response_model=PlanDetailView,
    summary="Submit the plan for approval",
    dependencies=[PLAN],
    responses={
        403: {"description": "The plan was written by somebody else."},
        409: {
            "description": (
                "No current blast-radius report, or a change no longer describes the estate."
            )
        },
    },
)
async def submit(
    plan_id: PlanIdPath,
    session: Session,
    principal: CurrentPrincipal,
    settings: RequestSettings,
) -> PlanDetailView:
    service = _service(session, settings)
    plan, _ = await service.submit(_actor(principal), plan_id)
    return await _detail(service, plan)


@router.post(
    "/{plan_id}/approval",
    response_model=PlanDetailView,
    summary="Approve or reject a plan",
    dependencies=[APPROVE],
    responses={
        403: {"description": "The caller requested this plan and cannot also approve it."},
        409: {
            "description": (
                "The plan is not awaiting approval, or its preconditions no longer hold — in "
                "which case it is invalidated rather than approved."
            )
        },
        422: {"description": "A rejection with no reason."},
    },
)
async def decide(
    plan_id: PlanIdPath,
    body: DecisionRequest,
    session: Session,
    principal: CurrentPrincipal,
    settings: RequestSettings,
) -> PlanDetailView:
    """The approval records the plan digest and the basis it answered against (ADR-0036)."""
    service = _service(session, settings)
    plan = await service.decide(
        _actor(principal), plan_id, decision=body.decision, rationale=body.rationale
    )
    return await _detail(service, plan)


@router.post(
    "/{plan_id}/exports",
    response_model=ExportDetailView,
    status_code=status.HTTP_201_CREATED,
    summary="Produce the signed change plan and its runbook",
    dependencies=[EXPORT],
    responses={
        403: {"description": "The caller requested or approved this plan."},
        409: {
            "description": (
                "The plan is not approved, the approval covers a different version of it, a "
                "precondition no longer holds, or this deployment has no signing key."
            )
        },
    },
)
async def create_export(
    plan_id: PlanIdPath,
    session: Session,
    principal: CurrentPrincipal,
    settings: RequestSettings,
) -> ExportDetailView:
    """The last act. A signed document and a script — and still nothing applied by ADG."""
    export, runbook = await _service(session, settings).export(_actor(principal), plan_id)
    return _export_detail(export, runbook)


@router.get(
    "/{plan_id}/exports",
    response_model=list[ExportView],
    summary="Every signed export of this plan",
    dependencies=[READ],
)
async def list_exports(
    plan_id: PlanIdPath,
    session: Session,
    settings: RequestSettings,
) -> list[ExportView]:
    return [_export_view(export) for export in await _service(session, settings).exports(plan_id)]


@router.get(
    "/{plan_id}/exports/{export_id}",
    response_model=ExportDetailView,
    summary="One signed export, with its document and runbook",
    dependencies=[READ],
    responses={404: {"description": "No such export on this plan."}},
)
async def get_export(
    plan_id: PlanIdPath,
    export_id: ExportIdPath,
    session: Session,
    settings: RequestSettings,
) -> ExportDetailView:
    """The document is served as it was signed. The runbook is re-rendered from the plan."""
    return await _export_with_runbook(_service(session, settings), plan_id, export_id)


@router.get(
    "/{plan_id}/exports/{export_id}/runbook",
    response_class=Response,
    summary="The runbook alone, as a PowerShell file",
    dependencies=[READ],
    responses={
        200: {"content": {"text/plain": {}}, "description": "A PowerShell script."},
        404: {"description": "No such export on this plan."},
    },
)
async def get_runbook(
    plan_id: PlanIdPath,
    export_id: ExportIdPath,
    session: Session,
    settings: RequestSettings,
) -> Response:
    """Plain text, so an administrator can save it and run it.

    ``text/plain`` rather than a download type, and no ``Content-Disposition``: a change plan
    that a browser saves and offers to run on a double-click is exactly the artifact the
    ``-Execute`` switch exists to defuse, and there is no reason to help it along.
    """
    detail = await _export_with_runbook(_service(session, settings), plan_id, export_id)
    return Response(content=detail.runbook or "", media_type="text/plain; charset=utf-8")


@router.get(
    "/{plan_id}/audit",
    response_model=AuditView,
    summary="The plan's audit chain, and whether it is intact",
    dependencies=[READ],
)
async def audit(
    plan_id: PlanIdPath,
    session: Session,
    settings: RequestSettings,
) -> AuditView:
    events, verification = await _service(session, settings).audit(plan_id)
    return AuditView(
        chain_key=f"plan:{plan_id}",
        length=verification.length,
        intact=verification.intact,
        head_digest=verification.head_digest,
        broken_at=verification.broken_at,
        reason=verification.reason,
        events=[
            AuditEventView(
                chain_index=event.chain_index,
                event_type=event.event_type.value,
                occurred_at=event.occurred_at,
                actor_subject=event.actor_subject,
                actor_display_name=event.actor_display_name,
                actor_roles=list(event.actor_roles),
                payload=event.payload,
                digest=event.digest,
            )
            for event in events
        ],
    )


@router.post(
    "/{plan_id}/cancellation",
    response_model=PlanDetailView,
    summary="Abandon a plan",
    dependencies=[PLAN],
    responses={409: {"description": "The plan has already reached a terminal state."}},
)
async def cancel(
    plan_id: PlanIdPath,
    body: CancelRequest,
    session: Session,
    principal: CurrentPrincipal,
    settings: RequestSettings,
) -> PlanDetailView:
    """Everything the plan said and every answer it got stay readable."""
    service = _service(session, settings)
    plan = await service.cancel(_actor(principal), plan_id, reason=body.reason)
    return await _detail(service, plan)


# ------------------------------------------------------------------------------- views


def _change_from(index: int, body: ChangeBody) -> PlannedChange:
    """One request change, validated by the domain rather than by the request model.

    Pydantic checks shapes; :class:`app.remediation.model.PlannedChange` checks meaning — that
    a modification narrows, that a membership change carries its edge, that a replacement
    names its group. Doing it here means the same rules apply to a plan written by a test, a
    script, or a future UI, rather than only to one that arrived over HTTP.

    The step number is the position in the submitted list. A caller does not choose it: gaps
    and repeats are the two ways an ordered runbook silently loses a step, and both are
    unrepresentable if the server assigns them.
    """
    return PlannedChange(
        change_id=uuid4(),
        sequence_index=index,
        kind=body.kind,
        target_kind=body.target_kind,
        target_key=body.target_key,
        target_display=body.target_display,
        principal_sid=body.principal_sid,
        principal_key=body.principal_key,
        principal_display_name=body.principal_display_name,
        entry=None if body.entry is None else _entry_from(body.entry),
        membership=None if body.membership is None else _membership_from(body.membership),
        after_access_mask=body.after_access_mask,
        after_permission=body.after_permission,
        replacement_group_key=body.replacement_group_key,
        replacement_group_sid=body.replacement_group_sid,
        replacement_group_display_name=body.replacement_group_display_name,
        item_id=body.item_id,
        decision_id=body.decision_id,
        proposal_id=body.proposal_id,
        risk_finding_key=body.risk_finding_key,
        notes=body.notes,
    )


def _entry_from(body: EntryBody) -> EntrySnapshot:
    return EntrySnapshot(
        ace_key=body.ace_key,
        trustee_sid=body.trustee_sid,
        trustee_key=body.trustee_key,
        ace_type=body.ace_type,
        access_mask=body.access_mask,
        permission=body.permission,
        ace_flags=body.ace_flags,
        source=None if body.source is None else AceSource(body.source),
        inherited_from=body.inherited_from,
        order_index=body.order_index,
        version_id=body.version_id,
        observed_from=body.observed_from,
        last_confirmed_at=body.last_confirmed_at,
    )


def _membership_from(body: MembershipBody) -> MembershipSnapshot:
    return MembershipSnapshot(
        group_key=body.group_key,
        member_key=body.member_key,
        member_sid=body.member_sid,
        edge_kind=body.edge_kind,
        member_kind=body.member_kind,
        group_display_name=body.group_display_name,
        member_display_name=body.member_display_name,
    )


def _entry_view(entry: EntrySnapshot) -> EntryView:
    return EntryView(
        ace_key=entry.ace_key,
        trustee_sid=entry.trustee_sid,
        trustee_key=entry.trustee_key,
        ace_type=entry.ace_type,
        access_mask=entry.access_mask,
        permission=entry.permission,
        ace_flags=entry.ace_flags,
        source=None if entry.source is None else entry.source.value,
        inherited_from=entry.inherited_from,
        order_index=entry.order_index,
        content_digest=entry.content_digest,
        version_id=entry.version_id,
        observed_from=entry.observed_from,
        last_confirmed_at=entry.last_confirmed_at,
    )


def _membership_view(edge: MembershipSnapshot) -> MembershipView:
    return MembershipView(
        group_key=edge.group_key,
        member_key=edge.member_key,
        member_sid=edge.member_sid,
        edge_kind=edge.edge_kind,
        member_kind=edge.member_kind,
        group_display_name=edge.group_display_name,
        member_display_name=edge.member_display_name,
        # Rendered rather than left to the client to derive from the key's shape. Which
        # machine a step is performed on is not a presentational detail.
        is_local_group=edge.is_local,
        host=edge.host_key,
        content_digest=edge.content_digest,
    )


def _change_view(change: PlannedChange) -> ChangeView:
    return ChangeView(
        change_id=change.change_id,
        sequence_index=change.sequence_index,
        kind=change.kind,
        target_kind=change.target_kind,
        target_key=change.target_key,
        target_display=change.target_display,
        principal_sid=change.principal_sid,
        principal_key=change.principal_key,
        principal_display_name=change.principal_display_name,
        entry=None if change.entry is None else _entry_view(change.entry),
        membership=None if change.membership is None else _membership_view(change.membership),
        after_access_mask=change.after_access_mask,
        after_permission=change.after_permission,
        replacement_group_key=change.replacement_group_key,
        replacement_group_sid=change.replacement_group_sid,
        replacement_group_display_name=change.replacement_group_display_name,
        item_id=change.item_id,
        decision_id=change.decision_id,
        proposal_id=change.proposal_id,
        risk_finding_key=change.risk_finding_key,
        notes=change.notes,
        precondition_digest=change.precondition_digest,
    )


def _plan_view(plan: ChangePlan) -> PlanView:
    return PlanView(
        plan_id=plan.plan_id,
        title=plan.title,
        rationale=plan.rationale,
        status=plan.status,
        plan_digest=plan.digest,
        campaign_id=plan.campaign_id,
        requested_by_subject=plan.requested_by_subject,
        requested_by_display_name=plan.requested_by_display_name,
        requested_at=plan.requested_at,
        basis_token=plan.basis_token,
        basis_run_id=plan.basis_run_id,
        simulation_id=plan.simulation_id,
        simulation_basis_token=plan.simulation_basis_token,
        has_current_simulation=plan.has_current_simulation,
        impact_summary=dict(plan.impact_summary),
        changes=[_change_view(change) for change in plan.changes],
        submitted_at=plan.submitted_at,
        decided_at=plan.decided_at,
        approved_by_subject=plan.approved_by_subject,
        approved_plan_digest=plan.approved_plan_digest,
        approval_is_current=plan.approval_is_current,
        rejection_reason=plan.rejection_reason,
        exported_at=plan.exported_at,
        invalidated_at=plan.invalidated_at,
        invalidation_reason=plan.invalidation_reason,
        canceled_at=plan.canceled_at,
        canceled_by_subject=plan.canceled_by_subject,
    )


def _precondition_view(report: PreconditionReport) -> PreconditionReportView:
    return PreconditionReportView(
        checked_at=report.checked_at,
        basis_token=report.basis_token,
        plan_basis_token=report.plan_basis_token,
        basis_moved=report.basis_moved,
        satisfied=report.satisfied,
        # The sentence is built on the server so that four verdicts a renderer might map to
        # three colors cannot collapse "it is gone" into "nobody has looked".
        summary=summarize_verdict(report),
        counts=report.counts(),
        preconditions=[
            PreconditionView(
                change_id=item.change_id,
                sequence_index=item.sequence_index,
                verdict=item.verdict.value,
                expected_digest=item.expected_digest,
                observed_digest=item.observed_digest,
                summary=item.summary,
                blocks_export=item.blocks_export,
                detail=dict(item.detail),
            )
            for item in report.preconditions
        ],
    )


def _approval_view(approval: ApprovalRecord) -> ApprovalView:
    return ApprovalView(
        approval_id=approval.approval_id,
        decision=approval.decision,
        approver_subject=approval.approver_subject,
        approver_display_name=approval.approver_display_name,
        approver_roles=list(approval.approver_roles),
        decided_at=approval.decided_at,
        rationale=approval.rationale,
        plan_digest=approval.plan_digest,
        basis_token=approval.basis_token,
        simulation_id=approval.simulation_id,
    )


def _candidate_view(candidate: PlanCandidate) -> CandidateView:
    return CandidateView(
        item_id=candidate.item_id,
        campaign_id=candidate.campaign_id,
        decision_id=candidate.decision_id,
        decision=candidate.decision,
        decided_at=candidate.decided_at,
        rationale=candidate.rationale,
        target_kind=candidate.target_kind,
        target_key=candidate.target_key,
        target_path=candidate.target_path,
        principal_key=candidate.principal_key,
        principal_sid=candidate.principal_sid,
        entry_count=len(candidate.grants),
        # The entries the *frozen evidence* named, not whatever the ACL holds now. A planner
        # building from current state would silently retarget the plan between the review and
        # the change window -- the same rule GovernanceService.propose_remediation follows.
        ace_keys=[str(grant.get("ace_key")) for grant in candidate.grants if grant.get("ace_key")],
    )


def _export_view(export: PlanExport) -> ExportView:
    return ExportView(
        export_id=export.export_id,
        plan_id=export.plan_id,
        exported_by_subject=export.exported_by_subject,
        exported_by_display_name=export.exported_by_display_name,
        exported_at=export.exported_at,
        document_digest=export.document_digest,
        signature=export.signature,
        signature_algorithm=export.signature_algorithm,
        signature_key_id=export.signature_key_id,
        basis_token=export.basis_token,
        audit_head_digest=export.audit_head_digest,
    )


def _export_detail(export: PlanExport, runbook: str) -> ExportDetailView:
    return ExportDetailView(
        **_export_view(export).model_dump(),
        document=dict(export.document),
        runbook=runbook,
    )


async def _export_with_runbook(
    service: RemediationService, plan_id: UUID, export_id: UUID
) -> ExportDetailView:
    """The stored document, plus a runbook rendered from the plan as it stands.

    The document is served byte for byte as it was signed; the script is regenerated. They
    are different kinds of artifact: the first is what a signature covers, the second is what
    somebody runs, and the second must come from the current renderer rather than from a copy
    that stopped improving on the day it was written.
    """
    export = await service.get_export(plan_id, export_id)
    plan = await service.get_plan(plan_id)
    return _export_detail(
        export,
        render_runbook(
            plan,
            document_digest_value=export.document_digest,
            signature=export.signature,
            key_id=export.signature_key_id,
            approver=plan.approved_by_subject,
        ),
    )


async def _detail(service: RemediationService, plan: ChangePlan) -> PlanDetailView:
    report = await service.preconditions(plan)
    approvals = await service.approvals(plan.plan_id)
    return PlanDetailView(
        **_plan_view(plan).model_dump(),
        preconditions=_precondition_view(report),
        approvals=[_approval_view(approval) for approval in approvals],
    )
