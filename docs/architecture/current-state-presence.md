# Current-state presence

**Status:** implemented · **Code:** `backend/app/models/current.py`, `backend/app/history/`

ADG stores two things about every collected object: the **latest state** it was observed to
hold, in `principals`, `membership_edges`, `servers`, `smb_shares`, `smb_share_aces`,
`ntfs_resources` and `ntfs_aces`; and the **whole timeline** of the states it has held, in
`object_versions`. Nothing deletes a row from the first set. That is deliberate and
permanent — see [`history-model.md`](history-model.md) §1 — and it leaves one question that
has to be answered somewhere:

> Which of the stored rows describe the estate **as it is now**?

This document is the answer. It states the rule, what may and may not produce a removal, and
what stays out of the rule's reach.

---

## 1. Historical state and current state are different questions

**Historical state.** A version may remain stored long after it stops being true. That is the
point of storing it: *"Alice held Modify on `\\FS01\Finance` until the March scan"* is an
answer an auditor needs, and it is unavailable in a store that overwrites.

**Current state.** An observation is current only when the presence model says it is valid
at the latest authoritative state. Retained and current are different properties, and the
place they come apart is exactly where this document applies:

```
retained historically  ≠  currently present
```

Before this rule was implemented they were treated as the same thing by every query-side
read, and the product could answer *no access* as of the last scan and *modify* right now
about the same person and the same folder. The point-in-time answer was the correct one; the
live answer was the stored row outliving its own truth.

---

## 2. The rule

> An object is **currently present** unless `object_versions` holds an **open tombstone** for
> it — a row with that `(object_kind, object_key)`, `valid_to IS NULL` and `is_present`
> false.

In SQL, once, in `app/models/current.py`:

```sql
NOT EXISTS (
  SELECT 1 FROM object_versions
  WHERE object_kind = :kind
    AND object_key  = <the row's key column>
    AND valid_to IS NULL
    AND is_present = false
)
```

Three properties follow from writing it this way rather than as *"an open version exists and
it is present"*, and all three are the reason it is written this way.

**Absence must be measured.** Only `HistoryWriter.close_absent` writes a tombstone, and only
an authoritative, in-scope reconciliation reaches it. So the only thing that can take a row
out of current state is a successful run that claimed to have enumerated the scope the object
lies in, using a collector structurally capable of seeing it.

**A gap in the record is not a deletion.** An object with no versions at all stays current. A
row that predates the history model, or one that a fixture inserted directly, is *unobserved*,
not *absent*, and an audit tool that rendered the two alike would report access as revoked on
the strength of nobody having looked.

**Reappearance needs no special case.** Observing a tombstoned object again closes the
tombstone and opens a present version (the writer counts it as `revived`). There is then no
open tombstone, so the object is current again — by the same predicate, with nothing to
reset.

### Where the rule is applied

Each `current_*` object in `app/models/current.py` is the base table with that predicate
applied, carrying the base table's own column names, so it substitutes for the table at a
read site without changing the query around it. **Query-side code names these and never the
base tables.** The writers — `app/ingestion/`, `app/history/writer.py` — go on using the base
tables, because a writer that could not see every row could not extend a version, revive a
tombstone, or verify an affirmation.

| Kind | Table | Current-state source |
| --- | --- | --- |
| `principal` | `principals` | `current_principals` |
| `membership_edge` | `membership_edges` | `current_membership_edges` |
| `server` | `servers` | `current_servers` |
| `smb_share` | `smb_shares` | `current_smb_shares` |
| `smb_ace` | `smb_share_aces` | `current_smb_share_aces` |
| `ntfs_resource` | `ntfs_resources` | `current_ntfs_resources` |
| `ntfs_ace` | `ntfs_aces` | `current_ntfs_aces` |

`CURRENT_STATE_KEYS` — which table holds each kind and which column carries its `object_key`
— is stated once and `app.history.bindings.BINDINGS` builds itself from it, so the writer,
the closure pass, the point-in-time readers and this filter cannot come to disagree about
where a kind lives.

---

## 3. What may remove something from current state

