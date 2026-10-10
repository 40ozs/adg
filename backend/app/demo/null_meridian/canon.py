r"""The Null Meridian canon: one fictional company, ten years of it, from one seed.

Null Meridian is the company the ADG demo — and the OGWA demo beside it — describe. This
module decides *who and what exists*: the departments, the 1,800 people employed on the
snapshot date and the people who left before it, every security group the company created
in ten years and the date it was created, and who belongs to what. It decides nothing about
file servers; :mod:`app.demo.null_meridian.estate` builds those on top of this.

Nothing here is ADG-specific beyond the SID and GUID it assigns, so the same canon can be
exported (:func:`canon_manifest`) and read by another application that has to agree with
ADG about who ``priya.shah@nullmeridian.info`` is and when ``Finance-RW`` was created.

## Determinism

There is no :mod:`random` here and no wall clock. Every draw comes from :class:`Draws`, a
counter-mode stream over SHA-256::

    value(label, n) = int.from_bytes(sha256(f"{SEED}|{label}|{n}").digest()[:8], "big")

A stream is identified by its label, so adding a draw to one stream cannot shift the values
another stream produces. That is what keeps an edit to, say, how project groups are staffed
from renaming every employee. The algorithm is spelled out so a second implementation in
another language can reproduce the canon without importing this module; the manifest's
digests are how it checks that it did.

Identifiers follow from the canon rather than being stored in it:

* **UUIDs** are ``uuid5(NAMESPACE, "<kind>:<stable key>")`` with
  ``NAMESPACE = uuid5(NAMESPACE_DNS, "nullmeridian.info")``.
* **The domain SID** is ``S-1-5-21-a-b-c`` with ``a``, ``b``, ``c`` the first three 32-bit
  words of ``sha256("null-meridian-v1|domain-sid")``.
* **RIDs** are allocated the way a domain controller allocates them, in creation order:
  every account and group, sorted by the day it was created, numbered from 1103. A group
  created in 2017 therefore has a lower RID than one created in 2024, as it would in a real
  directory.
* **objectGUIDs** are ``uuid5(NAMESPACE, "object-guid:<sid>")``.

## The canon is versioned by its seed

Changing a pool, a table or an algorithm here changes the company. That is a new canon,
``null-meridian-v2``, not an edit to ``v1`` — the manifest test pins ``v1`` precisely so that
such an edit cannot go unnoticed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import cached_property, lru_cache
from typing import Final, TypeVar

from app.demo.null_meridian.names import FAMILY_NAMES, GIVEN_NAMES

__all__ = [
    "ACTIVE_EMPLOYEES",
    "COMPANY",
    "DEPARTMENTS",
    "DOMAIN_DNS",
    "DOMAIN_NETBIOS",
    "DOMAIN_SID",
    "FOUNDED",
    "NAMESPACE",
    "OKTA_ADOPTED",
    "SEED",
    "SNAPSHOT",
    "STANDARDS_ADOPTED",
    "Canon",
    "Department",
    "Draws",
    "Employee",
    "Era",
    "Group",
    "Level",
    "Nesting",
    "build_canon",
    "era_of",
]

T = TypeVar("T")

# ---------------------------------------------------------------------------
# The fixed facts.
# ---------------------------------------------------------------------------

SEED: Final = "null-meridian-v1"
COMPANY: Final = "Null Meridian"
DOMAIN_DNS: Final = "nullmeridian.info"
DOMAIN_NETBIOS: Final = "NULLMERIDIAN"
DOMAIN_DN: Final = "DC=nullmeridian,DC=info"

FOUNDED: Final = dt.date(2016, 10, 1)
SNAPSHOT: Final = dt.date(2026, 10, 1)
OKTA_ADOPTED: Final = dt.date(2021, 10, 1)
STANDARDS_ADOPTED: Final = dt.date(2023, 10, 1)

ACTIVE_EMPLOYEES: Final = 1800
FORMER_EMPLOYEES: Final = 640

#: A disabled leaver's account is deleted this many days after the termination date. The
#: policy is why recent leavers appear in ADG as disabled accounts while older ones are gone,
#: and why an account crossing the line between two scans is tombstoned by the second one.
ACCOUNT_DELETION_DAYS: Final = 180

NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_DNS, DOMAIN_DNS)

#: The first RID handed out to an object the company created. Below it sit the built-in
#: accounts and groups every domain starts with.
FIRST_RID: Final = 1103


def stable_uuid(kind: str, key: str) -> str:
    """The canon's UUID for one object: ``uuid5(NAMESPACE, "<kind>:<key>")``."""
    return str(uuid.uuid5(NAMESPACE, f"{kind}:{key}"))


def _domain_sid() -> str:
    digest = hashlib.sha256(f"{SEED}|domain-sid".encode()).digest()
    words = [int.from_bytes(digest[index : index + 4], "big") for index in (0, 4, 8)]
    return "S-1-5-21-" + "-".join(str(word) for word in words)


DOMAIN_SID: Final = _domain_sid()


# ---------------------------------------------------------------------------
# The seeded stream.
# ---------------------------------------------------------------------------


class Draws:
    """A deterministic stream of pseudo-random values, identified by its label.

    Two streams with the same label produce the same values; two with different labels are
    independent. See the module docstring for the exact algorithm.
    """

    __slots__ = ("_counter", "_label")

    def __init__(self, *label: object) -> None:
        self._label = "|".join(str(part) for part in label)
        self._counter = 0

    def next_int(self) -> int:
        payload = f"{SEED}|{self._label}|{self._counter}".encode()
        self._counter += 1
        return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")

    def random(self) -> float:
        """A float in [0, 1)."""
        return self.next_int() / 2**64

    def below(self, bound: int) -> int:
        """An integer in [0, bound). The modulo bias over 64 bits is far below anything a
        demo could observe."""
        if bound <= 0:
            raise ValueError("bound must be positive")
        return self.next_int() % bound

    def between(self, low: int, high: int) -> int:
        """An integer in [low, high], inclusive."""
        return low + self.below(high - low + 1)

    def chance(self, probability: float) -> bool:
        return self.random() < probability

    def choice(self, items: Sequence[T]) -> T:
        return items[self.below(len(items))]

    def weighted(self, items: Sequence[tuple[T, float]]) -> T:
        total = sum(weight for _, weight in items)
        point = self.random() * total
        for item, weight in items:
            point -= weight
            if point < 0:
                return item
        return items[-1][0]

    def sample(self, items: Sequence[T], count: int) -> list[T]:
        """``count`` distinct items, in the order the draws ranked them."""
        ranked = sorted((self.next_int(), index) for index in range(len(items)))
        return [items[index] for _, index in ranked[: max(0, count)]]

    def date_between(self, start: dt.date, end: dt.date) -> dt.date:
        if end < start:
            return start
        return start + dt.timedelta(days=self.below((end - start).days + 1))


# ---------------------------------------------------------------------------
# Eras.
# ---------------------------------------------------------------------------


class Era(StrEnum):
    """The four generations of Null Meridian's naming conventions."""

    LEGACY = "legacy"
    """2016-10-01 to 2019-09-30: AD and file-share conventions, one per administrator."""
    TEAM = "team"
    """2019-10-01 to 2021-09-30: team- and application-specific groups."""
    TRANSITIONAL = "transitional"
    """2021-10-01 to 2023-09-30: Okta arrives; Okta and AD conventions side by side."""
    STANDARD = "standard"
    """2023-10-01 onward: the ``sg-*`` standard."""


ERA_STARTS: Final[tuple[tuple[Era, dt.date], ...]] = (
    (Era.LEGACY, FOUNDED),
    (Era.TEAM, dt.date(2019, 10, 1)),
    (Era.TRANSITIONAL, OKTA_ADOPTED),
    (Era.STANDARD, STANDARDS_ADOPTED),
)


def era_of(day: dt.date) -> Era:
    """The naming era a creation date falls in."""
    current = Era.LEGACY
    for era, start in ERA_STARTS:
        if day >= start:
            current = era
    return current


#: The two patterns the standard prescribes. A group created in the standard era that
#: matches neither must be listed in :data:`STANDARD_ERA_EXCEPTIONS`.
MODERN_SHARE_PATTERN: Final = re.compile(r"^sg-share-[a-z0-9]+(?:-[a-z0-9]+)*-(read-only|write)$")
MODERN_OKTA_PATTERN: Final = re.compile(r"^sg-okta-[a-z0-9]+(?:-[a-z0-9]+)*-[a-z0-9]+$")


def is_standard_name(name: str) -> bool:
    return bool(MODERN_SHARE_PATTERN.match(name) or MODERN_OKTA_PATTERN.match(name))


# ---------------------------------------------------------------------------
# Departments.
# ---------------------------------------------------------------------------


class Level(StrEnum):
    EXECUTIVE = "executive"
    VP = "vp"
    DIRECTOR = "director"
    MANAGER = "manager"
    IC = "individual_contributor"


@dataclass(frozen=True, slots=True)
class Team:
    slug: str
    name: str
    weight: int
    group: str | None = None
    """The AD team group its members belong to, or ``None`` for a team that never got one
    (every team formed after the standard did not: the standard has no team groups)."""


@dataclass(frozen=True, slots=True)
class Department:
    slug: str
    name: str
    size: int
    founded: dt.date
    sponsor: str
    """The executive role the department head reports to."""
    head_title: str
    directors: int
    dept_group: str
    """The legacy department group: every member of the department is in it, directly or
    through a team group."""
    ic_titles: tuple[str, ...]
    teams: tuple[Team, ...] = ()

    @property
    def ou(self) -> str:
        return f"OU={self.name.replace('/', '-')},OU=People,{DOMAIN_DN}"


EXECUTIVE_ROLES: Final[tuple[tuple[str, str], ...]] = (
    ("ceo", "Chief Executive Officer"),
    ("cfo", "Chief Financial Officer"),
    ("cto", "Chief Technology Officer"),
    ("coo", "Chief Operating Officer"),
    ("cro", "Chief Revenue Officer"),
    ("cmo", "Chief Marketing Officer"),
    ("cpo", "Chief Product Officer"),
    ("cpeo", "Chief People Officer"),
    ("gc", "General Counsel"),
    ("ciso", "Chief Information Security Officer"),
)

