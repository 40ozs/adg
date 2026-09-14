"""Collection coverage: the endpoint every empty state consults before it renders.

The frontend must never decide on its own that an empty list means "nothing is there". It
asks here, and the answer comes from :func:`app.domain.collection.assess_collection` — a
pure function, so the rule that turns run records into a verdict is tested without a
database and cannot drift between the API and the UI.
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
from app.ingestion.service import IngestionService

router = APIRouter(prefix="/api/v1/collection", tags=["collection"])


class CollectorCoverageView(BaseModel):
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
    collectors: list[CollectorCoverageView] = Field(default_factory=list)


@router.get(
    "/status",
    response_model=CollectionStatusResponse,
    summary="Whether an empty result means 'nothing is there' or 'nobody looked'",
    dependencies=[Depends(requires(Capability.COLLECTORS_READ))],
)
async def collection_status(session: Session) -> CollectionStatusResponse:
    latest = await IngestionService(session).latest_run_per_collector()
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


__all__ = ["router"]