Reconciliation is guarded in four independent places before a tombstone can be written, and
the presence rule inherits every one of them. None of this is new; it is restated here
because the rule is only as safe as these guards.

| Situation | Removes anything? | Why |
| --- | --- | --- |
| Successful run, scope reconciled, object not observed | **yes** | the collector claims it enumerated that scope completely |
| Successful run, no scope reconciled | no | the run makes no completeness claim |
| `partial` run | no | the contract refuses a reconciliation on a non-`succeeded` completion |
| `failed` / `canceled` run | no | same |
| Any run reporting `error_count > 0` | no | same — coverage was not achieved |
| `incremental` run | no | a delta query cannot report a deletion: a deleted object and an unchanged one both produce nothing |
| Run downgraded for missing batches | no | the ingestion service drops the reconciliation when it downgrades |
| Reconciled scope the run never declared | no | refused at completion |
| Object outside the reconciled scope key | no | `app/history/closure.py` selectors |
| Object of a kind that collector cannot see | no | `CLOSURE_RULES` is keyed on `(collector, scope kind)`; an SMB run cannot tombstone NTFS rows |

The last two are worth stating positively: an `smb` run reconciling `\\FS01` removes shares
and share ACEs of `FS01` and nothing else — not its directories, not its ACLs, not another
server's shares.

### Contract 1.4 affirmations

An affirmation (`ntfs_resource` only) says *your copy of this object is still correct*, and
the server verifies it against its own stored `acl_hash` before accepting it. An accepted
affirmation does exactly what a re-observation of the same state would: it extends the
version and records the object as seen by the run — **for the resource and for the entries
its digest covers**. That is what keeps the reconciliation the same run carries from marking
a whole DACL absent.

Contract 1.4 is unchanged by the presence rule, and the ingestion path that verifies an
affirmation deliberately still reads `ntfs_resources` directly: an affirmation naming an
object ADG has recorded as gone must be refused with `absent`, which is a different answer
from `unknown_object` and would be lost if that read were filtered.

---

## 4. What the rule does not cover

**`principal_aliases` and `principal_references` are not filtered.** Neither is a collected
object: no observation kind describes them, they have no timeline, and they are indexes over
what has been seen rather than statements about what is there. Where the reference index is
used as a *candidate* set — `resources_named_by`, `shares_named_by` — each row is confirmed
against the current ACL before it is offered, so a candidate cannot outlive the entry that
justified it.

**Effective-access semantics are unchanged.** This rule decides which rows are the engine's
input. What the engine then does with them — mask arithmetic, Deny ordering, the share/NTFS
cross, the coverage findings — is untouched. In particular an ACL with no current entries is
still *unread*, not *empty*: a share whose every entry was reconciled away grants an unknown
amount rather than nothing, for the same reason a share nobody has scanned does.

**History is untouched.** Point-in-time reads go through `object_versions` as they always
have, and every removed row is still there to be read: the version that held the state, its
interval, the run that closed it, and the tombstone that records the closing.

---

## 5. Cost

The predicate is one anti-join per row against `ix_object_versions_open_absent` — a partial
index over `(object_kind, object_key) WHERE valid_to IS NULL AND is_present = false`, so it
holds one entry per *removal* rather than one per object, and a current-state read is an
index probe that normally misses in a single page. PostgreSQL pulls the `current_*`
subqueries up into the enclosing statement, so an indexed lookup stays an indexed lookup and
the plans of the hot paths keep their shape.

Migration `0016_current_state_presence` adds the index. Correctness does not depend on it —
the predicate is in the query — so a deployment that has not run the revision yet answers the
same and pays a heap lookup per row for it.

---

## 6. The invariant, as a test

`tests/db/test_current_state_presence.py` holds the rule against a real estate, collected
through the ordinary ingestion API. The case worth knowing about is
`TestCurrentAgreesWithAsOfTheLatestState`: for a fully reconciled estate the ordinary
current-state answer and the point-in-time answer at the recollection instant are answers to
the same question and must be the same number. That is what would catch a *new* repository
reading a base table directly, long after the ones fixed here have been forgotten.
