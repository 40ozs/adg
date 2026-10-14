"""The traversal algorithms over in-memory graphs: no database, every property.

Each class pins one family of the lane's requirements: what a path is and which one is chosen,
what it rests on, what it may never do (jump hosts, follow provenance, follow history), how
it is bounded, and that nothing in an answer depends on the order rows arrive in.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import pytest

from app.domain.errors import DomainValidationError
from app.domain.relationships import (
    AdjacencyDirection,
    DerivationClass,
    NodeKind,
    NodeRef,
    RelationshipCondition,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipQualifier,
    RelationshipType,
)
from app.traversal.completeness import CompletenessReason
from app.traversal.model import AdjacencyBatch, ConditionOrigin, PathNote
from app.traversal.search import (
    ALTERNATE_WORK_BUDGET,
    SearchLimits,
    alternate_paths,
    neighbors,
    reverse_reachability,
    shortest_path,
)
from app.traversal.semantics import READING_TYPES, TraversalPolicy
from tests.support.traversal import (
    DOMAIN,
    FOREIGN_DOMAIN,
    Graph,
    MemoryAdjacency,
    domain,
    group,
    local,
    resource,
    user,
    well_known,
)

T = RelationshipType
ALICE, BOB, CAROL = user(1104), user(1105), user(1106)
TEAM, RW, AUDITORS = group(1201), group(1202), group(1210)
FS01_ADMINS = local("fs01", "S-1-5-32-544")
FS02_ADMINS = local("fs02", "S-1-5-32-544")
FS01_RDP = local("fs01", "S-1-5-32-555")
FS01_WINRM = local("fs01", "S-1-5-32-580")
PAYROLL = resource("\\\\fs01\\finance\\payroll")
FS02_DATA = resource("\\\\fs02\\data")


def keys(nodes: tuple[NodeRef, ...]) -> list[str]:
    return [node.key for node in nodes]


def payroll_estate() -> Graph:
    """alice → Finance-Team → Finance-RW → fs01 Administrators → payroll."""
    graph = Graph()
    graph.member(ALICE, TEAM)
    graph.member(TEAM, RW)
    graph.local_member(RW, FS01_ADMINS)
    graph.control(T.OWNS, FS01_ADMINS, PAYROLL, derivation_class=DerivationClass.OBSERVED)
    graph.control(T.WRITE_DACL, FS01_ADMINS, PAYROLL)
    graph.control(T.GENERIC_ALL, FS01_ADMINS, PAYROLL)
    return graph


# --- Shortest path ------------------------------------------------------------------------


class TestShortestPath:
    async def test_a_direct_relationship_is_a_one_step_path(self) -> None:
        graph = Graph()
        graph.control(T.GENERIC_WRITE, RW, PAYROLL)

        found = await shortest_path(RW, PAYROLL, MemoryAdjacency(graph))

        (path,) = found.paths
        assert path.hops == 1
        assert [edge.relationship_type for edge in path.steps[0].relationships] == [T.GENERIC_WRITE]
        assert found.reasons == ()

    async def test_nested_membership_is_its_real_direct_hops(self) -> None:
        """No nested_member_of edge exists or is needed: the chain is the stored edges."""
        graph = Graph()
        graph.member(ALICE, TEAM)
        graph.member(TEAM, RW)

        (path,) = (await shortest_path(ALICE, RW, MemoryAdjacency(graph))).paths

        assert keys(path.nodes) == [ALICE.key, TEAM.key, RW.key]
        assert [step.basis.relationship_type for step in path.steps] == [T.MEMBER_OF] * 2
        assert all(
            edge.relationship_type is not T.NESTED_MEMBER_OF
            for step in path.steps
            for edge in step.relationships
        )

    async def test_membership_leads_to_control(self) -> None:
        found = await shortest_path(ALICE, PAYROLL, MemoryAdjacency(payroll_estate()))

        (path,) = found.paths
        assert keys(path.nodes) == [ALICE.key, TEAM.key, RW.key, FS01_ADMINS.key, PAYROLL.key]
        assert [step.basis.relationship_type for step in path.steps[:3]] == [
            T.MEMBER_OF,
            T.MEMBER_OF,
            T.LOCAL_GROUP_MEMBER_OF,
        ]
        assert path.assessment.confidence is RelationshipConfidence.CONFIRMED
        assert not path.assessment.conditional
        assert path.assessment.notes == (PathNote.SHARE_LAYER_NOT_EVALUATED,)

    async def test_parallel_relationships_are_one_step_and_one_path(self) -> None:
        """owns, write_dacl and generic_all join the same pair: one route, three relationships."""
        provider = MemoryAdjacency(payroll_estate())
        shortest = await shortest_path(ALICE, PAYROLL, provider)
        every = await alternate_paths(ALICE, PAYROLL, MemoryAdjacency(payroll_estate()))

        last = shortest.paths[0].steps[-1]
        assert {edge.relationship_type for edge in last.relationships} == {
            T.OWNS,
            T.WRITE_DACL,
            T.GENERIC_ALL,
        }
        # Strongest first: the observed ownership, then the deterministic rights by type.
        assert [edge.relationship_type for edge in last.relationships] == [
            T.OWNS,
            T.GENERIC_ALL,
            T.WRITE_DACL,
        ]
        assert len(every.paths) == 1

    async def test_equal_length_routes_choose_the_smallest_node_sequence(self) -> None:
        graph = Graph()
        low, high = group(1300), group(1400)
        for middle in (high, low):  # inserted largest first
            graph.member(ALICE, middle)
            graph.control(T.GENERIC_WRITE, middle, PAYROLL)

        (path,) = (await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph))).paths

        assert path.nodes[1] == low

    @pytest.mark.parametrize("seed", range(8))
    async def test_the_choice_never_depends_on_row_order(self, seed: int) -> None:
        graph = Graph()
        middles = [group(1300 + index) for index in range(6)]
        for middle in middles:
            graph.member(ALICE, middle)
            graph.member(middle, RW)
        graph.control(T.GENERIC_WRITE, RW, PAYROLL)
        reference = await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph))
        shuffled = await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph, shuffle_seed=seed))

        assert shuffled.paths[0].identity == reference.paths[0].identity
        assert shuffled.paths[0].nodes[1] == middles[0]

    async def test_the_same_node_twice_is_refused(self) -> None:
        with pytest.raises(DomainValidationError):
            await shortest_path(ALICE, ALICE, MemoryAdjacency(Graph()))

    async def test_no_route_in_a_complete_graph_is_a_clean_empty_answer(self) -> None:
        found = await shortest_path(CAROL, PAYROLL, MemoryAdjacency(payroll_estate()))

        assert found.paths == ()
        assert found.reasons == ()

    async def test_a_not_current_relationship_is_never_followed(self) -> None:
        graph = payroll_estate()
        graph.set_currency(graph.edges[0], RelationshipCurrency.NOT_CURRENT)

        found = await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph))

        assert found.paths == ()
        assert found.exploration.not_current
        # History is not a boundary: a relationship measured gone does not weaken "no path".
        assert found.reasons == ()

    async def test_an_unknown_relationship_is_a_boundary_not_an_absence(self) -> None:
        graph = payroll_estate()
        graph.set_currency(graph.edges[0], RelationshipCurrency.UNKNOWN)

        found = await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph))

        assert found.paths == ()
        assert found.reasons == (CompletenessReason.UNKNOWN_RELATIONSHIPS,)

    async def test_a_cycle_is_crossed_once(self) -> None:
        graph = Graph()
        a, b, c = group(1501), group(1502), group(1503)
        graph.member(ALICE, a)
        graph.member(a, b)
        graph.member(b, c)
        graph.member(c, a)
        graph.control(T.GENERIC_WRITE, c, PAYROLL)

        found = await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph))

        assert keys(found.paths[0].nodes) == [ALICE.key, a.key, b.key, c.key, PAYROLL.key]


# --- What a path rests on -----------------------------------------------------------------


class TestConditionalAndUnresolved:
    async def test_a_conditional_hop_makes_the_path_conditional(self) -> None:
        graph = Graph()
        graph.member(ALICE, RW)
        grant = graph.control(
            T.GENERIC_ALL,
            RW,
            PAYROLL,
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=(RelationshipCondition.DENY_MAY_APPLY,),
        )

        (path,) = (await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph))).paths

        assert path.steps[0].basis.confidence is RelationshipConfidence.CONFIRMED
        assert path.steps[1].basis.conditional
        assert path.assessment.conditional
        assert path.assessment.confidence is RelationshipConfidence.PROBABLE
        (condition,) = path.assessment.conditions
        assert condition.condition is RelationshipCondition.DENY_MAY_APPLY
        assert condition.origin is ConditionOrigin.RELATIONSHIP
        assert condition.step == 1
        assert condition.relationship_id == grant.relationship_id

    async def test_confirmed_only_declines_the_conditional_hop_and_says_so(self) -> None:
        graph = Graph()
        graph.member(ALICE, RW)
        graph.control(
            T.GENERIC_ALL,
            RW,
            PAYROLL,
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=(RelationshipCondition.DENY_MAY_APPLY,),
        )

        found = await shortest_path(
            ALICE, PAYROLL, MemoryAdjacency(graph), policy=TraversalPolicy.CONFIRMED_ONLY
        )

        assert found.paths == ()
        assert found.reasons == (CompletenessReason.EXCLUDED_BY_POLICY,)
        assert len(found.exploration.excluded) == 1

    async def test_a_confirmed_route_beats_a_shorter_conditional_one_under_confirmed_only(
        self,
    ) -> None:
        graph = Graph()
        graph.control(
            T.GENERIC_ALL,
            ALICE,
            PAYROLL,
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=(RelationshipCondition.ACL_ORDER_UNKNOWN,),
        )
        graph.member(ALICE, RW)
        graph.control(T.GENERIC_WRITE, RW, PAYROLL)

        everything = await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph))
        confirmed = await shortest_path(
            ALICE, PAYROLL, MemoryAdjacency(graph), policy=TraversalPolicy.CONFIRMED_ONLY
        )

        assert everything.paths[0].hops == 1
        assert everything.paths[0].assessment.conditional
        assert confirmed.paths[0].hops == 2
        assert not confirmed.paths[0].assessment.conditional

    async def test_a_strong_and_a_weak_relationship_on_one_step_take_the_strong_one(
        self,
    ) -> None:
        graph = Graph()
        graph.control(T.WRITE_DACL, RW, PAYROLL)
        graph.control(
            T.GENERIC_ALL,
            RW,
            PAYROLL,
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=(RelationshipCondition.DENY_MAY_APPLY,),
        )

        (path,) = (await shortest_path(RW, PAYROLL, MemoryAdjacency(graph))).paths

        assert path.steps[0].basis.relationship_type is T.WRITE_DACL
        assert not path.assessment.conditional
        assert [edge.relationship_type for edge in path.steps[0].relationships] == [
            T.WRITE_DACL,
            T.GENERIC_ALL,
        ]

    async def test_an_unresolved_member_makes_the_path_indeterminate(self) -> None:
        foreign = NodeRef.principal(f"{FOREIGN_DOMAIN}-1500")
        graph = Graph()
        graph.member(
            foreign,
            RW,
            derivation_class=DerivationClass.UNRESOLVED,
            qualifiers=(RelationshipQualifier.FOREIGN_PRINCIPAL,),
        )
        graph.control(T.GENERIC_WRITE, RW, PAYROLL)

        (path,) = (await shortest_path(foreign, PAYROLL, MemoryAdjacency(graph))).paths

        assert path.assessment.unresolved
        assert path.assessment.confidence is RelationshipConfidence.INDETERMINATE
        assert RelationshipQualifier.FOREIGN_PRINCIPAL in path.assessment.qualifiers


class TestReadings:
    async def test_local_administration_is_read_on_the_membership_step(self) -> None:
        (path,) = (await shortest_path(ALICE, FS01_ADMINS, MemoryAdjacency(payroll_estate()))).paths

        last = path.steps[-1]
        assert last.basis.relationship_type is T.LOCAL_GROUP_MEMBER_OF
        assert [edge.relationship_type for edge in last.readings] == [T.LOCAL_ADMIN_OF]
        assert path.assessment.confidence is RelationshipConfidence.CONFIRMED

    async def test_readings_are_never_hops(self) -> None:
        """A reading on its own — no local_group_member_of beside it — leads nowhere."""
        graph = Graph()
        graph.member(ALICE, RW)
        reading = Graph()
        reading.local_member(RW, FS01_ADMINS)
        graph.add(next(e for e in reading.edges if e.relationship_type is T.LOCAL_ADMIN_OF))

        found = await shortest_path(ALICE, FS01_ADMINS, MemoryAdjacency(graph))

        assert found.paths == ()
        assert READING_TYPES.isdisjoint(
            edge.relationship_type for edge in found.exploration.excluded.values()
        )

    @pytest.mark.parametrize(
        ("target", "reading", "conditions"),
        [
            (
                FS01_RDP,
                T.REMOTE_DESKTOP_USER_OF,
                {
                    RelationshipCondition.LOGON_RIGHT_UNEVALUATED,
                    RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
                },
            ),
            (
                FS01_WINRM,
                T.REMOTE_MANAGEMENT_USER_OF,
                {
                    RelationshipCondition.SERVICE_ACCESS_POLICY_UNEVALUATED,
                    RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
                },
            ),
        ],
    )
    @pytest.mark.parametrize("policy", list(TraversalPolicy))
    async def test_a_path_ending_on_remote_access_is_conditional(
        self,
        target: NodeRef,
        reading: RelationshipType,
        conditions: set[RelationshipCondition],
        policy: TraversalPolicy,
    ) -> None:
        graph = Graph()
        graph.member(BOB, AUDITORS)
        graph.local_member(AUDITORS, target)

        (path,) = (await shortest_path(BOB, target, MemoryAdjacency(graph), policy=policy)).paths

        (found,) = path.steps[-1].readings
        assert found.relationship_type is reading
        assert found.conditional
        # The membership is confirmed; what it may allow is not.
        assert path.assessment.chain_confidence is RelationshipConfidence.CONFIRMED
        assert path.assessment.confidence is RelationshipConfidence.PROBABLE
        assert path.assessment.conditional
        reading_conditions = {
            item.condition
            for item in path.assessment.conditions
            if item.origin is ConditionOrigin.READING
        }
        assert reading_conditions == conditions

    async def test_a_reading_in_the_middle_of_a_path_does_not_condition_it(self) -> None:
        """Membership of Remote Desktop Users puts its SID in the token for any logon; using
        the group's NTFS rights does not depend on signing in over Remote Desktop."""
        graph = Graph()
        graph.local_member(BOB, FS01_RDP)
        graph.control(T.GENERIC_WRITE, FS01_RDP, PAYROLL)

        (path,) = (await shortest_path(BOB, PAYROLL, MemoryAdjacency(graph))).paths

        assert path.steps[0].readings
        assert not path.assessment.conditional

    async def test_a_local_account_using_local_administration_is_token_filtered(self) -> None:
        local_user = local("fs01", "S-1-5-21-111-222-333-1001")
        graph = Graph()
        graph.local_member(local_user, FS01_ADMINS)
        graph.control(T.GENERIC_ALL, FS01_ADMINS, PAYROLL)

        (path,) = (await shortest_path(local_user, PAYROLL, MemoryAdjacency(graph))).paths

        (condition,) = path.assessment.conditions
        assert condition.condition is RelationshipCondition.TOKEN_FILTERING_NOT_MODELED
        assert condition.origin is ConditionOrigin.PATH
        assert condition.step == 1
        assert path.assessment.chain_confidence is RelationshipConfidence.CONFIRMED
        assert path.assessment.confidence is RelationshipConfidence.PROBABLE

    async def test_a_domain_account_or_an_end_at_the_group_is_not_token_filtered(self) -> None:
        local_user = local("fs01", "S-1-5-21-111-222-333-1001")
        graph = payroll_estate()
        graph.local_member(local_user, FS01_ADMINS)

        domain_route = (await shortest_path(ALICE, PAYROLL, MemoryAdjacency(graph))).paths[0]
        ends_there = (await shortest_path(local_user, FS01_ADMINS, MemoryAdjacency(graph))).paths[0]

        assert not domain_route.assessment.conditional
        assert not ends_there.assessment.conditional


