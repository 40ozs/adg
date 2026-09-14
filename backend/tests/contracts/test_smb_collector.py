"""The SMB collector must produce payloads the contract accepts.

A collector that drifts from the contract fails at the worst possible moment: in
production, mid-scan, against a server someone is waiting on. So the collector is run
against a fixed fake estate - no network, no domain, no file server - and the payloads it
writes are validated exactly as a real POST would be: against the published JSON Schemas
*and* through the backend models, which independently re-derive every ``source_key``.

That second check is the one worth having. It compares two separate implementations of the
key derivations, PowerShell's and Python's, against each other; if they ever disagree, the
same share would be ingested twice under different identities and change detection would
quietly stop working.

The fixture covers what a real estate does: ordinary, hidden, administrative, and IPC
shares; a trustee that resolves and an orphaned SID that does not; an audit entry that must
never be reported as access; and a server that could not be reached at all.

Skipped when `pwsh` is unavailable.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from app.contracts.v1.envelopes import ObservationBatch, ScanRunCompletion, ScanRunStart
from tests.contracts.test_fixtures import validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
EXPORTER = (
    REPO_ROOT
    / "collector"
    / "powershell"
    / "smb"
    / "tests"
    / "Export-AdgSmbFixturePayload.ps1"
)

pytestmark = [
    pytest.mark.powershell,
    pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 (pwsh) is not on PATH"),
]


@pytest.fixture(scope="module")
def payloads(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Run the collector against the fake estate and return everything it wrote."""
    output_dir = tmp_path_factory.mktemp("adg-smb-collector")

    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(EXPORTER),
            "-OutputDirectory",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, (
        f"The SMB collector fixture export failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    written = {
        path.name: json.loads(path.read_text(encoding="utf-8-sig"))
        for path in sorted(output_dir.glob("*.json"))
    }
    assert written, f"The exporter wrote nothing: {result.stdout}"
    return written


def batches_of(payloads: dict[str, Any], run: str) -> list[dict[str, Any]]:
    return [value for name, value in payloads.items() if name.startswith(f"{run}-batch-")]


def observations_of(
    payloads: dict[str, Any], run: str, kind: str | None = None
) -> list[dict[str, Any]]:
    items = [item for batch in batches_of(payloads, run) for item in batch["observations"]]
    if kind is None:
        return items
    return [item for item in items if item["kind"] == kind]


class TestEveryPayloadValidates:
    def test_the_exporter_exists(self) -> None:
        assert EXPORTER.exists()

    def test_start_envelopes_validate(self, payloads: dict[str, Any]) -> None:
        schema = validator("scan-run-start.schema.json")
        for name, payload in payloads.items():
            if name.endswith("-start.json"):
                schema.validate(payload)

    def test_batches_validate(self, payloads: dict[str, Any]) -> None:
        schema = validator("observation-batch.schema.json")
        for name, payload in payloads.items():
            if "-batch-" in name:
                schema.validate(payload)

    def test_completions_validate(self, payloads: dict[str, Any]) -> None:
        schema = validator("scan-run-completion.schema.json")
        for name, payload in payloads.items():
            if name.endswith("-completion.json"):
                schema.validate(payload)

    def test_payloads_parse_through_the_backend_models(self, payloads: dict[str, Any]) -> None:
        # Model validation re-derives every source_key, so this asserts that the
        # PowerShell derivations agree with the Python ones.
        start = ScanRunStart.model_validate(payloads["run-01-start.json"])
        completion = ScanRunCompletion.model_validate(payloads["run-01-completion.json"])

        for batch_payload in batches_of(payloads, "run-01"):
            batch = ObservationBatch.model_validate(batch_payload)
            assert batch.run_id == start.run_id

        assert completion.run_id == start.run_id
        assert completion.reconciles(start) is True


