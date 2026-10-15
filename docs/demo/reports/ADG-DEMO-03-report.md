# ADG-DEMO-03 — Collection coverage, comparison timeout, and recovery

**Verdict: `READY_FOR_ADG_DEMO_REVIEW_WITH_LIMITATIONS`**

A reviewer can now answer both questions the demo was missing, in the real application, over
the reset Null Meridian estate:

* **How complete is the evidence behind this result?** Every risk total and the guided access
  investigation carry the collection behind them: which scopes supplied the evidence, which
  are unavailable, failed, partial or holding older evidence, the snapshot the numbers
  describe, and whether the incompleteness could change the conclusion. A failed or
  unreachable source is never shown as "checked, found nothing", and its findings are not
  zeroed: they stay counted, attributed to the scan that last read them.
* **What happens when ADG cannot finish a comparison within the interactive window?** It stops
  inside a bounded window with a typed outcome (`timed_out`, or `incomplete` at a named
  boundary), presents nothing it did not finish as an answer, and offers real recovery
  requests (retry inside the standard window, or compare one kind of object) that complete
  and are marked complete and recovered.

Every acceptance criterion (§1.2) is proven by tests at the domain, API + PostgreSQL, UI and
browser layers. ADG-DEMO-01 and ADG-DEMO-02 remain intact (§9). The limitations in §12 are
scope and environment limits; none weakens a conclusion the demo shows.

Building it exposed four adjacent correctness defects, all fixed here with regression tests
(§6.4): the risk report called counts "totals" over incomplete collection; the comparison cut
its two sides at different keys and misreported objects; the comparison page dropped every
scope but share and directory; and a client-side timeout was rendered as an outage. A fifth,
found by the browser walkthrough in this change's own first draft, was fixed before it
shipped (§6.5).

---

## 1. Summary

### 1.1 What the reviewer does

| # | Step | Route / anchor |
| --- | --- | --- |
| 1 | View the risk totals, and how ADG says they may be read | `/risks#risk-reading` |
| 2 | Inspect the collection coverage beside the counts | `/risks#risk-coverage` |
| 3 | Identify the unavailable source (NTFS on `NM-FS-03`, holding the 2026-09-17 scan) | the coverage card's disclosure; `/collectors` |
| 4 | Observe that the Apollo answer stands and the incomplete scopes cannot change it | `/access/investigate` section 6 `#collection-coverage` |
| 5 | Launch the synthetic comparison drill | `/demo` → **Run the comparison drill** |
| 6 | Observe the timed-out state | `/changes/compare`, *How this comparison ended* |
| 7 | Inspect the reason (Diagnostics) | same panel |
| 8 | Retry inside the standard window → stops as `incomplete`, at a named boundary | recovery action |
| 9 | Compare only folder permission entries | recovery action |
| 10 | Confirm the result is complete and recovered | same panel |

The walkthrough is in the demo guide and in [`docs/demo/coverage-and-recovery.md`](../coverage-and-recovery.md).

### 1.2 Acceptance criteria

| # | Criterion | Where it is proven |
| --- | --- | --- |
| 1 | Completeness visible beside risk totals | `/risks`: totals reading banner + *Collection behind these counts* under the severity counts. `risks-page.test.tsx` (6 new), browser checks 1–2 |
| 2 | Missing/failed collection ≠ successful zero findings | `ScopeCoverage.returned_nothing` only for a complete scan; `test_coverage.py::TestZeroFindingsIsNotMissingCollection` (3), `test_collection_coverage.py` (unreachable vs clean-empty scans) |
| 3 | Risk totals retain collection/snapshot context | migration 0018 records the basis token per evaluation; `interpretation.evaluated_basis` / `basis_current`; `test_the_evaluation_records_the_snapshot_it_read`, `..._makes_the_totals_stale` |
| 4 | Incomplete evidence does not become certainty | totals read `incomplete`/`stale`/`partial` with every reason; investigation's own certainty untouched (`test_the_investigation_keeps_its_own_uncertainty`) |
| 5 | Slow-comparison behavior measured | §5, §8; harnesses and raw results in the evidence directory |
| 6 | Comparison execution bounded | per-side object ceiling + interactive window (4 s default, ≤ 8 s), checkpointed; at 25 ms/statement: 13.8 s unbounded before, 4.24 s `timed_out` after (§8.4) |
| 7 | Typed timeout/interruption state | `outcome` ∈ `complete`/`incomplete`/`timed_out`/`cancelled`, `stopped_in`; client `timeout` failure kind |
| 8 | Partial results cannot masquerade as complete | `timed_out` presents no list; `incomplete` is cut at one aligned key, labelled partial; an older server's `truncated` reads `incomplete`. `TestTheSidesAreCutAtTheSameKey`, `compare-page.test.tsx` |
| 9 | Deterministic degraded behavior | the dataset's drill (1 ms window, estate-wide) and the object ceiling; pinned end to end on a reset (`TestTheCoverageAndRecoveryDemo`) |
| 10 | Legitimate recovery action | API-listed real requests carrying `retry_of`; `test_timeout_then_recovery_then_a_complete_recovered_comparison` |
| 11 | Recovered comparison completes, marked complete | `outcome: complete`, `recovered: true`; UI "Complete — this comparison recovered the earlier attempt"; browser check 10 |
| 12 | Telemetry sufficient to diagnose | one `changes.compare` line per comparison (§6.3); `test_every_comparison_is_logged_with_what_diagnoses_it`; `compare-telemetry.jsonl` |
| 13 | ADG-DEMO-01 intact | §9 |
| 14 | ADG-DEMO-02 intact | §9 |
| 15 | API contract artifacts consistent | OpenAPI regenerated, additive only; explanation contract stays 1.1, derived schemas byte-identical (§10.4) |
| 16 | No accessibility regressions | words + symbols beside color, `role=status` regions, native `<details>`, labelled lists/forms; keyboard and 390 px browser checks (§10.5) |
| 17 | No production or employer data | synthetic Null Meridian only; evidence scanned (§12) |
| 18 | Durable report and evidence | this file and [`ADG-DEMO-03-evidence/`](ADG-DEMO-03-evidence/) |

