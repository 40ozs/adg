"""Smoke test: the API can actually reach PostgreSQL.

This is the only test that requires the local development stack. It is skipped unless
``ADG_RUN_SMOKE_TESTS=1`` is set, so that the default unit run stays hermetic.

Run it with the stack up:

    docker compose up -d db
    $env:ADG_RUN_SMOKE_TESTS = "1"; .\\scripts\\backend-test.ps1 -Smoke
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import DEV_DATABASE_URL, Settings, build_settings
from app.db import Database
from app.main import create_app

# Read at import time: the autouse environment-isolation fixture clears ADG_* variables
# before each test runs.
SMOKE_ENABLED = os.getenv("ADG_RUN_SMOKE_TESTS") == "1"
SMOKE_DATABASE_URL = os.getenv("ADG_DATABASE_URL", DEV_DATABASE_URL)

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not SMOKE_ENABLED,
        reason="Set ADG_RUN_SMOKE_TESTS=1 with the development stack running.",
    ),
]


def smoke_settings() -> Settings:
    return build_settings(
        environment="development",
        log_format="text",
        database_url=SMOKE_DATABASE_URL,
    )


async def test_database_connectivity_probe_succeeds() -> None:
    database = Database(smoke_settings())
    try:
        result = await database.check_connectivity()
    finally:
        await database.dispose()

    assert result.ok, f"PostgreSQL unreachable: {result.error}"
    assert result.latency_ms >= 0


def test_readiness_endpoint_reports_ready_against_the_real_database() -> None:
    app = create_app(smoke_settings())

    with TestClient(app) as client:
        response = client.get("/health/ready")

    body = response.json()
    assert response.status_code == 200, body
    assert body["status"] == "ready"
    assert body["dependencies"][0] == {
        "name": "postgresql",
        "ok": True,
        "latency_ms": body["dependencies"][0]["latency_ms"],
        "error": None,
    }
