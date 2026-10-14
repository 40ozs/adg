"""Where a stored fact came from: the run that first reported it, the run that last did.

Every row ADG holds about a membership, an ACE or a descriptor carries its own provenance —
``source_key`` and the first and last observation, each as an instant and a run — and every
run carries the collector that produced it. None of that is needed to *compute* an access
answer, so the access repositories do not read it. It is exactly what an investigator needs
to *trust* one: "this membership was last seen by the directory collector on 1 October" is
the difference between a fact and an assertion.

This repository reads it, by key, for the rows an explanation already names, in **one
statement**: a ``UNION ALL`` over the four current-state tables, each branch an indexed
lookup by primary key, joined to the run and collector that last observed the row. Its size
is bounded by the explanation's own limits, never by the estate.

It is deliberately separate from :class:`~app.repositories.ResourceRepository` and
:class:`~app.repositories.MembershipRepository`: those have point-in-time and simulation
counterparts that must answer every question the live ones answer, and provenance is a
question only the live explanation asks.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import Text, any_, bindparam, literal, select, union_all
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.current import (
    current_membership_edges,
    current_ntfs_aces,
    current_ntfs_resources,
    current_smb_share_aces,
)
from app.models.schema import collector_sources, scan_runs

__all__ = ["ObservedRecord", "ProvenanceRepository", "RecordKind"]

RecordKind = Literal["membership_edge", "ntfs_ace", "smb_share_ace", "ntfs_resource"]


@dataclass(frozen=True, slots=True)
class ObservedRecord:
    """The provenance columns of one stored row, with the run that last observed it."""

    record: RecordKind
    key: str
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID
    collector: str | None
    """The collector of the last observing run. ``None`` only if the run row is missing,
    which the foreign keys make impossible and which is reported rather than assumed."""

    collection_method: str | None
    last_run_status: str | None


class ProvenanceRepository:
    """Provenance of stored rows, by key, in current state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def observed(
        self,
        *,
        membership_edges: Sequence[str] = (),
        ntfs_aces: Sequence[str] = (),
        share_aces: Sequence[str] = (),
        ntfs_resources: Sequence[str] = (),
    ) -> dict[tuple[RecordKind, str], ObservedRecord]:
        """Every named row that is currently present, keyed by ``(record kind, key)``.

        A key with no current row is simply absent from the result: the caller decides what
        an edge without a row means, because only it knows why the edge exists.
        """
        branches = [
            _branch("membership_edge", current_membership_edges, "edge_key", membership_edges),
            _branch("ntfs_ace", current_ntfs_aces, "ace_key", ntfs_aces),
            _branch("smb_share_ace", current_smb_share_aces, "ace_key", share_aces),
            _branch("ntfs_resource", current_ntfs_resources, "resource_key", ntfs_resources),
        ]
        present = [branch for branch in branches if branch is not None]
        if not present:
            return {}
        rows = union_all(*present).subquery("observed")
        statement = (
            select(
                rows,
                collector_sources.c.collector,
                collector_sources.c.method,
                scan_runs.c.status,
            )
            .select_from(rows)
            .outerjoin(scan_runs, scan_runs.c.run_id == rows.c.last_observed_run_id)
            .outerjoin(collector_sources, collector_sources.c.id == scan_runs.c.source_id)
        )
        found: dict[tuple[RecordKind, str], ObservedRecord] = {}
        for row in (await self._session.execute(statement)).mappings():
            record = cast(RecordKind, row["record"])
            found[(record, row["key"])] = ObservedRecord(
                record=record,
                key=row["key"],
                source_key=row["source_key"],
                first_observed_at=row["first_observed_at"],
                first_observed_run_id=row["first_observed_run_id"],
                last_observed_at=row["last_observed_at"],
                last_observed_run_id=row["last_observed_run_id"],
                collector=row["collector"],
                collection_method=row["method"],
                last_run_status=row["status"],
            )
        return found


def _branch(record: RecordKind, table: Any, key: str, keys: Sequence[str]) -> Any:
    """One ``UNION ALL`` branch: the provenance columns of the named rows of one table."""
    if not keys:
        return None
    column = table.c[key]
    named = bindparam(f"{record}_keys", list(dict.fromkeys(keys)), type_=ARRAY(Text))
    return select(
        literal(record, Text).label("record"),
        column.label("key"),
        table.c.source_key,
        table.c.first_observed_at,
        table.c.first_observed_run_id,
        table.c.last_observed_at,
        table.c.last_observed_run_id,
    ).where(column == any_(named))
