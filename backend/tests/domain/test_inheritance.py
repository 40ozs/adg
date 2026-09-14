r"""NTFS ACE propagation, and the boundary question it answers.

This is the arithmetic a tree scan rests on, and it is wrong in two directions. Too eager
and every directory in the estate is reported as a place where permissions change, burying
the few dozen where somebody actually decided something. Too lax and a real change reads as
inherited, which tells the next scan it may stop looking and silently drops every permission
change beneath it.

**The table below was measured, not recalled.** Each row was produced on Windows by creating
a directory carrying that single ACE and reading the raw descriptor of a child directory, a
grandchild, and a child file. The PowerShell mirror pins the same table
(``collector/powershell/ntfs/tests/AdgNtfsInheritance.Tests.ps1``), a live file system
re-measures it (``…/AdgNtfsRealFileSystem.Tests.ps1``), and the contract test runs the real
collector and compares every verdict against this module.
"""

from __future__ import annotations

import pytest

from app.domain import (
    AceFlag,
    AceType,
    AclAceFacts,
    AclBoundaryReason,
    acl_hash,
    boundary_reason_for,
    inherited_child_acl_hash,
    map_generic_rights,
    normalize_acl,
    project_inherited_ace,
    project_inherited_ace_flags,
    project_inherited_acl,
    projected_child_acl,
)

ADMINS = "S-1-5-32-544"
USERS = "S-1-5-21-1004336348-1177238915-682003330-1201"
EVERYONE = "S-1-1-0"

FULL_CONTROL = 0x001F01FF
READ_EXECUTE = 0x001200A9

#: parent flag byte -> (what a child container receives, what a child file receives).
#: ``None`` means the entry does not descend to a child of that kind at all, which is a
#: different answer from an entry that descends carrying no flags (``0x10``).
#:
#: This is the **single-entry** table: it holds for an ACE whose mask carries no generic bits
#: and whose trustee Windows does not substitute. The two exceptions each split one parent
#: entry into two child entries, and have their own class below.
MEASURED = [
    pytest.param(0x02, 0x12, None, id="CI"),
    pytest.param(0x01, 0x19, 0x10, id="OI"),
    pytest.param(0x03, 0x13, 0x10, id="OI|CI"),
    pytest.param(0x0A, 0x12, None, id="CI|IO"),
    pytest.param(0x09, 0x19, 0x10, id="OI|IO"),
    pytest.param(0x0B, 0x13, 0x10, id="OI|CI|IO"),
    pytest.param(0x06, 0x10, None, id="CI|NP"),
    pytest.param(0x05, None, 0x10, id="OI|NP"),
    pytest.param(0x07, 0x10, 0x10, id="OI|CI|NP"),
    pytest.param(0x0F, 0x10, 0x10, id="OI|CI|NP|IO"),
    pytest.param(0x00, None, None, id="none"),
]


def fact(
    *,
    trustee: str = EVERYONE,
    ace_type: AceType = AceType.ALLOW,
    mask: int = READ_EXECUTE,
    flags: int = 0x03,
    order: int | None = 0,
) -> AclAceFacts:
    return AclAceFacts(
        trustee_sid=trustee,
        ace_type=ace_type,
        access_mask=mask,
        ace_flags=flags,
        order_index=order,
    )


