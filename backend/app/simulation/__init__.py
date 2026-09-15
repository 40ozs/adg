r"""Non-destructive what-if simulation (Phase 9A).

*"If I take this group off the ACL, who loses access?"* is the question an administrator asks
before every permission change, and the reason it is hard is that the honest answer is almost
never the obvious one: an alternate group membership keeps the access, a Deny earlier in the
DACL means the entry was never granting anything, a share ACL in front of it was the real
limit all along. Guessing is how a change gets signed off that achieves nothing — or takes
away more than anybody intended.

Seven modules, in the order a proposal moves through them:

* :mod:`app.simulation.overlay` — the proposal, as an immutable value. Pure.
* :mod:`app.simulation.application` — applying it to stored rows, as a pure function over
  frozen records. Nothing is mutated and every invented row is marked.
* :mod:`app.simulation.repositories` — the two repositories the access engine takes by
  injection, reading through the overlay. This is the whole mechanism.
* :mod:`app.simulation.model` — the baseline, the scope, the bounds, and the shape of a
  report. Pure.
* :mod:`app.simulation.impact` — comparing two answers and qualifying the comparison. Pure.
* :mod:`app.simulation.service` — deriving the pairs, resolving both worlds, bounding the work.
* :mod:`app.simulation.store` — the two tables a proposal is kept in, and no others.

Four properties this package exists to guarantee.

**Nothing is changed.** Not Active Directory, not a share, not an NTFS descriptor, and not
ADG's own collected state. An overlay is data; applying it produces new records in memory; the
only rows written anywhere are in ``simulations`` and ``simulation_evaluations``, which no
collector, no ingestion path and no access query reads.

**The answer comes from the production engine.** :class:`app.services.AccessService` is used
twice — over the real repositories and over the overlay repositories that wrap them — with not
one line changed and no parameter telling it which world it is in. There is no simplified
simulation arithmetic, because a second implementation of the access check would eventually
disagree with the first, and on that day nobody would know which was right. This is the same
seam Phase 7A used to answer about a past instant, applied to a hypothetical one.

**A claim of loss is never stronger than its evidence.** Every delta carries the certainty of
both answers it compares, and when a proposal removes a route the surviving routes are
enumerated by re-running the access check rather than by reasoning about which route looked
important. A membership somebody was about to delete, with an alternate path around it, comes
back reported as changing nothing.

**Every result names the state it was computed against.** The collection-basis token moves if
and only if a collector has written something, so a report says exactly which facts it rests on
and a stored proposal can be told, as a fact rather than a guess, that those facts have moved.

See ``docs/architecture/simulation.md`` for the model, and ADR-0020 for why the overlay is
applied at the repository boundary instead of anywhere else.
"""

from __future__ import annotations

from app.simulation.application import (
    SIMULATED_AT,
    SIMULATED_RUN_ID,
    is_simulated_record,
    overlay_edges,
    overlay_ntfs_acl,
    overlay_resource,
    overlay_share_acl,
)
from app.simulation.errors import SimulationBoundsError, SimulationUnsupportedRead
from app.simulation.impact import classify, compare_access, surviving_paths
from app.simulation.model import (
    CAVEAT_DESCRIPTIONS,
    DEFAULT_BOUNDS,
    MAX_EXPLANATIONS_CEILING,
    MAX_PAIRS_CEILING,
    MAX_PRINCIPALS_CEILING,
    MAX_RESOURCES_CEILING,
    MAX_TIME_BUDGET_MS,
    OUTCOME_DESCRIPTIONS,
    TRUNCATION_DESCRIPTIONS,
    AccessDelta,
    BaselineKind,
    ChangeApplication,
    ChangeOutcome,
    ImpactDirection,
    ImpactSummary,
    ScopeKind,
    SimulationBaseline,
    SimulationBounds,
    SimulationCaveat,
    SimulationCost,
    SimulationReport,
    SimulationScope,
    SimulationTruncation,
    describe_caveat,
    summarize,
)
from app.simulation.overlay import (
    MAX_CHANGES,
    OVERLAY_DOCUMENT_VERSION,
    OVERLAY_HASH_LENGTH,
    SIMULATED_ACE_PREFIX,
    SIMULATED_EDGE_PREFIX,
    SIMULATED_SOURCE_KEY,
    ChangeKind,
    InheritanceChange,
    InheritedAceDisposition,
    MembershipChange,
    NtfsAceChange,
    ShareAceChange,
    SimulationChange,
    SimulationOverlay,
    is_simulated_ace_key,
    is_simulated_edge_key,
)
from app.simulation.repositories import (
    OverlayMembershipRepository,
    OverlayResourceRepository,
    simulated_repositories,
)
from app.simulation.service import SimulationService
from app.simulation.store import SimulationStore, StoredEvaluation, StoredSimulation

__all__ = [
    "CAVEAT_DESCRIPTIONS",
    "DEFAULT_BOUNDS",
    "MAX_CHANGES",
    "MAX_EXPLANATIONS_CEILING",
    "MAX_PAIRS_CEILING",
    "MAX_PRINCIPALS_CEILING",
    "MAX_RESOURCES_CEILING",
    "MAX_TIME_BUDGET_MS",
    "OUTCOME_DESCRIPTIONS",
    "OVERLAY_DOCUMENT_VERSION",
    "OVERLAY_HASH_LENGTH",
    "SIMULATED_ACE_PREFIX",
    "SIMULATED_AT",
    "SIMULATED_EDGE_PREFIX",
    "SIMULATED_RUN_ID",
    "SIMULATED_SOURCE_KEY",
    "TRUNCATION_DESCRIPTIONS",
    "AccessDelta",
    "BaselineKind",
    "ChangeApplication",
    "ChangeKind",
    "ChangeOutcome",
    "ImpactDirection",
    "ImpactSummary",
    "InheritanceChange",
    "InheritedAceDisposition",
    "MembershipChange",
    "NtfsAceChange",
    "OverlayMembershipRepository",
    "OverlayResourceRepository",
    "ScopeKind",
    "ShareAceChange",
    "SimulationBaseline",
    "SimulationBounds",
    "SimulationBoundsError",
    "SimulationCaveat",
    "SimulationChange",
    "SimulationCost",
    "SimulationOverlay",
    "SimulationReport",
    "SimulationScope",
    "SimulationService",
    "SimulationStore",
    "SimulationTruncation",
    "SimulationUnsupportedRead",
    "StoredEvaluation",
    "StoredSimulation",
    "classify",
    "compare_access",
    "describe_caveat",
    "is_simulated_ace_key",
    "is_simulated_edge_key",
    "is_simulated_record",
    "overlay_edges",
    "overlay_ntfs_acl",
    "overlay_resource",
    "overlay_share_acl",
    "simulated_repositories",
    "summarize",
    "surviving_paths",
]
