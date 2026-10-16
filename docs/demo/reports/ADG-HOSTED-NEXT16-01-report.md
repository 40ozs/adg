# ADG-HOSTED-NEXT16-01 — The free staging stack on `main`, deployed and checked on the hosted path

## 1. Verdict

**`READY_FOR_ADG_HOSTED_NEXT16_REVIEW_WITH_LIMITATIONS`**

`main` (`d9d39cc`, Next.js 16.4.1) now runs on the existing free demo staging: the thin
Cloudflare Worker `adg-demo-staging` in front of the one Render service `adg-demo-web` (Next.js and
the API behind the origin gate), on the existing Supabase database `adg_demo`. The deployment
stack was ported from `deploy/ADG-FREE-EDGE-SSR-03` file by file. Nothing of the old deploy line's
application code came with it. The ported infrastructure files are byte-identical to what was
already running on staging.

**React #418 on the hosted path: 0** in two 10-round runs of the committed detector: 2,280
records, 1,920 full document loads and 360 client transitions. Its positive control reported #418
on 62 of 62 loads of the same deployment. Hydration errors: **0**. Application console errors:
**0**. The gate also recorded 10 proxy-level resource errors, none from ADG code: 9 Render 503s
during one cold start after the test machine slept (run A), and 1 edge 502 on a background
prefetch (run B). §15 lists them. Smoke **23/23**, browser gate **22/22**, DEMO-01 **15/15**,
DEMO-02 **29/29**, public walkthrough **39/39**, DEMO-03 **39/39** in 1 of 3 runs (§14). The demo
state survived the deploy, a restart, two sleep/wake cycles and a Worker redeploy with an
unchanged fingerprint (§16).

Limitations (§18), none of them a code defect found on the hosted path:

1. **The demo database was migrated, not reset.** A reset under `main`'s code was refused by
   this session's permission policy, so only the additive migration `0018` was applied. The one
   stored risk evaluation predates `0018` and has no collection basis, so `/risks` adds one
   reason: "The last evaluation predates snapshot recording". The reading is still
   `incomplete`, as on a fresh reset. A documented reset (§17) removes the extra reason.
2. **DEMO-03's 4-second interactive window on 0.1 CPU.** 1 of 3 hosted runs reached every
   expected state. In the other two, one comparison passed the window earlier than on the
   4-vCPU host DEMO-03 was recorded on (§14).
3. **Hosted walkthroughs sign in as `planner`.** The hosted demo refuses `auditor` by policy,
   and the committed scripts sign in as `auditor` (§14).
4. Render `adg-demo-web` tracks `integration/ADG-HOSTED-NEXT16-01`, while the guide and the
   workflow name `deploy/ADG-FREE-STAGING-01` (§11).
5. Free-tier transport: a request that arrives during a cold start can get Render's 503, and
   one background prefetch in 1,140 got an edge 502 (§15).

---

## 2. Starting `main`

| | |
| --- | --- |
| `origin/main` at start | **`d9d39cc5cc61eb8c637c42595316735fbab7b187`**, from `git fetch`. Equal to the expected SHA |
| What it is | public demo line + Next.js 16.4.1 / React 19.3.0, webpack build (ADG-NEXT16-MERGE-01), Mac-reviewed |
| Working tree | a new worktree `../adg-HOSTED-NEXT16-01` from `origin/main`. `git status --short` and `--porcelain=v1` empty, `git diff --quiet` and `git diff --cached --quiet` exit 0 |
| Primary checkout | `~/Documents/code/adg` (on the frozen `deploy/ADG-FREE-EDGE-SSR-03`) holds two untracked Finder files (`.DS_Store`, `frontend/.DS_Store`). Not touched, not used |

## 3. Old deploy branch

| | |
| --- | --- |
| `origin/deploy/ADG-FREE-EDGE-SSR-03` | **`57bbbaadce50ba18c854f489a97cbcdef7bb3a17`**. Equal to the expected SHA. Not modified |
| Merge base with `main` | `1ab01a2` (2026-10-12). The branch is 9 commits ahead and 34 behind |
| Later deploy line | `fix/ADG-NEXT16-REACT418-01` (`04663bc`) and `deploy/ADG-FREE-STAGING-01` (`3e26e4a`) descend linearly from it. They add UI-UPDATE-01, Next 16.4.0 and COMPACT-IDENTITY-01. The live staging before this task was `33cca01` from that line. Not modified |
| Contract read from | `docs/handoffs/adg-free-edge-ssr-03.md`, `docs/deployment/cloudflare-demo.md` (as on `3e26e4a`), `adg-next16-react418-01.md`, `adg-next16-ui-integration-02.md` |

The contract, as those documents state it and the code confirms:

