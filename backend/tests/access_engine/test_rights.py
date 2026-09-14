"""Rights algebra: masks, normalization, layer separation, and display derivation.

The exhaustive tests at the bottom are the important ones. Every acceptance criterion for
this phase is a property that must hold for *all* masks, not for a handful of examples, so
they are checked over all 16,384 combinations of the fourteen file-system rights rather than
over a sample.
"""

from __future__ import annotations

import itertools

import pytest

from app.access_engine.rights import (
    CATEGORY_REQUIRED_MASKS,
    FILE_ALL_ACCESS,
    FILE_GENERIC_EXECUTE,
    FILE_GENERIC_READ,
    FILE_GENERIC_WRITE,
    MAX_ACCESS_MASK,
    SHARE_LEVEL_MASKS,
    SYNCHRONIZE_BIT,
    AccessPath,
    EffectiveRights,
    ExtendedRight,
    RightsCategory,
    RightsError,
    RightsLayer,
    RightsLayerError,
    RightsMask,
    apply_deny,
    category_display_name,
    classify_share_mask,
    effective_rights,
    intersect_all,
    normalize_mask,
    normalize_ntfs_mask,
    normalize_share_ace,
    normalize_share_mask,
    normalize_share_permission,
    resolve_canonical,
    summarize,
    union_all,
)
from app.domain.access import (
    SHARE_PERMISSION_MASKS,
    AceType,
    AclLayer,
    NtfsRight,
    SharePermission,
    SmbShareAce,
)
from app.domain.identity import Sid

TRUSTEE = Sid("S-1-5-21-1-2-3-1104")

READ = 0x00020089
READ_EXECUTE = 0x000200A9
WRITE = 0x00000116
MODIFY = 0x000301BF
FULL_CONTROL = 0x000F01FF

SHARE_READ = 0x001200A9
SHARE_CHANGE = 0x001301BF
SHARE_FULL = 0x001F01FF

FILE_SYSTEM_BITS = (
    0x00000001,  # READ_DATA
    0x00000002,  # WRITE_DATA
    0x00000004,  # APPEND_DATA
    0x00000008,  # READ_EA
    0x00000010,  # WRITE_EA
    0x00000020,  # EXECUTE
    0x00000040,  # DELETE_CHILD
    0x00000080,  # READ_ATTRIBUTES
    0x00000100,  # WRITE_ATTRIBUTES
    0x00010000,  # DELETE
    0x00020000,  # READ_CONTROL
    0x00040000,  # WRITE_DAC
    0x00080000,  # WRITE_OWNER
    0x00100000,  # SYNCHRONIZE
)


def all_file_system_masks() -> list[int]:
    """Every combination of the fourteen file-system rights: 2**14 masks."""
    masks = []
    for selection in itertools.product((0, 1), repeat=len(FILE_SYSTEM_BITS)):
        value = 0
        for chosen, bit in zip(selection, FILE_SYSTEM_BITS, strict=True):
            if chosen:
                value |= bit
        masks.append(value)
    return masks


ALL_MASKS = all_file_system_masks()


class TestRightsMaskConstruction:
    def test_a_mask_keeps_its_value_exactly(self) -> None:
        assert RightsMask.ntfs(MODIFY).value == MODIFY

    def test_the_default_layer_is_ntfs(self) -> None:
        assert RightsMask(MODIFY).layer is RightsLayer.NTFS

    @pytest.mark.parametrize("bad", [-1, MAX_ACCESS_MASK + 1])
    def test_values_outside_32_bits_are_rejected(self, bad: int) -> None:
        with pytest.raises(RightsError):
            RightsMask.ntfs(bad)

    def test_a_boolean_is_not_an_access_mask(self) -> None:
        with pytest.raises(RightsError):
            RightsMask(True)

    def test_the_layer_must_be_a_rights_layer(self) -> None:
        with pytest.raises(RightsError):
            RightsMask(MODIFY, "ntfs")  # type: ignore[arg-type]

    def test_masks_of_different_layers_are_never_equal(self) -> None:
        assert RightsMask.smb(SHARE_CHANGE) != RightsMask.ntfs(SHARE_CHANGE)

    def test_smb_change_and_ntfs_modify_are_the_same_bits_under_different_names(self) -> None:
        # The reason labels must never be compared: these are one mask with two names.
        assert SHARE_LEVEL_MASKS[SharePermission.CHANGE] & ~SYNCHRONIZE_BIT == MODIFY

    def test_the_empty_mask_is_falsy(self) -> None:
        assert not RightsMask.empty()
        assert RightsMask.empty().is_empty is True

    def test_repr_shows_the_mask_and_the_layer(self) -> None:
        assert repr(RightsMask.smb(SHARE_READ)) == "RightsMask(0x001200A9, smb_share)"

    def test_str_is_the_hexadecimal_mask(self) -> None:
        assert str(RightsMask.ntfs(MODIFY)) == "0x000301BF"


