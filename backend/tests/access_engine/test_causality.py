r"""The causality engine: which paths caused an answer, and what each one is worth.

These tests are organized by the claim they defend rather than by the function they call,
because the claims are what the phase promised and the functions are how it happened to
deliver them:

* a chain like *Alice → Group A → Group B → ACE → Resource* comes back, deterministically;
* two routes to the same rights stay two routes;
* an ACE that matched is not automatically a cause — it can be redundant, or capped by the
  other layer;
* and no removal is ever reported as sufficient while an alternate path survives.

The last one is the one with teeth. A tool that says "remove Alice from Finance-Team and
she loses access" when she is also in Finance-RW directly has told somebody to do work that
accomplishes nothing and then to sign off a finding as fixed.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.access_engine import (
    DEFAULT_EXPLANATION_LIMITS,
    OWNERSHIP_POSITION,
    AccessPath,
    EdgeKind,
    EffectiveAccess,
    ExplanationLimits,
    NodeKind,
    PathEffect,
    PathRelation,
    PathTruncation,
    RightsLayer,
    SubjectToken,
    TokenAssumption,
    explain_access,
    resolve_access,
)
from app.domain import DomainValidationError, GraphEdge, SharePermission
from tests.access_engine.support import (
    ALICE,
    AUTHENTICATED_USERS,
    BOB,
    DOMAIN_USERS,
    EVERYONE,
    FINANCE_RW,
    FINANCE_TEAM,
    FULL_CONTROL,
    MODIFY,
    READ_CONTROL,
    READ_EXECUTE,
    REPORTS,
    WRITE_DAC,
    allow,
    dacl,
    deny,
    group_sid,
    share_acl,
    share_allow,
    token,
    unread_share,
)

# One nested chain the whole file shares: Alice is in Finance-Team, which is in Finance-RW.
NESTED = (
    GraphEdge(edge_key="e-alice-team", group_key=FINANCE_TEAM, member_key=ALICE),
    GraphEdge(edge_key="e-team-rw", group_key=FINANCE_RW, member_key=FINANCE_TEAM),
)


def nested_token(*, path: AccessPath = AccessPath.REMOTE_SMB, **kwargs: Any) -> SubjectToken:
    """Alice's token for the shared nesting: both groups, with the chain that reached them.

    Typed as the real token rather than as `object`. An opaque return type makes every call
    site that passes it to `resolve_access` a type error, which is what the forty
    `# type: ignore` comments this file used to carry were suppressing.
    """
    return token(
        ALICE,
        [
            group_sid(FINANCE_TEAM, depth=1, path=(ALICE, FINANCE_TEAM)),
            group_sid(FINANCE_RW, depth=2, path=(ALICE, FINANCE_TEAM, FINANCE_RW)),
        ],
        path=path,
        **kwargs,
    )


def local(subject_token: Any, resource: Any) -> EffectiveAccess:
    """Resolve for local access, where no share ACL applies.

    Typed as returning the real answer rather than `object`: every caller reads
    `has_access` and `rights` off it, and an opaque return type turns each of those into a
    type error at the call site instead of here.
    """
    return resolve_access(subject_token, resource)


# --------------------------------------------------------------------------------------


class TestTheChainIsReturned:
    """`Alice → Group A → Group B → ACE → Resource`, which is the acceptance criterion."""

    def test_a_nested_grant_reports_the_whole_chain(self) -> None:
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY, key="ace-rw"))
        access = local(subject, resource)

        explanation = explain_access(access, resource, edges=NESTED)

        assert len(explanation.paths) == 1
        path = explanation.paths[0]
        assert path.chain == (ALICE, FINANCE_TEAM, FINANCE_RW)
        assert path.length == 2
        assert path.trustee_key == FINANCE_RW
        assert path.effect is PathEffect.CONTRIBUTES

    def test_the_path_walks_subject_through_groups_to_the_ace_and_the_resource(self) -> None:
        """The node sequence is the explanation; a chain that stops at the trustee is half."""
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY, key="ace-rw"))
        explanation = explain_access(
            local(subject, resource),
            resource,
            edges=NESTED,
        )

        graph = explanation.graph
        kinds = [graph.node(node_id).kind for node_id in explanation.paths[0].node_ids]  # type: ignore[union-attr]
        assert kinds == [
            NodeKind.PRINCIPAL,
            NodeKind.GROUP,
            NodeKind.GROUP,
            NodeKind.NTFS_ACE,
            NodeKind.RESOURCE,
        ]

    def test_an_ace_naming_the_subject_directly_has_a_one_element_chain(self) -> None:
        resource = dacl(allow(ALICE, MODIFY, key="ace-alice"))
        subject = token(ALICE, path=AccessPath.LOCAL)

        explanation = explain_access(local(subject, resource), resource)

        assert explanation.paths[0].chain == (ALICE,)
        assert explanation.paths[0].length == 0
        assert explanation.paths[0].via_group is False

    def test_an_ace_naming_nobody_in_the_token_produces_no_path(self) -> None:
        """Only matched entries are causes. An ACE for Bob explains nothing about Alice."""
        resource = dacl(allow(BOB, MODIFY, key="ace-bob"), allow(ALICE, READ_EXECUTE, key="a"))
        subject = token(ALICE, path=AccessPath.LOCAL)

        explanation = explain_access(local(subject, resource), resource)

        assert [path.trustee_key for path in explanation.paths] == [ALICE]


class TestMultiplePathsArePreserved:
    """Collapsing routes is how a remediation gets signed off having changed nothing."""

    def test_two_chains_to_one_ace_are_two_paths(self) -> None:
        """Alice reaches Finance-RW directly and through Finance-Team. Both are real."""
        edges = (
            *NESTED,
            GraphEdge(edge_key="e-alice-rw", group_key=FINANCE_RW, member_key=ALICE),
        )
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY, key="ace-rw"))

        explanation = explain_access(local(subject, resource), resource, edges=edges)

        assert [path.chain for path in explanation.paths] == [
            (ALICE, FINANCE_RW),
            (ALICE, FINANCE_TEAM, FINANCE_RW),
        ]

    def test_two_aces_naming_two_groups_are_two_paths(self) -> None:
        subject = token(
            ALICE,
            [group_sid(FINANCE_RW, path=(ALICE, FINANCE_RW)), group_sid(DOMAIN_USERS)],
            path=AccessPath.LOCAL,
        )
        resource = dacl(
            allow(FINANCE_RW, READ_EXECUTE, key="ace-rw"),
            allow(DOMAIN_USERS, MODIFY, key="ace-du"),
        )
        edges = (
            GraphEdge(edge_key="e1", group_key=FINANCE_RW, member_key=ALICE),
            GraphEdge(edge_key="e2", group_key=DOMAIN_USERS, member_key=ALICE),
        )

        explanation = explain_access(local(subject, resource), resource, edges=edges)

        assert [path.trustee_key for path in explanation.paths] == [FINANCE_RW, DOMAIN_USERS]
        assert all(path.effect is PathEffect.CONTRIBUTES for path in explanation.paths), (
            "Modify adds rights Read & Execute does not, so the second entry is a cause too"
        )

    def test_paths_are_ordered_deterministically(self) -> None:
        """Two runs over one estate must be byte-identical, or a diff means nothing."""
        edges = (
            *NESTED,
            GraphEdge(edge_key="e-alice-rw", group_key=FINANCE_RW, member_key=ALICE),
        )
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY), allow(FINANCE_TEAM, READ_EXECUTE))

        first = explain_access(local(subject, resource), resource, edges=edges)
        second = explain_access(
            local(subject, resource),
            resource,
            edges=tuple(reversed(edges)),
        )

        assert [p.chain for p in first.paths] == [p.chain for p in second.paths]
        assert [p.ace_position for p in first.paths] == [p.ace_position for p in second.paths]

    def test_an_assumed_sid_is_one_path_and_is_marked_assumed(self) -> None:
        """`Everyone` is real access through an edge that exists in no database."""
        subject = token(ALICE, path=AccessPath.LOCAL, assumption=TokenAssumption.AUTHENTICATED_USER)
        resource = dacl(allow(EVERYONE, READ_EXECUTE, key="ace-everyone"))

        explanation = explain_access(local(subject, resource), resource)

        path = explanation.paths[0]
        assert path.assumed is True
        assert path.chain == (ALICE, EVERYONE)
        edge = explanation.graph.edge(path.edge_ids[0])
        assert edge is not None and edge.kind is EdgeKind.ASSUMED_MEMBERSHIP
        assert edge.is_removable is False


class TestRedundantGrants:
    """An ACE on the ACL that settles nothing is a finding, not a cause."""

    def test_a_second_allow_for_the_same_rights_is_redundant(self) -> None:
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(
            allow(ALICE, MODIFY, key="ace-first"),
            allow(FINANCE_RW, MODIFY, key="ace-second"),
        )

        explanation = explain_access(local(subject, resource), resource)

        effects = {path.ace_key: path.effect for path in explanation.paths}
        assert effects == {
            "ace-first": PathEffect.CONTRIBUTES,
            "ace-second": PathEffect.REDUNDANT,
        }

    def test_a_redundant_path_delivers_nothing_but_is_still_reported(self) -> None:
        """Dropping it would leave a trustee list that does not match the paths shown."""
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(allow(ALICE, MODIFY), allow(FINANCE_RW, MODIFY))

        explanation = explain_access(local(subject, resource), resource)

        redundant = explanation.redundant
        assert len(redundant) == 1
        assert redundant[0].layer_rights.is_empty
        assert redundant[0].effective_rights.is_empty
        assert redundant[0].matters is False

    def test_an_ace_granting_a_subset_of_an_earlier_one_is_redundant(self) -> None:
        """Read & Execute is inside Modify, so the second entry adds no right at all."""
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(
            allow(ALICE, MODIFY, key="ace-modify"),
            allow(FINANCE_RW, READ_EXECUTE, key="ace-read"),
        )

        explanation = explain_access(local(subject, resource), resource)

        effects = {path.ace_key: path.effect for path in explanation.paths}
        assert effects["ace-read"] is PathEffect.REDUNDANT

    def test_order_decides_which_of_two_grants_is_redundant(self) -> None:
        """Reverse them and the redundancy moves. Causality follows the stored order."""
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(
            allow(FINANCE_RW, READ_EXECUTE, key="ace-read"),
            allow(ALICE, MODIFY, key="ace-modify"),
        )

        explanation = explain_access(local(subject, resource), resource)

        effects = {path.ace_key: path.effect for path in explanation.paths}
        assert effects == {
            "ace-read": PathEffect.CONTRIBUTES,
            "ace-modify": PathEffect.CONTRIBUTES,
        }, "Modify still adds the write bits Read & Execute did not"

    def test_a_deny_that_denies_nothing_is_redundant_too(self) -> None:
        """An Allow ahead of a Deny wins, and the Deny then settles nothing."""
        subject = token(ALICE, path=AccessPath.LOCAL)
        resource = dacl(allow(ALICE, MODIFY, key="ace-allow"), deny(ALICE, MODIFY, key="ace-deny"))

        explanation = explain_access(local(subject, resource), resource)

        denies = explanation.denies
        assert len(denies) == 1
        assert denies[0].effect is PathEffect.REDUNDANT


class TestDenyPaths:
    def test_a_deny_is_reported_as_a_deny_with_what_it_removes(self) -> None:
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(
            deny(ALICE, MODIFY, key="ace-deny"),
            allow(FINANCE_RW, FULL_CONTROL, key="ace-allow"),
        )

        explanation = explain_access(local(subject, resource), resource)

        denied = explanation.denies[0]
        assert denied.relation is PathRelation.DENY
        assert denied.effect is PathEffect.CONTRIBUTES
        assert denied.effective_rights.value & MODIFY == MODIFY

    def test_a_deny_the_other_layer_already_withholds_is_constrained(self) -> None:
        """The share never granted it, so the NTFS Deny changes no outcome."""
        subject = nested_token()
        resource = dacl(
            deny(FINANCE_RW, FULL_CONTROL & ~READ_EXECUTE, key="ace-deny"),
            allow(FINANCE_TEAM, READ_EXECUTE, key="ace-allow"),
        )
        share = share_acl(share_allow(FINANCE_TEAM, SharePermission.READ))
        access = resolve_access(subject, resource, share)

        explanation = explain_access(access, resource, share, edges=NESTED)

        denied = explanation.denies[0]
        assert denied.effect is PathEffect.CONSTRAINED
        assert denied.effective_rights.is_empty


class TestTheOtherLayerConstrains:
    """The acceptance criterion about a path constrained by the SMB/NTFS intersection."""

    def test_an_ntfs_grant_capped_by_the_share_is_partly_constrained(self) -> None:
        subject = nested_token()
        resource = dacl(allow(FINANCE_RW, FULL_CONTROL, key="ace-rw"))
        share = share_acl(share_allow(FINANCE_RW, SharePermission.READ))
        access = resolve_access(subject, resource, share)

        explanation = explain_access(access, resource, share, edges=NESTED)

        ntfs_path = next(p for p in explanation.paths if p.layer is RightsLayer.NTFS)
        assert ntfs_path.effect is PathEffect.CONTRIBUTES
        assert not ntfs_path.effective_rights.is_empty
        assert not ntfs_path.constrained_rights.is_empty, (
            "NTFS grants Full Control and the share grants Read; the difference is the cap"
        )

    def test_an_ntfs_grant_the_share_withholds_entirely_is_constrained(self) -> None:
        """Alarming ACL, no access: the case the engine must not report as a cause."""
        subject = token(
            ALICE,
            [group_sid(FINANCE_RW, path=(ALICE, FINANCE_RW)), group_sid(DOMAIN_USERS)],
        )
        resource = dacl(
            allow(DOMAIN_USERS, READ_EXECUTE, key="ace-read"),
            allow(FINANCE_RW, FULL_CONTROL & ~READ_EXECUTE, key="ace-write"),
        )
        share = share_acl(share_allow(DOMAIN_USERS, SharePermission.READ))
        access = resolve_access(subject, resource, share)

        explanation = explain_access(access, resource, share)

        write_path = next(p for p in explanation.paths if p.ace_key == "ace-write")
        assert write_path.effect is PathEffect.CONSTRAINED
        assert write_path.effective_rights.is_empty
        assert not write_path.layer_rights.is_empty, "it does contribute at the NTFS layer"

    def test_local_access_has_no_other_layer_to_constrain_it(self) -> None:
        """A restrictive share is not a control against anyone who can log on to the server."""
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, FULL_CONTROL, key="ace-rw"))

        explanation = explain_access(local(subject, resource), resource, edges=NESTED)

        assert explanation.paths[0].constrained_rights.is_empty
        assert explanation.paths[0].effect is PathEffect.CONTRIBUTES

    def test_an_unread_share_constrains_nothing_rather_than_inventing_a_cap(self) -> None:
        subject = nested_token()
        resource = dacl(allow(FINANCE_RW, MODIFY, key="ace-rw"))
        share = unread_share()
        access = resolve_access(subject, resource, share)

        explanation = explain_access(access, resource, share, edges=NESTED)

        assert explanation.paths[0].constrained_rights.is_empty
        assert explanation.paths[0].effect is PathEffect.CONTRIBUTES


class TestRemovalNeverOverstates:
    """The criterion: never promise that removing one edge removes all access."""

    def test_one_membership_of_two_removes_nothing(self) -> None:
        """Alice is in Finance-RW directly *and* through Finance-Team."""
        edges = (
            *NESTED,
            GraphEdge(edge_key="e-alice-rw", group_key=FINANCE_RW, member_key=ALICE),
        )
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY, key="ace-rw"))
        explanation = explain_access(local(subject, resource), resource, edges=edges)

        via_team = next(
            t for t in explanation.removal_targets if t.edge_id.endswith(f"group:{FINANCE_TEAM}")
        )
        assert via_team.rights_removed.is_empty
        assert via_team.revokes_all_access is False
        assert via_team.alternate_paths, "the direct membership is the alternate path"

    def test_the_only_membership_does_revoke(self) -> None:
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY, key="ace-rw"))
        explanation = explain_access(local(subject, resource), resource, edges=NESTED)

        first_hop = next(
            t for t in explanation.removal_targets if t.edge_id.endswith(f"group:{FINANCE_TEAM}")
        )
        assert first_hop.rights_removed.value & MODIFY == MODIFY
        assert first_hop.revokes_all_access is True
        assert first_hop.alternate_paths == ()

    def test_removing_an_allow_that_hides_a_redundant_allow_removes_nothing(self) -> None:
        """The failure mode inference gets wrong: the entry behind it takes over."""
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(
            allow(ALICE, MODIFY, key="ace-first"),
            allow(FINANCE_RW, MODIFY, key="ace-second"),
        )
        explanation = explain_access(local(subject, resource), resource)

        first = next(
            t
            for t in explanation.removal_targets
            if t.kind is EdgeKind.TRUSTEE and t.target.endswith(":0000")
        )
        assert first.rights_removed.is_empty, (
            "the second Allow grants the same rights once the first is gone"
        )
        assert first.revokes_all_access is False

    def test_removing_a_membership_that_carries_a_deny_widens_access(self) -> None:
        """Removal can *add* rights, and a model that assumed otherwise would hide it."""
        subject = token(
            ALICE,
            [group_sid(FINANCE_RW, path=(ALICE, FINANCE_RW)), group_sid(DOMAIN_USERS)],
            path=AccessPath.LOCAL,
        )
        resource = dacl(
            deny(FINANCE_RW, MODIFY, key="ace-deny"),
            allow(DOMAIN_USERS, MODIFY, key="ace-allow"),
        )
        edges = (
            GraphEdge(edge_key="e-rw", group_key=FINANCE_RW, member_key=ALICE),
            GraphEdge(edge_key="e-du", group_key=DOMAIN_USERS, member_key=ALICE),
        )
        explanation = explain_access(local(subject, resource), resource, edges=edges)

        deny_edge = next(
            t
            for t in explanation.removal_targets
            if t.kind is EdgeKind.MEMBERSHIP and t.target.endswith(f"group:{FINANCE_RW}")
        )
        assert not deny_edge.rights_added.is_empty
        assert deny_edge.rights_removed.is_empty
        assert deny_edge.revokes_all_access is False

    def test_an_assumed_membership_is_never_offered_as_a_removal(self) -> None:
        """There is no `Everyone` group to edit, and proposing one is a fake remediation."""
        subject = token(ALICE, path=AccessPath.LOCAL, assumption=TokenAssumption.AUTHENTICATED_USER)
        resource = dacl(allow(AUTHENTICATED_USERS, READ_EXECUTE, key="ace-au"))

        explanation = explain_access(local(subject, resource), resource)

        kinds = {target.kind for target in explanation.removal_targets}
        assert EdgeKind.ASSUMED_MEMBERSHIP not in kinds
        assert kinds == {EdgeKind.TRUSTEE}, "the ACE itself is the only real remediation"

    def test_removing_the_ace_when_it_is_the_only_grant_revokes(self) -> None:
        subject = token(ALICE, path=AccessPath.LOCAL, assumption=TokenAssumption.AUTHENTICATED_USER)
        resource = dacl(allow(AUTHENTICATED_USERS, READ_EXECUTE, key="ace-au"))

        explanation = explain_access(local(subject, resource), resource)

        assert [t.revokes_all_access for t in explanation.removal_targets] == [True]
        assert explanation.sufficient_removals

    def test_nothing_is_sufficient_when_two_independent_aces_grant(self) -> None:
        subject = token(
            ALICE,
            [group_sid(FINANCE_RW, path=(ALICE, FINANCE_RW)), group_sid(DOMAIN_USERS)],
            path=AccessPath.LOCAL,
        )
        resource = dacl(
            allow(FINANCE_RW, READ_EXECUTE, key="ace-rw"),
            allow(DOMAIN_USERS, READ_EXECUTE, key="ace-du"),
        )
        edges = (
            GraphEdge(edge_key="e-rw", group_key=FINANCE_RW, member_key=ALICE),
            GraphEdge(edge_key="e-du", group_key=DOMAIN_USERS, member_key=ALICE),
        )
        explanation = explain_access(local(subject, resource), resource, edges=edges)

        assert explanation.sufficient_removals == ()
        assert all(t.rights_removed.is_empty for t in explanation.removal_targets)

    def test_only_edges_on_a_path_are_offered(self) -> None:
        """An ACE nobody's chain reaches is not a remediation for this subject."""
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(allow(BOB, FULL_CONTROL, key="ace-bob"), allow(FINANCE_RW, MODIFY))

        explanation = explain_access(local(subject, resource), resource)

        assert all(
            "ace-bob" not in (target.edge_id or "") for target in explanation.removal_targets
        )
        assert len(explanation.removal_targets) == 1


