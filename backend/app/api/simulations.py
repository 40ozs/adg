r"""What-if proposals: the HTTP surface over Phase 9A's engine.

Phase 9A built a simulation engine with no route, deliberately, and left three questions for
whichever phase specified one. This module answers all three, and the answers are the whole
of its design.

**Is a simulation a POST that computes, or a POST that stores?** Both, as two routes, because
they are two different acts. ``POST /preview`` computes an answer and keeps nothing: it is
what the proposal editor calls while somebody is still typing, and a draft that accumulated a
row in the database on every keystroke would turn an editing session into an audit trail of
half-formed ideas. ``POST /`` stores the proposal *and* its first evaluation in one
transaction, which is what somebody does when the answer is worth attaching to a change
ticket. The body is the same, so promoting a draft is the same request to a different path.

**Which capability?** Its own pair, and not ``access:read``. A simulation discloses
*potential* access — "put this account in that group and it reaches the payroll share" — which
is a route map for privilege escalation assembled out of answers a reader would otherwise have
to compose by hand. So ``simulations:read`` is held from ``auditor`` upward rather than by
every viewer, and ``simulations:run`` is separate again: running one is the most expensive
computation this API can be asked for, and the split means an account that may *read* what
somebody already ran cannot make the estate resolve a thousand new pairs. See ADR-0034.

**Is a stale baseline a warning or a 409?** A field, on every response that could carry one,
and never a refusal. The proposal is still the proposal; what has moved is the estate. A 409
would withhold the report an operator stored precisely so they could look at it again, in
exchange for telling them something a boolean says better — and re-running against the new
state is one POST away, on a route that already exists.

## What this module will not do

**No route here writes to Windows.** There is no code path from any handler to Active
Directory, to a share, or to an NTFS descriptor: the handlers call
:class:`app.simulation.SimulationService`, which reads through overlay repositories that
expose no write method at all, and :class:`app.simulation.SimulationStore`, which reaches two
tables nothing else in ADG reads. ``tests/db/test_simulations_api.py`` digests every
collected-state table around every route and asserts the digest does not move.

**No route here remediates.** Phase 9B is explicitly a proposal-and-review surface. The only
thing that changes state is a proposal being written down, and the only thing that is deleted
is a proposal.

**Every response says so.** :data:`app.simulation.describe.NON_DESTRUCTIVE_NOTICE` is rendered
on every simulation payload, including the export, rather than left to the client to
remember. A client that renders it in small grey text has still been given the sentence.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import AccessPath, CausalPath
from app.api.access import RightsView, render_rights
from app.api.deps import Session
from app.api.graph import PrincipalSummary, principal_summary
from app.api.pagination import PageInfo, normalize_limit
from app.auth.dependencies import CurrentPrincipal, requires
from app.auth.principal import AuthenticatedPrincipal
from app.auth.roles import Capability
from app.config import Settings, get_settings
from app.domain import (
    AceType,
    DomainValidationError,
    MembershipEdgeKind,
    PrincipalKind,
    SharePermission,
)
from app.repositories import MembershipRepository, ResourceRepository
from app.repositories.alerts import WatchRepository
from app.repositories.membership import PrincipalRecord
from app.repositories.resources import NtfsResourceRecord
from app.risk_engine.facts import ResourceFacts
from app.simulation import (
    MAX_CHANGES,
    MAX_EXPLANATIONS_CEILING,
    MAX_PAIRS_CEILING,
    MAX_PRINCIPALS_CEILING,
    MAX_RESOURCES_CEILING,
    MAX_TIME_BUDGET_MS,
    AccessDelta,
    BaselineKind,
    ChangeApplication,
    ChangeKind,
    InheritanceChange,
    InheritedAceDisposition,
    MembershipChange,
    NtfsAceChange,
    ScopeKind,
    ShareAceChange,
    SimulationBounds,
    SimulationBoundsError,
    SimulationChange,
    SimulationOverlay,
    SimulationReport,
    SimulationScope,
    SimulationService,
    SimulationStore,
    StoredEvaluation,
    StoredSimulation,
)
from app.simulation.describe import (
    CAVEAT_DESCRIPTIONS,
    CHANGE_KIND_DESCRIPTIONS,
    DIRECTION_DESCRIPTIONS,
    DISPOSITION_DESCRIPTIONS,
    NON_DESTRUCTIVE_NOTICE,
    OUTCOME_DESCRIPTIONS,
    TRUNCATION_DESCRIPTIONS,
    describe_change,
)
from app.simulation.store import MAX_NAME_LENGTH

router = APIRouter(prefix="/api/v1/simulations", tags=["simulations"])

#: Reading a proposal somebody else ran, and the report it produced.
READ = Depends(requires(Capability.SIMULATIONS_READ))

#: Computing one, storing one, and removing one. Separate from READ because resolving a
#: proposal's affected scope is the most expensive request this API serves, and because
#: writing down a proposal is an act with an author.
RUN = Depends(requires(Capability.SIMULATIONS_RUN))

__all__ = ["router"]

SettingsDep = Annotated[Settings, Depends(get_settings)]

SimulationIdPath = Annotated[
    UUID, Path(description="The proposal's identifier, as returned when it was stored.")
]

#: The export format's own version, independent of the overlay document's. An exported file
#: outlives the build that wrote it, and a reader has to be able to tell what it is holding.
EXPORT_DOCUMENT_VERSION = "1.0"


# ------------------------------------------------------------------- request bodies


class MembershipChangeInput(BaseModel):
    """Put a principal into a group, or take one out."""

    kind: Literal["add_member", "remove_member"]
    group_key: str = Field(
        min_length=1,
        max_length=512,
        description="Storage key of the group: its SID, or host|SID for a local group.",
    )
    member_key: str = Field(
        min_length=1, max_length=512, description="Storage key of the principal."
    )
    edge_kind: Literal[
        "directory_group_member", "primary_group", "local_group_member", "well_known_implicit"
    ] = Field(
        default="directory_group_member",
        description=(
            "How the membership is expressed. Part of an edge's identity, so a removal "
            "naming the wrong one matches nothing -- which the report says rather than "
            "hides. A membership held by primaryGroupID is not a 'member' edge."
        ),
    )
    member_kind: str | None = Field(
        default=None,
        description="What the member is, when the proposal knows. Labels the edge only.",
    )

    def to_change(self) -> MembershipChange:
        return MembershipChange(
            kind=ChangeKind(self.kind),
            group_key=self.group_key,
            member_key=self.member_key,
            edge_kind=MembershipEdgeKind(self.edge_kind),
            member_kind=None if self.member_kind is None else PrincipalKind(self.member_kind),
        )


class NtfsAceChangeInput(BaseModel):
    r"""Add, change, or remove one entry on a directory's NTFS permissions."""

    kind: Literal["add_ntfs_ace", "modify_ntfs_ace", "remove_ntfs_ace"]
    resource_key: str = Field(
        min_length=1,
        max_length=1024,
        description="The directory's canonical UNC path, for example \\\\FS01\\Finance.",
    )
    ace_key: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "The entry being changed or removed, as the ACL listing reports it. Required "
            "for a modification or a removal: matching on trustee and mask instead would "
            "act on whichever entry happened to sort first when a DACL holds two alike."
        ),
    )
    trustee_sid: str | None = Field(
        default=None, max_length=256, description="The SID a new entry names. Additions only."
    )
    ace_type: Literal["allow", "deny"] | None = None
    access_mask: int | None = Field(
        default=None, ge=0, le=0xFFFFFFFF, description="The entry's 32-bit access mask."
    )
    ace_flags: int | None = Field(
        default=None,
        ge=0,
        le=0xFF,
        description=(
            "The ACE flag byte. 0x01 and 0x02 are the inheritance bits -- an entry carrying "
            "either reaches every directory below this one, and this phase does not evaluate "
            "them (the report says so)."
        ),
    )
    order_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Where in the DACL a new entry goes. Left out, it is placed canonically -- Deny "
            "ahead of Allow, both ahead of anything inherited -- which is where the Windows "
            "ACL editor puts one."
        ),
    )

    def to_change(self) -> NtfsAceChange:
        return NtfsAceChange(
            kind=ChangeKind(self.kind),
            resource_key=self.resource_key,
            ace_key=self.ace_key,
            trustee_sid=self.trustee_sid,
            ace_type=None if self.ace_type is None else AceType(self.ace_type),
            access_mask=self.access_mask,
            ace_flags=self.ace_flags,
            order_index=self.order_index,
        )


