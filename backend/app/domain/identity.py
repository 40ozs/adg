"""Identity: SIDs, domains, and principals.

**The SID is the identity.** Display names, sAMAccountNames, UPNs, and distinguished names
are mutable metadata: a user can be renamed, moved between organizational units, or have a
UPN reassigned to somebody else, and none of that changes what an ACL grants. Only the SID
survives those events, so only the SID may be a key or a join column.

Nothing here resolves access. These types describe *what was observed*, precisely enough
that the Phase 4 engine can compute effective access from them without guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.domain.errors import DomainValidationError

# Structure of a string-form SID: S-R-IA-SA-SA-...
#   R  revision, always 1 on Windows
#   IA identifier authority: decimal, or 0x-prefixed 12-digit hex when it exceeds 2^32
#   SA up to 15 sub-authorities, each an unsigned 32-bit integer
_SID_RE: Final = re.compile(
    r"""^S
        -(?P<revision>\d{1,10})
        -(?P<authority>0[xX][0-9a-fA-F]{1,12}|\d{1,20})
        (?P<subs>(?:-\d{1,10})*)$""",
    re.VERBOSE,
)

MAX_SUB_AUTHORITIES: Final = 15
_MAX_UINT32: Final = 2**32 - 1
_MAX_AUTHORITY: Final = 2**48 - 1
SID_REVISION: Final = 1

# Authority 5 ("NT Authority") with first sub-authority 21 introduces a machine or domain
# SID; the following three sub-authorities identify it, and any further one is a RID.
_NT_AUTHORITY: Final = 5
_DOMAIN_IDENTIFIER: Final = 21
_DOMAIN_SUB_AUTHORITY_COUNT: Final = 4  # 21, x, y, z

# BUILTIN\... groups. These SIDs are identical on every Windows computer, so a BUILTIN SID
# alone does not identify a principal: it is always scoped to the machine that reported it.
_BUILTIN_PREFIX: Final = "S-1-5-32-"

WELL_KNOWN_SID_NAMES: Final[dict[str, str]] = {
    "S-1-0-0": "NULL SID",
    "S-1-1-0": "Everyone",
    "S-1-2-0": "LOCAL",
    "S-1-2-1": "CONSOLE LOGON",
    "S-1-3-0": "CREATOR OWNER",
    "S-1-3-1": "CREATOR GROUP",
    "S-1-3-4": "OWNER RIGHTS",
    "S-1-5-1": "DIALUP",
    "S-1-5-2": "NETWORK",
    "S-1-5-3": "BATCH",
    "S-1-5-4": "INTERACTIVE",
    "S-1-5-6": "SERVICE",
    "S-1-5-7": "ANONYMOUS LOGON",
    "S-1-5-9": "ENTERPRISE DOMAIN CONTROLLERS",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-13": "TERMINAL SERVER USER",
    "S-1-5-15": "This Organization",
    "S-1-5-18": "SYSTEM",
    "S-1-5-19": "LOCAL SERVICE",
    "S-1-5-20": "NETWORK SERVICE",
    "S-1-5-32-544": "BUILTIN\\Administrators",
    "S-1-5-32-545": "BUILTIN\\Users",
    "S-1-5-32-546": "BUILTIN\\Guests",
    "S-1-5-32-547": "BUILTIN\\Power Users",
    "S-1-5-32-551": "BUILTIN\\Backup Operators",
    "S-1-5-32-555": "BUILTIN\\Remote Desktop Users",
    "S-1-5-32-559": "BUILTIN\\Performance Log Users",
    "S-1-5-32-562": "BUILTIN\\Distributed COM Users",
    "S-1-5-33": "WRITE RESTRICTED",
    "S-1-5-113": "Local account",
    "S-1-5-114": "Local account and member of Administrators group",
    "S-1-15-2-1": "ALL APPLICATION PACKAGES",
}

# Domain-relative RIDs worth naming. The name is still metadata; the (domain SID, RID) pair
# is what identifies the principal.
WELL_KNOWN_RID_NAMES: Final[dict[int, str]] = {
    500: "Administrator",
    501: "Guest",
    502: "krbtgt",
    512: "Domain Admins",
    513: "Domain Users",
    514: "Domain Guests",
    515: "Domain Computers",
    516: "Domain Controllers",
    517: "Cert Publishers",
    518: "Schema Admins",
    519: "Enterprise Admins",
    520: "Group Policy Creator Owners",
    525: "Protected Users",
    526: "Key Admins",
    527: "Enterprise Key Admins",
    553: "RAS and IAS Servers",
}


@dataclass(frozen=True, slots=True, order=True)
class Sid:
    """A canonical Windows security identifier.

    Construct with :meth:`parse` (or the constructor, which validates identically). Two
    SIDs are equal when their canonical string forms are equal; ``value`` is the storage
    key for every table and every API that refers to a principal.

    Canonical form rules — applied so that the same SID observed through different Windows
    APIs collapses to one key:

    * the literal ``S`` is upper case;
    * decimal components carry no leading zeros (``S-1-05-21`` → ``S-1-5-21``);
    * a hexadecimal identifier authority keeps a lower-case ``0x`` prefix with upper-case
      digits, and is rewritten as decimal when it fits in 32 bits, which is how Windows
      renders it;
    * surrounding whitespace is removed.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _canonicalize_sid(self.value))

    @classmethod
    def parse(cls, value: str) -> Sid:
        """Parse and canonicalize a string-form SID.

        Raises:
            DomainValidationError: if ``value`` is not a syntactically valid SID.
        """
        return cls(value)

    @classmethod
    def try_parse(cls, value: str) -> Sid | None:
        """Return the SID, or ``None`` when ``value`` is not a SID.

        Useful where a collector reports a field that is *either* a SID or a name.
        """
        try:
            return cls(value)
        except DomainValidationError:
            return None

    def __str__(self) -> str:
        return self.value

    @property
    def revision(self) -> int:
        return int(self.value.split("-")[1])

    @property
    def identifier_authority(self) -> int:
        authority = self.value.split("-")[2]
        return int(authority, 16) if authority.lower().startswith("0x") else int(authority)

    @property
    def sub_authorities(self) -> tuple[int, ...]:
        return tuple(int(part) for part in self.value.split("-")[3:])

    @property
    def rid(self) -> int | None:
        """The relative identifier: the last sub-authority, or ``None`` if there is none."""
        subs = self.sub_authorities
        return subs[-1] if subs else None

    @property
    def is_domain_relative(self) -> bool:
        """True for a SID issued by a domain or a workstation (``S-1-5-21-x-y-z-RID``)."""
        subs = self.sub_authorities
        return (
            self.identifier_authority == _NT_AUTHORITY
            and len(subs) > _DOMAIN_SUB_AUTHORITY_COUNT
            and subs[0] == _DOMAIN_IDENTIFIER
        )

    @property
    def is_domain_sid(self) -> bool:
        """True for the SID *of* a domain or machine (``S-1-5-21-x-y-z``, no RID)."""
        subs = self.sub_authorities
        return (
            self.identifier_authority == _NT_AUTHORITY
            and len(subs) == _DOMAIN_SUB_AUTHORITY_COUNT
            and subs[0] == _DOMAIN_IDENTIFIER
        )

    @property
    def domain_sid(self) -> Sid | None:
        """The issuing domain/machine SID, or ``None`` if this SID is not domain-relative.

        This is how a principal is attributed to a domain without trusting any name.
        """
        if not self.is_domain_relative:
            return None
        prefix = self.value.rsplit("-", 1)[0]
        return Sid(prefix)

    @property
    def is_builtin(self) -> bool:
        """True for ``S-1-5-32-*``.

        A BUILTIN SID is identical on every Windows computer, so it identifies a principal
        only together with the machine that reported it. See :class:`LocalGroup`.
        """
        return self.value.startswith(_BUILTIN_PREFIX)

    @property
    def is_well_known(self) -> bool:
        """True for a SID with the same meaning everywhere (``Everyone``, ``SYSTEM``, ...)."""
        return self.value in WELL_KNOWN_SID_NAMES or self.is_builtin

    @property
    def well_known_name(self) -> str | None:
        """A conventional display name for a well-known SID. Metadata, never identity."""
        return WELL_KNOWN_SID_NAMES.get(self.value)


