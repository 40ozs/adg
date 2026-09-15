"""Turning facts into candidate alerts. Pure, and deliberately ignorant of where they came from.

This is the half of the pipeline that decides *something worth saying happened*. The half
that decides whether to say it again is :mod:`app.alerts.dedupe`; the half that says it is
:mod:`app.alerts.sinks`. Keeping the three apart is what lets each be tested in isolation,
and the seam matters most here: detection over real sources would otherwise be exercisable
only by staging an ingestion that happens to change the right thing.

## The neutral inputs, and why they are not the real ones

The three record types below — :class:`ChangeNotice`, :class:`AccessExpansion` and
:class:`FindingNotice` — are flat values carrying exactly what a watch is matched against and
what a notification has to say. They are **not** :class:`app.changes.ObjectChange`,
:class:`app.changes.ChangeImpact` or :class:`app.risk_engine.RiskFinding`, and this module
imports none of those.

That is the structural half of prompt requirement 6, *do not embed delivery specifics into
the core risk engine*, pointed in the other direction: nothing in ``app/alerts`` may import
``app.risk_engine`` or ``app.changes``, so nothing in the alert pipeline can reach into a
rule, and a change to how a rule computes severity cannot change how an alert is delivered.
The translation lives in :mod:`app.services.alerts`, which is allowed to know both, and
``tests/alerts/test_layering.py`` fails if the dependency ever appears here.

The cost of the seam is one conversion per source, and it is worth it twice over: these
tests need no database fixtures, and a reader can see the entire vocabulary a sink can
receive by reading three dataclasses.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.alerts.model import (
    AlertEvent,
    AlertSubject,
    AlertTrigger,
    Watch,
    WatchKind,
)

__all__ = [
    "ACL_CHANGE_KINDS",
    "MEMBERSHIP_CHANGE_KINDS",
    "AccessExpansion",
    "ChangeNotice",
    "FindingNotice",
    "WatchIndex",
    "events_for_access_expansions",
    "events_for_changes",
    "events_for_findings",
    "resolution_keys",
]


#: Observation kinds whose movement is an access control list changing, by value.
#:
#: ``ntfs_resource`` and ``smb_share`` are here alongside the entry kinds because a
#: descriptor's own columns carry access facts: ``dacl_present`` going false is a NULL DACL,
#: which grants everyone everything and is the single most consequential thing on this list.
#: A watch that fired on entries and not on that would miss the worst case while appearing
#: to cover the ACL.
ACL_CHANGE_KINDS: frozenset[str] = frozenset({"ntfs_ace", "smb_ace", "ntfs_resource", "smb_share"})

MEMBERSHIP_CHANGE_KINDS: frozenset[str] = frozenset({"membership_edge"})


@dataclass(frozen=True, slots=True)
class ChangeNotice:
    """One classified change, in the terms a watch is matched against.

    ``container_key`` and ``related_key`` are the change's subject as
    :class:`app.changes.SubjectRef` reports it: for an entry on an access control list, the
    place and the trustee; for a membership edge, the group and the member. Matching happens
    on those rather than on ``key``, because an ACE's own key names an entry and a watch
    names a place.
    """

    kind: str
    key: str
    action: str
    container_key: str | None
    related_key: str | None
    severity: str
    """The change classifier's own grading, carried and never recomputed. See the note in
    :mod:`app.alerts.model` about why an alert has no severity of its own."""

    direction: str
    at: dt.datetime
    reasons: tuple[str, ...] = ()
    #: Display names, when ADG holds them. Absent is ordinary and is rendered as the key.
    container_label: str | None = None
    related_label: str | None = None
    window: tuple[dt.datetime, dt.datetime] | None = None
    """The interval the change is known to have happened inside (ADR-0019). Carried into the
    payload so a notification never dates a change to the scan that found it."""


@dataclass(frozen=True, slots=True)
class AccessExpansion:
    """Somebody can now do more to a place than they could before.

    Distinct from a :class:`ChangeNotice` about the same edit, and neither implies the other.
    An entry can be added that grants nothing because a Deny or the other layer still
    governs; access can widen with no entry touched because a group gained a member.
    """

    principal_key: str
    resource_key: str | None
    share_key: str | None
    gained: str
    """What was gained, already rendered by the access engine. A string because this module
    must not hold a rights mask it would be tempted to interpret."""

    before: str
    after: str
    at: dt.datetime
    cause_kind: str
    cause_key: str
    principal_label: str | None = None
    place_label: str | None = None

    def __post_init__(self) -> None:
        if not (self.resource_key or self.share_key):
            raise ValueError("An access expansion must name the place access was gained to.")


@dataclass(frozen=True, slots=True)
class FindingNotice:
    """A risk finding that opened, reopened, or resolved, in the terms an alert needs."""

    finding_key: str
    rule_id: str
    title: str
    severity: str
    confidence: str
    at: dt.datetime
    resource_key: str | None = None
    share_key: str | None = None
    principal_key: str | None = None
    summary: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WatchIndex:
    """The enabled watches, arranged for the lookup detection actually makes.

    Built once per pass rather than scanned per change: a detection pass over a busy week
    compares thousands of changes against the watch list, and a linear scan per change turns
    a linear pass into a quadratic one on the two inputs an operator has most control over.
    """

    by_kind: Mapping[WatchKind, Mapping[str, tuple[Watch, ...]]]

    @classmethod
    def build(cls, watches: Iterable[Watch]) -> WatchIndex:
        buckets: dict[WatchKind, dict[str, list[Watch]]] = {kind: {} for kind in WatchKind}
        for watch in watches:
            buckets[watch.kind].setdefault(_fold(watch.key), []).append(watch)
        return cls(
            by_kind={
                kind: {key: tuple(items) for key, items in mapping.items()}
                for kind, mapping in buckets.items()
            }
        )

    def matching(
        self, kind: WatchKind, key: str | None, trigger: AlertTrigger
    ) -> tuple[Watch, ...]:
        """Enabled watches of ``kind`` on ``key`` that subscribe to ``trigger``."""
        if key is None:
            return ()
        candidates = self.by_kind.get(kind, {}).get(_fold(key), ())
        return tuple(watch for watch in candidates if watch.watches(trigger))

    @property
    def is_empty(self) -> bool:
        return not any(mapping for mapping in self.by_kind.values())


def _fold(key: str) -> str:
    """Case-folded, for the comparison Windows itself makes.

    Every key in ADG that names a place is already case-folded at ingestion — a resource key
    is a case-folded UNC path, a share key a case-folded pair — but a watch key arrives from
    an operator through an API, so folding both sides is what keeps a watch typed
    ``\\\\FS01\\Finance`` from being silently inert.
    """
    return key.strip().casefold()


# --------------------------------------------------------------- change detection


def events_for_changes(
    index: WatchIndex, changes: Sequence[ChangeNotice], *, enabled_triggers: frozenset[AlertTrigger]
) -> tuple[AlertEvent, ...]:
    """Candidate alerts for every watched thing that moved.

    One event per (watch, change), so a directory watched by two teams alerts both — and each
    is deduplicated and cooled down on its own schedule, because the watches are separate
    subscriptions and one team's storm is not the other's.
    """
    events: list[AlertEvent] = []
    for change in changes:
        if change.kind in MEMBERSHIP_CHANGE_KINDS:
            events.extend(_membership_events(index, change, enabled_triggers))
        if change.kind in ACL_CHANGE_KINDS:
            events.extend(_acl_events(index, change, enabled_triggers))
    return tuple(events)


def _membership_events(
    index: WatchIndex, change: ChangeNotice, enabled: frozenset[AlertTrigger]
) -> list[AlertEvent]:
    trigger = AlertTrigger.WATCHED_GROUP_MEMBERSHIP_CHANGED
    if trigger not in enabled:
        return []
    group_key = change.container_key
    watches = index.matching(WatchKind.GROUP, group_key, trigger)
    if not watches or group_key is None:
        return []

    member = change.related_label or change.related_key or "an unnamed member"
    group = change.container_label or group_key
    verb = {"added": "joined", "removed": "left"}.get(change.action, f"{change.action} in")
    summary = f"{member} {verb} {group}."
    payload = _change_payload(change) | {
        "group_key": group_key,
        "group_label": change.container_label,
        "member_key": change.related_key,
        "member_label": change.related_label,
    }
    return [
        AlertEvent(
            trigger=trigger,
            subject=AlertSubject(principal_key=group_key),
            occurred_at=change.at,
            summary=summary,
            payload=payload,
            # The member, so two people joining one group are two alerts. Folding them into
            # one would make the cooldown hide the second person entirely, and "who is in
            # this group" is the question the watch exists to answer.
            discriminator=change.related_key,
            watch_id=watch.watch_id,
        )
        for watch in watches
    ]


def _acl_events(
    index: WatchIndex, change: ChangeNotice, enabled: frozenset[AlertTrigger]
) -> list[AlertEvent]:
    trigger = AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED
    if trigger not in enabled:
        return []

    # Which key names the place depends on what moved. An entry's container is the place; a
    # descriptor's own change names the place directly.
    place_key = change.container_key if change.kind in ("ntfs_ace", "smb_ace") else change.key
    if place_key is None:
        return []

    kind = WatchKind.SHARE if change.kind in ("smb_ace", "smb_share") else WatchKind.RESOURCE
    watches = index.matching(kind, place_key, trigger)
    if not watches:
        return []

    subject = (
        AlertSubject(share_key=place_key)
        if kind is WatchKind.SHARE
        else AlertSubject(resource_key=place_key)
    )
    place = change.container_label or place_key
    trustee = change.related_label or change.related_key
    if trustee is not None:
        summary = f"The access control list of {place} changed: {change.action} for {trustee}."
    else:
        summary = f"{place} changed: {change.action} ({change.severity})."
    payload = _change_payload(change) | {
        "place_key": place_key,
        "place_kind": kind.value,
        "place_label": change.container_label,
        "trustee_key": change.related_key,
        "trustee_label": change.related_label,
    }
    return [
        AlertEvent(
            trigger=trigger,
            subject=subject,
            occurred_at=change.at,
            summary=summary,
            payload=payload,
            # The trustee, for the same reason the membership case uses the member: two
            # trustees edited on one list are two things an operator has to look at.
            discriminator=change.related_key,
            watch_id=watch.watch_id,
        )
        for watch in watches
    ]


def _change_payload(change: ChangeNotice) -> dict[str, Any]:
    return {
        "change_kind": change.kind,
        "change_key": change.key,
        "action": change.action,
        "change_severity": change.severity,
        "direction": change.direction,
        "reasons": list(change.reasons),
        "observed_at": change.at.isoformat(),
        # ADR-0019: a change is a window, not an instant. The payload says so, so a
        # notification cannot date an edit to the scan that noticed it.
        "window": (
            None
            if change.window is None
            else {"from": change.window[0].isoformat(), "to": change.window[1].isoformat()}
        ),
    }


# ------------------------------------------------------------ expansion detection


def events_for_access_expansions(
    index: WatchIndex,
    expansions: Sequence[AccessExpansion],
    *,
    enabled_triggers: frozenset[AlertTrigger],
) -> tuple[AlertEvent, ...]:
    """Candidate alerts for effective access that grew against a watched place."""
    trigger = AlertTrigger.WATCHED_ACCESS_EXPANDED
    if trigger not in enabled_triggers:
        return ()

    events: list[AlertEvent] = []
    for expansion in expansions:
        kind = WatchKind.RESOURCE if expansion.resource_key else WatchKind.SHARE
        place_key = expansion.resource_key or expansion.share_key
        watches = index.matching(kind, place_key, trigger)
        if not watches:
            continue
        who = expansion.principal_label or expansion.principal_key
        where = expansion.place_label or place_key
        summary = f"{who} gained {expansion.gained} on {where}."
        payload = {
            "principal_key": expansion.principal_key,
            "principal_label": expansion.principal_label,
            "place_key": place_key,
            "place_kind": kind.value,
            "gained": expansion.gained,
            "rights_before": expansion.before,
            "rights_after": expansion.after,
            "cause_kind": expansion.cause_kind,
            "cause_key": expansion.cause_key,
            "observed_at": expansion.at.isoformat(),
        }
        subject = (
            AlertSubject(resource_key=expansion.resource_key, principal_key=expansion.principal_key)
            if expansion.resource_key
            else AlertSubject(share_key=expansion.share_key, principal_key=expansion.principal_key)
        )
        events.extend(
            AlertEvent(
                trigger=trigger,
                subject=subject,
                occurred_at=expansion.at,
                summary=summary,
                payload=payload,
                watch_id=watch.watch_id,
            )
            for watch in watches
        )
    return tuple(events)


# -------------------------------------------------------------- finding detection


def events_for_findings(
    findings: Sequence[FindingNotice],
    *,
    enabled_triggers: frozenset[AlertTrigger],
) -> tuple[AlertEvent, ...]:
    """Candidate alerts for risk findings that opened.

    **No watch, and no watch index parameter.** This trigger is estate-wide by policy: a
    critical exposure on a share nobody thought to watch is precisely the one worth
    interrupting somebody for, and requiring a subscription would make the feature's coverage
    equal to somebody's foresight.

    Which findings reach here — the severity threshold — is the caller's decision, from
    :attr:`app.alerts.configuration.AlertPolicy.finding_severity_threshold`. Filtering by
    severity in this module would mean comparing severity strings here, and the ordering of
    that vocabulary belongs to the risk engine.
    """
    trigger = AlertTrigger.CRITICAL_RISK_FINDING_OPENED
    if trigger not in enabled_triggers:
        return ()

    events: list[AlertEvent] = []
    for finding in findings:
        subject = AlertSubject(
            resource_key=finding.resource_key,
            share_key=finding.share_key,
            principal_key=finding.principal_key,
        )
        where = finding.resource_key or finding.share_key or finding.principal_key
        summary = finding.summary or (
            f"{finding.severity.capitalize()} risk finding opened: {finding.title} on {where}."
        )
        events.append(
            AlertEvent(
                trigger=trigger,
                subject=subject,
                occurred_at=finding.at,
                summary=summary,
                payload={
                    "finding_key": finding.finding_key,
                    "rule_id": finding.rule_id,
                    "title": finding.title,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "resource_key": finding.resource_key,
                    "share_key": finding.share_key,
                    "principal_key": finding.principal_key,
                    "detail": dict(finding.detail),
                    "opened_at": finding.at.isoformat(),
                },
                # The finding key, so that two findings of different rules about one
                # directory are two alerts, and so that an alert can be resolved by the
                # finding that produced it without a second lookup.
                discriminator=finding.finding_key,
            )
        )
    return tuple(events)


def resolution_keys(findings: Sequence[FindingNotice]) -> tuple[str, ...]:
    """The alert keys that findings resolving should close.

    Computed the same way the raise path computes them, from the same subject and the same
    discriminator, so that a resolution can only ever close the alert its own finding
    opened. Deriving it any other way — a query on the payload, say — would be a second
    implementation of the alert key.
    """
    return tuple(
        AlertEvent(
            trigger=AlertTrigger.CRITICAL_RISK_FINDING_OPENED,
            subject=AlertSubject(
                resource_key=finding.resource_key,
                share_key=finding.share_key,
                principal_key=finding.principal_key,
            ),
            occurred_at=finding.at,
            summary=f"resolution of {finding.finding_key}",
            payload={},
            discriminator=finding.finding_key,
        ).key
        for finding in findings
    )
