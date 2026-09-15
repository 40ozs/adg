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
  app/            Application package (api, models, access_engine, history, simulation, ...).
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
| `GET /api/v1/changes?from=&to=` | What moved in a window, classified and keyset-paginated. Scope with one of `server`, `share`, `directory`, `principal`, `group`. |
| `GET /api/v1/changes/summary` | Counts over the same window taken **before** the filter, so a page can say what it is not showing. |
| `GET /api/v1/changes/timeline?kind=&key=` | One object's whole history, as transitions rather than versions. |
| `GET /api/v1/changes/compare?from=&to=` | What is *different* between two instants — a different question from the feed, with objects nothing covers counted rather than reported as created or deleted. |
| `GET /api/v1/changes/impact?kind=&key=&at=` | Why access changed: effective access either side of one edit, by the live engine. Requires `access:read`. |

### Risks and alerts

| Route | What it answers |
| --- | --- |
| `GET /api/v1/risks/summary` | Severity counts, **what the last evaluation covered**, and what the rule configuration is hiding. Read the coverage before reading the counts. |
| `GET /api/v1/risks/findings` | Findings, filtered by severity, rule, confidence, place, principal and when they were first or last seen. An unknown filter value is refused, never ignored. |
| `GET /api/v1/risks/findings/{key}` | One finding with **the records it was matched on**, its timeline, the remediation, and whether it still re-derives from its own evidence. |
| `GET /api/v1/risks/rules` | The rule catalog as configured — **including the rules that are off**, whose silence is a decision rather than a result. |
| `GET /api/v1/risks/evaluations` | Recent passes: what ran, what it covered, what it decided. |
| `GET /api/v1/alerts` | The alert feed, with counts per trigger including the empty ones. |
| `GET /api/v1/alerts/{key}` | One alert and **every occurrence of it, suppressed ones included**, with the reason each was held. |
| `GET|POST /api/v1/alerts/watches` | Standing subscriptions, and the table of what each kind of watch can be told about. Writing needs `alerts:manage`. |
| `GET /api/v1/alerts/deliveries` | Queue depth, staleness, abandonment, and the policy in force — the four numbers that separate a quiet estate from a stopped pipeline. |

> **No route starts an evaluation.** Reading a report must not be able to trigger the most
> expensive thing in the product. An evaluation is `python -m app.operations evaluate-risks`,
> or a consequence of a scan run closing when `ADG_ALERTS_ON_RUN_COMPLETION` is set.

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

`http://localhost:3000`. Sections: Overview, Resources, Identities, Access, Risks, Changes,
Collectors, Settings — each shown only when the signed-in account holds
the capability behind it.

In development mode, sign in at `/login` and pick `viewer`, `auditor`, `admin`, `reviewer`
or `governance` to see the
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
- **A what-if is the production engine reading a proposal, never a second calculator.** A
  simulation substitutes the engine's inputs and changes nothing about the engine — or about
  the estate
  ([ADR-0020](docs/decisions/0020-simulation-is-the-engine-reading-an-overlay.md)).

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

### Changes

`/api/v1/changes` turns those timelines into findings: what moved in a window, classified
along four independent axes — what happened, whether it is about access, which way access
moved, and how much attention it deserves ([ADR-0027](docs/decisions/0027-a-change-is-classified-not-scored.md)).

Four things it refuses to say, and each is a failure mode of an ordinary scan diff:

- **A gap is never a removal.** A removal exists only where a scan that reconciled a scope
  looked and did not find the object.
- **The start of observation is never a creation.** An estate's first scan produces one
  `first_observed` per object. They are excluded from the default view and counted in the
  summary.
- **A change is never dated to the scan that found it.** Every change carries both ends of
  the window it happened inside.
- **A filtered page always says how much it is hiding.** `GET /changes/summary` counts the
  whole window before the filter.

An ACL edit is read as one edit rather than as an unrelated removal and addition, and a
renumbered ACE is compared through the same normalized DACL the collector uses, so a
reordering that changes nothing produces no diff. `GET /changes/impact` answers *why access
changed* by resolving effective access either side of one edit through the live engine — and
routinely disagrees with the edit's own direction, which is the point.

See [`docs/architecture/change-detection.md`](docs/architecture/change-detection.md).

History is never deleted by default. `ADG_HISTORY_RETENTION_DAYS` and
`ADG_HISTORY_RETENTION_ENABLED` must both be set for a prune to be possible at all, and it
never removes an object's open version or its newest closed one.

### Collection cadence

