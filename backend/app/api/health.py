"""Liveness and readiness endpoints.

``/health/live`` answers "is this process running?" and must never touch a dependency.
``/health/ready`` answers "can this process serve traffic?" and therefore probes
PostgreSQL. Orchestrators use the two differently: a failing liveness check restarts the
container, a failing readiness check only removes it from rotation.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from app import __version__
from app.db import Database

logger = logging.getLogger("adg.api.health")

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["alive"] = "alive"
    version: str = Field(description="ADG backend version")


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    latency_ms: float
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    version: str
    dependencies: list[DependencyStatus]


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
async def live() -> LivenessResponse:
    return LivenessResponse(version=__version__)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    database: Database = request.app.state.database
    result = await database.check_connectivity()

    if not result.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning(
            "health.ready.dependency_unavailable",
            extra={"dependency": "postgresql", "error": result.error},
        )

    return ReadinessResponse(
        status="ready" if result.ok else "not_ready",
        version=__version__,
        dependencies=[
            DependencyStatus(
                name="postgresql",
                ok=result.ok,
                latency_ms=result.latency_ms,
                error=result.error,
            )
        ],
    )
