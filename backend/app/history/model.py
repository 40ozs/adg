r"""The temporal model: what a version is, when it was true, and how sure ADG is.

Phases 0 through 6 stored the *latest* state of every object and the provenance of the
observation that produced it. That answers "what is true now" and "who said so". It cannot
answer "what was true on the 3rd", and it cannot answer "when did this stop being true",
because a newer observation overwrites the older one in place.

This module is the vocabulary for the answer. Nothing here touches a database: a version is
a value, its invariants are checks on that value, and both are testable without PostgreSQL.

## A version is a state, and an interval it was observed over

One row of ``object_versions`` says: *this object held this state, from ``valid_from``,
last confirmed at ``last_seen_at``, until ``valid_to``.* Three timestamps rather than two,
and the third is the point of the whole module.

**A collector samples; it does not watch.** If a scan on Monday saw a share ACL and a scan
on Friday saw a different one, the change happened somewhere in between and ADG does not
know where. A model with only ``valid_from`` and ``valid_to`` has nowhere to put that
ignorance, so it invents a precision it does not have: it says the change happened Friday,
which is merely the day somebody looked. This model records both ends —

* ``last_seen_at``: the newest observation that *confirmed* this state;
* ``valid_to``: the observation that *contradicted* it —

so the honest statement is available: the state ended somewhere in
``(last_seen_at, valid_to]``. That interval is :attr:`ObjectVersion.change_window`, and it
is what :meth:`ObjectVersion.certainty_at` reports on.

## Absence is a state, not a missing row

A tombstone — ``is_present`` false, ``state`` null — is a real version with a real interval.
It has to be, because "ADG knows this share was gone on Tuesday" and "ADG has nothing about
this share on Tuesday" are different answers, and an audit tool that renders them the same
way is lying about one of them. A gap between versions means *nobody looked*; a tombstone
means *somebody looked and it was not there*.

Only a reconciled scope may open a tombstone; see :mod:`app.history.closure`.

## Everything before Phase 7 is marked as such

The migration backfills one version per existing row, spanning ``first_observed_at`` to
``last_observed_at``. That is the most the old schema can support, and it is not the same
claim as an observed version: an object that changed twice between those two instants
produced one row then and produces one version now. Backfilled versions carry
:attr:`VersionOrigin.BACKFILLED` and every answer drawn from one reports
:attr:`Certainty.BACKFILLED`, so a reader is never told that a reconstructed interval was
watched.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError
from app.models.schema import STATE_DIGEST_LENGTH, CloseReason, VersionOrigin

__all__ = [
    "HISTORICAL_KINDS",
    "PROVENANCE_FIELDS",
    "REDUNDANT_FIELDS",
    "STATE_DIGEST_LENGTH",
    "Certainty",
    "ChangeWindow",
    "CloseReason",
    "ObjectTimeline",
    "ObjectVersion",
    "TemporalInvariantError",
    "VersionOrigin",
    "canonical_state",
    "digestible_state",
    "state_digest",
    "stored_state",
]

HISTORICAL_KINDS: Final[frozenset[ObservationKind]] = frozenset(ObservationKind)
"""Every kind under temporal tracking — which is every kind a collector may send.

Deliberately derived from the contract enum rather than listed. A kind added to the
contract and not to history would be stored with no version record at all, and the gap
would be invisible: current state would look complete and the timeline would simply have
nothing in it. Deriving the set means a new kind is tracked by default and a kind that
must *not* be tracked has to be excluded on purpose.
"""

REDUNDANT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "first_observed_at",
        "first_observed_run_id",
        "last_observed_at",
        "last_observed_run_id",
        "created_at",
        "updated_at",
    }
)
"""Columns a version already carries, in better form, in its own interval.

They are not stored in ``state``: ``first_observed_at`` is the version's ``valid_from``,
``last_observed_at`` is its ``last_seen_at``, and keeping a second copy inside the blob would
be a value that can disagree with the row holding it. :func:`app.history.repository.as_row`
puts them back from the version when a record is rebuilt.
"""

PROVENANCE_FIELDS: Final[frozenset[str]] = REDUNDANT_FIELDS | {"source_key"}
"""Everything the digest ignores: the redundant columns, plus the observation key.

