r"""What a change is, and the four things ADG says about one.

Phase 7A gave every object a timeline. A timeline answers *what was true on the 3rd*; it
does not answer *what changed since yesterday*, because that question is about the estate
rather than about an object, and because the raw transitions in a timeline are not yet
findings. An operator opening this page has one question — **is any of this a problem?** —
and a list of two thousand version rows does not answer it.

This module is the vocabulary that does. Nothing here touches a database, and nothing here
reads a clock: a change is a value computed from two versions, and every judgment about it
is a pure function of those two values plus the tables in :mod:`app.changes.fields` and
:mod:`app.changes.principals`.

## Four independent things, not one score

A change carries four judgments and they are deliberately not folded together, because
collapsing them is how an audit tool starts lying:

* :class:`ChangeAction` — **what happened to the object**: it appeared, it changed, it was
  found gone, or ADG simply started watching it.
* :class:`ChangeSignificance` — **whether it is about access at all.** A display name and an
  ACE are both stored state; only one of them is a permission.
* :class:`ChangeDirection` — **which way access moved.** Broadened and narrowed are not the
  same finding even at identical severity, and an operator triaging a week of changes reads
  this column first.
* :class:`ChangeSeverity` — **how much attention it deserves**, derived from the other
  three plus who the change is about.

A single severity number would have to fold direction into itself, and the folding is
lossy in the direction that matters: "Everyone was granted Full Control" and "Everyone's
Full Control was removed" are the same objects, the same fields and the same magnitude, and
one of them is an incident.

## Every one of them has an "ADG cannot tell" value, and it is never the quiet one

:attr:`ChangeSignificance.UNDETERMINED` and :attr:`ChangeDirection.UNDETERMINED` exist for
the same reason :attr:`app.history.model.Certainty.UNOBSERVED` does. A change ADG cannot
classify must not be classified as harmless, because the whole point of the page is that
somebody stops reading once the list looks clean. An undetermined change is scored at
:attr:`ChangeSeverity.MEDIUM` and is included by the default filter, so the cost of ADG not
understanding something is a line an operator reads rather than a line nobody sees.

## A change happened in a window, never at an instant

:attr:`ObjectChange.window` is :class:`app.history.model.ChangeWindow` — both ends, always
(ADR-0019). A collector samples; the instant in ``valid_from`` is the instant somebody
looked, and rendering it as the moment of the change dates an incident to a scan schedule.
:attr:`ObjectChange.window` is ``None`` for :attr:`ChangeAction.FIRST_OBSERVED`, because
there is no earlier observation to bound it and an invented lower bound would be worse than
no answer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError
from app.history.model import ChangeWindow, ObjectVersion, VersionOrigin

__all__ = [
    "SEVERITY_ORDER",
    "ChangeAction",
    "ChangeDirection",
    "ChangeSeverity",
    "ChangeSignificance",
    "ChangeSummary",
    "FieldDelta",
    "FieldSignificance",
    "ObjectChange",
    "SubjectRef",
    "at_least",
]


class ChangeAction(StrEnum):
    """What happened to the object itself."""

    ADDED = "added"
    """The object appeared somewhere ADG was already watching, or came back after a
    measured absence. This is a creation: something that was not there is there now."""

    MODIFIED = "modified"
    """The object's state changed. Its identity — and therefore its key — did not."""

    REMOVED = "removed"
    """A scan that reconciled a scope containing this object did not find it. This is the
    only action that rests on an inference, and it is the most heavily guarded thing in the
    product: see :mod:`app.history.closure`. A failed, partial or incremental run can never
    produce one."""

    FIRST_OBSERVED = "first_observed"
    """ADG had no prior view of the thing that contains this object, so its appearance in
    the record is the beginning of observation and **not evidence of a creation**.

    Rendered as "first seen", never as "added". The first scan of an estate produces one of
    these per object, and an interface that called them additions would report an entire
    estate as having been created on a Tuesday. :func:`app.changes.classify.classify` decides
    between this and :attr:`ADDED` by looking at the container, not at the object."""


class ChangeSignificance(StrEnum):
    """Whether a change is about access, about description, or about nothing at all."""

    SECURITY = "security"
    """It alters who can do what, or who could come to be able to. An ACE, a membership, an
    owner, a NULL DACL, an inheritance flag, whether an account can log on at all."""

    METADATA = "metadata"
    """A real change to a descriptive attribute with no access consequence ADG can name: a
    display name, a share remark, an operating-system string, a count ADG derives."""

    NOISE = "noise"
    """A difference in the stored state that carries no information about the object.

    Two things reach here. One is provenance — ``source_key`` differs between two runs that
    read identical facts. The other is **position without consequence**: an ACE renumbered
    because an entry above it was removed, where the normalized ACL is byte-identical
    either side. The second is the one that matters, and deciding it is
    :mod:`app.changes.correlation`'s job rather than a guess from the field name."""

    UNDETERMINED = "undetermined"
    """ADG cannot say which of the above this is. Never filtered out by default."""


