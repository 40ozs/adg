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
