r"""No access route may ask a question whose answer is the estate.

"Who can reach what" is a join between every principal and every resource, and the whole of
it is a Cartesian product: ten thousand users against a million directories is ten billion
rows, and an endpoint that will compute it given the right query string is a denial of
service against the organization that installed the product — reachable by a single GET, from
an API that Phase 6 has not yet put authentication in front of.

The defense is structural rather than defensive. Every access route is **anchored**: it names
one principal, or one resource, in its path. There is no route that names neither, so there
is no query string that asks for the product, and no limit needs to be enforced to prevent
one. These tests hold that shape in place by reading the routes themselves, so a future route
that forgets the anchor fails here rather than in production.

Read against the OpenAPI document rather than by calling the endpoints, deliberately: a
behavioral test can only find the unbounded route somebody thought to call.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import FastAPI

from app.access_engine import MAX_PATHS_CEILING, MAX_REMOVAL_TARGETS_CEILING
from app.api.pagination import MAX_LIMIT
from app.domain import MAX_DEPTH_CEILING, MAX_NODES_CEILING
from app.main import create_app

ACCESS_PREFIX = "/api/v1/access"

ANCHORS = ("{identifier}", "{resource}")
"""A path parameter that pins the answer to one principal or one directory.

Every access route must carry at least one. That is the property that makes the product
unreachable: an answer anchored to one principal is bounded by the ACLs that name it, and one
anchored to one resource is bounded by that resource's ACL.
"""

SINGULAR_ROUTES = frozenset(
    {
        # OpenAPI normalizes FastAPI's `{resource:path}` converter away, so these are the
        # routes spelled as the published contract spells them.
        "/api/v1/access/principals/{identifier}/resources/{resource}",
        "/api/v1/access/paths/principals/{identifier}/resources/{resource}",
    }
)
"""Routes that answer about exactly one pair and therefore need no paging.

