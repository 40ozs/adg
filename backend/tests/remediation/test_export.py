"""The signed document, and the script somebody runs at two in the morning.

Two halves. The signature tests are ordinary crypto hygiene: it covers the bytes, a tamper
breaks it, a missing key refuses. The runbook tests are the unusual ones, and they matter
more — the script is the only artifact of this whole product that a person executes, and its
two safety properties (it does nothing without ``-Execute``, and every step re-checks its
precondition on the machine) are properties of generated text that nothing else would catch.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domain import AceSource, MembershipEdgeKind, SharePermission
from app.domain.remediation import (
    ApprovalDecision,
    PlannedChangeKind,
    PreconditionVerdict,
)
from app.remediation.errors import RemediationConflict
from app.remediation.export import (
    EXPORT_DOCUMENT_KIND,
    build_document,
    document_digest,
    key_id_for,
    render_runbook,
    sign_document,
    verify_document,
)
from app.remediation.model import (
    ApprovalRecord,
    ChangePlan,
    ChangePrecondition,
    PlannedChange,
    PreconditionReport,
)
from tests.remediation import factories as f

KEY = "a-development-signing-key-at-least-this-long"


def a_report(plan_id: UUID, satisfied: bool = True) -> PreconditionReport:
    return PreconditionReport(
        plan_id=plan_id,
        checked_at=f.NOW,
        basis_token="a" * 32,
        plan_basis_token="a" * 32,
        preconditions=(
            ChangePrecondition(
                change_id=uuid4(),
                sequence_index=0,
                verdict=(
                    PreconditionVerdict.SATISFIED if satisfied else PreconditionVerdict.MISSING
                ),
                expected_digest="b" * 64,
                observed_digest="b" * 64 if satisfied else None,
                summary="unchanged" if satisfied else "gone",
            ),
        ),
    )


def an_approval(plan: ChangePlan) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=uuid4(),
        plan_id=plan.plan_id,
        decision=ApprovalDecision.APPROVE,
        approver_subject="dana@example.test",
        approver_display_name="Dana",
        approver_roles=("remediation_approver",),
        decided_at=f.NOW,
        rationale=None,
        plan_digest=plan.digest,
        basis_token="a" * 32,
    )


def a_document(plan: ChangePlan | None = None) -> dict[str, Any]:
    subject = plan if plan is not None else f.plan()
    return build_document(
        subject,
        approvals=[an_approval(subject)],
        preconditions=a_report(subject.plan_id),
        impact={"lost_access": 1},
        audit_head_digest="c" * 64,
        audit_head_index=3,
        exported_by_subject="ops@example.test",
        exported_by_display_name="Ops",
        exported_at=f.NOW,
    )


class TestTheSignature:
    def test_it_verifies_against_the_document_it_covers(self) -> None:
        document = a_document()
        signature, _ = sign_document(document, KEY)

        assert verify_document(document, signature, KEY)

    def test_a_changed_field_breaks_it(self) -> None:
        document = a_document()
        signature, _ = sign_document(document, KEY)
        document["plan"]["title"] = "Something else"

        assert not verify_document(document, signature, KEY)

    def test_a_changed_step_breaks_it(self) -> None:
        """The field somebody with an interest would change."""
        document = a_document()
        signature, _ = sign_document(document, KEY)
        document["plan"]["changes"][0]["target_key"] = "\\\\fs01\\somewhere-else"

        assert not verify_document(document, signature, KEY)

    def test_another_key_does_not_verify(self) -> None:
        document = a_document()
        signature, _ = sign_document(document, KEY)

        assert not verify_document(document, signature, "a-different-key-entirely-yes")

    def test_an_empty_signature_never_verifies(self) -> None:
        assert not verify_document(a_document(), "", KEY)

    def test_an_unconfigured_deployment_refuses_to_sign(self) -> None:
        """Refused rather than emitting an unsigned document: an unsigned change plan is
        indistinguishable from one somebody typed."""
        with pytest.raises(RemediationConflict) as raised:
            sign_document(a_document(), "")

        assert "ADG_REMEDIATION_SIGNING_KEY" in str(raised.value)

    def test_the_key_id_changes_when_the_key_does(self) -> None:
        """So a verifier holding the old key says "signed with a key I do not have" rather
        than reporting tampering that did not happen."""
        assert key_id_for(KEY) != key_id_for(KEY + "!")

    def test_the_key_id_is_stable(self) -> None:
        assert key_id_for(KEY) == key_id_for(KEY)

    def test_the_key_id_does_not_contain_the_key(self) -> None:
        assert KEY not in key_id_for(KEY)


class TestTheDocument:
    def test_it_names_what_it_is(self) -> None:
        """A verifier that recognizes only this kind refuses anything else, rather than
        checking a signature over a document whose meaning it does not know."""
        assert a_document()["kind"] == EXPORT_DOCUMENT_KIND

    def test_it_states_inside_the_signed_bytes_that_adg_changed_nothing(self) -> None:
        """A document that did not say so could be mistaken -- by a person or by a pipeline
        somebody builds later -- for a record of work completed."""
        execution = a_document()["execution"]

        assert execution["performed_by_adg"] is False
        assert "has made none of these changes" in execution["statement"]

    def test_it_carries_the_approval_and_the_digest_it_approved(self) -> None:
        plan = f.plan()
        document = a_document(plan)

        assert document["approvals"][0]["plan_digest"] == plan.digest

    def test_it_carries_the_preconditions_that_were_checked(self) -> None:
        assert a_document()["preconditions"]["satisfied"] is True

    def test_it_carries_the_audit_head_for_comparison_outside_the_database(self) -> None:
        audit = a_document()["audit"]

        assert audit["head_digest"] == "c" * 64
        assert audit["head_index"] == 3
        assert "outside ADG" in audit["note"]
        # The wording has to say which head it is: the export event carries this document's
        # digest, so it cannot be inside it, and a verifier told to compare against "the"
        # head would find a mismatch of exactly one event and read it as tampering.
        assert "BEFORE the export event" in audit["note"]

    def test_the_digest_is_over_the_canonical_bytes(self) -> None:
        document = a_document()

        assert document_digest(document) == document_digest(dict(reversed(list(document.items()))))


class TestTheRunbookIsSafeToRun:
    def _script(self, *changes: PlannedChange) -> str:
        plan = f.plan(*changes) if changes else f.plan()
        return render_runbook(
            plan,
            document_digest_value="d" * 64,
            signature="e" * 64,
            key_id="f" * 16,
            approver="dana@example.test",
        )

    def test_it_does_nothing_without_the_execute_switch(self) -> None:
        """A remediation script that is destructive when double-clicked is one that will
        eventually be double-clicked."""
        script = self._script()

        assert "[switch]$Execute" in script
        assert "if (-not $Execute)" in script
        assert "DRY RUN" in script

    def test_every_step_is_guarded_by_the_dry_run_check(self) -> None:
        script = self._script(
            f.change(index=0, entry=f.entry(ace_key="a")),
            f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER, index=1),
        )

        assert script.count("Test-AdgShouldExecute") >= 2 + 1  # two steps plus the definition

    def test_every_step_re_checks_its_precondition_on_the_machine(self) -> None:
        """ADG's own check is against what it last collected, which can be hours old."""
        script = self._script(
            f.change(index=0, entry=f.entry(ace_key="a")),
            f.change(index=1, entry=f.entry(ace_key="b")),
        )

        assert script.count("Stop-AdgRun") >= 3

    def test_an_ntfs_step_compares_the_mask_adg_observed(self) -> None:
        script = self._script(f.change(entry=f.entry(access_mask=f.WRITE_MASK)))

        assert f"$adgExpectedMask1 = {f.WRITE_MASK}" in script

    def test_it_refuses_to_act_when_more_than_one_entry_matches(self) -> None:
        """ADG planned for exactly one, and acting on whichever sorted first is how a
        remediation removes the wrong entry."""
        assert "More than one entry matches" in self._script()

    def test_it_carries_the_signature_for_verification_before_running(self) -> None:
        script = self._script()

        assert "e" * 64 in script
        assert "Signing key id" in script

    def test_it_says_adg_made_no_change(self) -> None:
        assert "ADG HAS NOT MADE THESE CHANGES" in self._script()


