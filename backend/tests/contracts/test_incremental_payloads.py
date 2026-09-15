r"""The PowerShell side and the Python side must agree about contract 1.4.

Every field the minor added -- ``mode``, ``job``, ``baseline``, ``affirmations``,
``checkpoint``, ``affirmation_count`` -- exists twice: once in the envelope builders the
collectors call, once in the pydantic models the API validates with, and once more in the
published JSON Schema a collector author reads. Three descriptions of one contract, written
in two languages, with nothing but this test stopping them from drifting.

So the fixture generator runs the *real* builders and this validates the bytes it wrote,
exactly as a POST would be validated: against the schemas, then through the models.

Skipped when ``pwsh`` is unavailable.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest

from app.contracts.v1.envelopes import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.domain import CheckpointKind, CollectionMode
from app.ingestion import plan_batch
from tests.contracts.test_fixtures import validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
EXPORTER = (
    REPO_ROOT
    / "collector"
    / "powershell"
    / "orchestrator"
    / "tests"
    / "Export-AdgIncrementalPayload.ps1"
)

pytestmark = [
    pytest.mark.powershell,
    pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 (pwsh) is not on PATH"),
]


@pytest.fixture(scope="module")
def payloads(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    directory = tmp_path_factory.mktemp("incremental")
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(EXPORTER),
            "-OutputDirectory",
            str(directory),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"the exporter failed:\n{result.stdout}\n{result.stderr}"
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json")
    }


class TestADeltaRun:
    def test_the_start_envelope_validates_against_the_published_schema(
        self, payloads: dict[str, Any]
    ) -> None:
        validator("scan-run-start.schema.json").validate(payloads["delta-start"])

    def test_the_start_envelope_is_accepted_by_the_model(self, payloads: dict[str, Any]) -> None:
        start = ScanRunStart.model_validate(payloads["delta-start"])
        assert start.mode is CollectionMode.DELTA
        assert start.incremental is True
        assert start.job == "ad-principals"
        assert start.baseline is not None
        assert start.baseline.kind is CheckpointKind.USN

    def test_the_baseline_names_both_halves_of_the_issuer(self, payloads: dict[str, Any]) -> None:
        # dsServiceName and invocationId. The first changes when the collector binds a
        # different DC; the second when the same DC is restored from backup and starts
        # reissuing USNs it has already handed out. A cursor compared on either alone
        # survives one of those two and skips whatever fell below it.
        issuer = payloads["delta-start"]["baseline"]["issuer"]
        assert "CN=NTDS Settings" in issuer
        assert "|" in issuer
        assert len(issuer.split("|")[1]) == 36

    def test_the_batch_carries_the_cursor_it_covers(self, payloads: dict[str, Any]) -> None:
        validator("observation-batch.schema.json").validate(payloads["delta-batch"])
        batch = ObservationBatch.model_validate(payloads["delta-batch"])
        assert batch.checkpoint is not None
        assert batch.checkpoint.token == "184987"

    def test_the_completion_records_the_cursor_the_next_run_resumes_from(
        self, payloads: dict[str, Any]
    ) -> None:
        validator("scan-run-completion.schema.json").validate(payloads["delta-completion"])
        completion = ScanRunCompletion.model_validate(payloads["delta-completion"])
        assert completion.checkpoint is not None
        assert completion.status == "succeeded"

    def test_a_delta_reconciles_nothing(self, payloads: dict[str, Any]) -> None:
        # The whole reason a delta is marked incremental. It reads what the directory says
        # has changed, and a deleted principal produces no change record -- so "nothing
        # arrived" and "nothing exists" are indistinguishable from inside it.
        assert payloads["delta-completion"]["reconciled_scopes"] == []


class TestAnAffirmingRun:
    def test_the_batch_validates_with_no_observations_at_all(
        self, payloads: dict[str, Any]
    ) -> None:
        # What a quiet tree produces, and the case the batch envelope was relaxed to allow.
        validator("observation-batch.schema.json").validate(payloads["affirm-batch"])
        batch = ObservationBatch.model_validate(payloads["affirm-batch"])
        assert batch.observations == []
        assert len(batch.affirmations) == 2
        assert batch.item_count == 2

    def test_every_affirmed_key_is_one_the_planner_can_resolve(
        self, payloads: dict[str, Any]
    ) -> None:
        # The planner re-derives the storage key from the source_key and refuses one that
        # does not round-trip, because an affirmation carries no payload to re-derive it
        # from -- so a malformed key would become a lookup that silently matches nothing.
        plan = plan_batch(ObservationBatch.model_validate(payloads["affirm-batch"]))
        assert [row.object_key for row in plan.affirmations] == [
            "\\\\fs01\\finance\\reports",
            "\\\\fs01\\finance\\archive",
        ]
        assert plan.affirmation_count == 2
        assert plan.observation_count == 0
        assert not plan.is_empty

    def test_an_affirming_run_still_reconciles_its_scope(self, payloads: dict[str, Any]) -> None:
        # The property that makes the whole mechanism worth having. The scan read every
        # descriptor; what it did not do is re-transmit the unchanged ones. It has therefore
        # enumerated its scope, and keeps the right to say what is no longer in it.
        validator("scan-run-completion.schema.json").validate(payloads["affirm-completion"])
        completion = ScanRunCompletion.model_validate(payloads["affirm-completion"])
        assert completion.affirmation_count == 2
        assert len(completion.reconciled_scopes) == 1

        start = ScanRunStart.model_validate(payloads["affirm-start"])
        assert start.mode is CollectionMode.FULL
        assert completion.reconciles(start)


class TestTheMinorIsAdditive:
    @pytest.mark.parametrize(
        "name", ["delta-start", "delta-batch", "delta-completion", "affirm-batch"]
    )
    def test_a_payload_that_uses_a_1_4_field_declares_1_4(
        self, payloads: dict[str, Any], name: str
    ) -> None:
        assert payloads[name]["schema_version"] == "1.4"

    def test_a_payload_that_uses_none_of_them_still_declares_what_it_was_written_against(
        self, payloads: dict[str, Any]
    ) -> None:
        # The affirming run's *start* carries mode and job, so it is 1.4. Its completion
        # carries affirmation_count, so it is too. What matters is the rule that produced
        # them: the version is bumped by the fields present, never by the collector's build.
        assert payloads["affirm-start"]["schema_version"] == "1.4"
        assert payloads["affirm-completion"]["schema_version"] == "1.4"
