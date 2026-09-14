r"""Reading ``object_versions``: one instant, or a whole timeline.

Three layers, deliberately separate.

:class:`VersionReader` is the only code that knows the temporal predicate. "The version that
answers for *T*" is ``valid_from <= T < valid_to``, half-open, because the instant one
version closes is the instant the next one opens and a closed interval at both ends would
make two versions answer for the same moment.

:class:`HistoricalMembershipRepository` and :class:`HistoricalResourceRepository` are the
current-state repositories with their reads redirected through that predicate. They are
**subclasses**, and that is the point of the whole design: :class:`app.services.AccessService`
takes both repositories by injection, so handing it these two makes the entire effective-access
engine -- the token build, the DACL projection for an unread path, the coverage findings, the
explanation -- answer as of a past instant with not one line of the engine changed. A separate
historical engine would be a second implementation of the most safety-critical code in the
product, and the first time the two disagreed, nobody would know which was right.

The records they return are built by the **same constructors** the live repositories use
(:func:`app.repositories.membership.principal_record` and friends), from a version's stored
state plus that version's own provenance: ``first_observed_at`` is the version's
``valid_from``, ``last_observed_at`` is its ``last_seen_at``. So a record read as of *T*
carries the window *that state* was observed over, not the window the object has existed
over, which is what a caller asking about *T* means.

**Tombstones are not rows.** A point-in-time read returns nothing for an object that a
reconciled scan found to be gone, exactly as it returns nothing for one nobody had collected
yet -- because what a repository is asked for is the object, and there was none. The
difference between the two is a real and important one, and it is answered by
:meth:`VersionReader.presence_at`, which reports it explicitly, rather than smuggled into a
``None`` that two different callers would read two different ways.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import RowMapping, Select, Text, any_, bindparam, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain import Direction, GraphEdge
from app.history.model import (
    Certainty,
    ObjectTimeline,
    ObjectVersion,
)
from app.models.schema import CloseReason, VersionOrigin, object_versions
from app.repositories.membership import (
    MembershipRepository,
    PrincipalRecord,
    graph_edge,
    principal_record,
)
from app.repositories.resources import (
    MAX_ACL_FETCH,
    NtfsAceRecord,
    NtfsResourceRecord,
    ResourceRepository,
    ShareAceRecord,
    ShareRecord,
    ntfs_ace_record,
    ntfs_resource_record,
    share_ace_record,
    share_record,
)

__all__ = [
    "HistoricalMembershipRepository",
    "HistoricalResourceRepository",
    "Presence",
    "VersionAudit",
    "VersionReader",
]

KEY_CHUNK: Final = 5_000

MAX_TIMELINE_VERSIONS: Final = 500
"""Versions returned for one object before a timeline reports itself truncated. An object
whose state changes five hundred times is a finding in its own right, and paging a timeline
that long in a single answer helps nobody."""

_COLUMNS: Final = (
    object_versions.c.id,
    object_versions.c.object_kind,
    object_versions.c.object_key,
    object_versions.c.container_key,
    object_versions.c.related_key,
    object_versions.c.is_present,
    object_versions.c.state,
    object_versions.c.state_hash,
    object_versions.c.origin,
    object_versions.c.valid_from,
    object_versions.c.last_seen_at,
    object_versions.c.valid_to,
    object_versions.c.close_reason,
    object_versions.c.opened_by_run_id,
    object_versions.c.last_seen_run_id,
    object_versions.c.closed_by_run_id,
)


@dataclass(frozen=True, slots=True)
class Presence:
    """Whether an object existed at an instant, and how firmly that is known.

    The three answers are genuinely three. ``present`` and ``absent`` are both findings --
    somebody looked. ``unobserved`` is the absence of a finding, and an audit tool that
    rendered it as "absent" would report access as revoked on the strength of nobody having
    looked, which is the single failure this product exists to avoid.
    """

    kind: ObservationKind
    key: str
    at: dt.datetime
    exists: bool | None
    """``True`` present, ``False`` measured absent, ``None`` nothing covers the instant."""

    certainty: Certainty
    version: ObjectVersion | None

    @property
    def is_unobserved(self) -> bool:
        return self.exists is None

    @property
    def absent_since(self) -> dt.datetime | None:
        """When the absence was recorded, for an answer that is a tombstone."""
        if self.version is None or self.version.is_present:
            return None
        return self.version.valid_from


@dataclass
class VersionAudit:
    """A tally of how firmly grounded the versions one answer was built from were.

    A point-in-time effective-access answer is computed by the ordinary engine over
    historical inputs, and the engine has no idea some of those inputs were reconstructed.
    Somebody has to carry that, and the honest place is the read itself: every version that
    leaves :class:`VersionReader` is counted by the certainty it has *at the instant asked
    about*, so an answer can report that it rests on -- for instance -- forty observed facts
    and one backfilled one, without the engine knowing anything about time.
    """

    counts: dict[Certainty, int] = field(default_factory=dict)

    def record(self, version: ObjectVersion, at: dt.datetime) -> None:
        certainty = version.certainty_at(at)
        self.counts[certainty] = self.counts.get(certainty, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def weakest(self) -> Certainty:
        """The least firm certainty any input carried, which is what an answer inherits.

        An answer is exactly as sound as its weakest input: one backfilled ACL in an
        otherwise fully observed resolution means the resolution could have been different
        and nobody would know. Ordered deliberately, not alphabetically.
        """
        for candidate in (
            Certainty.UNOBSERVED,
            Certainty.BACKFILLED,
            Certainty.INFERRED,
            Certainty.OBSERVED,
        ):
            if self.counts.get(candidate):
                return candidate
        return Certainty.UNOBSERVED


class VersionReader:
    """Point-in-time and timeline reads over ``object_versions``."""

    def __init__(self, session: AsyncSession, audit: VersionAudit | None = None) -> None:
        self._session = session
        self._audit = audit

    def _seen(self, version: ObjectVersion, at: dt.datetime) -> ObjectVersion:
        if self._audit is not None:
            self._audit.record(version, at)
        return version

    async def version_at(
        self, kind: ObservationKind, key: str, at: dt.datetime
    ) -> ObjectVersion | None:
        """The version covering ``at``, tombstone included, or ``None`` if none does."""
        rows = (
            (
                await self._session.execute(
                    self._at(kind, at).where(object_versions.c.object_key == key)
                )
            )
            .mappings()
            .all()
        )
        return self._seen(_version(rows[0]), at) if rows else None

    async def presence_at(self, kind: ObservationKind, key: str, at: dt.datetime) -> Presence:
        """Whether one object existed at an instant, as one of three answers."""
        version = await self.version_at(kind, key, at)
        if version is None:
            return Presence(
                kind=kind, key=key, at=at, exists=None, certainty=Certainty.UNOBSERVED, version=None
            )
        return Presence(
            kind=kind,
            key=key,
            at=at,
            exists=version.is_present,
            certainty=version.certainty_at(at),
            version=version,
        )

    async def versions_at(
        self,
        kind: ObservationKind,
        keys: Sequence[str],
        at: dt.datetime,
        *,
        present_only: bool = True,
    ) -> dict[str, ObjectVersion]:
        """The versions of many objects covering one instant, keyed by object key."""
        found: dict[str, ObjectVersion] = {}
        unique = list(dict.fromkeys(keys))
        for start in range(0, len(unique), KEY_CHUNK):
            chunk = unique[start : start + KEY_CHUNK]
            statement = self._at(kind, at).where(
                object_versions.c.object_key == any_(bindparam("keys", chunk, type_=ARRAY(Text)))
            )
            if present_only:
                statement = statement.where(object_versions.c.is_present)
            for row in (await self._session.execute(statement)).mappings():
                found[row["object_key"]] = self._seen(_version(row), at)
        return found

    async def contained_at(
        self,
        kind: ObservationKind,
        container_keys: Sequence[str],
        at: dt.datetime,
        *,
        present_only: bool = True,
        limit: int | None = None,
    ) -> tuple[ObjectVersion, ...]:
        """Every version of ``kind`` inside one of ``container_keys`` at ``at``.

        This is the indexed read ``container_key`` exists for: one share's ACL, one
        directory's ACL, one group's direct members, one server's shares.
        """
        if not container_keys:
            return ()
        statement = self._at(kind, at).where(
            object_versions.c.container_key
            == any_(bindparam("containers", list(container_keys), type_=ARRAY(Text)))
        )
        if present_only:
            statement = statement.where(object_versions.c.is_present)
        statement = statement.order_by(object_versions.c.object_key)
        if limit is not None:
            statement = statement.limit(limit)
        return tuple(
            self._seen(_version(row), at)
            for row in (await self._session.execute(statement)).mappings()
        )

    async def related_at(
        self,
        kind: ObservationKind,
        related_keys: Sequence[str],
        at: dt.datetime,
        *,
        present_only: bool = True,
        limit: int | None = None,
    ) -> tuple[ObjectVersion, ...]:
        """Every version of ``kind`` whose far end is one of ``related_keys`` at ``at``."""
        if not related_keys:
            return ()
        statement = self._at(kind, at).where(
            object_versions.c.related_key
            == any_(bindparam("related", list(related_keys), type_=ARRAY(Text)))
        )
        if present_only:
            statement = statement.where(object_versions.c.is_present)
        statement = statement.order_by(object_versions.c.object_key)
        if limit is not None:
            statement = statement.limit(limit)
        return tuple(
            self._seen(_version(row), at)
            for row in (await self._session.execute(statement)).mappings()
        )

    async def timeline(
        self, kind: ObservationKind, key: str, *, limit: int = MAX_TIMELINE_VERSIONS
    ) -> ObjectTimeline:
        """Every version of one object, oldest first.

        Reads one more row than it returns, which is how the answer knows whether it is the
        whole timeline or a prefix of one -- and says so, rather than presenting a page as a
        history.
        """
        statement = (
            select(*_COLUMNS)
            .where(
                object_versions.c.object_kind == kind.value,
                object_versions.c.object_key == key,
            )
            .order_by(object_versions.c.valid_from, object_versions.c.id)
            .limit(limit + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        truncated = len(rows) > limit
        return ObjectTimeline(
            kind=kind,
            key=key,
            versions=tuple(_version(row) for row in rows[:limit]),
            truncated=truncated,
        )

    async def absent_now(
        self, kind: ObservationKind, *, limit: int = 100
    ) -> tuple[ObjectVersion, ...]:
        """Objects a reconciled scan currently reports as gone.

        The current-state tables still hold a row for every one of these -- nothing in ADG
        deletes a collected fact -- so this is the only way to ask "what has been removed".
        """
        statement = (
            select(*_COLUMNS)
            .where(
                object_versions.c.object_kind == kind.value,
                object_versions.c.valid_to.is_(None),
                object_versions.c.is_present.is_(False),
            )
            .order_by(object_versions.c.valid_from.desc(), object_versions.c.object_key)
            .limit(limit)
        )
        return tuple(_version(row) for row in (await self._session.execute(statement)).mappings())

    @staticmethod
    def _at(kind: ObservationKind, at: dt.datetime) -> Select[Any]:
        """``[valid_from, valid_to)`` — half-open, so exactly one version answers."""
        return select(*_COLUMNS).where(
            object_versions.c.object_kind == kind.value,
            object_versions.c.valid_from <= at,
            or_(object_versions.c.valid_to.is_(None), object_versions.c.valid_to > at),
        )


def _version(row: RowMapping) -> ObjectVersion:
    return ObjectVersion(
        kind=ObservationKind(row["object_kind"]),
        key=row["object_key"],
        is_present=bool(row["is_present"]),
        valid_from=row["valid_from"],
        last_seen_at=row["last_seen_at"],
        valid_to=row["valid_to"],
        state=row["state"],
        state_hash=row["state_hash"],
        origin=VersionOrigin(row["origin"]),
        close_reason=None if row["close_reason"] is None else CloseReason(row["close_reason"]),
        opened_by_run_id=row["opened_by_run_id"],
        last_seen_run_id=row["last_seen_run_id"],
        closed_by_run_id=row["closed_by_run_id"],
        container_key=row["container_key"],
        related_key=row["related_key"],
        version_id=int(row["id"]),
    )


def as_row(version: ObjectVersion) -> dict[str, Any]:
    """A version's state, shaped like the current-state row it was projected from.

    The provenance a record carries is the **version's** provenance, not the object's: an
    answer about Tuesday must report the window the state it is describing was observed
    over. Reporting the object's whole lifetime there would tell a reader that a value seen
    once in January was still being confirmed in December.
    """
    if version.state is None:
        raise ValueError(
            f"A tombstone of {version.key} has no state and cannot be rebuilt as a record. "
            "Callers must check presence before reconstructing."
        )
    return {
        **version.state,
        "first_observed_at": version.valid_from,
        "first_observed_run_id": version.opened_by_run_id,
        "last_observed_at": version.last_seen_at,
        "last_observed_run_id": version.last_seen_run_id,
    }


class HistoricalMembershipRepository(MembershipRepository):
    """The membership repository, reading as of one instant.

    Only the methods an access resolution and a graph traversal use are overridden. The rest
    are inherited, and an inherited read returns **current** state -- so this class is safe
    exactly through the paths that use the overridden set, and nowhere else.

    That is a real hazard and it is guarded by a test rather than by a convention:
    ``tests/history/test_repository_coverage.py`` walks both base classes and asserts that
    every public read is either overridden here or named in an explicit list of reads the
    historical form deliberately does not provide. A method added to a base repository
    therefore fails the suite until somebody decides which of the two it is.
    """

    def __init__(
        self,
        session: AsyncSession,
        at: dt.datetime,
        audit: VersionAudit | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(session, **kwargs)
        self._at_instant = at
        self._versions = VersionReader(session, audit)

    @property
    def at(self) -> dt.datetime:
        return self._at_instant

    async def neighbors(
        self, direction: Direction, keys: Sequence[str]
    ) -> Mapping[str, Sequence[GraphEdge]]:
        """The edges leading out of ``keys`` as of the instant.

        ``DOWN`` reads the edges whose ``container_key`` is the group; ``UP`` reads those
        whose ``related_key`` is the member. Those are the two indexed columns
        ``object_versions`` carries for exactly this traversal, so a historical expansion
        costs one statement per level, as the live one does.
        """
        if not keys:
            return {}
        unique = list(dict.fromkeys(keys))
        result: dict[str, list[GraphEdge]] = {key: [] for key in unique}
        remaining = self._edge_fetch_limit - self._edges_fetched
        if remaining <= 0:
            return result
        reader = (
            self._versions.contained_at
            if direction is Direction.DOWN
            else self._versions.related_at
        )
        versions = await reader(
            ObservationKind.MEMBERSHIP_EDGE, unique, self._at_instant, limit=remaining
        )
        self._edges_fetched += len(versions)
        for version in versions:
            edge = graph_edge(as_row(version))
            result[edge.origin(direction)].append(edge)
        return result

    async def get_principal(self, principal_key: str) -> PrincipalRecord | None:
        version = await self._versions.version_at(
            ObservationKind.PRINCIPAL, principal_key, self._at_instant
        )
        if version is None or not version.is_present:
            return None
        return principal_record(as_row(version))

    async def principals_by_keys(self, keys: Sequence[str]) -> dict[str, PrincipalRecord]:
        versions = await self._versions.versions_at(
            ObservationKind.PRINCIPAL, keys, self._at_instant
        )
        return {key: principal_record(as_row(version)) for key, version in versions.items()}

    async def keys_with_members(self, keys: Sequence[str]) -> frozenset[str]:
        """Which of these keys had at least one membership edge into them at the instant.

        The live form groups ``membership_edges`` by ``group_key``; the historical form does
        the same over the edge versions that covered the instant. The distinction it draws
        is the one an access answer turns on -- "nobody has looked inside this group" versus
        "this group is empty" -- and it has to be drawn against the same instant as the rest
        of the answer, or a resolution would report a group as unexamined because it is
        empty *today*.
        """
        unique = sorted(set(keys))
        if not unique:
            return frozenset()
        versions = await self._versions.contained_at(
            ObservationKind.MEMBERSHIP_EDGE, unique, self._at_instant
        )
        return frozenset(
            version.container_key for version in versions if version.container_key is not None
        )


class HistoricalResourceRepository(ResourceRepository):
    """The resource repository, reading as of one instant. See the sibling class."""

    def __init__(
        self, session: AsyncSession, at: dt.datetime, audit: VersionAudit | None = None
    ) -> None:
        super().__init__(session)
        self._at_instant = at
        self._versions = VersionReader(session, audit)

    @property
    def at(self) -> dt.datetime:
        return self._at_instant

    async def get_share(self, share_key: str) -> ShareRecord | None:
        version = await self._versions.version_at(
            ObservationKind.SMB_SHARE, share_key.casefold(), self._at_instant
        )
        if version is None or not version.is_present:
            return None
        return share_record(as_row(version))

    async def get_ntfs_resource(self, resource_key: str) -> NtfsResourceRecord | None:
        version = await self._versions.version_at(
            ObservationKind.NTFS_RESOURCE, resource_key.casefold(), self._at_instant
        )
        if version is None or not version.is_present:
            return None
        return ntfs_resource_record(as_row(version))

    async def ntfs_resources_by_keys(self, keys: Sequence[str]) -> dict[str, NtfsResourceRecord]:
        versions = await self._versions.versions_at(
            ObservationKind.NTFS_RESOURCE, [key.casefold() for key in keys], self._at_instant
        )
        return {key: ntfs_resource_record(as_row(version)) for key, version in versions.items()}

    async def full_ntfs_acl(
        self, resource_key: str, *, limit: int = MAX_ACL_FETCH
    ) -> tuple[NtfsAceRecord, ...]:
        versions = await self._versions.contained_at(
            ObservationKind.NTFS_ACE, [resource_key.casefold()], self._at_instant, limit=limit
        )
        return _in_evaluation_order(ntfs_ace_record(as_row(version)) for version in versions)

    async def full_share_acl(
        self, share_key: str, *, limit: int = MAX_ACL_FETCH
    ) -> tuple[ShareAceRecord, ...]:
        versions = await self._versions.contained_at(
            ObservationKind.SMB_ACE, [share_key.casefold()], self._at_instant, limit=limit
        )
        return _in_evaluation_order(share_ace_record(as_row(version)) for version in versions)


def _in_evaluation_order(records: Any) -> tuple[Any, ...]:
    """ACL entries ordered as they were read, deny-before-allow position preserved.

    ``order_index`` is the position in the DACL as the collector read it, and it is what
    makes a Deny evaluable. A historical read comes back ordered by ACE key, which is
    alphabetical and meaningless, so it is re-sorted here. Entries with no recorded position
    sort last and keep their relative order, exactly as the live repository's ``NULLS LAST``
    does.
    """
    listed = list(records)
    return tuple(
        sorted(
            listed,
            key=lambda record: (record.order_index is None, record.order_index or 0),
        )
    )
