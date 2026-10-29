r"""ADG-DEMO-03: what the dataset declares for the coverage and recovery walkthrough.

Pure: no database. The dataset declares two things and computes neither answer: the
collector kinds its own scan history runs (so a kind among them with no run is "expected and
missing" rather than "not configured"), and one comparison drill -- two instants of its own
history and a launch window. What ADG answers is pinned against a reset estate in
``tests/db/test_genesee_press_demo.py::TestTheCoverageAndRecoveryDemo``.
"""

from __future__ import annotations

from app.demo.genesee_press.dataset import GeneseePressDataset
from app.demo.genesee_press.generations import GENERATIONS
from app.demo_mode.datasets import (
    REFERENCE_DATASET,
    dataset_comparisons,
    dataset_expected_collectors,
    load_dataset,
)
from app.domain.observation import CollectorKind

DATASET = GeneseePressDataset()


def test_the_expected_collectors_are_the_ones_its_scans_run() -> None:
    assert dataset_expected_collectors(DATASET) == tuple(
        sorted(kind.value for kind in CollectorKind)
    )


def test_the_reference_dataset_declares_its_own_too() -> None:
    reference = load_dataset(REFERENCE_DATASET)

    assert set(dataset_expected_collectors(reference)) <= {kind.value for kind in CollectorKind}
    assert dataset_expected_collectors(reference)


def test_there_is_one_drill_and_it_spans_the_first_scan_to_the_latest() -> None:
    [drill] = dataset_comparisons(DATASET)

    assert drill.key == "first-to-latest"
    assert drill.at_from == GENERATIONS[0].evaluated_at
    assert drill.at_to == GENERATIONS[-1].evaluated_at
    assert drill.at_from < drill.at_to


def test_both_instants_fall_after_every_run_of_their_generation_closed() -> None:
    """Otherwise the comparison would read a generation half-applied."""
    [drill] = dataset_comparisons(DATASET)
    first, latest = DATASET.generations()[0], DATASET.generations()[-1]

    for generation, instant in ((first, drill.at_from), (latest, drill.at_to)):
        closed = max(t.completion["completed_at"] for t in generation.transcripts)
        assert closed < instant.isoformat().replace("+00:00", "Z")


def test_the_drill_launches_inside_a_window_no_host_reads_two_instants_within() -> None:
    [drill] = dataset_comparisons(DATASET)
    assert drill.drill_window_ms == 1


def test_it_is_deterministic() -> None:
    assert dataset_comparisons(GeneseePressDataset()) == dataset_comparisons(DATASET)
