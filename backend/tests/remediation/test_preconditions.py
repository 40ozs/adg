"""The stale-state check: four verdicts, and why three of them are not one.

The distinction this suite exists to protect is between **missing** and **unobserved**. Both
are an absence in the data and they lead to opposite conclusions: one means somebody may
already have done the work, the other means nobody has looked. A plan skipped on the first
reading when the second was true is a removal that never happened, recorded as done.
"""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

from app.domain import AceSource, AceType
from app.domain.remediation import PlannedChangeKind, PreconditionVerdict
from app.remediation.preconditions import (
    ObservedState,
    evaluate_change,
    evaluate_plan,
    summarize_verdict,
)
from tests.remediation import factories as f

NOW = f.NOW


class TestTheFourVerdicts:
    def test_an_identical_entry_is_satisfied(self) -> None:
        change = f.change()

        verdict = evaluate_change(change, ObservedState(target_observed=True, entry=f.entry()))

        assert verdict.verdict is PreconditionVerdict.SATISFIED
        assert not verdict.blocks_export

    def test_a_widened_entry_is_changed_and_blocks(self) -> None:
        """The case the whole check exists for: the mask being removed is not the mask that
        was reviewed."""
        change = f.change(entry=f.entry(access_mask=f.READ_MASK))

        verdict = evaluate_change(
            change,
            ObservedState(target_observed=True, entry=f.entry(access_mask=f.FULL_MASK)),
        )

        assert verdict.verdict is PreconditionVerdict.CHANGED
        assert verdict.blocks_export
        assert "edited since the plan was written" in verdict.summary

    def test_a_changed_entry_names_the_field_that_moved(self) -> None:
        change = f.change(entry=f.entry(access_mask=f.READ_MASK))

        verdict = evaluate_change(
            change,
            ObservedState(target_observed=True, entry=f.entry(access_mask=f.FULL_MASK)),
        )

        assert "access_mask" in verdict.summary
        assert verdict.detail["differences"]["access_mask"] == (f.READ_MASK, f.FULL_MASK)

    def test_an_absent_entry_on_an_observed_target_is_missing(self) -> None:
        verdict = evaluate_change(f.change(), ObservedState(target_observed=True, entry=None))

        assert verdict.verdict is PreconditionVerdict.MISSING
        assert verdict.blocks_export

    def test_an_unobserved_target_is_not_reported_as_missing(self) -> None:
        verdict = evaluate_change(f.change(), ObservedState(target_observed=False))

        assert verdict.verdict is PreconditionVerdict.UNOBSERVED
        assert verdict.blocks_export

    def test_missing_and_unobserved_never_read_alike(self) -> None:
        """A renderer mapping four verdicts to three colors would collapse these two."""
        gone = evaluate_change(f.change(), ObservedState(target_observed=True, entry=None))
        unseen = evaluate_change(f.change(), ObservedState(target_observed=False))

        assert gone.summary != unseen.summary
        assert "nobody has looked" in unseen.summary
        assert "already" in gone.summary

    def test_every_verdict_but_satisfied_blocks_an_export(self) -> None:
        for verdict in PreconditionVerdict:
            assert verdict.blocks_export == (verdict is not PreconditionVerdict.SATISFIED)


class TestTheComparisonIsOnContent:
    def test_a_new_version_of_an_identical_entry_is_still_satisfied(self) -> None:
        """An entry removed and restored unchanged takes a new ``object_versions`` row.
        Comparing on identity would refuse the plan over a change that did not happen."""
        change = f.change(entry=f.entry(version_id=7))

        verdict = evaluate_change(
            change, ObservedState(target_observed=True, entry=f.entry(version_id=99))
        )

        assert verdict.verdict is PreconditionVerdict.SATISFIED

    def test_a_reordered_entry_is_changed(self) -> None:
        """A Deny moved behind an Allow changes what the DACL does without changing any ACE."""
        change = f.change(entry=f.entry(order_index=0))

        verdict = evaluate_change(
            change, ObservedState(target_observed=True, entry=f.entry(order_index=4))
        )

        assert verdict.verdict is PreconditionVerdict.CHANGED

    def test_an_entry_that_became_inherited_is_changed(self) -> None:
        """Where it is removed from depends on it."""
        change = f.change(entry=f.entry(source=AceSource.EXPLICIT))

        verdict = evaluate_change(
            change,
            ObservedState(
                target_observed=True,
                entry=f.entry(source=AceSource.INHERITED, inherited_from=f.FINANCE, ace_flags=0x13),
            ),
        )

        assert verdict.verdict is PreconditionVerdict.CHANGED

    def test_an_allow_that_became_a_deny_is_changed(self) -> None:
        change = f.change(entry=f.entry(ace_type=AceType.ALLOW))

        verdict = evaluate_change(
            change, ObservedState(target_observed=True, entry=f.entry(ace_type=AceType.DENY))
        )

        assert verdict.verdict is PreconditionVerdict.CHANGED


