# Effective access: from observations to an answer

A share-access audit exists to answer one question — *who can reach this data, and how* —
and the way that question is most often answered wrongly is not by miscalculating a mask.
It is by turning something nobody collected into a verdict: an unread share ACL reported as
unrestricted, a group nobody enumerated reported as "not a member", a directory nobody
scanned reported as granting nothing.

This document specifies how ADG resolves effective access, in the order the resolver does
it. The rights algebra it sits on is [rights-model.md](rights-model.md); the decisions
behind it are [ADR-0010](../decisions/0010-effective-access-is-an-access-check.md),
[ADR-0011](../decisions/0011-answers-carry-their-uncertainty.md), and
[ADR-0012](../decisions/0012-bounded-access-queries.md).

**Why** an answer is what it is — the membership and ACE paths that caused it, what each one
is worth, and what removing one would do — is a separate specification:
[access-causality.md](access-causality.md), decided by
[ADR-0013](../decisions/0013-causality-is-measured-not-inferred.md).

---

## 1. The three inputs

| Input | Module | What it supplies |
| --- | --- | --- |
| The **token** | `app.access_engine.subjects` | Which SIDs an ACL is evaluated against |
| The **NTFS DACL** | `app.access_engine.evaluation` | What the file system permits |
| The **share ACL** | `app.access_engine.evaluation` | What the share permits, for remote access only |

The result is their combination for one declared **access path**
(`app.access_engine.resolver`). Nothing infers the path: a remote calculation without the
share layer is refused, and a local one *with* it is refused, because each would report a
restriction Windows does not apply (ADR-0005, decision 9).

---

## 2. The token

Windows never checks an ACL against a user. It checks it against an access token, and a
token holds three kinds of SID:

1. **The subject's own SID.**
2. **Every group it belongs to**, transitively. ADG has these: they are the identity graph,
   walked upward by `app.domain.expand` with the traversal's own limits.
3. **SIDs the logon session contributes** — `Everyone`, `Authenticated Users`, and one
   logon-type SID. ADG has never seen a logon, so it *constructs* these from a named
   assumption and reports every SID it added.

### The assumption is part of the answer

`TokenAssumption` is reported on every response, and so is the list of SIDs it contributed.

| Assumption | Adds | Default for |
| --- | --- | --- |
| `authenticated_user` | `Everyone`, `Authenticated Users`, the path's logon SID | Users, computers, groups, unresolved SIDs |
| `anonymous` | `Everyone`, `ANONYMOUS LOGON`, the path's logon SID | `S-1-5-7` asked about directly |
| `sids_only` | nothing | Well-known SIDs asked about directly |

The logon-type SID follows the access path: `NETWORK` (`S-1-5-2`) over SMB, `INTERACTIVE`
(`S-1-5-4`) locally.

Two of these defaults are worth stating plainly:

* **A well-known SID gets `sids_only`.** Asking what `Everyone` can reach means `Everyone`;
  folding `Authenticated Users` into its token would answer a wider question than the one
  asked, and would report grants anonymous sessions do not get.
* **A group gets `authenticated_user`,** with `SUBJECT_IS_A_GROUP` raised. A group holds no
  token; what is being reported is what an authenticated member of it would have. That is
  the useful question, and it is not the literal one, so it is labelled.

### What is refused rather than guessed

A token also contains SIDs that depend on how the session was established — `BATCH`,
`SERVICE`, `REMOTE INTERACTIVE`, `Local account`, and the authentication-type SIDs. No
collected data settles which applied. An ACE naming one of them is reported as
`LOGON_TYPE_TRUSTEE` and **not** treated as unmatched: "Alice is not in this group" is a
conclusion, and "Alice may or may not have logged on that way" is not.

### Matching is by storage key, never by SID

`S-1-5-32-544` is byte-identical on every Windows computer. `BUILTIN\Administrators` on
FS01 and on FS02 are different groups, and both the token and every ACE trustee carry the
host-scoped key `fs01|S-1-5-32-544` that `app.domain.referenced_principal_key` derives.
Matching on the bare SID would hand every server's local administrators the rights of every
other's.

---

