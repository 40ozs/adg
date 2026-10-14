r"""Directory access against a real PostgreSQL: the summary, the tree, exceptions and export.

The estate (:mod:`tests.support.directory_estate`) is replayed through the ingestion API with
every directory's inherited entries stored, as a collector stores them — so the reference
index names the subject's groups on every directory under a grant, and the flat listing is
long in exactly the way the summary exists to fix.

What is asserted:

* **semantics are the engine's** — every directory the summary counts as reachable is one the
  paged ``/resources`` listing reports ``access: true`` for, and vice versa, and both agree
  with the estate's construction rules;
* **the summary is smaller than the listing and loses nothing** — roots plus descendants is
  exactly the reachable set; the ``paths`` export carries every evaluated directory;
* **boundaries surface** — a protected folder, a Deny, a widening grant and resumed access
  each appear where the estate put them;
* **the tree is lazy** — one level per request, collapsed at the share roots, and a second
  request for an opened branch is served from the computation the first one made;
* **cost** — the summary's statements do not grow with the number of directories, and a tree
  expansion after the summary is a basis read.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any
from urllib.parse import quote, urlencode

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.db import Database
from tests.support.directory_estate import (
    FULL,
    MODIFY,
    READ_EXECUTE,
    SUBJECT,
    DirectoryEstate,
    build_directory_estate,
)
from tests.support.ingest import replay

pytestmark = pytest.mark.anyio

BASE = f"/api/v1/access/principals/{quote(SUBJECT, safe='')}"


def url(suffix: str = "", **query: Any) -> str:
    rendered = urlencode({key: value for key, value in query.items() if value is not None})
    return f"{BASE}/directory-access{suffix}" + (f"?{rendered}" if rendered else "")


class StatementLog:
    """Every statement the engine issues while the block is open (as in the cost suites)."""

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


@pytest.fixture
async def estate(client: AsyncClient) -> DirectoryEstate:
    built = build_directory_estate(shares=2, per_share=80)
    await replay(client, built.document)
    return built


async def every_listing_row(client: AsyncClient) -> list[dict[str, Any]]:
    """The paged flat listing, walked to the end — what All paths shows."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = {"limit": 500, **({"cursor": cursor} if cursor else {})}
        response = await client.get(f"{BASE}/resources?{urlencode(query)}")
        assert response.status_code == 200, response.text
        body = response.json()
        rows.extend(body["items"])
        cursor = body["page"]["next_cursor"]
        if cursor is None:
            return rows


async def summary(client: AsyncClient, **query: Any) -> dict[str, Any]:
    response = await client.get(url(**query))
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def tree(client: AsyncClient, parent: str | None = None) -> dict[str, Any]:
    response = await client.get(url("/tree", parent=parent))
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def all_exceptions(client: AsyncClient, root: str) -> list[dict[str, Any]]:
    response = await client.get(url("/exceptions", root=root, limit=500))
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


# ------------------------------------------------------------------- semantics


