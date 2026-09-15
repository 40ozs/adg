"""Running the rules, and checking that a finding can be re-derived from its own evidence.

Two entry points.

:func:`evaluate` runs every enabled rule over a fact bundle and returns the findings in a
deterministic order, along with the account of which rules ran — because "no findings" and
"that rule was switched off" are different answers and a report that renders them the same
way is telling somebody their estate is clean when nobody looked.

:func:`reproduce` takes a finding, rebuilds a fact bundle from its evidence alone, re-runs the
single rule that produced it, and returns what came back. It is the check behind the phase's
central claim: *every finding can be reproduced from its evidence*. It is exercised as a test
per rule, and it is available at runtime too, because the question an auditor asks about a
six-month-old finding — "was this actually true, on the evidence you kept?" — is exactly this
function.

**Reproduction runs one rule, not all of them.** A finding is the output of a particular
predicate over particular records; running the other ten rules over a bundle assembled for the
first would ask them about records deliberately reduced to what one rule needed, and the
answers would be about the reduction rather than the estate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.risk_engine.catalog import CATALOG, RuleId
from app.risk_engine.configuration import DEFAULT_CONFIGURATION, RiskConfiguration
from app.risk_engine.evidence import rebuild_facts
from app.risk_engine.facts import FactKind, RiskFacts, RiskScope
from app.risk_engine.findings import RiskFinding
from app.risk_engine.rules import RULES

__all__ = [
    "EvaluationResult",
    "ReproductionReport",
    "RuleRun",
    "evaluate",
    "reproduce",
    "reproduce_all",
    "rules_affected_by",
]


@dataclass(frozen=True, slots=True)
class RuleRun:
    """What one rule did on one evaluation."""

    rule_id: RuleId
    ran: bool
    findings: int = 0
    skipped_because: str | None = None

    def __post_init__(self) -> None:
        if self.ran and self.skipped_because is not None:
            raise ValueError("A rule that ran has no reason for not running.")
        if not self.ran and self.skipped_because is None:
            raise ValueError(
                "A rule that did not run must say why. A silently skipped rule is an empty "
                "section of a report that reads as a clean one."
            )


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Every finding one pass produced, and the account of how it was produced."""

    findings: tuple[RiskFinding, ...]
    runs: tuple[RuleRun, ...]
    scope: RiskScope
    configuration_version: str

    @property
    def rules_run(self) -> tuple[RuleId, ...]:
        return tuple(run.rule_id for run in self.runs if run.ran)

    @property
    def rules_skipped(self) -> tuple[RuleId, ...]:
        return tuple(run.rule_id for run in self.runs if not run.ran)

    def by_rule(self, rule_id: RuleId) -> tuple[RiskFinding, ...]:
        return tuple(finding for finding in self.findings if finding.rule_id is rule_id)

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(finding.key for finding in self.findings)


_DISABLED: Final = "disabled by configuration"
_NO_DEPENDENCY: Final = "no fact it depends on changed"


def rules_affected_by(changed: Iterable[FactKind]) -> tuple[RuleId, ...]:
    """The rules that could produce a different answer after a change of these kinds.

    The whole of the incremental story, in one function. A rule declares which collected
    kinds it reads (:attr:`RuleDefinition.depends_on`); a run that changed nothing of a kind a
    rule reads cannot have changed that rule's answer, so the rule is not re-run.

    Passing no kinds returns nothing, deliberately: a caller with an empty change set is
    asking *what changed* and the answer is nothing, not everything. A full evaluation is
    requested by calling :func:`evaluate` with a complete scope, which is a different thing
    and reads differently at the call site.
    """
    kinds = frozenset(changed)
    if not kinds:
        return ()
    return tuple(
        rule_id for rule_id, definition in CATALOG.items() if definition.depends_on & kinds
    )


