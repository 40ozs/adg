r"""Genesee Press's file estate: servers, shares, folders and their permissions, on a date.

:mod:`.canon` says who works at Genesee Press and which groups exist. This module says what
those people and groups can reach: 33 file servers, about a hundred shares, and some seven
hundred folders whose ACLs accreted over ten years.

Two kinds of folder live here.

**Authored folders** carry a golden scenario or a deliberate risk, and their ACLs are
written out in :func:`_catalog`. They are the folders the documentation and the tests name.

**Organic folders** get their ACL from a seeded roll keyed on the folder's path: most inherit
cleanly, some add a group, some break inheritance, some name a person. The roll's weights
depend on the share's era, so a 2017 share carries more debt than a 2024 one. Every roll is a
pure function of ``(seed, path)``, so adding a folder elsewhere cannot change this one.

Inherited entries, ACL hashes and boundary verdicts are never written by hand: every folder
is materialized by :func:`app.demo.estate.materialize_directories`, which runs the server's
own inheritance and hashing functions.

Everything is a function of a **date**. :func:`build_estate` returns the estate as a scan on
that day would have found it: accounts not yet hired are absent, leavers are disabled and
later deleted, a folder created mid-month appears, and an entry removed in a clean-up is
gone. :mod:`.generations` uses that to produce three scans that differ the way real ones do.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Final

from app.contracts.v1.common import GroupScope, GroupType, PrincipalKind
from app.demo.estate import (
    AUTHENTICATED_USERS,
    BUILTIN_ADMINISTRATORS,
    CREATOR_OWNER,
    EVERYONE,
    SYSTEM,
    DemoAce,
    DemoEdge,
    DemoEstate,
    DemoPrincipal,
    DemoServer,
    DemoShare,
    DemoShareAce,
    DirectorySpec,
    materialize_directories,
)
from app.demo.genesee_press import vendor
from app.demo.genesee_press.canon import (
    ACQUISITION_TAG,
    DEPARTMENTS_BY_SLUG,
    DOMAIN_DNS,
    DOMAIN_NETBIOS,
    SNAPSHOT,
    Canon,
    Category,
    Draws,
    Employee,
    Era,
    Group,
    Level,
    build_canon,
    draw_key,
    era_of,
    legacy_key,
)
from app.domain import AceFlag, AceType, MembershipEdgeKind, UnresolvedReason

__all__ = [
    "PERMISSIONS",
    "SERVERS",
    "AceDef",
    "AceIntent",
    "FolderDef",
    "GeneseePressEstate",
    "Posture",
    "ShareDef",
    "build_estate",
    "catalog",
]

# ---------------------------------------------------------------------------
# Permissions, spelled once.
# ---------------------------------------------------------------------------

OI_CI: Final = int(AceFlag.OBJECT_INHERIT | AceFlag.CONTAINER_INHERIT)
CI: Final = int(AceFlag.CONTAINER_INHERIT)
THIS_ONLY: Final = int(AceFlag.NONE)
INHERIT_ONLY_OI_CI: Final = OI_CI | int(AceFlag.INHERIT_ONLY)

#: The NTFS permission levels the Windows security dialog offers, as access masks.
PERMISSIONS: Final[dict[str, int]] = {
    "R": 0x00120089,  # Read
    "RX": 0x001200A9,  # Read & execute
    "L": 0x001200A9,  # List folder contents (read & execute, folders only)
    "RW": 0x0012019F,  # Read + Write
    "M": 0x001301BF,  # Modify
    "F": 0x001F01FF,  # Full control
    "DEL": 0x00010040,  # Delete + delete child: used only on deny entries
}

PERMISSION_LABELS: Final[dict[str, str]] = {
    "R": "Read",
    "RX": "Read & execute",
    "L": "List folder contents",
    "RW": "Read + Write",
    "M": "Modify",
    "F": "Full control",
    "DEL": "Delete",
}

#: Trustee aliases usable in an :class:`AceDef`.
WELL_KNOWN: Final[dict[str, str]] = {
    "EVERYONE": EVERYONE,
    "AUTHENTICATED_USERS": AUTHENTICATED_USERS,
    "SYSTEM": SYSTEM,
    "CREATOR_OWNER": CREATOR_OWNER,
    "ADMINISTRATORS": BUILTIN_ADMINISTRATORS,
}

#: Trustees so wide that an ACL naming them is broad whatever else it says.
BROAD_GROUPS: Final = frozenset(
    {
        "All Staff",
        "Domain Users",
        "FS-Legacy-Users",
        "Shared_Drive_Users",
        "AD-Okta-Sync-All",
        "Remote-Workers-2020",
        "Managers",
        "Hiring-Managers",
    }
)
BROAD_WELL_KNOWN: Final = frozenset({"EVERYONE", "AUTHENTICATED_USERS"})

WRITE_LEVELS: Final = frozenset({"RW", "M", "F"})


class AceIntent(StrEnum):
    """Why an entry naming a person is there. Recorded in the manifest, not in ADG: ADG has
    no field for intent, which is rather the point of an access review."""

    TEMPORARY_PROJECT = "temporary-project"
    EXECUTIVE_ASSISTANT = "executive-assistant"
    FINANCE_CLOSE = "finance-close-exception"
    ACQUISITION_MIGRATION = "acquisition-migration"
    HISTORICAL_ENGINEER = "historical-engineer"
    NESTING_CONFUSION = "nested-group-confusion"
    INCIDENT_ACCESS = "incident-access"
    MIGRATION_LEFTOVER = "migration-leftover"
    FORMER_USER = "former-user"


class Posture(StrEnum):
    ORDINARY = "ordinary"
    QUESTIONABLE = "questionable"
    RISKY = "risky"


@dataclass(frozen=True, slots=True)
class AceDef:
    """One explicit entry as an administrator set it.

    ``trustee`` is a group name, an alias from :data:`WELL_KNOWN`, ``DOMAIN_USERS``, or an
    employee id (``GP-00042``).
    """

    trustee: str
    level: str
    flags: int = OI_CI
    deny: bool = False
    since: dt.date | None = None
    until: dt.date | None = None
    intent: AceIntent | None = None

    def present_on(self, day: dt.date) -> bool:
        return (self.since is None or day >= self.since) and (
            self.until is None or day < self.until
        )


@dataclass(frozen=True, slots=True)
class FolderDef:
    rel: str
    """Path below the share root, with ``/`` separators."""
    explicit: tuple[AceDef, ...] = ()
    protected: bool = False
    organic: bool = True
    """When true and nothing is authored, the folder's ACL comes from the seeded roll."""
    created: dt.date | None = None
    deleted: dt.date | None = None
    unreadable_from: dt.date | None = None
    """From this date the collector cannot read the folder's descriptor."""
    scenario: str | None = None


@dataclass(frozen=True, slots=True)
class ShareDef:
    server: str
    name: str
    area: str
    created: dt.date
    root: tuple[AceDef, ...]
    folders: tuple[FolderDef, ...] = ()
    description: str = ""
    share_acl: str = "mirror"
    """``mirror`` grants each trustee the share-level right its NTFS entries need;
    ``everyone`` and ``authenticated`` are the legacy wide-open share ACLs."""
    organic: bool = True
    scenario: str | None = None
    drive: str = "D"

    @property
    def unc(self) -> str:
        return rf"\\{self.server}\{self.name}"

    @property
    def era(self) -> Era:
        return era_of(self.created)


@dataclass(frozen=True, slots=True)
class ServerDef:
    name: str
    created: dt.date
    operating_system: str