class TestCycles:
    """A membership cycle must bound the walk and be reported, never unrolled."""

    def test_a_cycle_terminates_enumeration(self) -> None:
        edges = (
            GraphEdge(edge_key="e1", group_key=FINANCE_TEAM, member_key=ALICE),
            GraphEdge(edge_key="e2", group_key=FINANCE_RW, member_key=FINANCE_TEAM),
            GraphEdge(edge_key="e3", group_key=FINANCE_TEAM, member_key=FINANCE_RW),
        )
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY, key="ace-rw"))

        explanation = explain_access(local(subject, resource), resource, edges=edges)

        assert [path.chain for path in explanation.paths] == [(ALICE, FINANCE_TEAM, FINANCE_RW)]

    def test_a_cycle_is_reported(self) -> None:
        edges = (
            GraphEdge(edge_key="e1", group_key=FINANCE_TEAM, member_key=ALICE),
            GraphEdge(edge_key="e2", group_key=FINANCE_RW, member_key=FINANCE_TEAM),
            GraphEdge(edge_key="e3", group_key=FINANCE_TEAM, member_key=FINANCE_RW),
        )
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY))

        explanation = explain_access(local(subject, resource), resource, edges=edges)

        assert explanation.cycles
        assert set(explanation.cycles[0].members) == {FINANCE_TEAM, FINANCE_RW}

    def test_a_cycle_does_not_break_removal_analysis(self) -> None:
        edges = (
            GraphEdge(edge_key="e1", group_key=FINANCE_TEAM, member_key=ALICE),
            GraphEdge(edge_key="e2", group_key=FINANCE_RW, member_key=FINANCE_TEAM),
            GraphEdge(edge_key="e3", group_key=FINANCE_TEAM, member_key=FINANCE_RW),
        )
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY))

        explanation = explain_access(local(subject, resource), resource, edges=edges)

        first = next(t for t in explanation.removal_targets if t.kind is EdgeKind.MEMBERSHIP)
        assert first.revokes_all_access is True


