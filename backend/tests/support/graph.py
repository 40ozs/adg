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
from app.ingestion.plan import PrincipalRow, plan_batch
from app.repositories import PrincipalRecord
from tests.fixtures import Scenario, load_ad_graph


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
        self._rows_read = 0

    async def neighbors(
        self, direction: Direction, keys: Sequence[str]
    ) -> Mapping[str, Sequence[GraphEdge]]:
        self.calls += 1
        self.keys_requested += len(keys)
        table = self._down if direction is Direction.DOWN else self._up
        answer = {key: tuple(table.get(key, ())) for key in keys}
        self._rows_read += sum(len(found) for found in answer.values())
        return answer


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


class InMemoryMembershipRepository(InMemoryAdjacency):
    """Everything :class:`app.services.graph.GraphService` asks of a repository.

    The service does two things a bare adjacency provider cannot answer for: it labels each
    reached key with its stored principal, and it reports how many edge rows the request
    read. Supplying both in memory is what lets the `include=` filter, the unlabelled-member
    rule, and the traversal metadata be tested against the adversarial transcripts without a
    database — the database tests then check that PostgreSQL agrees.

    Principal records are built through :func:`app.ingestion.plan.plan_batch`, so the keys
    here are the keys ingestion would write. A helper that formatted them itself could pass
    while the real join failed.
    """

    def __init__(
        self, edges: Iterable[GraphEdge], principals: Mapping[str, PrincipalRecord] | None = None
    ) -> None:
        super().__init__(edges)
        self.principals: dict[str, PrincipalRecord] = dict(principals or {})

    async def principals_by_keys(self, keys: Sequence[str]) -> dict[str, PrincipalRecord]:
        return {key: self.principals[key] for key in keys if key in self.principals}

    @property
    def edges_fetched(self) -> int:
        """Rows a database would have read: every edge out of every key asked about."""
        return self._rows_read


def _record(row: PrincipalRow) -> PrincipalRecord:
    """A stored principal as the repository would return it, from a planned row."""
    moment = row.observed_at
    return PrincipalRecord(
        principal_key=row.principal_key,
        sid=row.sid,
        principal_kind=PrincipalKind(row.principal_kind),
        host_key=row.host_key,
        domain_sid=row.domain_sid,
        display_name=row.display_name,
        sam_account_name=row.sam_account_name,
        user_principal_name=row.user_principal_name,
        distinguished_name=row.distinguished_name,
        group_scope=row.group_scope,
        group_type=row.group_type,
        enabled=row.enabled,
        is_deleted=row.is_deleted,
        unresolved_reason=row.unresolved_reason,
        last_known_name=row.last_known_name,
        first_observed_at=moment,
        first_observed_run_id=row.run_id,
        last_observed_at=moment,
        last_observed_run_id=row.run_id,
    )


def repository_from_scenario(scenario: Scenario) -> InMemoryMembershipRepository:
    """Replay a transcript into the graph the ingestion phase would have stored.

    Newest-wins on principals, matching the upsert rule, so replaying a rename fixture after
    its "before" half behaves the way the database does.
    """
    edges: dict[str, GraphEdge] = {}
    records: dict[str, PrincipalRecord] = {}
    for batch in scenario.batches:
        plan = plan_batch(batch)
        for row in plan.principals:
            existing = records.get(row.principal_key)
            if existing is None or row.observed_at >= existing.last_observed_at:
                records[row.principal_key] = _record(row)
        for edge_row in plan.edges:
            edges[edge_row.edge_key] = GraphEdge(
                edge_key=edge_row.edge_key,
                group_key=edge_row.group_key,
                member_key=edge_row.member_key,
                kind=MembershipEdgeKind(edge_row.edge_kind),
                host_key=edge_row.host_key,
                member_kind=(PrincipalKind(edge_row.member_kind) if edge_row.member_kind else None),
                is_foreign_security_principal=edge_row.is_foreign_security_principal,
            )
    return InMemoryMembershipRepository(edges.values(), records)


def repository_from_fixture(name: str) -> InMemoryMembershipRepository:
    """The adversarial transcript ``name``, replayed into an in-memory repository."""
    return repository_from_scenario(load_ad_graph(name))
