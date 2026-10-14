r"""Relationship derivation: support retirement, the conditions it states, and a pass log.

ADG-GRAPH-01B adds the projector that derives privilege relationships from stored facts. Two
things it must say were not representable in revision 0018, and one it must record:

* **Retirement.** 0018 computes a support's currency from its premises' presence alone. That
  is exact for a fact a reconciliation tombstones, and blind to a fact that is still stored
  and now *says something else*: an NTFS owner changed in place, a Deny appeared ahead of a
  grant. The support would read current for ever. ``retired_at`` and ``retired_basis_token``
  record that a derivation read those facts, as stored, and no longer produced the support.
  Both or neither, and the basis token has the shape every other one has.
* **Conditions.** Six values join the closed vocabulary a conditional support names
  (``deny_may_apply``, ``acl_entries_incomplete``, ``acl_order_unknown``,
  ``logon_right_unevaluated``, ``service_access_policy_unevaluated``,
  ``remote_service_state_unknown``). The check constraint is replaced by one listing all
  sixteen; every row 0018 could hold still satisfies it.
* **``graph_derivation_passes``.** One row per projector pass, including a failed one, so
  whether the graph has been derived from everything collected is a question about rows rather
  than an assumption.

## Nothing else changes

Two nullable columns are added, NULL on every existing row; one check constraint is widened;
one table is added, empty. No row is rewritten and nothing is backfilled: derivation is the
projector's job, run by ``python -m app.operations rebuild-graph``.

``downgrade`` restores 0018 exactly. It must first remove the derived rows 0018 cannot
represent - supports that are retired (0018 would read them as current again, which is the
one thing it must not do) and supports naming a condition 0018 does not know - and then any
relationship left with no support. That loses derived state only, which ADR-0040 makes
re-derivable by definition; no collected fact is touched.

The value lists are literals, as in every revision: a migration keeps doing what it did on the
day it ran. ``tests/db/test_graph_relationships.py`` pins them equal to the domain vocabulary.

Revision ID: 0019_graph_derivation
Revises: 0018_graph_relationships
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_graph_derivation"
down_revision: str | None = "0018_graph_relationships"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASIS_TOKEN_LENGTH = 32

CONDITIONS_0018 = (
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
ADDED_CONDITIONS = (
    "acl_entries_incomplete",
    "acl_order_unknown",
    "deny_may_apply",
    "logon_right_unevaluated",
    "remote_service_state_unknown",
    "service_access_policy_unevaluated",
)
CONDITIONS = tuple(sorted(CONDITIONS_0018 + ADDED_CONDITIONS))
PROJECTION_TRIGGERS = ("rebuild", "run_completion")
PROJECTION_STATUSES = ("failed", "succeeded")
PASS_COUNTS = (
    "units_examined",
    "units_skipped",
    "relationships_created",
    "supports_created",
    "supports_extended",
    "supports_rederived",
    "supports_backdated",
    "supports_replayed",
    "supports_stale",
    "supports_reinstated",
    "supports_retired",
)

_SUPPORTS = "graph_relationship_supports"
_CONDITIONS_CHECK = "ck_graph_relationship_supports_conditions_valid"


def _values(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


def _conditions_predicate(values: Iterable[str]) -> str:
    return f"conditions <@ ARRAY[{_values(values)}]::text[]"


def _enum_check(column: str, values: Iterable[str]) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} IN ({_values(values)})", name=f"ck_{column}_valid")


def upgrade() -> None:
    op.add_column(_SUPPORTS, sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        _SUPPORTS, sa.Column("retired_basis_token", sa.String(BASIS_TOKEN_LENGTH), nullable=True)
    )
    op.create_check_constraint(
        "ck_graph_relationship_supports_retirement_is_whole",
        _SUPPORTS,
        "(retired_at IS NULL) = (retired_basis_token IS NULL)",
    )
    op.create_check_constraint(
        "ck_graph_relationship_supports_retired_basis_token_shape",
        _SUPPORTS,
        "retired_basis_token IS NULL OR "
        f"retired_basis_token ~ '^[0-9a-f]{{{BASIS_TOKEN_LENGTH}}}$'",
    )
    op.drop_constraint(_CONDITIONS_CHECK, _SUPPORTS, type_="check")
    op.create_check_constraint(_CONDITIONS_CHECK, _SUPPORTS, _conditions_predicate(CONDITIONS))

    op.create_table(
        "graph_derivation_passes",
        sa.Column("pass_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scan_runs.run_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("basis_token", sa.String(BASIS_TOKEN_LENGTH), nullable=False),
        sa.Column("rules", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        *(
            sa.Column(name, sa.Integer(), nullable=False, server_default="0")
            for name in PASS_COUNTS
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        _enum_check("trigger", PROJECTION_TRIGGERS),
        _enum_check("status", PROJECTION_STATUSES),
        sa.CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)",
            name="ck_graph_derivation_passes_failure_has_error",
        ),
        sa.CheckConstraint(
            f"basis_token ~ '^[0-9a-f]{{{BASIS_TOKEN_LENGTH}}}$'",
            name="ck_graph_derivation_passes_basis_token_shape",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name="ck_graph_derivation_passes_completed_after_started",
        ),
        sa.CheckConstraint(
            f"least({', '.join(PASS_COUNTS)}) >= 0",
            name="ck_graph_derivation_passes_counts_non_negative",
        ),
        comment=(
            "Each projector pass that derived privilege relationships: what started it, the "
            "collection basis it read, the rules it ran and what it changed. A failed pass "
            "changed nothing and is recorded anyway."
        ),
    )
    op.create_index(
        "ix_graph_derivation_passes_source_run", "graph_derivation_passes", ["source_run_id"]
    )
    op.create_index(
        "ix_graph_derivation_passes_completed_at", "graph_derivation_passes", ["completed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_graph_derivation_passes_completed_at", table_name="graph_derivation_passes")
    op.drop_index("ix_graph_derivation_passes_source_run", table_name="graph_derivation_passes")
    op.drop_table("graph_derivation_passes")

    # Derived rows 0018 cannot represent. A retired support would read as current again under
    # 0018's presence-only currency, and a support naming a 0019 condition violates 0018's
    # check. Evidence goes with its support (ON DELETE CASCADE); a relationship left with no
    # support has no meaning and goes too.
    op.execute(
        f"DELETE FROM {_SUPPORTS} WHERE retired_at IS NOT NULL "
        f"OR NOT ({_conditions_predicate(CONDITIONS_0018)})"
    )
    op.execute(
        "DELETE FROM graph_relationships r WHERE NOT EXISTS ("
        f"SELECT 1 FROM {_SUPPORTS} s WHERE s.relationship_id = r.relationship_id)"
    )
    op.drop_constraint(_CONDITIONS_CHECK, _SUPPORTS, type_="check")
    op.create_check_constraint(_CONDITIONS_CHECK, _SUPPORTS, _conditions_predicate(CONDITIONS_0018))
    op.drop_constraint(
        "ck_graph_relationship_supports_retired_basis_token_shape", _SUPPORTS, type_="check"
    )
    op.drop_constraint(
        "ck_graph_relationship_supports_retirement_is_whole", _SUPPORTS, type_="check"
    )
    op.drop_column(_SUPPORTS, "retired_basis_token")
    op.drop_column(_SUPPORTS, "retired_at")