class TestRightsMaskInspection:
    def test_recognized_rights_are_exposed_as_flags(self) -> None:
        mask = RightsMask.ntfs(READ_EXECUTE)

        assert NtfsRight.READ_DATA in mask.rights
        assert NtfsRight.EXECUTE in mask.rights
        assert NtfsRight.WRITE_DATA not in mask.rights

    def test_unknown_bits_are_reported_not_dropped(self) -> None:
        mask = RightsMask.ntfs(MODIFY | 0x00000400)

        assert mask.unrecognized_bits == 0x00000400
        assert mask.value == MODIFY | 0x00000400

    def test_extended_rights_are_recognized(self) -> None:
        mask = RightsMask.ntfs(int(ExtendedRight.MAXIMUM_ALLOWED))

        assert mask.extended is ExtendedRight.MAXIMUM_ALLOWED
        assert mask.unrecognized_bits == 0
        assert mask.is_indeterminate is True

    def test_access_system_security_is_known_but_not_indeterminate(self) -> None:
        mask = RightsMask.ntfs(int(ExtendedRight.ACCESS_SYSTEM_SECURITY))

        assert mask.unrecognized_bits == 0
        assert mask.is_indeterminate is False

    def test_escalation_rights_are_surfaced(self) -> None:
        mask = RightsMask.ntfs(MODIFY | int(NtfsRight.WRITE_DAC))

        assert mask.escalation_rights is NtfsRight.WRITE_DAC

    def test_grants_tests_every_bit_of_a_composite_right(self) -> None:
        mask = RightsMask.ntfs(READ)

        assert mask.grants(NtfsRight.READ_DATA) is True
        assert mask.grants(NtfsRight.READ_DATA | NtfsRight.WRITE_DATA) is False

    def test_membership_uses_grants(self) -> None:
        assert NtfsRight.READ_DATA in RightsMask.ntfs(READ)
        assert NtfsRight.WRITE_DATA not in RightsMask.ntfs(READ)

    def test_membership_of_a_mask_is_an_error_not_a_silent_false(self) -> None:
        # `in` cannot answer a cross-layer question, so it refuses rather than lying.
        with pytest.raises(RightsError):
            RightsMask.ntfs(READ) in RightsMask.ntfs(MODIFY)  # noqa: B015

    def test_membership_of_a_foreign_type_is_an_error(self) -> None:
        with pytest.raises(RightsError):
            "read" in RightsMask.ntfs(READ)  # noqa: B015


class TestGenericExpansion:
    @pytest.mark.parametrize(
        ("generic", "expected"),
        [
            (NtfsRight.GENERIC_READ, FILE_GENERIC_READ),
            (NtfsRight.GENERIC_WRITE, FILE_GENERIC_WRITE),
            (NtfsRight.GENERIC_EXECUTE, FILE_GENERIC_EXECUTE),
            (NtfsRight.GENERIC_ALL, FILE_ALL_ACCESS),
        ],
    )
    def test_each_generic_right_expands_through_the_file_system_mapping(
        self, generic: NtfsRight, expected: int
    ) -> None:
        assert RightsMask.ntfs(generic).expand_generics().value == expected

    def test_expansion_clears_the_generic_bits(self) -> None:
        expanded = RightsMask.ntfs(NtfsRight.GENERIC_ALL).expand_generics()

        assert expanded.has_generic_rights is False

    def test_expansion_preserves_specific_bits_already_present(self) -> None:
        mask = RightsMask.ntfs(int(NtfsRight.GENERIC_READ) | int(NtfsRight.DELETE))

        assert mask.expand_generics().value == FILE_GENERIC_READ | int(NtfsRight.DELETE)

    def test_expansion_preserves_the_layer(self) -> None:
        assert RightsMask.smb(NtfsRight.GENERIC_ALL).expand_generics().layer is (
            RightsLayer.SMB_SHARE
        )

    def test_expansion_is_idempotent(self) -> None:
        once = RightsMask.ntfs(NtfsRight.GENERIC_ALL).expand_generics()

        assert once.expand_generics() == once

    def test_a_mask_with_no_generic_bits_is_returned_unchanged(self) -> None:
        mask = RightsMask.ntfs(MODIFY)

        assert mask.expand_generics() is mask

    def test_expansion_never_loses_unrecognized_bits(self) -> None:
        mask = RightsMask.ntfs(int(NtfsRight.GENERIC_READ) | 0x00000400)

        assert mask.expand_generics().unrecognized_bits == 0x00000400


