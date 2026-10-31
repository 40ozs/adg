"""``ScopeView.display_name`` is an additive, optional field on the governance scope.

A client written before it existed must keep working: the scope is still ``kind`` + ``key``,
both still required, and the new name is optional. The scan-run scope, which shared the
component name until the shapes diverged, publishes the same two fields as before.
"""

from __future__ import annotations

import json

from app.api.governance import ScopeView
from app.contracts.openapi import SNAPSHOT_PATH

SCHEMAS = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["components"]["schemas"]


def test_the_governance_scope_adds_only_an_optional_name() -> None:
    scope = SCHEMAS["ScopeView"]

    assert scope["required"] == ["kind", "key"]
    assert set(scope["properties"]) == {"kind", "key", "display_name"}
    assert {"type": "null"} in scope["properties"]["display_name"]["anyOf"]


def test_the_scan_run_scope_keeps_its_old_shape() -> None:
    scope = SCHEMAS["ScanScopeView"]

    assert scope["required"] == ["kind", "key"]
    assert set(scope["properties"]) == {"kind", "key"}


def test_a_payload_without_the_name_still_parses() -> None:
    """What an older server sent, read by this model."""
    scope = ScopeView.model_validate({"kind": "principal", "key": "S-1-5-21-1-2-3-1101"})

    assert scope.display_name is None
    assert scope.model_dump() == {
        "kind": "principal",
        "key": "S-1-5-21-1-2-3-1101",
        "display_name": None,
    }
