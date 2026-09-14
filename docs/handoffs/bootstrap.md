# Handoff — Bootstrap (`00-bootstrap/00-project-bootstrap.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`

## Scope completed

Created the ADG workspace and a production-oriented monorepo skeleton that later phases can
build on without restructuring:

1. Git repository initialized at `C:\code\adg` with the required top-level layout.
2. FastAPI backend with `/health/live`, `/health/ready`, `/version`, structured JSON
   logging, environment-based configuration, and a database connectivity abstraction.
3. Alembic migrations rooted at `database/migrations`, with a baseline revision.
4. Next.js + TypeScript frontend: application shell, backend status view, environment-based
   API URL.
5. Docker Compose development stack for PostgreSQL, API, and web. Collectors stay native.
6. PowerShell 7 developer scripts for bootstrap, backend test, backend lint/type check,
   frontend check, and stack start/stop.
7. `.gitignore`, `.editorconfig`, `.env.example`, root `README.md`, `SECURITY.md`.
8. `docs/architecture/system-overview.md`, an ADR template, and directory READMEs for
   `collector/`, `docs/contracts/`, `docs/decisions/`, `docs/handoffs/`, `database/seed/`.
9. A health smoke test proving the API reaches PostgreSQL when the stack is running.
10. A CI workflow running the same gates as the local scripts.

## Files and modules added

### Backend (`backend/`)

| File | Purpose |
| --- | --- |
| `pyproject.toml` | Dependencies plus ruff/mypy/pytest configuration (single source of truth, also used by the image build). |
| `app/__init__.py` | Package and `__version__`. |
| `app/__main__.py` | `python -m app` — the supported way to start the API. |
| `app/config.py` | `Settings` (pydantic-settings, `ADG_` prefix), `build_settings`, cached `get_settings`. |
| `app/logging_config.py` | `JsonLogFormatter` and `configure_logging`. |
| `app/db.py` | `Database` (async engine, session context manager, `check_connectivity`) and `ConnectivityResult`. |
| `app/runtime.py` | Event-loop selection required by the PostgreSQL driver on Windows. |
| `app/main.py` | `create_app` factory: lifespan, CORS, request-ID/duration middleware, router mounting. |
| `app/api/health.py`, `app/api/meta.py`, `app/api/__init__.py` | Health, version, and the single router aggregation point. |
| `app/{models,collectors,access_engine,risk_engine,history}/__init__.py` | Documented placeholders for later phases. |
| `alembic.ini` | Alembic config pointing at `../database/migrations`; contains no credentials. |
| `tests/` | 7 test modules plus `conftest.py` (see results below). |

### Database (`database/`)

`migrations/env.py` (reads the URL from `Settings`, never from `alembic.ini`),
`migrations/script.py.mako`, `migrations/versions/0001_baseline.py`, `migrations/README.md`,
`seed/README.md`.

### Frontend (`frontend/`)

`package.json`, `tsconfig.json`, `next.config.mjs`, `eslint.config.mjs`, `.env.example`,
`app/layout.tsx`, `app/page.tsx`, `app/status/page.tsx`, `app/globals.css`,
`lib/config.ts` (validated `NEXT_PUBLIC_ADG_API_URL` resolution), `lib/api.ts` (typed API
access with explicit failure), `tests/config.test.ts`.

### Infrastructure and docs

`docker/Dockerfile.backend`, `docker/Dockerfile.frontend`, `docker-compose.yml`,
`.dockerignore`, `.github/workflows/ci.yml`, `scripts/*.ps1` (6 scripts),
`docs/architecture/system-overview.md`, `docs/decisions/adr-template.md`,
`docs/decisions/README.md`, `docs/contracts/README.md`, `docs/handoffs/README.md`,
`collector/README.md`, `README.md`, `SECURITY.md`, `.env.example`, `.editorconfig`,
`.gitignore`, `.gitattributes`.

## Important architecture decisions

1. **Collectors are native Windows processes, never containerized.** They are absent from
   `docker-compose.yml` by design and report observations only; they own no authorization
   logic.
2. **The backend owns authorization semantics.** `app/access_engine` is reserved for
   deterministic, framework-free permission math. Neither collectors nor the frontend may
   reimplement it.
3. **SID is the canonical identity key**; names/UPNs/DNs are metadata. Recorded in
   `docs/architecture/system-overview.md`; formalized in Phase 0A.
4. **Raw observations and derived state must remain distinguishable** in PostgreSQL. No
   schema is created yet, so Phase 0A/0B are free to model this without migration debt.
5. **Configuration is environment-based and validated at startup.** The backend refuses to
   start with `ADG_ENVIRONMENT=production` while the development database URL is still in
   place, and rejects any non-`postgresql+psycopg` URL.
