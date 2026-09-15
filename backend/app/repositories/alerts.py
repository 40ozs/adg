"""Storage for watches, alerts, and the outbox that delivers them.

Three repositories over one session, split by what they are for rather than by table:

* :class:`WatchRepository` — the configuration an operator writes.
* :class:`AlertRepository` — raising, suppressing and resolving. This is where
  :mod:`app.alerts.dedupe`'s decision meets a row.
* :class:`SqlAlertOutbox` — the :class:`app.alerts.Outbox` protocol over PostgreSQL.

Nothing here decides anything. Whether an occurrence notifies is
:func:`app.alerts.decide_raise`; how long to wait before a retry is
:class:`app.alerts.RetrySchedule`; what an alert *says* is :mod:`app.alerts.detection`. This
module loads, writes, and preserves two invariants the decision layer cannot see:

**A suppressed occurrence still writes an event.** Every path through :meth:`AlertRepository.
record` inserts into ``alert_events``, including the suppressed ones. That is the property
that keeps a quiet feed distinguishable from a broken one, and it is enforced here because
here is the only place that can fail to do it.

**``delivered_digest`` moves only on a notification.** A suppressed repeat updates the counts
and leaves it alone. If a suppression moved it, the next occurrence of the content an operator
was actually shown would compare unequal and read as new — the deduplication would be
inverted, quietly, in the direction that produces noise about things nobody was told.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import CursorResult
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import (
    AlertEvent,
    AlertLifecycle,
    AlertState,
    AlertStatus,
    AlertTransition,
    AlertTrigger,
    ClaimedDelivery,
    Decision,
    DeliveryEnvelope,
    DeliveryStatus,
    Watch,
    WatchKind,
    decide_raise,
    decide_resolve,
)
from app.alerts.model import TRIGGER_LIFECYCLE
from app.domain import DomainValidationError
from app.models.schema import alert_deliveries, alert_events, alert_watches, alerts

__all__ = [
    "AlertRepository",
    "RecordedAlert",
    "SqlAlertOutbox",
    "StoredAlert",
    "WatchConflict",
    "WatchRepository",
    "stored_alert",
]


class WatchConflict(DomainValidationError):
    """A watch already exists on that thing. Mapped to HTTP 409 by the API."""


@dataclass(frozen=True, slots=True)
class StoredAlert:
    """An alert as it currently stands."""

    key: str
    trigger: AlertTrigger
    lifecycle: AlertLifecycle
    status: AlertStatus
    watch_id: UUID | None
    watch_label: str | None
    resource_key: str | None
    share_key: str | None
    principal_key: str | None
    discriminator: str | None
    summary: str
    payload: Mapping[str, Any]
    payload_digest: str
    delivered_digest: str | None
    first_raised_at: dt.datetime
    last_raised_at: dt.datetime
    last_notified_at: dt.datetime | None
    resolved_at: dt.datetime | None
    occurrence_count: int
    suppressed_since_notice: int
    suppressed_total: int

    @property
    def is_open(self) -> bool:
        return self.status is AlertStatus.OPEN

    def state(self) -> AlertState:
        """The projection :mod:`app.alerts.dedupe` reads."""
        return AlertState(
            key=self.key,
            status=self.status,
            lifecycle=self.lifecycle,
            delivered_digest=self.delivered_digest,
            last_notified_at=self.last_notified_at,
            suppressed_since_notice=self.suppressed_since_notice,
        )


@dataclass(frozen=True, slots=True)
class RecordedAlert:
    """One occurrence, after it has been written."""

    event_id: UUID
    alert_key: str
    trigger: AlertTrigger
    transition: AlertTransition
    decision: Decision
    summary: str
    payload: Mapping[str, Any]
    occurred_at: dt.datetime
    watch_id: UUID | None = None
    watch_label: str | None = None

    @property
    def notified(self) -> bool:
        return self.decision.notifies


# ------------------------------------------------------------------------- watches


class WatchRepository:
    """The watch table, as values."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, watch: Watch, *, actor: str) -> Watch:
        """Insert a watch. Raises :class:`WatchConflict` if one already covers that thing."""
        existing = await self.for_target(watch.kind, watch.key)
        if existing is not None:
            raise WatchConflict(
                f"A {watch.kind.value} watch on {watch.key!r} already exists "
                f"({existing.watch_id}). A second one would double every alert about it and "
                "give each copy its own cooldown, so an operator who set a quiet window "
                "would still be notified at the other watch's rate. Edit the existing one.",
                field="key",
            )
        await self._session.execute(sa.insert(alert_watches).values(_watch_values(watch, actor)))
        return watch

    async def update(self, watch: Watch, *, actor: str) -> Watch:
        result = await self._session.execute(
            sa.update(alert_watches)
            .where(alert_watches.c.watch_id == watch.watch_id)
            .values(
                {
                    key: value
                    for key, value in _watch_values(watch, actor).items()
                    # The identity of a watch is what it watches. Allowing a kind or key edit
                    # would silently re-point every alert already raised under it at a
                    # different thing, and the history would then say a directory was edited
                    # when it was not.
                    if key not in ("watch_id", "kind", "watch_key", "created_at", "created_by")
                }
            )
        )
        # ``rowcount`` lives on CursorResult, which is what a DML statement returns;
        # the declared return type is the read-only base.
        if cast("CursorResult[Any]", result).rowcount == 0:
            raise DomainValidationError(f"No watch {watch.watch_id}.", field="watch_id")
        return watch

    async def delete(self, watch_id: UUID) -> bool:
        """Remove a watch. Alerts it raised are untouched -- see the migration's note."""
        result = await self._session.execute(
            sa.delete(alert_watches).where(alert_watches.c.watch_id == watch_id)
        )
        return bool(cast("CursorResult[Any]", result).rowcount)

    async def get(self, watch_id: UUID) -> Watch | None:
        row = (
            await self._session.execute(
                sa.select(alert_watches).where(alert_watches.c.watch_id == watch_id)
            )
        ).one_or_none()
        return None if row is None else _watch(row)

    async def for_target(self, kind: WatchKind, key: str) -> Watch | None:
        row = (
            await self._session.execute(
                sa.select(alert_watches).where(
                    alert_watches.c.kind == kind.value,
                    alert_watches.c.watch_key == key.strip().casefold(),
                )
            )
        ).one_or_none()
        return None if row is None else _watch(row)

    async def list(
        self, *, kind: WatchKind | None = None, enabled_only: bool = False
    ) -> tuple[Watch, ...]:
        statement = sa.select(alert_watches).order_by(
            alert_watches.c.kind, alert_watches.c.watch_key
        )
        if kind is not None:
            statement = statement.where(alert_watches.c.kind == kind.value)
        if enabled_only:
            statement = statement.where(alert_watches.c.enabled.is_(True))
        rows = (await self._session.execute(statement)).all()
        return tuple(_watch(row) for row in rows)

    async def enabled(self) -> tuple[Watch, ...]:
        """Every watch a detection pass should match against."""
        return await self.list(enabled_only=True)


