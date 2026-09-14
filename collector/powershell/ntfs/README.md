# ADG NTFS directory-tree collector

Walks the configured directory trees, reads the NTFS security descriptor of everything it
reaches, and reports it as [contract v1](../../../docs/contracts/collector-protocol.md)
observations — along with the answer a tree scan exists to produce: **did permissions change
here, or is this folder carrying what its parent handed down?**

It reports readings, never conclusions. Nothing here computes effective access, expands
group membership, applies Deny precedence, expands generic rights, or drops mask bits it does
not recognize — an ACE is evidence, and a simplified ACE is no longer evidence. Those
computations are the backend's, from these facts together with the share layer.

It *does* resolve one narrow piece of the inheritance algebra, because a tree scan cannot
work without it: which entries a parent hands down, carrying which flags. That is what the
boundary verdict is computed from, and it is not effective access —
see [ACL boundaries](../../../docs/architecture/ntfs-acl-boundaries.md).

**The two layers are separate on purpose.** Remote access over SMB is limited by **both** the
share ACL and the NTFS ACL; access at the console, or from a service running on the box,
is limited only by the NTFS one. ADG keeps them as separate observations, separate tables,
and separate API routes, so an auditor can see which layer is doing the restricting.

**ADG is read-only.** Nothing in this collector writes to a target, enables a privilege,
takes ownership, or modifies a security descriptor to make a read succeed
([ADR-0004](../../../docs/decisions/0004-read-only-collector-posture.md)). A directory whose
descriptor cannot be read is reported as an error and leaves the run partial — which is the
honest outcome, and the one that stops an unread ACL from being mistaken for an empty one.

---

## Running it

```powershell
# See what it would send, without touching a share
.\Invoke-AdgNtfsScan.ps1 -ScanRoot \\FS01\Finance -DryRun -OutputDirectory C:\code\adg\.tmp\ntfs

# Collect and submit
.\Invoke-AdgNtfsScan.ps1 -ConfigPath .\adg-ntfs-targets.json -ApiBaseUrl http://localhost:8000 -RunPerScanRoot

# A large tree, bounded and resumable
.\Invoke-AdgNtfsScan.ps1 -ApiBaseUrl http://localhost:8000 -ScanRoot \\FS01\Finance `
    -MaxDepth 8 -ExcludePath \\FS01\Finance\Archive -ConcurrencyLimit 8 `
    -CheckpointPath C:\ProgramData\ADG\finance.checkpoint.json

# ...and continuing it after an interruption
.\Invoke-AdgNtfsScan.ps1 -ApiBaseUrl http://localhost:8000 -ScanRoot \\FS01\Finance `
    -MaxDepth 8 -ExcludePath \\FS01\Finance\Archive -ConcurrencyLimit 8 `
    -CheckpointPath C:\ProgramData\ADG\finance.checkpoint.json -Resume
```

`-ShareRoot` and the configuration file's `shareRoots` are still accepted as spellings of
`-ScanRoot` / `scanRoots`, so a Phase 3A deployment keeps working unchanged.

Copy [`adg-ntfs-targets.example.json`](adg-ntfs-targets.example.json) to
`adg-ntfs-targets.json` and edit it. The example documents every option inline.

Targets are always explicit. There is no estate-wide sweep, and that is a posture decision
rather than a missing feature — a tool that walks shares it was never pointed at looks
exactly like reconnaissance, and it makes a run's declared scope mean something vague.
Generate the list from ADG's own share inventory (`GET /api/v1/servers/{server}/shares`,
collected by the SMB collector) rather than by probing.

**Use `-RunPerScanRoot` for anything larger than a handful of trees.** One unreadable
descriptor downgrades a combined run to `partial`, an operator reading that status cannot
tell which tree failed, and reconciliation becomes all-or-nothing instead of per-tree.

---

## What this phase reads, and what it does not

**Any directory inside a share.** A scan root may be `\\FS01\Finance` or
`\\FS01\Finance\Reports`; Phase 3A refused the second, and the walk lifts that because it
reads the ancestors. Starting below a share root is cheaper and it costs something, stated
rather than hidden: the starting directory's parent is not read, so its boundary is reported
as `scan_root` — unknown, and therefore a boundary — and the run cannot reconcile the share's
tree. One root may not sit inside another; both runs would read the same subtree and describe
it two different ways.

**Files are opt-in.** `includeFiles` reads the DACL of every file as well. It is off by
default because an estate holds orders of magnitude more files than directories, and the cost
is the operator's to accept deliberately rather than the collector's to impose. A file is
never traversed and is compared against the *object* projection of its directory rather than
the container one.

