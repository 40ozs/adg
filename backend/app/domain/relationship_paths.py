"""Bounded traversal over the persisted privilege-relationship graph.

GRAPH-01A/B made relationship identity, direction, currency, evidence and host scope
authoritative. This module does not infer new edges. It only walks current stored
relationships that an explicit traversal policy says have privilege-path meaning.

The algorithms are deliberately SQL-free. A provider supplies batched adjacency so tests can
exercise determinism, limits, cycles and conditional evidence independently of PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol

from app.domain.graph import TraversalLimits, TruncationReason
from app.domain.relationships import (
    AdjacencyDirection,
    CollectionStatus,
    DerivationClass,
    NodeRef,
    RELATIONSHIP_TAXONOMY,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipType,
    StoredRelationship,
)


class TraversalPolicy(StrEnum):
    """Which current stored relationships a privilege search may follow."""

    SUPPORTED = "supported"
    CONFIRMED_ONLY = "confirmed_only"


class TraversalClass(StrEnum):
    MEMBERSHIP = "membership"
    FILE_CONTROL = "file_control"
    SEMANTIC_ALIAS = "semantic_alias"
    CONDITIONAL_ALIAS = "conditional_alias"
    INFORMATIONAL = "informational"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class TraversalSemantics:
    relationship_type: RelationshipType
    relationship_class: TraversalClass
    privilege_traversable: bool
    reverse_traversable: bool
    neighbor_visible: bool = True
    note: str = ""


def _semantics() -> Mapping[RelationshipType, TraversalSemantics]:
    membership = {
        RelationshipType.MEMBER_OF,
        RelationshipType.PRIMARY_GROUP_OF,
        RelationshipType.LOCAL_GROUP_MEMBER_OF,
    }
    control = {
        RelationshipType.OWNS,
        RelationshipType.GENERIC_ALL,
        RelationshipType.GENERIC_WRITE,
        RelationshipType.WRITE_DACL,
        RelationshipType.WRITE_OWNER,
    }
    semantic_aliases = {RelationshipType.LOCAL_ADMIN_OF}
    conditional_aliases = {
        RelationshipType.REMOTE_DESKTOP_USER_OF,
        RelationshipType.REMOTE_MANAGEMENT_USER_OF,
    }
    informational = {RelationshipType.FOREIGN_PRINCIPAL_OF}
    result: dict[RelationshipType, TraversalSemantics] = {}
    for relationship_type in RelationshipType:
        if relationship_type in membership:
            result[relationship_type] = TraversalSemantics(
                relationship_type,
                TraversalClass.MEMBERSHIP,
                True,
                True,
                note="Direct membership hop. Nested membership is the path of these stored edges.",
            )
        elif relationship_type in control:
            result[relationship_type] = TraversalSemantics(
                relationship_type,
                TraversalClass.FILE_CONTROL,
                True,
                True,
                note="Current NTFS control relationship with exactly the meaning GRAPH-01B derived.",
            )
        elif relationship_type in semantic_aliases:
            result[relationship_type] = TraversalSemantics(
                relationship_type,
                TraversalClass.SEMANTIC_ALIAS,
                False,
                False,
                note=(
                    "A specialization of local_group_member_of. Exposed to neighbor/evidence "
                    "queries but not followed again as a second privilege hop."
                ),
            )
        elif relationship_type in conditional_aliases:
            result[relationship_type] = TraversalSemantics(
                relationship_type,
                TraversalClass.CONDITIONAL_ALIAS,
                False,
                False,
                note=(
                    "Conditional specialization of local-group membership. The underlying "
                    "local_group_member_of hop is traversed; this alias remains visible evidence."
                ),
            )
        elif relationship_type in informational:
            result[relationship_type] = TraversalSemantics(
                relationship_type,
                TraversalClass.INFORMATIONAL,
                False,
                False,
                note="Domain/provenance information, not a privilege transition.",
            )
        else:
            result[relationship_type] = TraversalSemantics(
                relationship_type,
                TraversalClass.UNSUPPORTED,
                False,
                False,
                note="No supported current privilege traversal semantics in GRAPH-01C.",
            )
    return result


TRAVERSAL_SEMANTICS: Final[Mapping[RelationshipType, TraversalSemantics]] = _semantics()
"""The single backend-owned traversal registry. It is exhaustive over RelationshipType."""

PRIVILEGE_RELATIONSHIP_TYPES: Final[frozenset[RelationshipType]] = frozenset(
    relationship_type
    for relationship_type, semantics in TRAVERSAL_SEMANTICS.items()
    if semantics.privilege_traversable
)

if set(TRAVERSAL_SEMANTICS) != set(RelationshipType):  # pragma: no cover - import-time guard
    raise RuntimeError("Traversal semantics must classify every relationship type.")


@dataclass(frozen=True, slots=True)
class AdjacencyBatch:
    items: tuple[StoredRelationship, ...]
    truncated: bool = False


class RelationshipAdjacencyProvider(Protocol):
    async def adjacent_many(
        self,
        nodes: Sequence[NodeRef],
        direction: AdjacencyDirection,
        *,
        types: frozenset[RelationshipType] | None,
        limit: int,
    ) -> AdjacencyBatch:
        """Return a deterministic bounded batch touching ``nodes`` in ``direction``."""
        ...


class PathState(StrEnum):
    SUPPORTED = "supported"
    CONDITIONAL = "conditional"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class RelationshipPath:
    nodes: tuple[NodeRef, ...]
    edges: tuple[StoredRelationship, ...]

    @property
    def hop_count(self) -> int:
        return len(self.edges)

    @property
    def state(self) -> PathState:
        if any(
            edge.confidence
            in {RelationshipConfidence.POSSIBLE, RelationshipConfidence.INDETERMINATE}
            for edge in self.edges
        ):
            return PathState.INCOMPLETE
        if any(
            edge.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
            or bool(edge.best_support.conditions)
            or edge.confidence is RelationshipConfidence.PROBABLE
            for edge in self.edges
        ):
            return PathState.CONDITIONAL
        return PathState.SUPPORTED

    @property
    def conditions(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    condition.value
                    for edge in self.edges
                    for condition in edge.best_support.conditions
                }
            )
        )


@dataclass(slots=True)
class TraversalDiagnostics:
    limits: TraversalLimits
    depth_reached: int = 0
    edges_read: int = 0
    nodes_reached: int = 0
    cycle_edges_skipped: int = 0
    truncation: set[TruncationReason] = field(default_factory=set)
    unknown_currency: set[str] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    policy_filtered: set[str] = field(default_factory=set)

    @property
    def complete_within_supported_graph(self) -> bool:
        return not (self.truncation or self.unknown_currency or self.unresolved)


@dataclass(frozen=True, slots=True)
class ShortestPathResult:
    source: NodeRef
    target: NodeRef
    path: RelationshipPath | None
    diagnostics: TraversalDiagnostics


@dataclass(frozen=True, slots=True)
class AlternatePathsResult:
    source: NodeRef
    target: NodeRef
    paths: tuple[RelationshipPath, ...]
    diagnostics: TraversalDiagnostics


@dataclass(frozen=True, slots=True)
class ReachedPath:
    node: NodeRef
    path: RelationshipPath


@dataclass(frozen=True, slots=True)
class ReverseReachabilityResult:
    target: NodeRef
    reached: tuple[ReachedPath, ...]
    diagnostics: TraversalDiagnostics


@dataclass(frozen=True, slots=True)
class NeighborResult:
    root: NodeRef
    nodes: tuple[NodeRef, ...]
    relationships: tuple[StoredRelationship, ...]
    diagnostics: TraversalDiagnostics


def relationship_sort_key(
    relationship: StoredRelationship,
) -> tuple[str, str, str, str, str, str, str, str]:
    scope_kind = "" if relationship.scope is None else relationship.scope.kind.value
    scope_key = "" if relationship.scope is None else relationship.scope.key
    return (
        relationship.relationship_type.value,
        relationship.source.kind.value,
        relationship.source.key,
        relationship.target.kind.value,
        relationship.target.key,
        scope_kind,
        scope_key,
        relationship.relationship_id,
    )


def node_sort_key(node: NodeRef) -> tuple[str, str]:
    return (node.kind.value, node.key)


def _path_tie_key(path: RelationshipPath) -> tuple[tuple[str, ...], ...]:
    return tuple(relationship_sort_key(edge) for edge in path.edges)


def _is_eligible(
    relationship: StoredRelationship,
    policy: TraversalPolicy,
    diagnostics: TraversalDiagnostics,
) -> bool:
    if relationship.currency is RelationshipCurrency.NOT_CURRENT:
        return False
    if relationship.currency is RelationshipCurrency.UNKNOWN:
        diagnostics.unknown_currency.add(relationship.relationship_id)
        return False
    semantics = TRAVERSAL_SEMANTICS[relationship.relationship_type]
    if not semantics.privilege_traversable:
        return False
    if relationship.confidence is RelationshipConfidence.INDETERMINATE:
        diagnostics.unresolved.add(relationship.relationship_id)
        return False
    if policy is TraversalPolicy.CONFIRMED_ONLY and (
        relationship.confidence is not RelationshipConfidence.CONFIRMED
        or relationship.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        or bool(relationship.best_support.conditions)
    ):
        diagnostics.policy_filtered.add(relationship.relationship_id)
        return False
    return True


def _next_node(relationship: StoredRelationship, direction: AdjacencyDirection) -> NodeRef:
    return (
        relationship.target
        if direction is AdjacencyDirection.OUTBOUND
        else relationship.source
    )


def _origin_node(relationship: StoredRelationship, direction: AdjacencyDirection) -> NodeRef:
    return (
        relationship.source
        if direction is AdjacencyDirection.OUTBOUND
        else relationship.target
    )


def _reconstruct_forward(
    source: NodeRef,
    target: NodeRef,
    parents: Mapping[NodeRef, tuple[NodeRef, StoredRelationship]],
) -> RelationshipPath:
    nodes = [target]
    edges: list[StoredRelationship] = []
    cursor = target
    while cursor != source:
        previous, relationship = parents[cursor]
        edges.append(relationship)
        nodes.append(previous)
        cursor = previous
    nodes.reverse()
    edges.reverse()
    return RelationshipPath(tuple(nodes), tuple(edges))


def _reconstruct_reverse(
    source: NodeRef,
    target: NodeRef,
    toward_target: Mapping[NodeRef, tuple[NodeRef, StoredRelationship]],
) -> RelationshipPath:
    nodes = [source]
    edges: list[StoredRelationship] = []
    cursor = source
    while cursor != target:
        next_node, relationship = toward_target[cursor]
        edges.append(relationship)
        nodes.append(next_node)
        cursor = next_node
    return RelationshipPath(tuple(nodes), tuple(edges))


async def shortest_path(
    provider: RelationshipAdjacencyProvider,
    source: NodeRef,
    target: NodeRef,
    *,
    policy: TraversalPolicy,
    limits: TraversalLimits,
) -> ShortestPathResult:
    diagnostics = TraversalDiagnostics(limits=limits)
    if source == target:
        diagnostics.nodes_reached = 1
        return ShortestPathResult(source, target, RelationshipPath((source,), ()), diagnostics)

    parents: dict[NodeRef, tuple[NodeRef, StoredRelationship]] = {}
    path_keys: dict[NodeRef, tuple[tuple[str, ...], ...]] = {source: ()}
    visited: set[NodeRef] = {source}
    frontier: list[NodeRef] = [source]

    for depth in range(limits.max_depth):
        if not frontier:
            break
        remaining_edges = limits.max_edges - diagnostics.edges_read
        if remaining_edges <= 0:
            diagnostics.truncation.add(TruncationReason.MAX_EDGES)
            break
        batch = await provider.adjacent_many(
            sorted(frontier, key=node_sort_key),
            AdjacencyDirection.OUTBOUND,
            types=PRIVILEGE_RELATIONSHIP_TYPES,
            limit=remaining_edges,
        )
        diagnostics.edges_read += len(batch.items)
        if batch.truncated:
            diagnostics.truncation.add(TruncationReason.MAX_EDGES)

        candidates: list[
            tuple[
                tuple[tuple[str, ...], ...],
                tuple[str, ...],
                NodeRef,
                NodeRef,
                StoredRelationship,
            ]
        ] = []
        frontier_set = set(frontier)
        for relationship in batch.items:
            origin = _origin_node(relationship, AdjacencyDirection.OUTBOUND)
            if origin not in frontier_set:
                continue
            if not _is_eligible(relationship, policy, diagnostics):
                continue
            next_node = relationship.target
            edge_key = relationship_sort_key(relationship)
            candidates.append((path_keys[origin], edge_key, origin, next_node, relationship))
        candidates.sort(key=lambda item: (item[0], item[1], node_sort_key(item[3])))

        next_frontier: list[NodeRef] = []
        for prefix, edge_key, origin, next_node, relationship in candidates:
            if next_node in visited:
                diagnostics.cycle_edges_skipped += 1
                continue
            if len(visited) >= limits.max_nodes:
                diagnostics.truncation.add(TruncationReason.MAX_NODES)
                break
            visited.add(next_node)
            parents[next_node] = (origin, relationship)
            path_keys[next_node] = (*prefix, edge_key)
            if next_node == target:
                diagnostics.depth_reached = depth + 1
                diagnostics.nodes_reached = len(visited)
                return ShortestPathResult(
                    source,
                    target,
                    _reconstruct_forward(source, target, parents),
                    diagnostics,
                )
            next_frontier.append(next_node)

        diagnostics.depth_reached = depth + 1
        if diagnostics.truncation:
            break
        frontier = sorted(next_frontier, key=lambda n: (path_keys[n], node_sort_key(n)))
    else:
        if frontier:
            diagnostics.truncation.add(TruncationReason.MAX_DEPTH)

    diagnostics.nodes_reached = len(visited)
    return ShortestPathResult(source, target, None, diagnostics)


async def alternate_paths(
    provider: RelationshipAdjacencyProvider,
    source: NodeRef,
    target: NodeRef,
    *,
    policy: TraversalPolicy,
    limits: TraversalLimits,
) -> AlternatePathsResult:
    diagnostics = TraversalDiagnostics(limits=limits)
    if source == target:
        diagnostics.nodes_reached = 1
        return AlternatePathsResult(source, target, (RelationshipPath((source,), ()),), diagnostics)

    states: list[RelationshipPath] = [RelationshipPath((source,), ())]
    found: list[RelationshipPath] = []
    found_keys: set[tuple[str, ...]] = set()
    unique_nodes: set[NodeRef] = {source}

    for depth in range(limits.max_depth):
        if not states:
            break
        tails = sorted({state.nodes[-1] for state in states}, key=node_sort_key)
        remaining_edges = limits.max_edges - diagnostics.edges_read
        if remaining_edges <= 0:
            diagnostics.truncation.add(TruncationReason.MAX_EDGES)
            break
        batch = await provider.adjacent_many(
            tails,
            AdjacencyDirection.OUTBOUND,
            types=PRIVILEGE_RELATIONSHIP_TYPES,
            limit=remaining_edges,
        )
        diagnostics.edges_read += len(batch.items)
        if batch.truncated:
            diagnostics.truncation.add(TruncationReason.MAX_EDGES)

        by_origin: dict[NodeRef, list[StoredRelationship]] = {}
        for relationship in batch.items:
            by_origin.setdefault(relationship.source, []).append(relationship)
        for relationships in by_origin.values():
            relationships.sort(key=relationship_sort_key)

        next_states: list[RelationshipPath] = []
        for state in sorted(states, key=_path_tie_key):
            tail = state.nodes[-1]
            for relationship in by_origin.get(tail, ()):
                if not _is_eligible(relationship, policy, diagnostics):
                    continue
                next_node = relationship.target
                if next_node in state.nodes:
                    diagnostics.cycle_edges_skipped += 1
                    continue
                if next_node not in unique_nodes and len(unique_nodes) >= limits.max_nodes:
                    diagnostics.truncation.add(TruncationReason.MAX_NODES)
                    break
                unique_nodes.add(next_node)
                candidate = RelationshipPath((*state.nodes, next_node), (*state.edges, relationship))
                if next_node == target:
                    key = tuple(edge.relationship_id for edge in candidate.edges)
                    if key not in found_keys:
                        found_keys.add(key)
                        found.append(candidate)
                        if len(found) >= limits.max_paths:
                            diagnostics.truncation.add(TruncationReason.MAX_PATHS)
                            break
                else:
                    next_states.append(candidate)
            if TruncationReason.MAX_PATHS in diagnostics.truncation:
                break
            if TruncationReason.MAX_NODES in diagnostics.truncation:
                break

        diagnostics.depth_reached = depth + 1
        if diagnostics.truncation:
            break
        states = sorted(next_states, key=_path_tie_key)
    else:
        if states:
            diagnostics.truncation.add(TruncationReason.MAX_DEPTH)

    diagnostics.nodes_reached = len(unique_nodes)
    found.sort(key=lambda path: (path.hop_count, _path_tie_key(path)))
    return AlternatePathsResult(source, target, tuple(found), diagnostics)


async def reverse_reachability(
    provider: RelationshipAdjacencyProvider,
    target: NodeRef,
    *,
    policy: TraversalPolicy,
    limits: TraversalLimits,
) -> ReverseReachabilityResult:
    diagnostics = TraversalDiagnostics(limits=limits)
    toward_target: dict[NodeRef, tuple[NodeRef, StoredRelationship]] = {}
    path_keys: dict[NodeRef, tuple[tuple[str, ...], ...]] = {target: ()}
    visited: set[NodeRef] = {target}
    frontier: list[NodeRef] = [target]
    reached_depth: dict[NodeRef, int] = {}

    for depth in range(limits.max_depth):
        if not frontier:
            break
        remaining_edges = limits.max_edges - diagnostics.edges_read
        if remaining_edges <= 0:
            diagnostics.truncation.add(TruncationReason.MAX_EDGES)
            break
        batch = await provider.adjacent_many(
            sorted(frontier, key=node_sort_key),
            AdjacencyDirection.INBOUND,
            types=PRIVILEGE_RELATIONSHIP_TYPES,
            limit=remaining_edges,
        )
        diagnostics.edges_read += len(batch.items)
        if batch.truncated:
            diagnostics.truncation.add(TruncationReason.MAX_EDGES)
        frontier_set = set(frontier)
        candidates: list[
            tuple[
                tuple[tuple[str, ...], ...],
                tuple[str, ...],
                NodeRef,
                NodeRef,
                StoredRelationship,
            ]
        ] = []
        for relationship in batch.items:
            closer = relationship.target
            if closer not in frontier_set:
                continue
            if not _is_eligible(relationship, policy, diagnostics):
                continue
            source = relationship.source
            edge_key = relationship_sort_key(relationship)
            candidates.append((path_keys[closer], edge_key, closer, source, relationship))
        candidates.sort(key=lambda item: (item[0], item[1], node_sort_key(item[3])))

        next_frontier: list[NodeRef] = []
        for suffix, edge_key, closer, source, relationship in candidates:
            if source in visited:
                diagnostics.cycle_edges_skipped += 1
                continue
            if len(visited) >= limits.max_nodes:
                diagnostics.truncation.add(TruncationReason.MAX_NODES)
                break
            visited.add(source)
            toward_target[source] = (closer, relationship)
            path_keys[source] = (edge_key, *suffix)
            reached_depth[source] = depth + 1
            next_frontier.append(source)

        diagnostics.depth_reached = depth + 1
        if diagnostics.truncation:
            break
        frontier = sorted(next_frontier, key=lambda n: (path_keys[n], node_sort_key(n)))
    else:
        if frontier:
            diagnostics.truncation.add(TruncationReason.MAX_DEPTH)

    diagnostics.nodes_reached = len(visited)
    reached = tuple(
        ReachedPath(node, _reconstruct_reverse(node, target, toward_target))
        for node in sorted(
            reached_depth,
            key=lambda n: (reached_depth[n], path_keys[n], node_sort_key(n)),
        )
    )
    return ReverseReachabilityResult(target, reached, diagnostics)


async def explore_neighbors(
    provider: RelationshipAdjacencyProvider,
    root: NodeRef,
    *,
    directions: frozenset[AdjacencyDirection],
    relationship_types: frozenset[RelationshipType] | None,
    limits: TraversalLimits,
) -> NeighborResult:
    diagnostics = TraversalDiagnostics(limits=limits)
    visible_types = frozenset(
        relationship_type
        for relationship_type, semantics in TRAVERSAL_SEMANTICS.items()
        if semantics.neighbor_visible
    )
    selected_types = visible_types if relationship_types is None else frozenset(
        relationship_types & visible_types
    )
    visited: set[NodeRef] = {root}
    frontier: list[NodeRef] = [root]
    relationships: dict[str, StoredRelationship] = {}

    for depth in range(limits.max_depth):
        if not frontier:
            break
        next_nodes: set[NodeRef] = set()
        for direction in sorted(directions, key=lambda item: item.value):
            remaining_edges = limits.max_edges - diagnostics.edges_read
            if remaining_edges <= 0:
                diagnostics.truncation.add(TruncationReason.MAX_EDGES)
                break
            batch = await provider.adjacent_many(
                sorted(frontier, key=node_sort_key),
                direction,
                types=selected_types,
                limit=remaining_edges,
            )
            diagnostics.edges_read += len(batch.items)
            if batch.truncated:
                diagnostics.truncation.add(TruncationReason.MAX_EDGES)
            frontier_set = set(frontier)
            for relationship in sorted(batch.items, key=relationship_sort_key):
                if _origin_node(relationship, direction) not in frontier_set:
                    continue
                if relationship.currency is RelationshipCurrency.NOT_CURRENT:
                    continue
                if relationship.currency is RelationshipCurrency.UNKNOWN:
                    diagnostics.unknown_currency.add(relationship.relationship_id)
                    continue
                relationships[relationship.relationship_id] = relationship
                next_node = _next_node(relationship, direction)
                if next_node in visited:
                    diagnostics.cycle_edges_skipped += 1
                    continue
                if len(visited) >= limits.max_nodes:
                    diagnostics.truncation.add(TruncationReason.MAX_NODES)
                    break
                visited.add(next_node)
                next_nodes.add(next_node)
            if diagnostics.truncation:
                break
        diagnostics.depth_reached = depth + 1
        if diagnostics.truncation:
            break
        frontier = sorted(next_nodes, key=node_sort_key)
    else:
        if frontier:
            diagnostics.truncation.add(TruncationReason.MAX_DEPTH)

    diagnostics.nodes_reached = len(visited)
    return NeighborResult(
        root,
        tuple(sorted(visited - {root}, key=node_sort_key)),
        tuple(sorted(relationships.values(), key=relationship_sort_key)),
        diagnostics,
    )


def capability_gaps() -> dict[str, tuple[str, ...]]:
    """Relationship families the persisted graph cannot fully answer today."""

    not_collected: list[str] = []
    partial: list[str] = []
    contract_only: list[str] = []
    for relationship_type, definition in RELATIONSHIP_TAXONOMY.items():
        if definition.collection.status is CollectionStatus.NOT_COLLECTED:
            not_collected.append(relationship_type.value)
        elif definition.collection.status is CollectionStatus.PARTIALLY_COLLECTED:
            partial.append(relationship_type.value)
        elif definition.collection.status is CollectionStatus.CONTRACT_ONLY:
            contract_only.append(relationship_type.value)
    return {
        "not_collected": tuple(sorted(not_collected)),
        "partially_collected": tuple(sorted(partial)),
        "contract_only": tuple(sorted(contract_only)),
    }
