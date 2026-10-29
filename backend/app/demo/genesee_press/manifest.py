r"""The Genesee Press manifest: ADG's demo estate summarized, with its provenance.

``python -m app.demo.genesee_press manifest --out docs/demo/genesee-press.manifest.json``
regenerates the committed ``docs/demo/genesee-press.manifest.json``: the company facts, the
vendored ``synthetic-enterprise`` snapshot it was read from, every count and distribution the
documentation quotes, the golden scenarios, and SHA-256 digests of the employee, group and
membership lists ADG's demo loads. The test suite checks it, so a change to the vendored
projection or to the estate that is not also a change to this file fails a test.

ADG no longer exports an identity canon for other applications to read (formerly
``null-meridian.canon.json``): the shared world lives in ``40ozs/synthetic-enterprise``, and
each application reads its own projection of it.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from typing import Any, Final

from app.demo.genesee_press import vendor
from app.demo.genesee_press.canon import (
    ACCOUNT_DELETION_DAYS,
    ACQUISITION_NAME,
    ACQUISITION_TAG,
    COMPANY,
    DATASET_KEY,
    DEPARTMENTS,
    DOMAIN_DN,
    DOMAIN_DNS,
    DOMAIN_NETBIOS,
    DOMAIN_SID,
    ERA_STARTS,
    FOUNDED,
    MAIL_DOMAIN,
    NAMESPACE,
    OKTA_ADOPTED,
    SNAPSHOT,
    STANDARD_ERA_EXCEPTIONS,
    STANDARDS_ADOPTED,
    Canon,
    Category,
    Era,
    build_canon,
    is_standard_name,
)
from app.demo.genesee_press.estate import (
    GeneseePressEstate,
    build_estate,
)
from app.demo.genesee_press.generations import GENERATIONS, build_generations
from app.demo.genesee_press.scenarios import golden_scenarios

__all__ = ["MANIFEST_FORMAT", "build_manifest", "canon_digests"]

MANIFEST_FORMAT: Final = "genesee-press-manifest/1"

NOTICE: Final = (
    "Synthetic. Genesee Press is a fictional company. Every name, address, identifier, SID, "
    "GUID, host name, share and path here comes from the synthetic world in "
    "40ozs/synthetic-enterprise or from ADG's own seeded estate, and describes no real "
    "person, organization or system."
)


def _employee_row(person: Any) -> list[Any]:
    return [
        person.employee_id,
        person.canonical_id,
        person.given_name,
        person.family_name,
        person.email,
        person.sam_account_name,
        person.sid,
        person.object_guid,
        person.department,
        person.team,
        person.title,
        person.level.value,
        person.manager_id,
        person.hire_date.isoformat(),
        person.termination_date.isoformat() if person.termination_date else None,
        person.previous_department,
        person.transfer_date.isoformat() if person.transfer_date else None,
        list(person.tags),
    ]


EMPLOYEE_COLUMNS: Final = (
    "employee_id",
    "canonical_id",
    "given_name",
    "family_name",
    "email",
    "sam_account_name",
    "sid",
    "object_guid",
    "department",
    "team",
    "title",
    "level",
    "manager_id",
    "hire_date",
    "termination_date",
    "previous_department",
    "transfer_date",
    "tags",
)


def _group_row(group: Any) -> list[Any]:
    return [
        group.name,
        group.canonical_id,
        group.sid,
        group.object_guid,
        group.created.isoformat(),
        group.era.value,
        group.category.value,
        group.department,
        group.application,
        group.tier,
        group.scope,
        group.group_type,
        group.owner_id,
        group.deleted.isoformat() if group.deleted else None,
        group.distinguished_name,
    ]


GROUP_COLUMNS: Final = (
    "name",
    "canonical_id",
    "sid",
    "object_guid",
    "created",
    "era",
    "category",
    "department",
    "application",
    "tier",
    "scope",
    "group_type",
    "owner_employee_id",
    "deleted",
    "distinguished_name",
)


def _digest(rows: Iterable[Any]) -> str:
    hasher = hashlib.sha256()
    for row in rows:
        hasher.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
        hasher.update(b"\n")
    return hasher.hexdigest()


def canon_digests(canon: Canon | None = None) -> dict[str, str]:
    canon = canon or build_canon()
    return {
        "employees": _digest(_employee_row(person) for person in canon.employees),
        "groups": _digest(_group_row(group) for group in canon.groups),
        "memberships_at_snapshot": _digest(list(edge) for edge in canon.memberships_on(SNAPSHOT)),
    }


def _company() -> dict[str, Any]:
    return {
        "name": COMPANY,
        "mail_domain": MAIL_DOMAIN,
        "directory_domain": DOMAIN_DNS,
        "netbios": DOMAIN_NETBIOS,
        "distinguished_name": DOMAIN_DN,
        "domain_sid": DOMAIN_SID,
        "dataset": DATASET_KEY,
        "snapshot": SNAPSHOT.isoformat(),
        "founded": FOUNDED.isoformat(),
        "age_years": (SNAPSHOT.year - FOUNDED.year),
        "identity_provider_adopted": OKTA_ADOPTED.isoformat(),
        "standards_adopted": STANDARDS_ADOPTED.isoformat(),
        "acquisition": ACQUISITION_NAME,
        "account_deletion_days_after_termination": ACCOUNT_DELETION_DAYS,
    }


def _source() -> dict[str, Any]:
    """Where the directory came from: the vendored projection and its lock."""
    lock = vendor.lock()
    head = vendor.header()
    return {
        "repository": lock["repository"],
        "commit": lock["commit"],
        "snapshot": lock["snapshot"],
        "contract_version": lock["contract_version"],
        "dataset": lock["dataset"],
        "projection": lock["projection"],
        "manifest_sha256": lock["manifest_sha256"],
        "canonical_digest": head["canonical_digest"],
        "options": head["options"],
        "estate_scope": (
            "ADG's directory catalogue: every employee, the directory groups with an ADG "
            "legacy_name, and the memberships among them"
        ),
    }


ALGORITHMS: Final[dict[str, str]] = {
    "draws": (
        "value(label, n) = int.from_bytes(sha256(f'{seed}|{label}|{n}').digest()[:8], 'big'); "
        "random() = value / 2**64; below(k) = value % k; seed and server labels keep their "
        "ADG v1 spellings (legacy draw keys)"
    ),
    "uuid": f"uuid5({NAMESPACE}, '<kind>:<key>'); NAMESPACE = uuid5(NAMESPACE_DNS, "
    f"'{DOMAIN_DNS}'); run and batch ids only",
    "directory": "people, accounts, groups, memberships, SIDs and objectGUIDs are read from "
    "the vendored synthetic-enterprise adg projection; none is derived here",
}


def _statistics(canon: Canon, snapshot: GeneseePressEstate) -> dict[str, Any]:
    active = canon.active
    former = canon.former
    states = Counter(person.account_state(SNAPSHOT) for person in canon.employees)
    groups = [group for group in canon.groups if group.exists_on(SNAPSHOT)]
    memberships = canon.memberships_on(SNAPSHOT)
    group_names = {group.name for group in groups}
    nested = [edge for edge in memberships if edge[1] in group_names]
    depths = canon.nesting_depths(SNAPSHOT)
    standard = [group for group in groups if group.era is Era.STANDARD]
    conforming = [group for group in standard if is_standard_name(group.name)]

    estate = snapshot.estate
    folders = snapshot.folders
    explicit = [folder for folder in folders if folder.assignment != "inherited"]
    user_aces = [
        ace for folder in folders for ace in folder.explicit if ace.trustee.startswith("GP-")
    ]
    group_aces = [
        ace
        for folder in folders
        for ace in folder.explicit
        if not ace.trustee.startswith("GP-")
        and ace.trustee not in ("SYSTEM", "ADMINISTRATORS", "CREATOR_OWNER")
    ]

    def share(counter: Counter[str], total: int) -> dict[str, float]:
        return {key: round(value / total, 3) for key, value in sorted(counter.items())}

    generations = {}
    for generation, transcripts in build_generations():
        generations[generation.key] = {
            "observed_on": generation.day.isoformat(),
            "title": generation.title,
            "runs": len(transcripts),
            "observations": sum(item.observation_count for item in transcripts),
            "statuses": dict(sorted(Counter(item.status for item in transcripts).items())),
        }

    return {
        "employees": {
            "active": len(active),
            "former": len(former),
            "accounts_enabled": states["enabled"],
            "accounts_disabled": states["disabled"],
            "accounts_deleted": states["deleted"],
            "by_department": {
                department.slug: sum(1 for p in active if p.department == department.slug)
                for department in DEPARTMENTS
            },
            "by_level": dict(sorted(Counter(p.level.value for p in active).items())),
            "hired_by_year": dict(sorted(Counter(str(p.hire_date.year) for p in active).items())),
            "left_by_year": dict(
                sorted(
                    Counter(
                        str(p.termination_date.year) for p in former if p.termination_date
                    ).items()
                )
            ),
            "tenure_over_8_years": sum(
                1 for p in active if (SNAPSHOT - p.hire_date).days >= 8 * 365
            ),
            "hired_in_last_90_days": sum(1 for p in active if (SNAPSHOT - p.hire_date).days < 90),
            "transferred": sum(1 for p in active if p.previous_department),
            "acquired": sum(1 for p in canon.employees if ACQUISITION_TAG in p.tags),
        },
        "groups": {
            "total": len(groups),
            "deleted_before_snapshot": sum(1 for g in canon.groups if not g.exists_on(SNAPSHOT)),
            "by_era": {era.value: sum(1 for g in groups if g.era is era) for era in Era},
            "by_category": dict(sorted(Counter(g.category.value for g in groups).items())),
            "standard_era": len(standard),
            "standard_era_conforming": len(conforming),
            "standard_era_exceptions": sorted(
                g.name for g in standard if not is_standard_name(g.name)
            ),
            "ownerless": sum(
                1 for g in groups if g.owner_id is None and g.category is not Category.BUILTIN
            ),
            "owned_by_former_employee": sum(
                1 for g in groups if g.owner_id and not canon.employees_by_id[g.owner_id].is_active
            ),
        },
        "memberships": {
            "member_edges": len(memberships),
            "person_edges": len(memberships) - len(nested),
            "nested_group_edges": len(nested),
            "primary_group_edges": sum(
                1 for p in canon.employees if p.account_state(SNAPSHOT) in ("enabled", "disabled")
            ),
            "max_nesting_depth": max(depths.values()),
            "nesting_depth_histogram": {
                str(depth): count for depth, count in sorted(Counter(depths.values()).items())
            },
        },
        "estate": {
            "servers": len(estate.servers),
            "shares": sum(1 for s in estate.shares if not s.is_special),
            "administrative_shares": sum(1 for s in estate.shares if s.is_special),
            "share_aces": sum(len(s.aces) for s in estate.shares),
            "directories": len(estate.directories),
            "ntfs_aces": sum(d.ace_count for d in estate.directories),
            "explicit_acl_objects": len(explicit),
            "protected_directories": sum(1 for d in estate.directories if d.dacl_protected),
            "explicit_direct_user_aces": len(user_aces),
            "explicit_group_aces": len(group_aces),
            "deny_aces": sum(1 for folder in folders for ace in folder.explicit if ace.deny),
            "assignment_distribution": share(
                Counter(folder.assignment for folder in explicit), len(explicit)
            ),
            "posture_distribution": share(
                Counter(folder.posture.value for folder in folders), len(folders)
            ),
            "direct_user_intents": dict(
                sorted(Counter(ace.intent.value for ace in user_aces if ace.intent).items())
            ),
            "directory_depth_histogram": {
                str(depth): count
                for depth, count in sorted(
                    Counter(d.depth_from_share_root for d in estate.directories).items()
                )
            },
        },
        "generations": generations,
    }


def build_manifest() -> dict[str, Any]:
    canon = build_canon()
    snapshot = build_estate(SNAPSHOT)
    document: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "notice": NOTICE,
        "company": _company(),
        "source": _source(),
        "algorithms": ALGORITHMS,
        "eras": [{"era": era.value, "from": start.isoformat()} for era, start in ERA_STARTS],
        "departments": [
            {
                "slug": department.slug,
                "name": department.name,
                "active_employees": sum(
                    1 for person in canon.active if person.department == department.slug
                ),
                "founded": department.founded.isoformat(),
                "department_group": department.dept_group,
                "teams": [
                    {"slug": team.slug, "name": team.name, "group": team.group}
                    for team in department.teams
                ],
            }
            for department in DEPARTMENTS
        ],
        "standard_era_exceptions": list(STANDARD_ERA_EXCEPTIONS),
        "generations": [
            {
                "key": generation.key,
                "observed_on": generation.day.isoformat(),
                "title": generation.title,
                "ntfs_failed": sorted(generation.ntfs_failed),
            }
            for generation in GENERATIONS
        ],
        "statistics": _statistics(canon, snapshot),
        "golden_scenarios": golden_scenarios(),
        "digests": canon_digests(canon),
    }
    return document
