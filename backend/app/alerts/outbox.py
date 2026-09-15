"""The seam between raising an alert and delivering one.

Everything upstream of this module decides *that* somebody should be told. Everything
downstream decides *how*. The outbox is the only thing they share, and it is a queue with a
transaction boundary through the middle of it — which is the entire point.

## Why a queue and not a call

The acceptance criterion this exists for is short: **alert delivery failure does not roll
back source ingestion.** A collector posts a run completion; the run is recorded; watches
match; alerts are raised. If raising an alert meant calling a webhook, then an endpoint that
is down, slow, or returning 500 would take the ingestion transaction with it — and the
estate would lose an observation because somebody's chat integration expired. Worse, it
would do so intermittently, which is how a collector ends up with a permanently failing
job nobody can reproduce.

So enqueueing is a **database write in the same transaction as the alert**, and delivery is a
**separate pass in a separate transaction**. An alert that was raised is durable whether or
not anything ever delivers it, and a delivery that fails is a row with a retry time on it
rather than an exception somewhere up the stack.

## Idempotency, and what it is actually for

Every envelope carries an :attr:`DeliveryEnvelope.idempotency_key` that is a pure function of
the alert event and the sink. It is sent with the request, and it is stable across retries —
so a receiver that has already applied the first attempt of a delivery whose acknowledgment
was lost can recognize the second and do nothing. Without it, "retry" and "duplicate" are the
same thing at the far end, and the honest choices are to retry and risk duplicates or to give
up and risk silence.

The key is **not** a secret and grants nothing. It is a name for one delivery.

## What the protocol deliberately does not have

No ``delete``. A delivered alert's row is the evidence that it was delivered, and an
abandoned one is the evidence that it was not; pruning belongs to a retention policy an
operator sets, next to the one for history, and not to the sender.

No ``deliver``. The outbox hands out work and records outcomes. What a delivery *is* lives in
:mod:`app.alerts.sinks`, which the outbox never imports — so a storage bug cannot become a
delivery bug and the in-memory implementation below is a complete substitute for the
database one in every test that is about dispatch rather than about SQL.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.alerts.model import AlertTransition, AlertTrigger

__all__ = [
    "ClaimedDelivery",
    "DeliveryEnvelope",
    "DeliveryStatus",
    "InMemoryOutbox",
    "Outbox",
    "idempotency_key",
]


class DeliveryStatus(StrEnum):
    """Where one delivery stands."""

    PENDING = "pending"
    """Waiting to be attempted, or waiting out a retry delay."""

    DELIVERED = "delivered"
    """The sink accepted it. Terminal."""

    FAILED = "failed"
    """The last attempt failed and another is scheduled. Still pending in spirit; a separate
    value so that "has never succeeded but is still trying" is visible in a queue-depth
    reading rather than hidden inside ``pending``."""

    ABANDONED = "abandoned"
    """No further attempt will be made -- the retry schedule ran out, or the sink reported a
    failure that retrying cannot fix. Terminal, and **loud**: an abandoned delivery is an
    alert somebody was meant to receive and did not, which an operator has to be able to
    see. Nothing deletes these."""


def idempotency_key(event_id: UUID, sink_name: str) -> str:
    """A stable name for one delivery of one alert event to one sink.

    A digest rather than the two values joined, so that it can be sent in a header without
    leaking a sink's configured name to the receiver, and so that its length is fixed
    whatever the sink is called.
    """
    material = f"{event_id}\x1f{sink_name}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DeliveryEnvelope:
    """One alert, addressed to one sink, in the form a sink actually receives.

    Flat and self-contained on purpose. A sink is handed this and nothing else — no session,
    no repository, no alert object with lazy attributes — so a sink cannot read anything the
    envelope does not say, and what a sink may see is reviewable by reading this class.
    """

    event_id: UUID
    alert_key: str
    trigger: AlertTrigger
    transition: AlertTransition
    sink_name: str
    occurred_at: dt.datetime
    summary: str
    payload: dict[str, Any]
    #: Occurrences this delivery speaks for, the current one included. Above 1 means a
    #: cooldown folded others in, and the sink is expected to say so: a notification that
    #: silently stands for eleven changes is a notification that under-reports by ten.
    folds: int = 1
    watch_id: UUID | None = None
    watch_label: str | None = None

    @property
    def idempotency_key(self) -> str:
        return idempotency_key(self.event_id, self.sink_name)

    def document(self) -> dict[str, Any]:
        """The vendor-neutral JSON body a sink sends.

        ADG's own document, not anybody's message format. A sink that needed Slack blocks or
        an email MIME tree would build them from this; nothing upstream of here has ever
        heard of either, which is the requirement that keeps the rules independent of the
        channels (prompt item 6).
        """
        return {
            "schema": "adg.alert/v1",
            "event_id": str(self.event_id),
            "alert_key": self.alert_key,
            "trigger": self.trigger.value,
            "transition": self.transition.value,
            "occurred_at": self.occurred_at.isoformat(),
            "summary": self.summary,
            "occurrences": self.folds,
            "watch": (
                None
                if self.watch_id is None
                else {"watch_id": str(self.watch_id), "label": self.watch_label}
            ),
            "detail": self.payload,
        }


@dataclass(frozen=True, slots=True)
class ClaimedDelivery:
    """A pending delivery handed out for one attempt."""

    delivery_id: UUID
    envelope: DeliveryEnvelope
    attempts: int
    """Attempts already made. 0 for a delivery that has never been tried."""

    first_enqueued_at: dt.datetime
    last_error: str | None = None


class Outbox(Protocol):
    """Storage for deliveries. Implemented over SQL in production and in memory in tests.

    Every method takes the instant it should use rather than reading a clock, for the reason
    the rest of this codebase gives: a retry schedule that consults ``now()`` internally can
    only be tested by waiting.
    """

    async def enqueue(self, envelopes: Sequence[DeliveryEnvelope], *, now: dt.datetime) -> int:
        """Record deliveries to attempt. Returns how many were newly recorded.

        **Idempotent on the idempotency key.** Enqueueing the same envelope twice records
        one delivery, so a retried ingestion that re-raises an alert cannot double-send it.
        """
        ...

    async def claim(self, *, now: dt.datetime, limit: int) -> tuple[ClaimedDelivery, ...]:
        """Take up to ``limit`` deliveries that are due, oldest first."""
        ...

    async def record_success(
        self, delivery_id: UUID, *, now: dt.datetime, detail: str | None = None
    ) -> None: ...

    async def record_failure(
        self,
        delivery_id: UUID,
        *,
        now: dt.datetime,
        error: str,
        retry_at: dt.datetime | None,
    ) -> None:
        """Record a failed attempt. ``retry_at`` of ``None`` abandons the delivery."""
        ...

    async def depth(self, *, now: dt.datetime) -> dict[DeliveryStatus, int]:
        """How many deliveries stand in each state. The operator's queue reading."""
        ...


