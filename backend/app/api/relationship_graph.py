r"""The privilege-relationship graph: paths, reachability and neighbors (ADG-GRAPH-01C).

Every answer here is a bounded search over the relationships GRAPH-01B derives, and every one
carries what a reader needs to not over-read it:

* **``outcome``** on a path answer — ``found``, ``no_path``, or one of four *inconclusive*
  values (``incomplete_evidence``, ``truncated``, ``boundary``, ``policy_restricted``). An
  empty ``paths`` list is never the answer; ``outcome`` is. ``no_path`` itself is a statement
  about the modeled graph only.
* **``completeness``** — every reason the answer is short of complete, from one closed
  vocabulary, each with a sentence. A listing (reachability, neighbors) carries it too.
* **``coverage``** — what the graph does not model at all: GPOs, trusts, membership
  modification, directory-object control, logon-time token SIDs, the share layer. Present on
  every response, because "not in the graph" is never "not in the estate".
* **``basis``** — the collected state *and* the derivation state the answer was computed
  from, read in the same ``REPEATABLE READ`` snapshot as the search itself.
* **``policy``** — which current relationships the search followed: ``all_current`` (default)
  or ``confirmed_only``. A conditional relationship is never silently dropped and never
  reported as confirmed.

**Nodes are addressed by canonical reference**, ``<kind>:<key>`` — the form every response
renders as ``ref`` — for example ``principal:S-1-5-21-…-1104``,
``principal:fs01|S-1-5-32-544``, ``ntfs_resource:\\fs01\finance\payroll`` or
``domain:S-1-5-21-…``. Keys must already be canonical (case-folded host and path, canonical
SID). A second spelling is refused with a 422, never resolved by guessing, because two
spellings of one key would make two nodes of one object; and a bare BUILTIN SID is the
directory's own group, never one host's — a 404 for it lists the host-local candidates.

**Limits are refused, not clamped.** Each bound has a default and a hard maximum published in
this document, and a value outside it is a 422. Nothing here can be asked to search without a
bound.

Nothing here derives, stores or changes a relationship, and nothing writes to the estate.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Header, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field

from app.api.caching import BasisView, CacheValidator, basis_view, not_modified, validator_for
from app.api.deps import PRINTABLE_IDENTIFIER, Session
from app.api.graph import PrincipalSummary, principal_summary
from app.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    PageInfo,
    decode_offset_cursor,
    encode_offset_cursor,
    normalize_limit,
)
from app.domain.errors import DomainValidationError
from app.domain.relationships import (
    CONDITION_DESCRIPTIONS,
    QUALIFIER_DESCRIPTIONS,
    RELATIONSHIP_TAXONOMY,
    AdjacencyDirection,
    NodeKind,
    NodeRef,
    RelationshipCondition,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipType,
    StoredRelationship,
)
from app.traversal.completeness import (
    REASON_CATEGORY,
    REASON_DESCRIPTIONS,
    CompletenessReason,
    GraphReadiness,
    PathOutcome,
    ReasonCategory,
    classify,
    conclusive,
    ordered,
)
from app.traversal.model import PathNote, TraversalEdge, TraversalPath
from app.traversal.search import (
    MAX_DEPTH_LIMIT,
    MAX_EDGES_LIMIT,
    MAX_NODES_LIMIT,
    MAX_PATHS_LIMIT,
    NEIGHBOR_DEPTH_LIMIT,
    Exploration,
    SearchLimits,
)
from app.traversal.semantics import (
    STORED_TYPES,
    TRAVERSAL_SEMANTICS,
    TraversalPolicy,
    coverage_gaps,
    semantics_of,
)
from app.traversal.service import NodeLabels, RelationshipGraphService, Snapshot

router = APIRouter(prefix="/api/v1/graph", tags=["relationship graph"])

__all__ = ["parse_node", "router"]

_UNPROCESSABLE: Final = 422
_DEFAULTS: Final = SearchLimits()
_BOUNDARY_SAMPLE: Final = 20
"""Boundary nodes named on an answer. The counts are always complete; the list is a sample."""

_EXAMPLES: Final = (
    "principal:S-1-5-21-1004336348-1177238915-682003330-1104, "
    "principal:fs01|S-1-5-32-544, ntfs_resource:\\\\fs01\\finance\\payroll, "
    "domain:S-1-5-21-3000000001-3000000002-3000000003"
)

NodeQuery = Annotated[
    str,
    Query(
        min_length=3,
        max_length=600,
        pattern=PRINTABLE_IDENTIFIER,
        description=f"A canonical node reference, '<kind>:<key>'. For example: {_EXAMPLES}.",
    ),
]
PolicyQuery = Annotated[
    TraversalPolicy,
    Query(
        description=(
            "all_current: follow every current relationship and mark what is conditional or "
            "unresolved. confirmed_only: follow only relationships whose confidence is "
            "confirmed, and count the rest as declined."
        )
    ),
]
MaxDepthQuery = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_DEPTH_LIMIT,
        description=f"Hops a route may have (default {_DEFAULTS.max_depth}, at most "
        f"{MAX_DEPTH_LIMIT}).",
    ),
]
MaxNodesQuery = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_NODES_LIMIT,
        description=f"Nodes the search may reach (default {_DEFAULTS.max_nodes}, at most "
        f"{MAX_NODES_LIMIT}).",
    ),
]
MaxEdgesQuery = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_EDGES_LIMIT,
        description=f"Relationships the search may read (default {_DEFAULTS.max_edges}, at "
        f"most {MAX_EDGES_LIMIT}).",
    ),
]
MaxPathsQuery = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_PATHS_LIMIT,
        description=f"Routes to return (default {_DEFAULTS.max_paths}, at most {MAX_PATHS_LIMIT}).",
    ),
]
LimitQuery = Annotated[
    int | None,
    Query(ge=1, le=MAX_LIMIT, description=f"Items per page (default {DEFAULT_LIMIT})."),
]
CursorQuery = Annotated[
    str | None, Query(description="Opaque cursor from a previous response's next_cursor.")
]
IfNoneMatchHeader = Annotated[
    str | None,
    Header(
        alias="If-None-Match",
        description=(
            "An ETag from a previous response. The server answers 304 when nothing has been "
            "collected or derived since."
        ),
    ),
]


# --------------------------------------------------------------------- views


class GraphScopeView(BaseModel):
    kind: str
    key: str


class GraphNodeView(BaseModel):
    """One node, identified canonically and labelled with whatever is stored about it."""

    ref: str = Field(description="The canonical reference, '<kind>:<key>'. Use it to query.")
    kind: str
    key: str
    label: str = Field(
        description="A display name when one is stored, else the key. Never an identifier."
    )
    present: bool = Field(
        description=(
            "Whether the node's own object has a current row (a principal, a resource). False "
            "for a node only a relationship names: an undescribed member, a domain."
        )
    )
    host: str | None = Field(default=None, description="The host a host-local principal is on.")
    principal: PrincipalSummary | None = None


class GraphVocabularyView(BaseModel):
    code: str
    description: str


class GraphEvidenceRefView(BaseModel):
    kind: str
    key: str


class GraphRelationshipView(BaseModel):
    """One relationship on an answer, with enough to say what it means and why it holds.

    The full support and evidence record is one request away at ``href``.
    """

    relationship_id: str
    relationship_type: str
    label: str
    relationship_class: str
    role: str = Field(description="hop, reading or informational.")
    source: str
    target: str
    scope: GraphScopeView | None
    derivation_class: str
    confidence: str
    currency: str
    conditional: bool
    unresolved: bool
    conditions: list[GraphVocabularyView]
    qualifiers: list[GraphVocabularyView]
    rule: str = Field(description="The rule and version of the support it is as strong as.")
    supports: int = Field(description="Independent ways it is supported.")
    premises: list[GraphEvidenceRefView] = Field(
        description="The stored facts its strongest support rests on."
    )
    explanation: str | None = None
    href: str


class GraphStepView(BaseModel):
    index: int
    source: str
    target: str
    scope: GraphScopeView | None
    basis: str = Field(description="The relationship this step is as strong as.")
    confidence: str
    relationships: list[GraphRelationshipView] = Field(
        description="Every followed relationship joining the two nodes, strongest first."
    )
    readings: list[GraphRelationshipView] = Field(
        description=(
            "Readings of this step's local membership: local administration, Remote Desktop, "
            "WinRM. Their own class and conditions apply; a Remote Desktop reading is never a "
            "statement that a sign-in succeeds."
        )
    )


class GraphPathConditionView(BaseModel):
    code: str
    description: str
    origin: str = Field(description="relationship, reading, or path (known only to the path).")
    step: int
    relationship_id: str | None = None


class GraphAssessmentView(BaseModel):
    """What a path rests on, folded from its steps. Never a score."""

    confidence: str = Field(
        description=(
            "The weakest of: every step's strongest relationship, the readings the path ends "
            "on, and the conditions only the path knows."
        )
    )
    chain_confidence: str = Field(
        description="The relationships alone, without the readings the path ends on."
    )
    conditional: bool
    unresolved: bool
    conditions: list[GraphPathConditionView]
    qualifiers: list[GraphVocabularyView]
    notes: list[GraphVocabularyView] = Field(
        description="What the path does not claim. Never changes its confidence."
    )


class GraphPathView(BaseModel):
    hops: int
    nodes: list[str]
    steps: list[GraphStepView]
    assessment: GraphAssessmentView


class GraphReasonView(BaseModel):
    code: str
    category: str
    description: str


class GraphCompletenessView(BaseModel):
    complete: bool = Field(
        description="False means the answer is short of complete for the reasons listed."
    )
    reasons: list[GraphReasonView]
    truncation: list[str] = Field(description="Which limits stopped the search.")


class GraphLimitsView(BaseModel):
    max_depth: int
    max_nodes: int
    max_edges: int
    max_paths: int | None = None


class GraphExplorationView(BaseModel):
    nodes_reached: int
    relationships_read: int
    reads: int = Field(description="Batched adjacency reads: one per level per direction.")
    depth_forward: int
    depth_backward: int
    unknown_relationships: int = Field(
        description="Met, of unknown currency, and not followed. They may still hold."
    )
    excluded_by_policy: int
    not_current_skipped: int = Field(description="History: not followed.")
    unresolved_boundary: list[str] = Field(
        description=f"Unresolved principals the search reached (up to {_BOUNDARY_SAMPLE})."
    )
    implicit_membership_boundary: list[str] = Field(
        description=f"Logon-time token SIDs the search reached (up to {_BOUNDARY_SAMPLE})."
    )


class GraphCoverageGapView(BaseModel):
    code: str
    summary: str
    relationship_types: list[str]


class GraphCoverageView(BaseModel):
    """What no answer over this graph can see, stated on every answer."""

    statement: str
    gaps: list[GraphCoverageGapView]


class GraphPassView(BaseModel):
    pass_id: str
    trigger: str
    status: str
    source_run_id: str | None
    completed_at: dt.datetime | None


class GraphProjectionView(BaseModel):
    """Whether the relationship graph reflects what was collected."""

    state: str = Field(description="current, behind, failed or empty.")
    token: str = Field(description="Moves whenever a derivation pass is recorded.")
    passes: int
    latest_pass: GraphPassView | None
    underived_runs: int = Field(description="Completed runs no successful pass covered.")
    failed_runs: int = Field(description="Of those, runs whose own derivation pass failed.")
    runs_in_progress: int
    underived_run_ids: list[str] = Field(description="Up to 20, oldest first.")


class GraphBasisView(BaseModel):
    collection: BasisView
    projection: GraphProjectionView
    snapshot: str = Field(
        description=(
            "repeatable_read: the basis, the projection state and every read of the search "
            "came from one snapshot."
        )
    )


class GraphQueryView(BaseModel):
    policy: str


class GraphPathResponse(BaseModel):
    """The shortest route between two nodes, or why there is none to show."""

    basis: GraphBasisView
    query: GraphQueryView
    source: GraphNodeView
    target: GraphNodeView
    outcome: PathOutcome = Field(
        description=(
            "found, no_path, or why nothing was found: incomplete_evidence, truncated, "
            "boundary, policy_restricted. Only no_path supports a negative conclusion, and "
            "only about what the graph models (see coverage)."
        )
    )
    conclusive: bool
    path: GraphPathView | None
    nodes: list[GraphNodeView]
    completeness: GraphCompletenessView
    limits: GraphLimitsView
    exploration: GraphExplorationView
    coverage: GraphCoverageView


class GraphPathsResponse(BaseModel):
    """Every simple route up to a cap, shortest first and then in canonical node order."""

    basis: GraphBasisView
    query: GraphQueryView
    source: GraphNodeView
    target: GraphNodeView
    outcome: PathOutcome
    conclusive: bool
    paths: list[GraphPathView]
    nodes: list[GraphNodeView]
    completeness: GraphCompletenessView
    limits: GraphLimitsView
    exploration: GraphExplorationView
    coverage: GraphCoverageView


class GraphReachedView(BaseModel):
    node: GraphNodeView
    hops: int
    boundary: list[str] = Field(
        description=(
            "unresolved_boundary: who holds this principal lies outside the collected domain. "
            "implicit_membership_boundary: a logon-time token SID — every principal whose "
            "token carries it is behind it."
        )
    )
    path: GraphPathView = Field(description="Its shortest route into the target.")


class GraphReachabilityResponse(BaseModel):
    """Every node with a route into the target, nearest first. A graph primitive: it
    classifies nothing and ranks nothing."""

    basis: GraphBasisView
    query: GraphQueryView
    target: GraphNodeView
    items: list[GraphReachedView]
    page: PageInfo
    completeness: GraphCompletenessView
    limits: GraphLimitsView
    exploration: GraphExplorationView
    coverage: GraphCoverageView


class GraphNeighborView(BaseModel):
    depth: int
    direction: str = Field(description="How the search met it: outbound or inbound.")
    relationship: GraphRelationshipView


class GraphNeighborsResponse(BaseModel):
    basis: GraphBasisView
    query: GraphQueryView
    node: GraphNodeView
    depth: int
    directions: list[str]
    types: list[str]
    items: list[GraphNeighborView]
    nodes: list[GraphNodeView] = Field(description="Every node this page's relationships name.")
    frontier: int = Field(
        description=(
            "Nodes at the last level whose own relationships were not listed: what a deeper "
            "request would expand. Not a truncation; it is outside the depth asked for."
        )
    )
    page: PageInfo
    completeness: GraphCompletenessView
    limits: GraphLimitsView
    exploration: GraphExplorationView
    coverage: GraphCoverageView


class GraphEvidenceView(BaseModel):
    kind: str
    key: str
    role: str
    state: str
    first_observed_at: dt.datetime
    first_observed_run_id: str
    last_observed_at: dt.datetime
    last_observed_run_id: str


class GraphSupportView(BaseModel):
    support_key: str
    derivation_class: str
    rule: str
    confidence: str
    currency: str
    qualifiers: list[GraphVocabularyView]
    conditions: list[GraphVocabularyView]
    attributes: dict[str, Any]
    basis_token: str
    derived_at: dt.datetime
    first_observed_at: dt.datetime
    last_observed_at: dt.datetime
    retired_at: dt.datetime | None
    evidence: list[GraphEvidenceView]


class GraphRelationshipDetailResponse(BaseModel):
    """One relationship and every way it is supported, down to the stored facts."""

    basis: GraphBasisView
    relationship: GraphRelationshipView
    reads_as: str
    note: str
    supports: list[GraphSupportView]
    nodes: list[GraphNodeView]


class GraphSemanticsView(BaseModel):
    relationship_type: str
    label: str
    reads_as: str
    role: str
    relationship_class: str
    privilege_traversable: bool
    reverse_meaningful: bool
    reads: str | None
    always_conditional: bool
    collection: str
    derivation: str
    note: str


class GraphLimitBoundView(BaseModel):
    name: str
    default: int
    maximum: int


class GraphSemanticsResponse(BaseModel):
    """How the path engine reads the graph: one table, the same for every answer."""

    relationship_types: list[GraphSemanticsView]
    policies: list[GraphVocabularyView]
    outcomes: list[GraphVocabularyView]
    reasons: list[GraphReasonView]
    limits: list[GraphLimitBoundView]
    tie_break: str
    coverage: GraphCoverageView


class GraphReadinessResponse(BaseModel):
    basis: GraphBasisView
    reasons: list[GraphReasonView]


# --------------------------------------------------------------------- parsing


def parse_node(value: str, field: str) -> NodeRef:
    """A canonical node reference, or a 422 that says exactly what is wrong with it."""
    kind_text, separator, key = value.partition(":")
    if not separator or not key:
        raise DomainValidationError(
            f"{value!r} is not a node reference. Give '<kind>:<canonical key>', for example "
            f"{_EXAMPLES}.",
            value=value,
            field=field,
        )
    try:
        kind = NodeKind(kind_text)
    except ValueError:
        kinds = ", ".join(item.value for item in NodeKind if not item.is_reserved)
        raise DomainValidationError(
            f"{kind_text!r} is not a node kind. Use one of: {kinds}.", value=value, field=field
        ) from None
    if kind.is_reserved:
        raise DomainValidationError(
            f"No {kind.value} node can exist: ADG collects no group policy object or "
            "directory container, so nothing names one (coverage: "
            "relationship_types_not_collected).",
            value=value,
            field=field,
        )
    try:
        return NodeRef(kind, key)
    except DomainValidationError as exc:
        raise DomainValidationError(str(exc), value=value, field=field) from None


async def _require_known(
    service: RelationshipGraphService, nodes: Sequence[tuple[str, NodeRef]]
) -> None:
    unknown = set(await service.unknown(node for _, node in nodes))
    for field, node in nodes:
        if node not in unknown:
            continue
        candidates = await service.host_scoped_candidates(node)
        detail: dict[str, Any] = {
            "message": (
                f"Nothing is stored about {node}: no current object and no relationship "
                "names it. It may not have been collected yet."
            ),
            "field": field,
        }
        if candidates:
            detail["message"] = (
                f"Nothing is stored about {node}, the directory's own BUILTIN group. A BUILTIN "
                "SID names a host's group only together with its host; use one of the "
                "candidates."
            )
            detail["candidates"] = [str(candidate) for candidate in candidates]
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _limits(
    max_depth: int, max_nodes: int, max_edges: int, max_paths: int | None = None
) -> SearchLimits:
    return SearchLimits(
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_paths=max_paths if max_paths is not None else _DEFAULTS.max_paths,
    )


# --------------------------------------------------------------------- rendering


def _scope_view(scope: RelationshipScope | None) -> GraphScopeView | None:
    return None if scope is None else GraphScopeView(kind=scope.kind.value, key=scope.key)


def _conditions(items: Iterable[RelationshipCondition]) -> list[GraphVocabularyView]:
    return [
        GraphVocabularyView(code=item.value, description=CONDITION_DESCRIPTIONS[item])
        for item in sorted(items)
    ]


def _qualifiers(items: Iterable[RelationshipQualifier]) -> list[GraphVocabularyView]:
    return [
        GraphVocabularyView(code=item.value, description=QUALIFIER_DESCRIPTIONS[item])
        for item in sorted(items)
    ]


class _Renderer:
    """Renders one answer's nodes and relationships from what the request already read."""

    def __init__(self, service: RelationshipGraphService, labels: NodeLabels) -> None:
        self._service = service
        self._labels = labels

    def label(self, node: NodeRef) -> str:
        if node.kind is NodeKind.PRINCIPAL:
            record = self._labels.principals.get(node.key)
            if record is not None:
                for candidate in (
                    record.display_name,
                    record.sam_account_name,
                    record.user_principal_name,
                    record.last_known_name,
                ):
                    if candidate:
                        return candidate
            sid = node.sid
            name = sid.well_known_name if sid is not None else None
            if name:
                return f"{node.host}\\{name}" if node.host else name
        if node.kind is NodeKind.NTFS_RESOURCE:
            resource = self._labels.resources.get(node.key)
            if resource is not None:
                return resource.path
        return node.key

    def node(self, node: NodeRef) -> GraphNodeView:
        principal = None
        if node.kind is NodeKind.PRINCIPAL:
            principal = principal_summary(node.key, self._labels.principals.get(node.key))
        return GraphNodeView(
            ref=str(node),
            kind=node.kind.value,
            key=node.key,
            label=self.label(node),
            present=node in self._labels.present,
            host=node.host,
            principal=principal,
        )

    def nodes(self, nodes: Iterable[NodeRef]) -> list[GraphNodeView]:
        return [self.node(node) for node in sorted(set(nodes))]

    def explanation(self, relationship_id: str) -> str | None:
        stored = self._service.loaded(relationship_id)
        if stored is None:
            return None
        return stored.explanation(
            {node: self.label(node) for node in (stored.source, stored.target)}
        )

    def relationship(self, edge: TraversalEdge) -> GraphRelationshipView:
        semantics = edge.semantics
        return GraphRelationshipView(
            relationship_id=edge.relationship_id,
            relationship_type=edge.relationship_type.value,
            label=semantics.label,
            relationship_class=semantics.relationship_class.value,
            role=semantics.role.value,
            source=str(edge.source),
            target=str(edge.target),
            scope=_scope_view(edge.scope),
            derivation_class=edge.derivation_class.value,
            confidence=edge.confidence.value,
            currency=edge.currency.value,
            conditional=edge.conditional,
            unresolved=edge.unresolved,
            conditions=_conditions(edge.conditions),
            qualifiers=_qualifiers(edge.qualifiers),
            rule=edge.rule,
            supports=edge.supports,
            premises=[
                GraphEvidenceRefView(kind=kind.value, key=key) for kind, key in edge.premises
            ],
            explanation=self.explanation(edge.relationship_id),
            href=f"/api/v1/graph/relationships/{edge.relationship_id}",
        )

    def path(self, path: TraversalPath) -> GraphPathView:
        assessment = path.assessment
        return GraphPathView(
            hops=path.hops,
            nodes=[str(node) for node in path.nodes],
            steps=[
                GraphStepView(
                    index=index,
                    source=str(step.source),
                    target=str(step.target),
                    scope=_scope_view(step.scope),
                    basis=step.basis.relationship_id,
                    confidence=step.basis.confidence.value,
                    relationships=[self.relationship(edge) for edge in step.relationships],
                    readings=[self.relationship(edge) for edge in step.readings],
                )
                for index, step in enumerate(path.steps)
            ],
            assessment=GraphAssessmentView(
                confidence=assessment.confidence.value,
                chain_confidence=assessment.chain_confidence.value,
                conditional=assessment.conditional,
                unresolved=assessment.unresolved,
                conditions=[
                    GraphPathConditionView(
                        code=item.condition.value,
                        description=CONDITION_DESCRIPTIONS[item.condition],
                        origin=item.origin.value,
                        step=item.step,
                        relationship_id=item.relationship_id,
                    )
                    for item in assessment.conditions
                ],
                qualifiers=_qualifiers(assessment.qualifiers),
                notes=[
                    GraphVocabularyView(code=note.value, description=_NOTE_DESCRIPTIONS[note])
                    for note in assessment.notes
                ],
            ),
        )


