"""The vocabulary: what a watch may subscribe to, and what a watch refuses to become.

The interesting tests here are the refusals. A watch that accepts a subscription it can never
honor is saved, listed in the interface, and silent forever — and silence from a monitoring
feature reads as "nothing happened", which is the one thing this product must never say by
accident.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.alerts import (
    MAX_COOLDOWN,
    MIN_COOLDOWN,
    NOTIFYING_TRANSITIONS,
    TRIGGER_DESCRIPTIONS,
    TRIGGER_LIFECYCLE,
    TRIGGERS_BY_WATCH_KIND,
    UNWATCHED_TRIGGERS,
    AlertEvent,
    AlertLifecycle,
    AlertSubject,
    AlertTransition,
    AlertTrigger,
    WatchKind,
    triggers_for_kind,
)
from app.domain import DomainValidationError
from tests.alerts.support import FINANCE, GROUP, T0, event, watch


class TestTheTables:
    def test_every_trigger_has_a_lifecycle(self) -> None:
        assert set(TRIGGER_LIFECYCLE) == set(AlertTrigger)

    def test_every_trigger_has_a_description_an_operator_can_read(self) -> None:
        assert set(TRIGGER_DESCRIPTIONS) == set(AlertTrigger)
        for text in TRIGGER_DESCRIPTIONS.values():
            assert len(text) > 40

    def test_every_watch_kind_names_the_triggers_it_supports(self) -> None:
        assert set(TRIGGERS_BY_WATCH_KIND) == set(WatchKind)
        for kind in WatchKind:
            assert triggers_for_kind(kind)

    def test_every_watchable_trigger_is_reachable_from_some_watch_kind(self) -> None:
        """Otherwise a trigger would be configurable in the policy and subscribable by
        nothing, which is a feature that exists in a settings page and nowhere else."""
        reachable = set().union(*TRIGGERS_BY_WATCH_KIND.values())
        assert reachable | UNWATCHED_TRIGGERS == set(AlertTrigger)

    def test_only_the_finding_trigger_fires_without_a_watch(self) -> None:
        assert {AlertTrigger.CRITICAL_RISK_FINDING_OPENED} == UNWATCHED_TRIGGERS

    def test_the_finding_trigger_is_the_only_stateful_one(self) -> None:
        """A finding is a condition; everything else here is an event that happened.

        If another stateful trigger arrives, something has to resolve it, and this test is
        where that gets noticed rather than in a feed full of alerts that never close.
        """
        stateful = {
            trigger
            for trigger, lifecycle in TRIGGER_LIFECYCLE.items()
            if lifecycle is AlertLifecycle.STATEFUL
        }
        assert stateful == {AlertTrigger.CRITICAL_RISK_FINDING_OPENED}

    def test_only_a_suppression_does_not_notify(self) -> None:
        assert set(AlertTransition) - {AlertTransition.SUPPRESSED} == NOTIFYING_TRANSITIONS


class TestWhatAWatchRefuses:
    def test_a_trigger_the_kind_could_never_fire(self) -> None:
        with pytest.raises(DomainValidationError) as error:
            watch(
                kind=WatchKind.RESOURCE,
                triggers=frozenset({AlertTrigger.WATCHED_GROUP_MEMBERSHIP_CHANGED}),
            )

        # The message names what that kind *does* support, because the person reading it is
        # about to retype the request.
        assert "watched_resource_acl_changed" in str(error.value)

    def test_subscribing_to_nothing(self) -> None:
        with pytest.raises(DomainValidationError) as error:
            watch(triggers=frozenset())

        assert "Disable it instead" in str(error.value)

    @pytest.mark.parametrize(
        "cooldown",
        [MIN_COOLDOWN - dt.timedelta(seconds=1), MAX_COOLDOWN + dt.timedelta(seconds=1)],
    )
    def test_a_cooldown_outside_the_bounds(self, cooldown: dt.timedelta) -> None:
        with pytest.raises(DomainValidationError):
            watch(cooldown=cooldown)

    def test_a_watch_with_no_label(self) -> None:
        """The key of a directory is a UNC path and the key of a group is a SID.

        A feed listing either one unlabeled is a feed nobody reads.
        """
        with pytest.raises(DomainValidationError):
            watch(label="   ")

    def test_a_watch_with_no_key(self) -> None:
        with pytest.raises(DomainValidationError):
            watch(key="  ")


class TestWhatAWatchAccepts:
    def test_a_group_watch_takes_the_membership_trigger(self) -> None:
        subject = watch(kind=WatchKind.GROUP, key=GROUP)

        assert subject.watches(AlertTrigger.WATCHED_GROUP_MEMBERSHIP_CHANGED)
        assert not subject.watches(AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED)

    def test_a_disabled_watch_watches_nothing(self) -> None:
        """Checked on the watch rather than at every call site.

        ``watches()`` folding ``enabled`` in is what keeps a detection pass from having to
        remember the check -- and a detection pass that forgot it would alert through a
        subscription somebody had deliberately turned off.
        """
        subject = watch(enabled=False)

        assert not subject.watches(AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED)


class TestWhatAnEventRefuses:
    def test_a_naive_instant(self) -> None:
        """A cooldown an hour out either suppresses an alert that should have gone or
        delivers the storm it was configured to prevent."""
        with pytest.raises(DomainValidationError):
            event(moment=dt.datetime(2026, 3, 2, 9, 0))

    def test_a_watched_trigger_with_no_watch_behind_it(self) -> None:
        """Removing the watch is the only control an operator has over such an alert.

        Constructed directly rather than through the builder, because the builder supplies a
        watch for every watched trigger -- which is what production code does too, and is
        exactly why this refusal needs a test that bypasses it.
        """
        with pytest.raises(DomainValidationError) as error:
            AlertEvent(
                trigger=AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED,
                subject=AlertSubject(resource_key=FINANCE),
                occurred_at=T0,
                summary="Something changed.",
                payload={},
                watch_id=None,
            )

        assert "cannot be silenced" in str(error.value)

    def test_an_empty_summary(self) -> None:
        with pytest.raises(DomainValidationError):
            event(summary="  ")

    def test_the_finding_trigger_needs_no_watch(self) -> None:
        raised = event(trigger=AlertTrigger.CRITICAL_RISK_FINDING_OPENED, watch_id=None)

        assert raised.watch_id is None
        assert raised.lifecycle is AlertLifecycle.STATEFUL


class TestTheDigest:
    def test_two_payloads_saying_the_same_thing_digest_the_same(self) -> None:
        """Assembled in a different order, by a different code path, at a different time."""
        one = event(payload={"a": 1, "b": [1, 2]}, moment=T0)
        two = event(payload={"b": [1, 2], "a": 1}, moment=T0)

        assert one.digest == two.digest
