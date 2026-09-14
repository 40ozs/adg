"""AD principals, membership edges, and scan-run provenance.

Creates the eight tables declared in ``backend/app/models/schema.py``, which is the single
description of the schema; this revision was generated from it and a smoke test reflects the
live database to keep the two from drifting.

What the shape encodes:

* ``principals.principal_key`` and ``membership_edges.group_key``/``member_key`` hold the
  domain's identity keys (a SID, or ``host|sid`` for a local group), so traversal joins on
  the exact strings the domain layer produces and a BUILTIN SID cannot merge across hosts.
* ``scan_run_batches`` is keyed on ``(run_id, batch_id)`` and ``observations`` on
  ``(run_id, source_key)``. Those two primary keys *are* the collector protocol's
  idempotency guarantees, enforced by the database rather than by application logic.
* Both endpoints of ``membership_edges`` are indexed, because traversal runs in both
  directions: "who is in this group" and "which groups contain this principal".
* Enumerated columns are ``text`` with check constraints generated from the domain enums;
  adding a value later is a constraint change, not a locking type migration.

Nothing here can express absence. There is no tombstone column and no delete path: marking
an object as no longer observed arrives with history in Phase 7.

Revision ID: 0002_ad_graph
Revises: 0001_baseline
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_ad_graph"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collector_sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("collector", sa.Text(), nullable=False),
        sa.Column("collector_host", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("collector_version", sa.Text(), nullable=True),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "collector IN ('active_directory', 'smb', 'ntfs', 'local_groups')",
            name="ck_collector_valid",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
        comment="Distinct (collector, host, method, version, target) tuples that reported facts.",
    )
    op.create_table(
        "membership_edges",
        sa.Column("edge_key", sa.String(length=512), nullable=False),
        sa.Column("group_key", sa.String(length=512), nullable=False),
        sa.Column("member_key", sa.String(length=512), nullable=False),
        sa.Column("group_sid", sa.String(length=200), nullable=False),
        sa.Column("member_sid", sa.String(length=200), nullable=False),
        sa.Column("edge_kind", sa.Text(), nullable=False),
        sa.Column("host_key", sa.Text(), nullable=True),
        sa.Column("member_kind", sa.Text(), nullable=True),
        sa.Column(
            "is_foreign_security_principal", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(edge_kind = 'local_group_member') = (host_key IS NOT NULL)",
            name="ck_membership_edges_local_has_host",
        ),
        sa.CheckConstraint(
            "edge_kind IN ('directory_group_member', 'primary_group', 'local_group_member', "
            "'well_known_implicit')",
            name="ck_edge_kind_valid",
        ),
        sa.CheckConstraint(
            "member_kind IS NULL OR member_kind IN ('user', 'domain_group', 'local_group', "
            "'computer', 'managed_service_account', 'well_known', 'foreign_security_principal', "
            "'unresolved')",
            name="ck_member_kind_valid",
        ),
        sa.CheckConstraint("group_key <> member_key", name="ck_membership_edges_no_self_edge"),
        sa.PrimaryKeyConstraint("edge_key"),
        comment="Directed membership edges. member_key is a member of group_key.",
    )
    op.create_index(
        "ix_membership_edges_group", "membership_edges", ["group_key", "member_key"], unique=False
    )
    op.create_index(
        "ix_membership_edges_last_observed_run",
        "membership_edges",
        ["last_observed_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_membership_edges_member", "membership_edges", ["member_key", "group_key"], unique=False
    )
    op.create_table(
        "principals",
        sa.Column("principal_key", sa.String(length=512), nullable=False),
        sa.Column("sid", sa.String(length=200), nullable=False),
        sa.Column("principal_kind", sa.Text(), nullable=False),
        sa.Column("host_key", sa.Text(), nullable=True),
        sa.Column("domain_sid", sa.String(length=200), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("sam_account_name", sa.Text(), nullable=True),
        sa.Column("user_principal_name", sa.Text(), nullable=True),
        sa.Column("distinguished_name", sa.Text(), nullable=True),
        sa.Column("group_scope", sa.Text(), nullable=True),
        sa.Column("group_type", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("unresolved_reason", sa.Text(), nullable=True),
        sa.Column("last_known_name", sa.Text(), nullable=True),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_run_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(principal_kind = 'local_group') = (host_key IS NOT NULL)",
            name="ck_principals_local_group_has_host",
        ),
        sa.CheckConstraint(
            "group_scope IS NULL OR group_scope IN ('domain_local', 'global', 'universal', "
            "'builtin_local', 'unknown')",
            name="ck_group_scope_valid",
        ),
        sa.CheckConstraint(
            "group_type IS NULL OR group_type IN ('security', 'distribution', 'unknown')",
            name="ck_group_type_valid",
        ),
        sa.CheckConstraint(
            "principal_kind <> 'unresolved' OR display_name IS NULL",
            name="ck_principals_unresolved_has_no_display_name",
        ),
        sa.CheckConstraint(
            "principal_kind IN ('user', 'domain_group', 'local_group', 'computer', "
            "'managed_service_account', 'well_known', 'foreign_security_principal', 'unresolved')",
            name="ck_principal_kind_valid",
        ),
        sa.CheckConstraint(
            "unresolved_reason IS NULL OR unresolved_reason IN ('deleted', 'untrusted_domain', "
            "'lookup_failed', 'unknown')",
            name="ck_unresolved_reason_valid",
        ),
        sa.PrimaryKeyConstraint("principal_key"),
        comment="Latest known state of every principal a collector has reported.",
    )
    op.create_index("ix_principals_domain_sid", "principals", ["domain_sid"], unique=False)
    op.create_index("ix_principals_kind", "principals", ["principal_kind"], unique=False)
    op.create_index(
        "ix_principals_last_observed_run", "principals", ["last_observed_run_id"], unique=False
    )
    op.create_index("ix_principals_sid", "principals", ["sid"], unique=False)
    op.create_table(
        "principal_aliases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("principal_key", sa.String(length=512), nullable=False),
        sa.Column("alias_kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_folded", sa.Text(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "alias_kind IN ('display_name', 'sam_account_name', 'user_principal_name', "
            "'distinguished_name', 'last_known_name')",
            name="ck_alias_kind_valid",
        ),
        sa.ForeignKeyConstraint(
            ["principal_key"], ["principals.principal_key"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "principal_key", "alias_kind", "value_folded", name="uq_principal_aliases_identity"
        ),
        comment="Every name-like value ever observed for a principal. Metadata, never identity.",
    )
    op.create_index(
        "ix_principal_aliases_value_folded", "principal_aliases", ["value_folded"], unique=False
    )
    op.create_table(
        "scan_runs",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("incremental", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("batch_count_reported", sa.Integer(), nullable=True),
        sa.Column("batch_count_received", sa.Integer(), server_default="0", nullable=False),
        sa.Column("observation_count_reported", sa.Integer(), nullable=True),
        sa.Column("observation_count_applied", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("downgrade_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status <> 'succeeded' OR error_count = 0", name="ck_scan_runs_succeeded_has_no_errors"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'partial', 'failed', 'canceled')",
            name="ck_status_valid",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_scan_runs_completed_after_started",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["collector_sources.id"],
        ),
        sa.PrimaryKeyConstraint("run_id"),
        comment="One execution of one collector. Every observation belongs to exactly one.",
    )
    op.create_index("ix_scan_runs_started_at", "scan_runs", ["started_at"], unique=False)
    op.create_index("ix_scan_runs_status", "scan_runs", ["status"], unique=False)
    op.create_table(
        "scan_run_batches",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("is_final", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("continuation_token", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["scan_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "batch_id"),
        comment="Batches already applied to a run. Presence of a row makes a replay a no-op.",
    )
    op.create_table(
        "scan_run_errors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["scan_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Failures a collector reported. An unreadable object is a fact, not a gap.",
    )
    op.create_index("ix_scan_run_errors_run_id", "scan_run_errors", ["run_id"], unique=False)
    op.create_table(
        "scan_run_scopes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("declared", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("reconciled", sa.Boolean(), server_default="false", nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('domain', 'server', 'share', 'directory_tree', 'local_groups_host')",
            name="ck_scope_kind_valid",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["scan_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "scope_kind", "scope_key", name="uq_scan_run_scopes_identity"
        ),
        comment="What a run claimed to enumerate, and what it ultimately reconciled.",
    )
    op.create_table(
        "observations",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_key", sa.String(length=512), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('principal', 'membership_edge', 'server', 'smb_share', 'smb_ace', "
            "'ntfs_resource', 'ntfs_ace')",
            name="ck_kind_valid",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "batch_id"],
            ["scan_run_batches.run_id", "scan_run_batches.batch_id"],
            name="fk_observations_batch",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["scan_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "source_key"),
        comment="Provenance: which run saw which object, when, in which batch.",
    )
    op.create_index("ix_observations_kind", "observations", ["kind"], unique=False)
    op.create_index(
        "ix_observations_subject", "observations", ["subject_key", "observed_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_observations_subject", table_name="observations")
    op.drop_index("ix_observations_kind", table_name="observations")
    op.drop_table("observations")
    op.drop_table("scan_run_scopes")
    op.drop_index("ix_scan_run_errors_run_id", table_name="scan_run_errors")
    op.drop_table("scan_run_errors")
    op.drop_table("scan_run_batches")
    op.drop_index("ix_scan_runs_status", table_name="scan_runs")
    op.drop_index("ix_scan_runs_started_at", table_name="scan_runs")
    op.drop_table("scan_runs")
    op.drop_index("ix_principal_aliases_value_folded", table_name="principal_aliases")
    op.drop_table("principal_aliases")
    op.drop_index("ix_principals_sid", table_name="principals")
    op.drop_index("ix_principals_last_observed_run", table_name="principals")
    op.drop_index("ix_principals_kind", table_name="principals")
    op.drop_index("ix_principals_domain_sid", table_name="principals")
    op.drop_table("principals")
    op.drop_index("ix_membership_edges_member", table_name="membership_edges")
    op.drop_index("ix_membership_edges_last_observed_run", table_name="membership_edges")
    op.drop_index("ix_membership_edges_group", table_name="membership_edges")
    op.drop_table("membership_edges")
    op.drop_table("collector_sources")