@dataclass
class _Record:
    delivery_id: UUID
    envelope: DeliveryEnvelope
    status: DeliveryStatus
    attempts: int
    first_enqueued_at: dt.datetime
    next_attempt_at: dt.datetime
    last_error: str | None = None
    delivered_at: dt.datetime | None = None


@dataclass
class InMemoryOutbox:
    """A complete outbox that keeps nothing. For tests, and for reading the protocol.

    Not a stub: it enforces the same idempotency rule and the same claim ordering as the SQL
    implementation, so a dispatcher test that passes against this one is testing the
    dispatcher rather than testing a mock that agrees with it.
    """

    records: dict[UUID, _Record] = field(default_factory=dict)
    _by_key: dict[str, UUID] = field(default_factory=dict)

    async def enqueue(self, envelopes: Sequence[DeliveryEnvelope], *, now: dt.datetime) -> int:
        written = 0
        for envelope in envelopes:
            key = envelope.idempotency_key
            if key in self._by_key:
                continue
            delivery_id = uuid4()
            self._by_key[key] = delivery_id
            self.records[delivery_id] = _Record(
                delivery_id=delivery_id,
                envelope=envelope,
                status=DeliveryStatus.PENDING,
                attempts=0,
                first_enqueued_at=now,
                next_attempt_at=now,
            )
            written += 1
        return written

    async def claim(self, *, now: dt.datetime, limit: int) -> tuple[ClaimedDelivery, ...]:
        due = sorted(
            (
                record
                for record in self.records.values()
                if record.status in (DeliveryStatus.PENDING, DeliveryStatus.FAILED)
                and record.next_attempt_at <= now
            ),
            key=lambda record: (record.next_attempt_at, record.first_enqueued_at),
        )
        return tuple(
            ClaimedDelivery(
                delivery_id=record.delivery_id,
                envelope=record.envelope,
                attempts=record.attempts,
                first_enqueued_at=record.first_enqueued_at,
                last_error=record.last_error,
            )
            for record in due[:limit]
        )

    async def record_success(
        self, delivery_id: UUID, *, now: dt.datetime, detail: str | None = None
    ) -> None:
        record = self.records[delivery_id]
        record.status = DeliveryStatus.DELIVERED
        record.attempts += 1
        record.delivered_at = now
        record.last_error = None

    async def record_failure(
        self,
        delivery_id: UUID,
        *,
        now: dt.datetime,
        error: str,
        retry_at: dt.datetime | None,
    ) -> None:
        record = self.records[delivery_id]
        record.attempts += 1
        record.last_error = error
        if retry_at is None:
            record.status = DeliveryStatus.ABANDONED
        else:
            record.status = DeliveryStatus.FAILED
            record.next_attempt_at = retry_at

    async def depth(self, *, now: dt.datetime) -> dict[DeliveryStatus, int]:
        counts = dict.fromkeys(DeliveryStatus, 0)
        for record in self.records.values():
            counts[record.status] += 1
        return counts

    # -- test conveniences, not part of the protocol ----------------------------------

    def delivered(self) -> tuple[DeliveryEnvelope, ...]:
        return tuple(
            record.envelope
            for record in self.records.values()
            if record.status is DeliveryStatus.DELIVERED
        )

    def snapshot(self, delivery_id: UUID) -> _Record:
        return replace(self.records[delivery_id])
