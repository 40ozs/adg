r"""The NTFS collector must produce payloads the contract accepts, and hashes ADG agrees with.

A collector that drifts from the contract fails at the worst possible moment: in production,
mid-scan, against a share someone is waiting on. So the collector is run against a fixed fake
estate - no network, no file server, no ACL - and the payloads it writes are validated
exactly as a real POST would be: against the published JSON Schemas, through the backend
models, and through the ingestion planner.

Three of the checks below compare two independent implementations against each other, and
they are the ones worth having:

* **Source keys.** Model validation re-derives every ``source_key`` from
  :mod:`app.contracts.v1.keys`. If PowerShell and Python ever disagreed, the same directory
  would be ingested twice under different identities and change detection would quietly stop
  working.
* **The ACL normal form.** :func:`app.domain.normalize_acl` re-derives every ``acl_hash``
  from the ``ntfs_ace`` observations the collector sent. A divergence here would not fail
  anything loudly; it would show up as a permanent, unexplainable disagreement on every
  directory in the estate, which is worse.
* **The planner.** ``plan_batch`` turns the real payloads into the rows that would be
  stored, including its own ``acl_hash`` verification. A batch the collector produces must
  be one the server accepts.

The fixture covers what a real estate does: an ordinary root with inheritance intact, a
protected DACL, an empty DACL, a NULL DACL, an INHERIT_ONLY entry, a Deny ahead of an Allow,
an orphaned SID, an ACE type the contract cannot express, and a root that is not there.

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
from app.domain import ACL_NORMAL_FORM_VERSION, AceType, AclAceFacts, normalize_acl
from app.ingestion import plan_batch
from tests.contracts.test_fixtures import validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
EXPORTER = (
    REPO_ROOT / "collector" / "powershell" / "ntfs" / "tests" / "Export-AdgNtfsFixturePayload.ps1"
)

FINANCE = "\\\\FS01\\Finance"
LOCKED = "\\\\FS01\\Locked"
WIDE = "\\\\FS01\\Wide"
ODD = "\\\\FS01\\Odd"

pytestmark = [
    pytest.mark.powershell,
    pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 (pwsh) is not on PATH"),
]


@pytest.fixture(scope="module")
def payloads(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Run the collector against the fake estate and return everything it wrote."""
    output_dir = tmp_path_factory.mktemp("adg-ntfs-collector")

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
        f"The NTFS collector fixture export failed (exit {result.returncode}).\n"
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


def resource_of(payloads: dict[str, Any], run: str) -> dict[str, Any]:
    resources = observations_of(payloads, run, "ntfs_resource")
    assert len(resources) == 1, f"{run} should describe exactly one directory"
    return resources[0]


def recompute(resource: dict[str, Any], aces: list[dict[str, Any]]) -> str:
    """The digest the backend derives from the entries the collector sent."""
    return normalize_acl(
        dacl_present=resource["dacl_present"],
        dacl_protected=resource.get("dacl_protected", False),
        aces=[
            AclAceFacts(
                trustee_sid=ace["trustee_sid"],
                ace_type=AceType(ace["ace_type"]),
                access_mask=ace["access_mask"],
                ace_flags=ace["ace_flags"],
                order_index=ace.get("order_index"),
            )
            for ace in aces
        ],
    ).digest


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
        # Model validation re-derives every source_key, so this asserts that the PowerShell
        # derivations agree with the Python ones.
        for name, payload in payloads.items():
            if name.endswith("-start.json"):
                ScanRunStart.model_validate(payload)
            elif name.endswith("-completion.json"):
                ScanRunCompletion.model_validate(payload)
            else:
                ObservationBatch.model_validate(payload)

    def test_every_batch_plans_into_rows(self, payloads: dict[str, Any]) -> None:
        # The planner runs its own acl_hash verification over whichever batches carry a
        # directory's whole DACL, so this is also the server-side half of that check.
        for name, payload in payloads.items():
            if "-batch-" in name:
                plan = plan_batch(ObservationBatch.model_validate(payload))
                assert plan.observation_count == len(payload["observations"])


