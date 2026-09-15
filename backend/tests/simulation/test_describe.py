r"""The wording every simulation surface shares.

Three renderers read this module — the HTTP response, the structured export, and the web page
— so the properties worth pinning are the ones that would let them drift apart: a vocabulary
with a member nobody wrote a sentence for, a change kind whose sentence says the opposite of
what it does, and a notice somebody softened.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

import pytest

from app.domain import AceType, MembershipEdgeKind, SharePermission
from app.simulation import (
    ChangeKind,
    ImpactDirection,
    InheritanceChange,
    InheritedAceDisposition,
    MembershipChange,
    NtfsAceChange,
    ShareAceChange,
    SimulationCaveat,
    SimulationChange,
    SimulationTruncation,
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
    describe_direction,
)
from app.simulation.model import ChangeOutcome

RESOURCE = "\\\\FS01\\Finance"
SHARE = "fs01|finance"
GROUP = "S-1-5-21-1-2-3-1201"
MEMBER = "S-1-5-21-1-2-3-1104"


class TestEveryVocabularyIsComplete:
    """A member with no sentence renders as a bare code on somebody's screen."""

    @pytest.mark.parametrize(
        ("enum", "table"),
        [
            (ChangeKind, CHANGE_KIND_DESCRIPTIONS),
            (ImpactDirection, DIRECTION_DESCRIPTIONS),
            (InheritedAceDisposition, DISPOSITION_DESCRIPTIONS),
            (ChangeOutcome, OUTCOME_DESCRIPTIONS),
            (SimulationCaveat, CAVEAT_DESCRIPTIONS),
            (SimulationTruncation, TRUNCATION_DESCRIPTIONS),
        ],
    )
    def test_every_member_has_a_sentence(
        self, enum: type[StrEnum], table: Mapping[Any, str]
    ) -> None:
        assert set(enum) == set(table)
        assert all(text.strip() for text in table.values())

    def test_a_table_is_ordered_by_its_own_enum(self) -> None:
        """So two renderings of one vocabulary list it in the same order, and a diff between
        two exported documents means something changed."""
        assert list(CHANGE_KIND_DESCRIPTIONS) == list(ChangeKind)
        assert list(DIRECTION_DESCRIPTIONS) == list(ImpactDirection)


class TestTheNotice:
    def test_it_says_what_it_has_to_say(self) -> None:
        """The sentence the whole phase's first acceptance criterion rests on."""
        assert NON_DESTRUCTIVE_NOTICE.startswith("NO CHANGES WILL BE APPLIED")
        for named in ("Active Directory", "share", "NTFS descriptor"):
            assert named in NON_DESTRUCTIVE_NOTICE

    def test_it_is_one_string_rather_than_a_template(self) -> None:
        """No interpolation: a notice with a hole in it is a notice that can be rendered with
        the hole empty."""
        assert "{" not in NON_DESTRUCTIVE_NOTICE