_NOTE_DESCRIPTIONS: Final[Mapping[PathNote, str]] = {
    PathNote.SHARE_LAYER_NOT_EVALUATED: (
        "The path ends at an NTFS resource and states NTFS control. Reaching it over SMB also "
        "passes the share's permissions, which /api/v1/access/explain evaluates."
    ),
}


def _reasons(reasons: Iterable[CompletenessReason]) -> list[GraphReasonView]:
    return [
        GraphReasonView(
            code=reason.value,
            category=REASON_CATEGORY[reason].value,
            description=REASON_DESCRIPTIONS[reason],
        )
        for reason in ordered(reasons)
    ]


def _completeness(reasons: Sequence[CompletenessReason]) -> GraphCompletenessView:
    weakening = [
        reason for reason in reasons if REASON_CATEGORY[reason] is not ReasonCategory.NOTICE
    ]
    return GraphCompletenessView(
        complete=not weakening,
        reasons=_reasons(reasons),
        truncation=[
            reason.value
            for reason in ordered(reasons)
            if REASON_CATEGORY[reason] is ReasonCategory.TRUNCATION
        ],
    )


def _limits_view(limits: SearchLimits, *, paths: bool = False) -> GraphLimitsView:
    return GraphLimitsView(
        max_depth=limits.max_depth,
        max_nodes=limits.max_nodes,
        max_edges=limits.max_edges,
        max_paths=limits.max_paths if paths else None,
    )


