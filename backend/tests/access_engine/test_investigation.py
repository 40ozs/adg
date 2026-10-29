r"""ADG-DEMO-02: the investigation summary, and that it never says more than the explanation.

:func:`app.access_engine.investigate` answers an investigator's seven questions from an
explanation the engine already computed. These tests are organized by the claim each answer
makes, and every fixture is built through the engine's own constructors, so a summary that
drifted from the explanation it summarizes would fail here rather than on screen:

* the primary route is a *contributing* path, one per layer, ranked by how much of the
  answer its entry names, with DACL order breaking ties;
* direct, group, assumed and ownership routes are told apart, and so are explicit and
  inherited entries;
* removal effects are the engine's measured ones;
* whether an open question reaches the conclusion follows from its direction alone;
* and, carried over from ADG-DEMO-01, no SYSTEM answer is ever summarized as "absent".

The shape of the canonical demo (``\\GP-ENG-FS01\Engineering\Projects\Apollo``) is
reproduced in miniature by :func:`apollo` with the Phase 0B synthetic SIDs.
"""

from __future__ import annotations

import pytest

from app.access_engine import (
    AccessCondition,
    AccessExplanation,
    AccessFinding,
    AccessOutcome,
    AccessPath,
    AccessPresence,
    ConclusionImpact,
    EntryPlacement,
    EvidenceState,
    ExplanationLimits,
    NextStepCode,
    RemovalEffect,
    RightsLayer,
    RouteKind,
    ShareDacl,
    SubjectToken,
    explain_access,
    investigate,
    resolve_access,
)
from app.domain import GraphEdge, PrincipalKind, SharePermission
from tests.access_engine.support import (
    ALICE,
    DOMAIN,
    EVERYONE,
    FINANCE_RW,
    FINANCE_TEAM,
    MODIFY,
    ORPHAN,
    READ_EXECUTE,
    REPORTS,
    allow,
    dacl,
    group_sid,
    share_acl,
    share_allow,
    token,
    unread_share,
)
from tests.access_engine.test_system_explanation_regression import (
    ASSUMPTIONS,
    DESCRIPTIONS,
    SHARES,
    answer,
    system,
)

#: A group that is on the share ACL and whose members no run has collected.
NEVER_COLLECTED = f"{DOMAIN}-1290"

NESTED = (
    GraphEdge(edge_key="e-alice-team", group_key=FINANCE_TEAM, member_key=ALICE),
    GraphEdge(edge_key="e-team-rw", group_key=FINANCE_RW, member_key=FINANCE_TEAM),
)


def nested_token(*, path: AccessPath = AccessPath.REMOTE_SMB) -> SubjectToken:
    return token(
        ALICE,
        [
            group_sid(FINANCE_TEAM, depth=1, path=(ALICE, FINANCE_TEAM)),
            group_sid(FINANCE_RW, depth=2, path=(ALICE, FINANCE_TEAM, FINANCE_RW)),
        ],
        path=path,
    )


def unobserved(*trustees: str) -> tuple[AccessFinding, ...]:
    return tuple(
        AccessFinding(AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED, {"trustee_key": key})
        for key in trustees
    )


def explain(
    subject: SubjectToken,
    *entries: object,
    share: ShareDacl | None = None,
    findings: tuple[AccessFinding, ...] = (),
    limits: ExplanationLimits | None = None,
    **facts: object,
) -> AccessExplanation:
    resource = dacl(*entries, key=REPORTS, **facts)  # type: ignore[arg-type]
    access = resolve_access(subject, resource, share, findings=findings)
    if limits is None:
        return explain_access(access, resource, share, edges=NESTED)
    return explain_access(access, resource, share, edges=NESTED, limits=limits)


def apollo() -> AccessExplanation:
    """The canonical demo, in miniature.

    A direct explicit Modify entry for the subject comes first, then an inherited Modify
    entry reached through two nested groups, which is therefore redundant. The share grants
    Change to the subject's first group, then to the subject directly (redundant, as
    Fatima's own share entry is), and names a group nobody has collected members for.
    """
    return explain(
        nested_token(),
        allow(ALICE, MODIFY, key="ace-direct"),
        allow(FINANCE_RW, MODIFY, inherited=True, key="ace-inherited"),
        share=share_acl(
            share_allow(FINANCE_TEAM, SharePermission.CHANGE),
            share_allow(ALICE, SharePermission.CHANGE),
            share_allow(NEVER_COLLECTED, SharePermission.CHANGE),
        ),
        findings=unobserved(NEVER_COLLECTED),
    )


