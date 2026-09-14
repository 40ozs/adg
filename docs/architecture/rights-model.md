# ADG rights model

How ADG represents access rights, combines them across authorization layers, and derives the
labels an administrator reads. Implemented in `backend/app/access_engine/rights.py`; the
decision behind it is [ADR-0005](../decisions/0005-internal-rights-representation.md).

This document is normative for Phase 4 and later. Where it and the code disagree, the code
plus its tests win, and this document is a bug.

---

## 1. The one rule

**A mask is the truth. A label is a rendering of it.**

Every right ADG knows about lives in an access mask. Categories like `Read`, `Modify`, and
`Full Control` are computed from masks for display, and are never computed *back* into an
authorization decision. Nothing outside `app.access_engine.rights` may read an access-mask
bit, and nothing anywhere may compare rights by label.

The reason is concrete. SMB `Change` and NTFS `Modify` are the same mask, `0x001301BF`,
under two names. A mask of `Modify | WRITE_DAC` shares a name with plain `Modify` but lets its
holder rewrite the DACL. Labels are a lossy projection chosen by a UI; authorization is not.

---

## 2. `RightsMask`

A frozen value object: an unsigned 32-bit integer plus the layer it belongs to.

```python
RightsMask.ntfs(0x001301BF)   # what a file-system DACL permits
RightsMask.smb(0x001200A9)    # what a share ACL permits
RightsMask.effective(...)     # a computed combination; not an ACL
```

Properties that keep information from escaping:

| Property | Meaning |
| --- | --- |
| `value` | The raw mask. Authoritative, never narrowed. |
| `rights` | Recognized bits as `NtfsRight` flags. A **view** — reconstructing a mask from it loses unknown bits. |
| `extended` | `ACCESS_SYSTEM_SECURITY` / `MAXIMUM_ALLOWED`, which are not object rights. |
| `unrecognized_bits` | Bits matching no known right. Reported, never discarded. |
| `has_generic_rights` | Generic bits are still unexpanded. |
| `is_indeterminate` | `MAXIMUM_ALLOWED` is present, so the rights set has no fixed value. |
| `escalation_rights` | `WRITE_DAC` / `WRITE_OWNER`: the self-grant paths. |

### Layers

`RightsLayer` is `SMB_SHARE`, `NTFS`, or `EFFECTIVE`. It is distinct from Phase 0A's
`AclLayer`, which labels a *stored ACE*: an ACE belongs to one of two ACLs, but a computed
result belongs to neither.

Union, intersection, subtraction, and every comparison raise `RightsLayerError` across
layers. Two masks with identical bits and different layers are not equal. `in` refuses a mask
operand outright rather than answering a cross-layer question with a silent `False`.

---

## 3. Normalization

### Generic rights

Collectors store `GENERIC_*` bits verbatim (ADR-0003). The engine expands them at evaluation
time through the file-system generic mapping:

| Generic right | Expands to | Value |
| --- | --- | --- |
| `GENERIC_READ` | `READ_DATA \| READ_EA \| READ_ATTRIBUTES \| READ_CONTROL \| SYNCHRONIZE` | `0x00120089` |
| `GENERIC_WRITE` | `WRITE_DATA \| APPEND_DATA \| WRITE_EA \| WRITE_ATTRIBUTES \| READ_CONTROL \| SYNCHRONIZE` | `0x00120116` |
| `GENERIC_EXECUTE` | `EXECUTE \| READ_ATTRIBUTES \| READ_CONTROL \| SYNCHRONIZE` | `0x001200A0` |
| `GENERIC_ALL` | `FILE_ALL_ACCESS` | `0x001F01FF` |

Expansion clears the generic bits, is idempotent, preserves the layer, and preserves
unrecognized bits. `NormalizedRights` carries the raw and expanded forms together, plus which
generics were expanded, so a report can always show what the descriptor said.

Expansion happens **before** any intersection. Intersecting a raw `GENERIC_ALL`
(`0x10000000`) with a specific mask yields zero and would silently report no access.

### Indeterminate masks

`MAXIMUM_ALLOWED` is a request resolved by Windows at open time against the calling token.
ADG does not have the token, so it reports `indeterminate` rather than computing a value.
Anything derived from such a mask is marked inexact.

### Share levels

`Get-SmbShareAccess` reports one of three levels; the security-descriptor APIs report a mask.
`SmbShareAce` records whichever form its source provided, and both normalize to a share mask:

