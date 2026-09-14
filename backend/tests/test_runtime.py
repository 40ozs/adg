"""Event-loop selection.

These behaviors exist because psycopg's async driver refuses to run on Windows'
ProactorEventLoop; getting this wrong makes the API unable to reach PostgreSQL.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.runtime import event_loop_factory, install_selector_event_loop_policy


def test_factory_returns_a_usable_loop() -> None:
    loop = event_loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
        assert loop.run_until_complete(asyncio.sleep(0)) is None
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific loop selection")
def test_factory_avoids_the_proactor_loop_on_windows() -> None:
    loop = event_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
        assert not isinstance(loop, asyncio.ProactorEventLoop)
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific loop selection")
def test_policy_installation_produces_selector_loops_on_windows() -> None:
    install_selector_event_loop_policy()

    loop = asyncio.new_event_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_policy_installation_is_idempotent() -> None:
    install_selector_event_loop_policy()
    install_selector_event_loop_policy()

    loop = asyncio.new_event_loop()
    loop.close()
