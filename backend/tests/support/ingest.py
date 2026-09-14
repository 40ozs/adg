"""Replaying canonical scan-run transcripts through the ingestion API.

Phase 0B's fixtures are complete transcripts covering all seven observation kinds, and
since Phase 3A the ingestion endpoint stores every one of them — so a fixture can now be
replayed verbatim.

The subsetting helpers remain, because a subset is still what several tests want: a scenario
reduced to its AD half exercises the membership graph without any resource rows, and one
reduced to the share layer shows what a share-only estate looks like before the file-system
scan has run. They adjust the completion's counts to what was actually sent, so a run's
reported coverage still matches its observations.

They deliberately go through HTTP rather than calling the service: the status codes are part
of the collector contract, and a test that bypassed them would not be testing the contract.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from httpx import AsyncClient

from tests.fixtures import Scenario, load_raw

AD_KINDS = frozenset({"principal", "membership_edge"})
SMB_KINDS = frozenset({"server", "smb_share", "smb_ace"})
NTFS_KINDS = frozenset({"ntfs_resource", "ntfs_ace"})
STORABLE_KINDS = AD_KINDS | SMB_KINDS | NTFS_KINDS
"""Every contract v1 kind, as of Phase 3A. Kept as a union rather than a literal set so
that dropping a kind from one of the three groups cannot silently shrink it."""


def of_kinds(document: dict[str, Any], kinds: frozenset[str]) -> dict[str, Any]:
    """A transcript reduced to the given observation kinds."""
    reduced = copy.deepcopy(document)
    batches = []
    observations_sent = 0
    for batch in reduced["batches"]:
        kept = [item for item in batch["observations"] if item["kind"] in kinds]
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


def ad_only(document: dict[str, Any]) -> dict[str, Any]:
    """A transcript reduced to the AD observation kinds."""
    return of_kinds(document, AD_KINDS)


def smb_only(document: dict[str, Any]) -> dict[str, Any]:
    """A transcript reduced to the share layer and the principals its ACLs name.

    What a share-only estate looks like: the NTFS side of every resource is simply unknown,
    which is what an API asked about it must say rather than reporting open access.
    """
    return of_kinds(document, AD_KINDS | SMB_KINDS)


def storable(document: dict[str, Any]) -> dict[str, Any]:
    """A transcript reduced to everything the ingestion endpoint can persist.

    Since Phase 3A that is the whole transcript. The reduction is kept rather than inlined
    so that a kind added to the contract and not to the endpoint is dropped here - and the
    tests that use this helper keep passing - instead of failing every unrelated test.
    """
    return of_kinds(document, STORABLE_KINDS)


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


async def ingest_storable_scenario(
    client: AsyncClient, name: str, *, run_id: str | None = None
) -> dict[str, Any]:
    """Load a canonical scenario, drop only what cannot yet be stored, and replay it."""
    return await replay(client, with_run_id(storable(load_raw(name)), run_id))


async def ingest_smb_scenario(
    client: AsyncClient, name: str, *, run_id: str | None = None
) -> dict[str, Any]:
    """Load a canonical scenario, reduce it to the share layer, and replay it."""
    return await replay(client, with_run_id(smb_only(load_raw(name)), run_id))


def scenario_document(name: str, run_id: str | None = None) -> dict[str, Any]:
    """The AD half of a scenario, ready to post, without sending it."""
    return with_run_id(ad_only(load_raw(name)), run_id)


def storable_document(name: str, run_id: str | None = None) -> dict[str, Any]:
    """The storable part of a scenario, ready to post, without sending it."""
    return with_run_id(storable(load_raw(name)), run_id)


def edge_count(scenario: Scenario) -> int:
    return len(scenario.edges)
