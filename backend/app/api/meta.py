"""Application identity and version endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import __version__
from app.config import Settings

router = APIRouter(tags=["meta"])


class VersionResponse(BaseModel):
    name: str
    version: str
    environment: str


@router.get("/version", response_model=VersionResponse, summary="Application version")
async def version(request: Request) -> VersionResponse:
    settings: Settings = request.app.state.settings
    return VersionResponse(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )
