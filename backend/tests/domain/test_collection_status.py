"""The rule that decides whether an empty page can be believed.

Every case here is a real misreading waiting to happen. The pessimism is deliberate and is
asserted as such: the tests that matter most are the ones showing that a single failure
downgrades the whole verdict, and that "still running" is never "complete".
"""

from __future__ import annotations

import datetime as dt

from app.domain.collection import CollectionHealth, CollectorCoverage, assess_collection
from app.domain.observation import ScanStatus

STARTED = dt.datetime(2026, 9, 14, 8, 0, tzinfo=dt.UTC)


def run(
    collector: str,
    status: ScanStatus,
    *,
    errors: int = 0,
    downgrade: str | None = None,
) -> CollectorCoverage:
    return CollectorCoverage(
        collector=collector,
        status=status,
        started_at=STARTED,
        completed_at=STARTED + dt.timedelta(minutes=5),
        error_count=errors,
        target=None,
        downgrade_reason=downgrade,
    )


class TestNothingHasRun:
    def test_no_runs_at_all_is_no_data(self) -> None:
        assert assess_collection([]).health is CollectionHealth.NO_DATA

    def test_the_summary_says_the_emptiness_is_the_tools_and_not_the_estates(self) -> None:
        summary = assess_collection([]).summary

        assert "not because nothing is there" in summary

    def test_it_reports_no_concerns_because_nothing_went_wrong(self) -> None:
        """ "Nothing has run" is not a failure; it is a starting state, and dressing it up
        as a failure would train people to ignore the banner."""
        assert assess_collection([]).concerns == ()


class TestEverythingSucceeded:
    def test_clean_runs_are_healthy(self) -> None:
        status = assess_collection(
            [run("active_directory", ScanStatus.SUCCEEDED), run("smb", ScanStatus.SUCCEEDED)]
        )

        assert status.health is CollectionHealth.HEALTHY
        assert status.concerns == ()

    def test_the_summary_names_the_collectors_that_are_current(self) -> None:
        summary = assess_collection(
            [run("smb", ScanStatus.SUCCEEDED), run("ntfs", ScanStatus.SUCCEEDED)]
        ).summary

        assert "smb" in summary and "ntfs" in summary

    def test_collectors_are_reported_in_a_stable_order(self) -> None:
        """A banner whose contents reshuffle between refreshes reads as instability."""
        status = assess_collection(
            [run("smb", ScanStatus.SUCCEEDED), run("active_directory", ScanStatus.SUCCEEDED)]
        )

        assert [item.collector for item in status.coverage] == ["active_directory", "smb"]


class TestSomethingIsWrong:
    def test_a_failed_run_makes_the_whole_picture_failed(self) -> None:
        """Even beside three successes. The estate the failed collector covers is exactly
        the part nobody can see, and that is the part somebody is about to conclude is
        clean."""
        status = assess_collection(
            [
                run("active_directory", ScanStatus.SUCCEEDED),
                run("smb", ScanStatus.SUCCEEDED),
                run("ntfs", ScanStatus.FAILED),
            ]
        )

        assert status.health is CollectionHealth.FAILED

    def test_a_partial_run_is_incomplete(self) -> None:
        status = assess_collection([run("ntfs", ScanStatus.PARTIAL, downgrade="12 paths denied")])

        assert status.health is CollectionHealth.INCOMPLETE
        assert "12 paths denied" in status.concerns[0]

    def test_a_running_run_is_incomplete_rather_than_healthy(self) -> None:
        status = assess_collection([run("smb", ScanStatus.RUNNING)])

        assert status.health is CollectionHealth.INCOMPLETE
        assert "still in progress" in status.concerns[0]

    def test_a_pending_run_is_incomplete(self) -> None:
        assert (
            assess_collection([run("smb", ScanStatus.PENDING)]).health
            is CollectionHealth.INCOMPLETE
        )

    def test_a_success_that_reported_errors_is_not_trustworthy(self) -> None:
        """An unreadable directory is a fact the run succeeded in recording, and it is
        precisely a place where ADG cannot see the permissions."""
        status = assess_collection([run("ntfs", ScanStatus.SUCCEEDED, errors=4)])

        assert status.health is CollectionHealth.INCOMPLETE
        assert "4 error(s)" in status.concerns[0]

    def test_failure_outranks_incompleteness(self) -> None:
        status = assess_collection([run("smb", ScanStatus.PARTIAL), run("ntfs", ScanStatus.FAILED)])

        assert status.health is CollectionHealth.FAILED

    def test_the_incomplete_summary_names_the_direction_of_the_error(self) -> None:
        """Under-reporting, specifically. An audit tool that might over-report is annoying;
        one that might under-report is dangerous, and the wording has to say which."""
        summary = assess_collection([run("ntfs", ScanStatus.PARTIAL)]).summary

        assert "under-report" in summary

    def test_every_concern_is_collected_not_only_the_first(self) -> None:
        status = assess_collection(
            [run("smb", ScanStatus.PARTIAL), run("ntfs", ScanStatus.SUCCEEDED, errors=2)]
        )

        assert len(status.concerns) == 2


class TestTrustworthiness:
    def test_a_clean_success_is_trustworthy(self) -> None:
        assert run("smb", ScanStatus.SUCCEEDED).is_trustworthy is True

    def test_nothing_else_is(self) -> None:
        for status in ScanStatus:
            if status is ScanStatus.SUCCEEDED:
                continue
            assert run("smb", status).is_trustworthy is False, status

    def test_a_success_with_errors_is_not(self) -> None:
        assert run("smb", ScanStatus.SUCCEEDED, errors=1).is_trustworthy is False
