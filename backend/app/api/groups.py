r"""What one group actually reaches — the blast radius of a membership.

The access routes answer *"can this principal reach that directory?"*. This one answers the
question an administrator asks before touching a group at all: **if I put somebody in here,
what have I given them, and how many people are already in it?**

It is a listing, and its shape is dictated by two constraints that pull in opposite
directions.

**It must not loop the explanation engine over an estate.** Phase 5A's own prerequisites say
so, and the reason is arithmetic rather than taste: an explanation costs one bounded
resolution per pair, which is right for one pair and ruinous for a page of a hundred against
a group that nests forty deep. So each row here is an *answer* — the effective rights, the
limiting layer, the entries that produced it — and not a derivation. A row that needs one
carries ``explain`` with the URL that produces it for that pair alone.

**It must still be enough to act on.** A list of paths with no rights beside them tells an
administrator nothing; the whole point is to see that this group confers Modify on five
finance directories and Full Control on one nobody remembered. So the per-row answer is the
real one, from the same resolver the singular route uses, over a token built from the
group's own upward closure — which is exactly what a member of it inherits.

The membership count sits at the top for the same reason. Rights times population is impact;
rights alone is a fact about an ACL.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.access_engine import AccessPath, TokenAssumption
from app.api.access import (
    AppliedAceView,
    AssumptionQuery,
    CursorQuery,
    HostQuery,
    IdentifierPath,
    IfNoneMatchHeader,
    LimitQuery,
    PathQuery,
    ResourceRef,
    RightsView,
    ShareRef,
    TokenView,
    VerdictView,
    render_applied_ace,
    render_resource_ref,
    render_rights,
    render_share_ref,
    render_token,
    render_verdict,
)
from app.api.caching import CONTRACT_VERSION, BasisView, basis_view, current_validator, not_modified
from app.api.deps import Session, TraversalBounds
from app.api.graph import PrincipalSummary, principal_summary, resolve_principal
from app.api.pagination import (
    PageInfo,
    decode_keyset_cursor,
    encode_keyset_cursor,
    normalize_limit,
)
from app.repositories import MembershipRepository, PrincipalRecord, ResourceRepository
from app.services.access import AccessService, ResolvedAccess
from app.services.graph import GraphService

router = APIRouter(prefix="/api/v1/groups", tags=["access"])

__all__ = ["router"]


SharesQuery = Annotated[
    bool,
    Query(
        alias="shares",
        description=(
            "False (the default) lists the directories this group reaches. True lists the "
            "shares instead, each crossed with the ACL of the directory it publishes. Refused "
            "with access_path=local: local access does not pass through a share ACL."
        ),
    ),
]


class MembershipImpactView(BaseModel):
    """How many principals a change to this group would affect.

    Bounded, and honest about it. ``complete: false`` means the downward traversal hit a
    limit, so the counts are lower bounds and the real blast radius is larger — never
    smaller.
    """

    effective_members: int = Field(
        description="Principals reachable downward through nesting, the group itself excluded."
    )
    non_group_members: int = Field(
        description="Of those, the ones that are not themselves groups: the accounts that "
        "would actually hold the rights listed here. Unclassified principals are counted, "
        "because an orphaned SID inside a group is a finding and not a rounding error."
    )
    depth_reached: int
    complete: bool = Field(
        description="False means the traversal was truncated and both counts are lower bounds."
    )
    cycles: list[list[str]] = Field(
        default_factory=list,
        description="Membership cycles found below this group. A cycle is a finding.",
    )


class ResourceImpactView(BaseModel):
    """One resource this group's membership reaches, and what it confers there."""

    resource: ResourceRef
    share: ShareRef | None = None
    verdict: VerdictView = Field(
        description="What a member of this group gets here — and, when the answer is no "
        "rights, whether that is a Deny, an absence of any grant, or a gap in collection."
    )
    rights: RightsView = Field(description="The effective mask a member inherits.")
    limiting_layer: str = Field(description="Which ACL removed rights the other granted.")
    granted_by: list[AppliedAceView] = Field(
        default_factory=list,
        description="Entries that gave rights, both layers, in evaluation order.",
    )
    denied_by: list[AppliedAceView] = Field(
        default_factory=list, description="Entries that took rights away."
    )
    names_group: bool = Field(
        description=(
            "Whether any entry above names this group itself. False means the rights arrive "
            "some other way — through a group this one is nested inside, or through a world "
            "SID such as Everyone — and there is no entry naming this group to edit, so "
            "'remove the group from the ACL' is not an available fix on this resource."
        )
    )
    conditions: list[str] = Field(
        default_factory=list, description="Condition codes qualifying this row."
    )
    explain: str = Field(
        description="URL of the full derivation for this pair. Not inlined: an explanation "
        "costs a bounded resolution each, and a page of them is not a page a server should "
        "compute speculatively."
    )


class ResourceImpactResponse(BaseModel):
    """Everything one group reaches, one page at a time."""

    schema_version: str = Field(
        default=CONTRACT_VERSION, json_schema_extra={"const": CONTRACT_VERSION}
    )
    basis: BasisView
    group: PrincipalSummary
    access_path: str
    token: TokenView = Field(
        description="The SIDs a member of this group presents: the group, everything it is "
        "nested inside, and the SIDs Windows adds to every token of this kind. This is what "
        "makes a row appear even when the ACL names a parent group rather than this one."
    )
    membership: MembershipImpactView
    items: list[ResourceImpactView] = Field(default_factory=list)
    page: PageInfo


