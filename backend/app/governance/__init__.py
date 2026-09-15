r"""Governance: owners, review campaigns, attestations, proposed remediation, audit trail.

ADG's other half. Phases 0 through 7 answer *what does Windows say, and when did ADG learn
it*. This package answers *what did a person conclude about that, who were they, and what
were they looking at when they concluded it* — and keeps the two apart, which is the only
reason either answer is worth anything.

Four rules shape everything here. Each is enforced somewhere a future author cannot skip.

**Governance never writes a collected fact.** A decision records a judgment about an
observation and must never alter the observation; a decision that should lead to a change in
Windows produces a :class:`~app.governance.model.RemediationProposal`, which ADG records and
does not perform. Enforced twice: ``tests/governance/test_isolation.py`` walks this package's
syntax tree and fails if any write here names a collected table, and
``tests/db/test_governance_isolation.py`` digests every collected table before and after a
whole campaign and requires them byte-identical.

**A campaign is frozen against an instant.** Items are a pure function of
``(focus, scopes, options)`` applied to ``object_versions`` at the campaign's baseline, so a
reviewer certifies *what was true when the campaign was cut* and that statement stays true
however the estate moves on. It is also reproducible: regenerating from the campaign row
yields the same items with the same digests, which ``verify_campaign`` recomputes and compares.

**Capability is not authority.** Holding ``governance:review`` admits a request to the route.
An item may be answered only by the reviewer it was **assigned** to, checked against the
database on every decision. And whoever runs campaigns cannot answer them: ``governance:manage``
does not include ``governance:review`` (ADR-0029).

**The record is append-only.** A changed mind writes a new decision and supersedes the old
one; the audit trail is hash-chained per campaign and refuses updates and deletes by database
trigger. Both are in ``0008_governance_model``, not in a convention.

Layers, framework-free first:

* :mod:`app.domain.governance` — the closed sets of values (imported by the schema module).
* :mod:`app.governance.model` — values, invariants, the campaign state machine, the digests.
* :mod:`app.governance.audit` — the event and its hash chain.
* :mod:`app.governance.generation` — the item-generation policy. Pure, hence reproducible.
* :mod:`app.governance.repository` — every query, and the baseline reads over the timeline.
* :mod:`app.governance.service` — the workflow, the assignment gate, the transaction boundary.

Full treatment: ``docs/architecture/governance-model.md``.
"""

from __future__ import annotations

__all__: list[str] = []
