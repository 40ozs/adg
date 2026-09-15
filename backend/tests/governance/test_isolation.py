"""Governance cannot write a collected fact, proved from the syntax tree rather than by review.

This is the structural half of the phase's central acceptance criterion; the behavioral half
is ``tests/db/test_governance_isolation.py``, which runs a whole campaign against a real
estate and asserts every collected table is byte-identical afterwards.

The two catch different things. The behavioral test proves nothing was written *on the paths
it exercised*. This one proves no such path **exists**: it walks every module under
``app/governance`` and fails if any ``insert()``, ``update()`` or ``delete()`` in them names a
table that holds what a collector reported. A method added in a later phase that writes
``ntfs_aces`` fails here the moment it is written, whether or not anybody thought to call it
from a test.

The check is deliberately syntactic and deliberately conservative. It cannot see through a
variable, so it also fails on ``insert(some_table)`` where the table is not a literal name —
which is not a false positive so much as a refusal to guess: a write whose target cannot be
read off the page does not belong in this package.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.models import schema

# app/models/schema.py -> parents[1] is app/, so this is app/governance/.
GOVERNANCE_ROOT = pathlib.Path(schema.__file__).resolve().parents[1] / "governance"

#: Tables holding what a collector reported, plus the timeline derived from them. Governance
#: may read any of these and may write none. Derived from the schema module rather than
#: listed, so a table added in a later phase is protected by default: the exemption list
#: below is the part somebody has to edit on purpose.
GOVERNANCE_TABLES = frozenset(
    {
        "resource_owners",
        "review_campaigns",
        "review_campaign_scopes",
        "review_assignments",
        "review_items",
        "review_decisions",
        "remediation_proposals",
        "governance_audit_events",
    }
)

COLLECTED_TABLES = frozenset(
    name for name in schema.metadata.tables if name not in GOVERNANCE_TABLES
)

WRITING_CALLS = frozenset({"insert", "update", "delete"})


def governance_modules() -> list[pathlib.Path]:
    return sorted(path for path in GOVERNANCE_ROOT.glob("*.py"))


def writes_in(source: str) -> list[tuple[str, str, int]]:
    """Every ``(operation, table, line)`` the module performs, as written.

    A call is a write when its function is named ``insert``, ``update`` or ``delete`` — the
    SQLAlchemy Core constructors this codebase uses everywhere — and its first argument is a
    bare name. The name is what is checked against the table sets.
    """
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.id
            if isinstance(function, ast.Name)
            else function.attr
            if isinstance(function, ast.Attribute)
            else None
        )
        if name not in WRITING_CALLS or not node.args:
            continue
        target = node.args[0]
        found.append(
            (name, target.id if isinstance(target, ast.Name) else "<not a name>", node.lineno)
        )
    return found


class TestTheGovernancePackageWritesNoCollectedFact:
    def test_the_package_is_where_this_test_thinks_it_is(self) -> None:
        """A guard whose glob silently matched nothing would pass forever."""
        assert GOVERNANCE_ROOT.is_dir()
        assert len(governance_modules()) >= 5

    def test_the_collected_table_set_is_not_empty(self) -> None:
        """The same failure one level up: if the schema's table names ever stopped being
        readable here, every assertion below would vacuously pass."""
        assert "ntfs_aces" in COLLECTED_TABLES
        assert "object_versions" in COLLECTED_TABLES
        assert "principals" in COLLECTED_TABLES
        assert not (COLLECTED_TABLES & GOVERNANCE_TABLES)

    @pytest.mark.parametrize("module", governance_modules(), ids=lambda path: path.name)
    def test_no_module_writes_a_table_holding_collected_state(self, module: pathlib.Path) -> None:
        offenders = [
            f"{module.name}:{line} {operation}({table})"
            for operation, table, line in writes_in(module.read_text(encoding="utf-8"))
            if table in COLLECTED_TABLES
        ]

        assert not offenders, (
            "A governance module writes a table holding what a collector reported:\n  "
            + "\n  ".join(offenders)
            + "\nA review decision records a judgment about an observation and must never "
            "change the observation. If a decision should lead to a change in Windows, it "
            "produces a RemediationProposal, which ADG records and does not perform."
        )

    @pytest.mark.parametrize("module", governance_modules(), ids=lambda path: path.name)
    def test_every_write_names_its_table_on_the_page(self, module: pathlib.Path) -> None:
        """Conservative on purpose. A write through a variable cannot be checked by reading,
        so it is refused rather than guessed at."""
        opaque = [
            f"{module.name}:{line} {operation}(...)"
            for operation, table, line in writes_in(module.read_text(encoding="utf-8"))
            if table == "<not a name>"
        ]

        assert not opaque, (
            "A governance module writes to a table this guard cannot identify:\n  "
            + "\n  ".join(opaque)
            + "\nWrite the table name literally so the isolation check can read it."
        )

    def test_the_guard_actually_catches_a_violation(self) -> None:
        """A structural test that could never fail is decoration. This feeds it the exact
        mistake it exists to prevent."""
        violation = "await session.execute(update(ntfs_aces).values(access_mask=0))"

        found = writes_in(violation)

        assert found == [("update", "ntfs_aces", 1)]
        assert "ntfs_aces" in COLLECTED_TABLES

    def test_the_guard_does_not_flag_a_legitimate_governance_write(self) -> None:
        legitimate = "await session.execute(insert(review_decisions).values(decision='certify'))"

        found = writes_in(legitimate)

        assert found == [("insert", "review_decisions", 1)]
        assert "review_decisions" not in COLLECTED_TABLES


class TestGovernanceDoesNotReachTheEstateThroughAnotherLayer:
    @pytest.mark.parametrize("module", governance_modules(), ids=lambda path: path.name)
    def test_nothing_imports_the_ingestion_service(self, module: pathlib.Path) -> None:
        """:mod:`app.ingestion.service` is the only code that may write collected state.
        Governance reading it would be a way around the check above without tripping it."""
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        assert not any(name.startswith("app.ingestion") for name in imported), (
            f"{module.name} imports the ingestion layer. Governance records judgments about "
            "observations; writing one is ingestion's job and nothing else's."
        )

    @pytest.mark.parametrize("module", governance_modules(), ids=lambda path: path.name)
    def test_nothing_imports_the_history_writer(self, module: pathlib.Path) -> None:
        """``app.history.writer`` is the only code that may record an absence. A campaign
        must not be able to tombstone the thing it is reviewing."""
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }

        assert "app.history.writer" not in imported, (
            f"{module.name} imports the history writer, which is the only code permitted to "
            "mark a collected object absent."
        )
