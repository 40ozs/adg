"""The acceptance scenario: can the model state who has access to what, and why?

This mirrors §12 of `docs/architecture/permission-domain-model.md`. It builds every fact an
answer needs — and nothing that would pre-empt the Phase 4 engine — then asserts that the
explanation chain is reconstructible from stored facts alone.

It is deliberately an *integration* test of the domain types: if a later phase changes a
type in a way that breaks the explanation, this fails.
"""

from __future__ import annotations

import datetime as dt

from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    CollectorKind,
    DirectoryResource,
    DomainGroup,
    DomainIdentifier,
    GroupScope,
    GroupType,
    LocalGroup,
    MembershipEdge,
    MembershipEdgeKind,
    NtfsAce,
    NtfsRight,
    Observation,
    ObservationSource,
    ScanRun,
    ScanStatus,
    SecurityDescriptorFacts,
    Server,
    SharePermission,
    Sid,
    SmbShare,
    SmbShareAce,
    UnresolvedPrincipal,
    UnresolvedReason,
    User,
    WellKnownPrincipal,
    parse_local_path,
    parse_unc_path,
)

DOMAIN = Sid("S-1-5-21-1004336348-1177238915-682003330")
ALICE = Sid(f"{DOMAIN}-1104")
FINANCE_TEAM = Sid(f"{DOMAIN}-1201")
FINANCE_RW = Sid(f"{DOMAIN}-1202")
ORPHAN = Sid("S-1-5-21-999-888-777-1234")
EVERYONE = Sid("S-1-1-0")

MODIFY_MASK = 0x001301BF
START = dt.datetime(2026, 9, 14, 8, 0, tzinfo=dt.UTC)
END = dt.datetime(2026, 9, 14, 8, 5, tzinfo=dt.UTC)


def build_scenario() -> dict[str, object]:
    """Every fact needed to answer the question, as a collector would report it."""
    domain = DomainIdentifier(domain_sid=DOMAIN, dns_name="corp.example.com")
    alice = User(sid=ALICE, display_name="Alice Smith", sam_account_name="asmith", enabled=True)
    team = DomainGroup(
        sid=FINANCE_TEAM,
        display_name="Finance-Team",
        scope=GroupScope.GLOBAL,
        group_type=GroupType.SECURITY,
    )
    resource_group = DomainGroup(
        sid=FINANCE_RW,
        display_name="Finance-RW",
        scope=GroupScope.DOMAIN_LOCAL,
        group_type=GroupType.SECURITY,
    )

    edges = [
        MembershipEdge(group_sid=FINANCE_TEAM, member_sid=ALICE),
        MembershipEdge(group_sid=FINANCE_RW, member_sid=FINANCE_TEAM),
    ]

    server = Server(name="FS01", dns_host_name="fs01.corp.example.com")
    share = SmbShare(
        server_key=server.identity_key,
        name="Finance",
        local_path=parse_local_path("D:\\Shares\\Finance"),
    )
    directory = DirectoryResource(
        path=parse_unc_path("\\\\FS01\\Finance\\Reports"),
        local_path=parse_local_path("D:\\Shares\\Finance\\Reports"),
        share_key=share.identity_key,
        is_acl_boundary=True,
        depth_from_share_root=1,
    )

    share_ace = SmbShareAce(
        trustee_sid=EVERYONE, ace_type=AceType.ALLOW, permission=SharePermission.FULL
    )
    ntfs_ace = NtfsAce(
        trustee_sid=FINANCE_RW,
        ace_type=AceType.ALLOW,
        access_mask=MODIFY_MASK,
        flags=AceFlag.OBJECT_INHERIT | AceFlag.CONTAINER_INHERIT,
        order_index=0,
    )
    orphan_ace = NtfsAce(
        trustee_sid=ORPHAN, ace_type=AceType.ALLOW, access_mask=MODIFY_MASK, order_index=1
    )
    descriptor = SecurityDescriptorFacts(
        owner_sid=Sid("S-1-5-32-544"), dacl_present=True, dacl_protected=True, ace_count=2
    )

    run = ScanRun(
        source=ObservationSource(
            collector=CollectorKind.NTFS,
            collector_host="COLLECTOR01",
            method="System.IO.DirectoryInfo.GetAccessControl",
            collector_version="0.1.0",
            target="\\\\FS01\\Finance",
        ),
        started_at=START,
        status=ScanStatus.SUCCEEDED,
        completed_at=END,
        observation_count=2,
    )

    return {
        "domain": domain,
        "alice": alice,
        "team": team,
        "resource_group": resource_group,
        "edges": edges,
        "server": server,
        "share": share,
        "directory": directory,
        "share_ace": share_ace,
        "ntfs_ace": ntfs_ace,
        "orphan_ace": orphan_ace,
        "descriptor": descriptor,
        "run": run,
    }


