r"""An access investigation: the answer, the route that carries it, and what is still open.

:mod:`app.access_engine.causality` explains an answer completely: every path, what each one
is worth, and what removing each edge would do. That completeness is the point of it, and it
is also why a reviewer handed one cannot see where to start. Fatima's access to a project
folder can run through a dozen chains on two ACLs, most of them redundant. The investigator's
questions are narrower and always the same seven:

1. Is access present?
2. Which route carries it?
3. Is that route a direct entry, a group membership, or an assumption?
4. What evidence supports it?
5. What could not be confirmed?
6. Does that change the conclusion?
7. What should be looked at next?

This module answers them **from the explanation and nothing else**. It does not evaluate an
ACL, match a trustee or reason about which edge looked important. Every value below is a
selection from, or a classification of, something the engine already computed and the
response already carries, so it cannot disagree with the explanation it summarizes. Four
rules hold it there:

* **Access presence is the verdict.** ``granted`` is present, ``denied`` and ``no_grant`` are
  absent, ``indeterminate`` is undetermined. Nothing here can turn an inconclusive answer
  into a negative one; ADG-DEMO-01's SYSTEM invariants reach the summary unchanged because
  the summary has no second opinion.
* **The primary route is chosen among contributing paths only, one per layer.** A route
  that matched and settled nothing (redundant) or that the other ACL withholds
  (constrained) is not what carries the access, however alarming its entry looks. Over SMB
  an answer has *two* routes, one through each ACL, because both must allow a right for it
  to survive; reporting one of them as "the" path would hide the other half.
* **Removal effects are read from the measured removal targets.** "Removing this changes
  nothing" is the engine's re-evaluation, never an inference from the path list.
* **Whether an open question affects the conclusion follows from its direction, not its
  name.** A finding that could only widen the rights (``may_understate``) cannot undo access
  that is already established; one that could narrow them (``may_overstate``) can, so on a
  granted answer it reaches the conclusion itself. On an answer without access the roles
  swap: only a finding that could hide a grant matters. The direction sets live in
  :mod:`app.access_engine.conditions` and are not restated here.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.access_engine.causality import (
    OWNERSHIP_POSITION,
    AccessExplanation,
    CausalPath,
    RemovalTarget,
)
from app.access_engine.conditions import AccessCondition, AccessFinding
from app.access_engine.rights import RightsLayer, RightsMask
from app.access_engine.verdict import AccessOutcome, classify_access

__all__ = [
    "AccessPresence",
    "ConclusionImpact",
    "EntryPlacement",
    "EvidenceState",
    "Investigation",
    "NextStep",
    "NextStepCode",
    "OpenQuestion",
    "PrimaryRoute",
    "RemovalEffect",
    "RouteKind",
    "investigate",
]


class AccessPresence(StrEnum):
    """Question 1, read straight off the verdict."""

    PRESENT = "present"
    """Rights survive both layers (``granted``)."""

    ABSENT = "absent"
    """A conclusive negative (``denied`` or ``no_grant``)."""

    UNDETERMINED = "undetermined"
    """``indeterminate``: not answerable from what has been collected. Never "absent"."""


class RouteKind(StrEnum):
    """Question 3: how the entry at the end of a route reaches the subject."""

    DIRECT = "direct"
    """The entry names the subject itself."""

    GROUP = "group"
    """Through one or more observed group memberships."""

    ASSUMED = "assumed"
    """Through a SID Windows places in the token (Everyone, Authenticated Users). Real
    access, resting on no membership anybody could remove."""

    OWNERSHIP = "ownership"
    """The subject owns the object. No entry is involved."""


class EntryPlacement(StrEnum):
    """Question 3, the other axis: where the entry at the end of the route was set."""

    EXPLICIT = "explicit"
    """On this object's own ACL (or the share's ACL, which has no inheritance)."""

    INHERITED = "inherited"
    """On an ancestor folder, and inherited down to this one."""

    OWNER = "owner"
    """Not an entry at all: implicit owner rights."""


class RemovalEffect(StrEnum):
    """What the engine measured when the subject's own first link of a route was removed."""

    REVOKES_ACCESS = "revokes_access"
    """Nothing survives. This route is the only reason for the access."""

    NARROWS_ACCESS = "narrows_access"
    """Some rights go, some survive through other routes."""

    CHANGES_NOTHING = "changes_nothing"
    """Other routes deliver every right this one does."""

    WIDENS_ACCESS = "widens_access"
    """The edge also carried a Deny; removing it would grant more."""


