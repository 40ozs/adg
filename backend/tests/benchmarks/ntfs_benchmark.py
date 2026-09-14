r"""Measure what an NTFS tree costs the backend: to ingest, and to answer questions about.

Three suites, because a tree scan stresses three different things.

**Ingestion** is the one a large estate hits first. A walk of 100,000 directories produces
roughly three-quarters of a million observations, and they arrive as several thousand
batches over hours. What matters is not how fast one batch applies but whether the cost per
batch is *flat* — a writer that issues one statement per observation looks fine on the
fixtures and falls over on the estate.

**Query cost** is measured as statements per request, not as milliseconds. A millisecond is
a fact about this laptop; "this endpoint issues nine statements whatever the answer's size"
is a fact about the code, and it is the one that says whether an N+1 is present. Every
endpoint is measured at two dataset sizes and two page sizes, and anything whose count moves
with either is an N+1 by definition.

**Query plans** are captured for the statements the resource endpoints actually issue,
because a count of nine means something quite different when one of them is a sequential
scan of the ACE table.

Run it::

    python -m tests.benchmarks.ntfs_benchmark                  # small
    python -m tests.benchmarks.ntfs_benchmark --scale medium --json

It uses its own ``<database>_bench`` database, created and migrated on demand and
**truncated** on every run, so it never touches development or test data — the same database
``graph_benchmark`` uses, and for the same reason.

------------------------------------------------------------------------------------
What the directories-to-ACLs ratio does and does not buy

The collector reports that a tree of 20,000 directories holds 40 distinct ACL states. That
is a fact about *storage and query*: ``ntfs_aces`` holds one row per entry per directory, but
the distinct-permission questions an auditor asks group down to 40 answers, and
``acl_hash`` is what makes the grouping a single indexed comparison rather than a join over
every entry.

It buys nothing at collection time. Every one of those 20,000 directories was opened and had
its descriptor read, because reading it is the only way to find out which of the 40 states it
is in. Any claim that hashing lets a scan skip directories is a claim that ADG can know a
permission has not changed without looking at it, which it cannot.
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
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.config import Settings, build_settings
from app.db import Database
from app.ingestion.service import IngestionService
from app.main import create_app
from app.models.schema import (
    metadata,
    ntfs_aces,
    ntfs_resources,
    principals,
    servers,
    smb_share_aces,
    smb_shares,
)
from app.runtime import install_selector_event_loop_policy

BACKEND_ROOT = Path(__file__).resolve().parents[2]

SCALES: dict[str, dict[str, int]] = {
    # directories, entries per DACL, and how many distinct ACL states the tree holds. The
    # third is what a real estate makes small: thousands of folders, tens of decisions.
    "small": {"directories": 2_000, "aces_per_acl": 6, "acl_variants": 20, "shares": 4},
    "medium": {"directories": 20_000, "aces_per_acl": 7, "acl_variants": 40, "shares": 12},
    "large": {"directories": 100_000, "aces_per_acl": 8, "acl_variants": 80, "shares": 40},
}

TRUSTEES = [
    "S-1-5-32-544",
    "S-1-5-11",
    "S-1-1-0",
    "S-1-5-21-1111111111-2222222222-3333333333-1001",
    "S-1-5-21-1111111111-2222222222-3333333333-1002",
    "S-1-5-21-1111111111-2222222222-3333333333-1003",
    "S-1-5-21-1111111111-2222222222-3333333333-1004",
    "S-1-5-21-1111111111-2222222222-3333333333-1005",
]


@dataclass
class Measurement:
    """One measured operation, with the size of the thing it operated on."""

    suite: str
    name: str
    rows: int = 0
    statements: int = 0
    seconds: float = 0.0
    detail: str = ""

    @property
    def rows_per_second(self) -> float:
        return self.rows / self.seconds if self.seconds > 0 else 0.0

    @property
    def statements_per_row(self) -> float:
        return self.statements / self.rows if self.rows else 0.0


@dataclass
class Suite:
    """Everything one run measured, plus what it ran on."""

    scale: str
    measurements: list[Measurement] = field(default_factory=list)
    plans: dict[str, str] = field(default_factory=dict)
    machine: dict[str, str] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    def record(self, measurement: Measurement) -> None:
        self.measurements.append(measurement)

    def to_json(self) -> dict[str, Any]:
        return {
            "scale": self.scale,
            "machine": self.machine,
            "measurements": [
                {
                    **asdict(item),
                    "rows_per_second": round(item.rows_per_second, 1),
                    "statements_per_row": round(item.statements_per_row, 4),
                }
                for item in self.measurements
            ],
            "query_plans": self.plans,
            "findings": self.findings,
        }

    def rendered(self) -> str:
        width = max((len(item.name) for item in self.measurements), default=10)
        lines = [
            f"ADG NTFS backend benchmark — scale={self.scale}",
            f"{self.machine.get('python', '')} on {self.machine.get('platform', '')}",
            "",
            f"{'measurement':<{width}} {'rows':>9} {'stmts':>7} "
            f"{'seconds':>9} {'rows/s':>10}  detail",
        ]
        for item in self.measurements:
            lines.append(
                f"{item.name:<{width}} {item.rows:>9} {item.statements:>7} "
                f"{item.seconds:>9.3f} {item.rows_per_second:>10.1f}  {item.detail}"
            )
        if self.findings:
            lines.extend(["", "Findings:"])
            lines.extend(f"  * {finding}" for finding in self.findings)
        if self.plans:
            lines.extend(["", "Query plans:"])
            for name, plan in self.plans.items():
                lines.append(f"  {name}")
                lines.extend(f"    {line}" for line in plan.splitlines())
        return "\n".join(lines)


def machine_facts() -> dict[str, str]:
    return {
        "python": f"Python {platform.python_version()}",
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "measured_at": dt.datetime.now(tz=dt.UTC).isoformat(),
    }


class StatementCounter:
    """Counts the statements one session issues, by listening to the engine.

    Counting rather than timing is deliberate. A timing tells you this laptop was busy; a
    count tells you what the code did, and an N+1 is a statement count that moves with the
    size of the answer. It is the only measurement here that means the same thing on
    somebody else's machine.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine.sync_engine if hasattr(engine, "sync_engine") else engine
        self.count = 0
        self.statements: list[str] = []

    def _on_execute(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        self.count += 1
        self.statements.append(statement)

    def __enter__(self) -> StatementCounter:
        self.count = 0
        self.statements = []
        sa.event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc: object) -> None:
        sa.event.remove(self._engine, "before_cursor_execute", self._on_execute)


