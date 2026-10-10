r"""The Null Meridian canon: the company is exactly what the documentation says it is.

Every number the manifest quotes and every rule ``docs/demo/null-meridian.md`` states is
asserted here against a freshly built canon, so the documentation cannot drift from the
generator and the generator cannot drift from the seed.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import re
import uuid
from collections import Counter
from pathlib import Path

import pytest

from app.demo.null_meridian import canon as canon_module
from app.demo.null_meridian.canon import (
    ACTIVE_EMPLOYEES,
    DEPARTMENTS,
    DOMAIN_DNS,
    DOMAIN_NETBIOS,
    DOMAIN_SID,
    FOUNDED,
    NAMESPACE,
    OKTA_ADOPTED,
    SEED,
    SNAPSHOT,
    STANDARD_ERA_EXCEPTIONS,
    STANDARDS_ADOPTED,
    Canon,
    Category,
    Draws,
    Era,
    Level,
    build_canon,
    era_of,
    is_standard_name,
    stable_uuid,
)
from app.demo.null_meridian.manifest import (
    build_manifest,
    canon_digests,
    render_canon_document,
)

REPOSITORY = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPOSITORY / "docs" / "demo" / "null-meridian.manifest.json"
CANON_PATH = REPOSITORY / "docs" / "demo" / "null-meridian.canon.json"

#: The digests of canon v1. A change here is a change to the company: it needs a new seed,
#: a regenerated manifest, and a note to OGWA, not an edit to this constant.
PINNED_DIGESTS = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["digests"]


@pytest.fixture(scope="module")
def canon() -> Canon:
    return build_canon()


class TestTheFixedFacts:
    def test_the_company_constants(self) -> None:
        assert SEED == "null-meridian-v1"
        assert DOMAIN_DNS == "nullmeridian.info"
        assert DOMAIN_NETBIOS == "NULLMERIDIAN"
        assert dt.date(2026, 10, 1) == SNAPSHOT
        assert dt.date(2016, 10, 1) == FOUNDED
        assert dt.date(2021, 10, 1) == OKTA_ADOPTED
        assert dt.date(2023, 10, 1) == STANDARDS_ADOPTED
        assert (SNAPSHOT.year - FOUNDED.year) == 10

    def test_exactly_1800_active_employees(self, canon: Canon) -> None:
        assert ACTIVE_EMPLOYEES == 1800
        assert len(canon.active) == 1800
        assert sum(department.size for department in DEPARTMENTS) == 1800
        counts = Counter(person.department for person in canon.active)
        assert counts == {department.slug: department.size for department in DEPARTMENTS}

    def test_every_active_employee_holds_an_enabled_account_on_the_snapshot(
        self, canon: Canon
    ) -> None:
        assert all(person.account_state(SNAPSHOT) == "enabled" for person in canon.active)

    def test_a_historical_population_of_former_employees(self, canon: Canon) -> None:
        former = canon.former
        assert len(former) >= 400
        assert all(
            person.termination_date and person.termination_date <= SNAPSHOT for person in former
        )
        years = {person.termination_date.year for person in former if person.termination_date}
        assert years >= set(range(2018, 2027))


class TestItIsDeterministic:
    def test_two_builds_are_equal(self, canon: Canon) -> None:
        rebuilt = build_canon.__wrapped__()
        assert rebuilt == canon

    def test_the_digests_are_pinned(self, canon: Canon) -> None:
        """The canon is v1 on every machine. If this fails, the company changed."""
        assert canon_digests(canon) == PINNED_DIGESTS

    def test_the_committed_manifest_is_current(self) -> None:
        committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert committed == json.loads(json.dumps(build_manifest(), ensure_ascii=False)), (
            "docs/demo/null-meridian.manifest.json is stale. Regenerate it with "
            "python -m app.demo.null_meridian manifest "
            "--out ../docs/demo/null-meridian.manifest.json"
        )

    def test_the_committed_canon_is_current(self) -> None:
        assert CANON_PATH.read_text(encoding="utf-8") == render_canon_document(), (
            "docs/demo/null-meridian.canon.json is stale. Regenerate it with "
            "python -m app.demo.null_meridian manifest "
            "--canon-out ../docs/demo/null-meridian.canon.json"
        )

    def test_draws_follow_the_documented_algorithm(self) -> None:
        """So that a second implementation can reproduce the canon from the docs alone."""
        import hashlib

        draws = Draws("example", 7)
        expected = int.from_bytes(
            hashlib.sha256(f"{SEED}|example|7|0".encode()).digest()[:8], "big"
        )
        assert draws.next_int() == expected

    def test_the_draws_do_not_use_the_random_module(self) -> None:
        source = Path(canon_module.__file__).read_text(encoding="utf-8")
        assert "import random" not in source
        assert "datetime.now" not in source and "date.today" not in source


class TestIdentifiers:
    def test_mail_and_upn_are_in_the_canonical_domain(self, canon: Canon) -> None:
        pattern = re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+[0-9]*@nullmeridian\.info$")
        for person in canon.employees:
            assert pattern.match(person.email), person.email
            assert person.user_principal_name == person.email

    def test_mail_and_account_names_are_unique(self, canon: Canon) -> None:
        emails = [person.email for person in canon.employees]
        sams = [person.sam_account_name.casefold() for person in canon.employees]
        assert len(set(emails)) == len(emails)
        assert len(set(sams)) == len(sams)
        assert all(len(sam) <= 20 for sam in sams)

    def test_collisions_are_numbered_in_employee_order(self, canon: Canon) -> None:
        holders: dict[str, list[str]] = {}
        for person in sorted(canon.employees, key=lambda item: item.employee_id):
            local = f"{person.given_name}.{person.family_name}".casefold()
            holders.setdefault(re.sub(r"[^a-z0-9.-]", "", local), []).append(person.email)
        collided = {key: value for key, value in holders.items() if len(value) > 1}
        assert collided, "the population should be large enough to collide at least once"
        for local, emails in collided.items():
            assert emails[0] == f"{local}@{DOMAIN_DNS}"
            for index, email in enumerate(emails[1:], start=2):
                assert email == f"{local}{index}@{DOMAIN_DNS}"

    def test_sids_are_unique_and_in_the_synthetic_domain(self, canon: Canon) -> None:
        sids = [person.sid for person in canon.employees] + [g.sid for g in canon.groups]
        assert len(set(sids)) == len(sids)
        assert all(sid.startswith(f"{DOMAIN_SID}-") for sid in sids)

    def test_rids_follow_creation_order(self, canon: Canon) -> None:
        created = sorted(
            [(person.hire_date, person.rid) for person in canon.employees]
            + [
                (group.created, group.rid)
                for group in canon.groups
                if group.category is not Category.BUILTIN
            ]
        )
        rids = [rid for _, rid in created]
        assert rids == sorted(rids)
        assert canon.group("Domain Users").rid == 513
        assert canon.group("Domain Admins").rid == 512

    def test_uuids_are_uuid5_under_the_documented_namespace(self, canon: Canon) -> None:
        assert uuid.uuid5(uuid.NAMESPACE_DNS, "nullmeridian.info") == NAMESPACE
        for person in canon.employees[:50]:
            assert person.uuid == str(uuid.uuid5(NAMESPACE, f"employee:{person.employee_id}"))
            assert person.object_guid == stable_uuid("object-guid", person.sid)
        for group in canon.groups:
            assert uuid.UUID(group.uuid).version == 5

    def test_the_domain_sid_is_derived_from_the_seed(self) -> None:
        import hashlib

        digest = hashlib.sha256(b"null-meridian-v1|domain-sid").digest()
        words = [int.from_bytes(digest[index : index + 4], "big") for index in (0, 4, 8)]
        assert "S-1-5-21-" + "-".join(map(str, words)) == DOMAIN_SID

    def test_no_identifier_comes_from_outside_the_canon(self, canon: Canon) -> None:
        """Nothing borrowed: not the repository's other fixtures, not a real-looking tenant."""
        document = render_canon_document()
        for forbidden in (
            "corp.example",
            "example.com",
            "contoso",
            "fabrikam",
            "S-1-5-21-1004336348",
            '.local"',
            "@gmail",
            "@outlook",
        ):
            assert forbidden not in document
        domains = set(re.findall(r"@([a-z0-9.-]+)", document))
        assert domains == {DOMAIN_DNS}


