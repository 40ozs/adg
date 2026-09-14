r"""Telling "denied" from "not granted" from "nobody looked".

Phase 4B could already distinguish these — the information was in ``has_access`` together
with ``certainty`` and the findings — but distinguishing them was *the caller's* job, and
the caller is a browser written by somebody who will read ``access: false`` and render a
grey dash. This module is where the distinction is made once, so every consumer gets it
right by doing nothing.

Organized by the claim each group defends:

* the four outcomes are reachable, and each one means what it says;
* an incomplete answer never reports a negative, whatever else is true of it;
* a Deny that took nothing away is not a cause;
* outcome and certainty stay orthogonal, because folding them would make a real state
  unrepresentable.
"""

from __future__ import annotations

import pytest

from app.access_engine import (
    OUTCOME_DESCRIPTIONS,
    AccessCertainty,
    AccessCondition,
    AccessFinding,
    AccessOutcome,
    AccessPath,
    AclEntry,
    AclProvenance,
    EffectiveAccess,
    RightsLayer,
    classify_access,
    describe_outcome,
    resolve_access,
)
from app.domain import SharePermission
from tests.access_engine.support import (
    ALICE,
    DOMAIN_USERS,
    FINANCE_TEAM,
    FULL_CONTROL,
    MODIFY,
    READ_EXECUTE,
    allow,
    dacl,
    deny,
    share_acl,
    share_allow,
    share_deny,
    token,
    unread_share,
)


def local(*entries: AclEntry, **facts: object) -> EffectiveAccess:
    """A local-path answer, so the share layer stays out of a test that is not about it."""
    return resolve_access(
        token(ALICE, [FINANCE_TEAM], path=AccessPath.LOCAL), dacl(*entries, **facts)
    )


class TestTheFourOutcomes:
    """Each value is reachable, and reachable for the reason it names."""

    def test_rights_that_survive_are_granted(self) -> None:
        verdict = classify_access(local(allow(FINANCE_TEAM, MODIFY)))

        assert verdict.outcome is AccessOutcome.GRANTED
        assert verdict.has_access
        assert verdict.is_conclusive

    def test_a_deny_that_removes_everything_is_denied(self) -> None:
        verdict = classify_access(local(deny(FINANCE_TEAM, FULL_CONTROL), allow(ALICE, MODIFY)))

        assert verdict.outcome is AccessOutcome.DENIED
        assert not verdict.has_access
        assert verdict.is_conclusive

    def test_an_acl_that_names_nobody_is_no_grant(self) -> None:
        """The distinction that matters to an auditor: nobody decided this."""
        verdict = classify_access(local(allow(DOMAIN_USERS, MODIFY)))

        assert verdict.outcome is AccessOutcome.NO_GRANT
        assert verdict.denials == ()
        assert verdict.is_conclusive

    def test_an_empty_dacl_is_no_grant_and_not_denied(self) -> None:
        """An empty DACL grants nobody anything, and it is not a decision about anybody."""
        verdict = classify_access(local())

        assert verdict.outcome is AccessOutcome.NO_GRANT

    def test_an_unread_descriptor_is_indeterminate(self) -> None:
        r"""The one this vocabulary exists for.

        A directory no run has read evaluates against no entries and therefore grants
        nothing. Phase 4C already fixed the certainty for this case; without an outcome
        vocabulary a client still renders it identically to a directory that genuinely
        grants nobody anything — and "nobody can reach this" is the most dangerous
        false negative this product can produce.
        """
        access = resolve_access(
            token(ALICE, path=AccessPath.LOCAL),
            dacl(provenance=AclProvenance.UNOBSERVED),
        )
        verdict = classify_access(access)

        assert access.provenance is AclProvenance.UNOBSERVED

        assert verdict.outcome is AccessOutcome.INDETERMINATE
        assert not verdict.is_conclusive
        assert verdict.may_understate

    def test_every_outcome_has_a_sentence(self) -> None:
        for outcome in AccessOutcome:
            assert describe_outcome(outcome)
            assert OUTCOME_DESCRIPTIONS[outcome].endswith(".")


