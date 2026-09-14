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
  app/            Application package (api, models, access_engine, history, ...).
  tests/          Backend test suite.
frontend/         Next.js + TypeScript web application.
database/         PostgreSQL schema migrations.
  migrations/     Alembic migration scripts.
  seed/           Development seed data.
docker/           Container build definitions.
docs/
  architecture/   System and domain architecture, and what the MVP can answer.
  contracts/      Collector/API payload contracts.
  decisions/      Architecture decision records.
  handoffs/       Per-phase implementation handoffs.
  operations/     Runbook: install, verify, point at a domain, operate.
scripts/          Windows developer commands (PowerShell 7).
  windows-test-tree/  Builds real NTFS trees to validate and benchmark the collector against.
```

Two documents are the place to start:
[`docs/operations/mvp-runbook.md`](docs/operations/mvp-runbook.md) to get it running, and
[`docs/architecture/mvp-capabilities.md`](docs/architecture/mvp-capabilities.md) for what it
answers and — just as deliberately — what it does not.

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

# Fill a local database with the demo estate (AD + SMB + NTFS), through the ingestion API.
# Deterministic, idempotent, and deliberately imperfect — see --features for why.
.\scripts\seed-demo.ps1
.\scripts\seed-demo.ps1 -Profile large                         # for measuring, not demonstrating
.\scripts\seed-demo.ps1 -OutDir .\.tmp\demo                    # write the transcripts, post nothing

# Collectors
.\scripts\collector-test.ps1                                   # Pester suites
.\scripts\validate-collector-output.ps1 -Path .\out            # check a collector's JSON, offline
.\scripts\validate-collector-output.ps1 -Path .\out -Strict    # warnings fail the build too

# Membership-graph cost. Not a test; the recorded numbers and query plans live in
# docs\architecture\ad-graph-validation.md.
.\scripts\graph-benchmark.ps1
.\scripts\graph-benchmark.ps1 -Scale large -Database   # needs the database up

# NTFS scan cost, against a generated tree on a real volume. Also not a test; the numbers
# and query plans live in docs\architecture\ntfs-scan-performance.md.
.\scripts\ntfs-benchmark.ps1
.\scripts\ntfs-benchmark.ps1 -Scale medium -Database   # adds the ingestion and query suites

# Build that tree on its own, to point a collector at it by hand.
.\scripts\windows-test-tree\New-AdgTestTree.ps1
.\scripts\windows-test-tree\New-AdgTestTree.ps1 -Remove

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

> **Everything below requires authentication.** The four endpoints above, plus
> `GET /auth/config`, are the only ones that answer without a credential. Every other route
> needs a bearer token whose principal holds the capability that route declares; ingestion
> additionally accepts a collector key. See
> [`docs/architecture/authentication.md`](docs/architecture/authentication.md).

Authentication:

| Endpoint | Purpose |
| --- | --- |
| `GET /auth/config` | How this deployment signs in: mode, issuer, client id, endpoints, and the role table. Public. |
| `GET /auth/me` | The caller's roles and **capabilities**. What the web application reads to decide what to show. |
| `POST /auth/dev/login` | **Development mode only**, and not registered at all otherwise. Issues a token for a configured account without verifying any credential. |

Collector ingestion (contract v1; see
[`docs/contracts/collector-protocol.md`](docs/contracts/collector-protocol.md)):

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/scan-runs` | Begin a scan run. `201` new, `200` replayed, `409` conflicting. |
| `POST /api/v1/scan-runs/{run_id}/batches` | Send up to 1000 observations. Idempotent on `batch_id`. |
| `POST /api/v1/scan-runs/{run_id}/completion` | Close a run and record any reconciled scopes. |
| `GET /api/v1/scan-runs/{run_id}` | Inspect a run: status, coverage, counts, collector errors. |
| `GET /api/v1/scan-runs` | Runs newest first, filterable by collector and status. |
| `GET /api/v1/collection/status` | **Whether an empty result can be believed.** One verdict over the latest run of each scope — one collector against one target. |
| `GET /api/v1/collection/operations` | The collector status page: last success and last failure of every scope, what each run failed to deliver, how many objects are stored, and every reported error grouped by code. |

