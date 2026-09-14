r"""Effective-access endpoints: what a principal can actually do, and why.

Everything under ``/api/v1/access`` is **derived**. The rest of the API reports what
collectors observed; these routes report a conclusion drawn from those observations, and
the difference is what shapes every response here:

* **A mask is the answer; a label is a rendering.** ``rights.mask`` is authoritative and
  ``rights.label`` is recomputed from it. A client that stores or compares the label is
  comparing strings, and SMB ``Change`` and NTFS ``Modify`` are the same bits under two
  names — the exact mistake the rights model exists to prevent.
* **Every answer carries its own qualifications.** ``certainty`` says in which direction
  the result may be wrong, and ``findings`` says why. ``access: false`` with
  ``certainty: "at_least"`` means *no access was established*, never *no access exists*,
  and a client that renders the two identically has thrown away the finding.
* **A listing says what it could not list.** ``/resources/{resource}/principals`` reports
  ``complete: false`` and names the trustees it could not enumerate — an ``Everyone`` ACE,
  a group whose membership nobody collected — rather than returning a short list that
  looks whole.

* **An empty answer says which emptiness it is.** ``verdict.outcome`` is one of four
  values, not a boolean: ``denied`` (a Deny withheld it), ``no_grant`` (nothing names this
  principal), ``indeterminate`` (not answerable from what has been collected), and
  ``granted``. ADR-0016. The information was always in ``access`` plus ``certainty`` plus
  the findings; what was missing was a single field a client gets right by doing nothing.

Both bounded listings page, and they page differently on purpose: the principals of one
resource are computed by traversal and then sliced (offset), while the resources of one
principal come straight out of an index (keyset). The distinction is the one
:mod:`app.api.pagination` already draws, for the same reasons.

The explanation has two spellings, and they are one computation. ``/explain`` is addressed
by query parameters — a UNC path does not have to survive being a URL *path* segment — and
is what a client renders; ``/paths/principals/{identifier}/resources/{resource}`` is Phase
5A's path-addressed form and is unchanged. Both go through ``_explain``, so they cannot
answer differently, including in how they fail.

Neither of those pages. Each answers about exactly one pair, so there is no population to
slice; what can grow is the *graph* — group nesting is combinatorial — so they are bounded
by explicit path and removal limits instead, report the limits applied, and say
``complete: false`` when either bit. A truncated explanation read as a whole one is a
conclusion drawn from a subset, which is the same error in a different shape.
``/paths`` slices that same enumeration for a client that wants a list, and reports
``complete`` and ``has_more`` separately: the last page of a truncated enumeration is not
the last of the paths.

``/explain``, ``/paths`` and the group resource-impact listing carry an ``ETag`` over the
**collection basis** — the state of every scan run — so a repeat request costs one query
and a ``304``, and an ingestion invalidates every cached answer. There is no
time-to-live anywhere. ADR-0017 and :mod:`app.api.caching`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import (
    DEFAULT_EXPLANATION_LIMITS,
    MAX_PATHS_CEILING,
    MAX_REMOVAL_TARGETS_CEILING,
    AccessCertainty,
    AccessExplanation,
    AccessFinding,
    AccessOutcome,
    AccessPath,
    AccessVerdict,
    AclEvaluation,
    AclProvenance,
    AppliedAce,
    CausalPath,
    EdgeKind,
    EffectiveAccess,
    ExplanationEdge,
    ExplanationGraph,
    ExplanationLimits,
    ExplanationNode,
    LimitingLayer,
    NodeKind,
    PathEffect,
    PathRelation,
    RemovalTarget,
    RightsMask,
    SubjectToken,
    TokenAssumption,
    TokenSid,
    category_display_name,
    classify_access,
    summarize,
)
from app.api.caching import (
    CONTRACT_VERSION,
    BasisView,
    basis_view,
    current_validator,
    not_modified,
)
from app.api.deps import PRINTABLE_IDENTIFIER, Session, TraversalBounds
from app.api.graph import PrincipalSummary, principal_summary, resolve_principal
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
from app.domain import DomainValidationError, TraversalLimits, parse_unc_path
from app.repositories import MembershipRepository, PrincipalRecord, ResourceRepository
from app.services.access import (
    AccessService,
    PrincipalAccess,
    ResolvedAccess,
    ResolvedExplanation,
    ResourceAccessPage,
    SubjectAccessPage,
)
from app.services.graph import MemberInclusion

router = APIRouter(prefix="/api/v1/access", tags=["access"])

__all__ = ["router"]


IdentifierPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=512,
        pattern=PRINTABLE_IDENTIFIER,
        description=(
            "A SID (S-1-5-21-...) or a host-scoped storage key (fs01|S-1-5-32-544). "
            "Use ?host= to disambiguate a BUILTIN SID seen on several computers."
        ),
    ),
]

ResourcePath = Annotated[
    str,
    Path(
        min_length=5,
        max_length=1024,
        pattern=PRINTABLE_IDENTIFIER,
        description=(
            "A directory's canonical UNC path (\\\\FS01\\Finance), percent-encoded. A share "
            "key is not accepted here: a share and the directory it publishes have "
            "different ACLs, and only one of them limits local access."
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

PathQuery = Annotated[
    AccessPath,
    Query(
        alias="access_path",
        description=(
            "How the principal reaches the data. 'remote_smb' applies the share ACL and the "
            "NTFS ACL and reports the intersection; 'local' applies only NTFS, which is what "
            "anyone who can log on to the server gets."
        ),
    ),
]

AssumptionQuery = Annotated[
    TokenAssumption | None,
    Query(
        description=(
            "Which SIDs to assume are in the subject's access token beyond its observed "
            "memberships. Defaults from the subject's kind; the response always reports "
            "what was used and what it added."
        ),
    ),
]

InclusionQuery = Annotated[
    MemberInclusion,
    Query(
        alias="members",
        description=(
            "Which reached principals to report. 'non_groups' (the default) keeps users, "
            "computers, well-known SIDs and anything ADG cannot classify; 'users' keeps "
            "only accounts; 'all' includes the nested groups themselves."
        ),
    ),
]

LimitQuery = Annotated[
    int | None,
    Query(ge=1, le=MAX_LIMIT, description=f"Items per page (default {DEFAULT_LIMIT})."),
]

CursorQuery = Annotated[
    str | None, Query(description="Opaque cursor from a previous response's next_cursor.")
]

PrincipalQuery = Annotated[
    str,
    Query(
        min_length=1,
        max_length=512,
        pattern=PRINTABLE_IDENTIFIER,
        description=(
            "The principal to explain, as a SID (S-1-5-21-...) or a host-scoped storage key "
            "(fs01|S-1-5-32-544). Required: an explanation is always about somebody, and a "
            "route that would answer without one asks for the whole estate."
        ),
    ),
]

ResourceExplainQuery = Annotated[
    str,
    Query(
        min_length=5,
        max_length=1024,
        pattern=PRINTABLE_IDENTIFIER,
        description=(
            "The directory's canonical UNC path (\\\\FS01\\Finance). Required, and given as a "
            "query parameter rather than a path segment so that a client does not have to "
            "percent-encode backslashes into a URL path — which several proxies normalize or "
            "reject. A share key is not accepted: a share and the directory it publishes have "
            "different ACLs."
        ),
    ),
]

IfNoneMatchHeader = Annotated[
    str | None,
    Header(
        alias="If-None-Match",
        description=(
            "An ETag from a previous response. The server answers 304 when nothing has been "
            "collected since, so a client may hold an explanation indefinitely and still "
            "never render a stale one."
        ),
    ),
]

MaxCausalPathsQuery = Annotated[
    int | None,
    Query(
        ge=1,
        le=MAX_PATHS_CEILING,
        description=(
            "Causal paths to enumerate across the whole explanation, both layers together. "
            "Distinct from max_paths, which bounds the chains to any one trustee. Clamped "
            "to the server ceiling; the response reports the limits used and sets "
            "complete=false when anything was cut."
        ),
    ),
]

MaxRemovalTargetsQuery = Annotated[
    int | None,
    Query(
        ge=1,
        le=MAX_REMOVAL_TARGETS_CEILING,
        description=(
            "Edges to measure by re-running the access check without them. Each costs one "
            "check over each ACL."
        ),
    ),
]


# --------------------------------------------------------------------- views


class RightsView(BaseModel):
    """A rights mask, with its rendering alongside it. The mask is the answer."""

    mask: str = Field(description="The access mask, as 0xNNNNNNNN. This is authoritative.")
    value: int = Field(description="The same mask as an integer, for arithmetic.")
    layer: str = Field(description="smb_share, ntfs, or effective. Masks never cross layers.")
    label: str = Field(
        description=(
            "Display text derived from the mask. Never store or compare it: SMB 'Read' and "
            "NTFS 'Read & Execute' are the same bits, and 'Modify' can hide WRITE_DAC."
        )
    )
    primary: str = Field(description="The strongest category the mask fully contains.")
    categories: list[str] = Field(
        default_factory=list, description="Every category the mask contains, strongest first."
    )
    is_exact: bool = Field(
        description="False when the mask carries rights the label does not name."
    )
    extra_rights: list[str] = Field(
        default_factory=list, description="Rights present beyond what the categories cover."
    )
    escalation_rights: list[str] = Field(
        default_factory=list,
        description="WRITE_DAC / WRITE_OWNER, when present. The holder can self-grant.",
    )
    unrecognized_bits: str | None = Field(
        default=None, description="Mask bits matching no right ADG knows, as 0xNNNNNNNN."
    )
    indeterminate: bool = Field(
        default=False,
        description="MAXIMUM_ALLOWED is present; Windows resolves it per open, so no fixed "
        "rights set exists.",
    )


class FindingView(BaseModel):
    """One thing the resolver assumed or could not settle."""

    condition: str
    message: str
    may_overstate: bool = Field(
        description="True when this gap means the rights could be wider than reported."
    )
    may_understate: bool = Field(
        description="True when this gap means the rights could be narrower than reported."
    )
    detail: dict[str, Any] = Field(default_factory=dict)


class TokenEntryView(BaseModel):
    """One SID in the constructed access token, and why it is there."""

    principal: PrincipalSummary
    origin: str = Field(
        description="subject, group_membership, well_known, or logon_type. The last two are "
        "assumptions, not observations."
    )
    assumed: bool
    depth: int = Field(description="Membership hops from the subject. 0 for the subject itself.")
    path: list[str] = Field(
        default_factory=list, description="The membership chain, subject first."
    )


class TokenView(BaseModel):
    """The SIDs the ACLs were evaluated against."""

    subject: PrincipalSummary
    assumption: str
    access_path: str
    membership_complete: bool = Field(
        description="False means the traversal hit a limit and the token is a lower bound."
    )
    entries: list[TokenEntryView] = Field(default_factory=list)


class AppliedAceView(BaseModel):
    """One ACE that matched the token, and what it contributed where it sits."""

    ace_key: str | None = None
    layer: str = Field(
        description="Which ACL this entry sits on: smb_share or ntfs. Carried on the entry "
        "itself so that a list drawn from both layers stays unambiguous — SMB 'Change' and "
        "NTFS 'Modify' are the same bits under two names."
    )
    position: int = Field(description="Index in the DACL as stored. Order is load-bearing.")
    trustee: PrincipalSummary
    ace_type: str
    access_mask: str = Field(description="The entry's own mask, generic bits expanded.")
    contributed: str = Field(
        description=(
            "The part of the mask that survived what earlier entries had already settled. "
            "0x00000000 means the entry is on the ACL and does nothing."
        )
    )
    flags: int
    source: str = Field(description="explicit or inherited.")
    inherited_from: str | None = None
    matched_key: str = Field(description="The token entry this ACE named.")
    via_group: bool


class AclEvaluationView(BaseModel):
    """What one layer's ACL grants this token."""

    layer: str
    rights: RightsView
    granted_by: list[AppliedAceView] = Field(default_factory=list)
    denied_by: list[AppliedAceView] = Field(default_factory=list)
    superseded: list[AppliedAceView] = Field(
        default_factory=list,
        description="Entries that matched and contributed nothing because an earlier entry "
        "had already settled every right they name.",
    )
    owner_rights: RightsView | None = Field(
        default=None,
        description="Rights held by owning the object, outside the DACL. WRITE_DAC here is "
        "an escalation path no ACE shows.",
    )
    canonical_rights: RightsView | None = Field(
        default=None, description="What the canonical-ACL model would compute, for comparison."
    )
    order_dependent: bool = Field(
        default=False,
        description="True when the stored order changed this subject's answer: an Allow ahead "
        "of a Deny actually grants.",
    )
    entries_supplied: int
    entries_evaluated: int = Field(description="Of those, how many named this token.")


