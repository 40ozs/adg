"""Bounded, explainable traversal over privilege relationships (ADG-GRAPH-01C).

* :mod:`app.traversal.semantics` — which relationship types a path may use, and how: the
  traversal registry, the traversal policies, and what the graph does not model.
* :mod:`app.traversal.model` — steps, paths and what a path rests on.
* :mod:`app.traversal.search` — shortest path, alternate paths, reverse reachability and
  neighbor exploration, over an adjacency provider (no SQL).
* :mod:`app.traversal.completeness` — the outcome of an answer and every reason it is short
  of complete, including whether the graph reflects what was collected.
* :mod:`app.traversal.service` — the same, over PostgreSQL, in one consistent snapshot.

The relationships themselves are :mod:`app.domain.relationships` (GRAPH-01A) and are derived
by :mod:`app.relationships` (GRAPH-01B). Nothing here derives, stores or changes one. See
``docs/architecture/graph-relationships.md`` §10 and ADR-0042.
"""

from __future__ import annotations

from app.traversal.completeness import (
    CompletenessReason,
    GraphReadiness,
    PathOutcome,
    ReasonCategory,
    classify,
    conclusive,
)
from app.traversal.model import (
    AdjacencyBatch,
    PathAssessment,
    RelationshipAdjacency,
    TraversalEdge,
    TraversalPath,
    TraversalStep,
)
from app.traversal.search import (
    DEFAULT_LIMITS,
    Exploration,
    Neighborhood,
    PathSearch,
    Reachability,
    SearchLimits,
    alternate_paths,
    neighbors,
    reverse_reachability,
    shortest_path,
)
from app.traversal.semantics import (
    HOP_TYPES,
    INFORMATIONAL_TYPES,
    READING_TYPES,
    STORED_TYPES,
    TRAVERSAL_SEMANTICS,
    RelationshipClass,
    TraversalPolicy,
    TraversalRole,
    TraversalSemantics,
    coverage_gaps,
)

__all__ = [
    "DEFAULT_LIMITS",
    "HOP_TYPES",
    "INFORMATIONAL_TYPES",
    "READING_TYPES",
    "STORED_TYPES",
    "TRAVERSAL_SEMANTICS",
    "AdjacencyBatch",
    "CompletenessReason",
    "Exploration",
    "GraphReadiness",
    "Neighborhood",
    "PathAssessment",
    "PathOutcome",
    "PathSearch",
    "Reachability",
    "ReasonCategory",
    "RelationshipAdjacency",
    "RelationshipClass",
    "SearchLimits",
    "TraversalEdge",
    "TraversalPath",
    "TraversalPolicy",
    "TraversalRole",
    "TraversalSemantics",
    "TraversalStep",
    "alternate_paths",
    "classify",
    "conclusive",
    "coverage_gaps",
    "neighbors",
    "reverse_reachability",
    "shortest_path",
]