Collection runs as six independent jobs — AD principals, AD memberships, SMB inventory, NTFS
important roots, NTFS deep scan, and a full reconciliation — each with its own schedule, its
own resume point, and its own failure. One Windows scheduled task drives all six; the cadence
lives in the orchestrator configuration rather than in the scheduler, so it cannot drift
between the two.

Active Directory is read incrementally, filtered on `uSNChanged`. The watermark is tied to
the *incarnation* of the domain controller that issued it — `dsServiceName` and
`invocationId` together — because binding a different DC, or the same DC after a restore from
backup, makes the number mean something else, and resuming anyway would skip whatever fell
below it. Either event costs one full read, which is the error worth having.

The file system offers nothing equivalent: **writing an ACL does not move a directory's
`LastWriteTime`**, so a scan that skipped unchanged-looking directories would skip exactly
the changes ADG is for. An NTFS scan therefore reads every descriptor every time and sends
only the ones whose digest changed, *affirming* the rest with a key and a digest the server
verifies against what it holds. It remains a complete enumeration, so it keeps the right to
reconcile.

**No incremental run may mark anything absent.** Nothing announces a deletion to a query that
filters on change metadata — a deleted principal simply fails to appear, which is what an
unchanged one does — so absence is discovered only by the full reconciliation, on its own
schedule, which is therefore the upper bound on how long ADG can believe in access that no
longer exists. What each reconciliation had to correct is counted and reported as drift
([ADR-0025](docs/decisions/0025-incremental-collection-is-bounded-by-its-source.md),
[ADR-0026](docs/decisions/0026-an-affirmation-is-verified-and-a-checkpoint-trails-its-data.md)).

See [`docs/architecture/incremental-collection.md`](docs/architecture/incremental-collection.md)
and [`collector/powershell/orchestrator/README.md`](collector/powershell/orchestrator/README.md).

### Risk findings

Eleven deterministic rules read the collected permission facts and report what should not be
like this: `Everyone` on a share, a user named directly on an access control list, an
orphaned SID, a disabled account that still holds rights, a group that grants access and has
no members, inheritance broken below a share root, and so on.

There is no score and no model. A finding names the exact rule that produced it and **carries
the complete records it was computed from**, so the engine can rebuild the facts from that
evidence alone and re-derive the finding months later — which is how you answer *"was this
actually true, on the evidence you kept?"* about a finding from last quarter
([ADR-0023](docs/decisions/0023-risk-findings-are-reproducible-from-their-evidence.md)).

Severity is configuration and confidence is derived. A rule reports which *shape* it saw and
your configuration decides what that is worth, so quieting a noisy rule never makes it stop
looking. Confidence is computed from the facts — a DACL projected from an ancestor, a
truncated group expansion — and cannot be set.

Findings resolve and reopen as the estate changes, and nothing is deleted: *"Everyone had
Modify on the payroll share between March and June"* stays true after the entry is removed. An
evaluation may only resolve findings it actually covered, so an incremental pass after a
collector run cannot empty the report.

**ADG never guesses which data matters.** The sensitive-resource rule reports nothing until an
operator declares which resources are sensitive in `ADG_RISK_CONFIGURATION_PATH`
([ADR-0024](docs/decisions/0024-sensitivity-is-declared-not-inferred.md)). See
[`docs/operations/risk-rules.md`](docs/operations/risk-rules.md) for the configuration
reference and [`docs/architecture/risk-model.md`](docs/architecture/risk-model.md) for the
model.

The **Risks** page shows all of it, with the caveat that makes it readable: above the counts,
what the last evaluation actually covered. An empty table under *"the rules have never been
evaluated"* means nothing has looked — which is the single most dangerous sentence this
product could accidentally say, and the one it therefore says out loud. Each row opens an
evidence drawer holding the stored records themselves rather than a rendering of them, plus
whether the finding still re-derives from them.

Nothing runs by itself. Evaluate the rules with `python -m app.operations evaluate-risks`, or
set `ADG_ALERTS_ON_RUN_COMPLETION=true` to do it when a scan run closes.

### Alerts

Put a **watch** on a directory, a share or a group and ADG tells you when its permissions
change, when its membership changes, or when somebody's effective access to it grows — three
separate questions, because an entry can be added that grants nothing and access can widen
with no entry touched. A critical risk finding opening alerts on its own, with no watch:
requiring a subscription would make the feature's coverage equal to somebody's foresight.

Two properties are what make the feed believable when it is quiet.

