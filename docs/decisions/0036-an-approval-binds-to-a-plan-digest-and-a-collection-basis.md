# ADR-0036: An approval binds to a plan digest and a collection basis

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 10C — proposed remediation and write-path guardrails
- **Deciders:** Phase 10C implementation

## Context

An approval is a person saying *"yes, do that"*. The word **that** is doing all the work, and
a naive implementation loses it in two different ways.

**The plan changes.** Somebody approves a plan of three steps; a fourth is added; the plan is
still marked approved. The approval now authorizes a change nobody read. In a model where
approval is a boolean on a row, this requires no malice and no bug — just an edit.

**The estate changes.** Somebody approves a plan on Tuesday against an entry granting Read. On
Wednesday an administrator widens it to Full Control. On Thursday the plan is executed and
removes Full Control, which nobody reviewed and which somebody was presumably relying on. The
approval was honest, the plan was honest, and the outcome is a removal nobody approved.

The second is the one this product is uniquely placed to catch, because ADG already knows
exactly what it observed and when: `CollectionBasis` moves if and only if a collector has
written something, and each planned change carries a content digest of the entry it was written
against.

## Decision

**An approval records the plan digest and the collection basis it was answered against, and
both are compared before anything is exported.**

`plan_digest(plan)` covers every field an approver could have read and acted on: the title, the
rationale, the basis the plan was measured against, the simulation whose impact they were
shown, and every change in step order with its frozen before-state, its intended after-state
and its provenance. It deliberately **excludes** the lifecycle fields — status, timestamps, the
approval itself — because including them would make the digest change at the moment of
approval, so the approval could never record the digest it approved.

`remediation_approvals` stores `plan_digest` and `basis_token`, both `NOT NULL`, and is unique
on `(plan_id, approver_subject, plan_digest)`.

Export refuses when `plan.approved_plan_digest != plan.digest`. Separately, export re-evaluates
every precondition against current collected state and refuses on anything but `satisfied` —
and **invalidates the plan** rather than merely refusing, because a plan that has been put in
front of an approver as a true statement about the estate and is no longer one should not sit
waiting to be retried.

Submission is checked the same way and is *not* invalidated on failure: a draft has not been
shown to anybody and its steps are still editable, so the useful outcome is a report the
planner can act on.

The collection basis moving is reported beside the per-change verdicts and is **not** by itself
a refusal. A scan that touched a different server moves the token and changes nothing this plan
names; a rule that refused on the token alone would reject every plan after every scan and
would be switched off.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| A boolean `approved` column | Loses both halves of "what did you approve". Survives an edit to the plan and a change to the estate |
| Lock the plan on submission and rely on that | Locking is a rule somebody can add a code path around. The digest comparison is arithmetic and holds against a code path nobody reviewed |
| Refuse on any basis movement | Refuses every plan after every unrelated scan. An operator would stop believing the check |
| Compare the whole ACL hash rather than per-entry digests | Would catch a DACL rebuilt around unchanged entries — and would also refuse on every unrelated edit to the same folder. The narrower check was chosen; the cost is recorded in `docs/architecture/remediation.md` §9.2 |
| Re-freeze the plan's before-states on read, so it is always current | Exactly the failure ADR-0031 prevents for review items: it would silently invalidate the approval recorded against the old content, and the plan would always look satisfied |

## Consequences

**Positive**

- Editing a plan after approval invalidates the approval by arithmetic, not by anybody
  remembering to clear it — so the rule survives a future author adding a field.
- "Approved against facts that have since moved" is a state the system can name, and does.
- The refusal is legible: the precondition report says which step, which field moved, and from
  what to what.

**Negative / accepted costs**

- A plan whose entries move must be rewritten rather than edited. That is more work for a
  planner, and it is the correct amount of work.
- Invalidation on a failed approval or export is irreversible. A plan that failed for a
  transient reason — a scan mid-flight — is not recoverable and a new one must be written.
- Two digests and a token per approval is more machinery than a boolean, and a reader has to
  understand `CollectionBasis` to understand the check.

**Follow-up required**

- Nothing re-simulates automatically after an invalidation. A planner rewrites the plan and
  runs the what-if again by hand.

## Compliance

`tests/remediation/test_model.py::TestWhatAnApprovalIsOf` pins what the digest covers and
excludes. `tests/db/test_remediation_workflow.py::TestStaleStateRejection` moves the estate
under a plan four ways and requires the approval or the export to be refused and the plan
invalidated, and asserts that nothing was signed. `ck_remediation_plans_approval_is_attributed`
refuses an approved row that names neither the approver, the digest, nor the basis.
