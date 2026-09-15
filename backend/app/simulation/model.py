r"""What a simulation is asked, what it is measured against, and what it reports.

Pure: no I/O, no session, no clock beyond the instants it is handed. The types here are the
vocabulary every other module in the package speaks, and three of them carry most of the
weight.

**The baseline is part of the answer.** A what-if is only meaningful against a stated *what
is*, and ADG already has an exact name for the state of its collected facts: the
:class:`app.domain.CollectionBasis` token, which moves if and only if a collector has written
something (ADR-0017's cache identity, reused here for a different purpose). A report carries
the basis it was computed against, so *"removing this group revokes Alice's access"* can never
be read without the sentence *"as of collection state ``a1b2…``, latest run ``…``"*. The same
field is what makes staleness answerable rather than guessable: re-read the basis, compare the
token, and a simulation computed before the last scan says so.

**A claim about loss is the dangerous one.** Telling an administrator that a proposed removal
takes access away, when an alternate path they could not see keeps it, is how a change gets
signed off that achieves nothing. Telling them access *survives* when it does not is worse
still. Both are guarded the same way: the simulated answer is computed by the real engine and
its certainty is carried onto the delta, so a claim is qualified by the direction its evidence
could be wrong in (:class:`SimulationCaveat`) rather than presented flat.

**A bound that was hit is reported, never silently applied.** Every truncation has a member of
:class:`SimulationTruncation`, and :attr:`SimulationReport.complete` is false whenever any of
them fired — so a partial impact list cannot be read as a complete one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.access_engine import (
    AccessCertainty,
    AccessPath,
    CausalPath,
    EffectiveAccess,
    RightsMask,
)
from app.domain import CollectionBasis, DomainValidationError
from app.models.schema import SimulationBaselineKind as BaselineKind
from app.repositories.resources import NtfsResourceRecord
from app.simulation.errors import SimulationBoundsError
from app.simulation.overlay import SimulationChange, SimulationOverlay

__all__ = [
    "CAVEAT_DESCRIPTIONS",
    "DEFAULT_BOUNDS",
    "MAX_EXPLANATIONS_CEILING",
    "MAX_PAIRS_CEILING",
    "MAX_PRINCIPALS_CEILING",
    "MAX_RESOURCES_CEILING",
    "MAX_TIME_BUDGET_MS",
    "OUTCOME_DESCRIPTIONS",
    "TRUNCATION_DESCRIPTIONS",
    "AccessDelta",
    "BaselineKind",
    "ChangeApplication",
    "ChangeOutcome",
    "ImpactDirection",
    "ImpactSummary",
    "ScopeKind",
    "SimulationBaseline",
    "SimulationBounds",
    "SimulationCaveat",
    "SimulationCost",
    "SimulationReport",
    "SimulationScope",
    "SimulationTruncation",
    "describe_caveat",
    "ordered_truncation",
    "summarize",
]


# --------------------------------------------------------------------------------------
# The baseline
# --------------------------------------------------------------------------------------
#
# ``BaselineKind`` is :class:`app.models.schema.SimulationBaselineKind`, imported under the
# name this layer uses. It lives in the schema module for the reason every storage-facing
# enum in this project does: its values appear in a check constraint, and the constraint is
# generated from the enum, so there can only ever be one list of them.


@dataclass(frozen=True, slots=True)
class SimulationBaseline:
    """The state a simulation was computed against, named precisely enough to check later.

    ``basis`` is a summary of every scan run and its ``token`` moves if and only if a
    collector has written something. So comparing a stored token with a freshly read one is
    an exact staleness test, not a heuristic — and it is exact in the useful direction: an
    unchanged token means no fact has changed, which means the simulation would compute the
    same answer today.
    """

    kind: BaselineKind
    basis: CollectionBasis
    captured_at: dt.datetime
    at: dt.datetime | None = None
    """The instant an ``AS_OF`` baseline reads. ``None`` for a current-state baseline."""

    def __post_init__(self) -> None:
        if (self.kind is BaselineKind.AS_OF) != (self.at is not None):
            raise DomainValidationError(
                "An as-of baseline reads one instant and a current-state baseline reads no "
                "instant at all: the kind and the timestamp must agree, or a report would "
                "name a baseline it was not computed against.",
                field="at",
            )
        if self.at is not None and (self.at.tzinfo is None or self.at.utcoffset() is None):
            raise DomainValidationError(
                "A baseline instant must be timezone-aware; a naive one cannot be ordered "
                "against observations from a host in another time zone.",
                field="at",
            )

    @property
    def token(self) -> str:
        """The collection-state digest this simulation was computed against."""
        return self.basis.token

    @property
    def run_id(self) -> str | None:
        """The most recently updated scan run at the moment of capture.

        For a human reading a report, not for comparison: two runs can interleave, and the
        one with the newest ``updated_at`` is not necessarily the one whose facts an answer
        used. :attr:`token` is what is compared.
        """
        return self.basis.latest_run_id

    @property
    def is_empty(self) -> bool:
        """Whether nothing had been collected at all.

        Worth branching on: every answer over an empty estate is "no access", and a
        simulation run against one reports "no impact" for every proposal — truthfully, and
        uselessly, unless the emptiness is stated.
        """
        return self.basis.is_empty

    def is_stale_against(self, current: CollectionBasis) -> bool:
        """Whether a collector has written anything since this baseline was captured."""
        return current.token != self.basis.token


# --------------------------------------------------------------------------------------
# The question
# --------------------------------------------------------------------------------------


class ScopeKind(StrEnum):
    """Which pairs a simulation evaluates."""

    AFFECTED = "affected"
    """The pairs the overlay could possibly have changed, derived from the overlay itself.
    The default, and the only scope that answers *"what does this change do?"* rather than
    *"what does this change do to X?"*."""

    PAIR = "pair"
    """One principal against one directory. The cheapest question and the most precise."""

    RESOURCE = "resource"
    """Every principal ADG can enumerate for one directory, before and after."""

    SUBJECT = "subject"
    """Everything one principal can reach, before and after, one keyset page at a time."""


@dataclass(frozen=True, slots=True)
class SimulationScope:
    """The question put to a simulation."""

    kind: ScopeKind = ScopeKind.AFFECTED
    subject_key: str | None = None
    resource_key: str | None = None
    path: AccessPath = AccessPath.REMOTE_SMB
    limit: int = 100
    """Page size for the scopes that page. Ignored by ``PAIR`` and ``AFFECTED``."""

    after: str | None = None
    """Keyset cursor for ``SUBJECT``."""

    def __post_init__(self) -> None:
        needs_subject = self.kind in (ScopeKind.PAIR, ScopeKind.SUBJECT)
        needs_resource = self.kind in (ScopeKind.PAIR, ScopeKind.RESOURCE)
        if needs_subject and not self.subject_key:
            raise DomainValidationError(
                f"A {self.kind.value} simulation is about one principal and must name it.",
                field="subject_key",
            )
        if needs_resource and not self.resource_key:
            raise DomainValidationError(
                f"A {self.kind.value} simulation is about one directory and must name it.",
                field="resource_key",
            )
        if self.kind is ScopeKind.AFFECTED and (self.subject_key or self.resource_key):
            raise DomainValidationError(
                "An affected-scope simulation derives its own subjects and resources from "
                "the overlay. Naming one would narrow the answer without saying so.",
                field="kind",
            )

    def document(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "subject_key": self.subject_key,
            "resource_key": self.resource_key,
            "path": self.path.value,
            "limit": self.limit,
            "after": self.after,
        }


# --------------------------------------------------------------------------------------
# The bounds
# --------------------------------------------------------------------------------------

MAX_PRINCIPALS_CEILING: Final = 500
MAX_RESOURCES_CEILING: Final = 500
MAX_PAIRS_CEILING: Final = 5_000
MAX_EXPLANATIONS_CEILING: Final = 100
MAX_TIME_BUDGET_MS: Final = 60_000


@dataclass(frozen=True, slots=True)
class SimulationBounds:
    """Strict limits on the work one simulation may do.

    Every one of them is a *product* bound rather than a performance tuning knob. A proposal
    that would change access for more principals than :attr:`max_principals` is not a
    proposal anybody reviews from a screen; it is one that needs a report, and an engine that
    tried to compute it inside a request would take the request down and produce nothing at
    all. The bounds are what let this run synchronously and honestly: the answer is complete,
    or it says which bound stopped it.

    :attr:`time_budget_ms` is checked between pairs. It is a wall-clock deadline rather than a
    count of anything, because the cost of one pair depends on a membership graph whose shape
    ADG cannot predict from the overlay — a nesting depth nobody expected is exactly the case
    a count-based bound would sail past.
    """

    max_principals: int = 50
    max_resources: int = 50
    max_pairs: int = 500
    max_explanations: int = 25
    """Simulated answers for which the surviving causal paths are enumerated. Only claims of
    loss need them (see :attr:`SimulationCaveat.ALTERNATE_PATH_RETAINS_ACCESS`), and each one
    costs a second resolution."""

    time_budget_ms: int = 10_000

    def __post_init__(self) -> None:
        for name, value, ceiling in (
            ("max_principals", self.max_principals, MAX_PRINCIPALS_CEILING),
            ("max_resources", self.max_resources, MAX_RESOURCES_CEILING),
            ("max_pairs", self.max_pairs, MAX_PAIRS_CEILING),
            ("max_explanations", self.max_explanations, MAX_EXPLANATIONS_CEILING),
            ("time_budget_ms", self.time_budget_ms, MAX_TIME_BUDGET_MS),
        ):
            if value < 1:
                raise SimulationBoundsError(f"{name} must be at least 1; got {value}.")
            if value > ceiling:
                raise SimulationBoundsError(
                    f"{name} is capped at {ceiling}; {value} was asked for. The cap is what "
                    "keeps a simulation answerable inside one request rather than becoming a "
                    "job nobody is waiting for."
                )

    def clamped(self, **overrides: int | None) -> SimulationBounds:
        """This bound set with the given fields replaced, each clamped to its ceiling.

        Clamps rather than refuses, because a caller that asks for more than the ceiling is
        asking for a complete answer and gets a bounded one that says it is bounded — the
        same contract :meth:`app.access_engine.ExplanationLimits.clamped` offers.
        """
        ceilings = {
            "max_principals": MAX_PRINCIPALS_CEILING,
            "max_resources": MAX_RESOURCES_CEILING,
            "max_pairs": MAX_PAIRS_CEILING,
            "max_explanations": MAX_EXPLANATIONS_CEILING,
            "time_budget_ms": MAX_TIME_BUDGET_MS,
        }
        applied = {
            name: max(1, min(value, ceilings[name]))
            for name, value in overrides.items()
            if value is not None
        }
        return replace(self, **applied)


DEFAULT_BOUNDS: Final = SimulationBounds()


# --------------------------------------------------------------------------------------
# What happened to each change
# --------------------------------------------------------------------------------------


class ChangeOutcome(StrEnum):
    """Whether one proposed change actually took effect against this baseline.

    The most under-appreciated half of a what-if. A proposal written last week against an ACE
    that has since been removed changes nothing, and a simulation that reported "no impact"
    without saying *why* would be read as "this change is safe" when it means "this change no
    longer exists".
    """

    APPLIED = "applied"
    """The change was applied to the baseline state."""

    ALREADY_PRESENT = "already_present"
    """An addition that is already there. Windows would merge rather than duplicate, so the
    simulated world is the baseline world and the proposal is a no-op."""

    TARGET_NOT_FOUND = "target_not_found"
    """The ACE or membership edge this change names is not in the baseline. Somebody has
    changed it since the proposal was written, or it never existed."""

    TARGET_NOT_OBSERVED = "target_not_observed"
    """The object the change acts on has no descriptor in ADG at all: a directory nobody has
    read, or a share whose ACL nobody has read. Simulating against it would mean inventing
    the state it was meant to modify."""

    PARENT_NOT_OBSERVED = "parent_not_observed"
    """Clearing inheritance protection, where the parent whose entries would flow back down
    has not been read. The change **does** take effect -- the flag is flipped -- but nothing
    is projected, so the simulated ACL is a lower bound on the real one."""

    NOT_REPRESENTABLE = "not_representable"
    """The change cannot be expressed against this baseline state at all: editing one entry of
    a NULL DACL, which holds no entries and grants everyone everything. Windows would replace
    the descriptor, which is a different proposal from the one that was written."""


OUTCOME_DESCRIPTIONS: Final[dict[ChangeOutcome, str]] = {
    ChangeOutcome.APPLIED: "Applied to the baseline state.",
    ChangeOutcome.ALREADY_PRESENT: (
        "Already present in the baseline, so the proposal changes nothing."
    ),
    ChangeOutcome.TARGET_NOT_FOUND: (
        "The entry or membership this change names is not in the baseline state."
    ),
    ChangeOutcome.TARGET_NOT_OBSERVED: (
        "ADG has not read the object this change acts on, so there is no state to modify."
    ),
    ChangeOutcome.PARENT_NOT_OBSERVED: (
        "The parent whose entries would be inherited back has not been read; nothing was "
        "projected and the simulated ACL is a lower bound."
    ),
    ChangeOutcome.NOT_REPRESENTABLE: (
        "The baseline state cannot carry this change; see the detail for which rule it breaks."
    ),
}

_APPLIED_OUTCOMES: Final[frozenset[ChangeOutcome]] = frozenset(
    {ChangeOutcome.APPLIED, ChangeOutcome.PARENT_NOT_OBSERVED}
)
"""Outcomes under which the simulated world differs from the baseline.

