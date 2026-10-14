"""The path benchmark harness must still run. Its numbers are recorded, never asserted."""

from __future__ import annotations

from tests.benchmarks.path_benchmark import (
    SCALES,
    Suite,
    build_parser,
    machine_facts,
    run_memory_suite,
)


async def test_the_memory_suite_measures_every_shape() -> None:
    suite = Suite(scale="tiny", machine=machine_facts())

    await run_memory_suite(suite, SCALES["tiny"])

    assert [item.name for item in suite.measurements] == [
        "chain-shortest",
        "chain-no-path",
        "fan-reverse",
        "layered-alternates",
        "ring-shortest",
        "fan-neighbors",
    ]
    for item in suite.measurements:
        assert item.seconds_cold > 0
        assert item.relationships > 0
        assert item.reads > 0


async def test_a_wide_group_costs_reads_per_level_not_per_member() -> None:
    small = Suite(scale="tiny", machine=machine_facts())
    large = Suite(scale="tiny", machine=machine_facts())

    await run_memory_suite(small, {**SCALES["tiny"], "wide": 5})
    await run_memory_suite(large, {**SCALES["tiny"], "wide": 500})

    def reads(suite: Suite) -> int:
        return next(item.reads for item in suite.measurements if item.name == "fan-reverse")

    assert reads(small) == reads(large)


def test_the_command_line_parses() -> None:
    arguments = build_parser().parse_args(["--suite", "nullmeridian", "--repeat", "2", "--json"])
    assert arguments.suite == "nullmeridian"
    assert arguments.repeat == 2