def _watch_values(watch: Watch, actor: str) -> dict[str, Any]:
    return {
        "watch_id": watch.watch_id,
        "kind": watch.kind.value,
        # Folded on the way in, so that a watch typed \\FS01\Finance matches the resource key
        # \\fs01\finance. Folding only at comparison time would work until somebody wrote the
        # comparison without it, which is the kind of bug that makes a watch silently inert.
        "watch_key": watch.key.strip().casefold(),
        "label": watch.label,
        "triggers": sorted(trigger.value for trigger in watch.triggers),
        "cooldown_seconds": int(watch.cooldown.total_seconds()),
        "enabled": watch.enabled,
        "notes": watch.notes,
        "created_by": watch.created_by,
        "updated_by": actor,
        "created_at": watch.created_at,
        "updated_at": watch.updated_at,
    }


def _watch(row: Any) -> Watch:
    return Watch(
        watch_id=row.watch_id,
        kind=WatchKind(row.kind),
        key=row.watch_key,
        label=row.label,
        triggers=frozenset(AlertTrigger(value) for value in (row.triggers or ())),
        cooldown=dt.timedelta(seconds=int(row.cooldown_seconds)),
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
        enabled=bool(row.enabled),
        notes=row.notes,
    )


# -------------------------------------------------------------------------- alerts


