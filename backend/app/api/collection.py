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
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import Session
from app.auth.dependencies import requires
from app.auth.roles import Capability
from app.domain.collection import CollectorCoverage, assess_collection
from app.domain.operations import (
    ErrorGroup,
    ObjectCounts,
    RunOutcome,
    ScopeOperations,
    assemble_operations,
)
from app.ingestion.service import IngestionService
from app.repositories.operations import OperationsRepository

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


__all__ = ["router"]
