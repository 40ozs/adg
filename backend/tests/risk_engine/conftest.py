"""Fixtures shared by the risk-engine suites."""

from __future__ import annotations

import pytest

from app.risk_engine import (
    DEFAULT_CONFIGURATION,
    RiskConfiguration,
    SensitiveResource,
)


@pytest.fixture
def sensitive_configuration() -> RiskConfiguration:
    r"""The default configuration with one subtree declared sensitive.

    A fixture rather than a module constant because the sensitive-resource rule is the one
    whose behavior depends entirely on configuration, and a test that forgot to pass this gets
    the shipped default — which marks nothing and reports nothing — rather than silently
    inheriting a tag from another test.
    """
    return RiskConfiguration(
        version="test",
        disabled_rules=DEFAULT_CONFIGURATION.disabled_rules,
        severity_overrides=DEFAULT_CONFIGURATION.severity_overrides,
        option_overrides=DEFAULT_CONFIGURATION.option_overrides,
        sensitive_resources=(SensitiveResource(label="Payroll", path_prefix="\\\\FS01\\Finance"),),
    )