class TestTheRunbookSendsSomebodyToTheRightMachine:
    def _script(self, change: PlannedChange) -> str:
        return render_runbook(
            f.plan(change),
            document_digest_value="d" * 64,
            signature="e" * 64,
            key_id="f" * 16,
            approver=None,
        )

    def test_a_local_group_step_says_which_computer(self) -> None:
        """A BUILTIN SID names a different group on every machine, and a plan that sent
        somebody to a domain controller to edit a local group would fail there silently."""
        script = self._script(
            f.change(
                PlannedChangeKind.REMOVE_GROUP_MEMBER,
                target_key=f.BUILTIN_ADMINS,
                membership=f.edge(
                    group_key=f.BUILTIN_ADMINS,
                    edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                ),
            )
        )

        assert "LOCAL group on fs01" in script
        assert "Remove-LocalGroupMember" in script
        assert "Remove-ADGroupMember" not in script

    def test_a_directory_group_step_uses_the_directory_tooling(self) -> None:
        script = self._script(f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER))

        assert "Remove-ADGroupMember" in script
        assert "Remove-LocalGroupMember" not in script

    def test_an_inherited_entry_carries_a_warning_rather_than_a_command_that_fails(
        self,
    ) -> None:
        """An inherited ACE cannot be removed on the folder that shows it. The script says
        so instead of emitting a Set-Acl that silently does nothing."""
        script = self._script(
            f.change(
                entry=f.entry(source=AceSource.INHERITED, inherited_from=f.FINANCE, ace_flags=0x13)
            )
        )

        assert "INHERITED" in script
        assert f.FINANCE in script

    def test_a_share_step_uses_the_share_cmdlets(self) -> None:
        script = self._script(f.change(PlannedChangeKind.REMOVE_SHARE_ACE))

        assert "Revoke-SmbShareAccess" in script
        assert "Get-SmbShareAccess" in script

    def test_a_share_narrowing_grants_the_lower_level_back(self) -> None:
        script = self._script(
            f.change(
                PlannedChangeKind.MODIFY_SHARE_ACE,
                entry=f.share_entry(permission=SharePermission.FULL),
                after_permission=SharePermission.READ,
            )
        )

        assert "Revoke-SmbShareAccess" in script
        assert "-AccessRight Read" in script

    def test_a_replacement_renders_both_halves_in_order(self) -> None:
        script = self._script(f.change(PlannedChangeKind.REPLACE_WITH_GROUP))

        removal = script.index("Set-Acl")
        addition = script.index("Add-ADGroupMember")
        assert removal < addition


class TestTheRunbookCannotBeBrokenByItsOwnInputs:
    def test_a_title_containing_the_comment_terminator_is_defanged(self) -> None:
        """Otherwise the banner ends early and the rest of it becomes executable code."""
        plan = f.plan(title="Quarterly #> Remove-Item C:\\ -Recurse")

        script = render_runbook(
            plan,
            document_digest_value="d" * 64,
            signature="e" * 64,
            key_id="f" * 16,
            approver=None,
        )

        banner = script[: script.index("[CmdletBinding()]")]
        assert banner.count("#>") == 1

    def test_a_quote_in_a_path_is_escaped(self) -> None:
        plan = f.plan(f.change(target_display="\\\\fs01\\finance\\o'brien", entry=f.entry()))

        script = render_runbook(
            plan,
            document_digest_value="d" * 64,
            signature="e" * 64,
            key_id="f" * 16,
            approver=None,
        )

        assert "'\\\\fs01\\finance\\o''brien'" in script
