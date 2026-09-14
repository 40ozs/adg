"""Raw permission facts: ACE invariants for both authorization layers."""

from __future__ import annotations

import pytest

from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    AclLayer,
    NtfsAce,
    NtfsRight,
    SecurityDescriptorFacts,
    SharePermission,
    Sid,
    SmbShareAce,
)
from app.domain.errors import DomainValidationError

TRUSTEE = Sid("S-1-5-21-1-2-3-1104")
READ_EXECUTE = 0x001200A9
FULL_CONTROL = 0x001F01FF


class TestNtfsAce:
    def test_the_raw_mask_is_preserved_exactly(self) -> None:
        ace = NtfsAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=FULL_CONTROL)

        assert ace.access_mask == FULL_CONTROL

    def test_rights_are_exposed_as_flags(self) -> None:
        ace = NtfsAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=READ_EXECUTE)

        assert NtfsRight.READ_DATA in ace.rights
        assert NtfsRight.EXECUTE in ace.rights
        assert NtfsRight.WRITE_DATA not in ace.rights

    def test_escalation_rights_are_visible(self) -> None:
        ace = NtfsAce(
            trustee_sid=TRUSTEE,
            ace_type=AceType.ALLOW,
            access_mask=int(NtfsRight.WRITE_DAC | NtfsRight.WRITE_OWNER),
        )

        assert NtfsRight.WRITE_DAC in ace.rights
        assert NtfsRight.WRITE_OWNER in ace.rights

    def test_generic_rights_are_kept_unexpanded(self) -> None:
        ace = NtfsAce(
            trustee_sid=TRUSTEE,
            ace_type=AceType.ALLOW,
            access_mask=int(NtfsRight.GENERIC_ALL),
        )

        assert ace.uses_generic_rights is True
        # Not expanded into specific rights: that mapping belongs to the Phase 4 engine.
        assert NtfsRight.READ_DATA not in ace.rights

    def test_unknown_mask_bits_stay_visible(self) -> None:
        ace = NtfsAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=0x00000200)

        assert ace.unrecognized_bits == 0x00000200

    def test_deny_aces_are_recorded_as_observed(self) -> None:
        ace = NtfsAce(trustee_sid=TRUSTEE, ace_type=AceType.DENY, access_mask=FULL_CONTROL)

        # No precedence is applied here; the ACE only states what the descriptor holds.
        assert ace.ace_type is AceType.DENY

    def test_inheritance_flags_and_source_must_agree(self) -> None:
        with pytest.raises(DomainValidationError, match="contradicts"):
            NtfsAce(
                trustee_sid=TRUSTEE,
                ace_type=AceType.ALLOW,
                access_mask=READ_EXECUTE,
                flags=AceFlag.INHERITED,
                source=AceSource.EXPLICIT,
            )

    def test_an_inherited_ace_is_consistent(self) -> None:
        ace = NtfsAce(
            trustee_sid=TRUSTEE,
            ace_type=AceType.ALLOW,
            access_mask=READ_EXECUTE,
            flags=AceFlag.INHERITED | AceFlag.OBJECT_INHERIT | AceFlag.CONTAINER_INHERIT,
            source=AceSource.INHERITED,
            inherited_from="\\\\FS01\\Finance",
        )

        assert ace.is_inherited is True
        assert ace.is_inheritable is True
        assert ace.inherited_from == "\\\\FS01\\Finance"

    def test_an_explicit_ace_may_not_claim_an_inheritance_origin(self) -> None:
        with pytest.raises(DomainValidationError, match="inheritance origin"):
            NtfsAce(
                trustee_sid=TRUSTEE,
                ace_type=AceType.ALLOW,
                access_mask=READ_EXECUTE,
                inherited_from="\\\\FS01\\Finance",
            )

    def test_inherit_only_aces_do_not_apply_to_their_own_object(self) -> None:
        ace = NtfsAce(
            trustee_sid=TRUSTEE,
            ace_type=AceType.ALLOW,
            access_mask=FULL_CONTROL,
            flags=AceFlag.INHERIT_ONLY | AceFlag.OBJECT_INHERIT,
        )

        assert ace.applies_to_this_object is False
        assert ace.is_inheritable is True

    def test_no_propagate_flag_is_preserved(self) -> None:
        ace = NtfsAce(
            trustee_sid=TRUSTEE,
            ace_type=AceType.ALLOW,
            access_mask=FULL_CONTROL,
            flags=AceFlag.CONTAINER_INHERIT | AceFlag.NO_PROPAGATE_INHERIT,
        )

        assert AceFlag.NO_PROPAGATE_INHERIT in ace.flags

    @pytest.mark.parametrize("mask", [-1, 2**32, 2**40])
    def test_masks_outside_32_bits_are_rejected(self, mask: int) -> None:
        with pytest.raises(DomainValidationError, match="32-bit"):
            NtfsAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=mask)

    def test_negative_order_index_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="order_index"):
            NtfsAce(
                trustee_sid=TRUSTEE,
                ace_type=AceType.ALLOW,
                access_mask=READ_EXECUTE,
                order_index=-1,
            )

    def test_an_unresolved_trustee_is_acceptable(self) -> None:
        orphan = Sid("S-1-5-21-999-888-777-1234")
        ace = NtfsAce(trustee_sid=orphan, ace_type=AceType.ALLOW, access_mask=FULL_CONTROL)

        assert ace.trustee_sid == orphan


