"""The ``DISTINCT ON`` reads, pinned by what they select rather than by how they are spelled.

Four production queries reduce rows to "the first per group" with PostgreSQL's
``DISTINCT ON``. SQLAlchemy 2.1 changed the spelling (:mod:`app.sql_compat`), and a
spelling change is exactly where a query can go on returning rows while quietly returning
the wrong ones — the newest run per *collector* rather than per *scope*, the oldest earlier
version rather than the newest. These tests seed the tables directly, so each one controls
every row the query could pick, and assert which row wins in each group:

* :meth:`IngestionService.latest_run_per_scope` and the three
  :class:`OperationsRepository` reductions — one row per ``(collector, coalesce(target,
  ''))``, newest ``started_at`` first, ``run_id`` descending on a tie;
* :meth:`ChangeRepository.predecessors` falling through to ``_latest_before`` — the newest
  version strictly older than the one asked about;
* :meth:`ChangeRepository.first_present_at` — the oldest version that was *present*, so a
  leading tombstone is skipped.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.changes.repository import ChangeRepository
from app.contracts.v1.common import ObservationKind
from app.domain.observation import CollectorKind, ScanStatus
from app.history.model import ObjectVersion
from app.history.repository import VERSION_COLUMNS, version_from_row
from app.ingestion.service import IngestionService
from app.models.schema import CloseReason, collector_sources, object_versions, scan_runs
from app.repositories.operations import OperationsRepository
from tests.support import history as h

DAY = dt.timedelta(days=1)
LAST_WEEK = h.MONDAY - 7 * DAY


def _run_id(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"adg-distinct-on/{label}")


# ---------------------------------------------------------------- runs per scope


async def _source(
    session: AsyncSession, collector: CollectorKind, host: str, target: str | None
) -> int:
    fingerprint = hashlib.sha256(f"{collector}|{host}|{target}".encode()).hexdigest()
    return int(
        (
            await session.execute(
                sa.insert(collector_sources)
                .values(
                    fingerprint=fingerprint,
                    collector=collector.value,
                    collector_host=host,
                    method="test",
                    target=target,
                    first_seen_at=LAST_WEEK,
                    last_seen_at=LAST_WEEK,
                )
                .returning(collector_sources.c.id)
            )
        ).scalar_one()
    )


async def _run(
    session: AsyncSession,
    source_id: int,
    run_id: uuid.UUID,
    status: ScanStatus,
    started_at: dt.datetime,
) -> None:
    await session.execute(
        sa.insert(scan_runs).values(
            run_id=run_id,
            source_id=source_id,
            status=status.value,
            started_at=started_at,
            completed_at=started_at + dt.timedelta(minutes=5),
            error_count=0 if status is ScanStatus.SUCCEEDED else 1,
            created_at=started_at,
            updated_at=started_at,
        )
    )


@pytest.fixture
async def runs(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Four scopes, each built so the wrong reduction would pick a different row.

    * ``smb fs01`` — failed, then succeeded, then failed again: the latest run, the latest
      success and the latest failure are three different rows.
    * ``smb fs02`` — one success: a second scope of the same collector kind, which a
      per-kind reduction would swallow.
    * ``active_directory`` with no target, reported from two hosts — a NULL target is one
      scope, not one per source, so the two hosts' runs compete.
    * ``ntfs fs03`` — two failures started at the same instant: ``run_id`` breaks the tie.
    """
    smb_fs01 = await _source(session, CollectorKind.SMB, "collector01", "fs01")
    smb_fs02 = await _source(session, CollectorKind.SMB, "collector01", "fs02")
    ad_dc01 = await _source(session, CollectorKind.ACTIVE_DIRECTORY, "dc01", None)
    ad_dc02 = await _source(session, CollectorKind.ACTIVE_DIRECTORY, "dc02", None)
    ntfs_fs03 = await _source(session, CollectorKind.NTFS, "collector01", "fs03")

    ids = {
        label: _run_id(label)
        for label in (
            "fs01-monday-failed",
            "fs01-wednesday-succeeded",
            "fs01-friday-failed",
            "fs02-monday-succeeded",
            "ad-monday-succeeded",
            "ad-wednesday-partial",
        )
    }
    # Fixed literals, so which one sorts higher is visible in the source.
    ids["fs03-tie-low"] = uuid.UUID("00000000-0000-4000-8000-000000000001")
    ids["fs03-tie-high"] = uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")

    await _run(session, smb_fs01, ids["fs01-monday-failed"], ScanStatus.FAILED, h.MONDAY)
    await _run(
        session, smb_fs01, ids["fs01-wednesday-succeeded"], ScanStatus.SUCCEEDED, h.WEDNESDAY
    )
    await _run(session, smb_fs01, ids["fs01-friday-failed"], ScanStatus.FAILED, h.FRIDAY)
    await _run(session, smb_fs02, ids["fs02-monday-succeeded"], ScanStatus.SUCCEEDED, h.MONDAY)
    await _run(session, ad_dc01, ids["ad-monday-succeeded"], ScanStatus.SUCCEEDED, h.MONDAY)
    await _run(session, ad_dc02, ids["ad-wednesday-partial"], ScanStatus.PARTIAL, h.WEDNESDAY)
    await _run(session, ntfs_fs03, ids["fs03-tie-low"], ScanStatus.FAILED, h.THURSDAY)
    await _run(session, ntfs_fs03, ids["fs03-tie-high"], ScanStatus.FAILED, h.THURSDAY)
    await session.commit()
    return ids