``source_key`` looks descriptive and is not. It is the contract's key for the *observation*,
so it carries the run's own identity and differs between two runs that read identical facts.
Digesting it would make every scan a change, and a history that recorded a version per scan
would be a scan log wearing a history's clothes.

It is still **stored** (see :func:`stored_state`), because a record rebuilt from a version
has to be able to say which observation produced it. The value kept is the one from the run
that *opened* the version -- the observation that first showed this state, which is the one
that matters when the question is when the state began.
"""


class Certainty(StrEnum):
    """How firmly a point-in-time answer is grounded, from the reader's point of view."""

    OBSERVED = "observed"
    """The instant asked about lies between two observations of this very state."""

    INFERRED = "inferred"
    """The instant lies in a change window: after the last confirmation of this state and
    at or before the observation that ended it — or after the last confirmation of a state
    still believed current. The state is the best available answer and was not watched at
    that instant."""

    BACKFILLED = "backfilled"
    """The version was reconstructed by the migration. See :attr:`VersionOrigin.BACKFILLED`."""

    UNOBSERVED = "unobserved"
    """No version covers the instant. Nobody had looked yet, or a gap in coverage means
    nobody was looking. This is never rendered as "it did not exist"."""


class TemporalInvariantError(DomainValidationError):
    """A version violates an invariant that makes history readable.

    Raised by :meth:`ObjectVersion.validate`, which the writer calls before every insert.
    The database enforces the same rules with check constraints; this exists so that a
    violation is caught with a sentence explaining what it would break, rather than as a
    constraint name in a driver traceback.
    """


