"""Resource identity: servers, shares, and directories."""

from __future__ import annotations

import pytest

from app.domain import (
    DirectoryResource,
    Server,
    ShareType,
    Sid,
    SmbShare,
    parse_local_path,
    parse_unc_path,
)
from app.domain.errors import DomainValidationError


class TestServer:
    def test_identity_is_the_case_folded_name(self) -> None:
        assert Server(name="FS01").identity_key == "fs01"

    def test_a_unc_path_is_not_a_server_name(self) -> None:
        with pytest.raises(DomainValidationError, match="path separators"):
            Server(name="\\\\FS01")

    def test_an_empty_name_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="name"):
            Server(name="   ")

    def test_the_machine_sid_namespace_is_derived_from_the_computer_sid(self) -> None:
        server = Server(name="FS01", computer_sid=Sid("S-1-5-21-11-22-33-1105"))

        assert server.local_domain_sid == Sid("S-1-5-21-11-22-33")

    def test_an_unknown_computer_sid_yields_no_namespace(self) -> None:
        assert Server(name="FS01").local_domain_sid is None


class TestSmbShare:
    def test_identity_is_server_and_share_name_case_insensitively(self) -> None:
        one = SmbShare(server_key="FS01", name="Finance")
        two = SmbShare(server_key="fs01", name="finance")

        assert one.identity_key == two.identity_key

    def test_two_shares_over_the_same_directory_stay_distinct(self) -> None:
        path = parse_local_path("D:\\Shares\\Finance")
        public = SmbShare(server_key="FS01", name="Finance", local_path=path)
        restricted = SmbShare(server_key="FS01", name="Finance-RO", local_path=path)

        assert public.identity_key != restricted.identity_key
        assert public.local_path == restricted.local_path

    def test_the_unc_path_of_a_share_is_its_root(self) -> None:
        share = SmbShare(server_key="FS01", name="Finance")

        assert share.unc_path.value == "\\\\FS01\\Finance"
        assert share.unc_path.is_share_root is True

    @pytest.mark.parametrize(
        ("name", "hidden", "administrative"),
        [
            ("Finance", False, False),
            ("Finance$", True, False),
            ("C$", True, True),
            ("ADMIN$", True, True),
            ("IPC$", True, True),
        ],
    )
    def test_hidden_and_administrative_shares_are_recognized(
        self, name: str, hidden: bool, administrative: bool
    ) -> None:
        share = SmbShare(server_key="FS01", name=name)

        assert share.is_hidden is hidden
        assert share.is_administrative is administrative

    def test_only_disk_shares_carry_file_permissions(self) -> None:
        assert SmbShare(server_key="FS01", name="Finance").carries_file_permissions is True
        assert (
            SmbShare(
                server_key="FS01", name="IPC$", share_type=ShareType.IPC
            ).carries_file_permissions
            is False
        )

    def test_a_share_name_may_not_contain_separators(self) -> None:
        with pytest.raises(DomainValidationError, match="path separators"):
            SmbShare(server_key="FS01", name="Finance\\Reports")

    def test_a_share_must_record_its_server(self) -> None:
        with pytest.raises(DomainValidationError, match="server"):
            SmbShare(server_key="", name="Finance")


class TestDirectoryResource:
    def test_identity_is_the_case_folded_unc_path(self) -> None:
        upper = DirectoryResource(path=parse_unc_path("\\\\FS01\\Finance\\Reports"))
        lower = DirectoryResource(path=parse_unc_path("\\\\fs01\\finance\\reports"))

        assert upper.identity_key == lower.identity_key

    def test_a_share_root_is_marked(self) -> None:
        directory = DirectoryResource(path=parse_unc_path("\\\\FS01\\Finance"))

        assert directory.is_share_root is True

    def test_a_directory_that_blocks_inheritance_is_a_boundary(self) -> None:
        with pytest.raises(DomainValidationError, match="ACL boundary"):
            DirectoryResource(
                path=parse_unc_path("\\\\FS01\\Finance\\Payroll"),
                inheritance_enabled=False,
                is_acl_boundary=False,
            )

    def test_a_protected_directory_is_representable(self) -> None:
        directory = DirectoryResource(
            path=parse_unc_path("\\\\FS01\\Finance\\Payroll"),
            local_path=parse_local_path("D:\\Shares\\Finance\\Payroll"),
            share_key="fs01|finance",
            inheritance_enabled=False,
            is_acl_boundary=True,
            depth_from_share_root=1,
        )

        assert directory.is_acl_boundary is True
        assert directory.depth_from_share_root == 1

    def test_negative_depth_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="non-negative"):
            DirectoryResource(path=parse_unc_path("\\\\FS01\\Finance"), depth_from_share_root=-1)
