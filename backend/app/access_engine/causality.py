r"""Why a principal has the rights it has: the paths, and what each one is worth.

:mod:`app.access_engine.resolver` answers *what* — one mask, two layers, a certainty. This
module answers *why*, and the two are not the same question. "Alice has Modify" sends an
administrator looking; "Alice has Modify because ``Finance-Team`` is inside ``Finance-RW``
and ``Finance-RW`` is granted Modify on ``\\FS01\Finance``, and also because she is in
``Domain Users``, which the share grants Read" tells them what to change and warns them
that changing one of the two will not be enough.

The model is a graph with five node kinds — principal, group, SMB ACE, NTFS ACE, and the
share or resource they sit on — and four edge kinds: an observed membership, an *assumed*
membership (``Everyone`` is in every token and in no database), the trustee relation that
puts a principal on an ACE, and the grant or deny relation that ACE has to the object. A
**causal path** is one route through it: subject, up through groups, onto an ACE, onto the
object.

Four things it must get right, all of them in the direction that stops an answer being
more confident than the evidence:

**Multiple paths are preserved.** Alice may reach one ACE through two different chains, or
two ACEs through one. Collapsing those into "Alice has Modify" is how a remediation gets
signed off after removing one group membership that changed nothing. Every distinct simple
chain is enumerated and kept.

**A grant is not the same as an effective grant.** An Allow that matched the token can
still be worth nothing: an earlier entry may have settled every right it names
(:attr:`PathEffect.REDUNDANT`), or the *other* layer may withhold all of them
(:attr:`PathEffect.CONSTRAINED`). An NTFS ACE granting Full Control behind a share granting
Read is on the ACL, is matched, contributes at its own layer, and changes nothing about
what anyone can do over SMB. It is reported as what it is rather than as a cause.

**Removal analysis never promises more than it can prove.** :class:`RemovalTarget` is
computed by *re-running the access check with the edge gone* — not by reasoning about which
path looked important. Reasoning is where this goes wrong: deleting an Allow can reveal a
redundant Allow behind it, and deleting a membership can remove a Deny as well as a grant.
Re-evaluation gets both right for free, and an edge with an alternate path around it comes
back with nothing removed, which is exactly the claim that must not be overstated.

**The graph is bounded and ordered.** Nesting plus several trustees is a combinatorial
object, and a pathological directory can have a great many chains. Enumeration is bounded
by :class:`ExplanationLimits` and truncation is reported, never silent; and everything
comes back in a total order that does not depend on dictionary iteration, so two runs over
unchanged data produce identical output and a diff means something changed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from app.access_engine.conditions import AccessCondition, AccessFinding
from app.access_engine.evaluation import (
    UNOWNED_PRESENT_DACL,
    AclEntry,
    AclEvaluation,
    AppliedAce,
    evaluate_acl,
)
from app.access_engine.resolver import EffectiveAccess, ResourceDacl, ShareDacl
from app.access_engine.rights import RightsLayer, RightsMask, effective_rights
from app.access_engine.subjects import SidOrigin, SubjectToken, TokenSid
from app.domain.errors import DomainValidationError
from app.domain.graph import (
    MAX_DEPTH_CEILING,
    Direction,
    GraphCycle,
    GraphEdge,
    MembershipPath,
    TruncationReason,
    find_cycles,
    simple_paths,
)
from app.domain.graph import TraversalLimits as GraphLimits

__all__ = [
    "DEFAULT_EXPLANATION_LIMITS",
    "MAX_PATHS_CEILING",
    "MAX_REMOVAL_TARGETS_CEILING",
    "OWNERSHIP_POSITION",
    "AccessExplanation",
    "CausalPath",
    "EdgeKind",
    "ExplanationEdge",
    "ExplanationGraph",
    "ExplanationLimits",
    "ExplanationNode",
    "NodeKind",
    "PathEffect",
    "PathRelation",
    "PathTruncation",
    "RemovalTarget",
    "explain_access",
]

OWNERSHIP_POSITION: Final = -1
"""The :attr:`CausalPath.ace_position` of an ownership path. Negative because it sits
before every ACE in the walk, which is exactly where Windows applies it."""

MAX_PATHS_CEILING: Final = 1_000
MAX_REMOVAL_TARGETS_CEILING: Final = 256
"""Ceilings, not defaults, in the sense :mod:`app.domain.graph` already establishes: a
caller-supplied limit may be smaller and may not be larger, so no request can turn a
bounded explanation into an unbounded one."""

CEILINGS: Final[dict[str, int]] = {
    "max_paths": MAX_PATHS_CEILING,
    "max_paths_per_trustee": MAX_PATHS_CEILING,
    "max_depth": MAX_DEPTH_CEILING,
    "max_removal_targets": MAX_REMOVAL_TARGETS_CEILING,
}
"""One table, read by both the constructor and :meth:`ExplanationLimits.clamped`, so a
value that is refused when constructed directly is the same value that gets clamped at
the API boundary. Depth shares the membership graph's ceiling: it bounds the same walk."""


# --------------------------------------------------------------------------------------
# The graph
# --------------------------------------------------------------------------------------


class NodeKind(StrEnum):
    """What one node in the explanation graph is.

    A principal and a group are distinguished because the *edge* between them means
    different things to a reader: a user is where a chain starts, and a group is something
    that can be emptied, renamed, or removed from an ACL.
    """

    PRINCIPAL = "principal"
    """The subject, or any non-group principal on a chain."""

    GROUP = "group"
    """A group, whether a domain group or one server's local group."""

    ASSUMED_TRUSTEE = "assumed_trustee"
    """``Everyone``, ``Authenticated Users``, ``NETWORK`` — a SID Windows puts in the token
    that corresponds to no membership edge anywhere. Named as its own kind because it looks
    exactly like a group on an ACL and cannot be treated like one: nobody can be removed
    from it."""

    NTFS_ACE = "ntfs_ace"
    SMB_ACE = "smb_ace"
    RESOURCE = "resource"
    SHARE = "share"


