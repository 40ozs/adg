r"""Directory access: where one principal's reach begins inside the shares, and where it changes.

Four views of one computation, under ``/api/v1/access/principals/{identifier}/directory-access``:

* **the summary** (no suffix) — one row per *access root*: a reachable directory whose parent
  is not reachable. Each row carries how many reachable directories lie beneath it without
  interruption, how many *exceptions* sit inside that run (rights widen or narrow, certainty
  changes, inheritance is broken, access stops), and how the effective rights are spread;
* **the tree** (``/tree?parent=``) — one level of the directory tree at a time, from the
  share roots down, offering only what leads to reachable directories or marks where access
  stops. Lazy by construction: the client asks for the children of the branch it opens;
* **the exceptions of one root** (``/exceptions?root=``) — fetched when a reader asks;
* **the export** (``/export?content=summary|paths``) — CSV. ``summary`` is the roots;
  ``paths`` is every directory the engine evaluated, reachable or not, which is the complete
  set the paged ``/resources`` listing walks through. The summary never replaces it.

**Every verdict is the access engine's** (ADR-0010), computed by the same token, candidate
rule and evaluation as ``/principals/{identifier}/resources``. The summary groups verdicts;
it never evaluates an ACL. :mod:`app.services.directory_summary` states the grouping rules,
and in particular what a summary row does *not* claim: "184 reachable descendants" is not
"184 directories with identical permissions" — the exceptions say where they differ.

Directories are addressed by query parameter, never path segment: a UNC key does not
survive the frontend's proxy as a path segment (``frontend/lib/api/proxy.ts``).

**Cost.** The summary evaluates every candidate at once, bounded (``complete: false`` with a
named reason beyond the bound). The four routes share one computation per collected state,
held in a small per-application cache keyed by the collection basis — the validator the
``ETag`` carries — so expanding a branch after the summary costs one basis read and a
slice. Conditional GET answers ``304`` while nothing has been collected.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Sequence
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from app.access_engine import (
    AccessCertainty,
    AccessOutcome,
    AccessPath,
    LimitingLayer,
    TokenAssumption,
)
from app.api.access import (
    AssumptionQuery,
    HostQuery,
    IdentifierPath,
    IfNoneMatchHeader,
    RightsView,
    TokenView,
    render_rights,
    render_token,
)
from app.api.caching import BasisView, CacheValidator, basis_view, not_modified, validator_for
from app.api.deps import Session, TraversalBounds
from app.api.graph import PrincipalSummary, principal_summary, resolve_principal
from app.api.pagination import (
    MAX_LIMIT,
    PageInfo,
    decode_offset_cursor,
    encode_offset_cursor,
    normalize_limit,
)
from app.domain import DomainValidationError, TraversalLimits, parse_unc_path
from app.repositories import MembershipRepository, ResourceRepository
from app.repositories.basis import CollectionBasisRepository
from app.services.directory_access import (
    DirectoryAccess,
    DirectoryAccessCache,
    DirectoryAccessService,
)
from app.services.directory_summary import (
    AccessException,
    AccessRoot,
    DirectoryFacts,
    DirectoryVerdict,
    ExceptionReason,
    RootParent,
    TreeNode,
)

router = APIRouter(prefix="/api/v1/access", tags=["access"])

__all__ = ["REASON_LABELS", "router"]

#: Administrative wording for each exception reason. The codes are the contract; these are
#: what a screen or a spreadsheet shows.
REASON_LABELS: dict[ExceptionReason, str] = {
    ExceptionReason.RIGHTS_WIDEN: "Wider rights",
    ExceptionReason.RIGHTS_NARROW: "Narrower rights",
    ExceptionReason.RIGHTS_DIFFER: "Different rights",
    ExceptionReason.CERTAINTY_CHANGE: "Certainty changes",
    ExceptionReason.INHERITANCE_BROKEN: "Inheritance broken",
    ExceptionReason.ACCESS_DENIED: "Denied",
    ExceptionReason.ACCESS_ENDS: "Access ends",
    ExceptionReason.ACCESS_UNKNOWN: "Not established",
    ExceptionReason.OWNER_ONLY: "Owner rights only",
    ExceptionReason.NOT_OBSERVED: "Not scanned",
}

PARENT_LABELS: dict[RootParent, str] = {
    RootParent.SHARE_ROOT: "Share root",
    RootParent.NO_ACCESS: "Below a folder without access",
    RootParent.NOT_OBSERVED: "Below a folder no scan read",
}

_CACHE_ATTRIBUTE = "directory_access_cache"


# ----------------------------------------------------------------- representations


class ReasonView(BaseModel):
    reason: ExceptionReason
    label: str


class ReasonCountView(ReasonView):
    count: int


class DirectoryView(BaseModel):
    """Where one directory sits, and what its descriptor says about inheritance.

    ``observed: false`` is an ancestor no run read, present only so the tree connects; its
    descriptor fields are then null — unknown, not false.
    """

    key: str = Field(description="The canonical, case-folded UNC path. The identity.")
    path: str = Field(description="The UNC path as collected, for display.")
    name: str = Field(description="The last path segment.")
    server: str
    share: str
    share_key: str
    relative_path: str = Field(description="The path below the share root; empty at the root.")
    observed: bool
    is_acl_boundary: bool | None = None
    boundary_reason: str | None = None
    dacl_protected: bool | None = Field(
        default=None, description="True when the DACL refuses inherited entries."
    )


class DirectoryVerdictView(BaseModel):
    """The engine's answer for one directory. ``rights.mask`` is the answer."""

    access: bool
    outcome: AccessOutcome
    rights: RightsView
    certainty: AccessCertainty
    limiting_layer: LimitingLayer
    conditions: list[str] = Field(default_factory=list)


