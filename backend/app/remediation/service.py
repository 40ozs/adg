r"""The change-plan workflow: write, measure, submit, approve, export. Never apply.

Six acts, four of which are gated, and every gate is checked **at the moment it is relied
on** rather than only when a status was last set. That is the difference between a lifecycle
that documents an intention and one that holds: a plan approved on Tuesday against an estate
that moved on Wednesday is not an approved plan on Thursday, and the only way to know is to
look again.

## The gates

**A plan cannot be submitted without a current blast radius.** ``simulation_id`` is not
enough — the simulation's own basis token must equal the plan's, so a report measured against
a different world than the plan describes is not accepted as this plan's impact. The report
comes from :class:`app.simulation.SimulationService`, which runs the production access engine
twice; there is no remediation-specific impact arithmetic anywhere in this package.

**A plan cannot be approved by the person who wrote it.** Checked here against the actor, and
again by ``ck_remediation_plans_approver_is_not_the_requestor`` in the database, so a future
code path that sets the columns directly still cannot produce a self-approved plan (ADR-0038).

**A plan cannot be exported unless every precondition still holds.** Each change carries the
entry it was written against, digested; export re-reads the estate and refuses on anything but
equality. A plan that fails here is **invalidated**, not merely refused: it has already been
put in front of an approver as a true statement about the estate and it is no longer one.

**A plan cannot be exported unsigned.** No signing key configured means no export. See
:mod:`app.remediation.export`.

## What this service cannot do

Apply anything. There is no method here that calls a remediator, and
:func:`execution_policy` — the one method that mentions one — calls
:meth:`~app.remediation.executor.Remediator.describe`, which is the safe half of the
interface. ``tests/remediation/test_no_write_path.py`` asserts from the syntax tree that no
module in ``app/api`` or ``app/remediation`` outside ``executor.py`` names ``apply``.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain import (
    ApprovalDecision,
    ChangePlanStatus,
    ExecutionMode,
    GovernanceEventType,
)
from app.governance.audit import ChainVerification, GovernanceEvent, verify_chain
from app.governance.repository import GovernanceRepository
from app.governance.service import Actor
from app.remediation.errors import (
    RemediationConflict,
    RemediationForbidden,
    RemediationNotFound,
    RemediationValidationError,
)
from app.remediation.executor import ExecutorDescription, remediator_for
from app.remediation.export import (
    SIGNATURE_ALGORITHM,
    build_document,
    document_digest,
    render_runbook,
    sign_document,
)
from app.remediation.model import (
    ApprovalRecord,
    ChangePlan,
    PlanExport,
    PlannedChange,
    PreconditionReport,
    plan_chain,
    validate_changes,
    validate_rationale,
    validate_title,
    validate_transition,
)
from app.remediation.preconditions import evaluate_plan, summarize_verdict
from app.remediation.repository import PlanCandidate, RemediationRepository
from app.remediation.translate import overlay_for
from app.repositories.basis import CollectionBasisRepository
from app.simulation import (
    DEFAULT_BOUNDS,
    SimulationBounds,
    SimulationReport,
    SimulationService,
    SimulationStore,
)

__all__ = ["RemediationService", "SimulatedPlan"]

logger = logging.getLogger("adg.remediation")


class SimulatedPlan:
    """A plan and the blast radius just computed for it."""

    __slots__ = ("plan", "report", "simulation_id")

    def __init__(self, plan: ChangePlan, report: SimulationReport, simulation_id: UUID) -> None:
        self.plan = plan
        self.report = report
        self.simulation_id = simulation_id


class RemediationService:
    """The workflow over one database session. Commits once per mutating method."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = RemediationRepository(session)
        self._governance = GovernanceRepository(session)
        self._basis = CollectionBasisRepository(session)

    @staticmethod
    def _now() -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    # ------------------------------------------------------------------ execution policy

    def execution_policy(self) -> ExecutorDescription:
        """What this deployment can truthfully say about its own write posture.

        Calls :meth:`~app.remediation.executor.Remediator.describe`, which is the half of the
        interface that never writes. The factory is invoked rather than the description being
        composed from settings, because the answer an operator wants is what the *process*
        holds — a description assembled from configuration would keep saying "disabled" even
        if something had gone wrong with the factory.
        """
        return remediator_for(
            ExecutionMode(self._settings.remediation_execution_mode),
            environment=self._settings.environment,
            fixture_path=self._settings.remediation_lab_fixture_path,
        ).describe()

    # ----------------------------------------------------------------------------- reads

    async def get_plan(self, plan_id: UUID) -> ChangePlan:
        plan = await self._repository.get_plan(plan_id)
        if plan is None:
            raise RemediationNotFound(f"Change plan {plan_id} does not exist.")
        return plan

    async def list_plans(
        self,
        *,
        status: ChangePlanStatus | None = None,
        campaign_id: UUID | None = None,
        requested_by: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[ChangePlan, ...], bool]:
        return await self._repository.list_plans(
            status=status,
            campaign_id=campaign_id,
            requested_by=requested_by,
            limit=limit,
            offset=offset,
        )

    async def candidates(
        self, *, campaign_id: UUID | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[tuple[PlanCandidate, ...], bool]:
        return await self._repository.candidates(
            campaign_id=campaign_id, limit=limit, offset=offset
        )

    async def approvals(self, plan_id: UUID) -> tuple[ApprovalRecord, ...]:
        await self.get_plan(plan_id)
        return await self._repository.approvals_for(plan_id)

    async def exports(self, plan_id: UUID) -> tuple[PlanExport, ...]:
        await self.get_plan(plan_id)
        return await self._repository.exports_for(plan_id)

    async def get_export(self, plan_id: UUID, export_id: UUID) -> PlanExport:
        export = await self._repository.get_export(export_id)
        if export is None or export.plan_id != plan_id:
            raise RemediationNotFound(f"Export {export_id} does not exist on plan {plan_id}.")
        return export

    async def audit(self, plan_id: UUID) -> tuple[tuple[GovernanceEvent, ...], ChainVerification]:
        """The plan's events and whether its chain is intact.

        The verification is what an auditor compares against the head digest recorded in an
        export. It is not a signature and the architecture document says so: anybody able to
        rewrite rows can recompute every digest after the one they changed. What it buys is
        that a *quiet* edit is impossible.
        """
        await self.get_plan(plan_id)
        events, _ = await self._governance.events_for_chain(plan_chain(plan_id))
        return events, verify_chain(events)

    async def preconditions(self, plan: ChangePlan) -> PreconditionReport:
        """Whether the plan still describes the estate ADG last observed.

        Computed on read and never written back, for the reason ADR-0031 gives for baseline
        drift: the plan is a record of what was proposed against a named state, and refreshing
        it would silently invalidate the approval recorded against its digest.
        """
        observed = await self._repository.observed_state(plan.changes)
        basis = await self._basis.current()
        return evaluate_plan(
            plan.plan_id,
            plan.changes,
            observed,
            checked_at=self._now(),
            basis_token=basis.token,
            plan_basis_token=plan.basis_token,
        )

    # ---------------------------------------------------------------------------- create

    async def create_plan(
        self,
        actor: Actor,
        *,
        title: str,
        rationale: str,
        changes: Sequence[PlannedChange],
        campaign_id: UUID | None = None,
    ) -> ChangePlan:
        """Write a draft. Nothing is approved, nothing is measured, nothing is exported.

        The basis token is captured **here**, before anything else, so the plan names the
        state its frozen before-states came from. Reading it afterwards would leave a window
        in which a scan lands and the plan claims a basis it never saw — the same argument
        :meth:`app.simulation.SimulationService.baseline` makes.
        """
        clean_title = validate_title(title)
        clean_rationale = validate_rationale(rationale)
        validate_changes(changes)
        basis = await self._basis.current()
        now = self._now()
        plan = ChangePlan(
            plan_id=uuid4(),
            title=clean_title,
            rationale=clean_rationale,
            status=ChangePlanStatus.DRAFT,
            changes=tuple(sorted(changes, key=lambda change: change.sequence_index)),
            requested_by_subject=actor.subject,
            requested_by_display_name=actor.display_name,
            requested_at=now,
            basis_token=basis.token,
            basis_run_id=basis.latest_run_id,
            basis_captured_at=now,
            campaign_id=campaign_id,
            created_at=now,
            updated_at=now,
        )
        await self._repository.insert_plan(plan)
        await self._repository.insert_changes(plan.plan_id, plan.changes)
        await self._append(
            actor,
            plan,
            GovernanceEventType.PLAN_CREATED,
            now,
            {
                "title": plan.title,
                "change_count": len(plan.changes),
                "basis_token": plan.basis_token,
                "kinds": sorted({change.kind.value for change in plan.changes}),
            },
        )
        await self._session.commit()
        return plan

    async def replace_changes(
        self, actor: Actor, plan_id: UUID, changes: Sequence[PlannedChange]
    ) -> ChangePlan:
        """Rewrite a draft's steps. Refused once anybody has been asked to approve it.

        Editing after submission would change what an approver is answering about while they
        are answering it, and — because :func:`app.remediation.model.plan_digest` covers the
        changes — would leave any recorded approval pinned to a digest the plan no longer has.
        The second is the safety net; this is the rule.
        """
        plan = await self.get_plan(plan_id)
        if not plan.is_editable:
            raise RemediationConflict(
                f"Plan {plan_id} is {plan.status.value} and its changes are fixed. A plan is "
                "editable only while it is a draft: after that somebody has been asked to "
                "approve a specific set of changes. Cancel this plan and write another."
            )
        self._require_author(actor, plan)
        validate_changes(changes)
        ordered = tuple(sorted(changes, key=lambda change: change.sequence_index))
        now = self._now()
        await self._repository.replace_changes(plan_id, ordered)
        # The stored blast radius described the old steps; see clear_simulation.
        await self._repository.clear_simulation(plan_id, at=now)
        await self._session.commit()
        return await self.get_plan(plan_id)

    # -------------------------------------------------------------------------- simulate

    async def simulate(
        self, actor: Actor, plan_id: UUID, *, bounds: SimulationBounds = DEFAULT_BOUNDS
    ) -> SimulatedPlan:
        """Measure the plan with the production access engine, and store what it found.

        The overlay is :func:`app.remediation.translate.overlay_for`, the engine is
        :class:`app.simulation.SimulationService`, and the report is persisted through
        :class:`app.simulation.SimulationStore` — the same rows ``/api/v1/simulations`` reads,
        so the impact behind an approval is a first-class simulation somebody can open, re-run
        and compare rather than a number copied into a plan.

        Re-simulating an approved plan is refused. The approval names a simulation; replacing
        it afterwards would change the evidence an approver acted on without their knowing.
        """
        plan = await self.get_plan(plan_id)
        if plan.status not in (ChangePlanStatus.DRAFT, ChangePlanStatus.PENDING_APPROVAL):
            raise RemediationConflict(
                f"Plan {plan_id} is {plan.status.value} and cannot be re-measured. An approval "
                "names the blast radius it was given; replacing that afterwards would change "
                "the evidence somebody acted on."
            )
        simulation = SimulationService(self._session)
        baseline = await simulation.baseline()
        report = await simulation.run(overlay_for(plan), bounds=bounds, baseline=baseline)
        stored = await SimulationStore(self._session).save(
            report,
            name=f"Change plan: {plan.title}"[:200],
            description=(
                f"Blast radius for ADG change plan {plan.plan_id}. Computed by the production "
                "access engine over an overlay derived from the plan's steps."
            ),
            created_by=actor.subject,
        )
        now = self._now()
        await self._repository.attach_simulation(
            plan_id,
            simulation_id=stored.simulation_id,
            basis_token=baseline.token,
            impact_summary=report.summary.document(),
            at=now,
        )
        await self._append(
            actor,
            plan,
            GovernanceEventType.PLAN_SIMULATED,
            now,
            {
                "simulation_id": str(stored.simulation_id),
                "basis_token": baseline.token,
                "complete": report.complete,
                "inert": report.inert,
                "summary": report.summary.document(),
            },
        )
        await self._session.commit()
        return SimulatedPlan(await self.get_plan(plan_id), report, stored.simulation_id)

    # ---------------------------------------------------------------------------- submit

    async def submit(self, actor: Actor, plan_id: UUID) -> tuple[ChangePlan, PreconditionReport]:
        """Put the plan in front of an approver, or say why it is not ready.

        A draft that fails its preconditions is **refused and left as a draft**. It has not
        yet been presented to anybody as a true statement, and its steps are still editable,
        so the useful outcome is a report the planner can act on rather than a dead plan.
        Approval and export take the harder line; see :meth:`decide` and :meth:`export`.
        """
        plan = await self.get_plan(plan_id)
        validate_transition(plan.status, ChangePlanStatus.PENDING_APPROVAL)
        self._require_author(actor, plan)
        if not plan.has_current_simulation:
            raise RemediationConflict(
                f"Plan {plan_id} has no current blast-radius report, so there is nothing for "
                "an approver to weigh. "
                + (
                    "Run the simulation first."
                    if plan.simulation_id is None
                    else "Its stored simulation was measured against a different collection "
                    "basis than the plan; run it again."
                )
            )
        report = await self.preconditions(plan)
        if not report.satisfied:
            raise RemediationConflict(
                f"Plan {plan_id} is not ready for approval. {summarize_verdict(report)}"
            )
        now = self._now()
        await self._repository.update_status(
            plan_id, ChangePlanStatus.PENDING_APPROVAL, at=now, submitted_at=now
        )
        await self._append(
            actor,
            plan,
            GovernanceEventType.PLAN_SUBMITTED,
            now,
            {
                "plan_digest": plan.digest,
                "simulation_id": str(plan.simulation_id),
                "precondition_counts": report.counts(),
            },
        )
        await self._session.commit()
        return await self.get_plan(plan_id), report

    # ---------------------------------------------------------------------------- decide

    async def decide(
        self,
        actor: Actor,
        plan_id: UUID,
        *,
        decision: ApprovalDecision,
        rationale: str | None = None,
    ) -> ChangePlan:
        """Approve or reject, as somebody other than the person who asked.

        An approval records **what** it approved: the plan digest and the collection basis it
        was answered against, plus the simulation the approver was shown. Export compares all
        three. An approval that recorded none of them would still be attached to the plan
        after the plan changed and after the estate moved, which is how a signed change gets
        executed against facts nobody approved (ADR-0036).
        """
        plan = await self.get_plan(plan_id)
        if plan.status not in (ChangePlanStatus.PENDING_APPROVAL,):
            raise RemediationConflict(
                f"Plan {plan_id} is {plan.status.value}; only a plan awaiting approval can be "
                "approved or rejected."
            )
        if actor.subject == plan.requested_by_subject:
            raise RemediationForbidden(
                f"{actor.subject} requested plan {plan_id} and cannot also approve it. "
                "Somebody who could write a plan and approve it could produce a fully "
                "documented, fully audited removal of anybody's access with one person's "
                "involvement, and the trail would look impeccable. Ask a different approver."
            )
        if decision is ApprovalDecision.REJECT and not (rationale or "").strip():
            raise RemediationValidationError(
                "A rejection must say why. The planner's next step depends entirely on the "
                "reason, and 'rejected' on its own sends them back to guess at it.",
                field="rationale",
            )

        now = self._now()
        report = await self.preconditions(plan)
        if decision is ApprovalDecision.APPROVE and not report.satisfied:
            # Invalidated rather than refused. Unlike a draft, this plan has already been put
            # in front of somebody as a true statement about the estate, and it is not one.
            await self._invalidate(actor, plan, summarize_verdict(report), now)
            await self._session.commit()
            raise RemediationConflict(
                f"Plan {plan_id} can no longer be approved and has been invalidated. "
                f"{summarize_verdict(report)}"
            )

        approval = ApprovalRecord(
            approval_id=uuid4(),
            plan_id=plan_id,
            decision=decision,
            approver_subject=actor.subject,
            approver_display_name=actor.display_name,
            approver_roles=tuple(sorted(actor.roles)),
            decided_at=now,
            rationale=(rationale or "").strip() or None,
            plan_digest=plan.digest,
            basis_token=report.basis_token,
            simulation_id=plan.simulation_id,
        )
        await self._repository.insert_approval(approval)
        if decision is ApprovalDecision.APPROVE:
            await self._repository.update_status(
                plan_id,
                ChangePlanStatus.APPROVED,
                at=now,
                decided_at=now,
                approved_by_subject=actor.subject,
                approved_plan_digest=plan.digest,
                approved_basis_token=report.basis_token,
            )
            event = GovernanceEventType.PLAN_APPROVED
        else:
            await self._repository.update_status(
                plan_id,
                ChangePlanStatus.REJECTED,
                at=now,
                decided_at=now,
                rejection_reason=approval.rationale,
            )
            event = GovernanceEventType.PLAN_REJECTED
        await self._append(
            actor,
            plan,
            event,
            now,
            {
                "approval_id": str(approval.approval_id),
                "plan_digest": approval.plan_digest,
                "basis_token": approval.basis_token,
                "simulation_id": (
                    None if approval.simulation_id is None else str(approval.simulation_id)
                ),
                "rationale_present": approval.rationale is not None,
            },
        )
        await self._session.commit()
        return await self.get_plan(plan_id)

    # ---------------------------------------------------------------------------- export

    async def export(self, actor: Actor, plan_id: UUID) -> tuple[PlanExport, str]:
        """Produce the signed change plan and the runbook that carries it out.

        Five things are true before anything is written, and each of them has stopped being
        true for somebody at some point:

        1. the plan is approved;
        2. the approval is of *this* digest — the plan has not changed since;
        3. every precondition still holds, re-read now rather than trusted from the approval;
        4. the deployment has a signing key;
        5. the caller is neither the requestor nor the approver.

        Rule 5 is the third pair of hands (ADR-0038). It is checked here rather than only in
        the role table, because a person holding two roles is a legitimate configuration and
        this is the rule that still applies to them.

        Returns the stored export and the PowerShell runbook. The runbook is **not** stored:
        it is a pure function of the document, so keeping it would be a second copy of the
        same bytes ageing independently of the renderer — and the renderer's output is what
        somebody runs, so it must come from the current code, not from a row.
        """
        plan = await self.get_plan(plan_id)
        if plan.status is not ChangePlanStatus.APPROVED:
            raise RemediationConflict(
                f"Plan {plan_id} is {plan.status.value} and cannot be exported. Only an "
                "approved plan can be: an export is an instruction, and an instruction "
                "nobody approved is one somebody will carry out anyway."
            )
        if not plan.approval_is_current:
            raise RemediationConflict(
                f"Plan {plan_id} was approved against a different version of itself "
                f"({plan.approved_plan_digest}); it now digests to {plan.digest}. The "
                "approval does not cover what this plan says. Have it approved again."
            )
        if actor.subject in (plan.requested_by_subject, plan.approved_by_subject):
            raise RemediationForbidden(
                f"{actor.subject} is the requestor or the approver of plan {plan_id} and "
                "cannot also produce the signed instruction. Proposing, approving and "
                "carrying out a change are three pairs of hands (ADR-0038)."
            )

        now = self._now()
        report = await self.preconditions(plan)
        if not report.satisfied:
            await self._invalidate(actor, plan, summarize_verdict(report), now)
            await self._session.commit()
            raise RemediationConflict(
                f"Plan {plan_id} can no longer be exported and has been invalidated. "
                f"{summarize_verdict(report)} Nothing was signed."
            )

        approvals = await self._repository.approvals_for(plan_id)
        events, _ = await self._governance.events_for_chain(plan_chain(plan_id))
        # The head as it stands *now*, before the export event is appended below. It cannot
        # include that event: the event's payload carries this document's digest. The index
        # travels with the digest so a verifier can check an exact position rather than
        # comparing against a head that has since moved on by one.
        head = events[-1].digest if events else None
        head_index = events[-1].chain_index if events else None
        document = build_document(
            plan,
            approvals=approvals,
            preconditions=report,
            impact=plan.impact_summary,
            audit_head_digest=head,
            audit_head_index=head_index,
            exported_by_subject=actor.subject,
            exported_by_display_name=actor.display_name,
            exported_at=now,
        )
        # Raises RemediationConflict when no key is configured, before anything is written.
        signature, key_id = sign_document(document, self._settings.remediation_signing_key)
        export = PlanExport(
            export_id=uuid4(),
            plan_id=plan_id,
            exported_by_subject=actor.subject,
            exported_by_display_name=actor.display_name,
            exported_at=now,
            document=document,
            document_digest=document_digest(document),
            signature=signature,
            signature_algorithm=SIGNATURE_ALGORITHM,
            signature_key_id=key_id,
            basis_token=report.basis_token,
            audit_head_digest=head,
        )
        await self._repository.insert_export(export)
        await self._repository.update_status(
            plan_id, ChangePlanStatus.EXPORTED, at=now, exported_at=now
        )
        await self._append(
            actor,
            plan,
            GovernanceEventType.PLAN_EXPORTED,
            now,
            {
                "export_id": str(export.export_id),
                "document_digest": export.document_digest,
                "signature_key_id": key_id,
                "basis_token": export.basis_token,
                # Stated in the trail as well as in the document: an export is an
                # instruction leaving ADG, never a record that anything was changed.
                "performed_by_adg": False,
            },
        )
        await self._session.commit()
        runbook = render_runbook(
            plan,
            document_digest_value=export.document_digest,
            signature=export.signature,
            key_id=export.signature_key_id,
            approver=plan.approved_by_subject,
        )
        return export, runbook

    # ---------------------------------------------------------------------------- cancel

    async def cancel(self, actor: Actor, plan_id: UUID, *, reason: str | None = None) -> ChangePlan:
        """Abandon a plan, keeping everything it said and every answer it got."""
        plan = await self.get_plan(plan_id)
        validate_transition(plan.status, ChangePlanStatus.CANCELED)
        now = self._now()
        await self._repository.update_status(
            plan_id,
            ChangePlanStatus.CANCELED,
            at=now,
            canceled_at=now,
            canceled_by_subject=actor.subject,
        )
        await self._append(
            actor,
            plan,
            GovernanceEventType.PLAN_CANCELED,
            now,
            {"reason": (reason or "").strip() or None, "from_status": plan.status.value},
        )
        await self._session.commit()
        return await self.get_plan(plan_id)

    # --------------------------------------------------------------------------- helpers

    def _require_author(self, actor: Actor, plan: ChangePlan) -> None:
        """Only the requestor edits or submits their own plan.

        Capability is not authority, exactly as it is not in governance: holding
        ``remediation:plan`` admits a request to the route, and this decides whether *this*
        planner may act on *this* plan. Somebody else taking over writes a new one, which
        leaves both attributions intact instead of blending them.
        """
        if actor.subject != plan.requested_by_subject:
            raise RemediationForbidden(
                f"Plan {plan.plan_id} was written by {plan.requested_by_subject}. Editing or "
                "submitting somebody else's plan would attribute their name to changes they "
                "did not make. Write your own plan, or ask them to submit theirs."
            )

    async def _invalidate(
        self, actor: Actor, plan: ChangePlan, reason: str, at: dt.datetime
    ) -> None:
        await self._repository.update_status(
            plan.plan_id,
            ChangePlanStatus.INVALIDATED,
            at=at,
            invalidated_at=at,
            invalidation_reason=reason,
        )
        await self._append(
            actor,
            plan,
            GovernanceEventType.PLAN_INVALIDATED,
            at,
            {"reason": reason, "from_status": plan.status.value},
        )
        logger.info(
            "plan_invalidated",
            extra={"plan_id": str(plan.plan_id), "from_status": plan.status.value},
        )

    async def _append(
        self,
        actor: Actor,
        plan: ChangePlan,
        event_type: GovernanceEventType,
        occurred_at: dt.datetime,
        payload: Mapping[str, Any],
    ) -> GovernanceEvent:
        """One event on the plan's own chain, carrying the plan id in the payload.

        ``governance_audit_events`` has no ``plan_id`` column and deliberately gains none:
        the chain key already names the plan, the payload is inside the digest, and adding a
        column to an append-only hash-chained table for a second kind of subject is how that
        table starts describing everything and meaning nothing.
        """
        return await self._governance.append_event(
            chain_key=plan_chain(plan.plan_id),
            event_type=event_type,
            occurred_at=occurred_at,
            actor_subject=actor.subject,
            actor_display_name=actor.display_name,
            actor_roles=actor.roles,
            campaign_id=plan.campaign_id,
            payload={"plan_id": str(plan.plan_id), **dict(payload)},
        )
