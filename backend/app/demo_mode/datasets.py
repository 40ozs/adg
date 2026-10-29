r"""The contract between Demo Mode and the synthetic data it runs on.

Demo Mode does not generate data. It runs ADG against data generated elsewhere -- the
canonical Genesee Press estate in :mod:`app.demo.genesee_press`, and the small reference
estate in :mod:`app.demo` -- and this module is the whole of what it needs from such a
generator. Everything a dataset owns stays
with the dataset: its organization, its identifiers, its timestamps, its seed, its golden
scenarios and its determinism. Demo Mode carries those values through ingestion untouched
and never re-derives one.

## The contract

A dataset is anything satisfying :class:`SyntheticDataset`:

``metadata``
    :class:`DemoDatasetMetadata` -- who the fictional organization is. The UI shows this,
    so the application never hard-codes a company name.

``generations()``
    The dataset's scan history, oldest first, as :class:`ScanGeneration` values. Each is a
    tuple of :class:`app.demo.transcripts.DemoTranscript` -- the collector-protocol v1 runs
    the existing generator already produces, posted through ``POST /api/v1/scan-runs`` exactly
    as a Windows collector would post them. The reset installs the dataset's *baseline* --
    its first :func:`baseline_generations` generations, applied one at a time, in order --
    and "Run synthetic scan" applies whatever follows, one generation at a time. An operator
    may also reset to an earlier point of the history (``reset --generations N``) and then
    replay the rest through the scan. A dataset with one generation is valid: it simply has
    no further scans to offer.

``probes()``
    :class:`CanonicalProbe` values: a handful of objects the dataset promises exist after
    ingestion, named by the dataset's *own* canonical identifiers. The reset checks every one
    against current state and fails if any is missing, so a regression anywhere between the
    generator and the tables is caught at the boundary rather than in a screenshot.

Optional members, read with ``getattr`` so a dataset declares only what it has:

``risk_configuration()``
    An :class:`app.risk_engine.configuration.RiskConfiguration`: the sensitive resources and
    rule settings an operator of that estate would declare. A demo with no
    ``ADG_RISK_CONFIGURATION_PATH`` uses it; a dataset without it gets the shipped defaults.
    Production never reads it.

``baseline_generations``
    How many leading generations make up the canonical state the reset installs (default
    ``1``). A dataset whose canon is "what ADG had collected by the snapshot date" -- Null
    Meridian's three scans -- names all of them.

``scenarios()``
    :class:`DemoScenario` values: the walkthroughs the dataset is built to show, each naming
    the folder and the people or groups it is about by the dataset's own identifiers. Served
    on ``GET /api/v1/demo`` so the UI can link into the real application's pages for them;
    Demo Mode renders them and attaches no meaning to any of them.
``investigations()``
    :class:`DemoInvestigation` values: access questions the demo opens with -- one principal,
    one folder, one access path -- so a reviewer can start a guided investigation without
    looking up a SID. Served on ``GET /api/v1/demo`` beside the scenarios and, like them,
    display-only: the answer is whatever the real access engine computes for the pair.
``comparisons()``
    :class:`DemoComparison` values: point-in-time comparisons the demo offers as a recovery
    drill (ADG-DEMO-03) -- two instants of the dataset's own history, and optionally a
    deliberately short interactive window to launch with, so the comparison stops the same
    way every time and the reviewer can see what ADG does when one cannot finish. Display
    only: the comparison is the real one, run by ``GET /api/v1/changes/compare``.

**generations() is called often** -- by the demo overview on every request -- so a dataset
should build its transcripts once and return the same tuple thereafter.

**Run ids must be deterministic.** Demo Mode decides whether a generation has been applied
by asking whether its run ids exist in ``scan_runs``. The existing generator derives them
with uuid5, so a replay is recognized rather than duplicated; a dataset that minted fresh
ids would make every reset look like a new estate.

## The registry

:data:`DATASETS` names every dataset this build knows. An entry is a *loader path*, not an
object, so registering a dataset costs nothing until it is selected and a dataset whose
module is not present in this build reports itself as unavailable, with the reason, rather
than failing to import at startup. Selecting a dataset whose generator is absent -- or
whose generator fails to build -- is a clear refusal, never a silent substitution of some
other estate.
"""

from __future__ import annotations

import datetime as dt
import functools
import importlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.demo.transcripts import DemoTranscript

__all__ = [
    "CANONICAL_DATASET",
    "DATASETS",
    "REFERENCE_DATASET",
    "CanonicalProbe",
    "DatasetEntry",
    "DatasetUnavailable",
    "DemoComparison",
    "DemoDatasetMetadata",
    "DemoInvestigation",
    "DemoScenario",
    "ProbeKind",
    "ScanGeneration",
    "ScenarioSubject",
    "SyntheticDataset",
    "baseline_generations",
    "cached_dataset",
    "dataset_comparisons",
    "dataset_expected_collectors",
    "dataset_investigations",
    "dataset_scenarios",
    "load_dataset",
]