def _sample(nodes: Iterable[NodeRef]) -> list[str]:
    return [str(node) for node in sorted(nodes)[:_BOUNDARY_SAMPLE]]


def _exploration(seen: Exploration) -> GraphExplorationView:
    return GraphExplorationView(
        nodes_reached=seen.nodes_reached,
        relationships_read=seen.relationships_read,
        reads=seen.reads,
        depth_forward=seen.depth_forward,
        depth_backward=seen.depth_backward,
        unknown_relationships=len(seen.unknown),
        excluded_by_policy=len(seen.excluded),
        not_current_skipped=len(seen.not_current),
        unresolved_boundary=_sample(seen.unresolved_nodes),
        implicit_membership_boundary=_sample(seen.implicit_nodes),
    )


_COVERAGE_STATEMENT: Final = (
    "This answer covers memberships, host-local memberships and their readings, and NTFS "
    "ownership and control, as derived from collected facts. It says nothing about what the "
    "gaps below would add: an answer without them is not evidence of their absence."
)


def _coverage() -> GraphCoverageView:
    return GraphCoverageView(
        statement=_COVERAGE_STATEMENT,
        gaps=[
            GraphCoverageGapView(
                code=gap.code,
                summary=gap.summary,
                relationship_types=[item.value for item in gap.relationship_types],
            )
            for gap in coverage_gaps()
        ],
    )


