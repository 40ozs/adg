# ADR-0007: A trustee's resolution is computed at query time, never stored

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 2B — SMB persistence and resource APIs
- **Deciders:** ADG project

## Context

A share ACL names trustees by SID. Some of those SIDs resolve to a principal ADG holds; some
do not, because the account was deleted, because it belongs to a forest nobody can query, or
simply because the AD collector has not run yet. An unresolvable SID on an ACL — an
*orphaned trustee* — is one of the findings this product exists to report, so the question
"is this trustee known?" is asked of almost every ACE ADG shows anyone.

The two collectors that supply the answer are independent. The SMB collector reads share
ACLs on a file server; the AD collector reads principals from a domain controller. They run
on different schedules, with different credentials, and either can fail without the other
noticing. So the set of resolvable SIDs changes on its own, with no event that touches the
ACE rows at all.

Three ways to answer the question were available.

* **Store the answer on the ACE.** A `trustee_resolved` boolean written at ingest.
* **Store the answer and invalidate it.** The same column, with the AD ingestion path
  updating every ACE row that names a SID it has just described.
* **Compute the answer.** Store only the trustee's principal key, and left-join
  `principals` whenever an ACE is read.

The same question applies to every value that is a function of something already stored — a
share's UNC path, whether a share is hidden, whether it is administrative.

## Decision

**Store the trustee's principal key. Compute resolution by joining, every time.**
`smb_share_aces.trustee_key` holds the key the ACE points at, derived by
`app.domain.referenced_principal_key`; whether a `principals` row exists for that key is a
left join performed when the ACL is read, and it appears in the API as
`trustee.resolved`.

**Generalize the rule: no column holds a value derivable from another column.** A share's
UNC path is exactly `\\<server>\<share>` and is computed from `SmbShare`; hidden and
administrative state are computed from the share name. None of the three is stored.

`principal_references` is the one apparent exception and is not one. It records *that* a
resource named a principal — a fact from an observation — and carries no resolution of its
own. It exists so that "which resources name this SID" is a single indexed lookup as the
NTFS layer adds a second kind of ACL, not to cache a join.

## Consequences

**A stored answer would be wrong more often than it was right.** Between the SMB scan and
the next AD scan, every orphan flag would be an unverified guess. The most common ordering
in a real deployment — share ACLs collected before the directory is fully described — would
produce a database full of ACEs marked orphaned that are nothing of the sort, and an audit
report built on it would send someone to investigate accounts that exist.

**Invalidation would have been the wrong kind of coupling.** Making AD ingestion rewrite ACE
rows means the AD collector's correctness now depends on the SMB tables, a write amplified
across every ACE naming a newly described SID, and a new failure mode where a crash between
the two writes leaves the flag stale — with nothing to detect it, because the flag is the
only record of what the answer should be.

**The cost is one join per ACL read.** Bounded: `principals.principal_key` is the primary
key, and an ACL is a handful of entries. The read path already fetched the principal to
render a name, so in practice it is the same query.

**Both directions of the question stay answerable.** "Is this trustee known?" is the join;
"which shares name this SID?" is `principal_references` plus `smb_share_aces.trustee_key`,
both indexed. Neither requires the other to have been computed first.

**A derived value cannot disagree with its source.** There is no state in which a stored UNC
path names a share the row is not, because there is no stored UNC path. This is the same
reasoning as ADR-0003: a derived value beside its input is derived state stored as though it
were an observation.

**Trustee keys must be derived by one rule.** `referenced_principal_key` host-scopes a
BUILTIN SID and leaves every other SID global, and `MembershipEdge.member_key` calls the
same function. If the two ever diverged, an ACE and a local-group edge naming one trustee
would point at two different nodes, and the membership graph would not reach the ACL. That
is why the rule has exactly one implementation.
