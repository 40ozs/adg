r"""Pairing an ACL edit back together, and refusing to when the data cannot support it.

An ACE's identity includes what it grants, so tightening a DACL removes one row and adds
another. Presented unpaired, an operator sees "an entry was removed" and, eleven lines
later, "an entry was added", and reads at most one of them.

Pairing them is the readable answer and it is also the one place ADG computes a direction
from the data rather than assigning one: the two masks are compared. That is what makes
``Read -> Write`` come out ``mixed`` — it gains and loses bits at once — instead of being
rounded to whichever half the rule table happened to notice first.

The other half of this suite is the refusal. Two removals and two additions for one trustee
on one ACL cannot be paired: which goes with which is not in the data, and a guess would
print a before/after that never existed.
"""

from __future__ import annotations

from typing import Any

from app.changes.classify import classify
from app.changes.correlation import _share_sequence, correlate, key_of
from app.changes.model import ChangeAction, ChangeDirection, ChangeSeverity, ObjectChange
from app.contracts.v1.common import ObservationKind
from tests.changes import factories as f


def added(**kwargs: Any) -> ObjectChange:
    version = f.ntfs_ace(valid_from=f.FRIDAY, **kwargs)
    return classify(
        ObservationKind.NTFS_ACE, version.key, None, version, container_observed_before=True
    )


def removed(**kwargs: Any) -> ObjectChange:
    present = f.ntfs_ace(valid_from=f.MONDAY, last_seen_at=f.WEDNESDAY, valid_to=f.FRIDAY, **kwargs)
    gone = f.tombstone(
        ObservationKind.NTFS_ACE,
        present.key,
        valid_from=f.FRIDAY,
        container_key=f.FINANCE,
        related_key=present.related_key,
    )
    return classify(ObservationKind.NTFS_ACE, present.key, present, gone)


class TestPairingAnAclEdit:
    def test_a_tightening_is_one_edit_with_a_narrowed_direction(self) -> None:
        correlation = correlate(
            [
                removed(access_mask=f.FULL_CONTROL),
                added(access_mask=f.READ_EXECUTE, source_key="ntfs_ace|2"),
            ]
        )
        assert len(correlation.edits) == 1
        edit = correlation.edits[0]
        assert edit.direction is ChangeDirection.NARROWED
        assert edit.rights_before is not None and edit.rights_before.value == f.FULL_CONTROL
        assert edit.rights_after is not None and edit.rights_after.value == f.READ_EXECUTE

    def test_a_loosening_is_broadened(self) -> None:
        correlation = correlate(
            [
                removed(access_mask=f.READ_EXECUTE),
                added(access_mask=f.FULL_CONTROL, source_key="ntfs_ace|2"),
            ]
        )
        assert correlation.edits[0].direction is ChangeDirection.BROADENED

    def test_an_edit_that_gains_and_loses_bits_is_mixed(self) -> None:
        """Read & Execute to a write-only mask: neither a broadening nor a narrowing.

        Rounding this to one of the two is how a change report comes to say the opposite of
        what happened for the half of the mask it dropped.
        """
        correlation = correlate(
            [
                removed(access_mask=f.READ_EXECUTE),
                added(access_mask=0x000116, source_key="ntfs_ace|2"),
            ]
        )
        assert correlation.edits[0].direction is ChangeDirection.MIXED

    def test_a_deny_inverts_the_direction(self) -> None:
        """A Deny whose mask grew withholds more. Reading a Deny as a grant reports a
        tightening as a loosening, which is the most common way an ACL tool lies."""
        correlation = correlate(
            [
                removed(ace_type="deny", access_mask=f.READ_EXECUTE),
                added(ace_type="deny", access_mask=f.FULL_CONTROL, source_key="ntfs_ace|2"),
            ]
        )
        assert correlation.edits[0].direction is ChangeDirection.NARROWED

    def test_both_halves_point_back_at_the_same_edit(self) -> None:
        one, two = (
            removed(access_mask=f.FULL_CONTROL),
            added(access_mask=f.READ_EXECUTE, source_key="ntfs_ace|2"),
        )
        correlation = correlate([one, two])
        assert correlation.edit_of[key_of(one)] == correlation.edit_of[key_of(two)] == 0
        assert correlation.for_change(one) is correlation.for_change(two)

    def test_the_edit_takes_the_severity_of_its_more_severe_half(self) -> None:
        correlation = correlate(
            [
                removed(ace_type="deny", access_mask=f.FULL_CONTROL),
                added(ace_type="deny", access_mask=f.READ_EXECUTE, source_key="ntfs_ace|2"),
            ]
        )
        # The removal of a Deny is HIGH; the addition of a Deny is LOW.
        assert correlation.edits[0].severity is ChangeSeverity.HIGH

    def test_the_summary_names_the_trustee_and_both_masks(self) -> None:
        correlation = correlate(
            [
                removed(trustee=f.EVERYONE, access_mask=f.FULL_CONTROL),
                added(trustee=f.EVERYONE, access_mask=f.READ_EXECUTE, source_key="ntfs_ace|2"),
            ]
        )
        summary = correlation.edits[0].summary
        assert "Everyone" in summary
        assert "0x001f01ff" in summary and "0x001200a9" in summary


