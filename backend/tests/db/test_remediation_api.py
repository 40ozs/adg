"""Change plans over HTTP, as each role — and the estate, digested around all of it.

Two things are proved here that nothing else can prove.

**No API call changes Windows or ADG's copy of it.** Every collected table is digested before
a whole plan lifecycle runs over HTTP and again afterwards, and the two must be identical. A
plan is created, simulated, submitted, approved, exported and read back in between; if any of
that touched one byte of what a collector reported, a digest moves and the test names the
table. ``tests/remediation/test_no_write_path.py`` is the structural half — it proves no such
path *exists* — and this is the behavioral half, over the paths that actually run.

**The separation of duties survives the wiring.** Capability tables are easy to get right in a
unit test and easy to lose at the include site, so every route is driven as each role through
the real application.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Role
from app.config import Settings, build_settings
from app.db import Database
from app.main import create_app
from app.models.schema import metadata
from app.remediation.export import verify_document
from tests.support import history as h
from tests.support.auth import token_for_roles
from tests.support.ingest import replay

SIGNING_KEY = "a-development-change-plan-signing-key"

#: ADG's own records. Everything else holds what a collector reported, or is derived from it,
#: and none of it may move. Taken from the schema rather than listed, so a table added by a
#: later phase is covered automatically -- the guarantee is about *all* collected state, not a
#: sample somebody remembered to name.
ADG_OWNED = frozenset(
    {
        "resource_owners",
        "review_campaigns",
        "review_campaign_scopes",
        "review_assignments",
        "review_items",
        "review_decisions",
        "remediation_proposals",
        "governance_audit_events",
        "remediation_change_plans",
        "remediation_planned_changes",
        "remediation_approvals",
        "remediation_exports",
        "simulations",
        "simulation_evaluations",
    }
)

COLLECTED_TABLES = tuple(sorted(name for name in metadata.tables if name not in ADG_OWNED))

SERVER = "FS01"
SHARE_NAME = "Finance"
FINANCE = "\\\\fs01\\finance"
ALICE = "S-1-5-21-1004336348-1177238915-682003330-1101"
SYSTEM = "S-1-5-18"
READ_MASK = 0x1200A9
FULL_MASK = 0x1F01FF


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
def signing_settings(db_settings: Settings) -> Settings:
    """The test deployment, with a change-plan signing key.

    A key is required to export at all; ``db_settings`` deliberately has none, because the
    default posture is "this deployment cannot emit a signed instruction" and a fixture that
    quietly supplied one would hide that.
    """
    return build_settings(
        environment="test",
        log_format="text",
        database_url=db_settings.database_url,
        remediation_signing_key=SIGNING_KEY,
    )


@pytest.fixture
def as_role(
    signing_settings: Settings, database: Database
) -> Callable[..., AbstractAsyncContextManager[AsyncClient]]:
    """A client holding exactly the roles named, against a deployment that can sign.

    Each set of roles gets its own **subject**, derived from the role names. That is not
    cosmetic: ``token_for_roles`` defaults every token to one username, so without this the
    planner and the approver would be the same person and every separation-of-duties check
    would pass for the wrong reason — the service refusing a self-approval rather than the
    capability boundary doing its job. ``person`` overrides it where a test needs two
    different people holding the same role.
    """

    @asynccontextmanager
    async def build(*roles: Role, person: str | None = None) -> AsyncIterator[AsyncClient]:
        app = create_app(signing_settings)
        app.state.database = database
        username = person or "-".join(sorted(role.value for role in roles)) or "nobody"
        token = token_for_roles(signing_settings, *roles, username=username)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
        ) as active:
            yield active

    return build


@pytest.fixture
async def estate(client: AsyncClient) -> AsyncIterator[None]:
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
                h.ntfs_ace(FINANCE, ALICE, at=h.MONDAY, access_mask=READ_MASK),
                h.ntfs_ace(FINANCE, SYSTEM, at=h.MONDAY, access_mask=FULL_MASK, order_index=1),
            ],
            started_at=h.MONDAY,
            server_name=SERVER,
            share_name=SHARE_NAME,
        ),
    )
    yield


async def a_change_body(session: AsyncSession) -> dict[str, Any]:
    """One removal, built from the entry ADG actually holds."""
    from app.models.schema import ntfs_aces

    row = (
        await session.execute(
            sa.select(ntfs_aces).where(
                ntfs_aces.c.resource_key == FINANCE, ntfs_aces.c.trustee_sid == ALICE
            )
        )
    ).one()
    return {
        "kind": "remove_ntfs_ace",
        "target_kind": "resource",
        "target_key": FINANCE,
        "target_display": FINANCE,
        "principal_sid": ALICE,
        "principal_key": ALICE,
        "entry": {
            "ace_key": row.ace_key,
            "trustee_sid": row.trustee_sid,
            "trustee_key": row.trustee_key,
            "ace_type": row.ace_type,
            "access_mask": int(row.access_mask),
            "ace_flags": int(row.ace_flags),
            "source": row.source,
            "order_index": row.order_index,
        },
    }


async def a_plan_over_http(client: AsyncClient, session: AsyncSession) -> str:
    response = await client.post(
        "/api/v1/remediation",
        json={
            "title": "Remove Alice's direct access to Finance",
            "rationale": "Certified for removal in the Q3 access review.",
            "changes": [await a_change_body(session)],
        },
    )
    assert response.status_code == 201, response.text
    plan_id: str = response.json()["plan_id"]
    return plan_id


class TestTheWholeLifecycleOverHttp:
    async def test_a_plan_is_written_measured_approved_signed_and_read_back(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        async with as_role(Role.REMEDIATION_PLANNER) as planner:
            plan_id = await a_plan_over_http(planner, session)

            simulated = await planner.post(f"/api/v1/remediation/{plan_id}/simulation")
            assert simulated.status_code == 200, simulated.text
            assert simulated.json()["has_current_simulation"]

            submitted = await planner.post(f"/api/v1/remediation/{plan_id}/submission")
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "pending_approval"

        async with as_role(Role.REMEDIATION_APPROVER) as approver:
            approved = await approver.post(
                f"/api/v1/remediation/{plan_id}/approval",
                json={"decision": "approve", "rationale": "Reviewed the impact list."},
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "approved"
            assert approved.json()["approval_is_current"]

        async with as_role(Role.ADMIN) as operator:
            exported = await operator.post(f"/api/v1/remediation/{plan_id}/exports")
            assert exported.status_code == 201, exported.text
            body = exported.json()

            assert verify_document(body["document"], body["signature"], SIGNING_KEY)
            assert body["document"]["execution"]["performed_by_adg"] is False
            assert body["runbook"].startswith("#Requires -Version 7")

        async with as_role(Role.AUDITOR) as auditor:
            audit = await auditor.get(f"/api/v1/remediation/{plan_id}/audit")
            assert audit.status_code == 200
            assert audit.json()["intact"]
            assert [event["event_type"] for event in audit.json()["events"]] == [
                "plan.created",
                "plan.simulated",
                "plan.submitted",
                "plan.approved",
                "plan.exported",
            ]

    async def test_the_runbook_is_served_as_plain_text(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        """Plain text and no ``Content-Disposition``: a change plan a browser saves and offers
        to run on a double-click is what the ``-Execute`` switch exists to defuse."""
        plan_id = await _an_exported_plan(as_role, session)

        async with as_role(Role.ADMIN) as operator:
            exports = await operator.get(f"/api/v1/remediation/{plan_id}/exports")
            export_id = exports.json()[0]["export_id"]
            runbook = await operator.get(
                f"/api/v1/remediation/{plan_id}/exports/{export_id}/runbook"
            )

        assert runbook.status_code == 200
        assert runbook.headers["content-type"].startswith("text/plain")
        assert "content-disposition" not in runbook.headers
        assert "-Execute" in runbook.text


class TestNoRequestChangesTheEstate:
    async def test_every_collected_table_is_byte_identical_afterwards(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        """The phase's central acceptance criterion, measured rather than asserted."""
        before = await digest_of_collected_state(session)

        plan_id = await _an_exported_plan(as_role, session)
        async with as_role(Role.AUDITOR) as auditor:
            await auditor.get(f"/api/v1/remediation/{plan_id}")
            await auditor.get(f"/api/v1/remediation/{plan_id}/audit")
            await auditor.get("/api/v1/remediation/candidates")
            await auditor.get("/api/v1/remediation/execution-policy")

        after = await digest_of_collected_state(session)

        moved = [name for name in COLLECTED_TABLES if before[name] != after[name]]
        assert not moved, f"a remediation request changed collected state in {moved}"

    async def test_the_digest_would_notice_a_change(
        self, estate: None, session: AsyncSession
    ) -> None:
        """A comparison that could never fail is decoration."""
        from app.models.schema import ntfs_aces

        before = await digest_of_collected_state(session)
        await session.execute(
            sa.update(ntfs_aces)
            .where(ntfs_aces.c.trustee_sid == ALICE)
            .values(access_mask=FULL_MASK)
        )
        after = await digest_of_collected_state(session)
        await session.rollback()

        assert before["ntfs_aces"] != after["ntfs_aces"]