class RightsShareView(BaseModel):
    rights: RightsView
    directories: int


class AccessRootView(BaseModel):
    """One access root: where an uninterrupted run of reachable directories begins."""

    directory: DirectoryView
    verdict: DirectoryVerdictView
    reachable_descendants: int = Field(
        description=(
            "Reachable directories beneath the root, each reached through reachable parents. "
            "Not a claim that they share the root's rights: see exception_count."
        )
    )
    exception_count: int = Field(
        description="Places inside the run where the answer changes, or where access stops."
    )
    uniform: bool = Field(description="True only when exception_count is zero.")
    reasons: list[ReasonCountView] = Field(default_factory=list)
    rights_breakdown: list[RightsShareView] = Field(
        default_factory=list,
        description="Every effective mask held across the root and its descendants.",
    )
    starts_because: RootParent
    starts_because_label: str
    resumes_within: str | None = Field(
        default=None,
        description="When access resumes below an inaccessible folder inside another root's "
        "run, that root's key.",
    )
    resumes_below: str | None = Field(
        default=None, description="The inaccessible folder access resumes below."
    )


class DirectoryAccessTotals(BaseModel):
    reachable_directories: int = Field(
        description="Every directory the engine found reachable. Equals the sum over all "
        "roots of 1 + reachable_descendants."
    )
    roots: int
    exceptions: int
    shares: int = Field(description="Shares holding at least one reachable directory.")
    candidates_evaluated: int = Field(
        description="Paths whose ACL names a token SID or has no DACL, each evaluated. The "
        "set the All paths listing pages through."
    )
    unreachable_named: int = Field(
        description="Evaluated directories that turned out unreachable: named, holding nothing."
    )
    observed_directories: int = Field(
        description="Directories read in the shares concerned, whether reachable or not."
    )
    files_excluded: int = Field(
        description="Reachable files. The summary is about directories; files stay in the "
        "full-detail export and the All paths listing."
    )


class DirectoryAccessResponse(BaseModel):
    """One principal's directory access, summarized into access roots."""

    basis: BasisView
    subject: PrincipalSummary
    access_path: str
    token: TokenView
    complete: bool = Field(
        description="False when a bound was reached; the summary then covers what was read."
    )
    truncation: list[str] = Field(default_factory=list)
    totals: DirectoryAccessTotals
    access_levels: list[str] = Field(
        default_factory=list,
        description="The effective-access labels the roots carry, for filtering, most common "
        "first.",
    )
    roots: list[AccessRootView] = Field(default_factory=list)
    matched: int = Field(description="Roots matching the filters, across all pages.")
    page: PageInfo


