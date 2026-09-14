"""Invariants that must hold on every graph, not only the ones somebody thought to write.

A hand-written test proves the traversal is right about one graph. These generate hundreds
of graphs — deep, wide, cyclic, disconnected, multi-edged — and assert the properties that
must survive all of them:

1. **Direct edges stay direct.** A node at depth 1 has a stored edge to the root, and every
   node with a stored edge to the root is at depth 1.
2. **Recursion never changes identity.** Every key an answer returns is a key that appears
   in the stored edges. Traversal moves between nodes; it never invents one.
3. **No path is fabricated.** Every consecutive pair on every returned path is a real stored
   edge, followed in the right direction.
4. **A cycle never creates infinite output.** Generation includes graphs that are almost
   entirely cycles; every traversal terminates within its bounds.
5. **Completeness is honest.** This is the important one, and the one a reference
   implementation is needed for: when a traversal reports ``complete``, its node set must
   equal the set an unbounded breadth-first search finds. A bounded search that quietly
   stopped early would pass every other property here and still hand an operator a short
   member list that looks authoritative.

Generation is seeded and the seeds are fixed, so a failure is reproducible: the same seed
always produces the same graph. :func:`random_graph` is the only source of randomness.
"""

from __future__ import annotations

import itertools
import random
from collections import deque
from collections.abc import Iterable, Sequence

import pytest

from app.domain import (
    DEFAULT_LIMITS,
    Direction,
    GraphEdge,
    MembershipEdgeKind,
    TraversalLimits,
    TruncationReason,
    expand,
    find_cycles,
    find_paths,
)
from tests.support.graph import InMemoryAdjacency, edge

SEEDS = tuple(range(40))
"""Forty graphs per property. Fixed, so a failure names the graph that produced it."""

EDGE_KINDS = (
    MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
    MembershipEdgeKind.PRIMARY_GROUP,
    MembershipEdgeKind.WELL_KNOWN_IMPLICIT,
)


def random_graph(seed: int) -> list[GraphEdge]:
    """A membership graph with the shapes a real directory produces.

    Deliberately not a uniform random graph: nodes are laid out in layers so that most
    edges run "upward" the way nesting does, and then a handful of back edges are added,
    which is exactly how a directory acquires the cycles it is not supposed to have. Some
    seeds also produce parallel edges of different kinds between the same pair — a user who
    is both a listed member and a primaryGroupID member of one group.
    """
    rng = random.Random(seed)
    layers = rng.randint(2, 7)
    width = rng.randint(1, 6)
    nodes = [[f"L{layer}N{index}" for index in range(width)] for layer in range(layers)]

    edges: dict[str, GraphEdge] = {}

    def add(group: str, member: str, kind: MembershipEdgeKind) -> None:
        if group == member:
            return  # a self-edge is not representable; the domain rejects it
        built = edge(group, member, kind=kind)
        edges[built.edge_key] = built

    for layer in range(layers - 1):
        for member in nodes[layer]:
            for _ in range(rng.randint(0, 3)):
                group = rng.choice(nodes[layer + 1])
                add(group, member, rng.choice(EDGE_KINDS))
    # Skip-level edges: a user nested straight into a group three levels up.
    for _ in range(rng.randint(0, 5)):
        low = rng.randrange(layers)
        high = rng.randrange(layers)
        if low < high:
            add(rng.choice(nodes[high]), rng.choice(nodes[low]), EDGE_KINDS[0])
    # Back edges, which is how a directory ends up with membership cycles.
    for _ in range(rng.randint(0, 3)):
        low = rng.randrange(layers)
        high = rng.randrange(layers)
        if low < high:
            add(rng.choice(nodes[low]), rng.choice(nodes[high]), EDGE_KINDS[0])
    return list(edges.values())


def all_keys(edges: Iterable[GraphEdge]) -> list[str]:
    keys: set[str] = set()
    for item in edges:
        keys |= {item.group_key, item.member_key}
    return sorted(keys)


def reference_reachable(edges: Sequence[GraphEdge], root: str, direction: Direction) -> set[str]:
    """Every node reachable from ``root``, computed with no limits at all.

    The simplest correct implementation there is, written to be obviously right rather than
    efficient. It is the oracle the bounded traversal is checked against.
    """
    adjacency: dict[str, list[str]] = {}
    for item in edges:
        adjacency.setdefault(item.origin(direction), []).append(item.endpoint(direction))

    seen: set[str] = set()
    queue = deque([root])
    while queue:
        node = queue.popleft()
        for far in adjacency.get(node, ()):
            if far not in seen:
                seen.add(far)
                queue.append(far)
    seen.discard(root)
    return seen


