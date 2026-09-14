r"""Reading servers, shares, and raw share ACLs out of PostgreSQL.

Like :mod:`app.repositories.membership`, this turns rows into small typed records and does
nothing else: no permission logic, no interpretation of a mask, no notion of effective
access. What it returns is what a collector observed.

Three choices here are worth stating, because each is a claim about honesty rather than
about performance:

* **A missing parent is reported, not hidden.** A share whose server no run has described,
  and an ACE whose share was never observed, both come back with the parent as ``None``.
  There are no foreign keys to make those cases impossible, because they are real: a
  partial scan reads what it can reach. Dropping such a row from a listing would quietly
  shrink the estate.
* **Derived values are derived.** ``\\server\share``, hidden state, and "is this an
  administrative share" are computed from :class:`app.domain.SmbShare`, never read from a
  column, so a stored copy cannot disagree with the identity it was derived from.
* **A share ACL is returned in ACL order.** The order a DACL is written in is what makes a
  Deny evaluable, so ``order_index`` leads the sort and paging is by offset. Keyset paging
  would need a unique monotonic key and would force the rows into ``ace_key`` order, which
  is alphabetical and means nothing. A share ACL is a handful of entries; the trade the
  membership listings make does not apply.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import RowMapping, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    AceType,
    LocalPath,
    SharePermission,
    ShareType,
    Sid,
    SmbShare,
    UncPath,
    parse_local_path,
)
from app.models.schema import (
    ReferenceKind,
    principal_references,
    servers,
    smb_share_aces,
    smb_shares,
)
from app.repositories.membership import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page

__all__ = [
    "ResourceRepository",
    "ServerRecord",
    "ShareAceRecord",
    "ShareRecord",
    "ShareReferenceRecord",
]

ItemT = TypeVar("ItemT")


@dataclass(frozen=True, slots=True)
class ServerRecord:
    """A stored server, with the provenance that justifies it."""

    server_key: str
    name: str
    dns_host_name: str | None
    netbios_name: str | None
    computer_sid: str | None
    domain_sid: str | None
    is_domain_member: bool | None
    operating_system: str | None
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID
    share_count: int = 0


@dataclass(frozen=True, slots=True)
class ShareRecord:
    """A stored share. Everything positional about it is derived, not stored."""

    share_key: str
    server_key: str
    name: str
    local_path: str | None
    share_type: ShareType
    description: str | None
    concurrent_user_limit: int | None
    caching_mode: str | None
    is_special: bool | None
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID

    @property
    def _domain(self) -> SmbShare:
        path: LocalPath | None = parse_local_path(self.local_path) if self.local_path else None
        return SmbShare(
            server_key=self.server_key,
            name=self.name,
            local_path=path,
            share_type=self.share_type,
            description=self.description,
            concurrent_user_limit=self.concurrent_user_limit,
        )

    @property
    def unc_path(self) -> UncPath:
        return self._domain.unc_path

    @property
    def is_hidden(self) -> bool:
        return self._domain.is_hidden

    @property
    def is_administrative(self) -> bool:
        return self._domain.is_administrative

    @property
    def carries_file_permissions(self) -> bool:
        return self._domain.carries_file_permissions


@dataclass(frozen=True, slots=True)
class ShareAceRecord:
    """One share-level ACE, exactly as a collector read it.

    ``access_mask`` and ``permission`` are never both present: a source reports one form or
    the other, and converting between them here would invent precision. ``right_token`` is
    whichever one was reported, rendered.
    """

    ace_key: str
    share_key: str
    trustee_sid: str
    trustee_key: str
    ace_type: AceType
    access_mask: int | None
    permission: SharePermission | None
    right_token: str
    order_index: int | None
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID


@dataclass(frozen=True, slots=True)
class ShareReferenceRecord:
    """One share that names a trustee, with the entries that name it.

    ``share`` is ``None`` when ACEs exist for a share no run has described. That is an
    orphaned ACL rather than an error, and it is exactly the sort of thing a scan of one
    server's ACLs produces before a scan of its share list has run.
    """

    share_key: str
    share: ShareRecord | None
    aces: tuple[ShareAceRecord, ...]


class ResourceRepository:
    """Server, share, and share-ACL queries against one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------- servers

    async def list_servers(
        self, *, limit: int = DEFAULT_PAGE_SIZE, after: str | None = None
    ) -> Page[ServerRecord]:
        """Every known server, ordered by key."""
        page_size = _page_size(limit)
        statement: Select[Any] = (
            select(servers, _share_count_column())
            .order_by(servers.c.server_key)
            .limit(page_size + 1)
        )
        if after is not None:
            statement = statement.where(servers.c.server_key > after)

        rows = (await self._session.execute(statement)).mappings().all()
        return _page(rows, page_size, _server_record, lambda record: record.server_key)

    async def get_server(self, server_key: str) -> ServerRecord | None:
        row = (
            (
                await self._session.execute(
                    select(servers, _share_count_column()).where(
                        servers.c.server_key == server_key.casefold()
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _server_record(row)

    async def count_servers(self) -> int:
        return int(
            (await self._session.execute(select(func.count()).select_from(servers))).scalar_one()
        )

    async def servers_by_keys(self, keys: Sequence[str]) -> dict[str, ServerRecord]:
        if not keys:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(servers, _share_count_column()).where(
                        servers.c.server_key.in_(sorted(set(keys)))
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["server_key"]: _server_record(row) for row in rows}

    # ----------------------------------------------------------------- shares

    async def list_shares(
        self, server_key: str, *, limit: int = DEFAULT_PAGE_SIZE, after: str | None = None
    ) -> Page[ShareRecord]:
        """Shares published by one server, ordered by key."""
        page_size = _page_size(limit)
        statement: Select[Any] = (
            select(smb_shares)
            .where(smb_shares.c.server_key == server_key.casefold())
            .order_by(smb_shares.c.share_key)
            .limit(page_size + 1)
        )
        if after is not None:
            statement = statement.where(smb_shares.c.share_key > after)

        rows = (await self._session.execute(statement)).mappings().all()
        return _page(rows, page_size, _share_record, lambda record: record.share_key)

    async def count_shares(self, server_key: str) -> int:
        statement = (
            select(func.count())
            .select_from(smb_shares)
            .where(smb_shares.c.server_key == server_key.casefold())
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def get_share(self, share_key: str) -> ShareRecord | None:
        row = (
            (
                await self._session.execute(
                    select(smb_shares).where(smb_shares.c.share_key == share_key.casefold())
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _share_record(row)

    async def shares_by_keys(self, keys: Sequence[str]) -> dict[str, ShareRecord]:
        if not keys:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(smb_shares).where(smb_shares.c.share_key.in_(sorted(set(keys))))
                )
            )
            .mappings()
            .all()
        )
        return {row["share_key"]: _share_record(row) for row in rows}

    async def has_aces(self, share_key: str) -> bool:
        """Whether any ACE names this share, even if the share itself was never described."""
        statement = select(
            select(smb_share_aces.c.ace_key)
            .where(smb_share_aces.c.share_key == share_key.casefold())
            .exists()
        )
        return bool((await self._session.execute(statement)).scalar_one())

    # -------------------------------------------------------------- share ACL

    async def share_acl(
        self, share_key: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[tuple[ShareAceRecord, ...], bool]:
        """One share's ACL in DACL order, plus whether more entries follow.

        ``order_index`` leads the sort with nulls last: a source that did not report a
        position gets no invented one, and the remaining rows keep a deterministic order
        through ``ace_key``.
        """
        page_size = _page_size(limit)
        statement = (
            select(smb_share_aces)
            .where(smb_share_aces.c.share_key == share_key.casefold())
            .order_by(smb_share_aces.c.order_index.nulls_last(), smb_share_aces.c.ace_key)
            .offset(max(0, offset))
            .limit(page_size + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        has_more = len(rows) > page_size
        return tuple(_ace_record(row) for row in rows[:page_size]), has_more

    async def count_acl(self, share_key: str) -> int:
        statement = (
            select(func.count())
            .select_from(smb_share_aces)
            .where(smb_share_aces.c.share_key == share_key.casefold())
        )
        return int((await self._session.execute(statement)).scalar_one())

    # ------------------------------------------------------------- references

    async def shares_referencing(
        self,
        *,
        trustee_key: str | None = None,
        trustee_sid: Sid | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        after: str | None = None,
    ) -> Page[ShareReferenceRecord]:
        """Shares whose ACL names a trustee, with the entries that name it.

        Pass ``trustee_key`` for one specific principal — including one server's BUILTIN
        group — or ``trustee_sid`` to ask about a SID wherever it appears. The second form
        is the right answer for a BUILTIN SID: "which shares grant S-1-5-32-544" is a
        question about every server that has one, and answering with a 409 would refuse a
        question that has a single correct answer.
        """
        page_size = _page_size(limit)
        predicate = _trustee_predicate(trustee_key, trustee_sid)

        keys_statement: Select[Any] = (
            select(smb_share_aces.c.share_key)
            .where(predicate)
            .group_by(smb_share_aces.c.share_key)
            .order_by(smb_share_aces.c.share_key)
            .limit(page_size + 1)
        )
        if after is not None:
            keys_statement = keys_statement.where(smb_share_aces.c.share_key > after)

        keys = [row[0] for row in (await self._session.execute(keys_statement)).all()]
        has_more = len(keys) > page_size
        visible = keys[:page_size]
        if not visible:
            return Page(items=(), has_more=False, next_key=None)

        ace_rows = (
            (
                await self._session.execute(
                    select(smb_share_aces)
                    .where(smb_share_aces.c.share_key.in_(visible), predicate)
                    .order_by(
                        smb_share_aces.c.share_key,
                        smb_share_aces.c.order_index.nulls_last(),
                        smb_share_aces.c.ace_key,
                    )
                )
            )
            .mappings()
            .all()
        )
        grouped: dict[str, list[ShareAceRecord]] = {key: [] for key in visible}
        for row in ace_rows:
            grouped[row["share_key"]].append(_ace_record(row))

        shares = await self.shares_by_keys(visible)
        items = tuple(
            ShareReferenceRecord(share_key=key, share=shares.get(key), aces=tuple(grouped[key]))
            for key in visible
        )
        return Page(
            items=items, has_more=has_more, next_key=items[-1].share_key if has_more else None
        )

    async def count_shares_referencing(
        self, *, trustee_key: str | None = None, trustee_sid: Sid | None = None
    ) -> int:
        statement = (
            select(func.count(func.distinct(smb_share_aces.c.share_key)))
            .select_from(smb_share_aces)
            .where(_trustee_predicate(trustee_key, trustee_sid))
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def reference_keys_for(self, principal_key: str) -> tuple[str, ...]:
        """Every resource key that names this principal, from the reference index."""
        rows = (
            await self._session.execute(
                select(principal_references.c.reference_key)
                .where(
                    principal_references.c.principal_key == principal_key,
                    principal_references.c.reference_kind == ReferenceKind.SMB_ACE.value,
                )
                .order_by(principal_references.c.reference_key)
            )
        ).all()
        return tuple(row[0] for row in rows)


def _trustee_predicate(trustee_key: str | None, trustee_sid: Sid | None) -> Any:
    if trustee_key is not None:
        return smb_share_aces.c.trustee_key == trustee_key
    if trustee_sid is not None:
        return smb_share_aces.c.trustee_sid == trustee_sid.value
    raise ValueError("A trustee query needs either trustee_key or trustee_sid.")


def _share_count_column() -> Any:
    """Shares per server, as a correlated count.

    A scalar subquery rather than a join with ``GROUP BY``: the page is at most a few
    hundred servers and the count is an index-only scan on ``ix_smb_shares_server``, while
    the join would have to be an outer one to keep a server that publishes nothing.
    """
    return (
        select(func.count())
        .select_from(smb_shares)
        .where(smb_shares.c.server_key == servers.c.server_key)
        .scalar_subquery()
        .label("share_count")
    )


def _page_size(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def _page(
    rows: Sequence[RowMapping],
    page_size: int,
    build: Callable[[RowMapping], ItemT],
    key_of: Callable[[ItemT], str],
) -> Page[ItemT]:
    has_more = len(rows) > page_size
    items = tuple(build(row) for row in rows[:page_size])
    return Page(
        items=items, has_more=has_more, next_key=key_of(items[-1]) if has_more and items else None
    )


def _server_record(row: RowMapping) -> ServerRecord:
    return ServerRecord(
        server_key=row["server_key"],
        name=row["name"],
        dns_host_name=row["dns_host_name"],
        netbios_name=row["netbios_name"],
        computer_sid=row["computer_sid"],
        domain_sid=row["domain_sid"],
        is_domain_member=row["is_domain_member"],
        operating_system=row["operating_system"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
        share_count=int(row["share_count"]) if "share_count" in row else 0,
    )


def _share_record(row: RowMapping) -> ShareRecord:
    return ShareRecord(
        share_key=row["share_key"],
        server_key=row["server_key"],
        name=row["name"],
        local_path=row["local_path"],
        share_type=ShareType(row["share_type"]),
        description=row["description"],
        concurrent_user_limit=row["concurrent_user_limit"],
        caching_mode=row["caching_mode"],
        is_special=row["is_special"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
    )


def _ace_record(row: RowMapping) -> ShareAceRecord:
    permission = row["permission"]
    return ShareAceRecord(
        ace_key=row["ace_key"],
        share_key=row["share_key"],
        trustee_sid=row["trustee_sid"],
        trustee_key=row["trustee_key"],
        ace_type=AceType(row["ace_type"]),
        access_mask=None if row["access_mask"] is None else int(row["access_mask"]),
        permission=None if permission is None else SharePermission(permission),
        right_token=row["right_token"],
        order_index=row["order_index"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
    )
