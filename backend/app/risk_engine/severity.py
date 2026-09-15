"""How bad a finding is, and how sure ADG is that it is real.

These are two independent axes and they are kept apart on purpose.

**Severity** is a judgment about consequence — *Everyone with Modify on a file share is worse
than a stale direct ACE* — and it is therefore **configuration**, not code. A rule never
computes a severity. It reports a :class:`SeverityBand`, a short label naming which shape of
the finding it saw, and :class:`app.risk_engine.configuration.RuleConfig` maps that band to a
severity. An installation that considers a direct user ACE a formality lowers it in one place,
and no rule body changes.

**Confidence** is a statement about ADG's own evidence — *was every fact this rule read
observed directly, or was some of it projected from an ancestor or cut short by a traversal
limit?* — and it is therefore **derived**, never configurable. It is computed from the closed
set of :class:`FactQualifier` values the rule attaches to its outcome, and it is always the
weakest that any one qualifier allows.

The distinction matters because the two mistakes it prevents look identical in a report.
Raising confidence to make a finding look actionable claims evidence ADG does not have.
Lowering severity to quiet a noisy rule hides a real exposure. Neither can be done by
accident here: one is a configuration file, the other is a pure function of the facts.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Final

__all__ = [
    "CONFIDENCE_ORDER",
    "QUALIFIER_CONFIDENCE",
    "QUALIFIER_DESCRIPTIONS",
    "SEVERITY_ORDER",
    "Confidence",
    "FactQualifier",
    "Severity",
    "SeverityBand",
    "confidence_of",
    "describe_qualifier",
]


class Severity(StrEnum):
    """How consequential a finding is. Assigned by configuration, never by a rule."""

    INFORMATIONAL = "informational"
    """Worth knowing; not by itself a defect. The shape an auditor asks about."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    CRITICAL = "critical"
    """Broad write or control access that almost certainly should not exist."""


