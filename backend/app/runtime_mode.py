"""Which world this deployment is connected to: the real estate, or a synthetic one.

``ADG_RUNTIME_MODE`` is a **deployment boundary**, not a preference. It is read once, at
startup, from configuration; no route, no header and no browser control can change it, and
nothing in the API reports it as something a caller could change. A demonstration deployment
that a visitor could switch to "live" would be a production deployment with extra steps.

Two values exist:

``production``
    The default, and the behavior ADG has always had. Observations arrive from the Windows
    collectors over the ingestion API (``POST /api/v1/scan-runs``), authenticated by a
    collector key or an administrator's token. Nothing about this mode changed when demo mode
    was added.

``demo``
    Observations come from a synthetic dataset, posted through the *same* ingestion routes by
    an in-process provider (:mod:`app.demo_mode`). External ingestion is refused, collector
    credentials are refused at startup, alert delivery is log-only, and the database must be
    positively identified as a demo database. Everything downstream of ingestion -- history,
    current state, the access engine, risk, search, simulation, governance -- is the
    production code reading synthetic rows.

**Unknown values fail closed.** ``ADG_RUNTIME_MODE=foobar`` -- or an empty value, which is a
variable somebody set and did not fill in -- is a startup error naming the accepted values.
Nothing ever falls back to ``production``: a deployment that asked for something ADG does not
recognize has not asked for production, and treating it as though it had would connect a
box somebody meant to isolate. Only an *unset* variable means production, because that is
what every deployment that predates this setting already is.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["RuntimeMode", "UnknownRuntimeMode", "parse_runtime_mode"]


class RuntimeMode(StrEnum):
    """The data world a deployment is connected to. See the module docstring."""

    PRODUCTION = "production"
    DEMO = "demo"

    @property
    def data_source(self) -> str:
        """Where this mode's observations come from, as a public, secret-free label."""
        return "synthetic" if self is RuntimeMode.DEMO else "collectors"


class UnknownRuntimeMode(ValueError):
    """``ADG_RUNTIME_MODE`` held something that is not a runtime mode."""


def parse_runtime_mode(value: object) -> RuntimeMode:
    """Read a configured runtime mode, refusing anything that is not exactly one.

    Surrounding whitespace and letter case are forgiven, because ``Demo`` is unambiguous and a
    trailing space in an ``.env`` file is invisible. Nothing else is: an empty value, a typo
    and a plausible synonym (``prod``, ``live``, ``sandbox``) are all refused, so the only
    way to reach a mode is to name it.
    """
    if isinstance(value, RuntimeMode):
        return value
    accepted = ", ".join(mode.value for mode in RuntimeMode)
    if not isinstance(value, str):
        raise UnknownRuntimeMode(
            f"ADG_RUNTIME_MODE must be one of: {accepted}; received {value!r}."
        )
    normalized = value.strip().casefold()
    for mode in RuntimeMode:
        if normalized == mode.value:
            return mode
    raise UnknownRuntimeMode(
        f"ADG_RUNTIME_MODE must be one of: {accepted}; received {value!r}. ADG does not fall "
        "back to production for a value it does not recognize: a deployment meant to be "
        "isolated would otherwise start connected. Unset the variable for production."
    )
