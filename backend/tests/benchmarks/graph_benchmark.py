"""Measure what a membership answer costs, in memory and against PostgreSQL.

Two suites, because they answer different questions.

**In memory** isolates the traversal itself: how it scales with depth and width, how much
memory a shortest-path explanation costs per node, and how many provider round trips a
shape needs. Nothing here is affected by a laptop's disk or a colleague's query running on
the same database, so the shape of the curve is trustworthy even when the absolute numbers
are not.

**Against PostgreSQL** measures the thing an operator actually waits for, and — more
usefully — captures ``EXPLAIN (ANALYZE, BUFFERS)`` for the one query that matters, the
batched adjacency lookup. Whether that query can walk the index in order or has to sort a
wide frontier is the difference between a bounded query and a bounded query that reads
everything first, and it is not visible from a timing alone.

Run it::

    python -m tests.benchmarks.graph_benchmark                     # in-memory only
    python -m tests.benchmarks.graph_benchmark --database          # add the database suite
    python -m tests.benchmarks.graph_benchmark --scale large --json

The database suite uses its own ``<database>_bench`` database, created and migrated on
demand and **truncated** on every run, so it never touches development or test data.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import time
import tracemalloc
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from app.config import Settings, build_settings
from app.db import Database
from app.domain import Direction, GraphEdge, TraversalLimits, expand, find_cycles, find_paths
from app.models.schema import membership_edges, metadata, principals
from app.repositories import MembershipRepository
from app.runtime import install_selector_event_loop_policy
from app.services.graph import GraphService
from tests.support.graph import InMemoryAdjacency, chain, fan_out, ring

BACKEND_ROOT = Path(__file__).resolve().parents[2]

SCALES: dict[str, dict[str, int]] = {
    # groups x members-per-group, plus the depth of one deliberately deep strand.
    "small": {"groups": 200, "members_per_group": 20, "depth": 12, "wide_group": 2_000},
    "medium": {"groups": 2_000, "members_per_group": 25, "depth": 24, "wide_group": 20_000},
    "large": {"groups": 10_000, "members_per_group": 30, "depth": 48, "wide_group": 100_000},
}


@dataclass
class Measurement:
    """One timed operation, with the size of the thing it operated on."""

    suite: str
    name: str
    nodes: int = 0
    edges: int = 0
    provider_calls: int = 0
    seconds: float = 0.0
    peak_kib: float = 0.0
    detail: str = ""

    @property
    def nodes_per_second(self) -> float:
        return self.nodes / self.seconds if self.seconds > 0 else 0.0

    @property
    def bytes_per_node(self) -> float:
        return (self.peak_kib * 1024) / self.nodes if self.nodes else 0.0


@dataclass
class Suite:
    """Everything one run measured, plus what it ran on."""

    scale: str
    measurements: list[Measurement] = field(default_factory=list)
    plans: dict[str, str] = field(default_factory=dict)
    machine: dict[str, str] = field(default_factory=dict)

    def record(self, measurement: Measurement) -> None:
        self.measurements.append(measurement)

    def to_json(self) -> dict[str, Any]:
        return {
            "scale": self.scale,
            "machine": self.machine,
            "measurements": [
                {
                    **asdict(item),
                    "nodes_per_second": round(item.nodes_per_second, 1),
                    "bytes_per_node": round(item.bytes_per_node, 1),
                }
                for item in self.measurements
            ],
            "query_plans": self.plans,
        }

    def rendered(self) -> str:
        width = max((len(item.name) for item in self.measurements), default=10)
        lines = [
            f"ADG membership-graph benchmark — scale={self.scale}",
            f"{self.machine.get('python', '')} on {self.machine.get('platform', '')}",
            "",
            f"{'suite':<10} {'operation':<{width}} {'nodes':>8} {'edges':>9} "
            f"{'calls':>6} {'ms':>9} {'nodes/s':>11} {'B/node':>8}",
            "-" * (10 + width + 8 + 9 + 6 + 9 + 11 + 8 + 8),
        ]
        for item in self.measurements:
            lines.append(
                f"{item.suite:<10} {item.name:<{width}} {item.nodes:>8} {item.edges:>9} "
                f"{item.provider_calls:>6} {item.seconds * 1000:>9.1f} "
                f"{item.nodes_per_second:>11,.0f} {item.bytes_per_node:>8.0f}"
            )
            if item.detail:
                lines.append(f"{'':<10} {item.detail}")
        for name, plan in self.plans.items():
            lines.extend(["", f"query plan — {name}", "-" * 62, plan])
        return "\n".join(lines)


def machine_facts() -> dict[str, str]:
    return {
        "python": f"Python {platform.python_version()}",
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "processors": str(os.cpu_count() or 0),
        "measured_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------- in-memory suite


async def _timed_expansion(
    suite: Suite, name: str, edges: Sequence[GraphEdge], root: str, limits: TraversalLimits
) -> None:
    provider = InMemoryAdjacency(edges)
    tracemalloc.start()
    started = time.perf_counter()
    result = await expand(root, Direction.DOWN, provider, limits)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    suite.record(
        Measurement(
            suite="memory",
            name=name,
            nodes=len(result.nodes),
            edges=len(result.edges),
            provider_calls=provider.calls,
            seconds=elapsed,
            peak_kib=peak / 1024,
            detail="" if result.complete else f"truncated: {[r.value for r in result.truncation]}",
        )
    )


async def run_memory_suite(suite: Suite, scale: dict[str, int]) -> None:
    generous = TraversalLimits(max_depth=128, max_nodes=250_000, max_edges=1_000_000)

    depth = scale["depth"] * 4
    await _timed_expansion(suite, f"chain-{depth}", chain(depth), f"n{depth}", generous)

    width = scale["wide_group"]
    await _timed_expansion(suite, f"fan-out-{width}", fan_out(width), "g0", generous)

    # A layered graph: the shape a real directory has, and the one where the cost of
    # keeping a shortest path per node actually shows up.
    layers, per_layer = scale["depth"], scale["members_per_group"]
    layered: list[GraphEdge] = []
    for layer in range(layers):
        for index in range(per_layer):
            layered.append(
                GraphEdge(
                    edge_key=f"L{layer}->L{layer + 1}_{index}",
                    group_key=f"L{layer}",
                    member_key=f"L{layer + 1}_{index}",
                )
            )
            layered.append(
                GraphEdge(
                    edge_key=f"L{layer + 1}_{index}->L{layer + 1}",
                    group_key=f"L{layer + 1}_{index}",
                    member_key=f"L{layer + 1}",
                )
            )
    await _timed_expansion(suite, f"layered-{layers}x{per_layer}", layered, "L0", generous)

    size = scale["wide_group"] // 10
    await _timed_expansion(suite, f"ring-{size}", ring(size), "r0", generous)

    # Cycle detection on its own: Tarjan over the whole edge set, which is what every
    # expansion pays once at the end.
    started = time.perf_counter()
    cycles = find_cycles(ring(size))
    elapsed = time.perf_counter() - started
    suite.record(
        Measurement(
            suite="memory",
            name=f"find-cycles-{size}",
            nodes=size,
            edges=size,
            seconds=elapsed,
            detail=f"{len(cycles)} component(s)",
        )
    )

    # Path enumeration over stacked diamonds: exponential in the branch count, which is
    # exactly why max_paths exists. Measured at the cap, not past it.
    levels = 15
    diamonds: list[GraphEdge] = []
    for level in range(levels):
        for branch in ("a", "b"):
            diamonds.append(
                GraphEdge(
                    edge_key=f"L{level}{branch}->N{level}",
                    group_key=f"L{level}{branch}",
                    member_key=f"N{level}",
                )
            )
            diamonds.append(
                GraphEdge(
                    edge_key=f"N{level + 1}->L{level}{branch}",
                    group_key=f"N{level + 1}",
                    member_key=f"L{level}{branch}",
                )
            )
    provider = InMemoryAdjacency(diamonds)
    started = time.perf_counter()
    search = await find_paths(
        "N0", f"N{levels}", provider, TraversalLimits(max_depth=64, max_paths=1_000)
    )
    elapsed = time.perf_counter() - started
    suite.record(
        Measurement(
            suite="memory",
            name=f"paths-{levels}-diamonds",
            nodes=len(search.paths),
            edges=len(diamonds),
            provider_calls=provider.calls,
            seconds=elapsed,
            detail=f"32,768 simple paths exist; capped at {len(search.paths)}",
        )
    )


# --------------------------------------------------------------------- database suite


def bench_database_url(base: str | None = None) -> str:
    url = sa.engine.make_url(base or Settings().database_url)
    return url.set(database=f"{url.database}_bench").render_as_string(hide_password=False)


def ensure_database(url: str) -> None:
    """Create the benchmark database if needed and bring it to head."""
    parsed = sa.engine.make_url(url)
    admin = sa.create_engine(
        parsed.set(database="postgres", drivername="postgresql+psycopg").render_as_string(
            hide_password=False
        ),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": parsed.database},
            ).scalar_one_or_none()
            if exists is None:
                connection.execute(sa.text(f'CREATE DATABASE "{parsed.database}"'))
    finally:
        admin.dispose()

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "ADG_DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed against the benchmark database:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )


def seed(url: str, scale: dict[str, int]) -> tuple[int, int, str, str, str]:
    """Write a directory-shaped graph.

    Returns (principals, edges, wide group key, deep chain root, a member of the wide group).

    Rows are inserted directly rather than through the ingestion API: this measures the
    query path, and going through HTTP would measure the writer instead. The rows are the
    same rows ingestion produces.
    """
    groups = scale["groups"]
    per_group = scale["members_per_group"]
    depth = scale["depth"]
    wide = scale["wide_group"]

    domain = "S-1-5-21-2000000001-2000000002-2000000003"
    run_id = uuid.UUID("00000000-0000-4000-8000-00000000be01")
    moment = dt.datetime(2026, 9, 14, 9, 0, tzinfo=dt.UTC)
    stamp = {
        "source_key": "benchmark",
        "first_observed_at": moment,
        "first_observed_run_id": run_id,
        "last_observed_at": moment,
        "last_observed_run_id": run_id,
        "created_at": moment,
        "updated_at": moment,
    }

    principal_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []

    def group_sid(index: int) -> str:
        return f"{domain}-{100000 + index}"

    def user_sid(index: int) -> str:
        return f"{domain}-{500000 + index}"

    def add_edge(group: str, member: str) -> None:
        edge_rows.append(
            {
                "edge_key": f"{group}->{member}|directory_group_member",
                "group_key": group,
                "member_key": member,
                "group_sid": group,
                "member_sid": member,
                "edge_kind": "directory_group_member",
                "host_key": None,
                "member_kind": None,
                "is_foreign_security_principal": False,
                **stamp,
            }
        )

    for index in range(groups):
        principal_rows.append(
            {
                "principal_key": group_sid(index),
                "sid": group_sid(index),
                "principal_kind": "domain_group",
                "host_key": None,
                "domain_sid": domain,
                "display_name": f"Group-{index:05d}",
                "sam_account_name": f"Group-{index:05d}",
                "user_principal_name": None,
                "distinguished_name": None,
                "group_scope": "universal",
                "group_type": "security",
                "enabled": None,
                "is_deleted": False,
                "unresolved_reason": None,
                "last_known_name": None,
                **stamp,
            }
        )

    users = 0
    for index in range(groups):
        for offset in range(per_group):
            member = users + offset
            principal_rows.append(
                {
                    "principal_key": user_sid(member),
                    "sid": user_sid(member),
                    "principal_kind": "user",
                    "host_key": None,
                    "domain_sid": domain,
                    "display_name": f"User {member:06d}",
                    "sam_account_name": f"user{member:06d}",
                    "user_principal_name": f"user{member:06d}@corp.example.com",
                    "distinguished_name": None,
                    "group_scope": None,
                    "group_type": None,
                    "enabled": True,
                    "is_deleted": False,
                    "unresolved_reason": None,
                    "last_known_name": None,
                    **stamp,
                }
            )
            add_edge(group_sid(index), user_sid(member))
        users += per_group

    # A nesting strand, so a deep traversal has somewhere to go.
    for level in range(min(depth, groups - 1)):
        add_edge(group_sid(level + 1), group_sid(level))

    # One deliberately wide group, which is where a frontier stops being small.
    wide_key = f"{domain}-999999"
    principal_rows.append(
        {
            "principal_key": wide_key,
            "sid": wide_key,
            "principal_kind": "domain_group",
            "host_key": None,
            "domain_sid": domain,
            "display_name": "Everyone-RW",
            "sam_account_name": "Everyone-RW",
            "user_principal_name": None,
            "distinguished_name": None,
            "group_scope": "universal",
            "group_type": "security",
            "enabled": None,
            "is_deleted": False,
            "unresolved_reason": None,
            "last_known_name": None,
            **stamp,
        }
    )
    for index in range(wide):
        add_edge(wide_key, user_sid(index % max(users, 1)) if users else f"{domain}-{index}")

    deduplicated = list({str(row["edge_key"]): row for row in edge_rows}.values())

    engine = sa.create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
            connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
            for start in range(0, len(principal_rows), 5_000):
                connection.execute(sa.insert(principals), principal_rows[start : start + 5_000])
            for start in range(0, len(deduplicated), 5_000):
                connection.execute(sa.insert(membership_edges), deduplicated[start : start + 5_000])
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            # Without fresh statistics the planner is choosing on guesses, and a plan
            # captured from guesses says nothing about production.
            connection.execute(sa.text("ANALYZE principals"))
            connection.execute(sa.text("ANALYZE membership_edges"))
    finally:
        engine.dispose()

    deep_leaf = group_sid(0)
    wide_member = user_sid(0) if users else f"{domain}-0"
    return len(principal_rows), len(deduplicated), wide_key, deep_leaf, wide_member


REPEATS = 5
"""Runs per database measurement. The median is reported; the first is always slowest.