class TestLimits:
    """Path explosion is bounded, and the bound is reported rather than silent."""

    def test_the_path_limit_truncates_and_says_so(self) -> None:
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(
            allow(FINANCE_RW, READ_EXECUTE),
            allow(FINANCE_RW, MODIFY),
            allow(FINANCE_RW, FULL_CONTROL),
        )

        explanation = explain_access(
            local(subject, resource),
            resource,
            limits=ExplanationLimits(max_paths=2),
        )

        assert len(explanation.paths) == 2
        assert PathTruncation.MAX_PATHS in explanation.truncation
        assert explanation.complete is False

    def test_the_per_trustee_limit_truncates_and_says_so(self) -> None:
        """Three distinct chains to one group, capped at two."""
        edges = (
            GraphEdge(edge_key="e-a", group_key="g-a", member_key=ALICE),
            GraphEdge(edge_key="e-b", group_key="g-b", member_key=ALICE),
            GraphEdge(edge_key="e-c", group_key="g-c", member_key=ALICE),
            GraphEdge(edge_key="e-a-rw", group_key=FINANCE_RW, member_key="g-a"),
            GraphEdge(edge_key="e-b-rw", group_key=FINANCE_RW, member_key="g-b"),
            GraphEdge(edge_key="e-c-rw", group_key=FINANCE_RW, member_key="g-c"),
        )
        subject = token(
            ALICE,
            [
                group_sid("g-a"),
                group_sid("g-b"),
                group_sid("g-c"),
                group_sid(FINANCE_RW, depth=2, path=(ALICE, "g-a", FINANCE_RW)),
            ],
            path=AccessPath.LOCAL,
        )
        resource = dacl(allow(FINANCE_RW, MODIFY))

        explanation = explain_access(
            local(subject, resource),
            resource,
            edges=edges,
            limits=ExplanationLimits(max_paths_per_trustee=2),
        )

        assert len(explanation.paths) == 2
        assert PathTruncation.MAX_PATHS_PER_TRUSTEE in explanation.truncation

    def test_a_truncated_token_is_reported_as_incomplete(self) -> None:
        """A trustee may be missing entirely, not merely one chain to it."""
        subject = nested_token(path=AccessPath.LOCAL, membership_complete=False)
        resource = dacl(allow(FINANCE_RW, MODIFY))

        explanation = explain_access(local(subject, resource), resource, edges=NESTED)

        assert PathTruncation.MEMBERSHIP_INCOMPLETE in explanation.truncation
        assert explanation.complete is False

    def test_without_edges_the_recorded_chain_is_used_and_flagged(self) -> None:
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY))

        explanation = explain_access(local(subject, resource), resource)

        assert explanation.paths[0].chain == (ALICE, FINANCE_TEAM, FINANCE_RW)
        assert PathTruncation.EDGES_NOT_SUPPLIED in explanation.truncation

    def test_the_removal_limit_truncates_and_says_so(self) -> None:
        subject = token(
            ALICE,
            [group_sid(FINANCE_RW, path=(ALICE, FINANCE_RW)), group_sid(DOMAIN_USERS)],
            path=AccessPath.LOCAL,
        )
        resource = dacl(allow(FINANCE_RW, MODIFY), allow(DOMAIN_USERS, READ_EXECUTE))
        edges = (
            GraphEdge(edge_key="e-rw", group_key=FINANCE_RW, member_key=ALICE),
            GraphEdge(edge_key="e-du", group_key=DOMAIN_USERS, member_key=ALICE),
        )

        explanation = explain_access(
            local(subject, resource),
            resource,
            edges=edges,
            limits=ExplanationLimits(max_removal_targets=1),
        )

        assert len(explanation.removal_targets) == 1
        assert PathTruncation.MAX_REMOVAL_TARGETS in explanation.truncation

    def test_limits_are_clamped_not_rejected(self) -> None:
        clamped = DEFAULT_EXPLANATION_LIMITS.clamped(max_paths=10_000, max_removal_targets=0)
        assert clamped.max_paths == 1_000
        assert clamped.max_removal_targets == 1

    def test_a_limit_above_the_ceiling_is_refused_when_constructed_directly(self) -> None:
        with pytest.raises(DomainValidationError):
            ExplanationLimits(max_paths=10_000)


