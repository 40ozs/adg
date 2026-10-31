r"""User-facing engine prose says "identity"; the serialized vocabulary still says "principal".

ADG-UX-BACKEND-COPY-04 changed sentences only. Each sentence below reaches a screen verbatim
(the explain verdict, a condition, the What-if vocabulary and caveats). The second half pins
the wire values those sentences sit beside, so a later copy pass cannot rename them by
accident: clients send and store them.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any

import pytest

from app.access_engine.causality import NodeKind
from app.access_engine.conditions import CONDITION_DESCRIPTIONS
from app.access_engine.verdict import (
    _NO_GRANT_BETWEEN_LAYERS,
    OUTCOME_DESCRIPTIONS,
    AccessOutcome,
)
from app.domain.governance import CampaignFocus, ReviewScopeKind
from app.simulation.describe import CHANGE_KIND_DESCRIPTIONS
from app.simulation.model import (
    CAVEAT_DESCRIPTIONS,
    TRUNCATION_DESCRIPTIONS,
    SimulationTruncation,
)
from app.simulation.overlay import ChangeKind

OPENAPI = pathlib.Path(__file__).resolve().parents[3] / "docs" / "contracts" / "v1" / "openapi.json"

# Windows' own terms, kept exact (UX-01 §3 rule 1).
TERMS_OF_ART = re.compile(
    r"foreign security principal|well-known SYSTEM principal|"
    r"Windows grants every principal full access",
    re.IGNORECASE,
)

PROSE: dict[str, Mapping[Any, str]] = {
    "verdict": OUTCOME_DESCRIPTIONS,
    "no-grant between layers": _NO_GRANT_BETWEEN_LAYERS,
    "conditions": CONDITION_DESCRIPTIONS,
    "change kinds": CHANGE_KIND_DESCRIPTIONS,
    "simulation caveats": CAVEAT_DESCRIPTIONS,
    "simulation truncation": TRUNCATION_DESCRIPTIONS,
}


@pytest.mark.parametrize("table", PROSE.keys())
def test_engine_prose_says_identity_not_generic_principal(table: str) -> None:
    for key, sentence in PROSE[table].items():
        prose = TERMS_OF_ART.sub("", sentence)
        assert not re.search(r"\bprincipals?\b|\bprincipal's\b", prose, re.IGNORECASE), (
            table,
            key,
        )


def test_the_terms_of_art_are_still_there() -> None:
    conditions = " ".join(CONDITION_DESCRIPTIONS.values())
    assert "Windows grants every principal full access" in conditions
    assert "well-known SYSTEM principal" in conditions


def test_the_wording_the_screens_show() -> None:
    granted = OUTCOME_DESCRIPTIONS[AccessOutcome.GRANTED]
    assert granted == "This identity holds rights on this object."
    assert CHANGE_KIND_DESCRIPTIONS[ChangeKind.ADD_MEMBER] == "Add an identity to a group."
    assert CHANGE_KIND_DESCRIPTIONS[ChangeKind.REMOVE_MEMBER] == "Remove an identity from a group."


def test_serialized_values_are_unchanged() -> None:
    assert NodeKind.PRINCIPAL.value == "principal"
    assert ReviewScopeKind.PRINCIPAL.value == "principal"
    assert CampaignFocus.PRINCIPAL.value == "principal"
    assert SimulationTruncation.PRINCIPAL_BUDGET.value == "principal_budget"
    assert ChangeKind.ADD_MEMBER.value == "add_member"
    assert ChangeKind.REMOVE_MEMBER.value == "remove_member"


def test_the_explain_query_parameter_is_still_principal() -> None:
    spec = json.loads(OPENAPI.read_text())
    parameters = spec["paths"]["/api/v1/access/explain"]["get"]["parameters"]
    assert "principal" in {parameter["name"] for parameter in parameters}
