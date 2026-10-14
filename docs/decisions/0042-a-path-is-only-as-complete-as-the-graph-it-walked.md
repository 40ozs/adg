# ADR-0042: A path is only as complete as the graph it walked

- **Status:** Accepted
- **Date:** 2026-10-14
- **Phase:** ADG-GRAPH-01C — path engine and graph APIs
- **Deciders:** ADG-GRAPH-01C implementation

## Context

ADR-0040 made a privilege relationship a derived projection with traceable supports, and
ADR-0041 kept it current as its facts change. GRAPH-01C serves paths through those
relationships: shortest path, bounded alternates, reverse reachability and neighbor
exploration. A path engine can make a trustworthy graph untrustworthy in several ways that no
relationship row would show:

* **following the wrong edges.** `local_admin_of`, `remote_desktop_user_of` and
  `remote_management_user_of` are readings of the same `local_group_member_of` edge
  (architecture §9.3). Following both walks one fact twice and returns every route through a
  BUILTIN group twice. `foreign_principal_of` is provenance, and a route through it would read
  as a cross-domain privilege nobody collected. The same two nodes are often joined by several
  relationships at once (an owner holds `owns` and `write_dacl`), which a naive search returns
  as several routes;
* **making "nothing found" mean "no".** The graph does not model GPOs, trusts, membership
  modification or directory-object control; Windows adds `Everyone` and `Authenticated Users`
  to tokens at logon, which no stored membership leads into; a foreign principal's holders
  are outside the collected domain; a relationship whose evidence is missing is unknown, not
  absent; a search can be cut short; and the graph itself can be behind what was collected
  (an upgrade to `0019` that has not run `rebuild-graph`, a failed derivation pass);
* **choosing a different route each time**, from set or row order;
* **mixing graphs** when a run completes between two levels of one search;
* **serving an answer from before a rebuild**, because a rebuild moves no scan run and the
  existing validators are keyed on the collection basis alone;
* **becoming a side channel** around the capabilities that guard memberships and ACLs.

## Decision

1. **One traversal registry.** `app.traversal.semantics.TRAVERSAL_SEMANTICS` assigns every
   relationship type exactly one role: *hop* (`member_of`, `primary_group_of`,
   `local_group_member_of`, `owns`, `generic_all`, `generic_write`, `write_dacl`,
   `write_owner`), *reading* (the three local specializations, each naming the hop it reads),
   *informational* (`foreign_principal_of`), *traversal-only* (`nested_member_of`) or
   *not collected*. Only hops are followed by path search, always source to target. A test
   pins the registry to the taxonomy, and the not-collected types are never stored, traversed
   or fabricated.
2. **A path is a sequence of nodes.** Each step carries every followed hop joining its two
   nodes (strongest first) and every current reading of that pair. Two relationships between
   one pair never make two routes. A route that ends on a reading carries it into its own
   assessment, so a route into a host's Remote Desktop Users is conditional; a reading in the
   middle of a route does not condition it. The path adds what only it knows:
   `token_filtering_not_modeled` when a host-local account's membership of the host's
   Administrators is then used.
3. **Currency is the gate.** Only `current` relationships are followed. `not_current` is
   history and is skipped; `unknown` is counted as a boundary. Two named policies say what else
   is followed: `all_current` (every current hop, conditional and unresolved ones marked) and
   `confirmed_only` (confirmed hops only, every declined one counted).
4. **An answer names its own emptiness.** A path answer's `outcome` is `found`, `no_path`,
   `incomplete_evidence`, `truncated`, `boundary` or `policy_restricted`, decided by one pure
   function (`app.traversal.completeness.classify`) with a fixed precedence (evidence, then
   truncation, then boundary, then policy), the position ADR-0016 takes for access answers.
   `no_path` means: within the modeled relationship types, over a current graph, the search
   finished within its limits, met no boundary and declined nothing. A path search that finds
   nothing walks back from the target to its depth limit so that boundaries upstream of the
   target are seen wherever a route could have come from. Every response states what the graph
   does not model at all (`coverage`), so no answer reads as evidence about it.
5. **Readiness comes from the projector.** `RelationshipProjector.state()` (extended with the
   failed runs and the pass count) and the collection basis decide whether the graph reflects
   what was collected. There is no second readiness record.
6. **One snapshot per request, and a validator that covers derivation.** A graph request
   runs in one `REPEATABLE READ, READ ONLY` transaction: the basis, the projection state and
   every level of the search are read from it. The `ETag` covers the collection basis and a
   projection token that moves with every derivation pass.
