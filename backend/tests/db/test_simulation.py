r"""Phase 9A against a real PostgreSQL: the engine, the isolation, and the store.

The hermetic suites prove the arithmetic over in-memory repositories. Four things can only
be checked here, and they are the four the phase's acceptance criteria are written in:

1. **A simulation changes nothing.** Every collected-state table is digested before and
   after a proposal is evaluated *and stored*, and the digests must be identical. This is the
   test that would catch an overlay that leaked into an ingestion path, a repository that
   wrote what it read back, or a store that reached past its own two tables.
2. **The production resolver answers.** The before and after come from
   :class:`app.services.AccessService` over the live repositories, so if the engine and the
   simulation ever disagreed about the baseline, they would disagree here.
3. **Alternate paths are found in a real graph.** ``04-multiple-membership-paths`` puts Alice
   in ``Finance-RW`` by two chains and in ``Domain Users`` besides. Removing one chain is
   exactly the change an administrator would sign off believing it revokes her access.
4. **A stored proposal knows the estate has moved.** The basis token is read from
   ``scan_runs``; only a second real ingestion moves it.

The transcript is replayed through the HTTP ingestion endpoint, as every other database
suite does, so the rows under test are the rows a collector would have produced.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import AccessPath
from app.db import Database
from app.domain import AceType, MembershipEdgeKind
from app.models.schema import (
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    object_versions,
    principals,
    scan_runs,
    servers,
    smb_share_aces,
    smb_shares,
)
from app.repositories import MembershipRepository, ResourceRepository
from app.services.access import AccessService
from app.simulation import (
    ChangeKind,
    ChangeOutcome,
    ImpactDirection,
    MembershipChange,
    NtfsAceChange,
    ScopeKind,
    SimulationCaveat,
    SimulationOverlay,
    SimulationScope,
    SimulationService,
    SimulationStore,
)
from tests.db.test_query_cost import StatementLog
from tests.fixtures import load_raw
from tests.support.ingest import replay, storable, with_run_id

pytestmark = pytest.mark.anyio

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN_SID}-1104"
FINANCE_TEAM = f"{DOMAIN_SID}-1201"
FINANCE_RW = f"{DOMAIN_SID}-1202"
FINANCE_OPS = f"{DOMAIN_SID}-1204"
DOMAIN_USERS = f"{DOMAIN_SID}-513"

FINANCE = "\\\\FS01\\Finance"
FINANCE_KEY = FINANCE.casefold()

READ_EXECUTE = 0x001200A9
MODIFY = 0x001301BF

#: Everything a collector writes. A simulation may not change one byte of any of it.
COLLECTED_TABLES = (
    principals,
    membership_edges,
    servers,
    smb_shares,
    smb_share_aces,
    ntfs_resources,
    ntfs_aces,
    object_versions,
    scan_runs,
)


@pytest.fixture
async def estate(client: AsyncClient) -> dict[str, Any]:
    r"""``04-multiple-membership-paths``: Alice reaches ``\\FS01\Finance`` three ways."""
    return await replay(client, storable(load_raw("04-multiple-membership-paths")))


async def digest_of_collected_state(session: AsyncSession) -> str:
    """One digest over every row of every table a collector writes.

    Ordered by primary key so the digest is a property of the *contents* and not of the order
    PostgreSQL happened to return them in; a digest that moved when nothing changed would
    make the isolation test useless in the direction that matters.
    """
    hasher = hashlib.sha256()
    for table in COLLECTED_TABLES:
        hasher.update(table.name.encode())
        columns = sorted(table.c, key=lambda column: column.name)
        statement = sa.select(*columns).order_by(*[column for column in table.primary_key])
        for row in (await session.execute(statement)).all():
            hasher.update(repr(row).encode())
    return hasher.hexdigest()


def remove(
    group_key: str, kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
) -> MembershipChange:
    """Take Alice out of one group.

    The edge kind is part of an edge's identity, and ``Domain Users`` holds Alice by
    ``primaryGroupID`` rather than by ``member`` — a membership that does not appear in the
    group's member list at all, and the one a collector reading only ``member`` loses. A
    proposal naming the wrong kind matches nothing, which the report says rather than hides;
    see ``test_a_removal_naming_the_wrong_edge_kind_matches_nothing``.
    """
    return MembershipChange(
        kind=ChangeKind.REMOVE_MEMBER, group_key=group_key, member_key=ALICE, edge_kind=kind
    )


PRIMARY = MembershipEdgeKind.PRIMARY_GROUP


def pair_scope() -> SimulationScope:
    return SimulationScope(
        kind=ScopeKind.PAIR,
        subject_key=ALICE,
        resource_key=FINANCE,
        path=AccessPath.REMOTE_SMB,
    )


@pytest.mark.usefixtures("estate")
class TestTheBaselineIsTheLiveAnswer:
    async def test_alice_holds_modify_before_anything_is_proposed(
        self, session: AsyncSession
    ) -> None:
        answer = await AccessService(
            ResourceRepository(session), MembershipRepository(session)
        ).effective_access(ALICE, FINANCE)

        assert answer.access.rights.value == MODIFY

    async def test_a_simulation_of_nothing_reports_no_change(self, session: AsyncSession) -> None:
        report = await SimulationService(session).run(SimulationOverlay(), scope=pair_scope())

        assert report.deltas[0].direction is ImpactDirection.UNCHANGED
        assert report.inert


@pytest.mark.usefixtures("estate")
class TestAlternatePathsPreventAFalseClaim:
    async def test_removing_one_chain_changes_nothing(self, session: AsyncSession) -> None:
        """The whole point of the phase, against a real graph.

        ``Finance-Team`` is not Alice's only route into ``Finance-RW`` — ``Finance-Ops`` is
        the other — so taking her out of it revokes nothing. An administrator told otherwise
        would sign off a change that achieves nothing and believe the access was gone.
        """
        report = await SimulationService(session).run(
            SimulationOverlay(membership=(remove(FINANCE_TEAM),)), scope=pair_scope()
        )
        delta = report.deltas[0]

        assert report.applications[0].outcome is ChangeOutcome.APPLIED
        assert delta.direction is ImpactDirection.UNCHANGED
        assert delta.after.rights.value == MODIFY

    async def test_removing_both_chains_leaves_only_the_weaker_grant(
        self, session: AsyncSession
    ) -> None:
        report = await SimulationService(session).run(
            SimulationOverlay(membership=(remove(FINANCE_TEAM), remove(FINANCE_OPS))),
            scope=pair_scope(),
        )
        delta = report.deltas[0]

        assert delta.direction is ImpactDirection.REDUCED
        assert delta.after.rights.value == READ_EXECUTE
        assert delta.alternate_path_retained
        assert any(DOMAIN_USERS in path.chain for path in delta.retained_paths)

    async def test_a_removal_naming_the_wrong_edge_kind_matches_nothing(
        self, session: AsyncSession
    ) -> None:
        """And says so, rather than reporting "no impact" as though the change were safe.

        Alice's ``Domain Users`` membership is a primary-group one. A proposal written as an
        ordinary ``member`` removal names an edge that does not exist.
        """
        report = await SimulationService(session).run(
            SimulationOverlay(membership=(remove(DOMAIN_USERS),)), scope=pair_scope()
        )

        assert report.applications[0].outcome is ChangeOutcome.TARGET_NOT_FOUND
        assert report.inert

    async def test_removing_every_route_is_reported_as_losing_access(
        self, session: AsyncSession
    ) -> None:
        report = await SimulationService(session).run(
            SimulationOverlay(
                membership=(
                    remove(FINANCE_TEAM),
                    remove(FINANCE_OPS),
                    remove(DOMAIN_USERS, PRIMARY),
                )
            ),
            scope=pair_scope(),
        )
        delta = report.deltas[0]

        assert all(item.applied for item in report.applications)
        assert delta.direction is ImpactDirection.LOST_ACCESS
        assert not delta.alternate_path_retained
        assert SimulationCaveat.ALTERNATE_PATH_RETAINS_ACCESS not in delta.caveats


@pytest.mark.usefixtures("estate")
class TestNothingIsMutated:
    async def test_evaluating_a_proposal_changes_no_collected_row(
        self, session: AsyncSession
    ) -> None:
        before = await digest_of_collected_state(session)

        await SimulationService(session).run(
            SimulationOverlay(
                membership=(remove(FINANCE_TEAM),),
                ntfs_aces=(
                    NtfsAceChange(
                        kind=ChangeKind.ADD_NTFS_ACE,
                        resource_key=FINANCE,
                        trustee_sid=f"{DOMAIN_SID}-1109",
                        ace_type=AceType.ALLOW,
                        access_mask=MODIFY,
                    ),
                ),
            ),
            scope=SimulationScope(kind=ScopeKind.AFFECTED),
        )

        assert await digest_of_collected_state(session) == before

    async def test_storing_a_proposal_changes_no_collected_row(self, session: AsyncSession) -> None:
        """The store writes, so this is the one that proves *where*."""
        service = SimulationService(session)
        report = await service.run(
            SimulationOverlay(membership=(remove(FINANCE_TEAM),)), scope=pair_scope()
        )
        before = await digest_of_collected_state(session)

        await SimulationStore(session).save(report, name="Trim Finance-Team")
        await session.commit()

        assert await digest_of_collected_state(session) == before

    async def test_the_live_answer_is_identical_after_a_simulation(
        self, session: AsyncSession
    ) -> None:
        service = AccessService(ResourceRepository(session), MembershipRepository(session))
        before = (await service.effective_access(ALICE, FINANCE)).access.rights.value

        await SimulationService(session).run(
            SimulationOverlay(
                membership=(remove(FINANCE_TEAM), remove(FINANCE_OPS), remove(DOMAIN_USERS))
            ),
            scope=pair_scope(),
        )

        after = (
            await AccessService(
                ResourceRepository(session), MembershipRepository(session)
            ).effective_access(ALICE, FINANCE)
        ).access.rights.value
        assert before == after == MODIFY

    async def test_no_simulated_row_reaches_the_ace_table(self, session: AsyncSession) -> None:
        """Every invented record carries ``source_key = 'simulated'``.

        If one ever reached storage, this is the query that finds it — and it is checked
        after a proposal that adds an ACE, which is the change that invents one.
        """
        await SimulationService(session).run(
            SimulationOverlay(
                ntfs_aces=(
                    NtfsAceChange(
                        kind=ChangeKind.ADD_NTFS_ACE,
                        resource_key=FINANCE,
                        trustee_sid=f"{DOMAIN_SID}-1109",
                        ace_type=AceType.ALLOW,
                        access_mask=MODIFY,
                    ),
                )
            ),
            scope=SimulationScope(kind=ScopeKind.AFFECTED),
        )
        await session.commit()

        for table in (ntfs_aces, smb_share_aces, membership_edges):
            keys = [
                row[0]
                for row in (
                    await session.execute(
                        sa.select(table.c.source_key if "source_key" in table.c else table.c[0])
                    )
                ).all()
            ]
            assert not any(str(key).startswith("simulated") for key in keys), table.name


@pytest.mark.usefixtures("estate")
class TestTheBaselineIsNamed:
    async def test_a_report_carries_the_collection_state_it_was_computed_against(
        self, session: AsyncSession
    ) -> None:
        service = SimulationService(session)

        report = await service.run(SimulationOverlay(), scope=pair_scope())

        assert report.baseline.token == (await service.current_basis()).token
        assert report.baseline.run_id is not None
        assert not await service.is_stale(report.baseline)

    async def test_a_baseline_becomes_stale_when_a_collector_writes(
        self, session: AsyncSession, client: AsyncClient
    ) -> None:
        """Only a real ingestion moves the token, which is what makes the check exact."""
        service = SimulationService(session)
        baseline = await service.baseline()

        await replay(client, with_run_id(storable(load_raw("01-direct-user-grant"))))

        assert await service.is_stale(baseline)


@pytest.mark.usefixtures("estate")
class TestTheStore:
    async def test_a_saved_proposal_round_trips_through_its_own_constructors(
        self, session: AsyncSession
    ) -> None:
        overlay = SimulationOverlay(membership=(remove(FINANCE_TEAM),))
        service = SimulationService(session)
        report = await service.run(overlay, scope=pair_scope())

        stored = await SimulationStore(session).save(
            report, name="Trim Finance-Team", description="Ticket CHG-4711"
        )
        await session.commit()
        read_back = await SimulationStore(session).get(stored.simulation_id)

        assert read_back is not None
        assert read_back.overlay == overlay
        assert read_back.overlay.overlay_hash == overlay.overlay_hash
        assert read_back.name == "Trim Finance-Team"
        assert read_back.baseline_token == report.baseline.token

    async def test_the_first_evaluation_is_stored_with_the_proposal(
        self, session: AsyncSession
    ) -> None:
        service = SimulationService(session)
        report = await service.run(
            SimulationOverlay(membership=(remove(FINANCE_TEAM),)), scope=pair_scope()
        )
        store = SimulationStore(session)

        stored = await store.save(report, name="Trim Finance-Team")
        await session.commit()
        evaluations = await store.evaluations(stored.simulation_id)

        assert len(evaluations) == 1
        assert evaluations[0].scope_kind is ScopeKind.PAIR
        assert evaluations[0].complete
        assert evaluations[0].report["summary"]["evaluated"] == 1

    async def test_re_running_adds_an_evaluation_rather_than_replacing_one(
        self, session: AsyncSession, client: AsyncClient
    ) -> None:
        """Two evaluations against two collection states are two findings.

        *"This change was safe on Monday and takes access away today"* is the sentence the
        history of a proposal exists to make available; overwriting keeps only its second
        half.
        """
        service = SimulationService(session)
        store = SimulationStore(session)
        overlay = SimulationOverlay(membership=(remove(FINANCE_TEAM),))
        stored = await store.save(
            await service.run(overlay, scope=pair_scope()), name="Trim Finance-Team"
        )
        await session.commit()

        await replay(client, with_run_id(storable(load_raw("01-direct-user-grant"))))
        again = await service.run(overlay, scope=pair_scope())
        await store.record(stored.simulation_id, again, current=await service.current_basis())
        await session.commit()

        evaluations = await store.evaluations(stored.simulation_id)
        assert len(evaluations) == 2

    async def test_a_listing_pages_newest_first(self, session: AsyncSession) -> None:
        service = SimulationService(session)
        store = SimulationStore(session)
        for index in range(3):
            report = await service.run(
                SimulationOverlay(
                    membership=(
                        MembershipChange(
                            kind=ChangeKind.REMOVE_MEMBER,
                            group_key=FINANCE_TEAM,
                            member_key=f"{DOMAIN_SID}-111{index}",
                        ),
                    )
                ),
                scope=pair_scope(),
            )
            await store.save(report, name=f"Proposal {index}")
        await session.commit()

        page = await store.list(limit=2)

        assert len(page.items) == 2
        assert page.has_more
        assert page.next_key is not None

    async def test_deleting_a_proposal_takes_its_evaluations_and_nothing_else(
        self, session: AsyncSession
    ) -> None:
        service = SimulationService(session)
        store = SimulationStore(session)
        stored = await store.save(
            await service.run(SimulationOverlay(), scope=pair_scope()), name="Nothing"
        )
        await session.commit()
        before = await digest_of_collected_state(session)

        removed = await store.delete(stored.simulation_id)
        await session.commit()

        assert removed
        assert await store.get(stored.simulation_id) is None
        assert await store.evaluations(stored.simulation_id) == ()
        assert await digest_of_collected_state(session) == before

    async def test_an_unnamed_proposal_is_refused(self, session: AsyncSession) -> None:
        from app.domain import DomainValidationError

        report = await SimulationService(session).run(SimulationOverlay(), scope=pair_scope())

        with pytest.raises(DomainValidationError, match="needs a name"):
            await SimulationStore(session).save(report, name="   ")


@pytest.mark.usefixtures("estate")
class TestTheAffectedScope:
    async def test_a_membership_change_finds_the_resource_through_the_reference_index(
        self, session: AsyncSession
    ) -> None:
        r"""Nothing in the proposal names ``\\FS01\Finance``; the index does."""
        report = await SimulationService(session).run(
            SimulationOverlay(
                membership=(
                    MembershipChange(
                        kind=ChangeKind.ADD_MEMBER,
                        group_key=FINANCE_RW,
                        member_key=f"{DOMAIN_SID}-1109",
                    ),
                )
            ),
            scope=SimulationScope(kind=ScopeKind.AFFECTED),
        )

        assert FINANCE_KEY in {delta.resource_key for delta in report.deltas}

    async def test_the_cost_is_reported_so_a_bound_can_be_sized_from_evidence(
        self, session: AsyncSession
    ) -> None:
        report = await SimulationService(session).run(
            SimulationOverlay(membership=(remove(FINANCE_TEAM),)),
            scope=SimulationScope(kind=ScopeKind.AFFECTED),
        )

        assert report.cost.pairs_evaluated == len(report.deltas)
        assert report.cost.resolutions >= report.cost.pairs_evaluated
        assert report.cost.elapsed_ms >= 0


@pytest.mark.usefixtures("estate")
class TestTheCostIsBounded:
    """What a simulation actually costs the database, counted rather than assumed.

    The figures in ``docs/architecture/simulation.md`` §6 come from here. They are pinned with
    ceilings rather than equalities: an exact count would fail on any harmless change to a
    query, while a ceiling fails on the thing that matters — a read whose count grows with the
    size of the estate.
    """

    async def test_a_pair_simulation_issues_a_bounded_number_of_statements(
        self, session: AsyncSession, database: Database
    ) -> None:
        with StatementLog(database.engine) as log:
            report = await SimulationService(session).run(
                SimulationOverlay(membership=(remove(FINANCE_TEAM),)), scope=pair_scope()
            )

        # Two resolutions (before and after) plus one explanation, each a bounded set of
        # indexed reads, plus the applicability check and the basis read.
        assert report.cost.resolutions <= 3
        assert log.count <= 40, log.statements
        # The membership graph is walked level by level, never row by row.
        assert log.reading("membership_edges") <= 12

    async def test_an_affected_scope_over_this_estate_stays_inside_the_time_budget(
        self, session: AsyncSession
    ) -> None:
        report = await SimulationService(session).run(
            SimulationOverlay(membership=(remove(FINANCE_TEAM),)),
            scope=SimulationScope(kind=ScopeKind.AFFECTED),
        )

        assert report.cost.elapsed_ms < report.bounds.time_budget_ms
        assert report.complete or report.truncation
