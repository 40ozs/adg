"""Bearer-token verification for the two supported authentication modes.

Verification is where a mistake is fatal and invisible, so the two modes share no code
paths and no defaults:

* :class:`OidcTokenVerifier` accepts only asymmetric signatures, only from the configured
  issuer, only for the configured audience, and only with a key it fetched from the
  tenant's JWKS endpoint.
* :class:`DevelopmentTokenVerifier` accepts only HS256 tokens this process itself issued,
  under a distinctive issuer that no real tenant can produce.

The algorithm allow-lists are explicit in both. A verifier that accepts ``alg`` from the
token is the classic JWT vulnerability, and passing ``algorithms=`` is what forecloses it.

Neither verifier is ever selected by anything but :func:`build_verifier`, which reads the
mode from settings — and :class:`app.config.Settings` refuses to start a production process
in development mode. There is no code path in which a development token is accepted by a
production deployment.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from app.auth.principal import AuthSource

__all__ = [
    "DEVELOPMENT_AUDIENCE",
    "DEVELOPMENT_ISSUER",
    "DevelopmentTokenVerifier",
    "OidcTokenVerifier",
    "TokenError",
    "TokenVerifier",
    "build_verifier",
    "issue_development_token",
]

logger = logging.getLogger("adg.auth")

#: Deliberately not a URL. A development token cannot be mistaken for a tenant's token by
#: anything reading logs, and cannot collide with a real issuer value.
DEVELOPMENT_ISSUER = "adg-development-only"
DEVELOPMENT_AUDIENCE = "adg-api"

_OIDC_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384"]
_DEVELOPMENT_ALGORITHM = "HS256"

#: Tolerance for clock skew between ADG and the identity provider.
_LEEWAY_SECONDS = 60


class TokenError(Exception):
    """A bearer token could not be verified.

    The message is written for the operator reading the API log and is safe to return to
    the caller: it names the failure class and never echoes the token or its claims.
    """


class TokenVerifier(Protocol):
    """Verifies a bearer token and returns its claims."""

    source: AuthSource

    def verify(self, token: str) -> Mapping[str, Any]:
        """Return verified claims, or raise :class:`TokenError`."""


class OidcTokenVerifier:
    """Verifies tokens issued by an OIDC provider (Microsoft Entra ID in production).

    Signing keys come from the tenant's JWKS endpoint and are cached by
    :class:`jwt.PyJWKClient` across requests. The fetch is blocking, which is why callers
    run :meth:`verify` in a worker thread rather than on the event loop.
    """

    source: AuthSource = "oidc"

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        jwks_cache_seconds: int = 600,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks = PyJWKClient(jwks_url, cache_keys=True, lifespan=jwks_cache_seconds)

    def verify(self, token: str) -> Mapping[str, Any]:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientError as exc:
            # A key ADG cannot fetch is an availability problem, not a bad token, and the
            # distinction matters when every request starts failing at once.
            raise TokenError(
                "The signing key for this token could not be retrieved from the identity "
                f"provider's JWKS endpoint: {exc}"
            ) from exc
        except jwt.exceptions.InvalidTokenError as exc:
            raise TokenError(f"This is not a well-formed JWT: {exc}") from exc

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=_OIDC_ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("This token has expired; sign in again.") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenError(
                "This token was issued for a different audience. Check that the client "
                "requests a token for ADG's application ID URI."
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenError(
                "This token was issued by a different issuer than ADG_OIDC_ISSUER."
            ) from exc
        except jwt.exceptions.InvalidTokenError as exc:
            raise TokenError(f"This token could not be verified: {exc}") from exc

        return claims


class DevelopmentTokenVerifier:
    """Verifies the short-lived tokens the development login endpoint issues.

    Symmetric, because this verifier and the issuer are the same process. That is exactly
    why it must never run in production, and :class:`app.config.Settings` enforces it.
    """

    source: AuthSource = "development"

    def __init__(self, *, secret: str) -> None:
        if not secret:
            raise ValueError("A development token secret is required.")
        self._secret = secret

    def verify(self, token: str) -> Mapping[str, Any]:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_DEVELOPMENT_ALGORITHM],
                audience=DEVELOPMENT_AUDIENCE,
                issuer=DEVELOPMENT_ISSUER,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError(
                "This development session has expired; sign in again at /login."
            ) from exc
        except jwt.exceptions.InvalidTokenError as exc:
            raise TokenError(f"This development token could not be verified: {exc}") from exc
        return claims


def issue_development_token(
    *,
    secret: str,
    subject: str,
    roles: list[str],
    display_name: str,
    email: str | None,
    lifetime: dt.timedelta,
    now: dt.datetime | None = None,
) -> tuple[str, dt.datetime]:
    """Sign a development token. Returns the token and the instant it expires."""
    issued_at = now or dt.datetime.now(tz=dt.UTC)
    expires_at = issued_at + lifetime
    payload: dict[str, Any] = {
        "iss": DEVELOPMENT_ISSUER,
        "aud": DEVELOPMENT_AUDIENCE,
        "sub": subject,
        "name": display_name,
        "roles": roles,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, secret, algorithm=_DEVELOPMENT_ALGORITHM), expires_at


def build_verifier(settings: Any) -> TokenVerifier:
    """The verifier for the configured mode.

    Typed loosely against ``Settings`` to keep :mod:`app.config` free of a dependency on
    the auth package; the attributes read here are validated there.
    """
    if settings.auth_mode == "oidc":
        logger.info(
            "auth.mode.oidc",
            extra={"issuer": settings.oidc_issuer, "audience": settings.oidc_audience},
        )
        return OidcTokenVerifier(
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            jwks_url=settings.oidc_jwks_url,
        )
    # Loud, and at WARNING: an operator who lands here in a shared environment has
    # misconfigured something that matters.
    logger.warning(
        "auth.mode.development",
        extra={"environment": settings.environment},
    )
    return DevelopmentTokenVerifier(secret=settings.resolved_dev_auth_secret)
