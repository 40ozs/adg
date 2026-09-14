# Handoff — Phase 1B (`phase-01/02-ad-ingestion-graph.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-00b-contracts.md](phase-00b-contracts.md)
**Related, landed concurrently:** [phase-01a-ad-collector.md](phase-01a-ad-collector.md),
[phase-02a-smb-collector.md](phase-02a-smb-collector.md),
[phase-04a-rights-model.md](phase-04a-rights-model.md)

## Scope completed

Gave the Phase 0B contract somewhere to land, and made the stored edges answerable.

1. **Eight tables** for AD principals, name aliases, membership edges, collector sources,
   scan runs, declared/reconciled scopes, applied batches, run errors, and per-run
   observations — declared once in `backend/app/models/schema.py` and created by migration
   `0002_ad_graph`.
2. **The four collector-protocol endpoints**, idempotent as specified: start, batch,
   completion, inspect. This phase stores `principal` and `membership_edge` observations;
   the other five contract kinds are **rejected with an actionable 422**, not silently
   dropped.
3. **A bounded, cycle-safe traversal** in `backend/app/domain/graph.py` — breadth-first
   expansion, simple-path enumeration, and Tarjan cycle detection — depending on nothing but
   an `AdjacencyProvider` protocol, so all of it is testable without a database.
4. **Five graph endpoints**: principal lookup, direct members, effective members, containing
   groups (direct or effective), and every membership path between a principal and a group.
5. **`traversal.complete` on every recursive answer**, with the reasons a traversal stopped.
   A truncated result is a declared lower bound, never a membership list.
6. **180 new tests** — 111 hermetic, 69 against a real PostgreSQL — plus a PostgreSQL
   service in CI so the database half is a gate rather than a local nicety.

## Files and modules added or materially changed

### Schema and migration

| File | Contents |
| --- | --- |
| `backend/app/models/schema.py` | **New.** The single declaration of all eight tables: columns, check constraints generated from the domain enums, and the indexes traversal needs. |
| `database/migrations/versions/0002_ad_graph.py` | **New.** Generated from that declaration; `alembic check` reports no drift and the downgrade round-trips. |
| `database/migrations/env.py` | Changed: `target_metadata` now points at the schema module, so `alembic check` and autogenerate are meaningful. |

### Ingestion

| Module | Contents |
| --- | --- |
| `backend/app/ingestion/plan.py` | **New, pure.** Contract batch → the exact rows to write. Every key is asked of the domain object (`Principal.identity_key`, `MembershipEdge.identity_key`) rather than formatted, so storage keys and contract `source_key`s cannot drift. Raises `UnsupportedObservationKind` naming the offending kinds. |
| `backend/app/ingestion/service.py` | **New.** The run lifecycle against PostgreSQL: source upsert, run start with conflict detection, batch claim-and-apply, newest-wins upserts, completion with downgrade and reconciliation rules. |

### Query path

| Module | Contents |
| --- | --- |
| `backend/app/domain/graph.py` | **New.** `expand`, `find_paths`, `find_cycles`, `TraversalLimits`, `Expansion`, `MembershipPath`, `GraphCycle`. No SQL, no HTTP, no ORM. |
| `backend/app/repositories/membership.py` | **New.** Principal resolution, direct listings, alias lookup, and the `AdjacencyProvider` implementation — one batched array query per breadth-first level. |
| `backend/app/services/graph.py` | **New.** Joins traversal results to stored principals and applies the `include` filter. |
| `backend/app/api/graph.py` | **New.** The five query endpoints and their response models. |
| `backend/app/api/scan_runs.py` | **New.** The four ingestion endpoints. |
| `backend/app/api/pagination.py` | **New.** Keyset and offset cursors, validated and endpoint-specific. |
| `backend/app/api/deps.py` | **New.** Per-request session, traversal-limit parsing, and a repository whose row ceiling matches the traversal's edge budget. |
| `backend/app/api/__init__.py` | Changed: two routers attached. |
| `backend/app/domain/__init__.py` | Changed: graph types exported. |
| `backend/app/main.py` | Changed: exception handlers mapping `DomainValidationError` → 422, `IngestionConflict` → 409, `RunNotFound` → 404, `InvalidCursor` → 422. |

### Tests

`backend/tests/domain/test_graph.py` (43), `test_graph_scale.py` (15),
`backend/tests/ingestion/test_plan.py` (23), `backend/tests/api/test_pagination.py` (30),
`backend/tests/db/{conftest,test_schema,test_ingestion,test_graph_api}.py` (69),
`backend/tests/support/{graph,ingest}.py` (in-memory graphs and fixture replay helpers).

### Docs and CI

