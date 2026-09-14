# Windows test-tree generator

Builds real NTFS trees on a local volume so the collector can be run against something whose
every interesting case was put there deliberately, and written down.

```powershell
# The correctness tree: one directory per case a scanner gets wrong.
.\scripts\windows-test-tree\New-AdgTestTree.ps1

# A performance tree of known size and known ACL variety.
.\scripts\windows-test-tree\New-AdgTestTree.ps1 -Profile medium -Force

# Clean up. Restores every ACL it wrote before deleting anything.
.\scripts\windows-test-tree\New-AdgTestTree.ps1 -Remove
```

Trees land in `.tmp\windows-test-tree\<profile>` by default, which `.gitignore` already
excludes, with the manifest written beside them.

---

## This writes ACLs, and ADR-0004 is not suspended

[ADR-0004](../../docs/decisions/0004-read-only-collector-posture.md) makes the *application*
read-only: the collector never writes to a target, never enables a privilege, never takes
ownership, never modifies a descriptor to make a read succeed. That rule is about the thing
that ships.

This is a developer and CI tool. It writes only beneath the root it is given, only through the
**Access section** of a descriptor — never the SACL, which would need `SeSecurityPrivilege`,
a privilege this project must never hold — and `Remove-AdgTestTree` resets every ACL it wrote
before deleting anything, because a directory carrying a descending `Deny` cannot otherwise be
removed.

Everything runs unelevated, as the collector must.

---

## Two kinds of tree

### `semantics` — the correctness tree

Small, hand-specified, about 117 directories. One per case, and **every directory carries an
expectation**: how it was built, and what the collector is therefore expected to say about it.
That expectation is what
[`AdgNtfsTreeValidation.Tests.ps1`](../../collector/powershell/ntfs/tests/AdgNtfsTreeValidation.Tests.ps1)
asserts against, alongside a second independent read of every descriptor.

| Case | What it is there to catch |
| --- | --- |
| `01-fully-inherited` | A directory that inherits cleanly must not read as a boundary |
| `02-explicit-child` | An explicit ACE with and without inheritance flags — the second makes its children boundaries too, the first does not |
| `03-inheritance-disabled` | Protection *with* the entries copied and protection *without*. Two different facts that a rule collection cannot tell apart |
| `04-inheritance-reenabled` | Disabled and then re-enabled: the result must be indistinguishable from a directory nobody touched |
| `05-deep` | Twelve levels, for the depth limit and for inheritance reaching a fixed point |
| `06-wide` | Sixty-four identical siblings, for the one-level-at-a-time walk |
| `07-inaccessible` | A directory whose ACL reads and whose **contents cannot be listed** — two different rights |
| `08-reparse` | A junction to a sibling, a junction to its own parent (the cycle), and a junction whose target is gone |
| `09-mixed-allow-deny` | A Deny ahead of an Allow for one trustee: DACL order has to survive |
| `10-duplicate-acls` | Two identically permissioned directories in unrelated branches — same `acl_hash`, independent verdicts — plus an orphaned SID that resolves to no name |
| `11-generic-mask` | `0xe0010000` with inheritance flags. Not exotic: it sits on almost every directory Explorer creates, and it broke both the mask reader and the projection in Phase 3B |

The specification is declarative (`Get-AdgSemanticsTreeSpec`), which is what lets Pester
enumerate one test per case before anything has been built.

**The tree is created in two passes**, and the order is load-bearing. Every directory and
junction is created first; every ACL is written afterwards, root first. A directory carrying a
descending `Deny` cannot have children created beneath it — the generator obeys the ACLs it
writes like any other process — and a directory protected before its children exist would
propagate nothing to them.

### `small` / `medium` / `large` — the performance trees

A balanced tree of a known size whose ACLs come from a fixed small set, so the
directories-to-distinct-ACLs ratio is a **parameter** rather than a discovery.