class TestTheGraph:
    def test_nodes_and_edges_are_deduplicated(self) -> None:
        """One node per principal however many paths run through it."""
        edges = (
            *NESTED,
            GraphEdge(edge_key="e-alice-rw", group_key=FINANCE_RW, member_key=ALICE),
        )
        subject = nested_token(path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY), allow(FINANCE_TEAM, READ_EXECUTE))

        graph = explain_access(local(subject, resource), resource, edges=edges).graph

        ids = [node.node_id for node in graph.nodes]
        assert len(ids) == len(set(ids))
        assert sum(1 for node in graph.nodes if node.key == FINANCE_RW) == 1

    def test_two_aces_for_one_trustee_are_two_nodes(self) -> None:
        """Order is load-bearing, so position is part of an ACE's identity."""
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, READ_EXECUTE), allow(FINANCE_RW, MODIFY))

        graph = explain_access(local(subject, resource), resource).graph

        aces = [node for node in graph.nodes if node.is_ace]
        assert len(aces) == 2
        assert {node.position for node in aces} == {0, 1}

    def test_both_layers_appear_for_a_remote_answer(self) -> None:
        subject = nested_token()
        resource = dacl(allow(FINANCE_RW, MODIFY))
        share = share_acl(share_allow(FINANCE_RW, SharePermission.CHANGE))
        access = resolve_access(subject, resource, share)

        explanation = explain_access(access, resource, share, edges=NESTED)

        assert {path.layer for path in explanation.paths} == {
            RightsLayer.NTFS,
            RightsLayer.SMB_SHARE,
        }
        kinds = {node.kind for node in explanation.graph.nodes}
        assert NodeKind.NTFS_ACE in kinds and NodeKind.SMB_ACE in kinds

    def test_an_inherited_ace_is_marked(self) -> None:
        """An inherited entry is fixed on an ancestor; the remediation is a different one."""
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY, inherited=True), key=REPORTS)

        explanation = explain_access(local(subject, resource), resource)

        assert explanation.paths[0].inherited is True


