# AD graph: measured behavior, practical limits, and how to check a collector

**Phase:** 1C
**Code:** `backend/app/domain/graph.py`, `backend/app/repositories/membership.py`,
`backend/app/services/graph.py`, `backend/app/validation/`,
`backend/tests/benchmarks/graph_benchmark.py`
**Fixtures:** `backend/tests/fixtures/ad_graph/`
**Reads on from:** [membership-graph.md](membership-graph.md),
[ADR-0001](../decisions/0001-sid-as-identity.md),
[ADR-0002](../decisions/0002-graph-preserving-membership.md),
[ADR-0006](../decisions/0006-bounded-in-process-traversal.md)

[membership-graph.md](membership-graph.md) is the contract: what a node is, what an answer
means, what the limits do. This document is what happens when that contract meets a
directory that has been alive for a decade — nesting twenty-four deep, groups that contain
each other, three people called Jordan Rivera, a SID nobody can resolve any more, a group
with five hundred members, and a BUILTIN SID that means a different group on every computer
it appears on.

Everything here is measured or tested. Where a number appears, the machine and the dataset
that produced it appear with it, and §6 says plainly which numbers do not generalize.

---

## 1. What was validated, and with what

### The adversarial corpus

Eleven transcripts under `backend/tests/fixtures/ad_graph/`, each a complete scan run in
contract v1 form carrying only `principal` and `membership_edge` observations, so they
replay through the ingestion endpoints verbatim.

| Fixture | The hazard it carries |
| --- | --- |
| `a01-deep-nesting` | 24 levels of group nesting; one user at the bottom. |
| `a02-cycles` | A two-group ring and a three-group ring, reached in sequence, with a user beyond both. |
| `a03-duplicate-names` | Two users in one domain share a display name; a third in another forest shares the sAMAccountName too. Two groups in two domains are both called Finance. |
| `a04a` / `a04b-rename` | The same SID before and after a rename, a UPN change, and a move between organizational units. |
| `a05-unresolved-and-deleted` | A deleted account, an untrusted-domain SID, a failed lookup, a tombstoned user, and an edge naming a SID that nothing describes. |
| `a06-foreign-security-principals` | Trusted-forest members; one reachable by two routes of different lengths, only one of which crosses the trust. |
| `a07-disabled-empty-distribution` | A disabled member, a member whose enabled state was never reported, an empty group, a group containing only a group, and a distribution group. |
| `a08-large-group` | 512 direct members, 500 of them named only by an edge. |
| `a09-multiple-paths` | Four independent routes to one group, one of them a `primaryGroupID` membership. |
| `a10-builtin-scoping` | `S-1-5-32-544` as a local group on FS10, as a local group on FS11, and as the domain's own BUILTIN group. |
| `a10-builtin-scoping-hosts-only` | The same SID on two servers and nowhere else, so a bare SID is genuinely ambiguous. |

They are **generated**, by `backend/tests/fixtures/build_ad_graph.py`, through the contract
models — so every `source_key` is derived by `app.contracts.v1.keys` rather than typed, and
a fixture is valid by construction. The committed JSON is the artifact the tests load;
`tests/contracts/test_ad_graph_fixtures.py` rebuilds it in memory and compares byte for
byte, so the generator and the files cannot drift. Regenerate with:

```powershell
cd backend
.\.venv\Scripts\python.exe -m tests.fixtures.build_ad_graph           # write
.\.venv\Scripts\python.exe -m tests.fixtures.build_ad_graph --check   # verify only
```

Every fixture carries an `expectations` block naming the keys and counts its answers must
have. Two suites consume them: `tests/domain/test_graph_adversarial.py` (hermetic, against
an in-memory repository) and `tests/db/test_graph_adversarial.py` (through the real
ingestion and query endpoints against PostgreSQL). A third test proves every key named in
an expectation actually exists in the transcript, so a typo cannot produce an assertion
that passes forever for the wrong reason.

### Property tests

`tests/domain/test_graph_properties.py` generates layered graphs with skip-level edges and
back edges — the shape a directory acquires the cycles it is not supposed to have — from
forty fixed seeds, and asserts the invariants over every node of every graph:

| Invariant | How it is checked |
| --- | --- |
| Direct edges stay direct | The set of nodes at depth 1 equals the set of stored one-hop neighbours, both ways. |
| Recursion never changes identity | Every returned key appears in the stored edges; the root is never returned as a member of itself. |
| No path is fabricated | Every consecutive pair on every returned path is a stored edge followed in the right direction, and the edge key recorded for that hop is that edge. |
| A cycle never creates infinite output | Every traversal terminates within its node, edge and depth bounds on graphs built to be cyclic. |
| Depth is shortest distance | Compared against an independent unbounded breadth-first search. |
| **A complete answer is complete** | Compared against a reference implementation written to be obviously correct rather than fast: when `complete` is true, the node set must equal everything reachable with no limits at all. |
| Enumeration finds exactly the simple paths | Compared against exhaustive recursive search, for every ordered pair of nodes. |
| Answers are reproducible | Two runs over one graph must be equal, so a diff between scans means a change in the directory. |

The completeness invariant is the one that earns its cost. A bounded traversal that quietly
stopped early passes every other property in the table and still hands an operator a short
membership list that looks authoritative — and it found a real defect (§3.1).

### Benchmarks

`backend/tests/benchmarks/graph_benchmark.py`, run through
`.\scripts\graph-benchmark.ps1`. Not tests: no timing is asserted, because a threshold that
passes on one machine and fails on another teaches people to ignore the suite.
`tests/benchmarks/test_benchmark_harness.py` keeps the harness itself from rotting.

---

## 2. What the graph does under stress

Confirmed by both the hermetic and the database suites.

| Situation | Behavior |
| --- | --- |
| **24 levels of nesting** | Walked completely at the default `max_depth` of 32. The member at the bottom carries a 25-element `path` and 24 `edge_kinds`: the whole chain, as the explanation. |
| **Nesting past the limit** | `complete: false`, `truncation: ["max_depth"]`, and the unreached members are absent rather than guessed. |
| **Cycles** | Terminate, report `complete: true`, and return each strongly connected component with one concrete shortest loop to break. **A cycle is a finding, not a truncation.** Both rings in `a02` are stored — refusing an observed edge would hide the anomaly rather than report it. |
| **Two names, one string** | Five principals sharing three names stay five keys. Names are stored as `principal_aliases` rows and are never a lookup key, so a group never returns another domain's namesake. |
| **A rename** | The principal key, the edge key, and the membership are unchanged. Eight alias rows survive both runs — four name kinds, two values each — so "this SID used to be called sbeckett" is still answerable. |
| **An unresolvable SID** | Stored as a first-class principal with its reason and `last_known_name`, never a display name. Returned as an effective member like any other. |
| **A member nothing describes** | Returned with `kind: null`, `resolved: false`, `is_group: null` — never a guessed `false` — and kept by every `include` filter except the explicit `users`. `GET /principals/{key}` answers 200 for it, because a SID that demonstrably sits inside a group is a finding, not a 404. |
| **A deleted account** | Still a member, still returned, `is_deleted: true`. Filtering it out would understate access; its SID can still be on an ACL. |
| **A disabled account** | Still a member, still returned, `enabled: false`. Whether that matters is the reviewer's judgment, not the traversal's. An account whose state was never reported is `null`, not `false`. |
| **A foreign security principal** | `via_foreign_security_principal` is true for exactly the nodes reached through a trust hop, and false for those reached without one, even in the same answer. A trusted-forest group is a **leaf**: this domain cannot enumerate it, and inventing members would fabricate access. |
| **An empty group** | Zero members, `complete: true`, `total: 0` — not a null count, and not an error. |
| **512 direct members** | All stored, all returned. Six pages at the default size, two at the maximum, walked in index order with no member repeated or skipped. |
| **Four routes to one group** | All four enumerated, shortest first, lexicographic within a length, including the `primaryGroupID` route that a collector reading only the `member` attribute would lose entirely. The four share no first hop, which is the finding: removing any one leaves the access in place. |
| **One BUILTIN SID, three groups** | Three keys, three memberships, no merging. See §4. |
| **Re-ingesting an unchanged directory** | Converges: same keys, same counts, no duplicated edges. |

---

## 3. Defects found, and fixed

All six were found by running something, not by reading code. Each has a regression test
that fails without the fix.

### 3.1 A traversal could report a truncated answer as complete

`expand()` spends one extra batched lookup at the depth boundary to tell an exhausted
frontier from a real cut-off. If the edge budget ran out *during that probe*, the edge was
dropped silently. When every boundary edge pointed back into already-visited nodes — which
is exactly what a cycle looks like — no truncation was recorded at all, and the answer
called itself `complete` while the cycle analysis had run on an incomplete edge set.