def _canonicalize_sid(raw: str) -> str:
    if not isinstance(raw, str):  # pragma: no cover - defensive, dataclasses are untyped here
        raise DomainValidationError(f"A SID must be a string; received {type(raw).__name__}.")

    text = raw.strip()
    if not text:
        raise DomainValidationError("A SID must not be empty.", value=raw, field="sid")

    match = _SID_RE.match(text if text[:1] != "s" else "S" + text[1:])
    if match is None:
        raise DomainValidationError(
            f"{raw!r} is not a valid string-form SID. Expected S-R-IA[-SA...], "
            "for example S-1-5-21-1004336348-1177238915-682003330-512.",
            value=raw,
            field="sid",
        )

    revision = int(match.group("revision"))
    if revision != SID_REVISION:
        raise DomainValidationError(
            f"SID revision must be {SID_REVISION}; {raw!r} declares {revision}.",
            value=raw,
            field="sid",
        )

    authority_text = match.group("authority")
    authority = (
        int(authority_text, 16) if authority_text.lower().startswith("0x") else int(authority_text)
    )
    if authority > _MAX_AUTHORITY:
        raise DomainValidationError(
            f"SID identifier authority exceeds 48 bits in {raw!r}.", value=raw, field="sid"
        )

    sub_text = match.group("subs")
    sub_authorities = [int(part) for part in sub_text.split("-")[1:]] if sub_text else []
    if len(sub_authorities) > MAX_SUB_AUTHORITIES:
        raise DomainValidationError(
            f"A SID may have at most {MAX_SUB_AUTHORITIES} sub-authorities; "
            f"{raw!r} has {len(sub_authorities)}.",
            value=raw,
            field="sid",
        )
    for sub in sub_authorities:
        if sub > _MAX_UINT32:
            raise DomainValidationError(
                f"SID sub-authority {sub} exceeds 32 bits in {raw!r}.", value=raw, field="sid"
            )

    # Windows renders authorities that fit in 32 bits as decimal, and larger ones as hex.
    rendered_authority = f"0x{authority:012X}" if authority > _MAX_UINT32 else str(authority)
    parts = ["S", str(revision), rendered_authority, *(str(sub) for sub in sub_authorities)]
    return "-".join(parts)


