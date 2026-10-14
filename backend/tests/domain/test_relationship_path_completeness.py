"""GRAPH-01C completeness semantics around the existing projector state."""

from __future__ import annotations

import uuid

from app.domain.graph import TraversalLimits, TruncationReason
from app.domain.relationship_paths import TraversalDiagnostics
from app.domain.relationships import ProjectionStatus
from app.relationships.projector import ProjectionState
from app.services.relationship_paths import _completeness


def state(
    *,
    pass_id: str = "00000000-0000-0000-0000-000000000010",
    status: ProjectionStatus = ProjectionStatus.SUCCEEDED,
    basis: str = "a" * 32,
    underived: tuple[uuid.UUID, ...] = (),
    in_progress: tuple[uuid.UUID, ...] = (),
) -> ProjectionState:
    return ProjectionState(
        latest_pass={
            "pass_id": uuid.UUID(pass_id),
            "status": status.value,
            "basis_token": basis,
        },
        underived_runs=underived,
        runs_in_progress=in_progress,
    )


def test_complete_supported_search_still_discloses_uncollected_enterprise_families() -> None:
    projection = state()
    result = _completeness(TraversalDiagnostics(TraversalLimits()), projection, projection)
    assert result["complete_within_supported_graph"] is True
    assert result["complete_for_enterprise"] is False
    assert "relationship_families_not_collected" in result["reasons"]


def test_completed_run_missing_derivation_prevents_complete_no_path_claim() -> None:
    run_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    projection = state(underived=(run_id,))
    result = _completeness(TraversalDiagnostics(TraversalLimits()), projection, projection)
    assert result["complete_within_supported_graph"] is False
    assert result["graph"]["underived_run_count"] == 1
    assert "graph_not_ready" in result["reasons"]


def test_failed_latest_projection_prevents_complete_no_path_claim() -> None:
    projection = state(status=ProjectionStatus.FAILED)
    result = _completeness(TraversalDiagnostics(TraversalLimits()), projection, projection)
    assert result["complete_within_supported_graph"] is False
    assert result["graph"]["latest_pass_failed"] is True
    assert "graph_not_ready" in result["reasons"]


def test_basis_change_during_query_is_explicitly_incomplete() -> None:
    before = state(basis="a" * 32)
    after = state(
        pass_id="00000000-0000-0000-0000-000000000011",
        basis="b" * 32,
    )
    result = _completeness(TraversalDiagnostics(TraversalLimits()), before, after)
    assert result["complete_within_supported_graph"] is False
    assert result["graph_basis_stable"] is False
    assert "graph_basis_changed_during_query" in result["reasons"]


def test_truncation_and_unresolved_boundaries_are_not_plain_no_path() -> None:
    diagnostics = TraversalDiagnostics(TraversalLimits())
    diagnostics.truncation.add(TruncationReason.MAX_DEPTH)
    diagnostics.unresolved.add("relationship-unresolved")
    projection = state()
    result = _completeness(diagnostics, projection, projection)
    assert result["complete_within_supported_graph"] is False
    assert "traversal_truncated" in result["reasons"]
    assert "unresolved_relationship_boundary" in result["reasons"]
