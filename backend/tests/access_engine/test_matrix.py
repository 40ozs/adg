"""Properties that must hold for every case in the matrix, not just the ones we pictured.

:mod:`tests.validation.test_windows_oracle` checks the NTFS access check against Windows.
That leaves everything the resolver builds on top of it — the layer crossing, the access
path, DACL provenance, the certainty accounting — about which Windows has no opinion, because
it is ADG's own bookkeeping of what it did and did not see.

Those are held to **invariants**: statements that must be true of every answer regardless of
the inputs. An invariant is a weaker claim than an expected value and a much stronger test
than a hand-written case, because it cannot be satisfied by accident. "The effective rights
are a subset of what NTFS granted" has no correct-by-coincidence: either the crossing is an
intersection or some case in several hundred will show that it is not.

The ones that carry the most weight here:

* **Soundness.** No answer may contain a right that no ACE the token matched granted. A
  resolver that invented access would be the worst possible defect in an audit tool, and it
  is the one no expected-value test looks for.
* **Layer ordering.** Remote access can never exceed local access over the same NTFS DACL,
  because the share can only remove rights. This is the property the whole two-layer model
  reduces to.
* **Directional honesty.** An unread descriptor may never produce a `CERTAIN` answer, in
  either direction. This is the invariant Phase 4C found violated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from app.access_engine import (
    CONDITION_DESCRIPTIONS,
    ESCALATION_RIGHTS,
    FILE_ALL_ACCESS,
    OWNER_IMPLICIT_RIGHTS,
    AccessCertainty,
    AccessCondition,
    AccessPath,
    AclProvenance,
    EffectiveAccess,
    LimitingLayer,
    RightsLayer,
    ShareDacl,
    certainty_of,
    resolve_access,
)
from app.domain import AceType
from tests.access_engine.matrix import (
    ACL_CASE_COUNT,
    RESOLUTION_CASE_COUNT,
    AceSpec,
    AclCase,
    ResolutionCase,
    acl_cases,
    resolution_cases,
)

CASES = resolution_cases()
ACL_CASES = acl_cases()


def resolve(case: ResolutionCase) -> EffectiveAccess:
    return resolve_access(case.subject_token(), case.resource(), case.share_dacl())


def check_every_case(predicate: Callable[[ResolutionCase, EffectiveAccess], str | None]) -> None:
    """Run one invariant over the whole matrix and report every case that breaks it.

    Reporting all of them rather than stopping at the first is the difference between "this
    is broken" and "this is broken in these three shapes", which is the thing worth knowing
    when a property fails across hundreds of cases.
    """
    failures = [
        f"{case.case_id}: {message}"
        for case in CASES
        if (message := predicate(case, resolve(case))) is not None
    ]
    assert not failures, f"{len(failures)} of {len(CASES)} cases break this invariant:\n  " + (
        "\n  ".join(failures[:8])
    )


class TestTheMatrixItself:
    """A generator that silently stops generating turns every test below into a pass."""

    def test_the_acl_space_is_the_size_it_was_built_to_be(self) -> None:
        assert len(ACL_CASES) == ACL_CASE_COUNT
        assert len(ACL_CASES) > 5_000

    def test_the_resolution_space_covers_every_dimension(self) -> None:
        assert len(CASES) == RESOLUTION_CASE_COUNT
        assert len(CASES) > 400

        paths = {case.path for case in CASES}
        assert paths == {AccessPath.REMOTE_SMB, AccessPath.LOCAL}

        provenance = {case.provenance for case in CASES}
        assert provenance == {
            AclProvenance.OBSERVED,
            AclProvenance.DERIVED,
            AclProvenance.UNOBSERVED,
        }

        shares = {case.share for case in CASES}
        assert {"share_read", "share_change", "share_full", "share_none", "unobserved"} <= shares

        assert {case.subject_state for case in CASES} == {
            "user",
            "group",
            "unresolved",
            "undescribed",
        }

    def test_every_case_id_is_unique(self) -> None:
        ids = [case.case_id for case in CASES]
        assert len(set(ids)) == len(ids)

    def test_the_degenerate_dacl_states_reach_the_resolution_matrix(self) -> None:
        """A NULL DACL crossed with a share is the case most worth not losing."""
        null_dacl = [case for case in CASES if not case.acl.dacl_present]
        assert null_dacl
        assert {case.path for case in null_dacl} == {AccessPath.REMOTE_SMB, AccessPath.LOCAL}


class TestSoundness:
    """No answer may contain a right nothing granted."""

    def test_no_case_invents_a_right(self) -> None:
        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            ceiling = _ntfs_ceiling(case)
            if ceiling is None:
                return None
            extra = result.rights.value & ~ceiling
            if extra:
                return (
                    f"rights 0x{result.rights.value:08X} exceed everything the DACL could "
                    f"grant this token (0x{ceiling:08X}) by 0x{extra:08X}"
                )
            return None

        check_every_case(check)

    def test_no_case_reports_access_without_a_reason(self) -> None:
        """Access implies either a matched Allow, owner rights, or a NULL DACL."""

        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            if not result.has_access:
                return None
            explained = (
                bool(result.ntfs.granted_by)
                or result.ntfs.owner_rights is not None
                or not case.acl.dacl_present
                or case.provenance is AclProvenance.UNOBSERVED
            )
            return None if explained else "access is granted with nothing that explains it"

        check_every_case(check)


class TestTheLayersCross:
    """The two-layer model reduces to these."""

    def test_effective_rights_never_exceed_ntfs(self) -> None:
        def check(_: ResolutionCase, result: EffectiveAccess) -> str | None:
            ntfs = result.ntfs.rights.expand_generics().value
            extra = result.rights.value & ~ntfs
            return (
                f"effective 0x{result.rights.value:08X} exceeds NTFS 0x{ntfs:08X}"
                if extra
                else None
            )

        check_every_case(check)

    def test_effective_rights_never_exceed_an_observed_share(self) -> None:
        def check(_: ResolutionCase, result: EffectiveAccess) -> str | None:
            if result.share is None:
                return None
            share = result.share.rights.expand_generics().value
            extra = result.rights.value & ~share
            return (
                f"effective 0x{result.rights.value:08X} exceeds the share 0x{share:08X}"
                if extra
                else None
            )

        check_every_case(check)

    def test_local_access_is_exactly_what_ntfs_grants(self) -> None:
        """Nothing stands between a console logon and the file system DACL."""

        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            if case.path is not AccessPath.LOCAL:
                return None
            expected = result.ntfs.rights.expand_generics().value
            return (
                None
                if result.rights.value == expected
                else f"local 0x{result.rights.value:08X} != NTFS 0x{expected:08X}"
            )

        check_every_case(check)

    def test_remote_access_never_exceeds_local_access(self) -> None:
        """The property the whole share layer amounts to.

        A share ACL can only ever remove rights, so for one DACL the remote answer is a subset
        of the local one. A restrictive share is therefore never a substitute for NTFS
        permissions, which is the finding this comparison exists to make possible.
        """
        failures: list[str] = []
        for case in CASES:
            if case.path is not AccessPath.REMOTE_SMB:
                continue
            remote = resolve(case)
            local = resolve_access(
                replace(case, path=AccessPath.LOCAL).subject_token(), case.resource(), None
            )
            extra = remote.rights.value & ~local.rights.value
            if extra:
                failures.append(
                    f"{case.case_id}: remote 0x{remote.rights.value:08X} exceeds local "
                    f"0x{local.rights.value:08X} by 0x{extra:08X}"
                )
        assert not failures, "\n  ".join(failures[:8])

    def test_an_unread_share_acl_is_an_upper_bound_on_every_real_one(self) -> None:
        """The reason an unobserved share is not treated as an empty one, stated as a test.

        Whatever the share ACL turns out to say, the answer computed without it must be at
        least as wide — otherwise "we could not read the share" would be hiding access rather
        than bounding it.
        """
        failures: list[str] = []
        for case in CASES:
            if case.path is not AccessPath.REMOTE_SMB or case.share == "unobserved":
                continue
            bounded = resolve(case)
            unread = resolve_access(
                case.subject_token(),
                case.resource(),
                ShareDacl(share_key="fs01|finance", observed=False),
            )
            missing = bounded.rights.value & ~unread.rights.value
            if missing:
                failures.append(
                    f"{case.case_id}: the real share grants 0x{bounded.rights.value:08X}, "
                    f"which the unread-share bound 0x{unread.rights.value:08X} does not cover"
                )
        assert not failures, "\n  ".join(failures[:8])

    def test_the_limiting_layer_names_a_layer_that_is_actually_limiting(self) -> None:
        def check(_: ResolutionCase, result: EffectiveAccess) -> str | None:
            if result.share is None or result.crossed is None:
                return None
            ntfs = result.ntfs.rights.expand_generics().value
            share = result.share.rights.expand_generics().value
            if result.limiting_layer is LimitingLayer.NONE and ntfs != share:
                return f"reported as unlimited but NTFS 0x{ntfs:08X} != share 0x{share:08X}"
            if result.limiting_layer is LimitingLayer.SMB_SHARE and not (ntfs & ~share):
                return "blamed the share, which withholds nothing NTFS grants"
            if result.limiting_layer is LimitingLayer.NTFS and not (share & ~ntfs):
                return "blamed NTFS, which withholds nothing the share grants"
            return None

        check_every_case(check)


class TestDirectionalHonesty:
    """What the answer may be wrong about, and which way."""

    def test_an_unread_descriptor_is_never_certain(self) -> None:
        """The invariant this phase found violated.

        A path no run has read granted nothing and said so with `certainty: certain`, which
        told every consumer that nobody could reach a directory nobody had looked at.
        """

        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            if case.provenance is not AclProvenance.UNOBSERVED:
                return None
            if result.certainty is AccessCertainty.CERTAIN:
                return "an unread descriptor produced a certain answer"
            if AccessCondition.NTFS_ACL_NOT_OBSERVED not in result.conditions:
                return "an unread descriptor did not report that it was unread"
            return None

        check_every_case(check)

    def test_an_unread_descriptor_never_claims_the_dacl_was_empty(self) -> None:
        """Two contradictory statements about one object is not a disclosure."""

        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            if case.provenance is not AclProvenance.UNOBSERVED:
                return None
            return (
                "reported EMPTY_DACL for a descriptor nobody read"
                if AccessCondition.EMPTY_DACL in result.conditions
                else None
            )

        check_every_case(check)

    def test_an_unread_share_acl_is_never_certain(self) -> None:
        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            if case.share != "unobserved":
                return None
            if result.certainty is AccessCertainty.CERTAIN:
                return "an unread share ACL produced a certain answer"
            if AccessCondition.SHARE_ACL_NOT_OBSERVED not in result.conditions:
                return "an unread share ACL was not reported as unread"
            return None

        check_every_case(check)

    def test_a_derived_dacl_is_never_certain(self) -> None:
        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            if case.provenance is not AclProvenance.DERIVED:
                return None
            return (
                "a projected DACL produced a certain answer"
                if result.certainty is AccessCertainty.CERTAIN
                else None
            )

        check_every_case(check)

    def test_a_truncated_membership_is_never_certain(self) -> None:
        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            if case.membership_complete:
                return None
            return (
                "a truncated traversal produced a certain answer"
                if result.certainty is AccessCertainty.CERTAIN
                else None
            )

        check_every_case(check)

    def test_certainty_is_the_fold_of_the_findings_it_carries(self) -> None:
        """No result may carry a certainty its own findings do not produce."""

        def check(_: ResolutionCase, result: EffectiveAccess) -> str | None:
            expected = certainty_of(result.findings)
            return (
                None
                if result.certainty is expected
                else f"certainty {result.certainty.value!r} but findings fold to {expected.value!r}"
            )

        check_every_case(check)

    def test_every_condition_reported_has_a_sentence_for_an_operator(self) -> None:
        def check(_: ResolutionCase, result: EffectiveAccess) -> str | None:
            undocumented = [
                condition.value
                for condition in result.conditions
                if condition not in CONDITION_DESCRIPTIONS
            ]
            return f"conditions with no description: {undocumented}" if undocumented else None

        check_every_case(check)


class TestTheShapeOfEveryAnswer:
    """Structural guarantees a consumer is entitled to rely on."""

    def test_rights_always_carry_the_effective_layer(self) -> None:
        def check(_: ResolutionCase, result: EffectiveAccess) -> str | None:
            if result.rights.layer is not RightsLayer.EFFECTIVE:
                return f"rights carry {result.rights.layer.value!r}"
            if result.ntfs.rights.layer is not RightsLayer.NTFS:
                return f"NTFS rights carry {result.ntfs.rights.layer.value!r}"
            if result.share is not None and result.share.rights.layer is not RightsLayer.SMB_SHARE:
                return f"share rights carry {result.share.rights.layer.value!r}"
            return None

        check_every_case(check)

    def test_has_access_means_a_right_survived(self) -> None:
        def check(_: ResolutionCase, result: EffectiveAccess) -> str | None:
            return (
                None
                if result.has_access == bool(result.rights.value)
                else "has_access disagrees with the mask"
            )

        check_every_case(check)

    def test_escalation_is_reported_exactly_when_it_is_held(self) -> None:
        def check(_: ResolutionCase, result: EffectiveAccess) -> str | None:
            held = bool(result.rights.value & int(ESCALATION_RIGHTS))
            reported = AccessCondition.ESCALATION_RIGHTS in result.conditions
            if held and not reported:
                return "holds WRITE_DAC or WRITE_OWNER and does not say so"
            if reported and not held:
                return "reports escalation rights it does not hold"
            return None

        check_every_case(check)

    def test_every_contributing_entry_came_from_the_supplied_acls(self) -> None:
        """A result may not cite an ACE that was not on the ACL it was given."""

        def check(case: ResolutionCase, result: EffectiveAccess) -> str | None:
            supplied = set(case.resource().entries)
            share = case.share_dacl()
            if share is not None:
                supplied |= set(share.entries)
            cited = set(result.grant_entries) | set(result.deny_entries)
            stray = cited - supplied
            return f"{len(stray)} cited entries are not on either ACL" if stray else None

        check_every_case(check)

    def test_resolving_twice_gives_the_same_answer(self) -> None:
        """Set iteration and dictionary ordering are easy ways to make an audit unrepeatable."""
        for case in CASES[::7]:
            first, second = resolve(case), resolve(case)
            assert first.rights == second.rights, case.case_id
            assert first.certainty is second.certainty, case.case_id
            assert first.conditions == second.conditions, case.case_id
            assert first.limiting_layer is second.limiting_layer, case.case_id


class TestMonotonicity:
    """Adding an entry to the end of a DACL may move the answer only one way."""

    @pytest.mark.parametrize("mask_name", ["modify", "full", "write_dac"])
    def test_appending_an_allow_never_removes_a_right(self, mask_name: str) -> None:
        self._compare(AceType.ALLOW, mask_name, widens=True)

    @pytest.mark.parametrize("mask_name", ["modify", "full", "write_dac"])
    def test_appending_a_deny_never_adds_a_right(self, mask_name: str) -> None:
        self._compare(AceType.DENY, mask_name, widens=False)

    def _compare(self, ace_type: AceType, mask_name: str, *, widens: bool) -> None:
        from tests.access_engine.matrix import MASKS

        failures: list[str] = []
        for case in ACL_CASES[::53]:
            if not case.dacl_present:
                continue  # A NULL DACL has no entries to append to.
            before = _acl_rights(case)
            extended = replace(
                case,
                aces=(*case.aces, AceSpec(ace_type, "direct", MASKS[mask_name])),
            )
            after = _acl_rights(extended)
            lost = before & ~after if widens else after & ~before
            if lost:
                direction = "removed" if widens else "added"
                failures.append(
                    f"{case.case_id}: appending {ace_type.value} {mask_name} {direction} "
                    f"0x{lost:08X} (0x{before:08X} -> 0x{after:08X})"
                )
        assert not failures, "\n  ".join(failures[:8])


class TestTheDegenerateDaclStates:
    """A NULL DACL and an empty DACL look identical and mean opposite things."""

    def test_a_null_dacl_grants_everything_to_everybody(self) -> None:
        cases = [case for case in CASES if not case.acl.dacl_present]
        assert cases
        for case in cases:
            result = resolve(case)
            assert AccessCondition.NULL_DACL in result.conditions, case.case_id
            assert result.ntfs.rights.value == FILE_ALL_ACCESS, case.case_id

    def test_an_empty_dacl_grants_nothing_beyond_ownership(self) -> None:
        cases = [
            case
            for case in CASES
            if case.acl.dacl_present
            and not case.acl.aces
            and case.provenance is AclProvenance.OBSERVED
        ]
        assert cases
        for case in cases:
            result = resolve(case)
            assert AccessCondition.EMPTY_DACL in result.conditions, case.case_id
            owns = case.acl.owner == "direct"
            expected = int(OWNER_IMPLICIT_RIGHTS.value) if owns else 0
            assert result.ntfs.rights.value == expected, case.case_id


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _acl_rights(case: AclCase) -> int:
    """The NTFS rights for one ACL case, with generics expanded for comparison."""
    from app.access_engine import evaluate_acl

    resource = case.resource()
    result = evaluate_acl(
        resource.entries,
        case.subject_token(),
        layer=RightsLayer.NTFS,
        facts=resource.facts,
    )
    return result.rights.expand_generics().value


def _ntfs_ceiling(case: ResolutionCase) -> int | None:
    """Everything the DACL could possibly grant this token, computed without the engine.

    Deliberately independent of :func:`evaluate_acl`: it unions the expanded masks of every
    Allow entry the token could match and adds the owner's implicit rights, ignoring Deny
    entries and ordering entirely. That makes it a true ceiling and a weak one, which is
    exactly what a soundness check wants — it cannot fail for a subtle reason, only for the
    one reason that matters.

    ``None`` where no ceiling can be stated: a NULL DACL grants everything by definition, and
    an unread descriptor is bounded by nothing ADG holds.
    """
    acl = case.acl
    if not acl.dacl_present or case.provenance is AclProvenance.UNOBSERVED:
        return None

    from app.access_engine import RightsMask

    token_keys = set(case.subject_token().keys)
    ceiling = 0
    for ace in acl.aces:
        if ace.ace_type is not AceType.ALLOW:
            continue
        if ace.flags & 0x08:  # INHERIT_ONLY: describes children, grants nothing here.
            continue
        reachable = ace.sid in token_keys or ace.trustee == "owner_rights"
        if not reachable:
            continue
        ceiling |= RightsMask.ntfs(ace.mask).expand_generics().value
    if acl.owner == "direct":
        ceiling |= int(OWNER_IMPLICIT_RIGHTS.value)
    return ceiling
