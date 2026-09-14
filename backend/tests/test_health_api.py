"""Health and version endpoint behavior."""

from __future__ import annotations

from fastapi.testclient import TestClient
from httpx import AsyncClient

from app import __version__
from app.config import Settings
from app.db import Database
from app.main import create_app


async def test_liveness_reports_alive_without_touching_dependencies(
    healthy_client: AsyncClient,
) -> None:
    response = await healthy_client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive", "version": __version__}


async def test_liveness_succeeds_even_when_the_database_is_down(
    unhealthy_client: AsyncClient,
) -> None:
    response = await unhealthy_client.get("/health/live")

    assert response.status_code == 200


async def test_readiness_reports_ready_when_postgresql_answers(
    healthy_client: AsyncClient,
) -> None:
    response = await healthy_client.get("/health/ready")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ready"
    assert body["dependencies"] == [
        {"name": "postgresql", "ok": True, "latency_ms": 1.5, "error": None}
    ]


async def test_readiness_returns_503_with_an_actionable_diagnostic(
    unhealthy_client: AsyncClient,
) -> None:
    response = await unhealthy_client.get("/health/ready")
    body = response.json()

    assert response.status_code == 503
    assert body["status"] == "not_ready"
    dependency = body["dependencies"][0]
    assert dependency["ok"] is False
    assert "connection to server" in dependency["error"]


async def test_version_endpoint_reports_name_version_and_environment(
    healthy_client: AsyncClient,
) -> None:
    response = await healthy_client.get("/version")

    assert response.status_code == 200
    assert response.json() == {
        "name": "ADG",
        "version": __version__,
        "environment": "test",
    }


async def test_every_response_carries_a_request_id(healthy_client: AsyncClient) -> None:
    response = await healthy_client.get("/health/live")

    assert response.headers["X-Request-ID"]


async def test_supplied_request_id_is_echoed_back(healthy_client: AsyncClient) -> None:
    response = await healthy_client.get("/health/live", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"


def test_lifespan_builds_and_disposes_the_database(settings: Settings) -> None:
    app = create_app(settings)

    with TestClient(app) as client:
        # The engine is lazy, so no connection is attempted during startup.
        assert isinstance(app.state.database, Database)
        assert client.get("/health/live").status_code == 200
