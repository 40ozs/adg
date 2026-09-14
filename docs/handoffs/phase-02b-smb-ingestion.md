# Handoff — Phase 2B (`phase-02/02-smb-ingestion-api.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-01b-ad-graph.md](phase-01b-ad-graph.md)
**Collector this consumes:** [phase-02a-smb-collector.md](phase-02a-smb-collector.md)

## Scope completed

Gave the Phase 2A SMB collector somewhere to land, and made `\\server\share` answerable.

1. **Four tables** for servers, SMB shares, raw share-level ACEs, and the references those
   ACEs make to principals — declared in `backend/app/models/schema.py` and created by
   migration `0003_smb_resources`.
2. **Three more observation kinds accepted** by the existing ingestion endpoints:
   `server`, `smb_share`, `smb_ace`. `plan_batch` was extended, not replaced, so batch
   idempotency, the run lifecycle, and `observations` provenance carry over unchanged. The
   two NTFS kinds are still **rejected with an actionable 422** that now also lists what the
   endpoint does store.
3. **Six resource endpoints**: servers, one server, shares by server, share detail, a
   share's raw ACL, and the shares referencing a SID.
4. **Raw ACL facts labelled as raw.** Both ACL responses carry `kind: "raw_smb_acl"`, and
   nothing in this phase combines layers, expands groups, or converts a permission level to
   a mask.
5. **A deterministic share identifier** in the domain layer: every spelling
   `parse_unc_path` canonicalizes resolves to one key, and a folder path or an ambiguous key
   is refused rather than guessed at.
6. **136 new tests** — 55 hermetic, 81 against a real PostgreSQL — covering replay,
   rename and re-point, path normalization, unresolved trustees, partial and failed scans,
   out-of-order batches, and the new check constraints.

## Files and modules added or materially changed

### Schema and migration

| File | Contents |
| --- | --- |
| `backend/app/models/schema.py` | Changed. Adds `servers`, `smb_shares`, `smb_share_aces`, `principal_references`, and the `ReferenceKind` enum. |
| `database/migrations/versions/0003_smb_resources.py` | **New.** Generated from that declaration; `alembic check` reports no drift and the downgrade round-trips. |

### Domain

| Module | Contents |
| --- | --- |
| `backend/app/domain/identity.py` | Adds `referenced_principal_key(sid, host_key)` — the single implementation of "host-scope a BUILTIN SID, leave every other SID global". |
| `backend/app/domain/membership.py` | `MembershipEdge.member_key` now calls that function instead of restating the rule. |
| `backend/app/domain/access.py` | Adds `share_ace_right_token` and `share_ace_identity_key` as module functions, with `SmbShareAce.right_token` / `.identity_key(share_key)` delegating to them. |
| `backend/app/domain/resources.py` | Adds `ShareIdentity` and `parse_share_identifier`. |
| `backend/app/contracts/v1/keys.py` | `server_key`, `share_key`, and `smb_ace_key` now derive from the domain objects rather than formatting strings of their own. |

### Ingestion

| Module | Contents |
| --- | --- |
| `backend/app/ingestion/plan.py` | `SUPPORTED_KINDS` gains the three SMB kinds; new `ServerRow`, `ShareRow`, `ShareAceRow`, `PrincipalReferenceRow` and their builders; `BatchPlan` gains four collections. |
| `backend/app/ingestion/service.py` | `_write_servers`, `_write_shares`, `_write_share_aces`, `_write_references`, each a newest-wins upsert reusing the existing `_newest_wins` clause builder. |
| `backend/app/api/scan_runs.py` | The batch response reports `servers_written`, `shares_written`, `share_aces_written`. |

### Query path

| Module | Contents |
| --- | --- |
| `backend/app/repositories/resources.py` | **New.** `ResourceRepository`, `ServerRecord`, `ShareRecord`, `ShareAceRecord`, `ShareReferenceRecord`. |
| `backend/app/services/resources.py` | **New.** `ResourceService` — the one place `ResourceRepository` and `MembershipRepository` meet, which is where a trustee is resolved. |
| `backend/app/api/resources.py` | **New.** The six endpoints and their response models. |
| `backend/app/api/graph.py` | `_summary` renamed to `principal_summary` and exported, so a share ACE's trustee is rendered by exactly the code that renders a graph node. |
| `backend/app/api/__init__.py`, `app/repositories/__init__.py`, `app/services/__init__.py`, `app/domain/__init__.py` | Router attached, new types exported. |

### Tests