class AccessExceptionView(BaseModel):
    """One place inside a root's run where the answer changes or access stops."""

    directory: DirectoryView
    reasons: list[ReasonView]
    reachable: bool
    named: bool = Field(
        description="False when nothing on this ACL names the principal or its groups. The "
        "engine still evaluated it (verdict); it is null only for a folder no scan read."
    )
    verdict: DirectoryVerdictView | None = None
    parent_key: str
    parent_verdict: DirectoryVerdictView


class DirectoryExceptionsResponse(BaseModel):
    basis: BasisView
    subject: PrincipalSummary
    root: AccessRootView
    items: list[AccessExceptionView] = Field(default_factory=list)
    page: PageInfo


class DirectoryTreeNodeView(BaseModel):
    """One directory in the drill-down, with what lies beneath it."""

    directory: DirectoryView
    reachable: bool
    named: bool
    verdict: DirectoryVerdictView | None = None
    is_root: bool = Field(description="This directory starts a run in the summary.")
    root_key: str | None = Field(
        default=None, description="The root whose run this directory belongs to."
    )
    reasons: list[ReasonView] = Field(
        default_factory=list, description="How this directory differs from its parent."
    )
    reachable_below: int
    child_count: int = Field(description="Children this tree offers when expanded.")
    hidden_child_count: int = Field(
        description="Children a scan read that are left out: unreachable, below a folder "
        "without access, with nothing reachable beneath them."
    )


class DirectoryTreeResponse(BaseModel):
    basis: BasisView
    subject: PrincipalSummary
    complete: bool
    truncation: list[str] = Field(default_factory=list)
    parent: DirectoryTreeNodeView | None = Field(
        default=None, description="The directory expanded; null at the top level."
    )
    children: list[DirectoryTreeNodeView] = Field(default_factory=list)
    page: PageInfo


# --------------------------------------------------------------------- parameters

ResourceKeyQuery = Annotated[
    str,
    Query(
        min_length=3,
        max_length=4096,
        description=r"A directory's UNC path, for example \\FS01\Finance\Budgets.",
    ),
]
OptionalResourceKeyQuery = Annotated[
    str | None,
    Query(
        max_length=4096,
        description="The directory whose children to list. Omit for the share roots.",
    ),
]
FilterQuery = Annotated[
    str | None,
    Query(
        max_length=256,
        description="Case-insensitive text the root's path (server, share or folder) contains.",
    ),
]
AccessLevelQuery = Annotated[
    str | None,
    Query(max_length=64, description="Only roots whose effective access carries this label."),
]
ShareQuery = Annotated[
    str | None,
    Query(max_length=512, description="Only roots inside this share, by share key (fs01|finance)."),
]
SummaryLimitQuery = Annotated[
    int | None, Query(ge=1, le=MAX_LIMIT, description="Items per page (default 100).")
]
OffsetCursorQuery = Annotated[
    str | None, Query(description="Opaque cursor from a previous response's next_cursor.")
]
ExportContentQuery = Annotated[
    Literal["summary", "paths"],
    Query(
        description="summary: one row per access root. paths: every evaluated directory, "
        "reachable or not — the complete underlying set."
    ),
]


# ---------------------------------------------------------------------- endpoints

_COMMON_RESPONSES: dict[int | str, dict[str, str]] = {
    304: {"description": "Nothing has been collected since the ETag was issued."},
    404: {"description": "Nothing is stored about this principal."},
    409: {"description": "A bare SID matched several host-scoped principals."},
}