class TestSmbShareAce:
    def test_a_permission_level_may_be_recorded(self) -> None:
        ace = SmbShareAce(
            trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, permission=SharePermission.CHANGE
        )

        assert ace.permission is SharePermission.CHANGE
        assert ace.access_mask is None
        assert ace.layer is AclLayer.SMB_SHARE

    def test_an_access_mask_may_be_recorded(self) -> None:
        ace = SmbShareAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=FULL_CONTROL)

        assert ace.access_mask == FULL_CONTROL
        assert ace.permission is None

    def test_recording_neither_form_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="exactly one"):
            SmbShareAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW)

    def test_inventing_the_other_form_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="exactly one"):
            SmbShareAce(
                trustee_sid=TRUSTEE,
                ace_type=AceType.ALLOW,
                access_mask=FULL_CONTROL,
                permission=SharePermission.FULL,
            )

    def test_share_and_ntfs_aces_are_different_types(self) -> None:
        share_ace = SmbShareAce(
            trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, permission=SharePermission.FULL
        )
        ntfs_ace = NtfsAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=FULL_CONTROL)

        # The two layers are intersected by the Phase 4 engine, never interchanged, so
        # neither type carries the other's fields.
        assert not hasattr(share_ace, "flags")
        assert not hasattr(ntfs_ace, "permission")


class TestSecurityDescriptorFacts:
    def test_a_null_dacl_means_everyone_has_full_access(self) -> None:
        facts = SecurityDescriptorFacts(dacl_present=False)

        assert facts.grants_everyone_full_access is True
        assert facts.denies_everyone is False

    def test_an_empty_dacl_means_nobody_has_access(self) -> None:
        facts = SecurityDescriptorFacts(dacl_present=True, ace_count=0)

        assert facts.denies_everyone is True
        assert facts.grants_everyone_full_access is False

    def test_a_missing_dacl_cannot_carry_aces(self) -> None:
        with pytest.raises(DomainValidationError, match="NULL DACL"):
            SecurityDescriptorFacts(dacl_present=False, ace_count=3)

    def test_protection_from_inheritance_is_recorded(self) -> None:
        facts = SecurityDescriptorFacts(
            owner_sid=TRUSTEE, dacl_present=True, dacl_protected=True, ace_count=4
        )

        assert facts.dacl_protected is True
        assert facts.owner_sid == TRUSTEE
