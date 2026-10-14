"""How complete a traversal's answer is, and what an empty one means.

A path engine over a privilege graph is most dangerous when it says nothing: "no path"
reads as "safe". ADG's graph is honest about its edges (GRAPH-01B); this module keeps the
answers built from them equally honest. Every answer carries:

* **an outcome** (:class:`PathOutcome`) chosen by one pure function, :func:`classify`, with a
  fixed precedence, so that "nothing connects these two nodes in a complete, current graph"
  and "nothing was found because the graph is behind, or the search was cut short, or it met
  something the graph cannot see past" are different values a client branches on — the
  position ADR-0016 takes for access answers;
* **every reason** (:class:`CompletenessReason`) the answer is short of complete, each with an
  operator-facing sentence and a category;
* **the graph's readiness** (:class:`GraphReadiness`), read from the projector's own pass log
  (``RelationshipProjector.state``), so an answer over a graph that has not derived what was
  collected says so.

What no answer can see — relationship types ADG does not collect — is stated separately on
every response (:func:`app.traversal.semantics.coverage_gaps`). It is never a reason an answer
is incomplete, because no search could ever complete it; it is the boundary of the model.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

__all__ = [
    "REASON_CATEGORY",
    "REASON_DESCRIPTIONS",
    "CompletenessReason",
    "GraphReadiness",
    "PathOutcome",
    "ReasonCategory",
    "classify",
    "conclusive",
    "ordered",
]


class ReasonCategory(StrEnum):
    EVIDENCE = "evidence"
    """The graph does not yet reflect what was collected, or nothing was."""

    TRUNCATION = "truncation"
    """A limit stopped the search before it could finish."""

    BOUNDARY = "boundary"
    """The search met something the graph cannot see past."""

    POLICY = "policy"
    """The caller's traversal policy declined relationships that exist."""

    NOTICE = "notice"
    """Worth saying, and does not weaken the answer."""


class CompletenessReason(StrEnum):
    """Why an answer is short of complete. A closed vocabulary: a client branches on it."""

    NOTHING_COLLECTED = "nothing_collected"
    PROJECTION_FAILED = "projection_failed"
    PROJECTION_BEHIND = "projection_behind"
    MAX_DEPTH = "max_depth"
    MAX_NODES = "max_nodes"
    MAX_EDGES = "max_edges"
    MAX_PATHS = "max_paths"
    SEARCH_BUDGET = "search_budget"
    UNKNOWN_RELATIONSHIPS = "unknown_relationships"
    UNRESOLVED_BOUNDARY = "unresolved_boundary"
    IMPLICIT_MEMBERSHIP_BOUNDARY = "implicit_membership_boundary"
    EXCLUDED_BY_POLICY = "excluded_by_policy"
    COLLECTION_IN_PROGRESS = "collection_in_progress"


REASON_CATEGORY: Final[Mapping[CompletenessReason, ReasonCategory]] = {
    CompletenessReason.NOTHING_COLLECTED: ReasonCategory.EVIDENCE,
    CompletenessReason.PROJECTION_FAILED: ReasonCategory.EVIDENCE,
    CompletenessReason.PROJECTION_BEHIND: ReasonCategory.EVIDENCE,
    CompletenessReason.MAX_DEPTH: ReasonCategory.TRUNCATION,
    CompletenessReason.MAX_NODES: ReasonCategory.TRUNCATION,
    CompletenessReason.MAX_EDGES: ReasonCategory.TRUNCATION,
    CompletenessReason.MAX_PATHS: ReasonCategory.TRUNCATION,
    CompletenessReason.SEARCH_BUDGET: ReasonCategory.TRUNCATION,
    CompletenessReason.UNKNOWN_RELATIONSHIPS: ReasonCategory.BOUNDARY,
    CompletenessReason.UNRESOLVED_BOUNDARY: ReasonCategory.BOUNDARY,
    CompletenessReason.IMPLICIT_MEMBERSHIP_BOUNDARY: ReasonCategory.BOUNDARY,
    CompletenessReason.EXCLUDED_BY_POLICY: ReasonCategory.POLICY,
    CompletenessReason.COLLECTION_IN_PROGRESS: ReasonCategory.NOTICE,
}

REASON_DESCRIPTIONS: Final[Mapping[CompletenessReason, str]] = {
    CompletenessReason.NOTHING_COLLECTED: (
        "No scan run has been recorded. An empty answer means nobody looked."
    ),
    CompletenessReason.PROJECTION_FAILED: (
        "A derivation pass failed for a completed run and no later pass covered it. The graph "
        "does not reflect that run's facts."
    ),
    CompletenessReason.PROJECTION_BEHIND: (
        "Completed runs exist that no successful derivation pass covered (an upgrade that has "
        "not run 'python -m app.operations rebuild-graph', for one). The graph may not reflect "
        "their facts."
    ),
    CompletenessReason.MAX_DEPTH: (
        "The depth limit stopped the search with relationships still to follow: longer routes "
        "may exist."
    ),
    CompletenessReason.MAX_NODES: (
        "The node limit stopped the search: what was found is a lower bound."
    ),
    CompletenessReason.MAX_EDGES: (
        "The relationship-read limit stopped the search: what was found is a lower bound."
    ),
    CompletenessReason.MAX_PATHS: "More paths exist than the path cap returns.",
    CompletenessReason.SEARCH_BUDGET: (
        "Enumerating alternate paths reached its work budget: more paths may exist."
    ),
    CompletenessReason.UNKNOWN_RELATIONSHIPS: (
        "The search met relationships whose evidence is no longer stored and that nothing "
        "recorded gone. They were not followed, and they may still hold."
    ),
    CompletenessReason.UNRESOLVED_BOUNDARY: (
        "The search reached a principal the evidence could not resolve, such as a foreign "
        "security principal. Who holds it lies outside the collected domain."
    ),
    CompletenessReason.IMPLICIT_MEMBERSHIP_BOUNDARY: (
        "The search reached a SID Windows adds to a token at logon (Everyone, Authenticated "
        "Users, a logon-session SID). Membership of it is not a relationship, so routes "
        "through it are not followed from any principal."
    ),
    CompletenessReason.EXCLUDED_BY_POLICY: (
        "The traversal policy declined current relationships the search met (conditional or "
        "unresolved ones under confirmed_only). A route through them may exist."
    ),
    CompletenessReason.COLLECTION_IN_PROGRESS: (
        "A scan run is still collecting. The graph reflects completed runs only."
    ),
}

