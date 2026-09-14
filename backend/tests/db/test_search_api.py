r"""Global search, against a real PostgreSQL.

What matters here is not that search finds things — it is that an empty result is always
attributable. Three of these tests are about that: a category the caller may not search is
named, a truncated category is named, and the interpretation ADG chose is stated. An audit
tool whose search box quietly returns nothing is a tool that says "this group has no access"
when it means "I did not look".
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.auth.roles import Role
from app.contracts.v1 import keys
from app.domain import PrincipalKind, Sid
from app.repositories.search import MAX_HITS_PER_CATEGORY

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
FINANCE_MANAGERS = f"{DOMAIN_SID}-1202"
ALICE = f"{DOMAIN_SID}-1104"
BUILTIN_ADMINISTRATORS = "S-1-5-32-544"

FINANCE_UNC = "\\\\FS01\\Finance"
FINANCE_REPORTS_UNC = "\\\\FS01\\Finance\\Reports"


def observation(kind: str, index: int, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": kind,
        "run_id": "placeholder",
        "observed_at": f"2026-09-14T08:{index:02d}:00Z",
        **fields,
    }


def principal(index: int, sid: str, kind: str, **fields: Any) -> dict[str, Any]:
    # Derived rather than spelled out: the contract rejects a key a collector built its own
    # way, because two spellings of one object become two rows.
    source_key = keys.principal_key(Sid(sid), PrincipalKind(kind), fields.get("host_key"))
    return observation(
        "principal", index, source_key=source_key, sid=sid, principal_kind=kind, **fields
    )


def transcript(observations: list[dict[str, Any]], collector: str = "smb") -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    for item in observations:
        item["run_id"] = run_id
    return {
        "start": {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": {
                "collector": collector,
                "collector_host": "COLLECTOR01",
                "method": "test",
                "collector_version": "0.1.0",
            },
            "started_at": "2026-09-14T08:00:00Z",
            "scopes": [{"kind": "server", "key": "fs01"}],
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


async def replay(client: AsyncClient, document: dict[str, Any]) -> None:
    run_id = document["start"]["run_id"]
    started = await client.post("/api/v1/scan-runs", json=document["start"])
    assert started.status_code in (200, 201), started.text
    for batch in document["batches"]:
        response = await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)
        assert response.status_code == 202, response.text
    completed = await client.post(
        f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
    )
    assert completed.status_code == 200, completed.text


@pytest.fixture
async def estate(client: AsyncClient) -> None:
    """One server, two shares, one directory, and four principals worth finding."""
    await replay(
        client,
        transcript(
            [
                observation(
                    "server",
                    1,
                    source_key=keys.server_key("FS01"),
                    name="FS01",
                    dns_host_name="fs01.corp.example.com",
                ),
                observation(
                    "smb_share",
                    2,
                    source_key=keys.share_key("FS01", "Finance"),
                    server_name="FS01",
                    share_name="Finance",
                    local_path="D:\\Shares\\Finance",
                ),
                observation(
                    "smb_share",
                    3,
                    source_key=keys.share_key("FS01", "Payroll"),
                    server_name="FS01",
                    share_name="Payroll",
                    local_path="D:\\Shares\\Payroll",
                ),
                principal(
                    4,
                    FINANCE_MANAGERS,
                    "domain_group",
                    display_name="Finance Managers",
                    sam_account_name="finance-managers",
                    group_scope="global",
                    group_type="security",
                ),
                principal(
                    5,
                    ALICE,
                    "user",
                    display_name="Alice Chen",
                    sam_account_name="achen",
                    user_principal_name="achen@corp.example.com",
                    enabled=True,
                ),
                principal(
                    6,
                    BUILTIN_ADMINISTRATORS,
                    "local_group",
                    display_name="Administrators",
                    host_key="fs01",
                ),
            ]
        ),
    )
    await replay(
        client,
        transcript(
            [
                observation(
                    "ntfs_resource",
                    1,
                    source_key=keys.ntfs_resource_key(FINANCE_UNC),
                    path=FINANCE_UNC,
                    server_name="FS01",
                    share_name="Finance",
                    dacl_present=True,
                    dacl_protected=False,
                    inheritance_enabled=True,
                    is_acl_boundary=True,
                    ace_count=0,
                    boundary_reason="scan_root",
                    depth_from_share_root=0,
                ),
                observation(
                    "ntfs_resource",
                    2,
                    source_key=keys.ntfs_resource_key(FINANCE_REPORTS_UNC),
                    path=FINANCE_REPORTS_UNC,
                    server_name="FS01",
                    share_name="Finance",
                    dacl_present=True,
                    dacl_protected=False,
                    inheritance_enabled=True,
                    is_acl_boundary=False,
                    ace_count=0,
                    depth_from_share_root=1,
                ),
            ],
            collector="ntfs",
        ),
    )


class TestFindingAnIdentity:
    async def test_a_sid_finds_the_principal_carrying_it(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/search", params={"q": ALICE})).json()

        assert body["interpreted_as"] == "sid"
        assert [hit["display_name"] for hit in body["identities"]] == ["Alice Chen"]

    async def test_a_name_prefix_finds_a_group(self, client: AsyncClient, estate: None) -> None:
        body = (await client.get("/api/v1/search", params={"q": "Finance Man"})).json()

        assert [hit["sid"] for hit in body["identities"]] == [FINANCE_MANAGERS]

    async def test_matching_is_case_insensitive(self, client: AsyncClient, estate: None) -> None:
        body = (await client.get("/api/v1/search", params={"q": "finance man"})).json()

        assert len(body["identities"]) == 1

    async def test_a_sam_account_name_is_searched_too(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/search", params={"q": "achen"})).json()

        assert [hit["sid"] for hit in body["identities"]] == [ALICE]

    async def test_a_local_group_reports_the_host_that_scopes_it(
        self, client: AsyncClient, estate: None
    ) -> None:
        """A BUILTIN SID means nothing without its host, so the hit has to carry one."""
        body = (await client.get("/api/v1/search", params={"q": BUILTIN_ADMINISTRATORS})).json()

        assert body["identities"][0]["host_key"] == "fs01"
        # The storage key, not the observation's source key: this is what the identity
        # endpoints address, and it is host-scoped for exactly this reason.
        assert body["identities"][0]["principal_key"] == f"fs01|{BUILTIN_ADMINISTRATORS}"


class TestFindingAResource:
    async def test_a_server_name_finds_the_server(self, client: AsyncClient, estate: None) -> None:
        body = (await client.get("/api/v1/search", params={"q": "FS0"})).json()

        assert [hit["name"] for hit in body["servers"]] == ["FS01"]

    async def test_a_share_name_finds_the_share_with_its_unc_path(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/search", params={"q": "Payroll"})).json()

        assert [hit["unc_path"] for hit in body["shares"]] == ["\\\\fs01\\Payroll"]

    async def test_a_bare_server_path_lists_that_servers_shares(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/search", params={"q": "\\\\FS01"})).json()

        assert body["interpreted_as"] == "server"
        assert sorted(hit["name"] for hit in body["shares"]) == ["Finance", "Payroll"]

    async def test_a_share_path_finds_the_share_and_what_is_under_it(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/search", params={"q": FINANCE_UNC})).json()

        assert body["interpreted_as"] == "share"
        assert [hit["name"] for hit in body["shares"]] == ["Finance"]
        assert sorted(hit["path"] for hit in body["directories"]) == [
            FINANCE_UNC,
            FINANCE_REPORTS_UNC,
        ]

    async def test_a_directory_path_finds_the_directory(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/search", params={"q": FINANCE_REPORTS_UNC})).json()

        assert body["interpreted_as"] == "unc_path"
        assert [hit["path"] for hit in body["directories"]] == [FINANCE_REPORTS_UNC]

    async def test_a_share_path_narrows_to_the_server_that_was_named(
        self, client: AsyncClient, estate: None
    ) -> None:
        """``\\\\FS02\\Finance`` must not answer with FS01's Finance share."""
        body = (await client.get("/api/v1/search", params={"q": "\\\\FS02\\Finance"})).json()

        assert body["shares"] == []


