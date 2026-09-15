r"""The index the current-state presence predicate reads: open tombstones only.

Every ordinary current-state read in ADG now asks one extra question of every row it
returns — *does ``object_versions`` hold an open tombstone for this object?* — because a row
a reconciled scan proved gone must stop being live while staying in history
(:mod:`app.models.current`). This revision is the index that makes that question cheap.

## Why it is partial on two predicates rather than one

``valid_to IS NULL`` alone would index the open version of every object in the estate. That
is one entry per principal, edge, share, ACE and directory ADG holds — the whole store,
duplicated — and ``ux_object_versions_open`` already covers exactly that set for the
writer's "is there an open version" lookup.

The anti-join does not want the open versions. It wants the open *absences*, which are the
objects a reconciliation has removed and nothing has brought back: a handful after a normal
scan, a few thousand after a decommissioned server. Adding ``AND is_present = false`` makes
the index the size of the removals rather than of the estate, and turns each presence check
into a probe that almost always misses in one page.

## Nothing else changes

One index added, on one existing table. No column, constraint, or row is touched, and
``downgrade`` drops only what ``upgrade`` created. Correctness does not depend on it — the
predicate is in the query, not in the index — so a deployment that has not yet run this
revision answers the same and pays a heap lookup per row for it.

Revision ID: 0016_current_state_presence
Revises: 0015_remediation_change_plans
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_current_state_presence"
down_revision: str | None = "0015_remediation_change_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_object_versions_open_absent"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "object_versions",
        ["object_kind", "object_key"],
        unique=False,
        postgresql_where="valid_to IS NULL AND is_present = false",
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="object_versions")