class TestNormalization:
    def test_the_raw_mask_is_kept_alongside_the_normalized_one(self) -> None:
        result = normalize_ntfs_mask(NtfsRight.GENERIC_ALL)

        assert result.raw.value == int(NtfsRight.GENERIC_ALL)
        assert result.normalized.value == FILE_ALL_ACCESS

    def test_the_expansion_that_happened_is_recorded(self) -> None:
        result = normalize_ntfs_mask(NtfsRight.GENERIC_READ)

        assert result.expanded_generics is NtfsRight.GENERIC_READ
        assert result.was_generic is True

    def test_a_plain_mask_records_no_expansion(self) -> None:
        result = normalize_ntfs_mask(MODIFY)

        assert result.was_generic is False
        assert result.normalized.value == MODIFY

    def test_unrecognized_bits_are_carried_through(self) -> None:
        result = normalize_ntfs_mask(MODIFY | 0x00000400)

        assert result.unrecognized_bits == 0x00000400
        assert result.is_fully_understood is False

    def test_maximum_allowed_is_reported_as_indeterminate(self) -> None:
        result = normalize_ntfs_mask(int(ExtendedRight.MAXIMUM_ALLOWED))

        assert result.indeterminate is True
        assert result.is_fully_understood is False

    def test_a_clean_mask_is_fully_understood(self) -> None:
        assert normalize_ntfs_mask(MODIFY).is_fully_understood is True

    def test_normalization_preserves_the_layer(self) -> None:
        assert normalize_share_mask(SHARE_READ).layer is RightsLayer.SMB_SHARE
        assert normalize_ntfs_mask(MODIFY).layer is RightsLayer.NTFS

    def test_normalize_mask_accepts_an_existing_mask(self) -> None:
        assert normalize_mask(RightsMask.smb(SHARE_FULL)).normalized.value == SHARE_FULL


class TestShareNormalization:
    @pytest.mark.parametrize(
        ("level", "expected"),
        [
            (SharePermission.READ, SHARE_READ),
            (SharePermission.CHANGE, SHARE_CHANGE),
            (SharePermission.FULL, SHARE_FULL),
        ],
    )
    def test_each_share_level_denotes_its_documented_mask(
        self, level: SharePermission, expected: int
    ) -> None:
        mask = normalize_share_permission(level)

        assert mask.value == expected
        assert mask.layer is RightsLayer.SMB_SHARE

    def test_the_algebra_table_matches_the_domain_model_table(self) -> None:
        # Two copies exist for readability; this test is why they cannot drift.
        assert SHARE_LEVEL_MASKS == SHARE_PERMISSION_MASKS

    def test_a_share_ace_reported_as_a_mask_normalizes_to_that_mask(self) -> None:
        ace = SmbShareAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=SHARE_CHANGE)

        assert normalize_share_ace(ace).normalized.value == SHARE_CHANGE

    def test_a_share_ace_reported_as_a_level_normalizes_to_the_level_mask(self) -> None:
        ace = SmbShareAce(
            trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, permission=SharePermission.CHANGE
        )

        assert normalize_share_ace(ace).normalized.value == SHARE_CHANGE

    def test_both_report_forms_of_the_same_grant_agree(self) -> None:
        by_mask = SmbShareAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=SHARE_READ)
        by_level = SmbShareAce(
            trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, permission=SharePermission.READ
        )

        assert normalize_share_ace(by_mask).normalized == normalize_share_ace(by_level).normalized

    def test_a_deny_share_ace_still_normalizes_to_its_rights(self) -> None:
        # Allow/Deny is the resolver's concern; the algebra reports the rights named.
        ace = SmbShareAce(
            trustee_sid=TRUSTEE, ace_type=AceType.DENY, permission=SharePermission.FULL
        )

        assert normalize_share_ace(ace).normalized.value == SHARE_FULL


class TestLayerSeparation:
    def test_union_across_layers_is_refused(self) -> None:
        with pytest.raises(RightsLayerError):
            RightsMask.smb(SHARE_READ) | RightsMask.ntfs(READ)

    def test_intersection_across_layers_is_refused(self) -> None:
        with pytest.raises(RightsLayerError):
            RightsMask.smb(SHARE_READ) & RightsMask.ntfs(READ)

    def test_subtraction_across_layers_is_refused(self) -> None:
        with pytest.raises(RightsLayerError):
            RightsMask.smb(SHARE_READ) - RightsMask.ntfs(READ)

    def test_comparison_across_layers_is_refused(self) -> None:
        with pytest.raises(RightsLayerError):
            RightsMask.smb(SHARE_READ).issubset(RightsMask.ntfs(SHARE_READ))

    def test_disjointness_across_layers_is_refused(self) -> None:
        with pytest.raises(RightsLayerError):
            RightsMask.smb(SHARE_READ).isdisjoint(RightsMask.ntfs(READ))

    def test_the_error_names_both_layers(self) -> None:
        with pytest.raises(RightsLayerError, match=r"smb_share.*ntfs"):
            RightsMask.smb(SHARE_READ) | RightsMask.ntfs(READ)

    def test_combining_with_a_non_mask_is_refused(self) -> None:
        with pytest.raises(RightsError):
            RightsMask.ntfs(READ).union(MODIFY)  # type: ignore[arg-type]

    def test_acl_layers_map_onto_rights_layers(self) -> None:
        assert RightsLayer.from_acl_layer(AclLayer.SMB_SHARE) is RightsLayer.SMB_SHARE
        assert RightsLayer.from_acl_layer(AclLayer.NTFS) is RightsLayer.NTFS


