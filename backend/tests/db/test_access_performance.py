r"""What the three access questions cost as an estate grows.

`test_query_cost.py` pins the statement count of each access endpoint against one fixed
estate. That catches an N+1 within a request. It cannot catch the other shape of the same
defect — a request whose cost is fine on a test fixture and grows with the *estate*, which is
the one that only appears in production, on the largest customer, months after release.

So each of the three shapes the phase names is measured twice, against a small estate and a
large one built from the same generator, and the assertion is that the two counts are
**equal**. Not "below a threshold": equal. A cost that does not move when the estate grows
tenfold is bounded, and it is bounded for a reason a reader can check, whereas a threshold is
a number somebody picked.

The three shapes:

* **one principal against one resource** — the detail answer, and the one a UI issues most;
* **every principal on one share** — the trustee inversion, whose cost must follow the ACL
  rather than the size of the domain;
* **every share for one principal** — the reverse, whose cost must follow the principal's
  memberships rather than the number of shares.

Counted, never timed, for the reasons `test_query_cost.py` sets out at length. Wall-clock
numbers for these same shapes are in `docs/architecture/effective-access-performance.md`,
measured by `tests/benchmarks/access_benchmark.py`, which records the machine alongside them.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.db import Database
from app.domain import MAX_DEPTH_CEILING, MAX_NODES_CEILING
from tests.support.access_estate import SHARE_UNC, SUBJECT, load

pytestmark = pytest.mark.anyio

SMALL = 4
LARGE = 40
"""Estate sizes, ten times apart.

