# Handoff — Phase 3C (`phase-03/03-ntfs-performance-validation.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-03b-ntfs-boundaries.md](phase-03b-ntfs-boundaries.md)
**Contract version after this phase:** `1.3` — unchanged. No schema, no migration, no API field.

## Scope completed

The Phase 3B handoff said the thing worth more than any number of further mocked cases was an
automated walk of a real tree, and that a manual one had already found two shipped defects in
a single run. This phase built it.

1. **A test-tree generator** (`scripts/windows-test-tree/`) that builds real NTFS trees on a
   local volume — eleven hand-specified cases for correctness, and balanced trees of known
   size and known ACL variety for cost — and emits a **manifest** recording, per directory,
   how it was built and what the collector is therefore expected to say about it.
2. **An end-to-end validation suite** (285 tests) that walks that tree through the shipped
   entry point and checks three things against each other: what the generator built, what
   Windows stored on a **second independent read**, and what the collector reported.
3. **Four defects found and fixed.** Three in the NTFS collector, one of them a batch the API
   rejects outright; the fourth is the same bug in the SMB collector, shipped since Phase 2A.
4. **Measured performance** at two tree sizes, four concurrency settings, with and without
   file scanning — recorded with the machine and the method in
   [`ntfs-scan-performance.md`](../architecture/ntfs-scan-performance.md).
5. **N+1 measured rather than assumed.** No endpoint's statement count grows with the size of
   its answer; two *duplicated reads* did exist and are gone.
6. **A safe-defaults profile** for production scans, in code and in a documented
   configuration file that a test holds to the code.
7. **315 new tests** — 306 Pester and 9 Python. The collector suite went 628 → 934; the
   backend smoke suite 250 → 259.

## Four defects, found by walking a real tree

Every one of them passed every test in the repository beforehand.

### 1. A junction the policy declined was reported as a depth-limit failure

A default walk of the generated tree produced:

```text
\\...\08-reparse\sibling-link holds 1 subdirector(ies) that were not walked:
maxDepth is 20 and this directory sits at that depth.
```

It sits at depth 2. The walk stopped because `reparsePointPolicy` is `skip`, and it expressed
"do not descend past this" by queueing the junction at `maxDepth` — so the generic depth-limit
branch fired with the generic message. An operator would raise `maxDepth`, re-run, and get the
identical message; and the run's own account of why it could not reconcile named a setting
that had nothing to do with it. It also inflated `SkippedDepthLimited`.

Fixed with an explicit `NoDescend` on the queue entry. Three spurious errors on this tree
became none.

### 2. A dangling junction was reported as a rights problem

Following a junction whose target no longer exists produced `access_denied` with a message
about `FILE_LIST_DIRECTORY` and `READ_CONTROL` — sending somebody to look at permissions on a
directory whose permissions are fine. It now reports `reparse_target_unreadable` and names the
link. A decommissioned volume is an ordinary finding, and a different one.

The same fix stops the walk **listing** a junction it has already decided not to descend into:
every outcome of that listing was discarded, and it was one round trip per junction.

### 3. One orphaned SID on two directories made a batch the API rejects

The worst of the four, and invisible until a tree carried the same unresolved trustee on two
folders. Each resource group reports a `principal` observation describing the SID; both landed
in one batch; the contract forbids a batch from carrying one `source_key` twice, and the API
rejects the whole batch with a **422**.

In an estate an orphaned SID is orphaned *estate-wide* — it sits on dozens of folders — so this
is not a corner case. It is a collector that cannot submit.

The batch writer now drops a repeat within a batch, keeps the finding, and clears the set at
each batch boundary (a key repeating in a different batch is not a repeat: the server keys
observations by `(run_id, source_key)`). The same applies to two identical ACEs in one DACL,
which is possible because an ACE's key deliberately excludes `order_index`.

`scripts/validate-collector-output.ps1` was the thing that said so, on the collector's own
output. It had been reporting "no findings" for two phases because no fixture had a key repeat.

### 4. The SMB collector carried the identical bug, and had since Phase 2A

`Split-AdgObservationBatch` chunked by offset with no key check. An orphaned SID on two shares
of one server produces the same rejected batch. Fixed the same way, and `observation_count`
now reports what the batches carry rather than what the scan accumulated — otherwise the run
would claim more coverage than it delivered, which the output validator rightly refuses.

This mirrors Phase 3B, where the access-mask defect turned out to be in both collectors.

### And one performance defect, found by the benchmark

