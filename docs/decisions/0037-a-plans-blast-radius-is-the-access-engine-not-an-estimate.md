# ADR-0037: A plan's blast radius is the access engine, not an estimate

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 10C — proposed remediation and write-path guardrails
- **Deciders:** Phase 10C implementation

## Context

A change plan has to be put in front of an approver with an answer to *"what will this do?"*,
and the tempting implementation is a small one: the plan names the entries it removes, so list
the principals those entries name and call that the impact.

That answer is wrong in both directions, and wrong in ways an approver cannot detect from the
list alone:

* the principal holds the access through another group as well, so the removal achieves
  nothing — and the list says they lose it;
* a Deny earlier in the DACL meant the entry was never granting anything — and the list says
  they lose it;
* the share ACL in front was the real limit — and the list says they lose something the share
  never let them have;
* the entry sits on a folder whose children inherit it, so the change reaches further than the
  folder named.

Phase 9A already answers this correctly, with the production access engine run twice over an
overlay. The only question is whether remediation uses it or reimplements it.

## Decision

**A plan is translated into a `SimulationOverlay` and measured by
`app.simulation.SimulationService`. There is no remediation-specific impact arithmetic
anywhere in `app/remediation`.**

`app.remediation.translate` maps each `PlannedChangeKind` to the simulation's own `ChangeKind`.
The map is total over the enum and `tests/remediation/test_translation.py` asserts that by
iterating the enum, because a kind with no translation would be accepted, stored, approved and
exported having never been simulated — an instruction wearing the paperwork of one that had
been measured.

Translation is **one-way**. There is deliberately no function turning an overlay back into a
plan: an overlay can express changes a plan may not — adding an ACE, widening a mask, setting
inheritance — and a converter would be a way to smuggle one past `validate_narrowing`.

The report is persisted through `SimulationStore`, as an ordinary row in `simulations` and
`simulation_evaluations`. So the impact behind an approval is a first-class simulation somebody
can open at `/api/v1/simulations/{id}`, re-run and compare — rather than a number copied into a
plan and ageing there.

A plan cannot be submitted for approval unless it has a report **and** that report's collection
basis equals the plan's own. A report measured against a different world than the plan
describes is not this plan's blast radius, and the two drifting apart is invisible unless both
are recorded.

`replace_with_group` contributes **two** changes to one overlay — the removal and the
membership addition — measured together, because measuring either alone answers a question the
plan is not asking.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| List the principals the removed entries name | Wrong in four distinct ways above, and each one is invisible to the approver reading the list |
| A cheaper approximation, with the full simulation as an option | Two answers to one question. The day they disagree, nobody can say which was right about a change somebody already made — the same argument ADR-0020 makes for the simulation engine itself |
| Measure the two halves of `replace_with_group` separately | Reports a loss the plan never intends and a gain nobody is being given |
| Recompute the impact on every read of the plan | The most expensive request in the API, on a page load; and an approval would then name a report that no longer exists |

## Consequences

**Positive**

- The impact an approver reads is the same answer `/api/v1/access` would give about the same
  world, produced by the same code.
- Alternate paths are *measured*: a removal with a surviving group route comes back reported as
  changing nothing, with the surviving route named.
- Every caveat the simulation engine produces — uncertain baseline, loss may not hold,
  alternate path retains access — reaches the approver unchanged.

**Negative / accepted costs**

- Simulating is the most expensive thing the API does, and a plan must be simulated before it
  can be submitted. `remediation:plan` therefore implies the cost, which is why
  `remediation_approver` deliberately does **not** hold `simulations:run`.
- A plan's steps and the overlay's changes are not one-to-one (`replace_with_group` is two), so
  a reader comparing counts will see a mismatch.
- The simulation's bounds apply. A plan whose impact exceeds them produces a truncated report,
  and the report says so rather than the plan being refused.

**Follow-up required**

- Nobody has written a hundred-step plan and timed the simulation of it. `MAX_PLAN_CHANGES` is
  a ceiling informed by readability, not by measurement.

## Compliance

`tests/remediation/test_translation.py` iterates `PlannedChangeKind` and requires every member
to translate and the overlay to accept the result; it also asserts that the only additive
simulated change any plan can produce is `ADD_MEMBER`.
`tests/db/test_remediation_workflow.py::TestAPlanIsMeasuredBeforeItIsPutToAnybody` requires a
current report before submission and refuses to re-measure an approved plan.
`tests/remediation/test_isolation.py` would fail if this package grew its own reads of the ACL
tables for the purpose of deriving access.
