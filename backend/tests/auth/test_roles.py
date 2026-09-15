"""The role table, tested as a table.

These assertions are deliberately literal. A test that recomputed the expected capability
set from ``ROLE_CAPABILITIES`` would agree with any table, including a wrong one; writing
the sets out means a change to who can do what has to be made twice, on purpose.
"""

from __future__ import annotations

import pytest

from app.auth.roles import (
    ACTIVE_ROLES,
    RESERVED_CAPABILITIES,
    RESERVED_ROLES,
    ROLE_CAPABILITIES,
    Capability,
    Role,
    capabilities_for,
    parse_roles,
)


class TestWhatEachRoleGrants:
    def test_a_viewer_reads_the_estate_and_nothing_else(self) -> None:
        assert capabilities_for(frozenset({Role.VIEWER})) == frozenset(
            {
                Capability.RESOURCES_READ,
                Capability.IDENTITIES_READ,
                Capability.ACCESS_READ,
                Capability.RISKS_READ,
                # Seeing that the payroll share was edited is the same disclosure class as
                # seeing the edit in the change feed, which a viewer already holds. What a
                # viewer deliberately does not get is ALERTS_MANAGE -- see below.
                Capability.ALERTS_READ,
                Capability.CHANGES_READ,
                Capability.COLLECTORS_READ,
                Capability.SEARCH,
            }
        )

    def test_a_viewer_can_see_whether_collection_succeeded(self) -> None:
        """Not a convenience. Without it, a viewer cannot tell an empty estate from a
        failed scan, which is the misreading this product exists to prevent."""
        assert Capability.COLLECTORS_READ in capabilities_for(frozenset({Role.VIEWER}))

    def test_an_auditor_adds_reading_configuration_and_attestations(self) -> None:
        auditor = capabilities_for(frozenset({Role.AUDITOR}))
        viewer = capabilities_for(frozenset({Role.VIEWER}))

        assert auditor - viewer == {
            Capability.SETTINGS_READ,
            Capability.GOVERNANCE_READ,
            Capability.SIMULATIONS_READ,
            # Whether a change was simulated and approved before it was carried out is
            # exactly what an audit asks, and it cannot be asked of records the auditor
            # cannot read. Strictly reading: an auditor writes no plan, approves none and
            # exports none.
            Capability.REMEDIATION_READ,
        }

    def test_a_plain_viewer_sees_no_governance_record(self) -> None:
        """Not an oversight. A decision rationale can name a person and say something about
        them -- "leaver", "should never have had this" -- that no access-control list ever
        would, so reading attestations is a strictly wider disclosure than reading the
        estate and is granted separately."""
        viewer = capabilities_for(frozenset({Role.VIEWER}))

        assert not (
            viewer
            & {
                Capability.GOVERNANCE_READ,
                Capability.GOVERNANCE_REVIEW,
                Capability.GOVERNANCE_MANAGE,
            }
        )

    def test_an_admin_adds_writing_configuration_ingestion_and_alert_operations(self) -> None:
        admin = capabilities_for(frozenset({Role.ADMIN}))
        auditor = capabilities_for(frozenset({Role.AUDITOR}))

        assert admin - auditor == {
            Capability.SETTINGS_WRITE,
            Capability.COLLECTORS_INGEST,
            Capability.ALERTS_MANAGE,
            Capability.SIMULATIONS_RUN,
            # The signed change plan is produced by whoever is going to do the work. This
            # role deliberately gains neither REMEDIATION_PLAN nor REMEDIATION_APPROVE: an
            # administrator able to write a plan, approve it and export it would be the
            # single account ADR-0038 exists to rule out.
            Capability.REMEDIATION_EXPORT,
        }

    def test_a_plain_viewer_cannot_read_or_run_a_what_if(self) -> None:
        """Deliberate, and the reason is the composition rather than any one answer.

        A viewer can already ask who reaches a share and what a group contains. A simulation
        composes those into "put this account in that group and it reaches the payroll
        share", which is a route map for privilege escalation rather than a further fact
        about the estate -- so it is granted from ``auditor`` upward. See ADR-0034.
        """
        viewer = capabilities_for(frozenset({Role.VIEWER}))

        assert not (viewer & {Capability.SIMULATIONS_READ, Capability.SIMULATIONS_RUN})

    def test_reading_a_what_if_does_not_grant_running_one(self) -> None:
        """Resolving a proposal's affected scope is the most expensive request this API
        serves. An account that may read what somebody already ran must not thereby be able
        to make the estate resolve a thousand new pairs."""
        auditor = capabilities_for(frozenset({Role.AUDITOR}))

        assert Capability.SIMULATIONS_READ in auditor
        assert Capability.SIMULATIONS_RUN not in auditor

    def test_nobody_who_only_reads_can_configure_a_watch(self) -> None:
        """A watch decides who gets woken up.

        Somebody who could quietly disable the watch on the payroll share could make an
        exposure land in nobody's inbox -- which is the shape of the thing this product
        exists to find, performed on the product. Reading an alert and choosing who is paged
        are therefore separately held.
        """
        for role in (Role.VIEWER, Role.AUDITOR, Role.REVIEWER):
            granted = capabilities_for(frozenset({role}))
            assert Capability.ALERTS_READ in granted
            assert Capability.ALERTS_MANAGE not in granted

    def test_a_governance_administrator_does_not_configure_alerts(self) -> None:
        """Choosing who is paged is an operations decision, not part of running a review.

        The two roles failing separately is the point: a governance administrator who could
        also silence the alerting would hold both halves of the control.
        """
        assert Capability.ALERTS_MANAGE not in capabilities_for(frozenset({Role.GOVERNANCE_ADMIN}))

    def test_no_role_may_write_the_estate(self) -> None:
        """ADG is read-only by default (SECURITY.md). Remediation is not a capability any
        role holds, and this is the test that fails the day somebody grants it by
        accident."""
        for role in Role:
            assert Capability.REMEDIATION_EXECUTE not in capabilities_for(frozenset({role}))

    def test_a_reviewer_may_answer_a_review_but_not_run_one(self) -> None:
        reviewer = capabilities_for(frozenset({Role.REVIEWER}))

        assert Capability.GOVERNANCE_REVIEW in reviewer
        assert Capability.GOVERNANCE_MANAGE not in reviewer

    def test_nobody_can_both_write_a_change_plan_and_approve_it(self) -> None:
        """The separation of duties for remediation, as a property over the whole table.

        Written as a property rather than as three assertions about three roles, so that a
        role added later is covered without anybody remembering to extend this. An account
        able to write a plan and approve it could produce a fully documented, fully audited
        removal of anybody's access with one person's involvement -- and the audit trail
        would look impeccable, which is what makes it worth a structural test rather than a
        code review. See ADR-0038.
        """
        for role, granted in ROLE_CAPABILITIES.items():
            both = {Capability.REMEDIATION_PLAN, Capability.REMEDIATION_APPROVE} <= granted
            assert not both, f"{role.value} can write a change plan and approve it"

    def test_nobody_can_both_approve_a_change_plan_and_export_it(self) -> None:
        """The third pair of hands. Approving a plan and producing the signed instruction a
        person carries out are separately held, so that the approval an executing
        administrator relies on came from somebody else."""
        for role, granted in ROLE_CAPABILITIES.items():
            both = {Capability.REMEDIATION_APPROVE, Capability.REMEDIATION_EXPORT} <= granted
            assert not both, f"{role.value} can approve a change plan and export it"

    def test_a_remediation_approver_cannot_re_run_the_what_if(self) -> None:
        """Reading the blast radius is required to approve; re-running it is not.

        An approver who could run the simulation again with narrower bounds until the impact
        list looked acceptable would be approving their own answer, which is the failure the
        approval exists to prevent wearing a different hat.
        """
        approver = capabilities_for(frozenset({Role.REMEDIATION_APPROVER}))

        assert Capability.SIMULATIONS_READ in approver
        assert Capability.SIMULATIONS_RUN not in approver

    def test_a_governance_admin_may_run_a_review_but_not_answer_one(self) -> None:
        """The separation of duties, as one assertion. Whoever chooses which grants are put
        to a reviewer must not also be able to answer them, because a single account holding
        both can decide what it will be asked and then rubber-stamp it -- and the audit trail
        would show a complete, compliant campaign. Somebody who must do both is given both
        roles, which is a visible assignment rather than a silent property of one."""
        manager = capabilities_for(frozenset({Role.GOVERNANCE_ADMIN}))

        assert Capability.GOVERNANCE_MANAGE in manager
        assert Capability.GOVERNANCE_REVIEW not in manager

    def test_holding_both_governance_roles_grants_both(self) -> None:
        """The separation is a default, not a prohibition: a small organization where one
        person does both is expressible, and the assignment says so."""
        both = capabilities_for(frozenset({Role.REVIEWER, Role.GOVERNANCE_ADMIN}))

        assert {Capability.GOVERNANCE_REVIEW, Capability.GOVERNANCE_MANAGE} <= both

    def test_a_platform_admin_runs_no_review(self) -> None:
        """`admin` configures the server; it does not run access reviews. Letting whoever
        operates ADG quietly create and close attestation campaigns is precisely what an
        auditor would object to. See ADR-0029, which also states the limit of this control:
        anyone with the database credentials can do anything, so it guards against accident
        and casual misuse rather than against a determined operator."""
        admin = capabilities_for(frozenset({Role.ADMIN}))

        assert Capability.GOVERNANCE_MANAGE not in admin
        assert Capability.GOVERNANCE_REVIEW not in admin
        # It does read them: an administrator investigating a complaint has to be able to.
        assert Capability.GOVERNANCE_READ in admin

    def test_roles_combine_by_union(self) -> None:
        combined = capabilities_for(frozenset({Role.VIEWER, Role.ADMIN}))

        assert combined == capabilities_for(frozenset({Role.ADMIN}))


