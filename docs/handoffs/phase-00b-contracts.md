# Handoff — Phase 0B (`phase-00/02-contracts-and-test-vectors.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-00a-domain-model.md](phase-00a-domain-model.md)

## Scope completed

Turned the Phase 0A domain model into versioned ingestion contracts, a normative collector
protocol, and twelve canonical scan-run transcripts that every later phase can replay.

1. **Eleven JSON Schemas** (draft 2020-12) under `docs/contracts/v1/` covering the eight
   required payloads; the scan-run envelope is expressed as three documents (start, batch,
   completion) because a run's lifecycle has three distinct messages.
2. **Backend mirror** in `backend/app/contracts/v1/` — pydantic models with cross-field
   rules JSON Schema cannot express, plus conversion to the Phase 0A domain types.
3. **`docs/contracts/collector-protocol.md`** — how a collector begins, batches, completes,
   fails, and retries, with the source-key derivations and PowerShell recipes.
4. **Twelve fixtures** in `backend/tests/fixtures/scenarios/`, covering all eleven required
   shapes plus a partial run.
5. **A runnable PowerShell example** whose dry-run output is validated against the schemas
   by a test, so the example cannot drift from the contract.
6. **352 new tests** covering schema validity, rejection cases, model behavior, batch
   semantics, fixture integrity, schema/model parity, and the PowerShell example.

## Files and modules added

### Contracts (`docs/contracts/`)

| File | Contents |
| --- | --- |
| `collector-protocol.md` | Normative protocol: sequence, endpoints, source keys, batching, completion, reconciliation, failure and retry, reporting rules, PowerShell recipes, versioning. |
| `README.md` | Index of the schemas and the four rules a collector must honor (rewritten). |
| `v1/common.schema.json` | SID, domain SID, UUID, timestamp, source key, access mask, ACE flags, enums, host/share names, UNC and local paths, scopes, source, observation base, collector error. |
| `v1/principal-observation.schema.json` | User, group, computer, well-known, and unresolved principals. |
| `v1/membership-observation.schema.json` | One directed membership edge. |
| `v1/server-observation.schema.json` | A computer hosting shares. |
| `v1/smb-share-observation.schema.json` | A share definition. |
| `v1/smb-ace-observation.schema.json` | One share-level ACE (`oneOf`: mask XOR level). |
| `v1/ntfs-resource-observation.schema.json` | A directory plus descriptor-level facts. |
| `v1/ntfs-ace-observation.schema.json` | One file-system DACL entry. |
| `v1/scan-run-start.schema.json` | Opens a run; declares scopes. |
| `v1/observation-batch.schema.json` | Up to 1000 observations. |
| `v1/scan-run-completion.schema.json` | Closes a run; carries reconciliation. |

### Backend (`backend/app/contracts/`)

| Module | Contents |
| --- | --- |
| `__init__.py` | Versioning policy for contract packages. |
| `v1/__init__.py` | Public surface and the contract rules later phases inherit. |
| `v1/common.py` | `ContractModel`, `ObservationBase`, `SourceDescriptor`, `Scope`, `ScopeKind`, `ObservationKind`, SID/host/timestamp helpers, limits. |
| `v1/keys.py` | The normative source-key derivations, one function per kind. |
| `v1/observations.py` | The seven observation models with validators and `to_domain()`. |
| `v1/envelopes.py` | `ScanRunStart`, `ObservationBatch`, `CollectorError`, `ScanRunCompletion`. |

### Fixtures and tests (`backend/tests/`)

`fixtures/__init__.py` (typed loader), `fixtures/README.md`, twelve
`fixtures/scenarios/*.json`, and `contracts/test_json_schemas.py`,
`contracts/test_observation_models.py`, `contracts/test_envelopes.py`,
`contracts/test_fixtures.py`, `contracts/test_schema_model_parity.py`,
`contracts/test_powershell_example.py`.

### Collector example

`collector/powershell/examples/Send-AdgScanRun.ps1` — start, batch, complete, with
`ConvertTo-AdgAceFlags`, one key-derivation function per kind, retry with a reused
`batch_id`, and a `-DryRun -OutputDirectory` mode.

### Changed

`backend/pyproject.toml` (added `jsonschema` and `types-jsonschema` dev dependencies, and a
`powershell` pytest marker).

## Important architecture decisions

1. **The scan-run envelope is three documents, not one.** Start declares intent, batches
   carry facts, completion closes and (only sometimes) reconciles. A single envelope could
   not express "this run is still going" or "this run failed after 9 batches".