class TestDescribingAChange:
    def test_a_membership_change_names_both_ends_and_the_edge_kind(self) -> None:
        """The edge kind is in the sentence because it is part of the edge's identity: a
        proposal naming the wrong one matches nothing, and the reader has to be able to see
        which one was written."""
        sentence = describe_change(
            MembershipChange(
                kind=ChangeKind.REMOVE_MEMBER,
                group_key=GROUP,
                member_key=MEMBER,
                edge_kind=MembershipEdgeKind.PRIMARY_GROUP,
            )
        )

        assert sentence == f"Remove {MEMBER} from {GROUP} (primary_group)."

    def test_an_addition_and_a_removal_do_not_read_alike(self) -> None:
        added = describe_change(
            MembershipChange(kind=ChangeKind.ADD_MEMBER, group_key=GROUP, member_key=MEMBER)
        )
        removed = describe_change(
            MembershipChange(kind=ChangeKind.REMOVE_MEMBER, group_key=GROUP, member_key=MEMBER)
        )

        assert added.startswith("Add ") and " to " in added
        assert removed.startswith("Remove ") and " from " in removed

    def test_a_new_deny_says_denying_rather_than_allowing(self) -> None:
        """The one word in the sentence that inverts its meaning."""
        sentence = describe_change(
            NtfsAceChange(
                kind=ChangeKind.ADD_NTFS_ACE,
                resource_key=RESOURCE,
                trustee_sid=MEMBER,
                ace_type=AceType.DENY,
                access_mask=0x001301BF,
                ace_flags=0x03,
            )
        )

        assert "denying" in sentence
        assert "allowing" not in sentence

    def test_an_inheritable_entry_says_it_reaches_children(self) -> None:
        """The fact that decides whether a change reaches a subtree, which is the question
        this phase's descendant limitation is about."""
        inheritable = describe_change(
            NtfsAceChange(
                kind=ChangeKind.ADD_NTFS_ACE,
                resource_key=RESOURCE,
                trustee_sid=MEMBER,
                ace_type=AceType.ALLOW,
                access_mask=0x001200A9,
                ace_flags=0x03,
            )
        )
        local = describe_change(
            NtfsAceChange(
                kind=ChangeKind.ADD_NTFS_ACE,
                resource_key=RESOURCE,
                trustee_sid=MEMBER,
                ace_type=AceType.ALLOW,
                access_mask=0x001200A9,
                ace_flags=0x10,
            )
        )

        assert "inheritable by children" in inheritable
        assert "on this directory only" in local

    def test_a_removal_names_the_entry_it_removes(self) -> None:
        sentence = describe_change(
            NtfsAceChange(kind=ChangeKind.REMOVE_NTFS_ACE, resource_key=RESOURCE, ace_key="ace-1")
        )

        assert "ace-1" in sentence
        assert "NTFS permissions" in sentence

    def test_a_share_change_says_share_rather_than_ntfs(self) -> None:
        """The layer confusion the whole rights model exists to prevent, in one word."""
        sentence = describe_change(
            ShareAceChange(
                kind=ChangeKind.MODIFY_SHARE_ACE,
                share_key=SHARE,
                ace_key="share-ace-1",
                permission=SharePermission.READ,
            )
        )

        assert "share permissions" in sentence
        assert "NTFS" not in sentence
        assert "read" in sentence

    def test_protecting_says_what_happens_to_the_inherited_entries(self) -> None:
        """Windows asks this in a dialog box and the two answers produce different ACLs, so
        the sentence has to carry the answer rather than only the act."""
        dropped = describe_change(
            InheritanceChange(
                resource_key=RESOURCE,
                protected=True,
                inherited_entries=InheritedAceDisposition.REMOVE,
            )
        )
        kept = describe_change(
            InheritanceChange(
                resource_key=RESOURCE,
                protected=True,
                inherited_entries=InheritedAceDisposition.CONVERT_TO_EXPLICIT,
            )
        )

        assert "Drop the entries" in dropped
        assert "Keep the entries" in kept
        assert dropped != kept

    def test_unprotecting_says_the_parent_flows_back_down(self) -> None:
        sentence = describe_change(InheritanceChange(resource_key=RESOURCE, protected=False))

        assert "inherit from its parent again" in sentence

    def test_every_change_kind_produces_a_sentence(self) -> None:
        """Exhaustive over the vocabulary, so a kind added to the overlay and not to the
        renderer fails here rather than rendering as a Python repr on a page."""
        changes: list[SimulationChange] = [
            MembershipChange(kind=ChangeKind.ADD_MEMBER, group_key=GROUP, member_key=MEMBER),
            MembershipChange(kind=ChangeKind.REMOVE_MEMBER, group_key=GROUP, member_key=MEMBER),
            NtfsAceChange(
                kind=ChangeKind.ADD_NTFS_ACE,
                resource_key=RESOURCE,
                trustee_sid=MEMBER,
                ace_type=AceType.ALLOW,
                access_mask=0x001200A9,
                ace_flags=0x03,
            ),
            NtfsAceChange(
                kind=ChangeKind.MODIFY_NTFS_ACE,
                resource_key=RESOURCE,
                ace_key="ace-1",
                access_mask=0x001200A9,
            ),
            NtfsAceChange(kind=ChangeKind.REMOVE_NTFS_ACE, resource_key=RESOURCE, ace_key="ace-1"),
            ShareAceChange(
                kind=ChangeKind.ADD_SHARE_ACE,
                share_key=SHARE,
                trustee_sid=MEMBER,
                ace_type=AceType.ALLOW,
                permission=SharePermission.FULL,
            ),
            ShareAceChange(
                kind=ChangeKind.MODIFY_SHARE_ACE,
                share_key=SHARE,
                ace_key="share-ace-1",
                permission=SharePermission.READ,
            ),
            ShareAceChange(
                kind=ChangeKind.REMOVE_SHARE_ACE, share_key=SHARE, ace_key="share-ace-1"
            ),
            InheritanceChange(
                resource_key=RESOURCE,
                protected=True,
                inherited_entries=InheritedAceDisposition.REMOVE,
            ),
        ]

        covered = {change.kind for change in changes}

        assert covered == set(ChangeKind)
        for change in changes:
            sentence = describe_change(change)
            assert sentence.endswith(".")
            assert len(sentence) > 10


class TestDescribingADirection:
    def test_gaining_access_and_expanding_do_not_read_alike(self) -> None:
        """A principal who already had Read and now has Modify is a permissions change; one
        who had nothing and now has Read is a new person in the room. A report that merged
        them would bury the second in the first."""
        assert describe_direction(ImpactDirection.GAINED_ACCESS) != describe_direction(
            ImpactDirection.EXPANDED
        )
        assert describe_direction(ImpactDirection.LOST_ACCESS) != describe_direction(
            ImpactDirection.REDUCED
        )
