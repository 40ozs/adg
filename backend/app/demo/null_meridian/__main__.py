"""``python -m app.demo.null_meridian`` — the Null Meridian demo's operator commands.

    python -m app.demo.null_meridian reset [--skip-migrate] [--report PATH]
    python -m app.demo.null_meridian manifest [--out PATH] [--canon-out PATH]
    python -m app.demo.null_meridian transcripts --out DIR
    python -m app.demo.null_meridian stats

``reset`` is the only command that writes to a database, and it refuses to run unless the
deployment is in Demo Mode on a demo database (see :mod:`.reset`). The others are pure: they
build the canon and print or write it.

Exit codes: ``0`` done, ``1`` failed, ``3`` refused because the target is not a demo.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections.abc import Sequence

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.demo.null_meridian",
        description="The Null Meridian synthetic estate behind ADG's Demo Mode.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser(
        "reset", help="Empty the demo database and load the canonical Null Meridian estate."
    )
    reset.add_argument(
        "--skip-migrate", action="store_true", help="Do not run 'alembic upgrade heads' first."
    )
    reset.add_argument(
        "--report", type=pathlib.Path, help="Also write the reset report as JSON to this path."
    )
    manifest = commands.add_parser(
        "manifest", help="Print or write the canonical manifest (shared with OGWA)."
    )
    manifest.add_argument(
        "--out", type=pathlib.Path, help="Write the summary manifest here instead of printing it."
    )
    manifest.add_argument(
        "--canon-out",
        type=pathlib.Path,
        help="Also write the full identity canon (every employee, group and membership) here.",
    )
    transcripts = commands.add_parser(
        "transcripts", help="Write every generation's collector transcripts as JSON."
    )
    transcripts.add_argument("--out", type=pathlib.Path, required=True)
    commands.add_parser("stats", help="Print the estate's counts and distributions.")
    return parser


def _reset(args: argparse.Namespace) -> int:
    from app.config import get_settings
    from app.demo.null_meridian.reset import DemoResetRefused, reset_demo
    from app.logging_config import configure_logging

    try:
        settings = get_settings()
    except ValueError as error:
        print(
            f"Refused: the configuration is not a valid demo deployment.\n{error}", file=sys.stderr
        )
        return EXIT_REFUSED
    configure_logging(
        level="WARNING",
        log_format="text",
        service="adg-demo-reset",
        environment=settings.environment,
        version="",
    )
    try:
        report = asyncio.run(reset_demo(settings, migrate=not args.skip_migrate))
    except DemoResetRefused as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    document = report.as_document()
    rendered = json.dumps(document, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return EXIT_OK


def _manifest(args: argparse.Namespace) -> int:
    from app.demo.null_meridian.manifest import build_manifest, render_canon_document

    rendered = json.dumps(build_manifest(), indent=1, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(rendered)
    if args.canon_out:
        args.canon_out.parent.mkdir(parents=True, exist_ok=True)
        args.canon_out.write_text(render_canon_document(), encoding="utf-8")
        print(f"Wrote {args.canon_out}")
    return EXIT_OK


def _transcripts(args: argparse.Namespace) -> int:
    from app.demo.null_meridian.generations import build_generations

    args.out.mkdir(parents=True, exist_ok=True)
    index = 0
    for _, transcripts in build_generations():
        for transcript in transcripts:
            index += 1
            path = args.out / f"{index:03d}-{transcript.name}.json"
            path.write_text(json.dumps(transcript.as_document(), indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {index} transcripts to {args.out}")
    return EXIT_OK


def _stats() -> int:
    from app.demo.null_meridian.manifest import build_manifest

    print(json.dumps(build_manifest()["statistics"], indent=2))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "reset":
        return _reset(args)
    if args.command == "manifest":
        return _manifest(args)
    if args.command == "transcripts":
        return _transcripts(args)
    return _stats()


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