| Piece | What it requires |
| --- | --- |
| Worker | `frontend/wrangler.jsonc`, `cloudflare/{worker,routing,environment}.ts`; no binding; var `ADG_RENDER_ORIGIN`, secret `ADG_ORIGIN_KEY`; replaces any caller's key; an API answer without `X-ADG-Runtime-Mode: demo` is a 502 |
| Static assets | `.edge-assets/` from `npm run cf:assets` over `.next/static` of the **same** `docker build --target web` (linux/amd64, `RENDER_GIT_COMMIT` = the deployed SHA as the build ID) |
| Render | one free Docker service, `docker/Dockerfile.render`, no start command (`node gate.ts`), health `/health/live`, auto-deploy off |
| Origin gate | `frontend/render/gate.ts`: the only public listener; 403 without the key except `/health/live`; refuses to start without a key of at least 32 characters |
| Runtime settings | `next.config.mjs`: standalone output and no route preloading when `ADG_NEXT_OUTPUT=standalone`; build ID from `ADG_BUILD_ID` |
| Environment (names) | Render: `ADG_RUNTIME_MODE`, `ADG_DEMO_DATASET`, `ADG_DATABASE_URL`, `ADG_REMEDIATION_EXECUTION_MODE`, `ADG_ORIGIN_KEY`, `ADG_WEB_URL`, `ADG_ENVIRONMENT`, `ADG_AUTH_MODE`, `ADG_DEV_AUTH_SECRET`, `ADG_DEV_AUTH_USERS`, `ADG_LOG_LEVEL`, `ADG_LOG_FORMAT`. Worker: `ADG_ORIGIN_KEY`, `ADG_RENDER_ORIGIN` |
| Database | `adg_demo` through the Supabase session pooler; marker `demo / null-meridian-v1`; reset only deliberately; `verify` read-only |
| Checks | `frontend/cloudflare/checks/{smoke,browser}.mjs`, `null_meridian_fingerprint.py` |
| Rollback | `wrangler rollback <version>`; Render deploy of the previous commit |

## 4. Deploy-port classification

Every change `deploy/ADG-FREE-EDGE-SSR-03` holds over the merge base (32 files), and every later
deploy-line commit:

| Change | Class | Decision |
| --- | --- | --- |
| `frontend/wrangler.jsonc`, `cloudflare/{worker,routing,environment}.ts`, `cloudflare/tsconfig.json`, `cloudflare/assets.mjs` | `REQUIRED_DEPLOY_INFRA` | ported as on `57bbbaa` |
| `frontend/render/gate.ts`, `docker/Dockerfile.render`, `.dockerignore` (`.open-next`, `.wrangler`) | `REQUIRED_DEPLOY_INFRA` | ported as on `57bbbaa` |
| `frontend/next.config.mjs` (standalone, `preloadEntriesOnStart: false`, build ID) | `REQUIRED_DEPLOY_INFRA` | ported. `main` had not changed this file since the merge base |
| `frontend/cloudflare/checks/{smoke,browser}.mjs`, `null_meridian_fingerprint.py` | `REQUIRED_DEPLOY_INFRA` | ported as on `57bbbaa` |
| `frontend/tests/cloudflare/*`, `frontend/tests/render/gate.test.ts` | `REQUIRED_DEPLOY_INFRA` | ported |
| `backend/app/origin_gate.py`, `backend/tests/test_origin_gate.py` (gate of the suspended rollback service `adg-demo-api`) | `REQUIRED_DEPLOY_INFRA` | ported; the guide's rollback path names it |
| `.github/workflows/cloudflare-staging.yml` | `REQUIRED_DEPLOY_INFRA` | ported, with `6b69fe3`'s push trigger `deploy/ADG-FREE-STAGING-01` |
| `frontend/.gitignore` (`.edge-assets/`, `.wrangler/`, `.dev.vars`) | `REQUIRED_DEPLOY_INFRA` | ported |
| `frontend/package.json` scripts `cf:typecheck`, `cf:assets`; devDependencies `wrangler`, `@cloudflare/workers-types` | `REQUIRED_DEPLOY_INFRA` | applied by hand to `main`'s manifest |
| `frontend/package.json` `next`/`eslint-config-next` `^15.5.27` | `SUPERSEDED_BY_MAIN` | not ported; `main` has `^16.4.1` |
| `frontend/package-lock.json` | `REQUIRED_DEPLOY_INFRA` | regenerated on `main`'s lockfile by `npm install -D wrangler@4.149.0 @cloudflare/workers-types@5.20261012.1` (the versions the Next 16 staging line ran). 57 entries added; **no existing entry changed version** or was removed |
| `frontend/eslint.config.mjs` ignores `.edge-assets/**`, `.wrangler/**` | `REQUIRED_NEXT16_COMPAT` | applied to `main`'s flat config (Next 16), not the deploy branch's `FlatCompat` file |
| `frontend/tsconfig.json` `exclude` (`cloudflare/worker.ts`, `.edge-assets`, `.wrangler`) | `REQUIRED_NEXT16_COMPAT` | applied to `main`'s Next 16 tsconfig (`jsx: react-jsx`, `.next/dev/types`) |
| `backend/app/main.py` + `tests/demo_mode/test_boundary.py` (`14ed8fa`: the demo notice header without its em dash) | `REQUIRED_DEPLOY_INFRA` | ported. On `main`, every answer from the runbook export route is an unmarked HTTP 500, because a header value is latin-1. The hosted smoke check requires 403 there |
| `docs/deployment/cloudflare-demo.md`, handoffs `adg-cloudflare-demo-01`, `adg-free-public-staging-02`, `adg-free-edge-ssr-03`; `README.md` and `docs/architecture/demo-mode.md` pointers | `DOCUMENTATION_ONLY` | ported; the guide with `6b69fe3`'s branch name |
| Intermediate commits `cedca60` (OpenNext, Access), `9b64b6a`, `dc6e310`, `13719fb` | superseded within the branch | only their surviving net state was ported |
| `6b69fe3`: `navigation.mjs` step, three UI-UPDATE smoke checks, `adg-ui-update-01.md` | `OLD_APP_FEATURE` | not ported |
| `61fedb8`, `552bcb8`, `371b90e` (incl. `checks/navigation.mjs`), `861a11b`, `a5cdf88`, `afa7804` (ADG-UI-UPDATE-01) | `OLD_APP_FEATURE` / `DO_NOT_PORT` | excluded |
| `66ca201` (Next 16.4.0) | `SUPERSEDED_BY_MAIN` | excluded; `main` has `af94e2e` + `501c136` (16.4.1) |
| `4f33e08`, `98a9a79`, `0a66322`, `f50e8f2`, `9502b4b`, `33cca01` (ADG-UI-COMPACT-IDENTITY-01) | `OLD_APP_FEATURE` / `DO_NOT_PORT` | excluded |
| `f5005b1`, `04663bc`, `3e26e4a` (handoffs of branches not on `main`) | `DOCUMENTATION_ONLY` | not ported |
| `feature/ADG-GRAPH-*`, other `claude/*` branches | `DO_NOT_PORT` | not read for porting |

