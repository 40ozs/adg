# Handoff — Phase 4A (`phase-04/01-rights-model.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-00b-contracts.md](phase-00b-contracts.md)

> **Sequence note.** This phase was run directly after Phase 0B; phases 1–3 (collectors,
> ingestion, persistence) had not been run when it started. Phase 4A is a pure domain
> module with no dependency on them, so nothing was blocked. See *Prerequisites* for what
> this means for the phases that were skipped.

> **Concurrency note.** Another session was editing this working tree throughout this phase
> (AD/SMB collectors, `app/domain/graph.py`, `app/ingestion/`, `app/repositories/`,
> `app/models/schema.py`, migration `0002_ad_graph`). Nothing in this handoff touches those
> files, and the commit for this phase was scoped to explicit paths. The repo-wide gate
> results below are reported separately from this phase's own results for that reason.

## Scope completed

Built the rights algebra and normalization layer the effective-access engine sits on: a
framework-free module that combines, compares, and renders Windows access masks without ever
losing a distinction that a label would hide.

1. **`backend/app/access_engine/rights.py`** — `RightsMask` and the full algebra: generic-rights
   normalization, SMB level normalization, union / intersection / subtraction / subset
   comparison, the SMB∩NTFS intersection for a declared access path, and display
   summarization.
2. **Layer separation enforced by the type system.** SMB, NTFS, and computed-effective masks
   are distinct; every cross-layer operation raises.
3. **A display-category derivation that is provably safe** — a label can never denote a right
   the mask does not contain, and can never drop one it does.
4. **`docs/architecture/rights-model.md`** — the normative specification, including the exact
   five-step label derivation and a worked-example table generated from the implementation.
5. **[ADR-0005](../decisions/0005-internal-rights-representation.md)** — the decision, its
   seven rejected alternatives, and its compliance checks.
6. **164 unit tests**, including two exhaustive property suites over all 16,384 combinations
   of the fourteen file-system rights.

## Files and modules added or materially changed

### Added

| File | Contents |
| --- | --- |
| `backend/app/access_engine/rights.py` | The whole rights algebra. 956 lines, no I/O, no framework imports. |
| `backend/tests/access_engine/__init__.py` | Test package marker. |
| `backend/tests/access_engine/test_rights.py` | 164 tests across 14 classes. |
| `docs/architecture/rights-model.md` | Normative rights model and label-derivation spec. |
| `docs/decisions/0005-internal-rights-representation.md` | ADR-0005. |
| `docs/handoffs/phase-04a-rights-model.md` | This file. |

### Changed

| File | Change |
| --- | --- |
| `backend/app/access_engine/__init__.py` | Re-exports the public rights API; docstring explains what Phase 4A owns vs. Phase 4B. |
| `docs/decisions/README.md` | ADR-0005 added to the index. |
| `docs/architecture/permission-domain-model.md` | §8 now links to `rights-model.md` and ADR-0005. One paragraph added; no existing text altered. |

**No Phase 0A or 0B contract was modified.** `app/domain/access.py`, the v1 JSON Schemas, and
the pydantic contract models are untouched by this phase.

## Public API introduced

```
RightsMask            value object: 32-bit mask + RightsLayer; the unit of rights
RightsLayer           SMB_SHARE | NTFS | EFFECTIVE  (distinct from Phase 0A's AclLayer)
AccessPath            REMOTE_SMB | LOCAL — always explicit, never defaulted
ExtendedRight         ACCESS_SYSTEM_SECURITY, MAXIMUM_ALLOWED
NormalizedRights      raw + expanded mask, which generics were expanded, what was not understood
EffectiveRights       result of crossing layers, retaining both inputs for explanation
RightsCategory        NONE | TRAVERSE | READ | WRITE | READ_EXECUTE | MODIFY | FULL_CONTROL | SPECIAL
RightsSummary         primary + categories + extra_bits + escalation_rights + label
RightsError / RightsLayerError

normalize_mask / normalize_ntfs_mask / normalize_share_mask
normalize_share_permission / normalize_share_ace
union_all / intersect_all / apply_deny / resolve_canonical
effective_rights / summarize / classify_share_mask / category_display_name
```

## Important architecture decisions

Full reasoning in ADR-0005; the load-bearing ones:

1. **The mask is authoritative; a label is a rendering.** Nothing outside this module reads an
   access-mask bit, and nothing anywhere compares rights by label.
2. **Layers are part of the type.** Union, intersection, subtraction, and comparison all raise
   `RightsLayerError` across layers, and two masks with equal bits but different layers are
   not equal. This matters concretely: SMB `Change` and NTFS `Modify` are *the same mask*
   (`0x001301BF`) under two names, so label comparison is structurally wrong.
3. **A category is assigned only by a superset test.** `mask & required == required`. The
   label therefore always denotes a subset of the rights actually held.
4. **Excess rights are reported, never rounded away.** `Modify | WRITE_DAC` renders as
   `Modify (plus special permissions)` with `WRITE_DAC` named in both `extra_rights` and
   `escalation_rights`. This is the escalation case a naive summary deletes.
