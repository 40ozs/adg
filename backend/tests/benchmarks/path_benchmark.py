r"""Measured cost of the relationship path engine (ADG-GRAPH-01C).

Not tests, and nothing here is asserted. ``tests/db/test_graph_paths_api.py`` asserts the
*shape* of the cost — reads per level, not per member — because a statement count is the same
on every machine and a millisecond is not. This records the numbers an operator waits for, and
they are written down in ``docs/evidence/adg-graph/ADG-GRAPH-01C-report.md`` with the machine
that produced them. ``test_path_benchmark_harness.py`` checks only that the harness still runs.

Two suites:

* **memory** — synthetic graphs (a deep chain, a wide group, layered groups with many equal
  routes, a membership ring) over the in-memory adjacency the unit tests use: the algorithms'
  own cost and their read count, without a database.
* **nullmeridian** — the Null Meridian demo estate, reset into a dedicated demo database
  through the same ingestion and derivation a production estate goes through, and queried
  through the HTTP API. Each case is run once cold (first request after the reset) and then
  ``--repeat`` times warm; the two are reported separately and never mixed.

Run it::

    python -m tests.benchmarks.path_benchmark --suite memory
    python -m tests.benchmarks.path_benchmark --suite nullmeridian --reset --json
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import platform
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.config import Settings, build_settings
from app.db import Database
from app.domain.relationships import RelationshipType
from app.main import create_app
from app.runtime import install_selector_event_loop_policy
from app.traversal.search import (
    SearchLimits,
    alternate_paths,
    neighbors,
    reverse_reachability,
    shortest_path,
)
from tests.support.auth import auth_headers
from tests.support.traversal import Graph, MemoryAdjacency, group, resource, user

DEFAULT_REPEAT = 5
BENCH_DATABASE = "adg_path_bench_demo"
SCALES: dict[str, dict[str, int]] = {
    "tiny": {"depth": 6, "wide": 50, "layers": 3, "layer_width": 3, "ring": 5},
    "default": {"depth": 30, "wide": 20_000, "layers": 6, "layer_width": 6, "ring": 200},
}


@dataclass
class Measurement:
    suite: str
    name: str
    nodes: int
    relationships: int
    depth: int
    cap: int | None
    result: str
    reads: int
    statements: int | None
    seconds_cold: float
    seconds_warm_median: float | None = None


@dataclass
class Suite:
    scale: str
    machine: dict[str, str]
    measurements: list[Measurement] = field(default_factory=list)


def machine_facts() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
    }


# --- memory -------------------------------------------------------------------------------


def _chain(length: int) -> Graph:
    graph = Graph()
    nodes = [user(10_000), *(group(20_000 + index) for index in range(length))]
    for member, of in itertools.pairwise(nodes):
        graph.member(member, of)
    graph.control(RelationshipType.GENERIC_WRITE, nodes[-1], resource("\\\\fs01\\deep"))
    return graph


def _wide(members: int) -> Graph:
    graph = Graph()
    target = group(30_000)
    for index in range(members):
        graph.member(user(40_000 + index), target)
    graph.control(RelationshipType.GENERIC_WRITE, target, resource("\\\\fs01\\wide"))
    return graph


def _layered(layers: int, width: int) -> Graph:
    graph = Graph()
    rows = [
        [group(50_000 + layer * 100 + index) for index in range(width)] for layer in range(layers)
    ]
    for member in rows[0]:
        graph.member(user(10_000), member)
    for upper, lower in itertools.pairwise(rows):
        for member in upper:
            for of in lower:
                graph.member(member, of)
    for member in rows[-1]:
        graph.control(RelationshipType.GENERIC_WRITE, member, resource("\\\\fs01\\layered"))
    return graph


def _ring(size: int) -> Graph:
    graph = Graph()
    nodes = [group(60_000 + index) for index in range(size)]
    for member, of in itertools.pairwise([*nodes, nodes[0]]):
        graph.member(member, of)
    graph.member(user(10_000), nodes[0])
    graph.control(
        RelationshipType.GENERIC_WRITE, nodes[min(size // 2, 25)], resource("\\\\fs01\\ring")
    )
    return graph


async def _time(
    suite: Suite,
    name: str,
    graph: Graph,
    run: Callable[[MemoryAdjacency], Awaitable[tuple[str, int, int | None]]],
) -> None:
    provider = MemoryAdjacency(graph)
    started = time.perf_counter()
    result, depth, cap = await run(provider)
    elapsed = time.perf_counter() - started
    nodes = {edge.source for edge in graph.edges} | {edge.target for edge in graph.edges}
    suite.measurements.append(
        Measurement(
            suite="memory",
            name=name,
            nodes=len(nodes),
            relationships=len(graph.edges),
            depth=depth,
            cap=cap,
            result=result,
            reads=len(provider.calls),
            statements=None,
            seconds_cold=elapsed,
        )
    )


async def run_memory_suite(suite: Suite, scale: dict[str, int]) -> None:
    limits = SearchLimits(max_depth=32, max_nodes=100_000, max_edges=500_000, max_paths=100)
    chain = _chain(scale["depth"])
    wide = _wide(scale["wide"])
    layered = _layered(scale["layers"], scale["layer_width"])
    ring = _ring(scale["ring"])
    deep_target = resource("\\\\fs01\\deep")

    async def deep(provider: MemoryAdjacency) -> tuple[str, int, int | None]:
        found = await shortest_path(user(10_000), deep_target, provider, limits=limits)
        return f"hops={found.paths[0].hops}", limits.max_depth, None

    async def none(provider: MemoryAdjacency) -> tuple[str, int, int | None]:
        found = await shortest_path(user(99_999), deep_target, provider, limits=limits)
        return f"paths={len(found.paths)} reasons={len(found.reasons)}", limits.max_depth, None

    async def fan(provider: MemoryAdjacency) -> tuple[str, int, int | None]:
        found = await reverse_reachability(resource("\\\\fs01\\wide"), provider, limits=limits)
        return f"sources={len(found.sources)}", limits.max_depth, None

    async def equal(provider: MemoryAdjacency) -> tuple[str, int, int | None]:
        found = await alternate_paths(
            user(10_000), resource("\\\\fs01\\layered"), provider, limits=limits
        )
        return f"paths={len(found.paths)}", limits.max_depth, limits.max_paths

    async def cyclic(provider: MemoryAdjacency) -> tuple[str, int, int | None]:
        found = await shortest_path(
            user(10_000), resource("\\\\fs01\\ring"), provider, limits=limits
        )
        return f"hops={found.paths[0].hops if found.paths else 0}", limits.max_depth, None

    async def around(provider: MemoryAdjacency) -> tuple[str, int, int | None]:
        found = await neighbors(group(30_000), provider, depth=2, limits=limits)
        return f"relationships={len(found.relationships)}", 2, None

    await _time(suite, "chain-shortest", chain, deep)
    await _time(suite, "chain-no-path", chain, none)
    await _time(suite, "fan-reverse", wide, fan)
    await _time(suite, "layered-alternates", layered, equal)
    await _time(suite, "ring-shortest", ring, cyclic)
    await _time(suite, "fan-neighbors", wide, around)


# --- Null Meridian ------------------------------------------------------------------------


class _Statements:
    def __init__(self, engine: Any) -> None:
        self._engine = engine.sync_engine
        self.count = 0

    def _record(self, *_: Any) -> None:
        self.count += 1

    def __enter__(self) -> _Statements:
        self.count = 0
        sa.event.listen(self._engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_: object) -> None:
        sa.event.remove(self._engine, "before_cursor_execute", self._record)


def bench_settings(base_url: str) -> Settings:
    url = sa.engine.make_url(base_url).set(database=BENCH_DATABASE)
    return build_settings(
        environment="test",
        log_format="text",
        log_level="WARNING",
        runtime_mode="demo",
        database_url=url.render_as_string(hide_password=False),
    )


async def _reset(settings: Settings) -> None:
    from app.demo_mode.lifecycle import reset_demo

    url = sa.engine.make_url(settings.database_url)
    admin = sa.create_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            connection.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()
    await reset_demo(settings, migrate=True)


async def _pick(database: Database) -> dict[str, str]:
    """Representative nodes, chosen by a deterministic query over the estate itself."""
    async with database.session() as session:
        targets = (
            await session.execute(
                sa.text(
                    "SELECT target_key FROM graph_relationships WHERE target_kind = "
                    "'ntfs_resource' GROUP BY target_key ORDER BY count(*) DESC, target_key "
                    "LIMIT 5"
                )
            )
        ).scalars()
        local = (
            await session.execute(
                sa.text(
                    "SELECT source_key, target_key FROM graph_relationships WHERE "
                    "relationship_type = 'local_group_member_of' ORDER BY source_key, target_key "
                    "LIMIT 1"
                )
            )
        ).one()
        biggest = (
            await session.execute(
                sa.text(
                    "SELECT target_key FROM graph_relationships WHERE relationship_type IN "
                    "('member_of', 'primary_group_of') GROUP BY target_key ORDER BY count(*) "
                    "DESC, target_key LIMIT 1"
                )
            )
        ).scalar_one()
    return {
        "targets": ",".join(f"ntfs_resource:{target}" for target in targets),
        "local_member": f"principal:{local[0]}",
        "local_group": f"principal:{local[1]}",
        "biggest_group": f"principal:{biggest}",
    }


async def run_null_meridian_suite(suite: Suite, settings: Settings, repeat: int) -> None:
    database = Database(settings)
    app = create_app(settings)
    app.state.database = database
    try:
        async with database.session() as session:
            relationships = (
                await session.execute(sa.text("SELECT count(*) FROM graph_relationships"))
            ).scalar_one()
            nodes = (
                await session.execute(
                    sa.text(
                        "SELECT count(*) FROM (SELECT source_kind, source_key FROM "
                        "graph_relationships UNION SELECT target_kind, target_key FROM "
                        "graph_relationships) AS n"
                    )
                )
            ).scalar_one()
        picks = await _pick(database)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=auth_headers(settings),
        ) as client:
            # Of the five most-granted resources, the one with the deepest route into it.
            candidates = []
            for target in picks["targets"].split(","):
                listing = (
                    await client.get(
                        "/api/v1/graph/reachability", params={"target": target, "limit": 500}
                    )
                ).json()
                deepest_here = max(
                    listing["items"], key=lambda item: (item["hops"], item["node"]["ref"])
                )
                candidates.append((-deepest_here["hops"], target, listing, deepest_here))
            _, chosen, reverse, deepest = min(candidates, key=lambda item: (item[0], item[1]))
            picks["target"] = chosen
            reached = {item["node"]["ref"] for item in reverse["items"]}
            outsider = (
                await client.get("/api/v1/principals", params={"kind": "users", "limit": 500})
            ).json()["items"]
            stranger = next(
                f"principal:{item['key']}"
                for item in outsider
                if f"principal:{item['key']}" not in reached
            )
            cases: list[tuple[str, str, dict[str, Any], int, int | None]] = [
                (
                    "short-path",
                    "/api/v1/graph/paths/shortest",
                    {"source": picks["local_member"], "target": picks["local_group"]},
                    10,
                    None,
                ),
                (
                    "deeper-path",
                    "/api/v1/graph/paths/shortest",
                    {"source": deepest["node"]["ref"], "target": picks["target"]},
                    10,
                    None,
                ),
                (
                    "no-path",
                    "/api/v1/graph/paths/shortest",
                    {"source": stranger, "target": picks["target"]},
                    10,
                    None,
                ),
                (
                    "alternates-cap-10",
                    "/api/v1/graph/paths",
                    {"source": deepest["node"]["ref"], "target": picks["target"]},
                    10,
                    10,
                ),
                (
                    "alternates-cap-100",
                    "/api/v1/graph/paths",
                    {
                        "source": deepest["node"]["ref"],
                        "target": picks["target"],
                        "max_paths": 100,
                    },
                    10,
                    100,
                ),
                (
                    "reverse-reachability",
                    "/api/v1/graph/reachability",
                    {"target": picks["target"], "limit": 100},
                    10,
                    None,
                ),
                (
                    "neighbors-depth-1",
                    "/api/v1/graph/neighbors",
                    {"node": picks["biggest_group"], "limit": 100},
                    1,
                    None,
                ),
                (
                    "neighbors-depth-2",
                    "/api/v1/graph/neighbors",
                    {"node": picks["biggest_group"], "depth": 2, "limit": 100},
                    2,
                    None,
                ),
            ]
            for name, route, params, depth, cap in cases:
                statements = _Statements(database.engine)
                started = time.perf_counter()
                with statements:
                    response = await client.get(route, params=params)
                cold = time.perf_counter() - started
                body = response.json()
                warm: list[float] = []
                for _ in range(repeat):
                    started = time.perf_counter()
                    await client.get(route, params=params)
                    warm.append(time.perf_counter() - started)
                suite.measurements.append(
                    Measurement(
                        suite="nullmeridian",
                        name=name,
                        nodes=int(nodes),
                        relationships=int(relationships),
                        depth=depth,
                        cap=cap,
                        result=_summary(body),
                        reads=int(body.get("exploration", {}).get("reads", 0)),
                        statements=statements.count,
                        seconds_cold=cold,
                        seconds_warm_median=statistics.median(warm) if warm else None,
                    )
                )
    finally:
        await database.dispose()


def _summary(body: dict[str, Any]) -> str:
    if "outcome" in body and "path" in body:
        path = body["path"]
        return f"{body['outcome']} hops={path['hops'] if path else '-'}"
    if "paths" in body:
        return f"{body['outcome']} paths={len(body['paths'])}"
    page = body.get("page", {})
    return f"total={page.get('total')} complete={body['completeness']['complete']}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("memory", "nullmeridian"), default="memory")
    parser.add_argument("--scale", choices=sorted(SCALES), default="default")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--reset", action="store_true", help="Reset Null Meridian first.")
    parser.add_argument("--json", action="store_true")
    return parser


async def main(arguments: argparse.Namespace) -> Suite:
    suite = Suite(scale=arguments.scale, machine=machine_facts())
    if arguments.suite == "memory":
        await run_memory_suite(suite, SCALES[arguments.scale])
        return suite
    settings = bench_settings(Settings().database_url)
    if arguments.reset:
        await _reset(settings)
    await run_null_meridian_suite(suite, settings, arguments.repeat)
    return suite


if __name__ == "__main__":  # pragma: no cover - a command, not a test
    install_selector_event_loop_policy()
    parsed = build_parser().parse_args()
    result = asyncio.run(main(parsed))
    if parsed.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        for item in result.measurements:
            warm = "-" if item.seconds_warm_median is None else f"{item.seconds_warm_median:.3f}"
            counted = "-" if item.statements is None else str(item.statements)
            print(
                f"{item.suite:13} {item.name:22} rel={item.relationships:6} "
                f"nodes={item.nodes:6} depth={item.depth:2} cap={item.cap or '-':>4} "
                f"reads={item.reads:3} stmts={counted:>4} "
                f"cold={item.seconds_cold:.3f}s warm={warm}s  {item.result}"
            )