class TestTheReservedRole:
    def test_remediator_is_reserved_and_grants_nothing(self) -> None:
        assert Role.REMEDIATOR in RESERVED_ROLES
        assert capabilities_for(frozenset({Role.REMEDIATOR})) == frozenset()

    def test_holding_only_the_reserved_role_is_indistinguishable_from_holding_none(
        self,
    ) -> None:
        assert capabilities_for(frozenset({Role.REMEDIATOR})) == capabilities_for(frozenset())

    def test_the_reserved_role_does_not_subtract_from_an_active_one(self) -> None:
        """A user who is given remediator *as well as* viewer must still be a viewer."""
        assert capabilities_for(frozenset({Role.VIEWER, Role.REMEDIATOR})) == capabilities_for(
            frozenset({Role.VIEWER})
        )

    def test_the_reserved_capability_is_unreachable_from_any_role(self) -> None:
        reachable = frozenset().union(*ROLE_CAPABILITIES.values())

        assert not (reachable & RESERVED_CAPABILITIES)

    def test_active_roles_is_every_role_that_is_not_reserved(self) -> None:
        assert (
            frozenset(
                {
                    Role.VIEWER,
                    Role.AUDITOR,
                    Role.ADMIN,
                    Role.REVIEWER,
                    Role.GOVERNANCE_ADMIN,
                    Role.REMEDIATION_PLANNER,
                    Role.REMEDIATION_APPROVER,
                }
            )
            == ACTIVE_ROLES
        )


