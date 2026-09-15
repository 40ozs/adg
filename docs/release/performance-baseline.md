# ADG performance baseline

**Status:** accepted
**Phase:** Release Audit
**Date:** 2026-09-15

Every number here was **measured during this audit**, on the machine named below, by a
harness committed to the repository. Nothing is estimated, and where something is *not*
measured this document says so rather than leaving a gap that reads like a result.

```
Windows-11-10.0.26200-SP0, Intel64 Family 6 Model 198 Stepping 2 (GenuineIntel)
Python 3.13.14, PostgreSQL 16 in Docker Desktop, 2026-09-15
```

Two kinds of number, kept apart on purpose — the convention Phase 4C set and this document
keeps:

* **Statement counts are asserted**, in `tests/db/test_access_performance.py` and
  `tests/db/test_query_cost.py`. A statement count is the same number on every machine, so
  it can be a test.
* **Milliseconds are recorded here and asserted nowhere.** A threshold in milliseconds passes
  on a fast machine and fails on a busy one until nobody trusts the suite.

---

## 1. The estate these numbers describe

Built by `tests/support/access_estate.py`, which the cost *tests*, the access benchmark and
the release benchmark all import — so the assertions and the timings are about the same
thing.

At size *N*: one server, *N* shares, *N* share-root directories, *N*+2 principals, and **one
group holding every user**, named on every share ACL and every directory DACL.

That shape is deliberate. An estate granting each user access directly is linear however the
query is written and would measure nothing. A single broad group is what real ACLs carry, and
it is what makes "who can reach this" quadratic if computed naively.

Sizes 10, 100 and 400 — a fortyfold range.

---

## 2. Effective access

```
python -m tests.benchmarks.access_benchmark --sizes 10 100 400 --repeat 10
```

| Shape | Size | Rows | Min (ms) | Median (ms) | p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `one_principal_one_resource` | 10 | 1 | 9.12 | 9.98 | 11.17 |
| `every_principal_on_one_share` | 10 | 11 | 9.51 | 10.06 | 11.98 |
| `every_share_for_one_principal` | 10 | 10 | 10.14 | 10.36 | 11.25 |
| `one_principal_one_resource` | 100 | 1 | 9.51 | 10.26 | 10.92 |
| `every_principal_on_one_share` | 100 | 101 | 18.07 | 18.41 | 70.63 |
| `every_share_for_one_principal` | 100 | 100 | 18.01 | 18.82 | 21.59 |
| `one_principal_one_resource` | 400 | 1 | 9.14 | 10.02 | 10.56 |
| `every_principal_on_one_share` | 400 | 401 | 217.13 | 221.14 | 282.20 |
| `every_share_for_one_principal` | 400 | 400 | 214.88 | 219.53 | 583.23 |

**The detail answer is flat**: 9.98 → 10.26 → 10.02 ms as the estate grows fortyfold. The
question a UI issues most is independent of estate size, which is the property that matters.

The two listings grow — and they are also returning forty times more rows, so the honest
question is what a **fixed page** costs. This audit did not re-measure that shape; the
figures below are the ones Phase 4C recorded, reproduced here because they are the reason the
limitation exists and are cited by `known-limitations.md` §4.1:

| Estate | `resource -> principals` | `principal -> shares` |
| ---: | ---: | ---: |
| 100 | 13.1 ms | 12.2 ms |
| 400 | **149.3 ms** | 12.0 ms |

`resource -> principals` follows the estate at a fixed page and `principal -> shares` does
not. This is Phase 4C finding 3, held by a **strict `xfail`** so that it fails if it ever
starts passing. It is the known cost of the question "who can reach this", and the ceiling
and truncation reporting exist because of it.

> These numbers reproduce the ones recorded in
> `docs/architecture/effective-access-performance.md` closely (9.61/17.17/221.51 ms medians
> there against 9.98/18.41/221.14 here). That agreement is worth more than either number
> alone: it says the shape is stable across runs and that the earlier measurement was sound.

