"""GRAPH-01C path semantics over hand-built persisted relationships."""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import pytest

from app.domain.graph import TraversalLimits, TruncationReason
from app.domain.relationship_paths import (
    AdjacencyBatch,
    PathState,
    TRAVERSAL_SEMANTICS,
    TraversalClass,
    TraversalPolicy,
    alternate_paths,
    capability_gaps,
    explore_neighbors,
    reverse_reachability,
    shortest_path,
)
from app.domain.relationships import (
    AdjacencyDirection,
    DerivationClass,
    DerivationRule,
    EvidenceKind,
    EvidenceRole,
    EvidenceState,
    NodeKind,
    NodeRef,
    RelationshipCondition,
    RelationshipScope,
    RelationshipSupport,
    RelationshipType,
    StoredEvidence,
    StoredRelationship,
    relationship_id,
)

NOW = dt.datetime(2026, 10, 14, 12, tzinfo=dt.UTC)
RUN = UUID("00000000-0000-0000-0000-000000000001")
BASIS = "0" * 32


def principal(key: str) -> NodeRef:
    return NodeRef(NodeKind.PRINCIPAL, key)


def resource(key: str) -> NodeRef:
    return NodeRef(NodeKind.NTFS_RESOURCE, key)


def relationship(
    relationship_type: RelationshipType,
    source: NodeRef,
    target: NodeRef,
    *,
    derivation_class: DerivationClass = DerivationClass.OBSERVED,
    conditions: frozenset[RelationshipCondition] = frozenset(),
    scope: RelationshipScope | None = None,
    evidence_state: EvidenceState = EvidenceState.PRESENT,
) -> StoredRelationship:
    evidence = StoredEvidence(
        kind=(
            EvidenceKind.NTFS_ACE
            if relationship_type
            in {
                RelationshipType.GENERIC_ALL,
                RelationshipType.GENERIC_WRITE,
                RelationshipType.WRITE_DACL,
                RelationshipType.WRITE_OWNER,
            }
            else EvidenceKind.MEMBERSHIP_EDGE
        ),
        key=f"evidence:{relationship_type.value}:{source.key}:{target.key}",
        role=EvidenceRole.PREMISE,
        first_observed_at=NOW,
        first_observed_run_id=RUN,
        last_observed_at=NOW,
        last_observed_run_id=RUN,
        state=evidence_state,
    )
    support = RelationshipSupport(
        support_key=f"{relationship_type.value}:{source.key}:{target.key}".encode().hex()[:64].ljust(
            64, "0"
        ),
        derivation_class=derivation_class,
        rule=DerivationRule("test.path", 1),
        qualifiers=frozenset(),
        conditions=conditions,
        attributes={},
        basis_token=BASIS,
        derived_at=NOW,
        first_observed_at=NOW,
        first_observed_run_id=RUN,
        last_observed_at=NOW,
        last_observed_run_id=RUN,
        evidence=(evidence,),
    )
    return StoredRelationship(
        relationship_id=relationship_id(relationship_type, source, target, scope),
        relationship_type=relationship_type,
        source=source,
        target=target,
        scope=scope,
        supports=(support,),
    )


class FakeProvider:
    def __init__(self, relationships: list[StoredRelationship]) -> None:
        self.relationships = list(relationships)
        self.calls = 0

    async def adjacent_many(
        self,
        nodes: list[NodeRef] | tuple[NodeRef, ...],
        direction: AdjacencyDirection,
        *,
        types: frozenset[RelationshipType] | None,
        limit: int,
    ) -> AdjacencyBatch:
        self.calls += 1
        node_set = set(nodes)
        rows = [
            item
            for item in self.relationships
            if (types is None or item.relationship_type in types)
            and (
                item.source in node_set
                if direction is AdjacencyDirection.OUTBOUND
                else item.target in node_set
            )
        ]
        rows.sort(
            key=lambda item: (
                item.relationship_type.value,
                item.source.kind.value,
                item.source.key,
                item.target.kind.value,
                item.target.key,
                item.relationship_id,
            )
        )
        return AdjacencyBatch(tuple(rows[:limit]), truncated=len(rows) > limit)