class TestTheOrganization:
    def test_department_sizes_differ_substantially(self) -> None:
        sizes = [department.size for department in DEPARTMENTS]
        assert len(DEPARTMENTS) == 14
        assert max(sizes) / min(sizes) > 10

    def test_every_level_is_represented(self, canon: Canon) -> None:
        levels = Counter(person.level for person in canon.active)
        assert levels[Level.EXECUTIVE] == 10
        assert levels[Level.VP] == 13
        assert levels[Level.DIRECTOR] >= 10
        assert levels[Level.MANAGER] >= 150
        assert levels[Level.IC] >= 1400

    def test_everybody_but_the_ceo_has_a_manager_who_works_here(self, canon: Canon) -> None:
        by_id = canon.employees_by_id
        for person in canon.active:
            if "role:ceo" in person.tags:
                assert person.manager_id is None
                continue
            assert person.manager_id in by_id, person.employee_id
            assert by_id[person.manager_id].is_active

    def test_tenure_spans_the_ten_years(self, canon: Canon) -> None:
        hires = sorted(person.hire_date for person in canon.active)
        assert hires[0] == FOUNDED
        assert hires[-1] >= dt.date(2026, 9, 1)
        assert sum(1 for day in hires if (SNAPSHOT - day).days >= 8 * 365) >= 50
        assert sum(1 for day in hires if (SNAPSHOT - day).days < 90) >= 50

    def test_transfers_and_an_acquisition_exist(self, canon: Canon) -> None:
        transferred = [person for person in canon.active if person.previous_department]
        assert len(transferred) >= 30
        for person in transferred:
            assert person.transfer_date is not None
            assert person.hire_date < person.transfer_date < SNAPSHOT
        assert len(canon.tagged("acquired:lumen-analytics")) >= 10

    def test_nobody_is_hired_before_their_department_exists(self, canon: Canon) -> None:
        founded = {department.slug: department.founded for department in DEPARTMENTS}
        for person in canon.employees:
            assert person.hire_date >= founded[person.department_on(person.hire_date)]


