r"""Genesee Press's file estate and its three scans: shaped as documented, valid, repeatable.

The estate is derived by the same inheritance and hashing functions the server uses, so most
of what could go wrong is in the *shape*: too clean, too dirty, a golden scenario lost in an
edit, a direct-user grant concentrated in one obviously artificial folder. Each of those is a
test here. Behavior against a real database is in ``tests/db/test_genesee_press_demo.py``.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

import pytest

from app.contracts.v1 import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.demo.estate import DemoDirectory
from app.demo.genesee_press.canon import DOMAIN_SID, SNAPSHOT, Canon, build_canon
from app.demo.genesee_press.estate import (
    BROAD_WELL_KNOWN,
    SERVERS,
    AceIntent,
    FolderRecord,
    GeneseePressEstate,
    build_estate,
)
from app.demo.genesee_press.generations import (
    GENERATIONS,
    TRANSCRIPT_NAMESPACE,
    build_generations,
)
from app.demo.genesee_press.scenarios import SCENARIO_IDS, golden_scenarios
from app.domain.acl_hash import acl_hash
from app.domain.inheritance import project_inherited_acl
from tests.contracts.test_fixtures import OBSERVATION_SCHEMA, validator

G1, G2, G3 = (generation.day for generation in GENERATIONS)
WELL_KNOWN_SIDS = {"S-1-1-0", "S-1-5-11", "S-1-5-18", "S-1-3-0", "S-1-5-32-544"}


@pytest.fixture(scope="module")
def canon() -> Canon:
    return build_canon()


@pytest.fixture(scope="module")
def snapshot() -> GeneseePressEstate:
    return build_estate(SNAPSHOT)


def _folder(estate: GeneseePressEstate, path: str) -> FolderRecord:
    folded = path.casefold()
    return next(item for item in estate.folders if item.path.casefold() == folded)


def _directory(estate: GeneseePressEstate, path: str) -> DemoDirectory:
    folded = path.casefold()
    return next(item for item in estate.estate.directories if item.path.casefold() == folded)


class TestScale:
    def test_servers_shares_and_folders_are_in_range(self, snapshot: GeneseePressEstate) -> None:
        estate = snapshot.estate
        assert 25 <= len(estate.servers) <= 40
        assert 80 <= sum(1 for share in estate.shares if not share.is_special) <= 140
        assert len(estate.directories) >= 500
        explicit = [folder for folder in snapshot.folders if folder.assignment != "inherited"]
        assert 300 <= len(explicit) <= 600, "several hundred meaningful ACL objects"

    def test_synthetic_host_names_and_paths(self, snapshot: GeneseePressEstate) -> None:
        for server in snapshot.estate.servers:
            assert server.name.startswith("GP-")
            assert server.dns_host_name.endswith(".corp.geneseepress.example")
        for directory in snapshot.estate.directories:
            assert directory.path.startswith("\\\\GP-")

    def test_older_servers_carry_more_legacy_patterns(self, snapshot: GeneseePressEstate) -> None:
        by_server = {server.name: server.created for server in SERVERS}
        old = [f for f in snapshot.folders if by_server[f.server] < dt.date(2019, 10, 1)]
        new = [f for f in snapshot.folders if by_server[f.server] >= dt.date(2023, 10, 1)]

        def debt(folders: list[FolderRecord]) -> float:
            return sum(1 for f in folders if f.posture.value != "ordinary") / len(folders)

        assert debt(old) > 2 * debt(new)


class TestDistribution:
    def test_assignment_distribution_is_in_the_documented_bands(
        self, snapshot: GeneseePressEstate
    ) -> None:
        explicit = [folder for folder in snapshot.folders if folder.assignment != "inherited"]
        share = Counter(folder.assignment for folder in explicit)
        total = len(explicit)
        assert 0.65 <= share["group"] / total <= 0.75
        assert 0.15 <= share["direct-user"] / total <= 0.20
        assert 0.10 <= share["mixed"] / total <= 0.15

    def test_posture_is_moderate_debt(self, snapshot: GeneseePressEstate) -> None:
        share = Counter(folder.posture.value for folder in snapshot.folders)
        total = len(snapshot.folders)
        assert 0.70 <= share["ordinary"] / total <= 0.80
        assert 0.15 <= share["questionable"] / total <= 0.20
        assert 0.05 <= share["risky"] / total <= 0.10

    def test_direct_user_entries_are_spread_out_and_explained(
        self, snapshot: GeneseePressEstate
    ) -> None:
        direct = [
            (folder, ace)
            for folder in snapshot.folders
            for ace in folder.explicit
            if ace.trustee.startswith("GP-")
        ]
        assert len({folder.server for folder, _ in direct}) >= 15
        assert len({folder.area for folder, _ in direct}) >= 10
        per_share = Counter(folder.share for folder, _ in direct)
        assert max(per_share.values()) / len(direct) < 0.12, per_share.most_common(3)
        per_folder = Counter(folder.path for folder, _ in direct)
        assert max(per_folder.values()) <= 4
        assert {ace.intent for _, ace in direct} == set(AceIntent)

    def test_everyone_is_rare(self, snapshot: GeneseePressEstate) -> None:
        explicit_everyone = [
            folder
            for folder in snapshot.folders
            if any(ace.trustee in BROAD_WELL_KNOWN for ace in folder.explicit)
        ]
        assert len(explicit_everyone) <= 0.02 * len(snapshot.folders)
        everyone_shares = [
            share
            for share in snapshot.estate.shares
            if any(ace.trustee_sid == "S-1-1-0" for ace in share.aces)
        ]
        assert len(everyone_shares) <= 5

    def test_every_permission_level_is_used(self, snapshot: GeneseePressEstate) -> None:
        levels = {ace.level for folder in snapshot.folders for ace in folder.explicit}
        assert {"R", "RX", "L", "RW", "M", "F"} <= levels
        assert any(ace.deny for folder in snapshot.folders for ace in folder.explicit)

    def test_inherited_explicit_and_broken_inheritance_all_occur(
        self, snapshot: GeneseePressEstate
    ) -> None:
        directories = snapshot.estate.directories
        assert any(d.dacl_protected for d in directories)
        assert any(not d.is_acl_boundary for d in directories)
        assert any(any(ace.is_inherited for ace in d.aces) for d in directories)


class TestSelfConsistency:
    def test_every_acl_hash_is_the_hash_of_its_entries(self, snapshot: GeneseePressEstate) -> None:
        for directory in snapshot.estate.directories:
            assert directory.acl_hash == acl_hash(
                dacl_present=directory.dacl_present,
                dacl_protected=directory.dacl_protected,
                aces=[ace.facts() for ace in directory.aces],
            ), directory.path

    def test_a_child_that_inherits_carries_exactly_the_parent_projection(
        self, snapshot: GeneseePressEstate
    ) -> None:
        by_path = {d.path.casefold(): d for d in snapshot.estate.directories}
        checked = 0
        for directory in snapshot.estate.directories:
            if directory.is_acl_boundary or directory.depth_from_share_root == 0:
                continue
            parent = by_path[directory.path.rsplit("\\", 1)[0].casefold()]
            projected = project_inherited_acl(
                [ace.facts() for ace in parent.aces], for_container=True
            )
            assert [ace.facts() for ace in directory.aces] == list(projected), directory.path
            checked += 1
        assert checked >= 200

    def test_every_trustee_is_a_collected_principal_or_a_known_ghost(
        self, snapshot: GeneseePressEstate, canon: Canon
    ) -> None:
        """A trustee missing from the snapshot's principals must be one of the deliberate
        ghosts: an account or group deleted after ADG started scanning."""
        known = {principal.sid for principal in snapshot.estate.principals}
        ghosts: set[str] = set()
        for directory in snapshot.estate.directories:
            for ace in directory.aces:
                if ace.trustee_sid not in known and ace.trustee_sid not in WELL_KNOWN_SIDS:
                    ghosts.add(ace.trustee_sid)
        by_sid = {person.sid: person for person in canon.employees}
        groups = {group.sid: group for group in canon.groups}
        for sid in ghosts:
            if sid in groups:
                deleted = groups[sid].deleted
                assert deleted is not None and G1 < deleted <= SNAPSHOT
            else:
                state_g1 = by_sid[sid].account_state(G1)
                assert state_g1 in ("enabled", "disabled"), sid
                assert by_sid[sid].account_state(SNAPSHOT) == "deleted"
        assert ghosts, "the estate should carry at least one deleted-since-first-scan trustee"

    def test_every_share_and_directory_belongs_to_a_server(
        self, snapshot: GeneseePressEstate
    ) -> None:
        servers = {server.name for server in snapshot.estate.servers}
        assert {share.server_name for share in snapshot.estate.shares} <= servers
        shares = {(s.server_name, s.share_name.casefold()) for s in snapshot.estate.shares}
        for directory in snapshot.estate.directories:
            assert (directory.server_name, directory.share_name.casefold()) in shares

    def test_unresolved_principals_are_reported_with_a_last_known_name(
        self, snapshot: GeneseePressEstate
    ) -> None:
        unresolved = [
            p for p in snapshot.estate.principals if p.principal_kind.value == "unresolved"
        ]
        assert unresolved
        for principal in unresolved:
            assert principal.sid.startswith(DOMAIN_SID)
            assert principal.last_known_name and principal.last_known_name.startswith("GPCORP\\")


class TestItIsDeterministic:
    def test_two_builds_of_one_day_are_equal(self) -> None:
        first = build_estate.__wrapped__(SNAPSHOT)
        assert first == build_estate.__wrapped__(SNAPSHOT)

    def test_two_builds_of_the_transcripts_are_equal(self) -> None:
        first = build_generations()
        second = build_generations.__wrapped__()
        assert first == second

    def test_run_ids_are_uuid5_and_unique(self) -> None:
        import uuid

        ids = [t.run_id for _, ts in build_generations() for t in ts]
        assert len(set(ids)) == len(ids) == 300
        for _, transcripts in build_generations():
            for transcript in transcripts:
                assert transcript.run_id == str(
                    uuid.uuid5(TRANSCRIPT_NAMESPACE, f"run:{transcript.name}")
                )


class TestTheTranscripts:
    def test_every_envelope_is_accepted_by_the_backend_models(self) -> None:
        """All 300 runs and all 42,000 observations, through the models the API enforces."""
        for _, transcripts in build_generations():
            for transcript in transcripts:
                ScanRunStart.model_validate(transcript.start)
                for batch in transcript.batches:
                    ObservationBatch.model_validate(batch)
                ScanRunCompletion.model_validate(transcript.completion)

    def test_they_validate_against_the_published_schemas(self) -> None:
        """Every envelope, and a sample of every run's observations of every kind.

        jsonschema over all 42,000 observations takes minutes and checks nothing the sample
        does not: the observations of one kind are built by one function.
        """
        start = validator("scan-run-start.schema.json")
        batch_schema = validator("observation-batch.schema.json")
        completion = validator("scan-run-completion.schema.json")
        kinds = {kind: validator(name) for kind, name in OBSERVATION_SCHEMA.items()}
        seen: set[str] = set()
        for _, transcripts in build_generations():
            for transcript in transcripts:
                start.validate(transcript.start)
                completion.validate(transcript.completion)
                for batch in transcript.batches:
                    sampled: dict[str, dict[str, object]] = {}
                    for observation in batch["observations"]:
                        sampled.setdefault(observation["kind"], observation)
                    batch_schema.validate({**batch, "observations": list(sampled.values())})
                    for kind, observation in sampled.items():
                        kinds[kind].validate(observation)
                        seen.add(kind)
        assert seen == set(OBSERVATION_SCHEMA)

    def test_the_imperfect_runs_are_where_the_documentation_says(self) -> None:
        statuses = {
            (generation.key, transcript.name): transcript
            for generation, transcripts in build_generations()
            for transcript in transcripts
        }
        failed = {key for key, t in statuses.items() if t.status == "failed"}
        partial = {key for key, t in statuses.items() if t.status == "partial"}
        assert failed == {("g1", "g1-ntfs-gp-sec-fs01"), ("g3", "g3-ntfs-gp-fs-03")}
        assert partial == {("g3", "g3-ntfs-gp-mig-fs01")}
        for key in failed | partial:
            assert statuses[key].completion["reconciled_scopes"] == []
        assert statuses[("g3", "g3-ntfs-gp-fs-03")].observation_count == 0
        assert statuses[("g3", "g3-ntfs-gp-mig-fs01")].completion["errors"]
        for key, transcript in statuses.items():
            if key not in failed | partial:
                assert transcript.completion["reconciled_scopes"], key


class TestChangesBetweenGenerations:
    def test_what_appears_and_disappears(self, canon: Canon) -> None:
        g1, g2, g3 = (build_estate(day) for day in (G1, G2, G3))

        def paths(estate: GeneseePressEstate) -> set[str]:
            return {d.path for d in estate.estate.directories}

        def sids(estate: GeneseePressEstate) -> set[str]:
            return {p.sid for p in estate.estate.principals}

        scratch = r"\\GP-ENG-FS02\QA\Sprint-Scratch"
        assert scratch in paths(g1) and scratch not in paths(g2)
        new_close = r"\\GP-FIN-FS01\Finance\Accounting\Close\FY2026-Q3"
        assert new_close not in paths(g1) and new_close in paths(g2)
        staging = r"\\GP-MIG-FS01\Migrations\Wave-3-Staging"
        assert staging in paths(g2) and staging not in paths(g3)
        incident = r"\\GP-SEC-FS01\Incidents\IR-2026-014"
        assert incident not in paths(g2) and incident in paths(g3)

        vega = canon.group("Project-Vega").sid
        assert vega in sids(g1) and vega not in sids(g2)
        legal = canon.group("sec-share-legal-write").sid
        assert legal not in sids(g1) and legal in sids(g2)

        joined = sids(g3) - sids(g1)
        left = sids(g1) - sids(g3)
        assert len(joined) >= 10 and len(left) >= 5

    def test_permissions_change_between_generations(self) -> None:
        g1, g2 = build_estate(G1), build_estate(G2)
        campaigns = r"\\GP-MKT-FS01\Marketing\Campaigns"
        before = {ace.trustee for ace in _folder(g1, campaigns).explicit}
        after = {ace.trustee for ace in _folder(g2, campaigns).explicit}
        assert "All Staff" in before and "All Staff" not in after
        assert "MKT-Demand-Gen" in after
        forecasts = r"\\GP-SALES-FS01\Sales\Forecasts"
        assert any(a.trustee.startswith("GP-") for a in _folder(g1, forecasts).explicit)
        assert not any(a.trustee.startswith("GP-") for a in _folder(g2, forecasts).explicit)
        assert _directory(g1, campaigns).acl_hash != _directory(g2, campaigns).acl_hash

    def test_the_unreadable_folder_is_reported_not_observed(self) -> None:
        g3 = build_estate(G3)
        restricted = r"\\GP-MIG-FS01\Migrations\FS-Consolidation-2020\Restricted"
        assert restricted in g3.unreadable
        assert restricted not in {d.path for d in g3.estate.directories}
        assert restricted in {d.path for d in build_estate(G2).estate.directories}


class TestGoldenScenarios:
    def test_all_eight_are_described(self) -> None:
        assert [item["id"] for item in golden_scenarios()] == list(SCENARIO_IDS)

    def test_a_modern_healthy_share(self, snapshot: GeneseePressEstate, canon: Canon) -> None:
        folders = [f for f in snapshot.folders if f.share == r"\\GP-CORP-FS02\Design"]
        assert len(folders) >= 6
        trustees = {ace.trustee for f in folders for ace in f.explicit}
        assert trustees == {
            "SYSTEM",
            "ADMINISTRATORS",
            "sec-share-design-read-only",
            "sec-share-design-write",
        }
        assert not any(f.protected for f in folders)
        assert all(f.posture.value == "ordinary" for f in folders)
        edges = canon.memberships_on(SNAPSHOT)
        groups = {g.name for g in canon.groups}
        for name in ("sec-share-design-read-only", "sec-share-design-write"):
            members = [member for group, member in edges if group == name]
            assert members and not any(member in groups for member in members), name

    def test_b_legacy_finance(self, snapshot: GeneseePressEstate, canon: Canon) -> None:
        root = _folder(snapshot, r"\\GP-FIN-FS01\Finance")
        trustees = {ace.trustee for ace in root.explicit}
        assert {
            "Finance-RW",
            "GG_Finance_RW",
            "Finance_Read",
            "FS-Finance-R",
            "sec-share-finance-write",
            "sec-share-finance-read-only",
        } <= trustees
        close = _folder(snapshot, r"\\GP-FIN-FS01\Finance\Accounting\Close\FY2025")
        assert sum(1 for ace in close.explicit if ace.trustee.startswith("GP-")) == 2
        finance = [p for p in canon.active if p.department == "finance"]
        migrated = {m for g, m in canon.memberships_on(SNAPSHOT) if g == "sec-share-finance-write"}
        fraction = sum(1 for p in finance if p.employee_id in migrated) / len(finance)
        assert 0.2 < fraction < 0.7, "the migration is under way and incomplete"

    def test_c_engineering_sprawl(self, snapshot: GeneseePressEstate) -> None:
        folders = [f for f in snapshot.folders if f.share == r"\\GP-ENG-FS01\Engineering"]
        assert len(folders) >= 25
        trustees = {ace.trustee for f in folders for ace in f.explicit}
        assert {"Proj-Apollo", "PRJ_Nebula", "PRJ_Orion", "Proj-Zephyr-RW"} <= trustees
        historical = [
            ace
            for f in folders
            for ace in f.explicit
            if ace.intent is AceIntent.HISTORICAL_ENGINEER
        ]
        assert len(historical) >= 3

    def test_d_people_share(self, snapshot: GeneseePressEstate) -> None:
        folders = [f for f in snapshot.folders if f.share == r"\\GP-HR-FS01\People"]
        direct = [ace for f in folders for ace in f.explicit if ace.trustee.startswith("GP-")]
        assert len(direct) == 1
        for name in ("Employee-Relations", "Compensation"):
            folder = _folder(snapshot, rf"\\GP-HR-FS01\People\{name}")
            assert folder.protected
        exceptions = {f.path.rsplit("\\", 1)[-1] for f in folders if f.posture.value != "ordinary"}
        assert exceptions == {"Recruiting", "Payroll-Vendor-Transition"}

    def test_e_archive_debt(self, snapshot: GeneseePressEstate) -> None:
        folders = [f for f in snapshot.folders if f.share == r"\\GP-ARCH-FS01\Archive"]
        assert sum(1 for f in folders if f.protected) >= 4
        finance_2017 = _folder(snapshot, r"\\GP-ARCH-FS01\Archive\Finance-2017")
        assert Counter(a.trustee for a in finance_2017.explicit)["Finance-RW"] == 2
        assert any(a.trustee == "Project-Vega" for f in folders for a in f.explicit)
        assert sum(1 for f in folders for a in f.explicit if a.intent is AceIntent.FORMER_USER) >= 3

    def test_f_two_routes_for_one_person(self, snapshot: GeneseePressEstate, canon: Canon) -> None:
        mover = canon.one("golden:cross-department")
        assert mover.department == "sales" and mover.previous_department == "finance"
        revenue = _folder(snapshot, r"\\GP-FIN-FS01\Finance\Reports\Revenue")
        assert any(ace.trustee == mover.employee_id for ace in revenue.explicit)
        assert any(ace.trustee == "Revenue-Reporting" for ace in revenue.explicit)
        edges = set(canon.memberships_on(SNAPSHOT))
        assert ("Sales-Ops", mover.employee_id) in edges
        assert ("Revenue-Reporting", "Sales-Ops") in edges

    def test_g_the_chain_is_the_only_grant(self, snapshot: GeneseePressEstate) -> None:
        folder = _folder(snapshot, r"\\GP-ENG-FS01\Engineering\Runbooks\Payments-Legacy")
        assert folder.protected
        trustees = {a.trustee for a in folder.explicit} - {"SYSTEM", "ADMINISTRATORS"}
        assert trustees == {"Share_Engineering_Modify"}

    def test_h_the_safe_cleanup(self, snapshot: GeneseePressEstate) -> None:
        zephyr = _folder(snapshot, r"\\GP-ENG-FS01\Engineering\Projects\Zephyr")
        assert {a.trustee for a in zephyr.explicit} == {"Proj-Zephyr-RW"}
