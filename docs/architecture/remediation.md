# Proposed remediation and write-path guardrails

**Phase:** 10C · **Status:** implemented · **Code:** `backend/app/remediation/`, `backend/app/api/remediation.py`

A review that concludes *"this group should come off the payroll share"* and stops there has
produced a finding nobody can act on. The work that follows it is a permission change, and a
permission change is the most dangerous thing anybody does to a file server: it is performed
by hand, at night, under time pressure, against an ACL that may have moved since the reviewer
looked at it, by somebody who was not in the review.

This phase makes that change **reviewable before it happens** — precise, measured, approved by
a second person, checked against current state, and handed over as a signed instruction — and
it deliberately stops there. ADG does not carry the change out. Section 8 says exactly what
would have to be true before it could.

See ADR-0035 (a plan is an instruction, not an act), ADR-0036 (an approval binds to a digest
and a basis), ADR-0037 (the blast radius is the simulation engine) and ADR-0038 (three pairs
of hands).

---

## 1. The shape, in one paragraph

A **change plan** is a title, a rationale, and an ordered list of **planned changes**. Each
change names one object exactly — an `ace_key` on one DACL, or one `(group, member)` edge —
and carries the state ADG observed it in, digested. The plan is translated into a
`SimulationOverlay` and measured by the Phase 9A engine; the report is stored as an ordinary
simulation. It is then submitted, approved by somebody who is not the requestor, and exported
as a signed JSON document plus a PowerShell runbook. Every step re-checks that the estate still
matches the plan. Nothing in ADG applies anything.

---

## 2. What a change may say

Six kinds, and the vocabulary is closed (`app/domain/remediation.py`):

| Kind | Acts on | Becomes, in the overlay |
| --- | --- | --- |
| `remove_group_member` | a group's membership | `REMOVE_MEMBER` |
| `remove_share_ace` | a share ACL | `REMOVE_SHARE_ACE` |
| `modify_share_ace` | a share ACL | `MODIFY_SHARE_ACE` |
| `remove_ntfs_ace` | an NTFS DACL | `REMOVE_NTFS_ACE` |
| `modify_ntfs_ace` | an NTFS DACL | `MODIFY_NTFS_ACE` |
| `replace_with_group` | an NTFS DACL **and** a group | `REMOVE_NTFS_ACE` + `ADD_MEMBER` |

The map is total over the enum and a test asserts it over the enum rather than over a list
somebody maintains. A kind with no translation would be accepted, stored, approved and
exported having never been simulated.

### A plan can only ever take access away

`validate_narrowing` refuses:

* a resulting mask that is not a strict subset of the observed one;
* a resulting mask equal to the observed one — a step that changes nothing still costs a
  change window and still reads as work done;
* a share permission level that is not strictly lower;
* **any narrowing of a Deny**, because taking rights out of a Deny *grants* them. This is the
  clause a mask-only rule lets through, and it is the one that turns a remediation into an
  escalation.

Structurally, the only additive change a plan can produce in an overlay is the `ADD_MEMBER`
half of `replace_with_group`, whose whole purpose is to give back access the same step takes
away. `tests/remediation/test_translation.py` asserts that over every kind at once.

### And a plan is a set, not a list

* non-empty — an empty plan would be approved and signed and instruct nobody to do anything;
* at most 100 changes, **refused** rather than truncated;
* one step per object — two steps on one entry conflict, and the second was written against
  state the first one leaves behind, which nothing in the precondition check can see;
* step numbers 0..n-1 exactly once each, assigned by the server, so the runbook a person
  follows and the list an auditor reads cannot silently differ.

---

## 3. The blast radius is the simulation engine

`overlay_for(plan)` produces a `SimulationOverlay`; `SimulationService.run` evaluates it. That
is the Phase 9A engine, which runs `AccessService` twice — once over the real repositories,
once over the overlay pair — with no branch inside it that knows which world it is in.

There is **no remediation-specific impact arithmetic anywhere in this package**. A second
implementation of the access check would eventually disagree with the first, and on that day
nobody would be able to say which was right about a change somebody had already made.

The report is persisted through `SimulationStore`, so the impact behind an approval is a
first-class simulation somebody can open at `/api/v1/simulations/{id}`, re-run and compare —
not a number copied into a plan.

`replace_with_group` puts both halves in **one** overlay on purpose. Measuring the removal
alone reports a loss the plan never intends; measuring the addition alone reports a gain nobody
is being given. Together they answer the question the plan actually asks: *does this principal
end up where they started?*

---

## 4. Preconditions: whether the plan still describes the estate

Every change carries the entry it was written against, digested over **content** —
`(ace_key, trustee, type, mask, flags, source, inherited_from, order_index)`. Excluded:
`version_id`, `observed_from`, `last_confirmed_at`, `certainty`. Those say when ADG learned
the entry and how firmly, and a re-scan confirming an unchanged ACE must not read as somebody
having edited it. The same split `GrantEvidence` draws for baseline drift.

Four verdicts, and keeping three of them apart is the point:

| Verdict | Means | Blocks export |
| --- | --- | --- |
| `satisfied` | byte for byte what was reviewed | no |
| `changed` | present and different — somebody edited it | yes |
| `missing` | gone: the work is done, or something else happened | yes |
| `unobserved` | ADG has no current reading; **nobody has looked** | yes |

`missing` and `unobserved` are the same absence in the data and lead to opposite conclusions.
A plan skipped because "somebody must already have done it" when in fact nobody has scanned
since is a removal that never happened, recorded as done. The sentence a reader sees is built
on the server so no client can collapse the two.

### Read from the timeline, not from the ACL tables

This is the least obvious correctness property in the phase, and it was found by a test rather
than by design.

`ntfs_aces`, `smb_share_aces` and `membership_edges` are **accumulate-only**. Ingestion has no
delete path at all; a run that reconciles a scope records absence as a *tombstone* in
`object_versions` rather than by removing a row (ADR-0013). And an ACE key is
content-addressed, so widening a mask writes a **new** row and leaves the old one standing.

A precondition check that looked the `ace_key` up in `ntfs_aces` would therefore find the entry
as it stood before somebody edited it, report it unchanged, and let a signed instruction go out
against a mask nobody had reviewed. The check reads `object_versions` instead, through
`GovernanceRepository.grants_on_targets_at` — the same read baseline drift uses, so there is
one implementation of "what is on this ACL now" rather than two.

### Where preconditions are enforced

| Act | On failure |
| --- | --- |
| read a plan | reported beside it, never written back (the ADR-0031 argument, applied to a plan) |
| submit | **refused**, plan stays a draft — it has not been shown to anybody yet, and its steps are still editable |
| approve | **invalidated** — it has been put in front of somebody as a true statement and is no longer one |
| export | **invalidated**, and nothing is signed |

The collection basis moving is reported *beside* the verdicts and is not by itself a refusal: a
scan that touched a different server moves the token and changes nothing this plan names.

---

## 5. Approval, and what an approval is of

An approval records the **plan digest** and the **collection basis** it answered against, plus
the simulation the approver was shown. `plan_digest` covers everything an approver could have
acted on — title, rationale, basis, simulation id, and every change in order with its frozen
before-state, its intended after-state and its provenance — and deliberately excludes the
lifecycle fields, so that approving a plan does not change its digest.

Export compares. Editing a plan after approval therefore invalidates the approval by
arithmetic rather than by anybody remembering to clear it, which is the only way that rule
survives a future author adding a field.

### Separation of duties

Three capabilities, held by disjoint roles:

| Capability | Roles | May not |
| --- | --- | --- |
| `remediation:plan` | `governance_admin`, `remediation_planner` | approve, export |
| `remediation:approve` | `remediation_approver` | write a plan, export |
| `remediation:export` | `admin` | write a plan, approve one |
| `remediation:read` | `auditor` and above | anything else |

Enforced three times over, because each layer fails differently:

1. **The role table** — no role holds two of the three. Asserted as a property over the whole
   table, so a role added later is covered.
2. **The service** — the *person*, not the token: the requestor cannot approve, and neither the
   requestor nor the approver can export. Somebody legitimately holding two roles is still
   refused.
3. **The database** — `ck_remediation_plans_approver_is_not_the_requestor`, so a code path
   somebody writes later that sets the columns directly still cannot produce a self-approved
   plan. That is usually how a separation of duties stops holding.

`remediation_approver` deliberately holds `simulations:read` and **not** `simulations:run`. An
approver who could re-run the what-if with narrower bounds until the impact list looked
acceptable would be approving their own answer.

---

## 6. The signed export

A canonical JSON document — the plan, every change with its frozen before-state, the approvals
and the digests they approved, the precondition report, the impact summary, and the head of
the plan's audit chain — signed with HMAC-SHA256 under `ADG_REMEDIATION_SIGNING_KEY`.

**What the signature proves:** this document came from this ADG deployment, unmodified.
**What it does not:** that a named approver pressed a button. Anybody holding the key can
produce one. Accountability lives *inside* the document and in the audit chain.

**There is no unsigned export.** With no key configured the export is refused, with the setting
named. An unsigned change plan is indistinguishable from one somebody typed, and the
administrator executing it at two in the morning has no way to tell.

The `execution` block states, inside the signed bytes, that ADG performed none of this — so the
document cannot be mistaken, by a person or by a pipeline somebody builds later, for a record
of work completed.

### The runbook

A generated PowerShell 7 script, with two properties worth the generation:

* **It does nothing without `-Execute`.** Every step reports what it would do. A remediation
  script that is destructive when double-clicked is one that will eventually be
  double-clicked.
* **Every step re-checks its precondition on the machine**, against the live ACE or membership,
  and stops the whole run on a mismatch. ADG's own check is against what it last *collected*,
  which can be hours old; this one is against the object itself at the moment of the change.
  Neither makes the other redundant.

It uses the ordinary Windows commands — `Set-Acl`, `Revoke-SmbShareAccess`,
`Remove-ADGroupMember`, `Remove-LocalGroupMember` — and it sends somebody to the right machine:
a local group step says which computer, because a BUILTIN SID names a different group on every
one. An inherited entry gets a warning rather than a `Set-Acl` that silently does nothing.