class EffectiveAccessView(BaseModel):
    """The conclusion, with both layers and every qualification."""

    resource_key: str
    share_key: str | None = None
    access_path: str
    access: bool = Field(
        description=(
            "Whether any right survives. Read it with certainty: false with 'at_least' "
            "means no access was established, not that none exists."
        )
    )
    rights: RightsView
    certainty: AccessCertainty
    limiting_layer: LimitingLayer = Field(
        description="Which ACL removed rights the other granted. The field that says which "
        "ACL to go and fix."
    )
    acl_provenance: AclProvenance = Field(
        description="Whether the NTFS DACL was read on this object, projected from an "
        "ancestor, or never seen."
    )
    ntfs: AclEvaluationView
    share: AclEvaluationView | None = None
    conditions: list[str] = Field(default_factory=list)
    findings: list[FindingView] = Field(default_factory=list)


class ExplanationNodeView(BaseModel):
    """One node of the explanation graph."""

    id: str
    kind: NodeKind
    key: str
    sid: str | None = None
    display_name: str | None = None
    layer: str | None = Field(default=None, description="ACE nodes only: which ACL it is on.")
    position: int | None = Field(
        default=None,
        description="ACE nodes only: index in the DACL as stored. Two entries naming one "
        "trustee are two nodes, because order decides which of them settles a right.",
    )