def _by_scope(rows: Any) -> dict[tuple[str, str | None], str]:
    return {(row.collector, row.target): str(row.run_id) for row in rows}


class TestLatestRunPerScope:
    async def test_one_row_per_scope_newest_first(
        self, session: AsyncSession, runs: dict[str, uuid.UUID]
    ) -> None:
        latest = await IngestionService(session).latest_run_per_scope()

        assert len(latest) == 4
        by_scope = {(row.collector, row.target or ""): str(row.run_id) for row in latest}
        assert by_scope == {
            ("smb", "fs01"): str(runs["fs01-friday-failed"]),
            ("smb", "fs02"): str(runs["fs02-monday-succeeded"]),
            ("active_directory", ""): str(runs["ad-wednesday-partial"]),
            ("ntfs", "fs03"): str(runs["fs03-tie-high"]),
        }

    async def test_rows_come_back_in_scope_order(
        self, session: AsyncSession, runs: dict[str, uuid.UUID]
    ) -> None:
        latest = await IngestionService(session).latest_run_per_scope()

        scopes = [(row.collector, row.target or "") for row in latest]
        assert scopes == sorted(scopes)


class TestOperationsReductions:
    async def test_latest_runs(self, session: AsyncSession, runs: dict[str, uuid.UUID]) -> None:
        latest = await OperationsRepository(session).latest_runs()

        assert _by_scope(latest) == {
            ("smb", "fs01"): str(runs["fs01-friday-failed"]),
            ("smb", "fs02"): str(runs["fs02-monday-succeeded"]),
            ("active_directory", None): str(runs["ad-wednesday-partial"]),
            ("ntfs", "fs03"): str(runs["fs03-tie-high"]),
        }
        assert len(latest) == 4

    async def test_latest_successes_count_partial_and_skip_newer_failures(
        self, session: AsyncSession, runs: dict[str, uuid.UUID]
    ) -> None:
        successes = await OperationsRepository(session).latest_successes()

        assert _by_scope(successes) == {
            ("smb", "fs01"): str(runs["fs01-wednesday-succeeded"]),
            ("smb", "fs02"): str(runs["fs02-monday-succeeded"]),
            ("active_directory", None): str(runs["ad-wednesday-partial"]),
        }
        assert len(successes) == 3

    async def test_latest_failures(self, session: AsyncSession, runs: dict[str, uuid.UUID]) -> None:
        failures = await OperationsRepository(session).latest_failures()

        assert _by_scope(failures) == {
            ("smb", "fs01"): str(runs["fs01-friday-failed"]),
            ("ntfs", "fs03"): str(runs["fs03-tie-high"]),
        }
        assert len(failures) == 2


# ------------------------------------------------------------------ object versions

RUN = _run_id("versions")