class TestInformationalAndScope:
    async def test_a_foreign_principals_domain_is_never_a_privilege_step(self) -> None:
        foreign = NodeRef.principal(f"{FOREIGN_DOMAIN}-1500")
        graph = Graph()
        graph.foreign(foreign, domain())

        found = await shortest_path(foreign, domain(), MemoryAdjacency(graph))

        assert found.paths == ()

    async def test_the_same_local_sid_on_two_hosts_is_two_nodes(self) -> None:
        """A route into fs01's Administrators never continues from fs02's."""
        graph = Graph()
        graph.member(ALICE, RW)
        graph.local_member(RW, FS01_ADMINS)
        graph.control(T.GENERIC_ALL, FS02_ADMINS, FS02_DATA)

        found = await shortest_path(ALICE, FS02_DATA, MemoryAdjacency(graph))

        assert found.paths == ()
        assert FS01_ADMINS != FS02_ADMINS
        assert FS01_ADMINS.sid == FS02_ADMINS.sid

    async def test_a_host_spelled_two_ways_is_two_hosts(self) -> None:
        """A local group and an NTFS trustee join only when both collectors name the host
        identically (GRAPH-01B limitation). Nothing here reconciles fs01 with fs01.corp."""
        graph = Graph()
        graph.local_member(RW, FS01_ADMINS)
        long_name = local("fs01.corp.example.com", "S-1-5-32-544")
        graph.control(T.GENERIC_ALL, long_name, resource("\\\\fs01.corp.example.com\\data"))

        found = await shortest_path(
            RW, resource("\\\\fs01.corp.example.com\\data"), MemoryAdjacency(graph)
        )

        assert found.paths == ()

    async def test_a_misattributed_relationship_is_refused(self) -> None:
        class Liar(MemoryAdjacency):
            async def adjacent(
                self,
                nodes: Sequence[NodeRef],
                direction: AdjacencyDirection,
                types: frozenset[RelationshipType],
                *,
                limit: int,
            ) -> AdjacencyBatch:
                batch = await super().adjacent(nodes, direction, types, limit=limit)
                other = Graph()
                other.member(CAROL, AUDITORS)
                return AdjacencyBatch(edges=(*batch.edges, *other.edges))

        with pytest.raises(DomainValidationError):
            await shortest_path(ALICE, PAYROLL, Liar(payroll_estate()))


