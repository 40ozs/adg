r"""Controlled validation: does a simulation's prediction match what actually happens?

Phase 9A checked a simulation against the engine that produced it, which proves consistency.
This suite checks it against the estate. For each supported change type the procedure is the
same five steps, and every one of them is real:

1. build a known permission state and **collect it** through the ingestion API;
2. run the production simulation over a proposed change;
3. apply the equivalent change **fixture-side** — edit the observation set the way Windows
   would have changed the object;
4. **recollect**, as three reconciling collector runs;
5. resolve the same pairs again and compare predicted with observed.

The apparatus is ``tests/support/equivalence.py``; its docstring explains why the
recollection is three runs and why the post-change answer is read two ways.

## The one exception, and it is measured rather than asserted

ADG deletes nothing on ingestion — an absent observation is not evidence of removal — and
current-state reads are not routed through presence. So after a reconciled scan has proved an
ACE gone, the **live** engine still counts it while the **point-in-time** engine does not.
Every removal case here therefore agrees with the as-of reading and disagrees with the live
one, and :class:`TestTheKnownExceptionIsMeasured` pins that divergence with the exact masks.
It is Phase 7A's limitation 1 and Phase 9A's limitation 6, and it belongs in a test rather
than only in a document: the day somebody routes current-state reads through presence, this
suite tells them the limitation is gone.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import AccessPath
from app.domain import AceType, MembershipEdgeKind, SharePermission
from app.simulation import (
    AccessDelta,
    ChangeKind,
    ChangeOutcome,
    ImpactDirection,
    InheritanceChange,
    InheritedAceDisposition,
    MembershipChange,
    NtfsAceChange,
    ScopeKind,
    ShareAceChange,
    SimulationOverlay,
    SimulationReport,
    SimulationScope,
    SimulationService,
)
from tests.support.equivalence import (
    MODIFY,
    READ_EXECUTE,
    AceSpec,
    ControlledEstate,
    EdgeSpec,
    PrincipalSpec,
    ShareAceSpec,
    compare,
    delta_for,
    estate,
    observe,
    recollect,
)

pytestmark = pytest.mark.anyio

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN}-1104"
BOB = f"{DOMAIN}-1105"
FINANCE_RW = f"{DOMAIN}-1202"
FINANCE_RO = f"{DOMAIN}-1203"
CAROL = f"{DOMAIN}-1106"

FULL_CONTROL = 0x001F01FF


async def run_simulation(
    session: AsyncSession,
    overlay: SimulationOverlay,
    pairs: list[tuple[str, str]],
) -> SimulationReport:
    """Evaluate a proposal over exactly the pairs this test will measure.

    A ``pair`` scope per pair rather than the affected scope, so the comparison is over the
    pairs the test names rather than over whichever set the overlay happened to imply. The
    affected scope has its own coverage in ``tests/db/test_simulation.py``; what is under
    test here is whether the arithmetic matches reality.
    """
    service = SimulationService(session)
    deltas: list[AccessDelta] = []
    applications: tuple[Any, ...] = ()
    report: SimulationReport | None = None
    for subject_key, resource_key in pairs:
        report = await service.run(
            overlay,
            scope=SimulationScope(
                kind=ScopeKind.PAIR, subject_key=subject_key, resource_key=resource_key
            ),
        )
        deltas.extend(report.deltas)
        applications = report.applications
    assert report is not None, "at least one pair is required"
    return SimulationReport(
        overlay=report.overlay,
        baseline=report.baseline,
        scope=report.scope,
        bounds=report.bounds,
        applications=applications,
        deltas=tuple(deltas),
        summary=report.summary,
        cost=report.cost,
        truncation=report.truncation,
    )


async def validate(
    client: AsyncClient,
    session: AsyncSession,
    *,
    before_state: ControlledEstate,
    after_state: ControlledEstate,
    overlay: SimulationOverlay,
    pairs: list[tuple[str, str]],
) -> Any:
    """The whole procedure, as one call. Returns the equivalence and the simulation report."""
    await recollect(client, before_state)
    keys = [(subject, resource.casefold()) for subject, resource in pairs]
    observed_before = await observe(session, keys)
    report = await run_simulation(session, overlay, keys)

    await recollect(client, after_state)
    session.expire_all()
    observed_after = await observe(session, keys)

    equivalence = compare(report, observed_before, observed_after)
    assert all(item.baseline_agrees for item in equivalence.comparisons), (
        "The simulation's baseline disagrees with what was collected, so the harness changed "
        "something it did not mean to:\n  " + equivalence.report()
    )
    return equivalence, report


class TestMembershipChanges:
    async def test_removing_a_membership_predicts_the_loss_that_actually_happens(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The change an administrator makes most often, and the one they most often get
        wrong: Alice is in ``Finance-RW`` and in nothing else that reaches the directory."""
        before = estate()
        after = before.without_membership(FINANCE_RW, ALICE)
        overlay = SimulationOverlay.from_changes(
            [
                MembershipChange(
                    kind=ChangeKind.REMOVE_MEMBER,
                    group_key=FINANCE_RW,
                    member_key=ALICE,
                    edge_kind=MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
                )
            ]
        )

        equivalence, report = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(ALICE, before.path)],
        )

        delta = delta_for(report, ALICE, before.path)
        assert delta is not None
        assert delta.direction is ImpactDirection.LOST_ACCESS
        assert equivalence.equivalent, equivalence.report()

    async def test_adding_a_membership_predicts_the_access_that_actually_appears(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Carol is in nothing. Putting her in ``Finance-RW`` must deliver exactly Modify."""
        before = replace(
            estate(),
            principals=(*estate().principals, PrincipalSpec(CAROL, "user", "Carol Nolan")),
        )
        after = before.with_membership(EdgeSpec(FINANCE_RW, CAROL))
        overlay = SimulationOverlay.from_changes(
            [MembershipChange(kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_RW, member_key=CAROL)]
        )

        equivalence, report = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(CAROL, before.path)],
        )

        delta = delta_for(report, CAROL, before.path)
        assert delta is not None
        assert delta.direction is ImpactDirection.GAINED_ACCESS
        assert delta.after.rights.value == MODIFY
        assert equivalence.equivalent, equivalence.report()

    async def test_an_addition_agrees_with_the_live_reading_too(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """An addition is the case with no exception: nothing has to be proved absent, so
        the current-state engine and the point-in-time engine give the same answer."""
        before = estate()
        after = before.with_membership(EdgeSpec(FINANCE_RW, BOB))
        overlay = SimulationOverlay.from_changes(
            [MembershipChange(kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_RW, member_key=BOB)]
        )

        equivalence, _ = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(BOB, before.path)],
        )

        assert equivalence.equivalent, equivalence.report()
        assert all(item.live_agrees for item in equivalence.comparisons), equivalence.report()


class TestNtfsAceChanges:
    async def test_adding_an_entry_predicts_the_rights_it_actually_delivers(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = estate()
        new = AceSpec(BOB, "allow", FULL_CONTROL)
        after = before.with_ntfs_ace(new)
        overlay = SimulationOverlay.from_changes(
            [
                NtfsAceChange(
                    kind=ChangeKind.ADD_NTFS_ACE,
                    resource_key=before.path,
                    trustee_sid=BOB,
                    ace_type=AceType.ALLOW,
                    access_mask=FULL_CONTROL,
                    ace_flags=new.ace_flags,
                )
            ]
        )

        equivalence, report = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(BOB, before.path)],
        )

        delta = delta_for(report, BOB, before.path)
        assert delta is not None
        assert delta.direction is ImpactDirection.EXPANDED
        assert equivalence.equivalent, equivalence.report()

    async def test_removing_an_entry_predicts_the_loss_that_actually_happens(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = estate()
        target = before.ntfs_aces[1]  # Finance-RO, Read & Execute
        after = before.without_ntfs_ace(target)
        overlay = SimulationOverlay.from_changes(
            [
                NtfsAceChange(
                    kind=ChangeKind.REMOVE_NTFS_ACE,
                    resource_key=before.path,
                    ace_key=before.ntfs_ace_key(target),
                )
            ]
        )

        equivalence, report = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(BOB, before.path)],
        )

        delta = delta_for(report, BOB, before.path)
        assert delta is not None
        assert delta.direction is ImpactDirection.LOST_ACCESS
        assert equivalence.equivalent, equivalence.report()

    async def test_narrowing_an_entry_predicts_the_rights_that_actually_remain(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A modification, which is the change most likely to be got wrong by arithmetic:
        Modify down to Read & Execute is not a subtraction, it is a different mask."""
        before = estate()
        target = before.ntfs_aces[0]  # Finance-RW, Modify
        after = before.with_modified_ntfs_ace(target, access_mask=READ_EXECUTE)
        overlay = SimulationOverlay.from_changes(
            [
                NtfsAceChange(
                    kind=ChangeKind.MODIFY_NTFS_ACE,
                    resource_key=before.path,
                    ace_key=before.ntfs_ace_key(target),
                    access_mask=READ_EXECUTE,
                )
            ]
        )

        equivalence, report = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(ALICE, before.path)],
        )

        delta = delta_for(report, ALICE, before.path)
        assert delta is not None
        assert delta.direction is ImpactDirection.REDUCED
        assert delta.after.rights.value == READ_EXECUTE
        assert equivalence.equivalent, equivalence.report()

    async def test_a_deny_ahead_of_the_allow_predicts_the_revocation_that_happens(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The case a naive simulator gets wrong in the dangerous direction: a Deny added to
        a DACL is not "one more entry", it is evaluated first and it wins."""
        before = estate()
        deny = AceSpec(ALICE, "deny", MODIFY)
        after = before.with_ntfs_ace(deny)
        overlay = SimulationOverlay.from_changes(
            [
                NtfsAceChange(
                    kind=ChangeKind.ADD_NTFS_ACE,
                    resource_key=before.path,
                    trustee_sid=ALICE,
                    ace_type=AceType.DENY,
                    access_mask=MODIFY,
                    ace_flags=deny.ace_flags,
                )
            ]
        )

        equivalence, report = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(ALICE, before.path)],
        )

        delta = delta_for(report, ALICE, before.path)
        assert delta is not None
        assert delta.direction is ImpactDirection.LOST_ACCESS
        assert equivalence.equivalent, equivalence.report()


class TestShareAceChanges:
    async def test_narrowing_the_share_acl_predicts_the_cap_that_actually_applies(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A share ACL is a cap on remote access, and the layer crossing is where an
        administrator's mental model most often fails: the NTFS ACL still says Modify."""
        before = estate()
        target = before.share_aces[0]
        after = before.without_share_ace(target).with_share_ace(
            ShareAceSpec("S-1-1-0", "allow", "read")
        )
        overlay = SimulationOverlay.from_changes(
            [
                ShareAceChange(
                    kind=ChangeKind.MODIFY_SHARE_ACE,
                    share_key=before.share_key,
                    ace_key=before.share_ace_key(target),
                    permission=SharePermission.READ,
                )
            ]
        )

        equivalence, report = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(ALICE, before.path)],
        )

        delta = delta_for(report, ALICE, before.path)
        assert delta is not None
        assert delta.direction is ImpactDirection.REDUCED
        assert equivalence.equivalent, equivalence.report()

    async def test_the_local_path_is_not_capped_by_the_share_acl(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The same change, asked about console access, must change nothing at all.

        A simulation that reported a loss here would be telling an administrator that
        tightening a share ACL protects a directory from somebody sitting at the server.
        """
        before = estate()
        target = before.share_aces[0]
        after = before.without_share_ace(target).with_share_ace(
            ShareAceSpec("S-1-1-0", "allow", "read")
        )
        overlay = SimulationOverlay.from_changes(
            [
                ShareAceChange(
                    kind=ChangeKind.MODIFY_SHARE_ACE,
                    share_key=before.share_key,
                    ace_key=before.share_ace_key(target),
                    permission=SharePermission.READ,
                )
            ]
        )
        await recollect(client, before)
        service = SimulationService(session)

        report = await service.run(
            overlay,
            scope=SimulationScope(
                kind=ScopeKind.PAIR,
                subject_key=ALICE,
                resource_key=before.resource_key,
                path=AccessPath.LOCAL,
            ),
        )

        await recollect(client, after)
        session.expire_all()
        observed = await observe(session, [(ALICE, before.resource_key)], path=AccessPath.LOCAL)
        delta = report.deltas[0]
        assert delta.direction is ImpactDirection.UNCHANGED
        assert delta.after.rights.value == observed[(ALICE, before.resource_key)].as_of


class TestInheritanceChanges:
    async def test_protecting_a_share_root_with_no_parent_changes_nothing_and_says_so(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        r"""``\\FS01\Finance`` is a share root: ADG holds no parent for it, so nothing is
        inherited and protecting it removes nothing.

        Worth measuring rather than reasoning about. A simulation that projected an
        imaginary parent would predict a loss here, and the recollection would not show one.
        """
        before = estate()
        after = before.protected(keep_inherited=True)
        overlay = SimulationOverlay.from_changes(
            [
                InheritanceChange(
                    resource_key=before.path,
                    protected=True,
                    inherited_entries=InheritedAceDisposition.CONVERT_TO_EXPLICIT,
                )
            ]
        )

        equivalence, report = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(ALICE, before.path), (BOB, before.path)],
        )

        assert all(delta.direction is ImpactDirection.UNCHANGED for delta in report.deltas), [
            delta.direction for delta in report.deltas
        ]
        assert equivalence.equivalent, equivalence.report()

    async def test_dropping_the_inherited_entries_is_the_same_here_and_is_measured(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The other answer to the dialog box. On this directory the two coincide, and a
        simulation that treated ``remove`` as "empty the ACL" would predict total loss."""
        before = estate()
        after = before.protected(keep_inherited=False)
        overlay = SimulationOverlay.from_changes(
            [
                InheritanceChange(
                    resource_key=before.path,
                    protected=True,
                    inherited_entries=InheritedAceDisposition.REMOVE,
                )
            ]
        )

        equivalence, _ = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(ALICE, before.path), (BOB, before.path)],
        )

        assert equivalence.equivalent, equivalence.report()


