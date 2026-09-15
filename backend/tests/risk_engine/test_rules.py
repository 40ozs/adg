r"""Each rule: what it matches, and — more carefully — what it refuses to match.

The second half is the point. Every rule here could be written in a way that turns a gap in
ADG's collection into a finding, and every such mistake points the same direction: it reports
something as true because nobody looked. So each rule gets a "fires" test and at least one
"does not fire" test naming the gap it must not mistake for a fact.
"""

from __future__ import annotations

import pytest

from app.domain import (
    AceFlag,
    AceType,
    AclBoundaryReason,
    GroupType,
    PrincipalKind,
    UnresolvedReason,
)
from app.risk_engine import (
    DEFAULT_CONFIGURATION,
    AclProvenanceFacts,
    Confidence,
    FactQualifier,
    RiskConfiguration,
    RiskFacts,
    RiskFinding,
    RuleId,
    Severity,
    SeverityBand,
    evaluate,
    merge_option_overrides,
    only_rules,
)
from tests.risk_engine.support import (
    ALICE,
    AUTHENTICATED_USERS,
    DOMAIN_USERS,
    EVERYONE,
    FULL_CONTROL,
    MODIFY,
    READ_EXECUTE,
    bundle,
    group,
    membership,
    ntfs_ace,
    resource,
    share,
    share_ace,
    sid,
    unresolved,
    user,
)

RESOURCE = "fs01|finance"
SHARE = "fs01|finance"


def findings_of(
    rule: RuleId,
    facts: RiskFacts,
    configuration: RiskConfiguration = DEFAULT_CONFIGURATION,
) -> tuple[RiskFinding, ...]:
    """Every finding one rule produces, with the other ten switched off.

    Isolating the rule is what makes each assertion about that rule rather than about the
    order the catalog happens to list them in.
    """
    result = evaluate(facts, only_rules(configuration, [rule]))
    assert result.rules_run == (rule,)
    return result.by_rule(rule)


# --------------------------------------------------------------------------------------
# Everyone
# --------------------------------------------------------------------------------------


class TestEveryoneBroadAccess:
    def test_an_allow_entry_naming_everyone_is_a_finding(self):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, EVERYONE, MODIFY)),))
        (finding,) = findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts)
        assert finding.band is SeverityBand.WRITE
        assert finding.severity is Severity.CRITICAL
        assert finding.subject.resource_key == RESOURCE

    def test_full_control_bands_above_modify(self):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL)),))
        (finding,) = findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts)
        assert finding.band is SeverityBand.FULL_CONTROL

    def test_a_deny_ahead_of_the_allow_removes_the_finding(self):
        """The Windows access check, not an ACE count: the grant is genuinely not there."""
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL, ace_type=AceType.DENY, order=0),
                    ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL, order=1),
                ),
            )
        )
        assert findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts) == ()

    def test_an_allow_ahead_of_a_deny_still_grants_and_still_fires(self):
        """Windows honors stored order. The canonical model would hide this one."""
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, EVERYONE, MODIFY, order=0),
                    ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL, ace_type=AceType.DENY, order=1),
                ),
            )
        )
        (finding,) = findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts)
        assert finding.band is SeverityBand.WRITE

    def test_an_inherit_only_entry_grants_nothing_here(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL, flags=AceFlag.INHERIT_ONLY),
                ),
            )
        )
        assert findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts) == ()

    def test_a_grant_below_the_threshold_does_not_fire(self):
        configuration = merge_option_overrides(
            DEFAULT_CONFIGURATION, RuleId.EVERYONE_BROAD_ACCESS, minimum_category="modify"
        )
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, EVERYONE, READ_EXECUTE)),))
        assert findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts, configuration) == ()

    def test_a_null_dacl_is_reported_as_its_own_band(self):
        facts = bundle(resources=(resource(RESOURCE, dacl_present=False),))
        (finding,) = findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts)
        assert finding.band is SeverityBand.NULL_DACL
        assert finding.severity is Severity.CRITICAL

    def test_an_unread_descriptor_is_not_a_null_dacl(self):
        """The gap this rule must never turn into a critical finding."""
        facts = bundle(
            resources=(
                resource(RESOURCE, dacl_present=False, provenance=AclProvenanceFacts.UNOBSERVED),
            )
        )
        assert findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts) == ()

    def test_a_share_acl_nobody_read_produces_nothing(self):
        facts = bundle(shares=(share(SHARE, observed=False),))
        assert findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts) == ()

    def test_a_share_entry_is_found_on_the_share_layer(self):
        facts = bundle(shares=(share(SHARE, share_ace(SHARE, EVERYONE)),))
        (finding,) = findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts)
        assert finding.subject.share_key == SHARE
        assert finding.subject.resource_key is None

    def test_a_derived_acl_lowers_confidence_without_losing_the_finding(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL),
                    provenance=AclProvenanceFacts.DERIVED,
                    derived_distance=1,
                ),
            )
        )
        (finding,) = findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts)
        assert finding.confidence is Confidence.PROBABLE
        assert FactQualifier.ACL_DERIVED in finding.qualifiers

    def test_a_distant_projection_is_weaker_still(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL),
                    provenance=AclProvenanceFacts.DERIVED,
                    derived_distance=3,
                ),
            )
        )
        (finding,) = findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts)
        assert finding.confidence is Confidence.POSSIBLE

    def test_an_undelivered_entry_weakens_the_answer(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE, ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL), undelivered_ace_count=4
                ),
            )
        )
        (finding,) = findings_of(RuleId.EVERYONE_BROAD_ACCESS, facts)
        assert FactQualifier.ACE_COUNT_SHORT in finding.qualifiers
        assert finding.confidence is Confidence.POSSIBLE


