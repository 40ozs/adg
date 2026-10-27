r"""Genesee Press through Demo Mode against a real PostgreSQL: reset, history, scenarios.

The reset runs once for this module, the way an operator runs it -- ``app.demo_mode``'s
canonical reset with the default dataset: migrations, the deployment marker, the three scan
generations posted through the ingestion endpoints, a full risk evaluation and the canonical
probes. Everything below is then asked of the product through its own API or its own
current-state views -- never of the generator -- because the claim being tested is that
**the product** holds the estate the canon describes.

Then the history is replayed through the synthetic scan (reset to the first generation, scan
forward twice), and a second full reset at the end proves the operation is repeatable and
produces the same logical database, byte for byte through the fingerprint.

These tests use their own database, ``<dev database>_nm_demo_test``, dropped and recreated
once per module (empty and unmarked, as a new demo database is): the reset refuses any
database whose name does not say ``demo``, which is the point, and the Demo Mode suite's own
database is left alone.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import socket
import subprocess
import urllib.parse
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.auth.roles import Role
from app.config import Settings, build_settings
from app.db import Database
from app.demo.genesee_press.canon import SNAPSHOT, Canon, build_canon
from app.demo.genesee_press.estate import build_estate
from app.demo.genesee_press.generations import GENERATIONS, TRANSCRIPT_NAMESPACE
from app.demo.genesee_press.scenarios import investigations
from app.demo_mode.datasets import CANONICAL_DATASET, load_dataset
from app.demo_mode.lifecycle import (
    DemoLifecycleError,
    ResetReport,
    canonical_digests,
    demo_status,
    reset_demo,
    run_synthetic_scan,
    verify_demo,
)
from app.demo_mode.marker import DemoTargetRefused
from app.main import create_app
from app.models.current import current_ntfs_resources, current_principals
from tests.db.conftest import BASE_DATABASE_URL, _render
from tests.support.auth import auth_headers, token_for_roles

G1, G2, G3 = (generation.day for generation in GENERATIONS)
MANIFEST = Path(__file__).resolve().parents[3] / "docs" / "demo" / "genesee-press.manifest.json"
#: The Genesee Press branch's pre-integration reset evaluated risk at this instant; the
#: dataset contract now carries it as the last generation's ``evaluated_at``.
CANONICAL_RISK_PASS = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.UTC)
#: Sources of the canonical fingerprint that do not depend on when risks were evaluated.
STATE_SOURCES_EXCLUDING_RISK = "risk_findings"


def _demo_database_url() -> str:
    url = sa.engine.make_url(BASE_DATABASE_URL)
    return _render(url.set(database=f"{url.database}_nm_demo_test"))


DEMO_URL = _demo_database_url()


def _recreate_database(url: str) -> None:
    """A new, empty, unmarked database: what an operator creates for a demo."""
    parsed = sa.engine.make_url(url)
    admin = sa.create_engine(_render(parsed.set(database="postgres")), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
            connection.execute(sa.text(f'CREATE DATABASE "{parsed.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="module")
def demo_settings() -> Settings:
    _recreate_database(DEMO_URL)
    # No ADG_DEMO_DATASET: the default is the canonical dataset.
    return build_settings(
        environment="test",
        log_format="text",
        log_level="WARNING",
        runtime_mode="demo",
        database_url=DEMO_URL,
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
    def test_it_ran_on_the_canonical_dataset(self, first_reset: ResetReport) -> None:
        assert first_reset.dataset.key == CANONICAL_DATASET == "genesee-press-v1"
        assert first_reset.dataset.company_name == "Genesee Press"
        assert first_reset.initialized is True
        assert [item.name for item in first_reset.applied] == ["g1", "g2", "g3"]
        assert first_reset.canonical

    def test_every_run_was_accepted_with_the_status_it_claimed(
        self, first_reset: ResetReport
    ) -> None:
        runs = first_reset.as_dict()["runs"]
        assert runs["total"] == 300
        assert runs["recorded_statuses"] == {"failed": 2, "partial": 1, "succeeded": 297}
        assert [g["runs"] for g in runs["by_generation"]] == [100, 100, 100]
        assert {(r["name"], r["recorded_status"]) for r in runs["not_succeeded"]} == {
            ("g1-ntfs-gp-sec-fs01", "failed"),
            ("g3-ntfs-gp-fs-03", "failed"),
            ("g3-ntfs-gp-mig-fs01", "partial"),
        }

    def test_the_ingested_volume_is_the_manifests(self, first_reset: ResetReport) -> None:
        statistics = json.loads(MANIFEST.read_text(encoding="utf-8"))["statistics"]
        per_generation = [statistics["generations"][g]["observations"] for g in ("g1", "g2", "g3")]
        applied = [item.seed.observations_applied for item in first_reset.applied]
        assert applied == per_generation
        assert sum(applied) == 41_756

    def test_every_canonical_probe_is_present(self, first_reset: ResetReport) -> None:
        assert first_reset.probes_checked
        assert first_reset.ok
        missing = [r.probe.description for r in first_reset.probes if not r.present]
        assert missing == []
        assert len(first_reset.probes) == len(load_dataset(CANONICAL_DATASET).probes())

    def test_the_risk_pass_covered_the_whole_estate(self, first_reset: ResetReport) -> None:
        summary = first_reset.risk.summary
        assert summary.complete is True
        assert summary.truncated == ()
        assert summary.opened > 0
        assert first_reset.risk.evaluated_at == CANONICAL_RISK_PASS

    def test_the_risk_pass_used_the_datasets_configuration(self, engine: sa.Engine) -> None:
        """Sensitive folders exist only in Genesee Press's own declaration, so a finding on
        one proves the dataset's configuration -- not the shipped default -- was applied."""
        with engine.connect() as connection:
            sensitive = connection.execute(
                sa.text(
                    "SELECT count(*) FROM risk_findings WHERE status = 'open' "
                    "AND rule_id = 'broad_access_on_sensitive_resource'"
                )
            ).scalar_one()
            dated = connection.execute(
                sa.text(
                    "SELECT DISTINCT last_evaluated_at FROM risk_findings WHERE status = 'open'"
                )
            ).scalars()
            instants = set(dated)
        assert sensitive > 0
        assert instants == {CANONICAL_RISK_PASS}

    def test_the_deployment_marker_is_the_one_authority(self, engine: sa.Engine) -> None:
        """Demo Mode's ``deployment_marker`` row marks the database; the Genesee Press
        branch's ``COMMENT ON DATABASE`` marker is gone rather than kept as a second one."""
        with engine.connect() as connection:
            marker = connection.execute(
                sa.text(
                    "SELECT runtime_mode, dataset_key, dataset_seed, marked_by "
                    "FROM deployment_marker"
                )
            ).one()
            comment = connection.execute(
                sa.text(
                    "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                    "WHERE datname = current_database()"
                )
            ).scalar_one()
        assert tuple(marker) == (
            "demo",
            "genesee-press-v1",
            "genesee-press-v1",
            "python -m app.demo_mode reset",
        )
        assert comment is None

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
        assert all(upn.endswith("@geneseepress.example") for upn in upns)
        assert {person.email for person in canon.active} <= upns

    def test_current_state_matches_the_manifest(self, first_reset: ResetReport) -> None:
        """The canonical counts, read from the committed manifest rather than restated."""
        estate = json.loads(MANIFEST.read_text(encoding="utf-8"))["statistics"]["estate"]
        counts = first_reset.counts
        assert counts["current_server"] == estate["servers"]
        assert counts["current_smb_share"] == estate["shares"] + estate["administrative_shares"]
        assert counts["current_smb_ace"] == estate["share_aces"]
        # The two folders the partial g3 scan of GP-MIG-FS01 could not remove stay current.
        assert counts["current_ntfs_resource"] == estate["directories"] + 2
        assert counts["scan_runs"] == 300