# --- Boundaries and limits ----------------------------------------------------------------


class TestBoundaries:
    async def test_a_target_granted_to_a_logon_time_sid_is_a_boundary(self) -> None:
        graph = Graph()
        graph.control(T.GENERIC_WRITE, well_known("S-1-5-11"), PAYROLL)

        found = await shortest_path(CAROL, PAYROLL, MemoryAdjacency(graph))

        assert found.paths == ()
        assert found.reasons == (CompletenessReason.IMPLICIT_MEMBERSHIP_BOUNDARY,)
        assert found.exploration.implicit_nodes == {well_known("S-1-5-11")}

    async def test_a_foreign_principal_upstream_of_the_target_is_a_boundary(self) -> None:
        foreign = NodeRef.principal(f"{FOREIGN_DOMAIN}-1500")
        graph = Graph()
        graph.member(
            foreign,
            RW,
            derivation_class=DerivationClass.UNRESOLVED,
            qualifiers=(RelationshipQualifier.FOREIGN_PRINCIPAL,),
        )
        graph.control(T.GENERIC_WRITE, RW, PAYROLL)

        found = await shortest_path(CAROL, PAYROLL, MemoryAdjacency(graph))

        assert found.paths == ()
        assert CompletenessReason.UNRESOLVED_BOUNDARY in found.reasons
        assert found.exploration.unresolved_nodes == {foreign}

    async def test_max_depth_stops_a_long_chain_and_says_so(self) -> None:
        graph = Graph()
        chain = [ALICE, *(group(2000 + index) for index in range(6))]
        for member, of in itertools.pairwise(chain):
            graph.member(member, of)

        cut = await shortest_path(
            ALICE, chain[-1], MemoryAdjacency(graph), limits=SearchLimits(max_depth=4)
        )
        whole = await shortest_path(
            ALICE, chain[-1], MemoryAdjacency(graph), limits=SearchLimits(max_depth=6)
        )

        assert cut.paths == ()
        assert CompletenessReason.MAX_DEPTH in cut.reasons
        assert whole.paths[0].hops == 6
        assert whole.reasons == ()

    async def test_a_frontier_of_leaves_at_the_depth_limit_is_not_truncation(self) -> None:
        graph = Graph()
        graph.member(ALICE, TEAM)
        graph.member(BOB, RW)

        found = await shortest_path(
            ALICE, RW, MemoryAdjacency(graph), limits=SearchLimits(max_depth=1)
        )

        assert found.paths == ()
        assert found.reasons == ()

    async def test_max_nodes_and_max_edges_are_reported(self) -> None:
        graph = Graph()
        for index in range(50):
            graph.member(user(3000 + index), RW)
        graph.control(T.GENERIC_WRITE, RW, PAYROLL)

        nodes = await shortest_path(
            CAROL, PAYROLL, MemoryAdjacency(graph), limits=SearchLimits(max_nodes=10)
        )
        edges = await shortest_path(
            CAROL, PAYROLL, MemoryAdjacency(graph), limits=SearchLimits(max_edges=10)
        )

        assert CompletenessReason.MAX_NODES in nodes.reasons
        assert CompletenessReason.MAX_EDGES in edges.reasons

    def test_a_limit_out_of_range_is_refused(self) -> None:
        for bad in ({"max_depth": 0}, {"max_depth": 33}, {"max_paths": 101}, {"max_nodes": 0}):
            with pytest.raises(DomainValidationError):
                SearchLimits(**bad)

    async def test_one_read_per_level_however_wide(self) -> None:
        """A 200-member group costs the same reads as a 2-member one: no N+1."""
        small, large = Graph(), Graph()
        for graph, count in ((small, 2), (large, 200)):
            for index in range(count):
                graph.member(user(4000 + index), RW)
            graph.control(T.GENERIC_WRITE, RW, PAYROLL)
        small_reads, large_reads = MemoryAdjacency(small), MemoryAdjacency(large)

        await reverse_reachability(PAYROLL, small_reads)
        await reverse_reachability(PAYROLL, large_reads)

        assert len(small_reads.calls) == len(large_reads.calls)


