"""Builders for the alert suites.

Every instant is named rather than taken from a clock, so a cooldown test says what it means
and cannot fail at midnight.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID, uuid4

from app.alerts import (
    AccessExpansion,
    AlertEvent,
    AlertSubject,
    AlertTrigger,
    ChangeNotice,
    DeliveryEnvelope,
    DeliveryResult,
    FindingNotice,
    Watch,
    WatchKind,
)
from app.alerts.model import AlertTransition

#: A Monday morning. Every relative instant in the suites is written against it.
T0 = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)

FINANCE = "\\\\fs01\\finance"
SHARE = "share|fs01|finance"
GROUP = "principal|S-1-5-21-1111-2222-3333-5001"
ALICE = "principal|S-1-5-21-1111-2222-3333-1001"


def at(**offset: float) -> dt.datetime:
    """An instant relative to :data:`T0`, e.g. ``at(minutes=20)``."""
    return T0 + dt.timedelta(**offset)


def watch(
    *,
    kind: WatchKind = WatchKind.RESOURCE,
    key: str = FINANCE,
    triggers: frozenset[AlertTrigger] | None = None,
    cooldown: dt.timedelta = dt.timedelta(minutes=15),
    enabled: bool = True,
    label: str = "Finance directory",
    watch_id: UUID | None = None,
) -> Watch:
    if triggers is None:
        triggers = (
            frozenset({AlertTrigger.WATCHED_GROUP_MEMBERSHIP_CHANGED})
            if kind is WatchKind.GROUP
            else frozenset({AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED})
        )
    return Watch(
        watch_id=watch_id or uuid4(),
        kind=kind,
        key=key,
        label=label,
        triggers=triggers,
        cooldown=cooldown,
        created_at=T0,
        updated_at=T0,
        created_by="tester",
        enabled=enabled,
    )


def ace_change(
    *,
    place: str = FINANCE,
    trustee: str = ALICE,
    action: str = "added",
    kind: str = "ntfs_ace",
    moment: dt.datetime | None = None,
    severity: str = "high",
) -> ChangeNotice:
    return ChangeNotice(
        kind=kind,
        key=f"ntfs_ace|{place}|{trustee}|allow|0x001301bf|0x03",
        action=action,
        container_key=place,
        related_key=trustee,
        severity=severity,
        direction="broadened",
        at=moment or T0,
        reasons=("An entry granting Modify was added.",),
        container_label="Finance",
        related_label="Alice",
    )


def membership_change(
    *,
    group: str = GROUP,
    member: str = ALICE,
    action: str = "added",
    moment: dt.datetime | None = None,
) -> ChangeNotice:
    return ChangeNotice(
        kind="membership_edge",
        key=f"edge|{group}->{member}|direct",
        action=action,
        container_key=group,
        related_key=member,
        severity="medium",
        direction="broadened",
        at=moment or T0,
        container_label="Finance-RW",
        related_label="Alice",
    )


def expansion(
    *,
    place: str = FINANCE,
    principal: str = ALICE,
    gained: str = "Modify",
    moment: dt.datetime | None = None,
) -> AccessExpansion:
    return AccessExpansion(
        principal_key=principal,
        resource_key=place,
        share_key=None,
        gained=gained,
        before="Read",
        after="Modify",
        at=moment or T0,
        cause_kind="ntfs_ace",
        cause_key="ntfs_ace|x",
        principal_label="Alice",
        place_label="Finance",
    )


def finding(
    *,
    key: str = "f" * 64,
    severity: str = "critical",
    place: str = FINANCE,
    moment: dt.datetime | None = None,
    rule: str = "everyone_has_access",
) -> FindingNotice:
    return FindingNotice(
        finding_key=key,
        rule_id=rule,
        title="Everyone can reach this resource",
        severity=severity,
        confidence="confirmed",
        at=moment or T0,
        resource_key=place,
    )


def event(
    *,
    trigger: AlertTrigger = AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED,
    subject: AlertSubject | None = None,
    payload: dict[str, Any] | None = None,
    moment: dt.datetime | None = None,
    discriminator: str | None = None,
    watch_id: UUID | None = None,
    summary: str = "The access control list of Finance changed.",
) -> AlertEvent:
    return AlertEvent(
        trigger=trigger,
        subject=subject or AlertSubject(resource_key=FINANCE),
        occurred_at=moment or T0,
        summary=summary,
        payload=payload if payload is not None else {"action": "added"},
        discriminator=discriminator,
        watch_id=watch_id
        if watch_id is not None
        else (None if trigger == AlertTrigger.CRITICAL_RISK_FINDING_OPENED else uuid4()),
    )


def envelope(
    *,
    sink: str = "test-sink",
    moment: dt.datetime | None = None,
    event_id: UUID | None = None,
    transition: AlertTransition = AlertTransition.RAISED,
) -> DeliveryEnvelope:
    return DeliveryEnvelope(
        event_id=event_id or uuid4(),
        alert_key="a" * 64,
        trigger=AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED,
        transition=transition,
        sink_name=sink,
        occurred_at=moment or T0,
        summary="Something changed.",
        payload={"action": "added"},
    )


class RecordingSink:
    """A sink that records what it was handed and returns what it was told to.

    Not a mock: it implements the protocol exactly, so a dispatcher test that passes against
    it is a test of the dispatcher rather than of an expectation that agrees with it.
    """

    def __init__(
        self, name: str = "test-sink", *, results: list[DeliveryResult] | None = None
    ) -> None:
        self.name = name
        self.received: list[DeliveryEnvelope] = []
        self._results = list(results or [])

    async def deliver(self, envelope: DeliveryEnvelope) -> DeliveryResult:
        self.received.append(envelope)
        if self._results:
            return self._results.pop(0)
        return DeliveryResult.success("ok")


class ExplodingSink:
    """A sink that raises, which the protocol forbids and a real one will eventually do."""

    def __init__(self, name: str = "broken-sink") -> None:
        self.name = name
        self.attempts = 0

    async def deliver(self, envelope: DeliveryEnvelope) -> DeliveryResult:
        self.attempts += 1
        raise RuntimeError("the sink exploded")