class TestTheMeasuredTable:
    @pytest.mark.parametrize(("flags", "container", "_object"), MEASURED)
    def test_a_child_container_receives_what_windows_gives_it(
        self, flags: int, container: int | None, _object: int | None
    ) -> None:
        assert project_inherited_ace_flags(flags, for_container=True) == container

    @pytest.mark.parametrize(("flags", "_container", "object_"), MEASURED)
    def test_a_child_file_receives_what_windows_gives_it(
        self, flags: int, _container: int | None, object_: int | None
    ) -> None:
        assert project_inherited_ace_flags(flags, for_container=False) == object_

    def test_the_parents_own_inherited_bit_is_irrelevant(self) -> None:
        # An ACE the parent inherited propagates exactly as one set on the parent does. If
        # the bit were treated as meaningful, the second level of every tree would stop
        # inheriting and every grandchild would look like a boundary.
        assert project_inherited_ace_flags(0x13, for_container=True) == project_inherited_ace_flags(
            0x03, for_container=True
        )

    def test_inherit_only_is_not_a_propagation_stop(self) -> None:
        # CI|IO is "subfolders only": the bit says the ACE does not apply to the folder
        # holding it, which is a statement about the parent, and it descends exactly as
        # plain CI does.
        assert project_inherited_ace_flags(0x0A, for_container=True) == project_inherited_ace_flags(
            0x02, for_container=True
        )

    def test_an_object_inherit_entry_is_carried_through_containers_it_does_not_apply_to(
        self,
    ) -> None:
        # 0x19 is OI|IO|INHERITED: it grants nothing on this folder and still reaches the
        # files below it. Dropping it would make every folder under a "files only" grant
        # look like a place where permissions changed.
        projected = project_inherited_ace_flags(0x01, for_container=True)
        assert projected is not None
        assert AceFlag(projected) & AceFlag.INHERIT_ONLY
        assert AceFlag(projected) & AceFlag.OBJECT_INHERIT

    def test_no_propagate_stops_at_the_grandchild(self) -> None:
        child = project_inherited_ace_flags(0x07, for_container=True)
        assert child == int(AceFlag.INHERITED)
        assert project_inherited_ace_flags(child, for_container=True) is None

    def test_an_ordinary_inheritable_entry_reaches_a_fixed_point_after_one_level(self) -> None:
        # The reason a boundary comparison works below the first level at all: a clean child
        # and a clean grandchild carry the identical DACL.
        child = project_inherited_ace_flags(0x03, for_container=True)
        assert child is not None
        assert project_inherited_ace_flags(child, for_container=True) == child

    def test_every_projected_entry_is_marked_inherited(self) -> None:
        for flags in range(0x100):
            for container in (True, False):
                projected = project_inherited_ace_flags(flags, for_container=container)
                if projected is not None:
                    assert AceFlag(projected) & AceFlag.INHERITED

    def test_bits_above_the_flags_byte_are_ignored(self) -> None:
        assert project_inherited_ace_flags(0x0103, for_container=True) == 0x13


#: A mask with generic bits, and what the file-system generic mapping resolves it to.
#: 0xe0010000 is GENERIC_READ|GENERIC_WRITE|GENERIC_EXECUTE|DELETE — the generic form of
#: Modify, and the mask Explorer writes on almost every directory it creates.
GENERIC_MODIFY = 0xE0010000
MAPPED_MODIFY = 0x001301BF

CREATOR_OWNER = "S-1-3-0"
OWNER_RIGHTS = "S-1-3-4"


