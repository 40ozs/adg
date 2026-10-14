"""Revision 0019 against a database whose graph already holds relationships.

0018 shipped empty graph tables, and an installation may have recorded relationships in them
before 0019 arrives. This builds that state — a database at 0018, filled with collected facts
through the real ingestion API and holding one relationship written in 0018's shape — and then
checks the three things the revision promises:

* the upgrade changes no existing row: every pre-existing column of every table fingerprints
  the same, the existing support gains NULL retirement columns, and the pass log is empty;
* the upgraded schema works: the projector rebuilds over the existing facts, a support can be
  retired, and a support can name a condition only 0019 knows;
* the downgrade restores 0018 exactly, removing only the derived rows 0018 cannot represent
  (a retired support would read as current again under it), and a second upgrade succeeds.

Its own database (``<test database>_derivation_migration``), because it moves the schema up and
down and the shared test database must stay at head for every other suite.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.config import build_settings
from app.db import Database
from app.domain.relationships import (
    DerivationClass,
    DerivationRule,
    EvidenceKind,
    NodeKind,
    NodeRef,
    RelationshipAssertion,
    RelationshipCondition,
    RelationshipEvidence,
    RelationshipScope,
    RelationshipType,
)
from app.main import create_app
from app.models.schema import membership_edges, ntfs_aces
from app.relationships import RelationshipProjector
from app.repositories.basis import CollectionBasisRepository
from app.repositories.relationships import RelationshipRepository
from tests.db.conftest import BACKEND_ROOT, TEST_DATABASE_URL
from tests.support.auth import auth_headers
from tests.support.ingest import ingest_storable_scenario

PREVIOUS = "0018_graph_relationships"
HEAD = "0019_graph_derivation"
SCENARIOS = ("03-nested-group-grant", "09-broken-inheritance")
NEW_SUPPORT_COLUMNS = ("retired_at", "retired_basis_token")


def _url() -> str:
    url = sa.engine.make_url(TEST_DATABASE_URL)
    return url.set(database=f"{url.database}_derivation_migration").render_as_string(
        hide_password=False
    )


MIGRATION_URL = _url()


def alembic(*arguments: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env={**os.environ, "ADG_DATABASE_URL": MIGRATION_URL},
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            f"alembic {' '.join(arguments)} failed:\n{completed.stdout}\n{completed.stderr}"
        )


@pytest.fixture
def previous_revision_database(migrated_database: str) -> Iterator[str]:
    url = sa.engine.make_url(MIGRATION_URL)
    admin = sa.create_engine(
        url.set(database="postgres", drivername="postgresql+psycopg").render_as_string(
            hide_password=False
        ),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            connection.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
        alembic("upgrade", PREVIOUS)
        yield MIGRATION_URL
    finally:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
async def populated(previous_revision_database: str) -> AsyncIterator[Database]:
    """The 0018 database, with facts ingested and one relationship in 0018's shape.

    The projector cannot run against 0018 — it writes columns 0019 adds — so each completion's
    derivation fails inside its savepoint and the completions commit regardless: the very
    property ``project_completed_run`` exists to guarantee, exercised on a real old schema.
    """
    settings = build_settings(
        environment="test", log_format="text", database_url=previous_revision_database
    )
    database = Database(settings)
    app = create_app(settings)
    app.state.database = database
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=auth_headers(settings),
        ) as client:
            for scenario in SCENARIOS:
                await ingest_storable_scenario(client, scenario)
        await _record_in_0018_shape(previous_revision_database)
        yield database
    finally:
        await database.dispose()


async def _record_in_0018_shape(url: str) -> None:
    """One relationship, support and evidence row, written with 0018's columns only."""
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            edge = (
                connection.execute(
                    sa.select(membership_edges).where(
                        membership_edges.c.edge_kind == "local_group_member"
                    )
                )
                .mappings()
                .one()
            )
            assertion = RelationshipAssertion(
                relationship_type=RelationshipType.LOCAL_GROUP_MEMBER_OF,
                source=NodeRef.principal(edge["member_key"]),
                target=NodeRef.principal(edge["group_key"]),
                scope=RelationshipScope.host("fs01"),
                derivation_class=DerivationClass.OBSERVED,
                rule=DerivationRule("membership.local_edge", 1),
                evidence=(
                    RelationshipEvidence(
                        EvidenceKind.MEMBERSHIP_EDGE,
                        edge["edge_key"],
                        edge["last_observed_run_id"],
                        edge["last_observed_at"],
                    ),
                ),
                basis_token="0" * 32,
                derived_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
                attributes={"edge_kind": "local_group_member"},
            )
            now = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
            run = edge["last_observed_run_id"]
            observed = edge["last_observed_at"]
            parameters = {
                "rid": assertion.relationship_id,
                "key": assertion.support_key,
                "source": assertion.source.key,
                "target": assertion.target.key,
                "edge": edge["edge_key"],
                "run": run,
                "observed": observed,
                "now": now,
                "attributes": '{"edge_kind": "local_group_member"}',
            }
            connection.execute(
                sa.text(
                    "INSERT INTO graph_relationships (relationship_id, relationship_type, "
                    "source_kind, source_key, target_kind, target_key, scope_kind, scope_key, "
                    "created_at, updated_at) VALUES (:rid, 'local_group_member_of', 'principal', "
                    ":source, 'principal', :target, 'host', 'fs01', :now, :now)"
                ),
                parameters,
            )
            connection.execute(
                sa.text(
                    "INSERT INTO graph_relationship_supports (relationship_id, support_key, "
                    "derivation_class, rule_id, rule_version, qualifiers, conditions, "
                    "attributes, basis_token, derived_at, first_observed_at, "
                    "first_observed_run_id, last_observed_at, last_observed_run_id, "
                    "created_at, updated_at) VALUES (:rid, :key, 'observed', "
                    "'membership.local_edge', 1, '{}', '{}', "
                    "CAST(:attributes AS jsonb), repeat('0', 32), :now, "
                    ":observed, :run, :observed, :run, :now, :now)"
                ),
                parameters,
            )
            connection.execute(
                sa.text(
                    "INSERT INTO graph_relationship_evidence (relationship_id, support_key, "
                    "evidence_kind, evidence_key, role, first_observed_at, "
                    "first_observed_run_id, last_observed_at, last_observed_run_id, "
                    "created_at, updated_at) VALUES (:rid, :key, 'membership_edge', :edge, "
                    "'premise', :observed, :run, :observed, :run, :now, :now)"
                ),
                parameters,
            )
    finally:
        engine.dispose()


