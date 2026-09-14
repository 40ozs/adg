# The NTFS ACL normal form and `acl_hash`

**Phase 3A.** Normative for both implementations: `backend/app/domain/acl_hash.py` (Python)
and `Get-AdgNormalizedAcl` in `collector/powershell/ntfs/functions/AdgNtfsObservation.ps1`
(PowerShell). `backend/tests/contracts/test_ntfs_collector.py` runs the collector and
compares the two over a real DACL; changing either side alone fails that test.

See [ADR-0008](../decisions/0008-acl-normal-form-and-hash.md) for why the hash exists and
what it deliberately excludes.

**The digest is never compared parent-to-child.** A perfectly inheriting child has a
different digest from its parent, because Windows sets the `INHERITED` bit on every entry it
copies down — so a tree scan compares a resource against the DACL its parent *projects* onto
a child, a separate derivation over this same normal form.
[ntfs-acl-boundaries.md](ntfs-acl-boundaries.md) specifies it, and is the document to read
before using an `acl_hash` to decide anything about a tree.

---

## 1. The problem

An NTFS scan asks one question millions of times: *is this directory's DACL the same one it
inherited from its parent?* Answering it by comparing ACE lists pairwise is both slow and
fragile. Two readings of one descriptor can differ in

- the order the entries come back in (a rule collection does not promise DACL order);
- the spelling of a SID (`S-1-5-32-544` against `s-1-5-32-544`);
- the width and signedness of an access mask;
- whether the source bothered to report a position at all.

None of those differences mean the permissions differ. So ADG reduces a DACL to one
canonical text document and hashes that. Equal digests mean the same normalized DACL. The
document is reproducible, so a disagreement can be *explained* rather than only reported —
which is why `NormalizedAcl` keeps the text, not just the digest.

---

## 2. The document

```
adg-acl/1
dacl_present=true
dacl_protected=false
order=observed
ace=0|S-1-5-21-1004336348-1177238915-682003330-1202|allow|0x001301bf|0x03
ace=1|S-1-5-32-544|allow|0x001f01ff|0x13
```

* **Encoding.** UTF-8, no byte-order mark. Every line is terminated with `\n`, **including
  the last**: a document is a sequence of complete records, so appending an entry cannot
  change the bytes of the ones before it.
* **Line 1** is the format version, `adg-acl/1`. A change of format changes this token, so
  two digests produced by different formats can never be compared as though they agreed.
* **Lines 2–3** are the descriptor-level facts that the ACE list cannot carry.
  `dacl_present=false` is a NULL DACL — everyone has full access — and it must carry no
  entries. A *present but empty* DACL grants nobody access. Those are opposite facts, and
  normalizing them to the same document would invert the answer.
* **Line 4** is `order=observed` when every entry carried an `order_index`, and
  `order=unordered` otherwise (see §4).
* **Each remaining line** is one ACE:
  `ace=<rank>|<trustee SID>|<allow|deny>|0x%08x mask|0x%02x flags`.
  The SID is canonical (upper-case `S`, no leading zeros). The mask is the raw unsigned
  32-bit value, generic bits and unrecognized bits included. The flags byte is the raw
  `ACE_HEADER.AceFlags`, again including bits no enumeration names.

`digest = sha256(document).hexdigest()`, lower case, 64 characters.

---

## 3. Ordering, and why the *rank* is used

Windows evaluates a DACL in order. A Deny moved below an Allow grants access that was
previously refused, so **order is part of the ACL's identity** and a hash that called those
two ACLs equal would hide a real change.

What is *not* part of it is the numeric value of `order_index`. Entries are sorted by
`(order_index, content)` and rendered with their **rank** in that sorted sequence, so:

- a collector that numbered around the audit entries it dropped, and
- a collector that numbered only the DACL entries it kept

produce the same digest for the same sequence. The sort key is zero-padded to ten digits so
that an ordinal string comparison orders positions numerically — which is what the
PowerShell mirror has to use, because `Sort-Object` and the default `List.Sort()` compare
strings by the current culture and would otherwise order the same ACL differently on a
machine with a different locale.

Two entries claiming the same position are **refused**. The digest would otherwise depend on
the order the rows happened to be read back in, which is exactly what the normal form exists
to remove.

---

## 4. An unordered reading never collides with an ordered one

When any entry arrives without a position, the document says `order=unordered` and the
entries are sorted by their own content instead. The two modes therefore hash differently
even for identical entries.

