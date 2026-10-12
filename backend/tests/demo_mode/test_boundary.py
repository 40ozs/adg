"""The demo boundary, without a database: provider selection, the mutation policy, the public
runtime surface, and what a demo process does *not* load or start.

The isolation claims are checked the only way a negative can be: by looking at what the
process actually imported and executed while a demo application was built and served.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.remediation import ExportDetailView, _demo_safe
from app.auth.roles import Role
from app.config import Settings, build_settings
from app.demo_mode import providers
from app.demo_mode.policy import (
    MUTATION_POLICY,
    SIMULATION_NOTICE,
    Treatment,
    defang_runbook,
)
from app.demo_mode.providers import (
    CollectorObservationSource,
    SyntheticObservationSource,
    observation_source_for,
)
from app.main import DEMO_NOTICE_HEADER, RUNTIME_MODE_HEADER, create_app
from tests.conftest import StubDatabase
from tests.support.auth import token_for_roles

DEMO_DB = "postgresql+psycopg://adg:pw@localhost:5432/adg_demo"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

#: Modules that would mean a directory, SMB, WinRM or PowerShell client had been loaded.
INFRASTRUCTURE_MODULES = (
    "ldap",
    "ldap3",
    "smbprotocol",
    "smbclient",
    "impacket",
    "winrm",
    "pypsrp",
    "pywinrm",
    "win32security",
    "win32net",
    "pythoncom",
)


@pytest.fixture
def demo_settings() -> Settings:
    return build_settings(
        environment="test",
        log_format="text",
        runtime_mode="demo",
        database_url=DEMO_DB,
        demo_dataset="adg-reference-estate",
    )


@pytest.fixture
def production_settings() -> Settings:
    return build_settings(environment="test", log_format="text")


async def _client(settings: Settings, **headers: str) -> AsyncClient:
    app = create_app(settings)
    app.state.database = StubDatabase(_ok())
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", headers=headers
    )


def _ok() -> Any:
    from app.db import ConnectivityResult

    return ConnectivityResult(ok=True, latency_ms=1.0)


@pytest.fixture
async def demo_client(demo_settings: Settings) -> AsyncIterator[AsyncClient]:
    async with await _client(demo_settings) as client:
        yield client


@pytest.fixture
async def production_client(production_settings: Settings) -> AsyncIterator[AsyncClient]:
    async with await _client(production_settings) as client:
        yield client


def _mutating_routes(settings: Settings) -> set[tuple[str, str]]:
    document = create_app(settings).openapi()
    return {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method in operations
        if method.upper() in MUTATING
    }


class TestProviderSelection:
    def test_production_selects_the_collectors(self, production_settings: Settings) -> None:
        source = observation_source_for(production_settings)

        assert isinstance(source, CollectorObservationSource)
        assert source.accepts_external_ingestion is True

    def test_demo_selects_the_synthetic_dataset(self, demo_settings: Settings) -> None:
        source = observation_source_for(demo_settings)

        assert isinstance(source, SyntheticObservationSource)
        assert source.accepts_external_ingestion is False
        assert source.dataset().metadata.key == "adg-reference-estate"

    def test_demo_never_constructs_the_collector_source(
        self, demo_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_: object, **__: object) -> None:
            raise AssertionError("the production observation source was constructed in demo")

        monkeypatch.setattr(providers, "CollectorObservationSource", explode)
        app = create_app(demo_settings)

        assert isinstance(app.state.observation_source, SyntheticObservationSource)

    def test_the_application_records_its_source(self, production_settings: Settings) -> None:
        app = create_app(production_settings)
        assert isinstance(app.state.observation_source, CollectorObservationSource)


class TestTheMutationPolicyIsExhaustive:
    def test_every_mutating_route_a_demo_serves_is_classified(
        self, demo_settings: Settings
    ) -> None:
        served = _mutating_routes(demo_settings)
        assert served == set(MUTATION_POLICY), (
            f"Unclassified: {sorted(served - set(MUTATION_POLICY))}. "
            f"Classified but not served: {sorted(set(MUTATION_POLICY) - served)}. "
            "Add an entry to app.demo_mode.policy.MUTATION_POLICY."
        )

    def test_production_serves_every_classified_route_but_the_demo_ones(
        self, production_settings: Settings
    ) -> None:
        served = _mutating_routes(production_settings)
        assert set(MUTATION_POLICY) - served == {("POST", "/api/v1/demo/scans")}

    def test_ingestion_is_the_unavailable_class(self) -> None:
        unavailable = {
            path
            for (_, path), entry in MUTATION_POLICY.items()
            if entry.treatment is Treatment.UNAVAILABLE
        }
        assert unavailable == {
            "/api/v1/scan-runs",
            "/api/v1/scan-runs/{run_id}/batches",
            "/api/v1/scan-runs/{run_id}/completion",
        }

    def test_the_runbook_export_is_the_simulated_class(self) -> None:
        simulated = [k for k, e in MUTATION_POLICY.items() if e.treatment is Treatment.SIMULATED]
        assert simulated == [("POST", "/api/v1/remediation/{plan_id}/exports")]


RUN = "/api/v1/scan-runs/00000000-0000-0000-0000-000000000001"


class TestTheGuard:
    @pytest.mark.parametrize("path", ["/api/v1/scan-runs", f"{RUN}/batches", f"{RUN}/completion"])
    async def test_demo_refuses_external_ingestion_anonymously(
        self, demo_client: AsyncClient, path: str
    ) -> None:
        response = await demo_client.post(path, json={})

        assert response.status_code == 403
        body = response.json()
        assert body["status"] == "demo_only"
        assert "not available in the demo environment" in body["detail"]

    async def test_demo_refuses_external_ingestion_from_an_administrator(
        self, demo_settings: Settings
    ) -> None:
        token = token_for_roles(demo_settings, Role.ADMIN)
        async with await _client(demo_settings, Authorization=f"Bearer {token}") as client:
            response = await client.post("/api/v1/scan-runs", json={})

        assert response.status_code == 403
        assert response.json()["status"] == "demo_only"

    async def test_production_ingestion_is_untouched(self, production_client: AsyncClient) -> None:
        response = await production_client.post("/api/v1/scan-runs", json={})
        assert response.status_code == 401

    async def test_a_header_cannot_lift_the_guard(self, demo_client: AsyncClient) -> None:
        response = await demo_client.post(
            "/api/v1/scan-runs",
            json={},
            headers={"X-ADG-Runtime-Mode": "production", "X-ADG-Collector-Key": "x" * 40},
        )
        assert response.status_code == 403

    async def test_reads_still_work_in_demo(self, demo_client: AsyncClient) -> None:
        assert (await demo_client.get("/health/live")).status_code == 200
        assert (await demo_client.get("/api/v1/scan-runs")).status_code == 401  # needs a token


class TestTheRuntimeSurface:
    async def test_demo_says_so_publicly(self, demo_client: AsyncClient) -> None:
        body = (await demo_client.get("/runtime")).json()

        assert body["runtime_mode"] == "demo"
        assert body["data_source"] == "synthetic"
        assert body["synthetic"] is True
        assert body["dataset"]["company_name"] == "ADG reference estate"
        assert "No production Active Directory or file servers are connected" in body["notice"]

    async def test_production_says_production_and_nothing_else(
        self, production_client: AsyncClient
    ) -> None:
        body = (await production_client.get("/runtime")).json()

        assert body == {
            "runtime_mode": "production",
            "data_source": "collectors",
            "synthetic": False,
            "dataset": None,
            "notice": None,
        }

    async def test_the_runtime_surface_discloses_no_connection_detail(
        self, demo_client: AsyncClient
    ) -> None:
        raw = (await demo_client.get("/runtime")).text + (await demo_client.get("/version")).text
        raw += (await demo_client.get("/health/ready")).text
        # "postgresql" is not here: readiness has always named its dependency by engine.
        for secret in ("adg_demo", "localhost", "5432", "pw@", "psycopg", "://"):
            assert secret not in raw

    async def test_readiness_and_version_report_the_mode(self, demo_client: AsyncClient) -> None:
        ready = (await demo_client.get("/health/ready")).json()
        version = (await demo_client.get("/version")).json()

        assert (ready["runtime_mode"], ready["data_source"]) == ("demo", "synthetic")
        assert version["runtime_mode"] == "demo"

    async def test_every_demo_response_is_marked(self, demo_client: AsyncClient) -> None:
        response = await demo_client.get("/health/live")
        assert response.headers[RUNTIME_MODE_HEADER] == "demo"

    @pytest.mark.parametrize("roles", [(), (Role.REMEDIATION_PLANNER,)])
    async def test_a_simulated_route_carries_the_notice_whatever_it_answers(
        self, demo_settings: Settings, roles: tuple[Role, ...]
    ) -> None:
        """The notice header is sent on refusals too, and a header holds latin-1 only.

        The export is the one simulated route. Its notice once crashed every answer from it
        (HTTP 500, unmarked) because the em dash in :data:`SIMULATION_NOTICE` cannot be sent
        in a header.
        """
        token = token_for_roles(demo_settings, *roles)
        headers = {"Authorization": f"Bearer {token}"} if roles else {}
        async with await _client(demo_settings, **headers) as client:
            response = await client.post(
                "/api/v1/remediation/00000000-0000-0000-0000-000000000000/exports", json={}
            )

        assert response.status_code == (403 if roles else 401)
        assert response.headers[RUNTIME_MODE_HEADER] == "demo"
        notice = response.headers[DEMO_NOTICE_HEADER]
        assert notice.isascii()
        assert notice == SIMULATION_NOTICE.replace("—", "-")

    async def test_production_responses_are_not_marked(
        self, production_client: AsyncClient
    ) -> None:
        response = await production_client.get("/health/live")
        assert RUNTIME_MODE_HEADER not in response.headers
        assert DEMO_NOTICE_HEADER not in response.headers

    async def test_the_demo_routes_do_not_exist_in_production(
        self, production_settings: Settings
    ) -> None:
        paths = create_app(production_settings).openapi()["paths"]
        assert not [path for path in paths if path.startswith("/api/v1/demo")]

    async def test_no_route_changes_the_mode(self, demo_settings: Settings) -> None:
        """There is no switch: the runtime surface is read-only and no path names a switch."""
        switch_words = {"mode", "runtime-mode", "switch", "production", "go-live", "live-data"}
        for settings in (demo_settings, build_settings(environment="test")):
            paths = create_app(settings).openapi()["paths"]
            assert set(paths["/runtime"]) == {"get"}
            for path in paths:
                assert not switch_words & set(path.strip("/").split("/")), path


class TestTheRunbookIsInert:
    RUNBOOK = (
        "#Requires -Version 7\n<#\n    ADG change plan\n#>\n[CmdletBinding()]\nparam(\n"
        "    [switch]$Execute\n)\nRemove-ADGroupMember -Identity 'x' -Members 'y'\n"
    )

    def test_every_generated_line_is_commented_out(self) -> None:
        inert = defang_runbook(self.RUNBOOK, None)
        executable = [
            line for line in inert.splitlines() if line.strip() and not line.startswith("#")
        ]
        assert len(executable) == 1
        assert executable[0].startswith("throw 'ADG demo environment")
        assert SIMULATION_NOTICE in inert
        assert "# Remove-ADGroupMember -Identity 'x' -Members 'y'" in inert

    def test_the_export_is_defanged_only_in_demo(
        self, demo_settings: Settings, production_settings: Settings
    ) -> None:
        detail = ExportDetailView.model_construct(runbook=self.RUNBOOK, demo_notice=None)

        assert _demo_safe(detail, production_settings) is detail
        demo = _demo_safe(detail, demo_settings)
        assert demo.demo_notice == SIMULATION_NOTICE
        assert demo.runbook is not None and demo.runbook.startswith("# ADG DEMO ENVIRONMENT")
        assert "ADG reference estate (corp.example.com)" in demo.runbook


@pytest.fixture
def recorded_subprocesses(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    calls: list[list[str]] = []
    original = subprocess.Popen.__init__

    def record(self: subprocess.Popen[Any], args: Any, *rest: Any, **kwargs: Any) -> None:
        calls.append([str(part) for part in (args if isinstance(args, list) else [args])])
        original(self, args, *rest, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", record)
    yield calls


class TestIsolation:
    async def test_a_demo_application_loads_no_infrastructure_client(
        self, demo_client: AsyncClient
    ) -> None:
        await demo_client.get("/runtime")
        await demo_client.get("/health/ready")
        loaded = [name for name in INFRASTRUCTURE_MODULES if name in sys.modules]
        assert loaded == []

    async def test_a_demo_application_starts_no_process(
        self, demo_settings: Settings, recorded_subprocesses: list[list[str]]
    ) -> None:
        async with await _client(demo_settings) as client:
            await client.get("/runtime")
            await client.post("/api/v1/scan-runs", json={})
        assert recorded_subprocesses == []

    def test_no_demo_mode_module_names_powershell_or_a_directory_client(self) -> None:
        import pathlib

        import app.demo_mode

        package = pathlib.Path(app.demo_mode.__file__).parent
        for source in package.glob("*.py"):
            text = source.read_text(encoding="utf-8").casefold()
            for forbidden in ("pwsh", "import ldap", "smbclient", "winrm", "invoke-command"):
                assert forbidden not in text, f"{source.name} mentions {forbidden}"
