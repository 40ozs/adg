"""Bounded, cycle-safe traversal of the membership graph.

Membership is stored as edges and never as an expanded closure (ADR-0002), so every
"who is effectively in this group" question is a traversal. This module is that traversal,
and it is deliberately free of SQL, HTTP, and ORM: it walks whatever
:class:`AdjacencyProvider` it is handed, which in production is a PostgreSQL-backed
repository and in tests is a dictionary. That is what makes deep graphs, wide graphs,
cycles, and every limit testable without a database.

Four properties this module exists to guarantee:

**Termination.** A directory can contain membership cycles, and ADG stores them rather than
rejecting them, so traversal must survive them. Breadth-first search with a visited set
terminates on any graph, cyclic or not; no algorithm here recurses on graph structure.

**Explainability.** Reachability alone would answer *who* and throw away *why*. Every
reached node carries the shortest path that reached it, and
:func:`find_paths` enumerates every distinct simple path between two nodes, because the
chain ``alice → Finance-Team → Finance-RW`` is the product.

**Honest bounds.** A traversal that hit a limit returns what it found *and says so*
(:attr:`Expansion.truncation`). An audit tool that silently returns a partial member list
understates access, which is the most dangerous answer it can give. Callers must treat an
incomplete result as incomplete; nothing here pretends otherwise.

**Explicit cycle diagnostics.** Cycles are reported as strongly connected components with a
concrete representative loop, not swallowed by the visited set. A cycle is a finding.

Node identity throughout is the *key* string — :attr:`app.domain.Principal.identity_key`
and the matching :attr:`app.domain.MembershipEdge.group_key` / ``member_key`` — never a
bare SID, because ``BUILTIN\\Administrators`` on two servers must stay two nodes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol

from app.domain.errors import DomainValidationError
from app.domain.identity import PrincipalKind
from app.domain.membership import MembershipEdgeKind

__all__ = [
    "DEFAULT_LIMITS",
    "MAX_DEPTH_CEILING",
    "MAX_EDGES_CEILING",
    "MAX_NODES_CEILING",
    "MAX_PATHS_CEILING",
    "AdjacencyProvider",
    "Direction",
    "Expansion",
    "GraphCycle",
    "GraphEdge",
    "MembershipPath",
    "PathSearch",
    "ReachedNode",
    "TraversalLimits",
    "TruncationReason",
    "expand",
    "find_cycles",
    "find_paths",
]

# Ceilings, not defaults. They exist so that a caller-supplied limit cannot turn a bounded
# query into an unbounded one; the API clamps to them and reports the clamp.
MAX_DEPTH_CEILING: Final = 128
MAX_NODES_CEILING: Final = 250_000
MAX_EDGES_CEILING: Final = 1_000_000
MAX_PATHS_CEILING: Final = 1_000


class Direction(StrEnum):
    """Which way an edge is followed.

    An edge means "``member_key`` is a member of ``group_key``". Following it
    :attr:`DOWN` answers "who is in this group"; following it :attr:`UP` answers "which
    groups contain this principal".
    """

    DOWN = "down"
    UP = "up"

    @property
    def inverse(self) -> Direction:
        return Direction.UP if self is Direction.DOWN else Direction.DOWN


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One stored membership edge, as traversal sees it.

    A narrow projection of the ``membership_edges`` row: everything needed to walk the
    graph and to explain a hop, and nothing else. ``member_kind`` is the collector's
    report of what the member is, which is the only classification available for a member
    no run has described with its own principal observation yet.
    """

    edge_key: str
    group_key: str
    member_key: str
    kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
    host_key: str | None = None
    member_kind: PrincipalKind | None = None
    is_foreign_security_principal: bool = False

    def __post_init__(self) -> None:
        if not self.group_key or not self.member_key:
            raise DomainValidationError(
                "A graph edge must have both endpoints; an empty key cannot be traversed.",
                field="group_key" if not self.group_key else "member_key",
            )
        if self.group_key == self.member_key:
            raise DomainValidationError(
                f"A group cannot be a direct member of itself ({self.group_key}).",
                value=self.group_key,
                field="member_key",
            )

    def endpoint(self, direction: Direction) -> str:
        """The key this edge leads *to* when followed in ``direction``."""
        return self.member_key if direction is Direction.DOWN else self.group_key

    def origin(self, direction: Direction) -> str:
        """The key this edge leads *from* when followed in ``direction``."""
        return self.group_key if direction is Direction.DOWN else self.member_key