_ORDER: Final = {reason: index for index, reason in enumerate(CompletenessReason)}


def ordered(reasons: Iterable[CompletenessReason]) -> tuple[CompletenessReason, ...]:
    """Reasons in the vocabulary's fixed order: stable output, whatever found them first."""
    return tuple(sorted(set(reasons), key=_ORDER.__getitem__))


class PathOutcome(StrEnum):
    """What a path query's answer is. Branch on this, never on an empty list."""

    FOUND = "found"
    """At least one path exists in the graph."""

    NO_PATH = "no_path"
    """No path exists in the modeled graph: the search finished within its limits over a
    current graph, met no boundary and declined nothing. Still a statement about what ADG
    models — never about relationship types it does not collect (``coverage``)."""

    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    """Nothing found, and the graph does not reflect everything collected (or nothing was)."""

    TRUNCATED = "truncated"
    """Nothing found before a limit stopped the search."""

    BOUNDARY = "boundary"
    """Nothing found, and the search met something the graph cannot see past."""

    POLICY_RESTRICTED = "policy_restricted"
    """Nothing found through the relationships the policy follows; others exist."""


_PRECEDENCE: Final = (
    (ReasonCategory.EVIDENCE, PathOutcome.INCOMPLETE_EVIDENCE),
    (ReasonCategory.TRUNCATION, PathOutcome.TRUNCATED),
    (ReasonCategory.BOUNDARY, PathOutcome.BOUNDARY),
    (ReasonCategory.POLICY, PathOutcome.POLICY_RESTRICTED),
)


def classify(found: bool, reasons: Iterable[CompletenessReason]) -> PathOutcome:
    """The one place an outcome is decided.

    A found path is found whatever else is true: it is a real chain of current relationships.
    An empty answer takes the most fundamental reason it is incomplete — an incomplete graph
    before a cut-short search, before a boundary, before a declined relationship — and is
    ``no_path`` only when there is none.
    """
    if found:
        return PathOutcome.FOUND
    categories = {REASON_CATEGORY[reason] for reason in reasons}
    for category, outcome in _PRECEDENCE:
        if category in categories:
            return outcome
    return PathOutcome.NO_PATH


def conclusive(outcome: PathOutcome, reasons: Iterable[CompletenessReason]) -> bool:
    """Whether the answer supports its own conclusion.

    ``no_path`` always does, by construction. ``found`` does unless the graph is behind what
    was collected, in which case the path may rest on a relationship newer facts no longer
    establish. Every other outcome supports no negative conclusion at all.
    """
    if outcome is PathOutcome.NO_PATH:
        return True
    if outcome is PathOutcome.FOUND:
        return not any(REASON_CATEGORY[reason] is ReasonCategory.EVIDENCE for reason in reasons)
    return False


@dataclass(frozen=True, slots=True)
class GraphReadiness:
    """Whether the relationship graph reflects what was collected, from the pass log.

    Built from ``RelationshipProjector.state()`` — the projector's own record — rather than a
    second readiness table, and from the collection basis, so an answer can say "as of" both
    the facts and their derivation.
    """

    nothing_collected: bool
    passes: int
    latest_pass: Mapping[str, Any] | None
    underived_runs: tuple[UUID, ...]
    failed_runs: tuple[UUID, ...]
    runs_in_progress: tuple[UUID, ...]

    @property
    def reasons(self) -> tuple[CompletenessReason, ...]:
        found: list[CompletenessReason] = []
        if self.nothing_collected:
            found.append(CompletenessReason.NOTHING_COLLECTED)
        if self.failed_runs:
            found.append(CompletenessReason.PROJECTION_FAILED)
        if set(self.underived_runs) - set(self.failed_runs):
            found.append(CompletenessReason.PROJECTION_BEHIND)
        if self.runs_in_progress:
            found.append(CompletenessReason.COLLECTION_IN_PROGRESS)
        return ordered(found)

    @property
    def state(self) -> str:
        """One word for the projection: ``empty``, ``failed``, ``behind`` or ``current``."""
        if self.nothing_collected:
            return "empty"
        if self.failed_runs:
            return "failed"
        if self.underived_runs:
            return "behind"
        return "current"

    @property
    def token(self) -> str:
        """The projection's identity: it moves whenever a pass is recorded.

        The collection basis does not move when an operator rebuilds the graph, so a validator
        built from it alone would serve a pre-rebuild answer as fresh. The pass log only grows,
        so its count and its latest pass name the derived state exactly.
        """
        latest = self.latest_pass or {}
        completed = latest.get("completed_at")
        material = "\x1e".join(
            (
                str(self.passes),
                str(latest.get("pass_id", "")),
                completed.isoformat() if isinstance(completed, dt.datetime) else "",
                str(latest.get("status", "")),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
