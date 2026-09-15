"""The enums written out in ``app/models/schema.py`` must equal the ones in ``app/alerts``.

The duplication is deliberate and is the same one :class:`app.models.schema.VersionOrigin`
explains: the values appear inside check constraints, so the enum has to be importable
without importing anything that imports the schema module — and :mod:`app.alerts.model`
imports :mod:`app.domain`, which imports it.

These tests are what make writing them out safe. Without them, adding a trigger to
:class:`app.alerts.AlertTrigger` would produce an application that raises alerts the database
refuses to store, and the refusal would arrive as an integrity error in the middle of an
ingestion rather than as a failing test.
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from app.alerts import (
    AlertLifecycle,
    AlertStatus,
    AlertTransition,
    AlertTrigger,
    DeliveryStatus,
    WatchKind,
)
from app.alerts.model import MAX_COOLDOWN, MIN_COOLDOWN
from app.models.schema import (
    AlertLifecycleValue,
    AlertStatusValue,
    AlertTransitionValue,
    AlertTriggerValue,
    DeliveryStatusValue,
    WatchKindValue,
)

PAIRS: list[tuple[type[StrEnum], type[StrEnum]]] = [
    (WatchKind, WatchKindValue),
    (AlertTrigger, AlertTriggerValue),
    (AlertLifecycle, AlertLifecycleValue),
    (AlertStatus, AlertStatusValue),
    (AlertTransition, AlertTransitionValue),
    (DeliveryStatus, DeliveryStatusValue),
]


@pytest.mark.parametrize(
    ("domain", "schema"), PAIRS, ids=lambda item: getattr(item, "__name__", str(item))
)
def test_the_schema_copy_matches_the_domain_enum(
    domain: type[StrEnum], schema: type[StrEnum]
) -> None:
    assert {member.value for member in domain} == {member.value for member in schema}


def test_the_migrations_vocabularies_match_the_domains() -> None:
    """The revision's frozen copies too.

    A migration keeps creating what it created on the day it ran, so its lists are frozen on
    purpose -- but they are frozen *at the values that existed when it was written*, and this
    is the revision that introduced them. The day somebody adds a trigger, this fails and the
    fix is a new revision altering the constraint, which is a visible change rather than a
    silent divergence between the enum and the column.
    """
    namespace = _migration()

    def frozen(name: str) -> set[str]:
        value = namespace[name]
        assert isinstance(value, tuple), f"{name} should be a frozen tuple of values"
        return set(value)

    assert frozen("WATCH_KINDS") == {member.value for member in WatchKind}
    assert frozen("TRIGGERS") == {member.value for member in AlertTrigger}
    assert frozen("LIFECYCLES") == {member.value for member in AlertLifecycle}
    assert frozen("ALERT_STATUSES") == {member.value for member in AlertStatus}
    assert frozen("TRANSITIONS") == {member.value for member in AlertTransition}
    assert frozen("DELIVERY_STATUSES") == {member.value for member in DeliveryStatus}


def test_the_migrations_cooldown_bounds_match_the_domains() -> None:
    """The check constraint and the value object have to agree.

    Both are enforced on purpose -- a row written by anything other than this application
    still has to obey the bound -- and two that disagreed would let the application refuse a
    cooldown the database accepts, or write one the database rejects halfway through a
    request.
    """
    namespace = _migration()

    assert namespace["MIN_COOLDOWN_SECONDS"] == int(MIN_COOLDOWN.total_seconds())
    assert namespace["MAX_COOLDOWN_SECONDS"] == int(MAX_COOLDOWN.total_seconds())


def _migration() -> dict[str, object]:
    """The revision's module-level constants, read without importing alembic.

    Parsed from the source rather than imported: a revision module imports ``alembic.op``,
    which is only bound inside a migration run, and importing one outside that context has
    side effects this test has no business having.
    """
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "database"
        / "migrations"
        / "versions"
        / "0013_alert_pipeline.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if value is None:
            continue
        try:
            values[name] = ast.literal_eval(value)
        except ValueError:
            continue
    return values