---

## 3. Risk evaluation, history diffs and simulation — measured for the first time

Phase 8A's handoff said it plainly: *"the engine has been run against MVP-sized data only"*,
with "measure it" listed as work for a later phase. Nothing had measured risk evaluation,
history comparison or simulation. This audit wrote
`tests/benchmarks/release_benchmark.py` and did.

```
python -m tests.benchmarks.release_benchmark --sizes 10 100 400 --repeat 8
```

| Shape | Size | Rows | Min (ms) | Median (ms) | p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `risk_evaluate_estate` | 10 | 0 | 11.24 | 13.40 | 15.76 |
| `risk_evaluate_run` | 10 | 0 | 18.21 | 19.58 | 20.80 |
| `changes_feed` | 10 | 40 | 84.63 | 87.06 | 90.29 |
| `changes_compare` | 10 | 64 | 40.79 | 41.81 | 42.96 |
| `simulation_preview` | 10 | 110 | 187.34 | 200.71 | 247.20 |
| `risk_evaluate_estate` | 100 | 0 | 44.13 | 45.40 | 47.09 |
| `risk_evaluate_run` | 100 | 0 | 54.40 | 56.11 | 59.76 |
| `changes_feed` | 100 | 100 | 139.21 | 141.16 | 144.60 |
| `changes_compare` | 100 | 604 | 104.62 | 107.04 | 169.03 |
| `simulation_preview` | 100 | 500 | 353.40 | 361.08 | 425.52 |
| `risk_evaluate_estate` | 400 | 0 | 527.81 | 626.41 | 684.14 |
| `risk_evaluate_run` | 400 | 0 | 856.15 | 864.85 | 925.47 |
| `changes_feed` | 400 | 100 | 123.66 | 128.68 | 135.85 |
| `changes_compare` | 400 | 2404 | 232.41 | 237.50 | 293.81 |
| `simulation_preview` | 400 | 500 | 3151.40 | 3217.29 | **3538.92** |

`Rows` is what the shape produced: findings for the risk passes (zero — see §3.5), changes
returned for the history shapes, and **pairs evaluated** for the simulation, because a
what-if's cost follows what it had to resolve rather than what it returned.

### 3.1 The change feed is bounded, and it is the good news

87 → 141 → 129 ms for a **fixed 100-row page** across a fortyfold estate. A page of the
change feed does not follow the estate. This is the screen an operator refreshes.

### 3.2 A comparison is linear in what it returns

41 ms/64 changes → 107 ms/604 → 238 ms/2,404. Per change: 0.65 → 0.18 → 0.10 ms. It gets
*cheaper* per row as the batch grows, which is what a set-based diff should do. Comparison
is not a scaling risk; it is bounded by `COMPARISON_CEILING` and reports truncation.

### 3.3 Risk evaluation is superlinear, and that is the headline

| Estate | Full pass | Growth vs previous |
| ---: | ---: | --- |
| 10 | 13.40 ms | — |
| 100 | 45.40 ms | 10× estate → 3.4× time |
| 400 | 626.41 ms | 4× estate → **13.8× time** |

Between 10 and 100 it is comfortably **sublinear**. Between 100 and 400 it turns over and
grows faster than the estate. At 400 shares a full pass is 0.6 s; extrapolating that curve to
an estate with millions of access-control entries is exactly what this measurement says not
to do.

**This is why `ADG_ALERTS_ON_RUN_COMPLETION` defaults to off**, and the measurement now
supports a decision that was previously taken on caution alone. Drive the rules from a
schedule (`python -m app.operations evaluate-risks`) and measure your own estate before
turning inline evaluation on.

### 3.4 The incremental pass is *not* cheaper than the full one — as measured here

`risk_evaluate_run` is slower than `risk_evaluate_estate` at every size (19.6 vs 13.4, 56.1
vs 45.4, 864.9 vs 626.4 ms).

