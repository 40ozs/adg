"""Collection coverage, and the operator's view of it.

Two endpoints, deliberately separate.

``/status`` is the one **every page** calls before rendering an empty list: the frontend
must never decide on its own that an empty list means "nothing is there". Its answer comes
from :func:`app.domain.collection.assess_collection` — a pure function, so the rule that
turns run records into a verdict is tested without a database and cannot drift between the
API and the UI. It is kept cheap because it sits on the path of every screen.

``/operations`` is the collector status page: the last success and the last failure of every
scope, what each run failed to deliver, how many objects are actually stored, and every
reported error grouped by code. It costs five statements and no other screen waits on it, so
it is its own route rather than more fields on ``/status``.

``/coverage`` is the reviewer's form of the same records (ADG-DEMO-03): each scope's latest
attempt and the age of the evidence ADG holds for it, the collectors this estate expects and
whether each has reported, and the snapshot the counts describe. Given ``resource`` it also
names the scopes one answer about that folder reads, and whether an incomplete scope could
change it. It is what sits beside a risk total and an access investigation.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.caching import BasisView, basis_view
from app.api.deps import RequestSettings, Session
from app.auth.dependencies import requires
from app.auth.roles import Capability
from app.domain.collection import CollectorCoverage, assess_collection
from app.domain.coverage import (
    CoverageReport,
    KindCoverage,
    ScopeCoverage,
    server_of_resource,
)
from app.domain.operations import (
    ErrorGroup,
    ObjectCounts,
    RunOutcome,
    ScopeOperations,
    assemble_operations,
)
from app.ingestion.service import IngestionService
from app.repositories.operations import OperationsRepository
from app.services.coverage import collection_coverage

router = APIRouter(prefix="/api/v1/collection", tags=["collection"])


class CollectorCoverageView(BaseModel):
    """The latest run of one *scope*: one collector against one target.

    There is a row per ``(collector, target)``, not per collector kind. A kind that runs
    against four file servers produces four rows, and a failure on one of them stays
    visible however recently the other three succeeded.
    """

    collector: str
    status: str
    started_at: dt.datetime
    completed_at: dt.datetime | None
    error_count: int
    target: str | None
    downgrade_reason: str | None
    trustworthy: bool = Field(
        description="Whether this collector's coverage may be read as complete."
    )
    concern: str | None = Field(
        default=None, description="One sentence naming what is wrong, when something is."
    )


class CollectionStatusResponse(BaseModel):
    """Whether an empty result can be believed.

    ``health`` is the field a client branches on:

    * ``no_data`` — nothing has ever run. An empty page means "not collected".
    * ``healthy`` — every collector's latest run succeeded cleanly. An empty page means
      "nothing is there".
    * ``incomplete`` — something is partial, running, or reported errors.
    * ``failed`` — a collector's latest run failed; part of the estate is unobserved.
    """

    health: Literal["no_data", "healthy", "incomplete", "failed"]
    summary: str = Field(description="One sentence, written for an auditor.")
    concerns: list[str] = Field(default_factory=list)
    collectors: list[CollectorCoverageView] = Field(
        default_factory=list,
        description=(
            "One entry per (collector, target), ordered by collector then target. A "
            "collector kind that scans several servers appears once per server."
        ),
    )


@router.get(
    "/status",
    response_model=CollectionStatusResponse,
    summary="Whether an empty result means 'nothing is there' or 'nobody looked'",
    dependencies=[Depends(requires(Capability.COLLECTORS_READ))],
)
async def collection_status(session: Session) -> CollectionStatusResponse:
    latest = await IngestionService(session).latest_run_per_scope()
    status = assess_collection(
        CollectorCoverage(
            collector=item.collector,
            status=item.status,
            started_at=item.started_at,
            completed_at=item.completed_at,
            error_count=item.error_count,
            target=item.target,
            downgrade_reason=item.downgrade_reason,
        )
        for item in latest
    )
    return CollectionStatusResponse(
        health=status.health.value,
        summary=status.summary,
        concerns=list(status.concerns),
        collectors=[
            CollectorCoverageView(
                collector=item.collector,
                status=item.status.value,
                started_at=item.started_at,
                completed_at=item.completed_at,
                error_count=item.error_count,
                target=item.target,
                downgrade_reason=item.downgrade_reason,
                trustworthy=item.is_trustworthy,
                concern=item.concern,
            )
            for item in status.coverage
        ],
    )


# ---------------------------------------------------------------------------
# The operator view.
# ---------------------------------------------------------------------------


class RunOutcomeView(BaseModel):
    """One run, as the collector status page shows it."""

    run_id: str
    collector: str
    collector_host: str
    target: str | None
    status: str
    started_at: dt.datetime
    completed_at: dt.datetime | None
    error_count: int
    batches_reported: int | None
    batches_received: int
    observations_reported: int | None
    observations_applied: int
    declared_scopes: int
    reconciled_scopes: int
    incremental: bool
    downgrade_reason: str | None
    completeness: Literal["complete", "partial", "none", "in_progress"]
    shortfall: str | None = Field(
        default=None,
        description="What this run did not deliver, in one sentence. Null when nothing.",
    )


class ScopeOperationsView(BaseModel):
    """One collector against one target, with both of the runs an operator needs.

    ``latest``, ``last_success`` and ``last_failure`` are three different facts and the page
    shows all three. A scope whose latest run failed still has data on screen from its last
    success, and reporting only "failed" hides how old that data is.
    """

    collector: str
    target: str | None
    label: str
    completeness: Literal["complete", "partial", "none", "in_progress"]
    has_ever_succeeded: bool
    stale_success: bool = Field(
        description="The last success is not the latest run: the newest attempt did not work."
    )
    note: str | None
    latest: RunOutcomeView
    last_success: RunOutcomeView | None
    last_failure: RunOutcomeView | None


class ErrorGroupView(BaseModel):
    code: str
    count: int
    collectors: list[str]
    latest_occurred_at: dt.datetime | None
    sample_targets: list[str]
    is_widespread: bool = Field(
        description="Reported by more than one collector, so it is unlikely to be one object."
    )


class ObjectCountsView(BaseModel):
    """How many of each object the store holds.

    A collector that ran cleanly and wrote four rows did not do what it was configured to
    do, and nothing about its own status says so.
    """

    principals: int
    membership_edges: int
    servers: int
    shares: int
    share_aces: int
    directories: int
    ntfs_aces: int
    scan_runs: int
    total: int = Field(description="Collected objects. Excludes scan_runs: a run is an act.")


class CollectionOperationsResponse(BaseModel):
    health: Literal["no_data", "healthy", "incomplete", "failed"]
    summary: str
    notes: list[str] = Field(
        default_factory=list,
        description="One sentence per scope that needs attention, in scope order.",
    )
    scopes: list[ScopeOperationsView] = Field(default_factory=list)
    counts: ObjectCountsView
    errors: list[ErrorGroupView] = Field(
        default_factory=list, description="Reported collector errors, grouped by code."
    )
    total_errors: int = 0


def _run_view(run: RunOutcome) -> RunOutcomeView:
    return RunOutcomeView(
        run_id=run.run_id,
        collector=run.collector,
        collector_host=run.collector_host,
        target=run.target,
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_count=run.error_count,
        batches_reported=run.batches_reported,
        batches_received=run.batches_received,
        observations_reported=run.observations_reported,
        observations_applied=run.observations_applied,
        declared_scopes=run.declared_scopes,
        reconciled_scopes=run.reconciled_scopes,
        incremental=run.incremental,
        downgrade_reason=run.downgrade_reason,
        completeness=run.completeness.value,
        shortfall=run.shortfall,
    )


def _scope_view(scope: ScopeOperations) -> ScopeOperationsView:
    return ScopeOperationsView(
        collector=scope.collector,
        target=scope.target,
        label=scope.scope_label,
        completeness=scope.completeness.value,
        has_ever_succeeded=scope.has_ever_succeeded,
        stale_success=scope.stale_success,
        note=scope.note,
        latest=_run_view(scope.latest),
        last_success=None if scope.last_success is None else _run_view(scope.last_success),
        last_failure=None if scope.last_failure is None else _run_view(scope.last_failure),
    )


def _counts_view(counts: ObjectCounts) -> ObjectCountsView:
    return ObjectCountsView(**counts.as_mapping(), total=counts.total)


def _error_view(group: ErrorGroup) -> ErrorGroupView:
    return ErrorGroupView(
        code=group.code,
        count=group.count,
        collectors=list(group.collectors),
        latest_occurred_at=group.latest_occurred_at,
        sample_targets=list(group.sample_targets),
        is_widespread=group.is_widespread,
    )


@router.get(
    "/operations",
    response_model=CollectionOperationsResponse,
    summary="Collector operations: last success, last failure, completeness, counts, errors",
    dependencies=[Depends(requires(Capability.COLLECTORS_READ))],
)
async def collection_operations(session: Session) -> CollectionOperationsResponse:
    """Everything the collector status page renders, in five statements.

    The health verdict is the *same* one ``/status`` returns, folded from the same latest
    runs — so an operator told the estate is incomplete and a viewer reading the coverage
    banner are never looking at two different answers.
    """
    repository = OperationsRepository(session)
    latest = await repository.latest_runs()
    successes = await repository.latest_successes()
    failures = await repository.latest_failures()
    counts = await repository.object_counts()
    errors = await repository.error_summary()

    status = assess_collection(
        CollectorCoverage(
            collector=run.collector,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error_count=run.error_count,
            target=run.target,
            downgrade_reason=run.downgrade_reason,
        )
        for run in latest
    )
    report = assemble_operations(
        status=status,
        latest=latest,
        successes=successes,
        failures=failures,
        counts=counts,
        errors=errors,
    )
    return CollectionOperationsResponse(
        health=report.status.health.value,
        summary=report.status.summary,
        notes=list(report.notes),
        scopes=[_scope_view(scope) for scope in report.scopes],
        counts=_counts_view(report.counts),
        errors=[_error_view(group) for group in report.errors],
        total_errors=report.total_errors,
    )


# ---------------------------------------------------------------------------
# The reviewer's view (ADG-DEMO-03).
# ---------------------------------------------------------------------------

ScopeStateLiteral = Literal["collected", "partial", "unavailable", "failed", "running"]
EvidenceLiteral = Literal["current", "stale", "none"]
VerdictLiteral = Literal["complete", "partial", "incomplete", "none"]


class ScopeCoverageView(BaseModel):
    """One scope: what its latest attempt did, and how old the evidence ADG holds is."""

    collector: str
    target: str | None
    label: str
    state: ScopeStateLiteral = Field(
        description=(
            "What the latest run did: collected (complete), partial, unavailable (the target "
            "could not be reached), failed, or running."
        )
    )
    evidence: EvidenceLiteral = Field(
        description=(
            "What ADG holds: current (from the latest run), stale (the latest attempt "
            "delivered nothing; the rows are from an earlier run), or none."
        )
    )
    attempted_at: dt.datetime
    evidence_collected_at: dt.datetime | None
    observations: int = Field(description="Observations the latest run applied.")
    returned_nothing: bool = Field(
        description=(
            "Collected completely and the scan found nothing. The only state in which an "
            "empty result means 'checked, and nothing is there'."
        )
    )
    failure_codes: list[str] = Field(default_factory=list)
    sentence: str


class CollectorKindView(BaseModel):
    collector: str
    expected: bool
    state: Literal["reported", "not_collected", "not_configured"] = Field(
        description=(
            "reported; not_collected (expected for this estate, never reported); or "
            "not_configured (not expected, never reported -- ADG cannot tell an intentional "
            "omission from a collector nobody set up)."
        )
    )
    scopes: int
    collected: int


class CoverageCountsView(BaseModel):
    """Counts of scopes, never a percentage: scopes are not equal in size."""

    scopes: int
    collected: int
    partial: int
    unavailable: int
    failed: int
    running: int
    stale: int = Field(description="Scopes whose evidence is from an earlier run.")
    never_collected: int
    returned_nothing: int = Field(description="Collected completely, and found nothing.")


class CoverageSummaryView(BaseModel):
    """The line that sits beside a number, and what it is made of."""

    verdict: VerdictLiteral
    complete: bool
    headline: str
    counts: CoverageCountsView
    concerns: list[str] = Field(default_factory=list)
    snapshot: BasisView = Field(description="The collected state these scopes describe.")


class RelevantCoverageView(BaseModel):
    """The scopes one answer about a folder reads, and whether they could change it."""

    resource: str
    server: str | None
    verdict: VerdictLiteral
    complete: bool
    headline: str
    scopes: list[ScopeCoverageView]
    incomplete_elsewhere: int = Field(
        description="Scopes that are not complete and that this answer does not read."
    )
    could_change_answer: bool = Field(
        description=(
            "True when a scope this answer reads is not complete: a change ADG has not "
            "collected could change it. Separate from the answer's own certainty."
        )
    )
    statement: str


class CollectionCoverageResponse(CoverageSummaryView):
    """How complete the collected evidence is, scope by scope."""

    health: Literal["no_data", "healthy", "incomplete", "failed"] = Field(
        description="The same verdict /status returns, folded from the same runs."
    )
    expectation: Literal["configured", "dataset", "none"] = Field(
        description=(
            "Where the expected collector set came from: ADG_EXPECTED_COLLECTORS, the demo "
            "dataset's scan history, or nowhere."
        )
    )
    collectors: list[CollectorKindView]
    scopes: list[ScopeCoverageView]
    relevant: RelevantCoverageView | None = None


def scope_coverage_view(scope: ScopeCoverage) -> ScopeCoverageView:
    return ScopeCoverageView(
        collector=scope.collector,
        target=scope.target,
        label=scope.label,
        state=scope.state.value,
        evidence=scope.evidence.value,
        attempted_at=scope.attempted_at,
        evidence_collected_at=scope.evidence_collected_at,
        observations=scope.observations,
        returned_nothing=scope.returned_nothing,
        failure_codes=list(scope.failure_codes),
        sentence=scope.sentence,
    )


def _kind_view(kind: KindCoverage) -> CollectorKindView:
    return CollectorKindView(
        collector=kind.collector,
        expected=kind.expected,
        state=kind.state.value,
        scopes=kind.scopes,
        collected=kind.collected,
    )


def coverage_counts_view(report: CoverageReport) -> CoverageCountsView:
    from app.domain.coverage import ScopeState

    return CoverageCountsView(
        scopes=len(report.scopes),
        collected=report.collected,
        partial=report.count(ScopeState.PARTIAL),
        unavailable=report.count(ScopeState.UNAVAILABLE),
        failed=report.count(ScopeState.FAILED),
        running=report.count(ScopeState.RUNNING),
        stale=report.stale,
        never_collected=report.never_collected,
        returned_nothing=report.returned_nothing,
    )


def coverage_summary_view(report: CoverageReport, snapshot: BasisView) -> CoverageSummaryView:
    return CoverageSummaryView(
        verdict=report.verdict.value,
        complete=report.complete,
        headline=report.headline,
        counts=coverage_counts_view(report),
        concerns=list(report.concerns),
        snapshot=snapshot,
    )


def relevant_coverage_view(report: CoverageReport, resource: str) -> RelevantCoverageView:
    server = server_of_resource(resource)
    relevant = report.for_server(server)
    read = {(scope.collector, scope.target) for scope in relevant.scopes}
    elsewhere = sum(
        1
        for scope in report.scopes
        if not scope.is_complete and (scope.collector, scope.target) not in read
    )
    incomplete = [scope for scope in relevant.scopes if not scope.is_complete]
    if not relevant.scopes:
        statement = (
            "No collector has reported a scope this answer reads, so nothing about it has "
            "been collected."
        )
    elif not incomplete:
        statement = (
            f"Every scope this answer reads was collected completely in its latest scan "
            f"({', '.join(scope.label for scope in relevant.scopes)})."
        )
        if elsewhere:
            statement += (
                f" {elsewhere} scope(s) elsewhere in the estate are not complete. None of "
                "them holds this folder, its share, its server's local groups or the "
                "directory, so they cannot change this answer."
            )
    else:
        statement = (
            "Not every scope this answer reads is complete: "
            + " ".join(scope.sentence for scope in incomplete)
            + " The answer is computed from what ADG holds; a change it has not collected "
            "could change it."
        )
    return RelevantCoverageView(
        resource=resource,
        server=server,
        verdict=relevant.verdict.value,
        complete=relevant.complete,
        headline=relevant.headline,
        scopes=[scope_coverage_view(scope) for scope in relevant.scopes],
        incomplete_elsewhere=elsewhere,
        could_change_answer=bool(incomplete) or not relevant.scopes,
        statement=statement,
    )


@router.get(
    "/coverage",
    response_model=CollectionCoverageResponse,
    summary="How complete the collected evidence is, scope by scope, and for one folder",
    dependencies=[Depends(requires(Capability.COLLECTORS_READ))],
)
async def collection_coverage_report(
    session: Session,
    settings: RequestSettings,
    resource: Annotated[
        str | None,
        Query(
            max_length=2048,
            description="A UNC path. Adds the scopes an answer about this folder reads.",
        ),
    ] = None,
) -> CollectionCoverageResponse:
    """Coverage for a reviewer, in at most five statements.

    A failed or unavailable scope is reported with the age of the evidence ADG still holds
    for it; a scope that was collected and found nothing is reported as exactly that. The
    two are never the same row.
    """
    coverage = await collection_coverage(session, settings)
    report = coverage.report
    snapshot = basis_view(coverage.basis)
    summary = coverage_summary_view(report, snapshot)
    return CollectionCoverageResponse(
        **summary.model_dump(),
        health=coverage.status.health.value,
        expectation=coverage.expectation,
        collectors=[_kind_view(kind) for kind in report.kinds],
        scopes=[scope_coverage_view(scope) for scope in report.scopes],
        relevant=(
            relevant_coverage_view(report, resource.strip())
            if resource is not None and resource.strip()
            else None
        ),
    )


__all__ = ["router"]
