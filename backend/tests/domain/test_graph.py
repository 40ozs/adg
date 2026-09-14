"""Traversal of the membership graph: correctness, explanations, cycles, and bounds."""

from __future__ import annotations

import pytest

from app.domain import (
    Direction,
    DomainValidationError,
    GraphEdge,
    MembershipEdgeKind,
    PrincipalKind,
    TraversalLimits,
    TruncationReason,
    expand,
    find_cycles,
    find_paths,
    simple_paths,
)
from tests.fixtures import load_scenario
from tests.support.graph import (
    InMemoryAdjacency,
    chain,
    diamond,
    edge,
    edges_from_observations,
    fan_out,
    ring,
)

ALICE = "S-1-5-21-1004336348-1177238915-682003330-1104"
FINANCE_TEAM = "S-1-5-21-1004336348-1177238915-682003330-1201"
FINANCE_RW = "S-1-5-21-1004336348-1177238915-682003330-1202"
AUDITORS = "S-1-5-21-1004336348-1177238915-682003330-1204"
DOMAIN_USERS = "S-1-5-21-1004336348-1177238915-682003330-513"
RING_A = "S-1-5-21-1004336348-1177238915-682003330-1210"
RING_B = "S-1-5-21-1004336348-1177238915-682003330-1211"


def scenario_adjacency(name: str) -> InMemoryAdjacency:
    return InMemoryAdjacency(edges_from_observations(load_scenario(name).edges))


class TestEdgeInvariants:
    def test_an_edge_needs_both_endpoints(self):
        with pytest.raises(DomainValidationError, match="both endpoints"):
            GraphEdge(edge_key="k", group_key="g", member_key="")

    def test_a_self_edge_is_rejected(self):
        # Windows never creates one; observing it means a collector defect, and traversal
        # would report a principal as a member of itself.
        with pytest.raises(DomainValidationError, match="member of itself"):
            GraphEdge(edge_key="k", group_key="g", member_key="g")

    def test_endpoint_and_origin_follow_the_direction(self):
        item = edge("group", "member")
        assert item.endpoint(Direction.DOWN) == "member"
        assert item.origin(Direction.DOWN) == "group"
        assert item.endpoint(Direction.UP) == "group"
        assert item.origin(Direction.UP) == "member"

    def test_direction_inverse(self):
        assert Direction.DOWN.inverse is Direction.UP
        assert Direction.UP.inverse is Direction.DOWN


class TestNestedExpansion:
    """Scenario 03: alice → Finance-Team → Finance-RW."""

    async def test_effective_members_include_the_nested_user(self):
        provider = scenario_adjacency("03-nested-group-grant")
        result = await expand(FINANCE_RW, Direction.DOWN, provider)

        assert result.keys == (FINANCE_TEAM, ALICE)
        assert result.complete
        assert result.depth_reached == 2

    async def test_the_shortest_path_is_the_explanation(self):
        provider = scenario_adjacency("03-nested-group-grant")
        result = await expand(FINANCE_RW, Direction.DOWN, provider)

        alice = result.node(ALICE)
        assert alice is not None
        assert alice.depth == 2
        assert alice.path == (FINANCE_RW, FINANCE_TEAM, ALICE)
        assert alice.edge_kinds == (
            MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
            MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
        )

    async def test_the_expected_path_from_the_fixture_is_reproduced(self):
        expected = load_scenario("03-nested-group-grant").expectations["expected_path"]
        provider = scenario_adjacency("03-nested-group-grant")

        search = await find_paths(ALICE, FINANCE_RW, provider)

        assert [list(path.nodes) for path in search.paths] == [expected]
        assert search.is_member
        assert search.complete

    async def test_upward_expansion_finds_the_containing_groups(self):
        provider = scenario_adjacency("03-nested-group-grant")
        result = await expand(ALICE, Direction.UP, provider)

        assert set(result.keys) == {FINANCE_TEAM, FINANCE_RW}

    async def test_a_breadth_first_level_is_one_provider_call(self):
        # One query per level, not per node: this is what keeps a wide group from turning
        # into thousands of round trips.
        provider = scenario_adjacency("03-nested-group-grant")
        await expand(FINANCE_RW, Direction.DOWN, provider)
        assert provider.calls == 3  # root level, its members, then an empty level


