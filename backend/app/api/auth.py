"""Authentication endpoints: what mode this deployment runs, who you are, and — in
development only — how to become somebody.

``/auth/config`` is unauthenticated by necessity: a client has to know how to sign in
before it can. It exposes only what a public client already needs to start an OIDC
authorization-code flow (issuer, client id, endpoints, scopes), never a secret.

``/auth/dev/login`` is **not registered at all** outside development mode. Not registered,
rather than registered and refusing: a route that returns 403 still says "this deployment
knows how to issue its own tokens", and the OpenAPI document of a production deployment
should contain no such thing.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentPrincipal
from app.auth.dev_users import DevelopmentUser
from app.auth.principal import AuthenticatedPrincipal
from app.auth.roles import RESERVED_ROLES, Capability, Role, capabilities_for
from app.auth.tokens import issue_development_token
from app.config import Settings

logger = logging.getLogger("adg.api.auth")

#: Routes every deployment has. The development login route is added per application by
#: :func:`build_auth_router`, which is why this module exposes a builder rather than one
#: shared router object: ``create_app`` runs many times in a test session, and mutating a
#: module-level router would accumulate duplicate routes across applications.
base_router = APIRouter(prefix="/auth", tags=["auth"])


class RoleDescription(BaseModel):
    """One role and what it can do, so the UI never has to hard-code the table."""

    role: str
    active: bool = Field(
        description="False for a role that is reserved for a future capability and grants nothing."
    )
    capabilities: list[str]


class AuthConfigResponse(BaseModel):
    """Everything a client needs to start signing in. No secrets."""

    mode: Literal["oidc", "development"]
    development: bool = Field(
        description=(
            "True when this deployment issues its own tokens and verifies no credential. "
            "Clients must display this prominently; it cannot be true in production."
        )
    )
    environment: str
    issuer: str | None = None
    client_id: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    scopes: list[str] = Field(default_factory=list)
    development_accounts: list[str] = Field(
        default_factory=list,
        description="Selectable account names, in development mode only. Empty otherwise.",
    )
    roles: list[RoleDescription]


class DevelopmentLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)


class PrincipalView(BaseModel):
    """The caller, as the API sees them."""

    subject: str
    display_name: str | None
    email: str | None
    source: Literal["oidc", "development", "collector_key"]
    development: bool
    roles: list[str]
    capabilities: list[str] = Field(
        description=(
            "Exactly what this principal may do. The frontend reads this to decide what to "
            "show; the backend does not trust that decision."
        )
    )
    inactive_roles: list[str] = Field(
        default_factory=list,
        description="Reserved roles this account holds that grant nothing yet.",
    )
    unrecognized_roles: list[str] = Field(
        default_factory=list,
        description=(
            "Role or group values in the token that ADG does not know. Shown so an "
            "administrator can see a misconfigured assignment instead of guessing."
        ),
    )
    expires_at: dt.datetime | None = None


class DevelopmentLoginResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: dt.datetime
    principal: PrincipalView
    warning: str = Field(
        default=(
            "Development authentication: this token was issued without verifying any "
            "credential and is valid only on this process."
        )
    )


@base_router.get(
    "/config",
    response_model=AuthConfigResponse,
    summary="How to sign in to this deployment",
)
async def auth_config(request: Request) -> AuthConfigResponse:
    settings: Settings = request.app.state.settings
    development = settings.is_development_auth
    return AuthConfigResponse(
        mode=settings.auth_mode,
        development=development,
        environment=settings.environment,
        issuer=settings.oidc_issuer or None,
        client_id=settings.oidc_client_id or None,
        authorization_endpoint=settings.oidc_authorization_endpoint or None,
        token_endpoint=settings.oidc_token_endpoint or None,
        scopes=settings.oidc_scope_list,
        development_accounts=(
            [user.username for user in settings.development_users] if development else []
        ),
        roles=[
            RoleDescription(
                role=role.value,
                active=role not in RESERVED_ROLES,
                capabilities=sorted(
                    capability.value for capability in capabilities_for(frozenset({role}))
                ),
            )
            for role in sorted(Role, key=lambda item: item.value)
        ],
    )


@base_router.get("/me", response_model=PrincipalView, summary="The authenticated caller")
async def me(principal: CurrentPrincipal) -> PrincipalView:
    return _principal_view(principal)


async def development_login(
    payload: DevelopmentLoginRequest, request: Request
) -> DevelopmentLoginResponse:
    """Issue a token for a configured development account.

    Registered only when ``ADG_AUTH_MODE=development``; see :func:`build_auth_router`.
    """
    settings: Settings = request.app.state.settings
    requested = payload.username.strip().casefold()
    user = next(
        (item for item in settings.development_users if item.username == requested),
        None,
    )
    if user is None:
        available = ", ".join(item.username for item in settings.development_users)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No development account named {payload.username!r}. "
                f"Configured accounts: {available}."
            ),
        )

    lifetime = dt.timedelta(minutes=settings.dev_auth_token_lifetime_minutes)
    token, expires_at = issue_development_token(
        secret=settings.resolved_dev_auth_secret,
        subject=f"dev:{user.username}",
        roles=sorted(role.value for role in user.roles),
        display_name=user.display_name,
        email=user.email,
        lifetime=lifetime,
    )
    logger.warning(
        "auth.development_login",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "subject": f"dev:{user.username}",
            "roles": sorted(role.value for role in user.roles),
        },
    )
    return DevelopmentLoginResponse(
        access_token=token,
        expires_at=expires_at,
        principal=_development_principal_view(user, expires_at),
    )


def build_auth_router(settings: Settings) -> APIRouter:
    """The auth routes this deployment should have.

    The development login route exists only in development mode, so a production
    deployment's OpenAPI document cannot advertise a way to mint identities.
    """
    router = APIRouter()
    router.include_router(base_router)
    if settings.is_development_auth:
        router.add_api_route(
            "/auth/dev/login",
            development_login,
            methods=["POST"],
            response_model=DevelopmentLoginResponse,
            status_code=status.HTTP_200_OK,
            summary="DEVELOPMENT ONLY: sign in as a configured account without a credential",
            tags=["auth"],
            responses={404: {"description": "No such development account."}},
        )
    return router


def _principal_view(principal: AuthenticatedPrincipal) -> PrincipalView:
    return PrincipalView(
        subject=principal.subject,
        display_name=principal.display_name,
        email=principal.email,
        source=principal.source,
        development=principal.development,
        roles=sorted(role.value for role in principal.roles),
        capabilities=sorted(capability.value for capability in principal.granted),
        inactive_roles=sorted(role.value for role in principal.reserved_roles),
        unrecognized_roles=list(principal.unknown_roles),
        expires_at=principal.expires_at,
    )


def _development_principal_view(user: DevelopmentUser, expires_at: dt.datetime) -> PrincipalView:
    granted: frozenset[Capability] = capabilities_for(user.roles)
    return PrincipalView(
        subject=f"dev:{user.username}",
        display_name=user.display_name,
        email=user.email,
        source="development",
        development=True,
        roles=sorted(role.value for role in user.roles),
        capabilities=sorted(capability.value for capability in granted),
        inactive_roles=sorted(role.value for role in user.roles & RESERVED_ROLES),
        expires_at=expires_at,
    )


__all__ = ["base_router", "build_auth_router"]