class ExplanationEdgeView(BaseModel):
    """One edge of the explanation graph."""

    id: str
    kind: EdgeKind
    source: str
    target: str
    membership_edge_key: str | None = None
    ace_key: str | None = None
    removable: bool = Field(
        description="Whether an administrator could actually delete this relationship. False "
        "for an assumed membership: there is no Everyone group to edit."
    )


class ExplanationGraphView(BaseModel):
    """The typed nodes and edges every path refers to, deduplicated."""

    nodes: list[ExplanationNodeView] = Field(default_factory=list)
    edges: list[ExplanationEdgeView] = Field(default_factory=list)


class CausalPathView(BaseModel):
    """One route from the subject to one ACE on one object, and what it delivers."""

    id: str
    layer: str
    relation: PathRelation = Field(description="Whether the ACE at the end allows or denies.")
    effect: PathEffect = Field(
        description=(
            "What the path is worth. 'contributes' changed the final answer; 'redundant' "
            "means an earlier entry had already settled every right it names; 'constrained' "
            "means the other layer withholds all of it, so it does not affect access."
        )
    )
    chain: list[PrincipalSummary] = Field(
        default_factory=list,
        description="The membership chain, subject first and ACL trustee last.",
    )
    nodes: list[str] = Field(default_factory=list)
    edges: list[str] = Field(default_factory=list)
    ace_position: int
    ace_key: str | None = None
    ace_rights: RightsView = Field(description="The entry's own mask: what it is worth alone.")
    layer_rights: RightsView = Field(
        description="What it settled at its own layer that nothing earlier had already settled."
    )
    effective_rights: RightsView = Field(
        description="What it is worth to the final answer, after the layer crossing."
    )
    constrained_rights: RightsView = Field(
        description="The part of layer_rights the other ACL withholds."
    )
    assumed: bool = Field(
        description="True when the ACE reached the subject through an assumed token SID "
        "(Everyone, Authenticated Users) rather than an observed membership."
    )
    via_group: bool
    inherited: bool


class RemovalTargetView(BaseModel):
    """What removing one edge would do, measured by re-running the access check."""

    edge_id: str
    kind: EdgeKind
    source: str
    target: str
    rights_removed: RightsView = Field(
        description="Effective rights lost. Empty means removing this changes nothing, which "
        "is the honest answer whenever another path remains."
    )
    rights_added: RightsView = Field(
        description="Effective rights *gained*. Non-empty means the edge carried a Deny and "
        "removing it would widen access."
    )
    rights_after: RightsView
    revokes_all_access: bool = Field(
        description="Whether removing this one edge leaves no rights at all. False while any "
        "alternate path survives."
    )
    changes_nothing: bool
    alternate_paths: list[str] = Field(
        default_factory=list,
        description="Contributing paths that still deliver rights afterwards.",
    )
    paths_removed: list[str] = Field(default_factory=list)


class VerdictView(BaseModel):
    r"""The answer as one value, with the evidence that distinguishes it from its neighbors.

    ``access: false`` is two completely different findings and this is where they are told
    apart. Branch on ``outcome``; render ``reason``; and never draw a negative conclusion
    while ``conclusive`` is false.
    """

    outcome: AccessOutcome = Field(
        description=(
            "granted — rights survive. denied — none survive and a Deny entry is why. "
            "no_grant — none survive and nothing denied any; nothing names this principal. "
            "indeterminate — none were established and collection is incomplete in a "
            "direction that could hide a grant, so this is not a finding of no access."
        )
    )
    reason: str = Field(description="The operator-facing sentence for the outcome.")
    certainty: AccessCertainty = Field(
        description=(
            "Orthogonal to the outcome: the outcome is the verdict on the evidence, this is "
            "the direction the evidence can be wrong in. Both must be rendered."
        )
    )
    conclusive: bool = Field(
        description="False only for 'indeterminate'. While false, 'this principal has no "
        "access' is not a statement this response supports."
    )
    may_overstate: bool
    may_understate: bool = Field(
        description="True when the real rights could be wider than reported — keep looking."
    )
    denials: list[AppliedAceView] = Field(
        default_factory=list,
        description=(
            "Deny entries that actually withheld rights, both layers, in evaluation order. "
            "A projection of the per-layer 'denied_by' lists, placed here because it is the "
            "evidence for a 'denied' outcome. Entries that matched and took nothing away are "
            "not here; they are in the layer's 'superseded' list."
        ),
    )


class ExplanationLimitsView(BaseModel):
    """The bounds actually applied, after clamping."""

    max_paths: int
    max_paths_per_trustee: int
    max_depth: int
    max_removal_targets: int


class ResourceRef(BaseModel):
    """Enough of a directory to identify it, with whether it was ever read."""

    key: str
    path: str | None = None
    share_key: str | None = None
    observed: bool
    dacl_present: bool | None = None
    dacl_protected: bool | None = None
    is_acl_boundary: bool | None = None
    owner_sid: str | None = None


class ShareRef(BaseModel):
    """Enough of a share to identify it, with whether it was ever described."""

    key: str
    name: str | None = None
    server_key: str | None = None
    observed: bool


class EffectiveAccessResponse(BaseModel):
    """One principal, one resource, one access path."""

    subject: PrincipalSummary
    resource: ResourceRef
    share: ShareRef | None = None
    token: TokenView
    effective: EffectiveAccessView


class AccessPathsResponse(BaseModel):
    """One principal, one resource, and every path that caused the answer."""

    subject: PrincipalSummary
    resource: ResourceRef
    share: ShareRef | None = None
    effective: EffectiveAccessView
    graph: ExplanationGraphView
    paths: list[CausalPathView] = Field(default_factory=list)
    removal_targets: list[RemovalTargetView] = Field(
        default_factory=list,
        description="Every removable edge on a path, with the measured effect of deleting it.",
    )
    cycles: list[list[str]] = Field(
        default_factory=list,
        description="Membership cycles in the traversed subgraph. A cycle is a finding.",
    )
    limits: ExplanationLimitsView
    complete: bool = Field(
        description="False means the paths shown are a subset: at least one more route to "
        "these rights exists, so nothing may be concluded from the absence of one."
    )
    truncation: list[str] = Field(default_factory=list)


