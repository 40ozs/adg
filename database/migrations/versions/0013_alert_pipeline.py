r"""``alert_watches``, ``alerts``, ``alert_events`` and ``alert_deliveries``: the alert pipeline.

Phase 8A added findings. This revision adds the machinery that tells somebody about one —
and about a watched group gaining a member, a watched access control list being edited, and
effective access to a watched place growing.

## Four tables, and the two boundaries between them

``alert_watches`` is configuration: a standing subscription to one directory, share or group.
It is the only table here an operator writes directly.

``alerts`` is current state, one row per ``alert_key`` — a SHA-256 of the trigger, the subject
and a discriminator, and deliberately **not** of the content. That is what makes deduplication
possible at all: the same condition in the same place has to be the same row next week, so
that a second occurrence can be recognized rather than announced again. Putting the payload in
the key would make every occurrence a new alert and turn the cooldown into decoration.

``alert_events`` is the timeline, and it is where the honesty of this feature lives. **A
suppressed occurrence is written here.** Deduplication and cooldown decide what is
*delivered*; nothing decides what is *recorded*. Without that row, a feed that went quiet
because of a cooldown and a feed that went quiet because the estate did are the same reading —
which is this product's own failure mode applied to its own monitoring.

``alert_deliveries`` is an outbox, and the boundary it draws is the point of the whole design.
A delivery is enqueued in the same transaction as the alert and attempted in a **separate**
pass. So a webhook that is down, slow, or returning 500 cannot take an ingestion transaction
with it, and the estate never loses an observation because somebody's chat integration
expired. Its ``idempotency_key`` is unique, which is what makes enqueueing idempotent: a
re-raised alert records one delivery rather than two, and the same key travels on every retry
so a receiver that already applied a lost acknowledgment can recognize the second attempt.

## What this deliberately does not do

**No foreign keys to the collected tables, and none to ``alert_watches``.** An alert outlives
the watch that raised it; deleting a watch must not delete the record of what it told somebody.
The same rule the findings and governance tables follow, for the same reason.

**No cascade from ``alerts`` to ``alert_events``.** Nothing deletes an alert.

**No backfill.** There are no alerts before this revision because nothing was watching.
Manufacturing them from current state would date every one to the migration and claim
somebody had been told.

## Additive

Four tables and thirteen indexes. No existing column, index or constraint changes.

Revision ID: 0013_alert_pipeline
Revises: 0012_merge_concurrent_phases
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0013_alert_pipeline"
down_revision: str | None = "0012_merge_concurrent_phases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY_LENGTH: Final = 512
ALERT_KEY_LENGTH: Final = 64
DIGEST_LENGTH: Final = 64
IDEMPOTENCY_KEY_LENGTH: Final = 64
SUBJECT_LENGTH: Final = 320

#: The vocabularies, frozen at this revision. Written out rather than imported from
#: :mod:`app.models.schema` for the reason every revision in this project does it: a migration
#: must keep creating what it created on the day it ran, even after the application's
#: enumerations move on. A value added later arrives as its own revision that alters the
#: constraint, which is a visible change rather than a silent one.
WATCH_KINDS: Final[tuple[str, ...]] = ("resource", "share", "group")
TRIGGERS: Final[tuple[str, ...]] = (
    "watched_group_membership_changed",
    "watched_resource_acl_changed",
    "watched_access_expanded",
    "critical_risk_finding_opened",
)
LIFECYCLES: Final[tuple[str, ...]] = ("stateful", "transient")
ALERT_STATUSES: Final[tuple[str, ...]] = ("open", "resolved")
TRANSITIONS: Final[tuple[str, ...]] = (
    "raised",
    "reopened",
    "repeated",
    "suppressed",
    "resolved",
)
DELIVERY_STATUSES: Final[tuple[str, ...]] = ("pending", "delivered", "failed", "abandoned")

#: The cooldown bounds :mod:`app.alerts.model` fixes, restated where the data is. Below the
#: floor a script editing an access control list in a loop produces an alert per edit; above
#: the ceiling one day's change folds into the previous day's alert, where nobody reading
#: today's feed sees it.
MIN_COOLDOWN_SECONDS: Final = 60
MAX_COOLDOWN_SECONDS: Final = 86_400


def _enum_check(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def _digest_check(column: str, *, nullable: bool = False) -> str:
    predicate = f"{column} ~ '^[0-9a-f]{{{DIGEST_LENGTH}}}$'"
    return f"{column} IS NULL OR {predicate}" if nullable else predicate


def upgrade() -> None:
    op.create_table(
        "alert_watches",
        sa.Column("watch_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("watch_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("triggers", JSONB(none_as_null=True), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(SUBJECT_LENGTH), nullable=False),
        sa.Column("updated_by", sa.String(SUBJECT_LENGTH), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_enum_check("kind", WATCH_KINDS), name="ck_kind_valid"),
        sa.UniqueConstraint("kind", "watch_key", name="uq_alert_watches_target"),
        sa.CheckConstraint(
            "jsonb_typeof(triggers) = 'array' AND jsonb_array_length(triggers) > 0",
            name="ck_alert_watches_subscribes_to_something",
        ),
        sa.CheckConstraint(
            f"cooldown_seconds >= {MIN_COOLDOWN_SECONDS}"
            f" AND cooldown_seconds <= {MAX_COOLDOWN_SECONDS}",
            name="ck_alert_watches_cooldown_bounded",
        ),
        sa.CheckConstraint("length(btrim(label)) > 0", name="ck_alert_watches_labelled"),
        comment=(
            "Standing subscriptions: tell me when this directory, share or group changes. "
            "One row per watched thing."
        ),
    )
    op.create_index("ix_alert_watches_kind", "alert_watches", ["kind", "enabled"])

    op.create_table(
        "alerts",
        sa.Column("alert_key", sa.String(ALERT_KEY_LENGTH), primary_key=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("lifecycle", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("watch_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("watch_label", sa.Text(), nullable=True),
        sa.Column("resource_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("share_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("principal_key", sa.String(KEY_LENGTH), nullable=True),
        sa.Column("discriminator", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("payload_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("delivered_digest", sa.String(DIGEST_LENGTH), nullable=True),
        sa.Column("first_raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "suppressed_since_notice", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("suppressed_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(_enum_check("trigger", TRIGGERS), name="ck_trigger_valid"),
        sa.CheckConstraint(_enum_check("lifecycle", LIFECYCLES), name="ck_lifecycle_valid"),
        sa.CheckConstraint(_enum_check("status", ALERT_STATUSES), name="ck_status_valid"),
        sa.CheckConstraint(_digest_check("payload_digest"), name="ck_alerts_payload_digest_shape"),
        sa.CheckConstraint(
            _digest_check("delivered_digest", nullable=True),
            name="ck_alerts_delivered_digest_shape",
        ),
        sa.CheckConstraint(
            "resource_key IS NOT NULL OR share_key IS NOT NULL OR principal_key IS NOT NULL",
            name="ck_alerts_names_a_subject",
        ),
        sa.CheckConstraint(
            "lifecycle <> 'transient' OR (status = 'open' AND resolved_at IS NULL)",
            name="ck_alerts_transient_never_resolves",
        ),
        sa.CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)",
            name="ck_alerts_resolution_has_an_instant",
        ),
        sa.CheckConstraint("first_raised_at <= last_raised_at", name="ck_alerts_windows_ordered"),
        sa.CheckConstraint(
            "occurrence_count >= 1 AND suppressed_since_notice >= 0 AND suppressed_total >= 0",
            name="ck_alerts_counts_non_negative",
        ),
        comment=(
            "Current state of every alert, one row per trigger and subject. A suppressed "
            "occurrence updates the counts here and writes an event; it never deletes "
            "anything."
        ),
    )
    op.create_index("ix_alerts_status_raised", "alerts", ["status", "last_raised_at"])
    op.create_index("ix_alerts_trigger", "alerts", ["trigger", "last_raised_at"])
    op.create_index("ix_alerts_watch", "alerts", ["watch_id", "last_raised_at"])
    op.create_index("ix_alerts_resource", "alerts", ["resource_key", "status"])
    op.create_index("ix_alerts_share", "alerts", ["share_key", "status"])
    op.create_index("ix_alerts_principal", "alerts", ["principal_key", "status"])

    op.create_table(
        "alert_events",
        sa.Column("event_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("alert_key", sa.String(ALERT_KEY_LENGTH), nullable=False),
        sa.Column("transition", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("suppression_reason", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("payload_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("folds", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_run_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("source_evaluation_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_enum_check("transition", TRANSITIONS), name="ck_transition_valid"),
        sa.CheckConstraint(_enum_check("trigger", TRIGGERS), name="ck_trigger_valid"),
        sa.CheckConstraint(
            _digest_check("payload_digest"), name="ck_alert_events_payload_digest_shape"
        ),
        sa.CheckConstraint(
            "(transition = 'suppressed') = (suppression_reason IS NOT NULL)",
            name="ck_alert_events_suppression_names_its_reason",
        ),
        sa.CheckConstraint(
            "(transition = 'suppressed') <> notified",
            name="ck_alert_events_suppression_did_not_notify",
        ),
        sa.CheckConstraint("folds >= 1", name="ck_alert_events_folds_positive"),
        comment=(
            "Every occurrence of every alert, delivered or suppressed. Append-only; nothing "
            "in the application updates a row here. A suppressed occurrence is written "
            "precisely so that 'we were not told' and 'it did not happen' stay separable."
        ),
    )
    op.create_index("ix_alert_events_alert", "alert_events", ["alert_key", "occurred_at"])
    op.create_index("ix_alert_events_occurred", "alert_events", ["occurred_at"])
    op.create_index("ix_alert_events_run", "alert_events", ["source_run_id"])

    op.create_table(
        "alert_deliveries",
        sa.Column("delivery_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("alert_key", sa.String(ALERT_KEY_LENGTH), nullable=False),
        sa.Column("sink_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(IDEMPOTENCY_KEY_LENGTH), nullable=False),
        sa.Column("envelope", JSONB(none_as_null=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_enum_check("status", DELIVERY_STATUSES), name="ck_status_valid"),
        sa.UniqueConstraint("idempotency_key", name="uq_alert_deliveries_idempotency"),
        sa.CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)",
            name="ck_alert_deliveries_delivery_has_an_instant",
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR attempts = 0",
            name="ck_alert_deliveries_pending_has_not_been_tried",
        ),
        sa.CheckConstraint(
            "status NOT IN ('failed', 'abandoned') OR last_error IS NOT NULL",
            name="ck_alert_deliveries_failure_says_why",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_alert_deliveries_attempts_non_negative"),
        comment=(
            "The outbox. One row per alert event per sink. Enqueued in the alert's own "
            "transaction and delivered in a separate pass, so a failing endpoint cannot roll "
            "back the ingestion that produced the alert."
        ),
    )
    # Partial: delivered rows accumulate forever and the claim query never reads one, so a
    # full index over them would get slower every quarter for nothing.
    op.create_index(
        "ix_alert_deliveries_due",
        "alert_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'failed')"),
    )
    op.create_index("ix_alert_deliveries_event", "alert_deliveries", ["event_id"])
    op.create_index("ix_alert_deliveries_status", "alert_deliveries", ["status", "sink_name"])


def downgrade() -> None:
    """Drop the four tables. Every collected fact and every finding is untouched.

    What is lost is not recoverable by re-running anything: which alerts were delivered, to
    whom, and when. An alert is a record that somebody was told something, and no later pass
    over the estate can reconstruct that — re-running detection over today's facts would
    produce today's alerts, dated today, with no evidence of what was announced in March.

    Watches are lost too, which is configuration an operator would have to enter again.
    """
    op.drop_index("ix_alert_deliveries_status", table_name="alert_deliveries")
    op.drop_index("ix_alert_deliveries_event", table_name="alert_deliveries")
    op.drop_index("ix_alert_deliveries_due", table_name="alert_deliveries")
    op.drop_table("alert_deliveries")

    op.drop_index("ix_alert_events_run", table_name="alert_events")
    op.drop_index("ix_alert_events_occurred", table_name="alert_events")
    op.drop_index("ix_alert_events_alert", table_name="alert_events")
    op.drop_table("alert_events")

    op.drop_index("ix_alerts_principal", table_name="alerts")
    op.drop_index("ix_alerts_share", table_name="alerts")
    op.drop_index("ix_alerts_resource", table_name="alerts")
    op.drop_index("ix_alerts_watch", table_name="alerts")
    op.drop_index("ix_alerts_trigger", table_name="alerts")
    op.drop_index("ix_alerts_status_raised", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_alert_watches_kind", table_name="alert_watches")
    op.drop_table("alert_watches")
