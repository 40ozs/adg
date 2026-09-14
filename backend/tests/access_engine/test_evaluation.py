"""The access check itself: one DACL, one token, in the order the descriptor stores it.

Every test here is a claim about what Windows does, and the ones worth reading twice are
the four where the obvious implementation is wrong:

* an **Allow ahead of a Deny wins**, because Windows walks the ACL in order and does not
  re-sort it;
* an ``INHERIT_ONLY`` entry **grants nothing** on the object holding it;
* a **NULL DACL grants everything** while an **empty DACL grants nothing**;
* the **owner holds ``WRITE_DAC``** whatever the DACL says, so an explicit Deny on the
  owner does not stop them rewriting it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from app.access_engine import (
    MAX_ACL_ENTRIES,
    OWNER_IMPLICIT_RIGHTS,
    AccessCondition,
    AclEntry,
    AclEvaluation,
    DaclFacts,
    OrderViolationKind,
    RightsLayer,
    RightsMask,
    SubjectToken,
    TokenAssumption,
    canonical_order_violations,
    evaluate_acl,
    ntfs_entry,
    share_entry,
)
from app.domain import AceFlag, AceType, DomainValidationError, NtfsRight, SharePermission
from tests.access_engine.support import (
    ALICE,
    AUTHENTICATED_USERS,
    BUILTIN_ADMINS,
    CREATOR_OWNER,
    EVERYONE,
    FINANCE_RW,
    FINANCE_TEAM,
    FS01_ADMINS,
    FULL_CONTROL,
    MAXIMUM_ALLOWED,
    MODIFY,
    ORPHAN,
    OWNER_RIGHTS,
    READ_CONTROL,
    READ_EXECUTE,
    WRITE_DAC,
    allow,
    deny,
    group_sid,
    share_allow,
    token,
)

INHERIT_ONLY = int(AceFlag.INHERIT_ONLY)
CONTAINER_INHERIT = int(AceFlag.CONTAINER_INHERIT)


def ntfs(
    entries: Sequence[AclEntry],
    subject_token: SubjectToken | None = None,
    **facts: Any,
) -> AclEvaluation:
    """Evaluate a file-system DACL for a token that is in Finance-RW by default."""
    return evaluate_acl(
        entries,
        subject_token or token(groups=[FINANCE_RW]),
        layer=RightsLayer.NTFS,
        facts=DaclFacts(**facts),
    )


class TestMatchingTrustees:
    def test_an_ace_naming_the_subject_grants(self):
        result = ntfs([allow(ALICE, MODIFY)])

        assert result.rights == RightsMask.ntfs(MODIFY)
        assert result.granted_by[0].entry.trustee_key == ALICE

    def test_an_ace_naming_a_group_the_subject_is_in_grants(self):
        result = ntfs([allow(FINANCE_RW, MODIFY)])

        assert result.rights == RightsMask.ntfs(MODIFY)
        assert result.granted_by[0].via_group

    def test_an_ace_naming_a_group_the_subject_is_not_in_grants_nothing(self):
        result = ntfs([allow(FINANCE_TEAM, MODIFY)])

        assert result.rights.is_empty
        assert result.granted_by == ()

    def test_being_listed_on_the_acl_is_not_access(self):
        """The sentence the whole engine exists to make true."""
        result = ntfs([allow(ORPHAN, FULL_CONTROL)])

        assert result.entries_supplied == 1
        assert result.entries_evaluated == 0
        assert result.rights.is_empty

    def test_a_builtin_sid_matches_only_its_own_host(self):
        on_fs01 = token(groups=[group_sid(FS01_ADMINS)])
        entry = ntfs_entry(
            trustee_key="fs02|S-1-5-32-544",
            trustee_sid=BUILTIN_ADMINS,
            ace_type=AceType.ALLOW,
            access_mask=FULL_CONTROL,
        )

        assert ntfs([entry], on_fs01).rights.is_empty

    def test_several_allows_accumulate(self):
        result = ntfs([allow(ALICE, READ_EXECUTE), allow(FINANCE_RW, int(NtfsRight.WRITE_DATA))])

        assert result.rights == RightsMask.ntfs(READ_EXECUTE | int(NtfsRight.WRITE_DATA))
        assert len(result.granted_by) == 2


class TestOrderIsLoadBearing:
    def test_a_deny_before_an_allow_removes_the_rights(self):
        result = ntfs([deny(FINANCE_RW, MODIFY), allow(FINANCE_RW, MODIFY)])

        assert result.rights.is_empty
        assert result.denied_by[0].contributed == RightsMask.ntfs(MODIFY)

    def test_an_allow_before_a_deny_wins_because_windows_does_not_re_sort(self):
        result = ntfs([allow(FINANCE_RW, MODIFY), deny(FINANCE_RW, MODIFY)])

        assert result.rights == RightsMask.ntfs(MODIFY)

    def test_the_canonical_model_is_reported_beside_the_faithful_one(self):
        result = ntfs([allow(FINANCE_RW, MODIFY), deny(FINANCE_RW, MODIFY)])

        assert result.canonical_rights == RightsMask.ntfs(0)
        assert result.order_dependent

    def test_a_canonical_acl_agrees_with_the_canonical_model(self):
        result = ntfs([deny(FINANCE_RW, int(NtfsRight.DELETE)), allow(FINANCE_RW, MODIFY)])

        assert result.canonical_rights == result.rights
        assert not result.order_dependent

    def test_an_order_dependent_result_is_reported_per_subject(self):
        """It is a fact about one principal's evaluation, not about the ACL alone."""
        result = ntfs([allow(FINANCE_RW, MODIFY), deny(FINANCE_RW, MODIFY)])

        assert AccessCondition.ORDER_DEPENDENT_RESULT in result.conditions()

        uninvolved = ntfs(
            [allow(FINANCE_TEAM, MODIFY), deny(FINANCE_TEAM, MODIFY)],
        )
        assert AccessCondition.ORDER_DEPENDENT_RESULT not in uninvolved.conditions()

    def test_a_superseded_allow_is_reported_rather_than_dropped(self):
        result = ntfs([deny(FINANCE_RW, MODIFY), allow(FINANCE_RW, MODIFY)])

        assert [item.entry.ace_type for item in result.superseded] == [AceType.ALLOW]
        assert result.superseded[0].contributed.is_empty

    def test_a_partial_deny_leaves_the_rest_grantable(self):
        result = ntfs([deny(FINANCE_RW, int(NtfsRight.WRITE_DATA)), allow(FINANCE_RW, MODIFY)])

        assert not result.rights.grants(NtfsRight.WRITE_DATA)
        assert result.rights.grants(NtfsRight.READ_DATA)