5. **`SYNCHRONIZE` is excluded from every category requirement.** The one deliberate departure
   from the ACL editor's composites; it is a wait-handle primitive, not a right over data, and
   treating it as required would mark most real ACEs as `Special permissions`.
6. **`List folder contents` is deliberately not a category.** It is `Read & Execute` inherited
   by containers only — a property of the ACE's inheritance flags, not of the mask.
7. **Generic rights are expanded at evaluation time only** (ADR-0003 preserved), and always
   *before* intersection: intersecting a raw `GENERIC_ALL` with specific bits yields zero and
   would silently report no access.
8. **`MAXIMUM_ALLOWED` is reported as indeterminate, never resolved.** Its value depends on the
   calling token, which ADG does not have.
9. **The access path is never inferred.** A remote calculation without share rights raises
   rather than assuming an unrestricted share; a local calculation *with* share rights also
   raises.
10. **`ExtendedRight` is separate from `NtfsRight`** rather than widening the Phase 0A enum,
    because widening it would change what `NtfsAce.unrecognized_bits` reports about
    already-stored observations.

## Schemas and contracts

**None introduced, none changed.** This phase adds no JSON Schema, no API surface, no database
table, and no collector payload. It is internal backend domain logic only.

One duplication was introduced deliberately: `SHARE_LEVEL_MASKS` (algebra) alongside the
existing `SHARE_PERMISSION_MASKS` (observation model). They are pinned equal by
`test_the_algebra_table_matches_the_domain_model_table` so they cannot drift.

## Tests run and exact results

All commands run from `C:\code\adg\backend` using `.venv\Scripts\python.exe`.

### This phase's own gates — all pass

| Gate | Command | Result |
| --- | --- | --- |
| Tests | `pytest tests/access_engine -q` | **164 passed in 0.49s** |
| Lint | `ruff check app/access_engine tests/access_engine` | **All checks passed!** |
| Format | `ruff format --check app/access_engine tests/access_engine` | **4 files already formatted** |
| Types | `mypy app/access_engine tests/access_engine` (strict) | **Success: no issues found in 4 source files** |

### Repository-wide

| Command | Result |
| --- | --- |
| `pytest tests -q -m "not smoke" --ignore=tests/contracts` | **459 passed, 2 deselected** |
| `pytest tests -q -m "not smoke"` (full) | **4 collection errors in `tests/contracts/`** |
| `scripts\backend-lint.ps1` | **Fails** — 4 mypy errors, 3 ruff errors |

The repo-wide failures are **not caused by this phase**, and each was traced:

- The four collection errors are one `json.decoder.JSONDecodeError: Invalid \escape` raised
  while loading `docs/contracts/v1/smb-share-observation.schema.json`, which the concurrent
  session has mid-edit (`git status` shows it modified; this phase did not touch it).
- The mypy errors are in `app/ingestion/service.py` (2), `tests/domain/test_graph.py` (1), and
  `tests/domain/test_graph_scale.py` (1) — all files added by the concurrent session.
- The ruff errors are in the same concurrent-session files.

An earlier full run, taken after the rights module was complete but before the concurrent
session broke the schema, gave **808 passed, 1 skipped, 2 deselected in 6.14s**. Adding this
phase's last three tests makes the expected clean total **811 passed, 1 skipped**.

Nothing in `tests/access_engine/` depends on the contracts fixtures, the database, or a
Windows host.

### What the exhaustive suites actually check

`TestExhaustiveSafetyProperties` enumerates all 2¹⁴ = 16,384 combinations of the fourteen
file-system rights and asserts, for every one:

- every assigned category's required mask is a subset of the mask (**display can never grant**);
- `covered | extra_bits | (mask & SYNCHRONIZE)` reconstructs the mask exactly (**display can
  never lose**);
- extras never overlap the categories;
- the reported categories are pairwise incomparable;
- `primary` is the first reported category, or `SPECIAL`/`NONE`;
- summarization is deterministic, and a label is always produced;
- adding a right never shrinks the covered mask (monotonicity).

`TestExhaustiveLayerProperties` checks over a share × NTFS cross-product that the effective
mask never exceeds either input, and that remote access never exceeds local access.

## Known limitations

1. **Non-canonical DACLs are evaluated conservatively, not faithfully.** `apply_deny` /
   `resolve_canonical` use the canonical-ACL model (accumulate Allow, accumulate Deny,
   subtract). Windows walks ACEs in order and stops once the request is satisfied, so an Allow
   placed *before* a Deny wins. ADG treats Deny as winning regardless of position, which may
   report **less** access than a non-canonical ACL actually grants. Phase 4B must evaluate true
   DACL order and raise the non-canonical ACL as its own finding.
2. **No owner implicit rights.** An owner holds `READ_CONTROL` and `WRITE_DAC` regardless of the
   DACL. Not modeled here; `SecurityDescriptorFacts.owner_sid` exists for Phase 4B.
