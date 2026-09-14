"""The collector protocol, against a real PostgreSQL.

What is being tested here is not "does the insert work" but the four promises the contract
makes to a collector: a retried start is recognized, a retried batch is not applied twice,
re-ingesting the same facts converges instead of duplicating, and a run cannot report
coverage it did not achieve.
"""

from __future__ import annotations

import copy
import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schema import (
    membership_edges,
    observations,
    principal_aliases,
    principals,
    scan_run_batches,
    scan_run_scopes,
    scan_runs,
)
from tests.fixtures import load_scenario
from tests.support.ingest import ingest_scenario, replay, scenario_document

ALICE = "S-1-5-21-1004336348-1177238915-682003330-1104"
FINANCE_TEAM = "S-1-5-21-1004336348-1177238915-682003330-1201"
FINANCE_RW = "S-1-5-21-1004336348-1177238915-682003330-1202"


async def count(session: AsyncSession, table: sa.Table) -> int:
    return int((await session.execute(sa.select(sa.func.count()).select_from(table))).scalar_one())


class TestRunLifecycle:
    async def test_a_run_is_created_stored_and_readable(self, client: AsyncClient) -> None:
        scenario = load_scenario("03-nested-group-grant")
        result = await ingest_scenario(client, "03-nested-group-grant")

        response = await client.get(f"/api/v1/scan-runs/{result['run_id']}")
        body = response.json()

        assert response.status_code == 200
        assert body["status"] == "succeeded"
        assert body["collector"] == scenario.start.source.collector.value
        assert body["collector_host"] == scenario.start.source.collector_host
        assert body["batch_count_received"] == 1
        assert body["observation_count_applied"] == 5
        assert body["declared_scopes"]
        assert body["errors"] == []

    async def test_an_unknown_run_is_a_404(self, client: AsyncClient) -> None:
        response = await client.get(f"/api/v1/scan-runs/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_replaying_a_start_returns_200_and_creates_one_run(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = scenario_document("03-nested-group-grant")

        first = await client.post("/api/v1/scan-runs", json=document["start"])
        second = await client.post("/api/v1/scan-runs", json=document["start"])

        assert first.status_code == 201
        assert first.json()["created"] is True
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert await count(session, scan_runs) == 1
        assert await count(session, scan_run_scopes) == len(document["start"]["scopes"])

    async def test_reusing_a_run_id_for_a_different_source_is_a_conflict(
        self, client: AsyncClient
    ) -> None:
        document = scenario_document("03-nested-group-grant")
        await client.post("/api/v1/scan-runs", json=document["start"])

        impostor = copy.deepcopy(document["start"])
        impostor["source"]["collector_host"] = "SOMEONE-ELSE"
        response = await client.post("/api/v1/scan-runs", json=impostor)

        assert response.status_code == 409
        assert "different collector source" in response.json()["detail"]

    async def test_reusing_a_run_id_for_different_scopes_is_a_conflict(
        self, client: AsyncClient
    ) -> None:
        document = scenario_document("03-nested-group-grant")
        await client.post("/api/v1/scan-runs", json=document["start"])

        widened = copy.deepcopy(document["start"])
        widened["scopes"] = [*widened["scopes"], {"kind": "server", "key": "fs99"}]
        response = await client.post("/api/v1/scan-runs", json=widened)

        assert response.status_code == 409
        assert "already declared scopes" in response.json()["detail"]

    async def test_a_batch_for_an_unknown_run_is_a_404(self, client: AsyncClient) -> None:
        document = scenario_document("03-nested-group-grant")
        batch = document["batches"][0]

        response = await client.post(f"/api/v1/scan-runs/{batch['run_id']}/batches", json=batch)

        assert response.status_code == 404
        assert "POST the start envelope" in response.json()["detail"]

    async def test_a_batch_after_completion_is_a_409(self, client: AsyncClient) -> None:
        document = scenario_document("03-nested-group-grant")
        await replay(client, document)

        extra = copy.deepcopy(document["batches"][0])
        extra["batch_id"] = str(uuid.uuid4())
        response = await client.post(
            f"/api/v1/scan-runs/{document['start']['run_id']}/batches", json=extra
        )

        assert response.status_code == 409
        assert "already succeeded" in response.json()["detail"]

    async def test_a_batch_posted_to_the_wrong_run_is_rejected(self, client: AsyncClient) -> None:
        document = scenario_document("03-nested-group-grant")
        await client.post("/api/v1/scan-runs", json=document["start"])

        response = await client.post(
            f"/api/v1/scan-runs/{uuid.uuid4()}/batches", json=document["batches"][0]
        )

        assert response.status_code == 422
        assert "belongs to" in str(response.json()["detail"])


class TestBatchIdempotency:
    async def test_a_replayed_batch_is_acknowledged_but_not_applied(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = scenario_document("03-nested-group-grant")
        await client.post("/api/v1/scan-runs", json=document["start"])
        batch = document["batches"][0]
        url = f"/api/v1/scan-runs/{batch['run_id']}/batches"

        first = await client.post(url, json=batch)
        second = await client.post(url, json=batch)
        third = await client.post(url, json=batch)

        assert first.status_code == second.status_code == third.status_code == 202
        assert first.json() == {
            "run_id": batch["run_id"],
            "batch_id": batch["batch_id"],
            "applied": 5,
            "duplicate": False,
            "principals_written": 3,
            "edges_written": 2,
            # This transcript is the AD half only; the SMB counters exist because the same
            # endpoint stores share facts, and report zero when none were sent.
            "servers_written": 0,
            "shares_written": 0,
            "share_aces_written": 0,
        }
        assert second.json()["duplicate"] is True
        assert second.json()["applied"] == 0
        assert third.json()["duplicate"] is True

        assert await count(session, scan_run_batches) == 1
        assert await count(session, observations) == 5
        assert await count(session, principals) == 3
        assert await count(session, membership_edges) == 2

        run = await client.get(f"/api/v1/scan-runs/{batch['run_id']}")
        assert run.json()["batch_count_received"] == 1

    async def test_reingesting_the_same_batch_in_a_new_run_does_not_duplicate(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ingest_scenario(client, "03-nested-group-grant")
        principals_after_first = await count(session, principals)
        edges_after_first = await count(session, membership_edges)

        await ingest_scenario(client, "03-nested-group-grant")

        assert await count(session, principals) == principals_after_first == 3
        assert await count(session, membership_edges) == edges_after_first == 2
        # Provenance, however, accumulates: two runs saw these objects.
        assert await count(session, observations) == 10
        assert await count(session, scan_runs) == 2

    async def test_the_second_run_becomes_the_last_observer(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        first = await ingest_scenario(client, "03-nested-group-grant")
        second = await ingest_scenario(client, "03-nested-group-grant")

        row = (
            (
                await session.execute(
                    sa.select(principals).where(principals.c.principal_key == ALICE)
                )
            )
            .mappings()
            .one()
        )

        assert str(row["first_observed_run_id"]) == first["run_id"]
        assert str(row["last_observed_run_id"]) == second["run_id"]

    async def test_a_stale_observation_does_not_overwrite_a_newer_one(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A delayed run must not resurrect an old name over a newer one."""
        recent = scenario_document("03-nested-group-grant")
        for batch in recent["batches"]:
            for observation in batch["observations"]:
                observation["observed_at"] = "2026-09-14T12:00:00Z"
                if observation.get("display_name") == "Alice Smith":
                    observation["display_name"] = "Alice Renamed"
        await replay(client, recent)

        stale = scenario_document("03-nested-group-grant")
        for batch in stale["batches"]:
            for observation in batch["observations"]:
                observation["observed_at"] = "2026-01-01T00:00:00Z"
        await replay(client, stale)

        row = (
            (
                await session.execute(
                    sa.select(principals).where(principals.c.principal_key == ALICE)
                )
            )
            .mappings()
            .one()
        )
        assert row["display_name"] == "Alice Renamed"
        # The older run still contributes what only it knows: when the object was first seen.
        assert row["first_observed_at"].isoformat().startswith("2026-01-01")
        assert row["last_observed_at"].isoformat().startswith("2026-09-14")

    async def test_every_observed_name_is_kept_as_an_alias(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        first = scenario_document("03-nested-group-grant")
        await replay(client, first)

        renamed = scenario_document("03-nested-group-grant")
        for batch in renamed["batches"]:
            for observation in batch["observations"]:
                observation["observed_at"] = "2026-09-20T00:00:00Z"
                if observation.get("display_name") == "Finance-Team":
                    observation["display_name"] = "Finance-Team-Renamed"
        await replay(client, renamed)

        rows = (
            (
                await session.execute(
                    sa.select(principal_aliases)
                    .where(principal_aliases.c.principal_key == FINANCE_TEAM)
                    .order_by(principal_aliases.c.value)
                )
            )
            .mappings()
            .all()
        )
        values = [row["value"] for row in rows if row["alias_kind"] == "display_name"]
        assert values == ["Finance-Team", "Finance-Team-Renamed"]


class TestEveryContractKind:
    async def test_a_verbatim_transcript_is_accepted_whole(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # Until Phase 3A this batch was refused for carrying the two NTFS kinds, which was
        # right then: telling a collector "accepted" about observations that were dropped
        # would have reported coverage ADG did not have. Now every kind is stored, so a
        # published transcript replays exactly as a collector would send it - no subsetting.
        from tests.fixtures import load_raw
        from tests.support.ingest import with_run_id

        document = with_run_id(load_raw("10-smb-more-restrictive"))
        await client.post("/api/v1/scan-runs", json=document["start"])

        response = await client.post(
            f"/api/v1/scan-runs/{document['start']['run_id']}/batches",
            json=document["batches"][0],
        )

        assert response.status_code == 202, response.text
        assert response.json()["applied"] == len(document["batches"][0]["observations"])
        assert await count(session, scan_run_batches) == 1


class TestCompletion:
    async def test_a_succeeded_run_records_its_reconciled_scopes(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = scenario_document("03-nested-group-grant")
        document["completion"]["reconciled_scopes"] = document["start"]["scopes"]
        await replay(client, document)

        reconciled = (
            (
                await session.execute(
                    sa.select(scan_run_scopes).where(scan_run_scopes.c.reconciled.is_(True))
                )
            )
            .mappings()
            .all()
        )
        assert len(reconciled) == len(document["start"]["scopes"])

    async def test_reconciling_an_undeclared_scope_is_a_conflict(self, client: AsyncClient) -> None:
        document = scenario_document("03-nested-group-grant")
        run_id = document["start"]["run_id"]
        await client.post("/api/v1/scan-runs", json=document["start"])
        for batch in document["batches"]:
            await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)

        document["completion"]["reconciled_scopes"] = [{"kind": "server", "key": "never-declared"}]
        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
        )

        assert response.status_code == 409
        assert "never declared" in response.json()["detail"]

    async def test_an_incremental_run_may_not_reconcile(self, client: AsyncClient) -> None:
        document = scenario_document("03-nested-group-grant")
        document["start"]["incremental"] = True
        run_id = document["start"]["run_id"]
        await client.post("/api/v1/scan-runs", json=document["start"])
        for batch in document["batches"]:
            await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)

        document["completion"]["reconciled_scopes"] = document["start"]["scopes"]
        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
        )

        assert response.status_code == 409
        assert "incremental" in response.json()["detail"]

    async def test_a_run_that_lost_batches_is_downgraded_to_partial(
        self, client: AsyncClient
    ) -> None:
        document = scenario_document("03-nested-group-grant")
        run_id = document["start"]["run_id"]
        await client.post("/api/v1/scan-runs", json=document["start"])
        for batch in document["batches"]:
            await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)

        # The collector says it sent five batches; one arrived.
        document["completion"]["batch_count"] = 5
        document["completion"]["reconciled_scopes"] = document["start"]["scopes"]
        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
        )
        body = response.json()

        assert response.status_code == 200
        assert body["status"] == "partial"
        assert body["reconciled_scopes"] == 0
        assert "1 arrived" in body["downgrade_reason"]

        run = (await client.get(f"/api/v1/scan-runs/{run_id}")).json()
        assert run["reconciled_scopes"] == []

    async def test_a_partial_run_records_its_errors(self, client: AsyncClient) -> None:
        document = scenario_document("12-partial-run-no-reconciliation")
        result = await replay(client, document)

        run = (await client.get(f"/api/v1/scan-runs/{result['run_id']}")).json()

        assert run["status"] == "partial"
        assert run["reconciled_scopes"] == []
        assert run["errors"], "a partial run must say what it could not read"
        assert run["error_count"] >= len(run["errors"])

    async def test_a_replayed_completion_is_acknowledged(self, client: AsyncClient) -> None:
        document = scenario_document("03-nested-group-grant")
        await replay(client, document)

        again = await client.post(
            f"/api/v1/scan-runs/{document['start']['run_id']}/completion",
            json=document["completion"],
        )

        assert again.status_code == 200
        assert again.json()["already_completed"] is True

    async def test_a_contradicting_completion_is_a_conflict(self, client: AsyncClient) -> None:
        document = scenario_document("03-nested-group-grant")
        await replay(client, document)

        contradiction = copy.deepcopy(document["completion"])
        contradiction["status"] = "failed"
        # A failed run may not reconcile, so the claim has to drop its scopes to be a valid
        # envelope at all; what is under test is the server's refusal to revise an outcome.
        contradiction["reconciled_scopes"] = []
        response = await client.post(
            f"/api/v1/scan-runs/{document['start']['run_id']}/completion", json=contradiction
        )

        assert response.status_code == 409
        assert "not revisable" in response.json()["detail"]


class TestStoredShape:
    async def test_local_group_principals_and_edges_keep_their_host(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """BUILTIN\\Administrators on two servers must be two rows, not one."""
        builtin = "S-1-5-32-544"
        run_id = str(uuid.uuid4())
        start = {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": {
                "collector": "local_groups",
                "collector_host": "COLLECTOR01",
                "method": "Get-LocalGroupMember",
            },
            "started_at": "2026-09-14T08:00:00Z",
            "scopes": [
                {"kind": "local_groups_host", "key": "fs01"},
                {"kind": "local_groups_host", "key": "fs02"},
            ],
        }
        observations_payload = []
        for host, member in (("fs01", ALICE), ("fs02", FINANCE_TEAM)):
            observations_payload.append(
                {
                    "schema_version": "1.0",
                    "kind": "principal",
                    "run_id": run_id,
                    "observed_at": "2026-09-14T08:00:00Z",
                    "source_key": f"principal|{host}|{builtin}",
                    "sid": builtin,
                    "principal_kind": "local_group",
                    "host_key": host,
                    "display_name": "BUILTIN\\Administrators",
                }
            )
            observations_payload.append(
                {
                    "schema_version": "1.0",
                    "kind": "membership_edge",
                    "run_id": run_id,
                    "observed_at": "2026-09-14T08:00:00Z",
                    "source_key": f"edge|{host}|{builtin}->{member}|local_group_member",
                    "group_sid": builtin,
                    "member_sid": member,
                    "edge_kind": "local_group_member",
                    "host_key": host,
                }
            )

        assert (await client.post("/api/v1/scan-runs", json=start)).status_code == 201
        batch = {
            "schema_version": "1.0",
            "run_id": run_id,
            "batch_id": str(uuid.uuid4()),
            "sequence": 1,
            "is_final": True,
            "observations": observations_payload,
        }
        response = await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)
        assert response.status_code == 202, response.text

        keys = (
            (
                await session.execute(
                    sa.select(principals.c.principal_key).order_by(principals.c.principal_key)
                )
            )
            .scalars()
            .all()
        )
        assert list(keys) == [f"fs01|{builtin}", f"fs02|{builtin}"]

        members = (
            await session.execute(
                sa.select(membership_edges.c.group_key, membership_edges.c.member_key).order_by(
                    membership_edges.c.group_key
                )
            )
        ).all()
        assert [tuple(row) for row in members] == [
            (f"fs01|{builtin}", ALICE),
            (f"fs02|{builtin}", FINANCE_TEAM),
        ]

    @pytest.mark.parametrize(
        "name", ["01-direct-user-grant", "02-group-grant", "04-multiple-membership-paths"]
    )
    async def test_every_ad_observation_in_a_scenario_is_stored(
        self, client: AsyncClient, session: AsyncSession, name: str
    ) -> None:
        scenario = load_scenario(name)
        await ingest_scenario(client, name)

        assert await count(session, principals) == len(scenario.principals)
        assert await count(session, membership_edges) == len(scenario.edges)
        assert await count(session, observations) == len(scenario.principals) + len(scenario.edges)
