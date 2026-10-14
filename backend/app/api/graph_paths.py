"""Read-only privilege-relationship traversal APIs (ADG-GRAPH-01C)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PRINTABLE_IDENTIFIER, Session, TraversalBounds
from app.domain.relationship_paths import TraversalPolicy
from app.domain.relationships import AdjacencyDirection, NodeKind, NodeRef, RelationshipType
from app.schemas.relationship_paths import (
    AlternatePathsView,
    NeighborView,
    ReverseReachabilityView,
    ShortestPathView,
)
from app.services.relationship_paths import RelationshipPathService

router = APIRouter(prefix="/api/v1/graph/traversal", tags=["graph"])

NodeKeyQuery = Annotated[
    str,
    Query(
        min_length=1,
        max_length=512,
        pattern=PRINTABLE_IDENTIFIER,
        description="Canonical ADG object key. Display names are not identifiers.",
    ),
]


class NeighborDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"
    BOTH = "both"


@router.get("/shortest", response_model=ShortestPathView)
async def shortest_relationship_path(
    session: Session,
    limits: TraversalBounds,
    source_kind: NodeKind,
    source_key: NodeKeyQuery,
    target_kind: NodeKind,
    target_key: NodeKeyQuery,
    policy: Annotated[TraversalPolicy, Query()] = TraversalPolicy.SUPPORTED,
) -> ShortestPathView:
    """Shortest deterministic privilege path through current supported relationships."""

    result = await RelationshipPathService(session).shortest(
        NodeRef(source_kind, source_key),
        NodeRef(target_kind, target_key),
        policy=policy,
        limits=limits,
    )
    return ShortestPathView.model_validate(result)


@router.get("/alternates", response_model=AlternatePathsView)
async def alternate_relationship_paths(
    session: Session,
    limits: TraversalBounds,
    source_kind: NodeKind,
    source_key: NodeKeyQuery,
    target_kind: NodeKind,
    target_key: NodeKeyQuery,
    policy: Annotated[TraversalPolicy, Query()] = TraversalPolicy.SUPPORTED,
) -> AlternatePathsView:
    """Bounded deterministic simple paths; never an unbounded all-path enumeration."""

    result = await RelationshipPathService(session).alternatives(
        NodeRef(source_kind, source_key),
        NodeRef(target_kind, target_key),
        policy=policy,
        limits=limits,
    )
    return AlternatePathsView.model_validate(result)


@router.get("/reverse", response_model=ReverseReachabilityView)
async def reverse_relationship_reachability(
    session: Session,
    limits: TraversalBounds,
    target_kind: NodeKind,
    target_key: NodeKeyQuery,
    policy: Annotated[TraversalPolicy, Query()] = TraversalPolicy.SUPPORTED,
) -> ReverseReachabilityView:
    """Who can reach this target through bounded supported privilege paths."""

    result = await RelationshipPathService(session).reverse(
        NodeRef(target_kind, target_key),
        policy=policy,
        limits=limits,
    )
    return ReverseReachabilityView.model_validate(result)


@router.get("/neighbors", response_model=NeighborView)
async def relationship_neighbors(
    session: Session,
    limits: TraversalBounds,
    root_kind: NodeKind,
    root_key: NodeKeyQuery,
    direction: Annotated[NeighborDirection, Query()] = NeighborDirection.BOTH,
    relationship_type: Annotated[list[RelationshipType] | None, Query()] = None,
) -> NeighborView:
    """Bounded current graph exploration, including informational relationships."""

    directions = {
        NeighborDirection.OUTBOUND: frozenset({AdjacencyDirection.OUTBOUND}),
        NeighborDirection.INBOUND: frozenset({AdjacencyDirection.INBOUND}),
        NeighborDirection.BOTH: frozenset(
            {AdjacencyDirection.OUTBOUND, AdjacencyDirection.INBOUND}
        ),
    }[direction]
    result = await RelationshipPathService(session).neighbors(
        NodeRef(root_kind, root_key),
        directions=directions,
        relationship_types=None if relationship_type is None else frozenset(relationship_type),
        limits=limits,
    )
    return NeighborView.model_validate(result)
