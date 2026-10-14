"""The traversal over PostgreSQL: one consistent snapshot, batched reads, labels on request.

Three things happen here and nowhere else:

* **A snapshot.** A path search reads a level at a time, and a scan run completing between
  two levels would let one answer mix two graphs. :meth:`RelationshipGraphService.open` makes
  the request's transaction ``REPEATABLE READ, READ ONLY`` before its first statement, so the
  collection basis, the projection state and every level of every walk are read from one
  snapshot, and the basis an answer reports is the state it was computed from. Nothing here
  writes, and the read-only mode makes that a property of the transaction rather than a
  promise of the code.
* **Readiness**, from the projector's own pass log (``RelationshipProjector.state``) and the
  collection basis — never a second readiness record.
* **The adjacency provider** the pure search walks: one read of the adjacency index per level
  plus :meth:`RelationshipRepository.get_many`'s three, whatever the level holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain.basis import CollectionBasis
from app.domain.relationships import (
    AdjacencyDirection,
    NodeKind,
    NodeRef,
    RelationshipType,
    StoredRelationship,
)
from app.models.current import CURRENT_STATE
from app.models.schema import principals
from app.relationships.projector import RelationshipProjector
from app.repositories.basis import CollectionBasisRepository
from app.repositories.membership import MembershipRepository, PrincipalRecord
from app.repositories.relationships import RelationshipRepository
from app.repositories.resources import NtfsResourceRecord, ResourceRepository
from app.traversal.completeness import GraphReadiness
from app.traversal.model import AdjacencyBatch, TraversalEdge
from app.traversal.search import (
    DEFAULT_LIMITS,
    Neighborhood,
    PathSearch,
    Reachability,
    SearchLimits,
    alternate_paths,
    neighbors,
    reverse_reachability,
    shortest_path,
)
from app.traversal.semantics import STORED_TYPES, TraversalPolicy

__all__ = [
    "SNAPSHOT_ISOLATION",
    "NodeLabels",
    "RelationshipGraphService",
    "RepositoryAdjacency",
    "Snapshot",
]

SNAPSHOT_ISOLATION: Final = "repeatable_read"

_CANONICAL_KIND: Final[dict[NodeKind, ObservationKind]] = {
    NodeKind.PRINCIPAL: ObservationKind.PRINCIPAL,
    NodeKind.SERVER: ObservationKind.SERVER,
    NodeKind.SMB_SHARE: ObservationKind.SMB_SHARE,
    NodeKind.NTFS_RESOURCE: ObservationKind.NTFS_RESOURCE,
}


class RepositoryAdjacency:
    """:class:`~app.traversal.model.RelationshipAdjacency` over the relationship store.

    Keeps every relationship it loaded, so the edges a returned path names can be explained
    from what the search already read rather than read again.
    """

    def __init__(self, repository: RelationshipRepository) -> None:
        self._repository = repository
        self.loaded: dict[str, StoredRelationship] = {}

    async def adjacent(
        self,
        nodes: Sequence[NodeRef],
        direction: AdjacencyDirection,
        types: frozenset[RelationshipType],
        *,
        limit: int,
    ) -> AdjacencyBatch:
        relationships, truncated = await self._repository.adjacent_many(
            nodes, direction, types=types, limit=limit
        )
        for relationship in relationships:
            self.loaded[relationship.relationship_id] = relationship
        return AdjacencyBatch(
            edges=tuple(TraversalEdge.of(relationship) for relationship in relationships),
            truncated=truncated,
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The state an answer was computed from: collected facts, and their derivation."""

    isolation: str
    basis: CollectionBasis
    readiness: GraphReadiness


@dataclass(frozen=True, slots=True)
class NodeLabels:
    """What is stored about the nodes an answer names, read in one batch per kind."""

    principals: dict[str, PrincipalRecord]
    resources: dict[str, NtfsResourceRecord]
    present: frozenset[NodeRef]
    """Nodes whose canonical object has a current row. A node only a relationship names (an
    undescribed member, a domain) is a real node without one."""


