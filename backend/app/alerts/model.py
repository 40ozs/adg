r"""The vocabulary of the alert pipeline: what may be watched, what may be announced.

Everything here is a value. No database, no HTTP client, no clock — the instants are passed
in, so a test can name them and two runs over the same facts produce the same alerts.

## The one decision this module exists to enforce

**An alert carries no severity of its own.** It names the thing that produced it and carries
that thing's own grading in its payload — a :class:`app.risk_engine.Severity` for a finding,
a :class:`app.changes.ChangeSeverity` for a change. A third severity vocabulary would be a
third table to keep in step with the other two, and the first time it drifted, an alert would
grade an exposure differently from the finding it was raised about. The two existing scales
also do not mean the same thing: one describes a consequence in the estate, the other
describes how much an edit moved. Averaging them into "alert severity" would produce a number
whose unit nobody could name.

What an alert does carry is a :class:`AlertTrigger`, which says *why you are being told*, and
that is what an operator filters and routes on.

## Two lifecycles, and why the distinction is in the data

A risk finding is a **condition**: it is true until the estate changes, and it can resolve and
come back. An ACL edit is an **event**: it happened, and nothing that happens later can make
it not have happened. Alerts about the two therefore behave differently, and pretending
otherwise produces one of two lies — a "resolved" ACL change that was never a condition, or a
finding alert that stays open forever because nothing was watching for its resolution.

So :data:`TRIGGER_LIFECYCLE` maps each trigger to an :class:`AlertLifecycle`, and the
persistence layer refuses to resolve a transient alert. A transient alert ages out of a view
by date; it never resolves, because there is nothing to resolve.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from app.domain import DomainValidationError

__all__ = [
    "MAX_COOLDOWN",
    "MAX_LABEL_LENGTH",
    "MIN_COOLDOWN",
    "TRIGGERS_BY_WATCH_KIND",
    "TRIGGER_DESCRIPTIONS",
    "TRIGGER_LIFECYCLE",
    "UNWATCHED_TRIGGERS",
    "AlertEvent",
    "AlertLifecycle",
    "AlertStatus",
    "AlertSubject",
    "AlertTransition",
    "AlertTrigger",
    "Watch",
    "WatchKind",
    "alert_key",
    "payload_digest",
    "triggers_for_kind",
]

#: Separator inside every digest input. A control character, so it cannot occur in a UNC
#: path, a SID, a rule id or a label, which is what keeps two different subjects from
#: rendering to the same string. The risk engine's finding key uses the same device.
_SEPARATOR: Final = "\x1f"

MAX_LABEL_LENGTH: Final = 200

#: A cooldown shorter than this is not a cooldown; it is a formality that would let a script
#: editing an ACL in a loop produce an alert per edit. A cooldown longer than a day would
#: fold a Tuesday change into a Monday alert, and nobody reading Tuesday's feed would know.
MIN_COOLDOWN: Final = dt.timedelta(minutes=1)
MAX_COOLDOWN: Final = dt.timedelta(days=1)


class WatchKind(StrEnum):
    """What an operator may put a watch on.

    Three kinds, and each is a thing that *has an access control list or a membership* —
    which is the whole of what this pipeline can observe changing. A watch on a server was
    considered and left out: a server's own state (its operating system, its last boot) is
    not an access fact, and a watch that appeared to cover every share beneath it while
    actually covering none of them is the kind of quiet nothing this product exists to
    prevent. Watch the shares.
    """

    RESOURCE = "resource"
    """One directory, by its UNC path. Covers that directory's own DACL, not its children's:
    ADG holds a descriptor per directory and a watch that silently meant "and everything
    below" would fire on a subtree the operator never named."""

    SHARE = "share"
    """One SMB share, by its key. Covers the share-level access control list and the share
    object itself — not the directories published beneath it, for the same reason a
    directory watch stops at the directory. What a share grants is the share ACL crossed
    with the NTFS ACL of each path under it; a watch that quietly meant "and every directory
    below" would fire on a subtree the operator never named, and one covering a subtree of a
    hundred thousand directories would fire constantly."""

    GROUP = "group"
    """One security group, by its principal key. Covers its direct membership."""


