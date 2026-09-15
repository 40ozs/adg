"""Comparing two answers, and the caveats that stop a comparison being read too strongly."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.access_engine import (
    AccessCertainty,
    AccessCondition,
    AccessFinding,
    AccessPath,
    EffectiveAccess,
    ResourceDacl,
    RightsMask,
    ShareDacl,
    SubjectFacts,
    build_token,
    ntfs_entry,
    resolve_access,
)
from app.domain import AceType, PrincipalKind
from app.simulation import ImpactDirection, SimulationCaveat, classify, compare_access
from app.simulation.impact import deltas_by_severity, unchanged_pair
from tests.support.simulation import ALICE, FINANCE, MODIFY, READ_EXECUTE

FINANCE_KEY = FINANCE.casefold()


def answer(
    mask: int,
    *,
    path: AccessPath = AccessPath.LOCAL,
    findings: Sequence[AccessFinding] = (),
    share_observed: bool = True,
) -> EffectiveAccess:
    """One effective-access answer, produced by the real resolver.

    Built through :func:`app.access_engine.resolve_access` rather than by constructing an
    :class:`EffectiveAccess` by hand, so the certainties these tests reason about are the
    ones the engine actually derives.
    """
    # The kind matters: a subject nothing describes raises ``SUBJECT_UNRESOLVED``, which is
    # an uncertainty in both directions and would make every answer here uncertain before the
    # caveat under test could be reached.
    token = build_token(
        SubjectFacts(key=ALICE, sid=ALICE, kind=PrincipalKind.USER), (), access_path=path
    )
    entries = (
        ()
        if mask == 0
        else (
            ntfs_entry(
                trustee_key=ALICE,
                trustee_sid=ALICE,
                ace_type=AceType.ALLOW,
                access_mask=mask,
                order_index=0,
            ),
        )
    )
    dacl = ResourceDacl(resource_key=FINANCE_KEY, entries=entries)
    share = (
        ShareDacl(share_key="fs01|finance", observed=share_observed)
        if path is AccessPath.REMOTE_SMB
        else None
    )
    return resolve_access(token, dacl, share, findings=findings)


UNOBSERVED_GROUP = (
    AccessFinding(AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED, {"trustee_key": "g"}),
)


class TestClassification:
    @pytest.mark.parametrize(
        ("before", "after", "expected"),
        [
            (0, 0, ImpactDirection.UNCHANGED),
            (MODIFY, MODIFY, ImpactDirection.UNCHANGED),
            (0, READ_EXECUTE, ImpactDirection.GAINED_ACCESS),
            (READ_EXECUTE, 0, ImpactDirection.LOST_ACCESS),
            (READ_EXECUTE, MODIFY, ImpactDirection.EXPANDED),
            (MODIFY, READ_EXECUTE, ImpactDirection.REDUCED),
        ],
    )
    def test_the_six_directions(self, before, after, expected):
        assert classify(RightsMask.effective(before), RightsMask.effective(after)) is expected

    def test_gaining_some_rights_and_losing_others_is_its_own_finding(self):
        """Neither "expanded" nor "reduced" describes a rewritten ACE."""
        assert (
            classify(RightsMask.effective(0x00000001), RightsMask.effective(0x00000002))
            is ImpactDirection.CHANGED
        )

    def test_appearing_from_nowhere_is_gaining_access_not_an_expansion_from_zero(self):
        """The distinction an impact list is read for: a new person in the room."""
        assert (
            classify(RightsMask.effective(0), RightsMask.effective(MODIFY))
            is ImpactDirection.GAINED_ACCESS
        )


class TestGenericRights:
    def test_the_same_access_written_two_ways_is_not_reported_as_a_change(self):
        """``GENERIC_READ`` and the specific bits it maps to are the same permission.

        Subtracting the raw masks would report a change that does not exist.
        """
        generic = answer(0x80000000)
        specific = answer(generic.rights.expand_generics().value)

        delta = compare_access(ALICE, generic, specific)

        assert delta.direction is ImpactDirection.UNCHANGED
        assert delta.rights_added.is_empty
        assert delta.rights_removed.is_empty


class TestCaveats:
    def test_a_certain_pair_carries_none(self):
        delta = compare_access(ALICE, answer(MODIFY), answer(READ_EXECUTE))

        assert delta.before.certainty is AccessCertainty.CERTAIN
        assert delta.caveats == ()

    def test_a_claimed_loss_over_a_lower_bound_is_flagged(self):
        """``AT_LEAST`` means the truth could be wider than reported.

        Exactly the case where a right reported as removed is in fact retained — and the one
        caveat that stands between a report and a remediation that achieves nothing.
        """
        before = answer(MODIFY, findings=UNOBSERVED_GROUP)
        after = answer(READ_EXECUTE, findings=UNOBSERVED_GROUP)

        delta = compare_access(ALICE, before, after)

        assert after.certainty is AccessCertainty.AT_LEAST
        assert SimulationCaveat.LOSS_MAY_NOT_HOLD in delta.caveats
        assert SimulationCaveat.GAIN_MAY_NOT_HOLD not in delta.caveats

    def test_a_claimed_gain_over_an_upper_bound_is_flagged(self):
        """An unread share ACL can only narrow what NTFS grants."""
        before = answer(0, path=AccessPath.REMOTE_SMB, share_observed=False)
        after = answer(MODIFY, path=AccessPath.REMOTE_SMB, share_observed=False)

        delta = compare_access(ALICE, before, after)

        assert after.certainty is AccessCertainty.AT_MOST
        assert SimulationCaveat.GAIN_MAY_NOT_HOLD in delta.caveats
        assert SimulationCaveat.LOSS_MAY_NOT_HOLD not in delta.caveats

    def test_an_uncertain_baseline_is_reported_even_when_nothing_moved(self):
        """An unchanged verdict over uncertain inputs is not proof that nothing changed."""
        both = answer(MODIFY, findings=UNOBSERVED_GROUP)

        delta = compare_access(ALICE, both, both)

        assert delta.direction is ImpactDirection.UNCHANGED
        assert SimulationCaveat.BASELINE_UNCERTAIN in delta.caveats
        assert SimulationCaveat.SIMULATED_UNCERTAIN in delta.caveats


class TestGuards:
    def test_two_answers_about_different_resources_cannot_be_compared(self):
        """A delta between two different questions is not a delta."""
        other = resolve_access(
            build_token(
                SubjectFacts(key=ALICE, sid=ALICE, kind=None), (), access_path=AccessPath.LOCAL
            ),
            ResourceDacl(resource_key="\\\\fs01\\other"),
        )

        with pytest.raises(ValueError, match="same question"):
            compare_access(ALICE, answer(MODIFY), other)


class TestOrdering:
    def test_the_consequential_rows_come_first(self):
        """A reviewer must not have to scroll past a gain to find it."""
        gained = compare_access(ALICE, answer(0), answer(MODIFY))
        lost = compare_access(ALICE, answer(MODIFY), answer(0))
        unchanged = compare_access(ALICE, answer(MODIFY), answer(MODIFY))

        ordered = deltas_by_severity([unchanged, lost, gained])

        assert [delta.direction for delta in ordered] == [
            ImpactDirection.GAINED_ACCESS,
            ImpactDirection.LOST_ACCESS,
            ImpactDirection.UNCHANGED,
        ]


class TestUnchangedPairs:
    def test_one_answer_stands_for_both_sides(self):
        one = answer(MODIFY)

        delta = unchanged_pair(ALICE, FINANCE_KEY, None, AccessPath.LOCAL, one)

        assert delta.direction is ImpactDirection.UNCHANGED
        assert delta.before is delta.after is one
        assert delta.rights_added.is_empty

    def test_certainty_still_qualifies_it(self):
        delta = unchanged_pair(
            ALICE,
            FINANCE_KEY,
            None,
            AccessPath.LOCAL,
            answer(MODIFY, findings=UNOBSERVED_GROUP),
        )

        assert SimulationCaveat.BASELINE_UNCERTAIN in delta.caveats
