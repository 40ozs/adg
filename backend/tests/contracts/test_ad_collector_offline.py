"""The Active Directory collector must produce payloads the contract accepts.

The collector runs in offline mode against a synthetic directory fixture and the payloads
it writes are validated exactly as a real POST would be: against the published JSON Schemas
*and* through the backend models, which independently re-derive every ``source_key``.

That second check is the one worth having. It compares two separate implementations of the
key derivations — the PowerShell ones in ``AdgCollector.Common.psm1`` and the normative
Python ones in ``app.contracts.v1.keys`` — against each other, on real collector output
rather than on a hand-written example. A collector that derived keys differently would
silently create a second row for every object ADG already knows about.

Skipped when `pwsh` is unavailable.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest

from app.contracts.v1.envelopes import ObservationBatch, ScanRunCompletion, ScanRunStart
from tests.contracts.test_fixtures import validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
COLLECTOR = REPO_ROOT / "collector" / "powershell" / "ad" / "Invoke-AdgAdCollector.ps1"
DIRECTORY_FIXTURE = (
    REPO_ROOT / "collector" / "powershell" / "tests" / "fixtures" / "corp-domain.json"
)

pytestmark = [
    pytest.mark.powershell,
    pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 (pwsh) is not on PATH"),
]


@pytest.fixture(scope="module")
def offline_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Run the collector offline against the fixture and return what it wrote."""
    output_dir = tmp_path_factory.mktemp("adg-ad-collector")

    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(COLLECTOR),
            "-Offline",
            "-OutputDirectory",
            str(output_dir),
            "-DirectoryFixture",
            str(DIRECTORY_FIXTURE),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    # 0 is a clean run; 2 would mean partial coverage, which this fixture must not produce.
    assert result.returncode == 0, (
        f"The collector failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    payloads: dict[str, Any] = {}
    for name in ("start.json", "batch-001.json", "completion.json"):
        path = output_dir / name
        assert path.exists(), f"The collector did not write {name}: {result.stdout}"
        payloads[name] = json.loads(path.read_text(encoding="utf-8-sig"))

    stream = (output_dir / "envelopes.ndjson").read_text(encoding="utf-8-sig")
    payloads["envelopes"] = [json.loads(line) for line in stream.splitlines() if line.strip()]
    payloads["batch_files"] = sorted(output_dir.glob("batch-*.json"))
    return payloads


class TestOfflineOutput:
    def test_the_collector_exists(self) -> None:
        assert COLLECTOR.exists()
        assert DIRECTORY_FIXTURE.exists()

    def test_the_start_envelope_validates(self, offline_run: dict[str, Any]) -> None:
        validator("scan-run-start.schema.json").validate(offline_run["start.json"])

    def test_every_batch_validates(self, offline_run: dict[str, Any]) -> None:
        batch_validator = validator("observation-batch.schema.json")
        for path in offline_run["batch_files"]:
            batch_validator.validate(json.loads(path.read_text(encoding="utf-8-sig")))

    def test_the_completion_validates(self, offline_run: dict[str, Any]) -> None:
        validator("scan-run-completion.schema.json").validate(offline_run["completion.json"])

    def test_each_observation_validates_against_its_own_schema(
        self, offline_run: dict[str, Any]
    ) -> None:
        schemas = {
            "principal": validator("principal-observation.schema.json"),
            "membership_edge": validator("membership-observation.schema.json"),
        }
        observations = offline_run["batch-001.json"]["observations"]
        assert observations, "The run produced no observations."
        for observation in observations:
            schemas[observation["kind"]].validate(observation)

    def test_the_payloads_parse_through_the_backend_models(
        self, offline_run: dict[str, Any]
    ) -> None:
        # Model validation re-derives every source_key, so this asserts that the
        # PowerShell derivations agree with the normative Python ones.
        start = ScanRunStart.model_validate(offline_run["start.json"])
        batch = ObservationBatch.model_validate(offline_run["batch-001.json"])
        completion = ScanRunCompletion.model_validate(offline_run["completion.json"])

        assert batch.run_id == start.run_id == completion.run_id
        assert completion.status == "succeeded"
        assert completion.reconciles(start) is True

    def test_the_observations_convert_to_domain_objects(self, offline_run: dict[str, Any]) -> None:
        batch = ObservationBatch.model_validate(offline_run["batch-001.json"])
        for observation in batch.observations:
            observation.to_domain()

    def test_the_run_declares_the_domain_scope_and_reconciles_it(
        self, offline_run: dict[str, Any]
    ) -> None:
        start = offline_run["start.json"]
        completion = offline_run["completion.json"]

        assert start["incremental"] is False
        assert [scope["kind"] for scope in start["scopes"]] == ["domain"]
        assert completion["reconciled_scopes"] == start["scopes"]

    def test_the_ndjson_stream_carries_the_same_envelopes_in_send_order(
        self, offline_run: dict[str, Any]
    ) -> None:
        envelopes = offline_run["envelopes"]
        batch_count = len(offline_run["batch_files"])

        assert len(envelopes) == batch_count + 2
        assert envelopes[0] == offline_run["start.json"]
        assert envelopes[-1] == offline_run["completion.json"]

    def test_the_collector_reports_only_the_two_kinds_it_collects(
        self, offline_run: dict[str, Any]
    ) -> None:
        kinds = {item["kind"] for item in offline_run["batch-001.json"]["observations"]}
        assert kinds == {"principal", "membership_edge"}

    def test_nesting_is_preserved_rather_than_flattened(self, offline_run: dict[str, Any]) -> None:
        domain = "S-1-5-21-1004336348-1177238915-682003330"
        edges = {
            (item["group_sid"], item["member_sid"])
            for item in offline_run["batch-001.json"]["observations"]
            if item["kind"] == "membership_edge"
        }

        # Finance-RW contains Finance-Team, which contains Alice.
        assert (f"{domain}-1202", f"{domain}-1201") in edges
        assert (f"{domain}-1201", f"{domain}-1104") in edges
        # The closure of those two is exactly what must not be sent.
        assert (f"{domain}-1202", f"{domain}-1104") not in edges

    def test_primary_group_membership_is_reported(self, offline_run: dict[str, Any]) -> None:
        domain = "S-1-5-21-1004336348-1177238915-682003330"
        primary = {
            (item["group_sid"], item["member_sid"])
            for item in offline_run["batch-001.json"]["observations"]
            if item["kind"] == "membership_edge" and item["edge_kind"] == "primary_group"
        }
        assert (f"{domain}-513", f"{domain}-1104") in primary

    def test_the_collector_reports_no_derived_access(self, offline_run: dict[str, Any]) -> None:
        serialized = json.dumps(offline_run["batch-001.json"])

        for forbidden in ("effective", "can_access", "resolved_access", "expanded"):
            assert forbidden not in serialized
