# ACL boundaries: where permissions actually change

A file server holds millions of directories and a few thousand permission decisions. Every
directory has an ACL; almost none of them has an ACL anybody chose. The ones that do are
**boundaries** — the places where an administrator broke inheritance, added an entry, or
locked something down — and finding them is what turns a tree scan from an inventory nobody
can read into a report somebody can act on.

This document specifies how ADG decides. The digest it compares is specified in
[ntfs-acl-normalization.md](ntfs-acl-normalization.md); the reasoning behind the rule is
[ADR-0009](../decisions/0009-boundaries-are-derived-from-a-projection.md).

---

## 1. The comparison that does not work

The obvious test — is this directory's `acl_hash` the same as its parent's? — marks **every**
directory a boundary.

Windows sets the `INHERITED` bit (`0x10`) on every entry it copies down. A parent's explicit
ACE carrying `CONTAINER_INHERIT` (`0x02`) arrives at the child as the same ACE with `0x12`.
The two DACLs differ byte for byte *precisely because inheritance worked*:

```text
\\FS01\Finance          S-1-5-32-544  allow  0x001f01ff  flags 0x03   <- explicit here
\\FS01\Finance\Reports  S-1-5-32-544  allow  0x001f01ff  flags 0x13   <- inherited
```

Two different digests, one permission decision. An estate scanned that way reports a hundred
thousand boundaries and hides the forty that matter.

## 2. The comparison that does

A child is compared against what its parent **projects** onto a child — the DACL a freshly
created, unprotected child would have — not against the parent's own DACL.

```text
projection(\\FS01\Finance)  ==  acl_hash(\\FS01\Finance\Reports)   ->  not a boundary
projection(\\FS01\Finance)  !=  acl_hash(\\FS01\Finance\Payroll)   ->  a boundary
```

The projection is a pure function of the parent's entries, implemented twice and pinned
against each other: `app.domain.project_inherited_acl` and `Get-AdgInheritedAceProjection`.

**It reaches a fixed point after one level**, which is what makes the whole scheme work below
depth 1: a cleanly inheriting child and a cleanly inheriting grandchild carry the *identical*
DACL, so one projection compares correctly against every descendant of a uniform subtree.

### The propagation table

Measured on Windows rather than recalled from documentation — each row by creating a
directory carrying that single ACE and reading the raw descriptor of a child directory, a
grandchild, and a child file. `backend/tests/domain/test_inheritance.py` pins it,
`AdgNtfsInheritance.Tests.ps1` mirrors it, and `AdgNtfsRealFileSystem.Tests.ps1` re-measures
it against a live file system on every run.

This is the **single-entry** table: it holds for an ACE whose mask carries no generic bits
and whose trustee Windows does not substitute. Both exceptions are below, and both split one
parent entry into two child entries.

| Parent flags | Child directory receives | Child file receives |
| --- | --- | --- |
| `CI` (0x02) | `0x12` | — |
| `OI` (0x01) | `0x19` | `0x10` |
| `OI\|CI` (0x03) | `0x13` | `0x10` |
| `CI\|IO` (0x0a) | `0x12` | — |
| `OI\|IO` (0x09) | `0x19` | `0x10` |
| `OI\|CI\|IO` (0x0b) | `0x13` | `0x10` |
| `CI\|NP` (0x06) | `0x10` | — |
| `OI\|NP` (0x05) | — | `0x10` |
| `OI\|CI\|NP` (0x07) | `0x10` | `0x10` |
| `OI\|CI\|NP\|IO` (0x0f) | `0x10` | `0x10` |
| `0x00` | — | — |

Three rows are easy to get wrong from memory, and each would be a whole class of false
boundaries:

* **`INHERIT_ONLY` is not a propagation stop.** `CI|IO` ("subfolders only") descends exactly
  as plain `CI` does. The bit says the ACE does not apply to the object *holding* it, which
  is a statement about the parent.
* **An `OBJECT_INHERIT`-only ACE still reaches a child container**, as `OI|IO|INHERITED`
  (`0x19`), so it can carry on down to files — even though it grants nothing on that
  container. Dropping it makes every folder under a "files only" grant a boundary.
* **`NO_PROPAGATE_INHERIT` clears `OI`, `CI`, `NP` and `IO`** from the copy, which is what
  stops the grandchild inheriting.

### An ACE carrying a generic right becomes *two*

This is the rule that is easiest to miss and most expensive to miss. It is not an exotic
case: `0xe0010000` — `GENERIC_READ|GENERIC_WRITE|GENERIC_EXECUTE|DELETE`, the generic form of
Modify — sits on almost every directory Explorer creates, so a projection without this rule
reports **every** such directory as a boundary. The first version of this one did, and it
passed every test whose fixture masks happened to be specific. It was caught by walking a
real tree.

A generic mask is an indirection: Windows cannot apply it to an object without resolving it
through that object's generic mapping. So when it materializes such an ACE onto a child it
writes both halves of what the parent meant:

| Copy | Flags | Mask |
| --- | --- | --- |
| **effective** | `INHERITED`, every inheritance flag cleared | the generic bits **mapped** |
| **propagating** | `INHERITED \| INHERIT_ONLY \| (OI\|CI as before)` | the original, unmapped |

```text
parent    S-1-1-0  0xe0010000  flags 0x0b        (OI|CI|IO, generic)
child     S-1-1-0  0x001301bf  flags 0x10        <- effective: mapped, applies here
          S-1-1-0  0xe0010000  flags 0x1b        <- propagating: unmapped, keeps descending
```

The parent's own DACL holds the same pair once Windows has normalized it, so this is a fixed
point rather than something that grows with depth. A child file receives only the effective
copy: a file has nothing below it to propagate to.

The mapping is the fixed file-system `GENERIC_MAPPING`, and it lives in
`app.domain.map_generic_rights`. **This does not license expanding generic rights anywhere
else.** ADG stores masks exactly as read, because a stored expansion would bake one
interpretation into a fact; the expansion here is never stored and never reported — it
exists so a prediction can be compared against what Windows wrote.

### `CREATOR OWNER` hands down half an entry

`CREATOR OWNER` (`S-1-3-0`) and `CREATOR GROUP` (`S-1-3-1`) split too, and only halfway. The
propagating copy descends with `INHERIT_ONLY` **preserved** — unlike an ordinary trustee,
where it would be cleared because the entry applies to the child — and the effective copy is
an ACE naming *whoever created that child*, which is not a fact about the parent.

So the projection predicts the propagating half exactly and cannot predict the other, and a
directory beneath such a grant reports `acl_differs_from_parent`. That is a true statement
about its DACL and a misleading one about administrative intent; `SUBSTITUTED_TRUSTEES` names
the SIDs so the two cases stay tellable apart.

`OWNER RIGHTS` (`S-1-3-4`) looks like a sibling and is not one. Windows inherits it like any
other trustee and resolves it against the current owner at access time, so it projects
normally — which was measured rather than assumed, because assuming otherwise would have made
every directory under an `OWNER RIGHTS` grant a boundary.

### A file is not a small directory

A file receives the `OBJECT_INHERIT` entries with every inheritance flag stripped. That is a
different document from what a subfolder receives, so a file is compared against the
**object** projection. Comparing it against the container projection would report a boundary
on every file in the estate — which is why `resource_kind` exists (contract 1.3) rather than
being inferred.

## 3. The reasons

`is_acl_boundary` alone is a verdict with no evidence, and a tree scan's entire output is
verdicts. `boundary_reason` says why, and `NULL` — on a resource that is not a boundary — is
the only value meaning *carrying exactly what it inherited*.

The rule is evaluated in this order, which is the order of certainty:

| Order | Reason | Meaning |
| --- | --- | --- |
| 1 | `protected_dacl` | `SE_DACL_PROTECTED`. Refuses inherited entries, so a boundary whatever a projection says. |
| 2 | `null_dacl` | A NULL DACL, which cannot be anything that was inherited. |
| 3 | `share_root` | The directory a share publishes. Its parent lies outside the share. |
| 4 | `scan_root` | The walk started here, below a share root. The parent exists and was not read. |
| 5 | `parent_unreadable` | The parent's DACL, or this one, was not read in full. |
| 6 | `parent_null_dacl` | The parent has a NULL DACL, which projects nothing at all. |
| 7 | `acl_differs_from_parent` | The comparison was made and the DACLs differ. The ordinary finding. |
| — | *(none)* | The comparison was made and they match. |