class AlertRepository:
    """Raising, suppressing and resolving alerts, and reading them back."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> StoredAlert | None:
        row = (
            await self._session.execute(sa.select(alerts).where(alerts.c.alert_key == key))
        ).one_or_none()
        return None if row is None else _stored(row)

    async def by_keys(self, keys: Sequence[str]) -> dict[str, StoredAlert]:
        if not keys:
            return {}
        rows = (
            await self._session.execute(
                sa.select(alerts).where(alerts.c.alert_key.in_(sorted(set(keys))))
            )
        ).all()
        return {row.alert_key: _stored(row) for row in rows}

    async def record(
        self,
        event: AlertEvent,
        *,
        now: dt.datetime,
        cooldown: dt.timedelta,
        watch_enabled: bool = True,
        watch_label: str | None = None,
        source_run_id: UUID | None = None,
        source_evaluation_id: UUID | None = None,
    ) -> RecordedAlert:
        """Record one occurrence, delivering or suppressing it as the decision says.

        Always writes an ``alert_events`` row. That is not a convenience: a suppressed
        occurrence that wrote nothing would make a cooldown and a quiet estate the same
        reading afterwards, and the whole point of a cooldown is that it is recoverable.
        """
        existing = await self.get(event.key)
        decision = decide_raise(
            event,
            existing.state() if existing is not None else None,
            cooldown=cooldown,
            watch_enabled=watch_enabled,
        )
        event_id = uuid4()

        if existing is None:
            await self._insert(event, decision, now=now, watch_label=watch_label)
        else:
            await self._apply(existing, event, decision, now=now, watch_label=watch_label)

        await self._session.execute(
            sa.insert(alert_events).values(
                event_id=event_id,
                alert_key=event.key,
                transition=decision.transition.value,
                trigger=event.trigger.value,
                suppression_reason=(decision.reason.value if decision.reason is not None else None),
                summary=event.summary,
                payload=dict(event.payload),
                payload_digest=event.digest,
                folds=decision.folds,
                notified=decision.notifies,
                source_run_id=source_run_id,
                source_evaluation_id=source_evaluation_id,
                occurred_at=event.occurred_at,
                recorded_at=now,
            )
        )
        return RecordedAlert(
            event_id=event_id,
            alert_key=event.key,
            trigger=event.trigger,
            transition=decision.transition,
            decision=decision,
            summary=event.summary,
            payload=dict(event.payload),
            occurred_at=event.occurred_at,
            watch_id=event.watch_id,
            watch_label=watch_label,
        )

    async def resolve(
        self,
        key: str,
        *,
        now: dt.datetime,
        summary: str | None = None,
        source_evaluation_id: UUID | None = None,
    ) -> RecordedAlert | None:
        """Close a stateful alert. ``None`` when there is nothing to close.

        A resolution is never suppressed and never folded: see :mod:`app.alerts.dedupe`.
        """
        existing = await self.get(key)
        if existing is None:
            return None
        decision = decide_resolve(existing.state())
        if decision is None:
            return None

        text = summary or f"Resolved: {existing.summary}"
        await self._session.execute(
            sa.update(alerts)
            .where(alerts.c.alert_key == key)
            .values(
                status=AlertStatus.RESOLVED.value,
                resolved_at=now,
                last_raised_at=_at_least(existing.last_raised_at, now),
                last_notified_at=now,
                # The resolution is what was delivered, so the digest it is compared against
                # next time is the resolution's. A reopen is checked before the digest in
                # decide_raise, so this cannot suppress one.
                delivered_digest=existing.payload_digest,
                suppressed_since_notice=0,
            )
        )
        event_id = uuid4()
        await self._session.execute(
            sa.insert(alert_events).values(
                event_id=event_id,
                alert_key=key,
                transition=AlertTransition.RESOLVED.value,
                trigger=existing.trigger.value,
                suppression_reason=None,
                summary=text,
                payload={"resolved": True, "resolved_at": now.isoformat()},
                payload_digest=existing.payload_digest,
                folds=1,
                notified=True,
                source_evaluation_id=source_evaluation_id,
                occurred_at=now,
                recorded_at=now,
            )
        )
        return RecordedAlert(
            event_id=event_id,
            alert_key=key,
            trigger=existing.trigger,
            transition=AlertTransition.RESOLVED,
            decision=decision,
            summary=text,
            payload={"resolved": True},
            occurred_at=now,
            watch_id=existing.watch_id,
            watch_label=existing.watch_label,
        )

    async def open_keys_for_trigger(self, trigger: AlertTrigger) -> frozenset[str]:
        """Every open alert of one trigger, by key. The resolution pass's candidate set."""
        rows = (
            await self._session.execute(
                sa.select(alerts.c.alert_key).where(
                    alerts.c.trigger == trigger.value,
                    alerts.c.status == AlertStatus.OPEN.value,
                )
            )
        ).all()
        return frozenset(row.alert_key for row in rows)

    async def events_for(self, key: str, *, limit: int = 100) -> tuple[Mapping[str, Any], ...]:
        rows = (
            await self._session.execute(
                sa.select(alert_events)
                .where(alert_events.c.alert_key == key)
                .order_by(alert_events.c.occurred_at.desc(), alert_events.c.event_id.desc())
                .limit(limit)
            )
        ).all()
        return tuple(dict(row._mapping) for row in rows)

    async def deliveries_for(self, key: str) -> tuple[Mapping[str, Any], ...]:
        rows = (
            await self._session.execute(
                sa.select(alert_deliveries)
                .where(alert_deliveries.c.alert_key == key)
                .order_by(alert_deliveries.c.enqueued_at.desc())
            )
        ).all()
        return tuple(dict(row._mapping) for row in rows)

    async def _insert(
        self,
        event: AlertEvent,
        decision: Decision,
        *,
        now: dt.datetime,
        watch_label: str | None,
    ) -> None:
        notifies = decision.notifies
        await self._session.execute(
            sa.insert(alerts).values(
                alert_key=event.key,
                trigger=event.trigger.value,
                lifecycle=event.lifecycle.value,
                status=AlertStatus.OPEN.value,
                watch_id=event.watch_id,
                watch_label=watch_label,
                resource_key=event.subject.resource_key,
                share_key=event.subject.share_key,
                principal_key=event.subject.principal_key,
                discriminator=event.discriminator,
                summary=event.summary,
                payload=dict(event.payload),
                payload_digest=event.digest,
                delivered_digest=event.digest if notifies else None,
                first_raised_at=event.occurred_at,
                last_raised_at=event.occurred_at,
                last_notified_at=now if notifies else None,
                resolved_at=None,
                occurrence_count=1,
                suppressed_since_notice=0 if notifies else 1,
                suppressed_total=0 if notifies else 1,
            )
        )

    async def _apply(
        self,
        existing: StoredAlert,
        event: AlertEvent,
        decision: Decision,
        *,
        now: dt.datetime,
        watch_label: str | None,
    ) -> None:
        notifies = decision.notifies
        values: dict[str, Any] = {
            "summary": event.summary,
            "payload": dict(event.payload),
            "payload_digest": event.digest,
            "last_raised_at": _at_least(existing.last_raised_at, event.occurred_at),
            "occurrence_count": existing.occurrence_count + 1,
            "watch_label": watch_label if watch_label is not None else existing.watch_label,
        }
        if notifies:
            # Only a notification moves the delivered digest and clears the fold counter.
            # See the module docstring: moving it on a suppression inverts the deduplication.
            values["delivered_digest"] = event.digest
            values["last_notified_at"] = now
            values["suppressed_since_notice"] = 0
        else:
            values["suppressed_since_notice"] = existing.suppressed_since_notice + 1
            values["suppressed_total"] = existing.suppressed_total + 1

        if decision.transition is AlertTransition.REOPENED:
            values["status"] = AlertStatus.OPEN.value
            values["resolved_at"] = None
            # The open window restarts. An "open since" spanning a period the alert was
            # demonstrably resolved would be a claim somebody acts on.
            values["first_raised_at"] = existing.first_raised_at

        await self._session.execute(
            sa.update(alerts).where(alerts.c.alert_key == existing.key).values(values)
        )


