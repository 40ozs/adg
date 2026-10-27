r"""Genesee Press under Demo Mode: selected at startup and sealed, with no switch.

Three kinds of claim, each tested where it can be tested without a database. The generic
Demo Mode boundary is in ``tests/demo_mode``; these are the claims about the canonical
dataset running inside it.

* **The configuration boundary.** ``ADG_RUNTIME_MODE`` is ``production`` or ``demo`` and
  nothing else; a demo runs Genesee Press by default, under Genesee Press's own risk
  configuration, and refuses every setting that would let it receive or reach real data.
* **No runtime switch.** No request parameter or request body in the published API can name
  the mode, so a running deployment cannot be moved between demo and production by a
  request. (The mode is *reported*, read-only, by ``GET /runtime``, ``/version`` and
  ``/health/ready``.)
* **No production code path.** The Genesee Press package imports nothing that talks to a
  directory, a file share, PowerShell or the network and starts no process; the one
  subprocess of the whole demo is Demo Mode's schema migration; synthetic ingestion is an
  in-process ASGI call; and the application imports without ``httpx``, a development-only
  dependency. (The database half of this, with sockets refused at runtime, is in
  ``tests/db/test_genesee_press_demo.py``.)
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, build_settings
from app.db import ConnectivityResult
from app.demo.genesee_press.risk import DEMO_RISK_CONFIGURATION
from app.domain.remediation import ExecutionMode
from app.main import RUNTIME_MODE_HEADER, create_app
from app.remediation.executor import DisabledRemediator, remediator_for
from app.risk_engine.catalog import RuleId
from app.risk_engine.configuration import DEFAULT_CONFIGURATION
from app.runtime_mode import RuntimeMode
from tests.conftest import StubDatabase

DEMO_URL = "postgresql+psycopg://adg:secret@localhost:5432/adg_demo"
BACKEND = Path(__file__).resolve().parents[2]
PACKAGE = BACKEND / "app" / "demo" / "genesee_press"
DEMO_MODE = BACKEND / "app" / "demo_mode"
OPENAPI = BACKEND.parent / "docs" / "contracts" / "v1" / "openapi.json"


def demo(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "development",
        "runtime_mode": "demo",
        "database_url": DEMO_URL,
        "log_format": "text",
    }
    values.update(overrides)
    return build_settings(**values)


class TestTheConfigurationBoundary:
    def test_the_default_is_production(self) -> None:
        settings = build_settings(environment="test")
        assert settings.runtime_mode is RuntimeMode.PRODUCTION
        assert settings.is_demo is False
        assert settings.risk_configuration is DEFAULT_CONFIGURATION

    def test_a_valid_demo_runs_genesee_press_under_its_own_risk_configuration(self) -> None:
        settings = demo()
        assert settings.is_demo
        assert settings.resolved_demo_dataset == "genesee-press-v1"
        assert settings.risk_configuration is DEMO_RISK_CONFIGURATION
        assert settings.risk_configuration.marks_anything_sensitive

    def test_the_demo_tuning_is_the_datasets_alone(self) -> None:
        """redundant_access_paths at four routes is Genesee Press's operator tuning; the
        shipped default, and therefore production, is unchanged."""
        rule = RuleId("redundant_access_paths")
        assert DEMO_RISK_CONFIGURATION.for_rule(rule).options["minimum_paths"] == 4
        assert DEFAULT_CONFIGURATION.for_rule(rule).options["minimum_paths"] == 2
        assert build_settings(environment="test").risk_configuration is DEFAULT_CONFIGURATION

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            (
                {"database_url": "postgresql+psycopg://adg:secret@localhost:5432/adg"},
                "dedicated demo database",
            ),
            (
                {"database_url": "postgresql+psycopg://adg:secret@localhost:5432/adgdemo"},
                "dedicated demo database",
            ),
            ({"collector_api_keys": "dc01:" + "k" * 40}, "ADG_COLLECTOR_API_KEYS"),
            (
                {
                    "remediation_execution_mode": "lab",
                    "remediation_lab_fixture_path": "fixture.json",
                },
                "ADG_REMEDIATION_EXECUTION_MODE",
            ),
        ],
    )
    def test_a_demo_refuses_anything_that_could_reach_real_data(
        self, overrides: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            demo(**overrides)

    def test_a_demo_refuses_an_outbound_alert_sink(self, tmp_path: Path) -> None:
        policy = tmp_path / "alerts.json"
        policy.write_text(
            json.dumps(
                {
                    "sinks": [
                        {"name": "ops", "kind": "webhook", "options": {"url": "https://x.test"}}
                    ]
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="allows only log alert sinks"):
            demo(alert_policy_path=str(policy))

    def test_a_public_demo_may_run_with_production_strictness(self) -> None:
        """``ADG_ENVIRONMENT`` is how strict a deployment is, not what it is connected to.
        A public demo is a production-grade deployment of demo mode -- OIDC, no development
        sign-in -- and the reset's boundary is the runtime mode, the database name and the
        deployment marker, none of which this relaxes."""
        settings = demo(
            environment="production",
            auth_mode="oidc",
            oidc_issuer="https://login.example.invalid/t/v2.0",
            oidc_audience="adg",
            oidc_jwks_url="https://login.example.invalid/keys",
        )
        assert settings.is_demo and settings.is_production

    def test_a_demo_holds_only_the_refusing_remediator(self) -> None:
        """Remediation execution is disabled in a demo: the factory the remediation service
        consults hands it the remediator that refuses every plan, and nothing else."""
        settings = demo()
        remediator = remediator_for(
            ExecutionMode(settings.remediation_execution_mode),
            environment=settings.environment,
            fixture_path=settings.remediation_lab_fixture_path,
        )
        assert isinstance(remediator, DisabledRemediator)

    def test_an_explicit_risk_configuration_still_wins(self, tmp_path: Path) -> None:
        path = tmp_path / "risk.json"
        path.write_text(json.dumps({"version": "operator"}), encoding="utf-8")
        assert demo(risk_configuration_path=str(path)).risk_configuration.version == "operator"

    def test_the_mode_comes_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADG_RUNTIME_MODE", "demo")
        monkeypatch.setenv("ADG_DATABASE_URL", DEMO_URL)
        assert Settings(_env_file=None).is_demo  # type: ignore[call-arg]

    @pytest.mark.parametrize("value", ["standard", "Standard", "prod", "live", ""])
    def test_the_retired_standard_value_and_every_synonym_are_refused(self, value: str) -> None:
        """``standard`` was the Genesee Press branch's name for production. The integrated
        contract is ``production|demo``; a second spelling of production is refused rather
        than mapped, like any other unrecognized value."""
        with pytest.raises(ValueError, match=r"must be one of: production, demo"):
            build_settings(environment="test", runtime_mode=value)


def _request_schemas(document: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Every parameter name and every request-body schema, with ``$ref`` resolved."""
    components = document.get("components", {}).get("schemas", {})
    names: list[str] = []
    bodies: list[dict[str, Any]] = []

    def resolve(schema: Any, seen: frozenset[str]) -> None:
        if isinstance(schema, dict):
            ref = schema.get("$ref")
            if isinstance(ref, str):
                name = ref.rsplit("/", 1)[-1]
                if name not in seen and name in components:
                    resolve(components[name], seen | {name})
                return
            bodies.append(schema)
            for value in schema.values():
                resolve(value, seen)
        elif isinstance(schema, list):
            for value in schema:
                resolve(value, seen)

    for operations in document["paths"].values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            names += [str(item.get("name", "")) for item in operation.get("parameters", [])]
            resolve(operation.get("requestBody", {}), frozenset())
    return names, bodies