class ShareAceChangeInput(BaseModel):
    """Add, change, or remove one entry on a share's permissions."""

    kind: Literal["add_share_ace", "modify_share_ace", "remove_share_ace"]
    share_key: str = Field(min_length=1, max_length=512, description="The share key: server|share.")
    ace_key: str | None = Field(default=None, max_length=512)
    trustee_sid: str | None = Field(default=None, max_length=256)
    ace_type: Literal["allow", "deny"] | None = None
    access_mask: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    permission: Literal["read", "change", "full"] | None = Field(
        default=None,
        description=(
            "The share permission level, for a source that reports one. Exactly one of this "
            "and access_mask: inventing the other form would claim a precision the proposal "
            "does not have."
        ),
    )
    order_index: int | None = Field(default=None, ge=0)

    def to_change(self) -> ShareAceChange:
        return ShareAceChange(
            kind=ChangeKind(self.kind),
            share_key=self.share_key,
            ace_key=self.ace_key,
            trustee_sid=self.trustee_sid,
            ace_type=None if self.ace_type is None else AceType(self.ace_type),
            access_mask=self.access_mask,
            permission=None if self.permission is None else SharePermission(self.permission),
            order_index=self.order_index,
        )


class InheritanceChangeInput(BaseModel):
    """Protect a directory from its parent's permissions, or let them flow again."""

    kind: Literal["set_inheritance"]
    resource_key: str = Field(min_length=1, max_length=1024)
    protected: bool = Field(description="True to set SE_DACL_PROTECTED, false to clear it.")
    inherited_entries: Literal["convert_to_explicit", "remove"] | None = Field(
        default=None,
        description=(
            "What happens to the entries the directory inherits today. Required when "
            "protecting: Windows asks this in a dialog box and the two answers produce "
            "genuinely different ACLs, so a simulation that picked one silently would be "
            "wrong half the time."
        ),
    )

    def to_change(self) -> InheritanceChange:
        return InheritanceChange(
            resource_key=self.resource_key,
            protected=self.protected,
            inherited_entries=(
                None
                if self.inherited_entries is None
                else InheritedAceDisposition(self.inherited_entries)
            ),
        )


ChangeInput = Annotated[
    MembershipChangeInput | NtfsAceChangeInput | ShareAceChangeInput | InheritanceChangeInput,
    Field(discriminator="kind"),
]


class ScopeInput(BaseModel):
    """Which principal/directory pairs to evaluate."""

    kind: Literal["affected", "pair", "resource", "subject"] = Field(
        default="affected",
        description=(
            "affected derives the pairs from the proposal itself and answers 'what does this "
            "change do?'. The other three answer 'what does it do to X?' and must name X."
        ),
    )
    subject_key: str | None = Field(default=None, max_length=512)
    resource_key: str | None = Field(default=None, max_length=1024)
    path: Literal["remote_smb", "local"] = Field(
        default="remote_smb",
        description="local does not cross a share, so a share ACE change cannot affect it.",
    )
    limit: int = Field(default=100, ge=1, le=500)
    after: str | None = Field(default=None, max_length=1024)

    def to_scope(self) -> SimulationScope:
        return SimulationScope(
            kind=ScopeKind(self.kind),
            subject_key=self.subject_key,
            resource_key=self.resource_key,
            path=AccessPath(self.path),
            limit=self.limit,
            after=self.after,
        )


class BoundsInput(BaseModel):
    """Strict limits on the work one simulation may do. Every one is clamped, never exceeded."""

    max_principals: int | None = Field(default=None, ge=1)
    max_resources: int | None = Field(default=None, ge=1)
    max_pairs: int | None = Field(default=None, ge=1)
    max_explanations: int | None = Field(default=None, ge=1)
    time_budget_ms: int | None = Field(default=None, ge=1)

    def to_bounds(self) -> SimulationBounds:
        """The default bounds with whatever was asked for clamped into them.

        Clamped rather than refused, matching :meth:`SimulationBounds.clamped` and the
        traversal limits every other route takes: a caller asking for more than the ceiling
        is asking for a complete answer, and gets a bounded one that says it is bounded.
        """
        return SimulationBounds().clamped(
            max_principals=self.max_principals,
            max_resources=self.max_resources,
            max_pairs=self.max_pairs,
            max_explanations=self.max_explanations,
            time_budget_ms=self.time_budget_ms,
        )


