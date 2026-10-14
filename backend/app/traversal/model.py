"""The values a relationship traversal works with and returns.

Kept apart from the algorithms (:mod:`app.traversal.search`) so that a test, the service and
the API all share one definition of a step, a path and what a path rests on.

**A step is a pair of nodes, not a relationship.** Two nodes can be joined by several current
relationships at once — an owner holds ``owns`` and ``write_dacl`` on the same folder, and a
group may hold ``generic_all`` and ``write_owner`` there too. A route through them is one
route, so a path is a sequence of nodes and each step carries *every* hop that joins its two
nodes. A path is therefore never returned twice because two relationships connect the same
pair, and a client still sees each relationship with its own meaning and condition.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from app.domain.errors import DomainValidationError
from app.domain.relationships import (
    CONFIDENCE_ORDER,
    DERIVATION_CLASS_ORDER,
    UNRESOLVED_QUALIFIERS,
    AdjacencyDirection,
    DerivationClass,
    EvidenceKind,
    EvidenceRole,
    NodeKind,
    NodeRef,
    RelationshipCondition,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipType,
    StoredRelationship,
)
from app.traversal.semantics import TraversalSemantics, semantics_of

__all__ = [
    "AdjacencyBatch",
    "ConditionOrigin",
    "PathAssessment",
    "PathCondition",
    "PathNote",
    "RelationshipAdjacency",
    "TraversalEdge",
    "TraversalPath",
    "TraversalStep",
    "assess_path",
    "build_step",
    "make_path",
    "node_order",
]

ADMINISTRATORS_RID: Final = 544
"""BUILTIN\\Administrators. A host-local key ``<host>|S-1-5-32-544`` is that host's group."""


def node_order(node: NodeRef) -> tuple[str, str]:
    """The canonical ordering of nodes: kind, then canonical key. Never a display name."""
    return (node.kind.value, node.key)


@dataclass(frozen=True, slots=True)
class TraversalEdge:
    """One stored relationship as a traversal sees it: identity, endpoints and its fold.

    ``confidence``, ``derivation_class``, ``conditions`` and ``qualifiers`` are those of the
    relationship's best support (``StoredRelationship.best_support``), which is what the
    relationship is as strong as. ``premises`` are that support's premise references, so an
    edge on a returned path names the facts it rests on without carrying their rows.
    """

    relationship_id: str
    relationship_type: RelationshipType
    source: NodeRef
    target: NodeRef
    scope: RelationshipScope | None
    currency: RelationshipCurrency
    confidence: RelationshipConfidence
    derivation_class: DerivationClass
    conditions: frozenset[RelationshipCondition] = frozenset()
    qualifiers: frozenset[RelationshipQualifier] = frozenset()
    rule: str = ""
    supports: int = 1
    premises: tuple[tuple[EvidenceKind, str], ...] = ()

    @classmethod
    def of(cls, relationship: StoredRelationship) -> TraversalEdge:
        best = relationship.best_support
        return cls(
            relationship_id=relationship.relationship_id,
            relationship_type=relationship.relationship_type,
            source=relationship.source,
            target=relationship.target,
            scope=relationship.scope,
            currency=relationship.currency,
            confidence=relationship.confidence,
            derivation_class=relationship.derivation_class,
            conditions=best.conditions,
            qualifiers=best.qualifiers,
            rule=str(best.rule),
            supports=len(relationship.supports),
            premises=tuple(
                sorted(
                    (item.kind, item.key)
                    for item in best.evidence
                    if item.role is EvidenceRole.PREMISE
                )
            ),
        )

    @property
    def semantics(self) -> TraversalSemantics:
        return semantics_of(self.relationship_type)

    @property
    def unresolved(self) -> bool:
        """The evidence itself says an endpoint (always the source today) is unresolved."""
        return self.derivation_class is DerivationClass.UNRESOLVED or bool(
            self.qualifiers & UNRESOLVED_QUALIFIERS
        )

    @property
    def conditional(self) -> bool:
        return self.derivation_class is DerivationClass.CONDITIONALLY_DERIVED

    def origin(self, direction: AdjacencyDirection) -> NodeRef:
        """The end a search walking ``direction`` arrives from."""
        return self.source if direction is AdjacencyDirection.OUTBOUND else self.target

    def far(self, direction: AdjacencyDirection) -> NodeRef:
        """The end a search walking ``direction`` arrives at."""
        return self.target if direction is AdjacencyDirection.OUTBOUND else self.source

    @property
    def pair(self) -> tuple[NodeRef, NodeRef, RelationshipScope | None]:
        return (self.source, self.target, self.scope)

    @property
    def strength(self) -> tuple[int, int, str, str]:
        """Sorts strongest first: higher confidence, then stronger class, then type and id."""
        return (
            -CONFIDENCE_ORDER[self.confidence],
            -DERIVATION_CLASS_ORDER[self.derivation_class],
            self.relationship_type.value,
            self.relationship_id,
        )


