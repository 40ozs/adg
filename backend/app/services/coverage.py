"""Assembling the reviewer's collection-coverage report (ADG-DEMO-03).

Reads the same per-scope records the collector operations page reads
(:class:`app.repositories.operations.OperationsRepository`), so the coverage beside a risk
total and the operator's scope table can never be two different answers. At most five
statements, whatever the estate holds: the latest run, the last success and the last failure
of every scope, the error codes of the latest runs that failed (only when one did), and the
collection basis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.basis import CollectionBasis
from app.domain.collection import CollectionStatus, CollectorCoverage, assess_collection
from app.domain.coverage import CoverageReport, assess_coverage
from app.domain.observation import ScanStatus
from app.domain.operations import ObjectCounts, assemble_operations
from app.repositories.basis import CollectionBasisRepository
from app.repositories.operations import OperationsRepository

__all__ = ["CollectionCoverage", "ExpectationSource", "collection_coverage", "expected_collectors"]

logger = logging.getLogger("adg.coverage")

ExpectationSource = Literal["configured", "dataset", "none"]


@dataclass(frozen=True, slots=True)
class CollectionCoverage:
    """Everything a coverage view needs, read once."""

    report: CoverageReport
    status: CollectionStatus
    basis: CollectionBasis
    expectation: ExpectationSource


def expected_collectors(settings: Settings) -> tuple[tuple[str, ...], ExpectationSource]:
    """The collector kinds this deployment expects, and where that expectation came from.

    Configuration wins. A demo deployment with none configured expects what its dataset's
    scan history runs. Anything else has no expectation, and the report says so rather than
    guessing which absent collector was meant to exist.
    """
    configured = settings.expected_collector_kinds
    if configured:
        return configured, "configured"
    if settings.is_demo:
        from app.demo_mode.datasets import (
            DatasetUnavailable,
            cached_dataset,
            dataset_expected_collectors,
        )

        try:
            dataset = cached_dataset(settings.resolved_demo_dataset)
        except DatasetUnavailable:
            logger.warning("coverage.expectation.dataset_unavailable")
            return (), "none"
        return dataset_expected_collectors(dataset), "dataset"
    return (), "none"


async def collection_coverage(session: AsyncSession, settings: Settings) -> CollectionCoverage:
    repository = OperationsRepository(session)
    latest = await repository.latest_runs()
    successes = await repository.latest_successes()
    failures = await repository.latest_failures()
    failed_latest = [
        run.run_id for run in latest if run.status in (ScanStatus.FAILED, ScanStatus.CANCELED)
    ]
    codes = await repository.failure_codes(failed_latest)
    basis = await CollectionBasisRepository(session).current()

    status = assess_collection(
        CollectorCoverage(
            collector=run.collector,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error_count=run.error_count,
            target=run.target,
            downgrade_reason=run.downgrade_reason,
        )
        for run in latest
    )
    operations = assemble_operations(
        status=status,
        latest=latest,
        successes=successes,
        failures=failures,
        counts=ObjectCounts(),
        errors=(),
    )
    expected, source = expected_collectors(settings)
    report = assess_coverage(operations.scopes, failure_codes=codes, expected=expected)
    return CollectionCoverage(report=report, status=status, basis=basis, expectation=source)