A single timing against PostgreSQL measures connection warm-up, an empty buffer cache, and
whatever else the machine was doing, none of which is the query. The median of five is
still not a production number — nothing measured on one laptop is — but it is a number that
means the same thing twice in a row.
"""


async def _timed(
    suite: Suite,
    name: str,
    database: Database,
    limits: TraversalLimits,
    run: Callable[[MembershipRepository], Any],
) -> None:
    timings: list[float] = []
    nodes = 0
    edges = 0
    truncated = False
    for _ in range(REPEATS):
        async with database.session() as session:
            repository = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
            started = time.perf_counter()
            result = await run(repository)
            timings.append(time.perf_counter() - started)
            nodes = len(
                getattr(result, "nodes", getattr(result, "paths", getattr(result, "items", ())))
            )
            edges = repository.edges_fetched
            truncated = not getattr(result, "complete", True)

    timings.sort()
    suite.record(
        Measurement(
            suite="postgres",
            name=name,
            nodes=nodes,
            edges=edges,
            seconds=timings[len(timings) // 2],
            detail=(
                ("truncated; " if truncated else "")
                + f"median of {REPEATS}, best {timings[0] * 1000:.1f} ms, "
                f"worst {timings[-1] * 1000:.1f} ms"
            ),
        )
    )


async def run_database_suite(suite: Suite, url: str, scale: dict[str, int]) -> None:
    ensure_database(url)
    principal_count, edge_count, wide_key, deep_key, wide_member = seed(url, scale)
    suite.record(
        Measurement(
            suite="postgres",
            name="dataset",
            nodes=principal_count,
            edges=edge_count,
            detail=f"{principal_count:,} principals, {edge_count:,} edges, analyzed",
        )
    )

    settings: Settings = build_settings(environment="test", log_format="text", database_url=url)
    database = Database(settings)
    try:
        limits = TraversalLimits(max_depth=64, max_nodes=250_000, max_edges=1_000_000)
        # Warm the pool and the buffer cache, so the first measurement is a measurement of
        # the query rather than of connecting to PostgreSQL.
        async with database.session() as session:
            await session.execute(sa.text("SELECT count(*) FROM membership_edges"))

        await _timed(
            suite,
            "effective-members-wide",
            database,
            limits,
            lambda repository: GraphService(repository).effective_members(wide_key, limits),
        )
        await _timed(
            suite,
            "effective-groups-deep",
            database,
            limits,
            lambda repository: GraphService(repository).effective_groups(deep_key, limits),
        )
        await _timed(
            suite,
            "membership-paths",
            database,
            limits,
            lambda repository: GraphService(repository).membership_paths(
                wide_member, wide_key, limits
            ),
        )
        await _timed(
            suite,
            "direct-members-page",
            database,
            limits,
            lambda repository: repository.direct_members(wide_key, limit=500),
        )
        await _timed(
            suite,
            "count-direct-members",
            database,
            limits,
            lambda repository: repository.count_direct(group_key=wide_key),
        )
        await _timed(
            suite,
            "resolve-by-sid",
            database,
            limits,
            lambda repository: repository.resolve(wide_key),
        )

        suite.plans.update(await capture_plans(database, wide_key, scale))
    finally:
        await database.dispose()


async def capture_plans(database: Database, wide_key: str, scale: dict[str, int]) -> dict[str, str]:
    """``EXPLAIN (ANALYZE, BUFFERS)`` for the queries the traversal actually issues."""
    domain = "S-1-5-21-2000000001-2000000002-2000000003"
    frontier = [f"{domain}-{100000 + index}" for index in range(min(scale["groups"], 5_000))]

    statements: dict[str, tuple[str, dict[str, Any]]] = {
        "adjacency (one key)": (
            "SELECT edge_key, group_key, member_key, edge_kind FROM membership_edges "
            "WHERE group_key = ANY(:keys) "
            "ORDER BY group_key, member_key, edge_key LIMIT 1000",
            {"keys": [wide_key]},
        ),
        "adjacency (wide frontier)": (
            "SELECT edge_key, group_key, member_key, edge_kind FROM membership_edges "
            "WHERE group_key = ANY(:keys) "
            "ORDER BY group_key, member_key, edge_key LIMIT 1000",
            {"keys": frontier},
        ),
        "direct members page": (
            "SELECT edge_key, member_key FROM membership_edges WHERE group_key = :key "
            "ORDER BY member_key, edge_key LIMIT 501",
            {"key": wide_key},
        ),
        "count direct members": (
            "SELECT count(*) FROM membership_edges WHERE group_key = :key",
            {"key": wide_key},
        ),
    }

    plans: dict[str, str] = {}
    async with database.session() as session:
        for name, (sql, parameters) in statements.items():
            rows = (
                await session.execute(
                    sa.text(f"EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) {sql}"), parameters
                )
            ).all()
            plans[name] = "\n".join(_shorten(str(row[0])) for row in rows)
    return plans


def _shorten(line: str, limit: int = 160) -> str:
    """Trim an inlined array literal out of a plan line.

    A 5,000-key ``= ANY`` condition prints every key, which buries the plan under the data
    it was supposed to explain.
    """
    if len(line) <= limit:
        return line
    return f"{line[:limit]}… ({len(line) - limit} more characters)"


# --------------------------------------------------------------------------- driver


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.benchmarks.graph_benchmark",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scale", choices=sorted(SCALES), default="medium")
    parser.add_argument(
        "--database",
        action="store_true",
        help="Also run the PostgreSQL suite (creates and truncates <database>_bench).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Base URL; '_bench' is appended to the database name. Defaults to ADG_DATABASE_URL.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


async def run(scale_name: str, with_database: bool, database_url: str | None) -> Suite:
    scale = SCALES[scale_name]
    suite = Suite(scale=scale_name, machine=machine_facts())
    await run_memory_suite(suite, scale)
    if with_database:
        await run_database_suite(suite, bench_database_url(database_url), scale)
    return suite


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    install_selector_event_loop_policy()

    suite = asyncio.run(run(arguments.scale, arguments.database, arguments.database_url))

    print(json.dumps(suite.to_json(), indent=2) if arguments.as_json else suite.rendered())
    return 0


def iter_measurement_names(suite: Suite) -> Iterable[str]:
    return (item.name for item in suite.measurements)


if __name__ == "__main__":
    raise SystemExit(main())
