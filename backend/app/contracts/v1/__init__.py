"""ADG collector contract, version 1.

Backend mirror of the published JSON Schemas in `docs/contracts/v1/`. The schemas are what a
PowerShell collector author reads; these models are what the API validates against, and
`backend/tests/contracts/test_schema_model_parity.py` keeps the two from drifting.

Contract rules that later phases inherit:

* Ingestion is idempotent on ``(run_id, source_key)``; batches are idempotent on
  ``(run_id, batch_id)``.
* Absence is never inferred from a missing observation — only from a reconciled scope on a
  successful, non-incremental run.
* Observations are raw readings. No contract carries effective access, expanded membership,
  or any other derived conclusion (ADR-0003).
"""

from __future__ import annotations

from app.contracts.v1 import keys
from app.contracts.v1.common import (
    MAX_ACCESS_MASK,
    MAX_ACE_FLAGS,
    MAX_BATCH_OBSERVATIONS,
    SCHEMA_VERSION,
    ContractModel,
    ObservationBase,
    ObservationKind,
    Scope,
    ScopeKind,
    SourceDescriptor,
)
from app.contracts.v1.envelopes import (
    CollectorError,
    ObservationBatch,
    ScanRunCompletion,
    ScanRunStart,
)
from app.contracts.v1.observations import (
    OBSERVATION_MODELS,
    AnyObservation,
    MembershipObservation,
    NtfsAceObservation,
    NtfsResourceObservation,
    PrincipalObservation,
    ServerObservation,
    SmbAceObservation,
    SmbShareObservation,
)

__all__ = [
    "MAX_ACCESS_MASK",
    "MAX_ACE_FLAGS",
    "MAX_BATCH_OBSERVATIONS",
    "OBSERVATION_MODELS",
    "SCHEMA_VERSION",
    "AnyObservation",
    "CollectorError",
    "ContractModel",
    "MembershipObservation",
    "NtfsAceObservation",
    "NtfsResourceObservation",
    "ObservationBase",
    "ObservationBatch",
    "ObservationKind",
    "PrincipalObservation",
    "ScanRunCompletion",
    "ScanRunStart",
    "Scope",
    "ScopeKind",
    "ServerObservation",
    "SmbAceObservation",
    "SmbShareObservation",
    "SourceDescriptor",
    "keys",
]
