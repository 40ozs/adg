"""Publishing the API's OpenAPI document as a checked-in contract.

The frontend hand-writes its response types and checks them against this snapshot. That
only works if the snapshot is current, so it is generated from the application rather than
maintained, and ``tests/contracts/test_openapi_snapshot.py`` fails when the two diverge.

The document is generated for a **development-mode** application on purpose: it is the
superset, containing the development login route that a tenant deployment does not serve.
A frontend that must work in both has to know about both.

Run ``python -m app.contracts.openapi`` to rewrite the snapshot after changing an endpoint.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from app.config import build_settings

__all__ = ["SNAPSHOT_PATH", "generate", "write"]

SNAPSHOT_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "docs" / "contracts" / "v1" / "openapi.json"
)


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


def write(path: pathlib.Path = SNAPSHOT_PATH) -> pathlib.Path:
    """Write the snapshot, formatted stably so that a diff shows only real changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(generate(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":  # pragma: no cover - developer entry point
    written = write()
    print(f"Wrote {written}")
