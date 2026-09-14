"""Reading the collection basis: one aggregate, once per request.

The whole of this module is a single ``SELECT`` over ``scan_runs``. It is separate from the
other repositories because it belongs to none of them: the basis is not a fact about
principals or resources, it is a fact about *collection*, and every derived endpoint needs
it regardless of which area it answers about.

Cost. ``scan_runs`` holds one row per collector execution — a few per day per collector, so
thousands after years — and the aggregate is a sequential scan over that. It is measured
rather than assumed: ``tests/db/test_query_cost.py``'s harness counts the statements a
request issues, and the explanation endpoint's budget accounts for this one.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.basis import EMPTY_BASIS, CollectionBasis
from app.models.schema import scan_runs

__all__ = ["CollectionBasisRepository"]


class CollectionBasisRepository:
    """The identity of the collected state, over one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(self) -> CollectionBasis:
        """Summarize every scan run into one comparable basis.

        Returns :data:`app.domain.EMPTY_BASIS` when no run exists, rather than a basis full
        of nulls: "nothing has been collected" is a state a client renders, and it needs a
        cache identity like any other.
        """
        latest = (
            select(scan_runs.c.run_id)
            .order_by(scan_runs.c.updated_at.desc(), scan_runs.c.run_id)
            .limit(1)
            .scalar_subquery()
        )
        statement = select(
            func.count().label("runs"),
            func.max(scan_runs.c.updated_at).label("latest_activity_at"),
            func.coalesce(func.sum(scan_runs.c.observation_count_applied), 0).label(
                "observations_applied"
            ),
            func.coalesce(func.sum(scan_runs.c.batch_count_received), 0).label("batches_received"),
            latest.label("latest_run_id"),
        ).select_from(scan_runs)

        row = (await self._session.execute(statement)).mappings().one_or_none()
        if row is None or not row["runs"]:
            return EMPTY_BASIS
        return CollectionBasis(
            runs=int(row["runs"]),
            latest_run_id=_text(row["latest_run_id"]),
            latest_activity_at=_moment(row["latest_activity_at"]),
            observations_applied=int(row["observations_applied"]),
            batches_received=int(row["batches_received"]),
        )


def _text(value: Any) -> str | None:
    """A run id as a string. The column is a UUID, and the API renders one."""
    return None if value is None else str(value)


def _moment(value: Any) -> dt.datetime | None:
    return value if isinstance(value, dt.datetime) else None