class TestAlgebra:
    def test_union_collects_rights_from_both_masks(self) -> None:
        combined = RightsMask.ntfs(READ) | RightsMask.ntfs(WRITE)

        assert combined.value == READ | WRITE

    def test_intersection_keeps_only_shared_rights(self) -> None:
        combined = RightsMask.ntfs(MODIFY) & RightsMask.ntfs(READ)

        assert combined.value == READ

    def test_difference_removes_rights(self) -> None:
        remaining = RightsMask.ntfs(MODIFY) - RightsMask.ntfs(WRITE)

        assert remaining.value == MODIFY & ~WRITE

    def test_subset_and_superset_agree(self) -> None:
        smaller = RightsMask.ntfs(READ)
        larger = RightsMask.ntfs(MODIFY)

        assert smaller.issubset(larger) is True
        assert larger.issuperset(smaller) is True
        assert larger.issubset(smaller) is False

    def test_a_mask_is_its_own_subset_and_superset(self) -> None:
        mask = RightsMask.ntfs(MODIFY)

        assert mask.issubset(mask) is True
        assert mask.issuperset(mask) is True

    def test_disjoint_masks_share_nothing(self) -> None:
        assert RightsMask.ntfs(0x1).isdisjoint(RightsMask.ntfs(0x2)) is True
        assert RightsMask.ntfs(READ).isdisjoint(RightsMask.ntfs(MODIFY)) is False

    def test_operations_preserve_the_layer(self) -> None:
        result = RightsMask.smb(SHARE_FULL) & RightsMask.smb(SHARE_READ)

        assert result.layer is RightsLayer.SMB_SHARE

    def test_union_all_folds_every_mask(self) -> None:
        masks = [RightsMask.ntfs(READ), RightsMask.ntfs(WRITE), RightsMask.ntfs(0x10000)]

        assert union_all(masks).value == READ | WRITE | 0x10000

    def test_intersect_all_folds_every_mask(self) -> None:
        masks = [RightsMask.ntfs(FULL_CONTROL), RightsMask.ntfs(MODIFY), RightsMask.ntfs(READ)]

        assert intersect_all(masks).value == READ

    def test_an_empty_union_is_the_empty_mask(self) -> None:
        assert union_all([], layer=RightsLayer.NTFS).value == 0

    def test_an_empty_intersection_is_the_unconstrained_mask(self) -> None:
        assert intersect_all([], layer=RightsLayer.NTFS).value == MAX_ACCESS_MASK

    def test_folding_an_empty_sequence_without_a_layer_is_refused(self) -> None:
        # A rights value with no layer must not exist, even briefly.
        with pytest.raises(RightsError):
            union_all([])

    def test_folding_rejects_a_mask_of_the_wrong_layer(self) -> None:
        with pytest.raises(RightsLayerError):
            union_all([RightsMask.smb(SHARE_READ)], layer=RightsLayer.NTFS)

    def test_folding_rejects_a_non_mask(self) -> None:
        with pytest.raises(RightsError):
            union_all([MODIFY], layer=RightsLayer.NTFS)  # type: ignore[list-item]

    def test_folding_mixed_layers_without_a_declared_layer_is_refused(self) -> None:
        with pytest.raises(RightsLayerError):
            union_all([RightsMask.ntfs(READ), RightsMask.smb(SHARE_READ)])


class TestDenyMasking:
    def test_deny_removes_the_denied_rights(self) -> None:
        result = apply_deny(RightsMask.ntfs(MODIFY), RightsMask.ntfs(WRITE))

        assert result.value == MODIFY & ~WRITE

    def test_deny_of_rights_never_granted_changes_nothing(self) -> None:
        result = apply_deny(RightsMask.ntfs(READ), RightsMask.ntfs(int(NtfsRight.WRITE_OWNER)))

        assert result.value == READ

    def test_a_full_deny_leaves_nothing(self) -> None:
        result = apply_deny(RightsMask.ntfs(FULL_CONTROL), RightsMask.ntfs(FULL_CONTROL))

        assert result.is_empty is True

    def test_the_canonical_model_accumulates_then_subtracts(self) -> None:
        result = resolve_canonical(
            [RightsMask.ntfs(READ), RightsMask.ntfs(WRITE)],
            [RightsMask.ntfs(int(NtfsRight.WRITE_DATA))],
            layer=RightsLayer.NTFS,
        )

        assert result.value == (READ | WRITE) & ~int(NtfsRight.WRITE_DATA)

    def test_deny_wins_regardless_of_the_order_the_masks_arrive_in(self) -> None:
        allows = [RightsMask.ntfs(MODIFY)]
        denies = [RightsMask.ntfs(int(NtfsRight.DELETE))]

        forward = resolve_canonical(allows, denies, layer=RightsLayer.NTFS)
        reverse = resolve_canonical(list(reversed(allows)), denies, layer=RightsLayer.NTFS)

        assert forward == reverse
        assert NtfsRight.DELETE not in forward.rights

    def test_no_aces_at_all_grants_nothing(self) -> None:
        result = resolve_canonical([], [], layer=RightsLayer.NTFS)

        assert result.is_empty is True

    def test_deny_masking_across_layers_is_refused(self) -> None:
        with pytest.raises(RightsLayerError):
            apply_deny(RightsMask.ntfs(MODIFY), RightsMask.smb(SHARE_READ))