class EvidenceState(StrEnum):
    """How an open question stands, in the vocabulary ADG-DEMO-01 fixed for SYSTEM.

    A display classification only. Whether a question affects the conclusion is decided by
    its direction (:class:`ConclusionImpact`), never by this value, so an unfamiliar
    condition that falls through to :attr:`CONTEXT` still gets the right impact.
    """

    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    ASSUMED = "assumed"
    DERIVED = "derived"
    CONTEXT = "context"
    """A qualification worth reading (a protected DACL, escalation rights) that is not
    about missing or disputed evidence."""


class ConclusionImpact(StrEnum):
    """Question 6, per open question and for the answer as a whole. Ordered by severity."""

    NONE = "none"
    """Recorded for context. Neither direction of error is possible from it."""

    EXTENT = "extent"
    """The access stands. Only how far it reaches is open: the rights could be wider than
    shown on a granted answer."""

    PRESENCE = "presence"
    """The conclusion itself rests on it. On a granted answer the rights could be narrower,
    possibly to nothing; on any other answer a grant could be hidden."""


_IMPACT_RANK: Final[dict[ConclusionImpact, int]] = {
    ConclusionImpact.NONE: 0,
    ConclusionImpact.EXTENT: 1,
    ConclusionImpact.PRESENCE: 2,
}


class NextStepCode(StrEnum):
    """Question 7. A closed set, so a client words each one and a test can pin each one."""

    RESOLVE_TRUSTEE_IDENTITY = "resolve_trustee_identity"
    """An ACL names a SID whose kind no run established. Find out what it is."""

    COLLECT_GROUP_MEMBERSHIP = "collect_group_membership"
    """An ACL names a group ADG holds no members for. Collect it, or confirm it is empty."""

    SETTLE_SYSTEM_MAPPING = "settle_system_mapping"
    """SYSTEM's identity here is ambiguous, missing or conflicting (ADG-DEMO-01)."""

    RESOLVE_SUBJECT_IDENTITY = "resolve_subject_identity"
    """Nothing describes the subject, so its memberships may be incomplete."""

    COLLECT_SUBJECT_MEMBERSHIP = "collect_subject_membership"
    """The subject's membership traversal was truncated."""

    RESCAN_RESOURCE = "rescan_resource"
    """An ACL this answer depends on was not read, was projected, or was read in part."""

    REVIEW_REDUNDANT_ENTRY = "review_redundant_entry"
    """A direct entry carries the access and removing it changes nothing: other routes
    already deliver the same rights. The usual first clean-up."""

    REVIEW_SOLE_ROUTE = "review_sole_route"
    """This route is the only reason for the access. Removing its first link revokes it."""

    REVIEW_FINDING = "review_finding"
    """Any other open question that could change the answer."""

    INSPECT_EXPLANATION = "inspect_explanation"
    """Nothing above applies: the full explanation is the next place to look."""


@dataclass(frozen=True, slots=True)
class PrimaryRoute:
    """The route that carries the access through one ACL."""

    layer: RightsLayer
    path_id: str
    route: RouteKind
    placement: EntryPlacement
    membership_hops: int
    """Group memberships between the subject and the entry. 0 for a direct entry."""

    coverage: RightsMask
    """The part of the final effective mask this route's entry names on its own, carrying
    :attr:`RightsLayer.EFFECTIVE`. The ranking key: the route that alone accounts for the
    most of the answer is the one an investigator needs to see first."""

    removal_edge_id: str | None
    """The first link of this route an administrator could delete, as measured: the
    subject's membership of the first group, or the entry itself when nothing removable
    comes before it (a direct entry, or an entry reached through an assumed SID). ``None``
    for ownership, which no edge removal changes, and when the engine's removal limit was
    reached before this edge was measured."""

    removal_effect: RemovalEffect | None
    removal: RemovalTarget | None
    alternates: tuple[str, ...]
    """Other contributing grant paths on the same layer, in the engine's order."""


@dataclass(frozen=True, slots=True)
class OpenQuestion:
    """One finding the investigator has to weigh, with whether it reaches the conclusion."""

    finding: AccessFinding
    state: EvidenceState
    impact: ConclusionImpact
    about: str | None
    """The principal key the finding is about, when it is about one: the trustee an ACL
    names, or the SYSTEM subject. ``None`` for findings about an ACL or the token as such."""


@dataclass(frozen=True, slots=True)
class NextStep:
    code: NextStepCode
    about: str | None = None
    """The principal the step is about, when there is one."""

    path_id: str | None = None
    """The route the step is about, when there is one."""

    condition: AccessCondition | None = None
    """The finding the step answers, for a step that comes from an open question."""


