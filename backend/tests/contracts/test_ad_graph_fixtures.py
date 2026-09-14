"""The adversarial AD-graph transcripts must be valid contract v1, and must stay generated.

Two properties, and the second is the one that keeps this set maintainable. A fixture with
five hundred members cannot be reviewed by reading it, so the committed JSON is compared
against what `tests/fixtures/build_ad_graph.py` produces: an edit to either side that the
other does not match fails here rather than silently teaching a later phase something
untrue.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.contracts.v1.common import ObservationKind
from tests.contracts.test_json_schemas import validator_for
from tests.fixtures import (
    AD_GRAPH_DIR,
    Scenario,
    ad_graph_names,
    load_ad_graph,
    load_ad_graph_raw,
)
from tests.fixtures.build_ad_graph import build_all, render

NAMES = ad_graph_names()

AD_KINDS = frozenset({ObservationKind.PRINCIPAL.value, ObservationKind.MEMBERSHIP_EDGE.value})

SCHEMA_FOR_KIND = {
    ObservationKind.PRINCIPAL.value: "principal-observation.schema.json",
    ObservationKind.MEMBERSHIP_EDGE.value: "membership-observation.schema.json",
}


def test_the_set_is_not_empty() -> None:
    assert NAMES, f"No adversarial fixtures found under {AD_GRAPH_DIR}"


@pytest.mark.parametrize("name", NAMES)
class TestCommittedFilesAreGenerated:
    def test_the_committed_file_matches_the_generator(self, name: str) -> None:
        generated = build_all()
        assert name in generated, (
            f"{name}.json is committed but no builder produces it. Either add a builder or "
            "delete the file: an ungenerated fixture cannot be checked for consistency."
        )
        expected = render(generated[name])
        actual = (AD_GRAPH_DIR / f"{name}.json").read_text(encoding="utf-8")

        assert actual == expected, (
            f"{name}.json differs from tests/fixtures/build_ad_graph.py. Regenerate with "
            "`python -m tests.fixtures.build_ad_graph` and review the diff."
        )


@pytest.mark.parametrize("name", NAMES)
class TestSchemaValidity:
    def test_the_start_envelope_validates(self, name: str) -> None:
        validator_for("scan-run-start.schema.json").validate(load_ad_graph_raw(name)["start"])

    def test_every_batch_validates(self, name: str) -> None:
        validator = validator_for("observation-batch.schema.json")
        for batch in load_ad_graph_raw(name)["batches"]:
            validator.validate(batch)

    def test_every_observation_validates_against_its_own_schema(self, name: str) -> None:
        document = load_ad_graph_raw(name)
        for batch in document["batches"]:
            for observation in batch["observations"]:
                kind = observation["kind"]
                assert kind in SCHEMA_FOR_KIND, (
                    f"{name} carries a {kind!r} observation. This set is deliberately AD "
                    "only, so that it replays through the Phase 1 endpoints verbatim."
                )
                validator_for(SCHEMA_FOR_KIND[kind]).validate(observation)

    def test_the_completion_envelope_validates(self, name: str) -> None:
        validator_for("scan-run-completion.schema.json").validate(
            load_ad_graph_raw(name)["completion"]
        )

    def test_the_models_parse_it(self, name: str) -> None:
        scenario = load_ad_graph(name)

        assert scenario.name == name
        assert scenario.batches
        assert scenario.observations


@pytest.mark.parametrize("name", NAMES)
class TestTranscriptCoherence:
    def test_every_observation_belongs_to_the_run(self, name: str) -> None:
        scenario = load_ad_graph(name)
        run_id = scenario.start.run_id

        assert {batch.run_id for batch in scenario.batches} == {run_id}
        assert {item.run_id for item in scenario.observations} == {run_id}
        assert scenario.completion.run_id == run_id

    def test_source_keys_are_unique_across_the_whole_run(self, name: str) -> None:
        # The envelope only enforces this within one batch. Across batches a duplicate is
        # legal but pointless, and in a generated fixture it means the generator collided
        # two objects — which would make the fixture assert less than it appears to.
        keys = load_ad_graph(name).source_keys

        assert len(set(keys)) == len(keys), f"{name} repeats a source_key across batches"

    def test_the_completion_counts_what_was_sent(self, name: str) -> None:
        scenario = load_ad_graph(name)

        assert scenario.completion.batch_count == len(scenario.batches)
        assert scenario.completion.observation_count == len(scenario.observations)

    def test_batches_are_sequential_and_end_once(self, name: str) -> None:
        scenario = load_ad_graph(name)

        assert [batch.sequence for batch in scenario.batches] == list(
            range(1, len(scenario.batches) + 1)
        )
        assert [batch.is_final for batch in scenario.batches].count(True) == 1
        assert scenario.batches[-1].is_final

    def test_no_edge_is_a_self_edge(self, name: str) -> None:
        for edge in load_ad_graph(name).edges:
            domain_edge = edge.to_domain()
            assert domain_edge.group_key != domain_edge.member_key

    def test_expectations_are_present_and_explained(self, name: str) -> None:
        expectations: dict[str, Any] = load_ad_graph(name).expectations

        assert expectations, f"{name} carries no expectations block"
        assert expectations.get("note"), (
            f"{name} must say in `note` what a reader is supposed to conclude from it; a "
            "fixture nobody can interpret is a fixture nobody will maintain."
        )


def _keys(scenario: Scenario) -> set[str]:
    return {item.to_domain().identity_key for item in scenario.principals}


@pytest.mark.parametrize("name", NAMES)
def test_expected_keys_refer_to_something_in_the_transcript(name: str) -> None:
    """Every key named in `expectations` must be a principal or an edge endpoint here.

    A typo in an expectation would otherwise produce a test that asserts a key is absent
    from an answer — and passes, forever, for the wrong reason.
    """
    scenario = load_ad_graph(name)
    known = _keys(scenario)
    for edge in scenario.edges:
        domain_edge = edge.to_domain()
        known |= {domain_edge.group_key, domain_edge.member_key, domain_edge.identity_key}

    for field, value in scenario.expectations.items():
        if field in {"note", "known_hazard"}:
            continue
        for candidate in _flatten(value):
            # A bare SID, a host-scoped key, or an edge key — anything that names a node.
            if isinstance(candidate, str) and "S-1-" in candidate:
                assert candidate in known, f"{name}.expectations.{field} names unknown {candidate}"


def _flatten(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [item for entry in value for item in _flatten(entry)]
    return [value]


def test_the_generator_is_deterministic() -> None:
    """Two builds must be byte-identical, or `--check` would fail at random."""
    first = {name: render(document) for name, document in build_all().items()}
    second = {name: render(document) for name, document in build_all().items()}

    assert first == second


def test_every_generated_fixture_is_committed() -> None:
    generated = set(build_all())
    committed = set(NAMES)

    assert generated == committed, (
        f"only generated {sorted(generated - committed)}; only committed "
        f"{sorted(committed - generated)}"
    )


def test_the_committed_json_is_indented_and_newline_terminated() -> None:
    """These files are read by people and diffed by reviewers; compact JSON is not."""
    for name in NAMES:
        text = (AD_GRAPH_DIR / f"{name}.json").read_text(encoding="utf-8")

        assert text.endswith("\n")
        assert json.loads(text)
        assert "\n  " in text
