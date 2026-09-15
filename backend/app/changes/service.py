r"""The three questions the Changes page asks, and what each one costs.

1. **What changed since yesterday?** — :meth:`ChangeService.feed`, a window of transitions,
   newest first, filtered and paged.
2. **How much of it matters?** — :meth:`ChangeService.summary`, counts over the same window
   taken **before** the filter, so a page that shows twelve lines can say that it hid four
   hundred.
3. **What is different between these two instants?** — :meth:`ChangeService.compare`, which
   is a different question from the first and gives a different answer on purpose.

## The feed and the comparison disagree, correctly

A feed over Tuesday-to-Friday reports an ACE that was added on Wednesday and removed on
Thursday as two changes. A comparison of Tuesday against Friday reports nothing about it: at
both instants it was not there. Neither answer is wrong. The feed answers *what happened*,
which is what an incident review needs; the comparison answers *what is different*, which is
what a change-control review needs. Offering only one of them would quietly answer the other
question badly, so both exist and the responses say which they are.

## Filtering happens after classification, and that shapes the paging

Whether a change is security-relevant is not a column — it is a conclusion drawn from two
versions, the field table and, for an ordering change, a reconstructed ACL. So the filter
cannot be pushed into SQL, and a page of a hundred raw versions can yield three changes an
operator asked to see.

Rather than return a nearly empty page, :meth:`ChangeService.feed` keeps reading until the
page is full or its **scan budget** runs out, and then says which of the two happened.
``scan_exhausted`` is reported rather than hidden because the two endings mean different
things: a full page means there is more, and an exhausted budget means ADG stopped looking
before the window did — and a client that treated the second as the end of the data would
show a shorter history than exists.

## A window is required, and an unscoped one is bounded

The window is the driving predicate of every query here (``ix_object_versions_opened_at``),
so an unbounded one is a scan of the whole timeline. A **scoped** query is bounded by its
scope instead and may span any interval — "everything that ever happened to this share" is a
reasonable question. An **unscoped** one is capped at :data:`MAX_UNSCOPED_WINDOW` and refused
beyond it, with the scope suggested in the message, rather than accepted and served slowly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.changes.classify import classify
from app.changes.correlation import Correlation, correlate, key_of, ordering_materiality
from app.changes.fields import container_kind_of
from app.changes.model import (
    ChangeAction,
    ChangeSeverity,
    ChangeSignificance,
    ChangeSummary,
    ObjectChange,
    SummaryTally,
    at_least,
)
from app.changes.repository import ChangeRepository, VersionCursor
from app.changes.scope import ChangeScope
from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError
from app.history.model import ObjectVersion
from app.history.service import HistoryService

__all__ = [
    "DEFAULT_ACTIONS",
    "DEFAULT_SIGNIFICANCE",
    "MAX_UNSCOPED_WINDOW",
    "ChangeComparison",
    "ChangeFeed",
    "ChangeFilter",
    "ChangeService",
    "ObjectChanges",
]

MAX_UNSCOPED_WINDOW: Final = dt.timedelta(days=400)
"""The longest window an estate-wide query may span. Long enough for "since last year's
audit"; short enough that it cannot become "everything, ever" by accident. A scoped query is
not subject to it — see the module docstring."""

SCAN_BUDGET: Final = 4_000
"""Raw versions one :meth:`ChangeService.feed` call may examine before giving up on filling
the page. Ten pages of the maximum page size: enough that a page of security changes fills
even in a window dominated by metadata, bounded enough that a pathological filter cannot
walk the whole timeline in one request."""

SUMMARY_CEILING: Final = 2_000
"""Changes one summary classifies before reporting itself truncated. A summary is a page
header; a page header that costs a classification of a million transitions is a page that
times out, and a header that lies about its own completeness is worse than one that admits
it."""

COMPARISON_CEILING: Final = 5_000
"""Objects one point-in-time comparison reads **per side**."""

DEFAULT_ACTIONS: Final[frozenset[ChangeAction]] = frozenset(
    {ChangeAction.ADDED, ChangeAction.MODIFIED, ChangeAction.REMOVED}
)
"""What the feed shows when nobody says otherwise.

:attr:`ChangeAction.FIRST_OBSERVED` is excluded because it is definitionally not a change:
an estate's first scan produces one per object, and a "what changed yesterday" page whose
first answer is four million first sightings has answered a different question. It is still
**counted** in every summary, so the exclusion is visible rather than silent."""

DEFAULT_SIGNIFICANCE: Final[frozenset[ChangeSignificance]] = frozenset(
    {ChangeSignificance.SECURITY, ChangeSignificance.UNDETERMINED}
)
"""What the feed shows when nobody says otherwise.

