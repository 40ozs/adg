r"""Governance: owners, review campaigns, items, attestations, remediation, audit trail.

Phases 0 through 7 record what Windows says and when ADG learned it. This revision adds the
other half of an audit: what a **person** concluded about it, who that person was, and what
they were looking at when they concluded it.

## Eight tables, and one rule that shapes all of them

Nothing here is an observation. ``resource_owners`` is ADG's record of accountability and is
**not** ``ntfs_resources.owner_sid``; a ``review_decisions`` row is somebody's judgment about
a grant and changes no grant. There is therefore **no foreign key from any table added here
to any collected table**: a campaign names a ``target_key`` and a ``principal_key`` as
strings, exactly as the ACL tables name each other, so a review of a share a later
reconciliation proves is gone stays readable -- which is precisely when an auditor wants to
read it.

## Two triggers, because two properties must not depend on application code

``governance_audit_events`` is append-only: ``adg_governance_audit_events_immutable`` raises
on every ``UPDATE`` and every ``DELETE``. ``review_decisions`` is append-only with one
exception -- marking a row superseded -- and ``adg_review_decisions_append_only`` enforces
exactly that by comparing the row before and after with the two supersession columns removed.
Any other edit, and any delete, raises.

Both are triggers rather than conventions because the property has to survive the next author.
A repository method written in a later phase that "tidies" a decision row is the failure this
guards against, and the guard has to live where that author cannot forget to look.

``TRUNCATE`` is deliberately **not** blocked. A ``BEFORE TRUNCATE`` trigger would make the
tables impossible to clear, which every test fixture and environment reset needs to do -- and
a truncation is not a quiet edit: it removes the audit chain's head along with everything
else, which is the visible kind of loss. The chain digests are what make a quiet edit
detectable; the triggers are what make an accidental one impossible.

## This revision is additive and empty

It creates tables and creates no rows. There is nothing to backfill: governance records
decisions, and no decision has been made yet. ``downgrade()`` drops the tables and the trigger
functions; doing so destroys every attestation, which is why the docstring says it plainly
rather than leaving it to be discovered.

Revision ID: 0008_governance_model
Revises: 0007_history_model
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0008_governance_model"
down_revision: str | None = "0007_history_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY_LENGTH: Final = 512
SUBJECT_LENGTH: Final = 320
DIGEST_LENGTH: Final = 64

# Frozen at this revision. A migration must keep doing what it did on the day it ran even
# after the application's enums move on, so these are literals rather than imports -- the
# same rule 0007 states for its canonicalization.
TARGET_KINDS: Final[tuple[str, ...]] = ("share", "resource")
OWNERSHIP_ROLES: Final[tuple[str, ...]] = ("owner", "delegate")
CAMPAIGN_FOCUSES: Final[tuple[str, ...]] = ("resource", "principal")
CAMPAIGN_STATUSES: Final[tuple[str, ...]] = ("draft", "active", "closed", "canceled")
SCOPE_KINDS: Final[tuple[str, ...]] = ("server", "share", "directory_tree", "principal")
ITEM_STATUSES: Final[tuple[str, ...]] = ("pending", "decided")
DECISION_KINDS: Final[tuple[str, ...]] = ("certify", "revoke", "modify", "abstain")
CERTAINTIES: Final[tuple[str, ...]] = ("observed", "inferred", "backfilled", "unobserved")
REMEDIATION_ACTIONS: Final[tuple[str, ...]] = (
    "remove_ace",
    "reduce_rights",
    "remove_group_member",
    "replace_with_group",
    "manual_review",
)
REMEDIATION_STATUSES: Final[tuple[str, ...]] = ("proposed", "exported", "withdrawn")
EVENT_TYPES: Final[tuple[str, ...]] = (
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

#: Every table this revision creates, newest-dependency-last. Dropped in reverse.
TABLES: Final[tuple[str, ...]] = (
    "resource_owners",
    "review_campaigns",
    "review_campaign_scopes",
    "review_assignments",
    "review_items",
    "review_decisions",
    "remediation_proposals",
    "governance_audit_events",
)

_AUDIT_TRIGGER_FUNCTION: Final = """
CREATE OR REPLACE FUNCTION adg_governance_audit_events_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'governance_audit_events is append-only: % on event % was refused. The audit trail '
        'is the record of who decided what, and a row that can be edited is not a record. '
        'Append a correcting event instead.',
        TG_OP, COALESCE(OLD.event_id::text, '(unknown)')
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

