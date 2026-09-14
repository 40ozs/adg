"""Four answers to "can this principal reach this?", where a boolean gives two.

:attr:`EffectiveAccess.has_access` is a boolean, and its own docstring warns that ``False``
has two completely different meanings depending on :attr:`EffectiveAccess.certainty`. That
warning has been load-bearing since Phase 4B and it is the wrong shape for a frontend: a
caller has to read two fields, know the certainty vocabulary, and re-derive the distinction
correctly every time it renders a row. One of them will not.

So the distinction is made once, here, as a typed verdict:

* :attr:`AccessOutcome.GRANTED` — rights survive to the end.
* :attr:`AccessOutcome.DENIED` — no rights survive, and an explicit Deny entry is why.
  Somebody decided this, and the decision is on an ACL where it can be found.
* :attr:`AccessOutcome.NO_GRANT` — no rights survive and nothing denied them. No entry on
  either ACL names this principal with anything to give. Nobody decided; it simply is not
  granted.
* :attr:`AccessOutcome.INDETERMINATE` — no rights were established **and the inputs were
  incomplete**, so "no access" is not a conclusion this evidence supports.

The distinction between the last two is the one an auditor acts on. "Denied" is a control
somebody put there and may be relying on; "no grant" is the absence of one. Remediating
them is different work, and a UI that renders both as a grey dash has thrown the difference
away.

**Outcome and certainty are orthogonal, and both must be rendered.** The outcome is the
verdict on the evidence; the certainty is the direction in which the evidence can be wrong.
``GRANTED`` with ``AT_MOST`` means rights were established and an unseen restriction could
narrow them — still a finding, still real, because the share ACL nobody read can only take
rights away and the ones reported are what the NTFS layer already permits. Folding the
certainty into the outcome would make that case unrepresentable, which is why there is no
``PROBABLY_GRANTED``.

Pure: one function over one :class:`EffectiveAccess`, no database, no request, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.access_engine.evaluation import AppliedAce
from app.access_engine.resolver import AccessCertainty, EffectiveAccess

__all__ = [
    "OUTCOME_DESCRIPTIONS",
    "UNDERSTATING_CERTAINTIES",
    "AccessOutcome",
    "AccessVerdict",
    "classify_access",
    "describe_outcome",
]


class AccessOutcome(StrEnum):
    """What the answer *is*, as one value a client can branch on.

    The values are stable and appear in API responses; renaming one is a contract change.
    """

    GRANTED = "granted"
    """At least one right survives both layers. Read :attr:`AccessVerdict.certainty` for
    the direction this could still be wrong in."""

    DENIED = "denied"
    """No right survives, and an explicit Deny entry withheld what an Allow would have
    given. The entries responsible are in :attr:`AccessVerdict.denials`."""

    NO_GRANT = "no_grant"
    """No right survives and nothing denied one. Nothing on either ACL gives this principal
    anything here — which is a different fact from a Deny, and a different remediation."""

    INDETERMINATE = "indeterminate"
    """No right was established, and the inputs were incomplete in the direction that
    hides grants. This is *not* "no access": it is "this question was not answerable from
    what has been collected", and a consumer must not conclude the negative from it."""


#: Certainties under which the reported rights may be narrower than the truth, so an empty
#: result is not evidence of absence. Derived from the same direction vocabulary Phase 4B
#: established rather than a second list of conditions, so the two cannot disagree.
UNDERSTATING_CERTAINTIES: Final[frozenset[AccessCertainty]] = frozenset(
    {AccessCertainty.AT_LEAST, AccessCertainty.UNCERTAIN}
)


OUTCOME_DESCRIPTIONS: Final[dict[AccessOutcome, str]] = {
    AccessOutcome.GRANTED: "This principal holds rights on this object.",
    AccessOutcome.DENIED: (
        "This principal holds no rights here because a Deny entry withheld them. The Deny "
        "is on an ACL and can be found, changed, or relied upon."
    ),
    AccessOutcome.NO_GRANT: (
        "This principal holds no rights here and nothing denied any. No entry on either "
        "ACL names it with anything to give."
    ),
    AccessOutcome.INDETERMINATE: (
        "No rights were established, and what ADG has collected is incomplete in a way "
        "that could hide a grant. This is not a finding of no access; it is a gap in "
        "collection, and the answer must not be read as a negative."
    ),
}


def describe_outcome(outcome: AccessOutcome) -> str:
    """The operator-facing sentence for an outcome."""
    return OUTCOME_DESCRIPTIONS[outcome]


