"""The dataset contract: Demo Mode consumes the existing generators and never rewrites them.

Both adapters are checked against their generators directly -- their transcripts must be the
generator's transcripts, byte for byte -- because an adapter that "tidied up" a run id or a
timestamp would be a second source of synthetic truth. Genesee Press is the canonical
dataset; its canon tests run unconditionally, because it ships in this build.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import types
from collections.abc import Iterator, Sequence

import pytest

from app.demo import build_estate, build_transcripts
from app.demo import estate as reference_estate
from app.demo_mode import datasets
from app.demo_mode.datasets import (
    CANONICAL_DATASET,
    DATASETS,
    REFERENCE_DATASET,
    CanonicalProbe,
    DatasetUnavailable,
    DemoDatasetMetadata,
    ScanGeneration,
    SyntheticDataset,
    baseline_generations,
    dataset_scenarios,
    load_dataset,
)
from app.demo_mode.reference import ReferenceEstateDataset


class TestTheReferenceAdapter:
    def test_it_satisfies_the_contract(self) -> None:
        assert isinstance(load_dataset(REFERENCE_DATASET), SyntheticDataset)

    def test_its_transcripts_are_the_generators_byte_for_byte(self) -> None:
        adapted = load_dataset(REFERENCE_DATASET).generations()[0].transcripts
        direct = build_transcripts(build_estate("standard"))

        assert [json.dumps(t.as_document(), sort_keys=True) for t in adapted] == [
            json.dumps(t.as_document(), sort_keys=True) for t in direct
        ]

    def test_two_loads_are_identical(self) -> None:
        first = ReferenceEstateDataset().generations()[0].transcripts
        second = ReferenceEstateDataset().generations()[0].transcripts

        assert [t.as_document() for t in first] == [t.as_document() for t in second]

    def test_its_metadata_comes_from_the_estate(self) -> None:
        metadata = load_dataset(REFERENCE_DATASET).metadata

        assert metadata.domain == reference_estate.DOMAIN_DNS
        assert metadata.snapshot_date == reference_estate.EPOCH.date()
        assert metadata.synthetic is True

    def test_its_probes_name_the_estates_own_identifiers(self) -> None:
        """Every SID a probe names is a principal the estate built -- or a well-known SID."""
        estate = build_estate("standard")
        known = {principal.sid for principal in estate.principals} | {reference_estate.EVERYONE}
        named = {
            value
            for probe in load_dataset(REFERENCE_DATASET).probes()
            for name, value in probe.key.items()
            if name.endswith("sid")
        }
        assert named <= known

    def test_run_ids_are_deterministic(self) -> None:
        generation = load_dataset(REFERENCE_DATASET).generations()[0]
        assert generation.run_ids == ReferenceEstateDataset().generations()[0].run_ids


class TestTheRegistry:
    def test_the_canonical_dataset_is_genesee_press_and_ships_in_this_build(self) -> None:
        entry = DATASETS[CANONICAL_DATASET]
        assert CANONICAL_DATASET == "genesee-press-v1"
        assert entry.loader == "app.demo.genesee_press:synthetic_dataset"
        assert entry.dependency is None
        assert isinstance(load_dataset(CANONICAL_DATASET), SyntheticDataset)

    def test_the_canonical_dataset_is_the_default(self) -> None:
        from app.config import build_settings

        settings = build_settings(
            runtime_mode="demo", database_url="postgresql+psycopg://a:b@h/adg_demo"
        )
        assert settings.resolved_demo_dataset == CANONICAL_DATASET

    def test_an_absent_generator_is_refused_and_never_substituted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A registered dataset whose module is missing is a refusal naming what is missing --
        never the reference estate, or any other, in its place."""
        monkeypatch.setitem(
            DATASETS,
            "absent-v1",
            datasets.DatasetEntry(
                "absent-v1",
                "app.demo.no_such_generator:synthetic_dataset",
                "fixture",
                dependency="The absent generator must merge first.",
            ),
        )
        with pytest.raises(DatasetUnavailable) as refused:
            load_dataset("absent-v1")

        message = str(refused.value)
        assert "not present in this build" in message
        assert "will not substitute" in message
        assert "The absent generator must merge first." in message
        assert REFERENCE_DATASET not in message

    def test_an_unregistered_key_is_refused(self) -> None:
        with pytest.raises(DatasetUnavailable, match="not a registered demo dataset"):
            load_dataset("acme-v9")


class _Fake:
    def __init__(self, key: str) -> None:
        self._key = key

    @property
    def metadata(self) -> DemoDatasetMetadata:
        return DemoDatasetMetadata(
            key=self._key,
            company_name="Fixture Co",
            domain="fixture.invalid",
            snapshot_date=dt.date(2026, 1, 1),
            seed="fixture",
            description="Contract test fixture.",
        )

    def generations(self) -> Sequence[ScanGeneration]:
        return (ScanGeneration("baseline", "", ()),)

    def probes(self) -> Sequence[CanonicalProbe]:
        return ()


