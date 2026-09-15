# Incremental collection and reconciliation

**Status:** accepted (Phase 7B)
**Decisions:** [ADR-0025](../decisions/0025-incremental-collection-is-bounded-by-its-source.md),
[ADR-0026](../decisions/0026-an-affirmation-is-verified-and-a-checkpoint-trails-its-data.md)
**Contract:** [collector protocol](../contracts/collector-protocol.md) §11 (minor 1.4)
**Code:** `backend/app/domain/incremental.py`, `backend/app/ingestion/checkpoints.py`,
`collector/powershell/orchestrator/`

---

## 1. The problem, and the shape of its solution

Collection costs what the estate costs, not what its churn costs. Forty thousand principals
and a million directories are re-read on every scan and almost nothing in either has
changed, so a scan that runs often enough to be useful is a scan the estate cannot afford,
and a scan the estate can afford is a day behind.

The remedy is to do less. There are exactly three things that can be done less of, and they
are not interchangeable:

| | What it saves | Where it is safe |
| --- | --- | --- |
| **Read less** | Source I/O — the expensive part | Only where the source publishes change metadata that can be shown still to mean what it meant |
| **Send less** | Network, parse, upsert, history write | Wherever the reading still happened and can be proved to have happened |
| **Run less often** | Everything, proportionally | Wherever the answer's staleness is acceptable and *stated* |

ADG uses all three, in different places, because its three sources offer different things.
What it never does is conclude more from less. The one thing no amount of incremental
cleverness produces is **evidence of absence**, and §5 is about that.

---

## 2. What each source actually publishes

### Active Directory — `uSNChanged`, with two invalidating events

`uSNChanged` is a monotonically increasing counter, and a delta filtered on
`(uSNChanged>=N)` is exact and cheap. It is also **local to one domain controller**, and two
ordinary events make a saved watermark meaningless:

* **the collector binds a different DC.** The counters are unrelated. Resuming from DC1's
  number against DC2 skips every object whose USN on DC2 falls below it;
* **the DC is restored from backup.** Its counter rolls backwards and it reissues numbers it
  has already handed out. Its `dsServiceName` does not change. Its `invocationId` does.

So a watermark is stored with an **issuer** that is both facts joined:

```
CN=NTDS Settings,CN=DC01,...,DC=corp,DC=example,DC=com|2f0f9a3c-7c4e-4c0e-9a02-6b5f0a1f9d11
```

and a delta resumes only when the issuer of the saved cursor equals the issuer of the
directory this run bound. Anything else — a mismatch, an unreadable `invocationId`, a
watermark left by a run that did not succeed — resolves to a full read.

`whenChanged` is deliberately unused as a resume point. It is replicated, so it survives a DC
change, but it has one-second granularity, depends on the clocks of every DC that wrote it,
and is not monotonic across them. It is usable only with an overlap window whose correct
size nobody can state, and the failure mode of getting it wrong is a skipped object.

**What a delta cannot report is a deletion.** A deleted object moves to the Deleted Objects
container and returns no entry to the search. Nothing arrives — which is exactly what an
unchanged object does.

### SMB — nothing, and it does not matter

The SMB server publishes no change stamp of any kind. It also does not need one: a file
server has tens of shares, and reading all of them costs less than deciding which to skip.
Every SMB run is a full read.

### NTFS — nothing a walk can use, and this is the trap

**Writing a DACL does not move a directory's `LastWriteTime`.** It does not change its size
and it does not touch any attribute a walk can cheaply test. A scan that skipped
unchanged-*looking* directories on a timestamp would skip exactly the changes ADG exists to
find, and would produce no symptom at all.

The USN journal does record `USN_REASON_SECURITY_CHANGE`, and it is not available here: it is
per-volume, local to the file server, requires privilege ADG does not ask for
([ADR-0004](../decisions/0004-read-only-collector-posture.md)), and wraps — a journal that
wrapped between two scans is a silent gap.

So an NTFS scan **reads every descriptor, every time**. What it does less of is *sending*.

---

## 3. Affirmations: reading everything, sending what changed

An NTFS scan already computes an `acl_hash` for every descriptor it reads
([contract 1.2](../contracts/collector-protocol.md), [ADR-0008](../decisions/0008-acl-normal-form-and-hash.md)).
Contract **1.4** lets it send, for a descriptor whose freshly computed digest equals the one
it last reported for that path, an *affirmation* instead of the object:

```jsonc
{ "kind": "ntfs_resource",
  "source_key": "resource|\\\\fs01\\finance\\reports",
  "digest": "3b1f0c9d…",
  "observed_at": "2026-03-02T09:00:00Z" }
```

One line instead of a resource and forty entries.

**The server does not believe it.** It compares the digest against the `acl_hash` it holds
and refuses the affirmation when they differ, when it holds no digest, when it has never
seen the object, or when it has recorded the object as absent. Each refusal comes back in
the batch response naming the key, and the collector re-sends that object in full:

```jsonc
{ "affirmed": 812,
  "refused_affirmations": [
    { "source_key": "resource|\\\\fs01\\finance\\payroll",
      "reason": "digest_mismatch",
      "detail": "The affirmed digest 9f2c… is not the one ADG holds (3b1f…). The DACL changed between the two readings. Send the resource and its entries in full." } ] }
```

A refusal is not an error. `digest_mismatch` is what a changed ACL *looks like*, and on a
busy estate there will be some on every scan.

### The two invariants that make it safe

**The digest is computed from this scan's reading, never from a cache.** The collector-side
index (`collector/powershell/ntfs/functions/AdgNtfsDigestIndex.ps1`) decides only what to
*transmit*; `Get-AdgNtfsAffirmableDigest` takes the freshly computed digest as an argument,
so there is no code path by which an index entry alone becomes an affirmation.

**An affirmed object counts as observed — and so do the entries its digest covers.** This is
not an optimization. Reconciliation decides what to mark absent from the `observations`
table, so a resource affirmed without its ACEs would be a resource whose entire DACL the
next reconciliation tombstoned. `HistoryWriter.affirm_contained` confirms them with two
indexed statements, and `test_an_affirmed_directory_and_its_entries_survive_a_reconciliation`
is the test that fails if it ever stops.

Because affirmed objects are observed, **a run that affirmed everything it did not re-send
has still enumerated its whole scope**, and keeps the right to reconcile it.

### What the collector-side index costs

About 120 bytes per path — roughly 120 MB for a million directories, written as a flat,
streamed file rather than a JSON document. It is bounded by `digestIndexMaxEntries`; paths
beyond the bound are sent in full and a warning says so. It is discarded and rebuilt when
the scan fingerprint changes, because entries for paths this scan will not visit would
otherwise never be re-read, re-affirmed, or expired.

Every way the index can be wrong is expensive rather than incorrect: a stale entry produces
a refused affirmation and a re-send; a missing entry produces a full send.

---

## 4. Checkpoints

A checkpoint is the smallest piece of state in ADG and the one with the most dangerous
failure mode. Everything else, if it is wrong, is *visibly* wrong. A checkpoint that is wrong
acts on what the next run **does not read**, and a run that skipped an object reports
success, sends no error, and leaves nothing missing to notice.

```
kind    usn | timestamp | opaque       how the token may be compared
token   "184987"                       the cursor
issuer  "<dsServiceName>|<invocationId>"   what the cursor is local to
issued_at 2026-03-02T09:05:00Z
```

**A cursor advances only behind data that landed.** It is written inside the transaction
that wrote the batch, and again inside the completion transaction. Never speculatively. A
batch that never arrived cannot move the resume point past it.

**A cursor advances per batch, not only per run.** A delta that runs for an hour and dies at
minute fifty has had fifty minutes of batches accepted, and the objects they carried are in
the database. Re-reading them would be safe, and on a large estate "safe but wasteful" is a
scan that never finishes.

**A cursor advances only forwards, within one issuer.** A changed issuer, a changed kind, or
a backwards token is refused, and the refusal is **stored on the row**
(`collector_checkpoints.last_rejection_code`, cleared by the next accepted advance) rather
than only logged — because a job whose cursor cannot advance keeps running, keeps
succeeding, and keeps resuming from the same stale point, and nothing else about it looks
wrong.

**Two independent parties enforce "a failed run may not claim the source is current".**

| Who | What they know | What they refuse |
| --- | --- | --- |
| The collector | its own errors | writing a checkpoint after a run it did not finish cleanly |
| The contract model | the payload | a checkpoint on a completion whose status is not `succeeded` with no errors |
| The server | how many batches actually arrived | a checkpoint on a run it downgraded for short delivery |

The third is the one neither of the others can make. The collector believes it sent three
batches; only the server knows that two arrived.

A run the server downgraded still has its claimed cursor recorded against the *run*
(`scan_run_checkpoints`, role `result`), because what a collector claimed is evidence even
when the server will not act on it.

---

## 5. Reconciliation, and what drift means

A delta reads what its source says has changed. **Nothing announces a deletion to a query
that filters on change metadata.** A deleted principal, a removed share, a deleted directory:
in every case nothing arrives, which is what an unchanged object also does. No cadence of
delta runs distinguishes them.

So absence is discovered only by a **full reconciliation**, on its own schedule, and that
schedule is therefore the upper bound on how long ADG can believe in access that no longer
exists. Saying so is part of the design rather than a caveat about it.

**Drift is what a reconciliation had to correct, counted narrowly.** Only the presence
corrections:

```
drift = objects marked absent + objects revived
```

Ordinary state changes are deliberately *not* counted. An object whose ACL changed between
two reconciliations may well have been caught by a delta in between, or would have been on
its next pass; counting it here would make routine churn look like the cadence failing, and
an operator watching the number would learn nothing from it.

