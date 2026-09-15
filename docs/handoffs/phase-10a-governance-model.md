# Handoff — Phase 10A (`phase-10/01-governance-model.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-07a-history-model.md](phase-07a-history-model.md) — the last
phase whose work was committed when this one started. See *Concurrency* below: other phases
were in flight in the same tree throughout.
**Collector contract version after this phase:** unchanged by this phase.
**`docs/contracts/v1/openapi.json`:** regenerated. **15 paths / 18 operations added**, all
under `/api/v1/governance`. No existing path was changed.

---

## Scope completed

ADG can now record **what a person concluded** about the access it observed — and it keeps
that strictly apart from the observation, which is the whole point of the phase.

1. **Eight tables** for owners, campaigns, scopes, assignments, items, decisions, remediation
   proposals and an audit trail.
2. **A campaign is frozen against an instant** and is a pure function of its own row, so it
   is reproducible months later and a `verification` endpoint proves it by re-running the
   same generation.
3. **Items are grants**, resource-centric or principal-centric, each carrying every
   access-control entry that creates it as frozen evidence — with the `object_versions` row
   each came from and the certainty it had at the baseline.
4. **Attestations are append-only.** A changed mind supersedes; a database trigger refuses
   every other update and every delete.
5. **The audit trail is immutable and hash-chained per campaign**, so a quiet edit is not
   possible — and the honest limit of that claim is documented and pinned by a test.
6. **Three capabilities and two roles**, with `governance:manage` deliberately excluding
   `governance:review`, plus a second gate: an item may be answered only by the reviewer it
   was **assigned** to.
7. **Remediation is proposed, never performed.**
8. **18 API operations**, one migration, and **257 tests** attributable to this phase.

---

## What the model is, in six sentences

A **campaign** names an instant and generates its **items** from the versions of
`object_versions` that covered it, so a reviewer certifies what was true when the campaign was
cut and that statement stays true however the estate moves on. An **item** is one
`(principal, target)` grant with every entry that creates it copied in as evidence, digested,
and tagged with how firmly it was known at that instant. A **decision** is append-only and
attributable; changing one's mind writes a new row and supersedes the old, and the database
enforces exactly one current decision per item. Holding `governance:review` admits a request
and grants authority over no particular item — the **assignment** does that, checked against
the database every time. A `revoke` decision produces a **remediation proposal**, which is
ADG's record of a change somebody might make in Windows; ADG makes none of them, and no code
path in `app/governance` can write a collected table. Every governance act writes one
**audit event** in the same transaction, chained to the one before it.

Full treatment: [`docs/architecture/governance-model.md`](../architecture/governance-model.md).

---

## Files and modules added or materially changed

