"""Canonical ADG test vectors.

Each scenario under `scenarios/` is a complete, replayable scan-run transcript: a start
envelope, one or more observation batches, and a completion envelope, exactly as a collector
would send them. Every later phase — ingestion, the rights model, the effective-access
engine, the explanation API, the UI — is expected to use these rather than inventing its
own data, so that one shared set of facts pins behavior across the whole stack.

Each file also carries an ``expectations`` block describing what the scenario is *for*
(which principal, which resource, which layer limits access, how many paths exist). Those
are **expectations to be verified by later phases**, not results computed here: Phase 0B
neither implements nor asserts effective access.

The JSON files are the artifact. They are validated against the published JSON Schemas and
parsed by the contract models in `tests/contracts/test_fixtures.py`, and they are equally
usable from PowerShell (`Get-Content ... | ConvertFrom-Json`) by collector authors.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from functools import cache
from typing import Any

from app.contracts.v1.envelopes import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.contracts.v1.observations import (
    MembershipObservation,
    NtfsAceObservation,
    NtfsResourceObservation,
    PrincipalObservation,
    ServerObservation,
    SmbAceObservation,
    SmbShareObservation,
)

SCENARIO_DIR = pathlib.Path(__file__).parent / "scenarios"


@dataclass(frozen=True)
class Scenario:
    """One parsed scan-run transcript."""

    name: str
    title: str
    description: str
    expectations: dict[str, Any]
    start: ScanRunStart
    batches: list[ObservationBatch]
    completion: ScanRunCompletion
    raw: dict[str, Any]

    @property
    def observations(self) -> list[Any]:
        return [item for batch in self.batches for item in batch.observations]

    def of_kind(self, kind: str) -> list[Any]:
        return [item for item in self.observations if item.kind == kind]

    @property
    def principals(self) -> list[PrincipalObservation]:
        return [item for item in self.observations if isinstance(item, PrincipalObservation)]

    @property
    def edges(self) -> list[MembershipObservation]:
        return [item for item in self.observations if isinstance(item, MembershipObservation)]

    @property
    def servers(self) -> list[ServerObservation]:
        return [item for item in self.observations if isinstance(item, ServerObservation)]

    @property
    def shares(self) -> list[SmbShareObservation]:
        return [item for item in self.observations if isinstance(item, SmbShareObservation)]

    @property
    def share_aces(self) -> list[SmbAceObservation]:
        return [item for item in self.observations if isinstance(item, SmbAceObservation)]

    @property
    def resources(self) -> list[NtfsResourceObservation]:
        return [item for item in self.observations if isinstance(item, NtfsResourceObservation)]

    @property
    def ntfs_aces(self) -> list[NtfsAceObservation]:
        return [item for item in self.observations if isinstance(item, NtfsAceObservation)]

    @property
    def source_keys(self) -> list[str]:
        return [item.source_key for item in self.observations]


def scenario_paths() -> list[pathlib.Path]:
    return sorted(SCENARIO_DIR.glob("*.json"))


def scenario_names() -> list[str]:
    return [path.stem for path in scenario_paths()]


def load_raw(name: str) -> dict[str, Any]:
    """Return a scenario's untouched JSON, for schema validation."""
    path = SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(scenario_names())
        raise FileNotFoundError(f"No scenario named {name!r}. Available: {available}")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


@cache
def load_scenario(name: str) -> Scenario:
    """Parse a scenario through the contract models."""
    document = load_raw(name)
    return Scenario(
        name=document["scenario"],
        title=document["title"],
        description=document["description"],
        expectations=document["expectations"],
        start=ScanRunStart.model_validate(document["start"]),
        batches=[ObservationBatch.model_validate(batch) for batch in document["batches"]],
        completion=ScanRunCompletion.model_validate(document["completion"]),
        raw=document,
    )


def load_all() -> list[Scenario]:
    return [load_scenario(name) for name in scenario_names()]
