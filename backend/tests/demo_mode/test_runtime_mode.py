"""The runtime mode is a validated deployment setting that fails closed.

Three claims, each a way a demo boundary quietly stops being one:

* production is what an *unset* variable means, and nothing else falls back to it;
* demo refuses every configuration that could connect it to something real -- a database
  not named for the demo, a collector credential, an outbound alert sink;
* a production deployment refuses a demo setting rather than ignoring it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, build_settings
from app.runtime_mode import RuntimeMode, UnknownRuntimeMode, parse_runtime_mode

DEMO_DB = "postgresql+psycopg://adg:pw@localhost:5432/adg_demo"


def demo(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "runtime_mode": "demo",
        "database_url": DEMO_DB,
        "demo_dataset": "adg-reference-estate",
    }
    values.update(overrides)
    return build_settings(**values)


class TestParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("production", RuntimeMode.PRODUCTION),
            ("demo", RuntimeMode.DEMO),
            (" Demo ", RuntimeMode.DEMO),
            ("PRODUCTION", RuntimeMode.PRODUCTION),
        ],
    )
    def test_the_two_modes_are_recognized(self, raw: str, expected: RuntimeMode) -> None:
        assert parse_runtime_mode(raw) is expected

    @pytest.mark.parametrize("raw", ["foobar", "", "   ", "prod", "live", "sandbox", "demo-mode"])
    def test_anything_else_is_refused_and_never_read_as_production(self, raw: str) -> None:
        with pytest.raises(UnknownRuntimeMode, match="does not fall back to production"):
            parse_runtime_mode(raw)

    def test_a_non_string_is_refused(self) -> None:
        with pytest.raises(UnknownRuntimeMode):
            parse_runtime_mode(1)


class TestSettings:
    def test_an_unset_mode_is_production_and_behaves_as_before(self) -> None:
        settings = build_settings()

        assert settings.runtime_mode is RuntimeMode.PRODUCTION
        assert settings.is_demo is False
        assert settings.runtime_mode.data_source == "collectors"

    def test_an_unknown_mode_is_a_startup_error(self) -> None:
        with pytest.raises(ValidationError, match="ADG_RUNTIME_MODE must be one of"):
            build_settings(runtime_mode="foobar")

    def test_an_unknown_mode_from_the_environment_is_a_startup_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ADG_RUNTIME_MODE", "foobar")
        with pytest.raises(ValidationError, match="received 'foobar'"):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_an_empty_mode_from_the_environment_is_a_startup_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ADG_RUNTIME_MODE", "")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_demo_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADG_RUNTIME_MODE", "demo")
        monkeypatch.setenv("ADG_DATABASE_URL", DEMO_DB)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.is_demo
        assert settings.runtime_mode.data_source == "synthetic"

    def test_demo_defaults_to_the_canonical_dataset(self) -> None:
        assert demo(demo_dataset="").resolved_demo_dataset == "genesee-press-v1"

    def test_demo_needs_no_production_credential(self) -> None:
        """No OIDC, no collector key, no signing key: a demo starts with none of them."""
        settings = demo()

        assert settings.collector_api_keys == ""
        assert settings.oidc_issuer == ""
        assert settings.remediation_signing_key == ""


class TestDemoRefusals:
    @pytest.mark.parametrize(
        "database", ["adg", "adg_prod", "production", "demolition", "adgdemo", "nodemo"]
    )
    def test_a_database_not_named_for_the_demo_is_refused(self, database: str) -> None:
        with pytest.raises(ValidationError, match="dedicated demo database"):
            demo(database_url=f"postgresql+psycopg://adg:pw@localhost:5432/{database}")

    @pytest.mark.parametrize("database", ["adg_demo", "adg-demo", "demo", "adg_demo_test"])
    def test_a_demo_named_database_is_accepted(self, database: str) -> None:
        assert demo(database_url=f"postgresql+psycopg://adg:pw@localhost:5432/{database}").is_demo

    def test_the_development_default_database_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="dedicated demo database"):
            build_settings(runtime_mode="demo")

    def test_a_collector_credential_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="ADG_COLLECTOR_API_KEYS must be empty"):
            demo(collector_api_keys="fs01:" + "x" * 40)

    def test_an_unregistered_dataset_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not a registered demo dataset"):
            demo(demo_dataset="acme-v9")

    def test_an_outbound_alert_sink_is_refused_at_startup(self, tmp_path: Path) -> None:
        policy = tmp_path / "alerts.json"
        policy.write_text(
            json.dumps(
                {
                    "sinks": [
                        {"name": "ops", "kind": "webhook", "options": {"url": "https://x.test/h"}}
                    ]
                }
            )
        )
        with pytest.raises(ValidationError, match="allows only log alert sinks"):
            demo(alert_policy_path=str(policy))

    def test_an_outbound_sink_added_after_startup_is_refused_at_the_next_read(
        self, tmp_path: Path
    ) -> None:
        policy = tmp_path / "alerts.json"
        policy.write_text(json.dumps({"sinks": [{"name": "log", "kind": "log"}]}))
        settings = demo(alert_policy_path=str(policy))
        policy.write_text(
            json.dumps(
                {
                    "sinks": [
                        {"name": "ops", "kind": "webhook", "options": {"url": "https://x.test/h"}}
                    ]
                }
            )
        )
        with pytest.raises(ValueError, match="allows only log alert sinks"):
            _ = settings.alert_policy

    def test_a_remediation_executor_is_refused(self, tmp_path: Path) -> None:
        fixture = tmp_path / "lab.json"
        fixture.write_text("{}")
        with pytest.raises(ValidationError, match="must be 'disabled'"):
            demo(remediation_execution_mode="lab", remediation_lab_fixture_path=str(fixture))

    def test_production_refuses_a_demo_dataset_setting(self) -> None:
        with pytest.raises(ValidationError, match="ADG_RUNTIME_MODE is production"):
            build_settings(demo_dataset="adg-reference-estate")

    def test_production_keeps_its_own_rules_unchanged(self) -> None:
        """Nothing demo-specific is asked of production: any database name, collector keys."""
        settings = build_settings(
            database_url="postgresql+psycopg://adg:pw@db:5432/adg",
            collector_api_keys="fs01:" + "x" * 40,
        )
        assert settings.collector_key_map == {"fs01": "x" * 40}