@dataclass(frozen=True, slots=True)
class AdjacencyBatch:
    """The relationships touching a set of nodes, as one batched read returned them."""

    edges: tuple[TraversalEdge, ...]
    truncated: bool = False
    """More relationships existed than the read's limit allowed."""


class RelationshipAdjacency(Protocol):
    """Supplies the stored relationships touching a set of nodes, in one batched read.

    In production a PostgreSQL repository; in tests a dictionary. Every relationship of
    ``types`` is returned whatever its currency — deciding what to follow is the search's job,
    and a relationship of unknown currency is a boundary it has to see, not a row to hide.
    """

    async def adjacent(
        self,
        nodes: Sequence[NodeRef],
        direction: AdjacencyDirection,
        types: frozenset[RelationshipType],
        *,
        limit: int,
    ) -> AdjacencyBatch: ...


class ConditionOrigin(StrEnum):
    """Where a condition on a path comes from."""

    RELATIONSHIP = "relationship"
    """A hop the path traverses is conditional."""

    READING = "reading"
    """The path ends on a reading that is conditional (Remote Desktop, WinRM)."""

    PATH = "path"
    """Only the path knows it: remote use of a host's local administration by a local
    account is subject to UAC token filtering."""


@dataclass(frozen=True, slots=True, order=True)
class PathCondition:
    step: int
    origin: ConditionOrigin
    condition: RelationshipCondition
    relationship_id: str | None = None


class PathNote(StrEnum):
    """Something the path does not claim, said beside it. Never changes its assessment."""

    SHARE_LAYER_NOT_EVALUATED = "share_layer_not_evaluated"
    """The path ends at an NTFS resource: control is stated at the NTFS layer, and access over
    SMB also passes the share's permissions."""


@dataclass(frozen=True, slots=True)
class PathAssessment:
    """What a path rests on, folded from its steps. Never a score.

    ``confidence`` is the weakest of: every step's strongest hop, the readings the path ends
    on, and the conditions only the path knows. ``chain_confidence`` is the hops alone — "is
    this chain of memberships and control there" — so a client can tell "a member of Remote
    Desktop Users, confirmed" from "may sign in over Remote Desktop, conditional".
    """

    confidence: RelationshipConfidence
    chain_confidence: RelationshipConfidence
    conditional: bool
    unresolved: bool
    conditions: tuple[PathCondition, ...]
    qualifiers: frozenset[RelationshipQualifier]
    notes: tuple[PathNote, ...] = ()


@dataclass(frozen=True, slots=True)
class TraversalStep:
    """One step of a path: two nodes and every current hop joining them."""

    source: NodeRef
    target: NodeRef
    scope: RelationshipScope | None
    relationships: tuple[TraversalEdge, ...]
    """The hops joining the pair that the policy followed, strongest first."""
    readings: tuple[TraversalEdge, ...] = ()
    """Current readings of this step's local membership (local administration, Remote
    Desktop, WinRM), with their own class and conditions."""

    def __post_init__(self) -> None:
        if not self.relationships:
            raise DomainValidationError("A step rests on at least one relationship.")
        for edge in (*self.relationships, *self.readings):
            if edge.pair != (self.source, self.target, self.scope):
                raise DomainValidationError(
                    f"Relationship {edge.relationship_id} does not join this step's nodes.",
                    value=edge.relationship_id,
                )

    @property
    def basis(self) -> TraversalEdge:
        """The hop the step is as strong as."""
        return self.relationships[0]


@dataclass(frozen=True, slots=True)
class TraversalPath:
    nodes: tuple[NodeRef, ...]
    steps: tuple[TraversalStep, ...]
    assessment: PathAssessment

    @property
    def hops(self) -> int:
        return len(self.steps)

    @property
    def identity(self) -> tuple[tuple[str, str], ...]:
        return tuple(node_order(node) for node in self.nodes)