# --------------------------------------------------------------------------------------


class TestTheCanonicalShape:
    def test_access_is_present_and_carried_by_one_route_per_layer(self) -> None:
        summary = investigate(apollo())

        assert summary.presence is AccessPresence.PRESENT
        assert summary.outcome is AccessOutcome.GRANTED
        assert [route.layer for route in summary.primary_routes] == [
            RightsLayer.NTFS,
            RightsLayer.SMB_SHARE,
        ]

    def test_the_ntfs_route_is_the_direct_explicit_entry(self) -> None:
        explanation = apollo()
        ntfs = investigate(explanation).primary_routes[0]
        path = next(p for p in explanation.paths if p.path_id == ntfs.path_id)

        assert path.chain == (ALICE,)
        assert ntfs.route is RouteKind.DIRECT
        assert ntfs.placement is EntryPlacement.EXPLICIT
        assert ntfs.membership_hops == 0
        assert ntfs.coverage.value == explanation.access.rights.expand_generics().value

    def test_the_share_route_runs_through_the_group(self) -> None:
        explanation = apollo()
        share = investigate(explanation).primary_routes[1]
        path = next(p for p in explanation.paths if p.path_id == share.path_id)

        assert path.chain == (ALICE, FINANCE_TEAM)
        assert share.route is RouteKind.GROUP
        assert share.membership_hops == 1
        # The subject's own share entry comes later and settles nothing, so taking the
        # subject out of the group leaves the share's answer where it was.
        assert share.removal_effect is RemovalEffect.CHANGES_NOTHING

    def test_the_redundant_inherited_route_is_not_the_primary_one(self) -> None:
        """It names the same Modify, and settles nothing: the direct entry got there first."""
        explanation = apollo()
        inherited = [p for p in explanation.paths if p.inherited]
        assert inherited and all(not p.matters for p in inherited)

        primary = {route.path_id for route in investigate(explanation).primary_routes}
        assert primary.isdisjoint(p.path_id for p in inherited)

    def test_removing_the_direct_entry_is_measured_to_change_nothing(self) -> None:
        ntfs = investigate(apollo()).primary_routes[0]

        assert ntfs.removal_edge_id is not None
        assert ntfs.removal_edge_id.startswith("trustee:")
        assert ntfs.removal_effect is RemovalEffect.CHANGES_NOTHING
        assert ntfs.removal is not None and ntfs.removal.rights_removed.is_empty

    def test_the_never_collected_group_is_open_and_only_bounds_the_extent(self) -> None:
        summary = investigate(apollo())
        [question] = [
            q
            for q in summary.open_questions
            if q.finding.condition is AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED
        ]

        assert question.state is EvidenceState.MISSING
        assert question.about == NEVER_COLLECTED
        assert question.impact is ConclusionImpact.EXTENT
        assert summary.impact is ConclusionImpact.EXTENT
        assert summary.affects_conclusion is False

    def test_next_steps_settle_the_evidence_before_the_clean_up(self) -> None:
        summary = investigate(apollo())

        assert [(step.code, step.about) for step in summary.next_steps] == [
            (NextStepCode.COLLECT_GROUP_MEMBERSHIP, NEVER_COLLECTED),
            (NextStepCode.REVIEW_REDUNDANT_ENTRY, None),
        ]
        assert summary.next_steps[1].path_id == summary.primary_routes[0].path_id

    def test_an_unidentified_trustee_is_resolved_rather_than_enumerated(self) -> None:
        summary = investigate(apollo(), unidentified={NEVER_COLLECTED})

        assert summary.next_steps[0].code is NextStepCode.RESOLVE_TRUSTEE_IDENTITY
        assert summary.next_steps[0].about == NEVER_COLLECTED
        # Which step applies changes; whether the conclusion is reached does not.
        assert summary.impact is ConclusionImpact.EXTENT

    def test_it_is_deterministic(self) -> None:
        assert investigate(apollo()) == investigate(apollo())


