"""`python -m app.origin_gate`: the API behind a key, for a host with a public address."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import origin_gate
from app.__main__ import LOOP_FACTORY
from app.origin_gate import OriginGate

KEY = "k" * 40


def _client() -> TestClient:
    inner = FastAPI()

    @inner.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @inner.get("/runtime")
    def runtime() -> dict[str, str]:
        return {"runtime_mode": "demo"}

    return TestClient(OriginGate(inner, KEY))


@pytest.mark.parametrize(
    "headers", [{}, {"X-ADG-Origin-Key": "wrong"}, {"X-ADG-Origin-Key": KEY[:-1]}]
)
def test_a_request_without_the_key_is_refused(headers: dict[str, str]) -> None:
    response = _client().get("/runtime", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "This API answers only its staging proxy."}


def test_a_request_with_the_key_reaches_the_api() -> None:
    response = _client().get("/runtime", headers={"X-ADG-Origin-Key": KEY})

    assert response.status_code == 200
    assert response.json() == {"runtime_mode": "demo"}


def test_liveness_is_open_for_the_hosts_health_check_and_nothing_else_is() -> None:
    client = _client()

    assert client.get("/health/live").status_code == 200
    assert client.get("/health/live/").status_code == 403
    assert client.get("/health/ready").status_code == 403


def test_a_short_key_is_refused() -> None:
    with pytest.raises(ValueError, match="ADG_ORIGIN_KEY"):
        OriginGate(FastAPI(), "short")


def test_without_a_key_the_server_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADG_ORIGIN_KEY", raising=False)
    monkeypatch.setattr("app.origin_gate.uvicorn.run", lambda *a, **k: pytest.fail("started"))

    with pytest.raises(SystemExit, match="ADG_ORIGIN_KEY"):
        origin_gate.main([])


def test_the_gated_api_is_served_like_python_m_app(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setenv("ADG_ORIGIN_KEY", KEY)
    monkeypatch.setattr("app.origin_gate.uvicorn.run", fake_run)

    origin_gate.main(["--host", "0.0.0.0", "--port", "8000"])

    assert isinstance(captured["app"], OriginGate)
    assert (captured["host"], captured["port"]) == ("0.0.0.0", 8000)
    assert captured["loop"] == LOOP_FACTORY
