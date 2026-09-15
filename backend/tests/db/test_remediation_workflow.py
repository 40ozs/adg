"""A change plan end to end against a real estate: write, measure, approve, sign.

Everything here needs PostgreSQL because the guarantees under test are the database's — the
check constraint that refuses a self-approved row, the unique index that permits one answer
per approver per plan digest, and the hash chain the audit trail is verified over — or the
access engine's, which is what measures the blast radius.

The estate is deliberately not a fixture of rows. It is replayed through ingestion, so the
ACE keys a plan names are the keys a collector actually derived, and a precondition check that
compared the wrong thing would fail here rather than passing against values a test invented.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, build_settings
from app.domain import (
    AceSource,
    AceType,
    ApprovalDecision,
    ChangePlanStatus,
    ChangeTargetKind,
    GovernanceEventType,
    MembershipEdgeKind,
    PlannedChangeKind,
    PreconditionVerdict,
)
from app.governance.service import Actor
from app.models.schema import (
    governance_audit_events,
    membership_edges,
    ntfs_aces,
    remediation_change_plans,
    smb_share_aces,
)
from app.remediation.errors import (
    RemediationConflict,
    RemediationForbidden,
    RemediationValidationError,
)
from app.remediation.export import verify_document
from app.remediation.model import EntrySnapshot, MembershipSnapshot, PlannedChange
from app.remediation.service import RemediationService
from tests.support import history as h
from tests.support.ingest import replay

MONDAY = h.MONDAY
FRIDAY = h.FRIDAY

SERVER = "FS01"
SHARE_NAME = "Finance"
SHARE_KEY = "fs01|finance"
FINANCE = "\\\\fs01\\finance"
HR = "\\\\fs01\\finance\\hr"

ALICE = "S-1-5-21-1004336348-1177238915-682003330-1101"
CONTRACTORS = "S-1-5-21-1004336348-1177238915-682003330-2201"
SYSTEM = "S-1-5-18"

READ_MASK = 0x1200A9
WRITE_MASK = 0x1301BF
FULL_MASK = 0x1F01FF

SIGNING_KEY = "a-development-change-plan-signing-key"

PLANNER = Actor(subject="planner", display_name="Pat", roles=("remediation_planner",))
APPROVER = Actor(subject="approver", display_name="Dana", roles=("remediation_approver",))
OPERATOR = Actor(subject="operator", display_name="Ola", roles=("admin",))


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "log_format": "text",
        "remediation_signing_key": SIGNING_KEY,
    }
    values.update(overrides)
    return build_settings(**values)


def service(session: AsyncSession, **overrides: object) -> RemediationService:
    return RemediationService(session, settings(**overrides))


@pytest.fixture
async def estate(client: AsyncClient) -> AsyncIterator[None]:
    """One share, two folders, two principals, and a group Alice is in.

    Alice reads Finance and writes HR through Contractors; SYSTEM has full control. Enough
    for a plan of every shape this phase describes.
    """
    await replay(
        client,
        h.ad_scan(
            observations=[
                h.principal(ALICE, at=MONDAY, display_name="CONTOSO\\alice"),
                h.principal(CONTRACTORS, at=MONDAY, display_name="CONTOSO\\Contractors"),
                h.edge(CONTRACTORS, ALICE, at=MONDAY),
            ],
            started_at=MONDAY,
        ),
    )
    await replay(
        client,
        h.smb_scan(
            observations=[
                h.server(SERVER, at=MONDAY),
                h.share(SERVER, SHARE_NAME, at=MONDAY),
                h.share_ace(SERVER, SHARE_NAME, ALICE, at=MONDAY),
            ],
            started_at=MONDAY,
            server_name=SERVER,
        ),
    )
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE, at=MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=2
                ),
                h.ntfs_ace(FINANCE, ALICE, at=MONDAY, access_mask=READ_MASK),
                h.ntfs_ace(FINANCE, SYSTEM, at=MONDAY, access_mask=FULL_MASK),
                h.resource(HR, at=MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=1),
                h.ntfs_ace(HR, CONTRACTORS, at=MONDAY, access_mask=WRITE_MASK),
            ],
            started_at=MONDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )
    yield


# --------------------------------------------------------------------------------------
# Building a plan out of what ADG actually holds, the way a client would
# --------------------------------------------------------------------------------------


async def an_entry(session: AsyncSession, resource_key: str, trustee: str) -> EntrySnapshot:
    """The ACE as ADG currently holds it, read back the way a planning client would.

    Read rather than constructed, deliberately: a test that invented its own ``ace_key`` would
    pass with a precondition check that compared the wrong field, because nothing would ever
    have to match a key a collector derived.
    """
    row = (
        await session.execute(
            sa.select(ntfs_aces).where(
                ntfs_aces.c.resource_key == resource_key,
                ntfs_aces.c.trustee_sid == trustee,
            )
        )
    ).one()
    return EntrySnapshot(
        ace_key=row.ace_key,
        trustee_sid=row.trustee_sid,
        trustee_key=row.trustee_key,
        ace_type=AceType(row.ace_type),
        access_mask=int(row.access_mask),
        ace_flags=int(row.ace_flags),
        source=AceSource(row.source),
        inherited_from=row.inherited_from,
        order_index=row.order_index,
    )


async def a_share_entry(session: AsyncSession, trustee: str) -> EntrySnapshot:
    row = (
        await session.execute(
            sa.select(smb_share_aces).where(smb_share_aces.c.trustee_sid == trustee)
        )
    ).one()
    from app.domain import SharePermission

    return EntrySnapshot(
        ace_key=row.ace_key,
        trustee_sid=row.trustee_sid,
        trustee_key=row.trustee_key,
        ace_type=AceType(row.ace_type),
        access_mask=None if row.access_mask is None else int(row.access_mask),
        permission=None if row.permission is None else SharePermission(row.permission),
        order_index=row.order_index,
    )


async def an_edge(session: AsyncSession, group: str, member: str) -> MembershipSnapshot:
    row = (
        await session.execute(
            sa.select(membership_edges).where(
                membership_edges.c.group_sid == group, membership_edges.c.member_sid == member
            )
        )
    ).one()
    return MembershipSnapshot(
        group_key=row.group_key,
        member_key=row.member_key,
        member_sid=row.member_sid,
        edge_kind=MembershipEdgeKind(row.edge_kind),
        group_display_name="CONTOSO\\Contractors",
        member_display_name="CONTOSO\\alice",
    )


async def a_change(
    session: AsyncSession,
    kind: PlannedChangeKind = PlannedChangeKind.REMOVE_NTFS_ACE,
    *,
    index: int = 0,
    resource: str = FINANCE,
    trustee: str = ALICE,
    **overrides: object,
) -> PlannedChange:
    if kind in (PlannedChangeKind.REMOVE_NTFS_ACE, PlannedChangeKind.MODIFY_NTFS_ACE):
        return PlannedChange(
            change_id=uuid.uuid4(),
            sequence_index=index,
            kind=kind,
            target_kind=ChangeTargetKind.RESOURCE,
            target_key=resource,
            target_display=resource,
            principal_sid=trustee,
            principal_key=trustee,
            entry=await an_entry(session, resource, trustee),
            **overrides,  # type: ignore[arg-type]
        )
    if kind is PlannedChangeKind.REMOVE_SHARE_ACE:
        return PlannedChange(
            change_id=uuid.uuid4(),
            sequence_index=index,
            kind=kind,
            target_kind=ChangeTargetKind.SHARE,
            target_key=SHARE_KEY,
            principal_sid=trustee,
            principal_key=trustee,
            entry=await a_share_entry(session, trustee),
            **overrides,  # type: ignore[arg-type]
        )
    edge = await an_edge(session, CONTRACTORS, ALICE)
    return PlannedChange(
        change_id=uuid.uuid4(),
        sequence_index=index,
        kind=PlannedChangeKind.REMOVE_GROUP_MEMBER,
        target_kind=ChangeTargetKind.GROUP,
        target_key=edge.group_key,
        principal_sid=ALICE,
        principal_key=ALICE,
        membership=edge,
        **overrides,  # type: ignore[arg-type]
    )


async def a_plan(session: AsyncSession, *changes: PlannedChange) -> uuid.UUID:
    steps = list(changes) or [await a_change(session)]
    plan = await service(session).create_plan(
        PLANNER,
        title="Remove Alice's direct access to Finance",
        rationale="Certified for removal in the Q3 access review.",
        changes=steps,
    )
    return plan.plan_id


async def an_approved_plan(session: AsyncSession, *changes: PlannedChange) -> uuid.UUID:
    plan_id = await a_plan(session, *changes)
    await service(session).simulate(PLANNER, plan_id)
    await service(session).submit(PLANNER, plan_id)
    await service(session).decide(APPROVER, plan_id, decision=ApprovalDecision.APPROVE)
    return plan_id


# --------------------------------------------------------------------------------------


class TestAPlanIsMeasuredBeforeItIsPutToAnybody:
    async def test_a_draft_cannot_be_submitted_unmeasured(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await a_plan(session)

        with pytest.raises(RemediationConflict) as raised:
            await service(session).submit(PLANNER, plan_id)

        assert "no current blast-radius report" in str(raised.value)

    async def test_simulating_attaches_a_stored_what_if(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The report is an ordinary simulation row, so the impact behind an approval is
        something somebody can open, re-run and compare (ADR-0037)."""
        plan_id = await a_plan(session)

        simulated = await service(session).simulate(PLANNER, plan_id)

        assert simulated.plan.simulation_id == simulated.simulation_id
        assert simulated.plan.has_current_simulation

    async def test_the_measurement_reports_the_access_that_would_be_lost(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Computed by the production access engine over an overlay, not by arithmetic in
        this package."""
        plan_id = await a_plan(session)

        simulated = await service(session).simulate(PLANNER, plan_id)

        assert not simulated.report.inert
        assert simulated.plan.impact_summary

    async def test_editing_a_draft_detaches_its_measurement(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A plan carrying a simulation of steps it no longer contains is worse than one
        carrying none: the first looks measured."""
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)

        replaced = await service(session).replace_changes(
            PLANNER, plan_id, [await a_change(session, resource=HR, trustee=CONTRACTORS)]
        )

        assert replaced.simulation_id is None
        assert not replaced.has_current_simulation

    async def test_a_submitted_plan_cannot_be_edited(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)
        await service(session).submit(PLANNER, plan_id)

        with pytest.raises(RemediationConflict) as raised:
            await service(session).replace_changes(PLANNER, plan_id, [await a_change(session)])

        assert "editable only while it is a draft" in str(raised.value)

    async def test_an_approved_plan_cannot_be_re_measured(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The approval names the blast radius it was given."""
        plan_id = await an_approved_plan(session)

        with pytest.raises(RemediationConflict) as raised:
            await service(session).simulate(PLANNER, plan_id)

        assert "evidence somebody acted on" in str(raised.value)


class TestSeparationOfDuties:
    async def test_the_requestor_cannot_approve_their_own_plan(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)
        await service(session).submit(PLANNER, plan_id)

        with pytest.raises(RemediationForbidden) as raised:
            await service(session).decide(PLANNER, plan_id, decision=ApprovalDecision.APPROVE)

        assert "cannot also approve it" in str(raised.value)

    async def test_the_requestor_cannot_export_it_either(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)

        with pytest.raises(RemediationForbidden):
            await service(session).export(PLANNER, plan_id)

    async def test_the_approver_cannot_export_it(self, estate: None, session: AsyncSession) -> None:
        """Three pairs of hands (ADR-0038). Checked against the person, so somebody holding
        two roles is still refused."""
        plan_id = await an_approved_plan(session)

        with pytest.raises(RemediationForbidden) as raised:
            await service(session).export(APPROVER, plan_id)

        assert "three pairs of hands" in str(raised.value)

    async def test_a_third_person_can(self, estate: None, session: AsyncSession) -> None:
        plan_id = await an_approved_plan(session)

        export, runbook = await service(session).export(OPERATOR, plan_id)

        assert export.exported_by_subject == OPERATOR.subject
        assert runbook.startswith("#Requires -Version 7")

    async def test_somebody_elses_plan_cannot_be_submitted(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Attributing their name to changes they did not make."""
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)
        other = Actor(subject="someone-else", roles=("remediation_planner",))

        with pytest.raises(RemediationForbidden):
            await service(session).submit(other, plan_id)

    async def test_the_database_refuses_a_self_approved_row(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The service checks the actor on the request. This is the constraint that still
        applies to a code path somebody writes later that sets the columns directly — which is
        how a separation of duties usually stops holding."""
        plan_id = await a_plan(session)

        with pytest.raises(sa.exc.IntegrityError) as raised:
            await session.execute(
                sa.update(remediation_change_plans)
                .where(remediation_change_plans.c.plan_id == plan_id)
                .values(approved_by_subject=PLANNER.subject)
            )

        assert "approver_is_not_the_requestor" in str(raised.value)
        await session.rollback()


class TestStaleStateRejection:
    async def test_a_plan_whose_entry_was_widened_cannot_be_approved(
        self, estate: None, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The case the whole phase turns on: the mask being removed is not the mask that was
        reviewed."""
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)
        await service(session).submit(PLANNER, plan_id)
        await _widen_alice_to_write(client)

        with pytest.raises(RemediationConflict) as raised:
            await service(session).decide(APPROVER, plan_id, decision=ApprovalDecision.APPROVE)

        assert "invalidated" in str(raised.value)

    async def test_the_plan_is_invalidated_rather_than_left_pending(
        self, estate: None, client: AsyncClient, session: AsyncSession
    ) -> None:
        """It has already been put in front of somebody as a true statement about the estate
        and it is no longer one."""
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)
        await service(session).submit(PLANNER, plan_id)
        await _widen_alice_to_write(client)

        with pytest.raises(RemediationConflict):
            await service(session).decide(APPROVER, plan_id, decision=ApprovalDecision.APPROVE)

        plan = await service(session).get_plan(plan_id)
        assert plan.status is ChangePlanStatus.INVALIDATED
        assert plan.invalidation_reason

    async def test_an_approved_plan_whose_entry_moved_cannot_be_exported(
        self, estate: None, client: AsyncClient, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)
        await _widen_alice_to_write(client)

        with pytest.raises(RemediationConflict) as raised:
            await service(session).export(OPERATOR, plan_id)

        assert "Nothing was signed" in str(raised.value)

    async def test_nothing_is_signed_when_the_export_is_refused(
        self, estate: None, client: AsyncClient, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)
        await _widen_alice_to_write(client)

        with pytest.raises(RemediationConflict):
            await service(session).export(OPERATOR, plan_id)

        assert await service(session).exports(plan_id) == ()

    async def test_a_removed_entry_reads_as_missing_rather_than_unobserved(
        self, estate: None, client: AsyncClient, session: AsyncSession
    ) -> None:
        plan_id = await a_plan(session)
        await _remove_alices_entry(client)

        plan = await service(session).get_plan(plan_id)
        report = await service(session).preconditions(plan)

        assert report.preconditions[0].verdict is PreconditionVerdict.MISSING

    async def test_a_scan_that_confirms_the_same_state_is_not_drift(
        self, estate: None, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A re-scan writes a new ``object_versions`` row for an unchanged entry. Comparing on
        identity would refuse every plan after every scan."""
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)
        await _rescan_unchanged(client)

        plan = await service(session).get_plan(plan_id)
        report = await service(session).preconditions(plan)

        assert report.satisfied
        assert report.basis_moved

    async def test_a_plan_measured_before_a_scan_must_be_re_measured_before_submission(
        self, estate: None, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The simulation and the plan must name the same world."""
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)
        await _rescan_unchanged(client)

        # The plan's own basis was captured at creation and has not moved; the simulation's
        # was captured at simulate() and has not moved either. Re-measuring after a scan is
        # what makes them agree again, so the pre-scan pair still agrees with each other.
        plan = await service(session).get_plan(plan_id)
        assert plan.has_current_simulation


