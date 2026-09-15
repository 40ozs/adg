"""Applying an overlay to stored rows: what comes out, and what is left untouched.

Pure functions over frozen records, so everything here runs without a database and without
the access engine. The two properties worth stating up front are asserted first: the inputs
are not mutated, and every record that comes out of a change is marked as invented.
"""

from __future__ import annotations

import copy
from typing import Any

from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    Direction,
    SharePermission,
    parse_unc_path,
    project_inherited_acl,
)
from app.repositories.resources import NtfsAceRecord
from app.simulation import (
    SIMULATED_AT,
    SIMULATED_RUN_ID,
    SIMULATED_SOURCE_KEY,
    ChangeKind,
    InheritanceChange,
    InheritedAceDisposition,
    MembershipChange,
    NtfsAceChange,
    ShareAceChange,
    SimulationOverlay,
    is_simulated_ace_key,
    is_simulated_edge_key,
    is_simulated_record,
    overlay_edges,
    overlay_ntfs_acl,
    overlay_resource,
    overlay_share_acl,
)
from tests.support.simulation import (
    ALICE,
    BOB,
    CAROL,
    DOMAIN_USERS,
    FINANCE,
    FINANCE_RW,
    FINANCE_TEAM,
    FULL_CONTROL,
    MODIFY,
    READ_EXECUTE,
    REPORTS,
    SHARE_KEY,
    edge,
    ntfs_ace,
    resource,
    share_ace,
)

FINANCE_KEY = parse_unc_path(FINANCE).comparison_key
REPORTS_KEY = parse_unc_path(REPORTS).comparison_key


def finance_acl() -> list[NtfsAceRecord]:
    return [
        ntfs_ace(FINANCE, FINANCE_RW, MODIFY, order_index=0),
        ntfs_ace(FINANCE, DOMAIN_USERS, READ_EXECUTE, order_index=1),
    ]


def add_ace(**overrides: Any) -> SimulationOverlay:
    fields: dict[str, Any] = {
        "kind": ChangeKind.ADD_NTFS_ACE,
        "resource_key": FINANCE,
        "trustee_sid": CAROL,
        "ace_type": AceType.ALLOW,
        "access_mask": READ_EXECUTE,
    }
    fields.update(overrides)
    return SimulationOverlay(ntfs_aces=(NtfsAceChange(**fields),))


class TestNothingIsMutated:
    def test_the_baseline_entries_are_not_touched(self):
        entries = finance_acl()
        before = copy.deepcopy(entries)

        overlay_ntfs_acl(add_ace(), FINANCE_KEY, entries)

        assert entries == before

    def test_an_untouched_acl_comes_back_as_the_same_records(self):
        """No copying, no renumbering, no work: the overlay names another directory."""
        entries = finance_acl()

        applied = overlay_ntfs_acl(add_ace(resource_key=REPORTS), FINANCE_KEY, entries)

        assert list(applied) == entries
        assert all(one is other for one, other in zip(applied, entries, strict=True))

    def test_the_baseline_adjacency_is_not_touched(self):
        edges = [edge(FINANCE_TEAM, ALICE)]
        baseline = {FINANCE_TEAM: list(edges)}
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.REMOVE_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE
                ),
            )
        )

        result = overlay_edges(overlay, Direction.DOWN, [FINANCE_TEAM], baseline)

        assert baseline[FINANCE_TEAM] == edges
        assert result[FINANCE_TEAM] == []


class TestEveryInventedRowIsMarked:
    def test_an_added_entry_carries_the_simulated_provenance(self):
        applied = overlay_ntfs_acl(add_ace(), FINANCE_KEY, finance_acl())
        added = next(entry for entry in applied if entry.trustee_sid == CAROL)

        assert is_simulated_record(added)
        assert added.source_key == SIMULATED_SOURCE_KEY
        assert added.first_observed_run_id == SIMULATED_RUN_ID
        assert added.last_observed_at == SIMULATED_AT
        assert is_simulated_ace_key(added.ace_key)

    def test_an_observed_entry_that_was_not_edited_keeps_its_own_provenance(self):
        applied = overlay_ntfs_acl(add_ace(), FINANCE_KEY, finance_acl())
        untouched = next(entry for entry in applied if entry.trustee_sid == FINANCE_RW)

        assert not is_simulated_record(untouched)

    def test_an_added_edge_is_visibly_hypothetical(self):
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=BOB
                ),
            )
        )

        result = overlay_edges(overlay, Direction.DOWN, [FINANCE_TEAM], {FINANCE_TEAM: []})

        assert is_simulated_edge_key(result[FINANCE_TEAM][0].edge_key)


