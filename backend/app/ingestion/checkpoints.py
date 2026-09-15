"""Where each scheduled job may resume from.

A checkpoint is the smallest piece of state in ADG and the one with the most dangerous
failure mode. Every other stored fact, if it is wrong, is *visibly* wrong: an ACE nobody
sent, a principal with no SID. A checkpoint that is wrong is invisible, because its effect
is on what the next run **does not read** — and a run that skipped an object reports
success, sends no error, and leaves nothing missing to notice.

So the rules here are all refusals, and all of them are written down rather than implied.

**A cursor advances only behind data that landed.** The store is called from inside the
batch transaction, after the observations in that batch have been written, and again inside
the completion transaction. Nothing advances a checkpoint speculatively. If a batch never
arrives, the cursor stays behind the objects that batch would have carried, and the next
delta reads them again — the correct failure, because re-reading is free and skipping is
not.

**A cursor advances only within one issuer.** ``uSNChanged`` is a counter on one domain
controller. Across DCs the numbers are unrelated; on the *same* DC after a restore from
backup they are reused, which is why the issuer is the service name and the invocation id
together rather than a host name. When the issuer changes, the new cursor is refused and
the *reason is stored on the row*, so the collector's next run is told to read everything
instead of resuming from a number that no longer means what it meant.

**A refusal is recorded, not swallowed.** A job whose checkpoint cannot advance keeps
running, keeps succeeding, and keeps resuming from the same stale point forever. The only
thing that distinguishes that from working is ``last_rejection_code`` on the row, so it is
written where the operations endpoint reads it.

See `docs/architecture/incremental-collection.md` §4.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import RowMapping, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import Checkpoint, CheckpointKind, CheckpointRejection, CollectorKind
from app.models.schema import collector_checkpoints, scan_run_checkpoints

__all__ = ["CheckpointAdvance", "CheckpointStore", "StoredCheckpoint"]


@dataclass(frozen=True, slots=True)
class StoredCheckpoint:
    """A job's resume point as the database holds it."""

    collector: CollectorKind
    job: str
    checkpoint: Checkpoint
    run_id: UUID | None
    batch_id: UUID | None
    collector_host: str | None
    advanced_at: dt.datetime
    last_rejection_code: str | None = None
    last_rejection_message: str | None = None
    last_rejected_at: dt.datetime | None = None

    @property
    def is_blocked(self) -> bool:
        """Whether the most recent attempt to move this cursor was refused.

        A blocked job is still collecting — it simply cannot narrow what it reads, so it
        will re-read its whole scope until somebody looks. That is the safe direction, and
        it is why this is a report rather than an error.
        """
        return self.last_rejection_code is not None


@dataclass(frozen=True, slots=True)
class CheckpointAdvance:
    """What one attempt to move a job's cursor did."""

    collector: CollectorKind
    job: str
    accepted: bool
    checkpoint: Checkpoint
    previous: Checkpoint | None = None
    rejection: CheckpointRejection | None = None

    @property
    def code(self) -> str | None:
        return self.rejection.code if self.rejection else None