#: The executive roles that were not filled on day one.
_EXECUTIVE_HIRED: Final[dict[str, tuple[dt.date, dt.date]]] = {
    "ceo": (FOUNDED, FOUNDED),
    "cto": (FOUNDED, FOUNDED),
    "cfo": (dt.date(2016, 11, 1), dt.date(2017, 6, 30)),
    "coo": (dt.date(2017, 1, 1), dt.date(2018, 6, 30)),
    "cro": (dt.date(2017, 1, 1), dt.date(2019, 6, 30)),
    "cmo": (dt.date(2018, 1, 1), dt.date(2020, 6, 30)),
    "cpo": (dt.date(2017, 6, 1), dt.date(2019, 12, 31)),
    "cpeo": (dt.date(2019, 1, 1), dt.date(2021, 6, 30)),
    "gc": (dt.date(2018, 1, 1), dt.date(2019, 12, 31)),
    "ciso": (dt.date(2020, 1, 1), dt.date(2022, 6, 30)),
}

DEPARTMENTS: Final[tuple[Department, ...]] = (
    Department(
        "engineering",
        "Engineering",
        520,
        FOUNDED,
        "cto",
        "VP of Engineering",
        5,
        "Engineering",
        (
            "Software Engineer",
            "Senior Software Engineer",
            "Staff Software Engineer",
            "Principal Engineer",
            "QA Engineer",
            "Site Reliability Engineer",
        ),
        (
            Team("platform", "Platform", 9, "ENG-Platform"),
            Team("payments", "Payments", 8, "ENG-Payments"),
            Team("mobile", "Mobile", 7, "ENG-Mobile"),
            Team("web", "Web", 9, "ENG-Web"),
            Team("infrastructure", "Infrastructure", 6, "ENG-Infrastructure"),
            Team("sre", "Site Reliability", 5, "ENG-SRE"),
            Team("qa", "Quality", 5, "ENG-QA"),
            Team("identity", "Identity", 4, "ENG-Identity"),
            Team("api", "API", 6, "ENG-API"),
            Team("developer-experience", "Developer Experience", 3),
        ),
    ),
    Department(
        "product",
        "Product",
        95,
        FOUNDED,
        "cpo",
        "VP of Product",
        1,
        "Product Team",
        (
            "Product Manager",
            "Senior Product Manager",
            "Product Analyst",
            "Technical Program Manager",
        ),
        (
            Team("core", "Core Product", 5, "PRD-Core"),
            Team("growth", "Growth", 3, "PRD-Growth"),
            Team("platform-product", "Platform Product", 2),
            Team("product-ops", "Product Operations", 1),
        ),
    ),
    Department(
        "data",
        "Data",
        110,
        dt.date(2019, 11, 1),
        "cto",
        "VP of Data",
        1,
        "Data-Team",
        (
            "Data Engineer",
            "Senior Data Engineer",
            "Data Analyst",
            "Data Scientist",
            "Analytics Engineer",
        ),
        (
            Team("data-platform", "Data Platform", 4, "Team_Data_Platform"),
            Team("analytics", "Analytics", 5, "Team_Analytics"),
            Team("ml", "Machine Learning", 3, "Team_ML"),
            Team("data-governance", "Data Governance", 1),
        ),
    ),
    Department(
        "it",
        "Information Technology",
        85,
        FOUNDED,
        "coo",
        "VP of IT",
        1,
        "IT-Staff",
        (
            "IT Support Specialist",
            "Systems Administrator",
            "Network Engineer",
            "Endpoint Engineer",
            "Identity Engineer",
        ),
        (
            Team("helpdesk", "Helpdesk", 5, "IT-Helpdesk"),
            Team("endpoint", "Endpoint", 3, "IT-Endpoint"),
            Team("network", "Network", 2, "IT-Network"),
            Team("corporate-systems", "Corporate Systems", 2, "IT-Corp-Systems"),
            Team("identity-access", "Identity and Access", 2),
        ),
    ),
    Department(
        "security",
        "Security",
        40,
        dt.date(2020, 1, 15),
        "ciso",
        "VP of Security Engineering",
        0,
        "Security-Team",
        ("Security Engineer", "Security Analyst", "GRC Analyst", "Detection Engineer"),
        (
            Team("soc", "Security Operations", 3, "SEC-SOC"),
            Team("appsec", "Application Security", 2, "SEC-AppSec"),
            Team("grc", "Governance, Risk and Compliance", 1),
        ),
    ),
    Department(
        "finance",
        "Finance",
        90,
        FOUNDED,
        "cfo",
        "VP of Finance",
        1,
        "Finance-Team",
        (
            "Staff Accountant",
            "Senior Accountant",
            "Financial Analyst",
            "Payroll Specialist",
            "Accounts Payable Specialist",
            "Tax Analyst",
        ),
        (
            Team("accounting", "Accounting", 5, "FIN-Accounting"),
            Team("fpa", "Financial Planning and Analysis", 3, "FIN-FPandA"),
            Team("payroll", "Payroll", 2, "FIN-Payroll"),
            Team("tax-treasury", "Tax and Treasury", 1),
        ),
    ),
    Department(
        "people",
        "People / HR",
        60,
        dt.date(2017, 1, 9),
        "cpeo",
        "VP of People",
        0,
        "HR Staff",
        (
            "People Partner",
            "Recruiter",
            "Senior Recruiter",
            "People Operations Specialist",
            "Compensation Analyst",
        ),
        (
            Team("recruiting", "Recruiting", 4, "HR-Recruiting"),
            Team("people-ops", "People Operations", 3, "HR-PeopleOps"),
            Team("total-rewards", "Total Rewards", 1),
            Team("learning", "Learning and Development", 1),
        ),
    ),
    Department(
        "legal",
        "Legal",
        28,
        dt.date(2018, 2, 1),
        "gc",
        "Deputy General Counsel",
        0,
        "Legal_Dept",
        (
            "Corporate Counsel",
            "Senior Counsel",
            "Paralegal",
            "Contracts Manager",
            "Privacy Counsel",
        ),
        (
            Team("commercial", "Commercial", 3),
            Team("privacy", "Privacy", 1),
            Team("corporate", "Corporate", 1),
        ),
    ),
    Department(
        "sales",
        "Sales",
        300,
        FOUNDED,
        "cro",
        "VP of Sales",
        3,
        "GG_Sales",
        (
            "Account Executive",
            "Senior Account Executive",
            "Sales Development Representative",
            "Sales Engineer",
            "Sales Operations Analyst",
        ),
        (
            Team("na-east", "North America East", 5, "Sales-NA-East"),
            Team("na-west", "North America West", 5, "Sales-NA-West"),
            Team("emea", "EMEA", 4, "Sales-EMEA"),
            Team("apac", "APAC", 2, "Sales-APAC"),
            Team("enterprise", "Enterprise", 3, "Sales-Enterprise"),
            Team("sales-ops", "Sales Operations", 1, "Sales-Ops"),
            Team("sdr", "Sales Development", 3, "Sales-SDR"),
        ),
    ),
    Department(
        "marketing",
        "Marketing",
        110,
        FOUNDED,
        "cmo",
        "VP of Marketing",
        1,
        "Marketing-All",
        (
            "Marketing Manager",
            "Content Strategist",
            "Demand Generation Manager",
            "Event Manager",
            "Product Marketing Manager",
            "Marketing Analyst",
        ),
        (
            Team("content", "Content", 3, "MKT-Content"),
            Team("demand-gen", "Demand Generation", 3, "MKT-Demand-Gen"),
            Team("events", "Events", 2, "MKT-Events"),
            Team("product-marketing", "Product Marketing", 2, "MKT-PMM"),
            Team("brand", "Brand", 1),
        ),
    ),
    Department(
        "customer-success",
        "Customer Success",
        190,
        dt.date(2017, 3, 1),
        "cro",
        "VP of Customer Success",
        2,
        "CS-All",
        (
            "Support Engineer",
            "Senior Support Engineer",
            "Customer Success Manager",
            "Onboarding Specialist",
            "Technical Account Manager",
        ),
        (
            Team("support-t1", "Support Tier 1", 6, "CS-Support-T1"),
            Team("support-t2", "Support Tier 2", 4, "CS-Support-T2"),
            Team("onboarding", "Onboarding", 3, "CS-Onboarding"),
            Team("account-management", "Account Management", 4, "CS-Account-Mgmt"),
            Team("cs-ops", "Customer Success Operations", 1),
        ),
    ),
    Department(
        "operations",
        "Operations",
        105,
        FOUNDED,
        "coo",
        "VP of Operations",
        1,
        "Ops Team",
        (
            "Operations Analyst",
            "Facilities Coordinator",
            "Procurement Specialist",
            "Business Operations Manager",
            "Workplace Coordinator",
        ),
        (
            Team("facilities", "Facilities", 3, "OPS-Facilities"),
            Team("procurement", "Procurement", 2, "OPS-Procurement"),
            Team("bizops", "Business Operations", 3, "OPS-BizOps"),
            Team("workplace", "Workplace", 2),
        ),
    ),
    Department(
        "design",
        "Design",
        55,
        dt.date(2018, 4, 2),
        "cpo",
        "Head of Design",
        0,
        "Design_Team",
        (
            "Product Designer",
            "Senior Product Designer",
            "UX Researcher",
            "Brand Designer",
            "Content Designer",
        ),
        (
            Team("product-design", "Product Design", 4),
            Team("brand-design", "Brand Design", 1),
            Team("research", "Research", 1),
        ),
    ),
    Department(
        "executive",
        "Executive",
        12,
        FOUNDED,
        "ceo",
        "Chief Executive Officer",
        0,
        "Executives",
        ("Executive Assistant",),
    ),
)

DEPARTMENTS_BY_SLUG: Final[dict[str, Department]] = {item.slug: item for item in DEPARTMENTS}

#: Which department somebody who changed departments plausibly came from.
_TRANSFER_SOURCES: Final[dict[str, tuple[str, ...]]] = {
    "engineering": ("data", "it", "product", "security"),
    "product": ("engineering", "design", "customer-success", "marketing"),
    "data": ("engineering", "finance", "product"),
    "it": ("customer-success", "security", "operations"),
    "security": ("it", "engineering"),
    "finance": ("operations", "sales"),
    "people": ("operations", "customer-success"),
    "legal": ("finance", "operations"),
    "sales": ("customer-success", "marketing", "finance"),
    "marketing": ("sales", "design", "product"),
    "customer-success": ("sales", "operations", "it"),
    "operations": ("finance", "customer-success", "people"),
    "design": ("marketing", "product"),
}