@pytest.mark.anyio
async def test_direct_shortest_path() -> None:
    alice, admins = principal("S-1-5-21-1-1001"), principal("S-1-5-21-1-512")
    edge = relationship(RelationshipType.MEMBER_OF, alice, admins)
    result = await shortest_path(
        FakeProvider([edge]),
        alice,
        admins,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is not None
    assert result.path.nodes == (alice, admins)
    assert result.path.edges == (edge,)
    assert result.path.state is PathState.SUPPORTED


@pytest.mark.anyio
async def test_nested_membership_uses_real_direct_edges() -> None:
    alice = principal("S-1-5-21-1-1001")
    group_a = principal("S-1-5-21-1-2001")
    group_b = principal("S-1-5-21-1-2002")
    edges = [
        relationship(RelationshipType.MEMBER_OF, alice, group_a),
        relationship(RelationshipType.MEMBER_OF, group_a, group_b),
    ]
    result = await shortest_path(
        FakeProvider(edges),
        alice,
        group_b,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is not None
    assert [edge.relationship_type for edge in result.path.edges] == [
        RelationshipType.MEMBER_OF,
        RelationshipType.MEMBER_OF,
    ]
    assert RelationshipType.NESTED_MEMBER_OF not in {
        edge.relationship_type for edge in result.path.edges
    }


@pytest.mark.anyio
async def test_membership_can_continue_to_supported_ntfs_control() -> None:
    alice = principal("S-1-5-21-1-1001")
    group = principal("S-1-5-21-1-2001")
    payroll = resource(r"\\fs01\payroll")
    edges = [
        relationship(RelationshipType.MEMBER_OF, alice, group),
        relationship(
            RelationshipType.GENERIC_WRITE,
            group,
            payroll,
            derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
        ),
    ]
    result = await shortest_path(
        FakeProvider(edges),
        alice,
        payroll,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is not None
    assert result.path.hop_count == 2


@pytest.mark.anyio
async def test_equal_length_tie_is_deterministic_across_input_order() -> None:
    alice = principal("S-1-5-21-1-1001")
    a = principal("S-1-5-21-1-2001")
    b = principal("S-1-5-21-1-2002")
    target = resource(r"\\fs01\payroll")
    edges = [
        relationship(RelationshipType.MEMBER_OF, alice, a),
        relationship(RelationshipType.MEMBER_OF, alice, b),
        relationship(
            RelationshipType.WRITE_DACL,
            a,
            target,
            derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
        ),
        relationship(
            RelationshipType.WRITE_DACL,
            b,
            target,
            derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
        ),
    ]
    one = await shortest_path(
        FakeProvider(edges),
        alice,
        target,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    two = await shortest_path(
        FakeProvider(list(reversed(edges))),
        alice,
        target,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert one.path is not None and two.path is not None
    assert [edge.relationship_id for edge in one.path.edges] == [
        edge.relationship_id for edge in two.path.edges
    ]


@pytest.mark.anyio
async def test_source_equals_target_is_zero_hop_path() -> None:
    alice = principal("S-1-5-21-1-1001")
    result = await shortest_path(
        FakeProvider([]),
        alice,
        alice,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is not None
    assert result.path.hop_count == 0


@pytest.mark.anyio
async def test_cycle_terminates_without_removing_the_edges() -> None:
    a, b, target = (
        principal("S-1-5-21-1-1001"),
        principal("S-1-5-21-1-1002"),
        principal("S-1-5-21-1-1003"),
    )
    provider = FakeProvider(
        [
            relationship(RelationshipType.MEMBER_OF, a, b),
            relationship(RelationshipType.MEMBER_OF, b, a),
        ]
    )
    result = await shortest_path(
        provider,
        a,
        target,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(max_depth=8),
    )
    assert result.path is None
    assert result.diagnostics.cycle_edges_skipped
    assert provider.calls <= 2


@pytest.mark.anyio
async def test_unknown_currency_is_not_absent() -> None:
    a, b = principal("S-1-5-21-1-1001"), principal("S-1-5-21-1-1002")
    edge = relationship(
        RelationshipType.MEMBER_OF,
        a,
        b,
        evidence_state=EvidenceState.MISSING,
    )
    result = await shortest_path(
        FakeProvider([edge]),
        a,
        b,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is None
    assert edge.relationship_id in result.diagnostics.unknown_currency
    assert not result.diagnostics.complete_within_supported_graph


@pytest.mark.anyio
async def test_measured_absent_relationship_is_not_current() -> None:
    a, b = principal("S-1-5-21-1-1001"), principal("S-1-5-21-1-1002")
    edge = relationship(
        RelationshipType.MEMBER_OF,
        a,
        b,
        evidence_state=EvidenceState.ABSENT,
    )
    result = await shortest_path(
        FakeProvider([edge]),
        a,
        b,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is None
    assert result.diagnostics.unknown_currency == set()


@pytest.mark.anyio
async def test_host_local_identity_does_not_collapse_same_builtin_sid() -> None:
    user = principal("S-1-5-21-1-1001")
    host_a = principal("host-a|S-1-5-32-544")
    host_b = principal("host-b|S-1-5-32-544")
    edge = relationship(
        RelationshipType.LOCAL_GROUP_MEMBER_OF,
        user,
        host_a,
        scope=RelationshipScope.host("host-a"),
    )
    result = await shortest_path(
        FakeProvider([edge]),
        user,
        host_b,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is None


@pytest.mark.anyio
async def test_conditional_edge_makes_path_conditional() -> None:
    group = principal("S-1-5-21-1-2001")
    target = resource(r"\\fs01\payroll")
    edge = relationship(
        RelationshipType.GENERIC_WRITE,
        group,
        target,
        derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
        conditions=frozenset({RelationshipCondition.DENY_MAY_APPLY}),
    )
    result = await shortest_path(
        FakeProvider([edge]),
        group,
        target,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is not None
    assert result.path.state is PathState.CONDITIONAL
    assert result.path.conditions == (RelationshipCondition.DENY_MAY_APPLY.value,)


@pytest.mark.anyio
async def test_confirmed_only_policy_drops_conditional_edge_explicitly() -> None:
    group = principal("S-1-5-21-1-2001")
    target = resource(r"\\fs01\payroll")
    edge = relationship(
        RelationshipType.GENERIC_WRITE,
        group,
        target,
        derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
        conditions=frozenset({RelationshipCondition.DENY_MAY_APPLY}),
    )
    result = await shortest_path(
        FakeProvider([edge]),
        group,
        target,
        policy=TraversalPolicy.CONFIRMED_ONLY,
        limits=TraversalLimits(),
    )
    assert result.path is None
    assert edge.relationship_id in result.diagnostics.policy_filtered


@pytest.mark.anyio
async def test_foreign_principal_provenance_is_not_a_privilege_hop() -> None:
    foreign = principal("S-1-5-21-200-300-400-1001")
    domain = NodeRef(NodeKind.DOMAIN, "S-1-5-21-200-300-400")
    edge = relationship(RelationshipType.FOREIGN_PRINCIPAL_OF, foreign, domain)
    result = await shortest_path(
        FakeProvider([edge]),
        foreign,
        domain,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is None
    assert (
        TRAVERSAL_SEMANTICS[RelationshipType.FOREIGN_PRINCIPAL_OF].relationship_class
        is TraversalClass.INFORMATIONAL
    )


@pytest.mark.anyio
async def test_local_specializations_are_not_double_hopped() -> None:
    user = principal("S-1-5-21-1-1001")
    admins = principal("host-a|S-1-5-32-544")
    edge = relationship(
        RelationshipType.LOCAL_ADMIN_OF,
        user,
        admins,
        derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
        scope=RelationshipScope.host("host-a"),
    )
    result = await shortest_path(
        FakeProvider([edge]),
        user,
        admins,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    assert result.path is None
    assert not TRAVERSAL_SEMANTICS[RelationshipType.LOCAL_ADMIN_OF].privilege_traversable


@pytest.mark.anyio
async def test_alternate_paths_are_simple_deterministic_and_capped() -> None:
    source = principal("S-1-5-21-1-1001")
    a = principal("S-1-5-21-1-2001")
    b = principal("S-1-5-21-1-2002")
    target = principal("S-1-5-21-1-3001")
    edges = [
        relationship(RelationshipType.MEMBER_OF, source, a),
        relationship(RelationshipType.MEMBER_OF, source, b),
        relationship(RelationshipType.MEMBER_OF, a, target),
        relationship(RelationshipType.MEMBER_OF, b, target),
        relationship(RelationshipType.MEMBER_OF, a, source),
    ]
    result = await alternate_paths(
        FakeProvider(edges),
        source,
        target,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(max_paths=1),
    )
    assert len(result.paths) == 1
    assert len(set(result.paths[0].nodes)) == len(result.paths[0].nodes)
    assert TruncationReason.MAX_PATHS in result.diagnostics.truncation


@pytest.mark.anyio
async def test_reverse_reachability_returns_forward_explanations() -> None:
    alice = principal("S-1-5-21-1-1001")
    group = principal("S-1-5-21-1-2001")
    target = resource(r"\\fs01\payroll")
    edges = [
        relationship(RelationshipType.MEMBER_OF, alice, group),
        relationship(
            RelationshipType.WRITE_OWNER,
            group,
            target,
            derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
        ),
    ]
    result = await reverse_reachability(
        FakeProvider(edges),
        target,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(),
    )
    reached = {item.node: item.path for item in result.reached}
    assert alice in reached
    assert reached[alice].nodes == (alice, group, target)


@pytest.mark.anyio
async def test_neighbor_exploration_can_show_informational_edge() -> None:
    foreign = principal("S-1-5-21-200-300-400-1001")
    domain = NodeRef(NodeKind.DOMAIN, "S-1-5-21-200-300-400")
    edge = relationship(RelationshipType.FOREIGN_PRINCIPAL_OF, foreign, domain)
    result = await explore_neighbors(
        FakeProvider([edge]),
        foreign,
        directions=frozenset({AdjacencyDirection.OUTBOUND}),
        relationship_types=None,
        limits=TraversalLimits(max_depth=1),
    )
    assert result.nodes == (domain,)
    assert result.relationships == (edge,)


@pytest.mark.anyio
async def test_max_depth_is_reported_instead_of_silent_no_path() -> None:
    a = principal("S-1-5-21-1-1001")
    b = principal("S-1-5-21-1-1002")
    c = principal("S-1-5-21-1-1003")
    result = await shortest_path(
        FakeProvider(
            [
                relationship(RelationshipType.MEMBER_OF, a, b),
                relationship(RelationshipType.MEMBER_OF, b, c),
            ]
        ),
        a,
        c,
        policy=TraversalPolicy.SUPPORTED,
        limits=TraversalLimits(max_depth=1),
    )
    assert result.path is None
    assert TruncationReason.MAX_DEPTH in result.diagnostics.truncation


def test_traversal_registry_is_exhaustive_and_collection_gaps_are_explicit() -> None:
    assert set(TRAVERSAL_SEMANTICS) == set(RelationshipType)
    gaps = capability_gaps()
    assert RelationshipType.TRUSTS.value in gaps["not_collected"]
    assert RelationshipType.CONTROLS_GPO.value in gaps["not_collected"]
    assert RelationshipType.GENERIC_WRITE.value in gaps["partially_collected"]
