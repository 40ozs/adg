"""The reviewer's workflow against a real estate: drift, context, bulk decisions, the queue.

Phase 10A proved a campaign can be frozen and answered. This is about the part a person
actually does, and every test here is about one of the four things that make a decision
evidence-based rather than ceremonial:

* **The reviewer is told when the thing they are certifying has moved.** The estate below
  changes under the campaign in four different ways on purpose — an entry widened, an entry
  withdrawn, a whole directory deleted, and a share nobody has re-scanned — because those are
  four different answers and three of them are an empty or different entry list.
* **The item is never rewritten.** Drift is computed beside it; the frozen evidence and its
  digest are what the decision and the audit event are about, and a test asserts they are
  byte-identical after a drift report has run over them.
* **A revocation that would not revoke anything says so.** Bob reaches DOCS directly *and*
  through a group, so removing his own entry leaves his access intact — the case a screen
  that showed only the entry would get wrong, and the most consequential field the context
  endpoint returns.
* **A bulk action is refused unless the items are one question**, and every gate the
  single-item path applies still applies per item.

These need PostgreSQL: the append-only triggers, the one-current-decision index and the
timeline's own interval arithmetic are the database's guarantees, not the application's.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Role
from app.domain import (
    CampaignFocus,
    CommentRequirement,
    DecisionKind,
    GovernanceEventType,
    ReviewItemStatus,
    ReviewScopeKind,
)
from app.governance.drift import DriftVerdict
from app.governance.model import CampaignScope, GenerationOptions
from app.governance.review import ReviewContextService
from app.governance.service import (
    Actor,
    GovernanceConflict,
    GovernanceForbidden,
    GovernanceNotFound,
    GovernanceService,
    NotHomogeneous,
)
from app.models.schema import review_items
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
DOCS = "\\\\fs01\\finance\\docs"
PROJECTS = "\\\\fs01\\finance\\projects"

ALICE = "S-1-5-21-1004336348-1177238915-682003330-1101"
BOB = "S-1-5-21-1004336348-1177238915-682003330-1102"
CAROL = "S-1-5-21-1004336348-1177238915-682003330-1103"
CONTRACTORS = "S-1-5-21-1004336348-1177238915-682003330-2201"
DOCS_RW = "S-1-5-21-1004336348-1177238915-682003330-2202"
EVERYONE = "S-1-1-0"
SYSTEM = "S-1-5-18"

READ_MASK = 0x1200A9
WRITE_MASK = 0x1301BF
FULL_MASK = 0x1F01FF

BASE = "/api/v1/governance"
ClientFactory = Callable[..., AbstractAsyncContextManager[AsyncClient]]

ADMIN = Actor(subject="gov-admin", display_name="Gov Admin", roles=("governance_admin",))
ALICE_REVIEWER = Actor(subject="alice", display_name="Alice", roles=("reviewer",))
BOB_REVIEWER = Actor(subject="bob", display_name="Bob", roles=("reviewer",))


@pytest.fixture
async def estate(client: AsyncClient) -> AsyncIterator[None]:
    """One share, scanned twice, moving in four different ways in between.

    Monday, the baseline every campaign below is cut against:

    * ``FINANCE`` — Alice reads, Bob reads, SYSTEM has full control.
    * ``HR`` — Contractors write.
    * ``DOCS`` — Bob reads directly, and the ``DOCS-RW`` group he belongs to writes.
    * ``PROJECTS`` — Carol reads.
    * the share ACL — Alice, and Everyone, so that the share layer is not what withholds
      access in the effective-access tests. Everyone is a well-known trustee and produces no
      item, which is exactly what makes it usable as scaffolding here.

    Friday, the file system is scanned again and:

    * Alice is widened to write on ``FINANCE`` — **modified**;
    * Contractors' entry on ``HR`` is gone, the directory is not — **removed**;
    * ``PROJECTS`` is not reported at all, so reconciliation tombstones it — **removed, and
      the target itself is gone**;
    * ``DOCS`` is unchanged, and the share is not re-scanned at all — **unchanged, against a
      target nothing has confirmed since Monday**.
    """
    await replay(
        client,
        h.ad_scan(
            observations=[
                h.principal(ALICE, at=MONDAY, display_name="CONTOSO\\alice"),
                h.principal(BOB, at=MONDAY, display_name="CONTOSO\\bob"),
                h.principal(CAROL, at=MONDAY, display_name="CONTOSO\\carol"),
                h.principal(CONTRACTORS, at=MONDAY, display_name="CONTOSO\\Contractors"),
                h.principal(DOCS_RW, at=MONDAY, display_name="CONTOSO\\Docs-RW"),
                h.edge(DOCS_RW, BOB, at=MONDAY),
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
                h.share_ace(SERVER, SHARE_NAME, EVERYONE, at=MONDAY, order_index=1),
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
                    FINANCE, at=MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=3
                ),
                h.ntfs_ace(FINANCE, ALICE, at=MONDAY, access_mask=READ_MASK),
                h.ntfs_ace(FINANCE, BOB, at=MONDAY, access_mask=READ_MASK, order_index=1),
                h.ntfs_ace(FINANCE, SYSTEM, at=MONDAY, access_mask=FULL_MASK, order_index=2),
                h.resource(HR, at=MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=1),
                h.ntfs_ace(HR, CONTRACTORS, at=MONDAY, access_mask=WRITE_MASK),
                h.resource(DOCS, at=MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=2),
                h.ntfs_ace(DOCS, BOB, at=MONDAY, access_mask=READ_MASK),
                h.ntfs_ace(DOCS, DOCS_RW, at=MONDAY, access_mask=WRITE_MASK, order_index=1),
                h.resource(
                    PROJECTS, at=MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=1
                ),
                h.ntfs_ace(PROJECTS, CAROL, at=MONDAY, access_mask=READ_MASK),
            ],
            started_at=MONDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE, at=FRIDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=3
                ),
                h.ntfs_ace(FINANCE, ALICE, at=FRIDAY, access_mask=WRITE_MASK),
                h.ntfs_ace(FINANCE, BOB, at=FRIDAY, access_mask=READ_MASK, order_index=1),
                h.ntfs_ace(FINANCE, SYSTEM, at=FRIDAY, access_mask=FULL_MASK, order_index=2),
                h.resource(HR, at=FRIDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=0),
                h.resource(DOCS, at=FRIDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=2),
                h.ntfs_ace(DOCS, BOB, at=FRIDAY, access_mask=READ_MASK),
                h.ntfs_ace(DOCS, DOCS_RW, at=FRIDAY, access_mask=WRITE_MASK, order_index=1),
            ],
            started_at=FRIDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )
    yield


def service(session: AsyncSession, *, now: dt.datetime = LATER) -> GovernanceService:
    return GovernanceService(session, now=now)


async def a_running_campaign(
    session: AsyncSession,
    *,
    reviewer: Actor = ALICE_REVIEWER,
    due_at: dt.datetime | None = None,
    comment_requirement: CommentRequirement = CommentRequirement.STANDARD,
    now: dt.datetime = LATER,
) -> uuid.UUID:
    campaign = await service(session, now=now).create_campaign(
        ADMIN,
        name="Finance recertification",
        focus=CampaignFocus.RESOURCE,
        scopes=[CampaignScope(kind=ReviewScopeKind.SHARE, key=SHARE_KEY)],
        baseline_at=h.MONDAY_END,
        due_at=due_at,
        options=GenerationOptions(),
        comment_requirement=comment_requirement,
    )
    campaign_id = campaign.campaign_id
    await service(session, now=now).generate(ADMIN, campaign_id)
    await service(session, now=now).assign(
        ADMIN,
        campaign_id,
        reviewer_subject=reviewer.subject,
        reviewer_display_name=reviewer.display_name,
    )
    await service(session, now=now).activate(ADMIN, campaign_id)
    return campaign_id


async def items_by_pair(
    session: AsyncSession, campaign_id: uuid.UUID
) -> dict[tuple[str, str], uuid.UUID]:
    page = await service(session).list_items(campaign_id, limit=200)
    return {(item.target_key, item.principal_key): item.item_id for item in page.items}


# --------------------------------------------------------------------------------- drift


class TestDriftTellsAReviewerWhatHasHappenedSinceTheFreeze:
    async def test_a_widened_entry_is_modified_and_names_the_field(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        report = await service(session).campaign_drift(campaign_id)
        drift = dict((item.item_id, drift) for item, drift in report.drifted)[
            items[(FINANCE, ALICE)]
        ]

        assert drift.verdict is DriftVerdict.MODIFIED
        assert drift.changes[0].fields == ("access_mask",)
        assert "rights" in drift.summary

    async def test_a_withdrawn_entry_on_a_directory_that_is_still_there_is_a_removal(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        report = await service(session).campaign_drift(campaign_id)
        drift = dict((item.item_id, drift) for item, drift in report.drifted)[
            items[(HR, CONTRACTORS)]
        ]

        assert drift.verdict is DriftVerdict.REMOVED
        assert drift.target_present is True
        assert "The target itself is gone" not in drift.summary

    async def test_a_deleted_directory_says_the_target_is_gone_not_the_grant(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Two removals that read identically from the entry list and mean different things:
        somebody took Contractors off HR, and somebody deleted PROJECTS entirely."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        report = await service(session).campaign_drift(campaign_id)
        drift = dict((item.item_id, drift) for item, drift in report.drifted)[
            items[(PROJECTS, CAROL)]
        ]

        assert drift.verdict is DriftVerdict.REMOVED
        assert drift.target_present is False
        assert "The target itself is gone" in drift.summary

    async def test_an_untouched_entry_is_unchanged(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        drifts = await ReviewContextService(session, now=LATER).drift_for(
            (await service(session).list_items(campaign_id, limit=200)).items
        )

        assert drifts[items[(DOCS, BOB)]].verdict is DriftVerdict.UNCHANGED
        assert not drifts[items[(DOCS, BOB)]].has_drifted

    async def test_an_unchanged_grant_on_a_share_nothing_rescanned_admits_it(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The quiet failure. The share ACL was read once, on Monday; its versions are still
        open, so the comparison finds the same entries and would happily say "unchanged" about
        an estate nothing has looked at for a week."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        drifts = await ReviewContextService(session, now=LATER).drift_for(
            (await service(session).list_items(campaign_id, limit=200)).items
        )
        drift = drifts[items[(SHARE_KEY, ALICE)]]

        assert drift.verdict is DriftVerdict.UNCHANGED
        assert "no scan has confirmed this target recently" in drift.summary

    async def test_comparing_before_the_estate_existed_is_unobserved_not_removed(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The third answer, and the one that must never render as a removal: a reviewer told
        "already gone" closes the item, and the access is still there."""
        campaign_id = await a_running_campaign(session)
        page = await service(session).list_items(campaign_id, limit=200)

        drifts = await ReviewContextService(session).drift_for(
            page.items, at=MONDAY - dt.timedelta(days=30)
        )

        assert {drift.verdict for drift in drifts.values()} == {DriftVerdict.UNOBSERVED}
        assert all(not drift.has_drifted for drift in drifts.values())


