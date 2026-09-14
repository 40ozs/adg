"""Reading a search box, tested without a database.

Interpretation decides which indexes a search touches, so it is the part worth pinning
exactly. Each case here is something somebody actually types: a SID pasted from an ACE, a
path pasted from Explorer, a group name half-remembered, and the paste that came with a
trailing separator.
"""

from __future__ import annotations

import pytest

from app.domain.search import (
    MAX_SEARCH_TERM,
    MIN_SEARCH_TERM,
    QueryShape,
    SearchTermError,
    interpret_query,
)


class TestSecurityIdentifiers:
    def test_a_sid_is_read_as_a_sid(self) -> None:
        query = interpret_query("S-1-5-21-1004336348-1177238915-682003330-512")

        assert query.shape is QueryShape.SID
        assert query.sid is not None
        assert query.sid.value == "S-1-5-21-1004336348-1177238915-682003330-512"

    def test_a_well_known_sid_is_read_as_a_sid(self) -> None:
        assert interpret_query("S-1-5-32-544").shape is QueryShape.SID

    def test_the_interpretation_is_stated_in_words(self) -> None:
        assert "security identifier" in interpret_query("S-1-5-32-544").explanation

    def test_something_that_merely_starts_with_s_is_a_name(self) -> None:
        assert interpret_query("S-Corp Finance").shape is QueryShape.NAME


class TestPaths:
    def test_a_bare_server_is_read_as_a_server(self) -> None:
        query = interpret_query("\\\\FS01")

        assert query.shape is QueryShape.SERVER
        assert query.server == "FS01"

    def test_a_server_and_share_is_read_as_a_share(self) -> None:
        query = interpret_query("\\\\FS01\\Finance")

        assert query.shape is QueryShape.SHARE
        assert query.server == "FS01"
        assert query.share == "Finance"

    def test_a_deeper_path_is_read_as_a_directory(self) -> None:
        query = interpret_query("\\\\FS01\\Finance\\Payroll\\2026")

        assert query.shape is QueryShape.UNC_PATH
        assert query.unc_path is not None
        assert query.unc_path.relative_path == "Payroll\\2026"

    def test_forward_slashes_are_accepted(self) -> None:
        """People paste from browsers and from scripts, not only from Explorer."""
        query = interpret_query("//FS01/Finance")

        assert query.shape is QueryShape.SHARE
        assert query.share == "Finance"

    def test_a_trailing_separator_does_not_change_the_answer(self) -> None:
        assert interpret_query("\\\\FS01\\Finance\\").shape is QueryShape.SHARE

    def test_a_path_that_cannot_be_parsed_falls_back_to_a_name(self) -> None:
        """A paste that is nearly a path is still perfectly good text to match names with;
        refusing it outright would be a worse answer than a slightly wrong one."""
        query = interpret_query("\\\\FS01\\Fin|ance")

        assert query.shape is QueryShape.NAME

    def test_a_local_path_is_not_a_path_lookup(self) -> None:
        """``D:\\Shares\\Finance`` names nothing on its own: a local path without a server
        does not identify a resource, which is why it is not ADG's identity key."""
        assert interpret_query("D:\\Shares\\Finance").shape is QueryShape.NAME


class TestNames:
    def test_ordinary_text_is_read_as_a_name(self) -> None:
        query = interpret_query("Finance Managers")

        assert query.shape is QueryShape.NAME
        assert query.text == "Finance Managers"

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert interpret_query("  Finance  ").text == "Finance"

    def test_the_explanation_admits_that_matching_is_by_prefix(self) -> None:
        """The user has to be told, or 'finance' not matching 'Corp-Finance' looks broken."""
        assert "prefix" in interpret_query("Finance").explanation


class TestRefusals:
    @pytest.mark.parametrize("term", ["", " ", "a", "  x "])
    def test_a_term_shorter_than_the_minimum_is_refused(self, term: str) -> None:
        with pytest.raises(SearchTermError, match=f"at least {MIN_SEARCH_TERM}"):
            interpret_query(term)

    def test_the_refusal_says_why_rather_than_just_no(self) -> None:
        with pytest.raises(SearchTermError, match="most of the estate"):
            interpret_query("a")

    def test_an_absurdly_long_term_is_refused(self) -> None:
        with pytest.raises(SearchTermError, match=f"limited to {MAX_SEARCH_TERM}"):
            interpret_query("x" * (MAX_SEARCH_TERM + 1))

    def test_a_term_at_exactly_the_limit_is_accepted(self) -> None:
        assert interpret_query("x" * MAX_SEARCH_TERM).shape is QueryShape.NAME
