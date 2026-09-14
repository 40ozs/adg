# Handoff — Phase 4C (`phase-04/03-effective-access-hardening.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-04b-effective-access.md](phase-04b-effective-access.md)
**Contract version after this phase:** `1.3` — unchanged. No collector payload, no schema, no
migration. One additive `AccessCondition` value.

## Scope completed

Phase 4B built the effective-access engine and tested it with hand-written cases: one test per
behavior, each written by the same person who wrote the behavior. This phase attacked it with
cases nobody chose, and checked the answers against Windows.

1. **A generated case matrix** (`backend/tests/access_engine/matrix.py`) — 5,631 ACL cases
   across every dimension the prompt names, plus 558 whole-resolver cases. Deterministic and
   ordered, and each ACL case renders to SDDL.
2. **A Windows verification harness** (`scripts/windows-access-check/`) that asks
   `AuthzAccessCheck` — the API behind the Windows *Effective Access* tab — what it grants for
   every case, and a second probe that measures what a real process gets on a real directory.
   **Neither needs elevation, a domain, or a share.**
3. **5,626 of 5,628 answerable cases agree with Windows exactly.** The two that differ are the
   NULL-DACL valid-rights mask, and a real directory settles it in the engine's favor.
4. **Two defects found and fixed**, one of them serious, plus one found and pinned as a strict
   `xfail` because fixing it is out of this phase's remit.
5. **32 invariants** over the resolution matrix — soundness, layer ordering, directional
   honesty — mutation-tested to confirm they bite.
6. **Performance measured and bounded** for the three shapes the prompt names, statement-counted
   in tests and timed in a benchmark.
7. **The Cartesian product is unreachable structurally**, held in place by tests that read the
   OpenAPI document rather than by calling endpoints somebody thought of.
8. **107 new tests** — 90 hermetic, 17 through HTTP against PostgreSQL. The hermetic suite went
   3,641 → 3,731; the full suite 3,947 → 4,053.

## Three findings

### 1. An unread directory reported "nobody has access", certainly — **fixed**

The worst defect in the phase, found by the invariant
`test_an_unread_descriptor_is_never_certain`.

A resource whose security descriptor no run had read resolved to:

```
rights     = 0x00000000
access     = False
certainty  = certain          <-- wrong
conditions = ['empty_dacl', 'ntfs_acl_not_observed']
```

`NTFS_ACL_NOT_OBSERVED` was raised correctly but belonged to **neither**
`OVERSTATING_CONDITIONS` nor `UNDERSTATING_CONDITIONS`, so `certainty_of` folded it to
`CERTAIN`. The share side had been handled — `SHARE_ACL_NOT_OBSERVED` was in the overstating
set since 4B — and the file-system side had been missed. A classic asymmetry.

This is precisely the failure ADR-0011 exists to prevent, and the Phase 4B handoff's own
prerequisite 1 instructs Phase 5 to branch on `AccessCertainty`. A risk rule following that
instruction would have treated *"we never looked at this directory"* as *"definitively nobody
has access"* and stopped looking exactly where it needed to look.

It also reported `EMPTY_DACL` — a claim that a descriptor nobody read was observed to be
empty, contradicting the other finding on the same answer.

**Fixed:** `NTFS_ACL_NOT_OBSERVED` joins `UNDERSTATING_CONDITIONS`, so the answer is now
`certainty: at_least`; and `resolve_access` suppresses `EMPTY_DACL` when the provenance is
`UNOBSERVED`. No existing test caught either, and all 3,641 still passed after the fix — the
gap was in coverage, not in disagreement.

### 2. A disabled account was indistinguishable from an enabled one — **fixed**

The prompt's dimension list names *"disabled/unresolved subjects where policy requires explicit
handling"*. Unresolved was handled. Disabled was not modeled at all: `PrincipalRecord` carries
`enabled`, `SubjectFacts` had no such field, and a disabled account with Full Control over
payroll resolved to `certainty: certain` with nothing saying the account cannot log on.

