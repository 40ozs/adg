"""Contract payloads to database rows, with no database in sight.

Every key written here is derived by asking the domain object for it —
``Principal.identity_key``, ``MembershipEdge.identity_key`` — rather than by formatting a
string. That is not style: the contract's ``source_key`` derivations in
:mod:`app.contracts.v1.keys` are built from the same domain objects, so deriving storage
keys the same way is what guarantees the two can never drift into describing different
objects.

Phase 1B stored the two AD observation kinds; Phase 2B adds ``server``, ``smb_share``, and
``smb_ace``. The two NTFS kinds are valid contract payloads that this endpoint cannot yet
persist, and they are **rejected with an actionable error** rather than accepted and
dropped: a collector told "accepted" about an observation that was discarded would report
coverage ADG does not have.

Two rules govern the SMB rows specifically:

* **A trustee SID becomes a principal key here, not at query time.** The rule — host-scope
  a BUILTIN SID, leave every other SID global — lives in
  :func:`app.domain.referenced_principal_key`, which is also what a membership edge uses,
  so an ACE and a local-group edge naming the same trustee land on the same key.
* **Nothing records whether that principal is known.** Resolution is a join against
  ``principals`` at query time. A stored "resolved" flag would be right only until the next
  AD run described the SID, and an audit tool reporting a resolved account as an orphan is
  as wrong as the reverse.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from app.contracts.v1 import (
    MembershipObservation,
    ObservationBatch,
    PrincipalObservation,
    ServerObservation,
    SmbAceObservation,
    SmbShareObservation,
    SourceDescriptor,
)
from app.contracts.v1.common import ObservationKind
from app.domain import (
    DomainValidationError,
    GroupScope,
    GroupType,
    LocalGroup,
    MembershipEdge,
    Principal,
    PrincipalKind,
    Server,
    Sid,
    SmbShare,
    SmbShareAce,
    UnresolvedPrincipal,
    referenced_principal_key,
)
from app.models.schema import AliasKind, ReferenceKind

SUPPORTED_KINDS: Final[frozenset[str]] = frozenset(
    {
        ObservationKind.PRINCIPAL.value,
        ObservationKind.MEMBERSHIP_EDGE.value,
        ObservationKind.SERVER.value,
        ObservationKind.SMB_SHARE.value,
        ObservationKind.SMB_ACE.value,
    }
)
"""What this phase can persist. The two NTFS kinds arrive with the file-system phase."""

__all__ = [
    "SUPPORTED_KINDS",
    "AliasRow",
    "BatchPlan",
    "EdgeRow",
    "ObservationRow",
    "PrincipalReferenceRow",
    "PrincipalRow",
    "ServerRow",
    "ShareAceRow",
    "ShareRow",
    "UnsupportedObservationKind",
    "plan_batch",
    "source_fingerprint",
]


class UnsupportedObservationKind(DomainValidationError):
    """A batch carried an observation kind this phase cannot store.

    Carries the offending kinds so the API can name them in a 422 rather than failing with
    "invalid payload", which would tell a collector author nothing.
    """

    def __init__(self, kinds: Iterable[str]) -> None:
        self.kinds = tuple(sorted(set(kinds)))
        listed = ", ".join(self.kinds)
        supported = ", ".join(sorted(SUPPORTED_KINDS))
        super().__init__(
            f"This endpoint stores these observation kinds: {supported}. The batch also "
            f"contained: {listed}. Those kinds are valid contract v1 payloads but are "
            "persisted by a later phase. Send them in their own run once that phase ships; "
            "they are rejected rather than dropped so that a collector is never told an "
            "observation was stored when it was not.",
            value=listed,
            field="observations",
        )


@dataclass(frozen=True, slots=True)
class PrincipalRow:
    """One row of ``principals``, plus the run that justifies it."""

    principal_key: str
    sid: str
    principal_kind: str
    host_key: str | None
    domain_sid: str | None
    display_name: str | None
    sam_account_name: str | None
    user_principal_name: str | None
    distinguished_name: str | None
    group_scope: str | None
    group_type: str | None
    enabled: bool | None
    is_deleted: bool
    unresolved_reason: str | None
    last_known_name: str | None
    source_key: str
    observed_at: dt.datetime
    run_id: UUID


@dataclass(frozen=True, slots=True)
class AliasRow:
    """One observed name for a principal. Metadata, never identity (ADR-0001)."""

    principal_key: str
    alias_kind: str
    value: str
    value_folded: str
    observed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class EdgeRow:
    """One row of ``membership_edges``."""

    edge_key: str
    group_key: str
    member_key: str
    group_sid: str
    member_sid: str
    edge_kind: str
    host_key: str | None
    member_kind: str | None
    is_foreign_security_principal: bool
    source_key: str
    observed_at: dt.datetime
    run_id: UUID


@dataclass(frozen=True, slots=True)
class ServerRow:
    """One row of ``servers``."""

    server_key: str
    name: str
    dns_host_name: str | None
    netbios_name: str | None
    computer_sid: str | None
    domain_sid: str | None
    is_domain_member: bool | None
    operating_system: str | None
    source_key: str
    observed_at: dt.datetime
    run_id: UUID


@dataclass(frozen=True, slots=True)
class ShareRow:
    """One row of ``smb_shares``.

    No UNC path and no ``is_hidden``: both are exact functions of the server and share
    names (:attr:`app.domain.SmbShare.unc_path`, :attr:`app.domain.SmbShare.is_hidden`), and
    a stored copy of a derived value is a second version of the truth that can disagree
    with the first.
    """

    share_key: str
    server_key: str
    name: str
    local_path: str | None
    share_type: str
    description: str | None
    concurrent_user_limit: int | None
    caching_mode: str | None
    is_special: bool | None
    source_key: str
    observed_at: dt.datetime
    run_id: UUID


@dataclass(frozen=True, slots=True)
class ShareAceRow:
    """One row of ``smb_share_aces``: a share-level ACE exactly as the source reported it."""

    ace_key: str
    share_key: str
    trustee_sid: str
    trustee_key: str
    ace_type: str
    access_mask: int | None
    permission: str | None
    right_token: str
    order_index: int | None
    source_key: str
    observed_at: dt.datetime
    run_id: UUID


@dataclass(frozen=True, slots=True)
class PrincipalReferenceRow:
    """One row of ``principal_references``: a resource named this principal.

    Carries no resolution. Whether ``principal_key`` has a ``principals`` row is a join, and
    stating it here would freeze an answer that the next AD run can change.
    """

    principal_key: str
    sid: str
    host_key: str | None
    reference_kind: str
    reference_key: str
    observed_at: dt.datetime
    run_id: UUID


@dataclass(frozen=True, slots=True)
class ObservationRow:
    """Provenance: this run, in this batch, saw this object at this instant."""

    run_id: UUID
    source_key: str
    kind: str
    batch_id: UUID
    observed_at: dt.datetime
    subject_key: str


@dataclass(frozen=True, slots=True)
class BatchPlan:
    """Everything one batch will write, already deduplicated and ordered."""

    run_id: UUID
    batch_id: UUID
    sequence: int
    is_final: bool
    continuation_token: str | None
    principals: tuple[PrincipalRow, ...]
    aliases: tuple[AliasRow, ...]
    edges: tuple[EdgeRow, ...]
    servers: tuple[ServerRow, ...]
    shares: tuple[ShareRow, ...]
    share_aces: tuple[ShareAceRow, ...]
    references: tuple[PrincipalReferenceRow, ...]
    observations: tuple[ObservationRow, ...]

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def is_empty(self) -> bool:
        return not self.observations


def source_fingerprint(source: SourceDescriptor) -> str:
    """A stable identity for one collector source tuple.

    A hash rather than the concatenated fields because ``target`` may be a 1024-character
    UNC path, which overflows a btree index row. The separator is a character no field may
    contain, so distinct tuples cannot collide by concatenation.
    """
    parts = (
        source.collector.value,
        source.collector_host.casefold(),
        source.method,
        source.collector_version or "",
        (source.target or "").casefold(),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def plan_batch(batch: ObservationBatch) -> BatchPlan:
    """Convert a validated batch into the rows it will write.

    Raises:
        UnsupportedObservationKind: if the batch carries a kind this phase cannot store.
        DomainValidationError: if an observation is internally inconsistent in a way the
            domain layer rejects. The contract models catch most of this at parse time;
            this is the backstop.
    """
    unsupported = {
        observation.kind
        for observation in batch.observations
        if observation.kind not in SUPPORTED_KINDS
    }
    if unsupported:
        raise UnsupportedObservationKind(unsupported)

    run_id = UUID(batch.run_id)
    batch_id = UUID(batch.batch_id)

    principals: dict[str, PrincipalRow] = {}
    aliases: dict[tuple[str, str, str], AliasRow] = {}
    edges: dict[str, EdgeRow] = {}
    servers: dict[str, ServerRow] = {}
    shares: dict[str, ShareRow] = {}
    share_aces: dict[str, ShareAceRow] = {}
    references: dict[tuple[str, str, str], PrincipalReferenceRow] = {}
    observations: list[ObservationRow] = []

    for observation in batch.observations:
        if isinstance(observation, PrincipalObservation):
            row = _principal_row(observation, run_id)
            # Two observations in one batch cannot share a source_key (the envelope rejects
            # that), and a principal's key is a function of its source_key, so a collision
            # here is impossible. Keeping the newest anyway costs nothing and means a future
            # relaxation of that rule cannot produce a "cannot affect row a second time"
            # failure deep inside the upsert.
            existing = principals.get(row.principal_key)
            if existing is None or row.observed_at >= existing.observed_at:
                principals[row.principal_key] = row
            for alias in _alias_rows(observation, row):
                key = (alias.principal_key, alias.alias_kind, alias.value_folded)
                previous = aliases.get(key)
                if previous is None or alias.observed_at >= previous.observed_at:
                    aliases[key] = alias
            subject_key = row.principal_key
        elif isinstance(observation, MembershipObservation):
            edge_row = _edge_row(observation, run_id)
            previous_edge = edges.get(edge_row.edge_key)
            if previous_edge is None or edge_row.observed_at >= previous_edge.observed_at:
                edges[edge_row.edge_key] = edge_row
            subject_key = edge_row.edge_key
        elif isinstance(observation, ServerObservation):
            server_row = _server_row(observation, run_id)
            previous_server = servers.get(server_row.server_key)
            if previous_server is None or server_row.observed_at >= previous_server.observed_at:
                servers[server_row.server_key] = server_row
            subject_key = server_row.server_key
        elif isinstance(observation, SmbShareObservation):
            share_row = _share_row(observation, run_id)
            previous_share = shares.get(share_row.share_key)
            if previous_share is None or share_row.observed_at >= previous_share.observed_at:
                shares[share_row.share_key] = share_row
            subject_key = share_row.share_key
        elif isinstance(observation, SmbAceObservation):
            ace_row = _share_ace_row(observation, run_id)
            previous_ace = share_aces.get(ace_row.ace_key)
            if previous_ace is None or ace_row.observed_at >= previous_ace.observed_at:
                share_aces[ace_row.ace_key] = ace_row
            # One reference row per (principal, share) however many ACEs name the trustee:
            # three entries for one group is one group that can see the share.
            reference = _reference_row(ace_row, observation.server_name, run_id)
            reference_id = (
                reference.principal_key,
                reference.reference_kind,
                reference.reference_key,
            )
            previous_reference = references.get(reference_id)
            if (
                previous_reference is None
                or reference.observed_at >= previous_reference.observed_at
            ):
                references[reference_id] = reference
            subject_key = ace_row.ace_key
        else:  # pragma: no cover - SUPPORTED_KINDS already excluded everything else
            raise UnsupportedObservationKind([observation.kind])

        observations.append(
            ObservationRow(
                run_id=run_id,
                source_key=observation.source_key,
                kind=observation.kind,
                batch_id=batch_id,
                observed_at=observation.observed_at,
                subject_key=subject_key,
            )
        )

    return BatchPlan(
        run_id=run_id,
        batch_id=batch_id,
        sequence=batch.sequence,
        is_final=batch.is_final,
        continuation_token=batch.continuation_token,
        principals=tuple(sorted(principals.values(), key=lambda item: item.principal_key)),
        aliases=tuple(
            sorted(
                aliases.values(),
                key=lambda item: (item.principal_key, item.alias_kind, item.value_folded),
            )
        ),
        edges=tuple(sorted(edges.values(), key=lambda item: item.edge_key)),
        servers=tuple(sorted(servers.values(), key=lambda item: item.server_key)),
        shares=tuple(sorted(shares.values(), key=lambda item: item.share_key)),
        share_aces=tuple(sorted(share_aces.values(), key=lambda item: item.ace_key)),
        references=tuple(
            sorted(
                references.values(),
                key=lambda item: (item.principal_key, item.reference_kind, item.reference_key),
            )
        ),
        observations=tuple(observations),
    )


def _principal_row(observation: PrincipalObservation, run_id: UUID) -> PrincipalRow:
    principal: Principal = observation.to_domain()
    # identity_key is the domain's answer, and LocalGroup overrides it to fold in the host.
    # Asking the object beats re-deriving the rule in two places.
    principal_key = principal.identity_key
    host_key = principal.host_key.casefold() if isinstance(principal, LocalGroup) else None

    group_scope: str | None = None
    group_type: str | None = None
    if principal.kind is PrincipalKind.DOMAIN_GROUP:
        scope = observation.group_scope or GroupScope.UNKNOWN
        kind = observation.group_type or GroupType.UNKNOWN
        group_scope, group_type = scope.value, kind.value

    # to_domain() already defaults a missing reason to UNKNOWN, so asking the domain object
    # keeps "the collector named no reason" from being stored as NULL, which would read as
    # "this principal is not unresolved".
    unresolved_reason = (
        principal.reason.value if isinstance(principal, UnresolvedPrincipal) else None
    )

    return PrincipalRow(
        principal_key=principal_key,
        sid=principal.sid.value,
        principal_kind=principal.kind.value,
        host_key=host_key,
        domain_sid=(
            principal.effective_domain_sid.value if principal.effective_domain_sid else None
        ),
        display_name=observation.display_name,
        sam_account_name=observation.sam_account_name,
        user_principal_name=observation.user_principal_name,
        distinguished_name=observation.distinguished_name,
        group_scope=group_scope,
        group_type=group_type,
        enabled=observation.enabled,
        is_deleted=observation.is_deleted,
        unresolved_reason=unresolved_reason,
        last_known_name=observation.last_known_name,
        source_key=observation.source_key,
        observed_at=observation.observed_at,
        run_id=run_id,
    )


def _alias_rows(observation: PrincipalObservation, row: PrincipalRow) -> Sequence[AliasRow]:
    candidates: tuple[tuple[AliasKind, str | None], ...] = (
        (AliasKind.DISPLAY_NAME, observation.display_name),
        (AliasKind.SAM_ACCOUNT_NAME, observation.sam_account_name),
        (AliasKind.USER_PRINCIPAL_NAME, observation.user_principal_name),
        (AliasKind.DISTINGUISHED_NAME, observation.distinguished_name),
        (AliasKind.LAST_KNOWN_NAME, observation.last_known_name),
    )
    return [
        AliasRow(
            principal_key=row.principal_key,
            alias_kind=alias_kind.value,
            value=value,
            # Windows name comparison is case-insensitive, so "Finance-RW" and "finance-rw"
            # are one alias, not two.
            value_folded=value.casefold(),
            observed_at=row.observed_at,
        )
        for alias_kind, value in candidates
        if value
    ]


def _edge_row(observation: MembershipObservation, run_id: UUID) -> EdgeRow:
    edge: MembershipEdge = observation.to_domain()
    return EdgeRow(
        edge_key=edge.identity_key,
        group_key=edge.group_key,
        member_key=edge.member_key,
        group_sid=edge.group_sid.value,
        member_sid=edge.member_sid.value,
        edge_kind=edge.kind.value,
        host_key=edge.host_key.casefold() if edge.host_key else None,
        member_kind=edge.member_kind.value if edge.member_kind else None,
        is_foreign_security_principal=edge.is_foreign_security_principal,
        source_key=observation.source_key,
        observed_at=observation.observed_at,
        run_id=run_id,
    )


def _server_row(observation: ServerObservation, run_id: UUID) -> ServerRow:
    server: Server = observation.to_domain()
    return ServerRow(
        server_key=server.identity_key,
        # The name as observed, case preserved. Comparison always goes through server_key,
        # so keeping the spelling costs nothing and an operator recognizes "FS01".
        name=server.name,
        dns_host_name=server.dns_host_name,
        netbios_name=server.netbios_name,
        computer_sid=server.computer_sid.value if server.computer_sid else None,
        domain_sid=server.domain_sid.value if server.domain_sid else None,
        is_domain_member=server.is_domain_member,
        # Not part of the domain Server: it describes the machine, not its identity, and
        # nothing in the permission model may depend on it.
        operating_system=observation.operating_system,
        source_key=observation.source_key,
        observed_at=observation.observed_at,
        run_id=run_id,
    )


def _share_row(observation: SmbShareObservation, run_id: UUID) -> ShareRow:
    share: SmbShare = observation.to_domain()
    return ShareRow(
        share_key=share.identity_key,
        server_key=share.server_key.casefold(),
        name=share.name,
        local_path=share.local_path.value if share.local_path else None,
        share_type=share.share_type.value,
        description=share.description,
        concurrent_user_limit=share.concurrent_user_limit,
        # Contract fields the domain type does not model: they describe how the share is
        # served, not who can reach it.
        caching_mode=observation.caching_mode,
        is_special=observation.is_special,
        source_key=observation.source_key,
        observed_at=observation.observed_at,
        run_id=run_id,
    )


def _share_ace_row(observation: SmbAceObservation, run_id: UUID) -> ShareAceRow:
    ace: SmbShareAce = observation.to_domain()
    share_key = observation.share_identity_key
    return ShareAceRow(
        ace_key=ace.identity_key(share_key),
        share_key=share_key,
        trustee_sid=ace.trustee_sid.value,
        # The share ACL was read on this server, so a BUILTIN trustee means *this* server's
        # local group. Every other SID keeps its global key.
        trustee_key=referenced_principal_key(ace.trustee_sid, observation.server_name),
        ace_type=ace.ace_type.value,
        access_mask=ace.access_mask,
        permission=ace.permission.value if ace.permission else None,
        right_token=ace.right_token,
        order_index=ace.order_index,
        source_key=observation.source_key,
        observed_at=observation.observed_at,
        run_id=run_id,
    )


def _reference_row(ace: ShareAceRow, server_name: str, run_id: UUID) -> PrincipalReferenceRow:
    return PrincipalReferenceRow(
        principal_key=ace.trustee_key,
        sid=ace.trustee_sid,
        # Recorded only when host scoping actually applied, so the column answers "was this
        # SID interpreted in one machine's context" without re-deriving the rule.
        host_key=server_name.casefold() if Sid(ace.trustee_sid).is_builtin else None,
        reference_kind=ReferenceKind.SMB_ACE.value,
        reference_key=ace.share_key,
        observed_at=ace.observed_at,
        run_id=run_id,
    )
