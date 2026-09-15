# Governance: owners, review campaigns, attestations, remediation, audit

**Status:** implemented in Phase 10A.
**Code:** `backend/app/domain/governance.py`, `backend/app/governance/`, `backend/app/api/governance.py`.
**Storage:** `database/migrations/versions/0008_governance_model.py`.
**Decisions:** [ADR-0028](../decisions/0028-governance-is-metadata-about-observations.md),
[ADR-0029](../decisions/0029-running-a-review-and-answering-one-are-separate.md),
[ADR-0030](../decisions/0030-a-campaign-is-a-function-of-a-baseline-instant.md).

---

## 1. What this layer is for, and what it must not become

Phases 0 through 7 answer *what does Windows say, and when did ADG learn it*. Every row in
those tables is somebody's observation, and ADG's whole value rests on the fact that it does
not edit them.

This layer answers a different question: *what did a person conclude about that, who were
they, and what were they looking at when they concluded it*. A governance record is a
**statement about** an observation. It is never an observation, and it never changes one.

That distinction is the entire design. Everything below is a consequence of it.

| Observed (Phases 0–7) | Governance (Phase 10A) |
| --- | --- |
| `ntfs_resources.owner_sid` — the SID Windows stores as the object's owner | `resource_owners` — who **ADG** holds accountable |
| `ntfs_aces` — the entries a collector read | `review_items` — the entries somebody was asked to judge |
| `object_versions` — when a state held | `review_decisions` — what a person concluded, and when |
| Written only by `app.ingestion` | Written only by `app.governance` |

The two columns in row one are the clearest case. ADG holding Alice accountable for
`\\fs01\finance` and Windows reporting `BUILTIN\Administrators` as its owner are both true,
and the **gap between them is a finding**. One column holding both would erase it.

---

## 2. The eight tables

```
resource_owners            who ADG holds accountable for a share or a directory
review_campaigns           one review, frozen against an instant
review_campaign_scopes     what it selected, and therefore what it claims to cover
review_assignments         who was asked, for which slice, by when
review_items               one grant to decide, with the evidence it was frozen from
review_decisions           attestations. append-only; a change of mind supersedes
remediation_proposals      changes somebody might make in Windows. ADG performs none
governance_audit_events    immutable, hash-chained record of every governance act
```

**No table here has a foreign key to a collected table.** A campaign names a `target_key`
and a `principal_key` as strings, exactly as the ACL tables name each other. A foreign key
would make a review of a share that a later reconciliation proves is gone either undeletable
or cascading — and both are wrong answers for the record of who attested to what, which is
precisely what somebody wants to read at that moment.

---

## 3. A campaign is a function of an instant

A campaign carries `baseline_at`. Its items are generated from the versions of
`object_versions` that covered that instant, and from nothing else:

```
items = generate(focus, scopes, options, grants_at(baseline_at))
```

Every input is stored on the campaign row. `object_versions` is append-only and a version's
interval is fixed once written. So **regenerating a campaign from its own row reproduces it
exactly**, months later, and `GET /campaigns/{id}/verification` does precisely that and
compares. Verification is a *re-run of the same two functions*, not a second implementation
of the rules — `generate()` and `verify_campaign()` both call
`GovernanceService._generate_from_baseline`.

That is what "a campaign is reproducible against its baseline snapshot" means here, and why
divergence is a finding rather than noise. The only ways a baseline can come to produce a
different answer are:

* history was edited directly in the database;
* it was restored from a partial backup;
* retention pruned versions past the baseline instant.

Each of those is something an auditor must be told, and the verification response says so in
prose rather than returning three numbers.

### What the freeze buys

Without it, "I approved this" silently comes to mean "I approve of whatever it is now", which
is the failure mode that makes attestation theater. With it, an approval is a dated statement
about a dated fact.

`tests/db/test_governance_workflow.py::TestACampaignFreezesTheEstateAtAnInstant` pins this
against a real estate: Alice is widened from read to write on Friday, and a campaign cut
against Monday still shows the read. A campaign cut against Friday shows the write, so the
first result is a freeze rather than a stale read.

---

## 4. An item is a grant, not an entry — and not effective access

An item describes one **`(principal, target)` pair** and carries *every* access-control entry
that creates it. Two decisions worth stating:

**Not one item per ACE.** An allow and a deny on the same principal and folder are one
relation. Splitting them would ask somebody to certify half a grant, and let the two halves
be answered differently.

**Not effective access.** An entry is what an administrator can actually remove, so a revoke
decision maps onto a change somebody can make. The full effective answer for the same pair —
inherited rights, nested group membership, deny ordering — is still available and *also*
frozen, because `HistoryService.effective_access_at(subject, resource, baseline_at)` is a pure
function of the same versions. Nothing is lost by not copying it into the item; what is
gained is that the item set stays an enumeration of removable things.

### Focus

