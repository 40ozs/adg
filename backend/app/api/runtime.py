"""``GET /runtime`` — what this deployment is connected to, for anybody who asks.

Public for the reason ``/auth/config`` is: the web shell must render the demo indicator
before anyone signs in, because "this is fictional data" has to be visible to the person
deciding whether to believe what they are looking at, not only to the person already inside.

It discloses the runtime mode, where observations come from (``collectors`` or
``synthetic``), and -- on a demo only -- the synthetic dataset's public name card. Never a
connection string, a database name, a hostname, a credential or a count of anything real.
On production it says ``production`` and nothing else.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import Settings
from app.demo_mode.datasets import DatasetUnavailable, DemoDatasetMetadata, cached_dataset

router = APIRouter(tags=["meta"])


class DatasetView(BaseModel):
    key: str
    company_name: str
    domain: str
    snapshot_date: dt.date
    seed: str
    synthetic: bool = True
    description: str
    facts: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def of(cls, metadata: DemoDatasetMetadata) -> DatasetView:
        return cls(
            key=metadata.key,
            company_name=metadata.company_name,
            domain=metadata.domain,
            snapshot_date=metadata.snapshot_date,
            seed=metadata.seed,
            description=metadata.description,
            facts=dict(metadata.facts),
        )


class RuntimeView(BaseModel):
    runtime_mode: str = Field(description="production or demo. Fixed by deployment.")
    data_source: str = Field(description="collectors, or synthetic on a demo deployment.")
    synthetic: bool
    dataset: DatasetView | None = Field(
        default=None, description="The synthetic dataset's public name card. Demo only."
    )
    notice: str | None = Field(
        default=None, description="One sentence for the UI to show on a demo deployment."
    )


def demo_notice(metadata: DemoDatasetMetadata | None) -> str:
    organization = metadata.company_name if metadata is not None else "a fictional organization"
    return (
        f"This environment uses deterministic synthetic data for {organization}. No production "
        "Active Directory or file servers are connected."
    )


@router.get("/runtime", response_model=RuntimeView, summary="Runtime mode and data source")
async def runtime(request: Request) -> RuntimeView:
    settings: Settings = request.app.state.settings
    mode = settings.runtime_mode
    if not settings.is_demo:
        return RuntimeView(runtime_mode=mode.value, data_source=mode.data_source, synthetic=False)
    try:
        metadata: DemoDatasetMetadata | None = cached_dataset(
            settings.resolved_demo_dataset
        ).metadata
    except DatasetUnavailable:
        # A demo API whose dataset cannot load refuses to start (verify_demo_startup), so this
        # is reached only by an application built without its lifespan. It still says demo.
        metadata = None
    return RuntimeView(
        runtime_mode=mode.value,
        data_source=mode.data_source,
        synthetic=True,
        dataset=DatasetView.of(metadata) if metadata is not None else None,
        notice=demo_notice(metadata),
    )