class ChangeDirection(StrEnum):
    """Which way access moved, as far as the change itself can say.

    This is a statement about the *change*, not about the estate: "broadened" means this
    edit grants something it did not grant before, not that anybody's effective access
    actually rose. Whether it did is an access question, answered by
    :mod:`app.changes.impact` over the real engine, and the two are kept apart on purpose —
    an Allow added below a Deny broadens the ACL and changes nobody's access.
    """

    BROADENED = "broadened"
    NARROWED = "narrowed"
    MIXED = "mixed"
    """Both, in one change: an ACL that gained an Allow and lost a Deny at once."""

    NEUTRAL = "neutral"
    """Nothing about access moved. A metadata edit, or an ordering the normal form says is
    not a difference."""

    UNDETERMINED = "undetermined"
    """The change is about access and ADG cannot say which way. Un-protecting a directory
    is the type case: it starts inheriting its parent's entries, and those may be Allows,
    Denies, or both."""


class ChangeSeverity(StrEnum):
    """How much attention a change deserves, from the change alone.

    Deliberately **not** a risk score. A risk score is a property of the estate as it now
    stands and is Phase 8's subject; this is a property of one transition, computed from
    what moved, in which direction, and about whom. The two will disagree, correctly: an
    Everyone/Full-Control ACE that has been in place for three years is a high risk and no
    change at all.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


SEVERITY_ORDER: Final[tuple[ChangeSeverity, ...]] = (
    ChangeSeverity.INFO,
    ChangeSeverity.LOW,
    ChangeSeverity.MEDIUM,
    ChangeSeverity.HIGH,
    ChangeSeverity.CRITICAL,
)
"""Least to most severe. The only ordering; ``StrEnum`` sorts alphabetically, which would
put ``critical`` below ``info`` and make a "at least high" filter return the wrong set."""


def at_least(severity: ChangeSeverity, floor: ChangeSeverity) -> bool:
    """Whether ``severity`` meets a minimum, by :data:`SEVERITY_ORDER` and not by name."""
    return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(floor)


class FieldSignificance(StrEnum):
    """What one field of one object kind means when it moves.

    The per-kind table is in :mod:`app.changes.fields`, and it is exhaustive over the
    columns each kind actually stores — checked by a test, so a column added to a
    current-state table fails the suite until somebody decides what a change to it means.
    The alternative is a new column defaulting to "not security relevant", which is the one
    default this product must never have.
    """

    IDENTITY = "identity"
    """Part of the object's key. It cannot move without the object becoming a different
    object, so a delta on one is a defect rather than a change — see
    :meth:`ObjectChange.identity_drift`."""

    SECURITY = "security"
    METADATA = "metadata"
    NOISE = "noise"
    ORDER = "order"
    """Position within an ACL. Whether it is :attr:`ChangeSignificance.NOISE` or
    :attr:`ChangeSignificance.SECURITY` depends on the other entries, so the field table
    cannot decide it alone and says so rather than guessing."""

    DERIVED = "derived"
    """ADG's own conclusion about the object, recomputed from collected facts rather than
    collected. It moves because something else moved, and that something else is its own
    change. Reported, never scored: scoring it would double-count the cause."""

    UNCLASSIFIED = "unclassified"
    """:data:`app.changes.fields.FIELD_SIGNIFICANCE` has no entry for this column.

    A value rather than ``None``, so that a delta is always fully typed and the "ADG has no
    opinion" case has to be handled by anything that branches on significance. It is scored
    at :attr:`ChangeSeverity.MEDIUM` and shown by default, which is the opposite of what a
    silent default would do. ``tests/changes/test_fields.py`` asserts no column of any bound
    table reaches it, so in a sound build it is unreachable — and it exists for the build
    that is not sound yet."""


@dataclass(frozen=True, slots=True)
class SubjectRef:
    """A thing a change is *about*, for a reader who thinks in resources and people.

    An ``ntfs_ace`` change is really "the ACL of ``\\\\fs01\\finance`` changed, for
    ``Finance-RW``". The object key is the truth and this is how it reads: ``container`` is
    the thing whose state an operator would go and look at, ``related`` is the principal the
    change is about when there is one.
    """

    container_kind: ObservationKind | None
    container_key: str | None
    related_kind: ObservationKind | None
    related_key: str | None


@dataclass(frozen=True, slots=True)
class FieldDelta:
    """One field's before and after, with what moving it means.

    ``before`` and ``after`` are the stored JSON values, not rendered ones. A client shows
    them; nothing in ADG compares them again, because the comparison already happened here
    and a second one would be a second implementation.
    """

    field: str
    before: Any
    after: Any
    significance: FieldSignificance

    def __post_init__(self) -> None:
        if self.before == self.after:
            raise DomainValidationError(
                f"A delta was built for {self.field} with equal values on both sides. An "
                "unchanged field in a change list is noise that trains a reader to skim, "
                "which is the one reading habit this page cannot afford.",
                field="field",
            )


@dataclass(frozen=True, slots=True)
class ObjectChange:
    """One transition of one object, classified.

    Built by :func:`app.changes.classify.classify` from two versions and nothing else, so
    the same two versions always produce the same change — including its severity. A
    classification that depended on when it was asked could not be quoted in a finding.
    """

    kind: ObservationKind
    key: str
    action: ChangeAction
    window: ChangeWindow | None
    """When the change happened, as the interval it is known to have happened inside.

    ``None`` for :attr:`ChangeAction.FIRST_OBSERVED`, which by definition has nothing
    earlier to bound it. Present for every other action — including an addition, whose lower
    bound comes from the last reading of the container it appeared in rather than from a
    predecessor it does not have. See :func:`app.changes.classify.window_between`."""

    before: ObjectVersion | None
    """The version that held immediately before, or ``None`` when ADG holds none."""

    after: ObjectVersion
    """The version that holds from the transition onward. Never ``None``: a tombstone is a
    real version, so even a removal has a resulting state. A change with no resulting state
    would be a gap in observation, and a gap is reported as a gap
    (:class:`app.history.repository.Presence`) rather than as a change."""

    deltas: tuple[FieldDelta, ...]
    significance: ChangeSignificance
    direction: ChangeDirection
    severity: ChangeSeverity
    subject: SubjectRef
    reasons: tuple[str, ...]
    """Why this severity and this direction, in sentences, in the order the rules fired.

    Carried rather than recomputed by a client for the reason the derived-response contract
    states once and applies everywhere: a client that re-derives a conclusion is a second
    implementation of it, and the two will disagree on the day it matters."""

    rule_ids: tuple[str, ...] = ()
    """The identifiers of the rules that fired, for a test or an operator to grep for."""

    def __post_init__(self) -> None:
        if self.action is ChangeAction.FIRST_OBSERVED and self.window is not None:
            raise DomainValidationError(
                f"A first observation of {self.key} carries a change window. A first "
                "observation is precisely the case where nothing was watching the thing "
                "that contains it, so no earlier confirmation exists to bound it — and an "
                "invented lower bound reads exactly like a measured one (ADR-0019).",
                field="window",
            )
        # Checked before the window rule below, because both fire on the same malformed
        # record and only this one names the actual mistake. A "first observation" that
        # follows a version is a real transition wearing the one label a reader is told to
        # skip, and being told it is missing a window would send the fix to the wrong place.
        if self.action is ChangeAction.FIRST_OBSERVED and self.before is not None:
            raise DomainValidationError(
                f"A first observation of {self.key} follows an earlier version of the same "
                "object. It is therefore not a first observation, and calling it one would "
                "hide a real transition behind the one action a reader is told to ignore.",
                field="action",
            )
        if self.before is not None and self.window is None:
            raise DomainValidationError(
                f"A {self.action.value} change to {self.key} follows a version ADG holds "
                "and carries no window. Every transition bounded by an earlier observation "
                "must report both ends of that bound (ADR-0019).",
                field="window",
            )

    @property
    def at(self) -> dt.datetime:
        """The instant this change entered the record: when the newer version opened.

        The **observation** time, not the change time — which is why it is named for what
        it is and why :attr:`window` exists beside it. It is what the feed sorts and pages
        on, because it is the only one of the two that is a single ordered value.
        """
        return self.after.valid_from

    @property
    def reconstructed(self) -> bool:
        """Whether either side came from the Phase 7A backfill rather than an observation.

        A backfilled version spans the whole interval the pre-history schema knew about and
        conceals any state the object held inside it, so a transition touching one is a
        transition ADG inferred from two endpoints rather than one it watched.
        """
        return any(
            version is not None and version.origin is VersionOrigin.BACKFILLED
            for version in (self.before, self.after)
        )

    @property
    def is_security_relevant(self) -> bool:
        return self.significance in (ChangeSignificance.SECURITY, ChangeSignificance.UNDETERMINED)

    @property
    def identity_drift(self) -> tuple[FieldDelta, ...]:
        """Deltas on fields that are part of the object's key.

        Always empty in a sound record: the key is derived from these values, so two states
        differing in one of them are two objects. A non-empty result means a key derivation
        and a stored column have come apart, which is a defect in ingestion rather than a
        change in the estate — and it is surfaced rather than dropped, because dropping it
        would hide the only symptom it has.
        """
        return tuple(
            delta for delta in self.deltas if delta.significance is FieldSignificance.IDENTITY
        )

    def significant_deltas(self) -> tuple[FieldDelta, ...]:
        """The deltas a reader should see first: security and order, then everything else."""
        rank = {
            FieldSignificance.SECURITY: 0,
            FieldSignificance.ORDER: 1,
            FieldSignificance.UNCLASSIFIED: 2,
            FieldSignificance.IDENTITY: 3,
            FieldSignificance.DERIVED: 4,
            FieldSignificance.METADATA: 5,
            FieldSignificance.NOISE: 6,
        }
        return tuple(sorted(self.deltas, key=lambda delta: (rank[delta.significance], delta.field)))


@dataclass(frozen=True, slots=True)
class ChangeSummary:
    """Counts over a window, across every change in it — including what a filter hid.

    The last clause is the point. The feed's default filter excludes metadata and noise, and
    an operator who cannot see *how much* was excluded has been shown a filtered view that
    looks like a complete one. Every tally here is taken before filtering; ``returned`` is
    what the filter let through.
    """

    window_from: dt.datetime
    window_to: dt.datetime
    total: int
    returned: int
    by_action: Mapping[ChangeAction, int]
    by_significance: Mapping[ChangeSignificance, int]
    by_severity: Mapping[ChangeSeverity, int]
    by_kind: Mapping[ObservationKind, int]
    reconstructed: int
    """How many of the counted changes rest on a backfilled version."""

    truncated: bool = False
    """Whether more changes exist in the window than the summary was allowed to count."""

    @property
    def excluded(self) -> int:
        return max(self.total - self.returned, 0)

    @property
    def highest(self) -> ChangeSeverity | None:
        """The most severe change in the window, or ``None`` when there were none."""
        for severity in reversed(SEVERITY_ORDER):
            if self.by_severity.get(severity):
                return severity
        return None


@dataclass
class SummaryTally:
    """A mutable accumulator that becomes a :class:`ChangeSummary`.

    Separate from the frozen result so that counting a stream of changes does not rebuild
    six mappings per item, and so that the thing a service passes around cannot be mistaken
    for a finished answer.
    """

    by_action: dict[ChangeAction, int] = field(default_factory=dict)
    by_significance: dict[ChangeSignificance, int] = field(default_factory=dict)
    by_severity: dict[ChangeSeverity, int] = field(default_factory=dict)
    by_kind: dict[ObservationKind, int] = field(default_factory=dict)
    total: int = 0
    returned: int = 0
    reconstructed: int = 0
    truncated: bool = False

    def count(self, change: ObjectChange, *, returned: bool) -> None:
        self.total += 1
        self.returned += 1 if returned else 0
        self.reconstructed += 1 if change.reconstructed else 0
        _bump(self.by_action, change.action)
        _bump(self.by_significance, change.significance)
        _bump(self.by_severity, change.severity)
        _bump(self.by_kind, change.kind)

    def finish(self, window_from: dt.datetime, window_to: dt.datetime) -> ChangeSummary:
        return ChangeSummary(
            window_from=window_from,
            window_to=window_to,
            total=self.total,
            returned=self.returned,
            by_action=dict(self.by_action),
            by_significance=dict(self.by_significance),
            by_severity=dict(self.by_severity),
            by_kind=dict(self.by_kind),
            reconstructed=self.reconstructed,
            truncated=self.truncated,
        )


def _bump(counts: dict[Any, int], key: Any) -> None:
    counts[key] = counts.get(key, 0) + 1


def newest_first(changes: Sequence[ObjectChange]) -> tuple[ObjectChange, ...]:
    """Changes ordered newest observation first, which is how a feed is read."""
    return tuple(sorted(changes, key=lambda change: change.at, reverse=True))