def _d(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _file_servers() -> tuple[ServerDef, ...]:
    """The file servers, oldest first: the projection's ``file_server`` hosts."""
    hosts = [host for host in vendor.records("hosts.jsonl") if host["role"] == "file_server"]
    return tuple(
        ServerDef(host["name"], _d(host["commissioned_on"]), host["operating_system"])
        for host in sorted(hosts, key=lambda host: (host["commissioned_on"], host["name"]))
    )


SERVERS: Final[tuple[ServerDef, ...]] = _file_servers()


SERVERS_BY_NAME: Final[dict[str, ServerDef]] = {server.name: server for server in SERVERS}

#: Folder names used to fill shares out. Authored folders come first in each share.
_AREA_FOLDERS: Final[dict[str, tuple[str, ...]]] = {
    "finance": (
        "Budgets",
        "Forecasts",
        "Reports",
        "Reports/Monthly",
        "Reports/Quarterly",
        "Vendors",
        "Expenses",
        "Policies",
        "Templates",
        "Audit-Support",
    ),
    "engineering": (
        "Docs",
        "Docs/Architecture",
        "Docs/Onboarding",
        "Tools",
        "Specs",
        "Benchmarks",
        "Postmortems",
        "Design-Reviews",
        "Scratch",
        "Archive",
    ),
    "product": (
        "Roadmaps",
        "Requirements",
        "Research",
        "Launches",
        "Pricing",
        "Feedback",
        "Competitive",
        "Metrics",
    ),
    "data": (
        "Pipelines",
        "Models",
        "Dashboards",
        "Exports",
        "Data-Dictionary",
        "Sandbox",
        "Requests",
        "Quality",
    ),
    "it": (
        "Software",
        "Images",
        "Scripts",
        "Inventory",
        "Vendors",
        "Runbooks",
        "Licenses",
        "Network",
    ),
    "security": ("Policies", "Assessments", "Vendors", "Awareness", "Tooling", "Reports"),
    "people": ("Policies", "Benefits", "Onboarding", "Learning", "Templates", "Surveys"),
    "legal": ("Contracts", "Policies", "Templates", "Corporate", "Trademarks", "Vendors"),
    "sales": (
        "Proposals",
        "Forecasts",
        "Playbooks",
        "Territories",
        "Pricing",
        "Customers",
        "Enablement",
        "Templates",
        "Wins",
    ),
    "marketing": (
        "Campaigns",
        "Content",
        "Events",
        "Brand",
        "Webinars",
        "Analytics",
        "Agencies",
        "Press",
    ),
    "customer-success": (
        "Playbooks",
        "Escalations",
        "Renewals",
        "Training",
        "Knowledge-Base",
        "QBRs",
        "Surveys",
        "Templates",
    ),
    "operations": (
        "Facilities",
        "Procurement",
        "Vendors",
        "Planning",
        "Travel",
        "Assets",
        "Contracts",
        "Reports",
    ),
    "design": ("Product-Design", "Brand", "Research", "Handoff", "Components", "Archive"),
    "executive": ("Board", "Strategy", "Offsites", "Investor-Relations", "Org-Planning"),
    "projects": ("Planning", "Status", "Deliverables", "Retrospectives", "Archive"),
    "archive": ("2017", "2018", "2019", "2020", "Misc"),
    "migrations": ("Wave-1", "Wave-2", "Logs", "Mappings"),
    "acquisitions": ("Integration", "Contracts", "People", "Systems", "Data-Room"),
    "shared": ("Public", "Templates", "Team-Drops", "Scans", "Training", "Events"),
}

#: Which department each area belongs to, for picking groups and people.
_AREA_DEPARTMENT: Final[dict[str, str]] = {
    "projects": "product",
    "archive": "it",
    "migrations": "it",
    "acquisitions": "data",
    "shared": "operations",
}


def a(trustee: str, level: str, flags: int = OI_CI, **kwargs: object) -> AceDef:
    """Shorthand for an :class:`AceDef` in the catalog below."""
    return AceDef(trustee, level, flags, **kwargs)  # type: ignore[arg-type]


#: Second-level folders that accumulate under almost anything.
_CHILD_FOLDERS: Final[tuple[str, ...]] = (
    "2024",
    "2025",
    "2026",
    "Archive",
    "Drafts",
    "Final",
    "Working",
    "Shared",
    "Old",
    "Templates",
    "Reviews",
    "Q3",
    "Q4",
)


def _fill(
    area: str, count: int, *, skip: Iterable[str] = (), share: str = ""
) -> tuple[FolderDef, ...]:
    """``count`` area folders, each with zero to two seeded subfolders."""
    taken = {item.casefold() for item in skip}
    names = [name for name in _AREA_FOLDERS[area] if name.casefold() not in taken]
    folders: list[FolderDef] = []
    for name in names[:count]:
        folders.append(FolderDef(name))
        if "/" in name:
            continue
        draws = Draws("children", draw_key(share).casefold(), draw_key(name).casefold())
        for child in sorted(draws.sample(_CHILD_FOLDERS, draws.below(3))):
            folders.append(FolderDef(f"{name}/{child}"))
    return tuple(folders)


# ---------------------------------------------------------------------------
# The catalog. Every share, every authored folder.
# ---------------------------------------------------------------------------


def catalog(canon: Canon) -> tuple[ShareDef, ...]:
    """Every share Genesee Press has, oldest servers first."""

    def person(
        label: str, predicate: Callable[[Employee], bool], *, active: bool = True, nth: int = 0
    ) -> str:
        pool = [
            employee
            for employee in canon.employees
            if predicate(employee) and (employee.is_active or not active)
        ]
        if not pool:
            raise LookupError(f"No employee matches {label!r}")
        return Draws("catalog-person", label).sample(pool, nth + 1)[nth].employee_id

    def tag(name: str) -> str:
        return canon.one(name).employee_id

    controller = tag("golden:controller")
    mover = tag("golden:cross-department")
    assistant_1 = tag("golden:executive-assistant-1")
    assistant_2 = tag("golden:executive-assistant-2")
    accountants = [
        person(
            f"close-{index}",
            lambda e: e.department == "finance" and e.team == "accounting" and e.level == Level.IC,
            nth=index,
        )
        for index in range(3)
    ]
    payroll_specialist = person(
        "payroll-vendor", lambda e: e.department == "finance" and e.team == "payroll"
    )
    veteran_engineers = [
        person(
            f"veteran-{index}",
            lambda e: (
                e.department == "engineering"
                and e.level == Level.IC
                and e.hire_date < dt.date(2019, 1, 1)
            ),
            nth=index,
        )
        for index in range(4)
    ]
    it_migrators = [
        person(
            f"migrator-{index}",
            lambda e: e.department == "it" and e.team in ("corporate-systems", "endpoint"),
            nth=index,
        )
        for index in range(3)
    ]
    responders = [
        person(
            f"responder-{index}",
            lambda e: (
                (e.department == "security" and e.team == "soc")
                or (e.department == "it" and e.team == "network")
            ),
            nth=index,
        )
        for index in range(2)
    ]
    acquired = [
        person(f"lumen-{index}", lambda e: ACQUISITION_TAG in e.tags, nth=index)  # v1 draw label
        for index in range(3)
    ]
    long_gone = [
        person(
            f"long-gone-{index}",
            lambda e: (
                e.termination_date is not None
                and e.termination_date < dt.date(2024, 6, 1)
                and e.hire_date < dt.date(2020, 1, 1)
            ),
            active=False,
            nth=index,
        )
        for index in range(4)
    ]
    recently_left = [
        person(
            f"recently-left-{index}",
            lambda e: (
                e.termination_date is not None
                and dt.date(2026, 5, 1) <= e.termination_date < dt.date(2026, 9, 1)
            ),
            active=False,
            nth=index,
        )
        for index in range(2)
    ]
    # Somebody whose account is deleted between the first and second scans, still named on
    # a folder: the trustee turns from a person into an unresolved SID between scans.
    crossing = person(
        "crosses-deletion",
        lambda e: (
            e.termination_date is not None
            and dt.date(2026, 9, 3)
            <= e.termination_date + dt.timedelta(days=180)
            < dt.date(2026, 9, 17)
        ),
        active=False,
    )
    sales_forecaster = person(
        "forecast-cleanup", lambda e: e.department == "finance" and e.team == "fpa"
    )
    hr_ops = person("hr-ops", lambda e: e.department == "people" and e.team == "people-ops")
    contractor_manager = person(
        "legal-temp", lambda e: e.department == "operations" and e.team == "procurement"
    )

    sys_admin = (a("SYSTEM", "F"), a("ADMINISTRATORS", "F"))
    shares: list[ShareDef] = []

    def share(
        server: str,
        name: str,
        area: str,
        created: str,
        root: Sequence[AceDef],
        folders: Sequence[FolderDef] = (),
        *,
        fill: int = 0,
        description: str = "",
        share_acl: str = "mirror",
        organic: bool = True,
        scenario: str | None = None,
    ) -> None:
        authored = tuple(folders)
        extra = _fill(
            area, fill, skip=[*(folder.rel for folder in authored), name], share=f"{server}\\{name}"
        )
        shares.append(
            ShareDef(
                server,
                name,
                area,
                _d(created),
                (*sys_admin, *root),
                authored + extra,
                description=description,
                share_acl=share_acl,
                organic=organic,
                scenario=scenario,
            )
        )

    # ---- GP-FS-01: the first file server, 2016 -------------------------------------------
    share(
        "GP-FS-01",
        "Shared",
        "shared",
        "2016-10-12",
        [a("FS-Legacy-Users", "M"), a("Shared_Drive_Users", "M")],
        [
            FolderDef("Public", (a("EVERYONE", "RX"),)),
            FolderDef("Team-Drops", (a("All Staff", "M"),)),
        ],
        fill=4,
        share_acl="everyone",
        description="The original company share.",
    )
    share(
        "GP-FS-01",
        "Departments",
        "shared",
        "2016-10-12",
        [a("All Staff", "L", CI), a("DOMAIN_USERS", "RX", THIS_ONLY)],
        [
            FolderDef("Engineering", (a("Share_Engineering_Modify", "M"),), protected=True),
            FolderDef("Finance", (a("Finance-RW", "M"), a("Finance_Read", "RX")), protected=True),
            FolderDef("Sales", (a("Sales-RW", "M"),)),
            FolderDef("Operations", (a("Ops-Share-RW", "M"),)),
            FolderDef("Marketing", (a("Marketing-All", "M"),)),
            FolderDef("IT", (a("IT-Share-Admins", "F"),)),
        ],
        share_acl="everyone",
        description="Department folders from before departments had servers of their own.",
    )
    share(
        "GP-FS-01",
        "Scans",
        "shared",
        "2017-09-11",
        [a("Scanner-Users", "M"), a("AUTHENTICATED_USERS", "RX")],
        [
            FolderDef("Finance-Scans", (a("Finance-Team", "M"),), protected=True),
            FolderDef(
                "HR-Scans",
                (a("HR Staff", "M"), a(long_gone[0], "M", intent=AceIntent.FORMER_USER)),
                protected=True,
            ),
        ],
        fill=1,
        share_acl="authenticated",
        description="Scan-to-folder target.",
    )
    share(
        "GP-FS-01",
        "Software",
        "it",
        "2016-11-21",
        [a("GG_IT_Software_R", "RX"), a("IT-Share-Admins", "M")],
        fill=3,
        description="Installers.",
    )

    # ---- GP-CORP-FS01 -------------------------------------------------------------------
    share(
        "GP-CORP-FS01",
        "Departments",
        "shared",
        "2016-11-07",
        [a("All Staff", "RX", CI), a("DOMAIN_USERS", "RX", THIS_ONLY)],
        [
            FolderDef("Facilities", (a("Engineering-FS", "RX"), a("Facilities_RW", "M"))),
            FolderDef("Customer-Success", (a("CS-Share-RW", "M"),)),
            FolderDef("Product", (a("Product-FS", "M"),)),
            FolderDef("People", (a("HR_Share_RW", "M"),), protected=True),
            FolderDef("Collaboration", (a("All Staff", "M"),)),
        ],
        share_acl="authenticated",
        description="Departmental collaboration, second edition.",
    )
    share(
        "GP-CORP-FS01",
        "Corporate",
        "operations",
        "2016-11-07",
        [a("All Staff", "RX"), a("Ops-Share-RW", "M")],
        [
            FolderDef("Policies"),
            FolderDef("Brand-Kit", (a("FS-Marketing-RW", "M"),)),
            FolderDef("Org-Charts", (a("HR_Share_RW", "M"),)),
        ],
        fill=2,
    )
    share(
        "GP-CORP-FS01",
        "Templates",
        "shared",
        "2017-02-20",
        [a("All Staff", "RX"), a("Ops-Share-RW", "M")],
        fill=3,
    )

    # ---- GP-ENG-FS01: Scenario C, G and H ----------------------------------------------
    share(
        "GP-ENG-FS01",
        "Engineering",
        "engineering",
        "2017-01-16",
        [
            a("Share_Engineering_Modify", "M"),
            a("Share_Engineering_Read", "RX"),
            a("Engineering-FS", "M"),
            a("sec-share-engineering-write", "M"),
            a("sec-share-engineering-read-only", "RX"),
            a("CREATOR_OWNER", "F", INHERIT_ONLY_OI_CI),
        ],
        [
            FolderDef("Services", organic=False),
            FolderDef("Services/Payments", (a("ENG-Payments", "M"),), scenario="C"),
            FolderDef("Services/Platform", (a("ENG-Platform", "M"), a("PRJ_Nebula", "M"))),
            FolderDef("Services/Identity", (a("ENG-Identity", "M"),)),
            FolderDef(
                "Services/API",
                (
                    a("ENG-API", "M"),
                    a(veteran_engineers[0], "M", intent=AceIntent.HISTORICAL_ENGINEER),
                ),
            ),
            FolderDef("Runbooks", organic=False),
            FolderDef(
                "Runbooks/Payments-Legacy",
                (a("Share_Engineering_Modify", "M"),),
                protected=True,
                scenario="G",
            ),
            FolderDef("Releases", (a("ENG-QA", "M"), a("Eng-Leads", "F"))),
            FolderDef("Tools", organic=False),
            FolderDef(
                "Tools/Build",
                (
                    a(veteran_engineers[1], "M", intent=AceIntent.HISTORICAL_ENGINEER),
                    a(veteran_engineers[2], "M", intent=AceIntent.HISTORICAL_ENGINEER),
                ),
            ),
            FolderDef("Teams", organic=False),
            FolderDef("Teams/Mobile", (a("ENG-Mobile", "M"),)),
            FolderDef("Teams/Web", (a("ENG-Web", "M"), a("Engineering-Frontend", "M"))),
            FolderDef("Teams/SRE", (a("ENG-SRE", "M"), a("Okta-AWS-Admins", "M"))),
            FolderDef("Projects", organic=False),
            FolderDef(
                "Projects/Apollo",
                (
                    a("Proj-Apollo", "M"),
                    a(veteran_engineers[3], "M", intent=AceIntent.HISTORICAL_ENGINEER),
                    a(long_gone[1], "M", intent=AceIntent.FORMER_USER),
                ),
            ),
            FolderDef("Projects/Nebula", (a("PRJ_Nebula", "M"),)),
            FolderDef("Projects/Orion", (a("PRJ_Orion", "M"),)),
            FolderDef("Projects/Zephyr", (a("Proj-Zephyr-RW", "M"),), scenario="H"),
            FolderDef(
                "Oncall", (a("ENG-Payments-Oncall", "M"), a("Okta_PagerDuty_Responders", "RX"))
            ),
        ],
        fill=6,
        scenario="C",
        description="Engineering since 2017.",
    )
    share(
        "GP-ENG-FS01",
        "Builds",
        "engineering",
        "2017-06-05",
        [a("Engineering-FS", "M"), a("APP-GitHub-Users", "M"), a("Share_Engineering_Read", "RX")],
        [
            FolderDef("Nightly"),
            FolderDef("Release-Candidates", (a("ENG-QA", "M"),)),
            FolderDef(
                "Signing-Keys",
                (
                    a("ENG-SRE", "F"),
                    a(veteran_engineers[0], "F", intent=AceIntent.HISTORICAL_ENGINEER),
                ),
                protected=True,
            ),
        ],
        fill=1,
    )
    share(
        "GP-ENG-FS01",
        "Projects",
        "projects",
        "2017-08-07",
        [a("Engineering-FS", "M"), a("Managers", "RX")],
        [
            FolderDef("Apollo", (a("Proj-Apollo", "M"),)),
            FolderDef("Zephyr", (a("Proj-Zephyr-RW", "M"),), scenario="H"),
        ],
        fill=2,
    )

    # ---- GP-FS-02 -----------------------------------------------------------------------
    share(
        "GP-FS-02",
        "Projects",
        "projects",
        "2017-03-06",
        [a("FS-Legacy-Users", "RX"), a("Managers", "M")],
        fill=5,
        share_acl="everyone",
    )
    share(
        "GP-FS-02",
        "Training",
        "shared",
        "2017-05-15",
        [a("All Staff", "RX"), a("HR_Share_RW", "M")],
        fill=3,
    )
    share(
        "GP-FS-02",
        "Team-Space",
        "shared",
        "2018-02-05",
        [a("All Staff", "M")],
        fill=4,
        share_acl="authenticated",
    )

    # ---- GP-FIN-FS01: Scenario B and F --------------------------------------------------
    share(
        "GP-FIN-FS01",
        "Finance",
        "finance",
        "2017-04-03",
        [
            a("Finance-RW", "M"),
            a("Finance_Read", "RX"),
            a("GG_Finance_RW", "M"),
            a("FS-Finance-R", "R"),
            a("sec-share-finance-write", "M"),
            a("sec-share-finance-read-only", "RX"),
            a("DOMAIN_USERS", "RX", THIS_ONLY),
        ],
        [
            FolderDef("Accounting", organic=False, scenario="B"),
            FolderDef("Accounting/Close", (a("FIN-Close-Team", "M"),), scenario="B"),
            FolderDef(
                "Accounting/Close/FY2025",
                (
                    a(accountants[0], "M", intent=AceIntent.FINANCE_CLOSE),
                    a(accountants[1], "M", intent=AceIntent.FINANCE_CLOSE),
                    a("Finance-Close-FY2024", "M"),
                ),
            ),
            FolderDef(
                "Accounting/Close/FY2026-Q3",
                (a(accountants[2], "M", intent=AceIntent.FINANCE_CLOSE),),
                created=_d("2026-09-08"),
            ),
            FolderDef("Accounting/AP", organic=False),
            FolderDef("Accounting/AR", organic=False),
            FolderDef(
                "Budgets", (a("Managers", "RX"), a(controller, "F", intent=AceIntent.FINANCE_CLOSE))
            ),
            FolderDef("Reports", organic=False),
            FolderDef(
                "Reports/Revenue",
                (a("Revenue-Reporting", "RX"), a(mover, "M", intent=AceIntent.NESTING_CONFUSION)),
                scenario="F",
            ),
            FolderDef(
                "Reports/Board",
                (
                    a("Board-Materials-R", "RX"),
                    a("Finance-RW", "M"),
                    a(assistant_1, "M", intent=AceIntent.EXECUTIVE_ASSISTANT),
                ),
                protected=True,
            ),
            FolderDef("Commissions", (a("GG_Sales", "RX"),)),
            FolderDef(
                "Audit", (a("GG_Finance_RW", "RX"), a("FIN-Close-Approvers", "M")), protected=True
            ),
            FolderDef("Tax"),
            FolderDef("Treasury"),
        ],
        scenario="B",
        description="Finance since 2017. Migration to sec-share-finance-* "
        "started in 2024 and is not finished.",
    )
    share(
        "GP-FIN-FS01",
        "Payroll",
        "finance",
        "2017-06-12",
        [a("Payroll_Admins", "M"), a("FIN-Payroll", "M")],
        [
            FolderDef("2025", organic=False),
            FolderDef("2026", organic=False),
            # The payroll vendor's file drop ran as a machine account in 2023, and the
            # quickest way to let it write was every authenticated account. Never reverted.
            FolderDef(
                "Vendor-Migration-2023",
                (
                    a(hr_ops, "M", intent=AceIntent.MIGRATION_LEFTOVER),
                    a(recently_left[0], "M", intent=AceIntent.FORMER_USER),
                    a("AUTHENTICATED_USERS", "M"),
                ),
            ),
        ],
        description="Payroll. Sensitive.",
    )
    share(
        "GP-FIN-FS01",
        "Accounting",
        "finance",
        "2018-03-12",
        [a("GG_Finance_RW", "M"), a("FS-Finance-R", "RX")],
        fill=4,
    )
    share(
        "GP-FIN-FS01",
        "Audit",
        "finance",
        "2019-02-11",
        [a("FIN-Close-Approvers", "M", since=_d("2022-08-01")), a("FS-Finance-R", "RX")],
        [
            FolderDef(
                "External-Auditors",
                (a(contractor_manager, "RX", intent=AceIntent.TEMPORARY_PROJECT),),
            )
        ],
        fill=2,
    )

    # ---- GP-HR-FS01: Scenario D ---------------------------------------------------------
    # People finished its move to the standard in 2024: HR_Share_RW was taken off the root
    # when sec-share-people-* went on, which is what "well controlled" looks like here.
    share(
        "GP-HR-FS01",
        "People",
        "people",
        "2018-01-22",
        [a("sec-share-people-write", "M"), a("sec-share-people-read-only", "RX")],
        [
            FolderDef(
                "Employee-Relations", (a("HR-Confidential", "M"),), protected=True, organic=False
            ),
            FolderDef("Benefits", organic=False),
            FolderDef("Recruiting", (a("Hiring-Managers", "RX"),), organic=False, scenario="D"),
            FolderDef(
                "Compensation",
                (a("HR-Confidential", "M"), a("Payroll_Admins", "RX")),
                protected=True,
                organic=False,
            ),
            FolderDef(
                "Compensation/Payroll-Vendor-Transition",
                (a(payroll_specialist, "M", intent=AceIntent.TEMPORARY_PROJECT),),
                organic=False,
                scenario="D",
            ),
            FolderDef("Onboarding", organic=False),
            FolderDef("Learning", organic=False),
        ],
        organic=False,
        scenario="D",
        description="People team. Mostly well controlled.",
    )
    share(
        "GP-HR-FS01",
        "Recruiting",
        "people",
        "2018-03-05",
        [a("HR-Recruiting", "M"), a("Hiring-Managers", "RX")],
        fill=3,
    )
    share("GP-HR-FS01", "Benefits", "people", "2019-01-07", [a("HR_Share_RW", "M")], fill=2)

    # ---- GP-SALES-FS01 -------------------------------------------------------------------
    share(
        "GP-SALES-FS01",
        "Sales",
        "sales",
        "2018-02-19",
        [
            a("Sales-RW", "M"),
            a("Sales_Read", "RX"),
            a("sec-share-sales-write", "M"),
            a("sec-share-sales-read-only", "RX"),
        ],
        [
            FolderDef(
                "Forecasts",
                (
                    a("Sales-Managers", "M"),
                    a(
                        sales_forecaster,
                        "M",
                        intent=AceIntent.TEMPORARY_PROJECT,
                        until=_d("2026-09-14"),
                    ),
                ),
            )
        ],
        fill=7,
    )
    share(
        "GP-SALES-FS01",
        "Proposals",
        "sales",
        "2018-05-14",
        [a("GG_Sales", "M"), a("Sales_Read", "RX")],
        fill=4,
    )
    share(
        "GP-SALES-FS01",
        "Commissions",
        "sales",
        "2019-01-28",
        [a("Sales-Managers", "M"), a("FIN-FPandA", "M"), a("GG_Sales", "RX")],
        fill=2,
        description="Every seller can read every commission statement.",
    )

    # ---- GP-FS-03: fails outright in the third scan ------------------------------------
    share(
        "GP-FS-03",
        "Collaboration",
        "shared",
        "2018-04-09",
        [a("All Staff", "M"), a("FS-Legacy-Users", "M")],
        fill=5,
        share_acl="authenticated",
    )
    share(
        "GP-FS-03",
        "Training",
        "shared",
        "2018-06-04",
        [a("All Staff", "RX"), a("HR_Share_RW", "M")],
        fill=3,
    )
    share(
        "GP-FS-03",
        "Events",
        "marketing",
        "2019-03-04",
        [a("FS-Marketing-RW", "M"), a("All Staff", "RX")],
        fill=3,
    )

    # ---- GP-LEGAL-FS01 -----------------------------------------------------------------
    share(
        "GP-LEGAL-FS01",
        "Legal",
        "legal",
        "2018-06-18",
        [
            a("Legal-RW", "M"),
            a("sec-share-legal-write", "M", since=_d("2026-09-11")),
            a("sec-share-legal-read-only", "RX", since=_d("2026-09-11")),
        ],
        [
            FolderDef(
                "Litigation-Hold",
                (a("Legal_Dept", "DEL", deny=True), a("Legal-RW", "M")),
                protected=True,
            ),
            FolderDef("Contracts", (a("GG_Legal_Contracts_R", "RX"),)),
            FolderDef(
                "Vendor-Review",
                (
                    a(
                        contractor_manager,
                        "M",
                        intent=AceIntent.TEMPORARY_PROJECT,
                        until=_d("2026-09-14"),
                    ),
                ),
            ),
        ],
        fill=4,
        description="Legal. Standard groups added in September 2026.",
    )
    share(
        "GP-LEGAL-FS01",
        "Contracts",
        "legal",
        "2019-04-22",
        [a("Legal-RW", "M"), a("GG_Legal_Contracts_R", "RX")],
        fill=4,
    )

    # ---- GP-MKT-FS01 --------------------------------------------------------------------
    share(
        "GP-MKT-FS01",
        "Marketing",
        "marketing",
        "2018-09-24",
        [
            a("FS-Marketing-RW", "M"),
            a("FS-Marketing-R", "RX"),
            a("sec-share-marketing-write", "M"),
            a("sec-share-marketing-read-only", "RX"),
        ],
        [
            FolderDef(
                "Campaigns",
                (
                    a("All Staff", "RX", until=_d("2026-09-15")),
                    a("MKT-Demand-Gen", "M", since=_d("2026-09-15")),
                ),
            )
        ],
        fill=6,
        scenario="H-alt",
    )
    share(
        "GP-MKT-FS01",
        "Brand",
        "marketing",
        "2018-09-24",
        [a("Design-Assets-RW", "M"), a("FS-Marketing-RW", "M"), a("All Staff", "RX")],
        fill=3,
    )
    share(
        "GP-MKT-FS01",
        "Events",
        "marketing",
        "2019-02-25",
        [a("MKT-Events", "M"), a("FS-Marketing-R", "RX")],
        fill=3,
    )

    # ---- GP-OPS-FS01 --------------------------------------------------------------------
    share(
        "GP-OPS-FS01",
        "Operations",
        "operations",
        "2019-01-14",
        [
            a("Ops-Share-RW", "M"),
            a("sec-share-operations-write", "M"),
            a("sec-share-operations-read-only", "RX"),
        ],
        fill=6,
    )
    share(
        "GP-OPS-FS01",
        "Facilities",
        "operations",
        "2019-01-14",
        [a("Facilities_RW", "M"), a("All Staff", "RX")],
        fill=3,
    )
    share(
        "GP-OPS-FS01",
        "Procurement",
        "operations",
        "2019-05-06",
        [a("OPS-Procurement", "M"), a("Finance_Read", "RX")],
        fill=3,
    )

    # ---- GP-ENG-FS02 --------------------------------------------------------------------
    share(
        "GP-ENG-FS02",
        "Engineering-Archive",
        "engineering",
        "2019-03-11",
        [a("Engineering-FS", "RX"), a("Eng-Leads", "M")],
        fill=4,
    )
    share(
        "GP-ENG-FS02",
        "QA",
        "engineering",
        "2019-03-11",
        [a("ENG-QA", "M"), a("Engineering-FS", "RX")],
        [FolderDef("Sprint-Scratch", deleted=_d("2026-09-12"))],
        fill=3,
    )
    share(
        "GP-ENG-FS02",
        "Releases",
        "engineering",
        "2019-10-07",
        [a("Engineering-FS", "M"), a("Okta_GitHub_Admin", "F")],
        fill=3,
    )

    # ---- GP-ARCH-FS01: Scenario E -------------------------------------------------------
    share(
        "GP-ARCH-FS01",
        "Archive",
        "archive",
        "2019-06-24",
        [
            a("GG_Archive_R", "RX"),
            a("FS-Legacy-Users", "RX"),
            a("sec-share-archive-read-only", "RX"),
            a("IT-Share-Admins", "M"),
        ],
        [
            FolderDef(
                "Finance-2017",
                (
                    a("Finance-RW", "M"),
                    a("Finance-RW", "RX"),
                    a(long_gone[2], "M", intent=AceIntent.FORMER_USER),
                ),
                protected=True,
                scenario="E",
            ),
            FolderDef(
                "HR-2018",
                (a("HR_Share_RW", "M"), a(recently_left[1], "M", intent=AceIntent.FORMER_USER)),
                protected=True,
                scenario="E",
            ),
            FolderDef(
                "Projects-2019",
                (a("Proj-Zephyr-RW", "M"), a("Project-Vega", "M")),
                protected=True,
                scenario="E",
            ),
            FolderDef("Engineering-2018", organic=False),
            FolderDef(
                "Legacy-Home-Drives",
                (
                    a(long_gone[3], "F", intent=AceIntent.FORMER_USER),
                    a(crossing, "F", intent=AceIntent.FORMER_USER),
                    a(it_migrators[0], "F", intent=AceIntent.MIGRATION_LEFTOVER),
                ),
                protected=True,
                scenario="E",
            ),
            FolderDef(
                "Scans", (a("AUTHENTICATED_USERS", "RX"), a("FS-Legacy-Users", "RW", deny=True))
            ),
        ],
        scenario="E",
        organic=False,
        description="Everything nobody would delete.",
    )
    share(
        "GP-ARCH-FS01",
        "Archive-Finance",
        "archive",
        "2019-06-24",
        [a("GG_Archive_R", "RX"), a("Finance-RW", "M"), a("Finance_Read", "RX")],
        fill=4,
    )
    share(
        "GP-ARCH-FS01",
        "Archive-HR",
        "archive",
        "2019-08-12",
        [a("HR_Share_RW", "M"), a("GG_Archive_R", "RX")],
        fill=3,
    )

    # ---- GP-DATA-FS01 -------------------------------------------------------------------
    share(
        "GP-DATA-FS01",
        "Data",
        "data",
        "2020-01-20",
        [
            a("Data-Team", "M"),
            a("Share-Data-RW", "M", since=_d("2022-02-07")),
            a("DATA_Share_Read", "RX", since=_d("2022-02-07")),
            a("sec-share-data-write", "M"),
            a("sec-share-data-read-only", "RX"),
        ],
        [
            FolderDef(
                "Brindlewood-Models",
                (
                    a("Acq-Brindlewood-RW", "M"),
                    a(acquired[0], "F", intent=AceIntent.ACQUISITION_MIGRATION),
                ),
            )
        ],
        fill=6,
    )
    share(
        "GP-DATA-FS01",
        "Analytics",
        "data",
        "2020-01-20",
        [a("Team_Analytics", "M"), a("APP-Tableau-Viewers", "RX")],
        fill=4,
    )
    share(
        "GP-DATA-FS01",
        "Exports",
        "data",
        "2020-06-22",
        [a("Data-Team", "M"), a("Revenue-Reporting", "RX")],
        fill=3,
    )

    # ---- GP-CS-FS01 ---------------------------------------------------------------------
    share(
        "GP-CS-FS01",
        "CustomerSuccess",
        "customer-success",
        "2020-02-24",
        [
            a("CS-Share-RW", "M"),
            a("CS_Share_Read", "RX", since=_d("2022-11-07")),
            a("sec-share-customer-success-write", "M"),
            a("sec-share-customer-success-read-only", "RX"),
        ],
        fill=6,
    )
    share(
        "GP-CS-FS01",
        "Support",
        "customer-success",
        "2020-02-24",
        [a("CS-Support-T1", "M"), a("CS-Support-T2", "M"), a("Zendesk-Agents", "M")],
        fill=4,
    )
    share(
        "GP-CS-FS01",
        "Onboarding",
        "customer-success",
        "2020-06-15",
        [a("CS-Onboarding", "M"), a("CS-All", "RX")],
        fill=3,
    )

    # ---- GP-IT-FS01 ---------------------------------------------------------------------
    share(
        "GP-IT-FS01",
        "IT",
        "it",
        "2020-03-16",
        [
            a("IT-Share-Admins", "M"),
            a("sec-share-it-write", "M"),
            a("sec-share-it-read-only", "RX"),
        ],
        fill=6,
    )
    share(
        "GP-IT-FS01",
        "Software",
        "it",
        "2020-03-16",
        [a("GG_IT_Software_R", "RX"), a("IT-Helpdesk", "M")],
        fill=3,
    )
    share(
        "GP-IT-FS01",
        "Scripts",
        "it",
        "2020-07-06",
        [a("IT-Staff", "M"), a("FS-Admins", "F")],
        fill=3,
    )

    # ---- GP-PROD-FS01 -------------------------------------------------------------------
    share(
        "GP-PROD-FS01",
        "Product",
        "product",
        "2020-05-18",
        [
            a("Product-FS", "M"),
            a("sec-share-product-write", "M"),
            a("sec-share-product-read-only", "RX"),
        ],
        fill=6,
    )
    share(
        "GP-PROD-FS01",
        "Research",
        "product",
        "2020-05-18",
        [a("Product Team", "M"), a("Design_Team", "RX")],
        [FolderDef("Vega", (a("Project-Vega", "M"),))],
        fill=3,
    )
    share(
        "GP-PROD-FS01",
        "Roadmaps",
        "product",
        "2021-01-11",
        [a("PRD-Core", "M"), a("Managers", "RX")],
        fill=3,
    )

    # ---- GP-SEC-FS01: not readable in the first scan -----------------------------------
    share(
        "GP-SEC-FS01",
        "Security",
        "security",
        "2020-09-21",
        [
            a("Security-Team", "M"),
            a("SEC-Share-Restricted", "M", since=_d("2022-09-12")),
            a("sec-share-security-write", "M"),
            a("sec-share-security-read-only", "RX"),
        ],
        fill=5,
    )
    share(
        "GP-SEC-FS01",
        "Incidents",
        "security",
        "2020-09-21",
        [a("SEC-SOC", "M"), a("SEC-Share-Restricted", "M", since=_d("2022-09-12"))],
        [
            FolderDef("IR-2024-007", (a(responders[0], "M", intent=AceIntent.INCIDENT_ACCESS),)),
            FolderDef(
                "IR-2026-014",
                (
                    a(responders[0], "M", intent=AceIntent.INCIDENT_ACCESS),
                    a(responders[1], "M", intent=AceIntent.INCIDENT_ACCESS),
                ),
                created=_d("2026-09-24"),
            ),
        ],
        fill=1,
    )
    share(
        "GP-SEC-FS01",
        "Audits",
        "security",
        "2021-03-01",
        [a("Security-Team", "M"), a("FIN-Close-Approvers", "RX", since=_d("2022-08-01"))],
        fill=3,
    )

    # ---- GP-MIG-FS01: partial in the third scan ----------------------------------------
    share(
        "GP-MIG-FS01",
        "Migrations",
        "migrations",
        "2020-11-09",
        [a("IT-Share-Admins", "M"), a("FS-Admins", "F")],
        [
            FolderDef(
                "FS-Consolidation-2020",
                (
                    a("FS-Legacy-Users", "M"),
                    a(it_migrators[1], "F", intent=AceIntent.MIGRATION_LEFTOVER),
                    a(it_migrators[2], "F", intent=AceIntent.MIGRATION_LEFTOVER),
                ),
                protected=True,
                scenario="E",
            ),
            FolderDef(
                "FS-Consolidation-2020/Restricted",
                (a("FS-Admins", "F"),),
                protected=True,
                unreadable_from=_d("2026-09-24"),
            ),
            FolderDef(
                "Okta-Cutover-2021",
                (
                    a("AD-Okta-Sync-All", "RX"),
                    a(it_migrators[0], "M", intent=AceIntent.MIGRATION_LEFTOVER),
                ),
            ),
            FolderDef("Wave-3-Staging", deleted=_d("2026-09-25")),
        ],
        fill=4,
        scenario="E",
    )
    share(
        "GP-MIG-FS01",
        "Staging",
        "migrations",
        "2021-02-01",
        [a("FS-Admins", "F"), a("IT-Staff", "M")],
        fill=3,
    )

    # ---- GP-ENG-FS03 --------------------------------------------------------------------
    share(
        "GP-ENG-FS03",
        "Engineering-Platform",
        "engineering",
        "2021-02-15",
        [a("Engineering-Backend", "M"), a("ENG-Platform", "M"), a("APP-AWS-Console", "RX")],
        fill=5,
    )
    share(
        "GP-ENG-FS03",
        "ML-Models",
        "data",
        "2021-02-15",
        [a("Team_ML", "M"), a("Engineering-Backend", "RX")],
        fill=4,
    )

    # ---- GP-ACQ-FS01 --------------------------------------------------------------------
    share(
        "GP-ACQ-FS01",
        "Acquisitions",
        "acquisitions",
        "2021-04-12",
        [a("Executives", "M"), a("Acq-Brindlewood-RW", "M"), a("Legal-RW", "M")],
        [
            FolderDef(
                "Brindlewood-Analytics",
                (
                    a("BA-Migrated-Users", "M"),
                    a(acquired[1], "F", intent=AceIntent.ACQUISITION_MIGRATION),
                    a(acquired[2], "M", intent=AceIntent.ACQUISITION_MIGRATION),
                ),
                protected=True,
            )
        ],
        fill=4,
    )
    share(
        "GP-ACQ-FS01",
        "Brindlewood-Analytics",
        "acquisitions",
        "2021-04-12",
        [a("BA-Migrated-Users", "M"), a("Data-Team", "RX"), a("Acq-Brindlewood-RW", "M")],
        fill=4,
        description="The acquired company's file server, migrated as it was.",
    )

    # ---- GP-FS-04 -----------------------------------------------------------------------
    share(
        "GP-FS-04",
        "Collaboration",
        "shared",
        "2021-06-14",
        [a("Remote-Workers-2020", "M"), a("All Staff", "RX")],
        fill=5,
    )
    share(
        "GP-FS-04",
        "Shared-Projects",
        "projects",
        "2021-06-14",
        [a("PRJ_Orion", "M"), a("PRJ_Nebula", "M"), a("Managers", "RX")],
        fill=4,
    )

    # ---- GP-EXEC-FS01 -------------------------------------------------------------------
    share(
        "GP-EXEC-FS01",
        "Executive",
        "executive",
        "2022-03-14",
        [
            a("Exec-Share", "M"),
            a("sec-share-executive-write", "M"),
            a("sec-share-executive-read-only", "RX"),
            a(assistant_1, "M", intent=AceIntent.EXECUTIVE_ASSISTANT),
            a(assistant_2, "M", intent=AceIntent.EXECUTIVE_ASSISTANT),
        ],
        fill=5,
    )
    share(
        "GP-EXEC-FS01",
        "Board",
        "executive",
        "2022-03-14",
        [
            a("Board-Materials-R", "RX"),
            a("Exec-Share", "M"),
            a(assistant_2, "M", intent=AceIntent.EXECUTIVE_ASSISTANT),
        ],
        [FolderDef("Minutes", (a(assistant_1, "RX", intent=AceIntent.EXECUTIVE_ASSISTANT),))],
        fill=3,
    )

    # ---- GP-PROJ-FS01 -------------------------------------------------------------------
    share(
        "GP-PROJ-FS01",
        "Projects-2022",
        "projects",
        "2022-06-20",
        [a("PRJ-Atlas-2022", "M"), a("Managers", "RX")],
        fill=4,
    )
    share(
        "GP-PROJ-FS01",
        "Atlas",
        "projects",
        "2022-06-20",
        [a("PRJ-Atlas-2022", "M"), a("sec-share-projects-read-only", "RX")],
        fill=4,
    )
    share(
        "GP-PROJ-FS01",
        "Projects",
        "projects",
        "2024-08-12",
        [a("sec-share-projects-write", "M"), a("sec-share-projects-read-only", "RX")],
        [FolderDef("Helios", (a("PRJ-Helios", "M"),))],
        fill=4,
    )

    # ---- GP-SALES-FS02 ------------------------------------------------------------------
    share(
        "GP-SALES-FS02",
        "Sales-Ops",
        "sales",
        "2022-09-19",
        [
            a("Sales-Ops", "M"),
            a("Revenue-Reporting", "RX"),
            a("sec-share-sales-read-only", "RX", since=_d("2024-07-15")),
        ],
        fill=5,
    )
    share(
        "GP-SALES-FS02",
        "Deal-Desk",
        "sales",
        "2022-09-19",
        [a("Sales-Managers", "M"), a("GG_Legal_Contracts_R", "RX")],
        fill=3,
    )

    # ---- GP-FIN-FS02 --------------------------------------------------------------------
    share(
        "GP-FIN-FS02",
        "Finance-Planning",
        "finance",
        "2023-03-13",
        [
            a("FIN-FPandA", "M"),
            a("Finance_Read", "RX"),
            a("sec-share-finance-write", "M"),
            a("sec-share-finance-read-only", "RX"),
        ],
        fill=5,
    )
    share(
        "GP-FIN-FS02",
        "Treasury",
        "finance",
        "2023-03-13",
        [a("FIN-Close-Approvers", "M"), a("sec-share-finance-read-only", "RX")],
        fill=3,
    )

    # ---- GP-CORP-FS02: Scenario A --------------------------------------------------------
    share(
        "GP-CORP-FS02",
        "Design",
        "design",
        "2023-11-13",
        [a("sec-share-design-read-only", "RX"), a("sec-share-design-write", "M")],
        [
            FolderDef(name, organic=False, scenario="A")
            for name in (
                "Product-Design",
                "Brand",
                "Research",
                "Research/Interviews",
                "Handoff",
                "Components",
                "Archive",
            )
        ],
        organic=False,
        scenario="A",
        description="Built to the 2023 standard.",
    )
    share(
        "GP-CORP-FS02",
        "Product-Collab",
        "product",
        "2024-03-11",
        [a("sec-share-product-write", "M"), a("sec-share-product-read-only", "RX")],
        fill=5,
    )

    # ---- GP-SHARE-FS01: the standard's departmental shares ------------------------------
    for slug, created in (
        ("it", "2024-02-19"),
        ("security", "2024-02-19"),
        ("data", "2024-03-04"),
        ("people", "2024-10-21"),
        ("customer-success", "2024-06-17"),
        ("operations", "2024-09-16"),
        ("marketing", "2024-05-20"),
    ):
        folder = DEPARTMENTS_BY_SLUG[slug].name.replace(" / ", "-").replace(" ", "-")
        share(
            "GP-SHARE-FS01",
            folder,
            slug,
            created,
            [a(f"sec-share-{slug}-read-only", "RX"), a(f"sec-share-{slug}-write", "M")],
            fill=4,
        )

    # ---- GP-DATA-FS02 -------------------------------------------------------------------
    share(
        "GP-DATA-FS02",
        "Data-Platform",
        "data",
        "2024-05-20",
        [a("sec-share-data-write", "M"), a("sec-share-data-read-only", "RX")],
        fill=5,
    )
    share(
        "GP-DATA-FS02",
        "Warehouse-Exports",
        "data",
        "2024-05-20",
        [a("sec-share-data-write", "M"), a("sec-okta-snowflake-analyst", "RX")],
        fill=3,
    )

    # ---- GP-CS-FS02 ---------------------------------------------------------------------
    share(
        "GP-CS-FS02",
        "CS-Enablement",
        "customer-success",
        "2024-09-16",
        [
            a("sec-share-customer-success-write", "M"),
            a("sec-share-customer-success-read-only", "RX"),
        ],
        fill=4,
    )
    share(
        "GP-CS-FS02",
        "Support-KB",
        "customer-success",
        "2024-09-16",
        [a("sec-share-customer-success-write", "M"), a("sec-okta-zendesk-agent", "RX")],
        fill=3,
    )

    # ---- GP-ENG-FS04 --------------------------------------------------------------------
    share(
        "GP-ENG-FS04",
        "Engineering-Docs",
        "engineering",
        "2025-01-20",
        [a("sec-share-engineering-write", "M"), a("sec-share-engineering-read-only", "RX")],
        fill=6,
    )
    share(
        "GP-ENG-FS04",
        "Runbooks",
        "engineering",
        "2025-01-20",
        [a("sec-share-engineering-write", "M"), a("sec-okta-pagerduty-responder", "RX")],
        fill=4,
    )

    # ---- GP-LEGAL-FS02 ------------------------------------------------------------------
    share(
        "GP-LEGAL-FS02",
        "Legal-Matters",
        "legal",
        "2025-03-17",
        [
            a("Legal-RW", "M"),
            a("sec-share-legal-write", "M", since=_d("2026-09-11")),
            a("sec-share-legal-read-only", "RX", since=_d("2026-09-11")),
        ],
        fill=4,
    )
    share(
        "GP-LEGAL-FS02",
        "Privacy",
        "legal",
        "2025-03-17",
        [a("Legal-RW", "M"), a("SEC-Share-Restricted", "RX")],
        fill=3,
    )

    # ---- GP-FS-05 -----------------------------------------------------------------------
    share(
        "GP-FS-05",
        "Cross-Functional",
        "shared",
        "2025-06-16",
        [a("sec-share-projects-write", "M"), a("sec-share-projects-read-only", "RX")],
        fill=4,
    )
    share(
        "GP-FS-05",
        "Shared-Modern",
        "shared",
        "2025-06-16",
        [a("sec-share-operations-write", "M"), a("sec-share-operations-read-only", "RX")],
        fill=3,
    )

    return tuple(shares)


# ---------------------------------------------------------------------------
# The organic roll.
# ---------------------------------------------------------------------------

#: How a folder's ACL came to be, by the era of its share.
_ROLL_WEIGHTS: Final[dict[Era, tuple[tuple[str, float], ...]]] = {
    Era.LEGACY: (
        ("inherit", 0.40),
        ("add-group", 0.23),
        ("protect-groups", 0.10),
        ("add-user", 0.12),
        ("protect-mixed", 0.10),
        ("protect-users", 0.05),
    ),
    Era.TEAM: (
        ("inherit", 0.48),
        ("add-group", 0.22),
        ("protect-groups", 0.08),
        ("add-user", 0.11),
        ("protect-mixed", 0.07),
        ("protect-users", 0.04),
    ),
    Era.TRANSITIONAL: (
        ("inherit", 0.55),
        ("add-group", 0.21),
        ("protect-groups", 0.07),
        ("add-user", 0.09),
        ("protect-mixed", 0.05),
        ("protect-users", 0.03),
    ),
    Era.STANDARD: (
        ("inherit", 0.68),
        ("add-group", 0.20),
        ("protect-groups", 0.04),
        ("add-user", 0.05),
        ("protect-mixed", 0.02),
        ("protect-users", 0.01),
    ),
}

_GROUP_LEVELS: Final = (("RX", 0.55), ("M", 0.35), ("R", 0.06), ("F", 0.04))
_USER_LEVELS: Final = (("M", 0.6), ("RX", 0.3), ("RW", 0.05), ("F", 0.05))


def _area_department(area: str) -> str:
    return _AREA_DEPARTMENT.get(area, area)


def _group_pool(canon: Canon, share: ShareDef) -> list[str]:
    department = _area_department(share.area)
    pool = [
        group.name
        for group in canon.groups
        if group.category not in (Category.BUILTIN, Category.DISTRIBUTION, Category.BROAD)
        and group.department == department
        and group.deleted is None
        and group.name not in ("Proj-Zephyr-RW",)
    ]
    return sorted(pool, key=legacy_key)


def _user_pool(canon: Canon, share: ShareDef) -> list[tuple[AceIntent, list[Employee]]]:
    department = _area_department(share.area)
    active = [person for person in canon.employees if person.is_active]
    same = [person for person in active if person.department == department]
    other = [
        person
        for person in active
        if person.department != department and person.level in (Level.IC, Level.MANAGER)
    ]
    pools: list[tuple[AceIntent, list[Employee]]] = []
    if share.area == "finance":
        pools.append((AceIntent.FINANCE_CLOSE, [p for p in same if p.team == "accounting"]))
    if share.area == "executive":
        pools.append(
            (AceIntent.EXECUTIVE_ASSISTANT, list(canon.tagged("role:executive-assistant")))
        )
    if share.area == "engineering":
        pools.append(
            (AceIntent.HISTORICAL_ENGINEER, [p for p in same if p.hire_date < dt.date(2021, 1, 1)])
        )
    if share.area in ("acquisitions", "data"):
        pools.append(
            (
                AceIntent.ACQUISITION_MIGRATION,
                [p for p in active if ACQUISITION_TAG in p.tags],
            )
        )
    if share.area in ("migrations", "archive"):
        pools.append((AceIntent.MIGRATION_LEFTOVER, [p for p in active if p.department == "it"]))
    if share.area == "security":
        pools.append(
            (AceIntent.INCIDENT_ACCESS, [p for p in active if p.department in ("security", "it")])
        )
    pools.append((AceIntent.NESTING_CONFUSION, same))
    pools.append((AceIntent.TEMPORARY_PROJECT, other))
    return [(intent, people) for intent, people in pools if people]


def _formers_for(canon: Canon, share: ShareDef) -> list[Employee]:
    department = _area_department(share.area)
    return [
        person
        for person in canon.employees
        if not person.is_active
        and person.department == department
        and person.hire_date < SNAPSHOT - dt.timedelta(days=200)
    ]


def _pick_user(draws: Draws, canon: Canon, share: ShareDef) -> AceDef:
    if share.era in (Era.LEGACY, Era.TEAM) and draws.chance(0.10):
        formers = _formers_for(canon, share)
        if formers:
            return AceDef(
                draws.choice(formers).employee_id,
                draws.weighted(_USER_LEVELS),
                intent=AceIntent.FORMER_USER,
            )
    pools = _user_pool(canon, share)
    intent, people = pools[0] if draws.chance(0.55) else draws.choice(pools)
    return AceDef(draws.choice(people).employee_id, draws.weighted(_USER_LEVELS), intent=intent)


def _roll(canon: Canon, share: ShareDef, folder: FolderDef) -> tuple[tuple[AceDef, ...], bool]:
    """The organic ACL for one folder: ``(explicit entries, protected)``."""
    draws = Draws("folder", draw_key(share.unc).casefold(), draw_key(folder.rel).casefold())
    weights = _ROLL_WEIGHTS[share.era]
    if "/" in folder.rel:
        # Deeper folders are mostly created and left alone.
        weights = tuple(
            (kind, weight * (3.0 if kind == "inherit" else 1.0)) for kind, weight in weights
        )
    kind = draws.weighted(weights)
    groups = _group_pool(canon, share)
    copied = tuple(
        ace
        for ace in share.root
        if ace.trustee not in ("SYSTEM", "ADMINISTRATORS")
        and ace.flags != THIS_ONLY
        and ace.since is None
    )

    def group_ace() -> AceDef:
        if share.era in (Era.LEGACY, Era.TEAM) and draws.chance(0.08):
            # The shortcut: a group everybody is in, because the right one did not exist.
            return AceDef(
                draws.choice(("All Staff", "FS-Legacy-Users", "Managers")),
                draws.weighted((("M", 0.5), ("RX", 0.5))),
            )
        return AceDef(draws.choice(groups), draws.weighted(_GROUP_LEVELS))

    if kind == "inherit" or not groups:
        return (), False
    if kind == "add-group":
        return (group_ace(),), False
    if kind == "add-user":
        return (_pick_user(draws, canon, share),), False
    system = (AceDef("SYSTEM", "F"), AceDef("ADMINISTRATORS", "F"))
    if kind == "protect-groups":
        # Breaking inheritance in the Windows dialog copies the inherited entries as explicit
        # ones. Admins then add one; some later remove one of the copies.
        kept = copied[: max(1, len(copied) - draws.below(2))]
        return (*system, *kept, group_ace()), True
    if kind == "protect-mixed":
        return (
            *system,
            copied[0] if copied else group_ace(),
            group_ace(),
            _pick_user(draws, canon, share),
        ), True
    users = {_pick_user(draws, canon, share) for _ in range(1 + draws.below(3))}
    return (*system, *sorted(users, key=lambda ace: legacy_key(ace.trustee))), True


# ---------------------------------------------------------------------------
# The estate on a date.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FolderRecord:
    """What the generator knows about a folder that ADG is not told."""

    path: str
    share: str
    server: str
    area: str
    explicit: tuple[AceDef, ...]
    protected: bool
    organic: bool
    scenario: str | None
    posture: Posture
    assignment: str
    """``group``, ``direct-user``, ``mixed`` or ``inherited`` (no explicit entries)."""


@dataclass(frozen=True, slots=True)
class GeneseePressEstate:
    day: dt.date
    estate: DemoEstate
    folders: tuple[FolderRecord, ...]
    unreadable: tuple[str, ...]
    shares: tuple[ShareDef, ...] = field(repr=False, default=())


def _host_key(server: str) -> str:
    return server.casefold()


def _resolve_trustee(trustee: str, canon: Canon, server: str) -> str:
    if trustee in WELL_KNOWN:
        return WELL_KNOWN[trustee]
    if trustee == "DOMAIN_USERS":
        return canon.group("Domain Users").sid
    if trustee.startswith("GP-"):
        return canon.employees_by_id[trustee].sid
    return canon.group(trustee).sid


def _trustee_exists(trustee: str, canon: Canon, day: dt.date) -> bool:
    """Whether the trustee existed on ``day`` — or had existed and left its SID behind."""
    if trustee in WELL_KNOWN or trustee == "DOMAIN_USERS":
        return True
    if trustee.startswith("GP-"):
        return canon.employees_by_id[trustee].account_state(day) != "absent"
    return canon.group(trustee).created <= day


def _ace(definition: AceDef, sid: str) -> DemoAce:
    return DemoAce(
        trustee_sid=sid,
        access_mask=PERMISSIONS[definition.level],
        ace_flags=definition.flags,
        ace_type=AceType.DENY if definition.deny else AceType.ALLOW,
    )


def _classify(
    explicit: Sequence[AceDef], protected: bool, canon: Canon, area: str
) -> tuple[Posture, str]:
    people = [ace for ace in explicit if ace.trustee.startswith("GP-")]
    groups = [
        ace
        for ace in explicit
        if not ace.trustee.startswith("GP-")
        and ace.trustee not in ("SYSTEM", "ADMINISTRATORS", "CREATOR_OWNER")
    ]
    if not people and not groups:
        assignment = "inherited"
    elif people and groups:
        assignment = "mixed"
    elif people:
        assignment = "direct-user"
    else:
        assignment = "group"

    sensitive = area in ("finance", "people", "legal", "executive", "security")
    risky = False
    questionable = False
    for ace in people:
        person = canon.employees_by_id[ace.trustee]
        if not person.is_active or ace.level == "F":
            risky = True
        questionable = True
    for ace in groups:
        broad = ace.trustee in BROAD_GROUPS or ace.trustee in BROAD_WELL_KNOWN
        if broad and not ace.deny and ace.level in WRITE_LEVELS:
            risky = True
        elif broad and not ace.deny and sensitive:
            questionable = True
        if ace.trustee in ("Project-Vega",) or ace.trustee == "Proj-Zephyr-RW":
            questionable = True
        if ace.level == "F" and not ace.deny:
            questionable = True
    trustees = Counter(ace.trustee for ace in groups)
    if any(count > 1 for count in trustees.values()):
        questionable = True
    # Breaking inheritance to narrow a sensitive folder to groups is the design working.
    # Anywhere else, or with people on it, it is a place later changes will not reach.
    if protected and not (sensitive and groups and not people):
        questionable = True
    eras = {canon.group(ace.trustee).era for ace in groups if ace.trustee in canon.groups_by_name}
    if Era.STANDARD in eras and (Era.LEGACY in eras or Era.TEAM in eras):
        questionable = True
    posture = Posture.RISKY if risky else Posture.QUESTIONABLE if questionable else Posture.ORDINARY
    return posture, assignment


@lru_cache(maxsize=8)
def build_estate(day: dt.date = SNAPSHOT) -> GeneseePressEstate:
    """The Genesee Press estate as a complete scan on ``day`` would have found it."""
    canon = build_canon()
    shares = catalog(canon)

    specs: list[DirectorySpec] = []
    folders: list[FolderRecord] = []
    unreadable: list[str] = []
    share_trustees: dict[str, dict[str, str]] = {}

    for share in shares:
        if share.created > day:
            continue
        server_local = f"{share.drive}:\\Shares\\{share.name}"
        entries: list[tuple[FolderDef, tuple[AceDef, ...], bool]] = [
            (FolderDef("", share.root, organic=False, scenario=share.scenario), share.root, False)
        ]
        for folder in share.folders:
            if folder.created is not None and folder.created > day:
                continue
            if folder.deleted is not None and folder.deleted <= day:
                continue
            if folder.explicit or folder.protected or not (folder.organic and share.organic):
                explicit, protected = folder.explicit, folder.protected
            else:
                explicit, protected = _roll(canon, share, folder)
            # The security dialog merges an entry identical to one already present.
            unique: dict[tuple[str, str, int, bool], AceDef] = {}
            for ace in explicit:
                unique.setdefault((ace.trustee, ace.level, ace.flags, ace.deny), ace)
            entries.append((folder, tuple(unique.values()), protected))

        trustees: dict[str, str] = {}
        # What each folder passes down, so a child never re-adds an entry it already
        # inherits: the result would be two identical inherited entries one level further
        # down, which no descriptor ADG could store distinguishes.
        passed: dict[str, frozenset[tuple[str, str, bool]]] = {}
        for folder, explicit, protected in entries:
            parent_rel = folder.rel.rpartition("/")[0] if "/" in folder.rel else ""
            inherited = (
                frozenset() if protected or not folder.rel else passed.get(parent_rel, frozenset())
            )
            explicit = tuple(
                ace
                for ace in explicit
                if not (ace.flags == OI_CI and (ace.trustee, ace.level, ace.deny) in inherited)
            )
            passed[folder.rel] = inherited | {
                (ace.trustee, ace.level, ace.deny)
                for ace in explicit
                if ace.flags == OI_CI and ace.present_on(day)
            }
            path = share.unc + ("\\" + folder.rel.replace("/", "\\") if folder.rel else "")
            local = server_local + ("\\" + folder.rel.replace("/", "\\") if folder.rel else "")
            live = tuple(
                ace
                for ace in explicit
                if ace.present_on(day) and _trustee_exists(ace.trustee, canon, day)
            )
            aces = tuple(
                _ace(ace, _resolve_trustee(ace.trustee, canon, share.server)) for ace in live
            )
            for ace in live:
                if ace.trustee in ("SYSTEM", "ADMINISTRATORS", "CREATOR_OWNER") or ace.deny:
                    continue
                level = "change" if ace.level in WRITE_LEVELS else "read"
                current = trustees.get(ace.trustee)
                if current != "change":
                    trustees[ace.trustee] = level
            if folder.unreadable_from is not None and day >= folder.unreadable_from:
                unreadable.append(path)
                continue
            specs.append(
                DirectorySpec(path=path, local_path=local, explicit=aces, protected=protected)
            )
            posture, assignment = _classify(live, protected, canon, share.area)
            folders.append(
                FolderRecord(
                    path=path,
                    share=share.unc,
                    server=share.server,
                    area=share.area,
                    explicit=live,
                    protected=protected,
                    organic=folder.organic
                    and share.organic
                    and not folder.explicit
                    and not folder.protected,
                    scenario=folder.scenario,
                    posture=posture,
                    assignment=assignment,
                )
            )
        share_trustees[share.unc] = trustees

    directories = materialize_directories(specs)
    servers = tuple(
        DemoServer(
            server.name,
            f"{server.name.casefold()}.{DOMAIN_DNS}",
            operating_system=server.operating_system,
        )
        for server in SERVERS
        if server.created <= day
    )
    demo_shares = tuple(
        _share(share, share_trustees[share.unc], canon) for share in shares if share.created <= day
    ) + tuple(
        DemoShare(
            server.name,
            "C$",
            "C:\\",
            description="Default share",
            is_special=True,
            aces=(DemoShareAce(BUILTIN_ADMINISTRATORS, permission="full"),),
        )
        for server in SERVERS
        if server.created <= day
    )
    referenced = {ace.trustee for record in folders for ace in record.explicit}
    principals, edges = _directory(canon, day, servers, referenced)
    return GeneseePressEstate(
        day=day,
        estate=DemoEstate(
            profile=f"genesee-press@{day.isoformat()}",
            principals=principals,
            edges=edges,
            servers=servers,
            shares=demo_shares,
            directories=directories,
        ),
        folders=tuple(folders),
        unreadable=tuple(unreadable),
        shares=shares,
    )


def _share(share: ShareDef, trustees: dict[str, str], canon: Canon) -> DemoShare:
    admins = DemoShareAce(canon.group("FS-Admins").sid, permission="full")
    if share.share_acl == "everyone":
        aces: tuple[DemoShareAce, ...] = (DemoShareAce(EVERYONE, permission="full"),)
    elif share.share_acl == "authenticated":
        aces = (DemoShareAce(AUTHENTICATED_USERS, permission="change"), admins)
    else:
        aces = (
            admins,
            *(
                DemoShareAce(_resolve_trustee(trustee, canon, share.server), permission=level)
                for trustee, level in sorted(trustees.items(), key=lambda item: legacy_key(item[0]))
                if trustee != "FS-Admins"
            ),
        )
    return DemoShare(
        share.server,
        share.name,
        f"{share.drive}:\\Shares\\{share.name}",
        description=share.description or None,
        aces=aces,
    )


def _user_principal(person: Employee, day: dt.date) -> DemoPrincipal:
    return DemoPrincipal(
        sid=person.sid,
        principal_kind=PrincipalKind.USER,
        display_name=person.display_name,
        sam_account_name=person.sam_account_name,
        user_principal_name=person.user_principal_name,
        distinguished_name=person.distinguished_name(day),
        enabled=person.account_state(day) == "enabled",
    )


def _group_principal(group: Group) -> DemoPrincipal:
    scope = {
        "global": GroupScope.GLOBAL,
        "domain_local": GroupScope.DOMAIN_LOCAL,
        "universal": GroupScope.UNIVERSAL,
    }[group.scope]
    return DemoPrincipal(
        sid=group.sid,
        principal_kind=PrincipalKind.DOMAIN_GROUP,
        display_name=group.name,
        sam_account_name=group.name,
        distinguished_name=group.distinguished_name,
        group_scope=scope,
        group_type=GroupType.DISTRIBUTION
        if group.group_type == "distribution"
        else GroupType.SECURITY,
    )


#: The day before the first scan. Accounts deleted before it appear to every scan as
#: unresolved SIDs; accounts deleted after it are tombstoned by the scan that first misses
#: them, which is the more interesting history.
FIRST_SCAN: Final = dt.date(2026, 9, 3)


def _directory(
    canon: Canon, day: dt.date, servers: Sequence[DemoServer], referenced: set[str]
) -> tuple[tuple[DemoPrincipal, ...], tuple[DemoEdge, ...]]:
    people = [
        person for person in canon.employees if person.account_state(day) in ("enabled", "disabled")
    ]
    groups = [group for group in canon.groups if group.exists_on(day)]
    principals: list[DemoPrincipal] = [_user_principal(person, day) for person in people]
    principals += [_group_principal(group) for group in groups]
    principals += [
        DemoPrincipal(
            sid=EVERYONE, principal_kind=PrincipalKind.WELL_KNOWN, display_name="Everyone"
        ),
        DemoPrincipal(
            sid=AUTHENTICATED_USERS,
            principal_kind=PrincipalKind.WELL_KNOWN,
            display_name="Authenticated Users",
        ),
        DemoPrincipal(sid=SYSTEM, principal_kind=PrincipalKind.WELL_KNOWN, display_name="SYSTEM"),
        DemoPrincipal(
            sid=CREATOR_OWNER, principal_kind=PrincipalKind.WELL_KNOWN, display_name="CREATOR OWNER"
        ),
    ]
    # SIDs left on ACLs by accounts deleted before ADG's first scan.
    for trustee in sorted(referenced, key=legacy_key):
        if not trustee.startswith("GP-"):
            continue
        person = canon.employees_by_id[trustee]
        if person.account_state(FIRST_SCAN) == "deleted":
            principals.append(
                DemoPrincipal(
                    sid=person.sid,
                    principal_kind=PrincipalKind.UNRESOLVED,
                    unresolved_reason=UnresolvedReason.DELETED,
                    last_known_name=f"{DOMAIN_NETBIOS}\\{person.sam_account_name}",
                )
            )
    # Each file server's local Administrators group: host-scoped, one per machine.
    for server in servers:
        principals.append(
            DemoPrincipal(
                sid=BUILTIN_ADMINISTRATORS,
                principal_kind=PrincipalKind.LOCAL_GROUP,
                display_name="Administrators",
                host_key=_host_key(server.name),
                group_scope=GroupScope.BUILTIN_LOCAL,
                group_type=GroupType.SECURITY,
            )
        )

    groups_by_name = canon.groups_by_name
    employees = canon.employees_by_id
    edges: list[DemoEdge] = [
        DemoEdge(
            group_sid=groups_by_name["Domain Users"].sid,
            member_sid=person.sid,
            edge_kind=MembershipEdgeKind.PRIMARY_GROUP,
            member_kind=PrincipalKind.USER,
        )
        for person in people
    ]
    # In the order ADG v1 emitted them (by v1 spelling), so the scan's observations are too.
    for parent, member in sorted(
        canon.memberships_on(day), key=lambda edge: (legacy_key(edge[0]), legacy_key(edge[1]))
    ):
        if member in groups_by_name:
            edges.append(
                DemoEdge(
                    groups_by_name[parent].sid,
                    groups_by_name[member].sid,
                    member_kind=PrincipalKind.DOMAIN_GROUP,
                )
            )
        else:
            edges.append(
                DemoEdge(
                    groups_by_name[parent].sid,
                    employees[member].sid,
                    member_kind=PrincipalKind.USER,
                )
            )
    for server in servers:
        for member in ("Domain Admins", "FS-Admins"):
            edges.append(
                DemoEdge(
                    group_sid=BUILTIN_ADMINISTRATORS,
                    member_sid=groups_by_name[member].sid,
                    edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                    host_key=_host_key(server.name),
                    member_kind=PrincipalKind.DOMAIN_GROUP,
                )
            )
    return tuple(principals), tuple(edges)
