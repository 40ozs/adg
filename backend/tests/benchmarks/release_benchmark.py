"""What risk evaluation, a history diff and a what-if cost, measured.

The three questions phases 7 through 9 added, and the three the release audit found had no
measured basis. `access_benchmark.py` covers the effective-access questions and
`graph_benchmark.py` the membership graph; those two were measured when they shipped. These
were not, and Phase 8A's handoff says so in as many words -- "the engine has been run against
MVP-sized data only".

Nothing here is asserted, for the reason `access_benchmark.py` gives: a millisecond threshold
passes on a fast machine and fails on a busy one until nobody trusts the suite. The output is
recorded in `docs/release/performance-baseline.md` with the machine that produced it, and
`test_benchmark_harness.py` checks only that the harness still runs.

Five shapes, and each is here because it is one that could follow the estate:

* **`risk_evaluate_estate`** loads every fact in the estate and runs every enabled rule. It is
  the pass an operator schedules, and the one whose cost nobody had measured.
* **`risk_evaluate_run`** loads only what one scan run moved. Against an estate that did not
  change it should be nearly free however large the estate is -- that is the whole claim
  incremental evaluation makes, and it is the claim worth checking.
* **`changes_feed`** pages the change feed, and **`changes_compare`** diffs two instants over
  the history tables. A diff that walked every version rather than the two bracketing the
  window would follow the estate.
* **`simulation_preview`** removes one member from the group that every share ACL names, so
  the proposal's affected scope is the whole estate. It is the most expensive request the API
  serves, and the one the bounds exist to cap.

Run it::

    python -m tests.benchmarks.release_benchmark
    python -m tests.benchmarks.release_benchmark --sizes 10 100 400 --repeat 8 --json

It builds its estates through the ingestion API into the smoke test database, so it needs
PostgreSQL reachable and the same configuration `scripts/backend-test.ps1 -Smoke` uses.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.config import build_settings
from app.db import Database
from app.main import create_app
from app.repositories.risk import RiskFactsRepository, RiskFindingRepository
from app.risk_engine import DEFAULT_CONFIGURATION
from app.runtime import install_selector_event_loop_policy
from app.services.risk import RiskService
from tests.benchmarks.access_benchmark import (
    Measurement,
    bench_database_url,
    machine_facts,
    truncate,
)
from tests.support.access_estate import GROUP, SUBJECT, load
from tests.support.auth import auth_headers

DEFAULT_SIZES = (10, 100)
DEFAULT_REPEAT = 8

#: The window every history shape is asked about. The estate generator stamps its
#: observations inside 2026-09-14, so this brackets all of them with room either side.
WINDOW_FROM = "2026-09-13T00:00:00Z"
WINDOW_TO = "2026-09-16T00:00:00Z"


#: Removing the one user from the one group that every share ACL and every directory DACL
#: names. The proposal is one change and its affected scope is the entire estate, which is
#: the combination the bounds exist for.
PROPOSAL: dict[str, Any] = {
    "changes": [
        {
            "kind": "remove_member",
            "group_key": GROUP,
            "member_key": SUBJECT,
            "edge_kind": "directory_group_member",
        }
    ]
}


@dataclass
class Shape:
    """One thing to time, and how to count what it returned.

    A callable rather than a URL, because two of the four shapes are not HTTP requests:
    risk evaluation has no route that *runs* one (it is driven by
    ``python -m app.operations evaluate-risks`` or by a scan completing), so measuring it
    through the API is not possible and pretending otherwise would measure the wrong thing.
    """

    name: str
    run: Callable[[AsyncClient, Database], Awaitable[int]]
    note: str = ""


async def _risk_estate(_client: AsyncClient, database: Database) -> int:
    async with database.session() as session:
        service = RiskService(
            RiskFactsRepository(session), RiskFindingRepository(session), DEFAULT_CONFIGURATION
        )
        evaluation = await service.evaluate_estate(now=dt.datetime.now(dt.UTC))
        await session.commit()
        return len(evaluation.findings)


async def _risk_run(client: AsyncClient, database: Database) -> int:
    """The incremental pass, against the run that built the estate.

    The run id is read back rather than threaded through, so this shape stays honest if the
    estate generator ever sends more than one run.
    """
    listing = await client.get("/api/v1/scan-runs?limit=1")
    listing.raise_for_status()
    items = listing.json()["items"]
    if not items:
        return 0
    run_id = items[0]["run_id"]
    async with database.session() as session:
        service = RiskService(
            RiskFactsRepository(session), RiskFindingRepository(session), DEFAULT_CONFIGURATION
        )
        evaluation = await service.evaluate_run(uuid.UUID(run_id), now=dt.datetime.now(dt.UTC))
        await session.commit()
        return len(evaluation.findings)


async def _changes_compare(client: AsyncClient, _database: Database) -> int:
    response = await client.get(f"/api/v1/changes/compare?from={WINDOW_FROM}&to={WINDOW_TO}")
    response.raise_for_status()
    return len(response.json()["changes"])


async def _changes_feed(client: AsyncClient, _database: Database) -> int:
    response = await client.get(f"/api/v1/changes?from={WINDOW_FROM}&to={WINDOW_TO}&limit=100")
    response.raise_for_status()
    return len(response.json()["changes"])


async def _simulation_preview(client: AsyncClient, _database: Database) -> int:
    """Returns the pairs the engine evaluated, not the rows it returned.

    A what-if's cost follows what it had to resolve, and a proposal can be expensive and
    return nothing -- which is precisely the case a row count would report as free.
    """
    response = await client.post("/api/v1/simulations/preview", json=PROPOSAL)
    response.raise_for_status()
    return int(response.json()["cost"]["pairs_evaluated"])


SHAPES: tuple[Shape, ...] = (
    Shape("risk_evaluate_estate", _risk_estate, "every rule, every fact"),
    Shape("risk_evaluate_run", _risk_run, "only what one run moved"),
    Shape("changes_feed", _changes_feed, "one 100-row page of the change feed"),
    Shape("changes_compare", _changes_compare, "two instants diffed"),
    Shape("simulation_preview", _simulation_preview, "one change, estate-wide scope"),
)


@dataclass
class Sample(Measurement):
    """A :class:`Measurement` that also carries the shape's note."""

    note: str = field(default="")

    def as_row(self) -> dict[str, Any]:
        return {**super().as_row(), "note": self.note}