class TestMultiplePaths:
    """Scenario 04: three distinct routes, one of them a primary-group edge."""

    async def test_every_expected_path_is_found(self):
        scenario = load_scenario("04-multiple-membership-paths")
        provider = InMemoryAdjacency(edges_from_observations(scenario.edges))

        found: list[list[str]] = []
        for group in (FINANCE_RW, DOMAIN_USERS):
            search = await find_paths(ALICE, group, provider)
            found.extend(list(path.nodes) for path in search.paths)

        expected = scenario.expectations["expected_paths"]
        assert sorted(found) == sorted(expected)
        assert len(found) == scenario.expectations["distinct_paths_expected"]

    async def test_paths_are_returned_shortest_first(self):
        provider = scenario_adjacency("04-multiple-membership-paths")
        search = await find_paths(ALICE, FINANCE_RW, provider)

        lengths = [path.length for path in search.paths]
        assert lengths == sorted(lengths)
        assert all(path.length == 2 for path in search.paths)

    async def test_the_primary_group_edge_is_traversed(self):
        # A collector that reads only the member attribute loses this edge entirely; the
        # traversal must not lose it a second time.
        provider = scenario_adjacency("04-multiple-membership-paths")
        search = await find_paths(ALICE, DOMAIN_USERS, provider)

        assert len(search.paths) == 1
        assert search.paths[0].edge_kinds == (MembershipEdgeKind.PRIMARY_GROUP,)

    async def test_expansion_keeps_only_one_shortest_path_per_node(self):
        provider = scenario_adjacency("04-multiple-membership-paths")
        result = await expand(FINANCE_RW, Direction.DOWN, provider)

        alice = result.node(ALICE)
        assert alice is not None
        assert alice.depth == 2
        assert alice.path[0] == FINANCE_RW and alice.path[-1] == ALICE
        # The full set of routes is find_paths' job, and it disagrees with this count on
        # purpose: expansion answers "who", enumeration answers "how many ways".
        search = await find_paths(ALICE, FINANCE_RW, provider)
        assert len(search.paths) == 2

    async def test_a_diamond_yields_two_paths(self):
        provider = InMemoryAdjacency(diamond())
        search = await find_paths("leaf", "top", provider)

        assert [list(path.nodes) for path in search.paths] == [
            ["leaf", "left", "top"],
            ["leaf", "right", "top"],
        ]


class TestSimplePathsOverASubgraph:
    """`simple_paths` is the enumeration `find_paths` performs, without the provider.

    It exists because the causality engine holds one traversal's edges and needs chains to
    many trustees: going back to an `AdjacencyProvider` per trustee would turn one bounded
    traversal into several. Extracting it rather than writing a second DFS is what keeps the
    ordering guarantee and the cycle handling in one place — two implementations would
    eventually disagree, and the disagreement would be an explanation that does not match
    the membership answer beside it.
    """

    async def test_it_agrees_with_find_paths_on_the_same_graph(self):
        """The property that makes the extraction safe rather than merely tidy."""
        scenario = load_scenario("04-multiple-membership-paths")
        provider = InMemoryAdjacency(edges_from_observations(scenario.edges))

        for group in (FINANCE_RW, DOMAIN_USERS):
            search = await find_paths(ALICE, group, provider)
            direct = simple_paths(provider.edges, ALICE, group)
            assert [path.nodes for path in direct.paths] == [path.nodes for path in search.paths], (
                group
            )

    def test_it_enumerates_every_route_through_a_diamond(self):
        found = simple_paths(diamond(), "leaf", "top")

        assert [list(path.nodes) for path in found.paths] == [
            ["leaf", "left", "top"],
            ["leaf", "right", "top"],
        ]
        assert found.complete is True

    def test_it_is_ordered_independently_of_the_edge_order_supplied(self):
        forward = simple_paths(diamond(), "leaf", "top")
        reversed_edges = simple_paths(list(reversed(diamond())), "leaf", "top")

        assert [p.nodes for p in forward.paths] == [p.nodes for p in reversed_edges.paths]

    def test_a_ring_terminates_and_yields_no_path_to_an_unreachable_node(self):
        found = simple_paths(ring(4), "r0", "unrelated")

        assert found.paths == ()

    def test_a_ring_yields_one_simple_path_between_two_of_its_members(self):
        """Following the loop round would enumerate infinitely many non-simple chains."""
        found = simple_paths(ring(4), "r0", "r2")

        assert [list(path.nodes) for path in found.paths] == [["r0", "r1", "r2"]]

    def test_the_path_limit_truncates_and_says_so(self):
        found = simple_paths(diamond(), "leaf", "top", TraversalLimits(max_paths=1))

        assert len(found.paths) == 1
        assert TruncationReason.MAX_PATHS in found.truncation
        assert found.complete is False

    def test_the_depth_limit_truncates_and_says_so(self):
        found = simple_paths(chain(5), "n0", "n5", TraversalLimits(max_depth=2))

        assert found.paths == ()
        assert TruncationReason.MAX_DEPTH in found.truncation

    def test_an_empty_subgraph_yields_nothing_rather_than_failing(self):
        """The state a caller is in when no edge was collected; not an error."""
        found = simple_paths((), ALICE, FINANCE_RW)

        assert found.paths == ()
        assert found.complete is True

    def test_a_principal_is_not_a_member_of_itself(self):
        with pytest.raises(DomainValidationError, match="two distinct keys"):
            simple_paths(diamond(), "leaf", "leaf")


