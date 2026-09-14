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