`backend/tests/domain/test_share_identity.py` (31), `tests/ingestion/test_plan_resources.py`
(24), `tests/db/test_smb_ingestion.py` (26), `tests/db/test_resource_api.py` (41),
`tests/db/test_schema.py` (+14), `tests/support/ingest.py` (`storable()`,
`storable_document()`, `ingest_storable_scenario()`; `ad_only()` kept for the AD-only tests).
`tests/db/test_ingestion.py` updated for the three new response counters and for the fact
that an SMB payload is no longer the example of an unstorable kind.

### Docs

`docs/architecture/resource-inventory.md` (new),
`docs/decisions/0007-resolution-is-a-join.md` (new), `docs/decisions/README.md`,
`docs/architecture/system-overview.md`, `README.md`.

## Important architecture decisions

1. **A trustee's resolution is a join, never a stored flag.** Recorded as
   [ADR-0007](../decisions/0007-resolution-is-a-join.md). The SMB and AD collectors run
   independently, so a `trustee_resolved` column would be stale the moment either one ran —
   and the common ordering in a real deployment (share ACLs collected before the directory
   is fully described) would fill the database with ACEs marked orphaned that are nothing of
   the sort. The same rule removes the stored UNC path, `is_hidden`, and
   `is_administrative`: a derived value stored beside its input is a second version of the
   truth that can disagree with the first.
2. **No foreign keys between the resource tables.** A share whose server no run has
   described, and an ACE whose share arrived in a later batch, are real observations. A
   foreign key would reject them at exactly the moment a partial scan most needs to record
   what it did manage to read. This is the shape `membership_edges` already has toward
   `principals`, and a missing parent is reported as `null` rather than dropped from the
   listing.
3. **One rule for host-scoping a referenced SID.** `referenced_principal_key` scopes a
   BUILTIN SID to the machine whose ACL named it and leaves every other SID global;
   `MembershipEdge.member_key` calls the same function. Had the two diverged, an ACE and a
   local-group edge naming one trustee would point at different nodes and the membership
   graph would never reach the ACL.
4. **A permission level and an access mask stay distinct.** The form is part of the ACE's
   identity key, `right_token` records which form was read, and a check constraint refuses a
   row carrying both. `Get-SmbShareAccess` can only report a level; rendering it as
   `0x001301bf` would claim precision the source never had. The Phase 4 algebra reconciles
   them.
5. **`order_index` is recorded but is not identity.** Canonical DACL order is what makes a
   Deny evaluable, so the position is stored and the ACL is returned in it — but two entries
   identical in trustee, type and right are duplicates, and reordering an ACL must not look
   like every entry being deleted and recreated.
6. **A share identifier is normalized in the domain, not at the edge.** Both the ACL and the
   detail endpoints call `parse_share_identifier`, so "which share is this" has one answer
   and is testable without a database. `\\FS01\Finance\Reports` is a 422 naming the share
   root, never a silent truncation to the share.
7. **A bare BUILTIN SID is not ambiguous for a resource query.** Principal lookup answers
   409 with candidates, because merging two servers' local administrators would invent
   access. "Which shares grant `S-1-5-32-544`" is a different question with one correct
   answer that spans servers, so it is answered, with each entry's trustee labelled
   individually.
8. **Offset pagination for an ACL, keyset for everything else.** DACL order is the answer's
   content; keyset paging would need a unique monotonic key and would force the entries into
   alphabetical `ace_key` order. An ACL is a handful of entries, so the risk keyset paging
   exists to avoid does not arise.
9. **`access_mask` is `bigint`.** An access mask is unsigned 32-bit and `0xFFFFFFFF` does not
   fit PostgreSQL's signed `integer` — it would land as `-1`, a different mask. A check
   constraint pins the range.
10. **`principal_references` records references, not resolutions.** It exists so that "which
    resources name this SID" stays one indexed lookup as Phase 3 adds a second kind of ACL.
    It carries no `resolved` column, deliberately.

## Schemas and contracts introduced or changed

**No contract change.** `docs/contracts/v1/` is untouched and contract v1.1 payloads are
accepted exactly as published; what changed is which kinds the server can persist.

New database schema at revision `0003_smb_resources`:

| Table | Key | Purpose |
| --- | --- | --- |
| `servers` | `server_key` (`Server.identity_key`) | Latest known state of each server. |
| `smb_shares` | `share_key` (`SmbShare.identity_key`) | Latest known state of each share. |
| `smb_share_aces` | `ace_key` (`SmbShareAce.identity_key(share_key)`) | Raw share ACEs, exactly as read. |
| `principal_references` | `(principal_key, reference_kind, reference_key)` | Which resources name which principals. |

