"""Collection coverage and the run list, against a real PostgreSQL.

The pure rule is tested in ``tests/domain/test_collection_status.py``. What is tested here
is the query feeding it: that "the latest run" really is the latest, per collector, and that
a superseded failure does not keep a healthy estate looking broken forever.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.auth.roles import Role


def transcript(
    *,
    collector: str = "smb",
    started_at: str,
    status: str = "succeeded",
    errors: int = 0,
    host: str = "COLLECTOR01",
    target: str | None = None,
) -> dict[str, Any]:
    """A run that reports nothing, which is all these tests need: coverage is about runs."""
    run_id = str(uuid.uuid4())
    error_list = [
        {
            "code": "access_denied",
            "message": f"Access denied reading target {index}",
            "target": f"\\\\FS01\\Share{index}",
        }
        for index in range(errors)
    ]
    return {
        "start": {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": {
                "collector": collector,
                "collector_host": host,
                "method": "test",
                "collector_version": "0.1.0",
                **({"target": target} if target is not None else {}),
            },
            "started_at": started_at,
            "scopes": [{"kind": "server", "key": "fs01"}],
            "incremental": False,
        },
        "batches": [],
        "completion": {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": status,
            "completed_at": started_at,
            "batch_count": 0,
            "observation_count": 0,
            "error_count": errors,
            "errors": error_list,
            "reconciled_scopes": [],
        },
    }


async def record(client: AsyncClient, document: dict[str, Any]) -> str:
    run_id = document["start"]["run_id"]
    started = await client.post("/api/v1/scan-runs", json=document["start"])
    assert started.status_code in (200, 201), started.text
    completed = await client.post(
        f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
    )
    assert completed.status_code == 200, completed.text
    return str(run_id)


class TestCollectionStatus:
    async def test_an_untouched_database_reports_no_data(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "no_data"
        assert body["collectors"] == []
        assert "nothing has been collected" in body["summary"]

    async def test_clean_runs_report_healthy(self, client: AsyncClient) -> None:
        await record(client, transcript(collector="smb", started_at="2026-09-14T08:00:00Z"))
        await record(
            client, transcript(collector="active_directory", started_at="2026-09-14T08:05:00Z")
        )

        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "healthy"
        assert sorted(item["collector"] for item in body["collectors"]) == [
            "active_directory",
            "smb",
        ]

    async def test_a_failed_run_makes_the_picture_failed(self, client: AsyncClient) -> None:
        await record(client, transcript(collector="smb", started_at="2026-09-14T08:00:00Z"))
        await record(
            client,
            transcript(collector="ntfs", started_at="2026-09-14T08:10:00Z", status="failed"),
        )

        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "failed"
        assert any("ntfs" in concern for concern in body["concerns"])

    async def test_a_later_success_supersedes_an_earlier_failure(self, client: AsyncClient) -> None:
        """Otherwise one bad night marks the estate unobserved until somebody deletes rows."""
        await record(
            client,
            transcript(collector="ntfs", started_at="2026-09-14T08:00:00Z", status="failed"),
        )
        await record(client, transcript(collector="ntfs", started_at="2026-09-14T09:00:00Z"))

        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "healthy"
        assert len(body["collectors"]) == 1

    async def test_an_earlier_success_does_not_hide_a_later_failure(
        self, client: AsyncClient
    ) -> None:
        await record(client, transcript(collector="ntfs", started_at="2026-09-14T08:00:00Z"))
        await record(
            client,
            transcript(collector="ntfs", started_at="2026-09-14T09:00:00Z", status="failed"),
        )

        assert (await client.get("/api/v1/collection/status")).json()["health"] == "failed"

    async def test_reported_errors_make_a_success_untrustworthy(self, client: AsyncClient) -> None:
        await record(
            client,
            transcript(
                collector="ntfs", started_at="2026-09-14T08:00:00Z", status="partial", errors=3
            ),
        )

        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "incomplete"
        assert body["collectors"][0]["trustworthy"] is False
        assert body["collectors"][0]["error_count"] == 3

    async def test_one_collector_on_two_hosts_with_no_target_is_still_one_collector(
        self, client: AsyncClient
    ) -> None:
        """Coverage is per *scope*: ``(collector, target)``. Two runs of one collector that
        declare no target are one scope, and the newest of them is its state — which is what
        this asserted before the unit changed, and still asserts."""
        await record(
            client,
            transcript(collector="ntfs", started_at="2026-09-14T08:00:00Z", host="COLLECTOR01"),
        )
        await record(
            client,
            transcript(collector="ntfs", started_at="2026-09-14T08:30:00Z", host="COLLECTOR02"),
        )

        body = (await client.get("/api/v1/collection/status")).json()

        assert len(body["collectors"]) == 1
        assert body["collectors"][0]["collector"] == "ntfs"

    async def test_a_failure_on_one_server_is_not_hidden_by_a_success_on_another(
        self, client: AsyncClient
    ) -> None:
        """The defect Phase 6D found, measured against a multi-server estate.

        Coverage used to be reduced to the newest run of each collector *kind*. That is
        correct only while each kind runs against one target — and the NTFS collector runs
        against every file server. Under the old rule a newer success on FS01 superseded the
        failure on FS03, the banner read ``healthy``, and every empty list in the product
        became readable as "nothing is there" while a whole server was unobserved. That is
        the exact misreading ``app/domain/collection.py`` exists to prevent.
        """
        await record(
            client,
            transcript(
                collector="ntfs",
                started_at="2026-09-14T08:00:00Z",
                status="failed",
                errors=1,
                host="FS03",
                target="FS03",
            ),
        )
        await record(
            client,
            transcript(
                collector="ntfs",
                started_at="2026-09-14T09:00:00Z",
                host="FS01",
                target="FS01",
            ),
        )

        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "failed"
        assert any("FS03" in concern for concern in body["concerns"])
        scopes = {(item["collector"], item["target"]): item for item in body["collectors"]}
        assert set(scopes) == {("ntfs", "FS01"), ("ntfs", "FS03")}
        assert scopes[("ntfs", "FS01")]["status"] == "succeeded"
        assert scopes[("ntfs", "FS03")]["status"] == "failed"

    async def test_a_concern_names_the_target_it_is_about(self, client: AsyncClient) -> None:
        """Two rows of one collector kind are only distinguishable by target. A concern that
        does not say which server was not read sends an operator to look at all of them."""
        await record(
            client,
            transcript(
                collector="ntfs",
                started_at="2026-09-14T08:00:00Z",
                status="failed",
                errors=1,
                host="FS03",
                target="FS03",
            ),
        )

        body = (await client.get("/api/v1/collection/status")).json()

        assert body["concerns"] == [
            "The most recent ntfs (FS03) run failed; its scope is unobserved."
        ]

    async def test_the_healthy_summary_names_each_collector_once(self, client: AsyncClient) -> None:
        """One kind scanning four servers is four coverage rows and one name. A banner
        reading "ntfs, ntfs, ntfs, ntfs" says nothing the first one did not."""
        for index, host in enumerate(["FS01", "FS02", "FS03"]):
            await record(
                client,
                transcript(
                    collector="ntfs",
                    started_at=f"2026-09-14T0{index + 1}:00:00Z",
                    host=host,
                    target=host,
                ),
            )

        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "healthy"
        assert body["summary"].count("ntfs") == 1
        assert len(body["collectors"]) == 3

    async def test_a_later_success_on_the_same_target_does_supersede_the_failure(
        self, client: AsyncClient
    ) -> None:
        """The other half of the rule. Per-scope must not mean a failure is remembered
        forever: re-scanning the server that failed clears it."""
        await record(
            client,
            transcript(
                collector="ntfs",
                started_at="2026-09-14T08:00:00Z",
                status="failed",
                errors=1,
                host="FS03",
                target="FS03",
            ),
        )
        await record(
            client,
            transcript(
                collector="ntfs",
                started_at="2026-09-14T10:00:00Z",
                host="FS03",
                target="FS03",
            ),
        )

        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "healthy"
        assert len(body["collectors"]) == 1

    async def test_a_viewer_may_read_it(self, client_as: Any, client: AsyncClient) -> None:
        """The whole point of granting viewers collectors:read."""
        await record(client, transcript(started_at="2026-09-14T08:00:00Z"))

        async with client_as(Role.VIEWER) as viewer:
            response = await viewer.get("/api/v1/collection/status")

        assert response.status_code == 200
        assert response.json()["health"] == "healthy"


class TestTheRunList:
    async def test_an_empty_database_lists_nothing_with_a_total_of_zero(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/scan-runs")).json()

        assert body["items"] == []
        assert body["page"]["total"] == 0
        assert body["page"]["has_more"] is False

    async def test_runs_come_back_newest_first(self, client: AsyncClient) -> None:
        for minute in ("00", "10", "20"):
            await record(client, transcript(started_at=f"2026-09-14T08:{minute}:00Z"))

        body = (await client.get("/api/v1/scan-runs")).json()

        assert [item["started_at"] for item in body["items"]] == [
            "2026-09-14T08:20:00Z",
            "2026-09-14T08:10:00Z",
            "2026-09-14T08:00:00Z",
        ]

    async def test_it_can_be_filtered_by_collector(self, client: AsyncClient) -> None:
        await record(client, transcript(collector="smb", started_at="2026-09-14T08:00:00Z"))
        await record(client, transcript(collector="ntfs", started_at="2026-09-14T08:10:00Z"))

        body = (await client.get("/api/v1/scan-runs", params={"collector": "ntfs"})).json()

        assert [item["collector"] for item in body["items"]] == ["ntfs"]
        assert body["page"]["total"] == 1

    async def test_it_can_be_filtered_by_status(self, client: AsyncClient) -> None:
        await record(client, transcript(started_at="2026-09-14T08:00:00Z"))
        await record(
            client,
            transcript(collector="ntfs", started_at="2026-09-14T08:10:00Z", status="failed"),
        )

        body = (await client.get("/api/v1/scan-runs", params={"status": "failed"})).json()

        assert [item["status"] for item in body["items"]] == ["failed"]

    async def test_an_unknown_collector_is_refused_rather_than_ignored(
        self, client: AsyncClient
    ) -> None:
        """A filter that silently matches everything is worse than an error: the caller
        believes they narrowed the list."""
        response = await client.get("/api/v1/scan-runs", params={"collector": "sharepoint"})

        assert response.status_code == 422

    async def test_a_page_reports_how_to_continue(self, client: AsyncClient) -> None:
        for index in range(5):
            await record(client, transcript(started_at=f"2026-09-14T08:{index:02d}:00Z"))

        first = (await client.get("/api/v1/scan-runs", params={"limit": 2})).json()

        assert len(first["items"]) == 2
        assert first["page"]["has_more"] is True
        assert first["page"]["total"] == 5

        second = (
            await client.get(
                "/api/v1/scan-runs",
                params={"limit": 2, "cursor": first["page"]["next_cursor"]},
            )
        ).json()

        assert len(second["items"]) == 2
        first_ids = {item["run_id"] for item in first["items"]}
        assert not (first_ids & {item["run_id"] for item in second["items"]})

    async def test_the_last_page_offers_no_cursor(self, client: AsyncClient) -> None:
        await record(client, transcript(started_at="2026-09-14T08:00:00Z"))

        body = (await client.get("/api/v1/scan-runs")).json()

        assert body["page"]["has_more"] is False
        assert body["page"]["next_cursor"] is None

    async def test_a_foreign_cursor_is_refused(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/scan-runs", params={"cursor": "not-a-cursor"})

        assert response.status_code == 422

    @pytest.mark.parametrize("path", ["/api/v1/scan-runs", "/api/v1/collection/status"])
    async def test_an_account_with_no_role_may_not_read_run_history(
        self, client_as: Any, path: str
    ) -> None:
        async with client_as() as nobody:
            response = await nobody.get(path)

        assert response.status_code == 403
