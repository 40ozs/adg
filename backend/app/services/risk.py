"""Running the risk rules against the estate, and keeping the findings honest over time.

Three ways to run an evaluation, and the differences between them are entirely about
**scope** — what the pass loaded, and therefore what it is entitled to conclude:

* :meth:`RiskService.evaluate_estate` loads everything and may resolve anything. It is the
  pass an operator runs to get a report they can act on.
* :meth:`RiskService.evaluate_run` loads the facts around what one scan run changed, and runs
  only the rules that read a kind of fact that moved. It may resolve only findings whose every
  subject it loaded. This is what keeps a report current between full passes without paying
  for a full pass every time a collector reports.
* :meth:`RiskService.evaluate_subject` re-checks one directory or share, for somebody looking
  at it now.

**The three cannot differ in what they conclude about the same facts**, only in how much they
look at, because all three call the same :func:`app.risk_engine.evaluate` over the same fact
types. A separate "quick" evaluation path would be a second implementation of the eleven
rules, and the first time it disagreed with the full pass nobody would know which was right.

Nothing here decides severity, matches a rule, or interprets an access mask. The service
loads, delegates, and reconciles; every judgment is in :mod:`app.risk_engine` and every
judgment about *access* is in :mod:`app.access_engine` beneath that.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.models.schema import RiskEvaluationTrigger
from app.repositories.risk import (
    ChangedFacts,
    FactLoad,
    ReconcileOutcome,
    RiskFactsRepository,
    RiskFindingRepository,
    StoredFinding,
    rebuild_finding,
)
from app.risk_engine import (
    DEFAULT_CONFIGURATION,
    EvaluationResult,
    RiskConfiguration,
    RiskFinding,
    RuleId,
    evaluate,
    reproduce,
    rules_affected_by,
)

__all__ = ["RiskEvaluation", "RiskService"]


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    """One completed pass: what it found, what it changed, and what it did not look at."""

    evaluation_id: UUID
    trigger: RiskEvaluationTrigger
    result: EvaluationResult
    outcome: ReconcileOutcome
    load: FactLoad
    started_at: dt.datetime
    completed_at: dt.datetime

    @property
    def findings(self) -> tuple[RiskFinding, ...]:
        return self.result.findings

    @property
    def complete(self) -> bool:
        """Whether this pass covered the whole estate with no ceiling reached.

        Read it before presenting a count as a total. An incremental pass that opened two
        findings has not established that there are two findings in the estate, and a report
        that renders the two the same way invites exactly that reading.
        """
        return self.result.scope.complete and self.load.complete

    @property
    def summary(self) -> str:
        """One line for an operator log, in American English."""
        return (
            f"{self.trigger.value} evaluation {self.evaluation_id}: "
            f"{len(self.result.findings)} matched, {len(self.outcome.opened)} opened, "
            f"{len(self.outcome.reopened)} reopened, {len(self.outcome.resolved)} resolved, "
            f"{len(self.outcome.out_of_scope)} left undecided"
        )


class RiskService:
    """Evaluates the risk rules over one database session."""

    def __init__(
        self,
        facts: RiskFactsRepository,
        findings: RiskFindingRepository,
        configuration: RiskConfiguration = DEFAULT_CONFIGURATION,
    ) -> None:
        self._facts = facts
        self._findings = findings
        self._configuration = configuration

    @property
    def configuration(self) -> RiskConfiguration:
        return self._configuration

    # -- the three passes -------------------------------------------------------------

    async def evaluate_estate(self, *, now: dt.datetime) -> RiskEvaluation:
        """Run every enabled rule over the whole estate."""
        load = await self._facts.load()
        return await self._run(
            load,
            trigger=RiskEvaluationTrigger.FULL,
            rules=None,
            now=now,
        )

    async def evaluate_run(self, run_id: UUID, *, now: dt.datetime) -> RiskEvaluation:
        """Re-evaluate what one scan run changed.

        Two narrowings, and they are independent:

        * **which facts are loaded** — the directories, shares and principals the run moved,
          plus the resources whose access control lists name a principal it moved; and
        * **which rules run** — only those that read a kind of fact that actually changed
          (:func:`app.risk_engine.rules_affected_by`).

        A run that changed nothing still records an evaluation. It records one with an empty
        scope, which resolves nothing and reports nothing, and that row is the evidence that
        the run was considered — an absent row and a run nobody evaluated look identical.
        """
        changed: ChangedFacts = await self._facts.changed_by_run(run_id)
        rules = rules_affected_by(changed.kinds)
        load = await self._facts.load(
            resource_keys=sorted(changed.resource_keys),
            share_keys=sorted(changed.share_keys),
        )
        return await self._run(
            load,
            trigger=RiskEvaluationTrigger.INCREMENTAL,
            rules=rules,
            now=now,
            source_run_id=run_id,
        )

    async def evaluate_subject(
        self,
        *,
        resource_keys: Sequence[str] = (),
        share_keys: Sequence[str] = (),
        now: dt.datetime,
    ) -> RiskEvaluation:
        """Re-check the named directories and shares, and nothing else."""
        load = await self._facts.load(
            resource_keys=list(resource_keys), share_keys=list(share_keys)
        )
        return await self._run(load, trigger=RiskEvaluationTrigger.TARGETED, rules=None, now=now)

    async def _run(
        self,
        load: FactLoad,
        *,
        trigger: RiskEvaluationTrigger,
        rules: Sequence[RuleId] | None,
        now: dt.datetime,
        source_run_id: UUID | None = None,
    ) -> RiskEvaluation:
        scope = load.facts.scope
        evaluation_id = await self._findings.start_evaluation(
            trigger=trigger,
            scope=scope,
            configuration_version=self._configuration.version,
            started_at=now,
            source_run_id=source_run_id,
        )
        result = evaluate(load.facts, self._configuration, rules=rules)
        outcome = await self._findings.reconcile(
            evaluation_id=evaluation_id,
            now=now,
            findings=result.findings,
            scope=scope,
            rules_run=result.rules_run,
        )
        await self._findings.complete_evaluation(
            evaluation_id,
            completed_at=now,
            outcome=outcome,
            rules_run=result.rules_run,
            rules_skipped=result.rules_skipped,
            # The truncation notes go on the evaluation row rather than being dropped. A pass
            # that ran out of room and said nothing about it is a pass whose empty sections
            # read as clean ones.
            error="; ".join(load.truncated) or None,
        )
        return RiskEvaluation(
            evaluation_id=evaluation_id,
            trigger=trigger,
            result=result,
            outcome=outcome,
            load=load,
            started_at=now,
            completed_at=now,
        )

    # -- reading back -----------------------------------------------------------------

    async def open_findings(
        self, *, rules: Sequence[RuleId] | None = None
    ) -> tuple[StoredFinding, ...]:
        """Every open finding, most severe first."""
        stored = await self._findings.open_findings(rules=rules)
        return tuple(sorted(stored.values(), key=lambda item: rebuild_finding(item).sort_key))

    async def get(self, key: str) -> StoredFinding | None:
        return await self._findings.get(key)

    async def verify(self, key: str) -> RiskFinding | None:
        """Re-derive a stored finding from the evidence stored with it.

        The question an auditor asks about a finding from last quarter — *was this actually
        true, on the evidence you kept?* — answered without reading the estate at all. The
        facts are rebuilt from the evidence blob and the rule is re-run over them
        (:func:`app.risk_engine.reproduce`).

        ``None`` means the stored evidence no longer re-derives the finding. That is a
        statement about ADG, not about the estate: either the evidence was insufficient when
        it was written, or the rule's predicate has since changed in a way that no longer
        matches what it matched then. Both are worth knowing and neither is visible from the
        finding itself.
        """
        record = await self._findings.get(key)
        if record is None:
            return None
        return reproduce(rebuild_finding(record), self._configuration)
