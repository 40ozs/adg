r"""Comparing two effective-access answers, and qualifying the comparison.

Pure, and deliberately small. Everything expensive already happened: the two answers were
each computed by :func:`app.access_engine.resolve_access` over real and overlaid rows, and
what is left is to say how they differ and how far that difference can be trusted.

**The difference is taken over expanded masks.** A DACL can carry generic rights
(``GENERIC_READ`` and friends), which Windows maps to specific bits at open time. Two answers
that hold the same access can therefore carry different mask values — one from an ACE written
generically, one from an ACE written specifically — and subtracting them raw would report a
change that does not exist. :meth:`app.access_engine.RightsMask.expand_generics` is applied to
both sides first, which is the same normalization the resolver applies before crossing layers.

**A claim inherits the certainty of the answer it rests on.** The access engine reports, for
each answer, the *direction* it could be wrong in: ``AT_MOST`` means the real rights are no
wider than reported, ``AT_LEAST`` that they are no narrower. Those map onto the two ways a
simulation can mislead:

* a claim of **loss** is undermined by an ``AT_LEAST`` simulated answer — the principal could
  still hold what the change was supposed to take away;
* a claim of **gain** is undermined by an ``AT_MOST`` simulated answer — the grant could be
  withheld by something nobody has read.

``UNCERTAIN`` undermines both, and neither is treated as a reason to withhold the delta: the
delta is reported with the caveat attached, because "we cannot be sure this removal works" is
information and silence is not.

**Alternate paths are measured, not inferred.** When a proposal removes a route, whether any
other route survives is answered by looking at the *simulated* explanation — the paths the
access check found after the change — rather than by reasoning about which of the paths in the
before picture looked important. Reasoning is where this goes wrong: removing an Allow can
reveal a redundant Allow behind it, and removing a membership can remove a Deny as well as a
grant.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.access_engine import (
    AccessCertainty,
    AccessExplanation,
    AccessPath,
    CausalPath,
    EffectiveAccess,
    PathRelation,
    RightsMask,
)
from app.repositories.resources import NtfsResourceRecord
from app.simulation.model import (
    AccessDelta,
    ImpactDirection,
    SimulationCaveat,
)

__all__ = [
    "GAIN_UNDERMINING_CERTAINTIES",
    "LOSS_UNDERMINING_CERTAINTIES",
    "classify",
    "compare_access",
    "surviving_paths",
]

LOSS_UNDERMINING_CERTAINTIES = frozenset({AccessCertainty.AT_LEAST, AccessCertainty.UNCERTAIN})
"""Certainties under which "these rights are gone" may be false.