Undetermined is in the default set and that is the whole reason the value exists: a change
ADG could not classify is the one an operator most needs to see, and a default that quietly
dropped it would make ADG's own gaps invisible in exactly the place they matter."""


@dataclass(frozen=True, slots=True)
class ChangeFilter:
    """Which changes a caller wants, over which interval.

    Everything except the window has a default that is a deliberate editorial choice rather
    than "everything": see :data:`DEFAULT_ACTIONS` and :data:`DEFAULT_SIGNIFICANCE`. The
    filter is echoed back on every response, so a caller always knows which choice produced
    the list in front of them.
    """

    window_from: dt.datetime
    window_to: dt.datetime
    scope: ChangeScope | None = None
    kinds: frozenset[ObservationKind] | None = None
    actions: frozenset[ChangeAction] = DEFAULT_ACTIONS
    significance: frozenset[ChangeSignificance] = DEFAULT_SIGNIFICANCE
    min_severity: ChangeSeverity = ChangeSeverity.INFO
    limit: int = 100

    def __post_init__(self) -> None:
        for name in ("window_from", "window_to"):
            moment: dt.datetime = getattr(self, name)
            if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
                raise DomainValidationError(
                    f"{name} must be timezone-aware. A naive instant cannot be ordered "
                    "against observations recorded by a collector in another time zone, "
                    "and a window an hour out reports a different day's changes.",
                    field=name,
                )
        if self.window_to <= self.window_from:
            raise DomainValidationError(
                f"A change window must end after it begins; received "
                f"{self.window_from.isoformat()} to {self.window_to.isoformat()}. An empty "
                "window returns no changes, which is indistinguishable from a quiet week.",
                field="window_to",
            )
        if self.scope is None and self.window_to - self.window_from > MAX_UNSCOPED_WINDOW:
            raise DomainValidationError(
                f"An estate-wide change window is limited to {MAX_UNSCOPED_WINDOW.days} "
                "days; narrow the window, or name a server, share, directory, principal or "
                "group and ask for any interval.",
                field="window_from",
            )
        if not self.actions:
            raise DomainValidationError(
                "A change query that excludes every action returns nothing, which reads as "
                "a quiet window rather than as an empty filter.",
                field="actions",
            )
        if not self.significance:
            raise DomainValidationError(
                "A change query that excludes every significance returns nothing, which "
                "reads as a quiet window rather than as an empty filter.",
                field="significance",
            )

    def admits(self, change: ObjectChange) -> bool:
        return (
            change.action in self.actions
            and change.significance in self.significance
            and at_least(change.severity, self.min_severity)
        )


@dataclass(frozen=True, slots=True)
class ChangeFeed:
    """One page of classified changes, and what produced it."""

    filters: ChangeFilter
    changes: tuple[ObjectChange, ...]
    correlation: Correlation
    has_more: bool
    next_cursor: VersionCursor | None
    scanned: int
    """Raw versions examined to build this page, filtered or not."""

    scan_exhausted: bool
    """Whether the page ended because the scan budget ran out rather than because the page
    filled or the window did. More changes exist either way; this says the page is short for
    a reason a client cannot see from its length."""