class TestNoRuntimeSwitch:
    def test_no_request_in_the_published_api_can_name_the_mode(self) -> None:
        document = json.loads(OPENAPI.read_text(encoding="utf-8"))
        names, bodies = _request_schemas(document)
        properties = {name for body in bodies for name in body.get("properties", {})}

        assert not [name for name in names if "mode" in name.casefold()]
        assert not [name for name in properties if "runtime" in name.casefold()]
        assert set(document["paths"]["/runtime"]) == {"get"}

    def test_no_route_accepts_a_mode(self) -> None:
        app = create_app(demo())
        for route in app.routes:
            path = str(getattr(route, "path", ""))
            assert "mode" not in path.casefold() or "auth" in path

    async def test_every_response_from_a_demo_says_so(self) -> None:
        app = create_app(demo())
        app.state.database = StubDatabase(ConnectivityResult(ok=True, latency_ms=1.0))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get("/health/live")
        assert response.headers[RUNTIME_MODE_HEADER] == "demo"

    async def test_a_production_deployment_does_not(self, healthy_client: AsyncClient) -> None:
        response = await healthy_client.get("/health/live")
        assert RUNTIME_MODE_HEADER not in response.headers


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestNoProductionCodePath:
    FORBIDDEN = (
        "ldap",
        "ldap3",
        "smbclient",
        "smbprotocol",
        "impacket",
        "pypsrp",
        "winrm",
        "socket",
        "httpx",
        "requests",
        "urllib.request",
        "paramiko",
    )

    @pytest.mark.parametrize(
        "module",
        sorted([*PACKAGE.glob("*.py"), *DEMO_MODE.glob("*.py")]),
        ids=lambda p: f"{p.parent.name}/{p.name}",
    )
    def test_no_demo_module_imports_a_network_or_directory_client(self, module: Path) -> None:
        imported = _imports(module)
        for name in self.FORBIDDEN:
            assert not any(item == name or item.startswith(f"{name}.") for item in imported), (
                f"{module.name} imports {name}"
            )

    def test_the_only_subprocess_is_demo_modes_migration(self) -> None:
        for module in [*PACKAGE.glob("*.py"), *DEMO_MODE.glob("*.py")]:
            source = module.read_text(encoding="utf-8")
            if module != DEMO_MODE / "lifecycle.py":
                assert "subprocess" not in source, module.name
            for marker in ("pwsh", "powershell.exe", "Invoke-Adg", "Get-SmbShare("):
                assert marker not in source, (module.name, marker)
        lifecycle = (DEMO_MODE / "lifecycle.py").read_text(encoding="utf-8")
        assert lifecycle.count("subprocess.run(") == 1
        assert '"alembic", "upgrade", "heads"' in lifecycle

    def test_genesee_press_has_no_reset_of_its_own(self) -> None:
        """One reset, one safety boundary: Demo Mode's. The generator only generates."""
        assert not (PACKAGE / "reset.py").exists()
        for module in PACKAGE.glob("*.py"):
            imported = _imports(module)
            assert not {"app.db", "app.config", "sqlalchemy", "app.models.schema"} & imported, (
                module.name
            )

    def test_the_seeding_client_is_in_process(self) -> None:
        ingest = (DEMO_MODE / "ingest.py").read_text(encoding="utf-8")
        assert ".invalid" in ingest, "the in-process client addresses no real host"
        assert "AsyncClient(" not in ingest

    def test_the_application_imports_without_httpx(self) -> None:
        """``httpx`` is a development dependency the API image does not install. The demo's
        in-process ingestion used to import it, which stopped ``app.main`` -- production
        included -- from importing at all outside a development environment."""
        program = (
            "import sys\n"
            "sys.modules['httpx'] = None\n"
            "import app.main, app.demo_mode.lifecycle, app.demo_mode.ingest, app.api.demo\n"
            "import app.demo_mode.__main__, app.demo.genesee_press.dataset\n"
            "print('imported')\n"
        )
        # Inherit the environment: a replaced one drops what the interpreter itself needs to
        # start (e.g. setup-python's LD_LIBRARY_PATH for a shared libpython).
        env = os.environ.copy()
        env["PYTHONPATH"] = str(BACKEND)
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]
        assert "imported" in completed.stdout


