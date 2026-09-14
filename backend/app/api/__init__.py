"""HTTP API routers.

``api_router`` is the single aggregation point; later phases attach their routers here so
that the application factory stays stable.

Health and version endpoints sit at the root because probes and humans read them. Everything
that speaks about collected facts lives under ``/api/v1``, which is the version boundary the
collector contract is pinned to: a breaking change means ``/api/v2``, never an edit here.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import graph, health, meta, scan_runs

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(meta.router)
api_router.include_router(scan_runs.router)
api_router.include_router(graph.router)

__all__ = ["api_router"]
