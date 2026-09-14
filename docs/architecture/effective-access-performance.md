# Effective access: measured cost

**Status:** accepted
**Phase:** 4C
**Applies to:** `/api/v1/access/*`, `app.services.access`

What the three access questions cost, measured rather than estimated, and which part of each
is bounded by something the caller controls.

Two kinds of number, kept apart on purpose:

* **Statement counts** are asserted, in `tests/db/test_access_performance.py`. A statement
  count is the same number on every machine, so it can be a test.
* **Milliseconds** are recorded here and asserted nowhere, by
  `tests/benchmarks/access_benchmark.py`. A threshold in milliseconds passes on a fast
  machine and fails on a busy one until nobody trusts the suite.

---

## 1. The estate these numbers describe

Built by `tests/support/access_estate.py`, which both the tests and the benchmark use, so the
assertions and the timings are about the same thing.

At size *N*: one server, *N* shares, *N* share-root directories, *N*+2 principals, and **one
group holding every user**, named on every share ACL and every directory DACL.

That shape is deliberate. An estate where each user is granted access directly is linear
however the query is written and would measure nothing. A single broad group is what real
ACLs carry, and it is what makes "who can reach this" quadratic if computed naively.

---

## 2. Measured

```
Windows-11-10.0.26200-SP0, Intel64 Family 6 Model 198 Stepping 2
Python 3.13.14, PostgreSQL in Docker Desktop, 2026-09-14
python -m tests.benchmarks.access_benchmark --sizes 10 100 400 --repeat 10
```

| Shape | Size | Rows | Min (ms) | Median (ms) | p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `one_principal_one_resource` | 10 | 1 | 9.19 | 9.61 | 10.89 |
| `every_principal_on_one_share` | 10 | 11 | 9.33 | 9.64 | 10.25 |
| `every_share_for_one_principal` | 10 | 10 | 8.69 | 9.71 | 10.66 |
| `one_principal_one_resource` | 100 | 1 | 8.35 | 9.04 | 9.75 |
| `every_principal_on_one_share` | 100 | 101 | 16.59 | 17.17 | 17.85 |
| `every_share_for_one_principal` | 100 | 100 | 17.23 | 17.72 | 18.64 |
| `one_principal_one_resource` | 400 | 1 | 9.28 | 9.82 | 10.39 |
| `every_principal_on_one_share` | 400 | 401 | 217.08 | 221.51 | 529.54 |
| `every_share_for_one_principal` | 400 | 400 | 218.55 | 225.78 | 254.83 |

**`one_principal_one_resource` is flat**: 9.61 → 9.04 → 9.82 ms as the estate grows fortyfold.
The detail answer a UI issues most is independent of estate size.

The two listings grow, but they are also returning forty times more rows, so the interesting
question is what happens at a **fixed** page.

---

## 3. At a fixed page size, one listing is bounded and one is not

The same twenty-five-row page, against two estates:

| estate | `resource -> principals` | `principal -> shares` |
| ---: | ---: | ---: |
| 100 | 13.1 ms | 12.2 ms |
| 400 | **149.3 ms** | 12.0 ms |

`principal -> shares` and `principal -> resources` page in the database with a keyset cursor
and are flat. `resource -> principals` is not: it expands every trustee on the ACL downward,
builds the whole set of principals the resource reaches, and only then slices out the page.

The endpoint says so in its own response. It is the only access listing that can report
`page.total`, and it cannot know that number without having materialized what it counts:

```
resource -> principals   page={"limit": 5, "has_more": true, "next_cursor": "...o...", "total": 41}
principal -> shares      page={"limit": 5, "has_more": true, "next_cursor": "...k...", "total": null}
principal -> resources   page={"limit": 5, "has_more": true, "next_cursor": "...k...", "total": null}
```

### What is and is not wrong

The **resolution** work is correctly bounded: a twenty-five-row page runs exactly twenty-five
access checks whatever the estate, asserted in
`test_the_resolution_work_is_bounded_by_the_page`. The statement count is constant too. A
profile at size 400 puts 68% of the time in `select.select` — waiting on PostgreSQL, not
computing — so this is the expansion query and its result set, not the engine.

### Why it matters on a real estate

ACLs name broad groups. An ACE naming `Domain Users` makes the expanded set every user in the
domain, per request, on every page. It stays anchored to one resource, so it is not the
Cartesian product `tests/api/test_access_bounds.py` rules out, but it is unbounded in the
estate all the same.

### Why it was not fixed here

Fixing it means moving the expansion and the slice into the database, or holding a traversal
across pages. Both change the shape of `AccessService` and one of them changes what
`page.total` can mean. Phase 4C's remit is to attack the engine and leave accepted contracts
alone, so it is pinned rather than patched: a **strict `xfail`** in
`tests/db/test_access_performance.py::TestWhereTheCostStillFollowsTheEstate`. The day it is
fixed, that test fails and somebody deletes it.

---

## 4. What is asserted

In `tests/db/test_access_performance.py`, against estates of 4 and 40:

| Property | How |
| --- | --- |
| Statement count does not follow the estate | Equal counts at both sizes, for all three shapes |
| A page never exceeds its limit | `len(items) <= limit`, `page.limit == limit` |
| A partial page says so | `has_more` true and a `next_cursor` present |
| Paging does not re-fan-out | One-row and full pages cost identical statements |
| An over-large limit is refused | `limit=100000` is a 422 from the framework |
| The traversal is clamped | An absurd `max_depth` is served, not attempted |
| The inversion follows the ACL | ≤ 8 reads of `membership_edges` for 41 principals |

And in `tests/db/test_query_cost.py`, from Phase 4B, against one fixed estate: exact
statement counts per endpoint and per table.

---

## 5. Reproducing

```powershell
# statement counts (asserted)
$env:ADG_RUN_SMOKE_TESTS = '1'
backend\.venv\Scripts\python.exe -m pytest tests/db/test_access_performance.py -q

# milliseconds (recorded here, asserted nowhere)
backend\.venv\Scripts\python.exe -m tests.benchmarks.access_benchmark --sizes 10 100 400 --repeat 10
```

The benchmark truncates the `<database>_test` database on every run and rebuilds its estates
from empty. It never touches the development database.