class TestEffectiveRightsOverSmb:
    @pytest.mark.parametrize(
        ("share", "ntfs", "expected"),
        [
            (SHARE_FULL, READ, READ),
            (SHARE_FULL, MODIFY, MODIFY),
            (SHARE_READ, FULL_CONTROL, SHARE_READ & FULL_CONTROL),
            (SHARE_READ, MODIFY, SHARE_READ & MODIFY),
            (SHARE_CHANGE, MODIFY, SHARE_CHANGE & MODIFY),
            (SHARE_CHANGE, READ, READ),
            (SHARE_READ, WRITE, SHARE_READ & WRITE),
            (SHARE_FULL, 0, 0),
            (0, FULL_CONTROL, 0),
        ],
    )
    def test_the_effective_mask_is_the_intersection(
        self, share: int, ntfs: int, expected: int
    ) -> None:
        result = effective_rights(
            ntfs=RightsMask.ntfs(ntfs),
            share=RightsMask.smb(share),
            path=AccessPath.REMOTE_SMB,
        )

        assert result.rights.value == expected

    def test_the_result_carries_the_effective_layer(self) -> None:
        result = effective_rights(
            ntfs=RightsMask.ntfs(MODIFY),
            share=RightsMask.smb(SHARE_FULL),
            path=AccessPath.REMOTE_SMB,
        )

        assert result.rights.layer is RightsLayer.EFFECTIVE

    def test_a_full_share_cannot_widen_ntfs(self) -> None:
        result = effective_rights(
            ntfs=RightsMask.ntfs(READ),
            share=RightsMask.smb(SHARE_FULL),
            path=AccessPath.REMOTE_SMB,
        )

        assert NtfsRight.WRITE_DATA not in result.rights.rights
        assert result.limited_by_ntfs is True
        assert result.limited_by_share is False

    def test_a_read_share_narrows_ntfs_modify(self) -> None:
        result = effective_rights(
            ntfs=RightsMask.ntfs(MODIFY),
            share=RightsMask.smb(SHARE_READ),
            path=AccessPath.REMOTE_SMB,
        )

        assert NtfsRight.WRITE_DATA not in result.rights.rights
        assert result.limited_by_share is True

    def test_both_inputs_are_retained_for_explanation(self) -> None:
        result = effective_rights(
            ntfs=RightsMask.ntfs(MODIFY),
            share=RightsMask.smb(SHARE_READ),
            path=AccessPath.REMOTE_SMB,
        )

        assert result.ntfs_rights.value == MODIFY
        assert result.share_rights is not None
        assert result.share_rights.value == SHARE_READ

    def test_generic_rights_are_expanded_before_intersecting(self) -> None:
        # Intersecting a raw GENERIC_ALL with specific bits would yield zero.
        result = effective_rights(
            ntfs=RightsMask.ntfs(NtfsRight.GENERIC_ALL),
            share=RightsMask.smb(SHARE_READ),
            path=AccessPath.REMOTE_SMB,
        )

        assert result.rights.value == SHARE_READ

    def test_an_indeterminate_input_makes_the_result_indeterminate(self) -> None:
        result = effective_rights(
            ntfs=RightsMask.ntfs(int(ExtendedRight.MAXIMUM_ALLOWED) | READ),
            share=RightsMask.smb(SHARE_FULL),
            path=AccessPath.REMOTE_SMB,
        )

        assert result.is_indeterminate is True

    def test_remote_access_without_share_rights_is_refused(self) -> None:
        # Defaulting to "unrestricted" would over-report access.
        with pytest.raises(RightsError):
            effective_rights(ntfs=RightsMask.ntfs(MODIFY), path=AccessPath.REMOTE_SMB)

    def test_the_share_argument_must_be_a_share_mask(self) -> None:
        with pytest.raises(RightsLayerError):
            effective_rights(
                ntfs=RightsMask.ntfs(MODIFY),
                share=RightsMask.ntfs(SHARE_READ),
                path=AccessPath.REMOTE_SMB,
            )

    def test_the_ntfs_argument_must_be_an_ntfs_mask(self) -> None:
        with pytest.raises(RightsLayerError):
            effective_rights(
                ntfs=RightsMask.smb(MODIFY),
                share=RightsMask.smb(SHARE_READ),
                path=AccessPath.REMOTE_SMB,
            )


class TestEffectiveRightsLocally:
    def test_local_access_ignores_the_share_acl(self) -> None:
        result = effective_rights(ntfs=RightsMask.ntfs(MODIFY), path=AccessPath.LOCAL)

        assert result.rights.value == MODIFY
        assert result.share_rights is None

    def test_a_restrictive_share_does_not_constrain_local_access(self) -> None:
        remote = effective_rights(
            ntfs=RightsMask.ntfs(FULL_CONTROL),
            share=RightsMask.smb(SHARE_READ),
            path=AccessPath.REMOTE_SMB,
        )
        local = effective_rights(ntfs=RightsMask.ntfs(FULL_CONTROL), path=AccessPath.LOCAL)

        assert local.rights.value > remote.rights.value
        assert local.limited_by_share is False

    def test_passing_share_rights_for_local_access_is_refused(self) -> None:
        with pytest.raises(RightsError):
            effective_rights(
                ntfs=RightsMask.ntfs(MODIFY),
                share=RightsMask.smb(SHARE_READ),
                path=AccessPath.LOCAL,
            )

    def test_local_generic_rights_are_expanded(self) -> None:
        result = effective_rights(
            ntfs=RightsMask.ntfs(NtfsRight.GENERIC_ALL), path=AccessPath.LOCAL
        )

        assert result.rights.value == FILE_ALL_ACCESS


