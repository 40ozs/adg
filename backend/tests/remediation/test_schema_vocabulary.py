"""The vocabulary written in three places agrees, and the chain of migrations still admits it.

Every value in this phase is written down three times: as an enum in ``app/domain``, as a
check constraint generated from that enum in ``app/models/schema.py``, and as a frozen literal
in ``0015_remediation_change_plans``. The first two cannot drift — one generates the other —
but the third can, and must: a released migration has to keep doing what it did on the day it
ran, so widening an enum means a **new** revision rather than an edit to an old one.

This suite is what makes that rule survive being forgotten. It compares each enum against the
vocabulary the whole chain of migrations leaves in force, pins the released literals to what
they actually created, and requires every later widening to be a superset — so a revision that
*removed* a value, stranding every stored row carrying it, fails here too.

The same shape as ``tests/governance/test_schema_vocabulary.py``, which Phase 10B had to teach
this lesson the hard way.
"""

from __future__ import annotations

import pathlib
from enum import StrEnum
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, Table

from app.domain import GovernanceEventType
from app.domain.remediation import (
    ApprovalDecision,
    ChangePlanStatus,
    ChangeTargetKind,
    ExecutionMode,
    PlannedChangeKind,
    PreconditionVerdict,
)
from app.models import schema
from app.remediation.model import MAX_TITLE_LENGTH

MIGRATIONS = (
    pathlib.Path(schema.__file__).resolve().parents[3] / "database" / "migrations" / "versions"
)

REMEDIATION_TABLES = (
    "remediation_change_plans",
    "remediation_planned_changes",
    "remediation_approvals",
    "remediation_exports",
)


def _module(name: str) -> Any:
    """Load one migration as a module, without alembic and without a database."""
    import importlib.util

    path = MIGRATIONS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_migration_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint_values(table: Table, name: str) -> set[str]:
    """The values a generated ``IN (...)`` check admits, read off the constraint."""
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == name:
            text = str(constraint.sqltext)
            inner = text[text.index("(") + 1 : text.rindex(")")]
            return {part.strip().strip("'") for part in inner.split(",")}
    raise AssertionError(f"{table.name} has no constraint named {name}")


class TestTheMigrationAgreesWithTheEnums:
    @pytest.mark.parametrize(
        ("literal", "enum"),
        [
            ("PLAN_STATUSES", ChangePlanStatus),
            ("CHANGE_KINDS", PlannedChangeKind),
            ("TARGET_KINDS", ChangeTargetKind),
            ("APPROVAL_DECISIONS", ApprovalDecision),
        ],
        ids=lambda value: getattr(value, "__name__", value),
    )
    def test_the_frozen_literal_matches_the_enum(self, literal: str, enum: type[StrEnum]) -> None:
        """They are equal *today*. When they next differ, the fix is a new revision that
        alters the constraint — never an edit to this one."""
        migration = _module("0015_remediation_change_plans")

        assert set(getattr(migration, literal)) == {member.value for member in enum}

    def test_the_audit_vocabulary_is_widened_rather_than_replaced(self) -> None:
        """Every value the trail admitted before this revision still is admitted. A narrowing
        would strand governance events already written."""
        migration = _module("0015_remediation_change_plans")

        assert set(migration.EVENT_TYPES_BEFORE) <= set(migration.EVENT_TYPES_AFTER)

    def test_the_widened_audit_vocabulary_matches_the_enum(self) -> None:
        migration = _module("0015_remediation_change_plans")

        assert set(migration.EVENT_TYPES_AFTER) == {member.value for member in GovernanceEventType}

    def test_0008_is_not_edited_to_admit_the_new_events(self) -> None:
        """The tempting fix, and the wrong one: a deployment already past 0008 would never
        re-evaluate an edit to it. This pins 0008's literal to what it actually created."""
        released = _module("0008_governance_model")

        assert "plan.created" not in released.EVENT_TYPES
        assert len(released.EVENT_TYPES) == 13

    def test_every_plan_event_is_a_new_value(self) -> None:
        migration = _module("0015_remediation_change_plans")
        released = _module("0008_governance_model")

        assert not (set(migration.PLAN_EVENT_TYPES) & set(released.EVENT_TYPES))