SEVERITY_ORDER: Final[dict[Severity, int]] = {
    Severity.INFORMATIONAL: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
"""Rank, for sorting a report. Never for arithmetic: severities do not add up.

A "risk score" summed across findings would be a number with no unit that a reader would
nevertheless compare between two shares. ADG ranks and counts; it does not score. See
ADR-0023.
"""


class Confidence(StrEnum):
    """How sure ADG is that the facts behind a finding are what it thinks they are.

    Never a statement about whether the *rule* is right — a rule is a deterministic predicate
    and either matched or did not. This is a statement about its inputs.
    """

    CONFIRMED = "confirmed"
    """Every fact the rule read was observed directly by a collector and is complete."""

    PROBABLE = "probable"
    """A fact the rule read was derived rather than read — most often a DACL projected from
    an ancestor because the directory's own descriptor was never fetched. The finding
    describes what Windows would produce by inheritance, which is right unless somebody set
    permissions on the directory in between."""

    POSSIBLE = "possible"
    """A fact the rule read is bounded or partial — a truncated traversal, a group whose
    membership no run enumerated. The finding may be real and the evidence cannot settle it."""


CONFIDENCE_ORDER: Final[dict[Confidence, int]] = {
    Confidence.POSSIBLE: 0,
    Confidence.PROBABLE: 1,
    Confidence.CONFIRMED: 2,
}
"""Rank, weakest first. :func:`confidence_of` takes the minimum over this order."""


class FactQualifier(StrEnum):
    """Something about a fact the rule read that keeps it short of directly observed.

    A closed vocabulary, deliberately small, and deliberately overlapping with
    :class:`app.access_engine.AccessCondition` in meaning rather than importing it: the
    access engine's conditions qualify *one principal's rights*, and these qualify *the facts
    a rule matched on*. Mapping the former onto the latter is the service layer's job, and
    doing it by hand there is what keeps the risk engine free of the access engine's types.
    """

    ACL_DERIVED = "acl_derived"
    """The resource's DACL was projected from an ancestor rather than read."""

    ACL_DERIVED_DISTANT = "acl_derived_distant"
    """The projection came from more than one level up, so any directory in between could
    carry permissions nobody has read."""

    ACE_COUNT_SHORT = "ace_count_short"
    """The descriptor declared more entries than ADG holds, so the rule matched over part of
    a DACL and cannot have seen a Deny that is missing."""

    MEMBERSHIP_TRUNCATED = "membership_truncated"
    """A membership traversal hit a limit. Any chain the rule walked is real; chains it did
    not walk may exist."""

    MEMBERSHIP_UNOBSERVED = "membership_unobserved"
    """A group the rule reasoned about has no collected membership at all."""

    PRINCIPAL_UNDESCRIBED = "principal_undescribed"
    """A principal the rule named has no stored record, so its kind and status are unknown."""


QUALIFIER_CONFIDENCE: Final[dict[FactQualifier, Confidence]] = {
    FactQualifier.ACL_DERIVED: Confidence.PROBABLE,
    FactQualifier.ACL_DERIVED_DISTANT: Confidence.POSSIBLE,
    FactQualifier.ACE_COUNT_SHORT: Confidence.POSSIBLE,
    FactQualifier.MEMBERSHIP_TRUNCATED: Confidence.POSSIBLE,
    FactQualifier.MEMBERSHIP_UNOBSERVED: Confidence.POSSIBLE,
    FactQualifier.PRINCIPAL_UNDESCRIBED: Confidence.PROBABLE,
}
"""The strongest confidence each qualifier permits.

``ACL_DERIVED`` stays ``PROBABLE`` because one level of inheritance is exactly what Windows
computes and ADG computes it with the same rules (:mod:`app.domain.inheritance`).
``ACL_DERIVED_DISTANT`` drops to ``POSSIBLE`` because every unread level in between is a
place inheritance could have been broken, and a broken level makes the projection fiction.
"""

QUALIFIER_DESCRIPTIONS: Final[dict[FactQualifier, str]] = {
    FactQualifier.ACL_DERIVED: (
        "This directory's own security descriptor was never read. Its permissions were "
        "projected from the nearest ancestor that was."
    ),
    FactQualifier.ACL_DERIVED_DISTANT: (
        "The permissions were projected from more than one level above this directory. Any "
        "directory in between could carry its own permissions that no run has read."
    ),
    FactQualifier.ACE_COUNT_SHORT: (
        "The security descriptor declared more entries than ADG holds, so this finding was "
        "evaluated over part of the access control list."
    ),
    FactQualifier.MEMBERSHIP_TRUNCATED: (
        "A group expansion reached its limit. The membership this finding names is real; "
        "further memberships may exist that it does not name."
    ),
    FactQualifier.MEMBERSHIP_UNOBSERVED: (
        "No run has collected the membership of a group this finding reasons about."
    ),
    FactQualifier.PRINCIPAL_UNDESCRIBED: (
        "No run has described a principal this finding names, so its kind and account "
        "status are unknown."
    ),
}


def describe_qualifier(qualifier: FactQualifier) -> str:
    """A sentence an operator can read, in American English."""
    return QUALIFIER_DESCRIPTIONS[qualifier]


def confidence_of(qualifiers: Iterable[FactQualifier]) -> Confidence:
    """The weakest confidence any of ``qualifiers`` permits; ``CONFIRMED`` when there are none.

    Weakest rather than an average, and certainly rather than a majority: one projected DACL
    makes the whole finding rest on a projection, and reporting it as confirmed because four
    other facts were observed would be arithmetic standing in for evidence.
    """
    weakest = Confidence.CONFIRMED
    for qualifier in qualifiers:
        candidate = QUALIFIER_CONFIDENCE[qualifier]
        if CONFIDENCE_ORDER[candidate] < CONFIDENCE_ORDER[weakest]:
            weakest = candidate
    return weakest


class SeverityBand(StrEnum):
    """Which shape of a finding a rule saw. The key configuration assigns a severity to.

    A band is a *fact* about the finding — "the grant reached Full Control", "the descriptor
    has no DACL at all" — and is therefore chosen by the rule. What that fact is worth is
    chosen by configuration. Bands are shared across rules wherever they mean the same thing,
    so an installation can say "Modify anywhere is high" once rather than eleven times.
    """

    DEFAULT = "default"
    """The rule has only one shape."""

    READ = "read"
    """The grant reaches a read category and no further."""

    WRITE = "write"
    """The grant reaches Write or Modify."""

    FULL_CONTROL = "full_control"
    """The grant reaches Full Control, including the rights to rewrite the ACL or take
    ownership."""

    NULL_DACL = "null_dacl"
    """There is no DACL at all, which grants everyone full access to the object."""

    PROTECTED = "protected"
    """An administrator set ``SE_DACL_PROTECTED`` on the object: it refuses entries its
    parent would otherwise pass down."""

    DIVERGED = "diverged"
    """The object's DACL is not what its parent projects onto a child of its kind, without
    protection being set. Somebody added or removed an entry here."""