def reference_distance(
    edges: Sequence[GraphEdge], root: str, direction: Direction
) -> dict[str, int]:
    """Breadth-first distance from ``root`` to every reachable node."""
    adjacency: dict[str, list[str]] = {}
    for item in edges:
        adjacency.setdefault(item.origin(direction), []).append(item.endpoint(direction))

    distance = {root: 0}
    queue = deque([root])
    while queue:
        node = queue.popleft()
        for far in adjacency.get(node, ()):
            if far not in distance:
                distance[far] = distance[node] + 1
                queue.append(far)
    return distance


def reference_simple_paths(
    edges: Sequence[GraphEdge], member: str, group: str, max_depth: int
) -> set[tuple[str, ...]]:
    """Every simple path from ``member`` up to ``group``, by exhaustive search."""
    outgoing: dict[str, list[str]] = {}
    for item in edges:
        outgoing.setdefault(item.member_key, []).append(item.group_key)

    found: set[tuple[str, ...]] = set()

    def walk(node: str, chain: tuple[str, ...]) -> None:
        if len(chain) - 1 >= max_depth:
            return
        for far in outgoing.get(node, ()):
            if far in chain:
                continue
            if far == group:
                found.add((*chain, far))
                continue
            walk(far, (*chain, far))

    walk(member, (member,))
    return found


GENEROUS = TraversalLimits(max_depth=64, max_nodes=10_000, max_edges=50_000, max_paths=1_000)
"""Far above anything `random_graph` produces, so a complete answer is genuinely complete."""


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("direction", [Direction.DOWN, Direction.UP])
class TestExpansionProperties:
    async def test_a_complete_answer_is_every_reachable_node(
        self, seed: int, direction: Direction
    ) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        for root in all_keys(edges):
            result = await expand(root, direction, provider, GENEROUS)

            assert result.complete, f"seed {seed} exceeded the generous limits at {root}"
            assert set(result.keys) == reference_reachable(edges, root, direction)

    async def test_recursion_never_invents_a_node(self, seed: int, direction: Direction) -> None:
        edges = random_graph(seed)
        known = set(all_keys(edges))
        provider = InMemoryAdjacency(edges)
        for root in all_keys(edges):
            result = await expand(root, direction, provider, GENEROUS)

            assert set(result.keys) <= known
            assert root not in set(result.keys), "a principal is not a member of itself"

    async def test_direct_edges_stay_direct(self, seed: int, direction: Direction) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        for root in all_keys(edges):
            direct = {item.endpoint(direction) for item in edges if item.origin(direction) == root}
            direct.discard(root)

            result = await expand(root, direction, provider, GENEROUS)
            at_depth_one = {node.key for node in result.nodes if node.depth == 1}

            assert at_depth_one == direct

    async def test_every_depth_is_the_shortest_distance(
        self, seed: int, direction: Direction
    ) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        for root in all_keys(edges):
            expected = reference_distance(edges, root, direction)
            result = await expand(root, direction, provider, GENEROUS)

            for node in result.nodes:
                assert node.depth == expected[node.key]

    async def test_no_path_is_fabricated(self, seed: int, direction: Direction) -> None:
        edges = random_graph(seed)
        stored = {(item.origin(direction), item.endpoint(direction)) for item in edges}
        by_key = {item.edge_key: item for item in edges}
        provider = InMemoryAdjacency(edges)

        for root in all_keys(edges):
            result = await expand(root, direction, provider, GENEROUS)
            for node in result.nodes:
                assert node.path[0] == root
                assert node.path[-1] == node.key
                assert len(set(node.path)) == len(node.path), "a shortest path never repeats"
                assert len(node.edge_keys) == len(node.path) - 1
                for index, edge_key in enumerate(node.edge_keys):
                    hop = (node.path[index], node.path[index + 1])
                    assert hop in stored, f"{hop} is not a stored edge"
                    taken = by_key[edge_key]
                    assert (taken.origin(direction), taken.endpoint(direction)) == hop

    async def test_every_reported_edge_is_a_stored_edge(
        self, seed: int, direction: Direction
    ) -> None:
        edges = random_graph(seed)
        by_key = {item.edge_key: item for item in edges}
        provider = InMemoryAdjacency(edges)
        for root in all_keys(edges):
            result = await expand(root, direction, provider, GENEROUS)

            for item in result.edges:
                assert by_key[item.edge_key] == item

    async def test_the_answer_is_the_same_every_time(self, seed: int, direction: Direction) -> None:
        # Two runs over one graph must agree exactly, or a bounded answer would differ from
        # the next for no visible reason and a diff between scans would be meaningless.
        edges = random_graph(seed)
        for root in all_keys(edges):
            first = await expand(root, direction, InMemoryAdjacency(edges), GENEROUS)
            second = await expand(root, direction, InMemoryAdjacency(edges), GENEROUS)

            assert first == second


