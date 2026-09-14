r"""The explanation endpoint, against a real PostgreSQL and real transcripts.

The engine suite proves the causality arithmetic over hand-built ACLs. This one proves the
**joins**: that the membership edges a collector wrote are the edges the path enumeration
walks, that the chains come back in the order the engine promises after a round trip
through the database, and that a removal target measured over stored rows says the same
thing it says in memory.

One test here is worth more than the rest put together.
``04-multiple-membership-paths`` is a canonical Phase 0B scenario whose own description
reads: *"An explanation must show every path, and removing one must not be reported as
removing access."* It carries an ``expected_paths`` list. That list was written before this
engine existed, by somebody describing the estate rather than the implementation, which
makes it the closest thing to an independent acceptance test this phase can have — so it is
read out of the fixture and compared, rather than restated here where it could be quietly
edited to match whatever the code does.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from app.db import Database
from tests.db.test_query_cost import StatementLog
from tests.fixtures import load_raw
from tests.support.ingest import replay, storable_document

pytestmark = pytest.mark.anyio

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN_SID}-1104"
FINANCE_TEAM = f"{DOMAIN_SID}-1201"
FINANCE_RW = f"{DOMAIN_SID}-1202"
DOMAIN_USERS = f"{DOMAIN_SID}-513"

FINANCE_UNC = "\\\\FS01\\Finance"

MODIFY = 0x001301BF


def paths_url(principal: str, resource: str, **query: str) -> str:
    url = (
        f"/api/v1/access/paths/principals/{quote(principal, safe='')}"
        f"/resources/{quote(resource, safe='')}"
    )
    if query:
        url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
    return url


async def seed(client: AsyncClient, name: str) -> None:
    await replay(client, storable_document(name))


def expectations(name: str) -> dict[str, Any]:
    """The scenario's own declared expectations, straight out of the fixture."""
    declared: dict[str, Any] = load_raw(name)["expectations"]
    return declared


@pytest.fixture
async def multiple_paths(client: AsyncClient) -> None:
    await seed(client, "04-multiple-membership-paths")


@pytest.fixture
async def nested_grant(client: AsyncClient) -> None:
    await seed(client, "03-nested-group-grant")


@pytest.fixture
async def cyclic(client: AsyncClient) -> None:
    await seed(client, "05-cyclic-group-graph")


@pytest.fixture
async def deny_estate(client: AsyncClient) -> None:
    await seed(client, "07-deny-candidate")


@pytest.fixture
async def share_limited(client: AsyncClient) -> None:
    await seed(client, "10-smb-more-restrictive")