# --------------------------------------------------------------------------------------
# Authenticated Users and Domain Users
# --------------------------------------------------------------------------------------


class TestAuthenticatedUsers:
    def test_it_fires_on_the_well_known_sid(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, AUTHENTICATED_USERS, MODIFY)),)
        )
        (finding,) = findings_of(RuleId.AUTHENTICATED_USERS_BROAD_ACCESS, facts)
        assert finding.severity is Severity.HIGH

    def test_it_does_not_fire_on_everyone(self):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, EVERYONE, MODIFY)),))
        assert findings_of(RuleId.AUTHENTICATED_USERS_BROAD_ACCESS, facts) == ()


class TestDomainUsers:
    def test_it_matches_by_rid_not_by_a_constant_sid(self):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, DOMAIN_USERS, MODIFY)),))
        (finding,) = findings_of(RuleId.DOMAIN_USERS_BROAD_ACCESS, facts)
        assert finding.detail["trustee_sid"] == DOMAIN_USERS

    def test_a_different_domain_s_domain_users_also_matches(self):
        other = "S-1-5-21-9-9-9-513"
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, other, MODIFY)),))
        (finding,) = findings_of(RuleId.DOMAIN_USERS_BROAD_ACCESS, facts)
        assert finding.detail["trustee_sid"] == other

    def test_an_ordinary_group_does_not_match(self):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),))
        assert findings_of(RuleId.DOMAIN_USERS_BROAD_ACCESS, facts) == ()


# --------------------------------------------------------------------------------------
# Direct user entries
# --------------------------------------------------------------------------------------


class TestDirectUserAce:
    def test_a_user_named_on_an_acl_is_a_finding(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, ALICE, MODIFY)),),
            principals=(user(ALICE, name="Alice Chen"),),
        )
        (finding,) = findings_of(RuleId.DIRECT_USER_ACE, facts)
        assert finding.subject.principal_key == ALICE
        assert finding.detail["trustee_label"] == "Alice Chen"

    def test_a_group_is_not_a_direct_user_entry(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),),
            principals=(group(sid(1201)),),
        )
        assert findings_of(RuleId.DIRECT_USER_ACE, facts) == ()

    def test_a_trustee_nothing_describes_is_not_reported_as_a_user(self):
        """The gap: no record and "not a user" are the same absence."""
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(9911), MODIFY)),))
        assert findings_of(RuleId.DIRECT_USER_ACE, facts) == ()

    def test_a_user_whose_entry_is_wholly_denied_holds_nothing_to_report(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, ALICE, FULL_CONTROL, ace_type=AceType.DENY, order=0),
                    ntfs_ace(RESOURCE, ALICE, MODIFY, order=1),
                ),
            ),
            principals=(user(ALICE),),
        )
        assert findings_of(RuleId.DIRECT_USER_ACE, facts) == ()