## 3. The access check

For each layer, in the order the descriptor stores its entries:

```text
if not dacl_present:                      # NULL DACL
    return every right, to everybody
if token holds the owner and no OWNER RIGHTS entry applies:
    granted |= READ_CONTROL | WRITE_DAC   # before any ACE is read
for each entry, in stored order:
    if INHERIT_ONLY:            skip      # it describes children, not this object
    if trustee not in token:    skip
    if DENY:   denied  |= mask & ~granted
    else:      granted |= mask & ~denied
```

This is what `AccessCheck` computes when asked for `MAXIMUM_ALLOWED`, which is the audit
question — *what may this token do* — rather than the application question — *may I open
for write*. Generic rights are expanded through the file-system generic mapping before the
comparison; intersecting an unexpanded `GENERIC_ALL` with specific bits yields zero and
would silently report no access.

### Order is load-bearing

Step three is order-sensitive, and deliberately so. `resolve_canonical` (Phase 4A)
implements the canonical model — accumulate every Allow, accumulate every Deny, subtract —
which is exact for a DACL in the order Windows maintains and **wrong** for one that has
been reordered: Windows honors what is stored, so an Allow placed ahead of a Deny wins.

ADG runs the faithful evaluation and reports the canonical result beside it:

* `NON_CANONICAL_DACL` — the ACL departs from canonical order. A fact about the object.
* `ORDER_DEPENDENT_RESULT` — **this subject's** rights differ from what the canonical model
  would compute. A fact about one evaluation, and the one worth escalating: the ACL editor
  and the access check no longer agree about this principal.

This closes Phase 4A's limitation 1.

#### What is *not* reported as non-canonical

An inherited Deny sitting behind an inherited Allow. That is perfectly canonical when the
two came from different ancestors — canonical order is *explicit deny, explicit allow, then
each ancestor's deny and allow in turn* — and ADG does not populate `inherited_from`, so
which ancestor an entry came from is unknown. Reporting it would turn ordinary two-level
inheritance into a false finding on a very large number of directories.

### The four trustees a naive check gets wrong

| Trustee | What Windows does | What ADG reports |
| --- | --- | --- |
| The **owner** | Grants `READ_CONTROL` and `WRITE_DAC` before reading the DACL; an explicit Deny cannot take them away | `OWNER_IMPLICIT_RIGHTS` — an escalation path no ACE shows |
| `OWNER RIGHTS` (`S-1-3-4`) | Resolved against the current owner at access time, and **replaces** the implicit rights | `OWNER_RIGHTS_ACE`; the entry is matched at its real position |
| `CREATOR OWNER` / `CREATOR GROUP` | Placeholders substituted at inheritance time; no token contains them | `CREATOR_OWNER_ACE` when the entry applies to this object — it looks like a grant in every ACL viewer and is inert |
| `INHERIT_ONLY` entries | Describe what children get; grant nothing here | Excluded from the evaluation and from the trustee list |

### NULL versus empty

A descriptor with **no DACL** grants everyone full access. A descriptor whose DACL is
**present and empty** grants nobody anything. The two are opposite, and an ACL viewer shows
an object with no DACL identically to one granting Everyone Full Control. `NULL_DACL` and
`EMPTY_DACL` are separate conditions, and `dacl_present` is not nullable anywhere in the
schema for exactly this reason.

---

## 4. Crossing the layers

```text
remote_smb:  effective = share_rights ∩ ntfs_rights
local:       effective = ntfs_rights
```

`limiting_layer` names the layer that **removed** rights the other granted — `smb_share`,
`ntfs`, `both`, `none`, or `unknown`. It is the most actionable field in a result: "the
user has Read" is a fact, and "NTFS grants Modify and the share grants Read" is a fix.

An **unread share ACL is not an open one.** Windows shares always carry a share ACL, so
zero stored entries means nobody has read it. The resolver does not fabricate one: the
answer becomes the NTFS rights, `limiting_layer` is `unknown`, `SHARE_ACL_NOT_OBSERVED` is
raised, and `certainty` is `at_most`. The share can only ever remove rights, so the number
stands as an upper bound rather than as an answer.