``PARENT_NOT_OBSERVED`` is in the set because the change *did* take effect: the protection
flag flipped, and what could not be computed is the entries that would flow back in. Treating
it as unapplied would report a simulated ACL that the report then claims was never changed.
"""


@dataclass(frozen=True, slots=True)
class ChangeApplication:
    """One proposed change and what became of it."""

    change: SimulationChange
    outcome: ChangeOutcome
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        """Whether this change altered the simulated world. See :data:`_APPLIED_OUTCOMES`."""
        return self.outcome in _APPLIED_OUTCOMES

    def document(self) -> dict[str, object]:
        return {
            "change": self.change.document(),
            "outcome": self.outcome.value,
            "detail": dict(self.detail),
        }


# --------------------------------------------------------------------------------------
# What happened to each principal
# --------------------------------------------------------------------------------------


class ImpactDirection(StrEnum):
    """How one principal's rights to one resource moved.

    Six values rather than three, because "gained rights" and "gained access" are different
    findings: a principal who already had Read and now has Modify is a permissions change, and
    a principal who had nothing and now has Read is a new person in the room. An impact list
    that merged them would bury the second in the first.
    """

    UNCHANGED = "unchanged"
    GAINED_ACCESS = "gained_access"
    """Held nothing before; holds something now."""

    LOST_ACCESS = "lost_access"
    """Held something before; holds nothing now."""

    EXPANDED = "expanded"
    """Held some rights before and strictly more now."""

    REDUCED = "reduced"
    """Held some rights before and strictly fewer now."""

    CHANGED = "changed"
    """Gained some rights and lost others. Rare, and worth its own value: it is what a
    rewritten ACE looks like, and neither "expanded" nor "reduced" describes it."""

    @property
    def is_gain(self) -> bool:
        return self in (ImpactDirection.GAINED_ACCESS, ImpactDirection.EXPANDED)

    @property
    def is_loss(self) -> bool:
        return self in (ImpactDirection.LOST_ACCESS, ImpactDirection.REDUCED)


class SimulationCaveat(StrEnum):
    """Why a delta may not mean what it appears to mean.

    Drawn from the certainty the access engine already computes. A simulation cannot be more
    certain than the answers it compares, and the two answers are computed from the same
    collected facts — so a coverage gap that made the live answer a bound makes the simulated
    one a bound too, and the *claim* inherits it.
    """

    BASELINE_UNCERTAIN = "baseline_uncertain"
    """The before answer rests on something ADG has not collected, so what the principal
    holds today is a bound rather than a number."""

    SIMULATED_UNCERTAIN = "simulated_uncertain"
    """The same, for the after answer."""

    LOSS_MAY_NOT_HOLD = "loss_may_not_hold"
    """Access is reported as removed or reduced, and the simulated rights are a **lower**
    bound — an unseen grant or an unenumerated group could leave the principal holding what
    the change was meant to take away. The most consequential caveat in the vocabulary: it is
    the one that stands between a report and a remediation that achieves nothing."""

    GAIN_MAY_NOT_HOLD = "gain_may_not_hold"
    """Access is reported as granted or widened, and the simulated rights are an **upper**
    bound — an unseen restriction could withhold it."""

    ALTERNATE_PATH_RETAINS_ACCESS = "alternate_path_retains_access"
    """The overlay removed a path to this resource and the principal still reaches it by
    another. Not a warning about uncertainty: a measured fact, produced by re-running the
    access check, and the reason a "remove this group" proposal so often changes nothing."""


CAVEAT_DESCRIPTIONS: Final[dict[SimulationCaveat, str]] = {
    SimulationCaveat.BASELINE_UNCERTAIN: (
        "What this principal holds today rests on facts ADG has not collected."
    ),
    SimulationCaveat.SIMULATED_UNCERTAIN: (
        "What this principal would hold rests on facts ADG has not collected."
    ),
    SimulationCaveat.LOSS_MAY_NOT_HOLD: (
        "Rights are reported as removed, but the simulated answer is a lower bound: an "
        "unseen grant or an unenumerated group could leave them in place."
    ),
    SimulationCaveat.GAIN_MAY_NOT_HOLD: (
        "Rights are reported as granted, but the simulated answer is an upper bound: an "
        "unseen restriction could withhold them."
    ),
    SimulationCaveat.ALTERNATE_PATH_RETAINS_ACCESS: (
        "The proposal removes one route to this resource and the principal still has another."
    ),
}


def describe_caveat(caveat: SimulationCaveat) -> str:
    """The sentence rendered beside a qualified delta."""
    return CAVEAT_DESCRIPTIONS[caveat]


@dataclass(frozen=True, slots=True)
class AccessDelta:
    """One principal against one resource, before and after, with both whole answers kept.

    The two :class:`~app.access_engine.EffectiveAccess` values are carried rather than reduced
    to masks, because the *reason* is what an operator acts on: which layer limits, which ACE
    grants, which findings qualify. A delta that stored only two numbers would be a diff of
    two conclusions with the evidence for neither.
    """

    subject_key: str
    resource_key: str
    share_key: str | None
    path: AccessPath
    before: EffectiveAccess
    after: EffectiveAccess
    direction: ImpactDirection
    rights_added: RightsMask
    rights_removed: RightsMask
    caveats: tuple[SimulationCaveat, ...] = ()
    retained_paths: tuple[CausalPath, ...] = ()
    """Routes that still deliver access after a removal, when one was looked for. Empty means
    either that none was looked for or that none survived — :attr:`alternate_path_retained`
    tells them apart."""

    resource: NtfsResourceRecord | None = None

    @property
    def changed(self) -> bool:
        return self.direction is not ImpactDirection.UNCHANGED

    @property
    def alternate_path_retained(self) -> bool:
        return SimulationCaveat.ALTERNATE_PATH_RETAINS_ACCESS in self.caveats

    @property
    def before_certainty(self) -> AccessCertainty:
        return self.before.certainty

    @property
    def after_certainty(self) -> AccessCertainty:
        return self.after.certainty

    def document(self) -> dict[str, object]:
        """The compact form persisted with a report.

        Masks and certainties, not the whole derivation. The derivation is reproducible — the
        overlay, the baseline token and the scope are all stored — and a stored copy of it
        would be a second, ageing account of an answer the engine can recompute exactly.
        """
        return {
            "subject_key": self.subject_key,
            "resource_key": self.resource_key,
            "share_key": self.share_key,
            "path": self.path.value,
            "direction": self.direction.value,
            "rights_before": f"0x{self.before.rights.value:08X}",
            "rights_after": f"0x{self.after.rights.value:08X}",
            "rights_added": f"0x{self.rights_added.value:08X}",
            "rights_removed": f"0x{self.rights_removed.value:08X}",
            "certainty_before": self.before.certainty.value,
            "certainty_after": self.after.certainty.value,
            "limiting_layer_after": self.after.limiting_layer.value,
            "caveats": [caveat.value for caveat in self.caveats],
            "retained_path_count": len(self.retained_paths),
        }


@dataclass(frozen=True, slots=True)
class ImpactSummary:
    """The counts a reader looks at first, derived from the deltas and never stored apart."""

    evaluated: int
    unchanged: int
    gained_access: int
    lost_access: int
    expanded: int
    reduced: int
    changed: int
    principals_gaining: tuple[str, ...]
    principals_losing: tuple[str, ...]
    resources_affected: tuple[str, ...]
    alternate_paths_retained: int

    @property
    def any_change(self) -> bool:
        return bool(self.evaluated - self.unchanged)

    def document(self) -> dict[str, object]:
        return {
            "evaluated": self.evaluated,
            "unchanged": self.unchanged,
            "gained_access": self.gained_access,
            "lost_access": self.lost_access,
            "expanded": self.expanded,
            "reduced": self.reduced,
            "changed": self.changed,
            "principals_gaining": list(self.principals_gaining),
            "principals_losing": list(self.principals_losing),
            "resources_affected": list(self.resources_affected),
            "alternate_paths_retained": self.alternate_paths_retained,
        }


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


class SimulationTruncation(StrEnum):
    """Why an impact list is a part of the answer rather than the whole of it."""

    PRINCIPAL_BUDGET = "principal_budget"
    RESOURCE_BUDGET = "resource_budget"
    PAIR_BUDGET = "pair_budget"
    TIME_BUDGET = "time_budget"
    EXPLANATION_BUDGET = "explanation_budget"
    """More deltas claimed a loss than alternate-path analysis was budgeted for. The deltas
    are still reported; what is missing is the list of surviving routes for some of them."""

    TRUSTEE_EXPANSION_INCOMPLETE = "trustee_expansion_incomplete"
    """A trustee whose members could not be listed — a world SID, or a group nobody has
    enumerated — so principals exist who are affected and are not in the list."""

    DESCENDANTS_NOT_EVALUATED = "descendants_not_evaluated"
    """An inheritable entry was changed, or inheritance was toggled, on a directory that has
    children. Everything under it inherits, and this phase evaluates only the directories
    named by the overlay. The impact on the subtree is real and is **not** in this report."""


TRUNCATION_DESCRIPTIONS: Final[dict[SimulationTruncation, str]] = {
    SimulationTruncation.PRINCIPAL_BUDGET: "More principals are affected than were evaluated.",
    SimulationTruncation.RESOURCE_BUDGET: "More resources are affected than were evaluated.",
    SimulationTruncation.PAIR_BUDGET: "The principal/resource budget was exhausted.",
    SimulationTruncation.TIME_BUDGET: "The time budget was exhausted before every pair ran.",
    SimulationTruncation.EXPLANATION_BUDGET: (
        "Alternate-path analysis was not run for every delta that claims a loss."
    ),
    SimulationTruncation.TRUSTEE_EXPANSION_INCOMPLETE: (
        "A trustee's membership could not be enumerated, so affected principals are missing."
    ),
    SimulationTruncation.DESCENDANTS_NOT_EVALUATED: (
        "Directories below a changed one inherit from it and were not evaluated."
    ),
}


@dataclass(frozen=True, slots=True)
class SimulationCost:
    """What one simulation actually spent. Reported so a bound can be sized from evidence."""

    pairs_evaluated: int
    resolutions: int
    """Access checks run. Two per pair, plus one more for each alternate-path analysis."""

    explanations: int
    edges_read: int
    elapsed_ms: int

    def document(self) -> dict[str, object]:
        return {
            "pairs_evaluated": self.pairs_evaluated,
            "resolutions": self.resolutions,
            "explanations": self.explanations,
            "edges_read": self.edges_read,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True, slots=True)
class SimulationReport:
    """Everything one simulation produced, including what it could not reach."""

    overlay: SimulationOverlay
    baseline: SimulationBaseline
    scope: SimulationScope
    bounds: SimulationBounds
    applications: tuple[ChangeApplication, ...]
    deltas: tuple[AccessDelta, ...]
    summary: ImpactSummary
    cost: SimulationCost
    truncation: tuple[SimulationTruncation, ...] = ()
    simulation_id: UUID | None = None
    """Set once the proposal has been persisted; ``None`` for a simulation run and discarded."""

    @property
    def complete(self) -> bool:
        """Whether every pair the scope implies was evaluated."""
        return not self.truncation

    @property
    def applied_changes(self) -> tuple[ChangeApplication, ...]:
        return tuple(item for item in self.applications if item.applied)

    @property
    def inert(self) -> bool:
        """Whether no change took effect at all.

        Distinct from "no impact": a proposal every one of whose changes was already present
        or whose targets have gone produces an empty impact list for a reason that has
        nothing to do with access, and the two must not read alike.
        """
        return not self.applied_changes

    @property
    def changed_deltas(self) -> tuple[AccessDelta, ...]:
        return tuple(delta for delta in self.deltas if delta.changed)

    def document(self) -> dict[str, object]:
        """The compact, versioned form persisted beside the proposal."""
        return {
            "scope": self.scope.document(),
            "bounds": {
                "max_principals": self.bounds.max_principals,
                "max_resources": self.bounds.max_resources,
                "max_pairs": self.bounds.max_pairs,
                "max_explanations": self.bounds.max_explanations,
                "time_budget_ms": self.bounds.time_budget_ms,
            },
            "baseline": {
                "kind": self.baseline.kind.value,
                "token": self.baseline.token,
                "run_id": self.baseline.run_id,
                "at": None if self.baseline.at is None else self.baseline.at.isoformat(),
                "captured_at": self.baseline.captured_at.isoformat(),
            },
            "applications": [item.document() for item in self.applications],
            "summary": self.summary.document(),
            "deltas": [delta.document() for delta in self.deltas],
            "cost": self.cost.document(),
            "truncation": [reason.value for reason in self.truncation],
            "complete": self.complete,
            "inert": self.inert,
        }


def summarize(deltas: Sequence[AccessDelta]) -> ImpactSummary:
    """Fold a set of deltas into the counts a report leads with.

    Sorted, deduplicated key lists rather than sets, so two runs over unchanged data produce
    identical documents and a diff between two reports means something changed.
    """
    counts: dict[ImpactDirection, int] = dict.fromkeys(ImpactDirection, 0)
    gaining: set[str] = set()
    losing: set[str] = set()
    affected: set[str] = set()
    retained = 0
    for delta in deltas:
        counts[delta.direction] += 1
        if delta.direction.is_gain:
            gaining.add(delta.subject_key)
        if delta.direction.is_loss:
            losing.add(delta.subject_key)
        if delta.changed:
            affected.add(delta.resource_key)
        if delta.alternate_path_retained:
            retained += 1
    return ImpactSummary(
        evaluated=len(deltas),
        unchanged=counts[ImpactDirection.UNCHANGED],
        gained_access=counts[ImpactDirection.GAINED_ACCESS],
        lost_access=counts[ImpactDirection.LOST_ACCESS],
        expanded=counts[ImpactDirection.EXPANDED],
        reduced=counts[ImpactDirection.REDUCED],
        changed=counts[ImpactDirection.CHANGED],
        principals_gaining=tuple(sorted(gaining)),
        principals_losing=tuple(sorted(losing)),
        resources_affected=tuple(sorted(affected)),
        alternate_paths_retained=retained,
    )


def ordered_truncation(reasons: Iterable[SimulationTruncation]) -> tuple[SimulationTruncation, ...]:
    """Truncation reasons deduplicated, in the enum's own order.

    A total order that does not depend on the sequence the bounds happened to be hit in, so a
    report is byte-stable across runs.
    """
    present = set(reasons)
    return tuple(reason for reason in SimulationTruncation if reason in present)
