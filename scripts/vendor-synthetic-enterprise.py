#!/usr/bin/env python3
r"""Vendor the ``adg`` projection of the shared synthetic world into this repository.

    python scripts/vendor-synthetic-enterprise.py --source PATH_TO_SYNTHETIC_ENTERPRISE

Copies ``snapshots/<snapshot>/manifest.json`` and ``snapshots/<snapshot>/projections/adg/``
out of a local ``40ozs/synthetic-enterprise`` checkout into
``backend/vendor/synthetic-enterprise/`` and rewrites ``SYNTHETIC_ENTERPRISE.lock`` with the
checkout's commit (PROJECTION-CONTRACT §1). The checkout must be clean, so the lock names
exactly what was copied. Nothing is fetched: check out the commit to pin first.

Then run the backend tests: every value that moved shows up there, and
``python -m app.demo.genesee_press manifest --out ../docs/demo/genesee-press.manifest.json``
(from ``backend/``) regenerates the committed manifest. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
VENDOR_DIR = REPOSITORY / "backend" / "vendor" / "synthetic-enterprise"
LOCK_NAME = "SYNTHETIC_ENTERPRISE.lock"
PROJECTION = "adg"
#: Must equal ``app.demo.genesee_press.vendor.PROJECTION_VERSION``; a new version is a code
#: change there first.
PROJECTION_VERSION = "0.3.0"


def _git(source: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vendor(source: pathlib.Path, snapshot: str) -> dict[str, str]:
    if _git(source, "status", "--porcelain"):
        raise SystemExit(f"{source} has uncommitted changes; vendor from a clean checkout.")
    commit = _git(source, "rev-parse", "HEAD")
    origin = source / "snapshots" / snapshot
    manifest = json.loads((origin / "manifest.json").read_text(encoding="utf-8"))
    header = json.loads(
        (origin / "projections" / PROJECTION / "projection.json").read_text(encoding="utf-8")
    )
    if header["projection_version"] != PROJECTION_VERSION:
        raise SystemExit(
            f"{source} has {PROJECTION}@{header['projection_version']}; ADG reads "
            f"{PROJECTION_VERSION}."
        )
    if VENDOR_DIR.exists():
        shutil.rmtree(VENDOR_DIR)
    target = VENDOR_DIR / snapshot
    (target / "projections").mkdir(parents=True)
    shutil.copy2(origin / "manifest.json", target / "manifest.json")
    shutil.copytree(origin / "projections" / PROJECTION, target / "projections" / PROJECTION)
    document = {
        "repository": "40ozs/synthetic-enterprise",
        "commit": commit,
        "snapshot": snapshot,
        "contract_version": header["contract_version"],
        "dataset": header["dataset"],
        "projection": f"{PROJECTION}@{header['projection_version']}",
        "manifest_sha256": _sha256(target / "manifest.json"),
        "manifest_source_commit": manifest["source"]["commit"],
    }
    (VENDOR_DIR / LOCK_NAME).write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=pathlib.Path, required=True)
    parser.add_argument("--snapshot", default="standard-baseline")
    args = parser.parse_args(argv)
    print(json.dumps(vendor(args.source.resolve(), args.snapshot), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