_DECISION_TRIGGER_FUNCTION: Final = """
CREATE OR REPLACE FUNCTION adg_review_decisions_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    before jsonb;
    after jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'review_decisions is append-only: deleting decision % was refused. An '
            'attestation that can be deleted is not an attestation. Record a new decision, '
            'which supersedes this one and leaves both readable.',
            OLD.decision_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- The one permitted update: marking a still-current decision superseded. Everything
    -- else about the row must be byte-identical, which is checked by removing the two
    -- supersession columns and comparing what is left. Listing the mutable columns rather
    -- than the immutable ones means a column added later is immutable by default.
    before := to_jsonb(OLD) - 'superseded_at' - 'superseded_by_decision_id';
    after := to_jsonb(NEW) - 'superseded_at' - 'superseded_by_decision_id';

    IF before IS DISTINCT FROM after THEN
        RAISE EXCEPTION
            'review_decisions is append-only: decision % may not be edited. Only '
            'superseded_at and superseded_by_decision_id may change, and only to mark the '
            'decision superseded by a newer one.',
            OLD.decision_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.superseded_at IS NOT NULL THEN
        RAISE EXCEPTION
            'Decision % was already superseded at %. A decision is superseded once; '
            're-pointing it would rewrite the order in which a reviewer changed their mind.',
            OLD.decision_id, OLD.superseded_at
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.superseded_at IS NULL THEN
        RAISE EXCEPTION
            'Decision % cannot be un-superseded. The supersession is part of the record.',
            OLD.decision_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _digest(column: str, *, nullable: bool = False) -> str:
    predicate = f"{column} ~ '^[0-9a-f]{{{DIGEST_LENGTH}}}$'"
    return f"{column} IS NULL OR {predicate}" if nullable else predicate


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    _create_resource_owners()
    _create_campaigns()
    _create_assignments()
    _create_items()
    _create_decisions()
    _create_remediation()
    _create_audit_events()
    _install_triggers()


def downgrade() -> None:
    """Drop every governance table. **This destroys every attestation ever recorded.**"""
    op.execute("DROP TRIGGER IF EXISTS adg_review_decisions_append_only ON review_decisions")
    op.execute(
        "DROP TRIGGER IF EXISTS adg_governance_audit_events_immutable ON governance_audit_events"
    )
    op.execute("DROP FUNCTION IF EXISTS adg_review_decisions_append_only()")
    op.execute("DROP FUNCTION IF EXISTS adg_governance_audit_events_immutable()")
    for table in reversed(TABLES):
        op.drop_table(table)


# ------------------------------------------------------------------------------- tables


def _create_resource_owners() -> None:
    op.create_table(
        "resource_owners",
        sa.Column("owner_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("ownership_role", sa.Text(), nullable=False),
        sa.Column("owner_subject", sa.String(SUBJECT_LENGTH), nullable=True),
        sa.Column("owner_principal_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("owner_display_name", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("assigned_by_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        _timestamp("assigned_at"),
        _timestamp("revoked_at", nullable=True),
        sa.Column("revoked_by_subject", sa.String(SUBJECT_LENGTH), nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint(_in("target_kind", TARGET_KINDS), name="ck_target_kind_valid"),
        sa.CheckConstraint(_in("ownership_role", OWNERSHIP_ROLES), name="ck_ownership_role_valid"),
        sa.CheckConstraint(
            "(owner_subject IS NULL) <> (owner_principal_key IS NULL)",
            name="ck_resource_owners_exactly_one_owner_form",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_subject IS NULL)",
            name="ck_resource_owners_revocation_is_attributed",
        ),
        comment=(
            "ADG's record of who is accountable for a resource. Not the Windows security "
            "descriptor's owner, which is collected in ntfs_resources.owner_sid."
        ),
    )
    op.create_index(
        "ux_resource_owners_active",
        "resource_owners",
        [
            "target_kind",
            "target_key",
            "ownership_role",
            sa.text("coalesce(owner_subject, owner_principal_key)"),
        ],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_resource_owners_target", "resource_owners", ["target_kind", "target_key"])
    op.create_index("ix_resource_owners_subject", "resource_owners", ["owner_subject"])
    op.create_index("ix_resource_owners_principal", "resource_owners", ["owner_principal_key"])


def _create_campaigns() -> None:
    op.create_table(
        "review_campaigns",
        sa.Column("campaign_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("focus", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        _timestamp("baseline_at"),
        _timestamp("due_at", nullable=True),
        sa.Column("include_inherited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("include_builtin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("include_deny", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("snapshot_digest", sa.String(DIGEST_LENGTH), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "excluded_counts",
            JSONB(none_as_null=True),
            nullable=False,
            server_default="{}",
        ),
        _timestamp("generated_at", nullable=True),
        _timestamp("activated_at", nullable=True),
        _timestamp("closed_at", nullable=True),
        sa.Column("closed_by_subject", sa.String(SUBJECT_LENGTH), nullable=True),
        sa.Column("created_by_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(_in("focus", CAMPAIGN_FOCUSES), name="ck_focus_valid"),
        sa.CheckConstraint(_in("status", CAMPAIGN_STATUSES), name="ck_status_valid"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_review_campaigns_name_not_blank"),
        sa.CheckConstraint("item_count >= 0", name="ck_review_campaigns_item_count_non_negative"),
        sa.CheckConstraint(
            _digest("snapshot_digest", nullable=True),
            name="ck_review_campaigns_snapshot_digest_shape",
        ),
        sa.CheckConstraint(
            "(generated_at IS NULL) = (snapshot_digest IS NULL)",
            name="ck_review_campaigns_generated_has_a_digest",
        ),
        sa.CheckConstraint(
            "status NOT IN ('active', 'closed') OR generated_at IS NOT NULL",
            name="ck_review_campaigns_open_only_when_generated",
        ),
        sa.CheckConstraint(
            "activated_at IS NULL OR generated_at IS NOT NULL",
            name="ck_review_campaigns_activation_follows_generation",
        ),
        sa.CheckConstraint(
            "due_at IS NULL OR due_at > baseline_at",
            name="ck_review_campaigns_due_after_baseline",
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL) = (closed_by_subject IS NULL)",
            name="ck_review_campaigns_closure_is_attributed",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(excluded_counts) = 'object'",
            name="ck_review_campaigns_excluded_counts_is_an_object",
        ),
        comment="One access review, frozen against the instant in baseline_at.",
    )
    op.create_index("ix_review_campaigns_status", "review_campaigns", ["status", "created_at"])
    op.create_index("ix_review_campaigns_created_at", "review_campaigns", ["created_at"])

    op.create_table(
        "review_campaign_scopes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_campaigns.campaign_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.String(KEY_LENGTH), nullable=False),
        _timestamp("created_at"),
        sa.CheckConstraint(_in("scope_kind", SCOPE_KINDS), name="ck_scope_kind_valid"),
        sa.CheckConstraint(
            "length(btrim(scope_key)) > 0", name="ck_review_campaign_scopes_key_not_blank"
        ),
        sa.UniqueConstraint(
            "campaign_id", "scope_kind", "scope_key", name="uq_review_campaign_scopes_identity"
        ),
        comment="What a campaign selected, and therefore what it claims to be complete about.",
    )


def _create_assignments() -> None:
    op.create_table(
        "review_assignments",
        sa.Column("assignment_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_campaigns.campaign_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        sa.Column("reviewer_display_name", sa.Text(), nullable=True),
        sa.Column("reviewer_email", sa.Text(), nullable=True),
        sa.Column("scope_kind", sa.Text(), nullable=True),
        sa.Column("scope_key", sa.String(KEY_LENGTH), nullable=True),
        _timestamp("due_at", nullable=True),
        sa.Column("assigned_by_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        _timestamp("assigned_at"),
        _timestamp("revoked_at", nullable=True),
        sa.Column("revoked_by_subject", sa.String(SUBJECT_LENGTH), nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint(
            f"scope_kind IS NULL OR {_in('scope_kind', SCOPE_KINDS)}", name="ck_scope_kind_valid"
        ),
        sa.CheckConstraint(
            "(scope_kind IS NULL) = (scope_key IS NULL)",
            name="ck_review_assignments_scope_is_whole_or_both",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_subject IS NULL)",
            name="ck_review_assignments_revocation_is_attributed",
        ),
        comment="A reviewer, and the slice of a campaign they were asked to answer.",
    )
    op.create_index(
        "ux_review_assignments_active",
        "review_assignments",
        [
            "campaign_id",
            "reviewer_subject",
            sa.text("coalesce(scope_kind, '')"),
            sa.text("coalesce(scope_key, '')"),
        ],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_review_assignments_reviewer", "review_assignments", ["reviewer_subject", "campaign_id"]
    )
    op.create_index("ix_review_assignments_campaign", "review_assignments", ["campaign_id"])


def _create_items() -> None:
    op.create_table(
        "review_items",
        sa.Column("item_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_campaigns.campaign_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("focus", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=True),
        sa.Column("principal_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("principal_sid", sa.String(200), nullable=False),
        sa.Column("principal_display_name", sa.Text(), nullable=True),
        sa.Column("grants", JSONB(none_as_null=True), nullable=False),
        sa.Column("evidence_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("certainty", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "assignment_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_assignments.assignment_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("current_decision_id", PgUUID(as_uuid=True), nullable=True),
        _timestamp("decided_at", nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(_in("focus", CAMPAIGN_FOCUSES), name="ck_focus_valid"),
        sa.CheckConstraint(_in("target_kind", TARGET_KINDS), name="ck_target_kind_valid"),
        sa.CheckConstraint(_in("status", ITEM_STATUSES), name="ck_status_valid"),
        sa.CheckConstraint(_in("certainty", CERTAINTIES), name="ck_review_items_certainty_valid"),
        sa.CheckConstraint(
            _digest("evidence_digest"), name="ck_review_items_evidence_digest_shape"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(grants) = 'array'", name="ck_review_items_grants_is_an_array"
        ),
        sa.CheckConstraint(
            "jsonb_array_length(grants) > 0", name="ck_review_items_grants_not_empty"
        ),
        sa.CheckConstraint(
            "(status = 'decided') = (current_decision_id IS NOT NULL)",
            name="ck_review_items_decided_names_its_decision",
        ),
        sa.CheckConstraint(
            "(decided_at IS NULL) = (current_decision_id IS NULL)",
            name="ck_review_items_decided_at_matches_decision",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "target_kind",
            "target_key",
            "principal_key",
            name="uq_review_items_natural_key",
        ),
        comment=("One grant to be decided, with the evidence it was frozen from at the baseline."),
    )
    op.create_index(
        "ix_review_items_campaign_status", "review_items", ["campaign_id", "status", "item_id"]
    )
    op.create_index("ix_review_items_assignment", "review_items", ["assignment_id", "status"])
    op.create_index("ix_review_items_principal", "review_items", ["principal_key"])
    op.create_index("ix_review_items_target", "review_items", ["target_kind", "target_key"])


def _create_decisions() -> None:
    op.create_table(
        "review_decisions",
        sa.Column("decision_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_items.item_id"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_campaigns.campaign_id"),
            nullable=False,
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("decided_by_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        sa.Column("decided_by_display_name", sa.Text(), nullable=True),
        _timestamp("decided_at"),
        sa.Column("decided_late", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "supersedes_decision_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_decisions.decision_id"),
            nullable=True,
        ),
        _timestamp("superseded_at", nullable=True),
        # Deferred on purpose; see the note in app/models/schema.py. The supersession is
        # written before the successor row exists, because the partial unique index permits
        # only one current decision per item and the other write order breaks it.
        sa.Column(
            "superseded_by_decision_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_decisions.decision_id", deferrable=True, initially="DEFERRED"),
            nullable=True,
        ),
        _timestamp("created_at"),
        sa.CheckConstraint(_in("decision", DECISION_KINDS), name="ck_decision_valid"),
        sa.CheckConstraint(
            "decision = 'certify' OR (rationale IS NOT NULL AND length(btrim(rationale)) > 0)",
            name="ck_review_decisions_reason_required",
        ),
        sa.CheckConstraint(
            "rationale IS NULL OR length(rationale) <= 4000",
            name="ck_review_decisions_rationale_length",
        ),
        sa.CheckConstraint(
            "(superseded_at IS NULL) = (superseded_by_decision_id IS NULL)",
            name="ck_review_decisions_supersession_names_its_successor",
        ),
        sa.CheckConstraint(
            "superseded_by_decision_id IS NULL OR superseded_by_decision_id <> decision_id",
            name="ck_review_decisions_not_superseded_by_itself",
        ),
        comment=(
            "Attestations. Append-only: a changed mind writes a new row and supersedes the "
            "old one. A trigger refuses every other update and every delete."
        ),
    )
    op.create_index(
        "ux_review_decisions_current",
        "review_decisions",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index("ix_review_decisions_item", "review_decisions", ["item_id", "decided_at"])
    op.create_index(
        "ix_review_decisions_campaign", "review_decisions", ["campaign_id", "decided_at"]
    )
    op.create_index(
        "ix_review_decisions_subject", "review_decisions", ["decided_by_subject", "decided_at"]
    )


def _create_remediation() -> None:
    op.create_table(
        "remediation_proposals",
        sa.Column("proposal_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_items.item_id"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_campaigns.campaign_id"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("review_decisions.decision_id"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("principal_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("ace_keys", JSONB(none_as_null=True), nullable=False),
        sa.Column("details", JSONB(none_as_null=True), nullable=False, server_default="{}"),
        sa.Column("proposed_by_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        _timestamp("proposed_at"),
        _timestamp("withdrawn_at", nullable=True),
        sa.Column("withdrawn_by_subject", sa.String(SUBJECT_LENGTH), nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint(_in("action", REMEDIATION_ACTIONS), name="ck_action_valid"),
        sa.CheckConstraint(_in("status", REMEDIATION_STATUSES), name="ck_status_valid"),
        sa.CheckConstraint(_in("target_kind", TARGET_KINDS), name="ck_target_kind_valid"),
        sa.CheckConstraint(
            "jsonb_typeof(ace_keys) = 'array'",
            name="ck_remediation_proposals_ace_keys_is_an_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name="ck_remediation_proposals_details_is_an_object",
        ),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_by_subject IS NULL)",
            name="ck_remediation_proposals_withdrawal_is_attributed",
        ),
        sa.CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL)",
            name="ck_remediation_proposals_status_matches_withdrawal",
        ),
        comment=(
            "Changes somebody might make in Windows as a result of a decision. ADG records "
            "them and performs none of them; see SECURITY.md."
        ),
    )
    op.create_index(
        "ix_remediation_proposals_campaign", "remediation_proposals", ["campaign_id", "status"]
    )
    op.create_index("ix_remediation_proposals_item", "remediation_proposals", ["item_id"])


def _create_audit_events() -> None:
    op.create_table(
        "governance_audit_events",
        sa.Column("event_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("chain_key", sa.Text(), nullable=False),
        sa.Column("chain_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        _timestamp("occurred_at"),
        sa.Column("actor_subject", sa.String(SUBJECT_LENGTH), nullable=False),
        sa.Column("actor_display_name", sa.Text(), nullable=True),
        sa.Column("actor_roles", JSONB(none_as_null=True), nullable=False, server_default="[]"),
        sa.Column("campaign_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("item_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("decision_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB(none_as_null=True), nullable=False, server_default="{}"),
        sa.Column("previous_digest", sa.String(DIGEST_LENGTH), nullable=True),
        sa.Column("event_digest", sa.String(DIGEST_LENGTH), nullable=False),
        _timestamp("created_at"),
        sa.CheckConstraint(_in("event_type", EVENT_TYPES), name="ck_event_type_valid"),
        sa.CheckConstraint(
            _digest("previous_digest", nullable=True),
            name="ck_governance_audit_events_previous_digest_shape",
        ),
        sa.CheckConstraint(
            _digest("event_digest"), name="ck_governance_audit_events_event_digest_shape"
        ),
        sa.CheckConstraint(
            "chain_index >= 0", name="ck_governance_audit_events_index_non_negative"
        ),
        sa.CheckConstraint(
            "(chain_index = 0) = (previous_digest IS NULL)",
            name="ck_governance_audit_events_only_the_first_has_no_link",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_governance_audit_events_payload_is_an_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(actor_roles) = 'array'",
            name="ck_governance_audit_events_actor_roles_is_an_array",
        ),
        sa.UniqueConstraint("chain_key", "chain_index", name="uq_governance_audit_events_position"),
        comment=(
            "Immutable governance audit trail. Append-only by trigger, hash-chained per "
            "campaign so a quiet edit cannot be made without invalidating every later event."
        ),
    )
    op.create_index(
        "ix_governance_audit_events_campaign",
        "governance_audit_events",
        ["campaign_id", "occurred_at"],
    )
    op.create_index(
        "ix_governance_audit_events_item", "governance_audit_events", ["item_id", "occurred_at"]
    )
    op.create_index(
        "ix_governance_audit_events_actor",
        "governance_audit_events",
        ["actor_subject", "occurred_at"],
    )


def _install_triggers() -> None:
    """The two properties that must not depend on application code. See the docstring."""
    op.execute(_AUDIT_TRIGGER_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER adg_governance_audit_events_immutable
        BEFORE UPDATE OR DELETE ON governance_audit_events
        FOR EACH ROW EXECUTE FUNCTION adg_governance_audit_events_immutable()
        """
    )
    op.execute(_DECISION_TRIGGER_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER adg_review_decisions_append_only
        BEFORE UPDATE OR DELETE ON review_decisions
        FOR EACH ROW EXECUTE FUNCTION adg_review_decisions_append_only()
        """
    )
