"""Membership edge invariants."""

from __future__ import annotations

import pytest

from app.domain import MembershipEdge, MembershipEdgeKind, PrincipalKind, Sid
from app.domain.errors import DomainValidationError

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
OTHER_DOMAIN = "S-1-5-21-9-8-7"
ALICE = Sid(f"{DOMAIN}-1104")
FINANCE_TEAM = Sid(f"{DOMAIN}-1201")
FINANCE_RW = Sid(f"{DOMAIN}-1202")
ADMINISTRATORS = Sid("S-1-5-32-544")


class TestEdgeIdentity:
    def test_a_user_to_group_edge_keys_on_both_ends(self) -> None:
        edge = MembershipEdge(group_sid=FINANCE_TEAM, member_sid=ALICE)

        assert edge.identity_key == f"{FINANCE_TEAM}->{ALICE}|directory_group_member"

    def test_group_nesting_is_an_ordinary_edge(self) -> None:
        edge = MembershipEdge(
            group_sid=FINANCE_RW, member_sid=FINANCE_TEAM, member_kind=PrincipalKind.DOMAIN_GROUP
        )

        assert edge.group_key == FINANCE_RW.value
        assert edge.member_key == FINANCE_TEAM.value

    def test_the_same_pair_under_two_kinds_are_two_edges(self) -> None:
        directory = MembershipEdge(group_sid=FINANCE_TEAM, member_sid=ALICE)
        primary = MembershipEdge(
            group_sid=FINANCE_TEAM, member_sid=ALICE, kind=MembershipEdgeKind.PRIMARY_GROUP
        )

        assert directory.identity_key != primary.identity_key

    def test_a_self_edge_is_rejected_as_a_collector_defect(self) -> None:
        with pytest.raises(DomainValidationError, match="member of itself"):
            MembershipEdge(group_sid=FINANCE_TEAM, member_sid=FINANCE_TEAM)


class TestLocalGroupEdges:
    def test_a_local_edge_is_scoped_to_its_host(self) -> None:
        edge = MembershipEdge(
            group_sid=ADMINISTRATORS,
            member_sid=FINANCE_TEAM,
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS01",
        )

        assert edge.group_key == "fs01|S-1-5-32-544"

    def test_the_same_local_group_on_two_servers_yields_two_edges(self) -> None:
        fs01 = MembershipEdge(
            group_sid=ADMINISTRATORS,
            member_sid=FINANCE_TEAM,
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS01",
        )
        fs02 = MembershipEdge(
            group_sid=ADMINISTRATORS,
            member_sid=FINANCE_TEAM,
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS02",
        )

        assert fs01.identity_key != fs02.identity_key

    def test_a_domain_member_keeps_its_global_key_inside_a_local_group(self) -> None:
        edge = MembershipEdge(
            group_sid=ADMINISTRATORS,
            member_sid=FINANCE_TEAM,
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS01",
        )

        assert edge.member_key == FINANCE_TEAM.value

    def test_a_local_edge_without_a_host_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="host"):
            MembershipEdge(
                group_sid=ADMINISTRATORS,
                member_sid=ALICE,
                kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            )

    def test_a_directory_edge_may_not_claim_a_host(self) -> None:
        with pytest.raises(DomainValidationError, match="local-group edges"):
            MembershipEdge(group_sid=FINANCE_TEAM, member_sid=ALICE, host_key="FS01")


class TestCrossDomainAndForeignPrincipals:
    def test_a_foreign_security_principal_edge_is_recorded(self) -> None:
        foreign_member = Sid(f"{OTHER_DOMAIN}-1500")
        edge = MembershipEdge(
            group_sid=FINANCE_RW,
            member_sid=foreign_member,
            is_foreign_security_principal=True,
        )

        assert edge.is_foreign_security_principal is True
        assert edge.crosses_domain is True

    def test_same_domain_membership_does_not_cross_a_boundary(self) -> None:
        assert MembershipEdge(group_sid=FINANCE_TEAM, member_sid=ALICE).crosses_domain is False

    def test_a_builtin_group_reports_unknown_rather_than_false(self) -> None:
        edge = MembershipEdge(
            group_sid=ADMINISTRATORS,
            member_sid=ALICE,
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS01",
        )

        assert edge.crosses_domain is None


class TestMalformedGraphs:
    def test_a_two_node_cycle_is_storable(self) -> None:
        # Cycles are anomalies to report, not reasons to discard observations.
        forward = MembershipEdge(group_sid=FINANCE_RW, member_sid=FINANCE_TEAM)
        backward = MembershipEdge(group_sid=FINANCE_TEAM, member_sid=FINANCE_RW)

        assert forward.identity_key != backward.identity_key

    def test_an_edge_to_an_unresolvable_sid_is_storable(self) -> None:
        orphan = Sid(f"{OTHER_DOMAIN}-4242")
        edge = MembershipEdge(group_sid=FINANCE_RW, member_sid=orphan)

        assert edge.member_key == orphan.value
        assert edge.member_kind is None

    def test_primary_group_membership_is_representable(self) -> None:
        domain_users = Sid(f"{DOMAIN}-513")
        edge = MembershipEdge(
            group_sid=domain_users, member_sid=ALICE, kind=MembershipEdgeKind.PRIMARY_GROUP
        )

        assert edge.kind is MembershipEdgeKind.PRIMARY_GROUP
