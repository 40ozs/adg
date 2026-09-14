"""The collection basis: what makes a cached answer stale, and what does not.

The basis is a cache key, so it has exactly one job and two ways to fail at it. If it moves
when nothing has changed, answers are recomputed for no reason — wasteful, harmless. If it
holds still when something *has* changed, a client serves an explanation of an estate that
no longer exists, and is told it is current. These tests are almost entirely about the
second failure.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Any

import pytest

from app.domain import EMPTY_BASIS, CollectionBasis

MOMENT = dt.datetime(2026, 9, 14, 8, 30, 0, 123456, tzinfo=dt.UTC)


def basis(**overrides: object) -> CollectionBasis:
    fields: dict[str, object] = {
        "runs": 3,
        "latest_run_id": "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31",
        "latest_activity_at": MOMENT,
        "observations_applied": 1200,
        "batches_received": 4,
    }
    fields.update(overrides)
    return CollectionBasis(**fields)  # type: ignore[arg-type]


class TestTheTokenIsStable:
    """A token that changed for any other reason would be a permanent cache miss."""

    def test_the_same_basis_digests_the_same_way(self) -> None:
        assert basis().token == basis().token

    def test_it_is_short_enough_to_read_and_long_enough_to_trust(self) -> None:
        token = basis().token

        assert len(token) == 32
        assert all(character in "0123456789abcdef" for character in token)

    def test_an_empty_estate_has_a_token_too(self) -> None:
        """ "Nothing collected" is a state a client renders, so it needs a cache identity."""
        assert EMPTY_BASIS.is_empty
        assert EMPTY_BASIS.token
        assert EMPTY_BASIS.token != basis().token


class TestEveryFieldMovesTheToken:
    """Each field is in the digest because each one can change without the others.

    Parametrized rather than written out so that adding a field to the basis and forgetting
    to digest it fails here — the new field arrives in ``basis()`` and its case has to be
    added, which is the moment somebody thinks about whether it belongs in the key.
    """

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("runs", 4),
            ("latest_run_id", "11111111-2222-3333-4444-555555555555"),
            ("latest_activity_at", MOMENT + dt.timedelta(microseconds=1)),
            ("observations_applied", 1201),
            ("batches_received", 5),
        ],
    )
    def test_changing_it_changes_the_token(self, field: str, value: Any) -> None:
        # `Any` rather than `object`: the parametrization deliberately mixes an int, a str
        # and a datetime, and `replace` type-checks each field against its own declared
        # type. A widened `object` makes every one of the five a type error.
        changed: dict[str, Any] = {field: value}

        assert replace(basis(), **changed).token != basis().token

    def test_every_field_is_covered(self) -> None:
        """Keeps the parametrization above honest as the dataclass grows."""
        covered = {
            "runs",
            "latest_run_id",
            "latest_activity_at",
            "observations_applied",
            "batches_received",
        }
        assert set(CollectionBasis.__slots__) == covered


class TestAnIdenticalInstantDigestsIdentically:
    """Two API workers must agree, or a client revalidates forever and never gets a 304."""

    def test_the_same_instant_in_another_zone_is_the_same_token(self) -> None:
        elsewhere = MOMENT.astimezone(dt.timezone(dt.timedelta(hours=9)))

        assert elsewhere.utcoffset() != MOMENT.utcoffset(), "same instant, different zone"
        assert basis(latest_activity_at=elsewhere).token == basis().token

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        """A naive value can only reach the digest from a test or a non-tz-aware driver.

        Pinned rather than rejected: raising here would turn a cosmetic difference into a
        500 on a live endpoint, and reading it as UTC is what the column already means.
        """
        naive = MOMENT.replace(tzinfo=None)

        assert basis(latest_activity_at=naive).token == basis().token

    def test_a_microsecond_is_not_rounded_away(self) -> None:
        """Two runs finishing in the same second are two different states."""
        nearly = MOMENT + dt.timedelta(microseconds=1)

        assert basis(latest_activity_at=nearly).token != basis().token


class TestTheEmptyState:
    def test_no_runs_is_empty(self) -> None:
        assert basis(runs=0).is_empty

    def test_one_run_is_not(self) -> None:
        assert not basis(runs=1).is_empty
