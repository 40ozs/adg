# Handoff — Phase 7B (`phase-07/02-incremental-collection.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-07a-history-model.md](phase-07a-history-model.md)
**Collector contract version after this phase:** `1.4` — **additive minor bump** (see §4).
**Derived-response contract:** `1.0` — unchanged.
**`docs/contracts/v1/openapi.json`:** regenerated. **No route was added, removed or altered**;
only response *fields*.

> **Read this first.** A second agent session was working in this same tree throughout this
> phase, building the governance, risk, change-feed and simulation phases. Its files are
> interleaved with this phase's in `git status`, its migrations branch off the same revision
> as this one, its tests currently fail lint and mypy, and it left a transaction open on the
> shared test database for half an hour. **Nothing was committed**, and §11 says exactly which
> files belong to this phase and why that decision was made.

## Scope completed

Collection now has a cadence. Each source is read as often as it can afford to be, resumed
where it can be resumed safely, and repaired on a slower schedule by a run that reads
everything — and none of that changes what ADG is allowed to conclude.

1. **Six independent jobs**, each with its own schedule, state, lock, checkpoint and failure,
   driven by one Windows scheduled task.
2. **A true Active Directory delta**, filtered on `uSNChanged`, with the watermark tied to the
   *incarnation* of the domain controller that issued it.
3. **Checkpoints that trail their data**: advanced inside the transaction that wrote the
   batch, never past a run the server downgraded, never backwards, never across issuers —
   and a refusal recorded where an operator can see it.
4. **Affirmations (contract 1.4)**: an NTFS scan reads every descriptor as before and sends
   the unchanged ones as a key and a digest the server *verifies* against what it holds.
5. **A reconciliation pass that measures what it repaired**, reported as drift.
6. **Idempotent retry with exponential backoff and jitter**, and a list of the failures a
   retry cannot fix.
7. **Telemetry for all seven signals the prompt names**, each stored where it is queryable.

---

## 1. The idea, in six sentences

A delta reads only what its source says has changed, and only Active Directory publishes
anything usable for that. The file system publishes nothing a walk can trust — **writing a
DACL does not move a directory's `LastWriteTime`** — so an NTFS scan still reads every
descriptor and reduces cost by what it *transmits* instead. What a delta can never report is
a deletion: nothing announces one to a query that filters on change metadata, so *nothing
arrived* and *nothing exists* are the same observation. Absence is therefore discovered only
by a full reconciliation, whose interval is the upper bound on how long ADG can believe in
access that no longer exists, and that bound is stated rather than implied. Every narrowing
in this phase fails safe: the worst outcome of any refusal is a full scan. The one error mode
that is never acceptable — a cursor ahead of the data, which makes the next run skip objects
that produce no error, no gap and no number that looks wrong — is structurally excluded.

Full treatment: [`docs/architecture/incremental-collection.md`](../architecture/incremental-collection.md).

---

## 2. Files added or materially changed

### Backend — added

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/domain/incremental.py` | 313 | Pure: `CollectionMode`, `CheckpointKind`, `Checkpoint` with its advance rule, `CheckpointRejection`, `ReconciliationDrift` |
| `backend/app/ingestion/checkpoints.py` | 268 | `CheckpointStore`: read, advance, refuse, record the refusal |
| `database/migrations/versions/0008_incremental_collection.py` | 190 | `collector_checkpoints`, `scan_run_checkpoints`, five columns on `scan_runs`, three on `scan_run_scopes` |

### Backend — changed

| File | What |
| --- | --- |
| `backend/app/contracts/v1/common.py` | `SCHEMA_VERSION` → `1.4`; `CollectorCheckpoint`, `Affirmation`, `MAX_BATCH_AFFIRMATIONS` |
| `backend/app/contracts/v1/envelopes.py` | `mode`/`job`/`baseline` on start, `affirmations`/`checkpoint` on batch, `affirmation_count`/`checkpoint` on completion, and `_require_minor` — the mirror of the additive rule |
| `backend/app/ingestion/plan.py` | `AffirmationRow`; the affirmed `source_key` is inverted **and re-applied**, so a key that does not round-trip is refused rather than becoming a lookup that matches nothing |
| `backend/app/ingestion/service.py` | `_apply_affirmations`, `_confirm_current_state`, `_advance_checkpoint`, `_record_drift`, `_delta_runs_since`; mode/job on start; the downgrade guard on the completion checkpoint |
| `backend/app/history/writer.py` | `AffirmationOutcome`; `affirm` (per key, with refusals) and `affirm_contained` (set-based, for the entries a digest covers); `_extension` generalized so both paths apply one rule |
| `backend/app/models/schema.py` | The two new tables; `mode`, `job`, three affirmation counters; `closed_absent`, `revived`, `delta_runs_since`; `ck_scan_runs_mode_matches_incremental` |
| `backend/app/api/scan_runs.py` | `affirmed` and `refused_affirmations` on the batch response, `drift` and `checkpoint` on the completion response, `mode`/`job`/counters/checkpoints/drift on the run views |
| `backend/tests/support/history.py` | `h.scan` gains the 1.4 arguments and bumps the declared version **only when one is used**; `h.resource` gains `digest` |
| `backend/tests/db/test_ingestion.py` | One expected dict names the three fields 1.4 adds to the batch response. The assertion stays an exact comparison |

### Collector — added

| File | Lines | Contents |
| --- | ---: | --- |
| `collector/powershell/orchestrator/functions/AdgJobConfig.ps1` | 373 | The six job kinds and what each may claim; durations; total validation at load |
| `collector/powershell/orchestrator/functions/AdgJobState.ps1` | 302 | Per-job state, the atomic write, the run lock |
| `collector/powershell/orchestrator/functions/AdgSchedule.ps1` | 232 | Due-ness, mode resolution, backoff — all pure |
| `collector/powershell/orchestrator/functions/AdgJobRunner.ps1` | 330 | One job: lock, retry, record. Collectors by injection |
| `collector/powershell/orchestrator/functions/AdgCollectorInvokers.ps1` | 226 | The only code here that knows what a collector is |
| `collector/powershell/orchestrator/Invoke-AdgCollection.ps1` | 175 | The entry point a scheduled task calls |
| `collector/powershell/orchestrator/Register-AdgCollectionTask.ps1` | 168 | Prints the task by default; `-Register` to create it |
| `collector/powershell/ntfs/functions/AdgNtfsDigestIndex.ps1` | 232 | What was last reported per path. Decides transmission, never reading |
| Tests + example config + README | — | 106 Pester tests, `adg-orchestrator.example.json`, an operator guide |

### Collector — changed

| File | What |
| --- | --- |
| `common/AdgCollector.Common.psm1`/`.psd1` | `New-AdgCheckpoint`, `New-AdgAffirmation`; the three envelope builders take the 1.4 fields and declare `1.4` **only when one is present** |
| `ad/AdgCollector.ActiveDirectory.psm1`/`.psd1` | `Get-AdgDirectoryIssuer` (dsServiceName + invocationId), `Add-AdgUsnFilter`, `Passes`, `SinceUsn`, `CheckpointIssuer`, `Job`; the fixture filter understands numeric `>=` |
| `ad/Invoke-AdgAdCollector.ps1` | `-Job -Passes -SinceUsn -CheckpointIssuer -PassThru` |
| `ntfs/functions/AdgNtfsScan.ps1` | `Add-AdgNtfsResourceGroup` decides send-or-affirm; the batch carries `affirmations` |
| `ntfs/functions/AdgNtfsConfig.ps1` | `digestIndexPath`, `digestIndexMaxEntries` |
| `smb/Invoke-AdgSmbScan.ps1`, `ntfs/Invoke-AdgNtfsScan.ps1` | `-PassThru` |
| `backend/tests/fixtures/ad_graph/*.json` (12) | Regenerated: they embed `schema_version`, which the bump changed. No other content differs |

### Documentation

* `docs/architecture/incremental-collection.md` — the model, the three sources and what each
  publishes, affirmations, checkpoints, drift, the six jobs, the telemetry table, and §9:
  what this phase deliberately did not change.
* **ADR-0025** — incremental collection is bounded by what its source can prove, and absence
  is never one of those things.
* **ADR-0026** — an affirmation is verified against stored state, and a checkpoint never
  leads the data it describes.
* `docs/contracts/collector-protocol.md` — §11-equivalent: the full **1.4** section, plus
  three cross-references in §2, §5 and §6.
* `collector/powershell/orchestrator/README.md` — the operator's guide: install, exit codes,
  state files, and "when something looks wrong".
* `README.md`, `collector/README.md` — a Collection cadence section and the orchestrator.

> **ADR numbering:** 0020–0024 were taken by the concurrent session while this phase was in
> progress, so these two were renumbered to 0025 and 0026 before any cross-reference was
> written. Nothing points at the old numbers.

---

## 3. Design decisions worth knowing

### A checkpoint is a cursor *and* the identity it belongs to

`uSNChanged` is a counter on one domain controller, and two ordinary events invalidate a
saved watermark. Binding a different DC is the obvious one. **A DC restored from backup is
the one that catches people**: its counter rolls backwards and it reissues numbers it has
already handed out, and its `dsServiceName` does not change. Its `invocationId` does.

So the issuer is both facts joined, and the store compares it before it will move anything.
A mismatch is refused, the reason is written to `collector_checkpoints.last_rejection_code`,
and the next run reads everything. `Checkpoint.advances_over` is a pure function returning a
*value* rather than raising, because the caller has to record the refusal: a delta whose
checkpoint was refused has not failed, but it also has not advanced, and nothing else about
it looks wrong.

### An affirmation is an observation the server checks rather than believes

The mechanism only works because `acl_hash` already existed (contract 1.2) and the server
already recomputes it from the entries it stores. An affirmation carries that value; the
server compares and refuses on mismatch, on an unknown object, on a stored row with no
digest, and on a tombstone. Each refusal comes back naming the key and the collector re-sends
in full. A collector cannot make ADG keep a state ADG does not already hold.

Two invariants make it honest rather than merely convenient:

* **the digest is computed from this scan's reading.** The collector-side index decides only
  what to transmit, and `Get-AdgNtfsAffirmableDigest` takes the fresh digest as an argument,
  so no code path turns an index entry alone into an affirmation;
* **an affirmed resource confirms the entries its digest covers.** Closure decides what to
  mark absent from the `observations` table, so a resource affirmed without its ACEs would be
  one whose whole DACL the next reconciliation tombstoned. `affirm_contained` does it with
  two indexed statements on `ix_object_versions_container` — no ACE key crosses into Python,
  which is the point: confirming a thousand unchanged directories must not cost more because
  they have forty entries each.

`test_an_affirmation_does_what_an_identical_re_observation_does` pins the equivalence the
cheap path rests on. If it ever diverged, the cheap path would be quietly recording something
different from the expensive one.

### Two parties enforce "a failed run may not declare the source current"

| Who | Knows | Refuses |
| --- | --- | --- |
| The collector | its own errors | writing a checkpoint after a run it did not finish cleanly |
| The contract model | the payload | a checkpoint on a completion that is not `succeeded` with zero errors |
| The server | how many batches **arrived** | a checkpoint on a run it downgraded for short delivery |

The third is the one neither of the others can make. The collector believes it sent three
batches; only the server knows that two arrived. The claimed cursor is still recorded against
the *run* (`scan_run_checkpoints`, role `result`) because what a collector claimed is evidence
even when the server will not act on it.

### Drift counts only the absences

A delta is *structurally* incapable of noticing that an object is gone, so every tombstone a
reconciliation writes is a fact no cadence of deltas would have produced. Ordinary state
changes are deliberately excluded: an object whose ACL changed between two reconciliations may
well have been caught by a delta in between, and counting it would make routine churn look
like the cadence failing. `delta_runs_since` is the denominator — three absences after fifty
deltas and three after one are different statements.

### Two jobs read half the domain, and neither may reconcile it

`ad_principals` and `ad_memberships` each declare the `domain` scope — it is what they set out
to look at — and mark themselves incremental. The instinct is that a clean scan of the domain
should be allowed to reconcile it; it observed **no membership edges**, and the Phase 7A
closure rules for a `domain` scope cover principals *and* edges, so reconciling it would
tombstone every edge in the estate. The rule is derived from the job kind rather than read
from the configuration, so it cannot be switched off by editing a file.

### Two defects the implementation found in itself

* **A `partial` run was being retried.** The first draft retried anything that was not
  `succeeded`. A partial run is a real result on an estate with an unreadable corner in it:
  it collected valid observations, reported the errors that stopped it, and reconciled
  nothing. Retrying it means reading the whole scope again to reach the same directory that
  denied access the first time — three times, with backoff in between. Caught by
  `AdgOrchestrator.Run.Tests.ps1`; the rule is now "only an outright failure is retried".
* **`Get-AdgDirectoryIssuer` warned on every run.** It was warning when the directory could
  not be identified, including on full runs that were never going to resume. The helper now
  reports at Verbose level and the *caller* warns once, only for a run belonging to a
  scheduled job — the only case where the absence has a lasting cost, because that job will
  read the whole directory for ever and look healthy doing it.

---

## 4. Contract 1.4 — what changed and why it is additive

| Envelope | Field | Notes |
| --- | --- | --- |
| start | `mode` | `full` \| `delta` \| `reconcile`. Must agree with `incremental`; derived from it when absent |
| start | `job` | Free-form; the key the checkpoint store uses. **Required if a checkpoint is sent** |
| start | `baseline` | The cursor a delta resumed from. Forbidden on a non-delta |
| batch | `affirmations` | Up to 5,000. A batch needs at least one observation *or* one affirmation |
| batch | `checkpoint` | The cursor covering everything in and before this batch |
| completion | `affirmation_count` | Counted the way `observation_count` is |
| completion | `checkpoint` | **Only** on `succeeded` with zero errors |

Additive in **both directions**, and the second half is new machinery worth naming.
`schema_minor` already made a later minor's *rule* apply only to payloads claiming it.
`_require_minor` is the mirror: a payload declaring `1.3` may not *use* a 1.4 field. Without
it, a collector sending `schema_version: "1.0"` with a checkpoint would have it stored, and
the version string would stop saying anything about what a payload can contain. The
PowerShell builders bump to `1.4` only when one of these fields is actually present, so a
collector that never uses them keeps sending exactly the payload it sent before.

`observations` on a batch relaxed from `minLength: 1` to `0`, and the published schema now
expresses the real rule as an `anyOf`: at least one observation **or** at least one
affirmation. A 1.0–1.3 payload is held to precisely the rule it was written against.

---

## 5. Schema

**Two tables added.**

`collector_checkpoints` — PK `(collector, job)`. Keyed on the job rather than the collector
host: a job moved to a rebuilt host that still binds the same DC has a watermark that is
still valid, and keying on the host would discard a usable cursor every time somebody rebuilt
a server. What protects the cursor is `issuer`, which the advance rule compares. Carries
`last_rejection_code` / `last_rejection_message` / `last_rejected_at`, cleared by the next
accepted advance.

`scan_run_checkpoints` — PK `(run_id, role)`, roles `baseline` and `result`. What *this run*
claimed, as opposed to where the *next* run may start.

**Columns added.** `scan_runs`: `mode`, `job`, `affirmation_count_reported`,
`affirmation_count_applied`, `affirmations_refused`. `scan_run_scopes`: `closed_absent`,
`revived`, `delta_runs_since`.

**One constraint worth naming.** `ck_scan_runs_mode_matches_incremental` pins
`(mode = 'delta') = incremental` in both directions. `incremental` is the flag
`_close_reconciled` reads, and the database is the last place it can be wrong.

---

## 6. Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| Backend, hermetic | `.\scripts\backend-test.ps1` | **5,091 passed**, 10 skipped, 886 deselected, 40.0s. Two failures appeared in a later run, both belonging to the concurrent session's `DecisionKind` governance enum — its own `test_schema_vocabulary` case, and the shared OpenAPI snapshot it had not regenerated. Neither touches this phase: the snapshot drift was compared schema by schema and is that one enum and nothing else, so it was deliberately **not** regenerated here |
| Backend, with PostgreSQL | `.\scripts\backend-test.ps1 -Smoke` | **5,968 passed**, 3 failed, 10 skipped, 1 xfailed, 35m42s — all three failures accounted for below, and all three now pass |
| Collectors (Pester) | `.\scripts\collector-test.ps1` | **1,050 passed**, 0 failed, 38.9s — 944 before this phase |
| Backend lint and types | `.\scripts\backend-lint.ps1` | **fails on the concurrent session's files only** — `ruff check`, `ruff format --check` and `mypy` all pass over every file this phase touched; see §11 |
| Frontend | `.\scripts\frontend-check.ps1` | not run — **no frontend file was touched by this phase** |

**190 tests are attributable to this phase**: 64 hermetic backend, 20 requiring PostgreSQL,
106 Pester.

| Suite | Count | What it pins |
| --- | ---: | --- |
| `backend/tests/incremental/test_modes_and_checkpoints.py` | 25 | Every checkpoint refusal, including the restored-from-backup case; mode ↔ `incremental`; drift counts only absences |
| `backend/tests/incremental/test_envelopes_1_4.py` | 25 | The 1.4 rules, and the additive rule in both directions |
| `backend/tests/contracts/test_incremental_payloads.py` | 14 | PowerShell writes the payloads with the real builders; Python validates them against the published schemas, the models and the planner |
| `backend/tests/db/test_incremental_collection.py` | 20 | Affirmations end to end; an affirming run reconciling without tombstoning what it affirmed; checkpoint safety; **a deleted principal that survives every delta and dies at reconciliation** |
| `collector/.../orchestrator/tests/*.Tests.ps1` | 88 | Configuration refusals, due-ness, mode resolution, backoff, locks, retry, the state file |
| `collector/.../ntfs/tests/AdgNtfsDigestIndex.Tests.ps1` | 18 | The index decides transmission only; send-vs-affirm; a path containing the separator |

**No existing test was weakened or deleted.** One was **corrected**: the exact-dict assertion
in `test_ingestion.py::TestBatchIdempotency` now names the three fields contract 1.4 adds to
the batch response, and is still an exact comparison. The twelve regenerated AD-graph fixtures
differ only in their embedded `schema_version`.

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| Repeated collection does not fully reprocess unchanged objects where a safe strategy exists | AD: `test_a_batch_advances_the_job_cursor` + `Resolve-AdgJobMode` tests. NTFS: `TestAnAffirmingRunStillEnumeratesItsScope` and `AdgNtfsDigestIndex.Tests.ps1` |
| Full reconciliation repairs intentionally injected drift | `test_a_deleted_principal_survives_every_delta_and_dies_at_reconciliation` — two deltas cannot see it, the reconciliation tombstones it and its edge, and reports `marked_absent=2`, `delta_runs_since=2` |
| Checkpoint advancement is transactional and safe | `TestACheckpointTrailsItsData` (four tests), plus the store's `with_for_update` read |
| A failed incremental run cannot silently declare the source current | `test_a_downgraded_run_may_not_advance_the_cursor` (server), `test_does_not_record_the_checkpoint_of_a_partial_run` and `test_makes_the_run_after_a_partial_one_read_everything` (collector), and the completion model's own rule |

### How the PostgreSQL run was done, and the three failures in it

The **shared** test database was unusable. The concurrent session left a transaction `idle in
transaction` on `adg_test` for over half an hour, holding a relation lock that blocked every
`clean_tables` truncation behind it; a run started against it made no progress in
twenty-five minutes. Killing another session's processes was not this phase's call, so the
run was done against a **private database** instead:

```
ADG_DATABASE_URL=postgresql+psycopg://adg:...@localhost:5432/adg_p7b  ADG_RUN_SMOKE_TESTS=1  pytest -q
```

It completed: **5,968 passed, 3 failed, 10 skipped, 1 xfailed, 35m42s**. The wall time is
contention, not slowness — five pytest processes from two sessions were sharing one
PostgreSQL container, and each test's fixture teardown truncates some forty tables.

**All three failures were investigated and all three now pass.**

| Failure | Cause | Resolution |
| --- | --- | --- |
| `test_ingestion.py::TestBatchIdempotency::test_a_replayed_batch_is_acknowledged_but_not_applied` | **This phase's.** The test asserts the batch response as an *exact* dict, and contract 1.4 adds `affirmed`, `refused_affirmations` and `checkpoint` to it | The expected dict now names the three new fields with their empty values. The assertion stays exact — it is the thing that made this visible, and weakening it to a subset check would remove the only guard on a response the frontend types against. `tests/db/test_ingestion.py` re-run: **25 passed** |
| `test_database_smoke.py::test_database_connectivity_probe_succeeds` | **The override.** These two connect to `ADG_DATABASE_URL` itself, and only `adg_p7b_test` exists — `adg_p7b` does not | Re-run without the override: **2 passed**. Nothing to fix |
| `test_database_smoke.py::test_readiness_endpoint_reports_ready_against_the_real_database` | Same | Same |

After the fix, the seven suites nearest this phase's changes were re-run together against the
private database — `test_ingestion`, `test_smb_ingestion`, `test_mvp_end_to_end`,
`test_incremental_collection`, `test_history_versions`, `test_history_queries`, `test_schema`
— **174 passed, 8m17s**.

The full suite has **not** been re-run end to end since that one-line test fix, because doing
so costs another 35 minutes of a contended container and the fix is confined to a literal in
one assertion. Prerequisite 1 below is to re-run it on a quiet tree.

---

## 7. Known limitations

1. **Only `ntfs_resource` may be affirmed.** It is the one kind with a published whole-object
   digest. `smb_share` has none and does not need one; a `principal` could have one, and does
   not.
2. **The NTFS saving is on the wire and in the database, not at the file server.** Every
   descriptor is still read on every scan. That is not a gap to close — it is
   [ADR-0025](../decisions/0025-incremental-collection-is-bounded-by-its-source.md).
3. **The digest index is a local file of roughly 120 bytes per path** — about 120 MB for a
   million directories. Bounded by `digestIndexMaxEntries`; paths beyond it are sent in full
   with a warning. It has not been measured on an estate that large.
4. **A reconciliation job records no checkpoint.** It runs three collectors against three
   sources and there is no single cursor that could describe where all of them got to. Each
   collector records its own through its own job, so nothing is lost — but an operator
   reading `collector_checkpoints` will not find a row for `full-reconciliation`.
5. **`delta_runs_since` counts runs that declared the scope, not runs of the job.** A tree
   scanned by two jobs is behind by whatever either of them missed, which is the right
   answer, and it means the number is not a per-job count.
6. **Drift is measured, not alerted on.** The counters are stored and returned; nothing
   thresholds them and nothing is on a screen.
7. **The orchestrator has no cross-host coordination.** Two collector hosts configured with
   the same job name against the same server-side job would each advance the same checkpoint.
   The monotonicity rule stops the cursor going backwards; it does not stop the two of them
   interleaving. One orchestrator per estate is the documented arrangement and nothing
   enforces it.
8. **`Register-AdgCollectionTask.ps1 -Register` has not been run on a machine.** The XML it
   prints was reviewed; the registration path was not exercised, because creating a scheduled
   task on the development host is a change to the host.
9. **A `timestamp` checkpoint kind exists in the contract and nothing emits one.** It is
   there for a source that has nothing better; AD deliberately does not use it.

---

## 8. Security and privilege assumptions

* **No new privilege, anywhere.** A `uSNChanged` filter is an ordinary LDAP search any
  authenticated domain account may issue. `invocationId` is read from the NTDS Settings
  object in the configuration naming context, which authenticated users can read. No
  collector asks for anything it was not already asking for, and all of them remain read-only
  ([ADR-0004](../decisions/0004-read-only-collector-posture.md)).
* **No new endpoint, so no new authorization decision.** The capability boundary in
  `app/api/__init__.py` is untouched by this phase and its exhaustiveness audit still passes.
* **Affirmations widen no trust boundary.** An affirmation is refused unless it matches a
  digest the server already holds, so the worst a compromised or buggy collector achieves is
  a refusal and a full re-send. It cannot make ADG keep a stale ACL.
* **Checkpoints are not secrets, and they are not free either.** `collector_checkpoints`
  holds a USN and a DC's distinguished name — no credential — but an operator who can write
  it could make a job skip a range. It is written only by the ingestion path, under the
  existing `collectors:ingest` capability.
* **The orchestrator holds no credential.** The API key is read from the environment variable
  the collector configurations name, so it is never in a configuration file, a command line,
  or a scheduled-task argument list. `Register-AdgCollectionTask.ps1` warns against running
  as SYSTEM and says why: on a member server SYSTEM authenticates to the domain as the
  *computer account*, so what the collector can read becomes whatever that computer was
  granted.
* **Registering the scheduled task needs local administrator on the collector host** and
  nothing in the domain. It prints by default and creates nothing without `-Register`.

---

## 9. Migration and compatibility notes

* **`0008_incremental_collection` is additive.** Two new tables, eight new columns (every one
  nullable or defaulted), two check constraints. Nothing existing is dropped or narrowed.
* **One backfill, one `UPDATE`.** Existing rows get `mode = 'delta'` where `incremental` is
  true, rather than defaulting to `full`: a run recorded as incremental *was* a delta in the
  only vocabulary it had, and filing it beside runs that read everything would misstate the
  history Phase 7A just built.
* **`downgrade()` removes exactly what `upgrade()` added.** Checkpoints are lost; every
  current-state and history table is untouched. The round trip was run against the
  development database.
* **The revision graph branches.** This revision descends from `0007_history_model`, and so do
  the concurrent session's. They have already chained `0009_risk_findings` onto this one and
  added `0012_merge_concurrent_phases` to bring the graph back to a single head; `alembic
  heads` reports one head. Nothing here needs changing, and **this phase's revision must not
  be re-pointed** without checking that merge.
* **A 1.0–1.3 collector is unaffected.** It sends no 1.4 field, the server derives `mode` from
  `incremental`, and every rule it is held to is the rule it was written against.
* **A 1.4 collector against an older server** would have its new fields rejected by
  `extra="forbid"`. That is the normal direction for this contract and unchanged by this phase.
* **Ingestion writes slightly more** on runs that carry a checkpoint: one locked read and one
  upsert per batch. A run with no checkpoint is unchanged. The affirmation path *reduces*
  writes substantially and is four set-based statements regardless of how many objects it
  confirms. **Neither effect has been measured**; see prerequisite 3.

---

## 10. Prerequisites for the next prompt

1. **Re-run the full PostgreSQL suite on a quiet tree**, against the ordinary
   `ADG_DATABASE_URL`. It was run during this phase and is accounted for in §6 — 5,968
   passed with three explained failures, all since resolved — but it has not been re-run
   since the one-line assertion fix, and the two `test_database_smoke.py` cases can only be
   demonstrated green on the real database URL. The blocker was another session's
   `idle in transaction` on `adg_test`; check `pg_stat_activity` before starting.
2. **Commit this phase, and re-check the two gates its absence breaks.** See §11: HEAD has
   this phase's OpenAPI schemas without the models that produce them, and a README linking
   three documents that are not committed. Committing the files listed there fixes both;
   `backend/app/models/schema.py` has to be reconciled with the concurrent session's edits to
   the same file first.
3. **Clear the lint gate.** `ruff check`, `ruff format --check` and `mypy` all pass over every
   file this phase touched, and fail over `app/changes/`, `app/governance/`,
   `tests/changes/`, `tests/governance/` and two `tests/db/` files belonging to the concurrent
   session. Whoever merges the two phases owns that.
4. **Measure the affirmation path.** The claim is that a quiet tree costs a key and a digest
   per directory instead of a resource and its entries. It is true by construction and has not
   been timed. `scripts/ntfs-benchmark.ps1` is the place to do it, and the number worth having
   is ingest time per thousand directories, affirmed versus observed.
5. **Surface the drift counters.** `closed_absent`, `revived` and `delta_runs_since` are on
   `scan_run_scopes` and returned by `POST /completion`; `affirmations_refused` and `mode` are
   on `scan_runs` and returned by `GET /scan-runs/{id}`. None of them reaches the Collectors
   page, and the drift number is the one an auditor would ask about first.
6. **Decide whether `/api/v1/collection/operations` should report checkpoint health.**
   `CheckpointStore.read_many` exists and nothing calls it. A job whose cursor has been
   refused keeps succeeding and keeps resuming from the same stale point, and
   `last_rejection_code` is the only thing that says so.
7. **Exercise `Register-AdgCollectionTask.ps1 -Register`** on a real collector host before it
   is documented as tested.

---

## 11. `git status --short`, and why nothing was committed

**Nothing was committed.** A second agent session was building the governance, risk,
change-feed and simulation phases in this same working tree for the whole of this phase. At
the time of writing, `git status --short` lists **126 paths**, of which roughly half belong to
that session, and several files — `backend/app/models/schema.py`,
`backend/app/domain/__init__.py`, `docs/decisions/README.md`, `README.md`,
`docs/contracts/v1/openapi.json` — carry edits from **both**.

Committing would therefore either attribute half of another phase to this one, or require
splitting files that git stages whole. The other session's tests currently fail lint and mypy,
so the commit would also not be a coherent tree. The index was empty and was left empty.

### HEAD is currently inconsistent, and this phase is the fix

While this handoff was being written the concurrent session committed its Phase 7C as
`56a6037` and `5537b16`. Those commits swept in **two pieces of this phase's work** from the
shared working tree and left the rest of it behind:

| At HEAD | State |
| --- | --- |
| `docs/contracts/v1/openapi.json` | Contains this phase's response schemas — `CheckpointAdvanceView`, `RefusedAffirmationView` and five more — because it was regenerated from a tree that had them |
| `backend/app/api/scan_runs.py` | Does **not** define them |
| `README.md` | Contains this phase's *Collection cadence* section, linking `docs/architecture/incremental-collection.md` and ADRs 0025/0026 |
| those three documents | Do not exist |

So `tests/contracts/test_openapi_snapshot.py::test_the_snapshot_is_current` **fails at HEAD**,
hermetically, and the README links three files that are not there. `backend/app/models/schema.py`
at HEAD is *not* affected — it carries neither this phase's tables nor the concurrent
session's — so nothing at HEAD declares a table that no migration creates.

**Committing this phase's files repairs all of it**, and that is the first thing the next
prompt should do. It was not done here for the reason above: `backend/app/models/schema.py`
in the working tree now holds this phase's two tables *and* the concurrent session's
governance, risk and simulation tables, and git stages a file whole. Committing it would put
table declarations into HEAD whose migrations are still untracked — trading one inconsistency
for another — and splitting it would mean rewriting a file another agent is actively editing.
That reconciliation belongs to whoever merges the two phases, not to a unilateral commit from
inside one of them.

`git log --oneline -1` is **`5537b16`** — *Phase 7C: record the commit hash in the handoff*,
the concurrent session's. Nothing in this phase was committed, and the index was left empty.

### The files that belong to this phase

**Added**

```
backend/app/domain/incremental.py
backend/app/ingestion/checkpoints.py
backend/tests/incremental/                       (__init__.py + 2 suites)
backend/tests/db/test_incremental_collection.py
backend/tests/contracts/test_incremental_payloads.py
database/migrations/versions/0008_incremental_collection.py
collector/powershell/orchestrator/               (module, 5 function files, 2 entry points,
                                                  3 test suites, 1 exporter, example config, README)
collector/powershell/ntfs/functions/AdgNtfsDigestIndex.ps1
collector/powershell/ntfs/tests/AdgNtfsDigestIndex.Tests.ps1
docs/architecture/incremental-collection.md
docs/decisions/0025-incremental-collection-is-bounded-by-its-source.md
docs/decisions/0026-an-affirmation-is-verified-and-a-checkpoint-trails-its-data.md
docs/handoffs/phase-07b-incremental.md
```

**Modified — this phase only**

```
backend/app/api/scan_runs.py
backend/app/contracts/v1/common.py
backend/app/contracts/v1/envelopes.py
backend/app/history/writer.py
backend/app/ingestion/plan.py
backend/app/ingestion/service.py
backend/tests/support/history.py
backend/tests/db/test_ingestion.py                           (the 1.4 response fields)
backend/tests/fixtures/ad_graph/*.json                       (12 files, regenerated)
collector/README.md
collector/powershell/ad/AdgCollector.ActiveDirectory.{psm1,psd1}
collector/powershell/ad/Invoke-AdgAdCollector.ps1
collector/powershell/common/AdgCollector.Common.{psm1,psd1}
collector/powershell/ntfs/AdgNtfsCollector.{psm1,psd1}
collector/powershell/ntfs/Invoke-AdgNtfsScan.ps1
collector/powershell/ntfs/functions/{AdgNtfsConfig,AdgNtfsObservation,AdgNtfsScan}.ps1
collector/powershell/smb/Invoke-AdgSmbScan.ps1
docs/contracts/collector-protocol.md
docs/contracts/v1/{common,observation-batch,scan-run-start,scan-run-completion}.schema.json
```

**Modified — shared with the concurrent session** (both phases' edits are present)

```
backend/app/models/schema.py          this phase: the two tables and eight columns
backend/app/domain/__init__.py        this phase: six exports from app.domain.incremental
docs/decisions/README.md              this phase: the rows for 0025 and 0026
README.md                             this phase: the "Collection cadence" section
docs/contracts/v1/openapi.json        regenerated; covers both phases' response models
```

**Untouched by this phase** — everything under `backend/app/{changes,governance,risk_engine,
simulation}`, `backend/tests/{changes,governance}`, `backend/tests/db/test_change*.py`,
`backend/tests/db/test_simulation.py`, `frontend/`, `backend/app/{auth,config,main}.py`,
`backend/app/api/__init__.py`, `backend/app/repositories/`, `backend/app/history/repository.py`,
`backend/tests/db/conftest.py`, `.env.example`, `docs/architecture/mvp-capabilities.md`,
`docs/contracts/derived-responses.md`, and migrations `0008_change_feed_index`,
`0008_governance_model`, `0009_risk_findings`, `0011_simulation_overlays`,
`0012_merge_concurrent_phases`.

---

## 12. Intentionally deferred

* **Any HTTP surface for the schedule.** The orchestrator is collector-side and its state
  lives on the collector host; the server records what runs told it and nothing more. The
  prompt's required work names configuration and an entry point, not routes.
* **Any frontend.** The drift counters, the affirmation counts and the checkpoint state are
  all reachable from the API, and none of them is on a screen. Prerequisite 4.
* **Alerting on drift.** Measured and reported; no threshold, no notification.
* **Affirmations for `smb_share`.** No published whole-object digest exists for it, and an SMB
  scan reads tens of objects per server.
* **A collector service.** `collector/service/` is still empty. One scheduled task calling
  `Invoke-AdgCollection.ps1` does what a service would, needs nothing to host it, and is what
  the prompt asked for.
