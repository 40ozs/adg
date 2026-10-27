# ADG

> **Synthetic data only.** All identities, organizations, directory objects, groups,
> resources, ACLs, findings, collection runs, provider responses, audit evidence, comparison
> data and credentials used by the ADG demo are synthetic or sanitized. No employer data,
> production credentials, customer information or live production directory data is
> included.

ADG is an evidence-aware access analysis system for Windows file shares. It reads identity,
group membership, SMB share and NTFS permission facts reported by collectors, and explains
*why* a person can reach a folder: the access paths, the collected evidence behind each step,
what it could not confirm, how complete the collection was, and what that means for risk
findings and for comparisons over time. It is read-only: it observes and explains
permissions, and changes nothing.

**For reviewers:**
[Demo guide](docs/demo/README.md) ·
[Run the demo](#run-the-demo) ·
[Architecture](docs/architecture/system-overview.md#the-reviewer-view-from-collection-to-screen) ·
[Code to review](#code-to-review) ·
[Representative tests](#representative-tests) ·
[Design decisions](#key-design-decisions) ·
[Demo reports](docs/demo/reports/) ·
[Limitations](#known-limitations)

## What problem it demonstrates

Enterprise access questions are hard to answer honestly. To say why someone can open a
folder, you have to combine directory identities, nested group membership, the share ACL and
the NTFS ACL. Each comes from a different collector, at a different time, and any of them can
be missing, stale or contradictory: a SID nobody can resolve, a group whose members were
never collected, a file server whose last scan failed.

The easy way to build such a tool is to treat missing evidence as absence and print a
confident answer. ADG deliberately does not. It keeps the uncertainty in the answer:

* known rights are reported as a **lower bound** (`at_least`) when unseen evidence could
  widen them;
* an ambiguous identity mapping is reported as **not determined**, never as "no access";
* a failed or unreachable collector is never shown as "checked, found nothing";
* a comparison that cannot finish in time **says so**, and presents nothing it did not
  finish.

The demo shows each of these on a deterministic synthetic company, **Genesee Press** (1,800
people, 205 groups, 33 file servers, three scans).

## What to look at first

A 10-minute path through the running demo. The [demo guide](docs/demo/README.md) has each step
in detail.

1. Open `http://localhost:3000/login`, sign in as **`auditor`**, then open `/demo`.
2. Under *Start here: a guided access investigation*, choose **Investigate access**. This asks
   *Why does Fatima Gordon have access to `\\GP-ENG-FS01\Engineering\Projects\Apollo`?*
3. Section 1: access **granted**, **Modify**, certainty **`at_least`**, over SMB.
4. Section 2: the **direct** NTFS entry and the share route **through `ENG-Mobile`**. Open
   *Every other route ADG found* for the **inherited** route through five nested groups.
5. Open **Evidence for hop N** under any hop: the collector, the scans and the stored row.
6. Section 3: two pieces of **missing evidence** (an unresolved former-employee SID and an
   uncollected group). Neither can take the access away; both mean the rights are a lower
   bound.
7. Section 6, then `/risks`: **collection coverage** beside the answer and beside the risk
   totals. One file server was unreachable on the last scan, so the totals read *not
   exhaustive*, and its 189 findings stay counted.
8. Back on `/demo`, choose **Run the comparison drill**: it **times out** with nothing
   presented, a retry stops **incomplete** at a named boundary, and **Compare only folder
   permission entries** completes and is marked **recovered**.

There is no hosted demo for this branch. [Run it locally](#run-the-demo).

## What is technically interesting

* **One access engine, no demo-only path.** Every screen in the demo is computed by the same
  engine, API and database a production deployment uses. Demo Mode only replaces the
  collectors with a synthetic provider that posts through the real ingestion routes
  ([demo-mode.md](docs/architecture/demo-mode.md)).
* **Access as a Windows access check.** ADG builds an access token for one declared access
  path, evaluates both ACLs in DACL order, and intersects them
  ([effective-access.md](docs/architecture/effective-access.md)).
* **Paths are measured, not inferred.** Each causal path carries the measured effect of
  removing it, so "removing this changes nothing" is computed, not guessed
  ([access-causality.md](docs/architecture/access-causality.md)).
* **Uncertainty has a direction.** Every gap is a typed finding that may *overstate* or
  *understate* the answer. The findings fold into `certain`, `at_least`, `at_most` or
  `uncertain`, and are never averaged into a score.
* **Coverage is per scope, and two facts per scope.** What the latest scan did (`collected`,
  `partial`, `unavailable`, `failed`, `running`) is kept apart from how fresh the held evidence
  is (`current`, `stale`, `none`).
* **Bounded comparisons with typed outcomes.** A point-in-time comparison is bounded in size
  and time, ends as `complete`, `incomplete`, `timed_out` or `cancelled`, and offers real
  recovery requests.

## Code to review

The paths below are where each claim is implemented. Everything else in the repository
supports these.

**Access logic** (`backend/app/access_engine/`, pure functions, no I/O)

| What | Where |
| --- | --- |
| Access token construction, SYSTEM mapping | [`subjects.py`](backend/app/access_engine/subjects.py): `build_token`, `system_mapping_of`, `default_assumption` |
| ACL evaluation in DACL order | [`evaluation.py`](backend/app/access_engine/evaluation.py): `evaluate_acl`; rights math in [`rights.py`](backend/app/access_engine/rights.py) |
| Access verdict and certainty | [`resolver.py`](backend/app/access_engine/resolver.py): `resolve_access`, `certainty_of`; [`verdict.py`](backend/app/access_engine/verdict.py): `classify_access`, `describe_verdict` |
| Path derivation and measured removals | [`causality.py`](backend/app/access_engine/causality.py): `explain_access` |
| The investigation (primary routes, open questions, impact, next steps) | [`investigation.py`](backend/app/access_engine/investigation.py): `investigate` |
| Loading the facts, and the coverage findings | [`backend/app/services/access.py`](backend/app/services/access.py): `AccessService.explain_access`, `_coverage_findings`, `_could_contain_members` |

**Evidence and uncertainty**

| What | Where |
| --- | --- |
| Condition vocabulary and the direction each can be wrong in | [`conditions.py`](backend/app/access_engine/conditions.py): `AccessCondition`, `OVERSTATING_CONDITIONS`, `UNDERSTATING_CONDITIONS` |
| Mapping and evidence states | `SystemMapping` in [`subjects.py`](backend/app/access_engine/subjects.py); `EvidenceState`, `ConclusionImpact` in [`investigation.py`](backend/app/access_engine/investigation.py) |
| Unresolved identities | `_could_contain_members` in [`services/access.py`](backend/app/services/access.py); `isUnresolved` in [`frontend/lib/identity.ts`](frontend/lib/identity.ts) |
| Hop provenance (collector, first and last scan, stored row) | [`backend/app/repositories/provenance.py`](backend/app/repositories/provenance.py): `ProvenanceRepository.observed` |
| Collection state per scope | [`backend/app/domain/collection.py`](backend/app/domain/collection.py): `assess_collection`; [`backend/app/domain/operations.py`](backend/app/domain/operations.py) |

**Risk and coverage**

| What | Where |
| --- | --- |
| Risk evaluation (eleven deterministic rules) | [`backend/app/risk_engine/engine.py`](backend/app/risk_engine/engine.py): `evaluate`; rules in [`rules.py`](backend/app/risk_engine/rules.py) |
| Collection coverage (scope state, evidence freshness, expected collectors) | [`backend/app/domain/coverage.py`](backend/app/domain/coverage.py): `assess_coverage`, `ScopeState`, `EvidenceAge`; [`backend/app/services/coverage.py`](backend/app/services/coverage.py): `expected_collectors` |
| How risk totals may be read (`complete` / `partial` / `incomplete` / `stale` / `not_evaluated`) | `interpret_totals`, `TotalsReading` in [`coverage.py`](backend/app/domain/coverage.py); served by [`backend/app/api/risks.py`](backend/app/api/risks.py) |
| The snapshot each risk pass read | [`database/migrations/versions/0018_risk_evaluation_basis.py`](database/migrations/versions/0018_risk_evaluation_basis.py) |

**Comparison and recovery**

| What | Where |
| --- | --- |
| The comparison, its aligned cut and its checkpoints | [`backend/app/changes/service.py`](backend/app/changes/service.py): `ChangeService.compare`, `_aligned_boundary` |
| Size and time bounds | `COMPARISON_CEILING`, `COMPARISON_WINDOW_MS`, `ComparisonBudget` in [`service.py`](backend/app/changes/service.py) |
| Typed outcomes | `ComparisonOutcome`, `ComparisonPhase` in [`service.py`](backend/app/changes/service.py) |
| Recovery actions and telemetry | [`backend/app/api/changes.py`](backend/app/api/changes.py): `compare_instants`, `_recovery`, the `changes.compare` log line |
| The deterministic drill | `GeneseePressDataset.comparisons` in [`backend/app/demo/genesee_press/dataset.py`](backend/app/demo/genesee_press/dataset.py) |

**UI** (Next.js; renders API fields and computes no permissions)

| What | Where |
| --- | --- |
| Investigation page | [`frontend/app/access/investigate/page.tsx`](frontend/app/access/investigate/page.tsx), [`components/Investigation.tsx`](frontend/components/Investigation.tsx), [`lib/investigation.ts`](frontend/lib/investigation.ts) |
| Risks page | [`frontend/app/risks/page.tsx`](frontend/app/risks/page.tsx), [`components/Risks.tsx`](frontend/components/Risks.tsx) |
| Comparison page | [`frontend/app/changes/compare/page.tsx`](frontend/app/changes/compare/page.tsx), [`components/Comparison.tsx`](frontend/components/Comparison.tsx), [`components/ComparisonLink.tsx`](frontend/components/ComparisonLink.tsx) |
| Coverage components and wording | [`frontend/components/Coverage.tsx`](frontend/components/Coverage.tsx), [`lib/coverage.ts`](frontend/lib/coverage.ts) |
| Explanation components | [`frontend/components/Explanation.tsx`](frontend/components/Explanation.tsx), [`lib/explanation.ts`](frontend/lib/explanation.ts) |
| Demo guide and demo indicator | [`frontend/components/DemoGuide.tsx`](frontend/components/DemoGuide.tsx), [`components/Banners.tsx`](frontend/components/Banners.tsx) |

## Representative tests

A short list, chosen to prove the claims above. The full suites are much larger (about 6,000
backend unit tests, 1,270 PostgreSQL tests and 1,360 frontend tests).

**ADG-DEMO-01: SYSTEM explanation correctness** ([report](docs/demo/reports/ADG-DEMO-01-report.md))

| Test | What it proves |
| --- | --- |
| [`test_system_mapping.py`](backend/tests/access_engine/test_system_mapping.py) `TestAmbiguous::test_the_default_question_is_indeterminate_not_no_grant` | SYSTEM over a share is an ambiguous mapping, answered "not determined" and never "no grant". |
| [`test_system_mapping.py`](backend/tests/access_engine/test_system_mapping.py) `TestMissing::test_no_token_is_invented_for_it` | When no collector described SYSTEM, ADG invents no group memberships for it. |
| [`test_system_mapping.py`](backend/tests/access_engine/test_system_mapping.py) `TestConflicting::test_both_sides_and_the_provenance_are_preserved` | A collected description that contradicts the SID's fixed meaning is kept, with both sides and the describing run. |
| [`test_system_mapping.py`](backend/tests/access_engine/test_system_mapping.py) `TestConfirmed::test_an_acl_naming_only_the_servers_administrators_is_not_a_no` | Even a confirmed SYSTEM mapping never yields an unsupported definite negative. |
| [`test_system_explanation_regression.py`](backend/tests/access_engine/test_system_explanation_regression.py) `TestNoContradictionAnywhereInTheMatrix` | Across every description × path × assumption × share shape, the verdict, certainty and paths agree. This module failed 46 of 52 before the fix. |
| [`test_system_mapping_api.py`](backend/tests/db/test_system_mapping_api.py) `TestConflictingEvidence::test_the_conflict_and_its_provenance_reach_the_client` | The mapping state survives ingestion → PostgreSQL → API → schema. |
| [`system-mapping.test.tsx`](frontend/tests/system-mapping.test.tsx) "a SID a run saw and could not resolve" | A stored but unresolved identity renders as unresolved, not as a resolved name. |

**ADG-DEMO-02: the guided access investigation** ([report](docs/demo/reports/ADG-DEMO-02-report.md))

| Test | What it proves |
| --- | --- |
| [`test_genesee_press_demo.py`](backend/tests/db/test_genesee_press_demo.py) `TestTheAccessInvestigation::test_the_ntfs_route_is_the_direct_entry_and_the_share_route_a_group` | On a real reset, the canonical answer's primary routes are the direct NTFS entry and the share entry via `ENG-Mobile`. |
| [`test_genesee_press_demo.py`](backend/tests/db/test_genesee_press_demo.py) `TestTheAccessInvestigation::test_the_inherited_route_through_five_groups_is_shown_in_order_and_redundant` | The inherited route is shown hop by hop, and marked redundant rather than primary. |
| [`test_genesee_press_demo.py`](backend/tests/db/test_genesee_press_demo.py) `TestTheAccessInvestigation::test_every_hop_carries_its_collector_and_scan` | Every hop carries its provenance: collector, method, and first and last observing scan. |
| [`test_genesee_press_demo.py`](backend/tests/db/test_genesee_press_demo.py) `TestTheAccessInvestigation::test_access_is_observed_and_qualified_not_certain` | The answer is `granted`, Modify, `at_least`: a lower bound, not certainty. |
| [`test_genesee_press_demo.py`](backend/tests/db/test_genesee_press_demo.py) `TestTheAccessInvestigation::test_what_cannot_be_confirmed_is_named_and_does_not_reach_the_conclusion` | Both gaps are named as missing evidence, and both are judged unable to remove the access. |
| [`test_unidentified_trustees.py`](backend/tests/access_engine/test_unidentified_trustees.py) `test_a_record_saying_nothing_resolved_the_sid_could_be_a_group` | An unresolved trustee counts as "possibly a group", so it cannot be used to claim certainty. |
| [`test_investigation.py`](backend/tests/access_engine/test_investigation.py) `TestWhetherAnOpenQuestionReachesTheConclusion::test_an_unread_share_acl_could_take_a_grant_away` | When missing evidence *could* remove the access, the impact is `presence` and the answer is provisional. |

**ADG-DEMO-03: coverage, comparison timeout and recovery** ([report](docs/demo/reports/ADG-DEMO-03-report.md))

| Test | What it proves |
| --- | --- |
| [`test_coverage.py`](backend/tests/domain/test_coverage.py) `TestZeroFindingsIsNotMissingCollection::test_the_two_zeros_never_share_a_verdict` | A failed collection with zero observations is never the same as a clean scan that found nothing. |
| [`test_collection_coverage.py`](backend/tests/db/test_collection_coverage.py) `TestAFailedSourceDoesNotLowerRisk::test_findings_on_a_server_whose_scan_failed_stay_open_and_counted` | A later failed scan does not erase or uncount existing findings. |
| [`test_coverage.py`](backend/tests/domain/test_coverage.py) `TestReadingRiskTotals::test_every_reason_is_listed_and_the_worst_one_wins` | Risk totals read `complete`, `partial`, `incomplete`, `stale` or `not_evaluated`, with every reason listed. |
| [`test_collection_coverage.py`](backend/tests/db/test_collection_coverage.py) `TestTheCoverageReport::test_a_server_that_failed_after_a_success_holds_stale_evidence` and `TestAnExpectedCollectorThatNeverReported::test_is_not_collected_and_makes_coverage_incomplete` | Stale evidence and a never-reported expected collector both make coverage visibly incomplete. |
| [`test_comparison_bounds.py`](backend/tests/db/test_comparison_bounds.py) `TestTheInteractiveWindow::test_past_the_window_after_reading_it_stops_and_presents_nothing` | A comparison past its window stops as `timed_out` and presents no partial list. |
| [`test_comparison_bounds.py`](backend/tests/db/test_comparison_bounds.py) `TestTheSidesAreCutAtTheSameKey::test_objects_past_the_shorter_cut_are_not_called_unobserved` | An `incomplete` comparison is cut at one aligned boundary, so nothing past it is misreported. |
| [`test_comparison_bounds.py`](backend/tests/db/test_comparison_bounds.py) `TestTheApi::test_timeout_then_recovery_then_a_complete_recovered_comparison` | A timed-out comparison offers a narrower request, which completes and is marked recovered. |
| [`test_genesee_press_demo.py`](backend/tests/db/test_genesee_press_demo.py) `TestTheCoverageAndRecoveryDemo::test_the_comparison_drill_times_out_then_recovers_to_complete` | The demo drill runs `timed_out` → `incomplete` → `complete` (recovered) on a real reset. |
| [`compare-page.test.tsx`](frontend/tests/compare-page.test.tsx) "reads an older server's truncated comparison as incomplete, never complete" | The UI never presents a partial comparison as complete. |
| [`risks-page.test.tsx`](frontend/tests/risks-page.test.tsx) "calls the counts totals only when evaluation and collection are both complete" | The risks page says "not exhaustive" unless both evaluation and collection are complete. |

How to run them is under [Run the tests](#run-the-tests).

## Key design decisions

These are the decisions ADG makes. They are not presented as universal best practice.

* **Preserve uncertainty.** Missing or conflicting evidence stays explicit, as a typed
  finding with a direction, rather than being coerced into a confident answer.
  [ADR-0011](docs/decisions/0011-answers-carry-their-uncertainty.md),
  [ADR-0015](docs/decisions/0015-emptiness-is-attributed-by-the-backend.md),
  [ADG-DEMO-01 §4](docs/demo/reports/ADG-DEMO-01-report.md#4-the-invariant-now-enforced).
* **Lower-bound access semantics.** When an unresolved trustee or an uncollected group could
  widen the rights, ADG reports the known rights as `at_least` rather than as certain.
  [ADR-0011](docs/decisions/0011-answers-carry-their-uncertainty.md),
  [ADG-DEMO-02 §4](docs/demo/reports/ADG-DEMO-02-report.md#4-evidence-model).
* **Evidence-aware risk totals.** Risk totals are read in the context of the collection and
  the snapshot they were computed over. A failed collector does not erase existing findings:
  a scan that did not reconcile a scope cannot mark anything absent.
  [ADR-0018](docs/decisions/0018-history-is-a-versioned-observation-log.md),
  [ADR-0023](docs/decisions/0023-risk-findings-are-reproducible-from-their-evidence.md),
  [ADG-DEMO-03 §3–4](docs/demo/reports/ADG-DEMO-03-report.md#3-coverage-semantics).
* **Bounded interactive comparisons.** Interactive comparison work is bounded in objects and
  time. A partial or timed-out comparison is never reported as complete.
  [ADR-0012](docs/decisions/0012-bounded-access-queries.md) (the same principle for access
  queries), [ADG-DEMO-03 §6](docs/demo/reports/ADG-DEMO-03-report.md#6-implementation).
* **Recovery by narrower scope.** The demo recovers by reducing the scope of a comparison. It
  does not pretend an estate-wide synchronous comparison can always finish, and there is no
  background job system.
  [ADR-0020](docs/decisions/0020-simulation-is-the-engine-reading-an-overlay.md) (no job
  queue), [ADG-DEMO-03 §6.2](docs/demo/reports/ADG-DEMO-03-report.md#62-typed-outcomes-and-recovery).
* **Demo is a deployment boundary.** The demo is the real application on synthetic data,
  selected by configuration, with no switch in the UI.
  [ADR-0039](docs/decisions/0039-demo-is-a-deployment-boundary.md).

All decisions: [docs/decisions/](docs/decisions/).

## Run the demo

The synthetic demo needs **no Windows host, domain, SMB server or collector**. These steps
were run from a fresh clone on Linux (Ubuntu 24.04) for
[DEMO-PUBLIC-01](docs/demo/reports/DEMO-PUBLIC-01-report.md#9-setup-validation).

### Requirements

| Tool | Version |
| --- | --- |
| Python | 3.11 or later (CI uses 3.13) |
| Node.js | 20.9 or later (CI uses 22) |
| PostgreSQL | 16 |

### 1. A PostgreSQL role and a demo database

The demo database must have `demo` as a word in its name; the reset refuses anything else.
With a local PostgreSQL 16, as a superuser:

```bash
psql -U postgres -c "CREATE ROLE adg LOGIN CREATEDB PASSWORD 'adg_dev_password';"
psql -U postgres -c "CREATE DATABASE adg_demo OWNER adg;"
```

`adg_dev_password` is the local development default from [`.env.example`](.env.example), not a
secret. `CREATEDB` is only needed for the PostgreSQL test suite, which creates its own
databases.

### 2. Backend: install, reset the synthetic estate, serve

```bash
cp .env.example .env
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

export ADG_RUNTIME_MODE=demo
export ADG_DATABASE_URL=postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg_demo
.venv/bin/python -m app.demo_mode reset    # migrate, wipe, ingest 3 synthetic scans, evaluate risks
.venv/bin/python -m app.demo_mode verify   # re-check the canonical objects; changes nothing
.venv/bin/python -m app                    # the API on http://127.0.0.1:8000
```

The reset takes a few minutes. It ingests 300 synthetic collector runs through the real
ingestion routes and ends with a fingerprint of the database.

### 3. Frontend

In a second terminal, from the repository root:

```bash
cd frontend
npm install
npm run build
npm run start                              # http://localhost:3000
```

`npm run dev` also works for development.

### 4. Open the demo

Open `http://localhost:3000/login`, sign in as **`auditor`**, and go to
`http://localhost:3000/demo`. Then follow the [demo guide](docs/demo/README.md).

### With Docker Compose instead

The repository also ships a Compose overlay that runs the reset and the API in containers:

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
```

This path was **not** run for DEMO-PUBLIC-01 (no Docker in the validation environment). CI
validates the base Compose file only.

### Run the tests

From `backend/` with the virtual environment above. The PostgreSQL tests read the database URL
from `.env` (or `ADG_DATABASE_URL`) and create their own `<name>_test` databases beside it.

```bash
# Representative unit tests (no database)
.venv/bin/python -m pytest -q tests/access_engine/test_system_mapping.py \
  tests/access_engine/test_system_explanation_regression.py \
  tests/access_engine/test_investigation.py tests/access_engine/test_unidentified_trustees.py \
  tests/domain/test_coverage.py

# Representative PostgreSQL tests (the Genesee Press classes run a full reset: several minutes)
ADG_RUN_SMOKE_TESTS=1 .venv/bin/python -m pytest -q tests/db/test_system_mapping_api.py \
  tests/db/test_collection_coverage.py tests/db/test_comparison_bounds.py \
  "tests/db/test_genesee_press_demo.py::TestTheSystemExplanation" \
  "tests/db/test_genesee_press_demo.py::TestTheAccessInvestigation" \
  "tests/db/test_genesee_press_demo.py::TestTheCoverageAndRecoveryDemo"

# Full suites, as CI runs them
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy app tests
.venv/bin/python -m pytest -q -m "not smoke"                  # unit
ADG_RUN_SMOKE_TESTS=1 .venv/bin/python -m pytest -q -m smoke  # PostgreSQL, about 30 minutes
```

Run `unset ADG_RUNTIME_MODE ADG_DATABASE_URL` first if you exported them for the demo in the
same shell.

From `frontend/`:

```bash
npx vitest run tests/system-mapping.test.tsx tests/investigation-page.test.tsx \
  tests/risks-page.test.tsx tests/compare-page.test.tsx tests/coverage.test.ts
npm run lint && npm run typecheck && npm run test && npm run build
```

## Known limitations

**Accepted product limitations of the demo** (each is explained in the
[ADG-DEMO-03 report §12](docs/demo/reports/ADG-DEMO-03-report.md#12-limitations)):

* Expected collectors are declared per collector kind, not per server.
* "Stale" means the latest scan of a scope delivered nothing (it failed or could not reach
  its target) while an earlier scan did. It is not an age threshold.
* The page-wide collection-health banner was not redesigned.
* An estate-wide demo comparison cannot finish inside one interactive request. The supported
  recovery is narrowing the scope.
* Comparison bounds rely on in-process checkpoints, not a background job system.
* Remote-database behavior was modeled with injected per-statement latency, not measured on
  hosted production infrastructure.
* Browser validation uses one-off evidence scripts, not a committed browser-test framework.
* SYSTEM over a share is never resolved to a computer account, and `NETWORK SERVICE` and
  `LOCAL SERVICE` are not specially handled
  ([ADG-DEMO-01 §9](docs/demo/reports/ADG-DEMO-01-report.md#9-known-limitations-and-deferred-work)).

**Beyond the demo:** the NTFS collector has been validated against real volumes, but the
Active Directory and SMB collectors have never met a real domain
([known-limitations.md](docs/release/known-limitations.md)). ADG is not presented as
production-ready.

## Demo reports and evidence

| Report | What it covers |
| --- | --- |
| [ADG-DEMO-01](docs/demo/reports/ADG-DEMO-01-report.md) | SYSTEM explanation correctness, and regression protection |
| [ADG-DEMO-02](docs/demo/reports/ADG-DEMO-02-report.md) | The guided access investigation: routes, provenance, open questions |
| [ADG-DEMO-03](docs/demo/reports/ADG-DEMO-03-report.md) | Collection coverage, risk interpretation, comparison timeout and recovery |
| [DEMO-PUBLIC-01](docs/demo/reports/DEMO-PUBLIC-01-report.md) | This reviewer presentation, the public-readiness scan, setup validation |

Each report links its evidence directory: API responses, screenshots, browser scripts and
measurements. All of it is synthetic.

---

# Product reference

Everything below describes the full product, including the Windows collectors and the
production deployment path. A reviewer of the demo does not need it.

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
  release/        Readiness, security review, measured performance, known limits.
scripts/          Windows developer commands (PowerShell 7).
  windows-test-tree/  Builds real NTFS trees to validate and benchmark the collector against.
```

## Demo Mode

The same application can run as a **demo deployment** on **Genesee Press**, a deterministic
synthetic company (1,800 people, 205 groups, 33 file servers, three scans), with no Windows
host, collector, domain, SMB server or PowerShell anywhere in it:

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
```

or, against a local PostgreSQL with a dedicated `adg_demo` database, from `backend/` with its
virtual environment active (every step, as validated, is in [Run the demo](#run-the-demo)):

```bash
export ADG_RUNTIME_MODE=demo \
       ADG_DATABASE_URL=postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg_demo
python -m app.demo_mode reset      # migrate, mark, wipe, ingest 3 scans, evaluate, validate
python -m app.demo_mode verify     # re-check the canonical objects; changes nothing
python -m app                      # the API, in demo mode
```

The mode is deployment configuration (`ADG_RUNTIME_MODE=production|demo`), never a switch in
the UI. Demo Mode is the runtime and safety boundary
([`docs/architecture/demo-mode.md`](docs/architecture/demo-mode.md)); Genesee Press is the
dataset it runs on ([`docs/demo/genesee-press.md`](docs/demo/genesee-press.md)). A free public
demo staging deployment of it (Cloudflare Workers Free + Render Free + Supabase PostgreSQL, no
Cloudflare Access) is described in [`docs/deployment/cloudflare-demo.md`](docs/deployment/cloudflare-demo.md).

Two documents are the place to start:
[`docs/operations/mvp-runbook.md`](docs/operations/mvp-runbook.md) to get it running, and
[`docs/architecture/mvp-capabilities.md`](docs/architecture/mvp-capabilities.md) for what it
answers and — just as deliberately — what it does not.

Before deploying it anywhere that matters, read
[`docs/release/known-limitations.md`](docs/release/known-limitations.md), and in particular
§1: the NTFS collector has been validated against real volumes and against Windows' own
access check, and **the Active Directory and SMB collectors have never met a real domain.**
[`docs/release/release-readiness.md`](docs/release/release-readiness.md) says what that means
for a first installation.

## Prerequisites (full product)

The synthetic demo needs none of the Windows tooling below; see [Run the demo](#run-the-demo).

| Tool | Version | Notes |
| --- | --- | --- |
| Windows | 10/11 or Server 2019+ | Collectors require Windows. |
| PowerShell | 7.x | `pwsh`. Windows PowerShell 5.1 only where a module requires it. |
| Python | 3.11+ | Backend. |
| Node.js | 20.9+ | Frontend. |
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

# Demo Mode: the synthetic Genesee Press estate (1,800 people, 33 file servers, three scans).
# Runs `python -m app.demo_mode reset`: the real API, ingestion, history and risk engine;
# touches no AD, SMB or collector. The API must run with ADG_RUNTIME_MODE=demo against a
# database named with 'demo' as a word. See docs\architecture\demo-mode.md.
.\scripts\demo-reset.ps1

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
