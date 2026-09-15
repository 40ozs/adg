r"""Persisting proposals, and what they were found to do.

Two tables, ``simulations`` and ``simulation_evaluations``, and **nothing else in ADG reads
either of them.** That is the point of the separation the phase requires: a proposal is not an
observation, it has no provenance a collector would recognize, and an access answer computed
from one would be a fabrication. Keeping the two apart at the table level means the isolation
does not rest on anybody remembering — a query against ``ntfs_aces`` cannot pick up a
simulated ACE, because no simulated ACE was ever written there.

**What is stored is the proposal, not the world it implies.** A row holds the overlay, the
baseline it was written against, and — per evaluation — the compact impact report. It does not
hold the simulated ACLs, the simulated tokens, or the derivations behind the deltas. Those are
reproducible: run the same overlay against the same baseline and the engine produces them
again, byte for byte, because every simulated record is built from constants
(:data:`app.simulation.application.SIMULATED_AT`). A stored copy would be a second account of
the same answer, ageing independently of the code that computes it.

**A stored simulation knows when it has gone stale.** Every row carries the collection-basis
token of the state it was measured against, and that token moves if and only if a collector
has written something. So :meth:`SimulationStore.get` can say *"this impact list was computed
against facts that have since changed"* as a fact rather than as a time-based guess.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import CursorResult, delete, insert, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import CollectionBasis, DomainValidationError
from app.models.schema import simulation_evaluations, simulations
from app.repositories.membership import Page
from app.simulation.model import BaselineKind, ScopeKind, SimulationReport
from app.simulation.overlay import SimulationOverlay

__all__ = [
    "MAX_NAME_LENGTH",
    "SimulationStore",
    "StoredEvaluation",
    "StoredSimulation",
]

MAX_NAME_LENGTH: Final = 200
"""Characters in a proposal's name. Long enough for a change-ticket title, short enough that
a listing renders."""

_DEFAULT_PAGE: Final = 50
_MAX_PAGE: Final = 200


@dataclass(frozen=True, slots=True)
class StoredSimulation:
    """One persisted proposal, with its overlay rebuilt through the overlay's own constructors.

    Rebuilt rather than returned as JSON, so a stored proposal that would no longer be
    accepted — a change kind that has been retired, a mask that is now out of range — is
    refused on the way out instead of being simulated under rules it was never validated
    against.
    """

    simulation_id: UUID
    name: str
    description: str | None
    created_by: str | None
    overlay: SimulationOverlay
    baseline_kind: BaselineKind
    baseline_at: dt.datetime | None
    baseline_token: str
    baseline_run_id: str | None
    baseline_captured_at: dt.datetime
    created_at: dt.datetime
    updated_at: dt.datetime

    @property
    def change_count(self) -> int:
        return len(self.overlay)

    def is_stale_against(self, current: CollectionBasis) -> bool:
        """Whether a collector has written anything since this proposal was measured."""
        return current.token != self.baseline_token


@dataclass(frozen=True, slots=True)
class StoredEvaluation:
    """One run of one proposal, and the compact report it produced."""

    evaluation_id: UUID
    simulation_id: UUID
    scope_kind: ScopeKind
    baseline_token: str
    stale_baseline: bool
    """Whether the collected state had already moved on from the proposal's own baseline when
    this evaluation ran. A true value does not make the result wrong — it was computed against
    real facts — but it does mean the result and the proposal name two different baselines."""

    pairs_evaluated: int
    complete: bool
    duration_ms: int
    report: dict[str, Any]
    computed_at: dt.datetime


class SimulationStore:
    """Reads and writes the two simulation tables, and no others."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------- write

    async def save(
        self,
        report: SimulationReport,
        *,
        name: str,
        description: str | None = None,
        created_by: str | None = None,
        current: CollectionBasis | None = None,
    ) -> StoredSimulation:
        """Store a proposal and the evaluation that produced this report, in one transaction.

        Both rows or neither. An evaluation without its proposal is unreadable — the overlay
        is what says what was simulated — and a proposal whose first evaluation was lost would
        report itself as never run.

        Args:
            report: what :meth:`app.simulation.SimulationService.run` returned.
            name: what to call the proposal in a listing.
            description: free text, optional.
            created_by: the authenticated subject, when there is one.
            current: the basis as it stands now, if the caller has already read it. Used only
                to record whether this evaluation ran against a baseline that had already
                moved; omitted, the report's own baseline is taken as current.
        """
        label = name.strip()
        if not label:
            raise DomainValidationError(
                "A stored proposal needs a name: a listing of unnamed what-ifs is unusable.",
                field="name",
            )
        if len(label) > MAX_NAME_LENGTH:
            raise DomainValidationError(
                f"A proposal name is at most {MAX_NAME_LENGTH} characters.", field="name"
            )

        simulation_id = report.simulation_id or uuid.uuid4()
        now = dt.datetime.now(dt.UTC)
        baseline = report.baseline
        stale = current is not None and baseline.is_stale_against(current)

        await self._session.execute(
            insert(simulations).values(
                simulation_id=simulation_id,
                name=label,
                description=description,
                created_by=created_by,
                overlay=report.overlay.document(),
                overlay_hash=report.overlay.overlay_hash,
                change_count=len(report.overlay),
                baseline_kind=baseline.kind.value,
                baseline_at=baseline.at,
                baseline_token=baseline.token,
                baseline_run_id=_as_uuid(baseline.run_id),
                baseline_captured_at=baseline.captured_at,
                created_at=now,
                updated_at=now,
            )
        )
        await self._record(simulation_id, report, stale=stale, at=now)
        return StoredSimulation(
            simulation_id=simulation_id,
            name=label,
            description=description,
            created_by=created_by,
            overlay=report.overlay,
            baseline_kind=baseline.kind,
            baseline_at=baseline.at,
            baseline_token=baseline.token,
            baseline_run_id=baseline.run_id,
            baseline_captured_at=baseline.captured_at,
            created_at=now,
            updated_at=now,
        )

    async def record(
        self,
        simulation_id: UUID,
        report: SimulationReport,
        *,
        current: CollectionBasis | None = None,
    ) -> UUID:
        """Add another evaluation to a proposal already stored.

        Re-running a proposal after a scan **adds** a row rather than replacing one. Two
        evaluations of one proposal against two collection states are two findings — "this
        change was safe on Monday and takes access away today" is the sentence the history of
        a proposal exists to make available — and overwriting would keep only the newer half.
        """
        stale = current is not None and report.baseline.is_stale_against(current)
        return await self._record(simulation_id, report, stale=stale, at=dt.datetime.now(dt.UTC))

    async def _record(
        self, simulation_id: UUID, report: SimulationReport, *, stale: bool, at: dt.datetime
    ) -> UUID:
        evaluation_id = uuid.uuid4()
        await self._session.execute(
            insert(simulation_evaluations).values(
                evaluation_id=evaluation_id,
                simulation_id=simulation_id,
                scope_kind=report.scope.kind.value,
                baseline_token=report.baseline.token,
                stale_baseline=stale,
                pairs_evaluated=len(report.deltas),
                complete=report.complete,
                duration_ms=report.cost.elapsed_ms,
                report=report.document(),
                computed_at=at,
                created_at=at,
            )
        )
        return evaluation_id

    async def delete(self, simulation_id: UUID) -> bool:
        """Remove a proposal and its evaluations. Returns whether anything was removed.

        The one destructive operation in the package, and it destroys only proposals: the
        cascade reaches ``simulation_evaluations`` and stops there, because nothing else
        points at either table. No collected fact can be reached from here.
        """
        result = await self._session.execute(
            delete(simulations).where(simulations.c.simulation_id == simulation_id)
        )
        # ``rowcount`` is on the cursor result a DELETE returns; the base ``Result`` type
        # does not declare it, so the narrowing is explicit rather than an ignore.
        return bool(result.rowcount) if isinstance(result, CursorResult) else False

    # -------------------------------------------------------------------- read

    async def get(self, simulation_id: UUID) -> StoredSimulation | None:
        row = (
            (
                await self._session.execute(
                    select(simulations).where(simulations.c.simulation_id == simulation_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _stored(row)

    async def list(
        self, *, limit: int = _DEFAULT_PAGE, after: str | None = None
    ) -> Page[StoredSimulation]:
        """Proposals newest first, keyset-paged on ``(created_at, simulation_id)``.

        The cursor is the composite rendered as one string, because two proposals saved in the
        same millisecond would make a timestamp-only cursor skip one.
        """
        page_size = max(1, min(limit, _MAX_PAGE))
        statement = (
            select(simulations)
            .order_by(simulations.c.created_at.desc(), simulations.c.simulation_id.desc())
            .limit(page_size + 1)
        )
        if after is not None:
            moment, _, identifier = after.partition("|")
            try:
                cursor = (dt.datetime.fromisoformat(moment), uuid.UUID(identifier))
            except ValueError as exc:
                raise DomainValidationError(
                    "A simulation listing cursor is the one this endpoint issued; it cannot "
                    "be constructed by hand.",
                    value=after,
                    field="after",
                ) from exc
            statement = statement.where(
                tuple_(simulations.c.created_at, simulations.c.simulation_id) < cursor
            )
        rows = (await self._session.execute(statement)).mappings().all()
        has_more = len(rows) > page_size
        items = tuple(_stored(row) for row in rows[:page_size])
        return Page(
            items=items,
            has_more=has_more,
            next_key=_cursor(items[-1]) if has_more and items else None,
        )

    async def evaluations(
        self, simulation_id: UUID, *, limit: int = _DEFAULT_PAGE
    ) -> tuple[StoredEvaluation, ...]:
        """Every run of one proposal, newest first."""
        statement = (
            select(simulation_evaluations)
            .where(simulation_evaluations.c.simulation_id == simulation_id)
            .order_by(simulation_evaluations.c.computed_at.desc())
            .limit(max(1, min(limit, _MAX_PAGE)))
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(
            StoredEvaluation(
                evaluation_id=row["evaluation_id"],
                simulation_id=row["simulation_id"],
                scope_kind=ScopeKind(row["scope_kind"]),
                baseline_token=row["baseline_token"],
                stale_baseline=bool(row["stale_baseline"]),
                pairs_evaluated=int(row["pairs_evaluated"]),
                complete=bool(row["complete"]),
                duration_ms=int(row["duration_ms"]),
                report=dict(row["report"]),
                computed_at=row["computed_at"],
            )
            for row in rows
        )


def _stored(row: Any) -> StoredSimulation:
    return StoredSimulation(
        simulation_id=row["simulation_id"],
        name=row["name"],
        description=row["description"],
        created_by=row["created_by"],
        overlay=SimulationOverlay.from_document(dict(row["overlay"])),
        baseline_kind=BaselineKind(row["baseline_kind"]),
        baseline_at=row["baseline_at"],
        baseline_token=row["baseline_token"],
        baseline_run_id=None if row["baseline_run_id"] is None else str(row["baseline_run_id"]),
        baseline_captured_at=row["baseline_captured_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _cursor(stored: StoredSimulation) -> str:
    return f"{stored.created_at.isoformat()}|{stored.simulation_id}"


def _as_uuid(value: str | None) -> UUID | None:
    """A run id as a UUID, or ``None`` when the basis had no run to name.

    The basis renders it as a string because a response does; the column is a UUID because
    ``scan_runs.run_id`` is. A value that is not a UUID is dropped rather than stored as
    text: it could only come from a basis built by hand, and storing it would put something
    in the column that never identified a run.
    """
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
