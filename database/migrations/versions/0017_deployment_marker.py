r"""The deployment marker: one row saying what a database is for.

ADG gained a demo runtime mode (``ADG_RUNTIME_MODE=demo``) that fills a database with a
synthetic estate and can wipe and refill it on demand. That is only safe if the database it
wipes is positively known to be a demo database. A connection-string convention alone is a
guard somebody edits; this table is the second, independent one: the demo reset writes the
marker when it initializes a database, and refuses to touch any database whose marker is
missing-and-populated or names another mode. A demo API refuses to start against one too.

## Nothing else changes

One table added, empty on every existing database. No existing column, constraint, index
or row is touched, production code never reads it, and ``downgrade`` drops only what
``upgrade`` created. See docs/architecture/demo-mode.md.

Revision ID: 0017_deployment_marker
Revises: 0016_current_state_presence
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_deployment_marker"
down_revision: str | None = "0016_current_state_presence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployment_marker",
        sa.Column("marker_id", sa.SmallInteger(), primary_key=True),
        sa.Column("runtime_mode", sa.Text(), nullable=False),
        sa.Column("dataset_key", sa.Text(), nullable=True),
        sa.Column("dataset_seed", sa.Text(), nullable=True),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("marked_by", sa.Text(), nullable=False),
        sa.CheckConstraint("marker_id = 1", name="ck_deployment_marker_singleton"),
        sa.CheckConstraint(
            "runtime_mode IN ('production', 'demo')", name="ck_deployment_marker_runtime_mode"
        ),
        sa.CheckConstraint(
            "runtime_mode <> 'demo' OR dataset_key IS NOT NULL",
            name="ck_deployment_marker_demo_names_its_dataset",
        ),
        comment=(
            "What this database is for, written by the operator command that initialized it. "
            "A demo deployment refuses to start, and a demo reset refuses to run, against a "
            "database that is not marked demo -- so synthetic data cannot be written over real "
            "observations by pointing a demo at the wrong connection string. See "
            "docs/architecture/demo-mode.md."
        ),
    )


def downgrade() -> None:
    op.drop_table("deployment_marker")