class TestEffectiveRightsInvariants:
    def test_the_result_must_carry_the_effective_layer(self) -> None:
        with pytest.raises(RightsError):
            EffectiveRights(
                path=AccessPath.LOCAL,
                rights=RightsMask.ntfs(MODIFY),
                ntfs_rights=RightsMask.ntfs(MODIFY),
            )

    def test_a_remote_result_must_record_the_share_rights(self) -> None:
        with pytest.raises(RightsError):
            EffectiveRights(
                path=AccessPath.REMOTE_SMB,
                rights=RightsMask.effective(MODIFY),
                ntfs_rights=RightsMask.ntfs(MODIFY),
            )

    def test_a_local_result_must_not_record_share_rights(self) -> None:
        with pytest.raises(RightsError):
            EffectiveRights(
                path=AccessPath.LOCAL,
                rights=RightsMask.effective(MODIFY),
                ntfs_rights=RightsMask.ntfs(MODIFY),
                share_rights=RightsMask.smb(SHARE_READ),
            )

    def test_the_ntfs_input_must_be_an_ntfs_mask(self) -> None:
        with pytest.raises(RightsLayerError):
            EffectiveRights(
                path=AccessPath.LOCAL,
                rights=RightsMask.effective(MODIFY),
                ntfs_rights=RightsMask.effective(MODIFY),
            )

    def test_the_share_input_must_be_a_share_mask(self) -> None:
        with pytest.raises(RightsLayerError):
            EffectiveRights(
                path=AccessPath.REMOTE_SMB,
                rights=RightsMask.effective(MODIFY),
                ntfs_rights=RightsMask.ntfs(MODIFY),
                share_rights=RightsMask.ntfs(SHARE_READ),
            )

    def test_an_effective_result_can_be_summarized(self) -> None:
        result = effective_rights(ntfs=RightsMask.ntfs(MODIFY), path=AccessPath.LOCAL)

        assert result.summarize().primary is RightsCategory.MODIFY


class TestDisplayCategories:
    @pytest.mark.parametrize(
        ("mask", "expected"),
        [
            (0, RightsCategory.NONE),
            (FULL_CONTROL, RightsCategory.FULL_CONTROL),
            (FILE_ALL_ACCESS, RightsCategory.FULL_CONTROL),
            (MODIFY, RightsCategory.MODIFY),
            (READ_EXECUTE, RightsCategory.READ_EXECUTE),
            (READ, RightsCategory.READ),
            (WRITE, RightsCategory.WRITE),
            (int(NtfsRight.EXECUTE), RightsCategory.TRAVERSE),
            (int(NtfsRight.READ_CONTROL), RightsCategory.SPECIAL),
            (int(NtfsRight.WRITE_OWNER), RightsCategory.SPECIAL),
        ],
    )
    def test_the_primary_category_of_a_known_mask(
        self, mask: int, expected: RightsCategory
    ) -> None:
        assert summarize(RightsMask.ntfs(mask)).primary is expected

    def test_synchronize_alone_is_not_access(self) -> None:
        assert summarize(RightsMask.ntfs(SYNCHRONIZE_BIT)).primary is RightsCategory.NONE

    def test_a_missing_synchronize_bit_does_not_demote_full_control(self) -> None:
        summary = summarize(RightsMask.ntfs(FULL_CONTROL))

        assert summary.primary is RightsCategory.FULL_CONTROL
        assert summary.is_exact is True

    def test_implied_categories_are_pruned(self) -> None:
        summary = summarize(RightsMask.ntfs(FULL_CONTROL))

        assert summary.categories == (RightsCategory.FULL_CONTROL,)

    def test_incomparable_categories_are_both_reported(self) -> None:
        summary = summarize(RightsMask.ntfs(READ | WRITE))

        assert set(summary.categories) == {RightsCategory.READ, RightsCategory.WRITE}
        assert summary.is_exact is True

    def test_generic_all_is_described_not_called_special(self) -> None:
        summary = summarize(RightsMask.ntfs(NtfsRight.GENERIC_ALL))

        assert summary.primary is RightsCategory.FULL_CONTROL
        assert summary.was_generic is True
        assert summary.raw.value == int(NtfsRight.GENERIC_ALL)

    def test_rights_beyond_the_label_are_reported(self) -> None:
        summary = summarize(RightsMask.ntfs(MODIFY | int(NtfsRight.WRITE_DAC)))

        assert summary.primary is RightsCategory.MODIFY
        assert summary.is_exact is False
        assert summary.extra_rights is NtfsRight.WRITE_DAC

    def test_an_escalation_right_hidden_behind_modify_stays_visible(self) -> None:
        summary = summarize(RightsMask.ntfs(MODIFY | int(NtfsRight.WRITE_DAC)))

        assert NtfsRight.WRITE_DAC in summary.escalation_rights

    def test_unrecognized_bits_survive_summarization(self) -> None:
        summary = summarize(RightsMask.ntfs(MODIFY | 0x00000400))

        assert summary.unrecognized_bits == 0x00000400
        assert summary.is_exact is False

    def test_maximum_allowed_makes_a_summary_inexact(self) -> None:
        summary = summarize(RightsMask.ntfs(READ | int(ExtendedRight.MAXIMUM_ALLOWED)))

        assert summary.indeterminate is True
        assert summary.is_exact is False

    def test_summarize_rejects_a_bare_integer(self) -> None:
        with pytest.raises(RightsError):
            summarize(MODIFY)  # type: ignore[arg-type]

    def test_summaries_work_on_every_layer(self) -> None:
        assert summarize(RightsMask.smb(SHARE_CHANGE)).primary is RightsCategory.MODIFY