Not in the deploy branch's diff and therefore untouched: application pages and components, API
routes, the generated OpenAPI snapshot, demo data, migrations. `.edge-assets/` and `.wrangler/`
are generated and git-ignored.

## 5. Ported

| Commit | What |
| --- | --- |
| `5ce87ba` | `fix: send the demo notice header without its em dash` (port of `14ed8fa`) |
| `25c4d7e` | `deploy: port the free staging stack onto main (thin Worker, one Render service, origin gate)` |
| `53b81a6` | `docs: free public demo staging guide and its handoffs, on main` |

`53b81a6` is the deployed commit. Every infrastructure file of the deployment
(`Dockerfile.render`, `gate.ts`, `next.config.mjs`, `wrangler.jsonc`, `worker.ts`,
`routing.ts`, `environment.ts`, `assets.mjs`, `.dockerignore`) is byte-identical to `57bbbaa` and
to the previously live `33cca01`. No change for Next.js 16 was needed in them:
`Dockerfile.render` runs `npm run build`, which on `main` is `next build --webpack`, on Node 22.

## 6. Excluded

UI-UPDATE-01, COMPACT-IDENTITY-01, the GRAPH branches, Next 15 (`^15.5.27`), the deploy line's
Next 16.4.0 commit, the old principal page and Demo Guide (never in the EDGE-SSR-03 diff), the
`navigation.mjs` harness and the three smoke checks for UI-UPDATE routes, and no generated
contract. Proof: `git diff --name-only origin/main..53b81a6` lists 32 files, none under
`frontend/app/`, `frontend/components/`, `backend/app/api/`, `database/` or the OpenAPI snapshot.

## 7. Integration branch

| | |
| --- | --- |
| Branch | **`integration/ADG-HOSTED-NEXT16-01`** (the preferred name), from `origin/main` `d9d39cc`, pushed |
| Deployed SHA | **`53b81a670f03ff69f6a7545dab103c844265d624`** |
| Head | this report's commit, documentation and evidence only on top of `53b81a6` |

## 8. Next.js / React

`next` **16.4.1**, `react`/`react-dom` **19.3.0**, `eslint-config-next` 16.4.1 (from `main`,
unchanged). `next build --webpack`, the flat ESLint config, `jsx: react-jsx`. The Render image
builds the standalone server with `ADG_NEXT_OUTPUT=standalone`. Node 22 in the image, Node
22.23.3 arm64 locally. No Turbopack, no Suspense boundary added, no application change.

## 9. Local validation

macOS arm64, Node 22.23.3, Python 3.13 (fresh `uv` venv in the worktree), PostgreSQL 16 in a
throwaway container.

