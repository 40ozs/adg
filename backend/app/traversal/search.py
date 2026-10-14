"""Bounded, deterministic search over privilege relationships.

Four primitives, each a breadth-first walk over a :class:`RelationshipAdjacency` that costs one
batched read per level (ADR-0006), and nothing here touches SQL, HTTP or the ORM:

* :func:`shortest_path` — the shortest route from one node to another;
* :func:`alternate_paths` — every simple route up to a cap, shortest first;
* :func:`reverse_reachability` — every node with a route *into* a target;
* :func:`neighbors` — the relationships around one node, a bounded number of levels out.

## What a search follows

Only :data:`~app.traversal.semantics.HOP_TYPES`, only **current** relationships, and only
the ones the caller's :class:`~app.traversal.semantics.TraversalPolicy` permits. Everything
else it meets is accounted for rather than dropped: a relationship of unknown currency is a
boundary (:attr:`CompletenessReason.UNKNOWN_RELATIONSHIPS`), one the policy declined is
counted (:attr:`CompletenessReason.EXCLUDED_BY_POLICY`), and a not-current one is history and
is never followed. A reading (local administration, Remote Desktop, WinRM) is read alongside
the membership it specializes and attached to that step.

## Determinism

Nodes order by ``(kind, canonical key)`` (:func:`~app.traversal.model.node_order`), never by
a display name, an insertion time, or the order a database returned rows in. Every frontier is
sorted, every batch is processed in sorted order, and a shortest path is the
**lexicographically smallest** node sequence among the shortest ones: at each position, the
smallest next node from which the target is still reachable in the remaining hops. Alternate
paths come back ordered by length and then by that same node order. So one graph and one query
give one answer, however the graph was derived.

## Termination and cycles

Every walk keeps a visited map, so a membership cycle is crossed once and never unrolled.
Alternate paths are *simple* (no node twice) and are enumerated inside the region the
breadth-first walks recorded, under a fixed work budget. Every limit that stops a walk early
is reported as a reason, never absorbed.

## Boundaries the graph cannot see past

Walking *towards* a target (reverse reachability, and the backward half of a path search),
the search reports a node it reached through an unresolved relationship — a foreign or
unresolvable principal, whose own holders are outside the collected domain — and a SID Windows
adds to tokens at logon (``Everyone``, ``Authenticated Users``), which no stored membership
leads into. A path search that finds nothing finishes that backward walk to its depth limit,
so an empty answer has looked for both wherever a route could have come from.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.domain.errors import DomainValidationError
from app.domain.relationships import (
    AdjacencyDirection,
    NodeKind,
    NodeRef,
    RelationshipCurrency,
    RelationshipScope,
    RelationshipType,
)
from app.traversal.completeness import CompletenessReason, ordered
from app.traversal.model import (
    RelationshipAdjacency,
    TraversalEdge,
    TraversalPath,
    TraversalStep,
    build_step,
    make_path,
    node_order,
)
from app.traversal.semantics import (
    HOP_TYPES,
    IMPLICIT_MEMBERSHIP_SIDS,
    READING_TYPES,
    STORED_TYPES,
    TraversalPolicy,
    TraversalRole,
)

__all__ = [
    "ALTERNATE_WORK_BUDGET",
    "DEFAULT_LIMITS",
    "MAX_DEPTH_LIMIT",
    "MAX_EDGES_LIMIT",
    "MAX_NODES_LIMIT",
    "MAX_PATHS_LIMIT",
    "NEIGHBOR_DEPTH_LIMIT",
    "Exploration",
    "Neighborhood",
    "PathSearch",
    "Reachability",
    "SearchLimits",
    "alternate_paths",
    "neighbors",
    "reverse_reachability",
    "shortest_path",
]

# Hard maximums. A caller may ask for less, never for more: the API refuses an out-of-range
# value with a 422 rather than computing something nobody bounded.
MAX_DEPTH_LIMIT: Final = 32
MAX_NODES_LIMIT: Final = 100_000
MAX_EDGES_LIMIT: Final = 500_000
MAX_PATHS_LIMIT: Final = 100
NEIGHBOR_DEPTH_LIMIT: Final = 3

ALTERNATE_WORK_BUDGET: Final = 200_000
"""Partial paths alternate-path enumeration may extend. Not caller-tunable: the number of
simple paths in a graph can grow exponentially, and the bound on that work is the server's."""

