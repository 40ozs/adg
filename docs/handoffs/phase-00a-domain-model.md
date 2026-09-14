# Handoff — Phase 0A (`phase-00/01-domain-model.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [bootstrap.md](bootstrap.md)

## Scope completed

Defined ADG's canonical permission domain semantics and implemented them as typed, tested,
framework-free Python. No permission-resolution algorithm was written; that is Phase 4.

1. `docs/architecture/permission-domain-model.md` — the canonical semantics: principles,
   entity table, SID normalization/uniqueness, membership as an edge graph, the two
   authorization layers, the Windows-authorization checklist, resource identity and path
   normalization, provenance, known gaps, and a worked "who has access and why" example.
2. Four ADRs (0001–0004) with alternatives, consequences, and compliance checks, indexed in
   `docs/decisions/README.md`.
3. `backend/app/domain/` — typed domain classes and enums for every required entity.
4. `backend/tests/domain/` — 203 unit tests covering normalization and invariants,
   including an executable version of the worked example.

## Files and modules added

### Documentation

| File | Contents |
| --- | --- |
| `docs/architecture/permission-domain-model.md` | The domain model (12 sections). |
| `docs/decisions/0001-sid-as-identity.md` | SID as canonical identity. |
| `docs/decisions/0002-graph-preserving-membership.md` | Membership stored as edges. |
| `docs/decisions/0003-raw-observations-vs-derived-state.md` | Raw facts vs derived state. |
| `docs/decisions/0004-read-only-collector-posture.md` | Read-only, least-privileged collectors. |
| `docs/decisions/README.md` | ADR index updated with the four records. |
| `docs/architecture/system-overview.md` | Identity section now links the domain model and ADR-0001. |

### Backend (`backend/app/domain/`)

| Module | Types |
| --- | --- |
| `errors.py` | `DomainError`, `DomainValidationError` (carries `value` and `field`). |
| `identity.py` | `Sid`, `DomainIdentifier`, `DomainKind`, `Principal`, `User`, `DomainGroup`, `LocalGroup`, `ComputerIdentity`, `WellKnownPrincipal`, `UnresolvedPrincipal`, `PrincipalKind`, `GroupScope`, `GroupType`, `UnresolvedReason`, well-known SID/RID tables. |
| `paths.py` | `UncPath`, `LocalPath`, `parse_unc_path`, `parse_local_path`, `parse_windows_path`. |
| `resources.py` | `Server`, `SmbShare`, `DirectoryResource`, `ShareType`. |
| `access.py` | `NtfsAce`, `SmbShareAce`, `SecurityDescriptorFacts`, `AceType`, `AceSource`, `AceFlag`, `NtfsRight`, `SharePermission`, `AclLayer`, `SHARE_PERMISSION_MASKS`. |
| `membership.py` | `MembershipEdge`, `MembershipEdgeKind`. |
| `observation.py` | `ScanRun`, `ScanStatus`, `Observation[FactT]`, `ObservationSource`, `CollectorKind`. |
| `__init__.py` | Curated public surface with `__all__`. |

### Tests (`backend/tests/domain/`)

`test_sid.py`, `test_paths.py`, `test_principals.py`, `test_access.py`, `test_membership.py`,
`test_observation.py`, `test_resources.py`, `test_worked_example.py`.

## Important architecture decisions

1. **SID as identity, with a host-scoping exception.** BUILTIN SIDs (`S-1-5-32-*`) are
   identical on every Windows computer, so `LocalGroup` and local membership edges key on
   `host_key|sid`. Merging them across servers would invent access nobody has. (ADR-0001)
2. **Membership is edges only.** No expansion function exists anywhere in the domain
   package; `primary_group` is a distinct edge kind because `primaryGroupID` membership is
   absent from a group's `member` attribute and is a classic collection bug. Cycles and
   unresolvable endpoints are storable; only a self-edge is rejected. (ADR-0002)