# --- Alternate paths ----------------------------------------------------------------------


def two_routes() -> Graph:
    graph = Graph()
    low, high = group(1300), group(1400)
    graph.member(ALICE, high)
    graph.member(high, RW)
    graph.member(ALICE, low)
    graph.member(low, RW)
    graph.control(T.GENERIC_WRITE, RW, PAYROLL)
    graph.member(ALICE, TEAM)
    graph.control(T.GENERIC_WRITE, TEAM, PAYROLL)
    return graph


class TestAlternatePaths:
    async def test_every_simple_route_shortest_first_then_in_node_order(self) -> None:
        found = await alternate_paths(ALICE, PAYROLL, MemoryAdjacency(two_routes()))

        assert [path.hops for path in found.paths] == [2, 3, 3]
        assert [path.nodes[1] for path in found.paths] == [TEAM, group(1300), group(1400)]
        assert found.reasons == ()

    @pytest.mark.parametrize("seed", range(6))
    async def test_the_order_never_depends_on_row_order(self, seed: int) -> None:
        reference = await alternate_paths(ALICE, PAYROLL, MemoryAdjacency(two_routes()))
        shuffled = await alternate_paths(
            ALICE, PAYROLL, MemoryAdjacency(two_routes(), shuffle_seed=seed)
        )

        assert [path.identity for path in shuffled.paths] == [
            path.identity for path in reference.paths
        ]

    async def test_no_route_repeats_a_node_and_no_route_repeats(self) -> None:
        graph = two_routes()
        a, b = group(1501), group(1502)
        graph.member(RW, a)
        graph.member(a, b)
        graph.member(b, RW)  # a cycle beside the target's trustee
        found = await alternate_paths(ALICE, PAYROLL, MemoryAdjacency(graph))

        identities = [path.identity for path in found.paths]
        assert len(identities) == len(set(identities))
        for path in found.paths:
            assert len(path.nodes) == len(set(path.nodes))

    async def test_the_path_cap_is_honored_and_reported(self) -> None:
        found = await alternate_paths(
            ALICE, PAYROLL, MemoryAdjacency(two_routes()), limits=SearchLimits(max_paths=2)
        )

        assert len(found.paths) == 2
        assert CompletenessReason.MAX_PATHS in found.reasons
        assert [path.hops for path in found.paths] == [2, 3]

    async def test_max_depth_bounds_alternates(self) -> None:
        found = await alternate_paths(
            ALICE, PAYROLL, MemoryAdjacency(two_routes()), limits=SearchLimits(max_depth=2)
        )

        assert [path.hops for path in found.paths] == [2]
        assert CompletenessReason.MAX_DEPTH in found.reasons

    async def test_a_confirmed_and_a_conditional_route_are_both_returned_and_told_apart(
        self,
    ) -> None:
        graph = Graph()
        graph.member(ALICE, RW)
        graph.control(T.GENERIC_WRITE, RW, PAYROLL)
        graph.member(ALICE, TEAM)
        graph.control(
            T.GENERIC_WRITE,
            TEAM,
            PAYROLL,
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=(RelationshipCondition.DENY_MAY_APPLY,),
        )

        everything = await alternate_paths(ALICE, PAYROLL, MemoryAdjacency(graph))
        confirmed = await alternate_paths(
            ALICE, PAYROLL, MemoryAdjacency(graph), policy=TraversalPolicy.CONFIRMED_ONLY
        )

        # Ordered by length, then node order (TEAM sorts before RW) -- never by confidence.
        assert [(path.nodes[1], path.assessment.conditional) for path in everything.paths] == [
            (TEAM, True),
            (RW, False),
        ]
        assert len(confirmed.paths) == 1
        assert not confirmed.paths[0].assessment.conditional
        assert CompletenessReason.EXCLUDED_BY_POLICY in confirmed.reasons

    async def test_the_work_budget_bounds_an_exponential_graph(self) -> None:
        """Layers of fully connected groups have exponentially many simple paths."""
        graph = Graph()
        layers = [[group(5000 + layer * 10 + index) for index in range(6)] for layer in range(8)]
        for member in layers[0]:
            graph.member(ALICE, member)
        for upper, lower in itertools.pairwise(layers):
            for member in upper:
                for of in lower:
                    graph.member(member, of)
        for member in layers[-1]:
            graph.control(T.GENERIC_WRITE, member, PAYROLL)

        found = await alternate_paths(
            ALICE,
            PAYROLL,
            MemoryAdjacency(graph),
            limits=SearchLimits(max_paths=100, max_depth=12),
        )

        assert len(found.paths) == 100
        assert CompletenessReason.MAX_PATHS in found.reasons
        assert ALTERNATE_WORK_BUDGET > 0


