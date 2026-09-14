# Handoff — Phase 3B (`phase-03/02-ntfs-boundary-scanner.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-03a-ntfs-root.md](phase-03a-ntfs-root.md)
**Contract version after this phase:** `1.3`

## Scope completed

The NTFS collector walks trees now, and answers the question a tree scan exists to answer:
**did permissions change here, or is this folder carrying what its parent handed down?**

1. **A configurable, resumable, bounded directory walk.** Maximum depth, include/exclude path
   patterns, a junction policy, a concurrency limit, a deadline, opt-in file scanning, batch
   size, and checkpoint/resume — all validated up front and all refused rather than clamped.
2. **Boundaries are derived, not claimed.** A resource is compared against the DACL its
   parent *projects* onto a child of its kind, and every verdict carries one of seven
   reasons. [ADR-0009](../decisions/0009-boundaries-are-derived-from-a-projection.md) and
   [`ntfs-acl-boundaries.md`](../architecture/ntfs-acl-boundaries.md) are the specification.
3. **The propagation rules were measured against Windows**, not recalled from documentation —
   and the measurement caught a defect that every unit test in the repository had missed.
4. **Contract 1.3**, additive: `ntfs_resource` gains `resource_kind`, `boundary_reason`, and
   `parent_acl_hash`.
5. **Migration `0005_ntfs_boundaries`**: three columns, two shape checks, one consistency
   check, one partial index. Nothing backfilled.
6. **The server checks the claim** rather than storing it unread. `GET /resources/{path}`
   reports the collector's verdict beside the one the backend derives from the parent it
   holds, on the same "report both, settle nothing" terms `acl_hash` already uses.
7. **A run can reconcile a `directory_tree` scope** for the first time in ADG's history — and
   only when the walk skipped nothing at all beneath that root.
8. **Streaming.** The scan emits batches as the walk produces them; memory no longer grows
   with the tree.
9. **Three defects fixed**, two of them pre-existing and shipped in Phase 3A. See below.
10. **409 new tests** — 292 Pester (including 88 against a real NTFS volume) and 117
    Python (102 hermetic, 15 against a real PostgreSQL). The collector suite went 336 → 628;
    the backend suite 3549 → 3666.

## Three defects found, two of them shipped in Phase 3A

These are the most important paragraphs in this handoff, because two of them were live in
`master` and neither was visible to any test in the repository.

### 1. The collector crashed on any DACL carrying a generic right

`[long] ([uint32] $ace.AccessMask)` in `AdgNtfsSource.ps1` and `AdgNtfsObservation.ps1`.
.NET surfaces an access mask as a signed `Int32`, so every mask with the top bit set — every
mask carrying a generic right — arrives negative, and PowerShell's `[uint32]` cast is
**range-checked rather than a reinterpretation**: it throws. `GENERIC_READ` alone is
`-2147483648`.

The effect: the Phase 3A collector reported `access_denied` for any directory whose DACL held
a generic ACE. On the machine this phase was developed on, that is *every* directory. No test
caught it because every fixture mask was positive.

Fixed by `ConvertTo-AdgAccessMask` (`([long] $value) -band 0xFFFFFFFF`). The SMB collector
carried the identical line for a share ACE reported as a mask, and is fixed the same way.

### 2. The boundary projection was wrong for generic rights — on almost every real directory

The first version of the projection mapped one parent ACE to at most one child ACE. That is
true only for an ACE whose mask carries no generic bits.

A generic mask is an indirection Windows cannot apply to an object without resolving it, so
when it materializes such an ACE onto a child it writes **both halves**: an *effective* copy
(mapped through the file-system generic mapping, every inheritance flag cleared) and a
*propagating* copy (unmapped, `INHERIT_ONLY`, still descending).

`0xe0010000` — the generic form of Modify — sits on almost every directory Explorer creates.
So the first walk of a real tree reported **seven directories, one unique ACL hash, and seven
boundaries**, which is the exact failure the whole design exists to prevent. Every unit test
passed, because every fixture mask was specific.

Fixed in `project_inherited_ace` / `Get-AdgInheritedAce`. The same real tree now reports one
boundary: the scan root, which is honestly unknown.

**The process lesson is recorded deliberately:** the rules are now measured on a live volume
on every run (`AdgNtfsRealFileSystem.Tests.ps1`, 88 tests comparing flags *and* masks against
what Windows actually wrote), because a table transcribed from documentation passes every
other test in this repository and is still wrong.