class TestTheCanonicalMultiplePathsScenario:
    """Checked against the fixture's own `expected_paths`, not against a restatement."""

    async def test_every_declared_path_is_returned(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        declared = expectations("04-multiple-membership-paths")
        response = await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))

        assert response.status_code == 200, response.text
        body = response.json()
        returned = {tuple(p["key"] for p in path["chain"]) for path in body["paths"]}
        expected = {tuple(chain) for chain in declared["expected_paths"]}

        assert expected <= returned, (
            "the scenario declares these chains and the explanation did not return them: "
            f"{sorted(expected - returned)}"
        )

    async def test_it_returns_as_many_distinct_chains_as_declared(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        declared = expectations("04-multiple-membership-paths")
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        chains = {tuple(p["key"] for p in path["chain"]) for path in body["paths"]}
        assert len(chains) == declared["distinct_paths_expected"]

    async def test_no_single_removal_revokes_access(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        """The scenario's stated requirement, and the acceptance criterion of the phase."""
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        assert body["effective"]["access"] is True
        sufficient = [t for t in body["removal_targets"] if t["revokes_all_access"]]
        assert sufficient == [], (
            "Alice reaches these rights by more than one route, so no single edge removal "
            f"revokes them; these were reported as sufficient: {sufficient}"
        )

    async def test_dropping_one_of_two_chains_to_a_trustee_removes_nothing(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        memberships = [t for t in body["removal_targets"] if t["kind"] == "membership"]
        assert memberships, "the estate is built from membership edges"
        via_team = [t for t in memberships if t["target"].endswith(FINANCE_TEAM)]
        assert via_team, f"no removal target for the Finance-Team hop: {memberships}"
        assert via_team[0]["rights_removed"]["value"] == 0
        assert via_team[0]["alternate_paths"]


class TestTheChainSurvivesTheDatabase:
    async def test_a_nested_grant_reports_the_whole_chain(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        chains = [tuple(p["key"] for p in path["chain"]) for path in body["paths"]]
        assert (ALICE, FINANCE_TEAM, FINANCE_RW) in chains

    async def test_the_chain_carries_display_names_and_not_only_keys(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """An explanation rendered as a row of raw SIDs is not one anybody can act on."""
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        longest = max(body["paths"], key=lambda path: len(path["chain"]))
        assert any(node.get("display_name") for node in longest["chain"])

    async def test_the_graph_names_every_node_each_path_refers_to(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        known = {node["id"] for node in body["graph"]["nodes"]}
        referenced = {node for path in body["paths"] for node in path["nodes"]}
        assert referenced <= known, f"paths refer to nodes not in the graph: {referenced - known}"

    async def test_the_graph_names_every_edge_each_path_refers_to(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        known = {edge["id"] for edge in body["graph"]["edges"]}
        referenced = {edge for path in body["paths"] for edge in path["edges"]}
        assert referenced <= known

    async def test_two_requests_return_identical_output(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """Deterministic across a round trip, or an explanation cannot be diffed."""
        url = paths_url(ALICE, FINANCE_UNC, access_path="local")
        first = (await client.get(url)).json()
        second = (await client.get(url)).json()

        assert first == second


class TestItAgreesWithTheAnswerItExplains:
    async def test_the_effective_block_matches_the_plain_endpoint(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """One resolution, two renderings. A drift here is two engines disagreeing."""
        plain = await client.get(
            f"/api/v1/access/principals/{quote(ALICE, safe='')}"
            f"/resources/{quote(FINANCE_UNC, safe='')}?access_path=local"
        )
        explained = await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))

        assert plain.json()["effective"] == explained.json()["effective"]

    async def test_no_grant_path_claims_a_right_the_answer_does_not_report(
        self, client: AsyncClient, share_limited: None
    ) -> None:
        body = (await client.get(paths_url(ALICE, FINANCE_UNC))).json()

        final = body["effective"]["rights"]["value"]
        for path in body["paths"]:
            if path["relation"] != "grant":
                continue
            assert path["effective_rights"]["value"] & ~final == 0, path["id"]


class TestTheLayersAreDistinguished:
    async def test_a_share_limited_estate_reports_a_constrained_ntfs_path(
        self, client: AsyncClient, share_limited: None
    ) -> None:
        """The scenario where NTFS grants more than the share lets through."""
        body = (await client.get(paths_url(ALICE, FINANCE_UNC))).json()

        assert body["effective"]["limiting_layer"] == "smb_share"
        ntfs_paths = [p for p in body["paths"] if p["layer"] == "ntfs" and p["relation"] == "grant"]
        assert ntfs_paths
        assert any(p["constrained_rights"]["value"] for p in ntfs_paths), (
            "the share caps NTFS here, so some NTFS grant must report constrained rights"
        )

    async def test_local_access_reports_no_share_layer_at_all(
        self, client: AsyncClient, share_limited: None
    ) -> None:
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        assert {path["layer"] for path in body["paths"]} == {"ntfs"}
        assert all(p["constrained_rights"]["value"] == 0 for p in body["paths"])

    async def test_a_deny_is_reported_as_a_deny(
        self, client: AsyncClient, deny_estate: None
    ) -> None:
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        assert any(path["relation"] == "deny" for path in body["paths"]), body["paths"]


class TestCyclesAndBounds:
    async def test_a_cyclic_estate_terminates_and_reports_the_cycle(
        self, client: AsyncClient, cyclic: None
    ) -> None:
        response = await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))

        assert response.status_code == 200, response.text
        assert response.json()["cycles"], "the ring is a finding and must be reported"

    async def test_a_small_path_limit_truncates_and_says_so(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        body = (
            await client.get(
                paths_url(ALICE, FINANCE_UNC, access_path="local", max_causal_paths="1")
            )
        ).json()

        assert len(body["paths"]) == 1
        assert body["complete"] is False
        assert "max_paths" in body["truncation"]

    async def test_the_response_reports_the_limits_it_applied(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        body = (
            await client.get(
                paths_url(ALICE, FINANCE_UNC, access_path="local", max_causal_paths="5")
            )
        ).json()

        assert body["limits"]["max_paths"] == 5

    async def test_an_over_large_limit_is_clamped_rather_than_rejected(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(
            paths_url(ALICE, FINANCE_UNC, access_path="local", max_causal_paths="999999")
        )

        assert response.status_code == 422, (
            "the schema caps this one before any code runs, unlike the traversal limits"
        )

    async def test_an_unknown_principal_is_a_404(self, client: AsyncClient) -> None:
        response = await client.get(
            paths_url(f"{DOMAIN_SID}-4242", FINANCE_UNC, access_path="local")
        )

        assert response.status_code == 404

    async def test_a_resource_that_is_not_a_unc_path_is_a_422(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(paths_url(ALICE, "fs01|finance", access_path="local"))

        assert response.status_code == 422


class TestTheWeakerPathIsStillAPath:
    async def test_the_primary_group_grant_appears_beside_the_stronger_one(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        """The primary-group edge is the one a `member`-only collector loses entirely."""
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        trustees = {path["chain"][-1]["key"] for path in body["paths"]}
        assert DOMAIN_USERS in trustees
        assert FINANCE_RW in trustees

    async def test_the_stronger_grant_is_what_the_answer_reports(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        declared = expectations("04-multiple-membership-paths")
        body = (await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))).json()

        assert body["effective"]["ntfs"]["rights"]["value"] == declared["expected_ntfs_mask"]
        assert declared["expected_ntfs_mask"] == MODIFY


class TestExplainingCostsNoExtraQuery:
    """The claim `AccessService.explain_access` makes about itself, measured.

    Path enumeration, the causality analysis and every removal re-evaluation run over the
    membership subgraph the token traversal already read, so explaining an answer must cost
    the same statements as resolving it. If that ever stops being true it will be because
    somebody went back to the database per path or per removal target, which is exactly the
    N+1 shape ADR-0012 exists to keep out of this module.
    """

    @pytest.fixture
    def statements(self, database: Database) -> StatementLog:
        return StatementLog(database.engine)

    async def test_it_issues_the_same_statements_as_the_plain_answer(
        self, client: AsyncClient, multiple_paths: None, statements: StatementLog
    ) -> None:
        plain_url = (
            f"/api/v1/access/principals/{quote(ALICE, safe='')}"
            f"/resources/{quote(FINANCE_UNC, safe='')}?access_path=local"
        )
        with statements:
            await client.get(plain_url)
        plain = statements.count

        with statements:
            await client.get(paths_url(ALICE, FINANCE_UNC, access_path="local"))
        explained = statements.count

        assert explained == plain, (
            f"explaining issued {explained} statements against {plain} for the same answer"
        )

    async def test_measuring_more_removals_costs_no_more_statements(
        self, client: AsyncClient, multiple_paths: None, statements: StatementLog
    ) -> None:
        """Each removal re-runs the access check in process, over rows already in hand."""
        with statements:
            await client.get(
                paths_url(ALICE, FINANCE_UNC, access_path="local", max_removal_targets="1")
            )
        few = statements.count

        with statements:
            await client.get(
                paths_url(ALICE, FINANCE_UNC, access_path="local", max_removal_targets="64")
            )
        many = statements.count

        assert few == many
