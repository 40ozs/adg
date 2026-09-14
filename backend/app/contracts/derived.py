"""Publishing the derived-answer responses as versioned JSON Schemas.

``docs/contracts/v1/*.schema.json`` is entirely about what a **collector sends**. This
module adds the other direction, one directory below in ``v1/derived/``: what the API
**returns** when it answers a question it derived rather than observed — an access
explanation, a page of causal paths, a group's resource impact.

Published as standalone draft 2020-12 documents rather than left inside ``openapi.json``
because they are consumed differently. The OpenAPI document describes an API: every route,
every parameter, every status code, and it is read by a code generator. These are the shape
of three payloads, addressable by ``$id``, and they are what a client validates an actual
response against — in a test, in a pipeline, or in a language whose OpenAPI tooling is worse
than its JSON Schema tooling.

**Generated, not maintained.** A hand-written copy of a response shape is a second
description that drifts from the first, and the drift is invisible until a client believes
the wrong one. So these come from the same pydantic models FastAPI serializes, exactly as
``app.contracts.openapi`` does for the API document, and
``tests/contracts/test_derived_schemas.py`` fails when the checked-in files fall behind.

Run ``python -m app.contracts.derived`` to rewrite them.

Versioning
----------

Every body carries ``schema_version``, and its value is
:data:`app.api.caching.CONTRACT_VERSION`. The rules are the ones
``docs/contracts/README.md`` already sets for the collector payloads, applied here:

* an **additive** change — a new optional field, a new enum member in a field a client is
  told to treat as open — bumps the minor version;
* a **breaking** change — a removed field, a renamed one, a narrowed type — requires a new
  major, a ``v2/`` directory, and a migration note;
* a published schema file is never edited in place to mean something different.

The version is also part of every cache validator, so a client holding a 1.0 body cannot be
handed a 304 by a server that now speaks 1.1.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from pydantic import BaseModel

from app.api.access import AccessExplanationResponse, AccessPathPageResponse
from app.api.caching import CONTRACT_VERSION
from app.api.groups import ResourceImpactResponse

__all__ = ["EXAMPLE_DIR", "SCHEMAS", "SCHEMA_DIR", "generate", "generate_all", "write"]

SCHEMA_DIR = pathlib.Path(__file__).resolve().parents[3] / "docs" / "contracts" / "v1" / "derived"
"""Its own directory, one level below the collector payloads.

Physically separate because the two directions are separate contracts with different rules —
and practically, because ``v1/*.schema.json`` is a glob the collector schema tests use to
mean "every payload a collector sends". A response schema sitting in it would be registered
as a collector payload and checked against the observation kinds, which it is not one of.
"""

EXAMPLE_DIR = SCHEMA_DIR / "examples"
"""Captured responses, one per schema. Real bodies from the smoke suite, not inventions;
``tests/contracts/test_derived_schemas.py`` validates each against its schema."""

_BASE_ID = "https://adg.local/contracts/v1/derived"

SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "access-explanation.schema.json": (
        AccessExplanationResponse,
        "The complete derivation of one principal's access to one directory: the verdict, "
        "both ACL layers, every causal path, and the measured effect of removing each edge. "
        "Returned by GET /api/v1/access/explain.",
    ),
    "access-paths.schema.json": (
        AccessPathPageResponse,
        "One page of the causal paths behind one answer, in the engine's deterministic "
        "order. Returned by GET /api/v1/access/paths.",
    ),
    "resource-impact.schema.json": (
        ResourceImpactResponse,
        "Every resource one group's membership reaches, what it confers on each, and how "
        "many principals a change would affect. Returned by "
        "GET /api/v1/groups/{identifier}/resource-impact.",
    ),
}
"""File name to (model, description). Adding a derived response means adding a line here;
the test that checks the directory against this mapping fails for one that is published and
not listed, or listed and not published."""


def generate(name: str) -> dict[str, Any]:
    """The published schema for one derived response.

    ``$id`` and ``$schema`` are added because a standalone schema needs both to be
    referenceable and to declare its dialect; pydantic emits neither, since inside an
    OpenAPI document neither would be correct.
    """
    model, description = SCHEMAS[name]
    document: dict[str, Any] = model.model_json_schema(
        ref_template="#/$defs/{model}", mode="serialization"
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_BASE_ID}/{name}",
        "x-contract-version": CONTRACT_VERSION,
        **document,
        "description": description,
    }


def generate_all() -> dict[str, dict[str, Any]]:
    return {name: generate(name) for name in SCHEMAS}


def write(directory: pathlib.Path = SCHEMA_DIR) -> list[pathlib.Path]:
    """Write every schema, formatted stably so that a diff shows only real changes."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[pathlib.Path] = []
    for name, document in generate_all().items():
        path = directory / name
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover - developer entry point
    for path in write():
        print(f"Wrote {path}")
