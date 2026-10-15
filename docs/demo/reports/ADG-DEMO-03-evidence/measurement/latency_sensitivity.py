"""ADG-DEMO-03: what a point-in-time comparison costs, and how it scales with database latency.

Run from ``backend/`` against a reset Null Meridian database, once per code version::

    PYTHONPATH=<checkout>/backend python latency_sensitivity.py <label> <out.jsonl>

It calls ``ChangeService.compare`` directly (no HTTP, no serialization) for the cases below,
counting statements and wall time, first as measured and then with a fixed delay injected
before every statement -- a model of a database across a network, where each round trip
costs the same whatever it carries. The delay is the only thing modelled; everything else is
the real comparison over the real estate.
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
from app.contracts.v1.common import ObservationKind
from app.db import Database

URL = "postgresql+psycopg://adg:adg_ci_password@localhost:5432/adg_demo"
G1 = dt.datetime(2026, 9, 3, 6, 0, tzinfo=dt.UTC)
G3 = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.UTC)
CASES = [
    ("estate-wide, per-side ceiling 5,000", None, 5_000),
    ("estate-wide, ceiling raised to 20,000 (no bound reached)", None, 20_000),
    ("folder permission entries only", frozenset({ObservationKind.NTFS_ACE}), 5_000),
]
DELAYS_MS = [0, 5, 10]


async def main(label: str, out: str) -> None:
    db = Database(build_settings(environment="test", log_format="text", database_url=URL))
    state = {"n": 0, "delay": 0.0}
    engine = db.engine.sync_engine

    @sa.event.listens_for(engine, "before_cursor_execute")
    def before(*_args: object) -> None:
        state["n"] += 1
        if state["delay"]:
            time.sleep(state["delay"])

    rows = []
    for name, kinds, ceiling in CASES:
        service.COMPARISON_CEILING = ceiling
        for delay in DELAYS_MS:
            state["delay"] = delay / 1000
            samples = []
            for _ in range(3):
                state["n"] = 0
                async with db.session() as session:
                    started = time.perf_counter()
                    result = await ChangeService(session).compare(G1, G3, kinds=kinds)
                    samples.append((time.perf_counter() - started) * 1000)
            record = {
                "code": label,
                "case": name,
                "injected_ms_per_statement": delay,
                "statements": state["n"],
                "median_ms": round(sorted(samples)[1], 1),
                "changes": len(result.changes),
                "truncated": result.truncated,
                "outcome": getattr(getattr(result, "outcome", None), "value", "n/a (untyped)"),
            }
            rows.append(record)
            print(json.dumps(record), flush=True)
    await db.dispose()
    with open(out, "a") as handle:
        for record in rows:
            handle.write(json.dumps(record) + "\n")


asyncio.run(main(sys.argv[1], sys.argv[2]))