@router.get(
    "/{identifier}/resource-impact",
    response_model=ResourceImpactResponse,
    summary="Every resource one group's membership reaches, and what it confers",
    responses={
        304: {"description": "Nothing has been collected since the ETag was issued."},
        404: {"description": "Nothing is stored about this principal."},
        409: {"description": "A bare SID matched several host-scoped principals."},
        422: {"description": "A bad identifier, a cursor from elsewhere, or shares with local."},
    },
)
async def resource_impact(
    identifier: IdentifierPath,
    session: Session,
    limits: TraversalBounds,
    response: Response,
    host: HostQuery = None,
    access_path: PathQuery = AccessPath.REMOTE_SMB,
    assumption: AssumptionQuery = None,
    shares: SharesQuery = False,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> ResourceImpactResponse | Response:
    r"""What a member of this group can reach, with the entries that put it there.

    Accepts any principal, not only a group — a user's "impact" is its own access, which is
    a coherent answer and cheaper to allow than to special-case. When the subject *is* a
    group, the resolver records the ``subject_is_a_group`` condition on every row: a group
    holds no access token, and what is reported is the rights an authenticated member of it
    would have. That is the useful question and not the literal one, and the condition says
    so rather than letting the distinction go unremarked.

    **Every candidate is listed, including the ones that confer nothing.** A directory whose
    ACL names this group and whose share ACL withholds everything is precisely the row an
    administrator needs to see: the grant is there, it is doing nothing today, and it will
    start doing something the moment somebody widens the share. Filtering those out would
    also make ``has_more`` a claim about a different set than the one being paged.

    ``names_group`` is the field that decides where a fix goes. True means an entry names
    this group and can be edited. False means the rights arrive through something else — a
    group this one is nested inside, or a world SID such as ``Everyone`` — and there is no
    entry naming this group to remove, so a remediation written against one would change
    nothing.
    """
    page_size = normalize_limit(limit)
    validator = await current_validator(
        session,
        route="groups.resource_impact",
        parameters={
            "identifier": identifier,
            "host": host,
            "access_path": access_path,
            "assumption": assumption,
            "shares": shares,
            "limit": page_size,
            "cursor": cursor,
            "max_depth": limits.max_depth,
            "max_nodes": limits.max_nodes,
            "max_edges": limits.max_edges,
            "max_paths": limits.max_paths,
        },
    )
    if validator.matches(if_none_match):
        return not_modified(validator)

    after = decode_keyset_cursor(cursor)
    membership = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    key, record = await resolve_principal(membership, identifier, host)
    service = AccessService(ResourceRepository(session), membership)

    page = await service.accessible_resources(
        key,
        shares=shares,
        path=access_path,
        limits=limits,
        assumption=assumption,
        limit=page_size,
        after=after,
    )
    # A second repository, and therefore a second edge budget, so that counting who is in
    # the group cannot consume the budget the access answers above were computed under.
    downward = await GraphService(
        MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    ).effective_members(key, limits)

    validator.apply(response)
    return ResourceImpactResponse(
        basis=basis_view(validator.basis),
        group=principal_summary(key, record),
        access_path=access_path.value,
        token=render_token(page.token, page.principals),
        membership=MembershipImpactView(
            effective_members=sum(1 for node in downward.nodes if node.key != key),
            non_group_members=sum(
                1 for node in downward.nodes if node.key != key and node.is_group is not True
            ),
            depth_reached=downward.depth_reached,
            complete=downward.complete,
            cycles=[list(cycle.members) for cycle in downward.cycles],
        ),
        items=[
            _impact_row(
                item,
                page.principals,
                subject_key=key,
                explain=_explain_url(
                    item, identifier, host=host, access_path=access_path, assumption=assumption
                ),
            )
            for item in page.items
        ],
        page=PageInfo(
            limit=page_size,
            has_more=page.has_more,
            next_cursor=(
                encode_keyset_cursor(page.next_key)
                if page.has_more and page.next_key is not None
                else None
            ),
            total=None,
        ),
    )


# ------------------------------------------------------------------- renderers


def _impact_row(
    item: ResolvedAccess,
    labels: dict[str, PrincipalRecord],
    *,
    subject_key: str,
    explain: str,
) -> ResourceImpactView:
    """One resource, its answer, and the entries on both layers that produced it."""
    access = item.access
    granted = list(access.ntfs.granted_by)
    denied = list(access.ntfs.denied_by)
    if access.share is not None:
        granted.extend(access.share.granted_by)
        denied.extend(access.share.denied_by)

    return ResourceImpactView(
        resource=render_resource_ref(item),
        share=render_share_ref(item),
        verdict=render_verdict(access, labels),
        rights=render_rights(access.rights),
        limiting_layer=access.limiting_layer.value,
        granted_by=[render_applied_ace(applied, labels) for applied in granted],
        denied_by=[render_applied_ace(applied, labels) for applied in denied],
        # Compared by **key**, not by depth. Depth 0 is not the same as "names this
        # group": an assumed SID such as Everyone also sits at depth 0, and reading a
        # share's Everyone entry as "this group is named here" would send an administrator
        # looking for an entry that does not exist.
        names_group=any(applied.matched.key == subject_key for applied in granted + denied),
        conditions=[condition.value for condition in access.conditions],
        explain=explain,
    )


def _explain_url(
    item: ResolvedAccess,
    identifier: str,
    *,
    host: str | None,
    access_path: AccessPath,
    assumption: TokenAssumption | None,
) -> str:
    """The derivation for one row, as a URL the client can follow unchanged.

    Carries the same principal identifier, host scoping and access path this listing was
    computed under, so that following it cannot silently answer a different question than
    the row it came from.
    """
    query: dict[str, str] = {
        "principal": identifier,
        "resource": item.resource.path if item.resource is not None else item.access.resource_key,
        "access_path": access_path.value,
    }
    if host is not None:
        query["host"] = host
    if assumption is not None:
        query["assumption"] = assumption.value
    return f"/api/v1/access/explain?{urlencode(query)}"
