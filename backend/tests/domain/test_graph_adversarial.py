"""Traversal against the adversarial transcripts.

Each test here replays one of the `tests/fixtures/ad_graph/` transcripts through the real
ingestion planner into an in-memory repository, then asks the real
:class:`app.services.graph.GraphService` the question the fixture's `expectations` block
says it is for. Keeping the two together means an expectation cannot quietly stop being
checked: `tests/contracts/test_ad_graph_fixtures.py` proves every key named in an
expectation exists, and these prove the answers match.

The database tests under `tests/db/` ask PostgreSQL the same questions. These run
everywhere and are where a failure is diagnosed; those prove the storage layer agrees.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain import Direction, TraversalLimits, TruncationReason
from app.services.graph import EffectiveMembership, GraphService, MemberInclusion
from tests.fixtures import load_ad_graph
from tests.support.graph import repository_from_fixture


def service(name: str) -> GraphService:
    return GraphService(repository_from_fixture(name))  # type: ignore[arg-type]


def expectations(name: str) -> dict[str, Any]:
    """The fixture's own statement of what the answer must be."""
    document: dict[str, Any] = load_ad_graph(name).expectations
    return document


def keys_of(
    membership: EffectiveMembership, inclusion: MemberInclusion = MemberInclusion.NON_GROUPS
) -> list[str]:
    """The keys an answer keeps under one `include=` filter, sorted for comparison."""
    return sorted(node.key for node in membership.nodes if node.matches(inclusion))


