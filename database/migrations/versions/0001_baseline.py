"""Baseline revision: establishes the migration chain.

ADG has no tables yet; the identity, resource, and observation schema arrives in Phase 1.
This empty baseline exists so that every environment — developer workstation, CI, and
deployment — starts from the same known revision and `alembic upgrade head` is meaningful
from day one.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: see the module docstring."""


def downgrade() -> None:
    """No-op: see the module docstring."""
