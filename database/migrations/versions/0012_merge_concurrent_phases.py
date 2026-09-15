"""Bring the branches that concurrent phases created back to one head.

This revision creates nothing and drops nothing. It exists because phases 8 and 9 were
built at the same time in one working tree, each adding revisions on top of
`0007_history_model`, which left `alembic upgrade head` refusing to run at all — the graph
had four heads and Alembic cannot choose between them.

A merge revision is the mechanism for exactly that: it declares that the branches are
independent and that applying all of them, in any order Alembic picks, produces one schema.
That claim is true here because no two of the branches touch the same table. Each one
creates its own.

**If the branches are later linearized** — somebody re-points the `down_revision` of each
phase's first revision so the whole thing is one chain — this file becomes unnecessary and
should be deleted in the same change. Leaving a merge over a chain that no longer branches
would not break anything, but it would tell a future reader that something happened here
that did not.

Revision ID: 0012_merge_concurrent_phases
Revises: 0008_change_feed_index, 0008_governance_model, 0009_risk_findings,
         0011_simulation_overlays
"""

from collections.abc import Sequence

revision: str = "0012_merge_concurrent_phases"
down_revision: str | Sequence[str] | None = (  # type: ignore[assignment]
    "0008_change_feed_index",
    "0008_governance_model",
    "0009_risk_findings",
    "0011_simulation_overlays",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing. A merge revision joins branches; it does not change the schema."""


def downgrade() -> None:
    """Nothing. Downgrading past this point splits the graph back into its branches."""