**Reconciliation is now possible, and still rare.** The contract's file-system scope kind,
`directory_tree`, claims the whole tree beneath a path was enumerated — so a walk that really
did enumerate one may claim it, which is the first time any ADG file-system run has been able
to. It is claimed only when **nothing at all** was skipped beneath that root: no depth limit
reached, no exclude pattern applied, no junction left unfollowed, no descriptor denied, no
directory left unlistable, no timeout, no resume. Anything less and the root is left out of
`reconciled_scopes`, because reconciling is what lets the backend mark objects absent — and a
scan that stopped early and claimed completeness would report live permissions as revoked.

Absence of a directory from ADG therefore still means *nobody has looked*, unless a run
reconciled the tree it sits in.

---

## Finding the boundaries

The verdict on every resource is `is_acl_boundary` plus a `boundary_reason` that says why.
The comparison is **not** child-digest against parent-digest: Windows sets the `INHERITED` bit
on every entry it copies down, so a perfectly inheriting child has a different digest from its
parent, and that comparison would report every directory in the estate as a boundary. A child
is compared against the DACL its parent *projects* onto a child of its kind.

| `boundary_reason` | Means |
| --- | --- |
| *(absent)* | Carrying exactly what it inherited. The only value that is not a boundary |
| `acl_differs_from_parent` | The comparison was made and they differ. The ordinary finding |
| `protected_dacl` | `SE_DACL_PROTECTED` — refuses inherited entries, so a boundary whatever the projection says |
| `null_dacl` | A NULL DACL, which cannot be anything that was inherited |
| `share_root` | The directory a share publishes; its parent is outside the share |
| `scan_root` | The walk started here; the parent exists and was not read |
| `parent_unreadable` | The parent's DACL, or this one, was not read in full |
| `parent_null_dacl` | The parent has a NULL DACL, which projects nothing |

**The last four mean nobody knows, and all four report a boundary.** A boundary that is not
really there costs one extra stored ACL; a boundary reported `false` tells the next scan it
may stop looking and silently drops every permission change beneath it.

One rule is worth knowing before reading a report, because it is the one that surprises:
**an ACE carrying a generic right propagates as two entries, not one.** `0xe0010000` — the
generic form of Modify — sits on almost every directory Explorer creates, and Windows splits
it into an effective copy (mapped, applies here) and a propagating copy (unmapped,
inherit-only). The collector models that; a tool that does not reports every such directory
as a boundary.

The one case it still gets wrong is `CREATOR OWNER`. Windows adds an ACE naming whoever
created the child, which is not a fact about the parent, so a directory beneath such a grant
reports `acl_differs_from_parent` — true about its DACL, misleading about intent.

The rules, the measured propagation table, and what the server does with the claim are in
[ACL boundaries](../../../docs/architecture/ntfs-acl-boundaries.md) and
[ADR-0009](../../../docs/decisions/0009-boundaries-are-derived-from-a-projection.md).

---

## Operational tuning

Every setting below is in `adg-ntfs-targets.json`, documented inline in
[the example](adg-ntfs-targets.example.json), and overridable per-run on the command line.

### Make it finish

| Setting | What it buys | What it costs |
| --- | --- | --- |
| `concurrencyLimit` | Descriptor reads in flight. Reading a descriptor over SMB is almost entirely latency, so **8–16 against a remote file server is the single largest win available**. Against a local disk it buys nothing | Nothing that is reported: the walk's decisions stay sequential and the output is identical at every setting |
| `maxDepth` | A hard bound on how far down the walk goes. `0` reads the roots only, which is exactly the Phase 3A behaviour | Reaching it is an error, and the tree is not reconciled. Leave it at the default unless a tree is genuinely unbounded |
| `excludePaths` | Skips a subtree entirely — an archive, a user-profile store, a backup staging area | The run becomes incremental and reconciles nothing |
| `includePaths` | Reports only matching directories, while still walking through the ones between a root and a match | Same: incremental, and a reported directory whose parent was passed over carries `parent_unreadable` |
| `batchSize` | Observations per POST. Larger batches mean fewer round trips; smaller ones make a retry cheaper | A batch never splits a resource, so it may run slightly over |

### Make it survivable

| Setting | What it buys |
| --- | --- |
| `checkpointPath` | The frontier, written atomically every `checkpointIntervalSeconds`. `-Resume` continues the same run id, so the two halves are one run to the server |
| `timeoutSeconds` | A deadline. The walk stops where it is, writes a checkpoint, and reports `canceled` — it never reports the part it reached as the whole |
| `retryCount`, `retryDelaySeconds` | A bounded retry on a descriptor read. An access denial is not worth retrying and is retried anyway, because guessing which failures are transient turns a blip into a permanent gap |