### 3. `GET /resources/{path}/acl` could 422 on ordinary stored data

`recompute_acl_hash` called `normalize_acl`, which rightly refuses to hash a DACL whose
entries claim the same position twice. But an ACE's identity deliberately excludes
`order_index` — so that reordering a DACL does not look like every entry being deleted and
recreated — and nothing removes an entry a later scan stopped seeing. The ACE that used to sit
at position 0 and the different one that replaced it therefore both survive, both claiming
position 0, and the endpoint failed.

Pre-existing since Phase 3A, and much more likely now: a tree walk re-reads the same
directories run after run. Fixed by falling back to the **unordered** normal form, which
exists for exactly this and reports itself as `ordered: false`.

## Files and modules added or materially changed

### Collector

| File | Contents |
| --- | --- |
| `functions/AdgNtfsWalk.ps1` | **New.** The traversal, its four loop guards, `Invoke-AdgParallelMap`, the per-directory read unit, the metrics, and the file-level scan |
| `functions/AdgNtfsCheckpoint.ps1` | **New.** The frontier, the settings fingerprint, atomic writes, and every refusal to resume into a different scan |
| `functions/AdgNtfsObservation.ps1` | Parent/depth derivation, the inheritance projection, the generic mapping, the boundary rule, and the 1.3 fields. **Defects 1 and 2 fixed here** |
| `functions/AdgNtfsConfig.ps1` | Rewritten: the scan policy, path-pattern matching, the nested-root refusal. The share-root-only restriction is lifted |
| `functions/AdgNtfsSource.ps1` | `Get-AdgChildDirectory`, `Get-AdgChildFile`, `Get-AdgFileSecurity`, and one shared descriptor conversion |
| `functions/AdgNtfsScan.ps1` | Rewritten: the streaming batch writer, scope intent, and reconciliation |
| `functions/AdgNtfsTransport.ps1` | Rewritten as a streaming sink; the dry-run file sink moved here |
| `Invoke-AdgNtfsScan.ps1` | The new parameters, resume, and the metrics an operator reads |
| `smb/functions/AdgSmbObservation.ps1` | **Defect 1 fixed here too** |

### Domain and contract

| Module | Contents |
| --- | --- |
| `backend/app/domain/inheritance.py` | **New.** `project_inherited_ace`, `project_inherited_acl`, `projected_child_acl`, `inherited_child_acl_hash`, `map_generic_rights`, `boundary_reason_for`, `AclBoundaryReason`, `SUBSTITUTED_TRUSTEES` |
| `backend/app/domain/resources.py` | `ResourceKind`; `DirectoryResource` gains `resource_kind` and refuses a share root reported as a file |
| `backend/app/contracts/v1/common.py` | `SCHEMA_VERSION` `1.2` → `1.3`, and `schema_minor()` — a rule a later minor introduces must apply only to payloads claiming that minor |
| `backend/app/contracts/v1/observations.py` | The three new fields and their validators |
| `docs/contracts/v1/*.schema.json` | `resourceKind`, `aclBoundaryReason`, the three fields, and the boundary conditional |

### Storage, ingestion, and query

| Module | Contents |
| --- | --- |
| `database/migrations/versions/0005_ntfs_boundaries.py` | **New.** Additive; `alembic check` clean, downgrade round-trips |
| `backend/app/models/schema.py` | The three columns, their constraints, and `ix_ntfs_resources_boundaries` |
| `backend/app/ingestion/plan.py`, `service.py` | The three fields carried through, stored as sent |
| `backend/app/repositories/resources.py` | `NtfsBoundaryVerification`, `verify_boundary`, `parent_key`, **defect 3 fixed** |
| `backend/app/services/resources.py` | `ResourceDetail` gains `boundary` and `parent` |
| `backend/app/api/resources.py` | `BoundaryView`, and `resource_kind` / `parent_path` / `boundary_reason` on the summary |

### Documentation

