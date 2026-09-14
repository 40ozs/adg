r"""The cache validator: what a 304 promises, and what would make it a lie.

A conditional GET is a claim — *"the body you already have is the body I would send"* — and
the claim is wrong the moment the validator omits something the body depends on. The failure
is silent and it is served as a fresh answer, so these tests are mostly about the components
of the key rather than about the HTTP mechanics.

The mechanics are here too, because ``If-None-Match`` has more shapes than a server usually
sees in development: a list, a ``*``, a weak tag against a strong one.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.api.caching import (
    BASIS_HEADER,
    CACHE_CONTROL,
    CONTRACT_VERSION,
    basis_view,
    not_modified,
    validator_for,
)
from app.domain import EMPTY_BASIS, CollectionBasis

BASIS = CollectionBasis(
    runs=2,
    latest_run_id="6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31",
    latest_activity_at=dt.datetime(2026, 9, 14, 8, 30, tzinfo=dt.UTC),
    observations_applied=900,
    batches_received=3,
)

PARAMETERS = {
    "principal": "S-1-5-21-1-2-3-1104",
    "resource": "\\\\FS01\\Finance",
    "host": None,
    "access_path": "remote_smb",
    "max_depth": 32,
}


def validator(**overrides: object):  # type: ignore[no-untyped-def]
    parameters = {**PARAMETERS, **overrides}
    return validator_for(BASIS, route="access.explain", parameters=parameters)


class TestTheValidatorCoversEverythingTheBodyDependsOn:
    """Each of these, changed alone, changes the answer. So each must change the tag."""

    def test_the_same_request_over_the_same_basis_is_the_same_tag(self) -> None:
        assert validator().etag == validator().etag

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("principal", "S-1-5-21-1-2-3-1105"),
            ("resource", "\\\\FS01\\Payroll"),
            ("host", "fs01"),
            ("access_path", "local"),
            ("max_depth", 8),
        ],
    )
    def test_changing_a_parameter_changes_the_tag(self, name: str, value: object) -> None:
        assert validator(**{name: value}).etag != validator().etag

    def test_collecting_anything_changes_the_tag(self) -> None:
        """The whole design in one assertion: a new run invalidates every answer."""
        moved = validator_for(
            CollectionBasis(
                runs=3,
                latest_run_id=BASIS.latest_run_id,
                latest_activity_at=BASIS.latest_activity_at,
                observations_applied=BASIS.observations_applied,
                batches_received=BASIS.batches_received,
            ),
            route="access.explain",
            parameters=PARAMETERS,
        )

        assert moved.etag != validator().etag

    def test_two_routes_do_not_share_a_tag(self) -> None:
        """``/explain`` and ``/paths`` take the same parameters and return different bodies."""
        paths = validator_for(BASIS, route="access.paths", parameters=PARAMETERS)

        assert paths.etag != validator().etag

    def test_an_absent_parameter_is_not_the_same_as_a_present_null(self) -> None:
        """Dropping nulls would let a route's new optional parameter reuse an old tag."""
        without = validator_for(
            BASIS,
            route="access.explain",
            parameters={key: value for key, value in PARAMETERS.items() if key != "host"},
        )

        assert without.etag != validator().etag

    def test_parameter_order_does_not_matter(self) -> None:
        """Otherwise the tag would depend on how the route happened to spell its dict."""
        reversed_order = dict(reversed(list(PARAMETERS.items())))

        assert validator_for(BASIS, route="access.explain", parameters=reversed_order).etag == (
            validator().etag
        )


class TestTheTagIsReadable:
    def test_it_is_quoted(self) -> None:
        """An unquoted ETag is not a valid one, and some caches drop it silently."""
        etag = validator().etag

        assert etag.startswith('"') and etag.endswith('"')

    def test_it_names_the_contract_version_and_the_basis(self) -> None:
        """So an operator comparing two responses can see *why* they differ."""
        etag = validator().etag

        assert CONTRACT_VERSION in etag
        assert BASIS.token in etag


class TestIfNoneMatch:
    def test_no_header_never_matches(self) -> None:
        assert not validator().matches(None)
        assert not validator().matches("")

    def test_its_own_tag_matches(self) -> None:
        current = validator()

        assert current.matches(current.etag)

    def test_another_tag_does_not(self) -> None:
        assert not validator().matches(validator(access_path="local").etag)

    def test_a_list_matches_if_any_member_does(self) -> None:
        """A client holding several cached variants sends all of their tags."""
        current = validator()
        header = f'"stale-one", {current.etag}, "stale-two"'

        assert current.matches(header)

    def test_a_weak_tag_matches_the_strong_one(self) -> None:
        """RFC 9110 requires the weak comparison for If-None-Match, however it was issued."""
        current = validator()

        assert current.matches(f"W/{current.etag}")

    def test_a_star_matches(self) -> None:
        """``*`` means "if you have anything at all", and the server always does."""
        assert validator().matches("*")


class TestTheResponseHeaders:
    def test_a_fresh_answer_carries_the_validator_and_the_rules(self) -> None:
        from fastapi import Response

        response = Response()
        current = validator()
        current.apply(response)

        assert response.headers["ETag"] == current.etag
        assert response.headers["Cache-Control"] == CACHE_CONTROL
        assert response.headers[BASIS_HEADER] == BASIS.token

    def test_the_rules_forbid_a_shared_cache_and_forbid_reuse_without_asking(self) -> None:
        """The two words the whole design rests on."""
        assert "private" in CACHE_CONTROL
        assert "no-cache" in CACHE_CONTROL
        assert "max-age" not in CACHE_CONTROL, "a duration is exactly what this must not have"

    def test_the_vary_header_names_the_authorization(self) -> None:
        """Two callers with different capabilities must not share a cached entry."""
        from fastapi import Response

        response = Response()
        validator().apply(response)

        assert "Authorization" in response.headers["Vary"]

    def test_a_304_repeats_the_headers_and_carries_no_body(self) -> None:
        """A client updates its stored headers from the 304; omitting them loses the rules."""
        current = validator()
        response = not_modified(current)

        assert response.status_code == 304
        assert response.body == b""
        assert response.headers["ETag"] == current.etag
        assert response.headers["Cache-Control"] == CACHE_CONTROL


class TestTheRenderedBasis:
    def test_it_carries_the_token_a_client_would_compare(self) -> None:
        view = basis_view(BASIS)

        assert view.token == BASIS.token
        assert view.runs == 2
        assert not view.is_empty

    def test_an_empty_estate_says_so(self) -> None:
        """The field a UI needs to render "nothing has been collected" rather than "none"."""
        assert basis_view(EMPTY_BASIS).is_empty
