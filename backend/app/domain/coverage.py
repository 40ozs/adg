"""Collection coverage for a reviewer: where the evidence came from, and how much of it there is.

:mod:`app.domain.collection` answers *can an empty page be believed?* with one verdict, and
:mod:`app.domain.operations` answers the operator's questions about each scope's last
success and last failure. This module reads the same per-scope records for a third reader:
somebody looking at a **number** -- a risk total, an access answer -- who needs to know how
complete the evidence behind it is. It adds no second model of collection health. Every
scope here is a :class:`app.domain.operations.ScopeOperations`, and every verdict is read
off fields those records already carry.

## What a scope can be

The latest attempt and the evidence held are two different facts, and reducing them to one
word is how a stale answer passes for a fresh one:

* :class:`ScopeState` is what the **latest run** of the scope did -- ``collected`` (complete),
  ``partial``, ``unavailable`` (the target could not be reached), ``failed`` (reached and
  not read, or the run gave up), or ``running``.
* :class:`EvidenceAge` is what ADG **holds** for it -- ``current`` (from the latest run),
  ``stale`` (the latest attempt delivered nothing, so the rows on screen are from an earlier
  run), or ``none`` (no run ever delivered).

A scope whose latest run succeeded and returned nothing is ``collected`` and
:attr:`ScopeCoverage.returned_nothing`. That is the only state in which "no findings here"
means "checked and found nothing". A failed or unavailable scope is never folded into it:
its zero is "nobody looked".

## What an estate can be

ADG does not know which collectors an estate is *supposed* to run unless it is told. The
expected set (``ADG_EXPECTED_COLLECTORS``, or a demo dataset's own declaration) turns a kind
that never reported into ``not_collected`` -- expected, and missing. Without an expectation
it is ``not_configured``, and ADG says it cannot tell the two apart rather than guessing.

:attr:`CoverageReport.verdict` is ``complete`` only when every scope's latest run was
complete and no expected collector is missing. Counts are reported as counts of scopes --
"98 of 100 scopes" -- and never as a percentage: scopes are not equal in size, and a
percentage would imply they were.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.domain.observation import CollectorKind, ScanStatus
from app.domain.operations import Completeness, ScopeOperations

__all__ = [
    "DOMAIN_WIDE_COLLECTORS",
    "UNREACHABLE_ERROR_CODES",
    "CoverageReport",
    "CoverageVerdict",
    "EvidenceAge",
    "KindCoverage",
    "KindState",
    "ScopeCoverage",
    "ScopeState",
    "TotalsInterpretation",
    "TotalsReading",
    "assess_coverage",
    "interpret_totals",
    "server_of_resource",
]

UNREACHABLE_ERROR_CODES: Final = frozenset(
    {"host_unreachable", "rpc_unavailable", "timeout", "server_unreachable"}
)
"""Collector error codes that mean the target was never reached.