class TestDriftDoesNotRewriteTheItem:
    async def test_the_frozen_evidence_and_its_digest_are_untouched(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The decision is about what was frozen. A campaign that refreshed its own items
        would make every attestation already recorded a statement about whatever the grant
        became — which is the failure the baseline exists to prevent."""
        campaign_id = await a_running_campaign(session)
        before = (
            await session.execute(
                sa.select(
                    review_items.c.item_id,
                    review_items.c.evidence_digest,
                    review_items.c.grants,
                )
                .where(review_items.c.campaign_id == campaign_id)
                .order_by(review_items.c.item_id)
            )
        ).all()

        await service(session).campaign_drift(campaign_id)
        after = (
            await session.execute(
                sa.select(
                    review_items.c.item_id,
                    review_items.c.evidence_digest,
                    review_items.c.grants,
                )
                .where(review_items.c.campaign_id == campaign_id)
                .order_by(review_items.c.item_id)
            )
        ).all()

        assert before == after

    async def test_the_report_says_how_much_of_the_campaign_it_covered(
        self, estate: None, session: AsyncSession
    ) -> None:
        """ "Nothing has changed" must not be readable as more than "nothing in the part that
        was checked"."""
        campaign_id = await a_running_campaign(session)

        report = await service(session).campaign_drift(campaign_id, limit=2)

        assert report.covered == 2
        assert report.total_items > 2
        assert report.has_more is True

    async def test_only_drifted_items_are_listed_and_the_rest_are_counted(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)

        report = await service(session).campaign_drift(campaign_id)

        assert {item.target_key for item, _ in report.drifted} == {FINANCE, HR, PROJECTS}
        assert report.counts["modified"] == 1
        assert report.counts["removed"] == 2
        assert report.counts["unchanged"] == report.covered - 3


# ------------------------------------------------------------------------------- context


class TestTheReviewScreenHasWhatADecisionNeeds:
    async def test_the_baseline_answer_is_frozen_and_the_current_one_is_not(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Alice was widened from read to write on Friday. The campaign is about Monday, so
        the baseline answer must still be read — and the current one must show the write,
        beside it rather than instead of it."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(FINANCE, ALICE)])

        assert context.baseline_access.available
        assert context.current_access.available
        assert context.baseline_access.rights is not None
        assert context.current_access.rights is not None
        assert context.baseline_access.rights.value != context.current_access.rights.value

    async def test_an_entry_held_through_a_group_as_well_says_revoking_it_is_not_enough(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The most consequential answer on the screen. Bob is named on DOCS directly *and*
        is in DOCS-RW, which is also named on it. A reviewer who revokes his entry believing
        his access ends has recorded an attestation that says something untrue."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(DOCS, BOB)])

        assert context.reach.available
        assert context.reach.direct_paths >= 1
        assert context.reach.group_paths >= 1
        assert context.reach.removing_reviewed_entries_leaves_access is True
        assert DOCS_RW in {route.principal_key for route in context.reach.routes}

    async def test_the_group_route_is_named_rather_than_left_as_a_sid(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(DOCS, BOB)])
        route = next(r for r in context.reach.routes if r.principal_key == DOCS_RW)

        assert route.display_name == "CONTOSO\\Docs-RW"
        assert route.depth >= 1

    async def test_a_revocation_that_would_actually_end_access_says_so(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The other direction, so the assertion above is not passing for everything.

        Bob's entry on ``FINANCE`` is the only thing granting him file-system rights there —
        the ``Everyone`` entry on the share ACL is a share-layer grant and the two layers
        cross, so it gives him nothing on its own. Removing his entry ends his access, and
        ``removing_reviewed_entries_leaves_access`` must therefore be false here and true on
        ``DOCS``, where he is also in a group named on the folder.
        """
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        finance = await service(session).item_context(items[(FINANCE, BOB)])
        docs = await service(session).item_context(items[(DOCS, BOB)])

        assert finance.reach.removing_reviewed_entries_leaves_access is False
        assert docs.reach.removing_reviewed_entries_leaves_access is True

    async def test_a_group_route_names_the_layer_it_lands_on(
        self, estate: None, session: AsyncSession
    ) -> None:
        """``Everyone`` on the share ACL is a real route and is reported as one, tagged with
        the layer. A reviewer looking at why somebody reaches a folder needs both layers:
        hiding the share grant would make a revocation at one layer look sufficient."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(FINANCE, BOB)])

        assert {route.layer for route in context.reach.routes} == {"smb_share"}
        assert EVERYONE in {route.principal_key for route in context.reach.routes}

    async def test_the_last_change_to_the_reviewed_entry_is_reported(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(FINANCE, ALICE)])

        assert context.changes
        assert {change.kind for change in context.changes} == {"ntfs_ace"}
        assert all(change.reasons for change in context.changes)

    async def test_a_share_item_resolves_its_access_against_the_published_directory(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A share entry sits on the share's own ACL, and "what can they do" is a question
        about the file system reached through it."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(SHARE_KEY, ALICE)])

        assert context.resolved_resource_key == FINANCE
        assert context.current_access.available

    async def test_the_context_carries_the_drift_verdict_too(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(HR, CONTRACTORS)])

        assert context.drift.verdict is DriftVerdict.REMOVED

    async def test_open_risk_findings_about_this_grant_are_shown_beside_it(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Whatever rules the risk engine happens to carry, the *relation* is what decides
        the order here, so the assertion is about shape rather than about any one rule —
        which keeps this test from breaking every time a rule is added."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(FINANCE, ALICE)])

        assert context.findings_truncated is False
        assert {finding.relation for finding in context.findings} <= {
            "access",
            "target",
            "principal",
        }
        for finding in context.findings:
            if finding.relation == "access":
                assert finding.resource_key == FINANCE or finding.share_key == FINANCE
                assert finding.principal_key == ALICE

    async def test_a_finding_about_this_exact_grant_outranks_one_about_the_place(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A critical finding about a group elsewhere in the estate matters less to this
        decision than a medium one about this grant, so relation orders before severity."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        context = await service(session).item_context(items[(FINANCE, ALICE)])
        order = ["access", "target", "principal"]
        ranks = [order.index(finding.relation) for finding in context.findings]

        assert ranks == sorted(ranks)


# ------------------------------------------------------------------------ bulk decisions


class TestABulkDecisionIsStillIndividuallyAuditable:
    async def test_it_records_one_decision_per_item(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)
        batch = [items[(FINANCE, BOB)], items[(DOCS, BOB)]]

        outcome = await service(session).bulk_decide(
            ALICE_REVIEWER, campaign_id, item_ids=batch, decision=DecisionKind.CERTIFY
        )

        assert outcome.item_count == 2
        assert {decision.item_id for decision in outcome.decisions} == set(batch)
        assert len({decision.decision_id for decision in outcome.decisions}) == 2

    async def test_every_item_gets_its_own_audit_event_with_its_own_evidence_digest(
        self, estate: None, session: AsyncSession
    ) -> None:
        """What "individually auditable" has to mean: the record afterwards is the same as
        for the identical decisions made one at a time."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)
        batch = [items[(FINANCE, BOB)], items[(DOCS, BOB)]]

        await service(session).bulk_decide(
            ALICE_REVIEWER, campaign_id, item_ids=batch, decision=DecisionKind.CERTIFY
        )
        events, _, chain = await service(session).audit_trail(campaign_id)
        recorded = [
            event for event in events if event.event_type is GovernanceEventType.DECISION_RECORDED
        ]

        assert {event.item_id for event in recorded} == set(batch)
        assert len({event.payload["evidence_digest"] for event in recorded}) == 2
        assert all(event.payload["bulk"] is True for event in recorded)
        assert chain.intact

    async def test_the_events_say_the_decision_was_one_of_several(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Without it a bulk certification is indistinguishable from somebody working fast,
        and an auditor reading one event cannot find the others."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        await service(session).bulk_decide(
            ALICE_REVIEWER,
            campaign_id,
            item_ids=[items[(FINANCE, BOB)], items[(DOCS, BOB)]],
            decision=DecisionKind.CERTIFY,
        )
        events, _, _ = await service(session).audit_trail(campaign_id)
        recorded = [
            event for event in events if event.event_type is GovernanceEventType.DECISION_RECORDED
        ]

        assert all(event.payload["bulk_size"] == 2 for event in recorded)

    async def test_the_items_are_marked_decided(self, estate: None, session: AsyncSession) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        await service(session).bulk_decide(
            ALICE_REVIEWER,
            campaign_id,
            item_ids=[items[(FINANCE, BOB)], items[(DOCS, BOB)]],
            decision=DecisionKind.CERTIFY,
        )
        item = await service(session).get_item(items[(DOCS, BOB)])

        assert item.status is ReviewItemStatus.DECIDED
        assert item.current_decision_id is not None


class TestWhatABulkDecisionRefuses:
    async def test_an_item_whose_grant_has_drifted(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Precisely the item that needs reading, and a batch is where it would not be."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        with pytest.raises(NotHomogeneous) as raised:
            await service(session).bulk_decide(
                ALICE_REVIEWER,
                campaign_id,
                item_ids=[items[(FINANCE, BOB)], items[(FINANCE, ALICE)]],
                decision=DecisionKind.CERTIFY,
            )

        assert "changed since the campaign was frozen" in str(raised.value)
        assert str(items[(FINANCE, ALICE)]) in str(raised.value)

    async def test_more_than_one_principal_across_more_than_one_target(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        with pytest.raises(NotHomogeneous) as raised:
            await service(session).bulk_decide(
                ALICE_REVIEWER,
                campaign_id,
                item_ids=[items[(FINANCE, BOB)], items[(DOCS, DOCS_RW)]],
                decision=DecisionKind.CERTIFY,
            )

        assert "not one question" in str(raised.value)

    async def test_a_share_entry_and_a_file_system_entry_together(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        with pytest.raises(NotHomogeneous) as raised:
            await service(session).bulk_decide(
                ALICE_REVIEWER,
                campaign_id,
                item_ids=[items[(SHARE_KEY, ALICE)], items[(FINANCE, BOB)]],
                decision=DecisionKind.CERTIFY,
            )

        assert "different kinds of access-control list" in str(raised.value)

    async def test_an_item_that_already_carries_a_decision(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Changing one's mind supersedes a specific attestation and needs its own reason."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)
        await service(session).decide(
            ALICE_REVIEWER, items[(DOCS, BOB)], decision=DecisionKind.CERTIFY
        )

        with pytest.raises(NotHomogeneous) as raised:
            await service(session).bulk_decide(
                ALICE_REVIEWER,
                campaign_id,
                item_ids=[items[(FINANCE, BOB)], items[(DOCS, BOB)]],
                decision=DecisionKind.CERTIFY,
            )

        assert "already carry a decision" in str(raised.value)

    async def test_an_item_belonging_to_another_campaign(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A stray id must come back missing rather than be acted on quietly."""
        first = await a_running_campaign(session)
        second = await a_running_campaign(session)
        theirs = await items_by_pair(session, second)
        mine = await items_by_pair(session, first)

        with pytest.raises(GovernanceNotFound):
            await service(session).bulk_decide(
                ALICE_REVIEWER,
                first,
                item_ids=[mine[(FINANCE, BOB)], theirs[(DOCS, BOB)]],
                decision=DecisionKind.CERTIFY,
            )

    async def test_an_item_assigned_to_somebody_else(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The assignment gate is not skipped by batching. It is checked per item."""
        campaign_id = await a_running_campaign(session, reviewer=ALICE_REVIEWER)
        items = await items_by_pair(session, campaign_id)

        with pytest.raises(GovernanceForbidden):
            await service(session).bulk_decide(
                BOB_REVIEWER,
                campaign_id,
                item_ids=[items[(FINANCE, BOB)], items[(DOCS, BOB)]],
                decision=DecisionKind.CERTIFY,
            )

    async def test_a_campaign_that_is_not_active(self, estate: None, session: AsyncSession) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)
        await service(session).close(ADMIN, campaign_id)

        with pytest.raises(GovernanceConflict):
            await service(session).bulk_decide(
                ALICE_REVIEWER,
                campaign_id,
                item_ids=[items[(FINANCE, BOB)]],
                decision=DecisionKind.CERTIFY,
            )

    async def test_a_refused_batch_writes_nothing(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Refused whole rather than applied partly: a reviewer left unsure which of eleven
        items they answered is worse off than one told the batch failed."""
        campaign_id = await a_running_campaign(session, reviewer=ALICE_REVIEWER)
        items = await items_by_pair(session, campaign_id)

        with pytest.raises(GovernanceForbidden):
            await service(session).bulk_decide(
                BOB_REVIEWER,
                campaign_id,
                item_ids=[items[(FINANCE, BOB)], items[(DOCS, BOB)]],
                decision=DecisionKind.CERTIFY,
            )
        await session.rollback()
        status = await service(session).campaign_status(campaign_id)

        assert status.counts.decided == 0


class TestTheCampaignCommentRequirement:
    async def test_a_strict_campaign_refuses_an_unexplained_approval(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(
            session, comment_requirement=CommentRequirement.ALWAYS
        )
        items = await items_by_pair(session, campaign_id)

        with pytest.raises(Exception) as raised:
            await service(session).decide(
                ALICE_REVIEWER, items[(DOCS, BOB)], decision=DecisionKind.CERTIFY
            )

        assert "every decision" in str(raised.value)

    async def test_the_standard_campaign_accepts_one(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        decision = await service(session).decide(
            ALICE_REVIEWER, items[(DOCS, BOB)], decision=DecisionKind.CERTIFY
        )

        assert decision.rationale is None

    async def test_the_requirement_reaches_a_bulk_decision_too(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(
            session, comment_requirement=CommentRequirement.ALWAYS
        )
        items = await items_by_pair(session, campaign_id)

        with pytest.raises(Exception) as raised:
            await service(session).bulk_decide(
                ALICE_REVIEWER,
                campaign_id,
                item_ids=[items[(FINANCE, BOB)], items[(DOCS, BOB)]],
                decision=DecisionKind.CERTIFY,
            )

        assert "every decision" in str(raised.value)

    async def test_an_investigate_decision_must_say_what_to_investigate(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        with pytest.raises(Exception) as raised:
            await service(session).decide(
                ALICE_REVIEWER, items[(DOCS, BOB)], decision=DecisionKind.INVESTIGATE
            )

        assert "investigate" in str(raised.value)

    async def test_an_investigate_decision_is_recorded_and_the_database_accepts_it(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The migration widened a check constraint. This is what proves it ran."""
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)

        decision = await service(session).decide(
            ALICE_REVIEWER,
            items[(DOCS, BOB)],
            decision=DecisionKind.INVESTIGATE,
            rationale="Bob left the team in January; confirm with HR before removing.",
        )

        assert decision.decision is DecisionKind.INVESTIGATE
        stored = await service(session).item_decisions(items[(DOCS, BOB)])
        assert [entry.decision for entry in stored] == [DecisionKind.INVESTIGATE]


# --------------------------------------------------------------------- queue and progress


class TestTheReviewerQueue:
    async def test_it_lists_the_campaigns_with_work_outstanding(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session, reviewer=ALICE_REVIEWER)

        entries = await service(session).reviewer_queue("alice")

        assert [entry.campaign.campaign_id for entry in entries] == [campaign_id]
        assert entries[0].pending == entries[0].assigned > 0

    async def test_somebody_who_was_asked_nothing_has_an_empty_queue(
        self, estate: None, session: AsyncSession
    ) -> None:
        await a_running_campaign(session, reviewer=ALICE_REVIEWER)

        assert await service(session).reviewer_queue("bob") == ()

    async def test_a_revoked_assignment_drops_out_without_any_item_being_edited(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session, reviewer=ALICE_REVIEWER)
        assignments = await service(session).list_assignments(campaign_id)
        await service(session).revoke_assignment(ADMIN, campaign_id, assignments[0].assignment_id)

        assert await service(session).reviewer_queue("alice") == ()

    async def test_a_closed_campaign_is_not_on_the_queue(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        await service(session).close(ADMIN, campaign_id)

        assert await service(session).reviewer_queue("alice") == ()

    async def test_an_overdue_campaign_sorts_first(
        self, estate: None, session: AsyncSession
    ) -> None:
        overdue = await a_running_campaign(session, due_at=LATER - dt.timedelta(days=1))
        await a_running_campaign(session, due_at=LATER + dt.timedelta(days=30))

        entries = await service(session).reviewer_queue("alice")

        assert entries[0].campaign.campaign_id == overdue
        assert entries[0].overdue is True
        assert entries[1].overdue is False


class TestReviewerProgressAndOverdueReporting:
    async def test_a_reviewer_past_their_deadline_with_work_left_is_overdue(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session, due_at=LATER - dt.timedelta(days=1))

        status = await service(session).campaign_status(campaign_id)

        assert status.overdue is True
        assert [progress.overdue for progress in status.reviewers] == [True]

    async def test_a_reviewer_who_finished_late_is_not_overdue_but_is_counted_late(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Overdue describes work outstanding. Somebody who answered everything, late, is
        done — and the lateness still has to show, which is what late_decisions is for."""
        campaign_id = await a_running_campaign(session, due_at=LATER - dt.timedelta(days=1))
        page = await service(session).list_items(campaign_id, limit=200)
        for item in page.items:
            await service(session).decide(
                ALICE_REVIEWER, item.item_id, decision=DecisionKind.CERTIFY
            )

        status = await service(session).campaign_status(campaign_id)

        assert [progress.overdue for progress in status.reviewers] == [False]
        assert status.reviewers[0].late_decisions == len(page.items)
        assert sum(progress.late_decisions for progress in status.reviewers) == len(page.items)

    async def test_a_campaign_with_no_deadline_has_nobody_overdue(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)

        status = await service(session).campaign_status(campaign_id)

        assert status.overdue is False
        assert not any(progress.overdue for progress in status.reviewers)

    async def test_progress_counts_what_the_reviewer_was_asked_not_the_campaign(
        self, estate: None, session: AsyncSession
    ) -> None:
        campaign_id = await a_running_campaign(session)
        items = await items_by_pair(session, campaign_id)
        await service(session).decide(
            ALICE_REVIEWER, items[(DOCS, BOB)], decision=DecisionKind.CERTIFY
        )

        status = await service(session).campaign_status(campaign_id)

        assert status.reviewers[0].decided == 1
        assert status.reviewers[0].pending == status.reviewers[0].assigned - 1
        assert 0 < status.reviewers[0].completion < 1


# --------------------------------------------------------------------------- over HTTP


@pytest.fixture
async def manager(client_as: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with client_as(Role.GOVERNANCE_ADMIN) as active:
        yield active


@pytest.fixture
async def reviewer(client_as: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with client_as(Role.REVIEWER) as active:
        yield active


async def subject_of(active: AsyncClient) -> str:
    response = await active.get("/auth/me")
    assert response.status_code == 200, response.text
    return str(response.json()["subject"])


async def a_live_campaign(manager: AsyncClient, reviewer: AsyncClient, **overrides: object) -> str:
    """Created, generated, assigned to this reviewer, and activated."""
    body: dict[str, object] = {
        "name": "Finance recertification",
        "focus": "resource",
        "scopes": [{"kind": "share", "key": SHARE_KEY}],
        "baseline_at": h.MONDAY_END.isoformat().replace("+00:00", "Z"),
    }
    body.update(overrides)
    created = await manager.post(f"{BASE}/campaigns", json=body)
    assert created.status_code == 201, created.text
    campaign_id = created.json()["campaign_id"]

    generated = await manager.post(f"{BASE}/campaigns/{campaign_id}/generation")
    assert generated.status_code == 200, generated.text
    assigned = await manager.post(
        f"{BASE}/campaigns/{campaign_id}/assignments",
        json={"reviewer_subject": await subject_of(reviewer)},
    )
    assert assigned.status_code == 201, assigned.text
    activated = await manager.post(f"{BASE}/campaigns/{campaign_id}/activation")
    assert activated.status_code == 200, activated.text
    return str(campaign_id)


async def item_ids_over_http(active: AsyncClient, campaign_id: str) -> dict[tuple[str, str], str]:
    response = await active.get(f"{BASE}/campaigns/{campaign_id}/items", params={"limit": 200})
    assert response.status_code == 200, response.text
    return {
        (item["target_key"], item["principal_key"]): item["item_id"]
        for item in response.json()["items"]
    }


class TestTheWorkflowOverHttp:
    async def test_a_reviewer_sees_their_own_queue(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, reviewer)

        response = await reviewer.get(f"{BASE}/queue")

        assert response.status_code == 200, response.text
        body = response.json()
        assert [entry["campaign_id"] for entry in body["entries"]] == [campaign_id]
        assert body["total_pending"] > 0

    async def test_the_queue_shrinks_as_the_reviewer_answers(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        """The queue counts what is *outstanding*, so answering has to move it.

        A reviewer's identity is not varied here: ``client_as`` mints every client the same
        authentication subject, so two clients differ by role and not by person. "Somebody
        who was asked nothing" and "a reviewer stood down" are covered by the service-level
        suite above, where the subject can be chosen.
        """
        campaign_id = await a_live_campaign(manager, reviewer)
        items = await item_ids_over_http(manager, campaign_id)
        before = (await reviewer.get(f"{BASE}/queue")).json()["total_pending"]

        answered = await reviewer.post(
            f"{BASE}/campaigns/{campaign_id}/decisions",
            json={
                "item_ids": [items[(FINANCE, BOB)], items[(DOCS, BOB)]],
                "decision": "certify",
            },
        )
        after = (await reviewer.get(f"{BASE}/queue")).json()["total_pending"]

        assert answered.status_code == 201, answered.text
        assert after == before - 2

    async def test_the_item_detail_carries_its_drift(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        """A screen that can show an item without showing that its grant no longer exists is
        a screen that will."""
        campaign_id = await a_live_campaign(manager, reviewer)
        items = await item_ids_over_http(manager, campaign_id)

        response = await manager.get(f"{BASE}/items/{items[(HR, CONTRACTORS)]}")

        assert response.status_code == 200, response.text
        assert response.json()["drift"]["verdict"] == "removed"
        assert response.json()["drift"]["has_drifted"] is True

    async def test_the_context_endpoint_answers_the_whole_screen_in_one_call(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, reviewer)
        items = await item_ids_over_http(manager, campaign_id)

        response = await reviewer.get(f"{BASE}/items/{items[(DOCS, BOB)]}/context")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reach"]["removing_reviewed_entries_leaves_access"] is True
        assert body["baseline_access"]["available"] is True
        assert body["current_access"]["rights"]["label"]
        assert body["drift"]["verdict"] == "unchanged"
        assert body["comment_requirement"] == "standard"

    async def test_a_viewer_reaches_none_of_it(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient, client_as: ClientFactory
    ) -> None:
        campaign_id = await a_live_campaign(manager, reviewer)
        items = await item_ids_over_http(manager, campaign_id)

        async with client_as(Role.VIEWER) as viewer:
            for path in (
                f"{BASE}/queue",
                f"{BASE}/campaigns/{campaign_id}/drift",
                f"{BASE}/items/{items[(DOCS, BOB)]}/context",
            ):
                response = await viewer.get(path)
                assert response.status_code == 403, path

    async def test_a_governance_administrator_cannot_answer_a_batch(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        """Separation of duties reaches the bulk route too: whoever chooses the questions does
        not also give the answers, however many at a time."""
        campaign_id = await a_live_campaign(manager, reviewer)
        items = await item_ids_over_http(manager, campaign_id)

        response = await manager.post(
            f"{BASE}/campaigns/{campaign_id}/decisions",
            json={"item_ids": [items[(FINANCE, BOB)]], "decision": "certify"},
        )

        assert response.status_code == 403

    async def test_a_bulk_decision_records_each_item(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, reviewer)
        items = await item_ids_over_http(manager, campaign_id)

        response = await reviewer.post(
            f"{BASE}/campaigns/{campaign_id}/decisions",
            json={
                "item_ids": [items[(FINANCE, BOB)], items[(DOCS, BOB)]],
                "decision": "certify",
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["item_count"] == 2
        assert {entry["item_id"] for entry in body["decisions"]} == {
            items[(FINANCE, BOB)],
            items[(DOCS, BOB)],
        }
        assert "own decision" in body["note"]

    async def test_a_drifted_batch_is_refused_and_says_why(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, reviewer)
        items = await item_ids_over_http(manager, campaign_id)

        response = await reviewer.post(
            f"{BASE}/campaigns/{campaign_id}/decisions",
            json={"item_ids": [items[(FINANCE, ALICE)]], "decision": "certify"},
        )

        assert response.status_code == 422, response.text
        assert "changed since the campaign was frozen" in response.json()["detail"]

    async def test_the_campaign_drift_report_is_readable(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, reviewer)

        response = await manager.get(f"{BASE}/campaigns/{campaign_id}/drift")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counts"]["modified"] == 1
        assert body["counts"]["removed"] == 2
        assert {entry["drift"]["verdict"] for entry in body["drifted"]} == {
            "modified",
            "removed",
        }
        assert body["covered"] == body["total_items"]

    async def test_a_campaign_can_require_a_comment_on_every_decision(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(
            manager, reviewer, comment_requirement="always", name="Audited review"
        )
        items = await item_ids_over_http(manager, campaign_id)

        refused = await reviewer.post(
            f"{BASE}/items/{items[(DOCS, BOB)]}/decisions", json={"decision": "certify"}
        )
        accepted = await reviewer.post(
            f"{BASE}/items/{items[(DOCS, BOB)]}/decisions",
            json={"decision": "certify", "rationale": "Reviewed with the data owner."},
        )

        assert refused.status_code == 422, refused.text
        assert accepted.status_code == 201, accepted.text

    async def test_an_investigate_decision_is_accepted_by_the_database(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        """The migration widened a check constraint; this is what proves it ran."""
        campaign_id = await a_live_campaign(manager, reviewer)
        items = await item_ids_over_http(manager, campaign_id)

        response = await reviewer.post(
            f"{BASE}/items/{items[(DOCS, BOB)]}/decisions",
            json={
                "decision": "investigate",
                "rationale": "Bob left the team in January; confirm with HR first.",
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["decision"] == "investigate"
