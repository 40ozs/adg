"""Draining the outbox: one bounded pass, one attempt per due delivery.

The dispatcher owns exactly one decision that neither the outbox nor a sink can make: given
that an attempt failed, **when — or whether — to try again**. The sink says whether retrying
could help; the retry schedule says how long to wait and how many times; this puts the two
together and writes the answer down.

Three properties, each of which is a way this can go wrong in production:

**Bounded.** A pass claims at most ``limit`` deliveries. A backlog therefore costs many short
passes rather than one unbounded run, and an operator draining by hand gets their prompt
back. :meth:`AlertDispatcher.drain` reports whether more remained, so a caller that wants the
whole queue loops deliberately instead of by accident.

**One attempt per delivery per pass.** A delivery that fails is scheduled and not retried
inside the same drain. Retrying immediately would burn the whole schedule against an endpoint
that has been down for one second, and the attempt ceiling would be reached before anybody
noticed there was a problem.

**Never raises for a delivery.** A sink that throws despite the protocol is caught here and
recorded as a transient failure. The alternative is a drain that dies on delivery three and
leaves forty-seven behind it unattempted — and the forty-seven would look, in the queue, like
deliveries nobody had got to yet.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.alerts.configuration import AlertPolicy, RetrySchedule
from app.alerts.outbox import ClaimedDelivery, DeliveryStatus, Outbox
from app.alerts.sinks import AlertSink, DeliveryResult

__all__ = ["AlertDispatcher", "DrainReport"]

logger = logging.getLogger("app.alerts.dispatcher")


@dataclass(frozen=True, slots=True)
class DrainReport:
    """What one pass did."""

    attempted: int = 0
    delivered: int = 0
    retrying: int = 0
    abandoned: int = 0
    #: Deliveries claimed whose sink is no longer in the policy. Left pending and counted,
    #: never abandoned: a sink removed while deliveries were queued for it is far more often
    #: a policy typo than a decision to discard those alerts, and abandoning them would be
    #: irreversible in a way that leaving them is not.
    unroutable: int = 0
    has_more: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def summary(self) -> str:
        """One line for an operator log, in American English."""
        return (
            f"{self.attempted} attempted, {self.delivered} delivered, "
            f"{self.retrying} scheduled for retry, {self.abandoned} abandoned, "
            f"{self.unroutable} unroutable" + (", more pending" if self.has_more else "")
        )


class AlertDispatcher:
    """Delivers what the outbox holds, through the sinks a policy names."""

    def __init__(
        self,
        outbox: Outbox,
        sinks: Mapping[str, AlertSink],
        *,
        retry: RetrySchedule | None = None,
        batch_size: int = 50,
    ) -> None:
        self._outbox = outbox
        self._sinks = dict(sinks)
        self._retry = retry or RetrySchedule()
        self._batch_size = batch_size

    @classmethod
    def from_policy(
        cls, outbox: Outbox, policy: AlertPolicy, sinks: Mapping[str, AlertSink]
    ) -> AlertDispatcher:
        return cls(
            outbox,
            sinks,
            retry=policy.retry,
            batch_size=policy.drain_batch_size,
        )

    async def drain(self, *, now: dt.datetime, limit: int | None = None) -> DrainReport:
        """Attempt every delivery that is due, up to the batch ceiling."""
        size = limit if limit is not None else self._batch_size
        claimed = await self._outbox.claim(now=now, limit=size)
        if not claimed:
            return DrainReport(has_more=False)

        attempted = delivered = retrying = abandoned = unroutable = 0
        errors: list[str] = []

        for delivery in claimed:
            sink = self._sinks.get(delivery.envelope.sink_name)
            if sink is None:
                unroutable += 1
                errors.append(
                    f"{delivery.envelope.sink_name!r} is not a configured sink; "
                    f"delivery {delivery.delivery_id} left pending"
                )
                continue

            attempted += 1
            result = await self._attempt(sink, delivery)
            if result.delivered:
                await self._outbox.record_success(
                    delivery.delivery_id, now=now, detail=result.detail
                )
                delivered += 1
                continue

            attempts = delivery.attempts + 1
            give_up = not result.retryable or self._retry.exhausted(attempts)
            retry_at = None if give_up else now + self._retry.delay_before(attempts)
            reason = result.detail or "delivery failed"
            await self._outbox.record_failure(
                delivery.delivery_id, now=now, error=reason, retry_at=retry_at
            )
            if give_up:
                abandoned += 1
                # An abandoned alert is somebody not being told something. It gets an error
                # line whether or not anything is reading the drain's return value.
                logger.error(
                    "alert.delivery.abandoned",
                    extra={
                        "delivery_id": str(delivery.delivery_id),
                        "alert_key": delivery.envelope.alert_key,
                        "trigger": delivery.envelope.trigger.value,
                        "sink": delivery.envelope.sink_name,
                        "attempts": attempts,
                        "reason": reason,
                        "retryable": result.retryable,
                    },
                )
                errors.append(
                    f"{delivery.envelope.sink_name}: abandoned after {attempts}: {reason}"
                )
            else:
                retrying += 1
                logger.warning(
                    "alert.delivery.retrying",
                    extra={
                        "delivery_id": str(delivery.delivery_id),
                        "sink": delivery.envelope.sink_name,
                        "attempts": attempts,
                        "retry_at": retry_at.isoformat() if retry_at else None,
                        "reason": reason,
                    },
                )

        return DrainReport(
            attempted=attempted,
            delivered=delivered,
            retrying=retrying,
            abandoned=abandoned,
            unroutable=unroutable,
            has_more=len(claimed) == size,
            errors=tuple(errors),
        )

    async def drain_until_empty(
        self, *, now: dt.datetime, max_passes: int = 20
    ) -> tuple[DrainReport, ...]:
        """Repeat :meth:`drain` while work remains, up to ``max_passes``.

        Bounded rather than a ``while True``. A sink failing transiently schedules its retry
        into the future, so a genuine backlog empties; a bug that re-enqueued would otherwise
        spin here forever, and the ceiling turns that into a visible short report.
        """
        passes: list[DrainReport] = []
        for _ in range(max_passes):
            report = await self.drain(now=now)
            if report.attempted == 0 and report.unroutable == 0:
                break
            passes.append(report)
            if not report.has_more:
                break
        return tuple(passes)

    async def depth(self, *, now: dt.datetime) -> dict[DeliveryStatus, int]:
        return await self._outbox.depth(now=now)

    async def _attempt(self, sink: AlertSink, delivery: ClaimedDelivery) -> DeliveryResult:
        try:
            return await sink.deliver(delivery.envelope)
        except Exception as error:
            logger.exception(
                "alert.delivery.sink_raised",
                extra={
                    "delivery_id": str(delivery.delivery_id),
                    "sink": delivery.envelope.sink_name,
                },
            )
            return DeliveryResult.transient(f"sink raised {type(error).__name__}: {error}")


def build_sinks(policy: AlertPolicy, factory: object = None) -> dict[str, AlertSink]:
    """Every enabled sink the policy names, by name.

    Kept here rather than in :mod:`app.alerts.sinks` because it is a dispatcher concern:
    sinks are values, and which of them a running process holds is a question about that
    process.
    """
    from app.alerts.sinks import build_sink

    registry = factory if isinstance(factory, dict) else None
    built: dict[str, AlertSink] = {}
    for config in policy.sinks:
        if not config.enabled:
            continue
        built[config.name] = build_sink(config, registry)
    return built


def envelopes_for(policy: AlertPolicy, trigger_value: str) -> Sequence[str]:
    """The sink names a trigger's alerts should be addressed to."""
    from app.alerts.model import AlertTrigger

    trigger = AlertTrigger(trigger_value)
    return [sink.name for sink in policy.sinks_for(trigger)]