class TestTheCollectorReconcilesNothing:
    def test_every_run_is_incremental(self, payloads: dict[str, Any]) -> None:
        # A share-root read has enumerated no directory tree. Reconciling the directory_tree
        # scope it declares would mark every directory under the root as deleted.
        starts = [value for name, value in payloads.items() if name.endswith("-start.json")]
        assert starts
        assert all(start["incremental"] is True for start in starts)

    def test_no_completion_reconciles_a_scope(self, payloads: dict[str, Any]) -> None:
        completions = [
            value for name, value in payloads.items() if name.endswith("-completion.json")
        ]
        assert completions
        assert all(completion["reconciled_scopes"] == [] for completion in completions)

    def test_a_clean_run_still_reconciles_nothing(self, payloads: dict[str, Any]) -> None:
        # The interesting case: 'succeeded' with no errors is exactly when a collector would
        # normally be allowed to reconcile, and this one still may not.
        completion = payloads["run-01-completion.json"]
        assert completion["status"] == "succeeded"
        assert completion["error_count"] == 0
        assert completion["reconciled_scopes"] == []

    def test_the_scope_names_the_tree_a_later_walk_will_reconcile(
        self, payloads: dict[str, Any]
    ) -> None:
        scopes = payloads["run-01-start.json"]["scopes"]
        assert scopes == [{"kind": "directory_tree", "key": FINANCE.lower()}]

    def test_the_run_declares_itself_an_ntfs_collector(self, payloads: dict[str, Any]) -> None:
        source = payloads["run-01-start.json"]["source"]
        assert source["collector"] == "ntfs"
        assert source["method"] == "DirectorySecurity.GetSecurityDescriptorBinaryForm"


class TestAnOrdinaryShareRoot:
    def test_the_directory_and_every_entry_are_reported(self, payloads: dict[str, Any]) -> None:
        resource = resource_of(payloads, "run-01")
        assert resource["path"] == FINANCE
        assert resource["server_name"] == "FS01"
        assert resource["share_name"] == "Finance"
        assert len(observations_of(payloads, "run-01", "ntfs_ace")) == resource["ace_count"]

    def test_the_root_is_a_boundary_although_inheritance_is_intact(
        self, payloads: dict[str, Any]
    ) -> None:
        # Its parent lies outside the share, so there is nothing to compare against.
        resource = resource_of(payloads, "run-01")
        assert resource["inheritance_enabled"] is True
        assert resource["dacl_protected"] is False
        assert resource["is_acl_boundary"] is True
        assert resource["depth_from_share_root"] == 0

    def test_an_inherited_entry_is_marked_inherited(self, payloads: dict[str, Any]) -> None:
        inherited = [
            ace
            for ace in observations_of(payloads, "run-01", "ntfs_ace")
            if ace["source"] == "inherited"
        ]
        assert len(inherited) == 1
        # source and the INHERITED bit are two spellings of one fact, and the contract
        # rejects a payload where they disagree.
        assert inherited[0]["ace_flags"] & 0x10

    def test_a_deny_keeps_its_place_ahead_of_the_allows(self, payloads: dict[str, Any]) -> None:
        aces = sorted(
            observations_of(payloads, "run-01", "ntfs_ace"), key=lambda a: a["order_index"]
        )
        assert aces[0]["ace_type"] == "deny"
        assert [ace["order_index"] for ace in aces] == list(range(len(aces)))

    def test_an_inherit_only_entry_is_kept(self, payloads: dict[str, Any]) -> None:
        # It grants nothing on this directory and everything on its children; dropping it
        # would hide a grant that reaches every subdirectory.
        inherit_only = [
            ace
            for ace in observations_of(payloads, "run-01", "ntfs_ace")
            if ace["ace_flags"] & 0x08
        ]
        assert len(inherit_only) == 1

    def test_a_generic_mask_is_not_expanded(self, payloads: dict[str, Any]) -> None:
        # GENERIC_ALL, exactly as stored. Windows maps it through the object's generic
        # mapping at access time; doing it here would bake one interpretation into a fact.
        masks = {ace["access_mask"] for ace in observations_of(payloads, "run-01", "ntfs_ace")}
        assert 0x10000000 in masks

    def test_an_orphaned_trustee_keeps_its_ace_and_gains_a_principal(
        self, payloads: dict[str, Any]
    ) -> None:
        principals = observations_of(payloads, "run-01", "principal")
        assert len(principals) == 1
        assert principals[0]["principal_kind"] == "unresolved"
        # The contract forbids a name on an unresolved principal; none may be invented.
        assert "display_name" not in principals[0]
        assert principals[0]["sid"] in {
            ace["trustee_sid"] for ace in observations_of(payloads, "run-01", "ntfs_ace")
        }

    def test_no_ace_claims_an_inheritance_origin_this_collector_cannot_know(
        self, payloads: dict[str, Any]
    ) -> None:
        # Naming the ancestor an entry came from needs the Win32 GetInheritanceSource, which
        # this collector does not call. Reporting a guess would send somebody to the wrong
        # folder to make a fix.
        assert all(
            "inherited_from" not in ace for ace in observations_of(payloads, "run-01", "ntfs_ace")
        )


