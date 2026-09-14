# ADG NTFS share-root collector

Reads the NTFS security descriptor of each configured SMB share root and reports it as
[contract v1](../../../docs/contracts/collector-protocol.md) observations.

It reports readings, never conclusions. Nothing here computes effective access, expands
group membership, resolves inheritance, applies Deny precedence, expands generic rights, or
drops mask bits it does not recognize — an ACE is evidence, and a simplified ACE is no longer
evidence. Those computations are the backend's, from these facts together with the share
layer.

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
.\Invoke-AdgNtfsScan.ps1 -ShareRoot \\FS01\Finance -DryRun -OutputDirectory C:\code\adg\.tmp\ntfs

# Collect and submit
.\Invoke-AdgNtfsScan.ps1 -ConfigPath .\adg-ntfs-targets.json -ApiBaseUrl http://localhost:8000 -RunPerShareRoot
```

Copy [`adg-ntfs-targets.example.json`](adg-ntfs-targets.example.json) to
`adg-ntfs-targets.json` and edit it. The example documents every option inline.

Targets are always explicit. There is no estate-wide sweep, and that is a posture decision
rather than a missing feature — a tool that walks shares it was never pointed at looks
exactly like reconnaissance, and it makes a run's declared scope mean something vague.
Generate the list from ADG's own share inventory (`GET /api/v1/servers/{server}/shares`,
collected by the SMB collector) rather than by probing.

**Use `-RunPerShareRoot` for anything larger than a handful of roots.** One unreadable
descriptor downgrades a combined run to `partial`, and an operator reading that status cannot
tell which root failed.

---

## What this phase reads, and what it does not

**Share roots only.** A path *inside* a share — `\\FS01\Finance\Reports` — is refused, with a
message saying so. Two of the facts a resource observation carries cannot be established for
a subdirectory without reading its ancestors: whether its DACL differs from its parent's, and
how deep it sits in a tree nothing has walked. Storing a guessed boundary would mislead the
recursive scan that follows, which uses boundaries to decide where to stop looking.

**Every run is `incremental`, and reconciles nothing.** The contract's file-system scope kind,
`directory_tree`, claims the whole tree beneath a path was enumerated. A share-root read has
enumerated no tree, so reconciling it would mark every directory under every root as deleted.
The run declares its `directory_tree` scopes — they state what it set out to look at, and the
tree walk that can honestly claim them will reconcile them — and marks itself incremental,
which the server refuses to let reconcile at all. This holds even for a clean, error-free
run. Absence of a directory from ADG therefore means *nobody has looked*, which is exactly
what it should mean until the recursive scanner ships.

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
| `is_acl_boundary` | Always `true` for a share root — see below |
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

**A share root is always reported as an ACL boundary.** Its parent lies outside the share —
often outside anything ADG audits — so there is nothing to compare it against, and "not a
boundary" would tell the tree walk it could skip the one directory every path through that
share must pass.

**`inherited_from` is never reported.** Naming the ancestor an inherited entry came from needs
the Win32 `GetInheritanceSource`, which this collector does not call. The `INHERITED` bit
already says the entry came from above; the tree walk, which reads the ancestors, can say
which one.

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
| `functions/AdgNtfsObservation.ps1` | Raw descriptors to contract observations. Pure; the source keys and the ACL normal form live here |
| `functions/AdgNtfsConfig.ps1` | Which share roots to read, and what is refused |
| `functions/AdgNtfsSource.ps1` | The only code that touches a file system — the seam the tests mock |
| `functions/AdgNtfsScan.ps1` | Orchestration, retries, scopes, batching |
| `functions/AdgNtfsTransport.ps1` | Submission, with the contract's retry rules |
| `tests/` | Pester suites; `Export-AdgNtfsFixturePayload.ps1` writes the payloads the backend contract test validates |

Run the suites with `.\scripts\collector-test.ps1 -Path collector\powershell\ntfs\tests`
(needs Pester 5). They mock every function that touches a file system, so the whole suite
runs on a workstation with no share, no file server, and no network.
