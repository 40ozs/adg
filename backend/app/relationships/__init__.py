"""Deriving privilege relationships from collected facts (ADG-GRAPH-01B).

* :mod:`app.relationships.rules` — pure rules: stored facts in, relationship assertions out.
* :mod:`app.relationships.projector` — which facts to read, reading them, recording what the
  rules concluded, and retiring what they no longer conclude.

The relationship model these produce is :mod:`app.domain.relationships`; the store they write
is :mod:`app.repositories.relationships`. See ``docs/architecture/graph-relationships.md``.
"""

from __future__ import annotations

from app.relationships.projector import (
    DerivationUnits,
    ProjectionOutcome,
    ProjectionState,
    RelationshipProjector,
    project_completed_run,
)
from app.relationships.rules import RULES, UNDERIVED, RuleSpec

__all__ = [
    "RULES",
    "UNDERIVED",
    "DerivationUnits",
    "ProjectionOutcome",
    "ProjectionState",
    "RelationshipProjector",
    "RuleSpec",
    "project_completed_run",
]
