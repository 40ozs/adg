"""The published schemas and the backend models must describe the same contract.

A collector author codes against `docs/contracts/v1/*.schema.json`. The API validates with
pydantic models. If those two drift, a collector can be correct by the documentation and
still be rejected — or, worse, send something the documentation forbids and have it
accepted. This test compares the two descriptions field by field.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.contracts.v1.envelopes import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.contracts.v1.observations import (
    MembershipObservation,
    NtfsAceObservation,
    NtfsResourceObservation,
    PrincipalObservation,
    ServerObservation,
    SmbAceObservation,
    SmbShareObservation,
)
from tests.contracts.test_json_schemas import SCHEMA_DIR

PAIRS: list[tuple[str, type]] = [
    ("principal-observation.schema.json", PrincipalObservation),
    ("membership-observation.schema.json", MembershipObservation),
    ("server-observation.schema.json", ServerObservation),
    ("smb-share-observation.schema.json", SmbShareObservation),
    ("smb-ace-observation.schema.json", SmbAceObservation),
    ("ntfs-resource-observation.schema.json", NtfsResourceObservation),
    ("ntfs-ace-observation.schema.json", NtfsAceObservation),
    ("scan-run-start.schema.json", ScanRunStart),
    ("observation-batch.schema.json", ObservationBatch),
    ("scan-run-completion.schema.json", ScanRunCompletion),
]

# The published schema requires these on the wire; the models default them so that server-
# side construction stays ergonomic. A payload missing them is still rejected in practice:
# `kind` is the discriminator a batch needs to pick a variant, and `schema_version` is
# pinned to 1.x by a validator.
MODEL_DEFAULTED_BUT_REQUIRED_ON_THE_WIRE = {"schema_version", "kind"}

COMMON = json.loads((SCHEMA_DIR / "common.schema.json").read_text(encoding="utf-8"))


def published(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return document


def resolve_published(node: dict[str, Any]) -> dict[str, Any]:
    """Follow a ``common.schema.json#/$defs/...`` reference."""
    ref = node.get("$ref")
    if not ref:
        return node
    if ref.startswith("common.schema.json#/$defs/"):
        resolved: dict[str, Any] = COMMON["$defs"][ref.rsplit("/", 1)[-1]]
        return resolved
    return node


