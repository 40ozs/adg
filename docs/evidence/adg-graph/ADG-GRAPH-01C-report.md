# ADG-GRAPH-01C: path engine and graph APIs

**Verdict: `GRAPH_PATH_ENGINE_READY_WITH_LIMITATIONS`**

ADG can now answer bounded graph questions over the relationships GRAPH-01B derives — the
shortest route between two nodes, bounded alternate routes, every node with a route into a
target, and the relationships around a node — and every answer says what it rests on and what
it cannot see. A route is made only of current, derived relationships that the traversal
registry says are privilege transitions; no edge is inferred, and none of the five
not-collected relationship types is traversed or fabricated. "Nothing found" is never
presented as stronger than the graph allows: an empty answer is `no_path` only over a current
graph, after a complete bounded search that met no boundary, and even then every response
states what the graph does not model.

The limitations in the verdict are GRAPH-01B's collection gaps, carried forward and now
represented in every answer (directory-object control, GPOs, trusts, membership modification),
plus this lane's own (§15): routes through logon-time SIDs are reported as boundaries
rather than followed, neighbor exploration around the very largest groups takes seconds, and
the lane's branch is not the one the prompt named (§1).

## 1. Git

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| Branch | `feature/ADG-GRAPH-01C-path-engine-claude` (see "Branch" below) |
| Start SHA = predecessor SHA | `e2efd749aaf46b6afbe3a04ad06c32e4efcfde5b` (`origin/feature/ADG-GRAPH-01B-relationship-derivation`, verified with `git ls-remote origin` at the start and again before each push; the branch has not moved) |
| `origin/main` | `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af`, an ancestor of the start per the 01B report (this lane's clone is shallow and did not re-derive the ancestry) |
| Implementation commits | `c2fd1a7c5e7eb37e7756bb527de3d75991cf2a0f` (path engine and graph APIs), `d23382ea58f10970677e86140f600026ff150607` (snapshot tests, ADR-0042, architecture §10), `3962170d2ea540450aa20a5a861a4152f7508153` (the OpenAPI snapshot's missing `frontier` field, §13) |
| Final SHA | the commit adding this report and the status update, directly on top of `3962170`; the lane return states it, and `git rev-parse origin/feature/ADG-GRAPH-01C-path-engine-claude` is authoritative |
| Merged to `main` or a deploy branch | **no** |

**Evidence review.** Before writing anything every remote branch was fetched and listed (16):
no GRAPH-01C branch, report, path module or ADR 0042 existed anywhere; `claude/adoring-cannon-7gh2pf`,
`claude/compassionate-hamilton-3sh5db`, the integration, deploy and fix branches carry no
ADR numbered above 0039 and no traversal over relationships. The predecessor head matched the
01B report and the status file exactly.

**Branch.** The prompt named `feature/ADG-GRAPH-01C-path-engine`. It did not exist at the
start. At 18:56 UTC, while this lane was in progress, another agent pushing as the repository
owner's identity created it at `e2efd74` and then pushed eight commits of a separate GRAPH-01C
implementation (`app/domain/relationship_paths.py`, `app/services/relationship_paths.py`,
`app/api/graph_paths.py`; head `ace11081314185ce843867d9f8fc11c94183543f` at the time of
writing). Every CI run on its implementation commits failed (runs 49–57). Pushing this lane
there would have meant force-pushing over that history or merging two competing graph engines,
both of which the lane forbids. The owner was asked and chose a separate branch:
`feature/ADG-GRAPH-01C-path-engine-claude`. **That branch is left untouched.** Choosing one
implementation for GRAPH-01D, and retiring the other, is a decision for the owner or GRAPH-01F;
this lane does not make it.

**Contract file.** `00_COMMON-CLAUDE-CODE-CLOUD-CONTRACT.md` is still not in the repository
(as 01B found). The prompt's own rules were applied: repository state is authoritative,
read-only toward audited systems, no merge, no deploy, no force-push, no claimed gate that did
not run, and stop rather than guess.

**Session.** The cloud session was provisioned for `40ozs/csw` (an unrelated repository) with
the branch `claude/amazing-heisenberg-ord6k1`. `40ozs/adg` was attached for this lane. Nothing
was pushed to `40ozs/csw`.

## 2. Environment

| | |
| --- | --- |
| Platform | Linux container (Claude Code cloud), not Windows |
| Python / PostgreSQL / Node | 3.13.16 / 16.15 (local cluster, non-production, created for this lane) / 22.22.0 |
| PowerShell, Pester | not installed: no collector code changed, and the `pwsh`-marked tests exercise collector contracts this lane does not touch |
| Active Directory, file servers, production systems | **none contacted**. Fixtures, transcripts through the ingestion API, and Null Meridian (synthetic) only |
| `rebuild-graph` | run only against the per-test databases and a local Null Meridian demo database (`adg_path_bench_demo`), never against anything else |

## 3. Reconciliation with what exists

Read before writing anything: the 01A and 01B reports and status; `graph-relationships.md`
(§9.11 is the brief for this lane); ADR-0040/0041; `app.domain.relationships`; the relationship
repository and projector, including `RelationshipProjector.state()`; migrations 0018/0019; the
membership traversal (`app.domain.graph`, ADR-0006) and its API; the access API's paths,
`/explain`, ETag and basis conventions (ADR-0012, ADR-0016); authorization (`app.auth`,
`app/api/__init__.py`, ADR-0014) and its exhaustive route audit; pagination; error envelopes;
the OpenAPI snapshot and the frontend contract test; Null Meridian.

What was reused rather than rebuilt:

* **The relationship store and its fold.** Currency, best support, confidence, conditions and
  explanations are `StoredRelationship`'s own; the engine never re-derives them.
  `RelationshipRepository` gained `adjacent_many` (one breadth-first level in one indexed read,
  plus `get_many`'s three) and `nodes_named`.
* **Readiness is the projector's.** `RelationshipProjector.state()` gained `failed_runs` and
  `passes`; no second readiness record exists.
* **The traversal discipline of ADR-0006.** Pure algorithms over an adjacency provider (a
  dictionary in tests), one batched read per level, visited sets, explicit limits, a one-level
  depth probe before reporting truncation.
* **The access API's conventions.** `basis` (the collection basis), strong `ETag`s with
  `If-None-Match` → 304, `Cache-Control: private, no-cache`, `complete`/`truncation`, offset
  cursors, `PageInfo`, `principal_summary`, `DomainValidationError` → structured 422.
* **The capability model.** One new capability, declared at the include site and added to the
  exhaustive route audit.

**Nothing was reused from `app.domain.graph` beyond its discipline.** It walks
`membership_edges`, not relationships, and returns membership chains; the relationship graph
also carries host-local readings and NTFS control and needs currency, readings and boundaries
that membership edges do not have. Both now agree on membership by construction (01B: the
three membership hops are exactly its edge set), and the membership routes are unchanged.

## 4. Traversal semantics

The registry is `app/traversal/semantics.py::TRAVERSAL_SEMANTICS`, one entry per relationship
type, published at `GET /api/v1/graph/semantics` and specified in `graph-relationships.md`
§10.1. Decisions: ADR-0042.

| Type | Role | Class | Followed | Direction |
| --- | --- | --- | --- | --- |
| `member_of`, `primary_group_of` | hop | identity membership | yes | member → group |
| `local_group_member_of` | hop | host-local membership | yes | member → host's group, scoped |
| `owns`, `generic_all`, `generic_write`, `write_dacl`, `write_owner` | hop | file/resource control (NTFS only) | yes | trustee → resource |
| `local_admin_of` | reading | host-local privilege | attached to its `local_group_member_of` step | — |
| `remote_desktop_user_of`, `remote_management_user_of` | reading (always conditional) | conditional remote access | attached to its step | — |
| `foreign_principal_of` | informational | informational | never (neighbors only) | — |
| `nested_member_of` | traversal-only | — | never stored; a nested chain is its direct hops | — |
| `can_modify_membership`, `controls_gpo`, `gpo_linked_to`, `gpo_applies_to`, `trusts` | not collected | directory-object control, group policy, trust | never; never fabricated | — |

* **Readings, not hops (§9.11).** The three local specializations are the projector's readings
  of the same `local_group_member_of` edge. They are attached to the step they read, with their
  own class and conditions. Following both would walk one fact twice.
* **Remote access stays conditional.** A route that *ends* on Remote Desktop or WinRM carries
  the reading's conditions into its assessment (`confidence: probable`, conditions with origin
  `reading`), while `chain_confidence` still says the membership itself is confirmed. No label,
  explanation or field describes a sign-in or session as successful. A reading in the middle of
  a route conditions nothing, because the group's SID is in any logon's token.
* **Local administration** is exactly the host's BUILTIN\Administrators membership. When a
  host-local *account* reaches that group and the route goes on to use the group's rights, the
  path adds `token_filtering_not_modeled` (UAC remote restrictions; domain accounts are not
  filtered).
* **`foreign_principal_of` is provenance.** It is never a privilege step; neighbor exploration
  shows it as `informational`.

## 5. Paths and evidence

* **A path is a sequence of nodes.** Each step carries every followed hop between its two
  nodes, strongest first, and every current reading of the pair. Two relationships joining one
  pair (an owner's `owns` and `write_dacl`) never make two routes.
* **Assessment**, never a score: `confidence` (the weakest of every step's strongest hop, the
  terminal readings and the path's own conditions), `chain_confidence` (hops only),
  `conditional`, `unresolved`, every condition with its origin and step, every qualifier, and
  the note `share_layer_not_evaluated` when a route ends at an NTFS resource.
* **Evidence per edge:** relationship id, type and human label, class, role, canonical source
  and target, scope, derivation class, confidence, currency, conditions and qualifiers with
  their sentences, rule and version, support count, the premise facts of the strongest support,
  a computed explanation, and `href` to `GET /api/v1/graph/relationships/{id}` — every support,
  every evidence link and each fact's stored state. Raw SIDs are keys; labels are display names
  resolved at read time.
* **Canonical identity:** nodes are `<kind>:<canonical key>`. A non-canonical spelling is a 422
  naming the field; reserved kinds (`gpo`, `directory_container`) are a 422 saying nothing can
  name one; an unknown node is a 404; a bare BUILTIN SID with host-local namesakes is a 404 that
  lists them. Nothing is resolved by guessing, and host-local nodes never merge by SID.

## 6. Searches

| | Behavior | Bounds (default / maximum) |
| --- | --- | --- |
| Shortest path | bidirectional, level-synchronous BFS (smaller frontier first); route chosen in memory when the visited sets meet; one-level probe at the depth bound | `max_depth` 10/32, `max_nodes` 20,000/100,000, `max_edges` 100,000/500,000 |
| Alternate paths | both sides walk to a combined depth of `max_depth`; simple routes enumerated in memory by length, then canonical order, pruned by distance to target; never a node twice, never a route twice | the above, `max_paths` 10/100, work budget 200,000 extensions |
| Reverse reachability | backward BFS to `max_depth`; routes reported source → target; offset-paged | `max_depth`, `max_nodes`, `max_edges`; page 100/500 |
| Neighbors | BFS over the requested types and directions; `frontier` counts unexpanded nodes | `depth` 1/3, `max_nodes`, `max_edges`; page 100/500 |

* **Tie-break.** Nodes order by `(kind, canonical key)`. The shortest path is the
  lexicographically smallest node sequence among the shortest routes; alternates are ordered by
  length, then that order; reverse reachability by hops, then node; neighbors by level, type,
  id. Every frontier is sorted and every batch processed in sorted order. Display names, set
  order, insertion order and row order never decide.
* **Cycles** are crossed once (visited maps); alternate routes are simple; nothing unrolls a
  cycle and nothing lists them unboundedly.
* **Limits are refused, not clamped:** a value outside its range is a 422 (ADR-0042 §8;
  ADR-0006's membership routes are unchanged).

## 7. Completeness, no-path semantics and readiness

`outcome` is one of `found`, `no_path`, `incomplete_evidence`, `truncated`, `boundary`,
`policy_restricted`, decided by `app.traversal.completeness.classify` with a fixed precedence
(evidence, truncation, boundary, policy). `conclusive` is true for `no_path`, and for `found`
unless the projection is behind. `completeness.reasons` lists every reason with a category and
a sentence:

| Reason | Category | When |
| --- | --- | --- |
| `nothing_collected` | evidence | no scan run exists |
| `projection_failed` | evidence | a completed run whose own derivation pass failed, not covered since |
| `projection_behind` | evidence | completed runs no successful pass covered (an upgrade to 0019 before `rebuild-graph`) |
| `max_depth`, `max_nodes`, `max_edges`, `max_paths`, `search_budget` | truncation | a limit stopped the search |
| `unknown_relationships` | boundary | relationships of unknown currency were met and not followed |
| `unresolved_boundary` | boundary | a foreign or unresolvable principal was reached walking towards the target |
| `implicit_membership_boundary` | boundary | a logon-time SID (`Everyone`, `Authenticated Users`, logon-session SIDs) was reached |
| `excluded_by_policy` | policy | `confirmed_only` declined current relationships |
| `collection_in_progress` | notice | a run is still collecting; the graph reflects completed runs |

* **"No path" is four different things, and they are four values:** `no_path` (complete,
  bounded, current, no boundary — about the modeled graph only), `incomplete_evidence`,
  `truncated`, `boundary` (plus `policy_restricted`). A path search that finds nothing walks back
  from the target to `max_depth - 1` so boundaries upstream of the target are seen wherever a
  route could have come from.
* **Coverage** (every response): relationship types not collected, directory-object control,
  logon-time token memberships, cross-domain membership, the share layer. It is the boundary of
  the model, never a completeness reason, and the reason `no_path` is never "no path in the
  environment".
* **Readiness** is `RelationshipProjector.state()` plus the collection basis, reported on every
  answer (`basis.projection`) and at `GET /api/v1/graph/readiness`: `current`, `behind`,
  `failed` or `empty`. Tested: all runs derived; a completed run never derived (`projection_behind`,
  `found` becomes inconclusive, `no_path` becomes `incomplete_evidence`); a failed pass
  (`projection_failed`); a rebuild (back to `current` and conclusive).
* **Rebuild compatibility.** Routes are identical over incrementally derived and cleanly rebuilt
  graphs (scenario test and Null Meridian test), and over two different ingestion orders.
* **Snapshot.** Each request runs in one `REPEATABLE READ, READ ONLY` transaction; a run that
  completes mid-request is invisible to it (tested), and the transaction cannot write (tested).
  The `ETag` covers the collection basis and a projection token (pass count and latest pass),
  so a rebuild — which moves no scan run — invalidates cached answers (tested).

## 8. API and authorization

| Route | |
| --- | --- |
| `GET /api/v1/graph/semantics` | registry, policies, outcomes, reasons, limits, tie-break, coverage |
| `GET /api/v1/graph/readiness` | projection state and its reasons |
| `GET /api/v1/graph/paths/shortest` | shortest route (`source`, `target`, `policy`, limits) |
| `GET /api/v1/graph/paths` | bounded alternates (`max_paths`) |
| `GET /api/v1/graph/reachability` | reverse reachability, paged |
| `GET /api/v1/graph/neighbors` | neighbor exploration (`direction`, repeatable `type`, `depth`), paged |
| `GET /api/v1/graph/relationships/{relationship_id}` | one relationship, every support and evidence link |

* **Capability `graph:read`**, declared at the include site, held by `viewer` and every role
  built on it — exactly where `identities:read`, `resources:read` and `access:read` are all held.
  Every hop is a membership or an NTFS entry those already disclose to the same reader, so the
  graph composes nothing a holder could not read hop by hop. `test_the_graph_is_never_a_side_channel`
  refuses a role table granting `graph:read` without all three. The exhaustive route audit
  (`tests/api/test_authorization.py`) covers the seven routes for every role and for each
  capability held alone; an unauthenticated request is a 401 before a session is opened, a
  token with no role or only the reserved role is a 403.
* **No object-level visibility rules exist in ADG** beyond capabilities (search filters
  categories by capability; nothing filters individual objects), so there is nothing per-object
  to preserve.
* **Contract.** `docs/contracts/v1/openapi.json` regenerated: seven paths and 35 schemas
  added, no existing schema renamed or changed (the graph models are prefixed `Graph…` because
  four names collided). The frontend contract tests pass against it (505).

## 9. Schema

**None.** No migration; `0018` and `0019` are untouched. The adjacency indexes from 0018
(`(source_kind, source_key, relationship_type, relationship_id)` and the target mirror) serve
every level read.

## 10. Tests

### 10.1 Gates

| Gate | Baseline (`e2efd74`) | Final |
| --- | --- | --- |
| `ruff check .` / `ruff format --check .` | clean / clean (414 files) | clean / clean (429 files) |
| `mypy app tests` | clean (413 source files) | clean (428 source files) |
| unit `pytest -m "not smoke"` | 5,928 passed, 130 skipped | **6,050 passed, 130 skipped** (+122 new) |
| database `pytest -m smoke` (PostgreSQL 16) | 1,316 passed, 1 skipped, 1 xfailed (55 min 48 s, run beside other work) | **1,367 passed, 1 skipped, 1 xfailed** (+51 new; 60 min, run beside the mutation runs) |
| frontend contract tests (`vitest run tests/contracts.test.ts tests/explanation-contract.test.ts`) | — | 505 passed against the regenerated snapshot |
| `pwsh`-marked tests, Pester | not run (no `pwsh`; collector code unchanged) | not run |
| GitHub Actions | — | `d23382e`: [run 59](https://github.com/40ozs/adg/actions/runs/38627863315) **failed** (backend unit step: the stale OpenAPI snapshot, §13; database step skipped; frontend, compose and Pester green). `3962170`: [run 61](https://github.com/40ozs/adg/actions/runs/38629779751) **all green** — backend lint, types, unit and database (≈ 41 min), frontend, compose, collector Pester on `windows-latest`. The docs commit on top changes Markdown only; its own run is reported in the lane return, not here. |

The baseline ran before any change, on a clean worktree of `e2efd74`, and matched the 01B
report exactly. Unit warnings are unchanged (6). The database run carries 71 warnings against
68: the new validation tests reach the existing `DomainValidationError` handler in
`app/main.py`, which uses Starlette's deprecated `HTTP_422_UNPROCESSABLE_ENTITY`; this lane does
not change that handler.

### 10.2 New and changed tests

* `tests/traversal/test_semantics.py` (13): the registry against the taxonomy and the rules;
  hops, readings, informational, not-collected; classes distinct; policies; coverage.
* `tests/traversal/test_search.py` (77): shortest path (direct, nested, membership → local group
  → control, parallel relationships, equal-length tie, row-order independence over 8 shuffles,
  same node, no path, not current, unknown, cycle); conditional and unresolved routes;
  readings (local admin, RDP and WinRM under both policies, a reading mid-route, token
  filtering for local accounts only); provenance and host scope (two hosts, two spellings of
  one host, a misattributed provider row); boundaries; limits (depth, the leaf probe, nodes,
  edges, refused values, reads per level); alternates (order, row order, simple, cap, depth,
  conditional beside confirmed, the work budget on an exponential graph); reverse reachability;
  neighbors.
* `tests/traversal/test_completeness.py` (24): every outcome, precedence, conclusiveness,
  readiness states, the projection token.
* `tests/db/test_graph_paths_api.py` (49): the HTTP surface over ingested estates — every case
  above end to end, plus validation, readiness (never derived, failed, nothing collected,
  rebuild), ETag and rebuild invalidation, clean-rebuild and ingestion-order equivalence,
  snapshot isolation, read-only transaction, authorization, and statement counts.
* `tests/db/test_null_meridian_demo.py::TestThePathEngine` (2): routes from a deterministic
  sample of sources (≥ 20 pairs over the three most-granted resources) are identical after a
  clean recomputation; the demo is `current`.
* `tests/benchmarks/test_path_benchmark_harness.py` (3) and `tests/benchmarks/path_benchmark.py`.
* Changed: `tests/auth/test_roles.py` (viewer set, side-channel test), `tests/api/test_authorization.py`
  (seven routes, `relationship_id`).

### 10.3 Required coverage

| Requirement | Tests |
| --- | --- |
| §32 shortest path: direct, multi-hop, membership-to-control, tie, source = target, no path, invalid source/target, unresolved node, host-scoped, conditional, mixed, retired excluded, duplicate relationships | `TestShortestPath`, `TestConditionalAndUnresolved`, `TestInformationalAndScope` (unit); `TestShortestPath`, `TestValidation`, `TestConditionalRoutes`, `TestHostScope`, `TestCurrentState` (DB) |
| §33 nested membership through stored edges; cycles | `test_nested_membership_is_its_real_direct_hops`, `test_a_cycle_is_crossed_once`, `test_membership_through_a_local_group_to_ntfs_control` |
| §34 host-local scope; host spelling | `test_the_same_local_sid_on_two_hosts_is_two_nodes`, `test_a_host_spelled_two_ways_is_two_hosts`, DB `TestHostScope` |
| §35 conditional remote access | `test_a_path_ending_on_remote_access_is_conditional` (unit and DB, both readings, both policies) |
| §36 informational edges | `test_a_foreign_principals_domain_is_never_a_privilege_step`, `test_a_foreign_member_is_unresolved_and_a_boundary`, `test_provenance_is_shown_as_informational` |
| §37 alternates | `TestAlternatePaths` (unit and DB) |
| §38 reverse reachability | `TestReverseReachability`, DB `TestReachability` |
| §39 neighbors | `TestNeighbors` (unit and DB) |
| §40 completeness, all eight cases | `TestTheOutcome`, `TestReadiness` (unit); DB `TestReadiness`, `test_a_route_through_a_logon_time_sid_is_a_boundary_not_a_no`, `TestBoundaries` |
| §41 authorization, malformed identifiers, excessive limits | DB `TestAuthorization`, `TestValidation`; `tests/api/test_authorization.py`; `test_the_graph_is_never_a_side_channel` |
| §42 determinism, rebuild in a different order | row-order tests (unit), `test_equal_length_routes_resolve_by_canonical_order`, `TestDeterminismAndRebuild`, `TestThePathEngine` |

## 11. Mutation checks

Each mutation was applied to a scratch worktree of the implementation commit `c2fd1a7`. The
traversal unit tests and `tests/auth/test_roles.py` (146) and `tests/db/test_graph_paths_api.py`
(47 at that commit) were run against a separate database, and the worktree was restored after
each. An unmutated control run passed both.

| Mutation | What it breaks | Unit failures | DB failures | Result |
| --- | --- | --- | --- | --- |
| control: no mutation | — | 0 of 146 | 0 of 47 | passes |
| `visited-node-protection-removed` | a node already reached is reached again | 2 | 0 | **killed** |
| `tie-break-removed` | successors in insertion order instead of canonical order (one of two sites) | 0 | 0 | survived: masked (below) |
| `tie-break-removed-everywhere` | no canonical order anywhere: rows processed as the provider returns them, successors in insertion order | 15 | 0 | **killed** |
| `max-depth-ignored` | the shortest-path search never stops at `max_depth` | 1 | 0 | **killed** |
| `confirmed-only-follows-conditional` | `confirmed_only` follows conditional and unresolved hops | 5 | 2 | **killed** |
| `conditional-assessed-as-confirmed` | a path's hops are assessed confirmed and their conditions dropped | 5 | 2 | **killed** |
| `graph-not-ready-treated-as-complete` | readiness never reports a reason | 4 | 3 | **killed** |
| `foreign-principal-of-traversable` | `foreign_principal_of` becomes a privilege hop | 4 | 2 | **killed** |
| `host-local-identity-collapsed-by-sid` | host-local endpoints are read as their bare SID | 0 | 16 | **killed** |
| `retired-relationships-followed` | not-current relationships are followed | 3 | 1 | **killed** |
| `graph-authorization-disabled` | the graph router is included without `graph:read` | 0 | 1 | **killed**; run separately against `tests/api/test_authorization.py` as well: 24 of 55 fail, among them the single-capability sweeps and the no-role sweep |
| `readings-followed-as-hops` | local specializations become hops beside the membership | 10 | 6 | **killed** |
| `unknown-read-as-absent` | relationships of unknown currency are dropped silently | 1 | 0 | **killed** |
| `implicit-membership-boundary-ignored` | logon-time SIDs are never boundaries | 2 | 2 | **killed** |
| `token-filtering-dropped` | the path-only `token_filtering_not_modeled` condition is lost | 1 | 0 | **killed** |
| `terminal-reading-ignored` | a route ending on Remote Desktop or WinRM no longer carries the reading's conditions | 4 | 4 | **killed** |

**The surviving mutation.** Canonical order is enforced in two places: every level's rows are
sorted when read, and the in-memory region sorts each node's successors. Equal-distance
successors are always recorded by one sorted read, so removing only the second sort leaves the
choice unchanged. It is redundancy, not an untested path: removing both is killed by 15 tests,
including every row-order-shuffling test.

## 12. Performance

Measured numbers, not an SLA, and nothing here is a pass threshold. Machine: this container,
4 vCPU x86_64, Linux 6.18, Python 3.13.16, PostgreSQL 16.15 on the same host. Harness:
`python -m tests.benchmarks.path_benchmark` (`--suite memory`, `--suite nullmeridian --repeat 5`),
run after every other job had finished.

**Null Meridian**, reset through the demo lifecycle (300 runs ingested and derived): **15,329
relationships** over **2,965 distinct nodes** (`member_of` 4,315; `generic_write` 3,093;
`primary_group_of` 1,893; `write_dacl` 1,703; `generic_all` 1,683; `write_owner` 1,683; `owns` 827;
`local_admin_of` 66; `local_group_member_of` 66; 01B measured 15,267 after a clean rebuild, the
incremental graph keeps not-current relationships too). Representative nodes are chosen by
deterministic queries over the estate: the target is the one of the five most-granted NTFS
resources with the deepest route into it (523 sources). Each case is an HTTP request through
the ASGI app. *Cold* is that case's first request in the process (the database's own caches
are already warm from the reset and the selection queries); *warm* is the median of 5 repeats.
They are reported separately and never mixed.

| Case | `max_depth` / cap | Result | Batched reads | SQL statements | Cold | Warm (median) |
| --- | --- | --- | --- | --- | --- | --- |
| short path (member → local Administrators) | 10 / — | found, 1 hop | 1 | 13 | 0.094 s | 0.088 s |
| deeper path (deepest source → resource) | 10 / — | found, 4 hops | 4 | 26 | 0.117 s | 0.105 s |
| no path (a user outside the 523 sources) | 10 / — | `no_path` (with the backward boundary walk) | 11 | 48 | 0.348 s | 0.406 s |
| alternates | 10 / 10 | found, 1 route | 9 | 43 | 0.202 s | 0.193 s |
| alternates | 10 / 100 | found, 1 route | 9 | 43 | 0.187 s | 0.175 s |
| reverse reachability (page of 100) | 10 / — | 523 sources, complete | 5 | 27 | 0.300 s | 0.286 s |
| neighbors of Domain Users, depth 1 (page of 100) | 1 / — | 1,885 relationships, complete | 2 | 14 | 0.387 s | 0.402 s |
| neighbors of Domain Users, depth 2 (page of 100) | 2 / — | 6,166 relationships, complete | 4 | 22 | 2.105 s | 1.960 s |

**Synthetic graphs** (in-memory adjacency, algorithm cost only, no database):

| Shape | Relationships | Result | Reads | Time |
| --- | --- | --- | --- | --- |
| chain of 30 nested groups → resource | 31 | shortest, 31 hops | 31 | 0.002 s |
| same chain, a principal with no route | 31 | no path, complete | 32 | 0.001 s |
| one group of 20,000 members → resource, reverse | 20,001 | 20,001 sources | 3 | 0.741 s |
| 6 layers × 6 fully connected groups (46,656 equal-length routes) | 192 | alternates capped at 100 | 9 | 0.011 s |
| 200-group membership ring | 202 | shortest, 27 hops | 27 | 0.002 s |
| neighbors of the 20,000-member group, depth 2 | 20,001 | 20,001 relationships | 4 | 0.672 s |

**N+1.** None found. Every batched read is one statement against the adjacency index plus
`get_many`'s three, whatever the level holds: statements are about four per level plus a fixed
cost (snapshot, basis, projection state, node checks, labels) of roughly ten — 13 for one read,
26 for four, 48 for eleven. Reads track depth, not width: 20,000 members are one level and
one read; 150 extra members of one group in `test_a_search_costs_reads_per_level_not_per_member`
leave the statement count under the asserted bound (40), and the in-memory harness asserts the
same read count for a 5- and a 500-member group.

**Where time goes.** Above a few hundred relationships per request the cost is loading each
relationship's supports and evidence (`get_many`'s evidence-state query) and folding them in
Python — the price of reading currency on every query instead of trusting a stored copy
(ADR-0040). The depth-2 neighborhood of Null Meridian's largest group (6,166 relationships) is
the worst case measured, at about 2 s. A no-path answer costs a second backward walk (the
boundary probe), visible as the 11 reads of the no-path case.

## 13. Findings made while building

1. **Schema-name collisions change other routes' contracts.** Four response models
   (`CoverageView`, `LimitsView`, `ReadinessResponse`, `ScopeView`) shared names with existing
   ones, and FastAPI resolves that by renaming the *existing* components to module-qualified
   names, which would have broken the frontend's typed contract. The graph models are prefixed
   `Graph…`; the regenerated document only adds.
2. **A neighbor search was reading one level too far.** Probing beyond the requested depth to
   flag "more exists" loaded the whole next level (≈ 2 s around Domain Users) and marked a
   complete answer incomplete. Relationships beyond the requested depth are not part of the
   question; the probe was removed and a `frontier` count replaces it.
3. **A neighbor relationship could be listed for a node the answer never reached.** The node
   budget was checked after listing; it is now checked first (found by a unit test).
4. **The collection basis alone cannot validate a graph answer.** A rebuild changes the graph
   and no scan run, so the ETag includes the projection token.
5. **Logon-time SIDs.** Null Meridian grants many resources to `SYSTEM` (a principal, not a
   boundary), and real estates grant to `Authenticated Users`; without the boundary, a route a
   user certainly has would read as `no_path`.
6. **The published contract fell one field behind.** The OpenAPI snapshot was regenerated
   before the neighbors response gained `frontier`, and the targeted runs used while building
   did not include the contract tests. The full unit run caught it locally after `d23382e` was
   pushed, and CI run 59 failed on it (backend unit step; the database step was skipped).
   `3962170` regenerates the snapshot (one property added).
7. **Canonical order was enforced twice.** Every level is sorted on read and the in-memory
   region sorts successors; a mutation removing one survived, so the mutation set includes
   removing both (§11).

## 14. Read-only boundary

No audited system was contacted or modified. Graph requests run in `READ ONLY` transactions
(tested); nothing in `app/traversal` or `app/api/relationship_graph.py` writes. The only writes
in this lane's tests are ADG's own ingestion of test transcripts and `rebuild-graph` against
test and local demo databases.

## 15. Limitations

* **Collection gaps (GRAPH-01B, unchanged):** directory-object ACLs and owners, membership
  modification, GPOs and trusts are not collected, so no route through them can be found. Every
  answer states this (`coverage`); none reads its absence as "no".
* **Logon-time token SIDs are boundaries, not hops.** A route through `Everyone` or
  `Authenticated Users` is reported (`implicit_membership_boundary`), never followed from a
  principal; the access engine remains the authority for effective access.
* **Host name agreement is still the operator's job** (01B §12): a local group and an NTFS
  trustee join only when both collectors spell the host identically. Tested and preserved;
  nothing guesses.
* **Neighbor exploration loads every relationship within its depth** to read currency;
  around Null Meridian's largest group a depth-2 exploration takes seconds (§12).
* **Alternate-path `max_depth` is conservative:** it is reported whenever a longer walk exists
  in the recorded region, simple or not.
* **A no-path search pays a backward boundary probe**, bounded by the same limits; on a very
  large estate it can itself be truncated, and the answer then says `truncated`.
* **Two GRAPH-01C branches exist** (§1). This lane is on `feature/ADG-GRAPH-01C-path-engine-claude`;
  the other agent's `feature/ADG-GRAPH-01C-path-engine` is untouched and red in CI. The owner or
  GRAPH-01F must choose one before GRAPH-01D starts.
* **Carried from 01B:** the local-groups collector has not read a remote domain-joined server;
  RDP and WinRM are conditional by construction; an upgrade to 0019 needs one `rebuild-graph`;
  Pester and the `pwsh` tests do not run in Linux CI; the epic's UI/deploy ancestry and the
  `claude/adoring-cannon-7gh2pf` hold are GRAPH-01F's to order.
* **No derived-response JSON Schema** was published for the graph responses
  (`docs/contracts/v1/derived/`); the OpenAPI snapshot describes them. GRAPH-01E can publish
  them when it fixes the rendering contract.

## 16. Next lane

`ADG-GRAPH-01D — Crown Jewels, Blast Radius, and Choke Points`, branched as
`feature/ADG-GRAPH-01D-exposure` from the final pushed SHA of
`origin/feature/ADG-GRAPH-01C-path-engine-claude` **once the owner has confirmed this branch,
not `feature/ADG-GRAPH-01C-path-engine`, is the GRAPH-01C result**. It should consume
`app.traversal` (shortest path, alternates, reverse reachability, the outcome and completeness
model, the registry) and must not re-derive path semantics or add hop types without a registry
entry.