@router.get(
    "/principals/{identifier}/directory-access",
    response_model=DirectoryAccessResponse,
    summary="Where one principal can reach inside the shares, as access roots",
    responses=_COMMON_RESPONSES,
)
async def directory_access(
    identifier: IdentifierPath,
    request: Request,
    response: Response,
    session: Session,
    limits: TraversalBounds,
    host: HostQuery = None,
    assumption: AssumptionQuery = None,
    q: FilterQuery = None,
    access: AccessLevelQuery = None,
    share: ShareQuery = None,
    limit: SummaryLimitQuery = None,
    cursor: OffsetCursorQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> DirectoryAccessResponse | Response:
    """The summary: access roots, each with its reachable descendants and exceptions.

    Filters apply on the server, across every root, before paging; ``matched`` counts what
    they kept. Without filters the roots come in share, then path, order.
    """
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    computed = await _computed(
        request,
        session,
        limits,
        identifier=identifier,
        host=host,
        assumption=assumption,
        view="summary",
        view_parameters={
            "q": q,
            "access": access,
            "share": share,
            "limit": page_size,
            "offset": offset,
        },
        if_none_match=if_none_match,
    )
    if isinstance(computed, Response):
        return computed
    model, validator = computed

    summary = model.summary
    labels = {root.key: render_rights(root.verdict.rights).primary for root in summary.roots}
    level_counts = Counter(labels.values())
    needle = q.strip().casefold() if q else ""
    wanted_share = share.strip().casefold() if share else None
    matching = [
        root
        for root in summary.roots
        if (not needle or needle in root.key)
        and (access is None or labels[root.key] == access)
        and (wanted_share is None or root.facts.share_key == wanted_share)
    ]
    visible = matching[offset : offset + page_size]
    has_more = offset + page_size < len(matching)

    validator.apply(response)
    return DirectoryAccessResponse(
        basis=basis_view(validator.basis),
        subject=principal_summary(model.subject_key, model.subject),
        access_path=model.path.value,
        token=render_token(model.token, model.principals),
        complete=model.complete,
        truncation=model.truncation,
        totals=DirectoryAccessTotals(
            reachable_directories=summary.reachable_directories,
            roots=len(summary.roots),
            exceptions=summary.exception_total,
            shares=len(summary.shares),
            candidates_evaluated=model.candidates_evaluated,
            unreachable_named=summary.unreachable_named,
            observed_directories=summary.observed_directories,
            files_excluded=model.files_excluded,
        ),
        access_levels=[
            label for label, _ in sorted(level_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        roots=[_root_view(root) for root in visible],
        matched=len(matching),
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
            total=len(matching),
        ),
    )


@router.get(
    "/principals/{identifier}/directory-access/tree",
    response_model=DirectoryTreeResponse,
    summary="One level of the directory-access tree",
    responses={
        **_COMMON_RESPONSES,
        422: {"description": "The parent is not a UNC path, or the tree does not offer it."},
    },
)
async def directory_access_tree(
    identifier: IdentifierPath,
    request: Request,
    response: Response,
    session: Session,
    limits: TraversalBounds,
    host: HostQuery = None,
    assumption: AssumptionQuery = None,
    parent: OptionalResourceKeyQuery = None,
    limit: SummaryLimitQuery = None,
    cursor: OffsetCursorQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> DirectoryTreeResponse | Response:
    """The children of one directory, or the share roots when ``parent`` is omitted.

    One level per request, so a client renders the tree as the reader opens it and never
    holds the file system. A directory the tree does not offer (it leads nowhere reachable)
    is refused rather than answered with an empty list that would read as "nothing here".
    """
    parent_key = _canonical(parent) if parent else None
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    computed = await _computed(
        request,
        session,
        limits,
        identifier=identifier,
        host=host,
        assumption=assumption,
        view="tree",
        view_parameters={"parent": parent_key, "limit": page_size, "offset": offset},
        if_none_match=if_none_match,
    )
    if isinstance(computed, Response):
        return computed
    model, validator = computed

    summary = model.summary
    parent_node: TreeNode | None = None
    if parent_key is not None:
        parent_node = summary.node(parent_key)
        if parent_node is None:
            raise _invalid(
                f"{parent} is not in this principal's directory-access tree: nothing beneath it "
                "is reachable and access does not stop at it.",
                field="parent",
            )
    children = summary.children(parent_key)
    visible = children[offset : offset + page_size]
    has_more = offset + page_size < len(children)

    validator.apply(response)
    return DirectoryTreeResponse(
        basis=basis_view(validator.basis),
        subject=principal_summary(model.subject_key, model.subject),
        complete=model.complete,
        truncation=model.truncation,
        parent=_node_view(parent_node) if parent_node is not None else None,
        children=[_node_view(node) for node in visible],
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
            total=len(children),
        ),
    )


@router.get(
    "/principals/{identifier}/directory-access/exceptions",
    response_model=DirectoryExceptionsResponse,
    summary="Where the answer changes beneath one access root",
    responses={
        **_COMMON_RESPONSES,
        422: {"description": "The root is not a UNC path, or not an access root."},
    },
)
async def directory_access_exceptions(
    identifier: IdentifierPath,
    request: Request,
    response: Response,
    session: Session,
    limits: TraversalBounds,
    root: ResourceKeyQuery,
    host: HostQuery = None,
    assumption: AssumptionQuery = None,
    limit: SummaryLimitQuery = None,
    cursor: OffsetCursorQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> DirectoryExceptionsResponse | Response:
    """Every exception inside one root's run, in path order: path, answer, reasons, parent."""
    root_key = _canonical(root)
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    computed = await _computed(
        request,
        session,
        limits,
        identifier=identifier,
        host=host,
        assumption=assumption,
        view="exceptions",
        view_parameters={"root": root_key, "limit": page_size, "offset": offset},
        if_none_match=if_none_match,
    )
    if isinstance(computed, Response):
        return computed
    model, validator = computed

    found = model.summary.root(root_key)
    if found is None:
        raise _invalid(f"{root} is not one of this principal's access roots.", field="root")
    items = model.summary.exceptions.get(found.key, ())
    visible = items[offset : offset + page_size]
    has_more = offset + page_size < len(items)

    validator.apply(response)
    return DirectoryExceptionsResponse(
        basis=basis_view(validator.basis),
        subject=principal_summary(model.subject_key, model.subject),
        root=_root_view(found),
        items=[_exception_view(item) for item in visible],
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
            total=len(items),
        ),
    )


@router.get(
    "/principals/{identifier}/directory-access/export",
    summary="Directory access as CSV: the roots, or every evaluated directory",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {}}, "description": "The CSV document."},
        404: {"description": "Nothing is stored about this principal."},
        409: {"description": "A bare SID matched several host-scoped principals."},
    },
)
async def directory_access_export(
    identifier: IdentifierPath,
    request: Request,
    session: Session,
    limits: TraversalBounds,
    content: ExportContentQuery = "summary",
    host: HostQuery = None,
    assumption: AssumptionQuery = None,
) -> Response:
    """The summary's roots, or the complete evaluated set, as a spreadsheet can open it.

    ``paths`` carries every candidate the engine evaluated — reachable or not, file or
    directory — with its verdict and, for a directory, the root whose run it belongs to and
    why it is an exception. Nothing the summary groups away is missing from it. A partial
    computation is named in the file name and in ``X-ADG-Complete``.
    """
    computed = await _computed(
        request,
        session,
        limits,
        identifier=identifier,
        host=host,
        assumption=assumption,
        view="export",
        view_parameters={"content": content},
        if_none_match=None,
    )
    if isinstance(computed, Response):  # no If-None-Match is passed, so never a 304
        return computed
    model, validator = computed
    body = _summary_csv(model) if content == "summary" else _paths_csv(model)
    stem = f"directory-access-{content}-{model.subject.sid if model.subject else 'principal'}"
    suffix = "" if model.complete else "-partial"
    headers = {
        "Content-Disposition": f'attachment; filename="{_safe_filename(stem)}{suffix}.csv"',
        "X-ADG-Complete": "true" if model.complete else "false",
    }
    result = Response(content=body, media_type="text/csv; charset=utf-8", headers=headers)
    validator.apply(result)
    return result