def _at_least(current: dt.datetime, candidate: dt.datetime) -> dt.datetime:
    """The later of two instants.

    ``last_raised_at`` must never move backwards. A detection pass over a window that
    overlaps an earlier one legitimately re-reads an older change, and letting it rewind the
    column would make ``first_raised_at <= last_raised_at`` fail — and, before that, would
    make an alert look newer or older than it is in a feed sorted by it.
    """
    return current if current >= candidate else candidate


def stored_alert(row: Any) -> StoredAlert:
    """One ``alerts`` row as a value.

    Public because the API lists alerts with its own filtered query rather than through this
    repository -- the filters belong to the request -- and rendering the rows twice would be
    two places that could disagree about what a column means.
    """
    return _stored(row)


def _stored(row: Any) -> StoredAlert:
    return StoredAlert(
        key=row.alert_key,
        trigger=AlertTrigger(row.trigger),
        lifecycle=AlertLifecycle(row.lifecycle),
        status=AlertStatus(row.status),
        watch_id=row.watch_id,
        watch_label=row.watch_label,
        resource_key=row.resource_key,
        share_key=row.share_key,
        principal_key=row.principal_key,
        discriminator=row.discriminator,
        summary=row.summary,
        payload=dict(row.payload or {}),
        payload_digest=row.payload_digest,
        delivered_digest=row.delivered_digest,
        first_raised_at=row.first_raised_at,
        last_raised_at=row.last_raised_at,
        last_notified_at=row.last_notified_at,
        resolved_at=row.resolved_at,
        occurrence_count=int(row.occurrence_count),
        suppressed_since_notice=int(row.suppressed_since_notice),
        suppressed_total=int(row.suppressed_total),
    )


