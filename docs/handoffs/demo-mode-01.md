# Handoff — ADG-DEMO-MODE-01 (first-class Demo Mode around the synthetic data system)

> **Integrated by [ADG-DEMO-INTEGRATION-01](adg-demo-integration-01.md).** This file records
> the source branch as it was handed over. Where it describes the runtime values, the reset
> command, the database marker or the dataset's availability, the integration handoff and
> [docs/architecture/demo-mode.md](../architecture/demo-mode.md) are current.

**Date:** 2026-10-10
**Branch:** `claude/vibrant-gates-rhovf1` (not pushed — push not authorized)
**Starting SHA:** `cc13ad27e11e538c849d4e0a53f2358e36454392` (= `main`)
**Ending SHA:** the commit carrying this file (`git log -1 -- docs/handoffs/demo-mode-01.md`)
**Previous handoff:** [release-audit.md](release-audit.md)
**Design:** [docs/architecture/demo-mode.md](../architecture/demo-mode.md) ·
[ADR-0039](../decisions/0039-demo-is-a-deployment-boundary.md)
**Collector contract version after this work:** unchanged.
**`docs/contracts/v1/openapi.json`:** regenerated. **1 path added** (`GET /runtime`); additive
fields `runtime_mode` on `/version` and `runtime_mode`, `data_source` on `/health/ready`. The
demo-only `/api/v1/demo` routes are absent from the (production) snapshot by design.
**Migration:** `0017_deployment_marker` (one new, empty table; nothing existing touched).

---

## Verdict

**READY_FOR_DEMO_INTEGRATION_WITH_LIMITATIONS.** The real ADG application runs, end to end,
against synthetic data with no AD/LDAP/SMB/PowerShell/collector dependency and without
duplicating any generator. The limitation that matters: the canonical dataset
(`null-meridian-v1`) is a registered, contract-defined dependency whose generator has not
merged; today Demo Mode runs on the reference estate that ships in `app.demo`.

---

## 1. Synthetic-data dependency — what existed, and what was consumed

| Item | Finding |
| --- | --- |
| Synthetic data on `main` | `backend/app/demo/` — `estate.py` (deterministic AD+SMB+NTFS estate, `corp.example.com`, fixed `EPOCH` 2026-09-14), `transcripts.py` (collector-protocol v1 runs, uuid5 run ids, one **partial** and one **failed** NTFS run), `seed.py` (`seed_transcripts`, posts through the ingestion API), `__main__.py` (`python -m app.demo`), tests in `tests/demo/`, used by `tests/db/test_mvp_end_to_end.py`. |
| Null Meridian in this repo / history | **None.** No file, commit or remote branch mentions Null Meridian (`git log --all`, `grep -ri`; remote has only `main`). |
| Active Null Meridian implementation | Claude Code session `session_01XBaCzo5oHYWNiWxi3TZySp`, task ADG-DEMO-NULL-MERIDIAN-01, branch **`claude/inspiring-volta-6uzdsy`**, based on `cc13ad2`, **not pushed** at the time of writing (no SHA observable). Observed writing `backend/app/demo/null_meridian/{__main__,generations,manifest,reset,risk,scenarios}.py`, `docs/demo/null-meridian.md`, and editing `backend/app/config.py` and `backend/app/main.py`. |
| Interfaces consumed | `app.demo.transcripts.DemoTranscript`, `app.demo.transcripts.build_transcripts`, `app.demo.transcripts.restamp` (tests only), `app.demo.estate.build_estate` and its exported constants, `app.demo.seed.seed_transcripts`. **No file under `app/demo/` was modified.** |
| Contract added | `app.demo_mode.datasets.SyntheticDataset` (metadata, generations, probes, optional risk_configuration) and registry entry `null-meridian-v1 → app.demo.null_meridian:synthetic_dataset`. Proposed to the sibling session by message. |
| Status of the Null Meridian build | In progress on the sibling branch (generator, distributions, three scan generations, manifest, golden scenarios, its own reset). |
| Unresolved dependency | **DEPENDENCY** — branch `claude/inspiring-volta-6uzdsy` / SHA unknown (unpushed) / requires `app.demo.null_meridian.synthetic_dataset()` satisfying `SyntheticDataset` / status: **not integrated**. Until then `ADG_DEMO_DATASET=null-meridian-v1` (the default) is a clear startup refusal naming this branch; `adg-reference-estate` runs today. |

