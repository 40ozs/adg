"""A campaign end to end against a real estate: freeze, assign, decide, verify, close.

The estate here is small and its timeline is explicit, because every question a campaign asks
is a question about an instant. Two scans of one share a week apart, with the permissions
changed in between, are enough to exercise the property the whole phase turns on: a campaign
cut against Monday keeps showing Monday's grants however Friday's scan changes them.

These run against PostgreSQL because the guarantees being tested are the database's — the
partial unique index that permits one current decision per item, the two triggers that make
decisions and audit events append-only, and the deferred foreign key that lets a supersession
be written before its successor exists.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    AceType,
    CampaignFocus,
    DecisionKind,
    GovernanceEventType,
    OwnershipRole,
    RemediationAction,
    ReviewItemStatus,
    ReviewScopeKind,
    ReviewTargetKind,
)
from app.governance.generation import ExclusionReason
from app.governance.model import CampaignScope, GenerationOptions
from app.governance.repository import GovernanceRepository
from app.governance.service import (
    Actor,
    GovernanceConflict,
    GovernanceForbidden,
    GovernanceNotFound,
    GovernanceService,
)
from tests.support import history as h
from tests.support.ingest import replay

MONDAY = h.MONDAY
FRIDAY = h.FRIDAY
LATER = h.NEXT_MONDAY_END + dt.timedelta(days=1)

SERVER = "FS01"
SHARE_NAME = "Finance"
SHARE_KEY = "fs01|finance"
FINANCE = "\\\\fs01\\finance"
HR = "\\\\fs01\\finance\\hr"

ALICE = "S-1-5-21-1004336348-1177238915-682003330-1101"
CONTRACTORS = "S-1-5-21-1004336348-1177238915-682003330-2201"
SYSTEM = "S-1-5-18"

READ_MASK = 0x1200A9
WRITE_MASK = 0x1301BF

ADMIN = Actor(subject="gov-admin", display_name="Gov Admin", roles=("governance_admin",))
ALICE_REVIEWER = Actor(subject="alice", display_name="Alice", roles=("reviewer",))
BOB_REVIEWER = Actor(subject="bob", display_name="Bob", roles=("reviewer",))


@pytest.fixture
async def estate(client: AsyncClient) -> AsyncIterator[None]:
    """Two scans of one share, a week apart, with the permissions changed in between.

    Monday: Alice reads Finance, Contractors write HR, SYSTEM has full control everywhere.
    Friday: Contractors' write is gone and Alice has been widened to write.
    """
    await replay(
        client,
        h.ad_scan(
            observations=[
                h.principal(ALICE, at=MONDAY, display_name="CONTOSO\\alice"),
                h.principal(CONTRACTORS, at=MONDAY, display_name="CONTOSO\\Contractors"),
            ],
            started_at=MONDAY,
        ),
    )
    await replay(
        client,
        h.smb_scan(
            observations=[
                h.server(SERVER, at=MONDAY),
                h.share(SERVER, SHARE_NAME, at=MONDAY),
                h.share_ace(SERVER, SHARE_NAME, ALICE, at=MONDAY),
            ],
            started_at=MONDAY,
            server_name=SERVER,
        ),
    )
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE, at=MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=2
                ),
                h.ntfs_ace(FINANCE, ALICE, at=MONDAY, access_mask=READ_MASK),
                h.ntfs_ace(FINANCE, SYSTEM, at=MONDAY, access_mask=0x1F01FF),
                h.resource(HR, at=MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=1),
                h.ntfs_ace(HR, CONTRACTORS, at=MONDAY, access_mask=WRITE_MASK),
            ],
            started_at=MONDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )
    # Friday: the estate moves on. Nothing about Monday's campaign may follow it.
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE, at=FRIDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=2
                ),
                h.ntfs_ace(FINANCE, ALICE, at=FRIDAY, access_mask=WRITE_MASK),
                h.ntfs_ace(FINANCE, SYSTEM, at=FRIDAY, access_mask=0x1F01FF),
                h.resource(HR, at=FRIDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=0),
            ],
            started_at=FRIDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )
    yield


def service(session: AsyncSession, *, now: dt.datetime = LATER) -> GovernanceService:
    return GovernanceService(session, now=now)


async def a_campaign(
    session: AsyncSession,
    *,
    baseline: dt.datetime = h.MONDAY_END,
    scopes: list[CampaignScope] | None = None,
    focus: CampaignFocus = CampaignFocus.RESOURCE,
    options: GenerationOptions | None = None,
    due_at: dt.datetime | None = None,
) -> uuid.UUID:
    campaign = await service(session).create_campaign(
        ADMIN,
        name="Q1 Finance review",
        focus=focus,
        scopes=scopes or [CampaignScope(kind=ReviewScopeKind.SHARE, key=SHARE_KEY)],
        baseline_at=baseline,
        due_at=due_at,
        options=options,
    )
    return campaign.campaign_id


async def a_running_campaign(
    session: AsyncSession, *, reviewer: Actor = ALICE_REVIEWER, **kwargs: object
) -> uuid.UUID:
    campaign_id = await a_campaign(session, **kwargs)  # type: ignore[arg-type]
    await service(session).generate(ADMIN, campaign_id)
    await service(session).assign(
        ADMIN,
        campaign_id,
        reviewer_subject=reviewer.subject,
        reviewer_display_name=reviewer.display_name,
    )
    await service(session).activate(ADMIN, campaign_id)
    return campaign_id


class TestACampaignFreezesTheEstateAtAnInstant:
    async def test_it_generates_the_grants_that_were_there_on_monday(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)

        outcome = await service(session).generate(ADMIN, campaign_id)
        pairs = {(item.target_key, item.principal_key) for item in outcome.items}

        assert (FINANCE, ALICE) in pairs
        assert (HR, CONTRACTORS) in pairs
        assert (SHARE_KEY, ALICE) in pairs

    async def test_the_frozen_evidence_is_mondays_mask_not_fridays(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The property the whole phase turns on. Alice was widened from read to write on
        Friday; a campaign cut against Monday must still show the read."""
        campaign_id = await a_campaign(session)

        outcome = await service(session).generate(ADMIN, campaign_id)
        alice = next(
            item
            for item in outcome.items
            if item.target_key == FINANCE and item.principal_key == ALICE
        )

        assert [grant.access_mask for grant in alice.grants] == [READ_MASK]

    async def test_a_campaign_cut_against_friday_shows_fridays_mask(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The other half of the same property: the freeze follows the instant asked for, so
        the earlier result was not simply a stale read."""
        campaign_id = await a_campaign(session, baseline=h.FRIDAY_END)

        outcome = await service(session).generate(ADMIN, campaign_id)
        alice = next(
            item
            for item in outcome.items
            if item.target_key == FINANCE and item.principal_key == ALICE
        )

        assert [grant.access_mask for grant in alice.grants] == [WRITE_MASK]

    async def test_a_grant_removed_on_friday_is_still_in_mondays_campaign(
        self, estate: None, session: AsyncSession
    ) -> None:
        monday = await a_campaign(session)
        friday = await a_campaign(session, baseline=h.FRIDAY_END)

        on_monday = await service(session).generate(ADMIN, monday)
        on_friday = await service(session).generate(ADMIN, friday)

        assert any(item.principal_key == CONTRACTORS for item in on_monday.items)
        assert not any(item.principal_key == CONTRACTORS for item in on_friday.items)

    async def test_the_name_a_principal_had_at_the_baseline_is_carried(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)

        outcome = await service(session).generate(ADMIN, campaign_id)
        alice = next(item for item in outcome.items if item.principal_key == ALICE)

        assert alice.principal_display_name == "CONTOSO\\alice"

    async def test_well_known_trustees_are_excluded_and_counted(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)

        outcome = await service(session).generate(ADMIN, campaign_id)

        assert not any(item.principal_sid == SYSTEM for item in outcome.items)
        assert outcome.excluded[ExclusionReason.BUILTIN_TRUSTEE] >= 1

    async def test_the_exclusions_are_stored_on_the_campaign_not_only_returned(
        self, estate: None, session: AsyncSession
    ) -> None:
        """They are reported with the campaign's status forever after, so "12 of 12
        certified" is never readable as coverage of everything."""
        campaign_id = await a_campaign(session)
        await service(session).generate(ADMIN, campaign_id)

        campaign = await service(session).get_campaign(campaign_id)

        assert campaign.excluded_counts[ExclusionReason.BUILTIN_TRUSTEE] >= 1

    async def test_a_directory_tree_scope_selects_the_subtree(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(
            session, scopes=[CampaignScope(kind=ReviewScopeKind.DIRECTORY_TREE, key=FINANCE)]
        )

        outcome = await service(session).generate(ADMIN, campaign_id)

        assert {item.target_key for item in outcome.items} == {FINANCE, HR}

    async def test_a_directory_tree_scope_does_not_select_the_share_acl(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The share's permissions belong to the whole share; a campaign scoped to one
        subdirectory did not ask about them."""
        campaign_id = await a_campaign(
            session, scopes=[CampaignScope(kind=ReviewScopeKind.DIRECTORY_TREE, key=HR)]
        )

        outcome = await service(session).generate(ADMIN, campaign_id)

        assert not any(item.target_kind is ReviewTargetKind.SHARE for item in outcome.items)

    async def test_a_principal_focused_campaign_finds_every_place_it_is_named(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(
            session,
            focus=CampaignFocus.PRINCIPAL,
            scopes=[CampaignScope(kind=ReviewScopeKind.PRINCIPAL, key=ALICE)],
        )

        outcome = await service(session).generate(ADMIN, campaign_id)

        assert {item.target_key for item in outcome.items} == {FINANCE, SHARE_KEY}
        assert {item.principal_key for item in outcome.items} == {ALICE}

    async def test_overlapping_scopes_do_not_duplicate_an_item(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(
            session,
            scopes=[
                CampaignScope(kind=ReviewScopeKind.SHARE, key=SHARE_KEY),
                CampaignScope(kind=ReviewScopeKind.DIRECTORY_TREE, key=FINANCE),
            ],
        )

        outcome = await service(session).generate(ADMIN, campaign_id)
        keys = [item.natural_key for item in outcome.items]

        assert len(keys) == len(set(keys))


class TestTheCampaignLifecycleAgainstTheDatabase:
    async def test_a_draft_may_be_regenerated_after_widening_its_scope(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(
            session, scopes=[CampaignScope(kind=ReviewScopeKind.DIRECTORY_TREE, key=HR)]
        )
        first = await service(session).generate(ADMIN, campaign_id)

        second = await service(session).generate(ADMIN, campaign_id)

        assert first.item_count == second.item_count
        assert second.regenerated is True

    async def test_an_active_campaign_cannot_be_regenerated(
        self, estate: None, session: AsyncSession
    ) -> None:
        """It would change what reviewers are answering underneath them and orphan the
        decisions already recorded."""
        campaign_id = await a_running_campaign(session)

        with pytest.raises(GovernanceConflict, match="frozen"):
            await service(session).generate(ADMIN, campaign_id)

    async def test_a_campaign_cannot_be_activated_before_it_is_generated(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)

        with pytest.raises(GovernanceConflict, match="no item set yet"):
            await service(session).activate(ADMIN, campaign_id)

    async def test_a_campaign_that_generated_nothing_cannot_be_activated(
        self, estate: None, session: AsyncSession
    ) -> None:
        """An empty campaign would report itself complete the moment it opened. The message
        says the likely cause, because it usually is one."""
        campaign_id = await a_campaign(session, baseline=MONDAY - dt.timedelta(days=30))
        await service(session).generate(ADMIN, campaign_id)

        with pytest.raises(GovernanceConflict, match="baseline predates the first scan"):
            await service(session).activate(ADMIN, campaign_id)

    async def test_a_closed_campaign_cannot_be_reopened(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        await service(session).close(ADMIN, campaign_id)

        with pytest.raises(Exception, match="closed cannot become active"):
            await service(session).activate(ADMIN, campaign_id)

    async def test_closing_with_items_undecided_is_allowed_and_recorded(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A campaign that could not close until every item was answered would either never
        close or be closed by somebody rubber-stamping the remainder."""
        campaign_id = await a_running_campaign(session)

        await service(session).close(ADMIN, campaign_id)
        events, _, _ = await service(session).audit_trail(campaign_id)
        closure = next(
            event for event in events if event.event_type is GovernanceEventType.CAMPAIGN_CLOSED
        )

        assert closure.payload["undecided"] > 0
        assert closure.payload["decided"] == 0

    async def test_a_canceled_campaign_keeps_the_decisions_already_made(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A campaign that deleted its own evidence on cancellation would be a way to unmake
        an attestation."""
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)

        await service(session).cancel(ADMIN, campaign_id, reason="Wrong scope.")
        decisions = await service(session).item_decisions(item.item_id)

        assert len(decisions) == 1
        assert decisions[0].decided_by_subject == "alice"


class TestWhoMayAnswerAnItem:
    async def test_the_assigned_reviewer_may(self, estate: None, session: AsyncSession) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]

        decision = await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY
        )

        assert decision.decided_by_subject == "alice"

    async def test_another_reviewer_may_not_even_holding_the_capability(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The capability admits the request; the assignment grants authority over the item.
        Bob holds `reviewer` here and is still refused."""
        campaign_id = await a_running_campaign(session, reviewer=ALICE_REVIEWER)
        item = (await service(session).list_items(campaign_id)).items[0]

        with pytest.raises(GovernanceForbidden, match="assigned to another reviewer"):
            await service(session).decide(BOB_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)

    async def test_an_unassigned_item_can_be_answered_by_nobody(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)
        await service(session).generate(ADMIN, campaign_id)
        await service(session).assign(
            ADMIN,
            campaign_id,
            reviewer_subject="alice",
            scope=CampaignScope(kind=ReviewScopeKind.DIRECTORY_TREE, key=HR),
        )
        await service(session).activate(ADMIN, campaign_id)
        page = await service(session).list_items(campaign_id, unassigned_only=True)

        with pytest.raises(GovernanceForbidden, match="assigned to nobody"):
            await service(session).decide(
                ALICE_REVIEWER, page.items[0].item_id, decision=DecisionKind.CERTIFY
            )

    async def test_a_revoked_reviewer_can_no_longer_answer(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        assignment = (await service(session).list_assignments(campaign_id))[0]
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).revoke_assignment(ADMIN, campaign_id, assignment.assignment_id)

        with pytest.raises(GovernanceForbidden, match="was revoked"):
            await service(session).decide(
                ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY
            )

    async def test_a_revoked_reviewers_earlier_decisions_stay(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)
        assignment = (await service(session).list_assignments(campaign_id))[0]

        await service(session).revoke_assignment(ADMIN, campaign_id, assignment.assignment_id)

        assert len(await service(session).item_decisions(item.item_id)) == 1

    async def test_no_decision_may_be_recorded_on_a_closed_campaign(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).close(ADMIN, campaign_id)

        with pytest.raises(GovernanceConflict, match="accepts no"):
            await service(session).decide(
                ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY
            )

    async def test_a_reviewers_queue_is_only_their_own_items(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)
        await service(session).generate(ADMIN, campaign_id)
        await service(session).assign(
            ADMIN,
            campaign_id,
            reviewer_subject="alice",
            scope=CampaignScope(kind=ReviewScopeKind.DIRECTORY_TREE, key=HR),
        )
        await service(session).activate(ADMIN, campaign_id)

        page = await service(session).list_items(campaign_id, reviewer_subject="alice")

        assert {item.target_key for item in page.items} == {HR}

    async def test_a_queue_for_somebody_with_no_assignment_is_empty_not_everything(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The dangerous failure mode: a filter that matched nothing and so filtered nothing
        would hand every item in the campaign to an unassigned caller."""
        campaign_id = await a_running_campaign(session)

        page = await service(session).list_items(campaign_id, reviewer_subject="nobody")

        assert page.items == ()
        assert page.total == 0


class TestADecisionIsAppendOnly:
    async def test_changing_a_mind_supersedes_rather_than_edits(
        self, estate: None, session: AsyncSession
    ) -> None:
        """ "Certified on the 3rd, revoked on the 9th" and "revoked on the 9th" are different
        histories, and only the first lets an auditor ask what changed in between."""
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        first = await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY
        )

        second = await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.REVOKE, rationale="Leaver."
        )
        decisions = await service(session).item_decisions(item.item_id)

        # Ordered by the supersession chain, not by decided_at: this service is pinned to a
        # fixed instant, so both decisions share a timestamp and a chronological sort would
        # order the reviewer's change of mind at random.
        assert len(decisions) == 2
        assert decisions[0].decided_at == decisions[1].decided_at
        assert decisions[0].decision_id == first.decision_id
        assert decisions[0].superseded_by_decision_id == second.decision_id
        assert not decisions[0].is_current
        assert decisions[1].is_current
        assert second.supersedes_decision_id == first.decision_id

    async def test_the_item_points_at_the_newest_decision(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)
        second = await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.ABSTAIN, rationale="Wrong person."
        )

        refreshed = await service(session).get_item(item.item_id)

        assert refreshed.status is ReviewItemStatus.DECIDED
        assert refreshed.current_decision_id == second.decision_id

    async def test_the_database_refuses_to_delete_a_decision(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Enforced by trigger, not convention: an attestation that can be deleted is not an
        attestation."""
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        decision = await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY
        )
        await session.commit()

        with pytest.raises(Exception, match="append-only"):
            await session.execute(
                sa.text("DELETE FROM review_decisions WHERE decision_id = :id"),
                {"id": decision.decision_id},
            )
        await session.rollback()

    async def test_the_database_refuses_to_edit_a_decisions_rationale(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        decision = await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.REVOKE, rationale="Leaver."
        )
        await session.commit()

        with pytest.raises(Exception, match="may not be edited"):
            await session.execute(
                sa.text("UPDATE review_decisions SET rationale = :r WHERE decision_id = :id"),
                {"r": "Actually, keep it.", "id": decision.decision_id},
            )
        await session.rollback()

    async def test_the_database_refuses_a_second_current_decision(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The partial unique index. Two current rows would make "what did the reviewer
        conclude" return two contradictory answers with no way to choose."""
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)
        await session.commit()

        with pytest.raises(Exception, match="ux_review_decisions_current"):
            await session.execute(
                sa.text(
                    "INSERT INTO review_decisions (decision_id, item_id, campaign_id, "
                    "decision, decided_by_subject, decided_at, decided_late, created_at) "
                    "VALUES (gen_random_uuid(), :item, :campaign, 'certify', 'mallory', "
                    "now(), false, now())"
                ),
                {"item": item.item_id, "campaign": campaign_id},
            )
        await session.rollback()

    async def test_a_late_decision_records_that_it_was_late(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Recorded at the moment rather than derived later, because the deadline it was late
        against is the campaign's due date *then*."""
        campaign_id = await a_running_campaign(session, due_at=h.MONDAY_END + dt.timedelta(days=1))
        item = (await service(session).list_items(campaign_id)).items[0]

        decision = await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY
        )

        assert decision.decided_late is True

    async def test_a_decision_inside_the_window_is_not_late(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session, due_at=LATER + dt.timedelta(days=30))
        item = (await service(session).list_items(campaign_id)).items[0]

        decision = await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY
        )

        assert decision.decided_late is False


class TestTheAuditTrail:
    async def test_every_act_is_recorded_in_order(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)

        events, _, chain = await service(session).audit_trail(campaign_id)

        assert [event.event_type for event in events] == [
            GovernanceEventType.CAMPAIGN_CREATED,
            GovernanceEventType.CAMPAIGN_GENERATED,
            GovernanceEventType.REVIEWER_ASSIGNED,
            GovernanceEventType.CAMPAIGN_ACTIVATED,
            GovernanceEventType.DECISION_RECORDED,
        ]
        assert chain.intact

    async def test_a_decision_event_records_what_was_certified_not_only_that_it_was(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Without the evidence digest, "Alice certified item 4f2" would depend on the item
        row still holding the evidence it held at the time."""
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)

        events, _, _ = await service(session).audit_trail(campaign_id)
        recorded = events[-1]

        assert recorded.payload["evidence_digest"] == item.evidence_digest
        assert recorded.payload["target_key"] == item.target_key
        assert recorded.payload["principal_key"] == item.principal_key
        assert recorded.payload["certainty"] == item.certainty.value

    async def test_the_roles_the_actor_held_are_recorded(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)

        events, _, _ = await service(session).audit_trail(campaign_id)

        assert events[-1].actor_roles == ("reviewer",)
        assert events[0].actor_roles == ("governance_admin",)

    async def test_a_superseded_decision_leaves_two_events(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)
        await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.REVOKE, rationale="Leaver."
        )

        events, _, chain = await service(session).audit_trail(campaign_id)
        kinds = [event.event_type for event in events]

        assert kinds.count(GovernanceEventType.DECISION_RECORDED) == 2
        assert GovernanceEventType.DECISION_SUPERSEDED in kinds
        assert chain.intact

    async def test_the_database_refuses_to_edit_an_audit_event(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        await session.commit()

        with pytest.raises(Exception, match="append-only"):
            await session.execute(
                sa.text("UPDATE governance_audit_events SET actor_subject = 'mallory'")
            )
        await session.rollback()
        del campaign_id

    async def test_the_database_refuses_to_delete_an_audit_event(
        self, estate: None, session: AsyncSession
    ) -> None:
        await a_running_campaign(session)
        await session.commit()

        with pytest.raises(Exception, match="append-only"):
            await session.execute(sa.text("DELETE FROM governance_audit_events"))
        await session.rollback()

    async def test_a_tampered_row_breaks_the_chain_and_the_break_is_located(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The trigger stops the application and an accident. The chain is what catches
        somebody with a database connection, and this is the proof it does — the trigger is
        dropped for the length of the test so that the tamper can actually land."""
        campaign_id = await a_running_campaign(session)
        await session.commit()
        await session.execute(sa.text("ALTER TABLE governance_audit_events DISABLE TRIGGER USER"))
        try:
            await session.execute(
                sa.text(
                    "UPDATE governance_audit_events SET actor_subject = 'mallory' "
                    "WHERE chain_key = :key AND chain_index = 1"
                ),
                {"key": f"campaign:{campaign_id}"},
            )
            _, _, chain = await service(session).audit_trail(campaign_id)
        finally:
            await session.execute(
                sa.text("ALTER TABLE governance_audit_events ENABLE TRIGGER USER")
            )
            await session.rollback()

        assert not chain.intact
        assert chain.broken_at == 1
        assert "changed after it was written" in (chain.reason or "")


class TestCampaignVerification:
    async def test_an_untouched_campaign_is_reproducible(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)

        verification = await service(session).verify_campaign(campaign_id)

        assert verification.reproducible
        assert verification.stored_digest == verification.recomputed_digest
        assert verification.missing == ()
        assert verification.unexpected == ()
        assert verification.evidence_changed == ()

    async def test_later_scans_do_not_make_it_unreproducible(
        self, estate: None, session: AsyncSession, client: AsyncClient
    ) -> None:
        """The point of freezing against an instant: the estate moving on is not drift."""
        campaign_id = await a_running_campaign(session)
        await replay(
            client,
            h.ntfs_scan(
                observations=[
                    h.resource(
                        FINANCE,
                        at=h.NEXT_MONDAY,
                        server_name=SERVER,
                        share_name=SHARE_NAME,
                        ace_count=1,
                    ),
                    h.ntfs_ace(FINANCE, ALICE, at=h.NEXT_MONDAY, access_mask=0x1F01FF),
                ],
                started_at=h.NEXT_MONDAY,
                server_name=SERVER,
                share_name=SHARE_NAME,
            ),
        )

        verification = await service(session).verify_campaign(campaign_id)

        assert verification.reproducible

    async def test_a_deleted_item_is_reported_as_missing_with_an_explanation(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Divergence means the record of what was reviewed no longer matches what the
        estate says was true then, which is a finding rather than a rendering difference."""
        campaign_id = await a_running_campaign(session)
        page = await service(session).list_items(campaign_id)
        victim = page.items[0]
        await session.execute(
            sa.text("DELETE FROM review_items WHERE item_id = :id"), {"id": victim.item_id}
        )

        verification = await service(session).verify_campaign(campaign_id)

        assert not verification.reproducible
        assert victim.natural_key in verification.missing
        assert "NOT reproducible" in verification.explanation
        assert "timeline itself changed" in verification.explanation

    async def test_altered_evidence_is_reported_separately_from_a_missing_item(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        page = await service(session).list_items(campaign_id)
        victim = page.items[0]
        await session.execute(
            sa.text("UPDATE review_items SET evidence_digest = :d WHERE item_id = :id"),
            {"d": "f" * 64, "id": victim.item_id},
        )

        verification = await service(session).verify_campaign(campaign_id)

        assert not verification.reproducible
        assert victim.natural_key in verification.evidence_changed
        assert verification.missing == ()
        assert verification.unexpected == ()

    async def test_an_ungenerated_campaign_has_nothing_to_verify(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)

        with pytest.raises(GovernanceConflict, match="not been generated"):
            await service(session).verify_campaign(campaign_id)


class TestCampaignStatus:
    async def test_it_counts_what_is_answered_and_what_is_nobodys(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)
        await service(session).generate(ADMIN, campaign_id)
        await service(session).assign(
            ADMIN,
            campaign_id,
            reviewer_subject="alice",
            scope=CampaignScope(kind=ReviewScopeKind.DIRECTORY_TREE, key=HR),
        )
        await service(session).activate(ADMIN, campaign_id)
        mine = await service(session).list_items(campaign_id, reviewer_subject="alice")
        await service(session).decide(
            ALICE_REVIEWER, mine.items[0].item_id, decision=DecisionKind.CERTIFY
        )

        report = await service(session).campaign_status(campaign_id)

        assert report.counts.decided == 1
        assert report.unassigned_items > 0
        assert report.reviewers[0].reviewer_subject == "alice"
        assert report.reviewers[0].decided == 1
        assert report.reviewers[0].pending == report.reviewers[0].assigned - 1

    async def test_an_empty_campaign_is_zero_per_cent_complete_not_a_hundred(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session, baseline=MONDAY - dt.timedelta(days=30))
        await service(session).generate(ADMIN, campaign_id)

        report = await service(session).campaign_status(campaign_id)

        assert report.counts.total == 0
        assert report.counts.completion == 0.0

    async def test_the_decisions_are_broken_down_by_kind(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = (await service(session).list_items(campaign_id)).items
        await service(session).decide(
            ALICE_REVIEWER, items[0].item_id, decision=DecisionKind.CERTIFY
        )
        await service(session).decide(
            ALICE_REVIEWER, items[1].item_id, decision=DecisionKind.REVOKE, rationale="Leaver."
        )

        report = await service(session).campaign_status(campaign_id)

        assert report.counts.by_decision == {"certify": 1, "revoke": 1}

    async def test_a_superseded_decision_is_not_counted_twice(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)
        await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.REVOKE, rationale="Leaver."
        )

        report = await service(session).campaign_status(campaign_id)

        assert report.counts.by_decision == {"revoke": 1}
        assert report.counts.decided == 1


class TestProposedRemediation:
    async def test_a_revoke_may_propose_removing_the_entries_it_was_about(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.REVOKE, rationale="Leaver."
        )

        proposal = await service(session).propose_remediation(
            ALICE_REVIEWER, item.item_id, action=RemediationAction.REMOVE_ACE
        )

        assert proposal.ace_keys == tuple(grant.ace_key for grant in item.grants)
        assert proposal.target_key == item.target_key
        assert proposal.is_active

    async def test_a_certified_grant_cannot_sprout_a_removal(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Storing both would leave two answers with nothing to choose between them."""
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY)

        with pytest.raises(GovernanceConflict, match="nothing to remediate"):
            await service(session).propose_remediation(
                ALICE_REVIEWER, item.item_id, action=RemediationAction.REMOVE_ACE
            )

    async def test_an_undecided_item_cannot_have_a_proposal(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]

        with pytest.raises(GovernanceConflict, match="no decision yet"):
            await service(session).propose_remediation(
                ALICE_REVIEWER, item.item_id, action=RemediationAction.REMOVE_ACE
            )

    async def test_only_the_assigned_reviewer_may_propose(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        item = (await service(session).list_items(campaign_id)).items[0]
        await service(session).decide(
            ALICE_REVIEWER, item.item_id, decision=DecisionKind.REVOKE, rationale="Leaver."
        )

        with pytest.raises(GovernanceForbidden):
            await service(session).propose_remediation(
                BOB_REVIEWER, item.item_id, action=RemediationAction.REMOVE_ACE
            )


class TestResourceOwnership:
    async def test_an_owner_is_recorded_and_listed(
        self, estate: None, session: AsyncSession
    ) -> None:
        await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.SHARE,
            target_key=SHARE_KEY,
            owner_subject="alice",
            owner_display_name="Alice",
        )

        owners, _ = await service(session).list_owners(target_key=SHARE_KEY)

        assert [owner.owner_subject for owner in owners] == ["alice"]
        assert owners[0].is_active

    async def test_recording_an_owner_changes_no_collected_row(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Ownership here is ADG metadata. The Windows security descriptor's owner is a
        separate, collected fact and this must not touch it."""
        before = (
            await session.execute(
                sa.text("SELECT owner_sid FROM ntfs_resources WHERE resource_key = :k"),
                {"k": FINANCE},
            )
        ).scalar_one_or_none()

        await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.RESOURCE,
            target_key=FINANCE,
            owner_principal_key=ALICE,
        )

        after = (
            await session.execute(
                sa.text("SELECT owner_sid FROM ntfs_resources WHERE resource_key = :k"),
                {"k": FINANCE},
            )
        ).scalar_one_or_none()

        assert before == after

    async def test_the_same_party_cannot_be_recorded_twice_on_one_target(
        self, estate: None, session: AsyncSession
    ) -> None:
        await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.SHARE,
            target_key=SHARE_KEY,
            owner_subject="alice",
        )
        await session.flush()

        with pytest.raises(Exception, match="ux_resource_owners_active"):
            await service(session).assign_owner(
                ADMIN,
                target_kind=ReviewTargetKind.SHARE,
                target_key=SHARE_KEY,
                owner_subject="alice",
            )
            await session.flush()
        await session.rollback()

    async def test_a_delegate_is_a_separate_record_from_the_owner(
        self, estate: None, session: AsyncSession
    ) -> None:
        """An audit can then tell a review performed by the accountable party from one
        performed by a stand-in."""
        await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.SHARE,
            target_key=SHARE_KEY,
            owner_subject="alice",
        )
        await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.SHARE,
            target_key=SHARE_KEY,
            ownership_role=OwnershipRole.DELEGATE,
            owner_subject="alice",
        )

        owners, _ = await service(session).list_owners(target_key=SHARE_KEY)

        assert {owner.ownership_role for owner in owners} == {
            OwnershipRole.OWNER,
            OwnershipRole.DELEGATE,
        }

    async def test_revoking_keeps_the_record_and_hides_it_from_the_active_list(
        self, estate: None, session: AsyncSession
    ) -> None:
        """ "Who was accountable last quarter" is exactly the question an audit of last
        quarter's campaign asks."""
        owner = await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.SHARE,
            target_key=SHARE_KEY,
            owner_subject="alice",
        )

        revoked = await service(session).revoke_owner(ADMIN, owner.owner_id)
        active, _ = await service(session).list_owners(target_key=SHARE_KEY)
        everything, _ = await service(session).list_owners(
            target_key=SHARE_KEY, include_revoked=True
        )

        assert not revoked.is_active
        assert revoked.revoked_by_subject == "gov-admin"
        assert active == ()
        assert len(everything) == 1

    async def test_the_same_party_may_be_recorded_again_after_revocation(
        self, estate: None, session: AsyncSession
    ) -> None:
        owner = await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.SHARE,
            target_key=SHARE_KEY,
            owner_subject="alice",
        )
        await service(session).revoke_owner(ADMIN, owner.owner_id)

        again = await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.SHARE,
            target_key=SHARE_KEY,
            owner_subject="alice",
        )

        assert again.is_active

    async def test_revoking_a_record_that_does_not_exist_is_a_not_found(
        self, estate: None, session: AsyncSession
    ) -> None:
        with pytest.raises(GovernanceNotFound):
            await service(session).revoke_owner(ADMIN, uuid.uuid4())

    async def test_ownership_events_live_on_their_own_chain(
        self, estate: None, session: AsyncSession
    ) -> None:
        await service(session).assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.SHARE,
            target_key=SHARE_KEY,
            owner_subject="alice",
        )

        events, _ = await GovernanceRepository(session).events_for_chain("owners")

        assert [event.event_type for event in events] == [GovernanceEventType.OWNER_ASSIGNED]


class TestWhatAnItemSaysAboutItsFooting:
    async def test_every_grant_names_the_version_it_came_from(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Re-reading that version reproduces the evidence exactly, which is what makes a
        campaign checkable long after the fact."""
        campaign_id = await a_campaign(session)
        outcome = await service(session).generate(ADMIN, campaign_id)

        for item in outcome.items:
            for grant in item.grants:
                assert grant.version_id > 0

    async def test_the_evidence_survives_a_round_trip_through_the_database(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_campaign(session)
        outcome = await service(session).generate(ADMIN, campaign_id)
        original = outcome.items[0]

        reloaded = await service(session).get_item(original.item_id)

        assert reloaded.grants == original.grants
        assert reloaded.evidence_digest == original.evidence_digest
        assert reloaded.certainty == original.certainty

    async def test_a_deny_entry_is_part_of_the_item_rather_than_a_separate_one(
        self, estate: None, session: AsyncSession, client: AsyncClient
    ) -> None:
        await replay(
            client,
            h.ntfs_scan(
                observations=[
                    h.resource(
                        FINANCE,
                        at=h.NEXT_MONDAY,
                        server_name=SERVER,
                        share_name=SHARE_NAME,
                        ace_count=2,
                    ),
                    h.ntfs_ace(FINANCE, ALICE, at=h.NEXT_MONDAY, access_mask=READ_MASK),
                    h.ntfs_ace(
                        FINANCE,
                        ALICE,
                        at=h.NEXT_MONDAY,
                        access_mask=0x10000,
                        ace_type=AceType.DENY,
                        order_index=1,
                    ),
                ],
                started_at=h.NEXT_MONDAY,
                server_name=SERVER,
                share_name=SHARE_NAME,
            ),
        )
        campaign_id = await a_campaign(session, baseline=h.NEXT_MONDAY_END)

        outcome = await service(session).generate(ADMIN, campaign_id)
        alice = next(
            item
            for item in outcome.items
            if item.target_key == FINANCE and item.principal_key == ALICE
        )

        assert len(alice.grants) == 2
        assert alice.has_deny
