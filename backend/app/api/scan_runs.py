"""Collector ingestion endpoints (contract v1).

These are the four routes specified in `docs/contracts/collector-protocol.md` §1. The
status codes are part of the contract, not an implementation detail, because a collector's
retry logic branches on them:

* ``201`` a run was created; ``200`` the same start was replayed.
* ``202`` a batch was applied, or recognized as already applied (``duplicate: true``).
* ``409`` the request contradicts an existing run — never retried unchanged.
* ``422`` the payload is wrong — never retried unchanged either.

This endpoint stores ``principal``, ``membership_edge``, ``server``, ``smb_share``, and
``smb_ace`` observations. A batch carrying an NTFS kind is rejected with a message naming
the kinds and listing what is accepted, rather than accepted with those observations quietly
dropped: a collector told "accepted" would go on to report coverage that ADG does not
actually hold.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import Session
from app.api.pagination import (
    MAX_LIMIT,
    PageInfo,
    decode_offset_cursor,
    encode_offset_cursor,
    normalize_limit,
)
from app.auth.dependencies import IngestPrincipal, ingest_principal, requires
from app.auth.roles import Capability
from app.contracts.v1 import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.domain import Checkpoint, CollectorKind, ReconciliationDrift, ScanStatus
from app.ingestion.checkpoints import CheckpointAdvance
from app.ingestion.service import IngestionService, RunSummary

logger = logging.getLogger("adg.api.ingestion")

#: Writing observations. A collector key satisfies this, and so does an administrator's
#: token; see :func:`app.auth.dependencies.ingest_principal`.
router = APIRouter(prefix="/api/v1/scan-runs", tags=["ingestion"])

#: Reading run history. Every role holds it, including viewer: somebody looking at an
#: empty list has to be able to find out whether anything ran.
READ_RUNS = Depends(requires(Capability.COLLECTORS_READ))

#: Writing observations, declared at the *route* rather than only as a handler parameter.
#: Route-level dependencies are solved before the handler's own, and the handler's own are
#: solved in declaration order — so a bare ``principal: IngestPrincipal`` parameter after
#: ``session: Session`` would open a database session for a request that is about to be
#: rejected with 401. The parameter is kept for logging; FastAPI caches the dependency, so
#: it is resolved exactly once per request.
#: ``tests/api/test_authorization.py`` holds this in place.
INGEST = Depends(ingest_principal)

RunIdPath = Annotated[
    UUID, Path(description="The run id the collector generated and reuses on every retry.")
]


class ScanRunStartedResponse(BaseModel):
    """Acknowledges a start envelope."""

    run_id: UUID
    status: str
    created: bool = Field(
        description="True when this call opened the run; false when it replayed a start."
    )


class RefusedAffirmationView(BaseModel):
    """One affirmation the server would not accept (contract 1.4).

    Returned rather than logged because the collector is the only party that can act on it:
    every entry means *send this object in full next time*, and a collector that is not told
    keeps affirming a state ADG does not hold.
    """

    source_key: str
    reason: str = Field(
        description=(
            "unknown_object, no_stored_digest, digest_mismatch, absent, or untracked. "
            "digest_mismatch is the ordinary one: the ACL changed."
        )
    )
    detail: str = Field(description="One sentence saying what to do about it.")


class CheckpointView(BaseModel):
    """A collector checkpoint as it went over the wire (contract 1.4)."""

    kind: str
    token: str
    issuer: str
    issued_at: dt.datetime


class CheckpointAdvanceView(BaseModel):
    """Whether this call moved the job's resume point, and why not if it did not."""

    job: str
    accepted: bool
    checkpoint: CheckpointView
    rejection_code: str | None = None
    rejection_reason: str | None = Field(
        default=None,
        description=(
            "Present when the cursor was refused. The stored checkpoint is unchanged, so "
            "the next run of this job must read its whole scope rather than resume."
        ),
    )


class ReconciliationDriftView(BaseModel):
    """What a reconciliation corrected that the incremental runs could not."""

    scope_kind: str
    scope_key: str
    marked_absent: int
    revived: int
    delta_runs_since: int
    summary: str


