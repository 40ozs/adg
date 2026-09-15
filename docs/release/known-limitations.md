# ADG known limitations

**Status:** accepted
**Phase:** Release Audit
**Date:** 2026-09-15

What ADG does not do, does not know, or gets wrong — collected from every phase handoff into
one place, ranked by how badly a reader could be misled, and stated in the form that matters:
**what a person would wrongly conclude, and when.**

This document exists because the product's central claim is that an empty answer can be
believed. That claim is only worth anything if the places it does not hold are written down.

---

## 1. The evidence gap: none of it has met a domain

**The Active Directory collector has never been run against a domain controller. The SMB
collector has never read a share ACL off a real file server.**

Every AD test runs against a fixture provider — a test double that understands the handful of
LDAP filters this collector issues and throws on anything else. The LDAP provider itself is
deliberately thin for exactly this reason, but its binding, paged search, referral handling
and byte-array `objectSid` conversion are **unverified against a real directory**.

The NTFS collector is the exception and it is a real one: it has been run against generated
NTFS trees on a real Windows volume, and the access check has been verified against Windows'
own `AuthzAccessCheck` on 5,626 of 5,628 cases.

> **What this means for a release:** the answers are trustworthy; the *collection* of AD and
> SMB facts is the part a first real deployment will find bugs in. Plan the first
> installation as a validation exercise against a non-production domain, and read the
> Collectors page before believing any empty result.

---

## 2. Where the access answer is wrong, and in which direction

These are the ones that change a verdict. Two of them **over-report** and one **under-reports**;
in an auditing tool the direction is the whole story.

### 2.1 Privileges are not modeled — under-reports (largest correctness gap)

`SeBackupPrivilege` bypasses the DACL entirely. A backup operator with no ACE anywhere is
reported by ADG as having **no access**, and nothing in the answer says a privilege might
apply. Same for `SeRestorePrivilege` and `SeTakeOwnershipPrivilege`.

> A reviewer certifying "nobody outside Finance can read this" can be wrong, and ADG will
> not have hinted at it.

### 2.2 Deny-only SIDs are not modeled — over-reports

A filtered administrator token carries `BUILTIN\Administrators` with
`SE_GROUP_USE_FOR_DENY_ONLY`: it matches Deny ACEs and never Allow ACEs. ADG treats every
token SID as enabled, so **for a member of a local administrators group it over-reports
access**.

### 2.3 Access-based enumeration is recorded, not evaluated

Whether a principal can *see* a folder they cannot open is a share setting ADG stores and
does not fold into the rights answer.

### 2.4 Unmodeled entirely

Conditional ACEs, central access policies, Dynamic Access Control claims, and the **SACL**.
A collector encountering an audit entry must not record it as an access ACE. `sIDHistory` is
not resolved; trusts are not entities. Per-directory case sensitivity, trailing dots and
spaces in path components, DFS namespace resolution, 8.3 short names, and host aliases
(`FS01` and `fs01.corp.example.com` remain separate servers until merged on a computer SID).

### 2.5 Two deliberate deviations from Windows

`MAXIMUM_ALLOWED` and `ACCESS_SYSTEM_SECURITY` in an ACE mask are handled differently from
`AuthzAccessCheck`, with rationale in `docs/architecture/effective-access-limits.md` §5.
Neither appears in a real ACL, which is why the 5,626-case matrix does not contain them.

---

## 3. The live-versus-as-of divergence — **fixed**

This entry used to read: *ADG deletes nothing on ingestion, current-state reads are not
routed through presence, so after a reconciled scan has proved an ACE gone the point-in-time
engine stops counting it and the live engine does not.* The first half is still true and
always will be. The second half is no longer.

Current-state reads now go through one presence predicate — an object is current unless
`object_versions` holds an open tombstone for it — described in
[`current-state-presence.md`](../architecture/current-state-presence.md). Membership edges,
share ACEs, NTFS ACEs, shares, directories, servers and principals a successful authoritative
reconciliation proved absent are excluded from every current-state answer, while every row
and every version stays stored and every point-in-time answer still reconstructs them.

The regression matrix is `tests/db/test_current_state_presence.py`, and
`TestCurrentAgreesWithAsOfTheLatestState` holds the invariant directly: over a fully
reconciled estate, the live and as-of-latest answers to the same question must be the same
number.

**What remains, and it is narrow.** A run that may not reconcile — partial, failed,
incremental, downgraded, or one that declared no reconciled scope — removes nothing, by
design. So between a removal happening in Windows and the next *successful authoritative*
scan of the scope it lies in, the live answer still shows the old grant. That is the
collection cadence, not a state-selection defect: ADG reports what it was last told by
somebody in a position to tell it.

---

## 4. Cost that follows the estate

Measured numbers are in [`performance-baseline.md`](performance-baseline.md). Two shapes grow
with the estate rather than with the page:

### 4.1 `resource -> principals` at a fixed page — **pinned as a strict `xfail`**

