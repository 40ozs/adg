r"""The governance audit trail: append-only events, chained so tampering is detectable.

Every governance act writes one event. The events are the record an auditor reads, and the
only one — a campaign's item rows say what the current answers are, and this says who gave
them and when, including the answers that were later changed.

Two properties, and they are enforced in different places because they fail differently.

**Append-only** is enforced by the database. ``0008_governance_model`` installs a trigger
that raises on every ``UPDATE`` and ``DELETE`` against ``governance_audit_events``. That
stops the accident — a repository method written later that thinks it may tidy a row — and
it stops it in the one place a future author cannot forget to look.

**Tamper-evidence** is enforced by arithmetic. Each event carries the digest of the event
before it in its chain, and its own digest is taken over its content *plus* that link. So
altering a past event, or removing one, invalidates every digest after it, and
:func:`verify_chain` says exactly where. A trigger protects against the application; the
chain protects against everything with a database connection, which is the threat an
attestation record actually has. It is not a signature — anyone able to rewrite rows could
recompute the whole chain — and the handoff says so rather than implying otherwise. What it
buys is that a *quiet* edit is not possible: the edit has to rewrite every later row, and
the chain heads recorded in exports and screenshots no longer match.

**One chain per campaign**, plus a single ``owners`` chain for ownership events that belong
to no campaign. Per-campaign rather than global because a campaign is the unit an auditor
examines and exports, and because two campaigns being worked on at once should not contend
for the same chain head.

``TRUNCATE`` is deliberately *not* blocked. A ``BEFORE TRUNCATE`` trigger would make the
table impossible to clear, which every test fixture and every environment reset needs to do;
and a truncation is not a quiet edit — it removes the chain head along with everything else,
which is exactly the visible kind of loss.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from app.governance.model import GovernanceEventType

__all__ = [
    "OWNERS_CHAIN",
    "ChainVerification",
    "GovernanceEvent",
    "campaign_chain",
    "digest_of",
    "event_digest",
    "verify_chain",
]

OWNERS_CHAIN: Final = "owners"
"""The chain ownership events join when they belong to no campaign."""


def campaign_chain(campaign_id: UUID) -> str:
    """The chain key for one campaign's events."""
    return f"campaign:{campaign_id}"


@dataclass(frozen=True, slots=True)
class GovernanceEvent:
    """One thing that happened, and who did it.

    ``actor_roles`` is stored alongside the subject because authorization is a fact about
    the moment: "Alice decided this" is not the same claim as "Alice, who then held the
    reviewer role, decided this", and the second is the one an audit needs after her roles
    have been changed.
    """

    event_id: UUID
    chain_key: str
    chain_index: int
    event_type: GovernanceEventType
    occurred_at: dt.datetime
    actor_subject: str
    actor_display_name: str | None
    actor_roles: tuple[str, ...]
    campaign_id: UUID | None
    item_id: UUID | None
    decision_id: UUID | None
    payload: dict[str, Any]
    previous_digest: str | None
    digest: str

    @property
    def is_chain_start(self) -> bool:
        return self.chain_index == 0


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_digest(
    *,
    chain_key: str,
    chain_index: int,
    event_type: GovernanceEventType,
    occurred_at: dt.datetime,
    actor_subject: str,
    actor_roles: Sequence[str],
    campaign_id: UUID | None,
    item_id: UUID | None,
    decision_id: UUID | None,
    payload: dict[str, Any],
    previous_digest: str | None,
) -> str:
    """The digest of one event, including its link to the one before it.

    ``chain_index`` is inside the digest as well as being the column that orders the chain.
    Without it, two events with identical content — the same actor revoking the same
    assignment twice — would digest identically, and one of the pair could be dropped
    without breaking verification.

    ``actor_display_name`` is **not** digested. It is a mutable attribute of an account
    copied for readability, and chaining it would make an audit trail fail verification
    because somebody got married.
    """
    material = {
        "chain_key": chain_key,
        "chain_index": chain_index,
        "event_type": event_type.value,
        # Microsecond precision, pinned to UTC: the same instant read back under another
        # session time zone must not digest differently.
        "occurred_at": occurred_at.astimezone(dt.UTC).isoformat(timespec="microseconds"),
        "actor_subject": actor_subject,
        "actor_roles": sorted(actor_roles),
        "campaign_id": None if campaign_id is None else str(campaign_id),
        "item_id": None if item_id is None else str(item_id),
        "decision_id": None if decision_id is None else str(decision_id),
        "payload": payload,
        "previous_digest": previous_digest,
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def digest_of(event: GovernanceEvent) -> str:
    """Recompute one event's digest from its own content."""
    return event_digest(
        chain_key=event.chain_key,
        chain_index=event.chain_index,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        actor_subject=event.actor_subject,
        actor_roles=event.actor_roles,
        campaign_id=event.campaign_id,
        item_id=event.item_id,
        decision_id=event.decision_id,
        payload=event.payload,
        previous_digest=event.previous_digest,
    )


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """Whether a chain is intact, and where it stops being so."""

    chain_key: str
    length: int
    intact: bool
    head_digest: str | None
    """The digest of the newest event. This is the value worth recording outside the
    database — in an export, a ticket, a screenshot — because it is what a later
    verification is compared against."""

    broken_at: int | None
    """The ``chain_index`` of the first event that does not verify, or ``None``."""

    reason: str | None

    @property
    def is_empty(self) -> bool:
        return self.length == 0


def verify_chain(events: Sequence[GovernanceEvent]) -> ChainVerification:
    """Walk a chain oldest-first and report the first place it breaks.

    Three ways it can break, reported separately because they mean different things:

    * an event whose own digest does not match its content — the row was edited;
    * an event whose ``previous_digest`` is not the previous event's digest — an event was
      removed, replaced, or inserted;
    * a gap in ``chain_index`` — an event was removed from the end of a run, which the
      previous check would also catch but would describe less clearly.
    """
    ordered = sorted(events, key=lambda event: event.chain_index)
    if not ordered:
        return ChainVerification(
            chain_key="", length=0, intact=True, head_digest=None, broken_at=None, reason=None
        )

    chain_key = ordered[0].chain_key
    previous: GovernanceEvent | None = None
    for position, event in enumerate(ordered):
        if event.chain_index != position:
            return _broken(
                chain_key,
                ordered,
                event.chain_index,
                f"Event {event.chain_index} sits at position {position}: the chain has a "
                "gap, so at least one event between them is missing.",
            )
        expected_link = None if previous is None else previous.digest
        if event.previous_digest != expected_link:
            return _broken(
                chain_key,
                ordered,
                event.chain_index,
                f"Event {event.chain_index} links to {event.previous_digest!r}, but the "
                f"event before it digests to {expected_link!r}. An event was replaced or "
                "inserted.",
            )
        if digest_of(event) != event.digest:
            return _broken(
                chain_key,
                ordered,
                event.chain_index,
                f"Event {event.chain_index} does not match its own digest: its content was "
                "changed after it was written.",
            )
        previous = event

    return ChainVerification(
        chain_key=chain_key,
        length=len(ordered),
        intact=True,
        head_digest=ordered[-1].digest,
        broken_at=None,
        reason=None,
    )


def _broken(
    chain_key: str, ordered: Sequence[GovernanceEvent], index: int, reason: str
) -> ChainVerification:
    return ChainVerification(
        chain_key=chain_key,
        length=len(ordered),
        intact=False,
        head_digest=ordered[-1].digest,
        broken_at=index,
        reason=reason,
    )
