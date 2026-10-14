"""The traversal registry, held against the taxonomy it reads."""

from __future__ import annotations

from app.domain.relationships import (
    RELATIONSHIP_TAXONOMY,
    CollectionStatus,
    DerivationStatus,
    Materialization,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipType,
)
from app.relationships import RULES, UNDERIVED
from app.traversal.semantics import (
    HOP_TYPES,
    INFORMATIONAL_TYPES,
    READING_TYPES,
    STORED_TYPES,
    TRAVERSAL_SEMANTICS,
    RelationshipClass,
    TraversalPolicy,
    TraversalRole,
    coverage_gaps,
)


class TestTheRegistry:
    def test_every_relationship_type_has_exactly_one_entry(self) -> None:
        assert set(TRAVERSAL_SEMANTICS) == set(RelationshipType)
        for relationship_type, entry in TRAVERSAL_SEMANTICS.items():
            assert entry.relationship_type is relationship_type

    def test_the_hops_are_the_membership_hops_and_ntfs_control(self) -> None:
        """Architecture §9.11: member_of, primary_group_of, local_group_member_of, and the
        control types. Nothing else is a privilege transition."""
        assert {
            RelationshipType.MEMBER_OF,
            RelationshipType.PRIMARY_GROUP_OF,
            RelationshipType.LOCAL_GROUP_MEMBER_OF,
            RelationshipType.OWNS,
            RelationshipType.GENERIC_ALL,
            RelationshipType.GENERIC_WRITE,
            RelationshipType.WRITE_DACL,
            RelationshipType.WRITE_OWNER,
        } == HOP_TYPES

    def test_the_local_specializations_are_readings_never_hops(self) -> None:
        """They are readings of the same local_group_member_of edge: following both would walk
        one fact twice."""
        assert {
            RelationshipType.LOCAL_ADMIN_OF,
            RelationshipType.REMOTE_DESKTOP_USER_OF,
            RelationshipType.REMOTE_MANAGEMENT_USER_OF,
        } == READING_TYPES
        for relationship_type in READING_TYPES:
            entry = TRAVERSAL_SEMANTICS[relationship_type]
            assert entry.reads is RelationshipType.LOCAL_GROUP_MEMBER_OF
            assert not entry.privilege_traversable
            assert RELATIONSHIP_TAXONOMY[relationship_type].target_builtin_rid is not None

    def test_remote_access_is_always_conditional_and_local_administration_is_not(self) -> None:
        assert TRAVERSAL_SEMANTICS[RelationshipType.REMOTE_DESKTOP_USER_OF].always_conditional
        assert TRAVERSAL_SEMANTICS[RelationshipType.REMOTE_MANAGEMENT_USER_OF].always_conditional
        assert not TRAVERSAL_SEMANTICS[RelationshipType.LOCAL_ADMIN_OF].always_conditional
        for relationship_type in READING_TYPES:
            label = TRAVERSAL_SEMANTICS[relationship_type].label
            assert "succe" not in label.casefold()

    def test_a_foreign_principals_domain_is_informational(self) -> None:
        entry = TRAVERSAL_SEMANTICS[RelationshipType.FOREIGN_PRINCIPAL_OF]
        assert {RelationshipType.FOREIGN_PRINCIPAL_OF} == INFORMATIONAL_TYPES
        assert entry.role is TraversalRole.INFORMATIONAL
        assert not entry.privilege_traversable
        assert not entry.reverse_meaningful

    def test_nothing_uncollected_or_unstorable_is_traversed(self) -> None:
        for relationship_type in UNDERIVED - {RelationshipType.NESTED_MEMBER_OF}:
            assert TRAVERSAL_SEMANTICS[relationship_type].role is TraversalRole.NOT_COLLECTED
        assert not UNDERIVED & STORED_TYPES
        nested = TRAVERSAL_SEMANTICS[RelationshipType.NESTED_MEMBER_OF]
        assert nested.role is TraversalRole.TRAVERSAL_ONLY
        assert (
            RELATIONSHIP_TAXONOMY[RelationshipType.NESTED_MEMBER_OF].materialization
            is Materialization.TRAVERSAL_ONLY
        )
        assert RelationshipType.TRUSTS not in STORED_TYPES

    def test_the_stored_types_are_exactly_what_a_rule_derives(self) -> None:
        produced = frozenset().union(*(spec.produces for spec in RULES.values()))
        derived = {
            relationship_type
            for relationship_type, definition in RELATIONSHIP_TAXONOMY.items()
            if definition.derivation.status is DerivationStatus.PROJECTOR
        }
        assert derived == STORED_TYPES == produced

    def test_reverse_walking_is_meaningful_for_exactly_the_hops(self) -> None:
        assert {
            item for item, entry in TRAVERSAL_SEMANTICS.items() if entry.reverse_meaningful
        } == HOP_TYPES

    def test_relationship_classes_stay_distinct(self) -> None:
        """Membership is not control, a host privilege is not a sign-in, provenance is not a
        privilege: the classes are never merged."""
        classes = {item: entry.relationship_class for item, entry in TRAVERSAL_SEMANTICS.items()}
        assert classes[RelationshipType.MEMBER_OF] is RelationshipClass.IDENTITY_MEMBERSHIP
        assert (
            classes[RelationshipType.LOCAL_GROUP_MEMBER_OF]
            is RelationshipClass.HOST_LOCAL_MEMBERSHIP
        )
        assert classes[RelationshipType.LOCAL_ADMIN_OF] is RelationshipClass.HOST_LOCAL_PRIVILEGE
        assert (
            classes[RelationshipType.REMOTE_DESKTOP_USER_OF]
            is RelationshipClass.CONDITIONAL_REMOTE_ACCESS
        )
        assert classes[RelationshipType.GENERIC_ALL] is RelationshipClass.FILE_RESOURCE_CONTROL
        assert classes[RelationshipType.FOREIGN_PRINCIPAL_OF] is RelationshipClass.INFORMATIONAL
        assert classes[RelationshipType.TRUSTS] is RelationshipClass.TRUST