| Gate | Result |
| --- | --- |
| `npm ci` | ok |
| `npm run lint` / `typecheck` / `cf:typecheck` | clean / clean / clean |
| `vitest` | **41 files, 1,450 tests passed** |
| `next build` (`next build --webpack`) | ok, Next.js 16.4.1 (webpack) |
| `docker build --platform linux/amd64 -f docker/Dockerfile.render --target web --build-arg RENDER_GIT_COMMIT=53b81a6…` | ok; standalone build; `BUILD_ID` = `53b81a670f03ff69f6a7545dab103c844265d624`; 61 static files |
| `wrangler deploy --dry-run` (4.149.0) | ok; **4.00 KiB / 1.70 KiB gzip**; bindings: `ADG_RENDER_ORIGIN` only |
| backend `ruff check` / `ruff format --check` / `mypy app tests` | clean / 414 files formatted / no issues in 413 files |
| backend hermetic suite (`-m "not smoke"`) | **6,131 passed**, 12 skipped, 1,273 deselected |
| backend full suite incl. PostgreSQL (`ADG_RUN_SMOKE_TESTS=1`) | **7,403 passed**, 12 skipped, 1 xfailed, 0 failed (20 min) |
| Alembic | one head, `0018_risk_evaluation_basis`; `database/` identical to `main`; no GRAPH migration (`0002_ad_graph` is the Phase 1B baseline from 2026-09-14) |
| Render settings (API) | `adg-demo-web`: Docker, `./docker/Dockerfile.render`, no start command, health `/health/live`, free, Oregon, auto-deploy off; exactly the 12 environment names of §3 (names read, values not) |
| Secret scan of `origin/main..53b81a6` | 0 hits for the origin key, session secret, database password, Render key, project ref; the only pooler-host match is the guide's `<ref>`/`<password>` template |

The backend changed (the header fix and the rollback gate), so the full suite ran, not a subset.

## 10. CI