# ------------------------------------------------------------------------- outbox


class SqlAlertOutbox:
    """:class:`app.alerts.Outbox` over PostgreSQL.

    Every method takes the instant it should use. The one piece of SQL worth reading twice is
    the claim: ``FOR UPDATE SKIP LOCKED``, so that two drains running at once divide the queue
    between them rather than both attempting the same delivery. Without it, a second operator
    running the drain command while a scheduled one is mid-pass would double-send — which the
    receiver's idempotency key would catch, if the receiver honors it, which is not something
    ADG can assume of somebody else's endpoint.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, envelopes: Sequence[DeliveryEnvelope], *, now: dt.datetime) -> int:
        if not envelopes:
            return 0
        rows = [
            {
                "delivery_id": uuid4(),
                "event_id": envelope.event_id,
                "alert_key": envelope.alert_key,
                "sink_name": envelope.sink_name,
                "status": DeliveryStatus.PENDING.value,
                "idempotency_key": envelope.idempotency_key,
                "envelope": _envelope_json(envelope),
                "attempts": 0,
                "last_error": None,
                "enqueued_at": now,
                "next_attempt_at": now,
            }
            for envelope in envelopes
        ]
        # ON CONFLICT DO NOTHING on the idempotency key: enqueueing the same envelope twice
        # records one delivery. This is what makes a retried ingestion safe -- it re-raises
        # the alert, which re-enqueues, which must not double-send.
        #
        # RETURNING rather than ``rowcount``: a multi-row INSERT goes through the driver's
        # executemany path, where ``rowcount`` is -1 and says nothing. Counting the rows the
        # statement actually returned is exact, and it is the number this reports as
        # "newly recorded".
        result = await self._session.execute(
            pg_insert(alert_deliveries)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[alert_deliveries.c.idempotency_key])
            .returning(alert_deliveries.c.delivery_id)
        )
        return len(result.all())

    async def claim(self, *, now: dt.datetime, limit: int) -> tuple[ClaimedDelivery, ...]:
        statement = (
            sa.select(alert_deliveries)
            .where(
                alert_deliveries.c.status.in_(
                    (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value)
                ),
                alert_deliveries.c.next_attempt_at <= now,
            )
            .order_by(alert_deliveries.c.next_attempt_at, alert_deliveries.c.enqueued_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple(
            ClaimedDelivery(
                delivery_id=row.delivery_id,
                envelope=_envelope(row),
                attempts=int(row.attempts),
                first_enqueued_at=row.enqueued_at,
                last_error=row.last_error,
            )
            for row in rows
        )

    async def record_success(
        self, delivery_id: UUID, *, now: dt.datetime, detail: str | None = None
    ) -> None:
        await self._session.execute(
            sa.update(alert_deliveries)
            .where(alert_deliveries.c.delivery_id == delivery_id)
            .values(
                status=DeliveryStatus.DELIVERED.value,
                attempts=alert_deliveries.c.attempts + 1,
                last_attempt_at=now,
                delivered_at=now,
                # Cleared: a delivered row carrying the error of an earlier attempt reads, in
                # a list, as a delivery that failed.
                last_error=None,
            )
        )

    async def record_failure(
        self,
        delivery_id: UUID,
        *,
        now: dt.datetime,
        error: str,
        retry_at: dt.datetime | None,
    ) -> None:
        abandoned = retry_at is None
        await self._session.execute(
            sa.update(alert_deliveries)
            .where(alert_deliveries.c.delivery_id == delivery_id)
            .values(
                status=(
                    DeliveryStatus.ABANDONED.value if abandoned else DeliveryStatus.FAILED.value
                ),
                attempts=alert_deliveries.c.attempts + 1,
                last_attempt_at=now,
                last_error=error[:2000],
                # An abandoned delivery keeps its last scheduled instant rather than being
                # nulled: the column is not nullable, and the value is the last time anything
                # would have tried, which is what an operator reading the row wants.
                next_attempt_at=retry_at if retry_at is not None else now,
            )
        )

    async def depth(self, *, now: dt.datetime) -> dict[DeliveryStatus, int]:
        rows = (
            await self._session.execute(
                sa.select(alert_deliveries.c.status, sa.func.count().label("total")).group_by(
                    alert_deliveries.c.status
                )
            )
        ).all()
        counts = dict.fromkeys(DeliveryStatus, 0)
        for row in rows:
            counts[DeliveryStatus(row.status)] = int(row.total)
        return counts

    async def pending_older_than(self, cutoff: dt.datetime) -> int:
        """Deliveries due before ``cutoff`` and still undelivered. The staleness reading.

        Queue *depth* says how much is waiting; this says how long the oldest has waited,
        which is the number that distinguishes a busy drain from one that is not running at
        all. A pipeline nobody drains has a growing depth and a growing age; a busy one has
        a growing depth and a flat age.
        """
        total = (
            await self._session.execute(
                sa.select(sa.func.count()).where(
                    alert_deliveries.c.status.in_(
                        (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value)
                    ),
                    alert_deliveries.c.next_attempt_at < cutoff,
                )
            )
        ).scalar_one()
        return int(total)


def _envelope_json(envelope: DeliveryEnvelope) -> dict[str, Any]:
    return {
        "event_id": str(envelope.event_id),
        "alert_key": envelope.alert_key,
        "trigger": envelope.trigger.value,
        "transition": envelope.transition.value,
        "sink_name": envelope.sink_name,
        "occurred_at": envelope.occurred_at.isoformat(),
        "summary": envelope.summary,
        "payload": envelope.payload,
        "folds": envelope.folds,
        "watch_id": str(envelope.watch_id) if envelope.watch_id else None,
        "watch_label": envelope.watch_label,
    }


def _envelope(row: Any) -> DeliveryEnvelope:
    stored = dict(row.envelope or {})
    return DeliveryEnvelope(
        event_id=UUID(stored["event_id"]),
        alert_key=stored["alert_key"],
        trigger=AlertTrigger(stored["trigger"]),
        transition=AlertTransition(stored["transition"]),
        sink_name=stored["sink_name"],
        occurred_at=dt.datetime.fromisoformat(stored["occurred_at"]),
        summary=stored["summary"],
        payload=dict(stored.get("payload") or {}),
        folds=int(stored.get("folds", 1)),
        watch_id=UUID(stored["watch_id"]) if stored.get("watch_id") else None,
        watch_label=stored.get("watch_label"),
    )


def lifecycle_of(trigger: AlertTrigger) -> AlertLifecycle:
    """Exposed for the API, which renders it beside the status."""
    return TRIGGER_LIFECYCLE[trigger]
