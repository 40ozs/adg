r"""Initializing, resetting, scanning and verifying a demo deployment.

One operator command does the whole cycle (``python -m app.demo_mode reset``):

1. **Refuse unless demo.** ``ADG_RUNTIME_MODE=demo`` and a demo-named database are startup
   conditions of :class:`~app.config.Settings`; the synthetic provider checks the mode again.
2. **Load the dataset**, before touching the database. An unavailable dataset is a refusal,
   never a substitution.
3. **Migrate** the database to the current schema (``alembic upgrade heads``), the same
   command an operator runs for production.
4. **Identify the database** (:mod:`app.demo_mode.marker`): marked demo, or empty and about
   to be marked. Anything else is refused and nothing has been written.
5. **Wipe demo-owned data**: every ADG table except the marker, in the transaction that
   re-locks and re-checks the marker. In a demo database every row is demo-owned.
6. **Ingest the baseline generation** through the real ingestion routes
   (:mod:`app.demo_mode.ingest`). History, reconciliation and current state are derived by
   ingestion itself, as they are for a collector.
7. **Evaluate the risk rules** with the operator command production uses
   (:func:`app.operations.commands.evaluate_risks`).
8. **Validate**: every run recorded with the status its transcript claimed, every canonical
   probe present in current state, and a fingerprint of the canonical state reported so two
   resets can be compared.

The fingerprint is the determinism check. It is a SHA-256 over current state, the version
history and the run outcomes, read in a fixed order and excluding only values that are
*about this process* rather than about the dataset: wall-clock bookkeeping timestamps and
database-generated serials. Every identifier, every relationship and every observed instant
the dataset supplied is in it, so a reset that altered any of them changes the fingerprint.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.contracts.v1.common import ObservationKind
from app.db import Database
from app.demo.seed import SeedReport
from app.demo_mode.datasets import (
    CanonicalProbe,
    DatasetUnavailable,
    DemoDatasetMetadata,
    SyntheticDataset,
    load_dataset,
)
from app.demo_mode.ingest import apply_generation
from app.demo_mode.marker import (
    DemoTargetRefused,
    Marker,
    TargetInspection,
    inspect_target,
    lock_and_require_demo,
    require_demo_database,
    write_marker,
)
from app.models.current import CURRENT_STATE
from app.models.schema import (
    deployment_marker,
    metadata,
    object_versions,
    risk_findings,
    scan_run_errors,
    scan_run_scopes,
    scan_runs,
    servers,
)
from app.runtime_mode import RuntimeMode

__all__ = [
    "DemoLifecycleError",
    "GenerationState",
    "ProbeResult",
    "ResetReport",
    "ScanReport",
    "StatusReport",
    "canonical_fingerprint",
    "check_probes",
    "demo_status",
    "generation_states",
    "reset_demo",
    "run_synthetic_scan",
    "verify_demo_startup",
]

BACKEND_ROOT: Final = Path(__file__).resolve().parents[2]


class DemoLifecycleError(RuntimeError):
    """A demo lifecycle step failed. The message says which and why."""


# ---------------------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeResult:
    probe: CanonicalProbe
    present: bool


@dataclass(frozen=True, slots=True)
class GenerationState:
    index: int
    name: str
    description: str
    applied: bool
    run_ids: tuple[str, ...]


@dataclass(slots=True)
class ResetReport:
    dataset: DemoDatasetMetadata
    database: str
    initialized: bool
    seed: SeedReport
    probes: list[ProbeResult]
    fingerprint: str
    counts: dict[str, int]
    risk_findings_open: int
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return all(result.present for result in self.probes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dataset": _metadata_dict(self.dataset),
            "database": self.database,
            "initialized": self.initialized,
            "runs": [
                {
                    "name": run.name,
                    "run_id": run.run_id,
                    "collector": run.collector,
                    "claimed_status": run.status,
                    "recorded_status": run.recorded_status,
                    "observations_applied": run.observations_applied,
                }
                for run in self.seed.runs
            ],
            "probes": [
                {"kind": r.probe.kind, "description": r.probe.description, "present": r.present}
                for r in self.probes
            ],
            "fingerprint": self.fingerprint,
            "counts": self.counts,
            "risk_findings_open": self.risk_findings_open,
            "duration_seconds": round(self.duration_seconds, 3),
        }


@dataclass(slots=True)
class StatusReport:
    runtime_mode: RuntimeMode
    dataset_key: str
    dataset: DemoDatasetMetadata | None
    dataset_error: str | None
    inspection: TargetInspection | None
    target_error: str | None
    generations: list[GenerationState] = field(default_factory=list)
    fingerprint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        marker = self.inspection.marker if self.inspection else None
        return {
            "runtime_mode": self.runtime_mode.value,
            "dataset_key": self.dataset_key,
            "dataset": _metadata_dict(self.dataset) if self.dataset else None,
            "dataset_error": self.dataset_error,
            "database": self.inspection.database_name if self.inspection else None,
            "marker": _marker_dict(marker) if marker else None,
            "target_error": self.target_error,
            "generations": [
                {"index": g.index, "name": g.name, "applied": g.applied} for g in self.generations
            ],
            "fingerprint": self.fingerprint,
        }


@dataclass(slots=True)
class ScanReport:
    generation: GenerationState
    seed: SeedReport
    fingerprint: str


# ---------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------


async def reset_demo(
    settings: Settings,
    *,
    migrate: bool = True,
    marked_by: str = "python -m app.demo_mode reset",
    database: Database | None = None,
) -> ResetReport:
    """Restore the demo database to the dataset's canonical baseline. See the module docstring.

    Raises:
        DemoLifecycleError: not demo mode, the dataset is unavailable, migration failed, or
            validation failed after ingestion.
        DemoTargetRefused: the database is not positively a demo database. Nothing written.
    """
    started = time.perf_counter()
    _require_demo(settings)
    dataset = _load(settings)
    generations = dataset.generations()
    if not generations:
        raise DemoLifecycleError(f"Dataset {dataset.metadata.key!r} has no baseline generation.")

    if migrate:
        _migrate(settings)

    owned = database is None
    db = database or Database(settings)
    try:
        async with db.engine.connect() as connection:
            inspection = await inspect_target(connection)
        require_demo_database(inspection, allow_initialize=True)
        initialized = inspection.marker is None
        if not inspection.schema_present:
            raise DemoLifecycleError(
                f"Database {inspection.database_name!r} has no ADG schema; run the reset "
                "without --no-migrate, or `alembic upgrade heads`, first."
            )

        async with db.engine.begin() as connection:
            await write_marker(connection, dataset.metadata, marked_by=marked_by)
            await lock_and_require_demo(connection)
            await _wipe_demo_owned(connection)

        seed = await apply_generation(settings, db, generations[0])
        _require_recorded_as_claimed(seed)

        # The production operator command, unchanged: the risk report a demo shows is the
        # one the rules produce, not one written for it.
        from app.operations.commands import evaluate_risks

        await evaluate_risks(db, settings)

        async with db.engine.connect() as connection:
            probes = await check_probes(connection, dataset.probes())
            fingerprint = await canonical_fingerprint(connection)
            counts = await _counts(connection)
            open_findings = int(
                (
                    await connection.execute(
                        sa.select(sa.func.count())
                        .select_from(risk_findings)
                        .where(risk_findings.c.status == "open")
                    )
                ).scalar_one()
            )
    finally:
        if owned:
            await db.dispose()

    report = ResetReport(
        dataset=dataset.metadata,
        database=inspection.database_name,
        initialized=initialized,
        seed=seed,
        probes=probes,
        fingerprint=fingerprint,
        counts=counts,
        risk_findings_open=open_findings,
        duration_seconds=time.perf_counter() - started,
    )
    if not report.ok:
        missing = ", ".join(r.probe.description for r in report.probes if not r.present)
        raise DemoLifecycleError(
            f"The dataset was ingested but these canonical objects are not in current state: "
            f"{missing}. Something between the generator and the tables lost them."
        )
    return report


async def run_synthetic_scan(
    settings: Settings, database: Database, dataset: SyntheticDataset
) -> ScanReport:
    """Apply the dataset's next unapplied scan generation through the synthetic provider.

    Raises:
        DemoLifecycleError: the baseline is not applied (reset first), or every generation
            the dataset defines has already been applied.
    """
    _require_demo(settings)
    async with database.engine.connect() as connection:
        require_demo_database(await inspect_target(connection), allow_initialize=False)
        states = await generation_states(connection, dataset)
    if not states or not states[0].applied:
        raise DemoLifecycleError(
            "The baseline generation has not been applied. Run `python -m app.demo_mode reset`."
        )
    pending = [state for state in states if not state.applied]
    if not pending:
        raise DemoLifecycleError(
            f"Every scan generation dataset {dataset.metadata.key!r} defines "
            f"({len(states)}) has been applied. Reset the demo to replay its history."
        )
    target = pending[0]
    seed = await apply_generation(settings, database, dataset.generations()[target.index])
    _require_recorded_as_claimed(seed)
    async with database.engine.connect() as connection:
        fingerprint = await canonical_fingerprint(connection)
    applied = GenerationState(target.index, target.name, target.description, True, target.run_ids)
    return ScanReport(generation=applied, seed=seed, fingerprint=fingerprint)


async def demo_status(settings: Settings, *, with_fingerprint: bool = False) -> StatusReport:
    """What a demo deployment is connected to, read without changing anything."""
    report = StatusReport(
        runtime_mode=settings.runtime_mode,
        dataset_key=settings.resolved_demo_dataset,
        dataset=None,
        dataset_error=None,
        inspection=None,
        target_error=None,
    )
    dataset: SyntheticDataset | None = None
    try:
        dataset = load_dataset(settings.resolved_demo_dataset)
        report.dataset = dataset.metadata
    except DatasetUnavailable as unavailable:
        report.dataset_error = str(unavailable)

    database = Database(settings)
    try:
        async with database.engine.connect() as connection:
            report.inspection = await inspect_target(connection)
            try:
                require_demo_database(report.inspection, allow_initialize=False)
            except DemoTargetRefused as refused:
                report.target_error = str(refused)
            if report.target_error is None and dataset is not None:
                report.generations = await generation_states(connection, dataset)
                if with_fingerprint:
                    report.fingerprint = await canonical_fingerprint(connection)
    except sa.exc.SQLAlchemyError as failure:
        report.target_error = f"The database could not be read: {type(failure).__name__}."
    finally:
        await database.dispose()
    return report


async def verify_demo_startup(settings: Settings, database: Database) -> DemoDatasetMetadata:
    """The demo API's startup check: the dataset loads and the database is marked demo.

    Called from the application lifespan, so a failure stops the process before it serves a
    single request -- the same posture as every other startup refusal in :mod:`app.config`.

    Raises:
        DatasetUnavailable, DemoTargetRefused.
    """
    dataset = load_dataset(settings.resolved_demo_dataset)
    async with database.engine.connect() as connection:
        inspection = await inspect_target(connection)
    require_demo_database(inspection, allow_initialize=False)
    marker = inspection.marker
    if marker is not None and marker.dataset_key != dataset.metadata.key:
        raise DemoTargetRefused(
            f"Database {inspection.database_name!r} holds dataset {marker.dataset_key!r} but "
            f"ADG_DEMO_DATASET selects {dataset.metadata.key!r}. Reset the demo to switch."
        )
    return dataset.metadata


# ---------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------


async def generation_states(
    connection: AsyncConnection, dataset: SyntheticDataset
) -> list[GenerationState]:
    """Which of the dataset's generations are applied: all of their run ids are recorded."""
    recorded = {
        str(run_id)
        for run_id in (await connection.execute(sa.select(scan_runs.c.run_id))).scalars()
    }
    return [
        GenerationState(
            index=index,
            name=generation.name,
            description=generation.description,
            applied=bool(generation.run_ids) and set(generation.run_ids) <= recorded,
            run_ids=generation.run_ids,
        )
        for index, generation in enumerate(dataset.generations())
    ]


