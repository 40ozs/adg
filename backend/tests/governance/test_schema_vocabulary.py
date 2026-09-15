"""The three places the governance vocabulary is written down must agree.

The values live in :mod:`app.domain.governance`. They appear again in two places that cannot
import it, and both are load-bearing:

* ``app/models/schema.py`` generates its check constraints from the enums — except for
  ``CERTAINTY_VALUES``, which it has to spell out because :mod:`app.history.model` imports the
  schema module and importing it back would close a cycle.
* ``0008_governance_model`` spells out **all** of them, deliberately: a migration must keep
  doing what it did on the day it ran even after the application's enums move on. That is the
  same rule ``0007_history_model`` states about its canonicalization.

Both duplications are correct and both can drift silently. This file is the drift guard, and
it is the only reason either duplication is safe. A value added to an enum without being added
to the migration would make the migration's check constraint refuse rows the application
happily builds — and that failure would surface as a constraint violation in production, not
as a test failure.

When one of these fails, the fix depends on which side is right. Adding a value to an enum
means a **new** migration that alters the constraint; it never means editing ``0008``.
"""

from __future__ import annotations

import enum
import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

from app.domain import (
    CampaignFocus,
    CampaignStatus,
    DecisionKind,
    GovernanceEventType,
    OwnershipRole,
    RemediationAction,
    RemediationStatus,
    ReviewItemStatus,
    ReviewScopeKind,
    ReviewTargetKind,
)
from app.history.model import Certainty
from app.models.schema import CERTAINTY_VALUES, metadata

VERSIONS = pathlib.Path(__file__).resolve().parents[3] / "database" / "migrations" / "versions"
REVISION_PATH = VERSIONS / "0008_governance_model.py"
WORKFLOW_REVISION_PATH = VERSIONS / "0013_access_review_workflow.py"
PLANS_REVISION_PATH = VERSIONS / "0015_remediation_change_plans.py"
GOVERNANCE_TABLES = (
    "resource_owners",
    "review_campaigns",
    "review_campaign_scopes",
    "review_assignments",
    "review_items",
    "review_decisions",
    "remediation_proposals",
    "governance_audit_events",
)