class TestInheritOnly:
    def test_an_inherit_only_entry_grants_nothing_here(self):
        result = ntfs([allow(FINANCE_RW, FULL_CONTROL, flags=INHERIT_ONLY | CONTAINER_INHERIT)])

        assert result.rights.is_empty
        assert result.entries_evaluated == 0

    def test_an_inherit_only_deny_removes_nothing_here(self):
        result = ntfs(
            [
                allow(FINANCE_RW, MODIFY),
                deny(FINANCE_RW, MODIFY, flags=INHERIT_ONLY | CONTAINER_INHERIT),
            ]
        )

        assert result.rights == RightsMask.ntfs(MODIFY)

    def test_an_inheritable_entry_that_also_applies_here_still_grants(self):
        result = ntfs([allow(FINANCE_RW, MODIFY, flags=CONTAINER_INHERIT)])

        assert result.rights == RightsMask.ntfs(MODIFY)


class TestTheDescriptorItself:
    def test_a_null_dacl_grants_everyone_everything(self):
        result = ntfs([], dacl_present=False)

        assert result.rights == RightsMask.ntfs(FULL_CONTROL)
        assert AccessCondition.NULL_DACL in result.conditions()

    def test_a_null_dacl_grants_a_principal_no_ace_names(self):
        stranger = token("S-1-5-21-1-2-3-9999")

        result = ntfs([], stranger, dacl_present=False)

        assert result.rights == RightsMask.ntfs(FULL_CONTROL)

    def test_an_empty_dacl_grants_nobody_anything(self):
        result = ntfs([])

        assert result.rights.is_empty
        assert AccessCondition.EMPTY_DACL in result.conditions()

    def test_entries_stored_against_a_null_dacl_are_reported_not_evaluated(self):
        """Rows outlive the descriptor they described; the disagreement is the finding."""
        result = ntfs([allow(FINANCE_RW, FULL_CONTROL)], dacl_present=False)

        assert result.granted_by == ()
        assert AccessCondition.ACE_COUNT_MISMATCH in result.conditions()

    def test_a_protected_dacl_is_reported(self):
        result = ntfs([allow(FINANCE_RW, MODIFY)], dacl_protected=True)

        assert AccessCondition.PROTECTED_DACL in result.conditions()

    def test_a_declared_count_that_disagrees_with_the_entries_is_reported(self):
        result = ntfs([allow(FINANCE_RW, MODIFY)], declared_ace_count=4)

        mismatch = next(
            finding
            for finding in result.findings
            if finding.condition is AccessCondition.ACE_COUNT_MISMATCH
        )
        assert mismatch.detail == {"declared": 4, "held": 1}

    def test_a_matching_declared_count_is_not_reported(self):
        result = ntfs([allow(FINANCE_RW, MODIFY)], declared_ace_count=1)

        assert AccessCondition.ACE_COUNT_MISMATCH not in result.conditions()


