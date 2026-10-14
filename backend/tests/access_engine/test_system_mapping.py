r"""Which identity a SYSTEM subject stands for: one state, and everything derived from it.

ADG-DEMO-01. :class:`SystemMapping` is the single authoritative answer to "who is SYSTEM in
this access check?", assessed once when the token is built. These tests defend:

* each of the four states is reachable, and only from the evidence that names it;
* exactly one finding reports the state, its condition is the state's own, and its detail
  carries the evidence — provenance included — in the same shape every time;
* only ``confirmed`` lets an entry naming SYSTEM grant anything to the SYSTEM subject over
  the path asked about, and only ``confirmed`` can yield a conclusive negative;
* the verdict sentence is derived from the state rather than contradicting it;
* a newer description changes the state only when it actually supports a different one;
* nothing about any other subject, or about a token built to ask what an ACL grants a SID,
  changes.

Synthetic SIDs only (the Phase 0B domain in :mod:`tests.access_engine.support`).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.access_engine import (
    AUTHENTICATED_USERS_SID,
    EVERYONE_SID,
    NETWORK_SID,
    OVERSTATING_CONDITIONS,
    SYSTEM_MAPPING_CONDITIONS,
    SYSTEM_SID,
    UNDERSTATING_CONDITIONS,
    AccessCertainty,
    AccessCondition,
    AccessFinding,
    AccessOutcome,
    AccessPath,
    EffectiveAccess,
    ResourceDacl,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    SystemMapping,
    TokenAssumption,
    TokenSid,
    build_token,
    classify_access,
    default_assumption,
    describe_outcome,
    explain_access,
    resolve_access,
    system_mapping_of,
)
from app.domain import PrincipalKind, SharePermission
from tests.access_engine.support import (
    ALICE,
    EVERYONE,
    FINANCE_TEAM,
    FS01_ADMINS,
    FULL_CONTROL,
    MODIFY,
    READ_EXECUTE,
    allow,
    dacl,
    deny,
    group_sid,
    share_acl,
    share_allow,
    user,
)

RUN = "6f1c2c1e-0000-4000-8000-00000000d001"
"""A synthetic run id: the provenance a confirmed or disputed mapping cites."""

DESCRIBED_AT = dt.datetime(2026, 9, 3, 6, 0, tzinfo=dt.UTC)

SYSTEM_MAPPING_FINDINGS = frozenset(SYSTEM_MAPPING_CONDITIONS.values())


def system(
    kind: PrincipalKind | None = PrincipalKind.WELL_KNOWN,
    *,
    display_name: str | None = "SYSTEM",
    described: bool = True,
) -> SubjectFacts:
    """SYSTEM as a collector described it — or did not."""
    return SubjectFacts(
        key=SYSTEM_SID,
        sid=SYSTEM_SID,
        kind=kind,
        display_name=display_name if kind not in (None, PrincipalKind.UNRESOLVED) else None,
        last_observed_run_id=RUN if described and kind is not None else None,
        last_observed_at=DESCRIBED_AT if described and kind is not None else None,
    )


def share_root(*extra: Any, owner: str | None = SYSTEM_SID) -> ResourceDacl:
    """The share root every Windows file server carries, plus whatever a test adds."""
    return dacl(
        allow(SYSTEM_SID, FULL_CONTROL),
        allow(FS01_ADMINS, FULL_CONTROL),
        allow(FINANCE_TEAM, MODIFY),
        *extra,
        owner_sid=owner,
    )


EVERYONE_SHARE = share_acl(share_allow(EVERYONE, SharePermission.FULL))


def resolve(
    subject: SubjectFacts,
    path: AccessPath,
    *,
    resource: ResourceDacl | None = None,
    assumption: TokenAssumption | None = None,
    groups: tuple[TokenSid, ...] = (),
) -> EffectiveAccess:
    token = build_token(subject, groups, access_path=path, assumption=assumption)
    layer = EVERYONE_SHARE if path is AccessPath.REMOTE_SMB else None
    return resolve_access(token, resource or share_root(), layer)


def mapping_findings(findings: tuple[AccessFinding, ...]) -> list[AccessFinding]:
    return [finding for finding in findings if finding.condition in SYSTEM_MAPPING_FINDINGS]


def the_mapping_finding(subject_token: SubjectToken) -> AccessFinding:
    found = mapping_findings(subject_token.findings)
    assert len(found) == 1, f"expected exactly one SYSTEM mapping finding, got {found}"
    return found[0]


class TestTheStateIsDerivedFromTheEvidence:
    """Every description by path, and the one state each produces."""

    @pytest.mark.parametrize(
        ("kind", "path", "expected"),
        [
            (PrincipalKind.WELL_KNOWN, AccessPath.LOCAL, SystemMapping.CONFIRMED),
            (PrincipalKind.WELL_KNOWN, AccessPath.REMOTE_SMB, SystemMapping.AMBIGUOUS),
            (None, AccessPath.LOCAL, SystemMapping.MISSING),
            (PrincipalKind.UNRESOLVED, AccessPath.LOCAL, SystemMapping.MISSING),
            (None, AccessPath.REMOTE_SMB, SystemMapping.AMBIGUOUS),
            (PrincipalKind.UNRESOLVED, AccessPath.REMOTE_SMB, SystemMapping.AMBIGUOUS),
            (PrincipalKind.USER, AccessPath.LOCAL, SystemMapping.CONFLICTING),
            (PrincipalKind.DOMAIN_GROUP, AccessPath.LOCAL, SystemMapping.CONFLICTING),
            (PrincipalKind.COMPUTER, AccessPath.LOCAL, SystemMapping.CONFLICTING),
            (PrincipalKind.USER, AccessPath.REMOTE_SMB, SystemMapping.CONFLICTING),
        ],
    )
    def test_the_state(
        self, kind: PrincipalKind | None, path: AccessPath, expected: SystemMapping
    ) -> None:
        subject = system(kind)

        assert system_mapping_of(subject, path) is expected
        assert build_token(subject, access_path=path).system_mapping is expected

    @pytest.mark.parametrize("path", list(AccessPath))
    def test_no_other_subject_has_a_system_mapping(self, path: AccessPath) -> None:
        for subject in (
            user(ALICE),
            user(FINANCE_TEAM, kind=PrincipalKind.DOMAIN_GROUP),
            SubjectFacts(key=EVERYONE_SID, sid=EVERYONE_SID, kind=PrincipalKind.WELL_KNOWN),
            SubjectFacts(key="S-1-5-20", sid="S-1-5-20", kind=PrincipalKind.WELL_KNOWN),
        ):
            built = build_token(subject, access_path=path)

            assert system_mapping_of(subject, path) is None
            assert built.system_mapping is None
            assert built.subject_presented
            assert mapping_findings(built.findings) == []


class TestExactlyOneFindingReportsTheState:
    def test_the_conditions_are_a_bijection_over_the_states(self) -> None:
        assert set(SYSTEM_MAPPING_CONDITIONS) == set(SystemMapping)
        assert len(set(SYSTEM_MAPPING_CONDITIONS.values())) == len(SystemMapping)

    @pytest.mark.parametrize("kind", [PrincipalKind.WELL_KNOWN, None, PrincipalKind.USER])
    @pytest.mark.parametrize("path", list(AccessPath))
    def test_one_finding_whose_condition_is_the_states(
        self, kind: PrincipalKind | None, path: AccessPath
    ) -> None:
        built = build_token(system(kind), access_path=path)
        finding = the_mapping_finding(built)

        assert built.system_mapping is not None
        assert finding.condition is SYSTEM_MAPPING_CONDITIONS[built.system_mapping]
        assert finding.detail["state"] == built.system_mapping.value

    def test_the_detail_has_one_shape_in_every_state(self) -> None:
        shapes = {
            frozenset(the_mapping_finding(build_token(system(kind), access_path=path)).detail)
            for kind in (
                PrincipalKind.WELL_KNOWN,
                None,
                PrincipalKind.UNRESOLVED,
                PrincipalKind.USER,
            )
            for path in AccessPath
        }

        assert shapes == {
            frozenset(
                {
                    "state",
                    "subject_sid",
                    "subject_key",
                    "access_path",
                    "presented",
                    "described_as",
                    "described_name",
                    "expected_kind",
                    "last_observed_run_id",
                    "last_observed_at",
                    "withheld_groups",
                }
            )
        }

    def test_every_state_understates_and_only_unsettled_ones_overstate(self) -> None:
        """No SYSTEM answer is ever a conclusive negative; only a settled identity is bounded."""
        for state, condition in SYSTEM_MAPPING_CONDITIONS.items():
            assert condition in UNDERSTATING_CONDITIONS
            assert (condition in OVERSTATING_CONDITIONS) is (state is not SystemMapping.CONFIRMED)


class TestConfirmed:
    """Local access to a SYSTEM a collector described as the well-known principal."""

    def test_the_entry_naming_system_grants_and_the_rights_are_a_lower_bound(self) -> None:
        access = resolve(system(), AccessPath.LOCAL)
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.GRANTED
        assert verdict.is_conclusive
        assert access.certainty is AccessCertainty.AT_LEAST, "LocalSystem's groups are unmodeled"
        assert access.rights.value & FULL_CONTROL == FULL_CONTROL
        assert [applied.entry.trustee_key for applied in access.ntfs.granted_by] == [SYSTEM_SID]
        assert verdict.reason == describe_outcome(AccessOutcome.GRANTED)

    def test_the_mapping_and_its_provenance_are_stated(self) -> None:
        detail = the_mapping_finding(build_token(system(), access_path=AccessPath.LOCAL)).detail

        assert detail["state"] == "confirmed"
        assert detail["presented"] is True
        assert detail["described_as"] == "well_known"
        assert detail["described_name"] == "SYSTEM"
        assert detail["last_observed_run_id"] == RUN
        assert detail["last_observed_at"] == DESCRIBED_AT.isoformat()

    def test_the_explanation_runs_from_system_through_its_entry(self) -> None:
        access = resolve(system(), AccessPath.LOCAL)
        explanation = explain_access(access, share_root())

        # The entry naming SYSTEM, and SYSTEM's ownership of the root: both are its own.
        assert explanation.contributing
        assert {path.chain for path in explanation.contributing} == {(SYSTEM_SID,)}
        assert any(path.ace_position == 0 for path in explanation.contributing)

    def test_an_acl_naming_only_the_servers_administrators_is_not_a_no(self) -> None:
        """LocalSystem is in BUILTIN\\Administrators. ADG does not model that, so it must not
        conclude SYSTEM is shut out of a directory only the administrators may reach."""
        access = resolve(
            system(), AccessPath.LOCAL, resource=dacl(allow(FS01_ADMINS, FULL_CONTROL))
        )
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.INDETERMINATE
        assert not verdict.is_conclusive
        assert "LocalSystem" in verdict.reason
        assert verdict.reason != describe_outcome(AccessOutcome.INDETERMINATE)

    def test_nothing_on_the_acl_is_still_not_a_conclusive_no(self) -> None:
        access = resolve(system(), AccessPath.LOCAL, resource=dacl(allow(ALICE, MODIFY)))

        assert classify_access(access).outcome is AccessOutcome.INDETERMINATE


class TestAmbiguous:
    """Over a share, SYSTEM is a computer account the question does not name."""

    def test_the_subject_entry_is_kept_and_never_matched(self) -> None:
        built = build_token(system(), access_path=AccessPath.REMOTE_SMB)

        assert built.entries[0].key == SYSTEM_SID
        assert built.entries[0].origin is SidOrigin.SUBJECT
        assert not built.subject_presented
        assert not built.contains(SYSTEM_SID)
        assert built.entry(SYSTEM_SID) is None

    def test_the_default_question_is_indeterminate_not_no_grant(self) -> None:
        access = resolve(system(), AccessPath.REMOTE_SMB)
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.INDETERMINATE
        assert not verdict.is_conclusive
        assert access.certainty is AccessCertainty.UNCERTAIN
        assert access.ntfs.granted_by == ()
        assert "computer account" in verdict.reason
        assert "No entry on either ACL names it" not in verdict.reason

    def test_no_explanation_path_describes_system_as_mapped(self) -> None:
        access = resolve(system(), AccessPath.REMOTE_SMB)

        assert explain_access(access, share_root(), EVERYONE_SHARE).paths == ()

    def test_assumed_session_sids_still_apply_and_are_qualified(self) -> None:
        """Every candidate computer account carries these, so what they reach is real."""
        resource = share_root(allow(EVERYONE, READ_EXECUTE))
        access = resolve(
            system(),
            AccessPath.REMOTE_SMB,
            resource=resource,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )
        verdict = classify_access(access)
        explanation = explain_access(access, resource, EVERYONE_SHARE)

        assert verdict.outcome is AccessOutcome.GRANTED
        assert access.certainty is AccessCertainty.UNCERTAIN
        assert {applied.entry.trustee_key for applied in access.ntfs.granted_by} == {EVERYONE}
        assert SYSTEM_SID not in {path.trustee_key for path in explanation.paths}
        assert verdict.reason.startswith("Rights survive, but only through SIDs")
        assert {EVERYONE_SID, AUTHENTICATED_USERS_SID, NETWORK_SID} <= access.token.keys

    def test_ownership_gives_a_network_session_nothing(self) -> None:
        access = resolve(system(), AccessPath.REMOTE_SMB, resource=dacl(owner_sid=SYSTEM_SID))

        assert access.ntfs.owner_rights is None
        assert AccessCondition.OWNER_IMPLICIT_RIGHTS not in access.conditions

    def test_a_deny_naming_system_is_not_reported_as_the_cause(self) -> None:
        access = resolve(
            system(),
            AccessPath.REMOTE_SMB,
            resource=dacl(deny(SYSTEM_SID, FULL_CONTROL), allow(EVERYONE, MODIFY)),
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )

        assert access.ntfs.denied_by == ()
        assert classify_access(access).outcome is AccessOutcome.GRANTED

    def test_groups_observed_for_systems_local_identity_are_withheld_and_named(self) -> None:
        observed = group_sid(FS01_ADMINS, path=(SYSTEM_SID, FS01_ADMINS))
        built = build_token(system(), [observed], access_path=AccessPath.REMOTE_SMB)

        assert not built.contains(FS01_ADMINS)
        assert the_mapping_finding(built).detail["withheld_groups"] == [FS01_ADMINS]

        local = build_token(system(), [observed], access_path=AccessPath.LOCAL)
        assert local.contains(FS01_ADMINS)
        assert the_mapping_finding(local).detail["withheld_groups"] == []


class TestMissing:
    """Nothing described S-1-5-18: no mapping is invented."""

    @pytest.mark.parametrize("kind", [None, PrincipalKind.UNRESOLVED])
    def test_no_token_is_invented_for_it(self, kind: PrincipalKind | None) -> None:
        subject = system(kind)
        built = build_token(subject, access_path=AccessPath.LOCAL)

        assert default_assumption(subject) is TokenAssumption.SIDS_ONLY
        assert [entry.key for entry in built.entries] == [SYSTEM_SID]
        assert AccessCondition.ASSUMED_TOKEN_SIDS not in {f.condition for f in built.findings}

    @pytest.mark.parametrize("kind", [None, PrincipalKind.UNRESOLVED])
    def test_a_literal_match_is_reported_but_never_certain(
        self, kind: PrincipalKind | None
    ) -> None:
        access = resolve(system(kind), AccessPath.LOCAL)
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.GRANTED
        assert access.certainty is AccessCertainty.UNCERTAIN
        assert "no collector has described S-1-5-18" in verdict.reason

    def test_an_empty_answer_is_indeterminate(self) -> None:
        access = resolve(system(None), AccessPath.LOCAL, resource=dacl(allow(ALICE, MODIFY)))
        verdict = classify_access(access)

        assert verdict.outcome is AccessOutcome.INDETERMINATE
        assert not verdict.is_conclusive
        assert "No collector has described S-1-5-18" in verdict.reason

    def test_the_absence_of_a_description_is_carried_as_none(self) -> None:
        detail = the_mapping_finding(build_token(system(None), access_path=AccessPath.LOCAL)).detail

        assert detail["described_as"] is None
        assert detail["described_name"] is None
        assert detail["last_observed_run_id"] is None


class TestConflicting:
    """A collector said S-1-5-18 is something Windows says it is not."""

    def test_both_sides_and_the_provenance_are_preserved(self) -> None:
        subject = system(PrincipalKind.USER, display_name="svc-backup")
        detail = the_mapping_finding(build_token(subject, access_path=AccessPath.LOCAL)).detail

        assert detail["state"] == "conflicting"
        assert detail["described_as"] == "user"
        assert detail["described_name"] == "svc-backup"
        assert detail["expected_kind"] == "well_known"
        assert detail["last_observed_run_id"] == RUN

    def test_the_disputed_kind_does_not_choose_the_token(self) -> None:
        """A 'user' description would otherwise have added Everyone and the logon SID."""
        subject = system(PrincipalKind.USER, display_name="svc-backup")

        assert default_assumption(subject) is TokenAssumption.SIDS_ONLY

    def test_no_conclusion_is_drawn(self) -> None:
        subject = system(PrincipalKind.USER, display_name="svc-backup")
        granted = classify_access(resolve(subject, AccessPath.LOCAL))
        empty = classify_access(
            resolve(subject, AccessPath.LOCAL, resource=dacl(allow(ALICE, MODIFY)))
        )

        assert granted.certainty is AccessCertainty.UNCERTAIN
        assert "disputed" in granted.reason
        assert empty.outcome is AccessOutcome.INDETERMINATE

    def test_the_conflict_is_not_hidden_behind_the_share_question(self) -> None:
        subject = system(PrincipalKind.USER, display_name="svc-backup")
        built = build_token(subject, access_path=AccessPath.REMOTE_SMB)

        assert built.system_mapping is SystemMapping.CONFLICTING
        assert not built.subject_presented, "and S-1-5-18 is still never presented over SMB"


class TestNewerEvidence:
    """The state follows the description it is given — and changes only when that does."""

    def test_a_missing_mapping_is_confirmed_once_a_description_arrives(self) -> None:
        before = build_token(system(None), access_path=AccessPath.LOCAL)
        after = build_token(system(), access_path=AccessPath.LOCAL)

        assert before.system_mapping is SystemMapping.MISSING
        assert after.system_mapping is SystemMapping.CONFIRMED

    def test_a_conflict_is_settled_only_by_a_well_known_description(self) -> None:
        disputed = system(PrincipalKind.USER, display_name="svc-backup")
        still_disputed = system(PrincipalKind.DOMAIN_GROUP, display_name="SYSTEM")
        unresolved = system(PrincipalKind.UNRESOLVED)

        assert system_mapping_of(disputed, AccessPath.LOCAL) is SystemMapping.CONFLICTING
        assert system_mapping_of(still_disputed, AccessPath.LOCAL) is SystemMapping.CONFLICTING
        assert system_mapping_of(unresolved, AccessPath.LOCAL) is SystemMapping.MISSING
        assert system_mapping_of(system(), AccessPath.LOCAL) is SystemMapping.CONFIRMED

    def test_no_description_resolves_the_share_question(self) -> None:
        """Ambiguity over SMB is structural: a better description of S-1-5-18 cannot end it."""
        for kind in (None, PrincipalKind.UNRESOLVED, PrincipalKind.WELL_KNOWN):
            assert system_mapping_of(system(kind), AccessPath.REMOTE_SMB) is (
                SystemMapping.AMBIGUOUS
            )

    def test_a_display_name_alone_never_confirms(self) -> None:
        """Names are metadata. Only the kind a collector reported settles the description."""
        named = SubjectFacts(
            key=SYSTEM_SID, sid=SYSTEM_SID, kind=PrincipalKind.UNRESOLVED, display_name=None
        )

        assert system_mapping_of(named, AccessPath.LOCAL) is SystemMapping.MISSING


class TestEveryoneElseIsUnchanged:
    def test_a_token_built_to_ask_what_an_acl_grants_a_sid_still_matches_it(self) -> None:
        """The risk engine builds tokens directly to ask what entries naming a SID grant."""
        literal = SubjectToken(
            subject=SubjectFacts(key=SYSTEM_SID, sid=SYSTEM_SID),
            assumption=TokenAssumption.SIDS_ONLY,
            access_path=AccessPath.REMOTE_SMB,
            entries=(TokenSid(key=SYSTEM_SID, sid=SYSTEM_SID, origin=SidOrigin.SUBJECT),),
        )

        assert literal.subject_presented
        assert literal.system_mapping is None
        assert literal.contains(SYSTEM_SID)

    def test_an_entry_naming_system_never_matches_another_subject(self) -> None:
        for path in AccessPath:
            access = resolve(user(ALICE), path, assumption=TokenAssumption.AUTHENTICATED_USER)

            assert SYSTEM_SID not in {a.entry.trustee_key for a in access.ntfs.granted_by}
            assert access.token.system_mapping is None
