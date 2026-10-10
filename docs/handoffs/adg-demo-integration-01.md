# Handoff — ADG-DEMO-INTEGRATION-01 (Demo Mode × the Null Meridian estate)

**Date:** 2026-10-10
**Integration branch:** `claude/adg-demo-null-meridian-integration-5biwm0` — the branch this
session was assigned; it plays the role the brief calls `integration/ADG-DEMO-NULL-MERIDIAN`.
**Starting SHA:** `main` @ `cc13ad27e11e538c849d4e0a53f2358e36454392`
**Sources consumed (unchanged):**
Demo Mode `claude/vibrant-gates-rhovf1` @ `28e574320a8ef1e2c657df66d2f4570392f36f95`;
Null Meridian `claude/inspiring-volta-6uzdsy` @ `4512524b71f453d15a1f40be48578c1509bf6828`
(two commits: the estate, and `bc82bb3` the `RiskFindingRepository.by_keys` chunking fix).
**Ending SHA:** the commit carrying this file (`git log -1 -- docs/handoffs/adg-demo-integration-01.md`).
**Previous handoffs:** [demo-mode-01.md](demo-mode-01.md) ·
[adg-demo-null-meridian-01.md](adg-demo-null-meridian-01.md)
**Design:** [docs/architecture/demo-mode.md](../architecture/demo-mode.md) ·
[ADR-0039](../decisions/0039-demo-is-a-deployment-boundary.md) (amended) ·
[docs/demo/null-meridian.md](../demo/null-meridian.md)
**Collector contract:** unchanged. **`docs/contracts/v1/openapi.json`:** unchanged by the
integration (Demo Mode's additive `/runtime` and `runtime_mode` fields as merged).
**New:** `docs/contracts/v1/openapi.demo.json` — the demo-only routes. **Migration:**
`0017_deployment_marker` (Demo Mode's), nothing new.

---

## Verdict

⟪TBD⟫

```text
Demo Mode     = the runtime, safety, persistence and operation boundary  (app.demo_mode)
Null Meridian = the deterministic synthetic enterprise dataset           (app.demo.null_meridian)
```

---

## 1. Source state

| Ref | Expected | Resolved | |
| --- | --- | --- | --- |
| `origin/main` | `cc13ad2` | `cc13ad27e11e538c849d4e0a53f2358e36454392` | ✓ |
| `origin/claude/vibrant-gates-rhovf1` | `28e5743` | `28e574320a8ef1e2c657df66d2f4570392f36f95` | ✓ |
| `origin/claude/inspiring-volta-6uzdsy` | `4512524` | `4512524b71f453d15a1f40be48578c1509bf6828` | ✓ |

Both sources branch from `cc13ad2`. Neither source branch was rebased, amended, pushed to or
otherwise modified; `git rev-parse` of both remote refs at the end of the work returns the SHAs
above.

## 2. Merge sequence and conflicts

```text
main @ cc13ad2 ─┬─ merge Demo Mode @ 28e5743        (22d4c9c, no conflicts)
                ├─ merge Null Meridian @ 4512524    (a2a6d00, 2 textual conflicts)
                ├─ reconcile into one architecture  (a9fe81b)
                ├─ document one architecture        (7fa7680)
                └─ evidence + this handoff          (the commit carrying this file)
```

| File | Conflict | Resolution |
| --- | --- | --- |
| `backend/app/config.py` | *textual*: `runtime_mode` field (`RuntimeMode` enum vs `Literal["standard","demo"]`), `make_url` import, `risk_configuration` demo branch, `is_demo`/`resolved_demo_dataset` vs `database_name`. *Semantic, auto-merged*: NM's `_confine_demo_mode` validator, `RuntimeMode` literal and `DEMO_DATABASE_MARKER` landed beside Demo Mode's. | Demo Mode's enum, validator and dataset-contract risk configuration. NM's literal, constant, `database_name` and validator removed: every check it made is made, at least as strictly, by Demo Mode's validator — except `ADG_ENVIRONMENT=production`, deliberately not adopted (§3.4). |
| `backend/app/main.py` | *textual*: startup-log `runtime_mode` and header constant. *Semantic*: both set `X-ADG-Runtime-Mode` (NM in the request-id middleware, Demo Mode in its own). | One header implementation (Demo Mode's middleware, which also sets `X-ADG-Demo-Notice`); NM's CORS exposure kept and extended to both headers. |
| reset / CLI | `python -m app.demo_mode reset` vs `python -m app.demo.null_meridian reset` | One reset: Demo Mode's (§3.3). |
| DB safety | `deployment_marker` table vs `COMMENT ON DATABASE` | `deployment_marker` only (§3.5). |
| `.env.example` | both added a runtime section; NM's set `ADG_RUNTIME_MODE=standard` | Demo Mode's section, extended; `standard` removed (it is now a startup error). |
| `docker-compose.yml` | NM defaulted `ADG_RUNTIME_MODE` to `standard` | `production` — `standard` would refuse to start. |
| `docker-compose.demo.yml` | defaulted to `adg-reference-estate` pending NM | `null-meridian-v1`; remediation execution pinned `disabled`. |
| `README.md`, docs | two descriptions of Demo Mode | one (§3.12). |
| NM tests | asserted `standard`, the database comment, `reset.py` | ported to the canonical contract (§6). |

## 3. Architecture decisions

### 3.1 Runtime mode

`ADG_RUNTIME_MODE` is `app.runtime_mode.RuntimeMode` (`production` | `demo`), parsed by
`parse_runtime_mode`: unset → production; `production`/`demo` (case and surrounding space
forgiven) → that mode; **anything else, including empty and `standard`, is a startup error**.
`standard` was not kept as a synonym: nothing outside the NM branch used it, and the base
compose file — the one place it was a default — now says `production`. No route, header,
cookie or UI control changes the mode; `/runtime` is `GET`-only; a test checks that no request
parameter or request-body schema in the published API can name it.

### 3.2 The SyntheticDataset adapter

`app.demo.null_meridian.synthetic_dataset()` → `NullMeridianDataset` (`dataset.py`), loaded
through the registry entry `null-meridian-v1 → app.demo.null_meridian:synthetic_dataset`.
It constructs nothing; a test checks it calls no estate constructor.

| Contract member | Source in the NM package |
| --- | --- |
| `metadata` | `canon` (Null Meridian, `nullmeridian.info`, snapshot 2026-10-01, seed `null-meridian-v1`, founded 2016-10-01); display facts read from `manifest.build_manifest()` — asserted equal to the committed manifest |
| `generations()` | `generations.build_generations()` — g1/g2/g3, 100 runs each, transcripts byte-identical (asserted) |
| `ScanGeneration.evaluated_at` (new, optional) | `Generation.evaluated_at` = scan day 06:00 UTC (`RISK_PASS_AT`, moved from NM's `reset.EVALUATED_AT`) |
| `baseline_generations` (new, optional) | 3 |
| `risk_configuration()` | `risk.DEMO_RISK_CONFIGURATION` |
| `scenarios()` (new, optional) | `scenarios.golden_scenarios()` — A–H |
| `probes()` | 23 canonical objects: scenarios A–H (e.g. G's chain link by link, H's `Proj-Zephyr-RW` entry) and the two history promises (`Wave-3-Staging` survives the partial g3 scan; `NM-FS-03`'s folders survive its failed g3 scan) |

### 3.3 One reset, one CLI

`python -m app.demo_mode reset | status | scan | verify` is the operator interface;
`scripts/demo-reset.ps1` and the compose `demo-reset` service call it. NM's
`app/demo/null_meridian/reset.py` (its own refusals, marker, admin-token seeding client,
wipe and fingerprint) is **deleted**; `python -m app.demo.null_meridian` keeps three pure
developer utilities (`manifest`, `transcripts`, `stats`) that read no configuration and open
no database (asserted).

The reset installs the dataset's baseline **one generation at a time through the same
`apply_generation` the scan uses**, then runs `evaluate_risks` under the dataset's
configuration, dated by the last applied generation. `reset --generations N` stops early — an
earlier point of the dataset's own history — and `scan` replays the rest; a DB test proves
reset(g1) → scan(g2, via the API) → scan(g3, via the lifecycle) reaches current-state, history
and run digests identical to a full reset. `scan` now runs the risk pass after the generation
it applies (it previously ran none, so the risk view lagged a synthetic scan). `verify` moved
into the lifecycle and requires the baseline before checking probes.

### 3.4 Startup refusals

Demo Mode's validator is the one: database not named with `demo` as a word, collector keys,
any remediation executor, enabled non-`log` alert sinks (also on every policy read),
unregistered dataset; production refuses `ADG_DEMO_DATASET`. NM's refusal of
`ADG_ENVIRONMENT=production` in demo mode was **not adopted**: `ADG_ENVIRONMENT` is how strict a
deployment is, not what it is connected to, and refusing it would force a public demo onto
development sign-in. The reset's destructive boundary (mode, name, marker) is unaffected. NM's
blanket refusal of any `ADG_ALERT_POLICY_PATH` is replaced by Demo Mode's per-sink rule, which
is as safe and permits a log-only policy file.

### 3.5 Database marker and name guard

`deployment_marker` (migration 0017) is the only authority: written only when the reset
initializes a database holding no row in any ADG table, locked and re-checked in the
transaction that wipes, required by the API lifespan. NM's `COMMENT ON DATABASE` marker is
gone, not retained as informational metadata — a second marker would invite somebody to trust
it. A DB test proves a populated database carrying NM's old comment but no marker row is
refused and left untouched. The name rule is Demo Mode's (`demo` as a whole word), stricter
than NM's substring check; `adgdemo` is refused (tested).

### 3.6 Canonical dataset identity

`null-meridian-v1` everywhere: registry key, `metadata.key`, `metadata.seed`, and the
marker's `dataset_key` and `dataset_seed`. No alias. It is the default of `ADG_DEMO_DATASET`.
`adg-reference-estate` remains selectable explicitly (it keeps the Demo Mode DB suite fast) and
is never a fallback: an unavailable dataset is a refusal at startup and at reset, and the
dataset risk configuration no longer falls back to defaults when its dataset cannot load.

### 3.7 In-process ingestion (a defect fixed)

Demo Mode's synthetic provider imported `httpx` (`ASGITransport`, `AsyncClient`). `httpx` is a
`[dev]` dependency; the API image installs the backend without it; and `app.main` imports the
provider through the demo router. **On the Demo Mode branch, `import app.main` fails without
`httpx`** — reproduced in a clean venv with the image's dependency set
(`ModuleNotFoundError: No module named 'httpx'`), so its API image could not start in either
mode. The integration keeps Demo Mode's dependency-override provider (no credential minted)
and replaces the transport with NM's dependency-free in-process ASGI poster (`InProcessPoster`,
reserved host `demo-synthetic-provider.invalid`). Proven: the integrated backend imports and
resets in a venv without `httpx` (§5), and a hermetic test imports the application with
`httpx` blocked.

### 3.8 Risk configuration and the repository fix

The demo's configuration comes only through `dataset.risk_configuration()`; `app/config.py`
no longer imports NM. Demo tuning preserved: `redundant_access_paths.minimum_paths` 4 (shipped
default 2, asserted unchanged for production), eleven sensitive folders.
`RiskFindingRepository.by_keys` chunking (5,000 keys per statement) and its regression test
(`TestLookupByKeyAtScale`, 70,000+ keys) are preserved unchanged in behavior; the only edit
moves `KEY_CHUNK` below `MAX_MEMBERSHIP_DEPTH`'s docstring, which the original insertion had
detached. `AlertRepository.by_keys` has the same unchunked shape and is left as a follow-up
(bounded by alerts raised per evaluation).

### 3.9 Fingerprint

One implementation: Demo Mode's dataset-agnostic `canonical_fingerprint`, now combining
per-source digests (NM's diagnostic `table_digests` idea) — SHA-256 over digests of the seven
`current_*` views, `object_versions`, `scan_runs`, `scan_run_scopes`, `scan_run_errors` and
`risk_findings`, excluding row bookkeeping, risk-evaluation bookkeeping and serials.
`deployment_marker` is outside it. NM's ten-table fingerprint was removed from the product;
run unchanged (from `4512524`) against the integrated database it still yields NM's recorded
`db736602…85c28a0` (§5).

### 3.10 API, contracts and UI

`GET /runtime`, `/version` and `/health/ready` as Demo Mode merged them (mode, data source,
dataset name card; no connection detail — asserted). `GET /api/v1/demo` gains
`baseline_generations`, `generations[].in_baseline` and `scenarios`; `POST /api/v1/demo/scans`
reports its risk pass. The demo routes are absent from `openapi.json` by design and are now
published in `openapi.demo.json` (generated and snapshot-tested like the main document); the
frontend's types are checked against it. The UI keeps Demo Mode's indicator (header pill,
expandable explanation, footer line) — NM had only the response header — and adds the
**demo guide** (`/demo`): scenarios A–H from the API, each linked to the real directory,
principal and access-explanation pages, the scan history, and the mutation policy. No
scenario is a UI fixture.

### 3.11 Compose

`docker-compose.demo.yml` layered over the base: own volume `adg_demo_pgdata` (the base
`adg_pgdata` does not appear in the rendered configuration), database `adg_demo`,
`ADG_RUNTIME_MODE=demo`, `ADG_DEMO_DATASET=null-meridian-v1`,
`ADG_REMEDIATION_EXECUTION_MODE=disabled`, empty collector keys, alert-policy and lab-fixture
paths; one-shot `demo-reset` (`python -m app.demo_mode reset`) before the API. Validated with
`docker compose config` (base and layered); not built or run — no Docker daemon here.

### 3.12 Documentation

One architecture in `docs/architecture/demo-mode.md`; ADR-0039 amended with an *Integration*
section listing what was kept and removed; `docs/demo/null-meridian.md` §1 and §9 rewritten
for the canonical reset; README and `.env.example` reconciled; the two source handoffs carry a
pointer here and are otherwise left as the record of their branches. The committed manifest
and canon JSON are unchanged (asserted byte-for-byte by the NM canon tests).

## 4. Null Meridian canon (preserved)

From the committed manifest — not restated in code — and the integrated database after reset:

| | Manifest | Integrated DB (current state) |
| --- | ---: | ---: |
| Active employees (enabled users) | 1,800 | 1,800 |
| Former employees | 640 | — |
| Departments | 14 | — |
| Groups | 205 | — |
| Servers | 33 | 33 |
| Shares (+ admin) | 91 + 33 | 124 |
| Share ACEs | 606 | 606 |
| Folders | 824 | 826 (824 + 2 kept by the partial g3 scan) |
| NTFS entries / direct-user / group | 4,111 / 136 / 518 | 4,117 current (incl. the 2 kept folders') |
| Scans / observations | 300 / 41,756 | 300 runs (297 succeeded, 1 partial, 2 failed) / 41,756 applied |
| Object versions / open tombstones | — | 14,316 / 70 |
| Risk findings matched / open | — | 9,666 / 9,386 (complete, no ceiling) |

Golden scenarios A–H: generator-side in `tests/demo/test_null_meridian_estate.py`
(`TestGoldenScenarios`), and through the real API in `tests/db/test_null_meridian_demo.py`
(A, B, C, D, E, F, G, H incl. the simulation) — all pass.

## 5. Determinism

⟪TBD⟫

## 6. Tests

⟪TBD⟫

## 7. Safety gate

⟪TBD⟫

## 8. Browser validation

⟪TBD⟫

## 9. Known limitations

⟪TBD⟫

## 10. Main merge recommendation

⟪TBD⟫

## 11. Files changed (relative to `main`)

⟪TBD⟫