class TestParsingRolesFromATenant:
    def test_it_accepts_the_values_adg_publishes(self) -> None:
        roles, unknown = parse_roles(["viewer", "admin"])

        assert roles == frozenset({Role.VIEWER, Role.ADMIN})
        assert unknown == ()

    @pytest.mark.parametrize("written", ["Viewer", "VIEWER", "  viewer  "])
    def test_it_tolerates_the_casing_and_spacing_a_human_types(self, written: str) -> None:
        roles, unknown = parse_roles([written])

        assert roles == frozenset({Role.VIEWER})
        assert unknown == ()

    def test_an_unknown_value_grants_nothing_and_is_reported(self) -> None:
        """Reported rather than dropped: 'I assigned Auditors and they see nothing' has to
        be answerable from a log line."""
        roles, unknown = parse_roles(["Auditors", "viewer"])

        assert roles == frozenset({Role.VIEWER})
        assert unknown == ("Auditors",)

    def test_an_unknown_value_is_reported_once(self) -> None:
        _, unknown = parse_roles(["Auditors", "Auditors"])

        assert unknown == ("Auditors",)

    def test_empty_values_are_ignored_rather_than_reported(self) -> None:
        roles, unknown = parse_roles(["", "   ", "viewer"])

        assert roles == frozenset({Role.VIEWER})
        assert unknown == ()

    def test_a_capability_string_is_not_a_role(self) -> None:
        """Capabilities and roles are different vocabularies; a token naming one must not
        satisfy the other."""
        roles, unknown = parse_roles(["resources:read"])

        assert roles == frozenset()
        assert unknown == ("resources:read",)
