r"""The path engine's HTTP surface over PostgreSQL, over estates ingested like any other.

Nothing here records a relationship by hand. Each estate arrives as collector transcripts
through the ingestion API, the projector derives the graph at each completion exactly as it
does in production, and the tests ask the graph API about it. So what is asserted is the whole
chain a client relies on: collected facts → derived relationships → a bounded path → an
answer that says what it rests on and what it cannot see.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Role
from app.db import Database
from app.domain.relationships import NodeKind, NodeRef
from app.models.schema import graph_relationships, metadata
from app.relationships import RelationshipProjector
from app.repositories.basis import CollectionBasisRepository
from app.traversal.semantics import TraversalPolicy
from app.traversal.service import RelationshipGraphService
from tests.support.ingest import replay
from tests.support.relationships import ace, at, edge, principal, resource, transcript

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
FOREIGN_DOMAIN = "S-1-5-21-3000000001-3000000002-3000000003"
ALICE, BOB, CAROL = f"{DOMAIN}-1104", f"{DOMAIN}-1105", f"{DOMAIN}-1106"
TEAM, RW, AUDITORS, OTHER = f"{DOMAIN}-1201", f"{DOMAIN}-1202", f"{DOMAIN}-1210", f"{DOMAIN}-1150"
FOREIGN_USER = f"{FOREIGN_DOMAIN}-1500"
ADMINS, RDP, WINRM = "S-1-5-32-544", "S-1-5-32-555", "S-1-5-32-580"
AUTHENTICATED_USERS = "S-1-5-11"
FULL, MODIFY = 0x001F01FF, 0x001301BF
FINANCE, PAYROLL = "\\\\FS01\\Finance", "\\\\FS01\\Finance\\Payroll"
DATA = "\\\\FS02\\Data"

USERS = (ALICE, BOB, CAROL)


def p(sid: str) -> str:
    return f"principal:{sid}"


def host_ref(host: str, sid: str) -> str:
    return f"principal:{host}|{sid}"


def ntfs(path: str) -> str:
    return f"ntfs_resource:{path.casefold()}"


PAYROLL_REF, FINANCE_REF, DATA_REF = ntfs(PAYROLL), ntfs(FINANCE), ntfs(DATA)
FS01_ADMINS, FS02_ADMINS = host_ref("fs01", ADMINS), host_ref("fs02", ADMINS)


def directory(
    minute: int,
    edges: tuple[tuple[str, str], ...] = ((TEAM, ALICE), (RW, TEAM), (AUDITORS, BOB)),
    *,
    foreign: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    observed = at(minute)
    sids = sorted({sid for pair in edges for sid in pair} | set(USERS))
    items = [
        *(
            principal(sid, observed, kind="user" if sid in USERS else "domain_group", name=sid[-4:])
            for sid in sids
        ),
        *(
            edge(group, member, observed, member_kind="user" if member in USERS else "domain_group")
            for group, member in edges
        ),
        *(
            edge(
                group,
                member,
                observed,
                member_kind="foreign_security_principal",
                foreign=True,
            )
            for group, member in foreign
        ),
    ]
    return transcript("active_directory", [("domain", "corp.example.com")], items, started=observed)


def local_groups(
    minute: int,
    members: tuple[tuple[str, str], ...] = ((ADMINS, RW), (RDP, AUDITORS), (WINRM, AUDITORS)),
    *,
    host: str = "FS01",
) -> dict[str, Any]:
    observed = at(minute)
    groups = sorted({group for group, _ in members})
    items = [
        *(principal(group, observed, kind="local_group", host=host) for group in groups),
        *(
            edge(
                group,
                member,
                observed,
                kind="local_group_member",
                host=host,
                member_kind="user" if member in USERS else "domain_group",
            )
            for group, member in members
        ),
    ]
    return transcript(
        "local_groups", [("local_groups_host", host.casefold())], items, started=observed
    )


def ntfs_scan(
    minute: int,
    *,
    payroll_entries: tuple[tuple[str, int, int, str], ...] = ((ADMINS, FULL, 0, "allow"),),
    owner: str = ADMINS,
    finance_entries: tuple[tuple[str, int, int, str], ...] = ((RW, MODIFY, 0, "allow"),),
    root: str = FINANCE,
    child: str = PAYROLL,
) -> dict[str, Any]:
    observed = at(minute)
    items = [
        resource(root, observed, ace_count=len(finance_entries)),
        *(
            ace(root, trustee, mask, order, observed, ace_type=kind)
            for trustee, mask, order, kind in finance_entries
        ),
        resource(child, observed, ace_count=len(payroll_entries), owner=owner, depth=1),
        *(
            ace(child, trustee, mask, order, observed, ace_type=kind)
            for trustee, mask, order, kind in payroll_entries
        ),
    ]
    return transcript("ntfs", [("directory_tree", root.casefold())], items, started=observed)


async def estate(client: AsyncClient) -> None:
    """alice ∈ Finance-Team ∈ Finance-RW ∈ fs01 Administrators, which owns and controls
    Payroll; bob ∈ Auditors ∈ fs01 Remote Desktop and Remote Management Users; carol is in
    nothing."""
    await replay(client, directory(0))
    await replay(client, local_groups(1))
    await replay(client, ntfs_scan(2))


async def shortest(client: AsyncClient, source: str, target: str, **params: Any) -> Any:
    response = await client.get(
        "/api/v1/graph/paths/shortest", params={"source": source, "target": target, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def step_types(path: dict[str, Any]) -> list[str]:
    return [step["relationships"][0]["relationship_type"] for step in path["steps"]]


def reasons(body: dict[str, Any]) -> list[str]:
    return [reason["code"] for reason in body["completeness"]["reasons"]]


async def wipe(database: Database) -> None:
    names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
    async with database.engine.begin() as connection:
        await connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


# --- Shortest path ------------------------------------------------------------------------


class TestShortestPath:
    async def test_membership_through_a_local_group_to_ntfs_control(
        self, client: AsyncClient
    ) -> None:
        await estate(client)

        body = await shortest(client, p(ALICE), PAYROLL_REF)

        assert body["outcome"] == "found"
        assert body["conclusive"] is True
        path = body["path"]
        assert path["nodes"] == [p(ALICE), p(TEAM), p(RW), FS01_ADMINS, PAYROLL_REF]
        assert step_types(path) == ["member_of", "member_of", "local_group_member_of", "owns"]
        assert [r["relationship_type"] for r in path["steps"][2]["readings"]] == ["local_admin_of"]
        assert {r["relationship_type"] for r in path["steps"][3]["relationships"]} == {
            "owns",
            "generic_all",
            "generic_write",
            "write_dacl",
            "write_owner",
        }
        assert path["assessment"]["confidence"] == "confirmed"
        assert path["assessment"]["conditional"] is False
        assert [note["code"] for note in path["assessment"]["notes"]] == [
            "share_layer_not_evaluated"
        ]
        assert body["completeness"] == {"complete": True, "reasons": [], "truncation": []}
        assert body["basis"]["projection"]["state"] == "current"
        assert body["basis"]["snapshot"] == "repeatable_read"
        assert {gap["code"] for gap in body["coverage"]["gaps"]} >= {
            "relationship_types_not_collected",
            "implicit_token_memberships",
        }

    async def test_every_edge_is_traceable(self, client: AsyncClient) -> None:
        await estate(client)
        body = await shortest(client, p(ALICE), PAYROLL_REF)

        for step in body["path"]["steps"]:
            for relationship in step["relationships"] + step["readings"]:
                assert relationship["premises"], relationship
                assert relationship["rule"].count("@") == 1
                assert relationship["explanation"]
                detail = await client.get(relationship["href"])
                assert detail.status_code == 200
                supports = detail.json()["supports"]
                assert supports and all(support["evidence"] for support in supports)
        labels = {node["ref"]: node["label"] for node in body["nodes"]}
        assert labels[PAYROLL_REF] == PAYROLL
        assert labels[FS01_ADMINS] == "fs01\\BUILTIN\\Administrators"

    async def test_a_direct_relationship_is_one_hop(self, client: AsyncClient) -> None:
        await estate(client)

        body = await shortest(client, p(RW), FINANCE_REF)

        assert body["path"]["hops"] == 1
        assert step_types(body["path"]) == ["generic_write"]

    async def test_equal_length_routes_resolve_by_canonical_order(
        self, client: AsyncClient
    ) -> None:
        await replay(client, directory(0, ((TEAM, ALICE), (OTHER, ALICE))))
        await replay(
            client,
            ntfs_scan(1, finance_entries=((TEAM, MODIFY, 0, "allow"), (OTHER, MODIFY, 1, "allow"))),
        )

        first = await shortest(client, p(ALICE), FINANCE_REF)
        again = await shortest(client, p(ALICE), FINANCE_REF)

        # OTHER's RID (1150) sorts before TEAM's (1201): the key decides, not the name.
        assert first["path"]["nodes"] == [p(ALICE), p(OTHER), FINANCE_REF]
        assert again == first

    async def test_no_route_in_a_current_graph_is_a_conclusive_no_path(
        self, client: AsyncClient
    ) -> None:
        await estate(client)

        body = await shortest(client, p(CAROL), PAYROLL_REF)

        assert body["outcome"] == "no_path"
        assert body["conclusive"] is True
        assert body["path"] is None
        assert body["completeness"]["complete"] is True
        assert "not evidence of their absence" in body["coverage"]["statement"]

    async def test_a_route_through_a_logon_time_sid_is_a_boundary_not_a_no(
        self, client: AsyncClient
    ) -> None:
        await replay(client, directory(0))
        await replay(
            client, ntfs_scan(1, finance_entries=((AUTHENTICATED_USERS, MODIFY, 0, "allow"),))
        )

        body = await shortest(client, p(CAROL), FINANCE_REF)

        assert body["outcome"] == "boundary"
        assert body["conclusive"] is False
        assert reasons(body) == ["implicit_membership_boundary"]
        assert body["exploration"]["implicit_membership_boundary"] == [p(AUTHENTICATED_USERS)]


class TestValidation:
    async def test_a_node_reference_must_be_canonical(self, client: AsyncClient) -> None:
        await estate(client)
        for source, field in (
            ("S-1-5-21-1-2-3-4", "source"),  # no kind
            ("account:S-1-5-21-1-2-3-4", "source"),  # no such kind
            (f"principal:FS01|{ADMINS}", "source"),  # host not case-folded
            ("principal:s-1-5-21-1-2-3-4", "source"),  # SID not canonical
            ("ntfs_resource:\\\\FS01\\Finance", "source"),  # path not case-folded
            ("gpo:S-1-5-21-1-2-3|0f6d1f4a-0000-0000-0000-000000000000", "source"),  # reserved
        ):
            response = await client.get(
                "/api/v1/graph/paths/shortest", params={"source": source, "target": PAYROLL_REF}
            )
            assert response.status_code == 422, source
            assert response.json()["detail"]["field"] == field, source

    async def test_the_same_node_twice_is_refused(self, client: AsyncClient) -> None:
        await estate(client)
        response = await client.get(
            "/api/v1/graph/paths/shortest", params={"source": p(ALICE), "target": p(ALICE)}
        )
        assert response.status_code == 422
        assert response.json()["detail"]["field"] == "target"

    async def test_an_unknown_node_is_a_404_and_a_bare_builtin_lists_its_hosts(
        self, client: AsyncClient
    ) -> None:
        await estate(client)

        unknown = await client.get(
            "/api/v1/graph/paths/shortest",
            params={"source": p(f"{DOMAIN}-9999"), "target": PAYROLL_REF},
        )
        builtin = await client.get(
            "/api/v1/graph/paths/shortest", params={"source": p(ALICE), "target": p(ADMINS)}
        )

        assert unknown.status_code == 404
        assert unknown.json()["detail"]["field"] == "source"
        assert builtin.status_code == 404
        assert builtin.json()["detail"]["candidates"] == [FS01_ADMINS]

    @pytest.mark.parametrize(
        ("route", "params"),
        [
            ("paths/shortest", {"max_depth": 0}),
            ("paths/shortest", {"max_depth": 33}),
            ("paths/shortest", {"max_nodes": 100_001}),
            ("paths/shortest", {"max_edges": 500_001}),
            ("paths/shortest", {"policy": "everything"}),
            ("paths", {"max_paths": 101}),
            ("paths", {"max_paths": 0}),
            ("reachability", {"limit": 501}),
            ("reachability", {"max_depth": 1000}),
            ("neighbors", {"depth": 4}),
            ("neighbors", {"type": "nested_member_of"}),
            ("neighbors", {"type": "trusts"}),
        ],
    )
    async def test_an_out_of_range_limit_is_refused_never_clamped(
        self, client: AsyncClient, route: str, params: dict[str, Any]
    ) -> None:
        base = {
            "paths/shortest": {"source": p(ALICE), "target": PAYROLL_REF},
            "paths": {"source": p(ALICE), "target": PAYROLL_REF},
            "reachability": {"target": PAYROLL_REF},
            "neighbors": {"node": p(RW)},
        }[route]
        response = await client.get(f"/api/v1/graph/{route}", params={**base, **params})
        assert response.status_code == 422, response.text


# --- Host scope ---------------------------------------------------------------------------


class TestHostScope:
    async def test_the_same_local_group_on_two_hosts_is_two_nodes(
        self, client: AsyncClient
    ) -> None:
        await estate(client)
        await replay(client, local_groups(3, ((ADMINS, AUDITORS),), host="FS02"))
        await replay(
            client,
            ntfs_scan(
                4,
                root="\\\\FS02\\Share",
                child=DATA,
                finance_entries=(),
                payroll_entries=((ADMINS, FULL, 0, "allow"),),
            ),
        )

        alice_fs02 = await shortest(client, p(ALICE), DATA_REF)
        bob_fs02 = await shortest(client, p(BOB), DATA_REF)
        bob_fs01 = await shortest(client, p(BOB), PAYROLL_REF)

        assert alice_fs02["outcome"] == "no_path"
        assert bob_fs02["path"]["nodes"] == [p(BOB), p(AUDITORS), FS02_ADMINS, DATA_REF]
        assert bob_fs01["outcome"] == "no_path"
        for step in bob_fs02["path"]["steps"][1:]:
            assert step["scope"] == {"kind": "host", "key": "fs02"}

    async def test_a_host_spelled_two_ways_does_not_join(self, client: AsyncClient) -> None:
        """GRAPH-01B limitation, kept visible: a local group and an NTFS trustee join only
        when both collectors spell the host identically. Nothing guesses that they match."""
        await replay(client, directory(0))
        await replay(client, local_groups(1))
        await replay(
            client,
            ntfs_scan(
                2,
                root="\\\\fs01.corp.example.com\\Finance",
                child="\\\\fs01.corp.example.com\\Finance\\Payroll",
            ),
        )

        body = await shortest(client, p(ALICE), ntfs("\\\\fs01.corp.example.com\\Finance\\Payroll"))

        assert body["outcome"] == "no_path"


# --- Conditional and unresolved -------------------------------------------------------------


class TestConditionalRoutes:
    @pytest.mark.parametrize(
        ("group", "reading", "conditions"),
        [
            (
                RDP,
                "remote_desktop_user_of",
                {"logon_right_unevaluated", "remote_service_state_unknown"},
            ),
            (
                WINRM,
                "remote_management_user_of",
                {"service_access_policy_unevaluated", "remote_service_state_unknown"},
            ),
        ],
    )
    @pytest.mark.parametrize("policy", ["all_current", "confirmed_only"])
    async def test_a_route_to_remote_access_is_conditional(
        self,
        client: AsyncClient,
        group: str,
        reading: str,
        conditions: set[str],
        policy: str,
    ) -> None:
        await estate(client)

        body = await shortest(client, p(BOB), host_ref("fs01", group), policy=policy)

        assert body["outcome"] == "found"
        (found,) = body["path"]["steps"][-1]["readings"]
        assert found["relationship_type"] == reading
        assert found["conditional"] is True
        assert {item["code"] for item in found["conditions"]} == conditions
        assert "succe" not in found["explanation"].casefold()
        assessment = body["path"]["assessment"]
        assert assessment["chain_confidence"] == "confirmed"
        assert assessment["confidence"] == "probable"
        assert assessment["conditional"] is True
        assert {
            item["code"] for item in assessment["conditions"] if item["origin"] == "reading"
        } == conditions

    async def test_a_conditional_grant_is_shown_and_confirmed_only_routes_around_it(
        self, client: AsyncClient
    ) -> None:
        """A Deny for another trustee ahead of Finance-RW's grant makes it conditional. The
        owner's implicit right through fs01 Administrators is a confirmed, longer route."""
        await replay(client, directory(0))
        await replay(client, local_groups(1))
        await replay(
            client,
            ntfs_scan(2, payroll_entries=((AUDITORS, FULL, 0, "deny"), (RW, FULL, 1, "allow"))),
        )

        mixed = await shortest(client, p(ALICE), PAYROLL_REF)
        confirmed = await shortest(client, p(ALICE), PAYROLL_REF, policy="confirmed_only")

        assert mixed["path"]["nodes"] == [p(ALICE), p(TEAM), p(RW), PAYROLL_REF]
        assert mixed["path"]["assessment"]["conditional"] is True
        assert mixed["path"]["assessment"]["chain_confidence"] == "probable"
        last = mixed["path"]["steps"][-1]
        assert all(relationship["conditional"] for relationship in last["relationships"])
        assert {c["code"] for c in mixed["path"]["assessment"]["conditions"]} == {"deny_may_apply"}
        assert confirmed["path"]["nodes"] == [p(ALICE), p(TEAM), p(RW), FS01_ADMINS, PAYROLL_REF]
        assert confirmed["path"]["assessment"]["conditional"] is False
        assert "excluded_by_policy" in reasons(confirmed)

    async def test_confirmed_only_with_no_confirmed_route_is_policy_restricted(
        self, client: AsyncClient
    ) -> None:
        await replay(client, directory(0))
        await replay(
            client,
            ntfs_scan(
                1,
                owner=AUDITORS,
                payroll_entries=((AUDITORS, FULL, 0, "deny"), (RW, FULL, 1, "allow")),
            ),
        )

        body = await shortest(client, p(ALICE), PAYROLL_REF, policy="confirmed_only")

        assert body["outcome"] == "policy_restricted"
        assert body["conclusive"] is False
        assert body["exploration"]["excluded_by_policy"] >= 1

    async def test_a_foreign_member_is_unresolved_and_a_boundary(self, client: AsyncClient) -> None:
        await replay(client, directory(0, foreign=((RW, FOREIGN_USER),)))
        await replay(client, local_groups(1))
        await replay(client, ntfs_scan(2))

        forward = await shortest(client, p(FOREIGN_USER), PAYROLL_REF)
        none = await shortest(client, p(CAROL), PAYROLL_REF)
        provenance = await shortest(client, p(FOREIGN_USER), f"domain:{FOREIGN_DOMAIN}")

        assert forward["path"]["assessment"]["unresolved"] is True
        assert forward["path"]["assessment"]["confidence"] == "indeterminate"
        assert none["outcome"] == "boundary"
        assert "unresolved_boundary" in reasons(none)
        assert none["exploration"]["unresolved_boundary"] == [p(FOREIGN_USER)]
        # foreign_principal_of is provenance: never a step of a privilege path.
        assert provenance["outcome"] == "no_path"