@pytest.mark.parametrize("seed", SEEDS)
class TestBoundedTraversalProperties:
    @pytest.mark.parametrize("max_nodes", [1, 3, 10])
    async def test_a_node_limit_is_never_exceeded_and_never_lies(
        self, seed: int, max_nodes: int
    ) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        limits = TraversalLimits(max_depth=64, max_nodes=max_nodes, max_edges=50_000)
        for root in all_keys(edges):
            result = await expand(root, Direction.DOWN, provider, limits)
            reachable = reference_reachable(edges, root, Direction.DOWN)

            assert len(result.nodes) <= max_nodes
            if result.complete:
                assert set(result.keys) == reachable
            else:
                assert set(result.keys) <= reachable
                assert result.truncation, "an incomplete answer must say which limit stopped it"

    @pytest.mark.parametrize("max_depth", [1, 2, 3])
    async def test_a_depth_limit_keeps_only_nodes_within_that_distance(
        self, seed: int, max_depth: int
    ) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        limits = TraversalLimits(max_depth=max_depth, max_nodes=10_000, max_edges=50_000)
        for root in all_keys(edges):
            expected = reference_distance(edges, root, Direction.DOWN)
            within = {key for key, distance in expected.items() if 0 < distance <= max_depth}

            result = await expand(root, Direction.DOWN, provider, limits)

            assert set(result.keys) == within
            beyond = any(distance > max_depth for distance in expected.values())
            assert (TruncationReason.MAX_DEPTH in result.truncation) is beyond, (
                "reaching the depth limit with nothing beyond it is a complete answer; "
                "reaching it with more beyond it is not"
            )

    # Depth is swept alongside the edge budget on purpose. The two limits interact: a
    # traversal stopped by the depth limit spends one extra lookup proving its frontier is
    # exhausted, and that probe is where an exhausted edge budget can drop a real edge. A
    # sweep over edge budgets alone never reaches that code at all.
    @pytest.mark.parametrize("max_depth", [1, 2, 3, 64])
    @pytest.mark.parametrize("max_edges", [1, 2, 5, 20])
    async def test_an_edge_limit_never_produces_a_silently_short_answer(
        self, seed: int, max_depth: int, max_edges: int
    ) -> None:
        # The property the boundary probe exists for: if any stored edge out of a reached
        # node was not considered, the answer must not call itself complete.
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        limits = TraversalLimits(max_depth=max_depth, max_nodes=10_000, max_edges=max_edges)
        for root in all_keys(edges):
            result = await expand(root, Direction.DOWN, provider, limits)
            if not result.complete:
                continue

            reached = {root, *result.keys}
            expected_edges = {item.edge_key for item in edges if item.group_key in reached}
            assert expected_edges <= {item.edge_key for item in result.edges}, (
                f"seed {seed}, root {root}: an answer called itself complete while edges "
                "out of nodes it reached had been dropped"
            )

    async def test_every_traversal_terminates_however_cyclic_the_graph(self, seed: int) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        for root in all_keys(edges):
            result = await expand(root, Direction.DOWN, provider, DEFAULT_LIMITS)

            assert len(result.nodes) <= DEFAULT_LIMITS.max_nodes
            assert len(result.edges) <= DEFAULT_LIMITS.max_edges
            assert result.depth_reached <= DEFAULT_LIMITS.max_depth


