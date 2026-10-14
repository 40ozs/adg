"""How long the directory-access summary, tree and exceptions take on a large directory tree.

``tests/db/test_directory_access_api.py`` asserts the *shape* of the cost — the summary's
statement count does not follow the number of directories, and a tree expansion after the
summary reads no resource table. This measures the time a reader waits, and counts the
statements behind each request, against estates built from
:mod:`tests.support.directory_estate`: every directory stored with its full inherited DACL,
with protected folders, Deny entries, widening grants and resumed access spread through
every share.

Nothing here is asserted (``test_directory_access_benchmark_harness.py`` checks only that
it runs). Numbers belong in the handoff with the machine that produced them.

Run it::

    python -m tests.benchmarks.directory_access_benchmark
    python -m tests.benchmarks.directory_access_benchmark --estates 4x250 20x1000 --repeat 5

Estates go into ``<database>_bench`` (never the development or test database), truncated
before each size.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.config import build_settings
from app.db import Database
from app.main import create_app
from app.runtime import install_selector_event_loop_policy
from tests.benchmarks.access_benchmark import (
    Measurement,
    bench_database_url,
    machine_facts,
    truncate,
)
from tests.benchmarks.graph_benchmark import ensure_database
from tests.support.auth import auth_headers
from tests.support.directory_estate import SUBJECT, build_directory_estate
from tests.support.ingest import replay

DEFAULT_ESTATES = ("4x250", "8x1000")
DEFAULT_REPEAT = 5

BASE = f"/api/v1/access/principals/{quote(SUBJECT, safe='')}"


@dataclass
class EstateFacts:
    shares: int
    per_share: int
    directories: int = 0
    observations: int = 0
    ingest_seconds: float = 0.0
    reachable: int = 0
    roots: int = 0
    exceptions: int = 0
    candidates: int = 0
    statements: dict[str, int] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return {
            "estate": f"{self.shares}x{self.per_share}",
            "directories": self.directories,
            "observations": self.observations,
            "ingest_seconds": round(self.ingest_seconds, 1),
            "reachable": self.reachable,
            "roots": self.roots,
            "exceptions": self.exceptions,
            "candidates": self.candidates,
            "statements": self.statements,
        }


class StatementCounter:
    def __init__(self, engine: Any) -> None:
        self._engine = engine.sync_engine
        self.count = 0

    def _record(self, *args: Any) -> None:
        self.count += 1

    def __enter__(self) -> StatementCounter:
        self.count = 0
        sa.event.listen(self._engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        sa.event.remove(self._engine, "before_cursor_execute", self._record)


def parse_estate(text: str) -> tuple[int, int]:
    shares, _, per_share = text.partition("x")
    return int(shares), int(per_share)


async def measure(
    estates: Sequence[str], repeat: int
) -> tuple[list[EstateFacts], list[Measurement]]:
    url = bench_database_url()
    ensure_database(url)
    settings = build_settings(database_url=url)
    database = Database(settings)
    application = create_app(settings)
    application.state.database = database
    counter = StatementCounter(database.engine)

    facts: list[EstateFacts] = []
    results: list[Measurement] = []
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://benchmark",
        headers=auth_headers(settings),
        timeout=600,
    ) as client:
        for text in estates:
            shares, per_share = parse_estate(text)
            fact = EstateFacts(shares=shares, per_share=per_share)
            await truncate(database)
            estate = build_directory_estate(shares=shares, per_share=per_share, seed=29)
            fact.directories = len(estate.directories)
            fact.observations = estate.observation_count
            started = time.perf_counter()
            await replay(client, estate.document)
            fact.ingest_seconds = time.perf_counter() - started
            size = fact.directories

            def cold() -> None:
                # The per-application computation cache, emptied: what the first reader
                # after an ingestion waits for.
                application.state.directory_access_cache = None

            # Warm the connection and statement caches once, then measure.
            cold()
            first = (await client.get(f"{BASE}/directory-access?limit=500")).json()
            fact.reachable = first["totals"]["reachable_directories"]
            fact.roots = first["totals"]["roots"]
            fact.exceptions = first["totals"]["exceptions"]
            fact.candidates = first["totals"]["candidates_evaluated"]
            share_root = estate.shares[0]
            busiest = max(first["roots"], key=lambda root: root["exception_count"])

            shapes: dict[str, tuple[str, bool]] = {
                "summary_cold": (f"{BASE}/directory-access?limit=100", True),
                "summary_warm": (f"{BASE}/directory-access?limit=100", False),
                "tree_top_warm": (f"{BASE}/directory-access/tree", False),
                "tree_expand_warm": (
                    f"{BASE}/directory-access/tree?{urlencode({'parent': share_root})}",
                    False,
                ),
                "exceptions_warm": (
                    f"{BASE}/directory-access/exceptions?"
                    + urlencode({"root": busiest["directory"]["key"], "limit": 500}),
                    False,
                ),
                "flat_listing_page": (f"{BASE}/resources?limit=500", False),
            }
            for shape, (target, is_cold) in shapes.items():
                if is_cold:
                    cold()
                with counter:
                    response = await client.get(target)
                response.raise_for_status()
                fact.statements[shape] = counter.count
                body = response.json()
                rows = len(body.get("roots") or body.get("children") or body.get("items") or [])
                samples: list[float] = []
                for _ in range(repeat):
                    if is_cold:
                        cold()
                    begun = time.perf_counter()
                    timed = await client.get(target)
                    samples.append(time.perf_counter() - begun)
                    timed.raise_for_status()
                results.append(Measurement(shape=shape, size=size, rows=rows, samples=samples))
                print(f"  {text:>8} {shape:<20} {results[-1].median_ms:>9.2f} ms median")
            facts.append(fact)
    await database.dispose()
    return facts, results


def render(facts: list[EstateFacts], results: list[Measurement], machine: dict[str, Any]) -> str:
    lines = [
        "",
        f"Measured on {machine['platform']} ({machine['processor']}), "
        f"Python {machine['python']}, at {machine['measured_at']}.",
        "",
        "| Estate | Directories | Observations | Ingest (s) | Candidates | Reachable | Roots "
        "| Exceptions |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for fact in facts:
        lines.append(
            f"| {fact.shares}x{fact.per_share} | {fact.directories} | {fact.observations} | "
            f"{fact.ingest_seconds:.1f} | {fact.candidates} | {fact.reachable} | {fact.roots} | "
            f"{fact.exceptions} |"
        )
    lines += [
        "",
        "| Shape | Directories | Rows | Statements | Min (ms) | Median (ms) | p95 (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    by_size = {fact.directories: fact for fact in facts}
    for item in results:
        statements = by_size[item.size].statements.get(item.shape, 0)
        lines.append(
            f"| {item.shape} | {item.size} | {item.rows} | {statements} | {item.min_ms:.2f} | "
            f"{item.median_ms:.2f} | {item.p95_ms:.2f} |"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Time directory access on large trees.")
    parser.add_argument(
        "--estates",
        nargs="+",
        default=list(DEFAULT_ESTATES),
        help="Estates as SHARESxDIRECTORIES_PER_SHARE, for example 20x1000.",
    )
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of tables.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    install_selector_event_loop_policy()
    arguments = build_parser().parse_args(argv)
    machine = machine_facts()
    facts, results = asyncio.run(measure(arguments.estates, arguments.repeat))
    if arguments.json:
        print(
            json.dumps(
                {
                    "machine": machine,
                    "estates": [fact.as_row() for fact in facts],
                    "results": [item.as_row() for item in results],
                },
                indent=2,
            )
        )
    else:
        print(render(facts, results, machine))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