The first concurrency measurement had **concurrency 8 slower than concurrency 1** (76.9 against
95.5 paths/s). `Invoke-AdgParallelMap` ran `Import-Module -Force` inside the parallel body —
once per *directory*, re-dot-sourcing seven files every time, because `-Force` defeats the
already-loaded check. `ForEach-Object -Parallel` reuses a runspace pool, so without `-Force` the
import happens once per runspace. Removing it turned a 20% regression into a 38% gain.

No test could have caught it: the walk suites pin concurrency at 1 deliberately, because a
Pester mock does not cross a runspace boundary.

## Files and modules added or materially changed

### The generator and the harnesses

| File | Contents |
| --- | --- |
| `scripts/windows-test-tree/AdgTestTree.psm1` | **New**, 1,046 lines. The declarative case specification, the two-pass builder, raw-descriptor ACE writing, junctions, the manifest, and a cleanup that resets every ACL it wrote |
| `scripts/windows-test-tree/New-AdgTestTree.ps1` | **New.** The CLI: `-Profile`, `-Force`, `-Remove`, `-ManifestPath`, and the UNC-reachability report |
| `scripts/windows-test-tree/README.md` | **New.** The cases, the manifest, the two traps, and the one case that cannot be built unelevated |
| `collector/powershell/ntfs/tests/AdgNtfsTreeValidation.Tests.ps1` | **New**, 285 tests against a real tree |
| `scripts/ntfs-benchmark.ps1` | **New.** The collector-side benchmark |
| `backend/tests/benchmarks/ntfs_benchmark.py` | **New.** Ingestion throughput, per-request statement counts, and `EXPLAIN` plans |
| `backend/tests/db/test_query_cost.py` | **New**, 9 tests. The N+1 property, and the two duplicated reads, pinned |

### The collector

| File | Contents |
| --- | --- |
| `functions/AdgNtfsWalk.ps1` | `NoDescend` on the queue entry; the reparse-aware enumeration failure; **defects 1, 2 and the concurrency regression fixed here** |
| `functions/AdgNtfsScan.ps1` | The batch writer deduplicates by `source_key` within a batch. **Defect 3** |
| `functions/AdgNtfsCheckpoint.ps1` | The frontier round-trips `NoDescend`, `IsReparsePoint`, `ReparseTarget` |
| `functions/AdgNtfsConfig.ps1` | `Get-AdgNtfsSafeDefault`, and every default routed through one named fallback table |
| `Invoke-AdgNtfsScan.ps1` | `-SafeDefaults`, and a warning when it is used without a checkpoint |
| `adg-ntfs-safe-defaults.example.json` | **New.** The production profile, every value explained |
| `smb/functions/AdgSmbScan.ps1` | `Split-AdgObservationBatch` deduplicates; `observation_count` reports what was sent. **Defect 4** |

### The backend

| Module | Contents |
| --- | --- |
| `app/repositories/resources.py` | `NtfsBoundaryVerification.parent` — the parent row the verdict was reached against, carried out instead of fetched twice |
| `app/services/resources.py` | `resource_detail` uses it; `ntfs_acl` accepts an already-read resource, which `share_root_acl` now passes |

### Documentation

| File | Contents |
| --- | --- |
| `docs/architecture/ntfs-scan-performance.md` | **New.** Every measurement, the machine, the method, and the scale limits |
| `collector/powershell/ntfs/README.md` | The safe-defaults section, the corrected junction diagnostics, the deduplication rule |
| `collector/powershell/smb/README.md` | The deduplication rule |
| `README.md` | The two new scripts |

## Important architecture decisions

### The expectation is recorded at construction, never derived from the result

Each generated directory carries `expectedBoundary` and `expectedBoundaryReason`, written when
the case is declared. Deriving them afterwards from the ACL would mean testing the
implementation against itself: the same arithmetic on both sides, agreeing perfectly and
proving nothing. The manifest is what the tree *was built to be*, and the collector is checked
against that.

### The verification reads Windows through a different API than the collector does

The collector reads a descriptor through `FileSystemAclExtensions::GetAccessControl`. The
suite re-reads it through `Get-Acl` — the PowerShell provider stack, a genuinely different
route to the same bytes. Verifying it with the same two calls would compare the collector to
itself, and a mistake in either call would agree with itself perfectly.

**It is a real independent check and not a perfect one**, and the suite says so: both routes end
at the same Windows API family, and only a second implementation would remove that.

### The tree is built in two passes, and the order is load-bearing

