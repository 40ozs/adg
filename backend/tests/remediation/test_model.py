"""What a change plan must say, what it may never say, and what its digest covers.

The narrowing rules get the most room here, and deliberately. Every other invariant in this
module fails loudly when it is broken — a membership change with no edge raises, a plan with
no steps raises. A plan that *widens* access fails silently: it has a title, a rationale, an
approver and a signature, it reads exactly like remediation, and what it does is grant.
"""

from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest

from app.domain import AceSource, AceType, MembershipEdgeKind, SharePermission
from app.domain.remediation import (
    ApprovalDecision,
    ChangePlanStatus,
    ChangeTargetKind,
    PlannedChangeKind,
)
from app.remediation.errors import RemediationValidationError
from app.remediation.model import (
    MAX_PLAN_CHANGES,
    PLAN_TRANSITIONS,
    TERMINAL_STATUSES,
    ApprovalRecord,
    EntrySnapshot,
    MembershipSnapshot,
    PlannedChange,
    plan_digest,
    validate_changes,
    validate_rationale,
    validate_title,
    validate_transition,
)
from tests.remediation import factories as f

#: Sorted for a stable parametrization order, and named so the ids can reuse the same list --
#: an inline lambda id function is untyped and mypy reads its argument as object.
TERMINAL_STATUS_IDS: list[ChangePlanStatus] = sorted(TERMINAL_STATUSES, key=lambda s: s.value)