class BatchAcceptedResponse(BaseModel):
    """Acknowledges a batch, applied or recognized as a replay."""

    run_id: UUID
    batch_id: UUID
    applied: int = Field(description="Observations stored by this call. Zero for a replay.")
    duplicate: bool = Field(description="True when this batch_id had already been applied.")
    principals_written: int = 0
    edges_written: int = 0
    servers_written: int = 0
    shares_written: int = 0
    share_aces_written: int = 0
    affirmed: int = Field(
        default=0, description="Affirmations accepted: the object was confirmed unchanged."
    )
    refused_affirmations: list[RefusedAffirmationView] = Field(
        default_factory=list,
        description=(
            "Affirmations the server would not accept. Not an error — re-send each named "
            "object in full."
        ),
    )
    checkpoint: CheckpointAdvanceView | None = Field(
        default=None, description="The job cursor this batch moved, when it carried one."
    )


class ScanRunCompletedResponse(BaseModel):
    """Acknowledges a completion, with the status the server actually recorded."""

    run_id: UUID
    status: str = Field(
        description=(
            "The recorded status, which may be lower than the one claimed: a run that sent "
            "more batches than arrived is recorded as partial."
        )
    )
    already_completed: bool
    reconciled_scopes: int
    downgrade_reason: str | None = None
    drift: list[ReconciliationDriftView] = Field(
        default_factory=list,
        description=(
            "One entry per reconciled scope, counting only what this run corrected about "
            "objects' presence. Empty for a run that reconciled nothing."
        ),
    )
    checkpoint: CheckpointAdvanceView | None = Field(
        default=None,
        description=(
            "The job cursor this completion moved. Absent when the run carried no "
            "checkpoint, and refused when the server downgraded the run."
        ),
    )


class ScopeView(BaseModel):
    kind: str
    key: str


class CollectorErrorView(BaseModel):
    code: str
    message: str
    target: str | None = None
    occurred_at: dt.datetime | None = None


class ScanRunView(BaseModel):
    """Everything known about one run."""

    run_id: UUID
    status: str
    incremental: bool
    mode: str = Field(description="full, delta, or reconcile (contract 1.4).")
    job: str | None = Field(default=None, description="The scheduled job this run belongs to.")
    started_at: dt.datetime
    completed_at: dt.datetime | None
    collector: str
    collector_host: str
    method: str
    collector_version: str | None
    target: str | None
    batch_count_reported: int | None
    batch_count_received: int
    observation_count_reported: int | None
    observation_count_applied: int
    affirmation_count_reported: int | None = None
    affirmation_count_applied: int = 0
    affirmations_refused: int = 0
    error_count: int
    notes: str | None
    downgrade_reason: str | None
    declared_scopes: list[ScopeView]
    reconciled_scopes: list[ScopeView]
    errors: list[CollectorErrorView]
    baseline: CheckpointView | None = Field(
        default=None, description="The checkpoint this delta resumed from."
    )
    result_checkpoint: CheckpointView | None = Field(
        default=None, description="The cursor this run left behind."
    )
    drift: list[ReconciliationDriftView] = Field(default_factory=list)


@router.post(
    "",
    response_model=ScanRunStartedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin a scan run",
    dependencies=[INGEST],
    responses={
        200: {"description": "The same start was replayed; the existing run is returned."},
        409: {"description": "The run id exists but describes a different run."},
    },
)
async def start_scan_run(
    start: ScanRunStart,
    session: Session,
    response: Response,
    principal: IngestPrincipal,
) -> ScanRunStartedResponse:
    outcome = await IngestionService(session).start_run(start)
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    logger.info(
        "ingestion.run.start",
        # Not "created": LogRecord already owns that attribute, and logging raises rather
        # than overwrite it — which would turn every successful run start into a 500.
        extra={
            "run_id": str(outcome.run_id),
            "run_created": outcome.created,
            "subject": principal.subject,
        },
    )
    return ScanRunStartedResponse(
        run_id=outcome.run_id, status=outcome.status.value, created=outcome.created
    )