@dataclass(frozen=True, slots=True)
class Investigation:
    """The seven answers, each traceable to a field of the explanation."""

    presence: AccessPresence
    outcome: AccessOutcome
    primary_routes: tuple[PrimaryRoute, ...]
    """At most one per layer, NTFS first. Empty unless access is present."""

    open_questions: tuple[OpenQuestion, ...]
    impact: ConclusionImpact
    """The most severe impact of any open question."""

    next_steps: tuple[NextStep, ...]
    routes_complete: bool
    """False when the explanation was truncated: another route may exist that would rank
    above the one reported, so "this is the strongest route" is a claim about the routes
    enumerated."""

    @property
    def affects_conclusion(self) -> bool:
        return self.impact is ConclusionImpact.PRESENCE


def investigate(
    explanation: AccessExplanation,
    *,
    unidentified: Collection[str] = (),
) -> Investigation:
    """Summarize one explanation as an investigation.

    Args:
        explanation: the engine's explanation of one principal/resource answer.
        unidentified: principal keys whose kind no run established — no record, or a record
            of kind ``unresolved``. The engine works in keys and does not hold principal
            records; the caller does, and only it can say which trustees are unknown SIDs
            rather than groups nobody enumerated. The difference decides which next step
            applies, never whether a question reaches the conclusion.
    """
    verdict = classify_access(explanation.access)
    presence = _presence(verdict.outcome)
    granted = presence is AccessPresence.PRESENT

    routes = _primary_routes(explanation) if granted else ()
    questions = tuple(
        OpenQuestion(
            finding=finding,
            state=_state(finding),
            impact=_impact(finding, granted=granted),
            about=_about(finding),
        )
        for finding in explanation.access.findings
    )
    impact = max(
        (question.impact for question in questions),
        key=_IMPACT_RANK.__getitem__,
        default=ConclusionImpact.NONE,
    )
    return Investigation(
        presence=presence,
        outcome=verdict.outcome,
        primary_routes=routes,
        open_questions=questions,
        impact=impact,
        next_steps=_next_steps(questions, routes, frozenset(unidentified)),
        routes_complete=explanation.complete,
    )


# --------------------------------------------------------------------------------------
# Question 1
# --------------------------------------------------------------------------------------


def _presence(outcome: AccessOutcome) -> AccessPresence:
    if outcome is AccessOutcome.GRANTED:
        return AccessPresence.PRESENT
    if outcome is AccessOutcome.INDETERMINATE:
        return AccessPresence.UNDETERMINED
    return AccessPresence.ABSENT


# --------------------------------------------------------------------------------------
# Questions 2-4
# --------------------------------------------------------------------------------------


def _primary_routes(explanation: AccessExplanation) -> tuple[PrimaryRoute, ...]:
    """The strongest contributing grant path on each layer, NTFS first.

    Ranked by :attr:`PrimaryRoute.coverage`, then by observed over assumed, then by the
    engine's own order — which is DACL order, the order Windows reaches the entries in — so
    a tie goes to the entry that actually settles the rights first.

    Coverage compares masks as bits. That is sound for exactly the reason the layer
    crossing is: an SMB and an NTFS mask are the same 32 bits under different names, and
    the effective mask is their intersection. Nothing here labels or reinterprets them.
    """
    final = explanation.access.rights.expand_generics().value
    removals = {target.edge_id: target for target in explanation.removal_targets}
    routes: list[PrimaryRoute] = []
    for layer in (RightsLayer.NTFS, RightsLayer.SMB_SHARE):
        candidates = [
            (index, path)
            for index, path in enumerate(explanation.paths)
            if path.layer is layer and path.matters and path.is_grant
        ]
        if not candidates:
            continue
        _, best = min(
            candidates,
            key=lambda item: (
                -_coverage(item[1], final).bit_count(),
                item[1].assumed,
                item[0],
            ),
        )
        routes.append(_route(best, final, explanation, removals))
    return tuple(routes)


def _coverage(path: CausalPath, final: int) -> int:
    return path.ace_rights.expand_generics().value & final


