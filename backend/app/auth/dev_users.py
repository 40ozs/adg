"""The fixed account list behind development-only authentication.

Development mode exists so that the application can be driven end to end without an Entra
tenant. It is not a password system and deliberately does not pretend to be one: there is
no credential to check, only a named account from a configured list. That is safe precisely
because the mode cannot run outside development — see
:meth:`app.config.Settings._reject_development_auth_in_production` — and it is honest,
because a fake password prompt would invite somebody to believe the boundary is real.

Configured through ``ADG_DEV_AUTH_USERS`` as comma-separated entries::

    username:role|role:Display Name

The display name is optional. Roles are separated by ``|`` so that a comma keeps its one
meaning of "next account".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.roles import Role, parse_roles

__all__ = ["DEFAULT_DEV_AUTH_USERS", "DevelopmentUser", "parse_development_users"]

#: One account per active role, so a developer can see each role's view of the product
#: without editing configuration first.
DEFAULT_DEV_AUTH_USERS = (
    "viewer:viewer:Development Viewer,"
    "auditor:auditor:Development Auditor,"
    "admin:admin:Development Administrator"
)


@dataclass(frozen=True, slots=True)
class DevelopmentUser:
    username: str
    roles: frozenset[Role]
    display_name: str

    @property
    def email(self) -> str:
        """A non-routable address, so nothing can accidentally mail a development account."""
        return f"{self.username}@development.invalid"


class DevelopmentUserError(ValueError):
    """``ADG_DEV_AUTH_USERS`` could not be parsed. The message names the offending entry."""


def parse_development_users(raw: str) -> tuple[DevelopmentUser, ...]:
    """Parse the configured account list, rejecting anything ambiguous.

    Every failure names the entry and what was expected. A silently dropped account would
    show up much later as "I can't sign in as auditor", with nothing in the logs.
    """
    users: list[DevelopmentUser] = []
    seen: set[str] = set()

    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        parts = candidate.split(":")
        if len(parts) < 2 or len(parts) > 3:
            raise DevelopmentUserError(
                f"ADG_DEV_AUTH_USERS entry {candidate!r} is not "
                "'username:role|role' or 'username:role|role:Display Name'."
            )

        username = parts[0].strip().casefold()
        if not username:
            raise DevelopmentUserError(
                f"ADG_DEV_AUTH_USERS entry {candidate!r} has an empty user name."
            )
        if username in seen:
            raise DevelopmentUserError(
                f"ADG_DEV_AUTH_USERS names {username!r} more than once; one entry per account."
            )

        roles, unknown = parse_roles(parts[1].split("|"))
        if unknown:
            raise DevelopmentUserError(
                f"ADG_DEV_AUTH_USERS entry {candidate!r} names unknown role(s) "
                f"{', '.join(repr(value) for value in unknown)}. "
                f"Valid roles: {', '.join(sorted(role.value for role in Role))}."
            )
        if not roles:
            raise DevelopmentUserError(
                f"ADG_DEV_AUTH_USERS entry {candidate!r} grants no role. "
                "Give the account at least one role, or remove the entry."
            )

        display = parts[2].strip() if len(parts) == 3 and parts[2].strip() else username
        seen.add(username)
        users.append(DevelopmentUser(username=username, roles=roles, display_name=display))

    if not users:
        raise DevelopmentUserError(
            "ADG_DEV_AUTH_USERS is empty, so no account could sign in. Set it, or unset it "
            f"to use the default list: {DEFAULT_DEV_AUTH_USERS}"
        )
    return tuple(users)