@router.post(
    "/{run_id}/batches",
    response_model=BatchAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a batch of observations",
    dependencies=[INGEST],
    responses={
        404: {"description": "No such run; send the start envelope first."},
        409: {"description": "The run is already completed and accepts no more observations."},
        422: {"description": "The payload is invalid, or carries a kind this phase cannot store."},
    },
)
async def submit_batch(
    run_id: RunIdPath,
    batch: ObservationBatch,
    session: Session,
    principal: IngestPrincipal,
) -> BatchAcceptedResponse:
    if UUID(batch.run_id) != run_id:
        # Applying it to the path's run would attribute these observations to a run that
        # never declared their scope.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"The batch declares run_id {batch.run_id} but was posted to run {run_id}. "
                "Post a batch to the run it belongs to."
            ),
        )
    outcome = await IngestionService(session).apply_batch(batch)
    logger.info(
        "ingestion.batch.applied",
        extra={
            "run_id": str(outcome.run_id),
            "batch_id": str(outcome.batch_id),
            "applied": outcome.applied,
            "duplicate": outcome.duplicate,
            "affirmed": outcome.affirmations.applied,
            "affirmations_refused": outcome.affirmations.refused_count,
            "subject": principal.subject,
        },
    )
    return BatchAcceptedResponse(
        run_id=outcome.run_id,
        batch_id=outcome.batch_id,
        applied=outcome.applied,
        duplicate=outcome.duplicate,
        principals_written=outcome.principals_written,
        edges_written=outcome.edges_written,
        servers_written=outcome.servers_written,
        shares_written=outcome.shares_written,
        share_aces_written=outcome.share_aces_written,
        affirmed=outcome.affirmations.applied,
        refused_affirmations=[
            RefusedAffirmationView(
                source_key=item.source_key, reason=item.reason, detail=item.detail
            )
            for item in outcome.affirmations.refused
        ],
        checkpoint=_advance_view(outcome.checkpoint),
    )


@router.post(
    "/{run_id}/completion",
    response_model=ScanRunCompletedResponse,
    summary="Close a scan run",
    dependencies=[INGEST],
    responses={
        404: {"description": "No such run."},
        409: {
            "description": (
                "The run was already completed with a different outcome, or the completion "
                "reconciles a scope the run never declared."
            )
        },
    },
)
async def complete_scan_run(
    run_id: RunIdPath,
    completion: ScanRunCompletion,
    session: Session,
    principal: IngestPrincipal,
) -> ScanRunCompletedResponse:
    if UUID(completion.run_id) != run_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"The completion declares run_id {completion.run_id} but was posted to run "
                f"{run_id}. Close the run it belongs to."
            ),
        )
    outcome = await IngestionService(session).complete_run(completion)
    logger.info(
        "ingestion.run.complete",
        extra={
            "run_id": str(outcome.run_id),
            "status": outcome.status.value,
            "downgraded": outcome.downgrade_reason is not None,
            "drift": sum(item.drift for item in outcome.drift),
            "checkpoint_advanced": (
                outcome.checkpoint.accepted if outcome.checkpoint is not None else None
            ),
            "subject": principal.subject,
        },
    )
    return ScanRunCompletedResponse(
        run_id=outcome.run_id,
        status=outcome.status.value,
        already_completed=outcome.already_completed,
        reconciled_scopes=outcome.reconciled_scopes,
        downgrade_reason=outcome.downgrade_reason,
        drift=[_drift_view(item) for item in outcome.drift],
        checkpoint=_advance_view(outcome.checkpoint),
    )


