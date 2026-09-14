r"""NTFS share-root resources, raw NTFS ACEs, and the references they make.

Creates the two tables declared in ``backend/app/models/schema.py``, which stays the single
description of the schema; this revision was generated from it and a smoke test reflects the
live database to keep the two from drifting.

What the shape encodes:

* A directory is identified by its **canonical UNC path**, case-folded —
  ``DirectoryResource.identity_key``. A local path such as ``D:\Shares\Finance`` names
  nothing on its own, because it does not say which server, so it is recorded beside the
  key rather than used as one.
* ``ntfs_resources.share_key`` is the ``SmbShare.identity_key`` of the share the path sits
  under. That column is the link between the two layers: with it, one share can report its
  raw SMB ACL and the raw NTFS ACL of its root as two separate answers, which is exactly
  what the share layer and the file-system layer are — separate.
* **No foreign keys between the resource tables**, matching ``0003_smb_resources``. An NTFS
  run can read a share root before any SMB run has described the share publishing it, and
  that reading is still a fact. Rejecting it would discard evidence at the moment a partial
  scan most needs to record what it did manage to read.
* ``dacl_present`` is not nullable and has no default. ``false`` is a NULL DACL — everyone
  has full access — while a present-but-empty DACL grants nobody access. Those are opposite
  facts, and a column that could also mean "the collector did not say" would let one be read
  as the other. ``ck_ntfs_resources_null_dacl_has_no_aces`` keeps the pair consistent.
* ``ck_ntfs_resources_blocked_inheritance_is_a_boundary`` encodes the domain invariant that
  a directory refusing inherited ACEs is, by definition, a place where permissions change.
* ``ntfs_aces.ace_flags`` is the raw ``ACE_HEADER.AceFlags`` byte, unknown bits included.
  Inheritance and propagation are deliberately *not* split into separate boolean columns:
  they are one byte in the descriptor, and splitting them would make a round trip lossy for
  any bit a later Windows release defines.
* ``ck_ntfs_aces_source_matches_inherited_bit`` stops the ``source`` column and bit ``0x10``
  of the flags byte — two spellings of one fact — from ever disagreeing.
* ``access_mask`` is ``bigint``: an access mask is unsigned 32-bit and ``0xFFFFFFFF``
  overflows PostgreSQL's signed ``integer``.
* ``acl_hash`` holds the collector's digest of the normalized DACL it read (contract 1.2),
  stored as reported. It is never rewritten from the stored ACEs, because the point of
  keeping it is that it can disagree with them and so show that entries went missing.

``principal_references`` gains ``ntfs_ace`` as a reference kind, so that "which resources
name this SID" stays one indexed lookup across both layers rather than a union over every
ACL table there will eventually be. Alembic does not compare check constraints, so the swap
is written by hand.

Nothing here can express absence. There is no tombstone column and no delete path: a
directory missing from a partial or failed scan keeps its row, and marking an object as no
longer observed arrives with history in Phase 7.

Revision ID: 0004_ntfs_resources
Revises: 0003_smb_resources
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_ntfs_resources"
down_revision: str | None = "0003_smb_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REFERENCE_KIND_CONSTRAINT = "ck_reference_kind_valid"


def upgrade() -> None:
    op.create_table(
        "ntfs_resources",
        sa.Column("resource_key", sa.String(length=512), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("server_key", sa.String(length=512), nullable=False),
        sa.Column("share_key", sa.String(length=512), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("owner_sid", sa.String(length=200), nullable=True),
        sa.Column("group_sid", sa.String(length=200), nullable=True),
        sa.Column("dacl_present", sa.Boolean(), nullable=False),
        sa.Column("dacl_protected", sa.Boolean(), nullable=False),
        sa.Column("inheritance_enabled", sa.Boolean(), nullable=False),
        sa.Column("is_acl_boundary", sa.Boolean(), nullable=False),
        sa.Column("ace_count", sa.Integer(), nullable=False),
        sa.Column("depth_from_share_root", sa.Integer(), nullable=True),
        sa.Column("acl_hash", sa.String(length=64), nullable=True),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "acl_hash IS NULL OR acl_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ntfs_resources_acl_hash_shape",
        ),
        sa.CheckConstraint("ace_count >= 0", name="ck_ntfs_resources_ace_count_non_negative"),
        sa.CheckConstraint(
            "dacl_present OR ace_count = 0", name="ck_ntfs_resources_null_dacl_has_no_aces"
        ),
        sa.CheckConstraint(
            "depth_from_share_root IS NULL OR depth_from_share_root >= 0",
            name="ck_ntfs_resources_depth_non_negative",
        ),
        sa.CheckConstraint(
            "inheritance_enabled OR is_acl_boundary",
            name="ck_ntfs_resources_blocked_inheritance_is_a_boundary",
        ),
        sa.PrimaryKeyConstraint("resource_key"),
        comment="Directories whose NTFS security descriptor ADG has read, keyed by UNC path.",
    )
    op.create_index("ix_ntfs_resources_acl_hash", "ntfs_resources", ["acl_hash"], unique=False)
    op.create_index(
        "ix_ntfs_resources_last_observed_run",
        "ntfs_resources",
        ["last_observed_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_ntfs_resources_server", "ntfs_resources", ["server_key", "resource_key"], unique=False
    )
    op.create_index(
        "ix_ntfs_resources_share", "ntfs_resources", ["share_key", "resource_key"], unique=False
    )

    op.create_table(
        "ntfs_aces",
        sa.Column("ace_key", sa.String(length=512), nullable=False),
        sa.Column("resource_key", sa.String(length=512), nullable=False),
        sa.Column("trustee_sid", sa.String(length=200), nullable=False),
        sa.Column("trustee_key", sa.String(length=512), nullable=False),
        sa.Column("ace_type", sa.Text(), nullable=False),
        sa.Column("access_mask", sa.BigInteger(), nullable=False),
        sa.Column("ace_flags", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("inherited_from", sa.Text(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=True),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "((ace_flags & 16) <> 0) = (source = 'inherited')",
            name="ck_ntfs_aces_source_matches_inherited_bit",
        ),
        sa.CheckConstraint("ace_type IN ('allow', 'deny')", name="ck_ace_type_valid"),
        sa.CheckConstraint(
            "inherited_from IS NULL OR source = 'inherited'",
            name="ck_ntfs_aces_origin_only_when_inherited",
        ),
        sa.CheckConstraint("source IN ('explicit', 'inherited')", name="ck_source_valid"),
        sa.CheckConstraint(
            "strpos(ace_key, resource_key || '|') = 1", name="ck_ntfs_aces_key_scoped_by_resource"
        ),
        sa.CheckConstraint(
            "access_mask >= 0 AND access_mask <= 4294967295",
            name="ck_ntfs_aces_access_mask_range",
        ),
        sa.CheckConstraint(
            "ace_flags >= 0 AND ace_flags <= 255", name="ck_ntfs_aces_ace_flags_range"
        ),
        sa.CheckConstraint(
            "order_index IS NULL OR order_index >= 0",
            name="ck_ntfs_aces_order_index_non_negative",
        ),
        sa.PrimaryKeyConstraint("ace_key"),
        comment="Raw NTFS ACEs, exactly as read. No inheritance is resolved and no Deny applied.",
    )
    op.create_index(
        "ix_ntfs_aces_last_observed_run", "ntfs_aces", ["last_observed_run_id"], unique=False
    )
    op.create_index("ix_ntfs_aces_resource", "ntfs_aces", ["resource_key", "ace_key"], unique=False)
    op.create_index(
        "ix_ntfs_aces_trustee", "ntfs_aces", ["trustee_key", "resource_key"], unique=False
    )
    op.create_index("ix_ntfs_aces_trustee_sid", "ntfs_aces", ["trustee_sid"], unique=False)

    # Widening an enumeration check, not replacing it: every value the old constraint
    # allowed is still allowed, so no existing row can be invalidated.
    op.drop_constraint(_REFERENCE_KIND_CONSTRAINT, "principal_references", type_="check")
    op.create_check_constraint(
        _REFERENCE_KIND_CONSTRAINT,
        "principal_references",
        "reference_kind IN ('smb_ace', 'ntfs_ace')",
    )


def downgrade() -> None:
    # Narrowing the constraint again would fail while any ntfs_ace reference remained, so
    # those rows go first. They describe the tables this revision is about to drop.
    op.execute("DELETE FROM principal_references WHERE reference_kind = 'ntfs_ace'")
    op.drop_constraint(_REFERENCE_KIND_CONSTRAINT, "principal_references", type_="check")
    op.create_check_constraint(
        _REFERENCE_KIND_CONSTRAINT, "principal_references", "reference_kind IN ('smb_ace')"
    )

    op.drop_index("ix_ntfs_aces_trustee_sid", table_name="ntfs_aces")
    op.drop_index("ix_ntfs_aces_trustee", table_name="ntfs_aces")
    op.drop_index("ix_ntfs_aces_resource", table_name="ntfs_aces")
    op.drop_index("ix_ntfs_aces_last_observed_run", table_name="ntfs_aces")
    op.drop_table("ntfs_aces")

    op.drop_index("ix_ntfs_resources_share", table_name="ntfs_resources")
    op.drop_index("ix_ntfs_resources_server", table_name="ntfs_resources")
    op.drop_index("ix_ntfs_resources_last_observed_run", table_name="ntfs_resources")
    op.drop_index("ix_ntfs_resources_acl_hash", table_name="ntfs_resources")
    op.drop_table("ntfs_resources")
