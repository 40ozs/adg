"""What the operator commands actually do, separated from how they are invoked.

Functions rather than a script body, so the tests exercise the same code path an operator
runs. A command that only exists inside ``__main__`` is a command whose behavior is pinned by
nothing.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from app.alerts import DrainReport
from app.config import Settings
from app.db import Database
from app.relationships.projector import ProjectionOutcome, RelationshipProjector
from app.repositories.relationships import RecordOutcome
from app.repositories.risk import RiskFactsRepository, RiskFindingRepository
from app.services.alerts import AlertService
from app.services.risk import RiskService

__all__ = [
    "DrainSummary",
    "EvaluationSummary",
    "GraphRebuildSummary",
    "drain_alerts",
    "evaluate_risks",
    "rebuild_graph",
]

logger = logging.getLogger("adg.operations")


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    """What one full pass found, and whether it covered the estate."""

    evaluation_id: Any
    matched: int
    opened: int
    reopened: int
    resolved: int
    complete: bool
    truncated: tuple[str, ...]
    alerts_notified: int = 0
    alerts_suppressed: int = 0

    @property
    def lines(self) -> tuple[str, ...]:
        """What to print. The coverage line is unconditional, deliberately.

        A summary that mentioned truncation only when it happened would teach an operator to
        skim past the line, and the one time it mattered they would.
        """
        head = (
            f"Evaluation {self.evaluation_id}: {self.matched} findings matched, "
            f"{self.opened} opened, {self.reopened} reopened, {self.resolved} resolved."
        )
        coverage = (
            "Coverage: the whole estate, no ceiling reached. These counts are totals."
            if self.complete
            else (
                "Coverage: INCOMPLETE. These counts are what this pass examined, not what "
                "the estate contains, and nothing outside the pass was resolved."
            )
        )
        alerts = f"Alerts: {self.alerts_notified} notified, {self.alerts_suppressed} suppressed."
        return (head, coverage, alerts, *(f"  - {note}" for note in self.truncated))


@dataclass(frozen=True, slots=True)
class DrainSummary:
    """What a drain achieved, across however many passes it took."""

    passes: tuple[DrainReport, ...]

    @property
    def delivered(self) -> int:
        return sum(report.delivered for report in self.passes)

    @property
    def abandoned(self) -> int:
        return sum(report.abandoned for report in self.passes)

    @property
    def retrying(self) -> int:
        return sum(report.retrying for report in self.passes)

    @property
    def lines(self) -> tuple[str, ...]:
        if not self.passes:
            return ("Nothing was due.",)
        rendered = [
            f"{len(self.passes)} pass(es): {self.delivered} delivered, "
            f"{self.retrying} scheduled for retry, {self.abandoned} abandoned."
        ]
        if self.abandoned:
            # Abandonment is the state that means somebody was meant to be told and was not.
            # It gets its own line rather than a number inside a summary.
            rendered.append(
                "ABANDONED deliveries are alerts nobody received. They are kept; see "
                "GET /api/v1/alerts/deliveries and the last_error on each."
            )
        for report in self.passes:
            rendered.extend(f"  - {error}" for error in report.errors)
        return tuple(rendered)


async def evaluate_risks(
    database: Database, settings: Settings, *, now: dt.datetime | None = None
) -> EvaluationSummary:
    """Run every enabled rule over the whole estate, then alert on what it opened.

    Two transactions, in this order and not the other: the findings are committed before a
    single alert is raised. An alerting failure therefore leaves a complete, correct report
    behind it — which is the version of "delivery failure does not roll back the source" that
    applies to an operator-run pass.
    """
    moment = now or dt.datetime.now(tz=dt.UTC)
    async with database.session() as session:
        evaluation = await RiskService(
            RiskFactsRepository(session),
            RiskFindingRepository(session),
            settings.risk_configuration,
        ).evaluate_estate(now=moment)
        matched = {finding.key: finding for finding in evaluation.findings}
        opened = [
            matched[key]
            for key in (*evaluation.outcome.opened, *evaluation.outcome.reopened)
            if key in matched
        ]
        resolved = list(evaluation.outcome.resolved)
        await session.commit()
        logger.info("operations.risk.evaluated", extra={"summary": evaluation.summary})

    notified = suppressed = 0
    async with database.session() as session:
        alerts = await AlertService(session, settings.alert_policy).evaluate_findings(
            opened=opened,
            resolved_keys=resolved,
            now=moment,
            evaluation_id=evaluation.evaluation_id,
        )
        notified, suppressed = len(alerts.notified), len(alerts.suppressed)
        await session.commit()

    return EvaluationSummary(
        evaluation_id=evaluation.evaluation_id,
        matched=len(evaluation.findings),
        opened=len(evaluation.outcome.opened),
        reopened=len(evaluation.outcome.reopened),
        resolved=len(evaluation.outcome.resolved),
        complete=evaluation.complete,
        truncated=tuple(evaluation.load.truncated),
        alerts_notified=notified,
        alerts_suppressed=suppressed,
    )


async def drain_alerts(
    database: Database,
    settings: Settings,
    *,
    now: dt.datetime | None = None,
    max_passes: int = 20,
) -> DrainSummary:
    """Attempt every delivery that is due, in bounded passes.

    Each pass commits on its own. A drain interrupted halfway keeps what it delivered, which
    matters because the alternative is re-sending everything it had already got through — and
    a receiver that does not honor the idempotency key would show the duplicates.
    """
    moment = now or dt.datetime.now(tz=dt.UTC)
    reports: list[DrainReport] = []
    for _ in range(max_passes):
        async with database.session() as session:
            report = await AlertService(session, settings.alert_policy).dispatch(now=moment)
            await session.commit()
        if report.attempted == 0 and report.unroutable == 0:
            break
        reports.append(report)
        if not report.has_more:
            break
    return DrainSummary(passes=tuple(reports))


@dataclass(frozen=True, slots=True)
class GraphRebuildSummary:
    """What one rebuild of the privilege graph derived, and whether the graph is now current."""

    outcome: ProjectionOutcome
    discarded_existing: bool
    underived_runs: int
    runs_in_progress: int

    @property
    def needs_attention(self) -> bool:
        """Units the rules could not read are a defect somewhere upstream, worth a look."""
        return bool(self.outcome.skipped)

    @property
    def lines(self) -> tuple[str, ...]:
        outcome = self.outcome
        head = (
            f"Graph pass {outcome.pass_id}: {outcome.units_examined} units, "
            f"{outcome.relationships_created} relationships created, "
            f"{outcome.count(RecordOutcome.CREATED)} supports created, "
            f"{outcome.count(RecordOutcome.EXTENDED)} extended, "
            f"{outcome.count(RecordOutcome.REPLAYED)} unchanged, "
            f"{outcome.count(RecordOutcome.REINSTATED)} reinstated, "
            f"{outcome.supports_retired} retired."
        )
        mode = (
            "Mode: clean recomputation; every derived relationship was discarded first."
            if self.discarded_existing
            else "Mode: in place; unchanged facts re-derive to the same supports."
        )
        coverage = (
            f"Runs not yet derived: {self.underived_runs} completed, "
            f"{self.runs_in_progress} still collecting."
        )
        skipped = [
            f"  - skipped {skip.kind.value} {skip.key!r}: {skip.reason}"
            for skip in outcome.skipped[:50]
        ]
        return (head, mode, coverage, *skipped)


async def rebuild_graph(
    database: Database, *, discard_existing: bool = False, now: dt.datetime | None = None
) -> GraphRebuildSummary:
    """Re-derive every privilege relationship from the facts stored now, in one transaction.

    Reads collected facts and writes only ADG's derived graph tables. A failure rolls the whole
    pass back, so the graph is either the old one or the rebuilt one, never half of each.
    """
    moment = now or dt.datetime.now(tz=dt.UTC)
    async with database.session() as session:
        projector = RelationshipProjector(session)
        outcome = await projector.rebuild(now=moment, discard_existing=discard_existing)
        await session.commit()
        state = await projector.state()
    logger.info("operations.graph.rebuilt", extra={"summary": outcome.summary})
    return GraphRebuildSummary(
        outcome=outcome,
        discarded_existing=discard_existing,
        underived_runs=len(state.underived_runs),
        runs_in_progress=len(state.runs_in_progress),
    )
