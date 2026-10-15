# ADG-NEXT16-RECONCILE-01 — The validated Next.js 16 integration, reconciled onto the current `main`

## 1. Verdict

**`READY_FOR_ADG_NEXT16_MERGE_WITH_LIMITATIONS`**

The validated Next.js 16 / React #418 integration (`claude/beautiful-knuth-3zruji` @ `aff70e8`)
is reconciled onto the current `main` (`32da1ae`) by one conflict-free merge commit, `1309146`.
Both durable reports are in the reconciled tree.

* **Tree equivalence.** The reconciled tree differs from the validated `aff70e8` tree in exactly
  one path: the public-demo merge report, which `main` added. Its blob is `main`'s. Every
  application subtree (`frontend`, `backend`, `database`, `collector`, `scripts`, `docker`,
  `.github`, `docs/contracts`) is hash-identical to `aff70e8`'s (§6).
* **History.** The three integration commits keep their SHAs (`af94e2e`, `501c136`,
  `aff70e8`), their authorship and their `(cherry picked from commit 66ca201…)` trailer. No
  replay, rebase, squash or force-push. `main` is an ancestor of the result, so the merge into
  `main` is a fast-forward (§5).
* **Behavior.** Every gate on the reconciled tree matches the count the earlier reports
  recorded for the same set (§8, §10, §12). The four reviewer walkthroughs passed **122 of 122**
  checks against a clean-clone build (§8).
* **React #418.** The integration report's large-sample evidence (0 of 1,344 full loads) still
  applies, because the application tree is identical to the one it measured. The smoke here
  agrees: the positive control saw #418 on 62 of 62 loads. Under the 40 ms stream delay, the
  reconciled build had #418 on **0 of 96** full loads, and current `main` (Next 15.5.25) on
  **14 of 96** in the same container (§9).
* **CI.** Run 87 passed every job on `1309146` (§13).

The limitations are carried over unchanged from the integration (its §14; listed at the end of
this report). The reconciliation added none:

* the hosted timing was simulated on one machine;
* only Chromium was run;
* this line has no hosted deployment;
* ADG-UI-UPDATE-01, ADG-UI-COMPACT-IDENTITY-01, the deploy line and GRAPH stay out.

---

## 2. Current `main`

