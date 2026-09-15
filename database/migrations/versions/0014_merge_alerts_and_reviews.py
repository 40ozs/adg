"""Rejoin the two revision branches that grew off ``0012_merge_concurrent_phases``.

Two phases were built at once against the same working tree, and each added a revision
revising the previous merge:

* ``0013_alert_pipeline`` — watches, alerts, alert events and the delivery outbox.
* ``0013_access_review_workflow`` — the access-review workflow tables.

Neither depends on the other and neither touches a table the other creates, so the graph is a
genuine fork rather than a conflict. What it breaks is ``alembic upgrade head``, which refuses
to choose between two heads — an operator upgrading a deployment would get *"Multiple head
revisions are present"* and no guidance about which to apply.

**This revision creates and drops nothing.** It is a join in the revision graph and nothing
else, which is why it can be written without knowing anything about either branch's contents.
``upgrade()`` and ``downgrade()`` are deliberately empty; a merge revision that did work would
be a schema change hiding inside a bookkeeping entry, and the next person to read the graph
would have no reason to look inside it.

The same shape and the same reason as ``0012_merge_concurrent_phases``, one phase later.

Revision ID: 0014_merge_alerts_and_reviews
Revises: 0013_alert_pipeline, 0013_access_review_workflow
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0014_merge_alerts_and_reviews"
down_revision: str | Sequence[str] | None = (  # type: ignore[assignment]
    "0013_alert_pipeline",
    "0013_access_review_workflow",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing. See the module docstring: this is a graph join, not a schema change."""


def downgrade() -> None:
    """Nothing. Downgrading past this re-forks the graph into its two branches."""