Now the dropped edge sets `truncation: ["max_edges"]`. Caught by the completeness property
once the depth sweep was added to the edge-budget sweep; a sweep over edge budgets alone
never reaches the boundary probe, which is why the two limits are varied together.

### 3.2 The repository could out-truncate the traversal without saying so

A traversal can only declare `max_edges` truncation when it is *offered* the edge that
exceeds its budget, so callers pair the repository with `max_edges + 1`. The repository
then clamped that back to `MAX_EDGES_CEILING`, which took the probe row away at exactly the
largest budget a caller can ask for — leaving one setting silently blind. The ceiling is
now `MAX_EDGES_CEILING + 1`, documented as a probe that is never kept, and the pairing is
pinned at the seam in `tests/api/test_traversal_pairing.py`.

### 3.3 `?host=` did not scope the lookup

`GET /principals/S-1-5-32-544?host=FS10` returned the *domain's* BUILTIN group, because
resolution matched the bare SID as an exact storage key before the host was considered. The
one parameter that exists to disambiguate a BUILTIN SID was silently ignored in exactly the
case it exists for. A host-scoped key is now tried first when the identifier is a bare SID.

### 3.4 Host names were neither bounded nor printable

`common.schema.json#/$defs/hostName` has declared `maxLength: 255` and forbidden
`U+0000`–`U+001F` since Phase 0B. `normalize_host` checked neither, so `host_key` — which
becomes half of the storage key of every local group — could be 400 characters long or
contain a newline, and was caught downstream only as an oversized `source_key`: a true
statement about the wrong field. The published constraints are now enforced where every
host-like field already funnelled, with messages that name `host_key` and the bound.

`tests/contracts/test_published_constraints.py` tests this behaviorally rather than by
diffing JSON, because the models enforce most published constraints in validators
(`canonical_sid`, `parse_unc_path`, `normalize_host`) where a JSON-level comparison is all
false positives.

### 3.5 A key was split at the wrong separator

`split_key` labels the one kind of node ADG knows least about — a key a traversal reached
that no `principals` row describes. It split at the *first* `|`. A SID can never contain
one, but a host name can, so `odd|host|S-1-5-32-544` was reported as host `odd` with a
"SID" of `host|S-1-5-32-544`. Now split at the last separator, where the SID is.

### 3.6 Batch validation was quadratic on the ingestion hot path

`ObservationBatch` rejected duplicate `source_key`s with `list.count` per element — a
million string comparisons for a batch at the contract's 1,000-observation maximum, on
every POST. Replaced with one `Counter` pass. Same behavior, same message.

---

## 4. The identity hazard this phase could not fix

**Only `local_group` principals and `local_group_member` edges carry a host.** A BUILTIN or
well-known SID reported any other way is stored under the bare SID.

That is correct for a single domain and wrong for two. `S-1-5-32-544` is byte-identical on
every Windows computer *and in every Active Directory domain*, so a domain's own
`BUILTIN\Administrators` — which an AD collector reports as `principal_kind=domain_group`,
`group_scope=builtin_local` — occupies the key `S-1-5-32-544`. Collect a second domain the
same way and the two groups merge into one node, and their memberships with them. `a10`
pins the three-way distinction that *does* hold today; the merge across two domains is not
yet reachable because ADG has collected only one.

A second, smaller consequence is reachable today and is pinned in
`tests/db/test_graph_adversarial.py`: once a domain BUILTIN group occupies the bare key,
`GET /principals/S-1-5-32-544` resolves it outright with **200**, rather than the **409**
listing all three candidates that `a10-builtin-scoping-hosts-only` produces. The caller is
handed one group and no hint that two others share the SID. The memberships still do not
merge, which is the part that matters most.

**Why it is not fixed here.** Host-scoping a domain BUILTIN group changes
`Principal.identity_key`, and therefore `principals.principal_key`,
`membership_edges.group_key`, and every `source_key` a collector derives. That is a
versioned contract migration with a data migration behind it, not an edit, and this phase
is explicitly not the place for one.

**What is in place instead.** `scripts\validate-collector-output.ps1` reports
`unscoped_well_known_group` as a **warning** on any collector output that puts a well-known
group SID into the graph without a host, naming the remedy: report it as
`principal_kind=local_group` with `host_key`, and its memberships as
`edge_kind=local_group_member`, whenever the group belongs to one machine.