def _projection_view(readiness: GraphReadiness) -> GraphProjectionView:
    latest = readiness.latest_pass
    latest_view = None
    if latest is not None:
        source_run = latest.get("source_run_id")
        latest_view = GraphPassView(
            pass_id=str(latest.get("pass_id")),
            trigger=str(latest.get("trigger")),
            status=str(latest.get("status")),
            source_run_id=None if source_run is None else str(source_run),
            completed_at=latest.get("completed_at"),
        )
    return GraphProjectionView(
        state=readiness.state,
        token=readiness.token,
        passes=readiness.passes,
        latest_pass=latest_view,
        underived_runs=len(readiness.underived_runs),
        failed_runs=len(readiness.failed_runs),
        runs_in_progress=len(readiness.runs_in_progress),
        underived_run_ids=[str(run) for run in readiness.underived_runs[:20]],
    )


def _basis(snapshot: Snapshot) -> GraphBasisView:
    return GraphBasisView(
        collection=basis_view(snapshot.basis),
        projection=_projection_view(snapshot.readiness),
        snapshot=snapshot.isolation,
    )


async def _open(
    service: RelationshipGraphService,
    *,
    route: str,
    parameters: Mapping[str, Any],
    if_none_match: str | None,
) -> tuple[Snapshot, CacheValidator, Response | None]:
    """Open the snapshot and decide 304 before any search runs.

    The validator covers the collection basis *and* the projection token: rebuilding the graph
    changes no scan run, and an answer from before a rebuild must not be served after it.
    """
    snapshot = await service.open()
    validator = validator_for(
        snapshot.basis,
        route=route,
        parameters={**parameters, "projection": snapshot.readiness.token},
    )
    if validator.matches(if_none_match):
        return snapshot, validator, not_modified(validator)
    return snapshot, validator, None


