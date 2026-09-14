r"""Storing servers, shares, and share ACLs against a real PostgreSQL.

These go through HTTP, like the AD ingestion tests, because the status codes are part of the
collector contract. What they check is the part that cannot be checked without a database:
that a replay converges, that a share survives a scan that never mentioned it, that an
unresolved trustee neither blocks ingestion nor becomes permanently unresolved, and that the
check constraints bite.

The most important assertions here are about **absence**. A share that a failed scan did not
report still exists; deleting it would tell an operator that access was revoked when it was
not. There is no delete path in the ingestion service at all, and these tests are what keeps
one from being added by accident.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schema import principal_references, servers, smb_share_aces, smb_shares
from tests.fixtures import load_raw
from tests.support.ingest import replay, storable, storable_document, with_run_id

FINANCE = "fs01|finance"
TRUSTEE = "S-1-5-11"
"""The share-ACL trustee in scenario 10: Authenticated Users, which nothing describes."""


async def counts(session: AsyncSession) -> dict[str, int]:
    result = {}
    for name, table in (
        ("servers", servers),
        ("shares", smb_shares),
        ("aces", smb_share_aces),
        ("references", principal_references),
    ):
        result[name] = int(
            (await session.execute(sa.select(sa.func.count()).select_from(table))).scalar_one()
        )
    return result


async def share_row(session: AsyncSession, share_key: str = FINANCE) -> Any:
    return (
        (await session.execute(sa.select(smb_shares).where(smb_shares.c.share_key == share_key)))
        .mappings()
        .one_or_none()
    )


def observation(document: dict[str, Any], kind: str) -> dict[str, Any]:
    """The first observation of ``kind``, still attached to the document so it can be edited."""
    for batch in document["batches"]:
        for item in batch["observations"]:
            if item["kind"] == kind:
                found: dict[str, Any] = item
                return found
    raise AssertionError(f"no {kind} observation in this transcript")


def at(document: dict[str, Any], day: str) -> dict[str, Any]:
    """Move a whole transcript to another day, consistently.

    Every timestamp moves together: the database refuses a run that completed before it
    started, which is the right constraint and makes a half-shifted transcript a hard
    error rather than a confusing one.
    """
    moved = copy.deepcopy(document)
    moved["start"]["started_at"] = f"{day}T08:00:00Z"
    for batch in moved["batches"]:
        for item in batch["observations"]:
            item["observed_at"] = f"{day}T08:00:01Z"
    moved["completion"]["completed_at"] = f"{day}T09:00:00Z"
    return moved


def only(document: dict[str, Any], kinds: set[str]) -> dict[str, Any]:
    """Drop every observation outside ``kinds``, fixing the completion's counts."""
    trimmed = copy.deepcopy(document)
    batches = []
    sent = 0
    for batch in trimmed["batches"]:
        kept = [item for item in batch["observations"] if item["kind"] in kinds]
        if kept:
            batch["observations"] = kept
            sent += len(kept)
            batches.append(batch)
    trimmed["batches"] = batches
    trimmed["completion"]["batch_count"] = len(batches)
    trimmed["completion"]["observation_count"] = sent
    return trimmed


class TestStoringOneScan:
    async def test_a_transcript_lands_as_servers_shares_and_aces(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("10-smb-more-restrictive"))

        assert (await counts(session))["shares"] == 1
        row = await share_row(session)
        assert row is not None
        assert row["server_key"] == "fs01"
        assert row["name"] == "Finance"
        assert row["local_path"] == "D:\\Shares\\Finance"
        assert row["share_type"] == "disk"

    async def test_the_response_counts_what_it_stored(self, client: AsyncClient) -> None:
        result = await replay(client, storable_document("10-smb-more-restrictive"))

        applied = result["batches"][0]
        assert applied["servers_written"] == 1
        assert applied["shares_written"] == 1
        assert applied["share_aces_written"] == 1

    async def test_an_ace_keeps_the_form_its_source_reported(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("10-smb-more-restrictive"))

        row = (await session.execute(sa.select(smb_share_aces))).mappings().one()
        assert row["permission"] == "read"
        assert row["access_mask"] is None
        assert row["right_token"] == "read"

    async def test_every_scenario_stores_its_smb_half(self, client: AsyncClient) -> None:
        # The canonical transcripts are the shared data set; if one of them stops ingesting,
        # every later phase built on it is affected.
        for name in ("01-direct-user-grant", "06-unresolved-sid", "11-ntfs-more-restrictive"):
            await replay(client, storable_document(name))

    async def test_a_batch_carrying_both_layers_is_accepted(self, client: AsyncClient) -> None:
        # Phase 3A. A combined run reads a share and the directory it publishes in one pass,
        # and the two layers travel together naturally; nothing merges them on arrival.
        document = with_run_id(load_raw("10-smb-more-restrictive"))
        run_id = document["start"]["run_id"]
        assert (await client.post("/api/v1/scan-runs", json=document["start"])).status_code == 201

        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/batches", json=document["batches"][0]
        )

        assert response.status_code == 202, response.text
        kinds = {item["kind"] for item in document["batches"][0]["observations"]}
        assert {"smb_ace", "ntfs_ace"} <= kinds, "the fixture must exercise both layers"