async def measure(sizes: Sequence[int], repeat: int) -> list[Sample]:
    settings = build_settings(database_url=bench_database_url())
    database = Database(settings)
    application = create_app(settings)
    application.state.database = database

    results: list[Sample] = []
    transport = ASGITransport(app=application)
    # Signed in as an administrator: ingestion needs 'collectors:ingest' to build the estate
    # and every read route needs a credential. See the note in access_benchmark.measure.
    async with AsyncClient(
        transport=transport, base_url="http://benchmark", headers=auth_headers(settings)
    ) as client:
        for size in sizes:
            await truncate(database)
            await load(client, size)
            for shape in SHAPES:
                # One warm-up, discarded, matching access_benchmark: the first call through
                # a shape pays for connection setup and statement preparation.
                rows = await shape.run(client, database)

                samples: list[float] = []
                for _ in range(repeat):
                    started = time.perf_counter()
                    await shape.run(client, database)
                    samples.append(time.perf_counter() - started)
                results.append(
                    Sample(
                        shape=shape.name,
                        size=size,
                        rows=rows,
                        samples=samples,
                        note=shape.note,
                    )
                )
                print(f"  {shape.name:<24} size={size:<5} {results[-1].median_ms:>9.2f} ms median")
    await database.dispose()
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Time risk evaluation, a history diff and a what-if against PostgreSQL."
    )
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    return parser


def render(results: list[Sample], facts: dict[str, Any]) -> str:
    width = max(len(item.shape) for item in results)
    lines = [
        "",
        f"Measured on {facts['platform']} ({facts['processor']}), Python {facts['python']}",
        f"at {facts['measured_at']}.",
        "",
        f"| {'Shape'.ljust(width)} | Size | Rows | Min (ms) | Median (ms) | p95 (ms) |",
        f"| {'-' * width} | ---: | ---: | -------: | ----------: | -------: |",
    ]
    for item in results:
        lines.append(
            f"| {item.shape.ljust(width)} | {item.size:>4} | {item.rows:>4} | "
            f"{item.min_ms:>8.2f} | {item.median_ms:>11.2f} | {item.p95_ms:>8.2f} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    install_selector_event_loop_policy()
    arguments = build_parser().parse_args(argv)
    facts = machine_facts()
    print(f"Measuring {len(SHAPES)} shapes at sizes {arguments.sizes}...")
    results = asyncio.run(measure(arguments.sizes, arguments.repeat))
    if arguments.json:
        print(json.dumps({"machine": facts, "results": [r.as_row() for r in results]}, indent=2))
    else:
        print(render(results, facts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