# --------------------------------------------------------------------- routes


@router.get(
    "/semantics",
    response_model=GraphSemanticsResponse,
    summary="How the path engine reads each relationship type, and what it does not model",
)
async def semantics() -> GraphSemanticsResponse:
    return GraphSemanticsResponse(
        relationship_types=[
            GraphSemanticsView(
                relationship_type=item.relationship_type.value,
                label=item.label,
                reads_as=RELATIONSHIP_TAXONOMY[item.relationship_type].reads_as,
                role=item.role.value,
                relationship_class=item.relationship_class.value,
                privilege_traversable=item.privilege_traversable,
                reverse_meaningful=item.reverse_meaningful,
                reads=None if item.reads is None else item.reads.value,
                always_conditional=item.always_conditional,
                collection=RELATIONSHIP_TAXONOMY[item.relationship_type].collection.status.value,
                derivation=RELATIONSHIP_TAXONOMY[item.relationship_type].derivation.status.value,
                note=item.note,
            )
            for item in sorted(
                TRAVERSAL_SEMANTICS.values(), key=lambda entry: entry.relationship_type.value
            )
        ],
        policies=[
            GraphVocabularyView(code=policy.value, description=_POLICY_DESCRIPTIONS[policy])
            for policy in TraversalPolicy
        ],
        outcomes=[
            GraphVocabularyView(code=outcome.value, description=_OUTCOME_DESCRIPTIONS[outcome])
            for outcome in PathOutcome
        ],
        reasons=_reasons(CompletenessReason),
        limits=[
            GraphLimitBoundView(
                name="max_depth", default=_DEFAULTS.max_depth, maximum=MAX_DEPTH_LIMIT
            ),
            GraphLimitBoundView(
                name="max_nodes", default=_DEFAULTS.max_nodes, maximum=MAX_NODES_LIMIT
            ),
            GraphLimitBoundView(
                name="max_edges", default=_DEFAULTS.max_edges, maximum=MAX_EDGES_LIMIT
            ),
            GraphLimitBoundView(
                name="max_paths", default=_DEFAULTS.max_paths, maximum=MAX_PATHS_LIMIT
            ),
            GraphLimitBoundView(name="neighbor_depth", default=1, maximum=NEIGHBOR_DEPTH_LIMIT),
            GraphLimitBoundView(name="page_size", default=DEFAULT_LIMIT, maximum=MAX_LIMIT),
        ],
        tie_break=_TIE_BREAK,
        coverage=_coverage(),
    )


