"""The governance HTTP surface, driven as each role would drive it.

``tests/api/test_authorization.py`` proves every route is behind the right capability, with a
database that explodes if anything reaches it. This does the other half: it runs the real
requests against a real estate and checks what each role can actually *do* — which is where
the separation of duties either holds or turns out to be decoration.

Three properties are the reason this file exists rather than more unit tests:

* a plain viewer cannot see or touch anything governance (the phase's fourth acceptance
  criterion);
* a governance administrator can run a campaign and cannot answer one, and a reviewer the
  reverse — neither can do the other's half by holding their own role;
* holding ``governance:review`` is not authority over an item. The assignment is, and it is
  checked against the database on every decision.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

import pytest
from httpx import AsyncClient

from app.auth.roles import Role
from tests.support import history as h
from tests.support.ingest import replay

SERVER = "FS01"
SHARE_NAME = "Finance"
SHARE_KEY = "fs01|finance"
FINANCE = "\\\\fs01\\finance"
ALICE = "S-1-5-21-1004336348-1177238915-682003330-1101"
SYSTEM = "S-1-5-18"

BASE = "/api/v1/governance"
BASELINE = h.MONDAY_END.isoformat().replace("+00:00", "Z")

ClientFactory = Callable[..., AbstractAsyncContextManager[AsyncClient]]


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
                    FINANCE, at=h.MONDAY, server_name=SERVER, share_name=SHARE_NAME, ace_count=2
                ),
                h.ntfs_ace(FINANCE, ALICE, at=h.MONDAY, access_mask=0x1200A9),
                # A well-known trustee, so the campaign has something to exclude and the
                # exclusion tally it reports is a real number rather than an empty object.
                h.ntfs_ace(FINANCE, SYSTEM, at=h.MONDAY, access_mask=0x1F01FF, order_index=1),
            ],
            started_at=h.MONDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )


def campaign_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Q1 Finance review",
        "focus": "resource",
        "scopes": [{"kind": "share", "key": SHARE_KEY}],
        "baseline_at": BASELINE,
    }
    body.update(overrides)
    return body


@pytest.fixture
async def manager(client_as: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with client_as(Role.GOVERNANCE_ADMIN) as active:
        yield active


@pytest.fixture
async def reviewer(client_as: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with client_as(Role.REVIEWER) as active:
        yield active


async def a_live_campaign(manager: AsyncClient, reviewer_subject: str) -> str:
    """Created, generated, assigned and activated — ready for decisions."""
    created = await manager.post(f"{BASE}/campaigns", json=campaign_body())
    assert created.status_code == 201, created.text
    campaign_id = created.json()["campaign_id"]

    generated = await manager.post(f"{BASE}/campaigns/{campaign_id}/generation")
    assert generated.status_code == 200, generated.text

    assigned = await manager.post(
        f"{BASE}/campaigns/{campaign_id}/assignments",
        json={"reviewer_subject": reviewer_subject},
    )
    assert assigned.status_code == 201, assigned.text

    activated = await manager.post(f"{BASE}/campaigns/{campaign_id}/activation")
    assert activated.status_code == 200, activated.text
    return str(campaign_id)


async def subject_of(client: AsyncClient) -> str:
    response = await client.get("/auth/me")
    assert response.status_code == 200, response.text
    return str(response.json()["subject"])


class TestAViewerHoldsNoGovernanceAuthority:
    """The phase's fourth acceptance criterion, exercised rather than declared."""

    async def test_a_viewer_cannot_create_a_campaign(
        self, estate: None, client_as: ClientFactory
    ) -> None:
        async with client_as(Role.VIEWER) as viewer:
            response = await viewer.post(f"{BASE}/campaigns", json=campaign_body())

        assert response.status_code == 403
        assert "governance:manage" in response.json()["detail"]

    async def test_a_viewer_cannot_list_campaigns(
        self, estate: None, client_as: ClientFactory
    ) -> None:
        """A decision rationale can name a person and say something about them that no
        access-control list ever would, so reading attestations is a wider disclosure than
        reading the estate."""
        async with client_as(Role.VIEWER) as viewer:
            response = await viewer.get(f"{BASE}/campaigns")

        assert response.status_code == 403

    async def test_a_viewer_cannot_record_a_decision(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient, client_as: ClientFactory
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))
        items = await manager.get(f"{BASE}/campaigns/{campaign_id}/items")
        item_id = items.json()["items"][0]["item_id"]

        async with client_as(Role.VIEWER) as viewer:
            response = await viewer.post(
                f"{BASE}/items/{item_id}/decisions", json={"decision": "certify"}
            )

        assert response.status_code == 403

    async def test_a_viewer_can_still_read_the_estate(
        self, estate: None, client_as: ClientFactory
    ) -> None:
        """The governance capabilities are additional, not a re-partitioning: nothing a
        viewer could do before this phase stopped working."""
        async with client_as(Role.VIEWER) as viewer:
            response = await viewer.get("/api/v1/servers")

        assert response.status_code == 200


