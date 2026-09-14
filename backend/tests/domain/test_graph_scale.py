"""Traversal against generated graphs no hand-written fixture would contain.

The canonical Phase 0 scenarios are small by design — they exist to pin semantics. These
tests pin the properties that only show up at size: that deep nesting does not exhaust the
Python stack, that a wide group costs one query rather than thousands, and that a graph
built to be pathological still terminates and still reports honest bounds.
"""

from __future__ import annotations

import pytest

from app.domain import (
    MAX_DEPTH_CEILING,
    Direction,
    GraphEdge,
    TraversalLimits,
    TruncationReason,
    expand,
    find_cycles,
    find_paths,
)
from tests.support.graph import InMemoryAdjacency, chain, edge, fan_out, ring


class TestDeepGraphs:
    async def test_a_chain_deeper_than_the_ceiling_stops_at_the_ceiling(self):
        # No caller can ask for more than MAX_DEPTH_CEILING hops, so a 1,000-level chain
        # is answered as a bounded lower bound rather than walked to the end.
        provider = InMemoryAdjacency(chain(1_000))

        result = await expand(
            "n1000", Direction.DOWN, provider, TraversalLimits(max_depth=MAX_DEPTH_CEILING)
        )

        assert len(result.nodes) == MAX_DEPTH_CEILING
        assert result.depth_reached == MAX_DEPTH_CEILING
        assert TruncationReason.MAX_DEPTH in result.truncation
        assert not result.complete

    async def test_a_chain_at_the_ceiling_is_walked_completely(self):
        provider = InMemoryAdjacency(chain(MAX_DEPTH_CEILING))

        result = await expand(
            f"n{MAX_DEPTH_CEILING}",
            Direction.DOWN,
            provider,
            TraversalLimits(max_depth=MAX_DEPTH_CEILING),
        )

        assert result.complete
        assert len(result.nodes) == MAX_DEPTH_CEILING

    def test_cycle_detection_survives_a_deep_acyclic_chain(self):
        # Tarjan's algorithm is textbook-recursive; this pins the iterative rewrite.
        assert find_cycles(chain(5000)) == ()

    def test_cycle_detection_survives_a_deep_ring(self):
        cycles = find_cycles(ring(5000))

        assert len(cycles) == 1
        assert len(cycles[0].members) == 5000
        assert len(cycles[0].representative_path) == 5001

    async def test_the_shortest_path_is_recorded_for_every_node_in_a_deep_chain(self):
        depth = 120
        provider = InMemoryAdjacency(chain(depth))
        result = await expand(
            f"n{depth}", Direction.DOWN, provider, TraversalLimits(max_depth=depth)
        )

        deepest = result.node("n0")
        assert deepest is not None
        assert deepest.depth == depth
        assert len(deepest.path) == depth + 1
        assert deepest.path[0] == f"n{depth}"
        assert deepest.path[-1] == "n0"

    async def test_a_deep_chain_costs_one_query_per_level(self):
        depth = 100
        provider = InMemoryAdjacency(chain(depth))
        await expand(f"n{depth}", Direction.DOWN, provider, TraversalLimits(max_depth=depth))

        # depth levels plus the final empty level that proves the walk finished.
        assert provider.calls == depth + 1


class TestWideGraphs:
    async def test_a_group_with_fifty_thousand_members_expands_in_two_queries(self):
        provider = InMemoryAdjacency(fan_out(50_000))

        result = await expand("g0", Direction.DOWN, provider)

        assert len(result.nodes) == 50_000
        assert result.complete
        # One call for the root, one for the 50,000-node frontier. Not 50,001.
        assert provider.calls == 2
        assert provider.keys_requested == 50_001

    async def test_a_group_wider_than_the_node_limit_reports_truncation(self):
        provider = InMemoryAdjacency(fan_out(50_000))

        result = await expand("g0", Direction.DOWN, provider, TraversalLimits(max_nodes=1_000))

        assert len(result.nodes) == 1_000
        assert not result.complete
        assert TruncationReason.MAX_NODES in result.truncation

    async def test_a_wide_and_deep_graph_stays_bounded(self):
        # 20 levels of 50: 1,000 groups, 51,000 principals if fully expanded.
        edges: list[GraphEdge] = []
        for level in range(20):
            for index in range(50):
                edges.append(edge(f"L{level}", f"L{level + 1}_{index}"))
                edges.append(edge(f"L{level + 1}_{index}", f"L{level + 1}"))
        provider = InMemoryAdjacency(edges)

        result = await expand("L0", Direction.DOWN, provider, TraversalLimits(max_nodes=500))

        assert len(result.nodes) == 500
        assert TruncationReason.MAX_NODES in result.truncation


class TestPathologicalGraphs:
    async def test_a_graph_that_is_entirely_one_cycle_terminates(self):
        size = 2_000
        provider = InMemoryAdjacency(ring(size))

        result = await expand("r0", Direction.DOWN, provider, TraversalLimits(max_depth=128))

        assert len(result.nodes) == 128
        assert TruncationReason.MAX_DEPTH in result.truncation
        # The traversal stopped, but the cycle within what it saw is still reported.
        assert result.cycles == ()  # the ring does not close inside 128 of 2,000 hops

    async def test_a_cycle_reached_through_a_long_tail_is_still_reported(self):
        edges = [*chain(40), *ring(3, prefix="c"), edge("c0", "n40")]
        provider = InMemoryAdjacency(edges)

        result = await expand("c0", Direction.UP, provider, TraversalLimits(max_depth=64))

        assert len(result.cycles) == 1
        assert result.cycles[0].members == ("c0", "c1", "c2")

    async def test_exponential_path_counts_are_capped_not_enumerated(self):
        # 15 stacked diamonds: 32,768 simple paths within the default depth budget.
        # Enumerating them all would be a denial of service dressed up as a query.
        levels = 15
        edges: list[GraphEdge] = []
        for level in range(levels):
            for branch in ("a", "b"):
                edges.append(edge(f"L{level}{branch}", f"N{level}"))
                edges.append(edge(f"N{level + 1}", f"L{level}{branch}"))
        provider = InMemoryAdjacency(edges)

        search = await find_paths(
            "N0", f"N{levels}", provider, TraversalLimits(max_depth=64, max_paths=25)
        )

        assert len(search.paths) == 25
        assert TruncationReason.MAX_PATHS in search.truncation
        assert not search.complete
        assert search.is_member, "a capped search still proves membership"

    @pytest.mark.parametrize("width", [1, 2, 500])
    async def test_every_member_of_a_wide_group_gets_a_one_hop_explanation(
        self, width: int
    ) -> None:
        provider = InMemoryAdjacency(fan_out(width))
        result = await expand("g0", Direction.DOWN, provider)

        assert len(result.nodes) == width
        assert {node.depth for node in result.nodes} == {1}
        assert all(node.path == ("g0", node.key) for node in result.nodes)
