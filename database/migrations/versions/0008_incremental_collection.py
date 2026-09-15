r"""Checkpoints, collection mode, and reconciliation drift.

Phase 7A gave ADG a timeline. This revision gives collection a *cadence*: a job may read
only what its source says has changed, resume where it left off, and be repaired on a slower
schedule by a run that reads everything.

Three things have to be recorded for that to be safe, and all three are added here.

## Where a job got to

``collector_checkpoints`` holds one resume point per ``(collector, job)``. It is keyed on the
job rather than on the collector host because a cursor belongs to whatever *issued* it: a
``uSNChanged`` watermark is a counter on a domain controller, so moving the job to a rebuilt
collector host does not invalidate it, while binding a different DC -- or the same DC after a
restore from backup, which reissues numbers it has already handed out -- does. That is what
``issuer`` is for, and why the advance rule compares it rather than the host.

The row also keeps the *refusal*: when a checkpoint may not advance, the reason is stored and
cleared only by the next successful advance. A refused cursor is the quiet failure mode of
every incremental system -- the job keeps running, keeps succeeding, and keeps resuming from
the same stale point -- so it is recorded where an operator looks rather than only logged.

## What a run set out to do

``scan_runs.mode`` says *why* a run is or is not incremental. ``incremental`` remains the flag
the reconciliation guard reads, and a check constraint pins ``mode = 'delta'`` to it in both
directions, so no row can claim to be a delta while remaining eligible to mark objects
absent. Existing rows are backfilled from the flag they already carry rather than defaulted
to ``full``: a run that was recorded as incremental was a delta in the only vocabulary it
had, and filing it beside runs that read everything would misstate the history this project
just spent a phase building.

``scan_run_checkpoints`` keeps the two cursors of one run -- where it resumed from, where it
got to -- as rows rather than as six columns on ``scan_runs``, because a checkpoint's four
fields mean nothing apart and most runs have neither.

## What the deltas missed

``scan_run_scopes`` gains three counters, recorded where the reconciliation itself is
recorded. Only the two presence corrections count as drift: an incremental run cannot observe
an absence, so a tombstone written by a reconciliation is a fact no amount of running the
delta more often would have produced. ``delta_runs_since`` is the denominator -- three
absences after fifty deltas and three after one are different statements about the cadence.

## Additive, and reversible

Every column is nullable or carries a server default, every new table is new, and nothing
existing is dropped or narrowed. ``downgrade()`` removes exactly what ``upgrade()`` added:
the checkpoints are lost, and every current-state and history table is untouched.

Revision ID: 0008_incremental_collection
Revises: 0007_history_model
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0008_incremental_collection"
down_revision: str | None = "0007_history_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOKEN_LENGTH: Final = 512

COLLECTION_MODES: Final[tuple[str, ...]] = ("full", "delta", "reconcile")
CHECKPOINT_KINDS: Final[tuple[str, ...]] = ("usn", "timestamp", "opaque")
COLLECTOR_KINDS: Final[tuple[str, ...]] = ("active_directory", "smb", "ntfs", "local_groups")
CHECKPOINT_ROLES: Final[tuple[str, ...]] = ("baseline", "result")


def _in_list(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    # --- scan_runs: mode, job, and the affirmation counters -----------------------------
    op.add_column(
        "scan_runs",
        sa.Column("mode", sa.Text(), nullable=False, server_default="full"),
    )
    op.add_column("scan_runs", sa.Column("job", sa.Text(), nullable=True))
    op.add_column(
        "scan_runs", sa.Column("affirmation_count_reported", sa.Integer(), nullable=True)
    )
    op.add_column(
        "scan_runs",
        sa.Column(
            "affirmation_count_applied", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "scan_runs",
        sa.Column("affirmations_refused", sa.Integer(), nullable=False, server_default="0"),
    )

    # Backfilled from the flag the row already carries, not defaulted. See the module
    # docstring: an existing incremental run was a delta, and saying so costs one UPDATE.
    op.execute("UPDATE scan_runs SET mode = 'delta' WHERE incremental")

    op.create_check_constraint(
        "ck_mode_valid", "scan_runs", _in_list("mode", COLLECTION_MODES)
    )
    op.create_check_constraint(
        "ck_scan_runs_mode_matches_incremental", "scan_runs", "(mode = 'delta') = incremental"
    )

    # --- scan_run_scopes: reconciliation drift ------------------------------------------
    for column in ("closed_absent", "revived", "delta_runs_since"):
        op.add_column(
            "scan_run_scopes",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )

    # --- scan_run_checkpoints -----------------------------------------------------------
    op.create_table(
        "scan_run_checkpoints",
        sa.Column("run_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("checkpoint_kind", sa.Text(), nullable=False),
        sa.Column("token", sa.String(length=TOKEN_LENGTH), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["scan_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "role"),
        sa.CheckConstraint(
            _in_list("role", CHECKPOINT_ROLES), name="ck_scan_run_checkpoints_role"
        ),
        sa.CheckConstraint(
            _in_list("checkpoint_kind", CHECKPOINT_KINDS),
            name="ck_checkpoint_kind_valid",
        ),
        comment="Where a delta run resumed from, and the cursor it left behind.",
    )

    # --- collector_checkpoints ----------------------------------------------------------
    op.create_table(
        "collector_checkpoints",
        sa.Column("collector", sa.Text(), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("checkpoint_kind", sa.Text(), nullable=False),
        sa.Column("token", sa.String(length=TOKEN_LENGTH), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("batch_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("collector_host", sa.Text(), nullable=True),
        sa.Column("advanced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_rejection_code", sa.Text(), nullable=True),
        sa.Column("last_rejection_message", sa.Text(), nullable=True),
        sa.Column("last_rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("collector", "job"),
        sa.CheckConstraint(
            _in_list("collector", COLLECTOR_KINDS),
            name="ck_collector_valid",
        ),
        sa.CheckConstraint(
            _in_list("checkpoint_kind", CHECKPOINT_KINDS),
            name="ck_checkpoint_kind_valid",
        ),
        comment=(
            "The resume point of each scheduled collection job. Advanced only by an "
            "applied batch or a succeeded run, and never backwards within one issuer."
        ),
    )
    op.create_index(
        "ix_collector_checkpoints_advanced_at", "collector_checkpoints", ["advanced_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_collector_checkpoints_advanced_at", table_name="collector_checkpoints")
    op.drop_table("collector_checkpoints")
    op.drop_table("scan_run_checkpoints")

    for column in ("delta_runs_since", "revived", "closed_absent"):
        op.drop_column("scan_run_scopes", column)

    op.drop_constraint("ck_scan_runs_mode_matches_incremental", "scan_runs", type_="check")
    op.drop_constraint("ck_mode_valid", "scan_runs", type_="check")
    for column in (
        "affirmations_refused",
        "affirmation_count_applied",
        "affirmation_count_reported",
        "job",
        "mode",
    ):
        op.drop_column("scan_runs", column)
