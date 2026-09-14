"""ADG FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import api_router
from app.config import Settings, get_settings
from app.db import Database
from app.logging_config import configure_logging

logger = logging.getLogger("adg.api")

REQUEST_ID_HEADER = "X-Request-ID"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ADG API application.

    Passing ``settings`` explicitly is what lets tests exercise alternate environments
    without mutating process-wide state.
    """
    resolved = settings or get_settings()
    configure_logging(
        level=resolved.log_level,
        log_format=resolved.log_format,
        service=resolved.app_name,
        environment=resolved.environment,
        version=__version__,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.database = Database(resolved)
        logger.info(
            "api.startup",
            extra={"environment": resolved.environment, "version": __version__},
        )
        try:
            yield
        finally:
            await app.state.database.dispose()
            logger.info("api.shutdown")

    app = FastAPI(
        title="ADG API",
        summary="Windows-domain share-access auditing and governance",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved

    if resolved.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=[REQUEST_ID_HEADER],
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "http.request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    app.include_router(api_router)
    return app


app = create_app()