`docs/architecture/membership-graph.md` (new), `docs/decisions/0006-bounded-in-process-traversal.md`
(new), `docs/decisions/README.md`, `docs/architecture/system-overview.md`, `README.md`,
`.github/workflows/ci.yml`.

## Important architecture decisions

1. **Traversal is in-process and bounded, not a recursive CTE.** Recorded as
   [ADR-0006](../decisions/0006-bounded-in-process-traversal.md). A CTE returns a flat row
   set, so per-node shortest paths, all-simple-paths, and strongly connected components
   would be post-processed in Python anyway — and every limit and cycle case would then be
   testable only with a database. Cost is comparable: one indexed query per breadth-first
   level, not one per node.
2. **A truncated answer says so.** Every recursive response carries
   `traversal.complete` and `traversal.truncation`. An audit tool that returns a short
   member list as if it were the whole one understates access, which is the most dangerous
   wrong answer it can give. `is_member: false` on a truncated path search means *unknown*.
   Reaching a limit with nothing beyond it costs one extra batched lookup and is reported as
   **complete**, so a precise bound is not mistaken for a hit one.
3. **Identity is the domain's key string, not a surrogate id.** `principals.principal_key`
   is `Principal.identity_key` and `membership_edges.group_key`/`member_key` are the
   matching values, so traversal joins on exactly what the domain produces. A local group
   stays `host|sid`; a bare BUILTIN SID matching several hosts is answered **409 with the
   candidates**, never by picking one.
4. **An unstorable observation kind is rejected, never dropped.** A collector told
   "accepted" about an observation that was discarded would go on to report coverage ADG
   does not hold. The 422 names the kinds and says a later phase persists them.
5. **Upserts are newest-wins, with a separate first-observed rule.** Written as `CASE`
   expressions rather than a conflict `WHERE`, so a late-arriving *older* run cannot
   overwrite newer names or a newer `is_deleted`, but can still push `first_observed_at`
   further back — which is genuinely new information.
6. **The default effective-members filter keeps unlabelled SIDs.** `include=non_groups`
   returns principals with `kind: null`, `resolved: false`, and `is_group: null`. A SID
   that demonstrably sits inside a group but that nothing has described is exactly the
   finding this tool exists to surface; filtering it away for want of a label would hide it.
   `include=users` is available when a caller really does want accounts only.
7. **Two pagination styles, honestly labelled.** Keyset for direct listings (a group's
   membership changes while it is paged, and a skipped member is a missed finding); offset
   for recursive results, which have no index to seek into. Cursors are endpoint-specific
   and a foreign or malformed one is a 422, because silently restarting at page one would
   make a client's second page look like a complete result set.
8. **Current state and provenance are separate tables.** `principals` and
   `membership_edges` hold latest-known state; `observations` holds one row per
   `(run_id, source_key)`. That primary key *is* the contract's idempotency guarantee, and
   it is what will let Phase 7 answer "which run saw this, and when".
9. **Enumerated columns are `text` with check constraints generated from the domain enums**,
   so adding a value later is a constraint change rather than a locking type migration, and
   the authoritative list stays in `app.domain`.

## Schemas and contracts introduced or changed

**No contract change.** `docs/contracts/v1/` is untouched; this phase implements the
endpoints Phase 0B specified, with the status codes it specified.

New database schema at revision `0002_ad_graph`:

| Table | Key | Purpose |
| --- | --- | --- |
| `collector_sources` | sha256 `fingerprint` | Distinct (collector, host, method, version, target) tuples. |
| `scan_runs` | `run_id` | One execution of one collector. |
| `scan_run_scopes` | `(run_id, kind, key)` | Declared coverage and what was reconciled. |
| `scan_run_batches` | `(run_id, batch_id)` | Applied batches — the idempotency guarantee. |
| `scan_run_errors` | surrogate | What a collector could not read. |
| `principals` | `principal_key` | Latest known state of every principal. |
| `principal_aliases` | `(principal_key, alias_kind, value_folded)` | Every name ever observed. |
| `membership_edges` | `edge_key` | One row per observed direct relationship. |
| `observations` | `(run_id, source_key)` | Provenance for every stored object. |

New API surface, all under `/api/v1`: `POST /scan-runs`, `POST /scan-runs/{id}/batches`,
`POST /scan-runs/{id}/completion`, `GET /scan-runs/{id}`, `GET /principals/{id}`,
`GET /groups/{id}/members`, `GET /groups/{id}/effective-members`,
`GET /principals/{id}/groups`, `GET /principals/{id}/membership-paths`.

