r"""Turning observations into versions, and reconciled scopes into tombstones.

Two operations, called from exactly two places in :mod:`app.ingestion.service`:

* :meth:`HistoryWriter.record` runs inside ``apply_batch``, beside each current-state
  upsert, and is handed **the same row dictionaries that upsert writes**. That is the
  central guarantee of this module: history is not a second derivation of what a collector
  said, it is a projection of the row that was stored, so the timeline of an object and its
  current state cannot describe two different things.

* :meth:`HistoryWriter.close_absent` runs inside ``complete_run``, once per reconciled
  scope, and is the only code in ADG that may record an absence.

## What decides that something changed

The digest of the state, and nothing else. ``last_observed_at`` moves on every scan;
``source_key`` carries the run's own identity; ``updated_at`` moves when anything at all is
written. All of them are stripped (:data:`app.history.model.PROVENANCE_FIELDS`) before the
digest, so re-reading an unchanged ACL a thousand times extends one version a thousand times
and creates no second version. A history that recorded a version per scan would be a scan
log, and the question it exists to answer -- *when did this change?* -- would be unanswerable
in it.

## Observations that arrive out of order

Runs can overlap and a delayed run can land after a newer one. The current-state upsert
already handles that by refusing to let an older reading overwrite a newer one
(``_newest_wins``). History follows the same rule, and it is worth stating exactly, because
the alternative is a model that rewrites the past:

* An observation **newer than** the open version's last confirmation may open, extend or
  close it. This is the ordinary path.
* An observation **older than** the open version's beginning, carrying the *same* state,
  pulls ``valid_from`` back. That is genuinely new knowledge -- the state held earlier than
  anything previously seen -- and it adds no contradiction.
* An observation older than the open version's last confirmation that carries a *different*
  state is **not applied**. Something newer has already confirmed the current state, and
  splicing a contradicting reading underneath it would either overlap an interval or
  rewrite one that newer evidence supports. It is counted as ``stale`` and reported, not
  silently dropped.

## Why a tombstone is written, rather than a row deleted

Nothing in ADG deletes a collected fact, and absence does not change that. A closure closes
the present version with reason ``absent`` and opens a **tombstone** -- a version with no
state, covering from the reconciling run's completion onward. The tombstone is what makes
"ADG knows this was gone on Tuesday" distinguishable from "ADG has nothing for Tuesday",
and only the first of those may ever be rendered as a deletion.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import UUID

from sqlalchemy import (
    CursorResult,
    RowMapping,
    Select,
    Text,
    and_,
    any_,
    bindparam,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind, ScopeKind
from app.domain.errors import DomainValidationError
from app.domain.observation import CollectorKind
from app.history.bindings import KindBinding, binding_for
from app.history.closure import (
    ClosureRule,
    ColumnEquals,
    DirectorySubtree,
    LocalScoping,
    RunDomains,
    Selector,
    SubtreeOfScopeKey,
    ViaParent,
    closure_rules_for,
    directory_subtree_of,
)
from app.history.model import (
    HISTORICAL_KINDS,
    state_digest,
    stored_state,
)
from app.models.schema import CloseReason, VersionOrigin, object_versions, observations, principals

__all__ = ["ClosureOutcome", "HistoryOutcome", "HistoryWriter"]

KEY_CHUNK: Final = 5_000
"""Keys per ``= ANY(...)`` lookup. Matches the membership repository's chunk, for the same
reason: one array parameter per statement, sized so a large batch costs a handful of
statements rather than one per object."""


@dataclass(frozen=True, slots=True)
class HistoryOutcome:
    """What one call to :meth:`HistoryWriter.record` did to the timeline."""

    opened: int = 0
    """Objects seen for the first time, or seen with a state no open version held."""

    extended: int = 0
    """Open versions whose interval grew because this observation confirmed them."""

    unchanged: int = 0
    """Observations that confirmed a version already covering their instant. Counted rather
    than folded into ``extended`` so that a replayed batch is visibly a replay."""

    superseded: int = 0
    """Open versions closed because this observation showed a different state."""

    revived: int = 0
    """Tombstones closed because the object was observed again."""

    replaced: int = 0
    """Open versions overwritten in place because a contradicting observation carried the
    very instant they began at, leaving them no interval to have held over. See
    :meth:`HistoryWriter._replacement`."""

    stale: int = 0
    """Observations too old to be applied; see the module docstring."""

    def __add__(self, other: HistoryOutcome) -> HistoryOutcome:
        return HistoryOutcome(
            opened=self.opened + other.opened,
            extended=self.extended + other.extended,
            unchanged=self.unchanged + other.unchanged,
            superseded=self.superseded + other.superseded,
            revived=self.revived + other.revived,
            replaced=self.replaced + other.replaced,
            stale=self.stale + other.stale,
        )

    @property
    def versions_written(self) -> int:
        """New rows this call inserted: a first sighting, a change, or a revival."""
        return self.opened


@dataclass(frozen=True, slots=True)
class ClosureOutcome:
    """What one reconciled scope marked absent."""

    scope_kind: ScopeKind
    scope_key: str
    closed: dict[ObservationKind, int]
    """Objects tombstoned, per kind. Empty when the scope was fully observed."""

    reaffirmed: dict[ObservationKind, int]
    """Existing tombstones confirmed still absent, per kind."""

    skipped_reason: str | None = None
    """Why this scope closed nothing at all, when that was a decision rather than an
    outcome: no closure rule for the collector, or a scope key that does not parse."""

    @property
    def total_closed(self) -> int:
        return sum(self.closed.values())


@dataclass(frozen=True, slots=True)
class AffirmationOutcome:
    """What a batch of affirmations did to the timeline (Phase 7B).

    The three refusal lists are the point of this type. An affirmation that cannot be
    applied is not an error and must not fail the batch -- the collector re-reads the object
    in full on the next pass -- but it must not be *silent* either, because an affirmation
    silently dropped looks exactly like one applied, and the object would then be missing
    from the run's coverage without anything saying so.
    """

    extended: int = 0
    """Open versions whose interval grew because the affirmation confirmed them."""

    unchanged: int = 0
    """Affirmations confirming a span the open version already covers -- a replay, or a
    reading older than the last confirmation. Counted, not folded into ``extended``."""

    untracked: tuple[str, ...] = ()
    """Keys with no open version at all. ADG has never stored this object, so there is no
    state for the digest to have matched and nothing to confirm."""

    absent: tuple[str, ...] = ()
    """Keys whose open version is a tombstone. The collector says the object is unchanged
    and ADG's record says it is gone; reviving it is a claim about state, which only a full
    observation carries. Refused, so the collector sends the object properly."""

    def __add__(self, other: AffirmationOutcome) -> AffirmationOutcome:
        return AffirmationOutcome(
            extended=self.extended + other.extended,
            unchanged=self.unchanged + other.unchanged,
            untracked=self.untracked + other.untracked,
            absent=self.absent + other.absent,
        )

    @property
    def applied(self) -> int:
        return self.extended + self.unchanged

    def as_history_outcome(self) -> HistoryOutcome:
        """The same counts in the vocabulary a batch reports its timeline effect in.

        An affirmation can only ever extend or confirm, so the other six counters are zero
        by construction rather than by omission. Folding it in means a batch's reported
        history covers everything that batch did to the timeline, which is what a caller
        reading one number needs it to mean.
        """
        return HistoryOutcome(extended=self.extended, unchanged=self.unchanged)

    @property
    def refused_keys(self) -> tuple[str, ...]:
        return self.untracked + self.absent


@dataclass(frozen=True, slots=True)
class _Incoming:
    """One observed state, ready to compare against whatever is open."""

    key: str
    state: dict[str, Any]
    digest: str
    observed_at: dt.datetime
    run_id: UUID
    container_key: str | None
    related_key: str | None


@dataclass(frozen=True, slots=True)
class _OpenVersion:
    """The open version of one object, as much of it as a decision needs."""

    version_id: int
    key: str
    is_present: bool
    state_hash: str | None
    valid_from: dt.datetime
    last_seen_at: dt.datetime
    opened_by_run_id: UUID


class HistoryWriter:
    """Version bookkeeping over one session. Commits nothing; the caller owns the
    transaction, so a batch that fails halfway leaves neither current state nor history
    partly written."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ recording

    async def record(
        self,
        kind: ObservationKind,
        rows: Sequence[Mapping[str, Any]],
        now: dt.datetime,
    ) -> HistoryOutcome:
        """Fold one kind's just-written rows into the timeline.

        ``rows`` are the dictionaries the current-state upsert was given, provenance columns
        included; this method strips them itself rather than trusting a caller to.
        """
        if kind not in HISTORICAL_KINDS:
            raise DomainValidationError(
                f"{kind.value} is not a historically tracked kind.", field="kind"
            )
        if not rows:
            return HistoryOutcome()

        binding = binding_for(kind)
        incoming = self._incoming(binding, rows)
        open_versions = await self._open_versions(kind, list(incoming))

        to_open: list[dict[str, Any]] = []
        to_extend: list[dict[str, Any]] = []
        to_replace: list[dict[str, Any]] = []
        to_close: list[dict[str, Any]] = []
        outcome = HistoryOutcome()

        for key, entry in incoming.items():
            current = open_versions.get(key)
            if current is None:
                to_open.append(self._new_version(binding, entry, now))
                outcome += HistoryOutcome(opened=1)
                continue

            if current.is_present and current.state_hash == entry.digest:
                extension = self._extension(
                    current, observed_at=entry.observed_at, run_id=entry.run_id, now=now
                )
                if extension is None:
                    outcome += HistoryOutcome(unchanged=1)
                else:
                    to_extend.append(extension)
                    outcome += HistoryOutcome(extended=1)
                continue

            if entry.observed_at < current.last_seen_at:
                # Older than the newest confirmation of what is open, and disagreeing with
                # it. See the module docstring: applying it would rewrite a span that newer
                # evidence supports.
                outcome += HistoryOutcome(stale=1)
                continue

            if entry.observed_at == current.valid_from:
                # A contradiction bearing the same instant the open version began at. There
                # is no interval over which the old state held -- versions are half-open,
                # so ``[t, t)`` covers nothing and no query could ever return it -- and the
                # later-arriving reading is the one the current-state upsert also keeps. So
                # the version is overwritten rather than split.
                to_replace.append(self._replacement(current, entry, now))
                outcome += HistoryOutcome(replaced=1, revived=1 if not current.is_present else 0)
                continue

            to_close.append(
                {
                    "target_id": current.version_id,
                    "closed_at": entry.observed_at,
                    "reason": CloseReason.SUPERSEDED.value,
                    "closing_run_id": entry.run_id,
                    "written_at": now,
                }
            )
            to_open.append(self._new_version(binding, entry, now))
            outcome += (
                HistoryOutcome(revived=1, opened=1)
                if not current.is_present
                else HistoryOutcome(superseded=1, opened=1)
            )

        # Closing before opening, because ``ux_object_versions_open`` allows exactly one
        # open version per object and PostgreSQL checks it per statement.
        if to_close:
            await self._close_versions(to_close)
        if to_extend:
            await self._extend_versions(to_extend)
        if to_replace:
            await self._replace_versions(to_replace)
        if to_open:
            await self._session.execute(insert(object_versions).values(to_open))
        return outcome

    # ------------------------------------------------------------------ affirmation

    async def affirm(
        self,
        kind: ObservationKind,
        keys: Sequence[str],
        *,
        observed_at: dt.datetime,
        run_id: UUID,
        now: dt.datetime,
    ) -> AffirmationOutcome:
        """Confirm that these objects still hold the state already recorded (Phase 7B).

        This is :meth:`record` with the comparison already settled. ``record`` computes a
        digest from an incoming payload and, when it equals the open version's, extends the
        interval; an affirmation *is* the claim that the two are equal, verified by the
        caller against the digest the collector sent. So the same extension is applied by
        the same helper, and an affirmation cannot do anything to a timeline that an
        identical re-observation would not have done. ``tests/db/test_history_affirm.py``
        asserts that equivalence directly rather than leaving it as a claim in a docstring.

        What this method will not do is open a version, close one, or change a state. Every
        one of those is a statement about what an object *is*, and an affirmation carries no
        state to make it with -- so a key with nothing open, or with a tombstone open, is
        refused and named in the outcome rather than being quietly given one.
        """
        if kind not in HISTORICAL_KINDS:
            raise DomainValidationError(
                f"{kind.value} is not a historically tracked kind.", field="kind"
            )
        unique = sorted(set(keys))
        if not unique:
            return AffirmationOutcome()

        open_versions = await self._open_versions(kind, unique)

        extensions: list[dict[str, Any]] = []
        unchanged = 0
        untracked: list[str] = []
        absent: list[str] = []
        for key in unique:
            current = open_versions.get(key)
            if current is None:
                untracked.append(key)
                continue
            if not current.is_present:
                absent.append(key)
                continue
            extension = self._extension(current, observed_at=observed_at, run_id=run_id, now=now)
            if extension is None:
                unchanged += 1
            else:
                extensions.append(extension)

        if extensions:
            await self._extend_versions(extensions)
        return AffirmationOutcome(
            extended=len(extensions),
            unchanged=unchanged,
            untracked=tuple(untracked),
            absent=tuple(absent),
        )

    async def affirm_contained(
        self,
        kind: ObservationKind,
        container_keys: Sequence[str],
        *,
        observed_at: dt.datetime,
        run_id: UUID,
        now: dt.datetime,
    ) -> AffirmationOutcome:
        """Confirm every open version of ``kind`` whose container is one of these keys.

        The entries of a container are not independently affirmable and must not be
        affirmed one by one. An NTFS ACE is not something a collector enumerates: it reads a
        *descriptor*, and the entries come with it. What the collector verified is therefore
        the descriptor's digest, and what that digest covers is the whole DACL -- so the
        honest unit of affirmation is "the entries of this resource", which is exactly the
        predicate here.

        It is also the only affordable one. A batch may affirm 5,000 resources; listing
        their entries would mean tens of thousands of keys crossing into Python and back
        out again per batch, which is the cost this whole mechanism exists to avoid. Two
        indexed statements do it instead, on ``ix_object_versions_container``.

        Unlike :meth:`affirm`, this reports no per-key refusals. There is nothing sensible
        to report: the caller verified a digest over the container, so a contained version
        that is missing or tombstoned is not a collector error but a disagreement between
        two things ADG itself stores -- the resource's digest and its entries -- which
        ingestion checks when the entries arrive rather than when they are affirmed.
        """
        if kind not in HISTORICAL_KINDS:
            raise DomainValidationError(
                f"{kind.value} is not a historically tracked kind.", field="kind"
            )
        unique = sorted(set(container_keys))
        if not unique:
            return AffirmationOutcome()

        extended = 0
        for chunk in _chunks(unique, KEY_CHUNK):
            containers = any_(bindparam("containers", chunk, type_=ARRAY(Text)))
            open_present = (
                object_versions.c.object_kind == kind.value,
                object_versions.c.valid_to.is_(None),
                object_versions.c.is_present.is_(True),
                object_versions.c.container_key == containers,
            )
            # Forward and backward as two statements, because they set different columns:
            # a later confirmation moves last_seen_at and the run that last saw it, an
            # earlier one moves valid_from and the run that opened it. This is
            # :meth:`_extension`'s rule, expressed as a predicate instead of a row.
            forward = await self._session.execute(
                update(object_versions)
                .where(*open_present, object_versions.c.last_seen_at < observed_at)
                .values(last_seen_at=observed_at, last_seen_run_id=run_id, updated_at=now)
            )
            backward = await self._session.execute(
                update(object_versions)
                .where(*open_present, object_versions.c.valid_from > observed_at)
                .values(valid_from=observed_at, opened_by_run_id=run_id, updated_at=now)
            )
            # `rowcount` lives on CursorResult, which is what a DML statement returns;
            # `Session.execute` is typed as returning the base Result.
            extended += max(
                cast("CursorResult[Any]", forward).rowcount,
                cast("CursorResult[Any]", backward).rowcount,
            )
        return AffirmationOutcome(extended=extended)

    def _incoming(
        self, binding: KindBinding, rows: Sequence[Mapping[str, Any]]
    ) -> dict[str, _Incoming]:
        """One entry per object key, keeping the newest reading when a batch repeats one."""
        latest: dict[str, _Incoming] = {}
        for row in rows:
            state = stored_state(row)
            entry = _Incoming(
                key=str(row[binding.key_column]),
                state=state,
                digest=state_digest(row),
                observed_at=row["last_observed_at"],
                run_id=row["last_observed_run_id"],
                container_key=_optional_key(binding.container_column, row),
                related_key=_optional_key(binding.related_column, row),
            )
            previous = latest.get(entry.key)
            if previous is None or entry.observed_at >= previous.observed_at:
                latest[entry.key] = entry
        return latest

    def _new_version(
        self, binding: KindBinding, entry: _Incoming, now: dt.datetime
    ) -> dict[str, Any]:
        return {
            "object_kind": binding.kind.value,
            "object_key": entry.key,
            "container_key": entry.container_key,
            "related_key": entry.related_key,
            "is_present": True,
            "state": entry.state,
            "state_hash": entry.digest,
            "origin": VersionOrigin.OBSERVED.value,
            "valid_from": entry.observed_at,
            "last_seen_at": entry.observed_at,
            "valid_to": None,
            "close_reason": None,
            "opened_by_run_id": entry.run_id,
            "last_seen_run_id": entry.run_id,
            "closed_by_run_id": None,
            "created_at": now,
            "updated_at": now,
        }

    def _extension(
        self,
        current: _OpenVersion,
        *,
        observed_at: dt.datetime,
        run_id: UUID,
        now: dt.datetime,
    ) -> dict[str, Any] | None:
        """The update that grows an open version, or ``None`` when it already covers this.

        An observation can grow a version at either end. Later than the last confirmation,
        it extends the interval forward. Earlier than the beginning — a delayed run
        reporting the same state — it extends it backward, and the run that opened the
        version changes with it, because the earliest evidence is what opened it.
        """
        forward = observed_at > current.last_seen_at
        backward = observed_at < current.valid_from
        if not forward and not backward:
            return None
        return {
            "target_id": current.version_id,
            "new_last_seen_at": observed_at if forward else current.last_seen_at,
            "new_last_seen_run_id": run_id if forward else None,
            "new_valid_from": observed_at if backward else current.valid_from,
            "new_opened_by_run_id": run_id if backward else None,
            "written_at": now,
        }

    def _replacement(
        self, current: _OpenVersion, entry: _Incoming, now: dt.datetime
    ) -> dict[str, Any]:
        """Overwrite a zero-width open version with the state that contradicted it.

        ``valid_from`` is kept: the instant is not in dispute, only what was true at it.
        ``origin`` becomes ``observed`` because the state now comes from an observation even
        if the row it overwrites was reconstructed by the migration.
        """
        return {
            "target_id": current.version_id,
            "new_is_present": True,
            "new_state": entry.state,
            "new_state_hash": entry.digest,
            "new_container_key": entry.container_key,
            "new_related_key": entry.related_key,
            "new_last_seen_at": entry.observed_at,
            "new_run_id": entry.run_id,
            "written_at": now,
        }

    async def _replace_versions(self, replacements: Sequence[dict[str, Any]]) -> None:
        statement = (
            update(object_versions)
            .where(object_versions.c.id == bindparam("target_id"))
            .values(
                is_present=bindparam("new_is_present"),
                state=bindparam("new_state", type_=JSONB(none_as_null=True)),
                state_hash=bindparam("new_state_hash"),
                container_key=bindparam("new_container_key"),
                related_key=bindparam("new_related_key"),
                last_seen_at=bindparam("new_last_seen_at"),
                last_seen_run_id=bindparam("new_run_id"),
                opened_by_run_id=bindparam("new_run_id"),
                origin=literal(VersionOrigin.OBSERVED.value),
                updated_at=bindparam("written_at"),
            )
        )
        await self._session.execute(statement, list(replacements))

    async def _extend_versions(self, extensions: Sequence[dict[str, Any]]) -> None:
        """Apply the extensions in one round trip.

        The two run-id parameters are nullable in the payload and coalesced in SQL rather
        than resolved in Python, so that the row's existing value is kept without this
        method having to read it back first.
        """
        statement = (
            update(object_versions)
            .where(object_versions.c.id == bindparam("target_id"))
            .values(
                last_seen_at=bindparam("new_last_seen_at"),
                last_seen_run_id=_coalesce_run("new_last_seen_run_id", "last_seen_run_id"),
                valid_from=bindparam("new_valid_from"),
                opened_by_run_id=_coalesce_run("new_opened_by_run_id", "opened_by_run_id"),
                updated_at=bindparam("written_at"),
            )
        )
        await self._session.execute(statement, list(extensions))

    async def _close_versions(self, closures: Sequence[dict[str, Any]]) -> None:
        statement = (
            update(object_versions)
            .where(object_versions.c.id == bindparam("target_id"))
            .values(
                valid_to=bindparam("closed_at"),
                close_reason=bindparam("reason"),
                closed_by_run_id=bindparam("closing_run_id"),
                updated_at=bindparam("written_at"),
            )
        )
        await self._session.execute(statement, list(closures))

    async def _open_versions(
        self, kind: ObservationKind, keys: Sequence[str]
    ) -> dict[str, _OpenVersion]:
        found: dict[str, _OpenVersion] = {}
        for chunk in _chunks(list(keys), KEY_CHUNK):
            statement = select(
                object_versions.c.id,
                object_versions.c.object_key,
                object_versions.c.is_present,
                object_versions.c.state_hash,
                object_versions.c.valid_from,
                object_versions.c.last_seen_at,
                object_versions.c.opened_by_run_id,
            ).where(
                object_versions.c.object_kind == kind.value,
                object_versions.c.object_key == any_(bindparam("keys", chunk, type_=ARRAY(Text))),
                object_versions.c.valid_to.is_(None),
            )
            for row in (await self._session.execute(statement)).mappings():
                found[row["object_key"]] = _OpenVersion(
                    version_id=int(row["id"]),
                    key=row["object_key"],
                    is_present=bool(row["is_present"]),
                    state_hash=row["state_hash"],
                    valid_from=row["valid_from"],
                    last_seen_at=row["last_seen_at"],
                    opened_by_run_id=row["opened_by_run_id"],
                )
        return found

    # ------------------------------------------------------------------- absence

    async def close_absent(
        self,
        *,
        run_id: UUID,
        collector: CollectorKind,
        scope_kind: ScopeKind,
        scope_key: str,
        completed_at: dt.datetime,
        now: dt.datetime,
    ) -> ClosureOutcome:
        """Tombstone everything inside one reconciled scope that this run did not observe.

        The caller must already have established that the run may reconcile at all; this
        method enforces only the parts that are properties of the scope itself — which kinds
        it covers, and which objects lie inside it.
        """
        rules = closure_rules_for(collector, scope_kind)
        if not rules:
            return ClosureOutcome(
                scope_kind=scope_kind,
                scope_key=scope_key,
                closed={},
                reaffirmed={},
                skipped_reason=(
                    f"The {collector.value} collector has no closure rule for a "
                    f"{scope_kind.value} scope, so nothing inside it may be inferred absent."
                ),
            )

        subtree: DirectorySubtree | None = None
        if scope_kind is ScopeKind.DIRECTORY_TREE:
            try:
                subtree = directory_subtree_of(scope_key)
            except DomainValidationError as error:
                # The run's observations are already stored and its completion is being
                # accepted; refusing it now would lose them over a malformed scope key.
                # Closing nothing is the safe reading of a boundary ADG cannot locate.
                return ClosureOutcome(
                    scope_kind=scope_kind,
                    scope_key=scope_key,
                    closed={},
                    reaffirmed={},
                    skipped_reason=(
                        f"The directory_tree scope key {scope_key!r} is not a UNC path "
                        f"({error}), so its boundary cannot be located and nothing inside "
                        "it may be inferred absent."
                    ),
                )

        by_kind = {rule.kind: rule for rule in rules}
        closed: dict[ObservationKind, int] = {}
        reaffirmed: dict[ObservationKind, int] = {}
        for rule in rules:
            candidates = self._scope_candidates(
                rule.kind, by_kind, run_id=run_id, scope_key=scope_key, subtree=subtree
            )
            unobserved = await self._unobserved_open_versions(
                rule.kind, candidates, run_id=run_id, completed_at=completed_at
            )
            present = [row for row in unobserved if row["is_present"]]
            tombstones = [row for row in unobserved if not row["is_present"]]

            if present:
                await self._close_versions(
                    [
                        {
                            "target_id": int(row["id"]),
                            "closed_at": completed_at,
                            "reason": CloseReason.ABSENT.value,
                            "closing_run_id": run_id,
                            "written_at": now,
                        }
                        for row in present
                    ]
                )
                await self._session.execute(
                    insert(object_versions).values(
                        [
                            {
                                "object_kind": rule.kind.value,
                                "object_key": row["object_key"],
                                "container_key": row["container_key"],
                                "related_key": row["related_key"],
                                "is_present": False,
                                "state": None,
                                "state_hash": None,
                                "origin": VersionOrigin.OBSERVED.value,
                                "valid_from": completed_at,
                                "last_seen_at": completed_at,
                                "valid_to": None,
                                "close_reason": None,
                                "opened_by_run_id": run_id,
                                "last_seen_run_id": run_id,
                                "closed_by_run_id": None,
                                "created_at": now,
                                "updated_at": now,
                            }
                            for row in present
                        ]
                    )
                )
                closed[rule.kind] = len(present)

            if tombstones:
                # A scan that looked again and still did not find it confirms the absence,
                # exactly as a re-observation confirms a presence. Without this, every
                # tombstone would look like a single moment rather than a state that has
                # held since.
                await self._extend_versions(
                    [
                        {
                            "target_id": int(row["id"]),
                            "new_last_seen_at": completed_at,
                            "new_last_seen_run_id": run_id,
                            "new_valid_from": row["valid_from"],
                            "new_opened_by_run_id": None,
                            "written_at": now,
                        }
                        for row in tombstones
                    ]
                )
                reaffirmed[rule.kind] = len(tombstones)

        return ClosureOutcome(
            scope_kind=scope_kind, scope_key=scope_key, closed=closed, reaffirmed=reaffirmed
        )

    def _scope_candidates(
        self,
        kind: ObservationKind,
        rules: Mapping[ObservationKind, ClosureRule],
        *,
        run_id: UUID,
        scope_key: str,
        subtree: DirectorySubtree | None,
    ) -> Select[Any]:
        """A ``SELECT`` of the current-state keys of ``kind`` that lie inside one scope.

        Driven off the current-state table rather than off ``object_versions`` so that the
        indexes the product already relies on -- ``ix_smb_shares_server``,
        ``ix_ntfs_resources_share`` -- do the work, and a scope's candidate set costs the
        same as listing that scope costs anywhere else in the API.

        ``ViaParent`` recurses into the parent kind's own rule, looked up in ``rules``,
        rather than carrying a copy of it. :func:`app.history.closure.closure_rules_for`
        has already established that the parent rule is there.
        """
        binding = binding_for(kind)
        selector = _resolved(rules[kind].selector, subtree)
        table = binding.table
        conditions: list[Any] = []

        if isinstance(selector, ColumnEquals):
            conditions.append(table.c[selector.column] == scope_key)
        elif isinstance(selector, DirectorySubtree):
            conditions.append(table.c.share_key == selector.share_key)
            if not selector.is_share_root:
                # ``starts_with`` rather than ``LIKE``: PostgreSQL's default LIKE escape
                # character is a backslash, which is the separator in every UNC path, and
                # ``_`` is a LIKE wildcard and an ordinary character in a folder name. A
                # pattern built from a real path would both mis-escape and over-match.
                conditions.append(
                    (table.c[binding.key_column] == selector.tree_key)
                    | func.starts_with(table.c[binding.key_column], f"{selector.tree_key}\\")
                )
        elif isinstance(selector, RunDomains):
            conditions.append(table.c[selector.column].in_(self._domains_observed_by(run_id)))
        elif isinstance(selector, ViaParent):
            parent_keys = self._scope_candidates(
                selector.parent, rules, run_id=run_id, scope_key=scope_key, subtree=subtree
            )
            conditions.append(table.c[selector.column].in_(parent_keys))
        else:  # pragma: no cover - the selector union is closed and resolved above
            raise DomainValidationError(f"Unknown scope selector {selector!r}.", field="selector")

        scoping = getattr(selector, "scoping", LocalScoping.ANY)
        if scoping is LocalScoping.DOMAIN_ONLY:
            conditions.append(table.c.host_key.is_(None))
        elif scoping is LocalScoping.HOST_ONLY:
            conditions.append(table.c.host_key.is_not(None))

        return select(table.c[binding.key_column]).where(and_(*conditions))

    def _domains_observed_by(self, run_id: UUID) -> Select[Any]:
        """The domain SIDs of the principals one run reported. See :class:`RunDomains`."""
        return (
            select(principals.c.domain_sid)
            .join(
                observations,
                and_(
                    observations.c.subject_key == principals.c.principal_key,
                    observations.c.kind == ObservationKind.PRINCIPAL.value,
                ),
            )
            .where(observations.c.run_id == run_id, principals.c.domain_sid.is_not(None))
            .distinct()
        )

    async def _unobserved_open_versions(
        self,
        kind: ObservationKind,
        candidates: Select[Any],
        *,
        run_id: UUID,
        completed_at: dt.datetime,
    ) -> Sequence[RowMapping]:
        """Open versions inside the scope that this run did not touch.

        Three conditions beyond "inside the scope", and each one is a way an absence could
        otherwise be invented:

        * the run did not observe the object -- checked against ``observations``, which is
          the run's own record of what it saw, rather than against which rows it happened
          to win an upsert on;
        * the version was last confirmed at or before this run finished. A version
          confirmed *after* this run completed was seen by something newer, and a stale run
          may not declare absent what a later one just found;
        * the version is open. A closed one has already been superseded and says nothing
          about now.
        """
        seen = select(literal(1)).where(
            observations.c.run_id == run_id,
            observations.c.kind == kind.value,
            observations.c.subject_key == object_versions.c.object_key,
        )
        statement = (
            select(
                object_versions.c.id,
                object_versions.c.object_key,
                object_versions.c.container_key,
                object_versions.c.related_key,
                object_versions.c.is_present,
                object_versions.c.valid_from,
            )
            .where(
                object_versions.c.object_kind == kind.value,
                object_versions.c.valid_to.is_(None),
                object_versions.c.last_seen_at <= completed_at,
                object_versions.c.object_key.in_(candidates),
                ~seen.exists(),
            )
            .order_by(object_versions.c.object_key)
        )
        return (await self._session.execute(statement)).mappings().all()


def _resolved(selector: Selector, subtree: DirectorySubtree | None) -> Selector:
    """Fill in the one selector that cannot be a constant. See :class:`SubtreeOfScopeKey`."""
    if not isinstance(selector, SubtreeOfScopeKey):
        return selector
    if subtree is None:  # pragma: no cover - only a directory_tree scope carries this rule
        raise DomainValidationError(
            "A subtree selector was reached without a parsed directory_tree scope key.",
            field="selector",
        )
    return subtree


def _coalesce_run(parameter: str, column: str) -> Any:
    """``COALESCE(:parameter, <column>)`` for a run-id column."""
    return func.coalesce(bindparam(parameter), object_versions.c[column])


def _optional_key(column: str | None, row: Mapping[str, Any]) -> str | None:
    if column is None:
        return None
    value = row.get(column)
    return None if value is None else str(value)


def _chunks(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
