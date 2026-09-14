# Handoff — Phase 7A (`phase-07/01-history-snapshots.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-06d-mvp-hardening.md](phase-06d-mvp-hardening.md)
**Collector contract version after this phase:** `1.3` — unchanged.
**Derived-response contract:** `1.0` — unchanged.
**`docs/contracts/v1/openapi.json`:** unchanged. **No route was added, removed or altered.**

## Scope completed

ADG can now answer *what was true on the 3rd* and *when did this stop being true*, and it
can answer them without changing a single current-state query.

1. **`object_versions`** — one row per state an object was observed to hold, with the
   interval it was observed over, for all seven contract kinds.
2. **A third timestamp.** A version records when a state was first observed, when it was
   **last confirmed**, and when it was contradicted — so a change is reported as the window
   it happened in rather than as the day somebody happened to look (ADR-0019).
3. **Tombstones.** A reconciled scope records absence as a version with no state, so *"ADG
   knows this was gone on Tuesday"* stays distinguishable from *"ADG has nothing for
   Tuesday"*. Nothing is deleted.
4. **Four independent guards on absence**, so a failed, partial, incremental or
   short-delivered run marks nothing absent, and no collector can close rows it is
   structurally incapable of having looked at.
5. **Point-in-time queries** for membership in both directions, the raw SMB and NTFS ACLs,
   resource existence, and effective access — the last of these through **the live engine**,
   not a second implementation.
6. **Certainty on every answer**: `observed`, `inferred`, `backfilled`, `unobserved`.
7. **A migration that backfills one version per existing row** and marks every one of them as
   reconstructed, because that is all the old schema can justify.
8. **Retention hooks** that delete nothing by default and need two separate switches to
   delete anything at all.

**No HTTP surface was added.** See *Intentionally deferred*.

---

## What the model is, in six sentences

A version says: *this object held this state, from `valid_from`, last confirmed at
`last_seen_at`, until `valid_to`.* `is_present` false is a tombstone. `state` is the
current-state row's descriptive columns as JSONB and `state_hash` is the digest that decides
whether the next observation is a change — provenance is stripped before digesting, so
re-reading an unchanged ACL a thousand times extends one version and creates no second one.
At most one version of an object is open at a time and versions never overlap, both enforced
by the database. A change is known to have happened in `(last_seen_at, valid_to]` and nowhere
more precisely, because a collector samples rather than watches. Every point-in-time answer
reports which of the four certainties it carries, and `unobserved` is never rendered as *it
did not exist*.

Full treatment: [`docs/architecture/history-model.md`](../architecture/history-model.md).

---

## Files and modules added or materially changed