| Level | Mask | Same bits as |
| --- | --- | --- |
| `Read` | `0x001200A9` | NTFS `Read & Execute` |
| `Change` | `0x001301BF` | NTFS `Modify` |
| `Full` | `0x001F01FF` | NTFS `Full Control` |

The right-hand column is the whole argument for comparing by mask: the vocabularies differ,
the rights do not.

---

## 4. Combining rights

### Within a layer

- `union` — accumulate multiple Allow grants.
- `intersection` — rights present in both.
- `difference` — Deny masking.
- `issubset` / `issuperset` / `isdisjoint` — comparison.
- `union_all` / `intersect_all` — folds. The identity for an empty union is the empty mask;
  for an empty intersection it is the full mask. Both require an explicit `layer`, because a
  rights value with no layer must not exist even briefly.

### Deny

`apply_deny(allowed, denied)` and `resolve_canonical(allows, denies, layer=...)` implement the
**canonical-ACL model**: accumulate every Allow, accumulate every Deny, subtract.

This is exact for a DACL in canonical order — Deny ACEs first — which is what Windows
produces and what the ACL editor maintains.

It is **not** a general evaluation of a non-canonical DACL. Windows walks ACEs in order and
stops once the requested access is fully granted, so an Allow placed before a Deny wins. ADG
deliberately treats Deny as winning regardless of position: it is the conservative reading
for a report about who can reach data, and it means ADG may report *less* access than a
non-canonical ACL actually grants. A non-canonical ACL is a finding in its own right, and
Phase 4B raises it separately rather than relying on this module to model ordering.

Selection — which ACEs name the principal, which apply to this object, which arrive by
inheritance — is Phase 4B's work. This module does the arithmetic on masks a caller has
already chosen.

### Across layers

Access over SMB passes both ACLs, so the effective right is the **intersection**. Local
access does not consult the share ACL at all.

```python
effective_rights(ntfs=..., share=..., path=AccessPath.REMOTE_SMB)  # intersection
effective_rights(ntfs=...,             path=AccessPath.LOCAL)      # NTFS alone
```

Neither path is a default:

- A remote calculation without share rights raises. Treating a missing share ACL as
  unrestricted would over-report access.
- A local calculation *with* share rights raises. It would imply a restriction Windows does
  not apply.

`EffectiveRights` retains both inputs, so a narrowing can be explained rather than merely
stated. "NTFS grants Modify but the share grants Read, so the user has Read" sends an
administrator to the right ACL; "the user has Read" sends them to the wrong one.
`limited_by_share` and `limited_by_ntfs` say which layer removed something.

A restrictive share is never a substitute for NTFS permissions: anyone who can log on to the
server bypasses it entirely. `test_a_restrictive_share_does_not_constrain_local_access` pins
this.

---

## 5. How a label is derived

This is the exact algorithm in `summarize()`. It runs on any layer.

### Step 1 — expand generics

So that `GENERIC_ALL` is described as `Full Control` rather than dismissed as special. The raw
mask is kept on the summary.

### Step 2 — collect every category the mask contains

A category is defined by a **required mask**. A mask reaches a category if and only if it
contains *every* required bit:

```
reached(category)  ⟺  mask & required[category] == required[category]
```

| Category | Required mask | Windows name |
| --- | --- | --- |
| `FULL_CONTROL` | `0x000F01FF` | Full Control |
| `MODIFY` | `0x000301BF` | Modify |
| `READ_EXECUTE` | `0x000200A9` | Read & Execute |
| `WRITE` | `0x00000116` | Write |
| `READ` | `0x00020089` | Read |
| `TRAVERSE` | `0x00000020` | Traverse |

These are the ACL-editor composites with **one deliberate departure**: `SYNCHRONIZE`
(`0x00100000`) is excluded from every requirement, and ignored when computing excess rights.
It is a wait-handle primitive, not a right over data; it appears in nearly every real ACE; and
letting its absence demote `Full Control` to `Special permissions` would produce noise rather
than information.

`List folder contents` is deliberately **absent**. It is not a distinct mask — it is
`Read & Execute` inherited by containers only — so it is a property of the ACE's inheritance
flags, not of the mask. Representing it here would invent a distinction that does not exist.

### Step 3 — drop implied categories

Keep only the maximal ones. `Full Control` implies `Modify`, and reporting both is noise. Two
categories that are incomparable are both kept: a mask of `Read | Write` reports both, because
neither contains the other.

### Step 4 — pick `primary`

