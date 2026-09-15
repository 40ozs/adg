"""Remediation cannot write a collected fact, proved from the syntax tree.

The structural twin of ``tests/governance/test_isolation.py``, extended to this package
because it has the same property to protect and a stronger reason to: governance records a
judgment *about* an observation, and remediation records an instruction about one. Neither
may change the observation, and this package additionally reads the ACL tables — which is
exactly the situation in which somebody eventually writes to one by reflex.

The behavioral half is ``tests/db/test_remediation_api.py``, which digests every collected
table around a whole plan lifecycle over HTTP and requires them byte-identical. This one
proves no such path **exists**.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.models import schema

# app/models/schema.py -> parents[1] is app/, so this is app/remediation/.
REMEDIATION_ROOT = pathlib.Path(schema.__file__).resolve().parents[1] / "remediation"

#: Tables holding ADG's own records rather than what a collector reported. Remediation may
#: write these four and the governance audit trail, and may write nothing else. Listed rather
#: than derived, because this is the part somebody has to edit on purpose: a table added in a
#: later phase is protected by default.
OWN_TABLES = frozenset(
    {
        "remediation_change_plans",
        "remediation_planned_changes",
        "remediation_approvals",
        "remediation_exports",
        "governance_audit_events",
        "remediation_proposals",
        "resource_owners",
        "review_campaigns",
        "review_campaign_scopes",
        "review_assignments",
        "review_items",
        "review_decisions",
    }
)

COLLECTED_TABLES = frozenset(name for name in schema.metadata.tables if name not in OWN_TABLES)

WRITING_CALLS = frozenset({"insert", "update", "delete"})

#: Module aliases a SQLAlchemy Core write may be reached through. This codebase imports the
#: constructors directly (``from sqlalchemy import insert``), and migrations spell them
#: ``sa.insert``; both are recognized.
#:
#: The narrowing matters. ``update`` is also a ``dict`` method, and
#: ``app/remediation/preconditions.py`` legitimately calls ``detail.update(...)`` while
#: building a response. A guard that read that as a database write would be suppressed with a
#: noqa within the week, and a suppressed guard protects nothing — so an attribute-form call
#: counts only when its receiver is a SQLAlchemy module.
SQLALCHEMY_ALIASES = frozenset({"sa", "sqlalchemy"})


def remediation_modules() -> list[pathlib.Path]:
    return sorted(REMEDIATION_ROOT.glob("*.py"))


def writes_in(source: str) -> list[tuple[str, str, int]]:
    """Every ``(operation, table, line)`` the module performs, as written.

    Conservative on purpose, exactly as the governance guard is: it cannot see through a
    variable, so ``insert(some_table)`` is reported as ``<not a name>`` too. That is a
    refusal to guess rather than a false positive — a write whose target cannot be read off
    the page does not belong in this package.
    """
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            name = function.id
        elif isinstance(function, ast.Attribute):
            receiver = function.value
            if not (isinstance(receiver, ast.Name) and receiver.id in SQLALCHEMY_ALIASES):
                continue
            name = function.attr
        else:
            continue
        if name not in WRITING_CALLS or not node.args:
            continue
        target = node.args[0]
        found.append(
            (name, target.id if isinstance(target, ast.Name) else "<not a name>", node.lineno)
        )
    return found


class TestTheRemediationPackageWritesNoCollectedFact:
    def test_the_package_is_where_this_test_thinks_it_is(self) -> None:
        """A guard whose glob silently matched nothing would pass forever."""
        assert REMEDIATION_ROOT.is_dir()
        assert len(remediation_modules()) >= 7

    def test_the_collected_table_set_is_not_empty(self) -> None:
        """The same failure one level up: if the schema's table names ever stopped being
        readable here, every assertion below would vacuously pass."""
        assert "ntfs_aces" in COLLECTED_TABLES
        assert "smb_share_aces" in COLLECTED_TABLES
        assert "membership_edges" in COLLECTED_TABLES
        assert "object_versions" in COLLECTED_TABLES
        assert not (COLLECTED_TABLES & OWN_TABLES)

    def test_the_four_new_tables_exist_and_are_this_packages_own(self) -> None:
        for name in (
            "remediation_change_plans",
            "remediation_planned_changes",
            "remediation_approvals",
            "remediation_exports",
        ):
            assert name in schema.metadata.tables
            assert name not in COLLECTED_TABLES

    @pytest.mark.parametrize("module", remediation_modules(), ids=lambda path: path.name)
    def test_no_module_writes_a_table_holding_collected_state(self, module: pathlib.Path) -> None:
        offenders = [
            f"{module.name}:{line} {operation}({table})"
            for operation, table, line in writes_in(module.read_text(encoding="utf-8"))
            if table in COLLECTED_TABLES
        ]

        assert not offenders, (
            "A remediation module writes a table holding what a collector reported:\n  "
            + "\n  ".join(offenders)
            + "\nA change plan describes a change somebody might make in Windows. ADG makes "
            "none of them, and it certainly does not make them to its own copy of the "
            "estate -- which would produce a report of work that had not happened."
        )

    @pytest.mark.parametrize("module", remediation_modules(), ids=lambda path: path.name)
    def test_every_write_names_its_table_on_the_page(self, module: pathlib.Path) -> None:
        opaque = [
            f"{module.name}:{line} {operation}(...)"
            for operation, table, line in writes_in(module.read_text(encoding="utf-8"))
            if table == "<not a name>"
        ]

        assert not opaque, (
            "A remediation module writes to a table this guard cannot identify:\n  "
            + "\n  ".join(opaque)
            + "\nWrite the table name literally so the isolation check can read it."
        )

    def test_the_guard_ignores_a_dictionary_update(self) -> None:
        """``detail.update(...)`` builds a response. A guard that called it a database write
        would be switched off, and a switched-off guard protects nothing."""
        assert writes_in("detail.update(dict(observed.detail))") == []

    def test_the_guard_still_sees_a_write_through_a_module_alias(self) -> None:
        assert writes_in("sa.insert(ntfs_aces)") == [("insert", "ntfs_aces", 1)]

    def test_the_guard_actually_catches_a_violation(self) -> None:
        """The exact mistake it exists to prevent: marking the estate as though the plan had
        been carried out."""
        violation = "await session.execute(delete(ntfs_aces).where(ntfs_aces.c.ace_key == key))"

        found = writes_in(violation)

        assert ("delete", "ntfs_aces", 1) in found
        assert "ntfs_aces" in COLLECTED_TABLES
