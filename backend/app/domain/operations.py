"""The operator's view of collection: what ran, what it missed, and what is in the store.

:mod:`app.domain.collection` answers one question — *can an empty page be believed?* — and
answers it for everybody, on every screen. This module answers the operator's questions,
which are different and more specific:

* When did this scope last **succeed**? When did it last **fail**? Those are two facts, and
  reducing them to "the latest run" — which is what the coverage banner does — throws away
  the one an operator needs most. A scope whose latest run succeeded but which failed twice
  yesterday is not the same as one that has never failed.
* Was the run **complete**? A run can succeed and still not deliver everything it collected:
  it may have sent more batches than arrived, reported more observations than were applied,
  or declared a scope it never reconciled. Each of those is a silent shortfall.
* **How much is actually stored?** A collector that ran cleanly and wrote four rows did not
  do what it was configured to do, and nothing about its own status says so.
* **What went wrong, across everything?** Twelve `access_denied` on one share is a
  permissions problem; twelve spread over twelve servers is a service-account problem.

Everything here is pure: it folds records into a report and holds no query, no session and
no request. :mod:`app.repositories.operations` supplies the records.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.domain.collection import CollectionStatus
from app.domain.observation import ScanStatus

__all__ = [
    "Completeness",
    "ErrorGroup",
    "ObjectCounts",
    "OperationsReport",
    "RunOutcome",
    "ScopeOperations",
    "assemble_operations",
]


class Completeness(StrEnum):
    """How much of what a scope's latest run was asked to do actually landed."""

    COMPLETE = "complete"
    """Succeeded, no errors, every batch delivered, every declared scope reconciled."""

    PARTIAL = "partial"
    """It ran and something is missing: errors, an undelivered batch, or an unreconciled
    scope. What was collected is trustworthy; what is absent proves nothing."""

    NONE = "none"
    """The run failed or was canceled. Nothing about this scope can be concluded."""

    IN_PROGRESS = "in_progress"
    """Still running, or never closed. The numbers below it are a partial tally."""


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """One run, reduced to what an operator judges it by."""

    run_id: str
    collector: str
    collector_host: str
    target: str | None
    status: ScanStatus
    started_at: dt.datetime
    completed_at: dt.datetime | None
    error_count: int
    batches_reported: int | None
    batches_received: int
    observations_reported: int | None
    observations_applied: int
    declared_scopes: int
    reconciled_scopes: int
    incremental: bool = False
    downgrade_reason: str | None = None

    @property
    def batches_missing(self) -> int:
        """Batches the collector says it sent that never arrived.

        ``batches_reported`` is None for a run that never closed, and an open run has not
        claimed a total yet — so nothing is missing, it is merely not finished.
        """
        if self.batches_reported is None:
            return 0
        return max(0, self.batches_reported - self.batches_received)

    @property
    def observations_missing(self) -> int:
        """Observations claimed but not stored.

        Non-zero without ``batches_missing`` means the batches arrived and carried fewer
        rows than the completion claimed — a collector-side counting bug, and worth
        surfacing rather than rounding away.
        """
        if self.observations_reported is None:
            return 0
        return max(0, self.observations_reported - self.observations_applied)

    @property
    def scopes_unreconciled(self) -> int:
        """Scopes a full run declared and never confirmed it had fully enumerated.

        Always zero for an **incremental** run, which by definition looks at part of its
        scope and is not expected to reconcile one. Counting those would mark every
        incremental run incomplete and teach an operator to ignore the column.
        """
        if self.incremental:
            return 0
        return max(0, self.declared_scopes - self.reconciled_scopes)

    @property
    def completeness(self) -> Completeness:
        if self.status in (ScanStatus.RUNNING, ScanStatus.PENDING):
            return Completeness.IN_PROGRESS
        if self.status in (ScanStatus.FAILED, ScanStatus.CANCELED):
            return Completeness.NONE
        if (
            self.status is ScanStatus.SUCCEEDED
            and not self.error_count
            and not self.batches_missing
            and not self.observations_missing
            and not self.scopes_unreconciled
        ):
            return Completeness.COMPLETE
        return Completeness.PARTIAL

    @property
    def shortfall(self) -> str | None:
        """What is missing from this run, in one sentence, or ``None``.

        Deliberately separate from :attr:`app.domain.collection.CollectorCoverage.concern`:
        that one warns a reader that a page may under-report, this one tells an operator
        what to go and fix.
        """
        parts: list[str] = []
        if self.batches_missing:
            parts.append(f"{self.batches_missing} batch(es) never arrived")
        if self.observations_missing:
            parts.append(f"{self.observations_missing} observation(s) were claimed but not stored")
        if self.scopes_unreconciled:
            parts.append(f"{self.scopes_unreconciled} declared scope(s) were not fully enumerated")
        if self.error_count:
            parts.append(f"{self.error_count} object(s) could not be read")
        if not parts:
            return None
        return "; ".join(parts) + "."