3. **No `CREATOR OWNER` substitution.**
4. **No inheritance evaluation.** `INHERIT_ONLY`, `NO_PROPAGATE_INHERIT`, and `dacl_protected`
   are represented in Phase 0A but not acted on here.
5. **No NULL-DACL / empty-DACL handling.** `SecurityDescriptorFacts.grants_everyone_full_access`
   and `denies_everyone` carry the facts; converting them to a mask is Phase 4B's.
6. **The generic mapping is the file-system one only.** Correct for files, directories, and
   share ACLs; an AD object ACE would need its own mapping.
7. **Categories are folder-oriented.** A file has no `DELETE_CHILD` and no `List folder
   contents`; the same masks are used for both object kinds.
8. **`TRAVERSE` as a category is an ADG invention.** Windows has no such preset. It exists
   because `EXECUTE`-only grants are common on the path segments above a share target and
   would otherwise all render as `Special permissions`.
9. **No conditional ACEs, central access policies, or DAC claims** — unchanged from Phase 0A.

## Security and privilege assumptions

- **Nothing here touches a target system.** No I/O of any kind; ADR-0004's read-only posture is
  unaffected and no new privilege is required by this phase.
- **The module is deliberately conservative where it is uncertain.** A missing share mask
  raises rather than defaulting to unrestricted; `MAXIMUM_ALLOWED` is reported as indeterminate
  rather than resolved; a mask matching no share level classifies as `None` rather than being
  promoted. Every one of these defaults errs toward *not* claiming access is narrower than it
  is.
- **The one place ADG may under-report** is the non-canonical-DACL case in limitation 1. It is
  recorded in ADR-0005 and must be closed in Phase 4B.
- **Escalation rights are structurally visible.** `RightsSummary.escalation_rights` surfaces
  `WRITE_DAC` / `WRITE_OWNER` even when the label reads `Modify`, so a risk rule cannot miss
  them by reading the label.

## Migration and compatibility notes

- **No migration.** No database change, no schema version bump, no stored data affected.
- **No breaking change.** Everything added is new; the only edits to existing files are the
  `access_engine/__init__.py` re-exports and two documentation cross-links.
- `RightsLayer` is additive alongside `AclLayer`, with `RightsLayer.from_acl_layer()` bridging
  them. `AclLayer` keeps its Phase 0A meaning.
- Any later phase that persists an effective-access result must store **the mask and the
  layer**, never the label. The label is recomputed for display.

## Prerequisites for the next prompt

**For Phase 4B (the resolver), which this module was shaped for:**

1. Consume `RightsMask` — do not reintroduce bare integer masks, and do not add a second
   place that interprets an access-mask bit.
2. Supply the selection logic this module deliberately omits: SID resolution, transitive group
   expansion over `MembershipEdge` (tolerating cycles), ACE selection by trustee, inheritance
   walking with the propagation flags, and stopping at a protected DACL.
3. Evaluate true DACL order, and raise a non-canonical-ACL finding where it diverges from
   `resolve_canonical` (limitation 1).
4. Add owner implicit rights and `CREATOR OWNER` substitution (limitations 2–3).
5. Convert NULL-DACL and empty-DACL facts into masks (limitation 5).
6. Call `effective_rights()` with an explicit `AccessPath`; it has no default by design.

**For the skipped phases 1–3, when they are run:**

7. Collectors must keep storing raw masks with generic bits **unexpanded** (ADR-0003 and
   ADR-0005 both depend on this). Nothing in Phase 4A changes what a collector reports.
8. The persistence layer must store the access mask as an integer plus a layer discriminator.
   It must not store a `RightsCategory`.
9. Whoever fixes `docs/contracts/v1/smb-share-observation.schema.json` should re-run the full
   suite; `tests/contracts/` is currently uncollectable for that unrelated reason.

## `git status --short`

Taken at the end of this phase, before the commit. Entries marked ✓ belong to this phase;
the rest belong to the concurrent session and were **not** staged or committed here.

```
 M backend/app/access_engine/__init__.py          ✓
 M backend/app/contracts/v1/observations.py
 M backend/app/domain/__init__.py
 M database/migrations/env.py
 M docs/architecture/permission-domain-model.md   ✓
 M docs/contracts/collector-protocol.md
 M docs/contracts/v1/smb-share-observation.schema.json
 M docs/decisions/README.md                       ✓
?? backend/app/access_engine/rights.py            ✓
?? backend/app/domain/graph.py
?? backend/app/ingestion/
?? backend/app/models/schema.py
?? backend/app/repositories/
?? backend/tests/access_engine/                   ✓
?? backend/tests/domain/test_graph.py
?? backend/tests/domain/test_graph_scale.py
?? backend/tests/support/
?? collector/powershell/ad/
?? collector/powershell/common/
?? collector/powershell/smb/
?? collector/powershell/tests/
?? database/migrations/versions/0002_ad_graph.py
?? docs/architecture/rights-model.md              ✓
?? docs/decisions/0005-internal-rights-representation.md ✓
```