3. **Raw facts keep everything.** Generic rights are not expanded, unrecognized mask bits
   are exposed rather than dropped, a NULL DACL is recorded as absent (everyone has access)
   and is distinguishable from an empty DACL (nobody does), and unresolved SIDs stay on the
   ACL without a guessed name. `Observation[FactT]` wraps raw facts only. (ADR-0003)
4. **The SMB and NTFS layers are separate types.** `SmbShareAce` and `NtfsAce` cannot be
   interchanged; intersecting them is the Phase 4 engine's job. A share ACE records exactly
   one of `access_mask` or `permission` — whichever the collecting API reported — because
   recording both would fabricate a value.
5. **Path identity is canonical and case-insensitive.** `UncPath`/`LocalPath` normalize
   separators, extended-length prefixes, duplicate separators, `.` segments, and trailing
   separators; they reject `..`, relative paths, and invalid characters rather than guessing.
   They compare *and hash* case-insensitively while preserving the observed spelling for
   display.
6. **Validation is constructor-level and actionable.** Every invariant raises
   `DomainValidationError` naming the offending value and field, so a collector diagnostic is
   readable by an operator.
7. **Unknown is never reported as no.** Tri-state properties (`grants_access`,
   `crosses_domain`, `is_forest_root`) return `None` when a source did not say.

## Schemas and contracts introduced

- No database schema and no API contract. These are in-process domain types only; the JSON
  contracts derived from them are Phase 0B's deliverable.
- Identity-key contracts that later phases must honor:
  - principal → canonical SID string;
  - local group → `host_key|sid` (case-folded host);
  - domain → domain SID;
  - server → case-folded name;
  - share → `server_key|share_name`, case-folded;
  - directory → case-folded canonical UNC path;
  - membership edge → `group_key->member_key|kind`;
  - scan run → UUID.
- `Sid` canonical form is now a stable contract: upper-case `S`, no leading zeros, decimal
  authority below 2³² and `0x`-prefixed upper-case hex above.

## Tests run and exact results

| Command | Result |
| --- | --- |
| `.\scripts\backend-test.ps1` (`pytest -q -m "not smoke"`) | **237 passed, 2 deselected** in 4.31s |
| `.\scripts\backend-test.ps1 -Smoke` (PostgreSQL running) | **239 passed** in 4.38s |
| `python -m pytest tests/domain -q` | **203 passed** in 0.13s |
| `python -W error::SyntaxWarning -m pytest tests/domain -q` | **203 passed**, no warnings |
| `.\scripts\backend-lint.ps1` | `ruff check` **All checks passed**; `ruff format --check` **41 files already formatted**; `mypy app tests` **Success: no issues found in 41 source files** (strict) |
| Documentation link check (all relative Markdown links) | **0 broken links** |

Two defects were found by running the tests rather than by inspection:

1. **Path equality was case-sensitive** while `comparison_key` was not, so
   `\\FS01\Finance` and `\\fs01\finance` were unequal under `==` but identical as keys — the
   exact "one directory, two rows" failure the model exists to prevent. `UncPath` and
   `LocalPath` now compare and hash on `comparison_key`. Pinned by `TestPathEquality`.
2. **A docstring escape** (`\F` in a non-raw string) raised a `SyntaxWarning`. The
   docstring is now raw, and the domain suite is run under `-W error::SyntaxWarning`.

## Known limitations

1. Deliberately absent, per the phase's non-goals: recursive membership expansion, ACL
   precedence and effective-rights calculation, and collectors. `test_worked_example.py`
   asserts that no effective-access symbol exists in the domain package.
2. Gaps recorded in §11 of the domain model: per-directory case sensitivity, trailing dots
   and spaces in path components, DFS namespace resolution, host aliases (`FS01` vs
   `fs01.corp.example.com` remain separate servers until merged on a computer SID), 8.3 short
   names, SACL/conditional ACEs/central access policies/DAC claims, `sIDHistory`, and trusts
   as entities.
