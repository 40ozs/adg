"""What a decision must say, and what a batch may cover.

Two rules a campaign's workflow turns on, both pure and both testable without a database.

**The comment requirement may only ever make the rule stricter.** ``ck_review_decisions_
reason_required`` in the database says every decision but ``certify`` carries a rationale, and
no campaign setting may switch that off — a per-campaign way to record an unexplained
revocation would defeat the one rule that exists so the person losing access can be told why.
So the tests below assert the floor holds under *every* requirement, not merely that the
strict setting is stricter.

**A bulk action is only defensible when the items are one question.** The homogeneity rules
are what separate "Alice on eleven folders" from eleven separate judgments recorded with one
click, and each one is here with the case it refuses.
"""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from app.domain import (
    AceType,
    CampaignFocus,
    CommentRequirement,
    DecisionKind,
    ReviewItemStatus,
    ReviewTargetKind,
)
from app.governance.model import (
    DECISIONS_REQUIRING_RATIONALE,
    MAX_RATIONALE_LENGTH,
    GovernanceValidationError,
    GrantEvidence,
    ReviewItem,
    decisions_requiring_rationale,
    validate_rationale,
)
from app.governance.service import NotHomogeneous, _require_homogeneous
from app.history.model import Certainty

NOW = dt.datetime(2026, 3, 9, 12, 0, tzinfo=dt.UTC)
FINANCE = "\\\\fs01\\finance"
HR = "\\\\fs01\\hr"
ALICE = "S-1-5-21-1-2-3-1001"
BOB = "S-1-5-21-1-2-3-1002"


def grant(target: str = FINANCE, trustee: str = ALICE) -> GrantEvidence:
    return GrantEvidence(
        target_kind=ReviewTargetKind.RESOURCE,
        ace_key=f"{target}|{trustee}",
        trustee_sid=trustee,
        trustee_key=trustee,
        ace_type=AceType.ALLOW,
        access_mask=0x1200A9,
        permission=None,
        ace_flags=0,
        source=None,
        inherited_from=None,
        order_index=0,
        version_id=1,
        observed_from=NOW,
        last_confirmed_at=NOW,
        certainty=Certainty.OBSERVED,
    )


def item(
    *,
    target: str = FINANCE,
    principal: str = ALICE,
    target_kind: ReviewTargetKind = ReviewTargetKind.RESOURCE,
) -> ReviewItem:
    return ReviewItem(
        item_id=uuid4(),
        campaign_id=uuid4(),
        focus=CampaignFocus.RESOURCE,
        target_kind=target_kind,
        target_key=target,
        target_path=target,
        principal_key=principal,
        principal_sid=principal,
        principal_display_name=None,
        grants=(grant(target, principal),),
        evidence_digest="0" * 64,
        certainty=Certainty.OBSERVED,
        status=ReviewItemStatus.PENDING,
        assignment_id=None,
        created_at=NOW,
    )


class TestTheRationaleFloorHoldsUnderEveryCampaign:
    @pytest.mark.parametrize("requirement", list(CommentRequirement))
    @pytest.mark.parametrize("decision", sorted(DECISIONS_REQUIRING_RATIONALE))
    def test_no_setting_makes_a_reason_optional_for_the_answers_that_need_one(
        self, requirement: CommentRequirement, decision: DecisionKind
    ) -> None:
        """The property, stated as a test rather than as a comment: the campaign setting is a
        floor-raiser and there is no value of it that lowers the floor."""
        with pytest.raises(GovernanceValidationError):
            validate_rationale(decision, None, requirement=requirement)

    @pytest.mark.parametrize("requirement", list(CommentRequirement))
    def test_every_requirement_is_at_least_the_database_s_own_rule(
        self, requirement: CommentRequirement
    ) -> None:
        assert decisions_requiring_rationale(requirement) >= DECISIONS_REQUIRING_RATIONALE

    def test_investigate_must_say_what_to_investigate(self) -> None:
        with pytest.raises(GovernanceValidationError) as raised:
            validate_rationale(DecisionKind.INVESTIGATE, "   ")

        assert "investigate" in str(raised.value)

    def test_whitespace_is_not_a_reason(self) -> None:
        with pytest.raises(GovernanceValidationError):
            validate_rationale(DecisionKind.REVOKE, "\n\t  ")


class TestTheStandardRequirement:
    def test_certify_needs_no_reason(self) -> None:
        """The default, and the reason for it: prose demanded for the expected answer
        produces "ok" four thousand times and buries the rationales that matter."""
        assert validate_rationale(DecisionKind.CERTIFY, None) is None

    def test_a_reason_given_anyway_is_kept_and_trimmed(self) -> None:
        assert validate_rationale(DecisionKind.CERTIFY, "  still needed  ") == "still needed"


class TestTheAlwaysRequirement:
    def test_certify_needs_a_reason_too(self) -> None:
        with pytest.raises(GovernanceValidationError) as raised:
            validate_rationale(DecisionKind.CERTIFY, None, requirement=CommentRequirement.ALWAYS)

        assert "every decision" in str(raised.value)

    def test_the_message_says_why_the_campaign_was_set_up_that_way(self) -> None:
        """A refusal a reviewer cannot act on is a refusal they will work around."""
        with pytest.raises(GovernanceValidationError) as raised:
            validate_rationale(DecisionKind.CERTIFY, None, requirement=CommentRequirement.ALWAYS)

        assert "not evidence that anybody looked" in str(raised.value)

    def test_a_certify_with_a_reason_is_accepted(self) -> None:
        assert (
            validate_rationale(
                DecisionKind.CERTIFY,
                "Reviewed with the data owner on the 9th.",
                requirement=CommentRequirement.ALWAYS,
            )
            == "Reviewed with the data owner on the 9th."
        )

    def test_the_length_ceiling_is_the_same_under_either_requirement(self) -> None:
        too_long = "x" * (MAX_RATIONALE_LENGTH + 1)

        for requirement in CommentRequirement:
            with pytest.raises(GovernanceValidationError) as raised:
                validate_rationale(DecisionKind.CERTIFY, too_long, requirement=requirement)
            assert str(MAX_RATIONALE_LENGTH) in str(raised.value)


class TestWhatOneBatchMayCover:
    def test_one_principal_over_many_targets_is_one_question(self) -> None:
        _require_homogeneous([item(target=FINANCE), item(target=HR)])

    def test_one_target_over_many_principals_is_one_question(self) -> None:
        _require_homogeneous([item(principal=ALICE), item(principal=BOB)])

    def test_many_principals_over_many_targets_is_not(self) -> None:
        """The case the rule exists for: an arbitrary basket of grants is several judgments
        wearing one click, and the record would not say so."""
        with pytest.raises(NotHomogeneous) as raised:
            _require_homogeneous(
                [item(target=FINANCE, principal=ALICE), item(target=HR, principal=BOB)]
            )

        assert "not one question" in str(raised.value)
        assert "one principal over many targets" in str(raised.value)

    def test_share_and_file_system_entries_are_not_one_question(self) -> None:
        """They are removed in different places, often by different people. Certifying both
        at once asserts two things and records one."""
        with pytest.raises(NotHomogeneous) as raised:
            _require_homogeneous(
                [
                    item(target_kind=ReviewTargetKind.SHARE),
                    item(target_kind=ReviewTargetKind.RESOURCE),
                ]
            )

        assert "different kinds of access-control list" in str(raised.value)

    def test_a_single_item_is_always_homogeneous(self) -> None:
        _require_homogeneous([item()])
