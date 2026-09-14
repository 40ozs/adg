"""Collector ingestion endpoints (contract v1).

These are the four routes specified in `docs/contracts/collector-protocol.md` §1. The
status codes are part of the contract, not an implementation detail, because a collector's
retry logic branches on them:

* ``201`` a run was created; ``200`` the same start was replayed.
* ``202`` a batch was applied, or recognized as already applied (``duplicate: true``).
* ``409`` the request contradicts an existing run — never retried unchanged.
* ``422`` the payload is wrong — never retried unchanged either.

Phase 1B stores ``principal`` and ``membership_edge`` observations. A batch carrying any
other kind is rejected with a message naming the kinds, rather than accepted with those
observations quietly dropped: a collector told "accepted" would go on to report coverage
that ADG does not actually hold.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Response, status
from pydantic import BaseModel, Field

from app.api.deps import Session
from app.contracts.v1 import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.ingestion.service import IngestionService

logger = logging.getLogger("adg.api.ingestion")

router = APIRouter(prefix="/api/v1/scan-runs", tags=["ingestion"])

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


class BatchAcceptedResponse(BaseModel):
    """Acknowledges a batch, applied or recognized as a replay."""

    run_id: UUID
    batch_id: UUID
    applied: int = Field(description="Observations stored by this call. Zero for a replay.")
    duplicate: bool = Field(description="True when this batch_id had already been applied.")
    principals_written: int = 0
    edges_written: int = 0


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
    error_count: int
    notes: str | None
    downgrade_reason: str | None
    declared_scopes: list[ScopeView]
    reconciled_scopes: list[ScopeView]
    errors: list[CollectorErrorView]


@router.post(
    "",
    response_model=ScanRunStartedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin a scan run",
    responses={
        200: {"description": "The same start was replayed; the existing run is returned."},
        409: {"description": "The run id exists but describes a different run."},
    },
)
async def start_scan_run(
    start: ScanRunStart, session: Session, response: Response
) -> ScanRunStartedResponse:
    outcome = await IngestionService(session).start_run(start)
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    logger.info(
        "ingestion.run.start",
        # Not "created": LogRecord already owns that attribute, and logging raises rather
        # than overwrite it — which would turn every successful run start into a 500.
        extra={"run_id": str(outcome.run_id), "run_created": outcome.created},
    )
    return ScanRunStartedResponse(
        run_id=outcome.run_id, status=outcome.status.value, created=outcome.created
    )


@router.post(
    "/{run_id}/batches",
    response_model=BatchAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a batch of observations",
    responses={
        404: {"description": "No such run; send the start envelope first."},
        409: {"description": "The run is already completed and accepts no more observations."},
        422: {"description": "The payload is invalid, or carries a kind this phase cannot store."},
    },
)
async def submit_batch(
    run_id: RunIdPath, batch: ObservationBatch, session: Session
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
        },
    )
    return BatchAcceptedResponse(
        run_id=outcome.run_id,
        batch_id=outcome.batch_id,
        applied=outcome.applied,
        duplicate=outcome.duplicate,
        principals_written=outcome.principals_written,
        edges_written=outcome.edges_written,
    )


@router.post(
    "/{run_id}/completion",
    response_model=ScanRunCompletedResponse,
    summary="Close a scan run",
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
    run_id: RunIdPath, completion: ScanRunCompletion, session: Session
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
        },
    )
    return ScanRunCompletedResponse(
        run_id=outcome.run_id,
        status=outcome.status.value,
        already_completed=outcome.already_completed,
        reconciled_scopes=outcome.reconciled_scopes,
        downgrade_reason=outcome.downgrade_reason,
    )


@router.get(
    "/{run_id}",
    response_model=ScanRunView,
    summary="Inspect a scan run",
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
        error_count=snapshot.error_count,
        notes=snapshot.notes,
        downgrade_reason=snapshot.downgrade_reason,
        declared_scopes=[ScopeView(kind=kind, key=key) for kind, key in snapshot.declared_scopes],
        reconciled_scopes=[
            ScopeView(kind=kind, key=key) for kind, key in snapshot.reconciled_scopes
        ],
        errors=[CollectorErrorView(**error) for error in snapshot.errors],
    )


__all__ = ["router"]
