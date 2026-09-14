"""The temporal model, without a database.

Every invariant here is also a check constraint on ``object_versions``. Both exist on
purpose: the constraint is what makes a violation impossible, and these are what say, in a
sentence, what the violation would have broken.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from app.contracts.v1.common import ObservationKind
from app.history.model import (
    HISTORICAL_KINDS,
    PROVENANCE_FIELDS,
    Certainty,
    ChangeWindow,
    CloseReason,
    ObjectTimeline,
    ObjectVersion,
    TemporalInvariantError,
    VersionOrigin,
    canonical_state,
    digestible_state,
    state_digest,
)

MONDAY = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
WEDNESDAY = dt.datetime(2026, 3, 4, 9, 0, tzinfo=dt.UTC)
FRIDAY = dt.datetime(2026, 3, 6, 9, 0, tzinfo=dt.UTC)
NEXT_MONDAY = dt.datetime(2026, 3, 9, 9, 0, tzinfo=dt.UTC)

RUN = uuid.UUID("11111111-1111-1111-1111-111111111111")
LATER_RUN = uuid.UUID("22222222-2222-2222-2222-222222222222")


def version(
    *,
    key: str = "fs01|finance",
    state: dict[str, object] | None = None,
    valid_from: dt.datetime = MONDAY,
    last_seen_at: dt.datetime = WEDNESDAY,
    valid_to: dt.datetime | None = None,
    origin: VersionOrigin = VersionOrigin.OBSERVED,
    is_present: bool = True,
) -> ObjectVersion:
    body = state if state is not None else {"share_key": key, "share_type": "disk"}
    return ObjectVersion(
        kind=ObservationKind.SMB_SHARE,
        key=key,
        is_present=is_present,
        valid_from=valid_from,
        last_seen_at=last_seen_at,
        valid_to=valid_to,
        state=body if is_present else None,
        state_hash=state_digest(body) if is_present else None,
        origin=origin,
        close_reason=None if valid_to is None else CloseReason.SUPERSEDED,
        opened_by_run_id=RUN,
        last_seen_run_id=RUN,
        closed_by_run_id=None if valid_to is None else LATER_RUN,
    )


class TestTheDigestDecidesWhatCountsAsAChange:
    def test_provenance_is_not_part_of_the_state(self) -> None:
        monday = {
            "share_key": "fs01|finance",
            "share_type": "disk",
            "source_key": "run-a|share|finance",
            "last_observed_at": MONDAY,
            "last_observed_run_id": RUN,
            "updated_at": MONDAY,
            "created_at": MONDAY,
            "first_observed_at": MONDAY,
            "first_observed_run_id": RUN,
        }
        friday = {**monday, "source_key": "run-b|share|finance", "last_observed_at": FRIDAY}

        assert state_digest(monday) == state_digest(friday)

    def test_every_provenance_field_is_stripped(self) -> None:
        state = {"share_key": "fs01|finance", **dict.fromkeys(PROVENANCE_FIELDS, "x")}

        assert set(digestible_state(state)) == {"share_key"}

    def test_a_changed_descriptive_value_changes_the_digest(self) -> None:
        before = {"share_key": "fs01|finance", "description": "Finance"}
        after = {"share_key": "fs01|finance", "description": "Finance (archived)"}

        assert state_digest(before) != state_digest(after)

    def test_key_order_does_not_change_the_digest(self) -> None:
        one = {"a": 1, "b": 2, "c": 3}
        other = {"c": 3, "a": 1, "b": 2}

        assert canonical_state(one) == canonical_state(other)

    def test_the_same_instant_in_two_time_zones_digests_identically(self) -> None:
        utc = {"seen": dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)}
        elsewhere = {
            "seen": dt.datetime(2026, 3, 2, 4, 0, tzinfo=dt.timezone(-dt.timedelta(hours=5)))
        }

        assert state_digest(utc) == state_digest(elsewhere)

    def test_a_uuid_survives_a_round_trip_through_json(self) -> None:
        assert digestible_state({"run": RUN}) == {"run": str(RUN)}


class TestCoverage:
    def test_the_interval_is_half_open(self) -> None:
        closed = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=FRIDAY)

        assert closed.covers(MONDAY)
        assert closed.covers(WEDNESDAY)
        assert not closed.covers(FRIDAY)

    def test_the_instant_a_version_closes_is_the_instant_the_next_one_opens(self) -> None:
        earlier = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=FRIDAY)
        later = version(valid_from=FRIDAY, last_seen_at=NEXT_MONDAY)

        assert [earlier.covers(FRIDAY), later.covers(FRIDAY)] == [False, True]

    def test_an_open_version_covers_everything_after_it_begins(self) -> None:
        assert version(valid_from=MONDAY, last_seen_at=WEDNESDAY).covers(NEXT_MONDAY)


class TestCertainty:
    def test_between_two_confirmations_the_answer_was_watched(self) -> None:
        subject = version(valid_from=MONDAY, last_seen_at=FRIDAY)

        assert subject.certainty_at(WEDNESDAY) is Certainty.OBSERVED

    def test_after_the_last_confirmation_the_answer_is_inferred(self) -> None:
        subject = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=NEXT_MONDAY)

        assert subject.certainty_at(FRIDAY) is Certainty.INFERRED

    def test_a_backfilled_version_never_reports_more_than_backfilled(self) -> None:
        subject = version(valid_from=MONDAY, last_seen_at=FRIDAY, origin=VersionOrigin.BACKFILLED)

        assert subject.certainty_at(WEDNESDAY) is Certainty.BACKFILLED

    def test_an_instant_the_version_does_not_cover_is_unobserved(self) -> None:
        subject = version(valid_from=WEDNESDAY, last_seen_at=FRIDAY)

        assert subject.certainty_at(MONDAY) is Certainty.UNOBSERVED


class TestTheChangeWindow:
    def test_a_closed_version_reports_the_interval_the_change_happened_in(self) -> None:
        subject = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=FRIDAY)
        window = subject.change_window

        assert window == ChangeWindow(after=WEDNESDAY, at_or_before=FRIDAY)
        assert window is not None and window.duration == dt.timedelta(days=2)

    def test_two_observations_that_bracket_a_change_exactly_report_no_ignorance(self) -> None:
        subject = version(valid_from=MONDAY, last_seen_at=FRIDAY, valid_to=FRIDAY)
        window = subject.change_window

        assert window is not None and window.is_exact

    def test_an_open_version_has_no_change_window(self) -> None:
        assert version().change_window is None

    def test_the_window_excludes_the_last_confirmation_and_includes_the_contradiction(
        self,
    ) -> None:
        window = ChangeWindow(after=WEDNESDAY, at_or_before=FRIDAY)

        assert not window.contains(WEDNESDAY)
        assert window.contains(FRIDAY)


class TestTheInvariants:
    def test_a_version_confirmed_before_it_began_is_refused(self) -> None:
        with pytest.raises(TemporalInvariantError, match="before it began"):
            version(valid_from=FRIDAY, last_seen_at=MONDAY).validate()

    def test_a_version_closed_before_its_newest_confirmation_is_refused(self) -> None:
        with pytest.raises(TemporalInvariantError, match="still observing it"):
            version(valid_from=MONDAY, last_seen_at=FRIDAY, valid_to=WEDNESDAY).validate()

    def test_a_closed_version_must_say_why(self) -> None:
        subject = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=FRIDAY)
        without_reason = ObjectVersion(**{**vars_of(subject), "close_reason": None})

        with pytest.raises(TemporalInvariantError, match="must say why"):
            without_reason.validate()

    def test_a_closed_version_must_name_the_run_that_closed_it(self) -> None:
        subject = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=FRIDAY)
        unattributed = ObjectVersion(**{**vars_of(subject), "closed_by_run_id": None})

        with pytest.raises(TemporalInvariantError, match="attributable"):
            unattributed.validate()

    def test_a_tombstone_carries_no_state(self) -> None:
        subject = version(is_present=False)
        contradictory = ObjectVersion(**{**vars_of(subject), "state": {"share_key": "x"}})

        with pytest.raises(TemporalInvariantError, match="tombstone"):
            contradictory.validate()

    def test_a_digest_that_is_not_the_digest_of_its_own_state_is_refused(self) -> None:
        subject = version()
        mismatched = ObjectVersion(**{**vars_of(subject), "state_hash": "0" * 64})

        with pytest.raises(TemporalInvariantError, match="not the digest"):
            mismatched.validate()

    def test_a_naive_instant_is_refused(self) -> None:
        subject = version()
        naive = ObjectVersion(**{**vars_of(subject), "valid_from": MONDAY.replace(tzinfo=None)})

        with pytest.raises(TemporalInvariantError, match="timezone-aware"):
            naive.validate()

    def test_a_valid_version_validates(self) -> None:
        assert version().validate() is not None


class TestTheTimeline:
    def test_versions_may_not_overlap(self) -> None:
        first = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=NEXT_MONDAY)
        second = version(valid_from=FRIDAY, last_seen_at=NEXT_MONDAY)

        with pytest.raises(TemporalInvariantError, match="overlap"):
            ObjectTimeline(kind=first.kind, key=first.key, versions=(first, second))

    def test_an_open_version_must_be_the_last_one(self) -> None:
        open_first = version(valid_from=MONDAY, last_seen_at=WEDNESDAY)
        after = version(valid_from=FRIDAY, last_seen_at=NEXT_MONDAY)

        with pytest.raises(TemporalInvariantError, match="At most one version"):
            ObjectTimeline(kind=open_first.kind, key=open_first.key, versions=(open_first, after))

    def test_it_answers_which_version_held_at_an_instant(self) -> None:
        first = version(valid_from=MONDAY, last_seen_at=MONDAY, valid_to=WEDNESDAY)
        second = version(valid_from=WEDNESDAY, last_seen_at=NEXT_MONDAY)
        timeline = ObjectTimeline(kind=first.kind, key=first.key, versions=(first, second))

        assert timeline.at(MONDAY) is first
        assert timeline.at(FRIDAY) is second
        assert timeline.at(MONDAY - dt.timedelta(days=1)) is None

    def test_the_start_of_a_state_is_bounded_by_its_predecessor(self) -> None:
        first = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=FRIDAY)
        second = version(valid_from=FRIDAY, last_seen_at=NEXT_MONDAY)
        timeline = ObjectTimeline(kind=first.kind, key=first.key, versions=(first, second))

        assert timeline.opened_window(second) == ChangeWindow(after=WEDNESDAY, at_or_before=FRIDAY)

    def test_the_first_version_has_no_lower_bound_and_says_so(self) -> None:
        first = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=FRIDAY)
        timeline = ObjectTimeline(kind=first.kind, key=first.key, versions=(first,))

        assert timeline.opened_window(first) is None

    def test_the_current_version_is_the_open_one(self) -> None:
        closed = version(valid_from=MONDAY, last_seen_at=WEDNESDAY, valid_to=FRIDAY)
        assert ObjectTimeline(closed.kind, closed.key, (closed,)).current is None

        still_open = version(valid_from=FRIDAY, last_seen_at=NEXT_MONDAY)
        timeline = ObjectTimeline(closed.kind, closed.key, (closed, still_open))
        assert timeline.current is still_open


class TestWhatIsTracked:
    def test_every_contract_observation_kind_has_history(self) -> None:
        """A kind stored with no version record would look complete and have no timeline."""
        assert frozenset(ObservationKind) == HISTORICAL_KINDS


def vars_of(subject: ObjectVersion) -> dict[str, Any]:
    """A slotted frozen dataclass's fields, for building a deliberately invalid twin."""
    return {name: getattr(subject, name) for name in ObjectVersion.__dataclass_fields__}
