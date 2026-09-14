"""Global search across identities, servers, shares, and directories.

One endpoint, because the UI has one search box. The response says how the term was
interpreted, which categories were searched, which were truncated, and which were not
searched at all — so an empty result is always attributable to something.

Every hit carries the key that addresses it on its own endpoint, so the frontend links by
identifier rather than by re-deriving one from a display name.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import Session
from app.auth.dependencies import CurrentPrincipal
from app.domain.search import SearchTermError, interpret_query
from app.repositories.search import MAX_HITS_PER_CATEGORY
from app.services.search import SearchResults, SearchService

router = APIRouter(prefix="/api/v1", tags=["search"])


class IdentityHitView(BaseModel):
    principal_key: str
    sid: str
    kind: str
    display_name: str | None
    sam_account_name: str | None
    user_principal_name: str | None
    host_key: str | None = Field(
        default=None, description="Set for a local group, which is scoped to one computer."
    )
    enabled: bool | None
    is_deleted: bool


class ServerHitView(BaseModel):
    server_key: str
    name: str
    dns_host_name: str | None


class ShareHitView(BaseModel):
    share_key: str
    server_key: str
    name: str
    unc_path: str
    share_type: str
    description: str | None


class DirectoryHitView(BaseModel):
    resource_key: str
    path: str
    server_key: str
    share_key: str
    is_acl_boundary: bool


class SkippedCategoryView(BaseModel):
    category: str
    reason: str


class SearchResponse(BaseModel):
    """What was found, and everything needed to interpret an empty answer."""

    query: str
    interpreted_as: Literal["sid", "unc_path", "server", "share", "name"]
    interpretation: str = Field(description="One sentence explaining the interpretation.")
    limit_per_category: int
    identities: list[IdentityHitView] = Field(default_factory=list)
    servers: list[ServerHitView] = Field(default_factory=list)
    shares: list[ShareHitView] = Field(default_factory=list)
    directories: list[DirectoryHitView] = Field(default_factory=list)
    truncated: list[str] = Field(
        default_factory=list,
        description=(
            "Categories with more matches than this page shows. Open the category's own "
            "listing to page through them."
        ),
    )
    not_searched: list[SkippedCategoryView] = Field(
        default_factory=list,
        description=(
            "Categories this account may not search. Reported so that an empty result is "
            "never mistaken for an absent object."
        ),
    )


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Find an identity, server, share, or directory by SID, name, or path",
    responses={422: {"description": "The search term cannot be used; the message says why."}},
)
async def search(
    principal: CurrentPrincipal,
    session: Session,
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=1024,
            description="A SID, a UNC path, a share name, or the start of a display name.",
        ),
    ],
) -> SearchResponse:
    try:
        query = interpret_query(q)
    except SearchTermError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    results = await SearchService(session).search(query, granted=principal.granted)
    return _response(results)


def _response(results: SearchResults) -> SearchResponse:
    query = results.query
    return SearchResponse(
        query=query.text,
        interpreted_as=query.shape.value,
        interpretation=query.explanation,
        limit_per_category=MAX_HITS_PER_CATEGORY,
        identities=[
            IdentityHitView(
                principal_key=hit.principal_key,
                sid=hit.sid,
                kind=hit.principal_kind,
                display_name=hit.display_name,
                sam_account_name=hit.sam_account_name,
                user_principal_name=hit.user_principal_name,
                host_key=hit.host_key,
                enabled=hit.enabled,
                is_deleted=hit.is_deleted,
            )
            for hit in results.identities
        ],
        servers=[
            ServerHitView(server_key=hit.server_key, name=hit.name, dns_host_name=hit.dns_host_name)
            for hit in results.servers
        ],
        shares=[
            ShareHitView(
                share_key=hit.share_key,
                server_key=hit.server_key,
                name=hit.name,
                # Derived here for display only. The share's identity stays share_key;
                # storing a second spelling is what lets two spellings disagree.
                unc_path=f"\\\\{hit.server_key}\\{hit.name}",
                share_type=hit.share_type,
                description=hit.description,
            )
            for hit in results.shares
        ],
        directories=[
            DirectoryHitView(
                resource_key=hit.resource_key,
                path=hit.path,
                server_key=hit.server_key,
                share_key=hit.share_key,
                is_acl_boundary=hit.is_acl_boundary,
            )
            for hit in results.directories
        ],
        truncated=sorted(results.truncated),
        not_searched=[
            SkippedCategoryView(category=item.category, reason=item.reason)
            for item in results.skipped
        ],
    )


__all__ = ["router"]
