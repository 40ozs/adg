r"""The resource query endpoints, against a real PostgreSQL.

What these pin down is the shape of the answers rather than the SQL: that every spelling of
a share lands on one share, that a folder path is refused instead of truncated, that an ACL
comes back in DACL order and says out loud that it is raw, and that an absent parent is
reported as ``null`` rather than dropped.

Data is built here rather than replayed from a fixture wherever a test needs several servers
or a long ACL — the canonical scenarios each describe one share, which is the wrong shape for
pagination.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from app.contracts.v1 import keys
from app.domain import Sid
from tests.support.ingest import replay, storable_document

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN_SID}-1104"
FINANCE_RW = f"{DOMAIN_SID}-1202"
AUTHENTICATED = "S-1-5-11"
ADMINISTRATORS = "S-1-5-32-544"

# Backslashes cannot appear inside an f-string expression on the pinned Python
# version, so the UNC spellings under test live here.
FS01_UNC = "\\\\FS01"
FINANCE_UNC = "\\\\FS01\\Finance"
FINANCE_FOLDER_UNC = "\\\\FS01\\Finance\\Reports"


def observation(kind: str, run_id: str, index: int, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": kind,
        "run_id": run_id,
        "observed_at": f"2026-09-14T08:{index:02d}:00Z",
        **fields,
    }


def server(run_id: str, index: int, name: str, **fields: Any) -> dict[str, Any]:
    return observation(
        "server", run_id, index, source_key=keys.server_key(name), name=name, **fields
    )


def share(run_id: str, index: int, host: str, name: str, **fields: Any) -> dict[str, Any]:
    return observation(
        "smb_share",
        run_id,
        index,
        source_key=keys.share_key(host, name),
        server_name=host,
        share_name=name,
        **fields,
    )


def ace(
    run_id: str,
    index: int,
    host: str,
    share_name: str,
    trustee: str,
    *,
    ace_type: str = "allow",
    permission: str | None = "read",
    access_mask: int | None = None,
    order_index: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "server_name": host,
        "share_name": share_name,
        "trustee_sid": trustee,
        "ace_type": ace_type,
    }
    if permission is not None:
        payload["permission"] = permission
    if access_mask is not None:
        payload["access_mask"] = access_mask
    if order_index is not None:
        payload["order_index"] = order_index
    return observation(
        "smb_ace",
        run_id,
        index,
        source_key=keys.smb_ace_key(
            host, share_name, Sid(trustee), ace_type, access_mask, permission
        ),
        **payload,
    )


def principal(run_id: str, index: int, sid: str, kind: str, **fields: Any) -> dict[str, Any]:
    return observation(
        "principal",
        run_id,
        index,
        source_key=f"principal|{sid}",
        sid=sid,
        principal_kind=kind,
        **fields,
    )


def transcript(observations: list[dict[str, Any]], scopes: list[dict[str, str]]) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    for item in observations:
        item["run_id"] = run_id
    return {
        "start": {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": {
                "collector": "smb",
                "collector_host": "COLLECTOR01",
                "method": "Win32_LogicalShareSecuritySetting",
                "collector_version": "0.1.0",
            },
            "started_at": "2026-09-14T08:00:00Z",
            "scopes": scopes,
            "incremental": False,
        },
        "batches": [
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "batch_id": str(uuid.uuid4()),
                "sequence": 1,
                "is_final": True,
                "observations": observations,
            }
        ],
        "completion": {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "succeeded",
            "completed_at": "2026-09-14T09:00:00Z",
            "batch_count": 1,
            "observation_count": len(observations),
            "error_count": 0,
            "errors": [],
            "reconciled_scopes": [],
        },
    }


@pytest.fixture
async def estate(client: AsyncClient) -> None:
    """Two servers, four shares, and an ACL with several entries."""
    run = "placeholder"
    observations = [
        server(run, 1, "FS01", dns_host_name="fs01.corp.example.com", is_domain_member=True),
        server(run, 2, "FS02", operating_system="Windows Server 2022"),
        share(run, 3, "FS01", "Finance", local_path="D:\\Shares\\Finance"),
        share(run, 4, "FS01", "Payroll", local_path="D:\\Shares\\Payroll", is_special=False),
        share(run, 5, "FS01", "C$", share_type="disk", is_special=True),
        share(run, 6, "FS02", "Archive", local_path="E:\\Archive"),
        ace(run, 7, "FS01", "Finance", AUTHENTICATED, permission="read", order_index=2),
        ace(run, 8, "FS01", "Finance", FINANCE_RW, permission="change", order_index=1),
        ace(
            run,
            9,
            "FS01",
            "Finance",
            ALICE,
            ace_type="deny",
            permission=None,
            access_mask=0x00120089,
            order_index=0,
        ),
        ace(run, 10, "FS01", "C$", ADMINISTRATORS, permission="full", order_index=0),
        ace(run, 11, "FS02", "Archive", ADMINISTRATORS, permission="full", order_index=0),
        principal(run, 12, FINANCE_RW, "domain_group", display_name="Finance-RW"),
    ]
    await replay(
        client,
        transcript(
            observations,
            [{"kind": "server", "key": "fs01"}, {"kind": "server", "key": "fs02"}],
        ),
    )


class TestListingServers:
    async def test_it_lists_every_server_with_its_share_count(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/servers")).json()

        assert [item["key"] for item in body["items"]] == ["fs01", "fs02"]
        assert {item["key"]: item["share_count"] for item in body["items"]} == {
            "fs01": 3,
            "fs02": 1,
        }
        assert body["page"]["total"] == 2

    async def test_it_keeps_the_observed_spelling(self, client: AsyncClient, estate: None) -> None:
        body = (await client.get("/api/v1/servers")).json()
        assert body["items"][0]["name"] == "FS01"

    async def test_it_pages_by_key(self, client: AsyncClient, estate: None) -> None:
        first = (await client.get("/api/v1/servers", params={"limit": 1})).json()

        assert [item["key"] for item in first["items"]] == ["fs01"]
        assert first["page"]["has_more"] is True

        second = (
            await client.get(
                "/api/v1/servers", params={"limit": 1, "cursor": first["page"]["next_cursor"]}
            )
        ).json()

        assert [item["key"] for item in second["items"]] == ["fs02"]
        assert second["page"]["has_more"] is False
        assert second["page"]["next_cursor"] is None

    async def test_a_foreign_cursor_is_refused(self, client: AsyncClient, estate: None) -> None:
        # Silently restarting at page one would make a client's second page look like a
        # complete result set.
        response = await client.get("/api/v1/servers", params={"cursor": "not-a-cursor"})
        assert response.status_code == 422

    async def test_an_empty_estate_is_an_empty_list_not_an_error(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/servers")).json()
        assert body["items"] == []
        assert body["page"]["total"] == 0


class TestOneServer:
    @pytest.mark.parametrize("identifier", ["FS01", "fs01", "Fs01"])
    async def test_any_casing_finds_it(
        self, client: AsyncClient, estate: None, identifier: str
    ) -> None:
        body = (await client.get(f"/api/v1/servers/{identifier}")).json()
        assert body["key"] == "fs01"
        assert body["dns_host_name"] == "fs01.corp.example.com"

    async def test_it_reports_provenance(self, client: AsyncClient, estate: None) -> None:
        body = (await client.get("/api/v1/servers/FS01")).json()

        assert body["provenance"]["source_key"] == "server|fs01"
        assert body["provenance"]["first_observed_run_id"]

    async def test_an_unknown_server_is_a_404_that_says_what_absence_means(
        self, client: AsyncClient, estate: None
    ) -> None:
        response = await client.get("/api/v1/servers/FS99")

        assert response.status_code == 404
        assert "no run has described it" in response.text

    async def test_a_unc_path_is_not_a_server_name(self, client: AsyncClient, estate: None) -> None:
        response = await client.get("/api/v1/servers/" + quote(FS01_UNC, safe=""))
        assert response.status_code == 422


class TestSharesOfAServer:
    async def test_it_lists_that_server_only(self, client: AsyncClient, estate: None) -> None:
        body = (await client.get("/api/v1/servers/FS01/shares")).json()

        assert [item["key"] for item in body["items"]] == [
            "fs01|c$",
            "fs01|finance",
            "fs01|payroll",
        ]
        assert body["server"]["key"] == "fs01"

    async def test_derived_values_are_computed_not_stored(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/servers/FS01/shares")).json()
        shares = {item["key"]: item for item in body["items"]}

        assert shares["fs01|finance"]["unc_path"] == "\\\\fs01\\Finance"
        assert shares["fs01|finance"]["is_hidden"] is False
        assert shares["fs01|c$"]["is_hidden"] is True
        assert shares["fs01|c$"]["is_administrative"] is True
        assert shares["fs01|payroll"]["is_administrative"] is False

    async def test_is_special_keeps_null_apart_from_false(
        self, client: AsyncClient, estate: None
    ) -> None:
        # "The source did not say" and "the source said no" are different facts.
        shares = {
            item["key"]: item
            for item in (await client.get("/api/v1/servers/FS01/shares")).json()["items"]
        }
        assert shares["fs01|finance"]["is_special"] is None
        assert shares["fs01|payroll"]["is_special"] is False
        assert shares["fs01|c$"]["is_special"] is True

    async def test_it_pages_by_key(self, client: AsyncClient, estate: None) -> None:
        first = (await client.get("/api/v1/servers/FS01/shares", params={"limit": 2})).json()

        assert [item["key"] for item in first["items"]] == ["fs01|c$", "fs01|finance"]
        assert first["page"]["total"] == 3

        second = (
            await client.get(
                "/api/v1/servers/FS01/shares",
                params={"limit": 2, "cursor": first["page"]["next_cursor"]},
            )
        ).json()

        assert [item["key"] for item in second["items"]] == ["fs01|payroll"]

    async def test_an_unknown_server_with_no_shares_is_a_404(
        self, client: AsyncClient, estate: None
    ) -> None:
        assert (await client.get("/api/v1/servers/FS99/shares")).status_code == 404


class TestOneShare:
    @pytest.mark.parametrize(
        "identifier",
        ["fs01|finance", "FS01|Finance", "\\\\FS01\\Finance", "\\\\fs01\\finance\\"],
    )
    async def test_every_spelling_reaches_one_share(
        self, client: AsyncClient, estate: None, identifier: str
    ) -> None:
        response = await client.get("/api/v1/shares/" + quote(identifier, safe=""))

        assert response.status_code == 200
        assert response.json()["key"] == "fs01|finance"

    async def test_the_forward_slash_spelling_cannot_travel_in_a_url_path(
        self, client: AsyncClient, estate: None
    ) -> None:
        # A percent-encoded '/' is decoded before routing, so '//fs01/finance' can never be
        # one path segment. The domain parser accepts the spelling and the hermetic tests
        # cover it; the URL simply cannot carry it. Documented rather than worked around —
        # a rewrite would have to guess where the identifier ended.
        response = await client.get("/api/v1/shares/" + quote("//fs01/finance", safe=""))

        assert response.status_code == 404

    async def test_it_carries_its_server_and_ace_count(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/shares/fs01|finance")).json()

        assert body["server"]["key"] == "fs01"
        assert body["ace_count"] == 3
        assert body["local_path"] == "D:\\Shares\\Finance"

    async def test_a_folder_path_is_refused_rather_than_truncated(
        self, client: AsyncClient, estate: None
    ) -> None:
        # Answering with the share's ACL would answer a question nobody asked, about an
        # object with different permissions.
        response = await client.get("/api/v1/shares/" + quote(FINANCE_FOLDER_UNC, safe=""))

        assert response.status_code == 422
        assert "directory inside a share" in response.text

    async def test_an_unknown_share_points_at_its_acl(
        self, client: AsyncClient, estate: None
    ) -> None:
        response = await client.get("/api/v1/shares/fs01|nosuchshare")

        assert response.status_code == 404
        assert "/acl" in response.text


class TestRawShareAcl:
    async def test_it_returns_entries_in_dacl_order(
        self, client: AsyncClient, estate: None
    ) -> None:
        # Canonical order is what makes a Deny evaluable; alphabetical order would destroy it.
        body = (await client.get("/api/v1/shares/fs01|finance/acl")).json()

        assert [entry["order_index"] for entry in body["entries"]] == [0, 1, 2]
        assert body["entries"][0]["ace_type"] == "deny"

    async def test_it_says_it_is_raw(self, client: AsyncClient, estate: None) -> None:
        body = (await client.get("/api/v1/shares/fs01|finance/acl")).json()
        assert body["kind"] == "raw_smb_acl"

    async def test_a_level_and_a_mask_are_reported_as_they_were_read(
        self, client: AsyncClient, estate: None
    ) -> None:
        entries = (await client.get("/api/v1/shares/fs01|finance/acl")).json()["entries"]
        by_right = {entry["right"]: entry for entry in entries}

        assert by_right["change"]["permission"] == "change"
        assert by_right["change"]["access_mask"] is None
        assert by_right["0x00120089"]["access_mask"] == 0x00120089
        assert by_right["0x00120089"]["permission"] is None

    async def test_a_described_trustee_is_resolved_and_an_undescribed_one_is_not(
        self, client: AsyncClient, estate: None
    ) -> None:
        entries = (await client.get("/api/v1/shares/fs01|finance/acl")).json()["entries"]
        by_sid = {entry["trustee"]["sid"]: entry["trustee"] for entry in entries}

        assert by_sid[FINANCE_RW]["resolved"] is True
        assert by_sid[FINANCE_RW]["display_name"] == "Finance-RW"
        assert by_sid[AUTHENTICATED]["resolved"] is False
        assert by_sid[AUTHENTICATED]["display_name"] is None

    async def test_a_builtin_trustee_is_keyed_to_its_server(
        self, client: AsyncClient, estate: None
    ) -> None:
        entries = (await client.get("/api/v1/shares/fs01|c$/acl")).json()["entries"]
        assert entries[0]["trustee"]["key"] == "fs01|S-1-5-32-544"
        assert entries[0]["trustee"]["host_key"] == "fs01"

    async def test_it_pages_by_offset(self, client: AsyncClient, estate: None) -> None:
        first = (await client.get("/api/v1/shares/fs01|finance/acl", params={"limit": 2})).json()

        assert len(first["entries"]) == 2
        assert first["page"]["total"] == 3

        second = (
            await client.get(
                "/api/v1/shares/fs01|finance/acl",
                params={"limit": 2, "cursor": first["page"]["next_cursor"]},
            )
        ).json()

        assert [entry["order_index"] for entry in second["entries"]] == [2]
        assert second["page"]["has_more"] is False

    async def test_a_keyset_cursor_is_refused_here(self, client: AsyncClient, estate: None) -> None:
        servers = (await client.get("/api/v1/servers", params={"limit": 1})).json()

        response = await client.get(
            "/api/v1/shares/fs01|finance/acl",
            params={"cursor": servers["page"]["next_cursor"]},
        )

        assert response.status_code == 422

    async def test_a_share_with_no_acl_and_no_row_is_a_404(
        self, client: AsyncClient, estate: None
    ) -> None:
        response = await client.get("/api/v1/shares/fs01|nosuchshare/acl")

        assert response.status_code == 404
        assert "empty ACL would be reported as an empty list" in response.text


class TestSharesReferencingATrustee:
    async def test_a_domain_sid_finds_every_share_that_names_it(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get(f"/api/v1/principals/{FINANCE_RW}/shares")).json()

        assert [item["share_key"] for item in body["items"]] == ["fs01|finance"]
        assert body["scope"] == "sid"
        assert body["trustee"]["resolved"] is True

    async def test_it_returns_only_the_entries_that_name_the_trustee(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get(f"/api/v1/principals/{FINANCE_RW}/shares")).json()

        aces = body["items"][0]["aces"]
        assert len(aces) == 1
        assert aces[0]["trustee"]["sid"] == FINANCE_RW

    async def test_a_builtin_sid_answers_across_every_server(
        self, client: AsyncClient, estate: None
    ) -> None:
        # "Which shares grant S-1-5-32-544" has one correct answer, and it spans servers.
        body = (await client.get(f"/api/v1/principals/{ADMINISTRATORS}/shares")).json()

        assert [item["share_key"] for item in body["items"]] == ["fs01|c$", "fs02|archive"]
        assert body["scope"] == "sid"

    async def test_a_host_scoped_key_answers_about_one_server(
        self, client: AsyncClient, estate: None
    ) -> None:
        scoped = quote(f"fs02|{ADMINISTRATORS}", safe="")
        body = (await client.get(f"/api/v1/principals/{scoped}/shares")).json()

        assert [item["share_key"] for item in body["items"]] == ["fs02|archive"]
        assert body["scope"] == "principal"

    async def test_an_unreferenced_sid_is_an_empty_list(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get(f"/api/v1/principals/{DOMAIN_SID}-9999/shares")).json()

        assert body["items"] == []
        assert body["page"]["total"] == 0
        assert body["trustee"]["resolved"] is False

    async def test_a_name_is_not_an_identifier(self, client: AsyncClient, estate: None) -> None:
        # Names are metadata (ADR-0001). Looking one up as identity would be the first step
        # toward attributing access to the wrong account.
        response = await client.get("/api/v1/principals/Finance-RW/shares")

        assert response.status_code == 422
        assert "names are " in response.text.lower()

    async def test_it_pages(self, client: AsyncClient, estate: None) -> None:
        first = (
            await client.get(f"/api/v1/principals/{ADMINISTRATORS}/shares", params={"limit": 1})
        ).json()

        assert [item["share_key"] for item in first["items"]] == ["fs01|c$"]
        assert first["page"]["has_more"] is True

        second = (
            await client.get(
                f"/api/v1/principals/{ADMINISTRATORS}/shares",
                params={"limit": 1, "cursor": first["page"]["next_cursor"]},
            )
        ).json()

        assert [item["share_key"] for item in second["items"]] == ["fs02|archive"]


class TestAgainstTheCanonicalScenarios:
    async def test_the_share_in_scenario_ten_is_reachable_by_its_unc_path(
        self, client: AsyncClient
    ) -> None:
        await replay(client, storable_document("10-smb-more-restrictive"))

        response = await client.get("/api/v1/shares/" + quote(FINANCE_UNC, safe=""))

        assert response.status_code == 200
        body = response.json()
        assert body["unc_path"] == "\\\\fs01\\Finance"
        assert body["ace_count"] == 1

    async def test_the_acl_matches_what_the_scenario_declares(self, client: AsyncClient) -> None:
        await replay(client, storable_document("10-smb-more-restrictive"))

        acl = (await client.get("/api/v1/shares/fs01|finance/acl")).json()

        # The scenario's whole point is that the share layer is the limiting one.
        assert acl["entries"][0]["permission"] == "read"
        assert acl["entries"][0]["ace_type"] == "allow"