class TestCurrentStateSemantics:
    def test_a_reconciled_scan_tombstones_what_it_no_longer_saw(
        self, engine: sa.Engine, canon: Canon
    ) -> None:
        scratch = r"\\GP-ENG-FS02\QA\Sprint-Scratch"
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
        """GP-FS-03's NTFS scan failed in g3. Everything g2 saw there is still current."""
        seen_in_g2 = {d.path for d in build_estate(G2).estate.directories_on("GP-FS-03")}
        assert seen_in_g2
        assert seen_in_g2 <= _current_paths(engine)

    def test_a_partial_scan_removes_nothing_even_what_is_really_gone(
        self, engine: sa.Engine
    ) -> None:
        """Wave-3-Staging was deleted on 2026-09-25. The g3 scan of GP-MIG-FS01 was partial,
        so it may not say so, and ADG keeps the folder until a complete scan does."""
        staging = r"\\GP-MIG-FS01\Migrations\Wave-3-Staging"
        restricted = r"\\GP-MIG-FS01\Migrations\FS-Consolidation-2020\Restricted"
        assert staging not in {d.path for d in build_estate(G3).estate.directories}
        current = _current_paths(engine)
        assert staging in current
        assert restricted in current

    def test_current_state_is_exactly_what_the_scans_justify(self, engine: sa.Engine) -> None:
        g2 = build_estate(G2).estate
        g3 = build_estate(G3).estate
        expected = {d.path for d in g3.directories if d.server_name != "GP-FS-03"}
        expected |= {d.path for d in g2.directories_on("GP-FS-03")}
        expected |= {d.path for d in g2.directories_on("GP-MIG-FS01")}
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
                        "WHERE server_key = 'gp-sec-fs01'"
                    )
                )
            }
        incident = r"\\GP-SEC-FS01\Incidents\IR-2026-014"
        assert first.pop(incident) == run("g3-ntfs-gp-sec-fs01"), "created after g2"
        assert set(first.values()) == {run("g2-ntfs-gp-sec-fs01")}, "g1 could not read it"