class AlertTrigger(StrEnum):
    """Why an alert was raised. The thing an operator filters and routes on."""

    WATCHED_GROUP_MEMBERSHIP_CHANGED = "watched_group_membership_changed"
    """A membership edge naming a watched group was added or removed."""

    WATCHED_RESOURCE_ACL_CHANGED = "watched_resource_acl_changed"
    """An entry on a watched directory's or share's access control list moved."""

    WATCHED_ACCESS_EXPANDED = "watched_access_expanded"
    """Effective access to a watched place **grew** for some principal. Distinct from the
    ACL trigger and not implied by it: an entry can be added that grants nothing, because
    the other layer or a Deny still governs, and an entry can be left alone while access
    widens underneath it because a group gained a member."""

    CRITICAL_RISK_FINDING_OPENED = "critical_risk_finding_opened"
    """A risk finding at or above the configured severity opened — anywhere in the estate,
    watched or not. A critical exposure on a share nobody thought to watch is precisely the
    one worth being told about."""


class AlertLifecycle(StrEnum):
    """Whether an alert describes a condition that can end, or an event that happened."""

    STATEFUL = "stateful"
    """Mirrors something that is true until it is not: it resolves, and it can reopen."""

    TRANSIENT = "transient"
    """A notice about something that occurred. It never resolves — an edit cannot un-happen
    — so it ages out of a view by date rather than by a status changing."""


TRIGGER_LIFECYCLE: Final[dict[AlertTrigger, AlertLifecycle]] = {
    AlertTrigger.WATCHED_GROUP_MEMBERSHIP_CHANGED: AlertLifecycle.TRANSIENT,
    AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED: AlertLifecycle.TRANSIENT,
    AlertTrigger.WATCHED_ACCESS_EXPANDED: AlertLifecycle.TRANSIENT,
    AlertTrigger.CRITICAL_RISK_FINDING_OPENED: AlertLifecycle.STATEFUL,
}


TRIGGER_DESCRIPTIONS: Final[dict[AlertTrigger, str]] = {
    AlertTrigger.WATCHED_GROUP_MEMBERSHIP_CHANGED: (
        "Somebody was added to or removed from a group you are watching. The alert names "
        "the members that moved; what they can consequently reach is an access question, "
        "and the alert links to it rather than guessing."
    ),
    AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED: (
        "An access control entry on a place you are watching was added, removed or edited. "
        "The alert names the trustee and what moved; it does not claim the edit changed "
        "what anybody can do, because frequently it does not."
    ),
    AlertTrigger.WATCHED_ACCESS_EXPANDED: (
        "Somebody can now do more to a place you are watching than they could before. This "
        "is the consequence rather than the edit, and the two are reported separately "
        "because they disagree often enough to matter."
    ),
    AlertTrigger.CRITICAL_RISK_FINDING_OPENED: (
        "A risk finding at or above the configured severity opened. Estate-wide: it does "
        "not require a watch, because the exposures worth interrupting somebody for are "
        "the ones nobody thought to watch for."
    ),
}


#: Triggers that fire without a watch. Estate-wide by policy rather than by subscription.
UNWATCHED_TRIGGERS: Final[frozenset[AlertTrigger]] = frozenset(
    {AlertTrigger.CRITICAL_RISK_FINDING_OPENED}
)


#: Which triggers a watch of each kind may subscribe to.
#:
#: Stated as a table and enforced in :meth:`Watch.__post_init__`, because the alternative is
#: a watch that accepts a subscription it can never honor. A membership trigger on a
#: directory watch would be configured, saved, listed in the interface, and silent forever —
#: and silence from a monitoring feature reads as "nothing happened".
TRIGGERS_BY_WATCH_KIND: Final[dict[WatchKind, frozenset[AlertTrigger]]] = {
    WatchKind.RESOURCE: frozenset(
        {AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED, AlertTrigger.WATCHED_ACCESS_EXPANDED}
    ),
    WatchKind.SHARE: frozenset(
        {AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED, AlertTrigger.WATCHED_ACCESS_EXPANDED}
    ),
    WatchKind.GROUP: frozenset({AlertTrigger.WATCHED_GROUP_MEMBERSHIP_CHANGED}),
}


def triggers_for_kind(kind: WatchKind) -> frozenset[AlertTrigger]:
    """The triggers a watch of ``kind`` may subscribe to."""
    return TRIGGERS_BY_WATCH_KIND[kind]


class AlertStatus(StrEnum):
    """Whether the condition an alert describes still holds."""

    OPEN = "open"
    RESOLVED = "resolved"


class AlertTransition(StrEnum):
    """What happened to an alert. Every one of these is recorded; only some notify.

    The separation is the whole of the storm control: a suppressed repeat is *written down*
    and not delivered, so "we were not told" and "it did not happen" stay distinguishable
    afterwards.
    """

    RAISED = "raised"
    """First time this alert has ever existed. Notifies."""

    REOPENED = "reopened"
    """A resolved stateful alert became true again. Notifies."""

    REPEATED = "repeated"
    """The same alert was raised again with different content, outside its cooldown.
    Notifies, and carries the count of everything suppressed since the last notification."""

    SUPPRESSED = "suppressed"
    """Raised again inside the cooldown, or with content identical to what was already
    delivered. Recorded; does not notify."""

    RESOLVED = "resolved"
    """The condition ended. Notifies, and is never suppressed — see
    :mod:`app.alerts.dedupe`."""


