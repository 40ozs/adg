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
  dropped;
* a reading no older than the last one, of a support that was **retired**, **reinstates** it:
  the rule that stopped producing it produces it again (ADG-GRAPH-01B).

Each evidence link keeps its own first and last observation, widened by every reading
whatever the support's outcome: when a fact was seen is true independently of what was derived
from it. A support's *context* evidence describes the derivation that last applied, so when a
newer derivation is applied its context replaces the old one; premises never change, because
they are the support's identity.

## Recording is set-based

:meth:`RelationshipRepository.record_many` stores any number of derivations in a bounded number
of statements per chunk — one insert per table, one locking read, one batch of updates — rather
than several round trips per relationship. :meth:`record` is ``record_many`` of one, so the
rules above have exactly one implementation.

## Retirement

:meth:`RelationshipRepository.retire` marks supports a derivation read and no longer produced
(:mod:`app.relationships.projector` decides which). A retired support is not current whatever
its premises say, and is kept, with its evidence, as the record of what used to hold.

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
    "RecordBatch",
    "RecordOutcome",
    "RecordResult",
    "RelationshipPage",
    "RelationshipRepository",
]

DEFAULT_PAGE_SIZE: Final = 100
MAX_PAGE_SIZE: Final = 500
"""The membership listings' page sizes, for the same reasons (``membership-graph.md`` §6)."""

RECORD_CHUNK: Final = 500
"""Derivations written per statement batch. An evidence row binds 11 parameters and a support
row 18, so a chunk stays far below PostgreSQL's 65,535-parameter ceiling even when every
support carries several evidence links."""

READ_CHUNK: Final = 5000
"""Ids per ``IN`` list on a read, for the same ceiling."""


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
    REINSTATED = "reinstated"
    """The support had been retired, and a reading no older than its last one produced it
    again. Any window or derivation change that reading carries is applied as well."""


@dataclass(frozen=True, slots=True)
class RecordResult:
    relationship_id: str
    support_key: str
    relationship_created: bool
    outcome: RecordOutcome


@dataclass(frozen=True, slots=True)
class RecordBatch:
    """What one :meth:`RelationshipRepository.record_many` call did, per derivation."""

    results: tuple[RecordResult, ...]

    @property
    def relationships_created(self) -> int:
        return sum(1 for result in self.results if result.relationship_created)

    def count(self, outcome: RecordOutcome) -> int:
        return sum(1 for result in self.results if result.outcome is outcome)


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


_REPLACES_CONTEXT: Final = frozenset(
    {RecordOutcome.EXTENDED, RecordOutcome.REDERIVED, RecordOutcome.REINSTATED}
)
"""The outcomes that apply the reading's derivation over an older one, whose context links
the new derivation may no longer name."""


def _decide(
    assertion: RelationshipAssertion, stored: sa.RowMapping
) -> tuple[RecordOutcome, dict[str, Any]]:
    """What one reading of an existing support does to it, and the columns it changes."""
    observed_at, run_id = assertion.supported_at
    derivation = _derivation_values(assertion)
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

    # A retired support produced again by a reading at least as new as its last one is
    # current again. An older reading cannot undo what a newer derivation concluded.
    if stored["retired_at"] is not None and reading >= last:
        outcome = RecordOutcome.REINSTATED
        changes = {**changes, "retired_at": None, "retired_basis_token": None}
    return outcome, changes


def _unique(assertions: Iterable[RelationshipAssertion]) -> list[RelationshipAssertion]:
    """One derivation per support, in write order; a contradictory repeat is refused."""
    by_identity: dict[tuple[str, str], RelationshipAssertion] = {}
    for assertion in assertions:
        identity = (assertion.relationship_id, assertion.support_key)
        existing = by_identity.get(identity)
        if existing is None:
            by_identity[identity] = assertion
        elif existing != assertion:
            raise ValueError(
                f"Support {identity[1]} of relationship {identity[0]} was derived two different "
                "ways in one batch; a deterministic projector produces each support once."
            )
    return [by_identity[identity] for identity in sorted(by_identity)]


