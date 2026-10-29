# ADG system overview

**Status:** accepted (Bootstrap phase)
**Applies to:** all later phases unless superseded by a decision recorded in
`docs/decisions/`.

## Purpose

ADG audits and governs access to Windows file shares. It answers, with evidence:

1. Who can access this share or directory?
2. What can this user access?
3. Exactly why does the user have that access?
4. What changed, when, and what was the access impact?
5. Which access conditions are risky or anomalous?
6. What would happen if a membership or ACL entry were removed?

## The reviewer view: from collection to screen

This is the path one answer in the synthetic demo takes, named by the modules that implement
each stage (paths are under `backend/app/` unless stated otherwise). The stages are drawn as
one column in reading order. Risk evaluation, coverage and comparison each read the stored
evidence directly rather than one another's output.

```text
 SYNTHETIC COLLECTORS / SOURCES            (production: native Windows collectors, collector/)
   demo/genesee_press/                     the Genesee Press estate: 3 scans × 100 collector runs
   demo_mode/ingest.py, providers.py       SyntheticObservationSource posts each run through
                                           the real POST /api/v1/scan-runs routes, in process
                 │
                 ▼
 COLLECTION RUNS / NORMALIZED EVIDENCE
   contracts/v1, ingestion/service.py      contract validation, idempotent batches
   history/writer.py                       versioned observations (object_versions), tombstones
   scan_runs + current-state tables        a run that did not reconcile a scope marks nothing absent
                 │
                 ▼
 IDENTITY + ACCESS DOMAIN MODEL
   services/access.py  AccessService       loads SubjectFacts, ResourceDacl, ShareDacl
     ._coverage_findings                   ◄ A: unresolved SIDs and uncollected groups on the ACLs
   access_engine/subjects.py  build_token  the token for one access path
                                           ◄ A: SYSTEM mapping (confirmed/ambiguous/missing/conflicting)
   access_engine/evaluation.py             evaluate_acl: each ACL in DACL order
   access_engine/resolver.py               resolve_access: share ∩ NTFS
     certainty_of(findings)                ◄ B: certain / at_least / at_most / uncertain
                 │
                 ▼
 RELATIONSHIP / PATH DERIVATION
   access_engine/causality.py              explain_access: causal paths, measured removal targets
                 │
                 ▼
 ACCESS EXPLANATION
   access_engine/verdict.py                classify_access, describe_verdict
   access_engine/investigation.py          investigate: primary routes, open questions
                                           (state + impact), next steps
   repositories/provenance.py              per-hop collector and first/last scan
                 │
                 ▼
 RISK EVALUATION
   risk_engine/engine.py                   evaluate: 11 deterministic rules; findings keep their
                                           evidence. Each pass records the collected-state basis
                                           it read (migration 0018_risk_evaluation_basis)
                 │
                 ▼
 COVERAGE / COMPLETENESS
   domain/coverage.py  assess_coverage     per scope: what the latest run did × how fresh the
                                           held evidence is   ◄ C: collection freshness enters
     interpret_totals                      complete / partial / incomplete / stale / not_evaluated
   services/coverage.py                    expected collectors (ADG_EXPECTED_COLLECTORS or the
                                           demo dataset's own scan history)
                 │
                 ▼
 COMPARISON / RECOVERY
   changes/service.py  ChangeService.compare
                                           aligned cut, 5,000-object ceiling per side,
                                           4 s window checked at checkpoints
                                           ◄ D: complete / incomplete / timed_out / cancelled
   api/changes.py  _recovery               retry_standard_window, narrow_kind (each names retry_of)
                 │
                 ▼
 API  (FastAPI, api/*; a capability is checked on every route)
   /api/v1/access/explain · /collection/coverage · /risks/summary · /changes/compare · /demo
                 │
                 ▼
 REVIEWER UI  (Next.js, frontend/; a server-side proxy at /api/adg/* holds the token;
               pages render API fields and compute no permissions)
   /demo · /access/investigate · /risks · /collectors · /changes/compare
```

