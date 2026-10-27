"""``python -m app.demo_mode`` — the one operator workflow for a demo deployment.

    python -m app.demo_mode reset    # migrate, identify, wipe, ingest, evaluate, validate
    python -m app.demo_mode reset --generations 1   # rewind to an earlier point of history
    python -m app.demo_mode status   # what the configured demo is connected to; changes nothing
    python -m app.demo_mode scan     # apply the dataset's next synthetic scan generation
    python -m app.demo_mode verify   # re-run the validation against the current database

The dataset is Genesee Press (``genesee-press-v1``) unless ``ADG_DEMO_DATASET`` names
another registered one. This is the only reset there is: ``app.demo.genesee_press``
generates the estate and has no database command of its own.

Every command reads the same environment the API reads (``ADG_RUNTIME_MODE``,
``ADG_DATABASE_URL``, ``ADG_DEMO_DATASET``), so the command and the deployment cannot
disagree about which database is the demo. Every command refuses to run unless
``ADG_RUNTIME_MODE=demo``; ``reset`` additionally refuses any database that is not marked
demo or empty (see :mod:`app.demo_mode.marker`).

Exit codes: ``0`` done, ``1`` failed or refused (the message says which), ``2`` usage.

Standard output carries the report and nothing else, so ``--json`` is machine-readable: the
application's log lines -- the in-process ingestion builds the application, which installs
its log handler on standard output as a served API does -- go to standard error while a
command runs.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import pathlib
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.db import Database
from app.demo.seed import SeedRunReport
from app.demo_mode.datasets import DatasetUnavailable, load_dataset
from app.demo_mode.lifecycle import (
    DemoLifecycleError,
    ResetReport,
    demo_status,
    reset_demo,
    run_synthetic_scan,
    verify_demo,
)
from app.demo_mode.marker import DemoTargetRefused
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
    reset.add_argument(
        "--generations",
        type=int,
        metavar="N",
        help=(
            "Apply only the dataset's first N scan generations -- an earlier point of its "
            "history -- so `scan` can replay the rest. Default: the canonical baseline."
        ),
    )
    reset.add_argument(
        "--report", type=pathlib.Path, help="Also write the reset report as JSON to this path."
    )
    commands.add_parser("status", help="Report the demo's dataset, database and generations.")
    commands.add_parser("scan", help="Apply the dataset's next synthetic scan generation.")
    commands.add_parser("verify", help="Check the canonical probes and print the fingerprint.")
    return parser


async def _scan(settings: Settings) -> dict[str, object]:
    dataset = load_dataset(settings.resolved_demo_dataset)
    database = Database(settings)
    try:
        report = await run_synthetic_scan(settings, database, dataset)
    finally:
        await database.dispose()
    return {
        "generation": report.generation.name,
        "runs": len(report.seed.runs),
        "recorded_statuses": _statuses(report.seed.runs),
        "observations_applied": report.seed.observations_applied,
        "risk": report.risk.as_dict(),
        "fingerprint": report.fingerprint,
    }


def _statuses(runs: Sequence[SeedRunReport]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for run in runs:
        counted[run.recorded_status] = counted.get(run.recorded_status, 0) + 1
    return dict(sorted(counted.items()))


def _reset_text(report: ResetReport) -> str:
    seed = report.seed
    applied = ", ".join(item.name for item in report.applied)
    beyond = len(report.applied) - report.baseline
    if beyond < 0:
        history = f"rewound: {len(report.applied)} of {report.baseline} baseline generations"
    elif beyond == 0:
        history = "canonical baseline"
    else:
        history = f"canonical baseline and {beyond} later generation{'s' if beyond > 1 else ''}"
    lines = [
        f"Demo reset: {report.dataset.company_name} ({report.dataset.key}, seed "
        f"{report.dataset.seed}) into {report.database}"
        f"{' (initialized)' if report.initialized else ''}.",
        f"generations: {applied} ({history})",
        f"runs: {len(seed.runs)} {_statuses(seed.runs)}; "
        f"observations applied: {seed.observations_applied}",
    ]
    lines += [
        f"  {run.name}: {run.recorded_status}"
        for run in seed.runs
        if run.recorded_status != "succeeded"
    ]
    if report.probes_checked:
        present = sum(result.present for result in report.probes)
        lines.append(f"probes: {present}/{len(report.probes)} present")
    else:
        lines.append("probes: not checked until the baseline is complete (`scan` forward)")
    risk = report.risk.as_dict()
    dated = f", dated {risk['evaluated_at']}" if risk["evaluated_at"] else ""
    incomplete = "" if risk["complete"] else " (INCOMPLETE)"
    lines += [
        f"risk pass: {risk['matched']} matched, {report.risk_findings_open} open"
        f"{incomplete}{dated}",
        f"fingerprint: {report.fingerprint}",
        f"duration: {report.duration_seconds:.1f}s (ingestion {report.ingest_seconds:.1f}s)",
    ]
    return "\n".join(lines)


def _run(args: argparse.Namespace, settings: Settings) -> tuple[dict[str, object], str]:
    """Run one command; return its report as a document and as console text."""
    if args.command == "reset":
        report = asyncio.run(
            reset_demo(settings, migrate=not args.no_migrate, generations=args.generations)
        )
        payload: dict[str, object] = report.as_dict()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload, _reset_text(report)
    if args.command == "status":
        payload = asyncio.run(demo_status(settings, with_fingerprint=True)).as_dict()
    elif args.command == "scan":
        payload = asyncio.run(_scan(settings))
    else:
        payload = asyncio.run(verify_demo(settings))
    return payload, json.dumps(payload, indent=2)


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
        # Log handlers installed while the command runs bind to whatever sys.stdout is when
        # they are created; pointing it at stderr keeps every log line out of the report.
        with contextlib.redirect_stdout(sys.stderr):
            payload, text = _run(args, settings)
    except (DemoLifecycleError, DemoTargetRefused, DatasetUnavailable) as failure:
        print(f"Refused: {failure}", file=sys.stderr)
        return EXIT_FAILED

    print(json.dumps(payload, indent=2) if args.json else text)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