def load_revision(path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"adg_revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REVISION = load_revision(REVISION_PATH)
WORKFLOW_REVISION = load_revision(WORKFLOW_REVISION_PATH)
PLANS_REVISION = load_revision(PLANS_REVISION_PATH)

#: The vocabulary each enum has **after the whole migration chain**, as opposed to what any
#: one revision froze. A later revision may widen a constraint — ``0013`` widens the decision
#: kinds to admit ``investigate`` — and when it does, the frozen literal in ``0008`` correctly
#: stops matching the enum. Checking the enum against ``0008`` alone would then force an edit
#: to a released migration, which is the one thing this file's docstring forbids.
EFFECTIVE_VOCABULARY: dict[str, tuple[str, ...]] = {
    "DECISION_KINDS": WORKFLOW_REVISION.DECISION_KINDS_AFTER,
    # 0015 widens the audit vocabulary to admit the eight ``plan.*`` events. The change-plan
    # lifecycle shares this trail rather than opening a second one, because it is the same
    # story continuing -- a campaign produced a decision, the decision produced a plan,
    # somebody approved it -- and an auditor who had to join two append-only tables to read
    # that sequence would be reading two accounts of one thing.
    "EVENT_TYPES": PLANS_REVISION.EVENT_TYPES_AFTER,
}


class TestTheSchemaModuleAgreesWithTheEnums:
    def test_the_written_out_certainties_are_the_enum_s_own(self) -> None:
        """``app/models/schema.py`` cannot import ``Certainty`` — the history model imports
        the schema — so it lists the values. This is what keeps that list honest."""
        assert set(CERTAINTY_VALUES) == {member.value for member in Certainty}

    def test_they_are_in_the_check_constraint_the_database_enforces(self) -> None:
        import sqlalchemy as sa

        constraint = next(
            item
            for item in metadata.tables["review_items"].constraints
            if isinstance(item, sa.CheckConstraint)
            and item.name == "ck_review_items_certainty_valid"
        )

        for value in CERTAINTY_VALUES:
            assert f"'{value}'" in str(constraint.sqltext)


class TestTheMigrationAgreesWithTheEnums:
    @pytest.mark.parametrize(
        ("literal", "enum"),
        [
            ("TARGET_KINDS", ReviewTargetKind),
            ("OWNERSHIP_ROLES", OwnershipRole),
            ("CAMPAIGN_FOCUSES", CampaignFocus),
            ("CAMPAIGN_STATUSES", CampaignStatus),
            ("SCOPE_KINDS", ReviewScopeKind),
            ("ITEM_STATUSES", ReviewItemStatus),
            ("DECISION_KINDS", DecisionKind),
            ("REMEDIATION_ACTIONS", RemediationAction),
            ("REMEDIATION_STATUSES", RemediationStatus),
            ("EVENT_TYPES", GovernanceEventType),
        ],
        ids=lambda value: value if isinstance(value, str) else value.__name__,
    )
    def test_each_frozen_list_matches_its_enum(
        self, literal: str, enum: type[enum.StrEnum]
    ) -> None:
        """A value added to the enum and to no migration would make the database refuse a row
        the application builds happily.

        Checked against the vocabulary the **whole chain** leaves in force, not against 0008
        alone: adding a value means a *new* revision that alters the constraint, and once one
        exists 0008's literal is correctly out of date. ``EFFECTIVE_VOCABULARY`` names the
        revision that last widened each list; everything absent from it is still 0008's.
        """
        effective = EFFECTIVE_VOCABULARY.get(literal, getattr(REVISION, literal))

        assert set(effective) == {member.value for member in enum}

    def test_the_frozen_lists_in_0008_are_not_edited_to_catch_up(self) -> None:
        """The other direction, and the reason the test above is not simply relaxed.

        A released migration must keep doing what it did on the day it ran. The temptation
        when the test above fails is to add the value to 0008 and move on, which changes what
        that revision does to a database that has already run it. This pins 0008's decision
        vocabulary to what it actually created, so that edit fails here.
        """
        assert REVISION.DECISION_KINDS == ("certify", "revoke", "modify", "abstain")

    def test_every_later_widening_is_a_superset_of_what_0008_froze(self) -> None:
        """A widening may only add. A later revision that *removed* a value would strand every
        stored row carrying it, and the constraint would refuse the row on the next write."""
        assert set(WORKFLOW_REVISION.DECISION_KINDS_BEFORE) == set(REVISION.DECISION_KINDS)
        assert set(WORKFLOW_REVISION.DECISION_KINDS_AFTER) >= set(REVISION.DECISION_KINDS)

    def test_the_certainties_match_too(self) -> None:
        assert set(REVISION.CERTAINTIES) == {member.value for member in Certainty}

    def test_the_revision_creates_exactly_the_declared_tables(self) -> None:
        assert set(REVISION.TABLES) == set(GOVERNANCE_TABLES)

    def test_every_table_it_names_is_declared_in_the_schema_module(self) -> None:
        """The reverse direction: a table created by the migration and never declared would
        exist in the database and be invisible to every query, and the reflection parity test
        in ``tests/db/test_schema.py`` only checks declared-then-missing."""
        for name in REVISION.TABLES:
            assert name in metadata.tables

    def test_the_revision_follows_the_released_history_head(self) -> None:
        """Anchored on the last released revision rather than on concurrent work in
        progress. Whichever branch merges second adds the merge revision."""
        assert REVISION.revision == "0008_governance_model"
        assert REVISION.down_revision == "0007_history_model"

    def test_the_key_and_digest_widths_match_the_schema_module(self) -> None:
        from app.models.schema import (
            GOVERNANCE_DIGEST_LENGTH,
            KEY_LENGTH,
            SUBJECT_LENGTH,
        )

        assert REVISION.KEY_LENGTH == KEY_LENGTH
        assert REVISION.SUBJECT_LENGTH == SUBJECT_LENGTH
        assert REVISION.DIGEST_LENGTH == GOVERNANCE_DIGEST_LENGTH


class TestTheGovernanceTablesAreDeclaredAsIntended:
    @pytest.mark.parametrize("name", GOVERNANCE_TABLES)
    def test_no_governance_table_has_a_foreign_key_to_a_collected_one(self, name: str) -> None:
        """A campaign names a target as a string, exactly as the ACL tables name each other.
        A foreign key would make a review of a share that a later reconciliation proves is
        gone either undeletable or cascading — and both are wrong answers for the record of
        who attested to what, which is precisely what somebody wants to read at that moment.
        """
        table = metadata.tables[name]
        offenders = [
            f"{name}.{key.parent.name} -> {key.column.table.name}"
            for key in table.foreign_keys
            if key.column.table.name not in GOVERNANCE_TABLES
        ]

        assert not offenders, "A governance table references a collected one: " + ", ".join(
            offenders
        )

    @pytest.mark.parametrize("name", GOVERNANCE_TABLES)
    def test_every_timestamp_is_timezone_aware(self, name: str) -> None:
        """A naive timestamp from another time zone would reorder an audit trail silently,
        which is the one thing an audit trail must never do."""
        import sqlalchemy as sa

        naive = [
            column.name
            for column in metadata.tables[name].columns
            if isinstance(column.type, sa.DateTime) and not column.type.timezone
        ]

        assert not naive, f"{name} has naive timestamp column(s): {naive}"