class TestMembership:
    def test_a_removal_matches_the_pair_rather_than_the_stored_key(self):
        """A proposal says "take Alice out of Finance-Team", not "delete row 4718"."""
        stored = edge(FINANCE_TEAM, ALICE)
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.REMOVE_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE
                ),
            )
        )

        result = overlay_edges(overlay, Direction.DOWN, [FINANCE_TEAM], {FINANCE_TEAM: [stored]})

        assert result[FINANCE_TEAM] == []

    def test_an_addition_appears_walking_upward_from_the_member(self):
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=CAROL
                ),
            )
        )

        result = overlay_edges(overlay, Direction.UP, [CAROL], {CAROL: []})

        assert [item.group_key for item in result[CAROL]] == [FINANCE_TEAM]

    def test_adding_a_membership_that_already_exists_does_not_duplicate_it(self):
        """A second edge would double every chain an explanation enumerates."""
        stored = edge(FINANCE_TEAM, ALICE)
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE
                ),
            )
        )

        result = overlay_edges(overlay, Direction.DOWN, [FINANCE_TEAM], {FINANCE_TEAM: [stored]})

        assert result[FINANCE_TEAM] == [stored]

    def test_a_change_to_another_group_leaves_this_adjacency_alone(self):
        overlay = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_RW, member_key=CAROL
                ),
            )
        )

        result = overlay_edges(overlay, Direction.DOWN, [FINANCE_TEAM], {FINANCE_TEAM: []})

        assert result[FINANCE_TEAM] == []


class TestNtfsEntries:
    def test_a_removal_drops_the_entry_it_names(self):
        entries = finance_acl()
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.REMOVE_NTFS_ACE,
                    resource_key=FINANCE,
                    ace_key=entries[0].ace_key,
                ),
            )
        )

        applied = overlay_ntfs_acl(overlay, FINANCE_KEY, entries)

        assert [entry.trustee_sid for entry in applied] == [DOMAIN_USERS]

    def test_a_removal_naming_an_entry_that_is_not_there_changes_nothing(self):
        entries = finance_acl()
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.REMOVE_NTFS_ACE, resource_key=FINANCE, ace_key="gone"
                ),
            )
        )

        applied = overlay_ntfs_acl(overlay, FINANCE_KEY, entries)

        assert [entry.trustee_sid for entry in applied] == [FINANCE_RW, DOMAIN_USERS]

    def test_a_modification_keeps_the_position_and_rebuilds_the_key(self):
        """An NTFS ACE's identity *is* its trustee, type, mask and flags.

        Reusing the old key would make two entries with different rights compare equal.
        """
        entries = finance_acl()
        original = entries[0].ace_key
        overlay = SimulationOverlay(
            ntfs_aces=(
                NtfsAceChange(
                    kind=ChangeKind.MODIFY_NTFS_ACE,
                    resource_key=FINANCE,
                    ace_key=original,
                    access_mask=READ_EXECUTE,
                ),
            )
        )

        applied = overlay_ntfs_acl(overlay, FINANCE_KEY, entries)

        assert applied[0].trustee_sid == FINANCE_RW
        assert applied[0].access_mask == READ_EXECUTE
        assert applied[0].order_index == 0
        assert applied[0].ace_key != original

    def test_an_addition_that_duplicates_an_entry_is_dropped(self):
        entries = finance_acl()
        overlay = add_ace(trustee_sid=DOMAIN_USERS, access_mask=READ_EXECUTE)

        applied = overlay_ntfs_acl(overlay, FINANCE_KEY, entries)

        assert len(applied) == 2

    def test_a_deny_is_placed_ahead_of_every_allow_by_default(self):
        """Where the Windows ACL editor puts one, and the only placement that denies."""
        applied = overlay_ntfs_acl(
            add_ace(ace_type=AceType.DENY, access_mask=MODIFY), FINANCE_KEY, finance_acl()
        )

        assert applied[0].trustee_sid == CAROL
        assert applied[0].ace_type is AceType.DENY

    def test_an_allow_is_placed_after_the_explicit_entries(self):
        applied = overlay_ntfs_acl(add_ace(), FINANCE_KEY, finance_acl())

        assert [entry.trustee_sid for entry in applied] == [FINANCE_RW, DOMAIN_USERS, CAROL]

    def test_an_explicit_position_is_honoured_even_when_it_is_not_canonical(self):
        """Simulating a non-canonical DACL is how an operator learns it behaves oddly."""
        applied = overlay_ntfs_acl(
            add_ace(ace_type=AceType.DENY, access_mask=MODIFY, order_index=2),
            FINANCE_KEY,
            finance_acl(),
        )

        assert [entry.trustee_sid for entry in applied] == [FINANCE_RW, DOMAIN_USERS, CAROL]

    def test_positions_are_renumbered_contiguously(self):
        """Two entries claiming one position is an ambiguity the ACL hash refuses."""
        applied = overlay_ntfs_acl(
            add_ace(ace_type=AceType.DENY, access_mask=MODIFY), FINANCE_KEY, finance_acl()
        )

        assert [entry.order_index for entry in applied] == [0, 1, 2]

    def test_an_unordered_baseline_is_left_unordered(self):
        """Inventing positions would claim an evaluation order nobody observed."""
        entries = [ntfs_ace(FINANCE, FINANCE_RW, MODIFY, order_index=0)]
        object.__setattr__(entries[0], "order_index", None)

        applied = overlay_ntfs_acl(add_ace(), FINANCE_KEY, entries)

        assert [entry.order_index for entry in applied] == [None, None]