class TestCurrentState:
    async def test_a_membership_reconciled_away_is_history_and_never_followed(
        self, client: AsyncClient
    ) -> None:
        await estate(client)
        await replay(client, directory(10, ((RW, TEAM), (AUDITORS, BOB))))

        body = await shortest(client, p(ALICE), PAYROLL_REF)

        assert body["outcome"] == "no_path"
        assert body["exploration"]["not_current_skipped"] >= 1

    async def test_a_membership_that_returns_is_followed_again(self, client: AsyncClient) -> None:
        await estate(client)
        await replay(client, directory(10, ((RW, TEAM), (AUDITORS, BOB))))
        await replay(client, directory(20))

        body = await shortest(client, p(ALICE), PAYROLL_REF)

        assert body["outcome"] == "found"


# --- Alternate paths ----------------------------------------------------------------------


class TestAlternatePaths:
    async def _two_ways(self, client: AsyncClient) -> None:
        await replay(client, directory(0, ((TEAM, ALICE), (RW, TEAM), (OTHER, ALICE))))
        await replay(client, local_groups(1, ((ADMINS, RW), (ADMINS, OTHER))))
        await replay(client, ntfs_scan(2))

    async def test_routes_shortest_first_then_canonical_and_never_twice(
        self, client: AsyncClient
    ) -> None:
        await self._two_ways(client)

        response = await client.get(
            "/api/v1/graph/paths", params={"source": p(ALICE), "target": PAYROLL_REF}
        )

        body = response.json()
        assert body["outcome"] == "found"
        assert [path["nodes"] for path in body["paths"]] == [
            [p(ALICE), p(OTHER), FS01_ADMINS, PAYROLL_REF],
            [p(ALICE), p(TEAM), p(RW), FS01_ADMINS, PAYROLL_REF],
        ]
        assert body["completeness"]["complete"] is True
        assert body["limits"]["max_paths"] == 10

    async def test_the_cap_is_reported(self, client: AsyncClient) -> None:
        await self._two_ways(client)

        response = await client.get(
            "/api/v1/graph/paths",
            params={"source": p(ALICE), "target": PAYROLL_REF, "max_paths": 1},
        )

        body = response.json()
        assert len(body["paths"]) == 1
        assert body["completeness"]["truncation"] == ["max_paths"]


