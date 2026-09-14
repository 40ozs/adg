"""The normalized ACL form: what it keeps, what it ignores, and what it refuses.

The digest is compared across two implementations (PowerShell's and Python's), across runs
weeks apart, and between a directory and its parent. Every one of those comparisons is only
meaningful if the same DACL always produces the same document — and if two DACLs that grant
differently always produce different ones.

So these tests come in two shapes, and both matter:

* **Stability.** The things that must NOT change the digest: the order the entries are
  handed over, the numeric values of their positions, and repeated evaluation.
* **Sensitivity.** The things that MUST change it: a different mask, a different flags byte,
  a reordering, a NULL versus an empty DACL, and protection from inheritance.

A test that only checked stability would pass for a function that returned a constant.
"""

from __future__ import annotations

import pytest

from app.domain import (
    ACL_HASH_ALGORITHM,
    ACL_HASH_LENGTH,
    ACL_NORMAL_FORM_VERSION,
    AceFlag,
    AceSource,
    AceType,
    AclAceFacts,
    DomainValidationError,
    NtfsAce,
    Sid,
    acl_hash,
    is_acl_hash,
    normalize_acl,
)

FINANCE_RW = "S-1-5-21-1004336348-1177238915-682003330-1202"
CONTRACTORS = "S-1-5-21-1004336348-1177238915-682003330-1310"
ADMINISTRATORS = "S-1-5-32-544"

MODIFY = 0x001301BF
READ_EXECUTE = 0x001200A9
FULL_CONTROL = 0x001F01FF


def ace(
    trustee: str = FINANCE_RW,
    ace_type: AceType = AceType.ALLOW,
    mask: int = MODIFY,
    flags: int = 0x03,
    order: int | None = 0,
) -> AclAceFacts:
    return AclAceFacts(
        trustee_sid=trustee,
        ace_type=ace_type,
        access_mask=mask,
        ace_flags=flags,
        order_index=order,
    )


class TestTheDocument:
    def test_it_names_its_own_format_first(self) -> None:
        # A format change that forgot to bump this token would let two incomparable digests
        # be compared as though they agreed.
        normalized = normalize_acl(dacl_present=True, aces=[ace()])
        assert normalized.lines[0] == ACL_NORMAL_FORM_VERSION

    def test_it_is_the_exact_text_the_collector_mirrors(self) -> None:
        # Pinned literally. The PowerShell implementation builds this string byte for byte,
        # and a contract test runs both; changing either side alone fails that test.
        normalized = normalize_acl(
            dacl_present=True,
            dacl_protected=True,
            aces=[
                ace(FINANCE_RW, mask=MODIFY, flags=0x03, order=0),
                ace(ADMINISTRATORS, mask=FULL_CONTROL, flags=0x13, order=1),
            ],
        )
        assert normalized.text == (
            "adg-acl/1\n"
            "dacl_present=true\n"
            "dacl_protected=true\n"
            "order=observed\n"
            f"ace=0|{FINANCE_RW}|allow|0x001301bf|0x03\n"
            f"ace=1|{ADMINISTRATORS}|allow|0x001f01ff|0x13\n"
        )

    def test_every_line_is_terminated_including_the_last(self) -> None:
        # A document is a sequence of complete records, so appending an entry cannot change
        # the bytes of the ones before it.
        assert normalize_acl(dacl_present=True, aces=[ace()]).text.endswith("\n")

    def test_the_digest_is_lower_case_hexadecimal_of_the_stated_length(self) -> None:
        digest = acl_hash(dacl_present=True, aces=[ace()])
        assert len(digest) == ACL_HASH_LENGTH
        assert is_acl_hash(digest)
        assert ACL_HASH_ALGORITHM == "sha256"

    def test_the_text_is_kept_so_a_disagreement_can_be_explained(self) -> None:
        # "Which entry differs" is the question an operator has to answer, and it is only
        # answerable if the thing that was hashed can be printed.
        normalized = normalize_acl(dacl_present=True, aces=[ace()])
        assert FINANCE_RW in normalized.text
        assert normalized.ace_count == 1


