"""Contract payloads to database rows, with no database in sight.

Every key written here is derived by asking the domain object for it —
``Principal.identity_key``, ``MembershipEdge.identity_key`` — rather than by formatting a
string. That is not style: the contract's ``source_key`` derivations in
:mod:`app.contracts.v1.keys` are built from the same domain objects, so deriving storage
keys the same way is what guarantees the two can never drift into describing different
objects.

Phase 1B stores the two AD observation kinds. The other five are valid contract payloads
that this endpoint cannot yet persist, and they are **rejected with an actionable error**
rather than accepted and dropped: a collector told "accepted" about an observation that
was discarded would report coverage ADG does not have.
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
    UnresolvedPrincipal,
)
from app.models.schema import AliasKind

SUPPORTED_KINDS: Final[frozenset[str]] = frozenset(
    {ObservationKind.PRINCIPAL.value, ObservationKind.MEMBERSHIP_EDGE.value}
)
"""What this phase can persist. The rest of contract v1 arrives with the SMB/NTFS phases."""

__all__ = [
    "SUPPORTED_KINDS",
    "AliasRow",
    "BatchPlan",
    "EdgeRow",
    "ObservationRow",
    "PrincipalRow",
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
        super().__init__(
            f"This endpoint stores {' and '.join(sorted(SUPPORTED_KINDS))} observations; the "
            f"batch also contained: {listed}. Those kinds are valid contract v1 payloads but "
            "are persisted by a later phase. Send them in their own run once that phase "
            "ships; they are rejected rather than dropped so that a collector is never told "
            "an observation was stored when it was not.",
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