@pytest.fixture
def fixture_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    module = types.ModuleType("tests_demo_fixture_dataset")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(
        DATASETS,
        "fixture-v1",
        datasets.DatasetEntry("fixture-v1", f"{module.__name__}:build", "fixture"),
    )
    yield module


class TestTheContractIsEnforced:
    def test_a_conforming_dataset_loads(self, fixture_module: types.ModuleType) -> None:
        fixture_module.build = lambda: _Fake("fixture-v1")  # type: ignore[attr-defined]
        assert load_dataset("fixture-v1").metadata.company_name == "Fixture Co"

    def test_a_dataset_that_names_itself_differently_is_refused(
        self, fixture_module: types.ModuleType
    ) -> None:
        fixture_module.build = lambda: _Fake("someone-else")  # type: ignore[attr-defined]
        with pytest.raises(DatasetUnavailable, match="describes itself as"):
            load_dataset("fixture-v1")

    def test_a_non_conforming_object_is_refused(self, fixture_module: types.ModuleType) -> None:
        fixture_module.build = lambda: object()  # type: ignore[attr-defined]
        with pytest.raises(DatasetUnavailable, match="does not satisfy"):
            load_dataset("fixture-v1")

    def test_a_missing_factory_is_refused(self, fixture_module: types.ModuleType) -> None:
        with pytest.raises(DatasetUnavailable, match="has no callable"):
            load_dataset("fixture-v1")

    def test_metadata_must_be_synthetic(self) -> None:
        with pytest.raises(ValueError, match="synthetic by definition"):
            DemoDatasetMetadata(
                key="x",
                company_name="x",
                domain="x",
                snapshot_date=dt.date(2026, 1, 1),
                seed="x",
                description="",
                synthetic=False,  # type: ignore[arg-type]
            )


class TestGeneseePressCanon:
    """The canonical values ADG-DEMO-MODE-01 requires Demo Mode to carry unchanged.

    Holds the generator's metadata to the canon, so a drift in either place is caught at the
    integration boundary. (Unskipped by ADG-DEMO-INTEGRATION-01: the generator is in-build.)
    """

    def test_the_canonical_organization(self) -> None:
        metadata = load_dataset(CANONICAL_DATASET).metadata

        assert metadata.key == "genesee-press-v1"
        assert metadata.company_name == "Genesee Press"
        assert metadata.domain == "corp.geneseepress.example"
        assert metadata.snapshot_date == dt.date(2026, 10, 1)
        assert metadata.seed == "genesee-press-v1"

    def test_it_offers_a_baseline_and_probes(self) -> None:
        dataset = load_dataset(CANONICAL_DATASET)
        assert dataset.generations()[0].transcripts
        assert dataset.probes()


class TestDatasetRiskConfiguration:
    def test_a_dataset_may_supply_its_risk_configuration(
        self, fixture_module: types.ModuleType
    ) -> None:
        from app.config import build_settings
        from app.risk_engine.configuration import DEFAULT_CONFIGURATION

        marker = DEFAULT_CONFIGURATION

        class WithRisk(_Fake):
            def risk_configuration(self) -> object:
                return marker

        fixture_module.build = lambda: WithRisk("fixture-v1")  # type: ignore[attr-defined]
        datasets.cached_dataset.cache_clear()
        settings = build_settings(
            runtime_mode="demo",
            database_url="postgresql+psycopg://a:b@h/adg_demo",
            demo_dataset="fixture-v1",
        )
        assert settings.risk_configuration is marker
        datasets.cached_dataset.cache_clear()

    def test_a_dataset_without_one_gets_the_defaults(self) -> None:
        from app.config import build_settings
        from app.risk_engine.configuration import DEFAULT_CONFIGURATION

        settings = build_settings(
            runtime_mode="demo",
            database_url="postgresql+psycopg://a:b@h/adg_demo",
            demo_dataset="adg-reference-estate",
        )
        assert settings.risk_configuration is DEFAULT_CONFIGURATION