## 2. Architecture delivered

* **Runtime mode** — `app/runtime_mode.py` (`RuntimeMode.PRODUCTION | DEMO`, fail-closed
  `parse_runtime_mode`); `Settings.runtime_mode` / `demo_dataset` with demo validators.
* **Provider selection** — `app/demo_mode/providers.py`: production →
  `CollectorObservationSource` (Windows collectors push to the ingestion API, exactly as
  before); demo → `SyntheticObservationSource`. Stored on `app.state.observation_source`.
* **Synthetic adapter** — `app/demo_mode/ingest.py`: the existing `seed_transcripts` over an
  in-process ASGI client against the real application; ingestion principal and policy guard
  overridden only on that never-served app object.
* **Persistence isolation** — demo DB name rule + `deployment_marker` + empty-or-marked rule,
  re-checked under lock before the wipe, and at API startup.
* **Collector isolation** — external ingestion `403 demo_only` for every caller; collector
  keys refused at startup; no collector source constructed.
* **Mutation policy** — `app/demo_mode/policy.py`, exhaustive and tested: A supported (ADG's
  own records), B simulated (runbook export served inert + notice), C unavailable (ingestion).
* **Lifecycle** — `python -m app.demo_mode reset|status|scan|verify`; `/api/v1/demo` and
  `/api/v1/demo/scans` (demo only); `GET /runtime`; `X-ADG-Runtime-Mode` header.
* **UI** — header pill + expandable explanation + footer line (`DemoEnvironmentIndicator`).
* **Deployment** — `docker-compose.demo.yml` (own volume, `adg_demo`, one-shot reset service).

## 3. Safety validation

| Concern | How it is excluded | Evidence |
| --- | --- | --- |
| Active Directory / LDAP | The backend contains no directory client (ADR-0004); demo loads none. | `test_boundary.py::TestIsolation::test_a_demo_application_loads_no_infrastructure_client` (no `ldap`, `ldap3`, `impacket`, …in `sys.modules`); source scan of `app/demo_mode`. |
| SMB | No SMB client in the backend; none loaded. | Same tests (`smbprotocol`, `smbclient`). |
| PowerShell / WinRM | No process started by a demo app; reset starts only `python -m alembic`. | `test_a_demo_application_starts_no_process` (Popen recorded: none); `winrm`/`pypsrp` not loaded; source scan for `pwsh`, `invoke-command`. |
| Production collector | Ingestion routes refused in demo (anonymous, admin token, forged headers); collector keys refused at startup; collector source never constructed. | `TestTheGuard`, `TestProviderSelection::test_demo_never_constructs_the_collector_source`, `test_external_ingestion_is_refused_and_writes_nothing` (DB). |
| Production credentials | Demo starts with no OIDC, collector key, signing key or Windows account; collector keys and executors refused. | `test_demo_needs_no_production_credential`, `test_a_collector_credential_is_refused`, `test_a_remediation_executor_is_refused`. |
| Outbound delivery | Non-log alert sinks refused at startup and at every policy read. | `test_an_outbound_alert_sink_*`. |
| Production database | Name rule; marker; unmarked-and-populated refused; production-marked refused; API refuses unmarked at startup; wipe under marker lock. | `TestDemoRefusals`, `TestRefusals` (DB): a database filled through the production ingestion path is refused and left byte-for-byte untouched. |
| Python-level network during reset | Every Python socket connect during a full reset recorded: only the database port. | `test_the_reset_contacts_nothing_over_python_sockets_but_the_database`. |
| Live run | API served in demo mode on `adg_demo`: `/runtime` correct, `POST /api/v1/scan-runs` → `403 demo_only` with `x-adg-runtime-mode: demo`. | §6 below. |

## 4. Synthetic integration validation