> The three `POST` routes authenticate with a collector key in `X-ADG-Collector-Key`
> (`ADG_COLLECTOR_API_KEYS`), or with an administrator's token. **Ingestion is never
> anonymous**, and a collector key grants `collectors:ingest` and nothing else — it cannot
> read a single observation back.
>
> `GET /api/v1/collection/status` is what every view consults before rendering an empty one.
> `no_data`, `incomplete` and `failed` all mean *nobody looked*; only `healthy` makes an
> empty list an answer. See
> [ADR-0015](docs/decisions/0015-emptiness-is-attributed-by-the-backend.md).

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
| `GET /api/v1/resources/{path}` | One resource's descriptor facts — owner, NULL-DACL state, inheritance — and its boundary verdict beside the one the server derives. |
| `GET /api/v1/resources/{path}/acl` | That resource's NTFS ACL in evaluation order, with the ACL digest. |
| `GET /api/v1/principals/{sid}/shares` | Shares whose ACL names a SID. |
| `GET /api/v1/search?q=` | One box for four lookups: a SID, a UNC path, a share name, or the start of a display name. |

> Search reports how it read the term, which categories it truncated, and which the account
> was not permitted to search. An empty result is always attributable.

> Each ACL response carries a `kind` — `raw_smb_acl` or `raw_ntfs_acl`. **These are observed
> facts of one layer, not effective access** — that requires *both* layers plus group
> expansion, and arrives as its own representation. A trustee with `resolved: false` is an
> orphaned SID on a live ACL, which is a finding rather than an error.
>
> The two layers are separate routes on purpose. Remote access over SMB is limited by the
> share ACL **and** the NTFS ACL; access at the console is limited only by the second. A
> share whose NTFS root nothing has read reports `root_resource: null` — *nobody has looked*,
> never *nothing restricts it*.
>
> A resource's `boundary` block is where the tree scan's answer lives: whether permissions
> change here, why, and what the server derives independently from the parent it holds. It is
> compared against what the parent **projects** onto a child rather than against the parent's
> own digest — see [ACL boundaries](docs/architecture/ntfs-acl-boundaries.md).

The web application's **System status** page (`http://localhost:3000/status`) renders the
same information from the browser's point of view.

## The web application

`http://localhost:3000`. Sections: Overview, Resources, Identities, Access, Risks (placeholder),
Changes (placeholder), Collectors, Settings — each shown only when the signed-in account holds
the capability behind it.

In development mode, sign in at `/login` and pick `viewer`, `auditor`, or `admin` to see the
product as that role sees it. **A development deployment verifies no credential**, and says so
in an undismissable banner on every page.

### Detail pages

Four pages carry the everyday work, each reached from search or from a link on another:

| Page | URL | What it answers |
| --- | --- | --- |
| Principal | `/identities/principal?key=<SID or host\|SID>` | Who this user or group is, what it belongs to, what belongs to it, which share ACLs name it, and what it can reach |
| Server | `/resources/server?key=<host>` | What ADG knows about the machine, when a collector last reached it, and the shares it publishes |
| Share | `/resources/share?key=<server\|share>` | Share metadata, the share ACL, the NTFS ACL of the directory it publishes, and who gets through both |
| Directory | `/resources/directory?key=<UNC path>` | Where a directory sits, whether permissions changed there, the ACL digest, and who can reach it |

