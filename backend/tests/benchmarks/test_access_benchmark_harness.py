"""The access benchmark must still run, even though its numbers are not assertions.

Same contract as `test_benchmark_harness.py`: no timing is asserted here, because a
performance threshold that passes on one machine and fails on another teaches people to
ignore the suite. What is asserted is that the harness still measures the three shapes it
claims to, against the same estate the cost tests use, and that its output can be read.

A benchmark that has quietly stopped running is worse than no benchmark, because the last
recorded numbers still look current.
"""

from __future__ import annotations

import json
from urllib.parse import unquote

import pytest

from tests.benchmarks.access_benchmark import (
    DEFAULT_REPEAT,
    DEFAULT_SIZES,
    SHAPES,
    Measurement,
    bench_database_url,
    build_parser,
    machine_facts,
    render,
)
from tests.support.access_estate import SHARE_UNC, SUBJECT


class TestItMeasuresTheShapesThePhaseNamed:
    def test_all_three_questions_are_covered(self) -> None:
        assert set(SHAPES) == {
            "one_principal_one_resource",
            "every_principal_on_one_share",
            "every_share_for_one_principal",
        }

    def test_every_shape_is_an_access_endpoint(self) -> None:
        for name, url in SHAPES.items():
            assert url.startswith("/api/v1/access/"), name

    def test_the_shapes_name_the_estate_the_cost_tests_build(self) -> None:
        """A benchmark measuring a different estate from the assertions measures nothing."""
        for name, url in SHAPES.items():
            decoded = unquote(url)
            assert SHARE_UNC in decoded or SUBJECT in decoded, name

    def test_the_listings_ask_for_a_page_the_api_will_serve(self) -> None:
        from app.api.pagination import MAX_LIMIT

        for name, url in SHAPES.items():
            if "limit=" not in url:
                continue
            requested = int(url.rsplit("limit=", 1)[1])
            assert requested <= MAX_LIMIT, f"{name} asks for {requested}, above the cap"


class TestTheStatisticsAreWhatTheyClaim:
    @pytest.fixture
    def measurement(self) -> Measurement:
        return Measurement(
            shape="one_principal_one_resource",
            size=10,
            rows=1,
            samples=[0.010, 0.012, 0.011, 0.100],
        )

    def test_the_median_is_not_dragged_by_an_outlier(self, measurement: Measurement) -> None:
        assert measurement.median_ms == 11.5

    def test_the_minimum_is_the_fastest_sample(self, measurement: Measurement) -> None:
        assert measurement.min_ms == 10.0

    def test_the_p95_reaches_the_slow_tail(self, measurement: Measurement) -> None:
        """The number that shows a stall the median hides."""
        assert measurement.p95_ms == 100.0

    def test_a_single_sample_still_produces_every_statistic(self) -> None:
        single = Measurement(shape="x", size=1, rows=1, samples=[0.05])
        assert single.min_ms == single.median_ms == single.p95_ms == 50.0

    def test_a_row_carries_everything_a_table_needs(self, measurement: Measurement) -> None:
        row = measurement.as_row()
        assert set(row) == {"shape", "size", "rows", "min_ms", "median_ms", "p95_ms"}
        assert json.dumps(row)


class TestTheOutputCanBeRead:
    def test_the_table_renders_one_row_per_measurement(self) -> None:
        results = [
            Measurement(shape=name, size=10, rows=3, samples=[0.01, 0.02]) for name in SHAPES
        ]
        table = render(results, machine_facts())
        for name in SHAPES:
            assert name in table
        assert table.count("\n|") == len(results) + 2  # header, separator, then the rows

    def test_the_table_records_the_machine(self) -> None:
        table = render([Measurement(shape="x", size=1, rows=1, samples=[0.01])], machine_facts())
        assert "Measured on" in table

    def test_machine_facts_carry_enough_to_compare_a_later_run(self) -> None:
        facts = machine_facts()
        assert set(facts) == {"measured_at", "platform", "processor", "python"}
        assert facts["measured_at"].endswith("Z")


class TestItsDefaultsAreSafe:
    def test_the_parser_defaults_to_the_documented_run(self) -> None:
        arguments = build_parser().parse_args([])
        assert arguments.sizes == list(DEFAULT_SIZES)
        assert arguments.repeat == DEFAULT_REPEAT
        assert arguments.json is False

    def test_sizes_and_repeat_can_be_overridden(self) -> None:
        arguments = build_parser().parse_args(["--sizes", "5", "50", "--repeat", "3", "--json"])
        assert arguments.sizes == [5, 50]
        assert arguments.repeat == 3
        assert arguments.json is True

    def test_it_measures_the_test_database_and_never_the_development_one(self) -> None:
        """This harness truncates what it points at, so where it points is load-bearing."""
        assert bench_database_url().rsplit("/", 1)[1].endswith("_test")
