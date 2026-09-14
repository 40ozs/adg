# ADR-0001: The SID is the canonical principal identity

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 0A — Formal permission domain model
- **Deciders:** ADG project

## Context

ADG must state who has access to a resource and keep that statement true across scans. The
identifying attributes Windows exposes for a principal fall into two groups:

* **Stable:** the SID. Windows stores SIDs — not names — in every ACL and every group
  membership. A SID is not reused within a domain, and account renames do not change it.
* **Unstable:** display name, `sAMAccountName`, UPN, distinguished name. All can be edited;
  a DN changes when an object moves between organizational units; a UPN can be reassigned to
  a different person; NetBIOS-qualified names are not unique across trusted forests.

Any model keyed on an unstable attribute produces wrong audit answers in ordinary
circumstances: a rename splits one principal into two rows, a UPN reassignment silently
transfers one person's access history to another, and a cross-forest name collision merges
two people.

There is also a category of SID that resolves to no name at all — a deleted account, or a
principal from a domain the collector cannot query. Those SIDs sit on real ACLs and are
among the findings ADG exists to surface.

## Decision

The SID is the primary identity of every principal throughout ADG.

1. `Sid` is a validating, canonicalizing value type (`backend/app/domain/identity.py`). Its
   canonical string form is the key used by database tables, API paths, join columns, and
   deduplication.
2. Names, UPNs, sAMAccountNames, and distinguished names are stored as **metadata only**.
   They may be displayed, indexed for search, and used for lookup convenience. They are
   never a key, never a join column, and never the basis for deciding that two records
   describe the same principal.
3. Canonicalization is mandatory on construction: upper-case `S`, no leading zeros,
   decimal rendering for identifier authorities below 2³², hexadecimal above, whitespace
   trimmed. Structural limits (revision 1, ≤ 15 sub-authorities, 32-bit sub-authorities,
   48-bit authority) are enforced.
4. **A BUILTIN SID is not globally unique.** `S-1-5-32-*` is identical on every Windows
   computer, so `LocalGroup` and local membership edges are keyed by `host_key|sid`.
5. An unresolvable SID is a first-class principal (`UnresolvedPrincipal`), never discarded
   and never given a guessed name. A name observed in an earlier scan is kept separately as
   `last_known_name`.
6. Where a recorded domain contradicts the domain derived from the SID, construction fails.
   The SID wins.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Key on `DOMAIN\sAMAccountName` | Renames split one principal into two; the value is not unique across trusted forests; it does not exist for well-known principals. |
| Key on UPN | Editable and reassignable; absent for groups and computers; reassignment would transfer access history between people. |
| Key on distinguished name | Changes whenever an object moves between organizational units. |
| Synthetic surrogate key with the SID as an attribute | Adds a lookup without adding stability, and invites code paths that join on something other than the SID. A surrogate key may still be introduced as a storage detail, but the SID remains the natural key. |
| Store SIDs verbatim without canonicalization | Different Windows APIs render the same SID differently; one principal would occupy several rows. |

## Consequences

**Positive**

- Renames, OU moves, and UPN changes do not affect identity or history.
- Orphaned and cross-forest SIDs are representable, so they can be reported.
- Collectors need not resolve names for a fact to be storable and useful.
- Two collectors reporting the same principal through different APIs converge on one row.

**Negative / accepted costs**

- Every user-facing surface must resolve SIDs to names for display, and must handle the
  case where no name exists.
- Name-based search requires a separate index over mutable metadata, kept deliberately
  distinct from identity.
- `sIDHistory` is not yet modeled, so an ACE naming a historical SID resolves as unresolved
  until a later phase adds it.

**Follow-up required**

- Phase 1 must store observed names with their scan run so that a stale name is visibly
  stale.
- A later phase should merge principals across `sIDHistory` on evidence, never on names.

## Compliance

- `Principal.identity_key` returns the SID; `LocalGroup` overrides it to include the host.
- `backend/tests/domain/test_sid.py` and `test_principals.py` pin canonicalization, the
  rejection of names as SIDs, the host-scoping of BUILTIN SIDs, the rule that a rename does
  not change identity, and the prohibition on naming an unresolved SID.
- A review should reject any schema, API route, or join that keys a principal by name.
