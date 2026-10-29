r"""ADG-DEMO-01: the SYSTEM explanation that contradicted itself.

Asked "what can SYSTEM do to ``\\GP-FS-01\Shared``?", the demo answered, in one response:

* verdict ``no_grant``, certainty ``certain`` — *"No entry on either ACL names it with
  anything to give"*;
* ``effective.ntfs.granted_by``: the entry naming **SYSTEM** with **Full Control**, and a
  causal path from SYSTEM through it.

Both cannot be true, and neither was the honest answer. A network logon never carries
``S-1-5-18``: a SYSTEM process reaches a share as the *computer account* of the machine it
runs on, so the token ADG built — ``{S-1-5-18}`` over ``remote_smb`` — is one Windows never
issues, and matching the NTFS entry against it described a mapping nothing supported. With
``assumption=authenticated_user`` the same question answered *granted, Full Control,
certain*, from a token holding ``S-1-5-18`` and ``NETWORK`` together.

This module pins the fixture that reproduced it, and checks it **only through the engine's
pre-existing public surface**, so it fails on the defective code for the defect's own reason
rather than on a missing name. The state model that replaced the contradiction is tested in
``test_system_mapping.py``.

All SIDs are synthetic: the Phase 0B domain from :mod:`tests.access_engine.support`.
"""

from __future__ import annotations

import pytest

from app.access_engine import (
    AccessCertainty,
    AccessCondition,
    AccessExplanation,
    AccessOutcome,
    AccessPath,
    AccessVerdict,
    EffectiveAccess,
    ResourceDacl,
    ShareDacl,
    SidOrigin,
    SubjectFacts,
    TokenAssumption,
    build_token,
    classify_access,
    explain_access,
    resolve_access,
)
from app.access_engine.evaluation import AclEvaluation
from app.domain import PrincipalKind, SharePermission
from tests.access_engine.support import (
    ALICE,
    AUTHENTICATED_USERS,
    EVERYONE,
    FINANCE_TEAM,
    FS01_ADMINS,
    FULL_CONTROL,
    MODIFY,
    READ_EXECUTE,
    allow,
    dacl,
    share_acl,
    share_allow,
)

SYSTEM = "S-1-5-18"

#: The sentence the defective verdict printed beside an entry that named the subject.
NOTHING_NAMES_IT = "No entry on either ACL names it"


def share_root() -> ResourceDacl:
    """A share root exactly as Windows (and Genesee Press's generator) writes one.

    SYSTEM and the server's administrators hold Full Control; one team holds Modify; SYSTEM
    owns the directory, which is the ordinary case for anything Windows created.
    """
    return dacl(
        allow(SYSTEM, FULL_CONTROL),
        allow(FS01_ADMINS, FULL_CONTROL),
        allow(FINANCE_TEAM, MODIFY),
        allow(EVERYONE, READ_EXECUTE, flags=0x0B),  # inherit-only: it never applies here
        owner_sid=SYSTEM,
    )


SHARES: dict[str, ShareDacl] = {
    "everyone": share_acl(share_allow(EVERYONE, SharePermission.FULL)),
    "authenticated": share_acl(
        share_allow(AUTHENTICATED_USERS, SharePermission.CHANGE),
        share_allow(FS01_ADMINS, SharePermission.FULL),
    ),
    "mirror": share_acl(
        share_allow(FS01_ADMINS, SharePermission.FULL),
        share_allow(FINANCE_TEAM, SharePermission.CHANGE),
    ),
}
"""The three share ACL shapes the demo estate generates. None of them names SYSTEM."""

ASSUMPTIONS: tuple[TokenAssumption | None, ...] = (
    None,  # the production default, which is what the demo asked with
    TokenAssumption.SIDS_ONLY,
    TokenAssumption.AUTHENTICATED_USER,
)

#: What a collector says SYSTEM is, in the cases the engine has to distinguish.
DESCRIPTIONS: dict[str, PrincipalKind | None] = {
    "well_known": PrincipalKind.WELL_KNOWN,
    "undescribed": None,
    "unresolved": PrincipalKind.UNRESOLVED,
    "described_as_a_user": PrincipalKind.USER,
}


def system(kind: PrincipalKind | None = PrincipalKind.WELL_KNOWN) -> SubjectFacts:
    return SubjectFacts(
        key=SYSTEM,
        sid=SYSTEM,
        kind=kind,
        display_name="SYSTEM" if kind is PrincipalKind.WELL_KNOWN else None,
    )


def answer(
    subject: SubjectFacts,
    path: AccessPath,
    assumption: TokenAssumption | None,
    share: ShareDacl | None,
) -> tuple[EffectiveAccess, AccessVerdict, AccessExplanation]:
    resource = share_root()
    token = build_token(subject, access_path=path, assumption=assumption)
    layer = share if path is AccessPath.REMOTE_SMB else None
    access = resolve_access(token, resource, layer)
    return access, classify_access(access), explain_access(access, resource, layer)


def layers(access: EffectiveAccess) -> tuple[AclEvaluation, ...]:
    return (access.ntfs,) if access.share is None else (access.ntfs, access.share)


def assert_consistent(
    access: EffectiveAccess, verdict: AccessVerdict, explanation: AccessExplanation
) -> None:
    """The invariant the defect broke: the sentence, the label and the evidence agree."""
    granting = [evaluation.layer for evaluation in layers(access) if evaluation.granted_by]
    if verdict.outcome is AccessOutcome.NO_GRANT and granting:
        assert NOTHING_NAMES_IT not in verdict.reason, (
            f"the verdict says nothing names the subject while {granting} granted it rights"
        )
    if verdict.outcome is AccessOutcome.GRANTED:
        assert not access.rights.is_empty
    if verdict.outcome in (AccessOutcome.NO_GRANT, AccessOutcome.DENIED):
        assert verdict.is_conclusive
    if not verdict.is_conclusive:
        assert verdict.outcome is AccessOutcome.INDETERMINATE


