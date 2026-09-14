"""Opaque cursors and the page envelope every list endpoint returns.

Two pagination styles, because the two kinds of result page differently and pretending
otherwise would be a lie about what the cursor guarantees:

* **Keyset** for direct listings straight out of an index (``after`` the last key seen).
  A group's membership changes while it is being paged; an offset would skip or repeat
  rows across pages, and in an audit tool a skipped member is a missed finding.
* **Offset** for recursive results, which are computed as one bounded traversal and then
  sliced. The traversal is re-run per page, so a change between pages can still shift the
  result — the response says so through its ``traversal`` block rather than implying a
  stability it does not have.

Cursors are base64url-encoded JSON, not signed. They are a position, not a capability: the
value they carry is a key the caller already has, so forging one grants nothing that the
same request without a cursor would not.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "InvalidCursor",
    "PageInfo",
    "decode_keyset_cursor",
    "decode_offset_cursor",
    "encode_keyset_cursor",
    "encode_offset_cursor",
    "normalize_limit",
]

DEFAULT_LIMIT: Final = 100
MAX_LIMIT: Final = 500

_KEYSET: Final = "k"
_OFFSET: Final = "o"


class InvalidCursor(ValueError):
    """A cursor was malformed or belongs to a different endpoint. Maps to HTTP 422.

    Rejected rather than ignored: silently restarting from the first page would make a
    client's second page look like a complete result set.
    """


class PageInfo(BaseModel):
    """Where a caller is in a list, and how to continue."""

    limit: int = Field(description="Maximum items this page could contain.")
    has_more: bool = Field(description="Whether more items exist beyond this page.")
    next_cursor: str | None = Field(
        default=None,
        description="Pass back as ?cursor= to fetch the next page. Null on the last page.",
    )
    total: int | None = Field(
        default=None,
        description=(
            "Exact number of items across all pages, when the endpoint can count them "
            "cheaply. Null means not counted, never zero."
        ),
    )


def normalize_limit(limit: int | None) -> int:
    """Clamp a requested page size into the allowed range."""
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def encode_keyset_cursor(after: str) -> str:
    """A cursor that resumes after a known sort key."""
    return _encode({"t": _KEYSET, "k": after})


def decode_keyset_cursor(cursor: str | None) -> str | None:
    """The sort key to resume after, or ``None`` for the first page."""
    if cursor is None:
        return None
    payload = _decode(cursor, expected=_KEYSET)
    value = payload.get("k")
    if not isinstance(value, str) or not value:
        raise InvalidCursor("This cursor carries no position; request the page without one.")
    return value


def encode_offset_cursor(offset: int) -> str:
    """A cursor that resumes at an index into a recomputed result."""
    return _encode({"t": _OFFSET, "o": offset})


def decode_offset_cursor(cursor: str | None) -> int:
    """The index to resume at, or ``0`` for the first page."""
    if cursor is None:
        return 0
    payload = _decode(cursor, expected=_OFFSET)
    value = payload.get("o")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidCursor("This cursor carries no valid offset; request the page without one.")
    return value


def _encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(cursor: str, *, expected: Literal["k", "o"]) -> dict[str, Any]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise InvalidCursor(
            "This is not a cursor this API issued. Cursors are opaque: pass back the "
            "next_cursor value from a previous response, unmodified."
        ) from exc
    if not isinstance(payload, dict) or payload.get("t") != expected:
        raise InvalidCursor(
            "This cursor was issued by a different endpoint. A cursor is only valid for the "
            "query that produced it; reusing one elsewhere would page through the wrong set."
        )
    return payload
