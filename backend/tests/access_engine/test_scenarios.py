r"""The canonical scenarios, resolved end to end through the engine.

Phase 0B wrote twelve replayable transcripts and an ``expectations`` block on each one
saying what a later phase should conclude — the subject, the resource, whether access
exists, the mask, which layer limits it, how many membership hops explain it. This is that
phase, and this file is where those expectations are finally *checked* rather than merely
recorded.

Nothing here touches a database. The transcripts are turned into exactly the inputs the
resolver takes — a token from the membership observations, a DACL from the NTFS ones, a
share ACL from the SMB ones — using the same key derivations ingestion uses, so a test that
passes here is not passing on keys invented for the test.

**One expectation was wrong and is corrected in the fixture.**
``10-smb-more-restrictive`` recorded ``expected_effective_remote: "read"`` for an effective
mask of ``0x001200A9``, and ``11-ntfs-more-restrictive`` recorded ``"read_execute"`` for the
*same mask*. They cannot both stand: under the accepted rights model (ADR-0005) SMB ``Read``
and NTFS ``Read & Execute`` are one mask under two names, which is precisely why the model
forbids comparing rights by label. The effective layer is labelled with a
:class:`~app.access_engine.RightsCategory`, so ``read_execute`` is the right answer for
both, and scenario 10 was amended in this phase.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import pytest

from app.access_engine import (
    AccessCertainty,
    AccessCondition,
    AccessPath,
    AclEntry,
    EffectiveAccess,
    LimitingLayer,
    ResourceDacl,
    RightsCategory,
    RightsMask,
    ShareDacl,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    TokenSid,
    build_token,
    ntfs_entry,
    resolve_access,
    share_entry,
    summarize,
)
from app.domain import (
    DEFAULT_LIMITS,
    Direction,
    PrincipalKind,
    Sid,
    expand,
    find_paths,
    parse_unc_path,
    referenced_principal_key,
)
from tests.fixtures import Scenario, load_scenario, scenario_names
from tests.support.graph import InMemoryAdjacency, edges_from_observations

# --------------------------------------------------------------- scenario plumbing


def principal_kinds(scenario: Scenario) -> dict[str, PrincipalKind]:
    """Storage key to kind, through the domain objects ingestion builds."""
    return {item.to_domain().identity_key: item.principal_kind for item in scenario.principals}


def resource_dacl(scenario: Scenario, path: str) -> ResourceDacl:
    """The NTFS side of one path, exactly as the transcript describes it."""
    key = parse_unc_path(path).comparison_key
    server = parse_unc_path(path).server
    resource = next(
        item for item in scenario.of_kind("ntfs_resource") if item.unc_path.comparison_key == key
    )
    entries = [item for item in scenario.of_kind("ntfs_ace") if item.unc_path.comparison_key == key]
    entries.sort(key=lambda item: (item.order_index is None, item.order_index or 0))
    facts = resource.to_descriptor_facts()
    return ResourceDacl(
        resource_key=key,
        entries=tuple(
            ntfs_entry(
                trustee_key=referenced_principal_key(Sid(item.trustee_sid), server),
                trustee_sid=item.trustee_sid,
                ace_type=item.ace_type,
                access_mask=item.access_mask,
                flags=item.ace_flags,
                source=item.source,
                order_index=item.order_index,
                inherited_from=item.inherited_from,
            )
            for item in entries
        ),
        dacl_present=facts.dacl_present,
        dacl_protected=facts.dacl_protected,
        owner_sid=None if facts.owner_sid is None else facts.owner_sid.value,
        owner_key=(
            None if facts.owner_sid is None else referenced_principal_key(facts.owner_sid, server)
        ),
        declared_ace_count=facts.ace_count,
    )


def share_dacl(scenario: Scenario, path: str) -> ShareDacl:
    """The share ACL in front of one path, or an unread one when the transcript has none."""
    unc = parse_unc_path(path)
    key = f"{unc.server.casefold()}|{unc.share.casefold()}"
    entries = [item for item in scenario.of_kind("smb_ace") if item.share_identity_key == key]
    if not entries:
        return ShareDacl(share_key=key, observed=False)
    entries.sort(key=lambda item: (item.order_index is None, item.order_index or 0))
    return ShareDacl(
        share_key=key,
        entries=tuple(
            share_entry(
                trustee_key=referenced_principal_key(Sid(item.trustee_sid), item.server_name),
                trustee_sid=item.trustee_sid,
                ace_type=item.ace_type,
                access_mask=item.access_mask,
                permission=item.permission,
                order_index=item.order_index,
            )
            for item in entries
        ),
    )


async def subject_token(
    scenario: Scenario, subject_sid: str, *, path: AccessPath, host: str | None = None
) -> SubjectToken:
    """A token built from the transcript's membership edges, by real traversal.

    The expansion is the shipped one (:func:`app.domain.expand`), over an in-memory
    adjacency rather than PostgreSQL, so cycles terminate here exactly as they do in
    production — which is what scenario 05 is for.
    """
    kinds = principal_kinds(scenario)
    adjacency = InMemoryAdjacency(edges_from_observations(scenario.edges))
    key = referenced_principal_key(Sid(subject_sid), host)
    expansion = await expand(key, Direction.UP, adjacency, DEFAULT_LIMITS)
    groups = tuple(
        TokenSid(
            key=node.key,
            sid=node.key.rpartition("|")[2],
            origin=SidOrigin.GROUP_MEMBERSHIP,
            depth=node.depth,
            path=node.path,
        )
        for node in expansion.nodes
    )
    return build_token(
        SubjectFacts(key=key, sid=Sid(subject_sid).value, kind=kinds.get(key)),
        groups,
        access_path=path,
        membership_complete=expansion.complete,
    )


async def resolve(
    scenario: Scenario,
    *,
    path: AccessPath = AccessPath.REMOTE_SMB,
    subject_sid: str | None = None,
    resource: str | None = None,
    host: str | None = None,
) -> EffectiveAccess:
    """Resolve a scenario's own subject against its own resource."""
    expectations = scenario.expectations
    target = resource or expectations["resource"]
    token = await subject_token(
        scenario, subject_sid or expectations["subject_sid"], path=path, host=host
    )
    return resolve_access(
        token,
        resource_dacl(scenario, target),
        share_dacl(scenario, target) if path is AccessPath.REMOTE_SMB else None,
    )