_SEARCH_TYPES: Final = HOP_TYPES | READING_TYPES

Pair = tuple[NodeRef, NodeRef, RelationshipScope | None]
Successor = tuple[NodeRef, RelationshipScope | None]


@dataclass(frozen=True, slots=True)
class SearchLimits:
    """The bounds of one search. Defaults are generous for a real estate and small against a
    pathological one: Active Directory nesting is rarely deeper than a handful of levels, and a
    privilege path adds a host-local hop and a control hop to that."""

    max_depth: int = 10
    max_nodes: int = 20_000
    max_edges: int = 100_000
    max_paths: int = 10

    def __post_init__(self) -> None:
        for name, ceiling in (
            ("max_depth", MAX_DEPTH_LIMIT),
            ("max_nodes", MAX_NODES_LIMIT),
            ("max_edges", MAX_EDGES_LIMIT),
            ("max_paths", MAX_PATHS_LIMIT),
        ):
            value: int = getattr(self, name)
            if not 1 <= value <= ceiling:
                raise DomainValidationError(
                    f"{name} must be between 1 and {ceiling}; received {value}. The maximum is "
                    "what keeps a caller-supplied limit from making a bounded search unbounded.",
                    value=value,
                    field=name,
                )


DEFAULT_LIMITS: Final = SearchLimits()


@dataclass(slots=True)
class Exploration:
    """What a search read and met, whatever it found. The raw material of completeness."""

    relationships_read: int = 0
    reads: int = 0
    """Batched adjacency reads issued: one per breadth-first level per direction."""
    nodes_reached: int = 0
    depth_forward: int = 0
    depth_backward: int = 0
    truncation: set[CompletenessReason] = field(default_factory=set)
    unknown: dict[str, TraversalEdge] = field(default_factory=dict)
    excluded: dict[str, TraversalEdge] = field(default_factory=dict)
    not_current: set[str] = field(default_factory=set)
    unresolved_nodes: set[NodeRef] = field(default_factory=set)
    implicit_nodes: set[NodeRef] = field(default_factory=set)

    @property
    def reasons(self) -> tuple[CompletenessReason, ...]:
        found = set(self.truncation)
        if self.unknown:
            found.add(CompletenessReason.UNKNOWN_RELATIONSHIPS)
        if self.unresolved_nodes:
            found.add(CompletenessReason.UNRESOLVED_BOUNDARY)
        if self.implicit_nodes:
            found.add(CompletenessReason.IMPLICIT_MEMBERSHIP_BOUNDARY)
        if self.excluded:
            found.add(CompletenessReason.EXCLUDED_BY_POLICY)
        return ordered(found)

    @property
    def truncated(self) -> bool:
        return bool(self.truncation)


def is_implicit_membership(node: NodeRef) -> bool:
    """A SID Windows adds to a token at logon, wherever it is named."""
    sid = node.sid
    return (
        node.kind is NodeKind.PRINCIPAL
        and sid is not None
        and (sid.value in IMPLICIT_MEMBERSHIP_SIDS)
    )


def _edge_order(edge: TraversalEdge, direction: AdjacencyDirection) -> tuple[object, ...]:
    return (
        node_order(edge.origin(direction)),
        node_order(edge.far(direction)),
        edge.relationship_type.value,
        edge.relationship_id,
    )


class _Side:
    """One direction of a breadth-first walk: its distances, its frontier, what it followed."""

    __slots__ = ("blocked", "depth", "direction", "dist", "edges", "frontier", "readings", "root")

    def __init__(self, root: NodeRef, direction: AdjacencyDirection) -> None:
        self.root = root
        self.direction = direction
        self.dist: dict[NodeRef, int] = {root: 0}
        self.frontier: list[NodeRef] = [root]
        self.depth = 0
        self.blocked = False
        self.edges: dict[str, TraversalEdge] = {}
        self.readings: dict[Pair, dict[str, TraversalEdge]] = {}

    @property
    def exhausted(self) -> bool:
        """Every node this side can reach is known, and nothing stopped it."""
        return not self.frontier and not self.blocked