class EdgeKind(StrEnum):
    """What one edge in the explanation graph asserts."""

    MEMBERSHIP = "membership"
    """An observed membership edge. The only kind an administrator can delete."""

    ASSUMED_MEMBERSHIP = "assumed_membership"
    """Windows places this SID in the token. No edge exists to remove."""

    TRUSTEE = "trustee"
    """This ACE names this principal. Removable by editing the ACL."""

    GRANT = "grant"
    """This Allow ACE applies to this object."""

    DENY = "deny"
    """This Deny ACE applies to this object."""

    OWNERSHIP = "ownership"
    """The subject owns the object, and Windows grants an owner ``READ_CONTROL`` and
    ``WRITE_DAC`` before it reads a single ACE. Not removable: the remediation is to
    change the owner, which is not the deletion of an edge."""


@dataclass(frozen=True, slots=True)
class ExplanationNode:
    """One node, identified by a stable id that the edges and paths refer to.

    ``node_id`` is derived from the node's own identity rather than from its position in a
    traversal, so the same estate produces the same ids on every run and two explanations
    can be compared.
    """

    node_id: str
    kind: NodeKind
    key: str
    sid: str | None = None
    display_name: str | None = None
    layer: RightsLayer | None = None
    """Set on ACE nodes only: which ACL the entry belongs to."""

    position: int | None = None
    """Set on ACE nodes only: the entry's index in the DACL as stored. Order is
    load-bearing, so two entries naming one trustee are two nodes."""

    @property
    def is_ace(self) -> bool:
        return self.kind in (NodeKind.NTFS_ACE, NodeKind.SMB_ACE)


@dataclass(frozen=True, slots=True)
class ExplanationEdge:
    """One directed edge, from a node nearer the subject to one nearer the object."""

    edge_id: str
    kind: EdgeKind
    source: str
    target: str
    membership_edge_key: str | None = None
    """The stored ``membership_edges`` row this edge came from, when it is an observed
    membership. ``None`` for everything else, which is precisely the set of edges that
    cannot be deleted from a directory."""

    ace_key: str | None = None
    """The stored ACE this edge came from, when it is a trustee, grant, or deny edge."""

    @property
    def is_removable(self) -> bool:
        """Whether an administrator could actually delete this relationship.

        An assumed membership cannot be removed — there is no ``Everyone`` group to edit —
        and the grant/deny edge between an ACE and its object is not a separate thing from
        the ACE. The two removable kinds are a membership row and an ACE.
        """
        return self.kind in (EdgeKind.MEMBERSHIP, EdgeKind.TRUSTEE)


@dataclass(frozen=True, slots=True)
class ExplanationGraph:
    """The typed nodes and edges every path in an explanation refers to.

    Deduplicated: one node per principal, per ACE, per object, however many paths run
    through it, so the graph is the shape a reader can be shown and the paths are routes
    across it. Both tuples are sorted by id.
    """

    nodes: tuple[ExplanationNode, ...] = ()
    edges: tuple[ExplanationEdge, ...] = ()

    def node(self, node_id: str) -> ExplanationNode | None:
        for candidate in self.nodes:
            if candidate.node_id == node_id:
                return candidate
        return None

    def edge(self, edge_id: str) -> ExplanationEdge | None:
        for candidate in self.edges:
            if candidate.edge_id == edge_id:
                return candidate
        return None


# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------


class PathRelation(StrEnum):
    """Whether the ACE at the end of a path allows or denies."""

    GRANT = "grant"
    DENY = "deny"


class PathEffect(StrEnum):
    """What a path is actually worth to the final answer.

    The distinction this module exists to draw. All three describe an ACE that **matched
    the token** — an ACE that names nobody in the token produces no path at all — and they
    differ in whether that match changed anything.
    """

    CONTRIBUTES = "contributes"
    """This path changes the final effective mask: a grant delivering rights that survive
    the layer crossing, or a deny withholding rights the other layer would have allowed
    through."""

    REDUNDANT = "redundant"
    """The ACE matched and settled nothing, because an entry earlier in the same DACL had
    already decided every right it names. Removing it changes nothing; so does leaving it.
    The ACL says more than it does."""

    CONSTRAINED = "constrained"
    """The ACE contributed at its own layer and the **other** layer withholds all of it, so
    it does not affect final access. An NTFS ACE granting Full Control behind a share
    granting Read is the case worth naming: the ACL is alarming, the access is not, and
    widening the share would make it real."""