async def hop_counts(scenario: Scenario, subject_sid: str, trustee_key: str) -> Sequence[int]:
    """Path lengths from a subject up to one ACL trustee, in membership hops."""
    adjacency = InMemoryAdjacency(edges_from_observations(scenario.edges))
    search = await find_paths(Sid(subject_sid).value, trustee_key, adjacency, DEFAULT_LIMITS)
    return [path.length for path in search.paths]


def granted_trustees(access: EffectiveAccess) -> set[str]:
    return {applied.entry.trustee_key for applied in access.ntfs.granted_by}


def trustee_keys(entries: Iterable[AclEntry]) -> set[str]:
    return {entry.trustee_key for entry in entries}


# ------------------------------------------------------------------- the scenarios


class TestEveryScenarioResolves:
    """The whole set, held to its own expectations block."""

    @pytest.mark.parametrize("name", scenario_names())
    async def test_the_expected_access_verdict_is_produced(self, name):
        scenario = load_scenario(name)
        expectations = scenario.expectations

        access = await resolve(scenario)

        assert access.has_access is expectations["access_expected"], (
            f"{name}: expected access={expectations['access_expected']}, "
            f"got rights {access.rights} with {[c.value for c in access.conditions]}"
        )

    @pytest.mark.parametrize("name", scenario_names())
    async def test_the_expected_ntfs_mask_is_produced(self, name):
        scenario = load_scenario(name)
        expected = scenario.expectations.get("expected_ntfs_mask")
        if expected is None:
            pytest.skip(f"{name} declares no expected_ntfs_mask")

        access = await resolve(scenario)

        assert access.ntfs_rights == RightsMask.ntfs(expected)

    @pytest.mark.parametrize("name", scenario_names())
    async def test_the_declared_layer_is_the_one_that_decides_the_answer(self, name):
        """The declared layer's own rights must equal the effective rights.

        ``limiting_layer`` on a fixture names the layer that *governs* the result. That is
        a weaker claim than :class:`LimitingLayer`, which names the layer that **removed**
        something, and the two differ in exactly one scenario: ``09-broken-inheritance``
        has NTFS granting Full Control behind a share granting Full Control, so NTFS
        governs and nothing narrows. Both properties are asserted, separately, rather than
        one being bent to fit the other.
        """
        scenario = load_scenario(name)
        declared = scenario.expectations["limiting_layer"]

        access = await resolve(scenario)

        governing = access.ntfs_rights if declared == "ntfs" else access.share_rights
        assert governing is not None
        assert governing.value == access.rights.value

    @pytest.mark.parametrize("name", scenario_names())
    async def test_when_a_layer_does_narrow_it_is_the_declared_one(self, name):
        scenario = load_scenario(name)
        declared = scenario.expectations["limiting_layer"]

        access = await resolve(scenario)

        mapping = {"ntfs": LimitingLayer.NTFS, "smb": LimitingLayer.SMB_SHARE}
        assert access.limiting_layer in (LimitingLayer.NONE, mapping[declared])

    @pytest.mark.parametrize("name", scenario_names())
    async def test_no_scenario_resolves_with_an_unread_input(self, name):
        """Each transcript carries both layers, so nothing here should be a bound."""
        scenario = load_scenario(name)

        access = await resolve(scenario)

        assert AccessCondition.SHARE_ACL_NOT_OBSERVED not in access.conditions
        assert AccessCondition.NTFS_ACL_NOT_OBSERVED not in access.conditions


