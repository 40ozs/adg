"""The authorization boundary, expressed as FastAPI dependencies.

Every route that speaks about collected facts declares a capability. There is no route
whose protection depends on a caller not knowing about it, and no route protected only by
the frontend not linking to it — the frontend hides nothing that this module would let
through.

Three entry points:

* :data:`CurrentPrincipal` — authenticated, no particular authority. Used by ``/auth/me``.
* :func:`requires` — authenticated *and* holding a capability. Used by every data route.
* :data:`IngestPrincipal` — a valid collector key, or a bearer token with
  ``collectors:ingest``. Used by the four ingestion routes.

Authentication failures are 401 with ``WWW-Authenticate: Bearer``; authorization failures
are 403. The distinction is not cosmetic: a 401 tells a client to obtain a new token, and
answering an authorization failure with 401 would send it into a refresh loop that can
never succeed.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from anyio import to_thread
from fastapi import Depends, Header, HTTPException, Request, status

from app.auth.principal import (
    AuthenticatedPrincipal,
    ClaimsMapping,
    collector_principal,
    principal_from_claims,
)
from app.auth.roles import Capability
from app.auth.tokens import TokenError, TokenVerifier, build_verifier
from app.config import Settings

__all__ = [
    "COLLECTOR_KEY_HEADER",
    "CurrentPrincipal",
    "IngestPrincipal",
    "OptionalPrincipal",
    "authenticated_principal",
    "claims_mapping_for",
    "install_auth",
    "optional_principal",
    "requires",
]

logger = logging.getLogger("adg.auth")

COLLECTOR_KEY_HEADER = "X-ADG-Collector-Key"

_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def claims_mapping_for(settings: Settings) -> ClaimsMapping:
    """Where this deployment's tokens carry authorization."""
    if settings.auth_mode == "oidc":
        return ClaimsMapping(
            roles_claim=settings.oidc_roles_claim,
            groups_claim=settings.oidc_groups_claim,
            group_roles=settings.group_role_map,
        )
    # Development tokens are minted by this process and always carry 'roles'.
    return ClaimsMapping()


def install_auth(app: Any, settings: Settings) -> None:
    """Attach the verifier and claims mapping to the application.

    Built once per application rather than per request: the OIDC verifier holds the JWKS
    cache, and rebuilding it per request would fetch the tenant's signing keys on every
    call.
    """
    app.state.token_verifier = build_verifier(settings)
    app.state.claims_mapping = claims_mapping_for(settings)


def _verifier(request: Request) -> TokenVerifier:
    # Starlette's `State` is untyped by design, so the annotation is asserted here rather
    # than inherited. Failing closed with a 500 is deliberate: an application built without
    # a verifier must refuse every request, never serve them unauthenticated.
    verifier: TokenVerifier | None = getattr(request.app.state, "token_verifier", None)
    if verifier is None:  # pragma: no cover - create_app always installs one
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is not configured on this application instance.",
        )
    return verifier


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.strip().casefold() != "bearer" or not value.strip():
        return None
    return value.strip()


async def optional_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedPrincipal | None:
    """The caller's principal, or ``None`` when no bearer token was presented.

    A *malformed or invalid* token still raises 401. Only the complete absence of one
    returns ``None``, so a client cannot downgrade itself to anonymous by corrupting its
    token and receive a public answer instead of an error.
    """
    token = _bearer_token(authorization)
    if token is None:
        return None

    verifier = _verifier(request)
    try:
        # PyJWKClient fetches over the network on a cache miss; running it on the event
        # loop would stall every other request behind one tenant round trip.
        claims = await to_thread.run_sync(verifier.verify, token)
    except TokenError as exc:
        logger.info(
            "auth.token.rejected",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "source": verifier.source,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers=_BEARER_CHALLENGE,
        ) from exc

    mapping: ClaimsMapping = request.app.state.claims_mapping
    try:
        principal = principal_from_claims(claims, mapping=mapping, source=verifier.source)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers=_BEARER_CHALLENGE,
        ) from exc

    if principal.unknown_roles:
        # Worth a log line every time: this is the shape of "the administrator assigned
        # something and the user still sees nothing".
        logger.warning(
            "auth.roles.unrecognized",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "subject": principal.subject,
                "unknown_roles": list(principal.unknown_roles),
            },
        )
    return principal


