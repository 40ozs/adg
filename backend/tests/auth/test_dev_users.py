"""Parsing the development account list.

Every failure here has to name the offending entry. A development account that silently
fails to appear is a developer spending an afternoon on "why can't I sign in as auditor".
"""

from __future__ import annotations

import pytest

from app.auth.dev_users import (
    DEFAULT_DEV_AUTH_USERS,
    DevelopmentUserError,
    parse_development_users,
)
from app.auth.roles import ACTIVE_ROLES, Role


class TestTheDefaultList:
    def test_it_provides_one_account_per_active_role(self) -> None:
        users = parse_development_users(DEFAULT_DEV_AUTH_USERS)

        assert {user.username for user in users} == {
            "viewer",
            "auditor",
            "admin",
            "reviewer",
            "governance",
            "planner",
            "approver",
        }
        assert {next(iter(user.roles)) for user in users} == ACTIVE_ROLES

    def test_no_default_account_holds_two_roles(self) -> None:
        """One role per account, so a developer sees the separation of duties rather than a
        superuser that hides it. See ADR-0029."""
        for user in parse_development_users(DEFAULT_DEV_AUTH_USERS):
            assert len(user.roles) == 1

    def test_the_reserved_role_gets_no_default_account(self) -> None:
        """Nothing should make it convenient to sign in as a role that grants nothing."""
        users = parse_development_users(DEFAULT_DEV_AUTH_USERS)

        assert all(Role.REMEDIATOR not in user.roles for user in users)

    def test_addresses_are_non_routable(self) -> None:
        for user in parse_development_users(DEFAULT_DEV_AUTH_USERS):
            assert user.email.endswith("@development.invalid")


class TestParsing:
    def test_a_display_name_is_optional(self) -> None:
        (user,) = parse_development_users("dana:viewer")

        assert user.display_name == "dana"

    def test_an_account_may_hold_several_roles(self) -> None:
        (user,) = parse_development_users("dana:viewer|admin:Dana Reed")

        assert user.roles == frozenset({Role.VIEWER, Role.ADMIN})
        assert user.display_name == "Dana Reed"

    def test_user_names_are_case_folded(self) -> None:
        (user,) = parse_development_users("DANA:viewer")

        assert user.username == "dana"

    def test_blank_entries_are_skipped(self) -> None:
        users = parse_development_users("dana:viewer, ,sam:admin")

        assert [user.username for user in users] == ["dana", "sam"]


class TestRefusals:
    def test_an_entry_with_no_role_is_refused(self) -> None:
        with pytest.raises(DevelopmentUserError, match="is not"):
            parse_development_users("dana")

    def test_an_unknown_role_is_refused_and_the_valid_ones_are_listed(self) -> None:
        with pytest.raises(DevelopmentUserError) as caught:
            parse_development_users("dana:superuser")

        message = str(caught.value)
        assert "'superuser'" in message
        assert "viewer" in message and "auditor" in message and "admin" in message

    def test_a_duplicate_account_is_refused(self) -> None:
        with pytest.raises(DevelopmentUserError, match="more than once"):
            parse_development_users("dana:viewer,dana:admin")

    def test_an_empty_user_name_is_refused(self) -> None:
        with pytest.raises(DevelopmentUserError, match="empty user name"):
            parse_development_users(":viewer")

    def test_an_empty_list_is_refused_with_the_default_shown(self) -> None:
        with pytest.raises(DevelopmentUserError) as caught:
            parse_development_users("   ")

        assert DEFAULT_DEV_AUTH_USERS in str(caught.value)

    def test_an_entry_with_too_many_fields_is_refused(self) -> None:
        with pytest.raises(DevelopmentUserError, match="is not"):
            parse_development_users("dana:viewer:Dana Reed:extra")

    def test_a_role_list_that_parses_to_nothing_is_refused(self) -> None:
        with pytest.raises(DevelopmentUserError, match="grants no role"):
            parse_development_users("dana:|")
