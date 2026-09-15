# ADR-0028: Governance is metadata *about* observations, never an observation

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 10A — governance data model: owners, reviews, attestations
- **Deciders:** Phase 10A implementation

## Context

ADG's value rests on one property: what it reports about an estate is what a collector read,
and nothing in the product edits it. ADR-0003 established the separation of raw observations
from derived state; ADR-0004 established that collectors are read-only toward Windows.

Governance introduces the first records in the product that are **not** observations. A
resource owner, a review decision, a remediation proposal — each is a human statement, and
each sits next to a collected fact it is about.

The pressure to blur the two is real and immediate, and it shows up in three places:

1. **Ownership.** `ntfs_resources.owner_sid` already exists — the SID Windows stores as the
   object's owner. An owner is an owner, so why not write ADG's owner there, or read it from
   there when nobody has set one?
2. **Revocation.** A reviewer decides a grant should go. ADG holds a row for that grant. The
   obvious implementation deletes or flags it.
3. **Convenience.** A review's conclusions are the freshest opinion in the system, so a
   "current effective access" query that took them into account would look more accurate.

Each is a small step, and each destroys the property the product exists for. After the first,
"who owns this" has two possible sources and no way to tell which answered. After the second,
the ACL tables describe a mixture of what Windows holds and what somebody wishes it held.
After the third, ADG can no longer answer "what does the estate actually look like" at all.

## Decision

**A governance record is a statement about an observation. It is never an observation, and it
never changes one.**

Four structural consequences, each enforced rather than documented:

1. **Separate tables, no foreign keys to collected ones.** The eight governance tables name
   collected objects by string key, exactly as the ACL tables name each other. A campaign
   about a share a later reconciliation proves is gone stays readable — which is precisely
   when somebody wants to read it.

2. **`resource_owners` is not `ntfs_resources.owner_sid`.** ADG's accountability record is
   neither read from nor written to the Windows owner field. Both are reported, separately and
   labelled; the API's ownership view carries `is_adg_metadata: true` on every record so a
   client cannot render one as the other. **The gap between them is a finding**, and one
   column holding both would erase it.

3. **A revoke decision removes nothing.** It produces a `RemediationProposal`: ADG's record of
   a change somebody *might* make in Windows. ADG performs none of them.
   `Capability.REMEDIATION_EXECUTE` remains reserved and held by no role.

4. **Governance never writes a collected table**, checked two ways.
   `tests/governance/test_isolation.py` walks the syntax tree of every module in
   `app/governance` and fails if any write names one — proving no such path *exists*.
   `tests/db/test_governance_isolation.py` digests every collected table before and after a
   whole campaign and requires them byte-identical — proving none of the paths that run does.

## Consequences

**A live access answer still counts a grant a reviewer revoked.** That is correct and it will
surprise somebody: ADG reports what the estate holds, and a decision is not a change. Until
somebody performs the remediation in Windows and a collector reads the result, the grant is
there. Surfacing "decided but not yet remediated" is a reporting problem, and it is a real
one — see *Not decided here*.

**A campaign can outlive what it reviewed.** Items reference deleted shares and principals
nothing describes any more. That is deliberate: an audit of last quarter asks exactly those
questions.

**Two columns named "owner" exist and mean different things.** Mitigated by naming
(`owner_subject` / `owner_principal_key` versus `owner_sid`), by the `is_adg_metadata` flag,
and by
`tests/db/test_governance_isolation.py::test_the_windows_owner_is_untouched_by_recording_an_adg_owner`,
which asserts both values side by side.

**Governance cannot be used to fix bad data.** An operator who knows an ACE was collected
wrongly cannot correct it through a review. That is the intended cost: the repair belongs to
the collector, and a product where opinions can overwrite observations has no observations.

## Not decided here

* **Showing "revoked but still present".** The data exists — a decision, a proposal, and a
  later observation of the same entry — and no screen or endpoint joins them yet.
* **Whether a remediation proposal should ever be executed by ADG.** The vocabulary reserves
  `RemediationStatus.EXPORTED` for handing one to something else. Executing remains out of
  scope and `REMEDIATION_EXECUTE` unheld.