def subject_matches(access: EffectiveAccess) -> list[str]:
    """Every entry the subject's *own* SID matched, on any layer, as ``layer:trustee``."""
    return [
        f"{evaluation.layer.value}:{applied.entry.trustee_key}"
        for evaluation in layers(access)
        for applied in (*evaluation.granted_by, *evaluation.denied_by, *evaluation.superseded)
        if applied.matched.origin is SidOrigin.SUBJECT
    ]


class TestTheOriginalContradiction:
    """The exact question the demo was asked, with the exact answer it must not give."""

    def test_the_demo_question_no_longer_contradicts_itself(self) -> None:
        access, verdict, explanation = answer(
            system(), AccessPath.REMOTE_SMB, None, SHARES["everyone"]
        )

        assert_consistent(access, verdict, explanation)
        assert subject_matches(access) == [], "S-1-5-18 is never in a network logon token"
        assert not explanation.paths, "no route may run from SYSTEM through an entry over SMB"
        assert verdict.outcome is AccessOutcome.INDETERMINATE
        assert not verdict.is_conclusive
        assert access.certainty is not AccessCertainty.CERTAIN

    def test_the_authenticated_variant_no_longer_grants_full_control_with_certainty(
        self,
    ) -> None:
        access, verdict, _ = answer(
            system(), AccessPath.REMOTE_SMB, TokenAssumption.AUTHENTICATED_USER, SHARES["everyone"]
        )

        assert subject_matches(access) == []
        assert access.rights.is_empty, "nothing here is granted to Everyone or the session SIDs"
        assert verdict.outcome is AccessOutcome.INDETERMINATE
        assert access.certainty is not AccessCertainty.CERTAIN

    def test_owning_the_directory_gives_a_network_session_nothing(self) -> None:
        """SYSTEM owns the root. Its implicit owner rights are local, not remote."""
        access, _, _ = answer(system(), AccessPath.REMOTE_SMB, None, SHARES["mirror"])

        assert AccessCondition.OWNER_IMPLICIT_RIGHTS not in access.conditions
        assert access.ntfs.owner_rights is None


class TestNoContradictionAnywhereInTheMatrix:
    """Every description by path by assumption by share shape: no sentence contradicts a label.

    The defect was one cell of this matrix. The invariant is checked over all of them so that
    a later change cannot move the contradiction into a neighbouring cell unnoticed.
    """

    @pytest.mark.parametrize("description", sorted(DESCRIPTIONS))
    @pytest.mark.parametrize("assumption", ASSUMPTIONS, ids=lambda a: a.value if a else "default")
    @pytest.mark.parametrize("shape", sorted(SHARES))
    def test_over_smb(
        self, description: str, assumption: TokenAssumption | None, shape: str
    ) -> None:
        access, verdict, explanation = answer(
            system(DESCRIPTIONS[description]), AccessPath.REMOTE_SMB, assumption, SHARES[shape]
        )

        assert_consistent(access, verdict, explanation)
        assert subject_matches(access) == []
        assert access.certainty is not AccessCertainty.CERTAIN
        assert verdict.outcome not in (AccessOutcome.NO_GRANT, AccessOutcome.DENIED)

    @pytest.mark.parametrize("description", sorted(DESCRIPTIONS))
    @pytest.mark.parametrize("assumption", ASSUMPTIONS, ids=lambda a: a.value if a else "default")
    def test_locally(self, description: str, assumption: TokenAssumption | None) -> None:
        access, verdict, explanation = answer(
            system(DESCRIPTIONS[description]), AccessPath.LOCAL, assumption, None
        )

        assert_consistent(access, verdict, explanation)
        assert verdict.outcome not in (AccessOutcome.NO_GRANT, AccessOutcome.DENIED)
        assert access.certainty is not AccessCertainty.CERTAIN, "no SYSTEM token is complete"
        confirmed = DESCRIPTIONS[description] is PrincipalKind.WELL_KNOWN
        if confirmed:
            assert subject_matches(access) == ["ntfs:S-1-5-18"]
            assert verdict.may_understate and not verdict.may_overstate
        else:
            assert verdict.may_understate and verdict.may_overstate, (
                "an undescribed or disputed SYSTEM is bounded in neither direction"
            )


class TestTheVerdictSentenceFollowsTheLayers:
    """The verdict half of the defect, which is not specific to SYSTEM at all.

    ``no_grant`` printed one fixed sentence — *nothing on either ACL names it* — even when
    one layer named the principal and granted it rights the other layer withheld.
    """

    def test_ntfs_grants_and_the_share_names_nobody(self) -> None:
        resource = dacl(allow(ALICE, MODIFY))
        share = share_acl(share_allow(FS01_ADMINS, SharePermission.FULL))
        token = build_token(
            SubjectFacts(key=ALICE, sid=ALICE, kind=PrincipalKind.USER),
            access_path=AccessPath.REMOTE_SMB,
            assumption=TokenAssumption.SIDS_ONLY,
        )
        access = resolve_access(token, resource, share)
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.NO_GRANT
        assert access.ntfs.granted_by
        assert NOTHING_NAMES_IT not in verdict.reason
        assert "NTFS" in verdict.reason and "share" in verdict.reason
