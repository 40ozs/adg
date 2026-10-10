r"""The synthetic provider: a dataset's scan runs, posted through ADG's own ingestion routes.

This is the whole of how synthetic data enters a demo deployment, and it adds no ingestion
code of its own. It builds the ordinary application (:func:`app.main.create_app`) against
the demo database and hands :func:`app.demo.seed.seed_transcripts` -- the existing
transport the seed CLI and the end-to-end suite already use -- an in-process HTTP client
bound to it. Every observation therefore goes through the real route, the real contract
validation, the real :class:`~app.ingestion.service.IngestionService`, the history writer,
reconciliation and current-state derivation, exactly as a Windows collector's would.

Two dependency overrides make that possible, and they exist **only on the application
object built here**, which never listens on a socket:

* :func:`app.auth.dependencies.ingest_principal` resolves to a collector principal named
  ``demo-synthetic-provider``, so no credential is configured, minted or stored anywhere.
* :func:`app.demo_mode.policy.demo_policy_guard` is lifted, because the guard's job is to
  refuse ingestion from *outside* a demo deployment and this is the inside.

The application a demo deployment serves has neither override, so its ingestion routes
stay refused. Nothing a request carries can reach this module.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import ingest_principal
from app.auth.principal import AuthenticatedPrincipal, collector_principal
from app.config import Settings
from app.db import Database
from app.demo.seed import SeedReport, seed_transcripts
from app.demo_mode.datasets import ScanGeneration
from app.demo_mode.policy import demo_policy_guard

__all__ = ["SYNTHETIC_PROVIDER", "NotADemoDeployment", "apply_generation"]

#: The collector identity synthetic runs are recorded under.
SYNTHETIC_PROVIDER = "demo-synthetic-provider"


class NotADemoDeployment(RuntimeError):
    """The synthetic provider was asked to run outside demo mode."""


def _synthetic_principal() -> AuthenticatedPrincipal:
    return collector_principal(SYNTHETIC_PROVIDER)


async def _no_guard() -> None:
    return None


async def apply_generation(
    settings: Settings, database: Database, generation: ScanGeneration
) -> SeedReport:
    """Post one generation's runs, in order, and report what the server recorded.

    Idempotent, because the transcripts' run ids are deterministic: a generation already
    applied is recognized as a replay and applies nothing.

    Raises:
        NotADemoDeployment: ``settings`` are not demo settings. Refused here as well as by
            every caller, so the synthetic provider cannot run against a production database
            even if a caller forgets to check.
    """
    if not settings.is_demo:
        raise NotADemoDeployment(
            "The synthetic provider runs only with ADG_RUNTIME_MODE=demo; this deployment is "
            f"{settings.runtime_mode.value}."
        )
    # Imported here: app.main imports the API, and the demo router imports this module.
    from app.main import create_app

    app = create_app(settings)
    app.state.database = database
    app.dependency_overrides[ingest_principal] = _synthetic_principal
    app.dependency_overrides[demo_policy_guard] = _no_guard
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://demo-synthetic-provider"
    ) as client:
        return await seed_transcripts(client, generation.transcripts)
