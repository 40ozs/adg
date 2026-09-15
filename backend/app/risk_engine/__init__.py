"""Deterministic, explainable risk rules over collected access facts.

Everything ADG reports as a risk is produced here, by a named rule, from records it cites.
There is no score, no model and no heuristic anywhere in this package, and that is the design
rather than a limitation of it: an access finding that cannot be checked is an access finding
nobody can act on, and one that cannot be *re*-checked six months later is one nobody should.

The package stacks in one direction, and no module imports one below it:

* :mod:`app.risk_engine.severity` — the two axes. Severity is configuration; confidence is
  derived from the facts and never chosen.
* :mod:`app.risk_engine.facts` — the typed snapshot a rule is evaluated over. Framework-free:
  no FastAPI, no SQLAlchemy, no I/O, no clock.
* :mod:`app.risk_engine.evidence` — the records behind a finding, complete enough to rebuild
  the facts and re-derive it.
* :mod:`app.risk_engine.catalog` — what each rule is, what it is worth by default, and what to
  do about it. Data, held apart from the predicates.
* :mod:`app.risk_engine.configuration` — what an installation may change, and the one thing it
  must supply: which resources are sensitive.
* :mod:`app.risk_engine.findings` — what a rule reports, and what the engine makes of it.
* :mod:`app.risk_engine.rules` — the eleven predicates.
* :mod:`app.risk_engine.engine` — running them, and reproducing a finding from its evidence.

**Risk never replaces permission truth.** A finding is a statement *about* the permission
facts, computed from them, carrying them; it is never stored in their place and never
consulted instead of them. "Who can reach this directory" is answered by
:mod:`app.access_engine` over what was collected, before and after any rule runs, and the
rules here call into that same access check rather than keeping a second opinion about what an
access mask means. See ADR-0023.

Persistence, fact loading and re-evaluation live in :mod:`app.services.risk` and
:mod:`app.repositories.risk`. Nothing in this package touches a database.
"""

from __future__ import annotations

from app.risk_engine.catalog import (
    CATALOG,
    Remediation,
    RuleDefinition,
    RuleId,
    SubjectKind,
    definition_for,
)
from app.risk_engine.configuration import (
    DEFAULT_CONFIGURATION,
    ConfigurationError,
    RiskConfiguration,
    RuleConfig,
    SensitiveResource,
    describe_configuration,
    load_configuration,
    merge_option_overrides,
    only_rules,
    parse_configuration,
)
from app.risk_engine.engine import (
    EvaluationResult,
    ReproductionReport,
    RuleRun,
    evaluate,
    reproduce,
    reproduce_all,
    rules_affected_by,
)
from app.risk_engine.evidence import (
    EVIDENCE_DIGEST_LENGTH,
    Evidence,
    EvidenceItem,
    EvidenceKind,
    rebuild_facts,
)
from app.risk_engine.facts import (
    MAX_WALK_DEPTH,
    AceFacts,
    AclProvenanceFacts,
    Chain,
    FactKind,
    MembershipFacts,
    PrincipalFacts,
    ResourceFacts,
    RiskFacts,
    RiskScope,
    ShareFacts,
    domain_relative_trustee,
)
from app.risk_engine.findings import (
    FINDING_KEY_LENGTH,
    FindingSubject,
    RiskFinding,
    RuleOutcome,
    finding_key,
    scope_covers,
)
from app.risk_engine.rules import RULES, Rule, band_for, rule_for
from app.risk_engine.severity import (
    CONFIDENCE_ORDER,
    QUALIFIER_CONFIDENCE,
    QUALIFIER_DESCRIPTIONS,
    SEVERITY_ORDER,
    Confidence,
    FactQualifier,
    Severity,
    SeverityBand,
    confidence_of,
    describe_qualifier,
)

__all__ = [
    "CATALOG",
    "CONFIDENCE_ORDER",
    "DEFAULT_CONFIGURATION",
    "EVIDENCE_DIGEST_LENGTH",
    "FINDING_KEY_LENGTH",
    "MAX_WALK_DEPTH",
    "QUALIFIER_CONFIDENCE",
    "QUALIFIER_DESCRIPTIONS",
    "RULES",
    "SEVERITY_ORDER",
    "AceFacts",
    "AclProvenanceFacts",
    "Chain",
    "Confidence",
    "ConfigurationError",
    "EvaluationResult",
    "Evidence",
    "EvidenceItem",
    "EvidenceKind",
    "FactKind",
    "FactQualifier",
    "FindingSubject",
    "MembershipFacts",
    "PrincipalFacts",
    "Remediation",
    "ReproductionReport",
    "ResourceFacts",
    "RiskConfiguration",
    "RiskFacts",
    "RiskFinding",
    "RiskScope",
    "Rule",
    "RuleConfig",
    "RuleDefinition",
    "RuleId",
    "RuleOutcome",
    "RuleRun",
    "SensitiveResource",
    "Severity",
    "SeverityBand",
    "ShareFacts",
    "SubjectKind",
    "band_for",
    "confidence_of",
    "definition_for",
    "describe_configuration",
    "describe_qualifier",
    "domain_relative_trustee",
    "evaluate",
    "finding_key",
    "load_configuration",
    "merge_option_overrides",
    "only_rules",
    "parse_configuration",
    "rebuild_facts",
    "reproduce",
    "reproduce_all",
    "rule_for",
    "rules_affected_by",
    "scope_covers",
]