@dataclass(frozen=True, slots=True)
class DemoDatasetMetadata:
    """Who a synthetic dataset describes. Public: served to anonymous callers on ``/runtime``.

    Nothing here may be a secret or an infrastructure detail. It is the fiction's name card.
    """

    key: str
    """The registry key, e.g. ``genesee-press-v1``."""
    company_name: str
    domain: str
    snapshot_date: dt.date
    """The instant the dataset describes. Fixed by the dataset, never the wall clock."""
    seed: str
    description: str
    synthetic: Literal[True] = True
    facts: Mapping[str, str] = field(default_factory=dict)
    """Further public, display-only facts the dataset wants shown (founded, headcount, ...).
    Demo Mode renders them and attaches no meaning to any of them."""

    def __post_init__(self) -> None:
        if self.synthetic is not True:
            raise ValueError("A demo dataset is synthetic by definition.")
        for name in ("key", "company_name", "domain", "seed"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"Demo dataset metadata must name its {name}.")


@dataclass(frozen=True, slots=True)
class ScanGeneration:
    """One step of a dataset's scan history: the runs a collector fleet would have posted."""

    name: str
    description: str
    transcripts: tuple[DemoTranscript, ...]
    evaluated_at: dt.datetime | None = None
    """The instant the risk pass that follows this generation is dated, as the operator's
    scheduled evaluation after that scan would have been. Set by a dataset whose canon fixes
    it, so findings' timestamps belong to the dataset rather than to the moment somebody ran
    the reset; ``None`` means the wall clock."""

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(transcript.run_id for transcript in self.transcripts)


@dataclass(frozen=True, slots=True)
class ScenarioSubject:
    """A person or group a scenario is about, by the dataset's own identifiers."""

    kind: Literal["person", "group"]
    name: str
    sid: str


@dataclass(frozen=True, slots=True)
class DemoScenario:
    """One walkthrough the dataset is built to show. Display-only; see the module docstring."""

    key: str
    title: str
    summary: str
    resource: str
    """The UNC path of the folder the scenario is about."""
    subjects: tuple[ScenarioSubject, ...] = ()
    related_resources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DemoInvestigation:
    """One access question the dataset offers as a guided investigation. Display-only."""

    key: str
    title: str
    question: str
    """The question in words, naming the principal and the folder."""
    principal: ScenarioSubject
    resource: str
    """The UNC path of the folder the question is about."""
    access_path: Literal["remote_smb", "local"] = "remote_smb"
    summary: str = ""
    scenarios: tuple[str, ...] = ()
    """Keys of the :class:`DemoScenario` walkthroughs this one belongs to."""


@dataclass(frozen=True, slots=True)
class DemoComparison:
    """One point-in-time comparison the dataset offers as a recovery drill. Display-only."""

    key: str
    title: str
    question: str
    at_from: dt.datetime
    at_to: dt.datetime
    drill_window_ms: int | None = None
    """A deliberately short interactive window to launch with, in milliseconds. Reading the
    two instants takes longer than this on any host, so the comparison stops at its first
    checkpoint every time. ``None`` launches inside the standard window."""
    summary: str = ""


ProbeKind = Literal["principal", "membership", "share", "share_ace", "ntfs_resource", "ntfs_ace"]


@dataclass(frozen=True, slots=True)
class CanonicalProbe:
    """An object the dataset promises exists in current state after its baseline is applied.

    ``key`` fields by kind -- every value is the dataset's own canonical identifier:

    * ``principal``: ``sid``
    * ``membership``: ``group_sid``, ``member_sid``
    * ``share``: ``server``, ``share``
    * ``share_ace``: ``server``, ``share``, ``trustee_sid``
    * ``ntfs_resource``: ``path``
    * ``ntfs_ace``: ``path``, ``trustee_sid``
    """

    kind: ProbeKind
    description: str
    key: Mapping[str, str]


@runtime_checkable
class SyntheticDataset(Protocol):
    """What Demo Mode needs from a synthetic-data generator. See the module docstring."""

    @property
    def metadata(self) -> DemoDatasetMetadata: ...

    def generations(self) -> Sequence[ScanGeneration]: ...

    def probes(self) -> Sequence[CanonicalProbe]: ...


def baseline_generations(dataset: SyntheticDataset) -> int:
    """How many leading generations the dataset's canonical baseline is. See the module docs.

    Raises:
        DatasetUnavailable: the dataset declares a baseline it does not have.
    """
    count = getattr(dataset, "baseline_generations", 1)
    available = len(dataset.generations())
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= available:
        raise DatasetUnavailable(
            f"Demo dataset {dataset.metadata.key!r} declares a baseline of {count!r} "
            f"generations but defines {available}."
        )
    return count


def dataset_scenarios(dataset: SyntheticDataset) -> tuple[DemoScenario, ...]:
    """The dataset's optional :class:`DemoScenario` walkthroughs, or none."""
    supplier = getattr(dataset, "scenarios", None)
    return tuple(supplier()) if callable(supplier) else ()


