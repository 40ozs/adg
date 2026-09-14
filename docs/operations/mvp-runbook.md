# ADG MVP runbook

**Status:** accepted (Phase 6D)
**Audience:** someone setting ADG up on a clean Windows workstation for the first time —
to develop on it, to demonstrate it, or to point it at a real domain.
**Covers:** Phases 0 through 6. There is no remediation and no change history yet; see
[`mvp-capabilities.md`](../architecture/mvp-capabilities.md) for what the release does and
does not answer.

Every command below was run on Windows 11 with PowerShell 7 and Docker Desktop. Run them
from the repository root unless a step says otherwise.

---

## 1. Prerequisites

| Tool | Version | Why |
| --- | --- | --- |
| Windows | 10/11 or Server 2019+ | The collectors are native Windows processes |
| PowerShell | **7+** (`pwsh`) | Collectors and scripts. Windows PowerShell 5.1 is not enough |
| Docker Desktop | current | PostgreSQL, and optionally the API and web tiers |
| Python | 3.11+ | The backend |
| Node.js | 20+ | The frontend |
| Pester | 5+ | Collector tests: `Install-Module Pester -MinimumVersion 5.0 -Scope CurrentUser` |

Docker Desktop must be **running**, not merely installed. `docker info` failing is the most
common first-run problem and `scripts/stack-up.ps1` says so explicitly.

---

## 2. First run: from a clean clone to a working product

```powershell
# 1. Dependencies, virtual environment, and a local .env from the template.
#    Never overwrites an existing .env.
.\scripts\bootstrap.ps1

# 2. PostgreSQL only. The API and web tiers run natively while developing, which is
#    faster to iterate on and gives readable tracebacks.
.\scripts\stack-up.ps1 -DbOnly

# 3. Create the schema. The container does NOT migrate on start: a service that migrates
#    itself migrates on every restart and on every replica.
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
cd ..

# 4. The API. Use `python -m app`, not a bare uvicorn command — see §7.
cd backend
.\.venv\Scripts\python.exe -m app --port 8000
# ... in a second terminal:
cd frontend
npm run dev
```

Then:

```powershell
# 5. Fill it with something to look at.
.\scripts\seed-demo.ps1
```

