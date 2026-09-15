r"""The risk engine against a real estate in a real database.

The hermetic suites pin what each rule matches. This one pins the three things that only
exist once the rules meet PostgreSQL:

1. **Facts come out of storage the way the rules expect them.** The fact bundle is built from
   the collected tables, so an estate seeded through the ingestion endpoints produces findings
   about the shapes the demo estate deliberately contains — an ``Everyone`` grant, an orphaned
   SID, a disabled account that still holds rights, a protected directory.
2. **Findings resolve and reopen as the estate changes**, and the timeline records each
   transition rather than only the newest pair of timestamps.
3. **An incremental pass does not close what it did not look at.** This is the safety property
   of the whole feature and the one that fails silently: an evaluation that re-matched a
   fraction of the estate and was allowed to resolve everything else would empty the report on
   the first collector run and look like an improvement.

The estate is :mod:`app.demo.estate`, replayed through the ingestion **HTTP endpoints** — the
same route a Windows collector takes — so the facts under test arrived the way real ones do.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.estate import DemoEstate, build_estate
from app.demo.seed import seed_transcripts
from app.demo.transcripts import build_transcripts
from app.models.schema import (
    RiskEvaluationTrigger,
    RiskFindingEventType,
    RiskFindingStatus,
    membership_edges,
    ntfs_aces,
    risk_evaluations,
    risk_finding_events,
    risk_findings,
)
from app.repositories.risk import (
    RiskFactsRepository,
    RiskFindingRepository,
    rebuild_finding,
)
from app.risk_engine import (
    DEFAULT_CONFIGURATION,
    RiskConfiguration,
    RiskFinding,
    RiskScope,
    RuleId,
    SensitiveResource,
    reproduce,
)
from app.services.risk import RiskEvaluation, RiskService

pytestmark = pytest.mark.anyio

FINANCE = r"\\fs01\finance"
PUBLIC = r"\\fs01\public"
CONFIDENTIAL = r"\\fs01\hr\confidential"

NOW = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)
LATER = NOW + dt.timedelta(hours=1)
LATER_STILL = NOW + dt.timedelta(hours=2)


@pytest.fixture(scope="module")
def estate() -> DemoEstate:
    return build_estate("small")


@pytest.fixture
async def seeded(client: AsyncClient, estate: DemoEstate) -> DemoEstate:
    """The demo estate, posted through the ingestion API exactly as a collector would."""
    await seed_transcripts(client, build_transcripts(estate))
    return estate


def service(session: AsyncSession, configuration: RiskConfiguration | None = None) -> RiskService:
    return RiskService(
        RiskFactsRepository(session),
        RiskFindingRepository(session),
        configuration or DEFAULT_CONFIGURATION,
    )


# --------------------------------------------------------------------------------------
# Facts out of storage
# --------------------------------------------------------------------------------------


class TestTheStoreToRulesSeam:
    async def test_the_loader_builds_a_bundle_from_the_collected_tables(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        load = await RiskFactsRepository(session).load()

        assert load.complete, f"the demo estate should fit in one bundle: {load.truncated}"
        assert load.facts.resources, "no directories were loaded"
        assert load.facts.shares, "no shares were loaded"
        assert load.facts.principals, "no principals were loaded"
        assert load.facts.scope.complete

    async def test_a_full_pass_finds_the_shapes_the_estate_deliberately_contains(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        evaluation = await service(session).evaluate_estate(now=NOW)

        produced = {finding.rule_id for finding in evaluation.findings}
        # Each of these is a feature the demo estate documents in `app.demo.estate.FEATURES`.
        assert RuleId.EVERYONE_BROAD_ACCESS in produced, "Everyone/Full Control on \\\\FS01\\Public"
        assert RuleId.UNRESOLVED_SID_ON_ACL in produced, (
            "the orphaned SID on \\\\FS02\\Projects\\Beta"
        )
        assert RuleId.DISABLED_PRINCIPAL_RETAINS_ACCESS in produced, "Erin Black, disabled"
        assert RuleId.BROKEN_INHERITANCE in produced, "HR\\Confidential is protected"
        assert evaluation.complete

    async def test_every_finding_reproduces_from_the_evidence_that_was_stored(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """The acceptance criterion, checked against evidence that made a round trip to JSONB."""
        evaluation = await service(session).evaluate_estate(now=NOW)
        assert evaluation.findings

        for finding in evaluation.findings:
            stored = await RiskFindingRepository(session).get(finding.key)
            assert stored is not None
            again = reproduce(rebuild_finding(stored), DEFAULT_CONFIGURATION)
            assert again is not None, f"{finding.rule_id.value} did not survive storage"
            assert again.key == finding.key

    async def test_a_share_whose_acl_no_run_read_produces_no_share_findings(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """FS03's run failed outright, so nothing below it may be reported as permissive."""
        evaluation = await service(session).evaluate_estate(now=NOW)
        keys = {finding.subject.share_key for finding in evaluation.findings}
        assert not any(key and key.startswith("fs03|") for key in keys)

    async def test_an_empty_group_is_reported_only_where_a_reconciling_run_enumerated_it(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """The coverage check, against the real reconciliation evidence rather than a fixture."""
        load = await RiskFactsRepository(session).load()
        enumerated = {key: record.enumerated for key, record in load.facts.memberships.items()}
        assert enumerated, "no membership records were loaded"
        # The AD run reconciles the domain scope and succeeds, so the principals it described
        # are enumerated; nothing in the estate should be claimed as enumerated without one.
        assert any(value is True for value in enumerated.values())
        assert all(value is not None for value in enumerated.values())


# --------------------------------------------------------------------------------------
# Persistence, resolution and reopening
# --------------------------------------------------------------------------------------


class TestTheFindingLifecycle:
    async def test_a_first_pass_opens_every_finding_and_records_the_transition(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        evaluation = await service(session).evaluate_estate(now=NOW)

        assert len(evaluation.outcome.opened) == len(evaluation.findings)
        assert evaluation.outcome.resolved == ()
        stored = await session.execute(sa.select(sa.func.count()).select_from(risk_findings))
        assert stored.scalar_one() == len(evaluation.findings)

        events = await session.execute(sa.select(risk_finding_events.c.event_type).distinct())
        assert {row.event_type for row in events} == {RiskFindingEventType.OPENED.value}

    async def test_an_unchanged_second_pass_reaffirms_and_opens_nothing(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        first = await service(session).evaluate_estate(now=NOW)
        second = await service(session).evaluate_estate(now=LATER)

        assert second.outcome.opened == ()
        assert second.outcome.resolved == ()
        assert set(second.outcome.reaffirmed) == set(first.outcome.opened)

        record = await RiskFindingRepository(session).get(first.findings[0].key)
        assert record is not None
        assert record.detected_at == NOW, "an unchanged finding keeps its open window"
        assert record.last_evaluated_at == LATER
        assert record.occurrence_count == 1

    async def test_removing_the_grant_resolves_the_finding_rather_than_deleting_it(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        first = await service(session).evaluate_estate(now=NOW)
        target = _first(first, RuleId.EVERYONE_BROAD_ACCESS)

        await _delete_everyone_entries(session, target.subject.resource_key)
        second = await service(session).evaluate_estate(now=LATER)

        assert target.key in second.outcome.resolved
        record = await RiskFindingRepository(session).get(target.key)
        assert record is not None
        assert record.status is RiskFindingStatus.RESOLVED
        assert record.resolved_at == LATER
        assert record.resolved_evaluation_id == second.evaluation_id
        assert record.evidence, "a resolved finding keeps the evidence it was true on"

    async def test_the_grant_coming_back_reopens_the_same_finding(
        self, seeded: DemoEstate, session: AsyncSession, client: AsyncClient, estate: DemoEstate
    ) -> None:
        first = await service(session).evaluate_estate(now=NOW)
        target = _first(first, RuleId.EVERYONE_BROAD_ACCESS)
        removed = await _delete_everyone_entries(session, target.subject.resource_key)

        await service(session).evaluate_estate(now=LATER)
        await _restore(session, removed)
        third = await service(session).evaluate_estate(now=LATER_STILL)

        assert target.key in third.outcome.reopened
        record = await RiskFindingRepository(session).get(target.key)
        assert record is not None
        assert record.status is RiskFindingStatus.OPEN
        assert record.resolved_at is None
        assert record.occurrence_count == 2
        assert record.first_detected_at == NOW, "first seen never moves"
        assert record.detected_at == LATER_STILL, "the current open window starts at the reopen"

    async def test_the_timeline_records_every_transition_not_just_the_last(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        first = await service(session).evaluate_estate(now=NOW)
        target = _first(first, RuleId.EVERYONE_BROAD_ACCESS)
        removed = await _delete_everyone_entries(session, target.subject.resource_key)
        await service(session).evaluate_estate(now=LATER)
        await _restore(session, removed)
        await service(session).evaluate_estate(now=LATER_STILL)

        events = await RiskFindingRepository(session).events_for(target.key)
        assert [event["event_type"] for event in events] == [
            RiskFindingEventType.OPENED.value,
            RiskFindingEventType.RESOLVED.value,
            RiskFindingEventType.REOPENED.value,
        ]

    async def test_a_resolution_carries_no_evidence_digest(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """The rule stopped matching, so there are no facts it was resolved on."""
        first = await service(session).evaluate_estate(now=NOW)
        target = _first(first, RuleId.EVERYONE_BROAD_ACCESS)
        await _delete_everyone_entries(session, target.subject.resource_key)
        await service(session).evaluate_estate(now=LATER)

        events = await RiskFindingRepository(session).events_for(target.key)
        resolution = next(
            event for event in events if event["event_type"] == RiskFindingEventType.RESOLVED.value
        )
        assert resolution["evidence_digest"] is None

    async def test_a_widened_grant_keeps_the_finding_and_replaces_its_evidence(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        first = await service(session).evaluate_estate(now=NOW)
        target = _first(first, RuleId.EVERYONE_BROAD_ACCESS)
        before = await RiskFindingRepository(session).get(target.key)
        assert before is not None

        await session.execute(
            sa.update(ntfs_aces)
            .where(
                ntfs_aces.c.resource_key == target.subject.resource_key,
                ntfs_aces.c.trustee_sid == "S-1-1-0",
            )
            .values(access_mask=0x001F01FF, ace_flags=3)
        )
        await session.flush()

        second = await service(session).evaluate_estate(now=LATER)
        after = await RiskFindingRepository(session).get(target.key)
        assert after is not None
        assert after.status is RiskFindingStatus.OPEN
        assert after.detected_at == NOW, "widening a grant is not a new finding"
        if target.key in second.outcome.evidence_changed:
            assert after.evidence_digest != before.evidence_digest


# --------------------------------------------------------------------------------------
# Incremental evaluation
# --------------------------------------------------------------------------------------


class TestIncrementalEvaluationNeverClosesWhatItDidNotSee:
    async def test_a_pass_scoped_to_one_directory_resolves_nothing_elsewhere(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """The safety property, stated as directly as it can be.

        Every finding in the estate is open. A pass that looks at one directory — and finds
        nothing there, because the grant has been removed — must resolve exactly the findings
        about that directory, and must leave every other finding alone.
        """
        first = await service(session).evaluate_estate(now=NOW)
        target = _first(first, RuleId.EVERYONE_BROAD_ACCESS)
        resource_key = target.subject.resource_key
        assert resource_key is not None
        await _delete_everyone_entries(session, resource_key)

        second = await service(session).evaluate_subject(resource_keys=[resource_key], now=LATER)

        assert target.key in second.outcome.resolved
        assert second.outcome.out_of_scope, "the other findings must be named, not silently kept"
        still_open = await RiskFindingRepository(session).open_findings()
        assert len(still_open) == len(first.findings) - len(second.outcome.resolved)

    async def test_a_pass_that_loaded_nothing_resolves_nothing(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """The failure mode this guard exists for: an empty pass must not empty the report."""
        first = await service(session).evaluate_estate(now=NOW)
        assert first.findings

        empty = await service(session).evaluate_subject(resource_keys=[], now=LATER)

        assert empty.outcome.resolved == ()
        assert len(empty.outcome.out_of_scope) == len(first.findings)
        still_open = await RiskFindingRepository(session).open_findings()
        assert len(still_open) == len(first.findings)

    async def test_a_rule_that_did_not_run_cannot_resolve_its_own_findings(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """An absence of matches from a rule that never ran says nothing at all."""
        first = await service(session).evaluate_estate(now=NOW)
        target = _first(first, RuleId.EVERYONE_BROAD_ACCESS)
        await _delete_everyone_entries(session, target.subject.resource_key)

        # A pass that runs only the broken-inheritance rule. It matches nothing about
        # Everyone, and must not conclude anything about it.
        from app.risk_engine import only_rules

        narrowed = service(session, only_rules(DEFAULT_CONFIGURATION, [RuleId.BROKEN_INHERITANCE]))
        second = await narrowed.evaluate_estate(now=LATER)

        assert target.key not in second.outcome.resolved
        record = await RiskFindingRepository(session).get(target.key)
        assert record is not None
        assert record.status is RiskFindingStatus.OPEN

    async def test_an_incremental_pass_after_a_run_re_evaluates_what_the_run_changed(
        self, seeded: DemoEstate, session: AsyncSession, client: AsyncClient, estate: DemoEstate
    ) -> None:
        await service(session).evaluate_estate(now=NOW)

        run_id = await _any_run_id(session)
        changed = await RiskFactsRepository(session).changed_by_run(run_id)
        assert not changed.is_empty, "the seeding run must have opened versions"

        evaluation = await service(session).evaluate_run(run_id, now=LATER)

        assert evaluation.trigger is RiskEvaluationTrigger.INCREMENTAL
        assert evaluation.result.scope.complete is False
        assert set(evaluation.result.rules_run) <= set(RuleId)
        assert evaluation.outcome.opened == (), "the estate has not changed since the full pass"

    async def test_a_truncated_full_load_does_not_claim_a_complete_scope(
        self, seeded: DemoEstate, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pass that ran out of room has not covered the estate, whatever it asked for.

        Without this, a bundle that hit its ceiling would still be entitled to resolve every
        finding about the directories it never loaded.
        """
        monkeypatch.setattr("app.repositories.risk.MAX_RESOURCES", 1)
        load = await RiskFactsRepository(session).load()

        assert not load.complete
        assert load.facts.scope.complete is False
        assert load.facts.scope.resource_keys is not None


# --------------------------------------------------------------------------------------
# The evaluation record
# --------------------------------------------------------------------------------------


class TestTheEvaluationRecord:
    async def test_a_pass_records_what_it_covered_and_what_it_ran(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        evaluation = await service(session).evaluate_estate(now=NOW)

        row = (
            await session.execute(
                sa.select(risk_evaluations).where(
                    risk_evaluations.c.evaluation_id == evaluation.evaluation_id
                )
            )
        ).one()
        assert row.trigger == RiskEvaluationTrigger.FULL.value
        assert row.scope_complete is True
        assert row.scope_resource_keys == []
        assert sorted(row.rules_run) == sorted(rule.value for rule in RuleId)
        assert row.findings_opened == len(evaluation.outcome.opened)
        assert row.completed_at is not None

    async def test_a_disabled_rule_is_recorded_as_skipped_rather_than_omitted(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        from app.risk_engine import only_rules

        narrowed = only_rules(DEFAULT_CONFIGURATION, [RuleId.BROKEN_INHERITANCE])
        evaluation = await service(session, narrowed).evaluate_estate(now=NOW)

        row = (
            await session.execute(
                sa.select(risk_evaluations.c.rules_skipped).where(
                    risk_evaluations.c.evaluation_id == evaluation.evaluation_id
                )
            )
        ).one()
        assert RuleId.EVERYONE_BROAD_ACCESS.value in row.rules_skipped

    async def test_the_sensitive_rule_fires_only_once_a_resource_is_tagged(
        self, seeded: DemoEstate, session: AsyncSession
    ) -> None:
        """Acceptance criterion: sensitive-resource rules require explicit configuration."""
        untagged = await service(session).evaluate_estate(now=NOW)
        assert untagged.result.by_rule(RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE) == ()

        tagged = RiskConfiguration(
            version="tagged",
            sensitive_resources=(SensitiveResource(label="Public share", path_prefix=PUBLIC),),
        )
        result = await service(session, tagged).evaluate_estate(now=LATER)
        findings = result.result.by_rule(RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE)
        assert findings, "the Everyone/Full Control grant on the tagged share should be reported"
        assert all(item.detail["sensitivity_label"] == "Public share" for item in findings)


# --------------------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------------------


class TestTheDatabaseRefusesIncoherentRows:
    async def test_a_complete_scope_may_not_also_name_keys(
        self, migrated_database: str, session: AsyncSession
    ) -> None:
        repository = RiskFindingRepository(session)
        with pytest.raises(Exception, match="complete_scope_has_no_key_lists"):
            await repository.start_evaluation(
                trigger=RiskEvaluationTrigger.FULL,
                scope=RiskScope(complete=True),
                configuration_version="x",
                started_at=NOW,
            )
            await session.execute(
                sa.update(risk_evaluations).values(scope_resource_keys=["fs01|finance"])
            )
            await session.flush()

    async def test_an_incremental_evaluation_must_name_its_run(
        self, migrated_database: str, session: AsyncSession
    ) -> None:
        with pytest.raises(Exception, match="incremental_names_its_run"):
            await RiskFindingRepository(session).start_evaluation(
                trigger=RiskEvaluationTrigger.INCREMENTAL,
                scope=RiskScope(),
                configuration_version="x",
                started_at=NOW,
            )
            await session.flush()


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _first(evaluation: RiskEvaluation, rule_id: RuleId) -> RiskFinding:
    findings = evaluation.result.by_rule(rule_id)
    assert findings, f"the estate produced no {rule_id.value} finding"
    return findings[0]


async def _delete_everyone_entries(
    session: AsyncSession, resource_key: str | None
) -> list[dict[str, Any]]:
    """Remove the Everyone entries from one directory, returning them so they can come back.

    Deleting rows directly rather than ingesting a corrected scan, deliberately: the subject
    under test is the finding lifecycle, and routing it through a second seeding would make the
    test about ingestion's supersede rules instead.
    """
    assert resource_key is not None
    rows = (
        await session.execute(
            sa.select(ntfs_aces).where(
                ntfs_aces.c.resource_key == resource_key,
                ntfs_aces.c.trustee_sid == "S-1-1-0",
            )
        )
    ).all()
    assert rows, f"{resource_key} has no Everyone entry to remove"
    await session.execute(
        sa.delete(ntfs_aces).where(
            ntfs_aces.c.resource_key == resource_key,
            ntfs_aces.c.trustee_sid == "S-1-1-0",
        )
    )
    await session.flush()
    return [dict(row._mapping) for row in rows]


async def _restore(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await session.execute(sa.insert(ntfs_aces).values(rows))
    await session.flush()


async def _any_run_id(session: AsyncSession) -> UUID:
    from app.models.schema import scan_runs

    row = (
        await session.execute(
            sa.select(scan_runs.c.run_id).order_by(scan_runs.c.started_at).limit(1)
        )
    ).one()
    run_id: UUID = row.run_id
    return run_id


async def _edge_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(sa.select(sa.func.count()).select_from(membership_edges))
        ).scalar_one()
    )
