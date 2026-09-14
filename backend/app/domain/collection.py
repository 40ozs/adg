"""Judging collection coverage: is an empty page empty, or is it unknown?

This is the single most important distinction the ADG user interface draws. A directory
listing with nothing in it means one of two completely different things:

* nothing is there, or
* nothing was collected, because a scan failed, is still running, or never ran.

An auditor who reads the second as the first concludes a share has no risky permissions,
when in fact nobody looked. So the judgement is made here — a pure function over run
records, with no database and no request — and every view that can render an empty state
is handed its verdict rather than inferring one.

The rule is deliberately pessimistic: any doubt downgrades the verdict. A collector whose
latest run failed makes the whole picture ``failed`` even if three others succeeded,
because the estate the failed one covers is exactly the part nobody can see.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.observation import ScanStatus

__all__ = [
    "CollectionHealth",
    "CollectionStatus",
    "CollectorCoverage",
    "assess_collection",
]


class CollectionHealth(StrEnum):
    """How much of the picture can be trusted."""

    NO_DATA = "no_data"
    """No collector has ever completed a run. Every list is empty because nothing ran."""

    HEALTHY = "healthy"
    """Every collector's most recent run succeeded with no reported errors."""

    INCOMPLETE = "incomplete"
    """Something is partial, still running, or reported errors. Absence proves nothing."""

    FAILED = "failed"
    """A collector's most recent run failed outright. Part of the estate is unobserved."""


@dataclass(frozen=True, slots=True)
class CollectorCoverage:
    """The latest run of one collector, reduced to what a coverage banner needs."""

    collector: str
    status: ScanStatus
    started_at: dt.datetime
    completed_at: dt.datetime | None
    error_count: int
    target: str | None
    downgrade_reason: str | None

    @property
    def is_trustworthy(self) -> bool:
        """Whether this collector's coverage may be read as complete."""
        return self.status is ScanStatus.SUCCEEDED and self.error_count == 0

    @property
    def concern(self) -> str | None:
        """One sentence naming what is wrong, or ``None`` when nothing is."""
        if self.status is ScanStatus.FAILED:
            return f"The most recent {self.collector} run failed; its scope is unobserved."
        if self.status is ScanStatus.PARTIAL:
            reason = self.downgrade_reason or "some targets failed"
            return f"The most recent {self.collector} run is partial ({reason})."
        if self.status in (ScanStatus.RUNNING, ScanStatus.PENDING):
            return f"A {self.collector} run is still in progress; results are incomplete."
        if self.error_count:
            return (
                f"The most recent {self.collector} run succeeded but reported "
                f"{self.error_count} error(s); some objects were unreadable."
            )
        return None


@dataclass(frozen=True, slots=True)
class CollectionStatus:
    """The verdict, and everything needed to explain it in one banner."""

    health: CollectionHealth
    coverage: tuple[CollectorCoverage, ...]
    concerns: tuple[str, ...]

    @property
    def has_any_run(self) -> bool:
        return bool(self.coverage)

    @property
    def summary(self) -> str:
        """One sentence for the banner. Written for an auditor, not a developer."""
        if self.health is CollectionHealth.NO_DATA:
            return (
                "No collector has reported yet. Every view is empty because nothing has "
                "been collected, not because nothing is there."
            )
        if self.health is CollectionHealth.HEALTHY:
            collectors = ", ".join(item.collector for item in self.coverage)
            return f"Collection is current for: {collectors}."
        if self.health is CollectionHealth.FAILED:
            return (
                "A collector's most recent run failed. Part of the estate is unobserved, "
                "so an empty result here does not mean an empty result in reality."
            )
        return (
            "Collection is incomplete. Results may under-report: an object nobody could "
            "read is not an object that grants nobody access."
        )


def assess_collection(latest_runs: Iterable[CollectorCoverage]) -> CollectionStatus:
    """Fold the latest run of each collector into one verdict.

    ``latest_runs`` must already be reduced to one record per collector; choosing *which*
    run is the latest is a query, and mixing that into the judgement would make the
    judgement untestable without a database.
    """
    coverage: tuple[CollectorCoverage, ...] = tuple(
        sorted(latest_runs, key=lambda item: item.collector)
    )
    if not coverage:
        return CollectionStatus(
            health=CollectionHealth.NO_DATA,
            coverage=(),
            concerns=(),
        )

    concerns: Sequence[str] = tuple(item.concern for item in coverage if item.concern is not None)

    if any(item.status is ScanStatus.FAILED for item in coverage):
        health = CollectionHealth.FAILED
    elif all(item.is_trustworthy for item in coverage):
        health = CollectionHealth.HEALTHY
    else:
        health = CollectionHealth.INCOMPLETE

    return CollectionStatus(health=health, coverage=coverage, concerns=tuple(concerns))