class TestTheGenericSplit:
    """An ACE carrying a generic right becomes two entries on a child, not one.

    Not an edge case, and not a subtlety: ``0xe0010000`` sits on almost every directory
    created through Explorer, so a projection that misses this reports **every** such
    directory as a boundary — which is the exact failure the projection exists to prevent,
    and which is invisible to any test whose fixture masks happen to be specific.

    Every expectation below was measured: a directory carrying the single ACE under test,
    then the raw descriptor of the real child directory, grandchild, and file Windows
    created beneath it. ``AdgNtfsRealFileSystem.Tests.ps1`` re-measures the same table.
    """

    def project(
        self, flags: int, *, mask: int = GENERIC_MODIFY, container: bool = True
    ) -> list[tuple[int, int]]:
        entry = fact(trustee=EVERYONE, mask=mask, flags=flags, order=0)
        return [
            (item.ace_flags, item.access_mask)
            for item in project_inherited_ace(entry, for_container=container)
        ]

    def test_the_mapping_is_the_one_windows_uses(self) -> None:
        assert map_generic_rights(GENERIC_MODIFY) == MAPPED_MODIFY

    def test_it_leaves_a_mask_with_no_generic_bits_alone(self) -> None:
        assert map_generic_rights(MAPPED_MODIFY) == MAPPED_MODIFY

    def test_it_keeps_the_specific_bits_that_were_already_there(self) -> None:
        # DELETE (0x00010000) rides along with the generic bits in the real mask, and
        # dropping it would change the grant.
        assert map_generic_rights(GENERIC_MODIFY) & 0x00010000

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            # effective copy (mapped, flags cleared), then propagating copy (unmapped, IO).
            pytest.param(0x02, [(0x10, MAPPED_MODIFY), (0x1A, GENERIC_MODIFY)], id="CI"),
            pytest.param(0x0A, [(0x10, MAPPED_MODIFY), (0x1A, GENERIC_MODIFY)], id="CI|IO"),
            pytest.param(0x03, [(0x10, MAPPED_MODIFY), (0x1B, GENERIC_MODIFY)], id="OI|CI"),
            pytest.param(0x0B, [(0x10, MAPPED_MODIFY), (0x1B, GENERIC_MODIFY)], id="OI|CI|IO"),
            # No CI, so nothing applies to the child container — only the propagating half.
            pytest.param(0x01, [(0x19, GENERIC_MODIFY)], id="OI"),
            # NO_PROPAGATE: the effective copy lands and nothing descends past it.
            pytest.param(0x06, [(0x10, MAPPED_MODIFY)], id="CI|NP"),
            pytest.param(0x07, [(0x10, MAPPED_MODIFY)], id="OI|CI|NP"),
            pytest.param(0x05, [], id="OI|NP"),
            pytest.param(0x00, [], id="none"),
        ],
    )
    def test_a_child_container_receives_what_windows_gave_it(
        self, flags: int, expected: list[tuple[int, int]]
    ) -> None:
        assert self.project(flags) == expected

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            pytest.param(0x01, [(0x10, MAPPED_MODIFY)], id="OI"),
            pytest.param(0x03, [(0x10, MAPPED_MODIFY)], id="OI|CI"),
            pytest.param(0x0B, [(0x10, MAPPED_MODIFY)], id="OI|CI|IO"),
            pytest.param(0x05, [(0x10, MAPPED_MODIFY)], id="OI|NP"),
            pytest.param(0x07, [(0x10, MAPPED_MODIFY)], id="OI|CI|NP"),
            pytest.param(0x02, [], id="CI"),
            pytest.param(0x06, [], id="CI|NP"),
        ],
    )
    def test_a_child_file_receives_only_the_effective_copy(
        self, flags: int, expected: list[tuple[int, int]]
    ) -> None:
        # A file is a leaf: it never carries the propagating half, and its copy is mapped
        # because a generic mask cannot be applied to an object unresolved.
        assert self.project(flags, container=False) == expected

    def test_the_pair_is_a_fixed_point(self) -> None:
        # The parent's own DACL holds the same pair, which is why this does not grow with
        # depth — and why a clean grandchild still compares equal to a clean child.
        parent = [
            fact(trustee=EVERYONE, mask=MAPPED_MODIFY, flags=0x10, order=0),
            fact(trustee=EVERYONE, mask=GENERIC_MODIFY, flags=0x1B, order=1),
        ]
        projected = project_inherited_acl(parent, for_container=True)
        assert [(item.ace_flags, item.access_mask) for item in projected] == [
            (0x10, MAPPED_MODIFY),
            (0x1B, GENERIC_MODIFY),
        ]

    def test_the_propagating_copy_keeps_the_mask_unmapped(self) -> None:
        # It is still an indirection for whatever object it eventually lands on; resolving
        # it early would hand a grandchild a mask Windows never wrote.
        projected = self.project(0x0B)
        assert projected[1] == (0x1B, GENERIC_MODIFY)

    def test_a_specific_mask_still_produces_one_entry(self) -> None:
        assert self.project(0x03, mask=MAPPED_MODIFY) == [(0x13, MAPPED_MODIFY)]

    def test_the_single_entry_rule_and_the_split_rule_agree_where_they_overlap(self) -> None:
        # For every flag byte, a specific-mask ACE must project to exactly what the
        # single-entry table says — so the split cannot have changed the ordinary case.
        for flags in range(0x100):
            for container in (True, False):
                entry = fact(trustee=EVERYONE, mask=MAPPED_MODIFY, flags=flags)
                projected = project_inherited_ace(entry, for_container=container)
                expected = project_inherited_ace_flags(flags, for_container=container)
                if expected is None:
                    assert projected == ()
                else:
                    assert [item.ace_flags for item in projected] == [expected]


