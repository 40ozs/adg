"""The graph endpoints, end to end: ingest through HTTP, then query through HTTP.

The canonical Phase 0 scenarios carry the semantics (nesting, multiple paths, cycles,
primary-group edges, orphaned SIDs); generated graphs carry the scale. Both go in through
the real ingestion API and come back out through the real query API, so the storage keys,
the SQL, the traversal, and the response shape are all under test at once — a suite that
called the service directly could pass while the endpoint returned nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from tests.fixtures import load_scenario
from tests.support.ingest import ingest_scenario, replay

ALICE = "S-1-5-21-1004336348-1177238915-682003330-1104"
FINANCE_TEAM = "S-1-5-21-1004336348-1177238915-682003330-1201"
FINANCE_RW = "S-1-5-21-1004336348-1177238915-682003330-1202"
AUDITORS = "S-1-5-21-1004336348-1177238915-682003330-1204"
DOMAIN_USERS = "S-1-5-21-1004336348-1177238915-682003330-513"
RING_A = "S-1-5-21-1004336348-1177238915-682003330-1210"
RING_B = "S-1-5-21-1004336348-1177238915-682003330-1211"
DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"


def synthetic_run(edges: list[tuple[str, str]], principals: dict[str, str]) -> dict[str, Any]:
    """Build a one-batch AD transcript from ``(group_rid, member_rid)`` pairs."""
    run_id = str(uuid.uuid4())
    observed = "2026-09-14T08:00:00Z"
    observations: list[dict[str, Any]] = []
    for rid, kind in principals.items():
        observations.append(
            {
                "schema_version": "1.0",
                "kind": "principal",
                "run_id": run_id,
                "observed_at": observed,
                "source_key": f"principal|{DOMAIN_SID}-{rid}",
                "sid": f"{DOMAIN_SID}-{rid}",
                "principal_kind": kind,
                "display_name": f"principal-{rid}",
            }
        )
    for group, member in edges:
        group_sid = f"{DOMAIN_SID}-{group}"
        member_sid = f"{DOMAIN_SID}-{member}"
        observations.append(
            {
                "schema_version": "1.0",
                "kind": "membership_edge",
                "run_id": run_id,
                "observed_at": observed,
                "source_key": f"edge|{group_sid}->{member_sid}|directory_group_member",
                "group_sid": group_sid,
                "member_sid": member_sid,
                "edge_kind": "directory_group_member",
            }
        )
    return {
        "start": {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": {
                "collector": "active_directory",
                "collector_host": "COLLECTOR01",
                "method": "Get-ADGroupMember",
            },
            "started_at": observed,
            "scopes": [{"kind": "domain", "key": DOMAIN_SID}],
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
            "completed_at": observed,
            "batch_count": 1,
            "observation_count": len(observations),
            "error_count": 0,
        },
    }


class TestPrincipalLookup:
    async def test_a_principal_is_returned_with_its_provenance(self, client: AsyncClient) -> None:
        result = await ingest_scenario(client, "03-nested-group-grant")

        response = await client.get(f"/api/v1/principals/{ALICE}")
        body = response.json()

        assert response.status_code == 200
        assert body["key"] == ALICE
        assert body["kind"] == "user"
        assert body["resolved"] is True
        assert body["is_group"] is False
        assert body["display_name"] == "Alice Smith"
        assert body["domain_sid"] == DOMAIN_SID
        assert body["last_observed_run_id"] == result["run_id"]
        assert body["direct_group_count"] == 1
        assert body["direct_member_count"] == 0

    async def test_a_lower_case_sid_resolves_to_the_canonical_one(
        self, client: AsyncClient
    ) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        response = await client.get(f"/api/v1/principals/{ALICE.lower()}")

        assert response.status_code == 200
        assert response.json()["key"] == ALICE

    async def test_every_observed_name_is_listed(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        body = (await client.get(f"/api/v1/principals/{FINANCE_TEAM}")).json()

        kinds = {alias["alias_kind"]: alias["value"] for alias in body["aliases"]}
        assert kinds["display_name"] == "Finance-Team"

    async def test_an_unknown_sid_is_a_404(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        response = await client.get(f"/api/v1/principals/{DOMAIN_SID}-9999")

        assert response.status_code == 404
        assert "not have been collected yet" in response.json()["detail"]

    async def test_a_builtin_sid_on_two_hosts_is_a_409_listing_both(
        self, client: AsyncClient
    ) -> None:
        builtin = "S-1-5-32-544"
        run_id = str(uuid.uuid4())
        observations = [
            {
                "schema_version": "1.0",
                "kind": "principal",
                "run_id": run_id,
                "observed_at": "2026-09-14T08:00:00Z",
                "source_key": f"principal|{host}|{builtin}",
                "sid": builtin,
                "principal_kind": "local_group",
                "host_key": host,
            }
            for host in ("fs01", "fs02")
        ]
        document = {
            "start": {
                "schema_version": "1.0",
                "run_id": run_id,
                "source": {
                    "collector": "local_groups",
                    "collector_host": "COLLECTOR01",
                    "method": "Get-LocalGroup",
                },
                "started_at": "2026-09-14T08:00:00Z",
                "scopes": [{"kind": "local_groups_host", "key": "fs01"}],
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
                "completed_at": "2026-09-14T08:00:00Z",
                "batch_count": 1,
                "observation_count": 2,
                "error_count": 0,
            },
        }
        await replay(client, document)

        ambiguous = await client.get(f"/api/v1/principals/{builtin}")
        assert ambiguous.status_code == 409
        detail = ambiguous.json()["detail"]
        assert [candidate["key"] for candidate in detail["candidates"]] == [
            f"fs01|{builtin}",
            f"fs02|{builtin}",
        ]

        scoped = await client.get(f"/api/v1/principals/{builtin}", params={"host": "FS01"})
        assert scoped.status_code == 200
        assert scoped.json()["key"] == f"fs01|{builtin}"

        by_key = await client.get(f"/api/v1/principals/fs02|{builtin}")
        assert by_key.status_code == 200
        assert by_key.json()["host_key"] == "fs02"


class TestDirectMembers:
    async def test_direct_members_are_listed_with_their_edge(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        body = (await client.get(f"/api/v1/groups/{FINANCE_RW}/members")).json()

        assert [item["principal"]["key"] for item in body["items"]] == [FINANCE_TEAM]
        assert body["items"][0]["edge_kind"] == "directory_group_member"
        assert body["items"][0]["principal"]["display_name"] == "Finance-Team"
        assert body["page"]["total"] == 1
        assert body["page"]["has_more"] is False
        assert body["page"]["next_cursor"] is None

    async def test_direct_members_exclude_the_nested_user(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        body = (await client.get(f"/api/v1/groups/{FINANCE_RW}/members")).json()

        assert ALICE not in [item["principal"]["key"] for item in body["items"]]

    async def test_paging_walks_every_member_exactly_once(self, client: AsyncClient) -> None:
        members = [str(2000 + index) for index in range(25)]
        await replay(
            client,
            synthetic_run(
                edges=[("1000", member) for member in members],
                principals={"1000": "domain_group", **{member: "user" for member in members}},
            ),
        )

        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            params: dict[str, str | int] = {"limit": 7}
            if cursor:
                params["cursor"] = cursor
            body = (
                await client.get(f"/api/v1/groups/{DOMAIN_SID}-1000/members", params=params)
            ).json()
            seen.extend(item["principal"]["key"] for item in body["items"])
            pages += 1
            cursor = body["page"]["next_cursor"]
            if not cursor:
                break

        assert pages == 4
        assert len(seen) == len(set(seen)) == 25
        assert sorted(seen) == sorted(f"{DOMAIN_SID}-{member}" for member in members)

    async def test_a_forged_cursor_is_rejected(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        response = await client.get(
            f"/api/v1/groups/{FINANCE_RW}/members", params={"cursor": "not-a-cursor"}
        )

        assert response.status_code == 422
        assert "opaque" in response.json()["detail"]

    async def test_a_cursor_from_another_endpoint_is_rejected(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "04-multiple-membership-paths")
        # Recursive results page by offset and direct listings page by key; a cursor from
        # one would silently seek to the wrong place in the other.
        recursive = (
            await client.get(
                f"/api/v1/groups/{FINANCE_RW}/effective-members",
                params={"include": "all", "limit": 1},
            )
        ).json()
        assert recursive["page"]["next_cursor"]

        response = await client.get(
            f"/api/v1/groups/{FINANCE_RW}/members",
            params={"cursor": recursive["page"]["next_cursor"]},
        )

        assert response.status_code == 422
        assert "different endpoint" in response.json()["detail"]


class TestEffectiveMembers:
    async def test_a_nested_user_is_an_effective_member_with_its_chain(
        self, client: AsyncClient
    ) -> None:
        scenario = load_scenario("03-nested-group-grant")
        await ingest_scenario(client, "03-nested-group-grant")

        body = (await client.get(f"/api/v1/groups/{FINANCE_RW}/effective-members")).json()

        assert [item["principal"]["key"] for item in body["items"]] == [ALICE]
        assert body["items"][0]["depth"] == 2
        assert body["items"][0]["path"] == scenario.expectations["expected_path"][::-1]
        assert body["traversal"]["complete"] is True
        assert body["cycles"] == []

    async def test_include_all_keeps_the_nested_group(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        body = (
            await client.get(
                f"/api/v1/groups/{FINANCE_RW}/effective-members", params={"include": "all"}
            )
        ).json()

        assert sorted(item["principal"]["key"] for item in body["items"]) == sorted(
            [FINANCE_TEAM, ALICE]
        )

    async def test_an_orphaned_sid_inside_a_group_is_reported_not_filtered_away(
        self, client: AsyncClient
    ) -> None:
        """The whole point of the tool: a SID nobody can resolve, sitting in a group."""
        await replay(
            client,
            synthetic_run(
                edges=[("1000", "7777")],
                principals={"1000": "domain_group"},  # 7777 is never described
            ),
        )

        body = (await client.get(f"/api/v1/groups/{DOMAIN_SID}-1000/effective-members")).json()

        assert len(body["items"]) == 1
        orphan = body["items"][0]["principal"]
        assert orphan["key"] == f"{DOMAIN_SID}-7777"
        assert orphan["resolved"] is False
        assert orphan["kind"] is None
        assert orphan["is_group"] is None, "unknown must never be reported as 'not a group'"

    async def test_users_only_excludes_an_unlabelled_sid(self, client: AsyncClient) -> None:
        await replay(
            client,
            synthetic_run(
                edges=[("1000", "7777"), ("1000", "2000")],
                principals={"1000": "domain_group", "2000": "user"},
            ),
        )

        body = (
            await client.get(
                f"/api/v1/groups/{DOMAIN_SID}-1000/effective-members", params={"include": "users"}
            )
        ).json()

        assert [item["principal"]["key"] for item in body["items"]] == [f"{DOMAIN_SID}-2000"]

    async def test_the_primary_group_edge_reaches_its_members(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "04-multiple-membership-paths")

        body = (await client.get(f"/api/v1/groups/{DOMAIN_USERS}/effective-members")).json()

        assert [item["principal"]["key"] for item in body["items"]] == [ALICE]
        assert body["items"][0]["edge_kinds"] == ["primary_group"]

    async def test_an_empty_result_is_still_reported_as_complete(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        body = (await client.get(f"/api/v1/groups/{ALICE}/effective-members")).json()

        assert body["items"] == []
        assert body["traversal"]["complete"] is True


class TestCycles:
    async def test_a_cyclic_graph_terminates_and_reports_the_cycle(
        self, client: AsyncClient
    ) -> None:
        scenario = load_scenario("05-cyclic-group-graph")
        await ingest_scenario(client, "05-cyclic-group-graph")

        body = (
            await client.get(
                f"/api/v1/groups/{FINANCE_RW}/effective-members", params={"include": "all"}
            )
        ).json()

        assert body["traversal"]["complete"] is True
        assert sorted(item["principal"]["key"] for item in body["items"]) == sorted(
            [RING_A, RING_B, ALICE]
        )
        assert len(body["cycles"]) == 1
        assert body["cycles"][0]["members"] == sorted(scenario.expectations["cycle_members"])
        loop = body["cycles"][0]["representative_path"]
        assert loop[0] == loop[-1]

    async def test_paths_through_a_cycle_stay_simple(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "05-cyclic-group-graph")

        body = (
            await client.get(
                f"/api/v1/principals/{ALICE}/membership-paths", params={"group": FINANCE_RW}
            )
        ).json()

        assert body["is_member"] is True
        for path in body["paths"]:
            assert len(set(path["nodes"])) == len(path["nodes"])
        assert body["cycles"]


class TestMembershipPaths:
    async def test_every_expected_path_is_returned(self, client: AsyncClient) -> None:
        scenario = load_scenario("04-multiple-membership-paths")
        await ingest_scenario(client, "04-multiple-membership-paths")

        found: list[list[str]] = []
        for group in (FINANCE_RW, DOMAIN_USERS):
            body = (
                await client.get(
                    f"/api/v1/principals/{ALICE}/membership-paths", params={"group": group}
                )
            ).json()
            found.extend(path["nodes"] for path in body["paths"])

        assert sorted(found) == sorted(scenario.expectations["expected_paths"])
        assert len(found) == scenario.expectations["distinct_paths_expected"]

    async def test_each_path_carries_the_principals_along_it(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "04-multiple-membership-paths")

        body = (
            await client.get(
                f"/api/v1/principals/{ALICE}/membership-paths", params={"group": FINANCE_RW}
            )
        ).json()

        names = {
            path["principals"][1]["display_name"] for path in body["paths"] if path["length"] == 2
        }
        assert names == {"Finance-Team", "Auditors"}

    async def test_a_non_member_gets_an_empty_but_complete_answer(
        self, client: AsyncClient
    ) -> None:
        await replay(
            client,
            synthetic_run(
                edges=[("1000", "2000")],
                principals={"1000": "domain_group", "3000": "domain_group", "2000": "user"},
            ),
        )

        body = (
            await client.get(
                f"/api/v1/principals/{DOMAIN_SID}-2000/membership-paths",
                params={"group": f"{DOMAIN_SID}-3000"},
            )
        ).json()

        assert body["is_member"] is False
        assert body["paths"] == []
        assert body["traversal"]["complete"] is True, (
            "only a complete search may be read as 'not a member'"
        )

    async def test_a_principal_is_not_a_member_of_itself(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        response = await client.get(
            f"/api/v1/principals/{ALICE}/membership-paths", params={"group": ALICE}
        )

        assert response.status_code == 422
        assert "not a member of itself" in response.json()["detail"]


class TestPrincipalGroups:
    async def test_direct_groups_are_the_default(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        body = (await client.get(f"/api/v1/principals/{ALICE}/groups")).json()

        assert [item["principal"]["key"] for item in body["items"]] == [FINANCE_TEAM]
        assert body["page"]["total"] == 1

    async def test_effective_groups_include_the_outer_group(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        body = (
            await client.get(f"/api/v1/principals/{ALICE}/groups", params={"scope": "effective"})
        ).json()

        assert sorted(item["principal"]["key"] for item in body["items"]) == sorted(
            [FINANCE_TEAM, FINANCE_RW]
        )
        assert body["traversal"]["complete"] is True

    async def test_effective_groups_cover_all_three_routes(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "04-multiple-membership-paths")

        body = (
            await client.get(f"/api/v1/principals/{ALICE}/groups", params={"scope": "effective"})
        ).json()

        assert sorted(item["principal"]["key"] for item in body["items"]) == sorted(
            [FINANCE_TEAM, AUDITORS, FINANCE_RW, DOMAIN_USERS]
        )

    async def test_an_unknown_scope_is_rejected(self, client: AsyncClient) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        response = await client.get(
            f"/api/v1/principals/{ALICE}/groups", params={"scope": "sideways"}
        )

        assert response.status_code == 422


class TestBoundsAtScale:
    async def test_a_deep_chain_truncates_and_says_so(self, client: AsyncClient) -> None:
        depth = 40
        await replay(
            client,
            synthetic_run(
                edges=[(str(1000 + level + 1), str(1000 + level)) for level in range(depth)],
                principals={str(1000 + level): "domain_group" for level in range(depth + 1)},
            ),
        )

        bounded = (
            await client.get(
                f"/api/v1/groups/{DOMAIN_SID}-{1000 + depth}/effective-members",
                params={"include": "all", "max_depth": 5, "limit": 500},
            )
        ).json()

        assert bounded["traversal"]["complete"] is False
        assert bounded["traversal"]["truncation"] == ["max_depth"]
        assert bounded["page"]["total"] == 5

        full = (
            await client.get(
                f"/api/v1/groups/{DOMAIN_SID}-{1000 + depth}/effective-members",
                params={"include": "all", "max_depth": 64, "limit": 500},
            )
        ).json()
        assert full["traversal"]["complete"] is True
        assert full["page"]["total"] == depth

    async def test_a_wide_group_is_enumerated_and_paged(self, client: AsyncClient) -> None:
        width = 300
        members = [str(2000 + index) for index in range(width)]
        await replay(
            client,
            synthetic_run(
                edges=[("1000", member) for member in members],
                principals={"1000": "domain_group", **{member: "user" for member in members}},
            ),
        )

        first = (
            await client.get(
                f"/api/v1/groups/{DOMAIN_SID}-1000/effective-members", params={"limit": 100}
            )
        ).json()

        assert first["page"]["total"] == width
        assert len(first["items"]) == 100
        assert first["page"]["has_more"] is True
        assert first["traversal"]["complete"] is True
        assert first["traversal"]["depth_reached"] == 1

        collected = list(first["items"])
        cursor = first["page"]["next_cursor"]
        while cursor:
            page = (
                await client.get(
                    f"/api/v1/groups/{DOMAIN_SID}-1000/effective-members",
                    params={"limit": 100, "cursor": cursor},
                )
            ).json()
            collected.extend(page["items"])
            cursor = page["page"]["next_cursor"]

        keys = [item["principal"]["key"] for item in collected]
        assert len(keys) == len(set(keys)) == width

    async def test_a_node_limit_produces_a_declared_lower_bound(self, client: AsyncClient) -> None:
        members = [str(2000 + index) for index in range(50)]
        await replay(
            client,
            synthetic_run(
                edges=[("1000", member) for member in members],
                principals={"1000": "domain_group", **{member: "user" for member in members}},
            ),
        )

        body = (
            await client.get(
                f"/api/v1/groups/{DOMAIN_SID}-1000/effective-members",
                params={"max_nodes": 10, "limit": 100},
            )
        ).json()

        assert body["page"]["total"] == 10
        assert body["traversal"]["complete"] is False
        assert "max_nodes" in body["traversal"]["truncation"]

    async def test_a_limit_above_the_ceiling_is_clamped_and_reported(
        self, client: AsyncClient
    ) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        body = (
            await client.get(
                f"/api/v1/groups/{FINANCE_RW}/effective-members", params={"max_depth": 100_000}
            )
        ).json()

        assert body["traversal"]["limits"]["max_depth"] == 128
        assert body["traversal"]["complete"] is True

    @pytest.mark.parametrize("value", [0, -1])
    async def test_a_nonsense_limit_is_rejected(self, client: AsyncClient, value: int) -> None:
        await ingest_scenario(client, "03-nested-group-grant")

        response = await client.get(
            f"/api/v1/groups/{FINANCE_RW}/effective-members", params={"max_depth": value}
        )

        assert response.status_code == 422
