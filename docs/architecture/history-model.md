# The history model

**Phase:** 7A · **Status:** implemented · **Code:** `backend/app/history/`

Phases 0 through 6 stored the *latest* state of every collected object together with the
provenance of the observation that produced it. That answers two questions well — *what is
true now* and *who said so* — and cannot answer two others at all:

* what was true on the 3rd?
* when did this stop being true?

Both are unanswerable in that schema for the same reason: a newer observation overwrites the
older one in place, and the older one is gone.

This document describes the model that answers them, and — more importantly — what it
refuses to claim.

---

## 1. A version is a state and the interval it was observed over

`object_versions` holds **one row per state an object was observed to hold**. The identity of
a row is `(object_kind, object_key, valid_from)`, and at most one version of an object is
open at a time; both are enforced by the database.

| Column | Meaning |
| --- | --- |
| `valid_from` | when this state was **first observed** |
| `last_seen_at` | the newest observation that **confirmed** it |
| `valid_to` | the observation that **contradicted** it; `NULL` while it still holds |
| `is_present` | `false` is a tombstone: a reconciled scan looked and did not find it |
| `state` | the descriptive columns of the current-state row, as JSONB |
| `state_hash` | the digest that decides whether the next observation is a change |
| `origin` | `observed`, or `backfilled` by the Phase 7 migration |
| `close_reason` | `superseded` (it changed) or `absent` (it was gone) |

Three timestamps rather than two, and the third is the point of the whole model.

Every one of the three is attributable. `opened_by_run_id`, `last_seen_run_id` and
`closed_by_run_id` name rows of `scan_runs` — the same runs `observations` records, and the
same runs the Collectors page reports coverage for. So *"when did this change, and which scan
saw it"* is answerable without a join through the observation log, and an ending nobody is
accountable for is refused by a check constraint (§8).

`observations` is not replaced by this table and does not become redundant. It records **which
run saw which object, in which batch** — one row per observation, whether or not anything
changed — and that is what makes a replayed batch a no-op and what the coverage verdict is
built from. A version records only the runs that opened and last confirmed a state. The two
answer different questions and neither derives the other.

### Why `last_seen_at` exists

**A collector samples; it does not watch.** If Monday's scan saw an ACL and Friday's saw a
different one, the change happened somewhere in between and ADG does not know where. A model
with only `valid_from` and `valid_to` has nowhere to put that ignorance, so it invents a
precision it does not have: it says the change happened on Friday, which is merely the day
somebody looked.

Recording both ends makes the honest statement available:

> the state ended somewhere in `(last_seen_at, valid_to]`

That interval is `ObjectVersion.change_window`, and the same relation bounds the *start* of
the next version — `ObjectTimeline.opened_window` reads the predecessor's `last_seen_at`. It
is also why retention never removes the newest closed version of a live object (§6).

### Certainty

Every point-in-time answer reports one of four values, derived from the version and the
instant asked about:

| Certainty | When |
| --- | --- |
| `observed` | `valid_from <= T <= last_seen_at` — the state was confirmed either side of *T* |
| `inferred` | *T* is later than the last confirmation: the best answer, not a watched one |
| `backfilled` | the version was reconstructed by the migration (§5) |
| `unobserved` | no version covers *T*: nobody had looked, or coverage has a gap |

`unobserved` is never rendered as "it did not exist". That distinction is the product.

---

## 2. Absence is a state, not a missing row

A tombstone is a real version with a real interval. It has to be, because

* *ADG knows this share was gone on Tuesday*, and
* *ADG has nothing about this share on Tuesday*

are different answers, and an audit tool that renders them the same way is lying about one of
them. A **gap** between versions means nobody looked; a **tombstone** means somebody looked
and it was not there.

Nothing is ever deleted from the current-state tables. A closure closes the present version
with reason `absent` and opens a tombstone; `smb_shares` still holds the row.

---

## 3. Who may record an absence

Only a run that **reconciled a scope**, and even then only inside that scope. Four
independent guards, each of which alone would prevent the failure it is aimed at:

1. **The completion model** refuses to build a completion that reconciles while reporting a
   non-`succeeded` status or any error at all (`ScanRunCompletion`).
2. **The ingestion service** refuses a scope the run never declared, refuses every scope on
   an `incremental` run, and drops the reconciliation entirely when it downgrades a run
   because fewer batches arrived than were sent.
