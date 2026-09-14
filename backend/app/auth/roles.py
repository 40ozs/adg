"""Application roles and the capabilities they grant.

This module is the whole of ADG's authorization model and it is deliberately boring: a
frozen table from role to capability, and a pure function that folds a set of roles into a
set of capabilities. No database, no request, no framework. Every route and every test
reads the same table, so "what may an auditor do?" has exactly one answer in the codebase.

Two rules are structural rather than conventional:

* **A role ADG does not know grants nothing.** Tokens from an identity provider carry
  whatever the tenant put in them. An unrecognized value is dropped, never guessed at.
* **A reserved role grants nothing, even to itself.** ``REMEDIATOR`` exists so that a
  tenant can provision the app role ahead of the feature, and so the name cannot be
  reused for something else. Until remediation ships, holding it must be indistinguishable
  from not holding it — see :data:`RESERVED_ROLES`.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ACTIVE_ROLES",
    "RESERVED_CAPABILITIES",
    "RESERVED_ROLES",
    "ROLE_CAPABILITIES",
    "Capability",
    "Role",
    "capabilities_for",
    "parse_roles",
]


class Role(StrEnum):
    """An application role. The value is the string an identity provider sends."""

    VIEWER = "viewer"
    AUDITOR = "auditor"
    ADMIN = "admin"
    #: Reserved for the future remediation capability. Grants nothing today.
    REMEDIATOR = "remediator"


class Capability(StrEnum):
    """One thing a request may be permitted to do.

    Routes depend on capabilities, never on roles: a capability says what the endpoint
    needs, and the role table says who has it. Adding a role then cannot silently change
    what an endpoint requires.
    """

    RESOURCES_READ = "resources:read"
    IDENTITIES_READ = "identities:read"
    ACCESS_READ = "access:read"
    RISKS_READ = "risks:read"
    CHANGES_READ = "changes:read"
    COLLECTORS_READ = "collectors:read"
    COLLECTORS_INGEST = "collectors:ingest"
    SEARCH = "search"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"
    #: Reserved alongside :attr:`Role.REMEDIATOR`. No role grants it.
    REMEDIATION_EXECUTE = "remediation:execute"


#: Roles that exist but are not yet active. They are recognized on a token — so that the
#: principal can be told the role is inactive rather than silently ignored — and they
#: contribute no capabilities.
RESERVED_ROLES: frozenset[Role] = frozenset({Role.REMEDIATOR})

#: Capabilities no active role grants. Present so that a route can be written against the
#: eventual capability and be provably unreachable until the role is activated.
RESERVED_CAPABILITIES: frozenset[Capability] = frozenset({Capability.REMEDIATION_EXECUTE})

ACTIVE_ROLES: frozenset[Role] = frozenset(Role) - RESERVED_ROLES

_VIEWER: frozenset[Capability] = frozenset(
    {
        Capability.RESOURCES_READ,
        Capability.IDENTITIES_READ,
        Capability.ACCESS_READ,
        Capability.RISKS_READ,
        Capability.CHANGES_READ,
        # A viewer must be able to see whether collection succeeded. Otherwise an empty
        # page is indistinguishable from a failed scan, which is the single most
        # dangerous misreading this product can produce.
        Capability.COLLECTORS_READ,
        Capability.SEARCH,
    }
)

_AUDITOR: frozenset[Capability] = _VIEWER | {Capability.SETTINGS_READ}

_ADMIN: frozenset[Capability] = _AUDITOR | {
    Capability.SETTINGS_WRITE,
    # Ingestion is an administrative act. Collectors normally authenticate with their own
    # key (see app.auth.dependencies), but an operator replaying a payload by hand needs a
    # human identity that can do it.
    Capability.COLLECTORS_INGEST,
}

#: The authorization model, in one place.
ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.VIEWER: _VIEWER,
    Role.AUDITOR: _AUDITOR,
    Role.ADMIN: _ADMIN,
    Role.REMEDIATOR: frozenset(),
}


def capabilities_for(roles: frozenset[Role]) -> frozenset[Capability]:
    """Every capability the given roles grant together.

    Reserved roles are accepted and contribute nothing, so a principal holding only
    reserved roles is authenticated with no authority at all.
    """
    granted: frozenset[Capability] = frozenset()
    for role in roles:
        if role in RESERVED_ROLES:
            continue
        granted |= ROLE_CAPABILITIES[role]
    return granted


def parse_roles(values: list[str]) -> tuple[frozenset[Role], tuple[str, ...]]:
    """Split raw role strings into the roles ADG knows and the ones it does not.

    Matching is case-insensitive and whitespace-tolerant because tenant configuration is
    typed by hand. Unknown values are returned rather than discarded so that the caller can
    log them: an administrator who assigns ``Auditors`` instead of ``auditor`` deserves to
    find out from a log line, not from a user reporting a blank screen.
    """
    known: set[Role] = set()
    unknown: list[str] = []
    for raw in values:
        candidate = raw.strip().casefold()
        if not candidate:
            continue
        try:
            known.add(Role(candidate))
        except ValueError:
            unknown.append(raw.strip())
    return frozenset(known), tuple(dict.fromkeys(unknown))