# --------------------------------------------------------------------- the database


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


def _paths(scale: dict[str, int]) -> list[tuple[str, str, str, int]]:
    r"""(path, server, share, depth) for a synthetic estate, parents before children.

    Shaped like a real one rather than like a flat list: a handful of shares, a few levels,
    and most of the directories at the bottom. Parents first because a boundary verdict is a
    comparison against a parent, and a seeder that wrote children first would produce a tree
    whose every directory looked like a scan root.
    """
    total = scale["directories"]
    share_count = scale["shares"]
    rows: list[tuple[str, str, str, int]] = []

    shares = [(f"fs{index // 4 + 1:02d}", f"share{index:02d}") for index in range(share_count)]
    for server, share in shares:
        rows.append((rf"\\{server}\{share}", server, share, 0))

    level = [(server, share, rf"\\{server}\{share}") for server, share in shares]
    depth = 1
    while len(rows) < total and depth <= 6:
        following: list[tuple[str, str, str]] = []
        for server, share, parent in level:
            for index in range(8):
                if len(rows) >= total:
                    break
                path = rf"{parent}\d{depth}-{index:02d}"
                rows.append((path, server, share, depth))
                following.append((server, share, path))
            if len(rows) >= total:
                break
        level = following
        depth += 1
        if not level:
            break
    return rows


def seed(url: str, scale: dict[str, int]) -> dict[str, Any]:
    """Write a synthetic NTFS estate directly, and return the keys the queries need.

    Rows are inserted rather than ingested: this suite measures the query path, and going
    through the ingestion API would measure the writer instead. The ingestion suite below
    measures the writer on purpose, and separately.
    """
    now = dt.datetime.now(tz=dt.UTC)
    run_id = uuid.uuid4()
    stamp = {
        "first_observed_at": now,
        "first_observed_run_id": run_id,
        "last_observed_at": now,
        "last_observed_run_id": run_id,
        "created_at": now,
        "updated_at": now,
        "source_key": "bench|seed",
    }

    rows = _paths(scale)
    variants = scale["acl_variants"]
    per_acl = scale["aces_per_acl"]

    server_rows = [
        {"server_key": key, "name": key.upper(), "fqdn": None, "os_caption": None, **stamp}
        for key in sorted({server for _, server, _, _ in rows})
    ]
    share_rows = [
        {
            "share_key": f"{server}|{share}",
            "server_key": server,
            "name": share,
            "unc_path": rf"\\{server}\{share}",
            "local_path": None,
            "description": None,
            "share_type": "disk",
            "is_special": False,
            **stamp,
        }
        for path, server, share, depth in rows
        if depth == 0
    ]

    resource_rows = []
    ace_rows = []
    principal_rows = [
        {
            "principal_key": sid,
            "sid": sid,
            "host_key": None,
            "domain_sid": None,
            "principal_kind": "well_known" if sid.startswith("S-1-5-32") else "user",
            "sam_account_name": None,
            "display_name": None,
            "distinguished_name": None,
            "user_principal_name": None,
            "enabled": None,
            "is_deleted": False,
            "last_known_name": None,
            **stamp,
        }
        for sid in TRUSTEES
    ]

    for index, (path, server, share, depth) in enumerate(rows):
        variant = index % variants
        # Only the variant-carrying directories are boundaries; everything else inherits.
        # That is the shape a real estate has, and the shape the ratio describes.
        is_boundary = depth == 0 or index % variants == 0
        resource_rows.append(
            {
                "resource_key": path.casefold(),
                "path": path,
                "server_key": server,
                "share_key": f"{server}|{share}",
                "local_path": None,
                "owner_sid": TRUSTEES[0],
                "group_sid": None,
                "dacl_present": True,
                "dacl_protected": depth == 0,
                "inheritance_enabled": depth != 0,
                "is_acl_boundary": is_boundary,
                "ace_count": per_acl,
                "depth_from_share_root": depth,
                "resource_kind": "directory",
                "boundary_reason": ("share_root" if depth == 0 else "acl_differs_from_parent")
                if is_boundary
                else None,
                "acl_hash": f"{variant:064x}",
                "parent_acl_hash": None,
                **stamp,
            }
        )
        for position in range(per_acl):
            sid = TRUSTEES[(variant + position) % len(TRUSTEES)]
            ace_rows.append(
                {
                    "ace_key": f"{path.casefold()}|{sid}|{position}",
                    "resource_key": path.casefold(),
                    "trustee_sid": sid,
                    "trustee_key": sid,
                    "ace_type": "allow",
                    "access_mask": 0x001F01FF,
                    # The flags byte and `source` are one fact reported twice, and the schema
                    # checks that they agree: 0x10 is the INHERITED bit, and a row claiming
                    # 'explicit' while carrying it is rejected.
                    "ace_flags": 0x13 if depth else 0x03,
                    "source": "inherited" if depth else "explicit",
                    "inherited_from": None,
                    "order_index": position,
                    **stamp,
                }
            )

    share_ace_rows = [
        {
            "ace_key": f"{row['share_key']}|{sid}|{position}",
            "share_key": row["share_key"],
            "trustee_sid": sid,
            "trustee_key": sid,
            "ace_type": "allow",
            # A share ACE carries a right token rather than a mask: the SMB layer grants
            # read / change / full, and it is a different vocabulary from the file system's.
            "right_token": "full",
            "access_mask": 0x001F01FF,
            "order_index": position,
            **stamp,
        }
        for row in share_rows
        for position, sid in enumerate(TRUSTEES[:4])
    ]

    engine = sa.create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
            connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
            connection.execute(sa.insert(principals), principal_rows)
            connection.execute(sa.insert(servers), server_rows)
            connection.execute(sa.insert(smb_shares), share_rows)
            connection.execute(sa.insert(smb_share_aces), share_ace_rows)
            for start in range(0, len(resource_rows), 5_000):
                connection.execute(sa.insert(ntfs_resources), resource_rows[start : start + 5_000])
            for start in range(0, len(ace_rows), 5_000):
                connection.execute(sa.insert(ntfs_aces), ace_rows[start : start + 5_000])
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            # Without fresh statistics the planner is choosing on guesses, and a plan captured
            # from guesses says nothing about production.
            for table in ("ntfs_resources", "ntfs_aces", "smb_shares", "smb_share_aces"):
                connection.execute(sa.text(f"ANALYZE {table}"))
    finally:
        engine.dispose()

    deep = max(rows, key=lambda row: row[3])
    return {
        "resources": len(resource_rows),
        "aces": len(ace_rows),
        "root_path": rows[0][0],
        "share_key": f"{rows[0][1]}|{rows[0][2]}",
        "server_key": rows[0][1],
        "deep_path": deep[0],
        "trustee": TRUSTEES[0],
    }