_TIE_BREAK: Final = (
    "Among routes of equal length, the one whose node sequence is lexicographically smallest, "
    "comparing (node kind, canonical key) pairs from the source. Alternate routes are ordered "
    "by length, then by that order. Display names, insertion order and database row order "
    "never decide."
)

_POLICY_DESCRIPTIONS: Final[Mapping[TraversalPolicy, str]] = {
    TraversalPolicy.ALL_CURRENT: (
        "Follow every current relationship. A route that rests on a conditional or unresolved "
        "one says so on the step and on the path."
    ),
    TraversalPolicy.CONFIRMED_ONLY: (
        "Follow only current relationships whose confidence is confirmed. Every one declined "
        "is counted, and an answer that declined any is never a conclusive no_path."
    ),
}

_OUTCOME_DESCRIPTIONS: Final[Mapping[PathOutcome, str]] = {
    PathOutcome.FOUND: "At least one route exists in the graph.",
    PathOutcome.NO_PATH: (
        "No route exists in the modeled graph: the search finished within its limits over a "
        "current graph and met no boundary. Says nothing about what coverage lists."
    ),
    PathOutcome.INCOMPLETE_EVIDENCE: (
        "Nothing found, and the graph does not reflect everything collected, or nothing was."
    ),
    PathOutcome.TRUNCATED: "Nothing found before a limit stopped the search.",
    PathOutcome.BOUNDARY: "Nothing found, and the search met something it cannot see past.",
    PathOutcome.POLICY_RESTRICTED: (
        "Nothing found through the relationships the policy follows; others exist."
    ),
}


@router.get(
    "/readiness",
    response_model=GraphReadinessResponse,
    summary="Whether the relationship graph reflects what was collected",
)
async def readiness(session: Session) -> GraphReadinessResponse:
    snapshot = await RelationshipGraphService(session).open()
    return GraphReadinessResponse(
        basis=_basis(snapshot), reasons=_reasons(snapshot.readiness.reasons)
    )


@router.get(
    "/paths/shortest",
    response_model=GraphPathResponse,
    summary="The shortest route from one node to another",
    responses={
        304: {"description": "Nothing was collected or derived since the ETag was issued."},
        404: {"description": "Nothing is stored about a node."},
        422: {
            "description": "A malformed reference, the same node twice, or a limit out of range."
        },
    },
)
async def shortest(
    session: Session,
    response: Response,
    source: NodeQuery,
    target: NodeQuery,
    policy: PolicyQuery = TraversalPolicy.ALL_CURRENT,
    max_depth: MaxDepthQuery = _DEFAULTS.max_depth,
    max_nodes: MaxNodesQuery = _DEFAULTS.max_nodes,
    max_edges: MaxEdgesQuery = _DEFAULTS.max_edges,
    if_none_match: IfNoneMatchHeader = None,
) -> GraphPathResponse | Response:
    """The lexicographically first of the shortest routes, with every relationship on it.

    Breadth-first from both ends, one batched read per level. A route is a sequence of nodes;
    each step lists every followed relationship joining its two nodes, strongest first, and
    the readings of a local membership. The path's ``assessment`` is the weakest of its steps,
    the readings it ends on, and the conditions only a path knows.
    """
    source_node, target_node = parse_node(source, "source"), parse_node(target, "target")
    if source_node == target_node:
        raise DomainValidationError(
            f"{source_node} is both the source and the target. A path joins two distinct nodes.",
            value=target,
            field="target",
        )
    limits = _limits(max_depth, max_nodes, max_edges)
    service = RelationshipGraphService(session)
    snapshot, validator, cached = await _open(
        service,
        route="graph.paths.shortest",
        parameters={
            "source": str(source_node),
            "target": str(target_node),
            "policy": policy.value,
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
        },
        if_none_match=if_none_match,
    )
    if cached is not None:
        return cached
    await _require_known(service, (("source", source_node), ("target", target_node)))
    search = await service.shortest_path(source_node, target_node, policy=policy, limits=limits)
    reasons = ordered((*snapshot.readiness.reasons, *search.reasons))
    outcome = classify(bool(search.paths), reasons)
    path_nodes = {node for path in search.paths for node in path.nodes}
    renderer = _Renderer(service, await service.labels({source_node, target_node, *path_nodes}))
    validator.apply(response)
    return GraphPathResponse(
        basis=_basis(snapshot),
        query=GraphQueryView(policy=policy.value),
        source=renderer.node(source_node),
        target=renderer.node(target_node),
        outcome=outcome,
        conclusive=conclusive(outcome, reasons),
        path=renderer.path(search.paths[0]) if search.paths else None,
        nodes=renderer.nodes(path_nodes),
        completeness=_completeness(reasons),
        limits=_limits_view(limits),
        exploration=_exploration(search.exploration),
        coverage=_coverage(),
    )


