r"""The Genesee Press directory canon, read from the shared synthetic world.

Genesee Press is the company ADG's demo describes, and the one the OGWA, CSW and AO demos
describe too: ``40ozs/synthetic-enterprise`` holds it once, as one canonical world, and gives
each application a projection of it. This module decides *who and what exists* in ADG's demo
directory: the departments and teams, the 1,800 people employed on the snapshot date and the
people who left before it, every security group and when it was created, who belongs to what,
and who manages each group. It decides nothing about file servers;
:mod:`app.demo.genesee_press.estate` builds those, ADG's own data, on top of this.

## Where the canon comes from

:func:`build_canon` reads the vendored ``adg`` projection (:mod:`.vendor`) and nothing else:
no generator, no network, no clock. Every value is the canonical one — mail and UPNs on
``geneseepress.example``, the ``corp.geneseepress.example`` / ``GPCORP`` directory, ``GP-``
employee ids, ``sec-*`` standard groups — with the canonical id of each person, account and
group kept beside it. SIDs and objectGUIDs are the directory's opaque identifiers and come
from the projection unchanged.

**ADG's demo estate is ADG's directory catalogue.** The canonical directory is the union of
the catalogues the shared world was reconciled from (ADR-0003): 509 groups (dataset 2.1.0;
521 before SYNTH-CROSSAPP-01 merged 12 case-only duplicates into ADG's groups), 206 of which are
the groups ADG's file estate was designed around. Those are the groups whose projection record
carries an ADG ``legacy_name``; the demo loads them, every employee, and the memberships among
them. The other 303 groups (and the 20 nestings that join them to ADG's standard share groups)
are canonical too, and stay out: an application consumes the subset of the shared world it
needs. One is added only when a concrete cross-application scenario needs ADG to see it, with
its effect on memberships, routes, findings, fingerprints and parity measured (SYNTH-ADG-01
report, D-ADG-02).

## Determinism

There is no :mod:`random` here and no wall clock. The estate's organic ACLs come from
:class:`Draws`, a counter-mode stream over SHA-256::

    value(label, n) = int.from_bytes(sha256(f"{LEGACY_DRAW_SEED}|{label}|{n}").digest()[:8], "big")

The seed and the labels keep their ADG v1 spellings (:data:`LEGACY_DRAW_SEED`, the servers'
``legacy_name``), so the file estate is the one ADG has always shipped: the same folders, the
same entries, the same findings. They are seeds, never shown. Changing them is a new dataset.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property, lru_cache
from typing import Any, Final, TypeVar

from app.demo.genesee_press import vendor

__all__ = [
    "ACCOUNT_DELETION_DAYS",
    "ACQUISITION_NAME",
    "ACQUISITION_TAG",
    "COMPANY",
    "DATASET_KEY",
    "DEPARTMENTS",
    "DEPARTMENTS_BY_SLUG",
    "DOMAIN_DN",
    "DOMAIN_DNS",
    "DOMAIN_NETBIOS",
    "DOMAIN_SID",
    "FOUNDED",
    "LEGACY_DRAW_SEED",
    "MAIL_DOMAIN",
    "NAMESPACE",
    "OKTA_ADOPTED",
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
    "draw_key",
    "era_of",
    "legacy_key",
]

T = TypeVar("T")

# ---------------------------------------------------------------------------
# The fixed facts, read from the projection's organization and directory.
# ---------------------------------------------------------------------------


def _one(name: str) -> dict[str, Any]:
    rows = vendor.records(name)
    if len(rows) != 1:
        raise vendor.VendorError(f"{name} holds {len(rows)} records; ADG reads exactly one.")
    return rows[0]


_ORGANIZATION: Final = _one("organization.jsonl")
_DIRECTORY: Final = _one("directory.jsonl")
_MILESTONES: Final[dict[str, dt.date]] = {
    item["label"]: dt.date.fromisoformat(item["on"]) for item in _ORGANIZATION["milestones"]
}

#: The dataset key Demo Mode records in the deployment marker.
DATASET_KEY: Final = "genesee-press-v1"
#: The seed of the estate's organic draws. Its ADG v1 spelling, kept so the file estate is
#: unchanged by the move to the shared world; an opaque seed, never displayed.
LEGACY_DRAW_SEED: Final = "null-meridian-v1"

COMPANY: Final[str] = _ORGANIZATION["name"]
MAIL_DOMAIN: Final[str] = _ORGANIZATION["primary_domain"]
"""Mail and UPN suffix: ``geneseepress.example``."""
DOMAIN_DNS: Final[str] = _DIRECTORY["dns_domain"]
"""The Active Directory DNS domain: ``corp.geneseepress.example``."""
DOMAIN_NETBIOS: Final[str] = _DIRECTORY["netbios_name"]
DOMAIN_DN: Final = ",".join(f"DC={label}" for label in DOMAIN_DNS.split("."))
DOMAIN_SID: Final[str] = _DIRECTORY["domain_sid"]
ACCOUNT_DELETION_DAYS: Final[int] = _DIRECTORY["account_deletion_days"]
"""A disabled leaver's account is deleted this many days after the termination date. The
policy is why recent leavers appear in ADG as disabled accounts while older ones are gone,
and why an account crossing the line between two scans is tombstoned by the second one."""

FOUNDED: Final = dt.date.fromisoformat(_ORGANIZATION["founded_on"])
SNAPSHOT: Final = dt.date.fromisoformat(vendor.header()["clock"]["anchor"][:10])
OKTA_ADOPTED: Final = _MILESTONES["identity provider adopted"]
STANDARDS_ADOPTED: Final = _MILESTONES["sec-* group naming standard adopted"]
(ACQUISITION_NAME, ACQUISITION_DATE) = next(
    (label.removeprefix("acquired "), on)
    for label, on in _MILESTONES.items()
    if label.startswith("acquired ")
)
ACQUISITION_TAG: Final = "acquired:" + ACQUISITION_NAME.casefold().replace(" ", "-")

#: The namespace of the UUIDs ADG's demo derives (run and batch ids).
NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_DNS, DOMAIN_DNS)


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
        payload = f"{LEGACY_DRAW_SEED}|{self._label}|{self._counter}".encode()
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


#: Estate names (ADG's own share and folder names) renamed with the acquisition. The
#: directory's renames come from the projection's ``legacy_*`` fields instead.
LEGACY_ESTATE_SPELLINGS: Final[dict[str, str]] = {
    "Brindlewood-Analytics": "Lumen-Analytics",
    "Brindlewood-Models": "Lumen-Models",
}


@lru_cache(maxsize=1)
def _legacy_spellings() -> dict[str, str]:
    spellings = {
        record["employee_id"]: record["legacy_employee_id"]
        for record in vendor.records("employees.jsonl")
    }
    for name in ("groups.jsonl", "hosts.jsonl"):
        spellings.update(
            {
                record["name"]: record["legacy_name"]
                for record in vendor.records(name)
                if record["legacy_name"]
            }
        )
    spellings.update(LEGACY_ESTATE_SPELLINGS)
    return spellings


def legacy_key(name: str) -> str:
    """``name`` (an employee id, group, server, share or folder) in its ADG v1 spelling.

    Only for seeded draw labels and the orders draws pick from, so renaming a value to its
    Genesee Press spelling leaves every roll of the estate as ADG v1 made it. Never shown.
    """
    return _legacy_spellings().get(name, name)


def draw_key(path: str) -> str:
    r"""A path (``\\SERVER\Share``, ``SERVER\Share``, ``Folder/Sub``) with each segment in its
    v1 spelling: the label of a seeded draw."""
    return re.sub(r"[^\\/]+", lambda match: legacy_key(match.group(0)), path)


# ---------------------------------------------------------------------------
# Eras.
# ---------------------------------------------------------------------------


class Era(StrEnum):
    """The four generations of Genesee Press's group-naming conventions."""

    LEGACY = "legacy"
    """2016-10-01 to 2019-09-30: AD and file-share conventions, one per administrator."""
    TEAM = "team"
    """2019-10-01 to 2021-09-30: team- and application-specific groups."""
    TRANSITIONAL = "transitional"
    """2021-10-01 to 2023-09-30: the identity provider arrives; its conventions and AD's side
    by side."""
    STANDARD = "standard"
    """2023-10-01 onward: the ``sec-*`` standard."""


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
#: matches neither is listed in :data:`STANDARD_ERA_EXCEPTIONS`.
MODERN_SHARE_PATTERN: Final = re.compile(r"^sec-share-[a-z0-9]+(?:-[a-z0-9]+)*-(read-only|write)$")
MODERN_OKTA_PATTERN: Final = re.compile(r"^sec-okta-[a-z0-9]+(?:-[a-z0-9]+)*-[a-z0-9]+$")