---

## 2. Repository state

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| Branch | `claude/tender-curie-vw3iss` (see note) |
| Desired canonical branch | `feature/ADG-DEMO-03-coverage-timeout-recovery` |
| Worktree | `/home/user/adg`, the session's clone; no separate worktree kept (two throwaway worktrees for measurement and trial merges were removed) |
| Starting SHA | `0cee1a49aca7bf5e7064baf2db9369bd233442c4` (head of `claude/serene-mayer-itiwyi`, ADG-DEMO-02's report commit) |
| Prerequisite | ADG-DEMO-02 code `6196eaa`, verdict `READY_FOR_ADG_DEMO_03_WITH_LIMITATIONS`; ADG-DEMO-01 `c37ca55` |
| Code SHA (all code and tests) | `95f47ae` |
| Ending SHA | the commit adding this report (`git log -1 origin/claude/tender-curie-vw3iss`; stated in the final response) |
| `origin/main` | `1ab01a2` (unchanged; nothing merged to it) |

Commits, in order:

| SHA | What |
| --- | --- |
| `76c5f81` | Show collection coverage beside risk totals (domain, service, `/collection/coverage`, risk interpretation, migration 0018) |
| `48d9b78` | Bound the point-in-time comparison and type how it ends (aligned cut, window, outcomes, recovery, telemetry, the drill) |
| `319acb7` | Put coverage beside the numbers, and a recovery path beside a comparison (frontend) |
| `413ded4` | Show a comparison running on the link that started it (§6.5) |
| `6e1bc00` | Record a link's prefetch in the demo guide test the way parallel branches do (§11) |
| `c709a51` | Say the coverage read is at most five statements (docstrings only — and, unnoticed, the published OpenAPI description of the coverage route; §10.6) |
| `d4a7a19` | Report, evidence, walkthrough and docs (first draft of this report) |
| `95f47ae` | Regenerate the OpenAPI snapshot for that description |
| report | this report's final results |

**Branch name.** This session's repository policy allows pushes only to
`claude/tender-curie-vw3iss`. The branch was reset to the required starting SHA before any
change (it had been created at `origin/main`) and holds nothing else. Renaming is a ref
operation: `git push origin claude/tender-curie-vw3iss:feature/ADG-DEMO-03-coverage-timeout-recovery`.

**Prior work reconciled from the repository, not session storage:** `ADG-DEMO-02-report.md`
and its evidence (verdict, deferred items: "risk-total redesign and collection-coverage
badges, including provenance freshness" and "comparison performance, timeouts and recovery"),
`docs/demo/access-investigation.md`, the DEMO-02 branch history (`6196eaa` code, four
documentation commits after it), and every remote head (§11). No ADG-DEMO-03 work existed on
any remote head.

---

## 3. Coverage semantics

### 3.1 What existed (Part 1)

ADG already had two collection-health models, and this change adds no third:

* **`/collection/status`** (`app/domain/collection.py`): the latest run per `(collector,
  target)` folded into `health` = `no_data | healthy | incomplete | failed`. Every page reads
  it before rendering an empty list.
* **`/collection/operations`** (`app/domain/operations.py`): per scope, the latest run, the
  last success and the last failure, and the latest run's `completeness` (`complete`,
  `partial`, `none`, `in_progress`), with `stale_success` (the last success is not the latest
  run). The collector status page.

What did not exist: an expected source set, a "not configured / not attempted" state, any
freshness judgement, a distinction between an unreachable target and a refused collector,
and **any link between collection and risk totals**. The risk report's `coverage` block
describes only the rule *evaluation* (full/incremental/truncated); on the reset estate it read
"A full pass covered the estate… these counts are totals" while NM-FS-03's folders had been
unobserved since 2026-10-01.

### 3.2 The reviewer's model (`app/domain/coverage.py`)

Built on `ScopeOperations`, the operations page's own records. Two facts per scope, never
merged:

| `state` — what the latest run did | `evidence` — what ADG holds |
| --- | --- |
| `collected`: succeeded, no errors, every batch delivered, every declared scope reconciled | `current`: from the latest run |
| `partial`: errors, an undelivered batch, or an unreconciled scope | `stale`: the latest attempt delivered nothing; the rows are from an earlier run (current-state rules keep a row until a complete scan proves it gone) |
| `unavailable`: failed, and every reported code says the target was not reached (`host_unreachable`, `rpc_unavailable`, `timeout` — the collectors' own vocabulary) | `none`: no run ever delivered |
| `failed`: failed or canceled for any other reason (e.g. `access_denied`, or no code) | |
| `running`: still open (earlier evidence stays `current`: a scan that has not finished has not failed) | |

* **Zero findings.** `returned_nothing` is true only for a `collected` scope whose run applied
  no observations: the one state where an empty result means "checked, nothing there". A
  failed or unavailable scope with zero observations is never it, and its sentence never says
  "none".
* **Expected sources.** `ADG_EXPECTED_COLLECTORS` (validated at startup) names the kinds the
  estate is supposed to run. A demo deployment with none configured expects what its dataset's
  own scan history runs (`dataset_expected_collectors`: all four for Null Meridian). An
  expected kind with no run is `not_collected` and makes coverage incomplete; an unexpected one
  is `not_configured`, because ADG cannot tell an intentional omission from a collector nobody
  set up. The architecture has no required/optional distinction, so every expected kind is
  treated as required.
* **Stale.** Ordinal, not wall-clock: a scope is stale when its latest attempt delivered
  nothing and an earlier one did. That is the case that matters on the demo (NM-FS-03), it is
  deterministic, and a wall-clock threshold over a synthetic dataset dated 2026-09/10 would
  flip with the reviewer's calendar. A time threshold is deferred (§12).
* **Verdict and completeness.** `complete` when every scope's latest run was complete and no
  expected kind is missing; `incomplete` when any scope is failed/unavailable or an expected
  kind never reported; `partial` when everything was attempted and something delivered part;
  `none` when nothing has run. Counts are counts of scopes — "98 of 100" — never a percentage:
  a scope is a whole server or the whole directory, and they are not equal in size.
* **Relevance.** `for_server(server)` selects the scopes one answer about a resource reads: the
  directory's (identities and memberships, for every answer) and the scopes whose target is
  the resource's own server (its shares, folders and local groups).

`/collection/status`, its `health` verdict and every existing field are **unchanged**; folding
never-reported kinds into `health` would have changed every page's banner and eight deliberate
single-kind tests, and the expectation mechanism gives the same protection where it is
declared.

### 3.3 On the reset estate

`GET /api/v1/collection/coverage` ([`api/coverage-apollo.json`](ADG-DEMO-03-evidence/api/coverage-apollo.json)):

* 100 scopes: 1 directory, 33 × local groups, 33 × SMB, 33 × NTFS. All four kinds reported and
  expected (`expectation: dataset`).
* `98 of 100 collection scopes collected completely · 1 unavailable · 1 partial · 1 holding
  older evidence`, verdict `incomplete`.
* `ntfs (NM-FS-03)`: `unavailable`, `host_unreachable`, evidence `stale` from 2026-09-17.
* `ntfs (NM-MIG-FS01)`: `partial` (2 declared scopes not reconciled, 1 object unreadable).
* For Apollo: 4 relevant scopes, all `collected`; `could_change_answer: false`;
  `incomplete_elsewhere: 2`.

At most five statements whatever the estate holds (the error-code read runs only when a latest
run failed; pinned at five on the estate with a failure: `test_five_statements_whatever_the_estate_holds`).

---

## 4. Risk integration

### 4.1 Surfaces

* **`/risks`** (the only surface that shows risk totals). The banner states how the counts may
  be read, from the API's `interpretation`; beside the severity counts, *Collection behind
  these counts*: the coverage line, the verdict in a word and a symbol, **Exhaustive** or
  **Not exhaustive**, the snapshot (token, run count, last collector write), and the
  incomplete scopes one disclosure away, linked to `/collectors`.
* **`/access/investigate`** section 6 (§7).
* **`/demo`**: the walkthrough's first four steps link to these.

"Principal reviewer-facing surfaces" was read as the *main* ones. The identity page
(`/identities/principal`) shows no risk totals, and it is the file 11 unmerged branches
conflict on (§11), so it is deliberately untouched; its existing coverage caveat is unchanged.

### 4.2 How incomplete coverage affects interpretation (`interpret_totals`)

Every reason is listed; the reading is the least trustworthy of them:

| Reading | When |
| --- | --- |
| `not_evaluated` | the rules have never run |
| `partial` | the last evaluation was incremental/targeted or truncated (the existing rule) |
| `incomplete` | collection is not complete (any scope not collected, or an expected kind missing) |
| `stale` | collection has moved since the evaluation, or the evaluation predates snapshot recording (`NULL` is never read as current) |
| `complete` | full evaluation, complete collection, same snapshot: the counts are totals |

`held` lists, per server-scoped scope that is not complete, the open findings still held
there, by `starts_with` on the casefolded keys (one statement).

On the reset ([`api/risk-summary.json`](ADG-DEMO-03-evidence/api/risk-summary.json)): 9,386
open findings (3 critical, 8 high, 1,957 medium, 423 low, 6,995 informational — unchanged),
reading `incomplete` with exactly one reason (collection), `basis_current: true` (the reset's
risk pass recorded the snapshot it read), and **189 open findings held on `NM-FS-03`** from
its 2026-09-17 scan.

### 4.3 Risk completeness properties (Part 4)

| Property | Proof |
| --- | --- |
| A failed collector does not become zero findings | `TestAFailedSourceDoesNotLowerRisk`: evaluate a clean FS01 estate, post an unreachable NTFS run for FS01, evaluate again: the same finding keys stay open, and `held` reports them with `evidence: stale` |
| A missing source does not silently lower risk | expected-but-never-reported kinds make coverage `incomplete` → totals `incomplete` (`TestAnExpectedCollectorThatNeverReported`, `test_coverage.py::TestReadingRiskTotals`) |
| Totals stay tied to their snapshot | `test_the_evaluation_records_the_snapshot_it_read`; `test_collection_that_moved_after_the_evaluation_makes_the_totals_stale` |
| Stale coverage distinguishable | `evidence: stale` per scope, counted; `held[].evidence` |
| Incomplete coverage without invalidating supported findings | findings stay listed and counted; only the reading changes |
| UI says totals are not exhaustive | `risks-page.test.tsx` "collection coverage beside the totals" (6 tests); browser checks 1–2 |

Why not just recompute: the risk engine already never resolves a finding outside a pass's
scope and a failed scan closes nothing in current state, so the counts were already right as
"what ADG holds". What was wrong was the sentence beside them.

---

## 5. Comparison baseline

### 5.1 Which path

No document or commit on any branch records a specific comparison timing out. The two
candidates, both measured:

* **`GET /api/v1/changes/compare`** — "Compare two points in time", the only feature the API
  and UI call a comparison, and the one ADG-DEMO-02 deferred as "comparison timeouts". No time
  bound, no typed outcome, an unpaginated body, and the web tier's default 10 s timeout.
* **The simulation preview** — the release audit's "most expensive request the API serves"
  (3.2 s at 400 shares). It already has a time budget, typed truncation and a 30 s client
  timeout; it is out of scope here (§12).

### 5.2 Reproduction and measurement (`0cee1a4`, reset Null Meridian)

Through the API (`measurement/api-before.jsonl`), first-scan-to-latest, estate-wide:

| Shape | Status | Time | Statements | Result |
| --- | --- | --- | --- | --- |
| estate, g1 → g3 | 200 | 723 ms cold, 214 ms warm | 42 | **truncated**: 145 changes, `unobserved_at_from 102`, **`unobserved_at_to 102`** |
| estate, g1 → g2 | 200 | 167–171 ms | 28 | truncated |
| estate, g2 → g3 | 200 | 151–244 ms | 17 | truncated |
| server `NM-ENG-FS01` | 200 | 34–174 ms | 2 | complete, 440 unchanged |

What each instant holds (`cut-analysis-before.txt`): **13,765 objects at g1 and 14,139 at
g3**, against a ceiling of 5,000 per side. Sorted by kind then key, the first 5,000 are all
group memberships (6,143 of them), so an estate-wide comparison on this estate **can never
complete**, and the part it does compare contains no file-system object at all.

### 5.3 Root cause (measured, three parts)

1. **The cut was misaligned.** Each side was read to its own ceiling. The later instant holds
   more memberships early in key order, so its cut fell earlier: the 102 objects between the
   two cuts were reported as **unobserved at the later instant** — every one of them was
   observed. In the opposite direction (more objects early on the earlier side) the same
   misalignment reports objects past the earlier cut as additions. The full comparison (no
   ceiling) has `unobserved_at_to: 0`.
2. **Its cost grew per scan instant and per ACL, with no time bound.** A full comparison
   issued **452 statements**: 253 container-confirmation queries (one per distinct scan
   instant) and 198 ACL reconstructions (2–4 per container with an order-only change). Locally
   that is ~1 s; at 25 ms per statement — a database across a network — it is **13.8 s**,
   past the web tier's 10 s timeout (§8.4).
3. **Failure was untyped.** Nothing stopped the work at an interactive limit, the response had
   only `truncated: bool`, and the web tier's `AbortSignal.timeout` was reported as kind
   `unreachable`: the reviewer saw *"ADG could not reach its API"*, an outage, for a question
   too large to answer interactively. A client that gave up did not stop the server, which
   ran on for nobody.

Raising the ceiling alone was measured and rejected (Part 7): it makes Null Meridian complete
(546 changes) at 452 statements, unbounded in both time and size on a larger estate.

---

## 6. Implementation

### 6.1 Bounding (`ChangeService.compare`)

* **Aligned cut.** Both sides are read under the `"C"` collation (byte order = code-point
  order, the order Python compares the boundary in) and, when either is cut, both are trimmed
  to the smaller of the cut sides' last keys. Everything up to the boundary is compared and
  nothing after it; the boundary is `compared_through`.
* **Repeated work removed.** `container_confirmed_before` answers every
  `(kind, container, instant)` in one statement (`unnest` of three arrays, one correlated
  `max` each on `ix_object_versions_container`): 42 → **6** statements for the bounded
  estate-wide comparison, 452 → 200 for the full one. The feed uses the same method; its
  suites pass unchanged.
* **Interactive window.** `COMPARISON_WINDOW_MS = 4,000` by default; a caller may ask 1–8,000
  (`time_budget_ms`), always under the web tier's 10 s, so the server's typed answer arrives
  first. Checked after the reads, before classification, and before each ACL reconstruction
  (`ordering_materiality(checkpoint=…)`). The worst case is bounded: the reads are bounded by
  the object ceiling, and past the window at most one more reconstruction (2–4 statements)
  runs.
* **Cancellation.** `should_stop` is the request's `is_disconnected`, checked at the same
  checkpoints: a client that went away ends the work (`cancelled`, logged, never delivered).
* **No job subsystem.** None exists and none was added (ADR-0020's decision stands): the
  comparison completes inside a request or says which bound stopped it.

### 6.2 Typed outcomes and recovery

| State | Where | Presented |
| --- | --- | --- |
| `running` | the link that started it (`useLinkStatus` → live "comparing…") | the previous page, announced as running |
| `complete` | API `outcome` | the whole comparison |
| `recovered` | `complete` + `retry_of` | the whole comparison, "recovered the earlier attempt" |
| `incomplete` | API `outcome`, `compared_through` | the compared part, labelled partial |
| `timed_out` | API `outcome`, `stopped_in` | nothing but diagnostics |
| `cancelled` | API `outcome` (log only) | nothing |
| `failed` | client `timeout` / `unreachable` / `server_error` | typed failure; a client timeout is "Timed out — no comparison is presented", not an outage |

Recovery actions are the API's (`recovery[]`), each a real request with its parameters and
`retry_of`: `retry_standard_window` (only after a shortened window) and `narrow_kind` (only
for kinds that fit the ceiling at both instants, with their counts — two counting statements,
run only for a comparison that stopped). A single-kind request is offered no kind (it would
send the reviewer in a circle); the narrowing form (scope + kind) covers the rest. Recovery
links are never prefetched: each is a real comparison.

### 6.3 Observability

One `changes.compare` INFO line per comparison (`adg.changes`), with `comparison_id`,
`retry_of`, `at_from`, `at_to`, `requested_scope`, `requested_kinds`, `effective_scope`
(whole scope / through `<kind>:<key>` / none presented), `outcome`, `complete`, `recovered`,
`stopped_in`, `window_ms`, `elapsed_ms`, `objects_per_side`, `objects_read_from`,
`objects_read_to`, `differences_found`, `changes_returned`, `recovery_offered` and `cached`
(always false: comparisons are not cached). Identifiers of the question and measurements of
the work, never a change, version, ACE or state; the handler's redaction still applies. The
same identifiers are on the response (`comparison_id`, `elapsed_ms`, …) and in the page's
Diagnostics. Sample: [`compare-telemetry.jsonl`](ADG-DEMO-03-evidence/compare-telemetry.jsonl).

### 6.4 Adjacent correctness fixes (all with regression tests)

1. **Risk totals over incomplete collection** were called totals (§3.1) → `interpretation`.
2. **Misaligned comparison cut** (§5.3) → aligned cut.
3. **The comparison page forwarded only `share` and `directory`**; `server`, `principal`,
   `group` and `kind` in the URL were silently dropped → all forwarded
   (`forwards every scope the API accepts`).
4. **A client timeout was an outage** (`unreachable`) → `FailureKind` `timeout`, headline
   "ADG did not answer in time"; refused connections stay `unreachable`.

### 6.5 Found by the browser walkthrough and fixed before shipping

The first draft announced the running state with a route-level `loading.tsx`. Under Next
15.5 it broke every recovery action: a search-parameter-only navigation from one comparison to
another had its RSC request aborted ~180 ms after the click and the outcome never arrived
(measured: stuck > 90 s; a hard navigation loaded in 3.6 s; without the loading boundary the
same navigations commit in 0.3–0.6 s). It was replaced by the link's own `useLinkStatus` live
region (`413ded4`). The unit tests could not have caught it; the browser run did.

### 6.6 Files

```
backend/app/domain/coverage.py            (new)   frontend/lib/coverage.ts               (new)
backend/app/services/coverage.py          (new)   frontend/components/Coverage.tsx       (new)
backend/app/api/collection.py  (/coverage)        frontend/components/Comparison.tsx     (new)
backend/app/api/risks.py  (interpretation)        frontend/components/ComparisonLink.tsx (new)
backend/app/repositories/{risk,operations}.py     frontend/app/risks/page.tsx
backend/app/changes/{service,repository,correlation}.py
backend/app/api/changes.py  (outcome, recovery, telemetry)
backend/app/api/demo.py, app/demo_mode/datasets.py, app/demo/null_meridian/dataset.py
backend/app/config.py (ADG_EXPECTED_COLLECTORS), app/models/schema.py
database/migrations/versions/0018_risk_evaluation_basis.py
frontend/app/access/investigate/page.tsx, app/changes/compare/page.tsx
frontend/components/DemoGuide.tsx, lib/{contracts,investigation,state}.ts, lib/api/{adg,client}.ts
docs/contracts/v1/openapi.json, openapi.demo.json (regenerated)
docs/demo/coverage-and-recovery.md (new), docs/architecture/change-detection.md, .env.example
```

---

## 7. Failure / recovery walkthrough

Deterministic, end to end, pinned against a real reset by
`test_null_meridian_demo.py::TestTheCoverageAndRecoveryDemo::test_the_comparison_drill_times_out_then_recovers_to_complete`
and driven in Chromium by the browser script. Responses:
[`compare-1-timed-out.json`](ADG-DEMO-03-evidence/api/compare-1-timed-out.json),
[`-2-incomplete.json`](ADG-DEMO-03-evidence/api/compare-2-incomplete.json),
[`-3-recovered.json`](ADG-DEMO-03-evidence/api/compare-3-recovered.json).

```
start      GET /changes/compare?from=2026-09-03T06:00Z&to=2026-10-01T06:00Z&time_budget_ms=1
           (the dataset's drill: estate-wide, first scan against latest, a 1 ms window)
  ↓
degraded   outcome timed_out, stopped_in read, changes [] — read 4,898 / 5,000 objects
           recovery: retry_standard_window; narrow_kind × 6 (each with its counts)
  ↓
incomplete retry inside the standard 4 s window (retry_of = the first attempt)
           outcome incomplete, compared_through membership_edge "S-1-5-21-…-513->…-2325|primary_group",
           145 changes labelled partial, unobserved_at_to 0 (not 102)
  ↓
recovery   compare only folder permission entries (3,947 → 4,124 objects; retry_of = the retry)
  ↓
complete   outcome complete, recovered true, 221 differences — "Complete — this comparison
           recovered the earlier attempt"
```

**Why deterministic.** The drill's timeout is a 1 ms window applied only to the synthetic
scenario (declared by the dataset, served on `/api/v1/demo`); reading two sides of
thousands of versions takes longer than that on any host, so it stops at its first checkpoint
every time (no randomness, no artificial slowness in production code). The `incomplete` step
is pure object counts (13,765/14,139 vs 5,000). Unit and DB tests that need exact timing use
an injected clock (`clock=` / the module-level `_clock`). The one judgment call: on a host so
slow that reading 5,000 objects per side exceeds 4 s, step 3 would read `timed_out` rather
than `incomplete` — still typed, still recoverable by narrowing.

---

## 8. Performance evidence

Environment: Claude Code cloud container, 4 vCPU (Intel Xeon), 15 GB, Ubuntu 24.04, Python
3.13.16, SQLAlchemy 2.1.4, PostgreSQL 16.15 (local cluster), Node 22.22, Chromium 1194. Several
runs shared the machine with the full PostgreSQL suite: noisy. Statement counts are exact;
read milliseconds for shape. No SLA is claimed (the repository defines none).

### 8.1 Input sizes

| | Objects at g1 | Objects at g3 | Per-side ceiling |
| --- | ---: | ---: | ---: |
| estate-wide | 13,765 | 14,139 | 5,000 |
| folder permission entries only | 3,947 | 4,124 | 5,000 |

### 8.2 Through the API, before and after

| Shape | Before (`0cee1a4`) | After |
| --- | --- | --- |
| estate g1 → g3 | 214 ms warm (723 cold), 42 statements, `truncated`, 102 misreported | 211 ms, **6** statements, `incomplete`, 0 misreported |
| estate g1 → g3, 1 ms drill | n/a (no window) | 196 ms, 4 statements, `timed_out` |
| estate g2 → g3 | 151–244 ms, 17 | 222 ms, 6, `incomplete` |
| folder permission entries | — | 315 ms, 152, `complete`, 221 |
| share permission entries | — | 92 ms, 52, `complete` |
| server `NM-ENG-FS01` | 34 ms, 2, complete | 29 ms, 2, `complete` |

### 8.3 Service-level, with injected per-statement latency (`latency.jsonl`, median of 3)

| Case | Code | Statements | 0 ms | 5 ms | 10 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| estate-wide, ceiling 5,000 | before | 42 | 356 | 497 | 679 |
| | after | **6** | 296 | 295 | 315 |
| estate-wide, ceiling 20,000 (no bound reached) | before | 452 | 1,055 | 3,470 | 6,324 |
| | after | **200** | 898 | 1,846 | 3,439 |
| folder permission entries | before | 328 | 461 | 2,495 | 3,945 |
| | after | **152** | 321 | 1,258 | 2,087 |

### 8.4 Bounded under a slow database (`bounded.jsonl`, 25 ms per statement, full estate)

| Code | Statements | Wall | Outcome |
| --- | ---: | ---: | --- |
| before | 452 | **13,789 ms** | ran to completion — past the web tier's 10 s, which the user saw as "could not reach its API" |
| after | 126 | **4,240 ms** | `timed_out` in `ordering`, nothing presented, recovery offered |

### 8.5 The reviewer's flow in the browser (`browser-walkthrough.json`, final run)

Click to rendered outcome, production build, includes server render: drill 815 ms → retry
1,050 ms → recovered comparison 1,595 ms. Server-side for the same requests
(`compare-telemetry.jsonl`): 480 ms (timed out at the read checkpoint), 374 ms, 486 ms.

---

## 9. Regression status

**ADG-DEMO-01 (SYSTEM correctness): intact.** No SYSTEM or engine code changed; the suites ran
before editing and after:

* engine (`test_system_explanation_regression` + `test_system_mapping` + `test_resolver`):
  **145 passed** before and after;
* `test_system_mapping_api.py` (10) and `TestTheSystemExplanation` (2): passed before and
  after, and in the full DB suite;
* UI set (`system-mapping`, `explanation`, `explanation-views`, `identity`): **171 passed**
  before and after.

**ADG-DEMO-02 (access investigation): intact.**

* engine `test_investigation` + `test_unidentified_trustees`: **102 passed**; seed 6;
* `TestTheAccessInvestigation` (10) + `TestTheGoldenScenariosThroughTheApi` (11) +
  `TestTheSystemExplanation` (2): **23 passed** before, and again after alongside the 5 new
  (`28 passed`);
* `test_explanation_api.py` + `test_access_paths_api.py`: **61 passed** (with
  `test_system_mapping_api` 10: 71);
* UI `explanation-page` + `explanation-contract`: **112 passed**; `investigation` +
  `investigation-page` + `demo-guide`: **68 passed** (DEMO-02's 58 + 10 new);
* the investigation answer on the reset is unchanged: `granted`, Modify, `at_least`, both open
  questions (`test_the_investigation_keeps_its_own_uncertainty`; browser check 4).
* Unresolved identities stay visibly unresolved (unchanged code; DEMO-02's tests pass).
* Demo fingerprint unchanged: `228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`
  ([`reset.json`](ADG-DEMO-03-evidence/reset.json)); the risk tables it covers are not touched
  (the new column is on `risk_evaluations`, which the fingerprint does not include).

---

## 10. Tests

Run in this container (§8). Commands are CI's (`.github/workflows/ci.yml`).

### 10.1 Backend

| Gate | Result |
| --- | --- |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** |
| `mypy app tests` | **passed** (411 source files) |
| New: `tests/domain/test_coverage.py` | **26 passed** |
| New: `tests/demo/test_null_meridian_coverage_drill.py` | **6 passed** |
| `tests/test_config.py` (3 new) | **16 passed** |
| `tests/access_engine` | **694 passed, 1 skipped** |
| `tests/contracts` (OpenAPI snapshots regenerated) | **611 passed, 109 skipped** |
| `pytest -m "not smoke"` (full unit suite) | **6,009 passed, 124 skipped, 1,273 deselected** (DEMO-02: 5,974 / 124 / 1,237; +35 unit, +36 DB tests deselected here) |

### 10.2 PostgreSQL

PostgreSQL 16 was stopped when the container started; it was started with
`pg_ctlcluster 16 main start` and the CI role and databases created — an environment step,
not a product failure.

| Gate | Result |
| --- | --- |
| New: `test_collection_coverage.py` | **18 passed** |
| New: `test_comparison_bounds.py` | **13 passed** |
| New: `TestTheCoverageAndRecoveryDemo` (5) + DEMO-01/02 classes (23), real reset | **28 passed** |
| Existing related: `test_change_comparison`, `test_change_feed`, `test_changes_api`, `test_query_cost`, `test_risk_findings`, `test_collection_status_api`, `test_operations_api` | **157 passed** |
| `test_system_mapping_api` + `test_explanation_api` + `test_access_paths_api` | **71 passed** |
| `pytest -m smoke` (full DB suite), on `319acb7` (backend identical to the code head but for two docstrings) | **1,272 passed, 1 xfailed**, exit 0, 31m48s (ADG-DEMO-02: 1,236 passed, 1 xfailed; +36 new; the xfail is the same pre-existing one). Includes `TestRepeatability` (a second reset reproducing the fingerprint), the history replay, and every SYSTEM and investigation class |

### 10.3 Frontend

| Gate | Result |
| --- | --- |
| `npm run lint` | **passed** |
| `npm run typecheck` | **passed** |
| New: `coverage.test.ts` (wording, client timeout) | **29 passed** |
| New: `compare-page.test.tsx` | **18 passed** |
| `risks-page` + `risks` (6 new) | **38 passed** |
| `investigation` + `investigation-page` + `demo-guide` (10 new) | **68 passed** |
| `contracts.test.ts` (new fields and 7 enum unions checked against OpenAPI) | **465 passed** |
| `npm run test` (full) | **1,361 passed** (38 files; DEMO-02: 1,259 in 36) |
| `npm run build` | **passed** |

### 10.4 Contract

* Explanation contract **stays 1.1**: `CONTRACT_VERSION` untouched; `python -m
  app.contracts.derived` regenerates the three derived schemas byte-identical.
* `docs/contracts/v1/openapi.json` and `openapi.demo.json` regenerated from the application
  (`python -m app.contracts.openapi`) at each code commit. The exception is `c709a51`, whose
  docstring change altered a published description without a regeneration; `95f47ae`
  regenerated it (§10.6), and the snapshot tests pass on the code head. Semantic diff against the start: **11 schemas added, none removed; properties added
  to `ComparisonResponse` and `RiskSummaryResponse`, none removed; one path added**
  (`/api/v1/collection/coverage`); the demo document adds `ComparisonSeedView` and
  `DemoView.comparisons`. No stale generated contract is committed: the snapshot tests compare
  the committed files with the running application.

### 10.5 Browser (end to end, real stack)

As in ADG-DEMO-02, the repository has no browser framework and none was added; a one-off
Playwright script (kept as evidence) drove the pre-installed Chromium against the API in demo
mode on a fresh reset and `next start` of the production build. **39 of 39 checks passed**
([`browser-walkthrough.json`](ADG-DEMO-03-evidence/browser-walkthrough.json)):

* the guide's ten-step walkthrough; the risks reading as an announced status; the counts
  still shown; NM-FS-03's 189 held findings; the coverage line, verdict word + symbol,
  "Not exhaustive", the snapshot;
* **Tab reaches the incomplete-scopes disclosure, Enter opens it, it shows a focus outline**;
* the collector status page names NM-FS-03; the investigation's section 6 says the incomplete
  scopes cannot change the answer, lists its four collected scopes, and the answer is still
  Modify / `at_least`;
* the drill: timed out (word + symbol, `role=status` `aria-live`), no list, no "nothing is
  different"; each recovery action has its own live "comparing…" region; **Tab reaches
  Diagnostics, Enter opens it**; retry → incomplete at a named boundary, `retry_of` = the first
  attempt, list labelled partial; narrow → complete and recovered, differences shown, no
  recovery offered;
* **no horizontal scroll at 390 px** on `/risks`, the investigation, and the comparison in all
  three outcomes;
* no application console errors. The browser's own `/favicon.ico` 404 is recorded and
  attributed (pre-existing: the application ships no favicon).

### 10.6 CI

GitHub Actions on `claude/tender-curie-vw3iss`:

* **Run 68** on `48d9b78` (both backend commits; frontend unchanged at that point)
  ([run](https://github.com/40ozs/adg/actions/runs/38655129143)): **every job passed** —
  Backend (lint, types, unit, **database tests** 23:39–00:03 UTC), Frontend (lint, types,
  tests, build), Collector (Pester, `windows-latest`), Docker Compose configuration.
* **Run 69** on `413ded4` (frontend included) ([run](https://github.com/40ozs/adg/actions/runs/38658153994)):
  *database tests still running at this commit (frontend, collector, compose and backend unit jobs passed); final result recorded in the next commit.*
* **Runs 70 and 71 failed** — on `c709a51` ([run](https://github.com/40ozs/adg/actions/runs/38658840603))
  and on the first report commit `d4a7a19` ([run](https://github.com/40ozs/adg/actions/runs/38658922806)):
  1 of 6,133 unit tests, `tests/contracts/test_openapi_snapshot.py::test_the_snapshot_is_current`.
  `c709a51` changed a route docstring that FastAPI publishes as the coverage operation's
  description, so the committed `openapi.json` no longer matched the application; the local
  gates had been run before that commit and were not rerun after it. Reproduced locally (same
  failure), fixed by regenerating the artifact (`95f47ae`), and every backend gate plus the
  frontend contract test rerun green before pushing. A real failure in this change, not a
  flake; no test was skipped or changed to pass.
* **Run 72** on the code head `95f47ae` ([run](https://github.com/40ozs/adg/actions/runs/38659592991)):
  *running at this commit; final result recorded in the next commit.*
* The final report commit triggers its own run (documentation only); its result is in the
  final response, not here.

---

## 11. Parallel branches

Fresh `git fetch --prune`, then write-free trial merges (`git merge-tree --write-tree`)
against the code head:

* **18 remote heads besides `main` and this branch; 6 already contained** (the DEMO-02 and
  DEMO-01 heads, the Demo integration head, `dazzling-keller`, `inspiring-volta`,
  `vibrant-gates`). **13 unmerged** — the same 13 ADG-DEMO-02 recorded.
* **11 conflict in `frontend/app/identities/principal/page.tsx`**, the pre-existing conflict;
  this work does not touch that file.
* **3 also conflict in the generated `docs/contracts/v1/openapi.json`**: `adoring-cannon`,
  `compassionate-hamilton` (as before) and now `feature/ADG-GRAPH-01C-path-engine-claude`,
  because this change regenerated it. Resolution is mechanical and must not pick a side:
  merge the source, then `python -m app.contracts.openapi` and `python -m
  app.contracts.derived`, then `tests/contracts` and the frontend `contracts.test.ts`.
* **2 merge cleanly**: `claude/wizardly-gates-6yjdxp`, `deploy/ADG-FREE-EDGE-SSR-03`.
* A new conflict this change first introduced in `frontend/tests/demo-guide.test.tsx` (11
  branches had made the same `next/link` mock change in different words) was removed by
  adopting their exact text (`6e1bc00`); the trial merges confirm it.
* **Migrations.** `feature/ADG-GRAPH-01A/B/C` add `0018_graph_relationships` (and `0019`) on
  `0017`, as this change adds `0018_risk_evaluation_basis`. The filenames differ, so there is
  no textual conflict, but a merge yields two Alembic heads. `alembic upgrade heads` (what the
  tests and the demo reset run) applies both; a one-line merge revision linearizes them, as
  `0012`/`0014` did before.
* **Files both sides edit, all merging textually:** `backend/app/models/schema.py` and
  `tests/db/test_null_meridian_demo.py` (GRAPH-01A/B/C), `frontend/lib/api/client.ts`
  (`wizardly-gates`), and on the eleven conflicting branches `lib/api/adg.ts`,
  `lib/contracts.ts`, `tests/contracts.test.ts`, `tests/risks-page.test.tsx` and
  `tests/api/test_authorization.py`. None of the unmerged branches touches
  `backend/app/access_engine/`, the coverage, risk-summary or comparison modules. The eleven
  also edit `frontend/components/Risks.tsx` (`FindingTable` sorting), which this change does
  not. After any integration, rerun the contract, risks-page, coverage, comparison and
  ADG-DEMO-01/02 suites (§10).

**Nothing was integrated**: no branch is needed for this work, and merging 150+ unreviewed
files was out of scope.

---

## 12. Limitations

**Product**

1. **The expected source set is per collector kind**, not per target: a server ADG has never
   scanned at all is invisible to coverage until a run names it. Null Meridian's expectation
   comes from its own scan history; production needs `ADG_EXPECTED_COLLECTORS`.
2. **Stale is ordinal, not time-based.** A scope whose latest scan succeeded long ago reads
   `current`; a wall-clock freshness threshold is deferred (deterministic demo data and a
   reviewer's calendar do not mix).
3. **`health` on `/collection/status` is unchanged**: a never-reported expected kind makes the
   *coverage* verdict and the risk reading incomplete, but the page-wide empty-state banner
   still judges only the scopes that reported.
4. **An estate-wide comparison of Null Meridian can never complete in one request** (13–14k
   objects per instant vs 5,000). This is the bound doing its job; recovery is by kind or
   scope. There is no continuation cursor (a segment that "completes" is not the whole
   comparison, and the UI would have to say so); deferred.
5. **ACL reconstruction is still per container** (2–4 statements each): checkpointed and
   bounded by the window, not batched.
6. **Kind narrowing is offered only for kinds that fit** at both instants; group memberships
   (6,143) never fit estate-wide on Null Meridian and must be narrowed by group or principal
   through the form.
7. **The risk engine's own pre-existing asymmetry when the directory collector is missing**
   (an unresolvable SID on an ACL reports `no_principal_record`; `DirectUserAce` skips unknown
   principals) is unchanged; the reading now says such totals are not exhaustive. Not visible
   on the demo (the directory is collected).
8. **Simulation preview** (the release audit's most expensive request) keeps its own existing
   bounds; out of scope.

**Environment**

1. Measurements come from one 4-vCPU cloud container, partly concurrent with the DB suite; the
   remote-database case is modelled by injected per-statement delay, not measured on a hosted
   deployment.
2. Not run locally: the Collector Pester suite (no Windows host) and `docker compose config`
   (no Docker). CI runs both (§10.6).
3. Browser validation used a one-off script (no E2E framework in the repository).
4. The `/favicon.ico` 404 is pre-existing.

**Intentionally deferred**

1. New collectors, production connectors, `NETWORK SERVICE` / `LOCAL SERVICE`, SYSTEM
   computer-account correlation, dashboard redesign, graph-engine work, OGWA policy features,
   infrastructure.
2. A durable/asynchronous comparison job (no job architecture exists; ADR-0020).
3. Integrating any parallel branch (§11).

Evidence hygiene: every artifact is synthetic Null Meridian; scanned for JWTs, bearer
tokens, passwords and secrets before commit (none; the only database URL is the CI throwaway
already in the workflow file).

---

## 13. Handoff

* **DEMO-PUBLIC-01 should start from:** the head of `claude/tender-curie-vw3iss` that contains
  this report (`git log -1 origin/claude/tender-curie-vw3iss`; the exact SHA is in the final
  response).
* **Code and tests, all gates above:** `95f47ae`. The local gates ran on `6e1bc00`/`319acb7`; `c709a51` and `95f47ae` change two docstrings and the regenerated contract, after which lint, format, mypy, the full unit suite, `tests/contracts` and the frontend contract test were rerun and passed. The report commit adds only documentation
  and `ADG-DEMO-03-evidence/`.
* **Before a public deployment:** decide `ADG_EXPECTED_COLLECTORS` for any non-demo estate;
  re-measure the comparison window on the hosted database (§12 env 1); add an Alembic merge
  revision if the GRAPH-01 branches are integrated (§11).