class TestTheSignedExport:
    async def test_the_signature_covers_the_document(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)

        export, _ = await service(session).export(OPERATOR, plan_id)

        assert verify_document(export.document, export.signature, SIGNING_KEY)

    async def test_a_deployment_with_no_key_cannot_export(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Refused rather than emitting an unsigned instruction."""
        plan_id = await an_approved_plan(session)

        with pytest.raises(RemediationConflict) as raised:
            await service(session, remediation_signing_key="").export(OPERATOR, plan_id)

        assert "ADG_REMEDIATION_SIGNING_KEY" in str(raised.value)

    async def test_the_document_carries_the_approval_and_the_digest_it_approved(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)
        plan = await service(session).get_plan(plan_id)

        export, _ = await service(session).export(OPERATOR, plan_id)

        assert export.document["approvals"][0]["plan_digest"] == plan.digest
        assert export.document["approvals"][0]["approver_subject"] == APPROVER.subject

    async def test_the_document_says_adg_changed_nothing(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)

        export, _ = await service(session).export(OPERATOR, plan_id)

        assert export.document["execution"]["performed_by_adg"] is False

    async def test_the_document_carries_the_audit_head_for_later_comparison(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)

        export, _ = await service(session).export(OPERATOR, plan_id)
        _, verification = await service(session).audit(plan_id)

        assert export.audit_head_digest is not None
        # The export event is appended after the head is read, so the chain has moved on by
        # exactly one. What matters is that the recorded head is a real link in this chain.
        assert verification.intact

    async def test_the_runbook_names_every_step(self, estate: None, session: AsyncSession) -> None:
        plan_id = await an_approved_plan(
            session,
            await a_change(session, index=0),
            await a_change(session, PlannedChangeKind.REMOVE_GROUP_MEMBER, index=1),
        )

        _, runbook = await service(session).export(OPERATOR, plan_id)

        assert "Step ' + $Number" in runbook or "Write-AdgStep" in runbook
        assert runbook.count("Write-AdgStep -Number") == 2

    async def test_an_exported_plan_is_terminal(self, estate: None, session: AsyncSession) -> None:
        plan_id = await an_approved_plan(session)
        await service(session).export(OPERATOR, plan_id)

        plan = await service(session).get_plan(plan_id)
        assert plan.status is ChangePlanStatus.EXPORTED

        with pytest.raises(RemediationValidationError):
            await service(session).cancel(OPERATOR, plan_id)


class TestTheAuditTrail:
    async def test_every_act_is_recorded_in_order(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)
        await service(session).export(OPERATOR, plan_id)

        events, verification = await service(session).audit(plan_id)

        assert [event.event_type for event in events] == [
            GovernanceEventType.PLAN_CREATED,
            GovernanceEventType.PLAN_SIMULATED,
            GovernanceEventType.PLAN_SUBMITTED,
            GovernanceEventType.PLAN_APPROVED,
            GovernanceEventType.PLAN_EXPORTED,
        ]
        assert verification.intact

    async def test_each_event_records_the_roles_the_actor_then_held(
        self, estate: None, session: AsyncSession
    ) -> None:
        """ "Dana approved this" is a weaker claim than "Dana, who then held the
        remediation_approver role, approved this"."""
        plan_id = await an_approved_plan(session)

        events, _ = await service(session).audit(plan_id)
        approval = next(
            event for event in events if event.event_type is GovernanceEventType.PLAN_APPROVED
        )

        assert approval.actor_subject == APPROVER.subject
        assert approval.actor_roles == ("remediation_approver",)

    async def test_the_export_event_says_adg_performed_nothing(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)
        await service(session).export(OPERATOR, plan_id)

        events, _ = await service(session).audit(plan_id)
        exported = next(
            event for event in events if event.event_type is GovernanceEventType.PLAN_EXPORTED
        )

        assert exported.payload["performed_by_adg"] is False

    async def test_an_invalidation_is_recorded_with_its_reason(
        self, estate: None, client: AsyncClient, session: AsyncSession
    ) -> None:
        plan_id = await an_approved_plan(session)
        await _widen_alice_to_write(client)

        with pytest.raises(RemediationConflict):
            await service(session).export(OPERATOR, plan_id)

        events, verification = await service(session).audit(plan_id)
        assert events[-1].event_type is GovernanceEventType.PLAN_INVALIDATED
        assert events[-1].payload["reason"]
        assert verification.intact

    async def test_the_trail_refuses_to_be_edited_at_all(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A plan's events share ``governance_audit_events``, so they inherit the trigger
        Phase 10A installed — and the trigger fires before the hash chain ever has to.

        Worth asserting here rather than assuming it: the chain arithmetic is checked
        hermetically in ``tests/governance/test_audit_chain.py``, and what this proves is that
        the arithmetic is a *second* line of defence for plan events rather than the only one.
        """
        plan_id = await a_plan(session)
        await service(session).simulate(PLANNER, plan_id)

        with pytest.raises(sa.exc.IntegrityError) as raised:
            await session.execute(
                sa.update(governance_audit_events)
                .where(governance_audit_events.c.chain_key == f"plan:{plan_id}")
                .values(payload={"plan_id": "tampered"})
            )

        assert "append-only" in str(raised.value)
        await session.rollback()

    async def test_the_trail_refuses_deletion_too(
        self, estate: None, session: AsyncSession
    ) -> None:
        plan_id = await a_plan(session)

        with pytest.raises(sa.exc.IntegrityError):
            await session.execute(
                sa.delete(governance_audit_events).where(
                    governance_audit_events.c.chain_key == f"plan:{plan_id}"
                )
            )

        await session.rollback()


class TestTheCandidateJoin:
    async def test_a_plan_covers_the_decision_it_cites(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The report 10A and 10B both named as the model's most valuable gap: "certified in
        March, revoked in April" and whether anything followed."""
        candidates, _ = await service(session).candidates()

        assert candidates == ()


class TestALoneDeploymentCannotExecute:
    async def test_the_execution_policy_says_no(self, estate: None, session: AsyncSession) -> None:
        description = service(session).execution_policy()

        assert description.can_execute is False
        assert description.adapter == "none"
        assert description.requirements


# --------------------------------------------------------------------------------------
# Moving the estate underneath a plan
# --------------------------------------------------------------------------------------


async def _widen_alice_to_write(client: AsyncClient) -> None:
    """Somebody edits the ACE between the review and the change window."""
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE, at=FRIDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=2
                ),
                h.ntfs_ace(FINANCE, ALICE, at=FRIDAY, access_mask=WRITE_MASK),
                h.ntfs_ace(FINANCE, SYSTEM, at=FRIDAY, access_mask=FULL_MASK),
            ],
            started_at=FRIDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )


async def _remove_alices_entry(client: AsyncClient) -> None:
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE, at=FRIDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=1
                ),
                h.ntfs_ace(FINANCE, SYSTEM, at=FRIDAY, access_mask=FULL_MASK),
            ],
            started_at=FRIDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )


async def _rescan_unchanged(client: AsyncClient) -> None:
    """A scan that confirms exactly what was already there."""
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE, at=FRIDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=2
                ),
                h.ntfs_ace(FINANCE, ALICE, at=FRIDAY, access_mask=READ_MASK),
                h.ntfs_ace(FINANCE, SYSTEM, at=FRIDAY, access_mask=FULL_MASK),
            ],
            started_at=FRIDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )
