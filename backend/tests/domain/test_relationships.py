"""The privilege-relationship contract: identity, scoping, evidence, and what each class means.

Pure domain tests — no database. They pin the rules a stored relationship must satisfy before
the repository ever sees it, so the database's own constraints are a second guard rather than
the first (``tests/db/test_graph_relationships.py`` covers that second guard).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import replace

import pytest

from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError
from app.domain.relationships import (
    CONDITION_DESCRIPTIONS,
    QUALIFIER_DESCRIPTIONS,
    RELATIONSHIP_TAXONOMY,
    CollectionStatus,
    DerivationClass,
    DerivationRule,
    DerivationStatus,
    EvidenceKind,
    EvidenceRole,
    EvidenceState,
    Materialization,
    NodeKind,
    NodeRef,
    RelationshipAssertion,
    RelationshipCondition,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipEvidence,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipSupport,
    RelationshipType,
    StoredEvidence,
    StoredRelationship,
    confidence_of,
    currency_of,
    relationship_id,
    support_key,
)

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
FOREIGN_DOMAIN = "S-1-5-21-9-8-7"
ALICE = NodeRef.principal(f"{DOMAIN}-1104")
FINANCE_TEAM = NodeRef.principal(f"{DOMAIN}-1201")
FINANCE_RW = NodeRef.principal(f"{DOMAIN}-1202")
FS01_ADMINS = NodeRef.principal("fs01|S-1-5-32-544")
FS02_ADMINS = NodeRef.principal("fs02|S-1-5-32-544")
FS01_RDP = NodeRef.principal("fs01|S-1-5-32-555")
FINANCE_ROOT = NodeRef(NodeKind.NTFS_RESOURCE, "\\\\fs01\\finance")
RUN_1 = uuid.UUID("00000000-0000-4000-8000-000000000001")
RUN_2 = uuid.UUID("00000000-0000-4000-8000-000000000002")
T1 = dt.datetime(2026, 9, 14, 8, 0, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 9, 15, 8, 0, tzinfo=dt.UTC)
BASIS = "0123456789abcdef0123456789abcdef"
EDGE = f"{DOMAIN}-1201->{DOMAIN}-1104|directory_group_member"
RULE = DerivationRule("membership.direct_edge", 1)


def edge_evidence(
    key: str = EDGE,
    run: uuid.UUID = RUN_1,
    at: dt.datetime = T1,
    role: EvidenceRole = EvidenceRole.PREMISE,
) -> RelationshipEvidence:
    return RelationshipEvidence(EvidenceKind.MEMBERSHIP_EDGE, key, run, at, role)


def membership(**overrides: object) -> RelationshipAssertion:
    values: dict[str, object] = {
        "relationship_type": RelationshipType.MEMBER_OF,
        "source": ALICE,
        "target": FINANCE_TEAM,
        "derivation_class": DerivationClass.OBSERVED,
        "rule": RULE,
        "evidence": (edge_evidence(),),
        "basis_token": BASIS,
        "derived_at": T2,
        "attributes": {"edge_kind": "directory_group_member"},
    }
    values.update(overrides)
    return RelationshipAssertion(**values)  # type: ignore[arg-type]


def ace_evidence(key: str, run: uuid.UUID = RUN_1, at: dt.datetime = T1) -> RelationshipEvidence:
    return RelationshipEvidence(EvidenceKind.NTFS_ACE, key, run, at)


ACE_EXPLICIT = f"\\\\fs01\\finance|{DOMAIN}-1104|allow|0x00040000|0x03"
ACE_INHERITED = f"\\\\fs01\\finance|{DOMAIN}-1104|allow|0x00040000|0x13"


def write_dacl(**overrides: object) -> RelationshipAssertion:
    values: dict[str, object] = {
        "relationship_type": RelationshipType.WRITE_DACL,
        "source": ALICE,
        "target": FINANCE_ROOT,
        "derivation_class": DerivationClass.DETERMINISTICALLY_DERIVED,
        "rule": DerivationRule("acl.ntfs_write_dac", 1),
        "evidence": (ace_evidence(ACE_EXPLICIT),),
        "basis_token": BASIS,
        "derived_at": T2,
    }
    values.update(overrides)
    return RelationshipAssertion(**values)  # type: ignore[arg-type]


def stored_support(
    assertion: RelationshipAssertion,
    *,
    states: dict[str, EvidenceState] | None = None,
    last: dt.datetime | None = None,
) -> RelationshipSupport:
    states = states or {}
    observed_at, run = assertion.supported_at
    return RelationshipSupport(
        support_key=assertion.support_key,
        derivation_class=assertion.derivation_class,
        rule=assertion.rule,
        qualifiers=assertion.qualifiers,
        conditions=assertion.conditions,
        attributes=assertion.attributes,
        basis_token=assertion.basis_token,
        derived_at=assertion.derived_at,
        first_observed_at=observed_at,
        first_observed_run_id=run,
        last_observed_at=last or observed_at,
        last_observed_run_id=run,
        evidence=tuple(
            StoredEvidence(
                kind=item.kind,
                key=item.key,
                role=item.role,
                first_observed_at=item.observed_at,
                first_observed_run_id=item.run_id,
                last_observed_at=item.observed_at,
                last_observed_run_id=item.run_id,
                state=states.get(item.key, EvidenceState.PRESENT),
            )
            for item in assertion.evidence
        ),
    )


def stored(*supports: RelationshipSupport, of: RelationshipAssertion) -> StoredRelationship:
    return StoredRelationship(
        relationship_id=of.relationship_id,
        relationship_type=of.relationship_type,
        source=of.source,
        target=of.target,
        scope=of.scope,
        supports=supports,
    )


class TestTaxonomy:
    def test_every_type_is_defined(self) -> None:
        assert set(RELATIONSHIP_TAXONOMY) == set(RelationshipType)
        for kind, definition in RELATIONSHIP_TAXONOMY.items():
            assert definition.relationship_type is kind

    def test_the_initial_taxonomy_covers_the_lane_requirements(self) -> None:
        required = {
            "member_of",
            "nested_member_of",
            "primary_group_of",
            "local_group_member_of",
            "local_admin_of",
            "remote_desktop_user_of",
            "remote_management_user_of",
            "owns",
            "generic_all",
            "generic_write",
            "write_dacl",
            "write_owner",
            "can_modify_membership",
            "controls_gpo",
            "gpo_linked_to",
            "gpo_applies_to",
            "trusts",
            "foreign_principal_of",
        }
        assert required <= {kind.value for kind in RelationshipType}

    def test_evidence_kinds_are_exactly_the_contract_observation_kinds(self) -> None:
        assert [kind.value for kind in EvidenceKind] == [kind.value for kind in ObservationKind]

    def test_every_condition_and_qualifier_has_a_sentence(self) -> None:
        assert set(CONDITION_DESCRIPTIONS) == set(RelationshipCondition)
        assert set(QUALIFIER_DESCRIPTIONS) == set(RelationshipQualifier)

    def test_collection_is_stated_honestly(self) -> None:
        """What ADG collects today, pinned so a change to it is a deliberate one."""
        expected = {
            RelationshipType.MEMBER_OF: CollectionStatus.COLLECTED,
            RelationshipType.NESTED_MEMBER_OF: CollectionStatus.COLLECTED,
            RelationshipType.PRIMARY_GROUP_OF: CollectionStatus.COLLECTED,
            # Since ADG-GRAPH-01B a shipped collector reports local-group edges.
            RelationshipType.LOCAL_GROUP_MEMBER_OF: CollectionStatus.COLLECTED,
            RelationshipType.LOCAL_ADMIN_OF: CollectionStatus.COLLECTED,
            RelationshipType.REMOTE_DESKTOP_USER_OF: CollectionStatus.COLLECTED,
            RelationshipType.REMOTE_MANAGEMENT_USER_OF: CollectionStatus.COLLECTED,
            RelationshipType.OWNS: CollectionStatus.PARTIALLY_COLLECTED,
            RelationshipType.GENERIC_ALL: CollectionStatus.PARTIALLY_COLLECTED,
            RelationshipType.GENERIC_WRITE: CollectionStatus.PARTIALLY_COLLECTED,
            RelationshipType.WRITE_DACL: CollectionStatus.PARTIALLY_COLLECTED,
            RelationshipType.WRITE_OWNER: CollectionStatus.PARTIALLY_COLLECTED,
            RelationshipType.CAN_MODIFY_MEMBERSHIP: CollectionStatus.NOT_COLLECTED,
            RelationshipType.CONTROLS_GPO: CollectionStatus.NOT_COLLECTED,
            RelationshipType.GPO_LINKED_TO: CollectionStatus.NOT_COLLECTED,
            RelationshipType.GPO_APPLIES_TO: CollectionStatus.NOT_COLLECTED,
            RelationshipType.TRUSTS: CollectionStatus.NOT_COLLECTED,
            RelationshipType.FOREIGN_PRINCIPAL_OF: CollectionStatus.COLLECTED,
        }
        assert {kind: d.collection.status for kind, d in RELATIONSHIP_TAXONOMY.items()} == expected

    def test_a_type_with_no_premise_is_exactly_one_nothing_collects(self) -> None:
        for definition in RELATIONSHIP_TAXONOMY.values():
            not_collected = definition.collection.status is CollectionStatus.NOT_COLLECTED
            assert not_collected == (not definition.collection.premises)

    def test_derivation_is_stated_separately_from_collection(self) -> None:
        """What ADG derives today (ADG-GRAPH-01B), pinned so a change to it is deliberate.

        Every type with a storable premise has a projector rule; every type without one has
        none, so a defined-but-uncollected type cannot appear to have been looked for.
        """
        statuses = {
            d.relationship_type: d.derivation.status for d in RELATIONSHIP_TAXONOMY.values()
        }
        assert statuses.pop(RelationshipType.NESTED_MEMBER_OF) is DerivationStatus.QUERY_TIME
        not_implemented = {
            RelationshipType.CAN_MODIFY_MEMBERSHIP,
            RelationshipType.CONTROLS_GPO,
            RelationshipType.GPO_LINKED_TO,
            RelationshipType.GPO_APPLIES_TO,
            RelationshipType.TRUSTS,
        }
        assert {t for t, s in statuses.items() if s is DerivationStatus.NOT_IMPLEMENTED} == (
            not_implemented
        )
        assert {t for t, s in statuses.items() if s is DerivationStatus.PROJECTOR} == (
            set(statuses) - not_implemented
        )
        for relationship_type in not_implemented:
            assert not RELATIONSHIP_TAXONOMY[relationship_type].collection.premises

    def test_share_entries_are_not_a_premise_of_any_control_type(self) -> None:
        """A share permission limits the share path; it is not control of the share."""
        for kind in (
            RelationshipType.GENERIC_ALL,
            RelationshipType.GENERIC_WRITE,
            RelationshipType.WRITE_DACL,
            RelationshipType.WRITE_OWNER,
        ):
            premises = RELATIONSHIP_TAXONOMY[kind].collection.premises
            assert EvidenceKind.SMB_ACE not in premises
            assert premises == {EvidenceKind.NTFS_ACE, EvidenceKind.NTFS_RESOURCE}

    def test_only_nested_membership_is_traversal_only(self) -> None:
        traversal = {
            d.relationship_type
            for d in RELATIONSHIP_TAXONOMY.values()
            if d.materialization is Materialization.TRAVERSAL_ONLY
        }
        assert traversal == {RelationshipType.NESTED_MEMBER_OF}

    def test_acl_rights_are_never_observed(self) -> None:
        """Reading a right out of a mask is already an interpretation."""
        for kind in (
            RelationshipType.GENERIC_ALL,
            RelationshipType.GENERIC_WRITE,
            RelationshipType.WRITE_DACL,
            RelationshipType.WRITE_OWNER,
        ):
            assert DerivationClass.OBSERVED not in RELATIONSHIP_TAXONOMY[kind].allowed_classes


class TestNodeReferences:
    @pytest.mark.parametrize(
        ("kind", "key"),
        [
            (NodeKind.PRINCIPAL, f"{DOMAIN}-1104"),
            (NodeKind.PRINCIPAL, "fs01|S-1-5-32-544"),
            (NodeKind.SERVER, "fs01"),
            (NodeKind.SMB_SHARE, "fs01|finance"),
            (NodeKind.NTFS_RESOURCE, "\\\\fs01\\finance\\reports"),
            (NodeKind.DOMAIN, DOMAIN),
            (NodeKind.GPO, f"{DOMAIN}|31b2f340-016d-11d2-945f-00c04fb984f9"),
            (NodeKind.DIRECTORY_CONTAINER, "31b2f340-016d-11d2-945f-00c04fb984f9"),
        ],
    )
    def test_canonical_keys_are_accepted(self, kind: NodeKind, key: str) -> None:
        assert NodeRef(kind, key).key == key

    @pytest.mark.parametrize(
        ("kind", "key"),
        [
            # A second spelling of one principal would make two nodes of one object.
            (NodeKind.PRINCIPAL, "s-1-5-21-1004336348-1177238915-682003330-1104"),
            (NodeKind.PRINCIPAL, "S-1-05-32-544"),
            (NodeKind.PRINCIPAL, "FS01|S-1-5-32-544"),
            (NodeKind.PRINCIPAL, "Alice"),
            (NodeKind.SERVER, "FS01"),
            (NodeKind.SERVER, "fs01\\finance"),
            (NodeKind.SMB_SHARE, "fs01"),
            (NodeKind.SMB_SHARE, "fs01|Finance"),
            (NodeKind.NTFS_RESOURCE, "\\\\FS01\\Finance"),
            (NodeKind.NTFS_RESOURCE, "c:\\data"),
            (NodeKind.DOMAIN, f"{DOMAIN}-512"),
            (NodeKind.GPO, "{31B2F340-016D-11D2-945F-00C04FB984F9}"),
            (NodeKind.DIRECTORY_CONTAINER, "OU=Finance,DC=corp,DC=example"),
        ],
    )
    def test_non_canonical_keys_are_refused(self, kind: NodeKind, key: str) -> None:
        with pytest.raises(DomainValidationError):
            NodeRef(kind, key)

    def test_a_host_local_principal_names_its_host(self) -> None:
        assert FS01_ADMINS.host == "fs01"
        assert ALICE.host is None

    def test_nodes_reference_existing_tables(self) -> None:
        assert NodeKind.PRINCIPAL.canonical_table == "principals"
        assert NodeKind.NTFS_RESOURCE.canonical_table == "ntfs_resources"
        assert NodeKind.DOMAIN.canonical_table is None
        assert NodeKind.GPO.is_reserved and NodeKind.DIRECTORY_CONTAINER.is_reserved
        assert not NodeKind.DOMAIN.is_reserved


class TestStableIdentity:
    def test_the_id_is_the_digest_of_type_endpoints_and_scope(self) -> None:
        material = "\x1f".join(
            ["member_of", "principal", ALICE.key, "principal", FINANCE_TEAM.key, "", ""]
        )
        assert membership().relationship_id == hashlib.sha256(material.encode()).hexdigest()

    def test_the_id_ignores_class_rule_evidence_and_time(self) -> None:
        first = membership()
        again = membership(
            derivation_class=DerivationClass.UNRESOLVED,
            qualifiers=frozenset({RelationshipQualifier.FOREIGN_PRINCIPAL}),
            rule=DerivationRule("membership.direct_edge", 7),
            evidence=(edge_evidence(run=RUN_2, at=T2),),
            derived_at=T2 + dt.timedelta(days=3),
        )
        assert first.relationship_id == again.relationship_id

    def test_the_id_changes_with_any_identity_component(self) -> None:
        base = membership().relationship_id
        assert membership(target=FINANCE_RW).relationship_id != base
        assert membership(source=FINANCE_RW).relationship_id != base
        assert relationship_id(RelationshipType.PRIMARY_GROUP_OF, ALICE, FINANCE_TEAM, None) != base

    def test_a_support_is_the_set_of_its_premises_in_any_order(self) -> None:
        a = (EvidenceKind.NTFS_ACE, ACE_EXPLICIT)
        b = (EvidenceKind.NTFS_RESOURCE, "\\\\fs01\\finance")
        assert support_key([a, b]) == support_key([b, a]) == support_key([a, b, a])
        assert support_key([a]) != support_key([a, b])

    def test_context_evidence_does_not_change_the_support(self) -> None:
        context = RelationshipEvidence(
            EvidenceKind.PRINCIPAL, FINANCE_TEAM.key, RUN_1, T1, EvidenceRole.CONTEXT
        )
        plain = membership()
        with_context = membership(evidence=(edge_evidence(), context))
        assert plain.support_key == with_context.support_key


class TestHostScoping:
    def local_admin(self, **overrides: object) -> RelationshipAssertion:
        values: dict[str, object] = {
            "relationship_type": RelationshipType.LOCAL_ADMIN_OF,
            "source": FINANCE_RW,
            "target": FS01_ADMINS,
            "scope": RelationshipScope.host("fs01"),
            "derivation_class": DerivationClass.DETERMINISTICALLY_DERIVED,
            "rule": DerivationRule("local.builtin_administrators", 1),
            "evidence": (edge_evidence(f"fs01|S-1-5-32-544->{FINANCE_RW.key}|local_group_member"),),
        }
        values.update(overrides)
        return membership(**values)

    def test_the_same_builtin_group_on_two_hosts_is_two_relationships(self) -> None:
        fs01 = self.local_admin()
        fs02 = self.local_admin(target=FS02_ADMINS, scope=RelationshipScope.host("fs02"))
        assert fs01.relationship_id != fs02.relationship_id

    def test_a_local_relationship_must_name_its_host(self) -> None:
        with pytest.raises(DomainValidationError, match="host-scoped"):
            self.local_admin(scope=None)

    def test_the_target_must_be_local_to_the_scope_host(self) -> None:
        with pytest.raises(DomainValidationError):
            self.local_admin(scope=RelationshipScope.host("fs02"))

    def test_local_admin_is_membership_in_administrators_only(self) -> None:
        with pytest.raises(DomainValidationError, match="S-1-5-32-544"):
            self.local_admin(target=FS01_RDP)
        rdp = self.local_admin(
            relationship_type=RelationshipType.REMOTE_DESKTOP_USER_OF, target=FS01_RDP
        )
        assert rdp.scope == RelationshipScope.host("fs01")

    def test_principals_local_to_two_hosts_cannot_be_related(self) -> None:
        with pytest.raises(DomainValidationError, match="two hosts"):
            self.local_admin(source=FS02_ADMINS)

    def test_an_endpoint_scoped_type_takes_its_scope_from_the_endpoints(self) -> None:
        owner_evidence = (
            RelationshipEvidence(EvidenceKind.NTFS_RESOURCE, FINANCE_ROOT.key, RUN_1, T1),
        )
        owned = membership(
            relationship_type=RelationshipType.OWNS,
            source=FS01_ADMINS,
            target=FINANCE_ROOT,
            scope=RelationshipScope.host("fs01"),
            evidence=owner_evidence,
            attributes={},
        )
        assert owned.scope == RelationshipScope.host("fs01")
        with pytest.raises(DomainValidationError, match="scoped by its endpoints"):
            replace(owned, scope=None)
        with pytest.raises(DomainValidationError, match="scoped by its endpoints"):
            membership(scope=RelationshipScope.host("fs01"))

    def test_a_host_scope_is_case_folded(self) -> None:
        with pytest.raises(DomainValidationError):
            RelationshipScope.host("FS01")


class TestTypeValidation:
    def test_endpoint_kinds_are_checked(self) -> None:
        with pytest.raises(DomainValidationError, match="ends at"):
            membership(target=FINANCE_ROOT)
        with pytest.raises(DomainValidationError, match="starts at"):
            write_dacl(source=FINANCE_ROOT)

    def test_a_membership_cannot_be_reflexive_and_a_right_can(self) -> None:
        with pytest.raises(DomainValidationError, match="itself"):
            membership(target=ALICE)
        assert write_dacl(target=ALICE).source == ALICE

    def test_a_class_the_type_does_not_allow_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="cannot be"):
            write_dacl(derivation_class=DerivationClass.OBSERVED)
        with pytest.raises(DomainValidationError, match="cannot be"):
            membership(
                derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
                conditions=frozenset({RelationshipCondition.NO_DENY_EVALUATED}),
            )

    def test_a_traversal_only_type_is_never_asserted(self) -> None:
        with pytest.raises(DomainValidationError, match="ADR-0002"):
            membership(
                relationship_type=RelationshipType.NESTED_MEMBER_OF,
                target=FINANCE_RW,
                derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
            )

    @pytest.mark.parametrize(
        "kind",
        [
            RelationshipType.TRUSTS,
            RelationshipType.CAN_MODIFY_MEMBERSHIP,
            RelationshipType.CONTROLS_GPO,
            RelationshipType.GPO_LINKED_TO,
            RelationshipType.GPO_APPLIES_TO,
        ],
    )
    def test_an_uncollected_type_cannot_be_asserted(self, kind: RelationshipType) -> None:
        with pytest.raises(DomainValidationError, match="cannot be asserted"):
            membership(relationship_type=kind)

    def test_attributes_are_validated_against_the_type(self) -> None:
        with pytest.raises(DomainValidationError, match="does not define"):
            membership(attributes={"colour": "red"})
        with pytest.raises(DomainValidationError, match="is a str"):
            membership(attributes={"edge_kind": 3})
        # A mask of True is not a mask, although bool is an int.
        with pytest.raises(DomainValidationError, match="is a int"):
            write_dacl(attributes={"access_mask": True})
        assert write_dacl(attributes={"access_mask": 0x40000, "inherited": False}).attributes

    def test_a_rule_id_is_a_dotted_lower_case_name(self) -> None:
        with pytest.raises(DomainValidationError):
            DerivationRule("Membership Direct", 1)
        with pytest.raises(DomainValidationError):
            DerivationRule("membership.direct_edge", 0)

    def test_the_basis_token_is_required_and_shaped(self) -> None:
        with pytest.raises(DomainValidationError, match="basis_token"):
            membership(basis_token="nope")

    def test_naive_timestamps_are_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="timezone-aware"):
            edge_evidence(at=dt.datetime(2026, 9, 14, 8, 0))


class TestEvidenceLinkage:
    def test_a_relationship_rests_on_a_premise(self) -> None:
        with pytest.raises(DomainValidationError, match="premise"):
            membership(evidence=())
        with pytest.raises(DomainValidationError, match="premise"):
            membership(evidence=(edge_evidence(role=EvidenceRole.CONTEXT),))

    def test_the_premise_must_be_a_fact_the_type_rests_on(self) -> None:
        with pytest.raises(DomainValidationError, match="none of the premises"):
            membership(evidence=(ace_evidence(ACE_EXPLICIT),))

    def test_an_observed_relationship_restates_exactly_one_fact(self) -> None:
        other = edge_evidence(f"{DOMAIN}-1202->{DOMAIN}-1104|directory_group_member")
        with pytest.raises(DomainValidationError, match="exactly one"):
            membership(evidence=(edge_evidence(), other))

    def test_an_identical_repeat_collapses(self) -> None:
        assert membership(evidence=(edge_evidence(), edge_evidence())).evidence == (
            edge_evidence(),
        )

    def test_a_contradictory_repeat_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="named twice"):
            membership(evidence=(edge_evidence(), edge_evidence(run=RUN_2, at=T2)))

    def test_a_support_is_established_by_its_newest_premise(self) -> None:
        resource = RelationshipEvidence(EvidenceKind.NTFS_RESOURCE, FINANCE_ROOT.key, RUN_2, T2)
        assertion = write_dacl(evidence=(resource, ace_evidence(ACE_EXPLICIT)))
        assert assertion.supported_at == (T2, RUN_2)
        reordered = write_dacl(evidence=(ace_evidence(ACE_EXPLICIT), resource))
        assert reordered.evidence == assertion.evidence

    def test_context_does_not_establish_a_support(self) -> None:
        late_context = RelationshipEvidence(
            EvidenceKind.PRINCIPAL, FINANCE_TEAM.key, RUN_2, T2, EvidenceRole.CONTEXT
        )
        assert membership(evidence=(edge_evidence(), late_context)).supported_at == (T1, RUN_1)


class TestObservedAndDerivedSemantics:
    def test_conditions_belong_to_conditional_supports_only(self) -> None:
        with pytest.raises(DomainValidationError, match="conditions"):
            write_dacl(derivation_class=DerivationClass.CONDITIONALLY_DERIVED)
        with pytest.raises(DomainValidationError, match="conditions"):
            write_dacl(conditions=frozenset({RelationshipCondition.NO_DENY_EVALUATED}))
        conditional = write_dacl(
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=frozenset({RelationshipCondition.INHERITANCE_UNRESOLVED}),
        )
        assert conditional.conditions

    @pytest.mark.parametrize(
        ("derivation_class", "qualifiers", "expected"),
        [
            (DerivationClass.OBSERVED, set(), RelationshipConfidence.CONFIRMED),
            (DerivationClass.DETERMINISTICALLY_DERIVED, set(), RelationshipConfidence.CONFIRMED),
            (DerivationClass.CONDITIONALLY_DERIVED, set(), RelationshipConfidence.PROBABLE),
            (DerivationClass.UNRESOLVED, set(), RelationshipConfidence.INDETERMINATE),
            (
                DerivationClass.OBSERVED,
                {RelationshipQualifier.EVIDENCE_FROM_PARTIAL_RUN},
                RelationshipConfidence.POSSIBLE,
            ),
            (
                DerivationClass.OBSERVED,
                {RelationshipQualifier.FOREIGN_PRINCIPAL},
                RelationshipConfidence.INDETERMINATE,
            ),
            (
                DerivationClass.CONDITIONALLY_DERIVED,
                {RelationshipQualifier.DERIVATION_TRUNCATED},
                RelationshipConfidence.POSSIBLE,
            ),
        ],
    )
    def test_confidence_is_the_weakest_of_class_and_qualifiers(
        self,
        derivation_class: DerivationClass,
        qualifiers: set[RelationshipQualifier],
        expected: RelationshipConfidence,
    ) -> None:
        assert confidence_of(derivation_class, qualifiers) is expected

    def test_currency_never_folds_unknown_into_not_current(self) -> None:
        assert currency_of([RelationshipCurrency.UNKNOWN]) is RelationshipCurrency.UNKNOWN
        assert (
            currency_of([RelationshipCurrency.NOT_CURRENT, RelationshipCurrency.UNKNOWN])
            is RelationshipCurrency.UNKNOWN
        )
        assert currency_of([RelationshipCurrency.NOT_CURRENT]) is RelationshipCurrency.NOT_CURRENT
        assert (
            currency_of([RelationshipCurrency.NOT_CURRENT, RelationshipCurrency.CURRENT])
            is RelationshipCurrency.CURRENT
        )

    def test_a_support_with_a_missing_premise_is_unknown_not_gone(self) -> None:
        support = stored_support(membership(), states={EDGE: EvidenceState.MISSING})
        assert support.currency is RelationshipCurrency.UNKNOWN
        assert stored_support(membership()).currency is RelationshipCurrency.CURRENT
        gone = stored_support(membership(), states={EDGE: EvidenceState.ABSENT})
        assert gone.currency is RelationshipCurrency.NOT_CURRENT

    def test_an_absent_context_does_not_end_a_support(self) -> None:
        context = RelationshipEvidence(
            EvidenceKind.PRINCIPAL, FINANCE_TEAM.key, RUN_1, T1, EvidenceRole.CONTEXT
        )
        assertion = membership(evidence=(edge_evidence(), context))
        support = stored_support(assertion, states={FINANCE_TEAM.key: EvidenceState.ABSENT})
        assert support.currency is RelationshipCurrency.CURRENT

    def test_one_removed_entry_does_not_end_a_relationship_another_still_supports(self) -> None:
        explicit = write_dacl()
        inherited = write_dacl(
            evidence=(ace_evidence(ACE_INHERITED),),
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=frozenset({RelationshipCondition.INHERITANCE_UNRESOLVED}),
        )
        relationship = stored(
            stored_support(explicit, states={ACE_EXPLICIT: EvidenceState.ABSENT}),
            stored_support(inherited),
            of=explicit,
        )
        assert relationship.currency is RelationshipCurrency.CURRENT
        # The relationship is as strong as its strongest *current* support.
        assert relationship.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        assert relationship.confidence is RelationshipConfidence.PROBABLE

        both_gone = stored(
            stored_support(explicit, states={ACE_EXPLICIT: EvidenceState.ABSENT}),
            stored_support(inherited, states={ACE_INHERITED: EvidenceState.ABSENT}),
            of=explicit,
        )
        assert both_gone.currency is RelationshipCurrency.NOT_CURRENT

    def test_the_strongest_current_support_wins(self) -> None:
        explicit = write_dacl()
        inherited = write_dacl(
            evidence=(ace_evidence(ACE_INHERITED),),
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=frozenset({RelationshipCondition.INHERITANCE_UNRESOLVED}),
        )
        relationship = stored(stored_support(inherited), stored_support(explicit), of=explicit)
        assert relationship.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED
        assert relationship.confidence is RelationshipConfidence.CONFIRMED

    def test_the_observation_window_spans_every_support(self) -> None:
        explicit = write_dacl()
        inherited = write_dacl(evidence=(ace_evidence(ACE_INHERITED, RUN_2, T2),))
        relationship = stored(stored_support(explicit), stored_support(inherited), of=explicit)
        assert relationship.first_observed_at == T1
        assert relationship.last_observed_at == T2


class TestUnresolved:
    def foreign_member(self, **overrides: object) -> RelationshipAssertion:
        foreign = NodeRef.principal(f"{FOREIGN_DOMAIN}-1105")
        values: dict[str, object] = {
            "source": foreign,
            "derivation_class": DerivationClass.UNRESOLVED,
            "qualifiers": frozenset({RelationshipQualifier.FOREIGN_PRINCIPAL}),
            "evidence": (edge_evidence(f"{DOMAIN}-1201->{foreign.key}|directory_group_member"),),
        }
        values.update(overrides)
        return membership(**values)

    def test_an_unresolved_support_says_what_is_unresolved(self) -> None:
        with pytest.raises(DomainValidationError, match="unresolved"):
            self.foreign_member(qualifiers=frozenset())
        with pytest.raises(DomainValidationError, match="unresolved"):
            self.foreign_member(
                qualifiers=frozenset({RelationshipQualifier.EVIDENCE_FROM_PARTIAL_RUN})
            )

    def test_an_unresolved_relationship_exists_and_is_indeterminate(self) -> None:
        assertion = self.foreign_member()
        relationship = stored(stored_support(assertion), of=assertion)
        assert relationship.currency is RelationshipCurrency.CURRENT
        assert relationship.confidence is RelationshipConfidence.INDETERMINATE
        text = relationship.explanation()
        assert "exists" in text and "cannot be established" in text
        assert "foreign security principal" in text

    def test_a_foreign_principal_is_issued_by_its_own_sid_prefix(self) -> None:
        foreign = NodeRef.principal(f"{FOREIGN_DOMAIN}-1105")
        evidence = (RelationshipEvidence(EvidenceKind.PRINCIPAL, foreign.key, RUN_1, T1),)
        issued = membership(
            relationship_type=RelationshipType.FOREIGN_PRINCIPAL_OF,
            source=foreign,
            target=NodeRef(NodeKind.DOMAIN, FOREIGN_DOMAIN),
            derivation_class=DerivationClass.UNRESOLVED,
            qualifiers=frozenset({RelationshipQualifier.FOREIGN_PRINCIPAL}),
            evidence=evidence,
            attributes={},
        )
        assert issued.target.key == FOREIGN_DOMAIN
        with pytest.raises(DomainValidationError, match="SID's own prefix"):
            replace(issued, target=NodeRef(NodeKind.DOMAIN, DOMAIN))


class TestDeterministicSerialization:
    def test_storage_order_does_not_change_the_rendering(self) -> None:
        explicit = write_dacl()
        inherited = write_dacl(evidence=(ace_evidence(ACE_INHERITED, RUN_2, T2),))
        one = stored(stored_support(explicit), stored_support(inherited), of=explicit)
        other = stored(stored_support(inherited), stored_support(explicit), of=explicit)
        assert one.to_json() == other.to_json()
        assert (
            one.as_dict()["supports"][0]["support_key"]
            < one.as_dict()["supports"][1]["support_key"]
        )

    def test_the_rendering_is_canonical_json(self) -> None:
        assertion = membership()
        rendered = stored(stored_support(assertion), of=assertion).to_json()
        assert rendered == json.dumps(json.loads(rendered), sort_keys=True, separators=(",", ":"))
        assert json.loads(rendered)["supports"][0]["evidence"][0]["key"] == EDGE

    def test_the_explanation_uses_labels_and_falls_back_to_keys(self) -> None:
        assertion = membership()
        relationship = stored(stored_support(assertion), of=assertion)
        assert relationship.explanation().startswith(f"{ALICE.key} is a direct member of")
        labelled = relationship.explanation({ALICE: "Alice", FINANCE_TEAM: "Finance-Team"})
        assert labelled.startswith("Alice is a direct member of Finance-Team.")
        assert "Observed: one collected membership_edge states it." in labelled

    def test_a_conditional_explanation_names_its_conditions(self) -> None:
        assertion = write_dacl(
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=frozenset(
                {
                    RelationshipCondition.NO_DENY_EVALUATED,
                    RelationshipCondition.INHERITANCE_UNRESOLVED,
                }
            ),
        )
        text = stored(stored_support(assertion), of=assertion).explanation()
        assert "holds only if" in text
        assert CONDITION_DESCRIPTIONS[RelationshipCondition.NO_DENY_EVALUATED] in text

    def test_an_explanation_says_when_currency_is_unknown(self) -> None:
        assertion = membership()
        missing = stored(
            stored_support(assertion, states={EDGE: EvidenceState.MISSING}), of=assertion
        )
        assert "unknown" in missing.explanation()

    def test_attributes_are_stored_in_key_order(self) -> None:
        assertion = write_dacl(attributes={"inherited": False, "access_mask": 0x40000})
        assert list(assertion.attributes) == ["access_mask", "inherited"]


class TestRetirement:
    """A support whose facts are still stored and no longer establish it (ADG-GRAPH-01B)."""

    def retired(self, support: RelationshipSupport) -> RelationshipSupport:
        return replace(support, retired_at=T2, retired_basis_token=BASIS)

    def test_a_retired_support_is_not_current_although_every_premise_is_present(self) -> None:
        assertion = write_dacl()
        support = self.retired(stored_support(assertion))
        assert {item.state for item in support.evidence} == {EvidenceState.PRESENT}
        assert support.currency is RelationshipCurrency.NOT_CURRENT
        relationship = stored(support, of=assertion)
        assert relationship.currency is RelationshipCurrency.NOT_CURRENT
        text = relationship.explanation()
        assert "still stored" in text and "no longer establish it" in text
        assert "recorded its evidence gone" not in text

    def test_a_retirement_is_attributed_or_refused(self) -> None:
        support = stored_support(write_dacl())
        with pytest.raises(DomainValidationError, match="both"):
            replace(support, retired_at=T2)
        with pytest.raises(DomainValidationError, match="both"):
            replace(support, retired_basis_token=BASIS)

    def test_another_current_support_keeps_the_relationship_current(self) -> None:
        explicit = write_dacl()
        inherited = write_dacl(evidence=(ace_evidence(ACE_INHERITED, RUN_2, T2),))
        relationship = stored(
            self.retired(stored_support(explicit)), stored_support(inherited), of=explicit
        )
        assert relationship.currency is RelationshipCurrency.CURRENT
        assert relationship.best_support.support_key == inherited.support_key

    def test_retirement_never_turns_unknown_into_absent_for_other_supports(self) -> None:
        explicit = write_dacl()
        inherited = write_dacl(evidence=(ace_evidence(ACE_INHERITED, RUN_2, T2),))
        relationship = stored(
            self.retired(stored_support(explicit)),
            stored_support(inherited, states={ACE_INHERITED: EvidenceState.MISSING}),
            of=explicit,
        )
        assert relationship.currency is RelationshipCurrency.UNKNOWN

    def test_the_rendering_carries_the_retirement(self) -> None:
        assertion = write_dacl()
        rendered = stored(self.retired(stored_support(assertion)), of=assertion).as_dict()
        (support,) = rendered["supports"]
        assert support["retired_basis_token"] == BASIS
        assert support["retired_at"] == T2.isoformat(timespec="microseconds")
        assert support["currency"] == "not_current"
