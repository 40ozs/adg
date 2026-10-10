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

**READY_FOR_ADG_DEMO_MAIN_MERGE_WITH_LIMITATIONS.**

The completed Null Meridian estate now runs through the completed Demo Mode architecture as
its default and canonical dataset: one runtime contract (`production|demo`), one dataset
contract, one reset/status/scan/verify CLI, one database authority (`deployment_marker`), one
fingerprint. Both feature sets are preserved — Demo Mode's boundary, guards, mutation policy,
runtime API and indicator; Null Meridian's canon, three generations, scenarios A–H, risk
tuning and the `by_keys` fix — and the integrated reset reproduces Null Meridian's own
recorded fingerprint (`db736602…85c28a0`) byte for byte. Production isolation is intact and
tested. Every integration-caused gate passes; every remaining failure is identical on `main`.

The limitations (§9) are not integration regressions: CI is red on `main` and here for the same
pre-existing reasons (three mypy errors; SQLAlchemy 2.1 `DISTINCT ON` deprecation — 80 DB tests,
identical ids, zero failures under 2.0.54); the Docker stack was validated but not run (no
daemon); scenario H's simulation answer is honestly partial at the product's bounds; and a
public deployment still needs a sign-in decision.

The integration also fixed two defects found on the way: Demo Mode's API could not import
without the dev-only `httpx` (so its image could not start), and `--json` CLI output was
interleaved with log lines.

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
into the lifecycle and requires the baseline before checking probes. `--report PATH` (from
NM's reset) writes the JSON report. Standard output now carries only the report: the
in-process ingestion builds the application, which installs its log handler on stdout, so
`--json` output was interleaved with log lines and did not parse (found by this integration's
evidence run; the code path is unchanged from Demo Mode's CLI); the CLI now runs each command
with stdout redirected to stderr and prints the report afterwards (tested).

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

Three resets of the integrated application, each `python -m app.demo_mode reset` (default
dataset, Null Meridian), run in a venv holding **only the API image's dependencies** (no
`[dev]` extras, so no `httpx`) — the way the compose `demo-reset` service runs it:

| | Reset 1 | Reset 2 | Reset 3 |
| --- | --- | --- | --- |
| Database | new, empty, unmarked `adg_demo` | dropped and recreated: new, empty, unmarked | the reset-2 database, already marked (`--no-migrate`) |
| Initialized (marker written) | yes | yes | no (marker kept) |
| Runs (succeeded / partial / failed) | 300 (297 / 1 / 2) | 300 (297 / 1 / 2) | 300 (297 / 1 / 2) |
| Observations applied | 41,756 | 41,756 | 41,756 |
| Probes | 23 / 23 | 23 / 23 | 23 / 23 |
| Risk pass (dated 2026-10-01T06:00Z) | 9,666 matched, 9,386 open, complete | same | same |
| **Canonical fingerprint** | `228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0` | **identical** | **identical** |
| Per-source digests | 11 | all 11 equal to reset 1 | — |
| NM-scope fingerprint (4512524 algorithm, run unchanged) | `db736602850a4a82936f861b68466e7c54b61a55192b04f6d062cbe8585c28a0` | **identical** | — |
| Duration (whole / ingestion through the API) | 139.8 s / 93.4 s | 137.4 s / 90.4 s | 132.3 s |

Reports: [`docs/demo/evidence/reset-1.json`](../demo/evidence/reset-1.json),
[`reset-2.json`](../demo/evidence/reset-2.json) (written by `reset --report`). After reset 3,
`verify` → ok, 23 probes, same fingerprint; `status` → `baseline_applied: true`, marker
`demo` / `null-meridian-v1` / `null-meridian-v1`; `scan` → refused, every generation applied.

Counts after every reset (identical across all three): current principals
2,139, membership edges 6,224, servers
33, shares 124, share ACEs 606,
NTFS resources 826, NTFS ACEs 4,117; object
versions 14,316, scan runs 300, open tombstones
70.

| Source | Digest (reset 1) | Reset 2 |
| --- | --- | --- |
| `current:membership_edge` | `10aa3f796aeff011…` | = |
| `current:ntfs_ace` | `f0d1ebb2ed751782…` | = |
| `current:ntfs_resource` | `1785f3fe5969b267…` | = |
| `current:principal` | `b580544a3d76fae0…` | = |
| `current:server` | `5b969787e29308fb…` | = |
| `current:smb_ace` | `4ea8a55e8049808e…` | = |
| `current:smb_share` | `c52bf0b41c4e7576…` | = |
| `object_versions` | `7ba7b8697ae1e8e5…` | = |
| `risk_findings` | `1507c197517dcb9d…` | = |
| `scan_run_errors` | `7ed6c7ef69dedcc4…` | = |
| `scan_run_scopes` | `5c69258a3f03d105…` | = |
| `scan_runs` | `6b929acfc2f6070f…` | = |

**Fingerprint scope.** The canonical fingerprint is Demo Mode's (§3.9), so its value differs
from both source branches' recorded values by construction: Demo Mode's
`f3f70279…` was over the reference estate with a sequential hash, and NM's `db736602…` over ten
raw tables including risk timestamps. To show the dataset state did not change, the NM
branch's own `fingerprint()` — copied verbatim from `4512524` into a scratch script, not into
the product — was run against the integrated database after resets 1 and 2: it returns
`db736602850a4a82936f861b68466e7c54b61a55192b04f6d062cbe8585c28a0`, the value NM recorded, with
all ten of its table digests equal to NM's committed `reset-1.json`. `deployment_marker` is in
neither scope. The integration changed how the estate is loaded and guarded, not one row of
what is loaded — risk-finding timestamps included, because the dataset now carries NM's
evaluation instant through the contract.

The DB suites assert the same independently: `TestRepeatability` (a second full reset with
Python sockets refused — equal fingerprint, digests, counts) and
`TestTheHistoryReplaysThroughTheSyntheticScan` (reset to g1, scan g2 and g3 — every
non-risk digest equal to the full reset's; the same number of open findings).

## 6. Tests

Environment: Linux container, Python 3.13.16, PostgreSQL 16 (local), Node 22. Two Python
environments: packages as `pip install -e ".[dev]"` resolves them today (FastAPI 0.143,
**SQLAlchemy 2.1.4**, pytest 9.1, mypy 2.4, ruff 0.17 — what CI installs), and the same with
**SQLAlchemy 2.0.54** pinned in the venv only (no dependency file changed). Baselines were run
on an untouched worktree of `cc13ad2` with its own databases.

| Suite | `main` @ `cc13ad2` | Integrated |
| --- | --- | --- |
| Demo Mode focused — `tests/demo_mode` hermetic | — (source: 81 passed, 2 skipped) | **94 passed, 0 skipped** |
| Demo Mode focused — `tests/demo_mode/test_demo_database.py` (PostgreSQL) | — (source: 24 passed) | **27 passed** |
| Null Meridian focused — `tests/demo/test_null_meridian_{canon,estate}.py`, `test_demo_mode.py` | — | **111 passed** (34 + 32 + 45) |
| Null Meridian focused — `tests/db/test_null_meridian_demo.py` (PostgreSQL) | — (source: 21) | **34 passed** |
| `by_keys` regression — `tests/db/test_risk_findings.py::TestLookupByKeyAtScale` | — | **passed** (in 23/23 of the file) |
| Hermetic backend, SQLAlchemy 2.1.4 (`-m "not smoke"`) | 5,549 passed, 124 skipped | **5,762 passed, 124 skipped, 0 failed** |
| Hermetic backend, SQLAlchemy 2.0.54 | — | **5,761 passed, 124 skipped, 0 failed** (taken before the last hermetic test, the CLI `--json` test, was added) |
| DB backend, SQLAlchemy 2.1.4 (`-m smoke`) | 1,064 passed, **80 failed**, 1 xfailed | 1,126 passed, **80 failed**, 1 xfailed |
| — the 80 failures | | **identical test ids** (80/80; none only on either side); every one `sqlalchemy.exc.SADeprecationWarning: Passing expression to distinct to generate a DISTINCT ON clause is deprecated`, promoted to an error by `filterwarnings = error::DeprecationWarning:app.*` — on both sides |
| DB backend, SQLAlchemy 2.0.54 (`-m smoke`) | — (NM source: 1,165 passed) | **1,206 passed, 0 failed**, 1 xfailed |
| `ruff check .` / `ruff format --check .` | clean | clean / 395 files formatted |
| `mypy app tests`, SQLAlchemy 2.1.4 | 3 errors | the **same 3** (`app/runtime.py:49`, `tests/test_runtime.py:31` — Windows-only asyncio attributes; `app/changes/repository.py:398` — SQLAlchemy 2.1 typing) |
| `mypy app tests`, SQLAlchemy 2.0.54 | 2 errors | the **same 2** |
| Frontend `tsc --noEmit` / `eslint .` | clean | clean / clean |
| Frontend `vitest run` | — (Demo Mode source: 1,139) | **1,163 passed** (33 files; +9 demo guide, +1 indicator link, +14 demo contract) |
| Frontend `next build` | success | success (new route `/demo`) |
| `docker compose config -q` (base; base + demo) | OK | OK / OK |
| Browser (headless Chromium, §8) | — | **22/22 checks** |
| GitHub Actions CI on the pushed branch | run 35009148121: Backend **fails at Type check** (the 3 mypy errors), unit/DB steps skipped; Frontend, Collector (Pester), Compose pass | run 38091215282: **the same** — Backend fails at Type check with the same errors; Frontend, **Collector (Pester)**, Compose pass |

Notes. Both DB runs were taken at `a9fe81b`. The later code changes are
`ResetReport.canonical` (`7fa7680`, covered by `TestResettingToAPointInHistory`, re-run after
it: 8/8 scan and history tests passed) and the CLI's stdout routing (hermetic test; exercised
by the three evidence resets in §5); neither touches what a reset writes. The mypy finding the Demo Mode branch
introduced (two unused `type: ignore` comments in its tests — its handoff ran `mypy app`, not
`mypy app tests` as CI does) is fixed; integrated mypy equals `main`. No test was skipped,
weakened or deleted to pass: the two Demo Mode tests that skipped while Null Meridian was
absent now run (`TestNullMeridianCanon`), and the two refusal tests that would have skipped
once it was present (`test_an_absent_generator_is_refused_and_never_substituted`,
`test_an_unavailable_dataset_is_refused_before_the_database_is_touched`) now use a registered
dataset whose module is missing, so they never skip. Null Meridian's tests that asserted the
removed contract (`standard`, the database comment, `reset.py`, the `ADG_ENVIRONMENT`
refusal) were rewritten to assert the canonical one; the claims behind them — no runtime
switch, no network or directory client, one subprocess, an in-process seeding client — are
all still asserted, over both packages now.

## 7. Safety gate

| Demo Mode must not… | How it is excluded | Evidence (all passing) |
| --- | --- | --- |
| contact Active Directory / LDAP | The backend has no directory client (ADR-0004); neither demo package imports one | `tests/demo_mode/test_boundary.py::TestIsolation::test_a_demo_application_loads_no_infrastructure_client`; `tests/demo/test_demo_mode.py::TestNoProductionCodePath::test_no_demo_module_imports_a_network_or_directory_client` (every module of `app/demo_mode` and `app/demo/null_meridian`: no `ldap`, `ldap3`, `impacket`, `socket`, `httpx`, `requests`, `urllib.request`, `paramiko`) |
| contact SMB | No SMB client anywhere | the same two tests (`smbprotocol`, `smbclient`) |
| contact WinRM | No WinRM client | the same (`winrm`, `pypsrp`, `pywinrm`) |
| run PowerShell | No process except the migration | `test_a_demo_application_starts_no_process` (Popen recorded: none); `test_the_only_subprocess_is_demo_modes_migration` (one `subprocess.run`, `alembic upgrade heads`, in `lifecycle.py`); NM DB `TestRepeatability`: every subprocess of a full reset recorded — only `python -m alembic upgrade heads`, no `pwsh`/`powershell` |
| construct the production collector | Provider chosen once from the mode | `TestProviderSelection::test_demo_never_constructs_the_collector_source` |
| accept external collector ingestion | `demo_policy_guard` on every route; the web tier forwards no `POST` | `TestTheGuard` (anonymous, administrator, forged header → `403 demo_only`); both DB suites' `test_external_ingestion_is_refused_and_writes_nothing` (Null Meridian included); browser: web proxy `405`, API `403 demo_only` |
| load collector credentials | Startup refusal | `test_a_collector_credential_is_refused`; `tests/demo/test_demo_mode.py` refusal matrix; compose passes `ADG_COLLECTOR_API_KEYS: ""` |
| initialize a remediation executor | `remediation_execution_mode` must be `disabled` (startup); the factory then returns only `DisabledRemediator` | `test_a_remediation_executor_is_refused`; `test_a_demo_holds_only_the_refusing_remediator`; compose pins `disabled`; runbook exports served inert (`TestTheRunbookIsInert`) |
| connect to a production database | Name rule + `deployment_marker`, re-checked under lock; API lifespan refuses | `TestDemoRefusals`/`TestRefusals` (Demo Mode DB); NM DB `TestTheBoundary`: unmarked populated database refused *even carrying the old NM comment*, production-marked refused, scan_runs untouched (300) |
| make Python network connections during a reset | psycopg reaches PostgreSQL through libpq, below Python's socket module | NM DB `TestRepeatability`: a **full Null Meridian reset** with `socket.socket.connect` and `socket.create_connection` replaced by functions that fail the test — passes, fingerprint identical; Demo Mode DB: every Python-level connect during a reset recorded — only the database port |
| deliver alerts outward | Non-`log` sinks refused at startup and on every policy read | `test_an_outbound_alert_sink_*`; `test_a_demo_refuses_an_outbound_alert_sink` |
| expose secrets | `/runtime`, `/version`, `/health/ready` carry no connection detail | `test_the_runtime_surface_discloses_no_connection_detail`; browser check on `/runtime` |

No Active Directory, LDAP, SMB, WinRM or PowerShell was contacted or executed, and no credential
was used, while building or validating this. The only databases touched were the container's
local PostgreSQL 16 (`adg`, `adg_base*`, `adg_i20*`, `adg_i21*`, `adg_demo`, and their
`_test`/`_demo_test`/`_nm_demo_test` derivatives).

## 8. Browser validation

The integrated API (`python -m app`, `ADG_RUNTIME_MODE=demo`, on the reset `adg_demo`) and the
production Next build (`next start`), driven by headless Chromium (Playwright 1.56.1):
**22/22 checks passed**, no page error. Screenshots, by the convention `demo-mode-01` set:

| Check | Result |
| --- | --- |
| Signed out: header pill reads **DEMO ENVIRONMENT · Synthetic enterprise data · Null Meridian** | ✓ — [indicator](adg-demo-integration-01-indicator.png) |
| Explanation: synthetic data, "No production Active Directory or file servers are connected", "The application is real"; no button or input | ✓ |
| Explanation lists the dataset's facts from the manifest (1,800 / 640 / 14 / 205 / 33 / 91 / 824 / 4,111 / 3 / 300 / 41,756) and links `/demo` | ✓ |
| Footer: organization and data fictional | ✓ |
| Sign in (development account `admin`) | ✓ |
| Demo guide renders scenarios **A–H** from `GET /api/v1/demo`; three generations applied | ✓ — [guide](adg-demo-integration-01-guide.png) |
| Scenario F's *explain their access* link opens the real explanation: the transferred employee against `Finance\Reports\Revenue`, both routes named | ✓ — [access path](adg-demo-integration-01-access-path.png) |
| Scenario B's folder renders on the real directory page with Null Meridian's ACL | ✓ |
| Risks page renders the full pass (3 critical, 8 high, 1,957 medium, 423 low, 6,995 informational = 9,386 open) | ✓ — [risks](adg-demo-integration-01-risks.png) |
| Scenario H through the real simulation editor: removing `Proj-Zephyr-RW`'s entry — **"This proposal changes nobody's access."** | ✓ — [simulation](adg-demo-integration-01-simulation.png) (see §9.3) |
| No production operation: web tier `POST /api/adg/api/v1/scan-runs` → 405; API `POST /api/v1/scan-runs` as administrator → `403 demo_only` | ✓ |
| `/runtime` → demo / synthetic / `null-meridian-v1`; no `postgresql`, password or database name in it; `X-ADG-Runtime-Mode: demo` on responses | ✓ |

## 9. Known limitations

1. **SQLAlchemy 2.1 drift (pre-existing, unchanged).** `pip` resolves `sqlalchemy>=2.0.30` to
   2.1.4, whose `DISTINCT ON` deprecation the suite promotes to an error: 80 DB tests fail on
   `main` and identically here (§6), and `/api/v1/collection/status` and the change feed raise
   under it in either runtime mode. With 2.0.54 everything passes. Not fixed here (no
   dependency or query change was in scope): switch `app/repositories/operations.py` and
   `app/changes/repository.py` to `postgresql.distinct_on`, or pin `sqlalchemy<2.1`.
2. **CI is red on `main` and here, identically** — at the Backend *Type check* step, on the
   three pre-existing mypy errors, so CI never reaches the backend unit or DB steps. Fixing
   item 1 and those three errors is what turns CI green; this branch adds no new error.
3. **Scenario H's simulation is bounded.** "Everything this change could affect" for
   `Engineering\Projects\Zephyr` reaches about 520 engineers, above the simulation's
   `max_principals` ceiling (500), so the preview reports `complete: false` with zero changes
   among the pairs it evaluated (50 at the UI default, 500 at the ceiling). That the clean-up
   changes nobody rests on the empty-group finding (the group has no members) and on those
   zero changes; the UI says plainly the answer is partial. Pre-existing: the simulation code
   is identical in all three trees and the data is byte-identical (§5); NM's DB test asserted
   zero changes, not completeness.
4. **`AlertRepository.by_keys`** has the unchunked `IN` shape `RiskFindingRepository.by_keys`
   had; bounded by alerts raised per evaluation (the demo keeps it small). Follow-up.
5. **Public sign-in posture undecided** (unchanged from Demo Mode): development sign-in is
   refused with `ADG_ENVIRONMENT=production`, so a public demo needs OIDC or a deliberate
   read-only visitor mode. `ADG_ENVIRONMENT=production` is *allowed* in demo mode by decision
   (§3.4), reversing the NM branch's refusal.
6. **Time.** A Null Meridian reset takes about 2¼ minutes here (132–140 s); the Null Meridian DB tests add about
   9–10 minutes to the smoke run (two full resets and the history replay). A synthetic scan of
   one Null Meridian generation is a synchronous request of a minute or two (100 runs and a
   risk pass); there is still no UI button for it (API and CLI only). Concurrent synthetic
   scans are not serialized and were not tested.
7. **Docker not exercised end to end.** No daemon in this container: the compose files are
   validated with `docker compose config`, and the image's dependency set is emulated with a
   clean venv holding only the non-dev dependencies (imports, reset, verify, status — §5).
8. **Evidence format changed.** `docs/demo/evidence/reset-{1,2}.json` are now the canonical
   reset report (Demo Mode's fingerprint and per-source digests). The NM-scope fingerprint is
   recorded in §5 and in `docs/demo/null-meridian.md` §9.
9. **Branch name.** The brief suggested `integration/ADG-DEMO-NULL-MERIDIAN`; this session's
   assigned branch `claude/adg-demo-null-meridian-integration-5biwm0` fills that role. It was
   pushed (the session's branch instructions require it; nothing else would survive the
   container); no PR was opened, nothing was merged or deployed.

## 10. Main merge recommendation

Merge this branch into `main` with a **merge commit** (the two source merges `22d4c9c` and
`a2a6d00` keep both histories visible; do not squash), after human review of §3. Before a
*public* deployment — not before the merge — decide the visitor sign-in posture (§9.5).
Independently of this work, the SQLAlchemy 2.1 query fix and the three mypy errors (§9.1–2)
should land on `main` so CI can run the backend suites at all; this branch neither causes nor
worsens either. The source branches `claude/vibrant-gates-rhovf1` and
`claude/inspiring-volta-6uzdsy` are fully contained here and can be retired after the merge.

## 11. Files changed (relative to `main`)

82 files. "reconciled" marks a file this integration changed beyond what the two source
merges brought in (`git diff a2a6d00`). Removed relative to the Null Meridian source, and so
absent from the list: `backend/app/demo/null_meridian/reset.py` (its reset, marker and
fingerprint — consolidated into `app.demo_mode`).

**Backend — Demo Mode (`app/demo_mode`, runtime, API, config)** — `backend/app/api/__init__.py` (modified); `backend/app/api/demo.py` (added, reconciled); `backend/app/api/health.py` (modified); `backend/app/api/meta.py` (modified); `backend/app/api/remediation.py` (modified); `backend/app/api/runtime.py` (added); `backend/app/config.py` (modified, reconciled); `backend/app/contracts/openapi.py` (modified, reconciled); `backend/app/demo_mode/__init__.py` (added); `backend/app/demo_mode/__main__.py` (added, reconciled); `backend/app/demo_mode/datasets.py` (added, reconciled); `backend/app/demo_mode/ingest.py` (added, reconciled); `backend/app/demo_mode/lifecycle.py` (added, reconciled); `backend/app/demo_mode/marker.py` (added); `backend/app/demo_mode/policy.py` (added); `backend/app/demo_mode/providers.py` (added); `backend/app/demo_mode/reference.py` (added); `backend/app/main.py` (modified); `backend/app/runtime_mode.py` (added).

**Backend — Null Meridian (`app/demo`)** — `backend/app/demo/estate.py` (modified); `backend/app/demo/null_meridian/__init__.py` (added, reconciled); `backend/app/demo/null_meridian/__main__.py` (added, reconciled); `backend/app/demo/null_meridian/canon.py` (added); `backend/app/demo/null_meridian/dataset.py` (added, reconciled); `backend/app/demo/null_meridian/estate.py` (added); `backend/app/demo/null_meridian/generations.py` (added, reconciled); `backend/app/demo/null_meridian/manifest.py` (added); `backend/app/demo/null_meridian/names.py` (added); `backend/app/demo/null_meridian/risk.py` (added); `backend/app/demo/null_meridian/scenarios.py` (added); `backend/app/demo/transcripts.py` (modified).

**Backend — other** — `backend/app/models/schema.py` (modified); `backend/app/repositories/risk.py` (modified, reconciled).

**Backend tests** — `backend/tests/api/test_authorization.py` (modified); `backend/tests/contracts/test_openapi_snapshot.py` (modified, reconciled); `backend/tests/db/test_null_meridian_demo.py` (added, reconciled); `backend/tests/db/test_risk_findings.py` (modified); `backend/tests/demo/test_demo_mode.py` (added, reconciled); `backend/tests/demo/test_null_meridian_canon.py` (added); `backend/tests/demo/test_null_meridian_estate.py` (added); `backend/tests/demo_mode/__init__.py` (added); `backend/tests/demo_mode/test_boundary.py` (added); `backend/tests/demo_mode/test_datasets.py` (added, reconciled); `backend/tests/demo_mode/test_demo_database.py` (added, reconciled); `backend/tests/demo_mode/test_runtime_mode.py` (added); `backend/tests/test_health_api.py` (modified).

**Database** — `database/migrations/versions/0017_deployment_marker.py` (added).

**Frontend** — `frontend/app/demo/page.tsx` (added, reconciled); `frontend/app/globals.css` (modified, reconciled); `frontend/components/AppShell.tsx` (modified); `frontend/components/Banners.tsx` (modified, reconciled); `frontend/components/DemoGuide.tsx` (added, reconciled); `frontend/lib/api/adg.ts` (modified, reconciled); `frontend/lib/api/health.ts` (modified); `frontend/lib/contracts.ts` (modified, reconciled); `frontend/tests/contracts.test.ts` (modified, reconciled); `frontend/tests/demo-guide.test.tsx` (added, reconciled); `frontend/tests/demo-indicator.test.tsx` (added, reconciled).

**Docs** — `docs/architecture/demo-mode.md` (added, reconciled); `docs/contracts/v1/openapi.demo.json` (added, reconciled); `docs/contracts/v1/openapi.json` (modified); `docs/decisions/0039-demo-is-a-deployment-boundary.md` (added, reconciled); `docs/decisions/README.md` (modified); `docs/demo/evidence/reset-1.json` (added, reconciled); `docs/demo/evidence/reset-2.json` (added, reconciled); `docs/demo/null-meridian.canon.json` (added); `docs/demo/null-meridian.manifest.json` (added); `docs/demo/null-meridian.md` (added, reconciled); `docs/handoffs/adg-demo-integration-01-access-path.png` (added, reconciled); `docs/handoffs/adg-demo-integration-01-guide.png` (added, reconciled); `docs/handoffs/adg-demo-integration-01-indicator.png` (added, reconciled); `docs/handoffs/adg-demo-integration-01-risks.png` (added, reconciled); `docs/handoffs/adg-demo-integration-01-simulation.png` (added, reconciled); `docs/handoffs/adg-demo-integration-01.md` (added, reconciled); `docs/handoffs/adg-demo-null-meridian-01.md` (added, reconciled); `docs/handoffs/demo-mode-01-indicator.png` (added); `docs/handoffs/demo-mode-01.md` (added, reconciled).

**Deployment and scripts** — `.env.example` (modified, reconciled); `README.md` (modified, reconciled); `docker-compose.demo.yml` (added, reconciled); `docker-compose.yml` (modified, reconciled); `scripts/demo-reset.ps1` (added, reconciled).
