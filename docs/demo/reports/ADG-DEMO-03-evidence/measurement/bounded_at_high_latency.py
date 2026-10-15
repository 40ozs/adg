"""ADG-DEMO-03: is the comparison bounded when the database is slow?

The full estate-wide comparison (ceiling raised so no object bound is reached), with 25 ms
injected before every statement. Run once per code version, as ``latency_sensitivity.py``.
"""

import asyncio
import datetime as dt
import json
import sys
import time

import sqlalchemy as sa

import app.changes.service as service
from app.changes.service import ChangeService
from app.config import build_settings
from app.db import Database

URL = "postgresql+psycopg://adg:adg_ci_password@localhost:5432/adg_demo"
G1 = dt.datetime(2026, 9, 3, 6, 0, tzinfo=dt.UTC)
G3 = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.UTC)


async def main(label: str, out: str) -> None:
    db = Database(build_settings(environment="test", log_format="text", database_url=URL))
    count = {"n": 0}

    @sa.event.listens_for(db.engine.sync_engine, "before_cursor_execute")
    def before(*_args: object) -> None:
        count["n"] += 1
        time.sleep(0.025)

    service.COMPARISON_CEILING = 20_000
    async with db.session() as session:
        started = time.perf_counter()
        result = await ChangeService(session).compare(G1, G3)
        elapsed = (time.perf_counter() - started) * 1000
    record = {
        "code": label,
        "case": "estate-wide, ceiling 20,000, 25 ms injected per statement",
        "statements": count["n"],
        "wall_ms": round(elapsed),
        "changes_presented": len(result.changes),
        "outcome": getattr(getattr(result, "outcome", None), "value", "n/a (untyped)"),
        "stopped_in": getattr(getattr(result, "stopped_in", None), "value", None),
        "web_tier_timeout_ms": 10_000,
    }
    print(json.dumps(record))
    with open(out, "a") as handle:
        handle.write(json.dumps(record) + "\n")
    await db.dispose()


asyncio.run(main(sys.argv[1], sys.argv[2]))