class TestTheSeparationOfDuties:
    async def test_a_governance_admin_runs_a_campaign(
        self, estate: None, manager: AsyncClient
    ) -> None:
        created = await manager.post(f"{BASE}/campaigns", json=campaign_body())

        assert created.status_code == 201
        assert created.json()["status"] == "draft"
        assert created.json()["snapshot_digest"] is None

    async def test_a_governance_admin_cannot_answer_an_item(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        """Whoever chooses the questions does not also give the answers. A single account
        able to do both can decide what it will be asked and then rubber-stamp it, and the
        audit trail would show a complete, compliant campaign."""
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))
        items = await manager.get(f"{BASE}/campaigns/{campaign_id}/items")
        item_id = items.json()["items"][0]["item_id"]

        response = await manager.post(
            f"{BASE}/items/{item_id}/decisions", json={"decision": "certify"}
        )

        assert response.status_code == 403
        assert "governance:review" in response.json()["detail"]

    async def test_a_reviewer_cannot_create_a_campaign(
        self, estate: None, reviewer: AsyncClient
    ) -> None:
        response = await reviewer.post(f"{BASE}/campaigns", json=campaign_body())

        assert response.status_code == 403
        assert "governance:manage" in response.json()["detail"]

    async def test_a_reviewer_cannot_assign_themselves(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        created = await manager.post(f"{BASE}/campaigns", json=campaign_body())
        campaign_id = created.json()["campaign_id"]

        response = await reviewer.post(
            f"{BASE}/campaigns/{campaign_id}/assignments",
            json={"reviewer_subject": await subject_of(reviewer)},
        )

        assert response.status_code == 403

    async def test_a_reviewer_cannot_close_a_campaign(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))

        response = await reviewer.post(f"{BASE}/campaigns/{campaign_id}/closure")

        assert response.status_code == 403

    async def test_an_auditor_reads_everything_and_changes_nothing(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient, client_as: ClientFactory
    ) -> None:
        """An auditor's job is to verify that attestations happened and by whom. That is the
        whole of it."""
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))

        async with client_as(Role.AUDITOR) as auditor:
            readable = await auditor.get(f"{BASE}/campaigns/{campaign_id}")
            audit = await auditor.get(f"{BASE}/campaigns/{campaign_id}/audit")
            refused = await auditor.post(f"{BASE}/campaigns", json=campaign_body())

        assert readable.status_code == 200
        assert audit.status_code == 200
        assert refused.status_code == 403

    async def test_a_platform_admin_cannot_run_or_answer_a_review(
        self, estate: None, client_as: ClientFactory
    ) -> None:
        """`admin` configures the server. Running an access review is a compliance function,
        and letting whoever operates ADG quietly create and close attestation campaigns is
        exactly what an auditor would object to. See ADR-0029."""
        async with client_as(Role.ADMIN) as admin:
            created = await admin.post(f"{BASE}/campaigns", json=campaign_body())
            readable = await admin.get(f"{BASE}/campaigns")

        assert created.status_code == 403
        assert readable.status_code == 200

    async def test_holding_both_roles_permits_both_halves(
        self, estate: None, client_as: ClientFactory
    ) -> None:
        """The separation is a default, not a prohibition: a small organization where one
        person does both is expressible, and the role assignment says so out loud."""
        async with client_as(Role.GOVERNANCE_ADMIN, Role.REVIEWER) as both:
            campaign_id = await a_live_campaign(both, await subject_of(both))
            items = await both.get(f"{BASE}/campaigns/{campaign_id}/items")
            decided = await both.post(
                f"{BASE}/items/{items.json()['items'][0]['item_id']}/decisions",
                json={"decision": "certify"},
            )

        assert decided.status_code == 201


