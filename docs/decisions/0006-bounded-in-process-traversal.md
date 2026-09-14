# ADR-0006: Membership traversal is bounded, in-process, and reports its own limits

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 1B — AD ingestion and membership graph
- **Deciders:** ADG project

## Context

ADR-0002 stores membership as edges and never as an expanded closure, which makes every
"who is effectively in this group?" question a traversal at query time. Phase 1B had to
choose how that traversal runs.

The graph is hostile in ways a textbook one is not. It can contain cycles, because ADG
records them rather than rejecting them — a cycle is a finding. It can be very wide: a
single `Domain Users` group holds every account in the estate. It can name members that no
run has described, because an edge and its endpoint's description arrive independently. And
the product question is not "who", it is "**why**": the chain
`alice → Finance-Team → Finance-RW` is the answer, so reachability alone is insufficient.

Three implementations were available.

* **A recursive CTE in PostgreSQL.** One round trip; the database does the work.
* **In-process breadth-first traversal over a batched adjacency query.** One query per level.
* **A materialized transitive-closure table**, maintained on ingest.

There is also a question none of the three answers by itself: what a query should do when
the graph is larger than any sane bound. An unbounded traversal is a denial-of-service
vector. A silently truncated one is worse — it reports fewer members than exist, and an
audit tool that understates access gives the most dangerous wrong answer it can.

## Decision

**Traversal is a bounded, cycle-safe, in-process algorithm over a batched adjacency
provider, and every recursive answer states whether it is complete.**

1. The algorithms live in `app/domain/graph.py` and depend on nothing but an
   `AdjacencyProvider` protocol — "give me the edges touching these keys". In production
   that protocol is a PostgreSQL repository; in tests it is a dictionary.
2. Expansion is breadth-first: one batched query per level, and the first time a node is
   reached is by a shortest path, which is the explanation worth keeping.
3. Cycles are survived by a visited set and then **reported** as strongly connected
   components (Tarjan's algorithm, written iteratively so a deep graph cannot exhaust the
   Python stack), each with one concrete representative loop.
4. Path enumeration returns every *simple* path, bounded by `max_paths`.
5. Four limits — `max_depth`, `max_nodes`, `max_edges`, `max_paths` — each with a default
   and a hard ceiling. A caller-supplied limit is clamped to the ceiling, not rejected, and
   the response reports the limits actually used.
6. **Every recursive response carries a `traversal` block with `complete` and
   `truncation`.** A truncated result is a lower bound and says so. `is_member: false` on a
   truncated path search means *unknown*, not *no*.
7. The repository's row ceiling is wired to the traversal's `max_edges`, so the database
   never materializes more edges than the traversal may consider.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Recursive CTE (`WITH RECURSIVE`) | Bounds become `WHERE depth < n` buried in SQL, and the result is a flat row set: reconstructing per-node shortest paths, enumerating all simple paths, and reporting strongly connected components all require either window-function contortions or post-processing in Python anyway. It is also untestable without a database, so cycle handling and every limit would be exercised only in the smoke suite rather than on every run. Cost is comparable: one query per level against an index, not one per node. |
| Materialized transitive closure maintained on ingest | The closure ADR-0002 explicitly refuses as a storage format, reintroduced through the side door. It goes stale on any nested change, cannot answer "what if this one edge were removed?", and destroys the path — the thing being sold. |
| Loading the whole edge table into memory and traversing there | Fine at fixture scale, impossible at estate scale, and the failure mode is a memory exhaustion rather than a bounded answer. |
| Unbounded traversal with a request timeout | A timeout is not an answer. The caller gets a 5xx and no information about what was found, and a slow query becomes a denial-of-service vector rather than a lower bound. |
| Truncating silently, without a `complete` flag | The failure this project most has to avoid: a member list shorter than reality, presented as reality. |

## Consequences

**Positive**

- Cycles, depth, width, every limit, and path enumeration are tested against generated
  graphs with no database at all, so they run on every ordinary test invocation.
- One query per breadth-first level: a 50,000-member group costs two queries, not 50,001.
- Explanations come for free — the shortest path is recorded as the traversal runs.
- Bounds are visible to the caller and to the API's own documentation, instead of being
  implicit in a query plan.
- Swapping the adjacency provider later (a cache, a different store, a recursive CTE for one
  hot path) changes nothing above it.

**Negative / accepted costs**

- A deep traversal costs N round trips rather than one. Mitigated by batching per level and
  by defaults that make deep traversals rare; revisit if a hot path proves otherwise.
- Result sets are materialized in the API process, which is what `max_nodes` bounds.
- Recursive pagination is offset-based over a re-run traversal, so a page is not a stable
  snapshot. The `traversal` block travels with every page rather than only the first, and
  the API documentation says so plainly.
- Clients must read `traversal.complete`. An API cannot force that, so the field is
  documented at every level: the endpoint description, the field description, and
  `docs/architecture/membership-graph.md`.

**Follow-up required**

- Phase 4: a derived expansion cache, marked as derived and attributed to an engine version
  (ADR-0003). It becomes another `AdjacencyProvider`, or a layer above one.
- Phase 7: history-aware traversal ("who was in this group last Tuesday?") needs the
  provider to take a point in time; the per-run `observations` rows written from Phase 1B
  are the evidence that will make it possible.

## Compliance

- `backend/app/domain/graph.py` imports nothing from SQLAlchemy, FastAPI, or any repository.
- `backend/tests/domain/test_graph.py` and `test_graph_scale.py` exercise cycles, deep
  chains, wide fan-out, exponential path counts, and every limit without a database.
- `backend/tests/db/test_graph_api.py` asserts `traversal.complete` is `false` with the
  correct `truncation` whenever a limit is reached, and `true` when a limit is merely
  touched.
- A review should reject any recursive endpoint whose response omits the `traversal` block,
  and any traversal that discards results without recording a truncation reason.