@pytest.mark.parametrize("seed", SEEDS)
class TestPathProperties:
    async def test_enumeration_finds_exactly_the_simple_paths(self, seed: int) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        keys = all_keys(edges)
        for member in keys:
            for group in keys:
                if member == group:
                    continue
                search = await find_paths(member, group, provider, GENEROUS)
                if not search.complete:
                    continue

                found = {path.nodes for path in search.paths}
                assert found == reference_simple_paths(edges, member, group, GENEROUS.max_depth)

    async def test_every_path_is_simple_distinct_and_real(self, seed: int) -> None:
        edges = random_graph(seed)
        stored = {(item.member_key, item.group_key) for item in edges}
        provider = InMemoryAdjacency(edges)
        keys = all_keys(edges)
        for member in keys:
            for group in keys:
                if member == group:
                    continue
                search = await find_paths(member, group, provider, GENEROUS)

                # Distinct by the memberships taken, not by the nodes visited. Two
                # parallel edges between one pair — a listed member who is also a
                # primaryGroupID member — are two separate grants, and removing one leaves
                # the access in place, so both are reported.
                signatures = {
                    (path.nodes, tuple(hop.edge_key for hop in path.edges)) for path in search.paths
                }
                assert len(signatures) == len(search.paths)
                for path in search.paths:
                    assert path.nodes[0] == member
                    assert path.nodes[-1] == group
                    assert len(set(path.nodes)) == len(path.nodes)
                    for lower, upper in itertools.pairwise(path.nodes):
                        assert (lower, upper) in stored
                    assert len(path.edges) == path.length

    async def test_parallel_memberships_are_reported_as_separate_routes(self, seed: int) -> None:
        # Not an incidental property: a user who is both a listed member of Finance-RW and
        # a primaryGroupID member of it has two grants. Collapsing them to one path would
        # tell an operator that removing the listed membership removes the access.
        edges = random_graph(seed)
        parallel: dict[tuple[str, str], list[GraphEdge]] = {}
        for item in edges:
            parallel.setdefault((item.member_key, item.group_key), []).append(item)
        pairs = [pair for pair, group in parallel.items() if len(group) > 1]
        if not pairs:
            pytest.skip(f"seed {seed} produced no parallel memberships")

        provider = InMemoryAdjacency(edges)
        member, group = pairs[0]
        search = await find_paths(member, group, provider, GENEROUS)

        direct = [path for path in search.paths if path.nodes == (member, group)]
        assert len(direct) == len(parallel[(member, group)])
        assert len({path.edges[0].kind for path in direct}) == len(direct)

    async def test_membership_agrees_with_the_upward_expansion(self, seed: int) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        keys = all_keys(edges)
        for member in keys:
            expansion = await expand(member, Direction.UP, provider, GENEROUS)
            assert expansion.complete
            groups = set(expansion.keys)

            for group in keys:
                if member == group:
                    continue
                search = await find_paths(member, group, provider, GENEROUS)

                assert search.complete
                assert search.is_member is (group in groups), (
                    "a path search and an upward expansion must never disagree about "
                    "whether a membership exists"
                )

    async def test_a_capped_search_reports_the_cap_rather_than_a_short_list(
        self, seed: int
    ) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        keys = all_keys(edges)
        limits = TraversalLimits(max_depth=64, max_nodes=10_000, max_edges=50_000, max_paths=1)
        for member in keys:
            for group in keys:
                if member == group:
                    continue
                full = await find_paths(member, group, provider, GENEROUS)
                capped = await find_paths(member, group, provider, limits)

                assert len(capped.paths) <= 1
                if len(full.paths) > 1:
                    assert not capped.complete
                    assert TruncationReason.MAX_PATHS in capped.truncation


@pytest.mark.parametrize("seed", SEEDS)
class TestCycleProperties:
    def test_components_partition_the_nodes_they_cover(self, seed: int) -> None:
        edges = random_graph(seed)
        cycles = find_cycles(edges)

        members = [key for cycle in cycles for key in cycle.members]
        assert len(members) == len(set(members)), "a node belongs to at most one component"

    def test_every_component_is_strongly_connected_in_both_directions(self, seed: int) -> None:
        edges = random_graph(seed)
        outgoing: dict[str, list[str]] = {}
        for item in edges:
            outgoing.setdefault(item.group_key, []).append(item.member_key)

        for cycle in find_cycles(edges):
            for start in cycle.members:
                reachable = set()
                queue = deque([start])
                while queue:
                    node = queue.popleft()
                    for far in outgoing.get(node, ()):
                        if far not in reachable:
                            reachable.add(far)
                            queue.append(far)
                assert set(cycle.members) <= reachable | {start}

    def test_every_representative_loop_is_walkable(self, seed: int) -> None:
        edges = random_graph(seed)
        stored = {(item.group_key, item.member_key) for item in edges}

        for cycle in find_cycles(edges):
            loop = cycle.representative_path
            assert loop[0] == loop[-1]
            assert len(loop) >= 3, "a loop needs at least two distinct nodes"
            for origin, far in itertools.pairwise(loop):
                assert (origin, far) in stored

    def test_a_graph_with_no_back_edges_reports_no_cycles(self, seed: int) -> None:
        # Strip the back edges out of the generated graph and the answer must be empty:
        # otherwise the detector is reporting components that are not cycles at all.
        edges = random_graph(seed)
        layered = [item for item in edges if _layer(item.member_key) < _layer(item.group_key)]

        assert find_cycles(layered) == ()

    async def test_cycles_reported_by_an_expansion_lie_inside_what_it_reached(
        self, seed: int
    ) -> None:
        edges = random_graph(seed)
        provider = InMemoryAdjacency(edges)
        for root in all_keys(edges):
            result = await expand(root, Direction.DOWN, provider, GENEROUS)
            reached = {root, *result.keys}

            for cycle in result.cycles:
                assert set(cycle.members) <= reached, (
                    "a cycle elsewhere in the directory is not this query's finding"
                )


def _layer(key: str) -> int:
    return int(key[1 : key.index("N")])
