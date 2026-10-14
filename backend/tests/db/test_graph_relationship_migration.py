"""Revision 0018 against a populated database: additive, reversible, and touching nothing else.

An installation upgrading to the relationship graph already holds collected facts, their
timeline and their provenance. This builds exactly that — a database at revision 0017, filled
through the real ingestion API — and then fingerprints every pre-existing table, row for row,
across the upgrade, the downgrade and a second upgrade. The migration may add three empty tables
and must change nothing else; a relationship must be recordable over the facts already there.

Its own database (``<test database>_graph_migration``), because it moves the schema up and down
and the shared test database must stay at head for every other suite.
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
    NodeRef,
    RelationshipAssertion,
    RelationshipCurrency,
    RelationshipEvidence,
    RelationshipScope,
    RelationshipType,
)
from app.main import create_app
from app.models.schema import membership_edges
from app.repositories.basis import CollectionBasisRepository
from app.repositories.relationships import RelationshipRepository
from tests.db.conftest import BACKEND_ROOT, TEST_DATABASE_URL
from tests.support.auth import auth_headers
from tests.support.ingest import ingest_storable_scenario

PREVIOUS = "0017_deployment_marker"
GRAPH_TABLES = frozenset(
    {"graph_relationships", "graph_relationship_supports", "graph_relationship_evidence"}
)
SCENARIOS = (
    "03-nested-group-grant",
    "05-cyclic-group-graph",
    "06-unresolved-sid",
    "09-broken-inheritance",
)
"""A nested chain, a cycle, an orphaned SID and a host-local group: the shapes an existing
estate holds that a careless migration is most likely to disturb."""


def _url() -> str:
    url = sa.engine.make_url(TEST_DATABASE_URL)
    return url.set(database=f"{url.database}_graph_migration").render_as_string(hide_password=False)


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
    """A fresh database built only as far as revision 0017."""
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
    """The 0017 database, filled through the ingestion API as an installation would be."""
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
        yield database
    finally:
        await database.dispose()


def tables_and_fingerprints(url: str) -> dict[str, tuple[int, str]]:
    """Every table but Alembic's own, with its row count and a digest of every row."""
    engine = sa.create_engine(url)
    try:
        names = sorted(set(sa.inspect(engine).get_table_names()) - {"alembic_version"})
        result: dict[str, tuple[int, str]] = {}
        with engine.connect() as connection:
            for name in names:
                row = connection.execute(
                    sa.text(
                        f"SELECT count(*), md5(coalesce(string_agg(t::text, chr(10) "
                        f"ORDER BY t::text), '')) FROM \"{name}\" t"
                    )
                ).one()
                result[name] = (int(row[0]), str(row[1]))
        return result
    finally:
        engine.dispose()


async def test_the_migration_is_additive_and_reversible_over_a_populated_database(
    populated: Database,
) -> None:
    before = tables_and_fingerprints(MIGRATION_URL)
    assert not GRAPH_TABLES & set(before)
    populated_tables = {name for name, (rows, _) in before.items() if rows}
    assert {"principals", "membership_edges", "ntfs_aces", "object_versions", "observations"} <= (
        populated_tables
    ), "the estate stored too little to be a meaningful upgrade"

    alembic("upgrade", "heads")
    upgraded = tables_and_fingerprints(MIGRATION_URL)
    assert set(upgraded) - set(before) == GRAPH_TABLES
    assert {name: upgraded[name] for name in before} == before, "the upgrade changed a row"
    assert all(upgraded[name][0] == 0 for name in GRAPH_TABLES), "nothing is backfilled"

    # The new schema works over the facts that were already there.
    await populated.dispose()
    async with populated.session() as session:
        edge = (
            (
                await session.execute(
                    sa.select(membership_edges).where(
                        membership_edges.c.edge_kind == "local_group_member"
                    )
                )
            )
            .mappings()
            .one()
        )
        assertion = RelationshipAssertion(
            relationship_type=RelationshipType.LOCAL_GROUP_MEMBER_OF,
            source=NodeRef.principal(edge["member_key"]),
            target=NodeRef.principal(edge["group_key"]),
            scope=RelationshipScope.host(edge["host_key"].casefold()),
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
            basis_token=(await CollectionBasisRepository(session).current()).token,
            derived_at=dt.datetime.now(tz=dt.UTC),
        )
        repository = RelationshipRepository(session)
        await repository.record(assertion, now=dt.datetime.now(tz=dt.UTC))
        await session.commit()
        stored = await repository.get(assertion.relationship_id)
        assert stored is not None and stored.currency is RelationshipCurrency.CURRENT
    await populated.dispose()

    alembic("downgrade", PREVIOUS)
    downgraded = tables_and_fingerprints(MIGRATION_URL)
    assert downgraded == before, "the downgrade removed more than the graph tables"

    alembic("upgrade", "heads")
    again = tables_and_fingerprints(MIGRATION_URL)
    assert {name: again[name] for name in before} == before
    assert all(again[name][0] == 0 for name in GRAPH_TABLES)
