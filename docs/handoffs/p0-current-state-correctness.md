# P0 — Current-state reconciliation correctness

**Status:** implemented · **Date:** 2026-09-15 · **Starting SHA:** `7f3f214a1ebb03aff41c175ad3ef1323d2f558eb`

ADG answered two different things about the same reconciled estate. After an authoritative
scan proved a membership edge or an ACE gone, the point-in-time engine correctly stopped
counting it and the ordinary current-state engine went on counting it, so the same person and
the same folder read

```
as of the recollection instant:  no access
right now:                       modify
```

That is fixed. Current-state reads now go through one presence predicate; nothing is deleted;
history still reconstructs every removed fact over the interval it held.

---

## 1. Root cause

`principals`, `membership_edges`, `servers`, `smb_shares`, `smb_share_aces`,
`ntfs_resources` and `ntfs_aces` hold the latest state of every object a collector has ever
reported, and **nothing removes a row from them**. That is a deliberate, permanent property:
an absent observation is not evidence of removal, and the record of what was once true is the
product.

Removal is recorded in `object_versions` instead. When a successful authoritative
reconciliation finds an object gone, `HistoryWriter.close_absent` closes its open version with
reason `absent` and opens a **tombstone** — `is_present = false`, `valid_to IS NULL`. The
point-in-time readers consult that table, so they see the removal.

**No query-side read consulted it.** Every current-state repository selected straight from the
base tables, so a row a reconciliation had proved absent stayed in every live answer until
something happened to overwrite it. The defect was not one repository method or one endpoint;
it was the absence of a presence concept on the query side altogether, and it reached
effective access, raw ACLs, the membership graph, the access token, search, the operator
inventory, risk facts and the simulation baseline alike.

It was a **known** limitation: documented in `docs/release/known-limitations.md` §3 and pinned
by two tests that asserted the divergence rather than the correctness
(`test_simulation_equivalence.py::TestTheKnownExceptionIsMeasured`,
`test_history_queries.py::…::test_the_live_answer_still_counts_what_a_reconciled_scan_proved_is_gone`).

---

## 2. Repositories and services affected

Read-side modules that named the base tables directly — all of them now name the
current-state sources instead:

| Module | What it answers | Kinds routed |
| --- | --- | --- |
| `app/repositories/membership.py` | principals, direct membership, graph adjacency | principal, membership_edge |
| `app/repositories/resources.py` | servers, shares, directories, both raw ACLs, candidate sets | server, smb_share, smb_ace, ntfs_resource, ntfs_ace |
| `app/repositories/risk.py` | the risk engine's fact bundle | all six above plus membership |
| `app/repositories/search.py` | the global search box | principal, server, smb_share, ntfs_resource |
| `app/repositories/operations.py` | the operator inventory counts | all seven |

Everything downstream inherits the fix through those five, and was verified to:
`app/services/access.py` (effective access, resource → principals, principal → resources,
principal → shares, the access token, coverage findings), `app/services/graph.py`,
`app/services/resources.py`, `app/services/search.py`, `app/access_engine/*`,
`app/risk_engine/*`, `app/simulation/*` (its overlay repositories subclass the two fixed
ones), and every API route over them.

**Deliberately not changed:** `app/ingestion/*` and `app/history/writer.py` keep reading the
base tables. A writer that could not see every row could not extend a version, revive a
tombstone, or answer a contract 1.4 affirmation with `absent` rather than `unknown_object`.

---

## 3. The canonical current-state rule

Stated once, in `backend/app/models/current.py`:

> An object is **currently present** unless `object_versions` holds an **open tombstone** for
> it — a row with that `(object_kind, object_key)`, `valid_to IS NULL` and `is_present` false.

```sql
NOT EXISTS (
  SELECT 1 FROM object_versions
  WHERE object_kind = :kind
    AND object_key  = <the row's key column>
    AND valid_to IS NULL
    AND is_present = false
)
```

Written as an anti-join on tombstones rather than as "an open present version exists", for
three reasons:

* **absence must be measured** — only a successful, authoritative, in-scope reconciliation
  writes a tombstone, so nothing else can remove a row from current state;
* **a gap is not a deletion** — an object with no versions at all stays current; *nobody
  looked* and *somebody looked and it was gone* must not render alike;