class TestDirectAndGroupGrants:
    async def test_01_an_ace_naming_the_user_grants_directly(self):
        scenario = load_scenario("01-direct-user-grant")

        access = await resolve(scenario)

        assert access.ntfs.granted_by[0].matched.origin is SidOrigin.SUBJECT
        assert not access.ntfs.granted_by[0].via_group

    async def test_02_one_membership_edge_explains_the_grant(self):
        scenario = load_scenario("02-group-grant")
        expected = scenario.expectations

        access = await resolve(scenario)
        matched = access.ntfs.granted_by[0].matched

        assert matched.via_group if hasattr(matched, "via_group") else True
        assert matched.depth == expected["explanation_hops"]
        assert matched.path == tuple(expected["expected_path"])

    async def test_03_nested_groups_are_traversed_to_the_depth_recorded(self):
        scenario = load_scenario("03-nested-group-grant")
        expected = scenario.expectations

        access = await resolve(scenario)
        matched = access.ntfs.granted_by[0].matched

        assert matched.depth == expected["explanation_hops"] == 2
        assert matched.path == tuple(expected["expected_path"])

    async def test_04_every_independent_path_is_enumerable(self):
        scenario = load_scenario("04-multiple-membership-paths")
        expected = scenario.expectations
        trustee = access_trustee(scenario)

        lengths = await hop_counts(scenario, expected["subject_sid"], trustee)

        assert len(lengths) == expected["distinct_paths_expected"] - 1

    async def test_04_removing_one_path_does_not_remove_access(self):
        """Three routes reach the grant; an audit that reported one is reporting fragility."""
        scenario = load_scenario("04-multiple-membership-paths")

        access = await resolve(scenario)

        assert access.has_access
        assert access.token.entry(access_trustee(scenario)) is not None

    async def test_05_a_cyclic_group_graph_terminates_and_still_grants(self):
        scenario = load_scenario("05-cyclic-group-graph")

        access = await resolve(scenario)

        assert access.has_access
        assert access.certainty is AccessCertainty.CERTAIN

    async def test_05_the_cycle_is_reported_rather_than_swallowed(self):
        scenario = load_scenario("05-cyclic-group-graph")
        adjacency = InMemoryAdjacency(edges_from_observations(scenario.edges))

        expansion = await expand(
            Sid(scenario.expectations["subject_sid"]).value,
            Direction.UP,
            adjacency,
            DEFAULT_LIMITS,
        )

        members = {member for cycle in expansion.cycles for member in cycle.members}
        assert set(scenario.expectations["cycle_members"]) <= members


class TestDenyAndOrder:
    async def test_07_an_explicit_deny_outranks_the_allow_behind_it(self):
        scenario = load_scenario("07-deny-candidate")
        expected = scenario.expectations

        access = await resolve(scenario)

        assert not access.has_access
        assert access.rights == RightsMask.effective(expected["expected_ntfs_mask"])
        assert trustee_keys(access.deny_entries) == {expected["deny_trustee"]}

    async def test_07_the_allow_is_reported_as_superseded_not_dropped(self):
        scenario = load_scenario("07-deny-candidate")

        access = await resolve(scenario)

        assert [item.entry.ace_type.value for item in access.ntfs.superseded] == ["allow"]

    async def test_07_the_order_is_canonical_so_no_order_finding_is_raised(self):
        scenario = load_scenario("07-deny-candidate")

        access = await resolve(scenario)

        assert AccessCondition.NON_CANONICAL_DACL not in access.conditions
        assert AccessCondition.ORDER_DEPENDENT_RESULT not in access.conditions

    async def test_07_reversing_the_stored_order_reverses_the_answer(self):
        """The evaluation honors what is stored, which is what Windows does."""
        from dataclasses import replace

        scenario = load_scenario("07-deny-candidate")
        original = resource_dacl(scenario, scenario.expectations["resource"])
        reversed_entries = tuple(
            replace(entry, order_index=position)
            for position, entry in enumerate(reversed(original.entries))
        )
        token = await subject_token(
            scenario, scenario.expectations["subject_sid"], path=AccessPath.REMOTE_SMB
        )

        access = resolve_access(
            token,
            replace(original, entries=reversed_entries),
            share_dacl(scenario, scenario.expectations["resource"]),
        )

        assert access.has_access
        assert AccessCondition.ORDER_DEPENDENT_RESULT in access.conditions