class TestCycles:
    """Scenario 05: Ring-A and Ring-B contain each other."""

    async def test_expansion_terminates_and_stays_complete(self):
        provider = scenario_adjacency("05-cyclic-group-graph")
        result = await expand(FINANCE_RW, Direction.DOWN, provider)

        assert result.complete, "a cycle must not look like a truncated traversal"
        assert set(result.keys) == {RING_A, RING_B, ALICE}

    async def test_the_cycle_is_reported_with_its_members(self):
        scenario = load_scenario("05-cyclic-group-graph")
        provider = InMemoryAdjacency(edges_from_observations(scenario.edges))

        result = await expand(FINANCE_RW, Direction.DOWN, provider)

        assert len(result.cycles) == 1
        assert list(result.cycles[0].members) == sorted(scenario.expectations["cycle_members"])

    async def test_the_cycle_carries_a_concrete_loop(self):
        provider = scenario_adjacency("05-cyclic-group-graph")
        result = await expand(FINANCE_RW, Direction.DOWN, provider)

        loop = result.cycles[0].representative_path
        assert loop[0] == loop[-1], "a representative loop must close"
        assert len(loop) == 3
        assert set(loop) == {RING_A, RING_B}

    async def test_path_enumeration_does_not_walk_the_cycle(self):
        provider = scenario_adjacency("05-cyclic-group-graph")
        search = await find_paths(ALICE, FINANCE_RW, provider)

        assert search.is_member
        assert all(len(set(path.nodes)) == len(path.nodes) for path in search.paths), (
            "every enumerated path must be simple"
        )
        assert search.cycles

    @pytest.mark.parametrize("size", [2, 3, 7, 50])
    async def test_rings_of_any_size_terminate_and_are_reported(self, size: int) -> None:
        provider = InMemoryAdjacency(ring(size))
        # Walking a ring of N groups costs N hops before it closes, so the depth budget has
        # to admit it; the point of the test is termination, not the default limit.
        result = await expand("r0", Direction.DOWN, provider, TraversalLimits(max_depth=size + 1))

        assert result.complete
        assert len(result.cycles) == 1
        assert len(result.cycles[0].members) == size
        assert len(result.cycles[0].representative_path) == size + 1

    async def test_two_independent_cycles_are_reported_separately(self):
        edges = [
            *ring(2, prefix="a"),
            *ring(3, prefix="b"),
            edge("top", "a0"),
            edge("top", "b0"),
        ]
        result = await expand("top", Direction.DOWN, InMemoryAdjacency(edges))

        assert [cycle.length for cycle in result.cycles] == [2, 3]

    def test_an_acyclic_graph_reports_no_cycles(self):
        assert find_cycles(chain(5)) == ()
        assert find_cycles(diamond()) == ()

    def test_cycle_detection_ignores_edges_outside_the_restriction(self):
        # A cycle among nodes the traversal never reached is not this traversal's finding.
        edges = [*ring(2, prefix="z"), edge("root", "leaf")]
        assert find_cycles(edges, restrict_to={"root", "leaf"}) == ()
        assert len(find_cycles(edges)) == 1


