"""The suppression decision: open, dedupe, repeat, resolve, reopen.

The five cases the prompt names, plus the ones that make them mean something — a suppression
that is recorded rather than dropped, a cooldown measured from the last *notification*, and
the two things that are never suppressed.

Every test here is microseconds and needs no database, which is the point of keeping the
decision pure: the interesting branches are exactly the ones that are hard to stage through
an ingestion.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.alerts import (
    AlertLifecycle,
    AlertState,
    AlertStatus,
    AlertTransition,
    SuppressionReason,
    decide_raise,
    decide_resolve,
)
from tests.alerts.support import at, event

COOLDOWN = dt.timedelta(minutes=15)


def _state(
    *,
    status: AlertStatus = AlertStatus.OPEN,
    lifecycle: AlertLifecycle = AlertLifecycle.TRANSIENT,
    delivered_digest: str | None = None,
    last_notified_at: dt.datetime | None = None,
    suppressed: int = 0,
) -> AlertState:
    return AlertState(
        key="a" * 64,
        status=status,
        lifecycle=lifecycle,
        delivered_digest=delivered_digest,
        last_notified_at=last_notified_at,
        suppressed_since_notice=suppressed,
    )


class TestTheFiveCases:
    """open, dedupe, repeat, resolve, reopen."""

    def test_an_alert_nobody_has_seen_is_raised(self) -> None:
        decision = decide_raise(event(), None, cooldown=COOLDOWN)

        assert decision.transition is AlertTransition.RAISED
        assert decision.notifies
        assert decision.reason is None
        assert decision.folds == 1

    def test_identical_content_is_a_duplicate_and_never_notifies_twice(self) -> None:
        """Detection ran twice -- a retried completion, an overlapping window.

        Matched on the payload digest rather than on a timestamp, so it holds however far
        apart the two passes ran. This is the case that makes overlapping detection windows a
        safe default rather than a source of noise.
        """
        first = event(payload={"action": "added", "trustee": "Alice"})
        again = event(payload={"action": "added", "trustee": "Alice"}, moment=at(hours=6))
        assert first.digest == again.digest

        decision = decide_raise(
            again,
            _state(delivered_digest=first.digest, last_notified_at=at()),
            cooldown=COOLDOWN,
        )

        assert decision.transition is AlertTransition.SUPPRESSED
        assert decision.reason is SuppressionReason.IDENTICAL_CONTENT
        assert not decision.notifies

    def test_different_content_outside_the_cooldown_is_a_repeat_and_notifies(self) -> None:
        decision = decide_raise(
            event(payload={"action": "removed"}, moment=at(minutes=20)),
            _state(delivered_digest="0" * 64, last_notified_at=at()),
            cooldown=COOLDOWN,
        )

        assert decision.transition is AlertTransition.REPEATED
        assert decision.notifies

    def test_a_stateful_alert_resolves(self) -> None:
        decision = decide_resolve(_state(lifecycle=AlertLifecycle.STATEFUL))

        assert decision is not None
        assert decision.transition is AlertTransition.RESOLVED
        assert decision.notifies

    def test_a_resolved_alert_becoming_true_again_reopens(self) -> None:
        decision = decide_raise(
            event(moment=at(days=30)),
            _state(
                status=AlertStatus.RESOLVED,
                lifecycle=AlertLifecycle.STATEFUL,
                delivered_digest="0" * 64,
                last_notified_at=at(),
            ),
            cooldown=COOLDOWN,
        )

        assert decision.transition is AlertTransition.REOPENED
        assert decision.notifies


class TestTheCooldown:
    def test_a_repeat_inside_the_window_is_suppressed(self) -> None:
        decision = decide_raise(
            event(payload={"action": "removed"}, moment=at(minutes=5)),
            _state(delivered_digest="0" * 64, last_notified_at=at()),
            cooldown=COOLDOWN,
        )

        assert decision.transition is AlertTransition.SUPPRESSED
        assert decision.reason is SuppressionReason.WITHIN_COOLDOWN
        assert decision.cooldown_until == at(minutes=15)

    def test_a_suppressed_occurrence_is_counted_and_the_count_reaches_the_next_delivery(
        self,
    ) -> None:
        """The whole point of a cooldown being recoverable.

        A notification that silently stood for eleven changes would under-report by ten, and
        the reader would have no way to know. ``folds`` carries the count into the delivery.
        """
        state = _state(delivered_digest="0" * 64, last_notified_at=at(), suppressed=10)

        suppressed = decide_raise(
            event(payload={"n": 1}, moment=at(minutes=1)), state, cooldown=COOLDOWN
        )
        escaped = decide_raise(
            event(payload={"n": 2}, moment=at(minutes=30)), state, cooldown=COOLDOWN
        )

        assert suppressed.folds == 11
        assert escaped.transition is AlertTransition.REPEATED
        assert escaped.folds == 11

    def test_the_window_is_measured_from_the_last_notification_not_the_last_occurrence(
        self,
    ) -> None:
        """Otherwise a fast-repeating change would extend the quiet window forever.

        Measured from the last occurrence, something changing every minute inside a
        fifteen-minute cooldown would push the next eligible instant out on every pass and
        the alert would never be delivered at all -- the storm control would have become a
        silencer. Here the notification at T0 fixes the window's end regardless of how many
        occurrences arrive inside it.
        """
        state = _state(delivered_digest="0" * 64, last_notified_at=at(), suppressed=14)

        decision = decide_raise(
            event(payload={"n": 15}, moment=at(minutes=15, seconds=1)), state, cooldown=COOLDOWN
        )

        assert decision.transition is AlertTransition.REPEATED

    def test_an_alert_that_has_never_notified_has_no_cooldown_to_wait_out(self) -> None:
        """A raise that was suppressed because the watch was off, then the watch comes on."""
        decision = decide_raise(
            event(payload={"n": 2}, moment=at(seconds=30)),
            _state(delivered_digest=None, last_notified_at=None, suppressed=1),
            cooldown=COOLDOWN,
        )

        assert decision.transition is AlertTransition.REPEATED


class TestWhatIsNeverSuppressed:
    def test_a_reopen_is_checked_before_the_digest(self) -> None:
        """A reopen's content is frequently identical to the original's.

        The same finding, the same evidence. Comparing digests first would call it a
        duplicate, and an exposure that came back would have been announced once, months ago.
        """
        original = event(payload={"rule": "everyone_has_access"})
        returning = event(payload={"rule": "everyone_has_access"}, moment=at(days=60))
        assert original.digest == returning.digest

        decision = decide_raise(
            returning,
            _state(
                status=AlertStatus.RESOLVED,
                lifecycle=AlertLifecycle.STATEFUL,
                delivered_digest=original.digest,
                last_notified_at=at(days=30),
            ),
            cooldown=dt.timedelta(days=365),
        )

        assert decision.transition is AlertTransition.REOPENED
        assert decision.notifies

    def test_a_resolution_waits_for_no_cooldown(self) -> None:
        decision = decide_resolve(
            _state(
                lifecycle=AlertLifecycle.STATEFUL,
                last_notified_at=at(seconds=1),
                suppressed=99,
            )
        )

        assert decision is not None
        assert decision.notifies
        assert decision.folds == 1


class TestWhatCannotResolve:
    def test_a_transient_alert_has_nothing_to_resolve(self) -> None:
        """An access control list edit cannot un-happen.

        Resolving one would put a "resolved" badge on a notice that was never a condition,
        and a reader would take it to mean the edit had been reverted.
        """
        assert decide_resolve(_state(lifecycle=AlertLifecycle.TRANSIENT)) is None

    def test_an_already_resolved_alert_does_not_resolve_twice(self) -> None:
        assert (
            decide_resolve(_state(status=AlertStatus.RESOLVED, lifecycle=AlertLifecycle.STATEFUL))
            is None
        )


class TestADisabledWatch:
    def test_it_suppresses_and_records_rather_than_discarding(self) -> None:
        """Turning a watch back on should show what it missed.

        A disabled watch that wrote nothing would make "this has been quiet" and "this was
        off" the same reading afterwards, which is the distinction the whole package exists
        to keep.
        """
        decision = decide_raise(event(), None, cooldown=COOLDOWN, watch_enabled=False)

        assert decision.transition is AlertTransition.SUPPRESSED
        assert decision.reason is SuppressionReason.WATCH_DISABLED
        assert not decision.notifies


class TestTheAlertKey:
    def test_it_is_a_function_of_the_subject_and_never_of_the_content(self) -> None:
        """The property deduplication rests on.

        The same condition in the same place has to be the same alert on Tuesday as it was
        on Monday. Putting the payload in the key would make every occurrence a new alert
        and turn the cooldown into decoration.
        """
        monday = event(payload={"observed_at": "monday", "count": 1})
        tuesday = event(payload={"observed_at": "tuesday", "count": 7}, moment=at(days=1))

        assert monday.key == tuesday.key
        assert monday.digest != tuesday.digest

    def test_a_discriminator_separates_two_alerts_about_one_place(self) -> None:
        alice = event(discriminator="principal|S-1-5-21-1")
        bob = event(discriminator="principal|S-1-5-21-2")

        assert alice.key != bob.key

    def test_an_alert_about_nothing_in_particular_is_refused(self) -> None:
        """Its identity would be the trigger alone, so every occurrence anywhere in the
        estate would fold into one alert and the cooldown would silence the estate."""
        from app.alerts import AlertSubject
        from app.domain import DomainValidationError

        with pytest.raises(DomainValidationError):
            AlertSubject()