class TestRanking:
    def test_the_route_naming_more_of_the_answer_wins(self) -> None:
        """Scenario F's shape: a direct Modify entry and a group route to Read & execute."""
        explanation = explain(
            nested_token(path=AccessPath.LOCAL),
            allow(FINANCE_RW, READ_EXECUTE, key="ace-group"),
            allow(ALICE, MODIFY, key="ace-direct"),
        )
        [route] = investigate(explanation).primary_routes

        assert route.route is RouteKind.DIRECT
        assert route.removal_effect is RemovalEffect.NARROWS_ACCESS
        assert len(route.alternates) == 1

    def test_an_earlier_narrower_direct_entry_does_not_outrank_a_wider_group_route(
        self,
    ) -> None:
        """A direct Read & execute ahead of an inherited group Modify: both contribute, and
        the group route names all of the answer."""
        explanation = explain(
            nested_token(path=AccessPath.LOCAL),
            allow(ALICE, READ_EXECUTE, key="ace-direct"),
            allow(FINANCE_RW, MODIFY, inherited=True, key="ace-group"),
        )
        [route] = investigate(explanation).primary_routes

        assert route.route is RouteKind.GROUP
        assert route.placement is EntryPlacement.INHERITED
        assert route.membership_hops == 2
        assert len(route.alternates) == 1

    def test_dacl_order_breaks_a_tie(self) -> None:
        explanation = explain(
            nested_token(path=AccessPath.LOCAL),
            allow(FINANCE_TEAM, MODIFY, key="ace-team"),
            allow(FINANCE_RW, MODIFY, key="ace-rw"),
        )
        [route] = investigate(explanation).primary_routes
        path = next(p for p in explanation.paths if p.path_id == route.path_id)

        assert path.trustee_key == FINANCE_TEAM


class TestRouteKinds:
    def test_the_only_route_is_reported_as_revoking(self) -> None:
        explanation = explain(
            nested_token(path=AccessPath.LOCAL), allow(FINANCE_RW, MODIFY, key="ace-rw")
        )
        summary = investigate(explanation)
        [route] = summary.primary_routes

        assert route.route is RouteKind.GROUP
        assert route.removal_edge_id is not None
        assert route.removal_edge_id.startswith("membership:")
        assert route.removal_effect is RemovalEffect.REVOKES_ACCESS
        assert [step.code for step in summary.next_steps] == [NextStepCode.REVIEW_SOLE_ROUTE]

    def test_an_assumed_sid_is_its_own_kind_and_its_only_removable_link_is_the_entry(
        self,
    ) -> None:
        """Nobody can be taken out of Everyone. What can be removed is the entry naming it,
        which the route kind says changes everybody's access, not just this subject's."""
        subject = token(ALICE, path=AccessPath.LOCAL, assumption=None)
        explanation = explain(subject, allow(EVERYONE, READ_EXECUTE, key="ace-everyone"))
        [route] = investigate(explanation).primary_routes

        assert route.route is RouteKind.ASSUMED
        assert route.removal_edge_id is not None
        assert route.removal_edge_id.startswith(f"trustee:assumed:{EVERYONE}->")
        assert route.removal_effect is RemovalEffect.REVOKES_ACCESS

    def test_ownership_is_neither_an_entry_nor_removable(self) -> None:
        subject = token(ALICE, path=AccessPath.LOCAL)
        explanation = explain(subject, owner_sid=ALICE, owner_key=ALICE)
        routes = investigate(explanation).primary_routes

        assert [(r.route, r.placement) for r in routes] == [
            (RouteKind.OWNERSHIP, EntryPlacement.OWNER)
        ]
        assert routes[0].removal_edge_id is None


