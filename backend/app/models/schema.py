"""Physical schema for AD principals, membership edges, and their provenance.

This module is the single description of the tables; the Alembic revision under
``database/migrations/versions`` creates exactly what is declared here, and a smoke test
reflects the live database and compares the two so the pair cannot drift.

Three shaping decisions are worth stating up front, because the rest of the phase depends
on them.

**Identity is a key string, not a surrogate id.** ``principals.principal_key`` is
:attr:`app.domain.Principal.identity_key` — a SID, or ``host|sid`` for a local group — and
``membership_edges.group_key``/``member_key`` are the matching values from
:class:`app.domain.MembershipEdge`. Traversal therefore joins on exactly the strings the
domain layer produces, and a BUILTIN SID observed on two servers can never merge into one
node. See ADR-0001 and ADR-0002.

**Enumerated columns are ``text`` with a check constraint**, generated from the domain
enums. A PostgreSQL ``enum`` type would make adding a value a migration with a lock, and the
authoritative list already lives in :mod:`app.domain`.

**Current state and provenance are separate tables.** ``principals`` and
``membership_edges`` hold the latest known state of each object; ``observations`` holds one
row per ``(run_id, source_key)``, which is what makes re-ingesting a batch a no-op and what
keeps "which run saw this, and when" answerable before Phase 7 adds full history.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from app.contracts.v1.common import ObservationKind, ScopeKind
from app.domain import (
    GroupScope,
    GroupType,
    MembershipEdgeKind,
    PrincipalKind,
    ScanStatus,
    UnresolvedReason,
)
from app.domain.observation import CollectorKind

metadata = MetaData()

# Key columns are long because a UNC path or a host-scoped SID can be: the derivations in
# app/contracts/v1/keys.py cap at this length, and the contract models enforce it.
KEY_LENGTH = 512

__all__ = [
    "KEY_LENGTH",
    "AliasKind",
    "collector_sources",
    "membership_edges",
    "metadata",
    "observations",
    "principal_aliases",
    "principals",
    "scan_run_batches",
    "scan_run_errors",
    "scan_run_scopes",
    "scan_runs",
]


class AliasKind(StrEnum):
    """Which name-like attribute an alias row records.

    Names are metadata (ADR-0001), but they are the metadata an operator searches by, and
    a name that changed is itself a finding. Keeping every observed value — rather than
    only the newest — costs one narrow table and preserves "this SID used to be called
    ``svc-backup``".
    """

    DISPLAY_NAME = "display_name"
    SAM_ACCOUNT_NAME = "sam_account_name"
    USER_PRINCIPAL_NAME = "user_principal_name"
    DISTINGUISHED_NAME = "distinguished_name"
    LAST_KNOWN_NAME = "last_known_name"


def _enum_check(column: str, enum: type[StrEnum], *, nullable: bool = False) -> CheckConstraint:
    """A check constraint pinning ``column`` to the domain enum's values."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    predicate = f"{column} IN ({values})"
    if nullable:
        predicate = f"{column} IS NULL OR {predicate}"
    return CheckConstraint(predicate, name=f"ck_{column}_valid")


def _timestamp(name: str, *, nullable: bool = False) -> Column[Any]:
    """A timezone-aware timestamp.

    ``timestamptz`` everywhere, without exception: a naive timestamp from a collector in
    another time zone would reorder history silently (see :mod:`app.domain.observation`).
    """
    return Column(name, DateTime(timezone=True), nullable=nullable)


