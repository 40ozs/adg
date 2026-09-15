"""The proposal itself: what it accepts, what it refuses, and what it digests to."""

from __future__ import annotations

import pytest

from app.domain import AceType, DomainValidationError, MembershipEdgeKind, SharePermission
from app.simulation import (
    MAX_CHANGES,
    OVERLAY_DOCUMENT_VERSION,
    ChangeKind,
    InheritanceChange,
    InheritedAceDisposition,
    MembershipChange,
    NtfsAceChange,
    ShareAceChange,
    SimulationOverlay,
)
from tests.support.simulation import ALICE, FINANCE, FINANCE_TEAM, MODIFY, SHARE_KEY


def add_alice() -> MembershipChange:
    return MembershipChange(kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE)


class TestMembershipChanges:
    def test_a_group_cannot_be_proposed_as_a_member_of_itself(self):
        with pytest.raises(DomainValidationError):
            MembershipChange(
                kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=FINANCE_TEAM
            )

    def test_a_host_scoped_group_needs_a_local_edge_kind(self):
        """A BUILTIN SID names a different group on every computer.

        An edge into ``fs01|S-1-5-32-544`` that is not marked local would be matched against
        the wrong group, or against none — and the proposal would then report itself as
        applied and change nothing.
        """
        with pytest.raises(DomainValidationError, match="local-group edge kind"):
            MembershipChange(
                kind=ChangeKind.ADD_MEMBER,
                group_key="fs01|S-1-5-32-544",
                member_key=ALICE,
            )

    def test_a_local_edge_reads_its_host_off_the_group_key(self):
        change = MembershipChange(
            kind=ChangeKind.ADD_MEMBER,
            group_key="fs01|S-1-5-32-544",
            member_key=ALICE,
            edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
        )

        assert change.host_key == "fs01"

    def test_a_domain_edge_may_not_claim_a_local_kind(self):
        with pytest.raises(DomainValidationError):
            MembershipChange(
                kind=ChangeKind.ADD_MEMBER,
                group_key=FINANCE_TEAM,
                member_key=ALICE,
                edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            )

    def test_an_ace_kind_is_not_a_membership_change(self):
        with pytest.raises(DomainValidationError, match="not a membership change"):
            MembershipChange(kind=ChangeKind.ADD_NTFS_ACE, group_key=FINANCE_TEAM, member_key=ALICE)

    def test_the_simulated_edge_key_is_visibly_hypothetical(self):
        assert add_alice().edge_key.startswith("simulated|")