class TestWhetherAnOpenQuestionReachesTheConclusion:
    def test_an_unread_share_acl_could_take_a_grant_away(self) -> None:
        explanation = explain(
            nested_token(), allow(ALICE, MODIFY, key="ace-direct"), share=unread_share()
        )
        summary = investigate(explanation)

        assert summary.presence is AccessPresence.PRESENT
        assert summary.impact is ConclusionImpact.PRESENCE
        assert summary.affects_conclusion is True
        assert NextStepCode.RESCAN_RESOURCE in [step.code for step in summary.next_steps]

    def test_without_access_only_a_hidden_grant_matters(self) -> None:
        explanation = explain(
            token(ALICE, path=AccessPath.LOCAL),
            allow(NEVER_COLLECTED, MODIFY, key="ace-other"),
            findings=unobserved(NEVER_COLLECTED),
        )
        summary = investigate(explanation)

        assert summary.outcome is AccessOutcome.INDETERMINATE
        assert summary.presence is AccessPresence.UNDETERMINED
        assert summary.primary_routes == ()
        assert summary.impact is ConclusionImpact.PRESENCE

    def test_a_conclusive_negative_has_nothing_open(self) -> None:
        explanation = explain(
            token(ALICE, path=AccessPath.LOCAL), allow(ORPHAN, MODIFY, key="ace-other")
        )
        summary = investigate(explanation)

        assert summary.outcome is AccessOutcome.NO_GRANT
        assert summary.presence is AccessPresence.ABSENT
        assert summary.impact is ConclusionImpact.NONE
        assert [step.code for step in summary.next_steps] == [NextStepCode.INSPECT_EXPLANATION]

    def test_an_assumption_is_recorded_and_reaches_nothing(self) -> None:
        subject = token(ALICE, path=AccessPath.LOCAL, assumption=None)
        explanation = explain(subject, allow(ALICE, MODIFY, key="ace-direct"))
        summary = investigate(explanation)
        assumed = [
            q
            for q in summary.open_questions
            if q.finding.condition is AccessCondition.ASSUMED_TOKEN_SIDS
        ]

        assert assumed and assumed[0].state is EvidenceState.ASSUMED
        assert assumed[0].impact is ConclusionImpact.NONE

    def test_a_truncated_explanation_says_its_routes_are_a_subset(self) -> None:
        explanation = explain(
            nested_token(path=AccessPath.LOCAL),
            allow(FINANCE_TEAM, MODIFY, key="ace-team"),
            allow(FINANCE_RW, MODIFY, key="ace-rw"),
            limits=ExplanationLimits(max_paths=1),
        )

        assert investigate(explanation).routes_complete is False


class TestTheSystemInvariantsCarryOver:
    """ADG-DEMO-01, restated for the summary: it has no opinion of its own about SYSTEM."""

    @pytest.mark.parametrize("described", sorted(DESCRIPTIONS))
    @pytest.mark.parametrize("shape", sorted(SHARES))
    @pytest.mark.parametrize("assumption", ASSUMPTIONS, ids=lambda a: str(a))
    @pytest.mark.parametrize("path", list(AccessPath))
    def test_no_system_answer_is_summarized_as_absent(
        self, described: str, shape: str, assumption: object, path: AccessPath
    ) -> None:
        _, verdict, explanation = answer(
            system(DESCRIPTIONS[described]),
            path,
            assumption,  # type: ignore[arg-type]
            SHARES[shape],
        )
        summary = investigate(explanation)
        [mapping] = [
            q
            for q in summary.open_questions
            if q.finding.condition.value.startswith("system_mapping_")
        ]

        assert summary.presence is not AccessPresence.ABSENT
        assert summary.outcome is verdict.outcome
        assert mapping.state.value == mapping.finding.detail["state"]
        assert mapping.impact is not ConclusionImpact.NONE
        if mapping.state is not EvidenceState.CONFIRMED:
            assert NextStepCode.SETTLE_SYSTEM_MAPPING in [s.code for s in summary.next_steps]
        else:
            assert NextStepCode.SETTLE_SYSTEM_MAPPING not in [s.code for s in summary.next_steps]

    def test_over_a_share_system_is_never_given_a_route(self) -> None:
        _, _, explanation = answer(
            system(PrincipalKind.WELL_KNOWN), AccessPath.REMOTE_SMB, None, SHARES["mirror"]
        )
        summary = investigate(explanation)

        assert summary.presence is AccessPresence.UNDETERMINED
        assert summary.primary_routes == ()
        assert summary.impact is ConclusionImpact.PRESENCE