class TestTheOperatorCommand:
    def test_json_output_is_the_report_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The in-process ingestion builds the application, which installs a log handler on
        standard output mid-command. ``--json`` must still print parseable JSON only."""
        import logging

        from app.config import get_settings
        from app.demo_mode import __main__ as cli
        from app.demo_mode.lifecycle import StatusReport
        from app.logging_config import configure_logging

        async def noisy_status(settings: Settings, *, with_fingerprint: bool = False) -> Any:
            configure_logging(level="WARNING", log_format="text")  # as create_app does
            logging.getLogger("adg.auth").warning("auth.mode.development")
            return StatusReport(
                runtime_mode=RuntimeMode.DEMO,
                dataset_key="genesee-press-v1",
                dataset=None,
                dataset_error=None,
                inspection=None,
                target_error="not inspected in this test",
            )

        monkeypatch.setattr(cli, "demo_status", noisy_status)
        monkeypatch.setenv("ADG_RUNTIME_MODE", "demo")
        monkeypatch.setenv("ADG_DATABASE_URL", DEMO_URL)
        get_settings.cache_clear()
        try:
            assert cli.main(["--json", "status"]) == 0
        finally:
            get_settings.cache_clear()

        captured = capsys.readouterr()
        assert json.loads(captured.out)["dataset_key"] == "genesee-press-v1"
        assert "auth.mode.development" in captured.err
