r"""The engine's own types: the catalog, the two axes, the keys, the facts and the scope.

These are the invariants the rules rest on. Each assertion below is paired with the thing it
would break: a band with no severity means a finding the engine has to invent a number for; a
finding key that includes the rule version means every corrected predicate resolves its whole
back catalog overnight; a scope test that accepts *any* covered key means an incremental pass
closing findings it never examined.
"""

from __future__ import annotations

import json

import pytest

from app.contracts.v1.common import ObservationKind
from app.domain import AclLayer, PrincipalKind, SharePermission
from app.domain.errors import DomainValidationError
from app.models.schema import FINDING_KEY_LENGTH as SCHEMA_FINDING_KEY_LENGTH
from app.risk_engine import (
    CATALOG,
    DEFAULT_CONFIGURATION,
    FINDING_KEY_LENGTH,
    RULES,
    Confidence,
    ConfigurationError,
    Evidence,
    EvidenceItem,
    EvidenceKind,
    FactKind,
    FactQualifier,
    FindingSubject,
    RiskConfiguration,
    RiskFacts,
    RiskScope,
    RuleId,
    SensitiveResource,
    Severity,
    SeverityBand,
    confidence_of,
    describe_configuration,
    describe_qualifier,
    finding_key,
    only_rules,
    parse_configuration,
    rules_affected_by,
    scope_covers,
)
from app.risk_engine.configuration import load_configuration
from tests.risk_engine.support import (
    ALICE,
    EVERYONE,
    FULL_CONTROL,
    MODIFY,
    membership,
    ntfs_ace,
    resource,
    share,
    share_ace,
    sid,
    user,
)

# --------------------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------------------


class TestCatalog:
    def test_every_rule_in_the_catalog_has_an_implementation(self):
        assert set(CATALOG) == set(RULES)
        assert set(CATALOG) == set(RuleId)

    @pytest.mark.parametrize("rule_id", list(RuleId), ids=lambda item: item.value)
    def test_every_band_a_rule_declares_has_a_default_severity(self, rule_id):
        definition = CATALOG[rule_id]
        assert set(definition.bands) == set(definition.default_severities)

    @pytest.mark.parametrize("rule_id", list(RuleId), ids=lambda item: item.value)
    def test_every_rule_carries_remediation_guidance(self, rule_id):
        remediation = CATALOG[rule_id].remediation
        assert remediation.summary.strip()
        assert remediation.steps
        assert all(step.strip() for step in remediation.steps)

    @pytest.mark.parametrize("rule_id", list(RuleId), ids=lambda item: item.value)
    def test_every_rule_declares_what_it_reads(self, rule_id):
        assert CATALOG[rule_id].depends_on, "a rule that depends on nothing never re-evaluates"

    def test_only_the_sensitive_rule_requires_configuration(self):
        requiring = {rule for rule, item in CATALOG.items() if item.requires_configuration}
        assert requiring == {RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE}

    def test_the_sensitive_rule_is_enabled_and_silent_by_default(self):
        """Enabled *and* producing nothing, which cannot be mistaken for a guarantee."""
        config = DEFAULT_CONFIGURATION.for_rule(RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE)
        assert config.enabled
        assert DEFAULT_CONFIGURATION.marks_anything_sensitive is False


# --------------------------------------------------------------------------------------
# Severity and confidence
# --------------------------------------------------------------------------------------


class TestSeverityAndConfidence:
    def test_no_qualifiers_means_confirmed(self):
        assert confidence_of(()) is Confidence.CONFIRMED

    def test_confidence_is_the_weakest_qualifier_not_an_average(self):
        weakest = confidence_of([FactQualifier.ACL_DERIVED, FactQualifier.MEMBERSHIP_TRUNCATED])
        assert weakest is Confidence.POSSIBLE

    @pytest.mark.parametrize("qualifier", list(FactQualifier), ids=lambda item: item.value)
    def test_every_qualifier_can_be_explained_to_an_operator(self, qualifier):
        assert describe_qualifier(qualifier).strip().endswith(".")

    def test_severity_ranks_but_does_not_add(self):
        """There is no arithmetic on severities anywhere; the rank is for sorting only."""
        from app.risk_engine import SEVERITY_ORDER

        assert SEVERITY_ORDER[Severity.CRITICAL] > SEVERITY_ORDER[Severity.HIGH]
        assert set(SEVERITY_ORDER) == set(Severity)


# --------------------------------------------------------------------------------------
# Finding identity
# --------------------------------------------------------------------------------------


class TestFindingKeys:
    def test_a_key_is_stable_for_the_same_rule_and_subject(self):
        subject = FindingSubject(resource_key="fs01|finance", discriminator=EVERYONE)
        assert finding_key(RuleId.EVERYONE_BROAD_ACCESS, subject) == finding_key(
            RuleId.EVERYONE_BROAD_ACCESS, subject
        )

    def test_different_rules_over_one_subject_are_different_findings(self):
        subject = FindingSubject(resource_key="fs01|finance")
        assert finding_key(RuleId.EVERYONE_BROAD_ACCESS, subject) != finding_key(
            RuleId.BROKEN_INHERITANCE, subject
        )

    def test_the_discriminator_separates_two_entries_on_one_list(self):
        left = FindingSubject(resource_key="fs01|finance", discriminator="S-1-1-0")
        right = FindingSubject(resource_key="fs01|finance", discriminator="S-1-5-11")
        assert finding_key(RuleId.EVERYONE_BROAD_ACCESS, left) != finding_key(
            RuleId.EVERYONE_BROAD_ACCESS, right
        )

    def test_a_subject_naming_nothing_is_refused(self):
        with pytest.raises(DomainValidationError):
            FindingSubject()

    def test_the_key_length_matches_the_column_that_stores_it(self):
        subject = FindingSubject(resource_key="fs01|finance")
        key = finding_key(RuleId.EVERYONE_BROAD_ACCESS, subject)
        assert len(key) == FINDING_KEY_LENGTH == SCHEMA_FINDING_KEY_LENGTH

    def test_the_separator_cannot_be_forged_by_a_key_that_contains_it(self):
        r"""Two subjects must not render to one string by one containing the separator.

        A UNC path cannot contain ``\x1f``, which is why that character was chosen; the test
        pins the property rather than the reasoning.
        """
        left = FindingSubject(resource_key="a", share_key="b")
        right = FindingSubject(resource_key="a\x1fb")
        assert finding_key(RuleId.DIRECT_USER_ACE, left) != finding_key(
            RuleId.DIRECT_USER_ACE, right
        )


# --------------------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------------------


class TestScope:
    def test_a_complete_scope_covers_everything(self):
        assert scope_covers(RiskScope.everything(), FindingSubject(resource_key="anything"))

    def test_a_partial_scope_covers_what_it_names(self):
        scope = RiskScope(resource_keys=frozenset({"fs01|finance"}))
        assert scope_covers(scope, FindingSubject(resource_key="fs01|finance"))
        assert not scope_covers(scope, FindingSubject(resource_key="fs01|hr"))

    def test_every_named_key_must_be_covered_not_merely_one(self):
        """The asymmetry that keeps an incremental pass from closing what it did not examine.

        A finding naming a resource and a principal is examined only when the pass loaded
        both. Accepting either half would close findings whose other half was never read, and
        report an exposure as fixed when nothing was done about it.
        """
        scope = RiskScope(
            resource_keys=frozenset({"fs01|finance"}), principal_keys=frozenset({ALICE})
        )
        both = FindingSubject(resource_key="fs01|finance", principal_key=ALICE)
        half = FindingSubject(resource_key="fs01|finance", principal_key=sid(9999))
        assert scope_covers(scope, both)
        assert not scope_covers(scope, half)

    def test_an_axis_the_scope_does_not_restrict_covers_nothing_on_that_axis(self):
        scope = RiskScope(resource_keys=frozenset({"fs01|finance"}))
        assert not scope_covers(
            scope, FindingSubject(resource_key="fs01|finance", principal_key=ALICE)
        )


# --------------------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------------------


class TestFacts:
    def test_the_fact_kinds_match_the_contract_s_observation_kinds(self):
        """Restated rather than imported, so a contract kind added without one here fails."""
        assert {item.value for item in FactKind} == {item.value for item in ObservationKind}

    def test_a_share_entry_reported_as_a_level_converts_through_the_rights_algebra(self):
        entry = share_ace("fs01|finance", EVERYONE, permission=SharePermission.READ)
        assert not entry.rights.is_empty
        assert entry.access_mask is None

    def test_an_ntfs_entry_must_carry_a_mask(self):
        with pytest.raises(DomainValidationError):
            ntfs_ace("fs01|finance", EVERYONE, MODIFY).__class__(
                ace_key="x",
                container_key="fs01|finance",
                layer=AclLayer.NTFS,
                trustee_key=EVERYONE,
                trustee_sid=EVERYONE,
                ace_type=ntfs_ace("a", EVERYONE, MODIFY).ace_type,
                access_mask=None,
            )

    def test_a_walk_terminates_on_a_cycle(self):
        facts = _bundle_with_cycle()
        chains = list(facts.chains_down(sid(1301), 10))
        assert chains, "the walk must produce the chains it can reach"
        assert all(len(set(chain.keys)) == len(chain.keys) for chain in chains)

    def test_a_walk_reports_truncation_at_the_depth_limit(self):
        facts = _bundle_with_cycle()
        chains = list(facts.chains_down(sid(1301), 1))
        assert any(chain.truncated for chain in chains)

    def test_the_reverse_index_is_built_from_the_membership_records(self):
        facts = _bundle_with_cycle()
        assert sid(1301) in facts.direct_groups(sid(1302))

    def test_an_undelivered_entry_count_survives_being_reduced_to_one_entry(self):
        r"""The property that makes a restricted citation reproduce at the same confidence.

        Evidence cites the entries bearing on one trustee. A resource storing the *declared*
        total would then look short by the entries the evidence deliberately left out, and the
        finding would come back weaker than it was recorded.
        """
        full = resource(
            "fs01|finance",
            ntfs_ace("fs01|finance", EVERYONE, FULL_CONTROL, order=0),
            ntfs_ace("fs01|finance", ALICE, MODIFY, order=1),
            undelivered_ace_count=3,
        )
        item = EvidenceItem.for_resource(full)
        rebuilt = type(full).from_evidence(item.attributes, ())
        assert rebuilt.undelivered_ace_count == 3
        assert rebuilt.qualifiers == full.qualifiers


def _bundle_with_cycle() -> RiskFacts:
    from tests.risk_engine.support import bundle, group

    return bundle(
        principals=(group(sid(1301)), group(sid(1302)), group(sid(1303))),
        memberships=(
            membership(sid(1301), sid(1302)),
            membership(sid(1302), sid(1303)),
            membership(sid(1303), sid(1301)),
        ),
    )


# --------------------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------------------


class TestEvidence:
    def test_items_are_canonically_ordered(self):
        first = EvidenceItem.for_principal(user(ALICE))
        second = EvidenceItem.for_resource(resource("fs01|finance"))
        assert Evidence.of([first, second]).items == Evidence.of([second, first]).items

    def test_duplicates_collapse_to_the_first(self):
        record = user(ALICE, name="Alice")
        other = user(ALICE, name="Renamed")
        evidence = Evidence.of(
            [EvidenceItem.for_principal(record), EvidenceItem.for_principal(other)]
        )
        assert len(evidence) == 1
        assert evidence.items[0].attributes["display_name"] == "Alice"

    def test_an_item_needs_a_key(self):
        with pytest.raises(DomainValidationError):
            EvidenceItem(EvidenceKind.PRINCIPAL, "", {})

    def test_the_digest_is_stable_across_processes(self):
        """Hex SHA-256 over a canonical rendering, so two machines agree."""
        evidence = Evidence.of([EvidenceItem.for_principal(user(ALICE))])
        assert len(evidence.digest) == 64
        assert (
            evidence.digest == Evidence.from_json(json.loads(json.dumps(evidence.to_json()))).digest
        )

    def test_configuration_items_are_not_rebuilt_into_facts(self):
        """A finding carrying its own configuration could reproduce under settings nobody uses."""
        from app.risk_engine import rebuild_facts

        evidence = Evidence.of(
            [
                EvidenceItem.for_resource(resource("fs01|finance")),
                EvidenceItem.for_configuration("sensitive_resource:Payroll", "path_prefix=x"),
            ]
        )
        rebuilt = rebuild_facts(evidence)
        assert len(rebuilt.resources) == 1

    def test_a_share_and_its_entries_are_reassembled_by_container(self):
        from app.risk_engine import rebuild_facts

        record = share("fs01|public", share_ace("fs01|public", EVERYONE))
        evidence = Evidence.of(
            [EvidenceItem.for_share(record), *(EvidenceItem.for_ace(a) for a in record.aces)]
        )
        rebuilt = rebuild_facts(evidence)
        assert len(rebuilt.shares[0].aces) == 1


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