class TestThePolicies:
    def test_all_current_follows_every_current_relationship_and_nothing_else(self) -> None:
        policy = TraversalPolicy.ALL_CURRENT
        for confidence in RelationshipConfidence:
            assert policy.permits(RelationshipCurrency.CURRENT, confidence)
            assert not policy.permits(RelationshipCurrency.UNKNOWN, confidence)
            assert not policy.permits(RelationshipCurrency.NOT_CURRENT, confidence)

    def test_confirmed_only_follows_confirmed_current_relationships(self) -> None:
        policy = TraversalPolicy.CONFIRMED_ONLY
        assert policy.permits(RelationshipCurrency.CURRENT, RelationshipConfidence.CONFIRMED)
        for confidence in set(RelationshipConfidence) - {RelationshipConfidence.CONFIRMED}:
            assert not policy.permits(RelationshipCurrency.CURRENT, confidence)
        assert not policy.permits(RelationshipCurrency.UNKNOWN, RelationshipConfidence.CONFIRMED)


class TestCoverage:
    def test_every_uncollected_type_is_a_stated_gap(self) -> None:
        gaps = {gap.code: gap for gap in coverage_gaps()}
        uncollected = {
            item
            for item, definition in RELATIONSHIP_TAXONOMY.items()
            if definition.collection.status is CollectionStatus.NOT_COLLECTED
        }
        assert set(gaps["relationship_types_not_collected"].relationship_types) == uncollected
        assert {
            RelationshipType.CAN_MODIFY_MEMBERSHIP,
            RelationshipType.CONTROLS_GPO,
            RelationshipType.GPO_LINKED_TO,
            RelationshipType.GPO_APPLIES_TO,
            RelationshipType.TRUSTS,
        } <= uncollected

    def test_directory_object_control_and_token_sids_are_stated(self) -> None:
        gaps = {gap.code: gap for gap in coverage_gaps()}
        assert RelationshipType.GENERIC_ALL in (
            gaps["directory_object_control_not_collected"].relationship_types
        )
        assert "implicit_token_memberships" in gaps
        assert "cross_domain_membership" in gaps
        assert "share_layer" in gaps
