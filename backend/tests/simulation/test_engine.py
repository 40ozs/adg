r"""The whole engine, over an estate in memory: what a proposal is found to do.

Every answer here is produced by :class:`app.services.AccessService` — the same class that
answers live questions — run twice over the same repositories, once wrapped in an overlay.
Nothing in these tests reaches a simplified calculator, because there is not one.

The estate is the one in ``tests/support/simulation.py``:

* ``\\fs01\finance`` grants ``Finance-RW`` Modify and ``Domain Users`` Read/Execute;
* ``alice`` is in both (``Finance-Team`` → ``Finance-RW``, and ``Domain Users``);
* ``bob`` is in ``Finance-RW`` only;
* ``carol`` is in ``Domain Users`` only;
* ``dave`` is in nothing.

Everything is asked over :attr:`AccessPath.LOCAL` unless a test is about the share layer.
Local access does not pass through a share ACL, so every difference between two answers comes
from NTFS and from membership — which keeps a failure message about the thing under test
rather than about a share ACE nobody meant to involve.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.access_engine import AccessPath
from app.domain import AceType
from app.services.access import AccessService
from app.simulation import (
    AccessDelta,
    ChangeKind,
    ChangeOutcome,
    ImpactDirection,
    MembershipChange,
    NtfsAceChange,
    ScopeKind,
    ShareAceChange,
    SimulationBounds,
    SimulationCaveat,
    SimulationOverlay,
    SimulationReport,
    SimulationScope,
    SimulationService,
    SimulationTruncation,
    simulated_repositories,
)
from tests.support.simulation import (
    ALICE,
    BOB,
    CAROL,
    DAVE,
    DOMAIN_USERS,
    FINANCE,
    FINANCE_RW,
    FINANCE_TEAM,
    MODIFY,
    READ_EXECUTE,
    REPORTS,
    SHARE_KEY,
    finance_estate,
    fixed_baseline,
)

LOCAL = SimulationScope(kind=ScopeKind.AFFECTED, path=AccessPath.LOCAL)


def pair(subject: str, resource: str = FINANCE) -> SimulationScope:
    return SimulationScope(
        kind=ScopeKind.PAIR, subject_key=subject, resource_key=resource, path=AccessPath.LOCAL
    )


def remove_alice_from_finance_team() -> SimulationOverlay:
    return SimulationOverlay(
        membership=(
            MembershipChange(
                kind=ChangeKind.REMOVE_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE
            ),
        )
    )


def remove_bob_from_finance_rw() -> SimulationOverlay:
    return SimulationOverlay(
        membership=(
            MembershipChange(kind=ChangeKind.REMOVE_MEMBER, group_key=FINANCE_RW, member_key=BOB),
        )
    )


async def run(
    overlay: SimulationOverlay, scope: SimulationScope = LOCAL, **kwargs: Any
) -> SimulationReport:
    resources, membership = finance_estate()
    service = SimulationService(session=None)  # type: ignore[arg-type]
    return await service.run_against(
        overlay,
        resources,
        membership,
        baseline=fixed_baseline(),
        scope=scope,
        **kwargs,
    )


def delta_for(report: SimulationReport, subject: str, resource: str = FINANCE) -> AccessDelta:
    """One delta out of a report.

    The resource is named because the affected scope legitimately returns several: a
    membership change reaches every directory the group is on, and a helper that took the
    first match would silently assert about whichever one sorted highest by severity.
    """
    return next(
        delta
        for delta in report.deltas
        if delta.subject_key == subject and delta.resource_key == resource.casefold()
    )


class TestTheBaselineIsWhatTheEngineSaysItIs:
    async def test_alice_holds_modify_and_bob_holds_modify_and_carol_read(self):
        """Pinned so that every later assertion about a *change* has a known starting point."""
        resources, membership = finance_estate()
        service = AccessService(resources, membership)

        answers = {
            key: (await service.effective_access(key, FINANCE, path=AccessPath.LOCAL)).access
            for key in (ALICE, BOB, CAROL, DAVE)
        }

        assert answers[ALICE].rights.value == MODIFY
        assert answers[BOB].rights.value == MODIFY
        assert answers[CAROL].rights.value == READ_EXECUTE
        assert not answers[DAVE].has_access


class TestRemovingAMembership:
    async def test_bob_loses_access_outright(self):
        report = await run(remove_bob_from_finance_rw())

        assert delta_for(report, BOB).direction is ImpactDirection.LOST_ACCESS
        assert BOB in report.summary.principals_losing

    async def test_alice_keeps_read_through_domain_users(self):
        """The finding this whole phase exists to produce.

        Taking Alice out of ``Finance-Team`` looks, on the ACL, like removing her access. It
        removes her Modify and leaves her Read/Execute, because ``Domain Users`` is on the same
        DACL — and an administrator told "this revokes Alice's access" would sign off a change
        that does not.
        """
        report = await run(remove_alice_from_finance_team())
        delta = delta_for(report, ALICE)

        assert delta.direction is ImpactDirection.REDUCED
        assert delta.after.rights.value == READ_EXECUTE
        assert delta.rights_removed.value
        assert delta.after.has_access

    async def test_the_surviving_route_is_named_rather_than_merely_implied(self):
        report = await run(remove_alice_from_finance_team())
        delta = delta_for(report, ALICE)

        assert delta.alternate_path_retained
        assert SimulationCaveat.ALTERNATE_PATH_RETAINS_ACCESS in delta.caveats
        assert any(DOMAIN_USERS in path.chain for path in delta.retained_paths), [
            path.chain for path in delta.retained_paths
        ]

    async def test_a_principal_who_loses_everything_has_no_surviving_route(self):
        report = await run(remove_bob_from_finance_rw())

        assert not delta_for(report, BOB).alternate_path_retained

    async def test_carol_is_untouched_and_says_so(self):
        report = await run(remove_alice_from_finance_team())

        assert delta_for(report, CAROL).direction is ImpactDirection.UNCHANGED


class TestAddingAMembership:
    async def test_a_principal_who_could_reach_nothing_gains_access(self):
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=DAVE
                ),
            )
        )

        report = await run(overlay)
        delta = delta_for(report, DAVE)

        assert delta.direction is ImpactDirection.GAINED_ACCESS
        assert delta.after.rights.value == MODIFY
        assert DAVE in report.summary.principals_gaining

    async def test_a_principal_who_already_had_read_has_it_expanded(self):
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=CAROL
                ),
            )
        )

        report = await run(overlay)

        assert delta_for(report, CAROL).direction is ImpactDirection.EXPANDED

    async def test_the_affected_resources_come_from_the_reference_index(self):
        r"""A membership change names no resource, and changes access to several.

        ``Finance-RW`` is on the DACL of ``\\fs01\finance`` and, by inheritance, of
        ``\\fs01\finance\reports``; adding somebody to a group that reaches it changes their
        access to both, and neither is written anywhere in the proposal.
        """
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=DAVE
                ),
            )
        )

        report = await run(overlay)

        assert {delta.resource_key for delta in report.deltas} == {
            FINANCE.casefold(),
            REPORTS.casefold(),
        }


class TestAceChanges:
    async def test_removing_the_grant_takes_it_from_everybody_it_reached(self):
        resources, _ = finance_estate()
        entries = await resources.full_ntfs_acl(FINANCE.casefold())
        target = next(entry for entry in entries if entry.trustee_sid == FINANCE_RW)
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.REMOVE_NTFS_ACE,
                    resource_key=FINANCE,
                    ace_key=target.ace_key,
                ),
            )
        )

        report = await run(overlay, pair(BOB))

        assert delta_for(report, BOB).direction is ImpactDirection.LOST_ACCESS

    async def test_adding_a_deny_ahead_of_the_allow_revokes_access(self):
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.ADD_NTFS_ACE,
                    resource_key=FINANCE,
                    trustee_sid=BOB,
                    ace_type=AceType.DENY,
                    access_mask=MODIFY,
                ),
            )
        )

        report = await run(overlay, pair(BOB))

        assert delta_for(report, BOB).direction is ImpactDirection.LOST_ACCESS

    async def test_adding_a_grant_puts_the_resource_into_the_subject_s_candidate_list(self):
        """Without the candidate injection, a what-if would report no change at all.

        The reference index only knows the trustees of *collected* ACLs, so a directory a
        proposal newly names would never be a candidate and the subject's answer would be
        computed for a page that does not contain it.
        """
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.ADD_NTFS_ACE,
                    resource_key=REPORTS,
                    trustee_sid=DAVE,
                    ace_type=AceType.ALLOW,
                    access_mask=READ_EXECUTE,
                ),
            )
        )
        resources, membership = finance_estate()
        overlaid_resources, _ = simulated_repositories(resources, membership, overlay)

        before = await resources.resources_named_by([DAVE])
        after = await overlaid_resources.resources_named_by([DAVE])

        assert before.items == ()
        assert after.items == (REPORTS.casefold(),)

    async def test_a_subject_scope_reports_the_newly_reachable_directory(self):
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.ADD_NTFS_ACE,
                    resource_key=REPORTS,
                    trustee_sid=DAVE,
                    ace_type=AceType.ALLOW,
                    access_mask=READ_EXECUTE,
                ),
            )
        )

        report = await run(
            overlay,
            SimulationScope(kind=ScopeKind.SUBJECT, subject_key=DAVE, path=AccessPath.LOCAL),
        )

        assert [delta.resource_key for delta in report.deltas] == [REPORTS.casefold()]
        assert report.deltas[0].direction is ImpactDirection.GAINED_ACCESS


class TestApplicability:
    async def test_a_removal_naming_an_entry_that_is_gone_is_reported_as_such(self):
        """ "No impact" and "this change no longer applies" are the same empty impact list."""
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.REMOVE_NTFS_ACE,
                    resource_key=FINANCE,
                    ace_key="ntfs_ace|nothing-like-this",
                ),
            )
        )

        report = await run(overlay, pair(ALICE))

        assert report.applications[0].outcome is ChangeOutcome.TARGET_NOT_FOUND
        assert report.inert
        assert delta_for(report, ALICE).direction is ImpactDirection.UNCHANGED

    async def test_adding_a_membership_that_already_exists_is_reported_as_a_no_op(self):
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE
                ),
            )
        )

        report = await run(overlay, pair(ALICE))

        assert report.applications[0].outcome is ChangeOutcome.ALREADY_PRESENT
        assert report.inert

    async def test_an_ace_change_on_a_directory_nobody_has_read_cannot_be_applied(self):
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.ADD_NTFS_ACE,
                    resource_key="\\\\fs01\\finance\\nowhere",
                    trustee_sid=DAVE,
                    ace_type=AceType.ALLOW,
                    access_mask=READ_EXECUTE,
                ),
            )
        )

        report = await run(overlay, pair(DAVE))

        assert report.applications[0].outcome is ChangeOutcome.TARGET_NOT_OBSERVED

    async def test_a_share_ace_change_on_an_unread_acl_cannot_be_applied(self):
        """Zero stored share entries means nobody has looked.

        Applying one simulated entry would turn "never looked" into "grants exactly this" —
        an invented certainty, in the direction that hides access.
        """
        resources, membership = finance_estate()
        resources.share_acls = {}
        service = SimulationService(session=None)  # type: ignore[arg-type]
        overlay = SimulationOverlay(
            share_aces=(
                ShareAceChange(
                    kind=ChangeKind.ADD_SHARE_ACE,
                    share_key=SHARE_KEY,
                    trustee_sid=DAVE,
                    ace_type=AceType.ALLOW,
                    access_mask=READ_EXECUTE,
                ),
            )
        )

        report = await service.run_against(
            overlay,
            resources,
            membership,
            baseline=fixed_baseline(),
            scope=pair(DAVE),
        )

        assert report.applications[0].outcome is ChangeOutcome.TARGET_NOT_OBSERVED

    async def test_an_unapplicable_change_is_not_handed_to_the_repositories(self):
        """The impact list and the applied-change list have to describe the same world."""
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.REMOVE_NTFS_ACE, resource_key=FINANCE, ace_key="absent"
                ),
            )
        )

        report = await run(overlay, pair(ALICE))

        assert report.applied_changes == ()
        assert delta_for(report, ALICE).after.rights.value == MODIFY


class TestIsolation:
    async def test_the_baseline_rows_are_unchanged_after_a_simulation(self):
        resources, membership = finance_estate()
        before_acls = copy.deepcopy(resources.ntfs_acls)
        before_resources = copy.deepcopy(resources.resources)
        before_edges = copy.deepcopy(membership.edges)
        service = SimulationService(session=None)  # type: ignore[arg-type]

        await service.run_against(
            SimulationOverlay.from_changes(
                [
                    MembershipChange(
                        kind=ChangeKind.REMOVE_MEMBER,
                        group_key=FINANCE_TEAM,
                        member_key=ALICE,
                    ),
                    NtfsAceChange(
                        kind=ChangeKind.ADD_NTFS_ACE,
                        resource_key=FINANCE,
                        trustee_sid=DAVE,
                        ace_type=AceType.ALLOW,
                        access_mask=READ_EXECUTE,
                    ),
                ]
            ),
            resources,
            membership,
            baseline=fixed_baseline(),
            scope=LOCAL,
        )

        assert resources.ntfs_acls == before_acls
        assert resources.resources == before_resources
        assert membership.edges == before_edges

    async def test_the_live_answer_is_identical_before_and_after_simulating(self):
        resources, membership = finance_estate()
        service = AccessService(resources, membership)
        simulation = SimulationService(session=None)  # type: ignore[arg-type]

        before = (await service.effective_access(ALICE, FINANCE, path=AccessPath.LOCAL)).access
        await simulation.run_against(
            remove_alice_from_finance_team(),
            resources,
            membership,
            baseline=fixed_baseline(),
            scope=LOCAL,
        )
        after = (await service.effective_access(ALICE, FINANCE, path=AccessPath.LOCAL)).access

        assert before.rights.value == after.rights.value == MODIFY

    async def test_the_overlay_object_itself_is_not_modified(self):
        overlay = remove_alice_from_finance_team()
        digest = overlay.overlay_hash

        report = await run(overlay)

        assert report.overlay is overlay
        assert overlay.overlay_hash == digest

    async def test_a_simulated_edge_does_not_count_as_having_enumerated_a_group(self):
        """A proposal is not an enumeration.

        Letting one satisfy ``keys_with_members`` would retire a coverage finding — turning
        "nobody has looked inside this group" into a verdict on the strength of a
        hypothetical.
        """
        resources, membership = finance_estate()
        membership._enumerated = set()  # nothing has been enumerated
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=DAVE
                ),
            )
        )
        _, overlaid = simulated_repositories(resources, membership, overlay)

        assert await overlaid.keys_with_members([FINANCE_TEAM]) == frozenset()


class TestDeterminism:
    async def test_two_runs_over_unchanged_data_produce_the_same_document(self):
        """So that a difference between two reports means something actually changed."""
        overlay = remove_alice_from_finance_team()

        one = dict((await run(overlay)).document())
        other = dict((await run(overlay)).document())

        # Elapsed time is the one field that legitimately differs between two identical runs.
        one.pop("cost")
        other.pop("cost")
        assert one == other


class TestBoundsAreReportedNotHidden:
    async def test_exhausting_the_pair_budget_truncates_and_says_so(self):
        report = await run(
            remove_alice_from_finance_team(),
            LOCAL,
            bounds=SimulationBounds(max_pairs=1),
        )

        assert not report.complete
        assert SimulationTruncation.PAIR_BUDGET in report.truncation

    async def test_a_complete_answer_says_it_is_complete(self):
        report = await run(remove_bob_from_finance_rw(), pair(BOB))

        assert report.complete
        assert report.truncation == ()

    async def test_an_inheritable_change_reports_the_subtree_it_did_not_evaluate(self):
        """Everything under a directory inherits from it, and this phase stops at the
        directories the overlay names. The gap is stated rather than left to be discovered."""
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.ADD_NTFS_ACE,
                    resource_key=FINANCE,
                    trustee_sid=DAVE,
                    ace_type=AceType.ALLOW,
                    access_mask=READ_EXECUTE,
                    ace_flags=3,
                ),
            )
        )

        report = await run(overlay)

        assert SimulationTruncation.DESCENDANTS_NOT_EVALUATED in report.truncation

    async def test_a_bound_beyond_its_ceiling_is_refused(self):
        from app.simulation import MAX_PAIRS_CEILING, SimulationBoundsError

        with pytest.raises(SimulationBoundsError, match="capped at"):
            SimulationBounds(max_pairs=MAX_PAIRS_CEILING + 1)


class TestTheBaselineIsNamed:
    async def test_every_report_carries_the_collection_state_it_was_computed_against(self):
        report = await run(remove_alice_from_finance_team())

        assert report.baseline.token == fixed_baseline().token
        assert report.baseline.run_id is not None
        baseline_document = report.document()["baseline"]
        assert isinstance(baseline_document, dict)
        assert baseline_document["token"] == report.baseline.token

    async def test_a_baseline_knows_when_collection_has_moved_on(self):
        baseline = fixed_baseline(observations=12)
        moved = fixed_baseline(observations=13).basis

        assert not baseline.is_stale_against(baseline.basis)
        assert baseline.is_stale_against(moved)
