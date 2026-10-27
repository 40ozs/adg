"""Collection coverage for a reviewer (ADG-DEMO-03): every state, and the one invariant.

The invariant is that a scope nobody managed to read is never reported the way a scope that
was read and found nothing is. Every test that builds a failed or unavailable scope beside
a clean empty one is a test of that sentence.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.domain.coverage import (
    CoverageReport,
    CoverageVerdict,
    EvidenceAge,
    KindState,
    ScopeState,
    TotalsInterpretation,
    TotalsReading,
    assess_coverage,
    interpret_totals,
    server_of_resource,
)
from app.domain.observation import ScanStatus
from app.domain.operations import RunOutcome, ScopeOperations

G2 = dt.datetime(2026, 9, 17, 3, 35, tzinfo=dt.UTC)
G3 = dt.datetime(2026, 10, 1, 3, 35, tzinfo=dt.UTC)


def run(
    collector: str,
    target: str | None,
    status: ScanStatus,
    *,
    at: dt.datetime = G3,
    errors: int = 0,
    observations: int = 10,
    run_id: str | None = None,
) -> RunOutcome:
    return RunOutcome(
        run_id=run_id or f"{collector}-{target}-{at.date()}",
        collector=collector,
        collector_host=target or "dc01",
        target=target,
        status=status,
        started_at=at,
        completed_at=None if status is ScanStatus.RUNNING else at + dt.timedelta(minutes=4),
        error_count=errors,
        batches_reported=None if status is ScanStatus.RUNNING else 1,
        batches_received=1,
        observations_reported=None if status is ScanStatus.RUNNING else observations,
        observations_applied=observations,
        declared_scopes=1,
        reconciled_scopes=1 if status is ScanStatus.SUCCEEDED and not errors else 0,
    )


def scope(
    latest: RunOutcome, *, success: RunOutcome | None = None, failure: RunOutcome | None = None
) -> ScopeOperations:
    usable = latest.status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL)
    return ScopeOperations(
        collector=latest.collector,
        target=latest.target,
        latest=latest,
        last_success=latest if usable else success,
        last_failure=latest if latest.status is ScanStatus.FAILED else failure,
    )


def directory() -> ScopeOperations:
    return scope(run("active_directory", "example.test", ScanStatus.SUCCEEDED))


def collected(collector: str, server: str, observations: int = 10) -> ScopeOperations:
    return scope(run(collector, server, ScanStatus.SUCCEEDED, observations=observations))


def unreachable(server: str) -> tuple[ScopeOperations, dict[str, tuple[str, ...]]]:
    """An NTFS scope whose latest scan could not reach its server, after a good one."""
    earlier = run("ntfs", server, ScanStatus.SUCCEEDED, at=G2)
    latest = run("ntfs", server, ScanStatus.FAILED, observations=0, errors=1)
    return scope(latest, success=earlier), {latest.run_id: ("host_unreachable",)}


class TestEveryScopeCollected:
    def test_is_complete_and_says_so_in_counts(self) -> None:
        report = assess_coverage([directory(), collected("smb", "FS01"), collected("ntfs", "FS01")])

        assert report.verdict is CoverageVerdict.COMPLETE
        assert report.complete
        assert report.headline == "3 of 3 collection scopes collected completely"
        assert report.concerns == ()

    def test_the_headline_is_a_count_of_scopes_never_a_percentage(self) -> None:
        """Scopes are not equal in size; a percentage would claim they were."""
        report = assess_coverage([directory(), collected("smb", "FS01")])

        assert "%" not in report.headline


class TestAnUnavailableSource:
    def test_a_target_that_could_not_be_reached_is_unavailable_with_stale_evidence(
        self,
    ) -> None:
        down, codes = unreachable("FS03")
        report = assess_coverage(
            [directory(), collected("ntfs", "FS01"), down], failure_codes=codes
        )

        item = next(item for item in report.scopes if item.target == "FS03")
        assert item.state is ScopeState.UNAVAILABLE
        assert item.evidence is EvidenceAge.STALE
        assert item.evidence_collected_at == G2 + dt.timedelta(minutes=4)
        assert "2026-09-17" in item.sentence
        assert report.verdict is CoverageVerdict.INCOMPLETE
        assert report.headline == (
            "2 of 3 collection scopes collected completely · 1 unavailable · "
            "1 holding older evidence"
        )

    def test_a_refused_collector_is_failed_not_unavailable(self) -> None:
        latest = run("ntfs", "FS04", ScanStatus.FAILED, observations=0, errors=1)
        report = assess_coverage(
            [directory(), scope(latest)], failure_codes={latest.run_id: ("access_denied",)}
        )

        item = report.scopes[-1]
        assert item.state is ScopeState.FAILED
        assert item.evidence is EvidenceAge.NONE
        assert "access_denied" in item.sentence
        assert "has ever been collected" in item.sentence
        assert report.verdict is CoverageVerdict.INCOMPLETE

    def test_mixed_codes_are_not_called_unavailable(self) -> None:
        """Unreachable only when every reported code says the target was not reached."""
        latest = run("ntfs", "FS05", ScanStatus.FAILED, observations=0, errors=2)
        report = assess_coverage(
            [scope(latest)], failure_codes={latest.run_id: ("host_unreachable", "access_denied")}
        )

        assert report.scopes[0].state is ScopeState.FAILED

    def test_a_failure_with_no_recorded_code_is_failed(self) -> None:
        latest = run("smb", "FS06", ScanStatus.FAILED, observations=0)
        assert assess_coverage([scope(latest)]).scopes[0].state is ScopeState.FAILED

    def test_a_canceled_scan_is_failed(self) -> None:
        latest = run("smb", "FS06", ScanStatus.CANCELED, observations=0)
        assert assess_coverage([scope(latest)]).scopes[0].state is ScopeState.FAILED


class TestZeroFindingsIsNotMissingCollection:
    """The invariant this module exists for."""

    def test_a_successful_scan_that_found_nothing_is_collected_and_says_so(self) -> None:
        report = assess_coverage([directory(), collected("ntfs", "EMPTY01", observations=0)])

        item = report.scopes[-1]
        assert item.state is ScopeState.COLLECTED
        assert item.returned_nothing
        assert "found nothing" in item.sentence
        assert report.complete
        assert report.returned_nothing == 1

    def test_a_failed_scan_with_zero_observations_is_not_found_nothing(self) -> None:
        down, codes = unreachable("FS03")
        report = assess_coverage([directory(), down], failure_codes=codes)

        item = report.scopes[-1]
        assert item.observations == 0
        assert not item.returned_nothing
        assert "found nothing" not in item.sentence
        assert not report.complete
        assert report.returned_nothing == 0

    def test_the_two_zeros_never_share_a_verdict(self) -> None:
        empty = assess_coverage([collected("ntfs", "FS01", observations=0)])
        down, codes = unreachable("FS01")
        missing = assess_coverage([down], failure_codes=codes)

        assert empty.verdict is CoverageVerdict.COMPLETE
        assert missing.verdict is CoverageVerdict.INCOMPLETE


class TestPartialAndRunning:
    def test_a_partial_scan_is_partial_with_current_evidence(self) -> None:
        latest = run("ntfs", "MIG01", ScanStatus.PARTIAL, errors=1)
        report = assess_coverage([directory(), scope(latest)])

        item = report.scopes[-1]
        assert item.state is ScopeState.PARTIAL
        assert item.evidence is EvidenceAge.CURRENT
        assert "could not be read" in item.sentence
        assert report.verdict is CoverageVerdict.PARTIAL

    def test_a_success_that_reported_errors_is_partial(self) -> None:
        latest = run("smb", "FS01", ScanStatus.SUCCEEDED, errors=2)
        assert assess_coverage([scope(latest)]).scopes[0].state is ScopeState.PARTIAL

    def test_a_running_scan_keeps_the_earlier_evidence_current(self) -> None:
        """A scan that has not finished has not failed."""
        earlier = run("ntfs", "FS01", ScanStatus.SUCCEEDED, at=G2)
        latest = run("ntfs", "FS01", ScanStatus.RUNNING)
        report = assess_coverage([scope(latest, success=earlier)])

        item = report.scopes[0]
        assert item.state is ScopeState.RUNNING
        assert item.evidence is EvidenceAge.CURRENT
        assert report.verdict is CoverageVerdict.PARTIAL


class TestExpectedCollectors:
    def test_an_expected_kind_that_never_reported_is_not_collected(self) -> None:
        report = assess_coverage(
            [directory(), collected("smb", "FS01")], expected=("active_directory", "smb", "ntfs")
        )

        ntfs = next(kind for kind in report.kinds if kind.collector == "ntfs")
        assert ntfs.state is KindState.NOT_COLLECTED
        assert report.verdict is CoverageVerdict.INCOMPLETE
        assert "never reported: ntfs" in report.headline
        assert any("folders and their permission lists" in item for item in report.concerns)

    def test_a_kind_nobody_expects_is_not_configured_and_changes_nothing(self) -> None:
        """ADG cannot tell an intentional omission from a collector nobody set up."""
        report = assess_coverage([directory(), collected("smb", "FS01")])

        local = next(kind for kind in report.kinds if kind.collector == "local_groups")
        assert local.state is KindState.NOT_CONFIGURED
        assert not local.expected
        assert report.complete

    def test_every_collector_kind_is_listed_whether_or_not_it_reported(self) -> None:
        report = assess_coverage([])

        assert [kind.collector for kind in report.kinds] == [
            "active_directory",
            "local_groups",
            "ntfs",
            "smb",
        ]
        assert report.verdict is CoverageVerdict.NONE


class TestTheScopesOneAnswerReads:
    def test_the_directory_and_the_resources_own_server(self) -> None:
        down, codes = unreachable("FS03")
        report = assess_coverage(
            [
                directory(),
                collected("smb", "FS01"),
                collected("ntfs", "FS01"),
                collected("local_groups", "fs01"),
                down,
            ],
            failure_codes=codes,
        )

        relevant = report.for_server(server_of_resource(r"\\FS01\Engineering\Projects\Apollo"))

        assert sorted(item.label for item in relevant.scopes) == [
            "active_directory (example.test)",
            "local_groups (fs01)",
            "ntfs (FS01)",
            "smb (FS01)",
        ]
        assert relevant.complete
        assert not report.complete

    def test_a_folder_on_the_unavailable_server_reads_the_unavailable_scope(self) -> None:
        down, codes = unreachable("FS03")
        report = assess_coverage([directory(), down], failure_codes=codes)

        relevant = report.for_server(server_of_resource(r"\\FS03\Collaboration"))

        assert not relevant.complete
        assert relevant.verdict is CoverageVerdict.INCOMPLETE

    def test_server_of_resource(self) -> None:
        assert server_of_resource(r"\\GP-ENG-FS01\Engineering\Projects") == "GP-ENG-FS01"
        assert server_of_resource("//fs01/share") == "fs01"
        assert server_of_resource("") is None


class TestReadingRiskTotals:
    def complete(self) -> CoverageReport:
        return assess_coverage([directory(), collected("ntfs", "FS01")])

    def read(self, **overrides: Any) -> TotalsInterpretation:
        arguments: dict[str, Any] = {
            "has_ever_run": True,
            "evaluation_complete": True,
            "evaluation_trigger": "full",
            "truncation": None,
            "evaluated_basis": "a" * 32,
            "current_basis": "a" * 32,
            "coverage": self.complete(),
        }
        arguments.update(overrides)
        return interpret_totals(**arguments)

    def test_a_full_pass_over_complete_current_collection_is_a_total(self) -> None:
        reading = self.read()

        assert reading.reading is TotalsReading.COMPLETE
        assert reading.exhaustive
        assert reading.basis_current is True
        assert reading.reasons == ()

    def test_never_evaluated(self) -> None:
        reading = self.read(has_ever_run=False)
        assert reading.reading is TotalsReading.NOT_EVALUATED

    def test_incomplete_collection_makes_totals_not_exhaustive(self) -> None:
        down, codes = unreachable("FS03")
        coverage = assess_coverage([directory(), down], failure_codes=codes)

        reading = self.read(coverage=coverage)

        assert reading.reading is TotalsReading.INCOMPLETE
        assert not reading.exhaustive
        assert "1 unavailable" in reading.reasons[0]

    def test_a_partial_evaluation_outranks_incomplete_collection(self) -> None:
        down, codes = unreachable("FS03")
        coverage = assess_coverage([directory(), down], failure_codes=codes)

        reading = self.read(
            coverage=coverage, evaluation_complete=False, evaluation_trigger="incremental"
        )

        assert reading.reading is TotalsReading.PARTIAL
        assert len(reading.reasons) == 2

    def test_collection_that_moved_since_the_evaluation_is_stale(self) -> None:
        reading = self.read(current_basis="b" * 32)

        assert reading.reading is TotalsReading.STALE
        assert reading.basis_current is False

    def test_an_evaluation_with_no_recorded_basis_is_never_read_as_current(self) -> None:
        reading = self.read(evaluated_basis=None)

        assert reading.reading is TotalsReading.STALE
        assert reading.basis_current is None

    def test_every_reason_is_listed_and_the_worst_one_wins(self) -> None:
        down, codes = unreachable("FS03")
        coverage = assess_coverage([directory(), down], failure_codes=codes)

        reading = self.read(coverage=coverage, current_basis="b" * 32)

        assert reading.reading is TotalsReading.INCOMPLETE
        assert len(reading.reasons) == 2
