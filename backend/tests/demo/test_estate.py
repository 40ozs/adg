r"""The demo estate: repeatable, self-consistent, and awkward on purpose.

Three properties are asserted here, and each of them is a thing a demo dataset silently
stops having.

**Repeatable.** Two builds of the same profile are identical, byte for byte through the
transcripts. A generator that drifted — an unordered set, a wall clock, a random seed —
would make every screenshot and every end-to-end assertion unreproducible, and the drift
would be invisible until two people compared results.

**Self-consistent.** Every SID an ACL names is a principal the AD run reported, or is
deliberately unresolved; every directory belongs to a share that exists; every inherited ACE
is what the parent actually projects. A demo that violates any of these produces screens
full of findings that are artifacts of the fixture rather than of the product.

**Awkward.** :data:`app.demo.estate.FEATURES` lists what the estate carries on purpose, and
each entry is checked against the built estate. The failure this prevents is the slow one: a
feature is dropped in an edit, the demo still looks fine, and the part of the product that
feature exercised stops being exercised by anything.
"""

from __future__ import annotations

import pytest

from app.demo.estate import (
    ALICE,
    BOB,
    CONTRACTORS,
    DOMAIN_SID,
    DOMAIN_USERS,
    FEATURES,
    FINANCE_ANNOUNCE,
    FINANCE_LEADS,
    FINANCE_RW,
    FINANCE_TEAM,
    ORPHANED_SID,
    PROFILES,
    DemoAce,
    DemoDirectory,
    DemoEstate,
    DemoShare,
    build_estate,
    sid,
)
from app.domain import AceFlag, AceType, GroupType, MembershipEdgeKind, PrincipalKind
from app.domain.acl_hash import acl_hash
from app.domain.inheritance import AclBoundaryReason, project_inherited_acl

PROFILE_NAMES = sorted(PROFILES)


@pytest.fixture(scope="module")
def estate() -> DemoEstate:
    return build_estate("small")


class TestItIsRepeatable:
    @pytest.mark.parametrize("profile", PROFILE_NAMES)
    def test_two_builds_of_one_profile_are_equal(self, profile: str) -> None:
        assert build_estate(profile) == build_estate(profile)

    def test_an_unknown_profile_names_the_ones_that_exist(self) -> None:
        """Rather than a bare KeyError from a dict lookup three frames down."""
        with pytest.raises(KeyError, match="standard"):
            build_estate("enormous")

    @pytest.mark.parametrize("profile", PROFILE_NAMES)
    def test_every_profile_carries_every_feature(self, profile: str) -> None:
        """Size scales; the feature set does not. The small profile is the one a smoke test
        and a first demo use, so it must be the one that proves the most per row."""
        built = build_estate(profile)

        assert len(built.principals) >= 20
        assert len(built.directories) >= 10
        assert {server.name for server in built.servers} == {"FS01", "FS02", "FS03"}

    def test_a_larger_profile_is_larger(self) -> None:
        assert build_estate("large").object_count > build_estate("standard").object_count
        assert build_estate("standard").object_count > build_estate("small").object_count


class TestItIsSelfConsistent:
    def test_every_share_belongs_to_a_collected_server(self, estate: DemoEstate) -> None:
        names = {server.name.casefold() for server in estate.servers}

        assert {share.server_name.casefold() for share in estate.shares} <= names

    def test_every_directory_belongs_to_a_collected_share(self, estate: DemoEstate) -> None:
        shares = {
            (share.server_name.casefold(), share.share_name.casefold()) for share in estate.shares
        }
        for directory in estate.directories:
            key = (directory.server_name.casefold(), directory.share_name.casefold())
            assert key in shares, f"{directory.path} has no share"

    def test_every_membership_names_a_collected_principal(self, estate: DemoEstate) -> None:
        known = {principal.sid for principal in estate.principals}
        for edge in estate.edges:
            assert edge.group_sid in known, edge
            assert edge.member_sid in known, edge

    def test_every_ace_trustee_is_a_collected_principal(self, estate: DemoEstate) -> None:
        """Including the orphan. An unresolved principal *is* collected — reported as
        unresolved — which is what lets the UI say whose ACE it is rather than nothing."""
        known = {principal.sid for principal in estate.principals}

        for share in estate.shares:
            for share_ace in share.aces:
                assert share_ace.trustee_sid in known, (share.share_name, share_ace)
        for directory in estate.directories:
            for ntfs_ace in directory.aces:
                assert ntfs_ace.trustee_sid in known, (directory.path, ntfs_ace)

    def test_declared_ace_count_matches_the_entries(self, estate: DemoEstate) -> None:
        """The two disagreeing is exactly the shape the ACL-hash audit exists to catch, and
        a fixture that carried it would make every audit finding meaningless."""
        for directory in estate.directories:
            assert directory.ace_count == len(directory.aces)

    def test_every_acl_hash_is_the_hash_of_the_entries(self, estate: DemoEstate) -> None:
        for directory in estate.directories:
            recomputed = acl_hash(
                dacl_present=directory.dacl_present,
                dacl_protected=directory.dacl_protected,
                aces=[ace.facts() for ace in directory.aces],
            )
            assert directory.acl_hash == recomputed, directory.path

    def test_a_child_that_inherits_carries_exactly_the_parent_projection(
        self, estate: DemoEstate
    ) -> None:
        """Not an approximation of it. This is the property the server recomputes when it
        decides whether a directory is a boundary, so a fixture that only nearly matched
        would report every directory as changed."""
        by_path = {directory.path.casefold(): directory for directory in estate.directories}
        checked = 0
        for directory in estate.directories:
            if directory.is_acl_boundary or directory.depth_from_share_root == 0:
                continue
            parent = by_path[_parent(directory.path).casefold()]
            projected = project_inherited_acl(
                [ace.facts() for ace in parent.aces], for_container=True
            )
            assert [ace.facts() for ace in directory.aces] == list(projected), directory.path
            checked += 1
        assert checked >= 3, "no cleanly inheriting directory was checked"

    def test_an_inherited_entry_carries_the_inherited_flag(self, estate: DemoEstate) -> None:
        for directory in estate.directories:
            for ace in directory.aces:
                if ace.inherited_from is not None:
                    assert ace.ace_flags & int(AceFlag.INHERITED), (directory.path, ace)

    def test_ace_positions_are_dense_and_canonical(self, estate: DemoEstate) -> None:
        """Explicit entries before inherited ones, positions 0..n-1 with no gaps. Windows
        writes a DACL this way and evaluation depends on it."""
        for directory in estate.directories:
            positions = [ace.order_index for ace in directory.aces]
            assert positions == list(range(len(directory.aces))), directory.path
            inherited = [ace.is_inherited for ace in directory.aces]
            assert inherited == sorted(inherited), f"{directory.path}: inherited before explicit"

    def test_no_principal_is_a_member_of_itself(self, estate: DemoEstate) -> None:
        for edge in estate.edges:
            assert edge.group_sid != edge.member_sid, edge


