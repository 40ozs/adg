"""The collector-output validator has to be right about both halves of its job.

Two things are being tested. First, that it agrees with the API: anything it calls an
error must actually be rejected by the contract models, and anything the committed fixtures
contain must not be. A validator that cries wolf is worse than none, because a collector
author will start ignoring it.

Second, that the graph checks fire on the hazards they were written for. Each one is given
a minimal transcript containing exactly that hazard, so a check cannot pass by accident of
some other finding being present.
"""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

import pytest

from app.validation import Severity, validate_documents
from app.validation.__main__ import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from app.validation.collector_output import (
    Report,
    _check_unsupported_kinds,
    _Parsed,
    iter_codes,
    load_documents,
    validate_paths,
)
from tests.fixtures import (
    AD_GRAPH_DIR,
    SCENARIO_DIR,
    ad_graph_names,
    load_ad_graph_raw,
    scenario_names,
)
from tests.fixtures.build_ad_graph import CORP
from tests.support.ingest import ad_only

BUILTIN = "S-1-5-32-544"


def codes(documents: list[dict[str, Any]]) -> set[str]:
    return {finding.code for finding in validate_documents(documents).findings}


def transcript(name: str) -> dict[str, Any]:
    return load_ad_graph_raw(name)


def minimal(
    observations: list[dict[str, Any]], run_id: str = "00000000-0000-4000-8000-00000000d001"
) -> dict[str, Any]:
    """A complete, otherwise-clean transcript carrying exactly the observations given."""
    for observation in observations:
        observation["run_id"] = run_id
    return {
        "start": {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": {
                "collector": "active_directory",
                "collector_host": "COLLECTOR01",
                "method": "System.DirectoryServices.Protocols",
            },
            "started_at": "2026-09-14T09:00:00Z",
            "scopes": [{"kind": "domain", "key": CORP}],
            "incremental": False,
        },
        "batches": [
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "batch_id": "00000000-0000-4000-8000-1000000000d1",
                "sequence": 1,
                "is_final": True,
                "observations": observations,
            }
        ],
        "completion": {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "succeeded",
            "completed_at": "2026-09-14T09:10:00Z",
            "batch_count": 1,
            "observation_count": len(observations),
            "error_count": 0,
            "errors": [],
            "reconciled_scopes": [],
        },
    }