@dataclass(frozen=True, slots=True)
class CausalPath:
    """One route from the subject to one ACE on one object, and what it delivers.

    ``chain`` is the membership chain read the way an explanation is read — subject first,
    ACL trustee last — so ``(alice, finance-team, finance-rw)`` is the familiar
    *Alice → Finance-Team → Finance-RW*. A path whose ACE names the subject directly has a
    one-element chain.
    """

    path_id: str
    layer: RightsLayer
    relation: PathRelation
    effect: PathEffect
    chain: tuple[str, ...]
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    trustee_key: str
    ace_position: int
    ace_key: str | None
    ace_rights: RightsMask
    """The ACE's own mask with generic bits expanded: what it would be worth alone."""

    layer_rights: RightsMask
    """What it contributed at its own layer, after everything earlier in the DACL. Empty
    for a :attr:`PathEffect.REDUNDANT` path, which is what makes it redundant."""

    effective_rights: RightsMask
    """What it is worth to the final answer, carrying :attr:`RightsLayer.EFFECTIVE`.

    For a grant, the rights it delivers that survive the other layer. For a deny, the
    rights it removes that the other layer would otherwise have let through. Empty for
    both :attr:`PathEffect.REDUNDANT` and :attr:`PathEffect.CONSTRAINED`.
    """

    constrained_rights: RightsMask
    """The part of :attr:`layer_rights` the other layer withholds. Non-empty alongside a
    non-empty :attr:`effective_rights` means the path is partly constrained: it delivers
    something, and the other ACL is capping it."""

    assumed: bool
    """Whether the ACE reached the subject through an assumed token SID rather than an
    observed membership. An ``Everyone`` grant is real access and not a removable edge."""

    via_group: bool
    inherited: bool
    """Whether the ACE is inherited. An inherited entry is fixed on an ancestor, so the
    remediation is a different one from an explicit entry's."""

    @property
    def length(self) -> int:
        """Membership hops from the subject to the ACL trustee. 0 when named directly."""
        return len(self.chain) - 1

    @property
    def is_grant(self) -> bool:
        return self.relation is PathRelation.GRANT

    @property
    def matters(self) -> bool:
        """Whether removing this path alone could change the answer at all.

        Necessary, not sufficient: two paths can both matter and neither be removable on
        its own. :class:`RemovalTarget` is what settles that.
        """
        return self.effect is PathEffect.CONTRIBUTES


# --------------------------------------------------------------------------------------
# Removal
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RemovalTarget:
    """What removing one edge would actually do, measured rather than inferred.

    Every field here is the result of re-running the access check with the edge gone. That
    is deliberate and it is the whole safety property: an Allow deleted can uncover a
    redundant Allow behind it, and a membership deleted can take a Deny with it and
    *widen* access. Both outcomes fall out of a re-evaluation and neither falls out of
    reasoning about which path looked important.
    """

    edge_id: str
    kind: EdgeKind
    source: str
    target: str
    rights_removed: RightsMask
    """Effective rights the subject loses. Empty means removing this edge changes nothing,
    which is the honest answer whenever another path remains."""

    rights_added: RightsMask
    """Effective rights the subject **gains**. Normally empty; non-empty means the edge was
    carrying a Deny, and removing it would widen access rather than narrow it."""

    rights_after: RightsMask
    revokes_all_access: bool
    """Whether removing this one edge leaves the subject with no rights at all. ``False``
    while any alternate path survives — the claim this type exists to avoid overstating."""

    alternate_paths: tuple[str, ...]
    """Ids of the contributing paths that still deliver rights afterwards. Non-empty is the
    reason ``revokes_all_access`` is ``False``."""

    paths_removed: tuple[str, ...]
    """Ids of the paths that run through this edge and would disappear with it."""

    @property
    def changes_nothing(self) -> bool:
        return self.rights_removed.is_empty and self.rights_added.is_empty


# --------------------------------------------------------------------------------------
# Limits and the result
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplanationLimits:
    """Bounds on an explanation, so a pathological graph costs a bounded amount.

    Group nesting is combinatorial: a subject in twenty groups that each nest three ways
    into one ACL trustee has sixty chains to that one ACE before any second trustee is
    considered. The defaults are generous against a real directory — nesting deeper than a
    handful of levels is itself a finding — and small against a graph designed to be
    expensive.
    """

    max_paths: int = 200
    """Across the whole explanation, both layers together."""

    max_paths_per_trustee: int = 25
    """Chains enumerated from the subject to any one ACL trustee."""

    max_depth: int = 32
    """Membership hops on any one chain."""

    max_removal_targets: int = 64
    """Edges re-evaluated. Each costs one access check over each DACL."""

    def __post_init__(self) -> None:
        for name, ceiling in CEILINGS.items():
            value: int = getattr(self, name)
            if value < 1:
                raise DomainValidationError(
                    f"{name} must be at least 1; received {value}.", value=value, field=name
                )
            if value > ceiling:
                raise DomainValidationError(
                    f"{name} may not exceed {ceiling}; received {value}.", value=value, field=name
                )

    def clamped(self, **overrides: int | None) -> ExplanationLimits:
        """Apply non-``None`` overrides, silently reducing any that exceed a ceiling."""
        ceilings = CEILINGS
        values = {
            "max_paths": self.max_paths,
            "max_paths_per_trustee": self.max_paths_per_trustee,
            "max_depth": self.max_depth,
            "max_removal_targets": self.max_removal_targets,
        }
        for name, override in overrides.items():
            if override is None:
                continue
            if name not in ceilings:
                raise DomainValidationError(f"Unknown explanation limit {name!r}.", field=name)
            values[name] = max(1, min(override, ceilings[name]))
        return ExplanationLimits(**values)


DEFAULT_EXPLANATION_LIMITS: Final = ExplanationLimits()


class PathTruncation(StrEnum):
    """Why an explanation is not the whole story. Empty means it is."""

    MAX_PATHS = "max_paths"
    MAX_PATHS_PER_TRUSTEE = "max_paths_per_trustee"
    MAX_DEPTH = "max_depth"
    MAX_REMOVAL_TARGETS = "max_removal_targets"
    MEMBERSHIP_INCOMPLETE = "membership_incomplete"
    """The traversal that built the token was itself truncated, so a trustee the subject
    can reach may be missing entirely — not merely one chain to it."""

    EDGES_NOT_SUPPLIED = "edges_not_supplied"
    """A trustee reached the token through group membership and no supplied edge explains
    how. The chain the token recorded is reported, and alternate chains to that trustee
    were not enumerated."""