| File | Contents |
| --- | --- |
| `docs/architecture/ntfs-acl-boundaries.md` | **New.** The normative specification, including the measured table and both split rules |
| `docs/decisions/0009-…` | **New.** Why the comparison is against a projection, and why unknown is a boundary |
| `docs/contracts/collector-protocol.md` | §6 rewritten for a walk that can reconcile; a new §1.3 |
| `collector/powershell/ntfs/README.md` | Operational tuning, the boundary reasons, and what the real-filesystem suite covers |
| `docs/architecture/resource-inventory.md`, `ntfs-acl-normalization.md`, `README.md`, `collector/README.md` | Extended |

## Important architecture decisions

### A child is compared against the parent's projection, never against the parent

Inheritance sets the `INHERITED` bit on every entry it copies, so a perfectly inheriting child
has a different digest from its parent. The comparison is against what the parent hands
*down* — and the projection reaches a fixed point after one level, which is what makes one
comparison correct for every descendant of a uniform subtree.

### Unknown is a boundary

Four of the seven reasons — `share_root`, `scan_root`, `parent_unreadable`,
`parent_null_dacl` — mean nobody established anything, and all four report a boundary. A
boundary that is not really there costs one extra stored ACL. A boundary reported *false*
tells the next scan it may stop looking and silently drops every permission change beneath it.

### Intent at the start, achievement at the end

`incremental` is declared before the walk, because the server refuses to let it change
mid-run. It states intent: `false` only when nothing in the configuration already says the run
will look at part of its scope. `reconciled_scopes` states achievement, decided from what
happened: a root is listed only if the walk read it and skipped **nothing** beneath it — no
depth limit reached, no exclusion, no unfollowed junction, no denied descriptor, no
unlistable directory, no timeout, no resume. Conflating the two either forbids reconciliation
on every configured scan or grants it to a truncated one.

### A visited-path set does not catch a junction loop

A junction pointing at its own grandparent produces `\\fs\share\j`, `\\fs\share\j\j`,
`\\fs\share\j\j\j` — every one a path nothing has seen, so the visited set never fires and
only the depth limit ends the walk, after inventing a chain of paths describing one directory.
The walk therefore carries the reparse *targets* crossed on each branch and refuses a junction
whose target that branch has already crossed. A junction whose target cannot be read is not
followed either: an unverifiable link is exactly the one that might be the cycle.

### The boundary claim is verified on the read side

`plan_batch` is hermetic and holds one batch; a parent is routinely in a different one. So the
claim is stored as sent and checked where the parent's entries are in reach. This is the
opposite of `acl_hash`, which the planner *does* verify — because there the evidence arrives
in the same batch by contract (§5).

### The version gate is what keeps 1.3 additive

`boundary_reason` is required when `is_acl_boundary` is true — but only for payloads declaring
1.3 or later. A 1.2 collector sets the flag on a share root and has never heard of the field;
holding it to a rule it predates would reject it for being old rather than wrong. Those rows
store a `NULL` reason and are **not backfilled**: deriving one would write a verdict nobody
made, and the API already reports the server's own derivation beside the collector's claim.

### Concurrency is real, and the walk stays sequential

`Invoke-AdgParallelMap` reads one BFS level's descriptors concurrently and returns results in
input order; every decision is made afterwards, in order. At concurrency 1 the script block
runs inline — which is not only an optimization: a PowerShell parallel runspace is a separate
session state, a Pester mock does not exist inside one, and a suite exercising the walk
through the parallel path would silently be testing the real file system of whatever machine
it ran on. The walk suites therefore pin concurrency at 1, and the primitive is unit-tested at
both settings with script blocks that need nothing mocked.

## Schemas and contracts introduced or changed

**Contract 1.3, additive.** `ntfs_resource` gains:

| Field | Meaning |
| --- | --- |
| `resource_kind` | `directory` (default) or `file`. Decides which projection applies |
| `boundary_reason` | One of seven values, or absent. Required at 1.3+ when `is_acl_boundary` |
| `parent_acl_hash` | The parent's digest as this run read it — which reading was judged |

**New protocol rules** (`docs/contracts/collector-protocol.md`):

* §6 — rewritten. A walk that really did enumerate a tree may reconcile it, and the split
  between intent (`incremental`) and achievement (`reconciled_scopes`) is spelled out;
* §10, the 1.3 section — the three fields, the version-gated requirement, and the two rules
  binding a collector that sends them.

**New model validation.** A reason on a resource that is not a boundary is rejected at every
version. A share root reported as a file is rejected by the domain type.