The script's syntax is checked by PowerShell's own parser in
`tests/remediation/test_runbook_is_valid_powershell.py`; a brace error in generated text is
otherwise discovered in a change window.

---

## 7. What cannot happen

| Claim | How it is kept |
| --- | --- |
| No API call mutates Windows or AD | `tests/remediation/test_no_write_path.py` — syntax tree, imports, OpenAPI paths, role table, default configuration; each guard fed the mistake it exists to catch |
| No route reaches an executor | The API has no execution path. The only executor call anywhere is `describe()`, which cannot write |
| Nothing imported can write a descriptor | No `win32security`, `ldap3`, `smbprotocol`, `subprocess`… is a dependency, and no module in `app/api` or `app/remediation` imports one |
| `remediation:execute` is unreachable | Granted by no role, including the reserved `remediator` role that exists to hold the name |
| Remediation writes no collected fact | `tests/remediation/test_isolation.py` (structural) and `tests/db/test_remediation_api.py` (every collected table digested around a whole lifecycle) |
| Lab mode cannot run in production | Refused in `Settings` **and** in `remediator_for` — a guard stated once is a guard somebody moves |

---

## 8. What enabling a write adapter would require

This section exists because "we will add remediation later" is a sentence somebody will read
this document looking for. Nothing below is implemented, and none of it is a configuration
change.

**1. An adapter.** A class implementing `app.remediation.executor.Remediator` against a real
Windows write API. None exists in this codebase, and no dependency provides one — adding
`pywin32` or `ldap3` to `pyproject.toml` and importing it fails
`tests/remediation/test_no_write_path.py` today.

**2. A credential with authority over the estate.** ADG's collectors run read-only and hold
none. A write path needs an identity that can modify group membership or a security descriptor
— which is a far larger blast radius than everything ADG holds now, because it is the account
an attacker would want. It needs its own storage, its own rotation, and its own audit.

**3. A grant of `remediation:execute`.** The capability exists, is declared reserved, and is
held by no role. Activating it means editing `ROLE_CAPABILITIES` and deleting the assertion in
`tests/auth/test_roles.py::test_no_role_may_write_the_estate` — a deliberate act in a file
whose whole purpose is to make that act visible.

**4. An execution route.** There is none, and `test_no_write_path.py` fails when one appears.
It would need the same gates export has (approved, current digest, satisfied preconditions,
third pair of hands) *plus* two the export does not need: an execution window, and a per-change
result recorded transactionally.

**5. A documented rollback for every change kind.** Removing an ACE is reversible only if the
ACE was recorded; removing a membership is reversible only if the membership was. The plan
already stores both, which is most of the work — but "most of" is not a rollback procedure, and
a half-applied plan with no way back is worse than no automation.

**6. A different approval.** "Approved to plan" and "approved to execute" are different
statements, and an approval recorded under this phase's model is the first. Reusing it would
silently widen what every existing approval authorized.

**7. An answer to partial application.** ADG learns the estate's state only from the next
collection. An adapter that applied three of five changes and lost its connection leaves a
state nothing in ADG can describe until a scan runs — and `replace_with_group` is exactly such
a plan, with an interval in which somebody has lost access they were meant to keep.

Until all seven are true, the honest posture is the one shipped: ADG describes the change and a
person makes it.

---

## 9. Known limits

1. **A group's observation is weaker than a resource's.** `target_observed` for a membership
   change means ADG holds *some* version of the group principal — not that its membership was
   re-enumerated on the most recent run. A group scanned once and never again reads as observed.
2. **The precondition is per entry, not per ACL.** A plan whose entries are all unchanged
   passes even if the surrounding DACL was rebuilt around them. The ACL hash would catch that
   and would also refuse every plan after every unrelated edit; the narrower check was chosen
   deliberately and this is its cost.
3. **No UI.** Every route is an API call. `docs/handoffs/phase-10c-remediation.md` names the
   screens the model implies.
4. **Nothing joins a plan to the scan that would confirm it was carried out.** The data exists
   — an exported plan, and later observations of the same entries — and nothing reports
   "exported in April, and the entry is still there in June".
5. **A plan cites a decision, not the other way round.** `GET /candidates` answers "which
   decisions have no plan"; there is no field on a review item saying "a plan covers this".
6. **`GET /candidates` covers review decisions only.** A planned change may cite a
   `risk_finding_key` and the column, the digest and the export all carry it — but nothing
   enumerates *"which open risk findings have no plan"*, which is the same question asked of
   the other half of the product. The join is one query away and is not written.
7. **The lab adapter is not exercised against anything resembling Windows.** It mutates a
   dictionary. It proves the interface is implementable and that the guardrails around it hold,
   and it proves nothing about a real adapter's semantics.
8. **`MAX_PLAN_CHANGES` is 100 and is a guess.** It has been exercised against plans of a few
   steps. Nobody has written a hundred-step plan and timed the simulation of it.
