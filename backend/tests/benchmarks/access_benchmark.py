"""How long the three access questions take, and how that moves with the estate.

`tests/db/test_access_performance.py` asserts the shape of the cost — that the statement
count does not follow the estate — because a statement count is the same number on every
machine and a millisecond is not. This measures the number an operator actually waits for,
which the assertion deliberately refuses to be about.

Nothing here is asserted. The output is recorded in
`docs/architecture/effective-access-performance.md` together with the machine that produced
it, and `test_benchmark_harness.py` checks only that the harness still runs — a benchmark
that quietly stopped running is worse than none, because the last numbers still look current.

Run it::

    python -m tests.benchmarks.access_benchmark
    python -m tests.benchmarks.access_benchmark --sizes 10 100 --repeat 10 --json

It builds its estates through the ingestion API into the smoke test database, so it needs
PostgreSQL reachable and the same configuration `scripts/backend-test.ps1 -Smoke` uses.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import platform
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.config import build_settings
from app.db import Database
from app.main import create_app
from app.models.schema import metadata
from app.runtime import install_selector_event_loop_policy
from tests.support.access_estate import SHARE_UNC, SUBJECT, load

DEFAULT_SIZES = (10, 100)
DEFAULT_REPEAT = 8


def encoded(path: str) -> str:
    return quote(path, safe="")


SHAPES: dict[str, str] = {
    "one_principal_one_resource": (
        f"/api/v1/access/principals/{SUBJECT}/resources/{encoded(SHARE_UNC)}"
    ),
    "every_principal_on_one_share": (
        f"/api/v1/access/resources/{encoded(SHARE_UNC)}/principals?limit=500"
    ),
    "every_share_for_one_principal": f"/api/v1/access/principals/{SUBJECT}/shares?limit=500",
}
"""The three questions the phase names, spelled as the URLs a client issues."""


@dataclass
class Measurement:
    """One shape at one estate size."""

    shape: str
    size: int
    rows: int
    samples: list[float] = field(default_factory=list)

    @property
    def median_ms(self) -> float:
        return round(statistics.median(self.samples) * 1000, 2)

    @property
    def p95_ms(self) -> float:
        ordered = sorted(self.samples)
        index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        return round(ordered[index] * 1000, 2)

    @property
    def min_ms(self) -> float:
        return round(min(self.samples) * 1000, 2)

    def as_row(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "size": self.size,
            "rows": self.rows,
            "min_ms": self.min_ms,
            "median_ms": self.median_ms,
            "p95_ms": self.p95_ms,
        }


def machine_facts() -> dict[str, Any]:
    """Enough about the host that a number can be compared with a later one."""
    return {
        "measured_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
    }


def bench_database_url() -> str:
    """The smoke test database, which these runs truncate.

    The same database `tests/db` uses, and never the development one: the estates here are
    rebuilt from empty on every run and anything else living there would be destroyed.
    """
    base = build_settings().database_url
    head, _, name = base.rpartition("/")
    return f"{head}/{name.removesuffix('_test')}_test"


async def truncate(database: Database) -> None:
    tables = ", ".join(table.name for table in reversed(metadata.sorted_tables))
    async with database.engine.begin() as connection:
        await connection.execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


async def measure(sizes: Sequence[int], repeat: int) -> list[Measurement]:
    settings = build_settings(database_url=bench_database_url())
    database = Database(settings)
    application = create_app(settings)
    # Set directly rather than through the lifespan, which is the seam tests/db uses too.
    application.state.database = database

    results: list[Measurement] = []
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://benchmark") as client:
        for size in sizes:
            await truncate(database)
            await load(client, size)
            for shape, url in SHAPES.items():
                # One warm-up request, discarded: the first call through a shape pays for
                # connection setup and statement preparation, which is a real cost but not
                # the one a steady-state estate pays per request.
                first = await client.get(url)
                first.raise_for_status()
                body = first.json()
                rows = len(body["items"]) if "items" in body else 1

                samples: list[float] = []
                for _ in range(repeat):
                    started = time.perf_counter()
                    response = await client.get(url)
                    samples.append(time.perf_counter() - started)
                    response.raise_for_status()
                results.append(Measurement(shape=shape, size=size, rows=rows, samples=samples))
                print(f"  {shape:<32} size={size:<5} {results[-1].median_ms:>8.2f} ms median")
    await database.dispose()
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Time the three effective-access questions against PostgreSQL."
    )
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_SIZES),
        help="Estate sizes to build and measure.",
    )
    parser.add_argument(
        "--repeat", type=int, default=DEFAULT_REPEAT, help="Timed requests per shape."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    return parser


def render(results: list[Measurement], facts: dict[str, Any]) -> str:
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
