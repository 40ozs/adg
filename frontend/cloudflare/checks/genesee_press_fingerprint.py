"""Genesee Press's logical fingerprint, for comparing a hosted demo database with the canon.

Verification only, never imported by ADG. ``fingerprint()``, ``_digest_rows`` and the two
constants are copied verbatim from the dataset's own reset at commit ``4512524`` (then
``backend/app/demo/null_meridian/reset.py``), exactly as the integration handoff
(docs/handoffs/adg-demo-integration-01.md §5) ran them from a scratch copy. Demo Mode's
canonical fingerprint (``python -m app.demo_mode verify``) is the product's check; this one
answers a different question -- is the *dataset* in this database byte-for-byte Genesee
Press's -- independently of the database's own metadata. ``EXPECTED`` was re-pinned by
SYNTH-ADG-01 from a local reset of the vendored synthetic-enterprise projection.

Expected: b4b93b65a77d895cbd0495d34865937bdab39596494bd3ca035ce8f8e59445d0

Run inside the API image, against the demo database (reads only)::

    docker run --rm -i -e ADG_DATABASE_URL=... <image> python - < genesee_press_fingerprint.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa

from app.models.schema import metadata

EXPECTED = "b4b93b65a77d895cbd0495d34865937bdab39596494bd3ca035ce8f8e59445d0"

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


def main() -> int:
    url = sa.engine.make_url(os.environ["ADG_DATABASE_URL"])
    engine = sa.create_engine(url)
    try:
        overall, digests = fingerprint(engine)
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "fingerprint": overall,
                "expected": EXPECTED,
                "match": overall == EXPECTED,
                "digests": digests,
            },
            indent=2,
        )
    )
    return 0 if overall == EXPECTED else 1


if __name__ == "__main__":
    sys.exit(main())
