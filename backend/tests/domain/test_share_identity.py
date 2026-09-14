r"""Naming a share, and naming the principal an ACE points at.

Both are places where a wrong answer is silent. A share identifier that resolves to the
wrong share reports one share's ACL under another's name; a trustee key that host-scopes
the wrong SID either splits one domain group into one group per server, or merges every
server's local administrators into one group nobody is in.
"""

from __future__ import annotations

import pytest

from app.domain import (
    AceType,
    DomainValidationError,
    MembershipEdge,
    MembershipEdgeKind,
    SharePermission,
    Sid,
    SmbShare,
    SmbShareAce,
    parse_share_identifier,
    referenced_principal_key,
)

BUILTIN_ADMINS = Sid("S-1-5-32-544")
EVERYONE = Sid("S-1-1-0")
ALICE = Sid("S-1-5-21-1004336348-1177238915-682003330-1104")


class TestParsingAShareIdentifier:
    @pytest.mark.parametrize(
        "identifier",
        [
            "fs01|finance",
            "FS01|Finance",
            "\\\\FS01\\Finance",
            "\\\\fs01\\finance\\",
            "//FS01/Finance",
            "\\\\?\\UNC\\FS01\\Finance",
            "  \\\\FS01\\Finance  ",
        ],
    )
    def test_every_spelling_lands_on_one_key(self, identifier: str) -> None:
        assert parse_share_identifier(identifier).identity_key == "fs01|finance"

    def test_the_key_is_the_one_the_share_itself_produces(self) -> None:
        # If these two ever diverge, a share could be looked up by a key it is not stored
        # under, and the lookup would return 404 for a share that is present.
        share = SmbShare(server_key="FS01", name="Finance")
        assert parse_share_identifier("\\\\FS01\\Finance").identity_key == share.identity_key

    def test_it_keeps_the_observed_spelling_for_display(self) -> None:
        identity = parse_share_identifier("\\\\FS01\\Finance")
        assert identity.server == "FS01"
        assert identity.name == "Finance"
        assert str(identity) == "\\\\FS01\\Finance"

    def test_a_folder_inside_a_share_is_refused(self) -> None:
        # A folder and the share above it have different ACLs. Truncating the path would
        # answer a question about the share when the caller asked about the folder.
        with pytest.raises(DomainValidationError) as error:
            parse_share_identifier("\\\\FS01\\Finance\\Reports")
        assert "directory inside a share" in str(error.value)
        assert "\\\\FS01\\Finance" in str(error.value)

    @pytest.mark.parametrize(
        "identifier",
        ["", "   ", "FS01", "\\\\FS01", "a|b|c", "fs01|", "|finance", "fs01|fin\\ance"],
    )
    def test_an_ambiguous_identifier_is_refused_rather_than_guessed(self, identifier: str) -> None:
        with pytest.raises(DomainValidationError):
            parse_share_identifier(identifier)

    def test_a_relative_segment_is_refused(self) -> None:
        with pytest.raises(DomainValidationError):
            parse_share_identifier("\\\\FS01\\Finance\\..")

    def test_the_unc_path_round_trips(self) -> None:
        identity = parse_share_identifier("fs01|finance")
        assert parse_share_identifier(identity.unc_path.value).identity_key == "fs01|finance"


class TestKeyingAReferencedPrincipal:
    def test_a_builtin_sid_is_scoped_to_the_host_that_reported_it(self) -> None:
        assert referenced_principal_key(BUILTIN_ADMINS, "FS01") == "fs01|S-1-5-32-544"

    def test_the_same_builtin_sid_on_two_hosts_gives_two_keys(self) -> None:
        # The whole reason the rule exists: these are different groups with different
        # members, and merging them would invent access nobody has.
        assert referenced_principal_key(BUILTIN_ADMINS, "FS01") != referenced_principal_key(
            BUILTIN_ADMINS, "FS02"
        )

    @pytest.mark.parametrize("sid", [EVERYONE, ALICE])
    def test_a_globally_unique_sid_keeps_its_global_key(self, sid: Sid) -> None:
        # Host-scoping Everyone or a domain user would split one principal into one row per
        # server that mentions it, and the membership graph would never reach any of them.
        assert referenced_principal_key(sid, "FS01") == sid.value

    def test_no_host_means_no_scoping(self) -> None:
        assert referenced_principal_key(BUILTIN_ADMINS, None) == BUILTIN_ADMINS.value

    def test_it_agrees_with_the_membership_edge_rule(self) -> None:
        # An ACE and a local-group edge naming the same trustee must land on one node, or
        # the graph and the ACL would describe two different principals.
        edge = MembershipEdge(
            group_sid=BUILTIN_ADMINS,
            member_sid=ALICE,
            kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host_key="FS01",
        )
        assert edge.group_key == referenced_principal_key(BUILTIN_ADMINS, "FS01")
        assert edge.member_key == referenced_principal_key(ALICE, "FS01")


class TestShareAceIdentity:
    def ace(self, **fields: object) -> SmbShareAce:
        defaults: dict[str, object] = {
            "trustee_sid": EVERYONE,
            "ace_type": AceType.ALLOW,
            "permission": SharePermission.CHANGE,
        }
        return SmbShareAce(**{**defaults, **fields})  # type: ignore[arg-type]

    def test_a_level_and_a_mask_are_different_entries(self) -> None:
        # Not a conversion: a source that reported 'change' and one that reported the mask
        # took different readings, and claiming they are one observation would fabricate
        # precision the level never had.
        as_level = self.ace().identity_key("fs01|finance")
        as_mask = self.ace(permission=None, access_mask=0x001301BF).identity_key("fs01|finance")
        assert as_level != as_mask
        assert as_level.endswith("|change")
        assert as_mask.endswith("|0x001301bf")

    def test_order_index_is_not_part_of_identity(self) -> None:
        # Reordering an ACL must not look like every entry being deleted and recreated.
        first = self.ace(order_index=0).identity_key("fs01|finance")
        moved = self.ace(order_index=7).identity_key("fs01|finance")
        assert first == moved

    def test_the_share_key_is_folded(self) -> None:
        assert self.ace().identity_key("FS01|Finance") == self.ace().identity_key("fs01|finance")

    def test_type_is_part_of_identity(self) -> None:
        allow = self.ace().identity_key("fs01|finance")
        deny = self.ace(ace_type=AceType.DENY).identity_key("fs01|finance")
        assert allow != deny

    def test_the_right_token_reports_the_form_the_source_used(self) -> None:
        assert self.ace().right_token == "change"
        assert self.ace(permission=None, access_mask=0x001200A9).right_token == "0x001200a9"