def _weakest(values: Iterable[RelationshipConfidence]) -> RelationshipConfidence:
    return min(values, key=CONFIDENCE_ORDER.__getitem__, default=RelationshipConfidence.CONFIRMED)


def _is_host_local_account(node: NodeRef) -> bool:
    """A local account of one host: a host-scoped principal that is not a well-known group.

    UAC remote restrictions filter a *local account's* administrator token on a network
    logon; a domain account in a host's Administrators keeps its full token, and a BUILTIN
    or well-known SID is not an account.
    """
    sid = node.sid
    return (
        node.kind is NodeKind.PRINCIPAL
        and node.host is not None
        and sid is not None
        and sid.is_domain_relative
    )


def _is_host_administrators(node: NodeRef) -> bool:
    sid = node.sid
    return (
        node.kind is NodeKind.PRINCIPAL
        and node.host is not None
        and sid is not None
        and sid.is_builtin
        and sid.rid == ADMINISTRATORS_RID
    )


def build_step(
    hops: Sequence[TraversalEdge], readings: Sequence[TraversalEdge] = ()
) -> TraversalStep:
    ordered = tuple(sorted(hops, key=lambda edge: edge.strength))
    first = ordered[0]
    return TraversalStep(
        source=first.source,
        target=first.target,
        scope=first.scope,
        relationships=ordered,
        readings=tuple(
            sorted(readings, key=lambda edge: (edge.relationship_type.value, edge.relationship_id))
        ),
    )


def assess_path(steps: Sequence[TraversalStep]) -> PathAssessment:
    """Fold a path's steps into what the path as a whole rests on (architecture §9.11)."""
    conditions: list[PathCondition] = []
    qualifiers: set[RelationshipQualifier] = set()
    chain: list[RelationshipConfidence] = []
    overall: list[RelationshipConfidence] = []
    unresolved = False

    for index, step in enumerate(steps):
        basis = step.basis
        chain.append(basis.confidence)
        overall.append(basis.confidence)
        qualifiers |= basis.qualifiers
        unresolved = unresolved or basis.unresolved
        conditions.extend(
            PathCondition(index, ConditionOrigin.RELATIONSHIP, item, basis.relationship_id)
            for item in sorted(basis.conditions)
        )

    if steps:
        # A path that ends on a reading claims it: a route into a host's Remote Desktop Users
        # is a route to a conditional sign-in, never a confirmed one.
        last = len(steps) - 1
        for reading in steps[-1].readings:
            overall.append(reading.confidence)
            qualifiers |= reading.qualifiers
            unresolved = unresolved or reading.unresolved
            conditions.extend(
                PathCondition(last, ConditionOrigin.READING, item, reading.relationship_id)
                for item in sorted(reading.conditions)
            )

    # Remote use of local administration (architecture §9.3): a local account's membership of
    # a host's Administrators is filtered on a network logon unless policy says otherwise, and
    # that policy is not collected. It matters only when the path goes on to *use* the group.
    for index in range(len(steps) - 1):
        entering, leaving = steps[index], steps[index + 1]
        if (
            _is_host_administrators(entering.target)
            and _is_host_local_account(entering.source)
            and leaving.source == entering.target
        ):
            conditions.append(
                PathCondition(
                    index + 1,
                    ConditionOrigin.PATH,
                    RelationshipCondition.TOKEN_FILTERING_NOT_MODELED,
                )
            )
            overall.append(RelationshipConfidence.PROBABLE)

    notes: tuple[PathNote, ...] = ()
    if steps and steps[-1].target.kind is NodeKind.NTFS_RESOURCE:
        notes = (PathNote.SHARE_LAYER_NOT_EVALUATED,)

    return PathAssessment(
        confidence=_weakest(overall),
        chain_confidence=_weakest(chain),
        conditional=bool(conditions),
        unresolved=unresolved,
        conditions=tuple(sorted(conditions)),
        qualifiers=frozenset(qualifiers),
        notes=notes,
    )


def make_path(steps: Sequence[TraversalStep]) -> TraversalPath:
    if not steps:
        raise DomainValidationError("A path has at least one step.")
    nodes = (steps[0].source, *(step.target for step in steps))
    return TraversalPath(nodes=nodes, steps=tuple(steps), assessment=assess_path(steps))