**Recommendation for the phase that revises the contract.** Scope well-known group SIDs by
their issuing authority — the host for a machine-local group, the domain SID for a domain's
BUILTIN container — so that `S-1-5-32-544` never appears as a bare storage key.

---

## 5. Checking a collector's output

```powershell
# Everything a collector wrote, as a build gate.
.\scripts\validate-collector-output.ps1 -Path .\out -Strict

# One transcript, as JSON, for a pipeline to parse.
.\scripts\validate-collector-output.ps1 -Path .\out\ad-run.json -AsJson
```

Exit **0** when the API would accept every document, **1** when it would not (or, with
`-Strict`, when there are warnings), **2** when there was nothing to read. Nothing is sent
anywhere: the check is entirely offline, which is what lets it run in a collector's own
build as well as in an operator's hands.

Two layers, because they fail differently. The **envelope layer** parses every document
through the same contract models the endpoints use, so a rejection here is the rejection a
collector would get over HTTP — except that it reports *all* of them at once, with the file
and the field, instead of the first one per network round trip. The **graph layer** asks
what no per-observation check can: is the identity graph these observations describe sound?

### Findings

**Errors — the API would reject this, or the documents contradict themselves.**

| Code | Meaning |
| --- | --- |
| `malformed_json` | The file is not valid JSON. |
| `unreadable_file` | The path could not be read. |
| `unexpected_shape` | The document is not an object. |
| `unrecognized_document` | Not a transcript, a start, a batch, or a completion. |
| `invalid_start` | The start envelope is rejected; the message names the field. |
| `invalid_batch` | A batch or one of its observations is rejected. |
| `invalid_completion` | The completion envelope is rejected. |
| `conflicting_start` | One `run_id` started twice with different contents. |
| `reconciled_an_undeclared_scope` | Absence would be inferred outside anything the run claimed. |
| `batch_count_mismatch` | The completion claims more or fewer batches than are present. |
| `observation_count_mismatch` | The completion claims more or fewer observations than were sent. |
| `duplicate_batch_id` | `(run_id, batch_id)` is the idempotency key and it was reused. |
| `multiple_final_batches` | More than one batch ends the run. |
| `unstorable_observation_kind` | A valid contract kind that ingestion cannot persist yet. Read from `app.ingestion.plan.SUPPORTED_KINDS`, so it follows the phases. |

**Warnings — ADG would accept it and then hold something an auditor must be told about.**

| Code | Meaning |
| --- | --- |
| `unscoped_well_known_group` | §4. The one that can merge two organizations' groups. |
| `membership_cycle` | Groups contain each other. Survived and reported, but a directory defect; the finding names the shortest loop to break. |
| `one_sid_several_kinds` | One SID described as a user and a computer, say. Only `local_group` is host-scoped, so the rest share one key and the last written wins. |
| `member_kind_disagrees` | An edge calls a member a group; the member's own observation says otherwise. |
| `conflicting_principal_observations` | **One run** describing one principal two different ways. Two *runs* differing is a rename, not a conflict, and is not reported. |
| `batches_without_a_start` | Observations for a run this input never opened. |
| `run_never_completed` | A run with observations and no completion stays `running` forever. |
| `no_final_batch` | A truncated upload is indistinguishable from a complete one. |
| `batch_sequence_gap` | Sequence numbers are not 1..n, so a missing batch would be invisible. |
| `repeated_source_key` | Safe — ingestion is idempotent — but usually means an object was enumerated twice. |
| `group_wider_than_the_default_limit` | Every default-limit answer for it will be a declared lower bound. |
| `nesting_deeper_than_the_default_limit` | Answers will truncate at the default `max_depth`. |

**Informational.**

| Code | Meaning |
| --- | --- |
| `members_without_a_description` | Edges naming SIDs nothing describes. Legitimate across runs, and the membership is stored either way. |
| `one_name_several_principals` | Expected and handled; worth knowing before anybody searches by name and assumes one result. |
| `deep_nesting` | Within the default depth limit, but deep enough that a reviewer should see it. |

Absence of findings means well formed and self-consistent — **not correct**. The tool cannot
know what a directory actually contains.

---

## 6. Measured cost