3. **The closure rules** (`app/history/closure.py`) restrict what a reconciliation may close
   to the object kinds that collector actually reports. A `server` scope reconciled by the
   SMB collector cannot close NTFS rows: the SMB collector never opens a directory, so it
   cannot be evidence that one is gone. A pair with no rule closes **nothing**.
4. **The writer** will not close a version that was confirmed *after* the reconciling run
   completed. A stale run may not declare absent what a newer one just found.

### The rule table

| Collector | Scope | May close |
| --- | --- | --- |
| `active_directory` | `domain` | domain principals, and the edges of those principals |
| `local_groups` | `local_groups_host` | host-scoped principals and edges on that host |
| `smb` | `server` | that server, its shares, and those shares' ACEs |
| `smb` | `share` | that share and its ACEs |
| `ntfs` | `server` / `share` / `directory_tree` | resources in scope, and their ACEs |

Two shapes are worth naming:

* **ACL entries are selected through their parent.** An ACE is not independently enumerable —
  a collector reads a *descriptor* and the entries come with it — so the rule for an ACE kind
  is "every ACE of the resources this scope selected". That cannot select an ACE whose
  resource is out of scope, and cannot miss one whose key happens not to look like the
  scope's.
* **The `domain` scope resolves its own key space.** Every other scope key is a value stored
  on the rows it selects; a `domain` scope key is a DNS name, which no row stores. Rather
  than deriving a domain SID by string surgery on `distinguishedName`, the closure resolves
  the key space from **the run's own observations** — the distinct `domain_sid` values of the
  principals it reported. A run that reported no principal with a domain SID resolves an
  empty key space and closes nothing.

Local groups and domain groups are never confused: `S-1-5-32-544` means a different group on
every machine, so a domain scope closes only rows with `host_key IS NULL` and a
`local_groups_host` scope closes only rows with `host_key IS NOT NULL`.

---

## 4. What decides that something changed

The digest of the state, and nothing else.

`last_observed_at` moves on every scan, `source_key` carries the run's own identity, and
`updated_at` moves whenever anything is written. All three are excluded from the digest
(`PROVENANCE_FIELDS`), so re-reading an unchanged ACL a thousand times extends one version a
thousand times and creates no second version. **A history that recorded a version per scan
would be a scan log wearing a history's clothes**, and the question it exists to answer —
*when did this change?* — would be unanswerable in it.

`source_key` is *stored* but not digested: a record rebuilt from a version has to be able to
say which observation produced it, and the value kept is the one from the run that **opened**
the version, which is the observation that matters when the question is when the state began.

### Observations that arrive out of order

Runs overlap, and a delayed run can land after a newer one. History follows the same rule the
current-state upsert already follows — an older reading may not overwrite a newer one:

* an observation **newer than** the open version's last confirmation may open, extend or
  close it; this is the ordinary path;
* an observation **older than** the version's beginning carrying the *same* state pulls
  `valid_from` back, which is genuinely new knowledge and adds no contradiction;
* an observation older than the last confirmation carrying a *different* state is **not
  applied**, and is counted as `stale` rather than silently dropped;
* an observation contradicting an open version at the very instant it began **overwrites** it:
  intervals are half-open, so `[t, t)` covers nothing, and the later-arriving reading is the
  one the current-state upsert also keeps.

---

## 5. The backfill claims exactly what the old schema can support

Migration `0007_history_model` gives every existing row **one** version, spanning
`first_observed_at` to `last_observed_at`, marked `origin = 'backfilled'`. That is the most
the previous schema can justify: it kept two timestamps and one state, so an object that
changed twice between those instants left one row then and produces one version now.

Those versions are therefore not the same claim as an observed one, and every answer drawn
from one reports `Certainty.BACKFILLED`. **Marking them and reporting it is the whole
difference between reconstructing history and inventing it.**

The migration duplicates the canonicalization rather than importing it, because a migration
must keep doing what it did on the day it ran even after the application moves on;
`tests/db/test_history_migration.py` re-derives every backfilled digest with the
application's own function and asserts they agree, so the duplication cannot drift unnoticed.

---

## 6. Retention

Two switches, and the destructive one defaults to off:
`ADG_HISTORY_RETENTION_DAYS` says how long, `ADG_HISTORY_RETENTION_ENABLED` says whether. One
switch would mean a deployment that set a number while thinking about disk capacity had also
authorized deletion.

Three things are never candidates, whatever the policy says:

* **an open version** — it is current state;
* **the newest closed version of an object** — it is what bounds the open version's
  beginning (§1), and removing it makes "since when has this group had these members"
  unanswerable about a group that still exists;
* **anything closed more recently than the cutoff.**

`plan()` counts and writes nothing. `apply()` is the only destructive method in the history
layer, refuses outright while the policy is disabled rather than doing nothing quietly, and
reports what it removed. Nothing in the API or the collectors calls it; it is an operator
action.

---

## 7. Point-in-time queries

`HistoryService` answers four questions as of an instant: membership in either direction, the
raw SMB and NTFS ACLs, resource existence, and effective access.

**Effective access is computed by the ordinary engine.** There is no historical access
algorithm. `HistoricalMembershipRepository` and `HistoricalResourceRepository` are the live
repositories with their reads redirected through the temporal predicate, and
`AccessService` takes both by injection — so the DACL projection for a path nobody read, the
deny-before-allow ordering and the coverage findings for a group nobody enumerated all apply
unchanged. A second engine for history would be a second implementation of the most
safety-critical code in the product, and the first time the two disagreed nobody would know
which was right.

What the engine cannot know is that its inputs were historical, so that is carried beside it:
`VersionAudit` counts every version the answer read by the certainty it has at that instant,
and the answer's certainty is the weakest of them.

Only the reads a resolution and a traversal make are overridden. An inherited read returns
current state, which is a real hazard, so `tests/history/test_repository_coverage.py` names
every public read of each base class that the as-of form deliberately does not provide; a
method added to a base repository fails the suite until somebody decides which of the two it
is.

---

## 8. The temporal invariants

Each is a check constraint on `object_versions` *and* a check in
`ObjectVersion.validate()`. The constraint makes the violation impossible; the check says, in
a sentence, what it would have broken.

| Invariant | What violating it would break |
| --- | --- |
| `last_seen_at >= valid_from` | the confirming observation is what opens a version, so it cannot predate it |
| `valid_to IS NULL OR valid_to >= last_seen_at` | closing before the newest confirmation claims a state ended while something was still observing it |
| `(valid_to IS NULL) = (close_reason IS NULL)` | `superseded` and `absent` are different findings; an ending with no reason cannot be read |
| `(valid_to IS NULL) = (closed_by_run_id IS NULL)` | an ending nobody is accountable for cannot be audited |
| `is_present = (state IS NOT NULL)` | a tombstone carries no state, and a present object always carries one |
| `is_present = (state_hash IS NOT NULL)` | a present version with no digest would re-open on every scan |
| `state_hash = digest(state)` | the two are read by different queries — one compares, one renders — and a disagreement makes them answer differently |
| at most one open version per object | two would make "what is true now" return two contradictory rows |
| versions of one object do not overlap | "what was true then" would return two contradictory answers |
| every instant is timezone-aware, in UTC | a naive timestamp cannot be ordered against a collector in another time zone |

---

## 9. What this phase deliberately did not change

**Current-state reads are untouched.** Every Phase 0–6 query, index and plan is unchanged, so
"current-state queries still work" holds by construction rather than by re-testing twenty
repositories.

The consequence is stated rather than hidden, because it is a real overstatement of access:
**a live answer still counts an entry that a reconciled scan proved is gone.** Nothing deletes
a collected fact, so `membership_edges` keeps an edge that was removed and `ntfs_aces` keeps
a superseded entry — an ACE's identity includes its mask, so tightening a DACL *adds* a row
rather than changing one. Phase 7A records both removals in the timeline and does not apply
them to the current-state read path; the **as-of-now** answer is the correct one today.

Routing current-state reads through the open version's presence is the top prerequisite for
the next phase, and the divergence is pinned by
`tests/db/test_history_queries.py::TestPointInTimeEffectiveAccess::test_the_live_answer_still_counts_what_a_reconciled_scan_proved_is_gone`,
so the day it is fixed that test fails and says what changed.

No HTTP surface was added. The point-in-time services are a domain and service layer, in the
rhythm this project has followed since Phase 5 (engine, then API).

---

## See also

* ADR-0018 — history is a versioned observation log; current state stays a projection
* ADR-0019 — a change is recorded as a window, not an instant
* ADR-0003 — raw observations versus derived state
* ADR-0011 — answers carry their uncertainty
* `docs/contracts/collector-protocol.md` — scopes and reconciliation