6. **One process entry point everywhere: `python -m app`.** The PostgreSQL driver
   constrains the event loop (see "Known limitations"), so the image `CMD` and the
   documented native command are identical.
7. **Health endpoints are split by meaning.** `/health/live` touches no dependency;
   `/health/ready` probes PostgreSQL and returns 503 with a non-secret diagnostic. A
   failing dependency must never become a 500.
8. **Migrations live outside the backend package** (`database/migrations`) and read their
   URL from `Settings`, so schema history is reviewable on its own and cannot drift from
   the running service's target database.
9. **The API container runs as an unprivileged user** (uid 10001). ADG needs no host
   rights.

## Schemas and contracts introduced

- No database schema yet. `0001_baseline` is an intentional no-op that establishes the
  migration chain so `alembic upgrade head` is meaningful from day one.
- HTTP contracts introduced (stable from here unless versioned):
  - `GET /health/live` → `{"status": "alive", "version": str}`
  - `GET /health/ready` → `{"status": "ready"|"not_ready", "version": str, "dependencies": [{"name": str, "ok": bool, "latency_ms": float, "error": str|null}]}`, HTTP 503 when any dependency is down.
  - `GET /version` → `{"name": str, "version": str, "environment": str}`
  - Every response carries an `X-Request-ID` header (echoed when supplied).
- Configuration contract: `ADG_*` environment variables documented in `.env.example`.
- Frontend configuration: `NEXT_PUBLIC_ADG_API_URL` (browser) and `ADG_INTERNAL_API_URL`
  (server-side rendering only; never exposed to the browser).

## Tests run and exact results

All commands run on Windows 11, Python 3.13.14, Node 22.20.0, Docker 29.7.2.

| Command | Result |
| --- | --- |
| `.\scripts\backend-test.ps1` (`pytest -q -m "not smoke"`) | **34 passed, 2 deselected** in 4.21s |
| `.\scripts\backend-test.ps1 -Smoke` (PostgreSQL running) | **36 passed** in 4.28s (this run first exposed the ProactorEventLoop defect; green after the fix) |
| `.\scripts\backend-lint.ps1` (`ruff check`, `ruff format --check`, `mypy app tests`) | **All checks passed** / **24 files already formatted** / **Success: no issues found in 24 source files** (mypy strict) |
| `npm run lint` (frontend) | **passed, no findings** |
| `npm run typecheck` (frontend) | **passed** |
| `npm run test` (vitest) | **1 file, 8 tests passed** |
| `npm run build` (Next.js 15.5.25) | **Compiled successfully**; routes `/` and `/_not-found` static, `/status` dynamic |
| `docker compose config -q` | **exit 0** |
| `docker compose up -d --build` | **all three services started**; `adg-db-1` healthy, `adg-api-1` healthy, `adg-web-1` serving. `GET http://localhost:8000/health/ready` → 200 `ready`; `GET http://localhost:3000/status` → 200 rendering "Responding", readiness `ready`, dependency `postgresql` `up` |
| `alembic upgrade head` against the container | **`0001_baseline` applied**; `alembic current` → `0001_baseline (head)` |
| `alembic upgrade head --sql` (offline) | emits the `alembic_version` DDL without connecting |
| Native `python -m app --port 8012` + `curl /health/ready` | **HTTP 200**, `{"status":"ready", ... "postgresql", "ok":true}` |
| `.\scripts\bootstrap.ps1` | **exit 0**; reused the venv, dependencies up to date, created `.env` from the template (Git-ignored) |
| `.\scripts\stack-up.ps1 -DbOnly` then `.\scripts\stack-down.ps1` | **exit 0** both ways; containers created and removed cleanly |
| Backend tests re-run with a root `.env` present | **34 passed, 2 deselected** — configuration tests stay hermetic |
| Native run with `ADG_LOG_FORMAT=json` | one JSON object per line including `request_id`, `method`, `path`, `status_code`, `duration_ms`; no duplicate uvicorn access lines |

Behaviors verified by execution, not inspection: health/readiness responses (stubbed and
live), the 503 path, request-ID propagation, configuration rejection cases, JSON log
output, the Alembic chain, all six PowerShell scripts, and the Windows event-loop fix.

## Known limitations