3. The domain types are not yet persisted. No SQLAlchemy models or migrations exist for
   them; the mapping is Phase 1's work and may reveal indexing needs the keys above do not
   anticipate.
4. Well-known SID and RID tables are curated, not exhaustive. An unlisted well-known SID
   yields `conventional_name is None`, which is correct but less informative.
5. `SmbShare.unc_path` renders the server from the case-folded `server_key`, so it is not a
   display-faithful spelling. Harmless now that path equality ignores case, but a display
   name for the server should be carried when Phase 2 populates shares.
6. Audit (SACL) entries are modeled nowhere; a collector encountering one must not record it
   as an access ACE.

## Security and privilege assumptions

- Unchanged from bootstrap and now recorded as a decision: ADR-0004 (read-only,
  least-privileged collectors, no Domain Admin, remediation only as a separate authorized
  capability).
- This phase adds no collection, no I/O, and no network access — the domain package imports
  nothing beyond the standard library.
- The model is built so that privilege gaps stay visible: an unreadable object produces a
  `partial` run, and `ScanRun` cannot be `succeeded` with a non-zero `error_count`, so
  incomplete coverage can never be reported as complete access information.
- Unresolved SIDs, NULL DACLs, and `WRITE_DAC`/`WRITE_OWNER` grants are all representable,
  which is what lets later phases report them as findings.

## Migration and compatibility notes

- Additive only. The bootstrap phase's public contracts (`/health/live`, `/health/ready`,
  `/version`, `ADG_*` configuration) are untouched.
- Schema baseline remains `0001_baseline`; no migration was added, because nothing is
  persisted yet.
- The identity-key contracts above are now accepted semantics. Changing one later requires a
  versioned migration plus a new ADR superseding the relevant record.
- `StrEnum` is used for all string enums (Python 3.11+), so enum values serialize as their
  string values — relevant to Phase 0B's JSON contracts.

## Prerequisites for the next prompt (Phase 0B — contracts and test vectors)

1. Read `docs/architecture/permission-domain-model.md` and ADR-0001–0004 before designing any
   payload. The JSON contracts must be a faithful projection of these types, not a second,
   subtly different model.
2. Use the identity keys listed under "Schemas and contracts introduced" as the contract's
   identifiers. In particular, a local group or local membership edge must carry its host.
3. Contracts must preserve raw fidelity: the raw access mask (generic bits included), ACE
   flags, explicit-vs-inherited source, `dacl_present`/`dacl_protected`, and unresolved SIDs.
   A contract that reports "effective rights" is out of scope and violates ADR-0003.
4. A share ACE contract must accept exactly one of `access_mask` or `permission`, matching
   `SmbShareAce`.
5. Timestamps are timezone-aware UTC; a fixture with a naive timestamp must be invalid.
6. Fixtures should exercise the anomalies deliberately: orphaned SIDs, membership cycles,
   `INHERIT_ONLY` and `NO_PROPAGATE` ACEs, generic rights, NULL and empty DACLs, BUILTIN
   groups on two different servers, and cross-forest members.
7. New code must pass `.\scripts\backend-lint.ps1` (ruff + **mypy strict**) and
   `.\scripts\backend-test.ps1`.
8. The development stack is currently stopped; start it with `.\scripts\stack-up.ps1 -DbOnly`
   only if a contract test needs PostgreSQL (it should not).

## `git status --short`

Captured immediately before the phase commit:

```text
?? backend/app/domain/
?? backend/tests/domain/
?? docs/architecture/permission-domain-model.md
?? docs/decisions/0001-sid-as-identity.md
?? docs/decisions/0002-graph-preserving-membership.md
?? docs/decisions/0003-raw-observations-vs-derived-state.md
?? docs/decisions/0004-read-only-collector-posture.md
?? docs/handoffs/phase-00a-domain-model.md
 M docs/architecture/system-overview.md
 M docs/decisions/README.md
```

All of it, this handoff included, went into the phase commit; the working tree is clean
afterwards.
