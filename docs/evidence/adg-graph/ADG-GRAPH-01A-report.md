# ADG-GRAPH-01A: graph domain model and evidence contract

**Verdict: `READY_FOR_GRAPH_DERIVATION`**

The relationship layer's domain types, schema (revision `0018`) and minimal internal repository
are implemented and tested, for every requirement of the lane. Nothing derives relationships yet,
and several taxonomy types have no collected premises. Both are stated as part of the model
rather than hidden (see Limitations).

## 1. Git

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| Branch | `feature/ADG-GRAPH-01A-domain-model` |
| Start SHA | `3e26e4ad3fd351b5a8e956ae392ce8088ac485d1` (`origin/integration/ADG-NEXT16-UI-02`, = `origin/deploy/ADG-FREE-STAGING-01`) |
| `origin/main` at start | `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af` (an ancestor of the start SHA) |
| Predecessor lane | none: GRAPH-01A is the first lane of ADG-GRAPH-01 |
| Implementation commit | `20f89e1276bcf898dd36bb0477ff2a9c7b7fad8a` |
| Final SHA | the commit adding this report and the status file; recorded in `ADG-GRAPH-01-STATUS.md` and in the lane return. `git rev-parse origin/feature/ADG-GRAPH-01A-domain-model` is authoritative. |
| Merged to `main` | **no** (per the lane) |

**Why this base.** `git fetch --all --prune` found 12 remote branches and no GRAPH branch,
report or status file on any of them (`docs/evidence` did not exist anywhere). The newest
verified line of work, `integration/ADG-NEXT16-UI-02@3e26e4a`, contains `main` and every other
branch. The one exception is a single docs-only commit on `integration/ADG-UI-EDGE-01` (`4212f13`, a
superseded integration report). Its handoff verdict is `READY_FOR_ADG_COMBINED_STAGING_REVIEW`
and it is the live staging tree. Branching from `main` would have built the graph on a tree 26
commits behind what is deployed, and the graph epic's later UI lanes depend on that UI work.

**Consequence for the epic's final integration.** This branch, and every later GRAPH branch
chained from it, carries those 26 not-yet-merged UI/deploy commits. Merging the finished graph
epic into `main` also merges them, unless `integration/ADG-NEXT16-UI-02` reaches `main` first.
The integration lane must decide that ordering explicitly.

## 2. Environment

| | |
| --- | --- |
| Platform | Linux container (Claude Code cloud), not Windows |
| Python | 3.13.16 (CI uses 3.13) |
| PostgreSQL | 16, local cluster started for this session (CI uses `postgres:16-alpine`) |
| PowerShell / Pester | **not available** (`pwsh` absent): see Limitations |
| AD domain, SMB server, production systems | none contacted. Fixtures and synthetic scenarios only |

## 3. Reconciliation with what exists

Searched before designing: the membership graph (`app/domain/graph.py`, ADR-0002/0006), the
effective-access engine (`app/access_engine`), the resource inventory, the risk engine (its
`Confidence` vocabulary), the history model (`object_versions`, ADR-0018) and current-state
presence (`app/models/current.py`), simulation, collector contracts (`ObservationKind`,
`ScopeKind`), demo mode, and the identity rules (`Principal.identity_key`,
`referenced_principal_key`).

**No graph relationship layer existed.** The membership traversal is a specialized, query-time
answer over `membership_edges` and is left untouched. `nested_member_of` names it in the
taxonomy (`derivation: query_time`) instead of re-implementing it.

**Ponytail.** The repository has no Ponytail tool. `docs/handoffs/adg-ui-update-01.md` §3 defines
it as the lazy-reuse rules, and they were applied as such: node keys are the existing canonical
primary keys; evidence kinds are the contract's `ObservationKind`; currency is
`app.models.current`'s presence rule (`CURRENT_STATE_KEYS`); attribution is
`app.domain.basis`'s token; check constraints are generated with the schema's `_enum_check`;
confidence reuses the risk engine's value names; and tests replay the existing scenario
transcripts through the real ingestion API. No dependency was added.

## 4. What was built

