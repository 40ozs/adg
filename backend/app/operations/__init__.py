"""Operator commands: the things somebody runs, rather than something a request triggers.

Two of them, and both are here rather than behind an HTTP route on purpose.

``evaluate-risks``
    A full pass of the risk rules over the estate. It reads every directory, share,
    principal and access control entry ADG holds, and it is by some distance the most
    expensive thing this application does. A route that could start one would be a route
    that lets any reader of the report start it, repeatedly, by refreshing a page.

``drain-alerts``
    Attempt the deliveries the outbox holds. The API has a bounded hand-drain for an
    operator who has just fixed a webhook; this is the one to put on a schedule, because it
    can loop and because it does not hold a request open while a remote endpoint times out.

Both take the instant they run at from the clock once, at the start, and pass it down —
so the two halves of one pass cannot straddle midnight and disagree about which day they
happened on.
"""

from app.operations.commands import (
    DrainSummary,
    EvaluationSummary,
    drain_alerts,
    evaluate_risks,
)

__all__ = ["DrainSummary", "EvaluationSummary", "drain_alerts", "evaluate_risks"]
