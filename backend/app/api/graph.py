"""Principal and membership-graph query endpoints.

Every recursive answer here carries a ``traversal`` block saying whether it is complete.
That is not decoration. A bounded traversal that hit a limit has produced a **lower bound**
on membership, and an audit tool that presents a lower bound as an answer understates
access — the most dangerous mistake it can make. Clients must read ``traversal.complete``
before treating an empty or short result as "nobody else".

Identifiers accept either a bare SID or a host-scoped storage key (``fs01|S-1-5-32-544``).
A bare BUILTIN SID that matches several hosts is **not** resolved by picking one: the
request fails with 409 and lists the candidates, because merging one server's local
administrators into another's would invent access that nobody has.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from app.api.deps import Session, TraversalBounds
from app.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    PageInfo,
    decode_keyset_cursor,
    decode_offset_cursor,
    encode_keyset_cursor,
    encode_offset_cursor,
    normalize_limit,
)
from app.domain import GraphCycle, MembershipPath, Sid, TraversalLimits
from app.repositories import AliasRecord, DirectEdgeRecord, MembershipRepository, PrincipalRecord
from app.services.graph import (
    GROUP_KINDS,
    EffectiveMembership,
    GraphService,
    MemberInclusion,
    PathResult,
    ResolvedNode,
    split_key,
)

router = APIRouter(prefix="/api/v1", tags=["graph"])

__all__ = ["PrincipalSummary", "principal_summary", "router"]

IdentifierPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=512,
        description=(
            "A SID (S-1-5-21-...) or a host-scoped storage key (fs01|S-1-5-32-544). "
            "Use ?host= to disambiguate a BUILTIN SID seen on several computers."
        ),
    ),
]

HostQuery = Annotated[
    str | None,
    Query(
        max_length=255,
        description="Host that scopes a local group, when the identifier is a bare SID.",
    ),
]

LimitQuery = Annotated[
    int | None,
    Query(ge=1, le=MAX_LIMIT, description=f"Items per page (default {DEFAULT_LIMIT})."),
]

CursorQuery = Annotated[
    str | None, Query(description="Opaque cursor from a previous response's next_cursor.")
]


# --------------------------------------------------------------------- views


class PrincipalSummary(BaseModel):
    """What is known about one principal, as far as any of it is known."""

    key: str = Field(description="Storage key: the SID, or host|SID for a local group.")
    sid: str
    host_key: str | None = None
    kind: str | None = Field(
        default=None,
        description=(
            "Null when no run has described this principal. The membership that reached it "
            "was still observed; only the description is missing."
        ),
    )
    resolved: bool = Field(description="Whether ADG holds a principal record for this key.")
    is_group: bool | None = Field(
        default=None, description="Null when the kind is unknown, never a guessed false."
    )
    display_name: str | None = None
    sam_account_name: str | None = None
    user_principal_name: str | None = None
    distinguished_name: str | None = None
    group_scope: str | None = None
    group_type: str | None = None
    enabled: bool | None = None
    is_deleted: bool = False
    unresolved_reason: str | None = None
    last_known_name: str | None = None


class AliasView(BaseModel):
    alias_kind: str
    value: str
    first_observed_at: dt.datetime
    last_observed_at: dt.datetime


class PrincipalDetail(PrincipalSummary):
    """A principal, its provenance, every name ever seen for it, and its degree."""

    domain_sid: str | None = None
    first_observed_at: dt.datetime | None = None
    first_observed_run_id: UUID | None = None
    last_observed_at: dt.datetime | None = None
    last_observed_run_id: UUID | None = None
    aliases: list[AliasView] = Field(default_factory=list)
    direct_member_count: int = Field(description="Principals directly inside this one.")
    direct_group_count: int = Field(description="Groups this principal is directly inside.")


class LimitsView(BaseModel):
    max_depth: int
    max_nodes: int
    max_edges: int
    max_paths: int


class TraversalView(BaseModel):
    """Whether the answer above it is the whole answer."""

    complete: bool = Field(
        description=(
            "False means the result is a lower bound: more principals may qualify. Do not "
            "render an incomplete result as a complete membership list."
        )
    )
    truncation: list[str] = Field(
        default_factory=list, description="Which limits stopped the traversal."
    )
    limits: LimitsView
    depth_reached: int
    nodes_visited: int
    edges_read: int


class CycleView(BaseModel):
    """A membership cycle, reported as a finding rather than silently skipped."""

    members: list[str]
    representative_path: list[str] = Field(
        description="One concrete loop, first and last element equal."
    )


class DirectMemberView(BaseModel):
    principal: PrincipalSummary
    edge_kind: str
    edge_key: str
    host_key: str | None = None
    is_foreign_security_principal: bool = False
    first_observed_at: dt.datetime
    last_observed_at: dt.datetime
    last_observed_run_id: UUID


class EffectiveMemberView(BaseModel):
    principal: PrincipalSummary
    depth: int = Field(description="Hops from the queried group; 1 is a direct member.")
    path: list[str] = Field(
        description="Shortest chain of keys from the queried group to this principal."
    )
    edge_kinds: list[str]
    via_foreign_security_principal: bool = False


class MembershipPathView(BaseModel):
    nodes: list[str]
    principals: list[PrincipalSummary]
    edge_kinds: list[str]
    length: int


class DirectMembersResponse(BaseModel):
    group: PrincipalSummary
    items: list[DirectMemberView]
    page: PageInfo


class DirectGroupsResponse(BaseModel):
    principal: PrincipalSummary
    items: list[DirectMemberView]
    page: PageInfo


class EffectiveMembersResponse(BaseModel):
    group: PrincipalSummary
    include: str
    items: list[EffectiveMemberView]
    page: PageInfo
    traversal: TraversalView
    cycles: list[CycleView] = Field(default_factory=list)


class EffectiveGroupsResponse(BaseModel):
    principal: PrincipalSummary
    items: list[EffectiveMemberView]
    page: PageInfo
    traversal: TraversalView
    cycles: list[CycleView] = Field(default_factory=list)


class MembershipPathsResponse(BaseModel):
    principal: PrincipalSummary
    group: PrincipalSummary
    is_member: bool = Field(
        description=(
            "True when at least one path exists. False with traversal.complete false means "
            "unknown, not 'no': the search was cut short before it could rule membership out."
        )
    )
    paths: list[MembershipPathView]
    traversal: TraversalView
    cycles: list[CycleView] = Field(default_factory=list)


# ----------------------------------------------------------------- endpoints


@router.get(
    "/principals/{identifier}",
    response_model=PrincipalDetail,
    summary="Look up a principal by SID or storage key",
    responses={
        404: {"description": "No principal and no membership edge names this key."},
        409: {"description": "A bare SID matched several host-scoped principals."},
    },
)
async def get_principal(
    identifier: IdentifierPath, session: Session, host: HostQuery = None
) -> PrincipalDetail:
    repository = MembershipRepository(session)
    key, record = await _resolve(repository, identifier, host)
    aliases = await repository.aliases_for(key) if record is not None else ()
    return _principal_detail(
        key,
        record,
        aliases,
        direct_member_count=await repository.count_direct(group_key=key),
        direct_group_count=await repository.count_direct(member_key=key),
    )


@router.get(
    "/groups/{identifier}/members",
    response_model=DirectMembersResponse,
    summary="Direct members of a group",
    responses={404: {"description": "Unknown group."}, 409: {"description": "Ambiguous SID."}},
)
async def group_members(
    identifier: IdentifierPath,
    session: Session,
    host: HostQuery = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> DirectMembersResponse:
    repository = MembershipRepository(session)
    key, record = await _resolve(repository, identifier, host)
    page_size = normalize_limit(limit)
    page = await repository.direct_members(key, limit=page_size, after=decode_keyset_cursor(cursor))
    return DirectMembersResponse(
        group=principal_summary(key, record),
        items=[_direct_view(item) for item in page.items],
        page=PageInfo(
            limit=page_size,
            has_more=page.has_more,
            next_cursor=encode_keyset_cursor(page.next_key) if page.next_key else None,
            total=await repository.count_direct(group_key=key),
        ),
    )


@router.get(
    "/groups/{identifier}/effective-members",
    response_model=EffectiveMembersResponse,
    summary="Every principal that is effectively a member, with the chain that puts it there",
    responses={404: {"description": "Unknown group."}, 409: {"description": "Ambiguous SID."}},
)
async def group_effective_members(
    identifier: IdentifierPath,
    session: Session,
    limits: TraversalBounds,
    host: HostQuery = None,
    include: Annotated[
        MemberInclusion,
        Query(
            description=(
                "users: user and service accounts only. non_groups (default): everything "
                "that is not a known group, including SIDs ADG has not described — an "
                "orphaned SID inside a group is a finding, not noise. all: nested groups too."
            )
        ),
    ] = MemberInclusion.NON_GROUPS,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> EffectiveMembersResponse:
    repository = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    key, record = await _resolve(repository, identifier, host)
    result = await GraphService(repository).effective_members(key, limits)

    matching = [node for node in result.nodes if node.matches(include)]
    items, page = _slice(matching, limit, cursor)
    return EffectiveMembersResponse(
        group=principal_summary(key, record),
        include=include.value,
        items=[_effective_view(node) for node in items],
        page=page,
        traversal=_traversal_view(result, limits),
        cycles=[_cycle_view(cycle) for cycle in result.cycles],
    )


@router.get(
    "/principals/{identifier}/groups",
    response_model=DirectGroupsResponse | EffectiveGroupsResponse,
    summary="Groups containing a principal, directly or through nesting",
    responses={404: {"description": "Unknown principal."}, 409: {"description": "Ambiguous SID."}},
)
async def principal_groups(
    identifier: IdentifierPath,
    session: Session,
    limits: TraversalBounds,
    host: HostQuery = None,
    scope: Annotated[
        str,
        Query(
            pattern="^(direct|effective)$",
            description=(
                "direct: only groups that name this principal in their own membership. "
                "effective: every group reached through nesting as well."
            ),
        ),
    ] = "direct",
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> DirectGroupsResponse | EffectiveGroupsResponse:
    repository = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    key, record = await _resolve(repository, identifier, host)

    if scope == "direct":
        page_size = normalize_limit(limit)
        page = await repository.direct_groups(
            key, limit=page_size, after=decode_keyset_cursor(cursor)
        )
        return DirectGroupsResponse(
            principal=principal_summary(key, record),
            items=[_direct_view(item) for item in page.items],
            page=PageInfo(
                limit=page_size,
                has_more=page.has_more,
                next_cursor=encode_keyset_cursor(page.next_key) if page.next_key else None,
                total=await repository.count_direct(member_key=key),
            ),
        )

    result = await GraphService(repository).effective_groups(key, limits)
    items, page_info = _slice(list(result.nodes), limit, cursor)
    return EffectiveGroupsResponse(
        principal=principal_summary(key, record),
        items=[_effective_view(node) for node in items],
        page=page_info,
        traversal=_traversal_view(result, limits),
        cycles=[_cycle_view(cycle) for cycle in result.cycles],
    )


@router.get(
    "/principals/{identifier}/membership-paths",
    response_model=MembershipPathsResponse,
    summary="Every chain of memberships that puts a principal inside a group",
    responses={
        404: {"description": "Unknown principal or group."},
        409: {"description": "Ambiguous SID."},
        422: {"description": "The principal and the group are the same node."},
    },
)
async def membership_paths(
    identifier: IdentifierPath,
    session: Session,
    limits: TraversalBounds,
    group: Annotated[
        str,
        Query(min_length=1, max_length=512, description="The group's SID or storage key."),
    ],
    host: HostQuery = None,
    group_host: HostQuery = None,
) -> MembershipPathsResponse:
    repository = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    member_key, member_record = await _resolve(repository, identifier, host)
    group_key, group_record = await _resolve(repository, group, group_host)
    if member_key == group_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{member_key} is both the principal and the group. A principal is not a "
                "member of itself; name two distinct principals."
            ),
        )

    result: PathResult = await GraphService(repository).membership_paths(
        member_key, group_key, limits
    )
    return MembershipPathsResponse(
        principal=principal_summary(member_key, member_record),
        group=principal_summary(group_key, group_record),
        is_member=result.is_member,
        paths=[_path_view(path, result) for path in result.paths],
        traversal=TraversalView(
            complete=result.complete,
            truncation=[reason.value for reason in result.truncation],
            limits=_limits_view(limits),
            depth_reached=max((path.length for path in result.paths), default=0),
            nodes_visited=len({node for path in result.paths for node in path.nodes}),
            edges_read=repository.edges_fetched,
        ),
        cycles=[_cycle_view(cycle) for cycle in result.cycles],
    )


# ------------------------------------------------------------------- helpers


async def _resolve(
    repository: MembershipRepository, identifier: str, host: str | None
) -> tuple[str, PrincipalRecord | None]:
    """Turn a URL identifier into a storage key, or fail with a precise reason.

    A key with no ``principals`` row is still valid when a membership edge names it: the
    edge was observed, and refusing to answer would hide a membership ADG genuinely holds.
    """
    resolution = await repository.resolve(identifier, host)
    if resolution.record is not None:
        return resolution.record.principal_key, resolution.record
    if resolution.is_ambiguous:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"{identifier} names {len(resolution.candidates)} principals. A BUILTIN "
                    "SID is identical on every Windows computer, so it identifies a group "
                    "only together with its host. Re-request with ?host=, or use one of the "
                    "storage keys below."
                ),
                "candidates": [
                    {"key": candidate.principal_key, "host_key": candidate.host_key}
                    for candidate in resolution.candidates
                ],
            },
        )

    candidate = _candidate_key(identifier, host)
    if await repository.is_known(candidate):
        return candidate, None
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            f"Nothing is stored about {identifier!r}: no principal observation and no "
            "membership edge names it. It may simply not have been collected yet."
        ),
    )


def _candidate_key(identifier: str, host: str | None) -> str:
    """The storage key an identifier would have, canonicalizing the SID if it is one."""
    if "|" in identifier:
        return identifier
    sid = Sid.try_parse(identifier)
    value = sid.value if sid is not None else identifier
    return f"{host.casefold()}|{value}" if host else value


def principal_summary(key: str, record: PrincipalRecord | None) -> PrincipalSummary:
    """Render a principal, or the honest absence of one.

    Public because the resource endpoints render the trustee of a share ACE the same
    way. A second implementation would be free to drift into reporting an unresolved
    SID as a resolved principal with no name, which is a different claim entirely.
    """
    if record is None:
        host, sid = split_key(key)
        return PrincipalSummary(key=key, sid=sid, host_key=host, resolved=False)
    return PrincipalSummary(
        key=record.principal_key,
        sid=record.sid,
        host_key=record.host_key,
        kind=record.principal_kind.value,
        resolved=True,
        is_group=record.principal_kind in GROUP_KINDS,
        display_name=record.display_name,
        sam_account_name=record.sam_account_name,
        user_principal_name=record.user_principal_name,
        distinguished_name=record.distinguished_name,
        group_scope=record.group_scope,
        group_type=record.group_type,
        enabled=record.enabled,
        is_deleted=record.is_deleted,
        unresolved_reason=record.unresolved_reason,
        last_known_name=record.last_known_name,
    )


def _principal_detail(
    key: str,
    record: PrincipalRecord | None,
    aliases: tuple[AliasRecord, ...],
    *,
    direct_member_count: int,
    direct_group_count: int,
) -> PrincipalDetail:
    summary = principal_summary(key, record)
    return PrincipalDetail(
        **summary.model_dump(),
        domain_sid=record.domain_sid if record else None,
        first_observed_at=record.first_observed_at if record else None,
        first_observed_run_id=record.first_observed_run_id if record else None,
        last_observed_at=record.last_observed_at if record else None,
        last_observed_run_id=record.last_observed_run_id if record else None,
        aliases=[
            AliasView(
                alias_kind=alias.alias_kind,
                value=alias.value,
                first_observed_at=alias.first_observed_at,
                last_observed_at=alias.last_observed_at,
            )
            for alias in aliases
        ],
        direct_member_count=direct_member_count,
        direct_group_count=direct_group_count,
    )


def _direct_view(item: DirectEdgeRecord) -> DirectMemberView:
    return DirectMemberView(
        principal=principal_summary(item.counterpart_key, item.counterpart),
        edge_kind=item.edge.kind.value,
        edge_key=item.edge.edge_key,
        host_key=item.edge.host_key,
        is_foreign_security_principal=item.edge.is_foreign_security_principal,
        first_observed_at=item.first_observed_at,
        last_observed_at=item.last_observed_at,
        last_observed_run_id=item.last_observed_run_id,
    )


def _effective_view(node: ResolvedNode) -> EffectiveMemberView:
    summary = principal_summary(node.key, node.principal)
    if node.principal is None and node.reported_kind is not None:
        # The edge said what the member is even though nothing has described it. Reporting
        # that beats reporting nothing, as long as `resolved` still says it is second-hand.
        summary = summary.model_copy(
            update={"kind": node.reported_kind.value, "is_group": node.is_group}
        )
    return EffectiveMemberView(
        principal=summary,
        depth=node.depth,
        path=list(node.path),
        edge_kinds=[kind.value for kind in node.edge_kinds],
        via_foreign_security_principal=node.via_foreign_security_principal,
    )


def _path_view(path: MembershipPath, result: PathResult) -> MembershipPathView:
    return MembershipPathView(
        nodes=list(path.nodes),
        principals=[principal_summary(node, result.labels.get(node)) for node in path.nodes],
        edge_kinds=[kind.value for kind in path.edge_kinds],
        length=path.length,
    )


def _cycle_view(cycle: GraphCycle) -> CycleView:
    return CycleView(
        members=list(cycle.members), representative_path=list(cycle.representative_path)
    )


def _limits_view(limits: TraversalLimits) -> LimitsView:
    return LimitsView(
        max_depth=limits.max_depth,
        max_nodes=limits.max_nodes,
        max_edges=limits.max_edges,
        max_paths=limits.max_paths,
    )


def _traversal_view(result: EffectiveMembership, limits: TraversalLimits) -> TraversalView:
    return TraversalView(
        complete=result.complete,
        truncation=[reason.value for reason in result.truncation],
        limits=_limits_view(limits),
        depth_reached=result.depth_reached,
        nodes_visited=result.nodes_visited,
        edges_read=result.edges_read,
    )


def _slice(
    nodes: list[ResolvedNode], limit: int | None, cursor: str | None
) -> tuple[list[ResolvedNode], PageInfo]:
    """Page a recursive result.

    Offset-based, because the traversal produces the whole bounded set at once and there is
    no index to seek into. The traversal is re-run for each page, so a membership change
    between pages can shift the result — which is why the response's ``traversal`` block
    travels with every page rather than only the first.
    """
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    window = nodes[offset : offset + page_size]
    has_more = offset + page_size < len(nodes)
    return window, PageInfo(
        limit=page_size,
        has_more=has_more,
        next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
        total=len(nodes),
    )