class TestTheAssignmentIsTheSecondGate:
    async def test_the_assigned_reviewer_may_decide(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))
        items = await reviewer.get(f"{BASE}/campaigns/{campaign_id}/items", params={"mine": True})
        item_id = items.json()["items"][0]["item_id"]

        response = await reviewer.post(
            f"{BASE}/items/{item_id}/decisions", json={"decision": "certify"}
        )

        assert response.status_code == 201
        assert response.json()["current"] is True
        assert response.json()["decided_by_subject"] == await subject_of(reviewer)

    async def test_a_reviewer_holding_the_capability_but_not_the_item_is_refused(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        """The capability admitted the request. The assignment is what grants authority over
        this particular item, and it is checked against the database."""
        campaign_id = await a_live_campaign(manager, "somebody-else")
        items = await manager.get(f"{BASE}/campaigns/{campaign_id}/items")
        item_id = items.json()["items"][0]["item_id"]

        response = await reviewer.post(
            f"{BASE}/items/{item_id}/decisions", json={"decision": "certify"}
        )

        assert response.status_code == 403
        assert "assigned to another reviewer" in response.json()["detail"]

    async def test_a_reviewers_queue_contains_only_their_own_items(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, "somebody-else")

        mine = await reviewer.get(f"{BASE}/campaigns/{campaign_id}/items", params={"mine": True})

        assert mine.status_code == 200
        assert mine.json()["items"] == []
        assert mine.json()["page"]["total"] == 0


class TestTheWholeWorkflowOverHttp:
    async def test_a_campaign_can_be_run_end_to_end(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        subject = await subject_of(reviewer)
        campaign_id = await a_live_campaign(manager, subject)

        items = (await reviewer.get(f"{BASE}/campaigns/{campaign_id}/items")).json()["items"]
        for index, item in enumerate(items):
            body = (
                {"decision": "certify"}
                if index == 0
                else {"decision": "revoke", "rationale": "Left the department."}
            )
            answered = await reviewer.post(f"{BASE}/items/{item['item_id']}/decisions", json=body)
            assert answered.status_code == 201, answered.text

        status = await manager.get(f"{BASE}/campaigns/{campaign_id}/status")
        closed = await manager.post(f"{BASE}/campaigns/{campaign_id}/closure")

        assert status.json()["pending_items"] == 0
        assert status.json()["completion"] == 1.0
        assert status.json()["decisions_by_kind"] == {"certify": 1, "revoke": 1}
        assert closed.json()["status"] == "closed"

    async def test_the_item_carries_its_frozen_evidence_and_its_footing(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))
        items = (await manager.get(f"{BASE}/campaigns/{campaign_id}/items")).json()["items"]
        resource_item = next(item for item in items if item["target_kind"] == "resource")

        assert resource_item["grants"], "an item is its evidence and must never be empty"
        grant = resource_item["grants"][0]
        assert grant["trustee_sid"] == ALICE
        assert grant["access_mask"] == 0x1200A9
        assert grant["version_id"] > 0
        assert grant["certainty"] in {"observed", "inferred", "backfilled", "unobserved"}
        assert resource_item["principal_display_name"] == "CONTOSO\\alice"

    async def test_the_detail_view_shows_every_decision_including_superseded_ones(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))
        item_id = (await manager.get(f"{BASE}/campaigns/{campaign_id}/items")).json()["items"][0][
            "item_id"
        ]
        await reviewer.post(f"{BASE}/items/{item_id}/decisions", json={"decision": "certify"})
        await reviewer.post(
            f"{BASE}/items/{item_id}/decisions",
            json={"decision": "revoke", "rationale": "Leaver."},
        )

        detail = (await reviewer.get(f"{BASE}/items/{item_id}")).json()

        assert [entry["decision"] for entry in detail["decisions"]] == ["certify", "revoke"]
        assert detail["decisions"][0]["current"] is False
        assert detail["decisions"][1]["current"] is True

    async def test_a_revoke_without_a_reason_is_refused(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))
        item_id = (await manager.get(f"{BASE}/campaigns/{campaign_id}/items")).json()["items"][0][
            "item_id"
        ]

        response = await reviewer.post(
            f"{BASE}/items/{item_id}/decisions", json={"decision": "revoke"}
        )

        assert response.status_code == 422
        assert "must say why" in str(response.json())

    async def test_a_remediation_proposal_says_adg_did_not_perform_it(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))
        item_id = (await manager.get(f"{BASE}/campaigns/{campaign_id}/items")).json()["items"][0][
            "item_id"
        ]
        await reviewer.post(
            f"{BASE}/items/{item_id}/decisions",
            json={"decision": "revoke", "rationale": "Leaver."},
        )

        proposed = await reviewer.post(
            f"{BASE}/items/{item_id}/remediation", json={"action": "remove_ace"}
        )

        assert proposed.status_code == 201
        assert proposed.json()["status"] == "proposed"
        assert "does not perform it" in proposed.json()["note"]
        assert proposed.json()["ace_keys"]

    async def test_the_status_reports_what_the_campaign_did_not_ask_about(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        """So that "2 of 2 certified" is never readable as coverage of the whole estate."""
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))

        status = (await manager.get(f"{BASE}/campaigns/{campaign_id}/status")).json()

        assert status["campaign"]["excluded_counts"]
        assert status["campaign"]["options"]["include_inherited"] is False
        assert status["campaign"]["options"]["include_builtin"] is False

    async def test_verification_reports_the_campaign_as_reproducible(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))

        verification = (await manager.get(f"{BASE}/campaigns/{campaign_id}/verification")).json()

        assert verification["reproducible"] is True
        assert verification["stored_digest"] == verification["recomputed_digest"]
        assert "is reproducible" in verification["explanation"]

    async def test_the_audit_trail_verifies_and_names_its_head(
        self, estate: None, manager: AsyncClient, reviewer: AsyncClient
    ) -> None:
        campaign_id = await a_live_campaign(manager, await subject_of(reviewer))
        item_id = (await manager.get(f"{BASE}/campaigns/{campaign_id}/items")).json()["items"][0][
            "item_id"
        ]
        await reviewer.post(f"{BASE}/items/{item_id}/decisions", json={"decision": "certify"})

        audit = (await manager.get(f"{BASE}/campaigns/{campaign_id}/audit")).json()

        assert audit["chain"]["intact"] is True
        assert audit["chain"]["head_digest"]
        assert [event["event_type"] for event in audit["events"]][-1] == "decision.recorded"
        assert audit["events"][-1]["actor_roles"] == ["reviewer"]