@router.get(
    "/paths",
    response_model=GraphPathsResponse,
    summary="Every simple route from one node to another, up to a cap",
    responses={
        304: {"description": "Nothing was collected or derived since the ETag was issued."},
        404: {"description": "Nothing is stored about a node."},
        422: {
            "description": "A malformed reference, the same node twice, or a limit out of range."
        },
    },
)
async def paths(
    session: Session,
    response: Response,
    source: NodeQuery,
    target: NodeQuery,
    policy: PolicyQuery = TraversalPolicy.ALL_CURRENT,
    max_depth: MaxDepthQuery = _DEFAULTS.max_depth,
    max_paths: MaxPathsQuery = _DEFAULTS.max_paths,
    max_nodes: MaxNodesQuery = _DEFAULTS.max_nodes,
    max_edges: MaxEdgesQuery = _DEFAULTS.max_edges,
    if_none_match: IfNoneMatchHeader = None,
) -> GraphPathsResponse | Response:
    """Bounded alternate routes: shortest first, then in canonical node order.

    Simple routes only — no node twice — and never the same route twice, however many
    relationships join a pair of its nodes. ``max_paths`` and ``max_depth`` bound what is
    returned, and a fixed work budget bounds what is tried; ``completeness`` names whichever
    stopped it. This is graph traversal and nothing else: it ranks no route and removes none.
    """
    source_node, target_node = parse_node(source, "source"), parse_node(target, "target")
    if source_node == target_node:
        raise DomainValidationError(
            f"{source_node} is both the source and the target. A path joins two distinct nodes.",
            value=target,
            field="target",
        )
    limits = _limits(max_depth, max_nodes, max_edges, max_paths)
    service = RelationshipGraphService(session)
    snapshot, validator, cached = await _open(
        service,
        route="graph.paths",
        parameters={
            "source": str(source_node),
            "target": str(target_node),
            "policy": policy.value,
            "max_depth": max_depth,
            "max_paths": max_paths,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
        },
        if_none_match=if_none_match,
    )
    if cached is not None:
        return cached
    await _require_known(service, (("source", source_node), ("target", target_node)))
    search = await service.alternate_paths(source_node, target_node, policy=policy, limits=limits)
    reasons = ordered((*snapshot.readiness.reasons, *search.reasons))
    outcome = classify(bool(search.paths), reasons)
    path_nodes = {node for path in search.paths for node in path.nodes}
    renderer = _Renderer(service, await service.labels({source_node, target_node, *path_nodes}))
    validator.apply(response)
    return GraphPathsResponse(
        basis=_basis(snapshot),
        query=GraphQueryView(policy=policy.value),
        source=renderer.node(source_node),
        target=renderer.node(target_node),
        outcome=outcome,
        conclusive=conclusive(outcome, reasons),
        paths=[renderer.path(path) for path in search.paths],
        nodes=renderer.nodes(path_nodes),
        completeness=_completeness(reasons),
        limits=_limits_view(limits, paths=True),
        exploration=_exploration(search.exploration),
        coverage=_coverage(),
    )


@router.get(
    "/reachability",
    response_model=GraphReachabilityResponse,
    summary="Every node with a route into a target, nearest first",
    responses={
        304: {"description": "Nothing was collected or derived since the ETag was issued."},
        404: {"description": "Nothing is stored about the target."},
        422: {"description": "A malformed reference, a limit out of range, or a bad cursor."},
    },
)
async def reachability(
    session: Session,
    response: Response,
    target: NodeQuery,
    policy: PolicyQuery = TraversalPolicy.ALL_CURRENT,
    max_depth: MaxDepthQuery = _DEFAULTS.max_depth,
    max_nodes: MaxNodesQuery = _DEFAULTS.max_nodes,
    max_edges: MaxEdgesQuery = _DEFAULTS.max_edges,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> GraphReachabilityResponse | Response:
    """Reverse reachability: who holds a route into ``target``.

    The relationships are walked target to source, and each route is still reported source to
    target, in the direction the privilege flows. Results are ordered by hops, then by
    canonical reference, and paged with an offset cursor over the bounded result; the search
    re-runs per page, so ``completeness`` travels with every page. ``max_nodes`` caps how many
    sources are found at all. A source that is a foreign principal, or a logon-time SID such
    as Authenticated Users, is a boundary rather than an end: every principal behind it is.
    """
    target_node = parse_node(target, "target")
    limits = _limits(max_depth, max_nodes, max_edges)
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    service = RelationshipGraphService(session)
    snapshot, validator, cached = await _open(
        service,
        route="graph.reachability",
        parameters={
            "target": str(target_node),
            "policy": policy.value,
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "limit": page_size,
            "cursor": cursor,
        },
        if_none_match=if_none_match,
    )
    if cached is not None:
        return cached
    await _require_known(service, (("target", target_node),))
    found = await service.reverse_reachability(target_node, policy=policy, limits=limits)
    window = found.sources[offset : offset + page_size]
    has_more = len(found.sources) > offset + page_size
    routes = {item.node: found.path_from(item.node) for item in window}
    named = {node for route in routes.values() for node in route.nodes} | {target_node}
    renderer = _Renderer(service, await service.labels(named))
    reasons = ordered((*snapshot.readiness.reasons, *found.reasons))
    validator.apply(response)
    return GraphReachabilityResponse(
        basis=_basis(snapshot),
        query=GraphQueryView(policy=policy.value),
        target=renderer.node(target_node),
        items=[
            GraphReachedView(
                node=renderer.node(item.node),
                hops=item.hops,
                boundary=[
                    code
                    for code, flagged in (
                        (
                            CompletenessReason.UNRESOLVED_BOUNDARY.value,
                            item.unresolved_boundary,
                        ),
                        (
                            CompletenessReason.IMPLICIT_MEMBERSHIP_BOUNDARY.value,
                            item.implicit_membership,
                        ),
                    )
                    if flagged
                ],
                path=renderer.path(routes[item.node]),
            )
            for item in window
        ],
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
            total=len(found.sources),
        ),
        completeness=_completeness(reasons),
        limits=_limits_view(limits),
        exploration=_exploration(found.exploration),
        coverage=_coverage(),
    )


_DIRECTIONS: Final[Mapping[str, tuple[AdjacencyDirection, ...]]] = {
    "outbound": (AdjacencyDirection.OUTBOUND,),
    "inbound": (AdjacencyDirection.INBOUND,),
    "both": (AdjacencyDirection.OUTBOUND, AdjacencyDirection.INBOUND),
}


