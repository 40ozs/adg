"""Minting credentials for tests.

Every suite that talks to the API now has to present one, which is the point: a test client
that reached a data endpoint without a token would be proving something about a build of
the application nobody deploys.

Tokens are minted the same way the running application mints them — through
:func:`app.auth.tokens.issue_development_token`, signed with the settings object under
test — rather than by overriding the dependency. Overriding it would make every suite pass
against an application whose authentication had been removed.
"""

from __future__ import annotations

import datetime as dt

from app.auth.dev_users import DevelopmentUser
from app.auth.roles import Role
from app.auth.tokens import issue_development_token
from app.config import Settings

__all__ = ["auth_headers", "development_token", "token_for_roles"]

DEFAULT_LIFETIME = dt.timedelta(minutes=30)


def development_token(settings: Settings, username: str = "admin") -> str:
    """A token for one of the configured development accounts."""
    user = next(
        (item for item in settings.development_users if item.username == username),
        None,
    )
    if user is None:
        available = ", ".join(item.username for item in settings.development_users)
        raise LookupError(f"No development account {username!r}. Configured: {available}.")
    return _sign(settings, user)


def token_for_roles(settings: Settings, *roles: Role, username: str = "custom") -> str:
    """A token carrying exactly the roles named, including none at all.

    ``token_for_roles(settings)`` produces an authenticated principal with no authority,
    which is what proves that authentication and authorization are separate gates.
    """
    return _sign(
        settings,
        DevelopmentUser(
            username=username,
            roles=frozenset(roles),
            display_name=f"Test {username}",
        ),
    )


def auth_headers(settings: Settings, username: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {development_token(settings, username)}"}


def _sign(settings: Settings, user: DevelopmentUser) -> str:
    token, _ = issue_development_token(
        secret=settings.resolved_dev_auth_secret,
        subject=f"dev:{user.username}",
        roles=sorted(role.value for role in user.roles),
        display_name=user.display_name,
        email=user.email,
        lifetime=DEFAULT_LIFETIME,
    )
    return token