class AdjacencyProvider(Protocol):
    """Supplies the edges touching a set of nodes.

    Batched by design: a breadth-first level asks for every node on the frontier at once,
    so a traversal costs one query per level rather than one per node. An implementation
    must return an entry for each requested key (an empty sequence for a leaf) or omit it;
    both are read as "no edges".
    """

    async def neighbors(
        self, direction: Direction, keys: Sequence[str]
    ) -> Mapping[str, Sequence[GraphEdge]]:
        """Return the edges leading out of each key in ``direction``."""
        ...


class TruncationReason(StrEnum):
    """Why a traversal stopped early. An empty tuple of these means the answer is complete."""

    MAX_DEPTH = "max_depth"
    MAX_NODES = "max_nodes"
    MAX_EDGES = "max_edges"
    MAX_PATHS = "max_paths"


@dataclass(frozen=True, slots=True)
class TraversalLimits:
    """Safety bounds for one traversal.

    The defaults are generous against real directories and small against a pathological
    one. Active Directory nesting is rarely deeper than a handful of levels, and a Windows
    access token cannot hold more than roughly 1,015 SIDs, so a principal effectively in
    tens of thousands of groups is a data-quality finding rather than a query to satisfy.
    """

    max_depth: int = 32
    max_nodes: int = 50_000
    max_edges: int = 200_000
    max_paths: int = 100

    def __post_init__(self) -> None:
        for name, ceiling in (
            ("max_depth", MAX_DEPTH_CEILING),
            ("max_nodes", MAX_NODES_CEILING),
            ("max_edges", MAX_EDGES_CEILING),
            ("max_paths", MAX_PATHS_CEILING),
        ):
            value: int = getattr(self, name)
            if value < 1:
                raise DomainValidationError(
                    f"{name} must be at least 1; received {value}.", value=value, field=name
                )
            if value > ceiling:
                raise DomainValidationError(
                    f"{name} may not exceed {ceiling}; received {value}. The ceiling is what "
                    "keeps a caller-supplied limit from turning a bounded query into an "
                    "unbounded one.",
                    value=value,
                    field=name,
                )

    def clamped(self, **overrides: int | None) -> TraversalLimits:
        """Apply non-``None`` overrides, silently reducing any that exceed a ceiling.

        Used at the API boundary, where a caller may ask for more than the ceiling allows.
        The response reports the effective limits, so the clamp is visible.
        """
        ceilings = {
            "max_depth": MAX_DEPTH_CEILING,
            "max_nodes": MAX_NODES_CEILING,
            "max_edges": MAX_EDGES_CEILING,
            "max_paths": MAX_PATHS_CEILING,
        }
        values = {
            "max_depth": self.max_depth,
            "max_nodes": self.max_nodes,
            "max_edges": self.max_edges,
            "max_paths": self.max_paths,
        }
        for name, override in overrides.items():
            if override is None:
                continue
            if name not in ceilings:
                raise DomainValidationError(f"Unknown traversal limit {name!r}.", field=name)
            values[name] = max(1, min(override, ceilings[name]))
        return TraversalLimits(**values)


DEFAULT_LIMITS: Final = TraversalLimits()


@dataclass(frozen=True, slots=True)
class ReachedNode:
    """A node the traversal reached, with the shortest explanation of how.

    ``path`` runs from the traversal root to this node inclusive, so a one-hop member has
    a two-element path. It is the *shortest* path; :func:`find_paths` enumerates all of
    them when the full set matters.
    """

    key: str
    depth: int
    path: tuple[str, ...]
    edge_keys: tuple[str, ...]
    edge_kinds: tuple[MembershipEdgeKind, ...]
    reported_kind: PrincipalKind | None = None
    """What the edge that reached this node said the member is. Advisory metadata."""

    via_foreign_security_principal: bool = False
    """True when any hop on the shortest path crossed a foreign security principal."""


@dataclass(frozen=True, slots=True)
class GraphCycle:
    """A membership cycle, reported rather than swallowed.

    ``members`` is the strongly connected component, sorted for stable output.
    ``representative_path`` is one concrete loop through it, starting and ending on the
    same node, so an operator can be shown the actual chain to break.
    """

    members: tuple[str, ...]
    representative_path: tuple[str, ...]

    @property
    def length(self) -> int:
        return len(self.members)