**Fixed:** `SubjectFacts.enabled`, a new `AccessCondition.SUBJECT_DISABLED`, and the service
passes the value through. It sits in `OVERSTATING_CONDITIONS` — the rights are what the ACL
grants and no logon can currently exercise them, so the reported rights are an upper bound on
what anyone can do with the account today. The rights are still reported, because a disabled
account holding Full Control is a finding and not a non-event.

### 3. One listing's cost follows the estate, not the page — **pinned, not fixed**

`/access/resources/{r}/principals` expands every trustee on the ACL downward, builds the whole
set of principals the resource reaches, and only then slices out the requested page. The same
twenty-five-row page:

| estate | `resource -> principals` | `principal -> shares` |
| ---: | ---: | ---: |
| 100 | 13.1 ms | 12.2 ms |
| 400 | **149.3 ms** | 12.0 ms |

The two `principal -> ...` listings page in the database with a keyset cursor and stay flat.
The endpoint admits the difference in its own response: it is the only access listing that can
report `page.total`, and it cannot know that number without having materialized what it counts.

What is **not** wrong: the resolution work is correctly bounded — a twenty-five-row page runs
exactly twenty-five access checks whatever the estate — and the statement count is constant. A
profile at size 400 puts 68% of the time in `select.select`, waiting on PostgreSQL. The cost is
the expansion query and its result set, not the engine.

It matters because ACLs name broad groups: an ACE naming `Domain Users` makes the expanded set
every user in the domain, per request, on every page.

**Not fixed here.** Fixing it means moving the expansion and the slice into the database or
holding a traversal across pages; both change the shape of `AccessService` and one changes what
`page.total` can mean. Pinned as a **strict `xfail`** in
`tests/db/test_access_performance.py::TestWhereTheCostStillFollowsTheEstate`, so the day it is
fixed the test fails and somebody deletes it.

## Files and modules added or materially changed

### Added

| File | Lines | Contents |
| --- | --- | --- |
| `backend/tests/access_engine/matrix.py` | 712 | The case space: 5,631 ACL cases, 558 resolution cases, SDDL rendering, fingerprints |
| `backend/tests/access_engine/test_matrix.py` | 540 | 32 invariants over the resolution matrix |
| `backend/tests/validation/test_windows_oracle.py` | 373 | 34 tests comparing the engine with Windows |
| `backend/tests/api/test_access_bounds.py` | 240 | 9 tests; the Cartesian-product guard, read from the OpenAPI document |
| `backend/tests/db/test_access_performance.py` | 393 | 17 tests; cost at two estate sizes, paging bounds, the pinned defect |
| `backend/tests/support/access_estate.py` | 244 | One estate generator, shared by the cost tests and the benchmark |
| `backend/tests/benchmarks/access_benchmark.py` | 211 | The timed harness for the three shapes |
| `backend/tests/benchmarks/test_access_benchmark_harness.py` | 125 | 15 tests that the harness still runs |
| `backend/tests/fixtures/windows/ntfs-access-oracle.json` | 1.1 MB | 5,631 answers from Windows, with the host and the generic mapping |
| `backend/tests/fixtures/windows/real-directory-access.json` | 5 KB | 9 real directories, both instruments |
| `scripts/windows-access-check/AdgAuthzProbe.psm1` | 530 | The Authz P/Invoke |
| `scripts/windows-access-check/AdgRealAccessProbe.psm1` | 211 | `CreateFile`/`NtQueryObject`, NULL-DACL writer, token dump |
| `scripts/windows-access-check/Invoke-AdgAccessOracle.ps1` | 113 | Emits the matrix, asks Windows, writes the fixture |
| `scripts/windows-access-check/Invoke-AdgRealAccessProbe.ps1` | 227 | Builds real directories and measures them |
| `scripts/windows-access-check/README.md` | 155 | Prerequisites, why Authz, the elevated tier that was not built |
| `docs/architecture/effective-access-limits.md` | — | What the answer does not cover |
| `docs/architecture/effective-access-performance.md` | — | Measured cost |

### Changed

