"""Null Meridian: the synthetic ten-year-old, 1,800-person company behind ADG's Demo Mode.

Read ``docs/demo/null-meridian.md`` first. :mod:`.canon` decides who and what exists;
:mod:`.estate` builds the file servers on top of it; :mod:`.generations` cuts both into the
scan runs a real deployment would have received; :mod:`.dataset` presents all of it to Demo
Mode through :class:`app.demo_mode.datasets.SyntheticDataset`.

This package generates data and nothing else. Resetting a demo database, the safety checks
around it and the synthetic scan belong to Demo Mode (``python -m app.demo_mode``), which
loads this dataset through :func:`synthetic_dataset`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.demo.null_meridian.dataset import NullMeridianDataset

__all__ = ["synthetic_dataset"]


def synthetic_dataset() -> NullMeridianDataset:
    """Demo Mode's loader for ``null-meridian-v1`` (``app.demo.null_meridian:synthetic_dataset``).

    Imported on call, so importing one of this package's modules does not import the adapter.
    """
    from app.demo.null_meridian.dataset import NullMeridianDataset

    return NullMeridianDataset()