class TestTheGoldenScenariosThroughTheApi:
    async def test_f_both_routes_are_explained(self, api: AsyncClient, canon: Canon) -> None:
        mover = canon.one("golden:cross-department")
        path = r"\\GP-FIN-FS01\Finance\Reports\Revenue"
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
        path = r"\\GP-ENG-FS01\Engineering\Runbooks\Payments-Legacy"
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
        zephyr = r"\\GP-ENG-FS01\Engineering\Projects\Zephyr"
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
        design = r"\\GP-CORP-FS02\Design"
        response = await api.get(f"/api/v1/resources/{_q(design)}/acl")
        assert response.status_code == 200, response.text
        text = response.text
        assert "sec-share-design-write" in text
        people = {person.sid for person in canon.employees}
        assert not any(sid in text for sid in people)

    async def _acl(self, api: AsyncClient, path: str) -> dict[str, Any]:
        response = await api.get(f"/api/v1/resources/{_q(path)}/acl")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def test_b_four_generations_of_finance_groups_on_one_acl(self, api: AsyncClient) -> None:
        body = await self._acl(api, r"\\GP-FIN-FS01\Finance")
        names = {entry["trustee"]["display_name"] for entry in body["entries"]}
        assert {
            "Finance-RW",
            "GG_Finance_RW",
            "Finance_Read",
            "FS-Finance-R",
            "sec-share-finance-write",
            "sec-share-finance-read-only",
        } <= names

    async def test_c_engineering_mixes_legacy_and_standard_groups_at_the_root(
        self, api: AsyncClient
    ) -> None:
        body = await self._acl(api, r"\\GP-ENG-FS01\Engineering")
        names = {entry["trustee"]["display_name"] for entry in body["entries"]}
        assert {
            "Share_Engineering_Modify",
            "Engineering-FS",
            "sec-share-engineering-write",
        } <= names
        payments = r"\\GP-ENG-FS01\Engineering\Services\Payments"
        response = await api.get(f"/api/v1/access/resources/{_q(payments)}/principals")
        assert response.status_code == 200, response.text
        assert any(item["access"] for item in response.json()["items"])

    async def test_d_compensation_is_protected_and_limited_to_two_groups(
        self, api: AsyncClient
    ) -> None:
        body = await self._acl(api, r"\\GP-HR-FS01\People\Compensation")
        assert body["resource"]["dacl_protected"] is True
        trustees = {entry["trustee"]["display_name"] for entry in body["entries"]}
        assert trustees == {"HR-Confidential", "Payroll_Admins"}

    async def test_e_archive_acls_still_name_accounts_and_groups_that_are_gone(
        self, api: AsyncClient, canon: Canon
    ) -> None:
        homes = await self._acl(api, r"\\GP-ARCH-FS01\Archive\Legacy-Home-Drives")
        unresolved = [entry for entry in homes["entries"] if entry["trustee"]["kind"] != "user"]
        assert len(unresolved) == 2, homes["entries"]
        projects = await self._acl(api, r"\\GP-ARCH-FS01\Archive\Projects-2019")
        vega = canon.group("Project-Vega").sid
        named = [entry for entry in projects["entries"] if entry["trustee"]["sid"] == vega]
        assert len(named) == 1
        assert named[0]["trustee"]["resolved"] is False

    async def test_the_demo_overview_offers_every_scenario(
        self, demo_settings: Settings, first_reset: ResetReport
    ) -> None:
        app = create_app(demo_settings)
        database = Database(demo_settings)
        app.state.database = database
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {token_for_roles(demo_settings, Role.VIEWER)}"},
            ) as viewer:
                overview = await viewer.get("/api/v1/demo")
                runtime = await viewer.get("/runtime")
        finally:
            await database.dispose()
        assert overview.status_code == 200, overview.text
        body = overview.json()
        assert body["dataset"]["key"] == "genesee-press-v1"
        assert [(g["name"], g["applied"], g["in_baseline"]) for g in body["generations"]] == [
            ("g1", True, True),
            ("g2", True, True),
            ("g3", True, True),
        ]
        assert body["next_generation"] is None
        assert [s["key"] for s in body["scenarios"]] == list("ABCDEFGH")
        assert runtime.json()["dataset"]["company_name"] == "Genesee Press"
        assert runtime.json()["data_source"] == "synthetic"

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