class SimulationRequest(BaseModel):
    """A proposal, the question to ask about it, and the state to ask against."""

    changes: list[ChangeInput] = Field(
        min_length=1,
        max_length=MAX_CHANGES,
        description="What is being proposed. At least one; an empty proposal has no answer.",
    )
    scope: ScopeInput = Field(default_factory=ScopeInput)
    bounds: BoundsInput = Field(default_factory=BoundsInput)
    baseline_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "Evaluate against the estate as it was at this instant rather than as it is now. "
            "Reads the same point-in-time repositories the history API does."
        ),
    )

    def to_overlay(self) -> SimulationOverlay:
        return SimulationOverlay.from_changes(item.to_change() for item in self.changes)


class StoreSimulationRequest(SimulationRequest):
    """The same, plus the two fields that make it worth keeping."""

    name: str = Field(
        min_length=1,
        max_length=MAX_NAME_LENGTH,
        description="What this proposal is for. Long enough for a change-ticket title.",
    )
    description: str | None = Field(default=None, max_length=4000)


# -------------------------------------------------------------------------- views


class SimulationChangeView(BaseModel):
    """One proposed change, echoed with its own sentence and its resolved principals."""

    kind: str
    kind_description: str
    description: str = Field(
        description="The change as a sentence, in storage keys so that it is unambiguous."
    )
    document: dict[str, Any] = Field(
        description="The change exactly as it was submitted and will be stored."
    )
    group: PrincipalSummary | None = None
    member: PrincipalSummary | None = None
    trustee: PrincipalSummary | None = Field(
        default=None, description="The trustee a new ACL entry names, resolved where known."
    )


class SimulationApplicationView(BaseModel):
    """What became of one proposed change when it met the baseline state."""

    change: SimulationChangeView
    outcome: str = Field(
        description=(
            "applied, already_present, target_not_found, target_not_observed, "
            "parent_not_observed, or not_representable."
        )
    )
    outcome_description: str
    applied: bool = Field(
        description=(
            "Whether the simulated world differs from the baseline because of this change. "
            "An impact list can be empty because a proposal is safe or because none of it "
            "applies any more, and those are not the same advice."
        )
    )
    detail: dict[str, str] = Field(default_factory=dict)


class SimulationCaveatView(BaseModel):
    """Why one delta may not mean what it appears to mean."""

    code: str
    description: str


class SimulationRouteView(BaseModel):
    """A route that still delivers access after the proposal removes another.

    Enumerated by re-running the access check over the simulated world, not by reasoning
    about which route looked important. It is the field that stops a report being read as
    "this removal revokes access" when an alternate group membership keeps it.
    """

    layer: str = Field(description="smb_share or ntfs: which ACL the surviving entry is on.")
    chain: list[PrincipalSummary] = Field(
        default_factory=list,
        description="The membership chain, subject first and ACL trustee last.",
    )
    ace_key: str | None = None
    ace_position: int
    rights: RightsView = Field(description="What this route is worth to the final answer.")
    assumed: bool = Field(
        description=(
            "True when the entry reached the subject through an assumed token SID "
            "(Everyone, Authenticated Users) rather than an observed membership. Real "
            "access, and not a membership anybody can remove."
        )
    )
    inherited: bool = Field(
        description="True when the entry is inherited, so the remediation is on an ancestor."
    )
    via_group: bool


class SimulationResourceView(BaseModel):
    """What a directory is, for the reader deciding whether this delta matters."""

    resource_key: str
    share_key: str | None = None
    path: str | None = None
    sensitive: bool = Field(
        default=False, description="Declared sensitive in the risk configuration (ADR-0024)."
    )
    sensitivity_labels: list[str] = Field(
        default_factory=list,
        description="What makes it sensitive, in the operator's own words.",
    )
    watched: bool | None = Field(
        default=None,
        description=(
            "Whether a watch covers this directory or its share. Null when the caller does "
            "not hold alerts:read -- who is being notified about what is a statement about "
            "the organization, and it is not inherited by holding simulations:read."
        ),
    )


class SimulationDeltaView(BaseModel):
    """One principal against one directory, before and after."""

    subject: PrincipalSummary
    resource: SimulationResourceView
    access_path: str
    direction: str
    direction_description: str
    changed: bool
    rights_before: RightsView
    rights_after: RightsView
    rights_added: RightsView = Field(description="Effective rights the proposal would grant.")
    rights_removed: RightsView = Field(description="Effective rights the proposal would take.")
    certainty_before: str
    certainty_after: str
    limiting_layer_after: str = Field(
        description="Which ACL holds the answer down afterwards: share, ntfs, both or neither."
    )
    caveats: list[SimulationCaveatView] = Field(default_factory=list)
    alternate_path_retained: bool = Field(
        description=(
            "True when the proposal removes one route to this directory and the principal "
            "still reaches it by another. Measured, not inferred."
        )
    )
    retained_routes: list[SimulationRouteView] = Field(default_factory=list)


class SimulationSummaryView(BaseModel):
    """The counts a reader looks at first. Never the whole answer."""

    evaluated: int
    unchanged: int
    gained_access: int
    lost_access: int
    expanded: int
    reduced: int
    changed: int
    principals_gaining: list[PrincipalSummary] = Field(default_factory=list)
    principals_losing: list[PrincipalSummary] = Field(default_factory=list)
    principals_affected: int = Field(
        description="Distinct principals whose rights move in either direction."
    )
    resources_affected: list[str] = Field(default_factory=list)
    sensitive_resources_affected: list[str] = Field(
        default_factory=list,
        description="Of those, the ones declared sensitive in the risk configuration.",
    )
    watched_resources_affected: list[str] | None = Field(
        default=None,
        description="Of those, the ones a watch covers. Null without alerts:read.",
    )
    alternate_paths_retained: int = Field(
        description=(
            "Deltas where a route was removed and another survives. A high number against a "
            "removal proposal usually means the change achieves less than it appears to."
        )
    )


