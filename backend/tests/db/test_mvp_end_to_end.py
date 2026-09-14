r"""The MVP, end to end: collect, ingest, resolve, explain — on one estate, in one test.

Every other suite in this repository tests a layer. This one tests the *seams*, and the
seams are where an application that passes every unit test still does not work:

* the AD collector reports a group and the SMB collector names it on a share ACL — do the
  two agree on the key? (They agree because both derive it; the test is that they do.)
* a directory's ACL hash is computed by the generator and recomputed by the server from the
  rows it stored — do they match? If they do not, every boundary verdict in the product is
  computed against a descriptor nobody actually has.
* the resolver answers about a principal the graph resolved and a resource the NTFS run
  stored — and the answer has to name both correctly, cross two layers, and survive a scope
  that failed entirely.

The estate is :mod:`app.demo.estate`, replayed through the **ingestion HTTP endpoints**, not
written to the tables. That is the point: seeding is the same four status codes a Windows
collector gets, so this is a test of the endpoint as well as of everything downstream.

What makes the estate worth walking is what is wrong with it on purpose — a partial scan on
FS02, a failed scan on FS03, a deny that wins, a protected directory, an orphaned SID, a
disabled account that still holds rights, and a local group scoped to one machine. A
walkthrough over a clean estate proves the happy path and nothing else.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, ClassVar

import pytest
from httpx import AsyncClient

from app.auth.roles import Role
from app.demo.estate import (
    ALICE,
    BOB,
    CAROL,
    CONTRACTOR_BASE,
    DAN,
    ERIN,
    FINANCE_RW,
    ORPHANED_SID,
    DemoEstate,
    build_estate,
    sid,
)
from app.demo.seed import seed_transcripts
from app.demo.transcripts import build_transcripts

pytestmark = pytest.mark.anyio

FINANCE = r"\\fs01\finance"
REPORTS = r"\\fs01\finance\reports"
PAYROLL = r"\\fs01\finance\payroll"
CONFIDENTIAL = r"\\fs01\hr\confidential"
PUBLIC = r"\\fs01\public"
ARCHIVE = r"\\fs02\archive"
BETA = r"\\fs02\projects\beta"
ALPHA = r"\\fs02\projects\alpha"
LEGACY_SHARE = "fs03|legacy"
LEGACY_DIR = r"\\fs03\legacy"
RESTRICTED = r"\\fs02\projects\restricted"


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


@pytest.fixture(scope="module")
def estate() -> DemoEstate:
    return build_estate("small")


#: What the `seeded` fixture hands a test: the seeding report, and the estate it came from.
#: Named so that every test signature can say what it is receiving.
Seeded = dict[str, Any]


@pytest.fixture
async def seeded(client: AsyncClient, estate: DemoEstate) -> Seeded:
    """The demo estate, posted through the ingestion API exactly as a collector would."""
    report = await seed_transcripts(client, build_transcripts(estate))
    return {"report": report, "estate": estate}


class TestTheCollectorToStoreSeam:
    async def test_every_run_was_accepted_and_recorded_with_the_outcome_it_claimed(
        self, seeded: Seeded
    ) -> None:
        recorded = {run.name: run.recorded_status for run in seeded["report"].runs}

        assert recorded["ad"] == "succeeded"
        assert recorded["smb-fs01"] == "succeeded"
        assert recorded["ntfs-fs01"] == "succeeded"
        assert recorded["ntfs-fs02"] == "partial"
        assert recorded["ntfs-fs03"] == "failed"
        assert all(run.downgrade_reason is None for run in seeded["report"].runs), (
            "No run should have been downgraded: a downgrade means the server received "
            "fewer batches than the transcript claimed to send."
        )

    async def test_every_observation_was_stored(self, seeded: Seeded) -> None:
        expected = sum(
            transcript.observation_count for transcript in build_transcripts(seeded["estate"])
        )

        assert seeded["report"].observations_applied == expected

    async def test_seeding_twice_is_a_replay_rather_than_a_second_estate(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """Run ids are derived from the estate, so a second seeding is recognized and
        applies nothing. An operator who runs the seed script twice gets one estate."""
        again = await seed_transcripts(client, build_transcripts(seeded["estate"]))

        assert again.replayed is True
        assert again.observations_applied == 0

    async def test_the_object_counts_match_what_the_estate_describes(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        estate = seeded["estate"]
        counts = (await client.get("/api/v1/collection/operations")).json()["counts"]

        assert counts["principals"] == len(estate.principals)
        assert counts["membership_edges"] == len(estate.edges)
        assert counts["servers"] == len(estate.servers)
        assert counts["shares"] == len(estate.shares)
        assert counts["directories"] == len(estate.directories)
        assert counts["ntfs_aces"] == sum(item.ace_count for item in estate.directories)


class TestTheCollectionIsJudgeable:
    async def test_the_coverage_verdict_is_failed_because_a_server_is_unobserved(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/collection/status")).json()

        assert body["health"] == "failed"
        assert any("FS03" in concern for concern in body["concerns"])
        assert any("FS02" in concern for concern in body["concerns"])

    async def test_each_server_appears_as_its_own_scope(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """Three NTFS scopes with three different outcomes. Reduced to one row per collector
        kind — which is what this did before Phase 6D — two of the three would be invisible
        and the verdict would be whichever ran last."""
        body = (await client.get("/api/v1/collection/status")).json()
        ntfs = {
            item["target"]: item["status"]
            for item in body["collectors"]
            if item["collector"] == "ntfs"
        }

        assert ntfs == {"FS01": "succeeded", "FS02": "partial", "FS03": "failed"}

    async def test_the_operator_page_names_the_scope_that_has_never_succeeded(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/collection/operations")).json()
        never = [scope for scope in body["scopes"] if not scope["has_ever_succeeded"]]

        assert [scope["target"] for scope in never] == ["FS03"]
        assert any("never completed a run" in note for note in body["notes"])

    async def test_the_error_summary_groups_what_went_wrong(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/collection/operations")).json()
        codes = {group["code"]: group for group in body["errors"]}

        assert set(codes) == {"access_denied", "path_too_long", "host_unreachable"}
        assert codes["access_denied"]["sample_targets"] == [r"\\FS02\Projects\Restricted"]
        assert body["total_errors"] == 3

    async def test_the_partial_run_reports_what_it_did_not_deliver(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/collection/operations")).json()
        fs02 = next(scope for scope in body["scopes"] if scope["target"] == "FS02")

        assert fs02["completeness"] == "partial"
        assert "could not be read" in (fs02["latest"]["shortfall"] or "")


class TestTheStoreToResolverSeam:
    async def test_the_server_recomputes_the_same_acl_hash_the_collector_reported(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """If these disagree, every boundary verdict in the product is computed against a
        descriptor nobody actually holds — and nothing else in the API would show it."""
        body = (await client.get(f"/api/v1/shares/{quote('fs01|finance')}/root-acl")).json()

        assert body["acl_hash"]["agrees"] is True
        assert body["acl_hash"]["ace_count_agrees"] is True

    async def test_a_directory_that_inherits_is_not_reported_as_a_boundary(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (await client.get(f"/api/v1/resources/{quote(REPORTS)}")).json()

        assert body["is_acl_boundary"] is False
        assert body["boundary"]["agrees"] is True
        assert body["boundary"]["computed"] is False

    async def test_the_server_agrees_with_every_boundary_the_collector_claimed(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """Across the whole estate, not one directory. The projection is the piece most
        likely to be subtly wrong, and a single example would not show it."""
        disagreed: list[str] = []
        compared = 0
        for directory in seeded["estate"].directories:
            body = (
                await client.get(f"/api/v1/resources/{quote(directory.path.casefold())}")
            ).json()
            # `agrees` is None where the collector never claimed a comparison — a share
            # root's parent lies outside the share, so there is no verdict of the
            # collector's to agree or disagree with. None means "not compared", never
            # "disagreed", and reading it as falsy is the mistake this loop avoids.
            if body["boundary"]["agrees"] is False:
                disagreed.append(
                    f"{directory.path}: reported "
                    f"{body['boundary']['reported_reason']}, computed "
                    f"{body['boundary']['computed_reason']}"
                )
            elif body["boundary"]["agrees"] is True:
                compared += 1

        assert not disagreed, "Reported and computed boundaries disagree:\n  " + "\n  ".join(
            disagreed
        )
        assert compared >= 5, (
            "The estate must contain directories the server can actually check against "
            f"their parent; only {compared} were comparable, so this would prove little."
        )

    async def test_a_protected_directory_reports_why_it_is_a_boundary(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (await client.get(f"/api/v1/resources/{quote(CONFIDENTIAL)}")).json()

        assert body["is_acl_boundary"] is True
        assert body["boundary_reason"] == "protected_dacl"
        assert body["inheritance_enabled"] is False

    async def test_a_share_whose_file_system_was_never_read_says_so(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """FS03's share list was collected and its NTFS scan failed. The share exists; what
        is inside it is unknown, and the API has to say which."""
        share = await client.get(f"/api/v1/shares/{quote(LEGACY_SHARE)}")
        root = await client.get(f"/api/v1/shares/{quote(LEGACY_SHARE)}/root-acl")

        assert share.status_code == 200
        assert root.status_code == 404
        assert "no file-system run has read the directory" in root.json()["detail"]

    async def test_a_directory_the_scan_could_not_read_is_absent_and_explained(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        response = await client.get(f"/api/v1/resources/{quote(RESTRICTED)}")

        assert response.status_code == 404
        assert "not that the directory does not exist" in response.json()["detail"]


class TestTheAccessAnswers:
    #: The access matrix this estate is built to produce. One test rather than a
    #: parametrization: every case needs the whole estate seeded, and the seeding — not the
    #: assertion — is what costs the time. Mismatches are collected so a failure still names
    #: every case that went wrong rather than only the first.
    EXPECTED_ACCESS: ClassVar[list[tuple[int, str, bool, str]]] = [
        # Nested groups, two routes, and the NTFS ACL as the narrower layer.
        (ALICE, FINANCE, True, "Modify"),
        # One route only, and none of it reaches the protected directory.
        (BOB, FINANCE, True, "Modify"),
        (BOB, PAYROLL, False, "No access"),
        # Read-only through a different group.
        (CAROL, FINANCE, True, "Read & Execute"),
        # The deny wins over the grant the same principal holds by another route.
        (CONTRACTOR_BASE + 1, CONFIDENTIAL, False, "No access"),
        (CAROL, CONFIDENTIAL, True, "Modify"),
        # Everyone, full control: the grant every audit exists to find.
        (CONTRACTOR_BASE + 1, PUBLIC, True, "Full Control"),
        # Disabled, and still holding what the ACL grants its SID.
        (ERIN, PAYROLL, True, "Read & Execute"),
    ]

    async def test_the_engine_answers_what_the_estate_describes(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        wrong: list[str] = []
        for who, where, access, rights in self.EXPECTED_ACCESS:
            body = (
                await client.get(f"/api/v1/access/principals/{sid(who)}/resources/{quote(where)}")
            ).json()
            actual = (body["effective"]["access"], body["effective"]["rights"]["label"])
            if actual != (access, rights):
                wrong.append(f"{sid(who)} on {where}: expected {(access, rights)}, got {actual}")

        assert not wrong, "The engine disagrees with the estate:\n  " + "\n  ".join(wrong)

    async def test_the_share_can_be_the_narrower_layer(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """Archive grants Modify at the NTFS layer and Read at the share layer. A tool that
        reads only one of the two gets this wrong in one direction or the other."""
        body = (
            await client.get(f"/api/v1/access/principals/{sid(ALICE)}/resources/{quote(ARCHIVE)}")
        ).json()

        assert body["effective"]["rights"]["label"] == "Read & Execute"
        assert body["effective"]["limiting_layer"] == "smb_share"

    async def test_a_local_group_grants_on_its_own_host_and_nowhere_else(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """Dan is an administrator on FS01 through BUILTIN\\Administrators, which is a
        different group on FS02. Host-scoping the trustee is what makes that true."""
        on_fs01 = (
            await client.get(f"/api/v1/access/principals/{sid(DAN)}/resources/{quote(FINANCE)}")
        ).json()
        on_fs02 = (
            await client.get(f"/api/v1/access/principals/{sid(DAN)}/resources/{quote(ARCHIVE)}")
        ).json()

        assert on_fs01["effective"]["rights"]["label"] == "Full Control"
        assert on_fs02["effective"]["access"] is False

    async def test_an_unresolved_trustee_is_listed_rather_than_dropped(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """An ACE whose trustee no longer exists is a finding. Omitting it from the listing
        would under-report exactly the entry an audit is looking for."""
        body = (await client.get(f"/api/v1/access/resources/{quote(BETA)}/principals")).json()
        listed = {item["principal"]["sid"] for item in body["items"]}

        assert ORPHANED_SID in listed

    async def test_a_listing_says_when_it_could_not_be_complete(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """``Everyone`` cannot be enumerated, so the answer is bounded and says so rather
        than presenting a short list as the whole truth."""
        body = (await client.get(f"/api/v1/access/resources/{quote(PUBLIC)}/principals")).json()

        assert body["enumeration"]["complete"] is False
        assert any(
            item["sid"] == "S-1-1-0" for item in body["enumeration"]["unenumerable_trustees"]
        )


class TestTheExplanation:
    async def test_it_names_the_whole_chain(self, seeded: Seeded, client: AsyncClient) -> None:
        body = (
            await client.get(
                "/api/v1/access/explain",
                params={"principal": sid(ALICE), "resource": FINANCE},
            )
        ).json()
        node_ids = {node["id"] for node in body["graph"]["nodes"]}

        assert body["verdict"]["outcome"] == "granted"
        assert body["verdict"]["conclusive"] is True
        assert f"group:{sid(FINANCE_RW)}" in node_ids
        assert len(body["paths"]) >= 2

    async def test_a_removal_that_changes_nothing_is_reported_as_such(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """Alice reaches Finance-RW twice over, so cutting either membership leaves her
        access exactly where it was. A tool that offered one as a fix would have an
        administrator make a change that does nothing."""
        body = (
            await client.get(
                "/api/v1/access/explain",
                params={"principal": sid(ALICE), "resource": FINANCE},
            )
        ).json()
        memberships = [
            target for target in body["removal_targets"] if target["kind"] == "membership"
        ]

        assert memberships
        assert any(target["changes_nothing"] for target in memberships)

    async def test_a_single_route_removal_does_revoke(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """The contrast that makes the previous test mean something: Bob has one route, and
        removing it takes his access away."""
        body = (
            await client.get(
                "/api/v1/access/explain",
                params={"principal": sid(BOB), "resource": FINANCE},
            )
        ).json()

        assert any(target["revokes_all_access"] for target in body["removal_targets"])

    async def test_a_denial_is_explained_as_a_denial(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (
            await client.get(
                "/api/v1/access/explain",
                params={"principal": sid(CONTRACTOR_BASE + 1), "resource": CONFIDENTIAL},
            )
        ).json()

        assert body["verdict"]["outcome"] == "denied"
        assert body["verdict"]["denials"]

    async def test_a_disabled_account_is_flagged_without_its_rights_being_hidden(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (
            await client.get(
                "/api/v1/access/explain",
                params={"principal": sid(ERIN), "resource": PAYROLL},
            )
        ).json()
        conditions = {warning["condition"] for warning in body["warnings"]}

        assert body["verdict"]["outcome"] == "granted"
        assert "subject_disabled" in conditions

    async def test_an_answer_about_an_unobserved_resource_is_not_a_denial(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        """FS03's file system was never read. "No rights found" there is not a finding of no
        access, and the response has to distinguish the two."""
        body = (
            await client.get(
                f"/api/v1/access/principals/{sid(ALICE)}/resources/{quote(LEGACY_DIR)}"
            )
        ).json()

        assert body["resource"]["observed"] is False


class TestTheViewsARealReaderSees:
    """Every page's own API call, made as the account that would make it.

    The frontend renders these responses; that half is asserted in the frontend suite
    against captured bodies. What this covers is the half those cannot: that an **auditor**
    — not the administrator every other test in this file uses — can actually reach each
    one, and that the answers are populated rather than empty.
    """

    #: Every call a page of the product makes, with the parameters a real reader would
    #: produce. Walked in one test for the same reason as the access matrix: seeding the
    #: estate is what costs the time.
    PAGE_CALLS: ClassVar[list[tuple[str, dict[str, str] | None]]] = [
        ("/api/v1/collection/status", None),
        ("/api/v1/collection/operations", None),
        ("/api/v1/scan-runs", None),
        ("/api/v1/servers", None),
        ("/api/v1/servers/fs01/shares", None),
        (f"/api/v1/shares/{quote('fs01|finance')}", None),
        (f"/api/v1/shares/{quote('fs01|finance')}/acl", None),
        (f"/api/v1/resources/{quote(FINANCE)}", None),
        (f"/api/v1/resources/{quote(FINANCE)}/acl", None),
        (f"/api/v1/principals/{sid(ALICE)}", None),
        (f"/api/v1/principals/{sid(ALICE)}/groups", None),
        # Anchored on a group: a principal's membership paths are paths *into*
        # something, and the route requires the destination rather than enumerating
        # every group the principal is in.
        (f"/api/v1/principals/{sid(ALICE)}/membership-paths", {"group": sid(FINANCE_RW)}),
        (f"/api/v1/groups/{sid(FINANCE_RW)}/members", None),
        (f"/api/v1/groups/{sid(FINANCE_RW)}/effective-members", None),
        (f"/api/v1/groups/{sid(FINANCE_RW)}/resource-impact", None),
        (f"/api/v1/access/principals/{sid(ALICE)}/shares", None),
        (f"/api/v1/access/principals/{sid(ALICE)}/resources", None),
        (f"/api/v1/access/resources/{quote(FINANCE)}/principals", None),
        ("/api/v1/search", {"q": "finance"}),
        ("/api/v1/access/explain", {"principal": sid(ALICE), "resource": FINANCE}),
    ]

    async def test_an_auditor_reaches_every_page_of_the_product(
        self, seeded: Seeded, client_as: Any
    ) -> None:
        refused: list[str] = []
        async with client_as(Role.AUDITOR) as auditor:
            for path, params in self.PAGE_CALLS:
                response = await auditor.get(path, params=params)
                if response.status_code != 200:
                    refused.append(f"{path} -> {response.status_code} {response.text[:200]}")

        assert not refused, "An auditor could not reach:\n  " + "\n  ".join(refused)

    async def test_a_viewer_may_read_but_not_write(self, seeded: Seeded, client_as: Any) -> None:
        async with client_as(Role.VIEWER) as viewer:
            read = await viewer.get(f"/api/v1/access/resources/{quote(FINANCE)}/principals")
            write = await viewer.post("/api/v1/scan-runs", json={})

        assert read.status_code == 200
        assert write.status_code == 403

    async def test_an_administrator_may_write(self, seeded: Seeded, client_as: Any) -> None:
        """422, not 403: the empty body is refused by validation, which is only reachable
        once authorization has passed."""
        async with client_as(Role.ADMIN) as admin:
            response = await admin.post("/api/v1/scan-runs", json={})

        assert response.status_code == 422

    async def test_search_finds_the_estate_across_categories(
        self, seeded: Seeded, client_as: Any
    ) -> None:
        async with client_as(Role.AUDITOR) as auditor:
            by_name = (await auditor.get("/api/v1/search", params={"q": "finance"})).json()
            by_path = (await auditor.get("/api/v1/search", params={"q": FINANCE})).json()

        assert by_name["identities"]
        assert by_name["shares"]
        assert by_path["directories"]

    async def test_a_directory_page_can_be_reached_from_a_share_page(
        self, seeded: Seeded, client_as: Any
    ) -> None:
        """The navigation a person actually performs: share → its root directory → its ACL.
        Each step uses the key the previous response gave, so a key that does not round-trip
        fails here rather than as a dead link in the UI."""
        async with client_as(Role.AUDITOR) as auditor:
            share = (await auditor.get(f"/api/v1/shares/{quote('fs01|finance')}")).json()
            root_key = share["root_resource"]["key"]
            directory = await auditor.get(f"/api/v1/resources/{quote(root_key)}")
            acl = await auditor.get(f"/api/v1/resources/{quote(root_key)}/acl")

        assert directory.status_code == 200
        assert acl.status_code == 200
        assert acl.json()["entries"]

    async def test_an_identity_page_reaches_its_access_answer(
        self, seeded: Seeded, client_as: Any
    ) -> None:
        async with client_as(Role.AUDITOR) as auditor:
            groups = (await auditor.get(f"/api/v1/principals/{sid(ALICE)}/groups")).json()
            first = groups["items"][0]["principal"]["key"]
            impact = await auditor.get(f"/api/v1/groups/{quote(first)}/resource-impact")

        assert impact.status_code == 200


class TestTheAlphaCase:
    """One directory, because it is the one a reader is most likely to misread.

    Nobody set permissions on ``\\FS02\\Projects\\Alpha``. Windows materialized the parent's
    CREATOR OWNER grant as an entry naming whoever created it, and that entry cannot be
    derived from the parent — so ADG correctly reports the directory as differing from its
    parent. The finding is true about the DACL and misleading about intent, and the product
    has to report it rather than hide it.
    """

    async def test_it_is_reported_as_differing_from_its_parent(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (await client.get(f"/api/v1/resources/{quote(ALPHA)}")).json()

        assert body["boundary_reason"] == "acl_differs_from_parent"
        assert body["boundary"]["agrees"] is True

    async def test_the_entry_that_causes_it_is_visible_in_the_acl(
        self, seeded: Seeded, client: AsyncClient
    ) -> None:
        body = (await client.get(f"/api/v1/resources/{quote(ALPHA)}/acl")).json()
        trustees = {entry["trustee"]["sid"] for entry in body["entries"]}

        assert sid(BOB) in trustees