class TestTheAclHash:
    def test_the_collector_and_the_backend_derive_the_same_digest(
        self, payloads: dict[str, Any]
    ) -> None:
        # The cross-language check. Two independent implementations of the normal form,
        # compared over a real DACL with a Deny, an inherited entry, an INHERIT_ONLY entry,
        # a generic mask, and an orphaned SID.
        resource = resource_of(payloads, "run-01")
        aces = observations_of(payloads, "run-01", "ntfs_ace")
        assert resource["acl_hash"] == recompute(resource, aces)

    def test_the_digest_does_not_depend_on_the_order_the_entries_are_read_back(
        self, payloads: dict[str, Any]
    ) -> None:
        # The server hashes rows it reads in ace_key order; the collector hashes while
        # walking the DACL. They must agree, or every directory would look changed.
        resource = resource_of(payloads, "run-01")
        aces = observations_of(payloads, "run-01", "ntfs_ace")
        shuffled = sorted(aces, key=lambda ace: ace["source_key"])
        assert recompute(resource, shuffled) == resource["acl_hash"]

    def test_reordering_the_dacl_changes_the_digest(self, payloads: dict[str, Any]) -> None:
        # A Deny moved below an Allow grants access that was previously refused; a digest
        # that called those two ACLs equal would hide a real change.
        resource = resource_of(payloads, "run-01")
        aces = observations_of(payloads, "run-01", "ntfs_ace")
        reversed_order = [
            {**ace, "order_index": len(aces) - 1 - ace["order_index"]} for ace in aces
        ]
        assert recompute(resource, reversed_order) != resource["acl_hash"]

    def test_the_empty_and_the_null_dacl_hash_differently(self, payloads: dict[str, Any]) -> None:
        # Nobody has access, versus everybody does. One field apart, and opposite facts.
        empty = resource_of(payloads, "run-02")
        null = resource_of(payloads, "run-03")
        assert empty["dacl_present"] is True
        assert null["dacl_present"] is False
        assert recompute(empty, []) != recompute(null, [])

    def test_a_reported_digest_is_the_published_normal_form(self, payloads: dict[str, Any]) -> None:
        # Pins the version token itself: a format change that forgot to bump it would let
        # two incomparable digests be compared as though they agreed.
        resource = resource_of(payloads, "run-01")
        aces = observations_of(payloads, "run-01", "ntfs_ace")
        normalized = normalize_acl(
            dacl_present=True,
            aces=[
                AclAceFacts(
                    trustee_sid=ace["trustee_sid"],
                    ace_type=AceType(ace["ace_type"]),
                    access_mask=ace["access_mask"],
                    ace_flags=ace["ace_flags"],
                    order_index=ace["order_index"],
                )
                for ace in aces
            ],
        )
        assert normalized.lines[0] == ACL_NORMAL_FORM_VERSION
        assert resource["acl_hash"] == normalized.digest