* **reappearance needs no special case** — observing a tombstoned object closes the tombstone
  (`revived`), and the same predicate then admits it again.

Each `current_*` object is the base table with that predicate applied, carrying the base
table's own column names, so it substitutes for the table at a read site without changing the
query around it. Query-side code names these and never the base tables — which is the part
that makes the next accidental "select every stored row" hard to write.

`CURRENT_STATE_KEYS` (kind → table, key column) is now the single statement of where a kind
lives; `app.history.bindings.BINDINGS` builds itself from it, so the writer, the closure pass,
the point-in-time readers and the presence filter cannot drift apart.

Full write-up: [`docs/architecture/current-state-presence.md`](../architecture/current-state-presence.md).

### One thing that is not the base rule

`principal_references` is an append-only *candidate* index, not a collected object with a
timeline. `resources_named_by` and `shares_named_by` now confirm each reference against the
current ACL (`_still_named`), so a candidate cannot outlive the entry that justified it. That
settles removed entries and removed containers in one predicate: closing a resource closes its
entries too, so a removed directory names nobody.

---

## 4. Files changed

**New**

* `backend/app/models/current.py` — the rule, the seven current-state sources, `CURRENT_STATE_KEYS`
* `backend/tests/db/test_current_state_presence.py` — the regression matrix (35 tests)
* `database/migrations/versions/0016_current_state_presence.py` — the supporting index
* `docs/architecture/current-state-presence.md` — the semantics
* `docs/handoffs/p0-current-state-correctness.md` — this file

**Changed**

* `backend/app/models/schema.py` — declares `ix_object_versions_open_absent`
* `backend/app/history/bindings.py` — `BINDINGS` built from `CURRENT_STATE_KEYS`
* `backend/app/history/repository.py` — `absent_now` docstring ties it to the filter
* `backend/app/repositories/{membership,resources,risk,search,operations}.py` — routed through
  the current-state sources; `resources.py` additionally gains `_still_named`
* `backend/tests/db/test_simulation_equivalence.py` — `TestTheKnownExceptionIsMeasured` →
  `TestTheFormerExceptionIsGone`, assertions inverted
* `backend/tests/db/test_history_queries.py` — the pinned Phase 7A limitation, inverted
* `backend/tests/db/test_explanation_api.py` — `payroll_estate` stacks two transcripts; the
  second is now replayed without its reconciliation (see §8)
* `backend/tests/support/ingest.py` — `without_reconciliation`
* `backend/tests/support/equivalence.py` — the two readings' docstring
* `docs/release/known-limitations.md` — §3 rewritten as fixed, narrowed to what remains

---

## 5. Schema and migration

One index, `0016_current_state_presence`:

```sql
CREATE INDEX ix_object_versions_open_absent
    ON object_versions (object_kind, object_key)
    WHERE valid_to IS NULL AND is_present = false;
```

**Why an index and not a projection.** The existing history schema already carries everything
the rule needs, so the fix is a query change and no second source of truth was created. The
index is a pure accelerator: correctness lives in the predicate, so a deployment that has not
run the revision yet answers identically and pays a heap lookup per row for it.

**Why doubly partial.** `valid_to IS NULL` alone would index the open version of every object
in the estate — the whole store, duplicated, and `ux_object_versions_open` already covers that
set for the writer. Adding `is_present = false` makes the index the size of the *removals*, so
a presence check is a probe that normally misses in one page.

No column, constraint or row is touched. `downgrade` drops only what `upgrade` created.

---

## 6. Pre-fix regression result

The new suite was run against the pre-fix repository sources (the application modules
restored to `7f3f214a`, the test file and migration in place):

```
26 failed, 9 passed in 54.57s
```

The headline failure, verbatim:

```
tests/db/test_current_state_presence.py:281:
    assert reading.live == 0, (
E   AssertionError: and the current-state engine now sees it too: an authoritative
    reconciliation that did not report the edge is what makes the edge not current
E   assert 1245631 == 0
E    +  where 1245631 = ObservedAccess(subject_key='…-1104', resource_key='\\fs01\finance',
                        as_of=0, live=1245631, …).live
```

`0x1301BF` is Modify. The as-of reading was already `0`; the live reading was Modify, for the
same estate at the same instant.