class DomainKind(StrEnum):
    """What issued a set of SIDs."""

    ACTIVE_DIRECTORY = "active_directory"
    """An Active Directory domain."""

    LOCAL_MACHINE = "local_machine"
    """A standalone Windows computer's local account database (SAM)."""

    UNKNOWN = "unknown"
    """A domain SID observed through a trust or an ACL, not yet identified."""


@dataclass(frozen=True, slots=True)
class DomainIdentifier:
    """A domain (or machine account database) and the forest it belongs to.

    Identity is ``domain_sid``. DNS and NetBIOS names are metadata: both can be renamed,
    and NetBIOS names are not unique across forests joined by a trust.
    """

    domain_sid: Sid
    kind: DomainKind = DomainKind.ACTIVE_DIRECTORY
    dns_name: str | None = None
    netbios_name: str | None = None
    forest_dns_name: str | None = None
    forest_root_domain_sid: Sid | None = None

    def __post_init__(self) -> None:
        if not self.domain_sid.is_domain_sid:
            raise DomainValidationError(
                f"{self.domain_sid} is not a domain SID. A domain SID has the form "
                "S-1-5-21-x-y-z with no RID.",
                value=self.domain_sid.value,
                field="domain_sid",
            )
        for name_field in ("dns_name", "netbios_name", "forest_dns_name"):
            value = getattr(self, name_field)
            if value is not None and not value.strip():
                raise DomainValidationError(
                    f"{name_field} must be a non-empty name or None.", field=name_field
                )

    @property
    def identity_key(self) -> str:
        return self.domain_sid.value

    @property
    def is_forest_root(self) -> bool | None:
        """``None`` when the forest root is unknown, rather than a misleading ``False``."""
        if self.forest_root_domain_sid is None:
            return None
        return self.forest_root_domain_sid == self.domain_sid