class TestAProtectedDacl:
    def test_broken_inheritance_is_reported_explicitly(self, payloads: dict[str, Any]) -> None:
        resource = resource_of(payloads, "run-02")
        assert resource["path"] == LOCKED
        assert resource["dacl_protected"] is True
        assert resource["inheritance_enabled"] is False
        assert resource["is_acl_boundary"] is True

    def test_an_empty_dacl_is_an_empty_list_and_not_a_missing_one(
        self, payloads: dict[str, Any]
    ) -> None:
        resource = resource_of(payloads, "run-02")
        assert resource["dacl_present"] is True
        assert resource["ace_count"] == 0
        assert observations_of(payloads, "run-02", "ntfs_ace") == []

    def test_protection_changes_the_digest(self, payloads: dict[str, Any]) -> None:
        resource = resource_of(payloads, "run-02")
        unprotected = {**resource, "dacl_protected": False}
        assert recompute(resource, []) != recompute(unprotected, [])


class TestANullDacl:
    def test_it_is_reported_as_absent_rather_than_empty(self, payloads: dict[str, Any]) -> None:
        resource = resource_of(payloads, "run-03")
        assert resource["path"] == WIDE
        assert resource["dacl_present"] is False
        assert resource["ace_count"] == 0

    def test_it_is_raised_as_a_finding_as_well_as_reported(self, payloads: dict[str, Any]) -> None:
        completion = payloads["run-03-completion.json"]
        assert completion["status"] == "partial"
        assert [error["code"] for error in completion["errors"]] == ["null_dacl"]


class TestAnEntryTheContractCannotExpress:
    def test_what_could_be_read_is_reported_and_the_rest_is_an_error(
        self, payloads: dict[str, Any]
    ) -> None:
        resource = resource_of(payloads, "run-04")
        assert resource["path"] == ODD
        assert resource["ace_count"] == 1
        completion = payloads["run-04-completion.json"]
        assert completion["status"] == "partial"
        assert [error["code"] for error in completion["errors"]] == ["unmappable_ace_type"]

    def test_no_digest_is_reported_for_a_dacl_only_partly_read(
        self, payloads: dict[str, Any]
    ) -> None:
        # A digest over part of a DACL is indistinguishable from a digest of all of it, and
        # comparing one to a parent's would answer the boundary question wrong without ever
        # looking wrong.
        assert "acl_hash" not in resource_of(payloads, "run-04")


class TestAShareRootThatIsNotThere:
    def test_it_fails_rather_than_reporting_a_directory_with_no_permissions(
        self, payloads: dict[str, Any]
    ) -> None:
        completion = payloads["run-05-completion.json"]
        assert completion["status"] == "failed"
        assert completion["observation_count"] == 0
        assert [error["code"] for error in completion["errors"]] == ["path_not_found"]

    def test_it_still_declares_the_scope_it_set_out_to_look_at(
        self, payloads: dict[str, Any]
    ) -> None:
        start = payloads["run-05-start.json"]
        assert len(start["scopes"]) == 1
        assert payloads["run-05-completion.json"]["reconciled_scopes"] == []


class TestBatching:
    def test_a_directory_and_its_entries_travel_together(self, payloads: dict[str, Any]) -> None:
        # The exporter sets batchSize to 3 and Finance produces seven observations, so a
        # batcher that cut at the requested size would split them - and the server's
        # acl_hash check, which needs the whole DACL in one batch, would silently stop
        # running.
        batches = batches_of(payloads, "run-01")
        assert len(batches) == 1
        assert len(batches[0]["observations"]) == 7

    def test_the_last_batch_of_every_run_is_marked_final(self, payloads: dict[str, Any]) -> None:
        for run in ("run-01", "run-02", "run-03", "run-04"):
            batches = sorted(batches_of(payloads, run), key=lambda b: b["sequence"])
            assert batches[-1]["is_final"] is True
            assert all(batch["is_final"] is False for batch in batches[:-1])

    def test_every_batch_has_its_own_id(self, payloads: dict[str, Any]) -> None:
        ids = [batch["batch_id"] for name, batch in payloads.items() if "-batch-" in name]
        assert len(ids) == len(set(ids))
