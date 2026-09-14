r"""The demo transcripts: valid contract v1, and cut into runs that mean something.

The generator builds every observation as its contract model, so an invalid payload cannot
be produced at all. What this file adds is the two things that check does not cover:

* the transcripts also validate against the **published JSON Schemas** — the document a
  collector author writes against, which is a separate artifact from the Python models and
  has drifted from them before;
* the **cut into runs** is the one a real deployment would produce. A demo whose whole
  estate arrived in one perfect run would never exercise a partial scan, a failed scan, a
  scope that was never reconciled, or a server ADG knows about and cannot see inside.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.contracts.v1 import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.demo.estate import PROFILES, build_estate
from app.demo.transcripts import DemoTranscript, build_transcripts, restamp
from tests.contracts.test_fixtures import OBSERVATION_SCHEMA, validator

PROFILE_NAMES = sorted(PROFILES)


@pytest.fixture(scope="module")
def transcripts() -> tuple[DemoTranscript, ...]:
    return build_transcripts(build_estate("small"))


class TestTheyAreValidContractPayloads:
    def test_every_envelope_validates_against_the_published_schemas(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        start = validator("scan-run-start.schema.json")
        batch = validator("observation-batch.schema.json")
        completion = validator("scan-run-completion.schema.json")

        for transcript in transcripts:
            start.validate(transcript.start)
            for item in transcript.batches:
                batch.validate(item)
            completion.validate(transcript.completion)

    def test_every_observation_validates_against_its_own_schema(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        validators = {kind: validator(name) for kind, name in OBSERVATION_SCHEMA.items()}
        seen: set[str] = set()

        for transcript in transcripts:
            for batch in transcript.batches:
                for observation in batch["observations"]:
                    kind = observation["kind"]
                    validators[kind].validate(observation)
                    seen.add(kind)

        assert seen == set(OBSERVATION_SCHEMA), (
            "The demo estate must exercise every observation kind; missing: "
            f"{sorted(set(OBSERVATION_SCHEMA) - seen)}"
        )

    def test_every_envelope_is_accepted_by_the_backend_models(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        """The schemas say what is well formed; the models add the cross-field rules the API
        actually enforces, and a payload can pass one and be refused by the other."""
        for transcript in transcripts:
            ScanRunStart.model_validate(transcript.start)
            for batch in transcript.batches:
                ObservationBatch.model_validate(batch)
            ScanRunCompletion.model_validate(transcript.completion)

    @pytest.mark.parametrize("profile", PROFILE_NAMES)
    def test_every_profile_produces_valid_payloads(self, profile: str) -> None:
        """Including ``large``, which is the one that crosses the batch-size boundary."""
        for transcript in build_transcripts(build_estate(profile)):
            ScanRunStart.model_validate(transcript.start)
            for batch in transcript.batches:
                ObservationBatch.model_validate(batch)
            ScanRunCompletion.model_validate(transcript.completion)


class TestTheCutIntoRuns:
    def test_there_is_a_run_for_every_collector_kind_this_product_has(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        kinds = {transcript.collector for transcript in transcripts}

        assert kinds == {"active_directory", "local_groups", "smb", "ntfs"}

    def test_one_run_succeeded_one_is_partial_and_one_failed(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        """The three outcomes the collector status page has to distinguish. An estate with
        only successes cannot demonstrate the product's most important behavior — that an
        empty list below a failed scan is unknown rather than empty."""
        outcomes = {transcript.name: transcript.status for transcript in transcripts}

        assert outcomes["ntfs-fs01"] == "succeeded"
        assert outcomes["ntfs-fs02"] == "partial"
        assert outcomes["ntfs-fs03"] == "failed"

    def test_the_failed_run_carries_no_observations_at_all(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        failed = _named(transcripts, "ntfs-fs03")

        assert failed.batches == ()
        assert failed.completion["observation_count"] == 0
        assert failed.completion["error_count"] == 1

    def test_the_partial_run_names_what_it_could_not_read(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        partial = _named(transcripts, "ntfs-fs02")
        errors = partial.completion["errors"]

        assert len(errors) == 2
        assert {error["code"] for error in errors} == {"access_denied", "path_too_long"}
        assert all(error["target"] for error in errors)

    def test_only_a_clean_run_reconciles_its_scopes(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        """Absence may be inferred inside a reconciled scope. A run that hit errors claiming
        one is how "nobody reported it" becomes "it was deleted"."""
        for transcript in transcripts:
            reconciled = transcript.completion["reconciled_scopes"]
            if transcript.status == "succeeded" and not transcript.completion["error_count"]:
                assert reconciled, transcript.name
            else:
                assert reconciled == [], transcript.name

    def test_the_smb_run_for_the_unreadable_server_still_succeeded(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        """A server whose share list is readable and whose file system is not."""
        assert _named(transcripts, "smb-fs03").status == "succeeded"
        assert _named(transcripts, "ntfs-fs03").status == "failed"

    def test_the_local_group_comes_from_a_local_groups_run(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        """Not from the directory run. ``S-1-5-32-544`` is a fact about one machine, and
        attributing it to the domain scan would make it look domain-wide."""
        local = _named(transcripts, "local-groups-fs01")
        sids = {
            observation["sid"]
            for batch in local.batches
            for observation in batch["observations"]
            if observation["kind"] == "principal"
        }
        directory_sids = {
            observation["sid"]
            for batch in _named(transcripts, "ad").batches
            for observation in batch["observations"]
            if observation["kind"] == "principal"
        }

        assert sids == {"S-1-5-32-544"}
        assert "S-1-5-32-544" not in directory_sids

    def test_every_batch_is_sequenced_and_the_last_one_says_so(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        for transcript in transcripts:
            sequences = [batch["sequence"] for batch in transcript.batches]
            assert sequences == list(range(1, len(sequences) + 1)), transcript.name
            finals = [batch["is_final"] for batch in transcript.batches]
            if finals:
                assert finals == [False] * (len(finals) - 1) + [True], transcript.name

    def test_the_completion_counts_what_was_actually_sent(
        self, transcripts: tuple[DemoTranscript, ...]
    ) -> None:
        """A run claiming more than it sent is recorded as partial by the server. A demo
        that tripped that by accident would show every run downgraded for no reason."""
        for transcript in transcripts:
            assert transcript.completion["batch_count"] == len(transcript.batches)
            assert transcript.completion["observation_count"] == transcript.observation_count

    def test_a_large_estate_is_split_across_several_batches(self) -> None:
        large = build_transcripts(build_estate("large"))
        ad = _named(large, "ad")

        assert len(ad.batches) > 1

    def test_the_batch_size_is_honored(self) -> None:
        for transcript in build_transcripts(build_estate("standard"), batch_size=10):
            for batch in transcript.batches:
                assert len(batch["observations"]) <= 10


class TestIdentity:
    def test_run_ids_are_derived_so_a_second_seeding_is_a_replay(self) -> None:
        """The property that makes the seed script safe to run twice: the same estate is
        the same runs, which the ingestion endpoint recognizes and does not re-apply."""
        first = build_transcripts(build_estate("small"))
        second = build_transcripts(build_estate("small"))

        assert [item.run_id for item in first] == [item.run_id for item in second]

    def test_restamping_produces_a_different_run_carrying_the_same_facts(self) -> None:
        original = _named(build_transcripts(build_estate("small")), "ad")
        fresh = restamp(original)

        assert fresh.run_id != original.run_id
        assert len(fresh.batches) == len(original.batches)
        for batch in fresh.batches:
            assert batch["run_id"] == fresh.run_id
            assert all(item["run_id"] == fresh.run_id for item in batch["observations"])
        assert fresh.completion["run_id"] == fresh.run_id

    def test_restamping_keeps_the_source_keys(self) -> None:
        """A source key identifies the object, not the run. If restamping changed one, a
        second seeding would create a second row for every object in the estate."""
        original = _named(build_transcripts(build_estate("small")), "ad")
        fresh = restamp(original)

        def keys(transcript: DemoTranscript) -> list[str]:
            return [
                observation["source_key"]
                for batch in transcript.batches
                for observation in batch["observations"]
            ]

        assert keys(fresh) == keys(original)


def _named(transcripts: Sequence[DemoTranscript], name: str) -> DemoTranscript:
    return next(item for item in transcripts if item.name == name)
