"""``python -m app.demo.genesee_press`` — developer utilities for the Genesee Press estate.

    python -m app.demo.genesee_press manifest [--out PATH]
    python -m app.demo.genesee_press transcripts --out DIR
    python -m app.demo.genesee_press stats

Every command is pure: it reads the vendored projection, builds the estate and prints or
writes it, and none of them reads configuration or opens a database. ``manifest`` is how the
committed ``docs/demo/genesee-press.manifest.json`` is regenerated. The vendored projection
itself changes only through ``scripts/vendor-synthetic-enterprise.py``.

Loading Genesee Press into a demo database is not done here. It is Demo Mode's job, behind
Demo Mode's safety checks: ``python -m app.demo_mode reset`` (see docs/architecture/demo-mode.md).

Exit codes: ``0`` done, ``2`` usage.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections.abc import Sequence

EXIT_OK = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.demo.genesee_press",
        description=(
            "Developer utilities for the Genesee Press estate. To load it into a demo "
            "database, use `python -m app.demo_mode reset`."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", help="Print or write the estate's manifest.")
    manifest.add_argument(
        "--out", type=pathlib.Path, help="Write the summary manifest here instead of printing it."
    )
    transcripts = commands.add_parser(
        "transcripts", help="Write every generation's collector transcripts as JSON."
    )
    transcripts.add_argument("--out", type=pathlib.Path, required=True)
    commands.add_parser("stats", help="Print the estate's counts and distributions.")
    return parser


def _manifest(args: argparse.Namespace) -> int:
    from app.demo.genesee_press.manifest import build_manifest

    rendered = json.dumps(build_manifest(), indent=1, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(rendered)
    return EXIT_OK


def _transcripts(args: argparse.Namespace) -> int:
    from app.demo.genesee_press.generations import build_generations

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
    from app.demo.genesee_press.manifest import build_manifest

    print(json.dumps(build_manifest()["statistics"], indent=2))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "manifest":
        return _manifest(args)
    if args.command == "transcripts":
        return _transcripts(args)
    return _stats()


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