---

## 5. Paths nobody scanned

A path whose descriptor was never read is not a path with no permissions. Windows would
have given it what its parent projects onto a child, and ADG has that projection
(`app.domain.project_inherited_acl`, specified in
[ntfs-acl-boundaries.md](ntfs-acl-boundaries.md)).

So the resolver walks the path's ancestors — a pure function of the path, so every
candidate key is known before a row is read, and the whole chain costs **one** query — and
projects from the nearest one that was read.

| `AclProvenance` | Meaning | Conditions raised |
| --- | --- | --- |
| `observed` | This object's own descriptor was read | — |
| `derived` | Projected from an ancestor | `NTFS_ACL_DERIVED`, plus `INTERMEDIATE_PATH_UNOBSERVED` when the ancestor is more than one level up |
| `unobserved` | Neither the object nor any ancestor was read | `NTFS_ACL_NOT_OBSERVED` |

A projection is a real answer and a *prediction*: it holds only while nothing in between
breaks inheritance. A parent with a NULL DACL projects nothing at all — what its child
holds comes from the creating process's default DACL — so that case falls through to
`unobserved` rather than being reported as open.

---

## 6. What an answer admits it does not know

`AccessCondition` is a closed vocabulary (`app.access_engine.conditions`) and every value
appears in API responses, so a rename is a contract change. Each occurrence is an
`AccessFinding` carrying the evidence and an operator-facing sentence.

The conditions are folded into a **direction**, not a confidence score:

| `AccessCertainty` | Means |
| --- | --- |
| `certain` | Every input the answer depends on was observed |
| `at_most` | An unseen restriction could only narrow this. An upper bound |
| `at_least` | An unseen grant or membership could only widen this. A lower bound |
| `uncertain` | Gaps in both directions, or `MAXIMUM_ALLOWED` in an evaluated mask |

Two directions at once do **not** cancel: a result nobody can bound is exactly the one that
must not be reported as a number without qualification.

**`access: false` with `certainty: at_least` means no access was established, never that
none exists.** A consumer that renders the two identically has thrown the finding away.

`ASSUMED_TOKEN_SIDS` is deliberately in neither direction. Windows places `Everyone`,
`Authenticated Users` and the logon SID in every token of the declared kind, so the
assumption is exact once the kind is known — and whether the kind is known is what
`SUBJECT_IS_A_GROUP` and `SUBJECT_UNRESOLVED` report. Counting it as an uncertainty would
mark every ordinary answer uncertain, which is how a certainty flag stops being read.

### The condition that hides findings

`TRUSTEE_MEMBERSHIP_UNOBSERVED` is the one to watch. An ACE naming a group the subject is
not in settles something *only* if ADG has collected that group's membership. When it has
not, "no match" is ignorance rather than a conclusion, and a principal may hold rights the
answer does not show. The resolver cannot see this for itself — it needs a database — so
the service supplies it (`unobserved_trustee_findings`), for every ACL trustee that is
unmatched, is or might be a group, and has no membership edges.

---

## 7. Keeping the queries bounded

Three questions, three shapes, none of which touches the whole estate.

### Principal → resource

One upward membership traversal for the token (one query per breadth-first level), two
indexed ACL reads, one membership-coverage query, one label lookup. Constant in the size of
the domain, and pinned by `tests/db/test_query_cost.py`.

### Resource → principals — the inversion

The naive form is quadratic: every principal in the domain evaluated against this ACL. The
bounded form **inverts** it. A principal's rights here depend on nothing except which of
this ACL's trustees it belongs to — so each trustee is expanded **downward** once, and the
map is inverted. One traversal per trustee (tens), not one per principal (hundreds of
thousands), and it is exact rather than approximate.

The listing then says what it could not cover. `enumeration.complete` is false when any
trustee's membership cannot be listed:

* a **world SID** — `Everyone`, `Authenticated Users`, `ANONYMOUS LOGON` — whose membership
  is every principal there is and is in no database;
* a group whose expansion hit a traversal limit.

Each one means principals hold rights here that are not in the listing, and the response
names them rather than returning a short list that looks whole.