``AT_LEAST`` says the reported rights are a lower bound: the truth could be wider, which is
precisely the case where a right reported as removed is in fact retained.
"""

GAIN_UNDERMINING_CERTAINTIES = frozenset({AccessCertainty.AT_MOST, AccessCertainty.UNCERTAIN})
"""Certainties under which "these rights are granted" may be false."""


def classify(before: RightsMask, after: RightsMask) -> ImpactDirection:
    """How one principal's rights moved between two answers.

    Both masks are assumed already expanded; :func:`compare_access` does that. The order of
    the tests matters: *held nothing* and *holds nothing* are checked before the subset tests,
    so that appearing from nowhere is reported as gaining access rather than as an expansion
    from zero, which is the distinction an impact list is read for.
    """
    if before.value == after.value:
        return ImpactDirection.UNCHANGED
    if before.is_empty:
        return ImpactDirection.GAINED_ACCESS
    if after.is_empty:
        return ImpactDirection.LOST_ACCESS
    if before.issubset(after):
        return ImpactDirection.EXPANDED
    if after.issubset(before):
        return ImpactDirection.REDUCED
    return ImpactDirection.CHANGED


def compare_access(
    subject_key: str,
    before: EffectiveAccess,
    after: EffectiveAccess,
    *,
    explanation: AccessExplanation | None = None,
    resource: NtfsResourceRecord | None = None,
) -> AccessDelta:
    """One before/after pair, classified and qualified.

    Args:
        subject_key: the principal both answers are about.
        before: the answer over collected state.
        after: the answer over the same state with the overlay applied.
        explanation: the *simulated* explanation, when alternate-path analysis was run for
            this pair. Its surviving grant paths become
            :attr:`AccessDelta.retained_paths`, and their presence alongside a removal is
            what raises :attr:`SimulationCaveat.ALTERNATE_PATH_RETAINS_ACCESS`.
        resource: the directory row, carried so a renderer does not have to look it up again.

    Raises:
        ValueError: if the two answers are not about the same principal, resource and access
            path. A delta between two different questions is not a delta, and computing one
            silently is the sort of mistake that produces a plausible number.
    """
    if before.resource_key != after.resource_key or before.path is not after.path:
        raise ValueError(
            "A delta compares two answers to the same question. "
            f"{before.resource_key}/{before.path.value} and "
            f"{after.resource_key}/{after.path.value} are two different ones."
        )

    before_rights = before.rights.expand_generics()
    after_rights = after.rights.expand_generics()
    direction = classify(before_rights, after_rights)
    added = after_rights.difference(before_rights)
    removed = before_rights.difference(after_rights)

    retained = surviving_paths(explanation)
    caveats: list[SimulationCaveat] = []
    if before.certainty is not AccessCertainty.CERTAIN:
        caveats.append(SimulationCaveat.BASELINE_UNCERTAIN)
    if after.certainty is not AccessCertainty.CERTAIN:
        caveats.append(SimulationCaveat.SIMULATED_UNCERTAIN)
    if not removed.is_empty and after.certainty in LOSS_UNDERMINING_CERTAINTIES:
        caveats.append(SimulationCaveat.LOSS_MAY_NOT_HOLD)
    if not added.is_empty and after.certainty in GAIN_UNDERMINING_CERTAINTIES:
        caveats.append(SimulationCaveat.GAIN_MAY_NOT_HOLD)
    if retained:
        caveats.append(SimulationCaveat.ALTERNATE_PATH_RETAINS_ACCESS)

    return AccessDelta(
        subject_key=subject_key,
        resource_key=after.resource_key,
        share_key=after.share_key,
        path=after.path,
        before=before,
        after=after,
        direction=direction,
        rights_added=added,
        rights_removed=removed,
        caveats=tuple(caveats),
        retained_paths=retained,
        resource=resource,
    )


def surviving_paths(explanation: AccessExplanation | None) -> tuple[CausalPath, ...]:
    """The routes that still deliver rights after the overlay was applied.

    Only paths that actually contribute: an Allow whose rights an earlier entry had already
    settled, or which the other layer withholds entirely, is on the ACL and delivers nothing,
    and listing it as a surviving route would be exactly the false reassurance this analysis
    exists to prevent. :attr:`app.access_engine.CausalPath.matters` is the engine's own test
    for that, and it is used rather than restated.
    """
    if explanation is None:
        return ()
    return tuple(
        path for path in explanation.paths if path.relation is PathRelation.GRANT and path.matters
    )


def unchanged_pair(
    subject_key: str,
    resource_key: str,
    share_key: str | None,
    path: AccessPath,
    answer: EffectiveAccess,
) -> AccessDelta:
    """A delta for a pair the overlay provably could not affect.

    Used where a scope enumerates a pair that no applied change touches: rather than running
    the access check twice over identical inputs, the one answer stands for both sides. The
    certainty caveats still apply, because an unchanged verdict over uncertain inputs is not
    proof that nothing changed.
    """
    caveats: list[SimulationCaveat] = []
    if answer.certainty is not AccessCertainty.CERTAIN:
        caveats.extend((SimulationCaveat.BASELINE_UNCERTAIN, SimulationCaveat.SIMULATED_UNCERTAIN))
    empty = RightsMask.effective(0)
    return AccessDelta(
        subject_key=subject_key,
        resource_key=resource_key,
        share_key=share_key,
        path=path,
        before=answer,
        after=answer,
        direction=ImpactDirection.UNCHANGED,
        rights_added=empty,
        rights_removed=empty,
        caveats=tuple(caveats),
    )


def deltas_by_severity(deltas: Sequence[AccessDelta]) -> tuple[AccessDelta, ...]:
    """Deltas ordered so the consequential ones are first, then by key.

    Gains before losses before everything else: a principal who can suddenly reach a
    directory is the finding a reviewer must not scroll past, and an unchanged row is the one
    they can. Ties break on the keys, so the order is total and two runs over unchanged data
    produce the same list.
    """
    rank = {
        ImpactDirection.GAINED_ACCESS: 0,
        ImpactDirection.EXPANDED: 1,
        ImpactDirection.LOST_ACCESS: 2,
        ImpactDirection.REDUCED: 3,
        ImpactDirection.CHANGED: 4,
        ImpactDirection.UNCHANGED: 5,
    }
    return tuple(
        sorted(
            deltas,
            key=lambda delta: (rank[delta.direction], delta.resource_key, delta.subject_key),
        )
    )
