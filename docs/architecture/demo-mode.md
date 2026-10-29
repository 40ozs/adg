# Demo Mode

**Status:** implemented · **Code:** `backend/app/runtime_mode.py`, `backend/app/demo_mode/`,
`backend/app/demo/genesee_press/`, `backend/app/api/runtime.py`, `backend/app/api/demo.py`,
`frontend/components/Banners.tsx`, `frontend/components/DemoGuide.tsx` · **Decision:**
[ADR-0039](../decisions/0039-demo-is-a-deployment-boundary.md) · **Dataset:**
[Genesee Press](../demo/genesee-press.md) · **Evidence:** [Demo Mode](../handoffs/demo-mode-01.md),
[Null Meridian, the dataset's former name](../handoffs/adg-demo-null-meridian-01.md),
[SYNTH-ADG-01](../demo/reports/SYNTH-ADG-01-report.md),
[integration](../handoffs/adg-demo-integration-01.md)

Demo Mode is the real ADG application — the same API, ingestion, history, current-state
derivation, access engine, risk rules, search, simulation, governance and UI — running on a
**synthetic estate** instead of one reported by Windows collectors. One codebase, one image,
one schema. A deployment is either `production` or `demo`, and which one it is is decided by
its configuration, never by anybody using it.

Two things, and only two, make up a demo:

```text
Demo Mode     = the runtime, safety, persistence and operation boundary  (app.demo_mode)
Genesee Press = the deterministic synthetic enterprise dataset it runs on (app.demo.genesee_press)
```

Genesee Press generates data and nothing else. Every reset, guard, marker, scan and status
command belongs to Demo Mode, which loads the dataset through one contract (§3).

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
surrounding whitespace are forgiven (`Demo` is unambiguous); nothing else is. In particular
`standard` — the name an earlier Genesee Press branch used for production — is refused like
any other unrecognized value rather than kept as a synonym.

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
`app.demo.seed.seed_transcripts`, an in-process ASGI poster bound to it
(`InProcessPoster`: the request goes straight to the application object, through every
middleware, and addresses only the reserved host `demo-synthetic-provider.invalid`). Two
dependency overrides exist **only on that in-process application object**, which never
listens on a socket: the ingestion principal resolves to the collector
`demo-synthetic-provider` — no credential is configured, minted or stored — and the demo
policy guard is lifted. The deployment's served application has neither, so its ingestion
routes stay closed. The provider refuses to run unless the settings are demo.

The poster is a few dozen lines rather than `httpx` on purpose: `httpx` is a development
dependency the API image does not install, and the application imports this module, so an
`httpx` import here would stop the API — production included — from starting. A test imports
the application with `httpx` blocked.

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
| `generations() -> Sequence[ScanGeneration]` | The dataset's scan history, oldest first. Each is a tuple of `app.demo.transcripts.DemoTranscript` — collector-protocol v1 runs — plus an optional `evaluated_at`, the instant the risk pass after that scan is dated (otherwise the wall clock). **Run ids must be deterministic** (uuid5): applied-ness is "every run id is in `scan_runs`". |
| `baseline_generations` *(optional, default 1)* | How many leading generations make up the canonical state the reset installs. Later generations are applied one at a time by *Run synthetic scan*. |
| `probes() -> Sequence[CanonicalProbe]` | Objects the dataset promises exist in current state once its baseline is applied, named by its own canonical ids: `principal{sid}`, `membership{group_sid,member_sid}`, `share{server,share}`, `share_ace{server,share,trustee_sid}`, `ntfs_resource{path}`, `ntfs_ace{path,trustee_sid}`. The reset fails if any is missing. |
| `risk_configuration()` *(optional)* | A `RiskConfiguration` declaring the estate's sensitive resources and any operator tuning. Used when `ADG_RISK_CONFIGURATION_PATH` is empty; without it the shipped defaults apply. Production never reads it, and global `config.py` names no dataset's folders. |
| `scenarios()` *(optional)* | `DemoScenario` walkthroughs — key, title, summary, the folder, and the people and groups by SID. Served on `GET /api/v1/demo`; the UI links each into the real pages. Display-only. |

Registered datasets (`DATASETS`), each a lazy `module:callable` loader:

| Key | Loader | Status |
| --- | --- | --- |
| `genesee-press-v1` | `app.demo.genesee_press:synthetic_dataset` | **Canonical and the default.** The Genesee Press estate ([docs](../demo/genesee-press.md)). |
| `adg-reference-estate` | `app.demo_mode.reference:synthetic_dataset` | Selectable explicitly, never a fallback. A thin adapter over the small estate in `app.demo` (`corp.example.com`); its transcripts are asserted byte-identical to `app.demo`'s own. Fast enough for the Demo Mode database suite. |

`ADG_DEMO_DATASET` selects one; empty means `genesee-press-v1`. One name throughout: the
registry key, the dataset's metadata key, its seed and the `dataset_key`/`dataset_seed` on
the deployment marker all read `genesee-press-v1`. A selected dataset that cannot load — its
module absent or failing to build — is a refusal at startup and at reset; nothing substitutes
another estate, and its risk configuration is never silently replaced by the defaults.

### The Genesee Press adapter

`app.demo.genesee_press.dataset.GeneseePressDataset` maps the estate onto the contract
and constructs nothing (a test checks it calls no estate constructor):

| Member | Source |
| --- | --- |
| `metadata` | `canon` (company, `corp.geneseepress.example`, snapshot 2026-10-01, dataset key, founded) and display facts read from `manifest.build_manifest()` — the document the committed `docs/demo/genesee-press.manifest.json` is checked against. No count is hard-coded. |
| `generations()` | `generations.build_generations()`: `g1` 2026-09-03, `g2` 09-17, `g3` 10-01; 100 runs each; each risk pass dated 06:00 UTC on its scan day (`generations.RISK_PASS_AT`). |
| `baseline_generations` | 3 — the canon is what three scans had collected by the snapshot. |
| `risk_configuration()` | `risk.DEMO_RISK_CONFIGURATION`: eleven sensitive folders; `redundant_access_paths.minimum_paths` 2 → 4. |
| `scenarios()` | `scenarios.golden_scenarios()` — A to H. |
| `probes()` | 23 objects scenarios A–H are about (e.g. the Design root's `sec-share-design-write` entry, every link of G's six-group chain, `Proj-Zephyr-RW` on Zephyr) plus the two history promises: `Wave-3-Staging` survives the partial g3 scan of `GP-MIG-FS01`, and `GP-FS-03`'s folders survive its failed g3 scan. |

`tests/demo_mode/test_datasets.py::TestGeneseePressCanon` and `TestTheGeneseePressAdapter`
hold the adapter to the canon (transcripts byte-identical to the generator's, facts equal to
the committed manifest, every probe present in the generated estate).

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

`deployment_marker` is the **only** authority. (An earlier Genesee Press branch marked its
database with `COMMENT ON DATABASE … 'adg-demo:genesee-press-v1'` and accepted any database
whose name merely *contained* `demo`; both were removed at integration rather than kept as a
second, competing authority. A populated database carrying that comment but no marker row is
refused — a test proves it.)

## 5. Initialize and reset — one operator workflow

```bash
export ADG_RUNTIME_MODE=demo                       # ADG_DEMO_DATASET unset: Genesee Press
export ADG_DATABASE_URL=postgresql+psycopg://adg:<pw>@localhost:5432/adg_demo
cd backend
python -m app.demo_mode reset    # first run initializes; every run restores the baseline
python -m app.demo_mode verify   # probes + fingerprint against the database as it is
python -m app                    # serve
```

This is the only reset there is. (`python -m app.demo.genesee_press` keeps three pure
developer utilities — `manifest`, `transcripts`, `stats` — and has no database command.)

`reset` performs, in order: refuse unless demo → load the dataset (refuse if unavailable,
before touching the database) → `alembic upgrade heads` → identify the database (§4) → lock
the marker and truncate every ADG table except it → ingest the baseline **one generation at a
time** through the synthetic provider (Genesee Press: g1, g2, g3 — 300 runs, 41,756
observations) → `app.operations.commands.evaluate_risks`, the production operator command,
under the dataset's risk configuration and dated by the last applied generation → validate:
every run recorded with the status its transcript claimed, every probe present in current
state → print a **canonical fingerprint** with one digest per source. `--report PATH` also
writes the JSON report.

`reset --generations N` stops after the first `N` generations: the dataset's own history,
rewound, so `scan` can replay the rest (`reset --generations 1`, then `scan`, `scan`). The
probes describe the baseline, so they are checked once it is complete; a test proves that
scanning forward reaches the same current state, history and run digests as a full reset.

The fingerprint is SHA-256 over per-source digests of current state (all seven `current_*`
views), `object_versions`, `scan_runs`, `scan_run_scopes`, `scan_run_errors` and
`risk_findings`, excluding only values about the process rather than the dataset: row
bookkeeping timestamps, risk-evaluation bookkeeping and serials, and database serials. Every
identifier, relationship, hash and observed instant the dataset supplied is in it. The
deployment marker is outside it. Two resets of the same dataset produce the same fingerprint;
reset timing and both fingerprints are recorded in the integration handoff.

Other commands: `status` (dataset, marker, generations, baseline, fingerprint — changes
nothing), `scan` (next generation, then the risk pass), `verify` (baseline applied, probes,
counts, fingerprint). All refuse unless demo. `--json` prints machine-readable reports.

With Docker: `docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build`.
The override gives PostgreSQL its own volume and an `adg_demo` database, runs a one-shot
`demo-reset` service (Genesee Press by default; a few minutes), and starts the API only after
it succeeds.

## 6. Temporal semantics are untouched

Demo data enters through ingestion, so it gets ADG's real observation model: append-only
observations, `object_versions`, current state as "no open tombstone", tombstones only from a
successful authoritative in-scope reconciliation, partial and failed runs closing nothing.
Genesee Press's three generations walk through every rule: g1's NTFS scan of `GP-SEC-FS01`
**fails** (its shares are known, its folders are not); g2 tombstones accounts crossing the
180-day deletion line, the deleted `Project-Vega` group with its edges and a deleted folder,
all through reconciled complete scans; g3's scan of `GP-FS-03` **fails** and that of
`GP-MIG-FS01` is **partial**, so every folder they did not report stays current — including
`Wave-3-Staging`, really deleted on 2026-09-25 — until a complete scan says otherwise.
`tests/db/test_genesee_press_demo.py::TestCurrentStateSemantics` asserts each, and that
current state is exactly what the scans justify. The reference estate carries its own partial
(FS02) and failed (FS03) runs, asserted in `tests/demo_mode/test_demo_database.py`. Demo Mode
applies the dataset's history and does not author a second one.

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

`GET /api/v1/demo` (any role; demo only) lists the dataset, which generations are applied and
which form the baseline, the next one, the dataset's scenarios, and the mutation policy.
`POST /api/v1/demo/scans` (`collectors:ingest`, as a real run requires) applies the next
generation and the risk pass after it; `409` when none remains. The routes do not exist in
production, so they are absent from `docs/contracts/v1/openapi.json` and published instead in
`docs/contracts/v1/openapi.demo.json`, which the frontend's demo guide is checked against.

## 10. The UI

The shell reads `/runtime` on every page, before sign-in. In demo mode the header carries a
small labelled pill — **DEMO ENVIRONMENT · Synthetic enterprise data · <company>** — that
expands to the notice ("This environment uses deterministic synthetic data for <company>. No
production Active Directory or file servers are connected."), a line saying the application
itself is real, and the dataset's domain, snapshot and seed; the footer adds that the
organization is fictional. Nothing is shown in production. It offers no control.

The panel links to **`/demo`, the demo guide**: the dataset's scenarios (Genesee Press's A–H),
each linking into the real application — the folder's directory page, each person's and
group's principal page, and the access explanation of each person against the folder — plus
the scan history and what the demo lets a visitor change. It renders `GET /api/v1/demo` and
nothing else; there is no second, UI-only copy of a scenario. A production deployment's
`/demo` says it is not a demo and asks its API nothing.

## 11. Background and scheduled work

| Job | Demo behavior |
| --- | --- |
| Windows collectors (`collector/`, scheduled tasks) | Not part of a demo deployment. If one were pointed at a demo it would be refused (class C) and could not authenticate (no collector keys). |
| `python -m app.operations evaluate-risks` | **Runs** against the demo database; the reset calls it. |
| `python -m app.operations drain-alerts` | **Runs**; deliveries go to log sinks only. |
| Inline post-run evaluation (`ADG_ALERTS_ON_RUN_COMPLETION`) | **Runs** for synthetic runs exactly as for collector runs (off by default). |
| History retention pruning | Not scheduled by the application in any mode; a demo reset supersedes it. |
| Synthetic scans | `python -m app.demo_mode scan` / `POST /api/v1/demo/scans` — the dataset's next generation through the synthetic provider, then the production risk pass dated as the dataset dates it. No demo job can reach a collector. |

## 12. Public deployment model (not performed)

```text
Browser → ADG web (Next.js) → ADG API (ADG_RUNTIME_MODE=demo) → PostgreSQL (adg_demo, marked)
                                         ▲
                     python -m app.demo_mode reset  (one-shot, at deploy and on demand)
```

Linux containers only: no Windows, PowerShell, AD DS, LDAP, SMB, NTFS enumeration, domain
join or collector. Before exposing one publicly, decide the visitor sign-in posture:
development authentication is refused with `ADG_ENVIRONMENT=production`, so a public demo
needs either OIDC or development sign-in restricted, through `ADG_DEV_AUTH_USERS`, to accounts
a stranger may hold. Deployment itself is out of scope here; the free public demo staging
deployment, and the account policy it uses, is
[`docs/deployment/cloudflare-demo.md`](../deployment/cloudflare-demo.md).
