"""GRAPH-01C traversal through the real PostgreSQL relationship projection and HTTP API."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.auth.roles import Role
from app.db import Database
from app.domain.relationships import RelationshipType
from tests.support.ingest import ingest_storable_scenario

pytestmark = pytest.mark.anyio

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN}-1104"
FINANCE_RW = f"{DOMAIN}-1202"


def shortest_url() -> str:
    return (
        "/api/v1/graph/traversal/shortest"
        f"?source_kind=principal&source_key={ALICE}"
        f"&target_kind=principal&target_key={FINANCE_RW}"
    )


class StatementLog:
    """Every SQL statement one engine issues while a request is measured."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine.sync_engine if hasattr(engine, "sync_engine") else engine
        self.statements: list[str] = []

    def _record(self, conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        self.statements.append(" ".join(statement.split()))

    def __enter__(self) -> StatementLog:
        self.statements = []
        sa.event.listen(self._engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        sa.event.remove(self._engine, "before_cursor_execute", self._record)

    @property
    def count(self) -> int:
        return len(self.statements)

    def reading(self, table: str) -> int:
        pattern = re.compile(rf"\b(?:FROM|JOIN)\s+{re.escape(table)}\b", re.IGNORECASE)
        return sum(1 for statement in self.statements if pattern.search(statement))


@pytest.fixture
def statements(database: Database) -> StatementLog:
    return StatementLog(database.engine)


async def seed_path(client: AsyncClient) -> None:
    await ingest_storable_scenario(client, "03-nested-group-grant")


async def request_cost(
    client: AsyncClient, statements: StatementLog
) -> tuple[int, dict[str, Any]]:
    with statements:
        response = await client.get(shortest_url())
    assert response.status_code == 200, response.text
    return statements.count, response.json()


class TestShortestPathApi:
    async def test_projected_nested_membership_is_two_real_hops(
        self, client: AsyncClient
    ) -> None:
        await seed_path(client)
        response = await client.get(shortest_url())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["found"] is True
        assert body["conclusion"] == "path_found"
        assert body["path"]["hop_count"] == 2
        assert [edge["relationship_type"] for edge in body["path"]["edges"]] == [
            RelationshipType.MEMBER_OF.value,
            RelationshipType.MEMBER_OF.value,
        ]
        assert body["path"]["state"] == "supported"
        assert body["completeness"]["complete_within_supported_graph"] is True
        # GRAPH-01B does not collect trust/GPO/AD-object-control families. A successful
        # supported path is therefore explainable but never proof of full-enterprise coverage.
        assert body["completeness"]["complete_for_enterprise"] is False
        assert "relationship_families_not_collected" in body["completeness"]["reasons"]

    async def test_limits_are_server_enforced(self, client: AsyncClient) -> None:
        await seed_path(client)
        response = await client.get(f"{shortest_url()}&max_depth=129")
        assert response.status_code == 422

    async def test_unknown_well_formed_target_is_a_bounded_no_path_not_name_guessing(
        self, client: AsyncClient
    ) -> None:
        await seed_path(client)
        response = await client.get(
            "/api/v1/graph/traversal/shortest"
            f"?source_kind=principal&source_key={ALICE}"
            "&target_kind=principal&target_key=S-1-5-21-999-888-777-1001"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["found"] is False
        assert body["conclusion"] == "no_path_within_supported_graph"
        assert body["target"]["key"] == "S-1-5-21-999-888-777-1001"


class TestAuthorization:
    async def test_anonymous_caller_cannot_read_privilege_paths(
        self, client: AsyncClient, anonymous_client: AsyncClient
    ) -> None:
        await seed_path(client)
        response = await anonymous_client.get(shortest_url())
        assert response.status_code == 401

    async def test_reserved_role_with_no_access_capability_is_forbidden(
        self,
        client: AsyncClient,
        client_as: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        await seed_path(client)
        async with client_as(Role.REMEDIATOR) as restricted:
            response = await restricted.get(shortest_url())
        assert response.status_code == 403

    async def test_viewer_access_read_capability_may_read_paths(
        self,
        client: AsyncClient,
        client_as: Callable[..., AbstractAsyncContextManager[AsyncClient]],
    ) -> None:
        await seed_path(client)
        async with client_as(Role.VIEWER) as viewer:
            response = await viewer.get(shortest_url())
        assert response.status_code == 200


class TestBoundedDatabaseCost:
    async def test_shortest_path_statement_count_does_not_follow_unrelated_estate_growth(
        self, client: AsyncClient, statements: StatementLog
    ) -> None:
        await seed_path(client)
        before_count, before = await request_cost(client, statements)

        # Add a materially different set of projected relationships that is not on the ALICE
        # path. The traversal should still batch by the frontier it explores, not scan/query
        # once per relationship in the estate.
        await ingest_storable_scenario(client, "09-broken-inheritance")
        after_count, after = await request_cost(client, statements)

        assert after_count == before_count, (
            f"shortest-path request grew from {before_count} to {after_count} SQL statements "
            "after unrelated graph growth"
        )
        assert after["path"] == before["path"]
        assert statements.reading("graph_relationships") <= 4


class TestOtherGraphPrimitives:
    async def test_reverse_reachability_returns_the_nested_source(
        self, client: AsyncClient
    ) -> None:
        await seed_path(client)
        response = await client.get(
            "/api/v1/graph/traversal/reverse"
            f"?target_kind=principal&target_key={FINANCE_RW}&max_depth=4"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        reached = {item["source"]["key"]: item for item in body["reached"]}
        assert ALICE in reached
        assert reached[ALICE]["path"]["hop_count"] == 2

    async def test_alternates_are_bounded_and_neighbor_api_can_show_graph(
        self, client: AsyncClient
    ) -> None:
        await seed_path(client)
        alternates = await client.get(
            "/api/v1/graph/traversal/alternates"
            f"?source_kind=principal&source_key={ALICE}"
            f"&target_kind=principal&target_key={FINANCE_RW}&max_paths=2"
        )
        assert alternates.status_code == 200, alternates.text
        assert alternates.json()["path_count"] == 1

        neighbors = await client.get(
            "/api/v1/graph/traversal/neighbors"
            f"?root_kind=principal&root_key={ALICE}&max_depth=1"
        )
        assert neighbors.status_code == 200, neighbors.text
        body = neighbors.json()
        assert any(item["relationship_type"] == "member_of" for item in body["relationships"])
