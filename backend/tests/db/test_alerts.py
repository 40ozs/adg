r"""The alert pipeline against a real database, and the HTTP surface over it.

The hermetic suites pin the decisions. This one pins the things that only exist once those
decisions meet PostgreSQL and FastAPI:

1. **The five lifecycle cases through storage** — open, dedupe, repeat, resolve, reopen — so
   that the columns that carry them (``delivered_digest``, ``suppressed_since_notice``,
   ``resolved_at``) actually move the way :mod:`app.alerts.dedupe` decided they should.
2. **A suppressed occurrence is written down.** The property the whole feature rests on, and
   the one a repository is in a position to quietly skip.
3. **Enqueueing is idempotent at the unique constraint**, not merely in the in-memory
   implementation.
4. **Delivery failure does not roll back the source.** Checked with a sink that fails: the
   alert, its event and its queued delivery all survive, and so does the ingestion behind it.
5. **Watch configuration through the API**, including the refusals — the duplicate watch, the
   trigger a kind could never fire, and the capability a reader does not hold.

The estate is :mod:`app.demo.estate`, replayed through the ingestion **HTTP endpoints**, so
the facts under test arrived the way real ones do.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import (
    AlertDispatcher,
    AlertPolicy,
    AlertStatus,
    AlertSubject,
    AlertTransition,
    AlertTrigger,
    DeliveryEnvelope,
    DeliveryResult,
    DeliveryStatus,
    RetrySchedule,
    SuppressionReason,
    Watch,
    WatchKind,
)
from app.auth.roles import Role
from app.demo.estate import DemoEstate, build_estate
from app.demo.seed import seed_transcripts
from app.demo.transcripts import build_transcripts
from app.models.schema import alert_deliveries
from app.repositories.alerts import AlertRepository, SqlAlertOutbox, WatchConflict, WatchRepository
from app.repositories.risk import RiskFactsRepository, RiskFindingRepository
from app.risk_engine import DEFAULT_CONFIGURATION
from app.services.alerts import AlertService, notice_for_finding
from app.services.risk import RiskService

pytestmark = pytest.mark.anyio

NOW = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)
FINANCE = r"\\fs01\finance"
COOLDOWN = dt.timedelta(minutes=15)


def at(**offset: float) -> dt.datetime:
    return NOW + dt.timedelta(**offset)


#: "supply a watch for me" -- distinct from ``None``, which means *deliberately none* and
#: is the only thing the finding trigger accepts.
AUTO_WATCH = "auto"


def _event(
    *,
    payload: dict[str, Any],
    moment: dt.datetime | None = None,
    watch_id: Any = AUTO_WATCH,
    trigger: AlertTrigger = AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED,
    discriminator: str | None = None,
) -> Any:
    from app.alerts import AlertEvent

    return AlertEvent(
        trigger=trigger,
        subject=AlertSubject(resource_key=FINANCE),
        occurred_at=moment or NOW,
        summary="The access control list of Finance changed.",
        payload=payload,
        discriminator=discriminator,
        watch_id=uuid4() if watch_id is AUTO_WATCH else watch_id,
    )


@pytest.fixture(scope="module")
def estate() -> DemoEstate:
    return build_estate("small")


@pytest.fixture
async def seeded(client: AsyncClient, estate: DemoEstate) -> DemoEstate:
    await seed_transcripts(client, build_transcripts(estate))
    return estate


# --------------------------------------------------------------------------------------
# The lifecycle, through storage
# --------------------------------------------------------------------------------------


class TestTheAlertLifecycle:
    """open, dedupe, repeat, resolve, reopen -- the five cases, against real columns."""

    async def test_a_new_alert_opens_and_records_a_raised_event(
        self, session: AsyncSession
    ) -> None:
        repository = AlertRepository(session)

        result = await repository.record(
            _event(payload={"action": "added"}), now=NOW, cooldown=COOLDOWN
        )

        assert result.transition is AlertTransition.RAISED
        assert result.notified
        stored = await repository.get(result.alert_key)
        assert stored is not None
        assert stored.status is AlertStatus.OPEN
        assert stored.delivered_digest == stored.payload_digest
        assert stored.occurrence_count == 1
        assert stored.suppressed_total == 0

    async def test_the_same_content_again_is_deduplicated_and_still_recorded(
        self, session: AsyncSession
    ) -> None:
        """Detection ran twice. The alert must not be delivered twice -- and the second
        occurrence must still leave a row, so a cooldown and a quiet estate stay separable."""
        repository = AlertRepository(session)
        payload = {"action": "added", "trustee": "Alice"}
        first = await repository.record(_event(payload=payload), now=NOW, cooldown=COOLDOWN)
        second = await repository.record(
            _event(payload=payload, moment=at(hours=6)), now=at(hours=6), cooldown=COOLDOWN
        )

        assert second.alert_key == first.alert_key
        assert second.transition is AlertTransition.SUPPRESSED
        assert second.decision.reason is SuppressionReason.IDENTICAL_CONTENT

        events = await repository.events_for(first.alert_key)
        assert len(events) == 2, "a suppressed occurrence must still be written down"
        assert {row["transition"] for row in events} == {"raised", "suppressed"}

        stored = await repository.get(first.alert_key)
        assert stored is not None
        assert stored.occurrence_count == 2
        assert stored.suppressed_total == 1

    async def test_a_repeat_inside_the_cooldown_is_held_and_the_count_reaches_the_delivery(
        self, session: AsyncSession
    ) -> None:
        repository = AlertRepository(session)
        first = await repository.record(_event(payload={"n": 0}), now=NOW, cooldown=COOLDOWN)
        for index in range(1, 4):
            await repository.record(
                _event(payload={"n": index}, moment=at(minutes=index)),
                now=at(minutes=index),
                cooldown=COOLDOWN,
            )

        escaped = await repository.record(
            _event(payload={"n": 9}, moment=at(minutes=30)),
            now=at(minutes=30),
            cooldown=COOLDOWN,
        )

        assert escaped.transition is AlertTransition.REPEATED
        assert escaped.decision.folds == 4, "the three held occurrences plus this one"
        stored = await repository.get(first.alert_key)
        assert stored is not None
        assert stored.suppressed_since_notice == 0, "a notification clears the fold counter"
        assert stored.suppressed_total == 3

    async def test_a_suppression_does_not_move_the_delivered_digest(
        self, session: AsyncSession
    ) -> None:
        """If it did, the deduplication would be inverted.

        The next occurrence of the content an operator was actually shown would compare
        unequal and read as new, so a repeating change would produce noise about exactly the
        thing that had already been reported.
        """
        repository = AlertRepository(session)
        first = await repository.record(_event(payload={"n": 0}), now=NOW, cooldown=COOLDOWN)
        delivered = (await repository.get(first.alert_key)).delivered_digest  # type: ignore[union-attr]

        await repository.record(
            _event(payload={"n": 1}, moment=at(minutes=1)), now=at(minutes=1), cooldown=COOLDOWN
        )
        after = await repository.get(first.alert_key)

        assert after is not None
        assert after.delivered_digest == delivered
        assert after.payload_digest != delivered

    async def test_a_stateful_alert_resolves_and_reopens_with_its_history_intact(
        self, session: AsyncSession
    ) -> None:
        repository = AlertRepository(session)
        opened = await repository.record(
            _event(
                payload={"finding": "x"},
                trigger=AlertTrigger.CRITICAL_RISK_FINDING_OPENED,
                discriminator="f" * 64,
                watch_id=None,
            ),
            now=NOW,
            cooldown=COOLDOWN,
        )

        resolved = await repository.resolve(opened.alert_key, now=at(days=1))
        assert resolved is not None
        closed = await repository.get(opened.alert_key)
        assert closed is not None
        assert closed.status is AlertStatus.RESOLVED
        assert closed.resolved_at == at(days=1)

        reopened = await repository.record(
            _event(
                payload={"finding": "x"},
                moment=at(days=30),
                trigger=AlertTrigger.CRITICAL_RISK_FINDING_OPENED,
                discriminator="f" * 64,
                watch_id=None,
            ),
            now=at(days=30),
            cooldown=COOLDOWN,
        )

        assert reopened.transition is AlertTransition.REOPENED
        again = await repository.get(opened.alert_key)
        assert again is not None
        assert again.status is AlertStatus.OPEN
        assert again.resolved_at is None
        assert again.first_raised_at == NOW, "the original sighting is never rewritten"

        events = await repository.events_for(opened.alert_key)
        assert [row["transition"] for row in reversed(events)] == [
            "raised",
            "resolved",
            "reopened",
        ]

    async def test_a_transient_alert_cannot_be_resolved(self, session: AsyncSession) -> None:
        """An access control list edit cannot un-happen.

        The database says so too: ``ck_alerts_transient_never_resolves``. This checks the
        application refuses before the constraint has to.
        """
        repository = AlertRepository(session)
        raised = await repository.record(
            _event(payload={"action": "added"}), now=NOW, cooldown=COOLDOWN
        )

        assert await repository.resolve(raised.alert_key, now=at(days=1)) is None
        stored = await repository.get(raised.alert_key)
        assert stored is not None
        assert stored.status is AlertStatus.OPEN

    async def test_a_disabled_watch_records_what_it_missed(self, session: AsyncSession) -> None:
        repository = AlertRepository(session)

        result = await repository.record(
            _event(payload={"action": "added"}),
            now=NOW,
            cooldown=COOLDOWN,
            watch_enabled=False,
        )

        assert not result.notified
        events = await repository.events_for(result.alert_key)
        assert events[0]["suppression_reason"] == SuppressionReason.WATCH_DISABLED.value

    async def test_last_raised_at_never_moves_backwards(self, session: AsyncSession) -> None:
        """An overlapping detection window legitimately re-reads an older change.

        Letting it rewind the column would break ``first_raised_at <= last_raised_at`` and,
        before that, would make an alert look newer or older than it is in a sorted feed.
        """
        repository = AlertRepository(session)
        latest = await repository.record(
            _event(payload={"n": 1}, moment=at(hours=2)), now=at(hours=2), cooldown=COOLDOWN
        )
        await repository.record(
            _event(payload={"n": 2}, moment=NOW), now=at(hours=3), cooldown=COOLDOWN
        )

        stored = await repository.get(latest.alert_key)
        assert stored is not None
        assert stored.last_raised_at == at(hours=2)


# --------------------------------------------------------------------------------------
# The outbox
# --------------------------------------------------------------------------------------


class TestTheOutbox:
    def _envelope(self, *, sink: str = "log", event_id: Any = None) -> DeliveryEnvelope:
        return DeliveryEnvelope(
            event_id=event_id or uuid4(),
            alert_key="a" * 64,
            trigger=AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED,
            transition=AlertTransition.RAISED,
            sink_name=sink,
            occurred_at=NOW,
            summary="Something changed.",
            payload={"action": "added"},
        )

    async def test_enqueueing_the_same_envelope_twice_records_one_row(
        self, session: AsyncSession
    ) -> None:
        """At the unique constraint, not merely in the in-memory implementation.

        This is what makes a retried scan completion safe: it re-raises the alert, which
        re-enqueues, and the recipient must not be told twice.
        """
        outbox = SqlAlertOutbox(session)
        envelope = self._envelope()

        assert await outbox.enqueue([envelope], now=NOW) == 1
        assert await outbox.enqueue([envelope], now=at(minutes=5)) == 0

        total = (
            await session.execute(sa.select(sa.func.count()).select_from(alert_deliveries))
        ).scalar_one()
        assert total == 1

    async def test_the_envelope_is_stored_rather_than_rebuilt_at_delivery_time(
        self, session: AsyncSession
    ) -> None:
        """A retry next Tuesday must send what the alert said when it was raised.

        Rebuilding it would send what the estate looks like when the retry happens, which is
        a different claim wearing the original's timestamp.
        """
        outbox = SqlAlertOutbox(session)
        await outbox.enqueue([self._envelope()], now=NOW)

        claimed = await outbox.claim(now=NOW, limit=10)

        assert len(claimed) == 1
        assert claimed[0].envelope.payload == {"action": "added"}
        assert claimed[0].envelope.occurred_at == NOW

    async def test_a_failure_schedules_a_retry_and_a_claim_respects_it(
        self, session: AsyncSession
    ) -> None:
        outbox = SqlAlertOutbox(session)
        await outbox.enqueue([self._envelope()], now=NOW)
        claimed = await outbox.claim(now=NOW, limit=10)
        await outbox.record_failure(
            claimed[0].delivery_id, now=NOW, error="HTTP 503", retry_at=at(minutes=1)
        )

        assert await outbox.claim(now=at(seconds=30), limit=10) == ()
        assert len(await outbox.claim(now=at(minutes=2), limit=10)) == 1

    async def test_abandoning_keeps_the_row_and_the_reason(self, session: AsyncSession) -> None:
        """An abandoned delivery is an alert nobody received. Deleting it would make that
        indistinguishable from an alert that was never raised."""
        outbox = SqlAlertOutbox(session)
        await outbox.enqueue([self._envelope()], now=NOW)
        claimed = await outbox.claim(now=NOW, limit=10)
        await outbox.record_failure(
            claimed[0].delivery_id, now=NOW, error="HTTP 404 not found", retry_at=None
        )

        depth = await outbox.depth(now=NOW)
        assert depth[DeliveryStatus.ABANDONED] == 1
        row = (await session.execute(sa.select(alert_deliveries))).one()
        assert row.last_error is not None

    async def test_staleness_is_separable_from_depth(self, session: AsyncSession) -> None:
        """Depth alone cannot distinguish a busy pipeline from one nothing is draining."""
        outbox = SqlAlertOutbox(session)
        await outbox.enqueue([self._envelope()], now=NOW)

        assert await outbox.pending_older_than(at(minutes=-1)) == 0
        assert await outbox.pending_older_than(at(minutes=30)) == 1

    async def test_a_successful_delivery_clears_an_earlier_attempts_error(
        self, session: AsyncSession
    ) -> None:
        """A delivered row carrying an old error reads, in a list, as one that failed."""
        outbox = SqlAlertOutbox(session)
        await outbox.enqueue([self._envelope()], now=NOW)
        claimed = await outbox.claim(now=NOW, limit=10)
        await outbox.record_failure(
            claimed[0].delivery_id, now=NOW, error="HTTP 503", retry_at=at(minutes=1)
        )
        await outbox.record_success(claimed[0].delivery_id, now=at(minutes=2))

        row = (await session.execute(sa.select(alert_deliveries))).one()
        assert row.status == DeliveryStatus.DELIVERED.value
        assert row.last_error is None
        assert row.attempts == 2


# --------------------------------------------------------------------------------------
# Watches
# --------------------------------------------------------------------------------------


class TestWatchStorage:
    def _watch(self, **overrides: Any) -> Watch:
        defaults: dict[str, Any] = {
            "watch_id": uuid4(),
            "kind": WatchKind.RESOURCE,
            "key": FINANCE,
            "label": "Finance directory",
            "triggers": frozenset({AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED}),
            "cooldown": COOLDOWN,
            "created_at": NOW,
            "updated_at": NOW,
            "created_by": "tester",
        }
        return Watch(**{**defaults, **overrides})

    async def test_a_key_is_folded_on_the_way_in(self, session: AsyncSession) -> None:
        r"""So that a watch typed ``\\FS01\Finance`` matches the resource key.

        Folding only at comparison time would work until somebody wrote a comparison without
        it, and the watch would then be silently inert -- which reads, in a feed, as a quiet
        directory.
        """
        repository = WatchRepository(session)
        await repository.create(self._watch(key=r"\\FS01\Finance"), actor="tester")

        found = await repository.for_target(WatchKind.RESOURCE, FINANCE)

        assert found is not None
        assert found.key == FINANCE

    async def test_a_second_watch_on_one_thing_is_refused(self, session: AsyncSession) -> None:
        repository = WatchRepository(session)
        await repository.create(self._watch(), actor="tester")

        with pytest.raises(WatchConflict) as error:
            await repository.create(self._watch(), actor="tester")

        assert "already exists" in str(error.value)

    async def test_deleting_a_watch_keeps_the_alerts_it_raised(self, session: AsyncSession) -> None:
        """An alert is the record that somebody was told something.

        Deleting the subscription does not un-tell them, so nothing cascades.
        """
        watches = WatchRepository(session)
        alerts = AlertRepository(session)
        watch = await watches.create(self._watch(), actor="tester")
        raised = await alerts.record(
            _event(payload={"action": "added"}, watch_id=watch.watch_id),
            now=NOW,
            cooldown=COOLDOWN,
            watch_label=watch.label,
        )

        assert await watches.delete(watch.watch_id)

        surviving = await alerts.get(raised.alert_key)
        assert surviving is not None
        assert surviving.watch_id == watch.watch_id
        assert surviving.watch_label == "Finance directory"

    async def test_only_enabled_watches_are_offered_to_a_detection_pass(
        self, session: AsyncSession
    ) -> None:
        repository = WatchRepository(session)
        await repository.create(self._watch(), actor="tester")
        await repository.create(self._watch(key=r"\\fs01\public", enabled=False), actor="tester")

        assert len(await repository.list()) == 2
        assert len(await repository.enabled()) == 1


# --------------------------------------------------------------------------------------
# Delivery failure does not roll back the source
# --------------------------------------------------------------------------------------


class _FailingSink:
    name = "failing"

    def __init__(self) -> None:
        self.attempts = 0

    async def deliver(self, envelope: DeliveryEnvelope) -> DeliveryResult:
        self.attempts += 1
        return DeliveryResult.transient("the endpoint is down")


class TestDeliveryFailureIsContained:
    async def test_a_failing_sink_leaves_the_alert_and_its_event_intact(
        self, session: AsyncSession
    ) -> None:
        """The acceptance criterion, at the smallest scale it can be checked at.

        The alert is written and the delivery queued in one transaction; the attempt is a
        separate pass. A sink that fails therefore leaves a durable alert, a durable event,
        and a delivery row with a retry time on it -- and nothing upstream is undone.
        """
        repository = AlertRepository(session)
        outbox = SqlAlertOutbox(session)
        raised = await repository.record(
            _event(payload={"action": "added"}), now=NOW, cooldown=COOLDOWN
        )
        await outbox.enqueue(
            [
                DeliveryEnvelope(
                    event_id=raised.event_id,
                    alert_key=raised.alert_key,
                    trigger=raised.trigger,
                    transition=raised.transition,
                    sink_name="failing",
                    occurred_at=NOW,
                    summary=raised.summary,
                    payload=dict(raised.payload),
                )
            ],
            now=NOW,
        )
        await session.commit()

        sink = _FailingSink()
        report = await AlertDispatcher(
            outbox, {sink.name: sink}, retry=RetrySchedule(max_attempts=3)
        ).drain(now=NOW)
        await session.commit()

        assert report.retrying == 1
        assert await repository.get(raised.alert_key) is not None
        assert len(await repository.events_for(raised.alert_key)) == 1
        queued = (await session.execute(sa.select(alert_deliveries))).one()
        assert queued.status == DeliveryStatus.FAILED.value

    async def test_a_scan_run_completes_even_when_the_alert_pipeline_raises(
        self, client: AsyncClient, estate: DemoEstate, monkeypatch: Any
    ) -> None:
        """The acceptance criterion at full scale, through the ingestion endpoints.

        The post-completion hook is replaced with one that raises outright -- not a failing
        sink, which the hook handles, but a defect in the hook itself. The completion must
        still return 200 and the observations must still be applied, because the hook runs
        after the ingestion transaction has committed and the route refuses to let anything
        it does become the collector's problem.

        Without the guard at the call site, this would be a 500 for a run that had already
        been recorded, and the collector would retry it.
        """
        import app.api.scan_runs as scan_runs

        async def explode(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("the alert pipeline is broken")

        monkeypatch.setattr(scan_runs, "evaluate_run_after_commit", explode)

        await seed_transcripts(client, build_transcripts(estate))

        status = (await client.get("/api/v1/collection/status")).json()
        assert status["health"] != "no_data", "the runs were recorded despite the broken hook"
        assert status["collectors"], "every collector's coverage is there"
        servers = (await client.get("/api/v1/servers")).json()
        assert servers["items"], "the observations the runs reported are durable"


# --------------------------------------------------------------------------------------
# The post-run hook, when an installation turns it on
# --------------------------------------------------------------------------------------


class TestThePostRunHook:
    """It is opt-in, and being off is never silent.

    An installation that has not enabled it and has not scheduled
    ``python -m app.operations evaluate-risks`` gets "the rules have not been evaluated"
    from the risk report -- never a clean-looking empty one.
    """

    async def test_it_is_off_by_default_and_says_so(self, db_settings: Any) -> None:
        from app.services.alerts import evaluate_run_after_commit

        outcome = await evaluate_run_after_commit(
            None, db_settings, run_id=uuid4(), window_from=NOW
        )

        assert not outcome.ran
        assert "disabled" in outcome.summary

    async def test_when_enabled_a_completed_run_evaluates_the_rules_and_alerts(
        self, migrated_database: str, estate: DemoEstate
    ) -> None:
        """The full loop: a collector closes a run, the rules run, a finding opens, an alert
        is raised, and a delivery is queued and attempted -- all after the commit."""
        from httpx import ASGITransport
        from httpx import AsyncClient as HttpClient

        from app.config import build_settings
        from app.db import Database
        from app.main import create_app
        from tests.support.auth import auth_headers

        settings = build_settings(
            environment="test",
            log_format="text",
            database_url=migrated_database,
            alerts_on_run_completion=True,
        )
        database = Database(settings)
        app = create_app(settings)
        app.state.database = database
        try:
            async with HttpClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers=auth_headers(settings),
            ) as http:
                await seed_transcripts(http, build_transcripts(estate))

                summary = (await http.get("/api/v1/risks/summary")).json()
                assert summary["coverage"]["has_ever_run"] is True
                assert summary["coverage"]["trigger"] == "incremental"
                assert summary["total"] > 0

                feed = (await http.get("/api/v1/alerts")).json()
                assert feed["items"], "a critical finding should have raised an alert"
                assert all(
                    item["trigger"] == "critical_risk_finding_opened" for item in feed["items"]
                )

                queue = (await http.get("/api/v1/alerts/deliveries")).json()
                assert queue["depth"]["delivered"] > 0, "the log sink should have taken them"
                assert queue["abandoned"] == 0
        finally:
            await database.dispose()

    async def test_an_incremental_pass_reports_that_its_counts_are_not_totals(
        self, migrated_database: str, estate: DemoEstate
    ) -> None:
        """The honesty this phase exists for, at the point it matters most.

        The hook runs an *incremental* evaluation, so the report it produces is what ADG
        currently holds rather than what the estate contains. A dashboard that rendered those
        counts as totals would report a partially examined estate as a fully examined one.
        """
        from httpx import ASGITransport
        from httpx import AsyncClient as HttpClient

        from app.config import build_settings
        from app.db import Database
        from app.main import create_app
        from tests.support.auth import auth_headers

        settings = build_settings(
            environment="test",
            log_format="text",
            database_url=migrated_database,
            alerts_on_run_completion=True,
        )
        database = Database(settings)
        app = create_app(settings)
        app.state.database = database
        try:
            async with HttpClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers=auth_headers(settings),
            ) as http:
                await seed_transcripts(http, build_transcripts(estate))

                coverage = (await http.get("/api/v1/risks/summary")).json()["coverage"]

                assert coverage["has_ever_run"] is True
                assert coverage["complete"] is False
        finally:
            await database.dispose()


# --------------------------------------------------------------------------------------
# Findings to alerts, end to end
# --------------------------------------------------------------------------------------


class TestFindingsBecomeAlerts:
    async def test_a_critical_finding_raises_an_alert_with_no_watch_configured(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        evaluation = await RiskService(
            RiskFactsRepository(session),
            RiskFindingRepository(session),
            DEFAULT_CONFIGURATION,
        ).evaluate_estate(now=NOW)
        opened = [
            finding
            for finding in evaluation.findings
            if finding.key in set(evaluation.outcome.opened)
        ]
        assert opened, "the demo estate should open findings"

        result = await AlertService(session, AlertPolicy()).evaluate_findings(
            opened=opened, now=NOW
        )

        assert result.recorded, "at least one critical finding should have raised an alert"
        for recorded in result.recorded:
            assert recorded.trigger is AlertTrigger.CRITICAL_RISK_FINDING_OPENED
            assert recorded.watch_id is None

    async def test_the_threshold_decides_which_findings_alert(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """Severity is the risk engine's vocabulary; the threshold is the policy's.

        Lowering it must widen the set rather than change any finding's grading.
        """
        evaluation = await RiskService(
            RiskFactsRepository(session),
            RiskFindingRepository(session),
            DEFAULT_CONFIGURATION,
        ).evaluate_estate(now=NOW)
        opened = list(evaluation.findings)

        critical = await AlertService(
            session, AlertPolicy(finding_severity_threshold="critical")
        ).evaluate_findings(opened=opened, now=NOW)
        everything = await AlertService(
            session, AlertPolicy(finding_severity_threshold="informational")
        ).evaluate_findings(opened=opened, now=at(days=1))

        assert len(everything.recorded) > len(critical.recorded)

    async def test_resolving_a_finding_closes_the_alert_it_opened(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """Derived from the finding rather than looked up by payload.

        A lookup would be a second implementation of the alert key, and the day the two
        disagreed the resolution would close nothing -- leaving an operator acting on an
        exposure that is gone.
        """
        evaluation = await RiskService(
            RiskFactsRepository(session),
            RiskFindingRepository(session),
            DEFAULT_CONFIGURATION,
        ).evaluate_estate(now=NOW)
        critical = [
            finding for finding in evaluation.findings if finding.severity.value == "critical"
        ]
        assert critical, "the demo estate should produce a critical finding"

        service = AlertService(session, AlertPolicy())
        raised = await service.evaluate_findings(opened=critical[:1], now=NOW)
        assert raised.recorded

        closed = await service.evaluate_findings(
            opened=[], resolved_keys=[critical[0].key], now=at(days=1)
        )

        assert closed.resolved == (raised.recorded[0].alert_key,)
        stored = await AlertRepository(session).get(raised.recorded[0].alert_key)
        assert stored is not None
        assert stored.status is AlertStatus.RESOLVED

    async def test_the_notice_carries_the_catalog_title_rather_than_the_rule_identifier(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """``broad_write_on_sensitive_resource`` is a grep target.

        What somebody reads at two in the morning is the catalog's sentence.
        """
        evaluation = await RiskService(
            RiskFactsRepository(session),
            RiskFindingRepository(session),
            DEFAULT_CONFIGURATION,
        ).evaluate_estate(now=NOW)

        notice = notice_for_finding(evaluation.findings[0], at=NOW)

        assert notice.title != notice.rule_id
        assert " " in notice.title


# --------------------------------------------------------------------------------------
# The HTTP surface
# --------------------------------------------------------------------------------------


class TestTheWatchApi:
    async def test_a_watch_can_be_created_listed_edited_and_deleted(
        self, client: AsyncClient
    ) -> None:
        created = await client.post(
            "/api/v1/alerts/watches",
            json={
                "kind": "resource",
                "key": FINANCE,
                "label": "Finance directory",
                "triggers": ["watched_resource_acl_changed"],
            },
        )
        assert created.status_code == 201, created.text
        watch_id = created.json()["watch_id"]

        listed = await client.get("/api/v1/alerts/watches")
        assert listed.status_code == 200
        assert [item["label"] for item in listed.json()["watches"]] == ["Finance directory"]

        edited = await client.patch(f"/api/v1/alerts/watches/{watch_id}", json={"enabled": False})
        assert edited.status_code == 200
        assert edited.json()["enabled"] is False

        removed = await client.delete(f"/api/v1/alerts/watches/{watch_id}")
        assert removed.status_code == 204
        assert (await client.get("/api/v1/alerts/watches")).json()["watches"] == []

    async def test_the_listing_serves_the_table_of_what_each_kind_supports(
        self, client: AsyncClient
    ) -> None:
        """So a client does not hold a second copy of a rule it could disagree with."""
        body = (await client.get("/api/v1/alerts/watches")).json()

        kinds = {entry["kind"]: entry["triggers"] for entry in body["kinds"]}
        assert kinds["group"] == ["watched_group_membership_changed"]
        assert "watched_resource_acl_changed" in kinds["resource"]

    async def test_a_trigger_the_kind_could_never_fire_is_refused(
        self, client: AsyncClient
    ) -> None:
        """Saved, it would be configured, listed in the interface, and silent forever."""
        response = await client.post(
            "/api/v1/alerts/watches",
            json={
                "kind": "resource",
                "key": FINANCE,
                "label": "Finance",
                "triggers": ["watched_group_membership_changed"],
            },
        )

        assert response.status_code == 422
        assert "watched_resource_acl_changed" in response.text

    async def test_a_duplicate_watch_is_a_conflict_naming_the_existing_one(
        self, client: AsyncClient
    ) -> None:
        body = {
            "kind": "resource",
            "key": FINANCE,
            "label": "Finance",
            "triggers": ["watched_resource_acl_changed"],
        }
        await client.post("/api/v1/alerts/watches", json=body)

        response = await client.post("/api/v1/alerts/watches", json=body)

        assert response.status_code == 409
        assert "already exists" in response.text

    async def test_a_reader_cannot_configure_a_watch(self, client_as: Any) -> None:
        """Somebody who could quietly disable the watch on the payroll share could make an
        exposure land in nobody's inbox."""
        async with client_as(Role.VIEWER) as viewer:
            listed = await viewer.get("/api/v1/alerts/watches")
            created = await viewer.post(
                "/api/v1/alerts/watches",
                json={
                    "kind": "resource",
                    "key": FINANCE,
                    "label": "Finance",
                    "triggers": ["watched_resource_acl_changed"],
                },
            )

        assert listed.status_code == 200
        assert created.status_code == 403