Traversal limits (query parameters, clamped to ceilings, reported back):
`max_depth` 32/128, `max_nodes` 50,000/250,000, `max_edges` 200,000/1,000,000,
`max_paths` 100/1,000. Page size: default 100, maximum 500.

## Tests run and exact results

| Command | Result |
| --- | --- |
| `python -m pytest -q -m "not smoke"` | **900 passed, 1 skipped, 71 deselected** in 7.65s |
| `ADG_RUN_SMOKE_TESTS=1 python -m pytest -q` | **971 passed, 1 skipped** in 25.60s |
| `python -m pytest tests/db -q` (smoke enabled) | **69 passed** in 17.40s |
| `python -m pytest tests/domain/test_graph*.py tests/ingestion tests/api -q` | **111 passed** in 0.42s |
| `python -m ruff check .` | **All checks passed** |
| `python -m ruff format --check .` | **88 files already formatted** |
| `python -m mypy app tests` | **Success: no issues found in 87 source files** (strict) |
| `python -m alembic upgrade head` / `downgrade 0001_baseline` / `upgrade head` | Applied and reversed cleanly |
| `python -m alembic check` | **No new upgrade operations detected** |
| Relative Markdown link check, whole repository | **0 broken links** |

The one skip is the pre-existing `common.schema.json` case, which carries no embedded
examples. Smoke tests run against a separate `adg_test` database, created and migrated by
the suite, so a developer's own data is never truncated.

**Four defects were found by running the tests, not by inspection:**

1. **`extra={"created": ...}` crashed every successful run start.** `created` is a reserved
   `LogRecord` attribute and the logging module raises rather than overwrite it, so
   `POST /api/v1/scan-runs` returned 500 after writing the run. Renamed to `run_created`.
2. **A traversal that reached exactly `max_depth` reported itself truncated even when
   nothing lay beyond it**, turning complete member lists into unusable lower bounds. Now
   one extra batched lookup distinguishes an exhausted frontier from a real cut-off.
3. **`str(URL)` masks the password**, so every test-database connection failed
   authentication for no visible reason. Rendering now opts out explicitly.
4. **A joined direct-listing query resolved eight ambiguous column names** (`host_key`,
   `source_key`, the four observation columns, `created_at`, `updated_at` exist on both
   tables) to whichever table came last. Replaced with two queries.

## Known limitations

1. **Only two of the seven observation kinds are stored.** `server`, `smb_share`,
   `smb_ace`, `ntfs_resource`, and `ntfs_ace` are rejected with 422. A collector that
   batches AD facts together with share facts must split them by kind until the SMB/NTFS
   ingestion phase lands.
2. **No authentication anywhere.** Every endpoint is open, including the ingestion writes.
   OIDC/Entra arrives in the auth phase; until then the API must not be exposed beyond a
   trusted network.
3. **Recursive pagination is not a stable snapshot.** Each page re-runs the traversal, so a
   membership change between pages can shift results. The `traversal` block travels with
   every page, but a caller wanting a consistent large export should raise the page size
   rather than page.
4. **Nothing is ever marked absent.** There is no delete path. Reconciled scopes are
   recorded as evidence; acting on them is Phase 7.
5. **Batch application is not serialized against completion.** A batch committing at the
   same instant as a completion can be counted after the status is written. The run's
   `batch_count` check catches the material case (fewer batches received than sent
   downgrades the run to `partial`), and completion takes a row lock, but two collectors
   racing on one run remain outside the contract.
6. **`edges_read` is per-request, not per-traversal-step.** It reports what the repository
   fetched for the whole request, which for a path search includes the expansion that fed it.
7. **No query result is cached.** Every effective-members answer walks the graph again.
   Acceptable at current scale; the `AdjacencyProvider` seam is where a cache goes.
8. **Aliases accumulate without bound.** A principal renamed weekly grows one alias row per
   distinct name. Intentional — name history is evidence — but it has no pruning policy.
9. **`GET /principals/{id}` counts direct degree with two count queries.** Fine against the
   index; it would want revisiting if principal lookup becomes a bulk operation.
10. **`observations` rows are never pruned.** One row per object per run, growing linearly
    with scan frequency. Retention belongs with history in Phase 7.

## Security and privilege assumptions

- **Unchanged posture: read-only, least privilege.** Nothing in this phase reads a target
  system. No endpoint can change a permission; there is no payload in contract v1 that
  could express one.
- **The database cannot represent an unsafe fact.** Check constraints refuse an unscoped
  local group, a self-edge, an unresolved principal carrying a display name, and any value
  outside the domain enums — enforced by PostgreSQL, so bypassing the application layer
  does not bypass the invariant.