class TestWhatDoesNotChangeTheDigest:
    def test_the_order_the_entries_are_handed_over(self) -> None:
        # The server hashes rows it read back in ace_key order; the collector hashes while
        # walking the DACL. They must agree, or every directory would look changed.
        entries = [ace(FINANCE_RW, order=0), ace(ADMINISTRATORS, mask=FULL_CONTROL, order=1)]
        assert acl_hash(dacl_present=True, aces=entries) == acl_hash(
            dacl_present=True, aces=list(reversed(entries))
        )

    def test_the_numeric_value_of_a_position(self) -> None:
        # One collector numbers around the entries it dropped, another numbers only what it
        # kept. Same ACL, same evaluation order, same digest.
        contiguous = acl_hash(
            dacl_present=True, aces=[ace(FINANCE_RW, order=0), ace(ADMINISTRATORS, order=1)]
        )
        gapped = acl_hash(
            dacl_present=True, aces=[ace(FINANCE_RW, order=4), ace(ADMINISTRATORS, order=11)]
        )
        assert contiguous == gapped

    def test_the_case_a_sid_was_spelled_in(self) -> None:
        # Canonicalized on the way in: two readings of one descriptor must hash the same.
        assert acl_hash(dacl_present=True, aces=[ace(ADMINISTRATORS.lower())]) == acl_hash(
            dacl_present=True, aces=[ace(ADMINISTRATORS)]
        )

    def test_evaluating_it_again(self) -> None:
        entries = [ace(CONTRACTORS, AceType.DENY, order=0), ace(FINANCE_RW, order=1)]
        assert acl_hash(dacl_present=True, aces=entries) == acl_hash(
            dacl_present=True, aces=entries
        )

    def test_whether_the_caller_passed_facts_or_domain_aces(self) -> None:
        # A caller holding stored rows, a contract payload, or a live descriptor must all
        # arrive at the same number.
        from_facts = acl_hash(dacl_present=True, aces=[ace(FINANCE_RW, flags=0x13, order=0)])
        from_domain = acl_hash(
            dacl_present=True,
            aces=[
                NtfsAce(
                    trustee_sid=Sid(FINANCE_RW),
                    ace_type=AceType.ALLOW,
                    access_mask=MODIFY,
                    flags=AceFlag(0x13),
                    source=AceSource.INHERITED,
                    order_index=0,
                )
            ],
        )
        assert from_facts == from_domain


class TestWhatDoesChangeTheDigest:
    def test_reordering_the_entries(self) -> None:
        # Windows evaluates a DACL in order: a Deny moved below an Allow grants access that
        # was previously refused. A digest that called those two ACLs equal would hide it.
        deny_first = acl_hash(
            dacl_present=True,
            aces=[ace(CONTRACTORS, AceType.DENY, order=0), ace(CONTRACTORS, order=1)],
        )
        allow_first = acl_hash(
            dacl_present=True,
            aces=[ace(CONTRACTORS, order=0), ace(CONTRACTORS, AceType.DENY, order=1)],
        )
        assert deny_first != allow_first

    def test_a_different_mask(self) -> None:
        assert acl_hash(dacl_present=True, aces=[ace(mask=MODIFY)]) != acl_hash(
            dacl_present=True, aces=[ace(mask=READ_EXECUTE)]
        )

    def test_a_different_flags_byte(self) -> None:
        # ObjectInherit and ContainerInherit reach different children: same trustee, same
        # mask, different grant.
        assert acl_hash(dacl_present=True, aces=[ace(flags=0x01)]) != acl_hash(
            dacl_present=True, aces=[ace(flags=0x02)]
        )

    def test_allow_against_deny(self) -> None:
        assert acl_hash(dacl_present=True, aces=[ace(ace_type=AceType.ALLOW)]) != acl_hash(
            dacl_present=True, aces=[ace(ace_type=AceType.DENY)]
        )

    def test_a_null_dacl_against_an_empty_one(self) -> None:
        # Everyone has full access, versus nobody does. The whole reason dacl_present is a
        # field rather than an inference from an empty list.
        assert acl_hash(dacl_present=False) != acl_hash(dacl_present=True)

    def test_protection_from_inheritance(self) -> None:
        # Two directories holding identical entries, one protected and one not, are not the
        # same ACL: one is a boundary and the other inherits.
        assert acl_hash(dacl_present=True, dacl_protected=True, aces=[ace()]) != acl_hash(
            dacl_present=True, dacl_protected=False, aces=[ace()]
        )

    def test_an_unordered_reading_against_an_ordered_one(self) -> None:
        # Different-quality observations: one knows the evaluation order and the other does
        # not. Treating them as the same would claim knowledge that was never collected.
        ordered = normalize_acl(dacl_present=True, aces=[ace(order=0)])
        unordered = normalize_acl(dacl_present=True, aces=[ace(order=None)])
        assert ordered.ordered is True
        assert unordered.ordered is False
        assert ordered.digest != unordered.digest

    def test_a_duplicated_entry(self) -> None:
        # A DACL can legitimately hold the same grant twice, and that is itself a finding;
        # collapsing them would make a redundant ACL look tidy.
        once = acl_hash(dacl_present=True, aces=[ace(order=0)])
        twice = acl_hash(dacl_present=True, aces=[ace(order=0), ace(order=1)])
        assert once != twice