7. **Determinism by canonical order.** Nodes order by `(kind, canonical key)`. The shortest
   path is the lexicographically smallest node sequence among the shortest; alternates are
   ordered by length, then that order.
8. **Bounded, and refused rather than clamped.** Every search is breadth-first with one batched
   read per level (ADR-0006), under `max_depth`, `max_nodes`, `max_edges` and, for alternates,
   `max_paths` and a fixed work budget. Each has a default and a hard maximum published in the
   OpenAPI document; a value outside it is a 422. ADR-0006's membership routes clamp; these
   refuse, because the bound is part of the question a path answer is about, and a client must
   not be able to believe it asked a larger question than the one answered.
9. **`graph:read`, held exactly where `identities:read`, `resources:read` and `access:read`
   are.** Every hop is a membership or an NTFS entry those already disclose to the same reader.
   A role table granting `graph:read` without all three is refused by a test.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Follow every stored relationship type | Walks each BUILTIN membership twice and treats provenance (`foreign_principal_of`) as a privilege. |
| Hop the specializations instead of `local_group_member_of` | A membership is a fact in any logon's token; reading it as "may sign in over RDP" would make every route *through* Remote Desktop Users conditional on a sign-in it does not use. |
| A path as a sequence of relationships | Returns one route once per parallel relationship and fills the path cap with duplicates. |
| `is_member: false`-style boolean plus a `complete` flag | Two fields a client must combine correctly; ADR-0016 rejected the same shape for access answers. |
| Follow `unknown` relationships | Turns "nobody recorded it gone" into "it holds". |
| Precompute reachability | The closure ADR-0002 rejected; stale on any change and loses the route. |
| READ COMMITTED per statement | One answer could mix two graphs when a run completes between levels. |
| Key the validator on the collection basis only | A rebuild changes the graph and no scan run; the old answer would be served as fresh. |
| Clamp out-of-range limits (ADR-0006) | Leaves the bound out of the published contract, and lets a client believe it asked a larger question than the one answered. The membership routes keep clamping; nothing here changes them. |
| Grant `graph:read` from `auditor` upward | Every hop is already readable by a viewer; withholding the composition protects nothing a viewer cannot assemble hop by hop, while the side-channel test is what actually prevents an escalation. |

## Consequences

**Positive**

- No route is returned twice, and the same graph and question always give the same answer,
  incrementally derived or rebuilt.
- "Not found" can be told apart from "not looked at" in one field, and the reason is named.
- The graph cannot answer from a projection that is behind what was collected without saying
  so, and a rebuild invalidates cached answers.

**Negative / accepted costs**

- A path search that finds nothing costs a second backward walk (the boundary probe), bounded
  by the same limits; on a large estate it can itself be truncated, and the answer then says so.
- Alternate-path enumeration reports `max_depth` conservatively whenever a longer walk exists
  in the recorded region, simple or not.
- Neighbor exploration loads every relationship within its depth to read currency; around the
  largest group of Null Meridian (1,885 members) a depth-2 exploration takes seconds.
- Routes through logon-time SIDs (`Everyone`, `Authenticated Users`) are reported as
  boundaries, not followed; the access engine remains the authority for effective access.

**Follow-up required**

- GRAPH-01D builds exposure analysis on these primitives and must not re-derive path
  semantics.
- GRAPH-01E renders `outcome`, `completeness`, `coverage` and the per-step readings.
- Collecting directory-object ACLs, GPOs and trusts would add hop types; each needs its own
  registry entry before a search may follow it.

## Compliance

- `backend/tests/traversal/test_semantics.py` pins the registry to the taxonomy and the rules.
- `backend/tests/traversal/test_search.py` covers cycles, ties, row-order independence,
  readings, conditional and unresolved routes, host scope, boundaries and every limit without
  a database.
- `backend/tests/traversal/test_completeness.py` holds the outcome precedence.
- `backend/tests/db/test_graph_paths_api.py` covers the HTTP surface over ingested estates,
  including readiness, ETag invalidation by a rebuild, rebuild and ingestion-order equivalence,
  authorization and statement counts.
- `backend/tests/auth/test_roles.py::test_the_graph_is_never_a_side_channel`.
- `backend/tests/db/test_null_meridian_demo.py::TestThePathEngine` shows routes over the demo
  are unchanged by a clean recomputation.