class TestWhatTheAnswerAdmits:
    async def test_it_states_how_the_term_was_read(self, client: AsyncClient, estate: None) -> None:
        body = (await client.get("/api/v1/search", params={"q": "Finance"})).json()

        assert "prefix" in body["interpretation"]

    async def test_a_truncated_category_says_so(self, client: AsyncClient) -> None:
        """A search box that silently drops matches is how a share stops existing."""
        await replay(
            client,
            transcript(
                [
                    principal(
                        index,
                        f"{DOMAIN_SID}-{2000 + index}",
                        "user",
                        display_name=f"Test User {index:03d}",
                    )
                    for index in range(MAX_HITS_PER_CATEGORY + 5)
                ],
                collector="active_directory",
            ),
        )

        body = (await client.get("/api/v1/search", params={"q": "Test User"})).json()

        assert len(body["identities"]) == MAX_HITS_PER_CATEGORY
        assert "identities" in body["truncated"]
        assert body["limit_per_category"] == MAX_HITS_PER_CATEGORY

    async def test_an_untruncated_category_does_not_claim_to_be(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/search", params={"q": "Payroll"})).json()

        assert body["truncated"] == []

    async def test_finding_nothing_is_not_an_error(self, client: AsyncClient, estate: None) -> None:
        response = await client.get("/api/v1/search", params={"q": "Nothing Here"})

        assert response.status_code == 200
        body = response.json()
        assert body["identities"] == []
        assert body["not_searched"] == []


class TestWhatTheCallerMayLookAt:
    async def test_a_viewer_searches_everything(self, client_as: Any, estate: None) -> None:
        async with client_as(Role.VIEWER) as viewer:
            body = (await viewer.get("/api/v1/search", params={"q": "Finance"})).json()

        assert body["not_searched"] == []
        assert body["identities"] and body["shares"]

    async def test_an_account_with_no_role_cannot_search_at_all(
        self, client_as: Any, estate: None
    ) -> None:
        async with client_as() as nobody:
            response = await nobody.get("/api/v1/search", params={"q": "Finance"})

        assert response.status_code == 403


class TestRefusals:
    async def test_a_one_character_term_is_refused_with_a_reason(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/search", params={"q": "a"})

        assert response.status_code == 422
        assert "at least 2 characters" in response.json()["detail"]

    async def test_a_wildcard_is_searched_for_literally(
        self, client: AsyncClient, estate: None
    ) -> None:
        """``%`` must find principals whose name starts with a percent sign — of which there
        are none — rather than every principal in the estate."""
        body = (await client.get("/api/v1/search", params={"q": "%%"})).json()

        assert body["identities"] == []
        assert body["shares"] == []

    async def test_an_underscore_is_searched_for_literally(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/search", params={"q": "A_ice"})).json()

        assert body["identities"] == []
