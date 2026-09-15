"""Delivery: the outbox, the sinks, and the retry schedule between them.

Against :class:`app.alerts.InMemoryOutbox`, which enforces the same idempotency rule and the
same claim ordering as the SQL implementation — so a test that passes here is a test of the
dispatcher rather than of a mock that agrees with it. The SQL implementation's own behavior
is pinned in ``tests/db/test_alerts.py``.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.alerts import (
    AlertDispatcher,
    DeliveryResult,
    DeliveryStatus,
    InMemoryOutbox,
    RetrySchedule,
    idempotency_key,
)
from app.alerts.configuration import PolicyError
from app.alerts.sinks import LogSink, WebhookSink, classify_response
from tests.alerts.support import ExplodingSink, RecordingSink, at, envelope

pytestmark = pytest.mark.anyio

SCHEDULE = RetrySchedule(
    initial=dt.timedelta(seconds=30), multiplier=4.0, maximum=dt.timedelta(hours=1), max_attempts=3
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestTheOutboxIsIdempotent:
    async def test_enqueueing_the_same_envelope_twice_records_one_delivery(self) -> None:
        """What makes a retried ingestion safe.

        A retried scan completion re-raises the alert, which re-enqueues. Without this, the
        recipient would be told twice about one change, and the second telling would carry no
        indication that it was a repeat.
        """
        outbox = InMemoryOutbox()
        one = envelope()
        again = envelope(event_id=one.event_id, sink=one.sink_name)

        assert await outbox.enqueue([one], now=at()) == 1
        assert await outbox.enqueue([again], now=at(minutes=1)) == 0
        assert len(outbox.records) == 1

    async def test_the_same_event_to_two_sinks_is_two_deliveries(self) -> None:
        outbox = InMemoryOutbox()
        one = envelope(sink="log")
        two = envelope(event_id=one.event_id, sink="webhook")

        assert await outbox.enqueue([one, two], now=at()) == 2

    def test_the_key_is_stable_across_retries_and_differs_per_sink(self) -> None:
        """Stable, because a receiver that applied a lost acknowledgment has to recognize
        the next attempt. Per sink, because two sinks are two deliveries."""
        one = envelope()
        assert one.idempotency_key == idempotency_key(one.event_id, one.sink_name)
        assert one.idempotency_key != idempotency_key(one.event_id, "other")


class TestOneDrain:
    async def test_a_successful_delivery_is_terminal(self) -> None:
        outbox = InMemoryOutbox()
        sink = RecordingSink()
        await outbox.enqueue([envelope(sink=sink.name)], now=at())

        report = await AlertDispatcher(outbox, {sink.name: sink}).drain(now=at())

        assert (report.attempted, report.delivered) == (1, 1)
        assert len(sink.received) == 1
        assert (await outbox.depth(now=at()))[DeliveryStatus.DELIVERED] == 1

    async def test_the_sink_receives_adgs_own_document_and_nobody_elses_format(self) -> None:
        """Requirement 6, from the delivery end: no vendor shape anywhere upstream."""
        outbox = InMemoryOutbox()
        sink = RecordingSink()
        await outbox.enqueue([envelope(sink=sink.name)], now=at())

        await AlertDispatcher(outbox, {sink.name: sink}).drain(now=at())

        document = sink.received[0].document()
        assert document["schema"] == "adg.alert/v1"
        assert set(document) == {
            "schema",
            "event_id",
            "alert_key",
            "trigger",
            "transition",
            "occurred_at",
            "summary",
            "occurrences",
            "watch",
            "detail",
        }

    async def test_a_drain_is_bounded_and_says_whether_more_remained(self) -> None:
        outbox = InMemoryOutbox()
        sink = RecordingSink()
        await outbox.enqueue([envelope(sink=sink.name) for _ in range(5)], now=at())

        report = await AlertDispatcher(outbox, {sink.name: sink}, batch_size=2).drain(now=at())

        assert report.attempted == 2
        assert report.has_more

    async def test_nothing_due_is_not_an_error(self) -> None:
        report = await AlertDispatcher(InMemoryOutbox(), {}).drain(now=at())

        assert report.attempted == 0
        assert not report.has_more


class TestRetry:
    async def test_a_transient_failure_is_scheduled_rather_than_retried_in_the_same_pass(
        self,
    ) -> None:
        """Retrying immediately would burn the whole schedule against an endpoint that has
        been down for one second, and the ceiling would be reached before anybody noticed."""
        outbox = InMemoryOutbox()
        sink = RecordingSink(results=[DeliveryResult.transient("HTTP 503")])
        await outbox.enqueue([envelope(sink=sink.name)], now=at())
        dispatcher = AlertDispatcher(outbox, {sink.name: sink}, retry=SCHEDULE)

        report = await dispatcher.drain(now=at())

        assert (report.retrying, report.abandoned) == (1, 0)
        assert len(sink.received) == 1
        record = next(iter(outbox.records.values()))
        assert record.status is DeliveryStatus.FAILED
        assert record.next_attempt_at == at(seconds=30)

    async def test_it_is_not_claimed_again_until_the_delay_has_passed(self) -> None:
        outbox = InMemoryOutbox()
        sink = RecordingSink(results=[DeliveryResult.transient("HTTP 503")])
        dispatcher = AlertDispatcher(outbox, {sink.name: sink}, retry=SCHEDULE)
        await outbox.enqueue([envelope(sink=sink.name)], now=at())
        await dispatcher.drain(now=at())

        too_soon = await dispatcher.drain(now=at(seconds=29))
        due = await dispatcher.drain(now=at(seconds=31))

        assert too_soon.attempted == 0
        assert due.attempted == 1

    async def test_the_schedule_backs_off_and_is_capped(self) -> None:
        assert SCHEDULE.delay_before(1) == dt.timedelta(seconds=30)
        assert SCHEDULE.delay_before(2) == dt.timedelta(minutes=2)
        assert SCHEDULE.delay_before(20) == SCHEDULE.maximum

    async def test_running_out_of_attempts_abandons_and_stops_trying(self) -> None:
        """Abandoned means somebody was meant to be told and was not.

        It is terminal and it is kept, because a delivery that vanished when it gave up
        would make an undelivered alert indistinguishable from one that was never raised.
        """
        outbox = InMemoryOutbox()
        sink = RecordingSink(results=[DeliveryResult.transient("HTTP 503")] * 5)
        dispatcher = AlertDispatcher(outbox, {sink.name: sink}, retry=SCHEDULE)
        await outbox.enqueue([envelope(sink=sink.name)], now=at())

        reports = [
            await dispatcher.drain(now=at(seconds=0)),
            await dispatcher.drain(now=at(minutes=1)),
            await dispatcher.drain(now=at(minutes=10)),
            await dispatcher.drain(now=at(hours=5)),
        ]

        assert reports[-2].abandoned == 1
        assert reports[-1].attempted == 0
        record = next(iter(outbox.records.values()))
        assert record.status is DeliveryStatus.ABANDONED
        assert record.last_error is not None

    async def test_a_permanent_failure_abandons_on_the_first_attempt(self) -> None:
        """A webhook URL with a typo in it fails identically every time.

        Retrying it keeps a delivery that will never succeed at the front of the queue,
        burning attempts, while the real alerts behind it wait.
        """
        outbox = InMemoryOutbox()
        sink = RecordingSink(results=[DeliveryResult.permanent("HTTP 404")])
        dispatcher = AlertDispatcher(outbox, {sink.name: sink}, retry=SCHEDULE)
        await outbox.enqueue([envelope(sink=sink.name)], now=at())

        report = await dispatcher.drain(now=at())

        assert (report.abandoned, report.retrying) == (1, 0)
        assert len(sink.received) == 1

    async def test_a_sink_that_raises_is_caught_and_treated_as_transient(self) -> None:
        """A drain that died on delivery three would leave forty-seven behind it unattempted.

        In the queue, those forty-seven would look like deliveries nothing had got to yet.
        """
        outbox = InMemoryOutbox()
        broken, working = ExplodingSink(), RecordingSink("good")
        await outbox.enqueue([envelope(sink=broken.name), envelope(sink=working.name)], now=at())

        report = await AlertDispatcher(
            outbox, {broken.name: broken, working.name: working}, retry=SCHEDULE
        ).drain(now=at())

        assert report.attempted == 2
        assert report.delivered == 1
        assert report.retrying == 1


class TestAnUnroutableDelivery:
    async def test_a_sink_removed_from_the_policy_leaves_its_deliveries_pending(self) -> None:
        """Far more often a policy typo than a decision to discard those alerts.

        Abandoning them would be irreversible in a way that leaving them is not: restoring
        the sink lets the next drain send them.
        """
        outbox = InMemoryOutbox()
        await outbox.enqueue([envelope(sink="gone")], now=at())

        report = await AlertDispatcher(outbox, {}).drain(now=at())

        assert (report.unroutable, report.attempted) == (1, 0)
        assert report.errors
        assert next(iter(outbox.records.values())).status is DeliveryStatus.PENDING

    async def test_drain_until_empty_does_not_spin_on_one(self) -> None:
        outbox = InMemoryOutbox()
        await outbox.enqueue([envelope(sink="gone")], now=at())

        passes = await AlertDispatcher(outbox, {}, batch_size=1).drain_until_empty(
            now=at(), max_passes=4
        )

        assert len(passes) == 4  # bounded, rather than forever


class TestTheLogSink:
    async def test_it_always_delivers(self) -> None:
        """Which is why it is the wrong sink to test the retry path against."""
        result = await LogSink().deliver(envelope())

        assert result.delivered


class TestClassifyingAnHttpResponse:
    @pytest.mark.parametrize("code", [200, 201, 202, 204])
    def test_a_2xx_is_delivered(self, code: int) -> None:
        assert classify_response(code, "").delivered

    @pytest.mark.parametrize("code", [408, 425, 429, 500, 502, 503, 504])
    def test_backpressure_and_server_errors_are_worth_another_attempt(self, code: int) -> None:
        result = classify_response(code, "busy")
        assert not result.delivered
        assert result.retryable

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 422])
    def test_a_client_error_will_fail_identically_next_time(self, code: int) -> None:
        assert not classify_response(code, "bad token").retryable

    def test_a_redirect_is_permanent_rather_than_followed(self) -> None:
        """Following one would deliver an estate's exposure report to a host nobody
        configured."""
        assert not classify_response(302, "").retryable

    def test_the_error_keeps_an_excerpt_rather_than_the_whole_body(self) -> None:
        """Enough to recognize 'bad token'; not enough to put an HTML error page in the
        database."""
        result = classify_response(500, "x" * 5000)

        assert result.detail is not None
        assert len(result.detail) < 600


class TestTheWebhookSink:
    def test_it_refuses_a_url_that_is_not_http(self) -> None:
        with pytest.raises(PolicyError):
            WebhookSink("w", url="file:///etc/passwd")

    def test_it_is_constructed_before_anything_is_delivered(self) -> None:
        """A policy that only failed once an alert fired would fail at the worst moment and
        leave the alert sitting in the outbox."""
        sink = WebhookSink("w", url="https://example.invalid/hook")

        assert sink.name == "w"
