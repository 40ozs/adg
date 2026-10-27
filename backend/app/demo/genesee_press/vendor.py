r"""The vendored ``synthetic-enterprise`` projection Genesee Press is read from.

ADG does not generate its demo directory. Its people, accounts, groups, memberships, hosts,
org units and the organization itself come from the shared synthetic world,
``40ozs/synthetic-enterprise``, as the ``adg`` projection of one pinned snapshot
(PROJECTION-CONTRACT §1). The files are committed under :data:`VENDOR_DIR`:

    backend/vendor/synthetic-enterprise/
        SYNTHETIC_ENTERPRISE.lock          repository, commit, snapshot, versions, manifest digest
        <snapshot>/manifest.json           the snapshot manifest, unchanged
        <snapshot>/projections/adg/*       the ADG projection, unchanged

A running demo reads only these files. Nothing here opens a network connection, and nothing
fetches ``synthetic-enterprise`` at runtime. :func:`verify` checks, before anything is read,
that the manifest is the one the lock pins and that every projection file is the one the
manifest lists; :func:`records` refuses to read files that fail it.

Updating is an explicit change to this repository:

    python scripts/vendor-synthetic-enterprise.py --source PATH_TO_SYNTHETIC_ENTERPRISE_CHECKOUT

copies the projection out of a local checkout (at the commit to pin, with a clean tree) and
rewrites the lock. The test suite then shows every value that moved. The script lives outside
``app/`` because it runs ``git``; a demo process runs no external program but its
migration.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from functools import cache, lru_cache
from typing import Any, Final

__all__ = [
    "LOCK_PATH",
    "PROJECTION",
    "VENDOR_DIR",
    "VendorError",
    "header",
    "lock",
    "records",
    "verify",
]

#: ``backend/vendor/synthetic-enterprise``: beside ``app/``, so every image that copies
#: ``backend/`` carries it.
VENDOR_DIR: Final = pathlib.Path(__file__).resolve().parents[3] / "vendor" / "synthetic-enterprise"
LOCK_PATH: Final = VENDOR_DIR / "SYNTHETIC_ENTERPRISE.lock"

PROJECTION: Final = "adg"
REPOSITORY: Final = "40ozs/synthetic-enterprise"
SNAPSHOT: Final = "standard-baseline"
#: The projection version this code reads. A different version is a code change here first.
PROJECTION_VERSION: Final = "0.3.0"
CONTRACT_VERSION: Final = "0.2.0"


class VendorError(RuntimeError):
    """The vendored projection is missing, edited, or not the one the lock pins."""


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def lock() -> dict[str, Any]:
    if not LOCK_PATH.is_file():
        raise VendorError(f"{LOCK_PATH} is missing; vendor the projection first.")
    document: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return document


def snapshot_dir() -> pathlib.Path:
    return VENDOR_DIR / str(lock()["snapshot"])


def projection_dir() -> pathlib.Path:
    return snapshot_dir() / "projections" / PROJECTION


@lru_cache(maxsize=1)
def verify() -> None:
    """Raise :class:`VendorError` unless the vendored files are exactly the pinned ones."""
    pinned = lock()
    manifest_path = snapshot_dir() / "manifest.json"
    if not manifest_path.is_file():
        raise VendorError(f"{manifest_path} is missing.")
    if _sha256(manifest_path) != pinned["manifest_sha256"]:
        raise VendorError("The vendored manifest is not the one SYNTHETIC_ENTERPRISE.lock pins.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prefix = f"projections/{PROJECTION}/"
    listed = {path: info for path, info in manifest["files"].items() if path.startswith(prefix)}
    present = {f"{prefix}{path.name}" for path in projection_dir().iterdir() if path.is_file()}
    if present != set(listed):
        raise VendorError(
            f"The vendored projection files {sorted(present)} are not the manifest's "
            f"{sorted(listed)}."
        )
    for path, info in sorted(listed.items()):
        if _sha256(snapshot_dir() / path) != info["sha256"]:
            raise VendorError(f"{path} does not match its manifest digest; it was edited.")
    head = header()
    expected = {
        "projection": pinned["projection"],
        "contract": pinned["contract_version"],
        "dataset": pinned["dataset"],
    }
    found = {
        "projection": f"{head['projection']}@{head['projection_version']}",
        "contract": head["contract_version"],
        "dataset": head["dataset"],
    }
    if found != expected:
        raise VendorError(f"The vendored projection is {found}; the lock pins {expected}.")
    if pinned["projection"] != f"{PROJECTION}@{PROJECTION_VERSION}":
        raise VendorError(
            f"This code reads {PROJECTION}@{PROJECTION_VERSION}; the lock pins "
            f"{pinned['projection']}."
        )


def header() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        (projection_dir() / "projection.json").read_text(encoding="utf-8")
    )
    return document


@cache
def records(name: str) -> tuple[dict[str, Any], ...]:
    """Every record of one projection file (``employees.jsonl`` …), verified first."""
    verify()
    path = projection_dir() / name
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