@dataclass(frozen=True, slots=True)
class ScopeOperations:
    """One collector against one target: the latest run, and the last of each outcome."""

    collector: str
    target: str | None
    latest: RunOutcome
    last_success: RunOutcome | None
    last_failure: RunOutcome | None

    @property
    def scope_label(self) -> str:
        return self.collector if self.target is None else f"{self.collector} ({self.target})"

    @property
    def completeness(self) -> Completeness:
        return self.latest.completeness

    @property
    def has_ever_succeeded(self) -> bool:
        return self.last_success is not None

    @property
    def stale_success(self) -> bool:
        """The last success is older than the latest run: the newest attempt did not work.

        This is the state a status page most often gets wrong. Showing only the latest run
        says "failed" and hides that yesterday's data is still there; showing only the last
        success says "succeeded" and hides that it is stale. Both are shown.
        """
        if self.last_success is None:
            return False
        return self.last_success.run_id != self.latest.run_id

    @property
    def note(self) -> str | None:
        """One sentence for the operator, or ``None`` when the scope is healthy."""
        if self.latest.completeness is Completeness.NONE:
            if self.last_success is None:
                return (
                    f"{self.scope_label} has never completed a run. Nothing about this "
                    "scope has ever been collected."
                )
            return (
                f"The latest {self.scope_label} run failed. The data on screen is from "
                f"{_moment(self.last_success.completed_at or self.last_success.started_at)} "
                "and has not been refreshed since."
            )
        if self.latest.completeness is Completeness.IN_PROGRESS:
            return f"A {self.scope_label} run is still open; its totals are not final."
        if self.latest.completeness is Completeness.PARTIAL:
            return f"The latest {self.scope_label} run was incomplete: {self.latest.shortfall}"
        return None


@dataclass(frozen=True, slots=True)
class ErrorGroup:
    """Every reported failure sharing one code, folded together."""

    code: str
    count: int
    collectors: tuple[str, ...]
    latest_occurred_at: dt.datetime | None
    sample_targets: tuple[str, ...]

    @property
    def is_widespread(self) -> bool:
        """Reported by more than one collector, so it is unlikely to be one bad object."""
        return len(self.collectors) > 1


@dataclass(frozen=True, slots=True)
class ObjectCounts:
    """What the store actually holds. A clean run that wrote nothing is still a problem."""

    principals: int = 0
    membership_edges: int = 0
    servers: int = 0
    shares: int = 0
    share_aces: int = 0
    directories: int = 0
    ntfs_aces: int = 0
    scan_runs: int = 0

    @property
    def total(self) -> int:
        """Collected objects. ``scan_runs`` is excluded: a run is the act, not the estate."""
        return (
            self.principals
            + self.membership_edges
            + self.servers
            + self.shares
            + self.share_aces
            + self.directories
            + self.ntfs_aces
        )

    def as_mapping(self) -> Mapping[str, int]:
        return {
            "principals": self.principals,
            "membership_edges": self.membership_edges,
            "servers": self.servers,
            "shares": self.shares,
            "share_aces": self.share_aces,
            "directories": self.directories,
            "ntfs_aces": self.ntfs_aces,
            "scan_runs": self.scan_runs,
        }


@dataclass(frozen=True, slots=True)
class OperationsReport:
    """Everything the collector status page renders, assembled once."""

    status: CollectionStatus
    scopes: tuple[ScopeOperations, ...]
    counts: ObjectCounts
    errors: tuple[ErrorGroup, ...]

    @property
    def notes(self) -> tuple[str, ...]:
        """Every scope's note, in scope order. Empty when nothing needs attention."""
        return tuple(scope.note for scope in self.scopes if scope.note is not None)

    @property
    def never_succeeded(self) -> tuple[ScopeOperations, ...]:
        """Scopes with no successful run at all — the darkest parts of the estate."""
        return tuple(scope for scope in self.scopes if not scope.has_ever_succeeded)

    @property
    def total_errors(self) -> int:
        return sum(group.count for group in self.errors)


def _key(collector: str, target: str | None) -> tuple[str, str]:
    """The scope identity. ``target`` is coalesced because SQL groups it that way too."""
    return (collector, target or "")


def _moment(value: dt.datetime | None) -> str:
    return "an unknown time" if value is None else value.strftime("%Y-%m-%d %H:%M UTC")


def assemble_operations(
    *,
    status: CollectionStatus,
    latest: Iterable[RunOutcome],
    successes: Iterable[RunOutcome],
    failures: Iterable[RunOutcome],
    counts: ObjectCounts,
    errors: Iterable[ErrorGroup],
) -> OperationsReport:
    """Pair each scope's latest run with the last success and the last failure of that scope.

    ``successes`` and ``failures`` are each already reduced to one row per scope by the
    query; a scope absent from either simply has none. A success or failure naming a scope
    that has no latest run cannot happen — the latest run of a scope that has a successful
    run is at worst that run — and is dropped rather than invented, because a report with a
    scope in it that no run produced would be a report of something that did not happen.
    """
    by_scope = {_key(run.collector, run.target): run for run in latest}
    success_by_scope = {_key(run.collector, run.target): run for run in successes}
    failure_by_scope = {_key(run.collector, run.target): run for run in failures}

    scopes = tuple(
        ScopeOperations(
            collector=run.collector,
            target=run.target,
            latest=run,
            last_success=success_by_scope.get(key),
            last_failure=failure_by_scope.get(key),
        )
        for key, run in sorted(by_scope.items())
    )
    return OperationsReport(
        status=status,
        scopes=scopes,
        counts=counts,
        errors=tuple(sorted(errors, key=lambda group: (-group.count, group.code))),
    )