| File | Content |
| --- | --- |
| `backend/app/domain/relationships.py` | vocabulary, 18-type taxonomy, `NodeRef`, `RelationshipAssertion` with all invariants, stored records, the confidence and currency folds, explanation, deterministic serialization |
| `backend/app/models/schema.py` | `graph_relationships`, `graph_relationship_supports`, `graph_relationship_evidence`; constraints generated from the domain |
| `database/migrations/versions/0018_graph_relationships.py` | additive revision; downgrade drops only these three tables |
| `backend/app/repositories/relationships.py` | `record`, `get`/`get_many`, `adjacent`, `supported_by` |
| `docs/architecture/graph-relationships.md` | schema, object model, taxonomy, provenance, completeness, deferrals |
| `docs/decisions/0040-…md` | ADR-0040 (0033 was taken) |

### Migrations

`0018_graph_relationships` (revises `0017_deployment_marker`): three empty tables, 24 check
constraints (including the database's own recomputation of the relationship id), 1 unique
constraint, FKs to `scan_runs` and between the three tables, and 9 indexes. No existing object is
touched and nothing is backfilled. Verified over a populated database (§6).

### Schema, object model, taxonomy, provenance and completeness

Specified in full in `docs/architecture/graph-relationships.md`, with ADR-0040 for the decision. In
brief:

* **Object model.** A node is `(kind, existing canonical key)` for principals, servers, shares, NTFS
  resources and domains (domain SID). `gpo` and `directory_container` are reserved key forms,
  the only new canonical types, with no table and no collector.
* **Relationship = identity; support = one derivation; evidence = the stored facts.** The id is a
  SHA-256 of type, endpoints and scope, recomputed by the database. A relationship is as strong
  as its strongest current support.
* **Taxonomy** (18 types): `member_of`, `nested_member_of` (traversal only, never stored),
  `primary_group_of`, `local_group_member_of`, `local_admin_of`, `remote_desktop_user_of`,
  `remote_management_user_of`, `owns`, `generic_all`, `generic_write`, `write_dacl`,
  `write_owner`, `can_modify_membership`, `controls_gpo`, `gpo_linked_to`, `gpo_applies_to`,
  `trusts`, `foreign_principal_of`. Definition, collection and derivation are three separate
  statements per type.
* **Derivation classes.** The four classes are `observed`, `deterministically_derived`,
  `conditionally_derived` and `unresolved`. Each invariant is enforced in the domain and again
  in the database.
* **Completeness.** Qualifiers and conditions are closed vocabularies, each with a sentence.
  Confidence (`confirmed`/`probable`/`possible`/`indeterminate`) is computed and ordinal.
  Currency (`current`/`not_current`/`unknown`) is computed from the presence rule;
  unknown is never folded into not current.

### Collection support, stated honestly

| Status | Types |
| --- | --- |
| collected | `member_of`, `nested_member_of` (via traversal), `primary_group_of`, `foreign_principal_of` |
| partially collected | `owns` (NTFS owner only); `generic_all`, `generic_write`, `write_dacl`, `write_owner` (NTFS and share ACEs only; `write_dacl` also from NTFS ownership) |
| contract only | `local_group_member_of`, `local_admin_of`, `remote_desktop_user_of`, `remote_management_user_of` |
| not collected | `can_modify_membership`, `controls_gpo`, `gpo_linked_to`, `gpo_applies_to`, `trusts` |

Derivation support: `nested_member_of` is `query_time` (existing traversal). **Every other type
is `not_implemented`.** This lane derives nothing.

## 5. Findings made while building

1. **A NULL-passing CHECK.** The first host-scope constraint accepted a host-local endpoint
   with *no* scope, because `scope_kind = 'host'` is NULL there and PostgreSQL accepts a CHECK
   that evaluates to NULL. A DB test caught it. The fix wraps the predicate in
   `coalesce(…, false)` in both the schema and the migration. Every other new constraint was
   audited for the same failure.
2. **`write_dacl` needed a second premise kind.** An NTFS owner is implicitly granted
   `WRITE_DAC` unless `OWNER RIGHTS` restricts it, so a support resting on the resource's owner is
   legitimate. The taxonomy refused it until `ntfs_resource` was added for that type alone.
3. **Overlapping reconciled scans tombstone each other's facts.** Scenarios 03 and 09 both
   reconcile `\\fs01\finance`, so whichever is ingested second records the other's in-scope
   facts absent. That is correct behavior, and it is exactly what the currency model reads. The
   test fixture orders them deliberately and documents why.

## 6. Tests

| Gate | Baseline (`3e26e4a`) | After (`20f89e1`) |
| --- | --- | --- |
| `ruff check .` / `ruff format --check .` | clean / clean | clean / clean (405 files) |
| `mypy app tests` | clean | clean (404 source files) |
| unit `pytest -m "not smoke"` | 5,777 passed, 124 skipped | 5,864 passed, 124 skipped (+87 new) |
| database `pytest -m smoke` (PostgreSQL 16) | 1,240 passed, 1 xfailed | 1,277 passed, 1 xfailed (+37 new)¹ |
| GitHub Actions CI on `20f89e1` | n/a | [run 37](https://github.com/40ozs/adg/actions/runs/38576306642): backend (lint, types, unit, database), frontend, collector (Pester, `windows-latest`), compose. **All green.** |

¹ The local run reported 1,275 passed and 2 failed. Both failures were
`tests/test_database_smoke.py`, which connects to the *base* database named by
`ADG_DATABASE_URL`. That run used a fresh base name (`adgfull`) so that it could run beside the
baseline without sharing test databases, and that database had not been created. After it was
created, both tests passed. CI's database job, run on its own database, is green.

New tests:

* `tests/domain/test_relationships.py` (87): taxonomy completeness and honesty, canonical node
  keys, **stable identity**, **host scoping**, **type validation**, **evidence linkage**,
  **observed vs. derived semantics**, the confidence and currency folds, **unresolved
  relationships**, and **deterministic serialization**.
* `tests/db/test_graph_relationships.py` (36). These run over scenarios 03 and 09 ingested
  through the API:
  * the database recomputes the id;
  * evidence reaches its `observations` row and `object_versions` timeline;
  * unstored evidence is refused;
  * reverse lookup;
  * **duplicate observation handling**: replayed, extended, backdated, stale and rederived;
  * two supports of one relationship, one ended by a real reconciliation through the history
    writer;
  * missing vs. absent;
  * host scope listing;
  * adjacency in both directions with keyset paging;
  * every new check constraint and the run FK biting;
  * cascade deletes only derived rows;
  * the migration's literals equal the domain vocabulary.
* `tests/db/test_graph_relationship_migration.py` (1): **migration from a populated schema**.
  1. A fresh database is built at `0017` and filled through the ingestion API with scenarios
     03, 05, 06 and 09 (a nested chain, a cycle, an orphaned SID and a host-local group).
  2. Every pre-existing table is fingerprinted row by row (count + md5).
  3. `upgrade heads` changes no fingerprint, adds exactly the three tables, and backfills
     nothing.
  4. A relationship over the existing facts records and reads back as current.
  5. `downgrade 0017` restores the fingerprints exactly, and a second upgrade succeeds.

Mutation checks:

| Change | Result |
| --- | --- |
| a migration that rewrites one existing row | migration test fails |
| host-scope validation removed | 5 domain tests fail |
| `missing` folded into `not_current` | 2 domain tests fail |

## 7. Limitations

* **No derivation.** Nothing populates the store; ADG-GRAPH-01B owns the projector rules,
  including retiring a support that no rule produces any longer.
* **Collection gaps are real.** Directory-object ACLs and owners, GPOs, trusts and OU placement
  are not collected, so five types cannot hold a relationship. ACL-right types cover NTFS and
  share entries only. No shipped collector emits local-group edges (`contract_only`).
* **ACL-right supports state grants, not effective access.** Deny, inheritance and token
  filtering stay with the access engine and appear as conditions when unevaluated.
* **No public API, no traversal over relationships, no UI.** Not in scope for this lane. Any
  later visualization needs an accessible structured equivalent (contract §12).
* **Collector (Pester) and frontend gates were not run locally.** Pester needs Windows and
  PowerShell, which this environment lacks. Both gates ran in GitHub Actions on `20f89e1` and
  passed: Pester on `windows-latest`. This lane changes no collector or frontend code.
* **Branch base includes unmerged UI work** (§1). The graph epic's final integration must order
  itself against `integration/ADG-NEXT16-UI-02`.

## 8. Explicitly deferred relationships

`can_modify_membership`, `controls_gpo`, `gpo_linked_to`, `gpo_applies_to` and `trusts` are
defined and uncollected. The directory-object targets of `owns`, `generic_*`, `write_dacl` and
`write_owner` are defined and uncollected. `nested_member_of` stays a query-time traversal and
will never be stored.

## 9. Next lane

`ADG-GRAPH-01B` (relationship derivation), branched as
`feature/ADG-GRAPH-01B-relationship-derivation` from the final SHA of
`origin/feature/ADG-GRAPH-01A-domain-model` recorded in the status file.