# --------------------------------------------------------------------------------------
# Unresolved SIDs
# --------------------------------------------------------------------------------------


class TestUnresolvedSid:
    def test_a_trustee_with_no_record_is_an_orphan(self):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(9911), MODIFY)),))
        (finding,) = findings_of(RuleId.UNRESOLVED_SID_ON_ACL, facts)
        assert finding.detail["reason"] == "no_principal_record"

    def test_a_record_that_says_unresolved_carries_its_reason(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(9911), MODIFY)),),
            principals=(unresolved(sid(9911), unresolved_reason=UnresolvedReason.DELETED),),
        )
        (finding,) = findings_of(RuleId.UNRESOLVED_SID_ON_ACL, facts)
        assert finding.detail["reason"] == "deleted"

    def test_a_deleted_account_is_reported(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, ALICE, MODIFY)),),
            principals=(user(ALICE, is_deleted=True),),
        )
        (finding,) = findings_of(RuleId.UNRESOLVED_SID_ON_ACL, facts)
        assert finding.detail["reason"] == "deleted"

    @pytest.mark.parametrize("trustee", [EVERYONE, AUTHENTICATED_USERS, "S-1-5-32-544", "S-1-5-18"])
    def test_a_well_known_sid_with_no_record_is_not_an_orphan(self, trustee):
        """Everyone means Everyone whether or not a run happened to emit a row for it.

        Without this the rule would fire on nearly every access control list in the estate and
        bury the deleted domain accounts, which look identical except that their SIDs mean
        nothing anywhere.
        """
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, trustee, MODIFY)),))
        assert findings_of(RuleId.UNRESOLVED_SID_ON_ACL, facts) == ()

    def test_a_deny_entry_naming_an_orphan_is_still_reported(self):
        """A stale Deny is clutter with teeth; the rule does not require a grant."""
        facts = bundle(
            resources=(
                resource(RESOURCE, ntfs_ace(RESOURCE, sid(9911), MODIFY, ace_type=AceType.DENY)),
            )
        )
        assert len(findings_of(RuleId.UNRESOLVED_SID_ON_ACL, facts)) == 1


# --------------------------------------------------------------------------------------
# Inheritance
# --------------------------------------------------------------------------------------


class TestBrokenInheritance:
    def test_a_protected_dacl_is_reported(self):
        facts = bundle(resources=(resource(RESOURCE, dacl_protected=True),))
        (finding,) = findings_of(RuleId.BROKEN_INHERITANCE, facts)
        assert finding.band is SeverityBand.PROTECTED

    def test_a_divergence_from_the_parent_is_informational(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    is_acl_boundary=True,
                    boundary_reason=AclBoundaryReason.ACL_DIFFERS_FROM_PARENT,
                ),
            )
        )
        (finding,) = findings_of(RuleId.BROKEN_INHERITANCE, facts)
        assert finding.band is SeverityBand.DIVERGED
        assert finding.severity is Severity.INFORMATIONAL

    @pytest.mark.parametrize(
        "reason",
        [
            AclBoundaryReason.SCAN_ROOT,
            AclBoundaryReason.SHARE_ROOT,
            AclBoundaryReason.PARENT_UNREADABLE,
            AclBoundaryReason.PARENT_NULL_DACL,
        ],
    )
    def test_a_boundary_that_only_means_nobody_looked_is_not_a_finding(self, reason):
        """Four of the seven boundary reasons describe the collector's reach, not permissions.

        ADR-0009 makes unknown read as a boundary so a *scan* is safe. Turning that into a
        risk finding would report the scan's limits as the estate's permissions.
        """
        facts = bundle(
            resources=(resource(RESOURCE, is_acl_boundary=True, boundary_reason=reason),)
        )
        assert findings_of(RuleId.BROKEN_INHERITANCE, facts) == ()

    def test_a_share_root_is_excluded_by_default(self):
        facts = bundle(resources=(resource(RESOURCE, depth=0, dacl_protected=True),))
        assert findings_of(RuleId.BROKEN_INHERITANCE, facts) == ()

    def test_a_share_root_can_be_included_by_configuration(self):
        configuration = merge_option_overrides(
            DEFAULT_CONFIGURATION, RuleId.BROKEN_INHERITANCE, include_share_roots=True
        )
        facts = bundle(resources=(resource(RESOURCE, depth=0, dacl_protected=True),))
        assert len(findings_of(RuleId.BROKEN_INHERITANCE, facts, configuration)) == 1

    def test_divergence_can_be_switched_off_without_losing_protection(self):
        configuration = merge_option_overrides(
            DEFAULT_CONFIGURATION, RuleId.BROKEN_INHERITANCE, include_diverged=False
        )
        diverged = resource(
            "fs01|a",
            boundary_reason=AclBoundaryReason.ACL_DIFFERS_FROM_PARENT,
            is_acl_boundary=True,
        )
        protected = resource("fs01|b", dacl_protected=True)
        facts = bundle(resources=(diverged, protected))
        findings = findings_of(RuleId.BROKEN_INHERITANCE, facts, configuration)
        assert [item.subject.resource_key for item in findings] == ["fs01|b"]


