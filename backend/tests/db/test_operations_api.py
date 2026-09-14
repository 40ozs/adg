r"""``GET /api/v1/collection/operations`` against a real PostgreSQL.

The pure rules are in ``tests/domain/test_operations.py``. What is tested here is the part
that cannot be: the five queries behind them, and in particular that the page's cost does
**not** grow with the estate. An operator status page that issued a query per server would
be slowest on exactly the estate that needs it most, and no assertion about the response
body would ever show it.

The other half is the pairing. Three of the five queries reduce to one row per scope, and
the report joins them on ``(collector, target)`` — so a test that seeds one server proves
nothing about it. Every fixture here seeds at least three, with different outcomes.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.auth.roles import Role
from app.db import Database

pytestmark = pytest.mark.anyio


class StatementLog:
    """Every statement the engine issues while the block is open.

    A copy of the harness in ``test_query_cost.py`` rather than an import: that module is a
    test module, and importing one test file into another makes a fixture's failure land in
    a file that did not write it. The class is nine lines.
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


def run_document(
    *,
    collector: str = "ntfs",
    host: str,
    target: str,
    started_at: str,
    status: str = "succeeded",
    errors: list[dict[str, Any]] | None = None,
    reconcile: bool = True,
    incremental: bool = False,
) -> dict[str, Any]:
    """A run that reports no observations. Operations is about runs, not about the estate."""
    run_id = str(uuid.uuid4())
    error_list = errors or []
    scope = {"kind": "server", "key": target.casefold()}
    return {
        "run_id": run_id,
        "start": {
            "schema_version": "1.3",
            "run_id": run_id,
            "source": {
                "collector": collector,
                "collector_host": host,
                "method": "test",
                "collector_version": "0.1.0",
                "target": target,
            },
            "started_at": started_at,
            "scopes": [scope],
            "incremental": incremental,
        },
        "completion": {
            "schema_version": "1.3",
            "run_id": run_id,
            "status": status,
            "completed_at": started_at,
            "batch_count": 0,
            "observation_count": 0,
            "error_count": len(error_list),
            "errors": error_list,
            "reconciled_scopes": (
                [scope] if reconcile and status == "succeeded" and not error_list else []
            ),
        },
    }


async def record(client: AsyncClient, document: dict[str, Any]) -> str:
    started = await client.post("/api/v1/scan-runs", json=document["start"])
    assert started.status_code in (200, 201), started.text
    completed = await client.post(
        f"/api/v1/scan-runs/{document['run_id']}/completion", json=document["completion"]
    )
    assert completed.status_code == 200, completed.text
    return str(document["run_id"])


DENIED = [
    {
        "code": "access_denied",
        "message": "Access denied reading the security descriptor.",
        "target": r"\\FS02\Projects\Restricted",
    }
]


@pytest.fixture
async def three_servers(client: AsyncClient) -> None:
    """FS01 clean, FS02 partial with an error, FS03 failed and never successful."""
    await record(
        client, run_document(host="FS01", target="FS01", started_at="2026-09-14T08:00:00Z")
    )
    await record(
        client,
        run_document(
            host="FS02",
            target="FS02",
            started_at="2026-09-14T08:10:00Z",
            status="partial",
            errors=DENIED,
        ),
    )
    await record(
        client,
        run_document(
            host="FS03",
            target="FS03",
            started_at="2026-09-14T08:20:00Z",
            status="failed",
            errors=[
                {
                    "code": "host_unreachable",
                    "message": "The server did not respond.",
                    "target": r"\\FS03",
                }
            ],
        ),
    )


