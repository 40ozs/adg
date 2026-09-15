# ADG collection orchestrator

One scheduled task, six independent jobs, and a cadence that lives in a configuration file
rather than in the scheduler.

Full design: [`docs/architecture/incremental-collection.md`](../../../docs/architecture/incremental-collection.md).
This file is the operator's guide.

---

## Install

1. Copy [`adg-orchestrator.example.json`](adg-orchestrator.example.json) somewhere durable —
   `C:\ProgramData\ADG\adg-orchestrator.json` is the documented place — and edit it.
2. Make sure the collector configurations it names exist (the AD, SMB and NTFS collectors
   each have their own).
3. Validate it **before** anything is scheduled against it:

   ```powershell
   .\Invoke-AdgCollection.ps1 -ConfigPath C:\ProgramData\ADG\adg-orchestrator.json -ValidateOnly
   ```

4. See what it would do, without doing any of it:

   ```powershell
   .\Invoke-AdgCollection.ps1 -ConfigPath C:\ProgramData\ADG\adg-orchestrator.json -WhatIf
   ```

5. Print the scheduled task, review it, then create it:

   ```powershell
   .\Register-AdgCollectionTask.ps1 -ConfigPath C:\ProgramData\ADG\adg-orchestrator.json
   .\Register-AdgCollectionTask.ps1 -ConfigPath ... -UserName CORP\svc-adg-collector -Register
   ```

   Printing is the default. Registering a scheduled task is a change to a production machine,
   and this script does not make one merely because it was run.

The API credential is **never** in any of these files. It is read from the environment
variable the collector configurations name (default `ADG_COLLECTOR_KEY`), so it is not in a
command line, a scheduled-task argument list, or a shell history.

---

## Why one task and not six

The interval on the task is not how often anything is collected — it is how often the
orchestrator asks *what is due*. Each job's own interval decides the rest, so a short task
interval costs almost nothing: a run with nothing due exits in milliseconds.

Six tasks with six schedules would put the cadence in two places, the tasks and the
configuration, and the first symptom of the two drifting is a job that quietly stops running.

---

## The six jobs

| Job kind | Reads | Delta? | Reconciles? |
| --- | --- | --- | --- |
| `ad_principals` | users, groups, computers | yes, by `uSNChanged` | no — reads half the domain |
| `ad_memberships` | direct membership edges | yes, by `uSNChanged` | no — reads half the domain |
| `smb_inventory` | shares and share ACLs | no source metadata exists | yes |
| `ntfs_important_roots` | named trees, often | no | no — a subset, on purpose |
| `ntfs_deep_scan` | the whole tree | no, but affirms unchanged descriptors | yes |
| `full_reconciliation` | everything | no | yes — the repair pass |

`full_reconciliation` is the only job that can discover that something is **gone**, so its
interval is the upper bound on how long ADG can believe in access that no longer exists. A
delta reads what its source says has changed, and nothing announces a deletion to a query
that filters on change metadata: a deleted principal simply fails to appear, which is exactly
what an unchanged one does.

---

## Configuration

```jsonc
{
  "schema": "adg-orchestrator/1",
  "stateDirectory": "C:\\ProgramData\\ADG\\state",
  "collectorHost": "COLLECTOR01",
  "apiBaseUrl": "https://adg.corp.example.com",
  "jobs": [
    {
      "name": "ad-principals",          // becomes a file name and the server-side checkpoint key
      "kind": "ad_principals",
      "enabled": true,
      "every": "15m",                    // s | m | h | d. A bare number is refused.
      "strategy": "auto",                // auto | full | delta
      "collectorConfig": "C:\\ProgramData\\ADG\\adg-ad-collector.json",
      "maxAttempts": 3,
      "retryBaseSeconds": 30,
      "onlyBetween": { "start": "22:00", "end": "06:00" },   // optional; wraps midnight
      "reconcile": false,
      "roots": []                        // ntfs_important_roots only
    }
  ]
}
```

`every` refuses a bare number. The difference between reading "30" as seconds and as minutes
is a job that runs sixty times more often than its author intended, against a production
domain controller, and nothing about the result would look wrong.

