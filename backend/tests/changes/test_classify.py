r"""Two versions in, one classified change out — and the four ways that can go wrong.

The cases here are chosen for what they would cost if they were decided the other way:

* calling a first sighting a creation reports an estate as having been built on a Tuesday;
* calling a gap a removal revokes access nobody revoked;
* calling an unclassifiable change harmless hides ADG's own blind spot in the one place it
  matters;
* dating a change to ``valid_from`` dates every incident to a scan schedule.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.changes.classify import (
    action_for,
    classify,
    deltas_between,
    significance_for,
    window_between,
)
from app.changes.model import (
    ChangeAction,
    ChangeDirection,
    ChangeSeverity,
    ChangeSignificance,
    FieldSignificance,
)
from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError
from app.history.model import ObjectVersion, VersionOrigin
from tests.changes import factories as f


class TestDeltas:
    def test_an_unchanged_field_is_not_a_delta(self) -> None:
        before = f.principal_state()
        after = f.principal_state()
        assert deltas_between(ObservationKind.PRINCIPAL, before, after) == ()

    def test_a_field_present_on_one_side_only_is_a_delta_against_none(self) -> None:
        """A collector that starts sending a column has changed what ADG knows."""
        before = f.principal_state()
        after = {**f.principal_state(), "display_name": None}
        del before["display_name"]
        deltas = deltas_between(ObservationKind.PRINCIPAL, before, after)
        assert [delta.field for delta in deltas] == []  # both are None: still not a delta

        after_named = {**before, "display_name": "Alice"}
        deltas = deltas_between(ObservationKind.PRINCIPAL, before, after_named)
        assert [(d.field, d.before, d.after) for d in deltas] == [("display_name", None, "Alice")]

    def test_each_delta_carries_what_the_field_means(self) -> None:
        deltas = deltas_between(
            ObservationKind.PRINCIPAL,
            f.principal_state(enabled=False, display_name="Alice"),
            f.principal_state(enabled=True, display_name="Alice Smith"),
        )
        by_field = {delta.field: delta.significance for delta in deltas}
        assert by_field["enabled"] is FieldSignificance.SECURITY
        assert by_field["display_name"] is FieldSignificance.METADATA

    def test_a_delta_with_equal_sides_is_refused(self) -> None:
        from app.changes.model import FieldDelta

        with pytest.raises(DomainValidationError, match="equal values on both sides"):
            FieldDelta(
                field="enabled", before=True, after=True, significance=FieldSignificance.SECURITY
            )


class TestTheActionIsNotGuessed:
    def test_a_tombstone_is_a_removal(self) -> None:
        before = f.ntfs_ace()
        gone = f.tombstone(ObservationKind.NTFS_ACE, before.key, container_key=f.FINANCE)
        assert action_for(before, gone) is ChangeAction.REMOVED

    def test_a_first_version_inside_a_watched_container_is_an_addition(self) -> None:
        assert action_for(None, f.ntfs_ace(), container_observed_before=True) is ChangeAction.ADDED

    def test_a_first_version_with_no_watched_container_is_a_first_sighting(self) -> None:
        """Not an addition. ADG started looking; that is all this records."""
        assert (
            action_for(None, f.ntfs_ace(), container_observed_before=False)
            is ChangeAction.FIRST_OBSERVED
        )

    def test_an_unanswerable_container_question_is_a_first_sighting(self) -> None:
        """``None`` is unanswerable and resolves to the claim that asserts less."""
        assert (
            action_for(None, f.ntfs_ace(), container_observed_before=None)
            is ChangeAction.FIRST_OBSERVED
        )

    def test_a_revival_after_a_tombstone_is_an_addition_without_asking(self) -> None:
        """The only creation ADG can prove: somebody looked, it was gone, then it was back."""
        gone = f.tombstone(ObservationKind.NTFS_ACE, "x", valid_from=f.WEDNESDAY)
        back = f.ntfs_ace(valid_from=f.FRIDAY)
        assert action_for(gone, back, container_observed_before=None) is ChangeAction.ADDED

    def test_two_present_versions_are_a_modification(self) -> None:
        before = f.principal(enabled=False, valid_from=f.MONDAY, valid_to=f.FRIDAY)
        after = f.principal(enabled=True, valid_from=f.FRIDAY)
        assert action_for(before, after) is ChangeAction.MODIFIED


class TestTheWindowCarriesBothEnds:
    def test_it_runs_from_the_last_confirmation_to_the_contradiction(self) -> None:
        """ADR-0019. Not ``valid_to``, which is merely the day somebody looked."""
        before = f.principal(
            enabled=False, valid_from=f.MONDAY, last_seen_at=f.WEDNESDAY, valid_to=f.FRIDAY
        )
        after = f.principal(enabled=True, valid_from=f.FRIDAY)
        window = window_between(before, after)
        assert window is not None
        assert window.after == f.WEDNESDAY
        assert window.at_or_before == f.FRIDAY
        assert not window.is_exact

    def test_it_collapses_when_two_observations_bracket_the_change(self) -> None:
        before = f.principal(
            enabled=False, valid_from=f.MONDAY, last_seen_at=f.FRIDAY, valid_to=f.FRIDAY
        )
        after = f.principal(enabled=True, valid_from=f.FRIDAY)
        window = window_between(before, after)
        assert window is not None and window.is_exact

    def test_an_addition_is_bounded_by_the_last_reading_of_its_container(self) -> None:
        """An ACE's rights are part of its identity, so an entry that was added has no
        predecessor of its own — and the ACL it joined was read at a known instant.

        Without this, every ACL addition would carry only ``at``, which is the instant
        somebody looked, and rendering that as the change time is the model ADR-0019
        rejected.
        """
        window = window_between(
            None, f.ntfs_ace(valid_from=f.FRIDAY), container_confirmed_at=f.WEDNESDAY
        )
        assert window is not None
        assert window.after == f.WEDNESDAY
        assert window.at_or_before == f.FRIDAY

    def test_there_is_no_window_when_nothing_bounds_it(self) -> None:
        """Neither a predecessor nor a container reading. An invented lower bound reads
        exactly like a measured one."""
        assert window_between(None, f.ntfs_ace()) is None

    def test_a_first_sighting_with_nothing_to_bound_it_has_no_window(self) -> None:
        """A first observation is precisely the case where the container was not being
        watched, so a bound from it would be a contradiction rather than a refinement."""
        change = classify(
            ObservationKind.NTFS_ACE,
            "k",
            None,
            f.ntfs_ace(valid_from=f.FRIDAY),
            container_observed_before=False,
        )
        assert change.action is ChangeAction.FIRST_OBSERVED
        assert change.window is None

    def test_a_first_sighting_is_refused_a_window_even_when_one_is_offered(self) -> None:
        """The case the release audit found, and the one the test above only appeared to cover.

        The two container facts answer different questions and can disagree: nothing was
        watching the container *object*, yet a *sibling* inside it was confirmed earlier. The
        classifier built a change that its own invariant rejects, which surfaced as a 422
        from ``GET /api/v1/changes/compare`` on an ordinary estate.

        Offering the confirmation is the whole point of this test. Without it the call is
        indistinguishable from the one above, which is why this went unnoticed.
        """
        change = classify(
            ObservationKind.NTFS_ACE,
            "k",
            None,
            f.ntfs_ace(valid_from=f.FRIDAY),
            container_observed_before=False,
            container_confirmed_at=f.WEDNESDAY,
        )
        assert change.action is ChangeAction.FIRST_OBSERVED
        assert change.window is None

    def test_an_unanswerable_container_is_refused_a_window_too(self) -> None:
        """Same disagreement, reached the other way.

        ``None`` means unanswerable, and ``action_for`` reads it as "not observed before" —
        but a container nothing has ever described still has siblings that were read.
        """
        change = classify(
            ObservationKind.NTFS_ACE,
            "k",
            None,
            f.ntfs_ace(valid_from=f.FRIDAY),
            container_observed_before=None,
            container_confirmed_at=f.WEDNESDAY,
        )
        assert change.action is ChangeAction.FIRST_OBSERVED
        assert change.window is None

    def test_an_addition_inside_a_watched_container_reports_one(self) -> None:
        change = classify(
            ObservationKind.NTFS_ACE,
            "k",
            None,
            f.ntfs_ace(valid_from=f.FRIDAY),
            container_observed_before=True,
            container_confirmed_at=f.WEDNESDAY,
        )
        assert change.action is ChangeAction.ADDED
        assert change.window is not None and change.window.after == f.WEDNESDAY

    def test_a_change_with_an_earlier_version_must_carry_one(self) -> None:
        before = f.principal(enabled=False, valid_from=f.MONDAY, valid_to=f.FRIDAY)
        after = f.principal(enabled=True, valid_from=f.FRIDAY)
        change = classify(ObservationKind.PRINCIPAL, after.key, before, after)
        assert change.window is not None

    def test_the_sort_key_is_named_for_what_it_is(self) -> None:
        """``at`` is when somebody looked. The feed pages on it; a client renders ``window``."""
        after = f.principal(enabled=True, valid_from=f.FRIDAY)
        change = classify(ObservationKind.PRINCIPAL, after.key, None, after)
        assert change.at == f.FRIDAY


class TestSignificance:
    def test_an_object_appearing_or_disappearing_is_always_security(self) -> None:
        for action in (ChangeAction.ADDED, ChangeAction.REMOVED, ChangeAction.FIRST_OBSERVED):
            assert (
                significance_for(action, (), ordering_material=None) is ChangeSignificance.SECURITY
            )

    def test_a_metadata_only_modification_is_metadata(self) -> None:
        deltas = deltas_between(
            ObservationKind.PRINCIPAL,
            f.principal_state(display_name="Alice"),
            f.principal_state(display_name="Alice Smith"),
        )
        assert (
            significance_for(ChangeAction.MODIFIED, deltas, ordering_material=None)
            is ChangeSignificance.METADATA
        )

    def test_one_security_field_makes_the_whole_change_security(self) -> None:
        deltas = deltas_between(
            ObservationKind.PRINCIPAL,
            f.principal_state(enabled=True, display_name="Alice"),
            f.principal_state(enabled=False, display_name="Alice Smith"),
        )
        assert (
            significance_for(ChangeAction.MODIFIED, deltas, ordering_material=None)
            is ChangeSignificance.SECURITY
        )

    def test_an_unclassified_field_makes_it_undetermined(self) -> None:
        from app.changes.model import FieldDelta

        deltas = (
            FieldDelta(
                field="something_new",
                before=None,
                after=1,
                significance=FieldSignificance.UNCLASSIFIED,
            ),
        )
        assert (
            significance_for(ChangeAction.MODIFIED, deltas, ordering_material=None)
            is ChangeSignificance.UNDETERMINED
        )

    @pytest.mark.parametrize(
        ("material", "expected"),
        [
            (True, ChangeSignificance.SECURITY),
            (False, ChangeSignificance.NOISE),
            (None, ChangeSignificance.UNDETERMINED),
        ],
    )
    def test_an_ordering_change_waits_for_the_acl_comparison(
        self, material: bool | None, expected: ChangeSignificance
    ) -> None:
        """ "We could not check" and "we checked and it was nothing" are different answers."""
        deltas = deltas_between(
            ObservationKind.NTFS_ACE,
            f.ntfs_ace_state(order_index=3),
            f.ntfs_ace_state(order_index=2),
        )
        assert significance_for(ChangeAction.MODIFIED, deltas, ordering_material=material) is (
            expected
        )


class TestTheWholeClassification:
    def test_a_first_sighting_is_never_scored_however_alarming_it_looks(self) -> None:
        """An Everyone/Full-Control ACE ADG has only just seen may be six years old.

        That is a risk, which Phase 8 scores. Calling it a critical *change* would make an
        estate's first scan a page of incidents and teach the reader to skip the column.
        """
        change = classify(
            ObservationKind.NTFS_ACE,
            "k",
            None,
            f.ntfs_ace(trustee=f.EVERYONE, access_mask=f.FULL_CONTROL),
            container_observed_before=False,
        )
        assert change.action is ChangeAction.FIRST_OBSERVED
        assert change.severity is ChangeSeverity.INFO
        assert change.rule_ids == ("first_observed",)

    def test_the_same_ace_added_to_a_watched_directory_is_critical(self) -> None:
        change = classify(
            ObservationKind.NTFS_ACE,
            "k",
            None,
            f.ntfs_ace(trustee=f.EVERYONE, access_mask=f.FULL_CONTROL),
            container_observed_before=True,
        )
        assert change.action is ChangeAction.ADDED
        assert change.severity is ChangeSeverity.CRITICAL
        assert change.direction is ChangeDirection.BROADENED

    def test_a_classification_is_a_function_of_its_inputs_alone(self) -> None:
        """Run twice, same answer. A severity that could vary cannot be quoted in a finding."""
        before = f.principal(enabled=False, valid_from=f.MONDAY, valid_to=f.FRIDAY)
        after = f.principal(enabled=True, valid_from=f.FRIDAY)
        first = classify(ObservationKind.PRINCIPAL, after.key, before, after)
        second = classify(ObservationKind.PRINCIPAL, after.key, before, after)
        assert (first.severity, first.direction, first.reasons) == (
            second.severity,
            second.direction,
            second.reasons,
        )

    def test_a_backfilled_side_marks_the_change_reconstructed(self) -> None:
        before = f.principal(
            enabled=False,
            valid_from=f.MONDAY,
            valid_to=f.FRIDAY,
            origin=VersionOrigin.BACKFILLED,
        )
        after = f.principal(enabled=True, valid_from=f.FRIDAY)
        change = classify(ObservationKind.PRINCIPAL, after.key, before, after)
        assert change.reconstructed

    def test_the_subject_names_the_thing_a_reader_would_go_and_look_at(self) -> None:
        change = classify(
            ObservationKind.NTFS_ACE,
            "k",
            None,
            f.ntfs_ace(trustee=f.ALICE),
            container_observed_before=True,
        )
        assert change.subject.container_key == f.FINANCE
        assert change.subject.container_kind is ObservationKind.NTFS_RESOURCE
        assert change.subject.related_key == f.ALICE
        assert change.subject.related_kind is ObservationKind.PRINCIPAL

    def test_deltas_are_ordered_with_the_dangerous_ones_first(self) -> None:
        before = f.principal(
            enabled=True, display_name="Alice", valid_from=f.MONDAY, valid_to=f.FRIDAY
        )
        after = f.principal(enabled=False, display_name="Alice Smith", valid_from=f.FRIDAY)
        change = classify(ObservationKind.PRINCIPAL, after.key, before, after)
        assert change.significant_deltas()[0].field == "enabled"

    def test_identity_drift_is_surfaced_rather_than_dropped(self) -> None:
        """Two states differing in a key field are two objects: a defect, not a change.

        It is reported at HIGH because the only symptom it has is this line.
        """
        before = f.version(
            ObservationKind.PRINCIPAL,
            f.ALICE,
            f.principal_state(sid=f.ALICE),
            valid_from=f.MONDAY,
            valid_to=f.FRIDAY,
        )
        drifted = f.principal_state(sid=f.ALICE)
        drifted["sid"] = f.FINANCE_RW
        after = f.version(ObservationKind.PRINCIPAL, f.ALICE, drifted, valid_from=f.FRIDAY)
        change = classify(ObservationKind.PRINCIPAL, f.ALICE, before, after)
        assert change.rule_ids == ("identity.drift",)
        assert change.severity is ChangeSeverity.HIGH
        assert [delta.field for delta in change.identity_drift] == ["sid"]


class TestTheChangeRecordRefusesToLie:
    def test_a_first_observation_may_not_follow_an_earlier_version(self) -> None:
        from app.changes.model import ObjectChange, SubjectRef

        before = f.principal(enabled=True, valid_from=f.MONDAY, valid_to=f.FRIDAY)
        after = f.principal(enabled=False, valid_from=f.FRIDAY)
        with pytest.raises(DomainValidationError, match="not a first observation"):
            ObjectChange(
                kind=ObservationKind.PRINCIPAL,
                key=after.key,
                action=ChangeAction.FIRST_OBSERVED,
                window=None,
                before=before,
                after=after,
                deltas=(),
                significance=ChangeSignificance.SECURITY,
                direction=ChangeDirection.NEUTRAL,
                severity=ChangeSeverity.INFO,
                subject=SubjectRef(None, None, None, None),
                reasons=(),
            )

    def test_a_change_following_a_version_must_report_its_window(self) -> None:
        from app.changes.model import ObjectChange, SubjectRef

        before = f.principal(enabled=True, valid_from=f.MONDAY, valid_to=f.FRIDAY)
        after = f.principal(enabled=False, valid_from=f.FRIDAY)
        with pytest.raises(DomainValidationError, match="both ends"):
            ObjectChange(
                kind=ObservationKind.PRINCIPAL,
                key=after.key,
                action=ChangeAction.MODIFIED,
                window=None,
                before=before,
                after=after,
                deltas=(),
                significance=ChangeSignificance.SECURITY,
                direction=ChangeDirection.NEUTRAL,
                severity=ChangeSeverity.INFO,
                subject=SubjectRef(None, None, None, None),
                reasons=(),
            )


def _version_at(moment: dt.datetime) -> ObjectVersion:  # pragma: no cover - readability helper
    return f.principal(valid_from=moment)