#: Transitions that produce an outbox entry. The one list, so that adding a transition
#: without deciding whether it notifies is a change to this line rather than an omission.
NOTIFYING_TRANSITIONS: Final[frozenset[AlertTransition]] = frozenset(
    {
        AlertTransition.RAISED,
        AlertTransition.REOPENED,
        AlertTransition.REPEATED,
        AlertTransition.RESOLVED,
    }
)


class AlertSubject:
    """What an alert is about, as the keys a reader would go and look at.

    Deliberately the same shape as :class:`app.risk_engine.FindingSubject` and
    :class:`app.changes.SubjectRef` without being either of them: this package must not
    import the risk engine or the change classifier, because a sink that could reach into
    them would be a delivery channel that knows how a finding is computed.
    """

    __slots__ = ("principal_key", "resource_key", "share_key")

    def __init__(
        self,
        *,
        resource_key: str | None = None,
        share_key: str | None = None,
        principal_key: str | None = None,
    ) -> None:
        if not any((resource_key, share_key, principal_key)):
            raise DomainValidationError(
                "An alert must name at least one of a resource, a share or a principal. An "
                "alert about nothing in particular cannot be investigated, and cannot be "
                "deduplicated either: its identity would be the trigger alone, so every "
                "occurrence anywhere in the estate would fold into one.",
                field="subject",
            )
        self.resource_key = resource_key
        self.share_key = share_key
        self.principal_key = principal_key

    @property
    def canonical(self) -> str:
        """The rendering the alert key is computed over. Fixed order, blanks included."""
        return _SEPARATOR.join(
            part or "" for part in (self.resource_key, self.share_key, self.principal_key)
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AlertSubject):
            return NotImplemented
        return self.canonical == other.canonical

    def __hash__(self) -> int:
        return hash(self.canonical)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return (
            f"AlertSubject(resource_key={self.resource_key!r}, share_key={self.share_key!r}, "
            f"principal_key={self.principal_key!r})"
        )