| File | Change |
| --- | --- |
| `backend/app/access_engine/conditions.py` | `SUBJECT_DISABLED` added; `NTFS_ACL_NOT_OBSERVED` added to `UNDERSTATING_CONDITIONS`; `SUBJECT_DISABLED` to `OVERSTATING_CONDITIONS`; descriptions and rationale |
| `backend/app/access_engine/resolver.py` | `_ntfs_findings` suppresses `EMPTY_DACL` for an unobserved descriptor. Nothing else changed |
| `backend/app/access_engine/subjects.py` | `SubjectFacts.enabled`; `build_token` raises `SUBJECT_DISABLED` |
| `backend/app/services/access.py` | `_subject_facts` passes `record.enabled` through. One line |
| `docs/architecture/effective-access.md` | Section 11 added, linking forward. No existing text altered |

**No Phase 0A/0B contract was modified.** No JSON Schema, no observation model, no collector
payload, no migration, and no response model of any earlier phase.

## Important architecture decisions

1. **Windows is the oracle, not a second implementation of the access check.** Writing a
   Python reference model and comparing the two would mostly measure whether the same person
   made the same mistake twice. Every ACL case renders to SDDL and is answered by
   `AuthzAccessCheck`.
2. **`AuthzAccessCheck` over a synthetic descriptor, not `Set-Acl` on a real file.** A
   descriptor supplied as bytes reaches DACL shapes no API will write — including a
   non-canonical order, which `Set-Acl` silently corrects. That order is load-bearing (ADR-0010)
   and could not otherwise be tested at all. **Measured: Windows honors the stored order; an
   Allow ahead of a Deny wins.** Phase 4B asserted this; it is now a measurement.
3. **The token is supplied to both sides.** `AUTHZ_SKIP_TOKEN_GROUPS` plus
   `AuthzAddSidsToContext` build a context holding exactly the SIDs the case declares, so the
   comparison is about the access check and not about who agrees on group membership.
4. **Generic rights are mapped before the check**, with Windows' own `MapGenericMask`, because
   that is what the file system does at descriptor-set time. Authz expands nothing itself and
   an unmapped `GENERIC_ALL` grants **nothing at all**; skipping this would have made every
   generic case look like a bug. The four mapped values are now measured rather than quoted.
5. **The oracle is committed.** The comparison runs on every machine and in CI rather than
   becoming an opt-in step nobody runs. Each answer carries a fingerprint of the inputs it
   answered, and a matrix edited without re-running the probe is a hard failure, not a skip: a
   stale oracle reports a pass.
6. **Invariants, not expected values, above the access check.** Windows has no opinion about
   ADG's certainty accounting, so the resolver is held to properties — no invented rights,
   remote never exceeds local, an unread descriptor is never certain. Mutation-tested: changing
   the layer crossing from intersection to union fails six of them.
7. **The Cartesian-product guard is structural.** Every access route is anchored to one
   principal or one resource in its path, so the product is unreachable rather than merely
   capped, and the test reads the OpenAPI document — a behavioral test can only find the
   unbounded route somebody thought to call.
8. **Statement counts are asserted; milliseconds are recorded.** Following the convention
   `test_query_cost.py` and the Phase 3C benchmarks established.

## Schemas and contracts

**One additive enum value**, no migration, no schema change.

`AccessCondition.SUBJECT_DISABLED` is new. `AccessCondition`'s values are a wire contract —
clients branch on them — and adding one is additive, as the Phase 4B handoff records. A client
that does not know it will ignore it.

`SubjectFacts` gained an optional `enabled` field, defaulting to `None`. Every existing caller
compiles and behaves identically.

**One behavior change visible on the wire:** an answer about a resource whose DACL was never
observed now reports `certainty: at_least` instead of `certainty: certain`, and no longer
reports `empty_dacl`. Both are corrections. A consumer that was branching on the old value was
branching on a wrong one.

## Tests run and exact results

All commands from `C:\code\adg\backend` using `.venv\Scripts\python.exe`.

