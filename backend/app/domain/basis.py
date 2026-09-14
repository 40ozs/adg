"""The identity of the collected state an answer was computed from.

A derived answer — effective access, an explanation, an impact list — is a pure function of
two things: the request, and the facts in the database. The request is in the URL. This
module names the second half, so that a cached answer can be checked against the state it
was computed from instead of against a clock.

**Why a clock is the wrong instrument here.** A time-to-live says "this was true recently".
For an access explanation that is the wrong promise in both directions: a ten-second TTL
still serves an explanation from before the group membership that just changed, and it
throws away a perfectly valid one when nothing has been collected for a week. Neither is
about whether the answer is current. Whether the answer is current is decided by exactly one
thing — whether a collector has written anything since — and ADG already records that, per
run, with a timestamp it controls.

**The invariant that makes this sound.** Every stored fact belongs to exactly one scan run
(ADR-0003), every fact is written through ingestion, and ingestion cannot write a fact
without touching that run's row: a batch bumps ``observation_count_applied`` and
``updated_at``, completion sets the status, reconciliation runs inside a completion. So the
state of the ``scan_runs`` table is a complete summary of the state of everything derived
from it. If the basis is unchanged, no fact has changed, and an answer computed earlier is
not stale — it is *identical*.

That is a strong claim, so it is stated as a claim and tested as one: ``tests/db`` replays
transcripts and asserts the token moves when facts move and holds still when they do not.

The token deliberately covers **every** run rather than only the runs that produced the rows
one answer read. Narrowing it would invalidate less often, and it would also be wrong: a new
run that adds the first membership edge for a group an ACL names changes an answer that
never read a single row from that group. The cheap, correct key is the whole collection
state; the precise, incorrect one is a per-row provenance set.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Final

__all__ = ["EMPTY_BASIS", "CollectionBasis"]

TOKEN_LENGTH: Final = 32
"""Hex characters kept from the digest. 128 bits: far past collision risk for a cache key,
and short enough to read in a log line or an ETag."""


@dataclass(frozen=True, slots=True)
class CollectionBasis:
    """A summary of every scan run, reduced to one comparable value.

    Constructed from an aggregate over ``scan_runs``; see
    :class:`app.repositories.basis.CollectionBasisRepository`. Pure here so that the digest
    is testable without a database and so the fields are documented in one place.
    """

    runs: int
    """How many runs exist. Changes when one starts, and when one is deleted."""

    latest_run_id: str | None
    """The most recently updated run, for a human reading a response. Not the key: two runs
    can interleave, and the one with the newest ``updated_at`` is not necessarily the one
    whose facts an answer used."""

    latest_activity_at: dt.datetime | None
    """``max(updated_at)``. The single most sensitive field: every ingestion write sets it."""

    observations_applied: int
    """``sum(observation_count_applied)``. Moves on every batch, including one that
    re-applies an idempotent payload and changes no row — which is the safe direction: a
    basis that moves too often costs a recomputation, one that moves too rarely serves a
    stale answer as a fresh one."""

    batches_received: int
    """``sum(batch_count_received)``. Independent of the observation count so that an empty
    batch — legal, and the way a collector reports "this scope is empty" — still moves the
    basis."""

    @property
    def token(self) -> str:
        """A short, stable digest of the whole basis.

        Stable across processes and restarts: it is a hash of the field values, not of any
        in-memory identity, so two API workers answering the same request produce the same
        token and a client's cached copy survives a deployment that changed no facts.
        """
        material = "\x1f".join(
            (
                str(self.runs),
                self.latest_run_id or "-",
                _timestamp(self.latest_activity_at),
                str(self.observations_applied),
                str(self.batches_received),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:TOKEN_LENGTH]

    @property
    def is_empty(self) -> bool:
        """Whether nothing has ever been collected.

        Worth branching on: every derived answer over an empty database is "no access", and
        an empty database is the one case where that means "nobody has looked" rather than
        "nobody has rights". The verdict vocabulary reports this per answer; this is the
        estate-wide form of the same distinction.
        """
        return self.runs == 0


def _timestamp(value: dt.datetime | None) -> str:
    """A timezone-normalized rendering, so an identical instant always digests identically.

    PostgreSQL returns ``timestamptz`` as an aware datetime in the session time zone; a
    naive value can also reach here from a test constructing one by hand. Both are pinned to
    UTC before formatting, because otherwise the same instant read under two session time
    zones would produce two tokens and a client would revalidate forever.
    """
    if value is None:
        return "-"
    moment = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
    return moment.astimezone(dt.UTC).isoformat(timespec="microseconds")


EMPTY_BASIS: Final = CollectionBasis(
    runs=0,
    latest_run_id=None,
    latest_activity_at=None,
    observations_applied=0,
    batches_received=0,
)
"""The basis of a database no collector has ever written to. A real value, not a null: an
answer computed over an empty estate is still an answer and still has a cache identity."""
