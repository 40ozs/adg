"""The authorization boundary against a real database.

`tests/api/test_authorization.py` proves the boundary structurally, with a stub database.
This file proves the part that only a real one can: that the boundary is what stands between
an anonymous caller and actual estate data, and that a collector key can write observations
and then cannot read a single one of them back.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.roles import Role
from app.config import Settings, build_settings
from app.db import Database
from app.main import create_app

COLLECTOR_KEY = "a-collector-key-long-enough-to-be-accepted"

RUN = {
    "schema_version": "1.0",
    "source": {
        "collector": "smb",
        "collector_host": "COLLECTOR01",
        "method": "test",
        "collector_version": "0.1.0",
    },
    "started_at": "2026-09-14T08:00:00Z",
    "scopes": [{"kind": "server", "key": "fs01"}],
    "incremental": False,
}


@pytest.fixture
def keyed_settings(migrated_database: str) -> Settings:
    return build_settings(
        environment="test",
        log_format="text",
        database_url=migrated_database,
        collector_api_keys=f"fs01:{COLLECTOR_KEY}",
    )


@pytest.fixture
async def collector_client(keyed_settings: Settings) -> Any:
    """A client presenting a collector key and no bearer token at all."""
    database = Database(keyed_settings)
    app = create_app(keyed_settings)
    app.state.database = database
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-ADG-Collector-Key": COLLECTOR_KEY},
        ) as client:
            yield client
    finally:
        await database.dispose()


class TestAnAnonymousCaller:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/servers",
            "/api/v1/search?q=finance",
            "/api/v1/scan-runs",
            "/api/v1/collection/status",
            "/api/v1/principals/S-1-5-32-544",
        ],
    )
    async def test_reaches_no_estate_data(self, anonymous_client: AsyncClient, path: str) -> None:
        response = await anonymous_client.get(path)

        assert response.status_code == 401

    async def test_cannot_write_observations(self, anonymous_client: AsyncClient) -> None:
        response = await anonymous_client.post(
            "/api/v1/scan-runs", json={**RUN, "run_id": str(uuid.uuid4())}
        )

        assert response.status_code == 401

    async def test_can_still_read_the_health_probes(self, anonymous_client: AsyncClient) -> None:
        """Orchestrators hold no token, and these disclose only reachability and a version."""
        assert (await anonymous_client.get("/health/live")).status_code == 200
        assert (await anonymous_client.get("/health/ready")).status_code == 200
        assert (await anonymous_client.get("/version")).status_code == 200


class TestACollectorKey:
    async def test_may_open_a_run(self, collector_client: AsyncClient) -> None:
        response = await collector_client.post(
            "/api/v1/scan-runs", json={**RUN, "run_id": str(uuid.uuid4())}
        )

        assert response.status_code == 201

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/servers",
            "/api/v1/search?q=finance",
            "/api/v1/scan-runs",
            "/api/v1/collection/status",
        ],
    )
    async def test_may_not_read_anything_back(
        self, collector_client: AsyncClient, path: str
    ) -> None:
        """The key lives in a scheduled task's configuration on a file server. If stealing it
        also handed over the map of the estate, the key would be the finding."""
        response = await collector_client.get(path)

        assert response.status_code == 401

    async def test_cannot_inspect_the_run_it_just_created(
        self, collector_client: AsyncClient
    ) -> None:
        run_id = str(uuid.uuid4())
        created = await collector_client.post("/api/v1/scan-runs", json={**RUN, "run_id": run_id})
        assert created.status_code == 201

        assert (await collector_client.get(f"/api/v1/scan-runs/{run_id}")).status_code == 401


class TestRolesAgainstRealData:
    async def test_a_viewer_reads_the_estate(self, client_as: Any) -> None:
        async with client_as(Role.VIEWER) as viewer:
            assert (await viewer.get("/api/v1/servers")).status_code == 200
            assert (await viewer.get("/api/v1/collection/status")).status_code == 200

    async def test_a_viewer_cannot_write_observations(self, client_as: Any) -> None:
        async with client_as(Role.VIEWER) as viewer:
            response = await viewer.post(
                "/api/v1/scan-runs", json={**RUN, "run_id": str(uuid.uuid4())}
            )

        assert response.status_code == 403

    async def test_an_admin_can_write_observations_by_hand(self, client_as: Any) -> None:
        """An operator replaying a payload needs a human identity that can do it."""
        async with client_as(Role.ADMIN) as admin:
            response = await admin.post(
                "/api/v1/scan-runs", json={**RUN, "run_id": str(uuid.uuid4())}
            )

        assert response.status_code == 201

    @pytest.mark.parametrize(
        "path",
        ["/api/v1/servers", "/api/v1/principals/S-1-5-32-544", "/api/v1/collection/status"],
    )
    async def test_an_account_with_no_role_reads_nothing(self, client_as: Any, path: str) -> None:
        async with client_as() as nobody:
            response = await nobody.get(path)

        assert response.status_code == 403