| | |
| --- | --- |
| Repository | `40ozs/adg` (public; visibility not changed) |
| Starting `main` | **`32da1aecbc83a195f9db0849ec4dd5f652cc8bde`**. `git fetch` of every head at the start: `origin/main` equals the expected SHA and has **not advanced further**. This container's local `main` was stale at `cc13ad2`. It was not used, and not changed |
| What `main` holds over `744c2ef` | `04390d7` (adds `docs/demo/reports/ADG-DEMO-PUBLIC-MERGE-01-report.md`) and `32da1ae` (edits that file alone: CI run 77). `git diff --name-status 744c2ef 32da1ae` lists **one file, under `docs/`**. Both are **documentation only**, judged by their trees, not their subjects |
| CI on `main` | run [81](https://github.com/40ozs/adg/actions/runs/38774844746) on `32da1ae`, triggered by the push that moved `main` there (started 17:08:34 UTC): **success**. Run [77](https://github.com/40ozs/adg/actions/runs/38751393080) passed on `744c2ef`, the same code |
| `main` after this prompt | unchanged; nothing was merged or pushed to it |

---

## 3. Original integration

| | |
| --- | --- |
| Original base | `744c2efe89fb17f49cdfe640ebe5afd7d18253af` (the public-demo `main` the integration fetched at its start) |
| Integration head | **`aff70e85f0e81ff5ad2241f7219d87237575b5f3`** on `claude/beautiful-knuth-3zruji` (unchanged since its report) |
| Code head | **`501c1367e575efb51fedfe2353187024359acd52`**: Next.js 16.4.1. CI run [80](https://github.com/40ozs/adg/actions/runs/38761041680) passed every job on it |
| Framework provenance | **`66ca20151e9bacfb96ad3cbff689d2b5cdcfa417`** on `fix/ADG-NEXT16-REACT418-01`, cherry-picked as `af94e2e` (author 40ozs, kept), with only the framework change kept. Its message carries `(cherry picked from commit 66ca20151e9bacfb96ad3cbff689d2b5cdcfa417)` |
| Integration report | [`ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md): verdict `READY_FOR_ADG_NEXT16_POST_DEMO_MERGE_WITH_LIMITATIONS` |

`744c2ef..aff70e8`, checked file by file, not by subject:

| SHA | Author | Files | What |
| --- | --- | --- | --- |
| `af94e2e` | 40ozs | `frontend/eslint.config.mjs`, `package.json`, `package-lock.json`, `tsconfig.json` | the framework change from `66ca201`: Next 16.4.0, React 19.3, `next build --webpack`, flat ESLint, `jsx: react-jsx` |
| `501c136` | Claude | `frontend/package.json`, `package-lock.json` | Next.js 16.4.1 (12 lockfile entries) |
| `aff70e8` | Claude | 15 files under `docs/demo/reports/` | the integration report and its evidence |

No other commit. No backend, database, contract, application-code or deploy file.

The decision to integrate only `66ca201`, and not either Next 16 branch head, is **preserved**.
The repository gives no reason to revisit it. Every excluded head is still outside the
reconciled history (§6.3).

---

## 4. Divergence

```
744c2ef ─┬─ 04390d7 ─ 32da1ae                      main (report only)
         └─ af94e2e ─ 501c136 ─ aff70e8            claude/beautiful-knuth-3zruji
```

* `git merge-base origin/main aff70e8` = `744c2ef`: **exactly the expected shape**.
* The integration fetched `main` at `744c2ef`. Its report (§2) records `32da1ae` as the
  public-demo merge report's branch, `claude/intelligent-fermi-nxp2jw`, **not on `main`**.
* `main` was then fast-forwarded to that branch, the optional step 3 of the public-demo merge
  report's recommendations. CI run 81 on `main` at `32da1ae` dates that push: it started at
  17:08:34 UTC. The integration's report commit `aff70e8` came 20 minutes later, at 17:28:57
  UTC, still on the `744c2ef` base.
* `git merge-base --is-ancestor origin/main aff70e8` is **false**: `32da1ae` is not in the
  integration's history. A fast-forward of `main` to `aff70e8` is therefore impossible, and
  merging `aff70e8` as it stands would leave out the public-demo merge report.
* The two sides change **disjoint files**: one report on `main`'s side; four `frontend/` files
  and the integration's report on the other.

---

## 5. Reconciliation method

**A merge, not a replay.** On `claude/gallant-turing-aj7rf1`, created from `origin/main`
(`32da1ae`):

```
git merge-tree --write-tree origin/main aff70e8   # trial, write-free: tree d9da607, no conflict
git merge --no-ff aff70e85f0e81ff5ad2241f7219d87237575b5f3
```

| | |
| --- | --- |
| Merge commit | **`1309146587cf0c97d49e334a8054bcaccfd5ec57`** |
| Parents | `32da1ae` (first: `main`), `aff70e8` (second: the integration) |
| Tree | `d9da6070f3fcc5fca9fed9c24c4a2a45c6b4d2fa`, the tree the trial merge predicted |
| Conflicts | none; no file resolved by hand |
| Commits it brings into `main`'s history | `af94e2e`, `501c136`, `aff70e8`, **unchanged**: same SHAs, authors, messages and trailers |

**Why a merge rather than the preferred cherry-pick replay:**

1. **Provenance stays verifiable.** The integration report and its evidence name `af94e2e`,
   `501c136` and CI run 80 *on `501c136`*. A replay would make new commits with new SHAs. `main`
   would then carry a report naming commits that are not its ancestors, and CI evidence for a
   commit outside its history. The merge keeps the exact validated commits.
2. **The integration report prescribes it.** Its §15 says: if `main` first takes
   `claude/intelligent-fermi-nxp2jw`, "the two touch disjoint files. A merge is then needed and is
   mechanical; rerun CI on the merge."
3. **It needs no resolution.** The two sides touch disjoint files, so there is nothing for a
   replay to re-resolve. A cherry-pick would add a second `(cherry picked from …)` layer to
   `af94e2e` for no gain.
4. **`main` still fast-forwards.** The branch starts at `main`, so `main` is an ancestor of the
   result. Parent order keeps `main`'s line first, so `git log --first-parent` reads as `main`,
   then this merge, then this report's commit.

Nothing was squashed, rebased or force-pushed. Neither durable report was dropped.

**Branch.** `claude/gallant-turing-aj7rf1` is this session's only permitted push target. The
intended name is **`integration/ADG-NEXT16-POST-DEMO-02`**. Creating it is a ref operation that
changes no commit:
`git push origin claude/gallant-turing-aj7rf1:integration/ADG-NEXT16-POST-DEMO-02`.
`claude/beautiful-knuth-3zruji`, the original Next 16 branches, the GRAPH branches and the deploy
branches are untouched.

---

## 6. Tree-equivalence result

The full proof, every command with its output, is
[`tree-equivalence.txt`](ADG-NEXT16-RECONCILE-01-evidence/tree-equivalence.txt).

### 6.1 Validated `aff70e8` vs reconciled `1309146`

```
$ git diff --name-status aff70e8 1309146
A	docs/demo/reports/ADG-DEMO-PUBLIC-MERGE-01-report.md
```

**That is the only difference.** The added file is the public-demo merge report, blob
`6a1e761…`, identical to `main`'s. No application code differs, so none needs explaining.

| Path | Reconciled vs `aff70e8` |
| --- | --- |
| `frontend` (`192949e…`), `backend` (`b622027…`), `database` (`1e5bd9a…`) | **equal** |
| `collector`, `scripts`, `docker`, `.github` | **equal** |
| `docs/contracts`, `docs/architecture`, `docs/demo/README.md`, `README.md` | **equal** |
| `docker-compose.yml`, `docker-compose.demo.yml`, `.env.example` | **equal** |
| `ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md` (`c518519…`) and its evidence directory (`1e990d8…`) | **equal** |

`git diff --stat 501c136 1309146 -- . ':!docs'` is empty: outside `docs/`, the reconciled tree is
the code head CI run 80 passed.

### 6.2 Current `main` vs reconciled `1309146`

Outside `docs/`, exactly the integration's four framework files differ from `main`:
`frontend/eslint.config.mjs`, `package.json`, `package-lock.json`, `tsconfig.json`. Under
`docs/`, the only difference is the integration's report and its evidence directory (15 files).

### 6.3 Excluded lanes

None of these heads is an ancestor of `1309146`. The count is the head's commits not in the
reconciled history, the same as the public-demo merge report's §9 for each:

| Head | Not contained |
| --- | --- |
| `fix/ADG-NEXT16-REACT418-01` (the framework source; `66ca201` itself is integrated only as `af94e2e`) | 19 |
| `integration/ADG-NEXT16-UI-02` (UI-UPDATE-01 + COMPACT-IDENTITY-01) | 26 |
| `fix/ADG-UI-COMPACT-IDENTITY-01` | 20 |
| `integration/ADG-UI-EDGE-01` | 17 |
| `deploy/ADG-FREE-STAGING-01`, `deploy/ADG-FREE-EDGE-SSR-03` | 26, 9 |
| `feature/ADG-GRAPH-01A`, `-01B`, `-01C-path-engine`, `-01C-path-engine-claude` | 28, 31, 39, 35 |
| `claude/adoring-cannon-7gh2pf`, `claude/compassionate-hamilton-3sh5db`, `claude/wizardly-gates-6yjdxp` | 38, 32, 1 |

The only remote head the merge newly brings under `main` is `claude/beautiful-knuth-3zruji`.

---

## 7. Framework state

Read from the reconciled files and the installed packages, not from the earlier report:

| | Reconciled (`1309146`) |
| --- | --- |
| `next` | `^16.4.1` in `package.json`; **16.4.1** in the lockfile and in `node_modules` after `npm ci` |
| `react`, `react-dom` | `^19.3.0`; **19.3.0** installed |
| `@types/react`, `@types/react-dom` | `^19.3.0`; 19.3.0 |
| React the App Router runs (bundled in `next`) | **`19.3.0-canary-278794d7-20261002`**: the version string in `next/dist/compiled/react` and `react-dom`, as the integration recorded |
| Build | `"build": "next build --webpack"`. The build prints `▲ Next.js 16.4.1 (webpack)`: 39 routes, all `ƒ`. **Turbopack is not used** |
| ESLint | `eslint.config.mjs` imports `eslint-config-next/core-web-vitals` and `/typescript` (16.4.1) as flat configs. No `FlatCompat`. `@eslint/eslintrc` is not a direct dependency (it is still installed, as ESLint's own dependency) |
| TypeScript | `jsx: react-jsx`; `.next/dev/types/**/*.ts` included; `exclude: ["node_modules"]` |
| `next.config.mjs` | `reactStrictMode: true`, `env: {}`. No `output`, `experimental` or deploy setting. Equal to `main` |
| Suspense or loading boundary | **none**. No `Suspense` in `app`, `components` or `lib`. No `loading`, `error`, `not-found` or `default` file. No `middleware` or `proxy` file |
| UI-UPDATE / deploy changes | **none** (§6.3). No Worker, Render, `cf:*` script, `@cloudflare/workers-types` or standalone output |

---

## 8. Demo preservation

All gates ran on `1309146`. The browser runs targeted a production build from a **clean clone**
of the pushed branch, with the API in demo mode over a freshly reset Null Meridian database.

### 8.1 Demo reset

`python -m app.demo_mode reset` produced 300 runs (297 succeeded, 2 failed, 1 partial), 41,756
observations, probes 23/23 and 9,386 open findings, with fingerprint
**`228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`**. That equals DEMO-PUBLIC-01
and the integration. `python -m app.demo_mode verify` returned `ok: true`.

### 8.2 Focused regression sets

| Demo | Set | Result | Earlier count |
| --- | --- | --- | --- |
| all | README representative unit tests | **229 passed** | 229 |
| all | README representative PostgreSQL tests (`test_system_mapping_api`, `test_collection_coverage`, `test_comparison_bounds`, `TestTheSystemExplanation`, `TestTheAccessInvestigation`, `TestTheCoverageAndRecoveryDemo`; the three Null Meridian classes each run a real reset) | **58 passed** in 2m20s | 58 |
| all | README frontend set (`system-mapping`, `investigation-page`, `risks-page`, `compare-page`, `coverage`) | **121 passed** (5 files) | 121 |
| **01**, SYSTEM correctness | `test_system_explanation_regression` + `test_system_mapping` + `test_resolver` | **145 passed** | 145 |
| 01 | UI: `system-mapping`, `explanation`, `explanation-views`, `identity` | **171 passed** | 171 |
| **02**, Fatima → Apollo | `test_investigation` + `test_unidentified_trustees` | **102 passed** | 102 |
| 02 | UI: `investigation`, `investigation-page`, `demo-guide`, `explanation-page`, `explanation-contract` | **180 passed** | 180 |
| **03**, coverage | `tests/domain/test_coverage.py` | **26 passed** | 26 |
| 03, comparison and recovery | UI: `coverage`, `compare-page`, `risks-page`, `risks` | **85 passed** | 85 |
| 01–03 | the integration's 12-file UI set (`identity`, `views`, `pages`, `system-mapping`, `investigation`, `investigation-page`, `demo-guide`, `risks`, `risks-page`, `coverage`, `compare-page`, `changes`) | **269 passed** | 269 |

### 8.3 Browser walkthroughs

Each script ran **unchanged** from its evidence directory:
[`browser-runs.json`](ADG-NEXT16-RECONCILE-01-evidence/browser-runs.json) lists every check.

| Walkthrough | Result | What it proves on the reconciled build |
| --- | --- | --- |
| **DEMO-01 UI** | **15/15** | SYSTEM over `\\NM-FS-01\Shared`: mapping `ambiguous`, certainty `uncertain`, "not a finding of no access", no "No access" verdict, path `remote_smb`. SYSTEM locally: mapping `confirmed`, certainty `at_least`, path `local`. Unresolved SID `…-1108`: the `role=alert` banner names the key and the reason, and the heading shows `NULLMERIDIAN\lhansen unresolved` with no invented name |
| **ADG-DEMO-02** | **29/29** | Granted, `Modify`, `at_least`; the direct NTFS route and the share route through ENG-Mobile; hop evidence reached by Tab and opened by Enter; two missing-evidence items that do not affect the conclusion; the unresolved SID's page; SYSTEM over a share and locally; no horizontal scroll at 390 px |
| **ADG-DEMO-03** | **39/39** | Coverage beside the totals (98 of 100 scopes); NM-FS-03's 189 findings held, not zeroed; the investigation's coverage section; the drill: timed out (471 ms), then the retry incomplete at a named boundary (533 ms), then narrowed, complete and recovered (933 ms); Diagnostics by keyboard; no horizontal scroll at 390 px on five surfaces |
| **DEMO-PUBLIC-01** (public demo) | **39/39** | Every label, route and number the README and the demo guide cite: the demo pills, Fatima → Apollo W1.1–W1.6, the risk counts (3 / 8 / 1,957 / 423 / 6,995), the drill and its 221 differences |
| **Total** | **122/122** | |

* **Console:** each walkthrough's "no console errors from the application" check passed. The
  only console entry is the pre-existing `/favicon.ico` 404.
* **API:** the API log for the whole browser session has **no `ERROR` line and no 5xx**. It
  wrote 70 `changes.compare` lines (25 `timed_out`, 23 `incomplete`, 22 `complete`, 6 of them
  `recovered`).

---

## 9. React #418

### 9.1 The prior large-sample evidence still applies

The integration's gate measured the application built from the `frontend` tree `192949e…` and
its lockfile. The reconciled `frontend` tree is that same object (§6.1), and so are the
backend, database and contracts. The only added file is a Markdown report that no build reads.
So the integration report's §7 results apply to the reconciled tree **as recorded there**.
They were **not regenerated here**:

| Evidence (ADG-NEXT16-POST-DEMO-INTEGRATION-01 §7) | Result |
| --- | --- |
| Positive control, both builds | #418 on 62 of 62 full loads, on Next 15 and on Next 16 |
| Old `main` (Next 15.5.25), stream-delayed | **67 of 576** full loads with #418 (11.6%) |
| Integrated (Next 16.4.1), all full loads | **0 of 1,344**: 0 of 960 stream-delayed, 0 of 384 direct |
| Client-side transitions | 0 of 266 |
| Other application console errors | 0 |

### 9.2 The reconciliation smoke

The same harness ran, unchanged: `hydration-gate.script.js` behind `stream-delay-proxy.script.js`.
The reconciled build was the clean-clone `next start` of `1309146`. The baseline was current
`main`'s `frontend/` at `32da1ae`. Both used the same API and database on the same machine.
Every run is in
[`hydration-runs.json`](ADG-NEXT16-RECONCILE-01-evidence/hydration-runs.json); none was retried
or dropped.

| # | Run | Build | Condition | Full loads | Client transitions | Loads with #418 | Other app errors | Failed steps | 390 px overflow |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | positive control | **Next 16.4.1** (`1309146`) | direct + foreign node | 62 | 19 | **62** (expected) | 0 | 0 | 0 |
| R2 | smoke | **Next 16.4.1** (`1309146`) | 40 ms stream delay | **96** | 19 | **0** | 0 | 0 | 0 |
| R3 | baseline | Next 15.5.25 (`main`, `32da1ae`) | 40 ms stream delay | 96 | 19 | **14** | 0 | 0 | 0 |

One round is a fresh load of the 17 reviewer routes, then 3 reloads of each, then client
navigation along the reviewer path, the keyboard phase and 390 px loads.

* **R1** proves the detector sees #418 on the reconciled build.
* **R2** is the smoke. It is 96 slow-stream loads, nearly ten times the 10 asked for, with no
  #418, no other hydration code (#419–#429) and no other application error.
* **R3** shows the race still reproduces in this container. Its 14 occurrences (14.6%) all
  came at `document.readyState === "loading"`:
  * by phase: 10 on reloads, 3 at 390 px, 1 on a fresh load;
  * by page: the incomplete comparison 5, the demo guide 4, the folder-permission recovery 2,
    the Fatima → Apollo explanation 1, the investigation 1, `/resources` 1;
  * by message: all were `Minified React error #418; … args[]=HTML`, the same as the integration
    baseline.
* At R3's rate, 96 consecutive clean loads would happen by chance with probability about
  0.854⁹⁶ ≈ 3 × 10⁻⁷. At the integration's 40 ms rate (12.0%), about 5 × 10⁻⁶.

This smoke confirms that the reconciliation changed nothing; on its own it is not the
large-sample gate. The full repeated gate was **not** rerun, because the application tree is
proven identical (§6). Had any application file differed, it would have been required.

---

## 10. Contracts

| | |
| --- | --- |
| Explanation contract | **`1.1`**: `CONTRACT_VERSION: Final = "1.1"` in `backend/app/api/caching.py`, and `"const": "1.1"` in all three derived schemas |
| OpenAPI | `python -m app.contracts.openapi` and `python -m app.contracts.derived` regenerated `openapi.json`, `openapi.demo.json` and the three derived schemas. SHA-256 before and after are identical: `openapi.json` `96c585aa…`, `openapi.demo.json` `b9404961…`, `access-explanation` `a8472281…`, `access-paths` `4263e5fa…`, `resource-impact` `9cac9fbe…`. `git status` stayed clean. **No unintended difference** |
| Backend contract tests | `tests/contracts`: **611 passed, 109 skipped** (as ADG-DEMO-03, DEMO-PUBLIC-01 and the public-demo merge) |
| Frontend contract tests | `tests/contracts.test.ts`: **465 passed** |

---

## 11. Migrations

| | |
| --- | --- |
| `alembic heads` | **one head: `0018_risk_evaluation_basis`** |
| `alembic history` | `0017_deployment_marker → 0018_risk_evaluation_basis (head)`, linear from the `0014_merge_alerts_and_reviews` merge point |
| `database/` | hash-identical to `aff70e8`'s and `main`'s. Nothing under `database/` or `backend/` changed since `744c2ef` |
| GRAPH | **no GRAPH migration**: `0018_graph_relationships` is absent, and no `feature/ADG-GRAPH-01*` head is an ancestor (§6.3). The demo reset migrated this tree to the head |

---

## 12. Tests and build

All local gates ran on `1309146` in one Linux container, with a fresh `npm ci` and
`pip install -e ".[dev]"`: Python 3.13.16, Node 22.22.0, PostgreSQL 16.

### 12.1 Frontend

| Gate | Result |
| --- | --- |
| `npm run lint` | **passed** (ESLint 9, flat `eslint-config-next` 16.4.1) |
| `npm run typecheck` | **passed** |
| `npm run test` (full) | **1,361 passed** (38 files), equal to the integration and `main` |
| `npm run build` | **passed**: `▲ Next.js 16.4.1 (webpack)`, 39 routes |
| Clean clone | `git clone --branch claude/gallant-turing-aj7rf1` gives `1309146` with a clean tree. `npm ci` installs 466 packages (`next` 16.4.1, `react-dom` 19.3.0). The build passed and the clone's tree was still clean afterwards. Every browser run used this build |

### 12.2 Backend and database

| Gate | Result |
| --- | --- |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** (412 files) |
| `mypy app tests` | **passed** (411 source files) |
| `pytest -m "not smoke"` (full unit suite, includes `tests/contracts`) | **6,009 passed, 124 skipped, 1,273 deselected** in 1m50s. Equal to the integration, ADG-DEMO-03 and DEMO-PUBLIC-01. This container has no PowerShell 7 (`pwsh`); the integration report (§13) attributes 107 of the 124 skips to that, and CI's runner has it |
| Representative PostgreSQL tests | **58 passed** (§8.2) |
| Full PostgreSQL suite (`pytest -m smoke`) | not rerun locally. CI run 87 ran it on `1309146`: **1,272 passed, 1 xfailed** (§13) |

---

## 13. CI

GitHub Actions (`.github/workflows/ci.yml`) on `claude/gallant-turing-aj7rf1`:

**Run 87** on the reconciled merge `1309146`
([run 38811542694](https://github.com/40ozs/adg/actions/runs/38811542694)): **success, every job**,
completed 22:13:31 UTC.

| Job | Result |
| --- | --- |
| Frontend (install, lint, types, tests, build) | **success** (21:39:40–21:41:01 UTC) |
| Collector (Pester, `windows-latest`) | **success** (21:39:40–21:41:09 UTC) |
| Docker Compose configuration | **success** (21:39:40–21:39:46 UTC) |
| Backend: lint, format, `mypy` | **success**: ruff clean, 412 files formatted, `mypy` clean over 411 source files |
| Backend: unit tests | **6,121 passed, 12 skipped, 1,273 deselected** (21:40:26–21:43:26 UTC) |
| Backend: database tests (PostgreSQL) | **1,272 passed, 1 xfailed**, 6,133 deselected, in 29m59s (21:43:26–22:13:30 UTC) |

Every backend count equals run 80's on `501c136`. The xfail is the pre-existing one in
`test_access_performance.py`. The job's gate lines, verbatim, are in
[`ci-run-87-backend.txt`](ADG-NEXT16-RECONCILE-01-evidence/ci-run-87-backend.txt). CI skips 12
unit tests where this container skips 124 (§12.2), from the same collected set of 6,133.

The report commit on top of `1309146` changes only `docs/`. A report cannot name its own
commit, so that commit's run is given in the final response.

For reference: run 80 (`501c136`, the integration's code head), run 81 (`32da1ae` on `main`) and
run 77 (`744c2ef` on `main`) all passed.

---

## 14. Reports

| Report | In the reconciled tree | Blob |
| --- | --- | --- |
| [`ADG-DEMO-PUBLIC-MERGE-01-report.md`](ADG-DEMO-PUBLIC-MERGE-01-report.md) | **yes** | `6a1e761…`, identical to `main`'s |
| [`ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md) and [its evidence](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/) | **yes** | `c518519…` (directory `1e990d8…`), identical to `aff70e8`'s |
| this report and [`ADG-NEXT16-RECONCILE-01-evidence/`](ADG-NEXT16-RECONCILE-01-evidence/) | added by the report commit | |

Neither earlier report was edited. Where the integration report predates this reconciliation
(its §2 "`32da1ae` … not on `main`" and §15's fast-forward from `744c2ef`), this report
supersedes it on that point and leaves the original text as the record of its time.

---

## 15. Handoff

**Merge the report commit at the head of `claude/gallant-turing-aj7rf1` into `main`** (the
ending SHA, named in the final response). Its code is the merge `1309146`, and the report commit
changes only `docs/demo/reports/`.

* `main` (`32da1ae`) is an ancestor of the branch, so while `main` has not advanced this is a
  **fast-forward**: `git merge --ff-only <ending SHA>`. No new merge commit, no rebase.
* **Before merging, check:**
  1. CI on the ending SHA has passed every job;
  2. `git diff --name-status aff70e8 <ending SHA>` lists only
     `docs/demo/reports/ADG-DEMO-PUBLIC-MERGE-01-report.md`, this report and its evidence
     directory;
  3. `origin/main` is still `32da1ae`, or still an ancestor.
* **After merging:**
  1. run a fresh `npm ci` in `frontend/`, for anyone with a Next 15 `node_modules`;
  2. nothing else: no migration, no API or contract change, no environment variable.
* **Separate follow-ups, not part of this merge:**
  1. ADG-UI-UPDATE-01 / COMPACT-IDENTITY-01 against this line;
  2. the deploy line or staging moved to this line and to 16.4.1, then the hosted #418 gate;
  3. GRAPH, which needs a merge revision for the second `0018`.

---

## Limitations, carried forward

From the integration's §14, unchanged and not introduced here:

1. The hosted timing is simulated by the stream-delay proxy, not measured on the Worker/Render
   path.
2. Chromium only.
3. One 4-vCPU container.
4. Collector Pester and `docker compose config` ran in CI only. The Docker Compose demo path is
   still unrun.
5. This line has no hosted deployment. The existing staging runs a different tree.
6. Not integrated, deliberately: ADG-UI-UPDATE-01, ADG-UI-COMPACT-IDENTITY-01, the deploy line,
   GRAPH.
7. `npm audit` still reports 13 findings in test and lint tooling.

Specific to this prompt: the React #418 smoke is one round per condition (§9.2). The weight of
the #418 evidence rests on the integration's gate, which applies through tree identity.
