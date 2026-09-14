"""Batch semantics: idempotency, chunking, partial runs, and reconciliation.

The rules tested here are what make replay safe and what stop a partial scan from deleting
things it never looked at.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.v1 import keys
from app.contracts.v1.common import MAX_BATCH_OBSERVATIONS
from app.contracts.v1.envelopes import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.domain import ScanStatus

RUN_ID = "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31"
OTHER_RUN_ID = "7a2d2b63-4051-4b27-a03f-2e7c1f3b8d42"
BATCH_ID = "b1a7c0de-1111-4222-8333-444455556666"
STARTED_AT = "2026-09-14T08:00:00Z"
COMPLETED_AT = "2026-09-14T08:05:00Z"
SCOPE = {"kind": "directory_tree", "key": "\\\\fs01\\finance"}
SOURCE = {
    "collector": "ntfs",
    "collector_host": "COLLECTOR01",
    "method": "System.IO.DirectoryInfo.GetAccessControl",
}


def server_observation(name: str = "FS01", run_id: str = RUN_ID) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "server",
        "run_id": run_id,
        "observed_at": STARTED_AT,
        "source_key": keys.server_key(name),
        "name": name,
    }


def start(**overrides: Any) -> ScanRunStart:
    payload: dict[str, Any] = {
        "run_id": RUN_ID,
        "source": SOURCE,
        "started_at": STARTED_AT,
        "scopes": [SCOPE],
    }
    payload.update(overrides)
    return ScanRunStart.model_validate(payload)


def completion(**overrides: Any) -> ScanRunCompletion:
    payload: dict[str, Any] = {
        "run_id": RUN_ID,
        "status": "succeeded",
        "completed_at": COMPLETED_AT,
        "batch_count": 1,
        "observation_count": 1,
        "error_count": 0,
    }
    payload.update(overrides)
    return ScanRunCompletion.model_validate(payload)


class TestScanRunStart:
    def test_a_run_declares_what_it_will_enumerate(self) -> None:
        envelope = start()

        assert [scope.key for scope in envelope.scopes] == ["\\\\fs01\\finance"]

    def test_a_run_without_a_scope_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            start(scopes=[])

    def test_duplicate_scopes_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="each scope once"):
            start(scopes=[SCOPE, SCOPE])

    def test_scope_keys_are_case_folded_for_comparison(self) -> None:
        envelope = start(scopes=[{"kind": "share", "key": "FS01|Finance"}])

        assert envelope.scopes[0].key == "fs01|finance"

    def test_a_non_uuid_run_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be a UUID"):
            start(run_id="run-7")

    def test_the_start_converts_to_a_running_domain_run(self) -> None:
        run = start().to_domain()

        assert run.status is ScanStatus.RUNNING
        assert run.completed_at is None
        assert run.started_at == dt.datetime(2026, 9, 14, 8, 0, tzinfo=dt.UTC)

    def test_an_identical_start_is_byte_for_byte_repeatable(self) -> None:
        # Idempotency rests on the collector sending the same payload on retry.
        assert start().model_dump_json() == start().model_dump_json()


class TestObservationBatch:
    def batch(self, **overrides: Any) -> ObservationBatch:
        payload: dict[str, Any] = {
            "run_id": RUN_ID,
            "batch_id": BATCH_ID,
            "sequence": 1,
            "observations": [server_observation()],
        }
        payload.update(overrides)
        return ObservationBatch.model_validate(payload)

    def test_a_batch_carries_its_observations(self) -> None:
        assert self.batch().source_keys == ["server|fs01"]

    def test_replaying_a_batch_produces_identical_content(self) -> None:
        first = self.batch()
        replay = self.batch()

        assert first.batch_id == replay.batch_id
        assert first.source_keys == replay.source_keys
        assert first.model_dump_json() == replay.model_dump_json()

    def test_observations_must_belong_to_the_batch_run(self) -> None:
        with pytest.raises(ValidationError, match="must carry the batch's run_id"):
            self.batch(observations=[server_observation(run_id=OTHER_RUN_ID)])

    def test_a_duplicate_source_key_within_a_batch_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="same source_key twice"):
            self.batch(observations=[server_observation(), server_observation()])

    def test_two_different_objects_may_share_a_batch(self) -> None:
        batch = self.batch(observations=[server_observation("FS01"), server_observation("FS02")])

        assert batch.source_keys == ["server|fs01", "server|fs02"]

    def test_an_empty_batch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self.batch(observations=[])

    def test_an_oversized_batch_is_rejected_rather_than_truncated(self) -> None:
        too_many = [
            server_observation(f"FS{index:04d}") for index in range(MAX_BATCH_OBSERVATIONS + 1)
        ]

        with pytest.raises(ValidationError):
            self.batch(observations=too_many)

    def test_a_full_batch_is_accepted(self) -> None:
        exactly_enough = [
            server_observation(f"FS{index:04d}") for index in range(MAX_BATCH_OBSERVATIONS)
        ]

        assert len(self.batch(observations=exactly_enough).observations) == MAX_BATCH_OBSERVATIONS

    def test_a_sequence_below_one_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self.batch(sequence=0)

    def test_is_final_is_advisory_only(self) -> None:
        # A final batch does not complete the run; only the completion envelope does.
        batch = self.batch(is_final=True)

        assert batch.is_final is True

    def test_a_continuation_token_is_carried_verbatim(self) -> None:
        batch = self.batch(continuation_token="cursor:\\\\fs01\\finance\\reports")

        assert batch.continuation_token == "cursor:\\\\fs01\\finance\\reports"

    def test_mixed_observation_kinds_are_accepted(self) -> None:
        resource = {
            "schema_version": "1.0",
            "kind": "ntfs_resource",
            "run_id": RUN_ID,
            "observed_at": STARTED_AT,
            "source_key": keys.ntfs_resource_key("\\\\FS01\\Finance"),
            "path": "\\\\FS01\\Finance",
            "dacl_present": True,
            "ace_count": 0,
        }
        batch = self.batch(observations=[server_observation(), resource])

        assert {item.kind for item in batch.observations} == {"server", "ntfs_resource"}

    def test_an_unknown_observation_kind_is_rejected(self) -> None:
        unknown = dict(server_observation())
        unknown["kind"] = "effective_access"

        with pytest.raises(ValidationError):
            self.batch(observations=[unknown])


class TestCompletionAndReconciliation:
    def test_a_clean_successful_run_may_reconcile_its_scopes(self) -> None:
        envelope = completion(reconciled_scopes=[SCOPE])

        assert envelope.may_reconcile is True
        assert envelope.reconciles(start()) is True

    @pytest.mark.parametrize("status", ["partial", "failed", "canceled"])
    def test_a_non_successful_run_may_not_reconcile(self, status: str) -> None:
        with pytest.raises(ValidationError, match="must not reconcile"):
            completion(status=status, reconciled_scopes=[SCOPE])

    def test_a_successful_run_with_errors_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="never 'succeeded'"):
            completion(error_count=2)

    def test_a_partial_run_reports_errors_and_reconciles_nothing(self) -> None:
        envelope = completion(
            status="partial",
            error_count=1,
            errors=[
                {
                    "code": "access_denied",
                    "message": "READ_CONTROL denied.",
                    "target": "\\\\FS01\\Finance\\Payroll",
                }
            ],
        )

        assert envelope.domain_status is ScanStatus.PARTIAL
        assert envelope.may_reconcile is False
        assert envelope.reconciled_scopes == []

    def test_an_understated_error_count_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="smaller than the number of reported"):
            completion(
                status="partial",
                error_count=0,
                errors=[{"code": "access_denied", "message": "denied"}],
            )

    def test_a_run_may_not_reconcile_a_scope_it_never_declared(self) -> None:
        envelope = completion(reconciled_scopes=[{"kind": "share", "key": "fs02|payroll"}])

        assert envelope.reconciles(start()) is False

    def test_an_incremental_run_may_never_reconcile(self) -> None:
        envelope = completion(reconciled_scopes=[SCOPE])

        assert envelope.reconciles(start(incremental=True)) is False

    def test_a_completion_without_reconciliation_is_always_consistent(self) -> None:
        assert completion(status="failed", error_count=3, errors=[]).reconciles(start()) is True

    def test_completion_converts_to_a_terminal_domain_run(self) -> None:
        run = completion().to_domain(start())

        assert run.status is ScanStatus.SUCCEEDED
        assert run.completed_at == dt.datetime(2026, 9, 14, 8, 5, tzinfo=dt.UTC)
        assert run.duration == dt.timedelta(minutes=5)

    def test_a_partial_run_still_yields_usable_observations(self) -> None:
        run = completion(status="partial", error_count=1, errors=[]).to_domain(start())

        assert run.status.yields_usable_observations is True

    def test_a_failed_run_yields_no_usable_observations(self) -> None:
        run = completion(status="failed", error_count=1, errors=[]).to_domain(start())

        assert run.status.yields_usable_observations is False