The explanation route is exempt on the same ground as the answer it explains: it names one
principal and one resource, so there is no population to page through. Its size is bounded
instead by ``max_paths`` and ``max_removal_targets``, which
:class:`TestTheExplanationRouteIsBoundedWithoutPaging` holds in place — an exemption from
paging is not an exemption from being bounded.
"""

EXPLANATION_ROUTE = "/api/v1/access/paths/principals/{identifier}/resources/{resource}"


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


@pytest.fixture(scope="module")
def schema(app: FastAPI) -> dict[str, Any]:
    return app.openapi()


def access_paths(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        path: operations
        for path, operations in schema["paths"].items()
        if path.startswith(ACCESS_PREFIX)
    }


class TestThereAreAccessRoutesToCheck:
    """Every assertion below is vacuous if the prefix stops matching."""

    def test_the_schema_documents_them(self, schema: dict[str, Any]) -> None:
        assert len(access_paths(schema)) >= 4


class TestEveryRouteIsAnchored:
    def test_no_route_names_neither_a_principal_nor_a_resource(
        self, schema: dict[str, Any]
    ) -> None:
        """The test that makes the Cartesian product unreachable rather than merely capped."""
        unanchored = [
            path for path in access_paths(schema) if not any(anchor in path for anchor in ANCHORS)
        ]
        assert not unanchored, (
            "These access routes are not anchored to one principal or one resource, so their "
            f"answer grows with the estate: {unanchored}"
        )

    def test_no_route_takes_two_collections(self, schema: dict[str, Any]) -> None:
        """Two plural path segments with no anchor between them is the product, spelled out."""
        for path in access_paths(schema):
            collections = [
                part for part in path.split("/") if part in {"principals", "resources", "shares"}
            ]
            if len(collections) < 2:
                continue
            assert any(anchor in path for anchor in ANCHORS), path


class TestEveryListingIsPaged:
    """An anchored route can still return more rows than anyone can use."""

    def test_every_listing_accepts_a_limit_and_a_cursor(self, schema: dict[str, Any]) -> None:
        missing: list[str] = []
        for path, operations in access_paths(schema).items():
            if path in SINGULAR_ROUTES:
                continue
            names = _parameter_names(operations)
            if not {"limit", "cursor"} <= names:
                missing.append(f"{path} takes {sorted(names)}")
        assert not missing, "Listings with no paging: " + "; ".join(missing)

    def test_every_limit_is_capped(self, schema: dict[str, Any]) -> None:
        """A cap in the schema is a cap the framework enforces before any code runs."""
        uncapped: list[str] = []
        for path, operations in access_paths(schema).items():
            if path in SINGULAR_ROUTES:
                continue
            limit = _parameter(operations, "limit")
            assert limit is not None, path
            maximum = _maximum_of(limit["schema"])
            if maximum is None or maximum > MAX_LIMIT:
                uncapped.append(f"{path} caps limit at {maximum}")
        assert not uncapped, "Limits that are not bounded by MAX_LIMIT: " + "; ".join(uncapped)

    def test_every_listing_reports_whether_more_exists(self, schema: dict[str, Any]) -> None:
        """A page that cannot say it is partial is a page that will be read as complete.

        The indicator lives in the shared `page` envelope rather than at the top level of
        each response, so this follows the reference: asserting on the outer properties
        alone would pass for a response that carried a `page` object with nothing in it.
        """
        for path, operations in access_paths(schema).items():
            if path in SINGULAR_ROUTES:
                continue
            model = _response_model(schema, operations)
            assert model is not None, path
            page = _referenced(schema, model.get("properties", {}).get("page"))
            assert page is not None, (
                f"{path} returns {sorted(model.get('properties', {}))} and no page envelope"
            )
            properties = set(page.get("properties", {}))
            assert {"has_more", "next_cursor"} <= properties, (
                f"{path} pages with {sorted(properties)}, which cannot say the page is partial"
            )

    def test_the_singular_route_really_is_singular(self, schema: dict[str, Any]) -> None:
        """Guards the exemption: it is only sound while the route answers about one pair."""
        for path in SINGULAR_ROUTES:
            assert path in access_paths(schema), f"{path} no longer exists; revisit the exemption"
            assert path.count("{") == 2, path


class TestTheExplanationRouteIsBoundedWithoutPaging:
    """The paging exemption is sound only while something else bounds the response.

    An explanation is not a listing — there is no population to slice — but it is still a
    graph, and group nesting is combinatorial: a subject in twenty groups that each nest
    three ways into one ACL trustee has sixty chains to one ACE. So the route is held to
    the same standard by a different instrument: explicit, capped limits on the two things
    that can grow, and a flag that says when either of them bit.
    """

    def test_the_route_exists(self, schema: dict[str, Any]) -> None:
        """Every assertion below is vacuous if the route is renamed."""
        assert EXPLANATION_ROUTE in access_paths(schema)

    def test_it_caps_the_paths_it_will_enumerate(self, schema: dict[str, Any]) -> None:
        operations = access_paths(schema)[EXPLANATION_ROUTE]
        parameter = _parameter(operations, "max_causal_paths")
        assert parameter is not None, "the explanation has no bound on its own size"
        maximum = _maximum_of(parameter["schema"])
        assert maximum is not None and 0 < maximum <= MAX_PATHS_CEILING

    def test_it_caps_the_removals_it_will_measure(self, schema: dict[str, Any]) -> None:
        """Each removal target re-runs the access check, so this one is a compute bound."""
        operations = access_paths(schema)[EXPLANATION_ROUTE]
        parameter = _parameter(operations, "max_removal_targets")
        assert parameter is not None
        maximum = _maximum_of(parameter["schema"])
        assert maximum is not None and 0 < maximum <= MAX_REMOVAL_TARGETS_CEILING

    def test_it_says_when_it_was_cut_short(self, schema: dict[str, Any]) -> None:
        """A truncated explanation read as complete is a conclusion drawn from a subset."""
        operations = access_paths(schema)[EXPLANATION_ROUTE]
        model = _response_model(schema, operations)
        assert model is not None
        properties = set(model.get("properties", {}))
        assert {"complete", "truncation", "limits"} <= properties, (
            f"{EXPLANATION_ROUTE} returns {sorted(properties)} and cannot say it is partial"
        )


class TestTheTraversalIsBoundedToo:
    """Paging bounds the answer; it does not bound the walk that produced it."""

    def test_every_route_accepts_the_traversal_limits(self, schema: dict[str, Any]) -> None:
        """A nested group can be arbitrarily deep, and depth is not visible in a page size."""
        for path, operations in access_paths(schema).items():
            names = _parameter_names(operations)
            assert {"max_depth", "max_nodes"} & names, (
                f"{path} takes {sorted(names)} and cannot bound its own traversal"
            )

    def test_the_traversal_ceilings_are_finite(self) -> None:
        """The cap is enforced in code and reported, not declared in the schema.

        `TraversalLimits.clamped` silently reduces an over-large request to the server
        ceiling and the response states the limits actually used, so an unbounded value in
        the query string produces a bounded walk and an honest answer rather than a 422.
        That choice is Phase 1B's; what matters here is only that the ceilings exist and are
        finite, since a ceiling of `None` would make the clamp a no-op. The clamp itself is
        exercised end to end in `tests/db/test_access_performance.py`.
        """
        assert isinstance(MAX_DEPTH_CEILING, int) and 0 < MAX_DEPTH_CEILING <= 1_000
        assert isinstance(MAX_NODES_CEILING, int) and 0 < MAX_NODES_CEILING <= 10_000_000


# --------------------------------------------------------------------------------------
# Reading the schema
# --------------------------------------------------------------------------------------


def _operations(operations: dict[str, Any]) -> list[dict[str, Any]]:
    return [body for method, body in operations.items() if method in {"get", "post"}]


def _parameter_names(operations: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for operation in _operations(operations):
        for parameter in operation.get("parameters", []):
            names.add(parameter["name"])
    return names


def _parameter(operations: dict[str, Any], name: str) -> dict[str, Any] | None:
    for operation in _operations(operations):
        for parameter in operation.get("parameters", []):
            if parameter["name"] == name:
                found: dict[str, Any] = parameter
                return found
    return None


def _maximum_of(schema: dict[str, Any]) -> int | None:
    """The upper bound of a parameter schema, through the optional-parameter wrappers.

    An optional query parameter is rendered as ``anyOf: [{integer, maximum}, {null}]``, so
    the bound is one level down; a required one carries it directly. Both spellings mean the
    same cap and both must be found, or this test would pass for a parameter that has none.
    """
    if "maximum" in schema:
        return int(schema["maximum"])
    for variant in schema.get("anyOf", []):
        if "maximum" in variant:
            return int(variant["maximum"])
    return None


def _referenced(schema: dict[str, Any], node: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve a one-level `$ref` into the component it names."""
    if not node:
        return None
    reference = node.get("$ref", "")
    if not reference:
        return node
    name = re.sub(r"^#/components/schemas/", "", reference)
    component: dict[str, Any] | None = schema["components"]["schemas"].get(name)
    return component


def _response_model(schema: dict[str, Any], operations: dict[str, Any]) -> dict[str, Any] | None:
    for operation in _operations(operations):
        content = operation.get("responses", {}).get("200", {}).get("content", {})
        reference = content.get("application/json", {}).get("schema", {}).get("$ref", "")
        if reference:
            name = re.sub(r"^#/components/schemas/", "", reference)
            model: dict[str, Any] | None = schema["components"]["schemas"].get(name)
            return model
    return None
