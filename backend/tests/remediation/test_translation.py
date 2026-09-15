"""The plan is measured by the simulation engine, and the map to it is total.

The property that matters is not that each translation is right in isolation; it is that
**every** change kind has one. A kind with no translation is accepted, stored, approved and
exported having never been simulated — an instruction wearing the paperwork of one that had
been measured. So the first test iterates the enum rather than a list somebody maintains.
"""

from __future__ import annotations

import pytest

from app.domain import SharePermission
from app.domain.remediation import PlannedChangeKind
from app.remediation.translate import changes_for, overlay_for, translated_kinds
from app.simulation.overlay import ChangeKind, MembershipChange, NtfsAceChange, ShareAceChange
from tests.remediation import factories as f


class TestEveryKindTranslates:
    @pytest.mark.parametrize("kind", list(PlannedChangeKind), ids=lambda kind: kind.value)
    def test_it_produces_at_least_one_simulated_change(self, kind: PlannedChangeKind) -> None:
        """Iterated over the enum, so a kind added later fails here rather than shipping
        unmeasurable."""
        produced = changes_for(f.change(kind))

        assert produced

    @pytest.mark.parametrize("kind", list(PlannedChangeKind), ids=lambda kind: kind.value)
    def test_the_overlay_accepts_what_it_produces(self, kind: PlannedChangeKind) -> None:
        """A translation the overlay's own constructors refuse would raise at simulation time,
        which is after the plan has been written and shown to somebody."""
        overlay = overlay_for(f.plan(f.change(kind)))

        assert len(overlay) >= 1


class TestAPlanCanOnlyEverTakeAccessAway:
    def test_the_only_additive_change_it_can_produce_is_a_membership(self) -> None:
        """The structural half of the narrowing rule.

        ``validate_narrowing`` refuses a widening mask on one change. This says the same
        thing about the *set* of simulated changes a plan can produce: removals, narrowings,
        and exactly one addition — the group a ``replace_with_group`` puts somebody into,
        which exists to give back access the same step takes away.
        """
        every_kind = [f.change(kind, index=index) for index, kind in enumerate(PlannedChangeKind)]

        produced = translated_kinds(every_kind)
        additive = {kind for kind in produced if not kind.removes}

        assert additive == {ChangeKind.ADD_MEMBER}

    def test_no_plan_can_add_an_access_control_entry(self) -> None:
        every_kind = [f.change(kind, index=index) for index, kind in enumerate(PlannedChangeKind)]

        produced = translated_kinds(every_kind)

        assert ChangeKind.ADD_NTFS_ACE not in produced
        assert ChangeKind.ADD_SHARE_ACE not in produced

    def test_no_plan_can_set_inheritance(self) -> None:
        """Protecting a folder converts or drops every inherited entry at once. It is a
        legitimate change and a far larger one than any step here describes, so a plan cannot
        express it -- and a simulation of one would be measuring something the plan did not
        say."""
        every_kind = [f.change(kind, index=index) for index, kind in enumerate(PlannedChangeKind)]

        assert ChangeKind.SET_INHERITANCE not in translated_kinds(every_kind)


class TestWhatEachKindBecomes:
    def test_removing_an_ntfs_entry_names_the_entry_rather_than_the_trustee(self) -> None:
        (simulated,) = changes_for(f.change(PlannedChangeKind.REMOVE_NTFS_ACE))

        assert isinstance(simulated, NtfsAceChange)
        assert simulated.kind is ChangeKind.REMOVE_NTFS_ACE
        assert simulated.ace_key == "ace-1"

    def test_narrowing_an_ntfs_entry_carries_the_resulting_mask(self) -> None:
        (simulated,) = changes_for(
            f.change(
                PlannedChangeKind.MODIFY_NTFS_ACE,
                entry=f.entry(access_mask=f.FULL_MASK),
                after_access_mask=f.READ_MASK,
            )
        )

        assert isinstance(simulated, NtfsAceChange)
        assert simulated.kind is ChangeKind.MODIFY_NTFS_ACE
        assert simulated.access_mask == f.READ_MASK

    def test_a_share_modification_written_as_a_level_stays_a_level(self) -> None:
        """A share ACE carries a mask or a level, never both. Converting one to the other
        would invent a precision the collector did not report."""
        (simulated,) = changes_for(
            f.change(
                PlannedChangeKind.MODIFY_SHARE_ACE,
                entry=f.share_entry(permission=SharePermission.FULL),
                after_permission=SharePermission.READ,
            )
        )

        assert isinstance(simulated, ShareAceChange)
        assert simulated.permission is SharePermission.READ
        assert simulated.access_mask is None

    def test_removing_a_membership_keeps_the_edge_kind(self) -> None:
        """A local-group edge matched as a directory edge matches nothing, and the simulation
        would then report that removing it changes nothing."""
        from app.domain import MembershipEdgeKind

        (simulated,) = changes_for(
            f.change(
                PlannedChangeKind.REMOVE_GROUP_MEMBER,
                target_key=f.BUILTIN_ADMINS,
                membership=f.edge(
                    group_key=f.BUILTIN_ADMINS,
                    edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                ),
            )
        )

        assert isinstance(simulated, MembershipChange)
        assert simulated.edge_kind is MembershipEdgeKind.LOCAL_GROUP_MEMBER
        assert simulated.host_key == "fs01"


class TestReplacingADirectPermissionIsMeasuredAsOneThing:
    def test_it_produces_both_halves(self) -> None:
        produced = changes_for(f.change(PlannedChangeKind.REPLACE_WITH_GROUP))

        assert [item.kind for item in produced] == [
            ChangeKind.REMOVE_NTFS_ACE,
            ChangeKind.ADD_MEMBER,
        ]

    def test_both_halves_reach_one_overlay(self) -> None:
        """Measuring them apart answers the wrong question twice: the removal alone reports a
        loss the plan never intends, and the addition alone reports a gain nobody is being
        given. Together they answer the question the plan actually asks -- does this principal
        end up where they started?"""
        overlay = overlay_for(f.plan(f.change(PlannedChangeKind.REPLACE_WITH_GROUP)))

        assert len(overlay) == 2
        assert overlay.has_removals


class TestTheOverlayIsBuiltInStepOrder:
    def test_changes_are_ordered_by_step_number_not_by_arrival(self) -> None:
        first = f.change(index=0, entry=f.entry(ace_key="a"))
        second = f.change(index=1, entry=f.entry(ace_key="b"))
        plan = f.plan(second, first)

        keys = [item.ace_key for item in overlay_for(plan) if isinstance(item, NtfsAceChange)]

        assert keys == ["a", "b"]