# ------------------------------------------------------------------ the computation


async def _computed(
    request: Request,
    session: Session,
    limits: TraversalLimits,
    *,
    identifier: str,
    host: str | None,
    assumption: TokenAssumption | None,
    view: str,
    view_parameters: dict[str, object],
    if_none_match: str | None,
) -> tuple[DirectoryAccess, CacheValidator] | Response:
    """The shared computation for this principal and collected state, and the view's ETag.

    Two validators from one basis read: the *model's*, which keys the cache and is the same
    for all four views, and the *view's*, which names this view's own parameters and is what
    the response carries. Changing a filter or a tree parent changes the ETag without
    discarding the computation.
    """
    basis = await CollectionBasisRepository(session).current()
    common = {
        "identifier": identifier,
        "host": host,
        "access_path": AccessPath.REMOTE_SMB,
        "assumption": assumption,
        "max_depth": limits.max_depth,
        "max_nodes": limits.max_nodes,
        "max_edges": limits.max_edges,
        "max_paths": limits.max_paths,
    }
    model_validator = validator_for(basis, route="access.directory_access", parameters=common)
    view_validator = validator_for(
        basis,
        route=f"access.directory_access.{view}",
        parameters={**common, **view_parameters},
    )
    if view_validator.matches(if_none_match):
        return not_modified(view_validator)

    cache = _cache(request)
    model = cache.get(model_validator.etag)
    if model is None:
        membership = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
        key, _ = await resolve_principal(membership, identifier, host)
        service = DirectoryAccessService(ResourceRepository(session), membership)
        model = await service.directory_access(
            key, path=AccessPath.REMOTE_SMB, limits=limits, assumption=assumption
        )
        cache.put(model_validator.etag, model)
    return model, view_validator