@dataclass(frozen=True, slots=True)
class Expansion:
    """The bounded result of walking outward from one node.

    ``nodes`` excludes the root: "the effective members of Finance-RW" does not include
    Finance-RW. ``edges`` is the subgraph actually traversed, which is what the cycle
    analysis ran over and what a caller can re-walk to build alternative explanations.
    """

    root: str
    direction: Direction
    nodes: tuple[ReachedNode, ...]
    edges: tuple[GraphEdge, ...]
    cycles: tuple[GraphCycle, ...]
    depth_reached: int
    limits: TraversalLimits
    truncation: tuple[TruncationReason, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every reachable node was enumerated.

        A ``False`` here means the caller is holding a *lower bound* on membership, and
        must present it as one.
        """
        return not self.truncation

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(node.key for node in self.nodes)

    def node(self, key: str) -> ReachedNode | None:
        for candidate in self.nodes:
            if candidate.key == key:
                return candidate
        return None


@dataclass(frozen=True, slots=True)
class MembershipPath:
    """One simple (repetition-free) chain of memberships.

    ``nodes`` runs member-first, group-last — ``[alice, Finance-Team, Finance-RW]`` —
    regardless of which direction the search walked, because that is the order the chain
    is read in an explanation.
    """

    nodes: tuple[str, ...]
    edges: tuple[GraphEdge, ...] = field(default_factory=tuple)

    @property
    def length(self) -> int:
        """Number of hops. A direct membership has length 1."""
        return len(self.nodes) - 1

    @property
    def edge_kinds(self) -> tuple[MembershipEdgeKind, ...]:
        return tuple(edge.kind for edge in self.edges)


@dataclass(frozen=True, slots=True)
class PathSearch:
    """Every bounded simple path between a principal and a group."""

    member_key: str
    group_key: str
    paths: tuple[MembershipPath, ...]
    cycles: tuple[GraphCycle, ...]
    limits: TraversalLimits
    truncation: tuple[TruncationReason, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.truncation

    @property
    def is_member(self) -> bool:
        """Whether at least one path exists.

        ``False`` with :attr:`complete` ``False`` means *unknown*, not *no*: the search was
        cut short. Callers must not render a truncated empty result as "no access".
        """
        return bool(self.paths)


def _check_origin(edge: GraphEdge, key: str, direction: Direction) -> None:
    """Reject an adjacency provider that hands back an edge not touching ``key``.

    A misattributed edge would graft a subtree onto the wrong node and invent a membership
    nobody has, which is exactly the failure this tool exists to catch.
    """
    if edge.origin(direction) != key:
        raise DomainValidationError(
            f"The adjacency provider returned edge {edge.edge_key!r} for node {key!r}, but "
            f"the edge leads out of {edge.origin(direction)!r} when followed "
            f"{direction.value}. A misattributed edge would invent a membership nobody has.",
            value=edge.edge_key,
            field="edge_key",
        )


async def expand(
    root: str,
    direction: Direction,
    provider: AdjacencyProvider,
    limits: TraversalLimits = DEFAULT_LIMITS,
) -> Expansion:
    """Walk outward from ``root``, breadth-first, within ``limits``.

    Breadth-first rather than depth-first for two reasons: the first time a node is
    reached is by a shortest path, which is the explanation worth keeping; and one level
    is one batched provider call, so a traversal over a database costs a query per level
    instead of a query per node.

    Cycles do not affect termination — a node already visited is never expanded again —
    and are reported separately in :attr:`Expansion.cycles`.
    """
    if not root:
        raise DomainValidationError("A traversal needs a root key.", field="root")

    visited: dict[str, ReachedNode] = {}
    edges_seen: dict[str, GraphEdge] = {}
    truncation: set[TruncationReason] = set()

    root_node = ReachedNode(key=root, depth=0, path=(root,), edge_keys=(), edge_kinds=())
    frontier: list[str] = [root]
    paths: dict[str, ReachedNode] = {root: root_node}
    depth = 0

    while frontier and depth < limits.max_depth and not truncation:
        depth += 1
        neighbor_map = await provider.neighbors(direction, frontier)
        next_frontier: list[str] = []

        for key in frontier:
            for edge in neighbor_map.get(key, ()):
                _check_origin(edge, key, direction)
                # Recorded even when the far endpoint is already known: the cycle analysis
                # needs the closing edge, which by definition points at a visited node.
                if edge.edge_key not in edges_seen:
                    if len(edges_seen) >= limits.max_edges:
                        truncation.add(TruncationReason.MAX_EDGES)
                        break
                    edges_seen[edge.edge_key] = edge

                far = edge.endpoint(direction)
                if far in paths:
                    continue
                if len(paths) - 1 >= limits.max_nodes:
                    truncation.add(TruncationReason.MAX_NODES)
                    break

                origin_node = paths[key]
                paths[far] = ReachedNode(
                    key=far,
                    depth=depth,
                    path=(*origin_node.path, far),
                    edge_keys=(*origin_node.edge_keys, edge.edge_key),
                    edge_kinds=(*origin_node.edge_kinds, edge.kind),
                    reported_kind=edge.member_kind if direction is Direction.DOWN else None,
                    via_foreign_security_principal=(
                        origin_node.via_foreign_security_principal
                        or edge.is_foreign_security_principal
                    ),
                )
                visited[far] = paths[far]
                next_frontier.append(far)
            if truncation:
                break

        frontier = next_frontier

    if frontier and not truncation:
        # The depth limit stopped the walk with nodes still unexpanded — but a frontier of
        # leaves hides nothing, and reporting that as truncated would make a complete
        # member list look like a lower bound. One more batched lookup settles it, and the
        # edges it returns are real edges out of visited nodes, so they are kept.
        boundary = await provider.neighbors(direction, frontier)
        for key in frontier:
            for edge in boundary.get(key, ()):
                _check_origin(edge, key, direction)
                if edge.edge_key not in edges_seen and len(edges_seen) < limits.max_edges:
                    edges_seen[edge.edge_key] = edge
                if edge.endpoint(direction) not in paths:
                    truncation.add(TruncationReason.MAX_DEPTH)

    reachable = set(paths)
    cycles = find_cycles(edges_seen.values(), direction, restrict_to=reachable)

    return Expansion(
        root=root,
        direction=direction,
        nodes=tuple(sorted(visited.values(), key=lambda node: (node.depth, node.key))),
        edges=tuple(sorted(edges_seen.values(), key=lambda edge: edge.edge_key)),
        cycles=cycles,
        depth_reached=max((node.depth for node in visited.values()), default=0),
        limits=limits,
        truncation=tuple(sorted(truncation)),
    )


async def find_paths(
    member_key: str,
    group_key: str,
    provider: AdjacencyProvider,
    limits: TraversalLimits = DEFAULT_LIMITS,
) -> PathSearch:
    """Enumerate every simple membership path from ``member_key`` up to ``group_key``.

    Implemented as an upward expansion followed by path enumeration over the subgraph that
    expansion collected, so both halves obey one set of limits and one set of provider
    calls. Paths are returned shortest-first and, within a length, in lexicographic order,
    so the output is stable enough to diff between runs.
    """
    if member_key == group_key:
        raise DomainValidationError(
            "A principal is not a member of itself; give two distinct keys.",
            value=member_key,
            field="group_key",
        )

    expansion = await expand(member_key, Direction.UP, provider, limits)
    truncation = set(expansion.truncation)

    outgoing: dict[str, list[GraphEdge]] = {}
    for edge in expansion.edges:
        outgoing.setdefault(edge.member_key, []).append(edge)
    for edges in outgoing.values():
        edges.sort(key=lambda edge: (edge.group_key, edge.edge_key))

    found: list[MembershipPath] = []
    on_path: set[str] = {member_key}
    # Iterative DFS: the frontier holds (node, edges taken so far, next edge index).
    stack: list[tuple[str, list[GraphEdge], int]] = [(member_key, [], 0)]

    while stack:
        node, taken, index = stack.pop()
        candidates = outgoing.get(node, [])
        if index >= len(candidates):
            # This node's subtree is finished: it leaves the current path, which is what
            # lets a sibling branch legitimately pass through it.
            if node != member_key:
                on_path.discard(node)
            continue
        stack.append((node, taken, index + 1))

        edge = candidates[index]
        far = edge.group_key
        if far in on_path:
            # A cycle; already reported in `cycles`. Following it would enumerate
            # infinitely many chains, none of them a simple path.
            continue
        next_taken = [*taken, edge]
        if far == group_key:
            found.append(
                MembershipPath(
                    nodes=(member_key, *(hop.group_key for hop in next_taken)),
                    edges=tuple(next_taken),
                )
            )
            if len(found) >= limits.max_paths:
                truncation.add(TruncationReason.MAX_PATHS)
                break
            # Do not walk beyond the target: a chain that leaves the group and returns to
            # it is not a simple path.
            continue
        if len(next_taken) >= limits.max_depth:
            if outgoing.get(far):
                truncation.add(TruncationReason.MAX_DEPTH)
            continue
        on_path.add(far)
        stack.append((far, next_taken, 0))

    found.sort(key=lambda path: (path.length, path.nodes))
    return PathSearch(
        member_key=member_key,
        group_key=group_key,
        paths=tuple(found),
        cycles=expansion.cycles,
        limits=limits,
        truncation=tuple(sorted(truncation)),
    )


def find_cycles(
    edges: Iterable[GraphEdge],
    direction: Direction = Direction.DOWN,
    restrict_to: set[str] | None = None,
) -> tuple[GraphCycle, ...]:
    """Find every membership cycle among ``edges``, as strongly connected components.

    Tarjan's algorithm, written iteratively: a cycle-bearing directory can be nested deep
    enough that a recursive implementation would exhaust the Python stack, which would turn
    a reportable anomaly into a crash.

    ``direction`` only changes which way the representative loop reads; the components
    themselves are direction-independent.
    """
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        origin = edge.origin(direction)
        far = edge.endpoint(direction)
        if restrict_to is not None and (origin not in restrict_to or far not in restrict_to):
            continue
        adjacency.setdefault(origin, []).append(far)
        adjacency.setdefault(far, [])
    for neighbors in adjacency.values():
        neighbors.sort()

    index_of: dict[str, int] = {}
    low_link: dict[str, int] = {}
    on_stack: set[str] = set()
    component_stack: list[str] = []
    counter = 0
    components: list[list[str]] = []

    for start in sorted(adjacency):
        if start in index_of:
            continue
        # (node, next neighbor index). Explicit, so depth is heap-bound, not stack-bound.
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, next_index = work.pop()
            if next_index == 0:
                index_of[node] = low_link[node] = counter
                counter += 1
                component_stack.append(node)
                on_stack.add(node)

            recursed = False
            neighbors = adjacency[node]
            while next_index < len(neighbors):
                neighbor = neighbors[next_index]
                next_index += 1
                if neighbor not in index_of:
                    work.append((node, next_index))
                    work.append((neighbor, 0))
                    recursed = True
                    break
                if neighbor in on_stack:
                    low_link[node] = min(low_link[node], index_of[neighbor])
            if recursed:
                continue

            if low_link[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = component_stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
            if work:
                parent, _ = work[-1]
                low_link[parent] = min(low_link[parent], low_link[node])

    cycles: list[GraphCycle] = []
    for component in components:
        if len(component) < 2:
            continue
        members = tuple(sorted(component))
        cycles.append(
            GraphCycle(
                members=members,
                representative_path=_representative_loop(members[0], set(component), adjacency),
            )
        )
    cycles.sort(key=lambda cycle: cycle.members)
    return tuple(cycles)


def _representative_loop(
    start: str, component: set[str], adjacency: Mapping[str, Sequence[str]]
) -> tuple[str, ...]:
    """One concrete loop through ``component``, starting and ending at ``start``.

    Shortest such loop, found breadth-first, so the chain shown to an operator is the
    smallest set of memberships that has to be broken.
    """
    parents: dict[str, str] = {}
    queue: list[str] = [start]
    seen: set[str] = {start}
    while queue:
        node = queue.pop(0)
        for neighbor in adjacency.get(node, ()):
            if neighbor not in component:
                continue
            if neighbor == start:
                loop = [start]
                cursor = node
                while cursor != start:
                    loop.append(cursor)
                    cursor = parents[cursor]
                loop.append(start)
                loop.reverse()
                return tuple(loop)
            if neighbor in seen:
                continue
            seen.add(neighbor)
            parents[neighbor] = node
            queue.append(neighbor)
    return (start,)  # pragma: no cover - a component of size > 1 always closes a loop