The nine that passed pre-fix are exactly the right nine: the three "a partial / failed /
unreconciled scan removes nothing" cases, the replay-idempotence case, the
"a collector cannot remove what it cannot see" case, the affirmation case, the history
retention case, and the two mechanism checks. None of them is about removal, so none of them
could fail before the fix — which is itself the evidence that the suite is not simply
asserting the new behavior everywhere.

---

## 7. Post-fix regression matrix

`tests/db/test_current_state_presence.py` — **35 passed**. Every case is collected through the
ordinary ingestion API as the three collector runs a real deployment produces.

| Case | Assertion |
| --- | --- |
| membership removed | direct members, direct groups, adjacency, effective members, `keys_with_members`, `count_direct`, the **access token**, effective access — all exclude it |
| membership re-added | current again; timeline reads present → tombstone → present |
| SMB ACE removed | `full_share_acl`, `share_acl`, `count_acl`, `share_acls_for`, `shares_referencing`, `count_shares_referencing`, effective access |
| SMB ACE re-added | current again |
| NTFS ACE removed | `full_ntfs_acl`, `ntfs_acl`, `count_ntfs_acl`, `ntfs_acls_for`, `recompute_acl_hash`, effective access |
| NTFS ACE re-added | current again |
| share removed | `get_share`, `shares_by_keys`, `list_shares`, `count_shares`, `share_count` on the server, `shares_named_by`; the server itself survives |
| NTFS resource removed | `get_ntfs_resource`, `ntfs_resources_by_keys`, `get_share_root_resource`, `full_ntfs_acl`, `has_ntfs_aces`, `resources_named_by` |
| partial scan | removes nothing |
| failed scan | removes nothing |
| successful run reconciling no scope | removes nothing |
| authoritative scan of the same shape | **does** remove — the control for the three above |
| SMB run over the NTFS half | removes nothing: closure is per collector and per scope |
| replaying the same observation | idempotent, in the answer and in the ACL |
| contract 1.4 affirmation | resource and both entries survive the reconciliation the affirming run carries; `affirmed=1`, `refused=[]` |
| history | the row is still in `membership_edges`; the timeline has both versions; the as-of answer over the earlier interval is still Modify |
| current vs as-of-latest | equal, for both subjects, over all three removals, and on the local path |

Each access-producing case runs the full three beats — **Modify → 0 → Modify**.

Also inverted and passing: `test_simulation_equivalence.py::TestTheFormerExceptionIsGone`
(both readings now agree on a removal; a proposal naming a reconciled-away entry reports
`target_not_found` and is inert on a *current* baseline, matching the as-of one), and
`test_history_queries.py::…::test_the_live_answer_no_longer_counts_what_a_reconciled_scan_proved_is_gone`.

Risk (Phase 11) and simulation (Phase 12) are asserted at the source-fact level: the removed
broad ACE is not in `RiskFactsRepository.load()`'s bundle and the removed membership is not in
its `memberships`; the simulation baseline does not hold the reconciled-away entry. Neither
the finding lifecycle nor the simulation mathematics was touched.

---

## 8. Backend test results

```
6807 passed, 10 skipped, 1 xfailed, 73 warnings in 1670.65s   (backend-test.ps1 -Smoke)
5663 passed, 10 skipped, 1145 deselected in 77.17s            (backend-test.ps1)
```

The same run before this change was `6802 passed, 5 failed`; the five are accounted for
below. The one `xfail` is Phase 4C finding 3 — `resource -> principals` cost following the
estate — and it is still a strict xfail, untouched by this change.

Three suites needed changes beyond the new one, and each is worth naming:

1. **`test_simulation_equivalence.py::TestTheKnownExceptionIsMeasured`** and
   **`test_history_queries.py::…::test_the_live_answer_still_counts_…`** asserted the
   divergence. Both were written to fail the day it was fixed — they did, and both are now
   inverted rather than deleted, so the record of the limitation and of its ending sit
   together.

2. **`test_explanation_api.py::payroll_estate`** replayed two scenario transcripts into one
   estate to get both sides of a distinction. Both scenarios are `ntfs` runs reconciling
   `directory_tree \\fs01\finance`, so the second one authoritatively claims to have
   enumerated that tree and did not report Payroll — which now removes it, swapping the two
   verdicts the tests assert. **This is the fix working, not a regression:** two reconciling
   full enumerations of one scope are contradicting claims, not an additive merge. The second
   run is now replayed through `without_reconciliation`, which says the honest thing — it
   observed these objects and makes no claim about the rest of the scope.

