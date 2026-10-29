r"""A point-in-time comparison is bounded, and says how it ended (ADG-DEMO-03).

What was measured against the Genesee Press estate, and is pinned here on small estates:

* **The two sides were cut at different keys.** Each side was read up to the per-side
  ceiling independently, so when the later instant held more objects early in key order its
  cut fell earlier: every object between the two cuts was counted as *unobserved at the
  later instant* -- 102 of them in the g1-to-g3 comparison, every one of them observed. In
  the other direction the same misalignment reports objects past the earlier cut as
  additions. Both sides are now cut at the same key.
* **Nothing bounded its time.** A slow comparison ran until the web tier's own timeout
  abandoned it, and the user read that as an outage. It now stops inside its window with a
  typed outcome, presents no list it did not finish, and offers the comparisons that can
  finish.

Every outcome is reached deterministically: the per-side ceiling by object counts, the
window by a clock the test moves, cancellation by a caller that says it has gone.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.changes.service as comparison_module
from app.changes import (
    ChangeAction,
    ChangeService,
    ChangeSignificance,
    ComparisonBudget,
    ComparisonOutcome,
    ComparisonPhase,
)
from app.contracts.v1.common import ObservationKind
from app.domain import SharePermission
from tests.support import history as h
from tests.support.ingest import replay

pytestmark = pytest.mark.anyio

FS01 = "FS01"
FINANCE_PATH = "\\\\fs01\\finance"
GROUP = f"{h.DOMAIN_SID}-1101"
ALICE = f"{h.DOMAIN_SID}-1104"
FULL_CONTROL = 0x1F01FF
READ_EXECUTE = 0x1200A9
ALL_SIGNIFICANCE = frozenset(ChangeSignificance)
SHARES = frozenset({ObservationKind.SMB_SHARE})
FROM = h.MONDAY + dt.timedelta(minutes=1)
TO = h.NEXT_MONDAY


async def smb_day(client: AsyncClient, moment: dt.datetime, shares: list[str]) -> None:
    await replay(
        client,
        h.smb_scan(
            observations=[
                h.server(FS01, at=moment),
                *[h.share(FS01, name, at=moment) for name in shares],
                *[
                    h.share_ace(FS01, name, GROUP, at=moment, permission=SharePermission.FULL)
                    for name in shares
                ],
            ],
            started_at=moment,
        ),
    )


async def ntfs_day(
    client: AsyncClient, moment: dt.datetime, entries: list[tuple[str, int, int]]
) -> None:
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE_PATH,
                    at=moment,
                    server_name=FS01,
                    share_name="Finance",
                    ace_count=len(entries),
                ),
                *[
                    h.ntfs_ace(
                        FINANCE_PATH, trustee, at=moment, access_mask=mask, order_index=index
                    )
                    for trustee, mask, index in entries
                ],
            ],
            started_at=moment,
        ),
    )


@pytest.fixture
async def shares_added_early_in_key_order(client: AsyncClient) -> None:
    """Monday: shares M..P. Friday: two new shares, A and B, that sort before all of them.

    With a per-side ceiling of three, Monday is read through ``fs01|o`` and Friday only
    through ``fs01|m`` -- the shape the Genesee Press comparison had.
    """
    await smb_day(client, h.MONDAY, ["M", "N", "O", "P"])
    await smb_day(client, h.FRIDAY, ["A", "B", "M", "N", "O", "P"])


@pytest.fixture
async def reordered_acl(client: AsyncClient) -> None:
    """An ACL whose two entries swapped places: an order-only change, which needs the ACL
    rebuilt at both instants to classify -- the reconstruction phase of a comparison."""
    await ntfs_day(client, h.MONDAY, [(GROUP, FULL_CONTROL, 0), (ALICE, READ_EXECUTE, 1)])
    await ntfs_day(client, h.FRIDAY, [(GROUP, FULL_CONTROL, 1), (ALICE, READ_EXECUTE, 0)])


def clock_that_runs_out(after_calls: int) -> Callable[[], float]:
    """A clock standing still for ``after_calls`` readings, then ten seconds later."""
    calls = {"n": 0}

    def tick() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] <= after_calls else 10.0

    return tick


class TestANormalComparisonIsComplete:
    async def test_complete_with_nothing_to_recover(
        self, session: AsyncSession, shares_added_early_in_key_order: None
    ) -> None:
        comparison = await ChangeService(session).compare(
            FROM, TO, kinds=SHARES, significance=ALL_SIGNIFICANCE
        )

        assert comparison.outcome is ComparisonOutcome.COMPLETE
        assert comparison.complete
        assert comparison.truncated is False
        assert comparison.compared_through is None
        assert comparison.narrower == ()
        assert {change.key for change in comparison.changes} == {"fs01|a", "fs01|b"}
        assert comparison.unobserved_at_to == 0
        assert comparison.objects_read_from == 4
        assert comparison.objects_read_to == 6


class TestTheSidesAreCutAtTheSameKey:
    async def test_objects_past_the_shorter_cut_are_not_called_unobserved(
        self, session: AsyncSession, shares_added_early_in_key_order: None
    ) -> None:
        comparison = await ChangeService(session).compare(
            FROM,
            TO,
            kinds=SHARES,
            significance=ALL_SIGNIFICANCE,
            budget=ComparisonBudget(objects_per_side=3),
        )

        assert comparison.outcome is ComparisonOutcome.INCOMPLETE
        assert not comparison.complete
        assert comparison.truncated is True
        assert comparison.compared_through == (ObservationKind.SMB_SHARE, "fs01|m")
        # N and O were observed on Friday. Cut independently they read as unobserved there.
        assert comparison.unobserved_at_to == 0
        assert comparison.unchanged == 1
        assert {change.key for change in comparison.changes} == {"fs01|a", "fs01|b"}
        assert {change.action for change in comparison.changes} <= {
            ChangeAction.ADDED,
            ChangeAction.FIRST_OBSERVED,
        }

    async def test_nothing_past_the_boundary_is_reported_either_way(
        self, session: AsyncSession, shares_added_early_in_key_order: None
    ) -> None:
        comparison = await ChangeService(session).compare(
            FROM,
            TO,
            kinds=SHARES,
            significance=ALL_SIGNIFICANCE,
            budget=ComparisonBudget(objects_per_side=3),
        )
        assert comparison.compared_through is not None

        boundary = comparison.compared_through[1]
        assert all(change.key <= boundary for change in comparison.changes)

    async def test_a_single_kind_comparison_offers_no_circular_narrowing(
        self, session: AsyncSession, shares_added_early_in_key_order: None
    ) -> None:
        comparison = await ChangeService(session).compare(
            FROM, TO, kinds=SHARES, budget=ComparisonBudget(objects_per_side=3)
        )
        assert comparison.narrower == ()

    async def test_an_estate_wide_comparison_offers_the_kinds_that_fit(
        self, session: AsyncSession, shares_added_early_in_key_order: None
    ) -> None:
        comparison = await ChangeService(session).compare(
            FROM, TO, budget=ComparisonBudget(objects_per_side=6)
        )

        assert comparison.outcome is ComparisonOutcome.INCOMPLETE
        offered = {
            item.kind: (item.objects_at_from, item.objects_at_to) for item in comparison.narrower
        }
        assert offered == {
            ObservationKind.SERVER: (1, 1),
            ObservationKind.SMB_SHARE: (4, 6),
            ObservationKind.SMB_ACE: (4, 6),
        }


class TestTheInteractiveWindow:
    async def test_past_the_window_after_reading_it_stops_and_presents_nothing(
        self, session: AsyncSession, shares_added_early_in_key_order: None
    ) -> None:
        comparison = await ChangeService(session).compare(
            FROM,
            TO,
            significance=ALL_SIGNIFICANCE,
            budget=ComparisonBudget(window_ms=4_000),
            clock=clock_that_runs_out(after_calls=1),
        )

        assert comparison.outcome is ComparisonOutcome.TIMED_OUT
        assert comparison.stopped_in is ComparisonPhase.READ
        assert not comparison.complete
        assert comparison.truncated is True
        assert comparison.changes == ()
        assert comparison.compared_through is None
        assert comparison.objects_read_from > 0
        assert comparison.elapsed_ms >= 4_000

    async def test_past_the_window_while_rebuilding_acls_it_presents_nothing(
        self, session: AsyncSession, reordered_acl: None
    ) -> None:
        """No list whose order-only changes were left undecided is presented as an answer."""
        complete = await ChangeService(session).compare(
            FROM, TO, kinds=frozenset({ObservationKind.NTFS_ACE}), significance=ALL_SIGNIFICANCE
        )
        assert complete.outcome is ComparisonOutcome.COMPLETE
        assert complete.changes

        # Readings: the start, the read checkpoint, the classify checkpoint; then the clock
        # runs out at the first ACL reconstruction.
        stopped = await ChangeService(session).compare(
            FROM,
            TO,
            kinds=frozenset({ObservationKind.NTFS_ACE}),
            significance=ALL_SIGNIFICANCE,
            clock=clock_that_runs_out(after_calls=3),
        )

        assert stopped.outcome is ComparisonOutcome.TIMED_OUT
        assert stopped.stopped_in is ComparisonPhase.ORDERING
        assert stopped.changes == ()
        assert stopped.differences_found == complete.differences_found

    async def test_a_caller_that_went_away_cancels_the_work(
        self, session: AsyncSession, shares_added_early_in_key_order: None
    ) -> None:
        async def gone() -> bool:
            return True

        comparison = await ChangeService(session).compare(FROM, TO, should_stop=gone)

        assert comparison.outcome is ComparisonOutcome.CANCELLED
        assert comparison.changes == ()
        assert comparison.narrower == ()


def compare_params(**extra: str) -> dict[str, str]:
    return {"from": FROM.isoformat(), "to": TO.isoformat(), "significance": "security", **extra}


class TestTheApi:
    async def test_a_normal_comparison_is_complete_and_identified(
        self, client: AsyncClient, shares_added_early_in_key_order: None
    ) -> None:
        body = (await client.get("/api/v1/changes/compare", params=compare_params())).json()

        assert body["outcome"] == "complete"
        assert body["complete"] is True
        assert body["recovered"] is False
        assert body["recovery"] == []
        assert body["window_ms"] == comparison_module.COMPARISON_WINDOW_MS
        assert body["objects_per_side"] == comparison_module.COMPARISON_CEILING
        assert body["requested_scope"] == {"target": None, "key": None, "kinds": []}
        assert body["comparison_id"]

    async def test_timeout_then_recovery_then_a_complete_recovered_comparison(
        self,
        client: AsyncClient,
        shares_added_early_in_key_order: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The whole drill: start, time out, follow a recovery action, complete."""
        monkeypatch.setattr(comparison_module, "_clock", clock_that_runs_out(after_calls=1))
        first = (
            await client.get("/api/v1/changes/compare", params=compare_params(time_budget_ms="1"))
        ).json()

        assert first["outcome"] == "timed_out"
        assert first["complete"] is False
        assert first["truncated"] is True
        assert first["changes"] == []
        assert first["stopped_in"] == "read"
        actions = {item["action"]: item for item in first["recovery"]}
        assert "retry_standard_window" in actions
        assert actions["retry_standard_window"]["parameters"] == {
            "time_budget_ms": str(comparison_module.COMPARISON_WINDOW_MS),
            "retry_of": first["comparison_id"],
        }
        narrow = next(
            item
            for item in first["recovery"]
            if item["action"] == "narrow_kind" and item["parameters"]["kind"] == "smb_share"
        )
        assert narrow["objects_at_from"] == 4
        assert narrow["objects_at_to"] == 6

        monkeypatch.setattr(comparison_module, "_clock", lambda: 0.0)
        second = (
            await client.get(
                "/api/v1/changes/compare", params=compare_params(**narrow["parameters"])
            )
        ).json()

        assert second["outcome"] == "complete"
        assert second["complete"] is True
        assert second["recovered"] is True
        assert second["retry_of"] == first["comparison_id"]
        assert second["requested_scope"]["kinds"] == ["smb_share"]
        assert second["recovery"] == []

    async def test_every_comparison_is_logged_with_what_diagnoses_it(
        self,
        client: AsyncClient,
        shares_added_early_in_key_order: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="adg.changes"):
            body = (
                await client.get("/api/v1/changes/compare", params=compare_params(server="FS01"))
            ).json()

        record = next(item for item in caplog.records if item.getMessage() == "changes.compare")
        fields: dict[str, Any] = record.__dict__
        assert fields["comparison_id"] == body["comparison_id"]
        assert fields["outcome"] == "complete"
        assert fields["requested_scope"] == "server:FS01"
        assert fields["effective_scope"] == "whole scope"
        for name in (
            "at_from",
            "at_to",
            "window_ms",
            "elapsed_ms",
            "objects_per_side",
            "objects_read_from",
            "objects_read_to",
            "differences_found",
            "changes_returned",
            "recovery_offered",
            "retry_of",
            "cached",
            "stopped_in",
        ):
            assert name in fields
        # Measurements and identifiers of the question, never what was compared.
        assert "changes" not in fields
        assert all("state" not in str(value) for value in fields.values() if value)

    async def test_the_window_is_bounded(self, client: AsyncClient) -> None:
        too_long = await client.get(
            "/api/v1/changes/compare",
            params=compare_params(
                time_budget_ms=str(comparison_module.MAX_COMPARISON_WINDOW_MS + 1)
            ),
        )
        assert too_long.status_code == 422

    async def test_retry_of_must_name_an_attempt(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/changes/compare", params=compare_params(retry_of="not-an-id")
        )
        assert response.status_code == 422
