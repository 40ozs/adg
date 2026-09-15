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
    MAX_CHECKPOINT_TOKEN_LENGTH,
    AceSource,
    AceType,
    AclBoundaryReason,
    ApprovalDecision,
    CampaignFocus,
    CampaignStatus,
    ChangePlanStatus,
    ChangeTargetKind,
    CheckpointKind,
    CollectionMode,
    CommentRequirement,
    DecisionKind,
    GovernanceEventType,
    GroupScope,
    GroupType,
    MembershipEdgeKind,
    OwnershipRole,
    PlannedChangeKind,
    PrincipalKind,
    RemediationAction,
    RemediationStatus,
    ResourceKind,
    ReviewItemStatus,
    ReviewScopeKind,
    ReviewTargetKind,
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
    "ALERT_KEY_LENGTH",
    "CERTAINTY_VALUES",
    "FINDING_KEY_LENGTH",
    "GOVERNANCE_DIGEST_LENGTH",
    "IDEMPOTENCY_KEY_LENGTH",
    "KEY_LENGTH",
    "MAX_ACCESS_MASK_VALUE",
    "PLAN_TITLE_LENGTH",
    "SIMULATION_TOKEN_LENGTH",
    "STATE_DIGEST_LENGTH",
    "SUBJECT_LENGTH",
    "AlertLifecycleValue",
    "AlertStatusValue",
    "AlertTransitionValue",
    "AlertTriggerValue",
    "AliasKind",
    "CloseReason",
    "DeliveryStatusValue",
    "ReferenceKind",
    "RiskEvaluationTrigger",
    "RiskFindingEventType",
    "RiskFindingStatus",
    "SimulationBaselineKind",
    "VersionOrigin",
    "WatchKindValue",
    "alert_deliveries",
    "alert_events",
    "alert_watches",
    "alerts",
    "collector_sources",
    "governance_audit_events",
    "membership_edges",
    "metadata",
    "ntfs_aces",
    "ntfs_resources",
    "object_versions",
    "observations",
    "principal_aliases",
    "principal_references",
    "principals",
    "remediation_approvals",
    "remediation_change_plans",
    "remediation_exports",
    "remediation_planned_changes",
    "remediation_proposals",
    "resource_owners",
    "review_assignments",
    "review_campaign_scopes",
    "review_campaigns",
    "review_decisions",
    "review_items",
    "risk_evaluations",
    "risk_finding_events",
    "risk_findings",
    "scan_run_batches",
    "scan_run_errors",
    "scan_run_scopes",
    "scan_runs",
    "servers",
    "simulation_evaluations",
    "simulations",
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
    # Phase 7B. Why this run is or is not incremental. `incremental` stays the flag the
    # reconciliation guard reads; `mode` is the finer statement layered over it, and the
    # check constraint below keeps the pair from ever disagreeing.
    Column("mode", Text, nullable=False, server_default=CollectionMode.FULL.value),
    # The scheduled job this run belongs to, as named in the orchestrator configuration.
    # Free-form and never interpreted: it is what the checkpoint store keys on and what the
    # operator groups by.
    Column("job", Text, nullable=True),
    Column("affirmation_count_reported", Integer, nullable=True),
    Column("affirmation_count_applied", Integer, nullable=False, server_default="0"),
    # Affirmations the server refused because the digest disagreed with what it holds. A
    # non-zero count is not an error — the collector re-sends those objects in full — but it
    # is the number that says how much of a "nothing changed" scan was actually a change.
    Column("affirmations_refused", Integer, nullable=False, server_default="0"),
    # Set when a completion claimed a status the server could not corroborate — fewer
    # batches received than sent, for instance. Downgrading is recorded, never silent.
    Column("downgrade_reason", Text, nullable=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("status", ScanStatus),
    _enum_check("mode", CollectionMode),
    CheckConstraint(
        "completed_at IS NULL OR completed_at >= started_at",
        name="ck_scan_runs_completed_after_started",
    ),
    # The one pairing that would be a lie: a run recorded as a delta that is nonetheless
    # allowed to mark objects absent. Enforced here as well as in the contract model,
    # because this is the flag `_close_reconciled` reads and the database is the last place
    # it can be wrong.
    CheckConstraint(
        "(mode = 'delta') = incremental",
        name="ck_scan_runs_mode_matches_incremental",
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
    # Phase 7B: reconciliation drift, recorded where the reconciliation itself is recorded.
    # Only the two presence corrections count — an incremental run cannot observe an
    # absence, so these are exactly the facts no amount of extra delta runs would have
    # produced. Ordinary state changes are deliberately not counted here; see
    # app/domain/incremental.py.
    Column("closed_absent", Integer, nullable=False, server_default="0"),
    Column("revived", Integer, nullable=False, server_default="0"),
    # How many delta runs of the same job ran since this scope was last reconciled. It is
    # the denominator the drift count is read against: 3 absences after 50 deltas and 3
    # after one are different stories about the cadence.
    Column("delta_runs_since", Integer, nullable=False, server_default="0"),
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
    # The current-state predicate: "does an open tombstone exist for this object". One
    # partial index over open absences only, so it is the size of the removals rather than
    # of the estate, and every current-state read is an index probe that misses -- see
    # `app.models.current`. Doubly partial on purpose: `valid_to IS NULL` alone would index
    # every live object, which is the whole table and is what `ux_object_versions_open`
    # already covers for a different question.
    Index(
        "ix_object_versions_open_absent",
        "object_kind",
        "object_key",
        postgresql_where=text("valid_to IS NULL AND is_present = false"),
    ),
    Index("ix_object_versions_last_seen_run", "last_seen_run_id"),
    # The change feed's driving predicate: every change is a version opening, so "what
    # changed between Tuesday and Friday" is a range scan on valid_from. The row id is the
    # second column because one scan opens thousands of versions at a single instant, and a
    # cursor carrying only the timestamp would either skip every other version at that
    # instant or return them all again on the next page.
    Index("ix_object_versions_opened_at", "valid_from", "id"),
    comment=(
        "Validity intervals for every collected object. One row per state an object was "
        "observed to hold; is_present false is a measured absence."
    ),
)


# --- Incremental collection (Phase 7B) ------------------------------------------------

scan_run_checkpoints = Table(
    "scan_run_checkpoints",
    metadata,
    # Two roles, one row each: where the run resumed from and where it got to. A separate
    # table rather than six columns on scan_runs, because a checkpoint is four fields that
    # only mean anything together, and because most runs have neither.
    Column(
        "run_id",
        PgUUID(as_uuid=True),
        ForeignKey("scan_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("role", Text, primary_key=True),
    Column("checkpoint_kind", Text, nullable=False),
    Column("token", String(MAX_CHECKPOINT_TOKEN_LENGTH), nullable=False),
    Column("issuer", Text, nullable=False),
    _timestamp("issued_at"),
    _timestamp("recorded_at"),
    _enum_check("checkpoint_kind", CheckpointKind),
    CheckConstraint(
        "role IN ('baseline', 'result')",
        name="ck_scan_run_checkpoints_role",
    ),
    comment="Where a delta run resumed from, and the cursor it left behind.",
)


collector_checkpoints = Table(
    "collector_checkpoints",
    metadata,
    # Keyed on the job, not on the collector host. A job moved to a new collector host that
    # still binds the same domain controller has a watermark that is still valid -- USNs
    # belong to the DC, not to whoever read them -- and keying on the host would throw away
    # a usable cursor every time an operator rebuilt a server. What actually protects the
    # cursor is `issuer`, which the advance rule compares.
    Column("collector", Text, primary_key=True),
    Column("job", Text, primary_key=True),
    Column("checkpoint_kind", Text, nullable=False),
    Column("token", String(MAX_CHECKPOINT_TOKEN_LENGTH), nullable=False),
    Column("issuer", Text, nullable=False),
    _timestamp("issued_at"),
    # Which run and batch moved it here. A checkpoint advances per *applied batch*, so the
    # batch is the finer attribution and the one that matters when a run dies half way.
    Column("run_id", PgUUID(as_uuid=True), nullable=True),
    Column("batch_id", PgUUID(as_uuid=True), nullable=True),
    Column("collector_host", Text, nullable=True),
    _timestamp("advanced_at"),
    # Why the most recent attempt to move this cursor was refused, and when. Cleared on the
    # next successful advance. Stored rather than only logged because it is the operator's
    # only warning that a job has stopped making progress: a refused checkpoint leaves the
    # job resuming from the same place forever, and every run after it looks successful.
    Column("last_rejection_code", Text, nullable=True),
    Column("last_rejection_message", Text, nullable=True),
    _timestamp("last_rejected_at", nullable=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("collector", CollectorKind),
    _enum_check("checkpoint_kind", CheckpointKind),
    Index("ix_collector_checkpoints_advanced_at", "advanced_at"),
    comment=(
        "The resume point of each scheduled collection job. Advanced only by an applied "
        "batch or a succeeded run, and never backwards within one issuer."
    ),
)


# ----------------------------------------------------------------------------- governance
#
# Phase 10A. Everything below this line is ADG's own record of *decisions about* the tables
# above; nothing below is an observation, and no write path in ``app/governance`` touches a
# table above it. Two structural consequences are worth stating where the tables are
# declared, because both are easy to erode later:
#
# * **There is no foreign key from any governance table to a collected table.** A campaign
#   names a ``target_key`` and a ``principal_key`` as strings, exactly as the ACL tables name
#   each other, so a review of a share that a later reconciliation proves is gone stays
#   readable -- which is precisely when an auditor wants to read it. A foreign key would make
#   the collected side undeletable-or-cascading, and both are wrong answers for a record of
#   who attested to what.
# * **``resource_owners`` is ADG metadata.** It is not ``ntfs_resources.owner_sid`` -- the
#   SID Windows stores as the object's owner -- and is never written from or to it. The gap
#   between "ADG holds Alice accountable" and "Windows says BUILTIN\\Administrators owns it"
#   is itself a finding, and one column holding both would erase it. See ADR-0028.

GOVERNANCE_DIGEST_LENGTH = 64
"""Hex characters of the SHA-256 digests governance stores: an item's evidence digest, a
campaign's snapshot digest, and the audit chain's links. Full width, for the same reason
:data:`STATE_DIGEST_LENGTH` is: a truncated digest would eventually let two different item
sets verify as the same campaign."""

PLAN_TITLE_LENGTH = 200
"""Characters in a change plan's title. Long enough for a change-ticket subject, short
enough that a list of plans renders. Matched by
:data:`app.remediation.model.MAX_TITLE_LENGTH`, which is what refuses a longer one with a
sentence rather than with a database error; a test pins the two equal."""

SUBJECT_LENGTH = 320
"""An authentication subject -- an OIDC ``sub``/``oid``, or a development user name. Sized
for an email-shaped identifier, which is the longest form a tenant realistically sends."""

#: :class:`app.history.model.Certainty`, written out rather than generated from the enum.
#: :mod:`app.history.model` imports *this* module, so importing it back would close a cycle.
#: ``tests/governance/test_schema_vocabulary.py`` asserts this tuple equals the enum's
#: values, which is the drift guard the generated constraints get for free.
CERTAINTY_VALUES: tuple[str, ...] = ("observed", "inferred", "backfilled", "unobserved")


def _digest_check(table: str, column: str, *, nullable: bool = False) -> CheckConstraint:
    """A constraint pinning a column to a lowercase hex SHA-256 digest."""
    predicate = f"{column} ~ '^[0-9a-f]{{{GOVERNANCE_DIGEST_LENGTH}}}$'"
    if nullable:
        predicate = f"{column} IS NULL OR {predicate}"
    return CheckConstraint(predicate, name=f"ck_{table}_{column}_shape")


resource_owners = Table(
    "resource_owners",
    metadata,
    Column("owner_id", PgUUID(as_uuid=True), primary_key=True),
    Column("target_kind", Text, nullable=False),
    Column("target_key", String(KEY_LENGTH), nullable=False),
    Column("ownership_role", Text, nullable=False),
    # An owner is either an ADG user who signs in, or a Windows principal recorded before
    # anyone from it ever has. Both are real; exactly one is set on a row.
    Column("owner_subject", String(SUBJECT_LENGTH), nullable=True),
    Column("owner_principal_key", String(KEY_LENGTH), nullable=True),
    Column("owner_display_name", Text, nullable=True),
    Column("note", Text, nullable=True),
    Column("assigned_by_subject", String(SUBJECT_LENGTH), nullable=False),
    _timestamp("assigned_at"),
    # Revoked rather than deleted: "who was accountable last quarter" is exactly the
    # question an audit of last quarter's campaign asks.
    _timestamp("revoked_at", nullable=True),
    Column("revoked_by_subject", String(SUBJECT_LENGTH), nullable=True),
    _timestamp("created_at"),
    _enum_check("target_kind", ReviewTargetKind),
    _enum_check("ownership_role", OwnershipRole),
    CheckConstraint(
        "(owner_subject IS NULL) <> (owner_principal_key IS NULL)",
        name="ck_resource_owners_exactly_one_owner_form",
    ),
    CheckConstraint(
        "(revoked_at IS NULL) = (revoked_by_subject IS NULL)",
        name="ck_resource_owners_revocation_is_attributed",
    ),
    # One active record per (target, role, owner). The COALESCE is what makes it one index
    # across both owner forms; without it a target could carry the same party twice, once by
    # subject and once by SID, and "who owns this" would have two answers.
    Index(
        "ux_resource_owners_active",
        "target_kind",
        "target_key",
        "ownership_role",
        text("coalesce(owner_subject, owner_principal_key)"),
        unique=True,
        postgresql_where=text("revoked_at IS NULL"),
    ),
    Index("ix_resource_owners_target", "target_kind", "target_key"),
    Index("ix_resource_owners_subject", "owner_subject"),
    Index("ix_resource_owners_principal", "owner_principal_key"),
    comment=(
        "ADG's record of who is accountable for a resource. Not the Windows security "
        "descriptor's owner, which is collected in ntfs_resources.owner_sid."
    ),
)


review_campaigns = Table(
    "review_campaigns",
    metadata,
    Column("campaign_id", PgUUID(as_uuid=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=True),
    Column("focus", Text, nullable=False),
    Column("status", Text, nullable=False),
    # The freeze. Items are a pure function of (focus, scopes, options) applied to
    # object_versions at this instant, which is what makes a campaign reproducible from its
    # own row and what the verification endpoint recomputes.
    _timestamp("baseline_at"),
    _timestamp("due_at", nullable=True),
    # The generation options, as columns rather than a blob: they are what the campaign did
    # *not* ask about, they are reported with its status, and a reader deciding whether
    # "47 of 47 certified" means full coverage has to see them without parsing JSON.
    Column("include_inherited", Boolean, nullable=False, server_default="false"),
    Column("include_builtin", Boolean, nullable=False, server_default="false"),
    Column("include_deny", Boolean, nullable=False, server_default="true"),
    # How hard this campaign insists a decision explain itself. It may only ever *add* to
    # ck_review_decisions_reason_required below, never relax it: a campaign setting that
    # could switch off "a revocation must say why" would be a way to make an unexplained
    # removal, one campaign at a time.
    Column(
        "comment_requirement",
        Text,
        nullable=False,
        server_default=CommentRequirement.STANDARD.value,
    ),
    Column("snapshot_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=True),
    Column("item_count", Integer, nullable=False, server_default="0"),
    # {reason: count} for every grant that was present at the baseline and produced no item.
    # NOT NULL with a '{}' default: an empty tally and a missing tally read identically to a
    # human and mean different things to a query.
    Column("excluded_counts", JSONB(none_as_null=True), nullable=False, server_default="{}"),
    _timestamp("generated_at", nullable=True),
    _timestamp("activated_at", nullable=True),
    _timestamp("closed_at", nullable=True),
    Column("closed_by_subject", String(SUBJECT_LENGTH), nullable=True),
    Column("created_by_subject", String(SUBJECT_LENGTH), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("focus", CampaignFocus),
    _enum_check("status", CampaignStatus),
    _enum_check("comment_requirement", CommentRequirement),
    CheckConstraint("length(btrim(name)) > 0", name="ck_review_campaigns_name_not_blank"),
    CheckConstraint("item_count >= 0", name="ck_review_campaigns_item_count_non_negative"),
    _digest_check("review_campaigns", "snapshot_digest", nullable=True),
    # A generated campaign has a digest, and a digest means it was generated. Splitting the
    # two would allow a campaign that claims a frozen item set and cannot say which one.
    CheckConstraint(
        "(generated_at IS NULL) = (snapshot_digest IS NULL)",
        name="ck_review_campaigns_generated_has_a_digest",
    ),
    # Only a generated campaign may open for review. One activated before its items existed
    # would collect decisions on an item set that was still being built, which is the single
    # thing the draft state exists to prevent.
    CheckConstraint(
        "status NOT IN ('active', 'closed') OR generated_at IS NOT NULL",
        name="ck_review_campaigns_open_only_when_generated",
    ),
    CheckConstraint(
        "activated_at IS NULL OR generated_at IS NOT NULL",
        name="ck_review_campaigns_activation_follows_generation",
    ),
    CheckConstraint(
        "due_at IS NULL OR due_at > baseline_at",
        name="ck_review_campaigns_due_after_baseline",
    ),
    CheckConstraint(
        "(closed_at IS NULL) = (closed_by_subject IS NULL)",
        name="ck_review_campaigns_closure_is_attributed",
    ),
    CheckConstraint(
        "jsonb_typeof(excluded_counts) = 'object'",
        name="ck_review_campaigns_excluded_counts_is_an_object",
    ),
    Index("ix_review_campaigns_status", "status", "created_at"),
    Index("ix_review_campaigns_created_at", "created_at"),
    comment="One access review, frozen against the instant in baseline_at.",
)


review_campaign_scopes = Table(
    "review_campaign_scopes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "campaign_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("scope_kind", Text, nullable=False),
    Column("scope_key", String(KEY_LENGTH), nullable=False),
    _timestamp("created_at"),
    _enum_check("scope_kind", ReviewScopeKind),
    CheckConstraint("length(btrim(scope_key)) > 0", name="ck_review_campaign_scopes_key_not_blank"),
    UniqueConstraint(
        "campaign_id", "scope_kind", "scope_key", name="uq_review_campaign_scopes_identity"
    ),
    comment="What a campaign selected, and therefore what it claims to be complete about.",
)


review_assignments = Table(
    "review_assignments",
    metadata,
    Column("assignment_id", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "campaign_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    # The authentication subject, not a SID. An ADG reviewer is a person who signs in to ADG;
    # the Windows principals in a campaign are the *subjects of* the review. See ADR-0030.
    Column("reviewer_subject", String(SUBJECT_LENGTH), nullable=False),
    Column("reviewer_display_name", Text, nullable=True),
    Column("reviewer_email", Text, nullable=True),
    # NULL/NULL is an assignment covering the whole campaign.
    Column("scope_kind", Text, nullable=True),
    Column("scope_key", String(KEY_LENGTH), nullable=True),
    _timestamp("due_at", nullable=True),
    Column("assigned_by_subject", String(SUBJECT_LENGTH), nullable=False),
    _timestamp("assigned_at"),
    _timestamp("revoked_at", nullable=True),
    Column("revoked_by_subject", String(SUBJECT_LENGTH), nullable=True),
    _timestamp("created_at"),
    _enum_check("scope_kind", ReviewScopeKind, nullable=True),
    CheckConstraint(
        "(scope_kind IS NULL) = (scope_key IS NULL)",
        name="ck_review_assignments_scope_is_whole_or_both",
    ),
    CheckConstraint(
        "(revoked_at IS NULL) = (revoked_by_subject IS NULL)",
        name="ck_review_assignments_revocation_is_attributed",
    ),
    # One active assignment per (campaign, reviewer, scope). COALESCE for the same reason
    # ux_resource_owners_active uses it: an unscoped assignment is one value here rather than
    # a distinct NULL per row, so a reviewer cannot be given the whole campaign twice.
    Index(
        "ux_review_assignments_active",
        "campaign_id",
        "reviewer_subject",
        text("coalesce(scope_kind, '')"),
        text("coalesce(scope_key, '')"),
        unique=True,
        postgresql_where=text("revoked_at IS NULL"),
    ),
    Index("ix_review_assignments_reviewer", "reviewer_subject", "campaign_id"),
    Index("ix_review_assignments_campaign", "campaign_id"),
    comment="A reviewer, and the slice of a campaign they were asked to answer.",
)


review_items = Table(
    "review_items",
    metadata,
    Column("item_id", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "campaign_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("focus", Text, nullable=False),
    Column("target_kind", Text, nullable=False),
    # A string, not a foreign key. A review of a share a later reconciliation proves is gone
    # must stay readable, because that is exactly when somebody wants to read it.
    Column("target_key", String(KEY_LENGTH), nullable=False),
    Column("target_path", Text, nullable=True),
    Column("principal_key", String(KEY_LENGTH), nullable=False),
    Column("principal_sid", String(200), nullable=False),
    Column("principal_display_name", Text, nullable=True),
    # The frozen evidence: every access-control entry that creates this grant, exactly as it
    # stood at the baseline, each carrying the object_versions row it came from. JSONB rather
    # than a child table because it is written once, read whole, and never queried one entry
    # at a time -- and because an item *is* its evidence, so the two must not be able to
    # exist apart.
    Column("grants", JSONB(none_as_null=True), nullable=False),
    Column("evidence_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=False),
    # The weakest certainty of any grant. Stored rather than derived on read: an item
    # generated from state the Phase 7 backfill reconstructed must say so on every screen
    # that shows it, and a value recomputed later would drift as newer scans arrive.
    Column("certainty", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column(
        "assignment_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_assignments.assignment_id", ondelete="SET NULL"),
        nullable=True,
    ),
    # Deliberately not a foreign key to review_decisions: the two tables would then reference
    # each other, and a circular constraint makes the insert order of a decision and its
    # item's update a deadlock waiting to be found. The decision row is the authority; this
    # is a maintained pointer, and ux_review_decisions_current guarantees there is at most
    # one row it could point at.
    Column("current_decision_id", PgUUID(as_uuid=True), nullable=True),
    _timestamp("decided_at", nullable=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("focus", CampaignFocus),
    _enum_check("target_kind", ReviewTargetKind),
    _enum_check("status", ReviewItemStatus),
    CheckConstraint(
        "certainty IN (" + ", ".join(f"'{value}'" for value in CERTAINTY_VALUES) + ")",
        name="ck_review_items_certainty_valid",
    ),
    _digest_check("review_items", "evidence_digest"),
    CheckConstraint("jsonb_typeof(grants) = 'array'", name="ck_review_items_grants_is_an_array"),
    # An item with no evidence would be a question about nothing, and a reviewer certifying
    # it would be attesting to an empty set.
    CheckConstraint("jsonb_array_length(grants) > 0", name="ck_review_items_grants_not_empty"),
    CheckConstraint(
        "(status = 'decided') = (current_decision_id IS NOT NULL)",
        name="ck_review_items_decided_names_its_decision",
    ),
    CheckConstraint(
        "(decided_at IS NULL) = (current_decision_id IS NULL)",
        name="ck_review_items_decided_at_matches_decision",
    ),
    # The natural key of an item: a grant is a (principal, target) relation, so one campaign
    # asks about that pair exactly once. Two rows for one pair would let a reviewer certify
    # it and another revoke it with nothing to reconcile the two.
    UniqueConstraint(
        "campaign_id",
        "target_kind",
        "target_key",
        "principal_key",
        name="uq_review_items_natural_key",
    ),
    # Paging a campaign's items, filtered by status, is the hot path; the trailing key makes
    # the index cover the keyset order as well.
    Index("ix_review_items_campaign_status", "campaign_id", "status", "item_id"),
    Index("ix_review_items_assignment", "assignment_id", "status"),
    Index("ix_review_items_principal", "principal_key"),
    Index("ix_review_items_target", "target_kind", "target_key"),
    comment="One grant to be decided, with the evidence it was frozen from at the baseline.",
)


review_decisions = Table(
    "review_decisions",
    metadata,
    Column("decision_id", PgUUID(as_uuid=True), primary_key=True),
    # No ondelete: deleting a campaign whose items carry decisions must fail rather than
    # cascade. An attestation that can be erased by removing the thing it was about is not an
    # attestation.
    Column("item_id", PgUUID(as_uuid=True), ForeignKey("review_items.item_id"), nullable=False),
    Column(
        "campaign_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("decision", Text, nullable=False),
    Column("rationale", Text, nullable=True),
    Column("decided_by_subject", String(SUBJECT_LENGTH), nullable=False),
    Column("decided_by_display_name", Text, nullable=True),
    _timestamp("decided_at"),
    # Recorded at the moment rather than derived later, because the deadline it was late
    # against is the campaign's due date *then*.
    Column("decided_late", Boolean, nullable=False, server_default="false"),
    Column(
        "supersedes_decision_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_decisions.decision_id"),
        nullable=True,
    ),
    _timestamp("superseded_at", nullable=True),
    # DEFERRABLE INITIALLY DEFERRED, and this is load-bearing rather than defensive. The
    # partial unique index below permits exactly one *current* decision per item, so the
    # supersession must be written BEFORE the successor is inserted -- the other order has
    # two current rows in the middle of the transaction and the index refuses it. Writing it
    # first means pointing at a row that does not exist yet, which an immediate foreign key
    # refuses. Deferring the reference is what lets both invariants hold at once: neither is
    # relaxed, and both are true at commit.
    Column(
        "superseded_by_decision_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_decisions.decision_id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    ),
    _timestamp("created_at"),
    _enum_check("decision", DecisionKind),
    # Every answer but 'certify' must say why. An unexplained removal cannot be defended to
    # the person who loses access, and an unexplained abstention tells the campaign owner
    # nothing about who should have been asked instead.
    CheckConstraint(
        "decision = 'certify' OR (rationale IS NOT NULL AND length(btrim(rationale)) > 0)",
        name="ck_review_decisions_reason_required",
    ),
    CheckConstraint(
        "rationale IS NULL OR length(rationale) <= 4000",
        name="ck_review_decisions_rationale_length",
    ),
    CheckConstraint(
        "(superseded_at IS NULL) = (superseded_by_decision_id IS NULL)",
        name="ck_review_decisions_supersession_names_its_successor",
    ),
    CheckConstraint(
        "superseded_by_decision_id IS NULL OR superseded_by_decision_id <> decision_id",
        name="ck_review_decisions_not_superseded_by_itself",
    ),
    # At most one current decision per item, enforced by the database rather than by the
    # service being careful. Two would make "what did the reviewer conclude" return two
    # contradictory rows with no way to choose between them.
    Index(
        "ux_review_decisions_current",
        "item_id",
        unique=True,
        postgresql_where=text("superseded_at IS NULL"),
    ),
    Index("ix_review_decisions_item", "item_id", "decided_at"),
    Index("ix_review_decisions_campaign", "campaign_id", "decided_at"),
    Index("ix_review_decisions_subject", "decided_by_subject", "decided_at"),
    comment=(
        "Attestations. Append-only: a changed mind writes a new row and supersedes the old "
        "one. A trigger refuses every other update and every delete."
    ),
)


remediation_proposals = Table(
    "remediation_proposals",
    metadata,
    Column("proposal_id", PgUUID(as_uuid=True), primary_key=True),
    Column("item_id", PgUUID(as_uuid=True), ForeignKey("review_items.item_id"), nullable=False),
    Column(
        "campaign_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_campaigns.campaign_id"),
        nullable=False,
    ),
    Column(
        "decision_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_decisions.decision_id"),
        nullable=True,
    ),
    Column("action", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("target_kind", Text, nullable=False),
    Column("target_key", String(KEY_LENGTH), nullable=False),
    Column("principal_key", String(KEY_LENGTH), nullable=False),
    # The exact entries the proposal is about, by the identity the collector reported. An
    # instruction naming only a resource and a trustee would be ambiguous the moment a second
    # entry existed for the same pair.
    Column("ace_keys", JSONB(none_as_null=True), nullable=False),
    Column("details", JSONB(none_as_null=True), nullable=False, server_default="{}"),
    Column("proposed_by_subject", String(SUBJECT_LENGTH), nullable=False),
    _timestamp("proposed_at"),
    _timestamp("withdrawn_at", nullable=True),
    Column("withdrawn_by_subject", String(SUBJECT_LENGTH), nullable=True),
    _timestamp("created_at"),
    _enum_check("action", RemediationAction),
    _enum_check("status", RemediationStatus),
    _enum_check("target_kind", ReviewTargetKind),
    CheckConstraint(
        "jsonb_typeof(ace_keys) = 'array'", name="ck_remediation_proposals_ace_keys_is_an_array"
    ),
    CheckConstraint(
        "jsonb_typeof(details) = 'object'", name="ck_remediation_proposals_details_is_an_object"
    ),
    CheckConstraint(
        "(withdrawn_at IS NULL) = (withdrawn_by_subject IS NULL)",
        name="ck_remediation_proposals_withdrawal_is_attributed",
    ),
    CheckConstraint(
        "(status = 'withdrawn') = (withdrawn_at IS NOT NULL)",
        name="ck_remediation_proposals_status_matches_withdrawal",
    ),
    Index("ix_remediation_proposals_campaign", "campaign_id", "status"),
    Index("ix_remediation_proposals_item", "item_id"),
    comment=(
        "Changes somebody might make in Windows as a result of a decision. ADG records them "
        "and performs none of them; see SECURITY.md."
    ),
)


governance_audit_events = Table(
    "governance_audit_events",
    metadata,
    Column("event_id", PgUUID(as_uuid=True), primary_key=True),
    # 'campaign:<uuid>' or 'owners'. The chain an event belongs to, and the unit a hash chain
    # is verified over.
    Column("chain_key", Text, nullable=False),
    Column("chain_index", Integer, nullable=False),
    Column("event_type", Text, nullable=False),
    _timestamp("occurred_at"),
    Column("actor_subject", String(SUBJECT_LENGTH), nullable=False),
    Column("actor_display_name", Text, nullable=True),
    # The roles the actor held at the moment. "Alice decided this" and "Alice, who then held
    # the reviewer role, decided this" are different claims, and the second is the one an
    # audit needs after her assignments have been changed.
    Column("actor_roles", JSONB(none_as_null=True), nullable=False, server_default="[]"),
    # No foreign keys, deliberately, and for the same reason object_versions has none: the
    # trail outlives the rows it describes. An event about a decision must stay readable
    # whatever later happens to the campaign.
    Column("campaign_id", PgUUID(as_uuid=True), nullable=True),
    Column("item_id", PgUUID(as_uuid=True), nullable=True),
    Column("decision_id", PgUUID(as_uuid=True), nullable=True),
    Column("payload", JSONB(none_as_null=True), nullable=False, server_default="{}"),
    Column("previous_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=True),
    Column("event_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=False),
    _timestamp("created_at"),
    _enum_check("event_type", GovernanceEventType),
    _digest_check("governance_audit_events", "previous_digest", nullable=True),
    _digest_check("governance_audit_events", "event_digest"),
    CheckConstraint("chain_index >= 0", name="ck_governance_audit_events_index_non_negative"),
    # Exactly the first event in a chain has no predecessor. Without this a chain could be
    # re-rooted in the middle and still verify from that point onward.
    CheckConstraint(
        "(chain_index = 0) = (previous_digest IS NULL)",
        name="ck_governance_audit_events_only_the_first_has_no_link",
    ),
    CheckConstraint(
        "jsonb_typeof(payload) = 'object'", name="ck_governance_audit_events_payload_is_an_object"
    ),
    CheckConstraint(
        "jsonb_typeof(actor_roles) = 'array'",
        name="ck_governance_audit_events_actor_roles_is_an_array",
    ),
    # The chain's own shape: one event per position, enforced by the database. Two events
    # claiming index 7 would make the chain unverifiable and the conflict invisible.
    UniqueConstraint("chain_key", "chain_index", name="uq_governance_audit_events_position"),
    Index("ix_governance_audit_events_campaign", "campaign_id", "occurred_at"),
    Index("ix_governance_audit_events_item", "item_id", "occurred_at"),
    Index("ix_governance_audit_events_actor", "actor_subject", "occurred_at"),
    comment=(
        "Immutable governance audit trail. Append-only by trigger, hash-chained per campaign "
        "so a quiet edit cannot be made without invalidating every later event."
    ),
)


class SimulationBaselineKind(StrEnum):
    """Which collected state a stored simulation was measured against.

    Declared here beside :class:`VersionOrigin` and for the same reason: the values appear in
    a check constraint, the constraint is generated from the enum, and the enum therefore has
    to be importable without importing anything that imports this module.
    """

    CURRENT = "current"
    """Everything ADG has collected, as the live repositories read it."""

    AS_OF = "as_of"
    """The estate as it stood at one instant, through the Phase 7A as-of repositories."""


SIMULATION_TOKEN_LENGTH = 32
"""Hex characters of a collection-basis token, matching :data:`app.domain.basis.TOKEN_LENGTH`.

Restated rather than imported so the physical schema keeps no dependency on the domain
package; a test pins the two equal, because a column narrower than the token would truncate
the one value a stale-baseline check compares.
"""


simulations = Table(
    "simulations",
    metadata,
    Column("simulation_id", PgUUID(as_uuid=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=True),
    # The authenticated subject who proposed it, when one is known. Nullable because a
    # simulation can be computed by a backend caller that has no request behind it.
    Column("created_by", Text, nullable=True),
    # The proposal itself, as app.simulation.SimulationOverlay.document() renders it, with
    # its own document_version inside. JSONB rather than a normalized change table because a
    # change is only ever read back as a whole overlay -- nothing queries for "every proposal
    # that touches this ACE" -- and a per-kind table would be four tables whose validation
    # already lives, exactly once, in the overlay's constructors.
    Column("overlay", JSONB(none_as_null=True), nullable=False),
    Column("overlay_hash", String(SIMULATION_TOKEN_LENGTH), nullable=False),
    Column("change_count", Integer, nullable=False),
    # The state the proposal was written against. The token is what makes staleness exact:
    # it moves if and only if a collector has written something, so a stored simulation whose
    # token no longer matches is one whose impact list has to be recomputed before it is
    # believed. The run id is for a human reading the row and is deliberately not the
    # comparison -- two runs can interleave.
    Column("baseline_kind", Text, nullable=False),
    _timestamp("baseline_at", nullable=True),
    Column("baseline_token", String(SIMULATION_TOKEN_LENGTH), nullable=False),
    Column("baseline_run_id", PgUUID(as_uuid=True), nullable=True),
    _timestamp("baseline_captured_at"),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("baseline_kind", SimulationBaselineKind),
    CheckConstraint("change_count >= 0", name="ck_simulations_change_count_non_negative"),
    # An as-of baseline reads one instant and a current-state baseline reads none. A row that
    # disagreed with itself here would name a baseline the report was not computed against.
    CheckConstraint(
        "(baseline_kind = 'as_of') = (baseline_at IS NOT NULL)",
        name="ck_simulations_as_of_has_an_instant",
    ),
    CheckConstraint("jsonb_typeof(overlay) = 'object'", name="ck_simulations_overlay_is_an_object"),
    Index("ix_simulations_created_at", "created_at"),
    Index("ix_simulations_overlay_hash", "overlay_hash"),
    comment=(
        "Proposed changes to the estate. Read by the simulation engine and by nothing else; "
        "no collector, ingestion path or effective-access query reads this table."
    ),
)


simulation_evaluations = Table(
    "simulation_evaluations",
    metadata,
    Column("evaluation_id", PgUUID(as_uuid=True), primary_key=True),
    # The one foreign key in the simulation area, and it is deliberate. The rule that
    # resource tables carry none is about *observations*: a share whose server no run has
    # described is a real reading, and a constraint would reject it at the moment a partial
    # scan most needs to record what it managed to read. An evaluation is not an observation.
    # It is written by this application, in one transaction, after the proposal it belongs to,
    # and an evaluation of a proposal that does not exist is a defect rather than a partial
    # scan.
    Column(
        "simulation_id",
        PgUUID(as_uuid=True),
        ForeignKey("simulations.simulation_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("scope_kind", Text, nullable=False),
    # The basis token at the moment this evaluation ran, next to whether it had already moved
    # away from the proposal's own baseline. Both are stored because a result recomputed
    # against newer facts is still a result, and hiding which facts it used would make two
    # evaluations of one proposal incomparable.
    Column("baseline_token", String(SIMULATION_TOKEN_LENGTH), nullable=False),
    Column("stale_baseline", Boolean, nullable=False),
    Column("pairs_evaluated", Integer, nullable=False),
    Column("complete", Boolean, nullable=False),
    Column("duration_ms", Integer, nullable=False),
    # The compact report: applicability per change, the impact summary, one row per delta.
    # Not the whole derivation -- that is reproducible from the overlay and the baseline, and
    # a stored copy of it would be a second, ageing account of an answer the engine can
    # recompute exactly.
    Column("report", JSONB(none_as_null=True), nullable=False),
    _timestamp("computed_at"),
    _timestamp("created_at"),
    CheckConstraint("pairs_evaluated >= 0", name="ck_simulation_evaluations_pairs_non_negative"),
    CheckConstraint("duration_ms >= 0", name="ck_simulation_evaluations_duration_non_negative"),
    CheckConstraint(
        "jsonb_typeof(report) = 'object'", name="ck_simulation_evaluations_report_is_an_object"
    ),
    Index("ix_simulation_evaluations_simulation", "simulation_id", "computed_at"),
    comment=(
        "What one simulation produced when it was run. Separate from the proposal so that "
        "re-running against newer collected state adds a row rather than overwriting one."
    ),
)


# ----------------------------------------------------------------------------------- risk
#
# Phase 8A. Three tables, and the split between them is the design.
#
# * ``risk_evaluations`` -- one row per pass of the rule engine. It records what the pass
#   covered, which is what decides what it was allowed to resolve: a pass that loaded the
#   facts around one changed share may not close a finding about a share it never looked at.
# * ``risk_findings`` -- current state, one row per ``finding_key``. The key is a digest of
#   the rule and its subject (:func:`app.risk_engine.finding_key`) and deliberately excludes
#   the rule version, so correcting a predicate re-examines the findings it already made
#   instead of orphaning them.
# * ``risk_finding_events`` -- the transitions. Opened, reaffirmed, evidence changed,
#   resolved, reopened, each with the evaluation that decided it.
#
# The same shape ``object_versions`` has toward the current-state tables, and for the same
# reason: "is this finding open" and "when did it open, close and come back" are different
# questions, and answering the second from a pair of timestamps on the first would lose every
# cycle but the last.
#
# **No foreign key to a collected table.** A finding names a ``resource_key`` and a
# ``principal_key`` as strings, exactly as the ACL tables name each other. A finding about a
# share a later reconciliation proves is gone is precisely the finding an auditor wants to
# read, and a cascade would delete it at that moment.

#: Hex characters of a finding key: SHA-256 over the rule identifier and the subject. Written
#: out rather than imported from :mod:`app.risk_engine.findings`, because that module imports
#: nothing from here and this module must stay importable by the migration that creates the
#: table. ``tests/risk_engine/test_findings.py`` pins the two equal.
FINDING_KEY_LENGTH = 64


class RiskFindingStatus(StrEnum):
    """Whether a finding is currently present in the estate.

    Two values, and no third for "acknowledged" or "accepted". Accepting a risk is a decision
    *about* a finding rather than a property of one, it is made by a person rather than by the
    engine, and putting it here would let a re-evaluation overwrite somebody's judgment with a
    rule's opinion.
    """

    OPEN = "open"
    """The most recent evaluation that covered this subject still matched the rule."""

    RESOLVED = "resolved"
    """An evaluation that covered this subject ran the rule and it no longer matched."""


class RiskFindingEventType(StrEnum):
    """What one evaluation did to one finding."""

    OPENED = "opened"
    """First time this rule matched this subject."""

    REAFFIRMED = "reaffirmed"
    """Matched again, with the same evidence. The common case, and the cheap one."""

    EVIDENCE_CHANGED = "evidence_changed"
    """Still matching, on different facts -- the grant widened, another entry appeared. The
    finding stays open and its evidence is replaced; recording the transition is what keeps
    "this has been open since March" from also meaning "and has said the same thing since"."""

    RESOLVED = "resolved"
    """An evaluation that covered the subject did not match. The finding is closed, never
    deleted."""

    REOPENED = "reopened"
    """A resolved finding matched again. The same row, a new open window."""


class RiskEvaluationTrigger(StrEnum):
    """Why an evaluation ran, which tells a reader how much of the estate it covered."""

    FULL = "full"
    """Everything, on request. The only kind that may resolve a finding anywhere."""

    INCREMENTAL = "incremental"
    """The facts around what a scan run changed."""

    TARGETED = "targeted"
    """One subject, on request -- re-checking a finding somebody is looking at."""


risk_evaluations = Table(
    "risk_evaluations",
    metadata,
    Column("evaluation_id", PgUUID(as_uuid=True), primary_key=True),
    Column("trigger", Text, nullable=False),
    # The scan run whose changes prompted an incremental pass. No foreign key, for the same
    # reason the governance tables have none: the evaluation outlives whatever happens to the
    # run record, and a pass triggered by hand has no run at all.
    Column("source_run_id", PgUUID(as_uuid=True), nullable=True),
    Column("configuration_version", Text, nullable=False),
    # What the pass covered. ``scope_complete`` true means the whole estate; otherwise the
    # three key lists say which subjects it was entitled to reconcile. Stored rather than
    # recomputed, because "was this finding inside the pass that closed it" has to be
    # answerable afterwards, from the row, without re-deriving anything.
    Column("scope_complete", Boolean, nullable=False, server_default="false"),
    Column("scope_resource_keys", JSONB(none_as_null=True), nullable=False, server_default="[]"),
    Column("scope_share_keys", JSONB(none_as_null=True), nullable=False, server_default="[]"),
    Column("scope_principal_keys", JSONB(none_as_null=True), nullable=False, server_default="[]"),
    Column("rules_run", JSONB(none_as_null=True), nullable=False, server_default="[]"),
    Column("rules_skipped", JSONB(none_as_null=True), nullable=False, server_default="[]"),
    Column("findings_matched", Integer, nullable=False, server_default="0"),
    Column("findings_opened", Integer, nullable=False, server_default="0"),
    Column("findings_reopened", Integer, nullable=False, server_default="0"),
    Column("findings_resolved", Integer, nullable=False, server_default="0"),
    Column("error", Text, nullable=True),
    _timestamp("started_at"),
    _timestamp("completed_at", nullable=True),
    _enum_check("trigger", RiskEvaluationTrigger),
    CheckConstraint(
        "trigger <> 'incremental' OR source_run_id IS NOT NULL",
        name="ck_risk_evaluations_incremental_names_its_run",
    ),
    # A complete scope carries no key lists: the lists are what narrows a partial pass, and a
    # row claiming both would make "what was this allowed to close" ambiguous in the one
    # direction that closes findings nobody looked at.
    CheckConstraint(
        "NOT scope_complete OR ("
        "jsonb_array_length(scope_resource_keys) = 0"
        " AND jsonb_array_length(scope_share_keys) = 0"
        " AND jsonb_array_length(scope_principal_keys) = 0)",
        name="ck_risk_evaluations_complete_scope_has_no_key_lists",
    ),
    CheckConstraint(
        "findings_matched >= 0 AND findings_opened >= 0"
        " AND findings_reopened >= 0 AND findings_resolved >= 0",
        name="ck_risk_evaluations_counts_non_negative",
    ),
    Index("ix_risk_evaluations_started", "started_at"),
    Index("ix_risk_evaluations_run", "source_run_id"),
    comment=(
        "One pass of the risk rule engine, with the scope it covered -- which is what limits "
        "which findings it was entitled to resolve."
    ),
)


risk_findings = Table(
    "risk_findings",
    metadata,
    # The finding key is the primary key: a digest of the rule and its subject, so the same
    # shape in the same place is the same row on every evaluation. A surrogate id would need
    # a unique index on exactly these columns to mean anything, which is this.
    Column("finding_key", String(FINDING_KEY_LENGTH), primary_key=True),
    Column("rule_id", Text, nullable=False),
    Column("rule_version", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("confidence", Text, nullable=False),
    Column("severity_band", Text, nullable=False),
    Column("qualifiers", JSONB(none_as_null=True), nullable=False, server_default="[]"),
    # The subject, spread across columns rather than kept only inside the key, because "every
    # finding on this share" is an indexed question and a digest cannot be searched.
    Column("resource_key", String(KEY_LENGTH), nullable=True),
    Column("share_key", String(KEY_LENGTH), nullable=True),
    Column("principal_key", String(KEY_LENGTH), nullable=True),
    Column("discriminator", Text, nullable=True),
    Column("evidence", JSONB(none_as_null=True), nullable=False),
    Column("evidence_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=False),
    Column("detail", JSONB(none_as_null=True), nullable=False, server_default="{}"),
    # Three instants, answering three different questions. ``detected_at`` is when the current
    # open window began -- reset on a reopen, because an "open since" spanning a period the
    # finding was demonstrably gone would be a claim an operator would act on.
    # ``first_detected_at`` is when it was ever first seen and never moves.
    # ``last_evaluated_at`` is when a pass last covered it, which is how far "still there" is
    # actually warranted.
    _timestamp("first_detected_at"),
    _timestamp("detected_at"),
    _timestamp("last_evaluated_at"),
    _timestamp("resolved_at", nullable=True),
    Column("occurrence_count", Integer, nullable=False, server_default="1"),
    Column("first_evaluation_id", PgUUID(as_uuid=True), nullable=False),
    Column("last_evaluation_id", PgUUID(as_uuid=True), nullable=False),
    Column("resolved_evaluation_id", PgUUID(as_uuid=True), nullable=True),
    _enum_check("status", RiskFindingStatus),
    _digest_check("risk_findings", "evidence_digest"),
    # A resolved finding records when and by which pass; an open one records neither. Without
    # this a row could read as open and carry a resolution instant, and every "how long has
    # this been open" answer drawn from it would be wrong.
    CheckConstraint(
        "(status = 'resolved') = (resolved_at IS NOT NULL)",
        name="ck_risk_findings_resolution_has_an_instant",
    ),
    CheckConstraint(
        "(resolved_at IS NULL) = (resolved_evaluation_id IS NULL)",
        name="ck_risk_findings_resolution_is_attributed",
    ),
    CheckConstraint(
        "resource_key IS NOT NULL OR share_key IS NOT NULL OR principal_key IS NOT NULL",
        name="ck_risk_findings_names_a_subject",
    ),
    CheckConstraint("first_detected_at <= detected_at", name="ck_risk_findings_windows_ordered"),
    CheckConstraint("occurrence_count >= 1", name="ck_risk_findings_occurrence_count_positive"),
    CheckConstraint(
        "jsonb_typeof(evidence) = 'array'", name="ck_risk_findings_evidence_is_an_array"
    ),
    CheckConstraint("jsonb_typeof(detail) = 'object'", name="ck_risk_findings_detail_is_an_object"),
    CheckConstraint(
        "jsonb_typeof(qualifiers) = 'array'", name="ck_risk_findings_qualifiers_is_an_array"
    ),
    # The reads a report makes. Every one is filtered by status first, because the default
    # view is the open findings and a full scan of resolved history to build it would get
    # slower every quarter.
    Index("ix_risk_findings_status_severity", "status", "severity", "rule_id"),
    Index("ix_risk_findings_resource", "resource_key", "status"),
    Index("ix_risk_findings_share", "share_key", "status"),
    Index("ix_risk_findings_principal", "principal_key", "status"),
    Index("ix_risk_findings_rule", "rule_id", "status"),
    comment=(
        "Current state of every risk finding, one row per rule and subject. Resolved findings "
        "are kept, never deleted: a finding that was true in March is a fact about March."
    ),
)


risk_finding_events = Table(
    "risk_finding_events",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("finding_key", String(FINDING_KEY_LENGTH), nullable=False),
    Column("event_type", Text, nullable=False),
    Column("evaluation_id", PgUUID(as_uuid=True), nullable=False),
    Column("rule_id", Text, nullable=False),
    Column("rule_version", Text, nullable=False),
    Column("severity", Text, nullable=True),
    Column("confidence", Text, nullable=True),
    Column("evidence_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=True),
    Column("previous_evidence_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=True),
    _timestamp("occurred_at"),
    _enum_check("event_type", RiskFindingEventType),
    _digest_check("risk_finding_events", "evidence_digest", nullable=True),
    _digest_check("risk_finding_events", "previous_evidence_digest", nullable=True),
    # A resolution has nothing to say about evidence -- the rule stopped matching, so there is
    # none -- and every other event does. Stated as a constraint so a writer cannot record a
    # resolution that appears to carry the facts it was resolved on.
    CheckConstraint(
        "(event_type = 'resolved') = (evidence_digest IS NULL)",
        name="ck_risk_finding_events_only_a_resolution_has_no_evidence",
    ),
    CheckConstraint(
        "event_type = 'evidence_changed' OR previous_evidence_digest IS NULL",
        name="ck_risk_finding_events_only_a_change_names_what_it_replaced",
    ),
    Index("ix_risk_finding_events_finding", "finding_key", "occurred_at"),
    Index("ix_risk_finding_events_evaluation", "evaluation_id"),
    comment=(
        "Every transition a risk finding has been through, with the evaluation that decided "
        "it. Append-only in practice; nothing in the application updates a row here."
    ),
)


# ---------------------------------------------------------------------------------
# Alerting (Phase 8B)
#
# Three tables and one rule that shapes all of them: **a suppressed alert is still
# recorded.** Deduplication and cooldown decide what is *delivered*; nothing decides what
# is written down. Without that, a quiet feed and a broken pipeline are the same reading,
# which is this product's own failure mode applied to its own monitoring.
#
# The second shape worth noticing is that ``alert_deliveries`` is an outbox rather than a
# call: enqueueing is in the same transaction as the alert, and delivering is a separate
# pass. An endpoint that is down therefore cannot take an ingestion with it.

ALERT_KEY_LENGTH = 64
"""Hex characters of the SHA-256 that identifies one alert. Full width, for the reason
:data:`STATE_DIGEST_LENGTH` is: a truncated key would eventually fold two different alerts
into one row, and the one they folded into would be deduplicated against the wrong
history."""

IDEMPOTENCY_KEY_LENGTH = 64


class WatchKindValue(StrEnum):
    """:class:`app.alerts.WatchKind`, written out.

    Declared here rather than imported, for the reason :class:`VersionOrigin` gives: the
    values appear in a check constraint, so the enum has to be importable without importing
    anything that imports this module -- and :mod:`app.alerts.model` imports
    :mod:`app.domain`, which imports this. ``tests/alerts/test_schema_vocabulary.py``
    asserts the two agree, so the duplication cannot drift.
    """

    RESOURCE = "resource"
    SHARE = "share"
    GROUP = "group"


class AlertTriggerValue(StrEnum):
    """:class:`app.alerts.AlertTrigger`, written out. See :class:`WatchKindValue`."""

    WATCHED_GROUP_MEMBERSHIP_CHANGED = "watched_group_membership_changed"
    WATCHED_RESOURCE_ACL_CHANGED = "watched_resource_acl_changed"
    WATCHED_ACCESS_EXPANDED = "watched_access_expanded"
    CRITICAL_RISK_FINDING_OPENED = "critical_risk_finding_opened"


class AlertLifecycleValue(StrEnum):
    """:class:`app.alerts.AlertLifecycle`, written out. See :class:`WatchKindValue`."""

    STATEFUL = "stateful"
    TRANSIENT = "transient"


class AlertStatusValue(StrEnum):
    """:class:`app.alerts.AlertStatus`, written out. See :class:`WatchKindValue`."""

    OPEN = "open"
    RESOLVED = "resolved"


class AlertTransitionValue(StrEnum):
    """:class:`app.alerts.AlertTransition`, written out. See :class:`WatchKindValue`."""

    RAISED = "raised"
    REOPENED = "reopened"
    REPEATED = "repeated"
    SUPPRESSED = "suppressed"
    RESOLVED = "resolved"


class DeliveryStatusValue(StrEnum):
    """:class:`app.alerts.DeliveryStatus`, written out. See :class:`WatchKindValue`."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    ABANDONED = "abandoned"


alert_watches = Table(
    "alert_watches",
    metadata,
    Column("watch_id", PgUUID(as_uuid=True), primary_key=True),
    Column("kind", Text, nullable=False),
    # The key of the thing watched, case-folded on the way in so that a watch typed
    # \\FS01\Finance matches the resource key \\fs01\finance. No foreign key, deliberately:
    # a watch on a share that has not been collected yet is a legitimate thing to configure
    # ahead of a scan, and a constraint here would refuse it.
    Column("watch_key", String(KEY_LENGTH), nullable=False),
    Column("label", Text, nullable=False),
    Column("triggers", JSONB(none_as_null=True), nullable=False),
    Column("cooldown_seconds", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("notes", Text, nullable=True),
    Column("created_by", String(SUBJECT_LENGTH), nullable=False),
    Column("updated_by", String(SUBJECT_LENGTH), nullable=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("kind", WatchKindValue),
    # One watch per (kind, key). A second watch on the same directory would double every
    # alert about it and give each one its own cooldown, so an operator who set a quiet
    # window would still be paged at the other watch's rate.
    UniqueConstraint("kind", "watch_key", name="uq_alert_watches_target"),
    CheckConstraint(
        "jsonb_typeof(triggers) = 'array' AND jsonb_array_length(triggers) > 0",
        name="ck_alert_watches_subscribes_to_something",
    ),
    # The bounds app.alerts.model fixes, restated where the data is. A cooldown of zero is
    # not a cooldown; one longer than a day folds Tuesday's change into Monday's alert.
    CheckConstraint(
        "cooldown_seconds >= 60 AND cooldown_seconds <= 86400",
        name="ck_alert_watches_cooldown_bounded",
    ),
    CheckConstraint("length(btrim(label)) > 0", name="ck_alert_watches_labelled"),
    Index("ix_alert_watches_kind", "kind", "enabled"),
    comment=(
        "Standing subscriptions: tell me when this directory, share or group changes. One "
        "row per watched thing."
    ),
)


alerts = Table(
    "alerts",
    metadata,
    # The alert key is the primary key: a digest of the trigger, the subject and a
    # discriminator, never of the content. That is what makes deduplication possible at
    # all -- the same condition in the same place has to be the same row next week.
    Column("alert_key", String(ALERT_KEY_LENGTH), primary_key=True),
    Column("trigger", Text, nullable=False),
    Column("lifecycle", Text, nullable=False),
    Column("status", Text, nullable=False),
    # The watch that subscribed, or NULL for an estate-wide trigger. No foreign key: an
    # alert outlives the watch that raised it, and deleting a watch must not delete the
    # record of what it told somebody.
    Column("watch_id", PgUUID(as_uuid=True), nullable=True),
    Column("watch_label", Text, nullable=True),
    # The subject, spread across columns rather than kept only inside the key, because
    # "every alert about this share" is an indexed question and a digest cannot be searched.
    Column("resource_key", String(KEY_LENGTH), nullable=True),
    Column("share_key", String(KEY_LENGTH), nullable=True),
    Column("principal_key", String(KEY_LENGTH), nullable=True),
    Column("discriminator", Text, nullable=True),
    Column("summary", Text, nullable=False),
    Column("payload", JSONB(none_as_null=True), nullable=False, server_default="{}"),
    Column("payload_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=False),
    # The digest of the payload last **delivered**, not the last one recorded. A suppressed
    # repeat must not move it: if it did, the next occurrence of the content an operator was
    # actually shown would read as new. NULL until something has been delivered.
    Column("delivered_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=True),
    _timestamp("first_raised_at"),
    _timestamp("last_raised_at"),
    _timestamp("last_notified_at", nullable=True),
    _timestamp("resolved_at", nullable=True),
    Column("occurrence_count", Integer, nullable=False, server_default="1"),
    # Occurrences recorded but not delivered since the last notification. Carried into the
    # next delivery, so a notification never silently stands for eleven changes.
    Column("suppressed_since_notice", Integer, nullable=False, server_default="0"),
    Column("suppressed_total", Integer, nullable=False, server_default="0"),
    _enum_check("trigger", AlertTriggerValue),
    _enum_check("lifecycle", AlertLifecycleValue),
    _enum_check("status", AlertStatusValue),
    _digest_check("alerts", "payload_digest"),
    _digest_check("alerts", "delivered_digest", nullable=True),
    CheckConstraint(
        "resource_key IS NOT NULL OR share_key IS NOT NULL OR principal_key IS NOT NULL",
        name="ck_alerts_names_a_subject",
    ),
    # A transient alert describes something that happened; nothing that happens later can
    # make it not have happened. Stated as a constraint so that a resolution pass cannot
    # quietly close an ACL change and make the feed look tidier than the estate is.
    CheckConstraint(
        "lifecycle <> 'transient' OR (status = 'open' AND resolved_at IS NULL)",
        name="ck_alerts_transient_never_resolves",
    ),
    CheckConstraint(
        "(status = 'resolved') = (resolved_at IS NOT NULL)",
        name="ck_alerts_resolution_has_an_instant",
    ),
    CheckConstraint("first_raised_at <= last_raised_at", name="ck_alerts_windows_ordered"),
    CheckConstraint(
        "occurrence_count >= 1 AND suppressed_since_notice >= 0 AND suppressed_total >= 0",
        name="ck_alerts_counts_non_negative",
    ),
    # The reads the feed makes, status first because the default view is open alerts.
    Index("ix_alerts_status_raised", "status", "last_raised_at"),
    Index("ix_alerts_trigger", "trigger", "last_raised_at"),
    Index("ix_alerts_watch", "watch_id", "last_raised_at"),
    Index("ix_alerts_resource", "resource_key", "status"),
    Index("ix_alerts_share", "share_key", "status"),
    Index("ix_alerts_principal", "principal_key", "status"),
    comment=(
        "Current state of every alert, one row per trigger and subject. A suppressed "
        "occurrence updates the counts here and writes an event; it never deletes anything."
    ),
)


alert_events = Table(
    "alert_events",
    metadata,
    Column("event_id", PgUUID(as_uuid=True), primary_key=True),
    Column("alert_key", String(ALERT_KEY_LENGTH), nullable=False),
    Column("transition", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    # Why this occurrence was not delivered. NULL for every transition that notifies, which
    # is the pairing the check constraint below enforces.
    Column("suppression_reason", Text, nullable=True),
    Column("summary", Text, nullable=False),
    Column("payload", JSONB(none_as_null=True), nullable=False, server_default="{}"),
    Column("payload_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=False),
    # Occurrences this event speaks for, the current one included. Above 1 means a cooldown
    # folded others into it.
    Column("folds", Integer, nullable=False, server_default="1"),
    Column("notified", Boolean, nullable=False, server_default="false"),
    # The scan run or risk evaluation that produced it, when there was one. No foreign key:
    # an alert outlives the run that prompted it.
    Column("source_run_id", PgUUID(as_uuid=True), nullable=True),
    Column("source_evaluation_id", PgUUID(as_uuid=True), nullable=True),
    _timestamp("occurred_at"),
    _timestamp("recorded_at"),
    _enum_check("transition", AlertTransitionValue),
    _enum_check("trigger", AlertTriggerValue),
    _digest_check("alert_events", "payload_digest"),
    # A suppression names its reason and a notification does not have one. Without this a
    # row could read as delivered while carrying the reason it was held back, and the
    # "why was I not told" question would have two answers on one row.
    CheckConstraint(
        "(transition = 'suppressed') = (suppression_reason IS NOT NULL)",
        name="ck_alert_events_suppression_names_its_reason",
    ),
    CheckConstraint(
        "(transition = 'suppressed') <> notified",
        name="ck_alert_events_suppression_did_not_notify",
    ),
    CheckConstraint("folds >= 1", name="ck_alert_events_folds_positive"),
    Index("ix_alert_events_alert", "alert_key", "occurred_at"),
    Index("ix_alert_events_occurred", "occurred_at"),
    Index("ix_alert_events_run", "source_run_id"),
    comment=(
        "Every occurrence of every alert, delivered or suppressed. Append-only; nothing in "
        "the application updates a row here. A suppressed occurrence is written precisely "
        "so that 'we were not told' and 'it did not happen' stay separable."
    ),
)


alert_deliveries = Table(
    "alert_deliveries",
    metadata,
    Column("delivery_id", PgUUID(as_uuid=True), primary_key=True),
    Column("event_id", PgUUID(as_uuid=True), nullable=False),
    Column("alert_key", String(ALERT_KEY_LENGTH), nullable=False),
    Column("sink_name", Text, nullable=False),
    Column("status", Text, nullable=False),
    # A pure function of (event_id, sink_name), sent to the receiver so that a retry after
    # a lost acknowledgment can be recognized rather than applied twice. Unique here as
    # well, which is what makes enqueueing idempotent: a re-raised alert records one
    # delivery, not two.
    Column("idempotency_key", String(IDEMPOTENCY_KEY_LENGTH), nullable=False),
    # The envelope as it will be sent. Stored rather than rebuilt at delivery time: a retry
    # next Tuesday must send what the alert said when it was raised, not what the estate
    # looks like when the retry happens.
    Column("envelope", JSONB(none_as_null=True), nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", Text, nullable=True),
    _timestamp("enqueued_at"),
    _timestamp("next_attempt_at"),
    _timestamp("last_attempt_at", nullable=True),
    _timestamp("delivered_at", nullable=True),
    _enum_check("status", DeliveryStatusValue),
    UniqueConstraint("idempotency_key", name="uq_alert_deliveries_idempotency"),
    CheckConstraint(
        "(status = 'delivered') = (delivered_at IS NOT NULL)",
        name="ck_alert_deliveries_delivery_has_an_instant",
    ),
    CheckConstraint(
        "status <> 'pending' OR attempts = 0",
        name="ck_alert_deliveries_pending_has_not_been_tried",
    ),
    # An attempt that failed says why. An abandoned delivery with no error would be an
    # alert nobody received and no record of what stopped it.
    CheckConstraint(
        "status NOT IN ('failed', 'abandoned') OR last_error IS NOT NULL",
        name="ck_alert_deliveries_failure_says_why",
    ),
    CheckConstraint("attempts >= 0", name="ck_alert_deliveries_attempts_non_negative"),
    # The claim query: due work, oldest first. Partial, because delivered rows accumulate
    # forever and a full index over them would get slower every quarter for a query that
    # never reads one.
    Index(
        "ix_alert_deliveries_due",
        "next_attempt_at",
        postgresql_where=text("status IN ('pending', 'failed')"),
    ),
    Index("ix_alert_deliveries_event", "event_id"),
    Index("ix_alert_deliveries_status", "status", "sink_name"),
    comment=(
        "The outbox. One row per alert event per sink. Enqueued in the alert's own "
        "transaction and delivered in a separate pass, so a failing endpoint cannot roll "
        "back the ingestion that produced the alert."
    ),
)


# ---------------------------------------------------------------------------------------
# Phase 10C: proposed remediation
#
# Four tables holding ADG's description of work somebody might do in Windows. ADG does none
# of it (ADR-0035). Nothing here is an observation, nothing here has a collector behind it,
# and -- exactly as for the governance tables -- no column in any of them is a foreign key
# into a table a collector writes. A plan names its targets by string key, which is what
# lets it keep naming them after the object has gone: an instruction that vanished when the
# ACE did would take the evidence of what was planned with it.
# ---------------------------------------------------------------------------------------


remediation_change_plans = Table(
    "remediation_change_plans",
    metadata,
    Column("plan_id", PgUUID(as_uuid=True), primary_key=True),
    Column("title", String(PLAN_TITLE_LENGTH), nullable=False),
    # Why. NOT NULL with no default and no setting that relaxes it: somebody is about to
    # lose access, and the person losing it is entitled to better than "it was on the list".
    Column("rationale", Text, nullable=False),
    Column("status", Text, nullable=False),
    # The campaign whose decisions produced this plan, when one did. Nullable, because a plan
    # written straight out of an operator's judgment is legitimate -- it simply cites nothing,
    # which is a visible property rather than a hidden one.
    Column(
        "campaign_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_campaigns.campaign_id"),
        nullable=True,
    ),
    Column("requested_by_subject", String(SUBJECT_LENGTH), nullable=False),
    Column("requested_by_display_name", Text, nullable=True),
    _timestamp("requested_at"),
    # The collection basis the plan was written against: the identity of everything ADG had
    # collected at that moment. It moves if and only if a collector has written something,
    # which is what lets a plan be told it is stale as a fact rather than as a guess about
    # elapsed time.
    Column("basis_token", String(SIMULATION_TOKEN_LENGTH), nullable=False),
    Column("basis_run_id", String(KEY_LENGTH), nullable=True),
    _timestamp("basis_captured_at", nullable=True),
    # The what-if whose report is this plan's blast radius. A plain reference rather than a
    # copy: the report lives in simulation_evaluations, computed by the production access
    # engine, and duplicating it here would create a second account of the same answer that
    # ages independently of the code that produces it.
    Column(
        "simulation_id",
        PgUUID(as_uuid=True),
        ForeignKey("simulations.simulation_id"),
        nullable=True,
    ),
    Column("simulation_basis_token", String(SIMULATION_TOKEN_LENGTH), nullable=True),
    Column("impact_summary", JSONB(none_as_null=True), nullable=False, server_default="{}"),
    _timestamp("submitted_at", nullable=True),
    _timestamp("decided_at", nullable=True),
    Column("approved_by_subject", String(SUBJECT_LENGTH), nullable=True),
    # What was approved, pinned. An approval that recorded neither the plan's digest nor the
    # basis would still be attached to the plan after somebody edited it and after a scan
    # moved the estate, which is how a signed change gets executed against facts nobody
    # approved.
    Column("approved_plan_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=True),
    Column("approved_basis_token", String(SIMULATION_TOKEN_LENGTH), nullable=True),
    Column("rejection_reason", Text, nullable=True),
    _timestamp("exported_at", nullable=True),
    _timestamp("invalidated_at", nullable=True),
    Column("invalidation_reason", Text, nullable=True),
    _timestamp("canceled_at", nullable=True),
    Column("canceled_by_subject", String(SUBJECT_LENGTH), nullable=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _enum_check("status", ChangePlanStatus),
    _digest_check("remediation_change_plans", "approved_plan_digest", nullable=True),
    CheckConstraint("length(btrim(title)) > 0", name="ck_remediation_plans_title_not_blank"),
    # The rule the whole phase turns on, in the database rather than only in the service: an
    # approved plan names who approved it and what they approved. A row that reached
    # 'approved' with any of the three missing would be an approval nobody could attribute.
    CheckConstraint(
        "status <> 'approved' OR ("
        "approved_by_subject IS NOT NULL AND approved_plan_digest IS NOT NULL "
        "AND approved_basis_token IS NOT NULL)",
        name="ck_remediation_plans_approval_is_attributed",
    ),
    # An export happens only after an approval, so an exported plan carries the same three.
    CheckConstraint(
        "status <> 'exported' OR (exported_at IS NOT NULL AND approved_by_subject IS NOT NULL "
        "AND approved_plan_digest IS NOT NULL)",
        name="ck_remediation_plans_export_follows_approval",
    ),
    # Separation of duties, enforced by the database and not only by the service. The service
    # checks it against the actor on the request; this checks it against the two columns, so
    # a future code path that set them directly cannot produce a self-approved plan.
    CheckConstraint(
        "approved_by_subject IS NULL OR approved_by_subject <> requested_by_subject",
        name="ck_remediation_plans_approver_is_not_the_requestor",
    ),
    CheckConstraint(
        "status <> 'rejected' OR rejection_reason IS NOT NULL",
        name="ck_remediation_plans_rejection_says_why",
    ),
    CheckConstraint(
        "status <> 'invalidated' OR invalidation_reason IS NOT NULL",
        name="ck_remediation_plans_invalidation_says_why",
    ),
    CheckConstraint(
        "status <> 'canceled' OR canceled_at IS NOT NULL",
        name="ck_remediation_plans_cancellation_has_an_instant",
    ),
    CheckConstraint(
        "jsonb_typeof(impact_summary) = 'object'",
        name="ck_remediation_plans_impact_is_an_object",
    ),
    Index("ix_remediation_plans_status", "status", "requested_at"),
    Index("ix_remediation_plans_campaign", "campaign_id", "requested_at"),
    Index("ix_remediation_plans_requestor", "requested_by_subject", "requested_at"),
    comment=(
        "A proposed change plan. ADG performs none of it: there is no write adapter, no role "
        "grants remediation:execute, and no route reaches an executor. See ADR-0035."
    ),
)


remediation_planned_changes = Table(
    "remediation_planned_changes",
    metadata,
    Column("change_id", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "plan_id",
        PgUUID(as_uuid=True),
        ForeignKey("remediation_change_plans.plan_id", ondelete="CASCADE"),
        nullable=False,
    ),
    # Execution order, from zero and contiguous. It matters for replace_with_group, whose two
    # halves must not be reordered, and it is what an exported runbook numbers its steps by --
    # so a person and an auditor reading the same plan are looking at the same step 4.
    Column("sequence_index", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("target_kind", Text, nullable=False),
    Column("target_key", String(KEY_LENGTH), nullable=False),
    Column("target_display", Text, nullable=True),
    Column("principal_sid", String(200), nullable=False),
    Column("principal_key", String(KEY_LENGTH), nullable=False),
    Column("principal_display_name", Text, nullable=True),
    # The object acted on, named by the identity the collector reported. Exactly one of the
    # two is set for every kind but replace_with_group, which sets both because it is two
    # changes that must happen together.
    Column("ace_key", String(KEY_LENGTH), nullable=True),
    Column("group_key", String(KEY_LENGTH), nullable=True),
    Column("member_key", String(KEY_LENGTH), nullable=True),
    Column("edge_kind", Text, nullable=True),
    # The state the change was written against, frozen and digested. The digest is the
    # precondition: at export time ADG re-reads the object and refuses if what it finds is not
    # byte-identical, so an entry somebody widened between the review and the change window
    # stops the instruction instead of being narrowed from a mask nobody measured.
    Column("before_state", JSONB(none_as_null=True), nullable=False),
    Column("before_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=False),
    Column("after_access_mask", BigInteger, nullable=True),
    Column("after_permission", Text, nullable=True),
    Column("replacement_group_key", String(KEY_LENGTH), nullable=True),
    Column("replacement_group_sid", String(200), nullable=True),
    Column("replacement_group_display_name", Text, nullable=True),
    # Provenance. All four nullable and all four carried into the plan digest: an approver
    # told that a change comes from a signed-off access review approved that claim too, and
    # swapping the citation afterwards would change what the plan asserts without changing
    # what it does.
    Column("item_id", PgUUID(as_uuid=True), ForeignKey("review_items.item_id"), nullable=True),
    Column(
        "decision_id",
        PgUUID(as_uuid=True),
        ForeignKey("review_decisions.decision_id"),
        nullable=True,
    ),
    Column(
        "proposal_id",
        PgUUID(as_uuid=True),
        ForeignKey("remediation_proposals.proposal_id"),
        nullable=True,
    ),
    # By key rather than by foreign key: a finding is closed and its row can be pruned, and a
    # plan that lost its citation when the finding was tidied away would be a plan whose
    # reason had disappeared.
    Column("risk_finding_key", String(FINDING_KEY_LENGTH), nullable=True),
    Column("notes", Text, nullable=True),
    _timestamp("created_at"),
    _enum_check("kind", PlannedChangeKind),
    _enum_check("target_kind", ChangeTargetKind),
    _enum_check("edge_kind", MembershipEdgeKind, nullable=True),
    _enum_check("after_permission", SharePermission, nullable=True),
    _digest_check("remediation_planned_changes", "before_digest"),
    CheckConstraint("sequence_index >= 0", name="ck_remediation_changes_index_non_negative"),
    CheckConstraint(
        f"after_access_mask IS NULL OR after_access_mask BETWEEN 0 AND {MAX_ACCESS_MASK_VALUE}",
        name="ck_remediation_changes_mask_is_32_bit",
    ),
    # A membership change names an edge; everything else names an entry. Checked here rather
    # than only in the dataclass, because a row with neither would be a step whose target
    # nothing could resolve and whose precondition nothing could evaluate.
    CheckConstraint(
        "(kind = 'remove_group_member') = (ace_key IS NULL)",
        name="ck_remediation_changes_membership_has_no_entry",
    ),
    CheckConstraint(
        "kind <> 'remove_group_member' OR (group_key IS NOT NULL AND member_key IS NOT NULL "
        "AND edge_kind IS NOT NULL)",
        name="ck_remediation_changes_membership_names_its_edge",
    ),
    # Replacing a direct permission with group-based access has two halves, and a row with
    # only the first would remove the access and grant nothing back.
    CheckConstraint(
        "kind <> 'replace_with_group' OR (group_key IS NOT NULL AND member_key IS NOT NULL "
        "AND replacement_group_key IS NOT NULL AND replacement_group_sid IS NOT NULL)",
        name="ck_remediation_changes_replacement_names_its_group",
    ),
    # A removal has no resulting state; a modification must have one. A removal row carrying a
    # resulting mask would read as a modification to whoever performed it.
    CheckConstraint(
        "kind NOT IN ('remove_ntfs_ace', 'remove_share_ace', 'remove_group_member', "
        "'replace_with_group') OR (after_access_mask IS NULL AND after_permission IS NULL)",
        name="ck_remediation_changes_removals_have_no_resulting_state",
    ),
    CheckConstraint(
        "kind <> 'modify_ntfs_ace' OR after_access_mask IS NOT NULL",
        name="ck_remediation_changes_ntfs_modification_says_what_remains",
    ),
    CheckConstraint(
        "kind <> 'modify_share_ace' OR (after_access_mask IS NOT NULL) <> "
        "(after_permission IS NOT NULL)",
        name="ck_remediation_changes_share_modification_sets_one_form",
    ),
    CheckConstraint(
        "jsonb_typeof(before_state) = 'object'",
        name="ck_remediation_changes_before_state_is_an_object",
    ),
    # One step per position, and one step per object. Two steps on one object conflict, and
    # the second was written against the state the first one leaves behind -- which the
    # precondition check cannot see, because it compares against what ADG observed.
    UniqueConstraint("plan_id", "sequence_index", name="uq_remediation_changes_position"),
    Index("ix_remediation_changes_plan", "plan_id", "sequence_index"),
    Index("ix_remediation_changes_target", "target_kind", "target_key"),
    Index("ix_remediation_changes_item", "item_id"),
    comment=(
        "One precise change, with the state it was written against and where it came from. "
        "The before-state digest is the precondition re-checked before any export."
    ),
)


remediation_approvals = Table(
    "remediation_approvals",
    metadata,
    Column("approval_id", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "plan_id",
        PgUUID(as_uuid=True),
        ForeignKey("remediation_change_plans.plan_id"),
        nullable=False,
    ),
    Column("decision", Text, nullable=False),
    Column("approver_subject", String(SUBJECT_LENGTH), nullable=False),
    Column("approver_display_name", Text, nullable=True),
    # The roles held at the moment. "Dana approved this" is a weaker claim than "Dana, who
    # then held the remediation_approver role, approved this", and the second is the one an
    # audit needs after Dana's assignments have changed.
    Column("approver_roles", JSONB(none_as_null=True), nullable=False, server_default="[]"),
    _timestamp("decided_at"),
    Column("rationale", Text, nullable=True),
    # The two halves of "what did you approve". Both NOT NULL: an approval that recorded
    # neither would be a signature on nothing in particular.
    Column("plan_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=False),
    Column("basis_token", String(SIMULATION_TOKEN_LENGTH), nullable=False),
    # The blast-radius report in front of the approver when they answered, so that "approved
    # without anybody looking at the impact" is a question the data can answer.
    Column(
        "simulation_id",
        PgUUID(as_uuid=True),
        ForeignKey("simulations.simulation_id"),
        nullable=True,
    ),
    _timestamp("created_at"),
    _enum_check("decision", ApprovalDecision),
    _digest_check("remediation_approvals", "plan_digest"),
    CheckConstraint(
        "decision <> 'reject' OR rationale IS NOT NULL",
        name="ck_remediation_approvals_rejection_says_why",
    ),
    # One answer per approver per plan digest. A second answer to the same question is either
    # a duplicate submission or a changed mind, and a changed mind about an approval is not
    # expressible: an approved plan is exported or invalidated, never re-approved.
    UniqueConstraint(
        "plan_id",
        "approver_subject",
        "plan_digest",
        name="uq_remediation_approvals_one_answer_per_digest",
    ),
    Index("ix_remediation_approvals_plan", "plan_id", "decided_at"),
    comment=(
        "One approver's answer, pinned to the plan digest and the collection basis they "
        "answered about. Append-only in practice: nothing updates a row here."
    ),
)


remediation_exports = Table(
    "remediation_exports",
    metadata,
    Column("export_id", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "plan_id",
        PgUUID(as_uuid=True),
        ForeignKey("remediation_change_plans.plan_id"),
        nullable=False,
    ),
    Column("exported_by_subject", String(SUBJECT_LENGTH), nullable=False),
    Column("exported_by_display_name", Text, nullable=True),
    _timestamp("exported_at"),
    # The signed bytes, as an object. Stored rather than regenerated on read: a signature
    # covers a specific serialization, and re-deriving the document later -- after a display
    # name changed, after a field was added -- would produce bytes the signature does not
    # cover and a verification failure nobody could explain.
    Column("document", JSONB(none_as_null=True), nullable=False),
    Column("document_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=False),
    Column("signature", Text, nullable=False),
    Column("signature_algorithm", Text, nullable=False),
    # Which key signed it, as a digest prefix of the key itself. Rotating the key necessarily
    # changes this, so a verifier holding the old one can say "signed with a key I do not
    # have" instead of reporting tampering that did not happen.
    Column("signature_key_id", String(32), nullable=False),
    Column("basis_token", String(SIMULATION_TOKEN_LENGTH), nullable=False),
    # The head of the plan's audit chain at the moment of export. Worth recording outside the
    # database -- in the change ticket, in an email -- because it is what a later verification
    # is compared against.
    Column("audit_head_digest", String(GOVERNANCE_DIGEST_LENGTH), nullable=True),
    _timestamp("created_at"),
    _digest_check("remediation_exports", "document_digest"),
    _digest_check("remediation_exports", "audit_head_digest", nullable=True),
    CheckConstraint(
        "length(btrim(signature)) > 0", name="ck_remediation_exports_signature_not_blank"
    ),
    CheckConstraint(
        "jsonb_typeof(document) = 'object'", name="ck_remediation_exports_document_is_an_object"
    ),
    Index("ix_remediation_exports_plan", "plan_id", "exported_at"),
    comment=(
        "A signed change plan as handed to a human administrator. Exporting twice writes two "
        "rows: the document somebody is holding is the one signed at that moment."
    ),
)