class TestInheritance:
    def child_acl(self) -> list[NtfsAceRecord]:
        return [
            ntfs_ace(
                REPORTS,
                FINANCE_RW,
                MODIFY,
                flags=int(AceFlag.CONTAINER_INHERIT | AceFlag.INHERITED),
                source=AceSource.INHERITED,
                inherited_from=FINANCE_KEY,
                order_index=0,
            ),
            ntfs_ace(REPORTS, CAROL, READ_EXECUTE, order_index=1),
        ]

    def test_protecting_and_converting_keeps_the_entries_as_explicit_ones(self):
        overlay = SimulationOverlay(
            inheritance=(
                InheritanceChange(
                    resource_key=REPORTS,
                    protected=True,
                    inherited_entries=InheritedAceDisposition.CONVERT_TO_EXPLICIT,
                ),
            )
        )

        applied = overlay_ntfs_acl(overlay, REPORTS_KEY, self.child_acl())

        assert len(applied) == 2
        assert all(entry.source is AceSource.EXPLICIT for entry in applied)
        assert not applied[0].flags & AceFlag.INHERITED

    def test_protecting_and_removing_leaves_only_what_was_already_explicit(self):
        """Usually a far smaller ACL than anybody expects."""
        overlay = SimulationOverlay(
            inheritance=(
                InheritanceChange(
                    resource_key=REPORTS,
                    protected=True,
                    inherited_entries=InheritedAceDisposition.REMOVE,
                ),
            )
        )

        applied = overlay_ntfs_acl(overlay, REPORTS_KEY, self.child_acl())

        assert [entry.trustee_sid for entry in applied] == [CAROL]

    def test_clearing_protection_projects_the_parent_s_inheritable_entries_back(self):
        parent = [
            ntfs_ace(
                FINANCE,
                FINANCE_RW,
                MODIFY,
                flags=int(AceFlag.CONTAINER_INHERIT | AceFlag.OBJECT_INHERIT),
                order_index=0,
            )
        ]
        overlay = SimulationOverlay(
            inheritance=(InheritanceChange(resource_key=REPORTS, protected=False),)
        )

        applied = overlay_ntfs_acl(
            overlay,
            REPORTS_KEY,
            [ntfs_ace(REPORTS, CAROL, READ_EXECUTE, order_index=0)],
            parent_entries=parent,
        )

        assert [entry.trustee_sid for entry in applied] == [CAROL, FINANCE_RW]
        assert applied[1].source is AceSource.INHERITED
        assert applied[1].inherited_from == FINANCE_KEY

    def test_clearing_protection_with_no_parent_read_projects_nothing(self):
        """An unread parent is not a parent that grants nothing.

        The flag flips and the ACL is left as a lower bound; the service reports the gap as
        ``PARENT_NOT_OBSERVED``.
        """
        overlay = SimulationOverlay(
            inheritance=(InheritanceChange(resource_key=REPORTS, protected=False),)
        )
        entries = [ntfs_ace(REPORTS, CAROL, READ_EXECUTE, order_index=0)]

        applied = overlay_ntfs_acl(overlay, REPORTS_KEY, entries, parent_entries=None)

        assert [entry.trustee_sid for entry in applied] == [CAROL]


