"""Crossing the two layers, and admitting what could not be established.

The rights half of this is short — remote access is the intersection, local access is NTFS
alone — and it is the *other* half that carries the weight. Every one of these tests is
about a gap in the observations and what the resolver does with it, because the failure
mode an audit tool has to be built against is not an arithmetic error. It is a coverage
gap rendered as a verdict: an unread share ACL reported as unrestricted, an unwalked group
reported as "not a member", a directory nobody scanned reported as granting nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from app.access_engine import (
    CONDITION_DESCRIPTIONS,
    OVERSTATING_CONDITIONS,
    UNDERSTATING_CONDITIONS,
    AccessCertainty,
    AccessCondition,
    AccessFinding,
    AccessPath,
    AclEntry,
    AclProvenance,
    EffectiveAccess,
    LimitingLayer,
    ResourceDacl,
    RightsLayer,
    RightsMask,
    ShareDacl,
    TokenAssumption,
    certainty_of,
    describe_condition,
    resolve_access,
    unobserved_trustee_findings,
)
from app.domain import DomainValidationError, NtfsRight, SharePermission
from tests.access_engine.support import (
    ALICE,
    EVERYONE,
    FINANCE,
    FINANCE_RW,
    FINANCE_SHARE,
    FINANCE_TEAM,
    FULL_CONTROL,
    MODIFY,
    ORPHAN,
    READ_EXECUTE,
    WRITE_DAC,
    allow,
    dacl,
    deny,
    share_acl,
    share_allow,
    token,
    unread_share,
)


def remote(
    entries: Sequence[AclEntry] = (),
    share: ShareDacl | None = None,
    *,
    groups: Sequence[str] = (FINANCE_RW,),
    **facts: Any,
) -> EffectiveAccess:
    """Resolve a remote-SMB answer with the share granting Full Control unless told otherwise."""
    return resolve_access(
        token(groups=list(groups), assumption=TokenAssumption.AUTHENTICATED_USER),
        dacl(*entries, **facts),
        share if share is not None else share_acl(share_allow(EVERYONE, order=0)),
    )


def local(
    entries: Sequence[AclEntry] = (),
    *,
    groups: Sequence[str] = (FINANCE_RW,),
    **facts: Any,
) -> EffectiveAccess:
    return resolve_access(
        token(
            groups=list(groups),
            path=AccessPath.LOCAL,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        ),
        dacl(*entries, **facts),
    )


class TestCrossingTheLayers:
    def test_remote_access_is_the_intersection(self):
        result = remote(
            [allow(FINANCE_RW, FULL_CONTROL)],
            share_acl(share_allow(EVERYONE, SharePermission.READ, order=0)),
        )

        assert result.rights == RightsMask.effective(READ_EXECUTE)
        assert result.rights.layer is RightsLayer.EFFECTIVE

    def test_local_access_ignores_the_share_entirely(self):
        result = local([allow(FINANCE_RW, FULL_CONTROL)])

        assert result.rights == RightsMask.effective(FULL_CONTROL)
        assert result.share is None

    def test_a_permissive_share_cannot_widen_ntfs(self):
        result = remote([allow(FINANCE_RW, READ_EXECUTE)])

        assert result.rights == RightsMask.effective(READ_EXECUTE)
        assert result.limiting_layer is LimitingLayer.NTFS

    def test_a_restrictive_share_is_named_as_the_limit(self):
        result = remote(
            [allow(FINANCE_RW, FULL_CONTROL)],
            share_acl(share_allow(EVERYONE, SharePermission.READ, order=0)),
        )

        assert result.limiting_layer is LimitingLayer.SMB_SHARE

    def test_two_layers_each_withholding_something_report_both(self):
        result = remote(
            [allow(FINANCE_RW, int(NtfsRight.WRITE_DATA) | int(NtfsRight.READ_DATA))],
            share_acl(
                share_allow(EVERYONE, mask=int(NtfsRight.READ_DATA) | int(NtfsRight.DELETE)),
            ),
        )

        assert result.limiting_layer is LimitingLayer.BOTH

    def test_identical_layers_limit_nothing(self):
        result = remote(
            [allow(FINANCE_RW, READ_EXECUTE)],
            share_acl(share_allow(EVERYONE, mask=READ_EXECUTE)),
        )

        assert result.limiting_layer is LimitingLayer.NONE

    def test_the_layer_inputs_are_kept_for_the_explanation(self):
        result = remote(
            [allow(FINANCE_RW, FULL_CONTROL)],
            share_acl(share_allow(EVERYONE, SharePermission.READ, order=0)),
        )

        assert result.ntfs_rights == RightsMask.ntfs(FULL_CONTROL)
        assert result.share_rights == RightsMask.smb(READ_EXECUTE)
        assert result.crossed is not None
        assert result.crossed.limited_by_share


class TestTheAccessPathIsNeverInferred:
    def test_a_remote_resolution_without_a_share_is_refused(self):
        with pytest.raises(DomainValidationError, match="needs the share layer"):
            resolve_access(token(groups=[FINANCE_RW]), dacl(allow(FINANCE_RW, MODIFY)))

    def test_a_local_resolution_with_a_share_is_refused(self):
        with pytest.raises(DomainValidationError, match="does not pass through"):
            resolve_access(
                token(groups=[FINANCE_RW], path=AccessPath.LOCAL),
                dacl(allow(FINANCE_RW, MODIFY)),
                share_acl(share_allow(EVERYONE)),
            )


class TestAnUnreadShareIsNotAnOpenOne:
    def test_the_result_becomes_an_upper_bound(self):
        result = remote([allow(FINANCE_RW, FULL_CONTROL)], unread_share())

        assert result.rights == RightsMask.effective(FULL_CONTROL)
        assert result.certainty is AccessCertainty.AT_MOST
        assert AccessCondition.SHARE_ACL_NOT_OBSERVED in result.conditions

    def test_the_limiting_layer_is_unknown_rather_than_none(self):
        result = remote([allow(FINANCE_RW, MODIFY)], unread_share())

        assert result.limiting_layer is LimitingLayer.UNKNOWN

    def test_no_share_evaluation_is_fabricated(self):
        result = remote([allow(FINANCE_RW, MODIFY)], unread_share())

        assert result.share is None
        assert result.crossed is None

    def test_an_unobserved_share_may_not_carry_entries(self):
        with pytest.raises(DomainValidationError, match="cannot carry entries"):
            ShareDacl(share_key=FINANCE_SHARE, entries=(share_allow(EVERYONE),), observed=False)


class TestWhereTheDaclCameFrom:
    def test_an_observed_dacl_is_certain(self):
        result = local([allow(FINANCE_RW, MODIFY)])

        assert result.provenance is AclProvenance.OBSERVED
        assert result.certainty is AccessCertainty.CERTAIN

    def test_a_projected_dacl_is_reported_as_derived(self):
        result = resolve_access(
            token(groups=[FINANCE_RW], path=AccessPath.LOCAL),
            ResourceDacl(
                resource_key=FINANCE,
                entries=(allow(FINANCE_RW, MODIFY, inherited=True, order=0),),
                provenance=AclProvenance.DERIVED,
                derived_from="\\\\fs01\\finance",
                derived_distance=1,
            ),
        )

        assert result.certainty is AccessCertainty.AT_MOST
        assert AccessCondition.NTFS_ACL_DERIVED in result.conditions
        assert AccessCondition.INTERMEDIATE_PATH_UNOBSERVED not in result.conditions

    def test_a_projection_from_further_up_reports_the_unread_levels(self):
        result = resolve_access(
            token(groups=[FINANCE_RW], path=AccessPath.LOCAL),
            ResourceDacl(
                resource_key="\\\\fs01\\finance\\a\\b\\c",
                entries=(allow(FINANCE_RW, MODIFY, inherited=True, order=0),),
                provenance=AclProvenance.DERIVED,
                derived_from=FINANCE,
                derived_distance=3,
            ),
        )

        finding = next(
            item
            for item in result.findings
            if item.condition is AccessCondition.INTERMEDIATE_PATH_UNOBSERVED
        )
        assert finding.detail["unobserved_levels"] == 2

    def test_an_unobserved_path_grants_nothing_and_says_so(self):
        result = resolve_access(
            token(groups=[FINANCE_RW], path=AccessPath.LOCAL),
            ResourceDacl(resource_key=FINANCE, provenance=AclProvenance.UNOBSERVED),
        )

        assert not result.has_access
        assert AccessCondition.NTFS_ACL_NOT_OBSERVED in result.conditions

    def test_a_derived_dacl_must_name_its_ancestor(self):
        with pytest.raises(DomainValidationError, match="must agree"):
            ResourceDacl(resource_key=FINANCE, provenance=AclProvenance.DERIVED)

    def test_an_observed_dacl_may_not_claim_an_ancestor(self):
        with pytest.raises(DomainValidationError, match="must agree"):
            ResourceDacl(resource_key=FINANCE, derived_from=FINANCE)

    def test_an_unread_descriptor_may_not_be_reported_as_a_null_dacl(self):
        with pytest.raises(DomainValidationError, match="grant everyone everything"):
            ResourceDacl(
                resource_key=FINANCE, dacl_present=False, provenance=AclProvenance.UNOBSERVED
            )


class TestCertainty:
    def test_a_fully_observed_answer_is_certain(self):
        assert certainty_of([]) is AccessCertainty.CERTAIN

    def test_an_unseen_restriction_makes_it_an_upper_bound(self):
        findings = [AccessFinding(AccessCondition.SHARE_ACL_NOT_OBSERVED)]

        assert certainty_of(findings) is AccessCertainty.AT_MOST

    def test_an_unseen_membership_makes_it_a_lower_bound(self):
        findings = [AccessFinding(AccessCondition.MEMBERSHIP_TRUNCATED)]

        assert certainty_of(findings) is AccessCertainty.AT_LEAST

    def test_gaps_in_both_directions_do_not_cancel_out(self):
        findings = [
            AccessFinding(AccessCondition.SHARE_ACL_NOT_OBSERVED),
            AccessFinding(AccessCondition.MEMBERSHIP_TRUNCATED),
        ]

        assert certainty_of(findings) is AccessCertainty.UNCERTAIN

    def test_an_indeterminate_mask_is_uncertain_on_its_own(self):
        findings = [AccessFinding(AccessCondition.INDETERMINATE_RIGHTS)]

        assert certainty_of(findings) is AccessCertainty.UNCERTAIN

    def test_the_assumed_token_sids_alone_do_not_move_certainty(self):
        """Windows guarantees them for a session of the declared kind."""
        findings = [AccessFinding(AccessCondition.ASSUMED_TOKEN_SIDS)]

        assert certainty_of(findings) is AccessCertainty.CERTAIN

    def test_no_access_with_a_lower_bound_is_not_no_access(self):
        result = resolve_access(
            token(groups=[FINANCE_RW], path=AccessPath.LOCAL, membership_complete=False),
            dacl(allow(FINANCE_TEAM, MODIFY)),
        )

        assert not result.has_access
        assert result.certainty is AccessCertainty.AT_LEAST


class TestTrusteeCoverage:
    def test_a_trustee_with_no_collected_membership_is_a_finding(self):
        findings = unobserved_trustee_findings(
            [FINANCE_TEAM, FINANCE_RW], trustees_with_membership=[FINANCE_RW]
        )

        assert [item.detail["trustee_key"] for item in findings] == [FINANCE_TEAM]

    def test_it_is_reported_once_per_trustee(self):
        findings = unobserved_trustee_findings(
            [FINANCE_TEAM, FINANCE_TEAM], trustees_with_membership=[]
        )

        assert len(findings) == 1

    def test_the_caller_supplied_finding_reaches_the_certainty(self):
        result = resolve_access(
            token(groups=[FINANCE_RW], path=AccessPath.LOCAL),
            dacl(allow(FINANCE_TEAM, MODIFY)),
            findings=unobserved_trustee_findings([FINANCE_TEAM], trustees_with_membership=[]),
        )

        assert result.certainty is AccessCertainty.AT_LEAST
        assert AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED in result.conditions


class TestWhatTheResultReports:
    def test_the_grant_and_deny_entries_span_both_layers(self):
        result = remote(
            [allow(FINANCE_RW, FULL_CONTROL)],
            share_acl(share_allow(EVERYONE, SharePermission.READ, order=0)),
        )

        assert [entry.trustee_key for entry in result.grant_entries] == [FINANCE_RW, EVERYONE]

    def test_a_deny_on_either_layer_is_listed(self):
        result = remote([deny(FINANCE_RW, MODIFY), allow(FINANCE_RW, MODIFY)])

        assert [entry.trustee_key for entry in result.deny_entries] == [FINANCE_RW]

    def test_escalation_is_raised_on_the_effective_mask(self):
        result = remote([allow(FINANCE_RW, MODIFY | WRITE_DAC)])

        assert AccessCondition.ESCALATION_RIGHTS in result.conditions

    def test_escalation_is_not_raised_when_the_share_removes_it(self):
        result = remote(
            [allow(FINANCE_RW, MODIFY | WRITE_DAC)],
            share_acl(share_allow(EVERYONE, SharePermission.READ, order=0)),
        )

        assert AccessCondition.ESCALATION_RIGHTS not in result.conditions

    def test_the_label_is_recomputed_from_the_mask(self):
        result = remote([allow(FINANCE_RW, MODIFY)])

        assert result.summary().primary.value == "modify"

    def test_the_conditions_are_distinct_and_in_first_seen_order(self):
        result = remote([allow(ORPHAN, MODIFY)], unread_share())

        assert len(result.conditions) == len(set(result.conditions))

    def test_a_null_dacl_grants_through_an_unrestricted_share(self):
        result = remote([], dacl_present=True)
        open_result = resolve_access(
            token(assumption=TokenAssumption.AUTHENTICATED_USER),
            ResourceDacl(resource_key=FINANCE, dacl_present=False),
            share_acl(share_allow(EVERYONE, order=0)),
        )

        assert not result.has_access
        assert open_result.rights == RightsMask.effective(FULL_CONTROL)

    def test_a_resource_needs_a_key(self):
        with pytest.raises(DomainValidationError, match="needs a key"):
            ResourceDacl(resource_key="")

    def test_a_share_needs_a_key(self):
        with pytest.raises(DomainValidationError, match="needs a key"):
            ShareDacl(share_key="")

    def test_the_subject_is_carried_on_the_result(self):
        result = local([allow(ALICE, MODIFY)], groups=())

        assert result.token.subject.key == ALICE


class TestTheConditionVocabulary:
    """The vocabulary is a wire contract, so it has to stay complete and disjoint."""

    def test_every_condition_has_an_operator_facing_sentence(self):
        missing = [
            condition.value
            for condition in AccessCondition
            if condition not in CONDITION_DESCRIPTIONS
        ]

        assert missing == [], (
            f"{missing} would be reported to an operator as a bare code. A condition "
            "without a sentence is one nobody can act on."
        )

    def test_describe_condition_answers_for_every_member(self):
        assert all(describe_condition(condition) for condition in AccessCondition)

    def test_the_direction_sets_name_only_real_conditions(self):
        known = set(AccessCondition)

        assert known >= OVERSTATING_CONDITIONS
        assert known >= UNDERSTATING_CONDITIONS

    def test_a_condition_in_both_directions_is_deliberate(self):
        """Three of them are, and each is a gap whose missing data could be either kind."""
        assert {
            AccessCondition.ACE_COUNT_MISMATCH,
            AccessCondition.ACL_TRUNCATED,
            AccessCondition.SUBJECT_UNRESOLVED,
        } == OVERSTATING_CONDITIONS & UNDERSTATING_CONDITIONS