The collectors' own vocabulary (``collector/powershell``). A run that failed with only these
is ``unavailable``: the server was down or unreachable, which is a different operational
problem from a service account that was refused (``access_denied``), and a reviewer reads
the two differently."""

DOMAIN_WIDE_COLLECTORS: Final = frozenset({CollectorKind.ACTIVE_DIRECTORY.value})
"""Collectors whose scope is the directory rather than one server. Every access answer and
every risk finding reads identities and memberships, so these scopes are relevant to all of
them."""


class ScopeState(StrEnum):
    """What the latest run of one scope did."""

    COLLECTED = "collected"
    """Succeeded with no errors, every batch delivered and every declared scope reconciled."""

    PARTIAL = "partial"
    """It ran and delivered part: errors, a missing batch, or a scope it did not reconcile."""

    UNAVAILABLE = "unavailable"
    """It failed because the target could not be reached."""

    FAILED = "failed"
    """It failed or was canceled for any other reason."""

    RUNNING = "running"
    """Still open. Its totals are not final."""


class EvidenceAge(StrEnum):
    """What ADG holds for one scope, relative to its latest attempt."""

    CURRENT = "current"
    STALE = "stale"
    """The latest attempt delivered nothing. The rows ADG holds are from an earlier run --
    current-state rules keep a row until a complete scan proves it gone -- and anything that
    changed since is not reflected."""
    NONE = "none"


class KindState(StrEnum):
    """Whether one collector kind has reported at all."""

    REPORTED = "reported"
    NOT_COLLECTED = "not_collected"
    """Expected for this estate, and no run of it has ever reported."""
    NOT_CONFIGURED = "not_configured"
    """Not expected, and never reported. ADG cannot tell an intentional omission from a
    collector nobody set up, and does not pretend to."""


class CoverageVerdict(StrEnum):
    """How complete the collected evidence is, estate-wide or for one answer."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    """Everything was attempted and something delivered only part of what it read."""
    INCOMPLETE = "incomplete"
    """Part of the estate is unobserved or stale: a scope failed, was unavailable, or an
    expected collector never reported."""
    NONE = "none"
    """Nothing has been collected."""


@dataclass(frozen=True, slots=True)
class ScopeCoverage:
    """One scope, as a reviewer needs it."""

    collector: str
    target: str | None
    state: ScopeState
    evidence: EvidenceAge
    attempted_at: dt.datetime
    """When the latest run started."""
    evidence_collected_at: dt.datetime | None
    """When the run whose rows ADG holds finished. ``None`` when no run ever delivered."""
    observations: int
    """Observations the latest run applied."""
    failure_codes: tuple[str, ...] = ()
    shortfall: str | None = None

    @property
    def label(self) -> str:
        return self.collector if self.target is None else f"{self.collector} ({self.target})"

    @property
    def is_complete(self) -> bool:
        return self.state is ScopeState.COLLECTED

    @property
    def returned_nothing(self) -> bool:
        """Collected completely, and the run found nothing to report.

        The one state where an empty result means "checked, and nothing is there".
        """
        return self.state is ScopeState.COLLECTED and self.observations == 0

    @property
    def sentence(self) -> str:
        """One sentence a reviewer can read without knowing the run model."""
        if self.state is ScopeState.COLLECTED:
            if self.returned_nothing:
                return f"{self.label}: collected completely; the scan found nothing to report."
            return f"{self.label}: collected completely."
        held = (
            f" ADG still holds what the run of {_day(self.evidence_collected_at)} collected; "
            "anything that changed since is not reflected."
            if self.evidence is EvidenceAge.STALE
            else " Nothing for this scope has ever been collected."
            if self.evidence is EvidenceAge.NONE
            else ""
        )
        if self.state is ScopeState.UNAVAILABLE:
            return f"{self.label}: the latest scan could not reach its target.{held}"
        if self.state is ScopeState.FAILED:
            reason = f" ({', '.join(self.failure_codes)})" if self.failure_codes else ""
            return f"{self.label}: the latest scan failed{reason}.{held}"
        if self.state is ScopeState.RUNNING:
            return f"{self.label}: a scan is still running; its results are not final.{held}"
        detail = f" {self.shortfall}" if self.shortfall else ""
        return f"{self.label}: the latest scan was partial.{detail}"


@dataclass(frozen=True, slots=True)
class KindCoverage:
    """One collector kind across every scope it runs against."""

    collector: str
    expected: bool
    state: KindState
    scopes: int
    collected: int

    @property
    def sentence(self) -> str | None:
        if self.state is KindState.NOT_COLLECTED:
            return (
                f"No {self.collector} collector has ever reported, although this estate "
                f"expects one: {_WHAT_IT_COVERS.get(self.collector, 'its facts')} "
                "are unobserved."
            )
        return None


_WHAT_IT_COVERS: Final = {
    CollectorKind.ACTIVE_DIRECTORY.value: "identities and group memberships",
    CollectorKind.SMB.value: "shares and their permission lists",
    CollectorKind.NTFS.value: "folders and their permission lists",
    CollectorKind.LOCAL_GROUPS.value: "file servers' local group memberships",
}


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Every scope's coverage, and the verdict over them."""

    scopes: tuple[ScopeCoverage, ...]
    kinds: tuple[KindCoverage, ...]

    def count(self, state: ScopeState) -> int:
        return sum(1 for scope in self.scopes if scope.state is state)

    @property
    def collected(self) -> int:
        return self.count(ScopeState.COLLECTED)

    @property
    def stale(self) -> int:
        return sum(1 for scope in self.scopes if scope.evidence is EvidenceAge.STALE)

    @property
    def never_collected(self) -> int:
        return sum(1 for scope in self.scopes if scope.evidence is EvidenceAge.NONE)

    @property
    def returned_nothing(self) -> int:
        return sum(1 for scope in self.scopes if scope.returned_nothing)

    @property
    def missing_kinds(self) -> tuple[KindCoverage, ...]:
        return tuple(kind for kind in self.kinds if kind.state is KindState.NOT_COLLECTED)

    @property
    def verdict(self) -> CoverageVerdict:
        if not self.scopes:
            return CoverageVerdict.NONE
        unobserved = any(
            scope.state in (ScopeState.FAILED, ScopeState.UNAVAILABLE) for scope in self.scopes
        )
        if unobserved or self.missing_kinds:
            return CoverageVerdict.INCOMPLETE
        if all(scope.is_complete for scope in self.scopes):
            return CoverageVerdict.COMPLETE
        return CoverageVerdict.PARTIAL

    @property
    def complete(self) -> bool:
        return self.verdict is CoverageVerdict.COMPLETE

    @property
    def headline(self) -> str:
        """The one line that sits beside a number: "98 of 100 scopes collected · 1 …"."""
        if not self.scopes:
            return "Nothing has been collected"
        parts = [f"{self.collected} of {len(self.scopes)} collection scopes collected completely"]
        for state, word in (
            (ScopeState.UNAVAILABLE, "unavailable"),
            (ScopeState.FAILED, "failed"),
            (ScopeState.PARTIAL, "partial"),
            (ScopeState.RUNNING, "running"),
        ):
            if count := self.count(state):
                parts.append(f"{count} {word}")
        if self.stale:
            parts.append(f"{self.stale} holding older evidence")
        if self.missing_kinds:
            names = ", ".join(kind.collector for kind in self.missing_kinds)
            parts.append(f"never reported: {names}")
        return " · ".join(parts)

    @property
    def concerns(self) -> tuple[str, ...]:
        """One sentence per scope or kind that is not complete, in report order."""
        return tuple(
            [kind.sentence for kind in self.missing_kinds if kind.sentence is not None]
            + [scope.sentence for scope in self.scopes if not scope.is_complete]
        )

    def for_server(self, server: str | None) -> CoverageReport:
        """The scopes one answer about a resource on ``server`` reads.

        The directory's scopes always, because every answer reads identities and
        memberships; and the scopes whose target is the resource's own server -- its shares,
        its folders and its local groups. A server-targeted scope for any other server holds
        nothing this answer reads.
        """
        wanted = (server or "").casefold()
        scopes = tuple(
            scope
            for scope in self.scopes
            if scope.collector in DOMAIN_WIDE_COLLECTORS
            or (scope.target is not None and scope.target.casefold() == wanted)
        )
        return CoverageReport(scopes=scopes, kinds=self.kinds)