class _Walker:
    """The one place a level is read, and the one place every relationship is classified."""

    def __init__(
        self,
        provider: RelationshipAdjacency,
        policy: TraversalPolicy,
        limits: SearchLimits,
        types: frozenset[RelationshipType] = _SEARCH_TYPES,
    ) -> None:
        self.provider = provider
        self.policy = policy
        self.limits = limits
        self.types = types
        self.seen = Exploration()

    async def read(
        self, frontier: Sequence[NodeRef], direction: AdjacencyDirection
    ) -> list[TraversalEdge] | None:
        """One batched read of the frontier's relationships, sorted. ``None`` when the
        relationship budget is already spent."""
        remaining = self.limits.max_edges - self.seen.relationships_read
        if remaining < 1:
            self.seen.truncation.add(CompletenessReason.MAX_EDGES)
            return None
        batch = await self.provider.adjacent(frontier, direction, self.types, limit=remaining)
        self.seen.reads += 1
        edges = list(batch.edges)
        if batch.truncated or len(edges) > remaining:
            self.seen.truncation.add(CompletenessReason.MAX_EDGES)
            edges = sorted(edges, key=lambda edge: _edge_order(edge, direction))[:remaining]
        self.seen.relationships_read += len(edges)
        members = set(frontier)
        for edge in edges:
            if edge.origin(direction) not in members:
                # A misattributed edge would graft a subtree onto the wrong node and invent a
                # route nobody has: refuse it, exactly as app.domain.graph does.
                raise DomainValidationError(
                    f"The adjacency provider returned relationship {edge.relationship_id} for a "
                    f"node it does not touch in direction {direction.value}.",
                    value=edge.relationship_id,
                    field="relationship_id",
                )
        edges.sort(key=lambda edge: _edge_order(edge, direction))
        return edges

    def follows(self, edge: TraversalEdge) -> bool:
        """Classify a relationship the walk met; ``True`` when the walk follows it."""
        if edge.currency is RelationshipCurrency.NOT_CURRENT:
            self.seen.not_current.add(edge.relationship_id)
            return False
        if edge.currency is RelationshipCurrency.UNKNOWN:
            self.seen.unknown[edge.relationship_id] = edge
            return False
        if not self.policy.permits(edge.currency, edge.confidence):
            self.seen.excluded[edge.relationship_id] = edge
            return False
        return True

    async def expand(self, side: _Side) -> None:
        """Advance one side by one complete level."""
        if side.blocked or not side.frontier:
            return
        direction = side.direction
        edges = await self.read(side.frontier, direction)
        if edges is None:
            side.blocked = True
            return
        if CompletenessReason.MAX_EDGES in self.seen.truncation:
            # The read stopped at the budget: what this level reached is a lower bound.
            side.blocked = True

        next_level: list[NodeRef] = []
        for edge in edges:
            role = edge.semantics.role
            if role is TraversalRole.READING:
                if edge.currency is RelationshipCurrency.CURRENT:
                    side.readings.setdefault(edge.pair, {})[edge.relationship_id] = edge
                continue
            if role is not TraversalRole.HOP or not self.follows(edge):
                continue
            side.edges[edge.relationship_id] = edge
            far = edge.far(direction)
            if direction is AdjacencyDirection.INBOUND:
                if edge.unresolved:
                    self.seen.unresolved_nodes.add(far)
                if is_implicit_membership(far):
                    self.seen.implicit_nodes.add(far)
            if far in side.dist:
                continue
            if self.seen.nodes_reached >= self.limits.max_nodes:
                self.seen.truncation.add(CompletenessReason.MAX_NODES)
                side.blocked = True
                break
            self.seen.nodes_reached += 1
            side.dist[far] = side.depth + 1
            next_level.append(far)

        side.depth += 1
        side.frontier = sorted(next_level, key=node_order)
        if direction is AdjacencyDirection.OUTBOUND:
            self.seen.depth_forward = side.depth
        else:
            self.seen.depth_backward = side.depth


# --- The recorded region, and paths through it ------------------------------------------


