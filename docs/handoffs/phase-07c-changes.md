# Handoff — Phase 7C (`phase-07/03-change-diff-ui.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-07a-history-model.md](phase-07a-history-model.md)
**Collector contract version after this phase:** `1.3` — unchanged by this phase.
**Derived-response contract:** documented, no versioned migration.
**`docs/contracts/v1/openapi.json`:** **regenerated. Five routes added by this phase.** See
*Working alongside another session* — the regenerated document necessarily also carries
fifteen routes belonging to a concurrent session's uncommitted work.

> **Read this first.** Another Claude session was working in this same tree throughout this
> phase, building Phases 8 and 9 (`app/risk_engine/`, `app/governance/`, `app/simulation/`,
> `app/domain/incremental.py`, `app/ingestion/checkpoints.py`). Everything below is scoped to
> Phase 7C's own files, and the section at the end sets out precisely what was and was not
> touched outside them.

---

## Scope completed

`/changes` is no longer a placeholder. ADG answers *what changed since Friday, and is any of
it a problem?* — and refuses, four separate ways, to answer it dishonestly.

1. **A domain diff layer** over Phase 7A's timelines, covering all six kinds of change the
   prompt names: group membership, shares, SMB ACEs, NTFS ACEs, inheritance protection, and
   effective rights.
2. **Four-axis classification** — what happened, whether it is about access, which way access
   moved, and how much attention it deserves — each with an explicit "ADG cannot tell" value
   that is never the quiet one (ADR-0027).
3. **An ACL edit reads as one edit.** An ACE's rights are part of its identity, so tightening
   one removes a row and adds another; the two are paired back together and the direction is
   **computed by comparing the masks** rather than assigned by a rule.
4. **A reordered ACE is only a change when the ACL moved**, decided by rebuilding the
   normalized DACL at both ends — the same normal form the NTFS collector already uses.
5. **Five HTTP routes**: the feed, the summary, one object's timeline, a point-in-time
   comparison, and the impact answer.
6. **Correlation to effective access** through the live engine, with the two timing subtleties
   that make it correct rather than plausible (below).
7. **A Changes UI**: timeline, before/after, diff view, and a "why access changed" page, plus
   a point-in-time comparison page.
8. **One index** (`0008_change_feed_index`), which is prerequisite 3 of the 7A handoff.

---

## What the layer refuses to say, in four sentences

A **gap is never a removal**: a removal exists only where a scan that reconciled a scope
looked and did not find the object, which is Phase 7A's guard and is enforced four independent
ways before this code is reached. The **start of observation is never a creation**: an
object's first version is `first_observed` unless its *container* was already being read, so
an estate's first scan produces no additions at all. A change is **never dated to the scan
that found it**: every change carries both ends of the window it happened inside, including an
addition, whose lower bound comes from the last reading of the container it appeared in. And a
**filtered page always says how much it is hiding**: `/changes/summary` counts the whole window
before the filter, because a page that is clean because of a default is indistinguishable from
a quiet week.

Full treatment: [`docs/architecture/change-detection.md`](../architecture/change-detection.md).

---

## Files and modules added or materially changed

