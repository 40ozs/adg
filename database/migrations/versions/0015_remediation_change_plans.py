r"""Proposed remediation: change plans, their steps, their approvals and their signed exports.

Phase 10A recorded what a reviewer concluded. Phase 10B gave the reviewer a screen. This
revision adds the thing that follows a conclusion of ``revoke`` — a precise, simulated,
approvable description of the work — and it adds it as a **description**, never as an act.

## Nothing here can change Windows

Four tables, no trigger that reaches outward, no column that names a credential. ADG has no
write adapter compiled into it, ``remediation:execute`` is granted by no role, and no route
reaches an executor. The schema half of that guarantee is the one visible here: a plan names
its targets by string key, exactly as ``remediation_proposals`` does, so there is no path from
a plan row to a row a collector wrote.

## The four tables

``remediation_change_plans`` — one plan. Its lifecycle, who asked for it, the collection basis
it was written against, the simulation that measured it, and — once approved — *who approved
it, which plan digest they approved, and against which basis*. Three check constraints make
those inseparable from the status: a row cannot reach ``approved`` without all three, cannot
reach ``exported`` without an approval, and cannot name the requestor as its own approver.
That last one is the separation of duties, in the database rather than only in the service, so
that a future code path setting the columns directly still cannot produce a self-approved
plan.

``remediation_planned_changes`` — one precise step. The exact ACE key or membership edge, the
frozen before-state and its digest, the resulting mask or permission for a modification, and
the review decision or risk finding it came from. The before-digest is the **precondition**:
before any export ADG re-reads the object and refuses if what it finds is not byte-identical.

``remediation_approvals`` — every answer, pinned to a plan digest and a basis token. Unique on
``(plan, approver, digest)``: one answer per approver per version of the plan.

``remediation_exports`` — the signed document, stored as the bytes that were signed rather
than regenerated on read. Re-deriving a document later, after a display name changed or a
field was added, would produce bytes the signature does not cover and a verification failure
nobody could explain.

## One existing constraint is widened

``governance_audit_events.ck_event_type_valid`` gains eight ``plan.*`` values. The plan
lifecycle shares the governance audit trail rather than opening a second one because it is the
same story continuing — a campaign produced a decision, the decision produced a proposal, the
proposal became a plan, somebody approved it, somebody exported it — and an auditor who had to
join two append-only tables to read that sequence would be reading two accounts of one thing.
The events sit on their own chain key (``plan:<uuid>``), for the reason a campaign's do: the
plan is the unit that is examined and exported.

Widening a ``CHECK`` admits values that were previously refused and invalidates no stored row.

## Additive, and safe on a populated database

Every table is new and empty; there is nothing to backfill, because no plan has been written
yet. The revision is fast on any estate.

``downgrade()`` drops the four tables and **refuses to run while any export exists**. An
export is a signed document somebody may be holding, and the row is the only record of what
was signed and of the audit head it named; dropping it would make a document that verifies
against nothing. Rejecting is the same choice ``0013_access_review_workflow`` makes for an
attestation, for the same reason.

Revision ID: 0015_remediation_change_plans
Revises: 0014_merge_alerts_and_reviews
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0015_remediation_change_plans"
down_revision: str | None = "0014_merge_alerts_and_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision. A migration must keep doing what it did on the day it ran even
# after the application's enums move on, so these are literals rather than imports -- the rule
# 0007, 0008 and 0013 all state. Widening one means a NEW revision that alters the constraint;
# tests/remediation/test_schema_vocabulary.py fails the moment an enum and the chain of
# migrations disagree.
DIGEST_LENGTH: Final = 64
TOKEN_LENGTH: Final = 32
KEY_LENGTH: Final = 512
SUBJECT_LENGTH: Final = 320
SID_LENGTH: Final = 200
TITLE_LENGTH: Final = 200
FINDING_KEY_LENGTH: Final = 64
MAX_ACCESS_MASK_VALUE: Final = 0xFFFFFFFF

PLAN_STATUSES: Final[tuple[str, ...]] = (
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "exported",
    "invalidated",
    "canceled",
)
CHANGE_KINDS: Final[tuple[str, ...]] = (
    "remove_group_member",
    "remove_share_ace",
    "modify_share_ace",
    "remove_ntfs_ace",
    "modify_ntfs_ace",
    "replace_with_group",
)
TARGET_KINDS: Final[tuple[str, ...]] = ("share", "resource", "group")
APPROVAL_DECISIONS: Final[tuple[str, ...]] = ("approve", "reject")
EDGE_KINDS: Final[tuple[str, ...]] = (
    "directory_group_member",
    "local_group_member",
    "primary_group",
)
SHARE_PERMISSIONS: Final[tuple[str, ...]] = ("read", "change", "full")

#: The audit vocabulary before and after this revision. Both written out, so a reader of this
#: file can see exactly what was admitted without going to another revision for the first half.
EVENT_TYPES_BEFORE: Final[tuple[str, ...]] = (
    "campaign.created",
    "campaign.generated",
    "campaign.activated",
    "campaign.closed",
    "campaign.canceled",
    "reviewer.assigned",
    "reviewer.revoked",
    "decision.recorded",
    "decision.superseded",
    "remediation.proposed",
    "remediation.withdrawn",
    "owner.assigned",
    "owner.revoked",
)
PLAN_EVENT_TYPES: Final[tuple[str, ...]] = (
    "plan.created",
    "plan.simulated",
    "plan.submitted",
    "plan.approved",
    "plan.rejected",
    "plan.exported",
    "plan.invalidated",
    "plan.canceled",
)
EVENT_TYPES_AFTER: Final[tuple[str, ...]] = EVENT_TYPES_BEFORE + PLAN_EVENT_TYPES

EVENT_TYPE_CHECK: Final = "ck_event_type_valid"

#: Newest-dependency-last. Dropped in reverse.
TABLES: Final[tuple[str, ...]] = (
    "remediation_change_plans",
    "remediation_planned_changes",
    "remediation_approvals",
    "remediation_exports",
)


def _in(column: str, values: Sequence[str], *, nullable: bool = False) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    predicate = f"{column} IN ({rendered})"
    return f"{column} IS NULL OR {predicate}" if nullable else predicate


def _digest(column: str, *, nullable: bool = False) -> str:
    predicate = f"{column} ~ '^[0-9a-f]{{{DIGEST_LENGTH}}}$'"
    return f"{column} IS NULL OR {predicate}" if nullable else predicate


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[sa.DateTime[object]]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    _create_plans()
    _create_changes()
    _create_approvals()
    _create_exports()
    _widen_audit_vocabulary()


def _create_plans() -> None:
    op.create_table(
        "remediation_change_plans",
        sa.Column("plan_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(TITLE_LENGTH), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "campaign_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_campaigns.campaign_id"),
            nullable=True,
        ),
        sa.Column("requested_by_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        sa.Column("requested_by_display_name", sa.Text(), nullable=True),
        _timestamp("requested_at"),
        sa.Column("basis_token", sa.String(TOKEN_LENGTH), nullable=False),
        sa.Column("basis_run_id", sa.String(KEY_LENGTH), nullable=True),
        _timestamp("basis_captured_at", nullable=True),
        sa.Column(
            "simulation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.simulation_id"),
            nullable=True,
        ),
        sa.Column("simulation_basis_token", sa.String(TOKEN_LENGTH), nullable=True),
        sa.Column(
            "impact_summary",
            sa.dialects.postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default="{}",
        ),
        _timestamp("submitted_at", nullable=True),
        _timestamp("decided_at", nullable=True),
        sa.Column("approved_by_subject", sa.String(SUBJECT_LENGTH), nullable=True),
        sa.Column("approved_plan_digest", sa.String(DIGEST_LENGTH), nullable=True),
        sa.Column("approved_basis_token", sa.String(TOKEN_LENGTH), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        _timestamp("exported_at", nullable=True),
        _timestamp("invalidated_at", nullable=True),
        sa.Column("invalidation_reason", sa.Text(), nullable=True),
        _timestamp("canceled_at", nullable=True),
        sa.Column("canceled_by_subject", sa.String(SUBJECT_LENGTH), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(_in("status", PLAN_STATUSES), name="ck_status_valid"),
        sa.CheckConstraint(
            _digest("approved_plan_digest", nullable=True),
            name="ck_remediation_change_plans_approved_plan_digest_shape",
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_remediation_plans_title_not_blank"),
        # An approved plan names who approved it and what they approved. A row that reached
        # 'approved' with any of the three missing would be an approval nobody could attribute.
        sa.CheckConstraint(
            "status <> 'approved' OR ("
            "approved_by_subject IS NOT NULL AND approved_plan_digest IS NOT NULL "
            "AND approved_basis_token IS NOT NULL)",
            name="ck_remediation_plans_approval_is_attributed",
        ),
        sa.CheckConstraint(
            "status <> 'exported' OR (exported_at IS NOT NULL "
            "AND approved_by_subject IS NOT NULL AND approved_plan_digest IS NOT NULL)",
            name="ck_remediation_plans_export_follows_approval",
        ),
        # The separation of duties, in the database. See the module docstring.
        sa.CheckConstraint(
            "approved_by_subject IS NULL OR approved_by_subject <> requested_by_subject",
            name="ck_remediation_plans_approver_is_not_the_requestor",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR rejection_reason IS NOT NULL",
            name="ck_remediation_plans_rejection_says_why",
        ),
        sa.CheckConstraint(
            "status <> 'invalidated' OR invalidation_reason IS NOT NULL",
            name="ck_remediation_plans_invalidation_says_why",
        ),
        sa.CheckConstraint(
            "status <> 'canceled' OR canceled_at IS NOT NULL",
            name="ck_remediation_plans_cancellation_has_an_instant",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(impact_summary) = 'object'",
            name="ck_remediation_plans_impact_is_an_object",
        ),
        comment=(
            "A proposed change plan. ADG performs none of it: there is no write adapter, no "
            "role grants remediation:execute, and no route reaches an executor. See ADR-0035."
        ),
    )
    op.create_index(
        "ix_remediation_plans_status", "remediation_change_plans", ["status", "requested_at"]
    )
    op.create_index(
        "ix_remediation_plans_campaign", "remediation_change_plans", ["campaign_id", "requested_at"]
    )
    op.create_index(
        "ix_remediation_plans_requestor",
        "remediation_change_plans",
        ["requested_by_subject", "requested_at"],
    )


def _create_changes() -> None:
    op.create_table(
        "remediation_planned_changes",
        sa.Column("change_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("remediation_change_plans.plan_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("target_display", sa.Text(), nullable=True),
        sa.Column("principal_sid", sa.String(SID_LENGTH), nullable=False),
        sa.Column("principal_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("principal_display_name", sa.Text(), nullable=True),
        sa.Column("ace_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("group_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("member_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("edge_kind", sa.Text(), nullable=True),
        sa.Column("before_state", sa.dialects.postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("before_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("after_access_mask", sa.BigInteger(), nullable=True),
        sa.Column("after_permission", sa.Text(), nullable=True),
        sa.Column("replacement_group_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("replacement_group_sid", sa.String(SID_LENGTH), nullable=True),
        sa.Column("replacement_group_display_name", sa.Text(), nullable=True),
        sa.Column(
            "item_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_items.item_id"),
            nullable=True,
        ),
        sa.Column(
            "decision_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_decisions.decision_id"),
            nullable=True,
        ),
        sa.Column(
            "proposal_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("remediation_proposals.proposal_id"),
            nullable=True,
        ),
        sa.Column("risk_finding_key", sa.String(FINDING_KEY_LENGTH), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint(_in("kind", CHANGE_KINDS), name="ck_kind_valid"),
        sa.CheckConstraint(_in("target_kind", TARGET_KINDS), name="ck_target_kind_valid"),
        sa.CheckConstraint(_in("edge_kind", EDGE_KINDS, nullable=True), name="ck_edge_kind_valid"),
        sa.CheckConstraint(
            _in("after_permission", SHARE_PERMISSIONS, nullable=True),
            name="ck_after_permission_valid",
        ),
        sa.CheckConstraint(
            _digest("before_digest"),
            name="ck_remediation_planned_changes_before_digest_shape",
        ),
        sa.CheckConstraint("sequence_index >= 0", name="ck_remediation_changes_index_non_negative"),
        sa.CheckConstraint(
            f"after_access_mask IS NULL OR after_access_mask BETWEEN 0 AND {MAX_ACCESS_MASK_VALUE}",
            name="ck_remediation_changes_mask_is_32_bit",
        ),
        sa.CheckConstraint(
            "(kind = 'remove_group_member') = (ace_key IS NULL)",
            name="ck_remediation_changes_membership_has_no_entry",
        ),
        sa.CheckConstraint(
            "kind <> 'remove_group_member' OR (group_key IS NOT NULL "
            "AND member_key IS NOT NULL AND edge_kind IS NOT NULL)",
            name="ck_remediation_changes_membership_names_its_edge",
        ),
        sa.CheckConstraint(
            "kind <> 'replace_with_group' OR (group_key IS NOT NULL AND member_key IS NOT NULL "
            "AND replacement_group_key IS NOT NULL AND replacement_group_sid IS NOT NULL)",
            name="ck_remediation_changes_replacement_names_its_group",
        ),
        sa.CheckConstraint(
            "kind NOT IN ('remove_ntfs_ace', 'remove_share_ace', 'remove_group_member', "
            "'replace_with_group') OR (after_access_mask IS NULL AND after_permission IS NULL)",
            name="ck_remediation_changes_removals_have_no_resulting_state",
        ),
        sa.CheckConstraint(
            "kind <> 'modify_ntfs_ace' OR after_access_mask IS NOT NULL",
            name="ck_remediation_changes_ntfs_modification_says_what_remains",
        ),
        sa.CheckConstraint(
            "kind <> 'modify_share_ace' OR (after_access_mask IS NOT NULL) <> "
            "(after_permission IS NOT NULL)",
            name="ck_remediation_changes_share_modification_sets_one_form",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(before_state) = 'object'",
            name="ck_remediation_changes_before_state_is_an_object",
        ),
        sa.UniqueConstraint("plan_id", "sequence_index", name="uq_remediation_changes_position"),
        comment=(
            "One precise change, with the state it was written against and where it came "
            "from. The before-state digest is the precondition re-checked before any export."
        ),
    )
    op.create_index(
        "ix_remediation_changes_plan", "remediation_planned_changes", ["plan_id", "sequence_index"]
    )
    op.create_index(
        "ix_remediation_changes_target",
        "remediation_planned_changes",
        ["target_kind", "target_key"],
    )
    op.create_index("ix_remediation_changes_item", "remediation_planned_changes", ["item_id"])


def _create_approvals() -> None:
    op.create_table(
        "remediation_approvals",
        sa.Column("approval_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("remediation_change_plans.plan_id"),
            nullable=False,
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("approver_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        sa.Column("approver_display_name", sa.Text(), nullable=True),
        sa.Column(
            "approver_roles",
            sa.dialects.postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default="[]",
        ),
        _timestamp("decided_at"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("plan_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("basis_token", sa.String(TOKEN_LENGTH), nullable=False),
        sa.Column(
            "simulation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.simulation_id"),
            nullable=True,
        ),
        _timestamp("created_at"),
        sa.CheckConstraint(_in("decision", APPROVAL_DECISIONS), name="ck_decision_valid"),
        sa.CheckConstraint(
            _digest("plan_digest"), name="ck_remediation_approvals_plan_digest_shape"
        ),
        sa.CheckConstraint(
            "decision <> 'reject' OR rationale IS NOT NULL",
            name="ck_remediation_approvals_rejection_says_why",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "approver_subject",
            "plan_digest",
            name="uq_remediation_approvals_one_answer_per_digest",
        ),
        comment=(
            "One approver's answer, pinned to the plan digest and the collection basis they "
            "answered about. Append-only in practice: nothing updates a row here."
        ),
    )
    op.create_index(
        "ix_remediation_approvals_plan", "remediation_approvals", ["plan_id", "decided_at"]
    )


def _create_exports() -> None:
    op.create_table(
        "remediation_exports",
        sa.Column("export_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("remediation_change_plans.plan_id"),
            nullable=False,
        ),
        sa.Column("exported_by_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        sa.Column("exported_by_display_name", sa.Text(), nullable=True),
        _timestamp("exported_at"),
        sa.Column("document", sa.dialects.postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("document_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("signature_algorithm", sa.Text(), nullable=False),
        sa.Column("signature_key_id", sa.String(32), nullable=False),
        sa.Column("basis_token", sa.String(TOKEN_LENGTH), nullable=False),
        sa.Column("audit_head_digest", sa.String(DIGEST_LENGTH), nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint(
            _digest("document_digest"), name="ck_remediation_exports_document_digest_shape"
        ),
        sa.CheckConstraint(
            _digest("audit_head_digest", nullable=True),
            name="ck_remediation_exports_audit_head_digest_shape",
        ),
        sa.CheckConstraint(
            "length(btrim(signature)) > 0", name="ck_remediation_exports_signature_not_blank"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document) = 'object'",
            name="ck_remediation_exports_document_is_an_object",
        ),
        comment=(
            "A signed change plan as handed to a human administrator. Exporting twice writes "
            "two rows: the document somebody is holding is the one signed at that moment."
        ),
    )
    op.create_index(
        "ix_remediation_exports_plan", "remediation_exports", ["plan_id", "exported_at"]
    )


def _widen_audit_vocabulary() -> None:
    """Admit the eight ``plan.*`` event types into the governance audit trail.

    Widening rather than editing ``0008_governance_model``: a released migration must keep
    doing what it did on the day it ran, and a deployment already past 0008 would never
    re-evaluate an edit to it.
    """
    op.drop_constraint(EVENT_TYPE_CHECK, "governance_audit_events", type_="check")
    op.create_check_constraint(
        EVENT_TYPE_CHECK, "governance_audit_events", _in("event_type", EVENT_TYPES_AFTER)
    )


def downgrade() -> None:
    """Drop the four tables and narrow the audit vocabulary again.

    **Refuses while any export exists.** An export is a signed document somebody may be
    holding, and its row is the only record of what was signed and of the audit head it named.
    Dropping it would leave a document that verifies against nothing, which is worse than a
    downgrade that fails loudly. The same choice ``0013_access_review_workflow`` makes about an
    attestation.

    The audit events themselves are **not** deleted: ``governance_audit_events`` is append-only
    by trigger, and a downgrade that reached around the trigger to delete history would be the
    exact failure the trigger exists to prevent, reached through the migration tool. Narrowing
    the constraint while ``plan.*`` rows exist would therefore fail — which is correct, and the
    message below says so before it happens.
    """
    connection = op.get_bind()
    exports = connection.execute(sa.text("SELECT count(*) FROM remediation_exports")).scalar_one()
    if exports:
        raise RuntimeError(
            f"{exports} signed change-plan export(s) exist. Downgrading would drop the only "
            "record of what was signed and of the audit chain head each document names, "
            "leaving documents that verify against nothing. Archive them first, then "
            "downgrade."
        )
    plan_events = connection.execute(
        sa.text("SELECT count(*) FROM governance_audit_events WHERE event_type LIKE 'plan.%'")
    ).scalar_one()
    if plan_events:
        raise RuntimeError(
            f"{plan_events} plan audit event(s) exist and the trail is append-only by "
            "trigger, so they cannot be removed to make the narrowed constraint valid. "
            "Downgrading past this revision is not possible on a database where change "
            "plans have been used."
        )
    op.drop_constraint(EVENT_TYPE_CHECK, "governance_audit_events", type_="check")
    op.create_check_constraint(
        EVENT_TYPE_CHECK, "governance_audit_events", _in("event_type", EVENT_TYPES_BEFORE)
    )
    for table in reversed(TABLES):
        op.drop_table(table)
