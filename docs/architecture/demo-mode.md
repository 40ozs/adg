# Demo Mode

**Status:** implemented · **Code:** `backend/app/runtime_mode.py`, `backend/app/demo_mode/`,
`backend/app/api/runtime.py`, `backend/app/api/demo.py`, `frontend/components/Banners.tsx`
· **Decision:** [ADR-0039](../decisions/0039-demo-is-a-deployment-boundary.md) · **Evidence:**
[handoff](../handoffs/demo-mode-01.md)

Demo Mode is the real ADG application — the same API, ingestion, history, current-state
derivation, access engine, risk rules, search, simulation, governance and UI — running on a
**synthetic estate** instead of one reported by Windows collectors. One codebase, one image,
one schema. A deployment is either `production` or `demo`, and which one it is is decided by
its configuration, never by anybody using it.

It exists so that ADG can be demonstrated, tested end to end and eventually deployed publicly
without a Windows host, a domain, an SMB share, PowerShell, or a single real identity.

---

## 1. The runtime mode

```text
ADG_RUNTIME_MODE=production   # the default when unset; ADG as it always was
ADG_RUNTIME_MODE=demo         # synthetic data, isolated database, ingestion closed
```

`app.runtime_mode.RuntimeMode` is the typed value; `Settings.runtime_mode` reads it through
`parse_runtime_mode`. **Unknown values fail closed**: `foobar`, `prod`, `live` and an empty
value are startup errors naming the accepted values. Only an *unset* variable means
production, because that is what every deployment predating the setting already is. Case and
surrounding whitespace are forgiven (`Demo` is unambiguous); nothing else is.

It is orthogonal to `ADG_ENVIRONMENT`. `ADG_ENVIRONMENT` says how strict a deployment is
(development defaults allowed or not); `ADG_RUNTIME_MODE` says what it is connected to. A
public portfolio demo is a production-grade deployment of demo mode.

**There is no switch.** No route, header, cookie or UI control changes the mode. `/runtime`
answers `GET` only, the demo indicator renders no control, and a test asserts that no path in
either deployment names a mode change.

## 2. What "production provider" means in ADG, and how demo replaces it

ADG's backend has never contained a directory client, an SMB client or a PowerShell host
(ADR-0004; `tests/remediation/test_no_write_path.py`). Collection happens on Windows, in
`collector/`, and is **pushed** to `POST /api/v1/scan-runs`. So the production provider is the
ingestion API accepting what authenticated collectors send, and the demo boundary is drawn
there:

```text
                          ADG (one codebase)
                                 │
                  ADG_RUNTIME_MODE (configuration)
                 ┌───────────────┴────────────────┐
            production                           demo
                 │                                 │
   CollectorObservationSource         SyntheticObservationSource
   Windows collectors POST            dataset.generations() posted by the
   /api/v1/scan-runs with a           in-process synthetic provider through
   collector key or admin token       the SAME /api/v1/scan-runs routes
                 │                                 │
                 └──────────────┬──────────────────┘
                                ▼
          contract v1 validation → IngestionService → history writer
          → reconciliation → current state (object_versions, tombstones)
                                ▼
           API · access engine · risk · search · simulation · UI
```

`app.demo_mode.providers.observation_source_for(settings)` makes the choice once; the
application factory stores it on `app.state.observation_source`. In demo mode the collector
source is never constructed (a test replaces its class with one that raises).

### The synthetic provider

`app.demo_mode.ingest.apply_generation` builds the ordinary application
(`app.main.create_app`) against the demo database and hands the existing transport,
`app.demo.seed.seed_transcripts`, an in-process ASGI client bound to it. Two dependency
overrides exist **only on that in-process application object**, which never listens on a
socket: the ingestion principal resolves to the collector `demo-synthetic-provider`, and the
demo policy guard is lifted. The deployment's served application has neither, so its
ingestion routes stay closed. The provider refuses to run unless the settings are demo.