**Storage:** three columns on `ntfs_resources` with `ck_resource_kind_valid`,
`ck_boundary_reason_valid`, `ck_ntfs_resources_reason_implies_a_boundary` (one direction
only), `ck_ntfs_resources_parent_acl_hash_shape`, and `ix_ntfs_resources_boundaries` — a
partial index over the boundaries, which are the small minority of rows a full walk writes.

## Tests run and exact results

All run on 2026-09-14 against this tree.

| Gate | Command | Result |
| --- | --- | --- |
| Backend lint | `.\scripts\backend-lint.ps1` | **passed** — ruff check clean, 118 files formatted, mypy strict clean on 117 source files |
| Backend hermetic | `pytest tests -m "not smoke"` | **3416 passed, 9 skipped**, 250 deselected |
| Backend smoke | `pytest tests -m smoke` (PostgreSQL) | **250 passed**, 3425 deselected |
| Collector | `.\scripts\collector-test.ps1` | **628 passed, 0 failed** |
| Frontend | `.\scripts\frontend-check.ps1` | **passed** — lint, typecheck, unit tests, build |
| Migration | `alembic upgrade head` / `check` / `downgrade -1` / `upgrade head` / `check` | applied, **no drift**, downgrade round-trips |
| Collector output | `.\scripts\validate-collector-output.ps1 -Path <fixture>` | **no findings** on the collector's real output |

The smoke suite ran against `adg_phase3b_test`
(`ADG_DATABASE_URL=...:5432/adg_phase3b`), not the developer default, because two sessions
share this workstation and the autouse `clean_tables` fixture truncates everything.

### New tests

| Suite | Count | Covers |
| --- | --- | --- |
| `…/tests/AdgNtfsInheritance.Tests.ps1` | 64 (new) | The measured propagation table both ways, the generic split, CREATOR OWNER, OWNER RIGHTS, projection determinism, and all seven boundary reasons with their precedence |
| `…/tests/AdgNtfsWalk.Tests.ps1` | 59 (new) | Depth, include/exclude, all three reparse policies, a junction cycle, denied descriptors vs denied enumeration, orphaned trustees, file scanning, cancellation, metrics, and `Invoke-AdgParallelMap` at both concurrencies |
| `…/tests/AdgNtfsCheckpoint.Tests.ps1` | 32 (new) | The fingerprint's contents *and* its deliberate omissions, the frontier round trip, the tri-state parent flag, atomic writes, five refusals, and six resume behaviours |
| `…/tests/AdgNtfsRealFileSystem.Tests.ps1` | 88 (new) | **A real NTFS volume.** The projection measured against Windows for 19 ACE shapes × container/file/grandchild, real inheritance breaks, real junctions and a real cycle, a real denied enumeration, and a 40-level tree |
| `…/tests/AdgNtfsScan.Tests.ps1` | 45, from 31 (rewritten) | The batch writer, streaming order, status, scope intent, and every case where reconciliation is refused |
| `…/tests/AdgNtfsConfig.Tests.ps1` | 44, from 16 (rewritten) | The scan policy, every bound, path-pattern semantics, and the nested-root refusal |
| `…/tests/AdgNtfsObservation.Tests.ps1` | 51, from 44 | The 1.3 fields on the observation, derived depth, and the refusals around them |
| `backend/tests/domain/test_inheritance.py` | 89 (new) | The same table in Python, the generic split, substituted trustees, and the boundary rule end to end on a realistic tree |
| `backend/tests/contracts/test_ntfs_collector.py` | 49, from 36 | The **fourth** cross-language check: every boundary verdict in a tree run re-derived from the collector's own payloads |
| `backend/tests/db/test_ntfs_resources.py` | 58, from 43 | The boundary verdict over a real database, file resources, a pre-1.3 collector, and the position-collision fallback |

### The cross-language checks now number four

`test_ntfs_collector.py` runs the PowerShell collector over a fake estate and re-derives, in
Python: every `source_key`, every `acl_hash`, every **boundary verdict**, and every stored row
through the planner. The third is new and is the one this phase turns on — a divergence in the
projection would not fail loudly on its own, it would show up as a permanent disagreement
about every directory in the estate.

### Tests changed rather than added

* `test_plan_ntfs.py::test_a_protected_dacl_blocks_inheritance_and_is_a_boundary` — now sends
  a reason, and asserts the row carries it.