class TestASubstitutedTrustee:
    """CREATOR OWNER hands down half an entry, and Windows invents the other half."""

    def project(
        self, flags: int, *, trustee: str = CREATOR_OWNER, container: bool = True
    ) -> list[tuple[int, int]]:
        entry = fact(trustee=trustee, mask=0x10000000, flags=flags, order=0)
        return [
            (item.ace_flags, item.access_mask)
            for item in project_inherited_ace(entry, for_container=container)
        ]

    def test_a_container_receives_the_propagating_copy_with_inherit_only_preserved(self) -> None:
        # Measured: OI|CI|IO (0x0b) -> 0x1b, and CI|IO (0x0a) -> 0x1a. An ordinary trustee
        # would have had INHERIT_ONLY cleared, because the entry would apply to the child.
        assert self.project(0x0B) == [(0x1B, 0x10000000)]
        assert self.project(0x0A) == [(0x1A, 0x10000000)]

    def test_no_effective_copy_is_predicted_for_a_container(self) -> None:
        # The effective copy names whoever created the child, which is not a fact about the
        # parent. It is the one thing this projection cannot know, and the reason a
        # directory beneath such a grant reports acl_differs_from_parent.
        assert all(flags & int(AceFlag.INHERIT_ONLY) for flags, _ in self.project(0x0B))

    def test_a_file_receives_nothing_predictable(self) -> None:
        assert self.project(0x0B, container=False) == []

    def test_the_mask_is_left_unmapped_even_though_it_is_generic(self) -> None:
        # GENERIC_ALL is a generic bit, and Windows still writes it unmapped on the
        # propagating copy — because that copy is not being applied to anything yet.
        assert self.project(0x0B) == [(0x1B, 0x10000000)]

    def test_owner_rights_is_not_treated_as_a_creator_sid(self) -> None:
        # S-1-3-4 looks like a sibling of S-1-3-0 and is not one: Windows inherits it like
        # any other trustee and resolves it against the current owner at access time.
        # Measured, because assuming otherwise would silently make every directory under an
        # OWNER RIGHTS grant a boundary.
        entry = fact(trustee=OWNER_RIGHTS, mask=MAPPED_MODIFY, flags=0x03, order=0)
        projected = project_inherited_ace(entry, for_container=True)
        assert [(item.ace_flags, item.access_mask) for item in projected] == [(0x13, MAPPED_MODIFY)]


class TestProjectingAnAcl:
    def test_entries_that_do_not_descend_are_dropped(self) -> None:
        projected = project_inherited_acl(
            [
                fact(trustee=ADMINS, flags=0x03, order=0),
                fact(trustee=EVERYONE, flags=0x00, order=1),
                fact(trustee=USERS, flags=0x02, order=2),
            ],
            for_container=True,
        )
        assert [entry.trustee_sid for entry in projected] == [ADMINS, USERS]

    def test_positions_are_renumbered_over_the_survivors(self) -> None:
        projected = project_inherited_acl(
            [
                fact(flags=0x00, order=0),
                fact(trustee=ADMINS, flags=0x03, order=1),
                fact(trustee=USERS, flags=0x03, order=2),
            ],
            for_container=True,
        )
        assert [entry.order_index for entry in projected] == [0, 1]

    def test_the_result_does_not_depend_on_the_order_the_entries_arrived_in(self) -> None:
        # A projection built from database rows must equal one built from a live descriptor,
        # and rows come back in whatever order the query produced.
        entries = [
            fact(trustee=ADMINS, flags=0x03, order=0),
            fact(trustee=USERS, flags=0x02, order=1),
        ]
        assert project_inherited_acl(entries, for_container=True) == project_inherited_acl(
            list(reversed(entries)), for_container=True
        )

    def test_the_trustee_and_type_are_untouched(self) -> None:
        projected = project_inherited_acl(
            [fact(trustee=EVERYONE, ace_type=AceType.DENY, mask=MAPPED_MODIFY, flags=0x03)],
            for_container=True,
        )
        assert projected[0].trustee_sid == EVERYONE
        assert projected[0].ace_type is AceType.DENY
        # A specific mask passes through exactly as read. Nothing here interprets a mask;
        # the one place a generic bit is resolved is the effective half of a split entry,
        # which exists only so a prediction can be compared against what Windows wrote.
        assert projected[0].access_mask == MAPPED_MODIFY

    def test_an_unrecognized_mask_bit_survives(self) -> None:
        # A bit no NtfsRight names is still part of the grant, and a projection that dropped
        # it would report a boundary on every directory beneath the entry carrying it.
        odd = MAPPED_MODIFY | 0x00000800
        projected = project_inherited_acl(
            [fact(trustee=EVERYONE, mask=odd, flags=0x03)], for_container=True
        )
        assert projected[0].access_mask == odd

    def test_a_parent_that_hands_down_nothing_projects_an_empty_acl(self) -> None:
        assert project_inherited_acl([fact(flags=0x00)], for_container=True) == ()

    def test_a_split_entry_is_renumbered_as_two(self) -> None:
        # One parent ACE, two child entries, and the positions have to account for both or
        # the normalized document would carry a gap.
        projected = project_inherited_acl(
            [
                fact(trustee=ADMINS, mask=MAPPED_MODIFY, flags=0x03, order=0),
                fact(trustee=EVERYONE, mask=GENERIC_MODIFY, flags=0x03, order=1),
            ],
            for_container=True,
        )
        assert [entry.order_index for entry in projected] == [0, 1, 2]
        assert [entry.trustee_sid for entry in projected] == [ADMINS, EVERYONE, EVERYONE]

    def test_entries_without_a_position_sort_last_and_deterministically(self) -> None:
        projected = project_inherited_acl(
            [
                fact(trustee=USERS, flags=0x03, order=None),
                fact(trustee=ADMINS, flags=0x03, order=0),
            ],
            for_container=True,
        )
        assert [entry.trustee_sid for entry in projected] == [ADMINS, USERS]