class TestTheAwkwardShapes:
    """One test per entry of FEATURES. The list is documentation; these make it true."""

    def test_the_feature_list_is_not_empty_and_has_no_duplicates(self) -> None:
        names = [feature.name for feature in FEATURES]

        assert len(names) == len(set(names))
        assert len(names) >= 15

    def test_alice_reaches_finance_rw_through_two_different_groups(
        self, estate: DemoEstate
    ) -> None:
        via = {edge.member_sid for edge in estate.edges if edge.group_sid == sid(FINANCE_RW)}

        assert {sid(FINANCE_TEAM), sid(FINANCE_LEADS)} <= via
        for group in (FINANCE_TEAM, FINANCE_LEADS):
            assert any(
                edge.group_sid == sid(group) and edge.member_sid == sid(ALICE)
                for edge in estate.edges
            )

    def test_bob_reaches_it_through_exactly_one(self, estate: DemoEstate) -> None:
        """The contrast is the point: removing Bob's one membership does take his access
        away, and removing either of Alice's does not."""
        routes = [
            edge
            for edge in estate.edges
            if edge.member_sid == sid(BOB)
            and edge.edge_kind is MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
        ]

        assert len(routes) == 1
        assert routes[0].group_sid == sid(FINANCE_TEAM)

    def test_every_human_has_a_primary_group_edge(self, estate: DemoEstate) -> None:
        primary = {
            edge.member_sid
            for edge in estate.edges
            if edge.edge_kind is MembershipEdgeKind.PRIMARY_GROUP
        }

        assert sid(ALICE) in primary
        assert all(
            edge.group_sid == sid(DOMAIN_USERS)
            for edge in estate.edges
            if edge.edge_kind is MembershipEdgeKind.PRIMARY_GROUP
        )

    def test_there_is_a_membership_cycle(self, estate: DemoEstate) -> None:
        pairs = {(edge.group_sid, edge.member_sid) for edge in estate.edges}
        cycles = [(a, b) for (a, b) in pairs if (b, a) in pairs]

        assert cycles, "the estate must contain a cycle; traversal has to survive one"
        assert any(sid(CONTRACTORS) in pair for pair in cycles)

    def test_there_is_a_deny_ace_on_a_protected_directory(self, estate: DemoEstate) -> None:
        confidential = _find(estate, r"\\FS01\HR\Confidential")

        assert confidential.dacl_protected is True
        assert confidential.boundary_reason is AclBoundaryReason.PROTECTED_DACL
        denies = [ace for ace in confidential.aces if ace.ace_type is AceType.DENY]
        assert [ace.trustee_sid for ace in denies] == [sid(CONTRACTORS)]

    def test_the_deny_comes_before_the_grant(self, estate: DemoEstate) -> None:
        """Canonical order. An ACL with the deny after the grant evaluates differently, and
        a demo that had it would be demonstrating a non-canonical ACL without saying so."""
        confidential = _find(estate, r"\\FS01\HR\Confidential")
        first_deny = next(
            index for index, ace in enumerate(confidential.aces) if ace.ace_type is AceType.DENY
        )
        grants_after = [
            index
            for index, ace in enumerate(confidential.aces)
            if ace.ace_type is AceType.ALLOW and index > first_deny
        ]

        assert grants_after, "the deny must precede at least one grant to matter"

    def test_a_directory_that_inherits_cleanly_is_not_a_boundary(self, estate: DemoEstate) -> None:
        reports = _find(estate, r"\\FS01\Finance\Reports")

        assert reports.is_acl_boundary is False
        assert reports.boundary_reason is None

    def test_a_directory_with_an_extra_entry_differs_from_its_parent(
        self, estate: DemoEstate
    ) -> None:
        budgets = _find(estate, r"\\FS01\Finance\Budgets")

        assert budgets.boundary_reason is AclBoundaryReason.ACL_DIFFERS_FROM_PARENT

    def test_every_share_root_is_a_boundary_for_that_reason(self, estate: DemoEstate) -> None:
        roots = [item for item in estate.directories if item.depth_from_share_root == 0]

        assert roots
        for root in roots:
            assert root.boundary_reason in (
                AclBoundaryReason.SHARE_ROOT,
                AclBoundaryReason.PROTECTED_DACL,
            ), root.path

    def test_the_share_is_the_narrower_layer_somewhere(self, estate: DemoEstate) -> None:
        archive_share = _share(estate, "FS02", "Archive")
        archive_ntfs = _find(estate, r"\\FS02\Archive")

        # Read at the share layer, Modify at the NTFS layer: the share limits the answer.
        assert any(ace.access_mask == 0x001200A9 for ace in archive_share.aces)
        assert any(ace.access_mask == 0x001301BF for ace in archive_ntfs.aces)

    def test_the_ntfs_acl_is_the_narrower_layer_somewhere(self, estate: DemoEstate) -> None:
        finance_share = _share(estate, "FS01", "Finance")
        finance_ntfs = _find(estate, r"\\FS01\Finance")

        assert [ace.permission for ace in finance_share.aces] == ["full"]
        assert all(ace.access_mask != 0x001F01FF for ace in finance_ntfs.aces if _is_group(ace))

    def test_an_unresolved_sid_is_named_on_a_live_acl(self, estate: DemoEstate) -> None:
        beta = _find(estate, r"\\FS02\Projects\Beta")
        orphan = next(item for item in estate.principals if item.sid == ORPHANED_SID)

        assert orphan.principal_kind is PrincipalKind.UNRESOLVED
        assert orphan.display_name is None, "an unresolved principal must not carry a name"
        assert orphan.last_known_name is not None
        assert any(ace.trustee_sid == ORPHANED_SID for ace in beta.aces)

    def test_everyone_holds_full_control_somewhere(self, estate: DemoEstate) -> None:
        public = _find(estate, r"\\FS01\Public")

        assert any(
            ace.trustee_sid == "S-1-1-0" and ace.access_mask == 0x001F01FF for ace in public.aces
        )

    def test_a_generic_right_projects_as_two_entries(self, estate: DemoEstate) -> None:
        """The split is the whole reason a generic ACE is in the estate: a projection that
        produced one entry would report every directory below Projects as a boundary."""
        alpha = _find(estate, r"\\FS02\Projects\Alpha")
        it_admins = [ace for ace in alpha.aces if ace.trustee_sid == sid(1207)]

        assert len(it_admins) == 2
        masks = sorted(ace.access_mask for ace in it_admins)
        assert masks == [0x001F01FF, 0x10000000]

    def test_an_untouched_directory_can_still_differ_from_its_parent(
        self, estate: DemoEstate
    ) -> None:
        """Alpha has no explicit entry and is still a boundary, because Windows materialized
        the CREATOR OWNER grant as an entry naming whoever created it. True about the DACL,
        misleading about intent, and exactly the finding an operator has to be able to read
        correctly."""
        alpha = _find(estate, r"\\FS02\Projects\Alpha")

        assert alpha.boundary_reason is AclBoundaryReason.ACL_DIFFERS_FROM_PARENT
        assert any(ace.trustee_sid == sid(BOB) for ace in alpha.aces)

    def test_the_local_group_is_host_scoped(self, estate: DemoEstate) -> None:
        local = [item for item in estate.principals if item.host_key is not None]

        assert [item.sid for item in local] == ["S-1-5-32-544"]
        assert local[0].host_key == "fs01"
        assert local[0].principal_kind is PrincipalKind.LOCAL_GROUP
        assert any(
            edge.edge_kind is MembershipEdgeKind.LOCAL_GROUP_MEMBER and edge.host_key == "fs01"
            for edge in estate.edges
        )

    def test_a_distribution_group_exists_and_grants_nothing(self, estate: DemoEstate) -> None:
        announce = next(item for item in estate.principals if item.sid == sid(FINANCE_ANNOUNCE))
        named = {ace.trustee_sid for directory in estate.directories for ace in directory.aces} | {
            ace.trustee_sid for share in estate.shares for ace in share.aces
        }

        assert announce.group_type is GroupType.DISTRIBUTION
        assert announce.sid not in named
        assert any(edge.group_sid == announce.sid for edge in estate.edges)

    def test_a_disabled_account_is_still_named_on_an_acl(self, estate: DemoEstate) -> None:
        payroll = _find(estate, r"\\FS01\Finance\Payroll")
        erin = next(item for item in estate.principals if item.sid == sid(1108))

        assert erin.enabled is False
        assert any(ace.trustee_sid == erin.sid for ace in payroll.aces)

    def test_there_is_an_administrative_share(self, estate: DemoEstate) -> None:
        admin_shares = [share for share in estate.shares if share.is_special]

        assert [share.share_name for share in admin_shares] == ["C$"]

    def test_one_server_has_shares_and_no_directories(self, estate: DemoEstate) -> None:
        """FS03: its share list was read and its file system was not. ADG knows a share is
        there and nothing about what is inside it, which is what a failed NTFS run leaves
        behind and what every empty list below it has to be read as."""
        assert estate.shares_on("FS03")
        assert estate.directories_on("FS03") == ()
        assert "FS03" in estate.ntfs_errors

    def test_one_server_reports_errors_without_failing(self, estate: DemoEstate) -> None:
        assert estate.directories_on("FS02")
        assert len(estate.ntfs_errors["FS02"]) == 2


def _find(estate: DemoEstate, path: str) -> DemoDirectory:
    return next(item for item in estate.directories if item.path == path)


def _share(estate: DemoEstate, server: str, share: str) -> DemoShare:
    return next(
        item for item in estate.shares if item.server_name == server and item.share_name == share
    )


def _is_group(ace: DemoAce) -> bool:
    return ace.trustee_sid.startswith(f"{DOMAIN_SID}-12")


def _parent(path: str) -> str:
    return "\\\\" + "\\".join(path.lstrip("\\").split("\\")[:-1])
