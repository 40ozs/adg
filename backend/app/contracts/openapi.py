"""Publishing the API's OpenAPI document as a checked-in contract.

The frontend hand-writes its response types and checks them against this snapshot. That
only works if the snapshot is current, so it is generated from the application rather than
maintained, and ``tests/contracts/test_openapi_snapshot.py`` fails when the two diverge.

The document is generated for a **development-mode** application on purpose: it is the
superset, containing the development login route that a tenant deployment does not serve.
A frontend that must work in both has to know about both.

The demo deployment's own routes (``/api/v1/demo``) are absent from that document by
design -- a production deployment does not serve them -- and are published beside it, in
``openapi.demo.json``: only the demo paths and the schemas they reference, generated from a
demo-mode application. The frontend's demo guide is checked against that file.

Run ``python -m app.contracts.openapi`` to rewrite both snapshots after changing an endpoint.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from app.config import build_settings

__all__ = [
    "DEMO_PATH_PREFIX",
    "DEMO_SNAPSHOT_PATH",
    "SNAPSHOT_PATH",
    "generate",
    "generate_demo",
    "write",
]

SNAPSHOT_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "docs" / "contracts" / "v1" / "openapi.json"
)
DEMO_SNAPSHOT_PATH = SNAPSHOT_PATH.with_name("openapi.demo.json")
#: Every route a demo deployment serves and a production deployment does not.
DEMO_PATH_PREFIX = "/api/v1/demo"


def generate() -> dict[str, Any]:
    """The OpenAPI document for a development-mode application.

    Imported lazily so that this module can be imported without building an application —
    ``app.main`` constructs one at import time.
    """
    from app.main import create_app

    app = create_app(
        build_settings(
            environment="development",
            log_format="text",
            # Fixed so that the generated document does not change between runs. Nothing
            # about the secret reaches the document; it only keeps settings constructible.
            dev_auth_secret="snapshot-generation-only-not-a-real-secret",
        )
    )
    document: dict[str, Any] = app.openapi()
    return document


def generate_demo() -> dict[str, Any]:
    """The demo-only surface: the ``/api/v1/demo`` paths of a demo-mode application and the
    component schemas they reference, transitively. Building the application loads no
    dataset and touches no database."""
    from app.main import create_app

    app = create_app(
        build_settings(
            environment="development",
            log_format="text",
            dev_auth_secret="snapshot-generation-only-not-a-real-secret",
            runtime_mode="demo",
            database_url="postgresql+psycopg://snapshot:snapshot@localhost:5432/adg_demo",
        )
    )
    full: dict[str, Any] = app.openapi()
    paths = {
        path: item for path, item in full["paths"].items() if path.startswith(DEMO_PATH_PREFIX)
    }
    schemas: dict[str, Any] = full.get("components", {}).get("schemas", {})
    wanted: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[-1]
                if name not in wanted:
                    wanted.add(name)
                    collect(schemas[name])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(paths)
    return {
        "openapi": full["openapi"],
        "info": {
            **full["info"],
            "description": (
                "The routes a demo deployment (ADG_RUNTIME_MODE=demo) serves in addition to "
                "openapi.json. A production deployment serves none of them."
            ),
        },
        "paths": paths,
        "components": {"schemas": {name: schemas[name] for name in sorted(wanted)}},
    }


def _dump(document: dict[str, Any], path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def write(path: pathlib.Path = SNAPSHOT_PATH) -> pathlib.Path:
    """Write the snapshot, formatted stably so that a diff shows only real changes."""
    return _dump(generate(), path)


def write_demo(path: pathlib.Path = DEMO_SNAPSHOT_PATH) -> pathlib.Path:
    """Write the demo-surface snapshot beside the production one."""
    return _dump(generate_demo(), path)


if __name__ == "__main__":  # pragma: no cover - developer entry point
    for written in (write(), write_demo()):
        print(f"Wrote {written}")