class TestTheProjectedDigest:
    def setup_method(self) -> None:
        self.parent = [
            fact(trustee=ADMINS, mask=FULL_CONTROL, flags=0x03, order=0),
            fact(trustee=USERS, mask=READ_EXECUTE, flags=0x03, order=1),
        ]
        # What Windows actually produces beneath that parent: the same entries with
        # INHERITED added.
        self.clean_child = [
            fact(trustee=ADMINS, mask=FULL_CONTROL, flags=0x13, order=0),
            fact(trustee=USERS, mask=READ_EXECUTE, flags=0x13, order=1),
        ]

    def test_it_equals_the_digest_of_a_child_that_inherited_cleanly(self) -> None:
        assert inherited_child_acl_hash(dacl_present=True, aces=self.parent) == acl_hash(
            dacl_present=True, aces=self.clean_child
        )

    def test_it_never_equals_the_parents_own_digest(self) -> None:
        # The whole reason the projection exists. Inheritance sets the INHERITED bit on
        # every entry it copies, so a parent and a perfectly inheriting child are different
        # documents — and comparing them directly would report every directory in the estate
        # as a boundary.
        assert inherited_child_acl_hash(dacl_present=True, aces=self.parent) != acl_hash(
            dacl_present=True, aces=self.parent
        )

    def test_it_is_stable_at_every_level_below_the_first(self) -> None:
        assert inherited_child_acl_hash(
            dacl_present=True, aces=self.clean_child
        ) == inherited_child_acl_hash(dacl_present=True, aces=self.parent)

    def test_a_container_and_an_object_projection_are_different_documents(self) -> None:
        assert inherited_child_acl_hash(
            dacl_present=True, aces=self.parent, for_container=False
        ) != inherited_child_acl_hash(dacl_present=True, aces=self.parent, for_container=True)

    def test_a_null_dacl_projects_nothing_at_all(self) -> None:
        # What a child of a NULL-DACL directory holds comes from the creating process's
        # default DACL, which is not a fact about the parent.
        assert projected_child_acl(dacl_present=False) is None
        assert inherited_child_acl_hash(dacl_present=False) is None

    def test_a_dacl_with_no_inheritable_entries_projects_a_present_empty_one(self) -> None:
        # Not the same as projecting nothing: this parent hands its children an empty DACL,
        # which grants nobody access, and a child holding one is inheriting correctly.
        projection = projected_child_acl(dacl_present=True, aces=[fact(flags=0x00)])
        assert projection is not None
        assert projection.digest == acl_hash(dacl_present=True, aces=[])

    def test_the_projected_document_is_always_present_and_unprotected(self) -> None:
        # Inheritance produces a present DACL even when it produces no entries, and a child
        # that is itself protected is a boundary on that basis alone — so a projection
        # claiming protection could only ever make a boundary invisible.
        projection = projected_child_acl(dacl_present=True, aces=self.parent)
        assert projection is not None
        assert "dacl_present=true" in projection.lines
        assert "dacl_protected=false" in projection.lines

    def test_a_protected_parent_still_hands_its_entries_down(self) -> None:
        # Protection says a directory refuses entries from above, not that it withholds
        # them from below.
        assert inherited_child_acl_hash(dacl_present=True, aces=self.parent) == acl_hash(
            dacl_present=True, aces=project_inherited_acl(self.parent, for_container=True)
        )

    def test_the_projection_is_taken_over_the_normalized_form(self) -> None:
        projection = projected_child_acl(dacl_present=True, aces=self.parent)
        assert projection is not None
        assert projection == normalize_acl(
            dacl_present=True,
            dacl_protected=False,
            aces=project_inherited_acl(self.parent, for_container=True),
        )