| Profile | Fanout | Depth | Directories | ACL variants | Files per directory |
| --- | ---: | ---: | ---: | ---: | ---: |
| `small` | 5 | 4 | 781 | 8 | 3 |
| `medium` | 7 | 5 | 19,608 | 20 | 0 |
| `large` | 9 | 5 | 66,430 | 40 | 0 |

Only `small` carries files, so `.\scripts\ntfs-benchmark.ps1 -IncludeFiles` has something to
measure without multiplying the build time of the larger trees — and what a file scan shows is
a ratio, which the smallest tree reports as well as the largest.

**Building is slower than scanning**, by roughly an order of magnitude: every directory is a
create and every variant a descriptor write. That cost is the generator's, and the benchmark
reports it separately so it cannot be mistaken for the collector's.

---

## The manifest

Written to `<root>.manifest.json`. Per directory:

| Field | Meaning |
| --- | --- |
| `path`, `case`, `note` | Where it is, which case it belongs to, and why it is there |
| `protected`, `reparsePoint`, `danglingReparsePoint` | How it was built |
| `enumerable` | Whether its contents can be listed by the account that built it |
| `reported` | Whether the collector should produce an observation for it at all |
| `aclGroup` | A label: every directory sharing one must produce one `acl_hash` |
| `expectedBoundary`, `expectedBoundaryReason` | The verdict the collector should reach, or `null` where the answer depends on something outside the directory |
| `expectedTrustees` | SIDs that must appear on its DACL |

Expectations are recorded **at construction time** and deliberately not derived from the ACL
afterwards. An expectation computed by the same arithmetic as the implementation tests nothing.

---

## One case cannot be built unelevated, and it is not pretended

**A directory whose descriptor cannot be read.** The owner of an object holds `READ_CONTROL`
and `WRITE_DAC` implicitly whatever the DACL says, and handing ownership to somebody else needs
`SeRestorePrivilege`.

So the generator builds the case it *can* build honestly: a directory whose descriptor reads
fine and whose **contents cannot be listed** — `FILE_LIST_DIRECTORY`, a different right from
`READ_CONTROL`, and the case that actually turns up in an estate. The unreadable-descriptor
path stays covered by the mocked walk suite, and the manifest records the gap as
`deniedDescriptorNotBuildable` rather than leaving it unstated.

---

## Scanning a generated tree needs a UNC route

The collector identifies a directory by its UNC path, because a drive letter does not say which
server it is on. On one machine the only UNC route to an ordinary directory is the drive's
administrative share — `\\localhost\C$` — and reaching that needs local Administrators, a right
the collector itself must never require.

`New-AdgTestTree.ps1` reports whether that route works from the current session. Without it:

* the validation suite **skips**, with the reason, rather than rewriting itself to walk local
  paths — which would test a configuration that never ships;
* the benchmark **refuses to run**, for the same reason.

---

## Two traps worth knowing about

**`Get-AdgTestTreeOption`, not `$hashtable.Key`.** Under `Set-StrictMode -Version Latest` a
missing hashtable key is an error rather than `$null`, and the specification omits most keys on
most entries.

**`Resolve-AdgTestTreePath`, not `[System.IO.Path]::GetFullPath`.** PowerShell's current
location and the .NET process working directory are different things. This module deletes what
it is pointed at, and it was caught deleting the wrong path: a `-Remove` given a relative root
reported success against a directory under the user's profile while the real tree stood
untouched, and the next benchmark run silently reused the stale tree and measured it.

---

## See also

* [`AdgNtfsTreeValidation.Tests.ps1`](../../collector/powershell/ntfs/tests/AdgNtfsTreeValidation.Tests.ps1) — the suite this exists for
* [`ntfs-scan-performance.md`](../../docs/architecture/ntfs-scan-performance.md) — what the performance trees measured
* [`ntfs-acl-boundaries.md`](../../docs/architecture/ntfs-acl-boundaries.md) — the specification the expectations encode