class CheckpointStore:
    """Reads and advances job checkpoints over one session.

    Commits nothing: the caller owns the transaction, which is the whole point. A cursor
    committed separately from the batch it describes could be ahead of the data by exactly
    one crash.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(self, collector: CollectorKind, job: str) -> StoredCheckpoint | None:
        row = (
            (
                await self._session.execute(
                    select(collector_checkpoints).where(
                        collector_checkpoints.c.collector == collector.value,
                        collector_checkpoints.c.job == job,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return _stored(row) if row is not None else None

    async def read_many(
        self, collector: CollectorKind | None = None
    ) -> tuple[StoredCheckpoint, ...]:
        """Every stored checkpoint, for the operations view."""
        statement = select(collector_checkpoints).order_by(
            collector_checkpoints.c.collector, collector_checkpoints.c.job
        )
        if collector is not None:
            statement = statement.where(collector_checkpoints.c.collector == collector.value)
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(_stored(row) for row in rows)

    async def advance(
        self,
        *,
        collector: CollectorKind,
        job: str,
        checkpoint: Checkpoint,
        run_id: UUID | None,
        batch_id: UUID | None,
        collector_host: str | None,
        now: dt.datetime,
    ) -> CheckpointAdvance:
        """Move a job's cursor, or refuse and record why.

        The row is locked for the read so that two batches of the same job — which the
        protocol permits to arrive concurrently — cannot both read the old cursor, both
        decide they are ahead of it, and have the later write win regardless of which is
        actually further on.
        """
        existing = (
            (
                await self._session.execute(
                    select(collector_checkpoints)
                    .where(
                        collector_checkpoints.c.collector == collector.value,
                        collector_checkpoints.c.job == job,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        previous = _checkpoint(existing) if existing is not None else None

        rejection = checkpoint.advances_over(previous)
        if rejection is not None:
            await self._record_rejection(collector, job, rejection, now)
            return CheckpointAdvance(
                collector=collector,
                job=job,
                accepted=False,
                checkpoint=checkpoint,
                previous=previous,
                rejection=rejection,
            )

        values = {
            "collector": collector.value,
            "job": job,
            "checkpoint_kind": checkpoint.kind.value,
            "token": checkpoint.token,
            "issuer": checkpoint.issuer,
            "issued_at": checkpoint.issued_at,
            "run_id": run_id,
            "batch_id": batch_id,
            "collector_host": collector_host,
            "advanced_at": now,
            # Cleared on every accepted advance: the block is over, and leaving the reason
            # behind would keep reporting a job as stuck after it had recovered.
            "last_rejection_code": None,
            "last_rejection_message": None,
            "last_rejected_at": None,
            "created_at": now,
            "updated_at": now,
        }
        statement = pg_insert(collector_checkpoints).values(values)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[collector_checkpoints.c.collector, collector_checkpoints.c.job],
                set_={
                    key: statement.excluded[key]
                    for key in values
                    if key not in ("collector", "job", "created_at")
                },
            )
        )
        return CheckpointAdvance(
            collector=collector,
            job=job,
            accepted=True,
            checkpoint=checkpoint,
            previous=previous,
        )

    async def _record_rejection(
        self,
        collector: CollectorKind,
        job: str,
        rejection: CheckpointRejection,
        now: dt.datetime,
    ) -> None:
        await self._session.execute(
            update(collector_checkpoints)
            .where(
                collector_checkpoints.c.collector == collector.value,
                collector_checkpoints.c.job == job,
            )
            .values(
                last_rejection_code=rejection.code,
                last_rejection_message=rejection.message,
                last_rejected_at=now,
                updated_at=now,
            )
        )

    async def record_for_run(
        self, run_id: UUID, role: str, checkpoint: Checkpoint, now: dt.datetime
    ) -> None:
        """Keep a run's own two cursors beside the run.

        Separate from the job's cursor on purpose. The job's row says where the *next* run
        may start; these two say what *this* run claimed, which is what an operator needs
        when a gap has to be attributed to a particular run months later.
        """
        values = {
            "run_id": run_id,
            "role": role,
            "checkpoint_kind": checkpoint.kind.value,
            "token": checkpoint.token,
            "issuer": checkpoint.issuer,
            "issued_at": checkpoint.issued_at,
            "recorded_at": now,
        }
        statement = pg_insert(scan_run_checkpoints).values(values)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[scan_run_checkpoints.c.run_id, scan_run_checkpoints.c.role],
                set_={key: statement.excluded[key] for key in values if key != "run_id"},
            )
        )


def _checkpoint(row: RowMapping) -> Checkpoint:
    return Checkpoint(
        kind=CheckpointKind(row["checkpoint_kind"]),
        token=row["token"],
        issuer=row["issuer"],
        issued_at=row["issued_at"],
    )


def _stored(row: RowMapping) -> StoredCheckpoint:
    return StoredCheckpoint(
        collector=CollectorKind(row["collector"]),
        job=row["job"],
        checkpoint=_checkpoint(row),
        run_id=row["run_id"],
        batch_id=row["batch_id"],
        collector_host=row["collector_host"],
        advanced_at=row["advanced_at"],
        last_rejection_code=row["last_rejection_code"],
        last_rejection_message=row["last_rejection_message"],
        last_rejected_at=row["last_rejected_at"],
    )
