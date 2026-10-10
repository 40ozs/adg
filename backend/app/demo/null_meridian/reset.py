r"""The one operation that puts a demo database into the canonical Null Meridian state.

    python -m app.demo.null_meridian reset

What it does, in order, and why each step is shaped the way it is:

1. **Refuses anything that is not a demo.** ``ADG_RUNTIME_MODE`` must be ``demo``, the
   database name must contain ``demo`` (both already enforced by :class:`app.config.Settings`
   at startup, checked again here because this is the step that deletes), and the database
   must either carry the demo marker or hold no scan runs at all. A database that holds data
   and was never marked is refused even when its name says ``demo``.
2. **Migrates** the database to the current schema, with the same ``alembic upgrade heads``
   an operator runs. The demo uses the production schema unchanged.
3. **Marks** the database as demo-owned: ``COMMENT ON DATABASE`` carries
   :data:`DEMO_MARKER`. A comment rather than a table, so the demo adds nothing to the schema.
4. **Empties** every ADG table in that database. Everything in a marked demo database is
   demo-owned, so this removes only demo data; production lives in another database that
   step 1 refuses to touch.
5. **Seeds** the three scan generations through the ordinary ingestion endpoints —
   ``POST /api/v1/scan-runs``, ``.../batches``, ``.../completion`` — in-process, with the
   same validation, idempotency and reconciliation a Windows collector gets. No row is
   written by hand.
6. **Evaluates the risk rules** at a fixed instant, so findings exist on first page load.
7. **Fingerprints** the logical state and returns it with the counts, so two resets can be
   compared and the comparison means something.

Nothing here opens a network connection other than to the configured PostgreSQL, runs
PowerShell, reads a directory, or uses a Windows credential.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import sqlalchemy as sa

from app.auth.roles import Role
from app.auth.tokens import issue_development_token
from app.config import DEMO_DATABASE_MARKER, Settings
from app.db import Database
from app.demo.null_meridian.canon import SEED, SNAPSHOT
from app.demo.null_meridian.generations import build_generations
from app.demo.seed import SeedReport, seed_transcripts
from app.models.schema import metadata
from app.operations.commands import EvaluationSummary, evaluate_risks

__all__ = [
    "DEMO_MARKER",
    "EVALUATED_AT",
    "DemoResetRefused",
    "ResetReport",
    "fingerprint",
    "logical_counts",
    "reset_demo",
]

#: The comment a demo database carries. Includes the seed, so a database seeded from a
#: different canon version is recognizably not this one.
DEMO_MARKER: Final = f"adg-demo:{SEED}"

#: The instant the risk rules are evaluated at. Fixed, so findings' timestamps are part of
#: the canon rather than of the moment somebody ran the reset.
EVALUATED_AT: Final = dt.datetime.combine(SNAPSHOT, dt.time(6, 0), tzinfo=dt.UTC)

BACKEND_ROOT: Final = Path(__file__).resolve().parents[3]

#: Columns whose values come from the clock or a random id at ingestion time rather than
#: from the canon. The fingerprint leaves them out; everything else must match exactly.
VOLATILE_COLUMNS: Final = frozenset(
    {
        # Row bookkeeping written from the database clock at insert and update time.
        "created_at",
        "updated_at",
        # A serial surrogate key.
        "id",
        # uuid4 identifiers of risk evaluations.
        "first_evaluation_id",
        "last_evaluation_id",
        "resolved_evaluation_id",
    }
)

#: The tables whose content is the demo's logical state.
FINGERPRINT_TABLES: Final[tuple[str, ...]] = (
    "scan_runs",
    "principals",
    "membership_edges",
    "servers",
    "smb_shares",
    "smb_share_aces",
    "ntfs_resources",
    "ntfs_aces",
    "object_versions",
    "risk_findings",
)


class DemoResetRefused(RuntimeError):
    """The reset declined to touch this database. The message says why."""


@dataclass(slots=True)
class ResetReport:
    seed: str
    snapshot: str
    database: str
    duration_seconds: float
    seed_seconds: float
    runs: int
    observations_applied: int
    recorded_statuses: dict[str, int]
    counts: dict[str, int]
    risk: dict[str, Any]
    fingerprint: str
    table_digests: dict[str, str] = field(default_factory=dict)

    def as_document(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "snapshot": self.snapshot,
            "database": self.database,
            "duration_seconds": round(self.duration_seconds, 1),
            "seed_seconds": round(self.seed_seconds, 1),
            "runs": self.runs,
            "observations_applied": self.observations_applied,
            "recorded_statuses": self.recorded_statuses,
            "counts": self.counts,
            "risk": self.risk,
            "fingerprint": self.fingerprint,
            "table_digests": self.table_digests,
        }


def _sync_url(settings: Settings) -> str:
    return sa.engine.make_url(settings.database_url).render_as_string(hide_password=False)


def require_demo(settings: Settings) -> None:
    """Refuse unless the settings describe a demo deployment on a demo database."""
    if not settings.is_demo:
        raise DemoResetRefused(
            "ADG_RUNTIME_MODE is not 'demo'. The reset empties the database it is pointed "
            "at and runs only in a deployment started in Demo Mode."
        )
    if settings.is_production:
        raise DemoResetRefused("ADG_ENVIRONMENT=production. The reset never runs there.")
    if DEMO_DATABASE_MARKER not in settings.database_name.casefold():
        raise DemoResetRefused(
            f"The database {settings.database_name!r} is not named as a demo database."
        )


def _inspect_target(engine: sa.Engine) -> tuple[str | None, int]:
    """The database's comment, and how many scan runs it holds (-1 if unmigrated)."""
    with engine.connect() as connection:
        comment = connection.execute(
            sa.text(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                "WHERE datname = current_database()"
            )
        ).scalar_one_or_none()
        exists = connection.execute(sa.text("SELECT to_regclass('public.scan_runs')")).scalar()
        runs = -1
        if exists is not None:
            runs = int(connection.execute(sa.text("SELECT count(*) FROM scan_runs")).scalar_one())
    return comment, runs


