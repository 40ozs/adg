"""Whether to say it again — the one guard between a monitoring feature and a pager storm.

A pure decision. It takes what is already recorded about an alert, the candidate event, a
cooldown and an instant, and returns what should happen. No database, no clock, no I/O, so
every branch below is exercised in microseconds by :mod:`tests.alerts.test_dedupe` rather
than by staging an ingestion that happens to repeat itself.

## Three different things, deliberately not collapsed

**Duplicate.** The same alert, with content identical to what was last *delivered*. This is
detection running twice — a retried completion, an overlapping incremental window, an
operator re-running an evaluation — and it must never produce a second notification. Matched
on the payload digest, never on a timestamp, so it holds regardless of how far apart the two
passes ran.

**Repeat.** The same alert with *different* content. Something happened again. Worth telling
somebody, but not at the rate an automated process can generate it, so it waits for the
cooldown.

**Cooldown.** A repeat inside the window. Suppressed — and **counted**, which is the part
that matters. The next delivery carries how many were folded into it, so a feed that went
quiet because of a cooldown never looks the same as a feed that went quiet because the
estate did.

## What is never suppressed

A **resolution**, and a **reopen**. Both are deliberate and both point the same direction:
the failure mode of an alerting system is not "too loud", it is "somebody believed the last
thing it said". An operator who was told an exposure opened and is never told it closed will
keep acting on a condition that is gone; one who was told it closed and is never told it came
back will not act on a condition that is live. A storm of resolutions is also self-limiting
in a way a storm of repeats is not — nothing can resolve more often than it opened.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.alerts.model import (
    NOTIFYING_TRANSITIONS,
    AlertEvent,
    AlertLifecycle,
    AlertStatus,
    AlertTransition,
)

__all__ = [
    "AlertState",
    "Decision",
    "SuppressionReason",
    "decide_raise",
    "decide_resolve",
]


class SuppressionReason(StrEnum):
    """Why a candidate was not delivered. Recorded on the event, never merely implied."""

    IDENTICAL_CONTENT = "identical_content"
    """Byte-for-byte the notification already delivered. Detection ran twice."""

    WITHIN_COOLDOWN = "within_cooldown"
    """Something new, inside the window the watch is configured to be quiet for."""

    WATCH_DISABLED = "watch_disabled"
    """The subscription behind this alert is turned off. Recorded rather than dropped, so
    that turning a watch back on shows what it missed."""


@dataclass(frozen=True, slots=True)
class AlertState:
    """What is already recorded about one alert, as the decision needs to see it.

    A projection of the stored row rather than the row itself, so that the decision function
    cannot reach for a column somebody adds later and quietly start depending on it.
    """

    key: str
    status: AlertStatus
    lifecycle: AlertLifecycle
    #: The digest of the payload that was last **delivered**, not the last one recorded. A
    #: suppressed repeat must not update it: if it did, the next occurrence of the content
    #: an operator was actually shown would read as new.
    delivered_digest: str | None
    last_notified_at: dt.datetime | None
    #: Occurrences suppressed since the last notification. Carried into the next delivery.
    suppressed_since_notice: int = 0


@dataclass(frozen=True, slots=True)
class Decision:
    """What to do with one candidate, and why."""

    transition: AlertTransition
    reason: SuppressionReason | None = None
    #: Occurrences this delivery speaks for, the current one included. 1 for an ordinary
    #: alert; higher when a cooldown folded others into it.
    folds: int = 1
    #: When the next repeat of this alert would be eligible to notify. Null when the
    #: decision was to notify now and the cooldown has therefore just restarted from
    #: ``occurred_at``, or when nothing is pending.
    cooldown_until: dt.datetime | None = None

    @property
    def notifies(self) -> bool:
        return self.transition in NOTIFYING_TRANSITIONS


#: A resolution is compared against nothing and waits for nothing.
_RESOLUTION: Final = Decision(transition=AlertTransition.RESOLVED)


def decide_raise(
    event: AlertEvent,
    state: AlertState | None,
    *,
    cooldown: dt.timedelta,
    watch_enabled: bool = True,
) -> Decision:
    """Whether a candidate alert should notify, and under which transition.

    ``state`` is ``None`` for an alert that has never existed, which is the only case that
    can produce :attr:`AlertTransition.RAISED`. Everything else is a second occurrence of
    something already recorded, and the question is only whether anybody should be told
    again.

    A disabled watch suppresses rather than discards. The event is still recorded, so that
    an operator who turns a watch back on can see what happened while it was off — and so
    that "this watch has been quiet" and "this watch was off" are answerable separately
    afterwards, which they are not if one of them writes nothing.
    """
    if not watch_enabled:
        return Decision(
            transition=AlertTransition.SUPPRESSED,
            reason=SuppressionReason.WATCH_DISABLED,
            folds=(state.suppressed_since_notice + 1) if state is not None else 1,
            cooldown_until=_cooldown_until(state, cooldown),
        )

    if state is None:
        return Decision(transition=AlertTransition.RAISED)

    # A stateful alert that resolved and is true again. Never suppressed, and checked before
    # the digest: the content of a reopen is frequently identical to the content of the
    # original -- the same finding, the same evidence -- and treating that as a duplicate
    # would mean an exposure that came back was announced once, months ago.
    if state.status is AlertStatus.RESOLVED:
        return Decision(transition=AlertTransition.REOPENED)

    if state.delivered_digest == event.digest:
        return Decision(
            transition=AlertTransition.SUPPRESSED,
            reason=SuppressionReason.IDENTICAL_CONTENT,
            folds=state.suppressed_since_notice + 1,
            cooldown_until=_cooldown_until(state, cooldown),
        )

    eligible_at = _cooldown_until(state, cooldown)
    if eligible_at is not None and event.occurred_at < eligible_at:
        return Decision(
            transition=AlertTransition.SUPPRESSED,
            reason=SuppressionReason.WITHIN_COOLDOWN,
            folds=state.suppressed_since_notice + 1,
            cooldown_until=eligible_at,
        )

    return Decision(
        transition=AlertTransition.REPEATED,
        folds=state.suppressed_since_notice + 1,
    )


def decide_resolve(state: AlertState) -> Decision | None:
    """Whether the end of a condition should notify.

    ``None`` means there is nothing to resolve — the alert is already resolved, or it is
    transient and therefore describes something that cannot stop having happened. Both are
    ordinary and neither is an error: a resolution pass sweeps whatever it covered and most
    of what it covers has not changed.
    """
    if state.lifecycle is AlertLifecycle.TRANSIENT:
        return None
    if state.status is AlertStatus.RESOLVED:
        return None
    return _RESOLUTION


def _cooldown_until(state: AlertState | None, cooldown: dt.timedelta) -> dt.datetime | None:
    """When this alert may notify again, measured from the last time it actually did.

    From the last **notification** rather than the last occurrence, deliberately. Measured
    from the last occurrence, a change repeating faster than the cooldown would extend the
    quiet window indefinitely and the alert would never be delivered at all — the storm
    control would have become a silencer.
    """
    if state is None or state.last_notified_at is None:
        return None
    return state.last_notified_at + cooldown
