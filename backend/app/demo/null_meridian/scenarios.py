r"""The golden scenarios: eight walkthroughs that exist after every reset.

Each one names the folders, people and groups it is about, resolved from the canon, so the
documentation, the manifest and the tests all describe the same objects. The people are
chosen by rule (``golden:*`` tags in :mod:`.canon`); their names come from the seed.
"""

from __future__ import annotations

from typing import Any, Final

from app.demo.null_meridian.canon import Canon, build_canon

__all__ = ["SCENARIO_IDS", "golden_scenarios"]

SCENARIO_IDS: Final = ("A", "B", "C", "D", "E", "F", "G", "H")

FINANCE = r"\\NM-FIN-FS01\Finance"


def _person(canon: Canon, tag: str) -> dict[str, str]:
    person = canon.one(tag)
    return {
        "employee_id": person.employee_id,
        "name": person.display_name,
        "email": person.email,
        "sid": person.sid,
        "department": person.department,
        "title": person.title,
    }


def golden_scenarios(canon: Canon | None = None) -> list[dict[str, Any]]:
    canon = canon or build_canon()
    deep = _person(canon, "golden:deep-chain")
    mover = _person(canon, "golden:cross-department")
    controller = _person(canon, "golden:controller")
    return [
        {
            "id": "A",
            "title": "Modern healthy share",
            "resource": r"\\NM-CORP-FS02\Design",
            "groups": ["sg-share-design-read-only", "sg-share-design-write"],
            "demonstrates": (
                "A 2023 share built to the standard: two sg-share-* groups with people as "
                "direct members, inheritance unbroken, no direct user entries, no legacy groups."
            ),
            "look_at": [
                "GET /api/v1/resources/{\\\\NM-CORP-FS02\\Design}/acl",
                "GET /api/v1/groups/{sg-share-design-write SID}/members",
            ],
        },
        {
            "id": "B",
            "title": "Legacy finance hierarchy",
            "resource": FINANCE,
            "groups": [
                "Finance-RW",
                "GG_Finance_RW",
                "Finance_Read",
                "FS-Finance-R",
                "FIN-Close-Team",
                "sg-share-finance-write",
                "sg-share-finance-read-only",
            ],
            "people": [controller],
            "demonstrates": (
                "Four generations of finance groups on one 2017 ACL. FIN-Accounting -> "
                "Finance-Team -> Finance-RW -> GG_Finance_RW grants Modify, FIN-Close-Team "
                "grants it again, and about 40% of Finance also hold sg-share-finance-write "
                "directly: the 2024 migration added the modern groups and removed nothing. "
                "Close folders carry direct entries for named accountants."
            ),
            "look_at": [
                f"GET /api/v1/resources/{{{FINANCE}}}/acl",
                f"GET /api/v1/access/resources/{{{FINANCE}\\Accounting\\Close}}/principals",
            ],
        },
        {
            "id": "C",
            "title": "Engineering sprawl",
            "resource": r"\\NM-ENG-FS01\Engineering",
            "groups": [
                "Share_Engineering_Modify",
                "Engineering-FS",
                "APP-GitHub-Users",
                "sg-share-engineering-write",
                "ENG-Payments",
                "Proj-Apollo",
                "PRJ_Nebula",
                "PRJ_Orion",
            ],
            "demonstrates": (
                "The largest share: team folders, project folders, inherited access from "
                "five root entries, historical direct entries for long-tenured engineers, and "
                "an application group (APP-GitHub-Users) that became a file-share dependency. "
                "Most engineers reach Modify by three or more routes."
            ),
            "look_at": [
                "GET /api/v1/access/resources/"
                r"{\\NM-ENG-FS01\Engineering\Services\Payments}/principals"
            ],
        },
        {
            "id": "D",
            "title": "Sensitive People share",
            "resource": r"\\NM-HR-FS01\People",
            "groups": [
                "HR_Share_RW",
                "HR-Confidential",
                "sg-share-people-write",
                "Hiring-Managers",
                "Payroll_Admins",
            ],
            "demonstrates": (
                "Well controlled: Employee-Relations and Compensation are protected and "
                "limited to HR-Confidential. Two understandable exceptions: Recruiting is "
                "readable by Hiring-Managers (which nests every manager), and a Finance "
                "payroll specialist holds a direct entry on the payroll-vendor transition "
                "folder."
            ),
            "look_at": [r"GET /api/v1/resources/{\\NM-HR-FS01\People\Compensation}/acl"],
        },
        {
            "id": "E",
            "title": "Migration and archive debt",
            "resource": r"\\NM-ARCH-FS01\Archive",
            "also": [r"\\NM-MIG-FS01\Migrations\FS-Consolidation-2020"],
            "groups": [
                "GG_Archive_R",
                "FS-Legacy-Users",
                "Finance-RW",
                "Proj-Zephyr-RW",
                "Project-Vega",
            ],
            "demonstrates": (
                "Broken inheritance on every subfolder; Finance-RW granted twice on one ACL "
                "(Modify and Read & execute); Project-Vega, deleted on 2026-09-10, still named "
                "and now unresolved; home drives naming accounts deleted before ADG's first "
                "scan, an account deleted between the first and second scans, and an IT "
                "migrator's leftover Full control."
            ),
            "look_at": [r"GET /api/v1/resources/{\\NM-ARCH-FS01\Archive\Legacy-Home-Drives}/acl"],
        },
        {
            "id": "F",
            "title": "Cross-department access, two ways",
            "resource": FINANCE + r"\Reports\Revenue",
            "people": [mover],
            "groups": [
                "Sales-Ops",
                "Revenue-Reporting",
                "Finance_Read",
                "FS-Finance-R",
                "sg-share-finance-read-only",
            ],
            "demonstrates": (
                f"{mover['name']} moved from Finance to Sales Operations on 2022-02-01. "
                "Sales-Ops -> Revenue-Reporting grants Read & execute on the Revenue folder "
                "(and Revenue-Reporting -> Finance_Read reaches the share root); the direct "
                "Modify entry from their Finance days was never removed. ADG explains both "
                "paths, and that removing either one leaves the other standing."
            ),
            "look_at": [
                f"GET /api/v1/access/paths/principals/{mover['sid']}/resources/"
                + FINANCE
                + r"\Reports\Revenue"
            ],
        },
        {
            "id": "G",
            "title": "A six-level chain",
            "resource": r"\\NM-ENG-FS01\Engineering\Runbooks\Payments-Legacy",
            "people": [deep],
            "chain": [
                "ENG-Payments-Oncall",
                "ENG-Payments",
                "Engineering-Backend",
                "Engineering",
                "Engineering-FS",
                "Share_Engineering_Modify",
            ],
            "demonstrates": (
                f"{deep['name']} is on the payments on-call rotation. The only entry on this "
                "protected folder is Share_Engineering_Modify, which they reach through six "
                "nested groups — and through seven by a detour via APP-GitHub-Users, the "
                "application group that became a file-share dependency. Valid, and legible to "
                "nobody reading the ACL."
            ),
            "look_at": [
                f"GET /api/v1/principals/{deep['sid']}/membership-paths",
                f"GET /api/v1/access/paths/principals/{deep['sid']}/resources/"
                r"\\NM-ENG-FS01\Engineering\Runbooks\Payments-Legacy",
            ],
        },
        {
            "id": "H",
            "title": "A safe first clean-up",
            "resource": r"\\NM-ENG-FS01\Engineering\Projects\Zephyr",
            "also": [r"\\NM-ENG-FS01\Projects\Zephyr"],
            "groups": ["Proj-Zephyr-RW"],
            "demonstrates": (
                "Proj-Zephyr-RW has had no members since the project ended in 2019 and is still "
                "granted Modify. Removing the entry changes nobody's access, which a simulation "
                "confirms before anybody touches the folder."
            ),
            "look_at": ["GET /api/v1/risks/findings?rule=empty_permission_bearing_group"],
        },
    ]