Every directory and junction is created first; every ACL is written afterwards, root first.
A directory carrying a descending `Deny` cannot have children created beneath it — the
generator obeys the ACLs it writes like any other process — and a directory protected before
its children exist propagates nothing to them. Windows propagates an inheritable entry to the
children that already exist, which is what makes the second pass correct.

### Deduplication is per batch, never per run

A `source_key` may not repeat inside one batch. Across batches it may, because the server keys
observations by `(run_id, source_key)` and ignores the second arrival — so holding the key set
for a whole run would both drop real observations and grow without bound on a large estate.
The set is cleared with each batch.

### N+1 is asserted as a property, the duplicated reads as exact counts

`test_query_cost.py` asserts that a paged endpoint issues the same number of statements for one
row as for a hundred. That holds for any implementation and needs no editing when one changes.
The two duplicated reads are pinned differently — `reading("ntfs_resources") == 2` for
`GET /resources/{path}` — because that is the shape of the defect: a row fetched, then fetched
again by a layer that did not know it was already in hand. A total would break on any unrelated
change; a per-table count says exactly what the endpoint may do.

### The benchmark counts statements rather than timing them

A millisecond is a fact about the machine the suite ran on. A statement count is a fact about
the code, it means the same thing on somebody else's hardware, and an N+1 is precisely a
statement count that moves with the size of the answer.

### A missing `source_key` is kept, not deduplicated

Such an observation is malformed and the API rejects it naming the field. Treating every keyless
observation as a repeat of the first would silently discard the rest — a far worse failure than
the 422 the caller is about to be told about.

## Schemas and contracts introduced or changed

**None.** Contract 1.3 is unchanged: no new field, no new kind, no new rule, no migration, and
no API response field added, renamed, or removed.

Two **behaviour** changes are worth naming even though the shapes are identical:

* a batch no longer carries a repeated `source_key`. That was already forbidden by
  `docs/contracts/collector-protocol.md`; the collectors now honour it;
* an SMB run's `observation_count` reports what its batches carry. It previously reported what
  the scan accumulated, which differed only in the case that produced an unsendable batch.

One new collector **error code**, which is a diagnostic rather than a contract enumeration:
`reparse_target_unreadable`, for a junction that was followed and could not be listed.

## Tests run and exact results

All run on 2026-09-14 against this tree.

| Gate | Command | Result |
| --- | --- | --- |
| Collector | `.\scripts\collector-test.ps1` | **934 passed, 0 failed** (was 628) |
| Backend hermetic | `pytest tests -m "not smoke"` | **3,416 passed, 9 skipped**, 259 deselected |
| Backend smoke | `pytest tests -m smoke` (PostgreSQL) | **259 passed** (was 250) |
| Backend lint | `.\scripts\backend-lint.ps1` | **passed** — ruff clean, 120 files formatted, mypy strict clean on 119 source files |
| Frontend | `.\scripts\frontend-check.ps1` | **passed** — lint, typecheck, unit tests, build |
| Collector output | `.\scripts\validate-collector-output.ps1 -Path <real dry run>` | **no findings** — after defect 3; **3 errors** before it |
| Collector benchmark | `.\scripts\ntfs-benchmark.ps1 -Scale small\|medium` | ran; figures in `ntfs-scan-performance.md` |
| Backend benchmark | `python -m tests.benchmarks.ntfs_benchmark --scale small` | ran; no N+1 found |

The smoke suite ran against `adg_phase3c_test`
(`ADG_DATABASE_URL=...:5432/adg_phase3c`), not the developer default, because two sessions
share this workstation and the autouse `clean_tables` fixture truncates everything. The two
tests in `tests/test_database_smoke.py` probe `ADG_DATABASE_URL` itself and were therefore run
separately against the development URL, where they pass.

### New tests