class TestTheOwner:
    def test_the_owner_holds_read_control_and_write_dac_with_no_ace(self):
        result = ntfs([], owner_sid=ALICE)

        assert result.rights.grants(NtfsRight.WRITE_DAC)
        assert result.owner_rights == OWNER_IMPLICIT_RIGHTS
        assert AccessCondition.OWNER_IMPLICIT_RIGHTS in result.conditions()

    def test_an_explicit_deny_does_not_take_the_owners_control_rights_away(self):
        """Windows grants them before it reads the DACL. This is why ownership is a risk."""
        result = ntfs([deny(ALICE, FULL_CONTROL)], owner_sid=ALICE)

        assert result.rights.grants(NtfsRight.WRITE_DAC)
        assert result.rights.grants(NtfsRight.READ_CONTROL)

    def test_owning_through_a_group_still_grants_the_implicit_rights(self):
        result = ntfs([], owner_sid=FINANCE_RW)

        assert result.rights == RightsMask.ntfs(OWNER_IMPLICIT_RIGHTS.value)

    def test_a_non_owner_gets_nothing_implicit(self):
        result = ntfs([], owner_sid=FINANCE_TEAM)

        assert result.rights.is_empty
        assert result.owner_rights is None

    def test_a_builtin_owner_is_matched_on_its_host_scoped_key(self):
        on_fs01 = token(groups=[group_sid(FS01_ADMINS)])

        scoped = evaluate_acl(
            [],
            on_fs01,
            layer=RightsLayer.NTFS,
            facts=DaclFacts(owner_sid=BUILTIN_ADMINS, owner_key=FS01_ADMINS),
        )
        unscoped = evaluate_acl(
            [],
            on_fs01,
            layer=RightsLayer.NTFS,
            facts=DaclFacts(owner_sid=BUILTIN_ADMINS, owner_key="fs02|S-1-5-32-544"),
        )

        assert scoped.owner_rights == OWNER_IMPLICIT_RIGHTS
        assert unscoped.owner_rights is None

    def test_an_owner_rights_ace_replaces_the_implicit_rights(self):
        result = ntfs([allow(OWNER_RIGHTS, READ_EXECUTE)], owner_sid=ALICE)

        assert result.owner_rights is None
        assert result.rights == RightsMask.ntfs(READ_EXECUTE)
        assert not result.rights.grants(NtfsRight.WRITE_DAC)
        assert AccessCondition.OWNER_RIGHTS_ACE in result.conditions()

    def test_an_owner_rights_ace_applies_only_to_the_owner(self):
        result = ntfs([allow(OWNER_RIGHTS, READ_EXECUTE)], owner_sid=FINANCE_TEAM)

        assert result.rights.is_empty

    def test_an_inherit_only_owner_rights_ace_does_not_replace_anything(self):
        result = ntfs(
            [allow(OWNER_RIGHTS, READ_EXECUTE, flags=INHERIT_ONLY | CONTAINER_INHERIT)],
            owner_sid=ALICE,
        )

        assert result.owner_rights == OWNER_IMPLICIT_RIGHTS


class TestTrusteesTheCheckCannotResolve:
    def test_a_creator_owner_entry_grants_nothing_and_is_reported(self):
        """No token contains S-1-3-0, so the entry is inert — and looks like a grant."""
        result = ntfs([allow(CREATOR_OWNER, FULL_CONTROL)])

        assert result.rights.is_empty
        assert AccessCondition.CREATOR_OWNER_ACE in result.conditions()

    def test_an_inherit_only_creator_owner_entry_is_not_reported(self):
        """That is the ordinary arrangement on every folder; reporting it is noise."""
        result = ntfs([allow(CREATOR_OWNER, FULL_CONTROL, flags=INHERIT_ONLY | CONTAINER_INHERIT)])

        assert AccessCondition.CREATOR_OWNER_ACE not in result.conditions()

    def test_a_logon_session_trustee_is_reported_as_unevaluated(self):
        result = ntfs([allow("S-1-5-3", FULL_CONTROL)])

        finding = next(
            item for item in result.findings if item.condition is AccessCondition.LOGON_TYPE_TRUSTEE
        )
        assert finding.detail["trustee_sid"] == "S-1-5-3"
        assert result.rights.is_empty

    def test_the_access_paths_own_logon_sid_matches_rather_than_being_reported(self):
        network = token(assumption=TokenAssumption.AUTHENTICATED_USER)

        result = ntfs([allow("S-1-5-2", READ_EXECUTE)], network)

        assert result.rights == RightsMask.ntfs(READ_EXECUTE)
        assert AccessCondition.LOGON_TYPE_TRUSTEE not in result.conditions()

    def test_an_everyone_entry_matches_an_assumed_token(self):
        authenticated = token(assumption=TokenAssumption.AUTHENTICATED_USER)

        assert ntfs([allow(EVERYONE, READ_EXECUTE)], authenticated).rights == RightsMask.ntfs(
            READ_EXECUTE
        )

    def test_an_authenticated_users_entry_does_not_match_an_anonymous_token(self):
        anonymous = token(assumption=TokenAssumption.ANONYMOUS)

        assert ntfs([allow(AUTHENTICATED_USERS, READ_EXECUTE)], anonymous).rights.is_empty


