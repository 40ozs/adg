# NTFS scan performance, and what it costs to store the result

**Status:** measured, Phase 3C
**Applies to:** `collector/powershell/ntfs`, `app.ingestion`, the resource query endpoints
**Re-run with:** `.\scripts\ntfs-benchmark.ps1` and `python -m tests.benchmarks.ntfs_benchmark`

This document records what a tree scan costs, on hardware that is named, with the method
written down so somebody can disagree with it. Every figure here came from one of the two
benchmarks above. Nothing in it is an estimate.

---

## The claim this document exists to keep honest

A scan of 19,608 directories finds **40 distinct ACL states**. That ratio is the reason ADG
walks trees at all, and it is the single easiest thing in this project to overclaim.

**What it buys.** Storage and query. The boundary rows are a small minority of a full walk's
output — 21 of 19,608 in the measured tree — and `acl_hash` turns "which folders share this
permission state?" into one indexed comparison instead of a join over every entry of every
DACL. A partial index over the boundaries (`ix_ntfs_resources_boundaries`) is small because
the thing it indexes is small.

**What it does not buy.** Anything at collection time. Every one of those 19,608 directories
was opened and had its security descriptor read, because reading it is the only way to
discover which of the 40 states it is in. A claim that hashing lets a scan skip directories is
a claim that ADG can know a permission has not changed without looking at it.

The walk deliberately does not prune below a non-boundary, for the same reason (Phase 3B
limitation 5). If it ever does, that will change what a reconciled `directory_tree` scope
means and the two have to be decided together.

The two benchmarks report the two costs in separate columns for exactly this reason:
`DirectoriesRead` is what the scan paid, `UniqueAclHashes` is what the database keeps.

---

## The machine

Everything below was measured on one workstation, on 2026-09-14:

| | |
| --- | --- |
| OS | Windows 11 Pro 10.0.26200 |
| Processors | 20 |
| PowerShell | 7.6.6 |
| Python | 3.13.14 |
| PostgreSQL | 16 in Docker (`adg-db-1`) |
| Volume | local NTFS, reached over `\\localhost\C$` |

**A local volume is not a file server, and this is the most important caveat here.** A
descriptor read over `\\localhost\C$` costs CPU and a page-cache hit. The same read against a
remote server over SMB costs a network round trip, and a round trip is the thing
`concurrencyLimit` exists to hide. So:

* the **shapes** below transfer — flat memory, linear time, constant statements per batch;
* the **absolute throughput** does not, and will be lower against a remote server;
* the **concurrency figures understate** the gain that matters, because the latency they
  exist to hide is nearly absent here.

Re-run both benchmarks against your own estate before treating any number as a target.

---

## Collection: what the walk costs

`.\scripts\ntfs-benchmark.ps1 -Scale small|medium`, generated trees, default settings
otherwise.