**A suppressed alert is recorded, never dropped.** A cooldown decides what is *delivered* and
nothing decides what is *written down*, so "we were not told" and "it did not happen" stay
separable afterwards — and the next notification says how many occurrences it stands for
([ADR-0032](docs/decisions/0032-a-suppressed-alert-is-recorded-never-dropped.md)).

**Delivery cannot cost you an observation.** Alerts are enqueued in the same transaction that
raises them and delivered in a separate pass, so a webhook that is down can never roll back
the ingestion that produced the alert
([ADR-0033](docs/decisions/0033-raising-an-alert-and-delivering-one-are-separate-transactions.md)).
An alert that is never delivered is visible and permanent rather than silent.

Deliver `python -m app.operations drain-alerts --loop`. See
[`docs/operations/alerting.md`](docs/operations/alerting.md) for the policy file and
[`docs/architecture/alerting.md`](docs/architecture/alerting.md) for the design.

### What-if simulation

*"If I take this group off the ACL, who loses access?"* is answered by running the **real**
effective-access engine twice — once over the collected state, once over the same state with a
proposed change overlaid — and comparing the two answers. There is no simplified simulation
arithmetic, because a second implementation of the access check would eventually disagree with
the first and nobody would know which to believe
([ADR-0020](docs/decisions/0020-simulation-is-the-engine-reading-an-overlay.md)).

Nothing is changed by a simulation: not Active Directory, not a share, not an NTFS descriptor,
and not one collected row. A proposal is data, the overlay is applied in memory as rows are
read, and the only rows written anywhere are in `simulations` and `simulation_evaluations`,
which no collector, ingestion path or access query reads.

The answer a what-if most often has to give is *"this change does nothing"*: an alternate group
membership keeps the access, a Deny earlier in the DACL meant the entry never granted anything,
or the share ACL was the real limit all along. Surviving routes are enumerated by re-running the
access check rather than reasoned about, and a claim of loss that rests on facts ADG has not
collected is reported as a bound rather than as a number. Every result names the collection
state it was computed against, so *"this was computed before the last scan"* is a fact rather
than a guess.

A proposal is offered from wherever somebody is already looking — a membership row, an SMB
or NTFS entry, a route on the access explanation — as a plain link that opens the proposal
editor with the change already written. Every screen and every response leads with the same
sentence: **no changes will be applied**, served by the API so that three surfaces cannot word
it three ways, beside an `applied: false` field a test can assert on. Reading a stored proposal
needs `simulations:read` and computing one needs `simulations:run`; a plain viewer holds
neither, because a what-if composes two answers a viewer already has into a route map for
privilege escalation
([ADR-0034](docs/decisions/0034-a-simulation-is-offered-not-applied.md)).

**The prediction is validated against the estate, not only against the engine.**
`tests/db/test_simulation_equivalence.py` builds a known permission state, collects it,
simulates a change, applies the equivalent change fixture-side, recollects as three
reconciling collector runs, and compares predicted with observed for every supported change
type. The one exception it finds is measured with exact masks rather than described: a
current-state reading does not consult presence, so after a reconciled scan has proved a grant
gone the live engine still counts it — and a point-in-time baseline answers correctly.

See [`docs/architecture/simulation.md`](docs/architecture/simulation.md) for the engine and
[`docs/architecture/simulation-surface.md`](docs/architecture/simulation-surface.md) for the
API, the screens and the equivalence proof.

### Access reviews

A **review campaign** is an access review frozen against an instant. Its items are generated
from the state `object_versions` held at that instant, so a reviewer certifies *what was true
when the campaign was cut* and the statement stays true however the estate moves on — and the
campaign stays **reproducible**: regenerating it from its own row months later yields the same
items with the same digests, which `GET /api/v1/governance/campaigns/{id}/verification`
recomputes and compares
([ADR-0030](docs/decisions/0030-a-campaign-is-a-function-of-a-baseline-instant.md)).

An item is one `(principal, target)` grant and carries every access-control entry that creates
it, frozen as evidence with the `object_versions` row each came from and the certainty it had
at the baseline. Entries a campaign deliberately skipped — inherited ones, well-known trustees
— are counted and reported with its status, so *"47 of 47 certified"* is never readable as
coverage of everything.

**A decision never changes what a collector reported.** A `revoke` produces a *proposed
remediation*: ADG's record of a change somebody might make in Windows. ADG performs none of
them, and nothing in the governance package can write a collected table
([ADR-0028](docs/decisions/0028-governance-is-metadata-about-observations.md)). Recorded
resource ownership is ADG metadata and is kept beside, never merged with, the owner SID
Windows reports — the gap between the two is itself a finding.

