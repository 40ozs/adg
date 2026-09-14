# ADG

ADG is a Windows-domain share-access auditing and governance application. It collects
identity, SMB share, and NTFS permission facts from Windows environments and answers, with
evidence:

1. Who can access this share or directory?
2. What can this user access?
3. Exactly why does the user have that access?
4. What changed, when, and what was the access impact?
5. Which access conditions are risky or anomalous?
6. What would happen if a membership or ACL entry were removed?

**ADG is read-only by default.** It observes and explains permissions; it does not change
them.

## Repository layout

```text
collector/        Native Windows collectors (PowerShell 7 + .NET). Not containerized.
  powershell/     Collection scripts/modules.
  service/        Long-running collector host (service/scheduled task).
backend/          Python + FastAPI API. Owns all authorization semantics.
  app/            Application package (api, models, collectors, access_engine, ...).
  tests/          Backend test suite.
frontend/         Next.js + TypeScript web application.
database/         PostgreSQL schema history.
  migrations/     Alembic migration scripts.
  seed/           Development seed data.
docker/           Container build definitions.
docs/
  architecture/   System and domain architecture.
  contracts/      Collector/API payload contracts.
  decisions/      Architecture decision records.
  handoffs/       Per-phase implementation handoffs.
scripts/          Windows developer commands (PowerShell 7).
```

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Windows | 10/11 or Server 2019+ | Collectors require Windows. |
| PowerShell | 7.x | `pwsh`. Windows PowerShell 5.1 only where a module requires it. |
| Python | 3.11+ | Backend. |
| Node.js | 20+ | Frontend. |
| Docker Desktop | current | PostgreSQL, API, and web containers. |

## First-time setup (Windows)

```powershell
cd C:\code\adg
Copy-Item .env.example .env      # then edit .env
.\scripts\bootstrap.ps1
```

`bootstrap.ps1` creates `backend\.venv`, installs backend and frontend dependencies, and
seeds `.env` if it is missing. It never overwrites an existing `.env`.

## Everyday commands (Windows)

```powershell
# Start / stop the local stack (PostgreSQL + API + web)
.\scripts\stack-up.ps1
.\scripts\stack-up.ps1 -DbOnly        # database only; run API/web natively
.\scripts\stack-down.ps1
.\scripts\stack-down.ps1 -RemoveVolumes   # also deletes local database data

# Backend
.\scripts\backend-test.ps1            # unit tests (hermetic)
.\scripts\backend-test.ps1 -Smoke     # adds tests that need PostgreSQL running
.\scripts\backend-lint.ps1            # ruff + mypy
.\scripts\backend-lint.ps1 -Fix       # apply safe fixes first

# Collectors
.\scripts\collector-test.ps1                                   # Pester suites
.\scripts\validate-collector-output.ps1 -Path .\out            # check a collector's JSON, offline
.\scripts\validate-collector-output.ps1 -Path .\out -Strict    # warnings fail the build too

# Membership-graph cost. Not a test; the recorded numbers and query plans live in
# docs\architecture\ad-graph-validation.md.
.\scripts\graph-benchmark.ps1
.\scripts\graph-benchmark.ps1 -Scale large -Database   # needs the database up

# Frontend
.\scripts\frontend-check.ps1          # lint + typecheck + tests + production build
.\scripts\frontend-check.ps1 -SkipBuild

# Run the API natively against the containerized database
cd C:\code\adg\backend
.\.venv\Scripts\python.exe -m app --reload --port 8000

# Run the frontend natively
cd C:\code\adg\frontend
npm run dev

# Database migrations (run from backend\, which holds alembic.ini)
cd C:\code\adg\backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic revision -m "add identity tables"
```