* `TestTheCollectorReconcilesNothing` → `TestScopesAndReconciliation`. The old class asserted
  that no run could ever reconcile, which was true of Phase 3A by construction and is now
  false. Rewritten to assert the stronger property: a clean complete walk reconciles its tree,
  and every run with an error reconciles nothing.
* Two projection tests in each language used a generic mask to assert "the raw mask is
  untouched". That premise was the defect. Rewritten to assert what is actually true: a
  specific mask passes through unchanged, an unrecognized bit survives, and a generic mask
  splits.
* `test_ntfs_resources.py` — one pre-existing scope literal was written with unescaped
  backslashes (`"\fs01\finance\payroll"` → a form feed and a literal `\p`), raising a
  `SyntaxWarning` on every run. Replaced with the module constant the file already had for
  exactly this reason.

## Known limitations

1. **A directory beneath a `CREATOR OWNER` grant reports a false boundary.** Windows adds an
   ACE naming whoever created it, which is not a fact about the parent.
   `SUBSTITUTED_TRUSTEES` names the SIDs so the case stays tellable apart; nothing guesses the
   creator. Measured and pinned rather than merely asserted.
2. **The walk needs UNC paths, so the real-filesystem suite exercises the acquisition layer
   rather than the walk.** Writing to a UNC path on the local machine means `\\localhost\C$`,
   and reaching an administrative share needs local Administrators — which this collector must
   never require. The seam between the tested halves is one function call wide, and it *was*
   exercised manually: a real walk over `\\localhost\C$\code\adg\docs` reports 7 directories,
   1 unique ACL state, 1 boundary (the scan root). That is how defects 1 and 2 were found, and
   it is not automated.
3. **The parallel path is not exercised by the walk suites.** A Pester mock does not cross a
   runspace boundary. `Invoke-AdgParallelMap` is unit-tested at both settings; the walk is
   tested at concurrency 1.
4. **`inherited_from` is still never populated.** The `INHERITED` bit says an entry came from
   above; naming *which* ancestor needs `GetInheritanceSource`, or per-entry matching against
   the projection this phase now has.
5. **The walk never prunes.** It reads every directory it reaches. Stopping below a
   non-boundary is the obvious optimization and is deliberately not taken: it would change
   what a reconciled scope means, and that needs deciding before it is implemented.
6. **The SACL is still never read**, and `local_path` is still never reported for an NTFS
   resource. Unchanged from Phase 3A, both deliberate.
7. **A file's ACEs and a directory's share one table and one key space.** Nothing stops a
   later run reporting the same path as the other kind; `resource_kind` is newest-wins like
   every other column.
8. **A directory's entry rows are still never removed.** Phase 3A's limitation 8, and the
   cause of defect 3 above. The unordered fallback makes it survivable; cleaning it up still
   belongs with Phase 7.
9. **The open BUILTIN scoping hazard is unchanged.** Only `local_group` principals and
   `local_group_member` edges are host-scoped, so two collected domains still merge their
   `BUILTIN\Administrators`.

## Security and privilege assumptions

**Domain Admin is not required and must not be used.** Unchanged from Phase 3A, with two
additions the walk introduces.

| To read | The account needs |
| --- | --- |
| A resource's DACL, owner, and control flags | `READ_CONTROL` on it, and traverse on the path to it |
| The *contents* of a directory | `FILE_LIST_DIRECTORY` on it — a **different right** |
| A share root over SMB | Enough share-level access to open the share; `Read` suffices |
| Nothing else | No write, no `WRITE_DAC`, no `WRITE_OWNER`, no `SeTakeOwnershipPrivilege`, no `SeBackupPrivilege`, no `SeSecurityPrivilege` |

The second row is new and is reported as such: a directory whose ACL was read and whose
contents could not be listed is an ordinary result, and the walk reports the directory,
records the failure, and marks the tree non-exhaustive — rather than dropping both facts.

**The collector still does not escalate when a read is denied.** `SeBackupPrivilege` would
bypass the DACL; `WRITE_OWNER` would let it take ownership and grant itself `READ_CONTROL`.
Both refused by design.

**Nothing is written to a target.** The real-filesystem test suite writes ACLs, but only to
its own temporary directory, only through the Access section (writing a descriptor that
claims the SACL needs `SeSecurityPrivilege`, which this project must never hold), and it
restores and removes everything it created.

