"""HTTP API routers.

``api_router`` is the single aggregation point; later phases attach their routers here so
that the application factory stays stable.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import health, meta

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(meta.router)

__all__ = ["api_router"]