# --------------------------------------------------------------------------------------
# Membership shapes
# --------------------------------------------------------------------------------------


class TestDeepGroupNesting:
    def test_a_chain_longer_than_the_threshold_is_reported(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),),
            principals=(group(sid(1201), name="Finance-RW"),),
            memberships=(
                membership(sid(1201), sid(1202)),
                membership(sid(1202), sid(1203)),
                membership(sid(1203), sid(1204)),
                membership(sid(1204), ALICE),
            ),
        )
        (finding,) = findings_of(RuleId.DEEP_GROUP_NESTING, facts)
        assert finding.detail["depth"] == 4
        assert finding.subject.principal_key == sid(1201)

    def test_a_chain_at_the_threshold_is_not_reported(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),),
            principals=(group(sid(1201)),),
            memberships=(
                membership(sid(1201), sid(1202)),
                membership(sid(1202), sid(1203)),
                membership(sid(1203), ALICE),
            ),
        )
        assert findings_of(RuleId.DEEP_GROUP_NESTING, facts) == ()

    def test_a_cycle_terminates_and_is_reported_once(self):
        """The demo estate really contains one; a rule that walked it forever would hang."""
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1208), MODIFY)),),
            principals=(group(sid(1208)),),
            memberships=(
                membership(sid(1208), sid(1300)),
                membership(sid(1300), sid(1208), sid(1301)),
                membership(sid(1301), sid(1302)),
                membership(sid(1302), ALICE),
            ),
        )
        findings = findings_of(RuleId.DEEP_GROUP_NESTING, facts)
        assert len(findings) == 1

    def test_a_group_on_no_acl_is_not_examined(self):
        facts = bundle(
            memberships=(
                membership(sid(1201), sid(1202)),
                membership(sid(1202), sid(1203)),
                membership(sid(1203), sid(1204)),
                membership(sid(1204), ALICE),
            ),
        )
        assert findings_of(RuleId.DEEP_GROUP_NESTING, facts) == ()


class TestRedundantAccessPaths:
    def test_two_trustees_reaching_one_principal_is_a_finding(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, sid(1202), MODIFY, order=0),
                    ntfs_ace(RESOURCE, sid(1204), READ_EXECUTE, order=1),
                ),
            ),
            principals=(group(sid(1202)), group(sid(1204)), user(ALICE)),
            memberships=(membership(sid(1202), ALICE), membership(sid(1204), ALICE)),
        )
        (finding,) = findings_of(RuleId.REDUNDANT_ACCESS_PATHS, facts)
        assert finding.subject.principal_key == ALICE
        assert finding.detail["route_count"] == 2

    def test_two_chains_to_one_trustee_are_one_route(self):
        """Removing one chain changes nothing while the trustee is still on the list."""
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1202), MODIFY)),),
            principals=(group(sid(1202)), group(sid(1205)), group(sid(1206)), user(ALICE)),
            memberships=(
                membership(sid(1202), sid(1205), sid(1206)),
                membership(sid(1205), ALICE),
                membership(sid(1206), ALICE),
            ),
        )
        assert findings_of(RuleId.REDUNDANT_ACCESS_PATHS, facts) == ()

    def test_nested_groups_are_not_themselves_reported(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, sid(1202), MODIFY, order=0),
                    ntfs_ace(RESOURCE, sid(1204), MODIFY, order=1),
                ),
            ),
            principals=(group(sid(1202)), group(sid(1204)), group(sid(1207))),
            memberships=(membership(sid(1202), sid(1207)), membership(sid(1204), sid(1207))),
        )
        assert findings_of(RuleId.REDUNDANT_ACCESS_PATHS, facts) == ()


class TestEmptyPermissionBearingGroup:
    def test_an_enumerated_empty_group_on_an_acl_is_a_finding(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),),
            principals=(group(sid(1201), name="Finance-RW"),),
            memberships=(membership(sid(1201), enumerated=True),),
        )
        (finding,) = findings_of(RuleId.EMPTY_PERMISSION_BEARING_GROUP, facts)
        assert finding.subject.principal_key == sid(1201)

    @pytest.mark.parametrize("enumerated", [None, False])
    def test_a_group_nobody_enumerated_produces_nothing(self, enumerated):
        """The single most important refusal in the engine.

        Not a lower-confidence finding — nothing. The action this finding invites is removing
        a grant, and the group may have a hundred members that no run has collected.
        """
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),),
            principals=(group(sid(1201)),),
            memberships=(membership(sid(1201), enumerated=enumerated),),
        )
        assert findings_of(RuleId.EMPTY_PERMISSION_BEARING_GROUP, facts) == ()

    def test_a_group_with_no_membership_record_at_all_produces_nothing(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),),
            principals=(group(sid(1201)),),
        )
        assert findings_of(RuleId.EMPTY_PERMISSION_BEARING_GROUP, facts) == ()

    def test_a_user_named_on_an_acl_is_not_an_empty_group(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, ALICE, MODIFY)),),
            principals=(user(ALICE),),
            memberships=(membership(ALICE, enumerated=True),),
        )
        assert findings_of(RuleId.EMPTY_PERMISSION_BEARING_GROUP, facts) == ()


class TestDisabledPrincipalRetainsAccess:
    def test_a_disabled_user_named_directly_is_reported(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1108), MODIFY)),),
            principals=(user(sid(1108), name="Erin Black", enabled=False),),
        )
        (finding,) = findings_of(RuleId.DISABLED_PRINCIPAL_RETAINS_ACCESS, facts)
        assert finding.detail["depth"] == 0

    def test_a_disabled_user_inside_a_granted_group_is_reported_with_its_chain(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),),
            principals=(group(sid(1201)), user(sid(1108), enabled=False)),
            memberships=(membership(sid(1201), sid(1205)), membership(sid(1205), sid(1108))),
        )
        (finding,) = findings_of(RuleId.DISABLED_PRINCIPAL_RETAINS_ACCESS, facts)
        assert finding.detail["chain"] == [sid(1201), sid(1205), sid(1108)]

    def test_an_enabled_user_is_not_reported(self):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, ALICE, MODIFY)),),
            principals=(user(ALICE, enabled=True),),
        )
        assert findings_of(RuleId.DISABLED_PRINCIPAL_RETAINS_ACCESS, facts) == ()

    def test_an_unknown_account_status_is_not_reported_as_disabled(self):
        """``None`` is the normal state for a group and for anything no directory run read."""
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), MODIFY)),),
            principals=(group(sid(1201)),),
            memberships=(membership(sid(1201), ALICE),),
        )
        assert findings_of(RuleId.DISABLED_PRINCIPAL_RETAINS_ACCESS, facts) == ()


# --------------------------------------------------------------------------------------
# Sensitive resources
# --------------------------------------------------------------------------------------


