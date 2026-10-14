"""The directory-access benchmark must still run, though its numbers are not assertions.

Same contract as ``test_access_benchmark_harness.py``: nothing about time is asserted. What
is asserted is that the harness builds an estate, measures every shape it names, and reports
the summary's counts — so a benchmark that quietly stopped running cannot leave its last
numbers looking current.
"""

from __future__ import annotations

import os

import pytest

from tests.benchmarks.access_benchmark import machine_facts
from tests.benchmarks.directory_access_benchmark import build_parser, measure, parse_estate, render

SMOKE_ENABLED = os.getenv("ADG_RUN_SMOKE_TESTS") == "1"
DATABASE_URL = os.getenv("ADG_DATABASE_URL")


def test_an_estate_is_spelled_shares_by_directories() -> None:
    assert parse_estate("20x1000") == (20, 1000)
    assert build_parser().parse_args([]).estates == ["4x250", "8x1000"]


@pytest.mark.smoke
@pytest.mark.skipif(not SMOKE_ENABLED, reason="needs PostgreSQL (ADG_RUN_SMOKE_TESTS=1)")
def test_the_harness_measures_every_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    if DATABASE_URL:
        monkeypatch.setenv("ADG_DATABASE_URL", DATABASE_URL)
    facts, results = asyncio.run(measure(["1x40"], repeat=1))

    assert {item.shape for item in results} == {
        "summary_cold",
        "summary_warm",
        "tree_top_warm",
        "tree_expand_warm",
        "exceptions_warm",
        "flat_listing_page",
    }
    (fact,) = facts
    assert fact.directories == 40 and fact.reachable > 0 and fact.roots > 0
    assert fact.statements["tree_expand_warm"] < fact.statements["summary_cold"]
    assert "summary_cold" in render(facts, results, machine_facts())