#: Standard-era groups that knowingly ignore the standard. Each is a real-world shortcut:
#: a group made in a hurry during a close, a project, and a break-glass group the identity
#: team named for findability rather than convention.
STANDARD_ERA_EXCEPTIONS: Final[tuple[str, ...]] = (
    "Finance-Close-FY2024",
    "IT-Okta-Break-Glass",
    "PRJ-Helios",
)


def is_standard_name(name: str) -> bool:
    return bool(MODERN_SHARE_PATTERN.match(name) or MODERN_OKTA_PATTERN.match(name))


# ---------------------------------------------------------------------------
# Departments and teams: the projection's org units, keyed by ADG's slugs.
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
    group: str | None = None
    """The AD team group its members belong to, or ``None`` for a team that never got one."""
    canonical_id: str = ""


@dataclass(frozen=True, slots=True)
class Department:
    slug: str
    name: str
    founded: dt.date
    dept_group: str
    """The legacy department group: every member of the department is in it, directly or
    through a team group."""
    teams: tuple[Team, ...] = ()
    canonical_id: str = ""

    @property
    def ou(self) -> str:
        return f"OU={self.name.replace('/', '-')},OU=People,{DOMAIN_DN}"


def _catalogue_groups() -> list[dict[str, Any]]:
    """The groups of ADG's directory catalogue: those the projection maps to an ADG name."""
    return [record for record in vendor.records("groups.jsonl") if record["legacy_name"]]