**No secrets in source control.** `adg-ntfs-targets.example.json` still has no credential
field; the checkpoint file holds pending paths and digests, and no credentials.

## Migration and compatibility notes

* **`0005_ntfs_boundaries` is additive.** Three columns — one `NOT NULL` with a server
  default, two nullable — four check constraints, one partial index, and a table-comment
  change. No existing column is altered, narrowed, or dropped, and **no existing row is
  rewritten**. Applied, `alembic check` clean, downgrade round-trips twice.
* **Contract 1.3 is additive, and the new requirement is version-gated.** A collector sending
  1.0 through 1.2 is unaffected, including one that sets `is_acl_boundary` with no reason.
* **`SCHEMA_VERSION` moved `1.2` → `1.3`**, which regenerated the AD-graph fixtures
  (`python -m tests.fixtures.build_ad_graph`) — a version-string change only. **Edit the
  generator, never the JSON**; a test compares them byte for byte.
* **Two collector behaviour changes that are not additive**, both intended:
  * `Invoke-AdgNtfsScan` and `Invoke-AdgNtfsScanRun` now take `-OnStart`/`-OnBatch`/
    `-OnCompletion` sinks instead of returning a run holding every batch. `Split-AdgNtfsObservationBatch`
    is replaced by the batch writer; `Get-AdgNtfsResourceObservation` by the walk.
  * `ConvertTo-AdgNtfsResourceObservation` takes `-BoundaryReason` instead of `-IsShareRoot`,
    and derives `is_acl_boundary` and `depth_from_share_root` rather than accepting them.
* **`-ShareRoot` and `shareRoots` still work**, as spellings of `-ScanRoot` / `scanRoots`. An
  existing Phase 3A configuration and command line are unchanged.
* **`-RunPerShareRoot` is renamed `-RunPerScanRoot`.** Not aliased: the flag interacts with
  checkpointing, and a silently accepted old spelling would pair with a new setting it was
  never designed against.
* **No API response field was removed or renamed.** `NtfsResourceSummary` gains
  `resource_kind`, `parent_path`, and `boundary_reason`; `NtfsResourceDetailView` gains
  `parent` and `boundary`.

## Prerequisites for the next prompt

**Phase 3C / whatever walks next needs, in order:**

1. **A decision about pruning.** The walk reads every directory. Stopping below a
   non-boundary would cut a large estate's cost by orders of magnitude and would change what
   a reconciled `directory_tree` scope can mean — those two have to be decided together, not
   in sequence.
2. **`inherited_from`, now that it is derivable.** The projection can say which ancestor an
   inherited entry came from by matching a child's entries against each ancestor's
   projection. That is a real answer rather than the guess Phase 3A refused to make.
3. **A `principals/{trustee}/resources` route.** Still outstanding from the Phase 3A handoff;
   the reference rows and indexes have been there since. Not required by this phase and not
   built.
4. **An automated end-to-end walk.** The manual `\\localhost\C$` walk found two shipped
   defects in one run. Something equivalent — a UNC path reachable without elevation, or a
   test share created by an elevated CI step — would be worth more than any number of further
   mocked cases.

**Phase 4B (effective access) can now assume:** the inheritance *propagation* algebra exists
and is measured (`app.domain.inheritance`), including the generic mapping and which trustees
Windows substitutes. It still owes DACL-order evaluation, owner implicit rights, `CREATOR
OWNER` resolution at access time, and NULL-DACL semantics.

**Phase 7 (history and absence) inherits a live question.** `reconciled_scopes` is now
sometimes non-empty, and nothing yet acts on it. The semantics this phase chose — a root is
reconciled only if the walk skipped nothing at all beneath it — should be reviewed before the
first code that marks anything absent is written.

**Anyone touching the projection** must read
[`ntfs-acl-boundaries.md`](../architecture/ntfs-acl-boundaries.md) first, and must re-measure
rather than reason: `AdgNtfsRealFileSystem.Tests.ps1` is the specification, and the two
implementations move together or the contract test fails.

## `git status --short`

Taken after the commit, so only another session's in-flight work remains:

```text
 M backend/tests/contracts/test_smb_collector.py
```

That file was already modified when this phase started — a formatting-only change by a
concurrent session — and was deliberately excluded from this phase's commit, exactly as
Phase 3A excluded it.
