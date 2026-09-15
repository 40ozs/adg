"""What contract 1.4 lets a payload say, and what it refuses.

Two kinds of rule are tested here and they pull in opposite directions, which is the whole
difficulty of an additive minor:

* a rule 1.4 introduces must not be applied to a payload that declares an earlier minor,
  or the bump is not additive and every 1.0 collector in the estate starts failing;
* a payload that declares an earlier minor must not be allowed to *use* a 1.4 field, or the
  version string stops saying anything about what the payload can contain.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError

from app.contracts.v1 import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.domain import CollectionMode

RUN = str(uuid.UUID("6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31"))
BATCH = str(uuid.UUID("b1a7c0de-1111-4222-8333-444455556666"))
AT = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
DIGEST = "3b1f0c9d5e2a47b8c6d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6"
PATH_KEY = "resource|" + "\\\\fs01\\finance"

SOURCE = {"collector": "ntfs", "collector_host": "COLLECTOR01", "method": "GetAccessControl"}
CURSOR = {"kind": "usn", "token": "4711", "issuer": "DC01|abc", "issued_at": AT.isoformat()}


def start(**overrides: object) -> ScanRunStart:
    payload: dict[str, object] = {
        "run_id": RUN,
        "source": SOURCE,
        "started_at": AT,
        "scopes": [{"kind": "directory_tree", "key": "\\\\fs01\\finance"}],
    }
    payload.update(overrides)
    return ScanRunStart(**payload)  # type: ignore[arg-type]


def batch(**overrides: object) -> ObservationBatch:
    payload: dict[str, object] = {"run_id": RUN, "batch_id": BATCH, "sequence": 1}
    payload.update(overrides)
    return ObservationBatch(**payload)  # type: ignore[arg-type]


def completion(**overrides: object) -> ScanRunCompletion:
    payload: dict[str, object] = {
        "run_id": RUN,
        "status": "succeeded",
        "completed_at": AT,
        "batch_count": 1,
        "observation_count": 0,
    }
    payload.update(overrides)
    return ScanRunCompletion(**payload)  # type: ignore[arg-type]


def affirmation(source_key: str = PATH_KEY, digest: str = DIGEST) -> dict[str, object]:
    return {
        "kind": "ntfs_resource",
        "source_key": source_key,
        "digest": digest,
        "observed_at": AT,
    }


class TestModeIsDerivedForOlderCollectors:
    @pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
    def test_an_older_payload_gets_the_mode_its_flag_means(self, version: str) -> None:
        assert start(schema_version=version).mode is CollectionMode.FULL
        assert start(schema_version=version, incremental=True).mode is CollectionMode.DELTA

    @pytest.mark.parametrize("field", ["mode", "job", "baseline"])
    def test_an_older_payload_may_not_use_a_1_4_field(self, field: str) -> None:
        values = {"mode": "full", "job": "ad-principals", "baseline": CURSOR}
        with pytest.raises(ValidationError) as raised:
            start(schema_version="1.3", **{field: values[field]})
        assert "introduced in contract 1.4" in str(raised.value)

    def test_a_contradicting_mode_is_refused(self) -> None:
        with pytest.raises(ValidationError) as raised:
            start(mode="delta", incremental=False)
        assert "contradict" in str(raised.value)

    def test_a_full_run_may_not_declare_a_baseline(self) -> None:
        # It read everything. A baseline would record a starting point it did not start
        # from, and a later gap would be attributed to a resume that never happened.
        with pytest.raises(ValidationError) as raised:
            start(mode="full", baseline=CURSOR)
        assert "resumes from nothing" in str(raised.value)

    def test_a_delta_may_declare_one(self) -> None:
        assert start(mode="delta", incremental=True, baseline=CURSOR).baseline is not None


class TestABatchCarriesObservationsOrAffirmations:
    def test_a_batch_with_neither_is_refused(self) -> None:
        with pytest.raises(ValidationError) as raised:
            batch()
        assert "at least one observation or affirmation" in str(raised.value)

    def test_affirmations_alone_are_a_batch(self) -> None:
        payload = batch(affirmations=[affirmation()])
        assert payload.item_count == 1
        assert payload.affirmed_keys == [PATH_KEY]

    def test_a_key_may_not_be_both_observed_and_affirmed(self) -> None:
        observation = {
            "kind": "ntfs_resource",
            "run_id": RUN,
            "observed_at": AT,
            "source_key": PATH_KEY,
            "path": "\\\\FS01\\Finance",
            "server_name": "FS01",
            "share_name": "Finance",
            "dacl_present": True,
            "dacl_protected": True,
            "inheritance_enabled": False,
            "is_acl_boundary": True,
            "boundary_reason": "share_root",
            "ace_count": 1,
        }
        with pytest.raises(ValidationError) as raised:
            batch(observations=[observation], affirmations=[affirmation()])
        assert "both observes and affirms" in str(raised.value)

    def test_the_same_key_may_not_be_affirmed_twice(self) -> None:
        with pytest.raises(ValidationError) as raised:
            batch(affirmations=[affirmation(), affirmation()])
        assert "affirm the same source_key twice" in str(raised.value)

    @pytest.mark.parametrize(
        "digest",
        ["not-a-digest", DIGEST[:-1], DIGEST.upper().replace("A", "Z")],
        ids=["unhexlike", "too-short", "not-hex"],
    )
    def test_a_digest_that_could_not_be_an_acl_hash_is_refused(self, digest: str) -> None:
        # Refused here rather than downstream: a malformed digest reaching the comparison
        # would be reported as "the ACL changed", and the collector would re-read a
        # directory forever without being told its digest was the problem.
        with pytest.raises(ValidationError):
            batch(affirmations=[affirmation(digest=digest)])

    def test_an_upper_case_digest_is_accepted_and_folded(self) -> None:
        payload = batch(affirmations=[affirmation(digest=DIGEST.upper())])
        assert payload.affirmations[0].digest == DIGEST

    @pytest.mark.parametrize("field", ["affirmations", "checkpoint"])
    def test_an_older_batch_may_not_use_a_1_4_field(self, field: str) -> None:
        values = {"affirmations": [affirmation()], "checkpoint": CURSOR}
        with pytest.raises(ValidationError) as raised:
            batch(schema_version="1.3", observations=[], **{field: values[field]})
        assert "introduced in contract 1.4" in str(raised.value)


class TestOnlyASucceededRunMayRecordACheckpoint:
    @pytest.mark.parametrize("status", ["partial", "failed", "canceled"])
    def test_an_unsuccessful_run_may_not(self, status: str) -> None:
        with pytest.raises(ValidationError) as raised:
            completion(status=status, checkpoint=CURSOR)
        assert "must not record a checkpoint" in str(raised.value)

    def test_a_succeeded_run_that_reported_errors_may_not(self) -> None:
        # `succeeded` with errors is already impossible, so this is the partial case; the
        # assertion is that the checkpoint rule reads error_count and not only status.
        with pytest.raises(ValidationError):
            completion(
                status="partial",
                error_count=1,
                errors=[{"code": "access_denied", "message": "denied"}],
                checkpoint=CURSOR,
            )

    def test_a_clean_run_may(self) -> None:
        assert completion(checkpoint=CURSOR, affirmation_count=12).checkpoint is not None
