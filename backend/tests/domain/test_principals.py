"""Principal invariants: identity keys, domain attribution, and unresolved SIDs."""

from __future__ import annotations

import pytest

from app.domain import (
    ComputerIdentity,
    DomainGroup,
    DomainIdentifier,
    GroupScope,
    GroupType,
    LocalGroup,
    PrincipalKind,
    Sid,
    UnresolvedPrincipal,
    UnresolvedReason,
    User,
    WellKnownPrincipal,
)
from app.domain.errors import DomainValidationError

DOMAIN_SID = Sid("S-1-5-21-1004336348-1177238915-682003330")
OTHER_DOMAIN_SID = Sid("S-1-5-21-9-8-7")
ALICE = Sid(f"{DOMAIN_SID}-1104")
ADMINISTRATORS = Sid("S-1-5-32-544")


class TestIdentityKey:
    def test_a_user_is_keyed_by_sid_alone(self) -> None:
        assert User(sid=ALICE, display_name="Alice Smith").identity_key == ALICE.value

    def test_renaming_does_not_change_identity(self) -> None:
        before = User(sid=ALICE, display_name="Alice Smith", sam_account_name="asmith")
        after = User(sid=ALICE, display_name="Alice Jones", sam_account_name="ajones")

        assert before.identity_key == after.identity_key

    def test_two_principals_sharing_a_name_remain_distinct(self) -> None:
        one = User(sid=Sid(f"{DOMAIN_SID}-1104"), display_name="Alice")
        two = User(sid=Sid(f"{OTHER_DOMAIN_SID}-1104"), display_name="Alice")

        assert one.identity_key != two.identity_key

    def test_a_local_group_is_keyed_by_host_and_sid(self) -> None:
        group = LocalGroup(sid=ADMINISTRATORS, host_key="FS01")

        assert group.identity_key == "fs01|S-1-5-32-544"

    def test_builtin_groups_on_different_servers_are_different_principals(self) -> None:
        fs01 = LocalGroup(sid=ADMINISTRATORS, host_key="FS01")
        fs02 = LocalGroup(sid=ADMINISTRATORS, host_key="FS02")

        assert fs01.sid == fs02.sid
        assert fs01.identity_key != fs02.identity_key

    def test_host_key_comparison_is_case_insensitive(self) -> None:
        assert (
            LocalGroup(sid=ADMINISTRATORS, host_key="FS01").identity_key
            == LocalGroup(sid=ADMINISTRATORS, host_key="fs01").identity_key
        )

    def test_a_local_group_without_a_host_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="host"):
            LocalGroup(sid=ADMINISTRATORS)


class TestDomainAttribution:
    def test_domain_is_derived_from_the_sid_when_not_recorded(self) -> None:
        assert User(sid=ALICE).effective_domain_sid == DOMAIN_SID

    def test_recorded_domain_must_agree_with_the_sid(self) -> None:
        with pytest.raises(DomainValidationError, match="belongs to domain"):
            User(sid=ALICE, domain_sid=OTHER_DOMAIN_SID)

    def test_a_domain_relative_sid_may_record_its_own_domain(self) -> None:
        assert User(sid=ALICE, domain_sid=DOMAIN_SID).effective_domain_sid == DOMAIN_SID

    def test_a_well_known_sid_has_no_domain(self) -> None:
        assert WellKnownPrincipal(sid=Sid("S-1-1-0")).effective_domain_sid is None

    def test_a_rid_only_domain_sid_is_required_where_a_domain_is_expected(self) -> None:
        with pytest.raises(DomainValidationError, match="not a domain SID"):
            User(sid=ALICE, domain_sid=Sid(f"{DOMAIN_SID}-512"))


class TestDomainIdentifier:
    def test_identity_is_the_domain_sid(self) -> None:
        domain = DomainIdentifier(domain_sid=DOMAIN_SID, dns_name="corp.example.com")

        assert domain.identity_key == DOMAIN_SID.value

    def test_a_principal_sid_is_not_a_domain(self) -> None:
        with pytest.raises(DomainValidationError, match="not a domain SID"):
            DomainIdentifier(domain_sid=ALICE)

    def test_forest_root_is_unknown_rather_than_false(self) -> None:
        assert DomainIdentifier(domain_sid=DOMAIN_SID).is_forest_root is None

    def test_forest_root_is_reported_when_known(self) -> None:
        domain = DomainIdentifier(domain_sid=DOMAIN_SID, forest_root_domain_sid=DOMAIN_SID)

        assert domain.is_forest_root is True


class TestGroups:
    def test_distribution_groups_grant_no_access(self) -> None:
        group = DomainGroup(
            sid=Sid(f"{DOMAIN_SID}-1200"),
            group_type=GroupType.DISTRIBUTION,
            scope=GroupScope.UNIVERSAL,
        )

        assert group.grants_access is False

    def test_security_groups_grant_access(self) -> None:
        group = DomainGroup(sid=Sid(f"{DOMAIN_SID}-1201"), group_type=GroupType.SECURITY)

        assert group.grants_access is True

    def test_unknown_group_type_is_not_reported_as_harmless(self) -> None:
        assert DomainGroup(sid=Sid(f"{DOMAIN_SID}-1202")).grants_access is None

    def test_a_group_may_not_claim_another_kind(self) -> None:
        with pytest.raises(DomainValidationError, match="kind"):
            DomainGroup(sid=Sid(f"{DOMAIN_SID}-1203"), kind=PrincipalKind.USER)


class TestUnresolvedPrincipals:
    def test_an_unresolved_sid_is_a_valid_fact(self) -> None:
        orphan = UnresolvedPrincipal(sid=ALICE, reason=UnresolvedReason.DELETED)

        assert orphan.identity_key == ALICE.value
        assert orphan.kind is PrincipalKind.UNRESOLVED

    def test_an_unresolved_sid_may_not_carry_a_display_name(self) -> None:
        with pytest.raises(DomainValidationError, match="last_known_name"):
            UnresolvedPrincipal(sid=ALICE, display_name="Alice Smith")

    def test_a_previously_seen_name_is_kept_separately(self) -> None:
        orphan = UnresolvedPrincipal(sid=ALICE, last_known_name="CORP\\asmith")

        assert orphan.last_known_name == "CORP\\asmith"
        assert orphan.display_name is None


class TestWellKnownPrincipals:
    def test_conventional_name_is_filled_in_from_the_sid(self) -> None:
        assert WellKnownPrincipal(sid=Sid("S-1-1-0")).conventional_name == "Everyone"

    def test_an_unlisted_well_known_sid_has_no_name(self) -> None:
        assert WellKnownPrincipal(sid=Sid("S-1-5-1000")).conventional_name is None


class TestComputers:
    def test_a_computer_identity_keys_on_its_sid(self) -> None:
        computer = ComputerIdentity(sid=Sid(f"{DOMAIN_SID}-1305"), display_name="FS01$")

        assert computer.identity_key == f"{DOMAIN_SID}-1305"

    def test_managed_service_accounts_are_accepted(self) -> None:
        account = ComputerIdentity(
            sid=Sid(f"{DOMAIN_SID}-1306"), kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT
        )

        assert account.kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT


class TestImmutability:
    def test_principals_are_frozen(self) -> None:
        user = User(sid=ALICE)

        with pytest.raises(AttributeError):
            user.display_name = "changed"  # type: ignore[misc]
