"""Process/event-loop setup.

psycopg's async driver cannot run on Windows' ``ProactorEventLoop``; it raises
``InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode``.
uvicorn selects ``ProactorEventLoop`` by default on Windows (``uvicorn.loops.asyncio``),
so an API started natively on a developer workstation cannot reach PostgreSQL unless the
loop is chosen explicitly. Linux containers are unaffected.

Two entry points are provided because the choice has to be made in two different ways:

* :func:`event_loop_factory` is passed to uvicorn (``--loop app.runtime:event_loop_factory``
  or via ``python -m app``), because modern uvicorn builds its loop from a factory and
  ignores the asyncio policy.
* :func:`install_selector_event_loop_policy` is for hosts that create their own loop —
  pytest-asyncio, scripts, notebooks — where only the policy is consulted.
"""

from __future__ import annotations

import asyncio
import sys

WINDOWS = sys.platform == "win32"

PROACTOR_HINT = (
    "On Windows, start the API with `python -m app` or add "
    "`--loop app.runtime:event_loop_factory` to the uvicorn command: psycopg's async "
    "driver cannot use the default ProactorEventLoop."
)


def event_loop_factory() -> asyncio.AbstractEventLoop:
    """Return an event loop the PostgreSQL driver can use.

    uvicorn calls this with no arguments when it is named as ``--loop``.
    """
    if WINDOWS:
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def install_selector_event_loop_policy() -> None:
    """Make new event loops selector-based on Windows. No-op elsewhere.

    Safe to call more than once, and safe to call before a loop exists; it must be called
    before the loop that will talk to PostgreSQL is created.
    """
    if WINDOWS:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
