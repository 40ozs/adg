# Membership graph: storage, queries, and limits

**Phase:** 1B
**Code:** `backend/app/domain/graph.py`, `backend/app/repositories/membership.py`,
`backend/app/services/graph.py`, `backend/app/api/graph.py`
**Decisions:** [ADR-0001](../decisions/0001-sid-as-identity.md),
[ADR-0002](../decisions/0002-graph-preserving-membership.md),
[ADR-0006](../decisions/0006-bounded-in-process-traversal.md)

Membership is stored as directed edges and never as an expanded closure, so every question
of the form "who is effectively in this group?" is a traversal performed at query time.
This document is the contract for those traversals: what a node is, what the answers mean,
what the limits do, and — most importantly — how to tell a complete answer from a partial
one.

For how that contract behaves against a directory that has been alive for a decade — deep
nesting, cycles, renames, orphaned SIDs, very large groups, one BUILTIN SID meaning three
different groups — and for what the answers cost, measured, see
[ad-graph-validation.md](ad-graph-validation.md).

---

## 1. Nodes are keys, not SIDs

The node identity throughout the graph is a **storage key**:

| Principal | Key |
| --- | --- |
| Anything a domain issued | the canonical SID, e.g. `S-1-5-21-…-1104` |
| A local group | `<case-folded host>\|<SID>`, e.g. `fs01\|S-1-5-32-544` |

This is `Principal.identity_key` from the domain layer, and it is exactly what
`membership_edges.group_key` and `member_key` contain, so traversal is a join on stored
strings with nothing re-derived at query time.

The host scoping is not decoration. `S-1-5-32-544` is byte-identical on every Windows
computer: `BUILTIN\Administrators` on FS01 and on FS02 are different groups with different
members. Merging them would invent access that nobody has.

**Consequence for callers.** An API identifier may be a bare SID or a full key. A bare SID
that matches more than one stored principal is answered with **409 Conflict** and the list
of candidates — never by picking one. Pass `?host=` or use the full key.

Inside a local-group edge, only the *group* is host-scoped. A domain user nested into
`BUILTIN\Administrators` keeps its global key; scoping the member too would fragment one
user into one node per server.

---

## 2. Direction

An edge means **`member_key` is a member of `group_key`**.

| Direction | Question | Endpoint |
| --- | --- | --- |
| Down | Who is in this group? | `GET /api/v1/groups/{id}/members`, `…/effective-members` |
| Up | Which groups contain this principal? | `GET /api/v1/principals/{id}/groups` |

---

## 3. The five queries

### Direct members of a group
`GET /api/v1/groups/{id}/members`

One hop. Keyset-paginated, ordered by member key. Returns the edge (`edge_kind`, host,
foreign-security-principal flag) alongside the member, because *how* a membership was
established is part of the evidence.

### Direct parent groups of a principal
`GET /api/v1/principals/{id}/groups` (default `?scope=direct`)

The mirror image, one hop upward, paginated the same way.

### Recursively effective members of a group
`GET /api/v1/groups/{id}/effective-members`

Breadth-first expansion downward. Each result carries:

- `depth` — hops from the queried group; `1` is a direct member.
- `path` — the **shortest** chain of keys from the group to this principal.
- `edge_kinds` — the kind of each hop along that path.
- `via_foreign_security_principal` — whether any hop crossed a trust boundary.

The `?include=` filter decides what is kept:

| `include` | Keeps |
| --- | --- |
| `non_groups` *(default)* | Everything that is not a known group: users, computers, well-known SIDs, unresolved SIDs, **and principals ADG has not described yet**. |
| `users` | User and managed-service accounts only. |
| `all` | Every reached principal, nested groups included. |

The default is `non_groups` rather than `users` on purpose. An edge can name a member that
no run has described — the membership was observed, only the description is missing. Such a
node is returned with `kind: null` and `resolved: false`, and `is_group: null` rather than a
guessed `false`. An orphaned SID sitting inside a group is precisely the kind of finding
this tool exists to surface; filtering it away for want of a label would hide it.

### Recursively effective groups for a principal
`GET /api/v1/principals/{id}/groups?scope=effective`

The same expansion, upward.

### All membership paths between a principal and a group
`GET /api/v1/principals/{id}/membership-paths?group={id}`

Every **simple** (repetition-free) chain from the principal up to the group, shortest
first and lexicographic within a length, with the principals along each chain resolved.
This is the answer to "exactly why does this user have that access?"

`is_member` is `true` when at least one path exists. **`false` with
`traversal.complete: false` means *unknown*, not *no*:** the search was cut short before it
could rule membership out.

---

## 4. Cycles

A directory can contain membership cycles, and ADG stores them rather than rejecting them
(ADR-0002) because a cycle is an anomaly worth reporting. Every traversal is therefore
cycle-safe by construction: a breadth-first walk with a visited set terminates on any graph,
and path enumeration refuses to re-enter a node already on the current chain.

Cycles are then **reported**, not merely survived. Each response carries a `cycles` array of
strongly connected components:

```json
"cycles": [
  {
    "members": ["S-1-5-21-…-1210", "S-1-5-21-…-1211"],
    "representative_path": ["S-1-5-21-…-1210", "S-1-5-21-…-1211", "S-1-5-21-…-1210"]
  }
]
```

`members` is the component; `representative_path` is one concrete loop — the shortest one —
so an operator can be shown the actual memberships to break. Only cycles among nodes the
traversal actually reached are reported; a cycle elsewhere in the directory is not this
query's finding.

A cyclic graph is still a **complete** answer. A cycle is not truncation.

---

## 5. Limits, and how to read them

Every recursive response carries a `traversal` block:

```json
"traversal": {
  "complete": false,
  "truncation": ["max_nodes"],
  "limits": {"max_depth": 32, "max_nodes": 50000, "max_edges": 200000, "max_paths": 100},
  "depth_reached": 4,
  "nodes_visited": 50000,
  "edges_read": 63114
}
```

> **`complete: false` means the result is a lower bound.** More principals may qualify.
> Rendering an incomplete result as a membership list understates access, which is the most
> dangerous answer an audit tool can give. Clients must check this field before treating a
> short or empty result as "nobody else".

| Limit | Query parameter | Default | Ceiling | What it bounds |
| --- | --- | --- | --- | --- |
| `max_depth` | `?max_depth=` | 32 | 128 | Membership hops followed. |
| `max_nodes` | `?max_nodes=` | 50,000 | 250,000 | Distinct principals enumerated. |
| `max_edges` | `?max_edges=` | 200,000 | 1,000,000 | Edge rows read. |
| `max_paths` | `?max_paths=` | 100 | 1,000 | Distinct paths enumerated. |

The defaults are generous against a real directory and small against a pathological one:
Active Directory nesting is rarely deeper than a handful of levels, and a Windows access
token cannot hold much more than a thousand SIDs, so a principal effectively in tens of
thousands of groups is a data-quality finding rather than a query to satisfy.

A requested limit above the ceiling is **clamped, not rejected**, and the response reports
the limits actually used — more useful than a 422 for a request the server can answer
safely. A limit below 1 is rejected.

`max_paths` deserves separate mention: the number of simple paths between two nodes is
exponential in the number of branch points. Fifteen stacked "two ways up" choices already
give 32,768 distinct paths. Enumerating them all would be a denial of service wearing a
query's clothes, so the cap exists and reports itself.

**Reaching a limit is not the same as hitting one.** A traversal that stops at exactly
`max_depth` with nothing beyond it is reported as complete; the implementation spends one
extra batched lookup to distinguish "the frontier is exhausted" from "there is more".

---

## 6. Pagination

Two styles, because the two kinds of result genuinely page differently.

| Query | Style | Why |
| --- | --- | --- |
| Direct listings | **Keyset** (`after` the last key seen) | Straight out of an index. A group's membership changes while it is being paged, and an offset would skip or repeat rows — in an audit tool, a skipped member is a missed finding. |
| Recursive results | **Offset** into one bounded traversal | There is no index to seek into; the whole bounded set is produced at once and sliced. |

Cursors are opaque, endpoint-specific, and validated. A cursor from a different endpoint, or
a malformed one, is rejected with **422** rather than ignored — silently restarting from
page one would make a client's second page look like a complete result set.

Because a recursive page re-runs the traversal, the `traversal` block travels with **every**
page, not only the first, and a membership change between pages can shift the result.

Page sizes: default 100, maximum 500. Direct listings also report an exact `total`, which
both endpoints can count cheaply from an index; a `null` total means "not counted", never
zero.

---

## 7. Cost

| Operation | Database queries |
| --- | --- |
| Direct listing | 2 (the page, then the principals to label it) |
| Recursive expansion | 1 per breadth-first level, plus 1 to label the result |
| Path enumeration | The expansion's queries; enumeration itself is in memory |

One query per *level*, not per node: a group with 50,000 direct members costs two queries,
not 50,001. Frontier keys are sent as a single array parameter (chunked at 5,000 keys), not
as an expanded `IN` list.

Both endpoints of `membership_edges` are indexed — `(group_key, member_key)` and
`(member_key, group_key)` — because traversal runs in both directions and one index would
make half of it a sequential scan. The trailing column also covers the keyset pagination
order.

The repository's row ceiling is wired to the traversal's own `max_edges`, so the database
never materializes more edges than the traversal is permitted to consider.

---

## 8. What these queries deliberately do not do

- **They do not compute access.** Membership is one input to effective access; share and
  NTFS ACLs are the others. The effective-access engine arrives in Phase 4 and consumes
  these answers.
- **They do not infer absence.** Nothing here reports that a principal was *removed* from a
  group. Absence requires reconciliation and history (Phase 7); the stored evidence for it —
  `first_observed_*`, `last_observed_*`, and per-run `observations` rows — is being recorded
  from this phase onward.
- **They do not cache.** Every answer is computed from current edges. A derived expansion
  cache, clearly marked as derived and attributed to the engine version that produced it, is
  Phase 4's concern (ADR-0003).
- **They do not interpret well-known SIDs.** `Authenticated Users` is a node like any other;
  what it *means* on an ACL is contextual and belongs to the engine.