def _route(
    path: CausalPath,
    final: int,
    explanation: AccessExplanation,
    removals: dict[str, RemovalTarget],
) -> PrimaryRoute:
    edge_id = _first_removable_edge(path, explanation)
    removal = None if edge_id is None else removals.get(edge_id)
    return PrimaryRoute(
        layer=path.layer,
        path_id=path.path_id,
        route=_route_kind(path),
        placement=_placement(path),
        membership_hops=path.length,
        coverage=RightsMask.effective(_coverage(path, final)),
        removal_edge_id=edge_id if removal is not None else None,
        removal_effect=None if removal is None else _removal_effect(removal),
        removal=removal,
        alternates=tuple(
            other.path_id
            for other in explanation.paths
            if other.layer is path.layer
            and other.matters
            and other.is_grant
            and other.path_id != path.path_id
        ),
    )


def _route_kind(path: CausalPath) -> RouteKind:
    if path.ace_position == OWNERSHIP_POSITION:
        return RouteKind.OWNERSHIP
    if path.assumed:
        return RouteKind.ASSUMED
    if path.via_group:
        return RouteKind.GROUP
    return RouteKind.DIRECT


def _placement(path: CausalPath) -> EntryPlacement:
    if path.ace_position == OWNERSHIP_POSITION:
        return EntryPlacement.OWNER
    return EntryPlacement.INHERITED if path.inherited else EntryPlacement.EXPLICIT


def _first_removable_edge(path: CausalPath, explanation: AccessExplanation) -> str | None:
    """The first edge of the route, from the subject's end, that an administrator could delete.

    For a group route that is the subject's own membership, which changes this subject
    alone; for a direct entry it is the entry, which names this subject alone. For a route
    through an assumed SID it is the entry naming ``Everyone`` or ``Authenticated Users``,
    because there is no membership to remove — and removing that entry changes everybody,
    which the measured effect and the route kind together make visible.
    """
    edges = {edge.edge_id: edge for edge in explanation.graph.edges}
    for edge_id in path.edge_ids:
        edge = edges.get(edge_id)
        if edge is not None and edge.is_removable:
            return edge_id
    return None


def _removal_effect(target: RemovalTarget) -> RemovalEffect:
    if not target.rights_added.is_empty:
        return RemovalEffect.WIDENS_ACCESS
    if target.revokes_all_access:
        return RemovalEffect.REVOKES_ACCESS
    if target.rights_removed.is_empty:
        return RemovalEffect.CHANGES_NOTHING
    return RemovalEffect.NARROWS_ACCESS


# --------------------------------------------------------------------------------------
# Questions 5 and 6
# --------------------------------------------------------------------------------------

_SYSTEM_STATES: Final[dict[AccessCondition, EvidenceState]] = {
    AccessCondition.SYSTEM_MAPPING_CONFIRMED: EvidenceState.CONFIRMED,
    AccessCondition.SYSTEM_MAPPING_AMBIGUOUS: EvidenceState.AMBIGUOUS,
    AccessCondition.SYSTEM_MAPPING_MISSING: EvidenceState.MISSING,
    AccessCondition.SYSTEM_MAPPING_CONFLICTING: EvidenceState.CONFLICTING,
}

_STATES: Final[dict[AccessCondition, EvidenceState]] = {
    **_SYSTEM_STATES,
    AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED: EvidenceState.MISSING,
    AccessCondition.UNRESOLVED_TRUSTEE: EvidenceState.MISSING,
    AccessCondition.SUBJECT_UNRESOLVED: EvidenceState.MISSING,
    AccessCondition.MEMBERSHIP_TRUNCATED: EvidenceState.MISSING,
    AccessCondition.SHARE_ACL_NOT_OBSERVED: EvidenceState.MISSING,
    AccessCondition.NTFS_ACL_NOT_OBSERVED: EvidenceState.MISSING,
    AccessCondition.INTERMEDIATE_PATH_UNOBSERVED: EvidenceState.MISSING,
    AccessCondition.ACE_COUNT_MISMATCH: EvidenceState.MISSING,
    AccessCondition.ACL_TRUNCATED: EvidenceState.MISSING,
    AccessCondition.NTFS_ACL_DERIVED: EvidenceState.DERIVED,
    AccessCondition.LOGON_TYPE_TRUSTEE: EvidenceState.AMBIGUOUS,
    AccessCondition.SUBJECT_IS_A_GROUP: EvidenceState.AMBIGUOUS,
    AccessCondition.ASSUMED_TOKEN_SIDS: EvidenceState.ASSUMED,
}


def _state(finding: AccessFinding) -> EvidenceState:
    return _STATES.get(finding.condition, EvidenceState.CONTEXT)