@router.get(
    "/{run_id}",
    response_model=ScanRunView,
    summary="Inspect a scan run",
    dependencies=[READ_RUNS],
    responses={404: {"description": "No such run."}},
)
async def get_scan_run(run_id: RunIdPath, session: Session) -> ScanRunView:
    snapshot = await IngestionService(session).get_run(run_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No scan run {run_id}.")
    return ScanRunView(
        run_id=snapshot.run_id,
        status=snapshot.status.value,
        incremental=snapshot.incremental,
        mode=snapshot.mode.value,
        job=snapshot.job,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        collector=snapshot.collector,
        collector_host=snapshot.collector_host,
        method=snapshot.method,
        collector_version=snapshot.collector_version,
        target=snapshot.target,
        batch_count_reported=snapshot.batch_count_reported,
        batch_count_received=snapshot.batch_count_received,
        observation_count_reported=snapshot.observation_count_reported,
        observation_count_applied=snapshot.observation_count_applied,
        affirmation_count_reported=snapshot.affirmation_count_reported,
        affirmation_count_applied=snapshot.affirmation_count_applied,
        affirmations_refused=snapshot.affirmations_refused,
        error_count=snapshot.error_count,
        notes=snapshot.notes,
        downgrade_reason=snapshot.downgrade_reason,
        declared_scopes=[ScopeView(kind=kind, key=key) for kind, key in snapshot.declared_scopes],
        reconciled_scopes=[
            ScopeView(kind=kind, key=key) for kind, key in snapshot.reconciled_scopes
        ],
        errors=[CollectorErrorView(**error) for error in snapshot.errors],
        baseline=_checkpoint_view(snapshot.baseline),
        result_checkpoint=_checkpoint_view(snapshot.result_checkpoint),
        drift=[_drift_view(item) for item in snapshot.drift],
    )


class ScanRunSummaryView(BaseModel):
    """One row of the run list."""

    run_id: UUID
    status: str
    incremental: bool
    started_at: dt.datetime
    completed_at: dt.datetime | None
    collector: str
    collector_host: str
    method: str
    collector_version: str | None
    target: str | None
    mode: str
    job: str | None = None
    batch_count_received: int
    observation_count_applied: int
    affirmation_count_applied: int = 0
    error_count: int
    downgrade_reason: str | None


class ScanRunListResponse(BaseModel):
    items: list[ScanRunSummaryView]
    page: PageInfo


@router.get(
    "",
    response_model=ScanRunListResponse,
    summary="Scan runs, newest first",
    dependencies=[READ_RUNS],
    responses={422: {"description": "The cursor is not one this endpoint issued."}},
)
async def list_scan_runs(
    session: Session,
    collector: Annotated[
        CollectorKind | None,
        Query(description="Restrict to one collector kind."),
    ] = None,
    run_status: Annotated[
        ScanStatus | None,
        Query(
            alias="status",
            description="Restrict to runs in one lifecycle state.",
        ),
    ] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page.")] = None,
) -> ScanRunListResponse:
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    page = await IngestionService(session).list_runs(
        limit=page_size,
        offset=offset,
        collector=collector.value if collector is not None else None,
        status=run_status,
    )
    return ScanRunListResponse(
        items=[_summary_view(item) for item in page.items],
        page=PageInfo(
            limit=page_size,
            has_more=page.has_more,
            next_cursor=(encode_offset_cursor(offset + page_size) if page.has_more else None),
            total=page.total,
        ),
    )


def _summary_view(item: RunSummary) -> ScanRunSummaryView:
    return ScanRunSummaryView(
        run_id=item.run_id,
        status=item.status.value,
        incremental=item.incremental,
        started_at=item.started_at,
        completed_at=item.completed_at,
        collector=item.collector,
        collector_host=item.collector_host,
        method=item.method,
        collector_version=item.collector_version,
        target=item.target,
        mode=item.mode.value,
        job=item.job,
        batch_count_received=item.batch_count_received,
        observation_count_applied=item.observation_count_applied,
        affirmation_count_applied=item.affirmation_count_applied,
        error_count=item.error_count,
        downgrade_reason=item.downgrade_reason,
    )


def _checkpoint_view(checkpoint: Checkpoint | None) -> CheckpointView | None:
    if checkpoint is None:
        return None
    return CheckpointView(
        kind=checkpoint.kind.value,
        token=checkpoint.token,
        issuer=checkpoint.issuer,
        issued_at=checkpoint.issued_at,
    )


def _advance_view(advance: CheckpointAdvance | None) -> CheckpointAdvanceView | None:
    if advance is None:
        return None
    view = _checkpoint_view(advance.checkpoint)
    assert view is not None
    return CheckpointAdvanceView(
        job=advance.job,
        accepted=advance.accepted,
        checkpoint=view,
        rejection_code=advance.code,
        rejection_reason=advance.rejection.message if advance.rejection else None,
    )


def _drift_view(drift: ReconciliationDrift) -> ReconciliationDriftView:
    return ReconciliationDriftView(
        scope_kind=drift.scope_kind,
        scope_key=drift.scope_key,
        marked_absent=drift.marked_absent,
        revived=drift.revived,
        delta_runs_since=drift.delta_runs_since,
        summary=drift.summary(),
    )


__all__ = ["router"]