def _departments() -> tuple[Department, ...]:
    units = {record["canonical_id"]: record for record in vendor.records("org_units.jsonl")}
    unit_group: dict[tuple[str, str], str] = {}
    for record in _catalogue_groups():
        if record["category"] in ("department", "team") and record["department_canonical_id"]:
            unit_group[(record["category"], record["department_canonical_id"])] = record["name"]
    departments = []
    for unit in units.values():
        if unit["unit_type"] != "department":
            continue
        teams = tuple(
            Team(
                slug=team["legacy_slug"].split("/", 1)[1],
                name=team["name"],
                group=unit_group.get(("team", team["canonical_id"])),
                canonical_id=team["canonical_id"],
            )
            for team in sorted(units.values(), key=lambda item: item["legacy_slug"])
            if team["parent_canonical_id"] == unit["canonical_id"]
        )
        departments.append(
            Department(
                slug=unit["legacy_slug"],
                name=unit["name"],
                founded=dt.date.fromisoformat(unit["founded_on"]),
                dept_group=unit_group[("department", unit["canonical_id"])],
                teams=teams,
                canonical_id=unit["canonical_id"],
            )
        )
    return tuple(sorted(departments, key=lambda item: item.slug))


DEPARTMENTS: Final = _departments()
DEPARTMENTS_BY_SLUG: Final[dict[str, Department]] = {item.slug: item for item in DEPARTMENTS}


# ---------------------------------------------------------------------------
# People.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Employee:
    employee_id: str
    given_name: str
    family_name: str
    department: str
    """The department slug on the snapshot date, or at departure for a former employee."""
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
    sid: str = ""
    object_guid: str = ""
    canonical_id: str = ""
    account_canonical_id: str = ""

    @property
    def rid(self) -> int:
        return int(self.sid.rsplit("-", 1)[1])

    @property
    def display_name(self) -> str:
        return f"{self.given_name} {self.family_name}"

    @property
    def user_principal_name(self) -> str:
        return self.email

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
    era: Era
    department: str | None = None
    application: str | None = None
    tier: str | None = None
    scope: str = "global"
    group_type: str = "security"
    description: str = ""
    deleted: dt.date | None = None
    owner_id: str | None = None
    sid: str = ""
    object_guid: str = ""
    canonical_id: str = ""

    @property
    def rid(self) -> int:
        return int(self.sid.rsplit("-", 1)[1])

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


