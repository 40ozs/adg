"""Storage keys, and reading them back apart.

A key is ``sid`` or ``host|sid``. Nothing downstream re-derives that rule except
:func:`app.services.graph.split_key`, which labels the one kind of node ADG knows least
about — a key a traversal reached but no ``principals`` row describes. Getting the split
wrong there mislabels exactly those nodes, and there is no stored record to contradict it.

A SID can never contain ``|``. A host name can: the published ``hostName`` definition
forbids path separators and control characters and nothing else. So the separator that
matters is the **last** one, not the first.
"""

from __future__ import annotations

import pytest

from app.contracts.v1 import keys
from app.domain import LocalGroup, MembershipEdge, MembershipEdgeKind, PrincipalKind, Sid
from app.services.graph import split_key

BUILTIN = Sid("S-1-5-32-544")
USER = Sid("S-1-5-21-2000000001-2000000002-2000000003-1104")


class TestSplittingAKey:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("S-1-5-21-1-2-3-500", (None, "S-1-5-21-1-2-3-500")),
            ("S-1-1-0", (None, "S-1-1-0")),
            ("fs01|S-1-5-32-544", ("fs01", "S-1-5-32-544")),
            ("fs01.corp.example.com|S-1-5-32-544", ("fs01.corp.example.com", "S-1-5-32-544")),
            # A host name containing the separator: legal by the published contract, and
            # the reason the split reads from the right.
            ("odd|host|S-1-5-32-544", ("odd|host", "S-1-5-32-544")),
        ],
    )
    def test_the_host_and_the_sid_come_back_out(
        self, key: str, expected: tuple[str | None, str]
    ) -> None:
        assert split_key(key) == expected

    def test_the_sid_half_is_never_truncated(self) -> None:
        # The regression: splitting at the first separator returned ('odd', 'host|S-1-...'),
        # so an undescribed node was reported with a host that was half its real host and a
        # "SID" that was not a SID at all.
        host, sid = split_key("odd|host|S-1-5-32-544")

        assert Sid.try_parse(sid) is not None
        assert host == "odd|host"

    @pytest.mark.parametrize("host", ["FS01", "fs01", "fs01.corp.example.com", "a-b_c"])
    def test_a_local_group_key_round_trips(self, host: str) -> None:
        group = LocalGroup(sid=BUILTIN, host_key=host)

        assert split_key(group.identity_key) == (host.casefold(), BUILTIN.value)

    def test_a_domain_principal_key_has_no_host(self) -> None:
        assert split_key(USER.value) == (None, USER.value)


class TestKeysTheDomainProduces:
    def test_a_local_group_is_scoped_and_a_domain_group_is_not(self) -> None:
        local = LocalGroup(sid=BUILTIN, host_key="FS01")

        assert local.identity_key == f"fs01|{BUILTIN}"
        assert keys.principal_key(BUILTIN, PrincipalKind.LOCAL_GROUP, "FS01") == (
            f"principal|fs01|{BUILTIN}"
        )
        assert keys.principal_key(BUILTIN, PrincipalKind.DOMAIN_GROUP) == f"principal|{BUILTIN}"

    def test_the_same_builtin_sid_on_two_hosts_is_two_keys(self) -> None:
        first = LocalGroup(sid=BUILTIN, host_key="FS10")
        second = LocalGroup(sid=BUILTIN, host_key="FS11")

        assert first.identity_key != second.identity_key

    def test_a_domain_member_inside_a_local_group_keeps_its_global_key(self) -> None:
        # Scoping the member too would fragment one user into one node per server.
        edge = MembershipEdge(
            group_sid=BUILTIN,
            member_sid=USER,
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS10",
        )

        assert edge.group_key == f"fs10|{BUILTIN}"
        assert edge.member_key == USER.value
        assert split_key(edge.member_key) == (None, USER.value)

    def test_a_builtin_member_inside_a_local_group_is_scoped_to_that_host(self) -> None:
        edge = MembershipEdge(
            group_sid=BUILTIN,
            member_sid=Sid("S-1-5-32-545"),
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS10",
        )

        assert edge.member_key == "fs10|S-1-5-32-545"
        assert split_key(edge.member_key) == ("fs10", "S-1-5-32-545")

    def test_the_edge_key_names_both_endpoints_and_the_kind(self) -> None:
        edge = MembershipEdge(group_sid=BUILTIN, member_sid=USER, host_key=None)

        assert edge.identity_key == f"{BUILTIN}->{USER}|directory_group_member"

    def test_two_kinds_between_one_pair_are_two_edges(self) -> None:
        listed = MembershipEdge(group_sid=Sid("S-1-5-21-1-2-3-513"), member_sid=USER)
        primary = MembershipEdge(
            group_sid=Sid("S-1-5-21-1-2-3-513"),
            member_sid=USER,
            kind=MembershipEdgeKind.PRIMARY_GROUP,
        )

        assert listed.identity_key != primary.identity_key
        assert listed.group_key == primary.group_key
        assert listed.member_key == primary.member_key
