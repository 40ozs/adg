# ADR-0029: Running a review and answering one are separately held

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 10A — governance data model: owners, reviews, attestations
- **Deciders:** Phase 10A implementation

## Context

Before this phase ADG had three active roles — `viewer`, `auditor`, `admin` — forming a strict
superset chain, and ADR-0014 established that routes depend on capabilities rather than on
roles. Governance adds acts that were not previously expressible: creating a campaign,
choosing what it asks about, assigning reviewers, and recording attestations.

The obvious mapping is to fold them into the existing chain: `admin` runs campaigns, and
anyone who can read the estate can answer one. Two things are wrong with it.

**Whoever chooses the questions must not also give the answers.** An account that can both
scope a campaign and decide its items can pick a scope that excludes what it does not want
examined, answer the remainder, and close it. The audit trail then shows a complete,
compliant review, and every artifact of the process is in order. This is the failure that
separation of duties exists to prevent, and an access-review feature that does not implement
it is producing evidence rather than assurance.

**Running an access review is a compliance function, not an operations one.** `admin` in ADG
is a *platform* administrator: it sets configuration and may replay a collector payload.
Whoever operates the server is frequently not whoever is accountable for reviewing access to
finance data, and in the organizations this product targets they are usually in different
departments. Granting campaign control to the operator role would mean the person who runs
the database can silently create and close attestation campaigns — the first thing an auditor
would object to.

A third question follows: should a plain viewer read governance records at all? A decision
rationale is free text written about a named person. "Leaver." "Should never have had this."
"Ask HR." That is a different and wider disclosure than any access-control list.

## Decision

Three capabilities, and two new roles that do not nest:

```
governance:read     see campaigns, items, decisions, ownership, the audit trail
governance:review   record an attestation
governance:manage   create, scope, generate, assign, close; record ownership
```

| Role | read | review | manage |
| --- | :-: | :-: | :-: |
| `viewer` | – | – | – |
| `auditor` | ✓ | – | – |
| `admin` (platform) | ✓ | – | – |
| `reviewer` | ✓ | ✓ | – |
| `governance_admin` | ✓ | – | ✓ |

* **`governance:manage` does not include `governance:review`,** and vice versa. Somebody who
  must do both is assigned *both roles* — a visible assignment rather than a silent property
  of one.
* **`viewer` holds no governance capability at all.** The governance capabilities are
  additional; nothing a viewer could do before this phase stopped working.
* **`auditor` and `admin` hold only the read.** An auditor's job is to verify that
  attestations happened and by whom; an administrator investigating a complaint has to be
  able to look.

**Capability is not authority.** Holding `governance:review` admits a request to the route and
grants authority over no particular item. The second gate is the **assignment**: an item may
be answered only by the reviewer it was assigned to, checked against the database on every
decision. The two gates are independent — neither can be satisfied by holding the other.

## Consequences

**A deployment must assign the new roles before anyone can run a review.** Governance is
invisible and unusable until somebody is given `governance_admin` and somebody
`reviewer`. That is deliberate: the alternative is a feature that quietly becomes available to
whoever already held `admin`.

**`auditor` gained a capability.** `capabilities_for(AUDITOR) - capabilities_for(VIEWER)` is
now `{settings:read, governance:read}`, and `tests/auth/test_roles.py` was updated to say so
explicitly rather than recomputed from the table — the existing convention there, so that a
change to who can do what has to be made twice, on purpose.

**A one-person team is inconvenienced.** They hold two roles instead of one. The audit trail
then shows that the same subject both scoped and answered the campaign, which is exactly the
fact an auditor should be able to see.

**This is a control against accident and casual misuse, not against a determined operator.**
Anyone with the database credentials can do anything, including forging a whole audit chain
(see `governance-model.md` §6). Stating that plainly is part of the decision: a control
described as stronger than it is, is worse than one described accurately.

## Alternatives rejected

**Fold governance into `admin`.** Simplest, and it makes the operator of the server the
governor of the review process. Rejected above.

**One `governance` role holding all three capabilities.** Simpler to assign and defeats the
separation entirely — the capability split would exist in the code and never in any real
deployment.

**Let `governance:manage` decide items it is not assigned.** Would remove the need for two
roles in a small team. Rejected: the assignment gate would then be bypassable by the one
account most able to misuse it, which inverts the control.

**Enforce separation as a hard prohibition** (refuse to grant both roles). Rejected as
paternalistic and unworkable — small organizations genuinely have one person — and because
the honest record of "the same subject scoped and answered this" is more useful than a
constraint people route around by sharing an account.
