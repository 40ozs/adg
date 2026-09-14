# Effective access: what the answer does not cover

**Status:** accepted
**Phase:** 4C
**Applies to:** `app.access_engine`, `app.services.access`, `/api/v1/access/*`

[`effective-access.md`](effective-access.md) specifies what the engine computes.
This is the other half: what it does **not** compute, where it deliberately differs from
Windows, and what an operator or a rule author must therefore not read into a result.

Every statement here is either a measurement made in Phase 4C — the instrument is named —
or a modeling decision with its reason. Nothing here is a guess about Windows.

---

## 1. How the engine was checked

Two instruments, run by `scripts/windows-access-check/`.

| Instrument | What it answers | Where it lands |
| --- | --- | --- |
| `AuthzAccessCheck` over a synthetic descriptor | The access check itself, for any DACL shape | `backend/tests/fixtures/windows/ntfs-access-oracle.json` |
| A real directory opened with `MAXIMUM_ALLOWED` | What a running process was actually granted | `backend/tests/fixtures/windows/real-directory-access.json` |

**5,631 generated cases; 5,628 answerable; 5,626 agree with Windows exactly.** The two that
do not are §3 below, and the three that could not be asked are §2.

Authz is the API behind the Windows **Effective Access** tab. That is the right target for an
audit tool: it is the number an administrator sees when they check the same question by hand,
and a product that disagreed with the Windows UI would be wrong in practice whatever the
kernel did.

---

## 2. A descriptor with no owner is not a state a file can be in

`AccessCheck` refuses a security descriptor whose owner is absent, so three matrix cases have
no Windows answer. Every file-system object has an owner.

ADG still models `ResourceDacl.owner_sid = None`, because a **collector** may read the DACL
and fail to read the owner. That is a gap in ADG's observations, not a state of the object,
and the engine treats it as "this subject is not the owner" — which is the conservative
direction only for the owner's implicit rights, and is recorded here because it is the one
place where an unobserved fact is *not* escalated to a condition.

---

## 3. A NULL DACL: `0x001F01FF`, not `0x001FFFFF`

The only measured disagreement between the engine and the synthetic oracle.

| Instrument | NULL DACL answer |
| --- | --- |
| ADG | `0x001F01FF` (`FILE_ALL_ACCESS`) |
| Authz, synthetic descriptor | `0x001FFFFF` |
| **A real directory, opened** | **`0x001F01FF`** |

A bare descriptor carries no object type, so Authz cannot apply the file system's
valid-rights mask and returns every bit in the standard and specific ranges. The difference
is exactly `0x0000FE00` — specific-rights bits 9 to 15, which the file system does not define.

**The engine is right.** A real directory with `SE_DACL_PRESENT` clear grants
`FILE_ALL_ACCESS`, measured. Pinned in `TestTheNullDaclDivergence`.

---

## 4. The access check and an open are different questions

Measured, and the reason the engine models the access check rather than an open.

On a directory owned by the subject whose DACL grants the token nothing:

* the access check reports `READ_CONTROL | WRITE_DAC` — the owner's implicit rights;
* a `CreateFile` open is **refused outright**;
* and yet `Get-Acl` on that same directory **succeeds**, which is how the fixture recorded its
  SDDL — so the owner really does hold `READ_CONTROL`.

A successful open also returns rights the DACL never granted: `FILE_READ_ATTRIBUTES` arrives
through the parent's traverse right, and `DELETE` through the parent's `FILE_DELETE_CHILD`.

So an open-time granted mask is neither a superset nor a subset of the access check. ADG
reports the access check because that is what the Windows UI reports, and because an audit
tool's safe error is the one that over-states reach.

**Do not read `access: true` as "this principal can open this object."**

---

## 5. Where ADG deliberately differs from `AuthzAccessCheck`

Two cases where the engine reports something Authz does not, both on purpose.

### `MAXIMUM_ALLOWED` in an ACE

Windows grants nothing for an ACE whose mask is `0x02000000`: it is not a right that can be
granted. ADG raises `INDETERMINATE_RIGHTS` and sets `certainty: uncertain` instead of
reporting zero, because an ACE carrying it is a descriptor ADG cannot interpret, and
silently answering "no access" for an object nobody understands is the failure mode this
engine exists to avoid.

### `ACCESS_SYSTEM_SECURITY` in an ACE

Windows grants nothing for `0x01000000` in a DACL: access to the SACL is gated by
`SeSecurityPrivilege`, not by the DACL. ADG retains the bit in the mask and excludes it from
every label. It is a fact about the descriptor, not a claim about access.

Neither difference affects any effective-rights label, and neither appears in the 5,626-case
agreement because the matrix's masks are the ones a real ACL carries.

---

## 6. Privileges are not modeled

Unchanged from Phase 4B and still the largest gap.

