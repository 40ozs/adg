"""Global search: which lookups to run for a typed term, and which the caller may see.

The service does three things and no more. It asks :func:`app.domain.search.interpret_query`
what the text is, it runs the bounded lookups that interpretation calls for, and it drops
whole categories the caller has no capability for.

Dropping a category is reported, never silent. "No identities matched" and "you may not
search identities" are different answers, and conflating them would let a viewer conclude
a group does not exist when in truth they were not allowed to look. This is the same
distinction the frontend draws between *no data* and *collection failed*, applied to
authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Capability
from app.domain.search import QueryShape, SearchQuery
from app.repositories.search import (
    PrincipalHit,
    ResourceHit,
    SearchRepository,
    ServerHit,
    ShareHit,
)

__all__ = ["SearchCategory", "SearchResults", "SearchService", "SkippedCategory"]

HitT = TypeVar("HitT")


class SearchCategory:
    """The category names the API reports. Strings, because they are a wire contract."""

    IDENTITIES = "identities"
    SERVERS = "servers"
    SHARES = "shares"
    DIRECTORIES = "directories"


#: Which capability each category needs. Search does not invent a permission model; it
#: reuses the one the corresponding listing endpoints enforce.
CATEGORY_CAPABILITY: dict[str, Capability] = {
    SearchCategory.IDENTITIES: Capability.IDENTITIES_READ,
    SearchCategory.SERVERS: Capability.RESOURCES_READ,
    SearchCategory.SHARES: Capability.RESOURCES_READ,
    SearchCategory.DIRECTORIES: Capability.RESOURCES_READ,
}


@dataclass(frozen=True, slots=True)
class SkippedCategory:
    category: str
    reason: str


@dataclass(slots=True)
class SearchResults:
    """What one search found, and what it did not look at."""

    query: SearchQuery
    identities: tuple[PrincipalHit, ...] = ()
    servers: tuple[ServerHit, ...] = ()
    shares: tuple[ShareHit, ...] = ()
    directories: tuple[ResourceHit, ...] = ()
    truncated: set[str] = field(default_factory=set)
    skipped: tuple[SkippedCategory, ...] = ()

    @property
    def total_shown(self) -> int:
        return len(self.identities) + len(self.servers) + len(self.shares) + len(self.directories)


class SearchService:
    """Runs the lookups one interpretation calls for, within one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._repository = SearchRepository(session)

    async def search(self, query: SearchQuery, *, granted: frozenset[Capability]) -> SearchResults:
        limit = self._repository.limit
        skipped = tuple(
            SkippedCategory(
                category=category,
                reason=(f"Not searched: this account lacks the '{capability.value}' capability."),
            )
            for category, capability in sorted(CATEGORY_CAPABILITY.items())
            if capability not in granted
        )
        may = {
            category
            for category, capability in CATEGORY_CAPABILITY.items()
            if capability in granted
        }

        results = SearchResults(query=query, skipped=skipped)

        if SearchCategory.IDENTITIES in may:
            identity_hits: tuple[PrincipalHit, ...]
            if query.shape is QueryShape.SID and query.sid is not None:
                identity_hits = await self._repository.principals_by_sid(query.sid.value)
            elif query.shape is QueryShape.NAME:
                identity_hits = await self._repository.principals_by_name(query.text)
            else:
                # A path names no principal. Skipping the query is not a missing answer,
                # so it is not reported as a skipped category either.
                identity_hits = ()
            results.identities = _trim(identity_hits, limit, SearchCategory.IDENTITIES, results)

        if SearchCategory.SERVERS in may:
            term = query.server if query.server is not None else query.text
            server_hits: tuple[ServerHit, ...] = (
                await self._repository.servers_by_name(term)
                if query.shape in (QueryShape.NAME, QueryShape.SERVER)
                else ()
            )
            results.servers = _trim(server_hits, limit, SearchCategory.SERVERS, results)

        if SearchCategory.SHARES in may:
            share_hits = await self._shares(query)
            results.shares = _trim(share_hits, limit, SearchCategory.SHARES, results)

        if SearchCategory.DIRECTORIES in may:
            directory_hits = await self._directories(query)
            results.directories = _trim(directory_hits, limit, SearchCategory.DIRECTORIES, results)

        return results

    async def _shares(self, query: SearchQuery) -> tuple[ShareHit, ...]:
        if query.shape is QueryShape.NAME:
            return await self._repository.shares_by_name(query.text)
        if query.shape is QueryShape.SERVER and query.server is not None:
            return await self._repository.shares_by_name(None, server_key=query.server)
        if query.share is not None:
            return await self._repository.shares_by_name(query.share, server_key=query.server)
        return ()

    async def _directories(self, query: SearchQuery) -> tuple[ResourceHit, ...]:
        if query.shape is QueryShape.SERVER and query.server is not None:
            # Everything published from that machine, by path prefix.
            return await self._repository.resources_by_path(f"\\\\{query.server}\\")
        if query.unc_path is not None:
            return await self._repository.resources_by_path(query.unc_path.value)
        # A bare name cannot prefix-match a path, which always starts with two backslashes.
        return ()


def _trim(
    hits: tuple[HitT, ...], limit: int, category: str, results: SearchResults
) -> tuple[HitT, ...]:
    """Cut an over-fetched page back to the limit, recording that it was cut.

    The repository fetches ``limit + 1`` so that "there are more" is a fact rather than an
    inference from a full page.
    """
    if len(hits) > limit:
        results.truncated.add(category)
        return hits[:limit]
    return hits
