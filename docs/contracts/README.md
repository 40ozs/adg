# Contracts

Canonical payload contracts exchanged between collectors, the API, and the frontend, plus
the fixtures that pin them.

A contract here is a promise: once accepted, it changes only through an explicit, versioned
migration documented in the phase that changes it.

Contracts run in two directions, and the distinction matters because the rules differ:

* **Inbound** — what a collector sends. Raw observations, never conclusions (ADR-0003).
  Hand-written schemas, because a collector author codes against them.
* **Outbound** — what the API returns for a question it *derived*. Generated from the models
  the API serializes, because a hand-maintained copy of a response shape drifts and the
  drift is invisible until a client believes the wrong one.

## What is here

| Path | Purpose |
| --- | --- |
| [`collector-protocol.md`](collector-protocol.md) | How a collector begins, batches, completes, fails, and retries a scan. Normative. |
| [`derived-responses.md`](derived-responses.md) | What the API returns for a derived answer: the verdict vocabulary, the collection basis, caching, bounds, and identifiers. Normative. |
| [`v1/`](v1/) | JSON Schemas (draft 2020-12) for every collector payload and every derived response. |
| [`v1/derived/examples/`](v1/derived/examples/) | Captured derived responses. Real bodies from the smoke suite, validated against the schemas beside them. |

## The derived-response schemas

| Schema | Response |
| --- | --- |
| [`access-explanation.schema.json`](v1/derived/access-explanation.schema.json) | `GET /api/v1/access/explain` — the whole derivation of one principal's access to one directory. |
| [`access-paths.schema.json`](v1/derived/access-paths.schema.json) | `GET /api/v1/access/paths` — one page of the causal paths behind that answer. |
| [`resource-impact.schema.json`](v1/derived/resource-impact.schema.json) | `GET /api/v1/groups/{identifier}/resource-impact` — everything one group's membership reaches. |

Regenerate with `python -m app.contracts.derived`;
`backend/tests/contracts/test_derived_schemas.py` fails when they fall behind. See
[`derived-responses.md`](derived-responses.md) for what the fields promise — above all that
`granted`, `denied`, `no_grant` and `indeterminate` are four different answers and that the
last one forbids concluding the negative.

## The v1 schemas

| Schema | Payload |
| --- | --- |
| [`common.schema.json`](v1/common.schema.json) | Shared definitions: SID, timestamp, access mask, ACE flags, scopes, source, observation base. |
| [`scan-run-start.schema.json`](v1/scan-run-start.schema.json) | Opens a run and declares the scopes it will enumerate. |
| [`observation-batch.schema.json`](v1/observation-batch.schema.json) | A chunk of up to 1000 observations. |
| [`scan-run-completion.schema.json`](v1/scan-run-completion.schema.json) | Closes a run; the only place reconciliation can happen. |
| [`principal-observation.schema.json`](v1/principal-observation.schema.json) | A user, group, computer, well-known, or unresolved principal. |
| [`membership-observation.schema.json`](v1/membership-observation.schema.json) | One directed membership edge. |
| [`server-observation.schema.json`](v1/server-observation.schema.json) | A computer that hosts shares. |
| [`smb-share-observation.schema.json`](v1/smb-share-observation.schema.json) | A share definition. |
| [`smb-ace-observation.schema.json`](v1/smb-ace-observation.schema.json) | One share-level ACE. |
| [`ntfs-resource-observation.schema.json`](v1/ntfs-resource-observation.schema.json) | A directory and its descriptor-level facts. |
| [`ntfs-ace-observation.schema.json`](v1/ntfs-ace-observation.schema.json) | One file-system DACL entry. |

Backend mirror: `backend/app/contracts/v1/`. A parity test
(`backend/tests/contracts/test_schema_model_parity.py`) fails if the two descriptions drift.

## The rules a collector must honor

1. **Report readings, not conclusions.** No contract carries effective access, expanded
   membership, or a risk verdict, and none will in v1 (ADR-0003).
2. **Ingestion is idempotent** on `(run_id, source_key)`, and batches on
   `(run_id, batch_id)`. Replaying a batch changes nothing.
3. **Silence means nothing.** An object missing from a batch is not gone. Only a reconciled
   scope on a successful, error-free, non-incremental run authorizes marking anything absent
   — which is why a partial scan cannot delete what it never saw.
4. **Raw fidelity.** Raw access masks with generic bits intact, the raw ACE flags byte,
   `dacl_present` reported honestly (a NULL DACL grants everyone access; an empty DACL grants
   nobody), and unresolvable SIDs kept without a guessed name.

## Worked example and fixtures

* [`collector/powershell/examples/Send-AdgScanRun.ps1`](../../collector/powershell/examples/Send-AdgScanRun.ps1)
  builds and sends a complete run. Run it with `-DryRun -OutputDirectory <path>` to see the
  exact JSON; a test validates that output against these schemas.
* [`backend/tests/fixtures/`](../../backend/tests/fixtures/) holds twelve canonical scan
  transcripts (direct grant, nested groups, cycles, unresolved SIDs, Deny, inheritance,
  broken inheritance, each layer being the limiting one, and a partial run). Later phases
  should use those rather than invent data.

## Versioning

`v1` is contract major version 1; every payload carries `schema_version: "1.x"`. Additive
changes bump the minor version and are accepted by any v1 server. Breaking changes require a
`v2/` directory, a new endpoint prefix, and a migration note in the phase handoff that makes
the change. An accepted schema file is never edited in place to mean something different.