# --- Reverse reachability -----------------------------------------------------------------


class TestReachability:
    async def test_every_source_nearest_first_and_paged(self, client: AsyncClient) -> None:
        await estate(client)

        first = await client.get(
            "/api/v1/graph/reachability", params={"target": PAYROLL_REF, "limit": 2}
        )
        body = first.json()
        second = await client.get(
            "/api/v1/graph/reachability",
            params={"target": PAYROLL_REF, "limit": 2, "cursor": body["page"]["next_cursor"]},
        )

        assert [(item["node"]["ref"], item["hops"]) for item in body["items"]] == [
            (FS01_ADMINS, 1),
            (p(RW), 2),
        ]
        assert body["page"]["total"] == 4
        assert [(item["node"]["ref"], item["hops"]) for item in second.json()["items"]] == [
            (p(TEAM), 3),
            (p(ALICE), 4),
        ]
        route = second.json()["items"][1]["path"]
        assert route["nodes"][0] == p(ALICE)
        assert route["nodes"][-1] == PAYROLL_REF
        assert body["completeness"]["complete"] is True

    async def test_a_depth_cut_is_reported(self, client: AsyncClient) -> None:
        await estate(client)

        response = await client.get(
            "/api/v1/graph/reachability", params={"target": PAYROLL_REF, "max_depth": 2}
        )

        body = response.json()
        assert body["page"]["total"] == 2
        assert body["completeness"]["truncation"] == ["max_depth"]

    async def test_boundaries_are_flagged_on_the_source(self, client: AsyncClient) -> None:
        await replay(client, directory(0, foreign=((RW, FOREIGN_USER),)))
        await replay(
            client, ntfs_scan(1, finance_entries=((AUTHENTICATED_USERS, MODIFY, 0, "allow"),))
        )
        await replay(client, local_groups(2))

        response = await client.get("/api/v1/graph/reachability", params={"target": PAYROLL_REF})
        finance = await client.get("/api/v1/graph/reachability", params={"target": FINANCE_REF})

        flags = {item["node"]["ref"]: item["boundary"] for item in response.json()["items"]}
        assert flags[p(FOREIGN_USER)] == ["unresolved_boundary"]
        finance_flags = {item["node"]["ref"]: item["boundary"] for item in finance.json()["items"]}
        assert finance_flags[p(AUTHENTICATED_USERS)] == ["implicit_membership_boundary"]
        assert "implicit_membership_boundary" in reasons(finance.json())