class _Region:
    """The relationships both halves of a search followed, as an in-memory graph.

    Every hop of every route the breadth-first walks could see is in here (see
    :func:`shortest_path`), so paths are reconstructed and enumerated without another read.
    """

    def __init__(self, sides: Iterable[_Side]) -> None:
        self.out: dict[NodeRef, dict[Successor, dict[str, TraversalEdge]]] = {}
        self.into: dict[NodeRef, set[NodeRef]] = {}
        self.readings: dict[Pair, dict[str, TraversalEdge]] = {}
        for side in sides:
            for edge in side.edges.values():
                bucket = self.out.setdefault(edge.source, {}).setdefault(
                    (edge.target, edge.scope), {}
                )
                bucket[edge.relationship_id] = edge
                self.into.setdefault(edge.target, set()).add(edge.source)
            for pair, found in side.readings.items():
                self.readings.setdefault(pair, {}).update(found)

    def successors(self, node: NodeRef) -> list[Successor]:
        return sorted(
            self.out.get(node, {}),
            key=lambda item: (node_order(item[0]), "" if item[1] is None else item[1].key),
        )

    def distances_from(self, root: NodeRef) -> dict[NodeRef, int]:
        found = {root: 0}
        queue = deque([root])
        while queue:
            node = queue.popleft()
            for far, _ in self.successors(node):
                if far not in found:
                    found[far] = found[node] + 1
                    queue.append(far)
        return found

    def distances_to(self, root: NodeRef) -> dict[NodeRef, int]:
        found = {root: 0}
        queue = deque([root])
        while queue:
            node = queue.popleft()
            for near in sorted(self.into.get(node, ()), key=node_order):
                if near not in found:
                    found[near] = found[node] + 1
                    queue.append(near)
        return found

    def step(self, node: NodeRef, successor: Successor) -> TraversalStep:
        far, scope = successor
        hops = list(self.out[node][successor].values())
        readings = list(self.readings.get((node, far, scope), {}).values())
        return build_step(hops, readings)

    def path(
        self, nodes: Sequence[NodeRef], scopes: Sequence[RelationshipScope | None]
    ) -> TraversalPath:
        return make_path(
            [
                self.step(nodes[index], (nodes[index + 1], scopes[index]))
                for index in range(len(scopes))
            ]
        )


def _require_distinct(source: NodeRef, target: NodeRef) -> None:
    if source == target:
        raise DomainValidationError(
            f"{source} is both the source and the target. A path joins two distinct nodes.",
            value=str(target),
            field="target",
        )


@dataclass(frozen=True, slots=True)
class PathSearch:
    """The bounded answer to a path question, before readiness is folded in."""

    source: NodeRef
    target: NodeRef
    policy: TraversalPolicy
    limits: SearchLimits
    paths: tuple[TraversalPath, ...]
    exploration: Exploration

    @property
    def reasons(self) -> tuple[CompletenessReason, ...]:
        return self.exploration.reasons


async def _probe_depth(walker: _Walker, forward: _Side, backward: _Side) -> None:
    """At the depth bound, look one level further before calling the answer truncated.

    A frontier of leaves hides nothing, and reporting it as cut short would make a complete
    answer look like a lower bound — the same probe ``app.domain.graph.expand`` makes.
    """
    side = forward if len(forward.frontier) <= len(backward.frontier) else backward
    if not side.frontier or side.blocked:
        return
    before = len(side.dist)
    await walker.expand(side)
    if len(side.dist) > before:
        walker.seen.truncation.add(CompletenessReason.MAX_DEPTH)


async def _finish_backward(walker: _Walker, backward: _Side, depth: int) -> None:
    """Walk towards the target to ``depth``: the boundaries an empty answer must report."""
    while backward.frontier and not backward.blocked and backward.depth < depth:
        await walker.expand(backward)


def _meets(forward: _Side, backward: _Side) -> bool:
    smaller, larger = sorted((forward.dist, backward.dist), key=len)
    return any(node in larger for node in smaller)


