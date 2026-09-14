"""The authorization boundary, asserted against the route table rather than a list.

Two audits, and they answer different questions.

:class:`TestEveryRouteIsBehindTheBoundary` reads the OpenAPI document — every route the
application actually serves — and calls each one **without a credential**. A route added in
a later phase and left unprotected fails here, whether or not anyone remembered to write a
test for it. Enumerating endpoints by hand would only ever prove things about the endpoints
somebody thought of.

:class:`TestEveryRouteRequiresTheRightCapability` then calls every route **as each role**
and checks the answer against one declared table. "Behind a capability" and "behind the
*right* capability" are different properties: a route that required ``identities:read``
where it should require ``access:read`` passes the first audit completely, and would let a
viewer read the group-to-resource impact — which is the more sensitive disclosure the Phase
6A note at the include site is specifically about. The table is exhaustive over the routes
the application serves, so a new route with no entry fails rather than being skipped.

The database is a stub that raises if a session is opened. That turns a third property into
a test: a request that is going to be rejected must be rejected *before* it costs a
connection, which is the difference between an unauthenticated request being free and being
a way to exhaust the pool. It also means an *authorized* request fails loudly — which is
exactly how the capability audit distinguishes "allowed through" from "refused".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import PUBLIC_PATHS
from app.auth.roles import (
    RESERVED_CAPABILITIES,
    ROLE_CAPABILITIES,
    Capability,
    Role,
    capabilities_for,
)
from app.config import Settings, build_settings
from app.db import ConnectivityResult
from app.main import create_app
from tests.support.auth import auth_headers, development_token, token_for_roles

#: Values substituted into path parameters. Any syntactically acceptable value does: the
#: request must be refused before anything looks at it.
PATH_PARAMETERS = {
    "run_id": "00000000-0000-0000-0000-000000000001",
    "identifier": "S-1-5-21-1-2-3-1000",
    "trustee": "S-1-5-21-1-2-3-1000",
    "server": "fs01",
    "share": "fs01%7Cfinance",
    "resource": "fs01%7Cfinance",
}


class ExplodingDatabase:
    """A database that fails loudly if anything opens a session."""

    async def check_connectivity(self) -> ConnectivityResult:
        return ConnectivityResult(ok=True, latency_ms=1.0)

    async def dispose(self) -> None:
        return None

    def session(self) -> Any:
        raise AssertionError(
            "A database session was opened for a request that authorization rejects. "
            "Declare the auth dependency on the route, not only as a handler parameter: "
            "handler parameters are solved in declaration order, so one after 'session' "
            "runs too late."
        )


@pytest.fixture
def auth_settings() -> Settings:
    return build_settings(
        environment="test",
        log_format="text",
        dev_auth_secret="a-fixed-secret-for-these-tests-only",
    )


@pytest.fixture
async def anonymous(auth_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(auth_settings)
    app.state.database = ExplodingDatabase()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


def concrete_paths(app: Any) -> Iterator[tuple[str, str, str]]:
    """Every (method, template, concrete path) the application serves."""
    for template, operations in sorted(app.openapi()["paths"].items()):
        concrete = template
        for name, value in PATH_PARAMETERS.items():
            concrete = concrete.replace("{" + name + "}", value)
        assert "{" not in concrete, (
            f"{template} has a path parameter this test does not know how to fill. "
            "Add it to PATH_PARAMETERS rather than skipping the route."
        )
        for method in operations:
            yield method.upper(), template, concrete


class TestEveryRouteIsBehindTheBoundary:
    async def test_no_route_answers_without_a_credential(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        app = create_app(auth_settings)
        unprotected: list[str] = []

        for method, template, concrete in concrete_paths(app):
            if template in PUBLIC_PATHS:
                continue
            response = await anonymous.request(method, concrete, params={"q": "finance"})
            if response.status_code != 401:
                unprotected.append(f"{method} {template} -> {response.status_code}")

        assert not unprotected, (
            "These routes answered an unauthenticated request:\n  "
            + "\n  ".join(unprotected)
            + "\nAdd the router to app/api/__init__.py behind a capability, or add the "
            "path to PUBLIC_PATHS with a reason."
        )

    async def test_the_public_routes_are_exactly_the_documented_ones(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        app = create_app(auth_settings)
        answered: set[str] = set()

        for method, template, concrete in concrete_paths(app):
            response = await anonymous.request(method, concrete, params={"q": "finance"})
            # 422 is an answer: the route processed the request and objected to its body,
            # which only an unauthenticated-reachable route can do.
            if response.status_code != 401:
                answered.add(template)

        assert answered == set(PUBLIC_PATHS)

    async def test_a_rejected_request_never_reaches_the_database(
        self, anonymous: AsyncClient
    ) -> None:
        """ExplodingDatabase raises rather than returning, so this passes only if no route
        opened a session. Asserted separately because the message matters."""
        response = await anonymous.get("/api/v1/servers")

        assert response.status_code == 401

    async def test_the_challenge_names_the_scheme(self, anonymous: AsyncClient) -> None:
        response = await anonymous.get("/api/v1/servers")

        assert response.headers["WWW-Authenticate"] == "Bearer"


#: Which capability each route requires, as this application intends it. Exhaustive over
#: the served route table: :meth:`TestEveryRouteRequiresTheRightCapability
#: .test_the_table_covers_every_route` fails when a route is added and not listed here, so
#: the audit cannot quietly stop covering the API.
#:
#: ``None`` means the route is public (and must also appear in ``PUBLIC_PATHS``);
#: ``AUTHENTICATED`` means any valid token, whatever roles it carries; ``INGEST`` means the
#: ingestion credential — a collector key, or a role holding ``collectors:ingest``.
AUTHENTICATED = "*authenticated*"
INGEST = "*ingest*"

#: Every capability a route may require. A reserved one is excluded because no
#: role grants it, so nothing can hold it alone and there is nothing to audit.
AUDITABLE_CAPABILITIES: list[Capability] = sorted(
    set(Capability) - RESERVED_CAPABILITIES, key=lambda item: item.value
)

ROUTE_CAPABILITIES: dict[tuple[str, str], str | None] = {
    ("GET", "/health/live"): None,
    ("GET", "/health/ready"): None,
    ("GET", "/version"): None,
    ("GET", "/auth/config"): None,
    ("POST", "/auth/dev/login"): None,
    ("GET", "/auth/me"): AUTHENTICATED,
    # Ingestion. Written by a collector key far more often than by a person.
    ("POST", "/api/v1/scan-runs"): INGEST,
    ("POST", "/api/v1/scan-runs/{run_id}/batches"): INGEST,
    ("POST", "/api/v1/scan-runs/{run_id}/completion"): INGEST,
    # Run history and coverage. A viewer holds collectors:read on purpose: somebody looking
    # at an empty page has to be able to find out whether anything ran.
    ("GET", "/api/v1/scan-runs"): Capability.COLLECTORS_READ.value,
    ("GET", "/api/v1/scan-runs/{run_id}"): Capability.COLLECTORS_READ.value,
    ("GET", "/api/v1/collection/status"): Capability.COLLECTORS_READ.value,
    ("GET", "/api/v1/collection/operations"): Capability.COLLECTORS_READ.value,
    # Identities.
    ("GET", "/api/v1/principals/{identifier}"): Capability.IDENTITIES_READ.value,
    ("GET", "/api/v1/principals/{identifier}/groups"): Capability.IDENTITIES_READ.value,
    ("GET", "/api/v1/principals/{identifier}/membership-paths"): Capability.IDENTITIES_READ.value,
    ("GET", "/api/v1/groups/{identifier}/members"): Capability.IDENTITIES_READ.value,
    ("GET", "/api/v1/groups/{identifier}/effective-members"): Capability.IDENTITIES_READ.value,
    # Resources, and the raw ACLs that belong to them.
    ("GET", "/api/v1/servers"): Capability.RESOURCES_READ.value,
    ("GET", "/api/v1/servers/{server}"): Capability.RESOURCES_READ.value,
    ("GET", "/api/v1/servers/{server}/shares"): Capability.RESOURCES_READ.value,
    ("GET", "/api/v1/shares/{share}"): Capability.RESOURCES_READ.value,
    ("GET", "/api/v1/shares/{share}/acl"): Capability.RESOURCES_READ.value,
    ("GET", "/api/v1/shares/{share}/root-acl"): Capability.RESOURCES_READ.value,
    ("GET", "/api/v1/resources/{resource}"): Capability.RESOURCES_READ.value,
    ("GET", "/api/v1/resources/{resource}/acl"): Capability.RESOURCES_READ.value,
    ("GET", "/api/v1/principals/{trustee}/shares"): Capability.RESOURCES_READ.value,
    # Access answers, and the causality behind them.
    ("GET", "/api/v1/access/principals/{identifier}/shares"): Capability.ACCESS_READ.value,
    ("GET", "/api/v1/access/principals/{identifier}/resources"): Capability.ACCESS_READ.value,
    (
        "GET",
        "/api/v1/access/principals/{identifier}/resources/{resource}",
    ): Capability.ACCESS_READ.value,
    ("GET", "/api/v1/access/resources/{resource}/principals"): Capability.ACCESS_READ.value,
    ("GET", "/api/v1/access/explain"): Capability.ACCESS_READ.value,
    ("GET", "/api/v1/access/paths"): Capability.ACCESS_READ.value,
    (
        "GET",
        "/api/v1/access/paths/principals/{identifier}/resources/{resource}",
    ): Capability.ACCESS_READ.value,
    # Deliberately access:read, not identities:read. Knowing who is in a group and knowing
    # what that group reaches are different disclosures, and the more sensitive one does not
    # inherit the weaker requirement. See the note at the include site in app/api/__init__.
    ("GET", "/api/v1/groups/{identifier}/resource-impact"): Capability.ACCESS_READ.value,
    ("GET", "/api/v1/search"): Capability.SEARCH.value,
}


class TestEveryRouteRequiresTheRightCapability:
    """Behavioral, not structural.

    The capability is asserted by calling each route as each role and reading the status,
    rather than by reaching into FastAPI's dependency tree. That is deliberate twice over:
    the internals of ``include_router(dependencies=...)`` are a private detail that has
    changed shape between releases, and — more importantly — what matters is what the
    application *refuses*, not how it is wired to refuse it.

    ``ExplodingDatabase`` makes the distinction crisp. A refused request never reaches a
    session and answers 403. An allowed one opens a session and fails; with
    ``raise_app_exceptions=False`` that surfaces as a 500, which is a perfectly good signal
    that authorization let it through.
    """

    @pytest.fixture
    async def permissive(self, auth_settings: Settings) -> AsyncIterator[AsyncClient]:
        app = create_app(auth_settings)
        app.state.database = ExplodingDatabase()
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            yield client

    def test_the_table_covers_every_route(self, auth_settings: Settings) -> None:
        """The audit is only exhaustive if the table is. A route added without an entry
        fails here rather than being silently omitted from every assertion below."""
        app = create_app(auth_settings)
        served = {(method, template) for method, template, _ in concrete_paths(app)}
        listed = set(ROUTE_CAPABILITIES)

        assert served == listed, (
            f"Routes served but not audited: {sorted(served - listed)}. "
            f"Routes audited but not served: {sorted(listed - served)}. "
            "Add an entry to ROUTE_CAPABILITIES naming the capability the route requires."
        )

    def test_the_public_routes_are_the_ones_marked_public(self) -> None:
        public = {template for (_, template), cap in ROUTE_CAPABILITIES.items() if cap is None}

        assert public == set(PUBLIC_PATHS)

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.AUDITOR, Role.ADMIN])
    async def test_each_role_reaches_exactly_what_its_capabilities_allow(
        self, permissive: AsyncClient, auth_settings: Settings, role: Role
    ) -> None:
        held = {capability.value for capability in capabilities_for(frozenset({role}))}
        app = create_app(auth_settings)
        token = token_for_roles(auth_settings, role)
        wrong: list[str] = []

        for method, template, concrete in concrete_paths(app):
            required = ROUTE_CAPABILITIES[(method, template)]
            if required is None or required == AUTHENTICATED:
                continue
            expected_allowed = (
                Capability.COLLECTORS_INGEST.value in held
                if required == INGEST
                else required in held
            )
            response = await permissive.request(
                method,
                concrete,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": "finance", "principal": "S-1-5-21-1-2-3-1000", "resource": "x"},
                json={} if method == "POST" else None,
            )
            was_allowed = response.status_code != 403
            if was_allowed != expected_allowed:
                wrong.append(
                    f"{method} {template}: {role.value} "
                    f"{'reached' if was_allowed else 'was refused'} a route requiring "
                    f"{required!r} (status {response.status_code})"
                )

        assert not wrong, "\n  ".join(["Capability enforcement disagrees with the table:", *wrong])

    @pytest.mark.parametrize("capability", AUDITABLE_CAPABILITIES)
    async def test_one_capability_alone_reaches_exactly_its_own_routes(
        self,
        permissive: AsyncClient,
        auth_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
        capability: Capability,
    ) -> None:
        """The audit the role-by-role sweep above **cannot** perform.

        Every real role holds ``identities:read`` and ``access:read`` together, so a route
        that required the wrong one of the two is reachable by exactly the same accounts and
        no test over real roles can tell the difference. Proved: swapping the two on
        ``/api/v1/groups`` leaves the sweep above entirely green.

        So this mints a principal holding **one** capability, by narrowing the role table
        for the duration of the test, and asserts that it reaches precisely the routes the
        table says need that capability — and is refused by every other one. That is what
        pins ``resource-impact`` to ``access:read`` rather than to the ``identities:read``
        that the rest of ``/api/v1/groups`` carries: knowing who is in a group and knowing
        what that group reaches are different disclosures, and the more sensitive one must
        not inherit the weaker requirement.
        """
        monkeypatch.setitem(ROLE_CAPABILITIES, Role.VIEWER, frozenset({capability}))
        app = create_app(auth_settings)
        token = token_for_roles(auth_settings, Role.VIEWER)
        wrong: list[str] = []

        for method, template, concrete in concrete_paths(app):
            required = ROUTE_CAPABILITIES[(method, template)]
            if required is None or required == AUTHENTICATED:
                continue
            expected_allowed = (
                capability is Capability.COLLECTORS_INGEST
                if required == INGEST
                else required == capability.value
            )
            response = await permissive.request(
                method,
                concrete,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": "finance", "principal": "S-1-5-21-1-2-3-1000", "resource": "x"},
                json={} if method == "POST" else None,
            )
            was_allowed = response.status_code != 403
            if was_allowed != expected_allowed:
                wrong.append(
                    f"{method} {template} requires {required!r}: a principal holding only "
                    f"{capability.value!r} {'reached' if was_allowed else 'was refused'} it "
                    f"(status {response.status_code})"
                )

        assert not wrong, "\n  ".join(
            [f"Holding only {capability.value!r} reaches the wrong routes:", *wrong]
        )

    async def test_an_account_with_no_role_reaches_nothing_but_the_public_routes(
        self, permissive: AsyncClient, auth_settings: Settings
    ) -> None:
        app = create_app(auth_settings)
        token = token_for_roles(auth_settings)
        reached: list[str] = []

        for method, template, concrete in concrete_paths(app):
            if ROUTE_CAPABILITIES[(method, template)] in (None, AUTHENTICATED):
                continue
            response = await permissive.request(
                method,
                concrete,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": "finance"},
                json={} if method == "POST" else None,
            )
            if response.status_code != 403:
                reached.append(f"{method} {template} -> {response.status_code}")

        assert not reached, "An account with no ADG role reached: " + ", ".join(reached)


class TestWhatCountsAsACredential:
    async def test_a_garbled_token_is_rejected_rather_than_treated_as_absent(
        self, anonymous: AsyncClient
    ) -> None:
        """Otherwise a client could downgrade itself to anonymous by corrupting its token
        and get a public answer instead of an error."""
        response = await anonymous.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})

        assert response.status_code == 401

    async def test_a_token_without_the_bearer_scheme_is_no_token(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        token = development_token(auth_settings, "admin")
        response = await anonymous.get("/auth/me", headers={"Authorization": token})

        assert response.status_code == 401

    async def test_the_scheme_is_matched_case_insensitively(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        """RFC 7235 says the scheme is case-insensitive, and clients take it at its word."""
        token = development_token(auth_settings, "admin")
        response = await anonymous.get("/auth/me", headers={"Authorization": f"bEaReR {token}"})

        assert response.status_code == 200

    async def test_a_token_signed_by_another_process_is_rejected(
        self, anonymous: AsyncClient
    ) -> None:
        other = build_settings(
            environment="test",
            dev_auth_secret="a-different-process-with-its-own-long-secret",
        )
        response = await anonymous.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {development_token(other, 'admin')}"},
        )

        assert response.status_code == 401


class TestWhoMayDoWhat:
    async def test_an_authenticated_account_with_no_role_is_told_so(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        """403, not 401: the token is fine, so telling the client to refresh it would send
        it into a loop that can never succeed."""
        token = token_for_roles(auth_settings)
        response = await anonymous.get(
            "/api/v1/servers", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 403
        assert "no ADG role assigned" in response.json()["detail"]

    async def test_an_account_holding_only_the_reserved_role_is_told_it_is_reserved(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        token = token_for_roles(auth_settings, Role.REMEDIATOR)
        response = await anonymous.get(
            "/api/v1/servers", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert "reserved role" in detail
        assert "remediator" in detail

    async def test_a_viewer_may_not_ingest_observations(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        response = await anonymous.post(
            "/api/v1/scan-runs",
            headers=auth_headers(auth_settings, "viewer"),
            json={},
        )

        assert response.status_code == 403
        assert Capability.COLLECTORS_INGEST.value in response.json()["detail"]

    async def test_the_denial_names_the_capability_and_the_roles_held(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        response = await anonymous.post(
            "/api/v1/scan-runs", headers=auth_headers(auth_settings, "auditor"), json={}
        )

        detail = response.json()["detail"]
        assert "collectors:ingest" in detail
        assert "auditor" in detail


class TestCollectorKeys:
    KEY = "a-collector-key-long-enough-to-be-accepted"

    @pytest.fixture
    def keyed_settings(self) -> Settings:
        return build_settings(
            environment="test",
            log_format="text",
            dev_auth_secret="a-fixed-secret-for-these-tests-only",
            collector_api_keys=f"fs01:{self.KEY}",
        )

    @pytest.fixture
    async def keyed(self, keyed_settings: Settings) -> AsyncIterator[AsyncClient]:
        app = create_app(keyed_settings)
        app.state.database = ExplodingDatabase()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            yield c

    async def test_a_wrong_key_is_rejected_outright(self, keyed: AsyncClient) -> None:
        """Rejected rather than falling through to the bearer check, which would turn a key
        typo into a confusing complaint about a missing token."""
        response = await keyed.post(
            "/api/v1/scan-runs",
            headers={"X-ADG-Collector-Key": "wrong"},
            json={},
        )

        assert response.status_code == 401
        assert "not a configured collector key" in response.json()["detail"]

    async def test_a_collector_key_cannot_read_the_estate(self, keyed: AsyncClient) -> None:
        """The header is simply not a credential for reading; the request is unauthenticated."""
        response = await keyed.get("/api/v1/servers", headers={"X-ADG-Collector-Key": self.KEY})

        assert response.status_code == 401

    async def test_the_message_says_when_no_keys_are_configured(
        self, anonymous: AsyncClient
    ) -> None:
        response = await anonymous.post("/api/v1/scan-runs", json={})

        assert response.status_code == 401
        assert "ADG_COLLECTOR_API_KEYS is empty" in response.json()["detail"]


class TestTheDevelopmentLoginRoute:
    async def test_it_exists_in_development_mode(self, anonymous: AsyncClient) -> None:
        response = await anonymous.post("/auth/dev/login", json={"username": "admin"})

        assert response.status_code == 200
        assert response.json()["principal"]["roles"] == ["admin"]
        assert response.json()["principal"]["development"] is True

    async def test_the_token_it_issues_actually_works(self, anonymous: AsyncClient) -> None:
        token = (await anonymous.post("/auth/dev/login", json={"username": "auditor"})).json()[
            "access_token"
        ]

        me = await anonymous.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert me.status_code == 200
        assert me.json()["roles"] == ["auditor"]
        assert Capability.SETTINGS_READ.value in me.json()["capabilities"]

    async def test_an_unknown_account_is_refused_with_the_list(
        self, anonymous: AsyncClient
    ) -> None:
        response = await anonymous.post("/auth/dev/login", json={"username": "root"})

        assert response.status_code == 404
        assert "viewer, auditor, admin" in response.json()["detail"]

    async def test_it_does_not_exist_in_a_tenant_deployment(self) -> None:
        """Absent, not forbidden. A 403 would still advertise that this deployment knows
        how to mint its own identities."""
        settings = build_settings(
            environment="test",
            log_format="text",
            auth_mode="oidc",
            oidc_issuer="https://login.microsoftonline.com/tenant/v2.0",
            oidc_audience="api://adg",
            oidc_jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        )
        app = create_app(settings)

        assert "/auth/dev/login" not in app.openapi()["paths"]

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post("/auth/dev/login", json={"username": "admin"})

        assert response.status_code == 404


class TestTheAuthConfigEndpoint:
    async def test_it_announces_development_mode_unmistakably(self, anonymous: AsyncClient) -> None:
        body = (await anonymous.get("/auth/config")).json()

        assert body["mode"] == "development"
        assert body["development"] is True
        assert body["development_accounts"] == ["viewer", "auditor", "admin"]

    async def test_it_publishes_the_role_table_so_the_ui_need_not_hard_code_it(
        self, anonymous: AsyncClient
    ) -> None:
        published = (await anonymous.get("/auth/config")).json()["roles"]
        roles = {item["role"]: item for item in published}

        assert roles["remediator"]["active"] is False
        assert roles["remediator"]["capabilities"] == []
        assert "resources:read" in roles["viewer"]["capabilities"]

    async def test_it_carries_no_secret(self, anonymous: AsyncClient) -> None:
        body = (await anonymous.get("/auth/config")).text

        assert "secret" not in body.casefold()

    async def test_a_tenant_deployment_reports_no_development_accounts(self) -> None:
        settings = build_settings(
            environment="test",
            log_format="text",
            auth_mode="oidc",
            oidc_issuer="https://login.microsoftonline.com/tenant/v2.0",
            oidc_audience="api://adg",
            oidc_jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
            oidc_client_id="11111111-1111-1111-1111-111111111111",
        )
        app = create_app(settings)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            body = (await client.get("/auth/config")).json()

        assert body["development"] is False
        assert body["development_accounts"] == []
        assert body["client_id"] == "11111111-1111-1111-1111-111111111111"


class TestWhoAmI:
    async def test_it_reports_the_capabilities_the_frontend_should_obey(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        body = (
            await anonymous.get("/auth/me", headers=auth_headers(auth_settings, "viewer"))
        ).json()

        assert body["roles"] == ["viewer"]
        assert "resources:read" in body["capabilities"]
        assert "settings:write" not in body["capabilities"]

    async def test_it_reports_an_inactive_role_rather_than_hiding_it(
        self, anonymous: AsyncClient, auth_settings: Settings
    ) -> None:
        token = token_for_roles(auth_settings, Role.VIEWER, Role.REMEDIATOR)
        body = (
            await anonymous.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        ).json()

        assert body["inactive_roles"] == ["remediator"]
        assert "remediation:execute" not in body["capabilities"]