class TestAnIncompleteAnswerNeverConcludesTheNegative:
    """`indeterminate` outranks every other empty result, and that ordering is the point."""

    @pytest.mark.parametrize("certainty", [AccessCertainty.AT_LEAST, AccessCertainty.UNCERTAIN])
    def test_an_understating_certainty_beats_no_grant(self, certainty: AccessCertainty) -> None:
        access = resolve_access(
            token(ALICE, path=AccessPath.LOCAL),
            dacl(allow(DOMAIN_USERS, MODIFY)),
            findings=_findings_for(certainty),
        )

        assert access.certainty is certainty
        assert classify_access(access).outcome is AccessOutcome.INDETERMINATE

    def test_an_understating_certainty_beats_denied(self) -> None:
        r"""A Deny is conclusive about the rights it names *where it sits*.

        With a membership nobody collected, the subject could also be on an Allow that
        precedes the Deny — which grants, under the order Windows actually evaluates. So
        the honest report is "not established", not "denied".

        The denial is not thrown away: it stays on ``denials`` so a client can still show
        the entry. What changes is only the claim made about it.
        """
        access = resolve_access(
            token(ALICE, [FINANCE_TEAM], path=AccessPath.LOCAL),
            dacl(deny(FINANCE_TEAM, FULL_CONTROL)),
            findings=(
                AccessFinding(
                    AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED, {"trustee_sid": "S-1-5-99"}
                ),
            ),
        )
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.INDETERMINATE
        assert verdict.denials, "the deny is still the evidence, even when it is not the verdict"

    def test_a_truncated_membership_is_indeterminate(self) -> None:
        """The token is a lower bound, so the empty answer is a lower bound too."""
        access = resolve_access(
            token(ALICE, path=AccessPath.LOCAL, membership_complete=False),
            dacl(allow(DOMAIN_USERS, MODIFY)),
        )

        assert classify_access(access).outcome is AccessOutcome.INDETERMINATE

    def test_an_unread_share_does_not_make_an_empty_ntfs_answer_indeterminate(self) -> None:
        """An unseen *restriction* only narrows, so it cannot hide a grant.

        This is the case that would break if the rule were "any finding at all makes it
        indeterminate" rather than "a finding in the understating direction does". The
        share ACL nobody read can only take rights away; it cannot conjure the NTFS grant
        that is absent, so 'no grant' remains the conclusion.
        """
        access = resolve_access(token(ALICE), dacl(allow(DOMAIN_USERS, MODIFY)), unread_share())

        assert access.certainty is AccessCertainty.AT_MOST
        assert classify_access(access).outcome is AccessOutcome.NO_GRANT


class TestADenyThatTookNothingIsNotACause:
    """`denied` must point at an entry whose removal would change something."""

    def test_the_denied_by_invariant(self) -> None:
        """Pins the assumption :func:`classify_access` is built on.

        ``evaluate_acl`` files a Deny whose contribution is empty under ``superseded``
        rather than ``denied_by``. The verdict reads ``denied_by`` directly and would start
        reporting decorative denials as causes if that ever changed, so the invariant is
        asserted here rather than left to be discovered.
        """
        access = local(allow(FINANCE_TEAM, MODIFY), deny(FINANCE_TEAM, READ_EXECUTE))

        assert access.ntfs.superseded, "the deny behind an allow should be superseded"
        assert all(not applied.contributed.is_empty for applied in access.ntfs.denied_by)

    def test_a_deny_behind_an_allow_does_not_make_the_answer_denied(self) -> None:
        """Rights survive, so the outcome is granted however many Deny entries follow."""
        verdict = classify_access(local(allow(FINANCE_TEAM, MODIFY), deny(FINANCE_TEAM, MODIFY)))

        assert verdict.outcome is AccessOutcome.GRANTED

    def test_a_partial_deny_is_reported_alongside_a_grant(self) -> None:
        """Denials and a grant coexist: this is the ordinary shape of a real ACL."""
        verdict = classify_access(
            local(deny(FINANCE_TEAM, 0x00000002), allow(FINANCE_TEAM, MODIFY))
        )

        assert verdict.outcome is AccessOutcome.GRANTED
        assert len(verdict.denials) == 1