class TestNtfsAceChanges:
    def test_an_addition_needs_a_trustee_a_type_and_a_mask(self):
        with pytest.raises(DomainValidationError, match="trustee"):
            NtfsAceChange(kind=ChangeKind.ADD_NTFS_ACE, resource_key=FINANCE)

    def test_an_entry_with_no_mask_is_refused_rather_than_stored_as_a_no_op(self):
        with pytest.raises(DomainValidationError, match="not a permission"):
            NtfsAceChange(
                kind=ChangeKind.ADD_NTFS_ACE,
                resource_key=FINANCE,
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
            )

    def test_a_removal_must_name_the_entry_it_removes(self):
        with pytest.raises(DomainValidationError, match="already exists"):
            NtfsAceChange(kind=ChangeKind.REMOVE_NTFS_ACE, resource_key=FINANCE)

    def test_a_modification_must_change_something(self):
        with pytest.raises(DomainValidationError, match="must change something"):
            NtfsAceChange(kind=ChangeKind.MODIFY_NTFS_ACE, resource_key=FINANCE, ace_key="ace-1")

    def test_the_resource_key_is_folded_to_the_stored_form(self):
        change = NtfsAceChange(
            kind=ChangeKind.ADD_NTFS_ACE,
            resource_key="\\\\FS01\\Finance",
            trustee_sid=ALICE,
            ace_type=AceType.ALLOW,
            access_mask=MODIFY,
        )

        assert change.resource_key == "\\\\fs01\\finance"

    def test_the_trustee_key_is_resolved_in_the_server_s_context(self):
        """A BUILTIN trustee on FS01's tree is FS01's local group, not FS02's.

        Derived through the same function ingestion uses, so a simulated ACE is scoped the
        way a collected one is.
        """
        change = NtfsAceChange(
            kind=ChangeKind.ADD_NTFS_ACE,
            resource_key=FINANCE,
            trustee_sid="S-1-5-32-544",
            ace_type=AceType.ALLOW,
            access_mask=MODIFY,
        )

        assert change.trustee_key == "fs01|S-1-5-32-544"

    def test_a_domain_trustee_keeps_its_global_key(self):
        change = NtfsAceChange(
            kind=ChangeKind.ADD_NTFS_ACE,
            resource_key=FINANCE,
            trustee_sid=ALICE,
            ace_type=AceType.ALLOW,
            access_mask=MODIFY,
        )

        assert change.trustee_key == ALICE

    def test_a_mask_outside_thirty_two_bits_is_refused(self):
        with pytest.raises(DomainValidationError):
            NtfsAceChange(
                kind=ChangeKind.ADD_NTFS_ACE,
                resource_key=FINANCE,
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
                access_mask=0x1_0000_0000,
            )

    def test_a_negative_position_is_refused(self):
        with pytest.raises(DomainValidationError, match="not negative"):
            NtfsAceChange(
                kind=ChangeKind.ADD_NTFS_ACE,
                resource_key=FINANCE,
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
                access_mask=MODIFY,
                order_index=-1,
            )


class TestShareAceChanges:
    def test_exactly_one_right_form(self):
        with pytest.raises(DomainValidationError, match="exactly one"):
            ShareAceChange(
                kind=ChangeKind.ADD_SHARE_ACE,
                share_key=SHARE_KEY,
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
                access_mask=MODIFY,
                permission=SharePermission.CHANGE,
            )

    def test_neither_right_form_is_also_refused(self):
        with pytest.raises(DomainValidationError, match="exactly one"):
            ShareAceChange(
                kind=ChangeKind.ADD_SHARE_ACE,
                share_key=SHARE_KEY,
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
            )

    def test_a_unc_share_identifier_is_folded_to_the_storage_key(self):
        change = ShareAceChange(
            kind=ChangeKind.ADD_SHARE_ACE,
            share_key="\\\\FS01\\Finance",
            trustee_sid=ALICE,
            ace_type=AceType.ALLOW,
            permission=SharePermission.READ,
        )

        assert change.share_key == SHARE_KEY


class TestInheritanceChanges:
    def test_protecting_must_say_what_becomes_of_the_inherited_entries(self):
        with pytest.raises(DomainValidationError, match="two possible outcomes"):
            InheritanceChange(resource_key=FINANCE, protected=True)

    def test_clearing_protection_has_no_entries_to_dispose_of(self):
        with pytest.raises(DomainValidationError, match="no inherited entries"):
            InheritanceChange(
                resource_key=FINANCE,
                protected=False,
                inherited_entries=InheritedAceDisposition.REMOVE,
            )

    def test_both_dispositions_are_accepted_when_protecting(self):
        for disposition in InheritedAceDisposition:
            change = InheritanceChange(
                resource_key=FINANCE, protected=True, inherited_entries=disposition
            )
            assert change.kind is ChangeKind.SET_INHERITANCE


