"""Command-line entry point: ``python -m app.validation <paths...>``.

Exit codes are the interface, because this is meant to be a gate in a collector's own
build:

* ``0`` — no errors. Warnings and informational findings may still be present, and are
  printed, because they are the ones about the directory rather than the collector.
* ``1`` — at least one error: the API would reject this output.
* ``2`` — the arguments were unusable (nothing to read).

``--strict`` promotes warnings to a non-zero exit, for a pipeline that wants a clean
directory as well as a correct collector.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

from app.validation.collector_output import Report, validate_paths

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.validation",
        description=(
            "Validate ADG collector output: scan-run transcripts, observation batches, or "
            "whole directories of them. Checks what the API would check, then checks the "
            "identity graph the observations describe."
        ),
        epilog=(
            "Exit 0 when the API would accept every document, 1 when it would not, "
            "2 when there was nothing to read."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="JSON files or directories containing them.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the report as JSON instead of text.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on warnings as well as errors.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print findings only; suppress the summary header.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    missing = [path for path in arguments.paths if not path.exists()]
    if missing:
        for path in missing:
            print(f"No such file or directory: {path}", file=sys.stderr)
        return EXIT_USAGE

    report: Report = validate_paths(arguments.paths)

    if report.documents == 0:
        print("No JSON documents were found in the given paths.", file=sys.stderr)
        return EXIT_USAGE

    if arguments.as_json:
        print(json.dumps(report.to_json(), indent=2))
    elif arguments.quiet:
        for finding in report.sorted_findings():
            print(finding.rendered())
    else:
        print(report.rendered())

    if report.errors:
        return EXIT_FINDINGS
    if arguments.strict and report.warnings:
        return EXIT_FINDINGS
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
