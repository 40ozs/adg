r"""Null Meridian through Demo Mode against a real PostgreSQL: reset, history, scenarios.

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
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Role
from app.config import Settings, build_settings
from app.db import Database
from app.demo.null_meridian.canon import SNAPSHOT, Canon, build_canon
from app.demo.null_meridian.estate import build_estate
from app.demo.null_meridian.generations import GENERATIONS, TRANSCRIPT_NAMESPACE
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
from app.domain.relationships import NodeKind, NodeRef, RelationshipType
from app.main import create_app
from app.models.current import current_ntfs_resources, current_principals
from app.relationships import UNDERIVED, RelationshipProjector
from app.traversal.semantics import TraversalPolicy
from app.traversal.service import RelationshipGraphService
from tests.db.conftest import BASE_DATABASE_URL, _render
from tests.support.auth import auth_headers, token_for_roles
from tests.support.relationships import graph_snapshot

G1, G2, G3 = (generation.day for generation in GENERATIONS)
MANIFEST = Path(__file__).resolve().parents[3] / "docs" / "demo" / "null-meridian.manifest.json"
#: The Null Meridian branch's pre-integration reset evaluated risk at this instant; the
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
        assert first_reset.dataset.key == CANONICAL_DATASET == "null-meridian-v1"
        assert first_reset.dataset.company_name == "Null Meridian"
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
            ("g1-ntfs-nm-sec-fs01", "failed"),
            ("g3-ntfs-nm-fs-03", "failed"),
            ("g3-ntfs-nm-mig-fs01", "partial"),
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
        """Sensitive folders exist only in Null Meridian's own declaration, so a finding on
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
        """Demo Mode's ``deployment_marker`` row marks the database; the Null Meridian
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
            "null-meridian-v1",
            "null-meridian-v1",
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
        assert all(upn.endswith("@nullmeridian.info") for upn in upns)
        assert {person.email for person in canon.active} <= upns

    def test_current_state_matches_the_manifest(self, first_reset: ResetReport) -> None:
        """The canonical counts, read from the committed manifest rather than restated."""
        estate = json.loads(MANIFEST.read_text(encoding="utf-8"))["statistics"]["estate"]
        counts = first_reset.counts
        assert counts["current_server"] == estate["servers"]
        assert counts["current_smb_share"] == estate["shares"] + estate["administrative_shares"]
        assert counts["current_smb_ace"] == estate["share_aces"]
        # The two folders the partial g3 scan of NM-MIG-FS01 could not remove stay current.
        assert counts["current_ntfs_resource"] == estate["directories"] + 2
        assert counts["scan_runs"] == 300


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


class TestThePrivilegeGraph:
    """The relationship projector ran at every completion of the reset (ADG-GRAPH-01B).

    Demo Mode ingests through the same endpoints a collector uses, so the graph here was derived
    by exactly the code that derives a production estate's, over three generations of scans
    with removals, revivals and reconciliations in between.
    """

    def test_every_completed_run_was_derived_and_no_pass_failed(self, engine: sa.Engine) -> None:
        with engine.connect() as connection:
            failed = connection.execute(
                sa.text("SELECT count(*) FROM graph_derivation_passes WHERE status = 'failed'")
            ).scalar_one()
            underived = connection.execute(
                sa.text(
                    "SELECT count(*) FROM scan_runs r WHERE r.completed_at IS NOT NULL AND NOT "
                    "EXISTS (SELECT 1 FROM graph_derivation_passes p WHERE p.source_run_id = "
                    "r.run_id AND p.status = 'succeeded')"
                )
            ).scalar_one()
            runs = connection.execute(sa.text("SELECT count(*) FROM scan_runs")).scalar_one()
        assert runs > 0
        assert failed == 0
        assert underived == 0

    def test_the_estate_holds_only_relationships_its_facts_support(self, engine: sa.Engine) -> None:
        with engine.connect() as connection:
            types = {
                row[0]
                for row in connection.execute(
                    sa.text("SELECT DISTINCT relationship_type FROM graph_relationships")
                )
            }
            share_targets = connection.execute(
                sa.text("SELECT count(*) FROM graph_relationships WHERE target_kind = 'smb_share'")
            ).scalar_one()
        assert {"member_of", "local_group_member_of", "local_admin_of"} <= types
        assert types & {"generic_all", "generic_write", "write_dacl", "write_owner"}
        assert not types & {kind.value for kind in UNDERIVED}
        assert share_targets == 0

    async def test_a_clean_recomputation_reproduces_the_incremental_graph(
        self, demo_settings: Settings, first_reset: ResetReport
    ) -> None:
        database = Database(demo_settings)
        try:
            async with database.session() as session:
                incremental = await graph_snapshot(session)
                outcome = await RelationshipProjector(session).rebuild(
                    now=dt.datetime.now(tz=dt.UTC), discard_existing=True
                )
                rebuilt = await graph_snapshot(session)
                # Leave the demo database exactly as the reset left it for the tests below.
                await session.rollback()
        finally:
            await database.dispose()
        assert outcome.succeeded and not outcome.skipped
        assert incremental and rebuilt == incremental


class TestThePathEngine:
    """The path engine over the demo's derived graph (ADG-GRAPH-01C).

    Null Meridian's graph was derived incrementally across 300 completions with removals and
    revivals in between. A clean recomputation must not change a single route the engine
    returns: path identity depends on the facts, never on the order they were derived in.
    """

    @staticmethod
    async def _sample(
        session: AsyncSession, service: RelationshipGraphService
    ) -> list[tuple[NodeRef, NodeRef]]:
        rows = await session.execute(
            sa.text(
                "SELECT target_key FROM graph_relationships WHERE target_kind = 'ntfs_resource' "
                "GROUP BY target_key ORDER BY count(*) DESC, target_key LIMIT 3"
            )
        )
        pairs: list[tuple[NodeRef, NodeRef]] = []
        for key in rows.scalars():
            target = NodeRef(NodeKind.NTFS_RESOURCE, key)
            reached = await service.reverse_reachability(target, policy=TraversalPolicy.ALL_CURRENT)
            pairs.extend((item.node, target) for item in reached.sources[::25])
        return pairs

    @staticmethod
    async def _routes(
        service: RelationshipGraphService, pairs: list[tuple[NodeRef, NodeRef]]
    ) -> dict[tuple[NodeRef, NodeRef], Any]:
        found: dict[tuple[NodeRef, NodeRef], Any] = {}
        for source, target in pairs:
            search = await service.shortest_path(source, target, policy=TraversalPolicy.ALL_CURRENT)
            found[(source, target)] = [
                (
                    [str(node) for node in path.nodes],
                    [[edge.relationship_id for edge in step.relationships] for step in path.steps],
                    path.assessment.confidence.value,
                )
                for path in search.paths
            ]
        return found

    async def test_every_route_survives_a_clean_recomputation(
        self, demo_settings: Settings, first_reset: ResetReport
    ) -> None:
        database = Database(demo_settings)
        try:
            async with database.session() as session:
                service = RelationshipGraphService(session)
                snapshot = await service.open()
                pairs = await self._sample(session, service)
                incremental = await self._routes(service, pairs)
            async with database.session() as session:
                await RelationshipProjector(session).rebuild(
                    now=dt.datetime.now(tz=dt.UTC), discard_existing=True
                )
                service = RelationshipGraphService(session)
                await service.open()
                rebuilt = await self._routes(service, pairs)
                # Leave the demo database exactly as the reset left it.
                await session.rollback()
        finally:
            await database.dispose()
        assert snapshot.readiness.state == "current"
        assert len(pairs) >= 20
        assert all(incremental.values()), "every sampled source reaches its target"
        assert rebuilt == incremental

    async def test_the_demo_answers_are_current_and_conclusive(self, api: AsyncClient) -> None:
        readiness = (await api.get("/api/v1/graph/readiness")).json()
        semantics = (await api.get("/api/v1/graph/semantics")).json()

        assert readiness["basis"]["projection"]["state"] == "current"
        assert readiness["reasons"] == []
        assert {item["relationship_type"] for item in semantics["relationship_types"]} == {
            item.value for item in RelationshipType
        }


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

    async def _acl(self, api: AsyncClient, path: str) -> dict[str, Any]:
        response = await api.get(f"/api/v1/resources/{_q(path)}/acl")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def test_b_four_generations_of_finance_groups_on_one_acl(self, api: AsyncClient) -> None:
        body = await self._acl(api, r"\\NM-FIN-FS01\Finance")
        names = {entry["trustee"]["display_name"] for entry in body["entries"]}
        assert {
            "Finance-RW",
            "GG_Finance_RW",
            "Finance_Read",
            "FS-Finance-R",
            "sg-share-finance-write",
            "sg-share-finance-read-only",
        } <= names

    async def test_c_engineering_mixes_legacy_and_standard_groups_at_the_root(
        self, api: AsyncClient
    ) -> None:
        body = await self._acl(api, r"\\NM-ENG-FS01\Engineering")
        names = {entry["trustee"]["display_name"] for entry in body["entries"]}
        assert {"Share_Engineering_Modify", "Engineering-FS", "sg-share-engineering-write"} <= names
        payments = r"\\NM-ENG-FS01\Engineering\Services\Payments"
        response = await api.get(f"/api/v1/access/resources/{_q(payments)}/principals")
        assert response.status_code == 200, response.text
        assert any(item["access"] for item in response.json()["items"])

    async def test_d_compensation_is_protected_and_limited_to_two_groups(
        self, api: AsyncClient
    ) -> None:
        body = await self._acl(api, r"\\NM-HR-FS01\People\Compensation")
        assert body["resource"]["dacl_protected"] is True
        trustees = {entry["trustee"]["display_name"] for entry in body["entries"]}
        assert trustees == {"HR-Confidential", "Payroll_Admins"}

    async def test_e_archive_acls_still_name_accounts_and_groups_that_are_gone(
        self, api: AsyncClient, canon: Canon
    ) -> None:
        homes = await self._acl(api, r"\\NM-ARCH-FS01\Archive\Legacy-Home-Drives")
        unresolved = [entry for entry in homes["entries"] if entry["trustee"]["kind"] != "user"]
        assert len(unresolved) == 2, homes["entries"]
        projects = await self._acl(api, r"\\NM-ARCH-FS01\Archive\Projects-2019")
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
        assert body["dataset"]["key"] == "null-meridian-v1"
        assert [(g["name"], g["applied"], g["in_baseline"]) for g in body["generations"]] == [
            ("g1", True, True),
            ("g2", True, True),
            ("g3", True, True),
        ]
        assert body["next_generation"] is None
        assert [s["key"] for s in body["scenarios"]] == list("ABCDEFGH")
        assert runtime.json()["dataset"]["company_name"] == "Null Meridian"
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
        """The Null Meridian branch's database comment is not an authority: a populated
        database without the marker row is refused even when it carries that comment."""
        with engine.connect() as connection:
            name = connection.execute(sa.text("SELECT current_database()")).scalar_one()
        quoted = '"' + name + '"'
        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM deployment_marker"))
            connection.execute(
                sa.text(f"COMMENT ON DATABASE {quoted} IS 'adg-demo:null-meridian-v1'")
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
        # g1 could not read NM-SEC-FS01's file system: its shares are known, its folders not.
        with engine.connect() as connection:
            sec_folders = connection.execute(
                sa.select(sa.func.count())
                .select_from(current_ntfs_resources)
                .where(current_ntfs_resources.c.path.ilike(r"\\\\NM-SEC-FS01\\%"))
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