class TestTheOverlay:
    def test_two_changes_to_one_target_are_refused(self):
        """Their combined meaning would depend on the order they were applied in."""
        with pytest.raises(DomainValidationError, match="Two changes act on"):
            SimulationOverlay(
                membership=(
                    add_alice(),
                    MembershipChange(
                        kind=ChangeKind.REMOVE_MEMBER,
                        group_key=FINANCE_TEAM,
                        member_key=ALICE,
                    ),
                )
            )

    def test_more_changes_than_the_ceiling_are_refused(self):
        changes = tuple(
            MembershipChange(
                kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=f"{ALICE}{index}"
            )
            for index in range(MAX_CHANGES + 1)
        )
        with pytest.raises(DomainValidationError, match="at most"):
            SimulationOverlay(membership=changes)

    def test_the_digest_does_not_depend_on_the_order_changes_were_written_in(self):
        first = NtfsAceChange(
            kind=ChangeKind.ADD_NTFS_ACE,
            resource_key=FINANCE,
            trustee_sid=ALICE,
            ace_type=AceType.ALLOW,
            access_mask=MODIFY,
        )
        one = SimulationOverlay(membership=(add_alice(),), ntfs_aces=(first,))
        other = SimulationOverlay.from_changes([first, add_alice()])

        assert one.overlay_hash == other.overlay_hash

    def test_a_different_proposal_digests_differently(self):
        one = SimulationOverlay(membership=(add_alice(),))
        other = SimulationOverlay(
            membership=(
                MembershipChange(
                    kind=ChangeKind.REMOVE_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE
                ),
            )
        )

        assert one.overlay_hash != other.overlay_hash

    def test_an_empty_overlay_is_empty_and_has_no_removals(self):
        overlay = SimulationOverlay()

        assert overlay.is_empty
        assert not overlay.has_removals
        assert len(overlay) == 0

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.REMOVE_MEMBER,
            ChangeKind.REMOVE_NTFS_ACE,
            ChangeKind.MODIFY_NTFS_ACE,
            ChangeKind.SET_INHERITANCE,
        ],
    )
    def test_every_kind_that_can_take_access_away_says_so(self, kind):
        """``MODIFY`` counts, because a modification can narrow a mask."""
        assert kind.removes

    @pytest.mark.parametrize(
        "kind", [ChangeKind.ADD_MEMBER, ChangeKind.ADD_NTFS_ACE, ChangeKind.ADD_SHARE_ACE]
    )
    def test_an_addition_cannot_take_access_away(self, kind):
        assert not kind.removes


class TestSerialization:
    def test_an_overlay_survives_a_round_trip_through_its_document(self):
        overlay = SimulationOverlay.from_changes(
            [
                add_alice(),
                NtfsAceChange(
                    kind=ChangeKind.ADD_NTFS_ACE,
                    resource_key=FINANCE,
                    trustee_sid=ALICE,
                    ace_type=AceType.ALLOW,
                    access_mask=MODIFY,
                    ace_flags=3,
                    order_index=2,
                ),
                ShareAceChange(
                    kind=ChangeKind.ADD_SHARE_ACE,
                    share_key=SHARE_KEY,
                    trustee_sid=ALICE,
                    ace_type=AceType.ALLOW,
                    permission=SharePermission.CHANGE,
                ),
                InheritanceChange(
                    resource_key=FINANCE,
                    protected=True,
                    inherited_entries=InheritedAceDisposition.CONVERT_TO_EXPLICIT,
                ),
            ]
        )

        rebuilt = SimulationOverlay.from_document(overlay.document())

        assert rebuilt == overlay
        assert rebuilt.overlay_hash == overlay.overlay_hash

    def test_the_document_carries_its_version(self):
        assert SimulationOverlay().document()["document_version"] == OVERLAY_DOCUMENT_VERSION

    def test_a_document_from_another_version_is_refused_rather_than_reinterpreted(self):
        document = SimulationOverlay(membership=(add_alice(),)).document()
        document["document_version"] = "0.9"

        with pytest.raises(DomainValidationError, match="cannot be read by this build"):
            SimulationOverlay.from_document(document)

    def test_a_stored_change_is_revalidated_on_the_way_back_in(self):
        """A proposal that today's rules would refuse must not be simulated anyway."""
        document = SimulationOverlay(membership=(add_alice(),)).document()
        document["changes"][0]["member_key"] = document["changes"][0]["group_key"]

        with pytest.raises(DomainValidationError):
            SimulationOverlay.from_document(document)
