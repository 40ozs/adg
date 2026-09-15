# Architecture decision records

One file per decision, named `NNNN-short-title.md` with a zero-padded sequence number.
Copy `adr-template.md` to start a new one.

Rules:

- A record is never edited to say something different after it is accepted. To change a
  decision, add a new ADR and set the old one's status to `Superseded by ADR-NNNN`.
- Record the decision and its consequences, not a tutorial.
- Link the ADR from the architecture document it constrains.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-sid-as-identity.md) | The SID is the canonical principal identity | Accepted |
| [0002](0002-graph-preserving-membership.md) | Membership is stored as a graph of edges | Accepted |
| [0003](0003-raw-observations-vs-derived-state.md) | Raw observations and derived state are stored separately | Accepted |
| [0004](0004-read-only-collector-posture.md) | Collectors are read-only and least-privileged | Accepted |
| [0005](0005-internal-rights-representation.md) | Rights are layer-tagged bitmasks; labels are derived and never authoritative | Accepted |
| [0006](0006-bounded-in-process-traversal.md) | Membership traversal is bounded, in-process, and reports its own limits | Accepted |
| [0007](0007-resolution-is-a-join.md) | A trustee's resolution is computed at query time, never stored | Accepted |
| [0008](0008-acl-normal-form-and-hash.md) | A DACL has one normalized form, and its hash excludes the owner | Accepted |
| [0009](0009-boundaries-are-derived-from-a-projection.md) | An ACL boundary is derived from the parent's projection, and unknown is a boundary | Accepted |
| [0010](0010-effective-access-is-an-access-check.md) | Effective access is a faithful access check over a constructed token | Accepted |
| [0011](0011-answers-carry-their-uncertainty.md) | An effective-access answer carries the conditions that qualify it | Accepted |
| [0012](0012-bounded-access-queries.md) | Effective-access queries are bounded by inverting the question | Accepted |
| [0013](0013-causality-is-measured-not-inferred.md) | A removal's effect is measured by re-running the access check, never inferred | Accepted |
| [0014](0014-authorization-is-capabilities-enforced-in-the-backend.md) | Authorization is a capability check in the backend, declared once per router | Accepted |
| [0015](0015-emptiness-is-attributed-by-the-backend.md) | An empty view is attributed by the backend, never inferred from a row count | Accepted |
| [0016](0016-an-empty-answer-names-its-own-emptiness.md) | An empty access answer names which kind of emptiness it is | Accepted |
| [0017](0017-derived-answers-are-validated-against-collected-state.md) | A derived answer is cached against collected state, never against a clock | Accepted |
| [0018](0018-history-is-a-versioned-observation-log.md) | History is a versioned observation log; current state stays a projection | Accepted |
| [0019](0019-a-change-is-a-window-not-an-instant.md) | A change is recorded as a window, not an instant | Accepted |
| [0020](0020-simulation-is-the-engine-reading-an-overlay.md) | A simulation is the production engine reading through an overlay | Accepted |
| [0023](0023-risk-findings-are-reproducible-from-their-evidence.md) | A risk finding is a deterministic rule plus the records it can be re-derived from | Accepted |
| [0024](0024-sensitivity-is-declared-not-inferred.md) | Business sensitivity is declared by an operator, never inferred by ADG | Accepted |
| [0025](0025-incremental-collection-is-bounded-by-its-source.md) | Incremental collection is bounded by what its source can prove, and absence is never one of those things | Accepted |
| [0026](0026-an-affirmation-is-verified-and-a-checkpoint-trails-its-data.md) | An affirmation is verified against stored state, and a checkpoint never leads the data it describes | Accepted |
| [0028](0028-governance-is-metadata-about-observations.md) | Governance is metadata *about* observations, never an observation | Accepted |
| [0029](0029-running-a-review-and-answering-one-are-separate.md) | Running a review and answering one are separately held | Accepted |
| [0030](0030-a-campaign-is-a-function-of-a-baseline-instant.md) | A review campaign is a pure function of a baseline instant | Accepted |
| [0031](0031-drift-is-reported-beside-an-item-never-applied-to-it.md) | Baseline drift is reported beside a review item, never applied to it | Accepted |
| [0032](0032-a-suppressed-alert-is-recorded-never-dropped.md) | A suppressed alert is recorded, never dropped | Accepted |
| [0033](0033-raising-an-alert-and-delivering-one-are-separate-transactions.md) | Raising an alert and delivering one are separate transactions | Accepted |
| [0034](0034-a-simulation-is-offered-not-applied.md) | A simulation is offered, never applied, and its capability is its own | Accepted |
| [0035](0035-a-change-plan-is-an-instruction-never-an-act.md) | A change plan is an instruction, and ADG performs none of it | Accepted |
| [0036](0036-an-approval-binds-to-a-plan-digest-and-a-collection-basis.md) | An approval binds to a plan digest and a collection basis | Accepted |
| [0037](0037-a-plans-blast-radius-is-the-access-engine-not-an-estimate.md) | A plan's blast radius is the access engine, not an estimate | Accepted |
| [0038](0038-proposing-approving-and-carrying-out-are-three-pairs-of-hands.md) | Proposing, approving and carrying out a change are three pairs of hands | Accepted |