class TestOwnershipOverHttp:
    async def test_an_owner_is_recorded_and_labelled_as_adg_metadata(
        self, estate: None, manager: AsyncClient
    ) -> None:
        created = await manager.post(
            f"{BASE}/owners",
            json={
                "target_kind": "resource",
                "target_key": FINANCE,
                "owner_subject": "alice",
                "owner_display_name": "Alice",
            },
        )

        assert created.status_code == 201
        assert created.json()["is_adg_metadata"] is True
        assert created.json()["active"] is True

    async def test_naming_both_an_adg_user_and_a_windows_principal_is_refused(
        self, estate: None, manager: AsyncClient
    ) -> None:
        response = await manager.post(
            f"{BASE}/owners",
            json={
                "target_kind": "resource",
                "target_key": FINANCE,
                "owner_subject": "alice",
                "owner_principal_key": ALICE,
            },
        )

        assert response.status_code == 422
        assert "exactly one of them" in str(response.json())

    async def test_naming_neither_is_refused(self, estate: None, manager: AsyncClient) -> None:
        response = await manager.post(
            f"{BASE}/owners", json={"target_kind": "resource", "target_key": FINANCE}
        )

        assert response.status_code == 422

    async def test_withdrawing_keeps_the_record(self, estate: None, manager: AsyncClient) -> None:
        created = await manager.post(
            f"{BASE}/owners",
            json={"target_kind": "share", "target_key": SHARE_KEY, "owner_subject": "alice"},
        )
        owner_id = created.json()["owner_id"]

        revoked = await manager.delete(f"{BASE}/owners/{owner_id}")
        active = await manager.get(f"{BASE}/owners", params={"target_key": SHARE_KEY})
        everything = await manager.get(
            f"{BASE}/owners", params={"target_key": SHARE_KEY, "include_revoked": True}
        )

        assert revoked.json()["active"] is False
        assert active.json()["items"] == []
        assert len(everything.json()["items"]) == 1

    async def test_a_reviewer_cannot_record_ownership(
        self, estate: None, reviewer: AsyncClient
    ) -> None:
        response = await reviewer.post(
            f"{BASE}/owners",
            json={"target_kind": "share", "target_key": SHARE_KEY, "owner_subject": "alice"},
        )

        assert response.status_code == 403