def columns_of(url: str) -> dict[str, tuple[str, ...]]:
    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        return {
            name: tuple(column["name"] for column in inspector.get_columns(name))
            for name in sorted(set(inspector.get_table_names()) - {"alembic_version"})
        }
    finally:
        engine.dispose()


def fingerprints(url: str, columns: dict[str, tuple[str, ...]]) -> dict[str, tuple[int, str]]:
    """Each table's row count and a digest of every row, over exactly ``columns``."""
    engine = sa.create_engine(url)
    try:
        result: dict[str, tuple[int, str]] = {}
        with engine.connect() as connection:
            for name, names in sorted(columns.items()):
                row_text = "row(" + ", ".join(f't."{column}"' for column in names) + ")::text"
                row = connection.execute(
                    sa.text(
                        f"SELECT count(*), md5(coalesce(string_agg({row_text}, chr(10) "
                        f"ORDER BY {row_text}), '')) FROM \"{name}\" t"
                    )
                ).one()
                result[name] = (int(row[0]), str(row[1]))
        return result
    finally:
        engine.dispose()


def scalar(url: str, statement: str) -> int:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            return int(connection.execute(sa.text(statement)).scalar_one())
    finally:
        engine.dispose()


async def test_0019_preserves_an_existing_graph_and_its_downgrade_restores_0018(
    populated: Database,
) -> None:
    columns_0018 = columns_of(MIGRATION_URL)
    before = fingerprints(MIGRATION_URL, columns_0018)
    assert before["graph_relationships"][0] == 1, "the 0018 graph holds one relationship"
    assert before["graph_relationship_supports"][0] == 1
    assert "graph_derivation_passes" not in before

    alembic("upgrade", HEAD)
    columns_0019 = columns_of(MIGRATION_URL)
    assert set(columns_0019) - set(columns_0018) == {"graph_derivation_passes"}
    assert set(columns_0019["graph_relationship_supports"]) - set(
        columns_0018["graph_relationship_supports"]
    ) == set(NEW_SUPPORT_COLUMNS)
    assert fingerprints(MIGRATION_URL, columns_0018) == before, "the upgrade changed a row"
    assert (
        scalar(
            MIGRATION_URL,
            "SELECT count(*) FROM graph_relationship_supports "
            "WHERE retired_at IS NULL AND retired_basis_token IS NULL",
        )
        == 1
    )
    assert scalar(MIGRATION_URL, "SELECT count(*) FROM graph_derivation_passes") == 0

    # The upgraded schema works over the facts and the relationship already there.
    await populated.dispose()
    now = dt.datetime.now(tz=dt.UTC)
    async with populated.session() as session:
        outcome = await RelationshipProjector(session).rebuild(now=now)
        assert outcome.succeeded and outcome.relationships_created > 0
        repository = RelationshipRepository(session)
        relationships = await repository.get_many(
            (
                await session.execute(sa.text("SELECT relationship_id FROM graph_relationships"))
            ).scalars()
        )
        owns = next(
            r for r in relationships.values() if r.relationship_type is RelationshipType.OWNS
        )
        basis = (await CollectionBasisRepository(session).current()).token
        assert (
            await repository.retire(
                [(owns.relationship_id, owns.supports[0].support_key)], now=now, basis_token=basis
            )
            == 1
        )
        ace = (
            (
                await session.execute(
                    sa.select(ntfs_aces).where(ntfs_aces.c.trustee_sid == "S-1-5-32-544")
                )
            )
            .mappings()
            .one()
        )
        await repository.record(
            RelationshipAssertion(
                relationship_type=RelationshipType.WRITE_OWNER,
                source=NodeRef.principal("fs01|S-1-5-32-544"),
                target=NodeRef(NodeKind.NTFS_RESOURCE, ace["resource_key"]),
                scope=RelationshipScope.host("fs01"),
                derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
                conditions=frozenset({RelationshipCondition.ACL_ENTRIES_INCOMPLETE}),
                rule=DerivationRule("test.condition_only_0019_knows", 1),
                evidence=(
                    RelationshipEvidence(
                        EvidenceKind.NTFS_ACE,
                        ace["ace_key"],
                        ace["last_observed_run_id"],
                        ace["last_observed_at"],
                    ),
                ),
                basis_token=basis,
                derived_at=now,
            ),
            now=now,
        )
        await session.commit()
    await populated.dispose()

    supports = scalar(MIGRATION_URL, "SELECT count(*) FROM graph_relationship_supports")
    unrepresentable = scalar(
        MIGRATION_URL,
        "SELECT count(*) FROM graph_relationship_supports WHERE retired_at IS NOT NULL "
        "OR 'acl_entries_incomplete' = ANY(conditions)",
    )
    assert unrepresentable == 2

    alembic("downgrade", PREVIOUS)
    assert columns_of(MIGRATION_URL) == columns_0018
    assert (
        scalar(MIGRATION_URL, "SELECT count(*) FROM graph_relationship_supports")
        == supports - unrepresentable
    )
    assert (
        scalar(
            MIGRATION_URL,
            "SELECT count(*) FROM graph_relationships r WHERE NOT EXISTS (SELECT 1 FROM "
            "graph_relationship_supports s WHERE s.relationship_id = r.relationship_id)",
        )
        == 0
    )
    after = fingerprints(MIGRATION_URL, columns_0018)
    graph = {"graph_relationships", "graph_relationship_supports", "graph_relationship_evidence"}
    assert {k: v for k, v in after.items() if k not in graph} == {
        k: v for k, v in before.items() if k not in graph
    }, "the downgrade touched a collected fact"

    alembic("upgrade", HEAD)
    assert set(columns_of(MIGRATION_URL)) == set(columns_0019)
