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

    async def test_one_collector_running_on_two_hosts_is_still_one_collector(
        self, client: AsyncClient
    ) -> None:
        """Coverage is per collector kind. Two file servers scanned by the same collector
        are two runs, and the newest one is the state of that collector."""
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
