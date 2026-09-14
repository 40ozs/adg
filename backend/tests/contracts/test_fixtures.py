"""The canonical test vectors.

Every scenario is validated twice — against the published JSON Schemas (what a collector
author codes against) and through the backend models (what the API accepts) — and then
checked for the structural property that makes it worth keeping. If a later phase changes
a contract in a way that breaks a scenario, this fails here rather than in that phase.

The ``expectations`` blocks are checked for shape only. They state what Phase 4 must
conclude; Phase 0B does not compute effective access.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.domain import AceFlag, AceType, NtfsRight, PrincipalKind, Sid
from tests.contracts.test_json_schemas import REGISTRY, SCHEMA_DIR
from tests.fixtures import load_all, load_raw, load_scenario, scenario_names

SCENARIOS = scenario_names()

REQUIRED_SCENARIOS = {
    "01-direct-user-grant",
    "02-group-grant",
    "03-nested-group-grant",
    "04-multiple-membership-paths",
    "05-cyclic-group-graph",
    "06-unresolved-sid",
    "07-deny-candidate",
    "08-inherited-ace",
    "09-broken-inheritance",
    "10-smb-more-restrictive",
    "11-ntfs-more-restrictive",
}

OBSERVATION_SCHEMA = {
    "principal": "principal-observation.schema.json",
    "membership_edge": "membership-observation.schema.json",
    "server": "server-observation.schema.json",
    "smb_share": "smb-share-observation.schema.json",
    "smb_ace": "smb-ace-observation.schema.json",
    "ntfs_resource": "ntfs-resource-observation.schema.json",
    "ntfs_ace": "ntfs-ace-observation.schema.json",
}


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=REGISTRY)


class TestCoverage:
    def test_every_required_scenario_is_present(self) -> None:
        assert set(SCENARIOS) >= REQUIRED_SCENARIOS

    def test_scenarios_are_discoverable(self) -> None:
        assert len(SCENARIOS) >= len(REQUIRED_SCENARIOS)


@pytest.mark.parametrize("name", SCENARIOS)
class TestEveryScenario:
    def test_envelopes_validate_against_the_published_schemas(self, name: str) -> None:
        document = load_raw(name)

        validator("scan-run-start.schema.json").validate(document["start"])
        for batch in document["batches"]:
            validator("observation-batch.schema.json").validate(batch)
        validator("scan-run-completion.schema.json").validate(document["completion"])

    def test_each_observation_validates_against_its_own_schema(self, name: str) -> None:
        document = load_raw(name)

        for batch in document["batches"]:
            for observation in batch["observations"]:
                schema_name = OBSERVATION_SCHEMA[observation["kind"]]
                validator(schema_name).validate(observation)

    def test_the_scenario_parses_through_the_backend_models(self, name: str) -> None:
        scenario = load_scenario(name)

        assert scenario.observations
        assert scenario.start.run_id == scenario.completion.run_id

    def test_every_observation_belongs_to_the_run(self, name: str) -> None:
        scenario = load_scenario(name)

        for observation in scenario.observations:
            assert observation.run_id == scenario.start.run_id

    def test_source_keys_are_unique_within_the_run(self, name: str) -> None:
        scenario = load_scenario(name)
        keys = scenario.source_keys

        assert len(set(keys)) == len(keys)

    def test_every_observation_converts_to_a_domain_object(self, name: str) -> None:
        scenario = load_scenario(name)

        for observation in scenario.observations:
            assert observation.to_domain() is not None

    def test_replaying_the_transcript_is_deterministic(self, name: str) -> None:
        first = load_raw(name)
        second = load_raw(name)

        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_the_expectations_block_names_a_subject_and_a_resource(self, name: str) -> None:
        scenario = load_scenario(name)

        assert Sid(scenario.expectations["subject_sid"])
        assert scenario.expectations["resource"].startswith("\\\\")
        assert "access_expected" in scenario.expectations

    def test_no_scenario_carries_a_derived_conclusion_in_an_observation(self, name: str) -> None:
        document = load_raw(name)
        forbidden = ("effective", "resolved_access", "can_access", "permission_level")

        for batch in document["batches"]:
            for observation in batch["observations"]:
                assert not [field for field in observation if field.startswith(forbidden)]

    def test_the_completion_reconciles_only_what_the_start_declared(self, name: str) -> None:
        scenario = load_scenario(name)

        assert scenario.completion.reconciles(scenario.start) is True


class TestScenarioSpecifics:
    def test_direct_user_grant_names_the_user_on_the_ace(self) -> None:
        scenario = load_scenario("01-direct-user-grant")
        subject = scenario.expectations["subject_sid"]

        assert not scenario.edges
        assert [ace.trustee_sid for ace in scenario.ntfs_aces] == [subject]

    def test_group_grant_needs_exactly_one_edge(self) -> None:
        scenario = load_scenario("02-group-grant")

        assert len(scenario.edges) == 1
        assert scenario.edges[0].member_sid == scenario.expectations["subject_sid"]
        assert scenario.edges[0].group_sid == scenario.ntfs_aces[0].trustee_sid

    def test_nested_group_grant_is_two_hops(self) -> None:
        scenario = load_scenario("03-nested-group-grant")
        expected_path = scenario.expectations["expected_path"]

        by_member = {edge.member_sid: edge.group_sid for edge in scenario.edges}
        walked = [expected_path[0]]
        while walked[-1] in by_member:
            walked.append(by_member[walked[-1]])

        assert walked == expected_path
        assert walked[-1] == scenario.ntfs_aces[0].trustee_sid

    def test_multiple_paths_reach_the_same_group_by_different_routes(self) -> None:
        scenario = load_scenario("04-multiple-membership-paths")
        subject = scenario.expectations["subject_sid"]

        direct_groups = {edge.group_sid for edge in scenario.edges if edge.member_sid == subject}
        assert len(direct_groups) >= 2

        # And the primary-group edge is present, which a member-attribute-only collector
        # would have missed entirely.
        assert any(edge.edge_kind == "primary_group" for edge in scenario.edges)

    def test_the_cyclic_scenario_really_contains_a_cycle(self) -> None:
        scenario = load_scenario("05-cyclic-group-graph")
        pairs = {(edge.member_sid, edge.group_sid) for edge in scenario.edges}

        cycle = [(member, group) for member, group in pairs if (group, member) in pairs]

        assert cycle, "the fixture must contain a mutual membership pair"
        assert scenario.expectations["cycle_expected"] is True

    def test_the_unresolved_scenario_keeps_the_orphan_on_the_acl(self) -> None:
        scenario = load_scenario("06-unresolved-sid")
        orphan = scenario.expectations["orphaned_sid"]

        unresolved = [
            principal
            for principal in scenario.principals
            if principal.principal_kind is PrincipalKind.UNRESOLVED
        ]
        assert [principal.sid for principal in unresolved] == [orphan]
        assert unresolved[0].display_name is None
        assert unresolved[0].last_known_name is not None
        assert orphan in [ace.trustee_sid for ace in scenario.ntfs_aces]

    def test_the_deny_scenario_orders_deny_before_allow(self) -> None:
        scenario = load_scenario("07-deny-candidate")
        by_order = sorted(scenario.ntfs_aces, key=lambda ace: ace.order_index or 0)

        assert by_order[0].ace_type is AceType.DENY
        assert by_order[0].trustee_sid == scenario.expectations["deny_trustee"]
        assert by_order[1].ace_type is AceType.ALLOW
        # The fixture states the outcome; Phase 4 computes it.
        assert scenario.expectations["access_expected"] is False

    def test_the_inherited_scenario_records_where_the_ace_came_from(self) -> None:
        scenario = load_scenario("08-inherited-ace")
        inherited = [ace for ace in scenario.ntfs_aces if ace.source.value == "inherited"]

        assert len(inherited) == 1
        ace = inherited[0]
        assert AceFlag.INHERITED in ace.to_domain().flags
        assert ace.inherited_from == scenario.expectations["inherited_from"]
        assert ace.path == scenario.expectations["resource"]

    def test_the_broken_inheritance_scenario_marks_a_protected_boundary(self) -> None:
        scenario = load_scenario("09-broken-inheritance")
        boundary = scenario.expectations["boundary_resource"]

        protected = [resource for resource in scenario.resources if resource.path == boundary]
        assert len(protected) == 1
        assert protected[0].dacl_protected is True
        assert protected[0].inheritance_enabled is False
        assert protected[0].is_acl_boundary is True
        # Access arrives through a host-scoped local group, not through the parent's ACE.
        assert any(edge.edge_kind == "local_group_member" for edge in scenario.edges)

    def test_smb_more_restrictive_has_a_narrower_share_than_ntfs(self) -> None:
        scenario = load_scenario("10-smb-more-restrictive")

        assert scenario.share_aces[0].permission is not None
        assert scenario.share_aces[0].permission.value == "read"
        assert scenario.ntfs_aces[0].access_mask == int(scenario.expectations["expected_ntfs_mask"])
        assert scenario.expectations["limiting_layer"] == "smb"

    def test_ntfs_more_restrictive_has_a_narrower_ntfs_than_share(self) -> None:
        scenario = load_scenario("11-ntfs-more-restrictive")
        ace = scenario.ntfs_aces[0].to_domain()

        assert scenario.share_aces[0].permission is not None
        assert scenario.share_aces[0].permission.value == "full"
        assert NtfsRight.WRITE_DATA not in ace.rights
        assert NtfsRight.READ_DATA in ace.rights
        assert scenario.expectations["limiting_layer"] == "ntfs"

    def test_the_partial_run_scenario_reconciles_nothing(self) -> None:
        scenario = load_scenario("12-partial-run-no-reconciliation")

        assert scenario.completion.status == "partial"
        assert scenario.completion.error_count == 1
        assert scenario.completion.reconciled_scopes == []
        assert scenario.completion.may_reconcile is False
        assert scenario.expectations["reconciliation_expected"] is False


class TestFixturesAreUsableAsCollectorOutput:
    def test_a_scenario_batch_is_a_valid_post_body(self) -> None:
        document = load_raw("03-nested-group-grant")
        body = document["batches"][0]

        # Exactly what a collector would POST to /api/v1/scan-runs/{run_id}/batches.
        validator("observation-batch.schema.json").validate(body)
        assert body["run_id"] == document["start"]["run_id"]

    def test_declared_ace_counts_match_the_reported_aces(self) -> None:
        for scenario in load_all():
            reported: dict[str, int] = {}
            for ace in scenario.ntfs_aces:
                reported[ace.path.casefold()] = reported.get(ace.path.casefold(), 0) + 1
            for resource in scenario.resources:
                assert resource.ace_count == reported.get(resource.path.casefold(), 0), (
                    f"{scenario.name}: {resource.path} declares {resource.ace_count} ACEs "
                    f"but the run reports {reported.get(resource.path.casefold(), 0)}"
                )

    def test_every_ace_has_a_resource_observation_in_the_same_run(self) -> None:
        for scenario in load_all():
            resource_paths = {resource.path.casefold() for resource in scenario.resources}
            for ace in scenario.ntfs_aces:
                assert ace.path.casefold() in resource_paths, (
                    f"{scenario.name}: {ace.path} has ACEs but no resource observation"
                )

    def test_every_share_ace_has_a_share_observation(self) -> None:
        for scenario in load_all():
            share_keys = {share.source_key for share in scenario.shares}
            for ace in scenario.share_aces:
                expected = f"share|{ace.share_identity_key}"
                assert expected in share_keys, (
                    f"{scenario.name}: {expected} has a share ACE but no share observation"
                )

    def test_fixture_documents_carry_human_readable_intent(self) -> None:
        for scenario in load_all():
            assert scenario.title
            assert len(scenario.description) > 40
            assert isinstance(scenario.expectations, dict)


def _fields_starting_with(observation: dict[str, Any], prefixes: tuple[str, ...]) -> list[str]:
    return [field for field in observation if field.startswith(prefixes)]