class TestTheKnownExceptionIsMeasured:
    async def test_a_removal_diverges_from_the_live_reading_and_agrees_with_the_as_of_one(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """ADG deletes nothing on ingestion, and current-state reads do not consult presence.

        So after a reconciled scan has proved an entry gone, the live engine still counts it.
        The prediction is right; the *live reading* is the thing that has not caught up. This
        is Phase 7A's limitation 1 and Phase 9A's limitation 6, measured with exact masks
        rather than asserted in a document — and the day somebody routes current-state reads
        through presence, this test tells them the limitation is gone.
        """
        before = estate()
        after = before.without_membership(FINANCE_RW, ALICE)
        overlay = SimulationOverlay.from_changes(
            [
                MembershipChange(
                    kind=ChangeKind.REMOVE_MEMBER, group_key=FINANCE_RW, member_key=ALICE
                )
            ]
        )

        equivalence, _ = await validate(
            client,
            session,
            before_state=before,
            after_state=after,
            overlay=overlay,
            pairs=[(ALICE, before.path)],
        )

        comparison = equivalence.comparisons[0]
        assert comparison.predicted_before == MODIFY
        assert comparison.predicted_after == 0
        assert comparison.observed_after == 0, "the point-in-time reading sees the removal"
        assert comparison.observed_after_live == MODIFY, (
            "the current-state reading does not, because nothing is deleted on ingestion "
            "and it is not routed through presence"
        )
        assert comparison.agrees
        assert not comparison.live_agrees

    async def test_a_proposal_naming_an_entry_a_reconciled_scan_proved_gone_still_applies(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The same limitation, seen from the applicability check rather than the answer.

        Applicability is decided against the baseline, and a *current* baseline reads the
        ACE table directly. So an entry a reconciled scan has proved absent is still there
        to be matched, and the proposal is reported as ``applied`` rather than
        ``target_not_found`` -- with an impact list computed from a grant that no longer
        exists. Measured here rather than described, because it is the one case where the
        report is confidently wrong rather than merely bounded.

        **The workaround exists and is one field**: an ``as_of`` baseline is routed through
        presence, so a simulation against the recollection instant answers correctly. That
        is asserted below, so the fix is documented by a passing test rather than by prose.
        """
        before = estate()
        target = before.ntfs_aces[1]  # Finance-RO, Read & Execute
        await recollect(client, before)
        await recollect(client, before.without_ntfs_ace(target))
        session.expire_all()
        service = SimulationService(session)
        overlay = SimulationOverlay.from_changes(
            [
                NtfsAceChange(
                    kind=ChangeKind.REMOVE_NTFS_ACE,
                    resource_key=before.path,
                    ace_key=before.ntfs_ace_key(target),
                )
            ]
        )
        scope = SimulationScope(
            kind=ScopeKind.PAIR, subject_key=BOB, resource_key=before.resource_key
        )

        live = await service.run(overlay, scope=scope)
        as_of = await service.run(
            overlay, scope=scope, baseline=await service.baseline(at=dt.datetime.now(dt.UTC))
        )

        assert live.applications[0].outcome is ChangeOutcome.APPLIED
        assert live.deltas[0].direction is ImpactDirection.LOST_ACCESS
        assert as_of.applications[0].outcome is ChangeOutcome.TARGET_NOT_FOUND
        assert as_of.inert is True


class TestTheHarnessItself:
    async def test_recollecting_an_unchanged_estate_changes_no_answer(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The control. If a second scan of the same state moved an answer, every result in
        this suite would be measuring the harness rather than the simulation."""
        state = estate()
        await recollect(client, state)
        pairs = [(ALICE, state.resource_key), (BOB, state.resource_key)]
        first = await observe(session, pairs)

        await recollect(client, state)
        session.expire_all()
        second = await observe(session, pairs)

        for pair in pairs:
            assert second[pair].as_of == first[pair].as_of
            assert second[pair].live == first[pair].live

    async def test_the_two_readings_agree_while_nothing_has_been_removed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The divergence in :class:`TestTheKnownExceptionIsMeasured` is about *removal*,
        not about the two engines disagreeing in general."""
        state = estate()
        await recollect(client, state)

        observed = await observe(session, [(ALICE, state.resource_key)])

        reading = observed[(ALICE, state.resource_key)]
        assert reading.as_of == reading.live == MODIFY

    async def test_a_proposal_naming_an_entry_that_never_existed_says_target_not_found(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The regression guard for the discrepancy that is *not* a defect.

        A proposal whose target is not in the baseline must report ``target_not_found``
        rather than producing an empty impact list that reads as "this change is safe".
        """
        before = estate()
        await recollect(client, before)

        report = await SimulationService(session).run(
            SimulationOverlay.from_changes(
                [
                    NtfsAceChange(
                        kind=ChangeKind.REMOVE_NTFS_ACE,
                        resource_key=before.path,
                        ace_key=f"{before.resource_key}|{CAROL}|allow|0x001200a9|0x03",
                    )
                ]
            ),
            scope=SimulationScope(
                kind=ScopeKind.PAIR, subject_key=BOB, resource_key=before.resource_key
            ),
        )

        assert report.inert is True
        assert report.applications[0].outcome is ChangeOutcome.TARGET_NOT_FOUND