class TestTheHealthyRun:
    def test_it_declares_itself_an_smb_collection(self, payloads: dict[str, Any]) -> None:
        source = payloads["run-01-start.json"]["source"]

        assert source["collector"] == "smb"
        assert source["collector_host"] == "COLLECTOR01"

    def test_it_reports_every_share_but_reads_only_the_collected_acls(
        self, payloads: dict[str, Any]
    ) -> None:
        shares = observations_of(payloads, "run-01", "smb_share")
        names = sorted(item["share_name"] for item in shares)

        # All five exist and are recorded; only Finance and Projects had ACLs read.
        assert names == ["Archive$", "C$", "Finance", "IPC$", "Projects"]

        acl_shares = {item["share_name"] for item in observations_of(payloads, "run-01", "smb_ace")}
        assert acl_shares == {"Finance", "Projects"}

    def test_a_share_scope_is_declared_only_where_an_acl_was_read(
        self, payloads: dict[str, Any]
    ) -> None:
        scopes = payloads["run-01-start.json"]["scopes"]
        share_scopes = sorted(item["key"] for item in scopes if item["kind"] == "share")

        # C$ has no share scope, so its unread ACL can never be reconciled into
        # "nobody has access" - the inversion the contract warns about.
        assert share_scopes == ["fs01|finance", "fs01|projects"]
        assert any(item["kind"] == "server" and item["key"] == "fs01" for item in scopes)

    def test_the_administrative_share_is_flagged_by_the_server_not_by_its_name(
        self, payloads: dict[str, Any]
    ) -> None:
        shares = {
            item["share_name"]: item
            for item in observations_of(payloads, "run-01", "smb_share")
        }

        assert shares["C$"]["is_special"] is True
        # Archive$ is hidden but not administrative: an operator's decision, not Windows's.
        assert shares["Archive$"]["is_special"] is False
        assert shares["IPC$"]["share_type"] == "ipc"
        assert "local_path" not in shares["IPC$"]

    def test_share_aces_carry_exactly_one_right_form(self, payloads: dict[str, Any]) -> None:
        for ace in observations_of(payloads, "run-01", "smb_ace"):
            assert ("permission" in ace) != ("access_mask" in ace)

    def test_masks_are_reported_raw(self, payloads: dict[str, Any]) -> None:
        masks = {
            (item["share_name"], item["trustee_sid"]): item["access_mask"]
            for item in observations_of(payloads, "run-01", "smb_ace")
        }

        # GENERIC_ALL, not expanded into the specific rights it implies.
        assert masks[("Projects", "S-1-1-0")] == 0x10000000

    def test_the_audit_entry_is_not_reported_as_access(self, payloads: dict[str, Any]) -> None:
        # The fixture puts an audit ACE on Projects. A SACL entry governs logging, not
        # access; reporting one would fabricate access that does not exist.
        projects = [
            item
            for item in observations_of(payloads, "run-01", "smb_ace")
            if item["share_name"] == "Projects"
        ]

        assert len(projects) == 2
        assert sorted(item["ace_type"] for item in projects) == ["allow", "deny"]

    def test_the_orphaned_sid_is_kept_and_reported_as_unresolved(
        self, payloads: dict[str, Any]
    ) -> None:
        orphan = "S-1-5-21-1004336348-1177238915-682003330-9999"

        aces = [
            item
            for item in observations_of(payloads, "run-01", "smb_ace")
            if item["trustee_sid"] == orphan
        ]
        assert len(aces) == 1, "The ACE must survive: Windows still grants that access."

        principals = observations_of(payloads, "run-01", "principal")
        assert [item["sid"] for item in principals] == [orphan]
        assert principals[0]["principal_kind"] == "unresolved"
        assert principals[0]["unresolved_reason"] == "lookup_failed"
        # Never a guessed name.
        assert "display_name" not in principals[0]

    def test_a_resolvable_trustee_produces_no_principal_observation(
        self, payloads: dict[str, Any]
    ) -> None:
        # Naming a resolved principal belongs to the collectors that know what kind of
        # principal a SID is. This one does not, and must not guess.
        sids = {item["sid"] for item in observations_of(payloads, "run-01", "principal")}

        assert "S-1-5-21-1004336348-1177238915-682003330-1202" not in sids

    def test_batches_are_sequenced_with_one_final_marker(self, payloads: dict[str, Any]) -> None:
        batches = sorted(batches_of(payloads, "run-01"), key=lambda item: item["sequence"])

        assert [item["sequence"] for item in batches] == list(range(1, len(batches) + 1))
        assert [item["is_final"] for item in batches] == [False] * (len(batches) - 1) + [True]
        assert len({item["batch_id"] for item in batches}) == len(batches)

    def test_no_source_key_repeats_within_a_batch(self, payloads: dict[str, Any]) -> None:
        for batch in batches_of(payloads, "run-01"):
            keys = [item["source_key"] for item in batch["observations"]]
            assert len(keys) == len(set(keys))

    def test_it_reconciles_only_what_it_declared(self, payloads: dict[str, Any]) -> None:
        start = payloads["run-01-start.json"]
        completion = payloads["run-01-completion.json"]

        assert completion["status"] == "succeeded"
        assert completion["error_count"] == 0

        declared = {(item["kind"], item["key"]) for item in start["scopes"]}
        reconciled = {(item["kind"], item["key"]) for item in completion["reconciled_scopes"]}
        assert reconciled <= declared
        assert reconciled == declared

    def test_it_reports_no_derived_access(self, payloads: dict[str, Any]) -> None:
        serialized = json.dumps(payloads)

        for forbidden in ("effective", "can_access", "resolved_access", "risk_"):
            assert forbidden not in serialized


