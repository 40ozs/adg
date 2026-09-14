"""Run the ADG API: ``python -m app``.

This is the supported way to start the API natively (especially on Windows) because it
selects an event loop the PostgreSQL driver can use. See :mod:`app.runtime`.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from app.config import get_settings

LOOP_FACTORY = "app.runtime:event_loop_factory"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app", description="Run the ADG API.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: %(default)s)")
    parser.add_argument("--reload", action="store_true", help="Reload on source changes")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=LOOP_FACTORY,
        log_config=None,  # app.logging_config owns log handling
        access_log=False,  # the request-context middleware logs requests structurally
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