class TestLimits:
    async def test_depth_beyond_the_limit_truncates_and_says_so(self):
        provider = InMemoryAdjacency(chain(10))
        result = await expand("n10", Direction.DOWN, provider, TraversalLimits(max_depth=3))

        assert result.truncation == (TruncationReason.MAX_DEPTH,)
        assert not result.complete
        assert result.depth_reached == 3
        assert len(result.nodes) == 3

    async def test_a_traversal_that_exactly_fits_is_complete(self):
        # The depth limit was reached but nothing lay beyond it. Reporting this as
        # truncated would turn a complete member list into an unusable lower bound.
        provider = InMemoryAdjacency(chain(3))
        result = await expand("n3", Direction.DOWN, provider, TraversalLimits(max_depth=3))

        assert result.complete
        assert len(result.nodes) == 3

    async def test_a_ring_at_the_exact_depth_limit_is_complete(self):
        # The frontier at the limit has an edge, but only back to an already-visited node.
        provider = InMemoryAdjacency(ring(4))
        result = await expand("r0", Direction.DOWN, provider, TraversalLimits(max_depth=3))

        assert result.complete
        assert len(result.nodes) == 3

    async def test_node_limit_truncates(self):
        provider = InMemoryAdjacency(fan_out(100))
        result = await expand("g0", Direction.DOWN, provider, TraversalLimits(max_nodes=10))

        assert TruncationReason.MAX_NODES in result.truncation
        assert len(result.nodes) == 10

    async def test_edge_limit_truncates(self):
        provider = InMemoryAdjacency(fan_out(100))
        result = await expand("g0", Direction.DOWN, provider, TraversalLimits(max_edges=5))

        assert TruncationReason.MAX_EDGES in result.truncation
        assert len(result.edges) == 5

    async def test_path_limit_truncates(self):
        # A group reachable by many routes: 2^5 distinct paths through five diamonds.
        edges: list[GraphEdge] = []
        for level in range(5):
            edges.append(edge(f"L{level}a", f"N{level}"))
            edges.append(edge(f"L{level}b", f"N{level}"))
            edges.append(edge(f"N{level + 1}", f"L{level}a"))
            edges.append(edge(f"N{level + 1}", f"L{level}b"))
        provider = InMemoryAdjacency(edges)

        search = await find_paths("N0", "N5", provider, TraversalLimits(max_paths=7))

        assert len(search.paths) == 7
        assert search.truncation == (TruncationReason.MAX_PATHS,)
        assert not search.complete

        full = await find_paths("N0", "N5", provider, TraversalLimits(max_paths=100))
        assert len(full.paths) == 32
        assert full.complete

    def test_limits_above_the_ceiling_are_rejected(self):
        with pytest.raises(DomainValidationError, match="may not exceed"):
            TraversalLimits(max_depth=10_000)

    def test_limits_below_one_are_rejected(self):
        with pytest.raises(DomainValidationError, match="at least 1"):
            TraversalLimits(max_nodes=0)

    def test_clamping_reduces_rather_than_rejects(self):
        clamped = TraversalLimits().clamped(max_depth=10_000, max_paths=5)
        assert clamped.max_depth == 128
        assert clamped.max_paths == 5

    def test_clamping_ignores_none(self):
        base = TraversalLimits(max_depth=7)
        assert base.clamped(max_depth=None).max_depth == 7

    def test_clamping_rejects_an_unknown_limit(self):
        with pytest.raises(DomainValidationError, match="Unknown traversal limit"):
            TraversalLimits().clamped(max_width=5)


class TestMisuse:
    async def test_an_empty_root_is_rejected(self):
        with pytest.raises(DomainValidationError, match="needs a root key"):
            await expand("", Direction.DOWN, InMemoryAdjacency([]))

    async def test_paths_between_a_node_and_itself_are_rejected(self):
        with pytest.raises(DomainValidationError, match="not a member of itself"):
            await find_paths("x", "x", InMemoryAdjacency([]))

    async def test_a_provider_returning_a_misattributed_edge_is_rejected(self):
        class LyingProvider:
            async def neighbors(self, direction, keys):  # noqa: ANN202
                return {key: (edge("other-group", "other-member"),) for key in keys}

        with pytest.raises(DomainValidationError, match="misattributed edge"):
            await expand("root", Direction.DOWN, LyingProvider())

    async def test_an_unknown_root_yields_an_empty_complete_result(self):
        result = await expand("nobody", Direction.DOWN, InMemoryAdjacency(diamond()))

        assert result.nodes == ()
        assert result.complete
        assert result.depth_reached == 0


class TestMetadataCarriedThroughTraversal:
    async def test_the_reported_member_kind_survives_a_hop(self):
        edges = [
            edge("group", "user", member_kind=PrincipalKind.USER),
            edge("outer", "group", member_kind=PrincipalKind.DOMAIN_GROUP),
        ]
        result = await expand("outer", Direction.DOWN, InMemoryAdjacency(edges))

        kinds = {node.key: node.reported_kind for node in result.nodes}
        assert kinds == {"group": PrincipalKind.DOMAIN_GROUP, "user": PrincipalKind.USER}

    async def test_a_foreign_security_principal_hop_is_flagged_along_the_path(self):
        edges = [
            edge("outer", "fsp-group", is_foreign_security_principal=True),
            edge("fsp-group", "remote-user"),
        ]
        result = await expand("outer", Direction.DOWN, InMemoryAdjacency(edges))

        flags = {node.key: node.via_foreign_security_principal for node in result.nodes}
        assert flags == {"fsp-group": True, "remote-user": True}

    async def test_local_group_edges_stay_scoped_to_their_host(self):
        # BUILTIN\Administrators on two servers must never merge into one node.
        builtin = "S-1-5-32-544"
        alice = "S-1-5-21-1-2-3-1104"
        edges = [
            edge(
                f"fs01|{builtin}",
                alice,
                kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                host_key="fs01",
            ),
            edge(
                f"fs02|{builtin}",
                "S-1-5-21-1-2-3-1105",
                kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                host_key="fs02",
            ),
        ]
        provider = InMemoryAdjacency(edges)

        fs01 = await expand(f"fs01|{builtin}", Direction.DOWN, provider)
        assert fs01.keys == (alice,)

        fs02 = await expand(f"fs02|{builtin}", Direction.DOWN, provider)
        assert fs02.keys == ("S-1-5-21-1-2-3-1105",)
