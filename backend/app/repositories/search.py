"""Bounded lookups behind the global search box.

Search is the one query in ADG whose input is arbitrary text, so every statement here is
written to be cheap at any estate size:

* **Matching is by prefix only** (``value LIKE 'term%'``). An infix match cannot use a
  btree index and would degrade into a sequential scan of every path in the estate on a
  keystroke. The API says so in the interpretation it returns, so nobody is left wondering
  why ``finance`` did not match ``Corp-Finance``.
* **Every statement carries a LIMIT**, and the caller is told when it bit. A truncated
  search that claims to be complete is how an auditor concludes a share does not exist.
* **The term is never interpolated.** It is a bound parameter, and the LIKE wildcards
  ``%``, ``_`` and ``\\`` in it are escaped, so a user typing ``%`` searches for a percent
  sign rather than for everything.

Keys are compared case-folded because that is how they are stored (see
``Server.identity_key`` and friends); display columns are matched with ``ILIKE`` because
they preserve the case that was observed.

Every statement runs against the ``current_*`` sources in :mod:`app.models.current`, so a
principal, server, share or directory that a successful authoritative reconciliation proved
gone is not offered as a destination to navigate to. History still holds it; search is a way
into the estate as it is.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.current import (
    current_ntfs_resources,
    current_principals,
    current_servers,
    current_smb_shares,
)

__all__ = [
    "MAX_HITS_PER_CATEGORY",
    "PrincipalHit",
    "ResourceHit",
    "SearchRepository",
    "ServerHit",
    "ShareHit",
    "escape_like",
]

#: Per-category ceiling. Ten is a search box, not a report; the categories that deserve a
#: full listing have their own paginated endpoints, which the UI links to.
MAX_HITS_PER_CATEGORY = 10

_LIKE_ESCAPE = "\\"


def escape_like(term: str) -> str:
    """Neutralize LIKE wildcards so a typed ``%`` means a percent sign."""
    return (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


@dataclass(frozen=True, slots=True)
class PrincipalHit:
    principal_key: str
    sid: str
    principal_kind: str
    display_name: str | None
    sam_account_name: str | None
    user_principal_name: str | None
    host_key: str | None
    enabled: bool | None
    is_deleted: bool


@dataclass(frozen=True, slots=True)
class ServerHit:
    server_key: str
    name: str
    dns_host_name: str | None


@dataclass(frozen=True, slots=True)
class ShareHit:
    share_key: str
    server_key: str
    name: str
    share_type: str
    description: str | None


@dataclass(frozen=True, slots=True)
class ResourceHit:
    resource_key: str
    path: str
    server_key: str
    share_key: str
    is_acl_boundary: bool


class SearchRepository:
    """One bounded statement per category."""

    def __init__(self, session: AsyncSession, *, limit: int = MAX_HITS_PER_CATEGORY) -> None:
        self._session = session
        self._limit = max(1, min(limit, MAX_HITS_PER_CATEGORY))

    @property
    def limit(self) -> int:
        return self._limit

    async def principals_by_sid(self, sid: str) -> tuple[PrincipalHit, ...]:
        """Every principal carrying this SID.

        More than one is normal and not an error: a BUILTIN SID exists separately on every
        computer that reported it, which is exactly why ``principal_key`` scopes it by host.
        """
        statement = (
            select(current_principals)
            .where(current_principals.c.sid == sid)
            .order_by(current_principals.c.principal_key)
            .limit(self._limit + 1)
        )
        return await self._principals(statement)

    async def principals_by_name(self, term: str) -> tuple[PrincipalHit, ...]:
        pattern = f"{escape_like(term)}%"
        statement = (
            select(current_principals)
            .where(
                or_(
                    current_principals.c.display_name.ilike(pattern, escape=_LIKE_ESCAPE),
                    current_principals.c.sam_account_name.ilike(pattern, escape=_LIKE_ESCAPE),
                    current_principals.c.user_principal_name.ilike(pattern, escape=_LIKE_ESCAPE),
                    # A principal nobody could resolve still has the name the ACE carried,
                    # and that is frequently the only string an investigator has.
                    current_principals.c.last_known_name.ilike(pattern, escape=_LIKE_ESCAPE),
                )
            )
            .order_by(current_principals.c.display_name, current_principals.c.principal_key)
            .limit(self._limit + 1)
        )
        return await self._principals(statement)

    async def servers_by_name(self, term: str) -> tuple[ServerHit, ...]:
        pattern = f"{escape_like(term.casefold())}%"
        statement = (
            select(
                current_servers.c.server_key,
                current_servers.c.name,
                current_servers.c.dns_host_name,
            )
            .where(
                or_(
                    current_servers.c.server_key.like(pattern, escape=_LIKE_ESCAPE),
                    func.lower(current_servers.c.dns_host_name).like(pattern, escape=_LIKE_ESCAPE),
                )
            )
            .order_by(current_servers.c.server_key)
            .limit(self._limit + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(
            ServerHit(
                server_key=row["server_key"],
                name=row["name"],
                dns_host_name=row["dns_host_name"],
            )
            for row in rows
        )

    async def shares_by_name(
        self, term: str | None, *, server_key: str | None = None
    ) -> tuple[ShareHit, ...]:
        """Shares whose name starts with ``term``, optionally on one server.

        ``server_key`` is applied when the user typed ``\\\\FS01\\Fin``: narrowing to the
        server they named is the difference between one answer and one per file server.
        ``term`` of ``None`` means "every share on that server", which is what somebody who
        typed a bare ``\\\\FS01`` asked for.
        """
        statement = select(
            current_smb_shares.c.share_key,
            current_smb_shares.c.server_key,
            current_smb_shares.c.name,
            current_smb_shares.c.share_type,
            current_smb_shares.c.description,
        )
        if term is not None:
            pattern = f"{escape_like(term.casefold())}%"
            statement = statement.where(
                func.lower(current_smb_shares.c.name).like(pattern, escape=_LIKE_ESCAPE)
            )
        if server_key is not None:
            statement = statement.where(current_smb_shares.c.server_key == server_key.casefold())
        statement = statement.order_by(current_smb_shares.c.share_key).limit(self._limit + 1)

        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(
            ShareHit(
                share_key=row["share_key"],
                server_key=row["server_key"],
                name=row["name"],
                share_type=row["share_type"],
                description=row["description"],
            )
            for row in rows
        )

    async def resources_by_path(self, term: str) -> tuple[ResourceHit, ...]:
        """Directories whose canonical path starts with ``term``.

        ``resource_key`` is the case-folded canonical UNC path and is the primary key, so
        this prefix match is an index range scan — the reason search takes paths at all.
        """
        pattern = f"{escape_like(term.casefold())}%"
        statement = (
            select(
                current_ntfs_resources.c.resource_key,
                current_ntfs_resources.c.path,
                current_ntfs_resources.c.server_key,
                current_ntfs_resources.c.share_key,
                current_ntfs_resources.c.is_acl_boundary,
            )
            .where(current_ntfs_resources.c.resource_key.like(pattern, escape=_LIKE_ESCAPE))
            .order_by(current_ntfs_resources.c.resource_key)
            .limit(self._limit + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(
            ResourceHit(
                resource_key=row["resource_key"],
                path=row["path"],
                server_key=row["server_key"],
                share_key=row["share_key"],
                is_acl_boundary=bool(row["is_acl_boundary"]),
            )
            for row in rows
        )

    async def _principals(self, statement: Select[tuple[object, ...]]) -> tuple[PrincipalHit, ...]:
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(
            PrincipalHit(
                principal_key=row["principal_key"],
                sid=row["sid"],
                principal_kind=row["principal_kind"],
                display_name=row["display_name"],
                sam_account_name=row["sam_account_name"],
                user_principal_name=row["user_principal_name"],
                host_key=row["host_key"],
                enabled=row["enabled"],
                is_deleted=bool(row["is_deleted"]),
            )
            for row in rows
        )