2. **Idempotency has two keys.** `(run_id, batch_id)` makes a retried batch a no-op;
   `(run_id, source_key)` makes an individual observation converge even if de-duplication
   at the batch level is missed. Both are collector-generated and reused verbatim on retry.
3. **Source keys are derived, published, and verified.** `keys.py` is normative, the
   protocol document restates the formulas, and a test pins the two together. The server
   recomputes each key and rejects a mismatch, because a collector that derives keys
   differently would silently create a second row for an existing object.
4. **`ntfs_ace` keys exclude `order_index`.** Reordering an ACL must not look like every ACE
   being deleted and recreated.
5. **Reconciliation is the only path to absence,** and it is structurally unavailable to a
   partial run: `reconciled_scopes` must be empty unless the run succeeded with zero errors,
   and `reconciles(start)` additionally requires the scope to have been declared and the run
   not to be incremental. This is what makes "a partial scan cannot delete unseen objects" a
   property of the contract rather than a convention.
6. **A share ACE carries exactly one right form.** `Get-SmbShareAccess` reports levels; the
   descriptor APIs report masks. Recording both would fabricate a value the source never
   provided, so the key records which form was reported.
7. **`extra="forbid"` everywhere.** A misspelled field would otherwise be silently ignored,
   producing a quietly incomplete observation rather than a loud rejection.
8. **Enums are imported from `app.domain`, not redeclared,** so a contract value can never
   mean something the domain does not. Parity with the published schemas is tested.
9. **The PowerShell example is executable and tested.** Its dry-run output is validated
   against the schemas and parsed by the models, which re-derive every source key — a
   cross-implementation check between the PowerShell and Python derivations.

## Schemas and contracts introduced

**Contract v1** (`schema_version: "1.x"`). Every observation carries `schema_version`,
`kind`, `run_id`, `observed_at` (timezone-aware, normalized to UTC), and `source_key`.

Source-key derivations, now a stable contract:

| Kind | Derivation |
| --- | --- |
| `principal` | `principal\|<sid>`; `principal\|<host>\|<sid>` for a local group |
| `membership_edge` | `edge\|<group_key>-><member_key>\|<edge_kind>` |
| `server` | `server\|<host>` |
| `smb_share` | `share\|<host>\|<share>` |
| `smb_ace` | `smb_ace\|<host>\|<share>\|<trustee>\|<type>\|<level or 0x%08x mask>` |
| `ntfs_resource` | `resource\|<unc path>` |
| `ntfs_ace` | `ntfs_ace\|<unc path>\|<trustee>\|<type>\|0x%08x mask\|0x%02x flags` |

Endpoints specified (implemented from Phase 1): `POST /api/v1/scan-runs`,
`POST /api/v1/scan-runs/{run_id}/batches`, `POST /api/v1/scan-runs/{run_id}/completion`,
`GET /api/v1/scan-runs/{run_id}`.

Batch limits: 1–1000 observations; oversized batches are rejected, never truncated.

No database schema and no HTTP handler was added; the bootstrap contracts
(`/health/live`, `/health/ready`, `/version`) are untouched.

## Tests run and exact results

| Command | Result |
| --- | --- |
| `.\scripts\backend-test.ps1` (`pytest -q -m "not smoke"`) | **589 passed, 1 skipped, 2 deselected** in 5.27s |
| `.\scripts\backend-test.ps1 -Smoke` (PostgreSQL running) | **591 passed, 1 skipped** in 5.42s |
| `pytest tests/contracts -q` | **352 passed, 1 skipped** in 0.94s |
| `.\scripts\backend-lint.ps1` | `ruff check` **All checks passed**; `ruff format --check` **56 files already formatted**; `mypy app tests` **Success: no issues found in 55 source files** (strict) |
| `pwsh Send-AdgScanRun.ps1 -DryRun` | **exit 0**, 10 observations written across three payloads |
| Documentation link check (all relative Markdown links) | **0 broken links** |

The single skip is `common.schema.json`, which carries no embedded examples to validate.

Two defects were found by running the tests, not by inspection:

1. **The published UNC and local path patterns accepted `.` and `..` segments** while the
   domain parser rejects them — a schema laxer than the model, so a collector could send a
   path ADG cannot resolve and be told it was fine. Both patterns now reject traversal
   segments, and the replacement patterns were verified against positive and negative cases
   before being written into the contract.
2. **`mypy --strict` rejected the dict-unpacking used to build principals** in
   `to_domain()`, which was hiding which field reached which type. Rewritten with explicit
   keyword arguments.

## Known limitations

1. **The endpoints do not exist yet.** The protocol document specifies the routes, status
   codes, and idempotent responses; Phase 1 implements them. Nothing here has been exercised
   over HTTP.