class TestGroupNamingHistory:
    def test_every_group_is_named_for_its_era(self, canon: Canon) -> None:
        for group in canon.groups:
            assert group.era is era_of(group.created)
            if group.created >= STANDARDS_ADOPTED:
                assert is_standard_name(group.name) or group.name in STANDARD_ERA_EXCEPTIONS, (
                    group.name
                )
            else:
                assert not group.name.startswith("sg-"), group.name

    def test_the_standard_predominates_after_2023_10_01(self, canon: Canon) -> None:
        modern = [group for group in canon.groups if group.created >= STANDARDS_ADOPTED]
        conforming = [group for group in modern if is_standard_name(group.name)]
        assert len(modern) >= 50
        assert len(conforming) / len(modern) >= 0.9
        names = {group.name for group in modern}
        for name in (
            "sg-okta-github-developer",
            "sg-okta-github-admin",
            "sg-okta-salesforce-user",
            "sg-okta-workday-user",
            "sg-share-finance-read-only",
            "sg-share-finance-write",
            "sg-share-engineering-read-only",
            "sg-share-engineering-write",
        ):
            assert name in names

    def test_older_groups_vary_in_convention(self, canon: Canon) -> None:
        older = [
            group.name
            for group in canon.groups
            if group.created < STANDARDS_ADOPTED and group.category is not Category.BUILTIN
        ]
        styles = {
            "hyphenated": any(re.fullmatch(r"[A-Za-z]+-[A-Z]+", name) for name in older),
            "underscored": any(re.fullmatch(r"[A-Za-z]+_[A-Za-z]+", name) for name in older),
            "GG_ prefix": any(name.startswith("GG_") for name in older),
            "FS- prefix": any(name.startswith("FS-") for name in older),
            "APP- prefix": any(name.startswith("APP-") for name in older),
            "Okta prefix": any(name.casefold().startswith("okta") for name in older),
            "spaces": any(" " in name for name in older),
            "Share_ prefix": any(name.startswith("Share_") for name in older),
        }
        assert all(styles.values()), styles
        for name in (
            "Finance-RW",
            "Finance_Read",
            "GG_Finance_RW",
            "FS-Finance-R",
            "Share_Engineering_Modify",
            "Engineering-FS",
            "APP-GitHub-Users",
            "Okta_GitHub_Admin",
            "GG_App_Workday",
            "Salesforce-Sales",
        ):
            assert canon.group(name).created < STANDARDS_ADOPTED

    def test_okta_appears_only_after_okta_was_adopted(self, canon: Canon) -> None:
        for group in canon.groups:
            if "okta" in group.name.casefold():
                assert group.created >= OKTA_ADOPTED, group.name
        transitional = [g for g in canon.groups if g.era is Era.TRANSITIONAL]
        assert any("okta" in g.name.casefold() for g in transitional)
        assert any("okta" not in g.name.casefold() for g in transitional), (
            "the transitional era had AD conventions beside Okta's"
        )

    def test_legacy_application_groups_survive_beside_their_replacements(
        self, canon: Canon
    ) -> None:
        on_snapshot = {group.name for group in canon.groups if group.exists_on(SNAPSHOT)}
        for legacy, modern in (
            ("APP-GitHub-Users", "sg-okta-github-developer"),
            ("GG_App_Workday", "sg-okta-workday-user"),
            ("Salesforce-Sales", "sg-okta-salesforce-user"),
            ("Finance-RW", "sg-share-finance-write"),
        ):
            assert {legacy, modern} <= on_snapshot