| Gate | Command | Result |
| --- | --- | --- |
| Matrix invariants | `pytest tests/access_engine/test_matrix.py -q` | **32 passed in 0.56s** |
| Windows comparison | `pytest tests/validation/test_windows_oracle.py -q` | **34 passed in 0.40s** |
| API bounds | `pytest tests/api/test_access_bounds.py -q` | **9 passed in 0.20s** |
| Benchmark harness | `pytest tests/benchmarks/test_access_benchmark_harness.py -q` | **15 passed in 0.04s** |
| Access performance | `ADG_RUN_SMOKE_TESTS=1 pytest tests/db/test_access_performance.py -q` | **16 passed, 1 xfailed in 14.27s** |
| Hermetic suite | `pytest tests -q -m "not smoke"` | **3731 passed, 10 skipped, 323 deselected in 15.94s** |
| Full suite | `ADG_RUN_SMOKE_TESTS=1 pytest tests -q` | **4053 passed, 10 skipped, 1 xfailed in 159.17s** |
| Lint | `scripts\backend-lint.ps1` | **Backend checks passed** — ruff, ruff format, mypy strict, 140 files |

Before this phase the hermetic suite stood at 3,641 and the full suite at 3,947.

### The Windows comparison, in numbers

| | Cases |
| --- | ---: |
| Generated | 5,631 |
| Answerable by Windows | 5,628 |
| **Agreeing exactly** | **5,626** |
| Diverging (NULL-DACL mask, §3 of the limits doc) | 2 |
| Unanswerable (descriptor with no owner) | 3 |

Captured in **0.64 s** on Windows 11 Pro 10.0.26200, PowerShell 7.6.6, unelevated, not
domain-joined.

### The invariants were mutation-tested

Changing the layer crossing in `rights.py` from `&` to `|` fails six invariants, including
soundness and remote-never-exceeds-local. Restored; all 32 pass.

## Known limitations

1. **The share layer was not verified against a live SMB share.** These probes cover the NTFS
   access check. The share layer is the same check over the share's descriptor and the crossing
   is an intersection pinned by Phase 4A, but no measurement here involves `srvsvc`. Doing it
   needs local accounts and shares, which needs elevation — see the next item.
2. **The elevated tier was not built.** `scripts/windows-access-check/README.md` documents
   exactly what it would need (`New-LocalUser`, `New-LocalGroup`, `New-SmbShare`,
   `Grant-SmbShareAccess`, a connection as the created account, and cleanup — all Local
   Administrator). The session that ran this phase was unelevated, and rather than ship an
   untested script, the phase built the unelevated harness that could actually be run and
   verified. That harness turned out to cover the NTFS access check completely, which is the
   larger half.
3. **Deny-only SIDs are not modeled.** A filtered administrator token carries
   `BUILTIN\Administrators` with `SE_GROUP_USE_FOR_DENY_ONLY`, matching Deny ACEs and never
   Allow ACEs. ADG treats every token SID as enabled, so **for a member of a local
   administrators group it over-reports.**
4. **Privileges are still unmodeled.** `SeBackupPrivilege` bypasses the DACL entirely. A backup
   operator with no ACE anywhere is reported as having no access, with no condition saying so.
   Unchanged from 4B and still the largest gap in the product.
5. **`resource -> principals` cost follows the estate** — finding 3, pinned as a strict `xfail`.
6. **The access endpoints do not echo the effective traversal limits**, unlike the graph
   endpoints. Not a safety gap: the clamp happens regardless and a truncated traversal sets
   `membership_complete = false`. It is an inconsistency between two families of endpoints.
7. **The oracle fixture is 1.1 MB.** Regenerable in under a second and diff-readable, but it is
   the largest file in the repository.
8. **The matrix is a structured product, not exhaustive.** 5,631 cases is a deliberate slice:
   full cross-product over every dimension would be hundreds of thousands of cases whose extra
   cost buys nothing, because the crossing does not care which of thirteen masks produced a
   given set of rights.
