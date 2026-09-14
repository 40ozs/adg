r"""The Phase 5B endpoints, against a real PostgreSQL and real transcripts.

The engine suites prove the arithmetic; this one proves the wiring. Four things can only be
checked here:

1. **The verdict distinguishes real estates.** ``denied``, ``no_grant`` and
   ``indeterminate`` are produced from stored rows rather than from hand-built ACLs, so a
   scenario whose share ACL nobody collected really does come back as "not answerable"
   rather than as "no access".
2. **The conditional GET actually short-circuits.** An ETag is only worth having if a repeat
   request returns 304 *and* a request after an ingestion does not.
3. **Paging agrees with the whole.** ``/access/paths`` sliced across pages must reassemble
   into exactly ``/explain``'s ``paths``, in the same order, with nothing dropped at a page
   boundary and nothing repeated.
4. **The published schemas describe what the server actually sends.** Every response here is
   validated against ``docs/contracts/v1/*.schema.json``, which is the only check that
   couples the contract to the implementation rather than to another description of it.

Running with ``ADG_WRITE_EXAMPLES=1`` rewrites the example payloads under
``docs/contracts/v1/examples/``. They are captures, not inventions, which is the only way an
example can be trusted to be renderable.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote, urlencode

import pytest
from httpx import AsyncClient
from jsonschema import Draft202012Validator

from app.api.caching import BASIS_HEADER, CACHE_CONTROL, CONTRACT_VERSION
from app.contracts.derived import EXAMPLE_DIR, generate
from app.db import Database
from tests.db.test_query_cost import StatementLog
from tests.fixtures import load_raw
from tests.support.ingest import AD_KINDS, NTFS_KINDS, of_kinds, replay, smb_only, storable_document


def ntfs_only(document: dict[str, Any]) -> dict[str, Any]:
    """A transcript reduced to AD and the file-system layer: no share ACL was ever read.

    The mirror of ``smb_only``, which the support module already provides. Kept here rather
    than added there because only this file needs the direction where the *share* is the
    unobserved layer.
    """
    return of_kinds(document, AD_KINDS | NTFS_KINDS)


pytestmark = pytest.mark.anyio

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN_SID}-1104"
DOMAIN_USERS = f"{DOMAIN_SID}-513"
FINANCE_TEAM = f"{DOMAIN_SID}-1201"
FINANCE_RW = f"{DOMAIN_SID}-1202"

FINANCE_UNC = "\\\\FS01\\Finance"
PAYROLL_UNC = "\\\\FS01\\Finance\\Payroll"
REPORTS_UNC = "\\\\FS01\\Finance\\Reports"

WRITE_EXAMPLES = os.getenv("ADG_WRITE_EXAMPLES") == "1"


def explain_url(principal: str = ALICE, resource: str = FINANCE_UNC, **query: str) -> str:
    return "/api/v1/access/explain?" + urlencode(
        {"principal": principal, "resource": resource, **query}
    )


def paths_url(principal: str = ALICE, resource: str = FINANCE_UNC, **query: str) -> str:
    return "/api/v1/access/paths?" + urlencode(
        {"principal": principal, "resource": resource, **query}
    )


def impact_url(identifier: str = FINANCE_TEAM, **query: str) -> str:
    url = f"/api/v1/groups/{quote(identifier, safe='')}/resource-impact"
    return f"{url}?{urlencode(query)}" if query else url


async def seed(client: AsyncClient, name: str) -> None:
    await replay(client, storable_document(name))


def validate(name: str, payload: dict[str, Any]) -> None:
    """Check a live response against its published schema, and keep it as the example."""
    Draft202012Validator(generate(name)).validate(payload)
    if WRITE_EXAMPLES:
        EXAMPLE_DIR.mkdir(parents=True, exist_ok=True)
        path = EXAMPLE_DIR / name.replace(".schema.json", ".json")
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


@pytest.fixture
async def nested_grant(client: AsyncClient) -> None:
    await seed(client, "03-nested-group-grant")


@pytest.fixture
async def multiple_paths(client: AsyncClient) -> None:
    await seed(client, "04-multiple-membership-paths")


@pytest.fixture
async def deny_estate(client: AsyncClient) -> None:
    await seed(client, "07-deny-candidate")


@pytest.fixture
async def share_limited(client: AsyncClient) -> None:
    await seed(client, "10-smb-more-restrictive")


@pytest.fixture
async def two_resources(client: AsyncClient) -> None:
    """``08-inherited-ace``: Finance and Reports below it, both reached by Finance-RW.

    The smallest estate that can page, which is why the paging tests use it rather than
    skipping on an estate with one row.
    """
    await seed(client, "08-inherited-ace")


@pytest.fixture
async def payroll_estate(client: AsyncClient) -> None:
    """Two transcripts, because one principal must reach Payroll and one must not.

    ``09-broken-inheritance`` supplies the protected directory and the local-group nesting
    that reaches it; ``01-direct-user-grant`` supplies Alice, who is collected, is granted on
    the parent, and is in no local group. Neither scenario alone contains both sides of the
    distinction, and replaying two transcripts into one estate is what a real server does
    every night anyway.
    """
    await seed(client, "09-broken-inheritance")
    await seed(client, "01-direct-user-grant")


class TestTheExplanationIsOneRenderableObject:
    """Everything a client needs, from one request, without a second derivation."""

    async def test_it_answers_and_explains_in_one_body(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(explain_url())
        assert response.status_code == 200
        body = response.json()

        assert body["schema_version"] == CONTRACT_VERSION
        assert body["verdict"]["outcome"] == "granted"
        assert body["effective"]["access"] is True
        assert body["paths"], "a granted answer with no path is a causality engine failing"
        assert body["graph"]["nodes"] and body["graph"]["edges"]
        validate("access-explanation.schema.json", body)

    async def test_it_agrees_with_the_answer_it_explains(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """The two spellings must not drift; they share one resolution and this proves it."""
        explained = (await client.get(explain_url())).json()
        answered = (
            await client.get(
                f"/api/v1/access/principals/{quote(ALICE, safe='')}"
                f"/resources/{quote(FINANCE_UNC, safe='')}"
            )
        ).json()

        assert explained["effective"] == answered["effective"]
        assert explained["token"] == answered["token"]

    async def test_it_agrees_with_the_path_addressed_spelling(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        """Phase 5A's route and Phase 5B's differ only in how they are addressed."""
        query = (await client.get(explain_url())).json()
        path = (
            await client.get(
                f"/api/v1/access/paths/principals/{quote(ALICE, safe='')}"
                f"/resources/{quote(FINANCE_UNC, safe='')}"
            )
        ).json()

        assert query["paths"] == path["paths"]
        assert query["removal_targets"] == path["removal_targets"]
        assert query["graph"] == path["graph"]

    async def test_a_backslash_path_needs_no_path_encoding(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """The reason the query spelling exists: a UNC path in a URL *path* is hostile."""
        response = await client.get(explain_url(resource="\\\\FS01\\Finance"))

        assert response.status_code == 200

    async def test_it_is_deterministic(self, client: AsyncClient, multiple_paths: None) -> None:
        first = (await client.get(explain_url())).json()
        second = (await client.get(explain_url())).json()

        assert first == second


class TestTheVerdictTellsTheThreeNegativesApart:
    """The acceptance criterion this phase turns on, checked against stored estates."""

    async def test_a_principal_nothing_names_is_no_grant(
        self, client: AsyncClient, payroll_estate: None
    ) -> None:
        """Observed, on this object's ACL nowhere, and nothing denied it.

        Payroll refuses inherited entries and names only the server's local administrators.
        Alice is a real, collected user who is not in that group, so the grant on the parent
        genuinely stops above her: no Deny, no gap in collection, simply nothing that names
        her. ``certainty: certain`` is what separates this from the case below.
        """
        body = (await client.get(explain_url(principal=ALICE, resource=PAYROLL_UNC))).json()

        assert body["verdict"]["outcome"] == "no_grant"
        assert body["verdict"]["certainty"] == "certain"
        assert body["verdict"]["conclusive"] is True
        assert body["verdict"]["denials"] == []
        assert body["effective"]["access"] is False

        # Not "no paths": the share's Everyone entry does match her, and the explanation
        # keeps it — as `constrained`, because the file system withholds all of it. That is
        # the whole reason `effect` is a separate field from `relation`, and it is the more
        # useful answer: the share would let her in, and only NTFS is stopping her.
        assert body["paths"], "the matched share entry should still be explained"
        assert all(path["effect"] != "contributes" for path in body["paths"])
        assert [path["layer"] for path in body["paths"]] == ["smb_share"]
        assert body["effective"]["limiting_layer"] == "ntfs"

    async def test_the_boundary_is_where_the_grant_stops(
        self, client: AsyncClient, payroll_estate: None
    ) -> None:
        r"""The same object, the same ACL, a different principal: granted.

        Read together with the test above, this is what makes ``no_grant`` a finding about
        Alice rather than about Payroll. ``09-broken-inheritance`` says it in its own note:
        *"Finance-RW reaches Payroll only because it was nested into the server's
        BUILTIN\Administrators, not through the parent's ACE."*
        """
        expected = _expectations("09-broken-inheritance")
        body = (
            await client.get(
                explain_url(principal=expected["subject_sid"], resource=expected["resource"])
            )
        ).json()

        assert expected["access_expected"] is True
        assert body["verdict"]["outcome"] == "granted"
        assert body["resource"]["dacl_protected"] is True

        # No ACE on Payroll names Finance-RW. Every entry that grants it anything reaches it
        # through the local group, which is exactly what the fixture's note says and what an
        # administrator has to know before trying to "remove Finance-RW from Payroll".
        contributing = [path for path in body["paths"] if path["effect"] == "contributes"]
        assert contributing
        assert all(path["ace_key"] is None or path["via_group"] for path in contributing)

        # And one path has no ACE at all: the local group owns Payroll, so it holds
        # READ_CONTROL and WRITE_DAC whatever the DACL says. Phase 5A marks that path with
        # ace_position -1 precisely so it cannot be mistaken for an entry somebody could
        # delete — it is the escalation route no ACL viewer shows.
        ownership = [path for path in body["paths"] if path["ace_position"] == -1]
        assert len(ownership) == 1
        assert ownership[0]["ace_key"] is None
        assert "WRITE_DAC" in ownership[0]["effective_rights"]["escalation_rights"]

    async def test_a_deny_is_denied_and_names_the_entry(
        self, client: AsyncClient, deny_estate: None
    ) -> None:
        """Read against the scenario's own declared expectations, not against restated ones.

        ``07-deny-candidate`` was written in Phase 0B by somebody describing an estate: a
        Deny placed ahead of an Allow, ``access_expected: false``, and the trustee the Deny
        names. Taking all three out of the fixture is what makes this an acceptance test
        rather than a restatement of whatever the code does.
        """
        expected = _expectations("07-deny-candidate")
        body = (
            await client.get(
                explain_url(principal=expected["subject_sid"], resource=expected["resource"])
            )
        ).json()

        assert expected["access_expected"] is False and expected["deny_expected"] is True
        assert body["verdict"]["outcome"] == "denied"
        assert body["verdict"]["conclusive"] is True
        assert body["verdict"]["denials"], "a denied verdict must name what denied it"
        assert all(entry["ace_type"] == "deny" for entry in body["verdict"]["denials"])
        assert expected["deny_trustee"] in {
            entry["trustee"]["sid"] for entry in body["verdict"]["denials"]
        }
        assert body["effective"]["limiting_layer"] == expected["limiting_layer"]

    async def test_an_uncollected_estate_is_indeterminate_not_no_access(
        self, client: AsyncClient
    ) -> None:
        r"""The most important assertion in this file.

        The share layer is collected and the file system is not, so no NTFS descriptor for
        this path has ever been read. There is genuinely no grant to find — and reporting
        "no access" would be a false negative about a directory nobody has looked at.
        """
        await replay(client, smb_only(storable_document("03-nested-group-grant")))

        body = (await client.get(explain_url())).json()

        assert body["verdict"]["outcome"] == "indeterminate"
        assert body["verdict"]["conclusive"] is False
        assert body["verdict"]["may_understate"] is True
        assert body["effective"]["acl_provenance"] == "unobserved"
        assert any(
            warning["condition"] == "ntfs_acl_not_observed" for warning in body["warnings"]
        ), "the verdict must be traceable to the finding that produced it"

    async def test_an_unread_share_narrows_rather_than_hides(self, client: AsyncClient) -> None:
        """An unseen restriction only takes away, so a grant found under it is still a grant."""
        await replay(client, ntfs_only(storable_document("03-nested-group-grant")))

        body = (await client.get(explain_url())).json()

        assert body["verdict"]["outcome"] == "granted"
        assert body["verdict"]["may_overstate"] is True
        assert body["verdict"]["conclusive"] is True

    async def test_an_empty_database_is_a_404_not_a_negative(self, client: AsyncClient) -> None:
        """Before any assertion about outcomes: nothing stored is not "no access"."""
        response = await client.get(explain_url())

        assert response.status_code == 404


class TestPagingAgreesWithTheWhole:
    """A page is a slice of one enumeration, not a second one."""

    async def test_a_page_validates_and_carries_its_own_subgraph(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        response = await client.get(paths_url(limit="1"))
        assert response.status_code == 200
        body = response.json()

        assert len(body["items"]) <= 1
        referenced = {node for item in body["items"] for node in item["nodes"]}
        assert referenced <= {node["id"] for node in body["graph"]["nodes"]}
        assert {node["id"] for node in body["graph"]["nodes"]} == referenced
        validate("access-paths.schema.json", body)

    async def test_the_pages_reassemble_into_the_explanation(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        whole = (await client.get(explain_url())).json()["paths"]

        collected: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            query = {"limit": "1"}
            if cursor is not None:
                query["cursor"] = cursor
            page = (await client.get(paths_url(**query))).json()
            collected.extend(page["items"])
            cursor = page["page"]["next_cursor"]
            if cursor is None:
                break

        assert collected == whole, "paging must not drop, repeat, or reorder a path"

    async def test_the_total_is_the_whole_enumeration(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        whole = (await client.get(explain_url())).json()["paths"]
        page = (await client.get(paths_url(limit="1"))).json()

        assert page["page"]["total"] == len(whole)

    async def test_a_page_carries_the_verdict_it_explains(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        """A path list read without its answer is how a constrained path reads as access."""
        page = (await client.get(paths_url(limit="1"))).json()
        whole = (await client.get(explain_url())).json()

        assert page["verdict"] == whole["verdict"]

    async def test_a_cursor_from_another_endpoint_is_refused(
        self, client: AsyncClient, two_resources: None
    ) -> None:
        """A keyset cursor read as an offset would silently restart from the first page.

        Which is the dangerous failure: the caller would receive a valid-looking second page
        that is actually the first one again, and conclude the estate is twice as large as
        it is. The cursor carries its own kind, and the wrong kind is refused.
        """
        keyset = (await client.get(impact_url(FINANCE_RW, limit="1"))).json()["page"]
        assert keyset["next_cursor"], "this fixture must page, or the test proves nothing"

        response = await client.get(paths_url(resource=REPORTS_UNC, cursor=keyset["next_cursor"]))

        assert response.status_code == 422

    async def test_paging_does_not_widen_the_enumeration(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        """`complete` is about the enumeration; `has_more` is about the page. Not the same."""
        page = (await client.get(paths_url(limit="1", max_causal_paths="1"))).json()

        assert page["complete"] is False
        assert page["page"]["total"] == 1, "the ceiling bounds the population, not the page"


class TestTheConditionalGet:
    """A 304 is a claim about the estate. These are the ways it could be a false one."""

    async def test_a_repeat_request_is_not_modified(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        first = await client.get(explain_url())
        etag = first.headers["ETag"]

        second = await client.get(explain_url(), headers={"If-None-Match": etag})

        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["ETag"] == etag
        assert second.headers["Cache-Control"] == CACHE_CONTROL

    async def test_the_basis_header_is_on_both(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        first = await client.get(explain_url())
        second = await client.get(explain_url(), headers={"If-None-Match": first.headers["ETag"]})

        assert first.headers[BASIS_HEADER] == second.headers[BASIS_HEADER]
        assert first.json()["basis"]["token"] == first.headers[BASIS_HEADER]

    async def test_collecting_again_invalidates_it(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """The whole design, end to end: an ingestion makes every cached answer revalidate."""
        etag = (await client.get(explain_url())).headers["ETag"]

        await seed(client, "04-multiple-membership-paths")

        response = await client.get(explain_url(), headers={"If-None-Match": etag})

        assert response.status_code == 200, "a 304 after an ingestion would serve stale facts"
        assert response.headers["ETag"] != etag

    async def test_a_different_question_is_not_matched(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        etag = (await client.get(explain_url())).headers["ETag"]

        response = await client.get(
            explain_url(access_path="local"), headers={"If-None-Match": etag}
        )

        assert response.status_code == 200

    async def test_the_two_routes_do_not_share_a_tag(
        self, client: AsyncClient, multiple_paths: None
    ) -> None:
        explain = (await client.get(explain_url())).headers["ETag"]

        response = await client.get(paths_url(), headers={"If-None-Match": explain})

        assert response.status_code == 200

    async def test_the_basis_is_reported_as_of_a_run(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        basis = (await client.get(explain_url())).json()["basis"]

        assert basis["runs"] >= 1
        assert basis["is_empty"] is False
        assert basis["latest_run_id"]
        assert basis["observations_applied"] > 0


class TestTheResourceImpactOfAGroup:
    async def test_it_lists_what_the_group_reaches(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(impact_url(FINANCE_TEAM))
        assert response.status_code == 200
        body = response.json()

        assert body["items"], "a group named on an ACL must reach at least that resource"
        assert all(item["explain"].startswith("/api/v1/access/explain?") for item in body["items"])
        validate("resource-impact.schema.json", body)

    async def test_the_explain_link_answers_about_the_same_pair(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """A link that answered a different question would be worse than no link."""
        row = (await client.get(impact_url(FINANCE_TEAM))).json()["items"][0]

        followed = await client.get(row["explain"])

        assert followed.status_code == 200
        assert followed.json()["resource"]["key"] == row["resource"]["key"]
        assert followed.json()["effective"]["rights"]["value"] == row["rights"]["value"]

    async def test_it_counts_who_would_be_affected(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """Rights times population is impact; rights alone is a fact about an ACL."""
        body = (await client.get(impact_url(FINANCE_TEAM))).json()

        assert body["membership"]["effective_members"] >= 1
        assert body["membership"]["complete"] is True

    async def test_a_row_reached_through_nesting_says_so(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """Where the fix goes depends on this field and on nothing else in the row.

        ``03-nested-group-grant`` is the exact shape: the ACL names Finance-RW, and
        Finance-Team is inside it. Finance-Team therefore reaches the directory and has no
        entry of its own to edit — removing "Finance-Team" from that ACL is not a thing
        anyone can do, because it is not on it.
        """
        nested = (await client.get(impact_url(FINANCE_TEAM))).json()["items"]
        named = (await client.get(impact_url(FINANCE_RW))).json()["items"]

        assert nested, "the nested group must still reach the resource"
        assert named
        assert [item["names_group"] for item in nested] == [False] * len(nested)
        assert [item["names_group"] for item in named] == [True] * len(named)

    async def test_every_candidate_is_listed_including_the_useless_ones(
        self, client: AsyncClient, share_limited: None
    ) -> None:
        """A grant that confers nothing today is the row an auditor most needs to see."""
        body = (await client.get(impact_url(FINANCE_RW))).json()

        assert body["items"]
        assert all("verdict" in item for item in body["items"])
        # The share ACL grants Read where NTFS grants Full Control, so this estate's rows
        # are the interesting kind: a real grant that the other layer is holding back.
        assert any(item["limiting_layer"] == "smb_share" for item in body["items"])

    async def test_it_is_paged_and_the_pages_do_not_overlap(
        self, client: AsyncClient, two_resources: None
    ) -> None:
        """Keyset paging, so a resource added between pages cannot shift the window."""
        first = (await client.get(impact_url(FINANCE_RW, limit="1"))).json()
        assert first["page"]["has_more"] and first["page"]["next_cursor"]

        second = (
            await client.get(impact_url(FINANCE_RW, limit="1", cursor=first["page"]["next_cursor"]))
        ).json()

        assert len(first["items"]) == len(second["items"]) == 1
        assert first["items"][0]["resource"]["key"] != second["items"][0]["resource"]["key"]
        assert second["page"]["has_more"] is False

    async def test_shares_with_local_access_is_refused(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """Asking which shares a principal reaches locally answers nothing."""
        response = await client.get(impact_url(FINANCE_TEAM, shares="true", access_path="local"))

        assert response.status_code == 422

    async def test_a_revalidation_is_not_modified(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        etag = (await client.get(impact_url(FINANCE_TEAM))).headers["ETag"]

        response = await client.get(impact_url(FINANCE_TEAM), headers={"If-None-Match": etag})

        assert response.status_code == 304


class TestWhatTheCachingActuallyCosts:
    """Two claims this phase makes about statements, measured rather than asserted.

    The whole argument for a conditional GET is that revalidating is cheap. If the 304 path
    turned out to run the access check and throw the body away, the ETag would be costing a
    full explanation to save a serialization — which is the opposite of the point, and it is
    exactly what a later refactor that moves the validator below the work would produce.
    """

    @pytest.fixture
    def statements(self, database: Database) -> StatementLog:
        return StatementLog(database.engine)

    async def test_a_304_costs_one_query(
        self, client: AsyncClient, multiple_paths: None, statements: StatementLog
    ) -> None:
        etag = (await client.get(explain_url())).headers["ETag"]

        with statements:
            response = await client.get(explain_url(), headers={"If-None-Match": etag})

        assert response.status_code == 304
        assert statements.count == 1, (
            f"revalidating issued {statements.count} statements: {statements.statements}"
        )
        assert statements.reading("scan_runs") == 1

    async def test_the_validator_costs_exactly_one_more_than_the_answer(
        self, client: AsyncClient, multiple_paths: None, statements: StatementLog
    ) -> None:
        """The basis is one aggregate on top of the resolution, and nothing else.

        Phase 5A measured that explaining an answer costs what answering costs. This adds
        the only statement this phase introduces, and pins it at one — a basis read that
        grew with the estate, or ran per row, would be a cache key more expensive than the
        thing it caches.
        """
        path_addressed = (
            f"/api/v1/access/paths/principals/{quote(ALICE, safe='')}"
            f"/resources/{quote(FINANCE_UNC, safe='')}"
        )
        with statements:
            await client.get(path_addressed)
        without_basis = statements.count

        with statements:
            await client.get(explain_url())
        with_basis = statements.count

        assert with_basis == without_basis + 1


class TestItFailsTheWayTheOtherRoutesFail:
    """One resolver, so one 404 and one 409, whichever spelling the caller used."""

    async def test_an_unknown_principal_is_404(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        response = await client.get(explain_url(principal=f"{DOMAIN_SID}-9999"))

        assert response.status_code == 404

    async def test_a_share_key_as_a_resource_is_422(
        self, client: AsyncClient, nested_grant: None
    ) -> None:
        """A share and the directory it publishes have different ACLs."""
        response = await client.get(explain_url(resource="fs01|finance"))

        assert response.status_code == 422

    async def test_a_missing_principal_is_422(self, client: AsyncClient) -> None:
        """The parameter is required, which is what makes the route anchored."""
        response = await client.get("/api/v1/access/explain?resource=" + quote(FINANCE_UNC))

        assert response.status_code == 422

    async def test_a_missing_resource_is_422(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/access/explain?principal=" + quote(ALICE))

        assert response.status_code == 422


def _expectations(name: str) -> dict[str, Any]:
    """A scenario's own declared expectations, straight out of the fixture."""
    declared: dict[str, Any] = load_raw(name)["expectations"]
    return declared
