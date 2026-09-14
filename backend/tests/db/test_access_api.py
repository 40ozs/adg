r"""The effective-access endpoints, against a real PostgreSQL and real transcripts.

The domain suites prove the arithmetic; this one proves the **joins** — that the keys a
collector writes are the keys the resolver matches on, that a BUILTIN trustee stays scoped
to its server all the way from ingestion to an access answer, and that a path nobody
scanned gets its DACL projected from the ancestor that was.

Every estate here is a canonical scenario replayed through the ingestion API, so what is
under test is the same data the rest of the suite describes rather than rows written
directly into tables.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from httpx import AsyncClient

from app.contracts.v1 import keys
from tests.support.ingest import replay, storable_document

pytestmark = pytest.mark.anyio

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN_SID}-1104"
FINANCE_TEAM = f"{DOMAIN_SID}-1201"
FINANCE_RW = f"{DOMAIN_SID}-1202"
ORPHAN = "S-1-5-21-999888777-666555444-333222111-1234"
ADMINISTRATORS = "S-1-5-32-544"
EVERYONE = "S-1-1-0"

FINANCE_UNC = "\\\\FS01\\Finance"
PAYROLL_UNC = "\\\\FS01\\Finance\\Payroll"
REPORTS_UNC = "\\\\FS01\\Finance\\Reports"
DEEP_UNC = "\\\\FS01\\Finance\\Reports\\2026\\Q3"
OPEN_UNC = "\\\\FS01\\Open"
OPEN_KEY = "\\\\fs01\\open"

MODIFY = 0x001301BF
FULL_CONTROL = 0x001F01FF
READ_EXECUTE = 0x001200A9


def encoded(path: str) -> str:
    return quote(path, safe="")


def access_url(principal: str, resource: str, **query: str) -> str:
    url = f"/api/v1/access/principals/{quote(principal, safe='')}/resources/{encoded(resource)}"
    if query:
        url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
    return url


async def seed(client: AsyncClient, name: str) -> None:
    await replay(client, storable_document(name))


@pytest.fixture
async def direct_grant(client: AsyncClient) -> None:
    await seed(client, "01-direct-user-grant")


@pytest.fixture
async def nested_grant(client: AsyncClient) -> None:
    await seed(client, "03-nested-group-grant")


@pytest.fixture
async def deny_estate(client: AsyncClient) -> None:
    await seed(client, "07-deny-candidate")


@pytest.fixture
async def broken_inheritance(client: AsyncClient) -> None:
    await seed(client, "09-broken-inheritance")


@pytest.fixture
async def share_limited(client: AsyncClient) -> None:
    await seed(client, "10-smb-more-restrictive")


class TestOnePrincipalOnOneResource:
    async def test_a_direct_grant_resolves_to_the_stored_mask(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        response = await client.get(access_url(ALICE, FINANCE_UNC))

        assert response.status_code == 200, response.text
        body = response.json()["effective"]
        assert body["access"] is True
        assert body["ntfs"]["rights"]["value"] == MODIFY
        assert body["rights"]["layer"] == "effective"

    async def test_the_mask_is_authoritative_and_the_label_is_derived(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        body = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["effective"]

        assert body["rights"]["mask"] == f"0x{body['rights']['value']:08X}"
        assert body["rights"]["primary"] == "Modify"

    async def test_a_nested_grant_carries_the_chain_that_explains_it(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["effective"]

        granted = body["ntfs"]["granted_by"][0]
        assert granted["trustee"]["key"] == FINANCE_RW
        assert granted["via_group"] is True

    async def test_the_token_names_every_group_and_every_assumption(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["token"]

        origins = {entry["principal"]["key"]: entry["origin"] for entry in body["entries"]}
        assert origins[ALICE] == "subject"
        assert origins[FINANCE_TEAM] == "group_membership"
        assert origins[FINANCE_RW] == "group_membership"
        assert origins[EVERYONE] == "well_known"
        assert origins["S-1-5-2"] == "logon_type"

    async def test_an_observed_group_is_labelled_rather_than_left_unresolved(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["token"]

        rw = next(item for item in body["entries"] if item["principal"]["key"] == FINANCE_RW)
        assert rw["principal"]["resolved"] is True
        assert rw["principal"]["display_name"] == "Finance-RW"

    async def test_a_deny_ahead_of_an_allow_produces_no_access(
        self, client: AsyncClient, deny_estate: None
    ) -> None:
        body = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["effective"]

        assert body["access"] is False
        assert body["rights"]["value"] == 0
        assert body["ntfs"]["denied_by"][0]["trustee"]["key"] == FINANCE_TEAM

    async def test_the_superseded_allow_is_still_reported(
        self, client: AsyncClient, deny_estate: None
    ) -> None:
        body = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["effective"]

        assert [item["ace_type"] for item in body["ntfs"]["superseded"]] == ["allow"]
        assert body["ntfs"]["superseded"][0]["contributed"] == "0x00000000"

    async def test_local_access_bypasses_the_share_and_says_so(
        self, client: AsyncClient, share_limited: None
    ) -> None:
        remote = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["effective"]
        local = (await client.get(access_url(ALICE, FINANCE_UNC, access_path="local"))).json()[
            "effective"
        ]

        assert remote["limiting_layer"] == "smb_share"
        assert remote["share"]["rights"]["value"] == READ_EXECUTE
        assert local["share"] is None
        assert local["rights"]["value"] == FULL_CONTROL

    async def test_a_builtin_trustee_is_matched_on_its_own_server(
        self, client: AsyncClient, broken_inheritance: None
    ) -> None:
        """Payroll is reachable only through FS01's local Administrators group."""
        response = await client.get(access_url(FINANCE_RW, PAYROLL_UNC))

        body = response.json()["effective"]
        assert body["access"] is True
        assert body["ntfs"]["granted_by"][0]["trustee"]["key"] == f"fs01|{ADMINISTRATORS}"

    async def test_a_protected_dacl_is_reported_as_a_condition(
        self, client: AsyncClient, broken_inheritance: None
    ) -> None:
        body = (await client.get(access_url(FINANCE_RW, PAYROLL_UNC))).json()["effective"]

        assert "protected_dacl" in body["conditions"]
        assert body["resource_key"] == PAYROLL_UNC.casefold()

    async def test_the_subject_being_a_group_is_declared(
        self, client: AsyncClient, broken_inheritance: None
    ) -> None:
        body = (await client.get(access_url(FINANCE_RW, PAYROLL_UNC))).json()["effective"]

        assert "subject_is_a_group" in body["conditions"]
        assert body["certainty"] == "at_most"

    async def test_every_finding_carries_an_operator_facing_message(
        self, client: AsyncClient, broken_inheritance: None
    ) -> None:
        body = (await client.get(access_url(FINANCE_RW, PAYROLL_UNC))).json()["effective"]

        assert body["findings"]
        for finding in body["findings"]:
            assert finding["message"]
            assert finding["condition"]

    async def test_an_unknown_principal_is_a_404(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        response = await client.get(access_url(f"{DOMAIN_SID}-4242", FINANCE_UNC))

        assert response.status_code == 404

    async def test_a_share_key_is_refused_as_a_directory(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        response = await client.get(access_url(ALICE, "fs01|finance"))

        assert response.status_code == 422
        assert "share key" in response.text

    async def test_an_ambiguous_builtin_sid_is_a_409(
        self, client: AsyncClient, broken_inheritance: None
    ) -> None:
        """One host here, so the bare SID resolves; the host-scoped key always works."""
        scoped = await client.get(access_url(f"fs01|{ADMINISTRATORS}", PAYROLL_UNC))

        assert scoped.status_code == 200


class TestPathsNobodyScanned:
    async def test_a_dacl_is_projected_from_the_nearest_observed_ancestor(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        """Windows would have given the child what the parent projects; so does ADG."""
        response = await client.get(access_url(ALICE, REPORTS_UNC))

        body = response.json()["effective"]
        assert body["acl_provenance"] == "derived"
        assert body["ntfs"]["rights"]["value"] == MODIFY
        assert "ntfs_acl_derived" in body["conditions"]

    async def test_the_projection_is_reported_as_an_upper_bound(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        body = (await client.get(access_url(ALICE, REPORTS_UNC))).json()["effective"]

        assert body["certainty"] == "at_most"

    async def test_an_unread_path_further_down_reports_the_levels_nobody_saw(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        body = (await client.get(access_url(ALICE, DEEP_UNC))).json()["effective"]

        assert "intermediate_path_unobserved" in body["conditions"]
        finding = next(
            item for item in body["findings"] if item["condition"] == "intermediate_path_unobserved"
        )
        assert finding["detail"]["unobserved_levels"] == 2

    async def test_the_resource_is_reported_as_unobserved(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        body = (await client.get(access_url(ALICE, REPORTS_UNC))).json()

        assert body["resource"]["observed"] is False
        assert body["resource"]["key"] == REPORTS_UNC.casefold()

    async def test_a_path_under_an_unscanned_share_has_no_acl_at_all(
        self, client: AsyncClient, direct_grant: None
    ) -> None:
        body = (await client.get(access_url(ALICE, "\\\\FS99\\Nothing\\Here"))).json()["effective"]

        assert body["access"] is False
        assert "ntfs_acl_not_observed" in body["conditions"]
        assert "share_acl_not_observed" in body["conditions"]


class TestAnUnreadShareIsNotAnOpenOne:
    @pytest.fixture
    async def ntfs_only(self, client: AsyncClient) -> None:
        """An NTFS scan that ran before any share scan did."""
        document = storable_document("01-direct-user-grant")
        for batch in document["batches"]:
            batch["observations"] = [
                item for item in batch["observations"] if item["kind"] != "smb_ace"
            ]
        document["completion"]["observation_count"] = sum(
            len(batch["observations"]) for batch in document["batches"]
        )
        await replay(client, document)

    async def test_the_answer_becomes_an_upper_bound(
        self, client: AsyncClient, ntfs_only: None
    ) -> None:
        body = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["effective"]

        assert "share_acl_not_observed" in body["conditions"]
        assert body["certainty"] == "at_most"
        assert body["limiting_layer"] == "unknown"
        assert body["rights"]["value"] == MODIFY

    async def test_no_share_evaluation_is_invented(
        self, client: AsyncClient, ntfs_only: None
    ) -> None:
        body = (await client.get(access_url(ALICE, FINANCE_UNC))).json()["effective"]

        assert body["share"] is None
        assert body["share_key"] == "fs01|finance"


class TestWhoCanReachAResource:
    def url(self, resource: str, **query: str) -> str:
        url = f"/api/v1/access/resources/{encoded(resource)}/principals"
        if query:
            url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
        return url

    async def test_it_lists_the_user_reached_through_the_nested_groups(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(self.url(FINANCE_UNC))

        assert response.status_code == 200, response.text
        keys_listed = {item["principal"]["key"] for item in response.json()["items"]}
        assert ALICE in keys_listed

    async def test_each_listed_principal_carries_the_trustee_that_put_it_there(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(self.url(FINANCE_UNC))).json()

        alice = next(item for item in body["items"] if item["principal"]["key"] == ALICE)
        assert [entry["principal"]["key"] for entry in alice["via"]] == [FINANCE_RW]
        assert alice["via"][0]["path"] == [ALICE, FINANCE_TEAM, FINANCE_RW]

    async def test_groups_are_excluded_by_default_and_included_on_request(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        default = (await client.get(self.url(FINANCE_UNC))).json()
        everything = (await client.get(self.url(FINANCE_UNC, members="all"))).json()

        assert FINANCE_RW not in {item["principal"]["key"] for item in default["items"]}
        assert FINANCE_RW in {item["principal"]["key"] for item in everything["items"]}

    async def test_a_denied_principal_is_listed_with_no_rights(
        self, client: AsyncClient, deny_estate: None
    ) -> None:
        body = (await client.get(self.url(FINANCE_UNC, members="all"))).json()

        alice = next(item for item in body["items"] if item["principal"]["key"] == ALICE)
        assert alice["access"] is False
        assert alice["rights"]["value"] == 0

    async def test_a_world_trustee_makes_the_listing_incomplete_and_says_which(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """An Everyone ACE puts principals here that no enumeration can produce."""
        body = (await client.get(self.url(FINANCE_UNC))).json()

        assert body["enumeration"]["complete"] is False
        unenumerable = {item["key"] for item in body["enumeration"]["unenumerable_trustees"]}
        assert EVERYONE in unenumerable

    async def test_the_unenumerable_trustees_are_labelled(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(self.url(FINANCE_UNC))).json()

        for item in body["enumeration"]["unenumerable_trustees"]:
            assert item["sid"]

    async def test_it_pages_by_offset(self, client: AsyncClient, nested_grant: None) -> None:
        first = (await client.get(self.url(FINANCE_UNC, members="all", limit="1"))).json()

        assert len(first["items"]) == 1
        assert first["page"]["has_more"] is True
        assert first["page"]["total"] >= 2

        second = (
            await client.get(
                self.url(FINANCE_UNC, members="all", limit="1", cursor=first["page"]["next_cursor"])
            )
        ).json()
        assert second["items"][0]["principal"]["key"] != first["items"][0]["principal"]["key"]

    async def test_a_foreign_cursor_is_refused(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(self.url(FINANCE_UNC, cursor="bm90LWEtY3Vyc29y"))

        assert response.status_code == 422

    async def test_an_unscanned_resource_answers_with_an_empty_listing(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(self.url("\\\\FS99\\Nothing"))

        assert response.status_code == 200
        assert response.json()["resource"]["observed"] is False


class TestWhatOnePrincipalCanReach:
    async def test_it_lists_the_shares_whose_acl_names_the_token(
        self, client: AsyncClient, share_limited: None
    ) -> None:
        response = await client.get(f"/api/v1/access/principals/{ALICE}/shares")

        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["share"]["key"] for item in body["items"]] == ["fs01|finance"]
        assert body["items"][0]["access"] is True

    async def test_a_share_listing_crosses_the_root_dacl(
        self, client: AsyncClient, share_limited: None
    ) -> None:
        body = (await client.get(f"/api/v1/access/principals/{ALICE}/shares")).json()

        item = body["items"][0]
        assert item["rights"]["value"] == READ_EXECUTE
        assert item["limiting_layer"] == "smb_share"

    async def test_it_lists_the_directories_the_token_is_named_on(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(f"/api/v1/access/principals/{ALICE}/resources")

        body = response.json()
        assert [item["resource"]["key"] for item in body["items"]] == [FINANCE_UNC.casefold()]

    async def test_a_candidate_that_grants_nothing_is_listed_with_its_verdict(
        self, client: AsyncClient, deny_estate: None
    ) -> None:
        """Named on the ACL is not access, and the listing shows both halves."""
        body = (await client.get(f"/api/v1/access/principals/{ALICE}/resources")).json()

        assert [item["access"] for item in body["items"]] == [False]
        assert body["items"][0]["resource"]["key"] == FINANCE_UNC.casefold()

    async def test_it_pages_by_key(self, client: AsyncClient, broken_inheritance: None) -> None:
        first = (
            await client.get(f"/api/v1/access/principals/{FINANCE_RW}/resources?limit=1")
        ).json()

        assert len(first["items"]) == 1
        assert first["page"]["has_more"] is True

        second = (
            await client.get(
                f"/api/v1/access/principals/{FINANCE_RW}/resources"
                f"?limit=1&cursor={first['page']['next_cursor']}"
            )
        ).json()
        assert second["items"][0]["resource"]["key"] != first["items"][0]["resource"]["key"]

    async def test_the_token_is_reported_with_the_listing(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(f"/api/v1/access/principals/{ALICE}/resources")).json()

        assert body["token"]["subject"]["key"] == ALICE
        assert body["token"]["membership_complete"] is True

    async def test_a_share_listing_refuses_a_local_access_path(
        self, client: AsyncClient, share_limited: None
    ) -> None:
        response = await client.get(f"/api/v1/access/principals/{ALICE}/shares?access_path=local")

        assert response.status_code == 422
        assert "remote access path" in response.text

    async def test_an_unknown_principal_is_a_404(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(f"/api/v1/access/principals/{DOMAIN_SID}-9999/resources")

        assert response.status_code == 404


class TestResourcesNobodyIsNamedOn:
    @pytest.fixture
    async def null_dacl(self, client: AsyncClient) -> None:
        """A directory with no DACL at all: it names nobody and grants everybody.

        Appended to a canonical transcript rather than hand-built, so the envelopes and the
        run accounting are the ones the ingestion contract actually accepts.
        """
        document = storable_document("01-direct-user-grant")
        path = OPEN_UNC
        batch = document["batches"][0]
        batch["observations"].append(
            {
                "schema_version": "1.3",
                "kind": "ntfs_resource",
                "run_id": document["start"]["run_id"],
                "observed_at": "2026-09-14T08:00:00Z",
                "source_key": keys.ntfs_resource_key(path),
                "path": path,
                "server_name": "FS01",
                "share_name": "Open",
                "dacl_present": False,
                "dacl_protected": False,
                "ace_count": 0,
                "inheritance_enabled": True,
                "is_acl_boundary": True,
                "boundary_reason": "null_dacl",
                "depth_from_share_root": 0,
            }
        )
        document["completion"]["observation_count"] = sum(
            len(item["observations"]) for item in document["batches"]
        )
        await replay(client, document)

    async def test_a_null_dacl_resource_is_listed_although_no_ace_names_anybody(
        self, client: AsyncClient, null_dacl: None
    ) -> None:
        """The most important row in the answer, and the one an index alone would drop.

        ``\\\\FS01\\Open`` has no DACL, so it appears in no ``principal_references`` row.
        Its grant is to everybody, which makes omitting it the worst possible omission.
        """
        body = (await client.get(f"/api/v1/access/principals/{ALICE}/resources")).json()

        listed = {item["resource"]["key"] for item in body["items"]}
        assert OPEN_KEY in listed
        assert FINANCE_UNC.casefold() in listed

    async def test_it_grants_full_control_and_says_why(
        self, client: AsyncClient, null_dacl: None
    ) -> None:
        body = (await client.get(access_url(ALICE, OPEN_UNC, access_path="local"))).json()[
            "effective"
        ]

        assert body["access"] is True
        assert body["rights"]["value"] == FULL_CONTROL
        assert "null_dacl" in body["conditions"]