Every synthetic observation therefore passes through the real route, the real contract
models, the real `IngestionService`, the history writer and reconciliation — the same path,
status codes and idempotency a collector gets. Demo Mode adds no ingestion code.

## 3. The synthetic data: an existing dependency, consumed through a contract

Demo Mode does **not** generate data. The generators own their organization, identifiers,
timestamps, seed, golden scenarios and determinism; Demo Mode carries them through ingestion
untouched. `app.demo_mode.datasets` is the whole of what it asks of a generator:

| Member | Meaning |
| --- | --- |
| `metadata: DemoDatasetMetadata` | `key`, `company_name`, `domain`, `snapshot_date`, `seed`, `synthetic=True`, `description`, display-only `facts`. Public — `/runtime` serves it and the UI shows it, so the application hard-codes no company. |
| `generations() -> Sequence[ScanGeneration]` | The dataset's scan history, oldest first. Each is a tuple of `app.demo.transcripts.DemoTranscript` — collector-protocol v1 runs. Generation 0 is the baseline the reset installs; later ones are applied one at a time by *Run synthetic scan*. **Run ids must be deterministic** (uuid5): applied-ness is "every run id is in `scan_runs`". |
| `risk_configuration()` *(optional)* | A `RiskConfiguration` declaring the estate's sensitive resources, as an operator would. Used when `ADG_RISK_CONFIGURATION_PATH` is empty; without it the shipped defaults apply. |
| `probes() -> Sequence[CanonicalProbe]` | Objects the dataset promises exist in current state after its baseline, named by its own canonical ids: `principal{sid}`, `membership{group_sid,member_sid}`, `share{server,share}`, `share_ace{server,share,trustee_sid}`, `ntfs_resource{path}`, `ntfs_ace{path,trustee_sid}`. The reset fails if any is missing. |

Registered datasets (`DATASETS`), each a lazy `module:callable` loader:

| Key | Loader | Status |
| --- | --- | --- |
| `null-meridian-v1` | `app.demo.null_meridian:synthetic_dataset` | **Canonical. Pending** the Null Meridian generator (ADG-DEMO-NULL-MERIDIAN-01, branch `claude/inspiring-volta-6uzdsy`). Selecting it before that merges is a startup refusal naming the dependency — never a substitution. |
| `adg-reference-estate` | `app.demo_mode.reference:synthetic_dataset` | Available. A thin adapter over the estate already in `app.demo` (`corp.example.com`); its transcripts are asserted byte-identical to `app.demo`'s own. |

`ADG_DEMO_DATASET` selects one; empty means `null-meridian-v1`. The Null Meridian canon
(company *Null Meridian*, `nullmeridian.info`, snapshot 2026-10-01, seed `null-meridian-v1`,
1,800 active users, Okta 2021-10-01) is defined by that generator and documented with it;
`tests/demo_mode/test_datasets.py::TestNullMeridianCanon` holds the generator's metadata to it
and runs as soon as the module is present.

## 4. Database isolation

A demo deployment wipes and refills its database, so three independent conditions must hold
before it writes or serves anything:

1. `ADG_RUNTIME_MODE=demo`.
2. The database is **named** for the demo: `demo` as a whole word of its name (`adg_demo`,
   `adg-demo`, `adg_demo_test`). `adg`, `adg_prod`, `demolition` are refused at startup.
3. The database **says** it is a demo: its `deployment_marker` row (migration
   `0017_deployment_marker`) reads `runtime_mode='demo'`.

The marker is written in exactly one way — the reset initializing a database — and the reset
initializes only a database holding **no row in any ADG table**. An unmarked database with
data is refused (it may be a production database nobody marked); a database marked for
another mode is refused. The wipe runs in the transaction that locks and re-checks the marker.
A demo API runs the same check in its lifespan (`verify_demo_startup`) and does not start
against an unmarked database or one holding a different dataset than `ADG_DEMO_DATASET`.

