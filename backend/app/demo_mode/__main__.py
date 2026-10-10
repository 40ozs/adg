"""``python -m app.demo_mode`` — the one operator workflow for a demo deployment.

    python -m app.demo_mode reset    # migrate, identify, wipe, ingest, evaluate, validate
    python -m app.demo_mode status   # what the configured demo is connected to; changes nothing
    python -m app.demo_mode scan     # apply the dataset's next synthetic scan generation
    python -m app.demo_mode verify   # re-run the validation against the current database

Every command reads the same environment the API reads (``ADG_RUNTIME_MODE``,
``ADG_DATABASE_URL``, ``ADG_DEMO_DATASET``), so the command and the deployment cannot
disagree about which database is the demo. Every command refuses to run unless
``ADG_RUNTIME_MODE=demo``; ``reset`` additionally refuses any database that is not marked
demo or empty (see :mod:`app.demo_mode.marker`).

Exit codes: ``0`` done, ``1`` failed or refused (the message says which), ``2`` usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.db import Database
from app.demo_mode.datasets import DatasetUnavailable, load_dataset
from app.demo_mode.lifecycle import (
    DemoLifecycleError,
    canonical_fingerprint,
    check_probes,
    demo_status,
    reset_demo,
    run_synthetic_scan,
)
from app.demo_mode.marker import DemoTargetRefused, inspect_target, require_demo_database
from app.logging_config import configure_logging

EXIT_OK = 0
EXIT_FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.demo_mode",
        description="Operate an ADG demo deployment (ADG_RUNTIME_MODE=demo).",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser("reset", help="Restore the demo to its canonical baseline.")
    reset.add_argument(
        "--no-migrate",
        action="store_true",
        help="Skip `alembic upgrade heads` (the schema must already be current).",
    )
    commands.add_parser("status", help="Report the demo's dataset, database and generations.")
    commands.add_parser("scan", help="Apply the dataset's next synthetic scan generation.")
    commands.add_parser("verify", help="Check the canonical probes and print the fingerprint.")
    return parser


async def _verify(settings: Settings) -> dict[str, object]:
    dataset = load_dataset(settings.resolved_demo_dataset)
    database = Database(settings)
    try:
        async with database.engine.connect() as connection:
            require_demo_database(await inspect_target(connection), allow_initialize=False)
            probes = await check_probes(connection, dataset.probes())
            fingerprint = await canonical_fingerprint(connection)
    finally:
        await database.dispose()
    missing = [result.probe.description for result in probes if not result.present]
    if missing:
        raise DemoLifecycleError(
            "Canonical objects missing from current state: " + ", ".join(missing)
        )
    return {"dataset": dataset.metadata.key, "probes": len(probes), "fingerprint": fingerprint}


async def _scan(settings: Settings) -> dict[str, object]:
    dataset = load_dataset(settings.resolved_demo_dataset)
    database = Database(settings)
    try:
        report = await run_synthetic_scan(settings, database, dataset)
    finally:
        await database.dispose()
    return {
        "generation": report.generation.name,
        "runs": [run.name for run in report.seed.runs],
        "observations_applied": report.seed.observations_applied,
        "fingerprint": report.fingerprint,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
    except ValidationError as invalid:
        print(f"Configuration refused:\n{invalid}", file=sys.stderr)
        return EXIT_FAILED
    configure_logging(
        level="WARNING",
        log_format=settings.log_format,
        service=settings.app_name,
        environment=settings.environment,
        version="demo-mode",
    )
    # The in-process ingestion builds the application, which configures logging from the
    # settings; one INFO line per synthetic request is noise on an operator's console.
    settings = settings.model_copy(update={"log_level": "WARNING"})
    if not settings.is_demo:
        print(
            "Refused: ADG_RUNTIME_MODE is "
            f"{settings.runtime_mode.value}. Demo commands run only with ADG_RUNTIME_MODE=demo.",
            file=sys.stderr,
        )
        return EXIT_FAILED

    try:
        if args.command == "reset":
            report = asyncio.run(reset_demo(settings, migrate=not args.no_migrate))
            payload: dict[str, object] = report.as_dict()
            text = (
                f"Demo reset: {report.dataset.company_name} ({report.dataset.key}, seed "
                f"{report.dataset.seed}) into {report.database}"
                f"{' (initialized)' if report.initialized else ''}.\n"
                f"{report.seed.summary()}\n"
                f"probes: {sum(r.present for r in report.probes)}/{len(report.probes)} present\n"
                f"open risk findings: {report.risk_findings_open}\n"
                f"fingerprint: {report.fingerprint}\n"
                f"duration: {report.duration_seconds:.2f}s"
            )
        elif args.command == "status":
            status = asyncio.run(demo_status(settings, with_fingerprint=True))
            payload = status.as_dict()
            text = json.dumps(payload, indent=2)
        elif args.command == "scan":
            payload = asyncio.run(_scan(settings))
            text = json.dumps(payload, indent=2)
        else:
            payload = asyncio.run(_verify(settings))
            text = json.dumps(payload, indent=2)
    except (DemoLifecycleError, DemoTargetRefused, DatasetUnavailable) as failure:
        print(f"Refused: {failure}", file=sys.stderr)
        return EXIT_FAILED

    print(json.dumps(payload, indent=2) if args.json else text)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