def alert_key(trigger: AlertTrigger, subject: AlertSubject, discriminator: str | None) -> str:
    """The stable identity of one alert: what it is about, not what it says.

    A pure function of the trigger, the subject and a discriminator — never of the payload.
    That is what makes deduplication possible at all: the same condition in the same place
    has to be the same alert on Tuesday as it was on Monday, even though its payload carries
    a different instant and a different set of members. Putting the payload in the key would
    make every occurrence a new alert and turn the cooldown into decoration.

    ``discriminator`` separates several alerts of one trigger about one subject — the trustee
    whose entry moved, for instance, or the finding key — and is part of the identity, so two
    trustees edited on one ACL produce two alerts that are counted, suppressed and (where
    stateful) resolved independently.
    """
    material = _SEPARATOR.join((trigger.value, subject.canonical, discriminator or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def payload_digest(payload: Mapping[str, Any]) -> str:
    """A digest over an alert's content, used to tell a repeat from a duplicate.

    Canonical JSON — sorted keys, no insignificant whitespace — so that two payloads that
    say the same thing digest the same regardless of how they were assembled. The digest is
    what separates "this happened again" from "this is the same notification arriving
    twice", and those get different treatment: the first is delivered once the cooldown
    expires, the second never is.
    """
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Watch:
    """A standing subscription: tell me when this thing changes.

    Immutable. Editing one produces a new value; the repository writes it. A mutable watch
    would let a detection pass half-see an edit — some events matched against the old
    trigger set and some against the new — and an alert that fired under a subscription
    nobody held is indistinguishable from a bug.
    """

    __slots__ = (
        "cooldown",
        "created_at",
        "created_by",
        "enabled",
        "key",
        "kind",
        "label",
        "notes",
        "triggers",
        "updated_at",
        "watch_id",
    )

    def __init__(
        self,
        *,
        watch_id: UUID,
        kind: WatchKind,
        key: str,
        label: str,
        triggers: frozenset[AlertTrigger],
        cooldown: dt.timedelta,
        created_at: dt.datetime,
        updated_at: dt.datetime,
        created_by: str,
        enabled: bool = True,
        notes: str | None = None,
    ) -> None:
        if not key.strip():
            raise DomainValidationError("A watch must name the thing it watches.", field="key")
        if not label.strip():
            raise DomainValidationError(
                "A watch must carry a label. The key of a directory is a UNC path and the "
                "key of a group is a SID; an alert feed listing either one unlabeled is a "
                "feed nobody reads.",
                field="label",
            )
        if len(label) > MAX_LABEL_LENGTH:
            raise DomainValidationError(
                f"A watch label is limited to {MAX_LABEL_LENGTH} characters.", field="label"
            )
        if not triggers:
            raise DomainValidationError(
                "A watch that subscribes to no trigger notifies about nothing, which is "
                "indistinguishable in a list from one that is simply quiet. Disable it "
                "instead -- a disabled watch says so.",
                field="triggers",
            )
        allowed = triggers_for_kind(kind)
        unsupported = sorted(trigger.value for trigger in triggers - allowed)
        if unsupported:
            raise DomainValidationError(
                f"A {kind.value} watch cannot subscribe to {unsupported!r}. Nothing would "
                "ever match it, so the watch would be configured, listed, and silent "
                f"forever. A {kind.value} watch supports: "
                f"{sorted(trigger.value for trigger in allowed)!r}.",
                field="triggers",
            )
        if not MIN_COOLDOWN <= cooldown <= MAX_COOLDOWN:
            raise DomainValidationError(
                f"A cooldown must be between {MIN_COOLDOWN} and {MAX_COOLDOWN}; received "
                f"{cooldown}. Shorter is not a cooldown -- a script editing an ACL in a "
                "loop would produce an alert per edit. Longer would fold one day's change "
                "into the previous day's alert, where nobody reading today's feed sees it.",
                field="cooldown",
            )
        self.watch_id = watch_id
        self.kind = kind
        self.key = key
        self.label = label.strip()
        self.triggers = triggers
        self.cooldown = cooldown
        self.created_at = created_at
        self.updated_at = updated_at
        self.created_by = created_by
        self.enabled = enabled
        self.notes = notes

    def watches(self, trigger: AlertTrigger) -> bool:
        """Whether this watch would notify about ``trigger`` right now."""
        return self.enabled and trigger in self.triggers

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"Watch({self.kind.value} {self.key!r}, {sorted(t.value for t in self.triggers)})"


class AlertEvent:
    """One candidate alert, before anything has decided whether to deliver it.

    Produced by :mod:`app.alerts.detection` from facts, passed to :mod:`app.alerts.dedupe`
    for a decision, and only then written. Keeping detection and the decision apart is what
    makes both testable without a database: detection is *what happened*, the decision is
    *whether to say so again*, and conflating them produces a suppression rule that can only
    be exercised through a full ingestion.
    """

    __slots__ = (
        "digest",
        "discriminator",
        "key",
        "occurred_at",
        "payload",
        "subject",
        "summary",
        "trigger",
        "watch_id",
    )

    def __init__(
        self,
        *,
        trigger: AlertTrigger,
        subject: AlertSubject,
        occurred_at: dt.datetime,
        summary: str,
        payload: Mapping[str, Any],
        discriminator: str | None = None,
        watch_id: UUID | None = None,
    ) -> None:
        if occurred_at.tzinfo is None or occurred_at.tzinfo.utcoffset(occurred_at) is None:
            raise DomainValidationError(
                "An alert instant must be timezone-aware. A naive one cannot be ordered "
                "against a cooldown recorded by a server in another zone, and a cooldown an "
                "hour out either suppresses an alert that should have been delivered or "
                "delivers a storm it was configured to prevent.",
                field="occurred_at",
            )
        if not summary.strip():
            raise DomainValidationError(
                "An alert must carry a one-line summary. A delivery whose body is a payload "
                "and nothing else requires the recipient to hold ADG's data model in their "
                "head at whatever hour it arrives.",
                field="summary",
            )
        if watch_id is None and trigger not in UNWATCHED_TRIGGERS:
            raise DomainValidationError(
                f"{trigger.value} requires the watch that subscribed to it. An alert with "
                "no watch behind it cannot be silenced by removing a watch, which is the "
                "only control an operator has over it.",
                field="watch_id",
            )
        self.trigger = trigger
        self.subject = subject
        self.occurred_at = occurred_at
        self.summary = summary.strip()
        self.payload = dict(payload)
        self.discriminator = discriminator
        self.watch_id = watch_id
        self.key = alert_key(trigger, subject, discriminator)
        self.digest = payload_digest(self.payload)

    @property
    def lifecycle(self) -> AlertLifecycle:
        return TRIGGER_LIFECYCLE[self.trigger]

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"AlertEvent({self.trigger.value}, {self.key[:12]}…, {self.summary!r})"