Demo Mode uses the real schema; there is no demo-only schema. Production code never reads
the marker, and a production database simply has none.

## 5. Initialize and reset — one operator workflow

```bash
export ADG_RUNTIME_MODE=demo
export ADG_DEMO_DATASET=adg-reference-estate       # or null-meridian-v1 once merged
export ADG_DATABASE_URL=postgresql+psycopg://adg:<pw>@localhost:5432/adg_demo
cd backend
python -m app.demo_mode reset    # first run initializes; every run restores the baseline
python -m app                    # serve
```

`reset` performs, in order: refuse unless demo → load the dataset (refuse if unavailable,
before touching the database) → `alembic upgrade heads` → identify the database (§4) → lock
the marker and truncate every ADG table except it → ingest generation 0 through the synthetic
provider → `app.operations.commands.evaluate_risks` (the production operator command) →
validate: every run recorded with the status its transcript claimed, every probe present in
current state → print a **canonical fingerprint**.

The fingerprint is SHA-256 over current state (all seven `current_*` views), `object_versions`,
`scan_runs`, `scan_run_scopes`, `scan_run_errors` and `risk_findings`, excluding only values
about the process rather than the dataset: row bookkeeping timestamps, risk-evaluation clock
and serials, and database serials. Every identifier, relationship, hash and observed instant
the dataset supplied is in it. Two resets of the same dataset produce the same fingerprint.

Other commands: `status` (dataset, marker, generations, fingerprint — changes nothing),
`scan` (next generation), `verify` (probes + fingerprint). All refuse unless demo. `--json`
prints machine-readable reports.

With Docker: `docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build`.
The override gives PostgreSQL its own volume and an `adg_demo` database, runs a one-shot
`demo-reset` service, and starts the API only after it succeeds.

## 6. Temporal semantics are untouched

Demo data enters through ingestion, so it gets ADG's real observation model: append-only
observations, `object_versions`, current state as "no open tombstone", tombstones only from a
successful authoritative in-scope reconciliation, partial and failed runs closing nothing.
The reference baseline already carries a **partial** NTFS run (FS02) and a **failed** one
(FS03); tests assert they tombstone nothing, that the FS03 share survives its server's failed
scan, that every object has exactly one open version, and that re-posting the partial run
erases nothing. Membership changes, ACL changes, disappearance and tombstones arrive as later
generations from the generator that defines them (Null Meridian); Demo Mode applies them and
does not author a second history.

## 7. Mutation policy

Every mutating route is classified in `app.demo_mode.policy.MUTATION_POLICY`;
`tests/demo_mode/test_boundary.py` holds the table exhaustive against the OpenAPI document. The
guard is a backend dependency on every API route (`build_api_router`), not a hidden button.