def _two_batches(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> dict[str, Any]:
    """One run carrying two batches, with the counts kept honest."""
    document = minimal(first + second)
    run_id = document["start"]["run_id"]
    template = document["batches"][0]
    document["batches"] = [
        {**copy.deepcopy(template), "sequence": 1, "is_final": False, "observations": first},
        {
            **copy.deepcopy(template),
            "batch_id": "00000000-0000-4000-8000-1000000000d2",
            "sequence": 2,
            "is_final": True,
            "observations": second,
        },
    ]
    for batch in document["batches"]:
        batch["run_id"] = run_id
        for observation in batch["observations"]:
            observation["run_id"] = run_id
    document["completion"]["batch_count"] = 2
    document["completion"]["observation_count"] = len(first) + len(second)
    return document


def principal(sid: str, kind: str, **fields: Any) -> dict[str, Any]:
    host = fields.pop("host_key", None)
    source_key = f"principal|{host.casefold()}|{sid}" if host else f"principal|{sid}"
    payload = {
        "schema_version": "1.0",
        "kind": "principal",
        "run_id": "placeholder",
        "observed_at": "2026-09-14T09:00:01Z",
        "source_key": source_key,
        "sid": sid,
        "principal_kind": kind,
        **fields,
    }
    if host:
        payload["host_key"] = host
    return payload


def edge(group: str, member: str, **fields: Any) -> dict[str, Any]:
    kind = fields.pop("edge_kind", "directory_group_member")
    host = fields.pop("host_key", None)
    group_key = f"{host.casefold()}|{group}" if host else group
    member_key = (
        f"{host.casefold()}|{member}" if host and member.startswith("S-1-5-32-") else member
    )
    payload = {
        "schema_version": "1.0",
        "kind": "membership_edge",
        "run_id": "placeholder",
        "observed_at": "2026-09-14T09:00:02Z",
        "source_key": f"edge|{group_key}->{member_key}|{kind}",
        "group_sid": group,
        "member_sid": member,
        "edge_kind": kind,
        **fields,
    }
    if host:
        payload["host_key"] = host
    return payload


def corp(rid: int) -> str:
    return f"{CORP}-{rid}"


class TestTheCommittedCorpusIsClean:
    @pytest.mark.parametrize("name", ad_graph_names())
    def test_no_adversarial_fixture_would_be_rejected_by_the_api(self, name: str) -> None:
        report = validate_documents([transcript(name)])

        assert report.ok, [finding.message for finding in report.errors]

    @pytest.mark.parametrize("name", scenario_names())
    def test_the_ad_half_of_every_canonical_scenario_is_accepted(self, name: str) -> None:
        document = ad_only(json.loads((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8")))
        if not document["batches"]:
            pytest.skip(f"{name} carries no AD observations")

        report = validate_documents([document])

        assert report.ok, [finding.message for finding in report.errors]

    def test_the_whole_adversarial_directory_validates_in_one_pass(self) -> None:
        report = validate_paths([AD_GRAPH_DIR])

        assert report.ok
        assert report.documents == len(ad_graph_names())
        assert report.observations > 600
        assert len(report.runs) == len(ad_graph_names())


class TestEnvelopeChecks:
    def test_a_payload_the_models_reject_is_an_error_naming_the_field(self) -> None:
        document = minimal([principal("not-a-sid", "user")])

        report = validate_documents([document])

        assert not report.ok
        assert any(finding.code == "invalid_batch" for finding in report.errors)
        assert any("sid" in finding.message for finding in report.errors)

    def test_every_problem_is_reported_rather_than_only_the_first(self) -> None:
        # The API stops at the first rejection; a collector author fixing one field at a
        # time through a network round trip is the slowest possible loop.
        document = minimal([principal("not-a-sid", "user"), principal("also-bad", "user")])

        assert len(validate_documents([document]).errors) >= 2

    def test_an_observation_count_that_overstates_coverage_is_an_error(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        document["completion"]["observation_count"] = 99

        assert "observation_count_mismatch" in codes([document])

    def test_a_batch_count_that_overstates_coverage_is_an_error(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        document["completion"]["batch_count"] = 4

        assert "batch_count_mismatch" in codes([document])

    def test_a_reused_batch_id_is_an_error(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        document["batches"].append(copy.deepcopy(document["batches"][0]))
        document["batches"][1]["sequence"] = 2
        document["completion"]["batch_count"] = 2
        document["completion"]["observation_count"] = 2

        assert "duplicate_batch_id" in codes([document])

    def test_two_final_batches_is_an_error(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        second = copy.deepcopy(document["batches"][0])
        second["batch_id"] = "00000000-0000-4000-8000-1000000000d2"
        second["sequence"] = 2
        second["observations"][0]["source_key"] = f"principal|{corp(2)}"
        second["observations"][0]["sid"] = corp(2)
        document["batches"].append(second)
        document["completion"]["batch_count"] = 2
        document["completion"]["observation_count"] = 2

        assert "multiple_final_batches" in codes([document])

    def test_a_missing_final_batch_is_a_warning(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        document["batches"][0]["is_final"] = False

        assert "no_final_batch" in codes([document])

    def test_a_sequence_gap_is_a_warning(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        document["batches"][0]["sequence"] = 7

        assert "batch_sequence_gap" in codes([document])

    def test_batches_with_no_start_are_a_warning(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        del document["start"]

        assert "batches_without_a_start" in codes([document])

    def test_a_run_that_never_completed_is_a_warning(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        del document["completion"]

        assert "run_never_completed" in codes([document])

    def test_a_transcript_of_storable_kinds_raises_nothing(self) -> None:
        # Every contract v1 kind is stored as of Phase 3A, so a published scenario - which
        # carries all seven - must validate clean. Until this phase it did not, and the
        # finding it raised named the very kinds it had just listed as supported.
        document = json.loads((SCENARIO_DIR / "01-direct-user-grant.json").read_text("utf-8"))

        assert "unstorable_observation_kind" not in codes([document])

    def test_an_observation_kind_ingestion_cannot_store_is_an_error(self) -> None:
        # Raised by hand: nothing in contract v1 reaches this path any more, and the check
        # has to keep working for whichever kind a later contract adds ahead of its
        # ingestion support.
        report = Report()
        parsed = _Parsed()
        parsed.other_kinds["registry_key"] = 3
        _check_unsupported_kinds(parsed, report)

        assert [finding.code for finding in report.findings] == ["unstorable_observation_kind"]
        assert "registry_key" in report.findings[0].message

    def test_a_run_reconciling_an_undeclared_scope_is_an_error(self) -> None:
        document = minimal([principal(corp(1), "user", display_name="A")])
        document["completion"]["reconciled_scopes"] = [{"kind": "domain", "key": "s-1-5-21-9-9-9"}]

        assert "reconciled_an_undeclared_scope" in codes([document])

    def test_two_different_starts_for_one_run_id_is_an_error(self) -> None:
        first = minimal([principal(corp(1), "user", display_name="A")])
        second = copy.deepcopy(first)
        second["start"]["source"]["collector_host"] = "COLLECTOR02"

        assert "conflicting_start" in codes([first, second])


class TestGraphChecks:
    def test_an_unscoped_builtin_group_is_flagged(self) -> None:
        # The hazard: S-1-5-32-544 reported as a domain group has no host scope, so a
        # second domain collected the same way merges into this node.
        document = minimal(
            [
                principal(BUILTIN, "domain_group", display_name="Administrators"),
                principal(corp(500), "user", display_name="Owen"),
                edge(BUILTIN, corp(500), member_kind="user"),
            ]
        )

        report = validate_documents([document])
        finding = next(f for f in report.findings if f.code == "unscoped_well_known_group")

        assert finding.severity is Severity.WARNING
        assert finding.subject == BUILTIN
        assert "merges into this one node" in finding.remedy

    def test_a_host_scoped_builtin_group_is_not_flagged(self) -> None:
        document = minimal(
            [
                principal(BUILTIN, "local_group", host_key="FS10", display_name="Administrators"),
                principal(corp(500), "user", display_name="Owen"),
                edge(BUILTIN, corp(500), edge_kind="local_group_member", host_key="FS10"),
            ]
        )

        assert "unscoped_well_known_group" not in codes([document])

    def test_a_membership_cycle_is_flagged_with_the_loop_to_break(self) -> None:
        document = minimal(
            [
                edge(corp(10), corp(11), member_kind="domain_group"),
                edge(corp(11), corp(10), member_kind="domain_group"),
            ]
        )

        report = validate_documents([document])
        finding = next(f for f in report.findings if f.code == "membership_cycle")

        assert finding.severity is Severity.WARNING
        assert corp(10) in finding.remedy and corp(11) in finding.remedy
        assert "→" in finding.remedy

    def test_an_acyclic_graph_reports_no_cycle(self) -> None:
        document = minimal([edge(corp(10), corp(11), member_kind="domain_group")])

        assert "membership_cycle" not in codes([document])

    def test_one_sid_reported_as_two_kinds_is_flagged(self) -> None:
        # It takes two batches to express, because one batch may not repeat a source_key
        # and the source_key of a non-local-group principal is a function of its SID alone.
        # That is the hazard restated: the two descriptions share one storage key.
        document = _two_batches(
            [principal(corp(20), "user", display_name="A")],
            [principal(corp(20), "computer", display_name="A")],
        )

        assert "one_sid_several_kinds" in codes([document])

    def test_an_edge_that_disagrees_with_the_members_own_observation_is_flagged(self) -> None:
        document = minimal(
            [
                principal(corp(30), "user", display_name="A"),
                edge(corp(31), corp(30), member_kind="domain_group"),
            ]
        )

        report = validate_documents([document])
        finding = next(f for f in report.findings if f.code == "member_kind_disagrees")

        assert finding.subject == corp(30)

    def test_an_edge_that_agrees_is_not_flagged(self) -> None:
        document = minimal(
            [
                principal(corp(30), "user", display_name="A"),
                edge(corp(31), corp(30), member_kind="user"),
            ]
        )

        assert "member_kind_disagrees" not in codes([document])

    def test_members_nothing_describes_are_reported_without_being_an_error(self) -> None:
        document = minimal([edge(corp(40), corp(41))])

        report = validate_documents([document])
        finding = next(f for f in report.findings if f.code == "members_without_a_description")

        assert report.ok, "an undescribed member is a finding ADG keeps, not a rejection"
        assert finding.severity is Severity.INFO

    def test_two_principals_sharing_a_name_are_reported_as_information(self) -> None:
        document = minimal(
            [
                principal(corp(50), "user", display_name="Jordan Rivera"),
                principal(corp(51), "user", display_name="Jordan Rivera"),
            ]
        )

        report = validate_documents([document])
        finding = next(f for f in report.findings if f.code == "one_name_several_principals")

        assert finding.severity is Severity.INFO
        assert report.ok

    def test_one_run_describing_a_principal_two_different_ways_is_flagged(self) -> None:
        second = principal(corp(60), "user", display_name="B")
        second["observed_at"] = "2026-09-14T09:00:05Z"
        document = _two_batches([principal(corp(60), "user", display_name="A")], [second])

        assert "conflicting_principal_observations" in codes([document])

    def test_two_runs_describing_a_principal_differently_is_a_rename_not_a_conflict(self) -> None:
        before = transcript("a04a-rename-before")
        after = transcript("a04b-rename-after")

        assert "conflicting_principal_observations" not in codes([before, after])

    def test_deep_nesting_is_reported(self) -> None:
        assert "deep_nesting" in codes([transcript("a01-deep-nesting")])

    def test_nesting_past_the_default_depth_limit_is_a_warning(self) -> None:
        observations = [
            edge(corp(1000 + level + 1), corp(1000 + level), member_kind="domain_group")
            for level in range(40)
        ]

        report = validate_documents([minimal(observations)])
        finding = next(
            f for f in report.findings if f.code == "nesting_deeper_than_the_default_limit"
        )

        assert finding.severity is Severity.WARNING

    def test_a_clean_transcript_produces_nothing_at_all(self) -> None:
        document = minimal(
            [
                principal(corp(70), "domain_group", display_name="Finance-RW"),
                principal(corp(71), "user", display_name="Ines"),
                edge(corp(70), corp(71), member_kind="user"),
            ]
        )

        report = validate_documents([document])

        assert report.findings == []
        assert report.ok


class TestLoading:
    def test_malformed_json_is_an_error_that_names_the_file(self, tmp_path: pathlib.Path) -> None:
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")

        _, findings = load_documents([broken])

        assert [finding.code for finding in findings] == ["malformed_json"]
        assert "broken.json" in findings[0].message

    def test_a_directory_is_expanded(self) -> None:
        documents, findings = load_documents([AD_GRAPH_DIR])

        assert findings == []
        assert len(documents) == len(ad_graph_names())

    def test_an_array_of_envelopes_is_accepted(self, tmp_path: pathlib.Path) -> None:
        document = minimal([principal(corp(80), "user", display_name="A")])
        bundle = tmp_path / "bundle.json"
        bundle.write_text(
            json.dumps([document["start"], *document["batches"], document["completion"]]),
            encoding="utf-8",
        )

        report = validate_paths([bundle])

        assert report.ok
        assert report.observations == 1

    def test_something_that_is_not_an_envelope_is_an_error(self, tmp_path: pathlib.Path) -> None:
        stray = tmp_path / "stray.json"
        stray.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

        assert "unrecognized_document" in {f.code for f in validate_paths([stray]).findings}

    def test_a_json_scalar_is_an_error(self, tmp_path: pathlib.Path) -> None:
        stray = tmp_path / "scalar.json"
        stray.write_text("42", encoding="utf-8")

        assert "unexpected_shape" in {f.code for f in validate_paths([stray]).findings}


class TestCommandLine:
    def test_a_clean_directory_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([str(AD_GRAPH_DIR)]) == EXIT_OK
        assert "ADG collector-output validation" in capsys.readouterr().out

    def test_strict_fails_on_the_warnings_the_fixtures_carry(self) -> None:
        # The adversarial set deliberately contains cycles and an unscoped BUILTIN group.
        assert main([str(AD_GRAPH_DIR), "--strict"]) == EXIT_FINDINGS

    def test_errors_exit_one(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(minimal([principal("nope", "user")])), encoding="utf-8")

        assert main([str(bad)]) == EXIT_FINDINGS
        assert "ERROR" in capsys.readouterr().out

    def test_a_missing_path_exits_two(self, tmp_path: pathlib.Path) -> None:
        assert main([str(tmp_path / "absent.json")]) == EXIT_USAGE

    def test_an_empty_directory_exits_two(self, tmp_path: pathlib.Path) -> None:
        assert main([str(tmp_path)]) == EXIT_USAGE

    def test_json_output_is_parseable_and_complete(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main([str(AD_GRAPH_DIR), "--json"])

        report = json.loads(capsys.readouterr().out)
        assert report["ok"] is True
        assert report["examined"]["documents"] == len(ad_graph_names())
        assert {"error", "warning", "info"} == set(report["counts"])
        for finding in report["findings"]:
            assert finding["remedy"], f"{finding['code']} has no remedy"

    def test_quiet_suppresses_the_header(self, capsys: pytest.CaptureFixture[str]) -> None:
        main([str(AD_GRAPH_DIR), "--quiet"])

        assert "ADG collector-output validation" not in capsys.readouterr().out


class TestEveryFindingIsUsable:
    def test_every_declared_code_is_documented_in_the_architecture_note(self) -> None:
        document = (
            pathlib.Path(__file__).resolve().parents[3]
            / "docs"
            / "architecture"
            / "ad-graph-validation.md"
        ).read_text(encoding="utf-8")

        for code in iter_codes():
            assert f"`{code}`" in document, f"{code} is emitted but not documented"

    def test_every_finding_the_corpus_produces_carries_a_remedy(self) -> None:
        report = validate_paths([AD_GRAPH_DIR])

        for finding in report.findings:
            assert finding.remedy
            assert finding.message
            assert len(finding.remedy) > 40, f"{finding.code}: remedy is too terse to act on"