### Added — the history layer

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/history/model.py` | 478 | Pure: the version, the change window, certainty, the canonical state and its digest, and every temporal invariant as a check with a sentence attached |
| `backend/app/history/closure.py` | 285 | Pure: the rule table for what a reconciled scope may mark absent, per `(collector, scope kind)` |
| `backend/app/history/bindings.py` | 93 | The one mapping from object kind to storage that the writer, the closure and the readers all use |
| `backend/app/history/writer.py` | 741 | Observations to versions; reconciled scopes to tombstones. The only code that may record an absence |
| `backend/app/history/repository.py` | 563 | The temporal predicate, and the as-of subclasses of the two repositories the access engine takes by injection |
| `backend/app/history/service.py` | 331 | The point-in-time query surface |
| `backend/app/history/retention.py` | 237 | What may eventually be forgotten. Deletes nothing by default |
| `database/migrations/versions/0007_history_model.py` | 339 | The table, its five indexes, and the backfill |

### Changed

| File | What |
| --- | --- |
| `backend/app/models/schema.py` | `object_versions`; `VersionOrigin` and `CloseReason` beside `AliasKind`/`ReferenceKind`; `STATE_DIGEST_LENGTH` |
| `backend/app/ingestion/service.py` | Each `_write_*` folds its own rows into the timeline and returns what that did; `complete_run` runs the closure pass per reconciled scope; `BatchOutcome.history`, `CompletionOutcome.closures` |
| `backend/app/repositories/{membership,resources}.py` | The seven record constructors are now public and take a `RowLike`, so a record rebuilt from a version and one read live come from the same constructor |
| `backend/app/config.py` | `history_retention_days`, `history_retention_enabled`, `history_retention_policy` |
| `README.md`, `.env.example` | The history section, the two invariants, the retention switches |
| `docs/architecture/mvp-capabilities.md` | Limit 3 was "does not compare scans"; it is now "does not compare scans *on screen*" — the data exists, the surface does not |
| `docs/contracts/collector-protocol.md` | "history is retained (Phase 7)" → what actually happens |
| `backend/app/ingestion/{__init__,service}.py`, `backend/app/models/schema.py` | Docstrings that said "Phase 7 will…" now say what it does. One of them — *"Nothing is ever marked absent"* — had become **false**, and a module docstring that lies about the module's most safety-critical property is worse than none |

### Documentation

* `docs/architecture/history-model.md` — the model, the guards, the invariant table, and §9:
  what this phase deliberately did not change.
* ADR-0018 — history is a versioned observation log; current state stays a projection.
* ADR-0019 — a change is recorded as a window, not an instant.

---

## Design decisions worth knowing

### One generic table, not seven mirrors

The temporal rules are identical for an ACE and a principal: what opens a version, what closes
one, what may be inferred absent, what retention may remove. Seven per-kind history tables
would be seven copies of those rules and seven chances for one to drift. `object_versions`
carries `object_kind` — the contract's own `ObservationKind`, so a kind added to the contract
is tracked by default — and stores the descriptive columns as JSONB with a digest.

The two columns a query needs to be indexed on are `container_key` (the object this one is an
entry of: the share for a share ACE, the resource for an NTFS ACE, the group for a membership
edge) and `related_key` (the far end: the member, the trustee). Both are **projections of the
state**, copied from the same row dictionary the current-state upsert writes, so a version's
indexed columns cannot describe a different object from its own blob.

### History is written from the row that was stored, not re-derived from the payload

`HistoryWriter.record` is handed **the same dictionaries the current-state upsert is given**,
inside the same transaction. The timeline of an object and its current state therefore cannot
describe two different things, and a batch that fails halfway leaves neither. This is the same
technique the Phase 6D demo estate used for inheritance: the fixture holds no second copy of
the rule, so it cannot drift from it.

### Point-in-time effective access is the live engine

`HistoricalMembershipRepository` and `HistoricalResourceRepository` are **subclasses** of the
repositories `AccessService` already takes by injection, with their reads redirected through
`valid_from <= T < valid_to`. So the DACL projection for a path nobody read, the
deny-before-allow ordering, the coverage findings for a group nobody enumerated — all of it
applies to a historical answer with not one line of the engine changed. A second engine for
history would be a second implementation of the most safety-critical code in the product, and
the first time the two disagreed nobody would know which was right.

Subclassing has a matching hazard: **an inherited read returns current state**. That is
handled by a test rather than by a convention. `tests/history/test_repository_coverage.py`
walks both base classes and asserts that every public read is either overridden or named in an
explicit list with a note saying why a current-state answer from it is safe. A method added to
a base repository fails the suite until somebody decides which of the two it is.

What the engine cannot know is that its inputs were historical, so that is carried beside it:
`VersionAudit` counts every version the answer read by the certainty it has *at the instant*,
and the answer's certainty is the weakest of them.

### The `domain` scope resolves its own key space

Every other scope key is a value stored on the rows it selects — a host name, a server key, a
share key, a UNC prefix. A `domain` scope key is a DNS name and no row stores it. Deriving a
domain SID by string surgery on `distinguishedName` would work most of the time, and *most of
the time* is not a property to hang deletion on.

The closure instead resolves the key space from **the run's own observations**: the distinct
`domain_sid` values of the principals that run reported. That rests on exactly the authority
the reconciliation itself rests on, and it fails safe — a run that reported no principal with
a domain SID resolves an empty key space and closes nothing.

### ACL entries are closed through their parent

An ACE is not independently enumerable: a collector reads a *descriptor* and the entries come
with it. So the rule for an ACE kind is not a predicate over ACE keys but "every ACE of the
resources this scope selected". That cannot select an ACE whose resource is out of scope and
cannot miss one whose key happens not to look like the scope's. The same relation gives the AD
case its shape: a domain run's edges are the edges of the principals it is authoritative for.

### `starts_with`, not `LIKE`

A `directory_tree` scope naming a subdirectory needs a prefix test, and `LIKE` is wrong twice
over on this data: PostgreSQL's default `LIKE` escape character is a backslash, which is the
separator in every UNC path, and `_` is a `LIKE` wildcard and an ordinary character in a
Windows folder name. A pattern built from a real path would both mis-escape and over-match.
The predicate is `share_key = …` (indexed) **and** `starts_with(resource_key, …)`.

### Two defects the implementation found in itself

* **A JSON `null` is not a SQL `NULL`.** SQLAlchemy's JSON type writes Python `None` as the
  JSON value `null`, which is a *present* value: a tombstone would have satisfied
  `state IS NOT NULL` while claiming the object is absent, and "we have no state" and "its
  state is the null value" would have been stored identically. Caught by
  `ck_object_versions_presence_matches_state` on the first tombstone the suite wrote. The
  column is now `JSONB(none_as_null=True)`.
* **A zero-width version.** Two observations of one object at the *same instant* carrying
  different states would close a version at the instant it opened — a `[t, t)` interval no
  query could ever return, and a unique-constraint violation on `(kind, key, valid_from)`.
  The writer now **overwrites** the open version in that case rather than splitting it, which
  is also what the current-state upsert does with the same pair. Caught by an existing Phase 2
  test replaying two scenarios that describe one SID differently.

---

## Schemas and contracts

**No contract changed.** No route was added, removed or altered; `openapi.json` is
byte-identical; the collector protocol document gained a clarification and no new field.

**One table added**, `object_versions`, with five indexes:

* `ux_object_versions_open` — unique on `(object_kind, object_key)` **where `valid_to IS
  NULL`**. At most one open version per object, enforced by the database rather than by the
  writer being careful.
* `uq_object_versions_identity` — unique on `(object_kind, object_key, valid_from)`.
* `ix_object_versions_container` / `ix_object_versions_related` — the two indexed reads a
  point-in-time query makes.
* `ix_object_versions_closed_at` — partial, for retention.
* `ix_object_versions_last_seen_run`.

Nine check constraints, one per temporal invariant; the table is listed in
`docs/architecture/history-model.md` §8 with what violating each would break.

**Two settings added**: `ADG_HISTORY_RETENTION_DAYS` (0 = keep everything, the default) and
`ADG_HISTORY_RETENTION_ENABLED` (default false).

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| Backend, hermetic | `.\scripts\backend-test.ps1` | **4,286 passed**, 10 skipped, 632 deselected, 32.8s — 4,219 passed / 568 deselected before this phase |
| Backend, with PostgreSQL | `.\scripts\backend-test.ps1 -Smoke` | **4,917 passed**, 10 skipped, 1 xfailed, 7m10s — 4,786 passed before this phase |
| Backend lint and types | `.\scripts\backend-lint.ps1` | **passed** — ruff, ruff format, mypy over `app` and `tests` |
| Collectors | `.\scripts\collector-test.ps1` | not run — **no collector file was touched** |
| Frontend | `.\scripts\frontend-check.ps1` | not run — **no frontend file was touched**, and no contract it types against changed |

**131 tests are attributable to this phase** — 67 hermetic (`tests/history/`) and 64
requiring PostgreSQL (`tests/db/test_history_*.py`). The two deltas account for the
totals exactly: hermetic passed rose by 67 and deselected by 64.

**No existing test was weakened, deleted or corrected.** Every Phase 0–6 assertion holds
unchanged, which is the point of leaving the current-state tables alone.

One caveat on the timing, stated rather than glossed: the full run went from 5m43s to
7m10s, and that is **not** a measurement of what history writing costs. The suite gained
131 tests over the same interval and the machine had unrelated work on it. Ingestion now
issues one extra read plus up to three writes per object kind per batch; isolating that
is prerequisite 4 below.

### What each suite covers

| File | What it pins |
| --- | --- |
| `tests/history/test_model.py` | The digest ignores provenance and nothing else; the interval is half-open; the four certainties; the change window; every invariant, each with the sentence explaining what violating it breaks |
| `tests/history/test_closure_rules.py` | The rule table as *permission*: an SMB run cannot close a directory, a domain run cannot close a local group, a pair with no rule closes nothing, a dangling `ViaParent` is refused rather than silently selecting nothing |
| `tests/history/test_repository_coverage.py` | Every public read of each base repository is overridden as-of or named; the overridden set covers what a resolution calls |
| `tests/history/test_retention_policy.py` | The default keeps everything; a window under 30 days is refused; an enabled policy with nothing to enforce is refused |
| `tests/db/test_history_versions.py` | The write path end to end: open, extend, supersede, tombstone, reaffirm, revive; **four kinds of run that must mark nothing absent**; out-of-order arrival; the database refusing two open versions |
| `tests/db/test_history_queries.py` | A three-day estate reconstructed after the fact: membership both ways, the raw ACLs, existence, effective access, timelines — and the live/as-of divergence, pinned |
| `tests/db/test_history_migration.py` | The migration's **own** `_backfill`, run against an ingested MVP estate with the timeline emptied: every row covered, the interval exactly what the old schema knew, the migration's digest equal to the application's, every answer reporting `backfilled` |
| `tests/db/test_history_retention.py` | Planning writes nothing; a disabled policy refuses rather than no-ops; the open version and the newest closed one survive; current state is untouched |

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| Failed/partial scans cannot falsely remove historical/current objects | `test_history_versions.py::TestNoRunMayMarkAnythingAbsentWithoutReconciling` — four run shapes, parameterized; plus `test_a_run_that_lost_a_batch_is_downgraded_and_reconciles_nothing` and `test_a_stale_run_may_not_tombstone_what_a_newer_one_just_found` |
| Current-state queries still work | The whole pre-existing suite, unchanged and green; plus `test_history_migration.py::test_current_state_queries_are_unaffected_by_the_backfill` |
| Point-in-time raw state can be reconstructed for tested scenarios | `test_history_queries.py::TestPointInTimeRawAcls`, `TestPointInTimeMembership`, `TestExistence`, `TestPointInTimeEffectiveAccess` |
| Migration tests preserve MVP data | `test_history_migration.py::TestTheBackfillPreservesEveryRow` — asserted over *every* row of *every* tracked table, not a sample |

---

## Known limitations

1. **A live answer still counts a grant a reconciled scan proved is gone.** This is the
   important one. Nothing in ADG deletes a collected fact, so `membership_edges` keeps an edge
   that was removed and `ntfs_aces` keeps a superseded entry — an ACE's identity includes its
   mask, so tightening a DACL *adds* a row rather than changing one. Phase 7A records both
   removals in the timeline and does **not** route current-state reads through presence, so
   the live effective-access answer can overstate access where the as-of-now answer does not.
   Pinned by
   `test_history_queries.py::TestPointInTimeEffectiveAccess::test_the_live_answer_still_counts_what_a_reconciled_scan_proved_is_gone`,
   which will fail the day it is fixed and say what changed.
2. **No HTTP surface.** `HistoryService` is reachable from the backend only. Nothing on the
   web application shows a timeline, and `/changes` is still a placeholder.
3. **An older observation that contradicts a newer one is not spliced into history.** It is
   counted as `stale` and reported in `BatchOutcome.history`, not stored. The current-state
   upsert has always discarded such a reading's descriptive columns; history behaving the same
   way is consistent rather than a new inconsistency, but the information is lost.
4. **A backfilled version conceals any change that happened inside its interval.** By
   construction — the old schema kept no evidence either way. Every answer drawn from one says
   `backfilled` rather than claiming to have watched.
5. **JSONB state is not column-typed.** Reconstruction goes through the same record
   constructors the live repositories use, so callers get typed records, but the database
   cannot constrain a version's state the way it constrains the row it came from.
6. **A `(collector, scope kind)` pair with no closure rule closes nothing, silently.** That is
   the safe default and it is the *quiet* kind of safe: a collector reconciling a scope kind
   nobody wrote a rule for will appear to work and will never mark anything absent.
7. **A point-in-time membership answer is bounded at 1,000 direct edges** and reports
   `truncated`; the live listing endpoints page instead.
8. **Retention is operator-invoked.** There is no scheduler, no startup hook and no endpoint.

---

## Security and privilege assumptions

* **Unchanged for collectors.** No collector was modified, no new permission is required, and
  nothing in this phase asks a collector for anything it was not already sending. Absence is
  inferred from the `reconciled_scopes` the protocol has carried since Phase 0B.
* **Absence is the only new destructive-looking capability, and it is guarded four ways** —
  contract model, ingestion service, closure rules, and the writer's `last_seen_at <=
  completed_at` check. Three of the four are enforced before this phase's code is reached.
* **The one genuinely destructive operation is retention**, and it requires two settings, one
  of which defaults to false, and is reachable only by an operator calling it.
  `HistoryRetentionService.apply` refuses outright while the policy is disabled rather than
  doing nothing quietly, because a caller that asked to prune and was silently ignored would
  go on believing the database was being kept in bounds.
* **`object_versions` holds the same data as the current-state tables**, so it carries the
  same disclosure class: SIDs, account names, UNC paths and ACLs. It inherits the same
  retention question the log stream has; see `docs/operations/mvp-runbook.md` §6.
* **No new endpoint, so no new authorization decision.** The capability boundary in
  `app/api/__init__.py` is untouched and its exhaustiveness audit still passes.

---

## Migration and compatibility notes

* **`0007_history_model` is additive.** It creates one table and backfills it. No existing
  column, index or constraint changes.
* **It is not a no-op on a populated database.** The backfill reads every row of all seven
  tracked tables and writes one version each, in Python, in chunks of 2,000 — because
  `state_hash` must be the digest of the *canonical* rendering of the state and PostgreSQL's
  `jsonb` text output is neither sorted nor separated that way. On a large estate this is the
  slowest migration the project has. It streams rather than materializing, and it is
  restartable only by re-running the whole revision.
* **`downgrade()` drops the table.** History is lost; current state is untouched.
* **The revision duplicates the canonicalization** rather than importing
  `app.history.model.state_digest`, because a migration must keep doing what it did on the day
  it ran even after the application moves on.
  `test_history_migration.py::test_every_backfilled_digest_is_the_application_s_own`
  re-derives every digest with the application's function, so the duplication cannot drift
  unnoticed. **If that test ever fails, the fix is a data migration, not an edit to 0007.**
* **The first scan after migrating does not create spurious versions.** A backfilled version
  carries a real digest, so an unchanged object extends it rather than opening a second one —
  verified by the digest-parity test above, which is what makes that true.
* **Ingestion writes more.** One extra read plus up to three writes per object kind per batch.
  The effect on ingestion has **not** been isolated; see the caveat under *Tests run*.

---

## Prerequisites for the next prompt

1. **Route current-state reads through presence.** Limitation 1 is a correctness problem the
   product now has the evidence to fix, and it is the reason to do 7B before anything else.
   The join is against `object_versions` where `valid_to IS NULL AND is_present`; the pinned
   test says which assertion to invert.
2. **Decide the API surface for point-in-time answers.** The service layer is complete and
   typed. Open questions the next prompt must settle: whether `at` is a query parameter on the
   existing routes or a parallel `/history/...` namespace, which capability a historical
   answer requires (it discloses state a principal may no longer have), and how a response
   renders a `ChangeWindow` — **both ends, never a single `changed_at`** (ADR-0019).
3. **`/changes` needs a diff service**, not just a reader. `ObjectTimeline.changes()` gives
   per-object transitions; "what changed between Tuesday and Friday across the estate" is a
   different query and has no index for it yet — `ix_object_versions_closed_at` is keyed on
   `valid_to` alone and would serve a whole-estate scan, not a per-scope one.
4. **Measure the backfill on a large estate.** It has been run against MVP-sized data only.
   Before this ships to an installation with millions of ACEs, somebody should time it and
   decide whether `BACKFILL_CHUNK` and the streaming read are sized right.
5. **Consider surfacing `BatchOutcome.history` and `CompletionOutcome.closures`.** Both carry
   real operator information — how many versions a scan opened, and what each reconciled scope
   marked absent — and neither reaches the Collectors page.

---

## Intentionally deferred

* **The HTTP surface** (see above). Phase 5 built the access engine and Phase 5B its API; this
  phase follows the same rhythm, and the prompt's required work names services, not routes.
* **Filtering current-state reads by presence.** Deliberate, documented, and pinned by a test
  rather than left to be discovered.
* **Any UI.** No frontend file was touched.
* **A diff/change-feed service.** Prerequisite 3.

---

## `git status --short`

```
(clean)
```

Everything above is committed as **`5c9cc6b`** — *Phase 7A: history is a versioned
observation log*. The working tree is clean.

One file in that commit is **not** this phase's work and is named here so the next
reader is not puzzled by it: `backend/tests/contracts/test_smb_collector.py` carried an
uncommitted, formatting-only reflow when this phase started. It is required — the
committed version of that file is not clean under ruff 0.16.7, verified by running
`ruff format --check` against `git show HEAD:...` — so the lint gate was failing on a
dirty tree before this phase and passes on a clean one after it.