Ten is enough that a per-row cost is unmissable — a linear endpoint would issue forty
statements where a bounded one issues the same handful — and small enough that building both
estates keeps the smoke suite quick.
"""


def encoded(path: str) -> str:
    return quote(path, safe="")


class StatementLog:
    """Every statement one engine issues while the block is open.

    Deliberately a copy of the helper in `test_query_cost.py` rather than an import: that
    module is about one estate and this one is about two, and a shared helper across the two
    would couple a change in either to the other. The duplication is nine lines and it keeps
    each file readable on its own.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine.sync_engine if hasattr(engine, "sync_engine") else engine
        self.statements: list[str] = []

    def _record(self, conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        self.statements.append(" ".join(statement.split()))

    def __enter__(self) -> StatementLog:
        self.statements = []
        sa.event.listen(self._engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        sa.event.remove(self._engine, "before_cursor_execute", self._record)

    @property
    def count(self) -> int:
        return len(self.statements)

    def reading(self, table: str) -> int:
        pattern = re.compile(rf"\b(?:FROM|JOIN)\s+{re.escape(table)}\b", re.IGNORECASE)
        return sum(1 for statement in self.statements if pattern.search(statement))


@pytest.fixture
def statements(database: Database) -> StatementLog:
    return StatementLog(database.engine)


@pytest.fixture
async def small_estate(client: AsyncClient) -> int:
    await load(client, SMALL)
    return SMALL


@pytest.fixture
async def large_estate(client: AsyncClient) -> int:
    await load(client, LARGE)
    return LARGE


# --------------------------------------------------------------------------------------
# The three shapes
# --------------------------------------------------------------------------------------

ONE_PAIR = f"/api/v1/access/principals/{SUBJECT}/resources/{encoded(SHARE_UNC)}"
ALL_PRINCIPALS = f"/api/v1/access/resources/{encoded(SHARE_UNC)}/principals?limit=200"
ALL_SHARES = f"/api/v1/access/principals/{SUBJECT}/shares?limit=200"

SHAPES = [
    pytest.param(ONE_PAIR, id="one-principal-one-resource"),
    pytest.param(ALL_PRINCIPALS, id="every-principal-on-one-share"),
    pytest.param(ALL_SHARES, id="every-share-for-one-principal"),
]


async def cost(client: AsyncClient, statements: StatementLog, url: str) -> tuple[int, int]:
    """The statement count and the row count for one request."""
    with statements:
        response = await client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()
    rows = len(body["items"]) if "items" in body else 1
    return statements.count, rows


class TestCostDoesNotFollowTheEstate:
    """The measurement this phase was asked to make."""

    @pytest.mark.parametrize("url", SHAPES)
    async def test_the_statement_count_is_the_same_at_ten_times_the_size(
        self, client: AsyncClient, statements: StatementLog, url: str
    ) -> None:
        await load(client, SMALL)
        small_statements, small_rows = await cost(client, statements, url)

        await load(client, LARGE)
        large_statements, large_rows = await cost(client, statements, url)

        assert small_statements == large_statements, (
            f"{url} issued {small_statements} statements against an estate of {SMALL} and "
            f"{large_statements} against one of {LARGE}: its cost follows the estate."
        )
        assert large_rows >= small_rows, "the larger estate returned a smaller answer"

    async def test_the_large_estate_really_is_larger(
        self, client: AsyncClient, large_estate: int
    ) -> None:
        """Guards every comparison above: two identical estates agree about everything."""
        principals = await client.get(ALL_PRINCIPALS)
        shares = await client.get(ALL_SHARES)
        assert len(principals.json()["items"]) > SMALL
        assert len(shares.json()["items"]) > SMALL


class TestTheAnswerIsBoundedByThePage:
    """An anchored endpoint still has to page."""

    @pytest.mark.parametrize(
        "url",
        [
            pytest.param(
                f"/api/v1/access/resources/{encoded(SHARE_UNC)}/principals", id="principals"
            ),
            pytest.param(f"/api/v1/access/principals/{SUBJECT}/shares", id="shares"),
        ],
    )
    @pytest.mark.parametrize("limit", [1, 5])
    async def test_a_page_never_exceeds_its_limit(
        self, client: AsyncClient, large_estate: int, url: str, limit: int
    ) -> None:
        response = await client.get(f"{url}?limit={limit}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= limit
        assert body["page"]["limit"] == limit

    async def test_a_partial_page_says_so_and_offers_the_next(
        self, client: AsyncClient, large_estate: int
    ) -> None:
        response = await client.get(
            f"/api/v1/access/resources/{encoded(SHARE_UNC)}/principals?limit=2"
        )
        page = response.json()["page"]
        assert page["has_more"] is True
        assert page["next_cursor"]

    async def test_paging_a_listing_costs_the_same_as_taking_it_whole(
        self, client: AsyncClient, large_estate: int, statements: StatementLog
    ) -> None:
        """Paging must slice a traversal that has happened, not re-run it per page."""
        one, _ = await cost(
            client, statements, f"/api/v1/access/resources/{encoded(SHARE_UNC)}/principals?limit=1"
        )
        many, _ = await cost(client, statements, ALL_PRINCIPALS)
        assert one == many

    async def test_an_over_large_limit_is_refused_rather_than_served(
        self, client: AsyncClient, large_estate: int
    ) -> None:
        """The cap is the framework's, so it applies before any query is planned."""
        response = await client.get(
            f"/api/v1/access/resources/{encoded(SHARE_UNC)}/principals?limit=100000"
        )
        assert response.status_code == 422


class TestTheTraversalIsClamped:
    """A caller cannot buy an unbounded walk by asking for one."""

    async def test_an_absurd_depth_is_answered_rather_than_attempted(
        self, client: AsyncClient, large_estate: int
    ) -> None:
        """A request for an unbounded walk is served from a clamped one.

        `TraversalBounds` reduces the request to `MAX_DEPTH_CEILING` / `MAX_NODES_CEILING`
        before the service sees it, so the ceilings — not the query string — decide how far
        the walk goes. What the caller is told is not the clamped number but the thing that
        depends on it: `membership_complete`, which is false whenever a limit actually cut
        the traversal short and the token is therefore a lower bound.

        Unlike the graph endpoints, these do not echo the effective limits back. That is a
        real gap and it is recorded in `docs/architecture/effective-access-limits.md`; it is
        not a safety gap, because the clamp happens either way and truncation is disclosed.
        """
        response = await client.get(f"{ONE_PAIR}?max_depth=100000000&max_nodes=100000000")
        assert response.status_code == 200, response.text
        token = response.json()["token"]
        assert token["membership_complete"] is True, (
            "this estate is one hop deep, so a clamped ceiling cannot have truncated it"
        )
        assert MAX_DEPTH_CEILING < 100_000_000 and MAX_NODES_CEILING < 100_000_000

    async def test_clamping_does_not_cost_more_statements(
        self, client: AsyncClient, large_estate: int, statements: StatementLog
    ) -> None:
        """A clamped ceiling must not turn into a walk that tries to reach it."""
        plain, _ = await cost(client, statements, ONE_PAIR)
        absurd, _ = await cost(client, statements, f"{ONE_PAIR}?max_depth=100000000")
        assert plain == absurd


class TestTheInversionFollowsTheAclNotTheDomain:
    """Why "who can reach this" is computed by expanding trustees rather than principals."""

    async def test_membership_is_read_a_bounded_number_of_times(
        self, client: AsyncClient, large_estate: int, statements: StatementLog
    ) -> None:
        with statements:
            response = await client.get(ALL_PRINCIPALS)

        assert response.status_code == 200, response.text
        listed = len(response.json()["items"])
        reads = statements.reading("membership_edges")
        assert listed >= LARGE, "the estate did not produce the principals to invert"
        assert reads <= 8, (
            f"{listed} principals cost {reads} reads of membership_edges; the inversion "
            "should cost one per breadth-first level per trustee, not one per member."
        )


class TestWhereTheCostStillFollowsTheEstate:
    r"""A defect this phase found and measured, and could not responsibly fix inside it.

    `/access/resources/{r}/principals` expands every trustee on the ACL downward, builds the
    whole set of principals the resource reaches, and only then slices out the requested page.
    The *resolution* work is correctly bounded — a twenty-five-row page runs twenty-five access
    checks whatever the estate — but the expansion that precedes it is not, and it is the part
    that dominates.

    Measured on the machine recorded in `docs/architecture/effective-access-performance.md`,
    asking for the same twenty-five rows:

    | estate | `resource -> principals` | `principal -> shares` |
    | -----: | -----------------------: | --------------------: |
    |    100 |                  13.1 ms |               12.2 ms |
    |    400 |                 149.3 ms |               12.0 ms |

    The two `principal -> ...` listings page in the database with a keyset cursor and stay flat.
    This one pages by offset over a set it has already built, which is visible in its own
    response: it is the only access listing that reports `page.total`, and it cannot know that
    number without having materialized what it counts.

    That matters on a real estate because ACLs name broad groups. An ACE naming `Domain Users`
    makes the expanded set every user in the domain, per request, on every page — anchored to
    one resource, so it is not the Cartesian product `test_access_bounds.py` rules out, but it
    is unbounded in the estate all the same.

    Phase 4B recorded the offset paging as a known limitation. What this phase adds is the
    measurement of what it costs and the evidence of where the cost sits. Fixing it means
    moving the expansion and the slice into the database, or holding a traversal across pages;
    both change the shape of `AccessService`, and neither belongs in a hardening phase whose
    remit is to leave accepted contracts alone.

    The assertions below are counted, never timed. The timings above are context; what is
    asserted is the set size the endpoint reports, which is the same number on every machine.
    """

    async def test_the_resolution_work_is_bounded_by_the_page(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First, what is *not* wrong, so the finding below is not read as larger than it is."""
        counter = _count_resolutions(monkeypatch)
        url = f"/api/v1/access/resources/{encoded(SHARE_UNC)}/principals?limit=25"

        await load(client, LARGE)
        counter.reset()
        await client.get(url)

        assert counter.calls == 25, (
            f"a 25-row page ran {counter.calls} access checks; the page should bound them"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Known defect: resource->principals expands every principal the resource reaches "
            "before slicing the page, so the work behind a fixed-size page grows with the "
            "estate. See the class docstring and docs/architecture/effective-access-limits.md."
        ),
    )
    async def test_a_fixed_page_should_not_expand_a_growing_set(self, client: AsyncClient) -> None:
        """`page.total` is the size of the set that was built to serve a twenty-five-row page."""
        url = f"/api/v1/access/resources/{encoded(SHARE_UNC)}/principals?limit=25"

        await load(client, SMALL)
        small = (await client.get(url)).json()["page"]["total"]

        await load(client, LARGE)
        large = (await client.get(url)).json()["page"]["total"]

        assert small == large, (
            f"serving 25 rows expanded {small} principals against an estate of {SMALL} and "
            f"{large} against one of {LARGE}: the page size does not bound the expansion."
        )

    async def test_the_principal_listings_do_not_share_the_defect(
        self, client: AsyncClient
    ) -> None:
        """The contrast that makes the finding actionable: two of the three already page well.

        Neither reports a total, because neither builds one: both ask the database for one page
        of rows with a keyset cursor and resolve exactly what comes back.
        """
        await load(client, LARGE)
        for url in (
            f"/api/v1/access/principals/{SUBJECT}/shares?limit=25",
            f"/api/v1/access/principals/{SUBJECT}/resources?limit=25",
        ):
            page = (await client.get(url)).json()["page"]
            assert page["total"] is None, (
                f"{url} reports a total of {page['total']}, which means it materialized the "
                "whole answer to serve one page"
            )
            assert page["has_more"] is True
            assert page["next_cursor"]


class _ResolutionCounter:
    """How many access checks one request performed."""

    def __init__(self) -> None:
        self.calls = 0

    def reset(self) -> None:
        self.calls = 0


def _count_resolutions(monkeypatch: pytest.MonkeyPatch) -> _ResolutionCounter:
    """Wrap `resolve_access` where the service looks it up, and count the calls.

    Patched on `app.services.access` rather than on `app.access_engine`: the service imported
    the name at module load, so rebinding it at the source would not be seen.
    """
    import app.services.access as access_service

    counter = _ResolutionCounter()
    # `resolve_access` is imported into the service module rather than re-exported from it,
    # so mypy will not find it on the module object. Reaching it by name is the point: this
    # is the binding the service actually calls.
    original = getattr(access_service, "resolve_access")  # noqa: B009

    def counting(*args: Any, **kwargs: Any) -> Any:
        counter.calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(access_service, "resolve_access", counting)
    return counter