class TestDeepNesting:
    NAME = "a01-deep-nesting"

    async def test_the_user_is_reached_at_the_full_nesting_depth(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        user = next(node for node in result.nodes if node.key == expected["bottom_user_key"])
        assert user.depth == expected["user_depth_from_top"]
        assert result.complete is expected["complete_at_default_depth"]

    async def test_the_explanation_is_the_whole_chain(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        user = next(node for node in result.nodes if node.key == expected["bottom_user_key"])
        assert user.path[0] == expected["top_group_key"]
        assert user.path[-1] == expected["bottom_user_key"]
        assert len(user.path) == int(str(expected["nesting_depth"])) + 1
        assert len(set(user.path)) == len(user.path), "a shortest path never repeats a node"

    async def test_a_depth_limit_below_the_nesting_reports_a_lower_bound(self) -> None:
        expected = expectations(self.NAME)
        limit = int(str(expected["truncates_at_max_depth"]))

        result = await service(self.NAME).effective_members(
            str(expected["top_group_key"]), TraversalLimits(max_depth=limit)
        )

        assert not result.complete
        assert TruncationReason.MAX_DEPTH in result.truncation
        assert expected["bottom_user_key"] not in {node.key for node in result.nodes}
        assert result.depth_reached == limit

    async def test_the_default_limits_answer_a_twenty_four_deep_directory_completely(self) -> None:
        # The stated design claim is that the defaults are generous against a real
        # directory. Twenty-four levels is already far past anything sane.
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        assert result.complete
        assert result.truncation == ()

    async def test_the_chain_is_walked_upward_to_the_same_answer(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_groups(str(expected["bottom_user_key"]))

        assert result.complete
        assert result.direction is Direction.UP
        assert len(result.nodes) == expected["nesting_depth"]


class TestCycles:
    NAME = "a02-cycles"

    async def test_expansion_terminates_and_reports_itself_complete(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        assert result.complete is expected["expansion_is_complete"]
        assert result.truncation == ()

    async def test_both_cycles_are_reported_as_components(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        assert len(result.cycles) == expected["cycle_count"]
        assert [list(cycle.members) for cycle in result.cycles] == expected["cycle_members"]

    async def test_every_cycle_carries_a_loop_an_operator_could_break(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        for cycle in result.cycles:
            loop = cycle.representative_path
            assert loop[0] == loop[-1], "a loop must close"
            assert len(loop) == len(cycle.members) + 1
            assert set(loop) == set(cycle.members)

    async def test_the_user_beyond_both_cycles_is_still_reached(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        assert keys_of(result) == expected["effective_non_group_members"]

    async def test_a_cycle_does_not_multiply_the_paths_out_of_it(self) -> None:
        expected = expectations(self.NAME)

        search = await service(self.NAME).membership_paths(
            str(expected["user_key"]), str(expected["top_group_key"])
        )

        assert search.is_member
        for path in search.paths:
            assert len(set(path.nodes)) == len(path.nodes), "every path must be simple"


class TestDuplicateNames:
    NAME = "a03-duplicate-names"

    async def test_principals_sharing_a_name_keep_separate_keys(self) -> None:
        repository = repository_from_fixture(self.NAME)
        expected = expectations(self.NAME)

        assert sorted(repository.principals) == expected["distinct_principal_keys"]

    async def test_the_shared_display_name_really_is_shared(self) -> None:
        repository = repository_from_fixture(self.NAME)
        expected = expectations(self.NAME)

        sharing = [
            key
            for key, record in repository.principals.items()
            if record.display_name == expected["shared_display_name"]
        ]

        assert len(sharing) == 3, "the fixture must actually contain the collision it pins"

    async def test_each_finance_group_returns_only_its_own_members(self) -> None:
        expected = expectations(self.NAME)
        repository = repository_from_fixture(self.NAME)
        graph = GraphService(repository)  # type: ignore[arg-type]

        groups = [
            key for key, record in repository.principals.items() if record.display_name == "Finance"
        ]
        assert len(groups) == 2, "two groups named Finance is the point of this fixture"

        answers = {key: keys_of(await graph.effective_members(key)) for key in sorted(groups)}
        assert sorted(answers.values()) == sorted(
            [
                list(expected["corp_finance_effective_members"]),
                list(expected["partner_finance_effective_members"]),
            ]
        )


class TestRename:
    BEFORE = "a04a-rename-before"
    AFTER = "a04b-rename-after"

    def test_the_sid_keyed_identity_is_unchanged_by_the_rename(self) -> None:
        before = expectations(self.BEFORE)
        after = expectations(self.AFTER)

        assert before["subject_key"] == after["subject_key"]
        assert before["group_key"] == after["group_key"]
        assert before["edge_key"] == after["edge_key"]

    def test_every_name_field_really_did_change(self) -> None:
        before = expectations(self.BEFORE)
        after = expectations(self.AFTER)

        for field in ("display_name", "sam_account_name", "distinguished_name"):
            assert before[field] != after[field], f"{field} must differ for this to be a rename"

    async def test_the_membership_survives_the_rename(self) -> None:
        after = expectations(self.AFTER)

        result = await service(self.AFTER).effective_members(str(after["group_key"]))

        assert [node.key for node in result.nodes] == [after["subject_key"]]

    async def test_the_new_name_labels_the_same_key(self) -> None:
        after = expectations(self.AFTER)

        result = await service(self.AFTER).effective_members(str(after["group_key"]))

        node = result.nodes[0]
        assert node.principal is not None
        assert node.principal.display_name == after["display_name"]
        assert node.key == after["subject_key"]


class TestUnresolvedAndDeleted:
    NAME = "a05-unresolved-and-deleted"

    async def test_every_member_is_returned_however_little_is_known_about_it(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["group_key"]))

        assert len(keys_of(result)) == expected["effective_member_count"]

    async def test_a_member_nothing_describes_is_kept_and_labelled_unknown(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["group_key"]))

        node = next(node for node in result.nodes if node.key == expected["undescribed_member_key"])
        assert node.resolved is expected["undescribed_member_is_resolved"]
        assert node.kind is expected["undescribed_member_kind"]
        assert node.is_group is None, "unsure must never be reported as 'not a group'"

    async def test_an_undescribed_member_still_carries_its_sid_and_explanation(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["group_key"]))

        node = next(node for node in result.nodes if node.key == expected["undescribed_member_key"])
        assert node.sid == expected["undescribed_member_key"]
        assert node.host_key is None
        assert node.path == (expected["group_key"], expected["undescribed_member_key"])

    async def test_unresolved_principals_are_members_like_any_other(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["group_key"]))

        reached = {node.key for node in result.nodes}
        assert set(expected["unresolved_keys"]) <= reached

    async def test_a_deleted_account_is_still_reported(self) -> None:
        # An account flagged deleted still holds whatever its SID was granted, and its SID
        # can still sit on an ACL. Filtering it out would understate access.
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["group_key"]))

        node = next(node for node in result.nodes if node.key == expected["deleted_account_key"])
        assert node.principal is not None
        assert node.principal.is_deleted is True

    async def test_users_only_drops_everything_unlabelled(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["group_key"]))

        assert keys_of(result, MemberInclusion.USERS) == [expected["deleted_account_key"]]