The same twenty-five-row page costs ~13 ms against a 100-share estate and ~149 ms against a
400-share one. The inverse question, `principal -> shares`, is flat. This is Phase 4C
finding 3 and it is a *known* failure held by a test that fails if it ever starts passing.

### 4.2 Offset paging re-runs the traversal per page

Recursive results are computed as one bounded traversal and then sliced, so a change between
pages can shift the result. The response says so through its `traversal` block rather than
implying a stability it does not have — but a client paging a large recursive result is not
reading a stable snapshot.

### 4.3 Simulation is the most expensive request the API serves

~197 ms for a **one-change** proposal against a 10-share estate, because resolving the
affected scope is the work. `simulations:run` is deliberately withheld from `viewer` for this
reason, and the bounds exist to cap it. See the baseline for how it moves.

---

## 5. Where "I don't know" is reported as something else

### 5.1 `unresolved_reason` is always `unknown` in the AD collector

The contract supports `deleted`, `untrusted_domain` and `lookup_failed`; the collector does
not yet distinguish them, because doing so honestly needs the LDAP result code threaded
through the resolver.

### 5.2 Sibling ACE changes are counted over the page, not the window

A resource whose ACE changes landed on the previous page shows zero siblings here and can be
reported as an **unexplained digest change**. Recorded rather than papered over: the
alternative is a second windowed query per resource.

### 5.3 Broad trustees make many ACLs look noisy

Most ACLs carry an `Everyone` or `Authenticated Users` entry whose membership no database
holds. ADG reports that accurately, and it reads as noise until a consumer renders it well.

### 5.4 Deleted objects are only seen where normally discoverable

The AD collector does not set the show-deleted control, so a member DN pointing into the
Deleted Objects container resolves as unreadable and is reported as an error rather than as a
deletion.

---

## 6. Explanation and remediation limits

1. **Each removal is measured alone.** "Which two edges together would revoke this" is a set
   cover and is not attempted; where no single edge suffices, `sufficient_removals` is empty
   and says so rather than guessing at a pair. On a real estate this is the common case.
2. **Only two operations are modeled** in access paths: deleting a membership edge and
   deleting an ACE. Changing an owner, breaking inheritance and narrowing a mask are each a
   different operation with a different blast radius.
3. **Paths are not ranked.** Every contributing path is reported equally.
4. **ADG performs no change.** A change plan is a signed instruction a person carries out.
   Nothing verifies it was carried out except the next collection.
5. **The governance audit chain detects a quiet edit, not a determined one.** Anyone with
   database credentials can rewrite the chain wholesale. The code says this rather than
   implying more.

---

## 7. Operational limitations

1. **The log stream is personal data.** SIDs, UNC paths, account names and group names are
   deliberately *not* redacted, because they are what an operator correlates against a
   Windows event log. Apply the retention and access controls you would apply to a directory
   export, and do not ship these logs somewhere your identity data may not go.
2. **A webhook alert sink accepts an `http://` URL.** Over plain http that publishes an
   exposure report and a bearer token to anything on the path. Off by default; use `https`.
3. **Post-run risk evaluation is off by default** (`ADG_ALERTS_ON_RUN_COMPLETION=false`) and
   that is a cost decision, not a doubt about the feature — it has not been profiled against
   an estate with millions of ACEs. It is never silent: every completion logs `post_run`
   saying so, and the risk report's `coverage` block reports when the rules were last
   evaluated, so an installation that turned neither on sees "the rules have not been
   evaluated" rather than a clean report.
4. **Retention deletes nothing unless two switches are set**, and pruning is an operator
   command that nothing in the API or the collectors calls.
5. **`docker-compose.yml` is a development stack.** It has no restart policy, no resource
   limits, no TLS termination, and a default database password. A real deployment puts the
   API behind a reverse proxy that terminates TLS.
6. **The sensitive-resource risk rule reports nothing until resources are declared
   sensitive** (ADR-0024). That is a configuration state, not a clean result, and the report
   distinguishes them.

---

## 8. Test and tooling limitations

1. **The risk engine's own thresholds are judgments, not measurements.** They are
   configuration precisely so an installation can disagree.
2. **The scenario matrix is a structured product, not exhaustive.** 5,631 cases is a
   deliberate slice; the full cross-product buys nothing, because the layer crossing does not
   care which of thirteen masks produced a given set of rights.
3. **The elevated Windows verification tier was never built.** Verifying the *share* layer
   against a live SMB share needs local accounts and shares, which needs Local Administrator.
   `scripts/windows-access-check/README.md` documents exactly what it would need. The
   unelevated harness that was built covers the NTFS access check completely, which is the
   larger half.
4. **Benchmarks assert nothing.** A millisecond threshold passes on a fast machine and fails
   on a busy one until nobody trusts the suite. The numbers live in documents with the
   machine that produced them — which is why a benchmark that stops running is dangerous, and
   why one that had stopped is finding P-1 in `release-readiness.md`.

---

## 9. What is deliberately out of scope

DLP, SharePoint, OneDrive, Exchange, Linux/NFS, sensitive-content classification, and any
form of data-at-rest inspection. ADG reads permissions, not content.