class SimulationTruncationView(BaseModel):
    """Why an impact list is part of the answer rather than the whole of it."""

    code: str
    description: str


class SimulationCostView(BaseModel):
    """What one simulation actually spent, so a bound can be sized from evidence."""

    pairs_evaluated: int
    resolutions: int
    explanations: int
    edges_read: int
    elapsed_ms: int


class SimulationBoundsView(BaseModel):
    max_principals: int
    max_resources: int
    max_pairs: int
    max_explanations: int
    time_budget_ms: int


class SimulationScopeView(BaseModel):
    kind: str
    subject_key: str | None = None
    resource_key: str | None = None
    path: str
    limit: int
    after: str | None = None


class SimulationBaselineView(BaseModel):
    """The state this answer was computed against, named precisely enough to check later."""

    kind: str = Field(description="current or as_of.")
    token: str = Field(
        description=(
            "A digest of every scan run. It moves if and only if a collector has written "
            "something, so comparing two is an exact staleness test rather than a clock."
        )
    )
    run_id: str | None = Field(
        default=None,
        description="The most recently updated run at capture. For a human, not for comparison.",
    )
    at: dt.datetime | None = Field(default=None, description="The instant an as_of baseline reads.")
    captured_at: dt.datetime
    is_empty: bool = Field(
        description=(
            "True when nothing has been collected at all. Every answer over an empty estate "
            "is 'no access', so every proposal against one reports no impact -- truthfully, "
            "and uselessly, unless the emptiness is stated."
        )
    )
    stale: bool = Field(
        description="True when a collector has written something since this was captured."
    )
    current_token: str = Field(description="The collection-state token right now.")


class SimulationReportView(BaseModel):
    """Everything one simulation produced, including what it could not reach."""

    notice: str = Field(description="That nothing was applied. Rendered on every surface.")
    applied: Literal[False] = Field(
        default=False,
        description=(
            "Always false, and a field rather than prose so a client can assert on it. No "
            "route in this API writes to Active Directory, a share, or an NTFS descriptor."
        ),
    )
    simulation_id: UUID | None = Field(
        default=None, description="Set when the proposal was stored. Null for a preview."
    )
    overlay_hash: str = Field(
        description=(
            "A stable digest of the proposal itself. Two proposals that say the same thing "
            "digest the same however they were assembled."
        )
    )
    change_count: int
    baseline: SimulationBaselineView
    scope: SimulationScopeView
    bounds: SimulationBoundsView
    applications: list[SimulationApplicationView]
    inert: bool = Field(
        description=(
            "True when no change took effect at all -- every one was already present, or its "
            "target has gone. Distinct from 'no impact', and the two must not read alike."
        )
    )
    summary: SimulationSummaryView
    deltas: list[SimulationDeltaView] = Field(
        description="One per principal/directory pair evaluated, unchanged ones included."
    )
    complete: bool = Field(description="False when any bound was hit. See truncation.")
    truncation: list[SimulationTruncationView] = Field(default_factory=list)
    cost: SimulationCostView


class StoredSimulationView(BaseModel):
    """A proposal as it was written down."""

    simulation_id: UUID
    name: str
    description: str | None = None
    created_by: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    change_count: int
    overlay_hash: str
    changes: list[SimulationChangeView]
    baseline: SimulationBaselineView


class StoredSimulationsResponse(BaseModel):
    notice: str
    simulations: list[StoredSimulationView]
    page: PageInfo


class SimulationEvaluationView(BaseModel):
    """One run of one proposal, as the listing shows it."""

    evaluation_id: UUID
    simulation_id: UUID
    scope_kind: str
    baseline_token: str
    stale_baseline: bool = Field(
        description=(
            "Whether the estate had already moved on from the proposal's own baseline when "
            "this ran. It does not make the result wrong -- it was computed against real "
            "facts -- but the result and the proposal then name two different baselines."
        )
    )
    pairs_evaluated: int
    complete: bool
    duration_ms: int
    computed_at: dt.datetime
    report: dict[str, Any] = Field(
        description="The compact report document, exactly as it was persisted."
    )


class SimulationDetailView(BaseModel):
    """A stored proposal and its evaluation history."""

    notice: str
    simulation: StoredSimulationView
    stale: bool = Field(
        description=(
            "Whether a collector has written something since this proposal was measured. A "
            "field rather than a 409: the proposal is still the proposal, and re-running it "
            "is one POST to /evaluations away."
        )
    )
    current_token: str
    evaluations: list[SimulationEvaluationView]


class StoredSimulationResponse(BaseModel):
    """A proposal that has just been stored, with the report that came with it."""

    notice: str
    simulation: StoredSimulationView
    report: SimulationReportView


class SimulationVocabularyEntry(BaseModel):
    code: str
    description: str


class SimulationVocabularyResponse(BaseModel):
    """Every closed vocabulary a simulation report speaks, with its wording.

    Served rather than hard-coded in the client for the reason the watch-trigger table and
    the navigation's capability list are: a second copy of a vocabulary is a second copy that
    can be wrong, and the wrong one is always the one somebody trusts. The caveat list is the
    one that matters -- a client that invented its own text for ``loss_may_not_hold`` would be
    writing the sentence that stands between a report and a remediation that achieves nothing.
    """

    notice: str
    change_kinds: list[SimulationVocabularyEntry]
    inherited_ace_dispositions: list[SimulationVocabularyEntry]
    outcomes: list[SimulationVocabularyEntry]
    directions: list[SimulationVocabularyEntry]
    caveats: list[SimulationVocabularyEntry]
    truncations: list[SimulationVocabularyEntry]
    scope_kinds: list[SimulationVocabularyEntry]
    max_changes: int
    bounds_ceilings: SimulationBoundsView


class SimulationPlanView(BaseModel):
    """What was proposed, and against what."""

    simulation_id: UUID
    name: str
    description: str | None = None
    created_by: str | None = None
    created_at: dt.datetime
    overlay_hash: str
    baseline: SimulationBaselineView
    changes: list[SimulationChangeView]


