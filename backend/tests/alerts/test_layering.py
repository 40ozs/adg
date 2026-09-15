"""The structural half of prompt requirement 6, as a test rather than as a convention.

*Do not embed email/Slack vendor specifics into the core risk engine* — and, pointed the
other way, do not let the alert pipeline reach into a rule. Both directions are here, because
a layering that is only documented is a layering that holds until somebody is in a hurry.

The import graph is read from the source rather than from ``sys.modules``, so a dependency
that exists but has not been executed by this test session still fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ALERTS = Path(__file__).resolve().parents[2] / "app" / "alerts"
RISK_ENGINE = Path(__file__).resolve().parents[2] / "app" / "risk_engine"

#: Packages the delivery pipeline must not know about.
#:
#: ``app.changes`` and ``app.risk_engine`` are the two sources it raises alerts *about*, and
#: reaching either would let a delivery channel depend on how a rule is computed. The rest
#: are the infrastructure a *pure* package must not need: a value module that imports a
#: session has stopped being testable in microseconds.
FORBIDDEN_IN_ALERTS = (
    "app.changes",
    "app.risk_engine",
    "app.repositories",
    "app.services",
    "sqlalchemy",
    "fastapi",
)

#: What the risk engine must not know about. It is Phase 8A's own property; this is the half
#: 8B could have broken, by teaching a rule to mention a sink.
FORBIDDEN_IN_RISK_ENGINE = ("app.alerts", "httpx", "sqlalchemy", "fastapi")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _modules(package: Path) -> list[Path]:
    return sorted(path for path in package.glob("*.py"))


@pytest.mark.parametrize("module", _modules(ALERTS), ids=lambda path: path.name)
def test_the_alert_pipeline_does_not_import_the_sources_it_reports_on(module: Path) -> None:
    """Including transitively-by-name: ``app.changes.model`` is caught as well as
    ``app.changes``."""
    offending = sorted(
        name
        for name in _imports(module)
        if any(name == root or name.startswith(f"{root}.") for root in FORBIDDEN_IN_ALERTS)
    )

    assert not offending, (
        f"{module.name} imports {offending}. app/alerts must stay free of the sources it "
        "raises alerts about and of the framework beneath them: the translation from a "
        "finding or a classified change into a flat record lives in app/services/alerts.py, "
        "which is allowed to know both."
    )


@pytest.mark.parametrize("module", _modules(RISK_ENGINE), ids=lambda path: path.name)
def test_the_risk_engine_does_not_import_the_alert_pipeline(module: Path) -> None:
    """Prompt requirement 6, in its original direction.

    A rule that could mention a sink is a rule whose severity somebody would eventually tune
    to make a chat channel quieter.
    """
    offending = sorted(
        name
        for name in _imports(module)
        if any(name == root or name.startswith(f"{root}.") for root in FORBIDDEN_IN_RISK_ENGINE)
    )

    assert not offending, (
        f"{module.name} imports {offending}; the rules must not know a delivery channel exists."
    )


def test_the_sinks_module_mentions_no_vendor() -> None:
    """The prose half of the same requirement.

    A sink that shaped a Slack block or an email MIME tree would have put a vendor's message
    format into ADG's core. Adding a chat integration means writing a sink that reshapes
    :meth:`DeliveryEnvelope.document`; it never means teaching the pipeline about a channel.
    """
    source = (ALERTS / "sinks.py").read_text(encoding="utf-8").casefold()
    body = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("#", "*"))
    )

    for vendor in ("slack", "pagerduty", "teams", "smtp", "sendgrid", "opsgenie"):
        # Named in prose (the module docstring says what it refuses to do); never in code.
        assert f'"{vendor}' not in body
        assert f"'{vendor}" not in body


def test_the_alert_service_is_where_the_translation_lives() -> None:
    """The positive half: the seam exists somewhere, rather than nowhere.

    Without this, deleting the conversion and inlining it into app/alerts would pass every
    test above by making app/services/alerts.py trivial.
    """
    service = Path(__file__).resolve().parents[2] / "app" / "services" / "alerts.py"
    imports = _imports(service)

    assert {"app.alerts", "app.changes", "app.risk_engine"} <= imports