class TestAMembershipChange:
    def test_a_present_edge_is_satisfied(self) -> None:
        change = f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER)

        verdict = evaluate_change(
            change, ObservedState(target_observed=True, membership_present=True)
        )

        assert verdict.verdict is PreconditionVerdict.SATISFIED
        assert "still a member of" in verdict.summary

    def test_a_removed_edge_is_missing(self) -> None:
        change = f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER)

        verdict = evaluate_change(
            change, ObservedState(target_observed=True, membership_present=False)
        )

        assert verdict.verdict is PreconditionVerdict.MISSING
        assert "no longer a member" in verdict.summary

    def test_an_unknown_group_is_unobserved_rather_than_missing(self) -> None:
        change = f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER)

        verdict = evaluate_change(change, ObservedState(target_observed=False))

        assert verdict.verdict is PreconditionVerdict.UNOBSERVED


class TestAWholePlan:
    def test_a_change_with_no_observation_is_treated_as_unobserved(self) -> None:
        """A missing key in the mapping is exactly the shape of bug that would quietly reduce
        a plan's checks to the subset a query happened to return."""
        change = f.change()

        report = evaluate_plan(
            uuid4(),
            [change],
            {},
            checked_at=NOW,
            basis_token="a" * 32,
            plan_basis_token="a" * 32,
        )

        assert report.preconditions[0].verdict is PreconditionVerdict.UNOBSERVED
        assert not report.satisfied

    def test_one_failure_is_enough_to_block(self) -> None:
        good = f.change(index=0, entry=f.entry(ace_key="a"))
        bad = f.change(index=1, entry=f.entry(ace_key="b"))

        report = evaluate_plan(
            uuid4(),
            [good, bad],
            {
                good.change_id: ObservedState(target_observed=True, entry=f.entry(ace_key="a")),
                bad.change_id: ObservedState(target_observed=True, entry=None),
            },
            checked_at=NOW,
            basis_token="a" * 32,
            plan_basis_token="a" * 32,
        )

        assert not report.satisfied
        assert [item.sequence_index for item in report.blocking] == [1]

    def test_a_report_is_ordered_by_step_number(self) -> None:
        first = f.change(index=0, entry=f.entry(ace_key="a"))
        second = f.change(index=1, entry=f.entry(ace_key="b"))

        report = evaluate_plan(
            uuid4(),
            [second, first],
            {},
            checked_at=NOW,
            basis_token="a" * 32,
            plan_basis_token="a" * 32,
        )

        assert [item.sequence_index for item in report.preconditions] == [0, 1]

    def test_a_moved_basis_is_reported_beside_the_verdicts_not_folded_into_them(self) -> None:
        """A scan that touched a different server moves the token and changes nothing this
        plan names. Folding it in would refuse every plan after every scan."""
        change = f.change()

        report = evaluate_plan(
            uuid4(),
            [change],
            {change.change_id: ObservedState(target_observed=True, entry=f.entry())},
            checked_at=NOW,
            basis_token="b" * 32,
            plan_basis_token="a" * 32,
        )

        assert report.basis_moved
        assert report.satisfied

    def test_the_counts_name_every_verdict(self) -> None:
        report = evaluate_plan(
            uuid4(),
            [f.change()],
            {},
            checked_at=NOW,
            basis_token="a" * 32,
            plan_basis_token="a" * 32,
        )

        assert set(report.counts()) == {verdict.value for verdict in PreconditionVerdict}


class TestTheSummarySentence:
    def test_a_satisfied_plan_says_so(self) -> None:
        change = f.change()
        report = evaluate_plan(
            uuid4(),
            [change],
            {change.change_id: ObservedState(target_observed=True, entry=f.entry())},
            checked_at=NOW,
            basis_token="a" * 32,
            plan_basis_token="a" * 32,
        )

        assert "still match" in summarize_verdict(report)

    def test_a_blocked_plan_says_how_many_and_which_way(self) -> None:
        """ "Preconditions failed" leaves an operator unable to tell one moved entry from a
        rebuilt share."""
        change = f.change()
        report = evaluate_plan(
            uuid4(),
            [change],
            {change.change_id: ObservedState(target_observed=True, entry=None)},
            checked_at=NOW,
            basis_token="a" * 32,
            plan_basis_token="a" * 32,
        )

        sentence = summarize_verdict(report)
        assert "1 of 1" in sentence
        assert "missing" in sentence

    def test_a_satisfied_plan_whose_basis_moved_says_that_too(self) -> None:
        change = f.change()
        report = evaluate_plan(
            uuid4(),
            [change],
            {change.change_id: ObservedState(target_observed=True, entry=f.entry())},
            checked_at=NOW,
            basis_token="b" * 32,
            plan_basis_token="a" * 32,
        )

        assert "none of what moved touches this plan" in summarize_verdict(report)


class TestTheReportIsSerializable:
    def test_every_field_survives_the_document(self) -> None:
        change = f.change()
        report = evaluate_plan(
            uuid4(),
            [change],
            {change.change_id: ObservedState(target_observed=True, entry=f.entry())},
            checked_at=dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC),
            basis_token="a" * 32,
            plan_basis_token="a" * 32,
        )

        document = report.document()

        assert document["satisfied"] is True
        assert document["checked_at"].startswith("2026-09-14T12:00:00")
        assert len(document["preconditions"]) == 1
