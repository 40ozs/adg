"""Observations and scan runs: where a fact came from and when it was true.

Every stored fact is an *observation*: something a named collector saw, on a named host, at
a known instant. That provenance is what lets ADG answer "why do you believe this?" and
"what changed since Tuesday?", and it is what keeps raw facts distinguishable from derived
conclusions.

Two invariants are enforced here because violating either corrupts history silently:

* **Timestamps are timezone-aware, in UTC.** A naive timestamp from a server in another
  time zone would reorder history.
* **A run that has finished has an end.** A terminal status without ``completed_at`` makes
  "what was true at time T" unanswerable.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar
from uuid import UUID, uuid4

from app.domain.errors import DomainValidationError


class CollectorKind(StrEnum):
    """Which collector produced an observation."""

    ACTIVE_DIRECTORY = "active_directory"
    SMB = "smb"
    NTFS = "ntfs"
    LOCAL_GROUPS = "local_groups"


class ScanStatus(StrEnum):
    """Lifecycle of a scan run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    """Completed, but some targets failed.

    Its observations are usable; its coverage is not complete.
    """

    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ScanStatus.SUCCEEDED,
            ScanStatus.PARTIAL,
            ScanStatus.FAILED,
            ScanStatus.CANCELED,
        )

    @property
    def yields_usable_observations(self) -> bool:
        return self in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL)


def _require_utc(value: dt.datetime, field_name: str) -> dt.datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise DomainValidationError(
            f"{field_name} must be timezone-aware. A naive timestamp cannot be ordered "
            "against observations from a host in another time zone.",
            field=field_name,
        )
    return value.astimezone(dt.UTC)


@dataclass(frozen=True, slots=True)
class ObservationSource:
    """Who saw a fact, and how.

    ``method`` names the concrete mechanism (for example ``Get-SmbShareAccess`` or
    ``System.IO.DirectoryInfo.GetAccessControl``). Different mechanisms report different
    detail — share permissions as levels versus masks, for instance — so the method is part
    of the evidence, not a cosmetic label.
    """

    collector: CollectorKind
    collector_host: str
    method: str
    collector_version: str | None = None
    target: str | None = None
    """What was being read: a domain, a server, a share, or a path."""

    def __post_init__(self) -> None:
        for name in ("collector_host", "method"):
            value: str = getattr(self, name)
            if not value or not value.strip():
                raise DomainValidationError(
                    f"An observation source must record {name}.", field=name
                )


@dataclass(frozen=True, slots=True)
class ScanRun:
    """One execution of one collector against one scope.

    Every observation belongs to a scan run, which is what makes history and change
    detection possible: a fact absent from a later successful run of the same scope is a
    removal, while a fact absent from a failed run is simply unknown.
    """

    source: ObservationSource
    started_at: dt.datetime
    run_id: UUID = field(default_factory=uuid4)
    status: ScanStatus = ScanStatus.RUNNING
    completed_at: dt.datetime | None = None
    observation_count: int = 0
    error_count: int = 0
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", _require_utc(self.started_at, "started_at"))
        if self.completed_at is not None:
            object.__setattr__(
                self, "completed_at", _require_utc(self.completed_at, "completed_at")
            )
            if self.completed_at < self.started_at:
                raise DomainValidationError(
                    "A scan run cannot complete before it starts.", field="completed_at"
                )
        if self.status.is_terminal and self.completed_at is None:
            raise DomainValidationError(
                f"A {self.status.value} scan run must record completed_at; otherwise the "
                "validity window of its observations is unknown.",
                field="completed_at",
            )
        if not self.status.is_terminal and self.completed_at is not None:
            raise DomainValidationError(
                f"A {self.status.value} scan run must not record completed_at.",
                field="completed_at",
            )
        for name in ("observation_count", "error_count"):
            count: int = getattr(self, name)
            if count < 0:
                raise DomainValidationError(f"{name} must be non-negative.", field=name)
        if self.status is ScanStatus.SUCCEEDED and self.error_count:
            raise DomainValidationError(
                "A run with errors is PARTIAL or FAILED, never SUCCEEDED. Reporting "
                "complete coverage that was not achieved would understate access.",
                field="status",
            )

    @property
    def duration(self) -> dt.timedelta | None:
        if self.completed_at is None:
            return None
        return self.completed_at - self.started_at

    @property
    def identity_key(self) -> str:
        return str(self.run_id)


FactT = TypeVar("FactT")


@dataclass(frozen=True, slots=True)
class Observation(Generic[FactT]):
    """A single fact together with its provenance.

    ``Observation[NtfsAce]`` and ``Observation[MembershipEdge]`` are raw facts. Derived
    results — effective access, risk findings — are **never** wrapped in this type: that
    separation is what keeps a computed conclusion from being mistaken for something a
    collector saw. See ADR-0003.
    """

    fact: FactT
    observed_at: dt.datetime
    scan_run_id: UUID
    source: ObservationSource

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _require_utc(self.observed_at, "observed_at"))

    @classmethod
    def during(
        cls, run: ScanRun, fact: FactT, observed_at: dt.datetime | None = None
    ) -> Observation[FactT]:
        """Record ``fact`` as observed during ``run``."""
        return cls(
            fact=fact,
            observed_at=observed_at or dt.datetime.now(tz=dt.UTC),
            scan_run_id=run.run_id,
            source=run.source,
        )
