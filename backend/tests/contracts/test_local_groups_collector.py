"""The local-groups collector must produce payloads the contract accepts.

The collector runs offline against its host fixture
(``collector/powershell/localgroups/tests/fixtures/hosts.json``) and the payloads it writes are
validated exactly as a real POST would be: against the published JSON Schemas *and* through the
backend models, which re-derive every ``source_key``. The second check pins the PowerShell key
derivations for host-scoped principals and local membership edges to the normative Python
ones, on real collector output — a disagreement would split one local group into two.

``tests/db/test_graph_derivation.py`` replays the same output through the ingestion API and
checks what the projector derives from it.

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
from tests.contracts.test_fixtures import validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
COLLECTOR_ROOT = REPO_ROOT / "collector" / "powershell" / "localgroups"
COLLECTOR = COLLECTOR_ROOT / "Invoke-AdgLocalGroupScan.ps1"
HOST_FIXTURE = COLLECTOR_ROOT / "tests" / "fixtures" / "hosts.json"

pytestmark = [
    pytest.mark.powershell,
    pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 (pwsh) is not on PATH"),
]


def run_offline(output: pathlib.Path, *hosts: str) -> dict[str, dict[str, Any]]:
    """Run the collector offline over ``hosts``; return each host's payloads by file name."""
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"& '{COLLECTOR}' -ComputerName {','.join(hosts)} -Offline "
            f"-OutputDirectory '{output}' -HostFixture '{HOST_FIXTURE}'; exit $LASTEXITCODE",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode in (0, 2), (
        f"The collector failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    runs: dict[str, dict[str, Any]] = {}
    for host in hosts:
        directory = output / host.lower()
        runs[host.lower()] = {
            path.name: json.loads(path.read_text(encoding="utf-8-sig"))
            for path in sorted(directory.glob("*.json"))
        }
    return runs


@pytest.fixture(scope="module")
def offline(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return run_offline(tmp_path_factory.mktemp("adg-local-groups"), "FS01", "FS02", "DC01")


class TestOfflineOutput:
    def test_the_collector_and_its_fixture_exist(self) -> None:
        assert COLLECTOR.exists()
        assert HOST_FIXTURE.exists()

    def test_every_envelope_validates_against_the_published_schemas(
        self, offline: dict[str, dict[str, Any]]
    ) -> None:
        for payloads in offline.values():
            validator("scan-run-start.schema.json").validate(payloads["start.json"])
            validator("scan-run-completion.schema.json").validate(payloads["completion.json"])
            for name, payload in payloads.items():
                if name.startswith("batch-"):
                    validator("observation-batch.schema.json").validate(payload)

    def test_each_observation_validates_against_its_own_schema(
        self, offline: dict[str, dict[str, Any]]
    ) -> None:
        schemas = {
            "principal": validator("principal-observation.schema.json"),
            "membership_edge": validator("membership-observation.schema.json"),
        }
        observations = offline["fs01"]["batch-001.json"]["observations"]
        assert observations
        for observation in observations:
            schemas[observation["kind"]].validate(observation)

    def test_the_payloads_parse_through_the_backend_models(
        self, offline: dict[str, dict[str, Any]]
    ) -> None:
        for host in ("fs01", "fs02"):
            payloads = offline[host]
            start = ScanRunStart.model_validate(payloads["start.json"])
            batch = ObservationBatch.model_validate(payloads["batch-001.json"])
            completion = ScanRunCompletion.model_validate(payloads["completion.json"])
            assert start.run_id == batch.run_id == completion.run_id
            assert start.source.collector.value == "local_groups"
            assert [(s.kind.value, s.key) for s in start.scopes] == [("local_groups_host", host)]
            assert [(s.kind.value, s.key) for s in completion.reconciled_scopes] == [
                ("local_groups_host", host)
            ]

    def test_local_groups_are_host_scoped_and_every_member_is_an_edge(
        self, offline: dict[str, dict[str, Any]]
    ) -> None:
        observations = offline["fs01"]["batch-001.json"]["observations"]
        principals = [o for o in observations if o["kind"] == "principal"]
        edges = [o for o in observations if o["kind"] == "membership_edge"]
        assert {o["principal_kind"] for o in principals} == {"local_group"}
        assert all(o["source_key"].startswith("principal|fs01|") for o in principals)
        assert all(o["edge_kind"] == "local_group_member" and o["host_key"] for o in edges)
        orphan = next(o for o in edges if o["member_sid"].endswith("-9999"))
        assert orphan["member_kind"] == "unresolved"

    def test_a_domain_controller_is_refused_and_reconciles_nothing(
        self, offline: dict[str, dict[str, Any]]
    ) -> None:
        completion = offline["dc01"]["completion.json"]
        assert completion["status"] == "failed"
        assert completion["reconciled_scopes"] == []
        assert [error["code"] for error in completion["errors"]] == ["domain_controller"]
        assert not [name for name in offline["dc01"] if name.startswith("batch-")]
