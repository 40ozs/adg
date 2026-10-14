"""Storing and reading privilege relationships, with the evidence each one rests on.

The minimal internal surface the relationship layer needs before anything derives or serves a
relationship: record one derivation atomically, read relationships back with their supports and
evidence, list a node's relationships in either direction, and find the relationships that rest
on one stored fact. No HTTP surface, no traversal and no derivation live here (ADG-GRAPH-01A).

## Recording is idempotent, and an older reading never overwrites a newer one

:meth:`RelationshipRepository.record` follows the rule the current-state upsert and the history
writer already follow (``docs/architecture/history-model.md`` §4), applied to a support:

* a support seen for the first time is **created**;
* a reading newer than the support's last confirmation **extends** it and carries the newer
  derivation's class, rule, qualifiers, conditions and attributes;
* a reading at the same instant with a different derivation **re-derives** it — a rule's new
  version applied to the same evidence — and the later-arriving one is kept;
* a reading older than the support's first observation **backdates** it, which is new knowledge
  and contradicts nothing; its derivation is not applied;
* the same reading again is a **replay** and writes nothing;
* an older reading that disagrees is **stale** and is not applied — counted, never silently
  dropped.

Each evidence link keeps its own first and last observation, widened by every reading
whatever the support's outcome: when a fact was seen is true independently of what was derived
from it.

## Currency is read, never stored

Whether a premise still holds is the current-state presence rule (``app.models.current``) asked
of the evidence row: present, measured absent (an open tombstone), or missing (no row at all).
It is computed on every read, so it cannot go stale between a reconciliation and the next
derivation.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain.relationships import (
    AdjacencyDirection,
    DerivationClass,
    DerivationRule,
    EvidenceKind,
    EvidenceRole,
    EvidenceState,
    NodeKind,
    NodeRef,
    RelationshipAssertion,
    RelationshipCondition,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipScopeKind,
    RelationshipSupport,
    RelationshipType,
    StoredEvidence,
    StoredRelationship,
)
from app.models.current import CURRENT_STATE_KEYS
from app.models.schema import (
    graph_relationship_evidence,
    graph_relationship_supports,
    graph_relationships,
    object_versions,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "EvidenceNotStoredError",
    "RecordOutcome",
    "RecordResult",
    "RelationshipPage",
    "RelationshipRepository",
]

DEFAULT_PAGE_SIZE: Final = 100
MAX_PAGE_SIZE: Final = 500
"""The membership listings' page sizes, for the same reasons (``membership-graph.md`` §6)."""


class EvidenceNotStoredError(ValueError):
    """A derivation named a fact the store does not hold.

    Refused rather than recorded: a relationship whose evidence was never stored is born
    untraceable, which is the one property this layer exists to guarantee. (A fact can still
    go *missing* later — the demo lifecycle deletes rows — and is then read back as
    :attr:`app.domain.relationships.EvidenceState.MISSING`, never as absent.)
    """

    def __init__(self, missing: Sequence[tuple[str, str]]) -> None:
        self.missing = tuple(missing)
        rendered = ", ".join(f"{kind} {key!r}" for kind, key in self.missing)
        super().__init__(f"Evidence names facts that are not stored: {rendered}.")


class RecordOutcome(StrEnum):
    """What recording one derivation did to its support."""

    CREATED = "created"
    EXTENDED = "extended"
    REDERIVED = "rederived"
    BACKDATED = "backdated"
    REPLAYED = "replayed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class RecordResult:
    relationship_id: str
    support_key: str
    relationship_created: bool
    outcome: RecordOutcome


@dataclass(frozen=True, slots=True)
class RelationshipPage:
    """One keyset page of a node's relationships, ordered by ``(type, relationship_id)``."""

    items: tuple[StoredRelationship, ...]
    next_after: tuple[RelationshipType, str] | None
    """Pass back as ``after`` for the next page; ``None`` when this page is the last."""


def _derivation_values(assertion: RelationshipAssertion) -> dict[str, Any]:
    return {
        "derivation_class": assertion.derivation_class.value,
        "rule_id": assertion.rule.rule_id,
        "rule_version": assertion.rule.version,
        "qualifiers": sorted(item.value for item in assertion.qualifiers),
        "conditions": sorted(item.value for item in assertion.conditions),
        "attributes": dict(assertion.attributes),
        "basis_token": assertion.basis_token,
    }


def _stored_derivation(row: sa.RowMapping) -> dict[str, Any]:
    return {
        "derivation_class": row["derivation_class"],
        "rule_id": row["rule_id"],
        "rule_version": row["rule_version"],
        "qualifiers": sorted(row["qualifiers"]),
        "conditions": sorted(row["conditions"]),
        "attributes": dict(row["attributes"]),
        "basis_token": row["basis_token"],
    }