@dataclass(frozen=True, slots=True)
class AccessVerdict:
    """One typed answer, with the evidence that distinguishes it from the other three."""

    outcome: AccessOutcome
    certainty: AccessCertainty
    reason: str
    """The operator-facing sentence. Rendered, never branched on — branch on
    :attr:`outcome`, which is the stable value."""

    denials: tuple[AppliedAce, ...] = ()
    """Deny entries that actually withheld something, both layers, in evaluation order.

    Non-empty is possible under :attr:`AccessOutcome.GRANTED` too: a Deny that removed
    Write while Read survived is exactly the case an explanation exists to show. It is the
    *outcome* that says whether anything is left, not the presence of a denial.
    """

    @property
    def may_understate(self) -> bool:
        """Whether the real rights could be wider than reported. Keep looking if so."""
        return self.certainty in UNDERSTATING_CERTAINTIES

    @property
    def may_overstate(self) -> bool:
        """Whether the real rights could be narrower than reported."""
        return self.certainty in {AccessCertainty.AT_MOST, AccessCertainty.UNCERTAIN}

    @property
    def is_conclusive(self) -> bool:
        """Whether the answer may be acted on as it stands.

        False for :attr:`AccessOutcome.INDETERMINATE` alone. A conclusive ``NO_GRANT`` can
        be reported as "no access"; an inconclusive one may only be reported as "unknown".
        """
        return self.outcome is not AccessOutcome.INDETERMINATE

    @property
    def has_access(self) -> bool:
        """Whether rights survive, for a caller that only needs the boolean back."""
        return self.outcome is AccessOutcome.GRANTED


def classify_access(access: EffectiveAccess) -> AccessVerdict:
    """Reduce one resolved answer to one of four outcomes.

    Precedence, and why it is this way round:

    1. **Rights survive → GRANTED**, whatever the certainty. Something was established; the
       caveat belongs to :attr:`AccessVerdict.certainty`, not to the verdict.
    2. **Nothing survives and the answer may understate → INDETERMINATE.** This outranks
       ``DENIED`` deliberately. A Deny is conclusive only about the rights it names at the
       position it sits: an unobserved membership could put the subject on an Allow that
       *precedes* it, which grants under the order Windows actually evaluates (ADR-0010).
       Reporting ``DENIED`` there would be a stronger claim than the evidence supports, and
       the denial is not lost — it is still in :attr:`AccessVerdict.denials`.
    3. **A Deny withheld something → DENIED.**
    4. **Otherwise → NO_GRANT.**

    Step 3 reads ``denied_by`` rather than "a Deny entry exists on the ACL", because an
    entry whose contribution is empty took nothing away: it named rights an earlier entry
    had already settled, and calling that the cause would send an administrator to delete
    an entry whose removal changes nothing. It is the same distinction
    :mod:`app.access_engine.causality` draws between a matched ACE and a cause.
    """
    denials = _denials(access)
    certainty = access.certainty

    if access.has_access:
        outcome = AccessOutcome.GRANTED
    elif certainty in UNDERSTATING_CERTAINTIES:
        outcome = AccessOutcome.INDETERMINATE
    elif denials:
        outcome = AccessOutcome.DENIED
    else:
        outcome = AccessOutcome.NO_GRANT

    return AccessVerdict(
        outcome=outcome,
        certainty=certainty,
        reason=describe_outcome(outcome),
        denials=denials,
    )


def _denials(access: EffectiveAccess) -> tuple[AppliedAce, ...]:
    """Deny entries that withheld rights, NTFS first then share, in evaluation order.

    Both layers together, because "which ACL denied me" is answered by
    :attr:`EffectiveAccess.limiting_layer` and the entries themselves carry their layer on
    :attr:`AppliedAce.entry`. Splitting them here would make a caller reassemble the order.

    ``denied_by`` is already only the entries that took something away —
    :func:`app.access_engine.evaluation.evaluate_acl` files a Deny whose contribution is
    empty under ``superseded`` instead — so nothing is filtered here. That invariant is what
    makes step 3 of :func:`classify_access` mean "a Deny is the cause" rather than "a Deny
    is present", and ``TestTheDeniedByInvariant`` pins it rather than leaving it assumed.
    """
    applied = list(access.ntfs.denied_by)
    if access.share is not None:
        applied.extend(access.share.denied_by)
    return tuple(applied)