`strategy: "auto"` resolves to a delta only when the kind's source publishes change metadata,
a checkpoint was saved by a run that **succeeded**, and that checkpoint's issuer matches the
directory server this run binds. Anything else is a full read — which costs one expensive
scan and loses nothing.

---

## State

Three files per job in `stateDirectory`:

```
<job>.state.json   the last run's outcome and the checkpoint it left
<job>.lock         held (as an open handle) while the job runs
```

plus, for an NTFS job with `digestIndexPath` set in its collector configuration, the digest
index of what was last reported per path.

The state file is written to a temporary file and renamed, so a process killed mid-write
leaves the previous document intact rather than a file that fails to parse. A state file that
cannot be read is treated as *no state* — the next run reads everything, which is exactly
right when the resume point cannot be trusted — and warns, because an installation silently
doing full scans for ever is a cost somebody should be told about.

**A checkpoint is written only by a run that succeeded.** A partial run did not read
everything below its watermark, so a later run starting there would skip exactly the objects
this one failed on, and the gap would never be noticed because nothing would look missing.

Copying a state directory between collector hosts is safe; the issuer check catches a
checkpoint that no longer applies. Copying an NTFS digest index between hosts is also safe —
it produces refused affirmations and full re-sends, never a wrong answer.

---

## Reading the output

```
ad-principals            succeeded  1,204 observation(s), after 1 attempt(s)
ad-memberships           succeeded  318 observation(s)
smb-inventory            skipped    last completed 2.1h ago, interval 6h
ntfs-important-roots     partial    8,441 observation(s), 2 error(s)
ntfs-deep-scan           skipped    outside the 22:00-06:00 window (local time is 14:05)
full-reconciliation      succeeded  reconciled, 3 object(s) marked absent
```

| Exit code | Meaning |
| --- | --- |
| 0 | everything that ran succeeded, or nothing was due |
| 1 | a job failed outright |
| 2 | a job finished `partial`: its observations are valid, its coverage is not |
| 3 | the configuration could not be loaded, so nothing ran |

`2` is deliberately not `1`. A partial run collected real data and reconciled nothing, which
is the system working correctly on an estate with an unreadable corner in it; alerting on it
as a failure teaches an operator to ignore the alert. `3` is deliberately not `1` either:
nothing ran at all, and an operator who cannot tell those apart goes looking for a broken
domain controller when the problem is a comma.

---

## When something looks wrong

**A job is always running as `full` when it should be a delta.** Run it with `-Verbose`. The
usual cause is that the collector could not establish the directory server's identity
(`dsServiceName` plus `invocationId`), so it will not resume from a watermark it cannot tie
to the incarnation that issued it. The run warns once, naming the job.

**`affirmations_refused` is high.** A non-zero count is normal — it is what a changed ACL
looks like. A count approaching the affirmation count means the collector's digest index is
stale rather than the estate being busy: a restored backup, or a state directory copied from
another host. It self-heals over one scan.

**A job's checkpoint is not advancing.** Check `collector_checkpoints.last_rejection_code` on
the server (`GET /api/v1/scan-runs/{run_id}` shows what a run claimed). A refused cursor
leaves the job resuming from the same place for ever, and every run after it looks
successful — which is why the reason is stored rather than only logged.

**Two jobs never run at the same time.** By design, per job: the lock is an open file handle,
so a process that dies releases it. A job that is already running is skipped, not queued.

---

## Least privilege

The orchestrator itself needs nothing beyond read access to its configuration and write
access to its state directory. It starts collectors, and every one of them is read-only
([ADR-0004](../../../docs/decisions/0004-read-only-collector-posture.md)). Registering a
scheduled task needs administrative rights **on the collector host** and none in the domain.

Running the task as SYSTEM is convenient and usually wrong: on a member server SYSTEM
authenticates to the rest of the domain as the *computer account*, so what the collector can
read becomes whatever that computer happens to have been granted. Use a dedicated,
least-privileged domain account — see [`docs/collectors/ad.md`](../../../docs/collectors/ad.md).