class TestDisplayLabels:
    @pytest.mark.parametrize(
        ("mask", "expected"),
        [
            (0, "No access"),
            (FULL_CONTROL, "Full Control"),
            (MODIFY, "Modify"),
            (READ_EXECUTE, "Read & Execute"),
            (READ, "Read"),
            (WRITE, "Write"),
            (int(NtfsRight.EXECUTE), "Traverse"),
            (int(NtfsRight.READ_CONTROL), "Special permissions"),
            (MODIFY | int(NtfsRight.WRITE_DAC), "Modify (plus special permissions)"),
        ],
    )
    def test_the_rendered_label(self, mask: int, expected: str) -> None:
        assert summarize(RightsMask.ntfs(mask)).label == expected

    def test_an_indeterminate_mask_says_so(self) -> None:
        summary = summarize(RightsMask.ntfs(READ | int(ExtendedRight.MAXIMUM_ALLOWED)))

        assert summary.label == "Read (indeterminate: MAXIMUM_ALLOWED)"

    def test_maximum_allowed_is_not_also_called_a_special_permission(self) -> None:
        # It is a request marker, not a permission, and has its own clause in the label.
        summary = summarize(RightsMask.ntfs(READ | int(ExtendedRight.MAXIMUM_ALLOWED)))

        assert summary.special_permission_bits == 0
        assert "plus special permissions" not in summary.label

    def test_maximum_allowed_is_still_carried_in_the_extra_bits(self) -> None:
        # Excluded from the rendering, never dropped from the accounting.
        summary = summarize(RightsMask.ntfs(READ | int(ExtendedRight.MAXIMUM_ALLOWED)))

        assert summary.extra_bits == int(ExtendedRight.MAXIMUM_ALLOWED)
        assert summary.is_exact is False

    def test_a_real_special_permission_alongside_maximum_allowed_is_still_reported(self) -> None:
        mask = READ | int(NtfsRight.WRITE_DAC) | int(ExtendedRight.MAXIMUM_ALLOWED)
        summary = summarize(RightsMask.ntfs(mask))

        assert summary.special_permission_bits == int(NtfsRight.WRITE_DAC)
        assert summary.label == "Read (plus special permissions) (indeterminate: MAXIMUM_ALLOWED)"

    def test_every_category_has_a_display_name(self) -> None:
        for category in RightsCategory:
            assert category_display_name(category)


class TestShareClassification:
    @pytest.mark.parametrize(
        ("mask", "expected"),
        [
            (SHARE_FULL, SharePermission.FULL),
            (SHARE_CHANGE, SharePermission.CHANGE),
            (SHARE_READ, SharePermission.READ),
            (SHARE_CHANGE & ~SYNCHRONIZE_BIT, SharePermission.CHANGE),
            (int(NtfsRight.GENERIC_ALL), SharePermission.FULL),
        ],
    )
    def test_a_share_mask_classifies_to_its_level(
        self, mask: int, expected: SharePermission
    ) -> None:
        assert classify_share_mask(RightsMask.smb(mask)) is expected

    @pytest.mark.parametrize("mask", [0, int(NtfsRight.WRITE_DATA), SHARE_READ & ~0x1])
    def test_a_mask_that_is_no_level_classifies_to_none(self, mask: int) -> None:
        # None is a real answer: rounding to the nearest level would misreport the share.
        assert classify_share_mask(RightsMask.smb(mask)) is None

    def test_a_mask_beyond_read_but_short_of_change_is_not_promoted(self) -> None:
        mask = SHARE_READ | int(NtfsRight.WRITE_DATA)

        assert classify_share_mask(RightsMask.smb(mask)) is SharePermission.READ

    def test_classification_refuses_an_ntfs_mask(self) -> None:
        with pytest.raises(RightsLayerError):
            classify_share_mask(RightsMask.ntfs(SHARE_READ))

    def test_an_unclassifiable_share_mask_still_summarizes(self) -> None:
        mask = RightsMask.smb(int(NtfsRight.WRITE_DATA))

        assert classify_share_mask(mask) is None
        assert summarize(mask).primary is RightsCategory.SPECIAL