New API surface, all under `/api/v1`: `GET /servers`, `GET /servers/{server}`,
`GET /servers/{server}/shares`, `GET /shares/{share}`, `GET /shares/{share}/acl`,
`GET /principals/{trustee}/shares`.

Changed response body: `POST /api/v1/scan-runs/{id}/batches` gains `servers_written`,
`shares_written`, `share_aces_written`. Additive; existing fields are unchanged.

## Tests run and exact results

Measured twice: against **this phase's commit alone**, which is what the phase delivers, and
against the **whole working tree**, which also carried a second session's concurrent Phase 1C
work. Both were run; neither figure is the other.

At this phase's commit (`bc9630e`, checked out into a clean worktree):

| Command | Result |
| --- | --- |
| `pytest -q -m "not smoke"` | **955 passed, 1 skipped, 152 deselected** in 9.10s |
| `ADG_RUN_SMOKE_TESTS=1 pytest -q` | **1107 passed, 1 skipped** in 113.00s |
| `pytest tests/db -q` (smoke) | **150 passed** in 50.06s |
| `pytest tests/domain/test_share_identity.py tests/ingestion/test_plan_resources.py -q` | **55 passed** in 0.09s — this phase's hermetic tests |
| `pytest tests/db/test_smb_ingestion.py tests/db/test_resource_api.py -q` (smoke) | **67 passed** in 27.56s — plus 14 added to `test_schema.py` |
| `ruff check .` | **All checks passed** |
| `mypy app tests` | **Success: no issues found in 94 source files** (strict) |
| `ruff format --check .` | **94 files already formatted, 1 would be reformatted** — see below |

Against the whole working tree, with the concurrent Phase 1C work present:

| Command | Result |
| --- | --- |
| `.\scripts\backend-test.ps1` | **3206 passed, 9 skipped, 192 deselected** in 13.94s |
| `.\scripts\backend-test.ps1 -Smoke` | **3398 passed, 9 skipped** in 89.46s |
| `.\scripts\backend-lint.ps1` | **ruff: all checks passed; ruff format: 111 files already formatted; mypy: no issues in 110 source files** (strict) |

Independent of either tree:

| Command | Result |
| --- | --- |
| `alembic upgrade head` / `downgrade 0002_ad_graph` / `upgrade head` | Applied and reversed cleanly |
| `alembic check` | **No new upgrade operations detected** |
| OpenAPI generation | 15 `/api/v1` paths, six of them new |
| Relative Markdown link check, whole repository | **88 links checked, 0 broken** |

The skips are pre-existing. Smoke tests run against a separate `adg_test` database that the
suite creates and migrates with a real `alembic upgrade head`.

**One pre-existing gate failure, not introduced here and not fixed here.**
`backend/tests/contracts/test_smb_collector.py` has been unformatted in the committed tree
since Phase 2A; the fix is a `ruff format` artifact sitting unstaged in another session's
working tree, and Phase 1B left it alone for the same reason. It is formatting only — the
whole-tree `backend-lint.ps1` run above passes because that session's fix is present on disk.

**Five defects were found by running the tests, not by inspection:**

1. **The batch response did not report what it had stored.** `BatchOutcome` gained the three
   SMB counters but `BatchAcceptedResponse` did not, so a collector was told `applied: 3`
   with no indication of what kind of thing landed.
2. **A transcript shifted to another day failed a check constraint.** Moving
   `observed_at` without moving `started_at` produced a run that completed before it
   started. The constraint is right; the test helper now moves every timestamp together.
3. **One share described twice in a batch cannot be planned at all** — the envelope rejects
   duplicate `source_key`s before `plan_batch` sees them. The intra-batch newest-wins in
   `plan.py` is therefore defensive only, and the test now pins the envelope's refusal,
   which is the behavior that actually protects a collector.
4. **A completed run refuses further batches**, so "replay the whole transcript" is not what
   a retrying collector does. The test now re-sends each step in place, which is.
5. **`//fs01/finance` cannot travel in a URL path.** A percent-encoded `/` is decoded before
   routing, so it can never be one path segment. The domain parser accepts the spelling; the
   endpoint cannot, and this is documented rather than worked around — a rewrite would have
   to guess where the identifier ended.

## Known limitations

1. **Only five of the seven observation kinds are stored.** `ntfs_resource` and `ntfs_ace`
   are still rejected with 422. A collector batching share facts together with directory
   facts must split them by kind until the file-system phase lands.
2. **No authentication anywhere.** Unchanged from Phase 1B and still the largest open risk:
   the ingestion endpoints accept observations from anyone who can reach the port.
