r"""Collection coverage beside risk totals, against a real PostgreSQL (ADG-DEMO-03).

The pure rules are in ``tests/domain/test_coverage.py``. What is pinned here is what only
exists once they meet the database and the API:

* the per-scope states come out of real run records -- unavailable from a collector's own
  ``host_unreachable``, stale evidence from an earlier success, "found nothing" from a clean
  scan that reported no objects;
* a risk total carries its collection context and its snapshot, and reads as a total only
  when the evaluation was full, collection was complete, and the snapshot has not moved;
* **a source that fails does not turn its findings into zero**: they stay open, counted,
  and attributed to the scan that last read them.

The estate is :mod:`app.demo.estate` ("small"), replayed through the ingestion endpoints. It
has an unreachable server (FS03) and a partly unreadable one (FS02) by design; the tests that
need complete coverage seed only the FS01 runs and the directory.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, build_settings
from app.db import Database
from app.demo.estate import DemoEstate, build_estate
from app.demo.seed import seed_transcripts
from app.demo.transcripts import DemoTranscript, build_transcripts
from app.main import create_app
from app.models.schema import RiskFindingStatus, risk_evaluations, risk_findings
from app.repositories.basis import CollectionBasisRepository
from app.repositories.risk import RiskFactsRepository, RiskFindingRepository
from app.risk_engine import DEFAULT_CONFIGURATION
from app.services.risk import RiskService
from tests.support.auth import auth_headers

pytestmark = pytest.mark.anyio

NOW = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)
LATER = NOW + dt.timedelta(hours=2)
FS01_FOLDER = r"\\FS01\Finance"
FS03_FOLDER = r"\\FS03\Archive"
CLEAN = {"ad", "local-groups-fs01", "smb-fs01", "ntfs-fs01"}


@pytest.fixture(scope="module")
def estate() -> DemoEstate:
    return build_estate("small")


def transcripts(estate: DemoEstate, names: set[str] | None = None) -> list[DemoTranscript]:
    return [item for item in build_transcripts(estate) if names is None or item.name in names]


@pytest.fixture
async def whole(client: AsyncClient, estate: DemoEstate) -> None:
    """Every run of the small estate: FS03 unreachable, FS02 partial."""
    await seed_transcripts(client, transcripts(estate))


@pytest.fixture
async def clean(client: AsyncClient, estate: DemoEstate) -> None:
    """The directory and FS01 only: every scope that has reported was read completely."""
    await seed_transcripts(client, transcripts(estate, CLEAN))


async def bare_run(
    client: AsyncClient,
    *,
    collector: str,
    target: str,
    started_at: dt.datetime,
    status: str = "succeeded",
    code: str | None = None,
) -> None:
    """A run with no observations: a failed one, or a clean one that found nothing."""
    run_id = str(uuid.uuid4())
    errors = (
        [{"code": code, "message": f"{code} reading {target}", "target": f"\\\\{target}"}]
        if code
        else []
    )
    stamp = started_at.isoformat().replace("+00:00", "Z")
    started = await client.post(
        "/api/v1/scan-runs",
        json={
            "schema_version": "1.0",
            "run_id": run_id,
            "source": {
                "collector": collector,
                "collector_host": target,
                "method": "test",
                "collector_version": "0.1.0",
                "target": target,
            },
            "started_at": stamp,
            "scopes": [{"kind": "server", "key": target.casefold()}],
            "incremental": False,
        },
    )
    assert started.status_code in (200, 201), started.text
    completed = await client.post(
        f"/api/v1/scan-runs/{run_id}/completion",
        json={
            "schema_version": "1.0",
            "run_id": run_id,
            "status": status,
            "completed_at": stamp,
            "batch_count": 0,
            "observation_count": 0,
            "error_count": len(errors),
            "errors": errors,
            # A clean run reconciles what it declared; anything else never claims to.
            "reconciled_scopes": (
                [{"kind": "server", "key": target.casefold()}]
                if status == "succeeded" and not errors
                else []
            ),
        },
    )
    assert completed.status_code == 200, completed.text


async def evaluate(database: Database, now: dt.datetime = NOW) -> None:
    async with database.session() as session:
        await RiskService(
            RiskFactsRepository(session), RiskFindingRepository(session), DEFAULT_CONFIGURATION
        ).evaluate_estate(now=now)
        await session.commit()


async def open_findings_on(session: AsyncSession, server: str) -> set[str]:
    prefix = f"\\\\{server.casefold()}\\"
    rows = await session.execute(
        sa.select(risk_findings.c.finding_key).where(
            risk_findings.c.status == RiskFindingStatus.OPEN.value,
            sa.or_(
                sa.func.starts_with(risk_findings.c.resource_key, prefix),
                sa.func.starts_with(risk_findings.c.share_key, f"{server.casefold()}|"),
            ),
        )
    )
    return {str(row[0]) for row in rows}


def scope_of(body: dict[str, Any], label: str) -> dict[str, Any]:
    return next(item for item in body["scopes"] if item["label"] == label)


class TestTheCoverageReport:
    async def test_every_scope_reported_is_stated_with_its_state(
        self, client: AsyncClient, whole: None
    ) -> None:
        body = (await client.get("/api/v1/collection/coverage")).json()

        assert body["verdict"] == "incomplete"
        assert body["complete"] is False
        assert body["counts"] == {
            "scopes": 8,
            "collected": 6,
            "partial": 1,
            "unavailable": 1,
            "failed": 0,
            "running": 0,
            "stale": 0,
            "never_collected": 1,
            "returned_nothing": 0,
        }
        assert body["headline"] == (
            "6 of 8 collection scopes collected completely · 1 unavailable · 1 partial"
        )
        assert body["health"] == "failed"
        assert body["snapshot"]["runs"] == 8

    async def test_an_unreachable_server_is_unavailable_and_never_found_nothing(
        self, client: AsyncClient, whole: None
    ) -> None:
        body = (await client.get("/api/v1/collection/coverage")).json()
        fs03 = scope_of(body, "ntfs (FS03)")

        assert fs03["state"] == "unavailable"
        assert fs03["evidence"] == "none"
        assert fs03["failure_codes"] == ["host_unreachable"]
        assert fs03["observations"] == 0
        assert fs03["returned_nothing"] is False
        assert "could not reach" in fs03["sentence"]

    async def test_a_partly_unreadable_server_is_partial(
        self, client: AsyncClient, whole: None
    ) -> None:
        body = (await client.get("/api/v1/collection/coverage")).json()
        fs02 = scope_of(body, "ntfs (FS02)")

        assert fs02["state"] == "partial"
        assert fs02["evidence"] == "current"

    async def test_a_clean_scan_that_found_nothing_is_collected_and_says_so(
        self, client: AsyncClient, clean: None
    ) -> None:
        """The only zero that means 'checked, and nothing is there'."""
        await bare_run(client, collector="ntfs", target="FS09", started_at=LATER)

        body = (await client.get("/api/v1/collection/coverage")).json()
        fs09 = scope_of(body, "ntfs (FS09)")

        assert fs09["state"] == "collected"
        assert fs09["returned_nothing"] is True
        assert "found nothing" in fs09["sentence"]
        assert body["complete"] is True
        assert body["counts"]["returned_nothing"] == 1

    async def test_a_server_that_failed_after_a_success_holds_stale_evidence(
        self, client: AsyncClient, clean: None
    ) -> None:
        await bare_run(
            client,
            collector="ntfs",
            target="FS01",
            started_at=LATER,
            status="failed",
            code="host_unreachable",
        )

        body = (await client.get("/api/v1/collection/coverage")).json()
        fs01 = scope_of(body, "ntfs (FS01)")

        assert fs01["state"] == "unavailable"
        assert fs01["evidence"] == "stale"
        assert fs01["evidence_collected_at"] is not None
        assert fs01["evidence_collected_at"] < fs01["attempted_at"]
        assert "anything that changed since is not reflected" in fs01["sentence"]
        assert body["counts"]["stale"] == 1
        assert body["verdict"] == "incomplete"

    async def test_a_refused_collector_is_failed_rather_than_unavailable(
        self, client: AsyncClient, clean: None
    ) -> None:
        await bare_run(
            client,
            collector="smb",
            target="FS01",
            started_at=LATER,
            status="failed",
            code="access_denied",
        )

        body = (await client.get("/api/v1/collection/coverage")).json()

        assert scope_of(body, "smb (FS01)")["state"] == "failed"
        assert body["counts"]["failed"] == 1

    async def test_without_an_expectation_a_missing_kind_is_not_configured(
        self, client: AsyncClient, estate: DemoEstate
    ) -> None:
        await seed_transcripts(client, transcripts(estate, {"ad"}))

        body = (await client.get("/api/v1/collection/coverage")).json()

        assert body["expectation"] == "none"
        states = {item["collector"]: item["state"] for item in body["collectors"]}
        assert states == {
            "active_directory": "reported",
            "local_groups": "not_configured",
            "ntfs": "not_configured",
            "smb": "not_configured",
        }
        assert body["complete"] is True


@pytest.fixture
async def expecting_ntfs(database: Database, migrated_database: str) -> AsyncIterator[AsyncClient]:
    """A client for a deployment that declares it runs the directory and NTFS collectors."""
    settings: Settings = build_settings(
        environment="test",
        log_format="text",
        database_url=migrated_database,
        expected_collectors="active_directory,ntfs",
    )
    app = create_app(settings)
    app.state.database = database
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=auth_headers(settings),
    ) as client:
        yield client


class TestAnExpectedCollectorThatNeverReported:
    async def test_is_not_collected_and_makes_coverage_incomplete(
        self, expecting_ntfs: AsyncClient, estate: DemoEstate
    ) -> None:
        await seed_transcripts(expecting_ntfs, transcripts(estate, {"ad"}))

        body = (await expecting_ntfs.get("/api/v1/collection/coverage")).json()

        assert body["expectation"] == "configured"
        ntfs = next(item for item in body["collectors"] if item["collector"] == "ntfs")
        assert ntfs == {
            "collector": "ntfs",
            "expected": True,
            "state": "not_collected",
            "scopes": 0,
            "collected": 0,
        }
        assert body["verdict"] == "incomplete"
        assert "never reported: ntfs" in body["headline"]


class TestTheScopesOneAnswerReads:
    async def test_an_answer_on_a_clean_server_cannot_be_changed_by_the_others(
        self, client: AsyncClient, whole: None
    ) -> None:
        body = (
            await client.get("/api/v1/collection/coverage", params={"resource": FS01_FOLDER})
        ).json()
        relevant = body["relevant"]

        assert relevant["server"] == "FS01"
        assert sorted(item["label"] for item in relevant["scopes"]) == [
            "active_directory (corp.example.com)",
            "local_groups (FS01)",
            "ntfs (FS01)",
            "smb (FS01)",
        ]
        assert relevant["complete"] is True
        assert relevant["could_change_answer"] is False
        assert relevant["incomplete_elsewhere"] == 2
        assert "cannot change this answer" in relevant["statement"]

    async def test_an_answer_on_the_unreachable_server_could_be_changed(
        self, client: AsyncClient, whole: None
    ) -> None:
        body = (
            await client.get("/api/v1/collection/coverage", params={"resource": FS03_FOLDER})
        ).json()
        relevant = body["relevant"]

        assert relevant["complete"] is False
        assert relevant["could_change_answer"] is True
        assert "could not reach its target" in relevant["statement"]

    async def test_without_a_resource_there_is_no_relevant_block(
        self, client: AsyncClient, whole: None
    ) -> None:
        body = (await client.get("/api/v1/collection/coverage")).json()
        assert body["relevant"] is None

    async def test_five_statements_whatever_the_estate_holds(
        self, client: AsyncClient, whole: None, database: Database
    ) -> None:
        statements: list[str] = []

        def count(*args: Any) -> None:
            statements.append(str(args[2]))

        engine = database.engine.sync_engine
        sa.event.listen(engine, "before_cursor_execute", count)
        try:
            response = await client.get(
                "/api/v1/collection/coverage", params={"resource": FS01_FOLDER}
            )
        finally:
            sa.event.remove(engine, "before_cursor_execute", count)

        assert response.status_code == 200
        assert len(statements) == 5


class TestRiskTotalsCarryTheirCollectionContext:
    async def test_a_full_pass_over_complete_current_collection_reads_as_totals(
        self, client: AsyncClient, clean: None, database: Database
    ) -> None:
        await evaluate(database)

        body = (await client.get("/api/v1/risks/summary")).json()

        assert body["interpretation"]["reading"] == "complete"
        assert body["interpretation"]["exhaustive"] is True
        assert body["interpretation"]["basis_current"] is True
        assert body["interpretation"]["reasons"] == []
        assert body["interpretation"]["held"] == []
        assert body["collection"]["complete"] is True
        assert body["collection"]["snapshot"]["token"] == body["interpretation"]["evaluated_basis"]

    async def test_incomplete_collection_makes_the_same_counts_not_exhaustive(
        self, client: AsyncClient, whole: None, database: Database
    ) -> None:
        await evaluate(database)

        body = (await client.get("/api/v1/risks/summary")).json()

        assert body["total"] > 0
        assert body["interpretation"]["reading"] == "incomplete"
        assert body["interpretation"]["exhaustive"] is False
        assert "1 unavailable" in body["interpretation"]["reasons"][0]
        assert body["collection"]["verdict"] == "incomplete"
        held = {item["scope"]: item for item in body["interpretation"]["held"]}
        assert set(held) == {"ntfs (FS02)", "ntfs (FS03)"}
        assert held["ntfs (FS03)"]["evidence"] == "none"

    async def test_the_evaluation_records_the_snapshot_it_read(
        self, client: AsyncClient, clean: None, database: Database, session: AsyncSession
    ) -> None:
        current = await CollectionBasisRepository(session).current()
        await evaluate(database)

        recorded = (await session.execute(sa.select(risk_evaluations.c.basis_token))).scalar_one()

        assert recorded == current.token

    async def test_collection_that_moved_after_the_evaluation_makes_the_totals_stale(
        self, client: AsyncClient, clean: None, database: Database
    ) -> None:
        await evaluate(database)
        await bare_run(client, collector="smb", target="FS01", started_at=LATER)

        body = (await client.get("/api/v1/risks/summary")).json()

        assert body["interpretation"]["reading"] == "stale"
        assert body["interpretation"]["basis_current"] is False
        assert body["collection"]["snapshot"]["token"] != body["interpretation"]["evaluated_basis"]

    async def test_a_viewer_without_collector_access_still_gets_the_reading(
        self, client: AsyncClient, client_as: Any, whole: None, database: Database
    ) -> None:
        """The reading is computed from coverage either way; the detail needs the capability.

        Every shipped role holds collectors:read, so this pins the shape for a role that
        might not: an interpretation with its reasons, and no coverage block.
        """
        await evaluate(database)
        from app.auth.roles import Role

        async with client_as(Role.VIEWER) as viewer:
            body = (await viewer.get("/api/v1/risks/summary")).json()

        assert body["interpretation"]["reading"] == "incomplete"


class TestAFailedSourceDoesNotLowerRisk:
    async def test_findings_on_a_server_whose_scan_failed_stay_open_and_counted(
        self, client: AsyncClient, clean: None, database: Database, session: AsyncSession
    ) -> None:
        await evaluate(database, NOW)
        before = await open_findings_on(session, "FS01")
        assert before, "the clean FS01 estate should produce findings"

        await bare_run(
            client,
            collector="ntfs",
            target="FS01",
            started_at=LATER,
            status="failed",
            code="host_unreachable",
        )
        await evaluate(database, LATER + dt.timedelta(hours=1))
        await session.rollback()

        after = await open_findings_on(session, "FS01")
        assert after == before

        body = (await client.get("/api/v1/risks/summary")).json()
        held = {item["scope"]: item for item in body["interpretation"]["held"]}
        assert held["ntfs (FS01)"]["evidence"] == "stale"
        assert held["ntfs (FS01)"]["open_findings"] == len(before)
        assert body["interpretation"]["reading"] == "incomplete"
        assert body["interpretation"]["exhaustive"] is False
