"""In-memory relationship graphs for the traversal tests.

The traversal walks a :class:`~app.traversal.model.RelationshipAdjacency`, so its cycles,
limits, tie-breaks and boundaries are testable without a database — the same seam
``app.domain.graph`` uses (ADR-0006). :class:`Graph` builds relationships with the real
identity digest, and :class:`MemoryAdjacency` serves them in whatever order a test asks for,
so a test can prove that no answer depends on the order rows arrive in.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from app.domain.relationships import (
    AdjacencyDirection,
    DerivationClass,
    EvidenceKind,
    NodeKind,
    NodeRef,
    RelationshipCondition,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipType,
    confidence_of,
    relationship_id,
)
from app.traversal.model import AdjacencyBatch, TraversalEdge

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
FOREIGN_DOMAIN = "S-1-5-21-3000000001-3000000002-3000000003"


def user(rid: int) -> NodeRef:
    return NodeRef.principal(f"{DOMAIN}-{rid}")


def group(rid: int) -> NodeRef:
    return NodeRef.principal(f"{DOMAIN}-{rid}")


def local(host: str, sid: str) -> NodeRef:
    return NodeRef.principal(f"{host}|{sid}")


def resource(path: str) -> NodeRef:
    return NodeRef(NodeKind.NTFS_RESOURCE, path.casefold())


def domain(sid: str = FOREIGN_DOMAIN) -> NodeRef:
    return NodeRef(NodeKind.DOMAIN, sid)


def well_known(sid: str) -> NodeRef:
    return NodeRef.principal(sid)


_LOCAL: frozenset[RelationshipType] = frozenset(
    {
        RelationshipType.LOCAL_GROUP_MEMBER_OF,
        RelationshipType.LOCAL_ADMIN_OF,
        RelationshipType.REMOTE_DESKTOP_USER_OF,
        RelationshipType.REMOTE_MANAGEMENT_USER_OF,
    }
)


def _scope(
    relationship_type: RelationshipType, source: NodeRef, target: NodeRef
) -> RelationshipScope | None:
    if relationship_type in _LOCAL:
        host = target.host
        assert host is not None, "a local relationship needs a host-local group"
        return RelationshipScope.host(host)
    hosts = sorted({node.host for node in (source, target) if node.host is not None})
    return RelationshipScope.host(hosts[0]) if hosts else None


def make_edge(
    relationship_type: RelationshipType,
    source: NodeRef,
    target: NodeRef,
    *,
    currency: RelationshipCurrency = RelationshipCurrency.CURRENT,
    derivation_class: DerivationClass = DerivationClass.OBSERVED,
    conditions: Sequence[RelationshipCondition] = (),
    qualifiers: Sequence[RelationshipQualifier] = (),
) -> TraversalEdge:
    scope = _scope(relationship_type, source, target)
    return TraversalEdge(
        relationship_id=relationship_id(relationship_type, source, target, scope),
        relationship_type=relationship_type,
        source=source,
        target=target,
        scope=scope,
        currency=currency,
        confidence=confidence_of(derivation_class, qualifiers),
        derivation_class=derivation_class,
        conditions=frozenset(conditions),
        qualifiers=frozenset(qualifiers),
        rule="test.rule@1",
        premises=((EvidenceKind.MEMBERSHIP_EDGE, f"edge:{source.key}->{target.key}"),),
    )


@dataclass
class Graph:
    """A relationship graph built hop by hop."""

    edges: list[TraversalEdge] = field(default_factory=list)

    def add(self, edge: TraversalEdge) -> TraversalEdge:
        self.edges.append(edge)
        return edge

    def member(
        self,
        member: NodeRef,
        of: NodeRef,
        *,
        currency: RelationshipCurrency = RelationshipCurrency.CURRENT,
        derivation_class: DerivationClass = DerivationClass.OBSERVED,
        qualifiers: Sequence[RelationshipQualifier] = (),
    ) -> TraversalEdge:
        return self.add(
            make_edge(
                RelationshipType.MEMBER_OF,
                member,
                of,
                currency=currency,
                derivation_class=derivation_class,
                qualifiers=qualifiers,
            )
        )

    def primary(self, member: NodeRef, of: NodeRef) -> TraversalEdge:
        return self.add(make_edge(RelationshipType.PRIMARY_GROUP_OF, member, of))

    def local_member(
        self,
        member: NodeRef,
        of: NodeRef,
        *,
        currency: RelationshipCurrency = RelationshipCurrency.CURRENT,
        qualifiers: Sequence[RelationshipQualifier] = (),
    ) -> TraversalEdge:
        """A local membership, plus the reading the projector derives for a BUILTIN group."""
        unresolved = tuple(qualifiers)
        edge = self.add(
            make_edge(
                RelationshipType.LOCAL_GROUP_MEMBER_OF,
                member,
                of,
                currency=currency,
                derivation_class=(
                    DerivationClass.UNRESOLVED if unresolved else DerivationClass.OBSERVED
                ),
                qualifiers=unresolved,
            )
        )
        sid = of.key.rpartition("|")[2]
        if sid == "S-1-5-32-544":
            self.add(
                make_edge(
                    RelationshipType.LOCAL_ADMIN_OF,
                    member,
                    of,
                    currency=currency,
                    derivation_class=(
                        DerivationClass.UNRESOLVED
                        if unresolved
                        else DerivationClass.DETERMINISTICALLY_DERIVED
                    ),
                    qualifiers=unresolved,
                )
            )
        elif sid == "S-1-5-32-555":
            self.add(
                make_edge(
                    RelationshipType.REMOTE_DESKTOP_USER_OF,
                    member,
                    of,
                    currency=currency,
                    derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
                    conditions=(
                        RelationshipCondition.LOGON_RIGHT_UNEVALUATED,
                        RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
                    ),
                    qualifiers=unresolved,
                )
            )
        elif sid == "S-1-5-32-580":
            self.add(
                make_edge(
                    RelationshipType.REMOTE_MANAGEMENT_USER_OF,
                    member,
                    of,
                    currency=currency,
                    derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
                    conditions=(
                        RelationshipCondition.SERVICE_ACCESS_POLICY_UNEVALUATED,
                        RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
                    ),
                    qualifiers=unresolved,
                )
            )
        return edge

    def control(
        self,
        relationship_type: RelationshipType,
        trustee: NodeRef,
        target: NodeRef,
        *,
        currency: RelationshipCurrency = RelationshipCurrency.CURRENT,
        derivation_class: DerivationClass = DerivationClass.DETERMINISTICALLY_DERIVED,
        conditions: Sequence[RelationshipCondition] = (),
        qualifiers: Sequence[RelationshipQualifier] = (),
    ) -> TraversalEdge:
        return self.add(
            make_edge(
                relationship_type,
                trustee,
                target,
                currency=currency,
                derivation_class=derivation_class,
                conditions=conditions,
                qualifiers=qualifiers,
            )
        )

    def foreign(self, principal: NodeRef, issuer: NodeRef) -> TraversalEdge:
        return self.add(
            make_edge(
                RelationshipType.FOREIGN_PRINCIPAL_OF,
                principal,
                issuer,
                derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
            )
        )

    def set_currency(self, edge: TraversalEdge, currency: RelationshipCurrency) -> TraversalEdge:
        index = self.edges.index(edge)
        updated = replace(edge, currency=currency)
        self.edges[index] = updated
        return updated


class MemoryAdjacency:
    """Serves a :class:`Graph` the way the repository does, in a chosen row order."""

    def __init__(self, graph: Graph, *, shuffle_seed: int | None = None) -> None:
        self._edges = list(graph.edges)
        self._seed = shuffle_seed
        self.calls: list[tuple[AdjacencyDirection, tuple[NodeRef, ...]]] = []

    async def adjacent(
        self,
        nodes: Sequence[NodeRef],
        direction: AdjacencyDirection,
        types: frozenset[RelationshipType],
        *,
        limit: int,
    ) -> AdjacencyBatch:
        self.calls.append((direction, tuple(nodes)))
        wanted = set(nodes)
        found = [
            edge
            for edge in self._edges
            if edge.relationship_type in types
            and (edge.source if direction is AdjacencyDirection.OUTBOUND else edge.target) in wanted
        ]
        # The repository orders by (kind, key, type, id) so that a truncated read is a
        # deterministic subset; mirror it, then shuffle what is returned if asked.
        found.sort(
            key=lambda edge: (
                (edge.source if direction is AdjacencyDirection.OUTBOUND else edge.target),
                edge.relationship_type.value,
                edge.relationship_id,
            )
        )
        truncated = len(found) > limit
        found = found[:limit]
        if self._seed is not None:
            random.Random(self._seed + len(self.calls)).shuffle(found)
        return AdjacencyBatch(edges=tuple(found), truncated=truncated)


CONFIRMED = RelationshipConfidence.CONFIRMED
PROBABLE = RelationshipConfidence.PROBABLE
INDETERMINATE = RelationshipConfidence.INDETERMINATE