A grant has two ends, so a campaign must say which one it enumerated — that is what it claims
to be complete about.

* **`resource`** — scopes select shares and directories (`server`, `share`,
  `directory_tree`). Claims: every principal named on them.
* **`principal`** — the scope selects one principal (`principal`). Claims: every place that
  principal is named.

Neither claims the other, and a campaign reporting both would be claiming coverage it never
enumerated. A scope that does not match the focus is refused at creation.

Both selections are single indexed reads, using the two columns Phase 7A put on
`object_versions`: `container_key` for the resource focus, `related_key` for the principal
focus.

### Every exclusion is counted

By default a campaign skips **inherited** entries (they cannot be removed where they sit; the
fix is on the ancestor) and **well-known trustees** (`SYSTEM`, `BUILTIN\*` — they are on
nearly every descriptor). Both defaults are right and both narrow coverage, so the counts are
stored on the campaign and reported with its status. "47 of 47 certified" must never be
readable as coverage of everything.

### Certainty travels with the evidence

Every grant carries the `Certainty` it had at the baseline — `observed`, `inferred`,
`backfilled`, `unobserved` — and the item carries the weakest of them, the same rule
`AsOfAccess` applies. Certifying state the Phase 7 migration reconstructed, as though it had
been watched, is the one way this feature could make an audit *worse*.

Certainty is deliberately **not** part of the evidence digest: it is a property of the
question asked, derived from the version's interval and the instant. Digesting it would make
an item's digest change when a later scan confirmed the very same grant.

### Ceilings refuse rather than truncate

A scope selecting more than 20,000 targets, or producing more than 5,000 items, is **refused**
with a message naming the count and the ceiling. A campaign that quietly dropped the rest
would report complete coverage of a scope it never showed a reviewer.

---

## 5. Authorization: three capabilities and a second gate

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

Three things this table says on purpose:

**A plain viewer holds none of it.** A decision rationale can name a person and say something
about them — "leaver", "should never have had this" — that no access-control list ever would.
Reading attestations is a strictly wider disclosure than reading the estate.

**`manage` does not include `review`.** Whoever chooses the questions does not also give the
answers. A single account able to do both can decide what it will be asked and then
rubber-stamp it, and the audit trail would show a complete, compliant campaign. Somebody who
must do both is given both roles — a visible assignment rather than a silent property of one.

**The platform administrator runs no review.** `admin` configures the server and may replay a
collector payload. Running an access review is a compliance function. See ADR-0029, which also
states the limit of this control.

### The assignment is the second gate

Holding `governance:review` admits a request to the route. It grants authority over **no
particular item**. An item may be answered only by the reviewer it was *assigned* to, checked
against the database on every decision, with three distinct refusals:

* assigned to nobody — a governance administrator must assign a reviewer first;
* assigned to somebody else;
* your assignment has been revoked (your earlier decisions stay recorded).

An item attached to no assignment can never be decided, so the campaign status reports
`unassigned_items` **separately from** the pending total: a campaign with three thousand of
them is stuck, not slow.

---

## 6. The record is append-only, and the database enforces it

### Decisions

A changed mind writes a **new** decision and supersedes the old one. "Certified on the 3rd,
revoked on the 9th" and "revoked on the 9th" are different histories, and only the first lets
an auditor ask what changed in between.

Three mechanisms, none of them a convention:

* `ux_review_decisions_current` — unique on `(item_id) WHERE superseded_at IS NULL`. At most
  one current decision per item, enforced by the database.
* `adg_review_decisions_append_only` — a trigger that refuses every `DELETE`, and every
  `UPDATE` except marking a still-current decision superseded. It compares the row before and
  after with the two supersession columns removed, so *a column added later is immutable by
  default*.
* `superseded_by_decision_id` is **`DEFERRABLE INITIALLY DEFERRED`**, and that is load-bearing.
  The partial unique index permits one current decision, so the supersession must be written
  *before* the successor row is inserted — and an immediate foreign key would refuse a
  reference to a row that does not exist yet. Deferring it lets both invariants hold at once:
  neither is relaxed, and both are true at commit.

Decision history is ordered by the **supersession chain**, not by `decided_at`. Two decisions
can share an instant, and a tie broken by a random surrogate id would render a reviewer's
change of mind in the wrong order — the one thing that list exists to show correctly.

### The audit trail

Every governance act writes one event, in the same transaction as the act. Two properties,
enforced differently because they fail differently:

**Append-only** — `adg_governance_audit_events_immutable` raises on every `UPDATE` and
`DELETE`. That stops the accident: a repository method written in a later phase that thinks
it may tidy a row.

**Tamper-evident** — each event carries the digest of the previous event in its chain, and its
own digest covers its content *plus* that link. Altering or removing a past event invalidates
every digest after it, and `verify_chain` reports which event broke and in which of three ways
(edited / replaced-or-inserted / gap).