def assess_coverage(
    scopes: Iterable[ScopeOperations],
    *,
    failure_codes: Mapping[str, Sequence[str]] | None = None,
    expected: Iterable[str] = (),
) -> CoverageReport:
    """Fold the operations records into a reviewer's coverage report.

    ``failure_codes`` maps a run id to the error codes it reported, and is read only for the
    latest run of a scope that failed. ``expected`` names the collector kinds this estate is
    supposed to run; see the module docstring.
    """
    codes = failure_codes or {}
    expected_kinds = frozenset(expected)
    covered = tuple(
        sorted(
            (_scope(scope, codes.get(scope.latest.run_id, ())) for scope in scopes),
            key=lambda item: (item.collector, (item.target or "").casefold()),
        )
    )
    kinds: list[KindCoverage] = []
    for kind in sorted({*(item.value for item in CollectorKind), *expected_kinds}):
        mine = [scope for scope in covered if scope.collector == kind]
        if mine:
            state = KindState.REPORTED
        elif kind in expected_kinds:
            state = KindState.NOT_COLLECTED
        else:
            state = KindState.NOT_CONFIGURED
        kinds.append(
            KindCoverage(
                collector=kind,
                expected=kind in expected_kinds,
                state=state,
                scopes=len(mine),
                collected=sum(1 for scope in mine if scope.is_complete),
            )
        )
    return CoverageReport(scopes=covered, kinds=tuple(kinds))


def _scope(scope: ScopeOperations, codes: Sequence[str]) -> ScopeCoverage:
    latest = scope.latest
    completeness = latest.completeness
    if completeness is Completeness.COMPLETE:
        state = ScopeState.COLLECTED
    elif completeness is Completeness.PARTIAL:
        state = ScopeState.PARTIAL
    elif completeness is Completeness.IN_PROGRESS:
        state = ScopeState.RUNNING
    elif codes and set(codes) <= UNREACHABLE_ERROR_CODES:
        state = ScopeState.UNAVAILABLE
    else:
        state = ScopeState.FAILED

    success = scope.last_success
    if latest.status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL):
        evidence = EvidenceAge.CURRENT
        held_at = latest.completed_at or latest.started_at
    elif success is not None:
        # A running scan has not failed: what is held is the newest evidence there is.
        evidence = EvidenceAge.CURRENT if state is ScopeState.RUNNING else EvidenceAge.STALE
        held_at = success.completed_at or success.started_at
    else:
        evidence = EvidenceAge.NONE
        held_at = None
    return ScopeCoverage(
        collector=scope.collector,
        target=scope.target,
        state=state,
        evidence=evidence,
        attempted_at=latest.started_at,
        evidence_collected_at=held_at,
        observations=latest.observations_applied,
        failure_codes=tuple(sorted(set(codes))),
        shortfall=latest.shortfall,
    )


