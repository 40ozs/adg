"""``python -m app.operations`` — the two things an operator runs on a schedule.

    python -m app.operations evaluate-risks
    python -m app.operations drain-alerts [--loop --interval 60]

Both connect with ``ADG_DATABASE_URL`` from the environment, the same setting the API uses,
and both read the same risk configuration and alert policy the API reads. There is no
separate configuration for a scheduled pass: an installation whose report and whose cron job
disagreed about which rules are enabled would be a great deal worse than one with no cron job.

The exit code is what a scheduler branches on:

``0``
    The command did what it set out to do.
``1``
    It failed. The message says what.
``2``
    It succeeded and something needs a person: a risk pass that could not cover the estate,
    or a drain that abandoned a delivery. Both are cases where the normal reading of "exit 0"
    — *this is fine* — would be wrong, and both are invisible in the output of a job nobody
    reads unless the exit code says otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app import __version__
from app.config import get_settings
from app.db import Database
from app.logging_config import configure_logging
from app.operations.commands import drain_alerts, evaluate_risks

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NEEDS_ATTENTION = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.operations",
        description="ADG operator commands: evaluate the risk rules, drain the alert queue.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "evaluate-risks",
        help="Run every enabled rule over the whole estate and alert on what opened.",
    )

    drain = commands.add_parser("drain-alerts", help="Attempt the alert deliveries that are due.")
    drain.add_argument(
        "--loop",
        action="store_true",
        help="Keep draining on an interval instead of running once.",
    )
    drain.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between passes when looping (default: %(default)s).",
    )
    drain.add_argument(
        "--max-passes",
        type=int,
        default=20,
        help="Bounded passes per run (default: %(default)s).",
    )
    return parser


async def _evaluate() -> int:
    settings = get_settings()
    database = Database(settings)
    try:
        summary = await evaluate_risks(database, settings)
    finally:
        await database.dispose()
    for line in summary.lines:
        print(line)
    # A pass that could not cover the estate exits 2. It did not fail -- what it found is
    # real -- but its counts are not totals and nothing outside it was resolved, and a
    # scheduler that treated that as an ordinary success would report a shrinking estate as
    # an improving one.
    return EXIT_NEEDS_ATTENTION if not summary.complete else EXIT_OK


async def _drain(*, loop: bool, interval: int, max_passes: int) -> int:
    settings = get_settings()
    database = Database(settings)
    worst = EXIT_OK
    try:
        while True:
            summary = await drain_alerts(database, settings, max_passes=max_passes)
            for line in summary.lines:
                print(line, flush=True)
            if summary.abandoned:
                worst = EXIT_NEEDS_ATTENTION
            if not loop:
                break
            await asyncio.sleep(interval)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("Stopped.")
    finally:
        await database.dispose()
    return worst


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        service=settings.app_name,
        environment=settings.environment,
        version=__version__,
    )
    try:
        if args.command == "evaluate-risks":
            return asyncio.run(_evaluate())
        return asyncio.run(
            _drain(loop=args.loop, interval=args.interval, max_passes=args.max_passes)
        )
    except Exception as error:
        print(f"Failed: {error}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