# --- Neighbors ----------------------------------------------------------------------------


class TestNeighbors:
    async def test_directions_types_depth_and_paging(self, client: AsyncClient) -> None:
        await estate(client)

        outbound = (
            await client.get(
                "/api/v1/graph/neighbors", params={"node": p(RW), "direction": "outbound"}
            )
        ).json()
        inbound = (
            await client.get(
                "/api/v1/graph/neighbors",
                params={"node": p(RW), "direction": "inbound", "type": "member_of", "depth": 2},
            )
        ).json()
        paged = (
            await client.get("/api/v1/graph/neighbors", params={"node": p(RW), "limit": 1})
        ).json()

        assert {item["relationship"]["relationship_type"] for item in outbound["items"]} == {
            "local_group_member_of",
            "local_admin_of",
            "generic_write",
        }
        roles = {
            item["relationship"]["relationship_type"]: item["relationship"]["role"]
            for item in outbound["items"]
        }
        assert roles["local_admin_of"] == "reading"
        assert [(item["relationship"]["source"], item["depth"]) for item in inbound["items"]] == [
            (p(TEAM), 1),
            (p(ALICE), 2),
        ]
        assert len(paged["items"]) == 1
        assert paged["page"]["has_more"] is True
        assert paged["page"]["total"] == len(outbound["items"]) + 1

    async def test_provenance_is_shown_as_informational(self, client: AsyncClient) -> None:
        await replay(client, directory(0, foreign=((RW, FOREIGN_USER),)))

        body = (
            await client.get(
                "/api/v1/graph/neighbors",
                params={"node": p(FOREIGN_USER), "direction": "outbound"},
            )
        ).json()

        by_type = {item["relationship"]["relationship_type"]: item for item in body["items"]}
        assert by_type["foreign_principal_of"]["relationship"]["role"] == "informational"
        assert by_type["foreign_principal_of"]["relationship"]["target"] == (
            f"domain:{FOREIGN_DOMAIN}"
        )
        assert by_type["member_of"]["relationship"]["unresolved"] is True