Where each kind of uncertainty is decided:

| Marker | Decided in | What is decided |
| --- | --- | --- |
| **A** Ambiguity enters | `AccessService._coverage_findings` and `_could_contain_members` (`services/access.py`); `build_token` and `system_mapping_of` (`access_engine/subjects.py`) | An ACL trustee whose membership was never collected, or whose SID no collector could resolve, becomes a `trustee_membership_unobserved` finding. SYSTEM gets exactly one mapping state and one `system_mapping_*` finding. |
| **B** `at_least` / provisional | `certainty_of` (`access_engine/resolver.py`) over the condition directions in `access_engine/conditions.py`; `_impact` in `access_engine/investigation.py` | Findings that may only understate make the answer `at_least`; both directions make it `uncertain`. The investigation marks a finding `extent` (the access stands and could be wider) or `presence` (the access itself is provisional). |
| **C** Collection freshness enters | `assess_coverage` and `interpret_totals` (`domain/coverage.py`), over the collector operations records (`domain/operations.py`) | Each scope's latest run is `collected`, `partial`, `unavailable`, `failed` or `running`, and its evidence is `current`, `stale` or `none`. Risk totals are read against that, and against the snapshot the risk pass recorded. |
| **D** Typed comparison outcome | `ChangeService.compare` (`changes/service.py`); recovery in `api/changes.py` | Whether a comparison is `complete`, `incomplete` (with `compared_through`), `timed_out` (with `stopped_in`) or `cancelled`, and which recovery requests are offered. |

The demo walkthroughs that exercise this path are in [the demo guide](../demo/README.md). The
reasoning behind each stage is in the [ADRs](../decisions/) and in the
[demo reports](../demo/reports/).

## Component separation

```text
   Windows domain                     Docker / server side
 ┌──────────────────────┐          ┌─────────────────────────────────────┐
 │ Collector (native)   │          │ Backend API (FastAPI)               │
 │  PowerShell 7 + .NET │  HTTPS   │  - validates collector observations │
 │  - AD identities     ├─────────►│  - owns authorization semantics     │
 │  - SMB shares/ACLs   │  JSON    │  - effective access + explanation   │
 │  - NTFS descriptors  │          │  - risk, history, governance        │
 │  read-only           │          └──────────────┬──────────────────────┘
 └──────────────────────┘                         │ SQLAlchemy
                                                  ▼
                                   ┌─────────────────────────────────────┐
                                   │ PostgreSQL                          │
                                   │  raw observations + derived state,  │
                                   │  stored distinguishably             │
                                   └──────────────┬──────────────────────┘
                                                  │ stable HTTP API
                                                  ▼
                                   ┌─────────────────────────────────────┐
                                   │ Frontend (Next.js + TypeScript)     │
                                   │  renders API results; no permission │
                                   │  math of its own                    │
                                   └─────────────────────────────────────┘
```

### Collectors are native Windows processes

Collection needs domain context and Windows APIs — LDAP/ADSI, the SMB management surface,
and Win32 security descriptors — so collectors run natively as Windows processes, services,
or scheduled tasks. They are **not** containerized and are deliberately absent from
`docker-compose.yml`.

A collector's only job is to observe and report facts. It does not decide who "really" has
access, does not filter results by business rules, and does not write to target systems.
Replacing a collector implementation must never change the meaning of stored data.

### The backend owns authorization semantics

All permission math — SMB share rights, NTFS ACE precedence, Allow/Deny evaluation,
inheritance, group expansion, and access explanation — lives in backend domain modules
(`backend/app/access_engine`) as deterministic, typed, independently testable functions.
They are not framework-bound: the API layer calls them, but so can a test, a batch job, or
a simulation.

This is a single-source-of-truth rule. If a collector or the frontend computes effective
access on its own, the two answers will eventually disagree, and an audit tool that gives
two answers is worthless.

### PostgreSQL is persistent state