class SimulationExportView(BaseModel):
    """A simulation plan and its result, in one self-describing document.

    Self-describing on purpose: the vocabulary travels with the file. An export read six
    months later, by somebody without this API in front of them, must not need a second
    document to say what ``loss_may_not_hold`` meant.
    """

    document_version: str
    notice: str
    exported_at: dt.datetime
    plan: SimulationPlanView
    result: SimulationEvaluationView | None = Field(
        default=None,
        description="The evaluation exported. Null for a proposal that has never been run.",
    )
    stale: bool
    current_token: str
    vocabulary: SimulationVocabularyResponse


# ------------------------------------------------------------------------ routes


@router.get(
    "/vocabulary",
    response_model=SimulationVocabularyResponse,
    summary="Every vocabulary a simulation report speaks",
    dependencies=[READ],
)
async def vocabulary() -> SimulationVocabularyResponse:
    """The closed vocabularies and their wording, so a client never invents its own."""
    return _vocabulary_view()


@router.post(
    "/preview",
    response_model=SimulationReportView,
    summary="Evaluate a proposal without storing it",
    dependencies=[RUN],
    responses={422: {"description": "The proposal, the scope, or the bounds are not valid."}},
)
async def preview_simulation(
    body: SimulationRequest,
    session: Session,
    principal: CurrentPrincipal,
    settings: SettingsDep,
) -> SimulationReportView:
    """Answer *"what would this do?"* and keep nothing.

    **Nothing is written.** Not to Windows, and not to ADG: this route does not touch the
    ``simulations`` tables either, so an editing session leaves no trail of half-formed
    proposals. Store one with ``POST /api/v1/simulations`` when the answer is worth keeping.
    """
    report = await _run(body, session)
    return await _report_view(report, session, principal, settings)


@router.post(
    "",
    response_model=StoredSimulationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Evaluate a proposal and store it",
    dependencies=[RUN],
    responses={422: {"description": "The proposal, the scope, or the bounds are not valid."}},
)
async def store_simulation(
    body: StoreSimulationRequest,
    session: Session,
    principal: CurrentPrincipal,
    settings: SettingsDep,
) -> StoredSimulationResponse:
    """Write the proposal and its first evaluation down, in one transaction.

    Both rows or neither. An evaluation without its proposal is unreadable — the overlay is
    what says what was simulated — and a proposal whose first evaluation was lost would
    report itself as never run.

    **Still nothing is applied.** The two rows written here are ADG's own record of a
    question somebody asked. No collector reads them and no Windows object is touched.
    """
    report = await _run(body, session)
    store = SimulationStore(session)
    current = await SimulationService(session).current_basis()
    stored = await store.save(
        report,
        name=body.name,
        description=body.description,
        created_by=principal.subject,
        current=current,
    )
    await session.commit()
    view = await _report_view(report, session, principal, settings)
    return StoredSimulationResponse(
        notice=NON_DESTRUCTIVE_NOTICE,
        simulation=await _stored_view(stored, session, current.token),
        report=view.model_copy(update={"simulation_id": stored.simulation_id}),
    )


@router.get(
    "",
    response_model=StoredSimulationsResponse,
    summary="Stored proposals, newest first",
    dependencies=[READ],
)
async def list_simulations(
    session: Session,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: Annotated[str | None, Query(description="From a previous page's next_cursor.")] = None,
) -> StoredSimulationsResponse:
    """Every proposal anybody has stored. Keyset-paged on ``(created_at, simulation_id)``."""
    page_size = min(normalize_limit(limit), 200)
    try:
        page = await SimulationStore(session).list(limit=page_size, after=cursor)
    except DomainValidationError as error:
        raise _unprocessable(error) from error
    current = await SimulationService(session).current_basis()
    return StoredSimulationsResponse(
        notice=NON_DESTRUCTIVE_NOTICE,
        simulations=[await _stored_view(item, session, current.token) for item in page.items],
        page=PageInfo(limit=page_size, has_more=page.has_more, next_cursor=page.next_key),
    )