class TestBroadAccessOnSensitiveResource:
    def test_it_produces_nothing_when_nothing_is_marked(self):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL)),))
        assert findings_of(RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE, facts) == ()

    def test_a_tagged_resource_with_a_broad_write_grant_is_critical(self, sensitive_configuration):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL)),))
        (finding,) = findings_of(
            RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE, facts, sensitive_configuration
        )
        assert finding.severity is Severity.CRITICAL
        assert finding.detail["sensitivity_label"] == "Payroll"

    def test_a_read_grant_is_below_the_threshold(self, sensitive_configuration):
        facts = bundle(resources=(resource(RESOURCE, ntfs_ace(RESOURCE, EVERYONE, READ_EXECUTE)),))
        assert (
            findings_of(RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE, facts, sensitive_configuration)
            == ()
        )

    def test_a_named_group_is_not_a_broad_trustee(self, sensitive_configuration):
        facts = bundle(
            resources=(resource(RESOURCE, ntfs_ace(RESOURCE, sid(1201), FULL_CONTROL)),),
            principals=(group(sid(1201)),),
        )
        assert (
            findings_of(RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE, facts, sensitive_configuration)
            == ()
        )

    def test_an_untagged_neighbor_is_not_covered(self, sensitive_configuration):
        """A prefix tag stops at a path separator, so Finance-Archive is a different share."""
        other = resource(
            "fs01|finance-archive",
            ntfs_ace("fs01|finance-archive", EVERYONE, FULL_CONTROL),
            path="\\\\FS01\\Finance-Archive",
            share_key="fs01|finance-archive",
        )
        facts = bundle(resources=(other,))
        assert (
            findings_of(RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE, facts, sensitive_configuration)
            == ()
        )

    def test_a_child_of_a_tagged_prefix_is_covered(self, sensitive_configuration):
        child = resource(
            "fs01|finance|payroll",
            ntfs_ace("fs01|finance|payroll", EVERYONE, FULL_CONTROL),
            path="\\\\FS01\\Finance\\Payroll",
            depth=1,
        )
        facts = bundle(resources=(child,))
        assert (
            len(
                findings_of(
                    RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE, facts, sensitive_configuration
                )
            )
            == 1
        )


# --------------------------------------------------------------------------------------
# Cross-cutting
# --------------------------------------------------------------------------------------


class TestDeterminism:
    def test_the_same_facts_produce_the_same_findings_in_the_same_order(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL, order=0),
                    ntfs_ace(RESOURCE, ALICE, MODIFY, order=1),
                    ntfs_ace(RESOURCE, sid(9911), MODIFY, order=2),
                ),
            ),
            principals=(user(ALICE),),
        )
        first = [item.key for item in evaluate(facts).findings]
        second = [item.key for item in evaluate(facts).findings]
        assert first == second
        assert len(set(first)) == len(first), "finding keys must be unique within a pass"

    def test_findings_are_ordered_most_severe_first(self):
        facts = bundle(
            resources=(
                resource(
                    RESOURCE,
                    ntfs_ace(RESOURCE, EVERYONE, FULL_CONTROL, order=0),
                    ntfs_ace(RESOURCE, ALICE, MODIFY, order=1),
                ),
            ),
            principals=(user(ALICE),),
        )
        severities = [item.severity for item in evaluate(facts).findings]
        assert severities == sorted(severities, key=lambda item: -_RANK[item])

    def test_every_rule_reports_whether_it_ran(self):
        result = evaluate(bundle())
        assert len(result.runs) == len(RuleId)
        assert all(run.ran or run.skipped_because for run in result.runs)

    def test_a_disabled_rule_says_so_rather_than_reporting_nothing(self):
        configuration = only_rules(DEFAULT_CONFIGURATION, [RuleId.EVERYONE_BROAD_ACCESS])
        result = evaluate(bundle(), configuration)
        skipped = {run.rule_id: run.skipped_because for run in result.runs if not run.ran}
        assert RuleId.DIRECT_USER_ACE in skipped
        assert skipped[RuleId.DIRECT_USER_ACE] == "disabled by configuration"

    def test_a_distribution_group_is_described_as_granting_no_access(self):
        record = group(sid(1209), group_type=GroupType.DISTRIBUTION)
        assert record.grants_access is False
        assert group(sid(1201), group_type=GroupType.UNKNOWN).grants_access is None

    def test_an_empty_estate_produces_no_findings_at_all(self):
        result = evaluate(bundle())
        assert result.findings == ()
        assert len(result.rules_run) == len(RuleId)


_RANK = {
    Severity.INFORMATIONAL: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def test_the_principal_kind_vocabulary_is_the_domain_s(self=None):
    """The fact model borrows the domain's enumeration rather than keeping its own."""
    assert user(ALICE).kind is PrincipalKind.USER
    assert group(sid(1201)).kind is PrincipalKind.DOMAIN_GROUP