class TestTheEmptyState:
    async def test_an_untouched_database_reports_no_data_rather_than_health(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/collection/operations")).json()

        assert body["health"] == "no_data"
        assert body["scopes"] == []
        assert body["errors"] == []
        assert body["counts"]["total"] == 0

    async def test_the_counts_are_present_even_when_everything_is_zero(
        self, client: AsyncClient
    ) -> None:
        """The panel has to render. An absent counts object would make the page branch on
        emptiness in a second place, and the two branches would word it differently."""
        counts = (await client.get("/api/v1/collection/operations")).json()["counts"]

        assert counts["principals"] == 0
        assert counts["scan_runs"] == 0


class TestScopesAndTheirOutcomes:
    async def test_each_server_is_its_own_scope(
        self, client: AsyncClient, three_servers: None
    ) -> None:
        body = (await client.get("/api/v1/collection/operations")).json()

        assert [scope["target"] for scope in body["scopes"]] == ["FS01", "FS02", "FS03"]
        assert [scope["completeness"] for scope in body["scopes"]] == [
            "complete",
            "partial",
            "none",
        ]

    async def test_a_scope_that_has_never_succeeded_is_named(
        self, client: AsyncClient, three_servers: None
    ) -> None:
        body = (await client.get("/api/v1/collection/operations")).json()
        fs03 = next(scope for scope in body["scopes"] if scope["target"] == "FS03")

        assert fs03["has_ever_succeeded"] is False
        assert fs03["last_success"] is None
        assert fs03["last_failure"]["status"] == "failed"

    async def test_a_failure_after_a_success_keeps_both(self, client: AsyncClient) -> None:
        """The state a status page most often gets wrong. Yesterday's data is still on
        screen; the page has to say both that it is there and that it is stale."""
        success = await record(
            client, run_document(host="FS01", target="FS01", started_at="2026-09-14T08:00:00Z")
        )
        failure = await record(
            client,
            run_document(
                host="FS01",
                target="FS01",
                started_at="2026-09-14T09:00:00Z",
                status="failed",
                errors=DENIED,
            ),
        )

        body = (await client.get("/api/v1/collection/operations")).json()
        scope = body["scopes"][0]

        assert scope["latest"]["run_id"] == failure
        assert scope["last_success"]["run_id"] == success
        assert scope["last_failure"]["run_id"] == failure
        assert scope["stale_success"] is True
        assert "has not been refreshed since" in scope["note"]

    async def test_a_partial_run_counts_as_the_last_success(self, client: AsyncClient) -> None:
        """It collected real observations and its data is on screen. Calling it "not a
        success" would tell an operator the scope has never been read."""
        partial = await record(
            client,
            run_document(
                host="FS02",
                target="FS02",
                started_at="2026-09-14T08:00:00Z",
                status="partial",
                errors=DENIED,
            ),
        )

        body = (await client.get("/api/v1/collection/operations")).json()
        scope = body["scopes"][0]

        assert scope["has_ever_succeeded"] is True
        assert scope["last_success"]["run_id"] == partial
        assert scope["completeness"] == "partial"

    async def test_a_run_with_no_target_is_still_one_scope(self, client: AsyncClient) -> None:
        """``target`` is nullable and SQL does not group NULLs. Coalescing it is what keeps
        two untargeted runs of one collector from becoming two scopes."""
        for started in ("2026-09-14T08:00:00Z", "2026-09-14T09:00:00Z"):
            document = run_document(host="DC01", target="x", started_at=started)
            del document["start"]["source"]["target"]
            await record(client, document)

        body = (await client.get("/api/v1/collection/operations")).json()

        assert len(body["scopes"]) == 1
        assert body["scopes"][0]["target"] is None
        assert body["scopes"][0]["label"] == "ntfs"

    async def test_an_unreconciled_scope_makes_a_success_incomplete(
        self, client: AsyncClient
    ) -> None:
        await record(
            client,
            run_document(
                host="FS01", target="FS01", started_at="2026-09-14T08:00:00Z", reconcile=False
            ),
        )

        body = (await client.get("/api/v1/collection/operations")).json()
        scope = body["scopes"][0]

        assert scope["completeness"] == "partial"
        assert "not fully enumerated" in scope["latest"]["shortfall"]

    async def test_an_incremental_run_is_not_marked_incomplete_for_not_reconciling(
        self, client: AsyncClient
    ) -> None:
        await record(
            client,
            run_document(
                host="FS01",
                target="FS01",
                started_at="2026-09-14T08:00:00Z",
                reconcile=False,
                incremental=True,
            ),
        )

        body = (await client.get("/api/v1/collection/operations")).json()

        assert body["scopes"][0]["completeness"] == "complete"
        assert body["scopes"][0]["latest"]["shortfall"] is None


class TestTheErrorSummary:
    async def test_errors_are_grouped_by_code_across_runs(
        self, client: AsyncClient, three_servers: None
    ) -> None:
        body = (await client.get("/api/v1/collection/operations")).json()
        codes = {group["code"]: group for group in body["errors"]}

        assert set(codes) == {"access_denied", "host_unreachable"}
        assert body["total_errors"] == 2

    async def test_the_same_code_on_many_targets_is_one_row_with_examples(
        self, client: AsyncClient
    ) -> None:
        await record(
            client,
            run_document(
                host="FS01",
                target="FS01",
                started_at="2026-09-14T08:00:00Z",
                status="partial",
                errors=[
                    {
                        "code": "access_denied",
                        "message": "denied",
                        "target": rf"\\FS01\Share{index}",
                    }
                    for index in range(10)
                ],
            ),
        )

        body = (await client.get("/api/v1/collection/operations")).json()
        group = body["errors"][0]

        assert group["code"] == "access_denied"
        assert group["count"] == 10
        assert len(group["sample_targets"]) == 3, "the examples are bounded, not the list"

    async def test_a_code_reported_by_two_collectors_is_marked_widespread(
        self, client: AsyncClient
    ) -> None:
        for collector, host in (("ntfs", "FS01"), ("smb", "FS02")):
            await record(
                client,
                run_document(
                    collector=collector,
                    host=host,
                    target=host,
                    started_at="2026-09-14T08:00:00Z",
                    status="partial",
                    errors=[{"code": "access_denied", "message": "denied", "target": host}],
                ),
            )

        body = (await client.get("/api/v1/collection/operations")).json()
        group = body["errors"][0]

        assert group["is_widespread"] is True
        assert group["collectors"] == ["ntfs", "smb"]


class TestTheVerdictIsTheSameOneEveryPageSees:
    async def test_health_matches_the_coverage_endpoint(
        self, client: AsyncClient, three_servers: None
    ) -> None:
        """Two screens disagreeing about whether the estate is trustworthy would be worse
        than either being wrong. Both fold the same latest runs through the same function."""
        status = (await client.get("/api/v1/collection/status")).json()
        operations = (await client.get("/api/v1/collection/operations")).json()

        assert operations["health"] == status["health"] == "failed"
        assert operations["summary"] == status["summary"]


class TestWhatItCosts:
    async def test_the_page_costs_the_same_on_one_server_and_on_twelve(
        self, client: AsyncClient, statements: StatementLog
    ) -> None:
        """The property an operator page must have. A query per scope would make this the
        slowest page in the product on the estate that most needs it."""
        await record(
            client, run_document(host="FS01", target="FS01", started_at="2026-09-14T08:00:00Z")
        )
        with statements:
            await client.get("/api/v1/collection/operations")
        one_server = statements.count

        for index in range(2, 14):
            await record(
                client,
                run_document(
                    host=f"FS{index:02d}",
                    target=f"FS{index:02d}",
                    started_at=f"2026-09-14T08:{index:02d}:00Z",
                    status="partial" if index % 3 else "failed",
                    errors=DENIED,
                ),
            )
        with statements:
            response = await client.get("/api/v1/collection/operations")
        twelve_servers = statements.count

        assert len(response.json()["scopes"]) == 13
        assert twelve_servers == one_server, (
            f"The operations page issued {one_server} statement(s) for one scope and "
            f"{twelve_servers} for thirteen. Its cost must not grow with the estate."
        )

    async def test_it_reads_the_run_table_a_fixed_number_of_times(
        self, client: AsyncClient, three_servers: None, statements: StatementLog
    ) -> None:
        """Five, and each one is named.

        Three reductions over the run table — latest, last success, last failure — plus the
        row count in the object-counts statement, plus the join in the error summary. Five
        is the budget for *any* estate; a sixth means a layer started fetching per scope,
        which is the one failure this page must not have.
        """
        with statements:
            await client.get("/api/v1/collection/operations")

        assert statements.reading("scan_runs") == 5, (
            "Expected exactly five reads of scan_runs (latest per scope, last success, "
            "last failure, the row count, and the error summary's join); saw "
            f"{statements.reading('scan_runs')}."
        )
        assert statements.count == 5, (
            f"The whole page should cost five statements; it cost {statements.count}: "
            + " | ".join(item[:80] for item in statements.statements)
        )

    async def test_the_error_summary_is_one_statement_however_many_codes(
        self, client: AsyncClient, statements: StatementLog
    ) -> None:
        """The sample targets come from the same aggregate. A second query per code is the
        N+1 that would fire on exactly the estate with many codes."""
        await record(
            client,
            run_document(
                host="FS01",
                target="FS01",
                started_at="2026-09-14T08:00:00Z",
                status="partial",
                errors=[
                    {"code": f"code_{index}", "message": "m", "target": f"t{index}"}
                    for index in range(12)
                ],
            ),
        )

        with statements:
            response = await client.get("/api/v1/collection/operations")

        assert len(response.json()["errors"]) == 12
        assert statements.reading("scan_run_errors") == 1


class TestWhoMayReadIt:
    async def test_a_viewer_may_read_it(self, client_as: Any, three_servers: None) -> None:
        """Same capability as the coverage banner. Somebody looking at an empty page has to
        be able to find out whether anything ran."""
        async with client_as(Role.VIEWER) as viewer:
            response = await viewer.get("/api/v1/collection/operations")

        assert response.status_code == 200

    async def test_an_account_with_no_role_may_not(self, client_as: Any) -> None:
        async with client_as() as nobody:
            response = await nobody.get("/api/v1/collection/operations")

        assert response.status_code == 403

    async def test_it_refuses_an_unauthenticated_request(
        self, anonymous_client: AsyncClient
    ) -> None:
        response = await anonymous_client.get("/api/v1/collection/operations")

        assert response.status_code == 401