@router.get(
    "/{simulation_id}",
    response_model=SimulationDetailView,
    summary="One stored proposal and its evaluation history",
    dependencies=[READ],
    responses={404: {"description": "No such proposal."}},
)
async def get_simulation(
    simulation_id: SimulationIdPath,
    session: Session,
    evaluation_limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SimulationDetailView:
    """The proposal as it was written, and every time it has been run.

    Re-running a proposal **adds** an evaluation rather than replacing one. Two evaluations
    against two collection states are two findings — *"this change was safe on Monday and
    takes access away today"* is the sentence a proposal's history exists to make available.
    """
    store = SimulationStore(session)
    stored = await _require(store, simulation_id)
    current = await SimulationService(session).current_basis()
    evaluations = await store.evaluations(simulation_id, limit=evaluation_limit)
    return SimulationDetailView(
        notice=NON_DESTRUCTIVE_NOTICE,
        simulation=await _stored_view(stored, session, current.token),
        stale=stored.is_stale_against(current),
        current_token=current.token,
        evaluations=[_evaluation_view(item) for item in evaluations],
    )


@router.post(
    "/{simulation_id}/evaluations",
    response_model=SimulationReportView,
    status_code=status.HTTP_201_CREATED,
    summary="Run a stored proposal again",
    dependencies=[RUN],
    responses={404: {"description": "No such proposal."}},
)
async def evaluate_simulation(
    simulation_id: SimulationIdPath,
    session: Session,
    principal: CurrentPrincipal,
    settings: SettingsDep,
    scope: Annotated[
        Literal["affected", "pair", "resource", "subject"] | None,
        Query(description="Defaults to the scope stored with the proposal's first run."),
    ] = None,
    against_stored_baseline: Annotated[
        bool,
        Query(
            description=(
                "Re-run against the baseline the proposal was written for rather than "
                "against the estate as it is now. Useful for reproducing an old report "
                "exactly; the default answers the question an operator is actually asking."
            )
        ),
    ] = False,
) -> SimulationReportView:
    """Evaluate a stored proposal against the estate as it is now, and record the result.

    The stored overlay is rebuilt through the overlay's own constructors, so a proposal that
    would no longer be accepted — a change kind that has been retired, a mask now out of
    range — is refused on the way out instead of being simulated under rules it was never
    validated against.
    """
    store = SimulationStore(session)
    stored = await _require(store, simulation_id)
    service = SimulationService(session)
    baseline = (
        await service.baseline(at=stored.baseline_at)
        if against_stored_baseline and stored.baseline_kind is BaselineKind.AS_OF
        else await service.baseline()
    )
    question = SimulationScope(kind=ScopeKind(scope)) if scope else SimulationScope()
    try:
        report = await service.run(stored.overlay, scope=question, baseline=baseline)
    except DomainValidationError as error:
        raise _unprocessable(error) from error
    current = await service.current_basis()
    await store.record(simulation_id, report, current=current)
    await session.commit()
    view = await _report_view(report, session, principal, settings)
    return view.model_copy(update={"simulation_id": simulation_id})


@router.get(
    "/{simulation_id}/evaluations",
    response_model=list[SimulationEvaluationView],
    summary="Every run of one proposal, newest first",
    dependencies=[READ],
    responses={404: {"description": "No such proposal."}},
)
async def list_evaluations(
    simulation_id: SimulationIdPath,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SimulationEvaluationView]:
    """The proposal's history: what it was found to do, each time it was asked."""
    store = SimulationStore(session)
    await _require(store, simulation_id)
    return [_evaluation_view(item) for item in await store.evaluations(simulation_id, limit=limit)]


@router.get(
    "/{simulation_id}/export",
    response_model=SimulationExportView,
    summary="A proposal and its result as one structured document",
    dependencies=[READ],
    responses={404: {"description": "No such proposal, or no such evaluation of it."}},
)
async def export_simulation(
    simulation_id: SimulationIdPath,
    session: Session,
    evaluation_id: Annotated[
        UUID | None,
        Query(description="A specific run to export. Defaults to the most recent one."),
    ] = None,
) -> SimulationExportView:
    """The plan, the result, and the vocabulary that explains it, in one file.

    The result is the stored report document **verbatim** rather than a reshaping of it. A
    reshaped export would be a second description of an answer, ageing independently of the
    one the engine computes, and the one thing worse than no export is an export that quietly
    says something else.
    """
    store = SimulationStore(session)
    stored = await _require(store, simulation_id)
    current = await SimulationService(session).current_basis()
    evaluations = await store.evaluations(simulation_id, limit=200)
    selected: StoredEvaluation | None
    if evaluation_id is None:
        selected = evaluations[0] if evaluations else None
    else:
        selected = next((item for item in evaluations if item.evaluation_id == evaluation_id), None)
        if selected is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Simulation {simulation_id} has no evaluation {evaluation_id}.",
            )
    view = await _stored_view(stored, session, current.token)
    return SimulationExportView(
        document_version=EXPORT_DOCUMENT_VERSION,
        notice=NON_DESTRUCTIVE_NOTICE,
        exported_at=dt.datetime.now(dt.UTC),
        plan=SimulationPlanView(
            simulation_id=stored.simulation_id,
            name=stored.name,
            description=stored.description,
            created_by=stored.created_by,
            created_at=stored.created_at,
            overlay_hash=stored.overlay.overlay_hash,
            baseline=view.baseline,
            changes=view.changes,
        ),
        result=None if selected is None else _evaluation_view(selected),
        stale=stored.is_stale_against(current),
        current_token=current.token,
        vocabulary=_vocabulary_view(),
    )


@router.delete(
    "/{simulation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a stored proposal",
    dependencies=[RUN],
    responses={404: {"description": "No such proposal."}},
)
async def delete_simulation(simulation_id: SimulationIdPath, session: Session) -> None:
    """Delete a proposal and its evaluations.

    The one destructive operation in this API, and it destroys only proposals: the cascade
    reaches ``simulation_evaluations`` and stops, because nothing else points at either
    table. No collected fact can be reached from here.
    """
    if not await SimulationStore(session).delete(simulation_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No simulation {simulation_id}."
        )
    await session.commit()


# ----------------------------------------------------------------------- plumbing


async def _run(body: SimulationRequest, session: AsyncSession) -> SimulationReport:
    """Build the proposal and evaluate it, turning every domain refusal into a 422.

    The domain constructors are the validators, deliberately. Re-implementing "a removal must
    name the entry it removes" in a Pydantic model would be a second copy of a rule that can
    disagree with the one the engine enforces, and the second copy is always the lenient one.
    """
    try:
        overlay = body.to_overlay()
        scope = body.scope.to_scope()
        bounds = body.bounds.to_bounds()
    except (DomainValidationError, SimulationBoundsError) as error:
        raise _unprocessable(error) from error
    service = SimulationService(session)
    baseline = await service.baseline(at=body.baseline_at)
    try:
        return await service.run(overlay, scope=scope, bounds=bounds, baseline=baseline)
    except DomainValidationError as error:
        raise _unprocessable(error) from error


