# ADG-NEXT16-RECONCILE-01 — The validated Next.js 16 integration, reconciled with current `main`

## 1. Verdict

**`READY_FOR_ADG_NEXT16_MERGE_WITH_LIMITATIONS`**

The validated Next.js 16 / React #418 integration (`aff70e8`) and current `main` (`32da1ae`,
which carries the ADG-DEMO-PUBLIC-MERGE-01 report) are reconciled by one merge commit,
**`1a9b1e8`**, on a branch created from `main`. The merge had no conflict and needed no
resolution: the two sides touch disjoint files.

* The reconciled tree differs from the validated `aff70e8` tree by **exactly one file**,
  `docs/demo/reports/ADG-DEMO-PUBLIC-MERGE-01-report.md`, byte-identical to `main`'s (§6).
  `frontend/`, `backend/`, `database/`, `collector/`, `.github/`, `README.md` and every other
  top-level entry are the same git objects as in `aff70e8`.
* The validated commits `af94e2e`, `501c136` and `aff70e8` are in the history **unchanged**
  (same object ids, authorship, messages and `-x` trailer), so CI run 80 and the integration
  report still describe commits on this line (§5).
* `main` is the merge's first parent, so `main` fast-forwards to this branch (§15).
* Every focused gate reproduced the integration report's counts exactly, and a #418 smoke on a
  clean-clone build of `1a9b1e8` saw **0 of 192 stream-delayed full loads** after a positive
  control saw 62 of 62 (§9). Because the application tree is proven identical, the
  integration's large-sample evidence (0 of 1,344) still applies.
* CI run 84 on `1a9b1e8` passed every job (§13).