> Measured 2026-09-14 on one developer workstation: Windows 11 (AMD64), Python 3.13.14,
> PostgreSQL in Docker Desktop reached over loopback, psycopg 3.3.5, SQLAlchemy 2.0.52.
> **These are shape-of-the-curve numbers, not a production guarantee.** A server with real
> storage, a warm cache, and a network between it and the API will differ in every absolute
> figure; the ratios and the plans are what transfer.

### 6.1 Traversal alone, no database

`.\scripts\graph-benchmark.ps1 -Scale large`

| Shape | Nodes | Provider calls | Wall time | Bytes per node |
| --- | --- | --- | --- | --- |
| Chain, 192 deep (cut at `max_depth` 128) | 128 | 129 | 2.4 ms | 2,134 |
| One group, 100,000 direct members | 100,000 | **2** | 1,766 ms | 754 |
| Layered, 48 levels × 30 wide | 1,488 | 97 | 25.7 ms | 1,885 |
| Ring of 10,000 (cut at depth 128) | 128 | 129 | 2.3 ms | 2,161 |
| Cycle detection over 10,000 edges | — | 0 | 29.8 ms | — |
| 15 stacked diamonds, capped at 1,000 paths | 1,000 | 31 | 2.3 ms | — |

Two things to read out of this.

**One breadth-first level is one provider call, however wide it is.** A group with 100,000
direct members costs two calls, not 100,001. That is the claim the whole batched design
rests on, and it holds.

**`max_nodes` is a memory budget, and bytes per node grows with depth.** A wide, shallow
answer costs about 750 bytes per node; a deep one costs roughly 2,100, because every node
keeps the shortest path that reached it and that path is O(depth) long. At the `max_nodes`
default of 50,000 one answer is therefore ≈ 38–105 MB depending on shape, and at the
250,000 ceiling ≈ 190–525 MB — **per concurrent request**. Raising `max_nodes` towards the
ceiling on a shared deployment is a memory decision, not a patience decision.

### 6.2 Against PostgreSQL

Dataset: **310,001 principals, 400,048 membership edges**, `ANALYZE`d. Median of five runs
after a warm-up.

| Operation | Result size | Median | Best | Worst |
| --- | --- | --- | --- | --- |
| `effective-members` of a 100,000-member group | 100,000 nodes | 4,753 ms | 4,128 ms | 5,169 ms |
| `effective-groups` up a 48-level chain | 48 nodes | 31 ms | 30 ms | 48 ms |
| `membership-paths` between two principals | 1 path | 31 ms | 30 ms | 32 ms |
| `direct-members`, one 500-row page | 500 rows | 51 ms | 50 ms | 53 ms |
| `count_direct` on a 100,000-member group | 1 row | 19 ms | 19 ms | 20 ms |
| `resolve` by SID | 1 row | 1.2 ms | 1.1 ms | 1.8 ms |

### 6.3 Query plans

`EXPLAIN (ANALYZE, BUFFERS)` on the same dataset. Captured by the benchmark itself, so
these can be re-produced rather than trusted.

| Query | Plan | Server time |
| --- | --- | --- |
| Adjacency, one key | Index Scan `ix_membership_edges_group` → Incremental Sort | 0.51 ms |
| Adjacency, 5,000-key frontier | Index Scan (`= ANY`) → Incremental Sort; planning 1.5 ms | 1.36 ms |
| Direct-members page | Index Scan `ix_membership_edges_group` → Incremental Sort | 0.25 ms |
| Count direct members | **Index Only Scan**, Heap Fetches 0, 100,000 rows | 9.40 ms |

**No index change is needed, and one obvious change was measured and declined.** Both
traversal indexes are used, in both directions, with no sequential scan anywhere. The
`Incremental Sort` appears because `ix_membership_edges_group` is `(group_key, member_key)`
while the query orders by `(group_key, member_key, edge_key)` — the index supplies the
first two and only the tiebreak is sorted, in 32 groups of 41 kB. Adding `edge_key` to the
index would remove it and would widen every entry of the largest index in the schema to buy
back well under a millisecond. Not worth it. (The tiebreak itself is worth keeping: it is
what makes a truncated fetch truncate the same way twice.)

`count_direct` is an index-only scan with no heap fetches, which is as good as it gets, but
it is still O(rows): 9.4 ms for 100,000 members, and linear beyond that. `GET
/principals/{id}` issues two of them. A group with millions of members would make principal
lookup slow — recorded rather than solved, because no such group exists yet to measure.

### 6.4 The database is not the bottleneck