Identifiers travel as query parameters rather than as path segments: a directory is named by
its UNC path and a local group by `host|SID`, and `\` and `|` inside a URL *path* depend on
the browser, the Next.js router, and the Node runtime agreeing about normalization.

Five properties of these pages are worth knowing before reading the code:

- **The browser never holds the access token.** It lives in an `httpOnly` cookie that only
  the Next.js server reads; browser requests go to `/api/adg/*` on the same origin and the
  server attaches it.
- **No page interprets its own emptiness.** Each one is handed a verdict from
  `GET /api/v1/collection/status` and renders it, so "nothing is there" and "nobody looked"
  are different screens. Placeholder sections say they are placeholders rather than showing
  an empty table.
- **A raw ACL and an effective-access answer never share a frame.** Each section is labelled
  with what kind of statement it is — *raw, as collected* or *effective, computed* — carries
  its own caveat, and is tinted differently. Reading one as the other is the most common
  wrong conclusion in this domain, and the reason this product exists.
- **The SID is on screen beside every name**, and when two rows in one list share a name the
  qualifier — the host, or the domain SID — is promoted next to it. Two
  `BUILTIN\Administrators` rows are two groups, not a duplicate.
- **Each section is one query, chosen by the `?tab=` in the URL**, and every list pages
  server-side with its position in the URL. Nothing computes permissions in the browser.

The **Collectors** page (`/collectors`) is where every empty table points, and it is the
operator's screen rather than the auditor's. It carries one row per *scope* — one collector
against one target — with the **last successful** scan and the **last failed** scan side by
side, because those are two facts: a scope whose latest run failed still has data on screen,
and the page has to say how old it is. Beside them: what each run failed to deliver, how many
objects are actually stored (a collector that ran cleanly and wrote nothing reports
`succeeded` everywhere else in the product), and every reported error grouped by code.

## Architecture

See [`docs/architecture/system-overview.md`](docs/architecture/system-overview.md).
Key invariants:

- **SID is the canonical identity key.** Names, UPNs, and DNs are metadata.
- **Windows collection is native**, never containerized.
- **The backend owns authorization semantics.** Collectors report observations; the
  frontend renders API results. Neither performs permission math.
- **The authorization boundary is the backend's.** Capabilities are checked per route in
  `backend/app/api/__init__.py`; the frontend hiding a section is a courtesy, not a control
  ([ADR-0014](docs/decisions/0014-authorization-is-capabilities-enforced-in-the-backend.md)).
- **Least privilege.** Normal collection must never require Domain Admin.
- **Coverage is judged per scope**, `(collector, target)` — not per collector kind. A later
  success on one file server must never hide a failed scan of another, because every empty
  list below the failed one would then read as an answer.
- **Nothing is ever marked absent by a run that did not reconcile a scope.** A failed,
  partial or incremental scan produces *fewer observations*, not evidence of removal
  ([ADR-0018](docs/decisions/0018-history-is-a-versioned-observation-log.md)).
- **A change is recorded as a window, not an instant.** A collector samples rather than
  watches, so a change is known to have happened between the last confirmation and the
  contradicting reading, and that interval is what is reported
  ([ADR-0019](docs/decisions/0019-a-change-is-a-window-not-an-instant.md)).

### History

Every collected object carries a timeline: one version per state it was observed to hold,
with the interval it was observed over, and a tombstone when an authoritative full scan of a
reconciled scope looked and did not find it. `HistoryService` answers as of a past instant —
membership, the raw SMB and NTFS ACLs, resource existence, and effective access, the last of
these through the same engine that answers live questions. Every answer reports how firmly it
is grounded: `observed`, `inferred`, `backfilled`, or `unobserved`.

Current-state tables are unchanged, so every existing query behaves exactly as before — and
so a live answer can still count a grant a reconciled scan has proved is gone. See
[`docs/architecture/history-model.md`](docs/architecture/history-model.md) §9.

History is never deleted by default. `ADG_HISTORY_RETENTION_DAYS` and
`ADG_HISTORY_RETENTION_ENABLED` must both be set for a prune to be possible at all, and it
never removes an object's open version or its newest closed one.

## Security

See [`SECURITY.md`](SECURITY.md) and
[`docs/architecture/authentication.md`](docs/architecture/authentication.md). No secrets
belong in source control: `.env.example` is a template, and `.env` is ignored by Git.

The API **refuses to start** with `ADG_AUTH_MODE=development` and
`ADG_ENVIRONMENT=production`, so a deployment that verifies no credential cannot be shipped
by accident.

Every log record passes a redaction filter before it is formatted: bearer tokens, JWTs,
collector keys, `ADG_*` secrets and passwords inside connection strings are replaced with
`[redacted]` — in the message, in structured `extra=` values, and in tracebacks. SIDs, UNC
paths and account names are **not** redacted, deliberately: they are what an operator
correlates against a Windows event log, and they are the subject of the product. That makes
the log stream personal data rather than credential data; see
[`docs/operations/mvp-runbook.md`](docs/operations/mvp-runbook.md) §6.
