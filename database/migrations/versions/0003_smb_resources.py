"""Servers, SMB shares, raw share ACEs, and the principal references they make.

Creates the four tables declared in ``backend/app/models/schema.py``, which stays the single
description of the schema; this revision was generated from it and a smoke test reflects the
live database to keep the two from drifting.

What the shape encodes:

* Keys are the domain's own identity strings — ``servers.server_key`` is
  ``Server.identity_key``, ``smb_shares.share_key`` is ``SmbShare.identity_key``, and
  ``smb_share_aces.ace_key`` is ``SmbShareAce.identity_key(share_key)``. A UNC path is
  exactly ``\\<server>\\<share>`` and is derived rather than stored, so one share can never
  acquire two identities that disagree.
* **No foreign keys between the resource tables.** Batches may arrive in any order, and a
  share ACL read from a machine no run has described as a server is still a fact. Rejecting
  it would discard evidence at exactly the moment a partial scan most needs to record what
  it did read. This mirrors ``membership_edges``, which references principals it may not
  hold.
* ``smb_share_aces.trustee_key`` is the principal key the ACE points at, host-scoped for a
  BUILTIN trustee because ``S-1-5-32-544`` names a different group on every computer.
  Whether that principal is *known* is a join against ``principals``; there is no stored
  "resolved" flag to go stale when a later AD run resolves the SID.
* ``access_mask`` is ``bigint``: an access mask is unsigned 32-bit and ``0xFFFFFFFF``
  overflows PostgreSQL's signed ``integer``.
* A share ACE carries exactly one of ``access_mask`` and ``permission``, enforced by a check
  constraint. Recording both would fabricate a reading the collecting API never took.

Nothing here can express absence. There is no tombstone column and no delete path: a share
missing from a partial or failed scan keeps its row, and marking an object as no longer
observed arrives with history in Phase 7.

Revision ID: 0003_smb_resources
Revises: 0002_ad_graph
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_smb_resources"
down_revision: str | None = "0002_ad_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "principal_references",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("principal_key", sa.String(length=512), nullable=False),
        sa.Column("sid", sa.String(length=200), nullable=False),
        sa.Column("host_key", sa.Text(), nullable=True),
        sa.Column("reference_kind", sa.Text(), nullable=False),
        sa.Column("reference_key", sa.String(length=512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reference_kind IN ('smb_ace')", name="ck_reference_kind_valid"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "principal_key",
            "reference_kind",
            "reference_key",
            name="uq_principal_references_identity",
        ),
        comment="Every reference from a resource ACL to a principal key. Resolution is a join.",
    )
    op.create_index(
        "ix_principal_references_principal",
        "principal_references",
        ["principal_key", "reference_key"],
        unique=False,
    )
    op.create_index("ix_principal_references_sid", "principal_references", ["sid"], unique=False)
    op.create_index(
        "ix_principal_references_target",
        "principal_references",
        ["reference_kind", "reference_key"],
        unique=False,
    )
    op.create_table(
        "servers",
        sa.Column("server_key", sa.String(length=512), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("dns_host_name", sa.Text(), nullable=True),
        sa.Column("netbios_name", sa.Text(), nullable=True),
        sa.Column("computer_sid", sa.String(length=200), nullable=True),
        sa.Column("domain_sid", sa.String(length=200), nullable=True),
        sa.Column("is_domain_member", sa.Boolean(), nullable=True),
        sa.Column("operating_system", sa.Text(), nullable=True),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("server_key"),
        comment="Windows computers that have reported shares, keyed by the name collected.",
    )
    op.create_index("ix_servers_computer_sid", "servers", ["computer_sid"], unique=False)
    op.create_index(
        "ix_servers_last_observed_run", "servers", ["last_observed_run_id"], unique=False
    )
    op.create_table(
        "smb_share_aces",
        sa.Column("ace_key", sa.String(length=512), nullable=False),
        sa.Column("share_key", sa.String(length=512), nullable=False),
        sa.Column("trustee_sid", sa.String(length=200), nullable=False),
        sa.Column("trustee_key", sa.String(length=512), nullable=False),
        sa.Column("ace_type", sa.Text(), nullable=False),
        sa.Column("access_mask", sa.BigInteger(), nullable=True),
        sa.Column("permission", sa.Text(), nullable=True),
        sa.Column("right_token", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=True),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ace_type IN ('allow', 'deny')", name="ck_ace_type_valid"),
        sa.CheckConstraint(
            "permission IS NULL OR permission IN ('read', 'change', 'full')",
            name="ck_permission_valid",
        ),
        sa.CheckConstraint(
            "strpos(ace_key, share_key || '|') = 1", name="ck_smb_share_aces_key_scoped_by_share"
        ),
        sa.CheckConstraint(
            "(access_mask IS NULL) <> (permission IS NULL)",
            name="ck_smb_share_aces_exactly_one_right",
        ),
        sa.CheckConstraint(
            "access_mask IS NULL OR (access_mask >= 0 AND access_mask <= 4294967295)",
            name="ck_smb_share_aces_access_mask_range",
        ),
        sa.CheckConstraint(
            "order_index IS NULL OR order_index >= 0",
            name="ck_smb_share_aces_order_index_non_negative",
        ),
        sa.PrimaryKeyConstraint("ace_key"),
        comment="Raw share-level ACEs, exactly as read. No effective access is derived here.",
    )
    op.create_index(
        "ix_smb_share_aces_last_observed_run",
        "smb_share_aces",
        ["last_observed_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_smb_share_aces_share", "smb_share_aces", ["share_key", "ace_key"], unique=False
    )
    op.create_index(
        "ix_smb_share_aces_trustee", "smb_share_aces", ["trustee_key", "share_key"], unique=False
    )
    op.create_index(
        "ix_smb_share_aces_trustee_sid", "smb_share_aces", ["trustee_sid"], unique=False
    )
    op.create_table(
        "smb_shares",
        sa.Column("share_key", sa.String(length=512), nullable=False),
        sa.Column("server_key", sa.String(length=512), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("share_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("concurrent_user_limit", sa.Integer(), nullable=True),
        sa.Column("caching_mode", sa.Text(), nullable=True),
        sa.Column("is_special", sa.Boolean(), nullable=True),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "share_type IN ('disk', 'print', 'ipc', 'device', 'unknown')",
            name="ck_share_type_valid",
        ),
        sa.CheckConstraint(
            "strpos(share_key, server_key || '|') = 1", name="ck_smb_shares_key_scoped_by_server"
        ),
        sa.CheckConstraint(
            "concurrent_user_limit IS NULL OR concurrent_user_limit >= 0",
            name="ck_smb_shares_user_limit_non_negative",
        ),
        sa.PrimaryKeyConstraint("share_key"),
        comment="SMB shares. A share is a publication of a directory, not the directory.",
    )
    op.create_index(
        "ix_smb_shares_last_observed_run", "smb_shares", ["last_observed_run_id"], unique=False
    )
    op.create_index("ix_smb_shares_server", "smb_shares", ["server_key", "share_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_smb_shares_server", table_name="smb_shares")
    op.drop_index("ix_smb_shares_last_observed_run", table_name="smb_shares")
    op.drop_table("smb_shares")
    op.drop_index("ix_smb_share_aces_trustee_sid", table_name="smb_share_aces")
    op.drop_index("ix_smb_share_aces_trustee", table_name="smb_share_aces")
    op.drop_index("ix_smb_share_aces_share", table_name="smb_share_aces")
    op.drop_index("ix_smb_share_aces_last_observed_run", table_name="smb_share_aces")
    op.drop_table("smb_share_aces")
    op.drop_index("ix_servers_last_observed_run", table_name="servers")
    op.drop_index("ix_servers_computer_sid", table_name="servers")
    op.drop_table("servers")
    op.drop_index("ix_principal_references_target", table_name="principal_references")
    op.drop_index("ix_principal_references_sid", table_name="principal_references")
    op.drop_index("ix_principal_references_principal", table_name="principal_references")
    op.drop_table("principal_references")
