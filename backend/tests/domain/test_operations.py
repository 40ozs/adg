"""The operator's report: three facts per scope, and what "incomplete" actually means.

The rule this file protects is the one a status page is most likely to get wrong. A scope
has a *latest* run, a *last successful* run and a *last failed* run, and they are three
different facts. Showing only the first says "failed" and hides that yesterday's data is
still on screen; showing only the second says "succeeded" and hides that it is stale. Every
test in :class:`TestAScopeCarriesThreeFacts` is one way that collapse goes wrong.

The other half is completeness. A run can report ``succeeded`` and still not have delivered
what it collected — batches that never arrived, observations claimed and not stored, a scope
declared and never reconciled. None of those change the run's status, and all of them mean
the estate below that scope is less complete than it looks.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain.collection import CollectionHealth, CollectionStatus
from app.domain.observation import ScanStatus
from app.domain.operations import (
    Completeness,
    ErrorGroup,
    ObjectCounts,
    RunOutcome,
    ScopeOperations,
    assemble_operations,
)

STARTED = dt.datetime(2026, 9, 14, 8, 0, tzinfo=dt.UTC)
EMPTY_STATUS = CollectionStatus(health=CollectionHealth.NO_DATA, coverage=(), concerns=())


def run(
    *,
    run_id: str = "r1",
    collector: str = "ntfs",
    target: str | None = "FS01",
    status: ScanStatus = ScanStatus.SUCCEEDED,
    errors: int = 0,
    batches_reported: int | None = 2,
    batches_received: int = 2,
    observations_reported: int | None = 100,
    observations_applied: int = 100,
    declared: int = 1,
    reconciled: int = 1,
    incremental: bool = False,
    started: dt.datetime = STARTED,
) -> RunOutcome:
    return RunOutcome(
        run_id=run_id,
        collector=collector,
        collector_host="FS01",
        target=target,
        status=status,
        started_at=started,
        completed_at=started + dt.timedelta(minutes=4),
        error_count=errors,
        batches_reported=batches_reported,
        batches_received=batches_received,
        observations_reported=observations_reported,
        observations_applied=observations_applied,
        declared_scopes=declared,
        reconciled_scopes=reconciled,
        incremental=incremental,
    )


class TestWhatCountsAsComplete:
    def test_a_clean_delivered_reconciled_run_is_complete(self) -> None:
        assert run().completeness is Completeness.COMPLETE
        assert run().shortfall is None

    def test_a_failed_run_collected_nothing_usable(self) -> None:
        assert run(status=ScanStatus.FAILED).completeness is Completeness.NONE

    def test_a_canceled_run_is_the_same_as_a_failed_one(self) -> None:
        """An operator cancels a scan; the estate below it is just as unobserved."""
        assert run(status=ScanStatus.CANCELED).completeness is Completeness.NONE

    def test_a_run_still_going_is_not_reported_as_incomplete(self) -> None:
        """It is not finished. Marking it incomplete would make every scan look broken for
        as long as it runs, and teach an operator to ignore the column."""
        assert run(status=ScanStatus.RUNNING).completeness is Completeness.IN_PROGRESS
        assert run(status=ScanStatus.PENDING).completeness is Completeness.IN_PROGRESS

    def test_a_partial_run_is_incomplete(self) -> None:
        assert run(status=ScanStatus.PARTIAL).completeness is Completeness.PARTIAL

    def test_a_success_that_lost_a_batch_is_incomplete(self) -> None:
        """This is the one a run's own status will never tell you. The collector sent three
        batches, two arrived, and the completion still says ``succeeded``."""
        outcome = run(batches_reported=3, batches_received=2)

        assert outcome.completeness is Completeness.PARTIAL
        assert "1 batch(es) never arrived" in (outcome.shortfall or "")

    def test_a_success_that_stored_fewer_rows_than_claimed_is_incomplete(self) -> None:
        outcome = run(observations_reported=100, observations_applied=90)

        assert outcome.completeness is Completeness.PARTIAL
        assert "10 observation(s) were claimed but not stored" in (outcome.shortfall or "")

    def test_a_success_that_never_reconciled_its_scope_is_incomplete(self) -> None:
        outcome = run(declared=2, reconciled=0)

        assert outcome.completeness is Completeness.PARTIAL
        assert "2 declared scope(s) were not fully enumerated" in (outcome.shortfall or "")

    def test_an_incremental_run_is_not_expected_to_reconcile(self) -> None:
        """By definition it looks at part of its scope. Counting that as a shortfall would
        mark every incremental run incomplete."""
        outcome = run(declared=2, reconciled=0, incremental=True)

        assert outcome.scopes_unreconciled == 0
        assert outcome.completeness is Completeness.COMPLETE

    def test_a_success_that_reported_errors_is_incomplete(self) -> None:
        outcome = run(errors=4)

        assert outcome.completeness is Completeness.PARTIAL
        assert "4 object(s) could not be read" in (outcome.shortfall or "")

    def test_an_open_run_claims_no_totals_so_nothing_is_missing(self) -> None:
        """``batches_reported`` is None until a completion arrives. Treating None as zero
        would make every in-flight run look like it had lost everything it sent."""
        outcome = run(
            status=ScanStatus.RUNNING,
            batches_reported=None,
            observations_reported=None,
            batches_received=1,
            observations_applied=40,
        )

        assert outcome.batches_missing == 0
        assert outcome.observations_missing == 0

    def test_a_shortfall_names_every_cause_not_only_the_first(self) -> None:
        outcome = run(batches_reported=3, batches_received=1, errors=2, declared=2, reconciled=0)
        shortfall = outcome.shortfall or ""

        assert "batch(es) never arrived" in shortfall
        assert "declared scope(s)" in shortfall
        assert "could not be read" in shortfall

    def test_a_report_never_claims_more_arrived_than_was_sent(self) -> None:
        """A negative shortfall is a counting bug, not a surplus."""
        assert run(batches_reported=1, batches_received=4).batches_missing == 0
        assert run(observations_reported=1, observations_applied=9).observations_missing == 0


class TestAScopeCarriesThreeFacts:
    def test_a_failure_does_not_erase_the_last_success(self) -> None:
        success = run(run_id="ok", started=STARTED)
        failure = run(run_id="bad", status=ScanStatus.FAILED, started=STARTED + dt.timedelta(1))
        scope = ScopeOperations(
            collector="ntfs",
            target="FS01",
            latest=failure,
            last_success=success,
            last_failure=failure,
        )

        assert scope.completeness is Completeness.NONE
        assert scope.has_ever_succeeded is True
        assert scope.stale_success is True
        assert "has not been refreshed since" in (scope.note or "")

    def test_a_scope_that_has_never_succeeded_says_so_in_the_strongest_terms(self) -> None:
        failure = run(run_id="bad", status=ScanStatus.FAILED)
        scope = ScopeOperations(
            collector="ntfs",
            target="FS03",
            latest=failure,
            last_success=None,
            last_failure=failure,
        )

        assert scope.has_ever_succeeded is False
        assert "has never completed a run" in (scope.note or "")
        assert "FS03" in (scope.note or "")

    def test_a_success_that_is_the_latest_run_is_not_stale(self) -> None:
        latest = run(run_id="ok")
        scope = ScopeOperations(
            collector="ntfs",
            target="FS01",
            latest=latest,
            last_success=latest,
            last_failure=None,
        )

        assert scope.stale_success is False
        assert scope.note is None

    def test_a_healthy_scope_has_nothing_to_say(self) -> None:
        latest = run()
        scope = ScopeOperations(
            collector="smb", target="FS01", latest=latest, last_success=latest, last_failure=None
        )

        assert scope.note is None

    def test_a_scope_names_itself_by_collector_and_target(self) -> None:
        latest = run()
        with_target = ScopeOperations("ntfs", "FS01", latest, latest, None)
        without = ScopeOperations("ntfs", None, latest, latest, None)

        assert with_target.scope_label == "ntfs (FS01)"
        assert without.scope_label == "ntfs"

    def test_an_open_run_reports_its_totals_as_not_final(self) -> None:
        latest = run(status=ScanStatus.RUNNING, batches_reported=None, observations_reported=None)
        scope = ScopeOperations("ntfs", "FS01", latest, None, None)

        assert "still open" in (scope.note or "")


class TestAssemblingTheReport:
    def test_each_scope_is_paired_with_its_own_success_and_failure(self) -> None:
        """Not with another scope's. The pairing is by (collector, target), and getting it
        wrong would attribute FS01's success to FS03 — the single most misleading thing
        this page could do."""
        fs01 = run(run_id="fs01-ok", target="FS01")
        fs03_fail = run(run_id="fs03-bad", target="FS03", status=ScanStatus.FAILED)

        report = assemble_operations(
            status=EMPTY_STATUS,
            latest=[fs01, fs03_fail],
            successes=[fs01],
            failures=[fs03_fail],
            counts=ObjectCounts(),
            errors=[],
        )
        by_target = {scope.target: scope for scope in report.scopes}

        assert by_target["FS01"].last_success is fs01
        assert by_target["FS01"].last_failure is None
        assert by_target["FS03"].last_success is None
        assert by_target["FS03"].last_failure is fs03_fail

    def test_scopes_come_back_in_a_stable_order(self) -> None:
        """A page whose rows reshuffle between refreshes reads as instability."""
        runs = [
            run(run_id="c", collector="smb", target="FS02"),
            run(run_id="a", collector="ntfs", target="FS01"),
            run(run_id="b", collector="ntfs", target="FS02"),
        ]

        report = assemble_operations(
            status=EMPTY_STATUS,
            latest=runs,
            successes=[],
            failures=[],
            counts=ObjectCounts(),
            errors=[],
        )

        assert [(s.collector, s.target) for s in report.scopes] == [
            ("ntfs", "FS01"),
            ("ntfs", "FS02"),
            ("smb", "FS02"),
        ]

    def test_a_success_for_a_scope_with_no_latest_run_is_dropped(self) -> None:
        """It cannot happen — the latest run of a scope that has a success is at worst that
        run — and inventing a scope row for it would report something that did not happen."""
        report = assemble_operations(
            status=EMPTY_STATUS,
            latest=[],
            successes=[run(run_id="orphan")],
            failures=[],
            counts=ObjectCounts(),
            errors=[],
        )

        assert report.scopes == ()

    def test_notes_carry_only_the_scopes_that_need_attention(self) -> None:
        good = run(run_id="ok", target="FS01")
        bad = run(run_id="bad", target="FS03", status=ScanStatus.FAILED)

        report = assemble_operations(
            status=EMPTY_STATUS,
            latest=[good, bad],
            successes=[good],
            failures=[bad],
            counts=ObjectCounts(),
            errors=[],
        )

        assert len(report.notes) == 1
        assert "FS03" in report.notes[0]
        assert [scope.target for scope in report.never_succeeded] == ["FS03"]

    def test_errors_are_ordered_by_how_many_there_are(self) -> None:
        report = assemble_operations(
            status=EMPTY_STATUS,
            latest=[],
            successes=[],
            failures=[],
            counts=ObjectCounts(),
            errors=[
                ErrorGroup("path_too_long", 2, ("ntfs",), None, ()),
                ErrorGroup("access_denied", 40, ("ntfs", "smb"), None, ()),
            ],
        )

        assert [group.code for group in report.errors] == ["access_denied", "path_too_long"]
        assert report.total_errors == 42

    def test_an_error_reported_by_two_collectors_is_widespread(self) -> None:
        """Twelve access_denied on one share is a permission; twelve across two collectors
        is the service account, and those call for different responses."""
        assert ErrorGroup("access_denied", 12, ("ntfs", "smb"), None, ()).is_widespread
        assert not ErrorGroup("access_denied", 12, ("ntfs",), None, ()).is_widespread


class TestObjectCounts:
    def test_the_total_is_collected_objects_and_excludes_runs(self) -> None:
        """A run is the act of collecting, not a thing in the estate. Counting runs in the
        total would make an estate look populated after a hundred failed scans."""
        counts = ObjectCounts(
            principals=10,
            membership_edges=20,
            servers=1,
            shares=2,
            share_aces=3,
            directories=4,
            ntfs_aces=5,
            scan_runs=99,
        )

        assert counts.total == 45

    def test_an_empty_store_totals_zero(self) -> None:
        assert ObjectCounts().total == 0

    def test_the_mapping_names_every_field(self) -> None:
        """The API renders this mapping, so a field added here and not there would be
        silently absent from the page."""
        mapping = ObjectCounts().as_mapping()

        assert set(mapping) == {
            "principals",
            "membership_edges",
            "servers",
            "shares",
            "share_aces",
            "directories",
            "ntfs_aces",
            "scan_runs",
        }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ScanStatus.SUCCEEDED, Completeness.COMPLETE),
        (ScanStatus.PARTIAL, Completeness.PARTIAL),
        (ScanStatus.FAILED, Completeness.NONE),
        (ScanStatus.CANCELED, Completeness.NONE),
        (ScanStatus.RUNNING, Completeness.IN_PROGRESS),
        (ScanStatus.PENDING, Completeness.IN_PROGRESS),
    ],
)
def test_every_scan_status_maps_to_a_completeness(
    status: ScanStatus, expected: Completeness
) -> None:
    """Exhaustive over the enum: a status added later with no mapping falls through to
    PARTIAL, and this is where that is noticed."""
    assert run(status=status).completeness is expected