# --- Reverse reachability -----------------------------------------------------------------


class TestReverseReachability:
    async def test_every_source_nearest_first_with_its_route_in_privilege_order(self) -> None:
        found = await reverse_reachability(PAYROLL, MemoryAdjacency(payroll_estate()))

        assert [(item.node, item.hops) for item in found.sources] == [
            (FS01_ADMINS, 1),
            (RW, 2),
            (TEAM, 3),
            (ALICE, 4),
        ]
        route = found.path_from(ALICE)
        assert route.nodes[0] == ALICE
        assert route.nodes[-1] == PAYROLL
        assert found.reasons == ()

    async def test_directions_are_never_reversed(self) -> None:
        """Walking backwards changes where the search starts, never what an edge means."""
        found = await reverse_reachability(RW, MemoryAdjacency(payroll_estate()))

        assert [item.node for item in found.sources] == [TEAM, ALICE]
        for item in found.sources:
            for step in found.path_from(item.node).steps:
                assert step.basis.source == step.source

    async def test_cycles_terminate_and_each_source_appears_once(self) -> None:
        graph = Graph()
        a, b = group(1501), group(1502)
        graph.member(a, b)
        graph.member(b, a)
        graph.member(ALICE, a)
        graph.control(T.GENERIC_WRITE, b, PAYROLL)

        found = await reverse_reachability(PAYROLL, MemoryAdjacency(graph))

        assert [item.node for item in found.sources] == [b, a, ALICE]

    async def test_max_depth_and_max_nodes_cut_the_listing_and_say_so(self) -> None:
        depth = await reverse_reachability(
            PAYROLL, MemoryAdjacency(payroll_estate()), limits=SearchLimits(max_depth=2)
        )
        nodes = await reverse_reachability(
            PAYROLL, MemoryAdjacency(payroll_estate()), limits=SearchLimits(max_nodes=2)
        )

        assert [item.node for item in depth.sources] == [FS01_ADMINS, RW]
        assert CompletenessReason.MAX_DEPTH in depth.reasons
        assert len(nodes.sources) == 2
        assert CompletenessReason.MAX_NODES in nodes.reasons

    async def test_logon_time_sids_and_foreign_principals_are_flagged_boundaries(self) -> None:
        foreign = NodeRef.principal(f"{FOREIGN_DOMAIN}-1500")
        graph = Graph()
        graph.control(T.GENERIC_WRITE, well_known("S-1-1-0"), PAYROLL)
        graph.member(
            foreign,
            RW,
            derivation_class=DerivationClass.UNRESOLVED,
            qualifiers=(RelationshipQualifier.FOREIGN_PRINCIPAL,),
        )
        graph.control(T.GENERIC_WRITE, RW, PAYROLL)

        found = await reverse_reachability(PAYROLL, MemoryAdjacency(graph))

        flagged = {
            item.node: (item.unresolved_boundary, item.implicit_membership)
            for item in found.sources
        }
        assert flagged[well_known("S-1-1-0")] == (False, True)
        assert flagged[foreign] == (True, False)
        assert {
            CompletenessReason.UNRESOLVED_BOUNDARY,
            CompletenessReason.IMPLICIT_MEMBERSHIP_BOUNDARY,
        } <= set(found.reasons)

    async def test_conditional_sources_keep_their_condition(self) -> None:
        graph = Graph()
        graph.member(ALICE, RW)
        graph.control(
            T.GENERIC_ALL,
            RW,
            PAYROLL,
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=(RelationshipCondition.DENY_MAY_APPLY,),
        )

        found = await reverse_reachability(PAYROLL, MemoryAdjacency(graph))

        assert all(found.path_from(item.node).assessment.conditional for item in found.sources)

    @pytest.mark.parametrize("seed", range(4))
    async def test_the_order_never_depends_on_row_order(self, seed: int) -> None:
        reference = await reverse_reachability(PAYROLL, MemoryAdjacency(two_routes()))
        shuffled = await reverse_reachability(
            PAYROLL, MemoryAdjacency(two_routes(), shuffle_seed=seed)
        )

        assert shuffled.sources == reference.sources
        for item in reference.sources:
            assert shuffled.path_from(item.node).identity == reference.path_from(item.node).identity