class AccessExplanationResponse(BaseModel):
    r"""Everything needed to render "why does this principal have this access?" — once.

    The published shape of this response is
    ``docs/contracts/v1/access-explanation.schema.json``; the schema is the contract and
    this model is the implementation of it, held together by a parity test.

    It is deliberately one round trip. A client that had to fetch the answer, then the
    paths, then the ACL to work out which entry mattered would be reimplementing the access
    check to join them — and the whole point of the engine is that nothing outside it ever
    has to.
    """

    schema_version: str = Field(
        default=CONTRACT_VERSION,
        json_schema_extra={"const": CONTRACT_VERSION},
        description="Version of the derived-answer contract this body conforms to. Additive "
        "changes bump the minor; a breaking change means a new major and a new path.",
    )
    basis: BasisView
    subject: PrincipalSummary
    resource: ResourceRef
    share: ShareRef | None = None
    access_path: str
    verdict: VerdictView
    token: TokenView
    effective: EffectiveAccessView = Field(
        description="The full derivation: the effective mask, and the NTFS and SMB "
        "evaluations that produced it with every contributing and denying entry."
    )
    graph: ExplanationGraphView
    paths: list[CausalPathView] = Field(
        default_factory=list,
        description="Every route from the subject to an entry that matched, with what each "
        "one is actually worth. Bounded, not paged; /api/v1/access/paths pages the same "
        "list when an estate produces more of them than one response should carry.",
    )
    removal_targets: list[RemovalTargetView] = Field(
        default_factory=list,
        description="Every removable edge on a path, with the measured effect of deleting it.",
    )
    cycles: list[list[str]] = Field(
        default_factory=list,
        description="Membership cycles in the traversed subgraph. A cycle is a finding.",
    )
    warnings: list[FindingView] = Field(
        default_factory=list,
        description="Everything that qualifies this answer: assumptions made, trustees that "
        "could not be resolved, descriptors never read. The same list as effective.findings, "
        "surfaced here because it qualifies the whole response and not just the mask.",
    )
    limits: ExplanationLimitsView
    complete: bool = Field(
        description="False means the paths shown are a subset: at least one more route to "
        "these rights exists, so nothing may be concluded from the absence of one."
    )
    truncation: list[str] = Field(default_factory=list)


class AccessPathPageResponse(BaseModel):
    """One page of the routes that produced an answer.

    Same computation and same deterministic order as the explanation's ``paths``, sliced.
    The verdict travels with every page on purpose: a page of paths read without the answer
    they explain is how somebody concludes that a ``constrained`` path grants access.
    """

    schema_version: str = Field(
        default=CONTRACT_VERSION, json_schema_extra={"const": CONTRACT_VERSION}
    )
    basis: BasisView
    subject: PrincipalSummary
    resource: ResourceRef
    share: ShareRef | None = None
    access_path: str
    verdict: VerdictView
    graph: ExplanationGraphView = Field(
        description="The nodes and edges this page's paths refer to, and only those, so the "
        "page renders on its own without holding the whole graph."
    )
    items: list[CausalPathView] = Field(default_factory=list)
    page: PageInfo
    limits: ExplanationLimitsView
    complete: bool = Field(
        description="Whether the underlying enumeration was complete. Independent of paging: "
        "a page can be the last page of a truncated enumeration, which is exactly the case a "
        "client must not read as 'these are all the paths'."
    )
    truncation: list[str] = Field(default_factory=list)


class PrincipalAccessView(BaseModel):
    """One principal in a resource's effective-principal listing."""

    principal: PrincipalSummary
    access: bool
    rights: RightsView
    certainty: AccessCertainty
    limiting_layer: LimitingLayer
    via: list[TokenEntryView] = Field(
        default_factory=list,
        description="The ACL trustees that put this principal here, with the chain to each. "
        "Empty means the ACL names the principal directly.",
    )
    conditions: list[str] = Field(default_factory=list)


class EnumerationView(BaseModel):
    """Whether the listing is everybody, and what stopped it from being."""

    complete: bool
    unenumerable_trustees: list[PrincipalSummary] = Field(
        default_factory=list,
        description=(
            "Trustees whose members could not be listed: world SIDs such as Everyone, and "
            "groups whose membership no run has collected. Each one means principals hold "
            "rights here that are not in this listing."
        ),
    )
    trustees_truncated: bool = Field(
        default=False, description="The ACL has more distinct trustees than the server expands."
    )


class ResourcePrincipalsResponse(BaseModel):
    """Who can reach one resource."""

    resource: ResourceRef
    share: ShareRef | None = None
    access_path: str
    items: list[PrincipalAccessView] = Field(default_factory=list)
    enumeration: EnumerationView
    findings: list[FindingView] = Field(default_factory=list)
    page: PageInfo


class ResourceAccessView(BaseModel):
    """One resource in a principal's accessible-resource listing."""

    resource: ResourceRef
    share: ShareRef | None = None
    access: bool
    rights: RightsView
    certainty: AccessCertainty
    limiting_layer: LimitingLayer
    conditions: list[str] = Field(default_factory=list)


class PrincipalResourcesResponse(BaseModel):
    """What one principal can reach."""

    subject: PrincipalSummary
    access_path: str
    token: TokenView
    items: list[ResourceAccessView] = Field(default_factory=list)
    page: PageInfo


# ----------------------------------------------------------------- endpoints


@router.get(
    "/principals/{identifier}/resources/{resource:path}",
    response_model=EffectiveAccessResponse,
    summary="What one principal can do to one directory",
    responses={
        404: {"description": "Nothing is stored about this principal."},
        409: {"description": "A bare SID matched several host-scoped principals."},
        422: {"description": "The identifier is not a SID, or the path is not a UNC path."},
    },
)
async def effective_access(
    identifier: IdentifierPath,
    resource: ResourcePath,
    session: Session,
    limits: TraversalBounds,
    host: HostQuery = None,
    access_path: PathQuery = AccessPath.REMOTE_SMB,
    assumption: AssumptionQuery = None,
) -> EffectiveAccessResponse:
    r"""Resolve one principal's effective rights to one directory, with the derivation.

    The answer is the intersection of what the share ACL permits and what the NTFS DACL
    permits, evaluated in the order each descriptor stores its entries, against a token
    built from the principal's observed memberships plus the SIDs Windows guarantees.

    A path whose descriptor no run has read is not treated as having no permissions: the
    DACL is projected from the nearest ancestor that *was* read, and ``acl_provenance``
    says so.
    """
    membership = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    key, record = await resolve_principal(membership, identifier, host)
    service = AccessService(ResourceRepository(session), membership)

    resolved = await service.effective_access(
        key,
        _resource_key(resource),
        path=access_path,
        limits=limits,
        assumption=assumption,
    )
    return EffectiveAccessResponse(
        subject=principal_summary(key, record),
        resource=render_resource_ref(resolved),
        share=render_share_ref(resolved),
        token=render_token(resolved.access.token, resolved.principals),
        effective=_effective_view(resolved.access, resolved.principals),
    )