def evaluate(
    facts: RiskFacts,
    configuration: RiskConfiguration = DEFAULT_CONFIGURATION,
    *,
    rules: Sequence[RuleId] | None = None,
) -> EvaluationResult:
    """Run the rules over ``facts``.

    Args:
        facts: what the rules may read. Its :attr:`RiskFacts.scope` is carried through to the
            result, because what an evaluation *covered* is what a caller reconciling
            findings against it is allowed to resolve.
        configuration: severities, thresholds, enablement and the sensitivity tags.
        rules: run only these, in catalog order. ``None`` runs every enabled rule. A rule
            named here that configuration has disabled stays disabled — an explicit list
            narrows what runs and never overrides the operator's own switch.

    Returns:
        Findings ordered most severe first (see :attr:`RiskFinding.sort_key`), and one
        :class:`RuleRun` per rule in the catalog, including the ones that did not run.
    """
    requested = None if rules is None else frozenset(rules)
    findings: list[RiskFinding] = []
    runs: list[RuleRun] = []

    for rule_id in CATALOG:
        config = configuration.for_rule(rule_id)
        if not config.enabled:
            runs.append(RuleRun(rule_id, ran=False, skipped_because=_DISABLED))
            continue
        if requested is not None and rule_id not in requested:
            runs.append(RuleRun(rule_id, ran=False, skipped_because=_NO_DEPENDENCY))
            continue

        produced = 0
        for outcome in RULES[rule_id].evaluate(facts, config):
            findings.append(
                RiskFinding.build(
                    outcome,
                    definition=config.definition,
                    severity=config.severity_for(outcome.band),
                )
            )
            produced += 1
        runs.append(RuleRun(rule_id, ran=True, findings=produced))

    return EvaluationResult(
        findings=tuple(sorted(findings, key=lambda item: item.sort_key)),
        runs=tuple(runs),
        scope=facts.scope,
        configuration_version=configuration.version,
    )


def reproduce(
    finding: RiskFinding,
    configuration: RiskConfiguration = DEFAULT_CONFIGURATION,
) -> RiskFinding | None:
    """Re-derive ``finding`` from its own evidence, or ``None`` if the evidence is not enough.

    The facts are rebuilt from the evidence and nothing else — no database, no other finding,
    nothing the caller happens to have in hand — and the one rule that produced the finding is
    re-run over them. A finding whose key matches is returned; anything else returns ``None``.

    What a ``None`` means is worth being precise about, because there are two quite different
    causes and both are worth catching:

    * the rule read a record it did not cite, so the rebuilt facts are missing something and
      the predicate no longer matches; or
    * the rule is not a pure function of the facts it cites — it depends on something about
      the surrounding bundle, which makes the finding unreproducible in principle.

    The returned finding's severity comes from ``configuration``, so reproducing under
    different settings can legitimately return a finding with a different severity and the
    same key. That is the correct behavior: the shape was there, and what it is worth is a
    local decision that may since have changed.
    """
    rule = RULES.get(finding.rule_id)
    if rule is None:  # pragma: no cover - RuleId is closed and RULES is exhaustive
        return None
    config = configuration.for_rule(finding.rule_id)
    rebuilt = rebuild_facts(finding.evidence)
    for outcome in rule.evaluate(rebuilt, config):
        candidate = RiskFinding.build(
            outcome, definition=config.definition, severity=config.severity_for(outcome.band)
        )
        if candidate.key == finding.key:
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class ReproductionReport:
    """How much of a set of findings survived being re-derived from its own evidence.

    Built by :func:`reproduce_all` for an operator who wants the answer over a whole report
    rather than one row. ``unreproduced`` is the part worth looking at, and it is a list of
    keys rather than a count so that the answer to "which ones" does not require a second
    pass.
    """

    total: int
    reproduced: tuple[str, ...] = ()
    unreproduced: tuple[str, ...] = ()
    changed_severity: Mapping[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.unreproduced


def reproduce_all(
    findings: Iterable[RiskFinding],
    configuration: RiskConfiguration = DEFAULT_CONFIGURATION,
) -> ReproductionReport:
    """Reproduce every finding, and report which ones did not come back."""
    reproduced: list[str] = []
    unreproduced: list[str] = []
    changed: dict[str, tuple[str, str]] = {}
    total = 0
    for finding in findings:
        total += 1
        again = reproduce(finding, configuration)
        if again is None:
            unreproduced.append(finding.key)
            continue
        reproduced.append(finding.key)
        if again.severity is not finding.severity:
            changed[finding.key] = (finding.severity.value, again.severity.value)
    return ReproductionReport(
        total=total,
        reproduced=tuple(reproduced),
        unreproduced=tuple(unreproduced),
        changed_severity=changed,
    )