def _migrate(settings: Settings) -> None:
    """``alembic upgrade heads``, exactly as an operator runs it."""
    environment = {**os.environ, "ADG_DATABASE_URL": _sync_url(settings)}
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "heads"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DemoResetRefused(
            "The schema migration failed, so nothing was reset:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )


def _clear(engine: sa.Engine, database_name: str) -> None:
    names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
    quoted = '"' + database_name.replace('"', '""') + '"'
    with engine.begin() as connection:
        connection.execute(sa.text(f"COMMENT ON DATABASE {quoted} IS '{DEMO_MARKER}'"))
        connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@dataclass(slots=True)
class _Response:
    status_code: int
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


class InProcessPoster:
    """Posts JSON to an ASGI application in this process.

    Enough of an HTTP client for :func:`app.demo.seed.seed_transcripts`, and no more: the
    request still passes through every middleware, the authentication dependency, request
    validation and the ingestion endpoint, exactly as a collector's would. Written here
    rather than borrowed from ``httpx`` so the reset needs nothing the API image lacks.
    """

    def __init__(self, app: Any, headers: dict[str, str]) -> None:
        self._app = app
        self._headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]

    async def post(self, url: str, *, json: Any) -> _Response:
        body = _json_dumps(json)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": url,
            "raw_path": url.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                *self._headers,
                (b"host", b"demo-reset.invalid"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 0),
            "server": ("demo-reset.invalid", 80),
        }
        sent = False
        status = 500
        chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self._app(scope, receive, send)
        return _Response(status, b"".join(chunks))


def _json_dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _seeding_client(settings: Settings, database: Database) -> InProcessPoster:
    """An in-process client for the real API, holding an ingestion credential.

    The application is built from the deployment's own settings with development
    authentication and a secret generated for this call, so the token minted here is valid
    for this in-process application and for nothing else.
    """
    from app.main import create_app

    seeding_settings = settings.model_copy(
        update={
            "auth_mode": "development",
            "dev_auth_secret": secrets.token_urlsafe(48),
            # One INFO line per request is 900 requests of noise around a one-line report.
            "log_level": "WARNING",
        }
    )
    app = create_app(seeding_settings)
    app.state.database = database
    token, _ = issue_development_token(
        secret=seeding_settings.dev_auth_secret,
        subject="demo:null-meridian-reset",
        roles=[Role.ADMIN.value],
        display_name="Null Meridian demo reset",
        email=None,
        lifetime=dt.timedelta(hours=2),
    )
    return InProcessPoster(app, {"Authorization": f"Bearer {token}"})


async def seed_generations(client: Any) -> SeedReport:
    """Post every generation, in order, through the ingestion endpoints."""
    combined = SeedReport()
    for _, transcripts in build_generations():
        report = await seed_transcripts(client, transcripts)
        combined.runs.extend(report.runs)
    return combined