class TestMasksThatNeedInterpreting:
    def test_generic_rights_are_expanded_before_evaluation(self):
        result = ntfs([allow(FINANCE_RW, int(NtfsRight.GENERIC_READ))])

        assert result.rights.grants(NtfsRight.READ_DATA)
        assert AccessCondition.GENERIC_RIGHTS_EXPANDED in result.conditions()

    def test_a_generic_deny_removes_the_rights_it_stands_for(self):
        result = ntfs(
            [deny(FINANCE_RW, int(NtfsRight.GENERIC_READ)), allow(FINANCE_RW, FULL_CONTROL)]
        )

        assert not result.rights.grants(NtfsRight.READ_DATA)
        assert result.rights.grants(NtfsRight.WRITE_DATA)

    def test_maximum_allowed_is_reported_as_indeterminate(self):
        result = ntfs([allow(FINANCE_RW, MAXIMUM_ALLOWED | READ_EXECUTE)])

        assert AccessCondition.INDETERMINATE_RIGHTS in result.conditions()

    def test_unrecognized_bits_are_kept_and_reported(self):
        result = ntfs([allow(FINANCE_RW, READ_EXECUTE | 0x00000200)])

        assert result.rights.unrecognized_bits == 0x00000200
        assert AccessCondition.UNRECOGNIZED_RIGHTS_BITS in result.conditions()

    def test_one_layer_granting_write_dac_is_not_an_escalation_finding(self):
        """A share granting Full Control over NTFS granting nothing changes no ACL."""
        result = ntfs([allow(FINANCE_RW, MODIFY | WRITE_DAC)])

        assert AccessCondition.ESCALATION_RIGHTS not in result.conditions()
        assert result.escalation_rights & NtfsRight.WRITE_DAC


class TestCanonicalOrder:
    def test_a_canonical_dacl_reports_no_violations(self):
        entries = [
            deny(FINANCE_TEAM, MODIFY),
            allow(FINANCE_RW, MODIFY),
            deny(ALICE, MODIFY, inherited=True),
            allow(ALICE, READ_EXECUTE, inherited=True),
        ]

        assert canonical_order_violations(entries) == ()

    def test_an_explicit_entry_behind_an_inherited_one_is_a_violation(self):
        entries = [allow(ALICE, MODIFY, inherited=True), allow(FINANCE_RW, MODIFY)]

        violations = canonical_order_violations(entries)

        assert [item.kind for item in violations] == [OrderViolationKind.INHERITED_BEFORE_EXPLICIT]

    def test_an_explicit_deny_behind_an_explicit_allow_is_a_violation(self):
        entries = [allow(FINANCE_RW, MODIFY), deny(FINANCE_TEAM, MODIFY)]

        violations = canonical_order_violations(entries)

        assert [item.kind for item in violations] == [OrderViolationKind.ALLOW_BEFORE_DENY]
        assert violations[0].earlier_entry.trustee_key == FINANCE_RW

    def test_an_inherited_deny_behind_an_inherited_allow_is_not_reported(self):
        """Canonical across two ancestors, and ADG cannot tell which ancestor is which."""
        entries = [
            allow(FINANCE_RW, MODIFY, inherited=True),
            deny(FINANCE_TEAM, MODIFY, inherited=True),
        ]

        assert canonical_order_violations(entries) == ()

    def test_a_non_canonical_dacl_is_reported_on_the_evaluation(self):
        result = ntfs([allow(FINANCE_RW, MODIFY), deny(FINANCE_TEAM, MODIFY)])

        finding = next(
            item for item in result.findings if item.condition is AccessCondition.NON_CANONICAL_DACL
        )
        assert finding.detail["violations"][0]["kind"] == "allow_before_deny"


