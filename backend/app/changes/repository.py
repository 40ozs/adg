r"""Reading transitions out of ``object_versions``: one window, three queries.

Phase 7A's :class:`app.history.repository.VersionReader` answers about one instant or one
object. This answers about an *interval across the estate*, which is a different query with
a different index behind it, and the Phase 7A handoff said so: ``ix_object_versions_closed_at``
is keyed on ``valid_to`` alone and would serve a whole-estate scan rather than a scoped one.
Revision ``0008`` adds ``ix_object_versions_opened_at``, and this module is what reads it.

## Every change is a version opening

The whole feed rests on one property of the writer, and it is worth stating plainly because
it makes the query trivial:

    a creation opens a version; a modification opens a version; **a removal opens a
    version too**, because a tombstone is a version.

So "everything that changed between Tuesday and Friday" is exactly ``valid_from`` inside
that interval. There is no second query for deletions, no union, and no risk of the two
halves disagreeing about what a change is.

## Three queries, and why the second one is two

1. **The page.** Versions opening in the window, newest first, keyset-paged on
   ``(valid_from, id)`` — the index's own order, so paging never re-reads.
2. **The predecessors.** A version alone says what the state *is*; a change needs what it
   *was*. The writer closes a version at the same instant it opens the next, so a
   predecessor is found by exact equality — ``valid_to = the successor's valid_from`` — and
   that is the fast path. The slow path exists anyway, for the page's first change of each
   object, and it is what makes correctness independent of that guarantee: if the writer
   ever stops closing adjacently, the exact match simply stops matching and the ordered
   lookup finds the same predecessor one query later.
3. **The containers.** Only for versions with no predecessor at all, and only to answer one
   question: was the thing containing this object already in the record before it appeared?
   That is what separates a creation from the start of observation
   (:func:`app.changes.classify.action_for`), and it cannot be answered from the object.

## What is deliberately not here

No classification, no correlation, no rendering. This module returns versions and the
relations between them; :mod:`app.changes.service` turns those into changes. Keeping the
split means the classification rules can be tested without PostgreSQL and the SQL can be
tested without the rules.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import (
    Select,
    Text,
    and_,
    any_,
    bindparam,
    func,
    literal,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.changes.scope import ChangeScope, predicates_for
from app.contracts.v1.common import ObservationKind
from app.history.model import ObjectVersion
from app.history.repository import VERSION_COLUMNS, version_from_row
from app.models.schema import object_versions

__all__ = [
    "MAX_PAGE",
    "ChangeRepository",
    "VersionCursor",
    "WindowedVersions",
]

MAX_PAGE: Final = 500
"""Versions read for one page. Matches :data:`app.api.pagination.MAX_LIMIT`, because a page
of changes is a page of a list endpoint and two different ceilings for the same thing is one
ceiling somebody will trip over."""


@dataclass(frozen=True, slots=True)
class VersionCursor:
    """A position in the feed's ordering: ``(valid_from, id)``, newest first.

    Two columns, not one. ``valid_from`` alone is not unique — one scan opens thousands of
    versions at one instant — so a cursor carrying only the timestamp would either skip
    every other version at that instant or return them all again on the next page. The row
    id breaks the tie and is the index's own second column.
    """

    valid_from: dt.datetime
    version_id: int

    def encode(self) -> str:
        return f"{self.valid_from.isoformat()}|{self.version_id}"

    @classmethod
    def decode(cls, raw: str) -> VersionCursor | None:
        """``None`` for anything malformed, so a caller decides what a bad cursor means."""
        moment, separator, identifier = raw.rpartition("|")
        if not separator or not identifier.isdigit():
            return None
        try:
            parsed = dt.datetime.fromisoformat(moment)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return cls(valid_from=parsed, version_id=int(identifier))


@dataclass(frozen=True, slots=True)
class WindowedVersions:
    """One page of versions that opened inside a window."""

    versions: tuple[ObjectVersion, ...]
    has_more: bool
    next_cursor: VersionCursor | None


def _identity(version: ObjectVersion) -> tuple[ObservationKind, str]:
    return version.kind, version.key


class ChangeRepository:
    """Windowed reads over ``object_versions``, for one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ the page

    async def opened_between(
        self,
        window_from: dt.datetime,
        window_to: dt.datetime,
        *,
        kinds: frozenset[ObservationKind] | None = None,
        scope: ChangeScope | None = None,
        cursor: VersionCursor | None = None,
        limit: int = 100,
    ) -> WindowedVersions:
        """Versions that opened in ``[window_from, window_to)``, newest first.

        Half-open at the top for the same reason a version's interval is: two adjacent
        windows must partition the timeline, and a closed upper bound would report the
        change at the boundary in both of them.
        """
        bounded = max(1, min(limit, MAX_PAGE))
        statement = self._window(window_from, window_to, kinds=kinds, scope=scope)
        if cursor is not None:
            statement = statement.where(
                tuple_(object_versions.c.valid_from, object_versions.c.id)
                < tuple_(literal(cursor.valid_from), literal(cursor.version_id))
            )
        statement = statement.order_by(
            object_versions.c.valid_from.desc(), object_versions.c.id.desc()
        ).limit(bounded + 1)
        rows = (await self._session.execute(statement)).mappings().all()
        has_more = len(rows) > bounded
        kept = tuple(version_from_row(row) for row in rows[:bounded])
        following = (
            VersionCursor(
                valid_from=kept[-1].valid_from,
                version_id=kept[-1].version_id or 0,
            )
            if has_more and kept
            else None
        )
        return WindowedVersions(versions=kept, has_more=has_more, next_cursor=following)

    async def count_between(
        self,
        window_from: dt.datetime,
        window_to: dt.datetime,
        *,
        kinds: frozenset[ObservationKind] | None = None,
        scope: ChangeScope | None = None,
        ceiling: int,
    ) -> tuple[int, bool]:
        """How many versions opened in the window, and whether the count hit its ceiling.

        Bounded rather than exact. An estate's first scan opens one version per object, and
        a page header that costs a count over millions of rows is a page header that times
        out. The ceiling is applied in SQL — a subquery with ``LIMIT`` — so the database
        stops reading at it rather than counting everything and being told to stop.
        """
        inner = (
            self._window(window_from, window_to, kinds=kinds, scope=scope)
            .with_only_columns(object_versions.c.id)
            .limit(ceiling + 1)
            .subquery()
        )
        total = int(
            (await self._session.execute(select(func.count()).select_from(inner))).scalar_one()
        )
        return min(total, ceiling), total > ceiling

    # ---------------------------------------------------------------- predecessors

    async def predecessors(
        self, versions: Sequence[ObjectVersion]
    ) -> dict[tuple[ObservationKind, str, dt.datetime], ObjectVersion]:
        """The version that held immediately before each of ``versions``.

        Keyed by ``(kind, key, valid_from)`` — the successor's identity — because one object
        can appear twice in a page and the two occurrences have different predecessors.

        Two queries, and the second one usually returns nothing: see the module docstring.
        """
        if not versions:
            return {}
        found = await self._adjacent(versions)
        outstanding = [
            version
            for version in versions
            if (version.kind, version.key, version.valid_from) not in found
        ]
        if outstanding:
            found.update(await self._latest_before(outstanding))
        return found

    async def _adjacent(
        self, versions: Sequence[ObjectVersion]
    ) -> dict[tuple[ObservationKind, str, dt.datetime], ObjectVersion]:
        """Predecessors found by exact adjacency: ``valid_to`` equals the next start."""
        keys = sorted({version.key for version in versions})
        kinds = sorted({version.kind.value for version in versions})
        moments = sorted({version.valid_from for version in versions})
        statement = select(*VERSION_COLUMNS).where(
            object_versions.c.object_kind == any_(bindparam("kinds", kinds, type_=ARRAY(Text))),
            object_versions.c.object_key == any_(bindparam("keys", keys, type_=ARRAY(Text))),
            object_versions.c.valid_to.in_(moments),
        )
        matched: dict[tuple[ObservationKind, str, dt.datetime], ObjectVersion] = {}
        wanted = {(version.kind, version.key, version.valid_from) for version in versions}
        for row in (await self._session.execute(statement)).mappings():
            candidate = version_from_row(row)
            assert candidate.valid_to is not None
            identity = (candidate.kind, candidate.key, candidate.valid_to)
            if identity in wanted:
                matched[identity] = candidate
        return matched

    async def _latest_before(
        self, versions: Sequence[ObjectVersion]
    ) -> dict[tuple[ObservationKind, str, dt.datetime], ObjectVersion]:
        """The newest version of each object strictly older than the one asked about.

        ``DISTINCT ON`` with a per-object bound, so one query answers for every outstanding
        version at once and each answer respects that version's own instant rather than a
        shared one. Reached only for a page's first change of an object, which in a sound
        record means the object has no earlier version at all — and returning nothing is
        then the correct answer, arrived at by looking rather than by assuming.
        """
        bounds = or_(
            *[
                and_(
                    object_versions.c.object_kind == version.kind.value,
                    object_versions.c.object_key == version.key,
                    object_versions.c.valid_from < version.valid_from,
                )
                for version in versions
            ]
        )
        statement = (
            select(*VERSION_COLUMNS)
            .where(bounds)
            .distinct(object_versions.c.object_kind, object_versions.c.object_key)
            .order_by(
                object_versions.c.object_kind,
                object_versions.c.object_key,
                object_versions.c.valid_from.desc(),
            )
        )
        newest: dict[tuple[ObservationKind, str], ObjectVersion] = {}
        for row in (await self._session.execute(statement)).mappings():
            found = version_from_row(row)
            newest[_identity(found)] = found
        matched: dict[tuple[ObservationKind, str, dt.datetime], ObjectVersion] = {}
        for version in versions:
            previous = newest.get(_identity(version))
            if previous is not None and previous.valid_from < version.valid_from:
                matched[(version.kind, version.key, version.valid_from)] = previous
        return matched

    # ------------------------------------------------------------------ containers

    async def first_present_at(
        self, wanted: Mapping[ObservationKind, Sequence[str]]
    ) -> dict[tuple[ObservationKind, str], dt.datetime]:
        """When each named object was first observed to *exist*, by kind.

        ``is_present`` is part of the predicate on purpose. A container whose only earlier
        version is a tombstone was not being watched in any useful sense — a reconciled scan
        had found it gone — so an object appearing inside it is the first reading of a
        container that has just come back, not an addition to one under observation.
        """
        if not wanted:
            return {}
        branches = [
            and_(
                object_versions.c.object_kind == kind.value,
                object_versions.c.object_key
                == any_(bindparam(f"keys_{index}", sorted(set(keys)), type_=ARRAY(Text))),
            )
            for index, (kind, keys) in enumerate(wanted.items())
            if keys
        ]
        if not branches:
            return {}
        statement = (
            select(
                object_versions.c.object_kind,
                object_versions.c.object_key,
                object_versions.c.valid_from,
            )
            .where(or_(*branches), object_versions.c.is_present)
            .distinct(object_versions.c.object_kind, object_versions.c.object_key)
            .order_by(
                object_versions.c.object_kind,
                object_versions.c.object_key,
                object_versions.c.valid_from.asc(),
            )
        )
        return {
            (ObservationKind(row["object_kind"]), row["object_key"]): row["valid_from"]
            for row in (await self._session.execute(statement)).mappings()
        }

    async def container_confirmed_before(
        self, wanted: Sequence[tuple[ObservationKind, str, dt.datetime]]
    ) -> dict[tuple[ObservationKind, str, dt.datetime], dt.datetime]:
        """The newest confirmation of a sibling inside each container, before each instant.

        What bounds the *start* of an object that has no predecessor of its own. An ACE's
        rights are part of its identity, so an entry that was added is a new object with no
        earlier version — but the ACL it joined was read at a known instant, and the entry
        therefore appeared after it. Without this, every ACL addition would report no change
        window at all and leave a client with only the instant somebody looked, which is the
        rendering ADR-0019 exists to prevent.

        Batched by ``(kind, instant)``: one query per distinct instant, which in practice is
        one per scan, because a scan confirms everything it read at the same moment.
        """
        if not wanted:
            return {}
        answers: dict[tuple[ObservationKind, str, dt.datetime], dt.datetime] = {}
        grouped: dict[tuple[ObservationKind, dt.datetime], list[str]] = {}
        for kind, container, moment in wanted:
            grouped.setdefault((kind, moment), []).append(container)
        for (kind, moment), containers in grouped.items():
            statement = (
                select(
                    object_versions.c.container_key,
                    func.max(object_versions.c.last_seen_at).label("confirmed"),
                )
                .where(
                    object_versions.c.object_kind == kind.value,
                    object_versions.c.container_key
                    == any_(bindparam("containers", sorted(set(containers)), type_=ARRAY(Text))),
                    object_versions.c.last_seen_at < moment,
                )
                .group_by(object_versions.c.container_key)
            )
            for row in (await self._session.execute(statement)).mappings():
                answers[(kind, row["container_key"], moment)] = row["confirmed"]
        return answers

    # ----------------------------------------------------------- point-in-time sets

    async def present_at(
        self,
        moment: dt.datetime,
        *,
        kinds: frozenset[ObservationKind] | None = None,
        scope: ChangeScope | None = None,
        limit: int,
    ) -> tuple[tuple[ObjectVersion, ...], bool]:
        """Every version covering ``moment`` inside a scope, tombstones included.

        The set a point-in-time *comparison* is built from. Tombstones are kept rather than
        filtered, because a tombstone at one end and a state at the other is a real finding
        — "a reconciled scan proved this was gone, and now it is back" — while an object
        with no version at all at that instant is a gap in observation and is counted
        separately by the caller. Filtering here would merge the two.
        """
        statement = select(*VERSION_COLUMNS).where(
            object_versions.c.valid_from <= moment,
            or_(object_versions.c.valid_to.is_(None), object_versions.c.valid_to > moment),
        )
        statement = _restrict(statement, kinds=kinds, scope=scope)
        statement = statement.order_by(
            object_versions.c.object_kind, object_versions.c.object_key
        ).limit(limit + 1)
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(version_from_row(row) for row in rows[:limit]), len(rows) > limit

    # ------------------------------------------------------------------- internals

    @staticmethod
    def _window(
        window_from: dt.datetime,
        window_to: dt.datetime,
        *,
        kinds: frozenset[ObservationKind] | None,
        scope: ChangeScope | None,
    ) -> Select[Any]:
        statement = select(*VERSION_COLUMNS).where(
            object_versions.c.valid_from >= window_from,
            object_versions.c.valid_from < window_to,
        )
        return _restrict(statement, kinds=kinds, scope=scope)


def _restrict(
    statement: Select[Any],
    *,
    kinds: frozenset[ObservationKind] | None,
    scope: ChangeScope | None,
) -> Select[Any]:
    """Apply the kind filter and the scope, in that order.

    The scope is applied as one ``OR`` of ``(kind = ... AND predicate)`` branches, so a kind
    the scope has no rule for contributes no branch and is therefore excluded — exclusion by
    construction rather than by a second subtraction somebody has to remember to make.
    """
    if kinds is not None:
        statement = statement.where(
            object_versions.c.object_kind.in_(sorted(kind.value for kind in kinds))
        )
    if scope is not None:
        branches = [
            and_(object_versions.c.object_kind == kind.value, predicate)
            for kind, predicate in predicates_for(scope).items()
            if kinds is None or kind in kinds
        ]
        statement = statement.where(or_(*branches) if branches else _NOTHING)
    return statement


#: A predicate that selects nothing, for a scope that excludes every requested kind.
#:
#: Written as a literal false rather than by returning an empty result, so the caller still
#: executes a query and still gets an empty page with a valid cursor — rather than a special
#: case that has to be handled identically everywhere the query is used.
_NOTHING = object_versions.c.id.is_(None)
