# ADR-0004: Collectors are read-only and least-privileged

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 0A — Formal permission domain model
- **Deciders:** ADG project

## Context

ADG's collectors need broad *visibility*: directory objects and group memberships, share
definitions and share ACLs, and NTFS security descriptors across a file estate. Visibility of
that kind is routinely confused with control, and the easy path — run everything as Domain
Admin, and let the tool "fix" what it finds — is exactly the path that turns an auditing
tool into the largest available attack surface in the environment.

Two risks are specific to this product:

* **Blast radius.** A defect in a permission-writing code path can remove access for
  thousands of users in seconds, or grant it. Permission changes are difficult to reverse
  without a reliable record of the prior state.
* **Credential value.** A collector account with broad write rights is a high-value target,
  and its credentials must live somewhere a service can read them.

There is also a practical constraint: reading a security descriptor requires `READ_CONTROL`
on the object, and traversing to it requires traverse rights. Neither requires
administrative privilege, so a least-privilege design is genuinely achievable rather than
aspirational.

## Decision

ADG is read-only by default, and its collectors are least-privileged.

1. **No collector writes to any target system.** No ACL modification, no group-membership
   change, no file or directory modification, no directory-object write. Collectors read and
   report.
2. **Domain Admin is never required for normal collection.** Each collector documents the
   minimum rights it needs — directory read access for identities and membership,
   share-enumeration and share-security read rights for SMB, `READ_CONTROL` plus traverse
   for NTFS — and is expected to run under a dedicated, non-interactive, monitored account.
3. **Collectors hold no authorization logic.** They do not decide who "really" has access,
   do not filter by business rules, and do not omit findings they consider uninteresting.
   That separation is what makes a collector replaceable without changing the meaning of
   stored data (see ADR-0003).
4. **Remediation, when it arrives, is a separate capability.** Phase 10 may *propose*
   changes. Executing one must be an explicit, separately authorized, individually audited
   action with a recorded prior state — never a side effect of a scan, and never enabled by
   default.
5. **Failures are reported, never worked around.** A collector that cannot read a descriptor
   records the failure and marks the run `partial`; it does not escalate its own privileges,
   take ownership, or modify the object to make the read succeed. Access it could not
   observe is reported as unobserved, not as absent.
6. **No secrets in source control.** Credentials come from the environment or a secret store;
   the repository carries templates only.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Run collectors as Domain Admin for reliability | Creates a high-value credential and an unbounded blast radius to avoid a manageable permissions-setup task. |
| Allow collectors to take ownership or adjust ACLs when a read fails | Modifies the very state being audited, and destroys the evidence of the original configuration. |
| Ship remediation in the same code path as collection | A collection defect would become a permission-change defect. |
| Grant write rights now for convenience later | Privilege granted for a future feature is privilege available to a present bug. |

## Consequences

**Positive**

- Operating ADG cannot break access in the environments it audits.
- A compromised collector account yields visibility, not control.
- Deployment is defensible to a security reviewer, and the product can be trialled in
  production without change-control friction.
- Unreadable objects surface as gaps in coverage rather than as silent zero results.

**Negative / accepted costs**

- Setting up least-privilege rights is real work for the operator, and must be documented
  per collector.
- Some objects will be unreadable, so coverage is a first-class concept: `ScanStatus.PARTIAL`
  and `error_count` exist for this.
- Remediation cannot be a quick follow-up feature; it needs its own authorization and audit
  design.

**Follow-up required**

- Phases 1–3: each collector documents its exact minimum rights and validates them at
  startup with an actionable diagnostic.
- Phase 10: remediation guardrails — proposal, explicit authorization, prior-state capture,
  and an audit record.

## Compliance

- `SECURITY.md` and `docs/architecture/system-overview.md` state the posture; this ADR is
  the decision behind them.
- `ScanRun` cannot be `succeeded` with a non-zero `error_count`, so incomplete coverage
  cannot be reported as complete (`backend/tests/domain/test_observation.py`).
- A review should reject any collector code path that writes to a target system, any
  documentation instructing an operator to grant Domain Admin, and any default-enabled
  change execution.