Open <http://localhost:3000>, sign in as `auditor`, and start at **Collectors**. The demo
estate is deliberately imperfect, so that page will read `failed` — that is the point, not a
setup error. `python -m app.demo --features` (from `backend\`) prints every deliberate flaw
and why it is there.

### The whole stack in containers instead

```powershell
.\scripts\stack-up.ps1 -Build      # PostgreSQL + API + web
# Still migrate from the host, once:
cd backend; .\.venv\Scripts\python.exe -m alembic upgrade head; cd ..
.\scripts\seed-demo.ps1
.\scripts\stack-down.ps1
```

---

## 3. Verifying the install

In order, cheapest first. Each is independent.

```powershell
# Backend, hermetic. No database, no network.
.\scripts\backend-test.ps1

# Backend, including everything that needs PostgreSQL — the ingestion endpoints, the
# resolver, the end-to-end walkthrough, and the query-cost budgets.
.\scripts\backend-test.ps1 -Smoke

# Lint and types.
.\scripts\backend-lint.ps1

# Frontend: lint, types, unit tests, production build.
.\scripts\frontend-check.ps1

# Collectors (Pester). Every remote call is mocked, so this runs on a workstation with no
# domain, no file server and no network.
.\scripts\collector-test.ps1
```

`-Smoke` uses a **separate database** — the development database name with `_test` appended
— created and migrated on first use. Your own data is never truncated by a test run.

**The single most useful check that the whole product works** is
`backend/tests/db/test_mvp_end_to_end.py`, which is part of `-Smoke`. It seeds the demo
estate through the real ingestion endpoints and then walks collection → ingestion → resolver
→ explanation → every page's own API call, as an auditor.

---

## 4. Pointing it at a real domain

The collectors are native Windows processes. They are **read-only** and none of them needs
Domain Admin.

### Least privilege

| Collector | Needs | Does not need |
| --- | --- | --- |
| Active Directory | Read on the directory (any authenticated domain account) | Domain Admin |
| SMB | Read the share list and share ACLs on each file server (`Get-SmbShare`) | Local Administrator, in general — but some servers restrict the SMB cmdlets |
| NTFS | **Read permissions** (`READ_CONTROL`) on the directories walked | Read of file *contents* |

`READ_CONTROL` is the one to get right: it is the right to read a security descriptor, which
is not the same as the right to read the data. A service account with `READ_CONTROL` and no
data access can audit a share it cannot open. Where the account lacks it, the collector
reports `access_denied` for that object and the scan is recorded as **partial** — visible on
the Collectors page, never silently skipped.

### Credentials

Ingestion authenticates with a collector key, not a user token:

```powershell
# On the API host, in .env — one entry per collector host.
ADG_COLLECTOR_API_KEYS=fs01:<a long random value>,fs02:<another>
```

Keys are compared in constant time, and a presented-but-wrong key is refused outright rather
than falling through to the bearer check. **No secret belongs in source control**; `.env` is
git-ignored and `.env.example` is a template with no values in it.

### Running a collection

```powershell
$api = 'https://adg.example.com'
$env:ADG_COLLECTOR_KEY = '<the key configured for this host>'

# Identities and memberships, from a domain-joined host.
.\collector\powershell\ad\Invoke-AdgAdCollector.ps1 `
    -ConfigPath .\adg-ad-collector.json -ApiBaseUrl $api

# Shares. -RunPerServer opens one scan run per server, so a server that fails does not take
# the others' coverage down with it.
.\collector\powershell\smb\Invoke-AdgSmbScan.ps1 `
    -ApiBaseUrl $api -ConfigPath .\adg-smb-targets.json -RunPerServer `
    -CollectorKeyEnvironmentVariable ADG_COLLECTOR_KEY

# File-system descriptors. -SafeDefaults bounds depth and breadth, so a first run against an
# unfamiliar estate cannot become an unbounded walk.
.\collector\powershell\ntfs\Invoke-AdgNtfsScan.ps1 `
    -ApiBaseUrl $api -ConfigPath .\adg-ntfs-targets.json -SafeDefaults -RunPerScanRoot `
    -CollectorKeyEnvironmentVariable ADG_COLLECTOR_KEY
```

Each collector directory has an `*.example.json` beside it to start from. The SMB and NTFS
collectors both accept `-DryRun -OutputDirectory <dir>`, which writes the transcript to disk
and posts nothing — the right way to see what a collector *would* send before letting it
send anything.

The key is read from the **environment variable you name**, not from the command line: an
argument is visible in the process list and in shell history, and this one is a credential.

Validate a payload before you trust it:

```powershell
.\scripts\validate-collector-output.ps1 -Path .\out\run-001.json
```

---

## 5. Reading the Collectors page

This is the page to check first when anything looks wrong, and the one every empty table
elsewhere links to.

| What it says | What it means | What to do |
| --- | --- | --- |
| `complete` | Succeeded, no errors, every batch delivered, every declared scope reconciled | Nothing |
| `incomplete` | It ran and something is missing — read the shortfall on the row | Fix what the shortfall names, then re-scan |
| `nothing collected` | The latest run failed or was canceled | Check **Last successful scan** on the same row for how old your data is |
| `running` | Not finished. The numbers below it are a partial tally | Wait |

Three columns matter together, and the page shows all three on purpose:

* **Completeness** is about the *latest* attempt.
* **Last successful scan** is how old the data on screen actually is. A scope whose latest
  run failed still has data; the page marks that success as *not the latest attempt*.
* **Last failed scan** is there even when the latest run succeeded, because a scope that
  fails every other night is not the same as one that has never failed.

A scope that has **never** succeeded reads `never` in red. That is the worst state on the
page: nothing about that part of the estate has ever been collected, and every list below it
is unknown rather than empty.

**Errors** are grouped by code, widespread ones first. Twelve `access_denied` on one share is
a permission on that share; twelve across several collectors is the service account.

**What is stored** is the panel that catches the failure nothing else reports: a collector
that ran cleanly and wrote nothing reports `succeeded` everywhere in the product except in a
count of zero.

---

## 6. Operating it

### Backups

The database is the only durable state. Everything in it is derived from collector runs and
can be rebuilt by re-scanning, but the run history — what was observed when — cannot.

```powershell
docker exec adg-db-1 pg_dump -U adg -d adg -Fc > adg-$(Get-Date -Format yyyyMMdd).dump
```

### Logs

The API logs one JSON object per line (`ADG_LOG_FORMAT=json`; `text` for local reading).
Every record passes a redaction filter before either format renders it: bearer tokens, JWTs,
collector keys, `ADG_*` secrets and passwords in connection strings are replaced with
`[redacted]`, in the message, in `extra=` values, and in tracebacks.

**Identifiers are deliberately not redacted.** SIDs, UNC paths, account names and group names
appear in full, because they are what an operator correlates against a Windows event log and
a collector log — and because ADG's whole subject is who and what. That makes the log stream
**personal data**: apply the retention and access controls you would apply to a directory
export, and do not ship it somewhere your identity data may not go.

### Upgrading

```powershell
.\scripts\stack-down.ps1
git pull
.\scripts\bootstrap.ps1
cd backend; .\.venv\Scripts\python.exe -m alembic upgrade head; cd ..
.\scripts\stack-up.ps1
```

Migrations are additive within contract v1. Collectors and the API are versioned together by
the contract, not by release: a 1.2 collector can post to a 1.3 API.

### Going to production

The API **refuses to start** with `ADG_ENVIRONMENT=production` and development
authentication, so a deployment cannot accidentally ship with no front door. For a real
deployment:

```powershell
ADG_ENVIRONMENT=production
ADG_AUTH_MODE=oidc
ADG_OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0
ADG_OIDC_AUDIENCE=api://adg
ADG_OIDC_JWKS_URL=https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys
ADG_OIDC_CLIENT_ID=<app registration id>
ADG_WEB_URL=https://adg.example.com        # https, or the session cookie is discarded
```

Assign the `viewer`, `auditor` and `admin` app roles in Entra ID. A role ADG does not
recognize grants nothing and is reported on `/auth/me` as unrecognized, so a typo shows up as
a log line and a note on screen rather than as a blank product.

---

## 7. Things that will bite you

**Start the API with `python -m app`.** Recent uvicorn builds construct their event loop from
a factory and ignore the asyncio policy, so a bare `python -m uvicorn app.main:app` on
Windows gets a `ProactorEventLoop`, which psycopg refuses in async mode — every request 500s
with `Psycopg cannot use the 'ProactorEventLoop'`. `app/runtime.py` exists for exactly this.
If you must use uvicorn directly, pass `--loop app.runtime:event_loop_factory`.

**The container cannot reach `localhost`.** Inside the web container, `localhost` is the web
container. `ADG_INTERNAL_API_URL=http://api:8000` carries the address for server-side
rendering; `NEXT_PUBLIC_ADG_API_URL` is the browser's. The `/status` page reports both, so a
misconfiguration is visible rather than mysterious.

**Migrate before you seed.** The API starts happily against an unmigrated database and fails
on the first query. `/health/ready` reports the database as reachable, not as migrated.

**Seeding twice is a replay, not a second estate.** Run ids are derived from the estate, so
`seed-demo.ps1` run twice leaves one estate behind and reports that it applied nothing. Use
`-FreshRunIds` when you actually want a second set of runs in the history.

**A `409` from `/scan-runs/{id}/batches` means the run is closed.** Stop sending batches and
start a new run — that is the contract, and a batch arriving after a completion means the
collector and the server disagree about whether the run finished.

**Use a phase-scoped database for experiments.** `CREATE DATABASE adg_<something>`, migrate
it, point `ADG_DATABASE_URL` at it, and drop it afterwards. The shared `adg` database is
where someone else's data is.

**`ADG_*` environment variables beat `.env`.** Convenient for a one-off (`$env:ADG_DATABASE_URL
= '...'; python -m app`) and confusing later. Check `/status` if the API is reading something
you did not expect.

---

## 8. Where things are

| | |
| --- | --- |
| Collector protocol | `docs/contracts/collector-protocol.md` |
| Published OpenAPI | `docs/contracts/v1/openapi.json` (regenerate: `python -m app.contracts.openapi`) |
| What the MVP answers | `docs/architecture/mvp-capabilities.md` |
| Decisions and their reasons | `docs/decisions/` |
| Phase-by-phase history | `docs/handoffs/` |
| Security posture | `SECURITY.md` |