#: The acquisition whose people arrived in one batch and whose migration left debris.
ACQUISITION_NAME: Final = "Lumen Analytics"
ACQUISITION_DATE: Final = dt.date(2021, 4, 1)


# ---------------------------------------------------------------------------
# People.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Employee:
    employee_id: str
    given_name: str
    family_name: str
    department: str
    """The department on the snapshot date, or at departure for a former employee."""
    team: str | None
    title: str
    level: Level
    hire_date: dt.date
    termination_date: dt.date | None = None
    manager_id: str | None = None
    previous_department: str | None = None
    transfer_date: dt.date | None = None
    tags: tuple[str, ...] = ()
    email: str = ""
    sam_account_name: str = ""
    rid: int = 0

    @property
    def uuid(self) -> str:
        return stable_uuid("employee", self.employee_id)

    @property
    def display_name(self) -> str:
        return f"{self.given_name} {self.family_name}"

    @property
    def user_principal_name(self) -> str:
        return self.email

    @property
    def sid(self) -> str:
        return f"{DOMAIN_SID}-{self.rid}"

    @property
    def object_guid(self) -> str:
        return stable_uuid("object-guid", self.sid)

    @property
    def is_active(self) -> bool:
        return self.active_on(SNAPSHOT)

    def active_on(self, day: dt.date) -> bool:
        return self.hire_date <= day and (
            self.termination_date is None or self.termination_date > day
        )

    def account_state(self, day: dt.date) -> str:
        """``absent`` (not yet hired), ``enabled``, ``disabled`` or ``deleted``."""
        if day < self.hire_date:
            return "absent"
        if self.termination_date is None or day < self.termination_date:
            return "enabled"
        if day < self.termination_date + dt.timedelta(days=ACCOUNT_DELETION_DAYS):
            return "disabled"
        return "deleted"

    def department_on(self, day: dt.date) -> str:
        if self.transfer_date is not None and self.previous_department and day < self.transfer_date:
            return self.previous_department
        return self.department

    def team_on(self, day: dt.date) -> str | None:
        if self.transfer_date is not None and day < self.transfer_date:
            return None
        return self.team

    def distinguished_name(self, day: dt.date) -> str:
        if self.account_state(day) == "disabled":
            return f"CN={self.display_name},OU=Disabled Users,{DOMAIN_DN}"
        return f"CN={self.display_name},{DEPARTMENTS_BY_SLUG[self.department_on(day)].ou}"