class TestConfiguration:
    def test_the_defaults_enable_every_rule(self):
        assert set(DEFAULT_CONFIGURATION.enabled_rules()) == set(RuleId)

    def test_a_rule_can_be_disabled(self):
        parsed = parse_configuration({"rules": {"direct_user_ace": {"enabled": False}}})
        assert RuleId.DIRECT_USER_ACE not in parsed.enabled_rules()

    def test_a_band_can_be_re_graded(self):
        parsed = parse_configuration(
            {"rules": {"everyone_broad_access": {"severities": {"read": "critical"}}}}
        )
        config = parsed.for_rule(RuleId.EVERYONE_BROAD_ACCESS)
        assert config.severity_for(SeverityBand.READ) is Severity.CRITICAL
        assert config.severity_for(SeverityBand.WRITE) is Severity.CRITICAL

    def test_an_override_for_a_band_a_rule_never_reports_is_refused(self):
        """A setting that looks like a setting and does nothing is worse than an error."""
        with pytest.raises(ConfigurationError, match="never reports band"):
            parse_configuration(
                {"rules": {"direct_user_ace": {"severities": {"null_dacl": "high"}}}}
            )

    def test_an_unknown_option_is_refused(self):
        with pytest.raises(ConfigurationError, match="does not have"):
            parse_configuration(
                {"rules": {"deep_group_nesting": {"options": {"maximum_depth": 5}}}}
            )

    def test_an_unknown_rule_is_refused(self):
        with pytest.raises(ConfigurationError, match="not a known rule"):
            parse_configuration({"rules": {"everyone_full_control": {"enabled": False}}})

    def test_an_option_of_the_wrong_type_is_refused_where_it_is_read(self):
        parsed = parse_configuration(
            {"rules": {"deep_group_nesting": {"options": {"max_depth": "three"}}}}
        )
        with pytest.raises(ConfigurationError, match="whole number"):
            parsed.for_rule(RuleId.DEEP_GROUP_NESTING).option_int("max_depth")

    def test_a_category_threshold_must_name_a_real_level(self):
        parsed = parse_configuration(
            {"rules": {"everyone_broad_access": {"options": {"minimum_category": "none"}}}}
        )
        with pytest.raises(ConfigurationError, match="cannot be a threshold"):
            parsed.for_rule(RuleId.EVERYONE_BROAD_ACCESS).option_category("minimum_category")

    def test_a_sensitive_entry_needs_exactly_one_selector(self):
        with pytest.raises(ConfigurationError, match="exactly one"):
            SensitiveResource(label="x", resource_key="a", share_key="b")
        with pytest.raises(ConfigurationError, match="exactly one"):
            SensitiveResource(label="x")

    def test_a_sensitive_entry_needs_a_label(self):
        with pytest.raises(ConfigurationError, match="label"):
            SensitiveResource(label="  ", path_prefix="x")

    def test_a_misspelled_selector_is_refused_rather_than_ignored(self):
        with pytest.raises(ConfigurationError, match="unrecognized keys"):
            parse_configuration(
                {"sensitive_resources": [{"label": "x", "path_prefixes": "\\\\FS01\\Finance"}]}
            )

    def test_a_path_prefix_stops_at_a_separator(self):
        tag = SensitiveResource(label="Payroll", path_prefix="\\\\FS01\\Finance")
        assert tag.matches_resource(resource("a", path="\\\\FS01\\Finance\\Payroll"))
        assert tag.matches_resource(resource("a", path="\\\\fs01\\finance"))
        assert not tag.matches_resource(resource("a", path="\\\\FS01\\Finance-Archive"))

    def test_a_path_prefix_never_matches_a_share(self):
        tag = SensitiveResource(label="Payroll", path_prefix="\\\\FS01\\Finance")
        assert not tag.matches_share(share("fs01|finance"))

    def test_a_missing_file_is_an_error_not_a_silent_default(self, tmp_path):
        with pytest.raises(ConfigurationError, match="could not be read"):
            load_configuration(tmp_path / "absent.json")

    def test_malformed_json_names_the_file(self, tmp_path):
        path = tmp_path / "risk.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="not valid JSON"):
            load_configuration(path)

    def test_a_valid_file_round_trips(self, tmp_path):
        path = tmp_path / "risk.json"
        path.write_text(
            json.dumps(
                {
                    "version": "2026-q3",
                    "rules": {"broken_inheritance": {"enabled": False}},
                    "sensitive_resources": [
                        {"label": "Payroll", "path_prefix": "\\\\FS01\\Finance\\Payroll"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        parsed = load_configuration(path)
        assert parsed.version == "2026-q3"
        assert RuleId.BROKEN_INHERITANCE not in parsed.enabled_rules()
        assert parsed.marks_anything_sensitive

    def test_the_summary_names_the_rules_that_are_off_and_the_absence_of_tags(self):
        lines = describe_configuration(only_rules(DEFAULT_CONFIGURATION, [RuleId.DIRECT_USER_ACE]))
        rendered = "\n".join(lines)
        assert "everyone_broad_access: disabled" in rendered
        assert "No resource is marked sensitive" in rendered

    def test_the_summary_says_when_tags_exist(self):
        config = RiskConfiguration(
            sensitive_resources=(SensitiveResource(label="Payroll", share_key="fs01|finance"),)
        )
        assert any("1 resource tags declared" in line for line in describe_configuration(config))


# --------------------------------------------------------------------------------------
# Incremental selection
# --------------------------------------------------------------------------------------


class TestRuleSelection:
    def test_a_change_selects_only_the_rules_that_read_it(self):
        selected = rules_affected_by([FactKind.NTFS_RESOURCE])
        assert RuleId.BROKEN_INHERITANCE in selected
        assert RuleId.EMPTY_PERMISSION_BEARING_GROUP not in selected

    def test_a_membership_change_selects_the_membership_rules(self):
        selected = rules_affected_by([FactKind.MEMBERSHIP_EDGE])
        assert RuleId.DEEP_GROUP_NESTING in selected
        assert RuleId.EMPTY_PERMISSION_BEARING_GROUP in selected
        assert RuleId.BROKEN_INHERITANCE not in selected

    def test_no_change_selects_no_rule(self):
        """An empty change set means nothing changed, not that everything must be re-run."""
        assert rules_affected_by([]) == ()

    def test_every_rule_is_selected_by_some_kind(self):
        every = rules_affected_by(list(FactKind))
        assert set(every) == set(RuleId)

    def test_the_principal_kind_enum_is_the_domain_s(self):
        assert user(ALICE).kind is PrincipalKind.USER