# --- Readiness ----------------------------------------------------------------------------


class TestReadiness:
    async def test_a_completed_run_never_derived_makes_answers_inconclusive(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What an upgrade to 0019 looks like before rebuild-graph runs."""
        await estate(client)

        async def not_derived(*_: Any, **__: Any) -> None:
            return None

        monkeypatch.setattr("app.ingestion.service.project_completed_run", not_derived)
        await replay(
            client, directory(10, ((TEAM, ALICE), (RW, TEAM), (AUDITORS, BOB), (RW, CAROL)))
        )
        monkeypatch.undo()

        carol = await shortest(client, p(CAROL), PAYROLL_REF)
        alice = await shortest(client, p(ALICE), PAYROLL_REF)
        status = (await client.get("/api/v1/graph/readiness")).json()

        assert carol["outcome"] == "incomplete_evidence"
        assert reasons(carol) == ["projection_behind"]
        assert alice["outcome"] == "found"
        assert alice["conclusive"] is False
        assert status["basis"]["projection"]["state"] == "behind"
        assert status["basis"]["projection"]["underived_runs"] == 1
        assert [reason["code"] for reason in status["reasons"]] == ["projection_behind"]

        # The operator rebuilds; the graph catches up and the answer becomes conclusive.
        await RelationshipProjector(session).rebuild(now=dt.datetime.now(tz=dt.UTC))
        await session.commit()
        rebuilt = await shortest(client, p(CAROL), PAYROLL_REF)
        assert rebuilt["outcome"] == "found"
        assert rebuilt["conclusive"] is True
        assert rebuilt["basis"]["projection"]["state"] == "current"

    async def test_a_failed_derivation_is_named(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await replay(client, directory(0))

        def broken(*_: Any, **__: Any) -> Any:
            raise RuntimeError("a defect in a rule")

        monkeypatch.setattr("app.relationships.projector.derive_resource", broken)
        await replay(client, ntfs_scan(1))
        monkeypatch.undo()

        body = await shortest(client, p(CAROL), p(RW))

        assert body["outcome"] == "incomplete_evidence"
        assert reasons(body) == ["projection_failed"]
        assert body["basis"]["projection"]["state"] == "failed"
        assert body["basis"]["projection"]["latest_pass"]["status"] == "failed"

    async def test_nothing_collected(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/graph/readiness")).json()

        assert body["basis"]["projection"]["state"] == "empty"
        assert [reason["code"] for reason in body["reasons"]] == ["nothing_collected"]


# --- Caching, determinism and rebuilds ------------------------------------------------------


class TestDeterminismAndRebuild:
    async def test_a_clean_rebuild_returns_the_same_paths(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await estate(client)
        queries = [
            (p(ALICE), PAYROLL_REF),
            (p(BOB), host_ref("fs01", RDP)),
            (p(CAROL), PAYROLL_REF),
        ]
        before = [await shortest(client, source, target) for source, target in queries]
        alternates = await client.get(
            "/api/v1/graph/paths", params={"source": p(ALICE), "target": PAYROLL_REF}
        )

        await RelationshipProjector(session).rebuild(
            now=dt.datetime.now(tz=dt.UTC), discard_existing=True
        )
        await session.commit()
        after = [await shortest(client, source, target) for source, target in queries]
        again = await client.get(
            "/api/v1/graph/paths", params={"source": p(ALICE), "target": PAYROLL_REF}
        )

        for old, new in zip(before, after, strict=True):
            assert new["outcome"] == old["outcome"]
            assert new["path"] == old["path"]
        assert again.json()["paths"] == alternates.json()["paths"]

    async def test_the_answer_does_not_depend_on_ingestion_order(
        self, client: AsyncClient, database: Database
    ) -> None:
        await replay(client, directory(0))
        await replay(client, local_groups(1))
        await replay(client, ntfs_scan(2))
        forward = await shortest(client, p(ALICE), PAYROLL_REF)

        await wipe(database)
        await replay(client, ntfs_scan(0))
        await replay(client, local_groups(1))
        await replay(client, directory(2))
        backward = await shortest(client, p(ALICE), PAYROLL_REF)

        assert backward["path"]["nodes"] == forward["path"]["nodes"]
        assert [
            [r["relationship_id"] for r in step["relationships"]]
            for step in backward["path"]["steps"]
        ] == [
            [r["relationship_id"] for r in step["relationships"]]
            for step in forward["path"]["steps"]
        ]

    async def test_a_conditional_get_and_a_rebuild_invalidating_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await estate(client)
        params = {"source": p(ALICE), "target": PAYROLL_REF}
        first = await client.get("/api/v1/graph/paths/shortest", params=params)
        etag = first.headers["ETag"]

        cached = await client.get(
            "/api/v1/graph/paths/shortest", params=params, headers={"If-None-Match": etag}
        )
        other = await client.get(
            "/api/v1/graph/paths/shortest",
            params={**params, "policy": "confirmed_only"},
            headers={"If-None-Match": etag},
        )
        await RelationshipProjector(session).rebuild(now=dt.datetime.now(tz=dt.UTC))
        await session.commit()
        rebuilt = await client.get(
            "/api/v1/graph/paths/shortest", params=params, headers={"If-None-Match": etag}
        )

        assert cached.status_code == 304
        assert other.status_code == 200
        # A rebuild moves no scan run; the projection token is what invalidates the tag.
        assert rebuilt.status_code == 200
        assert rebuilt.headers["ETag"] != etag


class TestSnapshot:
    async def test_one_request_reads_one_snapshot(
        self, client: AsyncClient, database: Database
    ) -> None:
        """A run that completes while a request is between two reads is not half-visible."""
        await estate(client)
        carol = NodeRef.principal(CAROL)
        payroll = NodeRef(NodeKind.NTFS_RESOURCE, PAYROLL.casefold())

        async with database.session() as session:
            service = RelationshipGraphService(session)
            snapshot = await service.open()
            before = await service.shortest_path(carol, payroll, policy=TraversalPolicy.ALL_CURRENT)
            # Another writer completes a run that puts carol into Finance-RW, and commits.
            await replay(
                client,
                directory(10, ((TEAM, ALICE), (RW, TEAM), (AUDITORS, BOB), (RW, CAROL))),
            )
            during = await service.shortest_path(carol, payroll, policy=TraversalPolicy.ALL_CURRENT)
            basis = await CollectionBasisRepository(session).current()

        assert snapshot.isolation == "repeatable_read"
        assert before.paths == () and during.paths == ()
        assert basis.token == snapshot.basis.token
        # The next request reads the new state, and says it is a different one.
        after = await shortest(client, p(CAROL), PAYROLL_REF)
        assert after["outcome"] == "found"
        assert after["basis"]["collection"]["token"] != snapshot.basis.token

    async def test_a_graph_request_cannot_write(
        self, client: AsyncClient, database: Database
    ) -> None:
        await estate(client)
        async with database.session() as session:
            await RelationshipGraphService(session).open()
            with pytest.raises(sa.exc.DBAPIError, match="read-only transaction"):
                await session.execute(sa.delete(graph_relationships))


# --- Authorization ------------------------------------------------------------------------


class TestAuthorization:
    async def test_graph_read_is_required_and_held_by_the_estate_readers(
        self,
        client: AsyncClient,
        anonymous_client: AsyncClient,
        client_as: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        await estate(client)
        params = {"source": p(ALICE), "target": PAYROLL_REF}

        anonymous = await anonymous_client.get("/api/v1/graph/paths/shortest", params=params)
        async with client_as() as nobody:
            no_role = await nobody.get("/api/v1/graph/paths/shortest", params=params)
        async with client_as(Role.REMEDIATOR) as reserved:
            reserved_only = await reserved.get("/api/v1/graph/paths/shortest", params=params)
        async with client_as(Role.VIEWER) as viewer:
            allowed = await viewer.get("/api/v1/graph/paths/shortest", params=params)

        assert anonymous.status_code == 401
        assert no_role.status_code == 403
        assert reserved_only.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["outcome"] == "found"


# --- Query cost ---------------------------------------------------------------------------


class TestQueryCost:
    async def test_a_search_costs_reads_per_level_not_per_member(
        self, client: AsyncClient, database: Database
    ) -> None:
        from tests.db.test_query_cost import StatementLog

        many = tuple((TEAM, f"{DOMAIN}-{7000 + index}") for index in range(150))
        await replay(client, directory(0, ((TEAM, ALICE), (RW, TEAM), *many)))
        await replay(client, local_groups(1))
        await replay(client, ntfs_scan(2))

        log = StatementLog(database.engine)
        with log:
            await client.get("/api/v1/graph/reachability", params={"target": PAYROLL_REF})
        wide = log.count
        with log:
            await shortest(client, p(ALICE), PAYROLL_REF)
        path = log.count

        # 151 members of Finance-Team are one level of one read, not 151 reads.
        assert log.reading(graph_relationships.name) <= 12
        assert wide <= 40, wide
        assert path <= 40, path
