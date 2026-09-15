# ADR-0025: Incremental collection is bounded by what its source can prove, and absence is never one of those things

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 7B (`phase-07/02-incremental-collection.md`)
- **Deciders:** Phase 7B implementation

## Context

Recurring collection is expensive in proportion to the estate, not to the churn. A domain
with forty thousand principals and a file server with a million directories are re-read in
full on every scan, and almost nothing in either has changed. The obvious remedy is to read
only what changed — and the obvious remedy is where audit tools go wrong, because the three
sources ADG reads offer three very different things when asked *what changed*, and only one
of them offers anything at all.

**Active Directory** publishes `uSNChanged`, a monotonic counter, and `whenChanged`, a
replicated timestamp. `uSNChanged` is usable and `whenChanged` mostly is not: it has
one-second granularity, it depends on the clocks of every DC that wrote it, and it is not
monotonic across them. But `uSNChanged` is a counter on **one domain controller**. Two
things invalidate a saved watermark, and neither is exotic:

* the collector binds a different DC — the numbers are unrelated, and resuming from DC1's
  watermark on DC2 skips every object whose USN on DC2 happens to fall below it;
* the same DC is **restored from backup** — its USN counter rolls backwards and it reissues
  numbers it has already handed out. Its `dsServiceName` is unchanged. Its `invocationId` is
  not.

**SMB** publishes nothing. `Get-SmbShare` has no change stamp of any kind. It also does not
matter: a file server has tens of shares.

**NTFS** publishes nothing a walk can use, and this is the one that catches people. Writing
a DACL does **not** move a directory's `LastWriteTime`, does not change its size, and does
not touch any attribute a walk can cheaply test. A scan that skipped unchanged-*looking*
directories on a timestamp would skip exactly the changes ADG exists to find. The USN
journal does record `USN_REASON_SECURITY_CHANGE`, but it is per-volume, local, privileged,
and finite — none of which describes a collector reading a remote share.

Across all three, one thing is missing everywhere: **nothing announces a deletion to a query
that filters on change metadata.** A deleted AD principal moves to the Deleted Objects
container and returns no entry to a `uSNChanged` search. A removed share returns no row. A
deleted directory returns nothing. In every case *nothing arrives* — which is precisely what
an unchanged object also does.

## Decision

**Incremental collection narrows what is read only where the source publishes change
metadata that can be shown still to mean what it meant, and never narrows what may be
concluded.**

Concretely:

1. **Only Active Directory runs as a delta.** A delta is filtered on
   `(uSNChanged>=<watermark>)` and resumes only when the watermark's **issuer** matches the
   directory this run bound. The issuer is `dsServiceName` and `invocationId` joined; both
   are required and neither is sufficient. A mismatch, an unreadable `invocationId`, or a
   watermark left by a run that did not succeed all resolve to a full read.

2. **`whenChanged` is not used as a resume point.** The contract carries a `timestamp`
   checkpoint kind for a source that has nothing better, and the AD collector does not emit
   one.

3. **A delta run may never reconcile.** It is `incremental: true` in the start envelope,
   which the server already refuses to let reconcile anything. This is not a policy choice
   that could be relaxed: a delta cannot distinguish *deleted* from *unchanged*, so any
   absence it inferred would be invented.

4. **Absence is discovered only by a full reconciliation**, on its own schedule. Its
   interval is therefore the upper bound on how long ADG can believe in access that no
   longer exists, and that is stated to the operator rather than left implicit.

5. **A file-system scan reduces cost by what it transmits, never by what it reads.** Every
   descriptor is read on every scan and its digest computed fresh; only the unchanged ones
   are sent as affirmations (ADR-0021). The scan therefore remains a complete enumeration
   and keeps the right to reconcile.

6. **A job that reads part of its scope is incremental, whatever else it is.** An AD
   principals pass, an AD memberships pass, and a scan of named important roots each read a
   subset on purpose, so none of them may reconcile — a principals pass that reconciled the
   domain scope would mark every membership edge absent, having observed none.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Skip NTFS directories whose `LastWriteTime` is unchanged | Writing an ACL does not move `LastWriteTime`. This would skip the exact change the product exists to detect, and produce no symptom. |
| Read the NTFS USN journal for `USN_REASON_SECURITY_CHANGE` | Per-volume, local to the file server, requires privilege ADG does not ask for (ADR-0004), and wraps. A journal that wrapped between scans is a silent gap. |
| Use `whenChanged` as the AD watermark | One-second granularity and clock-dependent across DCs. Usable only with an overlap window whose correct size nobody can state, and the failure is a skipped object. |
| Key the watermark on the DC's host name | Survives a restore from backup, which is when the counter is reissuing numbers. The invocation id is the only thing that changes then. |
| Let a delta reconcile the objects it *did* see | Reconciliation is scope-wide by construction: it acts on what a run did **not** observe. There is no such thing as reconciling part of a scope. |
| Infer deletion from an object missing across N consecutive deltas | N deltas that never queried the object are N pieces of no evidence. It would mark absent whatever fell below a stale watermark — the failure this ADR exists to prevent, on a timer. |

## Consequences

**Positive**

- An AD delta reads the objects that changed instead of the whole directory, so the
  membership graph can be refreshed every fifteen minutes rather than nightly.
- Every narrowing is reversible and fails safe: the *worst* outcome of any refusal here is a
  full scan.
- The one thing a delta cannot see — absence — is named, scheduled, measured as
  reconciliation drift, and reported.

**Negative / accepted costs**

- ADG can be wrong about an object's continued existence for as long as the reconciliation
  interval, and the drift counter says by how much after the fact rather than at the time.
- Binding a different domain controller costs one full read of the directory.
- A restore from backup costs one full read, and is detected rather than configured.
- An NTFS scan still performs every descriptor read it always did. The saving is on the
  wire, in the parse, and in the database — not in the file server's I/O.

**Follow-up required**

- Surface the reconciliation drift counters on the Collectors page; they are stored on
  `scan_run_scopes` and returned by the completion endpoint, and no screen reads them yet.

## Compliance

- `backend/tests/incremental/test_modes_and_checkpoints.py` holds the checkpoint rules,
  including the restore-from-backup case, and asserts that only `delta` is forbidden to
  reconcile.
- `backend/tests/db/test_incremental_collection.py::TestFullReconciliationRepairsWhatTheDeltasCouldNotSee`
  runs two deltas over a deleted principal, asserts neither marks it absent, and asserts the
  reconciliation does.
- `collector/powershell/orchestrator/tests/AdgOrchestrator.Schedule.Tests.ps1` asserts that a
  changed issuer, a restored DC, and a previous partial run each resolve to a full read.
- `ck_scan_runs_mode_matches_incremental` on `scan_runs` makes a row that claims to be a
  delta while remaining eligible to reconcile impossible to store.