class TestTheSystemExplanation:
    r"""ADG-DEMO-01: the request that once contradicted itself, on the demo estate itself.

    ``GET /explain?principal=S-1-5-18&resource=\\GP-FS-01\Shared`` answered ``no_grant``,
    *certain*, "no entry on either ACL names it" -- beside ``SYSTEM: Full Control`` in
    ``effective.ntfs.granted_by``. Over a share SYSTEM is a computer account the question does
    not name, so the honest answer is the ambiguous mapping; locally it is confirmed.
    """

    SHARED = r"\\GP-FS-01\Shared"

    async def explain(self, api: AsyncClient, **query: str) -> dict[str, Any]:
        response = await api.get(
            "/api/v1/access/explain",
            params={"principal": "S-1-5-18", "resource": self.SHARED, **query},
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    @staticmethod
    def mapping(body: dict[str, Any]) -> dict[str, Any]:
        found: list[dict[str, Any]] = [
            w for w in body["warnings"] if w["condition"].startswith("system_mapping_")
        ]
        assert len(found) == 1, found
        return found[0]

    async def test_over_the_share_it_is_ambiguous_and_contradicts_nothing(
        self, api: AsyncClient
    ) -> None:
        body = await self.explain(api)

        assert self.mapping(body)["detail"]["state"] == "ambiguous"
        assert body["verdict"]["outcome"] == "indeterminate"
        assert body["verdict"]["conclusive"] is False
        assert body["verdict"]["certainty"] == "uncertain"
        assert "No entry on either ACL names it" not in body["verdict"]["reason"]
        assert body["effective"]["ntfs"]["granted_by"] == []
        assert body["paths"] == []

    async def test_locally_it_is_the_servers_own_system_and_holds_full_control(
        self, api: AsyncClient
    ) -> None:
        body = await self.explain(api, access_path="local")
        detail = self.mapping(body)["detail"]

        assert detail["state"] == "confirmed"
        assert detail["described_as"] == "well_known"
        assert detail["last_observed_run_id"] is not None
        assert body["verdict"]["outcome"] == "granted"
        assert body["verdict"]["certainty"] == "at_least"
        assert body["effective"]["rights"]["label"] == "Full Control"
        assert [e["trustee"]["key"] for e in body["effective"]["ntfs"]["granted_by"]] == [
            "S-1-5-18"
        ]


class TestTheAccessInvestigation:
    r"""ADG-DEMO-02: the guided investigation the demo opens with, end to end.

    *Why does Fatima Gordon have access to* ``\\GP-ENG-FS01\Engineering\Projects\Apollo``*?*
    Asked of the reset estate through ``GET /api/v1/demo`` and ``GET /api/v1/access/explain``
    exactly as the investigation page asks it. Every claim the seed's summary makes, and
    every field the page renders, is pinned here against what the engine computes.
    """

    APOLLO = r"\\GP-ENG-FS01\Engineering\Projects\Apollo"
    FIVE_GROUPS = (
        "ENG-Mobile",
        "Engineering-Frontend",
        "Engineering",
        "Engineering-FS",
        "Share_Engineering_Modify",
    )

    @pytest.fixture
    def seed(self, canon: Canon) -> dict[str, Any]:
        [item] = investigations(canon)
        return item

    async def explain(self, api: AsyncClient, principal: str) -> dict[str, Any]:
        response = await api.get(
            "/api/v1/access/explain", params={"principal": principal, "resource": self.APOLLO}
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    @staticmethod
    def path(body: dict[str, Any], path_id: str) -> dict[str, Any]:
        found: list[dict[str, Any]] = [item for item in body["paths"] if item["id"] == path_id]
        assert len(found) == 1, path_id
        return found[0]

    @staticmethod
    def hops(body: dict[str, Any], path: dict[str, Any]) -> list[tuple[str, str | None]]:
        nodes = {node["id"]: node for node in body["graph"]["nodes"]}
        return [(nodes[n]["kind"], nodes[n]["display_name"]) for n in path["nodes"]]

    async def test_the_demo_offers_the_question_without_a_sid_to_look_up(
        self, api: AsyncClient, seed: dict[str, Any]
    ) -> None:
        overview = (await api.get("/api/v1/demo")).json()

        [offered] = overview["investigations"]
        assert offered["key"] == "apollo"
        assert offered["principal"] == {
            "kind": "person",
            "name": "Fatima Gordon",
            "sid": seed["person"]["sid"],
        }
        assert offered["resource"] == self.APOLLO
        assert offered["access_path"] == "remote_smb"
        assert offered["question"] == f"Why does Fatima Gordon have access to {self.APOLLO}?"
        assert offered["scenarios"] == ["C", "H"]

    async def test_access_is_observed_and_qualified_not_certain(
        self, api: AsyncClient, seed: dict[str, Any]
    ) -> None:
        body = await self.explain(api, seed["person"]["sid"])

        assert body["schema_version"] == "1.2"
        assert body["verdict"]["outcome"] == "granted"
        assert body["verdict"]["conclusive"] is True
        assert body["verdict"]["certainty"] == "at_least"
        assert body["effective"]["rights"]["label"] == "Modify"
        assert body["effective"]["acl_provenance"] == "observed"
        assert body["complete"] is True
        assert body["investigation"]["presence"] == "present"

    async def test_the_ntfs_route_is_the_direct_entry_and_the_share_route_a_group(
        self, api: AsyncClient, seed: dict[str, Any]
    ) -> None:
        body = await self.explain(api, seed["person"]["sid"])
        ntfs, share = body["investigation"]["primary_routes"]

        assert (ntfs["layer"], ntfs["route"], ntfs["placement"], ntfs["membership_hops"]) == (
            "ntfs",
            "direct",
            "explicit",
            0,
        )
        direct = self.path(body, ntfs["path_id"])
        assert direct["effect"] == "contributes"
        assert (direct["via_group"], direct["inherited"]) == (False, False)
        assert self.hops(body, direct) == [
            ("principal", "Fatima Gordon"),
            ("ntfs_ace", None),
            ("resource", None),
        ]
        assert ntfs["coverage"]["label"] == "Modify"

        assert (share["layer"], share["route"], share["membership_hops"]) == (
            "smb_share",
            "group",
            1,
        )
        assert self.hops(body, self.path(body, share["path_id"])) == [
            ("principal", "Fatima Gordon"),
            ("group", "ENG-Mobile"),
            ("smb_ace", None),
            ("resource", None),
        ]

    async def test_the_inherited_route_through_five_groups_is_shown_in_order_and_redundant(
        self, api: AsyncClient, seed: dict[str, Any]
    ) -> None:
        body = await self.explain(api, seed["person"]["sid"])
        chains = {
            tuple(member["display_name"] for member in item["chain"]): item
            for item in body["paths"]
            if item["layer"] == "ntfs"
        }
        inherited = chains[("Fatima Gordon", *self.FIVE_GROUPS)]

        assert (inherited["via_group"], inherited["inherited"]) == (True, True)
        assert inherited["effect"] == "redundant"
        assert self.hops(body, inherited) == [
            ("principal", "Fatima Gordon"),
            *[("group", name) for name in self.FIVE_GROUPS],
            ("ntfs_ace", None),
            ("resource", None),
        ]
        # And the seven-group detour through the application group, also redundant.
        detour = chains[
            (
                "Fatima Gordon",
                "ENG-Mobile",
                "Engineering-Frontend",
                "Engineering",
                "APP-GitHub-Users",
                "Engineering-FS",
                "Share_Engineering_Modify",
            )
        ]
        assert detour["effect"] == "redundant"

    async def test_removing_either_route_is_measured_to_change_nothing(
        self, api: AsyncClient, seed: dict[str, Any]
    ) -> None:
        body = await self.explain(api, seed["person"]["sid"])
        routes = body["investigation"]["primary_routes"]

        assert [route["removal_effect"] for route in routes] == [
            "changes_nothing",
            "changes_nothing",
        ]
        assert all(route["rights_after_removal"]["label"] == "Modify" for route in routes)
        measured = {target["edge_id"]: target for target in body["removal_targets"]}
        for route in routes:
            assert measured[route["removal_edge_id"]]["changes_nothing"] is True

    async def test_every_hop_carries_its_collector_and_scan(
        self, api: AsyncClient, seed: dict[str, Any]
    ) -> None:
        body = await self.explain(api, seed["person"]["sid"])
        evidence = {item["edge_id"]: item for item in body["evidence"]}
        edges = body["graph"]["edges"]

        assert [item["edge_id"] for item in body["evidence"]] == [e["id"] for e in edges]
        expected = {"membership": "active_directory", "trustee": None, "grant": None}
        for edge in edges:
            hop = evidence[edge["id"]]
            assert hop["basis"] == "observed", hop
            assert hop["last_run_status"] == "succeeded"
            if edge["kind"] == "membership":
                assert hop["record"] == "membership_edge"
                assert hop["record_key"] == edge["membership_edge_key"]
                assert hop["collector"] == expected["membership"]
            else:
                layer = "ntfs" if "ntfs-ace" in edge["id"] else "smb"
                assert hop["record"] == ("ntfs_ace" if layer == "ntfs" else "smb_share_ace")
                assert hop["record_key"] == edge["ace_key"]
                assert hop["collector"] == layer

        direct = self.path(body, body["investigation"]["primary_routes"][0]["path_id"])
        entry = evidence[direct["edges"][0]]
        # Authored on the folder before ADG went live; seen by every NTFS scan since.
        assert entry["first_observed_at"].startswith(G1.isoformat())
        assert entry["last_observed_at"].startswith(G3.isoformat())

    async def test_what_cannot_be_confirmed_is_named_and_does_not_reach_the_conclusion(
        self, api: AsyncClient, seed: dict[str, Any], canon: Canon
    ) -> None:
        body = await self.explain(api, seed["person"]["sid"])
        investigation = body["investigation"]
        open_ = {
            item["about"]["sid"]: item
            for item in investigation["open_questions"]
            if item["about"] is not None
        }

        orphan = open_[seed["former"]["sid"]]
        assert orphan["condition"] == "trustee_membership_unobserved"
        assert orphan["state"] == "missing"
        assert orphan["impact"] == "extent"
        assert orphan["about"]["kind"] == "unresolved"
        assert orphan["about"]["display_name"] is None
        assert orphan["about"]["last_known_name"] == r"GPCORP\lhansen"

        zephyr = open_[canon.group("Proj-Zephyr-RW").sid]
        assert zephyr["condition"] == "trustee_membership_unobserved"
        assert zephyr["state"] == "missing"
        assert zephyr["impact"] == "extent"

        assert investigation["impact"] == "extent"
        assert investigation["affects_conclusion"] is False
        assert all(item["impact"] != "presence" for item in investigation["open_questions"])

    async def test_the_next_steps_follow_from_the_state(
        self, api: AsyncClient, seed: dict[str, Any], canon: Canon
    ) -> None:
        body = await self.explain(api, seed["person"]["sid"])
        steps = [
            (step["code"], None if step["about"] is None else step["about"]["sid"], step["path_id"])
            for step in body["investigation"]["next_steps"]
        ]

        assert steps == [
            ("resolve_trustee_identity", seed["former"]["sid"], None),
            ("collect_group_membership", canon.group("Proj-Zephyr-RW").sid, None),
            (
                "review_redundant_entry",
                None,
                body["investigation"]["primary_routes"][0]["path_id"],
            ),
        ]

    async def test_it_is_deterministic(self, api: AsyncClient, seed: dict[str, Any]) -> None:
        first = await self.explain(api, seed["person"]["sid"])
        second = await self.explain(api, seed["person"]["sid"])

        for field in ("investigation", "evidence", "paths", "graph", "removal_targets"):
            assert first[field] == second[field], field

    async def test_system_reaches_the_summary_with_its_mapping_intact(
        self, api: AsyncClient
    ) -> None:
        """ADG-DEMO-01's request, read through the investigation summary."""
        shared = r"\\GP-FS-01\Shared"
        over_smb = (
            await api.get(
                "/api/v1/access/explain", params={"principal": "S-1-5-18", "resource": shared}
            )
        ).json()["investigation"]
        local = (
            await api.get(
                "/api/v1/access/explain",
                params={"principal": "S-1-5-18", "resource": shared, "access_path": "local"},
            )
        ).json()["investigation"]

        def mapping(summary: dict[str, Any]) -> dict[str, Any]:
            found: list[dict[str, Any]] = [
                item
                for item in summary["open_questions"]
                if item["condition"].startswith("system_mapping_")
            ]
            assert len(found) == 1, found
            return found[0]

        assert over_smb["presence"] == "undetermined"
        assert over_smb["primary_routes"] == []
        assert mapping(over_smb)["state"] == "ambiguous"
        assert over_smb["affects_conclusion"] is True
        assert "settle_system_mapping" in [step["code"] for step in over_smb["next_steps"]]

        assert local["presence"] == "present"
        assert mapping(local)["state"] == "confirmed"
        assert mapping(local)["impact"] == "extent"
        assert "settle_system_mapping" not in [step["code"] for step in local["next_steps"]]


class TestTheCoverageAndRecoveryDemo:
    r"""ADG-DEMO-03: collection coverage beside the numbers, and a comparison that recovers.

    Asked of the reset estate through the same routes the pages call. The history leaves
    exactly two scopes incomplete -- GP-FS-03's g3 NTFS scan could not reach the server, and
    GP-MIG-FS01's was partial -- and everything here is read off those two facts.
    """

    APOLLO = r"\\GP-ENG-FS01\Engineering\Projects\Apollo"

    async def coverage(self, api: AsyncClient, **params: str) -> dict[str, Any]:
        response = await api.get("/api/v1/collection/coverage", params=params)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def compare(self, api: AsyncClient, **params: str) -> dict[str, Any]:
        response = await api.get("/api/v1/changes/compare", params=params)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def test_coverage_names_the_two_scopes_the_history_left_incomplete(
        self, api: AsyncClient
    ) -> None:
        body = await self.coverage(api)

        assert body["expectation"] == "dataset"
        assert {item["collector"]: item["state"] for item in body["collectors"]} == {
            "active_directory": "reported",
            "local_groups": "reported",
            "ntfs": "reported",
            "smb": "reported",
        }
        assert body["verdict"] == "incomplete"
        assert body["counts"] == {
            "scopes": 100,
            "collected": 98,
            "partial": 1,
            "unavailable": 1,
            "failed": 0,
            "running": 0,
            "stale": 1,
            "never_collected": 0,
            "returned_nothing": 0,
        }
        incomplete = {
            item["label"]: item for item in body["scopes"] if item["state"] != "collected"
        }
        assert set(incomplete) == {"ntfs (GP-FS-03)", "ntfs (GP-MIG-FS01)"}
        fs03 = incomplete["ntfs (GP-FS-03)"]
        assert fs03["state"] == "unavailable"
        assert fs03["evidence"] == "stale"
        assert fs03["failure_codes"] == ["host_unreachable"]
        assert fs03["evidence_collected_at"].startswith(GENERATIONS[1].day.isoformat())
        assert incomplete["ntfs (GP-MIG-FS01)"]["state"] == "partial"

    async def test_the_apollo_answer_reads_only_complete_scopes(self, api: AsyncClient) -> None:
        relevant = (await self.coverage(api, resource=self.APOLLO))["relevant"]

        assert sorted(item["label"] for item in relevant["scopes"]) == [
            "active_directory (corp.geneseepress.example)",
            "local_groups (GP-ENG-FS01)",
            "ntfs (GP-ENG-FS01)",
            "smb (GP-ENG-FS01)",
        ]
        assert relevant["complete"] is True
        assert relevant["could_change_answer"] is False
        assert relevant["incomplete_elsewhere"] == 2

    async def test_the_investigation_keeps_its_own_uncertainty(self, api: AsyncClient) -> None:
        """Coverage is context beside the answer; it changes nothing in it."""
        [seed] = investigations()
        response = await api.get(
            "/api/v1/access/explain",
            params={"principal": seed["person"]["sid"], "resource": self.APOLLO},
        )
        body = response.json()

        assert response.status_code == 200, response.text
        assert body["verdict"]["outcome"] == "granted"
        assert body["verdict"]["certainty"] == "at_least"

    async def test_the_risk_totals_carry_their_coverage_and_snapshot(
        self, api: AsyncClient
    ) -> None:
        body = (await api.get("/api/v1/risks/summary")).json()
        interpretation = body["interpretation"]

        assert body["total"] == 9386
        assert body["severity_counts"]["critical"] == 3
        assert interpretation["reading"] == "incomplete"
        assert interpretation["exhaustive"] is False
        assert interpretation["basis_current"] is True
        assert interpretation["evaluated_basis"] == body["collection"]["snapshot"]["token"]
        assert len(interpretation["reasons"]) == 1
        held = {item["scope"]: item for item in interpretation["held"]}
        assert held["ntfs (GP-FS-03)"]["evidence"] == "stale"
        assert held["ntfs (GP-FS-03)"]["open_findings"] == 189
        assert held["ntfs (GP-FS-03)"]["evidence_collected_at"].startswith(
            GENERATIONS[1].day.isoformat()
        )

    async def test_the_comparison_drill_times_out_then_recovers_to_complete(
        self, api: AsyncClient
    ) -> None:
        demo = (await api.get("/api/v1/demo")).json()
        [drill] = demo["comparisons"]
        window = {"from": drill["at_from"], "to": drill["at_to"]}

        started = await self.compare(api, **window, time_budget_ms=str(drill["drill_window_ms"]))
        assert started["outcome"] == "timed_out"
        assert started["complete"] is False
        assert started["changes"] == []
        actions = {item["action"]: item for item in started["recovery"]}
        assert set(actions) == {"retry_standard_window", "narrow_kind"}

        retried = await self.compare(
            api, **window, **actions["retry_standard_window"]["parameters"]
        )
        assert retried["outcome"] == "incomplete"
        assert retried["complete"] is False
        assert retried["retry_of"] == started["comparison_id"]
        assert retried["recovered"] is False
        assert retried["compared_through"]["kind"] == "membership_edge"
        assert retried["objects_read_to"] == 5000
        # Cut at one key on both sides: nothing observed at the later instant reads as unseen.
        assert retried["unobserved_at_to"] == 0
        narrower = {
            item["parameters"]["kind"]: item
            for item in retried["recovery"]
            if item["action"] == "narrow_kind"
        }
        assert "membership_edge" not in narrower
        assert (narrower["ntfs_ace"]["objects_at_from"], narrower["ntfs_ace"]["objects_at_to"]) == (
            3947,
            4124,
        )

        finished = await self.compare(api, **window, **narrower["ntfs_ace"]["parameters"])
        assert finished["outcome"] == "complete"
        assert finished["complete"] is True
        assert finished["recovered"] is True
        assert finished["retry_of"] == retried["comparison_id"]
        assert finished["requested_scope"]["kinds"] == ["ntfs_ace"]
        assert finished["differences_found"] == 221
        assert finished["recovery"] == []


class TestTheBoundary:
    def test_a_production_deployment_is_refused(self) -> None:
        production = build_settings(environment="test", database_url=DEMO_URL)
        with pytest.raises(DemoLifecycleError, match="ADG_RUNTIME_MODE=demo"):
            asyncio.run(reset_demo(production, migrate=False))

    @pytest.fixture
    def saved_marker(self, engine: sa.Engine) -> Iterator[None]:
        with engine.connect() as connection:
            row = connection.execute(sa.text("SELECT * FROM deployment_marker")).mappings().one()
        try:
            yield
        finally:
            with engine.begin() as connection:
                connection.execute(sa.text("DELETE FROM deployment_marker"))
                connection.execute(
                    sa.text(
                        "INSERT INTO deployment_marker (marker_id, runtime_mode, dataset_key, "
                        "dataset_seed, marked_at, marked_by) VALUES (:marker_id, "
                        ":runtime_mode, :dataset_key, :dataset_seed, :marked_at, :marked_by)"
                    ),
                    dict(row),
                )

    def test_an_unmarked_database_with_data_is_refused_whatever_its_comment_says(
        self, demo_settings: Settings, engine: sa.Engine, saved_marker: None
    ) -> None:
        """The Genesee Press branch's database comment is not an authority: a populated
        database without the marker row is refused even when it carries that comment."""
        with engine.connect() as connection:
            name = connection.execute(sa.text("SELECT current_database()")).scalar_one()
        quoted = '"' + name + '"'
        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM deployment_marker"))
            connection.execute(
                sa.text(f"COMMENT ON DATABASE {quoted} IS 'adg-demo:genesee-press-v1'")
            )
        try:
            with pytest.raises(DemoTargetRefused, match="carries no deployment marker"):
                asyncio.run(reset_demo(demo_settings, migrate=False))
            with engine.connect() as connection:
                runs = connection.execute(sa.text("SELECT count(*) FROM scan_runs")).scalar_one()
            assert runs == 300, "a refused reset changes nothing"
        finally:
            with engine.begin() as connection:
                connection.execute(sa.text(f"COMMENT ON DATABASE {quoted} IS NULL"))

    def test_a_database_marked_for_production_is_refused(
        self, demo_settings: Settings, engine: sa.Engine, saved_marker: None
    ) -> None:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE deployment_marker SET runtime_mode = 'production', "
                    "dataset_key = NULL, dataset_seed = NULL"
                )
            )
        with pytest.raises(DemoTargetRefused, match="marked runtime_mode='production'"):
            asyncio.run(reset_demo(demo_settings, migrate=False))
        with engine.connect() as connection:
            runs = connection.execute(sa.text("SELECT count(*) FROM scan_runs")).scalar_one()
        assert runs == 300

    async def test_external_ingestion_is_refused_and_writes_nothing(
        self, api: AsyncClient, engine: sa.Engine
    ) -> None:
        transcript = load_dataset(CANONICAL_DATASET).generations()[0].transcripts[0]
        response = await api.post("/api/v1/scan-runs", json=transcript.start)
        assert response.status_code == 403
        assert response.json()["status"] == "demo_only"
        with engine.connect() as connection:
            runs = connection.execute(sa.text("SELECT count(*) FROM scan_runs")).scalar_one()
        assert runs == 300

    async def test_a_demo_api_starts_on_the_reset_database(self, demo_settings: Settings) -> None:
        app = create_app(demo_settings)
        async with app.router.lifespan_context(app):
            assert app.state.database is not None