class TestWhoHasAccessAndWhy:
    def test_the_membership_path_is_reconstructible_from_edges(self) -> None:
        edges = build_scenario()["edges"]
        assert isinstance(edges, list)

        # Walk member -> group without any pre-expanded set.
        by_member: dict[str, str] = {edge.member_key: edge.group_key for edge in edges}
        path = [ALICE.value]
        while path[-1] in by_member:
            path.append(by_member[path[-1]])

        assert path == [ALICE.value, FINANCE_TEAM.value, FINANCE_RW.value]

    def test_the_final_group_is_the_ace_trustee(self) -> None:
        scenario = build_scenario()
        ntfs_ace = scenario["ntfs_ace"]
        assert isinstance(ntfs_ace, NtfsAce)

        assert ntfs_ace.trustee_sid == FINANCE_RW
        assert NtfsRight.WRITE_DATA in ntfs_ace.rights
        assert ntfs_ace.ace_type is AceType.ALLOW

    def test_both_layers_are_present_and_distinct(self) -> None:
        scenario = build_scenario()
        share_ace = scenario["share_ace"]
        ntfs_ace = scenario["ntfs_ace"]
        assert isinstance(share_ace, SmbShareAce)
        assert isinstance(ntfs_ace, NtfsAce)

        # The share layer is recorded as a level, the NTFS layer as a mask. Nothing here
        # intersects them: that is the Phase 4 engine's work.
        assert share_ace.permission is SharePermission.FULL
        assert ntfs_ace.access_mask == MODIFY_MASK

    def test_the_resource_is_identified_independently_of_spelling(self) -> None:
        scenario = build_scenario()
        directory = scenario["directory"]
        assert isinstance(directory, DirectoryResource)

        other_spelling = DirectoryResource(path=parse_unc_path("//fs01/finance/reports/"))

        assert directory.identity_key == other_spelling.identity_key

    def test_the_share_and_the_directory_remain_separate_entities(self) -> None:
        scenario = build_scenario()
        share = scenario["share"]
        directory = scenario["directory"]
        assert isinstance(share, SmbShare)
        assert isinstance(directory, DirectoryResource)

        assert directory.share_key == share.identity_key
        assert directory.path.share_root == share.unc_path

    def test_every_fact_carries_provenance(self) -> None:
        scenario = build_scenario()
        run = scenario["run"]
        ntfs_ace = scenario["ntfs_ace"]
        assert isinstance(run, ScanRun)
        assert isinstance(ntfs_ace, NtfsAce)

        observation = Observation.during(run, ntfs_ace, observed_at=END)

        assert observation.scan_run_id == run.run_id
        assert observation.source.collector is CollectorKind.NTFS
        assert observation.source.collector_host == "COLLECTOR01"
        assert observation.observed_at == END

    def test_an_orphaned_ace_survives_collection(self) -> None:
        scenario = build_scenario()
        orphan_ace = scenario["orphan_ace"]
        descriptor = scenario["descriptor"]
        assert isinstance(orphan_ace, NtfsAce)
        assert isinstance(descriptor, SecurityDescriptorFacts)

        orphan = UnresolvedPrincipal(
            sid=orphan_ace.trustee_sid, reason=UnresolvedReason.UNTRUSTED_DOMAIN
        )

        # The ACE is still on the ACL and still counted by the descriptor.
        assert orphan.identity_key == ORPHAN.value
        assert descriptor.ace_count == 2

    def test_the_descriptor_records_that_inheritance_is_blocked(self) -> None:
        scenario = build_scenario()
        descriptor = scenario["descriptor"]
        directory = scenario["directory"]
        assert isinstance(descriptor, SecurityDescriptorFacts)
        assert isinstance(directory, DirectoryResource)

        assert descriptor.dacl_protected is True
        assert directory.is_acl_boundary is True

    def test_no_effective_access_is_stored_anywhere(self) -> None:
        # Nothing in the domain package answers the access question; only the facts exist.
        import app.domain as domain_package

        forbidden = [
            name
            for name in dir(domain_package)
            if any(word in name.lower() for word in ("effective", "resolve_access", "can_access"))
        ]

        assert forbidden == []


class TestAdditionalRealities:
    def test_a_local_group_grant_is_representable(self) -> None:
        administrators = LocalGroup(sid=Sid("S-1-5-32-544"), host_key="FS01")
        edge = MembershipEdge(
            group_sid=administrators.sid,
            member_sid=FINANCE_TEAM,
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS01",
        )

        assert edge.group_key == administrators.identity_key

    def test_a_well_known_trustee_on_the_share_is_representable(self) -> None:
        everyone = WellKnownPrincipal(sid=EVERYONE)

        assert everyone.conventional_name == "Everyone"
        assert everyone.effective_domain_sid is None

    def test_an_inherited_ace_records_where_it_came_from(self) -> None:
        ace = NtfsAce(
            trustee_sid=FINANCE_RW,
            ace_type=AceType.ALLOW,
            access_mask=MODIFY_MASK,
            flags=AceFlag.INHERITED | AceFlag.CONTAINER_INHERIT,
            source=AceSource.INHERITED,
            inherited_from="\\\\FS01\\Finance",
        )

        assert ace.is_inherited is True
        assert ace.inherited_from == "\\\\FS01\\Finance"