class TestOwnership:
    """The cause no ACL viewer shows: an owner holds control rights whatever the DACL says."""

    def test_ownership_is_reported_as_a_path(self) -> None:
        subject = token(ALICE, path=AccessPath.LOCAL)
        resource = dacl(allow(BOB, MODIFY), owner_sid=ALICE)

        explanation = explain_access(local(subject, resource), resource)

        owned = [p for p in explanation.paths if p.ace_position == OWNERSHIP_POSITION]
        assert len(owned) == 1
        assert owned[0].chain == (ALICE,)
        assert owned[0].effect is PathEffect.CONTRIBUTES
        edge = explanation.graph.edge(owned[0].edge_ids[0])
        assert edge is not None and edge.kind is EdgeKind.OWNERSHIP

    def test_an_owner_denied_everything_still_has_a_path(self) -> None:
        """Deny cannot take WRITE_DAC from an owner, so the rights must still be explained."""
        subject = token(ALICE, path=AccessPath.LOCAL)
        resource = dacl(deny(ALICE, FULL_CONTROL), owner_sid=ALICE)
        access = local(subject, resource)

        explanation = explain_access(access, resource)

        assert access.has_access, "ownership survives an explicit full deny"
        explained = 0
        for path in explanation.grants:
            explained |= path.effective_rights.value
        assert access.rights.expand_generics().value & ~explained == 0

    def test_ownership_is_never_offered_as_a_removal_target(self) -> None:
        """Changing an owner is not the deletion of an edge, and must not be sold as one."""
        subject = token(ALICE, path=AccessPath.LOCAL)
        resource = dacl(allow(BOB, MODIFY), owner_sid=ALICE)

        explanation = explain_access(local(subject, resource), resource)

        assert all(t.kind is not EdgeKind.OWNERSHIP for t in explanation.removal_targets)

    def test_an_ace_regranting_what_ownership_already_confers_is_redundant(self) -> None:
        subject = token(ALICE, path=AccessPath.LOCAL)
        resource = dacl(allow(ALICE, READ_CONTROL | WRITE_DAC, key="ace-noop"), owner_sid=ALICE)

        explanation = explain_access(local(subject, resource), resource)

        by_key = {path.ace_key: path.effect for path in explanation.paths}
        assert by_key["ace-noop"] is PathEffect.REDUNDANT