def _local_part(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", text.casefold())


def _hire_years_ago(draws: Draws, max_years: float) -> float:
    """Tenure drawn from a truncated exponential: most people recent, some long-tenured."""
    scale = 3.2
    ceiling = 1.0 - math.exp(-max_years / scale)
    return -scale * math.log(1.0 - draws.random() * ceiling)


def _recent_hire(draws: Draws, earliest: dt.date) -> dt.date:
    age = (SNAPSHOT - earliest).days / 365.25
    days_ago = 1 + int(_hire_years_ago(draws, age) * 365.25)
    return max(earliest, SNAPSHOT - dt.timedelta(days=days_ago))


def _allocate(total: int, weights: Sequence[int]) -> list[int]:
    """Split ``total`` in proportion to ``weights`` by largest remainder."""
    whole = sum(weights)
    raw = [total * weight / whole for weight in weights]
    counts = [int(value) for value in raw]
    order = sorted(range(len(weights)), key=lambda index: (counts[index] - raw[index], index))
    for index in order[: total - sum(counts)]:
        counts[index] += 1
    return counts


@dataclass(slots=True)
class _Draft:
    """A person before identifiers are assigned."""

    key: str
    department: str
    team: str | None
    title: str
    level: Level
    hire_date: dt.date
    termination_date: dt.date | None = None
    manager_key: str | None = None
    previous_department: str | None = None
    transfer_date: dt.date | None = None
    tags: list[str] = field(default_factory=list)


def _draft_active() -> list[_Draft]:
    drafts: list[_Draft] = []
    executives: dict[str, _Draft] = {}

    executive = DEPARTMENTS_BY_SLUG["executive"]
    for role, title in EXECUTIVE_ROLES:
        start, end = _EXECUTIVE_HIRED[role]
        draft = _Draft(
            key=f"exec:{role}",
            department=executive.slug,
            team=None,
            title=title,
            level=Level.EXECUTIVE,
            hire_date=Draws("hire", "exec", role).date_between(start, end),
            manager_key=None if role == "ceo" else "exec:ceo",
            tags=[f"role:{role}"],
        )
        executives[role] = draft
        drafts.append(draft)
    for index in range(executive.size - len(EXECUTIVE_ROLES)):
        drafts.append(
            _Draft(
                key=f"exec:assistant:{index}",
                department=executive.slug,
                team=None,
                title="Executive Assistant",
                level=Level.IC,
                hire_date=Draws("hire", "exec-assistant", index).date_between(
                    dt.date(2018, 1, 1), dt.date(2023, 12, 31)
                ),
                manager_key="exec:ceo",
                tags=["role:executive-assistant"],
            )
        )

    for department in DEPARTMENTS:
        if department.slug == "executive":
            continue
        draws = Draws("org", department.slug)
        head = _Draft(
            key=f"{department.slug}:head",
            department=department.slug,
            team=None,
            title=department.head_title,
            level=Level.VP,
            hire_date=draws.date_between(
                department.founded, department.founded + dt.timedelta(days=3 * 365)
            ),
            manager_key=f"exec:{department.sponsor}",
            tags=["role:department-head"],
        )
        drafts.append(head)

        remaining = department.size - 1 - department.directors
        managers_total = math.ceil(remaining / 9)
        ics_total = remaining - managers_total
        weights = [team.weight for team in department.teams]
        manager_counts = [max(1, count) for count in _allocate(managers_total, weights)]
        while sum(manager_counts) > managers_total:
            largest = max(range(len(manager_counts)), key=lambda i: (manager_counts[i], -i))
            manager_counts[largest] -= 1
        ic_counts = _allocate(ics_total, weights)

        directors = [
            _Draft(
                key=f"{department.slug}:director:{index}",
                department=department.slug,
                team=None,
                title=f"Director, {department.name}",
                level=Level.DIRECTOR,
                hire_date=draws.date_between(department.founded, SNAPSHOT - dt.timedelta(days=400)),
                manager_key=head.key,
            )
            for index in range(department.directors)
        ]
        drafts.extend(directors)

        for team_index, team in enumerate(department.teams):
            boss = directors[team_index % len(directors)].key if directors else head.key
            managers = [
                _Draft(
                    key=f"{department.slug}:{team.slug}:manager:{index}",
                    department=department.slug,
                    team=team.slug,
                    title=f"Manager, {team.name}",
                    level=Level.MANAGER,
                    hire_date=draws.date_between(
                        department.founded, SNAPSHOT - dt.timedelta(days=120)
                    ),
                    manager_key=boss,
                )
                for index in range(manager_counts[team_index])
            ]
            drafts.extend(managers)
            for index in range(ic_counts[team_index]):
                ic_draws = Draws("ic", department.slug, team.slug, index)
                drafts.append(
                    _Draft(
                        key=f"{department.slug}:{team.slug}:ic:{index}",
                        department=department.slug,
                        team=team.slug,
                        title=ic_draws.choice(department.ic_titles),
                        level=Level.IC,
                        hire_date=_recent_hire(ic_draws, department.founded),
                        manager_key=managers[index % len(managers)].key,
                    )
                )

    # Transfers: people who changed departments, and kept some of the old one's access.
    for draft in drafts:
        if draft.level not in (Level.IC, Level.MANAGER) or draft.department == "executive":
            continue
        draws = Draws("transfer", draft.key)
        if (SNAPSHOT - draft.hire_date).days < 2 * 365 or not draws.chance(0.06):
            continue
        source = draws.choice(_TRANSFER_SOURCES[draft.department])
        if DEPARTMENTS_BY_SLUG[source].founded > draft.hire_date:
            continue
        draft.previous_department = source
        draft.transfer_date = draws.date_between(
            draft.hire_date + dt.timedelta(days=365), SNAPSHOT - dt.timedelta(days=90)
        )

    # The acquisition: one batch of hires on one day, into Data and Engineering.
    acquired = Draws("acquisition")
    candidates = [
        draft
        for draft in drafts
        if draft.level == Level.IC
        and draft.previous_department is None
        and (draft.department, draft.team)
        in {("data", "analytics"), ("data", "ml"), ("engineering", "api")}
        and draft.hire_date <= ACQUISITION_DATE
    ]
    for draft in acquired.sample(candidates, 18):
        draft.hire_date = ACQUISITION_DATE
        draft.tags.append("acquired:lumen-analytics")
    return drafts


def _draft_former() -> list[_Draft]:
    """People who left before the snapshot, spread over the company's history.

    Departures are weighted by headcount at the time, so a company that grew from nothing
    loses few people in its first year and most in its last few.
    """
    drafts: list[_Draft] = []
    sizes = [(department, float(department.size)) for department in DEPARTMENTS]
    span = (SNAPSHOT - FOUNDED).days
    for index in range(FORMER_EMPLOYEES):
        draws = Draws("former", index)
        department = draws.weighted(sizes)
        # Inverse CDF of a density proportional to t ** 1.6 over the company's life.
        fraction = draws.random() ** (1 / 2.6)
        termination = FOUNDED + dt.timedelta(days=max(45, int(fraction * span)))
        termination = min(termination, SNAPSHOT - dt.timedelta(days=1))
        earliest = max(department.founded, FOUNDED)
        if termination <= earliest + dt.timedelta(days=45):
            termination = earliest + dt.timedelta(days=45 + draws.below(200))
        tenure_days = 60 + int(-730 * math.log(1.0 - draws.random() * 0.95))
        hire = max(earliest, termination - dt.timedelta(days=tenure_days))
        if department.slug == "executive":
            level, title, team = Level.IC, "Executive Assistant", None
        else:
            level = draws.weighted(
                [(Level.IC, 0.88), (Level.MANAGER, 0.11), (Level.DIRECTOR, 0.01)]
            )
            team_entry = draws.choice(department.teams) if department.teams else None
            team = team_entry.slug if team_entry else None
            title = (
                draws.choice(department.ic_titles)
                if level == Level.IC
                else f"Manager, {team_entry.name}"
                if level == Level.MANAGER and team_entry
                else f"Director, {department.name}"
            )
        draft = _Draft(
            key=f"former:{index}",
            department=department.slug,
            team=team,
            title=title,
            level=level,
            hire_date=hire,
            termination_date=termination,
        )
        if (
            department.slug in ("data", "engineering")
            and hire <= ACQUISITION_DATE < termination
            and draws.chance(0.12)
        ):
            draft.hire_date = ACQUISITION_DATE
            draft.tags.append("acquired:lumen-analytics")
        drafts.append(draft)
    return drafts


def _name_people(drafts: list[_Draft]) -> dict[str, tuple[str, str]]:
    names: dict[str, tuple[str, str]] = {}
    for draft in drafts:
        draws = Draws("name", draft.key)
        names[draft.key] = (draws.choice(GIVEN_NAMES), draws.choice(FAMILY_NAMES))
    return names


# ---------------------------------------------------------------------------
# Groups.
# ---------------------------------------------------------------------------


class Category(StrEnum):
    BUILTIN = "builtin"
    BROAD = "broad"
    DEPARTMENT = "department"
    TEAM = "team"
    ROLE = "role"
    SHARE = "share"
    APPLICATION = "application"
    PROJECT = "project"
    ADMINISTRATIVE = "administrative"
    ACQUISITION = "acquisition"
    DISTRIBUTION = "distribution"


@dataclass(frozen=True, slots=True)
class Group:
    name: str
    created: dt.date
    category: Category
    department: str | None = None
    application: str | None = None
    tier: str | None = None
    scope: str = "global"
    group_type: str = "security"
    description: str = ""
    deleted: dt.date | None = None
    owner_id: str | None = None
    rid: int = 0

    @property
    def key(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.casefold()).strip("-")

    @property
    def era(self) -> Era:
        return era_of(self.created)

    @property
    def uuid(self) -> str:
        return stable_uuid("group", self.key)

    @property
    def sid(self) -> str:
        return f"{DOMAIN_SID}-{self.rid}"

    @property
    def object_guid(self) -> str:
        return stable_uuid("object-guid", self.sid)

    @property
    def ou(self) -> str:
        if self.category is Category.BUILTIN:
            return f"CN=Users,{DOMAIN_DN}"
        folder = {
            Era.LEGACY: "Legacy",
            Era.TEAM: "Teams and Applications",
            Era.TRANSITIONAL: "Okta Transition",
            Era.STANDARD: "Standard",
        }[self.era]
        return f"OU={folder},OU=Groups,{DOMAIN_DN}"

    @property
    def distinguished_name(self) -> str:
        return f"CN={self.name},{self.ou}"

    def exists_on(self, day: dt.date) -> bool:
        return self.created <= day and (self.deleted is None or day < self.deleted)


def _d(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


#: Built-in groups and their fixed RIDs.
BUILTIN_RIDS: Final[dict[str, int]] = {"Domain Admins": 512, "Domain Users": 513}

#: Standard-era groups that knowingly ignore the standard. Each is a real-world shortcut:
#: a group made in a hurry during a close, a project, and a break-glass group the identity
#: team named for findability rather than convention.
STANDARD_ERA_EXCEPTIONS: Final[tuple[str, ...]] = (
    "Finance-Close-FY2024",
    "IT-Okta-Break-Glass",
    "PRJ-Helios",
)

#: When each department's standard share groups were introduced. Legal's arrive between the
#: first and second demo scans, so the second scan reports two groups that did not exist.
SHARE_STANDARD_ROLLOUT: Final[dict[str, dt.date]] = {
    "it": _d("2023-10-16"),
    "design": _d("2023-11-06"),
    "security": _d("2023-12-04"),
    "data": _d("2024-01-15"),
    "finance": _d("2024-02-12"),
    "product": _d("2024-03-11"),
    "engineering": _d("2024-04-08"),
    "marketing": _d("2024-05-13"),
    "customer-success": _d("2024-06-10"),
    "sales": _d("2024-07-15"),
    "projects": _d("2024-08-12"),
    "operations": _d("2024-09-09"),
    "people": _d("2024-10-14"),
    "executive": _d("2025-01-13"),
    "legal": _d("2026-09-10"),
}

#: Standard application groups: (application, tier, created).
OKTA_STANDARD_GROUPS: Final[tuple[tuple[str, str, str], ...]] = (
    ("slack", "user", "2023-10-02"),
    ("zoom", "user", "2023-10-02"),
    ("google-workspace", "user", "2023-10-09"),
    ("okta", "admin", "2023-10-09"),
    ("github", "developer", "2023-10-23"),
    ("github", "admin", "2023-10-23"),
    ("jira", "user", "2023-11-13"),
    ("jira", "admin", "2023-11-13"),
    ("confluence", "user", "2023-11-13"),
    ("salesforce", "user", "2023-12-04"),
    ("salesforce", "admin", "2023-12-04"),
    ("workday", "user", "2024-01-08"),
    ("workday", "admin", "2024-01-08"),
    ("aws", "developer", "2024-02-05"),
    ("aws", "admin", "2024-02-05"),
    ("snowflake", "analyst", "2024-03-04"),
    ("snowflake", "admin", "2024-03-04"),
    ("tableau", "viewer", "2024-04-01"),
    ("netsuite", "user", "2024-05-06"),
    ("netsuite", "approver", "2024-05-06"),
    ("zendesk", "agent", "2024-06-03"),
    ("figma", "editor", "2024-07-01"),
    ("figma", "viewer", "2024-07-01"),
    ("datadog", "user", "2024-09-02"),
    ("pagerduty", "responder", "2024-09-02"),
    ("greenhouse", "recruiter", "2025-01-06"),
    ("greenhouse", "interviewer", "2025-01-06"),
    ("docusign", "sender", "2025-03-03"),
    ("gong", "user", "2025-05-05"),
    ("hubspot", "user", "2025-08-04"),
    ("1password", "user", "2026-02-02"),
)


def _group_catalog() -> list[Group]:
    groups: list[Group] = []

    def add(
        name: str,
        created: str,
        category: Category,
        *,
        department: str | None = None,
        application: str | None = None,
        tier: str | None = None,
        scope: str = "global",
        group_type: str = "security",
        description: str = "",
        deleted: str | None = None,
    ) -> None:
        groups.append(
            Group(
                name=name,
                created=_d(created),
                category=category,
                department=department,
                application=application,
                tier=tier,
                scope=scope,
                group_type=group_type,
                description=description,
                deleted=_d(deleted) if deleted else None,
            )
        )

    # Built in.
    add("Domain Admins", "2016-10-01", Category.BUILTIN, description="Built-in.")
    add("Domain Users", "2016-10-01", Category.BUILTIN, description="Built-in.")

    # ---- Legacy era: 2016-10-01 to 2019-09-30 --------------------------------------------
    add("All Staff", "2016-10-03", Category.BROAD, description="Every employee.")
    add(
        "Everyone-Announcements",
        "2016-10-03",
        Category.DISTRIBUTION,
        group_type="distribution",
        description="Company-wide mailing list.",
    )
    add(
        "FS-Admins",
        "2016-10-05",
        Category.ADMINISTRATIVE,
        description="File server administrators.",
    )
    add("VPN-Users", "2016-10-20", Category.APPLICATION, application="vpn")
    add(
        "Shared_Drive_Users",
        "2016-10-26",
        Category.BROAD,
        description="Created for the first shared drive.",
    )
    add("IT-Share-Admins", "2016-10-31", Category.SHARE, department="it", scope="domain_local")
    add("Finance-RW", "2016-11-07", Category.SHARE, department="finance", scope="domain_local")
    add(
        "Share_Engineering_Modify",
        "2016-11-14",
        Category.SHARE,
        department="engineering",
        scope="domain_local",
    )
    add(
        "FS-Legacy-Users",
        "2016-12-05",
        Category.BROAD,
        description="Everyone who used NM-FS-01. Nobody remembers why it is separate.",
    )
    add("Sales-RW", "2017-01-23", Category.SHARE, department="sales", scope="domain_local")
    add("HR_Share_RW", "2017-01-30", Category.SHARE, department="people", scope="domain_local")
    add("Ops-Share-RW", "2017-02-06", Category.SHARE, department="operations", scope="domain_local")
    add("Managers", "2017-02-13", Category.ROLE, description="People managers.")
    add("Exec-Share", "2017-03-06", Category.SHARE, department="executive", scope="domain_local")
    add("Finance_Read", "2017-03-20", Category.SHARE, department="finance", scope="domain_local")
    add(
        "Share_Engineering_Read",
        "2017-04-03",
        Category.SHARE,
        department="engineering",
        scope="domain_local",
    )
    add("HR-Confidential", "2017-05-08", Category.SHARE, department="people", scope="domain_local")
    add("Engineering-FS", "2017-05-22", Category.SHARE, department="engineering")
    add("Payroll_Admins", "2017-06-12", Category.SHARE, department="finance", scope="domain_local")
    add("Product-FS", "2017-06-19", Category.SHARE, department="product")
    add("Proj-Apollo", "2017-08-07", Category.PROJECT, department="engineering")
    add("Scanner-Users", "2017-09-11", Category.BROAD)
    add(
        "CS-Share-RW",
        "2018-01-15",
        Category.SHARE,
        department="customer-success",
        scope="domain_local",
    )
    add("Sales-Managers", "2018-01-08", Category.ROLE, department="sales")
    add("GG_IT_Software_R", "2018-02-26", Category.SHARE, department="it")
    add("Hiring-Managers", "2018-03-05", Category.ROLE, department="people")
    add(
        "Proj-Zephyr-RW",
        "2018-03-19",
        Category.PROJECT,
        department="engineering",
        scope="domain_local",
        description="Zephyr ended in 2019.",
    )
    add("Design-Assets-RW", "2018-04-16", Category.SHARE, department="design", scope="domain_local")
    add("Sales_Read", "2018-05-14", Category.SHARE, department="sales", scope="domain_local")
    add("GG_Finance_RW", "2018-06-04", Category.SHARE, department="finance")
    add("Legal-RW", "2018-06-11", Category.SHARE, department="legal", scope="domain_local")
    add(
        "Finance-Updates",
        "2018-07-02",
        Category.DISTRIBUTION,
        department="finance",
        group_type="distribution",
    )
    add("Eng-Leads", "2018-09-10", Category.ROLE, department="engineering")
    add(
        "FS-Marketing-RW",
        "2018-09-17",
        Category.SHARE,
        department="marketing",
        scope="domain_local",
    )
    add(
        "FS-Marketing-R", "2018-09-17", Category.SHARE, department="marketing", scope="domain_local"
    )
    add(
        "Board-Materials-R",
        "2018-10-15",
        Category.SHARE,
        department="executive",
        scope="domain_local",
    )
    add(
        "Facilities_RW", "2018-11-12", Category.SHARE, department="operations", scope="domain_local"
    )
    add("FS-Finance-R", "2019-02-11", Category.SHARE, department="finance", scope="domain_local")
    add("GG_Legal_Contracts_R", "2019-04-22", Category.SHARE, department="legal")
    add(
        "GG_Archive_R",
        "2019-06-17",
        Category.SHARE,
        scope="domain_local",
        description="Read access to the archive server.",
    )

    # ---- Team and application era: 2019-10-01 to 2021-09-30 ------------------------------
    add("Salesforce-Sales", "2019-10-15", Category.APPLICATION, application="salesforce")
    add("APP-GitHub-Users", "2019-11-04", Category.APPLICATION, application="github")
    add(
        "Engineering-Backend",
        "2019-11-18",
        Category.TEAM,
        department="engineering",
        description="Backend teams.",
    )
    add(
        "Engineering-Frontend",
        "2019-11-18",
        Category.TEAM,
        department="engineering",
        description="Frontend teams.",
    )
    add("APP-Jira-Users", "2019-12-02", Category.APPLICATION, application="jira")
    add(
        "FIN-Close-Team",
        "2020-01-13",
        Category.TEAM,
        department="finance",
        description="Month-end close.",
    )
    add("GG_App_Workday", "2020-02-10", Category.APPLICATION, application="workday")
    add("APP-Confluence-Users", "2020-03-02", Category.APPLICATION, application="confluence")
    add("PRJ_Nebula", "2020-03-16", Category.PROJECT, department="engineering")
    add(
        "Remote-Workers-2020",
        "2020-03-23",
        Category.BROAD,
        description="Created for the 2020 remote-work rollout.",
    )
    add("GG_App_NetSuite", "2020-04-06", Category.APPLICATION, application="netsuite")
    add("APP-AWS-Console", "2020-05-11", Category.APPLICATION, application="aws")
    add("Zendesk-Agents", "2020-06-01", Category.APPLICATION, application="zendesk")
    add(
        "Revenue-Reporting",
        "2020-06-22",
        Category.ROLE,
        department="finance",
        description="Revenue reports for Sales Ops and BizOps.",
    )
    add(
        "Project-Vega",
        "2020-09-14",
        Category.PROJECT,
        department="product",
        deleted="2026-09-10",
        description="Vega was cancelled in 2021.",
    )
    add("APP-Tableau-Viewers", "2021-01-11", Category.APPLICATION, application="tableau")
    add(
        "LA-Migrated-Users",
        "2021-04-01",
        Category.ACQUISITION,
        description=f"People who joined with {ACQUISITION_NAME}.",
    )
    add("Acq-Lumen-RW", "2021-04-05", Category.ACQUISITION, scope="domain_local")
    add("PRJ_Orion", "2021-05-03", Category.PROJECT, department="engineering")

    # ---- Transitional era: Okta arrives 2021-10-01 ---------------------------------------
    add(
        "AD-Okta-Sync-All",
        "2021-10-05",
        Category.BROAD,
        description="Everything Okta should provision to.",
    )
    add("OKTA_Slack_All", "2021-10-12", Category.APPLICATION, application="slack")
    add("Okta_GitHub_Admin", "2021-10-20", Category.APPLICATION, application="github", tier="admin")
    add("Okta_GitHub_Users", "2021-10-20", Category.APPLICATION, application="github", tier="user")
    add("Okta-Salesforce-Users", "2021-11-08", Category.APPLICATION, application="salesforce")
    add("IT-Corp-Systems", "2021-11-15", Category.TEAM, department="it")
    add("okta-zoom-users", "2021-12-06", Category.APPLICATION, application="zoom")
    add("Okta_Workday_Users", "2022-01-10", Category.APPLICATION, application="workday")
    add("Share-Data-RW", "2022-02-07", Category.SHARE, department="data", scope="domain_local")
    add("DATA_Share_Read", "2022-02-07", Category.SHARE, department="data", scope="domain_local")
    add("ENG-Identity", "2022-03-14", Category.TEAM, department="engineering")
    add("Okta-AWS-Admins", "2022-03-28", Category.APPLICATION, application="aws", tier="admin")
    add("PRJ-Atlas-2022", "2022-04-11", Category.PROJECT, department="product")
    add(
        "Okta_Snowflake_Analysts",
        "2022-04-25",
        Category.APPLICATION,
        application="snowflake",
        tier="analyst",
    )
    add(
        "ENG-Payments-Oncall",
        "2022-05-09",
        Category.TEAM,
        department="engineering",
        description="Payments on-call rotation.",
    )
    add("Okta-Jira-Users", "2022-06-06", Category.APPLICATION, application="jira")
    add(
        "Okta_Figma_Editors", "2022-06-20", Category.APPLICATION, application="figma", tier="editor"
    )
    add("FIN-Close-Approvers", "2022-08-01", Category.ROLE, department="finance")
    add("Okta-Datadog-Users", "2022-08-15", Category.APPLICATION, application="datadog")
    add(
        "SEC-Share-Restricted",
        "2022-09-12",
        Category.SHARE,
        department="security",
        scope="domain_local",
    )
    add("Okta_PagerDuty_Responders", "2022-10-03", Category.APPLICATION, application="pagerduty")
    add("okta-greenhouse-recruiters", "2022-10-17", Category.APPLICATION, application="greenhouse")
    add(
        "CS_Share_Read",
        "2022-11-07",
        Category.SHARE,
        department="customer-success",
        scope="domain_local",
    )
    add("Okta_Zendesk_Agents", "2023-01-09", Category.APPLICATION, application="zendesk")
    add("Okta-NetSuite-Users", "2023-03-06", Category.APPLICATION, application="netsuite")

    # ---- Standard era: 2023-10-01 onward -------------------------------------------------
    for area_slug, rollout_day in SHARE_STANDARD_ROLLOUT.items():
        for tier in ("read-only", "write"):
            add(
                f"sg-share-{area_slug}-{tier}",
                rollout_day.isoformat(),
                Category.SHARE,
                department=area_slug if area_slug in DEPARTMENTS_BY_SLUG else None,
                tier=tier,
                scope="domain_local",
                description=f"Standard {tier} access to {area_slug} shares.",
            )
    add(
        "sg-share-archive-read-only",
        "2025-02-10",
        Category.SHARE,
        tier="read-only",
        scope="domain_local",
        description="Standard read-only access to the archive.",
    )
    for application, tier, created in OKTA_STANDARD_GROUPS:
        add(
            f"sg-okta-{application}-{tier}",
            created,
            Category.APPLICATION,
            application=application,
            tier=tier,
            description=f"Okta assignment: {application} ({tier}).",
        )
    add(
        "Finance-Close-FY2024",
        "2024-01-08",
        Category.PROJECT,
        department="finance",
        description="Created during the FY2024 close. Never retired.",
    )
    add("IT-Okta-Break-Glass", "2024-03-04", Category.ADMINISTRATIVE, department="it")
    add("PRJ-Helios", "2024-06-03", Category.PROJECT, department="engineering")

    # Department and team groups come from the department table.
    seen = {group.name for group in groups}
    for department in DEPARTMENTS:
        if department.dept_group not in seen:
            groups.append(
                Group(
                    name=department.dept_group,
                    created=_DEPARTMENT_GROUP_CREATED[department.slug],
                    category=Category.DEPARTMENT,
                    department=department.slug,
                    description=f"{department.name} department.",
                )
            )
            seen.add(department.dept_group)
        for team in department.teams:
            if team.group and team.group not in seen:
                groups.append(
                    Group(
                        name=team.group,
                        created=_TEAM_GROUP_CREATED.get(team.group, _d("2020-01-06")),
                        category=Category.TEAM,
                        department=department.slug,
                        description=f"{department.name}: {team.name}.",
                    )
                )
                seen.add(team.group)
    return groups


_DEPARTMENT_GROUP_CREATED: Final[dict[str, dt.date]] = {
    "executive": _d("2016-10-03"),
    "it": _d("2016-10-05"),
    "engineering": _d("2016-10-10"),
    "finance": _d("2016-10-11"),
    "product": _d("2016-10-12"),
    "sales": _d("2016-10-17"),
    "operations": _d("2016-10-24"),
    "marketing": _d("2016-11-01"),
    "people": _d("2017-01-16"),
    "customer-success": _d("2017-03-06"),
    "legal": _d("2018-02-05"),
    "design": _d("2018-04-09"),
    "data": _d("2019-11-04"),
    "security": _d("2020-01-20"),
}

_TEAM_GROUP_CREATED: Final[dict[str, dt.date]] = {
    "ENG-Platform": _d("2019-10-07"),
    "ENG-Payments": _d("2019-10-07"),
    "ENG-Web": _d("2019-10-14"),
    "ENG-Mobile": _d("2019-10-14"),
    "ENG-Infrastructure": _d("2019-10-21"),
    "ENG-SRE": _d("2020-02-03"),
    "ENG-QA": _d("2019-10-28"),
    "ENG-API": _d("2020-04-13"),
    "PRD-Core": _d("2020-01-27"),
    "PRD-Growth": _d("2020-08-03"),
    "Team_Data_Platform": _d("2019-11-11"),
    "Team_Analytics": _d("2019-11-11"),
    "Team_ML": _d("2020-09-07"),
    "IT-Helpdesk": _d("2019-10-01"),
    "IT-Endpoint": _d("2019-10-01"),
    "IT-Network": _d("2019-10-01"),
    "SEC-SOC": _d("2020-06-15"),
    "SEC-AppSec": _d("2021-02-08"),
    "FIN-Accounting": _d("2019-10-21"),
    "FIN-FPandA": _d("2019-10-21"),
    "FIN-Payroll": _d("2019-12-09"),
    "HR-Recruiting": _d("2019-10-28"),
    "HR-PeopleOps": _d("2019-10-28"),
    "Sales-NA-East": _d("2019-10-07"),
    "Sales-NA-West": _d("2019-10-07"),
    "Sales-EMEA": _d("2020-01-13"),
    "Sales-APAC": _d("2020-11-02"),
    "Sales-Enterprise": _d("2020-03-02"),
    "Sales-Ops": _d("2019-10-07"),
    "Sales-SDR": _d("2020-05-04"),
    "MKT-Content": _d("2019-11-25"),
    "MKT-Demand-Gen": _d("2019-11-25"),
    "MKT-Events": _d("2019-11-25"),
    "MKT-PMM": _d("2021-03-01"),
    "CS-Support-T1": _d("2019-10-14"),
    "CS-Support-T2": _d("2019-10-14"),
    "CS-Onboarding": _d("2020-02-17"),
    "CS-Account-Mgmt": _d("2020-02-17"),
    "OPS-Facilities": _d("2019-12-02"),
    "OPS-Procurement": _d("2019-12-02"),
    "OPS-BizOps": _d("2020-06-08"),
}


# ---------------------------------------------------------------------------
# Nesting: groups inside groups.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Nesting:
    """``member`` is a member of ``parent`` from ``added`` (or from when both exist)."""

    parent: str
    member: str
    added: dt.date | None = None
    note: str = ""


def _nesting_catalog() -> list[Nesting]:
    edges: list[Nesting] = []

    def nest(parent: str, *members: str, added: str | None = None, note: str = "") -> None:
        for member in members:
            edges.append(Nesting(parent, member, _d(added) if added else None, note))

    # Every department group is in All Staff.
    for department in DEPARTMENTS:
        nest("All Staff", department.dept_group)
    # Team groups sit in their department group, except where an intermediate layer was
    # inserted in 2019 and never removed.
    via_backend = {
        "ENG-Platform",
        "ENG-Payments",
        "ENG-API",
        "ENG-Infrastructure",
        "ENG-SRE",
        "ENG-Identity",
    }
    via_frontend = {"ENG-Web", "ENG-Mobile"}
    for department in DEPARTMENTS:
        for team in department.teams:
            if not team.group:
                continue
            if team.group in via_backend:
                nest("Engineering-Backend", team.group)
            elif team.group in via_frontend:
                nest("Engineering-Frontend", team.group)
            else:
                nest(department.dept_group, team.group)
    nest(
        "Engineering",
        "Engineering-Backend",
        "Engineering-Frontend",
        note="A layer nobody needed: every member is already a member of a team group.",
    )
    nest("Engineering", "ENG-Platform", note="Duplicate of ENG-Platform -> Engineering-Backend.")
    nest("ENG-Payments", "ENG-Payments-Oncall")

    # Broad groups.
    nest("Shared_Drive_Users", "All Staff")
    nest(
        "FS-Legacy-Users",
        "All Staff",
        "Shared_Drive_Users",
        note="Two routes from everybody to the same group.",
    )
    nest("Remote-Workers-2020", "All Staff")
    nest("AD-Okta-Sync-All", "All Staff")
    nest("Scanner-Users", "Ops Team")

    # Engineering file access: the deep chain.
    nest("Engineering-FS", "Engineering")
    nest("Share_Engineering_Modify", "Engineering-FS")
    nest(
        "Engineering-FS",
        "APP-GitHub-Users",
        added="2020-02-03",
        note="An application group became a file-share dependency.",
    )
    nest("Share_Engineering_Read", "Product Team", "Data-Team", "Security-Team")
    nest(
        "sg-share-engineering-write",
        "Engineering-FS",
        note="Migration shortcut: the modern group nests the legacy one.",
    )
    nest("sg-share-engineering-read-only", "Product Team", "Data-Team", "Design_Team")
    nest(
        "Eng-Leads",
        "Managers",
        added="2026-09-22",
        note="Added between the second and third scans: every manager now an Eng lead.",
    )

    # Finance: legacy layering, an incomplete migration and a cross-department route.
    nest("Finance-RW", "Finance-Team")
    nest("GG_Finance_RW", "Finance-RW", note="A global wrapper around a domain-local group.")
    nest("GG_Finance_RW", "FIN-Close-Team")
    nest("Finance_Read", "Executives", "Revenue-Reporting")
    nest("FS-Finance-R", "Finance_Read", note="Read group nested in a second read group.")
    nest("Revenue-Reporting", "Sales-Ops", "OPS-BizOps")
    nest("Payroll_Admins", "FIN-Payroll", "HR-PeopleOps")
    nest("sg-share-finance-read-only", "Finance_Read")
    nest("sg-share-finance-write", "FIN-FPandA")
    nest(
        "FIN-Close-Approvers",
        "FIN-Close-Team",
        added="2026-09-15",
        note="Every closer became an approver between the first and second scans.",
    )

    # Sales, Marketing, Customer Success.
    nest("Sales-RW", "GG_Sales")
    nest("Sales_Read", "Marketing-All", "CS-All")
    nest("sg-share-sales-write", "GG_Sales")
    nest("sg-share-sales-read-only", "Marketing-All", "CS-All", "Sales_Read")
    nest("FS-Marketing-RW", "Marketing-All")
    nest("FS-Marketing-R", "GG_Sales", "Product Team")
    nest("sg-share-marketing-write", "Marketing-All")
    nest(
        "sg-share-marketing-read-only",
        "FS-Marketing-R",
        note="Same membership as FS-Marketing-R, one level deeper.",
    )
    nest("CS-Share-RW", "CS-All")
    nest("CS_Share_Read", "GG_Sales")
    nest("sg-share-customer-success-write", "CS-All")
    nest("sg-share-customer-success-read-only", "CS_Share_Read")

    # People, Legal, Operations, IT, Product, Executive, Data, Security.
    nest("HR_Share_RW", "HR Staff")
    nest("HR-Confidential", "HR-PeopleOps")
    nest("Hiring-Managers", "Managers", note="Every manager, whether they hire or not.")
    nest("Legal-RW", "Legal_Dept")
    nest("GG_Legal_Contracts_R", "Sales-Managers", "Finance-Team")
    nest("Ops-Share-RW", "Ops Team")
    nest("Facilities_RW", "OPS-Facilities", "IT-Helpdesk")
    nest("sg-share-operations-write", "Ops Team")
    nest("sg-share-operations-read-only", "Finance-Team")
    nest("IT-Share-Admins", "IT-Staff")
    nest("GG_IT_Software_R", "All Staff")
    nest("sg-share-it-read-only", "Security-Team")
    nest("Product-FS", "Product Team")
    nest("sg-share-product-write", "Product Team")
    nest("sg-share-product-read-only", "Design_Team", "Engineering-Frontend")
    nest("Exec-Share", "Executives")
    nest("Board-Materials-R", "Executives")
    nest("Share-Data-RW", "Data-Team")
    nest("Share-Data-RW", "LA-Migrated-Users", note="Acquired team kept their own route in.")
    nest("DATA_Share_Read", "Product Team", "Engineering-Backend")
    nest("Acq-Lumen-RW", "LA-Migrated-Users")
    nest("sg-share-data-write", "Data-Team")
    nest("sg-share-data-read-only", "DATA_Share_Read")
    nest("SEC-Share-Restricted", "Security-Team")
    nest("Design-Assets-RW", "Design_Team")
    nest(
        "sg-share-projects-read-only",
        "Managers",
        note="A department-shaped group reused where a role group was needed.",
    )
    nest("sg-share-archive-read-only", "GG_Archive_R")

    # Applications: legacy group -> Okta-era group -> standard group.
    nest("Salesforce-Sales", "GG_Sales", "CS-Account-Mgmt")
    nest("Okta-Salesforce-Users", "Salesforce-Sales")
    nest("sg-okta-salesforce-user", "Okta-Salesforce-Users")
    nest("APP-GitHub-Users", "Engineering")
    nest("APP-GitHub-Users", "Data-Team", added="2020-01-06")
    nest("Okta_GitHub_Users", "APP-GitHub-Users")
    nest("sg-okta-github-developer", "Okta_GitHub_Users")
    nest("sg-okta-github-admin", "Okta_GitHub_Admin")
    nest("GG_App_Workday", "All Staff")
    nest("Okta_Workday_Users", "GG_App_Workday")
    nest("sg-okta-workday-user", "Okta_Workday_Users")
    nest("OKTA_Slack_All", "AD-Okta-Sync-All")
    nest("sg-okta-slack-user", "OKTA_Slack_All")
    nest("okta-zoom-users", "AD-Okta-Sync-All")
    nest("sg-okta-zoom-user", "okta-zoom-users")
    nest("sg-okta-google-workspace-user", "AD-Okta-Sync-All")
    nest("sg-okta-1password-user", "AD-Okta-Sync-All")
    nest("APP-Jira-Users", "Engineering", "Product Team")
    nest("Okta-Jira-Users", "APP-Jira-Users")
    nest("sg-okta-jira-user", "Okta-Jira-Users", "Data-Team")
    nest("APP-Confluence-Users", "All Staff")
    nest("sg-okta-confluence-user", "APP-Confluence-Users")
    nest("APP-AWS-Console", "Engineering-Backend")
    nest("Okta-AWS-Admins", "ENG-SRE", "ENG-Infrastructure")
    nest("sg-okta-aws-developer", "APP-AWS-Console")
    nest("sg-okta-aws-admin", "Okta-AWS-Admins")
    nest("Zendesk-Agents", "CS-Support-T1", "CS-Support-T2")
    nest("Okta_Zendesk_Agents", "Zendesk-Agents")
    nest("sg-okta-zendesk-agent", "Okta_Zendesk_Agents")
    nest("GG_App_NetSuite", "Finance-Team")
    nest("Okta-NetSuite-Users", "GG_App_NetSuite")
    nest("sg-okta-netsuite-user", "Okta-NetSuite-Users")
    nest("sg-okta-netsuite-approver", "FIN-Close-Approvers")
    nest("APP-Tableau-Viewers", "Revenue-Reporting", "Data-Team")
    nest("sg-okta-tableau-viewer", "APP-Tableau-Viewers")
    nest("Okta_Snowflake_Analysts", "Team_Analytics")
    nest("sg-okta-snowflake-analyst", "Okta_Snowflake_Analysts")
    nest("Okta_Figma_Editors", "Design_Team")
    nest("sg-okta-figma-viewer", "Product Team")
    nest("Okta-Datadog-Users", "Engineering-Backend")
    nest("sg-okta-datadog-user", "Okta-Datadog-Users")
    nest("Okta_PagerDuty_Responders", "ENG-SRE", "ENG-Payments-Oncall")
    nest("sg-okta-pagerduty-responder", "Okta_PagerDuty_Responders")
    nest("okta-greenhouse-recruiters", "HR-Recruiting")
    nest("sg-okta-greenhouse-recruiter", "okta-greenhouse-recruiters")
    nest("sg-okta-greenhouse-interviewer", "Hiring-Managers")
    nest("sg-okta-jira-admin", "Eng-Leads")
    return edges


# ---------------------------------------------------------------------------
# Direct memberships of people.
# ---------------------------------------------------------------------------

#: Departments whose standard share groups hold people directly, as the standard intends.
#: Every other department's standard groups were filled by nesting the legacy group.
DIRECT_STANDARD_DEPARTMENTS: Final = frozenset(
    {"design", "legal", "people", "security", "it", "executive"}
)

#: Finance moved only some of its people onto ``sg-share-finance-write``.
FINANCE_MIGRATION_PROGRESS: Final = 0.4


@dataclass(frozen=True)
class Canon:
    """Everything about Null Meridian that is not a file server."""

    employees: tuple[Employee, ...]
    groups: tuple[Group, ...]
    nesting: tuple[Nesting, ...]
    assignments: tuple[tuple[str, str, dt.date | None, dt.date | None], ...]
    """Direct person-to-group memberships: ``(group, employee_id, since, until)``. ``since``
    of ``None`` means "from when both exist"; ``until`` of ``None`` means "still"."""

    @cached_property
    def active(self) -> tuple[Employee, ...]:
        return tuple(person for person in self.employees if person.is_active)

    @cached_property
    def former(self) -> tuple[Employee, ...]:
        return tuple(person for person in self.employees if not person.is_active)

    @cached_property
    def groups_by_name(self) -> dict[str, Group]:
        return {group.name: group for group in self.groups}

    @cached_property
    def employees_by_id(self) -> dict[str, Employee]:
        return {person.employee_id: person for person in self.employees}

    def group(self, name: str) -> Group:
        return self.groups_by_name[name]

    def tagged(self, tag: str) -> tuple[Employee, ...]:
        return tuple(person for person in self.employees if tag in person.tags)

    def one(self, tag: str) -> Employee:
        matches = self.tagged(tag)
        if len(matches) != 1:
            raise LookupError(f"{len(matches)} employees carry the tag {tag!r}; expected one.")
        return matches[0]

    def memberships_on(self, day: dt.date) -> tuple[tuple[str, str], ...]:
        """Every ``(group name, member)`` edge in the directory on ``day``.

        A member is a group name or an employee id. Primary-group membership of Domain Users
        is not included; it is a property of every account, not a row in ``member``.
        """
        groups = self.groups_by_name
        people = self.employees_by_id
        edges: set[tuple[str, str]] = set()
        for nesting in self.nesting:
            parent, member = groups[nesting.parent], groups[nesting.member]
            if not (parent.exists_on(day) and member.exists_on(day)):
                continue
            if nesting.added is not None and day < nesting.added:
                continue
            edges.add((nesting.parent, nesting.member))
        for group_name, employee_id, since, until in self.assignments:
            group = groups[group_name]
            person = people[employee_id]
            if not group.exists_on(day):
                continue
            if since is not None and day < since:
                continue
            if until is not None and day >= until:
                continue
            if person.account_state(day) not in ("enabled", "disabled"):
                continue
            edges.add((group_name, employee_id))
        return tuple(sorted(edges))

    def nesting_depths(self, day: dt.date = SNAPSHOT) -> dict[str, int]:
        """For every group, the longest chain of groups nested beneath it (0 = no nested
        groups)."""
        children: dict[str, list[str]] = {}
        names = {group.name for group in self.groups if group.exists_on(day)}
        for parent, member in self.memberships_on(day):
            if member in names:
                children.setdefault(parent, []).append(member)
        memo: dict[str, int] = {}

        def depth(name: str, trail: frozenset[str]) -> int:
            if name in memo:
                return memo[name]
            if name in trail:
                raise ValueError(f"Nesting cycle through {name!r}")
            best = 0
            for child in children.get(name, ()):
                best = max(best, 1 + depth(child, trail | {name}))
            memo[name] = best
            return best

        return {name: depth(name, frozenset()) for name in sorted(names)}


def _assignments(
    employees: Sequence[Employee], groups: Sequence[Group]
) -> list[tuple[str, str, dt.date | None, dt.date | None]]:
    """Which people are direct members of which groups, and over which dates."""
    rows: list[tuple[str, str, dt.date | None, dt.date | None]] = []
    team_groups = {
        (department.slug, team.slug): team.group
        for department in DEPARTMENTS
        for team in department.teams
        if team.group
    }
    group_created = {group.name: group.created for group in groups}

    def add(
        group: str, person: Employee, since: dt.date | None = None, until: dt.date | None = None
    ) -> None:
        if group not in group_created:
            raise KeyError(f"No group named {group!r}")
        # A leaver's memberships end at termination. The leaver process misses the team or
        # department group for a few people, and nothing else.
        kept = "leaver:kept-groups" in person.tags and group in (
            team_groups.get((person.department, person.team or "")),
            DEPARTMENTS_BY_SLUG[person.department].dept_group,
        )
        if person.termination_date is not None and not kept:
            until = (
                person.termination_date if until is None else min(until, person.termination_date)
            )
        if until is not None and since is not None and until <= since:
            return
        rows.append((group, person.employee_id, since, until))

    for person in employees:
        draws = Draws("membership", person.employee_id)
        department = DEPARTMENTS_BY_SLUG[person.department]
        team_group = team_groups.get((person.department, person.team or ""))
        transfer = person.transfer_date

        # Before a transfer: the old department's group. Sometimes never removed.
        if person.previous_department and transfer is not None:
            old = DEPARTMENTS_BY_SLUG[person.previous_department].dept_group
            kept = "transfer:kept-old-access" in person.tags
            add(old, person, until=None if kept else transfer)

        # Department or team group.
        if team_group:
            add(team_group, person, since=transfer)
            if draws.chance(0.10):
                add(department.dept_group, person, since=transfer)
        else:
            add(department.dept_group, person, since=transfer)

        # Roles.
        if person.level in (Level.MANAGER, Level.DIRECTOR, Level.VP, Level.EXECUTIVE):
            if draws.chance(0.85):
                add("Managers", person)
            if person.department == "sales":
                add("Sales-Managers", person)
            if person.department == "engineering" and person.hire_date < dt.date(2021, 1, 1):
                add("Eng-Leads", person)

        # Standard share groups with people in them.
        rollout = SHARE_STANDARD_ROLLOUT.get(person.department)
        if person.department in DIRECT_STANDARD_DEPARTMENTS and rollout is not None:
            add(f"sg-share-{person.department}-write", person)
        if (
            person.department == "finance"
            and person.team != "fpa"
            and draws.chance(FINANCE_MIGRATION_PROGRESS)
        ):
            add("sg-share-finance-write", person)
        if (
            rollout is not None
            and person.department in ("engineering", "sales", "customer-success")
            and person.hire_date >= rollout
        ):
            # Since the standard, new hires go straight into the modern group as well — so
            # they reach it twice, once directly and once through the legacy nesting.
            add(f"sg-share-{person.department}-write", person)

        # Applications, assigned directly since Okta.
        if person.department in ("engineering", "data") and person.hire_date >= dt.date(
            2023, 10, 23
        ):
            add("sg-okta-github-developer", person)
        if person.department == "design":
            add("sg-okta-figma-editor", person)
        if person.department == "sales" and draws.chance(0.7):
            add("sg-okta-gong-user", person)
        if person.department == "marketing" and draws.chance(0.8):
            add("sg-okta-hubspot-user", person)
        if person.department == "people" and person.team == "people-ops":
            add("sg-okta-workday-admin", person)
        if person.department == "data" and person.team == "data-platform":
            add("sg-okta-snowflake-admin", person)
        if person.department == "data" and person.team == "analytics" and draws.chance(0.4):
            add("sg-okta-snowflake-analyst", person)
        if "acquired:lumen-analytics" in person.tags:
            add("LA-Migrated-Users", person)
        if person.department == "finance" and person.team == "accounting" and draws.chance(0.7):
            add("FIN-Close-Team", person)
        if person.department == "finance" and person.level in (Level.MANAGER, Level.DIRECTOR):
            add("FIN-Close-Approvers", person)

    return rows


# ---------------------------------------------------------------------------
# Hand-picked memberships: the small groups whose members are named, not derived.
# ---------------------------------------------------------------------------


def _picked(employees: Sequence[Employee]) -> list[tuple[str, str, dt.date | None, dt.date | None]]:
    rows: list[tuple[str, str, dt.date | None, dt.date | None]] = []
    active = [person for person in employees if person.is_active]

    def people(predicate: Callable[[Employee], bool], label: str, count: int) -> list[Employee]:
        pool = [person for person in active if predicate(person)]
        return Draws("pick", label).sample(pool, count)

    def it(team: str) -> Callable[[Employee], bool]:
        return lambda person: person.department == "it" and person.team == team

    for person in people(it("network"), "domain-admins", 2) + people(
        lambda p: p.department == "it" and p.level == Level.VP, "domain-admins-vp", 1
    ):
        rows.append(("Domain Admins", person.employee_id, None, None))
    for person in people(it("corporate-systems"), "fs-admins", 4) + people(
        it("endpoint"), "fs-admins-endpoint", 2
    ):
        rows.append(("FS-Admins", person.employee_id, None, None))
    for person in people(it("identity-access"), "break-glass", 2):
        rows.append(("IT-Okta-Break-Glass", person.employee_id, None, None))
    for person in people(it("identity-access"), "okta-admin", 3):
        rows.append(("sg-okta-okta-admin", person.employee_id, None, None))

    def eng(team: str) -> Callable[[Employee], bool]:
        return lambda p: p.department == "engineering" and p.team == team

    for person in people(eng("payments"), "payments-oncall", 8):
        rows.append(("ENG-Payments-Oncall", person.employee_id, None, None))
    for person in people(eng("sre"), "github-admins", 4) + people(
        lambda p: p.department == "engineering" and p.level == Level.DIRECTOR, "github-admins-d", 2
    ):
        rows.append(("Okta_GitHub_Admin", person.employee_id, None, None))
    for person in people(eng("sre"), "github-admins-modern", 3):
        rows.append(("sg-okta-github-admin", person.employee_id, None, None))
    for person in people(
        lambda p: p.department == "engineering" and p.hire_date < dt.date(2018, 1, 1),
        "apollo",
        6,
    ):
        rows.append(("Proj-Apollo", person.employee_id, None, None))
    for person in people(
        lambda p: (
            p.department in ("engineering", "data") and p.team in ("platform", "data-platform")
        ),
        "nebula",
        10,
    ):
        rows.append(("PRJ_Nebula", person.employee_id, None, None))
    for person in people(lambda p: p.department == "product", "vega", 4):
        rows.append(("Project-Vega", person.employee_id, None, None))
    for person in people(
        lambda p: p.department in ("engineering", "product") and p.team in ("mobile", "core"),
        "orion",
        12,
    ):
        rows.append(("PRJ_Orion", person.employee_id, None, None))
    for person in people(
        lambda p: p.department in ("product", "engineering", "design", "marketing"),
        "atlas",
        15,
    ):
        rows.append(("PRJ-Atlas-2022", person.employee_id, None, None))
    for person in people(lambda p: p.department in ("engineering", "data"), "helios", 9):
        rows.append(("PRJ-Helios", person.employee_id, None, None))
    for person in people(lambda p: p.department == "finance", "close-fy2024", 6):
        rows.append(("Finance-Close-FY2024", person.employee_id, None, None))
    for person in people(lambda p: p.department == "finance" and p.team == "fpa", "revrep", 3):
        rows.append(("Revenue-Reporting", person.employee_id, None, None))
    for person in people(
        lambda p: p.level in (Level.MANAGER, Level.DIRECTOR) and p.department != "engineering",
        "projects-write",
        18,
    ):
        rows.append(("sg-share-projects-write", person.employee_id, None, None))
    for person in people(lambda p: p.department == "product", "design-readers", 8):
        rows.append(("sg-share-design-read-only", person.employee_id, None, None))
    for person in people(lambda p: p.department == "legal", "people-readers", 2):
        rows.append(("sg-share-people-read-only", person.employee_id, None, None))
    for person in people(
        lambda p: p.department == "finance" and p.level != Level.IC, "legal-readers", 3
    ):
        rows.append(("sg-share-legal-read-only", person.employee_id, None, None))
    for person in people(
        lambda p: p.department == "people" and p.level != Level.IC, "hr-confidential", 2
    ):
        rows.append(("HR-Confidential", person.employee_id, None, None))
    for person in people(
        lambda p: p.department == "legal" or (p.department == "sales" and p.level != Level.IC),
        "docusign",
        30,
    ):
        rows.append(("sg-okta-docusign-sender", person.employee_id, None, None))
    for person in people(
        lambda p: p.department == "sales" and p.team == "sales-ops", "salesforce-admin", 3
    ):
        rows.append(("sg-okta-salesforce-admin", person.employee_id, None, None))
    for person in people(
        lambda p: p.department == "it" and p.team == "corporate-systems", "workday-admin-it", 1
    ):
        rows.append(("sg-okta-workday-admin", person.employee_id, None, None))
    for person in people(
        lambda p: p.department == "engineering" and p.level == Level.MANAGER, "jira-admin", 4
    ):
        rows.append(("sg-okta-jira-admin", person.employee_id, None, None))
    return rows


# ---------------------------------------------------------------------------
# Assembly.
# ---------------------------------------------------------------------------


def _ownership(groups: list[Group], employees: Sequence[Employee]) -> list[Group]:
    """``managedBy``: standard groups have owners; older ones often do not, or name somebody
    who has since left."""
    heads = {
        person.department: person.employee_id
        for person in employees
        if person.is_active and "role:department-head" in person.tags
    }
    heads["executive"] = next(
        person.employee_id for person in employees if "role:ceo" in person.tags
    )
    it_identity = sorted(
        person.employee_id
        for person in employees
        if person.is_active
        and person.department == "it"
        and person.team == "identity-access"
        and person.level == Level.MANAGER
    )
    formers = [person for person in employees if not person.is_active]
    owned: list[Group] = []
    for group in groups:
        draws = Draws("owner", group.key)
        owner: str | None
        if group.category is Category.BUILTIN:
            owner = None
        elif group.era is Era.STANDARD:
            if group.application or group.department is None:
                owner = it_identity[0] if it_identity else heads["it"]
            else:
                owner = heads.get(group.department, heads["it"])
        else:
            roll = draws.random()
            if roll < 0.45 and group.era in (Era.LEGACY, Era.TEAM):
                owner = None
            elif roll < 0.70:
                candidates = [
                    person
                    for person in formers
                    if person.hire_date <= group.created
                    and (group.department is None or person.department == group.department)
                ]
                owner = draws.choice(candidates).employee_id if candidates else None
            else:
                owner = heads.get(group.department or "it", heads["it"])
        owned.append(replace(group, owner_id=owner))
    return owned


def _assign_identifiers(
    drafts: list[_Draft], groups: list[Group]
) -> tuple[list[Employee], list[Group]]:
    """Employee numbers, names, mail, account names and RIDs, in creation order."""
    ordered = sorted(
        drafts, key=lambda draft: (draft.hire_date, Draws("order", draft.key).next_int())
    )
    names = _name_people(ordered)
    ids = {draft.key: f"NM-{index:05d}" for index, draft in enumerate(ordered, start=1)}

    # RIDs: accounts and groups interleaved by creation date, the way a DC allocates them.
    creations: list[tuple[dt.date, int, str]] = [
        (draft.hire_date, 1, f"user:{draft.key}") for draft in ordered
    ]
    creations += [
        (group.created, 0, f"group:{group.name}")
        for group in groups
        if group.name not in BUILTIN_RIDS
    ]
    rid_for: dict[str, int] = {}
    for offset, (_, _, object_key) in enumerate(sorted(creations)):
        rid_for[object_key] = FIRST_RID + offset

    used_mail: Counter[str] = Counter()
    used_sam: Counter[str] = Counter()
    employees: list[Employee] = []
    for draft in ordered:
        given, family = names[draft.key]
        local = f"{_local_part(given)}.{_local_part(family)}"
        used_mail[local] += 1
        mail_local = local if used_mail[local] == 1 else f"{local}{used_mail[local]}"
        sam_base = (_local_part(given)[:1] + _local_part(family))[:18]
        used_sam[sam_base] += 1
        sam = sam_base if used_sam[sam_base] == 1 else f"{sam_base}{used_sam[sam_base]}"
        employees.append(
            Employee(
                employee_id=ids[draft.key],
                given_name=given,
                family_name=family,
                department=draft.department,
                team=draft.team,
                title=draft.title,
                level=draft.level,
                hire_date=draft.hire_date,
                termination_date=draft.termination_date,
                manager_id=ids.get(draft.manager_key) if draft.manager_key else None,
                previous_department=draft.previous_department,
                transfer_date=draft.transfer_date,
                tags=tuple(sorted(set(draft.tags))),
                email=f"{mail_local}@{DOMAIN_DNS}",
                sam_account_name=sam,
                rid=rid_for[f"user:{draft.key}"],
            )
        )
    stamped = [
        replace(group, rid=BUILTIN_RIDS.get(group.name) or rid_for[f"group:{group.name}"])
        for group in groups
    ]
    return employees, stamped


def _lifecycle_tags(drafts: list[_Draft]) -> None:
    """Decide which leavers kept their groups and which transfers kept old access."""
    for draft in drafts:
        draws = Draws("lifecycle", draft.key)
        if draft.termination_date is not None and draws.chance(0.10):
            draft.tags.append("leaver:kept-groups")
        if draft.previous_department is not None and draws.chance(0.35):
            draft.tags.append("transfer:kept-old-access")


def _golden_people(drafts: list[_Draft]) -> None:
    """Fix the handful of people the golden scenarios are about.

    Chosen by rule from the generated population, then adjusted only where a scenario needs
    a fact the population did not happen to provide (Scenario F needs somebody who moved from
    Finance to Sales Operations).
    """
    by_key = {draft.key: draft for draft in drafts}

    def first(predicate: Callable[[_Draft], bool], label: str) -> _Draft:
        pool = [draft for draft in drafts if draft.termination_date is None and predicate(draft)]
        if not pool:
            raise LookupError(label)
        return Draws("golden", label).sample(pool, 1)[0]

    mover = first(
        lambda d: d.department == "sales" and d.team == "sales-ops" and d.level == Level.IC,
        "scenario-f",
    )
    # The scenario is somebody who worked in Finance for years before moving.
    mover.hire_date = min(mover.hire_date, dt.date(2018, 3, 5))
    mover.title = "Sales Operations Analyst"
    mover.previous_department = "finance"
    mover.transfer_date = dt.date(2022, 2, 1)
    mover.tags = [tag for tag in mover.tags if not tag.startswith("transfer:")]
    mover.tags.append("golden:cross-department")

    oncall = first(
        lambda d: (
            d.department == "engineering"
            and d.team == "payments"
            and d.level == Level.IC
            and d.hire_date < dt.date(2023, 10, 1)
            and d.previous_department is None
        ),
        "scenario-g",
    )
    oncall.tags.append("golden:deep-chain")

    controller = first(
        lambda d: d.department == "finance" and d.team == "accounting" and d.level == Level.MANAGER,
        "controller",
    )
    controller.title = "Corporate Controller"
    controller.tags.append("golden:controller")

    for index, draft in enumerate(
        sorted((d for d in drafts if "role:executive-assistant" in d.tags), key=lambda d: d.key)
    ):
        draft.tags.append(f"golden:executive-assistant-{index + 1}")
    del by_key


@lru_cache(maxsize=1)
def build_canon() -> Canon:
    """The whole Null Meridian canon. Cached: it is pure and somewhat expensive."""
    drafts = _draft_active() + _draft_former()
    _golden_people(drafts)
    _lifecycle_tags(drafts)
    groups = _group_catalog()
    employees, groups = _assign_identifiers(drafts, groups)
    groups = _ownership(groups, employees)
    nesting = _nesting_catalog()
    assignments = _assignments(employees, groups) + _picked(employees)
    names = {group.name for group in groups}
    for edge in nesting:
        if edge.parent not in names or edge.member not in names:
            raise KeyError(f"Nesting names an unknown group: {edge}")
    return Canon(
        employees=tuple(employees),
        groups=tuple(sorted(groups, key=lambda group: group.rid)),
        nesting=tuple(nesting),
        assignments=tuple(sorted(set(assignments), key=lambda row: (row[0], row[1]))),
    )


def iter_dates(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)
