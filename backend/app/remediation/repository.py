r"""Every query remediation makes, and the only place it touches a database.

Two kinds of read live here and the distinction is the module's whole shape.

**Writes go to four tables, all of them ADG's own.** ``remediation_change_plans``,
``remediation_planned_changes``, ``remediation_approvals`` and ``remediation_exports``. Not
one write in this module names a table a collector fills, and
``tests/remediation/test_isolation.py`` proves it from the syntax tree rather than from
review — the same guard Phase 10A put over ``app/governance``, extended to cover this package
so that a method added in a later phase fails the moment it is written.

**Reads of collected state are read-only and narrow.** The precondition check needs to know
what one ACE looks like *now*, and it asks the ACL tables directly. Those reads exist so that
a plan can be refused; there is no read here that feeds a derivation, because deriving access
is :mod:`app.access_engine`'s job and a second implementation of it in this package would
eventually disagree with the first.

**The audit trail is governance's, reused rather than reimplemented.** A plan's events go into
``governance_audit_events`` on a ``plan:<uuid>`` chain, through
:meth:`app.governance.repository.GovernanceRepository.append_event`. Writing a second
append-only hash-chained table would give an auditor two places to look for one sequence of
events — a campaign produced a decision, the decision produced a plan, somebody approved it —
and two implementations of the chain arithmetic to disagree with each other.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Row, Select, delete, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain import (
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
from app.governance.model import GrantEvidence
from app.governance.repository import GovernanceRepository
from app.history.repository import VersionReader
from app.models.schema import (
    remediation_approvals,
    remediation_change_plans,
    remediation_exports,
    remediation_planned_changes,
    review_decisions,
    review_items,
)
from app.remediation.model import (
    ApprovalRecord,
    ChangePlan,
    EntrySnapshot,
    MembershipSnapshot,
    PlanExport,
    PlannedChange,
)
from app.remediation.preconditions import ObservedState

__all__ = [
    "MAX_PAGE",
    "PlanCandidate",
    "RemediationRepository",
]

MAX_PAGE: Final = 200
_DEFAULT_PAGE: Final = 50


class PlanCandidate:
    """A review decision that asks for a change and has no plan covering it yet.

    This is the join Phase 10A and 10B both named as their most valuable gap: a ``revoke``
    recorded in March, a proposal beside it, and nothing anywhere that says whether anybody
    did anything. Answering it is one query rather than a new table, because every part of
    the answer was already stored — it simply had no question put to it.
    """

    __slots__ = (
        "campaign_id",
        "decided_at",
        "decision",
        "decision_id",
        "grants",
        "item_id",
        "principal_key",
        "principal_sid",
        "rationale",
        "target_key",
        "target_kind",
        "target_path",
    )

    def __init__(
        self,
        *,
        item_id: UUID,
        campaign_id: UUID,
        decision_id: UUID,
        decision: DecisionKind,
        decided_at: dt.datetime,
        rationale: str | None,
        target_kind: ReviewTargetKind,
        target_key: str,
        target_path: str | None,
        principal_key: str,
        principal_sid: str,
        grants: Sequence[Mapping[str, Any]],
    ) -> None:
        self.item_id = item_id
        self.campaign_id = campaign_id
        self.decision_id = decision_id
        self.decision = decision
        self.decided_at = decided_at
        self.rationale = rationale
        self.target_kind = target_kind
        self.target_key = target_key
        self.target_path = target_path
        self.principal_key = principal_key
        self.principal_sid = principal_sid
        self.grants = tuple(dict(grant) for grant in grants)


class RemediationRepository:
    """Reads and writes for change plans. Four tables written, several read."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Composed rather than reimplemented. "What is on this ACL right now" is a question
        # governance already answers for baseline drift, and two implementations of it would
        # eventually disagree -- at which point a reviewer and a planner would be looking at
        # different pictures of the same folder.
        self._governance = GovernanceRepository(session)
        self._versions = VersionReader(session)

    # --------------------------------------------------------------------------- plans

    async def insert_plan(self, plan: ChangePlan) -> None:
        await self._session.execute(
            insert(remediation_change_plans).values(
                plan_id=plan.plan_id,
                title=plan.title,
                rationale=plan.rationale,
                status=plan.status.value,
                campaign_id=plan.campaign_id,
                requested_by_subject=plan.requested_by_subject,
                requested_by_display_name=plan.requested_by_display_name,
                requested_at=plan.requested_at,
                basis_token=plan.basis_token,
                basis_run_id=plan.basis_run_id,
                basis_captured_at=plan.basis_captured_at,
                simulation_id=plan.simulation_id,
                simulation_basis_token=plan.simulation_basis_token,
                impact_summary=dict(plan.impact_summary),
                created_at=plan.created_at or plan.requested_at,
                updated_at=plan.updated_at or plan.requested_at,
            )
        )

    async def insert_changes(self, plan_id: UUID, changes: Sequence[PlannedChange]) -> None:
        if not changes:
            return
        await self._session.execute(
            insert(remediation_planned_changes),
            [self._change_values(plan_id, change) for change in changes],
        )

    @staticmethod
    def _change_values(plan_id: UUID, change: PlannedChange) -> dict[str, Any]:
        entry = change.entry
        edge = change.membership
        # The whole frozen snapshot goes into before_state, and the digest of its *content*
        # goes into before_digest. The two are not interchangeable: the snapshot carries
        # provenance (which version it came from, when it was last confirmed) that the digest
        # deliberately excludes, so that a re-scan confirming an unchanged entry does not read
        # as somebody having edited it.
        before = (
            entry.document() if entry is not None else edge.document()  # type: ignore[union-attr]
        )
        return {
            "change_id": change.change_id,
            "plan_id": plan_id,
            "sequence_index": change.sequence_index,
            "kind": change.kind.value,
            "target_kind": change.target_kind.value,
            "target_key": change.target_key,
            "target_display": change.target_display,
            "principal_sid": change.principal_sid,
            "principal_key": change.principal_key,
            "principal_display_name": change.principal_display_name,
            "ace_key": None if entry is None else entry.ace_key,
            "group_key": None if edge is None else edge.group_key,
            "member_key": None if edge is None else edge.member_key,
            "edge_kind": None if edge is None else edge.edge_kind.value,
            "before_state": before,
            "before_digest": change.precondition_digest,
            "after_access_mask": change.after_access_mask,
            "after_permission": (
                None if change.after_permission is None else change.after_permission.value
            ),
            "replacement_group_key": change.replacement_group_key,
            "replacement_group_sid": change.replacement_group_sid,
            "replacement_group_display_name": change.replacement_group_display_name,
            "item_id": change.item_id,
            "decision_id": change.decision_id,
            "proposal_id": change.proposal_id,
            "risk_finding_key": change.risk_finding_key,
            "notes": change.notes,
            "created_at": dt.datetime.now(dt.UTC),
        }

    async def get_plan(self, plan_id: UUID) -> ChangePlan | None:
        row = (
            await self._session.execute(
                select(remediation_change_plans).where(
                    remediation_change_plans.c.plan_id == plan_id
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return _plan(row, await self.changes_for(plan_id))

    async def changes_for(self, plan_id: UUID) -> tuple[PlannedChange, ...]:
        rows = (
            await self._session.execute(
                select(remediation_planned_changes)
                .where(remediation_planned_changes.c.plan_id == plan_id)
                .order_by(remediation_planned_changes.c.sequence_index)
            )
        ).all()
        return tuple(_change(row) for row in rows)

    async def list_plans(
        self,
        *,
        status: ChangePlanStatus | None = None,
        campaign_id: UUID | None = None,
        requested_by: str | None = None,
        limit: int = _DEFAULT_PAGE,
        offset: int = 0,
    ) -> tuple[tuple[ChangePlan, ...], bool]:
        """A page of plans, newest first, each with its changes.

        Changes are fetched in one further query for the whole page rather than one per plan.
        A listing of fifty plans is a screen, and fifty extra round trips to render it is the
        shape of slowness nobody notices until an estate is large.
        """
        capped = max(1, min(limit, MAX_PAGE))
        statement: Select[Any] = select(remediation_change_plans)
        if status is not None:
            statement = statement.where(remediation_change_plans.c.status == status.value)
        if campaign_id is not None:
            statement = statement.where(remediation_change_plans.c.campaign_id == campaign_id)
        if requested_by is not None:
            statement = statement.where(
                remediation_change_plans.c.requested_by_subject == requested_by
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    remediation_change_plans.c.requested_at.desc(),
                    remediation_change_plans.c.plan_id,
                )
                .offset(offset)
                .limit(capped + 1)
            )
        ).all()
        page = rows[:capped]
        plan_ids = [row.plan_id for row in page]
        by_plan: dict[UUID, list[PlannedChange]] = {plan_id: [] for plan_id in plan_ids}
        if plan_ids:
            change_rows = (
                await self._session.execute(
                    select(remediation_planned_changes)
                    .where(remediation_planned_changes.c.plan_id.in_(plan_ids))
                    .order_by(remediation_planned_changes.c.sequence_index)
                )
            ).all()
            for row in change_rows:
                by_plan[row.plan_id].append(_change(row))
        return (
            tuple(_plan(row, tuple(by_plan[row.plan_id])) for row in page),
            len(rows) > capped,
        )

    async def update_status(
        self,
        plan_id: UUID,
        status: ChangePlanStatus,
        *,
        at: dt.datetime,
        **columns: Any,
    ) -> None:
        """Move a plan's status and set whatever else the transition records.

        The lifecycle rule itself lives in :func:`app.remediation.model.validate_transition`
        and is applied by the service before this is called. This is the write.
        """
        await self._session.execute(
            update(remediation_change_plans)
            .where(remediation_change_plans.c.plan_id == plan_id)
            .values(status=status.value, updated_at=at, **columns)
        )

    async def attach_simulation(
        self,
        plan_id: UUID,
        *,
        simulation_id: UUID,
        basis_token: str,
        impact_summary: Mapping[str, Any],
        at: dt.datetime,
    ) -> None:
        await self._session.execute(
            update(remediation_change_plans)
            .where(remediation_change_plans.c.plan_id == plan_id)
            .values(
                simulation_id=simulation_id,
                simulation_basis_token=basis_token,
                impact_summary=dict(impact_summary),
                updated_at=at,
            )
        )

    async def clear_simulation(self, plan_id: UUID, *, at: dt.datetime) -> None:
        """Detach a plan's blast-radius report.

        Called when a draft's steps change. The stored report described the old steps, and a
        plan carrying a simulation of changes it no longer contains is worse than one carrying
        none: the first looks measured. Clearing it is what makes "submitted without a current
        simulation" unreachable rather than merely discouraged.
        """
        await self._session.execute(
            update(remediation_change_plans)
            .where(remediation_change_plans.c.plan_id == plan_id)
            .values(
                simulation_id=None,
                simulation_basis_token=None,
                impact_summary={},
                updated_at=at,
            )
        )

    async def replace_changes(self, plan_id: UUID, changes: Sequence[PlannedChange]) -> None:
        """Swap a draft's changes wholesale.

        Delete and re-insert rather than a diff, because the set is small, the plan is a
        draft, and a partial update would leave step numbers to be reconciled against rows
        that are about to be replaced anyway. Only ever reached for a plan in ``draft``; the
        service checks that first.
        """
        await self._session.execute(
            delete(remediation_planned_changes).where(
                remediation_planned_changes.c.plan_id == plan_id
            )
        )
        await self.insert_changes(plan_id, changes)

    # ----------------------------------------------------------------------- approvals

    async def insert_approval(self, approval: ApprovalRecord) -> None:
        await self._session.execute(
            insert(remediation_approvals).values(
                approval_id=approval.approval_id,
                plan_id=approval.plan_id,
                decision=approval.decision.value,
                approver_subject=approval.approver_subject,
                approver_display_name=approval.approver_display_name,
                approver_roles=list(approval.approver_roles),
                decided_at=approval.decided_at,
                rationale=approval.rationale,
                plan_digest=approval.plan_digest,
                basis_token=approval.basis_token,
                simulation_id=approval.simulation_id,
                created_at=approval.decided_at,
            )
        )

    async def approvals_for(self, plan_id: UUID) -> tuple[ApprovalRecord, ...]:
        rows = (
            await self._session.execute(
                select(remediation_approvals)
                .where(remediation_approvals.c.plan_id == plan_id)
                .order_by(remediation_approvals.c.decided_at, remediation_approvals.c.approval_id)
            )
        ).all()
        return tuple(_approval(row) for row in rows)

    # ------------------------------------------------------------------------- exports

    async def insert_export(self, export: PlanExport) -> None:
        await self._session.execute(
            insert(remediation_exports).values(
                export_id=export.export_id,
                plan_id=export.plan_id,
                exported_by_subject=export.exported_by_subject,
                exported_by_display_name=export.exported_by_display_name,
                exported_at=export.exported_at,
                document=dict(export.document),
                document_digest=export.document_digest,
                signature=export.signature,
                signature_algorithm=export.signature_algorithm,
                signature_key_id=export.signature_key_id,
                basis_token=export.basis_token,
                audit_head_digest=export.audit_head_digest,
                created_at=export.exported_at,
            )
        )

    async def exports_for(self, plan_id: UUID) -> tuple[PlanExport, ...]:
        rows = (
            await self._session.execute(
                select(remediation_exports)
                .where(remediation_exports.c.plan_id == plan_id)
                .order_by(remediation_exports.c.exported_at, remediation_exports.c.export_id)
            )
        ).all()
        return tuple(_export(row) for row in rows)

    async def get_export(self, export_id: UUID) -> PlanExport | None:
        row = (
            await self._session.execute(
                select(remediation_exports).where(remediation_exports.c.export_id == export_id)
            )
        ).one_or_none()
        return None if row is None else _export(row)

    # -------------------------------------------------------- collected state, read-only

    async def observed_state(self, changes: Sequence[PlannedChange]) -> dict[UUID, ObservedState]:
        """What ADG currently holds about every change's target.

        **Read from the timeline, not from the ACL tables.** That is the whole correctness of
        this method, and it is not obvious. ``ntfs_aces``, ``smb_share_aces`` and
        ``membership_edges`` are accumulate-only: ingestion has no delete path at all, and a
        run that reconciles a scope records absence as a *tombstone* in ``object_versions``
        rather than by removing a row (``app/ingestion/service.py``, ADR-0013). And an ACE key
        is content-addressed, so widening a mask writes a **new** row and leaves the old one
        standing.

        So a precondition check that looked the ``ace_key`` up in ``ntfs_aces`` would find the
        entry as it stood before somebody edited it, report it unchanged, and let a signed
        instruction go out against a mask nobody had reviewed — which is the exact failure
        this check exists to prevent, arrived at by reading the wrong table. Found here by a
        test: ``test_a_removed_entry_reads_as_missing_rather_than_unobserved`` reported
        ``satisfied`` against an entry that had been gone for four days.

        The entry reads go through :class:`app.governance.repository.GovernanceRepository`,
        which already answers "what is on this ACL at an instant" for baseline drift. One
        implementation of that question rather than two: a second one would eventually
        disagree, and a reviewer and a planner would be shown different pictures of the same
        folder.

        Keyed by ``change_id``; a change with no answer is **absent** from the mapping, which
        :func:`app.remediation.preconditions.evaluate_plan` reads as *unobserved* — the safe
        reading of "I have nothing for this".
        """
        now = dt.datetime.now(dt.UTC)
        targets = [
            (
                ReviewTargetKind.SHARE
                if change.target_kind is ChangeTargetKind.SHARE
                else ReviewTargetKind.RESOURCE,
                change.target_key,
            )
            for change in changes
            if change.entry is not None
        ]
        grants = await self._governance.grants_on_targets_at(targets, now) if targets else {}
        presence = await self._governance.target_presence_at(targets, now) if targets else {}

        group_keys = list(
            dict.fromkeys(
                change.membership.group_key for change in changes if change.membership is not None
            )
        )
        members_by_group = await self._current_members(group_keys, now)
        observed_groups = await self._observed_groups(group_keys, now)

        states: dict[UUID, ObservedState] = {}
        for change in changes:
            if change.entry is not None:
                kind = (
                    ReviewTargetKind.SHARE
                    if change.target_kind is ChangeTargetKind.SHARE
                    else ReviewTargetKind.RESOURCE
                )
                exists, _ = presence.get((kind.value, change.target_key), (None, None))
                on_target = grants.get((kind.value, change.target_key), ())
                found = next(
                    (
                        grant
                        for grant in on_target
                        if grant.evidence.ace_key == change.entry.ace_key
                    ),
                    None,
                )
                states[change.change_id] = ObservedState(
                    # ``exists is None`` means ADG holds no version covering now and has
                    # nothing to say -- which is *unobserved*, and must never be rendered as
                    # the object having been removed.
                    target_observed=bool(exists),
                    entry=None if found is None else _snapshot(found.evidence),
                    last_observed_at=(None if found is None else found.evidence.last_confirmed_at),
                )
            else:
                edge = change.membership
                assert edge is not None
                states[change.change_id] = ObservedState(
                    # A group ADG holds no current version of is a group nobody has looked at.
                    # Weaker than the resource case -- a principal version says the group was
                    # seen, not that its membership was re-enumerated on that run -- and the
                    # limit is stated in docs/architecture/remediation.md rather than papered
                    # over here.
                    target_observed=edge.group_key in observed_groups,
                    membership_present=edge.member_key
                    in members_by_group.get(edge.group_key, frozenset()),
                )
        return states

    async def _current_members(
        self, group_keys: Sequence[str], at: dt.datetime
    ) -> dict[str, frozenset[str]]:
        """The direct members of each group as of ``at``, from the timeline.

        ``container_key`` on a membership-edge version is the group and ``related_key`` is the
        member (``app/history/bindings.py``), so this is the indexed read that column exists
        for. ``present_only`` is left at its default: a tombstoned edge is one that has been
        removed, and it must not come back as a member.
        """
        if not group_keys:
            return {}
        versions = await self._versions.contained_at(
            ObservationKind.MEMBERSHIP_EDGE, list(group_keys), at
        )
        members: dict[str, set[str]] = {key: set() for key in group_keys}
        for version in versions:
            if version.container_key is not None and version.related_key is not None:
                members.setdefault(version.container_key, set()).add(version.related_key)
        return {key: frozenset(value) for key, value in members.items()}

    async def _observed_groups(self, group_keys: Sequence[str], at: dt.datetime) -> frozenset[str]:
        """Which groups ADG holds any version of -- present or tombstoned -- at ``at``.

        ``present_only=False`` is load-bearing, exactly as it is in
        :meth:`app.governance.repository.GovernanceRepository.target_presence_at`: a group
        that was deleted has been *looked at*, and reporting it as unobserved would tell an
        operator nobody had scanned when in fact somebody had scanned and found it gone.
        """
        if not group_keys:
            return frozenset()
        versions = await self._versions.versions_at(
            ObservationKind.PRINCIPAL, list(group_keys), at, present_only=False
        )
        return frozenset(versions)

    # ---------------------------------------------------------------------- candidates

    async def candidates(
        self,
        *,
        campaign_id: UUID | None = None,
        limit: int = _DEFAULT_PAGE,
        offset: int = 0,
    ) -> tuple[tuple[PlanCandidate, ...], bool]:
        """Current ``revoke`` and ``modify`` decisions that no plan step cites yet.

        "Decided in March, and nothing has happened since" — the report 10A and 10B both
        named as the thing the model could not show. Superseded decisions are excluded: a
        reviewer who changed their mind has not left work outstanding, and a candidate list
        that included the old answer would ask somebody to act on a judgment that has been
        withdrawn.

        A decision is covered when **any** plan step cites it, whatever that plan's status.
        A plan that was rejected or cancelled still means somebody looked; leaving the
        decision in the candidate list would send the next planner to write it again.
        """
        capped = max(1, min(limit, MAX_PAGE))
        covered = (
            select(remediation_planned_changes.c.decision_id)
            .where(remediation_planned_changes.c.decision_id == review_decisions.c.decision_id)
            .exists()
        )
        statement = (
            select(
                review_decisions.c.decision_id,
                review_decisions.c.item_id,
                review_decisions.c.campaign_id,
                review_decisions.c.decision,
                review_decisions.c.decided_at,
                review_decisions.c.rationale,
                review_items.c.target_kind,
                review_items.c.target_key,
                review_items.c.target_path,
                review_items.c.principal_key,
                review_items.c.principal_sid,
                review_items.c.grants,
            )
            .join(review_items, review_items.c.item_id == review_decisions.c.item_id)
            .where(
                review_decisions.c.superseded_at.is_(None),
                review_decisions.c.decision.in_(
                    (DecisionKind.REVOKE.value, DecisionKind.MODIFY.value)
                ),
                ~covered,
            )
        )
        if campaign_id is not None:
            statement = statement.where(review_decisions.c.campaign_id == campaign_id)
        rows = (
            await self._session.execute(
                statement.order_by(
                    review_decisions.c.decided_at.desc(), review_decisions.c.decision_id
                )
                .offset(offset)
                .limit(capped + 1)
            )
        ).all()
        return (
            tuple(
                PlanCandidate(
                    item_id=row.item_id,
                    campaign_id=row.campaign_id,
                    decision_id=row.decision_id,
                    decision=DecisionKind(row.decision),
                    decided_at=row.decided_at,
                    rationale=row.rationale,
                    target_kind=ReviewTargetKind(row.target_kind),
                    target_key=row.target_key,
                    target_path=row.target_path,
                    principal_key=row.principal_key,
                    principal_sid=row.principal_sid,
                    grants=row.grants or (),
                )
                for row in rows[:capped]
            ),
            len(rows) > capped,
        )

    async def decision_is_covered(self, decision_id: UUID) -> bool:
        """Whether any plan step already cites this decision."""
        return bool(
            (
                await self._session.execute(
                    select(exists().where(remediation_planned_changes.c.decision_id == decision_id))
                )
            ).scalar()
        )

    async def plan_count(self) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count()).select_from(remediation_change_plans)
                )
            ).scalar_one()
        )


