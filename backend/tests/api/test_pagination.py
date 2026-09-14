"""Cursors: opaque, endpoint-specific, and never silently ignored."""

from __future__ import annotations

import base64
import json

import pytest

from app.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    InvalidCursor,
    decode_keyset_cursor,
    decode_offset_cursor,
    encode_keyset_cursor,
    encode_offset_cursor,
    normalize_limit,
)


class TestRoundTrip:
    @pytest.mark.parametrize(
        "value",
        [
            "S-1-5-21-1-2-3-1104",
            "fs01|S-1-5-32-544",
            "a" * 400,
            "key with spaces and | pipes",
            "ünïcödé-key",
        ],
    )
    def test_a_keyset_cursor_survives_a_round_trip(self, value: str) -> None:
        assert decode_keyset_cursor(encode_keyset_cursor(value)) == value

    @pytest.mark.parametrize("offset", [0, 1, 100, 999_999])
    def test_an_offset_cursor_survives_a_round_trip(self, offset: int) -> None:
        assert decode_offset_cursor(encode_offset_cursor(offset)) == offset

    def test_a_cursor_is_url_safe(self) -> None:
        cursor = encode_keyset_cursor("fs01|S-1-5-32-544")
        assert "+" not in cursor and "/" not in cursor and "=" not in cursor

    def test_no_cursor_means_the_first_page(self) -> None:
        assert decode_keyset_cursor(None) is None
        assert decode_offset_cursor(None) == 0


class TestRejection:
    """A bad cursor must fail loudly.

    Silently restarting from page one would make a client's second page look like a
    complete result set — the same class of mistake as reporting a truncated traversal as
    complete.
    """

    @pytest.mark.parametrize("cursor", ["", "not-base64!!", "Zm9v", "!!!!"])
    def test_a_malformed_cursor_is_rejected(self, cursor: str) -> None:
        with pytest.raises(InvalidCursor):
            decode_keyset_cursor(cursor)

    def test_a_keyset_cursor_is_refused_by_the_offset_decoder(self) -> None:
        cursor = encode_keyset_cursor("some-key")
        with pytest.raises(InvalidCursor, match="different endpoint"):
            decode_offset_cursor(cursor)

    def test_an_offset_cursor_is_refused_by_the_keyset_decoder(self) -> None:
        cursor = encode_offset_cursor(10)
        with pytest.raises(InvalidCursor, match="different endpoint"):
            decode_keyset_cursor(cursor)

    def test_a_negative_offset_is_rejected(self) -> None:
        forged = _forge({"t": "o", "o": -5})
        with pytest.raises(InvalidCursor, match="valid offset"):
            decode_offset_cursor(forged)

    def test_a_boolean_is_not_an_offset(self) -> None:
        # True == 1 in Python; accepting it would page from a position nobody asked for.
        forged = _forge({"t": "o", "o": True})
        with pytest.raises(InvalidCursor, match="valid offset"):
            decode_offset_cursor(forged)

    def test_an_empty_key_is_rejected(self) -> None:
        forged = _forge({"t": "k", "k": ""})
        with pytest.raises(InvalidCursor, match="no position"):
            decode_keyset_cursor(forged)

    def test_a_json_array_is_not_a_cursor(self) -> None:
        forged = _forge([1, 2, 3])
        with pytest.raises(InvalidCursor):
            decode_keyset_cursor(forged)


class TestLimits:
    def test_no_limit_uses_the_default(self) -> None:
        assert normalize_limit(None) == DEFAULT_LIMIT

    @pytest.mark.parametrize(("requested", "expected"), [(1, 1), (50, 50), (MAX_LIMIT, MAX_LIMIT)])
    def test_a_limit_in_range_is_honored(self, requested: int, expected: int) -> None:
        assert normalize_limit(requested) == expected

    @pytest.mark.parametrize("requested", [MAX_LIMIT + 1, 10_000])
    def test_an_oversized_limit_is_clamped(self, requested: int) -> None:
        assert normalize_limit(requested) == MAX_LIMIT

    @pytest.mark.parametrize("requested", [0, -1, -1000])
    def test_a_nonsense_limit_becomes_one(self, requested: int) -> None:
        assert normalize_limit(requested) == 1


def _forge(payload: object) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