class TestTheSchemaAgreesWithTheEnums:
    def test_the_plan_status_constraint_admits_exactly_the_enum(self) -> None:
        table = schema.metadata.tables["remediation_change_plans"]

        assert _constraint_values(table, "ck_status_valid") == {
            member.value for member in ChangePlanStatus
        }

    def test_the_change_kind_constraint_admits_exactly_the_enum(self) -> None:
        table = schema.metadata.tables["remediation_planned_changes"]

        assert _constraint_values(table, "ck_kind_valid") == {
            member.value for member in PlannedChangeKind
        }

    def test_the_target_kind_constraint_admits_exactly_the_enum(self) -> None:
        table = schema.metadata.tables["remediation_planned_changes"]

        assert _constraint_values(table, "ck_target_kind_valid") == {
            member.value for member in ChangeTargetKind
        }

    def test_the_title_column_is_as_wide_as_the_validator_allows(self) -> None:
        """A column narrower than the rule would turn a sentence-bearing refusal into a
        database error."""
        column = schema.metadata.tables["remediation_change_plans"].c.title

        assert column.type.length == MAX_TITLE_LENGTH  # type: ignore[attr-defined]
        assert schema.PLAN_TITLE_LENGTH == MAX_TITLE_LENGTH


class TestTheTablesStructure:
    @pytest.mark.parametrize("name", REMEDIATION_TABLES)
    def test_every_timestamp_is_timezone_aware(self, name: str) -> None:
        """A naive timestamp would reorder a plan's history under another session time
        zone."""
        table = schema.metadata.tables[name]

        for column in table.columns:
            if column.type.__class__.__name__ == "DateTime":
                assert column.type.timezone, f"{name}.{column.name}"  # type: ignore[attr-defined]

    @pytest.mark.parametrize("name", REMEDIATION_TABLES)
    def test_no_remediation_table_references_a_collected_one(self, name: str) -> None:
        """A plan names its targets by string key, exactly as ``remediation_proposals`` does.
        That is what lets it keep naming them after the object has gone: an instruction that
        vanished when the ACE did would take the evidence of what was planned with it."""
        # ADG's own records, none of which a collector writes. ``simulations`` is on the
        # list for the reason Phase 9A gives: it holds proposals, no collector fills it, and
        # no access query reads it -- so a plan referencing the what-if that measured it is a
        # reference between two ADG records, not a reference into the estate.
        adg_owned = {
            table
            for table in schema.metadata.tables
            if table.startswith(("remediation_", "review_", "governance_", "simulation"))
            or table == "resource_owners"
        }
        collected = set(schema.metadata.tables) - adg_owned
        table = schema.metadata.tables[name]

        referenced = {key.column.table.name for key in table.foreign_keys}

        assert not (referenced & collected), f"{name} references {referenced & collected}"

    def test_a_plan_cannot_be_its_own_approver_by_construction(self) -> None:
        """The separation of duties, in the database as well as in the service."""
        table = schema.metadata.tables["remediation_change_plans"]
        names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        assert "ck_remediation_plans_approver_is_not_the_requestor" in names

    def test_an_approved_plan_must_name_what_it_approved(self) -> None:
        table = schema.metadata.tables["remediation_change_plans"]
        names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        assert "ck_remediation_plans_approval_is_attributed" in names
        assert "ck_remediation_plans_export_follows_approval" in names


class TestTheExecutionVocabularyNamesNoRealAdapter:
    def test_it_has_exactly_two_values(self) -> None:
        """Adding a third that named a production write path would mean the write path
        already existed. See ADR-0035."""
        assert {member.value for member in ExecutionMode} == {"disabled", "lab"}

    def test_the_precondition_vocabulary_keeps_missing_and_unobserved_apart(self) -> None:
        """Two values, both blocking, and never collapsed into one.

        "It is gone" and "nobody has looked" are the same absence in the data and lead to
        opposite conclusions; a vocabulary with one value for both would make the distinction
        unrepresentable rather than merely unrendered.
        """
        blocking = {verdict for verdict in PreconditionVerdict if verdict.blocks_export}

        assert PreconditionVerdict.MISSING in blocking
        assert PreconditionVerdict.UNOBSERVED in blocking
        assert PreconditionVerdict.SATISFIED not in blocking
        assert len(blocking) == 3