The limitations are the integration's own, carried forward, not new defects (§16): hosted
timing simulated, Chromium only, no hosted deployment; plus one documentation staleness the
reconciliation deliberately did not fix (the README's Node.js floor) and a parallel replay
branch the owner should know about.

---

## 2. Current `main`

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| Starting `main` | **`32da1aecbc83a195f9db0849ec4dd5f652cc8bde`**. `git fetch` at the start: `origin/main` and `git ls-remote origin refs/heads/main` both `32da1ae`. It had not advanced past the expected SHA, so no further commit needed classifying |
| What it is | `744c2ef` plus two commits that add and then amend one file, `docs/demo/reports/ADG-DEMO-PUBLIC-MERGE-01-report.md` (§4) |
| CI on it | run [81](https://github.com/40ozs/adg/actions/runs/38774844746) on `main` at `32da1ae`: success, every job |
| After this task | unchanged. Nothing was merged or pushed to `main` |

---

## 3. Original integration

| | |
| --- | --- |
| Original base | `744c2efe89fb17f49cdfe640ebe5afd7d18253af` (the DEMO-PUBLIC-01 head, `main` when the integration started) |
| Integration branch | `claude/beautiful-knuth-3zruji` @ **`aff70e85f0e81ff5ad2241f7219d87237575b5f3`**, unchanged since its report (`git ls-remote`). Left untouched |
| Code head | **`501c1367e575efb51fedfe2353187024359acd52`** (Next.js 16.4.1) |
| Framework provenance | **`66ca20151e9bacfb96ad3cbff689d2b5cdcfa417`** (`frontend: Next.js 16.4.0 and React 19.3, webpack build`, author 40ozs) on `fix/ADG-NEXT16-REACT418-01`; also contained in `integration/ADG-NEXT16-UI-02` and `deploy/ADG-FREE-STAGING-01` |
| Its report | [`ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md), verdict `READY_FOR_ADG_NEXT16_POST_DEMO_MERGE_WITH_LIMITATIONS` |
| Its CI | run [80](https://github.com/40ozs/adg/actions/runs/38761041680) on `501c136` and run [82](https://github.com/40ozs/adg/actions/runs/38777706287) on `aff70e8`: success, every job |

The three integration commits, inspected by content, not subject:

| Commit | Author | Files | What it is |
| --- | --- | --- | --- |
| `af94e2e` | 40ozs (original author of `66ca201`) | `frontend/eslint.config.mjs`, `package.json`, `package-lock.json`, `tsconfig.json` | the framework change, `git cherry-pick -x 66ca201`, deploy-only context dropped; message ends `(cherry picked from commit 66ca20151e9bacfb96ad3cbff689d2b5cdcfa417)` |
| `501c136` | Claude | `frontend/package.json`, `package-lock.json` | Next.js 16.4.0 → 16.4.1, the October 14 security release |
| `aff70e8` | Claude | 15 files, all under `docs/demo/reports/` | the integration report and its evidence |

No commit in `744c2ef..aff70e8` touches `backend/`, `database/`, `collector/`, `README.md`,
`frontend/app`, `components`, `lib` or `tests`. None is from ADG-UI-UPDATE-01,
ADG-UI-COMPACT-IDENTITY-01, the deploy line or GRAPH (§7.3).

The integration's decision to take `66ca201` alone rather than either Next 16 branch head (its
§3.4: both heads carry the staging deploy line and the UI-UPDATE-01 / COMPACT-IDENTITY waves)
was re-checked against the repository and is preserved. Nothing found here contradicts it.

---

## 4. Divergence

```
744c2ef  Record CI run 75 and the repository's public visibility in DEMO-PUBLIC-01
├── 04390d7 → 32da1ae                 main: the ADG-DEMO-PUBLIC-MERGE-01 report
└── af94e2e → 501c136 → aff70e8       claude/beautiful-knuth-3zruji: the Next 16 integration
```

* `git merge-base 32da1ae aff70e8` = **`744c2ef`**, exactly the expected situation.
* `744c2ef..32da1ae`: 2 commits (`04390d7`, `32da1ae`), both Claude. `git diff --name-status
  744c2ef 32da1ae` is one line: `A docs/demo/reports/ADG-DEMO-PUBLIC-MERGE-01-report.md`
  (306 lines added, then 4 added and 2 removed recording CI run 77). **Documentation only.**
* `744c2ef..aff70e8`: the 3 commits in §3: 4 `frontend/` files modified, 15 files added under
  `docs/demo/reports/`.
* **Paths both sides changed: none.**

Why `aff70e8` no longer fast-forwards: the integration was cut from `744c2ef`. While it was
being validated, `main` advanced to `32da1ae`, the report the integration's own §2 describes
as "on `claude/intelligent-fermi-nxp2jw` … not on `main`". `32da1ae` is not an ancestor of
`aff70e8`, so `git merge --ff-only aff70e8` on `main` would refuse. The integration's §15
foresaw this: "If `main` first takes `claude/intelligent-fermi-nxp2jw` … the two touch disjoint
files. A merge is then needed and is mechanical; rerun CI on the merge."

---

## 5. Reconciliation method

**A `--no-ff` merge of `aff70e8` into a branch created from current `main`.**

| | |
| --- | --- |
| Branch | `claude/kind-goodall-9mlk79`, this session's only permitted push target. Intended name: **`integration/ADG-NEXT16-POST-DEMO-02`**. Renaming is a ref operation: `git push origin claude/kind-goodall-9mlk79:integration/ADG-NEXT16-POST-DEMO-02` |
| Created from | `origin/main` = `32da1ae` |
| Command | `git merge --no-ff origin/claude/beautiful-knuth-3zruji` |
| Result | **`1a9b1e8d9ed552ede07dc3047219b1735f12edd4`**, parents `32da1ae` (first) and `aff70e8`; tree `d9da6070f3fcc5fca9fed9c24c4a2a45c6b4d2fa` |
| Conflicts | none; nothing resolved by hand |
| Commits brought in | `af94e2e`, `501c136`, `aff70e8`, as the same objects |

### Why a merge rather than a replay

Both were tried before anything was committed, and both are conflict-free:

* write-free `git merge-tree --write-tree 32da1ae aff70e8` → tree `d9da607…`;
* `git cherry-pick -x af94e2e 501c136 aff70e8` onto `32da1ae` in a throwaway worktree →
  `af64ac4`, `9f87f1b`, `bab0049`, tree `d9da607…`. **The same tree.** The worktree was
  discarded.

The trees are identical, so the choice is only about history, and a merge is the more
auditable one here:

1. **The validated commits survive as themselves.** A replay creates three new commits that are
   patch-equivalent copies. CI run 80 ran on `501c136`, and the integration report names
   `af94e2e` and `501c136` throughout. After a replay those ids would not be in `main`'s
   history; after this merge they are.
2. **Provenance is untouched, not re-stamped.** `af94e2e` keeps its author (40ozs), its message
   and its single `(cherry picked from commit 66ca201…)` trailer. A replay would add a second
   `-x` trailer and a new committer date to every commit.
3. **Logical boundaries are preserved.** Framework, security release and report stay three
   commits. The merge commit adds no change of its own (§6), so nothing is squashed or hidden.
4. **It is what the integration report prescribed** for exactly this case (§4).
5. **`main` still fast-forwards.** `main` is the first parent, so `main`'s first-parent history
   reads "…`32da1ae` → reconciliation merge", and taking it needs no merge on `main`.

No history was rewritten and nothing was force-pushed. `claude/beautiful-knuth-3zruji`, the
original Next 16 branches, the GRAPH branches and the deploy branches are unchanged.

---

## 6. Tree-equivalence result

Full transcript:
[`ADG-NEXT16-RECONCILE-01-evidence/tree-equivalence.txt`](ADG-NEXT16-RECONCILE-01-evidence/tree-equivalence.txt).

**`git diff --name-status aff70e8 1a9b1e8`, every difference:**

```
A	docs/demo/reports/ADG-DEMO-PUBLIC-MERGE-01-report.md
```

* That file's blob in the reconciled tree is `6a1e7618…`, identical to `main`'s.
* `ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md` is `c518519b…` in both the reconciled tree and
  `aff70e8`.
* Top-level entries, object id in the reconciled tree / `aff70e8`:

| Entry | Same object as `aff70e8` |
| --- | --- |
| `frontend/` (`192949e351ce`) | **yes** |
| `backend/` (`b6220277c9b7`) | **yes** (also `main`'s) |
| `database/` (`1e5bd9a65420`) | **yes** (also `main`'s) |
| `collector/`, `docker/`, `scripts/`, `.github/`, `README.md`, `SECURITY.md`, Compose files, dotfiles | **yes** (also `main`'s) |
| `docs/` | no: `aff70e8`'s plus the one report above |

**No application-code difference exists**, so nothing needs explaining and the large-sample
evidence of the integration applies unchanged (§9).

The ending commit of this branch adds this report and
[`ADG-NEXT16-RECONCILE-01-evidence/`](ADG-NEXT16-RECONCILE-01-evidence/), both under
`docs/demo/reports/`. Against `aff70e8` the ending tree therefore differs by the public-demo
report plus this reconciliation's report material, and nothing else.

---

## 7. Framework state

Verified on the reconciled tree's files and installed packages, not taken from the earlier
report.

### 7.1 Versions and configuration

| | Reconciled tree |
| --- | --- |
| `next` | **16.4.1** (`package.json` `^16.4.1`; lockfile 16.4.1; installed 16.4.1) |
| `react`, `react-dom` | **19.3.0** (`^19.3.0`; lockfile and installed 19.3.0) |
| `@types/react`, `@types/react-dom` | 19.3.0 |
| React the App Router runs (bundled in `next`) | **`19.3.0-canary-278794d7-20261002`** (`node_modules/next/dist/compiled/react-dom`), the build the integration recorded. The production build's `4bd1b696-*.js` client chunk carries it |
| `eslint-config-next`, `@next/eslint-plugin-next`, `@next/env` | 16.4.1 |
| Build | `"build": "next build --webpack"`; the build prints **`▲ Next.js 16.4.1 (webpack)`**. Turbopack not used |
| ESLint | flat config: `eslint.config.mjs` imports `eslint-config-next/core-web-vitals` and `/typescript` directly; no `FlatCompat`. `@eslint/eslintrc` is not a direct dependency (only transitively, under `eslint` itself) |
| TypeScript | **`"jsx": "react-jsx"`**; `include` has `.next/dev/types/**/*.ts`; `exclude: ["node_modules"]` |
| `next.config.mjs` | `reactStrictMode: true`, nothing else; no `output: "standalone"`, no `experimental` |
| `npm audit` | 13 (2 critical, 8 high, 3 moderate), as the integration recorded; all test and lint tooling |

### 7.2 What is absent, as required

| Check | Result |
| --- | --- |
| `loading`, `error`, `global-error`, `not-found`, `default` files; `middleware`/`proxy` | none in `frontend/` |
| `Suspense` in `frontend/app`, `components`, `lib` | none. **No Suspense or loading boundary** |
| Deploy line: Cloudflare Worker, `wrangler`/`miniflare`/`workerd`, `@cloudflare/*`, `render/`, `Dockerfile.render`, `origin_gate.py`, staging workflow, `.edge-assets` | none in the tree or the lockfile; `.github/workflows/` holds only `ci.yml`, identical to `main`'s |
| ADG-UI-UPDATE-01 / COMPACT-IDENTITY-01: `principal/[key]`, `SortableTable`, Users/Groups directories, shortest-chain API, SID truncation | none |

### 7.3 Excluded lanes, by ancestry

For each parallel head: is it an ancestor of `1a9b1e8`, and how many of its commits (beyond
`main`) does `1a9b1e8` contain?

| Head | Ancestor | Its commits in `1a9b1e8` |
| --- | --- | --- |
| `feature/ADG-GRAPH-01A-domain-model`, `-01B-relationship-derivation`, `-01C-path-engine`, `-01C-path-engine-claude` | no | 0 |
| `fix/ADG-NEXT16-REACT418-01`, `integration/ADG-NEXT16-UI-02` | no | 0 (`af94e2e` is the `-x` cherry-pick of `66ca201`, not `66ca201` itself) |
| `fix/ADG-UI-COMPACT-IDENTITY-01`, `integration/ADG-UI-EDGE-01` | no | 0 |
| `deploy/ADG-FREE-STAGING-01`, `deploy/ADG-FREE-EDGE-SSR-03` | no | 0 |

`git log origin/main..1a9b1e8` lists exactly `1a9b1e8`, `aff70e8`, `501c136` and `af94e2e`.

---

## 8. Demo preservation

All local gates ran on `1a9b1e8` (Python 3.13.16, Node 22.22.0, npm 10.9.4, PostgreSQL 16.15).
The browser runs used a **clean clone of the pushed branch** at `1a9b1e8` (`npm ci`: 466
packages; `npm run build`: `▲ Next.js 16.4.1 (webpack)`, 39 routes; clone tree still clean), with
the API in demo mode over a freshly reset database.

**Demo reset:** 300 runs (297 succeeded, 2 failed, 1 partial), 41,756 observations, probes 23/23,
9,386 open findings, fingerprint **`228b9dce…f9efd0`**, equal to DEMO-PUBLIC-01 and the
integration. `app.demo_mode verify` passed.

| Demo | Set | Result | Prior |
| --- | --- | --- | --- |
| **DEMO-01**, SYSTEM correctness | backend `test_system_explanation_regression` + `test_system_mapping` + `test_resolver` | **145 passed** | 145 |
| | UI `system-mapping`, `explanation`, `explanation-views`, `identity` | **171 passed** | 171 |
| | `TestTheSystemExplanation`, `test_system_mapping_api` (PostgreSQL) | in the 58 below | — |
| | browser: DEMO-01 UI script (SYSTEM mapping states, unresolved-identity banner) | **15/15** | 15/15 |
| **DEMO-02**, Fatima → Apollo | backend `test_investigation` + `test_unidentified_trustees` | **102 passed** | 102 |
| | UI `investigation`, `investigation-page`, `demo-guide`, `explanation-page`, `explanation-contract` | **180 passed** | 180 |
| | `TestTheAccessInvestigation` (PostgreSQL) | in the 58 below | — |
| | browser: ADG-DEMO-02 walkthrough (granted, `Modify`, `at_least`; NTFS and share routes; hop provenance by keyboard; missing evidence; unresolved SID; SYSTEM over a share never "No access"; 390 px; console) | **29/29** | 29/29 |
| **DEMO-03**, coverage, timeout, recovery | backend `tests/domain/test_coverage.py` | **26 passed** | 26 |
| | UI `coverage`, `compare-page`, `risks-page`, `risks` | **85 passed** | 85 |
| | `test_collection_coverage`, `test_comparison_bounds`, `TestTheCoverageAndRecoveryDemo` (PostgreSQL) | in the 58 below | — |
| | browser: ADG-DEMO-03 walkthrough (coverage beside totals, unavailable NM-FS-03 held, drill timed out → incomplete → narrowed, complete and recovered, Diagnostics by keyboard, 390 px, console) | **39/39** | 39/39 |
| all | README representative unit tests | **229 passed** | 229 |
| all | README representative PostgreSQL tests (two full Null Meridian resets) | **58 passed** in 2m07s | 58 |
| all | README frontend set (`system-mapping`, `investigation-page`, `risks-page`, `compare-page`, `coverage`) | **121 passed** | 121 |
| all | the integration's 12-file preservation set | **269 passed** | 269 |
| **Public demo** | browser: DEMO-PUBLIC-01 documentation walkthrough (every label, route and number the README and demo guide cite) | **39/39** | 39/39 |

Browser results by check:
[`browser-runs.json`](ADG-NEXT16-RECONCILE-01-evidence/browser-runs.json). The only console
entry in any run is the pre-existing `/favicon.ico` 404, which every earlier walkthrough
records. While these ran, the API logged 70 `changes.compare` telemetry lines (25 `timed_out`,
23 `incomplete`, 22 `complete`, 6 of them `recovered`) and **no `ERROR` line**.

---

## 9. React #418

### 9.1 Prior large-sample evidence: **applicable**

The integration's gate measured the application tree that is now `frontend/` here, the same git
object (`192949e351ce`), served by the same backend tree (`b6220277c9b7`). The reconciliation
added one Markdown file outside `frontend/`. The integration's results therefore stand for this
tree. They were **not** re-generated here and are cited from
[its report §7](ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md#7-react-418) and
[`hydration-runs.json`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-runs.json):

* positive control: #418 on **62/62** full loads on both builds;
* old `main` (Next 15.5.25): #418 on **67 of 576** stream-delayed full loads (11.6%);
* integrated Next 16.4.1: **0 of 1,344** full loads (**0/960** stream-delayed, **0/384**
  direct) and **0/266** client-side transitions;
* zero other application console errors.

### 9.2 Smoke on the reconciled build (new)

The integration's unchanged `hydration-gate.script.js` and `stream-delay-proxy.script.js`,
against the clean-clone build of `1a9b1e8`, Chromium 141.0.7390.37 (Playwright 1.56.1). One gate
round is 17 reviewer routes loaded fresh and reloaded 3 times, client navigation along the
reviewer's path, keyboard-only interaction and 390 px loads. Every run is recorded in
[`hydration-smoke-runs.json`](ADG-NEXT16-RECONCILE-01-evidence/hydration-smoke-runs.json).

| Run | Condition | Full loads | Client transitions | Loads with #418 | Hydration family (#419–#429) | Other app errors | Failed steps | 390 px overflow |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R0, control | direct + foreign node | 62 | 19 | **62** (expected) | 0 | 0 | 0 | 0 |
| R1 | 40 ms stream delay | 96 | 19 | **0** | 0 | 0 | 0 | 0 |
| R2 | 100 ms stream delay | 96 | 19 | **0** | 0 | 0 | 0 | 0 |

* **The detector works on this build:** the control reports #418 on every full load, exactly as
  on both builds in the integration.
* **0 of 192 stream-delayed full loads and 0 of 38 client transitions** on the reconciled build:
  well beyond the "10 slow-stream loads" asked for. At `main`'s Next 15 baseline rate (11.6%),
  192 clean loads would occur by chance with probability about 0.884¹⁹² ≈ 5 × 10⁻¹¹.
* The only non-#418 console entry is the `/favicon.ico` 404, once per run.

This smoke confirms the build behaves as the measured one did; it is not presented as a new
large sample.

---

## 10. Contracts

| | |
| --- | --- |
| Explanation contract version | **`1.1`**: `CONTRACT_VERSION: Final = "1.1"` in `backend/app/api/caching.py`; `"const": "1.1"` in all three derived schemas |
| OpenAPI | `python -m app.contracts.openapi` rewrote `docs/contracts/v1/openapi.json` and `openapi.demo.json` |
| Derived | `python -m app.contracts.derived` rewrote the three derived schemas |
| Drift | **none.** All five files were rewritten (new modification times), their SHA-256 sums equal the committed files', and `git status` stayed clean |
| Backend contract tests | `tests/contracts`: **611 passed, 109 skipped** (equal to DEMO-PUBLIC-01 and ADG-DEMO-PUBLIC-MERGE-01) |
| Frontend contract test | `tests/contracts.test.ts`: **465 passed** |

---

## 11. Migrations

| | |
| --- | --- |
| `alembic heads` | **one head: `0018_risk_evaluation_basis`** |
| Latest revision | `0018_risk_evaluation_basis`, `down_revision = "0017_deployment_marker"`; history linear from `0014_merge_alerts_and_reviews` through `0015`–`0017` |
| GRAPH | **not introduced.** No `0018_graph_relationships` (or any other new revision) in `database/migrations/versions/`. The only file with "graph" in its name is `0002_ad_graph.py`, present since long before this line (on `1ab01a2`, last changed 2026-09-14) |
| `database/` | the same tree object as `main` and `aff70e8` |

---

## 12. Tests and build

All on `1a9b1e8` in this container unless marked.

### 12.1 Frontend

| Gate | Result |
| --- | --- |
| `npm run lint` | **passed** (ESLint 9, flat `eslint-config-next` 16.4.1) |
| `npm run typecheck` | **passed** |
| `npm run test` (full) | **1,361 passed** (38 files), equal to the integration |
| `npm run build` | **passed**, `▲ Next.js 16.4.1 (webpack)`, 39 routes; `/demo`, `/access/investigate`, `/risks`, `/changes/compare` server-rendered on demand (`ƒ`). Again in the clean clone (§8). `git status` clean after both |
| Focused sets | §8 and §10: 171, 180, 85, 121, 269, 465, every one equal to its prior count |

### 12.2 Backend

| Gate | Result |
| --- | --- |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** (412 files) |
| `mypy app tests` | **passed** (411 source files) |
| `pytest -m "not smoke"` (full unit suite, includes `tests/contracts`) | **6,009 passed, 124 skipped, 1,273 deselected** in 1m41s, equal to the integration, DEMO-PUBLIC-01 and ADG-DEMO-03 |
| Focused unit sets | §8: 229, 145, 102, 26 |

### 12.3 PostgreSQL

| Gate | Result |
| --- | --- |
| Demo reset and verify | §8: fingerprint `228b9dce…f9efd0`, verify passed |
| README representative PostgreSQL tests | **58 passed** in 2m07s (18:25:07–18:27:16 UTC) |
| `pytest -m smoke` (full database suite) | in CI run 84 on `1a9b1e8` (§13). Not run locally: the backend is the same tree object CI runs 77, 80, 81 and 82 already passed it on |

After every gate, `git status` showed only this report's new files.

---

## 13. CI

GitHub Actions (`.github/workflows/ci.yml`, runs on every push).

**Run 84** on the reconciliation merge `1a9b1e8`
([run](https://github.com/40ozs/adg/actions/runs/38784880690)): **every job passed.**

| Job | Result |
| --- | --- |
| Frontend (lint, types, tests, build) | **success** (18:21:07–18:22:13 UTC) |
| Collector (Pester, `windows-latest`) | **success** (18:21:07–18:22:21 UTC) |
| Docker Compose configuration | **success** (18:21:07–18:21:14 UTC) |
| Backend: lint, format, type check | **success**: ruff "All checks passed!", "412 files already formatted", mypy "no issues found in 411 source files" |
| Backend: unit tests | **success**: **6,121 passed, 12 skipped, 1,273 deselected** (18:21:52–18:24:47 UTC) |
| Backend: database tests (PostgreSQL 16) | **success**: **1,272 passed, 1 xfailed**, 6,133 deselected, in 28m37s (18:24:47–18:53:29 UTC) |

The backend counts equal run 80's on `501c136` exactly. The xfail is the pre-existing one in
`test_access_performance.py`. CI skips 12 unit tests where this container skips 124 because
GitHub's runner has PowerShell 7; the collected set is the same (6,121 + 12 = 6,009 + 124).
[`ci-run-84-backend.txt`](ADG-NEXT16-RECONCILE-01-evidence/ci-run-84-backend.txt) holds the
backend job's result lines, verbatim from the 957-line log.

The ending commit adds only this report and its evidence under `docs/demo/reports/`; its own
run is reported in the final response, as a report cannot name its own commit.

For reference: run 80 (`501c136`), run 82 (`aff70e8`) and run 81 (`main` at `32da1ae`), all
success on every job.

---

## 14. Reports

Both durable reports are in the reconciled tree, unchanged:

| Report | In `1a9b1e8` | Blob | Same as |
| --- | --- | --- | --- |
| [`ADG-DEMO-PUBLIC-MERGE-01-report.md`](ADG-DEMO-PUBLIC-MERGE-01-report.md) | yes | `6a1e7618…` | `main` (`32da1ae`) |
| [`ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md) and its 14-file evidence directory | yes | `c518519b…` | `aff70e8` |

The ADG-DEMO-01, -02, -03 and DEMO-PUBLIC-01 reports and their evidence are unchanged too. This
report and `ADG-NEXT16-RECONCILE-01-evidence/` are added by the ending commit.

Two statements in those reports are now historical, not wrong. They were left as written,
because reports record their own moment:

* ADG-DEMO-PUBLIC-MERGE-01 §9 lists React #418 as a known limitation and Next 16 as unmerged.
  Once this branch reaches `main`, that limitation is resolved by this line.
* ADG-NEXT16-POST-DEMO-INTEGRATION-01 §2 says the public-demo report is "not on `main`". It is
  now (`32da1ae`), which is why this reconciliation exists.

---

## 15. Handoff

**Merge `claude/kind-goodall-9mlk79` into `main` at its ending SHA**: the commit that adds this
report, named in the final response. Its code is the merge `1a9b1e8`, and the report commit
changes only `docs/demo/reports/`.

* `main` (`32da1ae`) is an ancestor of the branch, so this is a **fast-forward**:
  `git merge --ff-only <ending SHA>` on `main`. No new merge commit, no rebase.
* **Before merging, check:**
  1. `origin/main` is still `32da1ae` (or at least still an ancestor of the ending SHA);
  2. CI on the ending SHA has passed every job (it is documentation-only over `1a9b1e8`,
     whose run 84 passed);
  3. `git diff --name-status aff70e8 <ending SHA>` lists only
     `docs/demo/reports/ADG-DEMO-PUBLIC-MERGE-01-report.md`, this report and
     `ADG-NEXT16-RECONCILE-01-evidence/`.
* **After merging:** a fresh `npm ci` in `frontend/` for anyone with a Next 15 `node_modules`.
  No migration, API or contract change, and no environment variable.
* `claude/beautiful-knuth-3zruji` becomes fully contained in `main` by that merge. It can be
  retired afterwards; it was not deleted here.

---

## 16. Limitations

### 16.1 Carried forward from the integration (unchanged)

1. **Hosted timing is simulated, not measured.** The stream-delay proxy recreates the race on
   one machine; it is not the Worker and Render path.
2. **Chromium only.** Firefox and Safari were not run.
3. **No hosted deployment** of this line. The existing staging (`deploy/ADG-FREE-STAGING-01`)
   runs a different tree and was not touched.
4. ADG-UI-UPDATE-01, ADG-UI-COMPACT-IDENTITY-01, the deploy line and GRAPH remain deliberately
   outside this line. ADG-DEMO-01 to -03's accepted product limitations are unchanged.

### 16.2 This reconciliation

1. **Focused, not full, browser repetition.** One run of each walkthrough and 192 stream-delayed
   gate loads, as this task specified. The application tree is proven identical, so the
   integration's 15 walkthrough runs and 1,344-load gate are the large samples.
2. **Not run locally:** Collector Pester tests, `docker compose config` and the full PostgreSQL
   suite. CI run 84 ran all three on `1a9b1e8`. The Docker Compose demo path remains unrun.
3. **README Node.js floor is stale (not fixed here).** `next` 16.4.1 declares
   `engines.node: ">=20.9.0"`, but `README.md` says "Node.js 20 or later" (line 240) and "20+"
   (line 456). The integration did not change the README, and this reconciliation adds no
   change beyond the validated tree. CI and the frontend Dockerfile use Node 22, so nothing
   breaks. It is a one-line follow-up.
4. **A parallel replay exists.** `claude/adoring-lamport-1e22j9` (`0a65d91`, another session,
   CI run 83 success) independently re-ported `66ca201` onto `32da1ae` as `c054159` and
   `3524a02`, then made the README change in item 3. Its final `frontend/` tree equals this
   one's (`192949e351ce`). But its commits are not the validated ones: its 16.4.0 lockfile
   differs from `af94e2e`'s (`sharp` 0.35.4 where `af94e2e` resolves 0.35.5; the two converge
   at 16.4.1), `c054159` is authored by Claude rather
   than 40ozs, and it carries neither the integration report nor its evidence. It was
   inspected, not used and not modified. Only one of the two lines should reach `main`; this
   one keeps the validated history.

---

## Recommended next step

1. Fast-forward `main` to this branch's ending SHA once its CI has passed (§15).
2. Fix the README's Node.js floor to 20.9 in a one-line follow-up (§16.2, item 3), and retire
   `claude/adoring-lamport-1e22j9` and `claude/beautiful-knuth-3zruji` once `main` contains
   this line.
3. The separate follow-ups the integration listed still stand: ADG-UI-UPDATE-01 /
   COMPACT-IDENTITY-01 against this line, and the deploy line or staging moved to 16.4.1.