**Four of the seven mean "nobody knows", and all four report a boundary.** The asymmetry is
deliberate and is the single most important decision in this document: a boundary that is not
really there costs one extra stored ACL, while a boundary reported *false* tells the next
scan it may stop looking — and silently discards every permission change beneath it.

Order matters twice over. Protection is tested first because a protected directory whose
entries happen to match a projection is still a boundary. And `share_root` outranks
`scan_root` because a share root's parent is outside the share rather than merely outside
this run — a permanent fact rather than a property of one scan.

## 4. Checked, not trusted

The collector makes the verdict; the server redoes it. `GET /resources/{path}` reports both,
and resolves nothing:

```json
"boundary": {
  "reported": false,
  "reported_reason": null,
  "computed": false,
  "computed_reason": null,
  "agrees": true,
  "parent_key": "\\\\fs01\\finance",
  "parent_observed": true,
  "projection_available": true,
  "projected_child_acl_hash": "8c1d…",
  "resource_acl_hash": "8c1d…",
  "parent_acl_hash": "0211…",
  "reported_parent_acl_hash": "0211…",
  "parent_acl_hash_agrees": true
}
```

The server finds the parent **by path** (`NtfsResourceRecord.parent_key`, derived, never
stored — ADR-0007), projects the parent's stored ACEs, and compares. Everything it used is on
the wire, so a client can redo the arithmetic from the two ACL responses rather than taking
the verdict on trust.

Three fields need reading carefully:

* **`projected_child_acl_hash` is what the resource is compared against**, and it is not
  `parent_acl_hash`. Those two differ by construction — see §1.
* **`computed: null` means the server could not reach a verdict**, because the parent has not
  been read or has a NULL DACL. Null is unknown, never "no boundary".
* **`agrees: null` means neither side compared anything.** A collector reporting `share_root`,
  `scan_root`, `parent_unreadable` or `parent_null_dacl` never made a comparison, so the
  server knowing more than it did is not the collector having been wrong.

`parent_acl_hash_agrees` answers a different question: was the parent ADG holds the reading
the verdict was made against? `false` does not make the verdict wrong — the parent may simply
have been re-read since — but it does make it **stale**, which is how a boundary that moved
goes unnoticed.

## 5. What is not modelled

This is propagation, not effective access. Nothing here resolves Deny precedence, expands a
generic right, grants the owner implicit rights, or evaluates a conditional ACE. That is the
Phase 4 engine's work, and it consumes these facts rather than replacing them.

Two known gaps, both reported rather than papered over:

* **A directory created beneath a `CREATOR OWNER` grant reports a false boundary**, for the
  reason set out above: Windows adds an ACE naming the creator, and the creator is not a fact
  about the parent.
* **`inherited_from` is still never populated.** The `INHERITED` bit says an entry came from
  above; naming *which* ancestor needs the Win32 `GetInheritanceSource`, or the ancestor
  matching the inheritance algebra above can now support. The column exists and stays null
  rather than carrying a guess.

**Every rule here was measured, and that is a process commitment rather than a boast.** The
propagation table was produced by building directories with one ACE each and reading back
what Windows gave a real child, grandchild, and file.
`collector/powershell/ntfs/tests/AdgNtfsRealFileSystem.Tests.ps1` re-measures all of it on
every run, against a live volume, comparing flags **and** masks. The generic split was found
because the first version of this document was wrong and a real tree said so; a table
transcribed from documentation would have passed every other test in the repository.

## 6. Why this makes a tree scan affordable

The two numbers the collector reports together are `DirectoriesRead` and `UniqueAclHashes`,
and their ratio is the whole economic case. An estate of ten thousand directories with forty
distinct DACLs has forty permission decisions in it. Storing ten thousand ACLs would bury
them; storing forty and the boundaries that introduce them is the report.

`ix_ntfs_resources_boundaries` is a partial index on exactly that minority
(`share_key, resource_key WHERE is_acl_boundary`), because "where do permissions change under
this share" is the question the scan exists to answer and the one an auditor asks first.
