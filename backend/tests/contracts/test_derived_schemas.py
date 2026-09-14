r"""The published derived-response schemas, and the examples that illustrate them.

Three separate things have to agree, and each pair can drift on its own:

* the **models** FastAPI serializes,
* the **schemas** under ``docs/contracts/v1/``, which a client validates against,
* the **examples** beside them, which a developer reads first and trusts most.

The schemas are generated from the models, so the first pair is checked by a staleness
test — the same instrument ``test_openapi_snapshot.py`` uses, for the same reason. The
examples are captured from live responses by ``tests/db/test_explanation_api.py``, and
validated here, so an example cannot claim a shape the server does not send.

The rest of this file is about the contract itself rather than about drift: the vocabulary a
client is told to branch on, and the fields without which a response would be readable but
misleading. Those are asserted directly, because they are promises rather than consequences
of how a model happens to be spelled.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.access_engine import AccessOutcome
from app.api.caching import CONTRACT_VERSION
from app.contracts.derived import EXAMPLE_DIR, SCHEMA_DIR, SCHEMAS, generate

NAMES = sorted(SCHEMAS)


def published(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return document


def example(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (EXAMPLE_DIR / name.replace(".schema.json", ".json")).read_text(encoding="utf-8")
    )
    return payload


def properties(name: str) -> dict[str, Any]:
    found: dict[str, Any] = published(name)["properties"]
    return found


def definition(name: str, model: str) -> dict[str, Any]:
    found: dict[str, Any] = published(name)["$defs"][model]
    return found


class TestThePublishedSchemasAreCurrent:
    @pytest.mark.parametrize("name", NAMES)
    def test_the_file_matches_the_model(self, name: str) -> None:
        assert published(name) == generate(name), (
            f"docs/contracts/v1/{name} is out of date. "
            "Regenerate with: python -m app.contracts.derived"
        )

    @pytest.mark.parametrize("name", NAMES)
    def test_it_is_a_valid_2020_12_schema(self, name: str) -> None:
        Draft202012Validator.check_schema(published(name))

    @pytest.mark.parametrize("name", NAMES)
    def test_it_declares_an_id_and_a_dialect(self, name: str) -> None:
        """A standalone schema needs both, or it can be neither referenced nor validated."""
        document = published(name)

        assert document["$id"].endswith(name)
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_the_directory_and_the_mapping_agree(self) -> None:
        """A schema published and not listed would never be regenerated, and would rot."""
        assert {path.name for path in SCHEMA_DIR.glob("*.schema.json")} == set(SCHEMAS)

    def test_they_do_not_sit_among_the_collector_payloads(self) -> None:
        """Separate directories, because ``v1/*.schema.json`` means "what a collector sends".

        A response schema in that glob is registered as a collector payload by the Phase 0B
        schema tests and checked against the observation kinds, which it is not one of.
        """
        assert SCHEMA_DIR.name == "derived"
        assert SCHEMA_DIR.parent.name == "v1"
        assert not list(SCHEMA_DIR.parent.glob("access-*.schema.json"))


class TestTheExamplesAreReal:
    """Captured from live responses, so they cannot describe a response nobody sends."""

    @pytest.mark.parametrize("name", NAMES)
    def test_the_example_validates(self, name: str) -> None:
        Draft202012Validator(published(name)).validate(example(name))

    @pytest.mark.parametrize("name", NAMES)
    def test_the_example_declares_the_current_contract_version(self, name: str) -> None:
        assert example(name)["schema_version"] == CONTRACT_VERSION

    @pytest.mark.parametrize("name", NAMES)
    def test_the_example_carries_a_basis(self, name: str) -> None:
        """An example without one would teach a client to ignore the field."""
        basis = example(name)["basis"]

        assert basis["token"]
        assert basis["is_empty"] is False


class TestTheVersion:
    @pytest.mark.parametrize("name", NAMES)
    def test_the_body_pins_the_version_rather_than_defaulting_to_it(self, name: str) -> None:
        """A ``const`` is checkable by a client; a ``default`` is a suggestion to a generator."""
        assert properties(name)["schema_version"]["const"] == CONTRACT_VERSION

    @pytest.mark.parametrize("name", NAMES)
    def test_the_document_records_the_version_it_was_generated_for(self, name: str) -> None:
        assert published(name)["x-contract-version"] == CONTRACT_VERSION

    def test_the_version_is_a_dotted_minor(self) -> None:
        """Additive changes bump the minor, so there has to be one to bump."""
        major, _, minor = CONTRACT_VERSION.partition(".")

        assert major.isdigit() and minor.isdigit()


class TestTheVocabularyAClientBranchesOn:
    """These values appear in code a client writes. Changing one is a contract break."""

    def test_the_four_outcomes_are_published_exactly(self) -> None:
        r"""The acceptance criterion, expressed as a schema assertion.

        "Denied", "no grant" and "insufficient data" must be distinguishable *by a client*,
        which means they must be distinguishable in the document the client validates
        against — not merely in a docstring on the server.
        """
        assert definition("access-explanation.schema.json", "AccessOutcome")["enum"] == [
            outcome.value for outcome in AccessOutcome
        ]
        assert set(AccessOutcome) == {
            AccessOutcome.GRANTED,
            AccessOutcome.DENIED,
            AccessOutcome.NO_GRANT,
            AccessOutcome.INDETERMINATE,
        }

    @pytest.mark.parametrize("name", NAMES)
    def test_every_response_carries_a_verdict_or_carries_one_per_row(self, name: str) -> None:
        """No derived response may report access without saying which of the four it is."""
        document = published(name)
        top_level = set(document["properties"])

        assert "verdict" in top_level or "VerdictView" in document["$defs"], (
            f"{name} can report an empty result with no way to say what the emptiness means"
        )

    def test_the_verdict_names_its_own_reliability(self) -> None:
        """`conclusive` is the field that forbids reading `no_grant` as "no access"."""
        verdict = definition("access-explanation.schema.json", "VerdictView")["properties"]

        assert {"outcome", "reason", "certainty", "conclusive", "denials"} <= set(verdict)
        assert {"may_overstate", "may_understate"} <= set(verdict)

    def test_certainty_is_still_its_own_field(self) -> None:
        """Folding it into the outcome would make "granted, but possibly narrower" vanish."""
        verdict = definition("access-explanation.schema.json", "VerdictView")
        certainty = verdict["properties"]["certainty"]

        assert certainty["$ref"].endswith("AccessCertainty")
        assert definition("access-explanation.schema.json", "AccessCertainty")["enum"] == [
            "certain",
            "at_most",
            "at_least",
            "uncertain",
        ]


class TestTheFieldsWithoutWhichAResponseMisleads:
    def test_the_explanation_says_whether_it_is_whole(self) -> None:
        assert {"complete", "truncation", "limits"} <= set(
            properties("access-explanation.schema.json")
        )

    def test_the_explanation_carries_both_layers(self) -> None:
        """SMB and NTFS components, which is what makes `limiting_layer` actionable."""
        effective = definition("access-explanation.schema.json", "EffectiveAccessView")

        assert {"ntfs", "share", "limiting_layer", "rights"} <= set(effective["properties"])

    def test_an_applied_entry_names_its_layer(self) -> None:
        """A flat list drawn from both ACLs is ambiguous without it."""
        applied = definition("access-explanation.schema.json", "AppliedAceView")

        assert "layer" in applied["properties"]
        assert "layer" in applied["required"]

    def test_a_path_distinguishes_relation_from_effect(self) -> None:
        """A Deny that does not bite is unrepresentable in a single five-valued enum."""
        path = definition("access-explanation.schema.json", "CausalPathView")["properties"]

        assert "relation" in path and "effect" in path

    def test_the_paged_response_says_whether_more_exists(self) -> None:
        page = definition("access-paths.schema.json", "PageInfo")["properties"]

        assert {"has_more", "next_cursor", "limit"} <= set(page)

    def test_the_impact_rows_link_rather_than_inline_their_derivation(self) -> None:
        """Looping the explanation over an estate is the thing this must not do."""
        row = definition("resource-impact.schema.json", "ResourceImpactView")["properties"]

        assert "explain" in row
        assert "paths" not in row and "removal_targets" not in row

    def test_the_impact_reports_how_many_principals_are_affected(self) -> None:
        membership = definition("resource-impact.schema.json", "MembershipImpactView")

        assert {"effective_members", "non_group_members", "complete"} <= set(
            membership["properties"]
        )


class TestStableIdentifiersRatherThanDisplayNames:
    """An acceptance criterion: a relationship key must survive a rename."""

    def test_graph_edges_join_on_ids(self) -> None:
        edge = definition("access-explanation.schema.json", "ExplanationEdgeView")["properties"]

        assert {"id", "source", "target"} <= set(edge)
        assert all(edge[field]["type"] == "string" for field in ("id", "source", "target"))

    def test_a_path_references_nodes_and_edges_by_id(self) -> None:
        path = definition("access-explanation.schema.json", "CausalPathView")["properties"]

        assert path["nodes"]["items"]["type"] == "string"
        assert path["edges"]["items"]["type"] == "string"

    def test_a_principal_is_keyed_by_sid_not_by_name(self) -> None:
        summary = definition("access-explanation.schema.json", "PrincipalSummary")["properties"]

        assert "key" in summary and "sid" in summary
        assert "display_name" in summary, "the name is carried, but beside the key"

    def test_the_example_graph_resolves_every_reference(self) -> None:
        """The strongest check that ids are usable: follow them in a real payload."""
        payload = example("access-explanation.schema.json")
        nodes = {node["id"] for node in payload["graph"]["nodes"]}
        edges = {edge["id"] for edge in payload["graph"]["edges"]}

        for path in payload["paths"]:
            assert set(path["nodes"]) <= nodes, "a path references a node not in the graph"
            assert set(path["edges"]) <= edges, "a path references an edge not in the graph"
        for edge in payload["graph"]["edges"]:
            assert {edge["source"], edge["target"]} <= nodes
