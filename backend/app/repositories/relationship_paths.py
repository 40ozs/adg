"""Batched adjacency reads for the persisted privilege relationship graph.

The Phase 01A repository exposes one-node keyset pages. Path traversal needs a level at a
time; issuing that page query once per frontier node would be an N+1 graph walk. This narrow
adapter selects relationship ids for a bounded *set* of canonical nodes, then reuses
RelationshipRepository.get_many so currency and evidence are still computed in the one
authoritative place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.relationship_paths import AdjacencyBatch, relationship_sort_key
from app.domain.relationships import AdjacencyDirection, NodeRef, RelationshipType
from app.models.schema import graph_relationships
from app.repositories.relationships import RelationshipRepository

NODE_CHUNK: Final = 5000
"""Canonical nodes per adjacency-id query; keeps tuple parameters bounded on PostgreSQL."""


class RelationshipPathRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._relationships = RelationshipRepository(session)

    async def adjacent_many(
        self,
        nodes: Sequence[NodeRef],
        direction: AdjacencyDirection,
        *,
        types: frozenset[RelationshipType] | None,
        limit: int,
    ) -> AdjacencyBatch:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        ordered_nodes = sorted(set(nodes), key=lambda node: (node.kind.value, node.key))
        if not ordered_nodes:
            return AdjacencyBatch(())

        table = graph_relationships
        kind_column, key_column = (
            (table.c.source_kind, table.c.source_key)
            if direction is AdjacencyDirection.OUTBOUND
            else (table.c.target_kind, table.c.target_key)
        )
        type_values = None if types is None else sorted(item.value for item in types)

        ids: list[str] = []
        seen: set[str] = set()
        truncated = False
        for start in range(0, len(ordered_nodes), NODE_CHUNK):
            remaining = limit - len(ids)
            if remaining <= 0:
                truncated = True
                break
            chunk = ordered_nodes[start : start + NODE_CHUNK]
            endpoint_values = [(node.kind.value, node.key) for node in chunk]
            statement = (
                sa.select(table.c.relationship_id)
                .where(sa.tuple_(kind_column, key_column).in_(endpoint_values))
                .order_by(
                    table.c.relationship_type,
                    table.c.source_kind,
                    table.c.source_key,
                    table.c.target_kind,
                    table.c.target_key,
                    table.c.scope_kind,
                    table.c.scope_key,
                    table.c.relationship_id,
                )
                .limit(remaining + 1)
            )
            if type_values is not None:
                statement = statement.where(table.c.relationship_type.in_(type_values))
            rows = tuple((await self.session.execute(statement)).scalars())
            if len(rows) > remaining:
                rows = rows[:remaining]
                truncated = True
            for relationship_id in rows:
                if relationship_id not in seen:
                    seen.add(relationship_id)
                    ids.append(relationship_id)
            if truncated:
                break

        loaded = await self._relationships.get_many(ids)
        items = tuple(
            sorted(
                (loaded[relationship_id] for relationship_id in ids if relationship_id in loaded),
                key=relationship_sort_key,
            )
        )
        return AdjacencyBatch(items, truncated=truncated)