class TestAModificationMayOnlyNarrow:
    def test_a_mask_that_adds_a_right_is_refused(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            f.change(
                PlannedChangeKind.MODIFY_NTFS_ACE,
                entry=f.entry(access_mask=f.READ_MASK),
                after_access_mask=f.WRITE_MASK,
            )

        assert "adds rights it does not hold" in str(raised.value)

    def test_a_mask_that_is_a_strict_subset_is_accepted(self) -> None:
        narrowed = f.change(
            PlannedChangeKind.MODIFY_NTFS_ACE,
            entry=f.entry(access_mask=f.FULL_MASK),
            after_access_mask=f.READ_MASK,
        )

        assert narrowed.after_access_mask == f.READ_MASK

    def test_a_mask_that_changes_nothing_is_refused(self) -> None:
        """A step that leaves the entry as it is still costs a change window and still reads
        as work done."""
        with pytest.raises(RemediationValidationError) as raised:
            f.change(
                PlannedChangeKind.MODIFY_NTFS_ACE,
                entry=f.entry(access_mask=f.READ_MASK),
                after_access_mask=f.READ_MASK,
            )

        assert "exactly as it is" in str(raised.value)

    def test_narrowing_a_deny_is_refused_because_it_grants(self) -> None:
        """The clause a mask-only rule would let through.

        Taking rights out of a Deny gives them to the trustee. A rule that only compared
        masks would see a subset and approve an escalation.
        """
        with pytest.raises(RemediationValidationError) as raised:
            f.change(
                PlannedChangeKind.MODIFY_NTFS_ACE,
                entry=f.entry(ace_type=AceType.DENY, access_mask=f.FULL_MASK),
                after_access_mask=f.READ_MASK,
            )

        assert "Deny" in str(raised.value)
        assert "grants" in str(raised.value)

    def test_a_modification_with_no_resulting_state_is_refused(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            f.change(
                PlannedChangeKind.MODIFY_NTFS_ACE,
                entry=f.entry(),
                after_access_mask=None,
            )

        assert "must say what the entry should become" in str(raised.value)

    @pytest.mark.parametrize(
        ("observed", "requested"),
        [
            (SharePermission.READ, SharePermission.CHANGE),
            (SharePermission.CHANGE, SharePermission.FULL),
            (SharePermission.READ, SharePermission.FULL),
            (SharePermission.CHANGE, SharePermission.CHANGE),
        ],
    )
    def test_a_share_level_that_is_not_lower_is_refused(
        self, observed: SharePermission, requested: SharePermission
    ) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            f.change(
                PlannedChangeKind.MODIFY_SHARE_ACE,
                entry=f.share_entry(permission=observed),
                after_permission=requested,
            )

        assert "not less" in str(raised.value)

    def test_a_share_level_that_is_lower_is_accepted(self) -> None:
        narrowed = f.change(
            PlannedChangeKind.MODIFY_SHARE_ACE,
            entry=f.share_entry(permission=SharePermission.FULL),
            after_permission=SharePermission.CHANGE,
        )

        assert narrowed.after_permission is SharePermission.CHANGE

    @pytest.mark.parametrize(
        "kind",
        [
            PlannedChangeKind.REMOVE_NTFS_ACE,
            PlannedChangeKind.REMOVE_SHARE_ACE,
        ],
    )
    def test_a_removal_carrying_a_resulting_state_is_refused(self, kind: PlannedChangeKind) -> None:
        """It would read as a modification to whoever performs it."""
        with pytest.raises(RemediationValidationError) as raised:
            f.change(kind, after_access_mask=f.READ_MASK)

        assert "has no resulting state" in str(raised.value)


class TestAChangeNamesExactlyWhatItActsOn:
    def test_a_membership_change_must_carry_its_edge(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER, membership=None)

        assert "must carry the edge it removes" in str(raised.value)

    def test_an_entry_change_must_carry_its_entry(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            f.change(PlannedChangeKind.REMOVE_NTFS_ACE, entry=None)

        assert "must carry the entry it acts on" in str(raised.value)

    def test_a_membership_change_may_not_also_carry_an_entry(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER, entry=f.entry())

        assert "acts on an edge" in str(raised.value)

    def test_the_target_and_the_edge_must_be_the_same_group(self) -> None:
        """A step checked against one object and performed on another."""
        with pytest.raises(RemediationValidationError) as raised:
            f.change(
                PlannedChangeKind.REMOVE_GROUP_MEMBER,
                target_key="S-1-5-21-1-2-3-9999",
                membership=f.edge(),
            )

        assert "would be checked against one object and performed on another" in str(raised.value)

    @pytest.mark.parametrize(
        ("kind", "wrong"),
        [
            (PlannedChangeKind.REMOVE_NTFS_ACE, ChangeTargetKind.SHARE),
            (PlannedChangeKind.REMOVE_SHARE_ACE, ChangeTargetKind.RESOURCE),
            (PlannedChangeKind.REMOVE_GROUP_MEMBER, ChangeTargetKind.RESOURCE),
        ],
    )
    def test_a_kind_acting_on_the_wrong_layer_is_refused(
        self, kind: PlannedChangeKind, wrong: ChangeTargetKind
    ) -> None:
        """Share permissions and NTFS permissions are edited with different tools, on
        different dialogs, often by different people."""
        with pytest.raises(RemediationValidationError) as raised:
            f.change(kind, target_kind=wrong)

        assert "wrong one" in str(raised.value)

    def test_an_entry_with_neither_mask_nor_permission_is_refused(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            EntrySnapshot(
                ace_key="ace-1",
                trustee_sid=f.ALICE,
                trustee_key=f.ALICE,
                ace_type=AceType.ALLOW,
            )

        assert "neither an access mask nor a permission level" in str(raised.value)

    def test_a_replacement_must_name_the_group_it_puts_the_principal_into(self) -> None:
        """Otherwise the step removes the access and grants nothing back."""
        with pytest.raises(RemediationValidationError) as raised:
            f.change(PlannedChangeKind.REPLACE_WITH_GROUP, membership=None)

        assert "remove the access and grant nothing back" in str(raised.value)


class TestALocalGroupIsNotADirectoryGroup:
    def test_a_host_scoped_key_requires_a_local_edge_kind(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            MembershipSnapshot(
                group_key=f.BUILTIN_ADMINS,
                member_key=f.ALICE,
                member_sid=f.ALICE,
                edge_kind=MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
            )

        assert "wrong machine" in str(raised.value)

    def test_an_unscoped_key_may_not_claim_a_local_edge(self) -> None:
        with pytest.raises(RemediationValidationError):
            MembershipSnapshot(
                group_key=f.FINANCE_RW,
                member_key=f.ALICE,
                member_sid=f.ALICE,
                edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            )

    def test_the_host_is_read_off_the_key(self) -> None:
        local = MembershipSnapshot(
            group_key=f.BUILTIN_ADMINS,
            member_key=f.ALICE,
            member_sid=f.ALICE,
            edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
        )

        assert local.host_key == "fs01"
        assert local.is_local

    def test_a_group_cannot_be_a_member_of_itself(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            MembershipSnapshot(
                group_key=f.FINANCE_RW, member_key=f.FINANCE_RW, member_sid=f.FINANCE_RW
            )

        assert "cannot be a direct member of itself" in str(raised.value)


class TestAPlanAsAWhole:
    def test_a_plan_with_no_changes_is_refused(self) -> None:
        """It would be approved and signed, and instruct nobody to do anything."""
        with pytest.raises(RemediationValidationError) as raised:
            validate_changes([])

        assert "instructs nobody to do anything" in str(raised.value)

    def test_two_steps_on_one_object_are_refused(self) -> None:
        shared = f.entry(ace_key="ace-shared")
        with pytest.raises(RemediationValidationError) as raised:
            validate_changes(
                [
                    f.change(PlannedChangeKind.REMOVE_NTFS_ACE, index=0, entry=shared),
                    f.change(PlannedChangeKind.REMOVE_NTFS_ACE, index=1, entry=shared),
                ]
            )

        assert "both act on" in str(raised.value)

    def test_two_steps_on_different_objects_are_fine(self) -> None:
        validate_changes(
            [
                f.change(PlannedChangeKind.REMOVE_NTFS_ACE, index=0, entry=f.entry(ace_key="a")),
                f.change(PlannedChangeKind.REMOVE_NTFS_ACE, index=1, entry=f.entry(ace_key="b")),
            ]
        )

    def test_a_gap_in_the_step_numbers_is_refused(self) -> None:
        """The runbook a person follows and the list an auditor reads must be one list."""
        with pytest.raises(RemediationValidationError) as raised:
            validate_changes(
                [
                    f.change(index=0, entry=f.entry(ace_key="a")),
                    f.change(index=2, entry=f.entry(ace_key="b")),
                ]
            )

        assert "Step numbers must run" in str(raised.value)

    def test_more_changes_than_the_ceiling_are_refused_rather_than_truncated(self) -> None:
        too_many = [
            f.change(index=index, entry=f.entry(ace_key=f"ace-{index}"))
            for index in range(MAX_PLAN_CHANGES + 1)
        ]

        with pytest.raises(RemediationValidationError) as raised:
            validate_changes(too_many)

        assert "refusal rather than a truncation" in str(raised.value)

    def test_a_plan_at_the_ceiling_is_accepted(self) -> None:
        validate_changes(
            [
                f.change(index=index, entry=f.entry(ace_key=f"ace-{index}"))
                for index in range(MAX_PLAN_CHANGES)
            ]
        )

    def test_a_plan_must_say_why(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            validate_rationale("   ")

        assert "must say why" in str(raised.value)

    def test_a_plan_must_have_a_title(self) -> None:
        with pytest.raises(RemediationValidationError):
            validate_title("")


class TestTheLifecycle:
    @pytest.mark.parametrize(
        "status", TERMINAL_STATUS_IDS, ids=[s.value for s in TERMINAL_STATUS_IDS]
    )
    def test_a_terminal_status_leads_nowhere(self, status: ChangePlanStatus) -> None:
        for requested in ChangePlanStatus:
            with pytest.raises(RemediationValidationError):
                validate_transition(status, requested)

    def test_a_draft_cannot_be_approved_directly(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            validate_transition(ChangePlanStatus.DRAFT, ChangePlanStatus.APPROVED)

        assert "cannot become approved" in str(raised.value)

    def test_nothing_returns_to_draft(self) -> None:
        """A plan whose facts have moved is rewritten, not reopened: the alternative is an
        approval history attached to changes it never described."""
        for status, onward in PLAN_TRANSITIONS.items():
            assert ChangePlanStatus.DRAFT not in onward, status

    def test_an_approved_plan_cannot_be_approved_again(self) -> None:
        with pytest.raises(RemediationValidationError):
            validate_transition(ChangePlanStatus.APPROVED, ChangePlanStatus.APPROVED)

    def test_the_graph_names_every_status(self) -> None:
        """A status with no entry would raise a KeyError at the moment somebody used it."""
        assert set(PLAN_TRANSITIONS) == set(ChangePlanStatus)


class TestTheDigests:
    def test_the_digest_covers_the_changes(self) -> None:
        one = f.plan(f.change(entry=f.entry(access_mask=f.READ_MASK)))
        two = dataclasses.replace(one, changes=(f.change(entry=f.entry(access_mask=f.WRITE_MASK)),))

        assert plan_digest(one) != plan_digest(two)

    def test_the_digest_covers_the_rationale(self) -> None:
        one = f.plan()
        two = dataclasses.replace(one, rationale="Something else entirely.")

        assert plan_digest(one) != plan_digest(two)

    def test_the_digest_covers_the_provenance_a_change_cites(self) -> None:
        """An approver told a change comes from a signed-off review approved that claim too."""
        cited = uuid4()
        one = f.plan(f.change(decision_id=cited))
        two = f.plan(
            f.change(decision_id=uuid4(), change_id=one.changes[0].change_id),
            plan_id=one.plan_id,
        )
        two = dataclasses.replace(two, requested_at=one.requested_at)

        assert plan_digest(one) != plan_digest(two)

    def test_the_digest_ignores_the_lifecycle(self) -> None:
        """Otherwise approving a plan would change its digest, and the approval could never
        record the digest it approved."""
        draft = f.plan()
        approved = dataclasses.replace(
            draft,
            status=ChangePlanStatus.APPROVED,
            approved_by_subject="dana",
            submitted_at=f.NOW,
        )

        assert plan_digest(draft) == plan_digest(approved)

    def test_the_digest_does_not_depend_on_the_order_changes_arrive_in(self) -> None:
        first = f.change(index=0, entry=f.entry(ace_key="a"))
        second = f.change(index=1, entry=f.entry(ace_key="b"))

        ordered = f.plan(first, second)
        shuffled = dataclasses.replace(ordered, changes=(second, first))

        assert plan_digest(ordered) == plan_digest(shuffled)

    def test_an_entrys_content_digest_ignores_which_version_it_came_from(self) -> None:
        """An entry removed and restored unchanged takes a new ``object_versions`` row. A
        precondition compared on identity would report a change that did not happen."""
        assert f.entry(version_id=7).content_digest == f.entry(version_id=9_999).content_digest

    def test_an_entrys_content_digest_moves_when_the_mask_does(self) -> None:
        assert f.entry(access_mask=f.READ_MASK).content_digest != (
            f.entry(access_mask=f.WRITE_MASK).content_digest
        )

    def test_an_entrys_content_digest_moves_when_it_becomes_inherited(self) -> None:
        """Where an entry is removed from depends on it, so it is part of the precondition."""
        explicit = f.entry(source=AceSource.EXPLICIT)
        inherited = f.entry(source=AceSource.INHERITED, inherited_from=f.FINANCE, ace_flags=0x13)

        assert explicit.content_digest != inherited.content_digest

    def test_an_entrys_content_digest_moves_when_it_is_reordered(self) -> None:
        """A Deny moved behind an Allow changes what the DACL does without changing any ACE."""
        assert f.entry(order_index=0).content_digest != f.entry(order_index=5).content_digest


class TestWhatAnApprovalIsOf:
    def test_an_approval_matching_the_current_digest_is_current(self) -> None:
        plan = f.plan(status=ChangePlanStatus.APPROVED)
        approved = dataclasses.replace(
            plan, approved_plan_digest=plan.digest, approved_by_subject="dana"
        )

        assert approved.approval_is_current

    def test_an_approval_of_a_different_digest_is_not(self) -> None:
        plan = f.plan(status=ChangePlanStatus.APPROVED)
        stale = dataclasses.replace(plan, approved_plan_digest="0" * 64, approved_by_subject="dana")

        assert not stale.approval_is_current

    def test_a_simulation_measured_against_another_basis_is_not_this_plans(self) -> None:
        plan = f.plan(basis_token="a" * 32)
        measured_elsewhere = dataclasses.replace(
            plan, simulation_id=uuid4(), simulation_basis_token="b" * 32
        )

        assert not measured_elsewhere.has_current_simulation

    def test_a_simulation_on_the_same_basis_is(self) -> None:
        plan = f.plan(basis_token="a" * 32)
        measured = dataclasses.replace(plan, simulation_id=uuid4(), simulation_basis_token="a" * 32)

        assert measured.has_current_simulation

    def test_a_plan_with_no_simulation_has_none(self) -> None:
        assert not f.plan().has_current_simulation

    def test_an_approval_records_what_it_answered_about(self) -> None:
        plan = f.plan()
        record = ApprovalRecord(
            approval_id=uuid4(),
            plan_id=plan.plan_id,
            decision=ApprovalDecision.APPROVE,
            approver_subject="dana",
            approver_display_name="Dana",
            approver_roles=("remediation_approver",),
            decided_at=f.NOW,
            rationale=None,
            plan_digest=plan.digest,
            basis_token=plan.basis_token,
        )

        document = record.document()
        assert document["plan_digest"] == plan.digest
        assert document["basis_token"] == plan.basis_token
        assert document["approver_roles"] == ["remediation_approver"]


class TestTheStaleCheckIsAgainstCollectedState:
    def test_a_plan_is_stale_when_the_basis_has_moved(self) -> None:
        assert f.plan(basis_token="a" * 32).is_stale_against("b" * 32)

    def test_a_plan_is_not_stale_when_it_has_not(self) -> None:
        assert not f.plan(basis_token="a" * 32).is_stale_against("a" * 32)


class TestThePreconditionDigestIsTheEntrysWhenThereIsOne:
    def test_a_replacement_pins_the_entry_rather_than_the_membership(self) -> None:
        """The half of the change that removes something is the half that can do harm if the
        world has moved."""
        replacement = f.change(PlannedChangeKind.REPLACE_WITH_GROUP)

        assert replacement.entry is not None
        assert replacement.precondition_digest == replacement.entry.content_digest

    def test_a_membership_change_pins_its_edge(self) -> None:
        membership = f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER)

        assert membership.membership is not None
        assert membership.precondition_digest == membership.membership.content_digest


class TestAStepNumberIsNotNegative:
    def test_it_is_refused(self) -> None:
        with pytest.raises(RemediationValidationError):
            PlannedChange(
                change_id=uuid4(),
                sequence_index=-1,
                kind=PlannedChangeKind.REMOVE_NTFS_ACE,
                target_kind=ChangeTargetKind.RESOURCE,
                target_key=f.PAYROLL,
                principal_sid=f.ALICE,
                principal_key=f.ALICE,
                entry=f.entry(),
            )
