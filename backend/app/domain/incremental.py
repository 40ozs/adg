"""Incremental collection: what a run re-read, where it got to, and what that costs.

Three vocabularies live here, and all three are pure so that the rule deciding them is
testable without a directory, a file server, or a database.

**Mode** is what a run set out to do. It is not a synonym for ``incremental``: that flag
answers *may this run reconcile?* and mode answers *why not?*. The two are derived from
each other by :meth:`CollectionMode.is_incremental`, so a payload cannot claim a delta run
that is allowed to mark objects absent.

**Checkpoint** is where a delta run may resume from. The whole of its difficulty is that a
cursor means something only relative to whoever issued it: ``uSNChanged`` is a counter
local to one domain controller, and replaying DC1's number against DC2 silently skips every
object whose USN on DC2 happens to fall below it. So a checkpoint carries its issuer and
refuses to be compared across issuers, rather than being a bare number that looks usable
everywhere.

**Drift** is what a full reconciliation had to correct. It is defined narrowly on purpose:
only the absences. A delta run is *structurally* incapable of noticing that an object is
gone — nothing arrives to tell it — so every tombstone a reconciliation creates is a fact
the incremental cadence could never have caught by running more often. Counting changed
objects as drift as well would inflate it with ordinary churn that the deltas did catch, or
would have caught on their next pass, and an operator watching the number would learn
nothing from it.

See `docs/architecture/incremental-collection.md` and
[ADR-0025](../../../docs/decisions/0025-incremental-collection-is-bounded-by-its-source.md).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.domain.errors import DomainValidationError

__all__ = [
    "MAX_CHECKPOINT_TOKEN_LENGTH",
    "Checkpoint",
    "CheckpointKind",
    "CheckpointRejection",
    "CollectionMode",
    "ReconciliationDrift",
]

MAX_CHECKPOINT_TOKEN_LENGTH: Final = 512
"""A cursor is a resume point, not a payload. Anything longer is a collector storing state
on the server under another name, and the server cannot validate what it cannot interpret.
"""


class CollectionMode(StrEnum):
    """What a run set out to read.

    The three values are not three degrees of thoroughness; they are three different
    *claims about coverage*, and only the last two make one.
    """

    FULL = "full"
    """Every object in the declared scopes was read, and the run did not intend to skip
    any. It may reconcile if it also achieved that — see the completion envelope."""

    DELTA = "delta"
    """Only objects the source reported as changed since a checkpoint were read. Such a run
    never reconciles: an object deleted since the checkpoint produces no change record to
    read, so *nothing arrived* and *nothing exists* are indistinguishable from inside a
    delta."""

    RECONCILE = "reconcile"
    """A full read whose purpose is to repair what the deltas could not see. Identical to
    ``full`` in what it reads; distinct in why it ran, which is what lets an operator tell
    a scheduled repair from an ordinary scan when the drift number is non-zero."""

    @property
    def is_incremental(self) -> bool:
        """Whether the protocol's ``incremental`` flag must be set for this mode.

        The flag is the older, coarser statement and remains the one the reconciliation
        guard reads. Mode is the finer one layered over it. Deriving one from the other
        here means a payload can never carry a pair that contradicts itself.
        """
        return self is CollectionMode.DELTA

    @property
    def may_reconcile(self) -> bool:
        """Whether a *clean* run in this mode is permitted to mark objects absent."""
        return not self.is_incremental

    @property
    def reads_whole_scope(self) -> bool:
        return self in (CollectionMode.FULL, CollectionMode.RECONCILE)


class CheckpointKind(StrEnum):
    """What kind of cursor a checkpoint carries, and therefore how it may be compared."""

    USN = "usn"
    """A monotonic per-server counter — ``uSNChanged`` on a domain controller. Ordered, and
    meaningful only against the same issuer."""

    TIMESTAMP = "timestamp"
    """A replicated change time — ``whenChanged``. Ordered, but only to the second and only
    as well as the clocks involved, so a collector using one must re-read an overlap
    window rather than resuming exactly at it."""

    OPAQUE = "opaque"
    """A collector-private cursor the server stores and never interprets. Unordered: the
    server can tell that it changed, not that it moved forward."""

    @property
    def is_ordered(self) -> bool:
        return self is not CheckpointKind.OPAQUE


@dataclass(frozen=True, slots=True)
class CheckpointRejection:
    """Why a checkpoint may not replace the one already stored.

    A rejection is a value rather than an exception because the caller has to *record* it:
    a delta run whose checkpoint was refused has not failed, but it also has not advanced,
    and the next run must be told to read everything rather than resume from a cursor the
    server declined to move.
    """

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """How far a collector got, and who is entitled to believe it.

    ``issuer`` is the identity of whatever produced ``token`` — for a domain controller,
    its ``dsServiceName`` *and* its ``invocationId``, joined. Both are needed and neither
    is enough alone: the service name changes when the collector binds a different DC, and
    the invocation id changes when the same DC is restored from backup, which rolls its USN
    counter *backwards* and makes numbers already issued get issued again. A watermark
    compared only by server name would survive that restore and skip every object whose USN
    was reused.
    """

    kind: CheckpointKind
    token: str
    issuer: str
    issued_at: dt.datetime

    def __post_init__(self) -> None:
        for name, value in (("token", self.token), ("issuer", self.issuer)):
            if not value or not value.strip():
                raise DomainValidationError(
                    f"A checkpoint's {name} must not be empty. A cursor with no "
                    f"{'value' if name == 'token' else 'issuer'} cannot be resumed from "
                    "safely, and storing one would let the next run believe it had a "
                    "resume point when it has none.",
                    field=name,
                )
        if len(self.token) > MAX_CHECKPOINT_TOKEN_LENGTH:
            raise DomainValidationError(
                f"A checkpoint token may be at most {MAX_CHECKPOINT_TOKEN_LENGTH} "
                f"characters; received {len(self.token)}. A cursor is a resume point, not "
                "a place to keep collector state the server cannot interpret.",
                field="token",
            )
        if self.issued_at.tzinfo is None or self.issued_at.tzinfo.utcoffset(self.issued_at) is None:
            raise DomainValidationError(
                "A checkpoint's issued_at must be timezone-aware; a naive timestamp "
                "cannot be ordered against a cursor issued in another time zone.",
                field="issued_at",
            )
        if self.kind is CheckpointKind.USN and self.ordering_value is None:
            raise DomainValidationError(
                f"A usn checkpoint's token must be a non-negative integer; received "
                f"{self.token!r}. uSNChanged is a counter, and a token that does not parse "
                "as one cannot be compared to the stored watermark — so the server could "
                "neither accept nor refuse the next one on its merits.",
                field="token",
            )
        if self.kind is CheckpointKind.TIMESTAMP and self.ordering_value is None:
            raise DomainValidationError(
                f"A timestamp checkpoint's token must be an RFC 3339 timestamp with an "
                f"offset; received {self.token!r}.",
                field="token",
            )

    @property
    def ordering_value(self) -> int | dt.datetime | None:
        """The comparable form of ``token``, or ``None`` when there is not one."""
        if self.kind is CheckpointKind.USN:
            text = self.token.strip()
            if not text.isdigit():
                return None
            return int(text)
        if self.kind is CheckpointKind.TIMESTAMP:
            try:
                parsed = dt.datetime.fromisoformat(self.token.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return None
            return parsed.astimezone(dt.UTC)
        return None

    def comparable_to(self, other: Checkpoint) -> bool:
        """Whether these two cursors are measured on the same scale.

        Case-folded on the issuer because a DNS name is case-insensitive and a collector
        that reports ``DC01.corp.example.com`` one week and ``dc01.corp.example.com`` the
        next has not changed domain controller.
        """
        return (
            self.kind is other.kind
            and self.kind.is_ordered
            and self.issuer.casefold() == other.issuer.casefold()
        )

    def advances_over(self, previous: Checkpoint | None) -> CheckpointRejection | None:
        """``None`` when this checkpoint may replace ``previous``; the reason when not.

        Three outcomes, and the middle one is the interesting one:

        * no stored checkpoint, or an unordered kind — accepted. There is nothing to
          compare against, and refusing would leave a collector permanently unable to
          record its first cursor.
        * a different issuer — **refused**. The two numbers are not on the same scale, and
          silently taking the newer one is exactly the failure this class exists to
          prevent. The caller records the refusal and the next run reads everything.
        * the same issuer going backwards — **refused**. A cursor that moves backwards
          means either an out-of-order completion or a source that was rolled back, and in
          both cases the stored value is the one that describes more read data.
        """
        if previous is None:
            return None
        if not self.comparable_to(previous):
            if self.kind is not previous.kind:
                return CheckpointRejection(
                    code="checkpoint_kind_changed",
                    message=(
                        f"This checkpoint is a {self.kind.value} cursor and the stored one "
                        f"is {previous.kind.value}. The two are not measured on the same "
                        "scale, so the new one cannot be shown to be ahead of the old. The "
                        "next run must read its whole scope."
                    ),
                )
            if not self.kind.is_ordered:
                return None
            return CheckpointRejection(
                code="checkpoint_issuer_changed",
                message=(
                    f"This checkpoint was issued by {self.issuer!r} and the stored one by "
                    f"{previous.issuer!r}. A {self.kind.value} cursor is local to its "
                    "issuer, so the new value cannot be shown to be ahead of the old one. "
                    "The next run must read its whole scope rather than resume."
                ),
            )

        mine, theirs = self.ordering_value, previous.ordering_value
        if mine is None or theirs is None:  # pragma: no cover - forbidden by __post_init__
            return None
        if mine < theirs:  # type: ignore[operator]
            return CheckpointRejection(
                code="checkpoint_went_backwards",
                message=(
                    f"This checkpoint ({self.token}) is behind the stored one "
                    f"({previous.token}) from the same issuer. Accepting it would make the "
                    "next delta re-read a range already covered, or — if the source was "
                    "rolled back — treat reused cursor values as new ones. The stored "
                    "checkpoint is kept."
                ),
            )
        return None


@dataclass(frozen=True, slots=True)
class ReconciliationDrift:
    """What a full reconciliation found that the incremental cadence could not.

    **Only absences count.** A delta run reads what the source says changed; nothing in a
    directory or a file system announces a deletion to a query that filters on change
    metadata, so an object that is gone simply fails to appear — which is also what an
    unchanged object does. Every tombstone a reconciliation writes is therefore a fact no
    amount of extra delta runs would have produced.

    ``confirmed`` and ``superseded`` are carried for context and are deliberately *not*
    drift: an object whose state changed between two reconciliations may well have been
    caught by a delta in between, and counting it here would make ordinary churn look like
    a failure of the incremental strategy.
    """

    scope_kind: str
    scope_key: str
    marked_absent: int = 0
    revived: int = 0
    confirmed: int = 0
    superseded: int = 0
    delta_runs_since: int = 0

    def __post_init__(self) -> None:
        for name in ("marked_absent", "revived", "confirmed", "superseded", "delta_runs_since"):
            if getattr(self, name) < 0:
                raise DomainValidationError(f"{name} cannot be negative.", field=name)

    @property
    def drift(self) -> int:
        """Objects whose presence the deltas had wrong, in either direction."""
        return self.marked_absent + self.revived

    @property
    def is_clean(self) -> bool:
        return self.drift == 0

    def summary(self) -> str:
        """One sentence, written for the operator reading the collectors page."""
        scope = f"{self.scope_kind} {self.scope_key}"
        if self.is_clean:
            return (
                f"Reconciling {scope} found nothing the {self.delta_runs_since} "
                f"incremental run(s) since the last reconciliation had missed."
            )
        parts = []
        if self.marked_absent:
            parts.append(f"{self.marked_absent} object(s) no longer present")
        if self.revived:
            parts.append(f"{self.revived} object(s) present again after being marked absent")
        return (
            f"Reconciling {scope} recorded {' and '.join(parts)}. Incremental runs cannot "
            "observe an absence, so this is the cadence working as designed rather than a "
            "fault — but the estate was wrong by that much until this run."
        )