`delta_runs_since` is the denominator: three absences after fifty deltas and three after one
are different statements about the cadence. Both are stored on `scan_run_scopes` and returned
by the completion endpoint.

---

## 6. The six jobs

Each has its own schedule, its own state file, its own lock, its own checkpoint and its own
failure. One failing never stops the next; one already running is skipped rather than started
twice.

| Job | Source | Strategy | May reconcile | Why |
| --- | --- | --- | --- | --- |
| `ad_principals` | AD | delta by `uSNChanged` | **no** | Reads one half of the domain. It declares the domain scope — that is what it set out to look at — and marks itself incremental. |
| `ad_memberships` | AD | delta by `uSNChanged` | **no** | Same. A group's `uSNChanged` moves when its `member` attribute does, so membership churn is a delta. |
| `smb_inventory` | SMB | always full | yes | No change metadata exists and none is needed. |
| `ntfs_important_roots` | NTFS | scoped, frequent | **no** | A named subset, read on purpose. Reconciling a subset would mark every directory outside it deleted. |
| `ntfs_deep_scan` | NTFS | full walk, affirms | yes | Reads every descriptor; sends only what changed. Still a complete enumeration. |
| `full_reconciliation` | all | full | yes | The repair pass. The only job that can discover an absence. |

**Why a principals pass may not reconcile the domain** is worth stating plainly, because the
instinct is that a successful scan of the domain scope should be allowed to. It observed no
membership edges. The closure rules for a `domain` scope cover principals *and* edges, so
reconciling it would tombstone every edge in the estate.

---

## 7. Running it

One Windows scheduled task, on a short interval, calling one entry point:

```powershell
collector\powershell\orchestrator\Invoke-AdgCollection.ps1 -ConfigPath C:\ProgramData\ADG\adg-orchestrator.json
```

Each job decides for itself whether it is due, so a run with nothing due exits in
milliseconds. Six tasks with six schedules would put the cadence in two places — the tasks
and the configuration — and the first symptom of that drift is a job that quietly stops
running.

```powershell
# What would run, in what mode, and why. Changes nothing.
.\Invoke-AdgCollection.ps1 -ConfigPath ... -WhatIf

# Validate a new configuration before the task that runs it at 02:00 does.
.\Invoke-AdgCollection.ps1 -ConfigPath ... -ValidateOnly

# Print the scheduled task rather than creating one.
.\Register-AdgCollectionTask.ps1 -ConfigPath ...
```

Exit codes: `0` clean, `1` a job failed, `2` a job finished partial, `3` the configuration
could not be loaded so nothing ran. `2` is separate from `1` on purpose — a partial run
collected real data and reconciled nothing, which is the system working correctly on an
estate with an unreadable corner in it, and alerting on it as a failure teaches an operator
to ignore the alert.

### Retry

Only an **outright failure** is retried, with exponential backoff and jitter. A `partial`
run is a real result: retrying it means reading the whole scope again to reach the same
directory that denied access the first time. A failure a retry cannot fix — a rejected
payload, a missing configuration, a refused reconciliation — is not retried either, because
retrying it three times replaces a clear error at 02:00 with the same error at 02:07 and
again at 02:21.

A retried job is a **new run** with a new `run_id`. That is safe: ingestion is keyed on
`(run_id, source_key)` and every write is newest-wins, so a job that half-completed and was
retried converges. What a retry never inherits is the failed attempt's checkpoint.

---

## 8. Telemetry

| Signal | Where |
| --- | --- |
| changed objects | `scan_runs.observation_count_applied` |
| unchanged / skipped objects | `scan_runs.affirmation_count_applied` |
| affirmations the server refused | `scan_runs.affirmations_refused`, and per key in the batch response |
| full vs delta vs reconcile | `scan_runs.mode`, and `job` |
| the checkpoint | `collector_checkpoints` (per job), `scan_run_checkpoints` (per run, `baseline` and `result`) |
| a job whose cursor is stuck | `collector_checkpoints.last_rejection_code` / `last_rejection_message` |
| reconciliation drift | `scan_run_scopes.closed_absent`, `revived`, `delta_runs_since`; returned by `POST /completion` |

How to read `affirmations_refused`: a non-zero value is normal — it is what a changed ACL
looks like. A value approaching the affirmation count means the collector's index is stale
(a restored backup, a copied state directory) rather than the estate being busy.

---

## 9. What this phase deliberately did not change

* **No HTTP surface for the schedule.** The orchestrator is collector-side and its state is
  on the collector host. The server records what runs told it and nothing more.
* **No new capability and no new endpoint.** The contract gained fields; the API gained no
  route, so the capability boundary is untouched.
* **No change to what a reconciliation may close.** The Phase 7A closure rules are unchanged;
  what 7B changed is how often a run is *entitled* to invoke them.
* **No frontend.** The drift counters, the affirmation counts and the checkpoint state are
  all reachable from the API and none of them is on a screen.