class TestForeignSecurityPrincipals:
    NAME = "a06-foreign-security-principals"

    async def test_trust_crossing_is_flagged_on_exactly_the_hops_that_cross(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        crossed = sorted(node.key for node in result.nodes if node.via_foreign_security_principal)
        assert crossed == expected["keys_reached_across_the_trust"]

    async def test_local_members_are_not_flagged_as_foreign(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        local = sorted(node.key for node in result.nodes if not node.via_foreign_security_principal)
        assert local == expected["keys_reached_without_crossing_the_trust"]

    async def test_a_foreign_group_is_a_leaf_rather_than_an_invented_membership(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["foreign_group_key"]))

        assert result.nodes == ()
        assert result.complete, "nothing is known, and that is a complete answer"

    async def test_a_foreign_principal_is_not_filtered_out_as_a_non_user(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["top_group_key"]))

        assert expected["foreign_user_key"] in keys_of(result)


class TestDisabledEmptyAndDistribution:
    NAME = "a07-disabled-empty-distribution"

    async def test_a_disabled_account_is_still_an_effective_member(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["helpdesk_key"]))

        assert keys_of(result) == expected["helpdesk_effective_members"]

    async def test_the_disabled_flag_travels_with_the_member(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["helpdesk_key"]))

        node = next(node for node in result.nodes if node.key == expected["disabled_user_key"])
        assert node.principal is not None
        assert node.principal.enabled is False

    async def test_an_unreported_enabled_state_is_null_and_not_false(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["helpdesk_key"]))

        node = next(
            node for node in result.nodes if node.key == expected["unknown_enabled_state_key"]
        )
        assert node.principal is not None
        assert node.principal.enabled is None

    async def test_an_empty_group_expands_to_nothing_and_is_complete(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["empty_group_key"]))

        assert result.nodes == ()
        assert result.complete
        assert result.cycles == ()
        assert result.depth_reached == 0

    async def test_a_group_of_only_groups_has_no_non_group_members(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["wrapper_group_key"]))

        assert keys_of(result) == expected["wrapper_effective_members_non_groups"]
        assert keys_of(result, MemberInclusion.ALL) == expected["wrapper_effective_members_all"]

    async def test_a_distribution_group_still_has_real_membership(self) -> None:
        # It grants nothing on an ACL, but the edge exists and hiding it here would make
        # the group look empty to anybody investigating it.
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["distribution_group_key"]))

        assert keys_of(result) == [expected["active_user_key"]]


class TestLargeGroup:
    NAME = "a08-large-group"

    async def test_every_direct_member_is_returned(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(
            str(expected["group_key"]), TraversalLimits(max_depth=4)
        )

        assert len(keys_of(result, MemberInclusion.ALL)) == expected["effective_member_count_all"]
        assert result.complete

    async def test_the_filters_keep_what_they_say_they_keep(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["group_key"]))

        assert (
            len(keys_of(result, MemberInclusion.NON_GROUPS))
            == expected["effective_member_count_non_groups"]
        )
        assert (
            len(keys_of(result, MemberInclusion.USERS)) == expected["effective_member_count_users"]
        )

    async def test_users_only_would_hide_most_of_the_membership(self) -> None:
        # This is why include=users is not the default: it drops every member ADG has not
        # described, and those are exactly the ones worth looking at.
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected["group_key"]))

        kept = len(keys_of(result, MemberInclusion.USERS))
        dropped = len(keys_of(result, MemberInclusion.NON_GROUPS)) - kept
        assert dropped == expected["undescribed_member_count"] + 1  # the computer as well

    async def test_a_wide_level_costs_one_query_not_one_per_member(self) -> None:
        expected = expectations(self.NAME)
        repository = repository_from_fixture(self.NAME)

        await GraphService(repository).effective_members(  # type: ignore[arg-type]
            str(expected["group_key"]), TraversalLimits(max_depth=4)
        )

        # One call for the root, one for the 512-member frontier, one for the nested
        # group's own members. Not 513.
        assert repository.calls == 3

    async def test_a_node_limit_turns_the_answer_into_a_declared_lower_bound(self) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(
            str(expected["group_key"]), TraversalLimits(max_nodes=100)
        )

        assert len(result.nodes) == 100
        assert not result.complete
        assert TruncationReason.MAX_NODES in result.truncation


