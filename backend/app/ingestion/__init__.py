"""Turning collector payloads into stored facts.

Split deliberately in two:

* :mod:`app.ingestion.plan` is pure. It converts a validated contract envelope into the
  exact rows that will be written, deriving every key through :mod:`app.domain` so that the
  stored identity and the contract's ``source_key`` can never disagree. It touches no
  database, which is what makes idempotency and key derivation testable without one.
* :mod:`app.ingestion.service` executes a plan against PostgreSQL, and owns the run
  lifecycle: start, batch, completion.

The properties the contract promises (`docs/contracts/collector-protocol.md`) are enforced
here and nowhere else:

* a replayed ``(run_id, batch_id)`` is acknowledged without being applied again;
* an observation is keyed on ``(run_id, source_key)``, so even an un-deduplicated replay
  converges;
* nothing is ever marked absent — absence arrives with reconciliation in Phase 7.
"""

from __future__ import annotations

from app.ingestion.plan import (
    SUPPORTED_KINDS,
    AclHashMismatch,
    AliasRow,
    BatchPlan,
    EdgeRow,
    NtfsAceRow,
    NtfsResourceRow,
    ObservationRow,
    PrincipalRow,
    UnsupportedObservationKind,
    plan_batch,
    source_fingerprint,
)

__all__ = [
    "SUPPORTED_KINDS",
    "AclHashMismatch",
    "AliasRow",
    "BatchPlan",
    "EdgeRow",
    "NtfsAceRow",
    "NtfsResourceRow",
    "ObservationRow",
    "PrincipalRow",
    "UnsupportedObservationKind",
    "plan_batch",
    "source_fingerprint",
]