def _cache(request: Request) -> DirectoryAccessCache:
    state = request.app.state
    cache = getattr(state, _CACHE_ATTRIBUTE, None)
    if cache is None:
        cache = DirectoryAccessCache()
        setattr(state, _CACHE_ATTRIBUTE, cache)
    return cache


# ------------------------------------------------------------------ rendering


def _directory_view(facts: DirectoryFacts) -> DirectoryView:
    unc = parse_unc_path(facts.path)
    return DirectoryView(
        key=facts.key,
        path=unc.value,
        name=facts.name,
        server=unc.server,
        share=unc.share,
        share_key=facts.share_key,
        relative_path=unc.relative_path,
        observed=facts.observed,
        is_acl_boundary=facts.is_acl_boundary,
        boundary_reason=facts.boundary_reason,
        dacl_protected=facts.dacl_protected,
    )


def _verdict_view(verdict: DirectoryVerdict) -> DirectoryVerdictView:
    return DirectoryVerdictView(
        access=verdict.outcome is AccessOutcome.GRANTED,
        outcome=verdict.outcome,
        rights=render_rights(verdict.rights),
        certainty=verdict.certainty,
        limiting_layer=verdict.limiting_layer,
        conditions=list(verdict.conditions),
    )


def _reasons(reasons: Sequence[ExceptionReason]) -> list[ReasonView]:
    return [ReasonView(reason=reason, label=REASON_LABELS[reason]) for reason in reasons]


def _root_view(root: AccessRoot) -> AccessRootView:
    return AccessRootView(
        directory=_directory_view(root.facts),
        verdict=_verdict_view(root.verdict),
        reachable_descendants=root.reachable_descendants,
        exception_count=root.exception_count,
        uniform=root.uniform,
        reasons=[
            ReasonCountView(reason=reason, label=REASON_LABELS[reason], count=count)
            for reason, count in root.reason_counts
        ],
        rights_breakdown=[
            RightsShareView(rights=render_rights(rights), directories=count)
            for rights, count in root.rights_breakdown
        ],
        starts_because=root.parent,
        starts_because_label=PARENT_LABELS[root.parent],
        resumes_within=root.resumes_within,
        resumes_below=root.resumes_below,
    )