| Class | Routes | Demo behavior |
| --- | --- | --- |
| **A — supported** | governance campaigns, assignments, decisions, owners; remediation plans (write, edit, simulate, submit, approve, cancel); simulations; alert watches; alert drain; development sign-in; `POST /api/v1/demo/scans` | Unchanged. These write ADG's own records, which live in the demo database. Alert drain delivers to log sinks only (below). |
| **B — simulated** | `POST /api/v1/remediation/{plan_id}/exports` (and reading an export's runbook) | The plan is signed and audited as usual; the PowerShell runbook names fictional objects, so it is served with **every line commented out** under a banner and a single `throw`, and the response carries `demo_notice: "Simulation only — no directory or filesystem change was performed."` plus the `X-ADG-Demo-Notice` header. |
| **C — unavailable** | `POST /api/v1/scan-runs`, `/{run_id}/batches`, `/{run_id}/completion` | `403 {"status": "demo_only", "operation": …, "detail": …}` before authentication, for every caller including an administrator. Synthetic data enters only through the in-process provider. |

ADG performs no infrastructure mutation in any mode (ADR-0035): there is no endpoint that
changes AD or a file server to simulate. Every demo response carries `X-ADG-Runtime-Mode: demo`.

## 8. Startup validation

| Configuration | Result |
| --- | --- |
| `ADG_RUNTIME_MODE` unknown or empty | Refused. |
| demo + database not named for the demo (including the dev default `adg`) | Refused. |
| demo + `ADG_COLLECTOR_API_KEYS` set | Refused — a collector credential on a demo can only be a production one in the wrong place. |
| demo + an enabled non-`log` alert sink | Refused at startup, and on every later policy read (the file is re-read). |
| demo + `ADG_REMEDIATION_EXECUTION_MODE` other than `disabled` | Refused — a demo holds no executor of any kind. |
| demo + `ADG_DEMO_DATASET` unregistered | Refused. |
| demo API + dataset generator absent | Refused at lifespan, naming the dependency. |
| demo API + database unmarked, marked for production, or holding another dataset | Refused at lifespan. |
| production + `ADG_DEMO_DATASET` set | Refused — a contradiction about what the deployment is. |
| production | Every existing rule, unchanged. Nothing demo-specific is required or read. |

A demo needs **no** production credential: no OIDC tenant, collector key, signing key or
Windows account. (It may use OIDC; it does not have to.)

## 9. Health and status

`GET /runtime` (public, like `/auth/config`): `runtime_mode`, `data_source`
(`collectors`/`synthetic`), `synthetic`, and on a demo the dataset's public name card and a
one-sentence notice. `GET /health/ready` and `GET /version` gain `runtime_mode` (and readiness
`data_source`). None of them discloses a connection string, database name, host or credential;
a test asserts it.

`GET /api/v1/demo` (any role; demo only) lists the dataset, which generations are applied,
the next one, and the mutation policy. `POST /api/v1/demo/scans` (`collectors:ingest`, as a
real run requires) applies the next generation; `409` when none remains. The routes do not
exist in production.

## 10. The UI

The shell reads `/runtime` on every page, before sign-in. In demo mode the header carries a
small labelled pill — **DEMO ENVIRONMENT · Synthetic enterprise data · <company>** — that
expands to the notice ("This environment uses deterministic synthetic data for <company>. No
production Active Directory or file servers are connected."), a line saying the application
itself is real, and the dataset's domain, snapshot and seed; the footer adds that the
organization is fictional. Nothing is shown in production. It offers no control.

## 11. Background and scheduled work

| Job | Demo behavior |
| --- | --- |
| Windows collectors (`collector/`, scheduled tasks) | Not part of a demo deployment. If one were pointed at a demo it would be refused (class C) and could not authenticate (no collector keys). |
| `python -m app.operations evaluate-risks` | **Runs** against the demo database; the reset calls it. |
| `python -m app.operations drain-alerts` | **Runs**; deliveries go to log sinks only. |
| Inline post-run evaluation (`ADG_ALERTS_ON_RUN_COMPLETION`) | **Runs** for synthetic runs exactly as for collector runs (off by default). |
| History retention pruning | Not scheduled by the application in any mode; a demo reset supersedes it. |
| Synthetic scans | `python -m app.demo_mode scan` / `POST /api/v1/demo/scans` — the dataset's own generations through the synthetic provider. No demo job can reach a collector. |

## 12. Public deployment model (not performed)

```text
Browser → ADG web (Next.js) → ADG API (ADG_RUNTIME_MODE=demo) → PostgreSQL (adg_demo, marked)
                                         ▲
                     python -m app.demo_mode reset  (one-shot, at deploy and on demand)
```

Linux containers only: no Windows, PowerShell, AD DS, LDAP, SMB, NTFS enumeration, domain
join or collector. Before exposing one publicly, decide the visitor sign-in posture:
development authentication is refused with `ADG_ENVIRONMENT=production`, so a public demo
needs either OIDC or a deliberate read-only visitor mode, which this work does not add.
Deployment itself (e.g. Cloudflare) is out of scope here.