| Tree | Directories | Distinct ACLs | Boundaries | Concurrency | Paths/s | ACL reads/s | Batches | Retained (KiB) | Peak working set (MiB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| small | 781 | 17 | 9 | 1 | 96.9 | 96.9 | 8 | 8,172 | 173 |
| small | 781 | 17 | 9 | 4 | 130.5 | 130.5 | 8 | 8,351 | 207 |
| small | 781 | 17 | 9 | 8 | 133.5 | 133.5 | 8 | 15,108 | 237 |
| small | 781 | 17 | 9 | 16 | 128.7 | 128.7 | 8 | 25,852 | 289 |
| medium | 19,608 | 40 | 21 | 1 | 112.7 | 112.7 | 197 | 8,755 | 302 |
| medium | 19,608 | 40 | 21 | 8 | 124.8 | 124.8 | 197 | 46,258 | 422 |
| small **+ files** | 781 dirs + 2,340 files | 25 | — | 1 | 175.1 | 175.1 | 32 | 1,943 | 219 |

"Retained" is managed heap measured after a forced collection on both sides of the walk — what
the walk is still *holding*, rather than what it allocated. Peak working set is sampled during
the walk.

### Three things these numbers say

**1. Memory does not grow with the tree.** 8.2 MiB retained for 781 directories and 8.8 MiB
for 19,608 — a 25× larger tree for 7% more retained memory. That is the streaming design
working: observations are handed to the sink as they are produced and never accumulated. What
the walk does retain is bounded by the *frontier* and the visited set, not by the tree.

The practical consequence: scan size is limited by time, not by memory on the collector host.

**2. Time is linear in directories.** Per-directory cost is flat between the two sizes
(≈8–10 ms at concurrency 1), so a walk of N directories takes N × that. At the measured rate,
100,000 directories is roughly 15 minutes and a million is about 2.5 hours — against a *local*
volume, which is the optimistic end.

**3. Concurrency helps, and then stops.** 97 → 133 paths/s between 1 and 8, flat at 8, and
slightly worse at 16 while memory keeps climbing: each parallel runspace holds its own copy of
the collector module, and that is what the 46 MiB at concurrency 8 on the medium tree is. Hence
`concurrencyLimit: 8` in the safe-defaults profile — the point where throughput stops improving
and cost keeps rising. Expect the useful ceiling to be *higher* against a remote server, where
there is real latency to hide.

### What file scanning actually costs

The last row is the same 781-directory tree with three files in every directory and
`includeFiles` on. Against the directories-only walk of the same tree:

| | Directories only | With files | Ratio |
| --- | ---: | ---: | ---: |
| Paths read | 781 | 3,121 | **4.0×** |
| Wall clock | 8.4 s | 17.8 s | **2.1×** |
| Batches submitted | 8 | 32 | **4.0×** |
| Distinct ACL states found | 17 | 25 | **1.5×** |

Paths per second goes *up* — a file's descriptor is read and the file is never enumerated or
descended into, so it is the cheaper half of a directory's cost. That is what makes the ratio
worth writing down rather than the rate: four times the objects for roughly twice the time,
four times the API traffic, and half again as many distinct permission states.

Whether that is worth it is a question about the estate, and the multiplier there is much
worse than 4. This tree has three files per directory; a real file server has hundreds. The
default is off for that reason, and the recommendation is to turn it on for a named subtree
under investigation rather than for an estate.

### A defect the benchmark found

The first concurrency measurement showed concurrency 8 running **slower** than concurrency 1
(76.9 vs 95.5 paths/s). `Invoke-AdgParallelMap` imported the collector module with
`Import-Module -Force` inside the parallel body — that is, once per *directory*, re-dot-sourcing
seven files every time, because `-Force` defeats the "already loaded" check that makes a repeat
import free. `ForEach-Object -Parallel` reuses a pool of runspaces, so without `-Force` the
import happens once per runspace. Removing it turned a 20% regression into a 38% gain.

Nothing in the test suite could have caught this: the suites pin concurrency at 1 deliberately,
because a Pester mock does not cross a runspace boundary.

---

## Ingestion: what the backend costs to write

`python -m tests.benchmarks.ntfs_benchmark --scale small`, against PostgreSQL.

| Batch budget | Directories per batch | Observations | Statements per batch | Observations/s |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 14 | 392 | 7.0 | 441 |
| 500 | 71 | 1,988 | 7.0 | 1,398 |
| 1000 | 142 | 3,976 | 7.0 | 1,810 |

**Seven statements per batch, whatever the batch holds.** The writer is bulk: one
`INSERT … ON CONFLICT DO UPDATE` per table per batch, plus the batch claim and the run counter
update. It does not issue a statement per observation, which is the failure mode that looks
identical on a twenty-row fixture and falls over on an estate.

The consequence for an operator is the one column that does move: **batch size is a 4× write
throughput difference**, entirely because the per-batch cost is fixed. The contract's ceiling
is 1000 observations; the safe-defaults profile sets `batchSize: 500` in *directories'* terms,
which lands near the ceiling once each directory's ACEs travel with it.

---

## Query: what a read costs

Statements per request, counted with an engine listener rather than timed. A millisecond is a
fact about this laptop; a statement count is a fact about the code.

| Endpoint | Statements (1 row) | Statements (100 rows) |
| --- | ---: | ---: |
| `GET /servers` | 2 | 2 |
| `GET /servers/{server}/shares` | 3 | 3 |
| `GET /shares/{share}/acl` | 4 | 4 |
| `GET /shares/{share}/root-acl` | 5 | 5 |
| `GET /resources/{path}/acl` | 5 | 5 |
| `GET /principals/{trustee}/shares` | 6 | 6 |
| `GET /resources/{path}` | 7 | — |
| `GET /shares/{share}` | 4 | — |

**No endpoint's cost grows with the size of its answer.** That is the N+1 property, and it is
asserted as a property — not as a threshold — by `backend/tests/db/test_query_cost.py`, so it
keeps holding as the implementation changes.

### Two duplicated reads, found by measuring and removed

Neither was an N+1. Both were a row read twice in one request by two layers that did not know
the other had it:

* **`GET /resources/{path}`: 8 → 7 statements.** `verify_boundary` reads the parent to project
  from it; the response also shows the parent; each fetched it. The verdict now carries the
  parent row it judged against (`NtfsBoundaryVerification.parent`).
* **`GET /shares/{share}/root-acl`: 6 → 5 statements.** The root is looked up to learn its key,
  and `ntfs_acl` then looked the same primary key up again. It now accepts the row it was
  already handed.

Both are pinned by exact per-table assertions rather than by totals, because that is the shape
of the defect: `reading("ntfs_resources") == 2` says what the endpoint is allowed to do, and
does not break when an unrelated statement is added elsewhere.

### The plans

`EXPLAIN (ANALYZE, BUFFERS)` for the statements the resource endpoints issue. All four use an
index; none reads the ACE table sequentially.

| Query | Plan |
| --- | --- |
| One directory by key | `Index Scan using ntfs_resources_pkey`, 3 buffers |
| One directory's DACL in order | `Index Scan using ix_ntfs_aces_resource` + quicksort of 6 rows |
| The boundaries of one share | `Bitmap Index Scan on ix_ntfs_resources_boundaries` — the partial index, doing the job it exists for |
| Every directory naming one trustee | `Index Only Scan using ix_ntfs_aces_trustee` |

The sort on the second is over one DACL's entries and cannot be indexed away: the order is
`order_index NULLS LAST, ace_key`, and nulls-last over a nullable column is not the index's
order. At tens of entries per DACL the sort is noise; it would matter only for a DACL of
thousands, which is itself a finding worth reporting rather than optimizing for.

---

## Scale limits, stated rather than hidden

1. **A single run's wall clock is the binding limit, and it is linear.** Nothing here batches
   or parallelizes across trees. One tree of a million directories is one long run; ten trees
   of a hundred thousand are ten runs that can be scheduled independently, which is why
   `-RunPerScanRoot` is the production recommendation.

2. **Memory is bounded by the frontier, not by the tree — but the frontier is one BFS level.**
   A directory with a hundred thousand immediate subdirectories puts a hundred thousand entries
   on the queue at once. The concurrent stage is chunked at 512, so the *requests* are bounded;
   the frontier is not, because a checkpoint has to be able to persist it whole. Not measured,
   because the generator builds balanced trees; an estate with one enormous flat directory
   would be the case to measure.

3. **Concurrency multiplies memory, not just throughput.** Each runspace holds its own module.
   Measured: 8.8 MiB retained at concurrency 1 against 46 MiB at concurrency 8, on the same
   tree.

4. **The unreadable-descriptor path is not benchmarked.** Every failed read costs
   `retryCount + 1` attempts plus a `Test-Path`, and a tree where the collector may read
   nothing would be dominated by that. The generator cannot build the case unelevated (the
   owner of an object holds `READ_CONTROL` implicitly), so the cost is reasoned about rather
   than measured.

5. **File scanning is off by default and multiplies the path count.** A file's ACL is a real
   fact, and there are orders of magnitude more files than directories. Measure it on a named
   subtree before turning it on for an estate.

6. **These are single-collector numbers.** Nothing measures several collectors submitting to
   one API at once, and the ingestion figures above are a single writer against an
   otherwise-idle database.

---

## Reproducing

```powershell
# The collector side: builds a generated tree, walks it, reports the table above.
.\scripts\ntfs-benchmark.ps1 -Scale small
.\scripts\ntfs-benchmark.ps1 -Scale medium -KeepTree -OutFile .\.tmp\bench-medium.txt

# The backend side: needs PostgreSQL, uses its own <database>_bench database.
.\scripts\stack-up.ps1 -DbOnly
.\scripts\ntfs-benchmark.ps1 -Scale small -Database
```

The collector benchmark needs a UNC route to the tree it builds (`\\localhost\C$`), because the
collector identifies a directory by its UNC path. Without one it refuses to run rather than
silently measuring a local path, which is a configuration that never ships.

Building the tree is slower than scanning it — roughly 15 seconds for 19,608 directories against
about 175 seconds to walk them — and that cost is the generator's, reported separately.

## See also

* [`ntfs-acl-boundaries.md`](ntfs-acl-boundaries.md) — what a boundary is and how it is derived
* [`ntfs-acl-normalization.md`](ntfs-acl-normalization.md) — the normal form behind `acl_hash`
* [`ADR-0008`](../decisions/0008-acl-normal-form-and-hash.md) — why the digest exists
* `collector/powershell/ntfs/adg-ntfs-safe-defaults.example.json` — the production profile these
  measurements justify
* `scripts/windows-test-tree/README.md` — the trees both benchmarks are measured against
