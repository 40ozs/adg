"""The governance model, tested as rules rather than as code paths.

Every assertion here pins a sentence from :mod:`app.governance.model`. The rules this file
covers are the ones that decide whether an attestation means anything — what a decision must
say, when it may be given, what identifies an item, and what a digest is taken over — so each
test is named for the property and not for the method it happens to call.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.domain import (
    AceSource,
    AceType,
    CampaignFocus,
    CampaignStatus,
    DecisionKind,
    OwnershipRole,
    ReviewScopeKind,
    ReviewTargetKind,
    SharePermission,
)
from app.governance.model import (
    CAMPAIGN_TRANSITIONS,
    CampaignScope,
    GenerationOptions,
    GovernanceValidationError,
    GrantEvidence,
    ResourceOwner,
    ReviewCampaign,
    evidence_digest,
    item_natural_key,
    snapshot_digest,
    validate_baseline,
    validate_due_date,
    validate_rationale,
    validate_scopes,
    validate_transition,
    weakest_certainty,
)
from app.history.model import Certainty

NOW = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)
EARLIER = NOW - dt.timedelta(days=7)


def grant(
    ace_key: str = "\\\\fs01\\finance|S-1-5-21-1-2-3-500|allow|0x1200a9|0x0",
    *,
    trustee: str = "S-1-5-21-1-2-3-500",
    ace_type: AceType = AceType.ALLOW,
    mask: int | None = 0x1200A9,
    source: AceSource | None = AceSource.EXPLICIT,
    certainty: Certainty = Certainty.OBSERVED,
    version_id: int = 1,
    order_index: int | None = 0,
) -> GrantEvidence:
    return GrantEvidence(
        target_kind=ReviewTargetKind.RESOURCE,
        ace_key=ace_key,
        trustee_sid=trustee,
        trustee_key=trustee,
        ace_type=ace_type,
        access_mask=mask,
        permission=None,
        ace_flags=0x0,
        source=source,
        inherited_from=None,
        order_index=order_index,
        version_id=version_id,
        observed_from=EARLIER,
        last_confirmed_at=NOW,
        certainty=certainty,
    )


class TestWhatADecisionMustSay:
    @pytest.mark.parametrize(
        "decision", [DecisionKind.REVOKE, DecisionKind.MODIFY, DecisionKind.ABSTAIN]
    )
    def test_every_answer_but_certify_must_give_a_reason(self, decision: DecisionKind) -> None:
        """An unexplained removal cannot be defended to the person who loses access, and an
        unexplained abstention tells the campaign owner nothing about who to ask instead."""
        with pytest.raises(GovernanceValidationError, match="must say why"):
            validate_rationale(decision, None)

    def test_certify_needs_no_reason(self) -> None:
        """Requiring prose for the expected answer produces "ok" four thousand times, which
        buries the rationales that do matter."""
        assert validate_rationale(DecisionKind.CERTIFY, None) is None

    def test_whitespace_is_not_a_reason(self) -> None:
        with pytest.raises(GovernanceValidationError, match="must say why"):
            validate_rationale(DecisionKind.REVOKE, "   \n  ")

    def test_a_reason_is_trimmed(self) -> None:
        assert validate_rationale(DecisionKind.REVOKE, "  Leaver.  ") == "Leaver."

    def test_an_essay_is_refused_with_both_numbers(self) -> None:
        with pytest.raises(GovernanceValidationError, match="4000 characters; this one is 5000"):
            validate_rationale(DecisionKind.REVOKE, "x" * 5000)


class TestTheCampaignLifecycle:
    def test_a_draft_may_open_or_be_abandoned(self) -> None:
        validate_transition(CampaignStatus.DRAFT, CampaignStatus.ACTIVE)
        validate_transition(CampaignStatus.DRAFT, CampaignStatus.CANCELED)

    def test_a_draft_cannot_be_closed_without_being_opened(self) -> None:
        with pytest.raises(GovernanceValidationError, match="draft cannot become closed"):
            validate_transition(CampaignStatus.DRAFT, CampaignStatus.CLOSED)

    def test_a_closed_campaign_is_final(self) -> None:
        """Reopening would let decisions be added to a campaign somebody has already signed
        off, which is the one edit that makes a completed review unfalsifiable."""
        with pytest.raises(GovernanceValidationError, match="it may become: nothing"):
            validate_transition(CampaignStatus.CLOSED, CampaignStatus.ACTIVE)

    def test_a_canceled_campaign_is_final(self) -> None:
        with pytest.raises(GovernanceValidationError, match="canceled cannot become active"):
            validate_transition(CampaignStatus.CANCELED, CampaignStatus.ACTIVE)

    def test_the_message_names_both_states_and_the_way_out(self) -> None:
        with pytest.raises(GovernanceValidationError) as caught:
            validate_transition(CampaignStatus.ACTIVE, CampaignStatus.DRAFT)

        message = str(caught.value)
        assert "active" in message and "draft" in message
        assert "canceled, closed" in message

    def test_every_status_has_an_entry_so_a_new_one_cannot_be_forgotten(self) -> None:
        assert set(CAMPAIGN_TRANSITIONS) == set(CampaignStatus)


class TestTheBaselineIsAFrozenPast:
    def test_a_naive_instant_is_refused_rather_than_assumed_utc(self) -> None:
        """A naive timestamp cannot be ordered against observations from a collector in
        another time zone, so the campaign would freeze a different moment than intended."""
        with pytest.raises(GovernanceValidationError, match="timezone-aware"):
            validate_baseline(dt.datetime(2026, 9, 1, 12, 0), NOW)

    def test_a_future_baseline_is_refused(self) -> None:
        """It would freeze the campaign against state nobody has observed yet: the item set
        would be empty today and different tomorrow, so "reproducible against its baseline"
        would be false by construction."""
        with pytest.raises(GovernanceValidationError, match="in the future"):
            validate_baseline(NOW + dt.timedelta(minutes=1), NOW)

    def test_the_present_instant_is_allowed(self) -> None:
        assert validate_baseline(NOW, NOW) == NOW

    def test_an_offset_instant_is_normalized_to_utc(self) -> None:
        offset = dt.datetime(2026, 9, 1, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))

        assert validate_baseline(offset, NOW) == dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)

    def test_a_due_date_at_or_before_the_baseline_is_refused(self) -> None:
        """The campaign would be overdue before anyone could open it."""
        with pytest.raises(GovernanceValidationError, match="overdue before anyone"):
            validate_due_date(EARLIER, EARLIER)

    def test_no_due_date_is_allowed(self) -> None:
        assert validate_due_date(None, EARLIER) is None


class TestAScopeMustMatchTheFocus:
    def test_a_resource_campaign_takes_resource_scopes(self) -> None:
        validate_scopes(
            CampaignFocus.RESOURCE, [CampaignScope(kind=ReviewScopeKind.SHARE, key="fs01|finance")]
        )

    def test_a_principal_campaign_takes_a_principal_scope(self) -> None:
        validate_scopes(
            CampaignFocus.PRINCIPAL,
            [CampaignScope(kind=ReviewScopeKind.PRINCIPAL, key="S-1-5-21-1-2-3-500")],
        )

    def test_a_principal_campaign_refuses_a_share_scope(self) -> None:
        """Selecting by one end of a grant while claiming completeness about the other
        answers neither question."""
        with pytest.raises(GovernanceValidationError, match="cannot be scoped by share"):
            validate_scopes(
                CampaignFocus.PRINCIPAL,
                [CampaignScope(kind=ReviewScopeKind.SHARE, key="fs01|finance")],
            )

    def test_a_resource_campaign_refuses_a_principal_scope(self) -> None:
        with pytest.raises(GovernanceValidationError, match="cannot be scoped by principal"):
            validate_scopes(
                CampaignFocus.RESOURCE,
                [CampaignScope(kind=ReviewScopeKind.PRINCIPAL, key="S-1-1-0")],
            )

    def test_a_campaign_with_no_scope_is_refused(self) -> None:
        """It would review either the whole estate or nothing, and an item set nobody
        bounded is not something a reviewer can be accountable for."""
        with pytest.raises(GovernanceValidationError, match="at least one scope"):
            validate_scopes(CampaignFocus.RESOURCE, [])

    def test_a_blank_scope_key_is_refused(self) -> None:
        with pytest.raises(GovernanceValidationError, match="needs a key"):
            CampaignScope(kind=ReviewScopeKind.SHARE, key="   ")


class TestOwnershipIsAdgMetadata:
    def test_an_owner_is_a_user_or_a_principal_and_not_both(self) -> None:
        with pytest.raises(GovernanceValidationError, match="exactly one of them"):
            ResourceOwner(
                owner_id=uuid.uuid4(),
                target_kind=ReviewTargetKind.SHARE,
                target_key="fs01|finance",
                ownership_role=OwnershipRole.OWNER,
                owner_subject="alice",
                owner_principal_key="S-1-5-21-1-2-3-500",
                owner_display_name=None,
                note=None,
                assigned_by_subject="admin",
                assigned_at=NOW,
            )

    def test_an_owner_naming_nobody_is_refused(self) -> None:
        """Accountability belonging to nobody is the state this record exists to end."""
        with pytest.raises(GovernanceValidationError, match="exactly one of them"):
            ResourceOwner(
                owner_id=uuid.uuid4(),
                target_kind=ReviewTargetKind.SHARE,
                target_key="fs01|finance",
                ownership_role=OwnershipRole.OWNER,
                owner_subject=None,
                owner_principal_key=None,
                owner_display_name=None,
                note=None,
                assigned_by_subject="admin",
                assigned_at=NOW,
            )


class TestWhatAnItemIsAbout:
    def test_identity_is_the_principal_and_the_target_not_the_entry(self) -> None:
        """A relation may have several entries -- an allow and a deny, or two with different
        inheritance flags. Reviewing each separately would ask somebody to certify half a
        grant."""
        first = item_natural_key(ReviewTargetKind.RESOURCE, "\\\\fs01\\finance", "S-1-1")
        second = item_natural_key(ReviewTargetKind.RESOURCE, "\\\\fs01\\finance", "S-1-1")

        assert first == second

    def test_the_two_acl_layers_are_different_items(self) -> None:
        """A share's permissions and a directory's DACL are removed in different places, so
        certifying one says nothing about the other."""
        share = item_natural_key(ReviewTargetKind.SHARE, "fs01|finance", "S-1-1")
        resource = item_natural_key(ReviewTargetKind.RESOURCE, "fs01|finance", "S-1-1")

        assert share != resource


class TestTheEvidenceDigest:
    def test_the_order_entries_arrive_in_does_not_change_it(self) -> None:
        """The order a query returned rows in is not part of the grant, and two generations
        of one item must agree or verification would report a change that is not one."""
        a, b = grant(ace_key="a"), grant(ace_key="b")

        assert evidence_digest([a, b]) == evidence_digest([b, a])

    def test_a_different_mask_is_a_different_digest(self) -> None:
        assert evidence_digest([grant()]) != evidence_digest([grant(mask=0x1F01FF)])

    def test_a_different_ace_type_is_a_different_digest(self) -> None:
        assert evidence_digest([grant()]) != evidence_digest([grant(ace_type=AceType.DENY)])

    def test_confirming_the_same_state_again_does_not_change_it(self) -> None:
        """Certainty is a property of the question asked -- how firm was this at the baseline
        -- and is derived from the version's interval, not from the entry. Digesting it would
        make an item's digest change when a later scan confirmed the very same grant, which
        is exactly the case where nothing about the reviewed state changed at all."""
        watched = grant(certainty=Certainty.OBSERVED)
        reconstructed = grant(certainty=Certainty.BACKFILLED)

        assert evidence_digest([watched]) == evidence_digest([reconstructed])

    def test_a_different_version_is_a_different_digest(self) -> None:
        """The version is what makes a campaign checkable: re-reading it must reproduce this
        evidence, so an item built from a different row is a different item."""
        assert evidence_digest([grant()]) != evidence_digest([grant(version_id=99)])

    def test_a_share_permission_level_is_digested_as_well_as_a_mask(self) -> None:
        base = grant()
        leveled = GrantEvidence(
            **{
                **{field: getattr(base, field) for field in base.__slots__},
                "access_mask": None,
                "permission": SharePermission.CHANGE,
            }
        )

        assert evidence_digest([leveled]) != evidence_digest([base])


class TestTheSnapshotDigest:
    def test_it_does_not_depend_on_the_order_items_were_generated_in(self) -> None:
        first = (("resource", "a", "S-1-1"), "d1")
        second = (("resource", "b", "S-1-2"), "d2")

        assert snapshot_digest([first, second]) == snapshot_digest([second, first])

    def test_one_changed_item_changes_the_whole_campaign_digest(self) -> None:
        base = [(("resource", "a", "S-1-1"), "d1")]
        changed = [(("resource", "a", "S-1-1"), "d2")]

        assert snapshot_digest(base) != snapshot_digest(changed)

    def test_an_empty_campaign_has_a_digest_rather_than_none(self) -> None:
        """A campaign that generated nothing is still a campaign that was cut, and it must be
        distinguishable from one that was never generated at all."""
        assert len(snapshot_digest([])) == 64


class TestAnAnswerIsAsSoundAsItsWeakestFact:
    def test_one_reconstructed_entry_weakens_the_whole_item(self) -> None:
        assert (
            weakest_certainty(
                [grant(certainty=Certainty.OBSERVED), grant(certainty=Certainty.BACKFILLED)]
            )
            is Certainty.BACKFILLED
        )

    def test_inferred_beats_backfilled_but_loses_to_observed(self) -> None:
        assert (
            weakest_certainty(
                [grant(certainty=Certainty.OBSERVED), grant(certainty=Certainty.INFERRED)]
            )
            is Certainty.INFERRED
        )

    def test_all_observed_stays_observed(self) -> None:
        assert weakest_certainty([grant(), grant()]) is Certainty.OBSERVED

    def test_no_evidence_is_unobserved_rather_than_the_strongest_value(self) -> None:
        """Unreachable in practice -- generation never creates an item with no evidence,
        because an item *is* its evidence. Returning the strongest value instead would make
        the one bug that could produce an empty item present itself as a fully watched
        answer."""
        assert weakest_certainty([]) is Certainty.UNOBSERVED


class TestWhenACampaignIsOverdue:
    def _campaign(self, status: CampaignStatus, due: dt.datetime | None) -> ReviewCampaign:
        return ReviewCampaign(
            campaign_id=uuid.uuid4(),
            name="Q3",
            description=None,
            focus=CampaignFocus.RESOURCE,
            status=status,
            baseline_at=EARLIER,
            due_at=due,
            options=GenerationOptions(),
            scopes=(CampaignScope(kind=ReviewScopeKind.SHARE, key="fs01|finance"),),
            created_by_subject="admin",
            created_at=EARLIER,
        )

    def test_an_active_campaign_past_its_date_is_overdue(self) -> None:
        campaign = self._campaign(CampaignStatus.ACTIVE, NOW - dt.timedelta(days=1))

        assert campaign.is_overdue(NOW)

    def test_a_closed_campaign_is_never_overdue_however_late_it_closed(self) -> None:
        """Overdue describes work outstanding, and a closed campaign has none."""
        campaign = self._campaign(CampaignStatus.CLOSED, NOW - dt.timedelta(days=90))

        assert not campaign.is_overdue(NOW)

    def test_a_campaign_with_no_due_date_is_never_overdue(self) -> None:
        campaign = self._campaign(CampaignStatus.ACTIVE, None)

        assert not campaign.is_overdue(NOW)

    def test_only_an_active_campaign_accepts_decisions(self) -> None:
        for status in CampaignStatus:
            campaign = self._campaign(status, None)
            assert campaign.accepts_decisions is (status is CampaignStatus.ACTIVE)