- **Under-reported coverage is still structurally prevented.** A run with errors cannot be
  `succeeded`; a run that sent more batches than arrived is downgraded to `partial` with the
  reason recorded; a partial or incremental run cannot reconcile; reconciling an undeclared
  scope is a 409.
- **Ambiguity is never resolved by guessing.** A BUILTIN SID matching several hosts returns
  409 with the candidates rather than merging one server's local administrators into
  another's.
- **Truncation is never silent**, and a truncated path search reports `is_member: false`
  only alongside `complete: false`, so "unknown" cannot be read as "no access".
- **No credentials are logged.** The connection string is never echoed; cursors carry only
  a key the caller already holds and grant nothing.
- **Endpoints are unauthenticated.** This is the phase's largest open risk; see limitation 2.

## Migration and compatibility notes

- **Additive.** Revision `0002_ad_graph` follows `0001_baseline`; the downgrade drops only
  what it created and was exercised. Run `python -m alembic upgrade head` from `backend\`.
- **No contract change**, so every Phase 0B collector payload is accepted unchanged —
  subject to the kind restriction in limitation 1.
- **`database/migrations/env.py` now imports `app.models.schema`.** Migrations must be run
  with the backend package importable, which `alembic.ini`'s `prepend_sys_path = .` already
  arranges when running from `backend\`.
- **No new dependencies.** SQLAlchemy, psycopg, and Alembic were already pinned.
- **CI now starts PostgreSQL** and runs `pytest -m smoke` as a separate step.
- **Storage keys are load-bearing.** `principal_key` and `edge_key` are the domain identity
  keys; changing a derivation changes every stored object's identity and requires a data
  migration, not an edit.
- `alembic revision --autogenerate` must be run with an absolute `script_location`: Mako
  refuses the relative `../database/migrations` template path. Copy `alembic.ini`, rewrite
  that one line, and pass it with `-c`. Plain `upgrade`/`downgrade`/`check` are unaffected.

## Prerequisites for the next prompt

1. Read [`docs/architecture/membership-graph.md`](../architecture/membership-graph.md) and
   [ADR-0006](../decisions/0006-bounded-in-process-traversal.md) before touching the query
   path. The `traversal.complete` contract is the part that must not be eroded.
2. **SMB/NTFS ingestion extends `plan_batch`, it does not replace it.** Add the five
   remaining kinds to `SUPPORTED_KINDS` with their own row builders and tables; the run
   lifecycle, batch idempotency, and `observations` provenance already work for them.
3. Derive every new storage key by asking the domain object, as `plan.py` does. A key
   formatted by hand will drift from `app/contracts/v1/keys.py`.
4. Declare new tables in `backend/app/models/schema.py` and generate the migration from it;
   `tests/db/test_schema.py` will fail if the two diverge.
5. Replay fixtures rather than inventing data: `tests/support/ingest.py` has the helpers,
   and `ad_only()` can be relaxed as kinds become storable.
6. The effective-access engine should consume `GraphService`, not re-walk the edges, and
   must propagate `traversal.complete` into whatever it produces — an access answer computed
   from a truncated membership is itself a lower bound.
7. Authentication is the highest-value next piece of work outside the phase plan: the
   ingestion endpoints currently accept observations from anyone who can reach the port.
8. New code must pass `.\scripts\backend-lint.ps1` (ruff + **mypy strict**),
   `.\scripts\backend-test.ps1`, and `.\scripts\backend-test.ps1 -Smoke` with the stack up
   (`.\scripts\stack-up.ps1 -DbOnly`).

## `git status --short`

Captured immediately before the phase commit. `backend/tests/contracts/test_smb_collector.py`
belongs to concurrent work in another session and was **not** staged:

```text
 M .github/workflows/ci.yml
 M README.md
 M backend/app/api/__init__.py
 M backend/app/domain/__init__.py
 M backend/app/main.py
 M backend/tests/contracts/test_smb_collector.py
 M database/migrations/env.py
 M docs/architecture/system-overview.md
 M docs/decisions/README.md
?? backend/app/api/deps.py
?? backend/app/api/graph.py
?? backend/app/api/pagination.py
?? backend/app/api/scan_runs.py
?? backend/app/domain/graph.py
?? backend/app/ingestion/
?? backend/app/models/schema.py
?? backend/app/repositories/
?? backend/app/services/
?? backend/tests/api/
?? backend/tests/db/
?? backend/tests/domain/test_graph.py
?? backend/tests/domain/test_graph_scale.py
?? backend/tests/ingestion/
?? backend/tests/support/
?? database/migrations/versions/0002_ad_graph.py
?? docs/architecture/membership-graph.md
?? docs/decisions/0006-bounded-in-process-traversal.md
```