The first surviving category in ladder order (the table above, broadest first). `primary` is a
convenience for a single-column table; any consumer that shows it alone must also show
`is_exact`.

### Step 5 — report what the categories do not cover

```
covered    = union of the surviving categories' required masks
extra_bits = mask & ~covered & ~SYNCHRONIZE
```

`extra_bits` is exposed as `extra_rights` (named flags) and never rounded away. If a mask
reaches no category but is non-empty, `primary` is `SPECIAL`; if it is empty, `NONE`.

### The two properties this guarantees

1. **Display can never grant.** Every assigned category's required mask is a subset of the
   mask, by construction of step 2. A label therefore always denotes rights that are actually
   held.
2. **Display can never lose.** `covered | extra_bits | (mask & SYNCHRONIZE)` reconstructs the
   mask exactly.

Both are checked over all 16,384 combinations of the fourteen file-system rights in
`TestExhaustiveSafetyProperties`, not over a sample.

### Worked examples

Generated from `summarize()`, not written by hand:

| Mask | `primary` | `categories` | `extra_rights` | `label` | Note |
| --- | --- | --- | --- | --- | --- |
| `0x00000000` | `none` | — | — | `No access` | |
| `0x00100000` | `none` | — | — | `No access` | `SYNCHRONIZE` alone is not access |
| `0x001F01FF` | `full_control` | full_control | — | `Full Control` | `FILE_ALL_ACCESS` |
| `0x000F01FF` | `full_control` | full_control | — | `Full Control` | no `SYNCHRONIZE`; not demoted |
| `0x10000000` | `full_control` | full_control | — | `Full Control` | was `GENERIC_ALL` |
| `0x000301BF` | `modify` | modify | — | `Modify` | |
| `0x000701BF` | `modify` | modify | `WRITE_DAC` | `Modify (plus special permissions)` | the escalation case |
| `0x0002019F` | `write` | write, read | — | `Write, Read` | incomparable categories |
| `0x00000020` | `traverse` | traverse | — | `Traverse` | |
| `0x00020000` | `special` | — | `READ_CONTROL` | `Special permissions` | reaches no category |
| `0x00060089` | `read` | read | `WRITE_DAC` | `Read (plus special permissions)` | |
| `0x02020089` | `read` | read | — | `Read (indeterminate: MAXIMUM_ALLOWED)` | `MAXIMUM_ALLOWED` |

Row 4 is why `SYNCHRONIZE` is excluded from the requirements. Row 7 is why a label alone is
never enough: `0x000701BF` is `Modify | WRITE_DAC`, whose holder can rewrite the DACL, and it
renders with `WRITE_DAC` named rather than hidden.

The last row shows the one bit that is in `extra_bits` but not in `special_permission_bits`:
`MAXIMUM_ALLOWED` is retained in the accounting — nothing is dropped — but it is a request
marker rather than a permission, so the label reports it in its own clause instead of calling
it a special permission.

### Share classification

`classify_share_mask()` returns the highest share level a share mask fully contains, or
`None`. `None` is a real answer — a share ACL can carry a mask that is no level at all, and
promoting it to the nearest one would misreport the share. Callers fall back to `summarize()`.
A mask of `Read` plus `WRITE_DATA` classifies as `Read`, not `Change`.

---

## 6. What this layer does not do

Deliberately out of scope for Phase 4A, and owned by Phase 4B or later:

- Resolving a SID to a principal, or expanding group membership.
- Selecting which ACEs apply to a principal or to an object.
- Walking inheritance, honoring `INHERIT_ONLY` / `NO_PROPAGATE_INHERIT`, or stopping at a
  protected DACL.
- True DACL-order evaluation and the non-canonical-ACL finding (see §4).
- Owner implicit rights (`READ_CONTROL` and `WRITE_DAC` are available to the owner regardless
  of the DACL) and `CREATOR OWNER` substitution.
- NULL-DACL and empty-DACL semantics, which `SecurityDescriptorFacts` already represents.
- Conditional ACEs, central access policies, and Dynamic Access Control claims, which are not
  modeled anywhere yet.

The module holds no identity and performs no I/O, which is what makes all of the above
testable without a database, a collector, or a Windows host.

Phase 4B built that layer on top of this one: see
[effective-access.md](effective-access.md), with [ADR-0010](../decisions/0010-effective-access-is-an-access-check.md)
for the access check and the token it is evaluated against. Everything listed above is now
implemented there, and nothing in this module changed to accommodate it.
