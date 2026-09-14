"""SID normalization, validation, and identity behavior.

These are the invariants everything else keys on: if two spellings of one SID produce two
keys, ADG reports one principal as two.
"""

from __future__ import annotations

import pytest

from app.domain import Sid
from app.domain.errors import DomainValidationError

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
DOMAIN_ADMINS = f"{DOMAIN_SID}-512"


class TestNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (DOMAIN_ADMINS, DOMAIN_ADMINS),
            (f"  {DOMAIN_ADMINS}  ", DOMAIN_ADMINS),
            ("s-1-5-21-1004336348-1177238915-682003330-512", DOMAIN_ADMINS),
            ("S-1-05-21-1004336348-1177238915-682003330-0512", DOMAIN_ADMINS),
            ("S-1-5-32-544", "S-1-5-32-544"),
            ("S-1-1-0", "S-1-1-0"),
            ("S-1-5-18", "S-1-5-18"),
            ("S-1-5", "S-1-5"),
        ],
    )
    def test_spellings_collapse_to_one_canonical_form(self, raw: str, expected: str) -> None:
        assert Sid(raw).value == expected

    def test_equal_sids_written_differently_compare_equal(self) -> None:
        assert Sid("s-1-5-21-1-2-3-0512") == Sid("S-1-5-21-1-2-3-512")

    def test_canonical_sids_hash_identically(self) -> None:
        assert len({Sid("s-1-5-18"), Sid("S-1-5-18")}) == 1

    def test_large_identifier_authority_is_rendered_as_hex(self) -> None:
        sid = Sid("S-1-281474976710655-1")

        assert sid.value == "S-1-0xFFFFFFFFFFFF-1"
        assert sid.identifier_authority == 2**48 - 1

    def test_hexadecimal_authority_within_32_bits_is_rendered_as_decimal(self) -> None:
        # Windows prints authorities that fit in 32 bits in decimal form.
        assert Sid("S-1-0x000000000005-32-544").value == "S-1-5-32-544"


class TestValidation:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "CORP\\alice",
            "alice@corp.example.com",
            "S-1",
            "S-1-",
            "1-5-21-1-2-3",
            "S-1-5-21-1-2-3-",
            "S-1-5-21-x-2-3",
            "S-1-5-21-1-2-3-512-extra-",
        ],
    )
    def test_malformed_values_are_rejected(self, raw: str) -> None:
        with pytest.raises(DomainValidationError):
            Sid(raw)

    def test_wrong_revision_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="revision"):
            Sid("S-2-5-21-1-2-3")

    def test_more_than_fifteen_sub_authorities_is_rejected(self) -> None:
        too_many = "S-1-5-" + "-".join(str(number) for number in range(16))

        with pytest.raises(DomainValidationError, match="at most 15"):
            Sid(too_many)

    def test_fifteen_sub_authorities_is_accepted(self) -> None:
        allowed = "S-1-5-" + "-".join(str(number) for number in range(15))

        assert Sid(allowed).sub_authorities == tuple(range(15))

    def test_sub_authority_wider_than_32_bits_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="32 bits"):
            Sid(f"S-1-5-21-{2**32}")

    def test_error_message_names_the_offending_value(self) -> None:
        with pytest.raises(DomainValidationError) as caught:
            Sid("CORP\\alice")

        error = caught.value
        assert error.value == "CORP\\alice"
        assert error.field == "sid"
        # The rejected input is shown repr-quoted, so that stray whitespace or control
        # characters stay visible, together with the shape that was expected.
        assert repr("CORP\\alice") in str(error)
        assert "S-1-5-21" in str(error)

    def test_try_parse_returns_none_for_a_name(self) -> None:
        assert Sid.try_parse("CORP\\alice") is None

    def test_try_parse_returns_a_sid_for_a_sid(self) -> None:
        assert Sid.try_parse(DOMAIN_ADMINS) == Sid(DOMAIN_ADMINS)


class TestStructure:
    def test_domain_relative_sid_exposes_its_domain_and_rid(self) -> None:
        sid = Sid(DOMAIN_ADMINS)

        assert sid.is_domain_relative is True
        assert sid.rid == 512
        assert sid.domain_sid == Sid(DOMAIN_SID)

    def test_a_domain_sid_is_not_domain_relative(self) -> None:
        sid = Sid(DOMAIN_SID)

        assert sid.is_domain_sid is True
        assert sid.is_domain_relative is False
        assert sid.domain_sid is None

    def test_well_known_sids_have_no_domain(self) -> None:
        assert Sid("S-1-1-0").domain_sid is None
        assert Sid("S-1-5-18").domain_sid is None

    def test_builtin_sids_are_recognized(self) -> None:
        administrators = Sid("S-1-5-32-544")

        assert administrators.is_builtin is True
        assert administrators.is_well_known is True
        assert administrators.well_known_name == "BUILTIN\\Administrators"

    def test_a_domain_group_is_not_well_known_even_with_a_well_known_rid(self) -> None:
        # Domain Admins has a conventional RID, but the SID itself is domain-specific.
        assert Sid(DOMAIN_ADMINS).is_well_known is False

    def test_names_are_metadata_only(self) -> None:
        # Two principals may share a display name; SIDs are what distinguish them.
        assert Sid("S-1-5-21-1-2-3-1001") != Sid("S-1-5-21-9-8-7-1001")

    def test_string_form_round_trips(self) -> None:
        assert Sid(str(Sid(DOMAIN_ADMINS))) == Sid(DOMAIN_ADMINS)