9. **ADG deliberately differs from Authz on two ACE masks** — `MAXIMUM_ALLOWED` and
   `ACCESS_SYSTEM_SECURITY`. Both are documented with rationale in §5 of the limits doc, and
   neither appears in the 5,626 because the matrix uses masks a real ACL carries.

## Security and privilege assumptions

- **No new privilege, no new collection, nothing written to a target.** The harness creates
  directories only under the caller's own `TEMP` and removes them. It needs no elevation, no
  domain membership, and no share.
- **The harness creates no accounts and stores no credentials.** That is the main reason the
  elevated tier was left unbuilt rather than half-built: a script that creates local accounts
  with known passwords is one somebody will eventually run on a machine that matters.
- **Both fixes err toward *not* narrowing**, consistent with ADR-0011. The unobserved-DACL fix
  turns a false "no access" into an explicit lower bound. `SUBJECT_DISABLED` still reports the
  rights and qualifies them, rather than suppressing a stale-permission finding.
- **The endpoints remain read-only and unauthenticated until Phase 6**, and their output is a
  reconnaissance map of the estate. Unchanged, restated because this phase measured how quickly
  that map can be produced.

## Migration and compatibility notes

- **No migration.** No table, column, index or schema change.
- **`AccessCondition.SUBJECT_DISABLED` is additive.** Renaming a condition would be breaking;
  adding one is not.
- **Two corrected answers.** A resource with an unobserved DACL now reports
  `certainty: at_least` and no longer reports `empty_dacl`. Anything persisting effective-access
  results from before this phase holds `certainty: certain` values for unscanned paths that
  were never true; they should be recomputed rather than migrated, since the stored mask was
  right and only its qualification was wrong.
- **`SubjectFacts` gained an optional field** with a default. No caller needs changing.

## Prerequisites for the next prompt

**For Phase 5 (risk):**

1. `certainty: at_least` now genuinely covers unscanned paths. A rule that lists "resources
   nobody can reach" must exclude them or it will report coverage gaps as clean results — which
   is what it would have done silently before this phase.
2. Read `SUBJECT_DISABLED` to separate *stale* access from *live* access. A disabled account
   with Full Control is a high-value finding and a different one from an enabled account with
   the same rights.
3. **Do not loop `effective_access` over many resources.** Finding 3 measures what that costs.
   A rule needing the same answer across an estate wants a query shape none of the three
   endpoints has; add it deliberately.
4. Deny-only SIDs (limitation 3) mean administrator-group members are over-reported. A rule
   that flags "too many people have Full Control" will over-count on any server whose local
   administrators group is on the ACL.

**For Phase 6 (UI/auth):**

5. Render `certainty: at_least` distinctly. After this phase it is the value an unscanned
   directory carries, and "we have not looked here" is a different thing on screen from "nobody
   has access".
6. `page.total` is present on `resource -> principals` and absent on the other two. That
   asymmetry is finding 3 showing through the API; do not build a UI that depends on a total
   from the other two.

**For whoever builds the elevated tier:**

7. `scripts/windows-access-check/README.md` has the privilege table and the reason each step
   needs it. Run it on a throwaway VM only.
8. The most valuable case it would add is not another NTFS shape — those are covered — but a
   **real SMB connection**, which would verify the share-layer crossing end to end and would
   settle whether `Everyone` and `Authenticated Users` behave over the wire as the constructed
   token assumes.

**For the collectors, when next touched:**

9. Collecting `enabled` for every principal is now load-bearing: `SUBJECT_DISABLED` fires only
   where a run recorded it, so a collector that omits it silently keeps disabled accounts
   indistinguishable.
10. Populating `inherited_from` remains the single highest-value collector change for this
    engine, unchanged from Phase 4B.

## Commit

`a68d3ca` — *Phase 4C: the effective-access engine, checked against Windows*. Not pushed; no
remote is configured for this repository.

## `git status --short`

Taken after the commit. `backend/tests/contracts/test_smb_collector.py` is a formatting change
that was already in the working tree when this phase started; it was **not** staged, exactly as
Phase 4B left it.

```
 M backend/tests/contracts/test_smb_collector.py
```
