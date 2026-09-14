"""The published OpenAPI document must match the application that serves it.

`docs/contracts/v1/openapi.json` is what the frontend checks its hand-written response
types against. A stale snapshot is worse than none: it would let the frontend keep
believing in a field the API stopped sending, and the type check would agree with it.

This test is the reason the snapshot can be trusted. When it fails, regenerate:

    python -m app.contracts.openapi
"""

from __future__ import annotations

import json

from app.contracts.openapi import SNAPSHOT_PATH, generate


def test_the_snapshot_is_current() -> None:
    generated = generate()
    published = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    missing = sorted(set(generated["paths"]) - set(published["paths"]))
    extra = sorted(set(published["paths"]) - set(generated["paths"]))

    assert generated == published, (
        "docs/contracts/v1/openapi.json is out of date. "
        f"Paths served but not published: {missing or 'none'}. "
        f"Paths published but not served: {extra or 'none'}. "
        "Regenerate with: python -m app.contracts.openapi"
    )


def test_the_snapshot_describes_the_development_superset() -> None:
    """A tenant deployment serves fewer routes than this document. The frontend has to work
    against both, so the snapshot is the superset — and the extra route is named here so
    that nobody mistakes its presence for a production route."""
    published = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert "/auth/dev/login" in published["paths"]
    assert "DEVELOPMENT ONLY" in published["paths"]["/auth/dev/login"]["post"]["summary"]


def test_it_carries_no_secret() -> None:
    """The document is published in the repository. Nothing in it may be a credential."""
    raw = SNAPSHOT_PATH.read_text(encoding="utf-8").casefold()

    assert "snapshot-generation-only" not in raw
    assert "dev_auth_secret" not in raw