PostgreSQL holds the identity graph, resources, raw ACEs, scan runs, and derived results.
Schema changes arrive only through Alembic migrations in `database/migrations`, so every
environment converges on the same structure.

Raw observations and derived state are stored so that they can always be told apart: a
derived effective-access result must never be indistinguishable from an observed ACE.

### The frontend is an API consumer

The Next.js application renders what the API returns. Its API base URL is environment
configuration (`NEXT_PUBLIC_ADG_API_URL`), so one build can be promoted across
environments.

## Identity model

**SID is the canonical identity key.** Display names, UPNs, sAMAccountNames, and
distinguished names are mutable metadata and must never be a primary key or a join key.
An unresolvable SID is still a valid, storable fact — orphaned SIDs on an ACL are a finding,
not an error. Formalized in [permission-domain-model.md](permission-domain-model.md) and ADR-0001.

## Membership

Group membership is stored as directed edges, never as an expanded closure, so that the
chain producing an access (`alice → Finance-Team → Finance-RW`) survives — the chain is the
product. Every "who is effectively in this group?" answer is therefore a bounded, cycle-safe
traversal performed at query time, and every such answer states whether it is complete.
Query semantics, cycle reporting, limits, and pagination are in
[membership-graph.md](membership-graph.md); the reasoning is ADR-0002 and ADR-0006.

## Resources

Servers, SMB shares, and raw share-level ACEs are stored as what a collector observed and
nothing more. A share is a publication of a directory rather than the directory itself; a
share ACL is kept in the form its source reported, level or mask, with no conversion between
them; and whether an ACE's trustee resolves to a known principal is computed by a join at
query time rather than stored, because the SMB and AD collectors run independently and a
stored flag would be stale the moment either one ran. Nothing here can mark an object
absent: a share a failed scan did not mention keeps its row. Identity, derivation, the API
surface, and the pagination choices are in [resource-inventory.md](resource-inventory.md);
the reasoning is ADR-0003 and ADR-0007.

## Effective access

The three stored layers — identity, share ACL, NTFS ACL — answer nothing on their own. What
a principal can actually do is the Windows access check run over an access token ADG
constructs, for one declared access path, with the two ACLs intersected. It is computed on
every request and never stored, and every answer names the observations it had to assume in
order to produce one: an unread share ACL is not an open share, and a group nobody
enumerated is not a group somebody is absent from.
[effective-access.md](effective-access.md) specifies it; ADR-0010, ADR-0011 and ADR-0012 are
the reasoning.

## Security posture

- **Read-only by default.** No ACL writes, no membership changes, no deletions on target
  systems. See `SECURITY.md`.
- **Least privilege.** Normal collection must never require Domain Admin; each collector
  documents the minimum rights it needs.
- **No secrets in source control.** Configuration comes from the environment;
  `.env.example` is a template.
- **Actionable diagnostics.** Every external input is validated, and failures say what to
  fix. The readiness probe reports the failure class without echoing the connection string.

## Runtime shape (development)

| Service | Where it runs | Port |
| --- | --- | --- |
| PostgreSQL | Docker | 5432 |
| Backend API | Docker or native venv | 8000 |
| Frontend | Docker or `npm run dev` | 3000 |
| Collectors | Native Windows only | n/a |

The API is started through one entry point everywhere — `python -m app` — because the
PostgreSQL driver constrains the event loop: psycopg's async driver cannot run on Windows'
`ProactorEventLoop`, which is what uvicorn selects by default on Windows. `app/runtime.py`
supplies the loop factory. Linux containers are unaffected, but they use the same entry
point so that there is only one way to start the service.

`GET /health/live` reports process liveness and touches no dependency; `GET /health/ready`
probes PostgreSQL and returns 503 when it is unreachable. Orchestrators must treat the two
differently: liveness failure means restart, readiness failure means remove from rotation.

## Out of scope

DLP, sensitive-content classification, SharePoint, OneDrive, Exchange, and Linux/NFS
permissions are outside the product boundary.