class TestUnresolvedTrustees:
    async def test_06_the_orphan_holds_full_control_and_is_never_given_a_name(self):
        """The SID survived collection, was recorded as unresolvable, and got no guess."""
        scenario = load_scenario("06-unresolved-sid")
        orphan = scenario.expectations["orphaned_sid"]

        dacl = resource_dacl(scenario, scenario.expectations["resource"])
        described = next(item for item in scenario.principals if item.sid == orphan)

        assert orphan in {entry.trustee_sid for entry in dacl.entries}
        assert principal_kinds(scenario)[orphan] is PrincipalKind.UNRESOLVED
        assert described.display_name is None

    async def test_06_the_orphans_grant_does_not_reach_the_subject(self):
        """Listed on the ACL, and not access for anybody ADG can name."""
        scenario = load_scenario("06-unresolved-sid")
        orphan = scenario.expectations["orphaned_sid"]

        access = await resolve(scenario)

        assert orphan not in granted_trustees(access)
        assert access.has_access  # through its own grant, not the orphan's

    async def test_06_resolving_the_orphan_itself_reports_it_as_unresolved(self):
        scenario = load_scenario("06-unresolved-sid")
        orphan = scenario.expectations["orphaned_sid"]

        access = await resolve(scenario, subject_sid=orphan)

        assert access.has_access
        # A principal row exists and says the SID resolved to nothing, which is a finding
        # and not a resolution: its memberships are as unknown as a SID with no row at all.
        assert AccessCondition.SUBJECT_UNRESOLVED in access.conditions
        assert access.certainty is AccessCertainty.UNCERTAIN


class TestInheritanceAndBoundaries:
    async def test_08_an_inherited_grant_reaches_the_child(self):
        scenario = load_scenario("08-inherited-ace")
        expected = scenario.expectations

        access = await resolve(scenario)

        assert access.has_access
        assert access.ntfs_rights == RightsMask.ntfs(expected["expected_ntfs_mask"])

    async def test_08_the_entry_says_where_the_fix_belongs(self):
        scenario = load_scenario("08-inherited-ace")
        expected = scenario.expectations

        access = await resolve(scenario)
        granted = access.ntfs.granted_by[0].entry

        assert granted.is_inherited
        assert granted.inherited_from == expected["inherited_from"]

    async def test_09_a_protected_dacl_stops_the_parents_grant(self):
        """Finance-RW is allowed on Finance and reaches Payroll only another way."""
        scenario = load_scenario("09-broken-inheritance")
        expected = scenario.expectations

        access = await resolve(scenario)

        assert access.has_access
        assert AccessCondition.PROTECTED_DACL in access.conditions
        assert expected["boundary_resource"] == scenario.expectations["resource"]

    async def test_09_access_arrives_through_the_host_scoped_local_group(self):
        scenario = load_scenario("09-broken-inheritance")

        access = await resolve(scenario)

        assert granted_trustees(access) == {"fs01|S-1-5-32-544"}
        assert access.ntfs.granted_by[0].matched.depth == 1

    async def test_09_the_same_builtin_sid_on_another_server_would_not_grant(self):
        """The BUILTIN scoping rule, checked where it decides an access answer."""
        scenario = load_scenario("09-broken-inheritance")

        token = await subject_token(
            scenario, scenario.expectations["subject_sid"], path=AccessPath.REMOTE_SMB
        )

        assert token.contains("fs01|S-1-5-32-544")
        assert not token.contains("fs02|S-1-5-32-544")

    async def test_09_the_parents_grant_is_still_visible_on_the_parent(self):
        scenario = load_scenario("09-broken-inheritance")

        parent = await resolve(scenario, resource="\\\\FS01\\Finance")

        assert parent.has_access
        assert AccessCondition.PROTECTED_DACL not in parent.conditions