That is deliberate. One reading knows the evaluation order and the other does not; treating
them as the same observation would claim knowledge that was never collected. The same
principle governs `smb_ace`'s `right_token`, where a permission level and an access mask are
kept distinct because *which reading was taken* is itself part of the observation.

The NTFS collector always reports positions — it reads an ordered `RawAcl` — so
`order=unordered` is reserved for a source that genuinely cannot say.

---

## 5. What the hash covers, and what it does not

| Fact | In the hash? | Why |
| --- | --- | --- |
| `dacl_present` | yes | NULL and empty DACLs are opposite facts |
| `dacl_protected` | yes | identical entries, one protected: one is a boundary, one inherits |
| trustee SID | yes | who the grant names |
| allow / deny | yes | |
| access mask (raw) | yes | unrecognized bits included: a dropped bit is a grant nobody can see |
| ACE flags byte (raw) | yes | `ObjectInherit` and `ContainerInherit` reach different children |
| evaluation order | yes, as rank | a reordered DACL grants differently |
| **owner** | **no** | see below |
| `inherited_from` | no | Windows's account of where an entry came from, not what it grants |
| the numeric `order_index` | no | §3 |

**The owner is excluded deliberately.** Ownership carries implicit `READ_CONTROL` and
`WRITE_DAC` whatever the DACL says, and it is recorded and reported beside the ACL — but
folding it into the digest would defeat the hash's main use. Every folder under a share root
is legitimately owned by whoever created it while sharing one identical inherited DACL. An
owner-sensitive hash would mark every one of those folders an ACL boundary, which is the
exact opposite of what a boundary is for.

---

## 6. Where the digest comes from, and who checks it

**The collector computes it** from the descriptor it just read, in one piece, and sends it on
the `ntfs_resource` observation (`acl_hash`, contract 1.2, optional and additive).

**The collector omits it entirely when it could not read the whole DACL** — an entry type the
contract cannot express, a trustee with no usable SID. A digest over part of a DACL is
indistinguishable from a digest of all of it, and comparing one to a parent's would answer
the boundary question wrong without ever looking wrong.

**Ingestion verifies it when it can.** `plan_batch` recomputes the digest from the
`ntfs_ace` observations in the same batch, but **only** when the batch carries exactly the
`ace_count` the resource declared. Anything less is a partial view of the DACL, which
normalizes to a different document by construction; treating that as a mismatch would reject
a collector that did nothing wrong but split its batches. The NTFS collector's batcher never
splits a directory's observations, so in practice the check runs on every batch it sends.

A mismatch is a **422**, and nothing in the batch is written. Both statements came from the
same collector about the same descriptor, so one is wrong and there is no way to tell which;
storing either would record an ACL that nobody ever saw. The error carries both digests *and
the normalized document the server hashed*, because "which entry differs" is the question an
operator actually has to answer.

**The API reports both, always.** `GET /api/v1/resources/{path}/acl` returns

```json
"acl_hash": {
  "reported": "…",          // what the collector computed, or null
  "computed": "…",          // what the server derives from the rows it holds
  "agrees": true,           // null when nothing was reported: unknown, not a disagreement
  "algorithm": "sha256",
  "normal_form_version": "adg-acl/1",
  "ordered": true,
  "declared_ace_count": 5,
  "stored_ace_count": 5,
  "ace_count_agrees": true
}
```

Both numbers, never one. They agree in the ordinary case; a disagreement means the stored
entries are not the ones that were hashed — entries lost in transit, a rejected batch, two
collectors describing one path differently — and every one of those is a coverage gap.
Choosing a winner here would bury it.

The `computed` digest is taken over the **whole** DACL regardless of paging: a digest over a
page is not a digest of the ACL, and two clients paging differently must not disagree about
one directory.

---

## 7. Stability rules a future change must not break

Anything that alters the bytes of the document changes every digest ADG has ever stored, so
it is a new format version (`adg-acl/2`), not an edit. In particular:

1. The field order within an `ace=` line is fixed.
2. `0x%08x` for the mask and `0x%02x` for the flags are fixed widths, lower case.
3. The document always ends with a newline.
4. `dacl_present` and `dacl_protected` are rendered `true`/`false`, lower case.
5. Sorting is ordinal, never culture-aware.
6. SIDs are canonicalized before they are rendered. The collector cannot send a
   non-canonical one — `Test-AdgSidString` is case sensitive and refuses `s-1-…` — and the
   backend canonicalizes through `app.domain.Sid`, so the two agree without the PowerShell
   side needing a case-folding rule the Python side would have to mirror exactly.