collector_sources = Table(
    "collector_sources",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    # sha256 of the five identifying fields. A hash rather than the concatenation because
    # target may be a 1024-character UNC path, which overflows a btree index row.
    Column("fingerprint", String(64), nullable=False, unique=True),
    Column("collector", Text, nullable=False),
    Column("collector_host", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("collector_version", Text, nullable=True),
    Column("target", Text, nullable=True),
    _timestamp("first_seen_at"),
    _timestamp("last_seen_at"),
    _enum_check("collector", CollectorKind),
    comment="Distinct (collector, host, method, version, target) tuples that reported facts.",
)


scan_runs = Table(
    "scan_runs",
    metadata,
    # The collector generates run_id and reuses it on retry; that is what makes starting a
    # run idempotent, so it is the primary key rather than a surrogate.
    Column("run_id", PgUUID(as_uuid=True), primary_key=True),
    Column("source_id", BigInteger, ForeignKey("collector_sources.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("incremental", Boolean, nullable=False, server_default="false"),
    _timestamp("started_at"),
    _timestamp("completed_at", nullable=True),
    Column("batch_count_reported", Integer, nullable=True),
    Column("batch_count_received", Integer, nullable=False, server_default="0"),
    Column("observation_count_reported", Integer, nullable=True),
    Column("observation_count_applied", Integer, nullable=False, server_default="0"),
    Column("error_count", Integer, nullable=False, server_default="0"),
    Column("notes", Text, nullable=True),
    # Set when a completion claimed a status the server could not corroborate — fewer
    # batches received than sent, for instance. Downgrading is recorded, never silent.
    Column("downgrade_reason", Text, nullable=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("status", ScanStatus),
    CheckConstraint(
        "completed_at IS NULL OR completed_at >= started_at",
        name="ck_scan_runs_completed_after_started",
    ),
    CheckConstraint(
        "status <> 'succeeded' OR error_count = 0",
        name="ck_scan_runs_succeeded_has_no_errors",
    ),
    Index("ix_scan_runs_started_at", "started_at"),
    Index("ix_scan_runs_status", "status"),
    comment="One execution of one collector. Every observation belongs to exactly one.",
)


scan_run_scopes = Table(
    "scan_run_scopes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        PgUUID(as_uuid=True),
        ForeignKey("scan_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("scope_kind", Text, nullable=False),
    Column("scope_key", Text, nullable=False),
    Column("declared", Boolean, nullable=False, server_default="true"),
    # Absence may be inferred only inside a reconciled scope, and only Phase 7 acts on it.
    # Recording the flag now means the later phase has the evidence it needs.
    Column("reconciled", Boolean, nullable=False, server_default="false"),
    UniqueConstraint("run_id", "scope_kind", "scope_key", name="uq_scan_run_scopes_identity"),
    _enum_check("scope_kind", ScopeKind),
    comment="What a run claimed to enumerate, and what it ultimately reconciled.",
)


scan_run_batches = Table(
    "scan_run_batches",
    metadata,
    # (run_id, batch_id) is the batch idempotency key from the collector protocol: the
    # primary key *is* the guarantee that a retried batch cannot be applied twice.
    Column(
        "run_id",
        PgUUID(as_uuid=True),
        ForeignKey("scan_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("batch_id", PgUUID(as_uuid=True), primary_key=True),
    Column("sequence", Integer, nullable=False),
    Column("is_final", Boolean, nullable=False, server_default="false"),
    Column("observation_count", Integer, nullable=False),
    # Opaque collector-side cursor. Stored for diagnostics; never interpreted.
    Column("continuation_token", Text, nullable=True),
    _timestamp("applied_at"),
    comment="Batches already applied to a run. Presence of a row makes a replay a no-op.",
)


scan_run_errors = Table(
    "scan_run_errors",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        PgUUID(as_uuid=True),
        ForeignKey("scan_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("code", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("target", Text, nullable=True),
    _timestamp("occurred_at", nullable=True),
    Index("ix_scan_run_errors_run_id", "run_id"),
    comment="Failures a collector reported. An unreadable object is a fact, not a gap.",
)


principals = Table(
    "principals",
    metadata,
    # SID, or 'host|sid' for a local group. Identical to Principal.identity_key, which is
    # also what membership_edges.group_key / member_key contain, so traversal is a join on
    # this column and nothing has to be re-derived at query time.
    Column("principal_key", String(KEY_LENGTH), primary_key=True),
    Column("sid", String(200), nullable=False),
    Column("principal_kind", Text, nullable=False),
    # Non-null exactly for local groups; a BUILTIN SID means nothing without its host.
    Column("host_key", Text, nullable=True),
    Column("domain_sid", String(200), nullable=True),
    Column("display_name", Text, nullable=True),
    Column("sam_account_name", Text, nullable=True),
    Column("user_principal_name", Text, nullable=True),
    Column("distinguished_name", Text, nullable=True),
    Column("group_scope", Text, nullable=True),
    Column("group_type", Text, nullable=True),
    Column("enabled", Boolean, nullable=True),
    Column("is_deleted", Boolean, nullable=False, server_default="false"),
    Column("unresolved_reason", Text, nullable=True),
    Column("last_known_name", Text, nullable=True),
    Column("source_key", String(KEY_LENGTH), nullable=False),
    _timestamp("first_observed_at"),
    Column("first_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("last_observed_at"),
    Column("last_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("principal_kind", PrincipalKind),
    _enum_check("group_scope", GroupScope, nullable=True),
    _enum_check("group_type", GroupType, nullable=True),
    _enum_check("unresolved_reason", UnresolvedReason, nullable=True),
    CheckConstraint(
        "(principal_kind = 'local_group') = (host_key IS NOT NULL)",
        name="ck_principals_local_group_has_host",
    ),
    CheckConstraint(
        "principal_kind <> 'unresolved' OR display_name IS NULL",
        name="ck_principals_unresolved_has_no_display_name",
    ),
    # The SID is what an ACE carries, so looking a principal up by SID is the hot path.
    Index("ix_principals_sid", "sid"),
    Index("ix_principals_domain_sid", "domain_sid"),
    Index("ix_principals_kind", "principal_kind"),
    Index("ix_principals_last_observed_run", "last_observed_run_id"),
    comment="Latest known state of every principal a collector has reported.",
)


principal_aliases = Table(
    "principal_aliases",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "principal_key",
        String(KEY_LENGTH),
        ForeignKey("principals.principal_key", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("alias_kind", Text, nullable=False),
    Column("value", Text, nullable=False),
    # Windows name comparisons are case-insensitive; the folded form is the uniqueness key
    # so that "Finance-RW" and "finance-rw" do not become two aliases.
    Column("value_folded", Text, nullable=False),
    _timestamp("first_observed_at"),
    _timestamp("last_observed_at"),
    UniqueConstraint(
        "principal_key", "alias_kind", "value_folded", name="uq_principal_aliases_identity"
    ),
    _enum_check("alias_kind", AliasKind),
    Index("ix_principal_aliases_value_folded", "value_folded"),
    comment="Every name-like value ever observed for a principal. Metadata, never identity.",
)


membership_edges = Table(
    "membership_edges",
    metadata,
    # MembershipEdge.identity_key: '<group_key>-><member_key>|<kind>'. One row per observed
    # direct relationship — never an expanded closure (ADR-0002).
    Column("edge_key", String(KEY_LENGTH), primary_key=True),
    Column("group_key", String(KEY_LENGTH), nullable=False),
    Column("member_key", String(KEY_LENGTH), nullable=False),
    Column("group_sid", String(200), nullable=False),
    Column("member_sid", String(200), nullable=False),
    Column("edge_kind", Text, nullable=False),
    Column("host_key", Text, nullable=True),
    # What the collector said the member is. Advisory: the principals row wins when present,
    # but this is the only kind available for a member no run has described yet.
    Column("member_kind", Text, nullable=True),
    Column("is_foreign_security_principal", Boolean, nullable=False, server_default="false"),
    Column("source_key", String(KEY_LENGTH), nullable=False),
    _timestamp("first_observed_at"),
    Column("first_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("last_observed_at"),
    Column("last_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("edge_kind", MembershipEdgeKind),
    _enum_check("member_kind", PrincipalKind, nullable=True),
    CheckConstraint("group_key <> member_key", name="ck_membership_edges_no_self_edge"),
    CheckConstraint(
        "(edge_kind = 'local_group_member') = (host_key IS NOT NULL)",
        name="ck_membership_edges_local_has_host",
    ),
    # Traversal walks the graph in both directions — "who is in this group" and "which
    # groups contain this principal" — so both endpoints are indexed. The trailing column
    # makes each index cover the keyset pagination order as well.
    Index("ix_membership_edges_group", "group_key", "member_key"),
    Index("ix_membership_edges_member", "member_key", "group_key"),
    Index("ix_membership_edges_last_observed_run", "last_observed_run_id"),
    comment="Directed membership edges. member_key is a member of group_key.",
)


observations = Table(
    "observations",
    metadata,
    # The contract's idempotency key, as the primary key. Re-sending an observation in the
    # same run is a conflict the upsert absorbs; it can never create a second row.
    Column(
        "run_id",
        PgUUID(as_uuid=True),
        ForeignKey("scan_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("source_key", String(KEY_LENGTH), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("batch_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("observed_at"),
    # principal_key or edge_key: which stored object this observation is evidence for.
    Column("subject_key", String(KEY_LENGTH), nullable=False),
    _timestamp("recorded_at"),
    ForeignKeyConstraint(
        ["run_id", "batch_id"],
        ["scan_run_batches.run_id", "scan_run_batches.batch_id"],
        ondelete="CASCADE",
        name="fk_observations_batch",
    ),
    _enum_check("kind", ObservationKind),
    Index("ix_observations_subject", "subject_key", "observed_at"),
    Index("ix_observations_kind", "kind"),
    comment="Provenance: which run saw which object, when, in which batch.",
)