A checkpoint is refused on resume if the roots, depth limit, include or exclude patterns,
reparse policy, or file setting have changed since it was written — resuming across a change
would produce one run that enumerated its scope under two different rules.

### Junctions and symbolic links

`reparsePointPolicy` decides what happens at one:

* **`skip`** (default) reads the junction's own descriptor — it is a real directory with a
  real ACL — and does not descend. What lies below belongs to the target and is reported
  under the target's own path if that is in scope.
* **`ignore`** does not read it at all.
* **`follow`** descends, guarded by the reparse targets already crossed on that branch.

Any of the three leaves paths under the junction unreported, so a tree containing one is
never reconciled.

**A visited-path set does not catch a junction loop**, which is why `follow` needs its own
guard: a junction pointing at its own grandparent produces `\\fs\share\j`,
`\\fs\share\j\j`, `\\fs\share\j\j\j` — every one a path nothing has seen before, so the
visited set never fires and only the depth limit ends the walk, after inventing a chain of
paths that all describe one directory. Comparing reparse *targets* along
the branch turns that into a single honest finding instead of a walk that ends only when the
depth limit does.

### Reading the metrics

Two numbers matter more than the rest, and they are printed together:

```text
  47 unique ACL state(s) across 12,904 director(ies) read; 51 boundar(ies).
```

That ratio is the whole economic case for a boundary scan. Twelve thousand directories, forty
seven distinct permission decisions. The four skip counters are kept apart rather than summed
— reparse points, exclusions, the depth limit, the include filter — because they are four
different operator settings, and "1,412 skipped" does not say which of yours produced it.

A tree that was not fully enumerated says so, and says why:

```text
  \FS01\Finance was not fully enumerated (reparse_point, depth_limit); its scope is not reconciled.
```

---

## Minimum privileges

Domain Admin is **not** required and must not be used.

| To read | The account needs |
| --- | --- |
| A directory's DACL, owner, and control flags | **`READ_CONTROL`** on that directory, and traverse (`FILE_TRAVERSE`) on the path to it |
| A share root over SMB | Enough share-level access to open the share — `Read` is sufficient |
| Nothing else | The collector never needs write, `WRITE_DAC`, `WRITE_OWNER`, `SeTakeOwnershipPrivilege`, `SeBackupPrivilege`, or `SeSecurityPrivilege` |

`READ_CONTROL` is granted implicitly by any of the standard `Read`, `Modify`, or
`Full Control` NTFS permissions, so an account with read access to the data can already read
the ACL. It does **not** require administrative rights on the file server.

**The SACL is deliberately not requested.** Audit entries govern logging, not access, and
reading them needs `SeSecurityPrivilege` — a privilege ADG has no reason to hold. Owner and
group *are* requested, because the owner holds implicit `READ_CONTROL` and `WRITE_DAC`
whatever the DACL says.

Run the collector as a **group managed service account (gMSA)** where possible. It holds no
password you have to store, which is why the configuration file has no credential field.

### When a read is denied

The collector does not escalate. `SeBackupPrivilege` and `SeRestorePrivilege` would let it
bypass the DACL and read anything; `WRITE_OWNER` would let it take ownership and grant itself
`READ_CONTROL`. Both are refused by design, because an auditing tool that can read what its
own credentials are not permitted to read is measuring something other than the estate's real
permissions — and it is a standing escalation path in an account that runs unattended.

An unreadable descriptor becomes a `collectorError` with code `access_denied`, the run goes
`partial`, and no resource observation is emitted at all. An observation asserts the object
was *seen*; a directory whose descriptor was refused was not seen, and a resource row with no
ACEs would read as "nobody has access" — the exact inversion the contract warns about.

---

## What it reports

For each share root, one `ntfs_resource` observation and one `ntfs_ace` per DACL entry:

| Reported | Notes |
| --- | --- |
| `owner_sid`, `group_sid` | The owner's implicit rights are not folded into the ACL |
| `dacl_present` | `false` is a **NULL DACL**: every user has full access. Never the same as an empty DACL, which grants nobody access |
| `dacl_protected`, `inheritance_enabled` | `SE_DACL_PROTECTED`: the directory refuses inherited entries |
| `is_acl_boundary`, `boundary_reason` | Whether permissions change here, and why. See above |
| `parent_acl_hash` | The parent's digest as this run read it: which reading the verdict was judged against |
| `resource_kind` | `directory`, or `file` when file scanning is on |
| `depth_from_share_root` | Derived from the path, so a resumed run and a first walk agree |
| `ace_count` | Equals the number of `ntfs_ace` observations sent, as the contract requires |
| `acl_hash` | The digest of the normalized DACL (contract 1.2). Omitted when the DACL was only partly readable |
| `trustee_sid` | Always a SID. A descriptor stores SIDs, so an account that no longer resolves still yields a usable ACE |
| `access_mask` | Raw, unsigned 32-bit, generic and unrecognized bits included |
| `ace_flags` | The raw `ACE_HEADER.AceFlags` byte, unknown bits included |
| `source` | `explicit` or `inherited`, derived from bit `0x10` so the two can never disagree |
| `order_index` | Position in the DACL as read, counted across entries that could not be reported |

Plus a `principal` observation with `principal_kind: unresolved` for every trustee SID that
did not resolve to a name. An orphaned SID on a folder ACL is a finding, not a defect to be
tidied away, and the backend cannot infer it from the ACE alone.

**`inherited_from` is still never reported.** Naming the ancestor an inherited entry came
from needs the Win32 `GetInheritanceSource`, which this collector does not call. The
`INHERITED` bit already says the entry came from above, and the walk now holds the ancestors —
but deriving *which* one needs the inheritance algebra applied per entry rather than per DACL.
The column exists and stays null rather than carrying a guess.

---

## Why the raw descriptor, and not `Get-Acl`'s rules

The descriptor is read through `System.Security.AccessControl.RawSecurityDescriptor`, built
from the binary form. Three facts the contract needs survive only there:

* **Whether a DACL is present at all.** A NULL DACL and an empty DACL both look like "no
  rules" through a rule collection, and they are opposite facts. `ControlFlags` says which.
* **The raw flags byte.** `FileSystemAccessRule` splits it across `InheritanceFlags`,
  `PropagationFlags`, and `IsInherited`, which loses any bit those three enumerations do not
  name.
* **DACL order.** Evaluation order is what makes a Deny meaningful, and a rule collection is
  not promised to preserve it.

---

## The ACL hash

Each resource carries `acl_hash`: SHA-256 over a canonical text rendering of the DACL,
specified in
[`docs/architecture/ntfs-acl-normalization.md`](../../../docs/architecture/ntfs-acl-normalization.md)
and decided in [ADR-0008](../../../docs/decisions/0008-acl-normal-form-and-hash.md).

`Get-AdgNormalizedAcl` in this collector and `app.domain.normalize_acl` in the backend are
two implementations of one format, and they must agree byte for byte — the server recomputes
the digest from the entries it stores and reports both, so a divergence would show up as a
permanent, unexplainable disagreement on every directory in the estate.
`backend/tests/contracts/test_ntfs_collector.py` runs this collector and compares the two.
Changing either side alone fails that test, which is the point.

The collector **omits** the digest when any DACL entry could not be reported. A digest over
part of a DACL is indistinguishable from a digest of all of it.

---

## Layout

| File | Holds |
| --- | --- |
| `functions/AdgNtfsObservation.ps1` | Raw descriptors to contract observations. Pure; the source keys, the ACL normal form, the inheritance projection, and the boundary rule live here |
| `functions/AdgNtfsConfig.ps1` | Where a walk starts, how far it goes, and what is refused |
| `functions/AdgNtfsSource.ps1` | The only code that touches a file system — the seam the tests mock |
| `functions/AdgNtfsCheckpoint.ps1` | Enough state to resume a walk, and nothing that would let it resume into a different one |
| `functions/AdgNtfsWalk.ps1` | The traversal, its four loop guards, and its metrics |
| `functions/AdgNtfsScan.ps1` | Streaming batches, scopes, status, and reconciliation |
| `functions/AdgNtfsTransport.ps1` | Submission, with the contract's retry rules |
| `tests/` | Pester suites; `Export-AdgNtfsFixturePayload.ps1` writes the payloads the backend contract test validates |

Run the suites with `.\scripts\collector-test.ps1 -Path collector\powershell\ntfs\tests`
(needs Pester 5). All but one mock every function that touches a file system, so they run on a
workstation with no share, no file server, and no network.

**`AdgNtfsRealFileSystem.Tests.ps1` is the exception**, and is the most valuable suite here:
it builds real directories on a real NTFS volume, lets Windows create real children under
them, reads the real descriptors back, and checks that the projection predicted exactly what
Windows produced. A propagation table copied out of documentation would pass every other test
and still be wrong. It needs no elevation, creates junctions rather than symbolic links for
that reason, and cleans up after itself.
