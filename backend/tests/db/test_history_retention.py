"""Retention against a real database: what a prune would take, and what it refuses to.

The estate is one share observed four times, changing on three of them, which is the
smallest thing that has a version old enough to prune *and* a successor old enough to stand
in for it. Four versions is also the smallest number that distinguishes the two rules —
"never the open one" and "never the newest closed one" — from each other.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError
from app.history.repository import VersionReader
from app.history.retention import HistoryRetentionService, RetentionPolicy
from app.models.schema import object_versions
from tests.support import history as h
from tests.support.ingest import replay

FS01 = "FS01"
FINANCE = "fs01|finance"

#: Long after every version in the estate closed, so a cutoff between them is easy to state.
MUCH_LATER = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
async def four_versions(client: AsyncClient) -> None:
    """One share, described differently on four consecutive scans."""
    for moment, description in (
        (h.MONDAY, "Finance"),
        (h.WEDNESDAY, "Finance (restricted)"),
        (h.FRIDAY, "Finance (archived)"),
        (h.NEXT_MONDAY, "Finance (retired)"),
    ):
        await replay(
            client,
            h.smb_scan(
                observations=[
                    h.server(FS01, at=moment),
                    h.share(FS01, "Finance", at=moment, description=description),
                ],
                started_at=moment,
                reconcile=False,
            ),
        )


async def version_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(object_versions)
                .where(object_versions.c.object_kind == ObservationKind.SMB_SHARE.value)
            )
        ).scalar_one()
    )


@pytest.mark.usefixtures("four_versions")
class TestPlanningWritesNothing:
    async def test_the_default_policy_plans_nothing_at_all(self, session: AsyncSession) -> None:
        plan = await HistoryRetentionService(session, RetentionPolicy()).plan(MUCH_LATER)

        assert plan.cutoff is None
        assert plan.is_empty

    async def test_a_plan_counts_candidates_without_removing_them(
        self, session: AsyncSession
    ) -> None:
        service = HistoryRetentionService(session, RetentionPolicy(retain_days=30, enabled=True))

        before = await version_count(session)
        plan = await service.plan(MUCH_LATER)

        assert plan.total > 0
        assert await version_count(session) == before

    async def test_it_plans_every_closed_version_but_the_newest(
        self, session: AsyncSession
    ) -> None:
        service = HistoryRetentionService(session, RetentionPolicy(retain_days=30, enabled=True))

        plan = await service.plan(MUCH_LATER)

        # Four versions: three closed, one open. The newest closed one is kept as the bound
        # on when the open one began, so two are candidates.
        assert plan.candidates[ObservationKind.SMB_SHARE] == 2

    async def test_a_cutoff_before_every_closure_plans_nothing(self, session: AsyncSession) -> None:
        service = HistoryRetentionService(
            session, RetentionPolicy(retain_days=36_500, enabled=True)
        )

        assert (await service.plan(MUCH_LATER)).is_empty


@pytest.mark.usefixtures("four_versions")
class TestApplyingIsAlwaysAnExplicitDecision:
    async def test_a_policy_that_is_not_enabled_refuses_rather_than_no_ops(
        self, session: AsyncSession
    ) -> None:
        """A caller that asked to prune and was silently ignored keeps believing it worked."""
        service = HistoryRetentionService(session, RetentionPolicy(retain_days=30))

        with pytest.raises(DomainValidationError, match="not enabled"):
            await service.apply(MUCH_LATER)

    async def test_the_default_policy_cannot_be_applied_at_all(self, session: AsyncSession) -> None:
        with pytest.raises(DomainValidationError, match="not enabled"):
            await HistoryRetentionService(session, RetentionPolicy()).apply(MUCH_LATER)

        assert await version_count(session) == 4


@pytest.mark.usefixtures("four_versions")
class TestWhatAPruneKeeps:
    async def test_the_open_version_survives(self, session: AsyncSession) -> None:
        service = HistoryRetentionService(session, RetentionPolicy(retain_days=30, enabled=True))

        await service.apply(MUCH_LATER)

        open_versions = (
            (
                await session.execute(
                    sa.select(object_versions.c.state).where(object_versions.c.valid_to.is_(None))
                )
            )
            .scalars()
            .all()
        )

        assert len(open_versions) == 2  # the share, and the server that was never superseded
        assert any(state.get("description") == "Finance (retired)" for state in open_versions)

    async def test_the_newest_closed_version_survives_so_the_current_state_has_a_beginning(
        self, session: AsyncSession
    ) -> None:
        service = HistoryRetentionService(session, RetentionPolicy(retain_days=30, enabled=True))

        outcome = await service.apply(MUCH_LATER)
        timeline = await VersionReader(session).timeline(ObservationKind.SMB_SHARE, FINANCE)

        assert outcome.removed == 2
        assert len(timeline) == 2
        assert timeline.versions[0].state is not None
        assert timeline.versions[0].state["description"] == "Finance (archived)"
        assert timeline.opened_window(timeline.versions[1]) is not None

    async def test_pruning_twice_removes_nothing_the_second_time(
        self, session: AsyncSession
    ) -> None:
        service = HistoryRetentionService(session, RetentionPolicy(retain_days=30, enabled=True))

        await service.apply(MUCH_LATER)
        second = await service.apply(MUCH_LATER)

        assert second.removed == 0

    async def test_current_state_is_untouched(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await HistoryRetentionService(session, RetentionPolicy(retain_days=30, enabled=True)).apply(
            MUCH_LATER
        )

        response = await client.get(f"/api/v1/shares/{FINANCE}")

        assert response.status_code == 200
        assert response.json()["name"] == "Finance"