class TestRefusingToPair:
    def test_two_edits_for_one_trustee_are_left_unpaired(self) -> None:
        """Which removal goes with which addition is not recoverable from the data."""
        correlation = correlate(
            [
                removed(access_mask=f.FULL_CONTROL),
                removed(access_mask=f.MODIFY),
                added(access_mask=f.READ_EXECUTE, source_key="ntfs_ace|3"),
                added(access_mask=0x000116, source_key="ntfs_ace|4"),
            ]
        )
        assert correlation.edits == ()
        assert correlation.edit_of == {}

    def test_a_removal_with_no_matching_addition_is_not_an_edit(self) -> None:
        assert correlate([removed(access_mask=f.FULL_CONTROL)]).edits == ()

    def test_entries_for_different_trustees_do_not_pair(self) -> None:
        correlation = correlate(
            [
                removed(trustee=f.ALICE, access_mask=f.FULL_CONTROL),
                added(trustee=f.FINANCE_RW, access_mask=f.READ_EXECUTE, source_key="ntfs_ace|2"),
            ]
        )
        assert correlation.edits == ()

    def test_an_allow_and_a_deny_do_not_pair(self) -> None:
        """Replacing an Allow with a Deny is two decisions, not one entry being rewritten."""
        correlation = correlate(
            [
                removed(ace_type="allow", access_mask=f.FULL_CONTROL),
                added(ace_type="deny", access_mask=f.FULL_CONTROL, source_key="ntfs_ace|2"),
            ]
        )
        assert correlation.edits == ()

    def test_entries_on_different_directories_do_not_pair(self) -> None:
        correlation = correlate(
            [
                removed(resource=f.FINANCE, access_mask=f.FULL_CONTROL),
                added(
                    resource="\\\\fs01\\hr",
                    access_mask=f.READ_EXECUTE,
                    source_key="ntfs_ace|2",
                ),
            ]
        )
        assert correlation.edits == ()

    def test_a_modification_is_not_half_of_an_edit(self) -> None:
        """Only a removal and an addition pair. A modification already has both states."""
        before = f.ntfs_ace(valid_from=f.MONDAY, last_seen_at=f.WEDNESDAY, valid_to=f.FRIDAY)
        after = f.ntfs_ace(order_index=4, source_key="ntfs_ace|2", valid_from=f.FRIDAY)
        change = classify(ObservationKind.NTFS_ACE, before.key, before, after)
        assert change.action is ChangeAction.MODIFIED
        assert correlate([change]).edits == ()


class TestTheShareLayerHasItsOwnNormalForm:
    """It is not :func:`app.domain.acl_hash.normalize_acl`, and that is the point.

    That form hashes ``dacl_present``, ``dacl_protected`` and an ACE flags byte — none of
    which a share ACL has. Feeding it zeros for them would produce a digest that *looks*
    comparable to a real NTFS one, which is the same trap as comparing an SMB mask to an
    NTFS mask carrying the same bits.
    """

    def test_it_never_collides_with_the_ntfs_form(self) -> None:
        from app.domain.acl_hash import ACL_NORMAL_FORM_VERSION

        rendered = _share_sequence([f.smb_ace_state()])
        assert rendered.startswith("adg-share-acl/1")
        assert ACL_NORMAL_FORM_VERSION not in rendered

    def test_entries_read_back_in_any_order_render_identically(self) -> None:
        """Position comes from ``order_index``, never from the order rows arrived in."""
        first = f.smb_ace_state(trustee=f.ALICE, order_index=0)
        second = f.smb_ace_state(trustee=f.FINANCE_RW, order_index=1)
        assert _share_sequence([first, second]) == _share_sequence([second, first])

    def test_a_genuine_reordering_renders_differently(self) -> None:
        before = [
            f.smb_ace_state(trustee=f.ALICE, order_index=0),
            f.smb_ace_state(trustee=f.FINANCE_RW, order_index=1),
        ]
        after = [
            f.smb_ace_state(trustee=f.ALICE, order_index=1),
            f.smb_ace_state(trustee=f.FINANCE_RW, order_index=0),
        ]
        assert _share_sequence(before) != _share_sequence(after)

    def test_a_renumbering_that_preserves_the_sequence_renders_identically(self) -> None:
        """The whole acceptance criterion in one assertion: positions reduce to rank.

        A collector that numbered around entries ADG does not store, and one that numbered
        only the entries it kept, describe the same ACL and must not produce a diff.
        """
        before = [
            f.smb_ace_state(trustee=f.ALICE, order_index=1),
            f.smb_ace_state(trustee=f.FINANCE_RW, order_index=3),
        ]
        after = [
            f.smb_ace_state(trustee=f.ALICE, order_index=0),
            f.smb_ace_state(trustee=f.FINANCE_RW, order_index=1),
        ]
        assert _share_sequence(before) == _share_sequence(after)

    def test_an_unordered_reading_can_never_equal_an_ordered_one(self) -> None:
        """One reading knows the evaluation order and the other does not. Treating them as
        the same observation would claim knowledge that was never collected."""
        ordered = [f.smb_ace_state(trustee=f.ALICE, order_index=0)]
        unordered = [f.smb_ace_state(trustee=f.ALICE, order_index=None)]
        assert _share_sequence(ordered) != _share_sequence(unordered)
