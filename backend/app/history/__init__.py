"""Scan runs, observation validity windows, and change history (Phase 7).

Seven modules, in the order a fact moves through them:

* :mod:`app.history.model` -- what a version is, and the temporal invariants. Pure.
* :mod:`app.history.closure` -- what a reconciled scope is allowed to mark absent. Pure.
* :mod:`app.history.bindings` -- how each tracked kind maps onto storage.
* :mod:`app.history.writer` -- observations to versions, reconciled scopes to tombstones.
  Called from :mod:`app.ingestion.service` and nowhere else.
* :mod:`app.history.repository` -- the point-in-time reads, including as-of forms of the
  two repositories the effective-access engine takes by injection.
* :mod:`app.history.service` -- the point-in-time query surface.
* :mod:`app.history.retention` -- what may eventually be forgotten. Deletes nothing by
  default.

See ``docs/architecture/history-model.md`` for the model and its invariants, ADR-0018 for
why current state stayed a separate projection, and ADR-0019 for why a change is recorded as
a window rather than an instant.
"""

from __future__ import annotations

from app.history.bindings import BINDINGS, KindBinding, binding_for
from app.history.closure import (
    CLOSURE_RULES,
    ClosureRule,
    closure_rules_for,
    reconcilable_kinds,
)
from app.history.model import (
    HISTORICAL_KINDS,
    PROVENANCE_FIELDS,
    REDUNDANT_FIELDS,
    Certainty,
    ChangeWindow,
    CloseReason,
    ObjectTimeline,
    ObjectVersion,
    TemporalInvariantError,
    VersionOrigin,
    canonical_state,
    digestible_state,
    state_digest,
    stored_state,
)
from app.history.repository import (
    HistoricalMembershipRepository,
    HistoricalResourceRepository,
    Presence,
    VersionAudit,
    VersionReader,
)
from app.history.retention import (
    HistoryRetentionService,
    RetentionOutcome,
    RetentionPlan,
    RetentionPolicy,
)
from app.history.service import (
    AsOfAccess,
    AsOfMembership,
    AsOfNtfsAcl,
    AsOfShareAcl,
    HistoryService,
)
from app.history.writer import ClosureOutcome, HistoryOutcome, HistoryWriter

__all__ = [
    "BINDINGS",
    "CLOSURE_RULES",
    "HISTORICAL_KINDS",
    "PROVENANCE_FIELDS",
    "REDUNDANT_FIELDS",
    "AsOfAccess",
    "AsOfMembership",
    "AsOfNtfsAcl",
    "AsOfShareAcl",
    "Certainty",
    "ChangeWindow",
    "CloseReason",
    "ClosureOutcome",
    "ClosureRule",
    "HistoricalMembershipRepository",
    "HistoricalResourceRepository",
    "HistoryOutcome",
    "HistoryRetentionService",
    "HistoryService",
    "HistoryWriter",
    "KindBinding",
    "ObjectTimeline",
    "ObjectVersion",
    "Presence",
    "RetentionOutcome",
    "RetentionPlan",
    "RetentionPolicy",
    "TemporalInvariantError",
    "VersionAudit",
    "VersionOrigin",
    "VersionReader",
    "binding_for",
    "canonical_state",
    "closure_rules_for",
    "digestible_state",
    "reconcilable_kinds",
    "state_digest",
    "stored_state",
]
