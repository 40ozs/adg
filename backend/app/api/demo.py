"""``/api/v1/demo`` — the demo deployment's own surface. Registered only in demo mode.

Two routes. Neither changes the runtime mode, and nothing anywhere does: there is no route
that turns a demo into a production deployment or back, because the mode is configuration.

* ``GET /api/v1/demo`` -- the dataset, which of its scan generations are applied, the
  walkthroughs it is built to show (scenarios A-H for Genesee Press, each naming the folder,
  people and groups it is about, so the UI can link into the real pages for them), and the
  mutation policy (what each write route does here). Every role may read it.
* ``POST /api/v1/demo/scans`` -- apply the dataset's next scan generation through the
  synthetic provider: new observations, history, reconciliation and current state, exactly
  as a collector's run would produce them. Requires ``collectors:ingest``, the capability a
  real run requires. The risk pass an operator's schedule would run follows it. When the
  dataset defines no further generation the answer is ``409`` saying so; Demo Mode does not
  invent a scan the dataset did not define.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.runtime import DatasetView
from app.auth.dependencies import requires
from app.auth.roles import Capability
from app.config import Settings
from app.demo_mode.datasets import (
    DatasetUnavailable,
    DemoComparison,
    DemoInvestigation,
    DemoScenario,
    SyntheticDataset,
    baseline_generations,
    cached_dataset,
    dataset_comparisons,
    dataset_investigations,
    dataset_scenarios,
)
from app.demo_mode.lifecycle import DemoLifecycleError, generation_states, run_synthetic_scan
from app.demo_mode.marker import DemoTargetRefused
from app.demo_mode.policy import MUTATION_POLICY

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class GenerationView(BaseModel):
    index: int
    name: str
    description: str
    applied: bool
    in_baseline: bool = Field(description="Part of the canonical state a reset installs.")


class ScenarioSubjectView(BaseModel):
    kind: Literal["person", "group"]
    name: str
    sid: str


class ScenarioView(BaseModel):
    key: str
    title: str
    summary: str
    resource: str = Field(description="UNC path of the folder the scenario is about.")
    subjects: list[ScenarioSubjectView]
    related_resources: list[str]

    @classmethod
    def of(cls, scenario: DemoScenario) -> ScenarioView:
        return cls(
            key=scenario.key,
            title=scenario.title,
            summary=scenario.summary,
            resource=scenario.resource,
            subjects=[
                ScenarioSubjectView(kind=subject.kind, name=subject.name, sid=subject.sid)
                for subject in scenario.subjects
            ],
            related_resources=list(scenario.related_resources),
        )


class InvestigationSeedView(BaseModel):
    """One access question the demo offers as a guided investigation (ADG-DEMO-02).

    The pair to ask about, and nothing about the answer: the investigation page sends it to
    ``GET /api/v1/access/explain`` like any other, and renders what the engine computes.
    """

    key: str
    title: str
    question: str
    principal: ScenarioSubjectView
    resource: str = Field(description="UNC path of the folder the question is about.")
    access_path: Literal["remote_smb", "local"]
    summary: str
    scenarios: list[str] = Field(description="Keys of the scenarios this question belongs to.")

    @classmethod
    def of(cls, item: DemoInvestigation) -> InvestigationSeedView:
        return cls(
            key=item.key,
            title=item.title,
            question=item.question,
            principal=ScenarioSubjectView(
                kind=item.principal.kind, name=item.principal.name, sid=item.principal.sid
            ),
            resource=item.resource,
            access_path=item.access_path,
            summary=item.summary,
            scenarios=list(item.scenarios),
        )


class ComparisonSeedView(BaseModel):
    """One point-in-time comparison the demo offers as a recovery drill (ADG-DEMO-03).

    Two instants and a launch window, and nothing about the result: the page sends them to
    ``GET /api/v1/changes/compare`` like any other comparison.
    """

    key: str
    title: str
    question: str
    at_from: dt.datetime
    at_to: dt.datetime
    drill_window_ms: int | None = Field(
        default=None,
        description="A deliberately short interactive window to launch with, in milliseconds.",
    )
    summary: str

    @classmethod
    def of(cls, item: DemoComparison) -> ComparisonSeedView:
        return cls(
            key=item.key,
            title=item.title,
            question=item.question,
            at_from=item.at_from,
            at_to=item.at_to,
            drill_window_ms=item.drill_window_ms,
            summary=item.summary,
        )


class PolicyView(BaseModel):
    method: str
    path: str
    operation: str
    treatment: Literal["supported", "simulated", "unavailable"]
    reason: str


class DemoView(BaseModel):
    runtime_mode: Literal["demo"] = "demo"
    dataset: DatasetView
    generations: list[GenerationView]
    baseline_generations: int
    next_generation: str | None = Field(
        description="The generation 'Run synthetic scan' would apply, or null when none remains."
    )
    scenarios: list[ScenarioView]
    investigations: list[InvestigationSeedView] = Field(
        default_factory=list,
        description="Guided access investigations to start from: one principal and one "
        "folder each, chosen by the dataset.",
    )
    comparisons: list[ComparisonSeedView] = Field(
        default_factory=list,
        description="Point-in-time comparisons to run as a recovery drill, chosen by the dataset.",
    )
    mutation_policy: list[PolicyView]


class ScanResultView(BaseModel):
    generation: str
    runs: list[str]
    observations_applied: int
    risk_findings_matched: int
    risk_evaluated_at: dt.datetime | None
    notice: str = (
        "Synthetic scan: observations generated by the demo dataset and ingested through the "
        "ordinary collector routes. No directory or file server was contacted."
    )


def _dataset(settings: Settings) -> SyntheticDataset:
    try:
        return cached_dataset(settings.resolved_demo_dataset)
    except DatasetUnavailable as unavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(unavailable)) from None


@router.get(
    "",
    response_model=DemoView,
    summary="The demo dataset, its scan generations and the mutation policy",
    dependencies=[Depends(requires(Capability.COLLECTORS_READ))],
)
async def demo_overview(request: Request) -> DemoView:
    settings: Settings = request.app.state.settings
    dataset = _dataset(settings)
    async with request.app.state.database.engine.connect() as connection:
        states = await generation_states(connection, dataset)
    pending = [state for state in states if not state.applied]
    baseline = baseline_generations(dataset)
    return DemoView(
        dataset=DatasetView.of(dataset.metadata),
        generations=[
            GenerationView(
                index=state.index,
                name=state.name,
                description=state.description,
                applied=state.applied,
                in_baseline=state.index < baseline,
            )
            for state in states
        ],
        baseline_generations=baseline,
        next_generation=pending[0].name if pending else None,
        scenarios=[ScenarioView.of(item) for item in dataset_scenarios(dataset)],
        investigations=[InvestigationSeedView.of(item) for item in dataset_investigations(dataset)],
        comparisons=[ComparisonSeedView.of(item) for item in dataset_comparisons(dataset)],
        mutation_policy=[
            PolicyView(
                method=method,
                path=path,
                operation=entry.operation,
                treatment=entry.treatment.value,
                reason=entry.reason,
            )
            for (method, path), entry in sorted(MUTATION_POLICY.items(), key=lambda i: i[0][::-1])
        ],
    )


@router.post(
    "/scans",
    response_model=ScanResultView,
    status_code=status.HTTP_201_CREATED,
    summary="Run the dataset's next synthetic scan generation",
    dependencies=[Depends(requires(Capability.COLLECTORS_INGEST))],
    responses={409: {"description": "The baseline is missing, or no generation remains."}},
)
async def run_scan(request: Request) -> ScanResultView:
    settings: Settings = request.app.state.settings
    dataset = _dataset(settings)
    try:
        report = await run_synthetic_scan(settings, request.app.state.database, dataset)
    except (DemoLifecycleError, DemoTargetRefused) as refused:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from None
    return ScanResultView(
        generation=report.generation.name,
        runs=[run.name for run in report.seed.runs],
        observations_applied=report.seed.observations_applied,
        risk_findings_matched=report.risk.summary.matched,
        risk_evaluated_at=report.risk.evaluated_at,
    )
