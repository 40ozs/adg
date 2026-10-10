r"""Where a deployment's observations come from, chosen once from the runtime mode.

ADG's backend holds no directory client, no SMB client and no PowerShell host -- it never
has (ADR-0004, and ``tests/remediation/test_no_write_path.py`` pins it). Collection happens
on Windows, in the collectors under ``collector/``, which *push* observations to the
ingestion API. So the production "provider" is not an object that connects to AD; it is
the ingestion API accepting what authenticated collectors send.

That makes the demo boundary concrete and small:

``production`` -> :class:`CollectorObservationSource`
    External collectors post to ``/api/v1/scan-runs`` with a collector key or an
    administrator's token. Exactly the behavior ADG had before demo mode.

``demo`` -> :class:`SyntheticObservationSource`
    The configured synthetic dataset, posted through the same routes by the in-process
    provider in :mod:`app.demo_mode.ingest`. External ingestion is refused
    (:mod:`app.demo_mode.policy`), collector keys are refused at startup
    (:mod:`app.config`), and the collector source is never constructed.

:func:`observation_source_for` is the only place the choice is made; the application
factory stores its answer on ``app.state.observation_source``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

from app.config import Settings
from app.demo_mode.datasets import SyntheticDataset, cached_dataset

__all__ = [
    "CollectorObservationSource",
    "ObservationSource",
    "SyntheticObservationSource",
    "observation_source_for",
]


class ObservationSource(Protocol):
    @property
    def kind(self) -> Literal["collectors", "synthetic"]: ...

    @property
    def accepts_external_ingestion(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class CollectorObservationSource:
    """Production: Windows collectors push observations over the ingestion API."""

    kind: ClassVar[Literal["collectors"]] = "collectors"
    accepts_external_ingestion: ClassVar[bool] = True


@dataclass(frozen=True, slots=True)
class SyntheticObservationSource:
    """Demo: a synthetic dataset, ingested in-process through the real ingestion routes."""

    kind: ClassVar[Literal["synthetic"]] = "synthetic"
    accepts_external_ingestion: ClassVar[bool] = False

    dataset_key: str

    def dataset(self) -> SyntheticDataset:
        """Load the dataset. Raises DatasetUnavailable rather than substituting another."""
        return cached_dataset(self.dataset_key)


def observation_source_for(settings: Settings) -> ObservationSource:
    """The observation source this deployment's runtime mode selects."""
    if settings.is_demo:
        return SyntheticObservationSource(dataset_key=settings.resolved_demo_dataset)
    return CollectorObservationSource()
