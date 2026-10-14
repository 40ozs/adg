r"""The privilege relationship graph: identity, supports and evidence (ADG-GRAPH-01A).

Three tables of **derived state** (ADR-0003, ADR-0040). A relationship names the canonical
objects it connects by the keys their own tables already use, and every support names the
stored facts it rests on by theirs; nothing collected is copied here, and everything here can
be dropped and re-derived without losing an observation.

## Nothing else changes

Three tables added, empty on every existing database. No existing column, constraint, index
or row is touched, and nothing is backfilled: deriving relationships is a projector's job
(ADG-GRAPH-01B), not a migration's, and a migration that derived them would freeze one rule
version into the schema history. ``downgrade`` drops only what ``upgrade`` created, and loses
only derived state.

The value lists below are literals rather than imports, as in every revision here: a
migration must keep doing what it did on the day it ran. ``tests/db/test_graph_relationships.py``
pins them equal to the domain vocabulary they were written from.

Revision ID: 0018_graph_relationships
Revises: 0017_deployment_marker
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_graph_relationships"
down_revision: str | None = "0017_deployment_marker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY_LENGTH = 512
DIGEST_LENGTH = 64
BASIS_TOKEN_LENGTH = 32

STORABLE_RELATIONSHIP_TYPES = (
    "can_modify_membership",
    "controls_gpo",
    "foreign_principal_of",
    "generic_all",
    "generic_write",
    "gpo_applies_to",
    "gpo_linked_to",
    "local_admin_of",
    "local_group_member_of",
    "member_of",
    "owns",
    "primary_group_of",
    "remote_desktop_user_of",
    "remote_management_user_of",
    "trusts",
    "write_dacl",
    "write_owner",
)
NO_SELF_RELATIONSHIP_TYPES = (
    "controls_gpo",
    "foreign_principal_of",
    "gpo_applies_to",
    "gpo_linked_to",
    "local_admin_of",
    "local_group_member_of",
    "member_of",
    "primary_group_of",
    "remote_desktop_user_of",
    "remote_management_user_of",
    "trusts",
)
NODE_KINDS = (
    "directory_container",
    "domain",
    "gpo",
    "ntfs_resource",
    "principal",
    "server",
    "smb_share",
)
SCOPE_KINDS = ("host",)
DERIVATION_CLASSES = (
    "conditionally_derived",
    "deterministically_derived",
    "observed",
    "unresolved",
)
QUALIFIERS = (
    "derivation_truncated",
    "endpoint_unresolved_at_collection",
    "evidence_backfilled",
    "evidence_from_partial_run",
    "foreign_principal",
)
UNRESOLVED_QUALIFIERS = ("endpoint_unresolved_at_collection", "foreign_principal")
CONDITIONS = (
    "gpo_inheritance_blocking_unevaluated",
    "gpo_link_state_unknown",
    "gpo_security_filtering_unevaluated",
    "gpo_wmi_filter_unevaluated",
    "inheritance_unresolved",
    "no_deny_evaluated",
    "object_type_scope_unevaluated",
    "token_filtering_not_modeled",
    "trust_selective_authentication_unknown",
    "trust_sid_filtering_unknown",
)
EVIDENCE_KINDS = (
    "membership_edge",
    "ntfs_ace",
    "ntfs_resource",
    "principal",
    "server",
    "smb_ace",
    "smb_share",
)
EVIDENCE_ROLES = ("context", "premise")

RELATIONSHIP_ID_SQL = (
    "encode(sha256(convert_to(concat_ws(chr(31), relationship_type, source_kind, source_key, "
    "target_kind, target_key, coalesce(scope_kind, ''), coalesce(scope_key, '')), 'UTF8')), "
    "'hex')"
)


def _values(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


def _in(column: str, values: Iterable[str], *, nullable: bool = False) -> sa.CheckConstraint:
    predicate = f"{column} IN ({_values(values)})"
    if nullable:
        predicate = f"{column} IS NULL OR {predicate}"
    return sa.CheckConstraint(predicate, name=f"ck_{column}_valid")


def _timestamp(name: str) -> sa.Column[sa.DateTime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def _run(name: str) -> sa.Column[sa.Uuid[object]]:
    return sa.Column(
        name, postgresql.UUID(as_uuid=True), sa.ForeignKey("scan_runs.run_id"), nullable=False
    )


def _host_local(end: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        # coalesce: with no scope the comparison is NULL, and a CHECK passes on NULL.
        f"{end}_kind <> 'principal' OR strpos({end}_key, '|') = 0 OR "
        f"coalesce(scope_kind = 'host' AND strpos({end}_key, scope_key || '|') = 1, false)",
        name=f"ck_graph_relationships_{end}_host_matches_scope",
    )


def upgrade() -> None:
    op.create_table(
        "graph_relationships",
        sa.Column("relationship_id", sa.String(DIGEST_LENGTH), primary_key=True),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("scope_kind", sa.Text(), nullable=True),
        sa.Column("scope_key", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            f"relationship_type IN ({_values(STORABLE_RELATIONSHIP_TYPES)})",
            name="ck_graph_relationships_type_is_storable",
        ),
        _in("source_kind", NODE_KINDS),
        _in("target_kind", NODE_KINDS),
        _in("scope_kind", SCOPE_KINDS, nullable=True),
        sa.CheckConstraint(
            "(scope_kind IS NULL) = (scope_key IS NULL)",
            name="ck_graph_relationships_scope_is_whole",
        ),
        sa.CheckConstraint(
            f"relationship_id ~ '^[0-9a-f]{{{DIGEST_LENGTH}}}$'",
            name="ck_graph_relationships_id_shape",
        ),
        sa.CheckConstraint(
            f"relationship_id = {RELATIONSHIP_ID_SQL}",
            name="ck_graph_relationships_id_is_digest",
        ),
        sa.CheckConstraint(
            f"relationship_type NOT IN ({_values(NO_SELF_RELATIONSHIP_TYPES)}) "
            "OR source_kind <> target_kind OR source_key <> target_key",
            name="ck_graph_relationships_no_self_edge",
        ),
        _host_local("source"),
        _host_local("target"),
        comment=(
            "Derived privilege relationships between canonical ADG objects: identity only. "
            "Rebuildable from collected facts; never a fact itself (ADR-0040)."
        ),
    )
    op.create_index(
        "ix_graph_relationships_source",
        "graph_relationships",
        ["source_kind", "source_key", "relationship_type", "relationship_id"],
    )
    op.create_index(
        "ix_graph_relationships_target",
        "graph_relationships",
        ["target_kind", "target_key", "relationship_type", "relationship_id"],
    )
    op.create_index(
        "ix_graph_relationships_scope",
        "graph_relationships",
        ["scope_kind", "scope_key", "relationship_id"],
        postgresql_where=sa.text("scope_key IS NOT NULL"),
    )
    op.create_index(
        "ix_graph_relationships_type",
        "graph_relationships",
        ["relationship_type", "relationship_id"],
    )

    op.create_table(
        "graph_relationship_supports",
        sa.Column(
            "relationship_id",
            sa.String(DIGEST_LENGTH),
            sa.ForeignKey("graph_relationships.relationship_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("support_key", sa.String(DIGEST_LENGTH), primary_key=True),
        sa.Column("derivation_class", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("qualifiers", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("conditions", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("basis_token", sa.String(BASIS_TOKEN_LENGTH), nullable=False),
        _timestamp("derived_at"),
        _timestamp("first_observed_at"),
        _run("first_observed_run_id"),
        _timestamp("last_observed_at"),
        _run("last_observed_run_id"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _in("derivation_class", DERIVATION_CLASSES),
        sa.CheckConstraint(
            f"qualifiers <@ ARRAY[{_values(QUALIFIERS)}]::text[]",
            name="ck_graph_relationship_supports_qualifiers_valid",
        ),
        sa.CheckConstraint(
            f"conditions <@ ARRAY[{_values(CONDITIONS)}]::text[]",
            name="ck_graph_relationship_supports_conditions_valid",
        ),
        sa.CheckConstraint(
            "(derivation_class = 'conditionally_derived') = (cardinality(conditions) > 0)",
            name="ck_graph_relationship_supports_conditional_has_conditions",
        ),
        sa.CheckConstraint(
            "derivation_class <> 'unresolved' OR qualifiers && "
            f"ARRAY[{_values(UNRESOLVED_QUALIFIERS)}]::text[]",
            name="ck_graph_relationship_supports_unresolved_names_why",
        ),
        sa.CheckConstraint(
            "rule_id ~ '^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)*$'",
            name="ck_graph_relationship_supports_rule_id_shape",
        ),
        sa.CheckConstraint("rule_version >= 1", name="ck_graph_relationship_supports_rule_version"),
        sa.CheckConstraint(
            "jsonb_typeof(attributes) = 'object'",
            name="ck_graph_relationship_supports_attributes_object",
        ),
        sa.CheckConstraint(
            f"support_key ~ '^[0-9a-f]{{{DIGEST_LENGTH}}}$'",
            name="ck_graph_relationship_supports_key_shape",
        ),
        sa.CheckConstraint(
            f"basis_token ~ '^[0-9a-f]{{{BASIS_TOKEN_LENGTH}}}$'",
            name="ck_graph_relationship_supports_basis_token_shape",
        ),
        sa.CheckConstraint(
            "last_observed_at >= first_observed_at",
            name="ck_graph_relationship_supports_window",
        ),
        comment=(
            "Each independent derivation of a relationship: class, rule, qualifiers, "
            "conditions, attribution and observation window. A relationship is as strong as "
            "its strongest current support."
        ),
    )
    op.create_index(
        "ix_graph_relationship_supports_last_observed_run",
        "graph_relationship_supports",
        ["last_observed_run_id"],
    )
    op.create_index(
        "ix_graph_relationship_supports_first_observed_run",
        "graph_relationship_supports",
        ["first_observed_run_id"],
    )

    op.create_table(
        "graph_relationship_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("relationship_id", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("support_key", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("evidence_kind", sa.Text(), nullable=False),
        sa.Column("evidence_key", sa.String(KEY_LENGTH), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        _timestamp("first_observed_at"),
        _run("first_observed_run_id"),
        _timestamp("last_observed_at"),
        _run("last_observed_run_id"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["relationship_id", "support_key"],
            [
                "graph_relationship_supports.relationship_id",
                "graph_relationship_supports.support_key",
            ],
            ondelete="CASCADE",
            name="fk_graph_relationship_evidence_support",
        ),
        sa.UniqueConstraint(
            "relationship_id",
            "support_key",
            "evidence_kind",
            "evidence_key",
            name="uq_graph_relationship_evidence_identity",
        ),
        _in("evidence_kind", EVIDENCE_KINDS),
        _in("role", EVIDENCE_ROLES),
        sa.CheckConstraint(
            "last_observed_at >= first_observed_at",
            name="ck_graph_relationship_evidence_window",
        ),
        comment=(
            "The stored facts each relationship support rests on, with the runs that observed "
            "them. A relationship is traceable to its observations through these rows."
        ),
    )
    op.create_index(
        "ix_graph_relationship_evidence_fact",
        "graph_relationship_evidence",
        ["evidence_kind", "evidence_key", "relationship_id"],
    )
    op.create_index(
        "ix_graph_relationship_evidence_last_observed_run",
        "graph_relationship_evidence",
        ["last_observed_run_id"],
    )
    op.create_index(
        "ix_graph_relationship_evidence_first_observed_run",
        "graph_relationship_evidence",
        ["first_observed_run_id"],
    )


def downgrade() -> None:
    # Derived state only: dropping it loses nothing a collector saw.
    op.drop_table("graph_relationship_evidence")
    op.drop_table("graph_relationship_supports")
    op.drop_table("graph_relationships")