async def check_probes(
    connection: AsyncConnection, probes: Sequence[CanonicalProbe]
) -> list[ProbeResult]:
    """Look each probe up in *current* state -- the views every query-side reader uses."""
    return [ProbeResult(probe, await _probe_present(connection, probe)) for probe in probes]


async def _probe_present(connection: AsyncConnection, probe: CanonicalProbe) -> bool:
    principals = CURRENT_STATE[ObservationKind.PRINCIPAL]
    edges = CURRENT_STATE[ObservationKind.MEMBERSHIP_EDGE]
    shares = CURRENT_STATE[ObservationKind.SMB_SHARE]
    share_aces = CURRENT_STATE[ObservationKind.SMB_ACE]
    resources = CURRENT_STATE[ObservationKind.NTFS_RESOURCE]
    ntfs_aces = CURRENT_STATE[ObservationKind.NTFS_ACE]
    key = probe.key

    def folded(column: Any, value: str) -> Any:
        return sa.func.lower(column) == value.casefold()

    statement: sa.Select[Any]
    match probe.kind:
        case "principal":
            statement = sa.select(principals.c.sid).where(principals.c.sid == key["sid"])
        case "membership":
            statement = sa.select(edges.c.edge_key).where(
                edges.c.group_sid == key["group_sid"], edges.c.member_sid == key["member_sid"]
            )
        case "share" | "share_ace":
            statement = (
                sa.select(shares.c.share_key)
                .join(servers, servers.c.server_key == shares.c.server_key)
                .where(folded(servers.c.name, key["server"]), folded(shares.c.name, key["share"]))
            )
            if probe.kind == "share_ace":
                statement = statement.join(
                    share_aces, share_aces.c.share_key == shares.c.share_key
                ).where(share_aces.c.trustee_sid == key["trustee_sid"])
        case "ntfs_resource" | "ntfs_ace":
            statement = sa.select(resources.c.resource_key).where(
                folded(resources.c.path, key["path"])
            )
            if probe.kind == "ntfs_ace":
                statement = statement.join(
                    ntfs_aces, ntfs_aces.c.resource_key == resources.c.resource_key
                ).where(ntfs_aces.c.trustee_sid == key["trustee_sid"])
        case _:  # pragma: no cover - ProbeKind is a closed Literal
            raise DemoLifecycleError(f"Unknown probe kind {probe.kind!r}.")
    return (await connection.execute(statement.limit(1))).first() is not None


