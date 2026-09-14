"""Turning a tenant's token claims into a principal.

The shapes covered here are the ones a Microsoft Entra ID token actually arrives in — app
roles under ``roles``, group object ids under ``groups``, a single string where a list was
expected — because this mapping is the part that cannot be exercised locally against a real
tenant, and therefore the part that has to be exhaustively exercised without one.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.auth.principal import ClaimsMapping, collector_principal, principal_from_claims
from app.auth.roles import Capability, Role

MAPPING = ClaimsMapping()


def build(**claims: object) -> dict[str, object]:
    return {"sub": "00000000-0000-0000-0000-000000000001", **claims}


class TestIdentity:
    def test_the_subject_claim_identifies_the_principal(self) -> None:
        principal = principal_from_claims(build(), mapping=MAPPING, source="oidc")

        assert principal.subject == "00000000-0000-0000-0000-000000000001"

    def test_the_object_id_stands_in_when_sub_is_absent(self) -> None:
        principal = principal_from_claims({"oid": "abc"}, mapping=MAPPING, source="oidc")

        assert principal.subject == "abc"

    def test_a_token_with_no_identity_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no 'sub' claim"):
            principal_from_claims({"roles": ["admin"]}, mapping=MAPPING, source="oidc")

    def test_a_blank_subject_is_no_subject(self) -> None:
        with pytest.raises(ValueError, match="no 'sub' claim"):
            principal_from_claims({"sub": "   "}, mapping=MAPPING, source="oidc")

    def test_the_display_name_prefers_name_over_preferred_username(self) -> None:
        principal = principal_from_claims(
            build(name="Dana Reed", preferred_username="dana@corp.example"),
            mapping=MAPPING,
            source="oidc",
        )

        assert principal.display_name == "Dana Reed"

    def test_the_expiry_is_read_as_an_instant_in_utc(self) -> None:
        principal = principal_from_claims(build(exp=1_800_000_000), mapping=MAPPING, source="oidc")

        assert principal.expires_at == dt.datetime.fromtimestamp(1_800_000_000, tz=dt.UTC)

    def test_a_nonsense_expiry_is_no_expiry_rather_than_a_crash(self) -> None:
        principal = principal_from_claims(build(exp="soon"), mapping=MAPPING, source="oidc")

        assert principal.expires_at is None


class TestAppRoleAssignments:
    def test_roles_in_the_roles_claim_grant_their_capabilities(self) -> None:
        principal = principal_from_claims(build(roles=["auditor"]), mapping=MAPPING, source="oidc")

        assert principal.roles == frozenset({Role.AUDITOR})
        assert principal.can(Capability.SETTINGS_READ)

    def test_a_single_string_is_accepted_where_a_list_was_expected(self) -> None:
        """Identity providers collapse one-element claims to a bare string often enough
        that treating it as absent would lock out every single-role account."""
        principal = principal_from_claims(build(roles="viewer"), mapping=MAPPING, source="oidc")

        assert principal.roles == frozenset({Role.VIEWER})

    def test_a_custom_claim_name_is_honored(self) -> None:
        mapping = ClaimsMapping(roles_claim="adg_roles")
        principal = principal_from_claims(
            build(adg_roles=["admin"], roles=["viewer"]), mapping=mapping, source="oidc"
        )

        assert principal.roles == frozenset({Role.ADMIN})

    def test_an_unrecognized_role_grants_nothing_and_is_carried_for_diagnosis(self) -> None:
        principal = principal_from_claims(
            build(roles=["ADG-Auditors"]), mapping=MAPPING, source="oidc"
        )

        assert principal.roles == frozenset()
        assert principal.granted == frozenset()
        assert principal.unknown_roles == ("ADG-Auditors",)
        assert principal.is_authorized_for_anything is False

    def test_a_reserved_role_is_recognized_and_reported_as_inactive(self) -> None:
        principal = principal_from_claims(
            build(roles=["remediator"]), mapping=MAPPING, source="oidc"
        )

        assert principal.roles == frozenset({Role.REMEDIATOR})
        assert principal.reserved_roles == frozenset({Role.REMEDIATOR})
        assert principal.granted == frozenset()
        # Recognized, so not reported as a tenant mistake.
        assert principal.unknown_roles == ()

    def test_nonstring_entries_in_the_claim_are_ignored(self) -> None:
        principal = principal_from_claims(
            build(roles=["viewer", 7, None]), mapping=MAPPING, source="oidc"
        )

        assert principal.roles == frozenset({Role.VIEWER})


class TestGroupAssignments:
    MAPPING = ClaimsMapping(group_roles={"11111111-1111-1111-1111-111111111111": Role.AUDITOR})

    def test_a_mapped_group_grants_its_role(self) -> None:
        principal = principal_from_claims(
            build(groups=["11111111-1111-1111-1111-111111111111"]),
            mapping=self.MAPPING,
            source="oidc",
        )

        assert principal.roles == frozenset({Role.AUDITOR})

    def test_the_mapping_is_case_insensitive(self) -> None:
        """Group object ids are pasted from the portal, which renders them in either case."""
        principal = principal_from_claims(
            build(groups=["11111111-1111-1111-1111-111111111111".upper()]),
            mapping=self.MAPPING,
            source="oidc",
        )

        assert principal.roles == frozenset({Role.AUDITOR})

    def test_an_unmapped_group_grants_nothing_and_is_reported_as_a_group(self) -> None:
        principal = principal_from_claims(
            build(groups=["99999999-9999-9999-9999-999999999999"]),
            mapping=self.MAPPING,
            source="oidc",
        )

        assert principal.roles == frozenset()
        assert principal.unknown_roles == ("group:99999999-9999-9999-9999-999999999999",)

    def test_groups_and_app_roles_combine(self) -> None:
        principal = principal_from_claims(
            build(roles=["viewer"], groups=["11111111-1111-1111-1111-111111111111"]),
            mapping=self.MAPPING,
            source="oidc",
        )

        assert principal.roles == frozenset({Role.VIEWER, Role.AUDITOR})

    def test_groups_are_ignored_when_no_mapping_is_configured(self) -> None:
        """An unconfigured deployment must not accidentally authorize every group member."""
        principal = principal_from_claims(
            build(groups=["11111111-1111-1111-1111-111111111111"]),
            mapping=ClaimsMapping(),
            source="oidc",
        )

        assert principal.roles == frozenset()


class TestTheDevelopmentFlag:
    def test_a_development_token_marks_its_principal(self) -> None:
        principal = principal_from_claims(
            build(roles=["admin"]), mapping=MAPPING, source="development"
        )

        assert principal.development is True

    def test_a_tenant_token_does_not(self) -> None:
        principal = principal_from_claims(build(roles=["admin"]), mapping=MAPPING, source="oidc")

        assert principal.development is False


class TestTheCollectorPrincipal:
    def test_a_collector_key_may_write_observations(self) -> None:
        principal = collector_principal("fs01")

        assert principal.can(Capability.COLLECTORS_INGEST)

    def test_a_collector_key_may_not_read_the_estate_back_out(self) -> None:
        """The key lives in a scheduled task's configuration on a file server. If it could
        read, stealing it would hand over the map of the estate."""
        principal = collector_principal("fs01")

        for capability in (
            Capability.RESOURCES_READ,
            Capability.IDENTITIES_READ,
            Capability.ACCESS_READ,
            Capability.SEARCH,
            Capability.COLLECTORS_READ,
            Capability.SETTINGS_READ,
        ):
            assert not principal.can(capability), capability

    def test_it_holds_no_role(self) -> None:
        assert collector_principal("fs01").roles == frozenset()
