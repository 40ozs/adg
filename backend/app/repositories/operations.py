"""Reading collector operations: five statements, whatever the estate holds.

The collector status page asks five things at once — the latest run of each scope, the last
success of each scope, the last failure of each scope, how many of every object are stored,
and what went wrong across all of them. Each is one statement, and none of them grows with
the number of scopes, servers or errors. That is the property
``tests/db/test_query_cost.py`` holds: an operator page that issued a query per server would
be slowest on exactly the estate that needs it most.

``DISTINCT ON`` does the per-scope reduction in one index-ordered pass. It is PostgreSQL
specific; so is the rest of this application, and the alternative — a correlated subquery
per scope — is the N+1 this module exists to avoid.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.observation import ScanStatus
from app.domain.operations import ErrorGroup, ObjectCounts, RunOutcome
from app.models.schema import (
    collector_sources,
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    principals,
    scan_run_errors,
    scan_run_scopes,
    scan_runs,
    servers,
    smb_share_aces,
    smb_shares,
)

__all__ = ["OperationsRepository"]

#: How many distinct error codes the summary reports, and how many example targets each
#: carries. Bounded because a run may report thousands of failures and an operator needs
#: the shape of them, not the list — the list is on the run itself.
MAX_ERROR_CODES = 20
MAX_SAMPLE_TARGETS = 3


def _scope_target() -> Any:
    """``target``, coalesced: SQL does not group NULLs, and a run without a target is one
    scope rather than one scope per run."""
    return func.coalesce(collector_sources.c.target, "")


def _scope_counts() -> Any:
    """Declared and reconciled scope counts per run, as a joinable subquery.

    Aggregated in its own subquery rather than joined into the run select: joining
    ``scan_run_scopes`` directly would multiply each run row by its scope count and make
    every other column wrong.
    """
    return (
        select(
            scan_run_scopes.c.run_id.label("run_id"),
            func.count().label("declared"),
            func.count().filter(scan_run_scopes.c.reconciled.is_(True)).label("reconciled"),
        )
        .group_by(scan_run_scopes.c.run_id)
        .subquery()
    )


class OperationsRepository:
    """Operator-facing reads over the run history and the stored estate."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _runs_select(self) -> Select[Any]:
        scopes = _scope_counts()
        joined = scan_runs.join(
            collector_sources, scan_runs.c.source_id == collector_sources.c.id
        ).outerjoin(scopes, scopes.c.run_id == scan_runs.c.run_id)
        return select(
            scan_runs.c.run_id,
            scan_runs.c.status,
            scan_runs.c.incremental,
            scan_runs.c.started_at,
            scan_runs.c.completed_at,
            scan_runs.c.error_count,
            scan_runs.c.batch_count_reported,
            scan_runs.c.batch_count_received,
            scan_runs.c.observation_count_reported,
            scan_runs.c.observation_count_applied,
            scan_runs.c.downgrade_reason,
            collector_sources.c.collector,
            collector_sources.c.collector_host,
            collector_sources.c.target,
            func.coalesce(scopes.c.declared, 0).label("declared_scopes"),
            func.coalesce(scopes.c.reconciled, 0).label("reconciled_scopes"),
        ).select_from(joined)

    async def _latest_per_scope(
        self, statuses: tuple[ScanStatus, ...] | None
    ) -> tuple[RunOutcome, ...]:
        target = _scope_target()
        statement = self._runs_select()
        if statuses is not None:
            statement = statement.where(
                scan_runs.c.status.in_([status.value for status in statuses])
            )
        statement = statement.distinct(collector_sources.c.collector, target).order_by(
            collector_sources.c.collector,
            target,
            scan_runs.c.started_at.desc(),
            scan_runs.c.run_id.desc(),
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(_outcome(row) for row in rows)

    async def latest_runs(self) -> tuple[RunOutcome, ...]:
        """The most recent run of each ``(collector, target)``, whatever its outcome."""
        return await self._latest_per_scope(None)

    async def latest_successes(self) -> tuple[RunOutcome, ...]:
        """The most recent run of each scope that finished with usable coverage.

        ``partial`` counts. A partial run collected real observations and its data is on
        screen; calling it "not a success" would tell an operator that a scope has never
        been read when in fact most of it has.
        """
        return await self._latest_per_scope((ScanStatus.SUCCEEDED, ScanStatus.PARTIAL))

    async def latest_failures(self) -> tuple[RunOutcome, ...]:
        """The most recent run of each scope that produced nothing usable."""
        return await self._latest_per_scope((ScanStatus.FAILED, ScanStatus.CANCELED))

    async def object_counts(self) -> ObjectCounts:
        """How many of each kind of object the store holds, in one statement.

        Scalar subqueries rather than one query per table: eight round trips to render one
        panel is the shape of an operator page that nobody keeps open.
        """

        def total(table: Any) -> Any:
            return select(func.count()).select_from(table).scalar_subquery()

        statement = select(
            total(principals).label("principals"),
            total(membership_edges).label("membership_edges"),
            total(servers).label("servers"),
            total(smb_shares).label("shares"),
            total(smb_share_aces).label("share_aces"),
            total(ntfs_resources).label("directories"),
            total(ntfs_aces).label("ntfs_aces"),
            total(scan_runs).label("scan_runs"),
        )
        row = (await self._session.execute(statement)).mappings().one()
        return ObjectCounts(
            principals=int(row["principals"]),
            membership_edges=int(row["membership_edges"]),
            servers=int(row["servers"]),
            shares=int(row["shares"]),
            share_aces=int(row["share_aces"]),
            directories=int(row["directories"]),
            ntfs_aces=int(row["ntfs_aces"]),
            scan_runs=int(row["scan_runs"]),
        )

    async def error_summary(self, limit: int = MAX_ERROR_CODES) -> tuple[ErrorGroup, ...]:
        """Every reported collector error, grouped by code, commonest first.

        The sample targets come from ``array_agg`` inside the same aggregate rather than a
        second query per code: the second query is the N+1, and it would be one per code on
        exactly the estate that has many codes.
        """
        joined = scan_run_errors.join(
            scan_runs, scan_run_errors.c.run_id == scan_runs.c.run_id
        ).join(collector_sources, scan_runs.c.source_id == collector_sources.c.id)
        statement = (
            select(
                scan_run_errors.c.code,
                func.count().label("count"),
                func.array_agg(collector_sources.c.collector.distinct()).label("collectors"),
                func.max(
                    func.coalesce(scan_run_errors.c.occurred_at, scan_runs.c.completed_at)
                ).label("latest_occurred_at"),
                func.array_agg(func.coalesce(scan_run_errors.c.target, "")).label("targets"),
            )
            .select_from(joined)
            .group_by(scan_run_errors.c.code)
            .order_by(func.count().desc(), scan_run_errors.c.code)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(
            ErrorGroup(
                code=str(row["code"]),
                count=int(row["count"]),
                collectors=tuple(sorted(str(item) for item in (row["collectors"] or []))),
                latest_occurred_at=_moment(row["latest_occurred_at"]),
                # Deduplicated in Python, not SQL: a distinct array_agg cannot be ordered
                # independently of the count aggregate in the same select, and the list is
                # already bounded by the group.
                sample_targets=tuple(
                    dict.fromkeys(item for item in (row["targets"] or []) if item)
                )[:MAX_SAMPLE_TARGETS],
            )
            for row in rows
        )


def _moment(value: Any) -> dt.datetime | None:
    return value if isinstance(value, dt.datetime) else None


def _outcome(row: Any) -> RunOutcome:
    return RunOutcome(
        run_id=str(row["run_id"]),
        collector=str(row["collector"]),
        collector_host=str(row["collector_host"]),
        target=row["target"],
        status=ScanStatus(row["status"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        error_count=int(row["error_count"]),
        batches_reported=(
            None if row["batch_count_reported"] is None else int(row["batch_count_reported"])
        ),
        batches_received=int(row["batch_count_received"]),
        observations_reported=(
            None
            if row["observation_count_reported"] is None
            else int(row["observation_count_reported"])
        ),
        observations_applied=int(row["observation_count_applied"]),
        declared_scopes=int(row["declared_scopes"]),
        reconciled_scopes=int(row["reconciled_scopes"]),
        incremental=bool(row["incremental"]),
        downgrade_reason=row["downgrade_reason"],
    )