# --- Neighbors ----------------------------------------------------------------------------


class TestNeighbors:
    async def test_outbound_and_inbound(self) -> None:
        graph = payroll_estate()

        outbound = await neighbors(
            RW, MemoryAdjacency(graph), directions=(AdjacencyDirection.OUTBOUND,)
        )
        inbound = await neighbors(
            RW, MemoryAdjacency(graph), directions=(AdjacencyDirection.INBOUND,)
        )

        assert {item.edge.relationship_type for item in outbound.relationships} == {
            T.LOCAL_GROUP_MEMBER_OF,
            T.LOCAL_ADMIN_OF,
        }
        assert [item.edge.source for item in inbound.relationships] == [TEAM]

    async def test_informational_relationships_are_shown(self) -> None:
        foreign = NodeRef.principal(f"{FOREIGN_DOMAIN}-1500")
        graph = Graph()
        graph.foreign(foreign, domain())

        found = await neighbors(foreign, MemoryAdjacency(graph))

        assert [item.edge.relationship_type for item in found.relationships] == [
            T.FOREIGN_PRINCIPAL_OF
        ]
        assert found.nodes[domain()] == 1

    async def test_a_type_filter_and_a_depth(self) -> None:
        graph = payroll_estate()

        members = await neighbors(
            RW,
            MemoryAdjacency(graph),
            directions=(AdjacencyDirection.INBOUND,),
            types=frozenset({T.MEMBER_OF}),
            depth=2,
        )

        assert [(item.edge.source, item.depth) for item in members.relationships] == [
            (TEAM, 1),
            (ALICE, 2),
        ]

    async def test_current_only_and_the_policy(self) -> None:
        graph = payroll_estate()
        graph.set_currency(graph.edges[1], RelationshipCurrency.NOT_CURRENT)  # team → rw
        graph.control(
            T.GENERIC_WRITE,
            RW,
            PAYROLL,
            derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
            conditions=(RelationshipCondition.DENY_MAY_APPLY,),
        )

        everything = await neighbors(RW, MemoryAdjacency(graph))
        confirmed = await neighbors(
            RW, MemoryAdjacency(graph), policy=TraversalPolicy.CONFIRMED_ONLY
        )

        listed = {item.edge.relationship_type for item in everything.relationships}
        assert T.MEMBER_OF not in listed  # the not-current membership is history
        assert T.GENERIC_WRITE in listed
        assert T.GENERIC_WRITE not in {
            item.edge.relationship_type for item in confirmed.relationships
        }
        assert CompletenessReason.EXCLUDED_BY_POLICY in confirmed.reasons

    async def test_host_scope_is_kept(self) -> None:
        graph = Graph()
        graph.local_member(RW, FS01_ADMINS)
        graph.local_member(RW, FS02_ADMINS)

        found = await neighbors(FS01_ADMINS, MemoryAdjacency(graph))

        assert {item.edge.scope.key for item in found.relationships if item.edge.scope} == {"fs01"}

    async def test_what_lies_beyond_the_depth_is_a_hint_not_a_truncation(self) -> None:
        found = await neighbors(
            RW, MemoryAdjacency(payroll_estate()), directions=(AdjacencyDirection.INBOUND,)
        )

        assert [item.edge.source for item in found.relationships] == [TEAM]
        assert found.frontier == 1  # Finance-Team's own members were not listed
        assert found.reasons == ()

    async def test_bounds(self) -> None:
        graph = Graph()
        for index in range(30):
            graph.member(user(6000 + index), RW)

        capped = await neighbors(RW, MemoryAdjacency(graph), limits=SearchLimits(max_nodes=5))

        assert len(capped.relationships) == 5
        assert CompletenessReason.MAX_NODES in capped.reasons
        with pytest.raises(DomainValidationError):
            await neighbors(RW, MemoryAdjacency(graph), depth=4)
        with pytest.raises(DomainValidationError):
            await neighbors(RW, MemoryAdjacency(graph), types=frozenset({T.TRUSTS}))

    @pytest.mark.parametrize("seed", range(4))
    async def test_the_order_never_depends_on_row_order(self, seed: int) -> None:
        reference = await neighbors(RW, MemoryAdjacency(payroll_estate()), depth=2)
        shuffled = await neighbors(
            RW, MemoryAdjacency(payroll_estate(), shuffle_seed=seed), depth=2
        )

        assert shuffled.relationships == reference.relationships


def test_the_fixture_keys_are_canonical() -> None:
    assert ALICE.key.startswith(DOMAIN)
    assert PAYROLL.kind is NodeKind.NTFS_RESOURCE