The task stated that GitHub Actions minutes were exhausted and to record
`CI_NOT_RUN_QUOTA_EXHAUSTED`. Nothing here waited on or relied on CI. The push nevertheless ran
**CI run [38847132425](https://github.com/40ozs/adg/actions/runs/38847132425) on `53b81a6`:
success** (Frontend; Backend; Collector (Pester); Docker Compose configuration). The
`cloudflare-staging` workflow did not run: its push trigger is `deploy/ADG-FREE-STAGING-01`, and
its environment holds no secrets in any case.

## 11. Deployment targets

Each one was identified from the provider before anything changed. All are the existing demo
staging. None is production.

| Target | Identity |
| --- | --- |
| Cloudflare | the owner's account (workers.dev subdomain `d-walkes`, the wrangler login on the operator machine), Worker **`adg-demo-staging`** <https://adg-demo-staging.d-walkes.workers.dev>; live before: version `66663d4a-c925-44dc-bdca-011e1d8f9c65` (tag `33cca01aa68a`) |
| Render | the owner's workspace, service **`adg-demo-web`** `srv-db6jbmlg1s2s739i4fsg` <https://adg-demo-web.onrender.com>; live before: `dep-db6pe17f3r2c73c0e8f0` @ `33cca01`. `adg-demo-api` (`srv-db6hltivcj2c73fujv30`) stays **suspended** and untouched |
| Database | Supabase session pooler, database **`adg_demo`**, `deployment_marker` = `demo / null-meridian-v1` (marked 2026-10-12T17:33:33Z by `python -m app.demo_mode reset`), 300 scan runs, 0 stored simulations, 0 change plans, 0 alert watches |
| Secrets/bindings | Worker secret `ADG_ORIGIN_KEY` (kept); Worker var `ADG_RENDER_ORIGIN`; Render: `ADG_DATABASE_URL`, `ADG_ORIGIN_KEY`, `ADG_DEV_AUTH_SECRET` (kept). No value was printed, changed or committed |
| Render branch | changed from `deploy/ADG-FREE-STAGING-01` to `integration/ADG-HOSTED-NEXT16-01` so that `53b81a6` can be deployed (`main`'s line is not a descendant of the deploy branch). Auto-deploy stays off |

## 12. Deployment

| Step | When (UTC, 2026-10-16) | Result |
| --- | --- | --- |
| Read-only `verify` + fingerprints, before | 03:46 | ok, 23/23 probes, `228b9dce…f9efd0`; Null Meridian `db736602…85c28a0` match |
| Demo reset (`python -m app.demo_mode reset`) | — | **refused by this session's permission policy; not run** |
| Migration `alembic upgrade heads` against `adg_demo` | 04:30:11–04:32:33 | `0017_deployment_marker` → **`0018_risk_evaluation_basis`** (one nullable column on `risk_evaluations`; no row touched) |
| Render deploy | created 04:32:57, live **04:35:57** | **`dep-db8qhucs728c73anb3n0`**, commit `53b81a670f03…` |
| Edge assets | before the Worker | `cf:assets` over the `--target web` image's `.next/static`: 61 files + `_headers` |
| Worker deploy | **04:36:35** | version **`d4b1645a-8ffe-4496-a4c8-b1fd0d6253fe`**, tag `53b81a670f03`; 4.00 KiB; 21 new assets, 40 already uploaded; secret `ADG_ORIGIN_KEY` present |
| Public check | 04:36 | `/version`, `/runtime`, `/health/live` 200 through the Worker; the homepage HTML carries build ID `53b81a6…`; Render without the key: 403 |
| Read-only `verify` + fingerprints, after | 04:37 | ok, 23/23 probes, **`228b9dce…f9efd0`** (unchanged); Null Meridian **`db736602…85c28a0`, match** |
| Worker redeploy (lifecycle check, §16) | 06:41 | version **`723ffc1c-6c45-4bb8-ab0b-0e9260ac8bf5`**, same commit and assets: **the live Worker at the end of this task** |

Migrations applied: one, `0018_risk_evaluation_basis`. No reset, no data change.

## 13. Hosted smoke

`frontend/cloudflare/checks/smoke.mjs` against `ADG_WEB_URL` = `STAGING_URL` =
`https://adg-demo-staging.d-walkes.workers.dev`, `RENDER_ORIGIN` =
`https://adg-demo-web.onrender.com`, with the database host and the project ref as canaries:

**23/23 passed, 0 failed** (04:37Z, right after the deploy). The checks, with their counts:

| Area | Result |
| --- | --- |
| Front gate / login | anonymous: 5 data routes → 401; development sign-in offers exactly `viewer, planner`; admin, auditor, reviewer, governance, approver → 404; API sign-in as `planner` (`remediation_planner`); web sign-in sets a session |
| Origin protection | **12 requests** to Render without the origin key or with a guessed one → **403**; only `/health/live` open |
| Health / readiness | `/health/ready` ready in demo mode, PostgreSQL 12.9 ms, no connection detail; `/version` demo; `/runtime` demo / synthetic / `null-meridian-v1`, discloses nothing; mode cannot be switched (POST/PUT/PATCH/DELETE → 405) |
| Refusals | ingestion 403 `demo_only` (anonymous; planner: 3 routes); **8 privileged writes → 403** (the export among them; it was a 500 on `main` before `5ce87ba`); no remediation execution route; the web tier forwards no ingestion write (405) |
| API and reviewer routes | `/api/v1/demo`: 3 generations applied, scenarios A–H; `/demo` guide; scenario A share; scenario F principal; scenario F explanation; scenario G path; risks; simulations |
| Canaries | neither the database host nor the project ref in any answer |

`frontend/cloudflare/checks/browser.mjs` (Chromium 1234 arm64, Playwright 1.62.1), as `planner`:
**22/22 passed, 0 page errors** (04:38Z). The Demo indicator and its explanation, sign-in offering only
`planner, viewer`, the `/demo` guide (8 scenarios, three generations), scenario A and B directory
pages, a principal page, scenario F (both routes), scenario G (six-level chain), the risk page
(3 / 8 / 1957 / 423 / 6995), scenario H through the real simulation editor, 11 primary
sections, status, both refusals, `/runtime`, `X-ADG-Runtime-Mode: demo`, sign-out. Two console
errors, both expected: the `/favicon.ico` 404 and the deliberate `POST /api/adg/api/v1/scan-runs`
→ 405.

Static assets: the homepage HTML carries the build ID `53b81a6…`. The Worker serves
`/_next/static/*` from the 61 uploaded files of the same image build, with
`Cache-Control: public, max-age=31536000, immutable`.

## 14. DEMO-01, DEMO-02, DEMO-03, public demo (hosted)

The committed walkthroughs, run against the hosted URL. Each copy differs from the committed
script in exactly three substitutions (`ADG-HOSTED-NEXT16-01-evidence/hosted-script-substitutions.txt`):
the browser path (`/opt/pw-browsers/chromium-1194/chrome-linux/chrome` → the Mac's Chromium 1234),
the web origin from `ADG_WEB_URL` (DEMO-02 and DEMO-03 hard-coded `localhost:3000`), and the
sign-in account from `ADG_SIGN_IN_AS` (default still `auditor`). The hosted demo offers only
`viewer` and `planner` (smoke: `auditor` → 404), so they ran as **`planner`**. `planner` reads
everything `auditor` reads on these pages. It lacks only `SETTINGS_READ`, which none of them uses.
The check labels still say "auditor", because the labels were not edited.

| Walkthrough (committed script) | Hosted result |
| --- | --- |
| **DEMO-01** UI (`demo-01-ui.script.js`) | **15/15**. SYSTEM over SMB: mapping ambiguous, certainty uncertain, outcome indeterminate, "not a finding of no access", no definite "No access", path `remote_smb`. SYSTEM local: mapping confirmed, `at_least`, path `local`. Unresolved SID: alert banner naming the key and the reason, nothing names it as anything else, no invented heading |
| **DEMO-02** (`ADG-DEMO-02-evidence/browser-walkthrough.script.js`) | **29/29**. Fatima Gordon → Apollo: access present (**granted**), **Modify**, **`at_least`**; NTFS route direct and explicit; **share (SMB) route** through ENG-Mobile; hop evidence names the collector and the scans (**provenance**), keyboard reachable with a focus outline; **two missing-evidence items** (lhansen, Proj-Zephyr-RW) that do not affect the conclusion; the orphaned SID stays unresolved; SYSTEM over a share never "No access"; no horizontal scroll at 390 px |
| **DEMO-03** (`ADG-DEMO-03-evidence/browser-walkthrough.script.js`), run 1 | **37/39**. Step 10: the folder-permission-entries comparison (`kind=ntfs_ace`, 3,947 → 4,124 objects, 221 differing) **timed out at 4,007 ms** "while it was at the ordering step", so it showed `⏱ Timed out`, not `✓ Complete … recovered` |
| DEMO-03, run 2 | **37/39**. Step 8: the standard-window retry timed out "at the read step" instead of stopping as `◐ Incomplete` at its group-members checkpoint. Step 10 recovered |
| DEMO-03, run 3 | **39/39**. `timed_out → incomplete → recovered`, every step |
| **Public demo** (`DEMO-PUBLIC-01-evidence/docs-walkthrough.script.js`) | **39/39**. README and Demo Guide claims match the hosted app: Demo pill before sign-in, the investigation (granted, Modify, `at_least`), risk totals 3 / 8 / 1,957 / 423 / 6,995 "not exhaustive", NM-FS-03's 189 findings held, coverage line, the drill `Timed out → Incomplete (5,000 per instant) → Complete, recovered, 221 differences`. It reads "README: /login offers the auditor account" from the README text: true locally; the hosted sign-in deliberately offers only `viewer`/`planner` |

All of them: no application console errors; the only browser error is the known
`/favicon.ico` 404.

Collection and risk semantics on the hosted API (`GET /api/v1/risks/summary` as `planner`):
collection verdict `incomplete`, "98 of 100 collection scopes collected completely · 1
unavailable · 1 partial · 1 holding older evidence"; interpretation `incomplete`, not exhaustive.
The reasons include the extra one from limitation 1: "The last evaluation predates snapshot
recording, so ADG cannot say which collected state it read" (`evaluated_basis` and
`basis_current` null).

**Why DEMO-03 varies on the hosted path.** The drill uses a fixed 4-second interactive window,
and DEMO-03 was recorded on a 4-vCPU container. Render Free gives the API 0.1 CPU, so the
estate-wide retry and the folder-only comparison finish close to the window: in 2 of the 4
hosted drill runs (DEMO-03 ×3, the public walkthrough ×1), one comparison passed the window
earlier than recorded. The application did what the design says: it stated the outcome
("Timed out — no comparison is presented"), showed nothing partial as an answer, and offered the
recovery actions. This is hosted capacity against a fixed window, not a regression. Screenshots:
`ADG-HOSTED-NEXT16-01-evidence/demo03-run1-step10-timed-out.png` and
`demo03-run3-step10-recovered.png`.

## 15. Hosted React #418

The committed detector,
`ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js`, against the hosted URL
with its defaults (`GATE_RELOADS=3`), `GATE_ROUNDS=10`. Only the browser path and the sign-in
account were changed (§14). Its routes, phases and counting are unchanged. Each round is a fresh
browser context with full loads of 17 reviewer routes, 3 reloads of each, client-side
navigation (the guide's links, Back and Forward, the comparison drill, both recovery actions, every
Primary link), keyboard-only use of three disclosures, and 390 px loads. The routes cover the
principal page (Fatima Gordon and the unresolved SID), the access investigation and explanation,
risks and collectors, and the comparison (timed out, incomplete, folder-only). Every record is
kept; none was retried or dropped.

**Positive control first** (`GATE_CONTROL=1`, 1 round, 1 reload, 04:42Z): the detector reported
#418 on **62 of 62** full document loads of the hosted build. It sees #418 on this deployment.

| Run | Window (UTC) | Records | Full loads | Client transitions | **#418** | Hydration family | Other errors | Failed steps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | 04:46:49–05:57:23 | 1,140 | 960 | 180 | **0** | 0 | 9 (all Render 503 during a cold start, below) | 0 |
| B | 06:42:05–07:35:35 | 1,140 | 960 | 180 | **0** | 0 | 1 (an edge 502 on a background prefetch, below) | 0 |
| **A + B** | | **2,280** | **1,920** | **360** | **0** | **0** | 10, all proxy-level | **0** |

Every round of both runs had 114 records. Per phase in each run: fresh 170, reload 680, nav 190, keys 30, phone 70.
No 390 px load scrolled horizontally. Run B started after every lifecycle event in §16, and the
test machine was kept awake for all of it (`caffeinate`; the power log shows no sleep in its
window).

**Run A's nine errors.** In round 6 the test machine went into idle sleep (05:16:47Z, 956 s; one
reload record lasted 956,719 ms across it). With no traffic for 15 minutes, Render stopped the
service (05:31:48Z). When the machine woke, the next loads started a cold start (gate start
05:32:51Z, port open 05:33:54Z). The nine loads between 05:32:44Z and 05:33:52Z (the unresolved-SID
principal ×4, `/status` ×4, `/changes` ×1) received **HTTP 503 from Render's proxy**. The Worker
answers 502 when the origin fails and 503 only for its own misconfiguration
(`cloudflare/routing.ts`). A misconfiguration would not have cleared on its own once the port opened.
Each of the nine is a resource-load console error. None is a page error, and none is #418. The 496
records after the port opened have no error. These are infrastructure errors from an
unplanned idle period, not application console errors. They are kept as recorded, and run B was
made uninterrupted to remove the ambiguity.

**Run B's one error.** Round 7, 07:15:24Z, a reload of the Fatima Gordon → Apollo
investigation. The page loaded (`ok`, heading present, no #418). The error was a **502 on
`/resources?_rsc=…`**, the RSC prefetch Next.js starts in the background for the Resources link
in the navigation. It came from the proxy layer: the origin gate logged no
`adg.gate.upstream_unavailable` (it logs one for every 502 of its own); Render logged no process
exit or error between 07:15:15Z and 07:15:40Z; and the Worker's runtime-mode 502 applies only to
`/api/v1/`, `/auth/` and `/health/`. That leaves a failed Worker → Render fetch or Render's own
proxy. Workers Logs could not be queried from this session (the wrangler login has no access to
them), so which of the two was not determined. A failed prefetch only means the link loads on
click instead of in advance.

Known error, recorded separately as allowed: `/favicon.ico` 404 in both runs (`public/` holds no icon).

**Result: React #418 = 0, hydration errors = 0, application console errors = 0, on the real
hosted URL.** The gate's 10 resource errors are infrastructure answers (503 while booting, one
502 at the proxy) and are disclosed above, not discarded. `BLOCKED_ON_HOSTED_REACT_418` does not
apply.

## 16. Persistence and lifecycle

After every event: read-only `verify` (23 probes) and the Null Meridian logical fingerprint
against `adg_demo`, and `smoke.mjs` through the Worker. Gate timings come from Render's logs (`adg.gate.*`).

| Event (2026-10-16, UTC) | What happened | `verify` | Null Meridian | Smoke |
| --- | --- | --- | --- | --- |
| **Deploy** `dep-db8qhucs728c73anb3n0` + Worker `d4b1645a` (04:32:57–04:36:35) | gate start 04:34:44; Next.js ready +5.5 s; API ready and port open +69.0 s; first answer +73.0 s | ok, `228b9dce…f9efd0` | match | 23/23 |
| **Sleep and wake, unplanned** (during #418 run A) | the test machine slept 05:16:47–05:32:43; with no traffic, Render stopped the instance at 05:31:48 (15 idle minutes); the next request woke it: start 05:32:51, port +62.5 s (05:33:54). The 9 page loads that arrived during the boot got Render's own 503 (§15) | ok, `228b9dce…f9efd0` | match | 23/23 |
| **Render restart** (`POST /v1/services/…/restart`, 06:05:55) | new instance start 06:06:03; port +61.4 s (06:07:04); first answer 06:07:06; the old instance was stopped only then (06:07:07), so there was no gap | ok (checked after the next row) | match | 23/23 (after the next row) |
| **Sleep and wake, deliberate** | idle after the restart: stopped 06:21:01, woken by a request at 06:22:09 (first page +74.6 s server-side), stopped again 06:38:21. Then `GET /` sent 06:38:50: gate start 06:39:02, port +62.5 s, page **HTTP 200 after 85.3 s** at the client, with the Demo indicator and no API failure page | ok, `228b9dce…f9efd0` | match | 23/23 |
| **Worker redeploy** (06:41, same commit and assets) | new version **`723ffc1c-6c45-4bb8-ab0b-0e9260ac8bf5`**; 4.00 KiB; 0 new assets | ok, `228b9dce…f9efd0` | match | 23/23 |

The integrated fingerprint stayed `228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`
from before the deploy through every event, the same value as every reset since ADG-DEMO-02.
Migration `0018` added a column and changed no row, so the fingerprint did not move. After all
the checks, `adg_demo` still holds 0 stored simulations, 0 change plans and 0 alert watches,
1 risk evaluation and 300 scan runs, at Alembic `0018_risk_evaluation_basis` (read-only count).

The restart's own smoke check was taken together with the following wake. This machine went into
maintenance sleep one second after the restart request, and by the time it woke, Render had already
put the idle service to sleep again. Render's own log shows that the restart completed.

## 17. Rollback readiness

Previous known-good hosted deployment: **Render `dep-db6pe17f3r2c73c0e8f0` @ `33cca01`** and
**Worker `66663d4a-c925-44dc-bdca-011e1d8f9c65`** (ADG-NEXT16-UI-INTEGRATION-02). Not performed:
nothing failed in a way that needs it.

```bash
# Worker (immediate):
(cd frontend && npx wrangler rollback 66663d4a-c925-44dc-bdca-011e1d8f9c65)
# Render: point the service back at the deploy branch and deploy the previous commit
#   PATCH /v1/services/srv-db6jbmlg1s2s739i4fsg {"branch": "deploy/ADG-FREE-STAGING-01"}
#   POST  /v1/services/srv-db6jbmlg1s2s739i4fsg/deploys {"commitId": "33cca01aa68a548e0ac738899f4b61f0a4bc0f16"}
# Both together: the Worker's edge assets and Render's HTML must come from the same build.
```

The database stays at `0018`. That is compatible with `33cca01`, which neither reads nor writes
`basis_token`; the column is nullable. Only a **reset run from the old line** would fail, because
its Alembic tree does not know `0018`. Before such a reset, run
`alembic downgrade 0017_deployment_marker` from this branch, which drops only that column.

To remove limitation 1, run the documented reset from this branch's backend. It applies to the
demo database only and takes minutes to half an hour from a workstation; there are no visitor
records to lose (§11):

```bash
export ADG_RUNTIME_MODE=demo ADG_DEMO_DATASET=null-meridian-v1 ADG_REMEDIATION_EXECUTION_MODE=disabled
export ADG_DATABASE_URL="$(cat ~/.config/adg-staging/adg-database-url)"
python -m app.demo_mode reset     # then: --json verify (expect 228b9dce…f9efd0), status
```

## 18. Remaining limitations

1. **Migrated, not reset.** This session's permission policy refused
   `python -m app.demo_mode reset` against the hosted database. Only `alembic upgrade heads`
   (`0017` → `0018`, additive) ran. Hosted `/risks` reads `incomplete`, as on a fresh reset,
   with one extra reason: the stored evaluation predates `0018`, so its collection basis is
   unknown (§14). The reset in §17 removes it. The owner decides whether to run it.
2. **DEMO-03's 4-second window on Render Free.** 2 of 4 hosted drill runs passed the window
   earlier than DEMO-03 recorded on 4 vCPU (§14). The state shown was correct each time. A
   reviewer who sees "Timed out" at the recovery step can retry.
3. **Hosted walkthroughs ran as `planner`**, not `auditor`, because the hosted demo refuses
   `auditor` by design. The committed scripts and their labels were left as they are.
4. **Branch tracking.** Render `adg-demo-web` tracks `integration/ADG-HOSTED-NEXT16-01`. The
   guide, the workflow trigger and the previous handoffs name `deploy/ADG-FREE-STAGING-01`, which
   still points at the old line (`3e26e4a`). This line is not a descendant of it, so it could
   not be fast-forwarded, and it was left alone, as the task required. Where the deployment
   should live (this branch merged to `main`, or the deploy branch moved to it) is the owner's
   call.
5. **Cold start and capacity, unchanged by this task.** 85 s from a sleeping service to the
   first page. Requests that arrive during the boot can get Render's 503 (run A). 0.1 CPU
   queues concurrent requests.
6. **One client, one browser.** Every hosted check ran from one machine, in Chromium 1234
   (arm64). Firefox and Safari were not run. Worker CPU analytics were not collected; the
   Worker is byte-identical to the one measured in EDGE-SSR-03 and UI-INTEGRATION-02.
7. **Known follow-ups, not addressed (out of scope):** the `/risks` severity bullet overlap,
   "(deleted)" vs "Deleted: no" on unresolved identities, the `/favicon.ico` 404, and stale
   wording in the MERGE-01 report.
8. The ported guide's status line still points to the EDGE-SSR-03 handoff; this report is the
   newer evidence for the hosted `main` line.

## 19. Clean tree

In the integration worktree, after this report's commit and its push:
`git status --short` and `git status --porcelain=v1` print nothing; `git diff --quiet` and
`git diff --cached --quiet` exit 0; the local branch equals `origin/integration/ADG-HOSTED-NEXT16-01`.
Generated files (`frontend/.edge-assets/`, `.next/`, `node_modules/`, `backend/.venv/`) are
git-ignored. Nothing was merged into `main`, and `deploy/ADG-FREE-EDGE-SSR-03`,
`deploy/ADG-FREE-STAGING-01` and `main` were not modified.

## 20. Recommended SHA

* **Deployed and validated code: `53b81a670f03ff69f6a7545dab103c844265d624`.** Render
  `dep-db8qhucs728c73anb3n0` and Worker `723ffc1c-6c45-4bb8-ab0b-0e9260ac8bf5` (assets from the
  same build) serve it.
* **For the next merge or handoff: the head of `integration/ADG-HOSTED-NEXT16-01`**, the commit
  that adds this report. It contains only `docs/` on top of `53b81a6`, so its application tree
  is the deployed one. Its SHA is given in the handoff message, because a commit cannot name
  itself.

Before merging it into `main`, the owner should decide on limitation 1 (reset or leave), on 4
(where the deployment branch lives), and on whether `main` should carry the staging stack at all.