@dataclass(frozen=True, slots=True)
class ObjectChanges:
    """One object's whole history, as transitions rather than as versions."""

    kind: ObservationKind
    key: str
    changes: tuple[ObjectChange, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class ChangeComparison:
    """What is different between two instants, within one scope."""

    at_from: dt.datetime
    at_to: dt.datetime
    scope: ChangeScope | None
    changes: tuple[ObjectChange, ...]
    correlation: Correlation
    unchanged: int
    """Objects present and identical at both instants."""

    unobserved_at_from: int
    """Objects covered at the later instant and by **no version at all** at the earlier one.

    The ones holding a *state* at the later instant appear in ``changes`` as well, as
    additions or first sightings depending on whether their container was already being
    read — so this count and that list deliberately overlap. The ones whose later version is
    a **tombstone** appear in neither: nothing covers the earlier instant and the later one
    is a measured absence, so there is no state on either side and calling it a removal
    would claim the object existed at the earlier instant."""

    unobserved_at_to: int
    """Objects covered at the earlier instant and by no version at the later one. Counted
    rather than reported as removals, for the same reason and with more at stake: it would
    report access as revoked on the strength of nobody having looked."""

    truncated: bool
    summary: ChangeSummary


@dataclass
class _Scan:
    """Mutable accumulator for one :meth:`ChangeService.feed` call."""

    collected: list[ObjectChange] = field(default_factory=list)
    examined: int = 0
    cursor: VersionCursor | None = None
    has_more: bool = False
    exhausted: bool = False


class ChangeService:
    """Change queries over one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = ChangeRepository(session)
        self._history = HistoryService(session)

    # ----------------------------------------------------------------------- feed

    async def feed(
        self, filters: ChangeFilter, *, cursor: VersionCursor | None = None
    ) -> ChangeFeed:
        """One page of changes matching ``filters``, newest first."""
        state = _Scan(cursor=cursor)
        batch_size = max(filters.limit, 50)
        while len(state.collected) < filters.limit:
            page = await self._repository.opened_between(
                filters.window_from,
                filters.window_to,
                kinds=filters.kinds,
                scope=filters.scope,
                cursor=state.cursor,
                limit=batch_size,
            )
            if not page.versions:
                state.has_more = False
                break
            classified = await self._classify_all(page.versions)
            state.examined += len(page.versions)
            filled = self._absorb(state, classified, filters)
            if filled:
                state.has_more = True
                break
            state.cursor = page.next_cursor
            state.has_more = page.has_more
            if not page.has_more:
                break
            if state.examined >= SCAN_BUDGET:
                state.exhausted = True
                state.has_more = True
                break
        return ChangeFeed(
            filters=filters,
            changes=tuple(state.collected),
            correlation=correlate(state.collected),
            has_more=state.has_more,
            next_cursor=state.cursor,
            scanned=state.examined,
            scan_exhausted=state.exhausted,
        )

    @staticmethod
    def _absorb(state: _Scan, classified: Sequence[ObjectChange], filters: ChangeFilter) -> bool:
        """Take admitted changes until the page is full. Returns whether it filled.

        When it fills, the cursor is moved to **the change that filled it**, not to the end
        of the batch. Versions after it in the same batch are re-examined on the next page
        and either admitted there or filtered again — which costs a little and cannot lose
        one, where advancing past them would silently drop every change the page had no room
        for.
        """
        for change in classified:
            if not filters.admits(change):
                continue
            state.collected.append(change)
            state.cursor = VersionCursor(
                valid_from=change.after.valid_from, version_id=change.after.version_id or 0
            )
            if len(state.collected) >= filters.limit:
                return True
        return False

    # -------------------------------------------------------------------- summary

    async def summary(self, filters: ChangeFilter) -> ChangeSummary:
        """Counts over the whole window, taken before the filter is applied.

        Two numbers a client cannot compute from a page: how many changes the window holds
        at all, and how many of them the filter in use is hiding. Without the second, a
        filtered page is indistinguishable from a complete one — which is the failure this
        product is built to avoid, applied to its own interface.
        """
        tally = SummaryTally()
        cursor: VersionCursor | None = None
        while tally.total < SUMMARY_CEILING:
            page = await self._repository.opened_between(
                filters.window_from,
                filters.window_to,
                kinds=filters.kinds,
                scope=filters.scope,
                cursor=cursor,
                limit=min(500, SUMMARY_CEILING - tally.total),
            )
            for change in await self._classify_all(page.versions):
                tally.count(change, returned=filters.admits(change))
            cursor = page.next_cursor
            if not page.has_more:
                return tally.finish(filters.window_from, filters.window_to)
        # The ceiling was reached. Whether anything lies beyond it is a question the
        # database can answer far more cheaply than another pass of classification, so it
        # is asked rather than assumed: a window holding exactly SUMMARY_CEILING changes is
        # a complete summary, and reporting it as truncated would understate a full answer.
        _, more = await self._repository.count_between(
            filters.window_from,
            filters.window_to,
            kinds=filters.kinds,
            scope=filters.scope,
            ceiling=SUMMARY_CEILING,
        )
        tally.truncated = more
        return tally.finish(filters.window_from, filters.window_to)

    # ------------------------------------------------------------------- timeline

    async def object_changes(
        self, kind: ObservationKind, key: str, *, limit: int | None = None
    ) -> ObjectChanges:
        """Every transition of one object, newest first.

        Built from :class:`app.history.model.ObjectTimeline` rather than from the windowed
        query, because a timeline is already ordered and adjacent by construction — the
        predecessor of each version is the one before it in the list, with no lookup and no
        possibility of pairing the wrong two.

        The first version of the object is reported as a change only when it follows a
        tombstone. Otherwise it is where the record begins, and a timeline that opened with
        "added" would date the object's creation to the first scan.
        """
        timeline = await self._history.timeline(kind, key, limit=limit)
        changes: list[ObjectChange] = []
        for previous, following, _ in timeline.changes():
            changes.append(classify(kind, key, previous, following))
        materiality = await ordering_materiality(self._session, changes)
        if materiality:
            changes = [
                classify(
                    kind,
                    key,
                    change.before,
                    change.after,
                    ordering_material=materiality.get(key_of(change)),
                )
                for change in changes
            ]
        return ObjectChanges(
            kind=kind,
            key=key,
            changes=tuple(reversed(changes)),
            truncated=timeline.truncated,
        )

    # ------------------------------------------------------------------ comparison

    async def compare(
        self,
        at_from: dt.datetime,
        at_to: dt.datetime,
        *,
        scope: ChangeScope | None = None,
        kinds: frozenset[ObservationKind] | None = None,
        significance: frozenset[ChangeSignificance] = DEFAULT_SIGNIFICANCE,
        min_severity: ChangeSeverity = ChangeSeverity.INFO,
    ) -> ChangeComparison:
        """What is different between two instants. See the module docstring.

        Both instants must be *covered* for an object to be compared. An object with no
        version at one of them is counted in ``unobserved_at_from`` or ``unobserved_at_to``
        and reported in neither direction, because a gap in observation is not a change and
        rendering it as one is the single failure mode this product exists to prevent.
        """
        if at_to <= at_from:
            raise DomainValidationError(
                "A comparison needs two distinct instants, the later one second.",
                field="at_to",
            )
        if scope is None and at_to - at_from > MAX_UNSCOPED_WINDOW:
            raise DomainValidationError(
                f"An estate-wide comparison is limited to {MAX_UNSCOPED_WINDOW.days} days "
                "apart; narrow the interval, or name a scope and compare any two instants.",
                field="at_from",
            )
        earlier, cut_earlier = await self._repository.present_at(
            at_from, kinds=kinds, scope=scope, limit=COMPARISON_CEILING
        )
        later, cut_later = await self._repository.present_at(
            at_to, kinds=kinds, scope=scope, limit=COMPARISON_CEILING
        )
        was = {(version.kind, version.key): version for version in earlier}
        now = {(version.kind, version.key): version for version in later}

        unchanged = 0
        unobserved_at_from = 0
        unobserved_at_to = len([identity for identity in was if identity not in now])
        pairs: list[tuple[ObjectVersion | None, ObjectVersion]] = []
        for identity, after in now.items():
            before = was.get(identity)
            if before is None:
                unobserved_at_from += 1
                if after.is_tombstone:
                    # Nothing covers the earlier instant and the later one is a measured
                    # absence: there is no state on either side to compare. Reporting this
                    # as a removal would claim the object existed at the earlier instant,
                    # which nothing supports -- and it is exactly what happens to an entry
                    # that was created and deleted entirely inside the interval.
                    continue
                pairs.append((None, after))
                continue
            if before.is_present == after.is_present and before.state_hash == after.state_hash:
                unchanged += 1
                continue
            pairs.append((before, after))

        unmatched = [after for before, after in pairs if before is None]
        coverage = await self._container_coverage(unmatched)
        confirmations = await self._container_confirmations(unmatched)

        def build(
            before: ObjectVersion | None, after: ObjectVersion, material: bool | None
        ) -> ObjectChange:
            fresh = before is None
            return classify(
                after.kind,
                after.key,
                before,
                after,
                container_observed_before=(
                    self._container_observed(after, coverage) if fresh else None
                ),
                container_confirmed_at=(
                    self._container_confirmed(after, confirmations) if fresh else None
                ),
                ordering_material=material,
            )

        changes = [build(before, after, None) for before, after in pairs]
        materiality = await ordering_materiality(self._session, changes)
        if materiality:
            changes = [
                build(change.before, change.after, materiality.get(key_of(change)))
                for change in changes
            ]

        tally = SummaryTally()
        kept: list[ObjectChange] = []
        for change in changes:
            admitted = change.significance in significance and at_least(
                change.severity, min_severity
            )
            tally.count(change, returned=admitted)
            if admitted:
                kept.append(change)
        tally.truncated = cut_earlier or cut_later
        ordered = tuple(sorted(kept, key=lambda change: change.at, reverse=True))
        return ChangeComparison(
            at_from=at_from,
            at_to=at_to,
            scope=scope,
            changes=ordered,
            correlation=correlate(ordered),
            unchanged=unchanged,
            unobserved_at_from=unobserved_at_from,
            unobserved_at_to=unobserved_at_to,
            truncated=cut_earlier or cut_later,
            summary=tally.finish(at_from, at_to),
        )

    # ------------------------------------------------------------------- internals

    async def _classify_all(self, versions: Sequence[ObjectVersion]) -> list[ObjectChange]:
        """Turn a page of versions into classified changes.

        Three passes, in this order because each needs the one before it: predecessors,
        then a first classification (which produces the deltas that say which changes are
        order-only), then the ordering reconstruction, then a second classification for the
        candidates it answered about. The second pass re-runs the pure classifier rather
        than mutating the first result, so a change is always the output of one function of
        its inputs and never a partially updated object.
        """
        predecessors = await self._repository.predecessors(versions)
        first_versions = [
            version
            for version in versions
            if (version.kind, version.key, version.valid_from) not in predecessors
        ]
        coverage = await self._container_coverage(first_versions)
        confirmations = await self._container_confirmations(first_versions)
        changes = [
            classify(
                version.kind,
                version.key,
                predecessors.get((version.kind, version.key, version.valid_from)),
                version,
                container_observed_before=self._container_observed(version, coverage),
                container_confirmed_at=self._container_confirmed(version, confirmations),
                sibling_ace_changes=_sibling_ace_changes(version, versions),
            )
            for version in versions
        ]
        materiality = await ordering_materiality(self._session, changes)
        if not materiality:
            return changes
        return [
            classify(
                change.kind,
                change.key,
                change.before,
                change.after,
                container_observed_before=self._container_observed(change.after, coverage),
                container_confirmed_at=self._container_confirmed(change.after, confirmations),
                sibling_ace_changes=_sibling_ace_changes(change.after, versions),
                ordering_material=materiality.get(key_of(change)),
            )
            for change in changes
        ]

    async def _container_coverage(
        self, versions: Sequence[ObjectVersion]
    ) -> dict[tuple[ObservationKind, str], dt.datetime]:
        """When each first-version's container was first observed to exist."""
        wanted: dict[ObservationKind, list[str]] = {}
        for version in versions:
            parent = container_kind_of(version.kind)
            if parent is None or not version.container_key:
                continue
            wanted.setdefault(parent, []).append(version.container_key)
        return await self._repository.first_present_at(wanted)

    async def _container_confirmations(
        self, versions: Sequence[ObjectVersion]
    ) -> dict[tuple[ObservationKind, str, dt.datetime], dt.datetime]:
        """The newest reading of each first-version's container before it appeared.

        Asked only for versions with no predecessor, because that is the only case where a
        change would otherwise carry no window: an entry that was *added* has no earlier
        version of its own, and the ACL it joined does.
        """
        return await self._repository.container_confirmed_before(
            [
                (version.kind, version.container_key, version.valid_from)
                for version in versions
                if version.container_key
            ]
        )

    @staticmethod
    def _container_confirmed(
        version: ObjectVersion,
        confirmations: dict[tuple[ObservationKind, str, dt.datetime], dt.datetime],
    ) -> dt.datetime | None:
        if not version.container_key:
            return None
        return confirmations.get((version.kind, version.container_key, version.valid_from))

    @staticmethod
    def _container_observed(
        version: ObjectVersion, coverage: dict[tuple[ObservationKind, str], dt.datetime]
    ) -> bool | None:
        """Whether this object's container was in the record before the object appeared.

        ``None`` — unanswerable — for a kind with no container and for a container nothing
        has ever described. Strictly before, not at: a container and its contents first read
        by the same scan were first seen together, and calling the contents an addition to a
        container of the same age would make every object of an estate's first scan a
        creation.
        """
        parent = container_kind_of(version.kind)
        if parent is None or not version.container_key:
            return None
        first = coverage.get((parent, version.container_key))
        if first is None:
            return None
        return first < version.valid_from


def _sibling_ace_changes(version: ObjectVersion, page: Sequence[ObjectVersion]) -> int | None:
    """How many NTFS ACEs of this resource also changed in the same page.

    Only meaningful for an ``ntfs_resource``; ``None`` for everything else, and ``None``
    rather than zero, so the ``resource.acl_hash.unexplained`` rule cannot fire on a kind it
    was not written for.

    Counted over the **page**, not the window, and that is a real limitation: a resource
    whose ACE changes landed on the previous page will show zero here and can be reported as
    an unexplained digest change. It is recorded in the handoff rather than papered over,
    because the alternative — a second windowed query per resource — costs more than the
    finding is worth at this stage.
    """
    if version.kind is not ObservationKind.NTFS_RESOURCE:
        return None
    return sum(
        1
        for other in page
        if other.kind is ObservationKind.NTFS_ACE and other.container_key == version.key
    )
