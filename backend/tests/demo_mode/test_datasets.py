"""The dataset contract: Demo Mode consumes the existing generator and never rewrites it.

The reference adapter is checked against :mod:`app.demo` directly -- its transcripts must be
the generator's transcripts, byte for byte -- because an adapter that "tidied up" a run id or
a timestamp would be a second source of synthetic truth.
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
    load_dataset,
)
from app.demo_mode.reference import ReferenceEstateDataset


def _canonical_available() -> bool:
    try:
        load_dataset(CANONICAL_DATASET)
    except DatasetUnavailable:
        return False
    return True


NULL_MERIDIAN_AVAILABLE = _canonical_available()
NULL_MERIDIAN_PENDING = (
    "DEPENDENCY: the Null Meridian generator (ADG-DEMO-NULL-MERIDIAN-01, branch "
    "claude/inspiring-volta-6uzdsy) has not merged; app.demo.null_meridian is absent."
)


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
    def test_the_canonical_dataset_is_registered_with_its_dependency(self) -> None:
        entry = DATASETS[CANONICAL_DATASET]
        assert entry.loader == "app.demo.null_meridian:synthetic_dataset"
        assert entry.dependency is not None

    @pytest.mark.skipif(NULL_MERIDIAN_AVAILABLE, reason="Null Meridian generator is present")
    def test_an_absent_generator_is_refused_and_never_substituted(self) -> None:
        with pytest.raises(DatasetUnavailable) as refused:
            load_dataset(CANONICAL_DATASET)

        message = str(refused.value)
        assert "not present in this build" in message
        assert "will not substitute" in message
        assert "claude/inspiring-volta-6uzdsy" in message

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
        DATASETS,  # type: ignore[arg-type]
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


@pytest.mark.skipif(not NULL_MERIDIAN_AVAILABLE, reason=NULL_MERIDIAN_PENDING)
class TestNullMeridianCanon:
    """The canonical values ADG-DEMO-MODE-01 requires Demo Mode to carry unchanged.

    Skipped until the generator merges; then it holds the generator's metadata to the canon,
    so a drift in either place is caught at the integration boundary.
    """

    def test_the_canonical_organization(self) -> None:
        metadata = load_dataset(CANONICAL_DATASET).metadata

        assert metadata.key == "null-meridian-v1"
        assert metadata.company_name == "Null Meridian"
        assert metadata.domain == "nullmeridian.info"
        assert metadata.snapshot_date == dt.date(2026, 10, 1)
        assert metadata.seed == "null-meridian-v1"

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
