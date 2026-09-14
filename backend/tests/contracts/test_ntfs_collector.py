r"""The NTFS collector must produce payloads the contract accepts, and hashes ADG agrees with.

A collector that drifts from the contract fails at the worst possible moment: in production,
mid-scan, against a share someone is waiting on. So the collector is run against a fixed fake
estate - no network, no file server, no ACL - and the payloads it writes are validated
exactly as a real POST would be: against the published JSON Schemas, through the backend
models, and through the ingestion planner.

Four of the checks below compare two independent implementations against each other, and
they are the ones worth having:

* **Source keys.** Model validation re-derives every ``source_key`` from
  :mod:`app.contracts.v1.keys`. If PowerShell and Python ever disagreed, the same directory
  would be ingested twice under different identities and change detection would quietly stop
  working.
* **The ACL normal form.** :func:`app.domain.normalize_acl` re-derives every ``acl_hash``
  from the ``ntfs_ace`` observations the collector sent. A divergence here would not fail
  anything loudly; it would show up as a permanent, unexplainable disagreement on every
  directory in the estate, which is worse.
* **The inheritance projection.** :func:`app.domain.inherited_child_acl_hash` and
  :func:`app.domain.boundary_reason_for` re-derive every boundary verdict in the tree run
  from the parent's entries. This is the arithmetic a tree scan rests on, and it is wrong in
  two directions: too eager and every directory is a boundary, burying the few dozen real
  ones; too lax and a real change reads as inherited, telling the next scan it may stop
  looking.
* **The planner.** ``plan_batch`` turns the real payloads into the rows that would be
  stored, including its own ``acl_hash`` verification. A batch the collector produces must
  be one the server accepts.

The fixture covers what a real estate does: an ordinary root with inheritance intact, a
protected DACL, an empty DACL, a NULL DACL, an INHERIT_ONLY entry, a Deny ahead of an Allow,
an orphaned SID, an ACE type the contract cannot express, a root that is not there, and a
three-level tree holding a clean child, a clean grandchild, an edited child, a child that
blocks inheritance, and a file.

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
from app.domain import (
    ACL_NORMAL_FORM_VERSION,
    AceType,
    AclAceFacts,
    boundary_reason_for,
    inherited_child_acl_hash,
    normalize_acl,
    parse_unc_path,
)
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
PROJECTS = "\\\\FS01\\Projects"

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


def resource_at(payloads: dict[str, Any], run: str, path: str) -> dict[str, Any]:
    """One resource of a multi-directory run, by path."""
    matches = [
        item for item in observations_of(payloads, run, "ntfs_resource") if item["path"] == path
    ]
    assert len(matches) == 1, f"{run} should describe {path} exactly once, not {len(matches)} times"
    return matches[0]


def facts_for(aces: list[dict[str, Any]], path: str) -> list[AclAceFacts]:
    """The entries the collector reported for one path, as normalizer facts."""
    return [
        AclAceFacts(
            trustee_sid=ace["trustee_sid"],
            ace_type=AceType(ace["ace_type"]),
            access_mask=ace["access_mask"],
            ace_flags=ace["ace_flags"],
            order_index=ace.get("order_index"),
        )
        for ace in aces
        if ace["path"] == path
    ]


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


class TestScopesAndReconciliation:
    """What a run declares it looked at, and what it will let the backend mark absent.

    New in Phase 3B, and the only thing in ADG that can mark an object absent. Phase 3A
    could never reach it: a share-root read enumerates no tree, so every run it produced was
    incremental by construction. A tree walk can enumerate a tree, so the question becomes
    real — and most of what follows is about the cases where the answer is still no.
    """

    def test_the_scope_names_the_tree_the_run_walked(self, payloads: dict[str, Any]) -> None:
        scopes = payloads["run-01-start.json"]["scopes"]
        assert scopes == [{"kind": "directory_tree", "key": FINANCE.lower()}]

    def test_a_clean_complete_walk_reconciles_its_tree(self, payloads: dict[str, Any]) -> None:
        completion = payloads["run-06-completion.json"]
        assert completion["status"] == "succeeded"
        assert completion["error_count"] == 0
        assert completion["reconciled_scopes"] == [
            {"kind": "directory_tree", "key": PROJECTS.lower()}
        ]

    def test_a_run_that_reconciles_declared_itself_non_incremental(
        self, payloads: dict[str, Any]
    ) -> None:
        # The contract refuses a reconciled scope on an incremental run, and the flag cannot
        # change mid-run — so intent is declared before the walk and achievement reported
        # after it.
        assert payloads["run-06-start.json"]["incremental"] is False

    @pytest.mark.parametrize("run", ["run-03", "run-04", "run-05"])
    def test_a_run_with_any_error_reconciles_nothing(
        self, payloads: dict[str, Any], run: str
    ) -> None:
        completion = payloads[f"{run}-completion.json"]
        assert completion["error_count"] > 0
        assert completion["reconciled_scopes"] == []

    def test_no_run_reconciles_a_scope_it_did_not_declare(self, payloads: dict[str, Any]) -> None:
        for name, completion in payloads.items():
            if not name.endswith("-completion.json"):
                continue
            declared = payloads[name.replace("-completion", "-start")]["scopes"]
            keys = {(scope["kind"], scope["key"]) for scope in declared}
            for scope in completion["reconciled_scopes"]:
                assert (scope["kind"], scope["key"]) in keys

    def test_the_run_declares_itself_an_ntfs_collector(self, payloads: dict[str, Any]) -> None:
        source = payloads["run-01-start.json"]["source"]
        assert source["collector"] == "ntfs"
        assert source["method"] == "DirectorySecurity.GetSecurityDescriptorBinaryForm"


class TestTheBoundaryVerdict:
    """The collector's boundary reasons, re-derived here from the same facts.

    The third cross-language check, and the one this phase adds. The naive test — compare a
    child's ``acl_hash`` to its parent's — marks *every* directory a boundary, because
    inheritance sets the INHERITED bit on every entry it copies. The comparison has to be
    against what the parent **projects** onto a child, and the two implementations of that
    projection have to agree exactly, or the two sides will disagree forever about every
    directory in the estate.
    """

    def test_the_projection_agrees_with_the_collector_on_every_directory(
        self, payloads: dict[str, Any]
    ) -> None:
        resources = {
            item["path"]: item for item in observations_of(payloads, "run-06", "ntfs_resource")
        }
        aces = observations_of(payloads, "run-06", "ntfs_ace")
        assert resources, "run-06 should describe a tree"

        compared = 0
        for path, resource in resources.items():
            parent_path = parse_unc_path(path).parent
            if parent_path is None or parent_path.value not in resources:
                continue
            parent = resources[parent_path.value]

            projected = inherited_child_acl_hash(
                dacl_present=parent["dacl_present"],
                aces=facts_for(aces, parent["path"]),
                # A file inherits through the object projection; comparing it against the
                # container one would report a boundary on every file in the estate.
                for_container=resource.get("resource_kind", "directory") == "directory",
            )
            expected = boundary_reason_for(
                is_share_root=parse_unc_path(path).is_share_root,
                is_scan_root=False,
                dacl_present=resource["dacl_present"],
                dacl_protected=resource.get("dacl_protected", False),
                acl_hash=resource.get("acl_hash"),
                parent_dacl_present=parent["dacl_present"],
                parent_projection=projected,
            )
            reported = resource.get("boundary_reason")
            assert reported == (None if expected is None else expected.value), (
                f"{path}: the collector said {reported!r}, the backend derives "
                f"{None if expected is None else expected.value!r}"
            )
            compared += 1

        assert compared >= 4, "the tree fixture should exercise several comparisons"

    def test_a_directory_that_inherited_cleanly_is_not_a_boundary(
        self, payloads: dict[str, Any]
    ) -> None:
        apollo = resource_at(payloads, "run-06", f"{PROJECTS}\\Apollo")
        assert apollo["is_acl_boundary"] is False
        assert "boundary_reason" not in apollo

    def test_a_grandchild_that_inherited_cleanly_is_not_a_boundary_either(
        self, payloads: dict[str, Any]
    ) -> None:
        # The property a hand-written comparison gets wrong. A clean child and a clean
        # grandchild carry the identical DACL, so the projection has to be stable below the
        # first level — otherwise every directory past depth 1 is a false boundary.
        designs = resource_at(payloads, "run-06", f"{PROJECTS}\\Apollo\\Designs")
        assert designs["is_acl_boundary"] is False
        assert designs["depth_from_share_root"] == 2

    def test_a_different_owner_does_not_make_a_boundary(self, payloads: dict[str, Any]) -> None:
        # Every folder under a root is owned by whoever created it while sharing one
        # inherited DACL, which is exactly why the digest excludes the owner (ADR-0008).
        root = resource_at(payloads, "run-06", PROJECTS)
        designs = resource_at(payloads, "run-06", f"{PROJECTS}\\Apollo\\Designs")
        assert designs["owner_sid"] != root["owner_sid"]
        assert designs["is_acl_boundary"] is False

    def test_an_added_explicit_entry_is_a_boundary(self, payloads: dict[str, Any]) -> None:
        gemini = resource_at(payloads, "run-06", f"{PROJECTS}\\Gemini")
        assert gemini["is_acl_boundary"] is True
        assert gemini["boundary_reason"] == "acl_differs_from_parent"

    def test_a_protected_dacl_is_a_boundary_whatever_the_projection_says(
        self, payloads: dict[str, Any]
    ) -> None:
        sealed = resource_at(payloads, "run-06", f"{PROJECTS}\\Sealed")
        assert sealed["boundary_reason"] == "protected_dacl"
        assert sealed["inheritance_enabled"] is False

    def test_the_scan_root_is_a_boundary_it_could_not_establish(
        self, payloads: dict[str, Any]
    ) -> None:
        root = resource_at(payloads, "run-06", PROJECTS)
        assert root["is_acl_boundary"] is True
        # share_root rather than scan_root: this path IS the directory the share publishes,
        # and its parent lies outside the share rather than merely outside this run.
        assert root["boundary_reason"] == "share_root"
        assert "parent_acl_hash" not in root

    def test_every_child_records_which_reading_of_the_parent_it_judged(
        self, payloads: dict[str, Any]
    ) -> None:
        resources = {
            item["path"]: item for item in observations_of(payloads, "run-06", "ntfs_resource")
        }
        for path, resource in resources.items():
            parent_path = parse_unc_path(path).parent
            if parent_path is None or parent_path.value not in resources:
                continue
            assert resource["parent_acl_hash"] == resources[parent_path.value]["acl_hash"]

    def test_a_file_is_compared_against_the_object_projection(
        self, payloads: dict[str, Any]
    ) -> None:
        readme = resource_at(payloads, "run-06", f"{PROJECTS}\\Apollo\\readme.txt")
        assert readme["resource_kind"] == "file"
        assert readme["is_acl_boundary"] is False

        # And the two projections really are different documents, so the assertion above is
        # not passing by coincidence.
        aces = observations_of(payloads, "run-06", "ntfs_ace")
        parent_facts = facts_for(aces, f"{PROJECTS}\\Apollo")
        assert inherited_child_acl_hash(
            dacl_present=True, aces=parent_facts, for_container=False
        ) != inherited_child_acl_hash(dacl_present=True, aces=parent_facts, for_container=True)

    def test_every_boundary_carries_a_reason_and_no_other_resource_does(
        self, payloads: dict[str, Any]
    ) -> None:
        for resource in observations_of(payloads, "run-06", "ntfs_resource"):
            if resource["is_acl_boundary"]:
                assert resource.get("boundary_reason"), f"{resource['path']} claims no reason"
            else:
                assert "boundary_reason" not in resource


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
