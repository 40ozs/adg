"""Physical schema for principals, membership edges, SMB and NTFS resources, and provenance.

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

**Current state and provenance are separate tables.** ``principals``,
``membership_edges``, ``servers``, ``smb_shares``, ``smb_share_aces``, ``ntfs_resources``,
and ``ntfs_aces`` hold the latest known state of each object; ``observations`` holds one row
per ``(run_id, source_key)``, which is what makes re-ingesting a batch a no-op and what
keeps "which run saw this, and when" answerable. Phase 7A added ``object_versions``
beside it, which holds the full timeline; ``observations`` stays, because "which run saw
this object" and "what state did it hold" are different questions and a version records
only the runs that opened and last confirmed it.

**Resource tables carry no foreign keys to each other.** A share whose server no run has
described, and an ACE whose share arrived in a later batch, are both real observations, and
a foreign key would reject them at exactly the moment a partial scan most needs to record
what it did manage to read. The keys are still the domain's own identity strings, so the
joins are exact; what is absent is reported as absent rather than refused on arrival —
the same shape ``membership_edges`` already has toward ``principals``.
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from app.contracts.v1.common import ObservationKind, ScopeKind
from app.domain import (
    ACL_HASH_LENGTH,
    AceSource,
    AceType,
    AclBoundaryReason,
    GroupScope,
    GroupType,
    MembershipEdgeKind,
    PrincipalKind,
    ResourceKind,
    ScanStatus,
    SharePermission,
    ShareType,
    UnresolvedReason,
)
from app.domain.observation import CollectorKind

metadata = MetaData()

# Key columns are long because a UNC path or a host-scoped SID can be: the derivations in
# app/contracts/v1/keys.py cap at this length, and the contract models enforce it.
KEY_LENGTH = 512

STATE_DIGEST_LENGTH = 64
"""Hex characters of the SHA-256 digest of a version's state. Full width, deliberately:
the digest is what decides whether an observation is a change or a repetition, and a
truncated one would eventually collapse two different ACLs into one version."""

MAX_ACCESS_MASK_VALUE = 0xFFFFFFFF
"""An access mask is an unsigned 32-bit value, which does not fit PostgreSQL's signed
``integer``. The columns holding one are ``bigint`` with a range check."""

__all__ = [
    "KEY_LENGTH",
    "MAX_ACCESS_MASK_VALUE",
    "STATE_DIGEST_LENGTH",
    "AliasKind",
    "CloseReason",
    "ReferenceKind",
    "VersionOrigin",
    "collector_sources",
    "membership_edges",
    "metadata",
    "ntfs_aces",
    "ntfs_resources",
    "object_versions",
    "observations",
    "principal_aliases",
    "principal_references",
    "principals",
    "scan_run_batches",
    "scan_run_errors",
    "scan_run_scopes",
    "scan_runs",
    "servers",
    "smb_share_aces",
    "smb_shares",
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


class ReferenceKind(StrEnum):
    """What kind of thing named a principal on an access-control list.

    A SID on an ACL is a reference, and a reference to a principal nothing has described is
    the orphaned-SID finding this tool exists to report. Recording the references in one
    table — rather than joining across every ACL table there will eventually be — is what
    keeps "which resources name this SID" a single indexed lookup as the NTFS layer arrives.
    """

    SMB_ACE = "smb_ace"
    NTFS_ACE = "ntfs_ace"


class VersionOrigin(StrEnum):
    """Where a row of ``object_versions`` came from, which decides what it may be claimed
    to prove.

    Declared here beside :class:`AliasKind` and :class:`ReferenceKind` rather than in
    :mod:`app.history.model`, for the same reason those two are: the values are part of the
    physical schema — they appear in a check constraint — and the check constraint has to be
    generated from the enum, which means the enum must be importable without importing
    anything that imports this module.
    """

    OBSERVED = "observed"
    """Opened by an observation. Its interval is bounded by instants collectors reported."""

    BACKFILLED = "backfilled"
    """Reconstructed by the Phase 7 migration from a pre-history row.

    The state is exactly what that row held and the interval is exactly what its two
    timestamps said. What is *not* known is whether the state changed and changed back
    inside that interval: the pre-history schema kept no evidence either way, and a
    backfilled version must never be read as though it had been watched.
    """


class CloseReason(StrEnum):
    """Why a version stopped being open.

    The distinction is the point of the column. ``superseded`` means the object is still
    there and something about it changed; ``absent`` means an authoritative scan looked
    inside the scope and did not find it. Collapsing the two would make "this share's ACL
    was tightened" and "this share was deleted" the same row.
    """

    SUPERSEDED = "superseded"
    ABSENT = "absent"


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
    # Absence may be inferred only inside a reconciled scope. Since Phase 7A this flag is
    # what a completion's closure pass acts on (see app/history/closure.py).
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


servers = Table(
    "servers",
    metadata,
    # Server.identity_key: the case-folded name the collector addressed the machine as.
    # Not the computer SID: a server is often reachable before anything has resolved its
    # SID, and a share ACL read from it is a fact whether or not that resolution happened.
    Column("server_key", String(KEY_LENGTH), primary_key=True),
    # Case-preserving, for display. Comparison always goes through server_key.
    Column("name", Text, nullable=False),
    Column("dns_host_name", Text, nullable=True),
    Column("netbios_name", Text, nullable=True),
    Column("computer_sid", String(200), nullable=True),
    Column("domain_sid", String(200), nullable=True),
    Column("is_domain_member", Boolean, nullable=True),
    Column("operating_system", Text, nullable=True),
    Column("source_key", String(KEY_LENGTH), nullable=False),
    _timestamp("first_observed_at"),
    Column("first_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("last_observed_at"),
    Column("last_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    # A DNS alias cannot be proven equivalent to a host name without resolving it, so two
    # names for one machine stay two rows until something proves otherwise. The computer
    # SID is indexed because it is the evidence that would prove it.
    Index("ix_servers_computer_sid", "computer_sid"),
    Index("ix_servers_last_observed_run", "last_observed_run_id"),
    comment="Windows computers that have reported shares, keyed by the name collected.",
)


smb_shares = Table(
    "smb_shares",
    metadata,
    # SmbShare.identity_key: '<server>|<share>', case-folded. The UNC path is exactly
    # \\<server>\<share> and is therefore derived, never stored: a second spelling of one
    # identity is a second thing that can disagree with the first.
    Column("share_key", String(KEY_LENGTH), primary_key=True),
    # Deliberately not a foreign key to servers.server_key. Batches may arrive in any
    # order, and a share ACL read from a machine no run has described as a server is still
    # a fact worth keeping -- exactly as membership_edges records an edge to a principal
    # nothing has described. Absence of the server row shows up as server: null in the API.
    Column("server_key", String(KEY_LENGTH), nullable=False),
    Column("name", Text, nullable=False),
    Column("local_path", Text, nullable=True),
    Column("share_type", Text, nullable=False),
    Column("description", Text, nullable=True),
    Column("concurrent_user_limit", Integer, nullable=True),
    Column("caching_mode", Text, nullable=True),
    # Contract 1.1. NULL means the source did not say, which is not the same as false:
    # an ordinary hidden share such as Data$ is hidden and not Special.
    Column("is_special", Boolean, nullable=True),
    Column("source_key", String(KEY_LENGTH), nullable=False),
    _timestamp("first_observed_at"),
    Column("first_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("last_observed_at"),
    Column("last_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("share_type", ShareType),
    CheckConstraint(
        "strpos(share_key, server_key || '|') = 1",
        name="ck_smb_shares_key_scoped_by_server",
    ),
    CheckConstraint(
        "concurrent_user_limit IS NULL OR concurrent_user_limit >= 0",
        name="ck_smb_shares_user_limit_non_negative",
    ),
    # Listing a server's shares is the hot path, and the trailing key makes the index cover
    # the keyset pagination order as well.
    Index("ix_smb_shares_server", "server_key", "share_key"),
    Index("ix_smb_shares_last_observed_run", "last_observed_run_id"),
    comment="SMB shares. A share is a publication of a directory, not the directory.",
)


smb_share_aces = Table(
    "smb_share_aces",
    metadata,
    # SmbShareAce.identity_key(share_key): '<share>|<trustee>|<type>|<right>'. order_index
    # is not part of it: reordering an ACL must not look like every entry being deleted
    # and recreated.
    Column("ace_key", String(KEY_LENGTH), primary_key=True),
    # No foreign key, for the same reason smb_shares.server_key has none: an ACL batch may
    # arrive before the batch describing the share, and an orphaned ACE is evidence.
    Column("share_key", String(KEY_LENGTH), nullable=False),
    Column("trustee_sid", String(200), nullable=False),
    # referenced_principal_key(trustee_sid, server): the principals row this ACE points at
    # if one exists. A BUILTIN trustee is host-scoped here because S-1-5-32-544 on FS01 is
    # a different group from S-1-5-32-544 on FS02. Whether the principal is *known* is a
    # join against principals, never a stored flag that could go stale the moment an AD
    # run resolves the SID.
    Column("trustee_key", String(KEY_LENGTH), nullable=False),
    Column("ace_type", Text, nullable=False),
    # bigint: an access mask is unsigned 32-bit and 0xFFFFFFFF overflows a signed integer.
    Column("access_mask", BigInteger, nullable=True),
    Column("permission", Text, nullable=True),
    # The right as reported -- 'change' or '0x001301bf' -- which is what the identity key
    # is built from. Stored so the key can be recomputed without re-deriving which form
    # the source used.
    Column("right_token", Text, nullable=False),
    # Position in the DACL as read. Recorded because canonical ordering is what makes a
    # Deny evaluable, but not part of identity.
    Column("order_index", Integer, nullable=True),
    Column("source_key", String(KEY_LENGTH), nullable=False),
    _timestamp("first_observed_at"),
    Column("first_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("last_observed_at"),
    Column("last_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("ace_type", AceType),
    _enum_check("permission", SharePermission, nullable=True),
    CheckConstraint(
        "(access_mask IS NULL) <> (permission IS NULL)",
        name="ck_smb_share_aces_exactly_one_right",
    ),
    CheckConstraint(
        f"access_mask IS NULL OR (access_mask >= 0 AND access_mask <= {MAX_ACCESS_MASK_VALUE})",
        name="ck_smb_share_aces_access_mask_range",
    ),
    CheckConstraint(
        "order_index IS NULL OR order_index >= 0",
        name="ck_smb_share_aces_order_index_non_negative",
    ),
    CheckConstraint(
        "strpos(ace_key, share_key || '|') = 1",
        name="ck_smb_share_aces_key_scoped_by_share",
    ),
    # Reading one share's ACL, and finding every share that names one trustee, are the two
    # questions this table exists to answer. Both get a covering index.
    Index("ix_smb_share_aces_share", "share_key", "ace_key"),
    Index("ix_smb_share_aces_trustee", "trustee_key", "share_key"),
    Index("ix_smb_share_aces_trustee_sid", "trustee_sid"),
    Index("ix_smb_share_aces_last_observed_run", "last_observed_run_id"),
    comment="Raw share-level ACEs, exactly as read. No effective access is derived here.",
)


ntfs_resources = Table(
    "ntfs_resources",
    metadata,
    # DirectoryResource.identity_key: the case-folded canonical UNC path. A local path is
    # not identity -- it names nothing without saying which server -- so it is recorded
    # beside the key rather than as it.
    Column("resource_key", String(KEY_LENGTH), primary_key=True),
    # Case-preserving, for display. Comparison always goes through resource_key.
    Column("path", Text, nullable=False),
    Column("server_key", String(KEY_LENGTH), nullable=False),
    # SmbShare.identity_key for the share this path sits under. This is the link that lets
    # one share show its raw SMB ACL and the raw NTFS ACL of its root as two separate
    # answers. No foreign key, for the reason every resource table here lacks one: an NTFS
    # run can read a root before any SMB run has described the share publishing it, and
    # that reading is still a fact.
    Column("share_key", String(KEY_LENGTH), nullable=False),
    Column("local_path", Text, nullable=True),
    Column("owner_sid", String(200), nullable=True),
    Column("group_sid", String(200), nullable=True),
    # SE_DACL_PRESENT. false is a NULL DACL: everyone has full access, and it is always a
    # finding. Not nullable and not defaulted, because "the collector did not say" must
    # never read as "a DACL was present".
    Column("dacl_present", Boolean, nullable=False),
    # SE_DACL_PROTECTED: this object blocks inheritance from its parent.
    Column("dacl_protected", Boolean, nullable=False),
    Column("inheritance_enabled", Boolean, nullable=False),
    Column("is_acl_boundary", Boolean, nullable=False),
    # The descriptor's own count, kept separate from the number of ntfs_aces rows stored.
    # A disagreement between the two means ACEs were lost in transit or never sent, which
    # is exactly the silent under-reporting an audit tool has to surface.
    Column("ace_count", Integer, nullable=False),
    Column("depth_from_share_root", Integer, nullable=True),
    # Directory unless an opt-in file scan reported otherwise (contract 1.3). Defaulted
    # rather than nullable: every row written before file scanning existed described a
    # directory, so there is a right answer for them and no need for a third state.
    Column(
        "resource_kind",
        String(32),
        nullable=False,
        server_default=ResourceKind.DIRECTORY.value,
    ),
    # Why this resource is a boundary (contract 1.3). NULL is not "unknown": it is the
    # only value that means "carrying exactly what it inherited". The unknown cases have
    # their own reasons -- parent_unreadable, parent_null_dacl, scan_root -- and all of
    # them set is_acl_boundary, because a boundary wrongly reported false tells a later
    # scan it may stop looking.
    Column("boundary_reason", String(32), nullable=True),
    # The collector's digest of the normalized DACL it read (contract 1.2). Stored as
    # reported and never rewritten from the stored ACEs: the point of keeping it is that
    # it can disagree with them.
    Column("acl_hash", String(ACL_HASH_LENGTH), nullable=True),
    # The parent's digest as the same run read it (contract 1.3). Not the value the
    # boundary verdict was compared against -- that is the parent's projection onto a
    # child, which the server recomputes from the parent's stored ACEs -- but the record
    # of WHICH reading of the parent was judged against, without which a disagreement
    # between collector and server cannot be told from the parent having simply changed
    # in between.
    Column("parent_acl_hash", String(ACL_HASH_LENGTH), nullable=True),
    Column("source_key", String(KEY_LENGTH), nullable=False),
    _timestamp("first_observed_at"),
    Column("first_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("last_observed_at"),
    Column("last_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    CheckConstraint(
        "dacl_present OR ace_count = 0", name="ck_ntfs_resources_null_dacl_has_no_aces"
    ),
    CheckConstraint(
        "inheritance_enabled OR is_acl_boundary",
        name="ck_ntfs_resources_blocked_inheritance_is_a_boundary",
    ),
    CheckConstraint("ace_count >= 0", name="ck_ntfs_resources_ace_count_non_negative"),
    CheckConstraint(
        "depth_from_share_root IS NULL OR depth_from_share_root >= 0",
        name="ck_ntfs_resources_depth_non_negative",
    ),
    CheckConstraint(
        f"acl_hash IS NULL OR acl_hash ~ '^[0-9a-f]{{{ACL_HASH_LENGTH}}}$'",
        name="ck_ntfs_resources_acl_hash_shape",
    ),
    CheckConstraint(
        f"parent_acl_hash IS NULL OR parent_acl_hash ~ '^[0-9a-f]{{{ACL_HASH_LENGTH}}}$'",
        name="ck_ntfs_resources_parent_acl_hash_shape",
    ),
    _enum_check("resource_kind", ResourceKind),
    _enum_check("boundary_reason", AclBoundaryReason, nullable=True),
    # A reason on a non-boundary contradicts itself and is refused at every contract
    # version. The converse is deliberately NOT constrained: a collector speaking 1.0
    # through 1.2 sets is_acl_boundary on a share root and has never heard of the reason
    # field, so a boundary with a NULL reason is a real row meaning "the collector that
    # wrote this predates the field" -- which the API reports as it stands rather than
    # backfilling a verdict nobody made.
    CheckConstraint(
        "boundary_reason IS NULL OR is_acl_boundary",
        name="ck_ntfs_resources_reason_implies_a_boundary",
    ),
    # Every directory under one share, and every one on one server, are the two ways this
    # table is walked. The trailing key makes each index cover keyset paging as well.
    Index("ix_ntfs_resources_share", "share_key", "resource_key"),
    Index("ix_ntfs_resources_server", "server_key", "resource_key"),
    # "Which directories carry this exact DACL" is what turns thousands of boundaries into
    # the few dozen distinct permission decisions behind them.
    Index("ix_ntfs_resources_acl_hash", "acl_hash"),
    # "Where do permissions change under this share" is the question a tree scan exists to
    # answer, and the one an auditor asks first. Partial, because the boundaries are the
    # small minority of rows and the non-boundaries are never the thing being looked for.
    Index(
        "ix_ntfs_resources_boundaries",
        "share_key",
        "resource_key",
        postgresql_where=text("is_acl_boundary"),
    ),
    # A NULL DACL grants everyone full access and names nobody, so such a row appears in no
    # principal_references entry. "What can this principal reach" has to union these in, or
    # it would omit exactly the resources that are open to the whole estate. Partial,
    # because they are meant to be vanishingly rare -- and when they are not, that is the
    # finding.
    Index(
        "ix_ntfs_resources_null_dacl",
        "resource_key",
        postgresql_where=text("NOT dacl_present"),
    ),
    Index("ix_ntfs_resources_last_observed_run", "last_observed_run_id"),
    comment=(
        "File-system resources whose NTFS security descriptor ADG has read, keyed by UNC "
        "path. Directories unless resource_kind says otherwise."
    ),
)


ntfs_aces = Table(
    "ntfs_aces",
    metadata,
    # NtfsAce.identity_key(resource_key): '<resource>|<trustee>|<type>|<mask>|<flags>'.
    # The flags byte is part of it because it is part of the grant; order_index is not,
    # for the same reason it is absent from a share ACE key.
    Column("ace_key", String(KEY_LENGTH), primary_key=True),
    Column("resource_key", String(KEY_LENGTH), nullable=False),
    Column("trustee_sid", String(200), nullable=False),
    # referenced_principal_key(trustee_sid, server): host-scoped for a BUILTIN SID, global
    # for every other. Whether the principal is known stays a join against principals.
    Column("trustee_key", String(KEY_LENGTH), nullable=False),
    Column("ace_type", Text, nullable=False),
    # bigint: 0xFFFFFFFF overflows PostgreSQL's signed integer to -1.
    Column("access_mask", BigInteger, nullable=False),
    # The raw ACE_HEADER.AceFlags byte, unknown bits included. Inheritance and propagation
    # are deliberately not split into columns: they are one byte in the descriptor, and
    # splitting them would make a round trip lossy for any bit Windows adds later.
    Column("ace_flags", Integer, nullable=False),
    # Redundant with bit 0x10 of ace_flags, and stored anyway: "which entries were set on
    # this folder" is what an administrator asks when deciding where to make a fix, and it
    # must not depend on remembering a bit position.
    Column("source", Text, nullable=False),
    # The ancestor Windows named as the origin, when it named one. Not a foreign key: the
    # ancestor may sit above everything this run was permitted to read.
    Column("inherited_from", Text, nullable=True),
    # Position in the DACL as read. Recorded because evaluation order is what makes a Deny
    # meaningful, but not part of identity.
    Column("order_index", Integer, nullable=True),
    Column("source_key", String(KEY_LENGTH), nullable=False),
    _timestamp("first_observed_at"),
    Column("first_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("last_observed_at"),
    Column("last_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("ace_type", AceType),
    _enum_check("source", AceSource),
    CheckConstraint(
        f"access_mask >= 0 AND access_mask <= {MAX_ACCESS_MASK_VALUE}",
        name="ck_ntfs_aces_access_mask_range",
    ),
    CheckConstraint("ace_flags >= 0 AND ace_flags <= 255", name="ck_ntfs_aces_ace_flags_range"),
    # The INHERITED bit and the source column are two spellings of one fact; letting them
    # disagree would make "where do I fix this" answer differently depending on which one
    # a query happened to read.
    CheckConstraint(
        "((ace_flags & 16) <> 0) = (source = 'inherited')",
        name="ck_ntfs_aces_source_matches_inherited_bit",
    ),
    CheckConstraint(
        "inherited_from IS NULL OR source = 'inherited'",
        name="ck_ntfs_aces_origin_only_when_inherited",
    ),
    CheckConstraint(
        "order_index IS NULL OR order_index >= 0", name="ck_ntfs_aces_order_index_non_negative"
    ),
    CheckConstraint(
        "strpos(ace_key, resource_key || '|') = 1", name="ck_ntfs_aces_key_scoped_by_resource"
    ),
    # Reading one directory's DACL, and finding every directory that names one trustee,
    # are the two questions this table exists to answer. Both get a covering index.
    Index("ix_ntfs_aces_resource", "resource_key", "ace_key"),
    Index("ix_ntfs_aces_trustee", "trustee_key", "resource_key"),
    Index("ix_ntfs_aces_trustee_sid", "trustee_sid"),
    Index("ix_ntfs_aces_last_observed_run", "last_observed_run_id"),
    comment="Raw NTFS ACEs, exactly as read. No inheritance is resolved and no Deny applied.",
)


principal_references = Table(
    "principal_references",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    # The key this reference resolves to, whether or not a principals row exists for it.
    # Unresolved is the absence of that row, computed by a join: storing a 'resolved' flag
    # would be a cache that lies the moment an AD run describes the SID.
    Column("principal_key", String(KEY_LENGTH), nullable=False),
    Column("sid", String(200), nullable=False),
    # The machine whose context scoped the reference, when scoping applied. NULL for a
    # globally unique SID, which is most of them.
    Column("host_key", Text, nullable=True),
    Column("reference_kind", Text, nullable=False),
    # What names the principal: a share_key today, an NTFS resource key in Phase 3.
    Column("reference_key", String(KEY_LENGTH), nullable=False),
    _timestamp("first_observed_at"),
    Column("first_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("last_observed_at"),
    Column("last_observed_run_id", PgUUID(as_uuid=True), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    UniqueConstraint(
        "principal_key", "reference_kind", "reference_key", name="uq_principal_references_identity"
    ),
    _enum_check("reference_kind", ReferenceKind),
    # "Which resources name this SID, resolved or not" is the question an orphaned-SID
    # report asks, and it must not require scanning every ACL table in the estate.
    Index("ix_principal_references_principal", "principal_key", "reference_key"),
    Index("ix_principal_references_sid", "sid"),
    Index("ix_principal_references_target", "reference_kind", "reference_key"),
    comment="Every reference from a resource ACL to a principal key. Resolution is a join.",
)


object_versions = Table(
    "object_versions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    # The seven kinds of contract v1. Reusing the contract enum rather than declaring a
    # storage-local one means a kind added to the contract is tracked here by default; a
    # kind that is accepted, stored, and silently given no history would look complete in
    # every current-state query and have an empty timeline, which is the hardest kind of
    # gap to notice.
    Column("object_kind", Text, nullable=False),
    # The same identity string the current-state table uses as its primary key --
    # principal_key, edge_key, share_key, ace_key, resource_key. Not a foreign key to any
    # of them: history outlives the row it describes, and a tombstone is precisely a
    # version of an object whose current-state row may since have been pruned.
    Column("object_key", String(KEY_LENGTH), nullable=False),
    # The object this one is an entry of, so that "everything inside X, as of T" is an
    # indexed read rather than a scan: the share for a share ACE, the resource for an NTFS
    # ACE, the server for a share, the group for a membership edge.
    Column("container_key", String(KEY_LENGTH), nullable=True),
    # The far end of a relation, when there is one: the member of a membership edge, the
    # trustee of an ACE. This is what makes "which groups did this principal belong to on
    # Tuesday" and "what named this SID on Tuesday" indexed in the other direction.
    Column("related_key", String(KEY_LENGTH), nullable=True),
    # False is a tombstone: an authoritative scan of a reconciled scope looked and did not
    # find the object. It is a stored row rather than the absence of one because "ADG knows
    # it was gone" and "ADG has nothing for that instant" are different answers, and only
    # the first one may be rendered as a deletion.
    Column("is_present", Boolean, nullable=False),
    # The descriptive columns of the current-state row, with provenance stripped. JSONB
    # rather than a per-kind mirror table because the temporal rules -- what opens a
    # version, what closes one, what may be inferred absent, what retention may remove --
    # are identical for all seven kinds, and seven copies of them would be seven chances
    # for them to drift.
    # ``none_as_null`` so that a tombstone's absent state is SQL NULL. Without it
    # SQLAlchemy writes Python ``None`` as the JSON value ``null``, which is a present
    # value: the row would satisfy ``state IS NOT NULL`` while claiming the object is
    # absent, and "we have no state" and "its state is the null value" would be stored
    # identically.
    Column("state", JSONB(none_as_null=True), nullable=True),
    Column("state_hash", String(STATE_DIGEST_LENGTH), nullable=True),
    Column("origin", Text, nullable=False, server_default=VersionOrigin.OBSERVED.value),
    # When this state was first observed.
    _timestamp("valid_from"),
    # The newest observation that confirmed this state. Not redundant with valid_to: a
    # collector samples rather than watches, so a change is known to have happened
    # somewhere in (last_seen_at, valid_to] and nowhere more precisely. Dropping this
    # column would replace that interval with the instant somebody happened to look.
    _timestamp("last_seen_at"),
    # NULL means this is the version currently believed to hold.
    _timestamp("valid_to", nullable=True),
    Column("close_reason", Text, nullable=True),
    Column("opened_by_run_id", PgUUID(as_uuid=True), nullable=False),
    Column("last_seen_run_id", PgUUID(as_uuid=True), nullable=False),
    Column("closed_by_run_id", PgUUID(as_uuid=True), nullable=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("object_kind", ObservationKind),
    _enum_check("origin", VersionOrigin),
    _enum_check("close_reason", CloseReason, nullable=True),
    CheckConstraint("last_seen_at >= valid_from", name="ck_object_versions_confirmed_after_opened"),
    CheckConstraint(
        "valid_to IS NULL OR valid_to >= last_seen_at",
        name="ck_object_versions_closed_after_confirmed",
    ),
    # A closed version must say why -- 'superseded' and 'absent' are different findings --
    # and must name the run accountable for the ending. An ending nobody is accountable for
    # cannot be audited, which is the one thing this table exists for.
    CheckConstraint(
        "(valid_to IS NULL) = (close_reason IS NULL)",
        name="ck_object_versions_closed_has_a_reason",
    ),
    CheckConstraint(
        "(valid_to IS NULL) = (closed_by_run_id IS NULL)",
        name="ck_object_versions_closed_has_a_run",
    ),
    CheckConstraint(
        "is_present = (state IS NOT NULL)", name="ck_object_versions_presence_matches_state"
    ),
    CheckConstraint(
        "is_present = (state_hash IS NOT NULL)",
        name="ck_object_versions_presence_matches_digest",
    ),
    CheckConstraint(
        f"state_hash IS NULL OR state_hash ~ '^[0-9a-f]{{{STATE_DIGEST_LENGTH}}}$'",
        name="ck_object_versions_state_hash_shape",
    ),
    UniqueConstraint("object_kind", "object_key", "valid_from", name="uq_object_versions_identity"),
    # At most one open version per object, enforced by the database rather than by the
    # writer being careful. Two open versions would make "what is true now" return two
    # contradictory rows, and the writer's own correctness depends on being able to read
    # "the open version" as a single row.
    Index(
        "ux_object_versions_open",
        "object_kind",
        "object_key",
        unique=True,
        postgresql_where=text("valid_to IS NULL"),
    ),
    Index(
        "ix_object_versions_container",
        "object_kind",
        "container_key",
        "valid_from",
        postgresql_where=text("container_key IS NOT NULL"),
    ),
    Index(
        "ix_object_versions_related",
        "object_kind",
        "related_key",
        "valid_from",
        postgresql_where=text("related_key IS NOT NULL"),
    ),
    # Retention reads closed versions by age and nothing else; partial, because the open
    # versions are the majority and are never candidates for removal.
    Index(
        "ix_object_versions_closed_at",
        "valid_to",
        postgresql_where=text("valid_to IS NOT NULL"),
    ),
    Index("ix_object_versions_last_seen_run", "last_seen_run_id"),
    comment=(
        "Validity intervals for every collected object. One row per state an object was "
        "observed to hold; is_present false is a measured absence."
    ),
)