class TestTheDeploymentSaysWhatItCanDo:
    async def test_it_reports_that_it_cannot_execute(
        self, estate: None, as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]]
    ) -> None:
        """An operator's real question is "could this thing modify my domain?", and a
        guarantee only checkable by reading source code is one most people will not check."""
        async with as_role(Role.AUDITOR) as auditor:
            response = await auditor.get("/api/v1/remediation/execution-policy")

        body = response.json()
        assert body["can_execute"] is False
        assert body["mode"] == "disabled"
        assert body["requirements"]

    async def test_the_api_offers_no_route_that_carries_a_plan_out(
        self, estate: None, as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]]
    ) -> None:
        async with as_role(Role.ADMIN) as operator:
            response = await operator.post("/api/v1/remediation/execution")

        assert response.status_code in (404, 405)


class TestSeparationOfDutiesOverHttp:
    async def test_a_planner_cannot_approve(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        """403 from the capability: no role holding ``remediation:plan`` holds
        ``remediation:approve``."""
        async with as_role(Role.REMEDIATION_PLANNER) as planner:
            plan_id = await a_plan_over_http(planner, session)
            await planner.post(f"/api/v1/remediation/{plan_id}/simulation")
            await planner.post(f"/api/v1/remediation/{plan_id}/submission")

            response = await planner.post(
                f"/api/v1/remediation/{plan_id}/approval", json={"decision": "approve"}
            )

        assert response.status_code == 403

    async def test_an_approver_cannot_write_a_plan(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        async with as_role(Role.REMEDIATION_APPROVER) as approver:
            response = await approver.post(
                "/api/v1/remediation",
                json={
                    "title": "A plan by the approver",
                    "rationale": "Should not be possible.",
                    "changes": [await a_change_body(session)],
                },
            )

        assert response.status_code == 403

    async def test_an_approver_cannot_export(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        plan_id = await _an_approved_plan(as_role, session)

        async with as_role(Role.REMEDIATION_APPROVER) as approver:
            response = await approver.post(f"/api/v1/remediation/{plan_id}/exports")

        assert response.status_code == 403

    async def test_a_person_holding_two_roles_is_still_refused(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        """The rule is about hands, not about tokens (ADR-0038). Holding both roles is a
        legitimate configuration, and the service still refuses the second act."""
        async with as_role(
            Role.REMEDIATION_PLANNER, Role.REMEDIATION_APPROVER, person="wears-both-hats"
        ) as both:
            plan_id = await a_plan_over_http(both, session)
            await both.post(f"/api/v1/remediation/{plan_id}/simulation")
            await both.post(f"/api/v1/remediation/{plan_id}/submission")

            response = await both.post(
                f"/api/v1/remediation/{plan_id}/approval", json={"decision": "approve"}
            )

        assert response.status_code == 403
        assert "cannot also approve it" in response.json()["detail"]

    async def test_a_plain_viewer_reaches_nothing(
        self, estate: None, as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]]
    ) -> None:
        async with as_role(Role.VIEWER) as viewer:
            listed = await viewer.get("/api/v1/remediation")
            policy = await viewer.get("/api/v1/remediation/execution-policy")

        assert listed.status_code == 403
        assert policy.status_code == 403

    async def test_an_auditor_reads_and_writes_nothing(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        plan_id = await _an_approved_plan(as_role, session)

        async with as_role(Role.AUDITOR) as auditor:
            readable = await auditor.get(f"/api/v1/remediation/{plan_id}")
            writable = await auditor.post(
                f"/api/v1/remediation/{plan_id}/approval", json={"decision": "approve"}
            )

        assert readable.status_code == 200
        assert writable.status_code == 403


class TestTheRefusalsAreLegible:
    async def test_an_unmeasured_plan_is_refused_with_a_reason(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        async with as_role(Role.REMEDIATION_PLANNER) as planner:
            plan_id = await a_plan_over_http(planner, session)
            response = await planner.post(f"/api/v1/remediation/{plan_id}/submission")

        assert response.status_code == 409
        assert "blast-radius" in response.json()["detail"]

    async def test_a_widening_change_is_refused_as_a_bad_request(
        self,
        estate: None,
        session: AsyncSession,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        """Remediation that can grant access is not remediation."""
        change = await a_change_body(session)
        change["kind"] = "modify_ntfs_ace"
        change["after_access_mask"] = FULL_MASK

        async with as_role(Role.REMEDIATION_PLANNER) as planner:
            response = await planner.post(
                "/api/v1/remediation",
                json={
                    "title": "Widen Alice",
                    "rationale": "Should be refused.",
                    "changes": [change],
                },
            )

        assert response.status_code == 422
        assert "adds rights" in response.json()["detail"]["message"]

    async def test_an_unsigned_deployment_refuses_to_export(
        self,
        estate: None,
        session: AsyncSession,
        db_settings: Settings,
        database: Database,
        as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        """The default posture. An unsigned change plan is indistinguishable from one
        somebody typed."""
        plan_id = await _an_approved_plan(as_role, session)

        app = create_app(db_settings)  # no signing key
        app.state.database = database
        token = token_for_roles(db_settings, Role.ADMIN)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
        ) as operator:
            response = await operator.post(f"/api/v1/remediation/{plan_id}/exports")

        assert response.status_code == 409
        assert "ADG_REMEDIATION_SIGNING_KEY" in response.json()["detail"]


# --------------------------------------------------------------------------------------


async def _an_approved_plan(
    as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]], session: AsyncSession
) -> str:
    async with as_role(Role.REMEDIATION_PLANNER) as planner:
        plan_id = await a_plan_over_http(planner, session)
        await planner.post(f"/api/v1/remediation/{plan_id}/simulation")
        await planner.post(f"/api/v1/remediation/{plan_id}/submission")
    async with as_role(Role.REMEDIATION_APPROVER) as approver:
        approved = await approver.post(
            f"/api/v1/remediation/{plan_id}/approval", json={"decision": "approve"}
        )
        assert approved.status_code == 200, approved.text
    return plan_id


async def _an_exported_plan(
    as_role: Callable[..., AbstractAsyncContextManager[AsyncClient]], session: AsyncSession
) -> str:
    plan_id = await _an_approved_plan(as_role, session)
    async with as_role(Role.ADMIN) as operator:
        exported = await operator.post(f"/api/v1/remediation/{plan_id}/exports")
        assert exported.status_code == 201, exported.text
    return plan_id