class PrincipalKind(StrEnum):
    """What a SID refers to, as reported by the source that resolved it."""

    USER = "user"
    DOMAIN_GROUP = "domain_group"
    LOCAL_GROUP = "local_group"
    COMPUTER = "computer"
    MANAGED_SERVICE_ACCOUNT = "managed_service_account"
    WELL_KNOWN = "well_known"
    """Everyone, Authenticated Users, SYSTEM, CREATOR OWNER, and similar."""

    FOREIGN_SECURITY_PRINCIPAL = "foreign_security_principal"
    """A principal from a trusted domain/forest, represented locally by its SID."""

    UNRESOLVED = "unresolved"
    """A SID that no authority resolved. A normal, storable observation — not an error."""


class GroupScope(StrEnum):
    """Active Directory group scope. Determines where a group may be used and nested."""

    DOMAIN_LOCAL = "domain_local"
    GLOBAL = "global"
    UNIVERSAL = "universal"
    BUILTIN_LOCAL = "builtin_local"
    UNKNOWN = "unknown"


class GroupType(StrEnum):
    """Security groups appear on ACLs; distribution groups never grant access."""

    SECURITY = "security"
    DISTRIBUTION = "distribution"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Principal:
    """Base class for anything a SID can refer to.

    Every name-like attribute is optional and advisory. A principal with no name at all is
    still a complete, useful fact: it says a specific SID appeared.
    """

    sid: Sid
    kind: PrincipalKind
    display_name: str | None = None
    sam_account_name: str | None = None
    domain_sid: Sid | None = None

    def __post_init__(self) -> None:
        if self.domain_sid is not None and not self.domain_sid.is_domain_sid:
            raise DomainValidationError(
                f"{self.domain_sid} is not a domain SID.",
                value=self.domain_sid.value,
                field="domain_sid",
            )
        declared = self.domain_sid
        derived = self.sid.domain_sid
        if declared is not None and derived is not None and declared != derived:
            raise DomainValidationError(
                f"Principal {self.sid} belongs to domain {derived} by its SID, but was "
                f"recorded under domain {declared}. Names may be ambiguous; SIDs are not.",
                value=self.sid.value,
                field="domain_sid",
            )

    @property
    def identity_key(self) -> str:
        """The storage key for this principal.

        SID alone, because a domain-issued SID is globally unique. :class:`LocalGroup`
        overrides this: BUILTIN SIDs repeat on every machine.
        """
        return self.sid.value

    @property
    def effective_domain_sid(self) -> Sid | None:
        """The domain this principal belongs to: recorded if known, else derived."""
        return self.domain_sid or self.sid.domain_sid


@dataclass(frozen=True, slots=True)
class User(Principal):
    """A human or service user account."""

    kind: PrincipalKind = PrincipalKind.USER
    user_principal_name: str | None = None
    distinguished_name: str | None = None
    enabled: bool | None = None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.kind not in (PrincipalKind.USER, PrincipalKind.MANAGED_SERVICE_ACCOUNT):
            raise DomainValidationError(f"User may not carry kind {self.kind.value}.", field="kind")


@dataclass(frozen=True, slots=True)
class DomainGroup(Principal):
    """A security or distribution group in a domain directory."""

    kind: PrincipalKind = PrincipalKind.DOMAIN_GROUP
    scope: GroupScope = GroupScope.UNKNOWN
    group_type: GroupType = GroupType.UNKNOWN
    distinguished_name: str | None = None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.kind is not PrincipalKind.DOMAIN_GROUP:
            raise DomainValidationError(
                f"DomainGroup may not carry kind {self.kind.value}.", field="kind"
            )

    @property
    def grants_access(self) -> bool | None:
        """Distribution groups never grant access; ``None`` when the type is unknown."""
        if self.group_type is GroupType.UNKNOWN:
            return None
        return self.group_type is GroupType.SECURITY


@dataclass(frozen=True, slots=True)
class LocalGroup(Principal):
    """A group in a single computer's local account database.

    ``host_key`` is part of the identity because BUILTIN SIDs (``S-1-5-32-544`` and
    friends) are byte-identical on every Windows computer: ``BUILTIN\\Administrators`` on
    FS01 and on FS02 are different groups with different members, and merging them would
    silently invent access that nobody has.
    """

    kind: PrincipalKind = PrincipalKind.LOCAL_GROUP
    host_key: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.kind is not PrincipalKind.LOCAL_GROUP:
            raise DomainValidationError(
                f"LocalGroup may not carry kind {self.kind.value}.", field="kind"
            )
        if not self.host_key.strip():
            raise DomainValidationError(
                "A local group must record the host it exists on: BUILTIN SIDs are "
                "identical on every computer.",
                field="host_key",
            )

    @property
    def identity_key(self) -> str:
        return f"{self.host_key.casefold()}|{self.sid.value}"