@router.get(
    "/paths/principals/{identifier}/resources/{resource:path}",
    response_model=AccessPathsResponse,
    summary="Why one principal has the rights it has on one directory",
    responses={
        404: {"description": "Nothing is stored about this principal."},
        409: {"description": "A bare SID matched several host-scoped principals."},
        422: {"description": "The identifier is not a SID, or the path is not a UNC path."},
    },
)
async def access_paths(
    identifier: IdentifierPath,
    resource: ResourcePath,
    session: Session,
    limits: TraversalBounds,
    host: HostQuery = None,
    access_path: PathQuery = AccessPath.REMOTE_SMB,
    assumption: AssumptionQuery = None,
    max_causal_paths: MaxCausalPathsQuery = None,
    max_removal_targets: MaxRemovalTargetsQuery = None,
) -> AccessPathsResponse:
    r"""Enumerate the membership and ACE paths that produced one effective-access answer.

    The same computation as ``/principals/{identifier}/resources/{resource}``, with the
    derivation kept: which chains reached which entries, what each one is actually worth,
    and what would change if any one edge were removed.

    Three distinctions the response draws that a trustee list cannot:

    * an ACE that **matched** the token is not necessarily a **cause**. It can be redundant
      — an earlier entry had already settled every right it names — or constrained, where
      the other ACL withholds all of it;
    * several paths can lead to the same rights, and all of them are kept. Two chains to one
      group is the common case, and collapsing them is how a remediation gets signed off
      having changed nothing;
    * ``removal_targets`` is **measured**, by re-running the access check with each edge
      gone. An edge with an alternate path around it reports no rights removed, and a
      membership carrying a Deny reports rights *added*.

    This route is not paged. It answers about exactly one principal and one resource, and
    its size is bounded by ``max_paths`` and ``max_removal_targets`` rather than by a
    cursor; ``limits`` reports what was applied after clamping and ``complete`` says whether
    anything was cut.
    """
    resolved, record, key = await _explain(
        session,
        identifier=identifier,
        resource=resource,
        host=host,
        access_path=access_path,
        assumption=assumption,
        limits=limits,
        explanation_limits=_explanation_limits(limits, max_causal_paths, max_removal_targets),
    )
    explanation = resolved.explanation
    labels = resolved.principals
    return AccessPathsResponse(
        subject=principal_summary(key, record),
        resource=render_resource_ref(resolved),
        share=render_share_ref(resolved),
        effective=_effective_view(explanation.access, labels),
        graph=_graph_view(explanation.graph),
        paths=[_causal_path_view(path, labels) for path in explanation.paths],
        removal_targets=[_removal_view(target) for target in explanation.removal_targets],
        cycles=[list(cycle.members) for cycle in explanation.cycles],
        limits=_limits_view(explanation),
        complete=explanation.complete,
        truncation=[reason.value for reason in explanation.truncation],
    )