async def shortest_path(
    source: NodeRef,
    target: NodeRef,
    provider: RelationshipAdjacency,
    *,
    policy: TraversalPolicy = TraversalPolicy.ALL_CURRENT,
    limits: SearchLimits = DEFAULT_LIMITS,
) -> PathSearch:
    """The shortest route from ``source`` to ``target``, lexicographically first among equals.

    Bidirectional and level-synchronous: each round advances, by one complete level, whichever
    side has the smaller frontier. The first time the two visited sets meet, every edge of
    every shortest route has been recorded by one side or the other — an edge at position *i*
    of a route of length *L* leaves a node the forward side expanded when *i* is below its
    depth, and enters one the backward side expanded otherwise, because the depths already sum
    to at least *L*. The route is then chosen in memory, with no further read.
    """
    _require_distinct(source, target)
    walker = _Walker(provider, policy, limits)
    forward = _Side(source, AdjacencyDirection.OUTBOUND)
    backward = _Side(target, AdjacencyDirection.INBOUND)

    met = False
    while True:
        if _meets(forward, backward):
            met = True
            break
        if forward.exhausted or backward.exhausted or forward.blocked or backward.blocked:
            break
        if forward.depth + backward.depth >= limits.max_depth:
            # Every route of max_depth hops or fewer would have met by now. Whatever the probe
            # finds is longer than the caller allowed, and is reported, not returned.
            await _probe_depth(walker, forward, backward)
            break
        side = forward if len(forward.frontier) <= len(backward.frontier) else backward
        await walker.expand(side)

    paths: tuple[TraversalPath, ...] = ()
    if met:
        path = _lexicographic_shortest(_Region((forward, backward)), source, target)
        if path is not None and path.hops <= limits.max_depth:
            paths = (path,)
    if not paths and not walker.seen.truncated:
        await _finish_backward(walker, backward, limits.max_depth - 1)
    return PathSearch(source, target, policy, limits, paths, walker.seen)


def _lexicographic_shortest(
    region: _Region, source: NodeRef, target: NodeRef
) -> TraversalPath | None:
    """The smallest node sequence among the shortest routes in the recorded region."""
    to_target = region.distances_to(target)
    length = to_target.get(source)
    if length is None:
        return None
    nodes = [source]
    scopes: list[RelationshipScope | None] = []
    node = source
    for position in range(length):
        remaining = length - position - 1
        far, scope = next(
            successor
            for successor in region.successors(node)
            if to_target.get(successor[0]) == remaining
        )
        nodes.append(far)
        scopes.append(scope)
        node = far
    return region.path(nodes, scopes)


async def alternate_paths(
    source: NodeRef,
    target: NodeRef,
    provider: RelationshipAdjacency,
    *,
    policy: TraversalPolicy = TraversalPolicy.ALL_CURRENT,
    limits: SearchLimits = DEFAULT_LIMITS,
) -> PathSearch:
    """Every simple route up to ``max_paths``, shortest first, then in node order.

    The two sides walk until their depths sum to ``max_depth`` (or one runs out), which records
    every hop of every route no longer than that; the routes are then enumerated in memory,
    one length at a time, depth-first in sorted order and pruned by the distance still to go.
    A route is a node sequence, so two relationships joining the same pair never make two
    routes, and no node appears twice in one.
    """
    _require_distinct(source, target)
    walker = _Walker(provider, policy, limits)
    forward = _Side(source, AdjacencyDirection.OUTBOUND)
    backward = _Side(target, AdjacencyDirection.INBOUND)

    while forward.depth + backward.depth < limits.max_depth:
        if forward.exhausted or backward.exhausted or forward.blocked or backward.blocked:
            break
        side = forward if len(forward.frontier) <= len(backward.frontier) else backward
        await walker.expand(side)
    else:
        if not (forward.exhausted or backward.exhausted):
            await _probe_depth(walker, forward, backward)

    region = _Region((forward, backward))
    paths, cut = _enumerate(region, source, target, limits)
    walker.seen.truncation |= cut
    if not paths and not walker.seen.truncated:
        await _finish_backward(walker, backward, limits.max_depth - 1)
    return PathSearch(source, target, policy, limits, tuple(paths), walker.seen)


