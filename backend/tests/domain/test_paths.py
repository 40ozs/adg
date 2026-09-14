"""Path normalization and resource-path identity."""

from __future__ import annotations

import pytest

from app.domain import LocalPath, UncPath, parse_local_path, parse_unc_path, parse_windows_path
from app.domain.errors import DomainValidationError

FINANCE = "\\\\FS01\\Finance\\Reports"


class TestUncNormalization:
    @pytest.mark.parametrize(
        "raw",
        [
            "\\\\FS01\\Finance\\Reports",
            "\\\\FS01\\Finance\\Reports\\",
            "\\\\FS01\\Finance\\Reports\\\\",
            "\\\\FS01\\\\Finance\\\\Reports",
            "//FS01/Finance/Reports",
            "\\\\FS01\\Finance\\.\\Reports",
            "  \\\\FS01\\Finance\\Reports  ",
            "\\\\?\\UNC\\FS01\\Finance\\Reports",
        ],
    )
    def test_equivalent_spellings_produce_one_path(self, raw: str) -> None:
        assert parse_unc_path(raw).value == FINANCE

    def test_case_is_preserved_for_display(self) -> None:
        assert parse_unc_path("\\\\fs01\\finance").value == "\\\\fs01\\finance"

    def test_case_differences_share_a_comparison_key(self) -> None:
        upper = parse_unc_path("\\\\FS01\\FINANCE\\Reports")
        lower = parse_unc_path("\\\\fs01\\finance\\reports")

        assert upper.comparison_key == lower.comparison_key
        assert upper.value != lower.value

    def test_share_root_is_identified(self) -> None:
        root = parse_unc_path("\\\\FS01\\Finance")

        assert root.is_share_root is True
        assert root.relative_path == ""
        assert root.parent is None

    def test_segments_and_parent_walk_upward(self) -> None:
        path = parse_unc_path("\\\\FS01\\Finance\\Reports\\2026")

        assert path.segments == ("Reports", "2026")
        assert path.parent is not None
        assert path.parent.value == FINANCE
        assert path.share_root.value == "\\\\FS01\\Finance"

    def test_child_extends_a_path(self) -> None:
        assert (
            parse_unc_path("\\\\FS01\\Finance").child("Reports").value
            == "\\\\FS01\\Finance\\Reports"
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "\\\\FS01",
            "\\\\FS01\\",
            "Finance\\Reports",
            "C:\\data",
            "\\\\FS01\\Finance\\Rep<orts",
            "\\\\FS01\\Fin|ance",
        ],
    )
    def test_invalid_unc_paths_are_rejected(self, raw: str) -> None:
        with pytest.raises(DomainValidationError):
            parse_unc_path(raw)

    def test_parent_traversal_is_rejected_rather_than_guessed(self) -> None:
        with pytest.raises(DomainValidationError, match=r"\.\."):
            parse_unc_path("\\\\FS01\\Finance\\..\\HR")

    def test_rejection_message_shows_the_expected_shape(self) -> None:
        with pytest.raises(DomainValidationError) as caught:
            parse_unc_path("Finance\\Reports")

        assert "server" in str(caught.value)
        assert "share" in str(caught.value)


class TestLocalPaths:
    @pytest.mark.parametrize(
        "raw",
        [
            "D:\\Shares\\Finance",
            "d:/shares/finance/",
            "D:\\\\Shares\\\\Finance",
            "\\\\?\\D:\\Shares\\Finance",
        ],
    )
    def test_equivalent_spellings_share_a_comparison_key(self, raw: str) -> None:
        assert parse_local_path(raw).comparison_key == "d:\\shares\\finance"

    def test_drive_letter_is_upper_cased(self) -> None:
        assert parse_local_path("d:\\data").value == "D:\\data"

    def test_drive_root_is_represented_with_a_separator(self) -> None:
        root = parse_local_path("C:\\")

        assert root.is_root is True
        assert root.value == "C:\\"

    def test_administrative_share_form_is_available(self) -> None:
        unc = parse_local_path("D:\\Shares\\Finance").to_unc("FS01")

        assert unc.value == "\\\\FS01\\D$\\Shares\\Finance"

    @pytest.mark.parametrize(
        "raw", ["", "data\\reports", "C:data", "1:\\data", "\\\\FS01\\Finance"]
    )
    def test_invalid_local_paths_are_rejected(self, raw: str) -> None:
        with pytest.raises(DomainValidationError):
            parse_local_path(raw)


class TestPathDispatch:
    def test_unc_input_yields_a_unc_path(self) -> None:
        assert isinstance(parse_windows_path("\\\\FS01\\Finance"), UncPath)

    def test_drive_input_yields_a_local_path(self) -> None:
        assert isinstance(parse_windows_path("E:\\data"), LocalPath)

    def test_extended_unc_input_yields_a_unc_path(self) -> None:
        assert isinstance(parse_windows_path("\\\\?\\UNC\\FS01\\Finance"), UncPath)

    def test_relative_input_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            parse_windows_path("data\\reports")


class TestConstructedPaths:
    def test_direct_construction_validates_components(self) -> None:
        with pytest.raises(DomainValidationError):
            UncPath(server="FS01", share="")

    def test_separators_in_a_component_are_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            UncPath(server="FS01", share="Finance\\Reports")

    def test_paths_are_immutable(self) -> None:
        path = parse_unc_path(FINANCE)

        with pytest.raises(AttributeError):
            path.server = "FS02"  # type: ignore[misc]


class TestPathEquality:
    """Windows file systems are case-insensitive, so path equality must be too.

    If it were not, a share reported as ``fs01`` and a directory reported as ``FS01``
    would be two resources, and permissions would appear to differ between them.
    """

    def test_paths_differing_only_in_case_are_equal(self) -> None:
        assert parse_unc_path("\\\\FS01\\Finance") == parse_unc_path("//fs01/finance/")

    def test_equal_paths_hash_identically(self) -> None:
        paths = {parse_unc_path("\\\\FS01\\Finance"), parse_unc_path("\\\\fs01\\FINANCE")}

        assert len(paths) == 1

    def test_different_paths_remain_distinct(self) -> None:
        assert parse_unc_path("\\\\FS01\\Finance") != parse_unc_path("\\\\FS02\\Finance")

    def test_local_paths_compare_case_insensitively(self) -> None:
        assert parse_local_path("D:\\Shares\\Finance") == parse_local_path("d:/shares/finance")

    def test_a_unc_path_never_equals_a_local_path(self) -> None:
        assert parse_unc_path("\\\\FS01\\D$\\data") != parse_local_path("D:\\data")

    def test_comparison_with_a_plain_string_is_false(self) -> None:
        assert parse_unc_path(FINANCE) != FINANCE