class TestTheHistoryReplaysThroughTheSyntheticScan:
    """The three generations through Demo Mode's scan, one at a time.

    ``reset --generations 1`` rewinds the demo to 2026-09-03; two synthetic scans -- one
    through the API, one through the lifecycle the CLI calls -- bring it to the snapshot.
    The result must be the canonical state: every current-state, history and run digest
    equal to the full reset's, and the same open findings. (Resolved findings may differ:
    the replay ran a risk pass after every generation, as an operator's schedule would,
    where the full reset runs one.)
    """

    def test_reset_then_scan_forward_reaches_the_canonical_state(
        self, demo_settings: Settings, first_reset: ResetReport, engine: sa.Engine
    ) -> None:
        rewound = asyncio.run(reset_demo(demo_settings, migrate=False, generations=1))

        assert [item.name for item in rewound.applied] == ["g1"]
        assert not rewound.canonical
        assert rewound.probes_checked is False
        assert rewound.risk.evaluated_at == dt.datetime(2026, 9, 3, 6, 0, tzinfo=dt.UTC)
        status = asyncio.run(demo_status(demo_settings))
        assert [g.applied for g in status.generations] == [True, False, False]
        assert status.baseline_applied is False
        with pytest.raises(DemoLifecycleError, match="not yet applied: g2, g3"):
            asyncio.run(verify_demo(demo_settings))
        # g1 could not read GP-SEC-FS01's file system: its shares are known, its folders not.
        with engine.connect() as connection:
            sec_folders = connection.execute(
                sa.select(sa.func.count())
                .select_from(current_ntfs_resources)
                .where(current_ntfs_resources.c.path.ilike(r"\\\\GP-SEC-FS01\\%"))
            ).scalar_one()
        assert sec_folders == 0

        async def scan_through_the_api() -> Any:
            app = create_app(demo_settings)
            database = Database(demo_settings)
            app.state.database = database
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://testserver",
                    headers=auth_headers(demo_settings),
                ) as client:
                    return await client.post("/api/v1/demo/scans")
            finally:
                await database.dispose()

        async def scan_through_the_lifecycle() -> Any:
            database = Database(demo_settings)
            try:
                return await run_synthetic_scan(
                    demo_settings, database, load_dataset(CANONICAL_DATASET)
                )
            finally:
                await database.dispose()

        second = asyncio.run(scan_through_the_api())
        assert second.status_code == 201, second.text
        assert second.json()["generation"] == "g2"
        assert len(second.json()["runs"]) == 100
        assert second.json()["risk_evaluated_at"] == "2026-09-17T06:00:00Z"

        third = asyncio.run(scan_through_the_lifecycle())
        assert third.generation.name == "g3"
        assert third.risk.evaluated_at == CANONICAL_RISK_PASS

        with pytest.raises(DemoLifecycleError, match="has been applied"):
            asyncio.run(scan_through_the_lifecycle())

        verified = asyncio.run(verify_demo(demo_settings))
        assert verified["ok"] is True
        assert verified["counts"] == first_reset.counts

        async def digests() -> dict[str, str]:
            database = Database(demo_settings)
            try:
                async with database.engine.connect() as connection:
                    return await canonical_digests(connection)
            finally:
                await database.dispose()

        replayed = asyncio.run(digests())
        canonical = dict(first_reset.digests)
        assert set(replayed) == set(canonical)
        for source in canonical:
            if source != STATE_SOURCES_EXCLUDING_RISK:
                assert replayed[source] == canonical[source], source

        with engine.connect() as connection:
            open_keys = set(
                connection.execute(
                    sa.text("SELECT finding_key FROM risk_findings WHERE status = 'open'")
                ).scalars()
            )
        assert len(open_keys) == first_reset.risk_findings_open


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

        assert second.initialized is False
        assert second.fingerprint == first_reset.fingerprint
        assert second.digests == first_reset.digests
        assert second.counts == first_reset.counts
        assert second.seed.observations_applied == first_reset.seed.observations_applied
        assert all(argv[1:4] == ["-m", "alembic", "upgrade"] for argv in commands), commands
        assert not any(
            "pwsh" in " ".join(argv) or "powershell" in " ".join(argv).casefold()
            for argv in commands
        )


def test_the_snapshot_constant_is_the_last_generation() -> None:
    assert GENERATIONS[-1].day == SNAPSHOT