Authorization splits three ways, and `governance:manage` deliberately does **not** include
`governance:review`: whoever chooses the questions does not also give the answers
([ADR-0029](docs/decisions/0029-running-a-review-and-answering-one-are-separate.md)). Holding
the capability is not authority over an item either — the item must also be *assigned* to the
caller, checked against the database on every decision. Two roles join the existing three:
`reviewer` and `governance_admin`; a plain `viewer` holds no governance capability at all,
because a decision rationale can name a person and say something about them that no
access-control list ever would.

Attestations are append-only. A changed mind writes a new decision and supersedes the old one,
and every governance act is recorded in a hash-chained audit trail; database triggers refuse
every other update and every delete. See
[`docs/architecture/governance-model.md`](docs/architecture/governance-model.md).

**The reviewer's screens** live under `/governance`: a queue of what was asked of *you*, a
campaign's progress and coverage, and one page per item carrying everything a decision needs —
the frozen entries, what they were worth then and are worth now, why the principal has the
access, any open risk finding about it, and when it last moved. A reviewer answers `approve`,
`propose revoke`, `propose narrower rights`, `needs investigation` or `not mine to judge`, with
a comment the campaign may require on every decision.

Two things that screen refuses to let a reviewer assume. **It says when the grant has changed
since the campaign was frozen** — and keeps "we looked and it is gone" apart from "nobody has
looked", because those send a reviewer to opposite conclusions. The item is never rewritten:
drift is shown beside the frozen evidence, which stays the subject of the decision
([ADR-0031](docs/decisions/0031-drift-is-reported-beside-an-item-never-applied-to-it.md)). And
**it says when revoking an entry would not actually end the access**, because a principal who
also reaches the target through a group keeps it either way — the case where a revoke decision
records something untrue.

Several items can be answered at once only when they are genuinely one question: one principal
across many targets, or one target across many principals, none already decided and none
drifted. Each still gets its own decision, evidence digest and audit event. See
[`docs/architecture/access-review-workflow.md`](docs/architecture/access-review-workflow.md).

## Remediation: proposing a change, and not making one

A review that concludes *"this group should come off the payroll share"* and stops there has
produced a finding nobody can act on. ADG turns it into a **change plan** — and then hands the
plan to a person, because ADG does not change Windows and cannot.

A plan is a title, a reason, and an ordered list of precise steps. Each step names one object
exactly (one ACE key, or one group-and-member pair) and carries the state ADG observed it in.
Four things then have to happen before anything leaves the building:

1. **It is measured.** The plan is translated into a what-if and evaluated by the production
   access engine — the same code `/api/v1/access` answers with, run twice. The report is stored
   as an ordinary simulation you can open and re-run. A plan cannot be submitted without one
   measured against the same collected state the plan was written against.
2. **Somebody else approves it.** The approval records *which version of the plan* and *which
   collected state* it answered about. Editing the plan afterwards invalidates the approval by
   arithmetic rather than by anybody remembering to clear it.
3. **The estate is re-checked.** Every step's frozen before-state is compared with what ADG
   holds now. `changed`, `missing` and `unobserved` each block the export and each say something
   different — *somebody edited this*, *it is gone*, and *nobody has looked*, which lead to three
   different places.
4. **A third person exports it.** The result is a signed JSON document and a PowerShell runbook
   that does nothing without `-Execute` and re-checks every precondition on the machine before
   it touches anything.

```
POST /api/v1/remediation                      write a plan          (remediation:plan)
POST /api/v1/remediation/{id}/simulation      measure it            (remediation:plan)
POST /api/v1/remediation/{id}/submission      put it to an approver (remediation:plan)
POST /api/v1/remediation/{id}/approval        answer it             (remediation:approve)
POST /api/v1/remediation/{id}/exports         sign it               (remediation:export)
GET  /api/v1/remediation/candidates           decisions with no plan yet
GET  /api/v1/remediation/execution-policy     can this deployment change my domain?
```

The last one is worth calling out. The answer is no, and it comes from the executor the running
process actually holds rather than from documentation — because a guarantee you can only check
by reading source code is one most people will not check.

Development accounts: `planner` and `approver`, one role each, so the separation of duties is
visible on your own screen. Set `ADG_REMEDIATION_SIGNING_KEY` before exporting; without it the
export is refused, deliberately.

Full treatment: [`docs/architecture/remediation.md`](docs/architecture/remediation.md). For the
administrator carrying a plan out:
[`docs/operations/remediation-runbook.md`](docs/operations/remediation-runbook.md).

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