### Added — the governance layer

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/domain/governance.py` | 198 | The ten closed value sets. Pure, so `app/models/schema.py` can generate check constraints from them without an import cycle |
| `backend/app/governance/model.py` | 697 | Values, invariants, the campaign state machine, the evidence and snapshot digests, every rule with a sentence attached |
| `backend/app/governance/audit.py` | 250 | The event, its digest, and the chain verification — what it detects and what it does not claim |
| `backend/app/governance/generation.py` | 280 | The item-generation policy. Pure, hence reproducible |
| `backend/app/governance/repository.py` | 1,488 | Every query. Baseline reads over `object_versions` (read-only); governance writes (governance tables only) |
| `backend/app/governance/service.py` | 1,086 | The workflow, the assignment gate, the audit append, the transaction boundary |
| `backend/app/api/governance.py` | 1,252 | 18 operations, each declaring its capability at the route |
| `backend/app/governance/__init__.py` | 47 | The four rules and where each is enforced |
| `database/migrations/versions/0008_governance_model.py` | 699 | Eight tables, their indexes and constraints, and two triggers |

### Changed

| File | What |
| --- | --- |
| `backend/app/models/schema.py` | The eight tables, `GOVERNANCE_DIGEST_LENGTH`, `SUBJECT_LENGTH`, `CERTAINTY_VALUES` |
| `backend/app/domain/__init__.py` | Re-exports the governance vocabulary |
| `backend/app/auth/roles.py` | Three capabilities, two roles, and the note on why `admin` grants neither |
| `backend/app/auth/dev_users.py` | `reviewer` and `governance` development accounts — **one role per account**, so the separation of duties is visible on a developer's own screen |
| `backend/app/api/__init__.py` | Includes the governance router |
| `backend/app/main.py` | Maps `GovernanceNotFound`/`Conflict`/`Forbidden` and the two ceiling errors onto 404/409/403/409 |
| `backend/tests/api/test_authorization.py` | 18 route entries, three path parameters, the two new roles in the sweep |
| `backend/tests/auth/test_roles.py`, `test_dev_users.py` | The role table, written out literally as that file's convention requires |
| `backend/tests/db/conftest.py` | `alembic upgrade heads`, not `head` — see *Concurrency* |
| `README.md`, `.env.example` | The access-review section; the two new development accounts |
| `SECURITY.md` | "Later governance phases may *propose* remediation" now says what this phase actually does, and what remains out of scope |

### Documentation

* `docs/architecture/governance-model.md` — the model, the tables, the two gates, the storage
  notes, and §10: what this phase deliberately does not do.
* **ADR-0028** — governance is metadata *about* observations, never an observation.
* **ADR-0029** — running a review and answering one are separately held.
* **ADR-0030** — a review campaign is a pure function of a baseline instant.

---

## Design decisions worth knowing

### A campaign is a pure function of its own row

`items = generate(focus, scopes, options, grants_at(baseline_at))`. Every input is a column;
`grants_at` reads `object_versions` and nothing else — no current-state table, no clock, no
session. Since `object_versions` is append-only and a version's interval is fixed once
written, regenerating a campaign months later reproduces it exactly.

That is what makes `GET /campaigns/{id}/verification` meaningful, and it is a **re-run**
rather than a second implementation: `generate()` and `verify_campaign()` both call
`_generate_from_baseline`. A separate checker would be a second description of the rules, and
the first time the two disagreed nobody would know which was right — the same argument
Phase 7A makes for point-in-time access using the live engine.

Divergence is therefore a finding, not noise. The only ways a baseline can come to produce a
different answer are history edited directly, restored from a partial backup, or pruned by
retention past the baseline. The response says that in prose rather than returning three
numbers.

### Items are grants, not entries, and not effective access

An item is one `(principal, target)` pair carrying **every** entry that creates it. An allow
and a deny on the same folder are one relation; splitting them would ask somebody to certify
half a grant and let the halves be answered differently.

They are entries rather than effective access because an entry is what an administrator can
actually remove, so a revoke maps onto a change somebody can make. The effective answer is
not lost — `HistoryService.effective_access_at` over the same instant is a pure function of
the same versions, so it is equally frozen — it is simply not what an item *is*.

### Every exclusion is counted

Inherited entries and well-known trustees are excluded by default, and both counts are stored
on the campaign and reported with its status. "47 of 47 certified" must never be readable as
coverage of everything. The options are columns rather than a blob so a reader can see them
without parsing JSON.

### Two independent authorization gates

`governance:manage` and `governance:review` are disjoint (ADR-0029), and the capability is
only the first gate. The second is the assignment, checked against the database on every
decision with three distinct refusals — assigned to nobody, assigned to somebody else, your
assignment was revoked — because those send an operator to three different places.

An item attached to no assignment can never be decided, so campaign status reports
`unassigned_items` **separately from** the pending total: a campaign with three thousand of
them is stuck, not slow.

### The deferred foreign key is load-bearing

`ux_review_decisions_current` permits one current decision per item, so a supersession must be
written **before** the successor row exists — and an immediate foreign key refuses a reference
to a row that does not exist yet. `superseded_by_decision_id` is `DEFERRABLE INITIALLY
DEFERRED`, which lets both invariants hold at once: neither is relaxed, and both are true at
commit. **Found by a test**, not by design: the first version wrote them in the other order
and PostgreSQL refused it.

### The decision-immutability trigger lists the mutable columns, not the immutable ones

`adg_review_decisions_append_only` compares `to_jsonb(OLD) - 'superseded_at' -
'superseded_by_decision_id'` with the same of `NEW`. A column added to `review_decisions` in a
later phase is therefore immutable **by default**, which is the safe direction for a rule
nobody will remember to update.

### The audit chain, and what it is not

Each event carries the previous event's digest, and its own digest covers its content plus
that link. Editing or removing a past event invalidates every digest after it, and
`verify_chain` says which event broke and in which of three ways.

It is **not a signature**. Anyone able to rewrite rows can recompute every digest after the
one they changed, and the result verifies. What it buys is that a *quiet* edit is impossible.
`tests/governance/test_audit_chain.py::TestTheLimitOfWhatTheChainProves` asserts exactly this,
so that nobody later reads a green verification as proof of more than it is.

`TRUNCATE` is deliberately not blocked: a `BEFORE TRUNCATE` trigger would make the tables
impossible to clear, which every environment reset needs, and a truncation is not a quiet edit.

### Three defects the implementation found in itself

* **Nothing committed.** The service wrote campaigns, items and decisions and never called
  `commit()`. The request scope does not commit either (`app/api/deps.py`), so every write was
  rolled back the moment the response was sent. Invisible to the service-level tests, which
  share one session and never cross a request boundary; caught the instant the HTTP suite ran
  — eighteen failures at once, all `404` on the request after a `201`. The service now owns
  the transaction and commits once per mutating method, after its audit event, the same shape
  `IngestionService` has.
* **Decision history ordered by a random tiebreak.** `decisions_for_item` ordered by
  `(decided_at, decision_id)`. Two decisions can share an instant, and the tiebreak was a
  random UUID — so a reviewer's change of mind rendered in the wrong order roughly half the
  time. Now linearized by following `supersedes_decision_id`, which is a total order and needs
  no extra column.
* **A supersession that could not be written.** See the deferred foreign key above.

---

## Schemas and contracts

**No collector contract changed.** No observation kind, field or scope was added.

**Eight tables added**, with these structural guarantees:

* **No foreign key from any governance table to any collected table.** A campaign names its
  target by string key, exactly as the ACL tables name each other. Asserted by
  `tests/governance/test_schema_vocabulary.py::test_no_governance_table_has_a_foreign_key_to_a_collected_one`.
* `ux_review_decisions_current` — unique on `(item_id) WHERE superseded_at IS NULL`.
* `uq_review_items_natural_key` — one item per `(campaign, target_kind, target_key,
  principal)`.
* `uq_governance_audit_events_position` — one event per `(chain_key, chain_index)`.
* `ux_resource_owners_active` / `ux_review_assignments_active` — partial unique indexes over
  `coalesce(...)`, so one party cannot be recorded twice under two spellings.
* Check constraints for every invariant, including `jsonb_array_length(grants) > 0` (an item
  *is* its evidence) and `status NOT IN ('active','closed') OR generated_at IS NOT NULL` (a
  campaign cannot open before it is frozen).

**Two triggers**: `adg_governance_audit_events_immutable` and
`adg_review_decisions_append_only`.

**18 API operations added** under `/api/v1/governance`; `openapi.json` regenerated.

**No new settings.** The two ceilings (5,000 items, 20,000 scope targets) are module constants,
matching how Phase 7A declares `MAX_MEMBERS_AT`.

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| Backend, hermetic | `.\scripts\backend-test.ps1` | **5,091 passed**, 10 skipped, 886 deselected, 39.5s |
| Backend, with PostgreSQL | `backend-test.ps1 -Smoke`, against an isolated database (see *Concurrency*) | **5,975 passed**, 1 failed, 10 skipped, 1 xfailed, 41m32s — the one failure is **not this phase's** and did not reproduce; see below |
| Backend lint | `ruff check .` / `ruff format --check .` | **passed** — 299 files |
| Backend types | `mypy app tests` | **7 errors, none in this phase's files** — all in `app/changes/` and `tests/db/test_change*`, another phase's in-flight work |
| Collectors | `.\scripts\collector-test.ps1` | not run — **no collector file was touched** |
| Frontend | `.\scripts\frontend-check.ps1` | not run — **no frontend file was touched** |

### The one smoke failure, and why it is not attributed to this phase

`tests/db/test_ingestion.py::TestBatchIdempotency::test_a_replayed_batch_is_acknowledged_but_not_applied`
failed once, in the 41-minute full run, and **did not reproduce**: it passes alone (1/1) and
with its own file (25/25) against the same database. What is known rather than guessed:

* It covers ingestion. This phase touches no ingestion code.
* `app/ingestion/service.py`, `app/ingestion/plan.py` and `app/api/scan_runs.py` were last
  modified at 17:13, ten minutes **before** the run began at 17:23, so it was not a module
  edited underneath the run.
* The run overlapped another session's test activity on the same PostgreSQL server, and a
  sibling database (`adg_test`) was observed with one backend `idle in transaction` and three
  `TRUNCATE`s blocked on `Lock/relation` for 42–59 minutes.

The likeliest explanation is contention between the two sessions on one server, but that is
**not proven** and is recorded as a hypothesis rather than a conclusion. Whoever runs the
suite on a quiet machine should confirm it — a genuinely flaky idempotency test would be worth
knowing about on its own.

**257 tests are attributable to this phase** — 151 hermetic (`tests/governance/`, plus the
role and dev-user additions) and 106 requiring PostgreSQL (`tests/db/test_governance_*.py`).

**No existing test was weakened or deleted.** Five were **updated**, each because this phase
changed the fact it asserted: the role table (`auditor` gained `governance:read`), the active
role set, the default development accounts, the `/auth/config` account list, and the route
capability table. All five are written out literally rather than recomputed — that file's
convention, so a change to who can do what has to be made twice, on purpose.

### What each suite covers

| File | What it pins |
| --- | --- |
| `tests/governance/test_model.py` | What a decision must say; the lifecycle, including that a closed campaign is final; the baseline must be a past, aware instant; scopes must match the focus; what the digests do and do not cover; an answer is as sound as its weakest fact |
| `tests/governance/test_generation.py` | An item is a grant not an entry; generation is order-independent; every exclusion is counted and the reason is deterministic; an unparsable SID is reviewed rather than dismissed; the ceiling **refuses** rather than truncates |
| `tests/governance/test_audit_chain.py` | Each of the ways a chain can break, located and explained; what is digested (subject, roles) and what is not (display name, time zone); **and the limit** — a wholesale rewrite verifies, which is why the head digest matters |
| `tests/governance/test_isolation.py` | No module in `app/governance` writes a collected table, from the syntax tree; no write is opaque to the check; nothing imports `app.ingestion` or `app.history.writer`; **and that the guard itself catches a planted violation** |
| `tests/governance/test_schema_vocabulary.py` | The vocabulary written in three places agrees; the migration's frozen literals match the enums; no governance table references a collected one; every timestamp is timezone-aware |
| `tests/db/test_governance_workflow.py` | A campaign end to end against a two-scan estate: the freeze, both focuses, the lifecycle, who may answer, supersession, the triggers biting, verification, status, remediation, ownership |
| `tests/db/test_governance_api.py` | Every role driven over HTTP: a viewer reaches nothing; the separation of duties both ways; the assignment gate; the whole workflow; the requests the API refuses |
| `tests/db/test_governance_isolation.py` | Every collected table digested before and after a whole campaign, identical — **and the digest proven to move** when a collected row is edited |

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| A campaign is reproducible against its baseline snapshot | `test_governance_workflow.py::TestCampaignVerification` — untouched campaign reproducible; later scans do not break it; a deleted item, and altered evidence, each reported in their own bucket |
| Review decisions cannot rewrite source permission observations | `test_governance_isolation.py` (every table, digested before and after) **and** `tests/governance/test_isolation.py` (no such path exists) |
| Every decision is attributable and auditable | `TestTheAuditTrail` — every act recorded in order, the roles held at the time, the evidence digest in the event, a superseded decision leaving two events, the triggers refusing edits, and a tampered row breaking the chain at a located index |
| RBAC prevents a normal viewer from creating/completing governance decisions | `test_governance_api.py::TestAViewerHoldsNoGovernanceAuthority` — create, list and decide all 403 — plus the capability sweep in `tests/api/test_authorization.py` |

---

## Known limitations

1. **No UI.** No frontend file was touched. The API is complete; nothing shows a campaign.
2. **One reviewer per item.** An assignment is exclusive, and assigning a narrower scope
   **reattaches** items from a wider one. There is no dual control, no four-eyes approval, and
   no delegation of a single item.
3. **Nothing tells a reviewer they have a queue.** No notification, no reminder, no scheduler.
   `overdue` is computed on read and reported; nobody is informed.
4. **No membership review.** Items are grants on an ACL. "Who is in this group" is a real
   review question and is not one of them.
5. **Principal focus is a direct trustee match**, not the effective closure through group
   membership — deliberate (access reached through a group is not remediable at the resource),
   and it means a principal-focused campaign does **not** enumerate everything that principal
   can reach.
6. **Retention can strand a campaign.** `ADG_HISTORY_RETENTION_DAYS` can prune versions past an
   open campaign's baseline, after which verification correctly reports it not reproducible.
   Nothing stops it.
7. **Proposals go nowhere.** `RemediationStatus.EXPORTED` exists in the vocabulary and nothing
   sets it; there is no export, and no endpoint withdraws a proposal.
8. **Ceilings are absolute.** A scope over 20,000 targets or 5,000 items is refused with no
   way to say "yes, really" — narrowing the scope is the only route.
9. **The audit chain is not a signature.** Stated in full in the architecture document, in the
   module docstring, and as a test.
10. **No campaign templates or recurrence.** Every campaign is created explicitly.

---

## Security and privilege assumptions

* **Unchanged for collectors.** No collector was modified and no new permission is required.
* **ADG stays read-only toward Windows.** Nothing in this phase can change an ACL, a group, or
  an owner. `Capability.REMEDIATION_EXECUTE` remains reserved and held by no role;
  `tests/auth/test_roles.py::test_no_role_may_write_the_estate` still passes unchanged.
* **A new and wider disclosure exists.** Decision rationales are free text written about named
  people. `governance:read` is therefore **not** granted to `viewer`, and the rationale column
  is a new category of personal data in the database and in any backup of it — the same
  retention question `docs/operations/mvp-runbook.md` §6 raises for the log stream.
* **Separation of duties is enforced in the backend**, in one table, and asserted by tests
  that call the routes as each role. Its limit is stated in ADR-0029: anyone with the database
  credentials can do anything, so it guards against accident and casual misuse rather than
  against a determined operator.
* **Two new roles must be assigned before governance is usable at all.** Nothing is enabled by
  upgrading.
* **The reviewer identity is an authentication subject, not a SID.** An ADG reviewer is a
  person who signs in to ADG; the Windows principals in a campaign are the *subjects of* the
  review. Conflating them would allow an attestation whose reviewer is a group, which is
  attributable to nobody.

---

## Migration and compatibility notes

* **`0008_governance_model` is additive and empty.** It creates eight tables and writes no
  rows: there is nothing to backfill, because no decision has been made yet. It is fast on any
  estate.
* **It installs two trigger functions and two triggers.** Both are PostgreSQL-specific, like
  everything else in this schema.
* **`downgrade()` drops the tables and destroys every attestation ever recorded.** The
  docstring says so plainly rather than leaving it to be discovered.
* **The revision is anchored on `0007_history_model`**, the last *released* revision — not on
  the concurrent work described below. See *Concurrency*.
* **The migration's enum literals are frozen** and must not be edited: adding a value to a
  governance enum means a **new** revision that alters the constraint.
  `tests/governance/test_schema_vocabulary.py` fails the moment they disagree.
* **No existing column, index or constraint was altered.** Every Phase 0–7 query is unaffected.

---

## Concurrency — read this before judging the tree

**Another Claude session was implementing other phases in this same working tree for the whole
of this one**, adding `app/changes/`, `app/risk_engine/`, `app/simulation/`,
`app/domain/incremental.py`, contract version 1.4, and five migrations. Consequences a reader
should know about:

1. **The tree is not committed.** See *`git status --short`*. Committing would have captured a
   half-written state of somebody else's phase in shared files — `app/api/__init__.py`,
   `app/models/schema.py`, `app/domain/__init__.py`, `app/contracts/v1/common.py` all carry
   both sessions' edits. The decision was to leave the tree coherent and uncommitted rather
   than to commit a mixture.
2. **The migration graph has multiple heads.** Five revisions now claim `0007_history_model` or
   its descendants as a parent across the two sessions, and the other session has begun adding
   merge revisions. This phase's revision is `0008_governance_model`, parent
   `0007_history_model` — anchored on the last *released* revision, which is the only stable
   choice while a sibling branch is still moving. **Whoever merges must add a merge revision
   including it.**
3. **`tests/db/conftest.py` now runs `alembic upgrade heads`, not `head`.** With more than one
   branch off a released revision, `head` refuses to choose and every smoke test fails for both
   sessions. `heads` is identical once the branches are linearized, so it needs no reverting.
4. **Smoke tests were run against an isolated database.** Both sessions' suites truncate every
   table between tests, so sharing `adg_test` made runs fail at random — a row written by one
   session's test vanishing mid-test in the other's. The governance suites were run with
   `ADG_DATABASE_URL=...adg_gov`, giving `adg_gov_test`. **Two sessions must not share a test
   database**; the databases `adg_gov`/`adg_gov_test` and `adg_gov_check` exist locally and can
   be dropped.
5. **Remaining lint/type failures are not this phase's.** `mypy` reports 7 errors, all in
   `app/changes/` and `tests/db/test_change*`.

---

## Prerequisites for the next prompt

1. **Linearize the migration graph.** Add a merge revision covering `0008_governance_model`
   and the other branches, or renumber. `alembic heads` should report one head before anything
   ships.
2. **Build the governance UI.** The API is complete and typed and `openapi.json` is current.
   The screens the model implies: a campaign list, a reviewer's queue, an item with its frozen
   evidence and its certainty shown honestly, and a campaign status page that puts
   `unassigned_items` and `excluded_counts` where they cannot be missed.
3. **Surface "decided but not yet remediated".** The data exists — a decision, a proposal, and
   later observations of the same entry — and nothing joins them. This is the most valuable
   thing the model currently cannot show, and it is the natural answer to limitation 7.
4. **Protect a campaign's baseline from retention** (limitation 6), or make retention refuse to
   prune versions an open campaign depends on.
5. **Decide whether membership review belongs here** (limitation 4). The item model extends to
   it — a `(member, group)` pair with the edge as evidence — but the target vocabulary and the
   remediation actions would both need a third case.
6. **Measure generation on a large estate.** It has been exercised against fixtures of a few
   dozen rows. The ceilings are guesses informed by nothing; somebody should generate a
   campaign over a real share and time it before either is trusted.
7. **Consider notifications** (limitation 3). A review nobody is told about is a review that
   does not happen.

---

## Intentionally deferred

* **Any UI.** The prompt's required work names a model, APIs and RBAC.
* **Dual control and delegation.** One reviewer per item is a real limitation, documented
  rather than half-built.
* **Export of remediation proposals.** The vocabulary reserves the status; nothing sets it.
* **Registering the governance responses as published JSON Schemas** in
  `docs/contracts/v1/derived/`. That directory is for answers ADG *derives from observations*;
  a campaign is a record, not a derivation, and adding it there would blur the one distinction
  this phase exists to keep sharp.

---

## `git status --short`

The tree is **uncommitted, and deliberately so** — see *Concurrency* §1. Files belonging to
this phase are listed below; everything else in `git status` belongs to the other session's
phases and is not this phase's to commit.

```
 M backend/app/api/__init__.py          (shared; also carries the other session's edits)
 M backend/app/auth/dev_users.py
 M backend/app/auth/roles.py
 M backend/app/domain/__init__.py       (shared)
 M backend/app/main.py                  (shared)
 M backend/app/models/schema.py         (shared)
 M backend/tests/api/test_authorization.py   (shared)
 M backend/tests/auth/test_dev_users.py
 M backend/tests/auth/test_roles.py
 M backend/tests/db/conftest.py         (shared; head -> heads)
 M docs/contracts/v1/openapi.json       (shared; regenerated, covers both sessions' routes)
 M docs/decisions/README.md             (shared)
 M README.md                            (shared)
 M .env.example
 M SECURITY.md
?? backend/app/api/governance.py
?? backend/app/domain/governance.py
?? backend/app/governance/
?? backend/tests/governance/
?? backend/tests/db/test_governance_api.py
?? backend/tests/db/test_governance_isolation.py
?? backend/tests/db/test_governance_workflow.py
?? database/migrations/versions/0008_governance_model.py
?? docs/architecture/governance-model.md
?? docs/decisions/0028-governance-is-metadata-about-observations.md
?? docs/decisions/0029-running-a-review-and-answering-one-are-separate.md
?? docs/decisions/0030-a-campaign-is-a-function-of-a-baseline-instant.md
?? docs/handoffs/phase-10a-governance-model.md
```

**ADR numbering:** this phase's decisions were first written as 0020–0022 and renumbered to
**0028–0030** when the other session's ADR-0020 (simulation) and 0023–0027 appeared in the
same tree. Every reference was updated with them.
