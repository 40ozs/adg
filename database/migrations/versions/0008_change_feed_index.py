r"""The index a change feed reads: ``object_versions`` by when a version opened.

Phase 7A left this as prerequisite 3 of its handoff, in these words:

    ``ObjectTimeline.changes()`` gives per-object transitions; "what changed between Tuesday
    and Friday across the estate" is a different query and has no index for it yet --
    ``ix_object_versions_closed_at`` is keyed on ``valid_to`` alone and would serve a
    whole-estate scan, not a per-scope one.

## Why one index is enough, and why it is on ``valid_from``

Every change opens a version. A creation opens one; a modification opens one; **a removal
opens one too**, because a tombstone is a version rather than the absence of a row. So the
set of changes in an interval is exactly the set of versions whose ``valid_from`` falls
inside it -- one range scan, no union with a second query over ``valid_to``, and no way for
two halves of a feed to disagree about what a change is.

``(valid_from, id)`` rather than ``valid_from`` alone, because the feed pages on both. One
scan opens thousands of versions at a single instant, so a cursor carrying only the
timestamp would either skip every other version at that instant or return them all again;
the row id breaks the tie, and having it in the index means the keyset comparison is served
by the same scan rather than by a sort afterwards.

Ascending, although the feed reads newest first: PostgreSQL scans a b-tree backwards at
essentially the same cost, and an index declared ``DESC`` would have to be declared that way
in ``app/models/schema.py`` too, where the reflected-index comparison in
``tests/db/test_schema.py`` cannot see sort direction. An index whose declaration and
migration can silently disagree is worse than one column of notional plan tidiness.

The scope predicates are filters applied to what this returns, not the driving predicate:
``ix_object_versions_container`` and ``ix_object_versions_related`` already exist for the
indexed-equality cases, and the prefix tests a share or directory scope needs
(:mod:`app.changes.scope`) are cheap over a window's worth of rows and would not be over a
timeline's worth. That is why :class:`app.changes.service.ChangeFilter` requires a window and
bounds an unscoped one.

## Nothing else changes

One index added, on one existing table. No column, constraint, or row is touched, and
``downgrade`` drops only what ``upgrade`` created. A deployment that has not yet run this
revision serves the same answers more slowly; nothing depends on the index for correctness.

Revision ID: 0008_change_feed_index
Revises: 0007_history_model
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_change_feed_index"
down_revision: str | None = "0007_history_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_object_versions_opened_at"


def upgrade() -> None:
    op.create_index(INDEX_NAME, "object_versions", ["valid_from", "id"], unique=False)


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="object_versions")