def _enumerate(
    region: _Region, source: NodeRef, target: NodeRef, limits: SearchLimits
) -> tuple[list[TraversalPath], set[CompletenessReason]]:
    to_target = region.distances_to(target)
    shortest = to_target.get(source)
    found: list[TraversalPath] = []
    cut: set[CompletenessReason] = set()
    if shortest is None:
        return found, cut

    work = 0
    for length in range(shortest, limits.max_depth + 1):
        nodes: list[NodeRef] = [source]
        scopes: list[RelationshipScope | None] = []
        on_path = {source}
        # Iterative depth-first search: (node, its sorted successors, next index).
        stack: list[tuple[NodeRef, list[Successor], int]] = [(source, region.successors(source), 0)]
        while stack:
            node, successors, index = stack[-1]
            if index >= len(successors):
                stack.pop()
                if len(nodes) > 1:
                    on_path.discard(nodes.pop())
                    scopes.pop()
                continue
            stack[-1] = (node, successors, index + 1)
            far, scope = successors[index]
            position = len(nodes)  # hops taken once ``far`` is appended
            if far in on_path:
                continue
            if far == target:
                if position == length:
                    found.append(region.path([*nodes, far], [*scopes, scope]))
                    if len(found) > limits.max_paths:
                        cut.add(CompletenessReason.MAX_PATHS)
                        return found[: limits.max_paths], cut
                continue
            still = to_target.get(far)
            if still is None:
                continue
            if position + still > length:
                if length == limits.max_depth:
                    # A route through here is longer than the depth limit allows.
                    cut.add(CompletenessReason.MAX_DEPTH)
                continue
            work += 1
            if work > ALTERNATE_WORK_BUDGET:
                cut.add(CompletenessReason.SEARCH_BUDGET)
                return found, cut
            nodes.append(far)
            scopes.append(scope)
            on_path.add(far)
            stack.append((far, region.successors(far), 0))
    return found, cut


# --- Reverse reachability -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReachedSource:
    node: NodeRef
    hops: int
    unresolved_boundary: bool
    implicit_membership: bool


class Reachability:
    """Every node with a route into a target, nearest first, and how each one gets there."""

    def __init__(
        self,
        target: NodeRef,
        policy: TraversalPolicy,
        limits: SearchLimits,
        side: _Side,
        exploration: Exploration,
    ) -> None:
        self.target = target
        self.policy = policy
        self.limits = limits
        self.exploration = exploration
        self._side = side
        self._region = _Region((side,))
        self.sources: tuple[ReachedSource, ...] = tuple(
            ReachedSource(
                node=node,
                hops=hops,
                unresolved_boundary=node in exploration.unresolved_nodes,
                implicit_membership=node in exploration.implicit_nodes,
            )
            for node, hops in sorted(
                side.dist.items(), key=lambda item: (item[1], node_order(item[0]))
            )
            if 1 <= hops <= limits.max_depth
        )

    @property
    def reasons(self) -> tuple[CompletenessReason, ...]:
        return self.exploration.reasons

    def path_from(self, node: NodeRef) -> TraversalPath:
        """The lexicographically first shortest route from ``node`` into the target."""
        dist = self._side.dist
        nodes = [node]
        scopes: list[RelationshipScope | None] = []
        current = node
        while current != self.target:
            far, scope = next(
                successor
                for successor in self._region.successors(current)
                if dist.get(successor[0]) == dist[current] - 1
            )
            nodes.append(far)
            scopes.append(scope)
            current = far
        return self._region.path(nodes, scopes)


async def reverse_reachability(
    target: NodeRef,
    provider: RelationshipAdjacency,
    *,
    policy: TraversalPolicy = TraversalPolicy.ALL_CURRENT,
    limits: SearchLimits = DEFAULT_LIMITS,
) -> Reachability:
    """Walk every hop into ``target`` backwards, to ``max_depth``.

    A graph primitive — who holds a route into this node — and nothing more: it classifies no
    asset and ranks nothing. The walk reverses which end it starts from, never what an edge
    means: each route is reported source to target, in the direction the privilege flows.
    """
    walker = _Walker(provider, policy, limits)
    side = _Side(target, AdjacencyDirection.INBOUND)
    while side.frontier and not side.blocked and side.depth < limits.max_depth:
        await walker.expand(side)
    if side.frontier and not side.blocked:
        before = len(side.dist)
        await walker.expand(side)
        if len(side.dist) > before:
            walker.seen.truncation.add(CompletenessReason.MAX_DEPTH)
        # The probe looked past the depth limit only to learn whether anything is there.
        # A boundary it met belongs to no source this answer reports.
        within = {node for node, hops in side.dist.items() if hops <= limits.max_depth}
        walker.seen.unresolved_nodes &= within
        walker.seen.implicit_nodes &= within
    return Reachability(target, policy, limits, side, walker.seen)


