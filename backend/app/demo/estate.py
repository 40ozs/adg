r"""A repeatable demo estate: AD, SMB and NTFS facts that actually fit together.

Every phase before this one tested a layer against a fixture built for that layer. This
module builds **one estate** and derives all three layers from it, so the AD group named on
a share ACL is the same group the membership graph contains, and the directory the share
publishes is the same directory the NTFS scan walked. A demo built any other way answers
every question separately and contradicts itself on the first question that crosses a layer.

Three properties are load bearing.

**Deterministic.** The same profile produces byte-identical transcripts on every machine
and every run — no clock, no random seed, no dictionary iteration order. That is what makes
a smoke test's assertions stable and what lets two people compare screenshots.

**Derived, not declared.** Inherited ACEs, ACL hashes, boundary verdicts and depths are
*computed* by the same domain functions the server uses (:mod:`app.domain.inheritance`,
:mod:`app.domain.acl_hash`). A hand-written fixture drifts from the rules the moment the
rules change; this one cannot, because it has no second copy of them. Only the explicit
entries an administrator would have set are written by hand.

**Deliberately awkward.** The estate carries the shapes that break naive implementations —
see :data:`FEATURES`. A demo whose every answer is "yes, through one group" proves nothing
about a product whose whole job is the other cases.

Nothing here touches a database. The output is collector transcripts in contract v1 form,
posted through the ordinary ingestion API by :mod:`app.demo.seed`, exactly as a Windows
collector would post them — so seeding exercises the real endpoint rather than a back door
into the tables.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from typing import Final

from app.contracts.v1.common import AclBoundaryReason, GroupScope, GroupType, PrincipalKind
from app.domain import (
    AceFlag,
    AceType,
    AclAceFacts,
    MembershipEdgeKind,
    ShareType,
    UnresolvedReason,
    acl_hash,
    boundary_reason_for,
    inherited_child_acl_hash,
    project_inherited_acl,
)

__all__ = [
    "DOMAIN_DNS",
    "DOMAIN_NETBIOS",
    "DOMAIN_SID",
    "FEATURES",
    "PROFILES",
    "DemoAce",
    "DemoDirectory",
    "DemoEdge",
    "DemoEstate",
    "DemoPrincipal",
    "DemoServer",
    "DemoShare",
    "DemoShareAce",
    "Feature",
    "Profile",
    "build_estate",
]

# ---------------------------------------------------------------------------
# The domain the demo estate lives in.
# ---------------------------------------------------------------------------

#: The same domain SID the Phase 0B scenario fixtures use, so a person who has read those
#: recognizes the principals here rather than learning a second cast.
DOMAIN_SID: Final = "S-1-5-21-1004336348-1177238915-682003330"
DOMAIN_DNS: Final = "corp.example.com"
DOMAIN_NETBIOS: Final = "CORP"

#: The instant the demo estate was "observed". Fixed, because a generator that used the
#: wall clock would produce a different transcript every run and no two seedings could be
#: compared. Callers that want fresh timestamps re-stamp the transcript instead.
EPOCH: Final = dt.datetime(2026, 9, 14, 6, 0, 0, tzinfo=dt.UTC)

# Access masks, spelled once.
FULL_CONTROL: Final = 0x001F01FF
MODIFY: Final = 0x001301BF
READ_EXECUTE: Final = 0x001200A9
WRITE_DATA: Final = 0x00000002
#: GENERIC_ALL. Present on one directory on purpose: a generic right is an indirection
#: Windows resolves per object, so it projects onto a child as *two* entries. Most real
#: estates are full of them and an estate with none never exercises that split.
GENERIC_ALL: Final = 0x10000000

# ACE flag combinations, spelled once.
OI_CI: Final = int(AceFlag.OBJECT_INHERIT | AceFlag.CONTAINER_INHERIT)
CI: Final = int(AceFlag.CONTAINER_INHERIT)
THIS_ONLY: Final = int(AceFlag.NONE)


def sid(rid: int) -> str:
    """A domain-relative SID for ``rid``."""
    return f"{DOMAIN_SID}-{rid}"


# Well-known trustees the estate names.
EVERYONE: Final = "S-1-1-0"
AUTHENTICATED_USERS: Final = "S-1-5-11"
SYSTEM: Final = "S-1-5-18"
BUILTIN_ADMINISTRATORS: Final = "S-1-5-32-544"
CREATOR_OWNER: Final = "S-1-3-0"

# Principal RIDs. Named constants rather than literals, because these appear in the
# runbook, in the e2e assertions and on screen, and a renumbering must move all of them.
DOMAIN_USERS: Final = 513
DOMAIN_ADMINS: Final = 512
ALICE: Final = 1104
BOB: Final = 1105
CAROL: Final = 1106
DAN: Final = 1107
ERIN: Final = 1108
SVC_BACKUP: Final = 1120
FINANCE_TEAM: Final = 1201
FINANCE_RW: Final = 1202
FINANCE_RO: Final = 1203
FINANCE_LEADS: Final = 1204
HR_TEAM: Final = 1205
HR_CONFIDENTIAL: Final = 1206
IT_ADMINS: Final = 1207
CONTRACTORS: Final = 1208
FINANCE_ANNOUNCE: Final = 1209
PROJECT_BASE: Final = 1300
CONTRACTOR_BASE: Final = 1500

#: A SID left on an ACL by a principal that no longer exists. Deliberately outside every
#: allocated range above so it can never collide with a generated user.
ORPHANED_SID: Final = f"{DOMAIN_SID}-9911"


# ---------------------------------------------------------------------------
# What the estate is for: the feature table.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Feature:
    """One product behavior this estate exists to exercise, and where to look for it."""

    name: str
    where: str
    why: str


#: Every awkward shape the demo estate carries on purpose. Asserted in the test suite, so a
#: feature removed from the estate fails a test rather than quietly weakening the demo.
FEATURES: Final[tuple[Feature, ...]] = (
    Feature(
        "nested groups",
        "Alice -> Finance-Team -> Finance-RW",
        "The answer is not visible in any single group's member list.",
    ),
    Feature(
        "two routes to one grant",
        "Alice reaches Finance-RW through Finance-Team and through Finance-Leads",
        "Removing one membership changes nothing; the explanation must say so.",
    ),
    Feature(
        "primary group",
        "every user -> Domain Users, as a primary_group edge",
        "Invisible to a collector that reads only the member attribute.",
    ),
    Feature(
        "a membership cycle",
        "Contractors and Project-001 each contain the other",
        "Traversal must terminate and report the cycle rather than hide it.",
    ),
    Feature(
        "a deny ACE that wins",
        r"Contractors denied on \\FS01\HR\Confidential",
        "Deny precedence, and a principal who is a member of a granting group anyway.",
    ),
    Feature(
        "broken inheritance",
        r"\\FS01\HR\Confidential is SE_DACL_PROTECTED",
        "A boundary reported with its reason, not a bare boolean.",
    ),
    Feature(
        "the share is the narrower layer",
        r"\\FS02\Archive: share grants read, NTFS grants modify",
        "The effective answer must be read, and must name which layer limited it.",
    ),
    Feature(
        "the NTFS ACL is the narrower layer",
        r"\\FS01\Finance: share grants full to Everyone, NTFS grants modify",
        "The common real shape, and the one a share-only tool gets wrong.",
    ),
    Feature(
        "an unresolved SID on a live ACL",
        r"an orphaned SID on \\FS02\Projects\Beta",
        "An ACE whose trustee cannot be named is a finding, not a blank row.",
    ),
    Feature(
        "a well-known trustee",
        r"Everyone, full control, on \\FS01\Public",
        "The most consequential grant in most estates.",
    ),
    Feature(
        "generic rights",
        r"GENERIC_ALL on \\FS02\Projects",
        "Projects onto a child as two entries; a naive projection reports a false boundary.",
    ),
    Feature(
        "CREATOR OWNER, materialized",
        r"\\FS02\Projects\Alpha carries an inherited grant naming Bob, who created it",
        "Unpredictable from the parent, so an untouched directory reads as changed.",
    ),
    Feature(
        "a host-scoped local group",
        r"BUILTIN\Administrators on FS01, with IT-Admins inside it",
        "S-1-5-32-544 is a different group on every machine.",
    ),
    Feature(
        "a distribution group",
        "Finance-Announce",
        "Named on no ACL and grants nothing; it must not appear as access.",
    ),
    Feature(
        "a disabled account",
        "Erin Black",
        "Still holds ACL-granted rights; disabling is not revocation.",
    ),
    Feature(
        "an administrative share",
        r"FS01\C$",
        "Special, and must not be presented as an ordinary published share.",
    ),
    Feature(
        "a run that failed outright",
        "the NTFS run against FS03",
        "Part of the estate is unobserved; every empty list below it is unknown, not empty.",
    ),
    Feature(
        "a partial run with errors",
        "the NTFS run against FS02",
        "Coverage is incomplete and the operator page has to say which objects were missed.",
    ),
)


# ---------------------------------------------------------------------------
# Estate records.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DemoPrincipal:
    sid: str
    principal_kind: PrincipalKind
    display_name: str | None = None
    sam_account_name: str | None = None
    user_principal_name: str | None = None
    distinguished_name: str | None = None
    group_scope: GroupScope | None = None
    group_type: GroupType | None = None
    enabled: bool | None = None
    host_key: str | None = None
    unresolved_reason: UnresolvedReason | None = None
    last_known_name: str | None = None


@dataclass(frozen=True, slots=True)
class DemoEdge:
    group_sid: str
    member_sid: str
    edge_kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
    host_key: str | None = None
    member_kind: PrincipalKind | None = None


@dataclass(frozen=True, slots=True)
class DemoServer:
    name: str
    dns_host_name: str
    is_domain_member: bool = True
    operating_system: str | None = None


@dataclass(frozen=True, slots=True)
class DemoShareAce:
    trustee_sid: str
    permission: str | None = None
    access_mask: int | None = None
    ace_type: AceType = AceType.ALLOW


@dataclass(frozen=True, slots=True)
class DemoShare:
    server_name: str
    share_name: str
    local_path: str
    description: str | None = None
    share_type: ShareType = ShareType.DISK
    is_special: bool = False
    aces: tuple[DemoShareAce, ...] = ()


@dataclass(frozen=True, slots=True)
class DemoAce:
    """One NTFS entry. ``order_index`` is assigned when the directory is materialized."""

    trustee_sid: str
    access_mask: int
    ace_flags: int = OI_CI
    ace_type: AceType = AceType.ALLOW
    inherited_from: str | None = None
    order_index: int | None = None

    @property
    def is_inherited(self) -> bool:
        return bool(self.ace_flags & int(AceFlag.INHERITED))

    def facts(self) -> AclAceFacts:
        return AclAceFacts(
            trustee_sid=self.trustee_sid,
            ace_type=self.ace_type,
            access_mask=self.access_mask,
            ace_flags=self.ace_flags,
            order_index=self.order_index,
        )


@dataclass(frozen=True, slots=True)
class DirectorySpec:
    """What an administrator set. Everything else about the directory is derived."""

    path: str
    local_path: str
    explicit: tuple[DemoAce, ...] = ()
    protected: bool = False
    dacl_present: bool = True
    owner_sid: str = BUILTIN_ADMINISTRATORS
    #: Reported in the run's error list instead of as an observation. The parent then has a
    #: child nobody read, which is a gap the product has to show rather than round down.
    unreadable: bool = False
    #: The SID Windows substituted for a ``CREATOR OWNER`` grant when this directory was
    #: created. It is stored as an *inherited* entry and it is not predictable from the
    #: parent — see :func:`app.domain.inheritance.project_inherited_ace` — so a directory
    #: carrying one is reported as differing from its parent although nobody set
    #: permissions on it. That false-looking finding is real, and worth having in a demo.
    materialized_creator: str | None = None


@dataclass(frozen=True, slots=True)
class DemoDirectory:
    """A directory as a collector would report it, with every derived field filled in."""

    path: str
    local_path: str
    server_name: str
    share_name: str
    owner_sid: str
    dacl_present: bool
    dacl_protected: bool
    inheritance_enabled: bool
    is_acl_boundary: bool
    boundary_reason: AclBoundaryReason | None
    depth_from_share_root: int
    acl_hash: str | None
    parent_acl_hash: str | None
    aces: tuple[DemoAce, ...]

    @property
    def ace_count(self) -> int:
        return len(self.aces)


@dataclass(frozen=True, slots=True)
class DemoRunError:
    code: str
    message: str
    target: str | None = None


@dataclass(frozen=True, slots=True)
class DemoEstate:
    """Everything the demo estate contains, before it is cut into scan runs."""

    profile: str
    principals: tuple[DemoPrincipal, ...]
    edges: tuple[DemoEdge, ...]
    servers: tuple[DemoServer, ...]
    shares: tuple[DemoShare, ...]
    directories: tuple[DemoDirectory, ...]
    ntfs_errors: dict[str, tuple[DemoRunError, ...]] = field(default_factory=dict)

    @property
    def object_count(self) -> int:
        return (
            len(self.principals)
            + len(self.edges)
            + len(self.servers)
            + len(self.shares)
            + sum(len(share.aces) for share in self.shares)
            + len(self.directories)
            + sum(directory.ace_count for directory in self.directories)
        )

    def directories_on(self, server_name: str) -> tuple[DemoDirectory, ...]:
        folded = server_name.casefold()
        return tuple(item for item in self.directories if item.server_name.casefold() == folded)

    def shares_on(self, server_name: str) -> tuple[DemoShare, ...]:
        folded = server_name.casefold()
        return tuple(item for item in self.shares if item.server_name.casefold() == folded)


@dataclass(frozen=True, slots=True)
class Profile:
    """How big the estate is. The *features* never vary; only the volume does.

    Scaling the feature set with the size would mean the small profile — the one a smoke
    test and a first demo use — is the one that proves the least.
    """

    name: str
    projects: int
    contractors: int
    reports_years: int


PROFILES: Final[dict[str, Profile]] = {
    # Enough to exercise every feature and small enough to read the whole transcript.
    "small": Profile(name="small", projects=2, contractors=2, reports_years=2),
    # The default: a plausible small-business estate.
    "standard": Profile(name="standard", projects=6, contractors=12, reports_years=4),
    # For measuring, not for demonstrating.
    "large": Profile(name="large", projects=60, contractors=250, reports_years=10),
}


# ---------------------------------------------------------------------------
# Building the estate.
# ---------------------------------------------------------------------------


def build_estate(profile: str = "standard") -> DemoEstate:
    """The whole demo estate, derived from one profile name.

    Raises:
        KeyError: if ``profile`` is not one of :data:`PROFILES`.
    """
    if profile not in PROFILES:
        raise KeyError(
            f"Unknown demo profile {profile!r}. Available: {', '.join(sorted(PROFILES))}."
        )
    shape = PROFILES[profile]

    principals = _principals(shape)
    edges = _edges(shape)
    servers = _servers()
    shares = _shares()
    directories = _directories(shape)
    return DemoEstate(
        profile=shape.name,
        principals=principals,
        edges=edges,
        servers=servers,
        shares=shares,
        directories=directories,
        ntfs_errors={
            "FS02": (
                DemoRunError(
                    code="access_denied",
                    message=(
                        "Access to the directory was denied reading its security "
                        "descriptor. Its permissions are unknown to this scan."
                    ),
                    target=r"\\FS02\Projects\Restricted",
                ),
                DemoRunError(
                    code="path_too_long",
                    message=(
                        "The path exceeded the maximum length the collector could open. "
                        "The subtree beneath it was not enumerated."
                    ),
                    target=r"\\FS02\Archive\2019\Q4\Approvals",
                ),
            ),
            "FS03": (
                DemoRunError(
                    code="host_unreachable",
                    message=(
                        "The server did not respond. No part of its file system was read, "
                        "so nothing about FS03 is known to this scan."
                    ),
                    target=r"\\FS03",
                ),
            ),
        },
    )


def _user(rid: int, name: str, sam: str, *, enabled: bool = True) -> DemoPrincipal:
    return DemoPrincipal(
        sid=sid(rid),
        principal_kind=PrincipalKind.USER,
        display_name=name,
        sam_account_name=sam,
        user_principal_name=f"{sam}@{DOMAIN_DNS}",
        distinguished_name=f"CN={name},OU=Staff,DC=corp,DC=example,DC=com",
        enabled=enabled,
    )


def _group(
    rid: int,
    name: str,
    *,
    scope: GroupScope = GroupScope.GLOBAL,
    group_type: GroupType = GroupType.SECURITY,
) -> DemoPrincipal:
    return DemoPrincipal(
        sid=sid(rid),
        principal_kind=PrincipalKind.DOMAIN_GROUP,
        display_name=name,
        sam_account_name=name,
        distinguished_name=f"CN={name},OU=Groups,DC=corp,DC=example,DC=com",
        group_scope=scope,
        group_type=group_type,
    )


def _principals(shape: Profile) -> tuple[DemoPrincipal, ...]:
    people = [
        _user(ALICE, "Alice Smith", "alice"),
        _user(BOB, "Bob Jones", "bob"),
        _user(CAROL, "Carol White", "carol"),
        _user(DAN, "Dan Brown", "dan"),
        # Disabled, and still named on an ACL. Disabling an account does not revoke what
        # its SID is granted, and a product that hides disabled accounts hides that.
        _user(ERIN, "Erin Black", "erin", enabled=False),
        DemoPrincipal(
            sid=sid(SVC_BACKUP),
            principal_kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT,
            display_name="svc-backup",
            sam_account_name="svc-backup$",
            distinguished_name="CN=svc-backup,OU=Service Accounts,DC=corp,DC=example,DC=com",
            enabled=True,
        ),
    ]
    people.extend(
        _user(CONTRACTOR_BASE + index, f"Contractor {index:03d}", f"ctr{index:03d}")
        for index in range(1, shape.contractors + 1)
    )

    groups = [
        _group(DOMAIN_USERS, "Domain Users", scope=GroupScope.GLOBAL),
        _group(DOMAIN_ADMINS, "Domain Admins", scope=GroupScope.GLOBAL),
        _group(FINANCE_TEAM, "Finance-Team"),
        _group(FINANCE_RW, "Finance-RW", scope=GroupScope.DOMAIN_LOCAL),
        _group(FINANCE_RO, "Finance-RO", scope=GroupScope.DOMAIN_LOCAL),
        _group(FINANCE_LEADS, "Finance-Leads"),
        _group(HR_TEAM, "HR-Team"),
        _group(HR_CONFIDENTIAL, "HR-Confidential", scope=GroupScope.DOMAIN_LOCAL),
        _group(IT_ADMINS, "IT-Admins"),
        _group(CONTRACTORS, "Contractors"),
        # Named on no ACL, and a distribution group besides: it cannot carry permissions
        # at all. It exists so that "member of a group" and "has access" stay distinct.
        _group(FINANCE_ANNOUNCE, "Finance-Announce", group_type=GroupType.DISTRIBUTION),
    ]
    groups.extend(
        _group(PROJECT_BASE + index, f"Project-{index:03d}", scope=GroupScope.DOMAIN_LOCAL)
        for index in range(1, shape.projects + 1)
    )

    local_and_well_known = [
        # BUILTIN\Administrators on FS01. Host-scoped: the same SID on FS02 is a different
        # group with different members, which is why host_key is part of its identity.
        DemoPrincipal(
            sid=BUILTIN_ADMINISTRATORS,
            principal_kind=PrincipalKind.LOCAL_GROUP,
            display_name="Administrators",
            host_key="fs01",
            group_scope=GroupScope.BUILTIN_LOCAL,
            group_type=GroupType.SECURITY,
        ),
        DemoPrincipal(
            sid=EVERYONE,
            principal_kind=PrincipalKind.WELL_KNOWN,
            display_name="Everyone",
        ),
        DemoPrincipal(
            sid=AUTHENTICATED_USERS,
            principal_kind=PrincipalKind.WELL_KNOWN,
            display_name="Authenticated Users",
        ),
        DemoPrincipal(
            sid=SYSTEM,
            principal_kind=PrincipalKind.WELL_KNOWN,
            display_name="SYSTEM",
        ),
        DemoPrincipal(
            sid=CREATOR_OWNER,
            principal_kind=PrincipalKind.WELL_KNOWN,
            display_name="CREATOR OWNER",
        ),
        # The ACL on Projects\Beta names this SID; the directory service cannot resolve it.
        # Reported as a fact — an ACE nobody can attribute is a finding, not a blank.
        DemoPrincipal(
            sid=ORPHANED_SID,
            principal_kind=PrincipalKind.UNRESOLVED,
            unresolved_reason=UnresolvedReason.DELETED,
            last_known_name="CORP\\jsmith",
        ),
    ]
    return tuple(people + groups + local_and_well_known)


def _edges(shape: Profile) -> tuple[DemoEdge, ...]:
    everyone_human = [ALICE, BOB, CAROL, DAN, ERIN] + [
        CONTRACTOR_BASE + index for index in range(1, shape.contractors + 1)
    ]
    edges = [
        # Primary group. A collector that reads only the `member` attribute never sees
        # these, and Domain Users is on a real ACL below, so omitting them would under-
        # report access for every account in the domain.
        DemoEdge(
            group_sid=sid(DOMAIN_USERS),
            member_sid=sid(rid),
            edge_kind=MembershipEdgeKind.PRIMARY_GROUP,
            member_kind=PrincipalKind.USER,
        )
        for rid in everyone_human
    ]

    edges += [
        # Alice reaches Finance-RW twice over: through Finance-Team and through
        # Finance-Leads. Neither removal on its own takes anything away.
        DemoEdge(sid(FINANCE_TEAM), sid(ALICE), member_kind=PrincipalKind.USER),
        DemoEdge(sid(FINANCE_LEADS), sid(ALICE), member_kind=PrincipalKind.USER),
        DemoEdge(sid(FINANCE_RW), sid(FINANCE_TEAM), member_kind=PrincipalKind.DOMAIN_GROUP),
        DemoEdge(sid(FINANCE_RW), sid(FINANCE_LEADS), member_kind=PrincipalKind.DOMAIN_GROUP),
        # Bob has exactly one route, so removing it does take his access away. The
        # explanation screen has to distinguish the two cases.
        DemoEdge(sid(FINANCE_TEAM), sid(BOB), member_kind=PrincipalKind.USER),
        # Carol is read-only on Finance and the one member of HR-Confidential.
        DemoEdge(sid(FINANCE_RO), sid(CAROL), member_kind=PrincipalKind.USER),
        DemoEdge(sid(HR_TEAM), sid(CAROL), member_kind=PrincipalKind.USER),
        DemoEdge(sid(HR_CONFIDENTIAL), sid(HR_TEAM), member_kind=PrincipalKind.DOMAIN_GROUP),
        # Dan is an administrator: IT-Admins is inside the local Administrators group on
        # FS01, which is how most real administrative access is actually held.
        DemoEdge(sid(IT_ADMINS), sid(DAN), member_kind=PrincipalKind.USER),
        DemoEdge(
            group_sid=BUILTIN_ADMINISTRATORS,
            member_sid=sid(IT_ADMINS),
            edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="fs01",
            member_kind=PrincipalKind.DOMAIN_GROUP,
        ),
        # Erin is disabled and still in Finance-Team.
        DemoEdge(sid(FINANCE_TEAM), sid(ERIN), member_kind=PrincipalKind.USER),
        # A distribution group with a member, granting nothing anywhere.
        DemoEdge(sid(FINANCE_ANNOUNCE), sid(ALICE), member_kind=PrincipalKind.USER),
        # The service account.
        DemoEdge(
            sid(IT_ADMINS),
            sid(SVC_BACKUP),
            member_kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT,
        ),
    ]

    # Contractors, and the cycle. Project-001 contains Contractors and Contractors contains
    # Project-001: a real misconfiguration, and the shape that makes an unguarded traversal
    # run forever.
    for index in range(1, shape.contractors + 1):
        edges.append(
            DemoEdge(
                sid(CONTRACTORS),
                sid(CONTRACTOR_BASE + index),
                member_kind=PrincipalKind.USER,
            )
        )
    edges.append(
        DemoEdge(sid(PROJECT_BASE + 1), sid(CONTRACTORS), member_kind=PrincipalKind.DOMAIN_GROUP)
    )
    edges.append(
        DemoEdge(sid(CONTRACTORS), sid(PROJECT_BASE + 1), member_kind=PrincipalKind.DOMAIN_GROUP)
    )
    # Every project group holds the finance team, so a project directory has a real
    # membership route rather than a direct user grant.
    for index in range(2, shape.projects + 1):
        edges.append(
            DemoEdge(
                sid(PROJECT_BASE + index),
                sid(FINANCE_TEAM),
                member_kind=PrincipalKind.DOMAIN_GROUP,
            )
        )
    return tuple(edges)


def _servers() -> tuple[DemoServer, ...]:
    return (
        DemoServer("FS01", f"fs01.{DOMAIN_DNS}", operating_system="Windows Server 2022 Standard"),
        DemoServer("FS02", f"fs02.{DOMAIN_DNS}", operating_system="Windows Server 2019 Standard"),
        # Declared by the SMB run and never reached by the NTFS run; see the failed run in
        # build_estate. A server ADG knows exists and cannot see inside is the honest shape
        # of an incomplete estate.
        DemoServer("FS03", f"fs03.{DOMAIN_DNS}", operating_system="Windows Server 2016 Standard"),
    )


def _shares() -> tuple[DemoShare, ...]:
    return (
        DemoShare(
            "FS01",
            "Finance",
            r"D:\Shares\Finance",
            description="Finance department share",
            # Everyone/full at the share layer, which is the Windows default and is why the
            # share ACL so rarely limits anything. The NTFS ACL is the narrow one here.
            aces=(DemoShareAce(EVERYONE, permission="full"),),
        ),
        DemoShare(
            "FS01",
            "HR",
            r"D:\Shares\HR",
            description="Human resources",
            aces=(
                DemoShareAce(sid(HR_TEAM), permission="change"),
                DemoShareAce(sid(IT_ADMINS), permission="full"),
            ),
        ),
        DemoShare(
            "FS01",
            "Public",
            r"D:\Shares\Public",
            description="Company-wide read/write drop",
            aces=(DemoShareAce(EVERYONE, permission="full"),),
        ),
        DemoShare(
            "FS01",
            "C$",
            r"C:\\",
            description="Default share",
            is_special=True,
            aces=(DemoShareAce(BUILTIN_ADMINISTRATORS, permission="full"),),
        ),
        DemoShare(
            "FS02",
            "Projects",
            r"E:\Shares\Projects",
            description="Project working area",
            aces=(DemoShareAce(AUTHENTICATED_USERS, permission="change"),),
        ),
        DemoShare(
            "FS02",
            "Archive",
            r"E:\Shares\Archive",
            description="Read-only archive",
            # Reported as a mask rather than a level, because the security-descriptor APIs
            # report masks and a collector must report the form its source gave it.
            aces=(
                DemoShareAce(AUTHENTICATED_USERS, access_mask=READ_EXECUTE),
                DemoShareAce(sid(IT_ADMINS), access_mask=FULL_CONTROL),
            ),
        ),
        DemoShare(
            "FS03",
            "Legacy",
            r"F:\Legacy",
            description="Legacy file store; the NTFS scan of this server failed",
            aces=(DemoShareAce(AUTHENTICATED_USERS, permission="change"),),
        ),
    )


def _directory_specs(shape: Profile) -> tuple[DirectorySpec, ...]:
    """The explicit permissions somebody set, and nothing else.

    Order matters: a directory's parent must appear before it, because the materializer
    walks this sequence once and reads each parent's finished DACL.
    """
    system_full = DemoAce(SYSTEM, FULL_CONTROL, OI_CI)
    admins_full = DemoAce(BUILTIN_ADMINISTRATORS, FULL_CONTROL, OI_CI)

    specs: list[DirectorySpec] = [
        DirectorySpec(
            path=r"\\FS01\Finance",
            local_path=r"D:\Shares\Finance",
            explicit=(
                system_full,
                admins_full,
                DemoAce(sid(FINANCE_RW), MODIFY, OI_CI),
                DemoAce(sid(FINANCE_RO), READ_EXECUTE, OI_CI),
                # Every domain account can traverse the share root and list it. This is
                # what makes Domain Users — and therefore the primary-group edge — matter.
                DemoAce(sid(DOMAIN_USERS), READ_EXECUTE, THIS_ONLY),
            ),
        ),
        # Inherits cleanly. The product must report it as *not* a boundary, which is the
        # assertion a wrong projection fails.
        DirectorySpec(path=r"\\FS01\Finance\Reports", local_path=r"D:\Shares\Finance\Reports"),
        # Somebody set permissions here: explicit entries on top of what it inherits.
        DirectorySpec(
            path=r"\\FS01\Finance\Budgets",
            local_path=r"D:\Shares\Finance\Budgets",
            explicit=(DemoAce(sid(FINANCE_LEADS), FULL_CONTROL, OI_CI),),
        ),
        # Protected: inheritance is broken and the ACL starts over.
        DirectorySpec(
            path=r"\\FS01\Finance\Payroll",
            local_path=r"D:\Shares\Finance\Payroll",
            protected=True,
            explicit=(
                system_full,
                admins_full,
                DemoAce(sid(FINANCE_LEADS), MODIFY, OI_CI),
                # Erin is disabled and still named here, directly.
                DemoAce(sid(ERIN), READ_EXECUTE, OI_CI),
            ),
        ),
        DirectorySpec(
            path=r"\\FS01\HR",
            local_path=r"D:\Shares\HR",
            explicit=(
                system_full,
                admins_full,
                DemoAce(sid(HR_TEAM), MODIFY, OI_CI),
            ),
        ),
        DirectorySpec(
            path=r"\\FS01\HR\Confidential",
            local_path=r"D:\Shares\HR\Confidential",
            protected=True,
            explicit=(
                system_full,
                admins_full,
                # Deny first, which is where Windows puts it and where it has to be for the
                # ACL to be in canonical order.
                DemoAce(
                    sid(CONTRACTORS),
                    FULL_CONTROL,
                    OI_CI,
                    ace_type=AceType.DENY,
                ),
                DemoAce(sid(HR_CONFIDENTIAL), MODIFY, OI_CI),
            ),
        ),
        DirectorySpec(
            path=r"\\FS01\Public",
            local_path=r"D:\Shares\Public",
            explicit=(
                system_full,
                admins_full,
                # The grant every audit exists to find.
                DemoAce(EVERYONE, FULL_CONTROL, OI_CI),
            ),
        ),
        DirectorySpec(
            path=r"\\FS02\Projects",
            local_path=r"E:\Shares\Projects",
            explicit=(
                system_full,
                admins_full,
                # A generic right: it projects onto a child as two entries, one mapped and
                # effective, one unmapped and inherit-only.
                DemoAce(sid(IT_ADMINS), GENERIC_ALL, OI_CI),
                DemoAce(sid(FINANCE_TEAM), MODIFY, OI_CI),
                # CREATOR OWNER projects only halfway: its effective copy names whoever
                # created the child, which is not a fact about this directory. Every child
                # below therefore reports acl_differs_from_parent, correctly.
                DemoAce(CREATOR_OWNER, FULL_CONTROL, OI_CI | int(AceFlag.INHERIT_ONLY)),
            ),
        ),
        # Nobody set permissions here. Windows materialized the CREATOR OWNER grant as an
        # inherited entry naming Bob, who created it, and that entry is not derivable from
        # the parent — so the directory is correctly reported as differing from its parent.
        DirectorySpec(
            path=r"\\FS02\Projects\Alpha",
            local_path=r"E:\Shares\Projects\Alpha",
            materialized_creator=sid(BOB),
        ),
        DirectorySpec(
            path=r"\\FS02\Projects\Beta",
            local_path=r"E:\Shares\Projects\Beta",
            explicit=(
                # An ACE whose trustee no longer exists. Windows keeps it; so does ADG.
                DemoAce(ORPHANED_SID, MODIFY, OI_CI),
            ),
        ),
        # Reported in the FS02 run's error list rather than as an observation, so the
        # parent has a child whose permissions nobody read.
        DirectorySpec(
            path=r"\\FS02\Projects\Restricted",
            local_path=r"E:\Shares\Projects\Restricted",
            unreadable=True,
        ),
        DirectorySpec(
            path=r"\\FS02\Archive",
            local_path=r"E:\Shares\Archive",
            explicit=(
                system_full,
                admins_full,
                # Modify at the NTFS layer; the share grants only read. The share is the
                # narrower layer here, which is the reverse of Finance.
                DemoAce(sid(FINANCE_TEAM), MODIFY, OI_CI),
            ),
        ),
    ]

    # Scale: reporting years under Finance\Reports, project directories under Projects.
    for year in range(2026 - shape.reports_years + 1, 2027):
        specs.append(
            DirectorySpec(
                path=rf"\\FS01\Finance\Reports\{year}",
                local_path=rf"D:\Shares\Finance\Reports\{year}",
            )
        )
    for index in range(1, shape.projects + 1):
        specs.append(
            DirectorySpec(
                path=rf"\\FS02\Projects\Project-{index:03d}",
                local_path=rf"E:\Shares\Projects\Project-{index:03d}",
                explicit=(DemoAce(sid(PROJECT_BASE + index), MODIFY, OI_CI),),
            )
        )
    return tuple(specs)


def _parent_path(path: str) -> str | None:
    """The parent UNC path, or ``None`` for a share root."""
    trimmed = path.rstrip("\\")
    parts = trimmed.lstrip("\\").split("\\")
    # parts == [server, share, ...segments]; a share root has exactly two.
    if len(parts) <= 2:
        return None
    return "\\\\" + "\\".join(parts[:-1])


def _split_unc(path: str) -> tuple[str, str]:
    parts = path.lstrip("\\").split("\\")
    return parts[0], parts[1]


def _directories(shape: Profile) -> tuple[DemoDirectory, ...]:
    """Materialize the specs: inherit, hash, and decide each boundary.

    The derivation runs the server's own functions, so this cannot encode a different idea
    of what a child inherits than the one the product enforces.
    """
    built: dict[str, DemoDirectory] = {}
    output: list[DemoDirectory] = []

    for spec in _directory_specs(shape):
        if spec.unreadable:
            continue

        server_name, share_name = _split_unc(spec.path)
        parent_path = _parent_path(spec.path)
        parent = built.get(parent_path.casefold()) if parent_path else None
        is_share_root = parent_path is None

        inherited: tuple[AclAceFacts, ...] = ()
        parent_dacl_present: bool | None = None
        parent_projection: str | None = None
        parent_hash: str | None = None
        if parent is not None:
            parent_dacl_present = parent.dacl_present
            parent_hash = parent.acl_hash
            parent_projection = inherited_child_acl_hash(
                dacl_present=parent.dacl_present,
                aces=[ace.facts() for ace in parent.aces],
                for_container=True,
            )
            if not spec.protected and parent.dacl_present:
                inherited = project_inherited_acl(
                    [ace.facts() for ace in parent.aces], for_container=True
                )

        # Canonical order: explicit entries first, then inherited ones. Windows writes a
        # DACL this way and the evaluation order depends on it.
        aces: list[DemoAce] = []
        for position, ace in enumerate(spec.explicit):
            aces.append(replace(ace, order_index=position))
        for offset, projected in enumerate(inherited):
            aces.append(
                DemoAce(
                    trustee_sid=projected.trustee_sid,
                    access_mask=projected.access_mask,
                    ace_flags=projected.ace_flags,
                    ace_type=projected.ace_type,
                    inherited_from=parent_path,
                    order_index=len(spec.explicit) + offset,
                )
            )
        if spec.materialized_creator is not None:
            aces.append(
                DemoAce(
                    trustee_sid=spec.materialized_creator,
                    access_mask=FULL_CONTROL,
                    ace_flags=int(AceFlag.INHERITED),
                    inherited_from=parent_path,
                    order_index=len(aces),
                )
            )

        own_hash = (
            acl_hash(
                dacl_present=spec.dacl_present,
                dacl_protected=spec.protected,
                aces=[ace.facts() for ace in aces],
            )
            if spec.dacl_present or not aces
            else None
        )
        reason = boundary_reason_for(
            is_share_root=is_share_root,
            is_scan_root=False,
            dacl_present=spec.dacl_present,
            dacl_protected=spec.protected,
            acl_hash=own_hash,
            parent_dacl_present=parent_dacl_present,
            parent_projection=parent_projection,
        )
        depth = 0 if is_share_root else (parent.depth_from_share_root + 1 if parent else 1)

        directory = DemoDirectory(
            path=spec.path,
            local_path=spec.local_path,
            server_name=server_name,
            share_name=share_name,
            owner_sid=spec.owner_sid,
            dacl_present=spec.dacl_present,
            dacl_protected=spec.protected,
            # SE_DACL_PROTECTED is exactly "inheritance is off here".
            inheritance_enabled=not spec.protected,
            is_acl_boundary=reason is not None,
            boundary_reason=reason,
            depth_from_share_root=depth,
            acl_hash=own_hash,
            parent_acl_hash=parent_hash,
            aces=tuple(aces),
        )
        built[spec.path.casefold()] = directory
        output.append(directory)

    return tuple(output)