def server_of_resource(resource: str) -> str | None:
    r"""The server a UNC path names: ``\\GP-ENG-FS01\Engineering\…`` → ``GP-ENG-FS01``."""
    trimmed = resource.strip().lstrip("\\/")
    server = trimmed.replace("/", "\\").split("\\", 1)[0]
    return server or None


def _day(value: dt.datetime | None) -> str:
    return "an unknown date" if value is None else value.strftime("%Y-%m-%d")


# ----------------------------------------------------------------------------- totals


class TotalsReading(StrEnum):
    """How a risk total may be read. Ordered from least to most trustworthy."""

    NOT_EVALUATED = "not_evaluated"
    """The rules have never run. Every count is zero because nothing has looked."""

    PARTIAL = "partial"
    """The last evaluation examined only part of the estate, or ran out of room."""

    INCOMPLETE = "incomplete"
    """The evaluation was full, but collection is not: findings for unobserved or partly
    read scopes can be missing, and findings held for stale scopes date from their last
    successful collection. The counts are what ADG holds, not what the estate has."""

    STALE = "stale"
    """Collection has moved since the evaluation, or ADG cannot say which collected state
    the evaluation read. The counts describe an earlier snapshot."""

    COMPLETE = "complete"
    """A full evaluation, over complete collection, of the snapshot that is still current.
    These counts are totals."""


@dataclass(frozen=True, slots=True)
class TotalsInterpretation:
    reading: TotalsReading
    reasons: tuple[str, ...]
    basis_current: bool | None
    """Whether the evaluation read the collected state that is current now. ``None`` when
    the evaluation predates snapshot recording, so nobody can say."""

    @property
    def exhaustive(self) -> bool:
        return self.reading is TotalsReading.COMPLETE


def interpret_totals(
    *,
    has_ever_run: bool,
    evaluation_complete: bool,
    evaluation_trigger: str | None,
    truncation: str | None,
    evaluated_basis: str | None,
    current_basis: str,
    coverage: CoverageReport,
) -> TotalsInterpretation:
    """Whether risk counts may be read as totals, and every reason they may not.

    Every reason is listed, and the reading is the least trustworthy of them, so a
    reviewer told "incomplete" also learns that the snapshot has moved when both are true.
    """
    if not has_ever_run:
        return TotalsInterpretation(
            reading=TotalsReading.NOT_EVALUATED,
            reasons=("The risk rules have never been evaluated.",),
            basis_current=None,
        )
    readings: list[TotalsReading] = []
    reasons: list[str] = []
    if not evaluation_complete:
        readings.append(TotalsReading.PARTIAL)
        reasons.append(
            f"The last evaluation ran out of room: {truncation}"
            if truncation
            else f"The last evaluation was {evaluation_trigger or 'partial'} and examined "
            "only part of the estate."
        )
    verdict = coverage.verdict
    if verdict is CoverageVerdict.NONE:
        readings.append(TotalsReading.INCOMPLETE)
        reasons.append("Nothing has been collected, so nothing could be evaluated.")
    elif verdict is not CoverageVerdict.COMPLETE:
        readings.append(TotalsReading.INCOMPLETE)
        reasons.append(
            f"Collection is {verdict.value}: {coverage.headline}. Findings for scopes that "
            "were not read completely can be missing, and findings held for a scope whose "
            "latest scan failed date from its last successful scan."
        )
    if evaluated_basis is None:
        basis_current: bool | None = None
        readings.append(TotalsReading.STALE)
        reasons.append(
            "The last evaluation predates snapshot recording, so ADG cannot say which "
            "collected state it read."
        )
    else:
        basis_current = evaluated_basis == current_basis
        if not basis_current:
            readings.append(TotalsReading.STALE)
            reasons.append(
                "Collection has changed since the last evaluation; the counts describe the "
                "collected state as it stood then."
            )
    order = list(TotalsReading)
    reading = min(readings, key=order.index) if readings else TotalsReading.COMPLETE
    return TotalsInterpretation(
        reading=reading, reasons=tuple(reasons), basis_current=basis_current
    )