class TestTheResourceRow:
    def test_the_declared_entry_count_moves_with_the_entries(self):
        """Otherwise every simulated resolution reports ``ACE_COUNT_MISMATCH``.

        The access check compares the count the descriptor declared with the entries it was
        handed, and a mismatch would fire on every answer — burying the finding the
        simulation was run to produce.
        """
        entries = finance_acl()
        row = resource(FINANCE, entries)
        overlay = add_ace()

        applied = overlay_ntfs_acl(overlay, FINANCE_KEY, entries)
        updated = overlay_resource(overlay, row, applied)

        assert updated.ace_count == 3
        assert row.ace_count == 2

    def test_the_digest_is_recomputed_so_it_describes_the_simulated_acl(self):
        entries = finance_acl()
        row = resource(FINANCE, entries)

        applied = overlay_ntfs_acl(add_ace(), FINANCE_KEY, entries)
        updated = overlay_resource(add_ace(), row, applied)

        assert updated.acl_hash != row.acl_hash

    def test_protecting_makes_the_directory_a_boundary(self):
        overlay = SimulationOverlay(
            inheritance=(
                InheritanceChange(
                    resource_key=REPORTS,
                    protected=True,
                    inherited_entries=InheritedAceDisposition.REMOVE,
                ),
            )
        )
        row = resource(REPORTS, [])

        updated = overlay_resource(overlay, row, ())

        assert updated.dacl_protected
        assert updated.is_acl_boundary
        assert not updated.inheritance_enabled

    def test_a_row_the_overlay_does_not_touch_comes_back_unchanged(self):
        row = resource(FINANCE, finance_acl())

        assert overlay_resource(add_ace(resource_key=REPORTS), row, ()) is row


class TestShareEntries:
    def test_an_entry_is_added_to_an_acl_somebody_has_read(self):
        baseline = [share_ace(DOMAIN_USERS, FULL_CONTROL, order_index=0)]
        overlay = SimulationOverlay(
            share_aces=(
                ShareAceChange(
                    kind=ChangeKind.ADD_SHARE_ACE,
                    share_key=SHARE_KEY,
                    trustee_sid=CAROL,
                    ace_type=AceType.ALLOW,
                    permission=SharePermission.READ,
                ),
            )
        )

        applied = overlay_share_acl(overlay, SHARE_KEY, baseline)

        assert [entry.trustee_sid for entry in applied] == [DOMAIN_USERS, CAROL]
        assert applied[1].permission is SharePermission.READ

    def test_an_unread_share_acl_is_left_alone(self):
        """Zero stored entries means nobody has looked, not that it grants nothing.

        Adding one simulated entry would turn "never looked" into "grants exactly this",
        which is an invented certainty in the direction that hides access.
        """
        overlay = SimulationOverlay(
            share_aces=(
                ShareAceChange(
                    kind=ChangeKind.ADD_SHARE_ACE,
                    share_key=SHARE_KEY,
                    trustee_sid=CAROL,
                    ace_type=AceType.ALLOW,
                    permission=SharePermission.READ,
                ),
            )
        )

        assert overlay_share_acl(overlay, SHARE_KEY, []) == ()

    def test_setting_a_mask_clears_the_permission_level_it_replaces(self):
        """A share ACE carries one right form or the other, never both."""
        baseline = [share_ace(DOMAIN_USERS, FULL_CONTROL, order_index=0)]
        overlay = SimulationOverlay(
            share_aces=(
                ShareAceChange(
                    kind=ChangeKind.MODIFY_SHARE_ACE,
                    share_key=SHARE_KEY,
                    ace_key=baseline[0].ace_key,
                    permission=SharePermission.READ,
                ),
            )
        )

        applied = overlay_share_acl(overlay, SHARE_KEY, baseline)

        assert applied[0].permission is SharePermission.READ
        assert applied[0].access_mask is None
        assert applied[0].right_token == SharePermission.READ.value

    def test_a_removal_drops_the_entry(self):
        baseline = [share_ace(DOMAIN_USERS, FULL_CONTROL, order_index=0)]
        overlay = SimulationOverlay(
            share_aces=(
                ShareAceChange(
                    kind=ChangeKind.REMOVE_SHARE_ACE,
                    share_key=SHARE_KEY,
                    ace_key=baseline[0].ace_key,
                ),
            )
        )

        assert overlay_share_acl(overlay, SHARE_KEY, baseline) == ()


def test_the_projection_is_the_domain_s_own_and_is_not_restated_here():
    """Clearing protection projects through :func:`app.domain.project_inherited_acl`.

    Asserted by comparison rather than by re-deriving the expected flags: the inheritance
    rules are pinned in ``tests/domain/test_inheritance.py`` against the table Windows
    actually implements, and a second copy of them here would be a second thing to get
    wrong.
    """
    parent = [
        ntfs_ace(FINANCE, FINANCE_RW, MODIFY, flags=int(AceFlag.OBJECT_INHERIT), order_index=0)
    ]
    overlay = SimulationOverlay(
        inheritance=(InheritanceChange(resource_key=REPORTS, protected=False),)
    )

    applied = overlay_ntfs_acl(overlay, REPORTS_KEY, [], parent_entries=parent, for_container=True)
    expected = project_inherited_acl([entry.acl_facts for entry in parent], for_container=True)

    assert [entry.access_mask for entry in applied] == [fact.access_mask for fact in expected]
    assert [entry.ace_flags for entry in applied] == [fact.ace_flags for fact in expected]
