"""Provenance invariants: scan runs and observations."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import (
    AceType,
    CollectorKind,
    MembershipEdge,
    NtfsAce,
    Observation,
    ObservationSource,
    ScanRun,
    ScanStatus,
    Sid,
)
from app.domain.errors import DomainValidationError

START = dt.datetime(2026, 9, 14, 8, 0, tzinfo=dt.UTC)
END = dt.datetime(2026, 9, 14, 8, 5, tzinfo=dt.UTC)
TRUSTEE = Sid("S-1-5-21-1-2-3-1104")


def source(collector: CollectorKind = CollectorKind.NTFS) -> ObservationSource:
    return ObservationSource(
        collector=collector,
        collector_host="COLLECTOR01",
        method="System.IO.DirectoryInfo.GetAccessControl",
        collector_version="0.1.0",
        target="\\\\FS01\\Finance",
    )


class TestObservationSource:
    def test_the_reading_method_is_part_of_the_evidence(self) -> None:
        assert source().method == "System.IO.DirectoryInfo.GetAccessControl"

    @pytest.mark.parametrize("missing", ["collector_host", "method"])
    def test_an_incomplete_source_is_rejected(self, missing: str) -> None:
        values = {
            "collector": CollectorKind.SMB,
            "collector_host": "COLLECTOR01",
            "method": "Get-SmbShareAccess",
        }
        values[missing] = "  "

        with pytest.raises(DomainValidationError, match=missing):
            ObservationSource(**values)  # type: ignore[arg-type]


class TestScanRun:
    def test_a_running_scan_has_no_end(self) -> None:
        run = ScanRun(source=source(), started_at=START)

        assert run.status is ScanStatus.RUNNING
        assert run.completed_at is None
        assert run.duration is None

    def test_a_completed_scan_reports_its_duration(self) -> None:
        run = ScanRun(
            source=source(), started_at=START, status=ScanStatus.SUCCEEDED, completed_at=END
        )

        assert run.duration == dt.timedelta(minutes=5)

    def test_naive_timestamps_are_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="timezone-aware"):
            ScanRun(source=source(), started_at=dt.datetime(2026, 9, 14, 8, 0))

    def test_timestamps_are_normalized_to_utc(self) -> None:
        eastern = dt.timezone(dt.timedelta(hours=-4))
        run = ScanRun(source=source(), started_at=START.astimezone(eastern))

        assert run.started_at == START
        assert run.started_at.tzinfo == dt.UTC

    def test_a_terminal_status_requires_an_end_time(self) -> None:
        with pytest.raises(DomainValidationError, match="completed_at"):
            ScanRun(source=source(), started_at=START, status=ScanStatus.SUCCEEDED)

    def test_a_running_scan_may_not_record_an_end_time(self) -> None:
        with pytest.raises(DomainValidationError, match="completed_at"):
            ScanRun(source=source(), started_at=START, completed_at=END)

    def test_completion_may_not_precede_the_start(self) -> None:
        with pytest.raises(DomainValidationError, match="before it starts"):
            ScanRun(
                source=source(), started_at=END, status=ScanStatus.SUCCEEDED, completed_at=START
            )

    def test_a_run_with_errors_is_not_reported_as_complete(self) -> None:
        with pytest.raises(DomainValidationError, match="PARTIAL or FAILED"):
            ScanRun(
                source=source(),
                started_at=START,
                status=ScanStatus.SUCCEEDED,
                completed_at=END,
                error_count=3,
            )

    def test_a_partial_run_yields_usable_observations(self) -> None:
        run = ScanRun(
            source=source(),
            started_at=START,
            status=ScanStatus.PARTIAL,
            completed_at=END,
            error_count=3,
            observation_count=900,
        )

        assert run.status.yields_usable_observations is True
        assert run.status.is_terminal is True

    def test_a_failed_run_yields_no_usable_observations(self) -> None:
        assert ScanStatus.FAILED.yields_usable_observations is False

    def test_runs_receive_distinct_identifiers(self) -> None:
        first = ScanRun(source=source(), started_at=START)
        second = ScanRun(source=source(), started_at=START)

        assert first.identity_key != second.identity_key


class TestObservation:
    def test_a_fact_carries_its_run_and_source(self) -> None:
        run = ScanRun(source=source(), started_at=START)
        ace = NtfsAce(trustee_sid=TRUSTEE, ace_type=AceType.ALLOW, access_mask=0x001200A9)

        observation = Observation.during(run, ace, observed_at=START)

        assert observation.fact is ace
        assert observation.scan_run_id == run.run_id
        assert observation.source == run.source

    def test_any_raw_fact_type_can_be_observed(self) -> None:
        run = ScanRun(source=source(CollectorKind.ACTIVE_DIRECTORY), started_at=START)
        edge = MembershipEdge(
            group_sid=Sid("S-1-5-21-1-2-3-1201"), member_sid=Sid("S-1-5-21-1-2-3-1104")
        )

        observation: Observation[MembershipEdge] = Observation.during(run, edge, observed_at=START)

        assert observation.fact == edge

    def test_naive_observation_times_are_rejected(self) -> None:
        run = ScanRun(source=source(), started_at=START)

        with pytest.raises(DomainValidationError, match="timezone-aware"):
            Observation(
                fact="anything",
                observed_at=dt.datetime(2026, 9, 14, 8, 0),
                scan_run_id=run.run_id,
                source=run.source,
            )

    def test_observations_default_to_now(self) -> None:
        run = ScanRun(source=source(), started_at=START)

        observation = Observation.during(run, "fact")

        assert observation.observed_at.tzinfo == dt.UTC
