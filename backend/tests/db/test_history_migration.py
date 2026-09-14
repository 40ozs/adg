r"""The Phase 7 backfill, run against MVP-shaped data.

An installation upgrading to Phase 7 has current-state rows and no timeline. The migration
has to give every one of them a version without inventing anything the old schema could not
support, and without corrupting the state it copies.

These tests run the migration's **own** ``_backfill`` — the function Alembic executes,
loaded from the revision file — against an estate ingested through the real endpoints, with
the timeline emptied first to stand in for a pre-Phase-7 database. What is asserted is:

* every current-state row gets exactly one open version, and no row is missed;
* the interval is exactly the two timestamps the old schema kept, and nothing wider;
* the state is the row, so a record rebuilt from a version equals the record read live;
* the digest the migration writes is the digest the **application** computes, so the
  duplicated canonicalization in the revision file cannot drift unnoticed;
* every backfilled version is marked as such, and every answer drawn from one says so.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.history.bindings import BINDINGS
from app.history.model import Certainty, VersionOrigin, state_digest, stored_state
from app.history.repository import VersionReader
from app.history.service import HistoryService
from app.models.schema import object_versions
from app.repositories import MembershipRepository, ResourceRepository
from tests.support.ingest import ingest_storable_scenario

REVISION_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "database"
    / "migrations"
    / "versions"
    / "0007_history_model.py"
)

SCENARIO = "03-nested-group-grant"
"""An MVP estate with all seven observation kinds: principals, a nested group chain, a
server, a share and its ACL, a directory and its DACL."""


def load_revision() -> ModuleType:
    """The migration module itself, so the test exercises the code Alembic runs."""
    spec = importlib.util.spec_from_file_location("adg_revision_0007", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def pre_phase_seven(client: AsyncClient, session: AsyncSession) -> dict[str, int]:
    """An ingested estate with its timeline removed: what an upgrading database looks like.

    Returns the current-state row count per kind, so the assertions can be about *every*
    row rather than about a handful somebody remembered to name.
    """
    await ingest_storable_scenario(client, SCENARIO)
    await session.execute(sa.delete(object_versions))
    await session.commit()

    counts: dict[str, int] = {}
    for kind, binding in BINDINGS.items():
        total = (
            await session.execute(sa.select(sa.func.count()).select_from(binding.table))
        ).scalar_one()
        counts[kind.value] = int(total)
    assert sum(counts.values()) > 0, "the scenario stored nothing to back-fill"
    return counts


async def run_backfill(session: AsyncSession) -> None:
    revision = load_revision()
    connection = await session.connection()
    await connection.run_sync(revision._backfill)
    await session.commit()


class TestTheBackfillPreservesEveryRow:
    async def test_every_current_state_row_gets_exactly_one_open_version(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        await run_backfill(session)

        rows = (
            await session.execute(
                sa.select(object_versions.c.object_kind, sa.func.count())
                .where(object_versions.c.valid_to.is_(None))
                .group_by(object_versions.c.object_kind)
            )
        ).all()
        written = {kind: int(total) for kind, total in rows}

        assert written == {kind: total for kind, total in pre_phase_seven.items() if total}

    async def test_no_version_is_closed_and_none_is_a_tombstone(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        """A migration may not conclude that anything is gone. Nobody looked."""
        await run_backfill(session)

        closed = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(object_versions)
                .where(
                    sa.or_(
                        object_versions.c.valid_to.is_not(None),
                        object_versions.c.is_present.is_(False),
                    )
                )
            )
        ).scalar_one()

        assert closed == 0

    async def test_the_interval_is_exactly_what_the_old_schema_knew(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        await run_backfill(session)

        for kind, binding in BINDINGS.items():
            table = binding.table
            mismatched = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(
                        object_versions.join(
                            table, table.c[binding.key_column] == object_versions.c.object_key
                        )
                    )
                    .where(
                        object_versions.c.object_kind == kind.value,
                        sa.or_(
                            object_versions.c.valid_from != table.c.first_observed_at,
                            object_versions.c.last_seen_at != table.c.last_observed_at,
                            object_versions.c.opened_by_run_id != table.c.first_observed_run_id,
                            object_versions.c.last_seen_run_id != table.c.last_observed_run_id,
                        ),
                    )
                )
            ).scalar_one()

            assert mismatched == 0, kind.value

    async def test_the_container_and_related_keys_match_the_row_they_came_from(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        await run_backfill(session)

        for kind, binding in BINDINGS.items():
            for column, projected in (
                (binding.container_column, object_versions.c.container_key),
                (binding.related_column, object_versions.c.related_key),
            ):
                if column is None:
                    continue
                table = binding.table
                mismatched = (
                    await session.execute(
                        sa.select(sa.func.count())
                        .select_from(
                            object_versions.join(
                                table,
                                table.c[binding.key_column] == object_versions.c.object_key,
                            )
                        )
                        .where(
                            object_versions.c.object_kind == kind.value,
                            projected.is_distinct_from(table.c[column]),
                        )
                    )
                ).scalar_one()

                assert mismatched == 0, f"{kind.value}.{column}"


class TestTheMigrationAndTheApplicationAgreeOnTheDigest:
    async def test_every_backfilled_digest_is_the_application_s_own(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        """The revision duplicates the canonicalization so it stays frozen. This is the guard."""
        await run_backfill(session)

        rows = (
            await session.execute(
                sa.select(
                    object_versions.c.object_kind,
                    object_versions.c.object_key,
                    object_versions.c.state,
                    object_versions.c.state_hash,
                )
            )
        ).mappings()

        checked = 0
        for row in rows:
            assert row["state_hash"] == state_digest(row["state"]), (
                f"{row['object_kind']} {row['object_key']}: the migration's digest is not the "
                "digest app.history.model computes. The revision's copy of the "
                "canonicalization has drifted, and every version it wrote is unreadable to "
                "the writer's change detection."
            )
            checked += 1
        assert checked == sum(pre_phase_seven.values())

    async def test_the_stored_state_is_the_row_minus_what_the_version_already_carries(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        await run_backfill(session)

        binding = BINDINGS[ObservationKind.SMB_SHARE]
        share = (await session.execute(sa.select(binding.table).limit(1))).mappings().one()
        version = (
            await session.execute(
                sa.select(object_versions.c.state).where(
                    object_versions.c.object_kind == ObservationKind.SMB_SHARE.value,
                    object_versions.c.object_key == share[binding.key_column],
                )
            )
        ).scalar_one()

        assert version == stored_state(dict(share))


class TestABackfilledAnswerSaysWhatItIs:
    async def test_every_version_is_marked_backfilled(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        await run_backfill(session)

        origins = (await session.execute(sa.select(object_versions.c.origin).distinct())).scalars()

        assert set(origins) == {VersionOrigin.BACKFILLED.value}

    async def test_a_point_in_time_answer_reports_backfilled_rather_than_observed(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        """Inside the interval it would otherwise be ``observed``, and it must not be.

        The old schema kept two timestamps and one state, so an object that changed and
        changed back between them left one row. Reporting that reconstruction as watched is
        the one thing a backfill must never do.
        """
        await run_backfill(session)

        binding = BINDINGS[ObservationKind.SMB_SHARE]
        row = (await session.execute(sa.select(binding.table).limit(1))).mappings().one()
        presence = await VersionReader(session).presence_at(
            ObservationKind.SMB_SHARE, row["share_key"], row["first_observed_at"]
        )

        assert presence.exists is True
        assert presence.certainty is Certainty.BACKFILLED

    async def test_an_effective_access_answer_rests_on_reconstructed_state_and_says_so(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        await run_backfill(session)

        resource = (
            (
                await session.execute(
                    sa.select(BINDINGS[ObservationKind.NTFS_RESOURCE].table).limit(1)
                )
            )
            .mappings()
            .one()
        )
        principal = (
            (await session.execute(sa.select(BINDINGS[ObservationKind.PRINCIPAL].table).limit(1)))
            .mappings()
            .one()
        )

        answer = await HistoryService(session).effective_access_at(
            principal["principal_key"], resource["resource_key"], resource["last_observed_at"]
        )

        assert answer.rests_on_reconstructed_state
        assert answer.certainty is Certainty.BACKFILLED


class TestTheProductStillWorksOnBackfilledHistory:
    async def test_a_reconstructed_record_equals_the_one_read_live(
        self, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        """The state is the row, so the two reads must produce the same record.

        Everything but provenance: a version reports the window *that state* was observed
        over, which for a backfilled version is the row's own two timestamps — so on a
        freshly migrated database even those agree.
        """
        await run_backfill(session)

        resources = ResourceRepository(session)
        row = (
            (
                await session.execute(
                    sa.select(BINDINGS[ObservationKind.NTFS_RESOURCE].table).limit(1)
                )
            )
            .mappings()
            .one()
        )

        live = await resources.get_ntfs_resource(row["resource_key"])
        as_of = (
            await HistoryService(session).resource_acl_at(
                row["resource_key"], row["last_observed_at"]
            )
        ).resource

        assert live is not None and as_of is not None
        assert as_of == live

    async def test_current_state_queries_are_unaffected_by_the_backfill(
        self, client: AsyncClient, session: AsyncSession, pre_phase_seven: dict[str, int]
    ) -> None:
        """The migration adds a table. It must not change a single current-state answer."""
        membership = MembershipRepository(session)
        group = (
            await session.execute(
                sa.select(BINDINGS[ObservationKind.PRINCIPAL].table.c.principal_key).limit(1)
            )
        ).scalar_one()
        before = await membership.get_principal(group)

        await run_backfill(session)

        assert await MembershipRepository(session).get_principal(group) == before

        response = await client.get("/api/v1/collection/status")
        assert response.status_code == 200


def as_dict(record: Any) -> dict[str, Any]:
    return {name: getattr(record, name) for name in record.__dataclass_fields__}
