# ADR-0002: Membership is stored as a graph of edges

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 0A — Formal permission domain model
- **Deciders:** ADG project

## Context

Access is almost never granted to a user directly. It reaches them through nested groups:
`alice → Finance-Team → Finance-RW → ACE on \\FS01\Finance`. ADG's third product question is
"exactly **why** does this user have that access", and the answer is that chain.

Two storage shapes were available:

* **Expanded closure** — for each group, store the transitive set of principals it contains.
  Fast to query for "who is in this group".
* **Edge graph** — store one row per observed direct relationship, and traverse at query
  time.

The domain also imposes conditions that a naive closure handles badly: groups nested across
domains and forests, membership via `primaryGroupID` (which does not appear in a group's
`member` attribute), local groups whose SIDs repeat on every machine, members that resolve
only to a SID, and membership cycles.

## Decision

Membership is stored exclusively as directed edges (`MembershipEdge`), one row per observed
relationship. Transitive closure is a query-time and cache concern, never a storage format.

1. An edge records `group_sid`, `member_sid`, `kind`, optional `host_key`, optional
   `member_kind`, and `is_foreign_security_principal`.
2. `kind` distinguishes `directory_group_member`, `primary_group`, `local_group_member`, and
   `well_known_implicit`, because they are collected differently and have different scopes.
   `primary_group` exists specifically because a collector reading only `member` loses every
   user's primary group — usually `Domain Users`, which is on a great many ACLs.
3. **Local-group edges are host-scoped.** `host_key` is required for them and forbidden on
   directory edges, because `BUILTIN\Administrators` on FS01 and on FS02 are different
   groups. A domain member inside a local group keeps its global key.
4. **Cycles and unresolvable endpoints are recorded, not rejected.** They are anomalies to
   report. Only a self-edge is rejected: Windows does not create one, so observing one
   indicates a collector defect.
5. Any derived expansion is stored separately, marked as derived, and attributed to the
   engine version that produced it (see ADR-0003).

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Store the expanded transitive closure as the primary representation | Destroys the explanation, which is the product. Goes stale when any nested group changes, must be fully recomputed, cannot be diffed meaningfully, and cannot answer "what if this one edge were removed?" (Phase 9). |
| Store both, with the closure as the source of truth | The closure would inevitably be read where the edges should be, and the two would drift. |
| Store edges only for direct user→group relationships, flattening nesting | Loses the intermediate group, which is exactly where an administrator makes a change. |
| Reject cycles at ingest | Hides a real anomaly and discards observations that were genuinely made. |

## Consequences

**Positive**

- Every access answer can be explained as a concrete path.
- Simulation ("remove alice from Finance-Team") is a graph operation on stored data.
- Change detection is per-edge: one added membership is one diff row.
- Local-group and cross-forest realities are representable without special cases.

**Negative / accepted costs**

- "Who is in this group?" requires traversal, which must be implemented carefully and
  cached; Phase 1 owns the recursive APIs and their performance.
- Traversal must be cycle-safe, since cycles are storable by design.
- The edge table is large — one row per relationship across the estate — and needs indexes
  on both endpoints.

**Follow-up required**

- Phase 1: recursive membership APIs with cycle detection, depth limits, and scale tests.
- Phase 4: a derived expansion cache keyed by scan run, clearly marked as derived.

## Compliance

- `backend/app/domain/membership.py` contains no expansion function of any kind.
- `backend/tests/domain/test_membership.py` pins host scoping, the two-node cycle being
  storable, the self-edge rejection, foreign security principals, and primary-group edges.
- A review should reject any stored table of transitive membership that is not explicitly
  marked as derived state.
