r"""Demo Mode against a real PostgreSQL: reset, determinism, the real API, and every refusal.

Marked ``smoke`` and skipped unless ``ADG_RUN_SMOKE_TESTS=1``, like ``tests/db``. It uses its
own database, ``<dev database>_demo_test``, dropped and recreated once per session -- never
the development database and never ``tests/db``'s, because a demo reset empties every table.

The dataset is the reference estate in :mod:`app.demo`. Nothing here generates synthetic
data except :func:`two_generation_dataset`, a test-only wrapper that re-posts the reference
baseline under fixed run ids so the synthetic-scan hook has a second generation to apply.
"""

from __future__ import annotations

import os
import socket
import sys
import types
import urllib.parse
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.auth.roles import Role
from app.config import Settings, build_settings
from app.db import Database
from app.demo import build_estate
from app.demo import estate as reference
from app.demo.seed import seed_transcripts
from app.demo.transcripts import restamp
from app.demo_mode import datasets
from app.demo_mode.datasets import (
    CanonicalProbe,
    DatasetEntry,
    DatasetUnavailable,
    DemoDatasetMetadata,
    ScanGeneration,
    load_dataset,
)
from app.demo_mode.ingest import NotADemoDeployment, apply_generation
from app.demo_mode.lifecycle import (
    DemoLifecycleError,
    _migrate,
    canonical_digests,
    canonical_fingerprint,
    demo_status,
    reset_demo,
    run_synthetic_scan,
    verify_demo_startup,
)
from app.demo_mode.marker import DemoTargetRefused
from app.demo_mode.reference import ReferenceEstateDataset
from app.main import create_app
from app.models.schema import metadata
from tests.support.auth import auth_headers, token_for_roles

SMOKE_ENABLED = os.getenv("ADG_RUN_SMOKE_TESTS") == "1"
pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not SMOKE_ENABLED,
        reason="Set ADG_RUN_SMOKE_TESTS=1 with PostgreSQL running, or run backend-test.ps1 -Smoke.",
    ),
]

BASE_DATABASE_URL = os.getenv("ADG_DATABASE_URL") or Settings().database_url
FINANCE = r"\\fs01\finance"


def _render(url: sa.engine.URL) -> str:
    return url.render_as_string(hide_password=False)


def _demo_test_url() -> str:
    url = sa.engine.make_url(BASE_DATABASE_URL)
    return _render(url.set(database=f"{url.database}_demo_test"))


DEMO_TEST_URL = _demo_test_url()