class TestItRefusesToExplainTheWrongAnswer:
    """A derivation built from a different ACL is plausible and wholly fictional."""

    def test_a_mismatched_resource_is_refused(self) -> None:
        subject = token(ALICE, [FINANCE_RW], path=AccessPath.LOCAL)
        resource = dacl(allow(FINANCE_RW, MODIFY))
        other = dacl(allow(FINANCE_RW, FULL_CONTROL), key=REPORTS)

        with pytest.raises(DomainValidationError, match="Explaining one with the other"):
            explain_access(local(subject, resource), other)

    def test_a_mismatched_share_is_refused(self) -> None:
        subject = nested_token()
        resource = dacl(allow(FINANCE_RW, MODIFY))
        share = share_acl(share_allow(FINANCE_RW))
        access = resolve_access(subject, resource, share)

        with pytest.raises(DomainValidationError):
            explain_access(access, resource, share_acl(share_allow(FINANCE_RW), key="fs02|other"))


class TestTheExplanationDecomposesTheAnswer:
    """Nothing here recomputes the answer; every mask is a decomposition of it."""

    def test_contributing_grants_never_exceed_the_reported_rights(self) -> None:
        subject = nested_token()
        resource = dacl(
            allow(FINANCE_RW, FULL_CONTROL, key="ace-rw"),
            allow(FINANCE_TEAM, MODIFY, key="ace-team"),
        )
        share = share_acl(share_allow(FINANCE_RW, SharePermission.READ))
        access = resolve_access(subject, resource, share)

        explanation = explain_access(access, resource, share, edges=NESTED)

        final = access.rights.expand_generics().value
        for path in explanation.grants:
            assert path.effective_rights.value & ~final == 0, (
                f"{path.path_id} claims rights the answer does not report"
            )

    def test_every_reported_right_is_explained_by_some_grant(self) -> None:
        """A right nobody can account for is an engine that cannot be audited."""
        subject = nested_token()
        resource = dacl(
            allow(FINANCE_TEAM, READ_EXECUTE, key="ace-team"),
            allow(FINANCE_RW, MODIFY, key="ace-rw"),
        )
        share = share_acl(share_allow(FINANCE_RW, SharePermission.CHANGE))
        access = resolve_access(subject, resource, share)

        explanation = explain_access(access, resource, share, edges=NESTED)

        explained = 0
        for path in explanation.grants:
            if path.layer is RightsLayer.NTFS:
                explained |= path.effective_rights.value
        assert access.rights.expand_generics().value & ~explained == 0