class TestTheHierarchy:
    def test_nested_membership_exists_and_has_no_cycle(self, canon: Canon) -> None:
        depths = canon.nesting_depths(SNAPSHOT)  # raises on a cycle
        assert sum(1 for depth in depths.values() if depth >= 3) >= 20
        assert max(depths.values()) <= 7, "the risk engine walks at most eight levels"

    def test_the_six_level_chain(self, canon: Canon) -> None:
        person = canon.one("golden:deep-chain")
        chain = [
            "ENG-Payments-Oncall",
            "ENG-Payments",
            "Engineering-Backend",
            "Engineering",
            "Engineering-FS",
            "Share_Engineering_Modify",
        ]
        edges = set(canon.memberships_on(SNAPSHOT))
        assert (chain[0], person.employee_id) in edges
        for member, parent in itertools.pairwise(chain):
            assert (parent, member) in edges, (parent, member)

    def test_duplicate_routes_to_one_group(self, canon: Canon) -> None:
        """An accountant in FIN-Close-Team reaches GG_Finance_RW twice."""
        edges = set(canon.memberships_on(SNAPSHOT))
        closers = [member for group, member in edges if group == "FIN-Close-Team"]
        assert closers
        assert ("GG_Finance_RW", "FIN-Close-Team") in edges
        assert ("GG_Finance_RW", "Finance-RW") in edges
        assert ("Finance-RW", "Finance-Team") in edges
        assert ("Finance-Team", "FIN-Accounting") in edges

    def test_ownerless_and_orphaned_owner_groups_exist(self, canon: Canon) -> None:
        legacy = [
            g
            for g in canon.groups
            if g.era in (Era.LEGACY, Era.TEAM) and g.category is not Category.BUILTIN
        ]
        assert any(group.owner_id is None for group in legacy)
        by_id = canon.employees_by_id
        assert any(group.owner_id and not by_id[group.owner_id].is_active for group in legacy)
        standard = [g for g in canon.groups if g.era is Era.STANDARD]
        assert all(group.owner_id and by_id[group.owner_id].is_active for group in standard)

    def test_the_empty_project_group_stays_empty(self, canon: Canon) -> None:
        for day in (dt.date(2026, 9, 3), dt.date(2026, 9, 17), SNAPSHOT):
            assert not [edge for edge in canon.memberships_on(day) if edge[0] == "Proj-Zephyr-RW"]
