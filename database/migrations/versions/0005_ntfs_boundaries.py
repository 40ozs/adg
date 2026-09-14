r"""Boundary evidence on ``ntfs_resources``: why, against what, and of what kind.

Phase 3A stored ``is_acl_boundary`` as a bare boolean that a share-root collector set to
``true`` by fiat, because a root's parent lies outside the share and there was nothing to
compare it against. A tree walk reaches directories that *do* have a readable parent, so the
flag becomes a claim that can be right or wrong — and a claim with no evidence behind it is
one nobody can check. This revision adds the evidence.

* ``boundary_reason`` says **why**. On a row that is *not* a boundary, ``NULL`` is the only
  value and it means "this resource carries exactly what its parent hands down". The cases
  where the answer genuinely is unknown have their own reasons — ``scan_root``,
  ``parent_unreadable``, ``parent_null_dacl`` — and every one of them also sets
  ``is_acl_boundary``. That asymmetry is deliberate: a boundary wrongly reported *true*
  costs one extra stored ACL, while a boundary wrongly reported *false* tells the next scan
  it may stop looking, and silently discards every permission change beneath it.

  ``ck_ntfs_resources_reason_implies_a_boundary`` constrains one direction only. A reason
  without a boundary contradicts itself at every contract version; a boundary without a
  reason is what a collector speaking 1.0 through 1.2 legitimately produces, because it
  sets the flag on a share root and has never heard of the field. Those rows keep a ``NULL``
  reason rather than a backfilled one: the derivation would be a verdict nobody made, and
  the API already reports the server's own derivation beside the collector's claim.

* ``parent_acl_hash`` says **against what**. It is *not* the value the verdict was compared
  with — that is the parent's projection onto a child, which the server recomputes from the
  parent's own stored ACEs (:mod:`app.domain.inheritance`) — but the record of which
  reading of the parent the collector judged against. Without it, a server/collector
  disagreement cannot be told from the parent having simply been changed in between.

* ``resource_kind`` says **of what kind**, because the projection differs: a directory
  receives its parent's ``CONTAINER_INHERIT`` entries and keeps propagating them, while a
  file receives the ``OBJECT_INHERIT`` ones with every inheritance flag stripped. Comparing
  a file against the container projection would report a boundary on every file in the
  estate. It is ``NOT NULL`` with a server default rather than nullable: file scanning did
  not exist before contract 1.3, so every existing row describes a directory and there is
  no third state to represent.

**Nothing is backfilled.** Every row Phase 3A wrote is a boundary with no reason, and it
stays that way. The reasons could be derived — protection from ``dacl_protected``, a NULL
DACL from ``dacl_present``, a share root from the path — but deriving them here would write
a verdict into a row whose collector never made one, and the next scan of that directory
replaces it with a real answer anyway.

Additive throughout. Three nullable-or-defaulted columns, two shape checks, one consistency
check and one partial index; no existing column is altered, narrowed, or dropped, no
existing row is rewritten, and no collector sending contract 1.0 through 1.2 is affected.

Revision ID: 0005_ntfs_boundaries
Revises: 0004_ntfs_resources
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ntfs_boundaries"
down_revision: str | None = "0004_ntfs_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BOUNDARY_REASONS = (
    "share_root",
    "scan_root",
    "protected_dacl",
    "null_dacl",
    "parent_null_dacl",
    "parent_unreadable",
    "acl_differs_from_parent",
)

_RESOURCE_KINDS = ("directory", "file")

_OLD_COMMENT = "Directories whose NTFS security descriptor ADG has read, keyed by UNC path."
_NEW_COMMENT = (
    "File-system resources whose NTFS security descriptor ADG has read, keyed by UNC "
    "path. Directories unless resource_kind says otherwise."
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.add_column(
        "ntfs_resources",
        sa.Column("resource_kind", sa.String(length=32), nullable=False, server_default="directory"),
    )
    op.add_column("ntfs_resources", sa.Column("boundary_reason", sa.String(length=32), nullable=True))
    op.add_column("ntfs_resources", sa.Column("parent_acl_hash", sa.String(length=64), nullable=True))

    op.create_check_constraint(
        "ck_resource_kind_valid",
        "ntfs_resources",
        f"resource_kind IN ({_quoted(_RESOURCE_KINDS)})",
    )
    op.create_check_constraint(
        "ck_boundary_reason_valid",
        "ntfs_resources",
        f"boundary_reason IS NULL OR boundary_reason IN ({_quoted(_BOUNDARY_REASONS)})",
    )
    op.create_check_constraint(
        "ck_ntfs_resources_reason_implies_a_boundary",
        "ntfs_resources",
        "boundary_reason IS NULL OR is_acl_boundary",
    )
    op.create_check_constraint(
        "ck_ntfs_resources_parent_acl_hash_shape",
        "ntfs_resources",
        "parent_acl_hash IS NULL OR parent_acl_hash ~ '^[0-9a-f]{64}$'",
    )

    # "Where do permissions change under this share" is the question a tree scan exists to
    # answer. Partial, because the boundaries are the small minority of rows a full walk
    # writes, and the ones that are not boundaries are never what is being looked for.
    op.create_index(
        "ix_ntfs_resources_boundaries",
        "ntfs_resources",
        ["share_key", "resource_key"],
        unique=False,
        postgresql_where=sa.text("is_acl_boundary"),
    )

    # The table no longer holds only directories. Kept in step with the declaration in
    # backend/app/models/schema.py, because `alembic check` compares comments too — and a
    # revision that leaves the live schema one edit away from the declaration is a revision
    # that will report drift on every run until somebody guesses which side is right.
    op.create_table_comment("ntfs_resources", _NEW_COMMENT, existing_comment=_OLD_COMMENT)


def downgrade() -> None:
    op.create_table_comment("ntfs_resources", _OLD_COMMENT, existing_comment=_NEW_COMMENT)
    op.drop_index("ix_ntfs_resources_boundaries", table_name="ntfs_resources")
    op.drop_constraint("ck_ntfs_resources_parent_acl_hash_shape", "ntfs_resources", type_="check")
    op.drop_constraint("ck_ntfs_resources_reason_implies_a_boundary", "ntfs_resources", type_="check")
    op.drop_constraint("ck_boundary_reason_valid", "ntfs_resources", type_="check")
    op.drop_constraint("ck_resource_kind_valid", "ntfs_resources", type_="check")

    # The rows stay; only the evidence columns go. A file resource left behind by a
    # downgraded deployment keeps its row and is indistinguishable from a directory again,
    # which is the honest consequence of dropping the column that told them apart.
    op.drop_column("ntfs_resources", "parent_acl_hash")
    op.drop_column("ntfs_resources", "boundary_reason")
    op.drop_column("ntfs_resources", "resource_kind")