* **Entry point:** registry → `ReferenceEstateDataset` → `app.demo.build_estate("standard")`
  + `build_transcripts`; transcripts asserted **byte-identical** to `app.demo`'s own.
* **Seed/reset:** `reset_demo` = migrate → identify → lock+wipe → ingest through real routes →
  `evaluate_risks` (the production operator command) → validate.
* **Representative objects** (the estate's own ids; verified in current state on every reset):
  Alice `…-1104`; Finance-RW `…-1202`; Alice ∈ Finance-Team; Finance-Team ∈ Finance-RW (nested);
  `\\FS01\Finance` share; Everyone on its share ACL; `\\FS01\Finance` NTFS root; Finance-RW ACE
  on it. Additionally: every principal's display name and the Finance root `acl_hash` equal the
  generator's; membership-edge count equals the estate's.
* **Determinism:** two resets → identical fingerprint and counts; a reset after using the demo
  (an extra run, or a synthetic scan) returns to the baseline fingerprint. Measured on the
  reference estate: `f3f70279690da53c997f77d10ac2dc74964e37be0a4626987277df353222e116`,
  reproduced across three separate processes.
* **Temporal/current state:** partial (FS02) and failed (FS03) runs recorded as claimed and
  tombstone nothing; FS03's share survives its failed NTFS scan; exactly one open version per
  object; re-posting the partial run erases nothing. Tombstone/disappearance generations come
  from the Null Meridian generator once merged.
* **Synthetic scan hook:** with a test-only two-generation wrapper (reference baseline re-posted
  under fixed uuid5 run ids), `POST /api/v1/demo/scans` applies generation 1 through the real
  routes (201), reports both applied, refuses a third (409); reset restores the baseline.

## 5. Commands

```bash
cd backend
export ADG_RUNTIME_MODE=demo ADG_DEMO_DATASET=adg-reference-estate \
       ADG_DATABASE_URL=postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg_demo
python -m app.demo_mode reset            # initialize / restore canonical baseline
python -m app.demo_mode --json status    # read-only report
python -m app.demo_mode scan             # next synthetic generation, if the dataset has one
python -m app                            # serve the API in demo mode
# Docker: docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build

# Tests
python -m pytest tests/demo_mode                              # hermetic
ADG_RUN_SMOKE_TESTS=1 python -m pytest tests/demo_mode        # + PostgreSQL integration
cd ../frontend && npx vitest run tests/demo-indicator.test.tsx
```

## 6. Test results

Environment: Linux container, Python 3.13.16, PostgreSQL 16 (local), Node 22, packages as
resolved by `pip install -e ".[dev]"` today (FastAPI 0.143, SQLAlchemy 2.1.4, pytest 9.1).

| Suite | Result |
| --- | --- |
| Demo Mode — hermetic (`tests/demo_mode`, no DB) | **81 passed**, 2 skipped (`TestNullMeridianCanon`: dependency not merged) |
| Demo Mode — PostgreSQL integration (`test_demo_database.py`) | **24 passed** |
| Backend full suite at **starting SHA** `cc13ad2` (smoke enabled) | 82 failed · 6611 passed · 124 skipped · 1 xfailed (2 of the 82 are `test_database_smoke` pointed at a base database the baseline run did not create) |
| Backend full suite on **this branch** (smoke enabled) | **80 failed · 6724 passed · 126 skipped · 1 xfailed** |
| Branch failures re-run at the starting SHA | **all 80 fail there too** — zero regressions. Single cause in every one: `SADeprecationWarning: Passing expression to distinct to generate a DISTINCT ON clause is deprecated` (SQLAlchemy 2.1), promoted to an error by the suite's warning filter. |
| `ruff check app` / `ruff format --check app` | clean |
| `mypy app` | 2 errors, both pre-existing and identical at `cc13ad2` (`app/runtime.py` Windows-only attribute; `app/changes/repository.py:398`) |
| Frontend `vitest run` | **1139 passed** (32 files; includes 7 new demo-indicator tests) |
| Frontend `tsc --noEmit`, `eslint .`, `next build` | clean / clean / success |
| Collector Pester suites | not run (no `pwsh` in this container); no collector file changed |

Tests changed outside `tests/demo_mode` (none weakened): `tests/api/test_authorization.py`
gains the `GET /runtime → public` entry its exhaustive table demands;
`tests/test_health_api.py` asserts the exact `/version` body including the new
`runtime_mode`; the OpenAPI snapshot was regenerated with `python -m app.contracts.openapi`;
`frontend/tests/contracts.test.ts` checks the new `RuntimeView`/`DatasetView` fields and the
added readiness/version fields.

**Live run.** `python -m app.demo_mode reset` against `adg_demo`, then `python -m app` and
`next start`: `/runtime` reported demo and the dataset; `POST /api/v1/scan-runs` →
`403 {"status":"demo_only",…}` with `x-adg-runtime-mode: demo`; the shell rendered the
indicator for a signed-out visitor — [screenshot](demo-mode-01-indicator.png). Reset reported
8 runs (FS02 `partial`, FS03 `failed`), 8/8 probes, 65 open risk findings, ~2.6–3.2 s.

## 7. Known limitations

1. **Null Meridian not integrated** (dependency above). The default dataset refuses until it is.
2. **No UI button for "Run synthetic scan".** The API (`POST /api/v1/demo/scans`) and CLI exist;
   the reference estate defines no second generation, so there is nothing for a button to do yet.
3. **Public sign-in posture undecided.** Development auth is refused with
   `ADG_ENVIRONMENT=production`; a public demo needs OIDC or a deliberate read-only visitor mode.
4. **Remediation runbook B-treatment is applied at the API layer** (create/read export, runbook
   download). The signed document is unchanged by design.
5. **Pre-existing, environment-specific failures** (80 tests, all also failing at `cc13ad2`):
   SQLAlchemy 2.1 deprecates `.distinct(cols)` for `DISTINCT ON`
   (`app/repositories/operations.py`, `app/changes/repository.py`), and the suite turns that
   warning into an error. It breaks `/api/v1/collection/status` and the change feed in either
   runtime mode. Not caused or touched by this work; pinning `sqlalchemy<2.1` or moving to
   `postgresql.distinct_on` is a separate fix. The demo integration tests use other reads.

## 8. Merge / integration notes

**Conflicts expected with `claude/inspiring-volta-6uzdsy`:**

| File | This branch | Sibling branch | Resolution |
| --- | --- | --- | --- |
| `backend/app/config.py` | `runtime_mode: RuntimeMode` enum (`production`/`demo`, fail-closed), `demo_dataset`, demo validators | `runtime_mode: Literal["standard","demo"]`, `database_name`, `_confine_demo_mode`, demo risk config import | Keep this branch's field (the task fixes the value as `production`); fold the sibling's stricter checks in as wanted (it also refuses demo with `ADG_ENVIRONMENT=production` and any `ADG_ALERT_POLICY_PATH`); expose its `DEMO_RISK_CONFIGURATION` as the dataset's `risk_configuration()` instead of importing it from config. |
| `backend/app/main.py` | lifespan demo check, observation source, demo header middleware, `demo_only` handler | runtime-mode startup log / response header | Keep one header implementation (`X-ADG-Runtime-Mode`). |
| Reset command | `python -m app.demo_mode reset` (dataset-agnostic) | `python -m app.demo.null_meridian reset` | Keep one operator workflow: have the Null Meridian module expose `synthetic_dataset()` and drop its own reset/marker logic, or reduce it to a thin call into `app.demo_mode.lifecycle.reset_demo`. |
| Migrations | `0017_deployment_marker` (down_revision `0016_current_state_presence`) | none observed | If the sibling adds one, renumber/merge revisions. |

**Recommended order:** (1) merge this branch (architecture, contract, guards; runs on the
reference estate); (2) rebase the Null Meridian branch onto it, resolving the table above and
adding `app/demo/null_meridian/__init__.py: synthetic_dataset()`; (3) the skipped
`TestNullMeridianCanon` tests then run and must pass; (4) switch `docker-compose.demo.yml`'s
default to `null-meridian-v1`.
