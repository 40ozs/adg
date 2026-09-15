"""Baseline drift: what the comparison says, and the four answers it must keep apart.

Every case here is built by hand, because the distinctions that matter are exactly the ones a
real estate takes two scans and a deleted share to produce: *changed*, *gone*, *the target is
gone*, and *nobody has looked*. Three of those are an empty or different entry list and they
lead a reviewer to three different actions, so the whole point of the module is that they
never render alike.

The other property under test is that a re-scan which changes nothing reports nothing. A
comparison taken over ``version_id`` would report drift on every timeline write, and a drift
banner that cries wolf is a drift banner nobody reads.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import AceSource, AceType, ReviewTargetKind, SharePermission
from app.governance.drift import (
    DriftVerdict,
    GrantChangeKind,
    compare_grants,
    content_digest,
)
from app.governance.model import GrantEvidence, evidence_digest
from app.history.model import Certainty

FROM = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
SEEN = dt.datetime(2026, 3, 6, 9, 0, tzinfo=dt.UTC)

FINANCE = "\\\\fs01\\finance"
ALICE = "S-1-5-21-1-2-3-1001"

READ_EXECUTE = 0x1200A9
MODIFY = 0x1301BF


def ntfs(
    *,
    mask: int = READ_EXECUTE,
    ace_type: AceType = AceType.ALLOW,
    source: AceSource = AceSource.EXPLICIT,
    flags: int = 0x0,
    order: int = 0,
    certainty: Certainty = Certainty.OBSERVED,
    version_id: int = 1,
    ace_key: str | None = None,
) -> GrantEvidence:
    """One NTFS entry. ``ace_key`` is stable across scans, which is what pairs the two sides."""
    return GrantEvidence(
        target_kind=ReviewTargetKind.RESOURCE,
        ace_key=ace_key or f"{FINANCE}|{ALICE}|{ace_type.value}",
        trustee_sid=ALICE,
        trustee_key=ALICE,
        ace_type=ace_type,
        access_mask=mask,
        permission=None,
        ace_flags=flags,
        source=source,
        inherited_from=None,
        order_index=order,
        version_id=version_id,
        observed_from=FROM,
        last_confirmed_at=SEEN,
        certainty=certainty,
    )


def share(*, permission: SharePermission = SharePermission.READ) -> GrantEvidence:
    return GrantEvidence(
        target_kind=ReviewTargetKind.SHARE,
        ace_key=f"share|{ALICE}",
        trustee_sid=ALICE,
        trustee_key=ALICE,
        ace_type=AceType.ALLOW,
        access_mask=None,
        permission=permission,
        ace_flags=None,
        source=None,
        inherited_from=None,
        order_index=0,
        version_id=7,
        observed_from=FROM,
        last_confirmed_at=SEEN,
        certainty=Certainty.OBSERVED,
    )


class TestNothingHasChanged:
    def test_the_same_entries_are_unchanged(self) -> None:
        baseline = (ntfs(),)

        drift = compare_grants(baseline, baseline, target_present=True)

        assert drift.verdict is DriftVerdict.UNCHANGED
        assert not drift.has_drifted
        assert drift.changes == ()
        assert drift.summary.startswith("Unchanged")

    def test_a_rescan_that_wrote_a_new_version_of_the_same_state_is_not_drift(self) -> None:
        """The false positive this module's content digest exists to avoid.

        An entry removed and restored identically, or a version row rewritten, gives the same
        grant a new ``version_id``. Comparing on evidence identity would tell a reviewer their
        grant had changed when the thing they are certifying is character for character the
        same.
        """
        baseline = (ntfs(version_id=1),)
        now = (ntfs(version_id=94),)

        drift = compare_grants(baseline, now, target_present=True)

        assert drift.verdict is DriftVerdict.UNCHANGED
        assert drift.evidence_reissued is True
        assert "new versions of them" in drift.summary
        # And the two digests disagree precisely because the version rows differ, which is
        # the fact `evidence_reissued` is reporting rather than hiding.
        assert content_digest(baseline) == content_digest(now)
        assert evidence_digest(baseline) != evidence_digest(now)

    def test_certainty_is_not_part_of_the_comparison(self) -> None:
        """A later scan confirming the same state changes the certainty of the *answer*, not
        the grant. Counting that as drift would flag every item after every scan."""
        baseline = (ntfs(certainty=Certainty.INFERRED),)
        now = (ntfs(certainty=Certainty.OBSERVED),)

        assert compare_grants(baseline, now, target_present=True).verdict is DriftVerdict.UNCHANGED

    def test_an_unchanged_grant_on_a_stale_target_says_so(self) -> None:
        """The quiet failure: an open version reads as unchanged forever if nobody looks
        again, so a campaign compared against a share last scanned in March would report
        every item unchanged — true about the record, misleading about the estate."""
        baseline = (ntfs(),)

        drift = compare_grants(
            baseline, baseline, target_present=True, target_certainty=Certainty.INFERRED
        )

        assert drift.verdict is DriftVerdict.UNCHANGED
        assert "no scan has confirmed this target recently" in drift.summary

    def test_an_unchanged_grant_on_a_freshly_read_target_says_nothing_extra(self) -> None:
        drift = compare_grants(
            (ntfs(),), (ntfs(),), target_present=True, target_certainty=Certainty.OBSERVED
        )

        assert drift.summary == "Unchanged since the campaign was frozen."


class TestTheGrantHasChanged:
    @pytest.mark.parametrize(
        ("changed", "field", "label"),
        [
            (ntfs(mask=MODIFY), "access_mask", "rights"),
            (
                ntfs(ace_type=AceType.DENY, ace_key=f"{FINANCE}|{ALICE}|allow"),
                "ace_type",
                "allow or deny",
            ),
            (ntfs(source=AceSource.INHERITED), "source", "set here or inherited"),
            (ntfs(flags=0x13), "ace_flags", "inheritance flags"),
            (ntfs(order=4), "order_index", "position in the list"),
        ],
    )
    def test_each_material_field_is_reported_by_name(
        self, changed: GrantEvidence, field: str, label: str
    ) -> None:
        drift = compare_grants((ntfs(),), (changed,), target_present=True)

        assert drift.verdict is DriftVerdict.MODIFIED
        assert drift.has_drifted
        assert [change.kind for change in drift.changes] == [GrantChangeKind.CHANGED]
        assert drift.changes[0].fields == (field,)
        assert drift.changes[0].field_labels == (label,)
        assert label in drift.summary

    def test_an_added_entry_is_an_addition_not_a_change(self) -> None:
        extra = ntfs(ace_type=AceType.DENY, ace_key="second-entry", mask=MODIFY)

        drift = compare_grants((ntfs(),), (ntfs(), extra), target_present=True)

        assert drift.verdict is DriftVerdict.MODIFIED
        assert [change.kind for change in drift.changes] == [GrantChangeKind.ADDED]
        assert drift.changes[0].ace_key == "second-entry"
        assert drift.changes[0].before is None
        assert "1 entry added" in drift.summary

    def test_one_entry_removed_from_several_is_a_removal_of_that_entry(self) -> None:
        extra = ntfs(ace_key="second-entry", mask=MODIFY)

        drift = compare_grants((ntfs(), extra), (ntfs(),), target_present=True)

        assert drift.verdict is DriftVerdict.MODIFIED
        assert [change.kind for change in drift.changes] == [GrantChangeKind.REMOVED]
        assert "1 entry removed" in drift.summary

    def test_the_reviewer_is_told_they_are_still_deciding_the_frozen_evidence(self) -> None:
        """The item is never rewritten, so the summary must not read as though the question
        had changed underneath the answer."""
        drift = compare_grants((ntfs(),), (ntfs(mask=MODIFY),), target_present=True)

        assert "You are deciding on the frozen evidence" in drift.summary

    def test_a_rewritten_entry_is_one_change_and_not_a_removal_plus_an_addition(self) -> None:
        """An ACE key is content-addressed — it digests the mask and the flags — so widening
        somebody's rights tombstones one key and opens another. Reported by key alone, the
        most common thing that ever happens to a grant reads as two unrelated events."""
        baseline = (ntfs(mask=READ_EXECUTE, ace_key="alice|read"),)
        now = (ntfs(mask=MODIFY, ace_key="alice|modify"),)

        drift = compare_grants(baseline, now, target_present=True)

        assert [change.kind for change in drift.changes] == [GrantChangeKind.CHANGED]
        assert drift.changes[0].fields == ("access_mask",)
        assert drift.changes[0].ace_key == "alice|modify"
        assert "rights" in drift.summary

    def test_the_derived_key_is_not_reported_as_a_field_that_changed(self) -> None:
        """``ace_key`` differs exactly when one of the fields it digests differs, so naming
        it would put a second word for the same change in front of a reviewer."""
        drift = compare_grants(
            (ntfs(mask=READ_EXECUTE, ace_key="alice|read"),),
            (ntfs(mask=MODIFY, ace_key="alice|modify"),),
            target_present=True,
        )

        assert "ace_key" not in drift.changes[0].fields

    def test_an_ambiguous_rewrite_is_left_as_separate_additions_and_removals(self) -> None:
        """Two entries out and two in for one trustee could be matched two ways. Guessing
        which became which would put a claim in front of a reviewer that ADG cannot support,
        so the honest shape is what actually happened to the list."""
        baseline = (ntfs(ace_key="old-1"), ntfs(ace_key="old-2", flags=0x02))
        now = (ntfs(ace_key="new-1", mask=MODIFY), ntfs(ace_key="new-2", flags=0x13))

        kinds = [
            change.kind for change in compare_grants(baseline, now, target_present=True).changes
        ]

        assert sorted(kinds) == sorted([GrantChangeKind.ADDED] * 2 + [GrantChangeKind.REMOVED] * 2)

    def test_an_allow_replaced_by_a_deny_is_not_paired_with_it(self) -> None:
        """An allow and a deny are not one another rewritten; they are opposite statements,
        and pairing them would report "allow or deny changed" for a grant that was withdrawn
        and a prohibition that was added."""
        baseline = (ntfs(ace_type=AceType.ALLOW, ace_key="allow-entry"),)
        now = (ntfs(ace_type=AceType.DENY, ace_key="deny-entry"),)

        kinds = sorted(
            change.kind for change in compare_grants(baseline, now, target_present=True).changes
        )

        assert kinds == sorted([GrantChangeKind.ADDED, GrantChangeKind.REMOVED])

    def test_the_change_list_is_ordered_by_entry_so_two_runs_agree(self) -> None:
        baseline = (ntfs(ace_key="b"), ntfs(ace_key="a"))
        now = (ntfs(ace_key="c", mask=MODIFY),)

        drift = compare_grants(baseline, now, target_present=True)
        keys = [change.ace_key for change in drift.changes]

        assert keys == sorted(keys)


class TestTheGrantIsGone:
    def test_no_entry_on_a_target_that_was_read_is_a_removal(self) -> None:
        drift = compare_grants((ntfs(),), (), target_present=True)

        assert drift.verdict is DriftVerdict.REMOVED
        assert drift.has_drifted
        assert [change.kind for change in drift.changes] == [GrantChangeKind.REMOVED]
        assert drift.current_grants == ()

    def test_a_removal_does_not_claim_a_cause(self) -> None:
        """ADG cannot tell a carried-out revocation from somebody deleting the wrong entry,
        and a summary that picked one would put a cause on an observation."""
        summary = compare_grants((ntfs(),), (), target_present=True).summary

        assert "cannot tell whether somebody acted on a review" in summary
        # Neither reading is asserted. "fixed"/"resolved" would claim a remediation happened;
        # "unauthorized" would claim it did not.
        for claim in ("fixed", "resolved", "remediated", "unauthorized"):
            assert claim not in summary.casefold()

    def test_a_deleted_target_is_reported_as_the_target_being_gone(self) -> None:
        drift = compare_grants((ntfs(),), (), target_present=False)

        assert drift.verdict is DriftVerdict.REMOVED
        assert drift.target_present is False
        assert "The target itself is gone" in drift.summary

    def test_a_deleted_target_and_a_withdrawn_grant_do_not_read_alike(self) -> None:
        gone = compare_grants((ntfs(),), (), target_present=False)
        withdrawn = compare_grants((ntfs(),), (), target_present=True)

        assert gone.summary != withdrawn.summary


class TestNobodyHasLooked:
    def test_no_version_covering_the_instant_is_not_a_removal(self) -> None:
        """The failure this verdict exists to prevent: a reviewer told "already removed" will
        close the item, and the access is still there.

        Both sides are asserted from the *same* empty current list, because that is the whole
        difficulty — the entry list cannot tell the two apart, and only ``target_present``
        can.
        """
        nobody_looked = compare_grants((ntfs(),), (), target_present=None)
        we_looked = compare_grants((ntfs(),), (), target_present=True)

        assert nobody_looked.verdict is DriftVerdict.UNOBSERVED
        assert we_looked.verdict is DriftVerdict.REMOVED

    def test_not_knowing_is_not_drift(self) -> None:
        """Counted as drift, every report taken while a collector was down would show the
        whole campaign as moved."""
        drift = compare_grants((ntfs(),), (), target_present=None)

        assert not drift.has_drifted
        assert not drift.changes

    def test_it_reports_no_current_digest_rather_than_the_digest_of_nothing(self) -> None:
        """A digest of the empty set is a real value that would compare unequal to the
        baseline, and a client diffing the two would conclude the grant had changed."""
        drift = compare_grants((ntfs(),), (), target_present=None)

        assert drift.current_content_digest is None
        assert drift.current_certainty is None

    def test_the_summary_says_the_evidence_is_still_the_question(self) -> None:
        summary = compare_grants((ntfs(),), (), target_present=None).summary

        assert "cannot say whether this grant still stands" in summary
        assert "which is what you are being asked about" in summary


class TestTheDigests:
    def test_the_content_digest_ignores_the_version_and_the_evidence_digest_does_not(self) -> None:
        one = (ntfs(version_id=1),)
        other = (ntfs(version_id=2),)

        assert content_digest(one) == content_digest(other)
        assert evidence_digest(one) != evidence_digest(other)

    def test_the_content_digest_is_order_independent(self) -> None:
        first = ntfs(ace_key="a")
        second = ntfs(ace_key="b", mask=MODIFY)

        assert content_digest((first, second)) == content_digest((second, first))

    def test_it_still_covers_every_field_that_decides_what_a_grant_is(self) -> None:
        base = ntfs()

        for other in (
            ntfs(mask=MODIFY),
            ntfs(ace_type=AceType.DENY),
            ntfs(flags=0x13),
            ntfs(source=AceSource.INHERITED),
            ntfs(order=9),
        ):
            assert content_digest((base,)) != content_digest((other,))

    def test_a_share_grant_digests_by_its_permission_level(self) -> None:
        """Share entries carry a named level instead of a mask, and the level is the grant."""
        assert content_digest((share(permission=SharePermission.READ),)) != content_digest(
            (share(permission=SharePermission.FULL),)
        )


class TestWhatTheComparisonCarriesForward:
    def test_the_baseline_digest_is_the_item_s_own(self) -> None:
        baseline = (ntfs(), ntfs(ace_key="second", mask=MODIFY))

        drift = compare_grants(baseline, baseline, target_present=True)

        assert drift.baseline_digest == evidence_digest(baseline)

    def test_the_current_certainty_is_the_weakest_of_the_current_entries(self) -> None:
        now = (
            ntfs(ace_key="a", certainty=Certainty.OBSERVED),
            ntfs(ace_key="b", certainty=Certainty.BACKFILLED, mask=MODIFY),
        )

        drift = compare_grants((ntfs(),), now, target_present=True)

        assert drift.current_certainty is Certainty.BACKFILLED

    def test_a_removal_reports_no_certainty_rather_than_observed(self) -> None:
        """``weakest_certainty`` answers ``observed`` for an empty set, which is right for an
        item — it always has a grant — and would be a claim about an absence here."""
        drift = compare_grants((ntfs(),), (), target_present=True)

        assert drift.current_certainty is None