def resolve_model(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Follow a pydantic ``#/$defs/...`` reference, including inside anyOf."""
    ref = node.get("$ref")
    if ref and ref.startswith("#/$defs/"):
        referenced: dict[str, Any] = defs.get(ref.rsplit("/", 1)[-1], {})
        return referenced
    for option in node.get("anyOf", []):
        resolved = resolve_model(option, defs)
        if "enum" in resolved or "const" in resolved:
            return resolved
    return node


def published_properties(name: str) -> dict[str, dict[str, Any]]:
    schema = published(name)
    properties = dict(schema.get("properties", {}))
    for clause in schema.get("allOf", []):
        ref = clause.get("$ref", "")
        if ref.endswith("#/$defs/observationBase"):
            properties.update(COMMON["$defs"]["observationBase"]["properties"])
    return properties


def published_required(name: str) -> set[str]:
    schema = published(name)
    required = set(schema.get("required", []))
    for clause in schema.get("allOf", []):
        ref = clause.get("$ref", "")
        if ref.endswith("#/$defs/observationBase"):
            required |= set(COMMON["$defs"]["observationBase"]["required"])
    return required


def model_schema(model: type) -> dict[str, Any]:
    document: dict[str, Any] = model.model_json_schema()  # type: ignore[attr-defined]
    return document


@pytest.mark.parametrize(("name", "model"), PAIRS, ids=[pair[0] for pair in PAIRS])
class TestParity:
    def test_the_same_fields_exist_on_both_sides(self, name: str, model: type) -> None:
        schema_fields = set(published_properties(name))
        model_fields = set(model_schema(model).get("properties", {}))

        assert schema_fields == model_fields, (
            f"{name}: only in the schema {sorted(schema_fields - model_fields)}; "
            f"only in the model {sorted(model_fields - schema_fields)}"
        )

    def test_the_model_never_requires_more_than_the_contract(self, name: str, model: type) -> None:
        model_required = set(model_schema(model).get("required", []))

        assert model_required <= published_required(name), (
            f"{name}: the model requires {sorted(model_required - published_required(name))}, "
            "which the published contract says is optional. A collector following the "
            "documentation would be rejected."
        )

    def test_everything_the_contract_requires_is_required_or_defaulted(
        self, name: str, model: type
    ) -> None:
        model_required = set(model_schema(model).get("required", []))
        missing = published_required(name) - model_required

        assert missing <= MODEL_DEFAULTED_BUT_REQUIRED_ON_THE_WIRE, (
            f"{name}: the contract requires {sorted(missing)} but the model does not. "
            "The API would accept a payload the documentation forbids."
        )

    def test_enumerations_agree(self, name: str, model: type) -> None:
        schema_properties = published_properties(name)
        model_document = model_schema(model)
        model_defs = model_document.get("$defs", {})

        for field, published_node in schema_properties.items():
            resolved_published = resolve_published(published_node)
            if "enum" not in resolved_published:
                continue
            model_node = resolve_model(model_document["properties"][field], model_defs)
            model_values = model_node.get("enum")
            if model_values is None and "const" in model_node:
                model_values = [model_node["const"]]

            assert model_values is not None, f"{name}.{field}: the model declares no enum"
            assert set(model_values) == set(resolved_published["enum"]), (
                f"{name}.{field}: schema {sorted(resolved_published['enum'])} vs model "
                f"{sorted(model_values)}"
            )


class TestKeyDerivationIsDocumented:
    """The protocol document restates the source-key formulas; they must stay true."""

    def test_every_documented_derivation_matches_the_implementation(self) -> None:
        from app.contracts.v1 import keys
        from app.domain import MembershipEdgeKind, PrincipalKind, Sid

        sid = Sid("S-1-5-21-1-2-3-1104")
        group = Sid("S-1-5-21-1-2-3-1201")
        builtin = Sid("S-1-5-32-544")

        assert keys.principal_key(sid, PrincipalKind.USER) == f"principal|{sid}"
        assert (
            keys.principal_key(builtin, PrincipalKind.LOCAL_GROUP, "FS01")
            == f"principal|fs01|{builtin}"
        )
        assert (
            keys.membership_key(group, sid, MembershipEdgeKind.DIRECTORY_GROUP_MEMBER)
            == f"edge|{group}->{sid}|directory_group_member"
        )
        assert keys.server_key("FS01") == "server|fs01"
        assert keys.share_key("FS01", "Finance") == "share|fs01|finance"
        assert (
            keys.smb_ace_key("FS01", "Finance", sid, "allow", None, "full")
            == f"smb_ace|fs01|finance|{sid}|allow|full"
        )
        assert (
            keys.smb_ace_key("FS01", "Finance", sid, "allow", 0x001200A9, None)
            == f"smb_ace|fs01|finance|{sid}|allow|0x001200a9"
        )
        assert keys.ntfs_resource_key("\\\\FS01\\Finance") == "resource|\\\\fs01\\finance"
        assert (
            keys.ntfs_ace_key("\\\\FS01\\Finance", sid, "allow", 0x001301BF, 0x03)
            == f"ntfs_ace|\\\\fs01\\finance|{sid}|allow|0x001301bf|0x03"
        )

    def test_the_protocol_document_lists_every_observation_kind(self) -> None:
        from app.contracts.v1.observations import OBSERVATION_MODELS

        protocol = (SCHEMA_DIR.parent / "collector-protocol.md").read_text(encoding="utf-8")

        for kind in OBSERVATION_MODELS:
            assert f"`{kind}`" in protocol, f"collector-protocol.md does not mention {kind}"
