"""HTTP response models for persisted privilege-graph traversal."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.relationship_paths import PathState, TraversalPolicy
from app.domain.relationships import (
    DerivationClass,
    EvidenceRole,
    EvidenceState,
    NodeKind,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipScopeKind,
    RelationshipType,
)


class GraphNodeView(BaseModel):
    kind: NodeKind
    key: str


class GraphScopeView(BaseModel):
    kind: RelationshipScopeKind
    key: str


class GraphEvidenceRefView(BaseModel):
    kind: str
    key: str
    role: EvidenceRole
    state: EvidenceState
    first_observed_run_id: str
    last_observed_run_id: str


class GraphRuleView(BaseModel):
    rule_id: str
    version: int


class GraphSupportRefView(BaseModel):
    support_key: str
    rule: GraphRuleView
    basis_token: str
    conditions: list[str]
    qualifiers: list[str]
    evidence: list[GraphEvidenceRefView]


class GraphPathEdgeView(BaseModel):
    relationship_id: str
    relationship_type: RelationshipType
    relationship_class: str
    source: GraphNodeView
    target: GraphNodeView
    scope: GraphScopeView | None = None
    derivation_class: DerivationClass
    confidence: RelationshipConfidence
    currency: RelationshipCurrency
    explanation: str
    support: GraphSupportRefView


class GraphPathView(BaseModel):
    nodes: list[GraphNodeView]
    edges: list[GraphPathEdgeView]
    hop_count: int = Field(ge=0)
    state: PathState
    conditions: list[str]


class GraphLimitsView(BaseModel):
    max_depth: int
    max_nodes: int
    max_edges: int
    max_paths: int


class GraphTraversalDiagnosticsView(BaseModel):
    depth_reached: int
    nodes_reached: int
    edges_read: int
    cycle_edges_skipped: int
    truncation: list[str]
    unknown_currency_count: int
    unknown_currency_relationship_ids: list[str]
    unresolved_count: int
    unresolved_relationship_ids: list[str]
    policy_filtered_count: int
    policy_filtered_relationship_ids: list[str]
    limits: GraphLimitsView


class GraphProjectionView(BaseModel):
    ready: bool
    latest_pass: dict[str, Any] | None
    underived_run_count: int
    underived_run_ids: list[str]
    runs_in_progress_count: int
    runs_in_progress_ids: list[str]
    latest_pass_failed: bool


class GraphCapabilityGapsView(BaseModel):
    not_collected: list[str]
    partially_collected: list[str]
    contract_only: list[str]


class GraphCompletenessView(BaseModel):
    complete_within_supported_graph: bool
    complete_for_enterprise: bool
    reasons: list[str]
    graph_basis_stable: bool
    graph: GraphProjectionView
    collection_capability_gaps: GraphCapabilityGapsView


class ShortestPathView(BaseModel):
    source: GraphNodeView
    target: GraphNodeView
    found: bool
    conclusion: str
    path: GraphPathView | None
    traversal_policy: TraversalPolicy
    diagnostics: GraphTraversalDiagnosticsView
    completeness: GraphCompletenessView


class AlternatePathsView(BaseModel):
    source: GraphNodeView
    target: GraphNodeView
    paths: list[GraphPathView]
    path_count: int
    traversal_policy: TraversalPolicy
    diagnostics: GraphTraversalDiagnosticsView
    completeness: GraphCompletenessView


class ReverseReachedView(BaseModel):
    source: GraphNodeView
    path: GraphPathView


class ReverseReachabilityView(BaseModel):
    target: GraphNodeView
    reached: list[ReverseReachedView]
    result_count: int
    traversal_policy: TraversalPolicy
    diagnostics: GraphTraversalDiagnosticsView
    completeness: GraphCompletenessView


class NeighborView(BaseModel):
    root: GraphNodeView
    nodes: list[GraphNodeView]
    relationships: list[GraphPathEdgeView]
    diagnostics: GraphTraversalDiagnosticsView
    completeness: GraphCompletenessView
