"""Replaying canonical scan-run transcripts through the ingestion API.

Phase 0B's fixtures are complete transcripts covering all seven observation kinds. Phase 1B
stores two of them, so replaying a fixture verbatim would be rejected — correctly, since the
endpoint refuses to acknowledge observations it cannot persist.

These helpers therefore send the AD half of a transcript and adjust the completion's counts
to what was actually sent, so a run's reported coverage still matches its observations. They
deliberately go through HTTP rather than calling the service: the status codes are part of
the collector contract, and a test that bypassed them would not be testing the contract.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from httpx import AsyncClient

from tests.fixtures import Scenario, load_raw

AD_KINDS = frozenset({"principal", "membership_edge"})


def ad_only(document: dict[str, Any]) -> dict[str, Any]:
    """A transcript reduced to the observation kinds this phase stores."""
    reduced = copy.deepcopy(document)
    batches = []
    observations_sent = 0
    for batch in reduced["batches"]:
        kept = [item for item in batch["observations"] if item["kind"] in AD_KINDS]
        if not kept:
            continue
        batch["observations"] = kept
        observations_sent += len(kept)
        batches.append(batch)
    reduced["batches"] = batches
    # A run must not claim it sent more than it did: an unexplained shortfall is what the
    # server downgrades a run to 'partial' for.
    reduced["completion"]["batch_count"] = len(batches)
    reduced["completion"]["observation_count"] = observations_sent
    return reduced


def with_run_id(document: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    """Re-stamp a transcript with a fresh run id so it can be replayed repeatedly."""
    fresh = run_id or str(uuid.uuid4())
    rewritten = copy.deepcopy(document)
    rewritten["start"]["run_id"] = fresh
    for batch in rewritten["batches"]:
        batch["run_id"] = fresh
        batch["batch_id"] = str(uuid.uuid4())
        for observation in batch["observations"]:
            observation["run_id"] = fresh
    rewritten["completion"]["run_id"] = fresh
    return rewritten


async def replay(client: AsyncClient, document: dict[str, Any]) -> dict[str, Any]:
    """Post start, batches, and completion; assert each step was accepted."""
    run_id = document["start"]["run_id"]

    started = await client.post("/api/v1/scan-runs", json=document["start"])
    assert started.status_code in (200, 201), started.text

    applied = []
    for batch in document["batches"]:
        response = await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)
        assert response.status_code == 202, response.text
        applied.append(response.json())

    completed = await client.post(
        f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
    )
    assert completed.status_code == 200, completed.text
    return {"run_id": run_id, "batches": applied, "completion": completed.json()}


async def ingest_scenario(
    client: AsyncClient, name: str, *, run_id: str | None = None
) -> dict[str, Any]:
    """Load a canonical scenario, reduce it to AD facts, and replay it."""
    document = with_run_id(ad_only(load_raw(name)), run_id)
    return await replay(client, document)


def scenario_document(name: str, run_id: str | None = None) -> dict[str, Any]:
    """The AD half of a scenario, ready to post, without sending it."""
    return with_run_id(ad_only(load_raw(name)), run_id)


def edge_count(scenario: Scenario) -> int:
    return len(scenario.edges)
