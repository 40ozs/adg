r"""``risk_evaluations``, ``risk_findings`` and ``risk_finding_events``: what the rules found.

Phases 0 through 7 collected facts and computed answers over them. This revision adds the
three tables that hold ADG's own **findings** about those facts — the output of
:mod:`app.risk_engine`, which is a set of deterministic rules and not a model, a score or a
heuristic.

## The split, and why it is three tables rather than one

``risk_findings`` is current state: one row per ``finding_key``, which is a SHA-256 of the
rule identifier and the subject it is about. The same shape in the same place digests to the
same key on every evaluation, which is what lets a later pass recognize a finding it has seen
before rather than opening a duplicate of it. The key deliberately excludes the rule's
*version*: correcting a predicate should re-examine the findings it already made, and a
version in the key would resolve every one of them and open an identical set the same second.

``risk_finding_events`` is the timeline. A finding that opened in March, resolved in June and
came back in July is one row here and three events; storing only the newest pair of timestamps
would lose every cycle but the last, which is exactly the pattern worth seeing.

``risk_evaluations`` is the pass, and it exists for one reason that is worth stating plainly
because it is the safety property of the whole feature: **a pass may only resolve findings it
actually covered.** An incremental evaluation loads the facts around what a scan run changed.
If it were allowed to close every finding it did not re-match, it would close every finding in
the estate on the first partial pass. The scope is therefore recorded on the evaluation row —
``scope_complete``, or the three key lists — and a resolution is written only for a finding
inside it. This is the same guard the Phase 7A closure applies to absence, in the same shape,
for the same reason.

## Nothing is deleted, and nothing here is an observation

A resolved finding is kept. "Everyone had Modify on the payroll share between March and June"
is a fact about March, it remains true after the entry is removed, and it is the single most
useful thing an auditor can be told during an access review.

No table here carries a foreign key to a collected table. A finding names a ``resource_key``
and a ``principal_key`` as strings, exactly as the ACL tables name each other — so a finding
about a share a later reconciliation proves is gone stays readable, which is precisely when
somebody wants to read it.

## Additive

Three tables and nine indexes. No existing column, index or constraint changes, and there is
no backfill: findings are produced by running the rules, and manufacturing them from current
state at migration time would date every one of them to the migration.

Revision ID: 0009_risk_findings
Revises: 0008_incremental_collection
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0009_risk_findings"
down_revision: str | None = "0008_incremental_collection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY_LENGTH: Final = 512
FINDING_KEY_LENGTH: Final = 64
DIGEST_LENGTH: Final = 64

#: The vocabularies, frozen at this revision. Written out rather than imported from
#: :mod:`app.models.schema` for the reason every revision in this project does it: a migration
#: must keep creating what it created on the day it ran, even after the application's
#: enumerations move on. A value added later arrives as its own revision that alters the
#: constraint, which is a visible change rather than a silent one.
FINDING_STATUSES: Final[tuple[str, ...]] = ("open", "resolved")
EVENT_TYPES: Final[tuple[str, ...]] = (
    "opened",
    "reaffirmed",
    "evidence_changed",
    "resolved",
    "reopened",
)
EVALUATION_TRIGGERS: Final[tuple[str, ...]] = ("full", "incremental", "targeted")


def _enum_check(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def _digest_check(column: str, *, nullable: bool = False) -> str:
    predicate = f"{column} ~ '^[0-9a-f]{{{DIGEST_LENGTH}}}$'"
    return f"{column} IS NULL OR {predicate}" if nullable else predicate


def upgrade() -> None:
    op.create_table(
        "risk_evaluations",
        sa.Column("evaluation_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("source_run_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("configuration_version", sa.Text(), nullable=False),
        sa.Column("scope_complete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "scope_resource_keys",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "scope_share_keys",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "scope_principal_keys",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rules_run",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rules_skipped",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("findings_matched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("findings_opened", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("findings_reopened", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("findings_resolved", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_enum_check("trigger", EVALUATION_TRIGGERS), name="ck_trigger_valid"),
        sa.CheckConstraint(
            "trigger <> 'incremental' OR source_run_id IS NOT NULL",
            name="ck_risk_evaluations_incremental_names_its_run",
        ),
        sa.CheckConstraint(
            "NOT scope_complete OR ("
            "jsonb_array_length(scope_resource_keys) = 0"
            " AND jsonb_array_length(scope_share_keys) = 0"
            " AND jsonb_array_length(scope_principal_keys) = 0)",
            name="ck_risk_evaluations_complete_scope_has_no_key_lists",
        ),
        sa.CheckConstraint(
            "findings_matched >= 0 AND findings_opened >= 0"
            " AND findings_reopened >= 0 AND findings_resolved >= 0",
            name="ck_risk_evaluations_counts_non_negative",
        ),
        comment=(
            "One pass of the risk rule engine, with the scope it covered -- which is what "
            "limits which findings it was entitled to resolve."
        ),
    )
    op.create_index("ix_risk_evaluations_started", "risk_evaluations", ["started_at"])
    op.create_index("ix_risk_evaluations_run", "risk_evaluations", ["source_run_id"])

    op.create_table(
        "risk_findings",
        sa.Column("finding_key", sa.String(FINDING_KEY_LENGTH), primary_key=True),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("severity_band", sa.Text(), nullable=False),
        sa.Column(
            "qualifiers",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("resource_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("share_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("principal_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("discriminator", sa.Text(), nullable=True),
        sa.Column("evidence", JSONB(none_as_null=True), nullable=False),
        sa.Column("evidence_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column(
            "detail",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("first_evaluation_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("last_evaluation_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("resolved_evaluation_id", PgUUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(_enum_check("status", FINDING_STATUSES), name="ck_status_valid"),
        sa.CheckConstraint(
            _digest_check("evidence_digest"), name="ck_risk_findings_evidence_digest_shape"
        ),
        sa.CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)",
            name="ck_risk_findings_resolution_has_an_instant",
        ),
        sa.CheckConstraint(
            "(resolved_at IS NULL) = (resolved_evaluation_id IS NULL)",
            name="ck_risk_findings_resolution_is_attributed",
        ),
        sa.CheckConstraint(
            "resource_key IS NOT NULL OR share_key IS NOT NULL OR principal_key IS NOT NULL",
            name="ck_risk_findings_names_a_subject",
        ),
        sa.CheckConstraint(
            "first_detected_at <= detected_at", name="ck_risk_findings_windows_ordered"
        ),
        sa.CheckConstraint(
            "occurrence_count >= 1", name="ck_risk_findings_occurrence_count_positive"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'", name="ck_risk_findings_evidence_is_an_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(detail) = 'object'", name="ck_risk_findings_detail_is_an_object"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(qualifiers) = 'array'", name="ck_risk_findings_qualifiers_is_an_array"
        ),
        comment=(
            "Current state of every risk finding, one row per rule and subject. Resolved "
            "findings are kept, never deleted: a finding that was true in March is a fact "
            "about March."
        ),
    )
    op.create_index(
        "ix_risk_findings_status_severity",
        "risk_findings",
        ["status", "severity", "rule_id"],
    )
    op.create_index("ix_risk_findings_resource", "risk_findings", ["resource_key", "status"])
    op.create_index("ix_risk_findings_share", "risk_findings", ["share_key", "status"])
    op.create_index("ix_risk_findings_principal", "risk_findings", ["principal_key", "status"])
    op.create_index("ix_risk_findings_rule", "risk_findings", ["rule_id", "status"])

    op.create_table(
        "risk_finding_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("finding_key", sa.String(FINDING_KEY_LENGTH), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("evaluation_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("evidence_digest", sa.String(DIGEST_LENGTH), nullable=True),
        sa.Column("previous_evidence_digest", sa.String(DIGEST_LENGTH), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_enum_check("event_type", EVENT_TYPES), name="ck_event_type_valid"),
        sa.CheckConstraint(
            _digest_check("evidence_digest", nullable=True),
            name="ck_risk_finding_events_evidence_digest_shape",
        ),
        sa.CheckConstraint(
            _digest_check("previous_evidence_digest", nullable=True),
            name="ck_risk_finding_events_previous_evidence_digest_shape",
        ),
        sa.CheckConstraint(
            "(event_type = 'resolved') = (evidence_digest IS NULL)",
            name="ck_risk_finding_events_only_a_resolution_has_no_evidence",
        ),
        sa.CheckConstraint(
            "event_type = 'evidence_changed' OR previous_evidence_digest IS NULL",
            name="ck_risk_finding_events_only_a_change_names_what_it_replaced",
        ),
        comment=(
            "Every transition a risk finding has been through, with the evaluation that "
            "decided it. Append-only in practice; nothing in the application updates a row "
            "here."
        ),
    )
    op.create_index(
        "ix_risk_finding_events_finding", "risk_finding_events", ["finding_key", "occurred_at"]
    )
    op.create_index("ix_risk_finding_events_evaluation", "risk_finding_events", ["evaluation_id"])


def downgrade() -> None:
    """Drop the three tables. Findings are lost; every collected fact is untouched.

    Findings are derived, so they can be produced again by re-running the rules over current
    state — but their *history* cannot: when each one opened, when it resolved, and how many
    times it came back are facts about the past that a fresh evaluation cannot reconstruct.
    """
    op.drop_index("ix_risk_finding_events_evaluation", table_name="risk_finding_events")
    op.drop_index("ix_risk_finding_events_finding", table_name="risk_finding_events")
    op.drop_table("risk_finding_events")

    op.drop_index("ix_risk_findings_rule", table_name="risk_findings")
    op.drop_index("ix_risk_findings_principal", table_name="risk_findings")
    op.drop_index("ix_risk_findings_share", table_name="risk_findings")
    op.drop_index("ix_risk_findings_resource", table_name="risk_findings")
    op.drop_index("ix_risk_findings_status_severity", table_name="risk_findings")
    op.drop_table("risk_findings")

    op.drop_index("ix_risk_evaluations_run", table_name="risk_evaluations")
    op.drop_index("ix_risk_evaluations_started", table_name="risk_evaluations")
    op.drop_table("risk_evaluations")