### Principal → resources

Candidates come from `principal_references`, keyed by the token's own trustees, **unioned
with the rows that have a NULL DACL**. Those name nobody, so they appear in no reference
row — and omitting exactly the resources that are open to the whole estate would invert the
finding this tool exists to produce. `ix_ntfs_resources_null_dacl` is the partial index
that makes the union cheap.

Only the page is evaluated, and its ACLs are read in one query rather than one per row.
**Candidates that turn out to grant nothing are returned with their verdict, not filtered**:
"named on the ACL and holding no access" is the distinction the engine exists to draw, and
filtering inside a page would also make `has_more` a claim about a different set than the
one being paged.

---

## 8. Paging

| Endpoint | Style | Why |
| --- | --- | --- |
| `/access/resources/{r}/principals` | offset | Computed by traversal, then sliced; the traversal re-runs per page |
| `/access/principals/{p}/resources` | keyset | Straight out of an index, so it resumes after a key |
| `/access/principals/{p}/shares` | keyset | Same |

The same distinction `app.api.pagination` already draws, for the same reasons. The
principal listings deliberately report no `total`: counting a union of two indexed reads
would double the cost of every page to produce a number that counts *candidates*, not
grants.

---

## 9. Ceilings

| Bound | Value | Behavior past it |
| --- | --- | --- |
| DACL entries evaluated | 4096 | `ACL_TRUNCATED`; the fetch ceiling is pinned equal by a test |
| Ancestor levels walked for a projection | 64 | Falls through to `unobserved` |
| Trustees expanded for a principal listing | 128 | `enumeration.trustees_truncated` |
| Membership traversal | `TraversalLimits` | `MEMBERSHIP_TRUNCATED`, and the token is a lower bound |

Every one of them reports rather than silently shortening the answer.

---

## 10. What is not modeled

* **Conditional ACEs, central access policies, and DAC claims.** Unchanged from Phase 0A.
* **The SACL.** Never read; it governs auditing, not access.
* **Privileges.** `SeBackupPrivilege` bypasses the DACL entirely, and `SeTakeOwnershipPrivilege`
  leads to `WRITE_DAC`. Neither is collected, so neither is evaluated — an administrator's
  real reach is wider than any answer here.
* **Share-level `MAXIMUM_ALLOWED`.** Reported as indeterminate, never resolved; its value
  depends on the calling token, which ADG does not have.
* **Access-based enumeration.** Whether a principal can *see* a folder they cannot open is
  a share setting ADG records but does not fold into rights.

---

## 11. How this specification was checked (Phase 4C)

Sections 1 to 10 describe what the engine computes. Phase 4C attacked it with a generated case
matrix and checked the result against Windows itself.

`backend/tests/access_engine/matrix.py` generates **5,631 ACL cases** across the dimensions
this document is written in — direct, group and nested-group grants; Allow and Deny in both
orders; explicit and inherited; protected, unprotected, empty and NULL DACLs; the three SMB
levels and NTFS subsets no label names; owner, non-owner, `OWNER RIGHTS` and `CREATOR OWNER` —
and renders each to SDDL so that `AuthzAccessCheck`, the API behind the Windows Effective
Access tab, can be asked the same question.

**5,628 were answerable and 5,626 agree with Windows exactly.** The remaining two are the
NULL-DACL valid-rights mask, which a real directory settles in the engine's favor.

A further **558 whole-resolver cases** are held to invariants rather than to expected values:
that no answer contains a right nothing granted, that remote access never exceeds local access
over the same DACL, that an unread descriptor is never `CERTAIN`. Those catch the defects an
expected-value test cannot, and one of them found a real one — see the Phase 4C handoff.

Two documents carry the results:

* [`effective-access-limits.md`](effective-access-limits.md) — what the answer does not cover,
  where the engine deliberately differs from `AuthzAccessCheck` and why, and what a rule author
  must not read into a result.
* [`effective-access-performance.md`](effective-access-performance.md) — the measured cost of
  each of the three questions, and the one listing whose cost still follows the estate.

The harness is `scripts/windows-access-check/`, and it needs no elevation, no domain and no
share.
