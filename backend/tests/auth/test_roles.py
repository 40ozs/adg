"""The role table, tested as a table.

These assertions are deliberately literal. A test that recomputed the expected capability
set from ``ROLE_CAPABILITIES`` would agree with any table, including a wrong one; writing
the sets out means a change to who can do what has to be made twice, on purpose.
"""

from __future__ import annotations

import pytest

from app.auth.roles import (
    ACTIVE_ROLES,
    RESERVED_CAPABILITIES,
    RESERVED_ROLES,
    ROLE_CAPABILITIES,
    Capability,
    Role,
    capabilities_for,
    parse_roles,
)


class TestWhatEachRoleGrants:
    def test_a_viewer_reads_the_estate_and_nothing_else(self) -> None:
        assert capabilities_for(frozenset({Role.VIEWER})) == frozenset(
            {
                Capability.RESOURCES_READ,
                Capability.IDENTITIES_READ,
                Capability.ACCESS_READ,
                Capability.RISKS_READ,
                Capability.CHANGES_READ,
                Capability.COLLECTORS_READ,
                Capability.SEARCH,
            }
        )

    def test_a_viewer_can_see_whether_collection_succeeded(self) -> None:
        """Not a convenience. Without it, a viewer cannot tell an empty estate from a
        failed scan, which is the misreading this product exists to prevent."""
        assert Capability.COLLECTORS_READ in capabilities_for(frozenset({Role.VIEWER}))

    def test_an_auditor_adds_reading_configuration(self) -> None:
        auditor = capabilities_for(frozenset({Role.AUDITOR}))
        viewer = capabilities_for(frozenset({Role.VIEWER}))

        assert auditor - viewer == {Capability.SETTINGS_READ}

    def test_an_admin_adds_writing_configuration_and_ingestion(self) -> None:
        admin = capabilities_for(frozenset({Role.ADMIN}))
        auditor = capabilities_for(frozenset({Role.AUDITOR}))

        assert admin - auditor == {Capability.SETTINGS_WRITE, Capability.COLLECTORS_INGEST}

    def test_no_role_may_write_the_estate(self) -> None:
        """ADG is read-only by default (SECURITY.md). Remediation is not a capability any
        role holds, and this is the test that fails the day somebody grants it by
        accident."""
        for role in Role:
            assert Capability.REMEDIATION_EXECUTE not in capabilities_for(frozenset({role}))

    def test_roles_combine_by_union(self) -> None:
        combined = capabilities_for(frozenset({Role.VIEWER, Role.ADMIN}))

        assert combined == capabilities_for(frozenset({Role.ADMIN}))


class TestTheReservedRole:
    def test_remediator_is_reserved_and_grants_nothing(self) -> None:
        assert Role.REMEDIATOR in RESERVED_ROLES
        assert capabilities_for(frozenset({Role.REMEDIATOR})) == frozenset()

    def test_holding_only_the_reserved_role_is_indistinguishable_from_holding_none(
        self,
    ) -> None:
        assert capabilities_for(frozenset({Role.REMEDIATOR})) == capabilities_for(frozenset())

    def test_the_reserved_role_does_not_subtract_from_an_active_one(self) -> None:
        """A user who is given remediator *as well as* viewer must still be a viewer."""
        assert capabilities_for(frozenset({Role.VIEWER, Role.REMEDIATOR})) == capabilities_for(
            frozenset({Role.VIEWER})
        )

    def test_the_reserved_capability_is_unreachable_from_any_role(self) -> None:
        reachable = frozenset().union(*ROLE_CAPABILITIES.values())

        assert not (reachable & RESERVED_CAPABILITIES)

    def test_active_roles_is_every_role_that_is_not_reserved(self) -> None:
        assert frozenset({Role.VIEWER, Role.AUDITOR, Role.ADMIN}) == ACTIVE_ROLES


class TestParsingRolesFromATenant:
    def test_it_accepts_the_values_adg_publishes(self) -> None:
        roles, unknown = parse_roles(["viewer", "admin"])

        assert roles == frozenset({Role.VIEWER, Role.ADMIN})
        assert unknown == ()

    @pytest.mark.parametrize("written", ["Viewer", "VIEWER", "  viewer  "])
    def test_it_tolerates_the_casing_and_spacing_a_human_types(self, written: str) -> None:
        roles, unknown = parse_roles([written])

        assert roles == frozenset({Role.VIEWER})
        assert unknown == ()

    def test_an_unknown_value_grants_nothing_and_is_reported(self) -> None:
        """Reported rather than dropped: 'I assigned Auditors and they see nothing' has to
        be answerable from a log line."""
        roles, unknown = parse_roles(["Auditors", "viewer"])

        assert roles == frozenset({Role.VIEWER})
        assert unknown == ("Auditors",)

    def test_an_unknown_value_is_reported_once(self) -> None:
        _, unknown = parse_roles(["Auditors", "Auditors"])

        assert unknown == ("Auditors",)

    def test_empty_values_are_ignored_rather_than_reported(self) -> None:
        roles, unknown = parse_roles(["", "   ", "viewer"])

        assert roles == frozenset({Role.VIEWER})
        assert unknown == ()

    def test_a_capability_string_is_not_a_role(self) -> None:
        """Capabilities and roles are different vocabularies; a token naming one must not
        satisfy the other."""
        roles, unknown = parse_roles(["resources:read"])

        assert roles == frozenset()
        assert unknown == ("resources:read",)
