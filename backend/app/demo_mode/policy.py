r"""What a demo deployment does with each request that changes something.

Every mutating route ADG serves is classified here, once, in one table. A test holds the
table exhaustive against the OpenAPI document, the same way ``tests/api/test_authorization.py``
holds the capability table exhaustive, so a new write route cannot ship without somebody
deciding what it means on a demo.

Three treatments:

**A -- supported.** The route writes ADG's own records -- a review decision, an owner, a
watch, a stored simulation, a change plan -- and in a demo deployment every such record lives
in the demo database. Nothing about the route changes.

**B -- simulated.** The route produces something that *describes* a change to real
infrastructure. ADG never performs one (ADR-0035): the closest it comes is the remediation
runbook, a PowerShell script an administrator runs. On a demo that script names fictional
objects, so it is served with every line commented out under a banner saying so, and the
response carries :data:`SIMULATION_NOTICE`. The rest of the workflow -- approval, signing,
the audit chain -- runs for real, because it is ADG's own and it is the thing being shown.

**C -- unavailable.** The route would let something outside the deployment put data into it.
In a demo that is the collector ingestion API: observations come from the synthetic dataset
alone, through the in-process provider (:mod:`app.demo_mode.ingest`), and an external
collector -- or anybody holding a token -- posting to a demo is refused with a
``demo_only`` response before authentication is even consulted.

The guard is a backend dependency on every API route, not a hidden button. A request that
reaches a refused route in demo mode is refused whatever the frontend shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from fastapi import Request

if TYPE_CHECKING:
    from app.demo_mode.datasets import DemoDatasetMetadata

__all__ = [
    "DEMO_ONLY_STATUS",
    "MUTATION_POLICY",
    "SIMULATION_NOTICE",
    "DemoOnlyRefusal",
    "PolicyEntry",
    "Treatment",
    "defang_runbook",
    "demo_policy_guard",
    "policy_for",
]

SIMULATION_NOTICE: Final = "Simulation only — no directory or filesystem change was performed."
DEMO_ONLY_STATUS: Final = "demo_only"


class Treatment(StrEnum):
    SUPPORTED = "supported"
    """A: runs as in production, against demo-owned records."""
    SIMULATED = "simulated"
    """B: runs, and its infrastructure-facing artifact is marked and made inert."""
    UNAVAILABLE = "unavailable"
    """C: refused in demo mode with a ``demo_only`` response."""


@dataclass(frozen=True, slots=True)
class PolicyEntry:
    operation: str
    treatment: Treatment
    reason: str


_ADG_RECORD = "Writes ADG's own records, which in a demo deployment live in the demo database."

_SUPPORTED_OPERATIONS: Final[dict[tuple[str, str], str]] = {
    ("POST", "/auth/dev/login"): "development sign-in",
    ("POST", "/api/v1/alerts/watches"): "create a watch",
    ("PATCH", "/api/v1/alerts/watches/{watch_id}"): "edit a watch",
    ("DELETE", "/api/v1/alerts/watches/{watch_id}"): "remove a watch",
    ("POST", "/api/v1/alerts/deliveries/drain"): "drain alert deliveries",
    ("POST", "/api/v1/governance/campaigns"): "create a review campaign",
    ("POST", "/api/v1/governance/campaigns/{campaign_id}/activation"): "activate a campaign",
    ("POST", "/api/v1/governance/campaigns/{campaign_id}/assignments"): "assign a reviewer",
    ("POST", "/api/v1/governance/campaigns/{campaign_id}/closure"): "close a campaign",
    ("POST", "/api/v1/governance/campaigns/{campaign_id}/decisions"): "record bulk attestations",
    ("POST", "/api/v1/governance/campaigns/{campaign_id}/generation"): "generate campaign items",
    ("POST", "/api/v1/governance/items/{item_id}/decisions"): "record an attestation",
    ("POST", "/api/v1/governance/items/{item_id}/remediation"): "propose a remediation",
    ("POST", "/api/v1/governance/owners"): "record a resource owner",
    ("DELETE", "/api/v1/governance/owners/{owner_id}"): "withdraw a resource owner",
    ("POST", "/api/v1/remediation"): "write a change plan",
    ("POST", "/api/v1/remediation/{plan_id}/approval"): "approve or reject a change plan",
    ("POST", "/api/v1/remediation/{plan_id}/cancellation"): "abandon a change plan",
    ("PUT", "/api/v1/remediation/{plan_id}/changes"): "edit a change plan",
    ("POST", "/api/v1/remediation/{plan_id}/simulation"): "simulate a change plan",
    ("POST", "/api/v1/remediation/{plan_id}/submission"): "submit a change plan",
    ("POST", "/api/v1/simulations"): "store a simulation",
    ("POST", "/api/v1/simulations/preview"): "preview a simulation",
    ("DELETE", "/api/v1/simulations/{simulation_id}"): "remove a simulation",
    ("POST", "/api/v1/simulations/{simulation_id}/evaluations"): "re-run a simulation",
    ("POST", "/api/v1/demo/scans"): "run the next synthetic scan",
}

_SUPPORTED_REASONS: Final[dict[str, str]] = {
    "development sign-in": "Issues a session token. Touches no estate.",
    "drain alert deliveries": (
        "Delivers to log sinks only: a demo deployment refuses any other sink kind at startup "
        "and on every policy read."
    ),
    "run the next synthetic scan": (
        "Applies the dataset's next scan generation through the in-process synthetic "
        "provider. Demo-only route."
    ),
}

MUTATION_POLICY: Final[dict[tuple[str, str], PolicyEntry]] = {
    **{
        key: PolicyEntry(name, Treatment.SUPPORTED, _SUPPORTED_REASONS.get(name, _ADG_RECORD))
        for key, name in _SUPPORTED_OPERATIONS.items()
    },
    ("POST", "/api/v1/remediation/{plan_id}/exports"): PolicyEntry(
        "export a change plan and its runbook",
        Treatment.SIMULATED,
        "The plan is signed and recorded as usual; the runbook names fictional objects, so it "
        "is served commented out and inert, marked simulation only.",
    ),
    **{
        ("POST", path): PolicyEntry(
            name,
            Treatment.UNAVAILABLE,
            "A demo deployment accepts no observations from outside it. Its data comes from "
            "the synthetic dataset, through the in-process synthetic provider.",
        )
        for path, name in (
            ("/api/v1/scan-runs", "begin a collector scan run"),
            ("/api/v1/scan-runs/{run_id}/batches", "send a collector batch"),
            ("/api/v1/scan-runs/{run_id}/completion", "close a collector scan run"),
        )
    },
}


def policy_for(method: str, path_template: str) -> PolicyEntry | None:
    return MUTATION_POLICY.get((method.upper(), path_template))


class DemoOnlyRefusal(Exception):
    """A request reached a route that demo mode does not offer. Rendered as HTTP 403."""

    def __init__(self, entry: PolicyEntry) -> None:
        super().__init__(
            f"'{entry.operation}' is not available in the demo environment. {entry.reason}"
        )
        self.entry = entry


async def demo_policy_guard(request: Request) -> None:
    """Refuse treatment-C routes in demo mode. A no-op in production.

    Installed on every API route by :func:`app.api.build_api_router`. Read from
    ``app.state.settings`` -- the deployment's configuration -- and from nothing a request
    carries, so no header, cookie or parameter can change what it decides.
    """
    settings = request.app.state.settings
    if not settings.is_demo:
        return
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if template is None:
        return
    entry = policy_for(request.method, template)
    if entry is not None and entry.treatment is Treatment.UNAVAILABLE:
        raise DemoOnlyRefusal(entry)


def defang_runbook(runbook: str, dataset: DemoDatasetMetadata | None) -> str:
    """A demo runbook: every generated line commented out, under a banner saying why.

    Commented out rather than refused, because reading the runbook is the point of showing
    the remediation workflow; and commented out rather than guarded by a ``throw`` before the
    script's ``param`` block, because PowerShell requires ``param`` first and a script that
    fails to parse says nothing useful. The one executable line refuses with the reason.
    """
    organization = (
        f"{dataset.company_name} ({dataset.domain}), a fictional"
        if dataset is not None
        else "a fictional"
    )
    banner = [
        "# ADG DEMO ENVIRONMENT -- SIMULATION ONLY",
        f"# {SIMULATION_NOTICE}",
        f"# This runbook was generated against {organization} synthetic organization.",
        "# Every line of the generated script below is commented out so that it cannot run.",
        "#",
    ]
    inert = [f"# {line}" if line else "#" for line in runbook.splitlines()]
    refusal = (
        "throw 'ADG demo environment: this runbook describes fictional objects and is a "
        "simulation artifact. Nothing was changed and nothing will be.'"
    )
    return "\n".join([*banner, *inert, "", refusal]) + "\n"
