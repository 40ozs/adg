"""The benchmark harness must still run, even though its numbers are not assertions.

No timing is asserted here. A performance threshold that passes on one machine and fails on
another teaches people to ignore the suite, and the numbers that matter are recorded in
`docs/architecture/ad-graph-validation.md` with the machine that produced them.

What is asserted is that the harness works: that it measures the shapes it claims to, that
the graphs it builds are the graphs it says, and that its output can be parsed. A benchmark
that has quietly stopped running is worse than no benchmark, because the last recorded
numbers still look current.
"""

from __future__ import annotations

import json

import pytest

from tests.benchmarks.graph_benchmark import (
    SCALES,
    Measurement,
    Suite,
    bench_database_url,
    build_parser,
    machine_facts,
    run_memory_suite,
)


@pytest.fixture
def tiny() -> dict[str, int]:
    """A scale small enough to run inside the ordinary test suite."""
    return {"groups": 10, "members_per_group": 3, "depth": 4, "wide_group": 50}


class TestTheMemorySuiteStillRuns:
    async def test_it_measures_every_shape_it_claims_to(self, tiny: dict[str, int]) -> None:
        suite = Suite(scale="tiny", machine=machine_facts())

        await run_memory_suite(suite, tiny)

        shapes = {item.name.split("-")[0] for item in suite.measurements}
        assert shapes == {"chain", "fan", "layered", "ring", "find", "paths"}

    async def test_every_measurement_has_a_size_and_a_duration(self, tiny: dict[str, int]) -> None:
        suite = Suite(scale="tiny", machine=machine_facts())

        await run_memory_suite(suite, tiny)

        for item in suite.measurements:
            assert item.seconds > 0, f"{item.name} recorded no elapsed time"
            assert item.nodes > 0, f"{item.name} measured nothing"
            assert item.suite == "memory"

    async def test_the_wide_shape_really_is_wide(self, tiny: dict[str, int]) -> None:
        suite = Suite(scale="tiny", machine=machine_facts())

        await run_memory_suite(suite, tiny)

        fan_out = next(item for item in suite.measurements if item.name.startswith("fan-out"))
        assert fan_out.nodes == tiny["wide_group"]
        # The claim the whole batched design rests on: one wide level, two queries.
        assert fan_out.provider_calls == 2

    async def test_the_deep_shape_really_is_deep(self, tiny: dict[str, int]) -> None:
        suite = Suite(scale="tiny", machine=machine_facts())

        await run_memory_suite(suite, tiny)

        chain = next(item for item in suite.measurements if item.name.startswith("chain"))
        assert chain.nodes == tiny["depth"] * 4
        assert chain.provider_calls == chain.nodes + 1

    async def test_the_path_measurement_is_taken_at_the_cap(self, tiny: dict[str, int]) -> None:
        # The point of measuring it is that 32,768 paths exist and the cap stops the walk.
        suite = Suite(scale="tiny", machine=machine_facts())

        await run_memory_suite(suite, tiny)

        paths = next(item for item in suite.measurements if item.name.startswith("paths"))
        assert paths.nodes == 1_000
        assert "32,768" in paths.detail

    async def test_a_cycle_is_found_in_the_ring_shape(self, tiny: dict[str, int]) -> None:
        suite = Suite(scale="tiny", machine=machine_facts())

        await run_memory_suite(suite, tiny)

        cycles = next(item for item in suite.measurements if item.name.startswith("find-cycles"))
        assert "1 component(s)" in cycles.detail


class TestReporting:
    async def test_the_text_report_lists_every_measurement(self, tiny: dict[str, int]) -> None:
        suite = Suite(scale="tiny", machine=machine_facts())
        await run_memory_suite(suite, tiny)

        rendered = suite.rendered()

        for item in suite.measurements:
            assert item.name in rendered
        assert "nodes/s" in rendered
        assert "B/node" in rendered

    async def test_the_json_report_is_parseable_and_carries_the_machine(
        self, tiny: dict[str, int]
    ) -> None:
        suite = Suite(scale="tiny", machine=machine_facts())
        await run_memory_suite(suite, tiny)

        document = json.loads(json.dumps(suite.to_json()))

        assert document["scale"] == "tiny"
        assert document["machine"]["platform"]
        assert document["machine"]["measured_at"], "a number without a date is not a measurement"
        assert len(document["measurements"]) == len(suite.measurements)
        for item in document["measurements"]:
            assert "nodes_per_second" in item
            assert "bytes_per_node" in item

    def test_derived_rates_are_safe_when_nothing_was_measured(self) -> None:
        empty = Measurement(suite="memory", name="none")

        assert empty.nodes_per_second == 0.0
        assert empty.bytes_per_node == 0.0


class TestConfiguration:
    def test_every_named_scale_describes_a_whole_graph(self) -> None:
        for name, scale in SCALES.items():
            assert set(scale) == {"groups", "members_per_group", "depth", "wide_group"}, name
            assert all(value > 0 for value in scale.values()), name

    def test_the_scales_are_ordered(self) -> None:
        assert (
            SCALES["small"]["wide_group"]
            < SCALES["medium"]["wide_group"]
            < SCALES["large"]["wide_group"]
        )

    def test_the_parser_defaults_to_the_memory_suite_only(self) -> None:
        # Running the database suite by accident would truncate a database.
        arguments = build_parser().parse_args([])

        assert arguments.database is False
        assert arguments.scale == "medium"

    def test_the_benchmark_database_is_never_the_development_one(self) -> None:
        url = bench_database_url("postgresql+psycopg://adg:secret@localhost:5432/adg")

        assert url.endswith("/adg_bench")

    def test_the_benchmark_database_is_never_the_test_one(self) -> None:
        # tests/db runs against <database>_test; sharing it would make a benchmark run
        # truncate the tables out from under a test run.
        url = bench_database_url("postgresql+psycopg://adg:secret@localhost:5432/adg")

        assert not url.endswith("_test")