class TestReplay:
    async def test_resending_every_step_of_a_run_changes_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # What a collector retrying a flaky upload actually does: the same start, the
        # same batch, the same completion, each sent twice.
        document = storable_document("10-smb-more-restrictive")
        run_id = document["start"]["run_id"]
        await replay(client, document)
        before = await counts(session)

        started = await client.post("/api/v1/scan-runs", json=document["start"])
        completed = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
        )

        assert started.status_code == 200
        assert completed.status_code == 200
        assert completed.json()["already_completed"] is True
        assert await counts(session) == before

    async def test_a_completed_run_refuses_more_observations(self, client: AsyncClient) -> None:
        # Not a replay: new facts under a closed run would be attributed to coverage
        # that run already reported on.
        document = storable_document("10-smb-more-restrictive")
        run_id = document["start"]["run_id"]
        await replay(client, document)

        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/batches", json=document["batches"][0]
        )

        assert response.status_code == 409

    async def test_a_duplicate_batch_is_acknowledged_and_not_reapplied(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = storable_document("10-smb-more-restrictive")
        run_id = document["start"]["run_id"]
        await client.post("/api/v1/scan-runs", json=document["start"])
        await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=document["batches"][0])
        before = await counts(session)

        again = await client.post(
            f"/api/v1/scan-runs/{run_id}/batches", json=document["batches"][0]
        )

        assert again.status_code == 202
        body = again.json()
        assert (body["duplicate"], body["applied"], body["shares_written"]) == (True, 0, 0)
        assert await counts(session) == before

    async def test_a_fresh_run_of_the_same_facts_updates_provenance_only(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("10-smb-more-restrictive"))
        first = await share_row(session)
        assert first is not None

        await replay(client, at(storable_document("10-smb-more-restrictive"), "2026-10-01"))

        after = await share_row(session)
        assert after is not None
        assert (await counts(session))["shares"] == 1
        assert after["first_observed_at"] == first["first_observed_at"]
        assert after["last_observed_at"] > first["last_observed_at"]
        assert after["last_observed_run_id"] != first["last_observed_run_id"]

    async def test_an_older_scan_arriving_late_cannot_overwrite_newer_facts(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        current = at(storable_document("10-smb-more-restrictive"), "2026-10-01")
        observation(current, "smb_share")["description"] = "current"
        await replay(client, current)

        stale = at(storable_document("10-smb-more-restrictive"), "2026-08-01")
        observation(stale, "smb_share")["description"] = "stale"
        await replay(client, stale)

        row = await share_row(session)
        assert row is not None
        assert row["description"] == "current", "a delayed old scan must not rewrite the present"
        # ... but it does push the first sighting further back, which is genuinely new.
        assert row["first_observed_at"].isoformat().startswith("2026-08-01")


class TestRenamingAndRepointing:
    async def test_a_share_repointed_at_a_new_path_stays_one_share(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # A share is a publication, not the directory: re-pointing it is a change to the
        # same share, and splitting it in two would hide that the ACL still applies.
        await replay(client, storable_document("10-smb-more-restrictive"))

        moved = at(storable_document("10-smb-more-restrictive"), "2026-10-01")
        observation(moved, "smb_share")["local_path"] = "E:\\Data\\Finance"
        await replay(client, moved)

        assert (await counts(session))["shares"] == 1
        row = await share_row(session)
        assert row is not None
        assert row["local_path"] == "E:\\Data\\Finance"

    async def test_a_differently_cased_spelling_is_the_same_share(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("10-smb-more-restrictive"))

        shouted = at(storable_document("10-smb-more-restrictive"), "2026-10-01")
        for batch in shouted["batches"]:
            for item in batch["observations"]:
                if item["kind"] in ("smb_share", "smb_ace"):
                    item["server_name"] = "fs01"
                    item["share_name"] = "FINANCE"
                if item["kind"] == "server":
                    item["name"] = "fs01"
        await replay(client, shouted)

        stored = await counts(session)
        assert stored["shares"] == 1
        assert stored["servers"] == 1
        row = await share_row(session)
        assert row is not None
        assert row["name"] == "FINANCE", "the newest spelling is what is displayed"

    async def test_a_renamed_share_becomes_a_second_share_and_the_first_survives(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # A rename is genuinely a new share identity — the old name may be re-used for
        # something else tomorrow. The old row stays until a reconciled scope says it is
        # gone, which is Phase 7's decision, not this endpoint's.
        await replay(client, storable_document("10-smb-more-restrictive"))

        renamed = at(storable_document("10-smb-more-restrictive"), "2026-10-01")
        for batch in renamed["batches"]:
            for item in batch["observations"]:
                if item["kind"] in ("smb_share", "smb_ace"):
                    item["share_name"] = "Finance-Archive"
                    item["source_key"] = item["source_key"].replace("finance", "finance-archive")
        await replay(client, renamed)

        assert (await counts(session))["shares"] == 2
        assert await share_row(session) is not None
        assert await share_row(session, "fs01|finance-archive") is not None

    async def test_a_local_path_is_normalized_before_it_is_stored(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = storable_document("10-smb-more-restrictive")
        observation(document, "smb_share")["local_path"] = "d:/Shares//Finance/"
        await replay(client, document)

        row = await share_row(session)
        assert row is not None
        assert row["local_path"] == "D:\\Shares\\Finance"


class TestUnresolvedTrustees:
    async def test_an_unknown_trustee_does_not_block_ingestion(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # Only the SMB half: nothing has described the trustee, and the ACL must still land.
        document = only(
            storable(load_raw("10-smb-more-restrictive")), {"server", "smb_share", "smb_ace"}
        )
        await replay(client, with_run_id(document))

        assert (await counts(session))["aces"] == 1
        reference = (await session.execute(sa.select(principal_references))).mappings().one()
        assert reference["principal_key"] == TRUSTEE
        assert reference["reference_key"] == FINANCE

    async def test_the_acl_reports_the_trustee_unresolved(self, client: AsyncClient) -> None:
        document = only(
            storable(load_raw("10-smb-more-restrictive")), {"server", "smb_share", "smb_ace"}
        )
        await replay(client, with_run_id(document))

        acl = (await client.get(f"/api/v1/shares/{FINANCE}/acl")).json()

        assert acl["entries"][0]["trustee"]["resolved"] is False
        assert acl["entries"][0]["trustee"]["sid"] == TRUSTEE
        assert acl["entries"][0]["trustee"]["display_name"] is None

    async def test_a_later_run_that_describes_the_sid_resolves_the_same_entry(
        self, client: AsyncClient
    ) -> None:
        # Proof that resolution is a join and not a stored flag: nothing about the ACE row
        # changed, yet the answer did.
        document = only(
            storable(load_raw("10-smb-more-restrictive")), {"server", "smb_share", "smb_ace"}
        )
        await replay(client, with_run_id(document))

        described = only(storable(load_raw("10-smb-more-restrictive")), {"principal"})
        principal = observation(described, "principal")
        principal["sid"] = TRUSTEE
        principal["principal_kind"] = "well_known"
        principal["display_name"] = "Authenticated Users"
        principal["source_key"] = f"principal|{TRUSTEE}"
        principal.pop("enabled", None)
        await replay(client, with_run_id(described))

        acl = (await client.get(f"/api/v1/shares/{FINANCE}/acl")).json()

        assert acl["entries"][0]["trustee"]["resolved"] is True
        assert acl["entries"][0]["trustee"]["display_name"] == "Authenticated Users"

    async def test_a_builtin_trustee_is_kept_apart_per_server(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        for host in ("FS01", "FS02"):
            document = only(
                storable(load_raw("10-smb-more-restrictive")), {"server", "smb_share", "smb_ace"}
            )
            for batch in document["batches"]:
                for item in batch["observations"]:
                    if item["kind"] == "server":
                        item["name"] = host
                        item["source_key"] = f"server|{host.casefold()}"
                    else:
                        item["server_name"] = host
                        item["source_key"] = item["source_key"].replace("fs01", host.casefold())
                    if item["kind"] == "smb_ace":
                        item["trustee_sid"] = "S-1-5-32-544"
                        item["source_key"] = (
                            f"smb_ace|{host.casefold()}|finance|S-1-5-32-544|allow|read"
                        )
            await replay(client, with_run_id(document))

        keys = (
            (
                await session.execute(
                    sa.select(principal_references.c.principal_key).order_by(
                        principal_references.c.principal_key
                    )
                )
            )
            .scalars()
            .all()
        )
        assert list(keys) == ["fs01|S-1-5-32-544", "fs02|S-1-5-32-544"]


class TestPartialAndFailedScans:
    async def test_a_partial_run_stores_its_observations_and_reconciles_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = storable_document("12-partial-run-no-reconciliation")
        result = await replay(client, document)

        assert result["completion"]["status"] == "partial"
        assert result["completion"]["reconciled_scopes"] == 0
        assert (await counts(session))["shares"] == 1

    async def test_a_scan_that_never_mentions_a_share_leaves_it_alone(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The single most important property of this phase. A server rebooted mid-scan
        # produces fewer observations, not evidence that its shares were removed.
        await replay(client, storable_document("10-smb-more-restrictive"))
        before = await share_row(session)

        silent = only(storable(load_raw("10-smb-more-restrictive")), {"server", "principal"})
        await replay(client, at(with_run_id(silent), "2026-10-01"))

        after = await share_row(session)
        assert after is not None
        assert after["last_observed_run_id"] == before["last_observed_run_id"]
        assert (await counts(session))["aces"] == 1

    async def test_a_failed_run_removes_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("10-smb-more-restrictive"))
        before = await counts(session)

        failed = storable_document("10-smb-more-restrictive")
        run_id = str(uuid.uuid4())
        failed["start"]["run_id"] = run_id
        await client.post("/api/v1/scan-runs", json=failed["start"])
        completion = {
            **failed["completion"],
            "run_id": run_id,
            "status": "failed",
            "batch_count": 0,
            "observation_count": 0,
            "error_count": 1,
            "errors": [
                {
                    "code": "unreachable",
                    "message": "The server did not answer on port 445.",
                    "target": "FS01",
                }
            ],
            "reconciled_scopes": [],
        }
        response = await client.post(f"/api/v1/scan-runs/{run_id}/completion", json=completion)

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert await counts(session) == before

    async def test_a_failed_run_may_not_reconcile(self, client: AsyncClient) -> None:
        document = storable_document("10-smb-more-restrictive")
        run_id = document["start"]["run_id"]
        await client.post("/api/v1/scan-runs", json=document["start"])

        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion",
            json={
                **document["completion"],
                "status": "failed",
                "error_count": 1,
                "errors": [{"code": "unreachable", "message": "no answer"}],
            },
        )

        assert response.status_code == 422
        assert "must not reconcile" in response.text


class TestOutOfOrderBatches:
    async def test_an_acl_can_arrive_before_its_share(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # No foreign key, on purpose: refusing this would discard a real ACL read.
        document = only(storable(load_raw("10-smb-more-restrictive")), {"smb_ace"})
        await replay(client, with_run_id(document))

        assert (await counts(session))["aces"] == 1
        assert await share_row(session) is None

    async def test_the_orphaned_acl_is_reported_with_a_null_share(
        self, client: AsyncClient
    ) -> None:
        document = only(storable(load_raw("10-smb-more-restrictive")), {"smb_ace"})
        await replay(client, with_run_id(document))

        acl = (await client.get(f"/api/v1/shares/{FINANCE}/acl")).json()

        assert acl["share"] is None
        assert len(acl["entries"]) == 1

    async def test_a_share_can_arrive_before_its_server(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = only(storable(load_raw("10-smb-more-restrictive")), {"smb_share"})
        await replay(client, with_run_id(document))

        stored = await counts(session)
        assert stored["shares"] == 1
        assert stored["servers"] == 0

    async def test_the_later_server_observation_joins_up(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client, with_run_id(only(storable(load_raw("10-smb-more-restrictive")), {"smb_share"}))
        )
        await replay(
            client, with_run_id(only(storable(load_raw("10-smb-more-restrictive")), {"server"}))
        )

        response = await client.get("/api/v1/servers/FS01/shares")

        assert response.status_code == 200
        body = response.json()
        assert body["server"]["key"] == "fs01"
        assert [item["key"] for item in body["items"]] == [FINANCE]
