"""Structured logging for the ADG backend.

Log records are emitted as one JSON object per line so that container log drivers and
log-aggregation tooling can parse them without regular expressions. A human-readable
text format is available for local debugging via ``ADG_LOG_FORMAT=text``.

Every record passes :class:`app.logging_redaction.RedactingFilter` on its way to either
format, so a credential that reaches the logging call — from a driver's exception text, a
library's warning, or an ``extra=`` added later — does not reach the stream. See that
module for which four shapes are removed and, just as deliberately, which identifiers are
not.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from collections.abc import Mapping
from typing import Any

from app.logging_redaction import RedactingFilter, redact

# Attributes present on every ``logging.LogRecord``; anything else was supplied by the
# caller through ``extra=`` and is merged into the JSON payload.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class TextLogFormatter(logging.Formatter):
    """The human-readable format, with the same exception redaction as the JSON one.

    A subclass rather than a plain ``Formatter`` so that ``ADG_LOG_FORMAT=text`` — which is
    what a developer runs locally, against a real database, with real credentials in the
    environment — is not the one format that prints a DSN in a traceback.
    """

    def formatException(self, ei: Any) -> str:
        return redact(super().formatException(ei))

    def formatStack(self, stack_info: str) -> str:
        return redact(super().formatStack(stack_info))


class JsonLogFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def __init__(self, service: str, environment: str, version: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment
        self._version = version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
            "environment": self._environment,
            "version": self._version,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = _coerce(value)
        if record.exc_info:
            # Redacted here rather than only in the filter: the traceback is rendered from
            # exc_info at format time, and a frame's locals or a driver's DSN can carry a
            # credential that no filter has seen yet.
            payload["exception"] = redact(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = redact(self.formatStack(record.stack_info))
        return json.dumps(payload, default=str, separators=(",", ":"))


def _coerce(value: Any) -> Any:
    """Return a JSON-serializable view of ``value`` without raising."""
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _coerce(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_coerce(item) for item in value]
    return str(value)


def configure_logging(
    *,
    level: str = "INFO",
    log_format: str = "json",
    service: str = "ADG",
    environment: str = "development",
    version: str = "0.0.0",
) -> None:
    """Install the ADG log handler on the root logger.

    Existing handlers are replaced so that repeated calls (for example, a reloading
    development server) do not duplicate output.
    """
    handler = logging.StreamHandler(stream=sys.stdout)
    if log_format == "json":
        handler.setFormatter(
            JsonLogFormatter(service=service, environment=environment, version=version)
        )
    else:
        handler.setFormatter(TextLogFormatter("%(asctime)s %(levelname)-8s %(name)s %(message)s"))
    # On the handler, not on a logger: a filter on a logger does not see records that
    # propagate up from its children, and uvicorn's records do exactly that.
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; route its records through ours instead.
    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # uvicorn.access is deliberately left as uvicorn configured it: when the server runs
    # with access_log=False it sets propagate=False, and forcing it back on here would
    # duplicate every request already logged by the API's request-context middleware.
    logging.getLogger("uvicorn.access").handlers.clear()