That is **not** a defect, and it is **not** a refutation of incremental evaluation. It is a
property of what this shape measures: it evaluates *the run that built the entire estate*, so
the "only what one run moved" narrowing excludes nothing, and the pass pays for the narrowing
queries on top of a full load. This is the first-collection case, and for it the full pass is
the right one to run.

> **What remains unmeasured:** the case incremental evaluation exists for — a run that
> changed *little* against an estate that is *large*. The claim that it is then nearly free
> is untested. Anyone relying on it should measure it, and the harness is now there to do it
> in.

### 3.5 Every risk pass found zero findings, and that is a caveat on §3.3

The benchmark estate is one broad group on every share — deliberately built to stress the
*access* questions, not to trip risk rules. So these numbers measure **loading every fact and
running every enabled rule**, which is the dominant cost, but they do not include the cost of
*writing and reconciling* findings. A real estate that trips rules will cost more than this.

### 3.6 Simulation is the most expensive request the API serves

200 → 361 → **3,217 ms** for a proposal containing **one change**. At 400 shares a single
what-if takes over three seconds, with p95 at 3.5 s.

`pairs_evaluated` caps at 500 at both 100 and 400, which is the **bounds doing their job** —
the engine stopped and the report says so through its `truncation` block rather than running
until something gave way. The cost is in resolving the affected scope, and that scope is the
whole estate for a change to a group every ACL names.

Two things follow, and both are already true in the product:

* `simulations:run` is deliberately withheld from `viewer`. An account that may *read* what
  somebody else ran must not thereby be able to make the estate resolve a thousand new pairs.
* The bounds are not a nicety. A what-if without them would not terminate usefully on a real
  estate.

---

## 4. Collection cost

Unchanged by this audit and recorded where it was measured:

* **Membership graph** — `docs/architecture/ad-graph-validation.md`, with query plans.
* **NTFS scan against a real volume** — `docs/architecture/ntfs-scan-performance.md`,
  including the incremental digest path, which reduces a quiet tree to a key and a digest per
  directory.

Both have their own harnesses (`graph_benchmark.py`, `ntfs_benchmark.py`) and both still run.

---

## 5. Migration cost, measured

A clean install from an empty database, all 17 revisions:

```
alembic upgrade head    →  2.67 s
```

Followed by 45 schema-parity and constraint tests against that freshly built database, all
passing — so the migration is verified to produce what `app/models/schema.py` declares, from
empty, at head `0015_remediation_change_plans` (a single head; see
[`release-readiness.md`](release-readiness.md) §4).

---

## 6. How to reproduce every number here

```powershell
# Effective access
cd C:\code\adg\backend
.\.venv\Scripts\python.exe -m tests.benchmarks.access_benchmark --sizes 10 100 400 --repeat 10

# Risk, history and simulation
.\.venv\Scripts\python.exe -m tests.benchmarks.release_benchmark --sizes 10 100 400 --repeat 8

# Collection
cd C:\code\adg
.\scripts\graph-benchmark.ps1 -Scale large -Database
.\scripts\ntfs-benchmark.ps1 -Scale medium -Database
```

Both Python benchmarks create and migrate their own `<database>_bench` and **truncate it on
every run**. They no longer touch `<database>_test`; that they used to is finding P-1 in
[`release-readiness.md`](release-readiness.md).

---

## 7. What has not been measured

Stated so the silence is not mistaken for a result.

1. **No estate larger than 400 shares.** Everything above extrapolates at your own risk, and
   §3.3 is a concrete warning against extrapolating the risk curve.
2. **No concurrency.** Every number is a single client against an idle server. No load test,
   no connection-pool saturation test, no measurement of what two simultaneous simulations
   cost.
3. **The steady-state incremental risk pass** (§3.4).
4. **Ingestion throughput at scale.** The demo seeder has a `large` profile for exactly this
   and it was not run for this audit.
5. **The frontend.** Server-render times are not measured; the production build completes and
   the bundle sizes are in the build output.