@router.get(
    "/neighbors",
    response_model=GraphNeighborsResponse,
    summary="The current relationships around one node, a bounded number of levels out",
    responses={
        304: {"description": "Nothing was collected or derived since the ETag was issued."},
        404: {"description": "Nothing is stored about the node."},
        422: {"description": "A malformed reference, a type that is never stored, or a limit."},
    },
)
async def neighbor_relationships(
    session: Session,
    response: Response,
    node: NodeQuery,
    direction: Annotated[
        Literal["outbound", "inbound", "both"],
        Query(
            description=(
                "outbound: relationships whose source is the node. inbound: whose target is "
                "the node. both (default)."
            )
        ),
    ] = "both",
    relationship_type: Annotated[
        list[RelationshipType] | None,
        Query(
            alias="type",
            description=(
                "Repeatable. Default: every type the graph stores (hops, readings and "
                "informational relationships)."
            ),
        ),
    ] = None,
    depth: Annotated[
        int,
        Query(
            ge=1,
            le=NEIGHBOR_DEPTH_LIMIT,
            description=f"Levels out (default 1, at most {NEIGHBOR_DEPTH_LIMIT}).",
        ),
    ] = 1,
    policy: PolicyQuery = TraversalPolicy.ALL_CURRENT,
    max_nodes: MaxNodesQuery = _DEFAULTS.max_nodes,
    max_edges: MaxEdgesQuery = _DEFAULTS.max_edges,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> GraphNeighborsResponse | Response:
    """Bounded neighbor exploration, for a client that draws the graph around a node.

    Lists every current relationship of the requested types — hops, readings, and
    informational ones such as ``foreign_principal_of`` — with the level and the direction it
    was met at. Relationships of unknown currency are counted and not listed; ones that are
    no longer current are history and are not listed either. Ordered by level, type and id,
    and paged with an offset cursor over the bounded result.
    """
    center = parse_node(node, "node")
    types = frozenset(relationship_type) if relationship_type else STORED_TYPES
    never = sorted(item.value for item in types - STORED_TYPES)
    if never:
        raise DomainValidationError(
            f"{', '.join(never)} is never stored: nested_member_of is a path (use "
            "/api/v1/graph/paths), and a not-collected type has no relationship to list.",
            value=never,
            field="type",
        )
    limits = _limits(_DEFAULTS.max_depth, max_nodes, max_edges)
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    service = RelationshipGraphService(session)
    snapshot, validator, cached = await _open(
        service,
        route="graph.neighbors",
        parameters={
            "node": str(center),
            "direction": direction,
            "type": sorted(item.value for item in types),
            "depth": depth,
            "policy": policy.value,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "limit": page_size,
            "cursor": cursor,
        },
        if_none_match=if_none_match,
    )
    if cached is not None:
        return cached
    await _require_known(service, (("node", center),))
    found = await service.neighbors(
        center,
        directions=_DIRECTIONS[direction],
        types=types,
        depth=depth,
        policy=policy,
        limits=limits,
    )
    window = found.relationships[offset : offset + page_size]
    has_more = len(found.relationships) > offset + page_size
    named = {center} | {
        endpoint for item in window for endpoint in (item.edge.source, item.edge.target)
    }
    renderer = _Renderer(service, await service.labels(named))
    reasons = ordered((*snapshot.readiness.reasons, *found.reasons))
    validator.apply(response)
    return GraphNeighborsResponse(
        basis=_basis(snapshot),
        query=GraphQueryView(policy=policy.value),
        node=renderer.node(center),
        depth=depth,
        directions=[item.value for item in found.directions],
        types=sorted(item.value for item in types),
        items=[
            GraphNeighborView(
                depth=item.depth,
                direction=item.direction.value,
                relationship=renderer.relationship(item.edge),
            )
            for item in window
        ],
        nodes=renderer.nodes(named),
        frontier=found.frontier,
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
            total=len(found.relationships),
        ),
        completeness=_completeness(reasons),
        limits=GraphLimitsView(max_depth=depth, max_nodes=max_nodes, max_edges=max_edges),
        exploration=_exploration(found.exploration),
        coverage=_coverage(),
    )


@router.get(
    "/relationships/{relationship_id}",
    response_model=GraphRelationshipDetailResponse,
    summary="One relationship, every way it is supported, and the stored facts behind each",
    responses={404: {"description": "No relationship has this id."}},
)
async def relationship_detail(
    session: Session,
    relationship_id: Annotated[
        str,
        Path(
            pattern="^[0-9a-f]{64}$",
            description="The relationship's identity digest, as every path edge names it.",
        ),
    ],
) -> GraphRelationshipDetailResponse:
    """The evidence behind an edge: its supports, their rules and conditions, and each
    premise's stored state. Current, not current and unknown supports are all shown, because
    what used to hold, and until when, stays explainable."""
    service = RelationshipGraphService(session)
    snapshot = await service.open()
    stored = await service.relationship(relationship_id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No relationship has the id {relationship_id}.",
        )
    renderer = _Renderer(service, await service.labels((stored.source, stored.target)))
    edge = TraversalEdge.of(stored)
    view = renderer.relationship(edge).model_copy(
        update={"explanation": _explain(stored, renderer)}
    )
    definition = RELATIONSHIP_TAXONOMY[stored.relationship_type]
    return GraphRelationshipDetailResponse(
        basis=_basis(snapshot),
        relationship=view,
        reads_as=definition.reads_as,
        note=semantics_of(stored.relationship_type).note,
        supports=[
            GraphSupportView(
                support_key=support.support_key,
                derivation_class=support.derivation_class.value,
                rule=str(support.rule),
                confidence=support.confidence.value,
                currency=support.currency.value,
                qualifiers=_qualifiers(support.qualifiers),
                conditions=_conditions(support.conditions),
                attributes=dict(support.attributes),
                basis_token=support.basis_token,
                derived_at=support.derived_at,
                first_observed_at=support.first_observed_at,
                last_observed_at=support.last_observed_at,
                retired_at=support.retired_at,
                evidence=[
                    GraphEvidenceView(
                        kind=item.kind.value,
                        key=item.key,
                        role=item.role.value,
                        state=item.state.value,
                        first_observed_at=item.first_observed_at,
                        first_observed_run_id=str(item.first_observed_run_id),
                        last_observed_at=item.last_observed_at,
                        last_observed_run_id=str(item.last_observed_run_id),
                    )
                    for item in support.evidence
                ],
            )
            for support in stored.supports
        ],
        nodes=renderer.nodes((stored.source, stored.target)),
    )


def _explain(stored: StoredRelationship, renderer: _Renderer) -> str:
    return stored.explanation(
        {node: renderer.label(node) for node in (stored.source, stored.target)}
    )