No frontend source was changed, so no frontend check was needed.

---

## 9. Lint and type results

```
scripts/backend-lint.ps1
  ruff check            All checks passed!
  ruff format --check   364 files already formatted
  mypy app tests        Success: no issues found in 363 source files
  Backend checks passed.
```

Schema parity (`tests/db/test_schema.py`) passes: the declared
`ix_object_versions_open_absent` exists in the database the migration builds. The OpenAPI
snapshot is unchanged — no response contract moved, only which rows reach one.

---

## 10. Query and performance impact

**Statement counts are unchanged.** The predicate is inline in the existing statement, not a
second round trip, so nothing in `tests/db/test_query_cost.py` (statement counts per endpoint)
or `tests/db/test_access_performance.py` (counts equal across a 10× estate) moved.

**Plans**, measured with `EXPLAIN (ANALYZE, BUFFERS)` on a synthetic estate of 400 000 NTFS
ACEs, 200 000 edges, 20 000 share ACEs, 722 000 versions and 2 000 open tombstones:

| Shape | Plan added | Buffers added | Time (base → current) |
| --- | --- | --- | --- |
| one principal → one resource (whole DACL, 8 entries) | Nested Loop Anti Join over `ix_object_versions_open_absent`, index-only, Heap Fetches 0 | +16 | 0.087 → 0.073 ms |
| resource → principals (trustees on one path) | same | +16 | 0.041 → 0.047 ms |
| principal → shares (candidate page) | same | +8 | 0.075 → 0.061 ms |
| direct group membership (40 rows) | same | +80 | 0.249 → 0.127 ms |
| effective group membership (one 500-key level) | **Merge Right Anti Join** — the planner scans the whole tombstone index once (2 buffers) instead of probing per row | +488 | 3.779 → 2.906 ms |

Every indexed lookup keeps its shape: PostgreSQL pulls the `current_*` subqueries up into the
enclosing statement, so `Index Scan using ix_ntfs_aces_resource` is still what drives the read
and the anti-join hangs off it. The differences above are within run-to-run noise on a warm
cache; the point is the plan shape, and it is the one the index was added for.

The one new *predicate* is `_still_named` on the two candidate methods, which costs an index
probe of `ix_ntfs_aces_trustee` / `ix_smb_share_aces_trustee` — `(trustee_key, container)`,
exactly the pair being matched — per reference row on a page.

---

## 11. Remaining current-state caveats

* **Collection cadence, not state selection.** Between a change happening in Windows and the
  next *successful authoritative* scan of the scope it lies in, the live answer shows the old
  grant. A partial, failed, incremental, downgraded or unreconciled run removes nothing, by
  design. This is the honest answer — ADG reports what it was last told by somebody in a
  position to tell it — and it is what §3 of `known-limitations.md` now says.
* **`principal_aliases` is not filtered.** It is a record of every name ever observed for a
  SID, which is the point of it; it is metadata, never identity, and never an access fact.
* **`principal_references` is confirmed, not filtered.** The rows stay; each is checked
  against the current ACL where it is used as a candidate. A future reader of that table for
  some other purpose should know it is append-only.
* **An ACL with no current entries is still *unread*, not *empty*.** A share whose every entry
  was reconciled away grants an unknown amount rather than nothing, exactly as a share nobody
  has scanned does. That is existing effective-access semantics and was deliberately not
  touched; it is why the new suite's share-ACE cases remove one entry of two.
* **No live-domain validation.** This is a database state-selection fix with deterministic
  PostgreSQL coverage and needed no Active Directory, SMB server or NTFS volume. Real-domain
  validation remains the separate follow-up it already was (`known-limitations.md` §1).

---

## 12. Deferred, deliberately

Nothing in the P0's scope was left out. Two adjacent things were noticed and not done:

* **Retention interacting with presence.** `HistoryRetentionService` never prunes an open
  version, so a tombstone cannot be collected and the predicate cannot be undermined by it.
  Worth a test of its own; there is no defect to fix.
* **A `removed` view.** `VersionReader.absent_now` enumerates exactly the rows the filter
  excludes, and nothing surfaces it in the API. "What has been removed" is now a question with
  a precise answer and no endpoint; that is a feature, not this P0.
