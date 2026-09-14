# ADR-0005: Rights are layer-tagged bitmasks; labels are derived and never authoritative

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 4A — Canonical SMB and NTFS rights model
- **Deciders:** ADG project

## Context

Every phase after this one needs to answer some version of "what can this principal do to
this object?" The answer has to survive being aggregated across ACEs, intersected across two
authorization layers, compared between a current and a previous scan, filtered in a query,
and finally shown to an administrator in a table. Each of those steps is an opportunity to
lose information, and the information that gets lost is always the same kind: the difference
between two rights sets that happen to share a friendly name.

Three constraints shaped the decision.

**Windows access masks do not partition into friendly categories.** `Read`, `Modify`, and
`Full Control` are presets in the ACL editor, not a classification of the mask space. A
32-bit mask has 2³² values; the editor names six of them. Real ACLs routinely contain masks
that are none of the presets — a grant of `Modify` plus `WRITE_DAC`, a traverse-only grant of
`EXECUTE | READ_ATTRIBUTES`, a mask carrying `GENERIC_ALL` instead of the expanded rights, a
mask with bits from a future Windows version that this build does not recognize. A
representation that can only hold the six presets must round every other mask to one of
them, and rounding an authorization value is how a report comes to understate access.

**SMB and NTFS are separate layers whose labels collide.** Access over a share is limited by
both ACLs, and the effective right is the intersection. The two layers use different
vocabularies for the same bits: share `Change` and NTFS `Modify` are *the same mask*,
`0x001301BF`. Meanwhile share `Full` and NTFS `Full Control` are also the same mask. Any code
that compares the layers by label is comparing strings that were chosen by two different
Windows UI teams, and will produce a wrong intersection the moment the vocabularies diverge —
which they already have.

**The escalation case is exactly the case a label hides.** A principal holding
`Modify | WRITE_DAC` can rewrite the DACL and grant themselves `Full Control`. Summarized to
a label, that grant reads `Modify` and looks ordinary. It is the single most important
finding an access audit can produce, and the naive design deletes it.

ADR-0003 already separates raw observations from derived state, and Phase 0A stores access
masks verbatim with generic bits unexpanded. This ADR decides what the *derived* side looks
like.

## Decision

Rights are represented internally as layer-tagged 32-bit masks. Display categories are
derived from masks by a subset test, and are never an input to any authorization
calculation.

1. **`RightsMask` is the unit of rights.** A frozen value object in
   `backend/app/access_engine/rights.py` wrapping an unsigned 32-bit integer plus a
   `RightsLayer`. It is the only type in which rights may be stored, combined, compared, or
   passed between modules. No function outside this module interprets an access mask.

2. **The mask is never narrowed to fit a name.** Unrecognized bits are retained in the value
   and reported through `unrecognized_bits`. Generic bits are expanded through the
   file-system `GENERIC_MAPPING` at evaluation time only, never at collection time, and the
   raw and expanded forms are carried together in `NormalizedRights` so a report can always
   show what the descriptor actually said. `MAXIMUM_ALLOWED` is reported as *indeterminate*
   rather than resolved to a guess.

3. **Layers are part of the type, and do not mix implicitly.** `RightsLayer` is
   `SMB_SHARE`, `NTFS`, or `EFFECTIVE`. Union, intersection, subtraction, and every
   comparison raise `RightsLayerError` across layers, and two masks with equal bits but
   different layers are not equal. The only way to cross layers is `effective_rights()`,
   which takes an explicit `AccessPath` and produces an `EFFECTIVE` mask. `RightsLayer` is
   deliberately a separate enum from Phase 0A's `AclLayer`: an ACE belongs to one of two
   ACLs, but a computed result belongs to neither.

4. **The access path is always explicit.** `AccessPath.REMOTE_SMB` intersects the share and
   NTFS masks; `AccessPath.LOCAL` uses NTFS alone. Neither is a default. Omitting the share
   mask for a remote calculation raises rather than assuming an unrestricted share, because
   that assumption over-reports access. Supplying one for a local calculation also raises,
   because it implies a restriction Windows does not apply.

5. **A category is defined by a required mask, and is assigned only by a superset test.**
   `CATEGORY_REQUIRED_MASKS` gives the bits a mask must contain to be described by each
   category. A label therefore always denotes a *subset* of the rights actually held, and can
   never imply access that is not present. Rights in excess of the assigned categories are
   reported as `extra_bits` rather than rounded away, so `Modify | WRITE_DAC` renders as
   "Modify (plus special permissions)" with `WRITE_DAC` named in `extra_rights` and in
   `escalation_rights`.

6. **A mask that maps to no category is a first-class result.** `RightsCategory.SPECIAL` and
   `RightsCategory.NONE` exist for it, and `classify_share_mask()` returns `None` rather than
   promoting a mask to the nearest share level.

7. **`SYNCHRONIZE` is excluded from every category requirement** and ignored when computing
   excess rights. It is a wait-handle primitive rather than a right over data, it is present
   in nearly every real ACE, and letting its absence demote `Full Control` to
   `Special permissions` would produce noise rather than information. This is the one
   deliberate departure from the ACL editor's composites, and it is the only one.

8. **Deny is applied by subtraction under a declared resolver model.** `apply_deny()` and
   `resolve_canonical()` accumulate Allow masks, accumulate Deny masks, and subtract. This is
   exact for a canonical DACL and conservative for a non-canonical one; see *Consequences*.

