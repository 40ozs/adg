"""ADG FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import build_api_router
from app.api.pagination import InvalidCursor
from app.auth.dependencies import install_auth
from app.config import Settings, get_settings
from app.db import Database
from app.domain import DomainValidationError
from app.ingestion.service import IngestionConflict, RunNotFound
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
    # Verifier and claims mapping are built once here, not per request: the OIDC
    # verifier owns the JWKS cache.
    install_auth(app, resolved)

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

    _install_exception_handlers(app)
    app.include_router(build_api_router(resolved))
    return app


def _install_exception_handlers(app: FastAPI) -> None:
    """Map the domain and ingestion failures onto the status codes the contract promises.

    Registered centrally rather than caught per route, because a collector's retry logic
    branches on the status code: a 422 must never be retried unchanged and a 409 must never
    be retried at all, so a failure that leaked out as a 500 would be retried forever.
    """

    @app.exception_handler(DomainValidationError)
    async def _domain_validation(request: Request, exc: DomainValidationError) -> JSONResponse:
        # Every domain message names the offending value and what was expected, so it is
        # returned verbatim: it is written for the operator reading the collector log.
        detail: dict[str, object] = {"message": str(exc)}
        if exc.field is not None:
            detail["field"] = exc.field
        if exc.value is not None:
            detail["value"] = exc.value
        logger.info(
            "api.rejected.invalid_payload",
            extra={"request_id": getattr(request.state, "request_id", None), "field": exc.field},
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": detail}
        )

    @app.exception_handler(IngestionConflict)
    async def _ingestion_conflict(request: Request, exc: IngestionConflict) -> JSONResponse:
        logger.warning(
            "api.rejected.conflict",
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    @app.exception_handler(RunNotFound)
    async def _run_not_found(request: Request, exc: RunNotFound) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})

    @app.exception_handler(InvalidCursor)
    async def _invalid_cursor(request: Request, exc: InvalidCursor) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)}
        )


app = create_app()