def _digest_rows(rows: Sequence[Sequence[Any]]) -> str:
    hasher = hashlib.sha256()
    for row in rows:
        hasher.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        hasher.update(b"\n")
    return hasher.hexdigest()


def fingerprint(engine: sa.Engine) -> tuple[str, dict[str, str]]:
    """A digest of the logical state: every fingerprinted table, volatile columns excluded,
    rows in a canonical order."""
    digests: dict[str, str] = {}
    with engine.connect() as connection:
        for name in FINGERPRINT_TABLES:
            table = metadata.tables[name]
            columns = [column for column in table.columns if column.name not in VOLATILE_COLUMNS]
            rows = connection.execute(sa.select(*columns)).all()
            rendered = sorted(
                (
                    [value.isoformat() if hasattr(value, "isoformat") else value for value in row]
                    for row in rows
                ),
                key=lambda item: json.dumps(item, default=str),
            )
            digests[name] = _digest_rows(rendered)
    overall = hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest()
    return overall, digests


def logical_counts(engine: sa.Engine) -> dict[str, int]:
    """Row counts for every collected kind, current state only, plus history."""
    queries = {
        "scan_runs": "SELECT count(*) FROM scan_runs",
        "object_versions": "SELECT count(*) FROM object_versions",
        "open_tombstones": (
            "SELECT count(*) FROM object_versions WHERE valid_to IS NULL AND NOT is_present"
        ),
        "risk_findings_open": "SELECT count(*) FROM risk_findings WHERE status = 'open'",
    }
    from app.models.current import (
        current_membership_edges,
        current_ntfs_aces,
        current_ntfs_resources,
        current_principals,
        current_servers,
        current_smb_share_aces,
        current_smb_shares,
    )

    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for label, source in (
            ("principals", current_principals),
            ("membership_edges", current_membership_edges),
            ("servers", current_servers),
            ("smb_shares", current_smb_shares),
            ("smb_share_aces", current_smb_share_aces),
            ("ntfs_resources", current_ntfs_resources),
            ("ntfs_aces", current_ntfs_aces),
        ):
            counts[label] = int(
                connection.execute(sa.select(sa.func.count()).select_from(source)).scalar_one()
            )
        for label, query in queries.items():
            try:
                counts[label] = int(connection.execute(sa.text(query)).scalar_one())
            except sa.exc.DBAPIError:
                connection.rollback()
    return counts


async def reset_demo(settings: Settings, *, migrate: bool = True) -> ResetReport:
    """Reset the demo database to the canonical Null Meridian state. See the module docs."""
    require_demo(settings)
    started = time.perf_counter()
    engine = sa.create_engine(_sync_url(settings))
    try:
        comment, runs = _inspect_target(engine)
        if comment != DEMO_MARKER and runs > 0:
            raise DemoResetRefused(
                f"The database {settings.database_name!r} holds {runs} scan runs and was "
                f"never marked as a demo database (its comment is {comment!r}). Nothing was "
                "changed. Point the demo at a new, empty database."
            )
        if migrate:
            _migrate(settings)
        _clear(engine, settings.database_name)

        database = Database(settings)
        try:
            seed_started = time.perf_counter()
            report = await seed_generations(_seeding_client(settings, database))
            seed_seconds = time.perf_counter() - seed_started
            summary: EvaluationSummary = await evaluate_risks(database, settings, now=EVALUATED_AT)
        finally:
            await database.dispose()

        overall, digests = fingerprint(engine)
        counts = logical_counts(engine)
    finally:
        engine.dispose()

    statuses: dict[str, int] = {}
    for run in report.runs:
        statuses[run.recorded_status] = statuses.get(run.recorded_status, 0) + 1
    return ResetReport(
        seed=SEED,
        snapshot=SNAPSHOT.isoformat(),
        database=settings.database_name,
        duration_seconds=time.perf_counter() - started,
        seed_seconds=seed_seconds,
        runs=len(report.runs),
        observations_applied=report.observations_applied,
        recorded_statuses=dict(sorted(statuses.items())),
        counts=counts,
        risk={
            "matched": summary.matched,
            "opened": summary.opened,
            "complete": summary.complete,
            "truncated": list(summary.truncated),
        },
        fingerprint=overall,
        table_digests=digests,
    )