def _version_row(
    key: str,
    valid_from: dt.datetime,
    valid_to: dt.datetime | None,
    *,
    present: bool = True,
) -> dict[str, Any]:
    state = {"server_key": key, "seen": valid_from.isoformat()} if present else None
    return {
        "object_kind": ObservationKind.SERVER.value,
        "object_key": key,
        "is_present": present,
        "state": state,
        "state_hash": (
            hashlib.sha256(f"{key}{valid_from.isoformat()}".encode()).hexdigest()
            if present
            else None
        ),
        "valid_from": valid_from,
        "last_seen_at": valid_from,
        "valid_to": valid_to,
        "close_reason": None if valid_to is None else CloseReason.SUPERSEDED.value,
        "opened_by_run_id": RUN,
        "last_seen_run_id": RUN,
        "closed_by_run_id": None if valid_to is None else RUN,
        "created_at": valid_from,
        "updated_at": valid_from,
    }


async def _versions(
    session: AsyncSession, rows: list[dict[str, Any]]
) -> dict[tuple[str, dt.datetime], ObjectVersion]:
    await session.execute(sa.insert(object_versions), rows)
    await session.commit()
    stored = (await session.execute(sa.select(*VERSION_COLUMNS))).mappings()
    return {(found.key, found.valid_from): found for found in map(version_from_row, stored)}


class TestLatestBefore:
    """Predecessors that are not adjacent — a gap in observation — reach ``_latest_before``."""

    async def test_newest_strictly_older_version_per_object(self, session: AsyncSession) -> None:
        stored = await _versions(
            session,
            [
                # fs01: three versions with gaps; asking about Thursday's must find Monday's,
                # not last week's (oldest) and not Thursday's itself or Friday's (not older).
                _version_row("fs01", LAST_WEEK, LAST_WEEK + DAY),
                _version_row("fs01", h.MONDAY, h.WEDNESDAY),
                _version_row("fs01", h.THURSDAY, h.FRIDAY),
                _version_row("fs01", h.NEXT_MONDAY, None),
                # fs02: one earlier version, also behind a gap.
                _version_row("fs02", h.MONDAY, h.MONDAY_END),
                _version_row("fs02", h.WEDNESDAY, None),
                # fs03: no earlier version at all.
                _version_row("fs03", h.THURSDAY, None),
            ],
        )

        asked = [
            stored[("fs01", h.THURSDAY)],
            stored[("fs02", h.WEDNESDAY)],
            stored[("fs03", h.THURSDAY)],
        ]
        found = await ChangeRepository(session).predecessors(asked)

        assert {
            (key, successor_from): (previous.key, previous.valid_from)
            for (_, key, successor_from), previous in found.items()
        } == {
            ("fs01", h.THURSDAY): ("fs01", h.MONDAY),
            ("fs02", h.WEDNESDAY): ("fs02", h.MONDAY),
        }

    async def test_adjacent_predecessor_still_wins(self, session: AsyncSession) -> None:
        stored = await _versions(
            session,
            [
                _version_row("fs01", h.MONDAY, h.WEDNESDAY),
                _version_row("fs01", h.WEDNESDAY, None),
            ],
        )

        found = await ChangeRepository(session).predecessors([stored[("fs01", h.WEDNESDAY)]])

        assert [previous.valid_from for previous in found.values()] == [h.MONDAY]


class TestFirstPresentAt:
    async def test_oldest_present_version_skipping_a_leading_tombstone(
        self, session: AsyncSession
    ) -> None:
        await _versions(
            session,
            [
                # fs01 was known only as gone last week, then came back on Monday.
                _version_row("fs01", LAST_WEEK, h.MONDAY, present=False),
                _version_row("fs01", h.MONDAY, h.WEDNESDAY),
                _version_row("fs01", h.WEDNESDAY, None),
                # fs02 has always been present; its oldest version is the answer.
                _version_row("fs02", h.MONDAY, h.FRIDAY),
                _version_row("fs02", h.FRIDAY, None),
                # fs03 has only ever been a tombstone: it was never present.
                _version_row("fs03", h.MONDAY, None, present=False),
            ],
        )

        first = await ChangeRepository(session).first_present_at(
            {ObservationKind.SERVER: ["fs01", "fs02", "fs03", "never-seen"]}
        )

        assert first == {
            (ObservationKind.SERVER, "fs01"): h.MONDAY,
            (ObservationKind.SERVER, "fs02"): h.MONDAY,
        }