class TestAnUnorderedReading:
    def test_it_sorts_by_content_so_input_order_still_does_not_matter(self) -> None:
        entries = [ace(ADMINISTRATORS, order=None), ace(FINANCE_RW, order=None)]
        assert acl_hash(dacl_present=True, aces=entries) == acl_hash(
            dacl_present=True, aces=list(reversed(entries))
        )

    def test_it_says_so_in_the_document(self) -> None:
        normalized = normalize_acl(dacl_present=True, aces=[ace(order=None)])
        assert "order=unordered" in normalized.lines
        assert normalized.lines[-1].startswith("ace=-|")

    def test_a_partly_numbered_acl_counts_as_unordered(self) -> None:
        # Half a position sequence is not an evaluation order.
        normalized = normalize_acl(
            dacl_present=True, aces=[ace(FINANCE_RW, order=0), ace(ADMINISTRATORS, order=None)]
        )
        assert normalized.ordered is False


class TestWhatItRefuses:
    def test_a_null_dacl_carrying_entries(self) -> None:
        with pytest.raises(DomainValidationError) as caught:
            normalize_acl(dacl_present=False, aces=[ace()])
        assert "NULL DACL" in str(caught.value)
        assert "opposite fact" in str(caught.value)

    def test_two_entries_claiming_one_position(self) -> None:
        # The digest would otherwise depend on the order the rows happened to be read in,
        # which is exactly what the normal form exists to remove.
        with pytest.raises(DomainValidationError) as caught:
            normalize_acl(
                dacl_present=True, aces=[ace(FINANCE_RW, order=3), ace(ADMINISTRATORS, order=3)]
            )
        assert "[3]" in str(caught.value)
        assert caught.value.field == "order_index"

    def test_a_mask_wider_than_a_descriptor_can_hold(self) -> None:
        with pytest.raises(DomainValidationError):
            AclAceFacts(
                trustee_sid=FINANCE_RW,
                ace_type=AceType.ALLOW,
                access_mask=0x1_0000_0000,
                ace_flags=0,
            )

    def test_flags_wider_than_the_header_byte(self) -> None:
        with pytest.raises(DomainValidationError):
            AclAceFacts(
                trustee_sid=FINANCE_RW, ace_type=AceType.ALLOW, access_mask=MODIFY, ace_flags=256
            )

    def test_a_negative_position(self) -> None:
        with pytest.raises(DomainValidationError):
            AclAceFacts(
                trustee_sid=FINANCE_RW,
                ace_type=AceType.ALLOW,
                access_mask=MODIFY,
                ace_flags=0,
                order_index=-1,
            )

    def test_a_trustee_that_is_not_a_sid(self) -> None:
        # Names are metadata and can never be identity, here least of all: a digest over a
        # name would change when somebody renamed a group.
        with pytest.raises(DomainValidationError):
            AclAceFacts(
                trustee_sid="CORP\\Finance-RW",
                ace_type=AceType.ALLOW,
                access_mask=MODIFY,
                ace_flags=0,
            )


class TestUnknownBitsSurvive:
    def test_a_mask_bit_no_right_names_is_still_hashed(self) -> None:
        # An ACE is evidence, and an ACE with a bit quietly removed is no longer evidence.
        known = acl_hash(dacl_present=True, aces=[ace(mask=MODIFY)])
        with_unknown = acl_hash(dacl_present=True, aces=[ace(mask=MODIFY | 0x0000_0800)])
        assert known != with_unknown

    def test_a_flags_bit_no_enumeration_names_is_still_hashed(self) -> None:
        assert acl_hash(dacl_present=True, aces=[ace(flags=0x03)]) != acl_hash(
            dacl_present=True, aces=[ace(flags=0x23)]
        )


class TestIsAclHash:
    @pytest.mark.parametrize(
        "value",
        [
            "0" * 64,
            "0f8c3a6beab25438144c1407be0c2d04467246a1db1234dbb877ccde449cbd57",
        ],
    )
    def test_it_accepts_a_digest(self, value: str) -> None:
        assert is_acl_hash(value)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "0" * 63,
            "0" * 65,
            "F" * 64,  # upper case: one digest must have one spelling
            "g" * 64,
        ],
    )
    def test_it_rejects_anything_else(self, value: str) -> None:
        assert not is_acl_hash(value)
