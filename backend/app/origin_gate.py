"""Run the ADG API behind an origin key: ``python -m app.origin_gate``.

For a host whose address is public but which exactly one caller may reach: the free Render
web service behind the Cloudflare staging Worker (docs/deployment/cloudflare-demo.md).
Render's free tier offers no private network or IP allow-list, so without this anyone who
found the ``onrender.com`` address would reach the API directly, around the Worker, and could
sign in as any development account the Render service lists.

The application is unchanged: the gate wraps ``app.main:app`` and refuses every request that
does not carry ``X-ADG-Origin-Key: $ADG_ORIGIN_KEY``, except ``/health/live``, which the host's
health check calls and which touches nothing. Without a key it refuses to start.
"""

from __future__ import annotations

import hmac
import os
import sys
from collections.abc import Sequence

import uvicorn
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.__main__ import LOOP_FACTORY, parse_args
from app.config import get_settings

KEY_VARIABLE = "ADG_ORIGIN_KEY"
KEY_HEADER = b"x-adg-origin-key"
MINIMUM_KEY_LENGTH = 32
#: The host's health check carries no key. Liveness reads no dependency and discloses nothing.
OPEN_PATHS = frozenset({"/health/live"})


class OriginGate:
    """ASGI wrapper: no key, no answer. Lifespan events pass, so startup checks still run."""

    def __init__(self, app: ASGIApp, key: str) -> None:
        if len(key) < MINIMUM_KEY_LENGTH:
            raise ValueError(f"{KEY_VARIABLE} must be at least {MINIMUM_KEY_LENGTH} characters")
        self._app = app
        self._key = key.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan" and scope["path"] not in OPEN_PATHS:
            supplied = dict(scope["headers"]).get(KEY_HEADER, b"")
            if not hmac.compare_digest(supplied, self._key):
                if scope["type"] == "http":
                    refusal = JSONResponse(
                        {"detail": "This API answers only its staging proxy."}, status_code=403
                    )
                    await refusal(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self._app(scope, receive, send)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    key = os.environ.get(KEY_VARIABLE, "").strip()
    if len(key) < MINIMUM_KEY_LENGTH:
        sys.exit(
            f"{KEY_VARIABLE} is not set (at least {MINIMUM_KEY_LENGTH} characters); not serving."
        )
    # Imported only once the key is known, so a missing key never builds the application.
    from app.main import app

    uvicorn.run(
        OriginGate(app, key),
        host=args.host,
        port=args.port,
        loop=LOOP_FACTORY,
        log_config=None,
        access_log=False,
        log_level=get_settings().log_level.lower(),
    )


if __name__ == "__main__":
    main()