class TestMultiplePaths:
    NAME = "a09-multiple-paths"

    async def test_every_route_is_enumerated(self) -> None:
        expected = expectations(self.NAME)

        search = await service(self.NAME).membership_paths(
            str(expected["subject_key"]), str(expected["target_group_key"])
        )

        assert len(search.paths) == expected["path_count"]
        assert [list(path.nodes) for path in search.paths] == expected["paths"]

    async def test_paths_are_ordered_shortest_first(self) -> None:
        expected = expectations(self.NAME)

        search = await service(self.NAME).membership_paths(
            str(expected["subject_key"]), str(expected["target_group_key"])
        )

        lengths = [path.length for path in search.paths]
        assert lengths == sorted(lengths)
        assert list(search.paths[0].nodes) == expected["shortest_path"]
        assert list(search.paths[-1].nodes) == expected["longest_path"]

    async def test_the_primary_group_route_is_present_and_labelled(self) -> None:
        expected = expectations(self.NAME)

        search = await service(self.NAME).membership_paths(
            str(expected["subject_key"]), str(expected["target_group_key"])
        )

        primary = next(
            path for path in search.paths if list(path.nodes) == expected["primary_group_path"]
        )
        assert primary.edge_kinds[0].value == "primary_group"

    async def test_removing_one_route_would_not_remove_the_access(self) -> None:
        expected = expectations(self.NAME)

        search = await service(self.NAME).membership_paths(
            str(expected["subject_key"]), str(expected["target_group_key"])
        )

        first_hops = {path.edges[0].edge_key for path in search.paths}
        assert len(first_hops) == expected["path_count"], (
            "four routes that share no first hop: this is the finding an operator needs, "
            "and a count alone would not show it"
        )

    async def test_the_expansion_and_the_path_search_agree_on_membership(self) -> None:
        expected = expectations(self.NAME)
        graph = service(self.NAME)

        members = await graph.effective_members(str(expected["target_group_key"]))
        search = await graph.membership_paths(
            str(expected["subject_key"]), str(expected["target_group_key"])
        )

        assert search.is_member
        assert expected["subject_key"] in {node.key for node in members.nodes}


class TestBuiltinScoping:
    NAME = "a10-builtin-scoping"

    def test_one_builtin_sid_becomes_three_distinct_keys(self) -> None:
        repository = repository_from_fixture(self.NAME)
        expected = expectations(self.NAME)

        builtin_keys = sorted(
            key for key, record in repository.principals.items() if record.sid == "S-1-5-32-544"
        )
        assert builtin_keys == expected["distinct_group_keys"]
        assert len(builtin_keys) == 3

    @pytest.mark.parametrize(
        ("group_field", "members_field"),
        [
            ("fs10_group_key", "fs10_effective_members"),
            ("fs11_group_key", "fs11_effective_members"),
            ("domain_builtin_group_key", "domain_builtin_effective_members"),
        ],
    )
    async def test_each_group_keeps_its_own_members(
        self, group_field: str, members_field: str
    ) -> None:
        expected = expectations(self.NAME)

        result = await service(self.NAME).effective_members(str(expected[group_field]))

        assert keys_of(result, MemberInclusion.ALL) == expected[members_field]

    async def test_the_host_scoped_groups_share_no_member(self) -> None:
        expected = expectations(self.NAME)
        graph = service(self.NAME)

        fs10 = await graph.effective_members(str(expected["fs10_group_key"]))
        fs11 = await graph.effective_members(str(expected["fs11_group_key"]))

        assert not {node.key for node in fs10.nodes} & {node.key for node in fs11.nodes}, (
            "merging BUILTIN\\Administrators across two servers would invent access"
        )

    def test_the_hazard_this_fixture_pins_is_written_down(self) -> None:
        # The domain's own BUILTIN group is not host-scoped, so a second collected domain
        # would merge with it. The fixture records that; the validator detects it.
        assert "known_hazard" in expectations(self.NAME)