class TestTheSummaryReadsTheEngine:
    async def test_reachable_directories_are_exactly_the_listings_and_the_estates(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        listing = await every_listing_row(client)
        body = await summary(client, limit=500)

        reachable_in_listing = {row["resource"]["key"] for row in listing if row["access"]}
        assert reachable_in_listing == set(estate.expected)
        assert body["totals"]["reachable_directories"] == len(reachable_in_listing)
        assert sum(1 + root["reachable_descendants"] for root in body["roots"]) == len(
            reachable_in_listing
        )
        assert body["totals"]["candidates_evaluated"] == len(listing)
        assert body["complete"] is True

    async def test_each_root_carries_the_engines_mask_for_it(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, limit=500)

        for root in body["roots"]:
            assert root["verdict"]["rights"]["value"] == estate.expected[root["directory"]["key"]]
            assert root["verdict"]["access"] is True

    async def test_the_summary_is_much_smaller_than_the_listing(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, limit=500)

        assert len(body["roots"]) * 4 < body["totals"]["reachable_directories"]
        assert body["matched"] == body["totals"]["roots"] == len(body["roots"])

    async def test_the_top_level_team_grants_are_roots_below_a_share_root_without_access(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, limit=500)
        roots = {root["directory"]["key"]: root for root in body["roots"]}

        for share in estate.shares:
            for team in ("Team00", "Team01", "Team02"):
                root = roots[f"{share}\\{team}".casefold()]
                assert root["starts_because"] == "no_access"
                assert root["verdict"]["rights"]["value"] == MODIFY
            assert f"{share}\\Team03".casefold() not in roots


class TestBoundaries:
    async def test_a_protected_folder_without_the_subject_ends_access_and_is_not_counted(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        assert estate.protected_without_subject, "the estate must contain one"
        body = await summary(client, limit=500)
        exceptions = {
            item["directory"]["key"]: item
            for root in body["roots"]
            for item in await all_exceptions(client, root["directory"]["key"])
        }

        for key in estate.protected_without_subject:
            if key not in exceptions:
                continue  # below another boundary; asserted not reachable either way
            item = exceptions[key]
            assert item["reachable"] is False
            assert item["named"] is False  # nothing on its ACL names the subject
            # ...and the engine was still asked: its verdict, not the summary's assumption.
            assert item["verdict"]["outcome"] == "no_grant"
            assert [reason["reason"] for reason in item["reasons"]] == ["access_ends"]
        reachable_keys = set(estate.expected)
        assert not reachable_keys & estate.protected_without_subject

    async def test_a_deny_is_named_as_a_denial(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, limit=500)
        denied = [
            item
            for root in body["roots"]
            for item in await all_exceptions(client, root["directory"]["key"])
            if item["reasons"][0]["reason"] == "access_denied"
        ]

        assert denied, "the estate writes Deny entries inside reachable runs"
        assert all(item["directory"]["key"] in estate.denied for item in denied)
        assert all(item["verdict"]["outcome"] == "denied" for item in denied)

    async def test_widened_rights_are_an_exception_and_the_root_is_not_uniform(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, limit=500)
        widened = [
            (root, item)
            for root in body["roots"]
            for item in await all_exceptions(client, root["directory"]["key"])
            if any(reason["reason"] == "rights_widen" for reason in item["reasons"])
        ]

        assert widened, "the estate writes explicit Full control inside reachable runs"
        for root, item in widened:
            assert item["verdict"]["rights"]["value"] == FULL
            assert root["uniform"] is False
            assert len(root["rights_breakdown"]) >= 2

    async def test_resumed_access_is_its_own_root_linked_to_the_run_it_resumes_in(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, limit=500)
        resumed = [root for root in body["roots"] if root["resumes_within"] is not None]

        assert resumed, "the estate re-grants read access below locked folders"
        keys = {root["directory"]["key"] for root in body["roots"]}
        for root in resumed:
            assert root["verdict"]["rights"]["value"] == READ_EXECUTE
            assert root["resumes_within"] in keys
            assert root["resumes_below"] in estate.protected_without_subject


# ------------------------------------------------------------------- filters


class TestFilters:
    async def test_a_text_filter_applies_across_every_root_before_paging(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        everything = await summary(client, limit=500)
        share = estate.shares[1]
        body = await summary(client, q=share.split("\\")[-1].lower(), limit=2)

        prefix = share.casefold()
        expected = [
            root for root in everything["roots"] if root["directory"]["key"].startswith(prefix)
        ]
        assert body["matched"] == len(expected)
        assert len(body["roots"]) == 2
        assert body["page"]["has_more"] is (len(expected) > 2)

    async def test_an_access_filter_keeps_roots_with_that_label(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        everything = await summary(client, limit=500)
        assert everything["access_levels"][0] == "Modify"
        (label,) = {
            root["verdict"]["rights"]["primary"]
            for root in everything["roots"]
            if root["verdict"]["rights"]["value"] == READ_EXECUTE
        }
        assert label in everything["access_levels"]
        body = await summary(client, access=label, limit=500)

        assert body["roots"]
        assert all(root["verdict"]["rights"]["value"] == READ_EXECUTE for root in body["roots"])

    async def test_a_share_filter_takes_a_share_key(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, share="fs01|dept00", limit=500)

        assert body["roots"]
        assert {root["directory"]["share_key"] for root in body["roots"]} == {"fs01|dept00"}


# ------------------------------------------------------------------- tree


class TestTree:
    async def test_the_top_level_is_the_share_roots_collapsed(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await tree(client)

        assert [node["directory"]["key"] for node in body["children"]] == [
            share.casefold() for share in estate.shares
        ]
        assert body["parent"] is None
        for node in body["children"]:
            assert node["reachable"] is False
            assert node["reachable_below"] > 0
            assert node["child_count"] == 3  # the three teams; Team03 leads nowhere
            assert node["hidden_child_count"] == 1

    async def test_expanding_returns_one_level_only(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        share = estate.shares[0]
        body = await tree(client, share)

        assert body["parent"]["directory"]["key"] == share.casefold()
        depths = {node["directory"]["key"].count("\\") for node in body["children"]}
        assert depths == {share.count("\\") + 1}
        team = body["children"][0]
        assert team["is_root"] is True and team["reachable"] is True

        nested = await tree(client, team["directory"]["path"])
        assert len(nested["children"]) == team["child_count"]
        depth = share.count("\\") + 2
        assert all(node["directory"]["key"].count("\\") == depth for node in nested["children"])

    async def test_a_directory_the_tree_does_not_offer_is_refused(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        response = await client.get(url("/tree", parent=f"{estate.shares[0]}\\Team03"))

        assert response.status_code == 422

    async def test_the_parent_is_addressed_case_insensitively(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        upper = await tree(client, estate.shares[0].upper())
        lower = await tree(client, estate.shares[0].lower() + "\\")

        assert upper["children"] == lower["children"]


# ------------------------------------------------------------------- exceptions


class TestExceptions:
    async def test_a_root_without_exceptions_lists_none_and_is_uniform(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, limit=500)
        for root in body["roots"]:
            items = await all_exceptions(client, root["directory"]["key"])
            assert len(items) == root["exception_count"]
            assert root["uniform"] is (root["exception_count"] == 0)

    async def test_something_other_than_a_root_is_refused(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        response = await client.get(url("/exceptions", root=estate.shares[0]))

        assert response.status_code == 422


# ------------------------------------------------------------------- export


class TestExport:
    async def test_the_paths_export_carries_every_evaluated_directory(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        listing = await every_listing_row(client)
        response = await client.get(url("/export", content="paths"))

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["x-adg-complete"] == "true"
        assert "attachment" in response.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert {row["path"].casefold() for row in rows} == {
            item["resource"]["key"] for item in listing
        }
        reachable = {row["path"].casefold() for row in rows if row["reachable"] == "yes"}
        assert reachable == set(estate.expected)

    async def test_the_summary_export_has_one_row_per_root(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        body = await summary(client, limit=500)
        response = await client.get(url("/export", content="summary"))

        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert [row["root_path"].casefold() for row in rows] == [
            root["directory"]["key"] for root in body["roots"]
        ]
        assert sum(int(row["reachable_descendants"]) + 1 for row in rows) == len(estate.expected)


# ------------------------------------------------------------------- caching and cost


class TestCost:
    async def test_a_tree_expansion_after_the_summary_reuses_its_computation(
        self, client: AsyncClient, statements: StatementLog, estate: DirectoryEstate
    ) -> None:
        await summary(client)
        with statements:
            await tree(client, estate.shares[0])

        assert statements.reading("ntfs_resources") == 0
        assert statements.reading("ntfs_aces") == 0
        assert statements.count <= 3  # the basis read, plus the session's own bookkeeping

    async def test_a_conditional_request_is_answered_304(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        first = await client.get(url())
        again = await client.get(url(), headers={"If-None-Match": first.headers["etag"]})
        other = await client.get(url(q="team"), headers={"If-None-Match": first.headers["etag"]})

        assert again.status_code == 304
        assert other.status_code == 200  # a different question carries a different tag

    async def test_an_ingestion_invalidates_the_computation(
        self, client: AsyncClient, estate: DirectoryEstate
    ) -> None:
        before = await summary(client)
        await replay(client, build_directory_estate(shares=3, per_share=80).document)
        after = await summary(client)

        assert before["basis"]["token"] != after["basis"]["token"]
        assert after["totals"]["shares"] == 3

    async def test_the_statement_count_does_not_follow_the_number_of_directories(
        self, client: AsyncClient, statements: StatementLog
    ) -> None:
        small = build_directory_estate(shares=1, per_share=40, seed=3)
        await replay(client, small.document)
        with statements:
            await summary(client)
        small_count = statements.count

        large = build_directory_estate(shares=1, per_share=400, seed=3)
        await replay(client, large.document)
        with statements:
            body = await summary(client)
        large_count = statements.count

        assert body["totals"]["reachable_directories"] > 5 * len(small.expected)
        assert large_count == small_count