class TestOneBadWatchCannotSilenceTheEstate:
    """Two defects the end-to-end run found, and the second is the serious one.

    A watch whose key the change feed cannot scope on raised out of the per-watch loop. That
    killed the whole detection pass -- and because the pass's failure returned early, it also
    skipped the **finding** half, which is the one trigger that fires estate-wide with no
    watch behind it. One misconfigured watch therefore silenced every critical finding in the
    estate, leaving a log line and a feed that looked quiet.

    Caught by running the feature end to end against a real database, not by the suite: every
    test until then had used a well-formed key.
    """

    async def test_an_unscopeable_key_is_refused_at_creation(self, client: AsyncClient) -> None:
        """The first line of defense: never store one.

        A watch with a key nothing can scope is accepted by every other validator, saved,
        listed in the interface, and silent forever -- which is the exact shape this phase
        refuses for triggers, applied to the other half of a watch's identity.
        """
        response = await client.post(
            "/api/v1/alerts/watches",
            json={
                "kind": "resource",
                # One leading separator rather than two: not a UNC path, and exactly
                # the shape a JSON escape or a shell heredoc makes of a correct one.
                "key": r"\fs01\finance",
                "label": "Finance",
                "triggers": ["watched_resource_acl_changed"],
            },
        )

        assert response.status_code == 422
        assert "silent forever" in response.text

    async def test_a_watch_that_cannot_be_scoped_does_not_stop_the_pass(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """The second line: one bad watch is skipped and reported, not fatal.

        Written directly through the repository, which is how such a row can still exist --
        it predates the creation check, or it was inserted by hand.
        """
        broken = Watch(
            watch_id=uuid4(),
            kind=WatchKind.RESOURCE,
            key="not-a-unc-path",
            label="Broken",
            triggers=frozenset({AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED}),
            cooldown=COOLDOWN,
            created_at=NOW,
            updated_at=NOW,
            created_by="tester",
        )
        await WatchRepository(session).create(broken, actor="tester")

        result = await AlertService(session, AlertPolicy()).evaluate_window(
            window_from=NOW - dt.timedelta(days=1), window_to=NOW
        )

        assert result.watches_considered == 1
        assert not result.complete, "a skipped watch must show up as incomplete coverage"
        assert any("could not be matched" in note for note in result.truncated)

    async def test_a_broken_watch_does_not_silence_the_estate_wide_finding_alerts(
        self, client: AsyncClient, estate: DemoEstate, migrated_database: str
    ) -> None:
        """The defect itself, end to end.

        A watch that cannot be scoped exists; a scan run completes with the inline hook on;
        critical findings open. The alerts about those findings need no watch at all, so a
        broken one must not be able to stop them.
        """
        from httpx import ASGITransport
        from httpx import AsyncClient as HttpClient

        from app.config import build_settings
        from app.db import Database
        from app.main import create_app
        from tests.support.auth import auth_headers

        settings = build_settings(
            environment="test",
            log_format="text",
            database_url=migrated_database,
            alerts_on_run_completion=True,
        )
        database = Database(settings)
        app = create_app(settings)
        app.state.database = database
        try:
            async with database.session() as setup:
                await WatchRepository(setup).create(
                    Watch(
                        watch_id=uuid4(),
                        kind=WatchKind.RESOURCE,
                        key="not-a-unc-path",
                        label="Broken",
                        triggers=frozenset({AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED}),
                        cooldown=COOLDOWN,
                        created_at=NOW,
                        updated_at=NOW,
                        created_by="tester",
                    ),
                    actor="tester",
                )
                await setup.commit()

            async with HttpClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers=auth_headers(settings),
            ) as http:
                await seed_transcripts(http, build_transcripts(estate))

                feed = (await http.get("/api/v1/alerts")).json()

            assert feed["items"], (
                "a watch nothing can scope must not stop the estate-wide finding alerts"
            )
            assert all(item["trigger"] == "critical_risk_finding_opened" for item in feed["items"])
        finally:
            await database.dispose()


