r"""Null Meridian against a real PostgreSQL: the reset, the history, and the golden scenarios.

The reset runs once for this module, the way an operator runs it: migrations, the three scan
generations posted through the ingestion endpoints, and a full risk evaluation. Everything
below is then asked of the product through its own API or its own current-state views — never
of the generator — because the claim being tested is that **the product** holds the estate
the canon describes.

A second reset at the end proves the operation is repeatable and produces the same logical
database, byte for byte through the fingerprint.

These tests use their own database, ``<dev database>_demo_test``: the reset refuses any
database whose name does not say ``demo``, which is the point.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import urllib.parse
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.config import Settings, build_settings
from app.db import Database
from app.demo.null_meridian.canon import SNAPSHOT, Canon, build_canon
from app.demo.null_meridian.estate import build_estate
from app.demo.null_meridian.generations import GENERATIONS, TRANSCRIPT_NAMESPACE
from app.demo.null_meridian.reset import (
    DEMO_MARKER,
    DemoResetRefused,
    ResetReport,
    require_demo,
    reset_demo,
)
from app.main import create_app
from app.models.current import current_ntfs_resources, current_principals
from tests.db.conftest import BASE_DATABASE_URL, _render
from tests.support.auth import auth_headers

G1, G2, G3 = (generation.day for generation in GENERATIONS)


def _demo_database_url() -> str:
    url = sa.engine.make_url(BASE_DATABASE_URL)
    return _render(url.set(database=f"{url.database}_demo_test"))


DEMO_URL = _demo_database_url()


def _ensure_database(url: str) -> None:
    parsed = sa.engine.make_url(url)
    admin = sa.create_engine(_render(parsed.set(database="postgres")), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": parsed.database},
            ).scalar_one_or_none()
            if exists is None:
                connection.execute(sa.text(f'CREATE DATABASE "{parsed.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="module")
def demo_settings() -> Settings:
    _ensure_database(DEMO_URL)
    return build_settings(
        environment="test", log_format="text", runtime_mode="demo", database_url=DEMO_URL
    )


@pytest.fixture(scope="module")
def first_reset(demo_settings: Settings) -> ResetReport:
    return asyncio.run(reset_demo(demo_settings, migrate=True))


@pytest.fixture(scope="module")
def engine(first_reset: ResetReport) -> Iterator[sa.Engine]:
    instance = sa.create_engine(DEMO_URL)
    try:
        yield instance
    finally:
        instance.dispose()


@pytest.fixture
async def api(demo_settings: Settings, first_reset: ResetReport) -> AsyncIterator[AsyncClient]:
    app = create_app(demo_settings)
    database = Database(demo_settings)
    app.state.database = database
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=auth_headers(demo_settings),
        ) as client:
            yield client
    finally:
        await database.dispose()


@pytest.fixture(scope="module")
def canon() -> Canon:
    return build_canon()


def _dicts(value: Any) -> Iterator[dict[str, Any]]:
    """Every mapping anywhere inside a decoded JSON document."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _dicts(item)


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _current_paths(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {row.path for row in connection.execute(sa.select(current_ntfs_resources.c.path))}


def _current_sids(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {row.sid for row in connection.execute(sa.select(current_principals.c.sid))}


def _stored_sids(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {row[0] for row in connection.execute(sa.text("SELECT sid FROM principals"))}


class TestTheReset:
    def test_every_run_was_accepted_with_the_status_it_claimed(
        self, first_reset: ResetReport
    ) -> None:
        assert first_reset.runs == 300
        assert first_reset.recorded_statuses == {"failed": 2, "partial": 1, "succeeded": 297}

    def test_the_risk_pass_covered_the_whole_estate(self, first_reset: ResetReport) -> None:
        assert first_reset.risk["complete"] is True
        assert first_reset.risk["truncated"] == []
        assert first_reset.risk["opened"] > 0

    def test_the_database_is_marked_as_demo_owned(self, engine: sa.Engine) -> None:
        with engine.connect() as connection:
            comment = connection.execute(
                sa.text(
                    "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                    "WHERE datname = current_database()"
                )
            ).scalar_one()
        assert comment == DEMO_MARKER

    async def test_exactly_1800_active_employees_are_enabled_in_adg(
        self, engine: sa.Engine, canon: Canon
    ) -> None:
        with engine.connect() as connection:
            enabled = connection.execute(
                sa.select(sa.func.count())
                .select_from(current_principals)
                .where(
                    current_principals.c.principal_kind == "user",
                    current_principals.c.enabled.is_(True),
                )
            ).scalar_one()
            upns = {
                row[0]
                for row in connection.execute(
                    sa.select(current_principals.c.user_principal_name).where(
                        current_principals.c.principal_kind == "user"
                    )
                )
            }
        assert enabled == 1800
        assert all(upn.endswith("@nullmeridian.info") for upn in upns)
        assert {person.email for person in canon.active} <= upns


class TestCurrentStateSemantics:
    def test_a_reconciled_scan_tombstones_what_it_no_longer_saw(
        self, engine: sa.Engine, canon: Canon
    ) -> None:
        scratch = r"\\NM-ENG-FS02\QA\Sprint-Scratch"
        with engine.connect() as connection:
            stored = {
                row[0] for row in connection.execute(sa.text("SELECT path FROM ntfs_resources"))
            }
            tombstone = connection.execute(
                sa.text(
                    "SELECT count(*) FROM object_versions WHERE object_kind = 'ntfs_resource' "
                    "AND object_key = :key AND valid_to IS NULL AND NOT is_present"
                ),
                {"key": scratch.casefold()},
            ).scalar_one()
        assert scratch in stored, "history keeps the row"
        assert scratch not in _current_paths(engine), "current state does not"
        assert tombstone == 1

    def test_deleted_groups_and_accounts_leave_current_state(
        self, engine: sa.Engine, canon: Canon
    ) -> None:
        vega = canon.group("Project-Vega").sid
        crossing = [
            p.sid
            for p in canon.employees
            if p.account_state(G1) != "deleted" and p.account_state(G2) == "deleted"
        ]
        assert crossing
        current, stored = _current_sids(engine), _stored_sids(engine)
        for sid in [vega, *crossing]:
            assert sid in stored and sid not in current, sid

    def test_a_failed_scan_removes_nothing(self, engine: sa.Engine) -> None:
        """NM-FS-03's NTFS scan failed in g3. Everything g2 saw there is still current."""
        seen_in_g2 = {d.path for d in build_estate(G2).estate.directories_on("NM-FS-03")}
        assert seen_in_g2
        assert seen_in_g2 <= _current_paths(engine)

    def test_a_partial_scan_removes_nothing_even_what_is_really_gone(
        self, engine: sa.Engine
    ) -> None:
        """Wave-3-Staging was deleted on 2026-09-25. The g3 scan of NM-MIG-FS01 was partial,
        so it may not say so, and ADG keeps the folder until a complete scan does."""
        staging = r"\\NM-MIG-FS01\Migrations\Wave-3-Staging"
        restricted = r"\\NM-MIG-FS01\Migrations\FS-Consolidation-2020\Restricted"
        assert staging not in {d.path for d in build_estate(G3).estate.directories}
        current = _current_paths(engine)
        assert staging in current
        assert restricted in current

    def test_current_state_is_exactly_what_the_scans_justify(self, engine: sa.Engine) -> None:
        g2 = build_estate(G2).estate
        g3 = build_estate(G3).estate
        expected = {d.path for d in g3.directories if d.server_name != "NM-FS-03"}
        expected |= {d.path for d in g2.directories_on("NM-FS-03")}
        expected |= {d.path for d in g2.directories_on("NM-MIG-FS01")}
        assert _current_paths(engine) == expected

    def test_a_server_first_read_in_g2_was_first_observed_then(self, engine: sa.Engine) -> None:
        import uuid

        def run(name: str) -> str:
            return str(uuid.uuid5(TRANSCRIPT_NAMESPACE, f"run:{name}"))

        with engine.connect() as connection:
            first = {
                row.path: str(row.first_observed_run_id)
                for row in connection.execute(
                    sa.text(
                        "SELECT path, first_observed_run_id FROM ntfs_resources "
                        "WHERE server_key = 'nm-sec-fs01'"
                    )
                )
            }
        incident = r"\\NM-SEC-FS01\Incidents\IR-2026-014"
        assert first.pop(incident) == run("g3-ntfs-nm-sec-fs01"), "created after g2"
        assert set(first.values()) == {run("g2-ntfs-nm-sec-fs01")}, "g1 could not read it"


class TestTheGoldenScenariosThroughTheApi:
    async def test_f_both_routes_are_explained(self, api: AsyncClient, canon: Canon) -> None:
        mover = canon.one("golden:cross-department")
        path = r"\\NM-FIN-FS01\Finance\Reports\Revenue"
        response = await api.get(
            f"/api/v1/access/paths/principals/{mover.sid}/resources/{_q(path)}"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        granted = body["effective"]["ntfs"]["granted_by"]
        names = {(entry["trustee"]["display_name"], entry["via_group"]) for entry in granted}
        assert ("Revenue-Reporting", True) in names
        assert (mover.display_name, False) in names
        assert body["effective"]["rights"]["label"] == "Modify"

    async def test_g_the_six_group_chain(self, api: AsyncClient, canon: Canon) -> None:
        person = canon.one("golden:deep-chain")
        path = r"\\NM-ENG-FS01\Engineering\Runbooks\Payments-Legacy"
        response = await api.get(
            f"/api/v1/access/paths/principals/{person.sid}/resources/{_q(path)}"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["complete"] is True
        chains = [
            [node["display_name"] for node in item["chain"]]
            for item in body["paths"]
            if item["layer"] == "ntfs"
        ]
        documented = [
            person.display_name,
            "ENG-Payments-Oncall",
            "ENG-Payments",
            "Engineering-Backend",
            "Engineering",
            "Engineering-FS",
            "Share_Engineering_Modify",
        ]
        assert documented in chains, chains
        # And the detour through the application group that became a share dependency.
        assert max(len(chain) for chain in chains) == 8
        assert all(chain[-1] == "Share_Engineering_Modify" for chain in chains)

    async def test_h_the_empty_group_is_a_finding(self, engine: sa.Engine, canon: Canon) -> None:
        with engine.connect() as connection:
            keys = {
                row[0]
                for row in connection.execute(
                    sa.text(
                        "SELECT principal_key FROM risk_findings "
                        "WHERE rule_id = 'empty_permission_bearing_group' AND status = 'open'"
                    )
                )
            }
        assert canon.group("Proj-Zephyr-RW").sid in keys

    async def test_h_a_simulation_confirms_the_cleanup_changes_nobody(
        self, api: AsyncClient, canon: Canon
    ) -> None:
        zephyr = r"\\NM-ENG-FS01\Engineering\Projects\Zephyr"
        sid = canon.group("Proj-Zephyr-RW").sid
        acl = await api.get(f"/api/v1/resources/{_q(zephyr)}/acl")
        assert acl.status_code == 200, acl.text
        keys = [
            entry["ace_key"]
            for entry in _dicts(acl.json())
            if "ace_key" in entry and sid in json.dumps(entry)
        ]
        assert len(keys) == 1, keys

        response = await api.post(
            "/api/v1/simulations/preview",
            json={
                "changes": [
                    {
                        "kind": "remove_ntfs_ace",
                        "resource_key": zephyr.casefold(),
                        "ace_key": keys[0],
                    }
                ]
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["applied"] is False
        assert body["summary"]["lost_access"] == 0
        assert body["summary"]["reduced"] == 0
        assert body["summary"]["changed"] == 0

    async def test_a_the_design_share_names_no_person(self, api: AsyncClient, canon: Canon) -> None:
        design = r"\\NM-CORP-FS02\Design"
        response = await api.get(f"/api/v1/resources/{_q(design)}/acl")
        assert response.status_code == 200, response.text
        text = response.text
        assert "sg-share-design-write" in text
        people = {person.sid for person in canon.employees}
        assert not any(sid in text for sid in people)

    def test_the_risk_report_has_every_kind_of_debt(self, engine: sa.Engine) -> None:
        with engine.connect() as connection:
            rules = {
                row[0]
                for row in connection.execute(
                    sa.text("SELECT DISTINCT rule_id FROM risk_findings WHERE status = 'open'")
                )
            }
        assert {
            "direct_user_ace",
            "disabled_principal_retains_access",
            "unresolved_sid_on_acl",
            "broken_inheritance",
            "deep_group_nesting",
            "redundant_access_paths",
            "empty_permission_bearing_group",
            "broad_access_on_sensitive_resource",
        } <= rules


class TestTheBoundary:
    def test_a_standard_deployment_is_refused(self) -> None:
        with pytest.raises(DemoResetRefused, match="ADG_RUNTIME_MODE"):
            require_demo(build_settings(environment="test"))

    def test_an_unmarked_database_with_data_is_refused(
        self, demo_settings: Settings, engine: sa.Engine
    ) -> None:
        with engine.connect() as connection:
            name = connection.execute(sa.text("SELECT current_database()")).scalar_one()
        quoted = '"' + name + '"'
        with engine.begin() as connection:
            connection.execute(sa.text(f"COMMENT ON DATABASE {quoted} IS NULL"))
        try:
            with pytest.raises(DemoResetRefused, match="never marked"):
                asyncio.run(reset_demo(demo_settings, migrate=False))
            with engine.connect() as connection:
                runs = connection.execute(sa.text("SELECT count(*) FROM scan_runs")).scalar_one()
            assert runs == 300, "a refused reset changes nothing"
        finally:
            with engine.begin() as connection:
                connection.execute(sa.text(f"COMMENT ON DATABASE {quoted} IS '{DEMO_MARKER}'"))


class TestRepeatability:
    def test_a_second_reset_reproduces_the_canonical_database(
        self,
        demo_settings: Settings,
        first_reset: ResetReport,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """…and does it without opening a socket from Python or running anything but the
        migration. PostgreSQL is reached through libpq, beneath Python's socket module, so
        any Python-level connection attempt during the reset is something else entirely."""
        commands: list[list[str]] = []
        original_run = subprocess.run

        def recording_run(argv: list[str], *args: Any, **kwargs: Any) -> Any:
            commands.append(list(argv))
            return original_run(argv, *args, **kwargs)

        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"The demo reset tried to open a network connection: {args}")

        monkeypatch.setattr(subprocess, "run", recording_run)
        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)

        second = asyncio.run(reset_demo(demo_settings, migrate=True))

        assert second.fingerprint == first_reset.fingerprint
        assert second.table_digests == first_reset.table_digests
        assert second.counts == first_reset.counts
        assert second.observations_applied == first_reset.observations_applied
        assert all(argv[1:4] == ["-m", "alembic", "upgrade"] for argv in commands), commands
        assert not any(
            "pwsh" in " ".join(argv) or "powershell" in " ".join(argv).casefold()
            for argv in commands
        )


def test_the_snapshot_constant_is_the_last_generation() -> None:
    assert GENERATIONS[-1].day == SNAPSHOT