#: Columns that describe this process rather than the dataset: when ADG received or
#: evaluated something on the wall clock, and serials the database assigned. Everything else
#: -- every key, identifier, relationship, hash, status, count, and every instant the dataset
#: itself reported -- is fingerprinted.
VOLATILE_COLUMNS: Final = frozenset(
    {
        # Row bookkeeping: when this database wrote the row.
        "created_at",
        "updated_at",
        # Risk evaluation runs on the wall clock and numbers its evaluations serially.
        "first_detected_at",
        "detected_at",
        "last_evaluated_at",
        "resolved_at",
        "first_evaluation_id",
        "last_evaluation_id",
        "resolved_evaluation_id",
        # Database serials.
        "id",
    }
)


async def canonical_fingerprint(connection: AsyncConnection) -> str:
    """SHA-256 over canonical demo state. Equal fingerprints mean equal canonical state."""
    sources: list[tuple[str, sa.FromClause]] = [
        *((f"current:{kind.value}", subquery) for kind, subquery in CURRENT_STATE.items()),
        ("object_versions", object_versions),
        ("scan_runs", scan_runs),
        ("scan_run_scopes", scan_run_scopes),
        ("scan_run_errors", scan_run_errors),
        ("risk_findings", risk_findings),
    ]
    digest = hashlib.sha256()
    for name, source in sources:
        columns = sorted(
            (column for column in source.columns if column.name not in VOLATILE_COLUMNS),
            key=lambda column: column.name,
        )
        rows = (await connection.execute(sa.select(*columns))).all()
        encoded = sorted(json.dumps(list(row), default=str, sort_keys=True) for row in rows)
        digest.update(f"{name}|{','.join(c.name for c in columns)}|{len(encoded)}\n".encode())
        for line in encoded:
            digest.update(line.encode())
            digest.update(b"\n")
    return digest.hexdigest()