9. **Rights math is framework-free and holds no identity.** The module imports nothing from
   FastAPI, SQLAlchemy, or the collectors, performs no I/O, and never resolves a SID, expands
   a group, or walks a directory tree. Which ACEs apply to a principal is the Phase 4B
   resolver's question; this module answers only what a set of already-selected masks means.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Store an enum of `Read` / `Modify` / `Full Control` as the rights type | Cannot represent the masks that are none of those, which is most of the real ones. Silently rounds `Modify + WRITE_DAC` to `Modify` and deletes the escalation finding. |
| A bare `int` for the mask, with helper functions | Nothing prevents intersecting a share mask with an NTFS mask, or comparing an effective result against an ACL value. The layer bug this module exists to prevent would be one keystroke away and invisible in review. |
| A `set[NtfsRight]` instead of a bitmask | Cannot hold unrecognized bits at all, so any right ADG does not yet know about is dropped on the first round-trip. Also diverges from the on-the-wire and on-disk representation for no benefit. |
| Expand generic rights in the collector | Bakes one interpretation of `GENERIC_MAPPING` into stored facts, violating ADR-0003, and makes the stored data unreconcilable against the Windows ACL editor. |
| Let the category be the stored value and the mask a detail | Inverts the dependency: every query, diff, and risk rule would then be comparing labels, and the intersection of SMB and NTFS would be undefined. |
| Model "List folder contents" as a category | It is not a distinct mask. It is `Read & Execute` inherited by containers only, so it is a property of the ACE's inheritance flags. A mask-level category would invent a distinction that does not exist. |
| Treat a missing `SYNCHRONIZE` bit as demoting the category | Technically faithful, practically useless: it would mark a large fraction of ordinary ACEs as `Special permissions` and train operators to ignore the column. |
| Resolve `MAXIMUM_ALLOWED` to the rights the rest of the descriptor grants | That resolution depends on the requesting token, which ADG does not have. Reporting a computed value would be a fabrication. |

## Consequences

**Positive**

- A rights set that matches no preset is representable, comparable, and displayable without
  loss, which is the acceptance criterion this phase turns on.
- `Modify | WRITE_DAC` cannot be silently reported as `Modify`; the excess rights and the
  escalation rights are both carried on the summary.
- SMB and NTFS cannot be compared by label, because the type system refuses the operation.
  The share/NTFS intersection has exactly one implementation.
- Display logic is provably safe: a category's required mask is a subset of the mask by
  construction, verified exhaustively rather than by example.
- The engine is unit-testable with no database, no collector, and no Windows host.

**Negative / accepted costs**

- Callers must say which layer a mask belongs to, and must say which access path they are
  asking about. This is more verbose than passing an integer, deliberately.
- Two tables of share-level masks now exist — `SHARE_PERMISSION_MASKS` next to the
  observation types, `SHARE_LEVEL_MASKS` next to the algebra. A test asserts they are equal
  so they cannot drift.
- `ExtendedRight` defines `ACCESS_SYSTEM_SECURITY` and `MAXIMUM_ALLOWED` separately from
  `NtfsRight` rather than widening the Phase 0A enum, because widening it would change what
  `NtfsAce.unrecognized_bits` reports about already-stored observations. The two notions of
  "unrecognized" are therefore slightly different, and the docstrings say so.
- The canonical-ACL Deny model diverges from Windows for a **non-canonical** DACL, where
  Windows stops at the first ACE satisfying the request and an Allow placed before a Deny
  wins. ADG treats Deny as winning regardless of position. This is the conservative reading
  for a report about who can reach data, but it means ADG may report *less* access than a
  non-canonical ACL actually grants. A non-canonical ACL is itself a finding, and Phase 4B
  must raise it separately rather than relying on this module to model the ordering.

**Follow-up required**

- Phase 4B: principal resolution, group expansion, ACE selection, inheritance walking, and
  true DACL-order evaluation with a divergence finding for non-canonical ACLs.
- Phase 4B: owner implicit rights (`READ_CONTROL` and `WRITE_DAC` are available to the owner
  regardless of the DACL) and `CREATOR OWNER` substitution.
- Any phase that persists an effective-access result must store the mask and the layer, not
  the label; the label is recomputed for display.
- Risk rules (Phase 7+) must test `escalation_rights` and `extra_rights`, not the label.

## Compliance

A reviewer can verify this decision is still honored by these checks, all in
`backend/tests/access_engine/test_rights.py`:

- `TestExhaustiveSafetyProperties::test_a_category_never_grants_a_right_the_mask_lacks`
  checks the subset property over all 16,384 combinations of the fourteen file-system
  rights. If display logic were ever allowed to inflate a label, this fails.
- `test_no_right_is_lost_between_the_categories_and_the_extras` reconstructs each mask from
  its summary, so a right can never be dropped during summarization.
- `TestLayerSeparation` asserts that every cross-layer operation raises.
- `TestExhaustiveLayerProperties::test_the_effective_mask_never_exceeds_either_layer` checks
  the intersection property across the share/NTFS cross-product.
- `test_the_algebra_table_matches_the_domain_model_table` pins the two share-mask tables
  together.

A review should reject: any comparison of rights by string label; any function outside
`app.access_engine.rights` that reads an access mask bit; any storage of a
`RightsCategory` as authorization truth; any call to `effective_rights()` whose access path
is inferred rather than passed; and any expansion of generic rights inside a collector.
