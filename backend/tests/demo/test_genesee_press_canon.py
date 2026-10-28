r"""The Genesee Press canon: the company is exactly what the documentation says it is.

Every number the manifest quotes and every rule ``docs/demo/genesee-press.md`` states is
asserted here against the canon read from the vendored ``synthetic-enterprise`` projection,
so the documentation cannot drift from the shared world and ADG cannot drift from the
snapshot its lock pins.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest

from app.demo.genesee_press import canon as canon_module
from app.demo.genesee_press import vendor
from app.demo.genesee_press.canon import (
    ACQUISITION_TAG,
    COMPANY,
    DATASET_KEY,
    DEPARTMENTS,
    DOMAIN_DN,
    DOMAIN_DNS,
    DOMAIN_NETBIOS,
    DOMAIN_SID,
    FOUNDED,
    LEGACY_DRAW_SEED,
    MAIL_DOMAIN,
    OKTA_ADOPTED,
    SNAPSHOT,
    STANDARD_ERA_EXCEPTIONS,
    STANDARDS_ADOPTED,
    Canon,
    Category,
    Draws,
    Era,
    Level,
    build_canon,
    draw_key,
    era_of,
    is_standard_name,
    legacy_key,
)
from app.demo.genesee_press.manifest import build_manifest, canon_digests

REPOSITORY = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPOSITORY / "docs" / "demo" / "genesee-press.manifest.json"

#: The digests of the canon read from the pinned snapshot. A change here is a change to the
#: company: it comes from re-vendoring synthetic-enterprise (a new lock), and the manifest is
#: regenerated with it; never an edit to this constant alone.
PINNED_DIGESTS = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["digests"]

#: Active employees per department on the snapshot: the ADG v1 sizes, unchanged by the move
#: to the shared world.
DEPARTMENT_SIZES = {
    "engineering": 520,
    "sales": 300,
    "customer-success": 190,
    "data": 110,
    "marketing": 110,
    "operations": 105,
    "product": 95,
    "finance": 90,
    "it": 85,
    "people": 60,
    "design": 55,
    "security": 40,
    "legal": 28,
    "executive": 12,
}


@pytest.fixture(scope="module")
def canon() -> Canon:
    return build_canon()


class TestTheFixedFacts:
    def test_the_company_constants(self) -> None:
        assert COMPANY == "Genesee Press"
        assert DATASET_KEY == "genesee-press-v1"
        assert MAIL_DOMAIN == "geneseepress.example"
        assert DOMAIN_DNS == "corp.geneseepress.example"
        assert DOMAIN_DN == "DC=corp,DC=geneseepress,DC=example"
        assert DOMAIN_NETBIOS == "GPCORP"
        assert dt.date(2026, 10, 1) == SNAPSHOT
        assert dt.date(2016, 10, 1) == FOUNDED
        assert dt.date(2021, 10, 1) == OKTA_ADOPTED
        assert dt.date(2023, 10, 1) == STANDARDS_ADOPTED
        assert (SNAPSHOT.year - FOUNDED.year) == 10

    def test_exactly_1800_active_employees(self, canon: Canon) -> None:
        assert len(canon.active) == 1800
        counts = Counter(person.department for person in canon.active)
        assert counts == DEPARTMENT_SIZES
        assert {department.slug for department in DEPARTMENTS} == set(DEPARTMENT_SIZES)

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
            "docs/demo/genesee-press.manifest.json is stale. Regenerate it with "
            "python -m app.demo.genesee_press manifest "
            "--out ../docs/demo/genesee-press.manifest.json"
        )

    def test_draws_follow_the_documented_algorithm(self) -> None:
        """So that a second implementation can reproduce the canon from the docs alone."""
        import hashlib

        draws = Draws("example", 7)
        expected = int.from_bytes(
            hashlib.sha256(f"{LEGACY_DRAW_SEED}|example|7|0".encode()).digest()[:8], "big"
        )
        assert draws.next_int() == expected

    def test_the_draws_do_not_use_the_random_module(self) -> None:
        source = Path(canon_module.__file__).read_text(encoding="utf-8")
        assert "import random" not in source
        assert "datetime.now" not in source and "date.today" not in source


class TestIdentifiers:
    def test_mail_and_upn_are_in_the_canonical_domain(self, canon: Canon) -> None:
        pattern = re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+[0-9]*@geneseepress\.example$")
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
            assert emails[0] == f"{local}@{MAIL_DOMAIN}"
            for index, email in enumerate(emails[1:], start=2):
                assert email == f"{local}{index}@{MAIL_DOMAIN}"

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

    def test_canonical_ids_and_directory_ids_come_from_the_projection(self, canon: Canon) -> None:
        guid = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        for person in canon.employees:
            assert person.canonical_id.startswith("per_")
            assert person.account_canonical_id.startswith("acc_")
            assert guid.match(person.object_guid)
            assert re.fullmatch(r"GP-\d{5}", person.employee_id)
        for group in canon.groups:
            assert group.canonical_id.startswith("grp_")
            assert guid.match(group.object_guid)
        guids = [p.object_guid for p in canon.employees] + [g.object_guid for g in canon.groups]
        assert len(set(guids)) == len(guids)

    def test_the_domain_sid_is_the_directorys(self) -> None:
        """SIDs are opaque directory identifiers: the shared world keeps ADG's, unchanged."""
        (directory,) = vendor.records("directory.jsonl")
        assert directory["domain_sid"] == DOMAIN_SID == "S-1-5-21-3949361974-1583827165-633756269"

    def test_no_identifier_comes_from_outside_the_canon(self, canon: Canon) -> None:
        """Nothing borrowed: not the repository's other fixtures, not a real-looking tenant,
        and no former organization value outside the vendored legacy_* fields."""
        document = json.dumps(
            [list(map(str, (p.email, p.sam_account_name, p.sid, p.title))) for p in canon.employees]
            + [list(map(str, (g.name, g.description, g.sid))) for g in canon.groups]
        )
        for forbidden in (
            "corp.example",
            "example.com",
            "contoso",
            "fabrikam",
            "S-1-5-21-1004336348",
            "S-1-5-21-244598355",
            '.local"',
            "@gmail",
            "@outlook",
            "nullmeridian",
            "Null Meridian",
            "NM-",
            "Lumen",
        ):
            assert forbidden not in document
        domains = set(re.findall(r"@([a-z0-9.-]+)", document))
        assert domains == {MAIL_DOMAIN}


