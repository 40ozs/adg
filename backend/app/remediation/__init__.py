r"""Remediation: describing a change to the estate, and never making one.

ADG's third half. Phases 0 to 7 answer *what does Windows say, and when did ADG learn it*.
Phases 8 to 10B answer *what did a person conclude about that*. This package answers *what
should be done about it, what would that do, who approved it, and what exactly does the
person carrying it out have to type* — and it stops precisely there.

Four rules shape everything here, each enforced somewhere a future author cannot skip.

**ADG performs no change.** There is no write adapter in this codebase and no dependency that
could provide one; ``Capability.REMEDIATION_EXECUTE`` is granted by no role, including the
reserved ``remediator`` role that exists to hold its name; no route reaches an executor; and
:class:`~app.remediation.executor.DisabledRemediator` — what every deployment gets — raises
from :meth:`~app.remediation.executor.Remediator.apply` unconditionally. Enforced by
``tests/remediation/test_no_write_path.py``, which walks the syntax tree of ``app/api`` and
``app/remediation`` and fails if anything outside ``executor.py`` so much as names an
execution call. See ADR-0035.

**Nothing leaves ADG that has not been measured.** A plan cannot be submitted for approval
without a blast-radius report, and the report must have been computed against the same
collection basis the plan was written against. The measurement is
:class:`app.simulation.SimulationService` — the production access engine, run twice — reached
through :func:`app.remediation.translate.overlay_for`. There is no remediation-specific impact
arithmetic anywhere in this package (ADR-0037).

**An approval is of a digest, not of a plan.** :func:`app.remediation.model.plan_digest`
covers every field an approver could have acted on; the approval records that digest and the
basis token it answered against, and export compares both. Editing a plan after approval
therefore invalidates the approval by arithmetic rather than by anybody remembering to clear
it (ADR-0036).

**Proposing, approving and carrying out are three pairs of hands.** ``remediation:plan``,
``remediation:approve`` and ``remediation:export`` are held by disjoint roles, the service
refuses an approver who is the requestor and an exporter who is either, and
``ck_remediation_plans_approver_is_not_the_requestor`` refuses a self-approved row whatever
code writes it (ADR-0038).

Layers, framework-free first:

* :mod:`app.domain.remediation` — the closed sets of values (imported by the schema module).
* :mod:`app.remediation.model` — the plan, its steps, the invariants and the digests. Pure.
* :mod:`app.remediation.translate` — plan to simulation overlay. Pure, and one-way.
* :mod:`app.remediation.preconditions` — the stale-state comparison. Pure.
* :mod:`app.remediation.export` — the canonical document, its signature, and the runbook. Pure.
* :mod:`app.remediation.executor` — the interface, the refusal, and the lab fixture.
* :mod:`app.remediation.repository` — every query.
* :mod:`app.remediation.service` — the workflow, the gates, the audit, the transaction.

Full treatment: ``docs/architecture/remediation.md``. What enabling a real write path would
require is §8 of it, and :meth:`app.remediation.executor.DisabledRemediator.describe` returns
the same list over HTTP.
"""

from __future__ import annotations

__all__: list[str] = []
