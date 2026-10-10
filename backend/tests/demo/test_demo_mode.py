r"""Demo Mode is selected at startup and sealed: nothing real in, nothing out, no switch.

Three kinds of claim, each tested where it can be tested without a database:

* **The configuration boundary.** ``ADG_RUNTIME_MODE=demo`` refuses every setting that would
  let a demo receive or reach real data, and refuses to run on a database that was not named
  for it.
* **No runtime switch.** No route, parameter or schema in the published API names the mode,
  so a running deployment cannot be moved between demo and production by a request.
* **No production code path.** The Null Meridian package imports nothing that talks to a
  directory, a file share, PowerShell or the network; the only subprocess it starts is the
  schema migration. (The database half of this, with sockets refused at runtime, is in
  ``tests/db/test_null_meridian_demo.py``.)
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, build_settings
from app.db import ConnectivityResult
from app.demo.null_meridian.risk import DEMO_RISK_CONFIGURATION
from app.main import RUNTIME_MODE_HEADER, create_app
from app.risk_engine.configuration import DEFAULT_CONFIGURATION
from tests.conftest import StubDatabase

DEMO_URL = "postgresql+psycopg://adg:secret@localhost:5432/adg_demo"
PACKAGE = Path(__file__).resolve().parents[2] / "app" / "demo" / "null_meridian"
OPENAPI = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "v1" / "openapi.json"


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
    def test_the_default_is_standard(self) -> None:
        settings = build_settings(environment="test")
        assert settings.runtime_mode == "standard"
        assert settings.is_demo is False
        assert settings.risk_configuration is DEFAULT_CONFIGURATION

    def test_a_valid_demo(self) -> None:
        settings = demo()
        assert settings.is_demo
        assert settings.database_name == "adg_demo"
        assert settings.risk_configuration is DEMO_RISK_CONFIGURATION
        assert settings.risk_configuration.marks_anything_sensitive

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            (
                {
                    "environment": "production",
                    "auth_mode": "oidc",
                    "oidc_issuer": "https://login.example.invalid/t/v2.0",
                    "oidc_audience": "adg",
                    "oidc_jwks_url": "https://login.example.invalid/keys",
                },
                "ADG_ENVIRONMENT=production",
            ),
            (
                {"database_url": "postgresql+psycopg://adg:secret@localhost:5432/adg"},
                "does not contain 'demo'",
            ),
            ({"collector_api_keys": "dc01:" + "k" * 40}, "ADG_COLLECTOR_API_KEYS"),
            (
                {
                    "remediation_execution_mode": "lab",
                    "remediation_lab_fixture_path": "fixture.json",
                },
                "ADG_REMEDIATION_EXECUTION_MODE",
            ),
            ({"alert_policy_path": "policy.json"}, "ADG_ALERT_POLICY_PATH"),
        ],
    )
    def test_a_demo_refuses_anything_that_could_reach_real_data(
        self, overrides: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            demo(**overrides)

    def test_an_explicit_risk_configuration_still_wins(self, tmp_path: Path) -> None:
        path = tmp_path / "risk.json"
        path.write_text(json.dumps({"version": "operator"}), encoding="utf-8")
        assert demo(risk_configuration_path=str(path)).risk_configuration.version == "operator"

    def test_the_mode_comes_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADG_RUNTIME_MODE", "demo")
        monkeypatch.setenv("ADG_DATABASE_URL", DEMO_URL)
        assert Settings(_env_file=None).is_demo  # type: ignore[call-arg]

    def test_an_unknown_mode_is_refused(self) -> None:
        with pytest.raises(ValueError, match="runtime_mode"):
            demo(runtime_mode="production")


class TestNoRuntimeSwitch:
    def test_the_published_api_never_names_the_mode(self) -> None:
        document = OPENAPI.read_text(encoding="utf-8").casefold()
        assert "runtime_mode" not in document
        assert "runtime-mode" not in document
        assert '"demo' not in document

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

    async def test_a_standard_deployment_does_not(self, healthy_client: AsyncClient) -> None:
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

    @pytest.mark.parametrize("module", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
    def test_the_package_imports_no_network_or_directory_client(self, module: Path) -> None:
        imported = _imports(module)
        for name in self.FORBIDDEN:
            assert not any(item == name or item.startswith(f"{name}.") for item in imported), (
                f"{module.name} imports {name}"
            )

    def test_the_only_subprocess_is_the_migration(self) -> None:
        offenders = []
        for module in PACKAGE.glob("*.py"):
            source = module.read_text(encoding="utf-8")
            if "subprocess" in source and module.name != "reset.py":
                offenders.append(module.name)
            for marker in ("pwsh", "powershell.exe", "Invoke-Adg", "Get-SmbShare("):
                assert marker not in source, (module.name, marker)
        assert offenders == []
        reset = (PACKAGE / "reset.py").read_text(encoding="utf-8")
        assert reset.count("subprocess.run(") == 1
        assert '"alembic", "upgrade", "heads"' in reset

    def test_the_seeding_client_is_in_process(self) -> None:
        reset = (PACKAGE / "reset.py").read_text(encoding="utf-8")
        assert "demo-reset.invalid" in reset, "the seeding client addresses no real host"
        assert "AsyncClient(" not in reset