def _rowcount(result: Any) -> int:
    count = getattr(result, "rowcount", None)
    return int(count) if isinstance(count, int) and count >= 0 else 0


class RelationshipRepository:
    """Relationships over one session. Writes are not committed here; the caller owns that."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Writing ---------------------------------------------------------------------------

    async def record(self, assertion: RelationshipAssertion, *, now: dt.datetime) -> RecordResult:
        """Store one derivation: the relationship, its support and every evidence link."""
        (result,) = (await self.record_many((assertion,), now=now)).results
        return result

    async def record_many(
        self, assertions: Iterable[RelationshipAssertion], *, now: dt.datetime
    ) -> RecordBatch:
        """Store derivations, each atomically with its evidence, in the caller's transaction.

        Rows are written in ``(relationship_id, support_key)`` order, so two callers writing
        overlapping sets lock them in the same order. A derivation repeated verbatim collapses;
        the same support derived two different ways in one call is a projector defect and is
        refused rather than resolved by whichever came last.
        """
        ordered = _unique(assertions)
        await self._require_stored(ordered)
        results: list[RecordResult] = []
        for start in range(0, len(ordered), RECORD_CHUNK):
            results.extend(await self._record_chunk(ordered[start : start + RECORD_CHUNK], now))
        return RecordBatch(tuple(results))

    async def retire(
        self,
        supports: Iterable[tuple[str, str]],
        *,
        now: dt.datetime,
        basis_token: str,
    ) -> int:
        """Mark supports retired: their facts were read, all stored, and no rule produced them.

        Already-retired supports are left as they were, so the retirement instant is the first
        derivation that stopped producing the support. Returns how many were retired now.
        """
        keys = sorted(set(supports))
        retired = 0
        table = graph_relationship_supports
        for start in range(0, len(keys), READ_CHUNK):
            chunk = keys[start : start + READ_CHUNK]
            result = await self._session.execute(
                sa.update(table)
                .where(
                    sa.tuple_(table.c.relationship_id, table.c.support_key).in_(chunk),
                    table.c.retired_at.is_(None),
                )
                .values(retired_at=now, retired_basis_token=basis_token, updated_at=now)
            )
            retired += _rowcount(result)
        return retired

    async def extend_observed(self, run_id: UUID, *, now: dt.datetime) -> int:
        """Widen every window a run's re-observation of an unchanged fact extends, in SQL.

        A fact a run observed again without changing it changes no derivation, so nothing has
        to be re-derived for it: what moves is *when it was last seen*. Each evidence link on
        a fact the run observed takes the fact's newer observation, and each support resting
        on one of them takes its newest premise reading — the same windows
        :meth:`record_many` would have written for an unchanged derivation, at one statement
        per evidence kind and one for supports, however many facts the run re-read.

        Retired supports are left as they were: their window ended when they were retired.
        Returns how many supports were extended.
        """
        evidence = graph_relationship_evidence
        for kind in EvidenceKind:
            facts, key_column = CURRENT_STATE_KEYS[ObservationKind(kind.value)]
            newer = sa.tuple_(
                facts.c.last_observed_at, sa.cast(facts.c.last_observed_run_id, sa.Text)
            ) > sa.tuple_(
                evidence.c.last_observed_at, sa.cast(evidence.c.last_observed_run_id, sa.Text)
            )
            await self._session.execute(
                sa.update(evidence)
                .where(
                    evidence.c.evidence_kind == kind.value,
                    evidence.c.evidence_key == facts.c[key_column],
                    facts.c.last_observed_run_id == run_id,
                    newer,
                )
                .values(
                    last_observed_at=facts.c.last_observed_at,
                    last_observed_run_id=facts.c.last_observed_run_id,
                    updated_at=now,
                )
            )

        # A support is re-confirmed only when the run observed *every* premise again: its
        # window is when its newest premise was seen completing it, and a support with a
        # premise the run did not see was not seen complete.
        supports = graph_relationship_supports
        premise = evidence.c.role == EvidenceRole.PREMISE.value
        touched = (
            sa.select(evidence.c.relationship_id, evidence.c.support_key)
            .where(evidence.c.last_observed_run_id == run_id, premise)
            .distinct()
            .subquery("touched")
        )
        confirmed = (
            sa.select(
                evidence.c.relationship_id,
                evidence.c.support_key,
                sa.func.max(evidence.c.last_observed_at).label("observed_at"),
            )
            .join(
                touched,
                sa.and_(
                    touched.c.relationship_id == evidence.c.relationship_id,
                    touched.c.support_key == evidence.c.support_key,
                ),
            )
            .where(premise)
            .group_by(evidence.c.relationship_id, evidence.c.support_key)
            .having(sa.func.bool_and(evidence.c.last_observed_run_id == run_id))
            .subquery("confirmed")
        )
        result = await self._session.execute(
            sa.update(supports)
            .where(
                supports.c.relationship_id == confirmed.c.relationship_id,
                supports.c.support_key == confirmed.c.support_key,
                supports.c.retired_at.is_(None),
                sa.tuple_(confirmed.c.observed_at, sa.literal(str(run_id)))
                > sa.tuple_(
                    supports.c.last_observed_at, sa.cast(supports.c.last_observed_run_id, sa.Text)
                ),
            )
            .values(
                last_observed_at=confirmed.c.observed_at,
                last_observed_run_id=run_id,
                updated_at=now,
            )
        )
        return _rowcount(result)

    async def _require_stored(self, assertions: Sequence[RelationshipAssertion]) -> None:
        """Every piece of evidence must name a row its current-state table holds."""
        wanted: dict[EvidenceKind, set[str]] = {}
        for assertion in assertions:
            for item in assertion.evidence:
                wanted.setdefault(item.kind, set()).add(item.key)
        missing: list[tuple[str, str]] = []
        for kind, keys in sorted(wanted.items()):
            table, column = CURRENT_STATE_KEYS[ObservationKind(kind.value)]
            ordered = sorted(keys)
            found: set[str] = set()
            for start in range(0, len(ordered), READ_CHUNK):
                found.update(
                    (
                        await self._session.execute(
                            sa.select(table.c[column]).where(
                                table.c[column].in_(ordered[start : start + READ_CHUNK])
                            )
                        )
                    ).scalars()
                )
            missing.extend((kind.value, key) for key in ordered if key not in found)
        if missing:
            raise EvidenceNotStoredError(missing)

    async def _record_chunk(
        self, chunk: Sequence[RelationshipAssertion], now: dt.datetime
    ) -> list[RecordResult]:
        created = await self._insert_relationships(chunk, now)
        inserted = await self._insert_supports(chunk, now)
        existing = [a for a in chunk if (a.relationship_id, a.support_key) not in inserted]
        stored = await self._lock_supports(existing)

        outcomes: dict[tuple[str, str], RecordOutcome] = {}
        updates: list[dict[str, Any]] = []
        for assertion in chunk:
            identity = (assertion.relationship_id, assertion.support_key)
            if identity in inserted:
                outcomes[identity] = RecordOutcome.CREATED
                continue
            outcome, changes = _decide(assertion, stored[identity])
            outcomes[identity] = outcome
            if changes:
                updates.append(
                    {"b_relationship_id": identity[0], "b_support_key": identity[1], **changes}
                )
        await self._update_supports(updates, now)

        # A support created just now has no older context to replace.
        replaced = [
            a for a in chunk if outcomes[(a.relationship_id, a.support_key)] in _REPLACES_CONTEXT
        ]
        await self._replace_context(replaced)
        await self._record_evidence(chunk, now)
        results = []
        for assertion in chunk:
            # A relationship created with several supports is created once: credited to the
            # first of them, so counting results counts relationships.
            first = assertion.relationship_id in created
            created.discard(assertion.relationship_id)
            results.append(
                RecordResult(
                    assertion.relationship_id,
                    assertion.support_key,
                    first,
                    outcomes[(assertion.relationship_id, assertion.support_key)],
                )
            )
        return results

    async def _insert_relationships(
        self, chunk: Sequence[RelationshipAssertion], now: dt.datetime
    ) -> set[str]:
        rows: dict[str, dict[str, Any]] = {}
        for assertion in chunk:
            rows.setdefault(
                assertion.relationship_id,
                {
                    "relationship_id": assertion.relationship_id,
                    "relationship_type": assertion.relationship_type.value,
                    "source_kind": assertion.source.kind.value,
                    "source_key": assertion.source.key,
                    "target_kind": assertion.target.kind.value,
                    "target_key": assertion.target.key,
                    "scope_kind": assertion.scope.kind.value if assertion.scope else None,
                    "scope_key": assertion.scope.key if assertion.scope else None,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        # Executed for many parameter sets rather than built as one multi-row VALUES: the
        # statement compiles once and is cached, and the driver batches the rows itself.
        created = (
            await self._session.execute(
                pg_insert(graph_relationships)
                .on_conflict_do_nothing(index_elements=[graph_relationships.c.relationship_id])
                .returning(graph_relationships.c.relationship_id),
                [rows[key] for key in sorted(rows)],
            )
        ).scalars()
        return set(created)

    async def _insert_supports(
        self, chunk: Sequence[RelationshipAssertion], now: dt.datetime
    ) -> set[tuple[str, str]]:
        rows = []
        for assertion in chunk:
            observed_at, run_id = assertion.supported_at
            rows.append(
                {
                    "relationship_id": assertion.relationship_id,
                    "support_key": assertion.support_key,
                    "derived_at": assertion.derived_at,
                    "first_observed_at": observed_at,
                    "first_observed_run_id": run_id,
                    "last_observed_at": observed_at,
                    "last_observed_run_id": run_id,
                    "created_at": now,
                    "updated_at": now,
                    **_derivation_values(assertion),
                }
            )
        table = graph_relationship_supports
        inserted = await self._session.execute(
            pg_insert(table)
            .on_conflict_do_nothing()
            .returning(table.c.relationship_id, table.c.support_key),
            rows,
        )
        return {(row.relationship_id, row.support_key) for row in inserted}

    async def _lock_supports(
        self, assertions: Sequence[RelationshipAssertion]
    ) -> dict[tuple[str, str], sa.RowMapping]:
        if not assertions:
            return {}
        table = graph_relationship_supports
        keys = [(a.relationship_id, a.support_key) for a in assertions]
        rows = (
            (
                await self._session.execute(
                    sa.select(table)
                    .where(sa.tuple_(table.c.relationship_id, table.c.support_key).in_(keys))
                    .order_by(table.c.relationship_id, table.c.support_key)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        return {(row["relationship_id"], row["support_key"]): row for row in rows}

    async def _update_supports(self, updates: Sequence[dict[str, Any]], now: dt.datetime) -> None:
        """One statement per distinct set of changed columns, each executed for many rows."""
        table = graph_relationship_supports
        by_columns: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for update in updates:
            columns = tuple(sorted(name for name in update if not name.startswith("b_")))
            by_columns.setdefault(columns, []).append({**update, "updated_at": now})
        for _, batch in sorted(by_columns.items()):
            # No .values(): with executemany, the parameter names that are columns become the
            # SET list, and the b_-prefixed ones bind the WHERE clause.
            statement = sa.update(table).where(
                table.c.relationship_id == sa.bindparam("b_relationship_id"),
                table.c.support_key == sa.bindparam("b_support_key"),
            )
            await self._session.execute(statement, batch)

    async def _replace_context(self, applied: Sequence[RelationshipAssertion]) -> None:
        """Drop the context links an applied derivation no longer names."""
        if not applied:
            return
        table = graph_relationship_evidence
        keep = [
            (a.relationship_id, a.support_key, item.kind.value, item.key)
            for a in applied
            for item in a.evidence
            if item.role is EvidenceRole.CONTEXT
        ]
        supports = sorted({(a.relationship_id, a.support_key) for a in applied})
        statement = sa.delete(table).where(
            table.c.role == EvidenceRole.CONTEXT.value,
            sa.tuple_(table.c.relationship_id, table.c.support_key).in_(supports),
        )
        if keep:
            statement = statement.where(
                sa.tuple_(
                    table.c.relationship_id,
                    table.c.support_key,
                    table.c.evidence_kind,
                    table.c.evidence_key,
                ).not_in(keep)
            )
        await self._session.execute(statement)

    async def _record_evidence(
        self, chunk: Sequence[RelationshipAssertion], now: dt.datetime
    ) -> None:
        table = graph_relationship_evidence
        rows = [
            {
                "relationship_id": assertion.relationship_id,
                "support_key": assertion.support_key,
                "evidence_kind": item.kind.value,
                "evidence_key": item.key,
                "role": item.role.value,
                "first_observed_at": item.observed_at,
                "first_observed_run_id": item.run_id,
                "last_observed_at": item.observed_at,
                "last_observed_run_id": item.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for assertion in chunk
            for item in assertion.evidence
        ]
        statement = pg_insert(table)
        incoming = statement.excluded
        earlier = sa.tuple_(
            incoming.first_observed_at, sa.cast(incoming.first_observed_run_id, sa.Text)
        ) < sa.tuple_(table.c.first_observed_at, sa.cast(table.c.first_observed_run_id, sa.Text))
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
            ),
            rows,
        )

    # --- Reading ---------------------------------------------------------------------------

    async def get(self, relationship_id: str) -> StoredRelationship | None:
        return (await self.get_many((relationship_id,))).get(relationship_id)

    async def get_many(self, relationship_ids: Iterable[str]) -> dict[str, StoredRelationship]:
        """Relationships by id, each with every support and evidence link.

        Three queries per :data:`READ_CHUNK` ids.
        """
        ids = sorted(set(relationship_ids))
        result: dict[str, StoredRelationship] = {}
        for start in range(0, len(ids), READ_CHUNK):
            result.update(await self._get_chunk(ids[start : start + READ_CHUNK]))
        return dict(sorted(result.items()))

    async def _get_chunk(self, ids: Sequence[str]) -> dict[str, StoredRelationship]:
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
                    retired_at=row["retired_at"],
                    retired_basis_token=row["retired_basis_token"],
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
        return result

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

    async def supports_resting_on(
        self, kind: EvidenceKind, keys: Iterable[str]
    ) -> set[tuple[str, str]]:
        """Every ``(relationship_id, support_key)`` with one of these facts as a premise."""
        ordered = sorted(set(keys))
        evidence = graph_relationship_evidence
        found: set[tuple[str, str]] = set()
        for start in range(0, len(ordered), READ_CHUNK):
            rows = await self._session.execute(
                sa.select(evidence.c.relationship_id, evidence.c.support_key)
                .where(
                    evidence.c.evidence_kind == kind.value,
                    evidence.c.evidence_key.in_(ordered[start : start + READ_CHUNK]),
                    evidence.c.role == EvidenceRole.PREMISE.value,
                )
                .distinct()
            )
            found.update((row.relationship_id, row.support_key) for row in rows)
        return found

    async def supports_targeting(
        self,
        kind: NodeKind,
        keys: Iterable[str],
        types: Iterable[RelationshipType],
    ) -> set[tuple[str, str]]:
        """Every support of a relationship of ``types`` whose target is one of these nodes."""
        ordered = sorted(set(keys))
        relationships, supports = graph_relationships, graph_relationship_supports
        type_values = sorted(item.value for item in types)
        found: set[tuple[str, str]] = set()
        for start in range(0, len(ordered), READ_CHUNK):
            rows = await self._session.execute(
                sa.select(supports.c.relationship_id, supports.c.support_key)
                .select_from(
                    relationships.join(
                        supports, supports.c.relationship_id == relationships.c.relationship_id
                    )
                )
                .where(
                    relationships.c.target_kind == kind.value,
                    relationships.c.target_key.in_(ordered[start : start + READ_CHUNK]),
                    relationships.c.relationship_type.in_(type_values),
                )
            )
            found.update((row.relationship_id, row.support_key) for row in rows)
        return found

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