async def authenticated_principal(
    principal: Annotated[AuthenticatedPrincipal | None, Depends(optional_principal)],
) -> AuthenticatedPrincipal:
    """The caller's principal. 401 when there is none."""
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "This endpoint requires authentication. Present an access token as "
                "'Authorization: Bearer <token>'."
            ),
            headers=_BEARER_CHALLENGE,
        )
    return principal


OptionalPrincipal = Annotated[AuthenticatedPrincipal | None, Depends(optional_principal)]
CurrentPrincipal = Annotated[AuthenticatedPrincipal, Depends(authenticated_principal)]


def requires(
    capability: Capability,
) -> Callable[..., Coroutine[Any, Any, AuthenticatedPrincipal]]:
    """A dependency that admits only principals holding ``capability``.

    Returned as a dependency rather than checked inside each handler so that the
    requirement appears in the OpenAPI document and can be asserted structurally: a route
    added without one is visible as a route with no security dependency, not as a route
    whose body somebody forgot to guard.
    """

    async def dependency(request: Request, principal: CurrentPrincipal) -> AuthenticatedPrincipal:
        if principal.can(capability):
            return principal
        logger.info(
            "auth.denied",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "subject": principal.subject,
                "capability": capability.value,
                "roles": sorted(role.value for role in principal.roles),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_denial_message(principal, capability),
        )

    dependency.__name__ = f"requires_{capability.name.lower()}"
    return dependency


def _denial_message(principal: AuthenticatedPrincipal, capability: Capability) -> str:
    if not principal.is_authorized_for_anything:
        if principal.reserved_roles:
            reserved = ", ".join(sorted(role.value for role in principal.reserved_roles))
            return (
                f"This account holds only reserved role(s) ({reserved}), which grant no "
                "access yet. Assign the viewer, auditor, or admin role."
            )
        return (
            "This account is authenticated but has no ADG role assigned, so it can see "
            "nothing. Assign the viewer, auditor, or admin role."
        )
    held = ", ".join(sorted(role.value for role in principal.roles)) or "none"
    return (
        f"This request requires the '{capability.value}' capability, which none of this "
        f"account's roles ({held}) grant."
    )


async def ingest_principal(
    request: Request,
    principal: Annotated[AuthenticatedPrincipal | None, Depends(optional_principal)],
    collector_key: Annotated[str | None, Header(alias=COLLECTOR_KEY_HEADER)] = None,
) -> AuthenticatedPrincipal:
    """The identity behind an ingestion request: a collector key, or an administrator.

    A collector key is checked first because it is the normal path — a scheduled task on a
    file server has no interactive user to borrow a token from. The comparison is
    constant-time, and a presented-but-wrong key is rejected outright rather than falling
    through to the bearer check: falling through would turn a key typo into a confusing
    401 about a missing token.
    """
    settings: Settings = request.app.state.settings
    configured = settings.collector_key_map

    if collector_key is not None:
        matched = _match_collector_key(configured, collector_key)
        if matched is None:
            logger.warning(
                "auth.collector_key.rejected",
                extra={"request_id": getattr(request.state, "request_id", None)},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    f"The {COLLECTOR_KEY_HEADER} presented is not a configured collector "
                    "key. Check ADG_COLLECTOR_API_KEYS on the server."
                ),
            )
        return collector_principal(matched)

    if principal is None:
        detail = (
            f"Ingestion requires either a collector key in {COLLECTOR_KEY_HEADER} or an "
            "access token for an account with the 'collectors:ingest' capability."
        )
        if not configured:
            detail += (
                " No collector keys are configured on this server "
                "(ADG_COLLECTOR_API_KEYS is empty)."
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers=_BEARER_CHALLENGE
        )

    if not principal.can(Capability.COLLECTORS_INGEST):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_denial_message(principal, Capability.COLLECTORS_INGEST),
        )
    return principal


IngestPrincipal = Annotated[AuthenticatedPrincipal, Depends(ingest_principal)]


def _match_collector_key(configured: dict[str, str], presented: str) -> str | None:
    """The key id whose secret equals ``presented``, compared in constant time.

    Every configured key is compared even after a match, so the time taken does not reveal
    the position of the matching key in the list.
    """
    matched: str | None = None
    for key_id, secret in configured.items():
        if hmac.compare_digest(secret, presented):
            matched = key_id
    return matched