@pytest.fixture(scope="session")
def demo_database() -> str:
    """A fresh, migrated, empty and *unmarked* database named for the demo."""
    url = sa.engine.make_url(DEMO_TEST_URL)
    admin = sa.create_engine(_render(url.set(database="postgres")), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            connection.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()
    _migrate(_settings(DEMO_TEST_URL))
    return DEMO_TEST_URL


def _settings(url: str, dataset: str = "adg-reference-estate", **extra: Any) -> Settings:
    return build_settings(
        environment="test",
        log_format="text",
        log_level="WARNING",
        runtime_mode="demo",
        database_url=url,
        demo_dataset=dataset,
        **extra,
    )


@pytest.fixture
def settings(demo_database: str) -> Settings:
    return _settings(demo_database)


@pytest.fixture(autouse=True)
def empty_and_unmarked(demo_database: str) -> Iterator[None]:
    """Every test starts from a database with no rows at all, the marker included."""
    engine = sa.create_engine(demo_database, isolation_level="AUTOCOMMIT")
    names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
    try:
        with engine.connect() as connection:
            connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()
    yield


def scalar(url: str, statement: str, **params: Any) -> Any:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(sa.text(statement), params).scalar()
    finally:
        engine.dispose()


async def reset(settings: Settings) -> Any:
    return await reset_demo(settings, migrate=False)


@pytest.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    instance = Database(settings)
    try:
        yield instance
    finally:
        await instance.dispose()


def _client(settings: Settings, database: Database, *roles: Role) -> AsyncClient:
    app = create_app(settings)
    app.state.database = database
    headers = (
        {"Authorization": f"Bearer {token_for_roles(settings, *roles)}"}
        if roles
        else auth_headers(settings)
    )
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", headers=headers
    )


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class TestReset:
    async def test_an_empty_database_is_initialized_and_marked(self, settings: Settings) -> None:
        report = await reset(settings)

        assert report.initialized is True
        assert report.ok
        assert scalar(settings.database_url, "SELECT runtime_mode FROM deployment_marker") == "demo"
        assert (
            scalar(settings.database_url, "SELECT dataset_key FROM deployment_marker")
            == "adg-reference-estate"
        )

    async def test_every_run_is_recorded_as_the_dataset_claimed(self, settings: Settings) -> None:
        report = await reset(settings)
        recorded = {run.name: run.recorded_status for run in report.seed.runs}

        assert recorded["ntfs-fs02"] == "partial"
        assert recorded["ntfs-fs03"] == "failed"
        assert {run.recorded_status for run in report.seed.runs} >= {"succeeded"}

    async def test_risk_rules_ran_over_the_synthetic_estate(self, settings: Settings) -> None:
        report = await reset(settings)
        assert report.risk_findings_open > 0

    async def test_the_reset_contacts_nothing_over_python_sockets_but_the_database(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """psycopg connects through libpq, below Python's socket module; anything that
        reached a directory, an SMB server or a webhook from Python would be recorded here."""
        destinations: list[Any] = []
        original = socket.socket.connect

        def record(self: socket.socket, address: Any) -> Any:
            destinations.append(address)
            return original(self, address)

        monkeypatch.setattr(socket.socket, "connect", record)
        await reset(settings)
        database_port = sa.engine.make_url(settings.database_url).port or 5432
        assert all(
            isinstance(address, tuple) and address[1] == database_port for address in destinations
        ), destinations


class TestDeterminism:
    async def test_two_resets_produce_the_same_canonical_state(self, settings: Settings) -> None:
        first = await reset(settings)
        second = await reset(settings)

        assert first.initialized and not second.initialized
        assert first.fingerprint == second.fingerprint
        assert first.counts == second.counts

    async def test_a_reset_restores_the_baseline_after_the_demo_was_used(
        self, settings: Settings, database: Database
    ) -> None:
        baseline = await reset(settings)
        # Use the demo: post the partial run again as a *new* run, as a rescan would.
        transcripts = load_dataset("adg-reference-estate").generations()[0].transcripts
        partial = next(t for t in transcripts if t.name == "ntfs-fs02")
        await apply_generation(settings, database, ScanGeneration("extra", "", (restamp(partial),)))
        async with database.engine.connect() as connection:
            assert await canonical_fingerprint(connection) != baseline.fingerprint

        restored = await reset(settings)
        assert restored.fingerprint == baseline.fingerprint

    async def test_the_generators_values_survive_ingestion_unchanged(
        self, settings: Settings
    ) -> None:
        await reset(settings)
        estate = build_estate("standard")
        url = settings.database_url

        engine = sa.create_engine(url)
        try:
            with engine.connect() as connection:
                stored = {
                    row.sid: row.display_name
                    for row in connection.execute(
                        sa.text("SELECT sid, display_name FROM principals")
                    )
                }
                finance_hash = connection.execute(
                    sa.text("SELECT acl_hash FROM ntfs_resources WHERE lower(path) = :p"),
                    {"p": FINANCE},
                ).scalar_one()
                edges = connection.execute(
                    sa.text("SELECT count(*) FROM membership_edges")
                ).scalar()
        finally:
            engine.dispose()

        for principal in estate.principals:
            assert stored[principal.sid] == principal.display_name
        directory = next(d for d in estate.directories if d.path.casefold() == FINANCE)
        assert finance_hash == directory.acl_hash
        assert edges == len(estate.edges)


class TestTemporalSemantics:
    async def test_partial_and_failed_runs_tombstone_nothing(self, settings: Settings) -> None:
        report = await reset(settings)

        assert report.counts["tombstones_open"] == 0
        # The share on FS03 survives its server's failed NTFS run.
        legacy = scalar(
            settings.database_url,
            "SELECT count(*) FROM smb_shares s JOIN servers v ON v.server_key = s.server_key "
            "WHERE lower(v.name) = 'fs03' AND lower(s.name) = 'legacy'",
        )
        assert legacy == 1

    async def test_every_current_object_has_exactly_one_open_version(
        self, settings: Settings
    ) -> None:
        await reset(settings)
        duplicates = scalar(
            settings.database_url,
            "SELECT count(*) FROM (SELECT object_kind, object_key FROM object_versions "
            "WHERE valid_to IS NULL GROUP BY 1, 2 HAVING count(*) > 1) d",
        )
        assert duplicates == 0

    async def test_a_repeated_partial_scan_does_not_erase_valid_state(
        self, settings: Settings, database: Database
    ) -> None:
        baseline = await reset(settings)
        transcripts = load_dataset("adg-reference-estate").generations()[0].transcripts
        partial = next(t for t in transcripts if t.name == "ntfs-fs02")
        await apply_generation(settings, database, ScanGeneration("again", "", (restamp(partial),)))
        status = await demo_status(settings)
        assert status.target_error is None
        after = scalar(
            settings.database_url,
            "SELECT count(*) FROM object_versions WHERE valid_to IS NULL AND NOT is_present",
        )
        assert after == 0
        current_resources = scalar(settings.database_url, "SELECT count(*) FROM ntfs_resources")
        assert current_resources == baseline.counts["current_ntfs_resource"]


class TestRefusals:
    async def test_an_unmarked_database_holding_data_is_never_wiped(
        self, demo_database: str, settings: Settings
    ) -> None:
        """Simulate a production database: filled by the production ingestion path, unmarked."""
        production = build_settings(
            environment="test", log_format="text", log_level="WARNING", database_url=demo_database
        )
        database = Database(production)
        try:
            app = create_app(production)
            app.state.database = database
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers=auth_headers(production),
            ) as client:
                transcripts = ReferenceEstateDataset().generations()[0].transcripts
                await seed_transcripts(client, transcripts[:1])
        finally:
            await database.dispose()
        before = scalar(demo_database, "SELECT count(*) FROM principals")

        with pytest.raises(DemoTargetRefused, match="already holds data"):
            await reset(settings)

        assert scalar(demo_database, "SELECT count(*) FROM principals") == before
        assert scalar(demo_database, "SELECT count(*) FROM deployment_marker") == 0

    async def test_a_database_marked_for_production_is_refused(self, settings: Settings) -> None:
        engine = sa.create_engine(settings.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO deployment_marker (marker_id, runtime_mode, marked_at, "
                        "marked_by) VALUES (1, 'production', now(), 'operator')"
                    )
                )
        finally:
            engine.dispose()

        with pytest.raises(DemoTargetRefused, match="marked runtime_mode='production'"):
            await reset(settings)

    async def test_a_demo_api_will_not_start_on_an_unmarked_database(
        self, settings: Settings
    ) -> None:
        app = create_app(settings)
        with pytest.raises(DemoTargetRefused, match="not yet marked"):
            async with app.router.lifespan_context(app):
                pass

    async def test_a_demo_api_starts_on_a_reset_database(self, settings: Settings) -> None:
        await reset(settings)
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            assert app.state.database is not None

    async def test_a_demo_api_will_not_serve_a_different_dataset_than_it_holds(
        self, settings: Settings, database: Database
    ) -> None:
        await reset(settings)
        engine = sa.create_engine(settings.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(sa.text("UPDATE deployment_marker SET dataset_key = 'other'"))
        finally:
            engine.dispose()

        with pytest.raises(DemoTargetRefused, match="holds dataset 'other'"):
            await verify_demo_startup(settings, database)

    async def test_an_unavailable_dataset_is_refused_before_the_database_is_touched(
        self, demo_database: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A selected dataset whose generator is absent: refused, nothing marked, and no other
        estate -- the reference one included -- loaded in its place."""
        monkeypatch.setitem(
            datasets.DATASETS,
            "absent-v1",
            DatasetEntry("absent-v1", "app.demo.no_such_generator:synthetic_dataset", "fixture"),
        )
        absent = _settings(demo_database, dataset="absent-v1")
        with pytest.raises(DatasetUnavailable):
            load_dataset(absent.resolved_demo_dataset)

        with pytest.raises(DemoLifecycleError, match="not present in this build"):
            await reset(absent)
        assert scalar(demo_database, "SELECT count(*) FROM deployment_marker") == 0
        assert scalar(demo_database, "SELECT count(*) FROM scan_runs") == 0

    async def test_the_synthetic_provider_refuses_production_settings(
        self, demo_database: str
    ) -> None:
        production = build_settings(environment="test", database_url=demo_database)
        database = Database(production)
        try:
            with pytest.raises(NotADemoDeployment):
                await apply_generation(
                    production, database, ReferenceEstateDataset().generations()[0]
                )
        finally:
            await database.dispose()
        assert scalar(demo_database, "SELECT count(*) FROM scan_runs") == 0


class TestTheRealApplication:
    async def test_reads_answer_from_the_synthetic_estate(
        self, settings: Settings, database: Database
    ) -> None:
        await reset(settings)
        async with _client(settings, database) as client:
            explanation = await client.get(
                "/api/v1/access/explain",
                params={"principal": reference.sid(reference.ALICE), "resource": FINANCE},
            )
            search = await client.get("/api/v1/search", params={"q": "finance"})
            # /api/v1/collection/status is deliberately not here: under SQLAlchemy 2.1 its
            # DISTINCT ON form raises a deprecation the suite treats as an error, in production
            # mode too. That is pre-existing dependency drift; see the demo-mode handoff.
            who = await client.get(f"/api/v1/access/resources/{quote(FINANCE)}/principals")
            share = await client.get(f"/api/v1/shares/{quote('fs01|finance')}")

        assert explanation.status_code == 200
        body = explanation.json()
        assert body["verdict"]["outcome"] == "granted"
        assert f"group:{reference.sid(reference.FINANCE_RW)}" in {
            node["id"] for node in body["graph"]["nodes"]
        }
        assert search.status_code == 200
        assert who.status_code == 200
        assert share.status_code == 200

    async def test_external_ingestion_is_refused_and_writes_nothing(
        self, settings: Settings, database: Database
    ) -> None:
        await reset(settings)
        before = scalar(settings.database_url, "SELECT count(*) FROM scan_runs")
        transcript = ReferenceEstateDataset().generations()[0].transcripts[0]
        async with _client(settings, database) as client:
            response = await client.post("/api/v1/scan-runs", json=restamp(transcript).start)

        assert response.status_code == 403
        assert response.json()["status"] == "demo_only"
        assert scalar(settings.database_url, "SELECT count(*) FROM scan_runs") == before

    async def test_the_demo_overview(self, settings: Settings, database: Database) -> None:
        await reset(settings)
        async with _client(settings, database, Role.VIEWER) as client:
            body = (await client.get("/api/v1/demo")).json()

        assert body["dataset"]["key"] == "adg-reference-estate"
        assert [(g["name"], g["applied"]) for g in body["generations"]] == [("baseline", True)]
        assert body["baseline_generations"] == 1
        assert body["next_generation"] is None
        assert body["scenarios"] == []
        treatments = {p["path"]: p["treatment"] for p in body["mutation_policy"]}
        assert treatments["/api/v1/scan-runs"] == "unavailable"

    async def test_a_dataset_with_no_further_generation_offers_no_scan(
        self, settings: Settings, database: Database
    ) -> None:
        await reset(settings)
        async with _client(settings, database) as client:
            response = await client.post("/api/v1/demo/scans")

        assert response.status_code == 409
        assert "has been applied" in response.json()["detail"]

    async def test_a_viewer_cannot_run_a_synthetic_scan(
        self, settings: Settings, database: Database
    ) -> None:
        await reset(settings)
        async with _client(settings, database, Role.VIEWER) as client:
            assert (await client.post("/api/v1/demo/scans")).status_code == 403


# --- The synthetic-scan hook, with a test-only second generation ----------------------------

SCAN_NAMESPACE = uuid.UUID("6b1c3c9e-6d55-4d0e-9a3f-2a8f4f6e0d11")


class _TwoGenerations:
    """The reference estate, plus its baseline re-posted under fixed run ids as a 'rescan'."""

    def __init__(self) -> None:
        self._reference = ReferenceEstateDataset()

    @property
    def metadata(self) -> DemoDatasetMetadata:
        base = self._reference.metadata
        return DemoDatasetMetadata(
            key="two-generation-fixture",
            company_name=base.company_name,
            domain=base.domain,
            snapshot_date=base.snapshot_date,
            seed=base.seed,
            description="Test fixture: the reference estate and one rescan.",
        )

    def generations(self) -> Sequence[ScanGeneration]:
        baseline = self._reference.generations()[0]
        rescan = tuple(
            restamp(t, run_id=str(uuid.uuid5(SCAN_NAMESPACE, t.name)))
            for t in baseline.transcripts
            if t.status == "succeeded"
        )
        return (baseline, ScanGeneration("rescan", "Every successful run, again.", rescan))

    def probes(self) -> Sequence[CanonicalProbe]:
        return self._reference.probes()


@pytest.fixture
def two_generation_dataset(monkeypatch: pytest.MonkeyPatch) -> str:
    module = types.ModuleType("tests_demo_two_generations")
    module.build = _TwoGenerations  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(
        datasets.DATASETS,
        "two-generation-fixture",
        DatasetEntry("two-generation-fixture", f"{module.__name__}:build", "fixture"),
    )
    datasets.cached_dataset.cache_clear()
    return "two-generation-fixture"


class TestSyntheticScan:
    async def test_the_next_generation_is_applied_through_the_real_routes(
        self, demo_database: str, two_generation_dataset: str
    ) -> None:
        settings = _settings(demo_database, dataset=two_generation_dataset)
        baseline = await reset(settings)
        database = Database(settings)
        try:
            async with _client(settings, database) as client:
                first = await client.post("/api/v1/demo/scans")
                overview = (await client.get("/api/v1/demo")).json()
                second = await client.post("/api/v1/demo/scans")
            async with database.engine.connect() as connection:
                after = await canonical_fingerprint(connection)
        finally:
            await database.dispose()

        assert first.status_code == 201, first.text
        assert first.json()["generation"] == "rescan"
        assert [g["applied"] for g in overview["generations"]] == [True, True]
        assert second.status_code == 409
        assert after != baseline.fingerprint
        assert scalar(demo_database, "SELECT count(*) FROM scan_runs") == len(
            baseline.seed.runs
        ) + len(first.json()["runs"])

    async def test_reset_returns_to_the_baseline_after_a_scan(
        self, demo_database: str, two_generation_dataset: str
    ) -> None:
        settings = _settings(demo_database, dataset=two_generation_dataset)
        baseline = await reset(settings)
        database = Database(settings)
        try:
            async with _client(settings, database) as client:
                assert (await client.post("/api/v1/demo/scans")).status_code == 201
        finally:
            await database.dispose()

        assert (await reset(settings)).fingerprint == baseline.fingerprint


class TestResettingToAPointInHistory:
    """``reset --generations N``: the dataset's own history, rewound, for ``scan`` to replay."""

    async def test_a_reset_can_install_more_than_the_baseline(
        self, demo_database: str, two_generation_dataset: str
    ) -> None:
        settings = _settings(demo_database, dataset=two_generation_dataset)
        report = await reset_demo(settings, migrate=False, generations=2)

        assert [item.name for item in report.applied] == ["baseline", "rescan"]
        assert report.probes_checked and report.ok
        assert report.canonical
        status = await demo_status(settings)
        assert [g.applied for g in status.generations] == [True, True]
        assert status.baseline_applied

    async def test_a_reset_refuses_a_generation_the_dataset_does_not_have(
        self, demo_database: str, two_generation_dataset: str
    ) -> None:
        settings = _settings(demo_database, dataset=two_generation_dataset)
        for wrong in (0, 3):
            with pytest.raises(DemoLifecycleError, match="can apply 1 to 2"):
                await reset_demo(settings, migrate=False, generations=wrong)
        assert scalar(demo_database, "SELECT count(*) FROM deployment_marker") == 0

    async def test_scanning_forward_equals_resetting_to_the_same_point(
        self, demo_database: str, two_generation_dataset: str
    ) -> None:
        settings = _settings(demo_database, dataset=two_generation_dataset)
        await reset_demo(settings, migrate=False, generations=1)
        database = Database(settings)
        try:
            dataset = load_dataset(two_generation_dataset)
            scanned = await run_synthetic_scan(settings, database, dataset)
            async with database.engine.connect() as connection:
                after_scan = await canonical_digests(connection)
        finally:
            await database.dispose()
        direct = await reset_demo(settings, migrate=False, generations=2)

        assert scanned.generation.name == "rescan"
        assert {k: v for k, v in after_scan.items() if k != "risk_findings"} == {
            k: v for k, v in direct.digests.items() if k != "risk_findings"
        }