@dataclass(frozen=True, slots=True)
class ComputerIdentity(Principal):
    """A computer or service identity encountered on an ACL or in a group."""

    kind: PrincipalKind = PrincipalKind.COMPUTER
    dns_host_name: str | None = None
    distinguished_name: str | None = None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.kind not in (PrincipalKind.COMPUTER, PrincipalKind.MANAGED_SERVICE_ACCOUNT):
            raise DomainValidationError(
                f"ComputerIdentity may not carry kind {self.kind.value}.", field="kind"
            )


class UnresolvedReason(StrEnum):
    """Why a SID could not be resolved to a principal."""

    DELETED = "deleted"
    """The account is known to have been deleted (an orphaned ACE)."""

    UNTRUSTED_DOMAIN = "untrusted_domain"
    """Issued by a domain this collector cannot query."""

    LOOKUP_FAILED = "lookup_failed"
    """Resolution failed or timed out; the SID may still be valid."""

    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UnresolvedPrincipal(Principal):
    """A SID observed on an ACL or in a group that no authority resolved.

    This is a first-class fact, not an error: orphaned SIDs on an ACL are exactly the kind
    of finding ADG exists to report. Never discard one, and never substitute a guessed name.
    """

    kind: PrincipalKind = PrincipalKind.UNRESOLVED
    reason: UnresolvedReason = UnresolvedReason.UNKNOWN
    last_known_name: str | None = None
    """A name seen in an earlier scan, if any. Display only; never a lookup key."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.kind is not PrincipalKind.UNRESOLVED:
            raise DomainValidationError(
                f"UnresolvedPrincipal may not carry kind {self.kind.value}.", field="kind"
            )
        if self.display_name is not None:
            raise DomainValidationError(
                "An unresolved SID has no display name. Record a name observed earlier in "
                "last_known_name so that it cannot be mistaken for a resolution.",
                field="display_name",
            )


@dataclass(frozen=True, slots=True)
class WellKnownPrincipal(Principal):
    """Everyone, Authenticated Users, SYSTEM, CREATOR OWNER, and similar.

    Their SIDs are universal, but what they *mean* on an ACL is contextual (CREATOR OWNER
    is replaced at inheritance time; Everyone excludes anonymous logon by default). That
    interpretation belongs to the Phase 4 engine, not here.
    """

    kind: PrincipalKind = PrincipalKind.WELL_KNOWN
    conventional_name: str | None = field(default=None)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.kind is not PrincipalKind.WELL_KNOWN:
            raise DomainValidationError(
                f"WellKnownPrincipal may not carry kind {self.kind.value}.", field="kind"
            )
        if self.conventional_name is None:
            object.__setattr__(self, "conventional_name", self.sid.well_known_name)


def referenced_principal_key(sid: Sid, host_key: str | None) -> str:
    """The storage key a reference to ``sid`` from ``host_key`` resolves to.

    A share ACL and a local group are both read *on a machine*, so a trustee they name is
    interpreted in that machine's context. Only a BUILTIN SID actually needs that context:
    ``S-1-5-32-544`` is byte-identical everywhere, so ``BUILTIN\\Administrators`` on FS01
    and on FS02 are different groups. Every other SID — a domain principal, a well-known
    SID such as ``Everyone``, or an account issued by the machine's own SID namespace — is
    globally unique and keeps its global key even when it appears on one server's ACL.

    Host-scoping a domain SID would split one group into one row per server that mentions
    it; failing to host-scope a BUILTIN SID would merge every server's local administrators
    into a single group that nobody is actually in. Both are wrong answers about who has
    access, which is why this rule has exactly one implementation.
    """
    if host_key and sid.is_builtin:
        return f"{host_key.casefold()}|{sid.value}"
    return sid.value