3. **Nothing is ever marked absent.** There is no delete path. A share missing from the
   newest scan keeps its row, as does an ACE removed from an ACL. Reconciled scopes are
   recorded as evidence; acting on them is Phase 7.
4. **No effective access.** The share layer is stored and returned raw. Combining it with
   NTFS and the membership graph is Phase 4B/5, and the `kind: "raw_smb_acl"` marker exists
   so a client cannot mistake one for the other in the meantime.
5. **A server's DNS alias is not merged with its host name.** Two names for one machine stay
   two rows until something proves them equivalent; `computer_sid` is indexed because it is
   the evidence that would prove it. Nothing performs that merge yet.
6. **`share_count` on a server listing is a correlated subquery per row.** Fine for a page
   of a few hundred servers against `ix_smb_shares_server`; it would want revisiting if a
   listing ever had to cover tens of thousands.
7. **The ACL endpoint's offset pagination is not a stable snapshot.** An ACL changing between
   pages can shift entries. Acceptable because an ACL is small enough to fetch in one page;
   raise the limit rather than paging if consistency matters.
8. **`principal_references` has no pruning.** One row per (principal, resource) pair,
   retained even after the last ACE naming that principal is superseded — because nothing is
   ever deleted. Retention belongs with history in Phase 7.
9. **No share observation implies a directory.** `smb_shares.local_path` is recorded but
   nothing walks it; that is the Phase 3 scanner's job.
10. **Trustee resolution is per request.** Each ACL read re-joins `principals`. Bounded and
    cheap at current scale; the service seam is where a cache would go if it ever is not.

## Security and privilege assumptions

- **Unchanged posture: read-only, least privilege.** Nothing in this phase touches a target
  system. No endpoint can change a permission, and contract v1 has no payload that could
  express one.
- **The database refuses to hold an incoherent share fact.** A share keyed to one server but
  labelled with another's, an ACE claiming both a permission level and a mask, an ACE with
  neither, a mask outside 32 bits, a negative DACL position, an unknown share type or ACE
  type — each is a check constraint, enforced by PostgreSQL, so bypassing the application
  layer does not bypass the invariant.
- **An unreadable ACL cannot look like an empty one.** A collector that could not read a
  share's security descriptor reports a `collectorError` and leaves the run `partial`; the
  share's stored ACL is untouched. Only a reconciled scope on a clean successful run can ever
  support an absence claim, and this phase makes none.
- **A failed scan removes nothing**, and a failed or partial run may not reconcile at all —
  refused by the contract model with an explanation, before any row is touched.
- **An orphaned trustee is reported, never hidden.** `resolved: false` with the SID intact,
  and no guessed name: an unresolved principal may not carry a display name, enforced by a
  constraint dating from Phase 1B.
- **Ambiguity is refused where it matters.** A folder path is not silently read as a share,
  and a name is never accepted as a principal identifier.
- **Endpoints are unauthenticated.** See limitation 2.

## Migration and compatibility notes