class TestLayers:
    def test_a_share_acl_evaluates_the_same_way(self):
        result = evaluate_acl(
            [share_allow(FINANCE_RW, SharePermission.READ, order=0)],
            token(groups=[FINANCE_RW]),
            layer=RightsLayer.SMB_SHARE,
        )

        assert result.rights == RightsMask.smb(READ_EXECUTE)

    def test_a_share_deny_outranks_a_share_allow_behind_it(self):
        result = evaluate_acl(
            [
                share_allow(EVERYONE, SharePermission.FULL, order=0),
                share_entry(
                    trustee_key=FINANCE_RW,
                    trustee_sid=FINANCE_RW,
                    ace_type=AceType.DENY,
                    permission=SharePermission.FULL,
                    order_index=1,
                ),
            ],
            token(groups=[FINANCE_RW], assumption=TokenAssumption.AUTHENTICATED_USER),
            layer=RightsLayer.SMB_SHARE,
        )

        assert result.rights == RightsMask.smb(FULL_CONTROL)

    def test_an_ntfs_entry_cannot_be_evaluated_as_a_share_acl(self):
        with pytest.raises(DomainValidationError, match="Crossing layers"):
            evaluate_acl(
                [allow(FINANCE_RW, MODIFY)],
                token(groups=[FINANCE_RW]),
                layer=RightsLayer.SMB_SHARE,
            )

    def test_the_effective_layer_is_not_an_acl(self):
        with pytest.raises(DomainValidationError, match="crossing two"):
            evaluate_acl([], token(), layer=RightsLayer.EFFECTIVE)

    def test_a_share_entry_needs_exactly_one_of_mask_and_permission(self):
        with pytest.raises(DomainValidationError, match="exactly one"):
            share_entry(
                trustee_key=ALICE,
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
                access_mask=MODIFY,
                permission=SharePermission.FULL,
            )

    def test_an_entry_may_not_carry_a_computed_effective_mask(self):
        """An ACL holds claims; EFFECTIVE is a result, and an ACE can never be one."""
        with pytest.raises(DomainValidationError, match="never a computed"):
            AclEntry(
                trustee_key=ALICE,
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
                mask=RightsMask.effective(MODIFY),
            )

    def test_an_entry_needs_a_trustee_key(self):
        with pytest.raises(DomainValidationError, match="trustee key"):
            AclEntry(
                trustee_key="",
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
                mask=RightsMask.ntfs(MODIFY),
            )


class TestBounds:
    def test_a_dacl_beyond_the_ceiling_is_reported_as_truncated(self):
        entries = [allow(FINANCE_RW, READ_EXECUTE) for _ in range(MAX_ACL_ENTRIES + 5)]

        result = ntfs(entries)

        assert AccessCondition.ACL_TRUNCATED in result.conditions()
        assert result.entries_evaluated == MAX_ACL_ENTRIES

    def test_a_dacl_at_the_ceiling_is_not_reported_as_truncated(self):
        entries = [allow(FINANCE_RW, READ_EXECUTE) for _ in range(MAX_ACL_ENTRIES)]

        assert AccessCondition.ACL_TRUNCATED not in ntfs(entries).conditions()


class TestWhatTheEvaluationReports:
    def test_the_contributed_mask_is_what_survived_earlier_entries(self):
        result = ntfs([deny(FINANCE_RW, int(NtfsRight.WRITE_DATA)), allow(FINANCE_RW, MODIFY)])

        granted = result.granted_by[0]
        assert granted.considered == RightsMask.ntfs(MODIFY)
        assert not granted.contributed.grants(NtfsRight.WRITE_DATA)

    def test_the_matched_token_entry_is_named(self):
        result = ntfs([allow(FINANCE_RW, MODIFY)])

        assert result.granted_by[0].matched.key == FINANCE_RW

    def test_a_direct_grant_is_not_via_a_group(self):
        result = ntfs([allow(ALICE, MODIFY)])

        assert not result.granted_by[0].via_group

    def test_the_owner_rights_are_kept_out_of_the_granted_entries(self):
        """They come from ownership, not from an ACE, and pretending otherwise invents one."""
        result = ntfs([], owner_sid=ALICE)

        assert result.granted_by == ()
        assert result.owner_rights is not None

    def test_entries_supplied_counts_everything_and_evaluated_counts_matches(self):
        result = ntfs([allow(ORPHAN, MODIFY), allow(FINANCE_RW, READ_CONTROL)])

        assert result.entries_supplied == 2
        assert result.entries_evaluated == 1
