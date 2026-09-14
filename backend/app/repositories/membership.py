"""Reading principals and membership edges out of PostgreSQL.

This is the only place in the query path that knows SQL exists. It implements
:class:`app.domain.AdjacencyProvider`, so the traversal in :mod:`app.domain.graph` sees
nothing but "give me the edges touching these keys" — which is why the graph algorithms are
testable against a dictionary and why the number of database round trips is a property of
the traversal (one per level) rather than of the data (one per node).

Two bounds live here rather than in the caller:

* **Key chunking.** A breadth-first frontier can be tens of thousands of nodes wide. Keys
  are sent as a single array parameter per chunk, not as an expanded ``IN`` list, so one
  wide level stays one query rather than one 50,000-placeholder statement.
* **A row ceiling.** ``edge_fetch_limit`` caps how many edge rows one traversal may pull.
  Without it a single group with a million members would materialize a million rows before
  the traversal got the chance to say "truncated". Callers set it to the traversal's
  ``max_edges`` **plus one**: a traversal can only declare itself truncated when it is
  offered the edge that exceeds its budget, so the repository has to be willing to read one
  row more than the traversal will keep. See :data:`EDGE_FETCH_CEILING`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Generic, TypeVar
from uuid import UUID

from sqlalchemy import RowMapping, Select, Text, any_, bindparam, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    MAX_EDGES_CEILING,
    Direction,
    GraphEdge,
    MembershipEdgeKind,
    PrincipalKind,
    Sid,
)
from app.models.schema import membership_edges, principal_aliases, principals

__all__ = [
    "EDGE_FETCH_CEILING",
    "AliasRecord",
    "DirectEdgeRecord",
    "MembershipRepository",
    "Page",
    "PrincipalRecord",
    "PrincipalResolution",
]

KEY_CHUNK: Final = 5_000
"""Keys per adjacency query. One array parameter, not one placeholder per key."""

EDGE_FETCH_CEILING: Final = MAX_EDGES_CEILING + 1
"""One row above the largest edge budget a traversal can hold.