class TestTheBoundaryReason:
    HASH = "a" * 64
    OTHER = "b" * 64

    def reason(
        self,
        *,
        is_share_root: bool = False,
        is_scan_root: bool = False,
        dacl_present: bool = True,
        dacl_protected: bool = False,
        acl_hash: str | None = HASH,
        parent_dacl_present: bool | None = None,
        parent_projection: str | None = None,
    ) -> AclBoundaryReason | None:
        """The rule with ordinary defaults, so each case states only what it changes."""
        return boundary_reason_for(
            is_share_root=is_share_root,
            is_scan_root=is_scan_root,
            dacl_present=dacl_present,
            dacl_protected=dacl_protected,
            acl_hash=acl_hash,
            parent_dacl_present=parent_dacl_present,
            parent_projection=parent_projection,
        )

    def test_a_digest_matching_the_projection_is_not_a_boundary(self) -> None:
        assert (
            boundary_reason_for(
                is_share_root=False,
                is_scan_root=False,
                dacl_present=True,
                dacl_protected=False,
                acl_hash=self.HASH,
                parent_dacl_present=True,
                parent_projection=self.HASH,
            )
            is None
        )

    def test_a_digest_that_does_not_match_is_the_ordinary_finding(self) -> None:
        assert (
            boundary_reason_for(
                is_share_root=False,
                is_scan_root=False,
                dacl_present=True,
                dacl_protected=False,
                acl_hash=self.HASH,
                parent_dacl_present=True,
                parent_projection=self.OTHER,
            )
            is AclBoundaryReason.ACL_DIFFERS_FROM_PARENT
        )

    def test_protection_is_decided_first(self) -> None:
        # A directory that refuses inherited entries is a boundary whatever a projection
        # says, and the order matters: the comparison would otherwise decide first and could
        # call a protected directory unchanged.
        assert (
            boundary_reason_for(
                is_share_root=False,
                is_scan_root=False,
                dacl_present=True,
                dacl_protected=True,
                acl_hash=self.HASH,
                parent_dacl_present=True,
                parent_projection=self.HASH,
            )
            is AclBoundaryReason.PROTECTED_DACL
        )

    def test_a_null_dacl_outranks_the_path_shape(self) -> None:
        assert (
            boundary_reason_for(
                is_share_root=True,
                is_scan_root=False,
                dacl_present=False,
                dacl_protected=False,
                acl_hash=None,
                parent_dacl_present=None,
                parent_projection=None,
            )
            is AclBoundaryReason.NULL_DACL
        )

    def test_every_case_nobody_could_establish_is_reported_as_a_boundary(self) -> None:
        # The asymmetry is the whole point: a boundary that is not really there costs one
        # extra stored ACL, while a boundary reported false tells the next scan it may stop
        # looking and silently drops every permission change beneath it.
        assert self.reason(is_share_root=True) is AclBoundaryReason.SHARE_ROOT
        assert self.reason(is_scan_root=True) is AclBoundaryReason.SCAN_ROOT
        assert self.reason(parent_dacl_present=None) is AclBoundaryReason.PARENT_UNREADABLE
        assert self.reason(parent_dacl_present=False) is AclBoundaryReason.PARENT_NULL_DACL
        # The parent was read and still projects nothing usable, which is unknown too.
        assert self.reason(parent_dacl_present=True) is AclBoundaryReason.PARENT_UNREADABLE

    def test_a_resource_whose_own_dacl_was_only_partly_read_is_unknown(self) -> None:
        # No digest means no comparison was made. An unread ACL is not an unchanged one.
        assert (
            boundary_reason_for(
                is_share_root=False,
                is_scan_root=False,
                dacl_present=True,
                dacl_protected=False,
                acl_hash=None,
                parent_dacl_present=True,
                parent_projection=self.HASH,
            )
            is AclBoundaryReason.PARENT_UNREADABLE
        )

    def test_share_root_outranks_scan_root(self) -> None:
        assert (
            boundary_reason_for(
                is_share_root=True,
                is_scan_root=True,
                dacl_present=True,
                dacl_protected=False,
                acl_hash=self.HASH,
                parent_dacl_present=None,
                parent_projection=None,
            )
            is AclBoundaryReason.SHARE_ROOT
        )

    def test_none_is_the_only_value_that_means_not_a_boundary(self) -> None:
        # Every case where nobody established anything returns a reason, and therefore a
        # boundary. Only a completed comparison that matched returns None.
        assert self.reason(is_scan_root=True) is not None
        assert self.reason(parent_dacl_present=None) is not None
        assert self.reason(parent_dacl_present=False) is not None
        assert self.reason(parent_dacl_present=True, parent_projection=self.HASH) is None


