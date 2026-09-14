"""Posting the demo transcripts through the ordinary ingestion API.

There is no direct-to-database path here, on purpose. Seeding writes through
``POST /api/v1/scan-runs``, ``.../batches`` and ``.../completion`` — the same four status
codes a Windows collector gets, the same validation, the same idempotency. A seeder that
inserted rows would let the demo diverge from anything a real collector can actually
produce, and it would stop the seeding itself from being a test of the endpoint.

The transport is anything with an awaitable ``post(url, json=...)`` returning an object
with ``status_code``, ``json()`` and ``text``. That is ``httpx.AsyncClient`` in the CLI and
the ASGI-bound test client in the suite, so the end-to-end tests seed exactly the way an
operator does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.demo.transcripts import DemoTranscript

__all__ = ["SeedReport", "SeedRunReport", "SeedingFailed", "seed_transcripts"]


class Poster(Protocol):
    """The part of an HTTP client this module uses."""

    async def post(self, url: str, *, json: Any) -> Any: ...


class SeedingFailed(RuntimeError):
    """A seeding step was refused. Carries the status and the server's own message."""

    def __init__(self, step: str, status_code: int, detail: str) -> None:
        super().__init__(f"{step} was refused with HTTP {status_code}: {detail}")
        self.step = step
        self.status_code = status_code
        self.detail = detail


@dataclass(slots=True)
class SeedRunReport:
    """What one run's seeding did, in the terms the ingestion contract uses."""

    name: str
    run_id: str
    collector: str
    status: str
    created: bool
    batches_posted: int
    observations_applied: int
    duplicate_batches: int
    recorded_status: str
    downgrade_reason: str | None = None

    @property
    def replayed(self) -> bool:
        """True when the server recognized this run and applied nothing new."""
        return not self.created and self.observations_applied == 0


@dataclass(slots=True)
class SeedReport:
    runs: list[SeedRunReport] = field(default_factory=list)

    @property
    def observations_applied(self) -> int:
        return sum(run.observations_applied for run in self.runs)

    @property
    def replayed(self) -> bool:
        """True when nothing at all was new: the estate was already seeded."""
        return bool(self.runs) and all(run.replayed for run in self.runs)

    def summary(self) -> str:
        lines = [
            f"{run.name:<20} {run.collector:<18} {run.recorded_status:<10} "
            f"{run.observations_applied:>6} applied"
            + ("  (replay)" if run.replayed else "")
            + (f"  downgraded: {run.downgrade_reason}" if run.downgrade_reason else "")
            for run in self.runs
        ]
        lines.append(f"{'total':<20} {'':<18} {'':<10} {self.observations_applied:>6} applied")
        return "\n".join(lines)


def _detail(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        return str(response.text)[:500]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)[:500]


#: Run states in which a run has been closed and will accept no further observations.
#: Mirrors ``ScanStatus.is_terminal``, read from the start response rather than imported,
#: because this module speaks the wire contract rather than the domain.
_TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed", "canceled"})


async def seed_transcripts(client: Poster, transcripts: Sequence[DemoTranscript]) -> SeedReport:
    """Post every run, in order, and report what the server recorded.

    Re-seeding is safe and is the normal case: run ids and batch ids are derived from the
    estate, so a second seeding applies nothing. An operator who runs the seed script twice
    gets one estate, not two.

    **A closed run's batches are not re-posted.** The collector protocol is explicit that a
    completed run rejects further batches with ``409`` — "stop sending batches, start a new
    run" — and it is right to: a batch arriving after a completion means the collector and
    the server disagree about whether the run finished, which is a real fault on a real
    collector. A replayed batch is not that, but the endpoint cannot tell the two apart
    without weakening the rule, so the seeder respects it instead: a start that comes back
    ``200`` with a terminal status means this run is already fully applied, and its batches
    are skipped. The completion is still posted, because it is idempotent and its answer is
    what the report shows.

    A run that was *interrupted* — started, some batches applied, never completed — is not
    terminal, so it resumes: every batch is re-posted and the applied ones are acknowledged
    as duplicates.

    The end-to-end walkthrough found this by seeding twice. Nothing else would have: a
    single seeding never re-posts anything.
    """
    report = SeedReport()
    for transcript in transcripts:
        started = await client.post("/api/v1/scan-runs", json=transcript.start)
        if started.status_code not in (200, 201):
            raise SeedingFailed(
                f"starting run {transcript.name}", started.status_code, _detail(started)
            )
        created = started.status_code == 201
        already_closed = not created and str(started.json()["status"]) in _TERMINAL_STATUSES

        applied = 0
        duplicates = 0
        for batch in () if already_closed else transcript.batches:
            response = await client.post(
                f"/api/v1/scan-runs/{transcript.run_id}/batches", json=batch
            )
            if response.status_code != 202:
                raise SeedingFailed(
                    f"batch {batch['sequence']} of run {transcript.name}",
                    response.status_code,
                    _detail(response),
                )
            body = response.json()
            applied += int(body["applied"])
            duplicates += 1 if body["duplicate"] else 0

        completed = await client.post(
            f"/api/v1/scan-runs/{transcript.run_id}/completion", json=transcript.completion
        )
        if completed.status_code != 200:
            raise SeedingFailed(
                f"completing run {transcript.name}", completed.status_code, _detail(completed)
            )
        outcome = completed.json()

        report.runs.append(
            SeedRunReport(
                name=transcript.name,
                run_id=transcript.run_id,
                collector=transcript.collector,
                status=transcript.status,
                created=created,
                batches_posted=len(transcript.batches),
                observations_applied=applied,
                duplicate_batches=duplicates,
                recorded_status=outcome["status"],
                downgrade_reason=outcome.get("downgrade_reason"),
            )
        )
    return report