# --- Neighbors ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Neighbor:
    edge: TraversalEdge
    depth: int
    direction: AdjacencyDirection


@dataclass(frozen=True, slots=True)
class Neighborhood:
    node: NodeRef
    depth: int
    directions: tuple[AdjacencyDirection, ...]
    types: frozenset[RelationshipType]
    policy: TraversalPolicy
    relationships: tuple[Neighbor, ...]
    nodes: dict[NodeRef, int]
    exploration: Exploration
    frontier: int = 0
    """Nodes at the last level whose own relationships were not listed. A hint for a client
    that expands the view, never a truncation: what lies beyond the requested depth is not
    part of the question asked."""

    @property
    def reasons(self) -> tuple[CompletenessReason, ...]:
        return self.exploration.reasons


async def neighbors(
    node: NodeRef,
    provider: RelationshipAdjacency,
    *,
    directions: Sequence[AdjacencyDirection] = (
        AdjacencyDirection.OUTBOUND,
        AdjacencyDirection.INBOUND,
    ),
    types: frozenset[RelationshipType] = STORED_TYPES,
    depth: int = 1,
    policy: TraversalPolicy = TraversalPolicy.ALL_CURRENT,
    limits: SearchLimits = DEFAULT_LIMITS,
) -> Neighborhood:
    """The current relationships around ``node``, ``depth`` levels out.

    Unlike path search this lists every stored type it was asked for — readings and
    informational relationships included — because it answers "what is next to this", not
    "what can this reach". Each relationship is listed once, at the level it was first met,
    with the direction it was met in; its own source and target are unchanged. The answer is
    complete when every relationship within ``depth`` levels is listed; what lies further out
    is counted in :attr:`Neighborhood.frontier`, not reported as a truncation.
    """
    if not 1 <= depth <= NEIGHBOR_DEPTH_LIMIT:
        raise DomainValidationError(
            f"depth must be between 1 and {NEIGHBOR_DEPTH_LIMIT}; received {depth}.",
            value=depth,
            field="depth",
        )
    unknown_types = types - STORED_TYPES
    if unknown_types or not types:
        raise DomainValidationError(
            "Neighbor exploration lists stored relationship types only.",
            value=sorted(item.value for item in unknown_types),
            field="types",
        )
    ordered_directions = tuple(sorted(set(directions), key=lambda item: item.value, reverse=True))
    walker = _Walker(provider, policy, limits, types=frozenset(types))
    reached: dict[NodeRef, int] = {node: 0}
    listed: dict[str, Neighbor] = {}
    frontier = [node]
    blocked = False

    for level in range(1, depth + 1):
        next_level: set[NodeRef] = set()
        for direction in ordered_directions:
            if not frontier or blocked:
                break
            edges = await walker.read(frontier, direction)
            if edges is None or CompletenessReason.MAX_EDGES in walker.seen.truncation:
                blocked = True
            for edge in edges or ():
                if edge.relationship_id in listed or not walker.follows(edge):
                    continue
                far = edge.far(direction)
                if far not in reached:
                    if walker.seen.nodes_reached >= limits.max_nodes:
                        # Listing it would name a node the answer never reached.
                        walker.seen.truncation.add(CompletenessReason.MAX_NODES)
                        blocked = True
                        break
                    walker.seen.nodes_reached += 1
                    reached[far] = level
                    next_level.add(far)
                listed[edge.relationship_id] = Neighbor(edge, level, direction)
        frontier = sorted(next_level, key=node_order)
        walker.seen.depth_forward = level
        if blocked:
            break

    relationships = tuple(
        sorted(
            listed.values(),
            key=lambda item: (
                item.depth,
                item.edge.relationship_type.value,
                item.edge.relationship_id,
            ),
        )
    )
    return Neighborhood(
        node=node,
        depth=depth,
        directions=ordered_directions,
        types=frozenset(types),
        policy=policy,
        relationships=relationships,
        nodes=reached,
        exploration=walker.seen,
        frontier=0 if blocked else len(frontier),
    )