class TestAgainstARealisticTree:
    """The comparison end to end, on the shape that made the naive version wrong."""

    def setup_method(self) -> None:
        self.root = [
            fact(trustee=ADMINS, mask=FULL_CONTROL, flags=0x03, order=0),
            fact(trustee=USERS, mask=READ_EXECUTE, flags=0x03, order=1),
        ]
        self.child = [
            fact(trustee=ADMINS, mask=FULL_CONTROL, flags=0x13, order=0),
            fact(trustee=USERS, mask=READ_EXECUTE, flags=0x13, order=1),
        ]

    def _reason(
        self, own: list[AclAceFacts], parent: list[AclAceFacts], *, protected: bool = False
    ) -> AclBoundaryReason | None:
        return boundary_reason_for(
            is_share_root=False,
            is_scan_root=False,
            dacl_present=True,
            dacl_protected=protected,
            acl_hash=acl_hash(dacl_present=True, dacl_protected=protected, aces=own),
            parent_dacl_present=True,
            parent_projection=inherited_child_acl_hash(dacl_present=True, aces=parent),
        )

    def test_a_clean_child_is_not_a_boundary(self) -> None:
        assert self._reason(self.child, self.root) is None

    def test_a_clean_grandchild_is_not_a_boundary(self) -> None:
        assert self._reason(self.child, self.child) is None

    def test_an_added_explicit_entry_is(self) -> None:
        edited = [*self.child, fact(trustee=EVERYONE, mask=READ_EXECUTE, flags=0x03, order=2)]
        assert self._reason(edited, self.root) is AclBoundaryReason.ACL_DIFFERS_FROM_PARENT

    def test_a_removed_inherited_entry_is(self) -> None:
        assert self._reason(self.child[:1], self.root) is AclBoundaryReason.ACL_DIFFERS_FROM_PARENT

    def test_a_reordered_dacl_is(self) -> None:
        # A Deny moved below an Allow grants access that was previously refused, so
        # evaluation order is part of the digest and therefore part of the comparison.
        swapped = [
            fact(trustee=USERS, mask=READ_EXECUTE, flags=0x13, order=0),
            fact(trustee=ADMINS, mask=FULL_CONTROL, flags=0x13, order=1),
        ]
        assert self._reason(swapped, self.root) is AclBoundaryReason.ACL_DIFFERS_FROM_PARENT

    def test_a_changed_mask_is(self) -> None:
        widened = [
            fact(trustee=ADMINS, mask=FULL_CONTROL, flags=0x13, order=0),
            fact(trustee=USERS, mask=FULL_CONTROL, flags=0x13, order=1),
        ]
        assert self._reason(widened, self.root) is AclBoundaryReason.ACL_DIFFERS_FROM_PARENT

    def test_protection_is_a_boundary_even_when_the_entries_match(self) -> None:
        assert (
            self._reason(self.child, self.root, protected=True) is AclBoundaryReason.PROTECTED_DACL
        )