class TestRequestsTheApiRefuses:
    async def test_a_future_baseline_is_refused_with_a_reason(
        self, estate: None, manager: AsyncClient
    ) -> None:
        future = (dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=1)).isoformat()

        response = await manager.post(f"{BASE}/campaigns", json=campaign_body(baseline_at=future))

        assert response.status_code == 422
        assert "future" in str(response.json())

    async def test_a_naive_baseline_is_refused_before_it_reaches_the_domain(
        self, estate: None, manager: AsyncClient
    ) -> None:
        response = await manager.post(
            f"{BASE}/campaigns", json=campaign_body(baseline_at="2026-03-02T09:05:00")
        )

        assert response.status_code == 422

    async def test_a_scope_that_does_not_match_the_focus_is_refused(
        self, estate: None, manager: AsyncClient
    ) -> None:
        response = await manager.post(
            f"{BASE}/campaigns",
            json=campaign_body(focus="principal", scopes=[{"kind": "share", "key": SHARE_KEY}]),
        )

        assert response.status_code == 422
        assert "cannot be scoped by share" in str(response.json())

    async def test_activating_an_ungenerated_campaign_is_a_conflict_not_a_validation_error(
        self, estate: None, manager: AsyncClient
    ) -> None:
        """409 rather than 422: the request was well-formed and the state refuses it. Telling
        the caller to fix their body would send them looking in the wrong place."""
        created = await manager.post(f"{BASE}/campaigns", json=campaign_body())
        campaign_id = created.json()["campaign_id"]

        response = await manager.post(f"{BASE}/campaigns/{campaign_id}/activation")

        assert response.status_code == 409
        assert "Generate it before activating" in response.json()["detail"]

    async def test_an_unknown_campaign_is_a_404(self, estate: None, manager: AsyncClient) -> None:
        response = await manager.get(f"{BASE}/campaigns/00000000-0000-0000-0000-00000000dead")

        assert response.status_code == 404

    async def test_a_control_character_in_a_key_is_refused_before_the_database(
        self, estate: None, manager: AsyncClient
    ) -> None:
        """Phase 6D found that a NUL byte survives every parser and reaches psycopg as a
        500. Every free-text key on this surface is pattern-checked for the same reason."""
        response = await manager.get(f"{BASE}/owners", params={"target_key": "fs01\x00finance"})

        assert response.status_code == 422

    async def test_an_unauthenticated_request_is_401_with_a_challenge(
        self, estate: None, anonymous_client: AsyncClient
    ) -> None:
        response = await anonymous_client.get(f"{BASE}/campaigns")

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
