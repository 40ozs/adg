"""Genesee Press: the synthetic ten-year-old, 1,800-person company behind ADG's Demo Mode.

Read ``docs/demo/genesee-press.md`` first. :mod:`.vendor` holds the pinned projection of the
shared synthetic world; :mod:`.canon` reads who and what exists from it;
:mod:`.estate` builds the file servers on top of it; :mod:`.generations` cuts both into the
scan runs a real deployment would have received; :mod:`.dataset` presents all of it to Demo
Mode through :class:`app.demo_mode.datasets.SyntheticDataset`.

This package builds the demo data and nothing else. Resetting a demo database, the safety checks
around it and the synthetic scan belong to Demo Mode (``python -m app.demo_mode``), which
loads this dataset through :func:`synthetic_dataset`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.demo.genesee_press.dataset import GeneseePressDataset

__all__ = ["synthetic_dataset"]


def synthetic_dataset() -> GeneseePressDataset:
    """Demo Mode's loader for ``genesee-press-v1`` (``app.demo.genesee_press:synthetic_dataset``).

    Imported on call, so importing one of this package's modules does not import the adapter.
    """
    from app.demo.genesee_press.dataset import GeneseePressDataset

    return GeneseePressDataset()