async def _counts(connection: AsyncConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind, subquery in CURRENT_STATE.items():
        counts[f"current_{kind.value}"] = int(
            (
                await connection.execute(sa.select(sa.func.count()).select_from(subquery))
            ).scalar_one()
        )
    for table in (object_versions, scan_runs):
        counts[table.name] = int(
            (await connection.execute(sa.select(sa.func.count()).select_from(table))).scalar_one()
        )
    counts["tombstones_open"] = int(
        (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(object_versions)
                .where(
                    object_versions.c.valid_to.is_(None), object_versions.c.is_present.is_(False)
                )
            )
        ).scalar_one()
    )
    return counts


# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------


def _require_demo(settings: Settings) -> None:
    if not settings.is_demo:
        raise DemoLifecycleError(
            "Demo lifecycle commands run only with ADG_RUNTIME_MODE=demo. This configuration is "
            f"{settings.runtime_mode.value}; nothing was done."
        )


def _load(settings: Settings) -> SyntheticDataset:
    try:
        return load_dataset(settings.resolved_demo_dataset)
    except DatasetUnavailable as unavailable:
        raise DemoLifecycleError(str(unavailable)) from unavailable


def _migrate(settings: Settings) -> None:
    """``alembic upgrade heads`` against the demo database, as an operator would run it."""
    environment = {**os.environ, "ADG_DATABASE_URL": settings.database_url}
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "heads"],
        cwd=_alembic_root(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DemoLifecycleError(
            "Migrating the demo database failed:\n" + (completed.stderr or completed.stdout)[-2000:]
        )


def _alembic_root() -> Path:
    """The directory holding ``alembic.ini``: the source tree, or the working directory.

    The container image installs the package *and* keeps the source at /app/backend, which
    is its working directory; an installed copy alone has no ``alembic.ini`` beside it.
    """
    for candidate in (BACKEND_ROOT, Path.cwd()):
        if (candidate / "alembic.ini").is_file():
            return candidate
    raise DemoLifecycleError(
        "alembic.ini was not found beside the application or in the working directory. Run "
        "the reset from the backend directory, or migrate first and pass --no-migrate."
    )


async def _wipe_demo_owned(connection: AsyncConnection) -> None:
    """Empty every ADG table but the marker. Only ever called after the marker is locked."""
    names = ", ".join(
        f'"{table.name}"' for table in metadata.sorted_tables if table is not deployment_marker
    )
    await connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def _require_recorded_as_claimed(seed: SeedReport) -> None:
    wrong = [
        f"{run.name}: claimed {run.status}, recorded {run.recorded_status}"
        for run in seed.runs
        if run.recorded_status != run.status
    ]
    if wrong:
        raise DemoLifecycleError(
            "Ingestion recorded runs differently from what the dataset claimed: " + "; ".join(wrong)
        )


def _metadata_dict(metadata_value: DemoDatasetMetadata) -> dict[str, Any]:
    return {
        "key": metadata_value.key,
        "company_name": metadata_value.company_name,
        "domain": metadata_value.domain,
        "snapshot_date": metadata_value.snapshot_date.isoformat(),
        "seed": metadata_value.seed,
        "synthetic": metadata_value.synthetic,
        "description": metadata_value.description,
        "facts": dict(metadata_value.facts),
    }


def _marker_dict(marker: Marker) -> dict[str, Any]:
    return {
        "runtime_mode": marker.runtime_mode,
        "dataset_key": marker.dataset_key,
        "dataset_seed": marker.dataset_seed,
        "marked_at": marker.marked_at.astimezone(dt.UTC).isoformat(),
        "marked_by": marker.marked_by,
    }
