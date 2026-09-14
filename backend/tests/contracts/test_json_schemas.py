"""The published JSON Schemas are the contract a collector author codes against.

These tests check the schemas themselves: that they are valid, that their embedded examples
validate, and — most importantly — that they *reject* the payloads that would corrupt
ADG's view of an estate if accepted.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = pathlib.Path(__file__).resolve().parents[3] / "docs" / "contracts" / "v1"

SCHEMA_FILES = sorted(SCHEMA_DIR.glob("*.schema.json"))

RUN_ID = "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31"
VALID_SID = "S-1-5-21-1004336348-1177238915-682003330-1104"


def load(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return document


def build_registry() -> Registry[Any]:
    """Register every schema under both its $id and its file name, so relative $refs work."""
    resources: list[tuple[str, Resource[Any]]] = []
    for path in SCHEMA_FILES:
        contents = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(contents, default_specification=DRAFT202012)
        resources.append((contents["$id"], resource))
        resources.append((path.name, resource))
    return Registry().with_resources(resources)


REGISTRY = build_registry()


def validator_for(name: str) -> Draft202012Validator:
    return Draft202012Validator(load(name), registry=REGISTRY)


def observation(kind: str, source_key: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": kind,
        "run_id": RUN_ID,
        "observed_at": "2026-09-14T08:00:00Z",
        "source_key": source_key,
        **fields,
    }


class TestSchemasThemselves:
    def test_every_contract_kind_has_a_schema(self) -> None:
        names = {path.name for path in SCHEMA_FILES}

        assert names == {
            "common.schema.json",
            "principal-observation.schema.json",
            "membership-observation.schema.json",
            "server-observation.schema.json",
            "smb-share-observation.schema.json",
            "smb-ace-observation.schema.json",
            "ntfs-resource-observation.schema.json",
            "ntfs-ace-observation.schema.json",
            "scan-run-start.schema.json",
            "observation-batch.schema.json",
            "scan-run-completion.schema.json",
        }

    @pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda path: path.name)
    def test_schema_is_valid_json_schema(self, path: pathlib.Path) -> None:
        schema = json.loads(path.read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].startswith("https://adg.local/contracts/v1/")

    @pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda path: path.name)
    def test_embedded_examples_validate(self, path: pathlib.Path) -> None:
        schema = json.loads(path.read_text(encoding="utf-8"))
        examples = schema.get("examples", [])
        if not examples:
            pytest.skip(f"{path.name} carries no examples")

        validator = Draft202012Validator(schema, registry=REGISTRY)
        for example in examples:
            validator.validate(example)


class TestSidValidation:
    @pytest.mark.parametrize(
        "sid",
        [
            "CORP\\alice",
            "alice@corp.example.com",
            "S-2-5-21-1-2-3",
            "1-5-21-1-2-3",
            "S-1-5-21-x-2-3",
            "S-1-5-21-1-2-3-",
            "",
        ],
    )
    def test_malformed_sids_are_rejected(self, sid: str) -> None:
        validator = validator_for("principal-observation.schema.json")
        payload = observation("principal", f"principal|{sid}", sid=sid, principal_kind="user")

        assert not validator.is_valid(payload)

    def test_a_well_formed_sid_is_accepted(self) -> None:
        validator = validator_for("principal-observation.schema.json")
        payload = observation(
            "principal", f"principal|{VALID_SID}", sid=VALID_SID, principal_kind="user"
        )

        validator.validate(payload)

    def test_a_trustee_sid_is_validated_on_an_ace(self) -> None:
        validator = validator_for("ntfs-ace-observation.schema.json")
        payload = observation(
            "ntfs_ace",
            "ntfs_ace|x",
            path="\\\\FS01\\Finance",
            trustee_sid="CORP\\alice",
            ace_type="allow",
            access_mask=1179817,
            ace_flags=0,
            source="explicit",
        )

        assert not validator.is_valid(payload)


class TestEnumValidation:
    def test_an_unknown_principal_kind_is_rejected(self) -> None:
        validator = validator_for("principal-observation.schema.json")
        payload = observation("principal", "principal|x", sid=VALID_SID, principal_kind="superuser")

        assert not validator.is_valid(payload)

    def test_an_unknown_ace_type_is_rejected(self) -> None:
        validator = validator_for("ntfs-ace-observation.schema.json")
        payload = observation(
            "ntfs_ace",
            "ntfs_ace|x",
            path="\\\\FS01\\Finance",
            trustee_sid=VALID_SID,
            ace_type="audit",
            access_mask=0,
            ace_flags=0,
            source="explicit",
        )

        assert not validator.is_valid(payload)

    def test_an_unknown_share_permission_is_rejected(self) -> None:
        validator = validator_for("smb-ace-observation.schema.json")
        payload = observation(
            "smb_ace",
            "smb_ace|x",
            server_name="FS01",
            share_name="Finance",
            trustee_sid=VALID_SID,
            ace_type="allow",
            permission="modify",
        )

        assert not validator.is_valid(payload)

    def test_an_unknown_scope_kind_is_rejected(self) -> None:
        validator = validator_for("scan-run-start.schema.json")
        payload = {
            "schema_version": "1.0",
            "run_id": RUN_ID,
            "source": {"collector": "ntfs", "collector_host": "C1", "method": "m"},
            "started_at": "2026-09-14T08:00:00Z",
            "scopes": [{"kind": "mailbox", "key": "x"}],
        }

        assert not validator.is_valid(payload)


class TestRequiredIdentifiers:
    def test_an_observation_without_a_run_id_is_rejected(self) -> None:
        validator = validator_for("server-observation.schema.json")
        payload = observation("server", "server|fs01", name="FS01")
        del payload["run_id"]

        assert not validator.is_valid(payload)

    def test_a_non_uuid_run_id_is_rejected(self) -> None:
        validator = validator_for("server-observation.schema.json")
        payload = observation("server", "server|fs01", name="FS01")
        payload["run_id"] = "run-7"

        assert not validator.is_valid(payload)

    def test_an_observation_without_a_source_key_is_rejected(self) -> None:
        validator = validator_for("server-observation.schema.json")
        payload = observation("server", "server|fs01", name="FS01")
        del payload["source_key"]

        assert not validator.is_valid(payload)

    def test_a_batch_without_a_run_id_is_rejected(self) -> None:
        validator = validator_for("observation-batch.schema.json")
        payload = {
            "schema_version": "1.0",
            "batch_id": RUN_ID,
            "sequence": 1,
            "observations": [observation("server", "server|fs01", name="FS01")],
        }

        assert not validator.is_valid(payload)

    def test_a_naive_timestamp_is_rejected(self) -> None:
        validator = validator_for("server-observation.schema.json")
        payload = observation("server", "server|fs01", name="FS01")
        payload["observed_at"] = "2026-09-14T08:00:00"

        assert not validator.is_valid(payload)


class TestAmbiguousResourceIdentity:
    def test_a_resource_without_a_path_is_rejected(self) -> None:
        validator = validator_for("ntfs-resource-observation.schema.json")
        payload = observation(
            "ntfs_resource",
            "resource|x",
            local_path="D:\\Shares\\Finance",
            server_name="FS01",
            dacl_present=True,
            ace_count=1,
        )

        assert not validator.is_valid(payload)

    @pytest.mark.parametrize(
        "path",
        [
            "D:\\Shares\\Finance",
            "Finance\\Reports",
            "\\\\FS01",
            "\\\\FS01\\",
            "\\\\?\\UNC\\FS01\\Finance",
            "\\\\FS01\\Finance\\..\\HR",
            "\\\\FS01\\Fin|ance",
        ],
    )
    def test_an_ambiguous_or_unnormalized_path_is_rejected(self, path: str) -> None:
        validator = validator_for("ntfs-resource-observation.schema.json")
        payload = observation(
            "ntfs_resource", "resource|x", path=path, dacl_present=True, ace_count=1
        )

        assert not validator.is_valid(payload)

    def test_a_canonical_unc_path_is_accepted(self) -> None:
        validator = validator_for("ntfs-resource-observation.schema.json")
        payload = observation(
            "ntfs_resource",
            "resource|\\\\fs01\\finance",
            path="\\\\FS01\\Finance",
            dacl_present=True,
            ace_count=1,
        )

        validator.validate(payload)

    def test_a_share_without_a_server_is_rejected(self) -> None:
        validator = validator_for("smb-share-observation.schema.json")
        payload = observation("smb_share", "share|x", share_name="Finance")

        assert not validator.is_valid(payload)

    def test_a_server_name_containing_a_path_is_rejected(self) -> None:
        validator = validator_for("server-observation.schema.json")
        payload = observation("server", "server|fs01", name="\\\\FS01")

        assert not validator.is_valid(payload)


class TestRightsMasks:
    @pytest.mark.parametrize("mask", [-1, 4294967296, 2**40])
    def test_a_mask_outside_32_bits_is_rejected(self, mask: int) -> None:
        validator = validator_for("ntfs-ace-observation.schema.json")
        payload = observation(
            "ntfs_ace",
            "ntfs_ace|x",
            path="\\\\FS01\\Finance",
            trustee_sid=VALID_SID,
            ace_type="allow",
            access_mask=mask,
            ace_flags=0,
            source="explicit",
        )

        assert not validator.is_valid(payload)

    def test_a_non_integer_mask_is_rejected(self) -> None:
        validator = validator_for("ntfs-ace-observation.schema.json")
        payload = observation(
            "ntfs_ace",
            "ntfs_ace|x",
            path="\\\\FS01\\Finance",
            trustee_sid=VALID_SID,
            ace_type="allow",
            access_mask="0x1301BF",
            ace_flags=0,
            source="explicit",
        )

        assert not validator.is_valid(payload)

    @pytest.mark.parametrize("flags", [-1, 256])
    def test_ace_flags_outside_one_byte_are_rejected(self, flags: int) -> None:
        validator = validator_for("ntfs-ace-observation.schema.json")
        payload = observation(
            "ntfs_ace",
            "ntfs_ace|x",
            path="\\\\FS01\\Finance",
            trustee_sid=VALID_SID,
            ace_type="allow",
            access_mask=0,
            ace_flags=flags,
            source="explicit",
        )

        assert not validator.is_valid(payload)

    def test_generic_rights_are_accepted_unexpanded(self) -> None:
        validator = validator_for("ntfs-ace-observation.schema.json")
        payload = observation(
            "ntfs_ace",
            "ntfs_ace|x",
            path="\\\\FS01\\Finance",
            trustee_sid=VALID_SID,
            ace_type="allow",
            access_mask=0x10000000,
            ace_flags=0,
            source="explicit",
        )

        validator.validate(payload)


class TestCrossFieldRules:
    def test_a_share_ace_with_neither_right_form_is_rejected(self) -> None:
        validator = validator_for("smb-ace-observation.schema.json")
        payload = observation(
            "smb_ace",
            "smb_ace|x",
            server_name="FS01",
            share_name="Finance",
            trustee_sid=VALID_SID,
            ace_type="allow",
        )

        assert not validator.is_valid(payload)

    def test_a_share_ace_with_both_right_forms_is_rejected(self) -> None:
        validator = validator_for("smb-ace-observation.schema.json")
        payload = observation(
            "smb_ace",
            "smb_ace|x",
            server_name="FS01",
            share_name="Finance",
            trustee_sid=VALID_SID,
            ace_type="allow",
            permission="full",
            access_mask=2032127,
        )

        assert not validator.is_valid(payload)

    def test_a_local_group_without_a_host_is_rejected(self) -> None:
        validator = validator_for("principal-observation.schema.json")
        payload = observation(
            "principal", "principal|x", sid="S-1-5-32-544", principal_kind="local_group"
        )

        assert not validator.is_valid(payload)

    def test_an_unresolved_principal_may_not_carry_a_display_name(self) -> None:
        validator = validator_for("principal-observation.schema.json")
        payload = observation(
            "principal",
            "principal|x",
            sid=VALID_SID,
            principal_kind="unresolved",
            display_name="Alice Smith",
        )

        assert not validator.is_valid(payload)

    def test_a_local_membership_edge_requires_a_host(self) -> None:
        validator = validator_for("membership-observation.schema.json")
        payload = observation(
            "membership_edge",
            "edge|x",
            group_sid="S-1-5-32-544",
            member_sid=VALID_SID,
            edge_kind="local_group_member",
        )

        assert not validator.is_valid(payload)

    def test_a_directory_edge_may_not_carry_a_host(self) -> None:
        validator = validator_for("membership-observation.schema.json")
        payload = observation(
            "membership_edge",
            "edge|x",
            group_sid="S-1-5-21-1-2-3-1201",
            member_sid=VALID_SID,
            edge_kind="directory_group_member",
            host_key="FS01",
        )

        assert not validator.is_valid(payload)

    def test_a_null_dacl_may_not_carry_aces(self) -> None:
        validator = validator_for("ntfs-resource-observation.schema.json")
        payload = observation(
            "ntfs_resource",
            "resource|x",
            path="\\\\FS01\\Finance",
            dacl_present=False,
            ace_count=3,
        )

        assert not validator.is_valid(payload)

    def test_a_null_dacl_with_no_aces_is_accepted(self) -> None:
        validator = validator_for("ntfs-resource-observation.schema.json")
        payload = observation(
            "ntfs_resource",
            "resource|\\\\fs01\\finance",
            path="\\\\FS01\\Finance",
            dacl_present=False,
            ace_count=0,
        )

        validator.validate(payload)

    def test_an_explicit_ace_may_not_record_an_inheritance_origin(self) -> None:
        validator = validator_for("ntfs-ace-observation.schema.json")
        payload = observation(
            "ntfs_ace",
            "ntfs_ace|x",
            path="\\\\FS01\\Finance\\Reports",
            trustee_sid=VALID_SID,
            ace_type="allow",
            access_mask=1179817,
            ace_flags=0,
            source="explicit",
            inherited_from="\\\\FS01\\Finance",
        )

        assert not validator.is_valid(payload)

    def test_unknown_fields_are_rejected(self) -> None:
        validator = validator_for("server-observation.schema.json")
        payload = observation("server", "server|fs01", name="FS01", effective_access="full")

        assert not validator.is_valid(payload)


class TestScanRunEnvelopes:
    def test_a_start_without_scopes_is_rejected(self) -> None:
        validator = validator_for("scan-run-start.schema.json")
        payload = {
            "schema_version": "1.0",
            "run_id": RUN_ID,
            "source": {"collector": "ntfs", "collector_host": "C1", "method": "m"},
            "started_at": "2026-09-14T08:00:00Z",
            "scopes": [],
        }

        assert not validator.is_valid(payload)

    @pytest.mark.parametrize("status", ["partial", "failed", "canceled"])
    def test_a_non_succeeded_run_may_not_reconcile(self, status: str) -> None:
        validator = validator_for("scan-run-completion.schema.json")
        payload = {
            "schema_version": "1.0",
            "run_id": RUN_ID,
            "status": status,
            "completed_at": "2026-09-14T08:05:00Z",
            "batch_count": 1,
            "observation_count": 10,
            "error_count": 0,
            "reconciled_scopes": [{"kind": "share", "key": "fs01|finance"}],
        }

        assert not validator.is_valid(payload)

    def test_a_successful_run_with_errors_may_not_reconcile(self) -> None:
        validator = validator_for("scan-run-completion.schema.json")
        payload = {
            "schema_version": "1.0",
            "run_id": RUN_ID,
            "status": "succeeded",
            "completed_at": "2026-09-14T08:05:00Z",
            "batch_count": 1,
            "observation_count": 10,
            "error_count": 2,
            "reconciled_scopes": [{"kind": "share", "key": "fs01|finance"}],
        }

        assert not validator.is_valid(payload)

    def test_a_clean_successful_run_may_reconcile(self) -> None:
        validator = validator_for("scan-run-completion.schema.json")
        payload = {
            "schema_version": "1.0",
            "run_id": RUN_ID,
            "status": "succeeded",
            "completed_at": "2026-09-14T08:05:00Z",
            "batch_count": 1,
            "observation_count": 10,
            "error_count": 0,
            "reconciled_scopes": [{"kind": "share", "key": "fs01|finance"}],
        }

        validator.validate(payload)

    def test_an_oversized_batch_is_rejected(self) -> None:
        validator = validator_for("observation-batch.schema.json")
        one = observation("server", "server|fs01", name="FS01")
        payload = {
            "schema_version": "1.0",
            "run_id": RUN_ID,
            "batch_id": RUN_ID,
            "sequence": 1,
            "observations": [one] * 1001,
        }

        assert not validator.is_valid(payload)

    def test_an_empty_batch_is_rejected(self) -> None:
        validator = validator_for("observation-batch.schema.json")
        payload = {
            "schema_version": "1.0",
            "run_id": RUN_ID,
            "batch_id": RUN_ID,
            "sequence": 1,
            "observations": [],
        }

        assert not validator.is_valid(payload)

    def test_a_future_major_version_is_rejected(self) -> None:
        validator = validator_for("server-observation.schema.json")
        payload = observation("server", "server|fs01", name="FS01")
        payload["schema_version"] = "2.0"

        assert not validator.is_valid(payload)

    def test_an_additive_minor_version_is_accepted(self) -> None:
        validator = validator_for("server-observation.schema.json")
        payload = observation("server", "server|fs01", name="FS01")
        payload["schema_version"] = "1.7"

        validator.validate(payload)
