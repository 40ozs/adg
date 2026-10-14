r"""Record which collected state each risk evaluation read.

A risk total is a count over the findings the last evaluation left open, and the evaluation
read the estate as collection stood at that moment. Until now nothing recorded *which*
collected state that was, so a report could not say whether its counts still describe the
estate ADG holds or an earlier one, and the two read identically (ADG-DEMO-03).

``basis_token`` is the collection basis token (:class:`app.domain.basis.CollectionBasis`)
read in the evaluation's own session as it started: the same token every derived access
answer carries and every ETag is keyed by. A report compares it with the current token.

## Nothing else changes

One nullable column added. Existing evaluations keep ``NULL``, which the report reads as
"ADG cannot say which collected state this evaluation read" -- never as "current". No
existing column, constraint, index or row is touched, and ``downgrade`` drops only what
``upgrade`` added.

Revision ID: 0018_risk_evaluation_basis
Revises: 0017_deployment_marker
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_risk_evaluation_basis"
down_revision: str | None = "0017_deployment_marker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "risk_evaluations",
        sa.Column(
            "basis_token",
            sa.Text(),
            nullable=True,
            comment=(
                "The collection basis token read as the evaluation started: which collected "
                "state its counts describe. NULL on evaluations recorded before it existed."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("risk_evaluations", "basis_token")