| Suite | Count | Covers |
| --- | --- | --- |
| `…/tests/AdgNtfsTreeValidation.Tests.ps1` | 285 (new) | **A real NTFS tree.** Owner, control flags, and every ACE in DACL order against a second read of Windows; all eleven cases' boundary verdicts and reasons; every trustee including an orphaned SID; the duplicate-ACL groups collapsing to one digest; the unlistable directory; every reparse policy including the cycle; a clean subtree reconciling; a truncated walk refusing to; and no batch carrying one key twice |
| `…/tests/AdgNtfsWalk.Tests.ps1` | 63, from 59 | The four new ones are defects 1 and 2 and their controls: no depth-limit blame for a declined junction, no listing of one, the reparse-specific enumeration failure, and the ordinary rights failure still reported as such |
| `…/tests/AdgNtfsScan.Tests.ps1` | 49, from 45 | Deduplication within a group, across a batch boundary, and the keyless case |
| `…/tests/AdgNtfsCheckpoint.Tests.ps1` | 34, from 32 | The junction verdict surviving a resume, and an older checkpoint reading as an ordinary directory |
| `…/tests/AdgNtfsConfig.Tests.ps1` | 51, from 44 | The safe-defaults profile, that it never overrides the configuration file, and **that it matches the example JSON that documents it** |
| `…/smb/tests/AdgSmbScan.Tests.ps1` | 34, from 30 | Defect 4: one key once per batch, the same key legitimately in the next, `is_final` after deduplication, and one orphaned SID across two shares end to end |
| `backend/tests/db/test_query_cost.py` | 9 (new) | The N+1 property at two page sizes across six endpoints, and the two duplicated reads |
| `backend/tests/benchmarks/ntfs_benchmark.py` | — | Not a test. A benchmark, run by hand |

### Tests changed rather than added

* `AdgNtfsScan.Tests.ps1` — `New-ObservationGroup` now emits distinct `source_key`s, and a
  fresh path per call. The writer deduplicates on them, so a fixture whose observations all
  claimed one key would be one observation by the time it reached a batch: the correct
  behaviour and the wrong fixture.

## What was measured

Full detail, with the machine and the caveats, in
[`ntfs-scan-performance.md`](../architecture/ntfs-scan-performance.md). The four numbers that
matter:

* **Memory does not grow with the tree.** 8.2 MiB retained for 781 directories, 8.8 MiB for
  19,608 — 25× the tree for 7% more memory. Scan size is bounded by time, not by the collector
  host's RAM.
* **Time is linear in directories**, ≈8–10 ms each at concurrency 1 against a local volume.
* **Concurrency helps to 8 and then stops**: 97 → 133 paths/s, flat at 8, slightly worse at 16
  while memory keeps climbing — which is why the profile sets 8.
* **Batch size is a 4× write throughput difference**, because ingestion issues a flat 7
  statements per batch whatever the batch holds.

**And the claim this phase was told not to make:** a tree of 19,608 directories holds 40
distinct ACL states, and that ratio buys storage and query cost — nothing at collection time.
Every one of those directories was opened and had its descriptor read, because that is the only
way to discover which of the 40 states it is in. Both benchmarks report `DirectoriesRead` and
`UniqueAclHashes` in separate columns for exactly this reason.

## Known limitations

1. **A directory whose *descriptor* cannot be read is still not built by the generator.** The
   owner of an object holds `READ_CONTROL` and `WRITE_DAC` implicitly, and handing ownership to
   somebody else needs `SeRestorePrivilege`. The generator builds the case it can build
   honestly — a directory whose descriptor reads and whose **contents** cannot be listed — and
   the manifest records the gap as `deniedDescriptorNotBuildable`. The unreadable-descriptor
   path stays covered by the mocked walk suite.
2. **The validation suite skips without a UNC route.** The collector identifies a directory by
   its UNC path, and on one machine the only such route is `\\localhost\C$`, which needs local
   Administrators — a right the collector must never require. Without it the suite skips with
   that reason and the benchmark refuses to run; neither rewrites itself to walk a local path,
   which would test a configuration that never ships. **On a CI agent without administrative
   shares, 285 of the 934 collector tests do not run.**
3. **The independent read is not fully independent.** `Get-Acl` and
   `FileSystemAclExtensions` are different routes to the same Windows API family. Only a
   second implementation would remove that, and nothing here pretends otherwise.
4. **The benchmarks measure a local volume.** A descriptor read over `\\localhost\C$` costs CPU
   and a page-cache hit; the same read against a remote file server costs a round trip, which
   is what `concurrencyLimit` exists to hide. The shapes transfer; the absolute figures do not,
   and the concurrency figures **understate** the gain that matters.
5. **One enormous flat directory is not measured.** The frontier is one BFS level, so a
   directory with a hundred thousand immediate children puts a hundred thousand entries on the
   queue. The concurrent stage is chunked at 512, so requests are bounded; the frontier is not,
   because a checkpoint must persist it whole. The generator builds balanced trees.
6. **Nothing measures several collectors submitting at once.** The ingestion figures are a
   single writer against an otherwise idle database.
