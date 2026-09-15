# What the ADG MVP can and cannot do

**Status:** accepted (Phase 6D)
**Audience:** whoever decides whether this is ready to put in front of an auditor — and
whoever has to answer "can it tell me X?" about it.
**Scope:** Phases 0 through 6. Risk findings and change history are named here only where
their absence changes how something else should be read.

This document is deliberately two-sided. The first half says what the product answers; the
second says what it does not, because in an auditing tool an unstated limit is a wrong
answer waiting to be quoted. Everything below is asserted by a test; where a claim rests on
a measurement rather than a rule, the measurement is named.

---

## The one idea the product is built around

**An empty list means one of two completely different things,** and ADG never lets them look
alike:

* *nothing is there* — and collection is current, so the emptiness is an answer;
* *nobody looked* — a collector failed, is still running, or never ran.

An auditor who reads the second as the first concludes a share has no risky permissions when
in fact nobody checked. So the judgement is made once, in
[`app/domain/collection.py`](../../backend/app/domain/collection.py), and every view is
handed a verdict rather than inferring one from `items.length === 0`. The same rule decides
the banner over a *populated* table, which is the more dangerous case: twelve shares look
complete whether or not a collector failed on a thirteenth.

The unit of that judgement is a **scope** — one collector against one target — not a
collector kind. Phase 6D changed it: with the old rule a later successful NTFS scan of one
file server superseded a failed scan of another, the banner read `healthy`, and a whole
server's worth of "nothing is there" became readable as an answer. See
[Known limits](#known-limits), item 1, for what the current rule still does not cover.

---

## What it answers

### Identities

| Question | Where |
| --- | --- |
| Who is this SID? | `GET /api/v1/principals/{sid}` — the SID is the identity; names are metadata |
| What groups is this principal in, directly and through nesting? | `/principals/{sid}/groups` |
| By which chain of memberships? | `/principals/{sid}/membership-paths?group=…` |
| Who is effectively in this group? | `/groups/{sid}/effective-members` |
| Was this principal renamed, moved, or deleted? | aliases and `unresolved_reason` on the principal |

The membership graph is **graph-preserving**: nesting, cycles, primary-group edges and
foreign security principals are stored as observed rather than flattened. A cycle is
reported as a finding, not filtered out; traversal terminates and says it was bounded.

### Resources

| Question | Where |
| --- | --- |
| What servers, shares and directories has ADG seen? | `/servers`, `/shares`, `/resources` |
| What does the share ACL say, exactly as read? | `/shares/{key}/acl` |
| What does the NTFS ACL say, exactly as read? | `/resources/{path}/acl` |
| Where do permissions change, and why there? | `is_acl_boundary` plus `boundary_reason` |
| Is the stored descriptor the one the collector read? | `acl_hash.agrees` on every ACL response |

Boundary verdicts are **recomputed by the server** from the parent's stored entries and
compared with what the collector reported; neither overrides the other, and a disagreement
is surfaced rather than resolved. A directory whose parent nobody read is reported as
*unknown*, never as *unchanged*.

### Access

| Question | Where |
| --- | --- |
| What can this principal do to this directory? | `/access/principals/{sid}/resources/{path}` |
| Everything this principal reaches | `/access/principals/{sid}/resources`, `…/shares` |
| Everyone who reaches this directory | `/access/resources/{path}/principals` |
| Everything a group's membership reaches | `/groups/{sid}/resource-impact` |
| **Why** — the whole derivation | `/access/explain?principal=…&resource=…` |

The effective answer is a real **access check**: a constructed token, ACEs evaluated in
stored order with deny precedence, generic rights mapped, and the share and NTFS layers
intersected with the narrower one named. It was validated against Windows itself — 5,626 of
5,628 synthetic cases matched the operating system's own `AccessCheck`, and the two that did
not are documented in [`effective-access-limits.md`](effective-access-limits.md).

Every answer **carries its own uncertainty**. An answer may say it could not be conclusive,
may over-state, or may under-state, and it names the reason: an unobserved descriptor, a
group whose membership was never collected, an assumed token SID, a non-canonical DACL, a
disabled account, a truncated enumeration.

### Causality

The explanation is the product's sharpest artifact and the one hardest to get right:

* every route from the principal to the resource, through the groups that carry it;
* the exact ACE, its position, its layer, and where an inherited entry was actually set;
* **measured** removals — each candidate relationship is removed and the access check re-run,
  so a removal that changes nothing is reported as changing nothing rather than offered as
  a fix;
* the same facts as a diagram and as a table, asserted equal, so accessibility does not
  depend on the picture.

### Operations

| Question | Where |
| --- | --- |
| Can an empty page be believed? | `/collection/status` — consulted by every screen |
| When did each scope last succeed? Last fail? | `/collection/operations` → `scopes[]` |
| Did a run deliver what it collected? | `completeness` and `shortfall` per run |
| How much is actually stored? | `counts` — a clean run that wrote nothing shows here and nowhere else |
| What went wrong, across everything? | `errors[]`, grouped by code, widespread first |

A run can report `succeeded` and still have lost a batch in transit, stored fewer rows than
it claimed, or never reconciled a scope it declared. None of those change its status, and
each means the estate below it is less complete than it looks — so each is reported.

### Who may see it

Authorization is **capabilities, enforced in the backend**, declared in one visible list at
the router include site. The frontend filters navigation as a courtesy; typing the URL of a
hidden section reaches a page whose data call the API refuses.

| Role | May |
| --- | --- |
| `viewer` | Read resources, identities, access, risks, changes, collector status; search |
| `auditor` | The above, plus read settings |
| `admin` | The above, plus write settings and ingest observations by hand |
| `remediator` | **Nothing.** Reserved so a tenant can provision the app role ahead of the feature |

Two audits hold this in place, and they answer different questions. One calls every route in
the OpenAPI document with no credential and fails on any that answers. The other mints a
principal holding exactly **one** capability and checks that it reaches precisely the routes
that need it — which is the only way to catch a route requiring the wrong one of two
capabilities that every real role holds together.

---

## What it does not do

**By design, this release:**

1. **Changes nothing.** The application is read-only and the collectors are read-only. There
   is no remediation, no ACL edit, no group membership change. The `remediator` role grants
   nothing, provably. Since Phase 9A ADG can *evaluate* a proposed change — who would gain,
   who would lose, and which alternate route keeps the access anyway — and it does so by
   overlaying the proposal on the rows as they are read, so not one collected row and not one
   object in Windows is touched. There is no surface for it yet; see
   [`simulation.md`](simulation.md).
2. **Does not compute risk.** There are no findings, no scores, no rules. `/risks` is a
   placeholder. Everything on screen is a fact or a derivation from facts.
3. **Compares scans, and says what it cannot compare.** No longer a limitation: Phase 7A
   recorded the evidence and Phase 7C surfaced it. `/changes` answers what moved in a window,
   classified along four axes, with the two halves of an ACL edit read as one edit and a
   "why access changed" answer resolved by the live engine either side of it. Two things it
   still refuses to say, by design: a scan that reconciled nothing produces no removals, and
   an object with no version at one end of a comparison is counted as unobserved rather than
   reported as created or deleted. See [`change-detection.md`](change-detection.md) and
   [`history-model.md`](history-model.md).
4. **Does not scan files, only directories.** The contract can carry a file resource; no
   collector emits one.
5. **Covers Windows file shares.** Not SharePoint, OneDrive, Exchange, NFS, or content
   classification.
6. **Does not read content.** ADG reads security descriptors and directory structure. It
   never opens a file.

**Known limits**

1. **A scope is `(collector, target)`, which is coarser than the truth.** An NTFS run
   declares one `directory_tree` scope per share and the coverage unit is the run's target,
   usually the server. A run that read three of four shares on a server reports one partial
   scope rather than three complete and one missing. The run's declared-versus-reconciled
   scope counts are on the operator page, so the shortfall is visible; attributing it to the
   specific share is not.
2. **Staleness is shown, not judged.** The operator page reports when each scope last
   succeeded and marks a success that is not the latest attempt. Nothing decides that a
   scope is *too* old; there is no schedule to compare against.
3. **Group membership is what a collector saw.** A group ADG has never enumerated makes
   every answer naming it under-report, and the answer says so — but it cannot say by how
   much. `Everyone` and `Authenticated Users` are inherently unenumerable, so any listing
   that crosses one is reported as incomplete.
4. **Local groups are scoped to the host that reported them.** `BUILTIN\Administrators` on
   FS01 and on FS02 are different groups, correctly. A host whose local groups were never
   collected leaves a trustee whose membership is unknown, which is reported as such.
5. **The answer is a static evaluation, not a logon.** A disabled account still shows the
   rights its SID is granted — disabling is not revocation — with the account's state
   flagged. Share-level and file-level auditing policy, logon hours and conditional access
   are outside the model.
6. **Bounded enumeration.** Path counts, traversal depth and removal candidates are capped.
   A truncated answer says `complete: false` and reports the limit applied; paging the route
   list does not widen it.
7. **No conditional GET.** Derived responses carry an `ETag` and a collection basis; no
   client revalidates against it yet, so each page load recomputes.
8. **One tenant, one domain.** Cross-forest trusts and foreign security principals are
   stored and reported as observed; nothing resolves a principal in another forest.

---

## How to see all of it in ten minutes

`scripts/seed-demo.ps1` fills a development database with an estate built for exactly this.
It is deterministic — the same estate every time, on every machine — and it is deliberately
imperfect, because a demo of a clean estate demonstrates the happy path and nothing else.
`python -m app.demo --features` prints what is wrong with it and why each thing is there:
nested groups, two routes to one grant, a membership cycle, a deny that wins, broken
inheritance, an unresolved SID on a live ACL, `Everyone` with full control, a generic right
that splits in two when it is inherited, a `CREATOR OWNER` grant that makes an untouched
directory report as changed, a disabled account that still holds rights, a partial scan, and
a server whose share list was read and whose file system was not.

The runbook is [`docs/operations/mvp-runbook.md`](../operations/mvp-runbook.md).

---

## Where the claims are checked

| Claim | Test |
| --- | --- |
| The whole chain works on one estate | `backend/tests/db/test_mvp_end_to_end.py` |
| The effective answer matches Windows | `backend/tests/validation/test_windows_oracle.py` |
| Every route is behind the right capability | `backend/tests/api/test_authorization.py` |
| A failure on one server is visible | `backend/tests/db/test_collection_status_api.py` |
| No read costs more as the estate grows | `backend/tests/db/test_query_cost.py` |
| An empty page says which emptiness it is | `frontend/tests/state.test.ts` |
| The demo estate carries every feature it claims | `backend/tests/demo/test_estate.py` |
