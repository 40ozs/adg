"""Application service for GRAPH-01C persisted-relationship traversal.

It wraps the pure bounded algorithms with PostgreSQL adjacency, projector readiness and a
stable response shape. Readiness is sampled before and after each logical traversal: if a
collector/projector pass changes the basis mid-request, the answer remains evidence-traceable
but is explicitly incomplete rather than pretending to be a coherent snapshot.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.graph import TraversalLimits
from app.domain.relationship_paths import (
    TRAVERSAL_SEMANTICS,
    NeighborResult,
    RelationshipPath,
    ReverseReachabilityResult,
    ShortestPathResult,
    TraversalDiagnostics,
    TraversalPolicy,
    alternate_paths,
    capability_gaps,
    explore_neighbors,
    reverse_reachability,
    shortest_path,
)
from app.domain.relationships import (
    AdjacencyDirection,
    NodeRef,
    ProjectionStatus,
    RelationshipType,
    StoredRelationship,
)
from app.relationships.projector import ProjectionState, RelationshipProjector
from app.repositories.relationship_paths import RelationshipPathRepository

_DIAGNOSTIC_SAMPLE = 50


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        if isinstance(value, dt.datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    return value


def _projection_view(state: ProjectionState) -> dict[str, Any]:
    latest = state.latest_pass
    latest_view: dict[str, Any] | None = None
    if latest is not None:
        keys = (
            "pass_id",
            "trigger",
            "status",
            "source_run_id",
            "basis_token",
            "started_at",
            "completed_at",
            "error",
        )
        latest_view = {
            key: _json_scalar(latest[key])
            for key in keys
            if key in latest and latest[key] is not None
        }
    latest_failed = bool(
        latest is not None and latest.get("status") == ProjectionStatus.FAILED.value
    )
    return {
        "ready": state.is_current and not latest_failed,
        "latest_pass": latest_view,
        "underived_run_count": len(state.underived_runs),
        "underived_run_ids": [str(item) for item in state.underived_runs[:_DIAGNOSTIC_SAMPLE]],
        "runs_in_progress_count": len(state.runs_in_progress),
        "runs_in_progress_ids": [
            str(item) for item in state.runs_in_progress[:_DIAGNOSTIC_SAMPLE]
        ],
        "latest_pass_failed": latest_failed,
    }


def _projection_token(state: ProjectionState) -> tuple[Any, ...]:
    latest = state.latest_pass
    return (
        None if latest is None else str(latest.get("pass_id")),
        None if latest is None else str(latest.get("status")),
        None if latest is None else str(latest.get("basis_token")),
        tuple(str(item) for item in state.underived_runs),
        tuple(str(item) for item in state.runs_in_progress),
    )


def _edge_view(relationship: StoredRelationship) -> dict[str, Any]:
    best = relationship.best_support
    return {
        "relationship_id": relationship.relationship_id,
        "relationship_type": relationship.relationship_type.value,
        "relationship_class": TRAVERSAL_SEMANTICS[
            relationship.relationship_type
        ].relationship_class.value,
        "source": {"kind": relationship.source.kind.value, "key": relationship.source.key},
        "target": {"kind": relationship.target.kind.value, "key": relationship.target.key},
        "scope": (
            None
            if relationship.scope is None
            else {"kind": relationship.scope.kind.value, "key": relationship.scope.key}
        ),
        "derivation_class": relationship.derivation_class.value,
        "confidence": relationship.confidence.value,
        "currency": relationship.currency.value,
        "explanation": relationship.explanation(),
        "support": {
            "support_key": best.support_key,
            "rule": {"rule_id": best.rule.rule_id, "version": best.rule.version},
            "basis_token": best.basis_token,
            "conditions": sorted(item.value for item in best.conditions),
            "qualifiers": sorted(item.value for item in best.qualifiers),
            "evidence": [
                {
                    "kind": item.kind.value,
                    "key": item.key,
                    "role": item.role.value,
                    "state": item.state.value,
                    "first_observed_run_id": str(item.first_observed_run_id),
                    "last_observed_run_id": str(item.last_observed_run_id),
                }
                for item in sorted(best.evidence, key=lambda item: (item.kind.value, item.key))
            ],
        },
    }


def _path_view(path: RelationshipPath) -> dict[str, Any]:
    return {
        "nodes": [{"kind": node.kind.value, "key": node.key} for node in path.nodes],
        "edges": [_edge_view(edge) for edge in path.edges],
        "hop_count": path.hop_count,
        "state": path.state.value,
        "conditions": list(path.conditions),
    }


def _diagnostics_view(diagnostics: TraversalDiagnostics) -> dict[str, Any]:
    return {
        "depth_reached": diagnostics.depth_reached,
        "nodes_reached": diagnostics.nodes_reached,
        "edges_read": diagnostics.edges_read,
        "cycle_edges_skipped": diagnostics.cycle_edges_skipped,
        "truncation": sorted(item.value for item in diagnostics.truncation),
        "unknown_currency_count": len(diagnostics.unknown_currency),
        "unknown_currency_relationship_ids": sorted(diagnostics.unknown_currency)[
            :_DIAGNOSTIC_SAMPLE
        ],
        "unresolved_count": len(diagnostics.unresolved),
        "unresolved_relationship_ids": sorted(diagnostics.unresolved)[:_DIAGNOSTIC_SAMPLE],
        "policy_filtered_count": len(diagnostics.policy_filtered),
        "policy_filtered_relationship_ids": sorted(diagnostics.policy_filtered)[
            :_DIAGNOSTIC_SAMPLE
        ],
        "limits": {
            "max_depth": diagnostics.limits.max_depth,
            "max_nodes": diagnostics.limits.max_nodes,
            "max_edges": diagnostics.limits.max_edges,
            "max_paths": diagnostics.limits.max_paths,
        },
    }


def _completeness(
    diagnostics: TraversalDiagnostics,
    before: ProjectionState,
    after: ProjectionState,
) -> dict[str, Any]:
    basis_stable = _projection_token(before) == _projection_token(after)
    readiness = _projection_view(after)
    gaps = capability_gaps()
    supported_complete = (
        diagnostics.complete_within_supported_graph
        and bool(readiness["ready"])
        and basis_stable
    )
    enterprise_complete = supported_complete and not any(gaps.values())
    reasons: list[str] = []
    if not readiness["ready"]:
        reasons.append("graph_not_ready")
    if not basis_stable:
        reasons.append("graph_basis_changed_during_query")
    if diagnostics.truncation:
        reasons.append("traversal_truncated")
    if diagnostics.unknown_currency:
        reasons.append("unknown_relationship_currency")
    if diagnostics.unresolved:
        reasons.append("unresolved_relationship_boundary")
    if gaps["not_collected"]:
        reasons.append("relationship_families_not_collected")
    if gaps["partially_collected"]:
        reasons.append("relationship_families_partially_collected")
    if gaps["contract_only"]:
        reasons.append("relationship_families_contract_only")
    return {
        "complete_within_supported_graph": supported_complete,
        "complete_for_enterprise": enterprise_complete,
        "reasons": reasons,
        "graph_basis_stable": basis_stable,
        "graph": readiness,
        "collection_capability_gaps": {key: list(value) for key, value in gaps.items()},
    }


class RelationshipPathService:
    """One request-scoped traversal service."""

    def __init__(self, session: AsyncSession) -> None:
        self._provider = RelationshipPathRepository(session)
        self._projector = RelationshipProjector(session)

    async def shortest(
        self,
        source: NodeRef,
        target: NodeRef,
        *,
        policy: TraversalPolicy,
        limits: TraversalLimits,
    ) -> dict[str, Any]:
        before = await self._projector.state()
        result = await shortest_path(
            self._provider, source, target, policy=policy, limits=limits
        )
        after = await self._projector.state()
        return self._shortest_view(result, policy, before, after)

    async def alternatives(
        self,
        source: NodeRef,
        target: NodeRef,
        *,
        policy: TraversalPolicy,
        limits: TraversalLimits,
    ) -> dict[str, Any]:
        before = await self._projector.state()
        result = await alternate_paths(
            self._provider, source, target, policy=policy, limits=limits
        )
        after = await self._projector.state()
        return {
            "source": {"kind": source.kind.value, "key": source.key},
            "target": {"kind": target.kind.value, "key": target.key},
            "paths": [_path_view(path) for path in result.paths],
            "path_count": len(result.paths),
            "traversal_policy": policy.value,
            "diagnostics": _diagnostics_view(result.diagnostics),
            "completeness": _completeness(result.diagnostics, before, after),
        }

    async def reverse(
        self,
        target: NodeRef,
        *,
        policy: TraversalPolicy,
        limits: TraversalLimits,
    ) -> dict[str, Any]:
        before = await self._projector.state()
        result = await reverse_reachability(
            self._provider, target, policy=policy, limits=limits
        )
        after = await self._projector.state()
        return self._reverse_view(result, policy, before, after)

    async def neighbors(
        self,
        root: NodeRef,
        *,
        directions: frozenset[AdjacencyDirection],
        relationship_types: frozenset[RelationshipType] | None,
        limits: TraversalLimits,
    ) -> dict[str, Any]:
        before = await self._projector.state()
        result = await explore_neighbors(
            self._provider,
            root,
            directions=directions,
            relationship_types=relationship_types,
            limits=limits,
        )
        after = await self._projector.state()
        return self._neighbor_view(result, before, after)

    @staticmethod
    def _shortest_view(
        result: ShortestPathResult,
        policy: TraversalPolicy,
        before: ProjectionState,
        after: ProjectionState,
    ) -> dict[str, Any]:
        completeness = _completeness(result.diagnostics, before, after)
        if result.path is not None:
            conclusion = "path_found"
        elif completeness["complete_within_supported_graph"]:
            conclusion = "no_path_within_supported_graph"
        else:
            conclusion = "no_path_incomplete"
        return {
            "source": {"kind": result.source.kind.value, "key": result.source.key},
            "target": {"kind": result.target.kind.value, "key": result.target.key},
            "found": result.path is not None,
            "conclusion": conclusion,
            "path": None if result.path is None else _path_view(result.path),
            "traversal_policy": policy.value,
            "diagnostics": _diagnostics_view(result.diagnostics),
            "completeness": completeness,
        }

    @staticmethod
    def _reverse_view(
        result: ReverseReachabilityResult,
        policy: TraversalPolicy,
        before: ProjectionState,
        after: ProjectionState,
    ) -> dict[str, Any]:
        return {
            "target": {"kind": result.target.kind.value, "key": result.target.key},
            "reached": [
                {
                    "source": {"kind": item.node.kind.value, "key": item.node.key},
                    "path": _path_view(item.path),
                }
                for item in result.reached
            ],
            "result_count": len(result.reached),
            "traversal_policy": policy.value,
            "diagnostics": _diagnostics_view(result.diagnostics),
            "completeness": _completeness(result.diagnostics, before, after),
        }

    @staticmethod
    def _neighbor_view(
        result: NeighborResult,
        before: ProjectionState,
        after: ProjectionState,
    ) -> dict[str, Any]:
        return {
            "root": {"kind": result.root.kind.value, "key": result.root.key},
            "nodes": [{"kind": node.kind.value, "key": node.key} for node in result.nodes],
            "relationships": [_edge_view(item) for item in result.relationships],
            "diagnostics": _diagnostics_view(result.diagnostics),
            "completeness": _completeness(result.diagnostics, before, after),
        }