def _exception_view(item: AccessException) -> AccessExceptionView:
    return AccessExceptionView(
        directory=_directory_view(item.facts),
        reasons=_reasons(item.reasons),
        reachable=item.reachable,
        named=item.verdict is not None and item.verdict.named,
        verdict=_verdict_view(item.verdict) if item.verdict is not None else None,
        parent_key=item.parent_key,
        parent_verdict=_verdict_view(item.parent_verdict),
    )


def _node_view(node: TreeNode) -> DirectoryTreeNodeView:
    return DirectoryTreeNodeView(
        directory=_directory_view(node.facts),
        reachable=node.reachable,
        named=node.verdict is not None and node.verdict.named,
        verdict=_verdict_view(node.verdict) if node.verdict is not None else None,
        is_root=node.is_root,
        root_key=node.root_key,
        reasons=_reasons(node.reasons),
        reachable_below=node.reachable_below,
        child_count=node.child_count,
        hidden_child_count=node.hidden_child_count,
    )


# ------------------------------------------------------------------ CSV


def _summary_csv(model: DirectoryAccess) -> str:
    rows: list[list[object]] = [
        [
            "server",
            "share",
            "root_path",
            "effective_access",
            "rights_mask",
            "certainty",
            "reachable_descendants",
            "exceptions",
            "uniform",
            "exception_reasons",
            "rights_breakdown",
            "starts_because",
            "resumes_within",
        ]
    ]
    for root in model.summary.roots:
        unc = parse_unc_path(root.facts.path)
        rights = render_rights(root.verdict.rights)
        rows.append(
            [
                unc.server,
                unc.share,
                unc.value,
                rights.label,
                rights.mask,
                root.verdict.certainty.value,
                root.reachable_descendants,
                root.exception_count,
                "yes" if root.uniform else "no",
                "; ".join(
                    f"{REASON_LABELS[reason]}: {count}" for reason, count in root.reason_counts
                ),
                "; ".join(
                    f"{render_rights(mask).label}: {count}" for mask, count in root.rights_breakdown
                ),
                PARENT_LABELS[root.parent],
                root.resumes_within or "",
            ]
        )
    return _csv(rows)


def _paths_csv(model: DirectoryAccess) -> str:
    rows: list[list[object]] = [
        [
            "path",
            "share_key",
            "kind",
            "observed",
            "reachable",
            "outcome",
            "effective_access",
            "rights_mask",
            "certainty",
            "limiting_layer",
            "conditions",
            "acl_boundary",
            "boundary_reason",
            "inheritance_protected",
            "summary_root",
            "exception_reasons",
        ]
    ]
    for item in sorted(model.paths, key=lambda entry: entry.key):
        node = model.summary.node(item.key)
        rights = render_rights(item.verdict.rights)
        rows.append(
            [
                item.path,
                item.share_key or "",
                item.resource_kind or "",
                _yes_no(item.observed),
                _yes_no(item.verdict.reachable),
                item.verdict.outcome.value,
                rights.label,
                rights.mask,
                item.verdict.certainty.value,
                item.verdict.limiting_layer.value,
                "; ".join(item.verdict.conditions),
                _yes_no(item.is_acl_boundary),
                item.boundary_reason or "",
                _yes_no(item.dacl_protected),
                (node.root_key or "") if node is not None else "",
                "; ".join(REASON_LABELS[reason] for reason in node.reasons) if node else "",
            ]
        )
    return _csv(rows)


def _csv(rows: list[list[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue()


def _cell(value: object) -> str:
    """A CSV cell that a spreadsheet will not execute.

    A cell opening with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return is a formula
    to most spreadsheets. Folder names are chosen by whoever can create folders, so a
    leading apostrophe neutralizes them; it is the convention spreadsheets themselves use.
    """
    text = str(value)
    return f"'{text}" if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


def _yes_no(value: bool | None) -> str:
    return "" if value is None else ("yes" if value else "no")


def _safe_filename(stem: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "_" for character in stem
    )


def _canonical(path: str) -> str:
    """A directory parameter's identity. A malformed path is the parser's 422."""
    return parse_unc_path(path).comparison_key


def _invalid(message: str, *, field: str) -> DomainValidationError:
    return DomainValidationError(message, field=field)
