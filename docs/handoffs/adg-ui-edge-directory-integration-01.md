# ADG-UI-EDGE-DIRECTORY-INTEGRATION-01: UI, Directory Access and the thin-Worker edge in one release candidate

Integration and release lane for three validated feature streams: ADG-UI-UPDATE-01 (with its
compact-identity / Exclude local groups addendum and the Next.js 16 integration),
ADG-DIRECTORY-ACCESS-01 and ADG-FREE-EDGE-SSR-03. No feature was reimplemented.

**Release candidate: `READY_FOR_ADG_UI_EDGE_DIRECTORY_RELEASE_WITH_LIMITATIONS`** (§8).
**Lane verdict: `ADG_UI_EDGE_DIRECTORY_INTEGRATION_01_BLOCKED`** (§14). The combined tree passes
every local gate, including every browser gate through a local Worker → origin gate →
Next.js/FastAPI chain. It was **not** merged to `main`, **not** deployed and **not** checked on
the public URL. This container has no Cloudflare or Render credentials, its network policy
refuses both public hosts, and the `cloudflare-staging` GitHub environment holds none of the
deploy workflow's secrets (§9). The public application is unchanged by this lane.

What this lane established, all with evidence below: the latest validated head of each lane
(§2); a candidate whose application tree is byte-identical to the validated Directory Access
source, so no duplicate ancestry was replayed (§3); the full hermetic and DB suites, every
frontend gate and CI green on it (§4); five browser gates, 3 runs each, through the real Worker
and origin gate, and once more through the **actual Render image** under Render Free limits
(§5); the thin-Worker architecture intact (§6); and the Directory Access benchmark without
regression (§7).

## 1. Pre-flight

