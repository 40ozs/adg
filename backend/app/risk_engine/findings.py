"""What a rule reports, and what the engine turns it into.

Two types, and the difference between them is the whole design.

:class:`RuleOutcome` is what a rule produces: *this shape is present, here, and these are the
records it is present in*. It carries no severity, because severity is configuration; no
confidence, because confidence is derived from the qualifiers; and no identity, because
identity is a digest the engine computes the same way for every rule. A rule that could set
any of those three could quietly disagree with every other rule about what they mean.

:class:`RiskFinding` is what the engine produces from an outcome plus the configuration: the
same facts with a severity assigned, a confidence derived, and a stable key that says *which
finding this is* across evaluations. The key is what makes resolve-and-reopen possible — the
same shape in the same place produces the same key next week, so the engine can tell "still
there" from "new" without comparing prose.

**The key deliberately excludes the rule version.** A rule whose predicate is corrected
should re-examine the findings it already made, not orphan them: if the shape is still there
the finding stays open under the new version, and if it is not the finding resolves. Putting
the version in the key would resolve every finding and immediately open a duplicate of each,
which reads in a report as an estate that changed overnight when nothing changed at all.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from app.domain.errors import DomainValidationError
from app.risk_engine.catalog import Remediation, RuleDefinition, RuleId, SubjectKind, definition_for
from app.risk_engine.evidence import Evidence
from app.risk_engine.facts import RiskScope
from app.risk_engine.severity import (
    SEVERITY_ORDER,
    Confidence,
    FactQualifier,
    Severity,
    SeverityBand,
    confidence_of,
)

__all__ = [
    "FINDING_KEY_LENGTH",
    "FindingSubject",
    "RiskFinding",
    "RuleOutcome",
    "finding_key",
    "scope_covers",
]

FINDING_KEY_LENGTH: Final = 64
"""Characters in a finding key: SHA-256, hex."""

_SEPARATOR: Final = "\x1f"
"""ASCII unit separator. Used between key parts because it cannot occur in a SID, a UNC path,
a host name or a rule identifier, so two different subjects cannot render to one string by
one of them containing the separator."""


@dataclass(frozen=True, slots=True)
class FindingSubject:
    """What a finding is about: a place, an identity, or a pairing of the two.

    At least one key is required. ``discriminator`` separates several findings of one rule
    about one subject — the trustee an entry names, for instance, when the rule's subject is
    the resource — and is part of the identity, so two entries on one DACL produce two
    findings that resolve independently.
    """

    resource_key: str | None = None
    share_key: str | None = None
    principal_key: str | None = None
    discriminator: str | None = None

    def __post_init__(self) -> None:
        if not any((self.resource_key, self.share_key, self.principal_key)):
            raise DomainValidationError(
                "A finding must name at least one of a resource, a share or a principal. A "
                "finding about nothing in particular cannot be acted on or resolved.",
                field="subject",
            )

    @property
    def canonical(self) -> str:
        """The rendering the finding key is computed over. Order is fixed, blanks included."""
        return _SEPARATOR.join(
            part or ""
            for part in (
                self.resource_key,
                self.share_key,
                self.principal_key,
                self.discriminator,
            )
        )

    @property
    def primary_kind(self) -> SubjectKind:
        """What this subject most specifically is, for grouping a report."""
        has_place = bool(self.resource_key or self.share_key)
        if has_place and self.principal_key:
            return SubjectKind.ACCESS
        return SubjectKind.RESOURCE if has_place else SubjectKind.PRINCIPAL


def finding_key(rule_id: RuleId, subject: FindingSubject) -> str:
    """The stable identity of one finding.

    A pure function of the rule and the subject, so the same shape in the same place produces
    the same key on every evaluation, on every machine. Not of the evidence: evidence changes
    when an unrelated entry is added to the same DACL, and a finding that changed identity
    every time its neighbor changed could never stay open.
    """
    material = f"{rule_id.value}{_SEPARATOR}{subject.canonical}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def scope_covers(scope: RiskScope, subject: FindingSubject) -> bool:
    """Whether an evaluation over ``scope`` actually examined ``subject``.

    **Every** key the subject names must be covered, not any of them. That asymmetry is the
    whole safety of incremental re-evaluation, and it points the only direction it safely can:

    * requiring *all* keys can leave a finding open that should have resolved, which a later
      full pass corrects and which in the meantime reports a risk that is already gone;
    * requiring *any* key would close findings the pass never examined, which reports a risk
      as fixed when nothing was done about it.

    The first error wastes somebody's afternoon. The second ends an access review with a clean
    report over an exposure that is still there.

    ``discriminator`` is not a key and is not tested: it distinguishes findings within one
    subject rather than naming a further object that would have to be loaded.
    """
    if scope.complete:
        return True
    checks = (
        (subject.resource_key, scope.covers_resource),
        (subject.share_key, scope.covers_share),
        (subject.principal_key, scope.covers_principal),
    )
    named = [(key, covers) for key, covers in checks if key is not None]
    if not named:  # pragma: no cover - FindingSubject requires at least one key
        return False
    return all(covers(key) for key, covers in named)


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """One match a rule reports. Facts only.

    ``detail`` holds the values a renderer needs to write the finding's sentence — the
    category the grant reached, the depth of the chain, the label of the sensitivity tag. It
    is **never** read back as an input by anything; the evidence is the input, and a
    reproduction ignores ``detail`` entirely. Keeping the two apart is what stops a rendering
    convenience from quietly becoming part of the logic.
    """

    subject: FindingSubject
    band: SeverityBand
    evidence: Evidence
    qualifiers: frozenset[FactQualifier] = frozenset()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.evidence:
            raise DomainValidationError(
                "A rule outcome must cite the records it matched. A finding with no evidence "
                "cannot be checked, cannot be reproduced, and is an assertion rather than a "
                "finding.",
                field="evidence",
            )
        object.__setattr__(self, "detail", dict(self.detail))


@dataclass(frozen=True, slots=True)
class RiskFinding:
    """One finding: the shape, where it is, how bad it is, and how sure ADG is."""

    rule_id: RuleId
    rule_version: str
    key: str
    subject: FindingSubject
    band: SeverityBand
    severity: Severity
    confidence: Confidence
    qualifiers: tuple[FactQualifier, ...]
    evidence: Evidence
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = finding_key(self.rule_id, self.subject)
        if self.key != expected:
            raise DomainValidationError(
                f"Finding key {self.key!r} does not match rule {self.rule_id.value!r} and its "
                f"subject, which digest to {expected!r}. A key computed any other way would "
                "stop the same finding matching itself between evaluations.",
                field="key",
            )
        derived = confidence_of(self.qualifiers)
        if self.confidence is not derived:
            raise DomainValidationError(
                f"Finding confidence {self.confidence.value!r} is not what its qualifiers "
                f"support ({derived.value!r}). Confidence is derived, never chosen.",
                field="confidence",
            )
        object.__setattr__(self, "detail", dict(self.detail))

    @classmethod
    def build(
        cls,
        outcome: RuleOutcome,
        *,
        definition: RuleDefinition,
        severity: Severity,
    ) -> RiskFinding:
        """Assemble a finding from a rule's outcome and the severity configuration assigned."""
        return cls(
            rule_id=definition.id,
            rule_version=definition.version,
            key=finding_key(definition.id, outcome.subject),
            subject=outcome.subject,
            band=outcome.band,
            severity=severity,
            confidence=confidence_of(outcome.qualifiers),
            qualifiers=tuple(sorted(outcome.qualifiers, key=lambda item: item.value)),
            evidence=outcome.evidence,
            detail=outcome.detail,
        )

    @property
    def definition(self) -> RuleDefinition:
        return definition_for(self.rule_id)

    @property
    def title(self) -> str:
        return self.definition.title

    @property
    def remediation(self) -> Remediation:
        return self.definition.remediation

    @property
    def evidence_digest(self) -> str:
        return self.evidence.digest

    @property
    def sort_key(self) -> tuple[int, int, str, str]:
        """Most severe first, most certain first, then stable by rule and key.

        Severity descends and confidence descends, so the first page of a report is the
        findings that are both consequential and well evidenced. A confident low finding
        outranking an uncertain critical one would be the wrong order for an operator with an
        afternoon; this order is the one an afternoon should be spent in.
        """
        return (
            -SEVERITY_ORDER[self.severity],
            -_CONFIDENCE_RANK[self.confidence],
            self.rule_id.value,
            self.key,
        )


_CONFIDENCE_RANK: Final[dict[Confidence, int]] = {
    Confidence.POSSIBLE: 0,
    Confidence.PROBABLE: 1,
    Confidence.CONFIRMED: 2,
}
