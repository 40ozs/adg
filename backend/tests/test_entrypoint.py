"""`python -m app` startup behavior."""

from __future__ import annotations

from typing import Any

import pytest

from app.__main__ import LOOP_FACTORY, main, parse_args


def test_defaults_bind_to_localhost_without_reload() -> None:
    args = parse_args([])

    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.reload is False


def test_arguments_are_parsed() -> None:
    args = parse_args(["--host", "0.0.0.0", "--port", "9001", "--reload"])

    assert (args.host, args.port, args.reload) == ("0.0.0.0", 9001, True)


def test_server_is_started_with_the_postgresql_safe_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("app.__main__.uvicorn.run", fake_run)

    main(["--port", "8123"])

    assert captured["app"] == "app.main:app"
    assert captured["port"] == 8123
    # Without this, psycopg cannot connect when the API runs natively on Windows.
    assert captured["loop"] == LOOP_FACTORY