def canonical_state(state: Mapping[str, Any]) -> str:
    """Render a state mapping to the one string that represents it.

    Provenance fields are dropped (:data:`PROVENANCE_FIELDS`). Keys are sorted and
    separators are tight, so two dictionaries built in different orders — which is what
    happens when one comes from an ingestion plan and the other from a JSONB column —
    render identically.

    Values are normalized rather than trusted to serialize: a ``UUID`` and a ``datetime``
    both stringify, but ``json.dumps`` refuses them, and a version whose digest depends on
    which code path built the mapping is a version that reports a change on every scan.
    """
    return json.dumps(
        digestible_state(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def digestible_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """What the digest is taken over: everything in :data:`PROVENANCE_FIELDS` dropped."""
    return {key: _digestible(value) for key, value in state.items() if key not in PROVENANCE_FIELDS}


def stored_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """What goes into the ``state`` column: one field wider than the digested form.

    The difference is ``source_key`` alone, and it is safe precisely because
    :func:`state_digest` strips it again: digesting a stored state reproduces that state's
    own hash, so the invariant "a version's digest is the digest of its own state" holds
    over the stored blob and is checked, not assumed
    (:meth:`ObjectVersion.validate`).
    """
    return {key: _digestible(value) for key, value in state.items() if key not in REDUNDANT_FIELDS}


def state_digest(state: Mapping[str, Any]) -> str:
    """The SHA-256 of :func:`canonical_state`, which is what "did this change?" compares."""
    return hashlib.sha256(canonical_state(state).encode("utf-8")).hexdigest()


def _digestible(value: Any) -> Any:
    """One JSON-representable form per value, chosen so the digest is stable.

    ``datetime`` is pinned to UTC before formatting for the reason stated throughout this
    codebase: the same instant read back under a different session time zone must not
    digest differently.
    """
    if isinstance(value, dt.datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
        return moment.astimezone(dt.UTC).isoformat(timespec="microseconds")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


@dataclass(frozen=True, slots=True)
class ChangeWindow:
    """The interval a transition is known to have happened inside.

    Half-open at the start and closed at the end: ``after`` is an instant the old state was
    confirmed, so the change came strictly later; ``at_or_before`` is the instant the new
    state was seen, so the change had happened by then.
    """

    after: dt.datetime
    at_or_before: dt.datetime

    @property
    def duration(self) -> dt.timedelta:
        """How wide the ignorance is. Zero when two observations bracket the change exactly."""
        return self.at_or_before - self.after

    @property
    def is_exact(self) -> bool:
        """Whether the transition is pinned to one instant rather than an interval."""
        return self.at_or_before == self.after

    def contains(self, moment: dt.datetime) -> bool:
        return self.after < moment <= self.at_or_before


@dataclass(frozen=True, slots=True)
class ObjectVersion:
    """One state of one object, over the interval it was observed to hold.

    The identity is ``(kind, key, valid_from)``; at most one version per object is open at
    a time, and the database enforces both (see ``object_versions``).
    """

    kind: ObservationKind
    key: str
    is_present: bool
    valid_from: dt.datetime
    last_seen_at: dt.datetime
    valid_to: dt.datetime | None
    state: Mapping[str, Any] | None
    state_hash: str | None
    origin: VersionOrigin
    close_reason: CloseReason | None
    opened_by_run_id: UUID
    last_seen_run_id: UUID
    closed_by_run_id: UUID | None
    container_key: str | None = None
    related_key: str | None = None
    version_id: int | None = None

    @property
    def is_open(self) -> bool:
        """Whether this is the version currently believed to hold."""
        return self.valid_to is None

    @property
    def is_tombstone(self) -> bool:
        """Whether this version records a *measured* absence rather than a state."""
        return not self.is_present

    @property
    def change_window(self) -> ChangeWindow | None:
        """When this version ended, as an interval. ``None`` while it is still open."""
        if self.valid_to is None:
            return None
        return ChangeWindow(after=self.last_seen_at, at_or_before=self.valid_to)

    def covers(self, moment: dt.datetime) -> bool:
        """Whether this version is the one that answers for ``moment``.

        Half-open, ``[valid_from, valid_to)``. The instant a version closes is the instant
        the next one opens, and a closed interval at both ends would make two versions
        answer for it.
        """
        if moment < self.valid_from:
            return False
        return self.valid_to is None or moment < self.valid_to

    def certainty_at(self, moment: dt.datetime) -> Certainty:
        """How firm this version's answer is for ``moment``.

        ``UNOBSERVED`` when the version does not cover the instant at all, so a caller that
        forgets to check :meth:`covers` cannot accidentally report a confident answer for an
        interval this version says nothing about.
        """
        if not self.covers(moment):
            return Certainty.UNOBSERVED
        if self.origin is VersionOrigin.BACKFILLED:
            return Certainty.BACKFILLED
        if moment <= self.last_seen_at:
            return Certainty.OBSERVED
        return Certainty.INFERRED

    def validate(self) -> ObjectVersion:
        """Check every temporal invariant, or raise with the reason it matters.

        Returns ``self`` so it can be used inline at a construction site.
        """
        if self.kind not in HISTORICAL_KINDS:
            raise TemporalInvariantError(
                f"{self.kind} is not a historically tracked object kind.",
                value=str(self.kind),
                field="kind",
            )
        for name in ("valid_from", "last_seen_at", "valid_to"):
            moment: dt.datetime | None = getattr(self, name)
            if moment is not None and (
                moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None
            ):
                raise TemporalInvariantError(
                    f"{name} must be timezone-aware; a naive timestamp cannot be ordered "
                    "against observations from a host in another time zone.",
                    field=name,
                )
        if self.last_seen_at < self.valid_from:
            raise TemporalInvariantError(
                f"A version of {self.key} was last confirmed at {self.last_seen_at.isoformat()}, "
                f"before it began at {self.valid_from.isoformat()}. The confirming "
                "observation is what opens the version, so it can never predate it.",
                field="last_seen_at",
            )
        if self.valid_to is not None and self.valid_to < self.last_seen_at:
            raise TemporalInvariantError(
                f"A version of {self.key} was closed at {self.valid_to.isoformat()} but "
                f"confirmed at {self.last_seen_at.isoformat()}. Closing a version before its "
                "newest confirmation would claim a state ended while something was still "
                "observing it.",
                field="valid_to",
            )
        if (self.valid_to is None) != (self.close_reason is None):
            raise TemporalInvariantError(
                f"A version of {self.key} has valid_to={self.valid_to!r} and "
                f"close_reason={self.close_reason!r}. A closed version must say why it "
                "closed — 'superseded' and 'absent' are different findings — and an open "
                "one must not claim a reason for an ending that has not happened.",
                field="close_reason",
            )
        if (self.valid_to is None) != (self.closed_by_run_id is None):
            raise TemporalInvariantError(
                f"A version of {self.key} has valid_to={self.valid_to!r} and "
                f"closed_by_run_id={self.closed_by_run_id!r}. Every ending is attributable "
                "to the run that observed it; an ending nobody is accountable for cannot be "
                "audited.",
                field="closed_by_run_id",
            )
        if self.is_present != (self.state is not None):
            raise TemporalInvariantError(
                f"A version of {self.key} has is_present={self.is_present} and "
                f"state={'a mapping' if self.state is not None else 'null'}. A tombstone "
                "carries no state, and a present object always carries one.",
                field="state",
            )
        if self.is_present != (self.state_hash is not None):
            raise TemporalInvariantError(
                f"A version of {self.key} has is_present={self.is_present} and "
                f"state_hash={self.state_hash!r}. The digest is what decides whether the "
                "next observation is a change, so a present version without one would "
                "re-open on every scan.",
                field="state_hash",
            )
        if self.state is not None and self.state_hash != state_digest(self.state):
            raise TemporalInvariantError(
                f"A version of {self.key} carries a state_hash that is not the digest of "
                "its own state. The two are read by different queries — one compares, one "
                "renders — and a disagreement makes them answer differently.",
                field="state_hash",
            )
        return self


@dataclass(frozen=True, slots=True)
class ObjectTimeline:
    """Every version of one object, oldest first.

    Adjacent versions are what makes the *start* of a state as answerable as its end: the
    change window that opened version *n* is bounded by version *n-1*'s last confirmation,
    which no single version knows on its own.
    """

    kind: ObservationKind
    key: str
    versions: tuple[ObjectVersion, ...]
    truncated: bool = False
    """Whether older versions exist beyond the page that was read. An answer drawn from a
    truncated timeline is still correct about the versions it holds; it is only incomplete
    about what came before them."""

    def __post_init__(self) -> None:
        previous: ObjectVersion | None = None
        for version in self.versions:
            if version.kind is not self.kind or version.key != self.key:
                raise TemporalInvariantError(
                    f"A timeline for {self.kind}/{self.key} was given a version of "
                    f"{version.kind}/{version.key}.",
                    field="versions",
                )
            if previous is not None:
                if previous.valid_to is None:
                    raise TemporalInvariantError(
                        f"The timeline of {self.key} has an open version followed by another "
                        "version. At most one version of an object is open, and it is the "
                        "last one.",
                        field="versions",
                    )
                if version.valid_from < previous.valid_to:
                    raise TemporalInvariantError(
                        f"Two versions of {self.key} overlap: one runs to "
                        f"{previous.valid_to.isoformat()} and the next begins at "
                        f"{version.valid_from.isoformat()}. Overlapping versions make "
                        "'what was true then' return two contradictory answers.",
                        field="versions",
                    )
            previous = version

    def __len__(self) -> int:
        return len(self.versions)

    def __iter__(self) -> Iterator[ObjectVersion]:
        return iter(self.versions)

    @property
    def current(self) -> ObjectVersion | None:
        """The open version, if the timeline reaches the present."""
        if self.versions and self.versions[-1].is_open:
            return self.versions[-1]
        return None

    def at(self, moment: dt.datetime) -> ObjectVersion | None:
        """The version covering ``moment``, or ``None`` when nobody had looked."""
        for version in self.versions:
            if version.covers(moment):
                return version
        return None

    def opened_window(self, version: ObjectVersion) -> ChangeWindow | None:
        """When ``version`` *began*, as an interval bounded by its predecessor.

        ``None`` for the first version in the timeline: there is no earlier observation to
        bound it, so its start is known only as "at or before ``valid_from``" — which is
        the absence of an answer, and is reported as such rather than as a window whose
        lower bound was invented.
        """
        index = self.versions.index(version)
        if index == 0:
            return None
        previous = self.versions[index - 1]
        return ChangeWindow(after=previous.last_seen_at, at_or_before=version.valid_from)

    def changes(self) -> tuple[tuple[ObjectVersion, ObjectVersion, ChangeWindow], ...]:
        """Every transition in the timeline, as ``(from, to, when)``."""
        transitions = []
        for previous, following in zip(self.versions, self.versions[1:], strict=False):
            transitions.append(
                (
                    previous,
                    following,
                    ChangeWindow(after=previous.last_seen_at, at_or_before=following.valid_from),
                )
            )
        return tuple(transitions)


def newest_first(versions: Sequence[ObjectVersion]) -> tuple[ObjectVersion, ...]:
    """Versions ordered newest to oldest, which is how a timeline is read by a human."""
    return tuple(sorted(versions, key=lambda version: version.valid_from, reverse=True))
