"""The audit chain: what it detects, and what it deliberately does not claim.

The chain is what makes a *quiet* edit of the governance record impossible. These tests build
chains by hand and then tamper with them in each of the ways somebody with a database
connection could, asserting that verification says which event broke and why.

The honest limit is tested too: the chain is not a signature. Anyone able to rewrite rows can
recompute every digest after the one they changed, and the last test states that plainly so
nobody reads a green verification as proof of more than it is.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from app.domain import GovernanceEventType
from app.governance.audit import (
    OWNERS_CHAIN,
    GovernanceEvent,
    campaign_chain,
    digest_of,
    event_digest,
    verify_chain,
)

AT = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)
CAMPAIGN = uuid.UUID("11111111-2222-3333-4444-555555555555")


def build(
    *specs: tuple[GovernanceEventType, str, dict[str, object]],
    chain_key: str = "campaign:test",
) -> list[GovernanceEvent]:
    """A well-formed chain, linked exactly as the repository links one."""
    events: list[GovernanceEvent] = []
    previous: str | None = None
    for index, (event_type, actor, payload) in enumerate(specs):
        digest = event_digest(
            chain_key=chain_key,
            chain_index=index,
            event_type=event_type,
            occurred_at=AT + dt.timedelta(minutes=index),
            actor_subject=actor,
            actor_roles=("reviewer",),
            campaign_id=CAMPAIGN,
            item_id=None,
            decision_id=None,
            payload=payload,
            previous_digest=previous,
        )
        events.append(
            GovernanceEvent(
                event_id=uuid.uuid4(),
                chain_key=chain_key,
                chain_index=index,
                event_type=event_type,
                occurred_at=AT + dt.timedelta(minutes=index),
                actor_subject=actor,
                actor_display_name=actor.title(),
                actor_roles=("reviewer",),
                campaign_id=CAMPAIGN,
                item_id=None,
                decision_id=None,
                payload=payload,
                previous_digest=previous,
                digest=digest,
            )
        )
        previous = digest
    return events


def three() -> list[GovernanceEvent]:
    return build(
        (GovernanceEventType.CAMPAIGN_CREATED, "admin", {"name": "Q3"}),
        (GovernanceEventType.CAMPAIGN_ACTIVATED, "admin", {"status": "active"}),
        (GovernanceEventType.DECISION_RECORDED, "alice", {"decision": "certify"}),
    )


class TestChainKeys:
    def test_a_campaign_gets_its_own_chain(self) -> None:
        """Per-campaign rather than one global chain: a campaign is the unit an auditor
        examines and exports, and two campaigns worked on at once must not contend for one
        head."""
        assert campaign_chain(CAMPAIGN) == f"campaign:{CAMPAIGN}"

    def test_ownership_events_have_a_chain_of_their_own(self) -> None:
        assert OWNERS_CHAIN == "owners"


class TestAnIntactChain:
    def test_a_well_formed_chain_verifies(self) -> None:
        result = verify_chain(three())

        assert result.intact
        assert result.length == 3
        assert result.broken_at is None
        assert result.reason is None

    def test_the_head_digest_is_the_newest_event(self) -> None:
        """The value worth recording outside the database — in an export, a ticket, a
        screenshot — because it is what a later verification is compared against."""
        events = three()

        assert verify_chain(events).head_digest == events[-1].digest

    def test_an_empty_chain_is_intact_rather_than_broken(self) -> None:
        """A campaign nothing has happened to yet has an empty trail, which is not a
        tampered one."""
        result = verify_chain([])

        assert result.intact
        assert result.is_empty

    def test_the_order_events_are_supplied_in_does_not_matter(self) -> None:
        """They come back from the database ordered, but verification sorts anyway: a caller
        that paged them differently must not be told the chain is broken."""
        assert verify_chain(list(reversed(three()))).intact

    def test_the_first_event_has_no_link(self) -> None:
        assert three()[0].previous_digest is None
        assert three()[0].is_chain_start


class TestWhatTamperingLooksLike:
    def test_editing_an_event_is_caught_and_located(self) -> None:
        events = three()
        events[1] = dataclasses.replace(events[1], actor_subject="mallory")

        result = verify_chain(events)

        assert not result.intact
        assert result.broken_at == 1
        assert "content was changed after it was written" in (result.reason or "")

    def test_editing_a_payload_is_caught(self) -> None:
        """The payload carries what was decided — the evidence digest, the target, the
        principal — so it is inside the digest and not beside it."""
        events = three()
        events[2] = dataclasses.replace(events[2], payload={"decision": "revoke"})

        assert verify_chain(events).broken_at == 2

    def test_removing_an_event_from_the_middle_is_caught(self) -> None:
        events = three()
        del events[1]

        result = verify_chain(events)

        assert not result.intact
        assert "the chain has a gap" in (result.reason or "")

    def test_replacing_an_event_with_a_well_formed_one_is_caught(self) -> None:
        """The replacement digests correctly on its own. What it cannot do is match the link
        the *next* event already committed to."""
        events = three()
        forged = build((GovernanceEventType.DECISION_RECORDED, "mallory", {"decision": "certify"}))[
            0
        ]
        events[1] = dataclasses.replace(forged, chain_index=1, previous_digest=events[0].digest)
        events[1] = dataclasses.replace(events[1], digest=digest_of(events[1]))

        result = verify_chain(events)

        assert not result.intact
        assert result.broken_at == 2
        assert "was replaced or inserted" in (result.reason or "")

    def test_re_rooting_a_chain_is_caught(self) -> None:
        """Dropping the first event and promoting the second would leave a chain that
        verifies from its new start. The index check refuses it."""
        events = three()[1:]

        result = verify_chain(events)

        assert not result.intact
        assert result.broken_at == 1

    def test_the_first_break_is_reported_rather_than_the_last(self) -> None:
        events = three()
        events[0] = dataclasses.replace(events[0], actor_subject="x")
        events[2] = dataclasses.replace(events[2], actor_subject="y")

        assert verify_chain(events).broken_at == 0


class TestWhatIsAndIsNotDigested:
    def test_a_renamed_actor_does_not_break_the_chain(self) -> None:
        """``actor_display_name`` is a mutable attribute of an account, copied for
        readability. Chaining it would make an audit trail fail verification because
        somebody got married."""
        events = three()
        events[2] = dataclasses.replace(events[2], actor_display_name="Alice Smith-Jones")

        assert verify_chain(events).intact

    def test_the_subject_is_digested_even_though_the_name_is_not(self) -> None:
        """The subject is the identity; the name is decoration. Only one of them is the
        claim "this person did this"."""
        events = three()
        events[2] = dataclasses.replace(events[2], actor_subject="bob")

        assert not verify_chain(events).intact

    def test_the_roles_held_at_the_time_are_digested(self) -> None:
        """ "Alice decided this" and "Alice, who then held the reviewer role, decided this"
        are different claims, and the second is the one an audit needs after her assignments
        have been changed."""
        events = three()
        events[2] = dataclasses.replace(events[2], actor_roles=("governance_admin",))

        assert not verify_chain(events).intact

    def test_two_identical_events_at_different_positions_digest_differently(self) -> None:
        """Without the index inside the digest, the same actor revoking the same assignment
        twice would produce two identical rows and one of the pair could be dropped without
        breaking verification."""
        repeated = build(
            (GovernanceEventType.REVIEWER_REVOKED, "admin", {"who": "alice"}),
            (GovernanceEventType.REVIEWER_REVOKED, "admin", {"who": "alice"}),
        )

        assert repeated[0].digest != repeated[1].digest
        assert verify_chain(repeated).intact

    def test_the_time_zone_a_row_is_read_back_in_does_not_change_the_digest(self) -> None:
        """The same instant under another session time zone must digest identically, or a
        chain would fail verification on a differently configured server."""
        elsewhere = AT.astimezone(dt.timezone(dt.timedelta(hours=-5)))
        common: dict[str, Any] = {
            "chain_key": "campaign:test",
            "chain_index": 0,
            "event_type": GovernanceEventType.CAMPAIGN_CREATED,
            "actor_subject": "admin",
            "actor_roles": ("governance_admin",),
            "campaign_id": CAMPAIGN,
            "item_id": None,
            "decision_id": None,
            "payload": {"name": "Q3"},
            "previous_digest": None,
        }

        assert event_digest(occurred_at=AT, **common) == event_digest(
            occurred_at=elsewhere, **common
        )

    def test_the_order_roles_are_supplied_in_does_not_change_the_digest(self) -> None:
        common: dict[str, Any] = {
            "chain_key": "campaign:test",
            "chain_index": 0,
            "event_type": GovernanceEventType.DECISION_RECORDED,
            "occurred_at": AT,
            "actor_subject": "alice",
            "campaign_id": CAMPAIGN,
            "item_id": None,
            "decision_id": None,
            "payload": {},
            "previous_digest": None,
        }

        assert event_digest(actor_roles=("reviewer", "viewer"), **common) == event_digest(
            actor_roles=("viewer", "reviewer"), **common
        )


class TestTheLimitOfWhatTheChainProves:
    def test_a_wholesale_rewrite_still_verifies_and_that_is_stated_not_hidden(self) -> None:
        """This is the honest limit. The chain is not a signature: anyone able to rewrite
        rows can recompute every digest after the one they changed, and the result verifies.

        What it buys is that a *quiet* edit is impossible — the edit has to rewrite every
        later event, and the chain head recorded in an export or a ticket no longer matches.
        The handoff and the module docstring say this rather than implying more; this test
        exists so that nobody later reads a green verification as proof of more than it is.
        """
        original = three()
        rewritten = build(
            (GovernanceEventType.CAMPAIGN_CREATED, "admin", {"name": "Q3"}),
            (GovernanceEventType.CAMPAIGN_ACTIVATED, "admin", {"status": "active"}),
            (GovernanceEventType.DECISION_RECORDED, "alice", {"decision": "revoke"}),
        )

        assert verify_chain(rewritten).intact
        # And this is the detection: the head no longer matches what was recorded before.
        assert verify_chain(rewritten).head_digest != verify_chain(original).head_digest