class TestTheGeneseePressAdapter:
    """``app.demo.genesee_press.synthetic_dataset()`` carries the generator's output unchanged."""

    def test_its_generations_are_the_generators_byte_for_byte(self) -> None:
        from app.demo.genesee_press.generations import GENERATIONS, build_generations

        adapted = load_dataset(CANONICAL_DATASET).generations()
        direct = build_generations()

        assert [g.name for g in adapted] == [generation.key for generation in GENERATIONS]
        assert [len(g.transcripts) for g in adapted] == [100, 100, 100]
        for generation, (_, transcripts) in zip(adapted, direct, strict=True):
            assert [
                json.dumps(t.as_document(), sort_keys=True) for t in generation.transcripts
            ] == [json.dumps(t.as_document(), sort_keys=True) for t in transcripts]

    def test_the_baseline_is_every_generation(self) -> None:
        """The canon is the 2026-10-01 snapshot: what three scans had collected by then."""
        dataset = load_dataset(CANONICAL_DATASET)
        assert baseline_generations(dataset) == 3 == len(dataset.generations())

    def test_each_risk_pass_is_dated_by_its_generation(self) -> None:
        from app.demo.genesee_press.generations import GENERATIONS

        dates = [g.evaluated_at for g in load_dataset(CANONICAL_DATASET).generations()]
        assert dates == [
            dt.datetime.combine(generation.day, dt.time(6, 0), tzinfo=dt.UTC)
            for generation in GENERATIONS
        ]
        # The canonical pass -- after the snapshot scan -- is the one the Genesee Press
        # reset used before integration: 2026-10-01T06:00Z.
        assert dates[-1] == dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.UTC)

    def test_the_risk_configuration_is_the_datasets_own(self) -> None:
        from app.demo.genesee_press.risk import DEMO_RISK_CONFIGURATION

        supplier = load_dataset(CANONICAL_DATASET).risk_configuration  # type: ignore[attr-defined]
        assert supplier() is DEMO_RISK_CONFIGURATION

    def test_metadata_facts_come_from_the_committed_manifest(self) -> None:
        from pathlib import Path

        manifest = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "docs"
                / "demo"
                / "genesee-press.manifest.json"
            ).read_text(encoding="utf-8")
        )
        statistics = manifest["statistics"]
        facts = load_dataset(CANONICAL_DATASET).metadata.facts

        assert facts["Active employees"] == f"{statistics['employees']['active']:,}"
        assert facts["Former employees"] == f"{statistics['employees']['former']:,}"
        assert facts["Groups"] == f"{statistics['groups']['total']:,}"
        assert facts["File servers"] == f"{statistics['estate']['servers']:,}"
        assert facts["Shares"] == f"{statistics['estate']['shares']:,}"
        assert facts["Folders"] == f"{statistics['estate']['directories']:,}"
        assert facts["NTFS entries"] == f"{statistics['estate']['ntfs_aces']:,}"
        assert (
            facts["Scan runs"] == f"{sum(g['runs'] for g in statistics['generations'].values()):,}"
        )
        assert facts["Observations"] == (
            f"{sum(g['observations'] for g in statistics['generations'].values()):,}"
        )

    def test_scenarios_a_to_h_come_from_the_golden_scenarios(self) -> None:
        from app.demo.genesee_press.scenarios import SCENARIO_IDS, golden_scenarios

        scenarios = dataset_scenarios(load_dataset(CANONICAL_DATASET))
        golden = golden_scenarios()

        assert [s.key for s in scenarios] == list(SCENARIO_IDS) == list("ABCDEFGH")
        for scenario, item in zip(scenarios, golden, strict=True):
            assert scenario.title == item["title"]
            assert scenario.resource == item["resource"]
            assert scenario.summary == item["demonstrates"]

    def test_scenario_subjects_and_probes_name_canon_identifiers(self) -> None:
        from app.demo.genesee_press.canon import build_canon

        canon = build_canon()
        known = {person.sid for person in canon.employees} | {g.sid for g in canon.groups}
        dataset = load_dataset(CANONICAL_DATASET)
        named = {s.sid for scenario in dataset_scenarios(dataset) for s in scenario.subjects}
        probed = {
            value
            for probe in dataset.probes()
            for name, value in probe.key.items()
            if name.endswith("sid")
        }
        assert named and named <= known
        assert probed and probed <= known

    def test_every_probe_is_in_the_snapshot_estate_or_survives_by_rule(self) -> None:
        """A probe is a promise about current state after the reset. Each one names an
        object the 2026-10-01 scans report -- or, for the two history probes, one the
        partial and failed scans may not remove."""
        from app.demo.genesee_press.canon import SNAPSHOT
        from app.demo.genesee_press.estate import build_estate

        estate = build_estate(SNAPSHOT).estate
        g2 = build_estate(dt.date(2026, 9, 17)).estate
        paths = {d.path.casefold(): d for d in estate.directories} | {
            d.path.casefold(): d for d in g2.directories
        }
        shares = {(s.server_name.casefold(), s.share_name.casefold()) for s in estate.shares}
        for probe in load_dataset(CANONICAL_DATASET).probes():
            key = probe.key
            if probe.kind in ("ntfs_resource", "ntfs_ace"):
                directory = paths.get(key["path"].casefold())
                assert directory is not None, probe.description
                if probe.kind == "ntfs_ace":
                    trustees = {ace.trustee_sid for ace in directory.aces}
                    assert key["trustee_sid"] in trustees, probe.description
            elif probe.kind == "share":
                assert (key["server"].casefold(), key["share"].casefold()) in shares

    def test_two_loads_are_identical(self) -> None:
        from app.demo.genesee_press import synthetic_dataset

        first, second = synthetic_dataset(), synthetic_dataset()
        assert first.metadata == second.metadata
        assert first.probes() == second.probes()
        assert [g.run_ids for g in first.generations()] == [g.run_ids for g in second.generations()]

    def test_the_adapter_constructs_no_estate_object(self) -> None:
        """An adapter points at the generator's objects; it never builds its own."""
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2] / "app" / "demo" / "genesee_press" / "dataset.py"
        ).read_text(encoding="utf-8")
        called = {
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not called & {
            "DemoTranscript",
            "DemoDirectory",
            "DemoAce",
            "DemoPrincipal",
            "_assemble",
            "_assemble_nm",
            "build_estate",
            "materialize_directories",
        }