Callers pair the repository with a traversal by passing ``max_edges + 1``, because a
traversal can only report ``max_edges`` truncation when it is *offered* the edge that
exceeds its budget. Clamping that pairing back to ``MAX_EDGES_CEILING`` would take the
probe row away at exactly the largest budget, and the traversal would then report a
repository-truncated answer as complete. The extra row is never kept — it exists only so
the cut-off can be seen.
"""

MAX_PAGE_SIZE: Final = 500
DEFAULT_PAGE_SIZE: Final = 100

ItemT = TypeVar("ItemT")


@dataclass(frozen=True, slots=True)
class Page(Generic[ItemT]):
    """One page of results plus the cursor that continues it.

    ``next_key`` is the sort key of the last item, which is what keyset pagination resumes
    from. Keyset rather than offset because the member list of a large group changes while
    it is being paged, and an offset would silently skip or repeat rows across pages — in an
    audit tool, a skipped member is a missed finding.
    """

    items: tuple[ItemT, ...]
    has_more: bool
    next_key: str | None


@dataclass(frozen=True, slots=True)
class PrincipalRecord:
    """A stored principal, with the provenance that justifies it."""

    principal_key: str
    sid: str
    principal_kind: PrincipalKind
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
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID

    @property
    def is_group(self) -> bool:
        """Whether this principal can contain members.

        Only groups can, so only groups are worth expanding. A principal ADG has never
        described is *not* covered by this property — see
        :attr:`app.services.graph.ResolvedNode.is_group`, which answers ``None`` for an
        unknown kind rather than "not a group".
        """
        return self.principal_kind in (PrincipalKind.DOMAIN_GROUP, PrincipalKind.LOCAL_GROUP)


@dataclass(frozen=True, slots=True)
class PrincipalResolution:
    """The outcome of looking a principal up by SID or key.

    ``candidates`` is non-empty only when a bare SID matched more than one stored principal,
    which happens for BUILTIN SIDs: ``S-1-5-32-544`` names a different group on every
    computer. Returning the candidates rather than picking one is the whole point — guessing
    would attribute one server's local administrators to another's.
    """

    record: PrincipalRecord | None
    candidates: tuple[PrincipalRecord, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return self.record is None and len(self.candidates) > 1

    @property
    def found(self) -> bool:
        return self.record is not None


@dataclass(frozen=True, slots=True)
class AliasRecord:
    """One name ever observed for a principal, and the window it was seen in.

    A name that stopped being observed is not deleted: "this SID used to be called
    svc-backup" is often the most useful thing an investigator can be told.
    """

    alias_kind: str
    value: str
    first_observed_at: dt.datetime
    last_observed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class DirectEdgeRecord:
    """One direct membership, with the principal at the far end when it is known."""

    edge: GraphEdge
    counterpart_key: str
    counterpart: PrincipalRecord | None
    first_observed_at: dt.datetime
    last_observed_at: dt.datetime
    last_observed_run_id: UUID


_EDGE_COLUMNS = (
    membership_edges.c.edge_key,
    membership_edges.c.group_key,
    membership_edges.c.member_key,
    membership_edges.c.edge_kind,
    membership_edges.c.host_key,
    membership_edges.c.member_kind,
    membership_edges.c.is_foreign_security_principal,
)


class MembershipRepository:
    """Principal lookup, direct membership listing, and graph adjacency."""

    def __init__(self, session: AsyncSession, edge_fetch_limit: int = MAX_EDGES_CEILING) -> None:
        self._session = session
        self._edge_fetch_limit = max(1, min(edge_fetch_limit, EDGE_FETCH_CEILING))
        self._edges_fetched = 0

    @property
    def edge_fetch_limit(self) -> int:
        """Rows this repository will read before it stops. Exposed so the pairing is testable."""
        return self._edge_fetch_limit

    @property
    def edges_fetched(self) -> int:
        """Edge rows read so far. Exposed so a request can report its own cost."""
        return self._edges_fetched

    # -------------------------------------------------------- adjacency provider

    async def neighbors(
        self, direction: Direction, keys: Sequence[str]
    ) -> Mapping[str, Sequence[GraphEdge]]:
        """Every edge leading out of ``keys`` in ``direction``, in one query per chunk."""
        if not keys:
            return {}
        origin = (
            membership_edges.c.group_key
            if direction is Direction.DOWN
            else membership_edges.c.member_key
        )
        far = (
            membership_edges.c.member_key
            if direction is Direction.DOWN
            else membership_edges.c.group_key
        )

        result: dict[str, list[GraphEdge]] = {key: [] for key in keys}
        for chunk in _chunks(list(dict.fromkeys(keys)), KEY_CHUNK):
            remaining = self._edge_fetch_limit - self._edges_fetched
            if remaining <= 0:
                break
            statement = (
                select(*_EDGE_COLUMNS)
                .where(origin == any_(bindparam("keys", chunk, type_=ARRAY(Text))))
                # Deterministic order so that a truncated fetch truncates the same way
                # twice: an unstable prefix would make one bounded answer differ from the
                # next for no visible reason.
                .order_by(origin, far, membership_edges.c.edge_key)
                .limit(remaining)
            )
            rows = (await self._session.execute(statement)).mappings().all()
            self._edges_fetched += len(rows)
            for row in rows:
                edge = _graph_edge(row)
                result[edge.origin(direction)].append(edge)
        return result

    # ------------------------------------------------------------------ lookup

    async def get_principal(self, principal_key: str) -> PrincipalRecord | None:
        row = (
            (
                await self._session.execute(
                    select(principals).where(principals.c.principal_key == principal_key)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _principal_record(row)

    async def resolve(self, identifier: str, host_key: str | None = None) -> PrincipalResolution:
        """Find a principal by storage key or by bare SID.

        A host-scoped key (``fs01|S-1-5-32-544``) is exact. A bare SID is exact for anything
        a domain issued, and deliberately ambiguous for a BUILTIN SID observed on several
        computers: pass ``host_key`` to name which one.

        **``host_key`` is asked first**, when the identifier is a bare SID. The host-scoped
        key is what the caller asked for, and answering with an unscoped principal that
        merely shares the SID would make ``?host=`` silently mean nothing — in exactly the
        case it exists for. A domain that reports its own ``BUILTIN\\Administrators``
        occupies the bare ``S-1-5-32-544`` key, so without this the scoped lookup for FS01's
        local group would be answered with the domain's group instead.

        Failing that, ``host_key`` only widens the search: a domain user is a perfectly good
        answer to "this SID, on FS01", because a domain principal has no host and scoping it
        to one would fragment one user into one node per server.
        """
        sid = Sid.try_parse(identifier)

        if host_key is not None and sid is not None:
            scoped = await self.get_principal(f"{host_key.casefold()}|{sid.value}")
            if scoped is not None:
                return PrincipalResolution(record=scoped)

        exact = await self.get_principal(identifier)
        if exact is not None:
            return PrincipalResolution(record=exact)

        if sid is None:
            return PrincipalResolution(record=None)

        statement = select(principals).where(principals.c.sid == sid.value)
        if host_key is not None:
            # Nothing is scoped to this host, so only unscoped principals can answer. The
            # host-scoped lookup above already ruled the other case out.
            statement = statement.where(principals.c.host_key.is_(None))
        rows = (
            (await self._session.execute(statement.order_by(principals.c.principal_key)))
            .mappings()
            .all()
        )
        records = tuple(_principal_record(row) for row in rows)
        if len(records) == 1:
            return PrincipalResolution(record=records[0])
        return PrincipalResolution(record=None, candidates=records)

    async def principals_by_keys(self, keys: Sequence[str]) -> dict[str, PrincipalRecord]:
        """Load many principals at once, for labelling a traversal's results."""
        found: dict[str, PrincipalRecord] = {}
        for chunk in _chunks(list(dict.fromkeys(keys)), KEY_CHUNK):
            rows = (
                (
                    await self._session.execute(
                        select(principals).where(
                            principals.c.principal_key
                            == any_(bindparam("keys", chunk, type_=ARRAY(Text)))
                        )
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                record = _principal_record(row)
                found[record.principal_key] = record
        return found

    async def aliases_for(self, principal_key: str) -> tuple[AliasRecord, ...]:
        """Every name observed for a principal, newest observation first."""
        rows = (
            (
                await self._session.execute(
                    select(principal_aliases)
                    .where(principal_aliases.c.principal_key == principal_key)
                    .order_by(
                        principal_aliases.c.last_observed_at.desc(),
                        principal_aliases.c.alias_kind,
                        principal_aliases.c.value_folded,
                    )
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            AliasRecord(
                alias_kind=row["alias_kind"],
                value=row["value"],
                first_observed_at=row["first_observed_at"],
                last_observed_at=row["last_observed_at"],
            )
            for row in rows
        )

    async def is_known(self, key: str) -> bool:
        """Whether anything at all is stored about a key.

        A key with no principal row can still be a real node: an edge may name a member no
        run has described yet. Both count as known, because answering "no such principal"
        for a SID that demonstrably appears in a group would hide a real membership.
        """
        statement = select(
            select(principals.c.principal_key)
            .where(principals.c.principal_key == key)
            .exists()
            .label("as_principal"),
            select(membership_edges.c.edge_key)
            .where(
                or_(
                    membership_edges.c.group_key == key,
                    membership_edges.c.member_key == key,
                )
            )
            .exists()
            .label("as_endpoint"),
        )
        row = (await self._session.execute(statement)).one()
        return bool(row.as_principal or row.as_endpoint)

    # ------------------------------------------------- direct membership listing

    async def direct_members(
        self, group_key: str, *, limit: int = DEFAULT_PAGE_SIZE, after: str | None = None
    ) -> Page[DirectEdgeRecord]:
        """Principals directly inside ``group_key``, ordered by member key."""
        return await self._direct(
            anchor=membership_edges.c.group_key,
            anchor_value=group_key,
            counterpart=membership_edges.c.member_key,
            limit=limit,
            after=after,
        )

    async def direct_groups(
        self, member_key: str, *, limit: int = DEFAULT_PAGE_SIZE, after: str | None = None
    ) -> Page[DirectEdgeRecord]:
        """Groups that directly contain ``member_key``, ordered by group key."""
        return await self._direct(
            anchor=membership_edges.c.member_key,
            anchor_value=member_key,
            counterpart=membership_edges.c.group_key,
            limit=limit,
            after=after,
        )

    async def _direct(
        self,
        *,
        anchor: Any,
        anchor_value: str,
        counterpart: Any,
        limit: int,
        after: str | None,
    ) -> Page[DirectEdgeRecord]:
        page_size = max(1, min(limit, MAX_PAGE_SIZE))
        # Deliberately two queries rather than one join: principals and membership_edges
        # share eight column names (host_key, source_key, the four observation columns,
        # created_at, updated_at), so a joined row mapping would silently resolve each of
        # them to whichever table came last.
        statement: Select[Any] = (
            select(
                *_EDGE_COLUMNS,
                membership_edges.c.first_observed_at,
                membership_edges.c.last_observed_at,
                membership_edges.c.last_observed_run_id,
                counterpart.label("counterpart_key"),
            )
            .where(anchor == anchor_value)
            # (counterpart, edge_key) is unique, so the pair is a total order and the cursor
            # cannot land in the middle of a tie.
            .order_by(counterpart, membership_edges.c.edge_key)
            .limit(page_size + 1)
        )
        if after is not None:
            statement = statement.where(counterpart > after)

        rows = (await self._session.execute(statement)).mappings().all()
        has_more = len(rows) > page_size
        visible = rows[:page_size]
        labels = await self.principals_by_keys([row["counterpart_key"] for row in visible])

        items = tuple(
            DirectEdgeRecord(
                edge=_graph_edge(row),
                counterpart_key=row["counterpart_key"],
                counterpart=labels.get(row["counterpart_key"]),
                first_observed_at=row["first_observed_at"],
                last_observed_at=row["last_observed_at"],
                last_observed_run_id=row["last_observed_run_id"],
            )
            for row in visible
        )
        return Page(
            items=items,
            has_more=has_more,
            next_key=items[-1].counterpart_key if has_more and items else None,
        )

    async def count_direct(
        self, *, group_key: str | None = None, member_key: str | None = None
    ) -> int:
        """Exact count of direct edges on one side. Cheap: both endpoints are indexed."""
        if (group_key is None) == (member_key is None):
            raise ValueError("Count exactly one side: pass group_key or member_key, not both.")
        column = (
            membership_edges.c.group_key if group_key is not None else membership_edges.c.member_key
        )
        value = group_key if group_key is not None else member_key
        return int(
            (
                await self._session.execute(
                    select(func.count()).select_from(membership_edges).where(column == value)
                )
            ).scalar_one()
        )


def _graph_edge(row: RowMapping) -> GraphEdge:
    return GraphEdge(
        edge_key=row["edge_key"],
        group_key=row["group_key"],
        member_key=row["member_key"],
        kind=MembershipEdgeKind(row["edge_kind"]),
        host_key=row["host_key"],
        member_kind=_optional_kind(row["member_kind"]),
        is_foreign_security_principal=bool(row["is_foreign_security_principal"]),
    )


def _optional_kind(value: str | None) -> PrincipalKind | None:
    return None if value is None else PrincipalKind(value)


def _principal_record(row: RowMapping) -> PrincipalRecord:
    return PrincipalRecord(
        principal_key=row["principal_key"],
        sid=row["sid"],
        principal_kind=PrincipalKind(row["principal_kind"]),
        host_key=row["host_key"],
        domain_sid=row["domain_sid"],
        display_name=row["display_name"],
        sam_account_name=row["sam_account_name"],
        user_principal_name=row["user_principal_name"],
        distinguished_name=row["distinguished_name"],
        group_scope=row["group_scope"],
        group_type=row["group_type"],
        enabled=row["enabled"],
        is_deleted=bool(row["is_deleted"]),
        unresolved_reason=row["unresolved_reason"],
        last_known_name=row["last_known_name"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
    )


def _chunks(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