1. **psycopg cannot use Windows' `ProactorEventLoop`.** uvicorn selects it by default on
   Windows, so `python -m uvicorn app.main:app` yields an API that cannot reach PostgreSQL
   (`InterfaceError: Psycopg cannot use the 'ProactorEventLoop'...`). This was found by
   running the smoke test, not by inspection. Mitigations in place: `app/runtime.py`
   supplies a selector loop factory, `python -m app` uses it, `tests/conftest.py` installs
   the matching policy before pytest-asyncio creates a loop, and the readiness diagnostic
   appends how to start the API correctly. Linux containers are unaffected. Anyone adding a
   second process entry point must apply the same loop selection.
2. **The web tier needs two API addresses.** Server-side rendering inside the `web`
   container cannot use `http://localhost:8000` — there, localhost is the web container.
   Found by loading the containerized status page, which reported "Backend unreachable"
   while the API was healthy. `ADG_INTERNAL_API_URL` (server-side, non-public) now carries
   the internal address and compose sets it to `http://api:8000`;
   `resolveServerApiUrl()` falls back to the browser URL when it is unset, so native
   development is unchanged. The status page displays both addresses.
3. `0001_baseline` creates no tables; the first real schema arrives with the domain model.
4. No authentication yet. The API is unauthenticated and must not be exposed beyond
   localhost until Phase 6 introduces the OIDC boundary.
5. The frontend has unit tests for configuration resolution only. There is no component
   or end-to-end test harness yet.
6. The frontend image runs a production build; day-to-day frontend work should use
   `npm run dev` with `.\scripts\stack-up.ps1 -DbOnly`.
7. The smoke test verifies connectivity only — it asserts nothing about schema state.
8. CI is configured but has never run: the repository has no remote.
9. `docs/contracts/` is intentionally empty (Phase 0B populates it), and no ADRs are
   recorded yet (Phase 0A creates the first four).

## Security and privilege assumptions

- **Read-only posture.** Nothing in this phase writes to any Windows system; there are no
  collectors yet. The stance is documented in `SECURITY.md` and
  `docs/architecture/system-overview.md`.
- **No secrets committed.** `.env` is git-ignored; `.env.example` holds local development
  placeholders only. Verified: `git status --short` shows no `.env`, `.venv`, or
  `node_modules` entries.
- The development credentials in `.env.example`/`docker-compose.yml` are for a local
  container and must never be reused elsewhere. Production must set `ADG_DATABASE_URL`;
  startup fails otherwise.
- Diagnostics never echo the connection string. `Database._diagnostic` reports the
  exception class and first line only; a test asserts the password does not appear.
- The API container runs as uid 10001, not root. No Windows privileges are required by
  anything in this phase; least-privilege collector requirements are documented for Phase 1
  in `SECURITY.md`.

## Migration and compatibility notes

- First phase; nothing to migrate. The schema baseline is `0001_baseline`.
- Later phases must add revisions rather than editing the baseline.
- The HTTP contracts above are now public. Changing a response shape requires an explicit
  versioned migration and a note in the phase handoff.
- `Settings` field names map to `ADG_*` variables; renaming one is a breaking deployment
  change and must be recorded in `.env.example` and an ADR.

## Prerequisites for the next prompt (Phase 0A — domain model)

1. Read `README.md`, `docs/architecture/system-overview.md`, and this handoff first.
2. The workspace is at `C:\code\adg` on branch `master`; the backend venv is
   `backend\.venv` (`.\.venv\Scripts\python.exe`), frontend deps are installed.
3. Put typed domain classes/enums under `backend/app/models/` (or a new
   `backend/app/domain/` package) and their tests under `backend/tests/`. Keep them
   framework-free and deterministic: no FastAPI, no SQLAlchemy in the domain types.
4. Do not add an effective-access algorithm, recursive membership expansion, or
   collectors — those are explicit non-goals of Phase 0A.
5. Any new module must pass `ruff check`, `ruff format --check`, and **mypy strict**
   (`.\scripts\backend-lint.ps1`) — strict mode is already enabled, so annotate everything.
6. Create the four required ADRs from `docs/decisions/adr-template.md`, numbered
   `0001`–`0004`, and add them to the index table in `docs/decisions/README.md`.
7. Extend `docs/architecture/system-overview.md`'s identity section rather than contradicting
   it: SID-as-identity is already recorded there.
8. Stop the stack with `.\scripts\stack-down.ps1` when finished, or leave `-DbOnly` running.

## `git status --short`

Captured immediately before the phase commit (every path is new in this phase):

```text
?? .dockerignore
?? .editorconfig
?? .env.example
?? .github/
?? .gitignore
?? README.md
?? SECURITY.md
?? backend/
?? collector/
?? database/
?? docker-compose.yml
?? docker/
?? docs/
?? frontend/
?? scripts/
```

Committed as `6b36e5a` (bootstrap) and `a391a15` (line-ending normalization). The
working tree is clean apart from this handoff's own commit.