class TestTheUnreachableServer:
    def test_it_fails_rather_than_reporting_an_empty_server(
        self, payloads: dict[str, Any]
    ) -> None:
        completion = payloads["run-02-completion.json"]

        assert completion["status"] == "failed"
        assert completion["error_count"] == 1
        assert completion["errors"][0]["code"] == "rpc_unavailable"
        assert completion["errors"][0]["target"] == "FS-OFFLINE"

    def test_it_emits_no_observation_for_a_host_it_never_saw(
        self, payloads: dict[str, Any]
    ) -> None:
        assert observations_of(payloads, "run-02") == []
        assert payloads["run-02-completion.json"]["observation_count"] == 0

    def test_it_declares_the_scope_and_reconciles_nothing(self, payloads: dict[str, Any]) -> None:
        # Declaring records the intent; refusing to reconcile is what stops the backend
        # concluding that every share on FS-OFFLINE was deleted. Unseen is not gone.
        start = payloads["run-02-start.json"]
        completion = payloads["run-02-completion.json"]

        assert [item["key"] for item in start["scopes"]] == ["fs-offline"]
        assert completion["reconciled_scopes"] == []

    def test_the_failed_run_claims_no_reconciliation_through_the_model_either(
        self, payloads: dict[str, Any]
    ) -> None:
        # reconciles() asks whether a reconciliation claim is *valid*, so an empty one is
        # vacuously true. What matters here is that the claim is empty at all.
        completion = ScanRunCompletion.model_validate(payloads["run-02-completion.json"])

        assert completion.status == "failed"
        assert completion.reconciled_scopes == []

    def test_the_contract_would_reject_a_failed_run_that_claimed_a_scope(
        self, payloads: dict[str, Any]
    ) -> None:
        # The guarantee is structural, not merely a convention this collector follows: a
        # partial or failed scan is incapable of marking anything absent.
        forged = dict(payloads["run-02-completion.json"])
        forged["reconciled_scopes"] = [{"kind": "server", "key": "fs-offline"}]

        with pytest.raises(ValidationError):
            validator("scan-run-completion.schema.json").validate(forged)