def _unprocessable(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


async def _require(store: SimulationStore, simulation_id: UUID) -> StoredSimulation:
    stored = await store.get(simulation_id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No simulation {simulation_id}."
        )
    return stored


async def _labels(session: AsyncSession, keys: set[str]) -> dict[str, PrincipalRecord]:
    """Principal records for every key a response renders, in one query.

    Rendering a storage key raw is what makes a report unreadable: an impact list of forty
    SIDs is a list nobody acts on. One batched lookup rather than one per delta, because the
    affected scope can legitimately produce hundreds.
    """
    if not keys:
        return {}
    return await MembershipRepository(session).principals_by_keys(sorted(keys))


async def _report_view(
    report: SimulationReport,
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    settings: Settings,
) -> SimulationReportView:
    """The whole report, with every key resolved and every code given its sentence."""
    keys = _keys_in(report)
    labels = await _labels(session, keys)
    service = SimulationService(session)
    current = await service.current_basis()
    marks = await _resource_marks(report, session, principal, settings)
    show_watches = principal.can(Capability.ALERTS_READ)

    deltas = [
        _delta_view(delta, labels, marks, show_watches=show_watches) for delta in report.deltas
    ]
    changed_resources = sorted({delta.resource_key for delta in report.deltas if delta.changed})
    return SimulationReportView(
        notice=NON_DESTRUCTIVE_NOTICE,
        simulation_id=report.simulation_id,
        overlay_hash=report.overlay.overlay_hash,
        change_count=len(report.overlay),
        baseline=_baseline_view(report, current.token),
        scope=SimulationScopeView(
            kind=report.scope.kind.value,
            subject_key=report.scope.subject_key,
            resource_key=report.scope.resource_key,
            path=report.scope.path.value,
            limit=report.scope.limit,
            after=report.scope.after,
        ),
        bounds=_bounds_view(report.bounds),
        applications=[_application_view(item, labels) for item in report.applications],
        inert=report.inert,
        summary=_summary_view(report, labels, marks, changed_resources, show_watches=show_watches),
        deltas=deltas,
        complete=report.complete,
        truncation=[
            SimulationTruncationView(code=reason.value, description=TRUNCATION_DESCRIPTIONS[reason])
            for reason in report.truncation
        ],
        cost=SimulationCostView(
            pairs_evaluated=report.cost.pairs_evaluated,
            resolutions=report.cost.resolutions,
            explanations=report.cost.explanations,
            edges_read=report.cost.edges_read,
            elapsed_ms=report.cost.elapsed_ms,
        ),
    )


def _keys_in(report: SimulationReport) -> set[str]:
    """Every principal key any part of this report renders."""
    keys: set[str] = set()
    for delta in report.deltas:
        keys.add(delta.subject_key)
        for path in delta.retained_paths:
            keys.update(path.chain)
    for change in report.overlay.changes:
        keys.update(_change_keys(change))
    return keys


def _change_keys(change: SimulationChange) -> set[str]:
    if isinstance(change, MembershipChange):
        return {change.group_key, change.member_key}
    if isinstance(change, NtfsAceChange | ShareAceChange):
        trustee = change.trustee_key
        return {trustee} if trustee else set()
    return set()


@dataclass(frozen=True, slots=True)
class _ResourceMark:
    """What a reader needs about one directory beyond its key: where it is, and whether
    anybody has said it matters."""

    path: str | None
    sensitivity: tuple[str, ...]
    watched: bool


async def _resource_marks(
    report: SimulationReport,
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    settings: Settings,
) -> dict[str, _ResourceMark]:
    """The path, the sensitivity labels, and the watch coverage of every directory named.

    The descriptor is fetched for the directories a delta does not carry one for, in **one**
    batched query rather than one per delta. A ``pair`` or ``subject`` simulation legitimately
    produces deltas with no record attached — the scope never enumerated the directory, it was
    named — and rendering those as a bare storage key would leave the one screen an operator
    reads before signing off a change showing a row of raw UNC keys and no sensitivity.

    Sensitivity is decided by :meth:`RiskConfiguration.tags_for_resource` rather than by a
    prefix test written here: the rule about whether ``\\FS01\\Finance`` covers
    ``\\FS01\\Finance-Archive`` is subtle, it is already written down once, and a second copy
    would eventually disagree about the one resource somebody cared about.
    """
    configuration = settings.risk_configuration
    watched_resources: set[str] = set()
    watched_shares: set[str] = set()
    if principal.can(Capability.ALERTS_READ):
        for watch in await WatchRepository(session).list():
            if watch.kind.value == "resource":
                watched_resources.add(watch.key.casefold())
            elif watch.kind.value == "share":
                watched_shares.add(watch.key.casefold())

    records: dict[str, NtfsResourceRecord] = {
        delta.resource_key: delta.resource for delta in report.deltas if delta.resource is not None
    }
    unknown = sorted({delta.resource_key for delta in report.deltas} - set(records))
    if unknown:
        records |= await ResourceRepository(session).ntfs_resources_by_keys(unknown)

    marks: dict[str, _ResourceMark] = {}
    for delta in report.deltas:
        if delta.resource_key in marks:
            continue
        record = records.get(delta.resource_key)
        labels: tuple[str, ...] = ()
        if record is not None and configuration.marks_anything_sensitive:
            facts = ResourceFacts(
                resource_key=record.resource_key,
                path=record.path,
                share_key=record.share_key,
                server_key=record.server_key,
            )
            labels = tuple(tag.label for tag in configuration.tags_for_resource(facts))
        watched = delta.resource_key.casefold() in watched_resources or (
            delta.share_key is not None and delta.share_key.casefold() in watched_shares
        )
        marks[delta.resource_key] = _ResourceMark(
            path=None if record is None else record.path,
            sensitivity=labels,
            watched=watched,
        )
    return marks


def _baseline_view(report: SimulationReport, current_token: str) -> SimulationBaselineView:
    baseline = report.baseline
    return SimulationBaselineView(
        kind=baseline.kind.value,
        token=baseline.token,
        run_id=baseline.run_id,
        at=baseline.at,
        captured_at=baseline.captured_at,
        is_empty=baseline.is_empty,
        stale=current_token != baseline.token,
        current_token=current_token,
    )


def _bounds_view(bounds: SimulationBounds) -> SimulationBoundsView:
    return SimulationBoundsView(
        max_principals=bounds.max_principals,
        max_resources=bounds.max_resources,
        max_pairs=bounds.max_pairs,
        max_explanations=bounds.max_explanations,
        time_budget_ms=bounds.time_budget_ms,
    )


def _change_view(
    change: SimulationChange, labels: dict[str, PrincipalRecord]
) -> SimulationChangeView:
    group = member = trustee = None
    if isinstance(change, MembershipChange):
        group = principal_summary(change.group_key, labels.get(change.group_key))
        member = principal_summary(change.member_key, labels.get(change.member_key))
    elif isinstance(change, NtfsAceChange | ShareAceChange):
        key = change.trustee_key
        if key is not None:
            trustee = principal_summary(key, labels.get(key))
    return SimulationChangeView(
        kind=change.kind.value,
        kind_description=CHANGE_KIND_DESCRIPTIONS[change.kind],
        description=describe_change(change),
        document=change.document(),
        group=group,
        member=member,
        trustee=trustee,
    )


def _application_view(
    item: ChangeApplication, labels: dict[str, PrincipalRecord]
) -> SimulationApplicationView:
    return SimulationApplicationView(
        change=_change_view(item.change, labels),
        outcome=item.outcome.value,
        outcome_description=OUTCOME_DESCRIPTIONS[item.outcome],
        applied=item.applied,
        detail=dict(item.detail),
    )


def _delta_view(
    delta: AccessDelta,
    labels: dict[str, PrincipalRecord],
    marks: dict[str, _ResourceMark],
    *,
    show_watches: bool,
) -> SimulationDeltaView:
    mark = marks[delta.resource_key]
    return SimulationDeltaView(
        subject=principal_summary(delta.subject_key, labels.get(delta.subject_key)),
        resource=SimulationResourceView(
            resource_key=delta.resource_key,
            share_key=delta.share_key,
            path=mark.path,
            sensitive=bool(mark.sensitivity),
            sensitivity_labels=list(mark.sensitivity),
            watched=mark.watched if show_watches else None,
        ),
        access_path=delta.path.value,
        direction=delta.direction.value,
        direction_description=DIRECTION_DESCRIPTIONS[delta.direction],
        changed=delta.changed,
        rights_before=render_rights(delta.before.rights),
        rights_after=render_rights(delta.after.rights),
        rights_added=render_rights(delta.rights_added),
        rights_removed=render_rights(delta.rights_removed),
        certainty_before=delta.before_certainty.value,
        certainty_after=delta.after_certainty.value,
        limiting_layer_after=delta.after.limiting_layer.value,
        caveats=[
            SimulationCaveatView(code=caveat.value, description=CAVEAT_DESCRIPTIONS[caveat])
            for caveat in delta.caveats
        ],
        alternate_path_retained=delta.alternate_path_retained,
        retained_routes=[_route_view(path, labels) for path in delta.retained_paths],
    )


def _route_view(path: CausalPath, labels: dict[str, PrincipalRecord]) -> SimulationRouteView:
    return SimulationRouteView(
        layer=path.layer.value,
        chain=[principal_summary(key, labels.get(key)) for key in path.chain],
        ace_key=path.ace_key,
        ace_position=path.ace_position,
        rights=render_rights(path.effective_rights),
        assumed=path.assumed,
        inherited=path.inherited,
        via_group=path.via_group,
    )


def _summary_view(
    report: SimulationReport,
    labels: dict[str, PrincipalRecord],
    marks: dict[str, _ResourceMark],
    changed_resources: list[str],
    *,
    show_watches: bool,
) -> SimulationSummaryView:
    summary = report.summary
    affected = set(summary.principals_gaining) | set(summary.principals_losing)
    return SimulationSummaryView(
        evaluated=summary.evaluated,
        unchanged=summary.unchanged,
        gained_access=summary.gained_access,
        lost_access=summary.lost_access,
        expanded=summary.expanded,
        reduced=summary.reduced,
        changed=summary.changed,
        principals_gaining=[
            principal_summary(key, labels.get(key)) for key in summary.principals_gaining
        ],
        principals_losing=[
            principal_summary(key, labels.get(key)) for key in summary.principals_losing
        ],
        principals_affected=len(affected),
        resources_affected=list(summary.resources_affected),
        sensitive_resources_affected=[key for key in changed_resources if marks[key].sensitivity],
        watched_resources_affected=(
            [key for key in changed_resources if marks[key].watched] if show_watches else None
        ),
        alternate_paths_retained=summary.alternate_paths_retained,
    )


async def _stored_view(
    stored: StoredSimulation, session: AsyncSession, current_token: str
) -> StoredSimulationView:
    keys: set[str] = set()
    for change in stored.overlay.changes:
        keys |= _change_keys(change)
    labels = await _labels(session, keys)
    return StoredSimulationView(
        simulation_id=stored.simulation_id,
        name=stored.name,
        description=stored.description,
        created_by=stored.created_by,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
        change_count=stored.change_count,
        overlay_hash=stored.overlay.overlay_hash,
        changes=[_change_view(change, labels) for change in stored.overlay.changes],
        baseline=SimulationBaselineView(
            kind=stored.baseline_kind.value,
            token=stored.baseline_token,
            run_id=stored.baseline_run_id,
            at=stored.baseline_at,
            captured_at=stored.baseline_captured_at,
            is_empty=False,
            stale=current_token != stored.baseline_token,
            current_token=current_token,
        ),
    )


def _evaluation_view(item: StoredEvaluation) -> SimulationEvaluationView:
    return SimulationEvaluationView(
        evaluation_id=item.evaluation_id,
        simulation_id=item.simulation_id,
        scope_kind=item.scope_kind.value,
        baseline_token=item.baseline_token,
        stale_baseline=item.stale_baseline,
        pairs_evaluated=item.pairs_evaluated,
        complete=item.complete,
        duration_ms=item.duration_ms,
        computed_at=item.computed_at,
        report=item.report,
    )


def _vocabulary_view() -> SimulationVocabularyResponse:
    return SimulationVocabularyResponse(
        notice=NON_DESTRUCTIVE_NOTICE,
        change_kinds=_entries(CHANGE_KIND_DESCRIPTIONS),
        inherited_ace_dispositions=_entries(DISPOSITION_DESCRIPTIONS),
        outcomes=_entries(OUTCOME_DESCRIPTIONS),
        directions=_entries(DIRECTION_DESCRIPTIONS),
        caveats=_entries(CAVEAT_DESCRIPTIONS),
        truncations=_entries(TRUNCATION_DESCRIPTIONS),
        scope_kinds=[
            SimulationVocabularyEntry(code=kind.value, description=(kind.__doc__ or "").strip())
            for kind in ScopeKind
        ],
        max_changes=MAX_CHANGES,
        bounds_ceilings=SimulationBoundsView(
            max_principals=MAX_PRINCIPALS_CEILING,
            max_resources=MAX_RESOURCES_CEILING,
            max_pairs=MAX_PAIRS_CEILING,
            max_explanations=MAX_EXPLANATIONS_CEILING,
            time_budget_ms=MAX_TIME_BUDGET_MS,
        ),
    )


def _entries(descriptions: dict[Any, str]) -> list[SimulationVocabularyEntry]:
    """A description table rendered in the enum's own order, which is a meaningful one."""
    return [
        SimulationVocabularyEntry(code=code.value, description=description)
        for code, description in descriptions.items()
    ]