The 500-row page takes 51 ms end to end, of which the edge query is **0.25 ms**. Broken
down:

| Step | Time |
| --- | --- |
| The edge query, 501 rows | 2 ms |
| Labelling those 500 members (`principals_by_keys`) | **47 ms** |
| — of which PostgreSQL executes | 0.86 ms |

The labelling query's server time is under a millisecond and the client sees 47. It is not
the columns selected and it is not the row count: a 500-row result with no array parameter
comes back in 1.5 ms.

**It tracks the size of the request, not the number of keys in it:**

| Elements | Key length | Request size | Best time |
| --- | --- | --- | --- |
| 100 | 48 | 4.7 KiB | 1.2 ms |
| 150 | 48 | 7.0 KiB | 1.2 ms |
| 200 | 48 | 9.4 KiB | **42.9 ms** |
| 300 | 48 | 14.1 KiB | 43.8 ms |
| 500 | 48 | 23.4 KiB | 44.3 ms |
| 500 | 2 | 1.8 KiB | 1.8 ms |
| 2,000 | 2 | 8.7 KiB | **46.5 ms** |
| 5,000 | 2 | 23.3 KiB | 51.1 ms |

A flat ~42 ms penalty appears once the request crosses roughly 8 KiB, and barely grows
after that. Five hundred short keys are fast; two thousand short keys are slow; the
crossover is in bytes. That is the signature of a socket-level stall on this loopback
connection, not of anything in the schema or the query.

**This is why `effective-members` over 100,000 nodes takes 4.75 seconds** while every query
inside it runs in single-digit milliseconds: the traversal itself is 1.8 s (§6.1), the
labelling is 20 chunks of 5,000 keys at ~58 ms each ≈ 1.2 s, and the rest is per-node
object construction.

**No change is being made on the strength of this.** Shrinking `KEY_CHUNK` to stay under
8 KiB would trade 20 stalled requests for roughly 1,000 fast ones — about the same total —
and the measurement is from one machine's loopback with one driver. Confirm it against a
real deployment before designing around it. What *is* actionable is the ratio: the cost of
a large membership answer is in the client and the round trips, not in PostgreSQL, so a
cache at the `AdjacencyProvider` seam (ADR-0006) would pay off where a schema change would
not.

---

## 7. Practical limits

| Question | Answer |
| --- | --- |
| How deep can nesting be? | 32 hops by default, 128 at the ceiling. Twenty-four levels — far past anything sane in a real directory — is answered completely at the default. |
| How wide can a group be? | 50,000 members by default, 250,000 at the ceiling, one query per level regardless. Past that the answer is a declared lower bound. |
| How many paths? | 100 by default, 1,000 at the ceiling. The count is exponential in the branch points: fifteen stacked "two ways up" choices already give 32,768. |
| What does a cycle cost? | Nothing in correctness and ~30 ms of Tarjan over 10,000 edges. A cyclic graph is still a complete answer. |
| What actually limits a large answer? | Memory and client-side round trips, not the database. See §6.1 and §6.4. |
| How large a directory has been tested? | 310,001 principals and 400,048 edges against PostgreSQL; 100,000 nodes in a single traversal in memory. Nothing larger has been measured, and nothing larger should be claimed. |

---

## 8. What is still open

1. **Well-known group SIDs are scoped only when reported as local groups** (§4). Detected,
   documented, not fixed; the fix is a contract revision.
2. **A bare BUILTIN SID that is also a stored key resolves outright**, hiding the other
   candidates (§4). Memberships do not merge.
3. **`count_direct` is linear in the membership**, and `GET /principals/{id}` issues two of
   them.
4. **Nothing is cached.** Every effective-members answer walks the graph again. §6.4 says
   where a cache would pay.
5. **Ordinary re-ingestion is not benchmarked.** The benchmark seeds rows directly, because
   it measures the query path; the write path's cost at scale is unmeasured.
6. **The measurements are from one workstation over loopback** (§6). Re-run
   `.\scripts\graph-benchmark.ps1 -Scale large -Database` on representative hardware and
   update §6 before anybody plans capacity from it.
7. **The smoke-test database is shared between concurrent runs.** `tests/db` derives its
   database from `ADG_DATABASE_URL` plus `_test` and truncates every table before each
   test, so two suites running at once on one machine delete each other's rows. Point
   `ADG_DATABASE_URL` at a different database name to run in parallel.
