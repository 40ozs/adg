"""Review decisions cannot rewrite source permission observations. Measured, not asserted.

This is the phase's central acceptance criterion and the one that would be easiest to satisfy
on paper. So it is tested the expensive way: every collected table is digested before a
governance workflow runs and again afterwards, and the two digests must be identical.

A whole campaign happens in between — created, generated, assigned, activated, certified,
revoked, a decision superseded, a remediation proposed, an owner recorded and withdrawn, the
campaign closed. If any of that touched a single byte of what a collector reported, a digest
moves and the test names the table.

The structural half of the same criterion is ``tests/governance/test_isolation.py``, which
proves no code path *exists* that could write one. The two are complementary: this one covers
the paths that run, that one covers the paths that do not.
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    CampaignFocus,
    DecisionKind,
    RemediationAction,
    ReviewScopeKind,
    ReviewTargetKind,
)
from app.governance.model import CampaignScope
from app.governance.service import Actor, GovernanceService
from app.models.schema import metadata
from tests.support import history as h
from tests.support.ingest import replay

GOVERNANCE_TABLES = frozenset(
    {
        "resource_owners",
        "review_campaigns",
        "review_campaign_scopes",
        "review_assignments",
        "review_items",
        "review_decisions",
        "remediation_proposals",
        "governance_audit_events",
    }
)

#: Every table that holds what a collector reported, or is derived from it. Taken from the
#: schema rather than listed, so a table added by a later phase is covered automatically —
#: which is the point: the guarantee is about *all* collected state, not a sample somebody
#: remembered to name.
COLLECTED_TABLES = tuple(sorted(name for name in metadata.tables if name not in GOVERNANCE_TABLES))

SERVER = "FS01"
SHARE_NAME = "Finance"
SHARE_KEY = "fs01|finance"
FINANCE = "\\\\fs01\\finance"
ALICE = "S-1-5-21-1004336348-1177238915-682003330-1101"

ADMIN = Actor(subject="gov-admin", roles=("governance_admin",))
REVIEWER = Actor(subject="alice", roles=("reviewer",))
LATER = h.NEXT_MONDAY_END + dt.timedelta(days=1)


async def digest_of_collected_state(session: AsyncSession) -> dict[str, str]:
    """One digest per collected table, over every row and every column.

    Rows are ordered by their whole rendered content rather than by a key, so the digest does
    not depend on physical row order — and a column added later is included without this test
    being edited, which is what keeps the guarantee from quietly narrowing.
    """
    digests: dict[str, str] = {}
    for name in COLLECTED_TABLES:
        table = metadata.tables[name]
        rows = (await session.execute(sa.select(table))).mappings().all()
        rendered = sorted(
            "|".join(f"{key}={row[key]!r}" for key in sorted(row.keys())) for row in rows
        )
        digests[name] = hashlib.sha256("\n".join(rendered).encode("utf-8")).hexdigest()
    return digests


@pytest.fixture
async def estate(client: AsyncClient) -> None:
    await replay(
        client,
        h.ad_scan(
            observations=[h.principal(ALICE, at=h.MONDAY, display_name="CONTOSO\\alice")],
            started_at=h.MONDAY,
        ),
    )
    await replay(
        client,
        h.smb_scan(
            observations=[
                h.server(SERVER, at=h.MONDAY),
                h.share(SERVER, SHARE_NAME, at=h.MONDAY),
                h.share_ace(SERVER, SHARE_NAME, ALICE, at=h.MONDAY),
            ],
            started_at=h.MONDAY,
            server_name=SERVER,
        ),
    )
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE,
                    at=h.MONDAY,
                    server_name=SERVER,
                    share_name=SHARE_NAME,
                    ace_count=1,
                    owner_sid="S-1-5-32-544",
                ),
                h.ntfs_ace(FINANCE, ALICE, at=h.MONDAY, access_mask=0x1200A9),
            ],
            started_at=h.MONDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )


async def run_a_whole_campaign(session: AsyncSession) -> None:
    """Every governance act this phase implements, in one pass."""
    service = GovernanceService(session, now=LATER)

    owner = await service.assign_owner(
        ADMIN,
        target_kind=ReviewTargetKind.RESOURCE,
        target_key=FINANCE,
        owner_subject="alice",
        note="Finance data owner.",
    )
    campaign = await service.create_campaign(
        ADMIN,
        name="Isolation check",
        focus=CampaignFocus.RESOURCE,
        scopes=[CampaignScope(kind=ReviewScopeKind.SHARE, key=SHARE_KEY)],
        baseline_at=h.MONDAY_END,
    )
    await service.generate(ADMIN, campaign.campaign_id)
    await service.assign(ADMIN, campaign.campaign_id, reviewer_subject="alice")
    await service.activate(ADMIN, campaign.campaign_id)

    page = await service.list_items(campaign.campaign_id)
    first, second = page.items[0], page.items[1]

    await service.decide(REVIEWER, first.item_id, decision=DecisionKind.CERTIFY)
    # A change of mind, which supersedes the first answer.
    await service.decide(REVIEWER, first.item_id, decision=DecisionKind.REVOKE, rationale="Leaver.")
    await service.propose_remediation(REVIEWER, first.item_id, action=RemediationAction.REMOVE_ACE)
    await service.decide(
        REVIEWER, second.item_id, decision=DecisionKind.MODIFY, rationale="Read is enough."
    )
    await service.revoke_owner(ADMIN, owner.owner_id)
    await service.close(ADMIN, campaign.campaign_id)


class TestAReviewChangesNothingItReviews:
    async def test_no_collected_table_changes_across_a_whole_campaign(
        self, estate: None, session: AsyncSession
    ) -> None:
        before = await digest_of_collected_state(session)

        await run_a_whole_campaign(session)
        after = await digest_of_collected_state(session)

        changed = [name for name in COLLECTED_TABLES if before[name] != after[name]]

        assert not changed, (
            "A governance workflow changed collected state in: "
            + ", ".join(changed)
            + ". A review records a judgment about an observation and must never alter the "
            "observation; a decision that should lead to a change in Windows produces a "
            "remediation proposal, which ADG records and does not perform."
        )

    async def test_the_guarantee_covers_every_table_rather_than_a_sample(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A digest map that had quietly stopped covering the ACL tables would pass the test
        above forever."""
        assert "ntfs_aces" in COLLECTED_TABLES
        assert "smb_share_aces" in COLLECTED_TABLES
        assert "object_versions" in COLLECTED_TABLES
        assert "principals" in COLLECTED_TABLES
        assert not (set(COLLECTED_TABLES) & GOVERNANCE_TABLES)

    async def test_the_digest_actually_moves_when_collected_state_changes(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The other way this test could be vacuous: a digest that never changes proves
        nothing. This makes the smallest possible edit to a collected row and checks it is
        noticed."""
        before = await digest_of_collected_state(session)

        await session.execute(
            sa.text("UPDATE ntfs_aces SET order_index = 99 WHERE resource_key = :k"),
            {"k": FINANCE},
        )
        after = await digest_of_collected_state(session)

        assert before["ntfs_aces"] != after["ntfs_aces"]
        await session.rollback()

    async def test_a_revoke_decision_leaves_the_entry_it_was_about_in_place(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Stated on its own because it is the one somebody would expect to be false: a
        reviewer decided the grant should go, and the grant is still there. ADG records the
        decision and proposes the change; making it is somebody else's act, elsewhere."""
        before = (
            await session.execute(
                sa.text("SELECT count(*) FROM ntfs_aces WHERE resource_key = :k"), {"k": FINANCE}
            )
        ).scalar_one()

        await run_a_whole_campaign(session)
        after = (
            await session.execute(
                sa.text("SELECT count(*) FROM ntfs_aces WHERE resource_key = :k"), {"k": FINANCE}
            )
        ).scalar_one()
        proposals = (
            await session.execute(sa.text("SELECT count(*) FROM remediation_proposals"))
        ).scalar_one()

        assert after == before
        assert proposals == 1

    async def test_the_timeline_gains_no_version_from_a_review(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Governance must not write history either. A campaign that opened or closed a
        version would make a review look like an observation, and the next campaign cut
        against the same baseline would disagree with this one."""
        before = (
            await session.execute(sa.text("SELECT count(*) FROM object_versions"))
        ).scalar_one()

        await run_a_whole_campaign(session)
        after = (
            await session.execute(sa.text("SELECT count(*) FROM object_versions"))
        ).scalar_one()

        assert after == before

    async def test_no_scan_run_is_created_or_altered_by_a_review(
        self, estate: None, session: AsyncSession
    ) -> None:
        """Provenance answers "which collector saw this". A governance act appearing there
        would attribute a human decision to a scan."""
        before = (
            await session.execute(sa.text("SELECT count(*), max(updated_at) FROM scan_runs"))
        ).one()

        await run_a_whole_campaign(session)
        after = (
            await session.execute(sa.text("SELECT count(*), max(updated_at) FROM scan_runs"))
        ).one()

        assert tuple(before) == tuple(after)

    async def test_the_windows_owner_is_untouched_by_recording_an_adg_owner(
        self, estate: None, session: AsyncSession
    ) -> None:
        """The two are kept side by side and never merged: the gap between "ADG holds Alice
        accountable" and "Windows says BUILTIN\\Administrators owns it" is itself a finding.
        See ADR-0028."""
        service = GovernanceService(session, now=LATER)

        await service.assign_owner(
            ADMIN,
            target_kind=ReviewTargetKind.RESOURCE,
            target_key=FINANCE,
            owner_subject="alice",
        )
        windows_owner = (
            await session.execute(
                sa.text("SELECT owner_sid FROM ntfs_resources WHERE resource_key = :k"),
                {"k": FINANCE},
            )
        ).scalar_one()
        adg_owners, _ = await service.list_owners(target_key=FINANCE)

        assert windows_owner == "S-1-5-32-544"
        assert adg_owners[0].owner_subject == "alice"
        assert adg_owners[0].owner_principal_key is None