> **Windows:** start the API with `python -m app`, not `python -m uvicorn app.main:app`.
> uvicorn selects `ProactorEventLoop` on Windows and psycopg's async driver refuses to
> run on it, so a plain uvicorn command yields an API that cannot reach PostgreSQL.
> See `backend/app/runtime.py`. Linux containers are unaffected.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health/live` | Process liveness. Touches no dependency. |
| `GET /health/ready` | Readiness; probes PostgreSQL. Returns 503 when a dependency is down. |
| `GET /version` | Application name, version, and environment. |
| `GET /docs` | OpenAPI documentation (development). |

Collector ingestion (contract v1; see
[`docs/contracts/collector-protocol.md`](docs/contracts/collector-protocol.md)):

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/scan-runs` | Begin a scan run. `201` new, `200` replayed, `409` conflicting. |
| `POST /api/v1/scan-runs/{run_id}/batches` | Send up to 1000 observations. Idempotent on `batch_id`. |
| `POST /api/v1/scan-runs/{run_id}/completion` | Close a run and record any reconciled scopes. |
| `GET /api/v1/scan-runs/{run_id}` | Inspect a run: status, coverage, counts, collector errors. |

Identity and membership graph (see
[`docs/architecture/membership-graph.md`](docs/architecture/membership-graph.md)):

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/principals/{id-or-sid}` | One principal, its provenance, and every name ever observed for it. |
| `GET /api/v1/groups/{sid}/members` | Direct members, keyset-paginated. |
| `GET /api/v1/groups/{sid}/effective-members` | Recursive membership with the chain that produces it. |
| `GET /api/v1/principals/{sid}/groups` | Containing groups, `?scope=direct` or `effective`. |
| `GET /api/v1/principals/{sid}/membership-paths?group=` | Every simple chain from a principal to a group. |

> Every recursive answer carries a `traversal` block. **`complete: false` means the result
> is a lower bound, not a membership list.** Limits, cycle reporting, and pagination
> semantics are documented in
> [`docs/architecture/membership-graph.md`](docs/architecture/membership-graph.md).
> How those answers behave against deep, cyclic, renamed, orphaned and very large graphs —
> and what they cost, measured — is in
> [`docs/architecture/ad-graph-validation.md`](docs/architecture/ad-graph-validation.md).

Resources and raw ACLs, share layer and file-system layer (see
[`docs/architecture/resource-inventory.md`](docs/architecture/resource-inventory.md)):

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/servers` | Every known server, with its share count. |
| `GET /api/v1/servers/{server}` | One server, with provenance. |
| `GET /api/v1/servers/{server}/shares` | Shares published by one server, keyset-paginated. |
| `GET /api/v1/shares/{share}` | One share by key (`fs01\|finance`) or UNC path, with its server and ACE count. |
| `GET /api/v1/shares/{share}/acl` | That share's **share-level** ACL in DACL order, trustees resolved where known. |
| `GET /api/v1/shares/{share}/root-acl` | The raw **NTFS** ACL of the directory that share publishes. |
| `GET /api/v1/resources/{path}` | One directory's descriptor facts: owner, NULL-DACL state, inheritance, boundary. |
| `GET /api/v1/resources/{path}/acl` | That directory's NTFS ACL in evaluation order, with the ACL digest. |
| `GET /api/v1/principals/{sid}/shares` | Shares whose ACL names a SID. |

> Each ACL response carries a `kind` — `raw_smb_acl` or `raw_ntfs_acl`. **These are observed
> facts of one layer, not effective access** — that requires *both* layers plus group
> expansion, and arrives as its own representation. A trustee with `resolved: false` is an
> orphaned SID on a live ACL, which is a finding rather than an error.
>
> The two layers are separate routes on purpose. Remote access over SMB is limited by the
> share ACL **and** the NTFS ACL; access at the console is limited only by the second. A
> share whose NTFS root nothing has read reports `root_resource: null` — *nobody has looked*,
> never *nothing restricts it*.

The web application's **System status** page (`http://localhost:3000/status`) renders the
same information from the browser's point of view.

## Architecture

See [`docs/architecture/system-overview.md`](docs/architecture/system-overview.md).
Key invariants:

- **SID is the canonical identity key.** Names, UPNs, and DNs are metadata.
- **Windows collection is native**, never containerized.
- **The backend owns authorization semantics.** Collectors report observations; the
  frontend renders API results. Neither performs permission math.
- **Least privilege.** Normal collection must never require Domain Admin.

## Security

See [`SECURITY.md`](SECURITY.md). No secrets belong in source control: `.env.example` is a
template, and `.env` is ignored by Git.
