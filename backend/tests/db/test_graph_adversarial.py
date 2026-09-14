"""The adversarial transcripts, all the way through PostgreSQL and HTTP.

`tests/domain/test_graph_adversarial.py` asks the same questions of an in-memory
repository. These replay the same transcripts through the real ingestion endpoints and read
the answers back through the real query endpoints, because a hermetic test proves the
algorithm and only this proves the *system*: the storage keys, the check constraints, the
SQL, the pagination cursors, and the JSON a client actually receives.

Where the two suites disagree, the difference is in the storage layer, which is exactly
what these exist to find.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.fixtures import load_ad_graph, load_ad_graph_raw
from tests.support.ingest import replay, with_run_id

API = "/api/v1"


async def ingest(client: AsyncClient, name: str) -> dict[str, Any]:
    """Replay one adversarial transcript verbatim. It is already AD-only."""
    return await replay(client, load_ad_graph_raw(name))


def expectations(name: str) -> dict[str, Any]:
    document: dict[str, Any] = load_ad_graph(name).expectations
    return document


async def effective(client: AsyncClient, key: str, **parameters: Any) -> dict[str, Any]:
    response = await client.get(f"{API}/groups/{key}/effective-members", params=parameters)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def member_keys(body: dict[str, Any]) -> list[str]:
    """Every principal key an items page carries, sorted for comparison."""
    return sorted(item["principal"]["key"] for item in body["items"])


def item_for(body: dict[str, Any], key: str) -> dict[str, Any]:
    found: dict[str, Any] = next(item for item in body["items"] if item["principal"]["key"] == key)
    return found


class TestDeepNesting:
    NAME = "a01-deep-nesting"

    async def test_the_whole_chain_is_walked_and_reported_complete(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["top_group_key"], include="all")

        assert body["traversal"]["complete"] is True
        assert body["traversal"]["depth_reached"] == expected["nesting_depth"]

    async def test_the_user_carries_the_whole_chain_as_its_explanation(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["top_group_key"], limit=500)

        user = item_for(body, expected["bottom_user_key"])
        assert user["depth"] == expected["user_depth_from_top"]
        assert len(user["path"]) == expected["nesting_depth"] + 1
        assert len(user["edge_kinds"]) == expected["nesting_depth"]

    async def test_a_short_depth_limit_declares_itself_a_lower_bound(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(
            client, expected["top_group_key"], max_depth=expected["truncates_at_max_depth"]
        )

        assert body["traversal"]["complete"] is False
        assert "max_depth" in body["traversal"]["truncation"]
        assert expected["bottom_user_key"] not in member_keys(body)


class TestCycles:
    NAME = "a02-cycles"

    async def test_the_query_returns_rather_than_looping(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["top_group_key"])

        assert body["traversal"]["complete"] is True
        assert member_keys(body) == expected["effective_non_group_members"]

    async def test_both_cycles_come_back_in_the_response(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["top_group_key"])

        assert [cycle["members"] for cycle in body["cycles"]] == expected["cycle_members"]
        for cycle in body["cycles"]:
            assert cycle["representative_path"][0] == cycle["representative_path"][-1]

    async def test_a_cycle_is_stored_rather_than_rejected(self, client: AsyncClient) -> None:
        # Both directions of the Ring-A/Ring-B pair must be present as rows: refusing to
        # store an observed edge would hide the anomaly instead of reporting it.
        result = await ingest(client, self.NAME)

        assert result["completion"]["status"] == "succeeded"
        applied = sum(batch["applied"] for batch in result["batches"])
        assert applied == len(load_ad_graph(self.NAME).observations)
        edges = sum(batch["edges_written"] for batch in result["batches"])
        assert edges == len(load_ad_graph(self.NAME).edges)


class TestDuplicateNamesAndRenames:
    async def test_two_principals_with_one_name_stay_two_rows(self, client: AsyncClient) -> None:
        await ingest(client, "a03-duplicate-names")
        expected = expectations("a03-duplicate-names")

        for key in expected["distinct_principal_keys"]:
            response = await client.get(f"{API}/principals/{key}")
            assert response.status_code == 200, f"{key}: {response.text}"
            assert response.json()["key"] == key

    async def test_a_group_never_returns_another_domains_namesake(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, "a03-duplicate-names")
        expected = expectations("a03-duplicate-names")
        corp_finance = expected["distinct_principal_keys"]

        body = await effective(client, next(key for key in corp_finance if key.endswith("-2310")))

        assert member_keys(body) == expected["corp_finance_effective_members"]

    async def test_a_rename_keeps_the_key_the_membership_and_the_old_names(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, "a04a-rename-before")
        await ingest(client, "a04b-rename-after")
        after = expectations("a04b-rename-after")

        response = await client.get(f"{API}/principals/{after['subject_key']}")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["key"] == after["subject_key"]
        assert body["display_name"] == after["display_name"]
        aliases = {alias["value"] for alias in body["aliases"]}
        assert set(after["previous_names"]) <= aliases
        assert len(body["aliases"]) == after["alias_count_after_both_runs"]

    async def test_a_rename_does_not_duplicate_the_edge(self, client: AsyncClient) -> None:
        await ingest(client, "a04a-rename-before")
        await ingest(client, "a04b-rename-after")
        after = expectations("a04b-rename-after")

        response = await client.get(f"{API}/groups/{after['group_key']}/members")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["page"]["total"] == 1
        assert body["items"][0]["principal"]["key"] == after["subject_key"]


class TestUnresolvedAndDeleted:
    NAME = "a05-unresolved-and-deleted"

    async def test_a_member_nothing_describes_is_returned_not_dropped(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["group_key"])

        assert len(body["items"]) == expected["effective_member_count"]
        undescribed = item_for(body, expected["undescribed_member_key"])["principal"]
        assert undescribed["resolved"] is False
        assert undescribed["kind"] is None
        assert undescribed["is_group"] is None

    async def test_the_undescribed_member_is_still_a_known_endpoint(
        self, client: AsyncClient
    ) -> None:
        # It has no principals row, but it demonstrably sits inside a group, so answering
        # "no such principal" would hide a real membership.
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(f"{API}/principals/{expected['undescribed_member_key']}")

        assert response.status_code == 200, response.text
        assert response.json()["resolved"] is False

    async def test_unresolved_principals_keep_their_reason_and_last_known_name(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        deleted = next(key for key in expected["unresolved_keys"] if key.endswith("-2501"))
        response = await client.get(f"{API}/principals/{deleted}")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kind"] == "unresolved"
        assert body["display_name"] is None, "an unresolved SID must never look resolved"

    async def test_a_deleted_account_is_still_a_member(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["group_key"])

        assert expected["deleted_account_key"] in member_keys(body)


class TestForeignSecurityPrincipals:
    NAME = "a06-foreign-security-principals"

    async def test_the_trust_hop_is_flagged_in_the_response(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["top_group_key"], include="all")

        crossed = sorted(
            item["principal"]["key"]
            for item in body["items"]
            if item["via_foreign_security_principal"]
        )
        assert crossed == expected["keys_reached_across_the_trust"]

    async def test_a_foreign_group_has_no_invented_members(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["foreign_group_key"], include="all")

        assert body["items"] == []
        assert body["traversal"]["complete"] is True


class TestDisabledEmptyAndDistribution:
    NAME = "a07-disabled-empty-distribution"

    async def test_a_disabled_account_is_returned_with_its_state(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["helpdesk_key"])

        assert member_keys(body) == expected["helpdesk_effective_members"]
        disabled = item_for(body, expected["disabled_user_key"])["principal"]
        assert disabled["enabled"] is False

    async def test_an_unreported_enabled_state_stays_null(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["helpdesk_key"])

        unknown = item_for(body, expected["unknown_enabled_state_key"])["principal"]
        assert unknown["enabled"] is None

    async def test_an_empty_group_answers_empty_and_complete(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["empty_group_key"], include="all")

        assert body["items"] == []
        assert body["traversal"]["complete"] is True

        direct = await client.get(f"{API}/groups/{expected['empty_group_key']}/members")
        assert direct.status_code == 200
        assert direct.json()["page"]["total"] == 0, "zero members, not an unknown count"

    async def test_the_include_filter_changes_the_answer_as_documented(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        non_groups = await effective(client, expected["wrapper_group_key"])
        everything = await effective(client, expected["wrapper_group_key"], include="all")

        assert member_keys(non_groups) == expected["wrapper_effective_members_non_groups"]
        assert member_keys(everything) == expected["wrapper_effective_members_all"]


class TestLargeGroup:
    NAME = "a08-large-group"

    async def test_every_member_survives_ingestion(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(f"{API}/groups/{expected['group_key']}/members")

        assert response.status_code == 200, response.text
        assert response.json()["page"]["total"] == expected["direct_member_count"]

    async def test_keyset_pagination_covers_the_membership_exactly_once(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            parameters: dict[str, Any] = {"limit": 100}
            if cursor:
                parameters["cursor"] = cursor
            response = await client.get(
                f"{API}/groups/{expected['group_key']}/members", params=parameters
            )
            assert response.status_code == 200, response.text
            body = response.json()
            seen.extend(item["principal"]["key"] for item in body["items"])
            pages += 1
            cursor = body["page"]["next_cursor"]
            if not cursor:
                break
            assert pages <= expected["pages_at_default_page_size"] + 1, "paging did not terminate"

        assert pages == expected["pages_at_default_page_size"]
        assert len(seen) == expected["direct_member_count"]
        assert len(set(seen)) == len(seen), "a repeated member means a skipped one elsewhere"
        assert seen == sorted(seen), "keyset pagination must walk the index in order"

    async def test_the_maximum_page_size_halves_the_work(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(
            f"{API}/groups/{expected['group_key']}/members", params={"limit": 500}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == 500
        assert body["page"]["next_cursor"]

    async def test_the_recursive_answer_counts_what_the_fixture_says(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        everything = await effective(client, expected["group_key"], include="all", limit=1)
        non_groups = await effective(client, expected["group_key"], limit=1)
        users = await effective(client, expected["group_key"], include="users", limit=1)

        assert everything["page"]["total"] == expected["effective_member_count_all"]
        assert non_groups["page"]["total"] == expected["effective_member_count_non_groups"]
        assert users["page"]["total"] == expected["effective_member_count_users"]

    async def test_a_wide_group_does_not_cost_a_query_per_member(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected["group_key"], include="all", limit=1)

        # One request reads each edge once, not once per member expanded.
        assert body["traversal"]["edges_read"] <= expected["direct_member_count"] + 10


class TestMultiplePaths:
    NAME = "a09-multiple-paths"

    async def test_every_route_comes_back_through_the_api(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(
            f"{API}/principals/{expected['subject_key']}/membership-paths",
            params={"group": expected["target_group_key"]},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_member"] is True
        assert body["traversal"]["complete"] is True
        assert [path["nodes"] for path in body["paths"]] == expected["paths"]

    async def test_a_capped_search_says_so_rather_than_looking_definitive(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(
            f"{API}/principals/{expected['subject_key']}/membership-paths",
            params={"group": expected["target_group_key"], "max_paths": 1},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["paths"]) == 1
        assert body["traversal"]["complete"] is False
        assert "max_paths" in body["traversal"]["truncation"]

    async def test_the_primary_group_edge_survived_storage(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(
            f"{API}/principals/{expected['subject_key']}/membership-paths",
            params={"group": expected["target_group_key"]},
        )

        kinds = {kind for path in response.json()["paths"] for kind in path["edge_kinds"]}
        assert "primary_group" in kinds


class TestBuiltinScoping:
    NAME = "a10-builtin-scoping"

    async def test_all_three_groups_are_stored_separately(self, client: AsyncClient) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        for key in expected["distinct_group_keys"]:
            response = await client.get(f"{API}/principals/{key}")
            assert response.status_code == 200, f"{key}: {response.text}"
            assert response.json()["key"] == key

    async def test_a_bare_builtin_sid_is_ambiguous_when_only_hosts_hold_it(
        self, client: AsyncClient
    ) -> None:
        await ingest(client, "a10-builtin-scoping-hosts-only")
        expected = expectations("a10-builtin-scoping-hosts-only")

        response = await client.get(f"{API}/principals/S-1-5-32-544")

        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert len(detail["candidates"]) == expected["bare_sid_candidate_count"]
        assert (
            sorted(item["key"] for item in detail["candidates"])
            == (expected["distinct_group_keys"])
        )

    async def test_an_unscoped_domain_builtin_group_wins_the_bare_sid_outright(
        self, client: AsyncClient
    ) -> None:
        """Pinning a consequence of the hazard, not endorsing it.

        ``S-1-5-32-544`` is both a bare SID and — once a domain reports its own BUILTIN
        group — an exact storage key. The exact key wins, so the caller is handed the
        domain group with no hint that FS10 and FS11 each have a different group with the
        same SID. The ambiguity that would otherwise be a 409 is invisible here.

        Changing this would change what a full storage key means, which is a contract
        decision rather than a validation one. It is recorded as an open finding in
        `docs/architecture/ad-graph-validation.md`, and
        `scripts/validate-collector-output.ps1` warns on the collector output that creates
        the situation.
        """
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(f"{API}/principals/S-1-5-32-544")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["key"] == expected["domain_builtin_group_key"]
        assert body["host_key"] is None
        assert body["direct_member_count"] == 1, (
            "one member — the other two groups' members are correctly not merged in, which "
            "is the part that matters most"
        )

    async def test_a_host_qualified_lookup_resolves_exactly_one_group(
        self, client: AsyncClient
    ) -> None:
        # The regression: the domain's own unscoped BUILTIN group used to be offered as a
        # rival candidate, so ?host= failed in exactly the case it exists for.
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(f"{API}/principals/S-1-5-32-544", params={"host": "FS10"})

        assert response.status_code == 200, response.text
        assert response.json()["key"] == expected["fs10_group_key"]

    @pytest.mark.parametrize(
        ("group_field", "members_field"),
        [
            ("fs10_group_key", "fs10_effective_members"),
            ("fs11_group_key", "fs11_effective_members"),
            ("domain_builtin_group_key", "domain_builtin_effective_members"),
        ],
    )
    async def test_each_group_keeps_its_own_membership(
        self, client: AsyncClient, group_field: str, members_field: str
    ) -> None:
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        body = await effective(client, expected[group_field], include="all")

        assert member_keys(body) == expected[members_field]

    async def test_the_check_constraint_refuses_an_unscoped_local_group(
        self, client: AsyncClient
    ) -> None:
        # Enforced by PostgreSQL, so bypassing the application layer does not bypass it.
        await ingest(client, self.NAME)
        expected = expectations(self.NAME)

        response = await client.get(f"{API}/principals/{expected['fs10_group_key']}")

        assert response.status_code == 200, response.text
        assert response.json()["host_key"] == "fs10"


class TestReingestionIsIdempotent:
    @pytest.mark.parametrize(
        "name",
        ["a02-cycles", "a05-unresolved-and-deleted", "a09-multiple-paths", "a10-builtin-scoping"],
    )
    async def test_replaying_a_transcript_as_a_new_run_changes_no_answer(
        self, client: AsyncClient, name: str
    ) -> None:
        # Storage keys are derived from identity, so a second scan of an unchanged
        # directory must converge on exactly the same graph rather than doubling it.
        await ingest(client, name)
        expected = expectations(name)
        subject = next(
            value
            for field, value in expected.items()
            if field.endswith("group_key") and isinstance(value, str)
        )
        before = await effective(client, subject, include="all", limit=500)

        await replay(client, with_run_id(load_ad_graph_raw(name)))
        after = await effective(client, subject, include="all", limit=500)

        assert member_keys(before) == member_keys(after)
        assert before["page"]["total"] == after["page"]["total"]
