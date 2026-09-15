# ADR-0035: A change plan is an instruction, and ADG performs none of it

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 10C — proposed remediation and write-path guardrails
- **Deciders:** Phase 10C implementation

## Context

Phases 10A and 10B end with a reviewer concluding that a grant should be removed, and nothing
after that. The obvious next feature is for ADG to remove it: the product knows the exact ACE,
has measured what removing it would do, and has the attestation that says it should go. Every
comparable product in this category eventually ships a "remediate" button.

Three things make that a different kind of feature from everything ADG does today.

**The credential.** ADG's collectors run read-only and hold no authority over the estate. A
write path needs an identity that can modify a security descriptor or a group membership —
which is, by construction, the account an attacker most wants. Introducing it changes ADG from
a thing that would be embarrassing to compromise into a thing that would be catastrophic to
compromise.

**The blast radius of a bug.** An audit tool that reports wrongly produces a wrong answer that
somebody can check. An audit tool that *acts* wrongly produces an outage, or an exposure, at
the speed of a loop. ADG has already found several defects in its own reasoning by writing
tests for it — including, in this phase, a precondition check that read the wrong table and
reported an entry unchanged four days after it was removed. That defect, in a product with a
write path, removes a mask nobody reviewed.

**Partial application.** ADG learns the estate's state only from the next collection. An
adapter that applies three changes of five and loses its connection leaves a state nothing in
ADG can describe until a scan runs.

## Decision

**ADG describes changes and makes none.** There is no write adapter in this codebase, no
dependency that could provide one, no route that reaches an executor, and no role that grants
`Capability.REMEDIATION_EXECUTE`.

The seam is named rather than absent: `app.remediation.executor.Remediator` is a protocol with
`describe()` and `apply()`. Every deployment gets `DisabledRemediator`, which raises from
`apply()` unconditionally with a message naming what would have to change. A fixture-backed
`LabRemediator` exists for tests, is refused outside a non-production deployment that has
explicitly asked for it, mutates an in-memory copy of a JSON file, and is imported by nothing
in `app/`.

The deliverable of a change plan is a **signed document and a runbook**, handed to a human
administrator. `docs/architecture/remediation.md` §8 lists the seven things that would each
have to be true before any execution path existed.

An interface with no implementation is deliberate rather than incomplete. Without a named seam,
the first person to add remediation adds it wherever the request happens to be, and the
guardrails get invented in a hurry by whoever is in one.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Ship execution behind a feature flag, off by default | A flag that can be turned on means the write path already exists, with its credential and its code, in every deployment. The guarantee "this cannot change my domain" would become "this is configured not to", which is a different and much weaker sentence |
| Ship execution for membership only, as the least dangerous kind | Removing the wrong membership is not obviously less dangerous than removing the wrong ACE, and it would require every item on the §8 list anyway |
| Ship nothing at all — no plan, no export | The review stops at a conclusion nobody can act on, which is the gap 10A and 10B both named. The change gets made anyway, by hand, unmeasured and unapproved |
| Emit a plan but leave the format to the operator | The runbook's precondition checks are the tedious part and the part that prevents the failure. A person writing it by hand writes them for the first three steps |

## Consequences

**Positive**

- The claim "ADG cannot change my Active Directory" is structural, not procedural, and is
  checked five ways by `tests/remediation/test_no_write_path.py`.
- The product needs no privileged credential, so compromising it discloses rather than
  destroys.
- `GET /api/v1/remediation/execution-policy` lets an operator ask the running process, which is
  the only answer worth having.
- When a write path is eventually wanted, the seam, the preconditions, the approval and the
  audit event are already on the path.

**Negative / accepted costs**

- The last mile is manual. Somebody types commands at two in the morning, and the runbook can
  only make that safer, not unnecessary.
- ADG cannot report whether a plan was carried out. Only a later scan can, and nothing yet
  joins the two (`docs/architecture/remediation.md` §9.4).
- A competitor's demo will be shorter.

**Follow-up required**

- Join an exported plan to the collection that would confirm it: "exported in April, still
  there in June" is the report this decision makes possible and does not yet produce.

## Compliance

`tests/remediation/test_no_write_path.py` fails if: any module in `app/api` or
`app/remediation` outside `executor.py` calls an execution method; any of them imports a
directory or descriptor client; the OpenAPI document grows a path that reads as an execution;
any role grants `remediation:execute`; or the default execution mode is anything but
`disabled`. `tests/auth/test_roles.py::test_no_role_may_write_the_estate` is the older
assertion this phase left standing unchanged.