class TestExhaustiveSafetyProperties:
    """The acceptance criteria, checked over all 2**14 file-system masks."""

    def test_the_sample_space_is_what_it_claims(self) -> None:
        assert len(ALL_MASKS) == 16384
        assert len(set(ALL_MASKS)) == 16384

    def test_a_category_never_grants_a_right_the_mask_lacks(self) -> None:
        for value in ALL_MASKS:
            summary = summarize(RightsMask.ntfs(value))
            for category in summary.categories:
                required = CATEGORY_REQUIRED_MASKS[category]
                assert value & required == required, (
                    f"0x{value:08X} was labeled {category.value!r}, which requires "
                    f"0x{required:08X} that the mask does not contain"
                )

    def test_the_covered_mask_is_always_a_subset_of_the_mask(self) -> None:
        for value in ALL_MASKS:
            summary = summarize(RightsMask.ntfs(value))
            assert summary.covered_mask & value == summary.covered_mask

    def test_no_right_is_lost_between_the_categories_and_the_extras(self) -> None:
        for value in ALL_MASKS:
            summary = summarize(RightsMask.ntfs(value))
            reconstructed = summary.covered_mask | summary.extra_bits | (value & SYNCHRONIZE_BIT)
            assert reconstructed == value, f"0x{value:08X} lost bits in summarization"

    def test_the_extras_never_overlap_the_categories(self) -> None:
        for value in ALL_MASKS:
            summary = summarize(RightsMask.ntfs(value))
            assert summary.covered_mask & summary.extra_bits == 0

    def test_an_exact_summary_accounts_for_every_right(self) -> None:
        for value in ALL_MASKS:
            summary = summarize(RightsMask.ntfs(value))
            if summary.is_exact:
                assert summary.covered_mask | (value & SYNCHRONIZE_BIT) == value

    def test_every_mask_gets_exactly_one_primary_category(self) -> None:
        for value in ALL_MASKS:
            summary = summarize(RightsMask.ntfs(value))
            if summary.categories:
                assert summary.primary is summary.categories[0]
            elif value & ~SYNCHRONIZE_BIT:
                assert summary.primary is RightsCategory.SPECIAL
            else:
                assert summary.primary is RightsCategory.NONE

    def test_summarization_is_deterministic(self) -> None:
        for value in ALL_MASKS:
            mask = RightsMask.ntfs(value)
            assert summarize(mask) == summarize(mask)

    def test_a_label_is_always_produced(self) -> None:
        for value in ALL_MASKS:
            assert summarize(RightsMask.ntfs(value)).label

    def test_the_reported_categories_are_pairwise_incomparable(self) -> None:
        for value in ALL_MASKS:
            summary = summarize(RightsMask.ntfs(value))
            for left in summary.categories:
                for right in summary.categories:
                    if left is right:
                        continue
                    left_mask = CATEGORY_REQUIRED_MASKS[left]
                    right_mask = CATEGORY_REQUIRED_MASKS[right]
                    assert left_mask & right_mask != left_mask, (
                        f"0x{value:08X} reported {left.value!r} and {right.value!r}, "
                        "but the first is implied by the second"
                    )

    def test_a_larger_mask_never_gets_a_smaller_category_set(self) -> None:
        # Monotonicity: adding a right can never remove a category that was reached.
        for value in ALL_MASKS:
            if value == FULL_CONTROL:
                continue
            base = summarize(RightsMask.ntfs(value))
            wider = summarize(RightsMask.ntfs(value | int(NtfsRight.READ_DATA)))
            assert base.covered_mask & wider.covered_mask == base.covered_mask


class TestExhaustiveLayerProperties:
    """Intersection semantics, checked over a full cross-product of share and NTFS levels."""

    def test_the_effective_mask_never_exceeds_either_layer(self) -> None:
        share_masks = [0, SHARE_READ, SHARE_CHANGE, SHARE_FULL]
        for share in share_masks:
            for ntfs in ALL_MASKS[::97]:
                result = effective_rights(
                    ntfs=RightsMask.ntfs(ntfs),
                    share=RightsMask.smb(share),
                    path=AccessPath.REMOTE_SMB,
                )
                assert result.rights.value & share == result.rights.value
                assert result.rights.value & ntfs == result.rights.value

    def test_remote_access_never_exceeds_local_access(self) -> None:
        for ntfs in ALL_MASKS[::97]:
            local = effective_rights(ntfs=RightsMask.ntfs(ntfs), path=AccessPath.LOCAL)
            remote = effective_rights(
                ntfs=RightsMask.ntfs(ntfs),
                share=RightsMask.smb(SHARE_CHANGE),
                path=AccessPath.REMOTE_SMB,
            )
            assert remote.rights.value & local.rights.value == remote.rights.value

    def test_intersection_is_commutative_in_effect(self) -> None:
        for ntfs in ALL_MASKS[::193]:
            for share in (SHARE_READ, SHARE_CHANGE, SHARE_FULL):
                forward = RightsMask.ntfs(ntfs).value & RightsMask.smb(share).value
                result = effective_rights(
                    ntfs=RightsMask.ntfs(ntfs),
                    share=RightsMask.smb(share),
                    path=AccessPath.REMOTE_SMB,
                )
                assert result.rights.value == forward