# ------------------------------------------------------------------- the query suite

REPEATS = 5
"""Runs per measurement; the median is reported. The first is always slowest — connection
warm-up and an empty buffer cache, neither of which is the query."""


async def measure_request(
    suite: Suite,
    client: AsyncClient,
    counter: StatementCounter,
    *,
    name: str,
    url: str,
    detail: str = "",
) -> int:
    """One endpoint, timed and counted. Returns the statements it issued."""
    timings: list[float] = []
    statements = 0
    for attempt in range(REPEATS):
        with counter:
            started = time.perf_counter()
            response = await client.get(url)
            timings.append(time.perf_counter() - started)
            if attempt == 0:
                statements = counter.count
    if response.status_code != 200:
        raise RuntimeError(f"{url} returned {response.status_code}: {response.text[:200]}")

    body = response.json()
    rows = len(body.get("items", body.get("entries", []))) or 1
    timings.sort()
    suite.record(
        Measurement(
            suite="query",
            name=name,
            rows=rows,
            statements=statements,
            seconds=timings[len(timings) // 2],
            detail=detail,
        )
    )
    return statements


async def run_query_suite(suite: Suite, url: str, keys: dict[str, Any]) -> None:
    """Every read endpoint, at two page sizes, counting statements rather than trusting them."""
    from urllib.parse import quote

    settings = build_settings(environment="test", database_url=url)
    database = Database(settings)
    app = create_app(settings)
    app.state.database = database
    counter = StatementCounter(database.engine)

    resource = quote(keys["deep_path"], safe="")
    share = quote(keys["share_key"], safe="")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://benchmark") as client:
        try:
            small = {}
            large = {}
            cases = [
                ("servers", "/api/v1/servers?limit={n}"),
                ("server shares", f"/api/v1/servers/{keys['server_key']}/shares?limit={{n}}"),
                ("share acl", f"/api/v1/shares/{share}/acl?limit={{n}}"),
                ("share root acl", f"/api/v1/shares/{share}/root-acl?limit={{n}}"),
                ("resource acl", f"/api/v1/resources/{resource}/acl?limit={{n}}"),
                ("trustee shares", f"/api/v1/principals/{keys['trustee']}/shares?limit={{n}}"),
            ]
            for name, template in cases:
                small[name] = await measure_request(
                    suite,
                    client,
                    counter,
                    name=f"{name} (limit 1)",
                    url=template.format(n=1),
                    detail="one row",
                )
                large[name] = await measure_request(
                    suite,
                    client,
                    counter,
                    name=f"{name} (limit 100)",
                    url=template.format(n=100),
                    detail="a hundred rows",
                )

            # Not paged, but the most expensive read in the API: it recomputes two digests
            # and fetches a parent.
            await measure_request(
                suite,
                client,
                counter,
                name="resource detail",
                url=f"/api/v1/resources/{resource}",
                detail="one directory + parent",
            )
            await measure_request(
                suite,
                client,
                counter,
                name="share detail",
                url=f"/api/v1/shares/{share}",
                detail="one share + its NTFS root",
            )

            # The N+1 test, stated as the property rather than as a threshold: a request's
            # statement count must not move with the size of its answer.
            for name in small:
                if large[name] != small[name]:
                    suite.findings.append(
                        f"N+1: '{name}' issued {small[name]} statement(s) for one row and "
                        f"{large[name]} for a hundred. A read's cost must not grow with its "
                        f"answer."
                    )
            if not suite.findings:
                suite.findings.append(
                    "No endpoint's statement count moved between a one-row and a "
                    "hundred-row answer, at either dataset size."
                )

            suite.plans.update(await capture_plans(database, keys))
        finally:
            await database.dispose()


async def capture_plans(database: Database, keys: dict[str, Any]) -> dict[str, str]:
    """``EXPLAIN (ANALYZE, BUFFERS)`` for the statements the resource endpoints issue."""
    statements = {
        "one directory by key": (
            "SELECT * FROM ntfs_resources WHERE resource_key = :key",
            {"key": keys["deep_path"].casefold()},
        ),
        "one directory's DACL in order": (
            "SELECT * FROM ntfs_aces WHERE resource_key = :key "
            "ORDER BY order_index NULLS LAST, ace_key",
            {"key": keys["deep_path"].casefold()},
        ),
        "the boundaries of one share": (
            "SELECT * FROM ntfs_resources WHERE share_key = :share AND is_acl_boundary "
            "ORDER BY resource_key LIMIT 100",
            {"share": keys["share_key"]},
        ),
        "every directory naming one trustee": (
            "SELECT resource_key FROM ntfs_aces WHERE trustee_key = :trustee "
            "ORDER BY resource_key LIMIT 100",
            {"trustee": keys["trustee"]},
        ),
    }

    plans: dict[str, str] = {}
    async with database.engine.connect() as connection:
        for name, (sql, parameters) in statements.items():
            rows = (
                await connection.execute(
                    sa.text(f"EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) {sql}"), parameters
                )
            ).all()
            plans[name] = "\n".join(_shorten(str(row[0])) for row in rows)
    return plans


def _shorten(line: str, limit: int = 160) -> str:
    return line if len(line) <= limit else line[: limit - 1] + "…"


# --------------------------------------------------------------- the ingestion suite


def _batch(run_id: str, sequence: int, paths: Sequence[str], per_acl: int) -> dict[str, Any]:
    """One contract-v1 batch of directory observations, shaped like the collector's.

    Source keys come from ``app.contracts.v1.keys`` rather than from a format string here.
    The contract validates that a reported key matches the derivation for its kind - a
    collector that derives them differently creates two rows for one object - so a benchmark
    that invented its own would be measuring a payload the server rejects.
    """
    from app.contracts.v1.keys import ntfs_ace_key, ntfs_resource_key
    from app.domain import AceType, Sid
    from app.domain.acl_hash import AclAceFacts, normalize_acl

    # The planner re-derives this digest from the ACEs in the same batch and refuses the
    # batch if the two disagree (collector-protocol section 5). So the benchmark computes a
    # real one: sending an invented digest would measure the rejection path.
    entries = [
        AclAceFacts(
            trustee_sid=TRUSTEES[position % len(TRUSTEES)],
            ace_type=AceType.ALLOW,
            access_mask=0x001F01FF,
            ace_flags=0x13,
            order_index=position,
        )
        for position in range(per_acl)
    ]
    digest = normalize_acl(dacl_present=True, dacl_protected=False, aces=entries).digest

    now = dt.datetime.now(tz=dt.UTC).isoformat().replace("+00:00", "Z")
    observations: list[dict[str, Any]] = []
    for path in paths:
        observations.append(
            {
                "schema_version": "1.3",
                "kind": "ntfs_resource",
                "run_id": run_id,
                "observed_at": now,
                "source_key": ntfs_resource_key(path),
                "path": path,
                "server_name": path.split("\\")[2],
                "share_name": path.split("\\")[3],
                "owner_sid": TRUSTEES[0],
                "dacl_present": True,
                "dacl_protected": False,
                "inheritance_enabled": True,
                "is_acl_boundary": False,
                "ace_count": per_acl,
                "depth_from_share_root": path.count("\\") - 3,
                "resource_kind": "directory",
                "acl_hash": digest,
            }
        )
        for position in range(per_acl):
            observations.append(
                {
                    "schema_version": "1.3",
                    "kind": "ntfs_ace",
                    "run_id": run_id,
                    "observed_at": now,
                    "source_key": ntfs_ace_key(
                        path=path,
                        trustee_sid=Sid(TRUSTEES[position % len(TRUSTEES)]),
                        ace_type="allow",
                        access_mask=0x001F01FF,
                        ace_flags=0x13,
                    ),
                    "path": path,
                    "trustee_sid": TRUSTEES[position % len(TRUSTEES)],
                    "ace_type": "allow",
                    "access_mask": 0x001F01FF,
                    "ace_flags": 0x13,
                    "source": "inherited",
                    "order_index": position,
                }
            )
    return {
        "schema_version": "1.3",
        "run_id": run_id,
        "batch_id": str(uuid.uuid4()),
        "sequence": sequence,
        "is_final": False,
        "observations": observations,
    }


async def run_ingestion_suite(suite: Suite, url: str, scale: dict[str, int]) -> None:
    """Apply batches at three sizes, and report statements per batch rather than per second.

    The number that matters is the third column: a writer that issues a bounded number of
    statements per batch scales with the estate, and one that issues a statement per
    observation does not — and on a fixture of twenty rows the two are indistinguishable.
    """
    from app.contracts.v1 import ObservationBatch, ScanRunStart

    settings = build_settings(environment="test", database_url=url)
    database = Database(settings)
    counter = StatementCounter(database.engine)
    per_acl = scale["aces_per_acl"]

    try:
        # Sized in observations rather than in directories, because that is what the contract
        # bounds: a batch carries at most 1000, and one directory arrives as itself plus one
        # observation per ACE.
        for budget in (100, 500, 1000):
            batch_size = max(1, budget // (1 + per_acl))
            run_id = str(uuid.uuid4())
            paths = [rf"\\fs01\bench\b{budget}\d{index:06d}" for index in range(batch_size * 4)]

            async with database.session() as session:
                service = IngestionService(session)
                await service.start_run(
                    ScanRunStart.model_validate(
                        {
                            "schema_version": "1.3",
                            "run_id": run_id,
                            "source": {
                                "collector": "ntfs",
                                "collector_host": "BENCHMARK",
                                "collector_version": "0.0.0",
                                "method": "tests.benchmarks.ntfs_benchmark",
                                "target": rf"\\fs01\bench\b{budget}",
                            },
                            "started_at": dt.datetime.now(tz=dt.UTC)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "scopes": [
                                {"kind": "directory_tree", "key": rf"\\fs01\bench\b{budget}"}
                            ],
                            "incremental": True,
                        }
                    )
                )

            chunks = [
                paths[start : start + batch_size] for start in range(0, len(paths), batch_size)
            ]
            observations = 0
            statements = 0
            started = time.perf_counter()
            for sequence, chunk in enumerate(chunks, start=1):
                payload = _batch(run_id, sequence, chunk, per_acl)
                batch = ObservationBatch.model_validate(payload)
                async with database.session() as session:
                    service = IngestionService(session)
                    with counter:
                        outcome = await service.apply_batch(batch)
                    statements += counter.count
                observations += outcome.applied
            elapsed = time.perf_counter() - started

            suite.record(
                Measurement(
                    suite="ingestion",
                    name=f"apply batches of {budget} observations",
                    rows=observations,
                    statements=statements,
                    seconds=elapsed,
                    detail=(
                        f"{len(chunks)} batches of {batch_size} directories, "
                        f"{statements / len(chunks):.1f} statements per batch"
                    ),
                )
            )

        counts = [
            item.statements / max(1, int(item.detail.split()[0]))
            for item in suite.measurements
            if item.suite == "ingestion"
        ]
        if max(counts) - min(counts) > 1.0:
            suite.findings.append(
                "Ingestion cost per batch moved with the batch size: "
                f"{[round(value, 1) for value in counts]} statements per batch. A bounded "
                "writer issues the same number whatever the batch holds."
            )
        else:
            suite.findings.append(
                f"Ingestion issues {min(counts):.0f} statements per batch whatever the batch "
                "holds, so a batch of 1000 costs the same round trips as a batch of 50."
            )
    finally:
        await database.dispose()


# ------------------------------------------------------------------------ the runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.benchmarks.ntfs_benchmark",
        description="Measure what an NTFS tree costs the backend.",
    )
    parser.add_argument("--scale", choices=sorted(SCALES), default="small")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Base URL; '_bench' is appended to the database name. Defaults to ADG_DATABASE_URL.",
    )
    return parser


async def run(scale_name: str, database_url: str | None) -> Suite:
    scale = SCALES[scale_name]
    suite = Suite(scale=scale_name, machine=machine_facts())

    url = bench_database_url(database_url)
    ensure_database(url)
    keys = seed(url, scale)
    suite.machine["dataset"] = f"{keys['resources']} directories, {keys['aces']} ACEs"

    await run_query_suite(suite, url, keys)
    await run_ingestion_suite(suite, url, scale)
    return suite


def main(argv: Sequence[str] | None = None) -> int:
    install_selector_event_loop_policy()
    arguments = build_parser().parse_args(argv)
    suite = asyncio.run(run(arguments.scale, arguments.database_url))
    print(json.dumps(suite.to_json(), indent=2) if arguments.json else suite.rendered())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