def _impact(finding: AccessFinding, *, granted: bool) -> ConclusionImpact:
    """Whether one finding can change the conclusion, from its direction alone.

    On a granted answer, a finding that may **overstate** (the truth could be narrower)
    could take the established access away, and one that may only **understate** (the truth
    could be wider) cannot. Without access it is the other way round: only a finding that
    could hide a grant reaches the conclusion, and nothing can be narrower than nothing.
    """
    if granted:
        if finding.may_overstate:
            return ConclusionImpact.PRESENCE
        if finding.may_understate:
            return ConclusionImpact.EXTENT
        return ConclusionImpact.NONE
    if finding.may_understate:
        return ConclusionImpact.PRESENCE
    return ConclusionImpact.NONE


def _about(finding: AccessFinding) -> str | None:
    detail: dict[str, Any] = finding.detail
    for field in ("trustee_key", "subject_key"):
        value = detail.get(field)
        if isinstance(value, str) and value:
            return value
    return None


# --------------------------------------------------------------------------------------
# Question 7
# --------------------------------------------------------------------------------------

_RESCAN: Final[frozenset[AccessCondition]] = frozenset(
    {
        AccessCondition.SHARE_ACL_NOT_OBSERVED,
        AccessCondition.NTFS_ACL_NOT_OBSERVED,
        AccessCondition.NTFS_ACL_DERIVED,
        AccessCondition.INTERMEDIATE_PATH_UNOBSERVED,
        AccessCondition.ACE_COUNT_MISMATCH,
        AccessCondition.ACL_TRUNCATED,
    }
)


def _next_steps(
    questions: Sequence[OpenQuestion],
    routes: Sequence[PrimaryRoute],
    unidentified: frozenset[str],
) -> tuple[NextStep, ...]:
    """What to look at next, most consequential first, without duplicates.

    Open questions that reach the conclusion come before those that only bound it, and both
    before clean-up of the routes themselves: an investigator who removes an entry before
    settling whether the answer holds has acted on a guess.
    """
    steps: dict[tuple[NextStepCode, str | None, str | None], NextStep] = {}
    ranked = sorted(
        (question for question in questions if question.impact is not ConclusionImpact.NONE),
        key=lambda question: -_IMPACT_RANK[question.impact],
    )
    for question in ranked:
        step = _step_for(question, unidentified)
        steps.setdefault((step.code, step.about, step.path_id), step)

    for route in routes:
        clean_up = _route_step(route)
        if clean_up is not None:
            steps.setdefault((clean_up.code, clean_up.about, clean_up.path_id), clean_up)

    if not steps:
        return (NextStep(NextStepCode.INSPECT_EXPLANATION),)
    return tuple(steps.values())


def _step_for(question: OpenQuestion, unidentified: frozenset[str]) -> NextStep:
    condition = question.finding.condition
    about = question.about
    code: NextStepCode
    if condition is AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED:
        code = (
            NextStepCode.RESOLVE_TRUSTEE_IDENTITY
            if about is not None and about in unidentified
            else NextStepCode.COLLECT_GROUP_MEMBERSHIP
        )
    elif condition is AccessCondition.UNRESOLVED_TRUSTEE:
        code = NextStepCode.RESOLVE_TRUSTEE_IDENTITY
    elif condition in _SYSTEM_STATES and condition is not AccessCondition.SYSTEM_MAPPING_CONFIRMED:
        # A confirmed mapping is settled; what remains open about it is the extent of the
        # rights (LocalSystem's own groups are not modeled), which is a finding to read.
        code = NextStepCode.SETTLE_SYSTEM_MAPPING
    elif condition is AccessCondition.SUBJECT_UNRESOLVED:
        code = NextStepCode.RESOLVE_SUBJECT_IDENTITY
    elif condition is AccessCondition.MEMBERSHIP_TRUNCATED:
        code = NextStepCode.COLLECT_SUBJECT_MEMBERSHIP
    elif condition in _RESCAN:
        code = NextStepCode.RESCAN_RESOURCE
    else:
        code = NextStepCode.REVIEW_FINDING
    return NextStep(code, about=about, condition=condition)


def _route_step(route: PrimaryRoute) -> NextStep | None:
    if route.removal_effect is RemovalEffect.REVOKES_ACCESS:
        return NextStep(NextStepCode.REVIEW_SOLE_ROUTE, path_id=route.path_id)
    if route.route is RouteKind.DIRECT and route.removal_effect is RemovalEffect.CHANGES_NOTHING:
        return NextStep(NextStepCode.REVIEW_REDUNDANT_ENTRY, path_id=route.path_id)
    return None