def _moment(observed_at: dt.datetime, run_id: UUID) -> tuple[dt.datetime, str]:
    """The order in which two readings compare: instant, then run id, so ties are stable."""
    return (observed_at, str(run_id))


class RelationshipRepository:
    """Relationships over one session. Writes are not committed here; the caller owns that."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Writing ---------------------------------------------------------------------------

    async def record(self, assertion: RelationshipAssertion, *, now: dt.datetime) -> RecordResult:
        """Store one derivation: the relationship, its support and every evidence link."""
        await self._require_stored(assertion)
        relationship_id = assertion.relationship_id
        key = assertion.support_key
        observed_at, run_id = assertion.supported_at

        created = (
            await self._session.execute(
                pg_insert(graph_relationships)
                .values(
                    relationship_id=relationship_id,
                    relationship_type=assertion.relationship_type.value,
                    source_kind=assertion.source.kind.value,
                    source_key=assertion.source.key,
                    target_kind=assertion.target.kind.value,
                    target_key=assertion.target.key,
                    scope_kind=assertion.scope.kind.value if assertion.scope else None,
                    scope_key=assertion.scope.key if assertion.scope else None,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[graph_relationships.c.relationship_id])
                .returning(graph_relationships.c.relationship_id)
            )
        ).scalar_one_or_none() is not None

        outcome = await self._record_support(
            assertion, relationship_id, key, observed_at, run_id, now
        )
        await self._record_evidence(assertion, relationship_id, key, now)
        return RecordResult(relationship_id, key, created, outcome)

    async def _require_stored(self, assertion: RelationshipAssertion) -> None:
        """Every piece of evidence must name a row its current-state table holds."""
        wanted: dict[EvidenceKind, set[str]] = {}
        for item in assertion.evidence:
            wanted.setdefault(item.kind, set()).add(item.key)
        missing: list[tuple[str, str]] = []
        for kind, keys in sorted(wanted.items()):
            table, column = CURRENT_STATE_KEYS[ObservationKind(kind.value)]
            found = set(
                (
                    await self._session.execute(
                        sa.select(table.c[column]).where(table.c[column].in_(sorted(keys)))
                    )
                ).scalars()
            )
            missing.extend((kind.value, key) for key in sorted(keys - found))
        if missing:
            raise EvidenceNotStoredError(missing)

    async def _record_support(
        self,
        assertion: RelationshipAssertion,
        relationship_id: str,
        key: str,
        observed_at: dt.datetime,
        run_id: UUID,
        now: dt.datetime,
    ) -> RecordOutcome:
        derivation = _derivation_values(assertion)
        inserted = (
            await self._session.execute(
                pg_insert(graph_relationship_supports)
                .values(
                    relationship_id=relationship_id,
                    support_key=key,
                    derived_at=assertion.derived_at,
                    first_observed_at=observed_at,
                    first_observed_run_id=run_id,
                    last_observed_at=observed_at,
                    last_observed_run_id=run_id,
                    created_at=now,
                    updated_at=now,
                    **derivation,
                )
                .on_conflict_do_nothing()
                .returning(graph_relationship_supports.c.support_key)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return RecordOutcome.CREATED

        identity = (
            graph_relationship_supports.c.relationship_id == relationship_id,
            graph_relationship_supports.c.support_key == key,
        )
        stored = (
            (
                await self._session.execute(
                    sa.select(graph_relationship_supports).where(*identity).with_for_update()
                )
            )
            .mappings()
            .one()
        )
        reading = _moment(observed_at, run_id)
        last = _moment(stored["last_observed_at"], stored["last_observed_run_id"])
        first = _moment(stored["first_observed_at"], stored["first_observed_run_id"])
        same_derivation = _stored_derivation(stored) == derivation

        changes: dict[str, Any] = {}
        if reading > last:
            outcome = RecordOutcome.EXTENDED
            changes = {
                "last_observed_at": observed_at,
                "last_observed_run_id": run_id,
                "derived_at": assertion.derived_at,
                **derivation,
            }
        elif reading == last and not same_derivation:
            outcome = RecordOutcome.REDERIVED
            changes = {"derived_at": assertion.derived_at, **derivation}
        elif reading < first:
            outcome = RecordOutcome.BACKDATED
            changes = {"first_observed_at": observed_at, "first_observed_run_id": run_id}
        elif same_derivation:
            outcome = RecordOutcome.REPLAYED
        else:
            outcome = RecordOutcome.STALE

        if changes:
            await self._session.execute(
                sa.update(graph_relationship_supports)
                .where(*identity)
                .values(updated_at=now, **changes)
            )
        return outcome

    async def _record_evidence(
        self,
        assertion: RelationshipAssertion,
        relationship_id: str,
        key: str,
        now: dt.datetime,
    ) -> None:
        table = graph_relationship_evidence
        for item in assertion.evidence:
            statement = pg_insert(table).values(
                relationship_id=relationship_id,
                support_key=key,
                evidence_kind=item.kind.value,
                evidence_key=item.key,
                role=item.role.value,
                first_observed_at=item.observed_at,
                first_observed_run_id=item.run_id,
                last_observed_at=item.observed_at,
                last_observed_run_id=item.run_id,
                created_at=now,
                updated_at=now,
            )
            incoming = statement.excluded
            earlier = sa.tuple_(
                incoming.first_observed_at, sa.cast(incoming.first_observed_run_id, sa.Text)
            ) < sa.tuple_(
                table.c.first_observed_at, sa.cast(table.c.first_observed_run_id, sa.Text)
            )
            later = sa.tuple_(
                incoming.last_observed_at, sa.cast(incoming.last_observed_run_id, sa.Text)
            ) > sa.tuple_(table.c.last_observed_at, sa.cast(table.c.last_observed_run_id, sa.Text))
            await self._session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_graph_relationship_evidence_identity",
                    set_={
                        "first_observed_at": sa.case(
                            (earlier, incoming.first_observed_at), else_=table.c.first_observed_at
                        ),
                        "first_observed_run_id": sa.case(
                            (earlier, incoming.first_observed_run_id),
                            else_=table.c.first_observed_run_id,
                        ),
                        "last_observed_at": sa.case(
                            (later, incoming.last_observed_at), else_=table.c.last_observed_at
                        ),
                        "last_observed_run_id": sa.case(
                            (later, incoming.last_observed_run_id),
                            else_=table.c.last_observed_run_id,
                        ),
                        "updated_at": now,
                    },
                    # A replay widens nothing and writes nothing.
                    where=sa.or_(earlier, later),
                )
            )

    # --- Reading ---------------------------------------------------------------------------

    async def get(self, relationship_id: str) -> StoredRelationship | None:
        return (await self.get_many((relationship_id,))).get(relationship_id)

    async def get_many(self, relationship_ids: Iterable[str]) -> dict[str, StoredRelationship]:
        """Relationships by id, each with every support and evidence link. Three queries."""
        ids = sorted(set(relationship_ids))
        if not ids:
            return {}
        relationships = (
            (
                await self._session.execute(
                    sa.select(graph_relationships).where(
                        graph_relationships.c.relationship_id.in_(ids)
                    )
                )
            )
            .mappings()
            .all()
        )
        supports = (
            (
                await self._session.execute(
                    sa.select(graph_relationship_supports).where(
                        graph_relationship_supports.c.relationship_id.in_(ids)
                    )
                )
            )
            .mappings()
            .all()
        )
        evidence = (await self._session.execute(self._evidence_with_state(ids))).mappings().all()

        links: dict[tuple[str, str], list[StoredEvidence]] = {}
        for row in evidence:
            links.setdefault((row["relationship_id"], row["support_key"]), []).append(
                StoredEvidence(
                    kind=EvidenceKind(row["evidence_kind"]),
                    key=row["evidence_key"],
                    role=EvidenceRole(row["role"]),
                    first_observed_at=row["first_observed_at"],
                    first_observed_run_id=row["first_observed_run_id"],
                    last_observed_at=row["last_observed_at"],
                    last_observed_run_id=row["last_observed_run_id"],
                    state=_state(row["tombstoned"], row["has_row"]),
                )
            )
        by_relationship: dict[str, list[RelationshipSupport]] = {}
        for row in supports:
            by_relationship.setdefault(row["relationship_id"], []).append(
                RelationshipSupport(
                    support_key=row["support_key"],
                    derivation_class=DerivationClass(row["derivation_class"]),
                    rule=DerivationRule(row["rule_id"], row["rule_version"]),
                    qualifiers=frozenset(RelationshipQualifier(q) for q in row["qualifiers"]),
                    conditions=frozenset(RelationshipCondition(c) for c in row["conditions"]),
                    attributes=dict(sorted(row["attributes"].items())),
                    basis_token=row["basis_token"],
                    derived_at=row["derived_at"],
                    first_observed_at=row["first_observed_at"],
                    first_observed_run_id=row["first_observed_run_id"],
                    last_observed_at=row["last_observed_at"],
                    last_observed_run_id=row["last_observed_run_id"],
                    evidence=tuple(
                        sorted(
                            links.get((row["relationship_id"], row["support_key"]), ()),
                            key=lambda link: (link.kind, link.key),
                        )
                    ),
                )
            )

        result: dict[str, StoredRelationship] = {}
        for row in relationships:
            scope = (
                RelationshipScope(RelationshipScopeKind(row["scope_kind"]), row["scope_key"])
                if row["scope_kind"] is not None
                else None
            )
            result[row["relationship_id"]] = StoredRelationship(
                relationship_id=row["relationship_id"],
                relationship_type=RelationshipType(row["relationship_type"]),
                source=NodeRef(NodeKind(row["source_kind"]), row["source_key"]),
                target=NodeRef(NodeKind(row["target_kind"]), row["target_key"]),
                scope=scope,
                supports=tuple(by_relationship[row["relationship_id"]]),
            )
        return dict(sorted(result.items()))

    async def adjacent(
        self,
        node: NodeRef,
        direction: AdjacencyDirection,
        *,
        types: Iterable[RelationshipType] | None = None,
        after: tuple[RelationshipType, str] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> RelationshipPage:
        """One keyset page of the relationships leaving (outbound) or reaching (inbound) a node.

        Ordered by ``(relationship_type, relationship_id)``, which the adjacency indexes cover,
        so a page is an index range scan however many relationships the node has.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        limit = min(limit, MAX_PAGE_SIZE)
        table = graph_relationships
        kind, key = (
            (table.c.source_kind, table.c.source_key)
            if direction is AdjacencyDirection.OUTBOUND
            else (table.c.target_kind, table.c.target_key)
        )
        statement = (
            sa.select(table.c.relationship_type, table.c.relationship_id)
            .where(kind == node.kind.value, key == node.key)
            .order_by(table.c.relationship_type, table.c.relationship_id)
            .limit(limit + 1)
        )
        if types is not None:
            statement = statement.where(
                table.c.relationship_type.in_(sorted(item.value for item in types))
            )
        if after is not None:
            statement = statement.where(
                sa.tuple_(table.c.relationship_type, table.c.relationship_id)
                > sa.tuple_(sa.literal(after[0].value), sa.literal(after[1]))
            )
        rows = (await self._session.execute(statement)).all()
        page = rows[:limit]
        loaded = await self.get_many(row.relationship_id for row in page)
        items = tuple(loaded[row.relationship_id] for row in page)
        next_after = (
            (RelationshipType(page[-1].relationship_type), page[-1].relationship_id)
            if len(rows) > limit
            else None
        )
        return RelationshipPage(items=items, next_after=next_after)

    async def supported_by(self, kind: EvidenceKind, key: str) -> tuple[str, ...]:
        """The ids of every relationship with a support resting on one stored fact."""
        rows = await self._session.execute(
            sa.select(graph_relationship_evidence.c.relationship_id)
            .where(
                graph_relationship_evidence.c.evidence_kind == kind.value,
                graph_relationship_evidence.c.evidence_key == key,
            )
            .distinct()
            .order_by(graph_relationship_evidence.c.relationship_id)
        )
        return tuple(rows.scalars())

    @staticmethod
    def _evidence_with_state(ids: Sequence[str]) -> sa.Select[Any]:
        """Every evidence link of ``ids``, with what current state says about its fact.

        ``has_row``: the fact's current-state table holds the key. ``tombstoned``: an open
        tombstone excludes it — the one rule ``app.models.current`` states for every
        current-state read, asked here of a key rather than of a table.
        """
        evidence = graph_relationship_evidence
        has_row = sa.case(
            *(
                (
                    evidence.c.evidence_kind == kind.value,
                    sa.exists().where(table.c[column] == evidence.c.evidence_key),
                )
                for kind, (table, column) in CURRENT_STATE_KEYS.items()
            ),
            else_=sa.false(),
        )
        tombstoned = sa.exists().where(
            object_versions.c.object_kind == evidence.c.evidence_kind,
            object_versions.c.object_key == evidence.c.evidence_key,
            object_versions.c.valid_to.is_(None),
            object_versions.c.is_present.is_(False),
        )
        return (
            sa.select(evidence, has_row.label("has_row"), tombstoned.label("tombstoned"))
            .where(evidence.c.relationship_id.in_(ids))
            .order_by(
                evidence.c.relationship_id,
                evidence.c.support_key,
                evidence.c.evidence_kind,
                evidence.c.evidence_key,
            )
        )


def _state(tombstoned: bool, has_row: bool) -> EvidenceState:
    if tombstoned:
        return EvidenceState.ABSENT
    return EvidenceState.PRESENT if has_row else EvidenceState.MISSING


# Every evidence kind must have a current-state table to be asked of, or its presence would be
# silently "missing" forever. Checked at import, like CURRENT_STATE_KEYS itself.
_UNMAPPED: Final = {kind.value for kind in EvidenceKind} - {
    kind.value for kind in CURRENT_STATE_KEYS
}
if _UNMAPPED or {kind.value for kind in ObservationKind} != {kind.value for kind in EvidenceKind}:
    raise RuntimeError(f"Evidence kinds without a current-state table: {sorted(_UNMAPPED)}")
