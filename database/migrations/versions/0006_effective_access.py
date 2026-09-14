"""Index the resources an access answer would otherwise miss.

Phase 4B adds no table and no column. It adds one index, for one reason.

"Which resources can this principal reach" is answered from `principal_references`: the
trustees of the principal's access token, looked up in an index rather than by evaluating
every directory in the estate against every principal in the domain. That works for every
resource somebody is named on — and misses, completely, the resources with a **NULL DACL**,
which grant everyone full access precisely by naming nobody.

Those are the rows an audit must never omit, so the candidate query unions them in. Without
an index that union is a sequential scan of the largest table in the schema on every call.
The predicate is `NOT dacl_present`, so the index holds only the rows that are actually
open to the estate, which on a healthy file server is none of them.

Revision ID: 0006_effective_access
Revises: 0005_ntfs_boundaries
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_effective_access"
down_revision: str | None = "0005_ntfs_boundaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_ntfs_resources_null_dacl",
        "ntfs_resources",
        ["resource_key"],
        unique=False,
        postgresql_where=sa.text("NOT dacl_present"),
    )


def downgrade() -> None:
    op.drop_index("ix_ntfs_resources_null_dacl", table_name="ntfs_resources")