def dataset_investigations(dataset: SyntheticDataset) -> tuple[DemoInvestigation, ...]:
    """The dataset's optional :class:`DemoInvestigation` questions, or none."""
    supplier = getattr(dataset, "investigations", None)
    return tuple(supplier()) if callable(supplier) else ()


def dataset_comparisons(dataset: SyntheticDataset) -> tuple[DemoComparison, ...]:
    """The dataset's optional :class:`DemoComparison` drills, or none."""
    supplier = getattr(dataset, "comparisons", None)
    return tuple(supplier()) if callable(supplier) else ()


def dataset_expected_collectors(dataset: SyntheticDataset) -> tuple[str, ...]:
    """The collector kinds the dataset's own scan history runs, in name order.

    A demo estate's expectation is not configured separately: every collector its
    generations post a run for is one the estate is supposed to have, so a kind among them
    that has no run in the database (an earlier reset point, a generation not yet applied)
    is "expected and missing" rather than "not configured" (:mod:`app.domain.coverage`).
    """
    return tuple(
        sorted(
            {
                transcript.collector
                for generation in dataset.generations()
                for transcript in generation.transcripts
            }
        )
    )


class DatasetUnavailable(LookupError):
    """The selected dataset is not registered, or its generator is not in this build."""


@dataclass(frozen=True, slots=True)
class DatasetEntry:
    """A registered dataset: where its loader lives and what depends on it."""

    key: str
    loader: str
    """``module:callable``. The callable takes no arguments and returns a SyntheticDataset."""
    summary: str
    dependency: str | None = None
    """Set while a generator lives outside this build: what has to merge first."""


#: The dataset the product's demo runs on, and the default of ``ADG_DEMO_DATASET``. One name:
#: the registry key, the dataset's metadata key, its seed and the marker on a demo database
#: all read ``genesee-press-v1``.
CANONICAL_DATASET: Final = "genesee-press-v1"
#: The small estate in :mod:`app.demo`. Selectable explicitly; never a fallback.
REFERENCE_DATASET: Final = "adg-reference-estate"

DATASETS: Final[Mapping[str, DatasetEntry]] = {
    CANONICAL_DATASET: DatasetEntry(
        key=CANONICAL_DATASET,
        loader="app.demo.genesee_press:synthetic_dataset",
        summary=(
            "Genesee Press: a ten-year-old, 1,800-person fictional enterprise directory and "
            "file estate, scanned three times. Generated by app.demo.genesee_press."
        ),
    ),
    REFERENCE_DATASET: DatasetEntry(
        key=REFERENCE_DATASET,
        loader="app.demo_mode.reference:synthetic_dataset",
        summary=(
            "The small reference estate in app.demo (corp.example.com): every awkward shape the "
            "product must handle, in a few dozen objects."
        ),
    ),
}


def load_dataset(key: str) -> SyntheticDataset:
    """Load a registered dataset, or say exactly why it cannot be loaded.

    Raises:
        DatasetUnavailable: ``key`` is not registered, or its generator module is not present
            in this build. Never substitutes another dataset.
    """
    entry = DATASETS.get(key)
    if entry is None:
        raise DatasetUnavailable(
            f"ADG_DEMO_DATASET={key!r} is not a registered demo dataset. Registered: "
            f"{', '.join(sorted(DATASETS))}."
        )
    module_name, _, attribute = entry.loader.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as missing:
        # Only the dataset's own module being absent means "not in this build". A module that
        # exists and fails to import one of *its* dependencies is a bug, and is re-raised.
        if missing.name is None or not module_name.startswith(missing.name):
            raise
        detail = f" {entry.dependency}" if entry.dependency else ""
        raise DatasetUnavailable(
            f"Demo dataset {key!r} is registered but its generator ({module_name}) is not "
            f"present in this build.{detail} Demo Mode will not substitute another dataset."
        ) from missing
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise DatasetUnavailable(
            f"Demo dataset {key!r}: {module_name} has no callable {attribute!r}. The contract is "
            "app.demo_mode.datasets.SyntheticDataset."
        )
    dataset = factory()
    if not isinstance(dataset, SyntheticDataset):
        raise DatasetUnavailable(
            f"Demo dataset {key!r}: {entry.loader} returned {type(dataset).__name__}, which does "
            "not satisfy app.demo_mode.datasets.SyntheticDataset."
        )
    if dataset.metadata.key != key:
        raise DatasetUnavailable(
            f"Demo dataset {key!r} describes itself as {dataset.metadata.key!r}. A registry key "
            "and the dataset's own key must agree, or the marker on a demo database would name "
            "a dataset other than the one it holds."
        )
    return dataset


@functools.cache
def cached_dataset(key: str) -> SyntheticDataset:
    """:func:`load_dataset`, once per process. For readers that need only the metadata --
    ``/runtime``, the shell's indicator -- and must not rebuild an estate per request."""
    return load_dataset(key)