2. **Server-side idempotency is specified, not implemented.** There is no storage of applied
   `batch_id` values and no upsert path; that arrives with persistence in Phase 1.
3. **Batch-level ordering is unconstrained.** A collector may send an `ntfs_ace` before its
   `ntfs_resource`; the contract only requires both in the same run. Ingestion will need to
   tolerate arrival order.
4. **`ace_count` consistency is checked in fixtures, not in the contract.** A batch whose
   resource declares three ACEs while reporting two is schema-valid; the server should
   verify it at run completion.
5. **The fixtures' `expectations` blocks are unverified claims** about effective access.
   Phase 4 must assert against them, and correct any that prove wrong.
6. **Fixtures are hand-maintained.** They were generated through the contract models, but no
   generator is committed; an edit must keep the transcript internally consistent (the tests
   check the main invariants).
7. **No compression, streaming, or authentication** is defined for batch upload. Collector
   authentication arrives with the API in Phase 1; the payloads are unencrypted JSON over
   whatever transport the deployment provides.
8. **The PowerShell example uses inline sample data.** It demonstrates payload construction
   and the protocol, not collection; it has never been run against a live API, only in
   dry-run mode.

## Security and privilege assumptions

- Unchanged: read-only, least-privileged collection (ADR-0004). Nothing in this phase reads
  or writes a target system; the PowerShell example performs no collection.
- **The contract cannot express a permission change.** There is no write, delete, or
  remediation payload in v1.
- **Under-reporting coverage is structurally prevented.** A run with errors cannot be
  `succeeded`, an understated `error_count` is rejected, and a non-clean run cannot
  reconcile, so an incomplete scan can never present itself as complete coverage.
- **Unreadable objects are reported, not hidden.** `collectorError` entries carry a code, a
  message, and the target; the protocol forbids escalating privilege or modifying an object
  to make a read succeed.
- **No derived conclusions are accepted.** There is no field for effective access anywhere
  in v1, and a test asserts no fixture carries one.
- All fixture data is synthetic. The fixtures README states that domain-captured data must
  never be committed.

## Migration and compatibility notes

- Additive only. Phase 0A's domain types are unchanged; Phase 0B consumes them.
- `docs/contracts/v1/` is now accepted. Additive changes bump the minor version
  (`1.1`, `1.2`, …) and are accepted by any v1 server; a breaking change requires
  `docs/contracts/v2/`, a new endpoint prefix, and a migration note.
- The source-key derivations are part of the contract: changing one changes every stored
  object's identity and requires a migration, not an edit.
- New dev dependencies: `jsonschema`, `types-jsonschema`. Run `.\scripts\bootstrap.ps1` (or
  `pip install -e ".[dev]"`) before running the suite.
- New pytest marker `powershell`; those tests skip automatically when `pwsh` is absent.

## Prerequisites for the next prompt (Phase 1 — AD collector)

1. Read `docs/contracts/collector-protocol.md` and this handoff first. The collector
   implements that protocol exactly; it does not invent payloads.
2. Use `backend/app/contracts/v1/keys.py` as the reference for source keys, and mirror it in
   PowerShell as `collector/powershell/examples/Send-AdgScanRun.ps1` does. If a derivation
   must change, change both plus the protocol table in the same commit.
3. Emit `primary_group` edges. A collector that reads only the `member` attribute
   under-reports access for every user in the domain.
4. Report unresolvable SIDs as `principal_kind: "unresolved"` with a reason, never with a
   guessed `display_name`.
5. Scope every local-group principal and edge by host.
6. A run that could not read something is `partial` with `collectorError` entries and
   **no** `reconciled_scopes`.
7. Replay the fixtures against the ingestion API as tests rather than writing new synthetic
   data: `from tests.fixtures import load_scenario`.
8. New code must pass `.\scripts\backend-lint.ps1` (ruff + **mypy strict**) and
   `.\scripts\backend-test.ps1`.
9. The development stack is stopped; start PostgreSQL with `.\scripts\stack-up.ps1 -DbOnly`
   when Phase 1 begins persisting.

## `git status --short`

Captured immediately before the phase commit:

```text
 M backend/pyproject.toml
 M docs/contracts/README.md
?? backend/app/contracts/
?? backend/tests/contracts/
?? backend/tests/fixtures/
?? collector/powershell/examples/
?? docs/contracts/collector-protocol.md
?? docs/contracts/v1/
?? docs/handoffs/phase-00b-contracts.md
```

All of it, this handoff included, went into the phase commit; the working tree is clean
afterwards.