One chain per campaign, plus an `owners` chain for ownership events. Per-campaign because a
campaign is the unit an auditor examines and exports, and because two campaigns worked on at
once should not contend for one head. The chain head is in the campaign's status response —
it is the value worth recording *outside* the database.

**What the chain is not.** It is not a signature. Anyone able to rewrite rows can recompute
every digest after the one they changed, and the result verifies. What it buys is that a
*quiet* edit is impossible: the edit has to rewrite every later event, and the head recorded
in an export or a ticket no longer matches.
`tests/governance/test_audit_chain.py::TestTheLimitOfWhatTheChainProves` states this as a
test so nobody later reads a green verification as proof of more than it is.

`TRUNCATE` is deliberately **not** blocked — a `BEFORE TRUNCATE` trigger would make the tables
impossible to clear, which every environment reset needs, and a truncation is not a quiet edit.

What is digested, and what is not: `actor_subject` and `actor_roles` are (identity, and the
authority held *at the time*); `actor_display_name` is not — chaining it would make an audit
trail fail verification because somebody got married.

---

## 7. Remediation is proposed, never performed

A `revoke` or `modify` decision may carry a `RemediationProposal`: an action, the target, the
principal, and **the exact `ace_keys` the frozen evidence named**. Built from the evidence
rather than from current state, so a proposal cannot silently retarget itself between the
review and the change window.

ADG performs none of it. The application is read-only toward the estate (SECURITY.md),
`Capability.REMEDIATION_EXECUTE` is held by no role, and nothing in `app/governance` can reach
a Windows API. The API response carries a constant `note` saying so, so a client cannot render
a proposal as an action taken.

A proposal may not be attached to a **certified** item: a removal instruction for a grant
somebody just certified is a contradiction, and storing it would leave two answers with
nothing to choose between.

---

## 8. How "a decision cannot rewrite an observation" is guaranteed

Two tests, covering different things.

**Structurally** — `tests/governance/test_isolation.py` walks every module in
`app/governance` and fails if any `insert()`, `update()` or `delete()` names a table holding
collected state. It proves no such path *exists*, whether or not anyone thought to call it.
It is conservative on purpose: a write through a variable is refused rather than guessed at,
and it also checks that nothing here imports `app.ingestion` or `app.history.writer` (the
only code permitted to write observations and to record an absence).

**Behaviourally** — `tests/db/test_governance_isolation.py` digests every collected table,
runs a whole campaign (created, generated, assigned, activated, certified, revoked, a decision
superseded, a proposal raised, an owner recorded and withdrawn, closed), and requires every
digest identical. The table list comes from the schema rather than a literal, so a table added
by a later phase is covered by default. It also asserts the digest *moves* when a collected
row is edited — a digest that never changes would prove nothing.

---

## 9. Storage notes

**Evidence is JSONB on the item.** Written once, read whole, never queried one entry at a
time — and an item *is* its evidence, so the two must not be able to exist apart
(`jsonb_array_length(grants) > 0` is a check constraint).

**The stored blob is wider than the digested form.** The digest covers the grant; the blob adds
the two instants the entry was watched over and its certainty. Exactly the relationship
`object_versions.state` has to its own digest, and for the same reason: those fields are
provenance, not part of the grant.

**`review_items.current_decision_id` is deliberately not a foreign key.** The two tables would
then reference each other, and a circular constraint makes the insert order of a decision and
its item's update a deadlock waiting to be found. The decision row is the authority;
`ux_review_decisions_current` guarantees there is at most one row the pointer could name.

**The vocabulary is written down three times, and a test keeps them equal.**
`app/domain/governance.py` holds the enums; `app/models/schema.py` generates check constraints
from them (except `CERTAINTY_VALUES`, which it spells out because `app.history.model` imports
the schema); `0008_governance_model` spells out all of them because a migration must keep doing
what it did on the day it ran. `tests/governance/test_schema_vocabulary.py` is the drift guard,
and is the only reason either duplication is safe. **Adding an enum value means a new
migration, never an edit to 0008.**

---

## 10. What this phase does not do

* **No UI.** No frontend file was touched.
* **One reviewer per item.** An assignment is exclusive; there is no dual control or
  four-eyes approval, and no delegation of a single item.
* **No notifications, no reminders, no scheduler.** Nothing tells a reviewer they have a
  queue or that it is overdue.
* **No membership review.** Items are grants on an ACL. "Who is in this group" is a real
  review question and is not one of them yet.
* **No campaign templates or recurrence.** Each campaign is created explicitly.
* **Principal focus is a direct trustee match**, not the effective closure through group
  membership. Deliberate: access reached through a group is not remediable at the resource,
  and expanding would make the item count a function of the graph's depth.
* **Proposals are never exported anywhere.** `RemediationStatus.EXPORTED` exists in the
  vocabulary and nothing sets it.