class RelationshipGraphService:
    """Answers graph questions over one session. Writes nothing."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._relationships = RelationshipRepository(session)
        self.adjacency = RepositoryAdjacency(self._relationships)

    async def open(self) -> Snapshot:
        """Pin the request to one snapshot, then read what the answer will be "as of".

        Must be the first thing the session does: PostgreSQL fixes a transaction's isolation
        before its first query. A session that is already inside a transaction (a caller
        composing this with other reads) keeps that transaction's own isolation, and the
        answer says so.
        """
        isolation = "caller_transaction"
        if not self._session.in_transaction():
            await self._session.execute(
                sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            isolation = SNAPSHOT_ISOLATION
        basis = await CollectionBasisRepository(self._session).current()
        state = await RelationshipProjector(self._session).state()
        readiness = GraphReadiness(
            nothing_collected=basis.is_empty,
            passes=state.passes,
            latest_pass=state.latest_pass,
            underived_runs=state.underived_runs,
            failed_runs=state.failed_runs,
            runs_in_progress=state.runs_in_progress,
        )
        return Snapshot(isolation=isolation, basis=basis, readiness=readiness)

    async def unknown(self, nodes: Iterable[NodeRef]) -> tuple[NodeRef, ...]:
        """The nodes nothing is stored about: no current canonical row, no relationship."""
        wanted = sorted(set(nodes))
        named = await self._relationships.nodes_named(wanted)
        present = await self._present(node for node in wanted if node not in named)
        return tuple(node for node in wanted if node not in named and node not in present)

    async def host_scoped_candidates(self, node: NodeRef) -> tuple[NodeRef, ...]:
        """For a bare BUILTIN SID nothing is stored under: the host-local groups that carry it.

        Offered, never chosen. A BUILTIN SID names a group only together with its host, and
        picking one would merge one server's local administrators into another's.
        """
        sid = node.sid
        if node.kind is not NodeKind.PRINCIPAL or node.host is not None or sid is None:
            return ()
        if not sid.is_builtin:
            return ()
        rows = await self._session.execute(
            sa.select(principals.c.principal_key)
            .where(principals.c.sid == sid.value, principals.c.host_key.is_not(None))
            .order_by(principals.c.principal_key)
            .limit(50)
        )
        return tuple(NodeRef.principal(key) for key in rows.scalars())

    async def labels(self, nodes: Iterable[NodeRef]) -> NodeLabels:
        wanted = set(nodes)
        principal_keys = sorted(node.key for node in wanted if node.kind is NodeKind.PRINCIPAL)
        resource_keys = sorted(node.key for node in wanted if node.kind is NodeKind.NTFS_RESOURCE)
        found_principals = (
            await MembershipRepository(self._session).principals_by_keys(principal_keys)
            if principal_keys
            else {}
        )
        found_resources = (
            await ResourceRepository(self._session).ntfs_resources_by_keys(resource_keys)
            if resource_keys
            else {}
        )
        present = {NodeRef.principal(key) for key in found_principals} | {
            NodeRef(NodeKind.NTFS_RESOURCE, key) for key in found_resources
        }
        others = [
            node for node in wanted if node.kind not in (NodeKind.PRINCIPAL, NodeKind.NTFS_RESOURCE)
        ]
        present |= await self._present(others)
        return NodeLabels(
            principals=dict(found_principals),
            resources=dict(found_resources),
            present=frozenset(present),
        )

    async def relationship(self, relationship_id: str) -> StoredRelationship | None:
        return await self._relationships.get(relationship_id)

    def loaded(self, relationship_id: str) -> StoredRelationship | None:
        """A relationship the searches of this request already read."""
        return self.adjacency.loaded.get(relationship_id)

    async def shortest_path(
        self,
        source: NodeRef,
        target: NodeRef,
        *,
        policy: TraversalPolicy,
        limits: SearchLimits = DEFAULT_LIMITS,
    ) -> PathSearch:
        return await shortest_path(source, target, self.adjacency, policy=policy, limits=limits)

    async def alternate_paths(
        self,
        source: NodeRef,
        target: NodeRef,
        *,
        policy: TraversalPolicy,
        limits: SearchLimits = DEFAULT_LIMITS,
    ) -> PathSearch:
        return await alternate_paths(source, target, self.adjacency, policy=policy, limits=limits)

    async def reverse_reachability(
        self,
        target: NodeRef,
        *,
        policy: TraversalPolicy,
        limits: SearchLimits = DEFAULT_LIMITS,
    ) -> Reachability:
        return await reverse_reachability(target, self.adjacency, policy=policy, limits=limits)

    async def neighbors(
        self,
        node: NodeRef,
        *,
        directions: Sequence[AdjacencyDirection],
        types: frozenset[RelationshipType] = STORED_TYPES,
        depth: int = 1,
        policy: TraversalPolicy,
        limits: SearchLimits = DEFAULT_LIMITS,
    ) -> Neighborhood:
        return await neighbors(
            node,
            self.adjacency,
            directions=directions,
            types=types,
            depth=depth,
            policy=policy,
            limits=limits,
        )

    async def _present(self, nodes: Iterable[NodeRef]) -> frozenset[NodeRef]:
        """Which of these nodes have a current canonical row. One read per kind asked."""
        by_kind: dict[NodeKind, list[str]] = {}
        for node in nodes:
            if node.kind in _CANONICAL_KIND:
                by_kind.setdefault(node.kind, []).append(node.key)
        found: set[NodeRef] = set()
        for kind, keys in sorted(by_kind.items()):
            view = CURRENT_STATE[_CANONICAL_KIND[kind]]
            column = _key_column(view, kind)
            rows = await self._session.execute(
                sa.select(column).where(column.in_(sorted(set(keys))))
            )
            found.update(NodeRef(kind, key) for key in rows.scalars())
        return frozenset(found)


def _key_column(view: sa.Subquery, kind: NodeKind) -> sa.ColumnElement[str]:
    name = {
        NodeKind.PRINCIPAL: "principal_key",
        NodeKind.SERVER: "server_key",
        NodeKind.SMB_SHARE: "share_key",
        NodeKind.NTFS_RESOURCE: "resource_key",
    }[kind]
    column: sa.ColumnElement[str] = view.c[name]
    return column