@dataclass(frozen=True, slots=True)
class Nesting:
    """``member`` is a member of ``parent`` from ``added`` until ``removed`` (each ``None``
    when unbounded)."""

    parent: str
    member: str
    added: dt.date | None = None
    removed: dt.date | None = None
    canonical_id: str = ""


# ---------------------------------------------------------------------------
# The canon.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Canon:
    """Everything about Genesee Press's directory that ADG's demo estate is built on."""

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
            if nesting.removed is not None and day >= nesting.removed:
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


def _day(instant: str | None) -> dt.date | None:
    return dt.date.fromisoformat(instant[:10]) if instant else None


@lru_cache(maxsize=1)
def build_canon() -> Canon:
    """The whole directory canon, from the vendored projection. Cached: it is pure."""
    units = {record["canonical_id"]: record for record in vendor.records("org_units.jsonl")}

    def slug(unit_id: str | None) -> str | None:
        return units[unit_id]["legacy_slug"] if unit_id else None

    people = sorted(vendor.records("employees.jsonl"), key=lambda record: record["employee_id"])
    employee_of = {record["canonical_id"]: record["employee_id"] for record in people}
    by_account = {record["account_canonical_id"]: record["employee_id"] for record in people}
    employees = tuple(
        Employee(
            employee_id=record["employee_id"],
            given_name=record["given_name"],
            family_name=record["family_name"],
            department=units[record["department_canonical_id"]]["legacy_slug"],
            team=(
                units[record["team_canonical_id"]]["legacy_slug"].split("/", 1)[1]
                if record["team_canonical_id"]
                else None
            ),
            title=record["title"],
            level=Level(record["level"]),
            hire_date=dt.date.fromisoformat(record["hire_date"]),
            termination_date=_day(record["termination_date"]),
            manager_id=employee_of.get(record["manager_canonical_id"] or ""),
            previous_department=slug(record["previous_department_canonical_id"]),
            transfer_date=_day(record["transfer_date"]),
            tags=tuple(sorted(record["tags"])),
            email=record["email"],
            sam_account_name=record["sam_account_name"],
            sid=record["sid"],
            object_guid=record["object_guid"],
            canonical_id=record["canonical_id"],
            account_canonical_id=record["account_canonical_id"],
        )
        for record in people
    )

    catalogue = _catalogue_groups()
    name_of = {record["canonical_id"]: record["name"] for record in catalogue}
    groups = tuple(
        sorted(
            (
                Group(
                    name=record["name"],
                    created=dt.date.fromisoformat(record["created_at"][:10]),
                    category=Category(record["category"]),
                    era=Era(record["naming_era"]),
                    department=slug(record["department_canonical_id"]),
                    application=record["legacy_application_token"],
                    tier=record["tier"],
                    scope=record["scope"],
                    group_type=record["group_type"],
                    description=record["description"] or "",
                    deleted=_day(record["deleted_at"]),
                    owner_id=employee_of.get(record["owner_canonical_id"] or ""),
                    sid=record["sid"],
                    object_guid=record["object_guid"],
                    canonical_id=record["canonical_id"],
                )
                for record in catalogue
            ),
            key=lambda group: group.rid,
        )
    )

    nesting: list[Nesting] = []
    assignments: set[tuple[str, str, dt.date | None, dt.date | None]] = set()
    for record in vendor.records("memberships.jsonl"):
        parent = name_of.get(record["group_canonical_id"])
        if parent is None:
            continue
        since, until = _day(record["valid_from"]), _day(record["valid_to"])
        if record["member_kind"] == "group":
            member = name_of.get(record["member_canonical_id"])
            if member is not None:
                nesting.append(Nesting(parent, member, since, until, record["canonical_id"]))
        elif record["member_canonical_id"] in by_account:
            assignments.add((parent, by_account[record["member_canonical_id"]], since, until))
    return Canon(
        employees=employees,
        groups=groups,
        nesting=tuple(sorted(nesting, key=lambda edge: (edge.parent, edge.member))),
        assignments=tuple(sorted(assignments, key=lambda row: (row[0], row[1], str(row[2])))),
    )


def iter_dates(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)
