"""The vocabulary: the severity ordering, and the summary that says what a filter hid."""

from __future__ import annotations

from typing import Any

import pytest

from app.changes.classify import classify
from app.changes.model import (
    SEVERITY_ORDER,
    ChangeAction,
    ChangeSeverity,
    ChangeSignificance,
    ObjectChange,
    SummaryTally,
    at_least,
)
from app.contracts.v1.common import ObservationKind
from tests.changes import factories as f


class TestSeverityIsOrderedByRankNotByName:
    def test_the_order_runs_from_informational_to_critical(self) -> None:
        assert SEVERITY_ORDER == (
            ChangeSeverity.INFO,
            ChangeSeverity.LOW,
            ChangeSeverity.MEDIUM,
            ChangeSeverity.HIGH,
            ChangeSeverity.CRITICAL,
        )

    def test_it_covers_every_value(self) -> None:
        assert set(SEVERITY_ORDER) == set(ChangeSeverity)

    def test_a_minimum_severity_filter_uses_it(self) -> None:
        assert at_least(ChangeSeverity.CRITICAL, ChangeSeverity.HIGH)
        assert not at_least(ChangeSeverity.LOW, ChangeSeverity.HIGH)

    def test_alphabetical_order_would_get_it_backwards(self) -> None:
        """``StrEnum`` sorts 'critical' below 'info'. A filter that compared the strings
        would answer "at least high" with the informational half of the list."""
        assert "critical" < "info"
        assert at_least(ChangeSeverity.CRITICAL, ChangeSeverity.INFO)


class TestTheSummarySaysWhatTheFilterHid:
    def _change(self, severity_source: dict[str, Any], *, container: bool = True) -> ObjectChange:
        version = f.ntfs_ace(valid_from=f.FRIDAY, **severity_source)
        return classify(
            ObservationKind.NTFS_ACE,
            version.key,
            None,
            version,
            container_observed_before=container,
        )

    def test_it_counts_everything_and_reports_what_was_returned(self) -> None:
        tally = SummaryTally()
        shown = self._change({"trustee": f.EVERYONE, "access_mask": f.FULL_CONTROL})
        hidden = self._change({"access_mask": f.READ_EXECUTE}, container=False)
        tally.count(shown, returned=True)
        tally.count(hidden, returned=False)
        summary = tally.finish(f.MONDAY, f.FRIDAY)
        assert summary.total == 2
        assert summary.returned == 1
        assert summary.excluded == 1

    def test_the_highest_severity_is_taken_by_rank(self) -> None:
        tally = SummaryTally()
        tally.count(self._change({"access_mask": f.READ_EXECUTE}), returned=True)
        tally.count(
            self._change({"trustee": f.EVERYONE, "access_mask": f.FULL_CONTROL}), returned=True
        )
        assert tally.finish(f.MONDAY, f.FRIDAY).highest is ChangeSeverity.CRITICAL

    def test_an_empty_window_has_no_highest_severity(self) -> None:
        assert SummaryTally().finish(f.MONDAY, f.FRIDAY).highest is None

    def test_first_sightings_are_counted_even_though_the_feed_hides_them(self) -> None:
        """The exclusion has to be visible somewhere, or a default has quietly answered a
        different question than the one that was asked."""
        tally = SummaryTally()
        tally.count(self._change({"access_mask": f.FULL_CONTROL}, container=False), returned=False)
        summary = tally.finish(f.MONDAY, f.FRIDAY)
        assert summary.by_action[ChangeAction.FIRST_OBSERVED] == 1
        assert summary.returned == 0

    def test_reconstructed_changes_are_counted_separately(self) -> None:
        from app.history.model import VersionOrigin

        before = f.principal(
            enabled=True,
            valid_from=f.MONDAY,
            valid_to=f.FRIDAY,
            origin=VersionOrigin.BACKFILLED,
        )
        after = f.principal(enabled=False, valid_from=f.FRIDAY)
        tally = SummaryTally()
        tally.count(classify(ObservationKind.PRINCIPAL, after.key, before, after), returned=True)
        assert tally.finish(f.MONDAY, f.FRIDAY).reconstructed == 1


class TestEveryJudgmentHasAnAdgCannotTellValue:
    @pytest.mark.parametrize(
        "enum_value",
        [ChangeSignificance.UNDETERMINED, ChangeSignificance.UNDETERMINED],
    )
    def test_significance_has_one(self, enum_value: ChangeSignificance) -> None:
        assert enum_value in set(ChangeSignificance)

    def test_direction_has_one(self) -> None:
        from app.changes.model import ChangeDirection

        assert ChangeDirection.UNDETERMINED in set(ChangeDirection)

    def test_an_action_that_records_the_start_of_observation_exists(self) -> None:
        assert ChangeAction.FIRST_OBSERVED in set(ChangeAction)
