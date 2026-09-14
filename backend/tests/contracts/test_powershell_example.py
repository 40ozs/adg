"""The PowerShell example must produce payloads the contract accepts.

A worked example that drifts from the contract is worse than no example: a collector author
copies it and gets rejected. So the example is executed in dry-run mode and its output is
validated the same way a real POST would be — against the published JSON Schemas *and*
through the backend models, which independently re-derive every ``source_key``.

That second check is the valuable one: it compares two separate implementations of the key
derivation, PowerShell's and Python's, against each other.

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
EXAMPLE = REPO_ROOT / "collector" / "powershell" / "examples" / "Send-AdgScanRun.ps1"

pytestmark = [
    pytest.mark.powershell,
    pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 (pwsh) is not on PATH"),
]


@pytest.fixture(scope="module")
def dry_run_output(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Run the example with -DryRun and return the payloads it wrote."""
    output_dir = tmp_path_factory.mktemp("adg-example")

    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(EXAMPLE),
            "-DryRun",
            "-OutputDirectory",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, (
        f"The example failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    payloads = {}
    for name in ("start.json", "batch-001.json", "completion.json"):
        path = output_dir / name
        assert path.exists(), f"The example did not write {name}: {result.stdout}"
        payloads[name] = json.loads(path.read_text(encoding="utf-8-sig"))
    return payloads


class TestExampleOutput:
    def test_the_example_file_exists(self) -> None:
        assert EXAMPLE.exists()

    def test_the_start_envelope_validates(self, dry_run_output: dict[str, Any]) -> None:
        validator("scan-run-start.schema.json").validate(dry_run_output["start.json"])

    def test_the_batch_validates(self, dry_run_output: dict[str, Any]) -> None:
        validator("observation-batch.schema.json").validate(dry_run_output["batch-001.json"])

    def test_the_completion_validates(self, dry_run_output: dict[str, Any]) -> None:
        validator("scan-run-completion.schema.json").validate(dry_run_output["completion.json"])

    def test_the_payloads_parse_through_the_backend_models(
        self, dry_run_output: dict[str, Any]
    ) -> None:
        # Model validation re-derives every source_key, so this asserts that the
        # PowerShell derivations agree with the Python ones.
        start = ScanRunStart.model_validate(dry_run_output["start.json"])
        batch = ObservationBatch.model_validate(dry_run_output["batch-001.json"])
        completion = ScanRunCompletion.model_validate(dry_run_output["completion.json"])

        assert batch.run_id == start.run_id == completion.run_id
        assert completion.reconciles(start) is True

    def test_the_example_covers_the_main_observation_kinds(
        self, dry_run_output: dict[str, Any]
    ) -> None:
        kinds = {item["kind"] for item in dry_run_output["batch-001.json"]["observations"]}

        assert kinds == {
            "server",
            "smb_share",
            "smb_ace",
            "principal",
            "membership_edge",
            "ntfs_resource",
            "ntfs_ace",
        }

    def test_the_ace_flags_helper_produced_the_expected_byte(
        self, dry_run_output: dict[str, Any]
    ) -> None:
        aces = [
            item
            for item in dry_run_output["batch-001.json"]["observations"]
            if item["kind"] == "ntfs_ace"
        ]

        # ContainerInherit | ObjectInherit, not inherited: 0x02 | 0x01.
        assert [ace["ace_flags"] for ace in aces] == [0x03]
        assert [ace["source"] for ace in aces] == ["explicit"]

    def test_the_share_ace_carries_exactly_one_right_form(
        self, dry_run_output: dict[str, Any]
    ) -> None:
        share_aces = [
            item
            for item in dry_run_output["batch-001.json"]["observations"]
            if item["kind"] == "smb_ace"
        ]

        for ace in share_aces:
            assert ("permission" in ace) != ("access_mask" in ace)

    def test_the_example_reports_no_derived_access(self, dry_run_output: dict[str, Any]) -> None:
        serialized = json.dumps(dry_run_output)

        for forbidden in ("effective", "can_access", "resolved_access"):
            assert forbidden not in serialized