class TestTheAlertFeedApi:
    async def test_it_shows_suppressed_occurrences_with_their_reason(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Without them, "why was I not told about the other four" has no answer, and a
        cooldown becomes indistinguishable from a pipeline that dropped them."""
        repository = AlertRepository(session)
        raised = await repository.record(_event(payload={"n": 0}), now=NOW, cooldown=COOLDOWN)
        await repository.record(
            _event(payload={"n": 1}, moment=at(minutes=1)), now=at(minutes=1), cooldown=COOLDOWN
        )
        await session.commit()

        response = await client.get(f"/api/v1/alerts/{raised.alert_key}")

        assert response.status_code == 200
        body = response.json()
        transitions = [event["transition"] for event in body["events"]]
        assert "suppressed" in transitions
        held = next(event for event in body["events"] if event["transition"] == "suppressed")
        assert held["suppression_reason"] == "within_cooldown"
        assert held["notified"] is False

    async def test_the_feed_counts_every_trigger_including_the_empty_ones(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A facet list that omitted them would make a feed with no membership alerts look
        like a feed that has no membership category."""
        await AlertRepository(session).record(_event(payload={"n": 0}), now=NOW, cooldown=COOLDOWN)
        await session.commit()

        body = (await client.get("/api/v1/alerts")).json()

        assert set(body["trigger_counts"]) == {trigger.value for trigger in AlertTrigger}
        assert body["trigger_counts"]["watched_group_membership_changed"] == 0

    async def test_the_queue_reports_depth_staleness_abandonment_and_the_policy(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/alerts/deliveries")

        assert response.status_code == 200
        body = response.json()
        assert set(body["depth"]) == {status.value for status in DeliveryStatus}
        assert "stale" in body and "abandoned" in body
        assert any("operator-log" in line for line in body["policy"])

    async def test_an_unknown_filter_value_is_refused_rather_than_ignored(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/alerts", params={"trigger": "made_up"})

        assert response.status_code == 422


class TestTheRiskReportApi:
    async def test_an_estate_nobody_has_evaluated_says_so(self, client: AsyncClient) -> None:
        """The single most important response in this phase.

        An empty findings list with no caveat reads as a clean estate. ``has_ever_run``
        false says the rules have never looked.
        """
        body = (await client.get("/api/v1/risks/summary")).json()

        assert body["coverage"]["has_ever_run"] is False
        assert body["coverage"]["complete"] is False
        assert body["total"] == 0

    async def test_a_full_pass_makes_the_counts_totals(
        self, seeded: DemoEstate, session: AsyncSession, client: AsyncClient
    ) -> None:
        await RiskService(
            RiskFactsRepository(session),
            RiskFindingRepository(session),
            DEFAULT_CONFIGURATION,
        ).evaluate_estate(now=NOW)
        await session.commit()

        body = (await client.get("/api/v1/risks/summary")).json()

        assert body["coverage"]["has_ever_run"] is True
        assert body["coverage"]["complete"] is True
        assert body["coverage"]["trigger"] == "full"
        assert body["total"] > 0
        assert set(body["severity_counts"]) == {
            "informational",
            "low",
            "medium",
            "high",
            "critical",
        }

    async def test_the_listing_reports_how_many_findings_the_default_filter_hides(
        self, seeded: DemoEstate, session: AsyncSession, client: AsyncClient
    ) -> None:
        await RiskService(
            RiskFactsRepository(session),
            RiskFindingRepository(session),
            DEFAULT_CONFIGURATION,
        ).evaluate_estate(now=NOW)
        await session.commit()

        body = (await client.get("/api/v1/risks/findings")).json()

        assert body["filters"]["status"] == ["open"]
        assert set(body["status_counts"]) == {"open", "resolved"}
        assert body["status_counts"]["open"] == body["page"]["total"]

    async def test_the_detail_route_carries_the_records_and_says_whether_they_reproduce(
        self, seeded: DemoEstate, session: AsyncSession, client: AsyncClient
    ) -> None:
        """The evidence drawer as an audit artifact rather than a restatement.

        ``"Everyone -> Modify"`` is a sentence; it cannot be rebuilt into facts. The whole
        records can, and ``reproduces`` says that they were.
        """
        evaluation = await RiskService(
            RiskFactsRepository(session),
            RiskFindingRepository(session),
            DEFAULT_CONFIGURATION,
        ).evaluate_estate(now=NOW)
        await session.commit()

        body = (await client.get(f"/api/v1/risks/findings/{evaluation.findings[0].key}")).json()

        assert body["evidence"], "a finding must cite the records it matched on"
        assert all(item["record"] for item in body["evidence"])
        assert body["reproduces"] is True
        assert body["rule"]["remediation"]["summary"]
        assert body["events"][0]["event_type"] == "opened"

    async def test_the_rules_route_lists_the_disabled_ones_too(self, client: AsyncClient) -> None:
        """A catalog of only what is enabled would make an installation with the interesting
        rules off look identical to a healthy one."""
        body = (await client.get("/api/v1/risks/rules")).json()

        assert len(body["rules"]) >= 11
        assert "rules_disabled" in body["configuration"]
        assert body["configuration"]["marks_anything_sensitive"] is False

    async def test_an_unknown_severity_is_refused(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/risks/findings", params={"severity": "critcal"})

        assert response.status_code == 422
        assert "Refused rather than ignored" in response.text