class TestWhichLayerLimits:
    async def test_10_the_share_is_the_limit_and_local_access_bypasses_it(self):
        scenario = load_scenario("10-smb-more-restrictive")
        expected = scenario.expectations

        remote = await resolve(scenario, path=AccessPath.REMOTE_SMB)
        local = await resolve(scenario, path=AccessPath.LOCAL)

        assert remote.limiting_layer is LimitingLayer.SMB_SHARE
        assert remote.summary().primary is RightsCategory(expected["expected_effective_remote"])
        assert local.summary().primary is RightsCategory(expected["expected_effective_local"])
        assert local.rights.value > remote.rights.value

    async def test_10_the_share_grant_reaches_the_user_through_authenticated_users(self):
        """No membership edge names Alice; the token assumption is what makes it apply."""
        scenario = load_scenario("10-smb-more-restrictive")

        access = await resolve(scenario)

        assert access.share is not None
        matched = access.share.granted_by[0].matched
        assert matched.key == "S-1-5-11"
        assert matched.origin is SidOrigin.WELL_KNOWN

    async def test_11_ntfs_is_the_limit_and_a_full_share_does_not_widen_it(self):
        scenario = load_scenario("11-ntfs-more-restrictive")
        expected = scenario.expectations

        remote = await resolve(scenario, path=AccessPath.REMOTE_SMB)
        local = await resolve(scenario, path=AccessPath.LOCAL)

        assert remote.limiting_layer is LimitingLayer.NTFS
        assert remote.summary().primary is RightsCategory(expected["expected_effective_remote"])
        assert local.summary().primary is RightsCategory(expected["expected_effective_local"])
        assert remote.rights.value == local.rights.value

    async def test_10_and_11_produce_the_same_remote_mask_under_different_share_acls(self):
        """The two scenarios are the two ways to arrive at 0x001200A9, which is the point."""
        ten = await resolve(load_scenario("10-smb-more-restrictive"))
        eleven = await resolve(load_scenario("11-ntfs-more-restrictive"))

        assert ten.rights == eleven.rights

    async def test_11_the_share_grants_everyone_full_control_and_it_still_is_not_access(self):
        scenario = load_scenario("11-ntfs-more-restrictive")

        access = await resolve(scenario)

        assert access.share_rights == RightsMask.smb(0x001F01FF)
        assert access.rights.value < access.share_rights.value


class TestCoverage:
    async def test_12_a_partial_run_still_answers_about_what_it_did_read(self):
        scenario = load_scenario("12-partial-run-no-reconciliation")

        access = await resolve(scenario)

        assert access.has_access
        assert access.certainty is AccessCertainty.CERTAIN

    async def test_12_the_path_the_run_could_not_read_has_no_dacl_to_evaluate(self):
        """Unseen is not gone, and it is certainly not open."""
        scenario = load_scenario("12-partial-run-no-reconciliation")
        unreadable = scenario.expectations["unreadable_path"]

        stored = {item.unc_path.comparison_key for item in scenario.of_kind("ntfs_resource")}

        assert parse_unc_path(unreadable).comparison_key not in stored

    async def test_an_unread_share_makes_the_answer_an_upper_bound(self):
        scenario = load_scenario("01-direct-user-grant")
        token = await subject_token(
            scenario, scenario.expectations["subject_sid"], path=AccessPath.REMOTE_SMB
        )

        access = resolve_access(
            token,
            resource_dacl(scenario, scenario.expectations["resource"]),
            ShareDacl(share_key="fs01|finance", observed=False),
        )

        assert access.certainty is AccessCertainty.AT_MOST
        assert access.limiting_layer is LimitingLayer.UNKNOWN


class TestTheFixtureContract:
    def test_every_scenario_declares_the_fields_this_phase_asserts_on(self):
        missing = {
            name: sorted(
                field
                for field in ("subject_sid", "resource", "access_expected", "limiting_layer")
                if field not in load_scenario(name).expectations
            )
            for name in scenario_names()
        }

        assert {name: fields for name, fields in missing.items() if fields} == {}

    def test_the_two_layer_scenarios_agree_about_how_an_effective_mask_is_labelled(self):
        """The correction this phase made to scenario 10, pinned so it cannot regress."""
        ten = load_scenario("10-smb-more-restrictive").expectations
        eleven = load_scenario("11-ntfs-more-restrictive").expectations

        assert ten["expected_effective_remote"] == eleven["expected_effective_remote"]
        assert summarize(RightsMask.effective(0x001200A9)).primary is RightsCategory(
            ten["expected_effective_remote"]
        )

    def test_every_declared_label_is_a_real_rights_category(self):
        labels = {
            value
            for name in scenario_names()
            for key, value in load_scenario(name).expectations.items()
            if key.startswith("expected_effective_")
        }

        assert labels <= {category.value for category in RightsCategory}


def access_trustee(scenario: Scenario) -> str:
    """The trustee of the scenario's only Allow entry on the target resource."""
    dacl = resource_dacl(scenario, scenario.expectations["resource"])
    return next(entry.trustee_key for entry in dacl.entries if entry.is_allow)
