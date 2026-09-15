# ADR-0038: Proposing, approving and carrying out a change are three pairs of hands

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 10C — proposed remediation and write-path guardrails
- **Deciders:** Phase 10C implementation

## Context

ADR-0029 established that whoever runs a review does not answer it: an account able to choose
the questions and give the answers can produce a complete, compliant-looking campaign on its
own. A change plan is the same shape of problem one step further along, and the stakes are
higher, because the output is not an attestation but an instruction somebody carries out
against a production file server.

An account able to write a plan and approve it can produce a fully documented, fully audited
removal of anybody's access with one person's involvement — and the audit trail would look
impeccable. That is worse than an undocumented removal, because it comes with evidence that it
was reviewed.

The same argument extends to the export. The signed document is what an administrator acts on,
and an account that could write a plan, approve it and sign it would be the whole control in
one place.

There is also a narrower failure worth naming: an approver who could **re-run** the blast-radius
simulation could keep narrowing its bounds until the impact list looked acceptable, and then
approve their own answer.

## Decision

**Three capabilities, held by disjoint roles, and checked against the person as well as the
token.**

| Capability | Granted to | Deliberately not |
| --- | --- | --- |
| `remediation:plan` | `governance_admin`, `remediation_planner` | anyone who can approve or export |
| `remediation:approve` | `remediation_approver` | anyone who can plan or export |
| `remediation:export` | `admin` | anyone who can plan or approve |
| `remediation:read` | `auditor`, and everything above | — |

`remediation_approver` holds `simulations:read` and **not** `simulations:run`: approving a
change without reading its blast radius is the failure this phase exists to prevent, and
re-running it until the answer is palatable is the same failure wearing a different hat.

Enforced in three places, because each fails differently:

1. **The role table** — asserted as a property over every role rather than as three assertions
   about three roles, so a role added later is covered without anybody remembering.
2. **The service** — against the *actor*: the requestor may not approve, and neither the
   requestor nor the approver may export. A person legitimately holding two roles is still
   refused, because the rule is about hands rather than about tokens.
3. **The database** — `ck_remediation_plans_approver_is_not_the_requestor`, so a code path
   somebody writes later that sets the columns directly still cannot produce a self-approved
   plan. That is how a separation of duties usually stops holding: not by anybody removing it,
   but by a new write path that never went through the old one.

`admin` gets export and neither of the other two. A platform administrator who could write a
plan, approve it and export it would be exactly the single account this decision rules out —
and export is gated on an approval somebody else recorded, so the capability on its own
produces nothing.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| One `remediation:manage` capability | Collapses all three roles into one account and makes the control unenforceable |
| Plan and approve separated; export folded into approve | The approver signs the instruction they approved, which is two of three controls in one pair of hands — and the export is the artifact that actually reaches the file server |
| Enforce in the service only | Survives until somebody adds a write path that sets the columns directly |
| Enforce in the database only | The database sees subjects, not intent; it cannot express "the caller of *this request* is the approver" |
| Let a person holding two roles proceed | The most tempting relaxation, and the one that makes the whole control cosmetic in every small team — which is where it matters most |

## Consequences

**Positive**

- No single account can take somebody's access away with documentation that says it was
  reviewed.
- A small organization sees the control rather than working around it: the refusal names the
  rule and says to ask a different approver.
- The development account list ships one role per account, so the separation is visible on a
  developer's own screen.

**Negative / accepted costs**

- Three people are needed to complete a change plan. In a two-person team, one plan cannot be
  finished — which is the control working, and will be experienced as friction.
- `governance_admin` can write a plan and cannot approve it, so a compliance team running its
  own reviews still needs an approver from somewhere else.
- The rule is stated in three places and all three must be edited together to change it. That
  is deliberate and it is a maintenance cost.

**Follow-up required**

- There is no delegation and no "approve on behalf of". An approver who is away blocks the
  plan, and the answer today is a second approver account.

## Compliance

`tests/auth/test_roles.py::test_nobody_can_both_write_a_change_plan_and_approve_it` and
`::test_nobody_can_both_approve_a_change_plan_and_export_it` iterate `ROLE_CAPABILITIES`.
`tests/db/test_remediation_workflow.py::TestSeparationOfDuties` drives the refusals at the
service, including for a person holding two roles, and asserts the database constraint fires on
a direct column write. `tests/db/test_remediation_api.py::TestSeparationOfDutiesOverHttp` drives
the same boundary over HTTP as each role.