| | |
| --- | --- |
| Repository | `40ozs/adg`, `/home/user/adg`, the only worktree in this container (clean at start) |
| Session branch | `claude/adoring-cannon-7gh2pf`, at `1ab01a2` (= `main`) at start, with no commits of its own. This session is instructed to push only that branch, so the suggested `integration/ADG-UI-EDGE-DIRECTORY-01` exists only as a local branch name for the same commits. |
| `origin/main` | `1ab01a2` (PR #2's merge). An ancestor of every lane below. |
| Canonical integration line | `deploy/ADG-FREE-STAGING-01` = `integration/ADG-NEXT16-UI-02` = `3e26e4a`: the tree Render tracks and the deploy workflow's trigger (`adg-next16-ui-integration-02.md` §8). |
| Other remote branches, untouched | `claude/compassionate-hamilton-3sh5db@8259ed3`, `deploy/ADG-FREE-EDGE-SSR-03@57bbbaa`, `fix/ADG-NEXT16-REACT418-01@04663bc`, `fix/ADG-UI-COMPACT-IDENTITY-01@f50e8f2`, `integration/ADG-UI-EDGE-01@4212f13`, `feature/ADG-GRAPH-01A-domain-model@676d48f`, `claude/wizardly-gates-6yjdxp@cedca60`, and the merged Demo-mode branches |
| Fetched | 2026-10-14 15:08 UTC and again before this report; no branch moved in between |

## 2. Lane readiness

Every head was established from the repository's own handoffs and from ancestry, not from
memory or a scratchpad.

| Lane | Head | Evidence | Classification |
| --- | --- | --- | --- |
| ADG-UI-UPDATE-01 | `a5cdf88` (task), addendum `f50e8f2`, SID leading truncation `33cca01`, all in `3e26e4a` | `adg-ui-update-01.md` §1, §7; `adg-ui-edge-01.md`; `adg-next16-ui-integration-02.md` (deployed and checked on staging 2026-10-13: browser 22/22 ×5, navigation 19/19 ×5) | `VALIDATED_READY`. Includes every item the owner listed: compact SID/table presentation, no SID under names, Users and Groups directories, Exclude local groups, Shortest Chains as linked names, sortable tables, collapsible Identity information, no-reload relationship navigation |
| ADG-NEXT16-UI-02 (Next.js 16.4.0 + the UI addendum) | `3e26e4a` | `adg-next16-ui-integration-02.md`: `READY_FOR_ADG_COMBINED_STAGING_REVIEW`; `adg-next16-react418-01.md`: React #418 resolved | `VALIDATED_WITH_LIMITATIONS`: both handoffs require the October 14, 2026 Next.js security release before `main` (§12) |
| ADG-DIRECTORY-ACCESS-01 | `8259ed3` | `adg-directory-access-01.md`, committed *in* `8259ed3` ("Record the final benchmark and the ADG-DIRECTORY-ACCESS-01 verdict"); its §1 names `3e26e4a` as the starting point | `VALIDATED_WITH_LIMITATIONS` (`ADG_DIRECTORY_ACCESS_01_COMPLETE_WITH_LIMITATIONS`; not deployed) |
| ADG-FREE-EDGE-SSR-03 | `57bbbaa`, in `3e26e4a` | `adg-free-edge-ssr-03.md`: Worker 1–3 ms CPU, Next.js on Render behind the origin gate, rollback drilled; the #418 limitation was closed by Next.js 16 | `VALIDATED_WITH_LIMITATIONS` (cold start 85 s, 0.1 CPU). Live on staging since 2026-10-12 |
| ADG-UI-EDGE-01 (report only) | `4212f13` | docs-only commit on top of `6b69fe3`, never merged into the NEXT16 line, although `adg-ui-update-01.md` links to it | `SUPERSEDED` as a deployment (by `3e26e4a`); kept as evidence (§3) |
| ADG-GRAPH-01A | `676d48f` | `feature/ADG-GRAPH-01A-domain-model` | Out of scope; not integrated |

## 3. Integration

Ancestry decides the plan:

```text
1ab01a2 (main) ─ cedca60 ─ … ─ 643da21 ─ 57bbbaa (EDGE-SSR-03) ─ afa7804 (+UI-UPDATE-01 a5cdf88) ─ 6b69fe3
   6b69fe3 ─┬─ 04663bc (Next 16) ─────────────┬─ 9502b4b ─ 33cca01 ─ 3e26e4a (NEXT16-UI-02 = deploy branch)
            ├─ f50e8f2 (compact identity) ────┘                          └─ 8259ed3 (DIRECTORY-ACCESS-01)
            └─ 4212f13 (ADG-UI-EDGE-01 report)
```

`git merge-base --is-ancestor` confirms `57bbbaa`, `a5cdf88`, `f50e8f2`, `33cca01`, `04663bc`,
`3e26e4a` and `1ab01a2` are all ancestors of `8259ed3`. Directory Access was built on the
integrated UI + EDGE + Next 16 tree, so there is **no duplicate ancestry to replay and nothing
to cherry-pick**. The only commit outside that line is the docs-only `4212f13`.

| Step | Commit | What |
| --- | --- | --- |
| Base | `3e26e4a` | the canonical integration line (§1); the session branch fast-forwarded `1ab01a2 → 3e26e4a` |
| 1 | `c84db9e` | `git merge --no-ff 8259ed3` (Directory Access). No conflict: `8259ed3` descends from the base. `--no-ff` keeps a merge commit naming the lane. |
| 2 | `e39fee1` | `git merge --no-ff 4212f13` (the ADG-UI-EDGE-01 report). Adds `docs/handoffs/adg-ui-edge-01.md` only. |
| 3 | `a305ce8` | checks: `frontend/cloudflare/checks/release.mjs` (§5) and the deploy workflow's hosted checks now run `directory-access.mjs` and `release.mjs` |
| 4 | the commit carrying this report | this report and two screenshots |

**Tree equivalence.** `git diff 8259ed3 e39fee1` is exactly one added file,
`docs/handoffs/adg-ui-edge-01.md`. The application tree of the candidate is byte-identical to
the validated Directory Access source, which is the validated UI + EDGE + Next 16 tree plus the
Directory Access change.

**Conflicts.** None. No file needed a semantic reconciliation in this lane. The three shared
files Directory Access touched on purpose are already reconciled in `8259ed3` and recorded in
`adg-directory-access-01.md` §1: `navigation.mjs` (tab label), `lib/identity.ts` (view id kept
as `directories`) and `EffectiveAccess.tsx` (an optional prop). The semantic areas listed for
review (selected-object and principal views, `SortableTable`, path and identity renderers, tabs,
router state, the API proxy, Next/Cloudflare/Wrangler config, manifests, deploy scripts, FastAPI
routes, schemas, snapshots and browser gates) were checked by the gates in §4–§6, not by merge
resolution.

**Changed after the merge (check tooling and docs only):** `.github/workflows/cloudflare-staging.yml`
(two more hosted checks and their logs), `docs/deployment/cloudflare-demo.md` (lists them),
`frontend/cloudflare/checks/release.mjs` (new). No application, backend or database code.

## 4. Local gates

Container: Linux 6.18.44 x86_64, 4 CPUs, Python 3.13.16, Node 22.22.0, PostgreSQL 16.15. The
commands are CI's (`.github/workflows/ci.yml`).

| Gate | Tree | Result | Reference (`8259ed3` report) |
| --- | --- | --- | --- |
| backend `ruff check .` | `e39fee1` | All checks passed | same |
| backend `ruff format --check .` | `e39fee1` | 409 files already formatted | 409 |
| backend `mypy app tests` | `e39fee1` | no issues in 408 source files | 408 |
| backend `pytest -q -m "not smoke"` | `e39fee1` | **5,810 passed, 124 skipped**, 1,265 deselected, 6 warnings, 1 min 55 s | 5,810 / 124 / 6 |
| backend `ADG_RUN_SMOKE_TESTS=1 pytest -q -m smoke` (**whole DB suite**) | `e39fee1` | **1,264 passed, 1 xfailed**, 0 failed, 5,934 deselected, 70 warnings, 29 min 34 s | 1,264 passed, 1 xfailed |
| frontend `npm run lint` | `a305ce8` | clean (includes `release.mjs`) | clean |
| frontend `npm run typecheck` | `e39fee1` | clean | clean |
| frontend `npm run test` | `e39fee1` | **40 files, 1,366 passed** | 40 / 1,366 |
| frontend `npm run build` (`next build --webpack`, Next.js 16.4.0) | `e39fee1` | compiled; 43 routes incl. `/api/directory-access/export` and `/identities/principal/[key]/[[...view]]` | ok |
| frontend standalone build (`ADG_NEXT_OUTPUT=standalone`, `ADG_BUILD_ID=e39fee1…`) | `e39fee1` | compiled; `BUILD_ID` = the commit | — |
| `npm run cf:typecheck` | `e39fee1` | clean | clean |
| Worker + gate unit tests (`vitest run tests/cloudflare tests/render`) | `e39fee1` | 3 files, 89 passed | — |
| `wrangler deploy --dry-run` (4.149.0) | `e39fee1` | **4.00 KiB / 1.70 KiB gzip, "No bindings found."**, 129 asset files | 4.00 KiB |
| `actionlint` 1.7.7 (`ci.yml`, `cloudflare-staging.yml`) | `a305ce8` | clean | clean |
| `docker build -f docker/Dockerfile.render` | `a305ce8` | builds (1 min 12 s, 777 MB) from a clean `git archive`, with the sandbox's TLS CA added to a scratch copy only (§5); `BUILD_ID` = `a305ce8…`; runs as `adg`; the gate exits 1 without a key | — |
| CI on GitHub, run [38587216167](https://github.com/40ozs/adg/actions/runs/38587216167) | `e39fee1` | **all green**: Backend (lint, types, unit, DB tests 32 min), Frontend (lint, types, tests, build), Collector (Pester, Windows), Docker Compose | — |

The whole DB suite ran on `e39fee1`. The later commits change only check scripts, the
deploy workflow and docs, which no backend test reads, so it was not rerun. The xfail is
pre-existing. Warnings: 70, against 69 at `8259ed3`. The categories are Starlette's
`HTTP_422_UNPROCESSABLE_ENTITY` deprecation (as before), Starlette's deprecation of `httpx` with
its test client (this venv resolved a newer Starlette), and one `SyntaxWarning` from
`tests/db/test_ntfs_resources.py:674`. That last one is an unchanged test file, and the warning
appears only when bytecode is compiled fresh. None comes from the integration.

Not run here: the Windows collector Pester suite (no PowerShell). CI runs it (row above).

## 5. Browser gates (local, through the edge topology)

Earlier lanes ran the browser gates against `next start` locally, where the three checks that
need the Worker or gate in front cannot pass (`adg-ui-update-01.md` §5: 19/22). This lane ran
the whole hosted request path locally instead:

```text
Chromium ─► wrangler dev (the thin Worker, cloudflare/worker.ts, :8787)
              │ X-ADG-Origin-Key (throwaway, generated for this run, never printed)
              ▼
            node render/gate.ts (:10000, the Render image's entry point, run with ADG_GATE_*_DIR)
              ├─► FastAPI 127.0.0.1:8000 (python -m app, demo mode, development sign-in: viewer, planner)
              └─► Next.js standalone server.js 127.0.0.1:3000 (BUILD_ID e39fee1…)
                    └─► PostgreSQL adg_demo (python -m app.demo_mode reset: 300 runs, 23/23 probes,
                        fingerprint 228b9dce…f9efd0 = the hosted value)
```

| Gate | Runs | Result |
| --- | --- | --- |
| `smoke.mjs` (`RENDER_ORIGIN` = the local gate; canaries: the database password, the origin key, the session secret) | 1 | **25/25** |
| `browser.mjs` (repository gate) | 3 | **22/22** each, 0 page errors. The one console error is the deliberate `POST /api/adg/api/v1/scan-runs` → 405 |
| `navigation.mjs` (ADG-UI-UPDATE-01) | 3 | **19/19** each, 0 page or console errors |
| `directory-access.mjs` (ADG-DIRECTORY-ACCESS-01) | 3 | **20/20** each, 0 page or console errors |
| `release.mjs` (this lane, below) | 3 after a harness fix (below) | **13/13** each, 0 page or console errors |
| React #418 | 12 runs | **0** |

`browser.mjs` passing 22/22 locally is new: the gate and Worker in front supply `/runtime`, the
runtime header and the `405` the three web-origin checks need.

**Origin protection** (`curl`, the smoke gate's direct-origin cases): to the gate without the
key, `/`, `/demo`, `/identities/users`, `/runtime`, `/version`, `/api/v1/demo`, `/auth/config`,
`/_next/static/x.js` and `/health/live/` all **403**; a guessed 64-character key **403**; exact
`/health/live` **200** `{"status":"alive","version":"0.1.0"}`; with the key **200**. Through the
Worker, a visitor's own wrong key is replaced (200), and API answers carry
`X-ADG-Runtime-Mode: demo`.

### The Render image itself, under Render Free limits

The image Render would build (`docker/Dockerfile.render` at `a305ce8`) ran with
`--memory=512m --cpus=0.1` behind the same `wrangler dev` Worker, against the same `adg_demo`.
It is the guide's "local rehearsal" (§6 there).

* **Build.** From a clean `git archive` of the commit. Docker Hub refused anonymous pulls (429),
  so the two official base images came from `mirror.gcr.io/library/…` and were tagged locally.
  The sandbox intercepts TLS, so a scratch copy of the Dockerfile added its CA bundle in the two
  stages that download (`NODE_EXTRA_CA_CERTS`, `PIP_CERT`). The committed Dockerfile is
  unchanged. Build 1 min 12 s, image 777 MB.
* **Start** (gate log): Next.js ready at 15.4 s, API ready and port open at **91.4 s**. Render
  measured 65–76 s for the same start at 0.1 CPU (`adg-free-edge-ssr-03.md` §7). This host's
  CPU quota is not Render's; the API's Demo Mode startup check is the long phase, as there.
* **Gates through Worker → image**, one run each: smoke **25/25**, browser **22/22**, navigation
  **19/19**, directory-access **20/20**, release **13/13**, 0 page errors, 0 React #418.
  Directory access document loads at 0.1 CPU: 3.3 s, then 2.1 s and 1.6 s.
* **Memory** after the whole run: **287 MiB of 512 MiB** (Render measured 250 MiB idle, 317 MiB
  peak).
* **Stop.** `docker stop`: the gate stopped Next.js (143) and the API (SIGTERM), then exited.

### What each acceptance item rests on

| Item (prompt §) | Measured by | Local result |
| --- | --- | --- |
| Servers: directory, numeric sort, select | navigation | 33 servers, Shares sorted 8…3 descending, no reload |
| Users: name search, SID search, sort, select | navigation | 13 matches for "Bianca"; SID search → 1; Direct groups sorted both ways; opens by SID, no reload |
| Groups: search, sort, select, views | navigation, release | as above; GG_Sales opened, every view switched |
| Exclude local groups (§10) | navigation (UI), release (API) | UI: 33 local Administrators removed, 5 Finance groups kept, search and sort kept, no reload. API: **238 → 205**, every removed group `local_group`, every non-local group kept, **Domain Local 58 → 58** |
| Compact rows, SIDs (§8, §36) | release, directory-access | Users 50 rows and Groups 50 rows, **every row ≤ 39.8 px**; no SID in any name cell; 50 SID cells each `white-space: nowrap`, `direction: rtl`, text = `title` = the full SID; Directory access 42 rows at 39.8 px, 24 clipped paths all titled |
| Relationship tables without SIDs | release | Bianca Rossi's effective groups: 29 rows, 173 principal links, 0 SID text; 49 share paths one line each, titled |
| Shortest Chains (§11, §37) | navigation, release | `Bianca Rossi → Sales-Enterprise → GG_Sales → All Staff → AD-Okta-Sync-All → OKTA_Slack_All → sg-okta-slack-user`; a group hop and a user hop open their principal pages by SID, no reload; Back and Forward; no chain link prefetches |
| Relationship switching (§18, §38) | navigation (user), release (group) | a user's 5 views and a group's 7 (Direct members, Effective members, Parent groups, Effective groups, Reachable shares, Directory access, Named on share ACLs): no reload, heading and principal kept, tab marked current, **1 request per switch**, and 6 back-and-forth switches stay at 1 each |
| Back/Forward | navigation, release | through a group's 7 views: 7 steps back and 7 forward land on the recorded URLs, in one document |
| Identity information (§19, §39) | navigation, release | folds, stays folded across 5 views, unfolds; Enter and Space toggle on the `<summary>`; a newly selected object (a chain link) starts **open**, as `adg-ui-update-01.md` specifies |
| Directory access Summary / Tree / All paths (§12, §40) | directory-access | 43 roots for 272 reachable (Σ(1 + descendants) = 272); Engineering Modify, 32, 2 exceptions, *varies*; path and count sorts; 41 share roots collapsed, **0 tree requests on load, 1 per first expansion**, nested 2, reopen from cache; All paths 100 rows on page 1 |
| Reachable shares → Directory access (§17) | directory-access | `share=nm-arch-fs01\|archive`: 1 root in one share |
| Export (§16) | directory-access, release | both links name `content=summary` / `content=paths`, neither names `mode`; summary 43 rows, paths 272 rows, `text/csv`, `X-ADG-Complete: true` |
| Deep links (§21) | navigation, release | 15 route families on a fresh document load through the Worker, every one 200 with a heading: `/`, `/resources`, `/identities/users`, `/identities/groups?exclude_local=1`, a principal, its effective groups, Directory access Summary / `?mode=tree` / `?mode=paths`, shares, ACL references, `/search`, a group's effective members, members and parent groups |
| Console / runtime | all | 0 page errors in 16 browser-gate runs (12 through the local gate, 4 through the image); the only console error is `browser.mjs`'s deliberate 405 |

Screenshots (1360 × 900, through the local Worker):
[`adg-ui-edge-directory-integration-01-groups.png`](adg-ui-edge-directory-integration-01-groups.png)
(Exclude local groups on, one-line rows, SIDs cut at the start) and
[`adg-ui-edge-directory-integration-01-chains.png`](adg-ui-edge-directory-integration-01-chains.png)
(Shortest Chains as linked names). Directory access has its own in `adg-directory-access-01.md`.

**`release.mjs`.** New in this lane. The NEXT16 handoff's on-screen measurements were made by
an uncommitted script (§9 there); this gate commits them and adds the items above that no
other gate measured. The first run failed one check: Playwright's `page.goBack()` returns before
a same-document navigation lands. A probe showed one history entry per tab click and Back walking
every view correctly, so the harness, not the app, was wrong. It now steps history in the page
and waits for the URL. After that fix it passed 13/13 in three runs, and once more through the
Render image.

**Domain Local count.** `adg-ui-update-01.md` §7 says Null Meridian has 37 Domain Local and 130
global groups. The database says 58 Domain Local, 148 global (one of them tombstoned, hence the
API's 238) and 33 machine-local. The filter's behaviour is the same either way: Domain Local is
kept, machine-local is removed. The earlier figures were a reporting error.

## 6. EDGE-SSR architecture

| Property | Evidence in this lane |
| --- | --- |
| The Worker renders nothing | `wrangler deploy --dry-run`: 4.00 KiB bundle, **0** occurrences of `next` in `worker.js`; `cloudflare/worker.ts` imports only `environment` and `routing` |
| No paid binding | "No bindings found."; `wrangler.jsonc`: assets + observability only, `global_fetch_strictly_public` |
| No OpenNext | no `@opennextjs/*` dependency, no `open-next.config.*`, no tracked reference outside `docs/` |
| Next.js renders at the origin | `docker/Dockerfile.render`: the standalone server behind `node gate.ts`; `render/gate.ts` routes non-API paths to `127.0.0.1:3000`, API paths to `127.0.0.1:8000` |
| Origin gate | SHA-256 + `timingSafeEqual`, `OPEN_PATHS = {/health/live}`, refuses to start without a ≥ 32-character key; the 89 Worker/gate unit tests; the local probes in §5 |
| Static files at the edge | `cf:assets` assembles `.edge-assets` (129 files) from the same build; `run_worker_first: false` |
| Deep links and client routing through the proxy | §5: 15 route families on fresh loads through the Worker, plus every client-side move in four gates |

Not verifiable here: Worker CPU on Cloudflare (`wrangler tail`, `workersInvocationsAdaptive`),
the Render cold start, and the hosted origin gate. They need the hosted deployment (§9, §11).

## 7. Directory Access benchmark

`python -m tests.benchmarks.directory_access_benchmark --estates 4x250 8x1000 20x1000 --repeat 5`
on `a305ce8` (application tree = `8259ed3`), estates from `tests/support/directory_estate.py`
(seed 29) ingested through the API into `adg_bench`. Run alone: the DB suite, the Render image,
the Worker and Docker had all been stopped first (load average 0.11 at the start). Measured on
Linux-6.18.44-fc-v114-x86_64-with-glibc2.39, Python 3.13.16, at 2026-10-14T15:50:22Z.

| Estate | Directories | Observations | Ingest (s) | Candidates | Reachable | Roots | Exceptions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4x250 | 1000 | 3974 | 4.4 | 795 | 756 | 49 | 91 |
| 8x1000 | 8000 | 29663 | 30.6 | 6007 | 5395 | 381 | 704 |
| 20x1000 | 20000 | 74903 | 76.6 | 15373 | 13759 | 899 | 1789 |

| Shape | Directories | Rows | Statements | Min (ms) | Median (ms) | p95 (ms) | Reference median / p95 (`8259ed3`) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| summary_cold | 1000 | 49 | 16 | 151.43 | 153.07 | 230.71 | 170.87 / 264.37 |
| summary_warm | 1000 | 49 | 1 | 8.67 | 8.78 | 9.02 | 9.87 / 11.96 |
| tree_top_warm | 1000 | 4 | 1 | 3.70 | 3.82 | 4.07 | 4.35 / 4.85 |
| tree_expand_warm | 1000 | 4 | 1 | 3.94 | 4.23 | 4.65 | 4.09 / 4.30 |
| exceptions_warm | 1000 | 16 | 1 | 5.03 | 5.58 | 5.90 | 5.90 / 9.82 |
| flat_listing_page | 1000 | 500 | 10 | 94.22 | 96.19 | 177.74 | 123.50 / 197.17 |
| summary_cold | 8000 | 100 | 28 | 1268.72 | 1283.61 | 1466.98 | 1639.79 / 1801.47 |
| summary_warm | 8000 | 100 | 1 | 17.22 | 17.66 | 19.30 | 21.23 / 25.68 |
| tree_top_warm | 8000 | 8 | 1 | 3.54 | 3.63 | 4.17 | 3.53 / 10.48 |
| tree_expand_warm | 8000 | 4 | 1 | 3.71 | 4.17 | 4.32 | 3.48 / 3.57 |
| exceptions_warm | 8000 | 47 | 1 | 6.92 | 8.03 | 8.46 | 6.30 / 6.86 |
| flat_listing_page | 8000 | 500 | 10 | 144.13 | 153.31 | 245.91 | 219.49 / 519.40 |
| summary_cold | 20000 | 100 | 44 | 3697.97 | **3804.58** | **4031.43** | 3925.26 / 5381.68 |
| summary_warm | 20000 | 100 | 1 | 25.31 | **26.25** | 26.40 | 25.80 / 41.25 |
| tree_top_warm | 20000 | 20 | 1 | 3.94 | 4.01 | 4.43 | 4.14 / 9.65 |
| tree_expand_warm | 20000 | 4 | 1 | 3.96 | **4.23** | 4.85 | 3.86 / 4.33 |
| exceptions_warm | 20000 | 55 | 1 | 8.09 | **8.25** | 12.85 | 7.61 / 10.08 |
| flat_listing_page | 20000 | 500 | 10 | 203.85 | 205.34 | 212.66 | 254.15 / 311.08 |

**No regression.** The estates are identical to the reference: the same candidates, reachable
directories, roots and exceptions at every size. Every statement count is identical: 16 / 28 / 44
for a cold summary, 1 for every warm read, 10 for a flat page. That means the bottom-up count
from `c125d4b` is in place, and the tree-counting loop it replaced has not come back. At 20,000
directories the cold summary is 3.80 s median (reference 3.93 s) and 4.03 s p95 (5.38 s). Warm
reads are within a millisecond of the reference: summary 26 ms, tree expansion 4 ms, one root's
exceptions 8 ms. The sub-10 ms differences are run-to-run noise on a shared host. The 50,000
candidate ceiling is not measured; extrapolating the same ~0.25 ms per candidate gives about
12–13 s, in line with the reference's estimate and inside the frontend's 30 s allowance.

## 8. Release candidate gate

**`READY_FOR_ADG_UI_EDGE_DIRECTORY_RELEASE_WITH_LIMITATIONS`** for the commit carrying this
report on `claude/adoring-cannon-7gh2pf` (application tree = `8259ed3`).

- [x] All material integration conflicts resolved (none arose; §3).
- [x] No known data-semantic regression: hermetic and DB suites as at `8259ed3`; engine-backed
  Directory Access semantics unchanged (byte-identical tree); Exclude local groups semantics
  checked against the API.
- [x] Combined frontend and backend gates pass (§4).
- [x] EDGE-SSR architecture intact (§6).
- [x] Directory Access intact (§5, §7).
- [x] UI features intact (§5).
- [x] Clean candidate tree.

*With limitations* (§12): every browser result is local, through a faithful but local edge
chain, and the repository's own precondition for `main`, the October 14 Next.js security
release, is not met because that release has not been published.

## 9. Merge, push and deployment

### Canonical branch

* **`main`** is the default branch. It moves only through merged pull requests (#1 and #2, both
  merge commits). **`deploy/ADG-FREE-STAGING-01`** is the deployment line: Render tracks it, and
  pushing to it starts the deploy workflow. The candidate fast-forwards both, because `1ab01a2`
  and `3e26e4a` are its ancestors. No newer work landed on either during this lane: both were
  fetched again before this report and had not moved.
* **Not merged to `main` (yet).** Two things stand in the way. First, a recorded precondition:
  `adg-next16-react418-01.md` §6 and `adg-next16-ui-integration-02.md` §9 keep Next.js 16.4.0 out
  of `main` until the October 14 security release is applied. That release is not published
  (§12). Second, this session is instructed to push only its own branch, and `main` changes by pull request.
  Merging overrides the precondition and is outward-facing, so the decision goes to the owner
  (§13).
* **Not pushed to `deploy/ADG-FREE-STAGING-01`.** Moving it without deploying would break the
  rule `adg-next16-ui-integration-02.md` §8 set: the deploy branch's head is the live tree. The
  push would also start the workflow, which would fail at preflight.
* **Pushed:** `claude/adoring-cannon-7gh2pf` only, `1ab01a2 → e39fee1 → a305ce8 →` this report,
  fast-forward each time. **Force push: no.** No history was rewritten and no other branch or
  worktree was touched.

### Deployment: blocked in this environment

| Route | State on 2026-10-14 |
| --- | --- |
| Workstation deploy (`docs/deployment/cloudflare-demo.md` §6), how every earlier lane deployed | Needs Wrangler signed in to the Cloudflare account, the Render dashboard or API key, and the origin key. This container has none of them: no `CLOUDFLARE_API_TOKEN`, `RENDER_API_KEY` or `ADG_ORIGIN_KEY` in its environment, and no Wrangler login. |
| Deploy workflow (`.github/workflows/cloudflare-staging.yml`) | The `cloudflare-staging` environment holds none of its 5 secrets and 3 variables. All 8 runs since 2026-10-12 failed at preflight. The latest, [38338789788](https://github.com/40ozs/adg/actions/runs/38338789788) on `3e26e4a`, logged all 8 names as missing. |
| Hosted checks from this container | The network policy refuses `adg-demo-staging.d-walkes.workers.dev` and `adg-demo-web.onrender.com` (the egress proxy answered `CONNECT` with 403 at 15:08 UTC). |

No Wrangler or Render command was run against the account, nothing was uploaded, and the public
Worker, the Render services and the `adg_demo` database were not touched. Nothing needed rolling
back.

## 10. Rollback point (recorded, not exercised)

From the latest repository evidence (`adg-next16-ui-integration-02.md` §5, deployed
2026-10-13; `adg-directory-access-01.md` §1 records that nothing was deployed after it):

| | Known-good public deployment |
| --- | --- |
| Commit | `33cca01aa68a548e0ac738899f4b61f0a4bc0f16` (application tree of `3e26e4a`, the deploy branch head) |
| Render `adg-demo-web` | deploy `dep-db6pe17f3r2c73c0e8f0` |
| Worker `adg-demo-staging` | version `66663d4a-c925-44dc-bdca-011e1d8f9c65`, tag `33cca01aa68a` |
| Rollback | `npx wrangler rollback 66663d4a-c925-44dc-bdca-011e1d8f9c65`, and Render → `adg-demo-web` → Deploys → roll back to `dep-db6pe17f3r2c73c0e8f0` (or deploy `33cca01`). Both together. The database is never touched by a rollback (`docs/deployment/cloudflare-demo.md` §10). |
| Before that | Worker `19b5a60a-fe07-4599-aba8-a7e8466fb946` (Next 16 alone, `66ca201`) |

This container could not confirm that these are still live: the public URL is unreachable from
it (§9).

## 11. Hosted validation

**Not run.** No hosted check was possible (§9). Each hosted gate of the prompt maps to a
committed script that runs unchanged against the public URL:

| Prompt gate | Script | Local evidence |
| --- | --- | --- |
| §34 smoke (shell, assets, API health, auth policy, no 5xx, direct origin) | `smoke.mjs` with `RENDER_ORIGIN` | 25/25 |
| §35 directories, §36 table density, §37 chains, §38 relationship navigation, §39 identity disclosure | `navigation.mjs`, `release.mjs` | 19/19, 13/13 |
| §40 Directory access, export | `directory-access.mjs`, `release.mjs` | 20/20, 13/13 |
| §34 console/runtime, demo policy, refusals | `browser.mjs` | 22/22 |
| §41 EDGE-SSR on Cloudflare | `wrangler tail adg-demo-staging --format json`; GraphQL `workersInvocationsAdaptive` (guide §6) | dry-run bundle only |
| §42 hosted performance | `release.mjs`'s Directory access timing; guide §7 | Directory access document loads 77–133 ms through the local gate, 1.6–3.3 s through the image at 0.1 CPU |

## 12. Limitations

* **Not deployed; no hosted evidence.** Everything in §5 ran locally through `wrangler dev` and
  the real gate or image, not through Cloudflare and Render. Cloudflare's edge (static assets,
  header handling, gzip) is not reproduced, and the 0.1 CPU rehearsal ran under this host's CPU
  quota, not Render's.
* **The Next.js security release is still owed.** `adg-next16-react418-01.md` §6 and
  `adg-next16-ui-integration-02.md` §9 hold Next.js 16.4.0 back from `main` until the
  October 14, 2026 security release is applied and their checks rerun. At 15:10 UTC on
  2026-10-14 npm had no such release: the newest 16.x is `16.4.0` (2026-10-06), the newest canary
  `16.5.0-canary.10` (2026-10-13), and 15.5.x ends at `15.5.27`.
* **Directory access sorting orders the page in hand** (100 roots a page), as everywhere in ADG;
  there is no global sort. Unchanged.
* **Directory access, unchanged from `adg-directory-access-01.md` §11:** ownership-only access
  outside the candidate set; SMB (`remote`) only; current state only; files not summarized; the
  first summary after an ingestion takes seconds on a large principal (§7); the computation cache
  is per process; the tree and exceptions use the JSON proxy's 10 s default.
* **Demo data gaps:** no unresolved hop and no computer account in any Null Meridian chain, so
  those two Shortest Chain cases rest on unit and DB tests (`adg-ui-update-01.md` §6).
* **Long Shortest Chains wrap.** In Bianca Rossi's effective groups 20 of 29 rows hold a chain of
  4–6 hops that wraps to a second line (62.6 px). Names never stack a SID; the one-line rule
  covers directory rows, SIDs and paths.
* **Horizontal scroll at 1360 px** in the Users and Groups directories (inside their own
  container), accepted in `adg-next16-ui-integration-02.md` §9.
* **`/favicon.ico` is a 404** (pre-existing).
* **Only Chromium** was run.
* Pre-existing staging limits (`adg-free-edge-ssr-03.md` §11): 85 s cold start, 0.1 CPU queueing,
  shared writable demo.

## 13. To finish the release

1. **Decide `main`** (§9): merge this candidate now, or after the Next.js security release.
2. **Deploy**, from a workstation as every previous lane did (`docs/deployment/cloudflare-demo.md`
   §6), or configure the `cloudflare-staging` environment's 5 secrets and 3 variables so the
   workflow does it:
   1. Fast-forward `deploy/ADG-FREE-STAGING-01` from `3e26e4a` to the candidate (no rewrite).
   2. Render: deploy that commit; wait until it is live.
   3. Edge assets: `docker build --platform linux/amd64 -f docker/Dockerfile.render --target web
      --build-arg RENDER_GIT_COMMIT=<sha>`, its `.next/static` → `npm run cf:assets`.
   4. `npx wrangler deploy --var ADG_RENDER_ORIGIN:https://adg-demo-web.onrender.com`.
3. **Hosted gates:** `smoke.mjs` (with `RENDER_ORIGIN`), `browser.mjs`, `navigation.mjs`,
   `directory-access.mjs`, `release.mjs` against the public URL; Worker CPU from analytics; a
   read-only `verify` (expect `228b9dce…f9efd0`).
4. If any hosted gate fails materially, roll back with §10.

## 14. Verdict

**`ADG_UI_EDGE_DIRECTORY_INTEGRATION_01_BLOCKED`**

The integration itself is done and validated. The candidate is
`READY_FOR_ADG_UI_EDGE_DIRECTORY_RELEASE_WITH_LIMITATIONS` (§8): every local and CI gate is green,
and every browser gate passes through the real Worker and origin gate, including the actual
Render image under Render Free limits. The release did not happen:

1. **Deployment and hosted validation** need credentials and network access this environment
   does not have (§9). The workflow could do it once its environment is configured.
2. **`main`** waits for the owner's decision, because the repository's own precondition (the
   Next.js security release) is unmet (§9, §12).

**The public application is unchanged by this lane.** The latest recorded deployment is
`33cca01` (Worker `66663d4a`, Render `dep-db6pe17f3r2c73c0e8f0`), which has the UI and EDGE work
but not Directory Access. This container could not confirm it is still the live version. §13
lists the steps that finish the release; §10 is the rollback point for when it happens.