### Added — the change layer

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/changes/model.py` | 431 | The vocabulary: four axes, each with an "ADG cannot tell" value; the delta; the change; the summary that counts before filtering |
| `backend/app/changes/fields.py` | 320 | What every stored column means when it moves, and what contains what. Exhaustive against `history/bindings.py` |
| `backend/app/changes/principals.py` | 183 | Broad trustees and privileged groups — the two judgments severity depends on |
| `backend/app/changes/rules.py` | 810 | The ordered severity table, first match wins, every rule carrying an id that reaches the response |
| `backend/app/changes/classify.py` | 259 | Two versions in, one classified change out. Pure |
| `backend/app/changes/scope.py` | 291 | What "changes on this share" selects, kind by kind. A rule table in `closure.py`'s shape |
| `backend/app/changes/repository.py` | 438 | The windowed reads: the page, the predecessors, the container bounds, the point-in-time sets |
| `backend/app/changes/correlation.py` | 411 | ACE edits paired back together; whether a reordering moved the normalized ACL |
| `backend/app/changes/service.py` | 683 | The feed, the summary, one object's timeline, the comparison |
| `backend/app/changes/impact.py` | 486 | Why access changed, over the live engine |
| `backend/app/api/changes.py` | 1,014 | Five routes and their response models |
| `database/migrations/versions/0008_change_feed_index.py` | 66 | `ix_object_versions_opened_at` |

### Added — the UI

| File | Contents |
| --- | --- |
| `frontend/lib/changes.ts` | Wording and ordering. Decides nothing; carries everything |
| `frontend/components/Changes.tsx` | Severity badge, summary strip, timeline, change card, diff table, before/after, impact caveat |
| `frontend/app/changes/page.tsx` | The feed, its filters, and one object's history |
| `frontend/app/changes/impact/page.tsx` | Why access changed |
| `frontend/app/changes/compare/page.tsx` | Point-in-time comparison |

### Changed

| File | What |
| --- | --- |
| `backend/app/api/__init__.py` | Two routers included: `changes.router` behind `CHANGES_READ`, `changes.impact_router` behind `ACCESS_READ` |
| `backend/app/models/schema.py` | `ix_object_versions_opened_at` declared |
| `backend/app/history/repository.py` | `_COLUMNS` → `VERSION_COLUMNS` and `_version` → `version_from_row`, both exported. **Rename only**; the change layer needs them and reaching into another module's privates is not a thing this codebase does |
| `backend/tests/api/test_authorization.py` | Five entries in `ROUTE_CAPABILITIES` |
| `frontend/lib/contracts.ts` | The change response shapes |
| `frontend/lib/api/adg.ts` | Five fetchers, five `USED_PATHS` entries |
| `frontend/lib/api/client.ts` | `query` accepts an array and sends it as a **repeated** parameter. Required by the repeatable filters; joining with commas would send one value the API rejects as an unknown enum member, and the failure would look like a filter that matched nothing |
| `frontend/lib/nav.ts`, `frontend/tests/nav.test.ts` | Changes is no longer a placeholder |
| `README.md`, `docs/architecture/mvp-capabilities.md`, `docs/contracts/derived-responses.md` | The section, the lifted limitation, the three fields a client must not misread |

---

## Design decisions worth knowing

### Every change is a version opening, including a removal

A tombstone is a version rather than the absence of a row, so *everything that changed between
Tuesday and Friday* is exactly `valid_from` inside that interval. One range scan. No union
with a second query over `valid_to`, and therefore no way for two halves of a feed to disagree
about what a change is. That is why one index was enough for prerequisite 3.

### The container is what separates a creation from the start of observation

An object cannot tell you whether its first version is a creation — it is identical either
way. Its **container** can: an ACE on a directory ADG has been reading for months was added;
the same ACE on a directory nobody had read is the first reading. `CONTAINER_KINDS` is a
seven-entry table and two of its entries are `None`, which is an answer rather than a gap: a
server is a root, and a domain principal is contained by a domain, which is not an object ADG
stores. Deriving one from `domain_sid` would make the parent an attribute of the child, which
is how a container test comes to answer "yes" for everything.

### An addition's window comes from its container too

This was found by a test that asserted every non-first-sighting change carries a window, and
failed. An ACE's rights are part of its identity, so an entry that was *added* has no
predecessor and therefore no lower bound — half of every ACL edit would have carried only
`at`, which is the instant somebody looked. The bound is now the newest confirmation of a
sibling in the same container, batched one query per distinct instant. Without it the UI would
have had nothing to render but the scan time, which is the model ADR-0019 rejected.

### Severity is a rule table, not a score

ADR-0027. First match wins, ids in the response, and `tests/changes/test_rules.py` builds one
case per rule and asserts that rule fires — so a rule shadowed by one above it fails the suite
rather than quietly never firing. Two orderings inside it are load-bearing:

* **Full Control before escalation.** Full Control *contains* `WRITE_DAC`, so the escalation
  rule matches every Full Control grant. Same severity, different sentence, and "Everyone was
  granted Full Control" is what happened.
* **`WRITE_BITS` listed one bit at a time.** See *Defects found*, below.

### The ordering question is answered by the ACL normal form, not by a new comparison

`order_index` moves whenever an entry above it is removed, and Windows numbers relative to
entries ADG does not store. Whether a position change matters is decided by rebuilding the
normalized DACL at both ends of the change window and comparing digests — the same
`app/domain/acl_hash.py` the collector mirrors. There is one definition of "the same ACL" in
this codebase and this reuses it rather than adding a second.

The share layer has no such digest (no flags, no DACL-present bit), so it has its own sequence
rendering under its own version token `adg-share-acl/1`, which can never collide with an NTFS
one. Feeding zeros to `normalize_acl` for the fields the share layer does not have would have
produced a digest that *looks* comparable to a real NTFS one — the same trap as comparing an
SMB mask to an NTFS mask carrying the same bits.

**A failure to reconstruct returns "unknown", never "no."**

### The impact answer needed two instants, and neither was the obvious one

Both were found by tests that failed with a confidently wrong answer.

* **`at_after` is the run's `completed_at`, not the change's `valid_from`.** A scan opens what
  it found at the instant it looked and closes what it did not find when it completed. Between
  the two, a directory carries both the old entry and the new one — a state that never
  existed. Resolving there reports a tightening from Full Control to Read & Execute as
  "nothing changed", because Full Control is still open. Pinned by
  `test_change_impact.py::test_resolving_inside_the_run_would_have_reported_no_change`, which
  asserts the two instants give different answers.
* **`at_before` is the newest confirmation of the state that was replaced.** For an ACL edit
  that is carried by the entry that was *removed*, not by the entry that was added.

### Two routers, two capabilities

The feed, summary, timeline and comparison require `changes:read`. `/changes/impact` requires
`access:read`, because it discloses what a principal could do rather than what was edited —
the same line `/api/v1/groups/{id}/resource-impact` already draws. Both are in the one visible
list in `app/api/__init__.py` and in the exhaustiveness audit.

### The feed and the comparison are different questions

An entry added on Wednesday and removed on Thursday appears in a Tuesday-to-Friday feed twice
and in a Tuesday-versus-Friday comparison not at all. Both are right, and a build that made
them agree would have dropped one of the two questions. Pinned by
`test_change_comparison.py::TestTheyDisagreeAndBothAreRight`.

---

## Defects found while building this

Four, all caught by tests written before the behavior was trusted.

1. **`FILE_GENERIC_WRITE` would have reported every read grant as a write grant.** It is
   `0x00120116` and `FILE_GENERIC_READ` is `0x00120089`: they share `READ_CONTROL` and
   `SYNCHRONIZE`, so intersecting a plain Read & Execute mask with generic-write is non-zero.
   The rule would have fired `ace.allow.broad.write` at `HIGH` on every read grant in the
   estate, and the severity column would have stopped meaning anything within one scan. Fixed
   by listing `WRITE_BITS` one bit at a time, with the reason on the constant.
2. **The comparison reported a tombstone with no earlier version as a removal.** An entry
   created and deleted entirely inside the interval is covered at the later instant by a
   tombstone and by nothing at the earlier one. Calling that a removal claims it existed at
   the earlier instant, which nothing supports. It is now counted under `unobserved_at_from`
   and reported in neither list.
3. **The impact answer resolved both sides at the same instant** for an ACL edit, and returned
   "nothing changed" for a tightening. Two causes, both above: no predecessor to take
   `at_before` from, and `at_after` landing inside the run.
4. **An ACL addition carried no change window at all**, leaving the UI with only the scan
   instant to render.

---

## Schemas and contracts

**Five routes added**, all `GET`, all under `/api/v1/changes`. No existing route changed.

`docs/contracts/v1/openapi.json` was regenerated. **It also carries fifteen routes belonging to
the concurrent session's uncommitted work**, because the document is generated from the whole
application and cannot be generated for a subset. Committing it is what keeps
`tests/contracts/test_openapi_snapshot.py` green; the alternative — committing this phase's
code and not the snapshot — would leave the tree failing that gate.

`docs/contracts/derived-responses.md` gained a section naming the three fields a client is most
likely to misread (`at`, `first_observed`, `severity`) and the two lists that make a page
honest (`edits`, `/summary`).

**One index added**, no table or column changed:

* `ix_object_versions_opened_at` on `(valid_from, id)` — the feed's driving predicate. Both
  columns, because one scan opens thousands of versions at a single instant and a cursor
  carrying only the timestamp would either skip every other version at that instant or return
  them all again. Declared ascending although the feed reads newest first: PostgreSQL scans a
  b-tree backwards at essentially the same cost, and a `DESC` declaration could silently
  disagree with the migration, since `tests/db/test_schema.py` compares reflected indexes and
  cannot see sort direction.

**No new setting. No collector change. No contract-version bump.**

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| Backend, hermetic | `.\scripts\backend-test.ps1` | **5,086 passed**, 10 skipped, 844 deselected, 39.5s |
| Backend, with PostgreSQL | see *Working alongside another session* | **this phase's 71 database tests pass**; the whole-suite figure is not attributable |
| Backend lint and types | `ruff check`, `ruff format --check`, `mypy` over this phase's files | **passed** — 0 findings in `app/changes`, `app/api/changes.py`, `tests/changes`, `tests/db/test_change*.py`, `0008_change_feed_index.py` |
| Frontend | `.\scripts\frontend-check.ps1` | **passed** — lint, typecheck, **742 tests**, production build |
| Collectors | `.\scripts\collector-test.ps1` | not run — **no collector file was touched** |

**293 tests are attributable to this phase**: 222 hermetic (`tests/changes/`), 71 requiring
PostgreSQL (`tests/db/test_change_feed.py` 33, `test_change_comparison.py` 13,
`test_change_impact.py` 13, `test_changes_api.py` 25 — minus overlap in the counts below), and
47 frontend (`tests/changes.test.ts` 29, `tests/changes-views.test.tsx` 18).

**No existing test was weakened or deleted.** Three were amended, each for a stated reason:

* `tests/api/test_authorization.py` — five new routes registered in the audit table. The audit
  failed until they were, which is what it is for.
* `frontend/tests/nav.test.ts` — Changes is no longer a placeholder.
* `frontend/tests/contracts.test.ts` and `frontend/lib/contracts.ts` — `mode` added to
  `ScanRunSummaryView`. **Not this phase's field**; see the section below.

### What each suite covers

| File | What it pins |
| --- | --- |
| `tests/changes/test_fields.py` | The field table is exhaustive against the bindings and classifies nothing that is not stored; the four judgments somebody could reasonably have called metadata; the container table |
| `tests/changes/test_rules.py` | Every rule has a case and fires on it; the table always answers; the editorial calls, each with the reason it is that way round; generic bits expanded before judged; a share level read as a mask |
| `tests/changes/test_classify.py` | The action is never guessed; the window carries both ends and where its lower bound comes from; significance; the record refuses to call a transition a first observation |
| `tests/changes/test_model.py` | Severity is ordered by rank and alphabetical order would invert it; the summary says what the filter hid |
| `tests/changes/test_correlation.py` | An ACL edit pairs into one narrowing; a Deny inverts; the four cases pairing is refused; the share layer's normal form, including the renumbering that must not diff |
| `tests/changes/test_scope.py` | A scope excludes what it cannot speak about; prefixes are safe and never `LIKE`; a principal key is not case-folded; refusals |
| `tests/db/test_change_feed.py` | The first scan is not a change report; what Friday did; the ACL edit as one edit; scopes; filtering and paging; the window bound; **no run may produce a removal without reconciling**, through the real endpoints |
| `tests/db/test_change_comparison.py` | The feed and the comparison disagree and both are right; the comparison never calls a gap a change; a renumbering the normal form ignores is noise and a genuine swap is not |
| `tests/db/test_change_impact.py` | An edit that moved access; an edit that moved none (capped share, winning Deny); a membership edit; what it refuses to guess; the two instants |
| `tests/db/test_changes_api.py` | The capability split; every change carries both ends of its window; the filter echoed back; cursors; two scopes refused; a naive instant refused |
| `frontend/tests/changes.test.ts` | Wording and ordering: a first sighting is never "added", the window is never an instant, an inconclusive "neutral" never reads as "nothing changed" |
| `frontend/tests/changes-views.test.tsx` | What is actually on screen, including that a removal's "after" column says *measured absent* rather than being blank |

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| An administrator can answer "what changed since yesterday?" without reading raw scan tables | `test_change_feed.py::TestWhatFridayDid` and `TestFilteringAndPaging`; `test_changes_api.py::TestTheFeed`; the `/changes` page |
| Changes link back to affected principals/resources and access explanations | `ChangeView.subject` (`test_classify.py::test_the_subject_names_the_thing_a_reader_would_go_and_look_at`); `/changes/impact` (`test_change_impact.py`); the "Why access changed" link, asserted in `changes-views.test.tsx` |
| No-op/reordered normalized ACL data does not create false security diffs | `test_change_comparison.py::TestAReorderingIsOnlyAChangeWhenTheAclMoved` — the renumbering is `noise` and invisible to the default filter, and a genuine swap is `security` |
| Add/remove/modify/no-op cases tested | `test_change_feed.py` (all four, end to end), `test_rules.py` (per rule), `test_classify.py` (per action) |

---

## Known limitations

1. **`sibling_ace_changes` is counted over the page, not the window.** A resource whose ACE
   changes landed on the previous page shows zero and can be reported as an unexplained digest
   change (`resource.acl_hash.unexplained`). A second windowed query per resource would fix it
   and costs more than the finding is worth today.
2. **The rule table is not configurable.** A tenant disagreeing that a removed Deny is `high`,
   or that `BUILTIN\Users` is broad, has to edit `app/changes/rules.py`.
3. **Correlation pairs only within the window being reported.** A removal on Tuesday and an
   addition on Friday pair when both are in the window and do not when only one is. Correct —
   the pair is a claim that these two events are one edit — but a narrow window shows halves.
4. **The comparison has no cursor.** It reads 5,000 objects per side and reports `truncated`.
5. **The feed's filter is applied after classification**, so a page can end on a scan budget
   rather than on data. The response says `scan_exhausted` and the UI renders a banner, because
   a short page with more behind it would otherwise read as the end of the list.
6. **`/changes/timeline` does not page.** It reads up to 500 versions and reports `truncated`.
7. **Phase 7A's limitation 1 still stands.** A *live* answer still counts a grant a reconciled
   scan proved is gone; only point-in-time answers route through presence. Nothing in this
   phase changed that, and `/changes/impact` is a point-in-time answer, so it is not affected.
8. **The UI's window presets are fixed** (1, 7, 30, 90 days) and both comparison instants must
   be supplied in the URL. There is no date picker.
9. **No severity is attached to a `first_observed` change**, by design — which means the first
   scan of an estate produces a page with nothing on it under the default filter, and the
   summary is the only place the volume is visible.

---

## Security and privilege assumptions

* **Unchanged for collectors.** No collector file was touched, no new permission is required,
  and nothing asks a collector for anything it was not already sending.
* **Nothing here can write.** Every route is a `GET`. The application stays read-only.
* **Two capabilities, deliberately split.** `changes:read` for what was edited; `access:read`
  for what a principal could consequently do. A role holding only the first is refused by
  `/changes/impact`, asserted in `test_changes_api.py::TestAuthorization`.
* **No new disclosure class.** Everything a change response carries — SIDs, account names, UNC
  paths, ACLs — is already carried by the current-state endpoints. The new thing disclosed is
  *when* it changed, which is the same data the timeline already held.
* **Absence is still guarded by Phase 7A's four independent checks.** This phase only reads
  tombstones; nothing here can create one.
* **The rule reasons are generated from stored state and well-known SID names only.** No rule
  resolves a display name, so a reason cannot leak a name the caller is not otherwise entitled
  to see.

---

## Migration and compatibility notes

* **`0008_change_feed_index` is additive and fast.** One index on one existing table; no
  column, constraint or row changes. `downgrade()` drops only what `upgrade()` created.
* **It is not required for correctness.** A deployment that has not run it serves the same
  answers more slowly.
* **No contract version changed.** A collector written against 1.3 is unaffected.
* **The frontend requires the regenerated `openapi.json`**, because `tests/contracts.test.ts`
  checks its hand-written types against it.
* **`app.history.repository` renamed two module-private names to public** (`VERSION_COLUMNS`,
  `version_from_row`). No behavior changed; nothing outside the package used them before.

---

## Working alongside another session

A second Claude session was editing `C:\code\adg` throughout this phase, building Phases 8 and
9. This is recorded because three things in the report above are only readable with it in mind.

**What I changed outside Phase 7C's own files, and why:**

| File | Change | Why it was unavoidable |
| --- | --- | --- |
| `docs/contracts/v1/openapi.json` | Regenerated | Generated from the whole application; cannot be generated for a subset. Carries the other session's fifteen governance routes as well as this phase's five |
| `frontend/lib/contracts.ts`, `frontend/tests/contracts.test.ts` | `mode` added to `ScanRunSummaryView` | **Not this phase's field.** It comes from the other session's uncommitted `app/api/scan_runs.py`. Regenerating the snapshot surfaced it and the frontend contract check went red. The value was taken from the published document, not guessed |

**What I did not touch:** `app/governance/`, `app/risk_engine/`, `app/simulation/`,
`app/domain/incremental.py`, `app/ingestion/`, `app/contracts/`, `app/auth/roles.py`,
`tests/support/simulation.py`, or any fixture — including where they are currently failing
their own gates.

**The repository-wide gates are red for reasons that are not this phase's:**

* `ruff format --check` wants to reformat six files, all theirs.
* `mypy` reports fifteen errors across `app/governance/`, `app/ingestion/service.py` (two
  undefined `bindparam` references, which are `NameError`s at runtime), `app/history/writer.py`
  and `app/repositories/risk.py`. Zero are in this phase's files, verified by running both
  tools against this phase's paths alone.
* The shared PostgreSQL test database deadlocked when both sessions truncated it at once. This
  phase's database tests were therefore run against an isolated database
  (`ADG_DATABASE_URL=...:5432/adg_p7c`, which the conftest turns into `adg_p7c_test`). That is
  a test-harness workaround and needs no code change; a second session simply must not share
  `adg_test`.

**Nothing of theirs was staged or committed by me.** Every `git add` named files explicitly.

---

## Prerequisites for the next prompt

1. **Phase 7B is still not done, and its prerequisite is unchanged.** Current-state reads do
   not route through presence, so a live answer still counts a grant a reconciled scan proved
   is gone. The pinned test that inverts when it is fixed is
   `test_history_queries.py::TestPointInTimeEffectiveAccess::test_the_live_answer_still_counts_what_a_reconciled_scan_proved_is_gone`.
   Phase 7C does not depend on it — every answer here is point-in-time — but the Access page
   and the Changes page can now disagree about the same estate, and an operator will notice.
2. **Decide whether the rule table is tenant-configurable.** The severity judgments are
   editorial and currently in source. If they are to be settings, the shape to preserve is the
   ordered table with ids, because the ids are what make a severity traceable.
3. **Consider a change feed for one *resource* rather than one object.** `/changes?share=...`
   exists; what an operator usually wants on a directory page is that directory's own recent
   changes inline, which is a scoped feed embedded in an existing view.
4. **`sibling_ace_changes` over the window.** Limitation 1. It is the difference between a real
   collector under-reporting finding and a false one.
5. **Measure the feed on a large estate.** Every bound here was chosen from reasoning, not from
   measurement: `SCAN_BUDGET`, `SUMMARY_CEILING`, `COMPARISON_CEILING` and
   `MAX_UNSCOPED_WINDOW`. The Phase 7A handoff asked the same of the backfill and it is still
   open.
6. **The Changes page has no date picker.** Window presets and URL-supplied instants were
   enough to build the surface honestly; they are not enough for an auditor comparing two
   specific quarter-ends.

---

## Intentionally deferred

* **Risk scoring of changes.** Severity here is a property of a transition; risk is a property
  of the estate (ADR-0023, the concurrent session's phase). They are deliberately not folded.
* **Alerting or notification.** A change feed is a page, not a subscription.
* **Any write path.** Reverting a change would be remediation, which is reserved behind a role
  that grants nothing today.
* **A derived JSON Schema for the change responses.** The three existing derived responses have
  generated schemas under `docs/contracts/v1/derived/`; these are documented in
  `derived-responses.md` and described by `openapi.json`, which is what the frontend checks
  against. Generating them belongs with whoever next touches `app/contracts/derived.py`.

---

## `git status --short`

Recorded at completion, and it lists another session's files as well as this phase's; only
this phase's were staged. See *Working alongside another session*.

```
(recorded in the commit trailer below)
```