7. **The `large` profile was not run.** It builds roughly 66,000 directories and takes several
   minutes to generate; `small` and `medium` established that the curve is linear and memory
   flat, which is what the profile exists to show.
8. **The walk still never prunes**, `inherited_from` is still never populated, the SACL is still
   never read, a directory's entry rows are still never removed, and the BUILTIN scoping hazard
   is unchanged. Phase 3B's limitations 4–9, none of which this phase touched.

## Security and privilege assumptions

**Domain Admin is not required and must not be used.** Unchanged from Phase 3B, and nothing in
this phase widens what the collector needs. The two fixes to the walk *reduce* what it does: a
junction the policy declined is no longer listed at all.

**The generator writes ACLs, and ADR-0004 is not suspended.** ADR-0004 makes the *application*
read-only — the collector never writes to a target, never enables a privilege, never takes
ownership, never modifies a descriptor to make a read succeed. That rule is about the thing
that ships. The generator is a developer and CI tool; it writes only beneath the root it is
given, only through the **Access** section of a descriptor (never the SACL, which would need
`SeSecurityPrivilege`), and `Remove-AdgTestTree` resets every ACL it wrote before deleting
anything — a directory carrying a descending `Deny` cannot otherwise be removed. Everything
runs unelevated.

**The validation suite and the benchmark need a UNC route, which on one machine means local
Administrators.** That is a fact about the *test harness*, never about the collector, and it is
why both skip or refuse rather than falling back to local paths.

**No secrets in source control.** `adg-ntfs-safe-defaults.example.json` has no credential field,
like the target example beside it.

## Migration and compatibility notes

* **No migration.** No schema change, and `alembic` was not touched.
* **No contract change.** 1.3 is unchanged; a collector at any earlier minor is unaffected.
* **No API field added, renamed, or removed.** `NtfsBoundaryVerification` gained an internal
  `parent` attribute; the JSON `boundary` and `parent` views are byte-identical.
* **`Split-AdgObservationBatch` may now return fewer observations than it was given**, when a
  source key repeated. Any caller counting the input rather than the output would overstate
  coverage; `Invoke-AdgSmbScan` is the only caller and was corrected.
* **The NTFS checkpoint format gained three frontier fields.** A checkpoint written by an
  earlier build resumes correctly: absent reads as "an ordinary directory", which is what those
  entries were. There is no version bump because the fingerprint already refuses a resume
  across a settings change, and these are not settings.
* **`-SafeDefaults` is additive.** Without it every default is exactly what it was.
* **`New-ObservationGroup` in the NTFS scan suite changed shape** (it now emits `source_key`).
  Test-only.

## Prerequisites for the next prompt

**Phase 4B (effective access) can now assume** everything Phase 3B listed, plus: the collector's
description of a real tree has been checked against Windows rather than against fixtures, and
the boundary algebra holds on real generic-mask ACEs, real inheritance breaks, real junctions,
and real duplicate ACLs across unrelated branches.

**Whoever works on collection next should know:**

1. **Run the validation suite on a machine with a UNC route, and read what it skips.** It is
   the only suite that exercises the seam between the walk and a real file system, and it found
   four defects on its first run. A CI agent without administrative shares runs 649 of the 934
   collector tests and reports a pass.
2. **The pruning decision is still open**, and still has to be decided together with what a
   reconciled `directory_tree` scope means. This phase makes the cost of *not* pruning
   concrete: every directory read, ≈8–10 ms each, linear.
3. **`inherited_from` is still derivable and still not derived.** Unchanged from Phase 3B.
4. **A `principals/{trustee}/resources` route is still outstanding**, from Phase 3A. The
   reference rows and indexes have been there since, and the benchmark shows the query plan it
   would use (`Index Only Scan using ix_ntfs_aces_trustee`).
5. **Re-run both benchmarks when the walk, the observation layer, the batching, or the resource
   queries change**, and update `ntfs-scan-performance.md`. A benchmark nobody re-runs is a
   number that used to be true.

**Anyone adding an observation kind** should know that the batch writers now enforce one
`source_key` per batch. If a new kind can legitimately describe the same subject twice in one
batch with different content, that is a contract question, not a writer question — the rule
exists because two observations with one key are indistinguishable.

## `git status --short`

Taken after the commit, so only another session's in-flight work remains:

```text
 M backend/tests/contracts/test_smb_collector.py
```

That file was already modified when this phase started — a formatting-only change by a
concurrent session — and was deliberately excluded from this phase's commit, exactly as
Phases 3A and 3B excluded it.