@dataclass(frozen=True, slots=True)
class AccessExplanation:
    """Every meaningful path behind one effective-access answer.

    The answer itself is :attr:`access`; nothing here recomputes it, and every mask below
    is a decomposition of the mask it already reported.
    """

    access: EffectiveAccess
    graph: ExplanationGraph
    paths: tuple[CausalPath, ...]
    removal_targets: tuple[RemovalTarget, ...]
    cycles: tuple[GraphCycle, ...]
    limits: ExplanationLimits
    truncation: tuple[PathTruncation, ...] = ()
    findings: tuple[AccessFinding, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every path was enumerated.

        ``False`` means the paths shown are a *subset*: there is at least one more route to
        these rights, so no conclusion may be drawn from the absence of one.
        """
        return not self.truncation

    @property
    def contributing(self) -> tuple[CausalPath, ...]:
        return tuple(path for path in self.paths if path.effect is PathEffect.CONTRIBUTES)

    @property
    def grants(self) -> tuple[CausalPath, ...]:
        return tuple(path for path in self.paths if path.is_grant)

    @property
    def denies(self) -> tuple[CausalPath, ...]:
        return tuple(path for path in self.paths if not path.is_grant)

    @property
    def redundant(self) -> tuple[CausalPath, ...]:
        return tuple(path for path in self.paths if path.effect is PathEffect.REDUNDANT)

    @property
    def constrained(self) -> tuple[CausalPath, ...]:
        return tuple(path for path in self.paths if path.effect is PathEffect.CONSTRAINED)

    @property
    def sufficient_removals(self) -> tuple[RemovalTarget, ...]:
        """Single edges whose removal revokes every right. Very often empty, correctly so."""
        return tuple(target for target in self.removal_targets if target.revokes_all_access)


# --------------------------------------------------------------------------------------
# Building it
# --------------------------------------------------------------------------------------


def explain_access(
    access: EffectiveAccess,
    resource: ResourceDacl,
    share: ShareDacl | None = None,
    *,
    edges: Iterable[GraphEdge] = (),
    limits: ExplanationLimits = DEFAULT_EXPLANATION_LIMITS,
) -> AccessExplanation:
    r"""Explain one resolved answer: the paths that caused it, and what each is worth.

    Args:
        access: the answer to explain, exactly as :func:`resolve_access` returned it.
        resource: the DACL that answer was resolved against. Required in full — the
            evaluation carries only the entries that *matched*, and removal analysis has to
            re-run the check over the whole DACL in its stored order.
        share: the share ACL, on the same terms. Must be the one ``access`` was resolved
            with.
        edges: the membership subgraph the token was built from — in practice
            :attr:`app.domain.Expansion.edges` from the upward traversal that produced it.
            Supplying it is what lets **alternate** chains be found; without it, each
            trustee is explained by the single chain the token recorded, and the result
            says so with :attr:`PathTruncation.EDGES_NOT_SUPPLIED`.
        limits: bounds on enumeration and on removal analysis.

    Returns:
        The graph, the paths across it, and the measured effect of removing each edge.

    Raises:
        DomainValidationError: if ``resource`` or ``share`` is not the one that produced
            ``access``. Explaining an answer with a different ACL than the one it was
            computed from would produce a plausible and wholly fictional derivation.
    """
    _check_inputs(access, resource, share)

    supplied = tuple(edges)
    builder = _Builder(access=access, resource=resource, share=share, limits=limits)
    builder.build(supplied)

    return AccessExplanation(
        access=access,
        graph=builder.graph(),
        paths=tuple(builder.paths),
        removal_targets=builder.removal_targets(),
        cycles=find_cycles(supplied, Direction.UP) if supplied else (),
        limits=limits,
        truncation=tuple(sorted(builder.truncation)),
        findings=tuple(builder.findings),
    )


def _check_inputs(access: EffectiveAccess, resource: ResourceDacl, share: ShareDacl | None) -> None:
    if resource.resource_key != access.resource_key:
        raise DomainValidationError(
            f"The answer is about {access.resource_key!r} and the DACL supplied is "
            f"{resource.resource_key!r}. Explaining one with the other would derive a "
            "plausible chain of causes for rights nobody holds.",
            field="resource",
        )
    share_key = None if share is None else share.share_key
    if share_key != access.share_key:
        raise DomainValidationError(
            f"The answer was resolved against share {access.share_key!r} and share "
            f"{share_key!r} was supplied.",
            field="share",
        )


@dataclass
class _Builder:
    """Mutable working state for one explanation. Never leaves this module."""

    access: EffectiveAccess
    resource: ResourceDacl
    share: ShareDacl | None
    limits: ExplanationLimits

    def __post_init__(self) -> None:
        self.nodes: dict[str, ExplanationNode] = {}
        self.edges: dict[str, ExplanationEdge] = {}
        self.paths: list[CausalPath] = []
        self.truncation: set[PathTruncation] = set()
        self.findings: list[AccessFinding] = []
        self._chains: dict[str, tuple[MembershipPath, ...]] = {}
        self._subgraph: tuple[GraphEdge, ...] = ()
        self._novel: dict[RightsLayer, dict[int, int]] = {}

    # -- assembly ----------------------------------------------------------------------

    def build(self, edges: Sequence[GraphEdge]) -> None:
        self._subgraph = tuple(edges)
        token = self.access.token
        if not token.membership_complete:
            self.truncation.add(PathTruncation.MEMBERSHIP_INCOMPLETE)

        self._object_node()
        self._principal_node(token.subject.key, token.subject.sid, token.subject.display_name)

        for layer, evaluation in self._evaluations():
            self._novel[layer] = self._novel_contributions(evaluation)
            self._ownership_path(evaluation, layer)
            for applied in self._applied(evaluation):
                self._paths_for(applied, layer)

        self.paths.sort(key=_path_order)

    def _novel_contributions(self, evaluation: AclEvaluation) -> dict[int, int]:
        """What each entry settled **that nothing before it had already settled**.

        The evaluator's own :attr:`AppliedAce.contributed` answers a narrower question: an
        Allow contributes whatever an earlier *Deny* had not taken, and it says nothing
        about an earlier Allow having granted the same rights already. That is right for
        computing a mask — a second Allow for rights already held changes no bit — and
        wrong for explaining one, because "this entry is why Alice has Modify" is false of
        the second of two identical grants.

        So the walk is replayed here, accumulating in each direction separately. An entry
        whose novel contribution is empty is :attr:`PathEffect.REDUNDANT`: the ACL says it,
        and removing it would change nothing.

        The owner's implicit rights seed the granted accumulator, because Windows grants
        them **before** it reads an ACE (see :mod:`app.access_engine.evaluation`). An ACE
        that hands an owner the rights ownership already confers is redundant, and it is
        worth saying so: it is very often the entry somebody added believing it was what
        granted the access.
        """
        granted = (
            0
            if evaluation.owner_rights is None
            else evaluation.owner_rights.expand_generics().value
        )
        denied = 0
        novel: dict[int, int] = {}
        for applied in self._applied(evaluation):
            contributed = applied.contributed.expand_generics().value
            if applied.is_allow:
                novel[applied.position] = contributed & ~granted
                granted |= contributed
            else:
                novel[applied.position] = contributed & ~denied
                denied |= contributed
        return novel

    def _evaluations(self) -> tuple[tuple[RightsLayer, AclEvaluation], ...]:
        """Both layers, NTFS first — the order :attr:`EffectiveAccess.grant_entries` uses."""
        pairs: list[tuple[RightsLayer, AclEvaluation]] = [(RightsLayer.NTFS, self.access.ntfs)]
        if self.access.share is not None:
            pairs.append((RightsLayer.SMB_SHARE, self.access.share))
        return tuple(pairs)

    @staticmethod
    def _applied(evaluation: AclEvaluation) -> tuple[AppliedAce, ...]:
        """Every entry that matched the token, in the order the DACL stores them.

        ``superseded`` is included deliberately: an Allow that grants nothing is a finding
        about the ACL, and an explanation that drops it leaves an administrator reading a
        trustee list that does not match the paths shown.
        """
        matched = [*evaluation.granted_by, *evaluation.denied_by, *evaluation.superseded]
        matched.sort(key=lambda applied: applied.position)
        return tuple(matched)

    # -- paths -------------------------------------------------------------------------

    def _paths_for(self, applied: AppliedAce, layer: RightsLayer) -> None:
        """Every chain from the subject to one matched ACE, as one path each."""
        ace_node = self._ace_node(applied, layer)
        relation = PathRelation.GRANT if applied.is_allow else PathRelation.DENY
        edge_kind = EdgeKind.GRANT if applied.is_allow else EdgeKind.DENY
        object_edge = self._add_edge(
            ExplanationEdge(
                edge_id=f"{edge_kind.value}:{ace_node.node_id}",
                kind=edge_kind,
                source=ace_node.node_id,
                target=self._object_node_id(),
                ace_key=applied.entry.ace_key,
            )
        )

        for chain in self._chains_to(applied.matched):
            if len(self.paths) >= self.limits.max_paths:
                self.truncation.add(PathTruncation.MAX_PATHS)
                return
            self._append_path(applied, layer, relation, ace_node, object_edge, chain)

    def _append_path(
        self,
        applied: AppliedAce,
        layer: RightsLayer,
        relation: PathRelation,
        ace_node: ExplanationNode,
        object_edge: ExplanationEdge,
        chain: MembershipPath,
    ) -> None:
        node_ids: list[str] = []
        edge_ids: list[str] = []
        token = self.access.token

        for index, key in enumerate(chain.nodes):
            if index == 0:
                node = self._principal_node(key, token.subject.sid, token.subject.display_name)
            else:
                node = self._membership_node(key, token)
            node_ids.append(node.node_id)
            if index == 0:
                continue
            previous = node_ids[index - 1]
            hop = chain.edges[index - 1] if index - 1 < len(chain.edges) else None
            edge = self._membership_edge(previous, node.node_id, hop)
            edge_ids.append(edge.edge_id)

        trustee_node_id = node_ids[-1]
        trustee_edge = self._add_edge(
            ExplanationEdge(
                edge_id=f"trustee:{trustee_node_id}->{ace_node.node_id}",
                kind=EdgeKind.TRUSTEE,
                source=trustee_node_id,
                target=ace_node.node_id,
                ace_key=applied.entry.ace_key,
            )
        )
        edge_ids.append(trustee_edge.edge_id)

        node_ids.append(ace_node.node_id)
        edge_ids.append(object_edge.edge_id)
        node_ids.append(self._object_node_id())

        novel = self._novel.get(layer, {}).get(applied.position, 0)
        delivered, constrained = self._crossing(novel, layer)
        effect = self._effect(novel, delivered, constrained)
        self.paths.append(
            CausalPath(
                path_id=f"p{len(self.paths):04d}",
                layer=layer,
                relation=relation,
                effect=effect,
                chain=tuple(chain.nodes),
                node_ids=tuple(node_ids),
                edge_ids=tuple(edge_ids),
                trustee_key=applied.entry.trustee_key,
                ace_position=applied.position,
                ace_key=applied.entry.ace_key,
                ace_rights=applied.considered,
                layer_rights=RightsMask(novel, layer),
                effective_rights=delivered,
                constrained_rights=constrained,
                assumed=applied.matched.is_assumed,
                via_group=applied.via_group,
                inherited=applied.entry.is_inherited,
            )
        )

    def _crossing(self, novel: int, layer: RightsLayer) -> tuple[RightsMask, RightsMask]:
        """Split one entry's novel contribution into what survives and what is capped.

        The other layer's mask is the whole of the crossing: the layers intersect, so a
        right this entry settles is worth something to the final answer exactly when the
        other layer also permits it. For a Deny, "worth something" means the right it
        removes is one the other layer would otherwise have allowed through — a Deny on a
        right the share never granted changes no outcome.
        """
        other = self._other_layer(layer)
        if other is None:
            return RightsMask.effective(novel), RightsMask.effective(0)
        return RightsMask.effective(novel & other), RightsMask.effective(novel & ~other)

    def _other_layer(self, layer: RightsLayer) -> int | None:
        """The mask the *other* ACL permits, or ``None`` when there is no other ACL.

        ``None`` covers local access, which does not pass a share ACL at all, and a share
        ACL nobody read — where the resolver already reports an upper bound and this module
        must not invent a cap it has no evidence for.
        """
        if self.access.share is None:
            return None
        if layer is RightsLayer.NTFS:
            return self.access.share.rights.expand_generics().value
        return self.access.ntfs.rights.expand_generics().value

    @staticmethod
    def _effect(novel: int, delivered: RightsMask, constrained: RightsMask) -> PathEffect:
        if not novel:
            return PathEffect.REDUNDANT
        if delivered.is_empty and not constrained.is_empty:
            return PathEffect.CONSTRAINED
        return PathEffect.CONTRIBUTES

    def _ownership_path(self, evaluation: AclEvaluation, layer: RightsLayer) -> None:
        """Rights the subject holds by **owning** the object, which no ACE explains.

        The cause that is invisible in every ACL viewer. An owner holds ``READ_CONTROL``
        and ``WRITE_DAC`` whatever the DACL says, so an owner explicitly denied everything
        can still rewrite the ACL and grant themselves the rest. An explanation that lists
        only ACEs would report those rights as uncaused, which is the one thing a causality
        engine may not do.

        It is not a removal target: the remediation is to change the object's owner, which
        is not the deletion of an edge and is not something this analysis can measure.
        """
        if evaluation.owner_rights is None:
            return
        novel = evaluation.owner_rights.expand_generics().value
        if not novel:
            return

        subject = self.access.token.subject
        principal = self._principal_node(subject.key, subject.sid, subject.display_name)
        edge = self._add_edge(
            ExplanationEdge(
                edge_id=f"ownership:{principal.node_id}->{self._object_node_id()}",
                kind=EdgeKind.OWNERSHIP,
                source=principal.node_id,
                target=self._object_node_id(),
            )
        )
        delivered, constrained = self._crossing(novel, layer)
        self.paths.append(
            CausalPath(
                path_id=f"p{len(self.paths):04d}",
                layer=layer,
                relation=PathRelation.GRANT,
                effect=self._effect(novel, delivered, constrained),
                chain=(subject.key,),
                node_ids=(principal.node_id, self._object_node_id()),
                edge_ids=(edge.edge_id,),
                trustee_key=subject.key,
                ace_position=OWNERSHIP_POSITION,
                ace_key=None,
                ace_rights=RightsMask(novel, layer),
                layer_rights=RightsMask(novel, layer),
                effective_rights=delivered,
                constrained_rights=constrained,
                assumed=False,
                via_group=False,
                inherited=False,
            )
        )

    # -- membership chains -------------------------------------------------------------

    def _chains_to(self, matched: TokenSid) -> tuple[MembershipPath, ...]:
        """Every simple chain from the subject to one ACL trustee, bounded and ordered.

        Cached per trustee: one ACL can name the same group in several entries, and the
        chains to it are the same each time.
        """
        cached = self._chains.get(matched.key)
        if cached is not None:
            return cached

        chains = self._enumerate(matched)
        self._chains[matched.key] = chains
        return chains

    def _enumerate(self, matched: TokenSid) -> tuple[MembershipPath, ...]:
        subject = self.access.token.subject.key
        if matched.origin is SidOrigin.SUBJECT:
            return (MembershipPath(nodes=(subject,)),)
        if matched.is_assumed:
            # No edge exists and none is claimed. The chain is two nodes long and the edge
            # between them is an assumption, which is what ASSUMED_MEMBERSHIP records.
            return (MembershipPath(nodes=(subject, matched.key)),)

        if not self._subgraph:
            self.truncation.add(PathTruncation.EDGES_NOT_SUPPLIED)
            return (self._recorded_chain(matched),)

        enumeration = simple_paths(
            self._subgraph,
            subject,
            matched.key,
            GraphLimits(
                max_depth=self.limits.max_depth,
                max_paths=self.limits.max_paths_per_trustee,
            ),
        )
        for reason in enumeration.truncation:
            self.truncation.add(
                PathTruncation.MAX_PATHS_PER_TRUSTEE
                if reason is TruncationReason.MAX_PATHS
                else PathTruncation.MAX_DEPTH
            )
        if not enumeration.paths:
            # The token says this trustee is reachable and the supplied subgraph does not
            # explain how. Reporting no path would contradict the answer being explained,
            # so the chain the traversal recorded is reported and the gap is named.
            self.truncation.add(PathTruncation.EDGES_NOT_SUPPLIED)
            self.findings.append(
                AccessFinding(
                    AccessCondition.MEMBERSHIP_TRUNCATED,
                    {"trustee_key": matched.key, "reason": "no_supplied_edge_explains_it"},
                )
            )
            return (self._recorded_chain(matched),)
        return enumeration.paths

    def _recorded_chain(self, matched: TokenSid) -> MembershipPath:
        """The single chain the token's traversal recorded, when nothing better is available.

        ``TokenSid.path`` is the *shortest* chain the expansion found, subject first. It is
        a real explanation and it is not the only one, which is why every caller of this
        also records a truncation.
        """
        subject = self.access.token.subject.key
        recorded = matched.path or (subject, matched.key)
        if recorded[0] != subject:
            recorded = (subject, *recorded)
        if recorded[-1] != matched.key:
            recorded = (*recorded, matched.key)
        return MembershipPath(nodes=tuple(dict.fromkeys(recorded)))

    # -- nodes and edges ---------------------------------------------------------------

    def _object_node_id(self) -> str:
        return f"resource:{self.access.resource_key}"

    def _object_node(self) -> ExplanationNode:
        return self._add_node(
            ExplanationNode(
                node_id=self._object_node_id(),
                kind=NodeKind.RESOURCE,
                key=self.access.resource_key,
            )
        )

    def _principal_node(
        self, key: str, sid: str | None, display_name: str | None
    ) -> ExplanationNode:
        return self._add_node(
            ExplanationNode(
                node_id=f"principal:{key}",
                kind=NodeKind.PRINCIPAL,
                key=key,
                sid=sid,
                display_name=display_name,
            )
        )

    def _membership_node(self, key: str, token: SubjectToken) -> ExplanationNode:
        """A node on a chain above the subject: a group, or an assumed trustee.

        Anything the subject reaches that is not the subject is a container of some kind;
        the one distinction that matters is whether it is a real group with members or a
        SID Windows supplies, because only the first can be edited.
        """
        entry = token.entry(key)
        kind = (
            NodeKind.ASSUMED_TRUSTEE if entry is not None and entry.is_assumed else NodeKind.GROUP
        )
        return self._add_node(
            ExplanationNode(
                node_id=f"{'assumed' if kind is NodeKind.ASSUMED_TRUSTEE else 'group'}:{key}",
                kind=kind,
                key=key,
                sid=None if entry is None else entry.sid,
                display_name=None if entry is None else entry.display_name,
            )
        )

    def _ace_node(self, applied: AppliedAce, layer: RightsLayer) -> ExplanationNode:
        """One node per ACE **position**, not per trustee.

        Two entries naming one group are two different causes with two different
        remediations, and an ACL's order is what decides which of them settles a right.
        """
        prefix = "ntfs-ace" if layer is RightsLayer.NTFS else "smb-ace"
        target = self.access.resource_key if layer is RightsLayer.NTFS else self.access.share_key
        return self._add_node(
            ExplanationNode(
                node_id=f"{prefix}:{target}:{applied.position:04d}",
                kind=NodeKind.NTFS_ACE if layer is RightsLayer.NTFS else NodeKind.SMB_ACE,
                key=applied.entry.ace_key or f"{target}#{applied.position}",
                sid=applied.entry.trustee_sid,
                display_name=applied.entry.trustee_name,
                layer=layer,
                position=applied.position,
            )
        )

    def _membership_edge(self, source: str, target: str, hop: GraphEdge | None) -> ExplanationEdge:
        """One hop of a chain. ``hop`` is ``None`` when no stored edge explains it."""
        kind = EdgeKind.MEMBERSHIP if hop is not None else EdgeKind.ASSUMED_MEMBERSHIP
        return self._add_edge(
            ExplanationEdge(
                edge_id=f"{kind.value}:{source}->{target}",
                kind=kind,
                source=source,
                target=target,
                membership_edge_key=None if hop is None else hop.edge_key,
            )
        )

    def _add_node(self, node: ExplanationNode) -> ExplanationNode:
        return self.nodes.setdefault(node.node_id, node)

    def _add_edge(self, edge: ExplanationEdge) -> ExplanationEdge:
        return self.edges.setdefault(edge.edge_id, edge)

    def graph(self) -> ExplanationGraph:
        return ExplanationGraph(
            nodes=tuple(sorted(self.nodes.values(), key=lambda node: node.node_id)),
            edges=tuple(sorted(self.edges.values(), key=lambda edge: edge.edge_id)),
        )

    # -- removal -----------------------------------------------------------------------

    def removal_targets(self) -> tuple[RemovalTarget, ...]:
        """Measure every removable edge that a path actually runs through.

        Only edges on a path are candidates: an ACE nobody's chain reaches is not a cause
        of this subject's access, and offering it as a remediation would send somebody to
        edit an entry that has nothing to do with the answer.
        """
        candidates = self._candidates()
        targets: list[RemovalTarget] = []
        for edge_id in candidates:
            if len(targets) >= self.limits.max_removal_targets:
                self.truncation.add(PathTruncation.MAX_REMOVAL_TARGETS)
                break
            target = self._measure(edge_id)
            if target is not None:
                targets.append(target)
        return tuple(targets)

    def _candidates(self) -> tuple[str, ...]:
        """Removable edges lying on at least one path, in a deterministic order.

        Ordered by the first path that uses each edge, so the list reads in the same order
        as the paths themselves rather than in whatever order a set iterates.
        """
        ordered: dict[str, None] = {}
        for path in self.paths:
            for edge_id in path.edge_ids:
                edge = self.edges.get(edge_id)
                if edge is not None and edge.is_removable:
                    ordered.setdefault(edge_id, None)
        return tuple(ordered)

    def _measure(self, edge_id: str) -> RemovalTarget | None:
        """Re-run the whole access check with one edge gone and diff the result.

        Measured rather than reasoned about. The two failure modes of reasoning are both
        real and both silent: removing an Allow can uncover a redundant Allow that grants
        the same rights, and removing a membership can take a Deny with it and *widen*
        access. A re-evaluation reports both, and reports nothing removed whenever an
        alternate path survives.
        """
        edge = self.edges.get(edge_id)
        if edge is None:
            return None

        surviving = self._surviving_keys(edge)
        rights = self._reevaluate(surviving, excluded_ace=self._ace_behind(edge))
        before = self.access.rights.expand_generics().value
        after = rights.value

        removed_paths = tuple(path.path_id for path in self.paths if edge_id in path.edge_ids)
        survivors = tuple(
            path.path_id
            for path in self.paths
            if path.effect is PathEffect.CONTRIBUTES
            and path.is_grant
            and edge_id not in path.edge_ids
        )
        return RemovalTarget(
            edge_id=edge_id,
            kind=edge.kind,
            source=edge.source,
            target=edge.target,
            rights_removed=RightsMask.effective(before & ~after),
            rights_added=RightsMask.effective(after & ~before),
            rights_after=rights,
            revokes_all_access=self.access.has_access and after == 0,
            alternate_paths=survivors,
            paths_removed=removed_paths,
        )

    def _surviving_keys(self, edge: ExplanationEdge) -> frozenset[str]:
        """Which token keys the subject still reaches once a membership edge is cut.

        Recomputed from the subgraph rather than by deleting one entry from the token: a
        group removed low on a chain takes everything above it with it, and a token edited
        one key at a time would keep groups the subject can no longer reach.

        Assumed SIDs always survive. Nobody can be removed from ``Everyone``, and a
        removal analysis that pretended otherwise would propose a remediation that does
        not exist.
        """
        token = self.access.token
        if edge.kind is not EdgeKind.MEMBERSHIP or edge.membership_edge_key is None:
            return token.keys

        remaining = [hop for hop in self._subgraph if hop.edge_key != edge.membership_edge_key]
        reachable = _reachable_upward(remaining, token.subject.key)
        return frozenset(
            entry.key
            for entry in token.entries
            if entry.origin is not SidOrigin.GROUP_MEMBERSHIP or entry.key in reachable
        )

    def _ace_behind(self, edge: ExplanationEdge) -> tuple[RightsLayer, int] | None:
        """The ACE a trustee edge points at, as the layer and position that identify it.

        Read off the target **node**, which already carries both, rather than parsed back
        out of an id string: an id is for identity and a resource key can contain anything
        a UNC path can.
        """
        if edge.kind is not EdgeKind.TRUSTEE:
            return None
        node = self.nodes.get(edge.target)
        if node is None or node.layer is None or node.position is None:
            return None
        return (node.layer, node.position)

    def _reevaluate(
        self, keys: frozenset[str], *, excluded_ace: tuple[RightsLayer, int] | None
    ) -> RightsMask:
        """The final effective mask for a reduced token and a reduced ACL.

        Runs the same :func:`evaluate_acl` and the same layer crossing the resolver ran, so
        a removal is measured by the engine that produced the answer rather than by a
        second, simpler model of it that would eventually disagree.
        """
        token = _restricted(self.access.token, keys)
        ntfs_entries = _without(self.resource.entries, excluded_ace, RightsLayer.NTFS)
        ntfs = evaluate_acl(ntfs_entries, token, layer=RightsLayer.NTFS, facts=self.resource.facts)

        if self.share is None:
            return effective_rights(ntfs=ntfs.rights, path=self.access.path).rights
        if not self.share.observed:
            # The resolver reported NTFS as an upper bound here rather than crossing with a
            # share ACL nobody read. A removal is measured against the same bound.
            return RightsMask.effective(ntfs.rights.expand_generics().value)

        share_entries = _without(self.share.entries, excluded_ace, RightsLayer.SMB_SHARE)
        share = evaluate_acl(
            share_entries, token, layer=RightsLayer.SMB_SHARE, facts=UNOWNED_PRESENT_DACL
        )
        return effective_rights(ntfs=ntfs.rights, path=self.access.path, share=share.rights).rights


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _path_order(path: CausalPath) -> tuple[int, int, int, tuple[str, ...]]:
    """Total order over paths: NTFS before share, then ACL order, then shortest chain.

    Deterministic to the last field, so an explanation over unchanged data is byte-identical
    between runs and a diff of two explanations means the estate changed.
    """
    layer_rank = 0 if path.layer is RightsLayer.NTFS else 1
    return (layer_rank, path.ace_position, path.length, path.chain)


def _without(
    entries: Sequence[AclEntry], excluded: tuple[RightsLayer, int] | None, layer: RightsLayer
) -> tuple[AclEntry, ...]:
    """The DACL with one entry removed, **keeping the order of the rest**.

    Order is load-bearing: rebuilding the list preserves it, and re-sorting it would change
    the answer the removal is being measured against.
    """
    if excluded is None or excluded[0] is not layer:
        return tuple(entries)
    return tuple(entry for index, entry in enumerate(entries) if index != excluded[1])


def _restricted(token: SubjectToken, keys: frozenset[str]) -> SubjectToken:
    """The same token carrying only ``keys``, with its findings and assumption intact."""
    return replace(token, entries=tuple(entry for entry in token.entries if entry.key in keys))


def _reachable_upward(edges: Sequence[GraphEdge], root: str) -> frozenset[str]:
    """Every key reachable from ``root`` by following memberships upward.

    Breadth-first with a visited set, so a membership cycle terminates the walk rather than
    unrolling it — the same guarantee :func:`app.domain.expand` gives, restated here
    because this runs over a subgraph already in memory and must not go back to a provider.
    """
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge.member_key, []).append(edge.group_key)

    seen: set[str] = set()
    frontier = [root]
    while frontier:
        node = frontier.pop()
        for group in outgoing.get(node, ()):
            if group in seen:
                continue
            seen.add(group)
            frontier.append(group)
    return frozenset(seen)