@router.get(
    "/explain",
    response_model=AccessExplanationResponse,
    summary="Why one principal has the access it has, as one renderable object",
    responses={
        304: {"description": "Nothing has been collected since the ETag was issued."},
        404: {"description": "Nothing is stored about this principal."},
        409: {"description": "A bare SID matched several host-scoped principals."},
        422: {"description": "The principal is not a SID, or the resource is not a UNC path."},
    },
)
async def explain(
    session: Session,
    limits: TraversalBounds,
    response: Response,
    principal: PrincipalQuery,
    resource: ResourceExplainQuery,
    host: HostQuery = None,
    access_path: PathQuery = AccessPath.REMOTE_SMB,
    assumption: AssumptionQuery = None,
    max_causal_paths: MaxCausalPathsQuery = None,
    max_removal_targets: MaxRemovalTargetsQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> AccessExplanationResponse | Response:
    r"""The whole answer to "why can this principal do this?", in one request.

    The same computation as
    ``/paths/principals/{identifier}/resources/{resource}``, addressed by query parameters
    and shaped for a client that has to render it. Three things it adds:

    * **``verdict``** — the one field that separates *denied*, *not granted*, and *not
      answerable from what has been collected*. ``access: false`` conflates all three, and
      an auditor who reads "no access" where the truth is "nobody scanned that server"
      stops looking exactly where they should keep looking.
    * **``basis``** — which collected state this was computed from, so an answer can be
      quoted as of a scan rather than as of a wall clock.
    * **conditional GET** — an ``ETag`` over *(contract version, collection basis, this
      request)*. Return it in ``If-None-Match`` and a repeat costs one small query and a
      ``304``. There is no time-to-live anywhere: the answer is reusable exactly as long as
      no collector has written anything, which is what the validator encodes.

    Everything Phase 5A established still holds, unchanged and unrepeated here: a matched
    ACE is not necessarily a cause, every path is kept rather than collapsed, and every
    removal target is *measured* by re-running the access check without that edge.
    """
    explanation_limits = _explanation_limits(limits, max_causal_paths, max_removal_targets)
    validator = await current_validator(
        session,
        route="access.explain",
        parameters={
            "principal": principal,
            "resource": resource,
            "host": host,
            "access_path": access_path,
            "assumption": assumption,
            **_limit_parameters(limits, explanation_limits),
        },
    )
    if validator.matches(if_none_match):
        return not_modified(validator)

    resolved, record, key = await _explain(
        session,
        identifier=principal,
        resource=resource,
        host=host,
        access_path=access_path,
        assumption=assumption,
        limits=limits,
        explanation_limits=explanation_limits,
    )
    explanation = resolved.explanation
    labels = resolved.principals
    validator.apply(response)
    return AccessExplanationResponse(
        basis=basis_view(validator.basis),
        subject=principal_summary(key, record),
        resource=render_resource_ref(resolved),
        share=render_share_ref(resolved),
        access_path=explanation.access.path.value,
        verdict=render_verdict(explanation.access, labels),
        token=render_token(explanation.access.token, labels),
        effective=_effective_view(explanation.access, labels),
        graph=_graph_view(explanation.graph),
        paths=[_causal_path_view(path, labels) for path in explanation.paths],
        removal_targets=[_removal_view(target) for target in explanation.removal_targets],
        cycles=[list(cycle.members) for cycle in explanation.cycles],
        warnings=[_finding_view(finding) for finding in explanation.access.findings],
        limits=_limits_view(explanation),
        complete=explanation.complete,
        truncation=[reason.value for reason in explanation.truncation],
    )


@router.get(
    "/paths",
    response_model=AccessPathPageResponse,
    summary="One page of the routes that produced an answer",
    responses={
        304: {"description": "Nothing has been collected since the ETag was issued."},
        404: {"description": "Nothing is stored about this principal."},
        409: {"description": "A bare SID matched several host-scoped principals."},
        422: {"description": "A bad identifier, a bad UNC path, or a cursor from elsewhere."},
    },
)
async def access_path_page(
    session: Session,
    limits: TraversalBounds,
    response: Response,
    principal: PrincipalQuery,
    resource: ResourceExplainQuery,
    host: HostQuery = None,
    access_path: PathQuery = AccessPath.REMOTE_SMB,
    assumption: AssumptionQuery = None,
    max_causal_paths: MaxCausalPathsQuery = None,
    max_removal_targets: MaxRemovalTargetsQuery = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> AccessPathPageResponse | Response:
    r"""Page through the causal paths behind one answer, in the engine's own order.

    An explanation of a well-nested estate can carry hundreds of chains, and a client that
    renders a list wants a page of it rather than all of it. This is that list, sliced
    server-side; ``/explain`` returns the same paths whole, and both come from one
    enumeration whose order Phase 5A made deterministic, so page two of this route and the
    second screenful of that one are the same paths.

    **Paging is a slice of the enumeration, not a widening of it.** ``max_causal_paths``
    still bounds how many paths are enumerated at all, and asking for page five does not
    enumerate more than that ceiling: ``complete: false`` means routes exist that no page
    will ever show. Paging and truncation are separate facts and both are reported, because
    a caller that walks to the last page of a truncated enumeration would otherwise conclude
    it had seen everything.

    ``graph`` carries only the nodes and edges this page's paths refer to, so the page
    renders on its own. ``removal_targets`` is not paged here — it is a property of the
    whole explanation, not of a page — and lives on ``/explain``.
    """
    explanation_limits = _explanation_limits(limits, max_causal_paths, max_removal_targets)
    page_size = normalize_limit(limit)
    validator = await current_validator(
        session,
        route="access.paths",
        parameters={
            "principal": principal,
            "resource": resource,
            "host": host,
            "access_path": access_path,
            "assumption": assumption,
            "limit": page_size,
            "cursor": cursor,
            **_limit_parameters(limits, explanation_limits),
        },
    )
    if validator.matches(if_none_match):
        return not_modified(validator)

    offset = decode_offset_cursor(cursor)
    resolved, record, key = await _explain(
        session,
        identifier=principal,
        resource=resource,
        host=host,
        access_path=access_path,
        assumption=assumption,
        limits=limits,
        explanation_limits=explanation_limits,
    )
    explanation = resolved.explanation
    labels = resolved.principals

    window = explanation.paths[offset : offset + page_size]
    has_more = len(explanation.paths) > offset + page_size
    validator.apply(response)
    return AccessPathPageResponse(
        basis=basis_view(validator.basis),
        subject=principal_summary(key, record),
        resource=render_resource_ref(resolved),
        share=render_share_ref(resolved),
        access_path=explanation.access.path.value,
        verdict=render_verdict(explanation.access, labels),
        graph=_graph_view(_subgraph(explanation.graph, window)),
        items=[_causal_path_view(path, labels) for path in window],
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
            total=len(explanation.paths),
        ),
        limits=_limits_view(explanation),
        complete=explanation.complete,
        truncation=[reason.value for reason in explanation.truncation],
    )


@router.get(
    "/resources/{resource:path}/principals",
    response_model=ResourcePrincipalsResponse,
    summary="Every principal ADG can show holds rights on one directory",
    responses={422: {"description": "The identifier is not a canonical UNC path."}},
)
async def resource_principals(
    resource: ResourcePath,
    session: Session,
    limits: TraversalBounds,
    access_path: PathQuery = AccessPath.REMOTE_SMB,
    members: InclusionQuery = MemberInclusion.NON_GROUPS,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> ResourcePrincipalsResponse:
    """List who can reach a directory, with the membership chain that explains each one.

    Bounded by expanding each **trustee** of the ACL downward once rather than evaluating
    every principal in the domain: a principal's rights here depend only on which of this
    ACL's trustees it belongs to.

    Paged by offset, because the list is recomputed per page out of a traversal rather than
    read from an index — the same trade the recursive membership endpoints make, and for
    the same reason. Read ``enumeration.complete`` before treating the listing as everybody.
    """
    membership = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    service = AccessService(ResourceRepository(session), membership)
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)

    result = await service.effective_principals(
        _resource_key(resource),
        path=access_path,
        limits=limits,
        inclusion=members,
        limit=page_size,
        offset=offset,
    )
    return ResourcePrincipalsResponse(
        resource=_resource_ref_from(result),
        share=_share_ref_from(result),
        access_path=result.path.value,
        items=[_principal_access_view(item, result.principals) for item in result.items],
        enumeration=EnumerationView(
            complete=result.complete,
            unenumerable_trustees=[
                principal_summary(key, result.principals.get(key))
                for key in result.unenumerable_trustees
            ],
            trustees_truncated=result.truncated_trustees,
        ),
        findings=[_finding_view(finding) for finding in result.findings],
        page=PageInfo(
            limit=page_size,
            has_more=result.has_more,
            next_cursor=(
                encode_offset_cursor(offset + len(result.items)) if result.has_more else None
            ),
            total=result.total,
        ),
    )


@router.get(
    "/principals/{identifier}/resources",
    response_model=PrincipalResourcesResponse,
    summary="Directories one principal can reach",
    responses={
        404: {"description": "Nothing is stored about this principal."},
        409: {"description": "A bare SID matched several host-scoped principals."},
    },
)
async def principal_resources(
    identifier: IdentifierPath,
    session: Session,
    limits: TraversalBounds,
    host: HostQuery = None,
    access_path: PathQuery = AccessPath.REMOTE_SMB,
    assumption: AssumptionQuery = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> PrincipalResourcesResponse:
    """List the directories whose ACLs could involve this principal, each with its verdict.

    Candidates come from the reference index keyed by the token's own trustees, unioned
    with the paths that have a NULL DACL and therefore name nobody. Only the page is
    evaluated, and its ACLs are read in one query.

    **Candidates that turn out to grant nothing are returned, not filtered.** "Named on the
    ACL and holding no access" is the distinction this engine exists to draw, and dropping
    those rows inside a page would also make ``has_more`` a claim about a different set
    than the one paged.
    """
    return await _principal_resource_page(
        identifier,
        session,
        limits,
        host=host,
        access_path=access_path,
        assumption=assumption,
        limit=limit,
        cursor=cursor,
        shares=False,
    )


@router.get(
    "/principals/{identifier}/shares",
    response_model=PrincipalResourcesResponse,
    summary="Shares one principal can reach",
    responses={
        404: {"description": "Nothing is stored about this principal."},
        409: {"description": "A bare SID matched several host-scoped principals."},
        422: {"description": "A share is a remote path; access_path=local is refused."},
    },
)
async def principal_shares(
    identifier: IdentifierPath,
    session: Session,
    limits: TraversalBounds,
    host: HostQuery = None,
    access_path: PathQuery = AccessPath.REMOTE_SMB,
    assumption: AssumptionQuery = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> PrincipalResourcesResponse:
    r"""List the shares whose ACL names this principal, each crossed with its root's DACL.

    The share question and the directory question are separate routes because they are
    separate answers: a share grants nothing on its own, and the rights reported here are
    the share ACL intersected with the NTFS ACL of the directory the share publishes.
    """
    return await _principal_resource_page(
        identifier,
        session,
        limits,
        host=host,
        access_path=access_path,
        assumption=assumption,
        limit=limit,
        cursor=cursor,
        shares=True,
    )


async def _principal_resource_page(
    identifier: str,
    session: Session,
    limits: TraversalBounds,
    *,
    host: str | None,
    access_path: AccessPath,
    assumption: TokenAssumption | None,
    limit: int | None,
    cursor: str | None,
    shares: bool,
) -> PrincipalResourcesResponse:
    """The body both listing routes share; only the candidate source differs."""
    membership = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    key, record = await resolve_principal(membership, identifier, host)
    service = AccessService(ResourceRepository(session), membership)
    page_size = normalize_limit(limit)

    result: SubjectAccessPage = await service.accessible_resources(
        key,
        shares=shares,
        path=access_path,
        limits=limits,
        assumption=assumption,
        limit=page_size,
        after=decode_keyset_cursor(cursor),
    )
    return PrincipalResourcesResponse(
        subject=principal_summary(key, record),
        access_path=result.path.value,
        token=render_token(result.token, result.principals),
        items=[_resource_access_view(item) for item in result.items],
        page=PageInfo(
            limit=page_size,
            has_more=result.has_more,
            next_cursor=(
                encode_keyset_cursor(result.next_key) if result.next_key is not None else None
            ),
            # Deliberately not counted. The candidate set is a union of two indexed reads
            # and counting it would double the cost of every page to produce a number the
            # caller cannot act on -- and which is a count of candidates, not of grants.
            total=None,
        ),
    )


# ------------------------------------------------------- explanation plumbing


def _explanation_limits(
    limits: TraversalLimits, max_causal_paths: int | None, max_removal_targets: int | None
) -> ExplanationLimits:
    """The explanation's own bounds, clamped, from the traversal's and the caller's.

    ``max_paths`` and ``max_depth`` already mean, for the membership traversal, exactly what
    the explanation needs them to mean for one trustee's chains, so they are reused rather
    than duplicated under a second name that could disagree with the first.

    Pure and cheap, and called before any query on purpose: the clamped values are part of
    the cache validator, so a request that asks for a different bound has to get a different
    ETag rather than a 304 carrying somebody else's bound.
    """
    return DEFAULT_EXPLANATION_LIMITS.clamped(
        max_paths=max_causal_paths,
        max_paths_per_trustee=limits.max_paths,
        max_depth=limits.max_depth,
        max_removal_targets=max_removal_targets,
    )


def _limit_parameters(
    limits: TraversalLimits, explanation_limits: ExplanationLimits
) -> dict[str, Any]:
    """Every bound that can change a body, as validator material.

    The **clamped** values, not what the caller asked for: two requests asking for 10,000
    paths and 1,000,000 paths get the same ceiling and therefore the same answer, and should
    share a cache entry rather than each holding their own.
    """
    return {
        "max_depth": limits.max_depth,
        "max_nodes": limits.max_nodes,
        "max_edges": limits.max_edges,
        "max_paths": limits.max_paths,
        "max_causal_paths": explanation_limits.max_paths,
        "max_removal_targets": explanation_limits.max_removal_targets,
    }


async def _explain(
    session: AsyncSession,
    *,
    identifier: str,
    resource: str,
    host: str | None,
    access_path: AccessPath,
    assumption: TokenAssumption | None,
    limits: TraversalLimits,
    explanation_limits: ExplanationLimits,
) -> tuple[ResolvedExplanation, PrincipalRecord | None, str]:
    """Resolve one principal against one resource and keep the derivation.

    Shared by all three explanation routes so that the query-addressed spelling and the
    path-addressed one cannot answer differently — including in how they fail, which is
    where a second implementation would diverge first: an ambiguous BUILTIN SID is a 409
    and an unknown one a 404, and both come from the single :func:`resolve_principal`.
    """
    membership = MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
    key, record = await resolve_principal(membership, identifier, host)
    service = AccessService(ResourceRepository(session), membership)
    resolved = await service.explain_access(
        key,
        _resource_key(resource),
        path=access_path,
        limits=limits,
        explanation_limits=explanation_limits,
        assumption=assumption,
    )
    return resolved, record, key


def _subgraph(graph: ExplanationGraph, paths: Sequence[CausalPath]) -> ExplanationGraph:
    """The nodes and edges a slice of paths refers to, and nothing else.

    A page carries its own subgraph so it can be rendered without the whole one. Node and
    edge ids are unchanged, so two pages' subgraphs compose into the full graph by union
    rather than needing to be reconciled.
    """
    node_ids = {node_id for path in paths for node_id in path.node_ids}
    edge_ids = {edge_id for path in paths for edge_id in path.edge_ids}
    return ExplanationGraph(
        nodes=tuple(node for node in graph.nodes if node.node_id in node_ids),
        edges=tuple(edge for edge in graph.edges if edge.edge_id in edge_ids),
    )


# ------------------------------------------------------------------- renderers


def _resource_key(identifier: str) -> str:
    r"""Validate a directory identifier, refusing a share key.

    ``fs01|finance`` names a share, and a share's ACL and its root directory's ACL are
    different documents. Converting one to the other here would answer a question about a
    share with a directory's permissions, which is the layer confusion the separate routes
    exist to prevent.
    """
    text = identifier.strip()
    if "|" in text:
        raise DomainValidationError(
            f"{identifier!r} looks like a share key. A directory is named by its UNC path, "
            "for example \\\\FS01\\Finance. For the share layer, ask "
            "/api/v1/access/principals/{principal}/shares.",
            value=identifier,
            field="resource",
        )
    return parse_unc_path(text).comparison_key


def render_rights(mask: RightsMask) -> RightsView:
    summary = summarize(mask)
    return RightsView(
        mask=str(mask),
        value=mask.value,
        layer=mask.layer.value,
        label=summary.label,
        primary=category_display_name(summary.primary),
        categories=[category_display_name(category) for category in summary.categories],
        is_exact=summary.is_exact,
        extra_rights=_right_names(summary.extra_rights),
        escalation_rights=_right_names(summary.escalation_rights),
        unrecognized_bits=(f"0x{mask.unrecognized_bits:08X}" if mask.unrecognized_bits else None),
        indeterminate=mask.is_indeterminate,
    )


def _right_names(rights: Any) -> list[str]:
    """Flag names for a rights value, stable and without the zero member."""
    if not rights:
        return []
    return [member.name for member in type(rights) if member.value and member & rights]


def _finding_view(finding: AccessFinding) -> FindingView:
    return FindingView(
        condition=finding.condition.value,
        message=finding.message,
        may_overstate=finding.may_overstate,
        may_understate=finding.may_understate,
        detail=finding.detail,
    )


def _token_entry_view(entry: TokenSid, labels: dict[str, PrincipalRecord]) -> TokenEntryView:
    return TokenEntryView(
        principal=principal_summary(entry.key, labels.get(entry.key)),
        origin=entry.origin.value,
        assumed=entry.is_assumed,
        depth=entry.depth,
        path=list(entry.path),
    )


def render_token(token: SubjectToken, labels: dict[str, PrincipalRecord]) -> TokenView:
    return TokenView(
        subject=principal_summary(token.subject.key, labels.get(token.subject.key)),
        assumption=token.assumption.value,
        access_path=token.access_path.value,
        membership_complete=token.membership_complete,
        entries=[_token_entry_view(entry, labels) for entry in token.entries],
    )


def render_applied_ace(applied: AppliedAce, labels: dict[str, PrincipalRecord]) -> AppliedAceView:
    entry = applied.entry
    return AppliedAceView(
        ace_key=entry.ace_key,
        layer=entry.layer.value,
        position=applied.position,
        trustee=principal_summary(entry.trustee_key, labels.get(entry.trustee_key)),
        ace_type=entry.ace_type.value,
        access_mask=str(applied.considered),
        contributed=str(applied.contributed),
        flags=int(entry.flags),
        source=entry.source.value,
        inherited_from=entry.inherited_from,
        matched_key=applied.matched.key,
        via_group=applied.via_group,
    )


def _evaluation_view(
    evaluation: AclEvaluation, labels: dict[str, PrincipalRecord]
) -> AclEvaluationView:
    return AclEvaluationView(
        layer=evaluation.layer.value,
        rights=render_rights(evaluation.rights),
        granted_by=[render_applied_ace(item, labels) for item in evaluation.granted_by],
        denied_by=[render_applied_ace(item, labels) for item in evaluation.denied_by],
        superseded=[render_applied_ace(item, labels) for item in evaluation.superseded],
        owner_rights=(
            None if evaluation.owner_rights is None else render_rights(evaluation.owner_rights)
        ),
        canonical_rights=(
            None
            if evaluation.canonical_rights is None
            else render_rights(evaluation.canonical_rights)
        ),
        order_dependent=evaluation.order_dependent,
        entries_supplied=evaluation.entries_supplied,
        entries_evaluated=evaluation.entries_evaluated,
    )


def _effective_view(
    access: EffectiveAccess, labels: dict[str, PrincipalRecord]
) -> EffectiveAccessView:
    return EffectiveAccessView(
        resource_key=access.resource_key,
        share_key=access.share_key,
        access_path=access.path.value,
        access=access.has_access,
        rights=render_rights(access.rights),
        certainty=access.certainty,
        limiting_layer=access.limiting_layer,
        acl_provenance=access.provenance,
        ntfs=_evaluation_view(access.ntfs, labels),
        share=None if access.share is None else _evaluation_view(access.share, labels),
        conditions=[condition.value for condition in access.conditions],
        findings=[_finding_view(finding) for finding in access.findings],
    )


def render_verdict(access: EffectiveAccess, labels: dict[str, PrincipalRecord]) -> VerdictView:
    """The typed answer, classified once by the engine rather than by each client."""
    verdict: AccessVerdict = classify_access(access)
    return VerdictView(
        outcome=verdict.outcome,
        reason=verdict.reason,
        certainty=verdict.certainty,
        conclusive=verdict.is_conclusive,
        may_overstate=verdict.may_overstate,
        may_understate=verdict.may_understate,
        denials=[render_applied_ace(applied, labels) for applied in verdict.denials],
    )


def _limits_view(explanation: AccessExplanation) -> ExplanationLimitsView:
    return ExplanationLimitsView(
        max_paths=explanation.limits.max_paths,
        max_paths_per_trustee=explanation.limits.max_paths_per_trustee,
        max_depth=explanation.limits.max_depth,
        max_removal_targets=explanation.limits.max_removal_targets,
    )


def _graph_view(graph: ExplanationGraph) -> ExplanationGraphView:
    return ExplanationGraphView(
        nodes=[_node_view(node) for node in graph.nodes],
        edges=[_edge_view(edge) for edge in graph.edges],
    )


def _node_view(node: ExplanationNode) -> ExplanationNodeView:
    return ExplanationNodeView(
        id=node.node_id,
        kind=node.kind,
        key=node.key,
        sid=node.sid,
        display_name=node.display_name,
        layer=None if node.layer is None else node.layer.value,
        position=node.position,
    )


def _edge_view(edge: ExplanationEdge) -> ExplanationEdgeView:
    return ExplanationEdgeView(
        id=edge.edge_id,
        kind=edge.kind,
        source=edge.source,
        target=edge.target,
        membership_edge_key=edge.membership_edge_key,
        ace_key=edge.ace_key,
        removable=edge.is_removable,
    )


def _causal_path_view(path: CausalPath, labels: dict[str, PrincipalRecord]) -> CausalPathView:
    """One path, with every principal on its chain rendered rather than left as a key.

    The chain is the product. A response that returned bare storage keys would make the
    client re-resolve each one, and a client that cannot would render the explanation as a
    row of SIDs.
    """
    return CausalPathView(
        id=path.path_id,
        layer=path.layer.value,
        relation=path.relation,
        effect=path.effect,
        chain=[principal_summary(key, labels.get(key)) for key in path.chain],
        nodes=list(path.node_ids),
        edges=list(path.edge_ids),
        ace_position=path.ace_position,
        ace_key=path.ace_key,
        ace_rights=render_rights(path.ace_rights),
        layer_rights=render_rights(path.layer_rights),
        effective_rights=render_rights(path.effective_rights),
        constrained_rights=render_rights(path.constrained_rights),
        assumed=path.assumed,
        via_group=path.via_group,
        inherited=path.inherited,
    )


def _removal_view(target: RemovalTarget) -> RemovalTargetView:
    return RemovalTargetView(
        edge_id=target.edge_id,
        kind=target.kind,
        source=target.source,
        target=target.target,
        rights_removed=render_rights(target.rights_removed),
        rights_added=render_rights(target.rights_added),
        rights_after=render_rights(target.rights_after),
        revokes_all_access=target.revokes_all_access,
        changes_nothing=target.changes_nothing,
        alternate_paths=list(target.alternate_paths),
        paths_removed=list(target.paths_removed),
    )


def _principal_access_view(
    item: PrincipalAccess, labels: dict[str, PrincipalRecord]
) -> PrincipalAccessView:
    return PrincipalAccessView(
        principal=principal_summary(item.key, item.principal),
        access=item.access.has_access,
        rights=render_rights(item.access.rights),
        certainty=item.access.certainty,
        limiting_layer=item.access.limiting_layer,
        via=[_token_entry_view(entry, labels) for entry in item.via],
        conditions=[condition.value for condition in item.access.conditions],
    )


def _resource_access_view(item: ResolvedAccess) -> ResourceAccessView:
    return ResourceAccessView(
        resource=render_resource_ref(item),
        share=render_share_ref(item),
        access=item.access.has_access,
        rights=render_rights(item.access.rights),
        certainty=item.access.certainty,
        limiting_layer=item.access.limiting_layer,
        conditions=[condition.value for condition in item.access.conditions],
    )


def render_resource_ref(item: ResolvedAccess | ResolvedExplanation) -> ResourceRef:
    row = item.resource
    if row is None:
        return ResourceRef(key=item.access.resource_key, observed=False)
    return ResourceRef(
        key=row.resource_key,
        path=row.path,
        share_key=row.share_key,
        observed=True,
        dacl_present=row.dacl_present,
        dacl_protected=row.dacl_protected,
        is_acl_boundary=row.is_acl_boundary,
        owner_sid=row.owner_sid,
    )


def render_share_ref(item: ResolvedAccess | ResolvedExplanation) -> ShareRef | None:
    if item.access.share_key is None:
        return None
    row = item.share
    if row is None:
        return ShareRef(key=item.access.share_key, observed=False)
    return ShareRef(key=row.share_key, name=row.name, server_key=row.server_key, observed=True)


def _resource_ref_from(page: ResourceAccessPage) -> ResourceRef:
    row = page.resource
    if row is None:
        return ResourceRef(key=page.resource_key, observed=False)
    return ResourceRef(
        key=row.resource_key,
        path=row.path,
        share_key=row.share_key,
        observed=True,
        dacl_present=row.dacl_present,
        dacl_protected=row.dacl_protected,
        is_acl_boundary=row.is_acl_boundary,
        owner_sid=row.owner_sid,
    )


def _share_ref_from(page: ResourceAccessPage) -> ShareRef | None:
    if page.share_key is None:
        return None
    row = page.share
    if row is None:
        return ShareRef(key=page.share_key, observed=False)
    return ShareRef(key=row.share_key, name=row.name, server_key=row.server_key, observed=True)