- **Additive.** Revision `0003_smb_resources` follows `0002_ad_graph`; the downgrade drops
  only what it created and was exercised in both directions. Run
  `python -m alembic upgrade head` from `backend\`.
- **No contract change**, so every Phase 0B/2A collector payload is accepted unchanged,
  subject to the kind restriction in limitation 1.
- **No new dependencies.**
- **`app.api.graph._summary` is now `principal_summary` and public.** Any later module
  rendering a principal must call it rather than build its own summary.
- **Storage keys are load bearing.** `server_key`, `share_key`, and `ace_key` are the domain
  identity keys; changing a derivation changes every stored object's identity and requires a
  data migration, not an edit.
- **`keys.server_key` / `share_key` / `smb_ace_key` now validate as they derive.** They build
  the domain object, so a server name containing a path separator, or an unknown ACE type,
  raises instead of producing a key. Every published derivation is unchanged, and the parity
  test in `tests/contracts/test_schema_model_parity.py` still pins them to the protocol
  document.
- `alembic revision --autogenerate` still needs an absolute `script_location`: copy
  `alembic.ini`, rewrite that one line, and pass it with `-c`. Plain
  `upgrade`/`downgrade`/`check` are unaffected.

## Prerequisites for the next prompt

1. Read [`docs/architecture/resource-inventory.md`](../architecture/resource-inventory.md)
   and [ADR-0007](../decisions/0007-resolution-is-a-join.md) before touching the resource
   tables. The part that must not be eroded is that resolution and every other derivable
   value are computed, not stored.
2. **NTFS ingestion extends `plan_batch` the same way this phase did.** Add
   `ntfs_resource` and `ntfs_ace` to `SUPPORTED_KINDS` with their own row builders and
   tables; the run lifecycle, batch idempotency, and provenance already work for them.
   `keys.ntfs_resource_key` and `keys.ntfs_ace_key` are already written.
3. **Reuse `referenced_principal_key` for an NTFS trustee.** A file-system ACL is read on a
   machine exactly as a share ACL is, so the same host-scoping rule applies, and an NTFS ACE
   must land on the same node as the share ACE naming the same trustee.
4. **Add `ReferenceKind.NTFS_ACE` rather than a second reference table.** `principal_references`
   was shaped for exactly this; the constraint is generated from the enum, so the migration
   is a constraint change.
5. Declare new tables in `backend/app/models/schema.py` and generate the migration from it;
   `tests/db/test_schema.py` fails if the two diverge.
6. Replay fixtures rather than inventing data. `tests/support/ingest.py` now has
   `storable()` and `storable_document()`; relax them to the whole transcript once the NTFS
   kinds are storable, and `only()` in `tests/db/test_smb_ingestion.py` shows how to send a
   deliberately partial one.
7. **The effective-access engine consumes these tables plus `GraphService`.** It must keep
   the layers distinct — SMB `Change` and NTFS `Modify` are the same mask `0x001301BF`, and
   ADR-0005's layer tagging exists to stop them being compared — and it must propagate
   `traversal.complete`: an access answer computed from a truncated membership is itself a
   lower bound.
8. New code must pass `.\scripts\backend-lint.ps1` (ruff + **mypy strict**),
   `.\scripts\backend-test.ps1`, and `.\scripts\backend-test.ps1 -Smoke` with the stack up
   (`.\scripts\stack-up.ps1 -DbOnly`).

## `git status --short`

Captured immediately before the phase commit. A second session was working in this tree
throughout; its files — the AD-graph validation work, the collector-output validator, the
graph benchmarks and their fixtures — were **not** staged, and `README.md` and
`backend/app/repositories/__init__.py`, which both sessions edited, were staged with this
phase's hunks only.

```text
 M README.md
 M backend/app/api/__init__.py
 M backend/app/api/graph.py
 M backend/app/api/scan_runs.py
 M backend/app/contracts/v1/common.py
 M backend/app/contracts/v1/envelopes.py
 M backend/app/contracts/v1/keys.py
 M backend/app/domain/__init__.py
 M backend/app/domain/access.py
 M backend/app/domain/graph.py
 M backend/app/domain/identity.py
 M backend/app/domain/membership.py
 M backend/app/domain/resources.py
 M backend/app/ingestion/plan.py
 M backend/app/ingestion/service.py
 M backend/app/models/schema.py
 M backend/app/repositories/__init__.py
 M backend/app/repositories/membership.py
 M backend/app/services/__init__.py
 M backend/app/services/graph.py
 M backend/tests/contracts/test_smb_collector.py
 M backend/tests/db/test_ingestion.py
 M backend/tests/db/test_schema.py
 M backend/tests/fixtures/__init__.py
 M backend/tests/support/graph.py
 M backend/tests/support/ingest.py
 M docs/architecture/membership-graph.md
 M docs/architecture/system-overview.md
 M docs/decisions/README.md
?? backend/app/api/resources.py
?? backend/app/repositories/resources.py
?? backend/app/services/resources.py
?? backend/app/validation/
?? backend/tests/api/test_traversal_pairing.py
?? backend/tests/benchmarks/
?? backend/tests/contracts/test_ad_graph_fixtures.py
?? backend/tests/contracts/test_published_constraints.py
?? backend/tests/db/test_graph_adversarial.py
?? backend/tests/db/test_resource_api.py
?? backend/tests/db/test_smb_ingestion.py
?? backend/tests/domain/test_graph_adversarial.py
?? backend/tests/domain/test_graph_properties.py
?? backend/tests/domain/test_key_scoping.py
?? backend/tests/domain/test_share_identity.py
?? backend/tests/fixtures/ad_graph/
?? backend/tests/fixtures/build_ad_graph.py
?? backend/tests/ingestion/test_plan_resources.py
?? backend/tests/validation/
?? database/migrations/versions/0003_smb_resources.py
?? docs/architecture/ad-graph-validation.md
?? docs/architecture/resource-inventory.md
?? docs/decisions/0007-resolution-is-a-join.md
?? scripts/graph-benchmark.ps1
?? scripts/validate-collector-output.ps1
```
