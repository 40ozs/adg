"""In-memory membership graphs for testing traversal.

:class:`app.domain.graph.AdjacencyProvider` is a protocol precisely so that the traversal
can be exercised without a database. These helpers build the graphs the tests need: the
canonical Phase 0 fixtures replayed as edges, and generated deep/wide/cyclic shapes that no
hand-written fixture would contain.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from app.contracts.v1 import MembershipObservation
from app.domain import Direction, GraphEdge, MembershipEdgeKind, PrincipalKind


class InMemoryAdjacency:
    """An adjacency provider backed by a list of edges.

    ``calls`` counts provider round trips, which is how the tests assert that a traversal
    costs one call per breadth-first level rather than one per node.
    """

    def __init__(self, edges: Iterable[GraphEdge]) -> None:
        self._down: dict[str, list[GraphEdge]] = defaultdict(list)
        self._up: dict[str, list[GraphEdge]] = defaultdict(list)
        self.edges: list[GraphEdge] = []
        for edge in edges:
            self.edges.append(edge)
            self._down[edge.group_key].append(edge)
            self._up[edge.member_key].append(edge)
        self.calls = 0
        self.keys_requested = 0

    async def neighbors(
        self, direction: Direction, keys: Sequence[str]
    ) -> Mapping[str, Sequence[GraphEdge]]:
        self.calls += 1
        self.keys_requested += len(keys)
        table = self._down if direction is Direction.DOWN else self._up
        return {key: tuple(table.get(key, ())) for key in keys}


def edge(
    group: str,
    member: str,
    kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
    host_key: str | None = None,
    member_kind: PrincipalKind | None = None,
    is_foreign_security_principal: bool = False,
) -> GraphEdge:
    """Build an edge with the identity key the database would store."""
    return GraphEdge(
        edge_key=f"{group}->{member}|{kind.value}",
        group_key=group,
        member_key=member,
        kind=kind,
        host_key=host_key,
        member_kind=member_kind,
        is_foreign_security_principal=is_foreign_security_principal,
    )


def edges_from_observations(observations: Iterable[MembershipObservation]) -> list[GraphEdge]:
    """Turn contract membership observations into traversal edges.

    Goes through :class:`app.domain.MembershipEdge` so the keys are the same ones ingestion
    writes; a test that built keys by hand could pass while the real join failed.
    """
    result: list[GraphEdge] = []
    for observation in observations:
        domain_edge = observation.to_domain()
        result.append(
            GraphEdge(
                edge_key=domain_edge.identity_key,
                group_key=domain_edge.group_key,
                member_key=domain_edge.member_key,
                kind=domain_edge.kind,
                host_key=domain_edge.host_key,
                member_kind=domain_edge.member_kind,
                is_foreign_security_principal=domain_edge.is_foreign_security_principal,
            )
        )
    return result


def chain(depth: int, prefix: str = "n") -> list[GraphEdge]:
    """A single strand ``n0 ∈ n1 ∈ ... ∈ n<depth>``: the deepest nesting possible."""
    return [edge(f"{prefix}{level + 1}", f"{prefix}{level}") for level in range(depth)]


def fan_out(width: int, group: str = "g0", prefix: str = "u") -> list[GraphEdge]:
    """One group with ``width`` direct members: the widest shape possible."""
    return [edge(group, f"{prefix}{index}") for index in range(width)]


def ring(size: int, prefix: str = "r") -> list[GraphEdge]:
    """A membership cycle of ``size`` groups, each contained in the next."""
    return [edge(f"{prefix}{(index + 1) % size}", f"{prefix}{index}") for index in range(size)]


def diamond() -> list[GraphEdge]:
    """``leaf`` reaches ``top`` by two distinct routes."""
    return [
        edge("left", "leaf"),
        edge("right", "leaf"),
        edge("top", "left"),
        edge("top", "right"),
    ]
