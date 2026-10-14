r"""Deriving privilege relationships from stored facts, and keeping them derived.

:mod:`app.relationships.rules` says what facts establish; this module decides *which* facts to
read, reads them, and records what the rules concluded. It writes only ADG's own derived
tables (``graph_relationships``, ``graph_relationship_supports``,
``graph_relationship_evidence``, ``graph_derivation_passes``) and never a collected fact.

## The unit of derivation

A rule reads a bounded set of facts and nothing else, so every derivation is per **unit**:

* a membership edge (``member_of``, ``primary_group_of``, ``local_group_member_of``, the local
  specializations, and ``foreign_principal_of`` by edge);
* a principal (``foreign_principal_of`` by object);
* an NTFS resource — its descriptor row **and every one of its current entries**, because
  whether one entry establishes a right depends on the Deny entries around it.

A pass re-derives a set of units from current state and then, for each unit, compares what the
rules produced with every support already stored for it:

* produced and stored → recorded again (extended, replayed, or reinstated if it had been
  retired);
* produced and not stored → created;
* stored and not produced, with **every premise still present** → **retired**: the facts are
  there and, as they now read, no longer establish it (an owner changed, a Deny appeared ahead
  of the grant);
* stored and not produced because a premise is gone → left alone. A tombstoned premise already
  makes the support not current (``app.models.current``), and a premise with no row at all
  makes it unknown — retiring it would turn "nobody said it is gone" into "it is gone".

So a reconciliation never needs the projector to take a relationship away, and a fact that
comes back makes its support current again without one either. The projector's work is the
part currency cannot read off a row: new facts, and facts that changed in place.

## When it runs

At **run completion**, inside the completion's transaction and after its reconciliation
(:func:`project_completed_run`), over every unit the run **changed** — every fact for which it
opened a version: new, changed, revived or tombstoned. A fact it merely observed again changes
no derivation; its supports' observation windows are widened in SQL instead
(:meth:`RelationshipRepository.extend_observed`), so a re-scan of an unchanged estate costs a
handful of statements rather than a re-derivation of everything. That is the history model's
own split between a change and a confirmation, and the point at which the run's facts and
their presence are final, so the graph converges exactly when the run's own state does.

A rule revision therefore takes effect on facts as they change, and everywhere at once only
after a rebuild; and an installation upgrading to the projector holds facts no pass has
derived yet. Both are what :meth:`RelationshipProjector.rebuild` is for, and
:meth:`RelationshipProjector.state` lists every run no pass has covered.

The pass runs in a **savepoint**. If it fails, nothing it wrote survives, a failed pass is
recorded instead, and the completion still commits: a projector defect must never cost the
estate an observation (ADR-0003), and a run whose derivation failed is listed by
:meth:`RelationshipProjector.state` until a later pass covers it. A replayed completion retries
a run that has no successful pass.

Passes are serialized by a transaction-scoped advisory lock, so two runs completing at once
cannot interleave their retirement decisions.

## Rebuild

:meth:`RelationshipProjector.rebuild` re-derives every unit in the estate with the same rules,
for recovery, for a new rule version, and to check that incremental derivation and a clean
recomputation agree. It reads facts and never writes them.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.access import AceSource, AceType
from app.domain.errors import DomainValidationError
from app.domain.identity import PrincipalKind
from app.domain.membership import MembershipEdgeKind
from app.domain.paths import parse_unc_path
from app.domain.relationships import (
    EvidenceKind,
    NodeKind,
    ProjectionStatus,
    ProjectionTrigger,
    RelationshipAssertion,
    RelationshipCurrency,
    RelationshipType,
)
from app.models.current import (
    current_membership_edges,
    current_ntfs_aces,
    current_ntfs_resources,
    current_principals,
)
from app.models.schema import (
    graph_derivation_passes,
    graph_relationship_evidence,
    graph_relationships,
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    object_versions,
    observations,
    principals,
    scan_runs,
)
from app.relationships.rules import (
    CONTROL_RIGHTS,
    RULES,
    AceFact,
    Derivation,
    DerivationContext,
    EdgeFact,
    FactReading,
    PrincipalFact,
    ResourceFact,
    SkippedUnit,
    derive_edge,
    derive_principal,
    derive_resource,
    ordered_entries,
)
from app.repositories.basis import CollectionBasisRepository
from app.repositories.relationships import RecordOutcome, RelationshipRepository

__all__ = [
    "PROJECTION_LOCK",
    "DerivationUnits",
    "ProjectionOutcome",
    "ProjectionState",
    "RelationshipProjector",
    "project_completed_run",
]

logger = logging.getLogger("adg.graph")

PROJECTION_LOCK: Final = 4_190_871_552_018_417_667
"""``pg_advisory_xact_lock`` key serializing projector passes. Any fixed 64-bit value would do;
this one is used for nothing else in ADG."""

EDGE_CHUNK: Final = 2000
PRINCIPAL_CHUNK: Final = 2000
RESOURCE_CHUNK: Final = 250
"""Units per read. A resource unit reads its whole DACL, so its chunk is smaller."""

SKIPS_LOGGED: Final = 50

RESOURCE_TYPES: Final = frozenset({RelationshipType.OWNS, *CONTROL_RIGHTS})
"""Every type a resource unit derives, which is every type that targets an NTFS resource."""

_RULES_RUN: Final = tuple(sorted(str(spec.rule) for spec in RULES.values()))


@dataclass(frozen=True, slots=True)
class DerivationUnits:
    """Which facts a pass re-derives, by unit kind."""

    edges: frozenset[str] = frozenset()
    principals: frozenset[str] = frozenset()
    resources: frozenset[str] = frozenset()

    def __len__(self) -> int:
        return len(self.edges) + len(self.principals) + len(self.resources)

    def __or__(self, other: DerivationUnits) -> DerivationUnits:
        return DerivationUnits(
            self.edges | other.edges,
            self.principals | other.principals,
            self.resources | other.resources,
        )


@dataclass(frozen=True, slots=True)
class ProjectionOutcome:
    """What one pass did. Mirrors its ``graph_derivation_passes`` row."""

    pass_id: UUID
    trigger: ProjectionTrigger
    status: ProjectionStatus
    source_run_id: UUID | None
    basis_token: str | None
    units_examined: int = 0
    relationships_created: int = 0
    outcomes: Mapping[RecordOutcome, int] = field(default_factory=lambda: MappingProxyType({}))
    supports_retired: int = 0
    skipped: tuple[SkippedUnit, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is ProjectionStatus.SUCCEEDED

    def count(self, outcome: RecordOutcome) -> int:
        return self.outcomes.get(outcome, 0)

    @property
    def summary(self) -> dict[str, Any]:
        """A flat rendering for a log line."""
        return {
            "pass_id": str(self.pass_id),
            "status": self.status.value,
            "units": self.units_examined,
            "skipped": len(self.skipped),
            "relationships_created": self.relationships_created,
            **{f"supports_{outcome.value}": count for outcome, count in self.outcomes.items()},
            "supports_retired": self.supports_retired,
            **({"error": self.error} if self.error else {}),
        }


@dataclass(frozen=True, slots=True)
class ProjectionState:
    """Whether the graph is derived from everything collected, as the pass log shows."""

    latest_pass: Mapping[str, Any] | None
    underived_runs: tuple[UUID, ...]
    """Completed runs no successful pass has covered: neither their own completion's pass nor a
    rebuild that started after their last write. Their facts may be newer than the graph."""

    runs_in_progress: tuple[UUID, ...]
    """Runs still collecting. Their facts are stored and not yet derived, by design."""

    @property
    def is_current(self) -> bool:
        return not self.underived_runs and not self.runs_in_progress


class RelationshipProjector:
    """Derives relationships over one session. Commits nothing; the caller owns that."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._relationships = RelationshipRepository(session)

    # --- Which units ---------------------------------------------------------------------

    async def changed_by_run(self, run_id: UUID) -> DerivationUnits:
        """Every unit with a fact this run *changed*: a version it opened.

        New, changed and revived facts open a version, and so does a tombstone a
        reconciliation writes; a fact observed again unchanged only extends one, and changes
        no derivation (:meth:`RelationshipRepository.extend_observed` widens its windows).
        This is the history model's own notion of a change, the one the incremental risk pass
        reads (``RiskFactsRepository.changed_by_run``).

        Two reads, both indexed: the run's own observations joined to each fact's open
        version (``observations`` by run, ``ux_object_versions_open``), which finds a change
        even after a later run re-observed the fact; and the versions whose last confirmation
        is this run, which finds what its reconciliation tombstoned.
        """
        versions = object_versions
        observed = (
            sa.select(versions.c.object_kind, versions.c.object_key, versions.c.container_key)
            .select_from(
                observations.join(
                    versions,
                    sa.and_(
                        versions.c.object_kind == observations.c.kind,
                        versions.c.object_key == observations.c.subject_key,
                        versions.c.valid_to.is_(None),
                    ),
                )
            )
            .where(observations.c.run_id == run_id, versions.c.opened_by_run_id == run_id)
        )
        reconciled = sa.select(
            versions.c.object_kind, versions.c.object_key, versions.c.container_key
        ).where(versions.c.last_seen_run_id == run_id, versions.c.opened_by_run_id == run_id)

        edges: set[str] = set()
        principal_keys: set[str] = set()
        resources: set[str] = set()
        for statement in (observed, reconciled):
            for kind, key, container in await self._session.execute(statement):
                if kind == EvidenceKind.MEMBERSHIP_EDGE.value:
                    edges.add(key)
                elif kind == EvidenceKind.PRINCIPAL.value:
                    principal_keys.add(key)
                elif kind == EvidenceKind.NTFS_RESOURCE.value:
                    resources.add(key)
                elif kind == EvidenceKind.NTFS_ACE.value and container:
                    resources.add(container)
        return DerivationUnits(frozenset(edges), frozenset(principal_keys), frozenset(resources))

    async def every_unit(self) -> DerivationUnits:
        """Every unit a stored fact or a stored relationship names: the scope of a rebuild.

        Units a relationship names are included even when their facts are gone, so a rebuild
        reaches every support that could need retiring, exactly as an incremental pass would.
        """
        evidence = graph_relationship_evidence

        async def keys(*statements: sa.Select[Any]) -> frozenset[str]:
            found: set[str] = set()
            for statement in statements:
                found.update((await self._session.execute(statement)).scalars())
            return frozenset(found)

        def evidence_of(kind: EvidenceKind) -> sa.Select[Any]:
            return sa.select(evidence.c.evidence_key).where(evidence.c.evidence_kind == kind.value)

        return DerivationUnits(
            edges=await keys(
                sa.select(membership_edges.c.edge_key), evidence_of(EvidenceKind.MEMBERSHIP_EDGE)
            ),
            principals=await keys(
                sa.select(principals.c.principal_key).where(
                    principals.c.principal_kind == PrincipalKind.FOREIGN_SECURITY_PRINCIPAL.value
                ),
                evidence_of(EvidenceKind.PRINCIPAL),
            ),
            resources=await keys(
                sa.select(ntfs_resources.c.resource_key),
                sa.select(ntfs_aces.c.resource_key).distinct(),
                sa.select(graph_relationships.c.target_key).where(
                    graph_relationships.c.target_kind == NodeKind.NTFS_RESOURCE.value
                ),
            ),
        )

    # --- Passes --------------------------------------------------------------------------

    async def derive_run(self, run_id: UUID, *, now: dt.datetime) -> ProjectionOutcome:
        """Re-derive what one run changed, and confirm what it re-observed unchanged.

        Runs inside the caller's transaction. The changed units are derived in full (records,
        retirements, reinstatements); the rest of what the run observed only widens windows,
        in SQL, after them, so a support the full derivation just retired is not confirmed.
        """
        await self._lock()
        units = await self.changed_by_run(run_id)
        return await self._derive(
            units,
            now=now,
            trigger=ProjectionTrigger.RUN_COMPLETION,
            source_run_id=run_id,
            confirm_run=run_id,
        )

    async def rebuild(
        self, *, now: dt.datetime, discard_existing: bool = False
    ) -> ProjectionOutcome:
        """Re-derive every unit in the estate from current facts.

        ``discard_existing`` first deletes every derived relationship, which is a clean
        recomputation: what survives is exactly what the rules produce from the facts now
        stored, and the record of supports that were retired is lost. Without it, a rebuild
        is an ordinary pass over everything, idempotent over unchanged facts.
        """
        await self._lock()
        if discard_existing:
            await self._session.execute(sa.delete(graph_relationships))
        units = await self.every_unit()
        return await self._derive(
            units, now=now, trigger=ProjectionTrigger.REBUILD, source_run_id=None
        )

    async def record_failure(
        self,
        *,
        pass_id: UUID,
        trigger: ProjectionTrigger,
        source_run_id: UUID | None,
        started_at: dt.datetime,
        error: str,
    ) -> None:
        """Record a pass that wrote nothing. Called in a fresh savepoint after the failure."""
        basis = (await CollectionBasisRepository(self._session).current()).token
        await self._session.execute(
            sa.insert(graph_derivation_passes).values(
                pass_id=pass_id,
                trigger=trigger.value,
                source_run_id=source_run_id,
                status=ProjectionStatus.FAILED.value,
                basis_token=basis,
                rules=list(_RULES_RUN),
                error=error[:4000],
                started_at=started_at,
                completed_at=max(started_at, dt.datetime.now(tz=dt.UTC)),
            )
        )

    async def has_succeeded_for(self, run_id: UUID) -> bool:
        passes = graph_derivation_passes
        found = (
            await self._session.execute(
                sa.select(passes.c.pass_id)
                .where(
                    passes.c.source_run_id == run_id,
                    passes.c.status == ProjectionStatus.SUCCEEDED.value,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return found is not None

    async def state(self) -> ProjectionState:
        """Whether every collected run has been derived, read from the pass log."""
        passes = graph_derivation_passes
        latest = (
            (
                await self._session.execute(
                    sa.select(passes).order_by(passes.c.completed_at.desc()).limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        succeeded = passes.c.status == ProjectionStatus.SUCCEEDED.value
        covered = sa.exists().where(
            succeeded,
            sa.or_(
                passes.c.source_run_id == scan_runs.c.run_id,
                sa.and_(
                    passes.c.trigger == ProjectionTrigger.REBUILD.value,
                    passes.c.started_at >= scan_runs.c.updated_at,
                ),
            ),
        )
        underived = (
            await self._session.execute(
                sa.select(scan_runs.c.run_id)
                .where(scan_runs.c.completed_at.is_not(None), ~covered)
                .order_by(scan_runs.c.completed_at, scan_runs.c.run_id)
            )
        ).scalars()
        underived_runs = tuple(underived)
        in_progress = (
            await self._session.execute(
                sa.select(scan_runs.c.run_id)
                .where(scan_runs.c.completed_at.is_(None), ~covered)
                .order_by(scan_runs.c.started_at, scan_runs.c.run_id)
            )
        ).scalars()
        return ProjectionState(
            latest_pass=None if latest is None else dict(latest),
            underived_runs=underived_runs,
            runs_in_progress=tuple(in_progress),
        )

    async def _lock(self) -> None:
        await self._session.execute(sa.select(sa.func.pg_advisory_xact_lock(PROJECTION_LOCK)))

    async def _derive(
        self,
        units: DerivationUnits,
        *,
        now: dt.datetime,
        trigger: ProjectionTrigger,
        source_run_id: UUID | None,
        confirm_run: UUID | None = None,
    ) -> ProjectionOutcome:
        pass_id = uuid.uuid4()
        started_at = dt.datetime.now(tz=dt.UTC)
        basis = (await CollectionBasisRepository(self._session).current()).token
        context = DerivationContext(basis_token=basis, derived_at=now)
        tally = _Tally()

        for chunk in _chunks(units.edges, EDGE_CHUNK):
            facts = await self._edges(chunk)
            derived = _concat(derive_edge(fact, context) for fact in facts)
            existing = await self._relationships.supports_resting_on(
                EvidenceKind.MEMBERSHIP_EDGE, chunk
            )
            await self._apply(derived, existing, tally, now=now, basis=basis)
        for chunk in _chunks(units.principals, PRINCIPAL_CHUNK):
            principal_facts = await self._principals(chunk)
            derived = _concat(derive_principal(fact, context) for fact in principal_facts)
            existing = await self._relationships.supports_resting_on(EvidenceKind.PRINCIPAL, chunk)
            await self._apply(derived, existing, tally, now=now, basis=basis)
        for chunk in _chunks(units.resources, RESOURCE_CHUNK):
            resource_facts = await self._resources(chunk)
            derived = _concat(derive_resource(fact, context) for fact in resource_facts)
            existing = await self._relationships.supports_targeting(
                NodeKind.NTFS_RESOURCE, chunk, RESOURCE_TYPES
            )
            await self._apply(derived, existing, tally, now=now, basis=basis)
        if confirm_run is not None:
            confirmed = await self._relationships.extend_observed(confirm_run, now=now)
            tally.outcomes[RecordOutcome.EXTENDED] = (
                tally.outcomes.get(RecordOutcome.EXTENDED, 0) + confirmed
            )

        outcome = ProjectionOutcome(
            pass_id=pass_id,
            trigger=trigger,
            status=ProjectionStatus.SUCCEEDED,
            source_run_id=source_run_id,
            basis_token=basis,
            units_examined=len(units),
            relationships_created=tally.relationships_created,
            outcomes=MappingProxyType(dict(sorted(tally.outcomes.items()))),
            supports_retired=tally.retired,
            skipped=tuple(tally.skipped),
        )
        await self._session.execute(
            sa.insert(graph_derivation_passes).values(
                pass_id=pass_id,
                trigger=trigger.value,
                source_run_id=source_run_id,
                status=ProjectionStatus.SUCCEEDED.value,
                basis_token=basis,
                rules=list(_RULES_RUN),
                units_examined=outcome.units_examined,
                units_skipped=len(outcome.skipped),
                relationships_created=outcome.relationships_created,
                supports_created=outcome.count(RecordOutcome.CREATED),
                supports_extended=outcome.count(RecordOutcome.EXTENDED),
                supports_rederived=outcome.count(RecordOutcome.REDERIVED),
                supports_backdated=outcome.count(RecordOutcome.BACKDATED),
                supports_replayed=outcome.count(RecordOutcome.REPLAYED),
                supports_stale=outcome.count(RecordOutcome.STALE),
                supports_reinstated=outcome.count(RecordOutcome.REINSTATED),
                supports_retired=outcome.supports_retired,
                started_at=started_at,
                completed_at=max(started_at, dt.datetime.now(tz=dt.UTC)),
            )
        )
        for skip in outcome.skipped[:SKIPS_LOGGED]:
            logger.warning(
                "graph.projection.unit_skipped",
                extra={"kind": skip.kind.value, "key": skip.key, "reason": skip.reason},
            )
        return outcome

    async def _apply(
        self,
        derived: Derivation,
        existing: set[tuple[str, str]],
        tally: _Tally,
        *,
        now: dt.datetime,
        basis: str,
    ) -> None:
        """Record what the rules produced; retire what they no longer produce over present facts."""
        tally.skipped.extend(derived.skipped)
        produced = {(a.relationship_id, a.support_key) for a in derived.assertions}
        candidates = existing - produced
        if derived.assertions:
            batch = await self._relationships.record_many(derived.assertions, now=now)
            tally.relationships_created += batch.relationships_created
            for result in batch.results:
                tally.outcomes[result.outcome] = tally.outcomes.get(result.outcome, 0) + 1
        if candidates:
            stored = await self._relationships.get_many({rid for rid, _ in candidates})
            retire = [
                (relationship.relationship_id, support.support_key)
                for relationship in stored.values()
                for support in relationship.supports
                if (relationship.relationship_id, support.support_key) in candidates
                # Every premise present and not already retired: the facts are there and the
                # rules no longer read them as establishing this. Anything else is already
                # not current (a tombstone) or unknown (a missing row), and stays so.
                and support.currency is RelationshipCurrency.CURRENT
            ]
            tally.retired += await self._relationships.retire(retire, now=now, basis_token=basis)

    # --- Facts ---------------------------------------------------------------------------

    async def _edges(self, keys: Sequence[str]) -> list[EdgeFact]:
        table = current_membership_edges
        rows = await self._session.execute(
            sa.select(table).where(table.c.edge_key.in_(keys)).order_by(table.c.edge_key)
        )
        return [
            EdgeFact(
                reading=_reading(EvidenceKind.MEMBERSHIP_EDGE, row.edge_key, row),
                group_key=row.group_key,
                member_key=row.member_key,
                group_sid=row.group_sid,
                member_sid=row.member_sid,
                edge_kind=MembershipEdgeKind(row.edge_kind),
                host_key=row.host_key,
                member_kind=None if row.member_kind is None else PrincipalKind(row.member_kind),
                is_foreign_security_principal=bool(row.is_foreign_security_principal),
            )
            for row in rows
        ]

    async def _principals(self, keys: Sequence[str]) -> list[PrincipalFact]:
        table = current_principals
        rows = await self._session.execute(
            sa.select(
                table.c.principal_key,
                table.c.sid,
                table.c.principal_kind,
                table.c.host_key,
                table.c.last_observed_at,
                table.c.last_observed_run_id,
            )
            .where(table.c.principal_key.in_(keys))
            .order_by(table.c.principal_key)
        )
        return [
            PrincipalFact(
                reading=_reading(EvidenceKind.PRINCIPAL, row.principal_key, row),
                principal_key=row.principal_key,
                sid=row.sid,
                principal_kind=PrincipalKind(row.principal_kind),
                host_key=row.host_key,
            )
            for row in rows
        ]

    async def _resources(self, keys: Sequence[str]) -> list[ResourceFact]:
        resources = current_ntfs_resources
        aces = current_ntfs_aces
        descriptor_rows = {
            row.resource_key: row
            for row in await self._session.execute(
                sa.select(
                    resources.c.resource_key,
                    resources.c.server_key,
                    resources.c.owner_sid,
                    resources.c.dacl_present,
                    resources.c.ace_count,
                    resources.c.last_observed_at,
                    resources.c.last_observed_run_id,
                ).where(resources.c.resource_key.in_(keys))
            )
        }
        entries: dict[str, list[AceFact]] = {}
        for row in await self._session.execute(
            sa.select(
                aces.c.ace_key,
                aces.c.resource_key,
                aces.c.trustee_key,
                aces.c.trustee_sid,
                aces.c.ace_type,
                aces.c.access_mask,
                aces.c.ace_flags,
                aces.c.source,
                aces.c.order_index,
                aces.c.last_observed_at,
                aces.c.last_observed_run_id,
            ).where(aces.c.resource_key.in_(keys))
        ):
            entries.setdefault(row.resource_key, []).append(
                AceFact(
                    reading=_reading(EvidenceKind.NTFS_ACE, row.ace_key, row),
                    trustee_key=row.trustee_key,
                    trustee_sid=row.trustee_sid,
                    ace_type=AceType(row.ace_type),
                    access_mask=int(row.access_mask),
                    ace_flags=int(row.ace_flags),
                    source=AceSource(row.source),
                    order_index=row.order_index,
                )
            )

        facts: list[ResourceFact] = []
        for key in sorted(set(descriptor_rows) | set(entries)):
            descriptor = descriptor_rows.get(key)
            facts.append(
                ResourceFact(
                    resource_key=key,
                    server_key=(
                        descriptor.server_key if descriptor is not None else _server_of(key)
                    ),
                    reading=(
                        None
                        if descriptor is None
                        else _reading(EvidenceKind.NTFS_RESOURCE, key, descriptor)
                    ),
                    owner_sid=None if descriptor is None else descriptor.owner_sid,
                    dacl_present=True if descriptor is None else bool(descriptor.dacl_present),
                    declared_ace_count=None if descriptor is None else int(descriptor.ace_count),
                    aces=ordered_entries(entries.get(key, ())),
                )
            )
        return facts


async def project_completed_run(
    session: AsyncSession, run_id: UUID, *, now: dt.datetime, unless_derived: bool = False
) -> ProjectionOutcome | None:
    """Derive one completed run's facts, in a savepoint; never raises.

    On failure everything the pass wrote is rolled back to the savepoint, a failed pass is
    recorded in a second savepoint, and the caller's transaction — the completion — goes on to
    commit. The failure is visible in ``graph_derivation_passes`` and in
    :meth:`RelationshipProjector.state`, and the next replayed completion or rebuild retries it.

    ``unless_derived`` skips a run a successful pass already covered, returning ``None``: what
    a replayed completion asks for.
    """
    projector = RelationshipProjector(session)
    started_at = dt.datetime.now(tz=dt.UTC)
    try:
        async with session.begin_nested():
            if unless_derived and await projector.has_succeeded_for(run_id):
                return None
            outcome = await projector.derive_run(run_id, now=now)
        return outcome
    except Exception as error:
        logger.exception("graph.projection.failed", extra={"run_id": str(run_id)})
        pass_id = uuid.uuid4()
        message = f"{type(error).__name__}: {error}"
        try:
            async with session.begin_nested():
                await projector.record_failure(
                    pass_id=pass_id,
                    trigger=ProjectionTrigger.RUN_COMPLETION,
                    source_run_id=run_id,
                    started_at=started_at,
                    error=message,
                )
        except Exception:
            logger.exception("graph.projection.failure_not_recorded", extra={"run_id": str(run_id)})
        return ProjectionOutcome(
            pass_id=pass_id,
            trigger=ProjectionTrigger.RUN_COMPLETION,
            status=ProjectionStatus.FAILED,
            source_run_id=run_id,
            basis_token=None,
            error=message,
        )


@dataclass
class _Tally:
    relationships_created: int = 0
    outcomes: dict[RecordOutcome, int] = field(default_factory=dict)
    retired: int = 0
    skipped: list[SkippedUnit] = field(default_factory=list)


def _reading(kind: EvidenceKind, key: str, row: Any) -> FactReading:
    return FactReading(kind, key, row.last_observed_run_id, row.last_observed_at)


def _server_of(resource_key: str) -> str:
    """The server an orphaned ACL's path names, case-folded as a server key is."""
    try:
        return parse_unc_path(resource_key).server.casefold()
    except DomainValidationError:
        return resource_key.lstrip("\\").split("\\", 1)[0].casefold()


def _chunks(keys: Iterable[str], size: int) -> Iterable[list[str]]:
    ordered = sorted(keys)
    for start in range(0, len(ordered), size):
        yield ordered[start : start + size]


def _concat(derivations: Iterable[Derivation]) -> Derivation:
    assertions: list[RelationshipAssertion] = []
    skipped: list[SkippedUnit] = []
    for derivation in derivations:
        assertions.extend(derivation.assertions)
        skipped.extend(derivation.skipped)
    return Derivation(tuple(assertions), tuple(skipped))