class TestBothLayersAreRepresented:
    def test_a_share_deny_is_a_denial(self) -> None:
        access = resolve_access(
            token(ALICE, [FINANCE_TEAM]),
            dacl(allow(FINANCE_TEAM, MODIFY)),
            share_acl(_share_deny(FINANCE_TEAM)),
        )
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.DENIED
        assert [applied.entry.layer for applied in verdict.denials] == [RightsLayer.SMB_SHARE]

    def test_a_share_that_grants_nobody_is_no_grant_not_denied(self) -> None:
        """The limiting layer says where to look; the outcome says what happened there."""
        access = resolve_access(
            token(ALICE, [FINANCE_TEAM]),
            dacl(allow(FINANCE_TEAM, MODIFY)),
            share_acl(share_allow(DOMAIN_USERS)),
        )
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.NO_GRANT
        assert verdict.denials == ()

    def test_denials_come_back_ntfs_first(self) -> None:
        """A stable order, so two renderings of one answer list them the same way."""
        access = resolve_access(
            token(ALICE, [FINANCE_TEAM]),
            dacl(deny(FINANCE_TEAM, FULL_CONTROL)),
            share_acl(_share_deny(FINANCE_TEAM)),
        )

        layers = [applied.entry.layer for applied in classify_access(access).denials]
        assert layers == [RightsLayer.NTFS, RightsLayer.SMB_SHARE]


class TestOutcomeAndCertaintyStayOrthogonal:
    """Folding one into the other would make a real, common state unrepresentable."""

    def test_granted_can_be_an_upper_bound(self) -> None:
        r"""Rights established, and an unseen restriction could still narrow them.

        There is deliberately no ``probably_granted``. The NTFS layer really does permit
        this; what is unknown is whether the share layer would allow it through, and that
        is what ``certainty`` says. A single enum could not carry both facts, and the one
        it would have to drop is the one an auditor acts on.
        """
        verdict = classify_access(
            resolve_access(
                token(ALICE, [FINANCE_TEAM]), dacl(allow(FINANCE_TEAM, MODIFY)), unread_share()
            )
        )

        assert verdict.outcome is AccessOutcome.GRANTED
        assert verdict.certainty is AccessCertainty.AT_MOST
        assert verdict.may_overstate and not verdict.may_understate
        assert verdict.is_conclusive, "an upper bound is still a finding to act on"

    def test_granted_survives_an_understating_gap(self) -> None:
        """Rights were established; that a gap could widen them does not unestablish them."""
        verdict = classify_access(
            resolve_access(
                token(ALICE, [FINANCE_TEAM], path=AccessPath.LOCAL, membership_complete=False),
                dacl(allow(FINANCE_TEAM, MODIFY)),
            )
        )

        assert verdict.outcome is AccessOutcome.GRANTED
        assert verdict.may_understate, "and there may be more rights than these"

    def test_the_reason_matches_the_outcome(self) -> None:
        for access in (
            local(allow(FINANCE_TEAM, MODIFY)),
            local(deny(FINANCE_TEAM, FULL_CONTROL), allow(ALICE, MODIFY)),
            local(allow(DOMAIN_USERS, MODIFY)),
        ):
            verdict = classify_access(access)
            assert verdict.reason == describe_outcome(verdict.outcome)

    def test_the_certainty_is_carried_through_unchanged(self) -> None:
        """The verdict never recomputes certainty; it reports the resolver's."""
        access = local(allow(FINANCE_TEAM, MODIFY))

        assert classify_access(access).certainty is access.certainty


def _share_deny(trustee: str) -> AclEntry:
    return share_deny(trustee, SharePermission.FULL)


def _findings_for(certainty: AccessCertainty) -> tuple[AccessFinding, ...]:
    """Findings that produce exactly the requested certainty.

    ``UNCERTAIN`` needs gaps in both directions at once, which is why it takes two.
    """
    understating = AccessFinding(AccessCondition.MEMBERSHIP_TRUNCATED, {})
    if certainty is AccessCertainty.AT_LEAST:
        return (understating,)
    return (understating, AccessFinding(AccessCondition.NTFS_ACL_DERIVED, {"ancestor": "x"}))