# ------------------------------------------------------------------------------ helpers


def _snapshot(evidence: GrantEvidence) -> EntrySnapshot:
    """One currently observed entry, in the shape the precondition compares.

    A straight field copy: :class:`~app.governance.model.GrantEvidence` and
    :class:`~app.remediation.model.EntrySnapshot` describe the same thing for two different
    purposes -- one is what a reviewer certified, the other is what an instruction acts on --
    and they are kept as separate types so that a change to what a review freezes cannot
    silently change what an export re-checks.
    """
    return EntrySnapshot(
        ace_key=evidence.ace_key,
        trustee_sid=evidence.trustee_sid,
        trustee_key=evidence.trustee_key,
        ace_type=evidence.ace_type,
        access_mask=evidence.access_mask,
        permission=evidence.permission,
        ace_flags=evidence.ace_flags,
        source=evidence.source,
        inherited_from=evidence.inherited_from,
        order_index=evidence.order_index,
        version_id=evidence.version_id,
        observed_from=evidence.observed_from,
        last_confirmed_at=evidence.last_confirmed_at,
    )


def _plan(row: Row[Any], changes: tuple[PlannedChange, ...]) -> ChangePlan:
    return ChangePlan(
        plan_id=row.plan_id,
        title=row.title,
        rationale=row.rationale,
        status=ChangePlanStatus(row.status),
        changes=changes,
        requested_by_subject=row.requested_by_subject,
        requested_by_display_name=row.requested_by_display_name,
        requested_at=row.requested_at,
        basis_token=row.basis_token,
        basis_run_id=row.basis_run_id,
        basis_captured_at=row.basis_captured_at,
        campaign_id=row.campaign_id,
        simulation_id=row.simulation_id,
        simulation_basis_token=row.simulation_basis_token,
        impact_summary=row.impact_summary or {},
        submitted_at=row.submitted_at,
        decided_at=row.decided_at,
        approved_by_subject=row.approved_by_subject,
        approved_plan_digest=row.approved_plan_digest,
        approved_basis_token=row.approved_basis_token,
        rejection_reason=row.rejection_reason,
        exported_at=row.exported_at,
        invalidated_at=row.invalidated_at,
        invalidation_reason=row.invalidation_reason,
        canceled_at=row.canceled_at,
        canceled_by_subject=row.canceled_by_subject,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _change(row: Row[Any]) -> PlannedChange:
    """Rebuild a change through its own constructor, so a stored row that would no longer be
    accepted is refused on the way *out*.

    The same choice :class:`app.simulation.store.StoredSimulation` makes. A plan written under
    rules that have since tightened — a narrowing that would now be refused, a kind that has
    been retired — must not be quietly exported under rules it was never validated against.
    """
    before = row.before_state or {}
    entry = None if row.ace_key is None else EntrySnapshot.from_document(before)
    membership = (
        None
        if row.group_key is None
        else MembershipSnapshot(
            group_key=row.group_key,
            member_key=row.member_key,
            member_sid=before.get("member_sid", ""),
            edge_kind=MembershipEdgeKind(row.edge_kind),
            member_kind=(
                None if before.get("member_kind") is None else PrincipalKind(before["member_kind"])
            ),
            group_display_name=before.get("group_display_name"),
            member_display_name=before.get("member_display_name"),
            version_id=before.get("version_id"),
        )
    )
    return PlannedChange(
        change_id=row.change_id,
        sequence_index=row.sequence_index,
        kind=PlannedChangeKind(row.kind),
        target_kind=ChangeTargetKind(row.target_kind),
        target_key=row.target_key,
        target_display=row.target_display,
        principal_sid=row.principal_sid,
        principal_key=row.principal_key,
        principal_display_name=row.principal_display_name,
        entry=entry,
        membership=membership,
        after_access_mask=(None if row.after_access_mask is None else int(row.after_access_mask)),
        after_permission=(
            None if row.after_permission is None else SharePermission(row.after_permission)
        ),
        replacement_group_key=row.replacement_group_key,
        replacement_group_sid=row.replacement_group_sid,
        replacement_group_display_name=row.replacement_group_display_name,
        item_id=row.item_id,
        decision_id=row.decision_id,
        proposal_id=row.proposal_id,
        risk_finding_key=row.risk_finding_key,
        notes=row.notes,
    )


def _approval(row: Row[Any]) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row.approval_id,
        plan_id=row.plan_id,
        decision=ApprovalDecision(row.decision),
        approver_subject=row.approver_subject,
        approver_display_name=row.approver_display_name,
        approver_roles=tuple(row.approver_roles or ()),
        decided_at=row.decided_at,
        rationale=row.rationale,
        plan_digest=row.plan_digest,
        basis_token=row.basis_token,
        simulation_id=row.simulation_id,
    )


def _export(row: Row[Any]) -> PlanExport:
    return PlanExport(
        export_id=row.export_id,
        plan_id=row.plan_id,
        exported_by_subject=row.exported_by_subject,
        exported_by_display_name=row.exported_by_display_name,
        exported_at=row.exported_at,
        document=row.document or {},
        document_digest=row.document_digest,
        signature=row.signature,
        signature_algorithm=row.signature_algorithm,
        signature_key_id=row.signature_key_id,
        basis_token=row.basis_token,
        audit_head_digest=row.audit_head_digest,
    )