class TestTheOrganization:
    def test_department_sizes_differ_substantially(self) -> None:
        sizes = list(DEPARTMENT_SIZES.values())
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
        assert ACQUISITION_TAG == "acquired:brindlewood-analytics"
        assert len(canon.tagged(ACQUISITION_TAG)) == 15

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
                assert not group.name.startswith("sec-"), group.name
            assert not re.match(r"(?i)^(sg|dg)[-_ ]", group.name), group.name
            if re.match(r"^(sec|di)-", group.name):
                assert group.name == group.name.lower(), group.name

    def test_the_standard_predominates_after_2023_10_01(self, canon: Canon) -> None:
        modern = [group for group in canon.groups if group.created >= STANDARDS_ADOPTED]
        conforming = [group for group in modern if is_standard_name(group.name)]
        assert len(modern) >= 50
        assert len(conforming) / len(modern) >= 0.9
        names = {group.name for group in modern}
        for name in (
            "sec-okta-github-developer",
            "sec-okta-github-admin",
            "sec-okta-salesforce-user",
            "sec-okta-workday-user",
            "sec-share-finance-read-only",
            "sec-share-finance-write",
            "sec-share-engineering-read-only",
            "sec-share-engineering-write",
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
            ("APP-GitHub-Users", "sec-okta-github-developer"),
            ("GG_App_Workday", "sec-okta-workday-user"),
            ("Salesforce-Sales", "sec-okta-salesforce-user"),
            ("Finance-RW", "sec-share-finance-write"),
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


class TestTheSharedWorld:
    """The canon is the pinned ``synthetic-enterprise`` projection, read locally and verified."""

    def test_the_lock_pins_one_snapshot_and_projection(self) -> None:
        lock = vendor.lock()
        assert lock["repository"] == "40ozs/synthetic-enterprise"
        assert re.fullmatch(r"[0-9a-f]{40}", lock["commit"])
        assert lock["projection"] == "adg@0.3.0"
        assert lock["contract_version"] == "0.2.0"
        assert lock["dataset"] == "synthetic-enterprise-world@2.1.0"  # SYNTH-CROSSAPP-01
        vendor.verify()

    def test_an_edited_projection_file_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        copy = tmp_path / "synthetic-enterprise"
        shutil.copytree(vendor.VENDOR_DIR, copy)
        monkeypatch.setattr(vendor, "VENDOR_DIR", copy)
        monkeypatch.setattr(vendor, "LOCK_PATH", copy / "SYNTHETIC_ENTERPRISE.lock")
        for cached in (vendor.lock, vendor.verify, vendor.records):
            cached.cache_clear()
        try:
            vendor.verify()  # the untouched copy verifies
            vendor.verify.cache_clear()
            target = copy / vendor.lock()["snapshot"] / "projections" / "adg" / "hosts.jsonl"
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with pytest.raises(vendor.VendorError, match=r"hosts\.jsonl"):
                vendor.verify()
        finally:
            for cached in (vendor.lock, vendor.verify, vendor.records):
                cached.cache_clear()

    def test_reading_the_canon_needs_no_network(self) -> None:
        for module in (canon_module, vendor):
            source = Path(str(module.__file__)).read_text(encoding="utf-8")
            for forbidden in ("urllib", "http", "socket", "requests", "github.com/"):
                assert forbidden not in source.replace("https://", ""), (module, forbidden)

    def test_the_estate_is_adgs_directory_catalogue(self, canon: Canon) -> None:
        groups = vendor.records("groups.jsonl")
        # 521 -> 509 at dataset 2.1.0: OGWA's 12 case-only duplicates of ADG groups are those
        # groups (SYNTH-CROSSAPP-01, D-XA-01); ADG's catalogue below is unchanged.
        assert len(groups) == 509
        assert len(canon.groups) == sum(1 for g in groups if g["legacy_name"]) == 206
        assert len(canon.employees) == len(vendor.records("employees.jsonl")) == 2440
        assert len(canon.nesting) == 192
        assert len(canon.memberships_on(SNAPSHOT)) == 4273

    def test_legacy_spellings_are_draw_keys_only(self, canon: Canon) -> None:
        assert legacy_key("GP-00874") == "NM-00874"
        assert legacy_key("sec-share-finance-write") == "sg-share-finance-write"
        assert legacy_key("GP-ENG-FS01") == "NM-ENG-FS01"
        assert legacy_key("Finance-RW") == "Finance-RW"
        assert draw_key(r"\\GP-ACQ-FS01\Brindlewood-Analytics") == r"\\NM-ACQ-FS01\Lumen-Analytics"
        assert draw_key("Brindlewood-Models/Archive") == "Lumen-Models/Archive"
        manifest = MANIFEST_PATH.read_text(encoding="utf-8")
        for legacy in ("NM-", "sg-share-", "sg-okta-", "nullmeridian", "Lumen", "NULLMERIDIAN"):
            assert legacy not in manifest, legacy