`SeBackupPrivilege` and `SeRestorePrivilege` bypass the DACL entirely.
`SeTakeOwnershipPrivilege` leads to `WRITE_OWNER` and from there to everything.
`SeSecurityPrivilege` reaches the SACL. None is collected, so **an administrator's real reach
exceeds any answer this engine produces**, and no condition says so, because ADG has no
evidence either way.

A backup operator with no ACE anywhere will be reported as having no access. That is the
single most consequential false negative in the product.

---

## 7. The token is a reconstruction

ADG has never seen a logon. The token is built from collected memberships plus a named
`TokenAssumption`, and every assumed SID is listed on `ASSUMED_TOKEN_SIDS`.

Phase 4C measured what Windows actually puts in a context built from a real user SID:

```
S-1-1-0 (Everyone)  S-1-5-11 (Authenticated Users)  <primary group>
<local groups>      S-1-5-32-544  S-1-5-32-545  S-1-5-32-555  ...
```

`Everyone` and `Authenticated Users` are there, which is what `TokenAssumption.AUTHENTICATED_USER`
assumes. What is **not** there, and cannot be:

* **Logon-session SIDs.** `NETWORK` / `INTERACTIVE` depend on how the session was
  established. ADG adds one from the declared access path; `BATCH`, `SERVICE` and
  `REMOTE INTERACTIVE` are refused and reported as `LOGON_TYPE_TRUSTEE`.
* **Deny-only SIDs.** A filtered administrator token carries `BUILTIN\Administrators` with
  `SE_GROUP_USE_FOR_DENY_ONLY`: it matches Deny ACEs and never Allow ACEs. ADG has no way to
  know whether a session is elevated, so it treats every token SID as enabled. **For a member
  of a local administrators group, ADG over-reports.**
* **`BUILTIN\Users` membership**, which is usually invisible. Every domain user is in it on
  every member server, and ADG only knows so where a local-group collection ran. Reported as
  `TRUSTEE_MEMBERSHIP_UNOBSERVED`.

---

## 8. Still unmodeled

Unchanged from Phase 4B, restated because a reader of this document should not have to go
looking:

* **Conditional ACEs, central access policies and DAC claims.** A conditional ACE is stored
  and evaluated as if unconditional, which over-states access.
* **The SACL.** Auditing, not access.
* **Access-based enumeration.** Whether a principal can *see* a folder they cannot open is a
  share setting ADG records and does not evaluate.
* **Inheritance levels.** `inherited_from` is never populated, so canonical-order checking
  cannot distinguish one ancestor's inherited block from another's. An inherited Deny behind
  an inherited Allow is deliberately not reported.
* **A projected DACL is a prediction.** `NTFS_ACL_DERIVED` holds only while nothing between
  the path and its ancestor breaks inheritance, which is exactly what an unscanned
  intermediate directory could do.

---

## 9. Bounds on the API

`tests/api/test_access_bounds.py` holds these in place structurally.

Every access route is **anchored** to one principal or one resource in its path. There is no
route that names neither, so there is no query string that asks for the principal-by-resource
product. Every listing is paged, every `limit` is capped at `MAX_LIMIT` by the framework, and
every page reports `has_more` and `next_cursor`.

Traversal depth and breadth are clamped to `MAX_DEPTH_CEILING` and `MAX_NODES_CEILING` in
`TraversalBounds` before the service sees them.

### Two known gaps

1. **The access endpoints do not echo the effective traversal limits**, unlike the graph
   endpoints. A caller whose `max_depth` was clamped is not told the number that was used.
   It is not a safety gap — the clamp happens regardless, and a traversal that was actually
   cut short sets `token.membership_complete = false`, which moves the answer to
   `certainty: at_least` — but it is an inconsistency between two families of endpoints.

2. **`/access/resources/{r}/principals` expands the whole reachable set per request.** See
   [`effective-access-performance.md`](effective-access-performance.md) §3. Pinned as a
   strict `xfail` in `tests/db/test_access_performance.py`.

---

## 10. What a rule author must not do

1. **Do not treat `access: false` as "no access".** With `certainty: at_least` it means *no
   access was established*. Phase 4C found the engine reporting `certainty: certain` for a
   directory whose descriptor no run had read — "nobody can reach this" for every directory
   nobody had looked at. Fixed; the shape of the mistake is the point.
2. **Do not compare labels.** `rights.mask` is the value, `rights.label` is a rendering.
   SMB `Read` and NTFS `Read & Execute` are one mask under two names.
3. **Do not re-derive conditions from the mask.** The owner's `WRITE_DAC` appears in no ACE;
   read `OWNER_IMPLICIT_RIGHTS`, `ESCALATION_RIGHTS` and `NULL_DACL` from the conditions.
4. **Do not read `enumeration.complete` as a formality.** On any ACL naming `Everyone` it is
   false, and that is the correct answer.
5. **Do not assume a disabled account has no access.** `SUBJECT_DISABLED` (added in Phase 4C)
   says the account cannot authenticate; the rights on the ACL are real, they are stale, and
   they will be live again the moment somebody re-enables it.
