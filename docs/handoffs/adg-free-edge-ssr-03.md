# ADG-FREE-EDGE-SSR-03 — Next.js off the Worker; one Render Free service behind one gate

**Verdict:** `READY_FOR_ADG_FREE_PUBLIC_STAGING_REVIEW_WITH_LIMITATIONS`. The Worker no longer
renders: it serves static files from the edge and proxies everything else, at **1–3 ms of CPU**
(median 1 ms; 2 of 1,881 invocations over 10 ms; no `exceededCpu`) against the 28–478 ms it
used before. Next.js and the API now run in **one Render Free service** behind one origin gate
that refuses every direct request. The database is unchanged and both fingerprints are exact.
Smoke passes 23/23, and rollback was drilled. Limitations (§11): React hydration error #418 in 2
of 3 browser-gate runs (the third passed 22/22) is not yet explained; a cold start takes 85 s
(was 73 s).

Operator guide: [`docs/deployment/cloudflare-demo.md`](../deployment/cloudflare-demo.md).
Previous handoff (superseded architecture, kept as evidence):
[`adg-free-public-staging-02.md`](adg-free-public-staging-02.md).

## 1. Why

On Workers Free (10 ms of CPU per request), the Worker rendered Next.js through OpenNext:
server-rendered pages used **28–136 ms of CPU at the median and up to 478 ms**. The API proxy
path used 1–4 ms (previous handoff §6). The pages worked only within Cloudflare's tolerance:
the architecture violated the documented limit on almost every page request (status
`BLOCKED_ON_CLOUDFLARE_FREE_LIMIT`). Workers Paid and Cloudflare Containers were not an option.

## 2. Commits (branch `deploy/ADG-FREE-EDGE-SSR-03`, from `643da21`)

| Commit | What |
| --- | --- |
| `5617e1e` | Next.js renders on Render: `docker/Dockerfile.render` (one image), `frontend/render/gate.ts` (front gate + supervisor), thin Worker (`routing.ts`, `environment.ts`, `worker.ts`, `wrangler.jsonc`), `cloudflare/assets.mjs`; OpenNext and the Worker-only API transport removed (`lib/api/client.ts` is `main`'s again); smoke's direct-origin check covers pages; workflow rewritten. |
| `df6556f` | Faster start on 0.1 CPU: no route preloading in the standalone build; API bytecode compiled into the image. |
| (this commit) | docs: deployment guide and this handoff. |

`main` (`1ab01a2`) and `deploy/ADG-FREE-STAGING-01` (`643da21`) are untouched.

## 3. Architecture as deployed

| Tier | Where | Plan | Notes |
| --- | --- | --- | --- |
| Worker `adg-demo-staging` | <https://adg-demo-staging.d-walkes.workers.dev> | Workers **Free** | Version `f5d9868c-6767-4e28-9faa-92000bc40899` (tag `df6556f17ce2`). Bundle 4.00 KiB (1.69 KiB gzip), startup 4 ms. Bindings: `ADG_ORIGIN_KEY` (`secret_text`), `ADG_RENDER_ORIGIN` (`plain_text`), nothing else. Static assets: 57 files from the `web` stage of `df6556f`. Compatibility flag `global_fetch_strictly_public` only. |
| Render `adg-demo-web` | <https://adg-demo-web.onrender.com> (`srv-db6jbmlg1s2s739i4fsg`) | Render **Free**, Oregon, 1 instance | `docker/Dockerfile.render` at `df6556f` (deploy `dep-db6jghqvcj2c7386rptg`). One public listener (the gate, `0.0.0.0:10000`); Next.js on `127.0.0.1:3000`, FastAPI on `127.0.0.1:8000` (in the same image run locally, `/proc/net/tcp` lists exactly these three listeners). Health `/health/live`, auto-deploy off, 12 settings: the 11 of `adg-demo-api` + `ADG_WEB_URL`. |
| Render `adg-demo-api` | `srv-db6hltivcj2c73fujv30` | Render Free | The previous API-only service, **suspended** on 2026-10-12 after validation, kept for rollback (§12); not deleted. |
| Database | existing Supabase project, `adg_demo`, session pooler | existing | Unchanged: no reset, no new database, no credential change. |

Request path: static files → Cloudflare edge (the Worker does not run); everything else →
Worker (key replaced, hop-by-hop headers dropped) → gate (constant-time key check) → FastAPI
for `/api/v1/…`, `/auth/…`, `/health/…`, `/version`, `/runtime`; Next.js for the rest → its
server-side calls to FastAPI over loopback.

## 4. Origin gate and spoofing (hosted)

| Case | Result |
| --- | --- |
| Direct to Render, no key: `/`, `/demo`, `/risks`, `/status`, `/runtime`, `/version`, `/health/ready`, `/auth/config`, `/auth/me`, `/api/v1/demo`, `/api/v1/risks/summary`, `/api/adg/api/v1/demo`, `/_next/static/…`, `/health/live/` | 403 `This service answers only its staging proxy.` |
| Direct `POST /auth/dev/login`, `POST /api/auth/dev-login` | 403 |
| Direct with a guessed key (64 hex, 64 base64, 1 character) on `/` and `/runtime` | 403 |
| `/health/live` | 200 `{"status":"alive","version":"0.1.0"}` (nothing else) |
| Keyed (operator) | 200, demo / synthetic / null-meridian-v1 |
| Visitor sends no key / a wrong key / the right key, through the Worker | the Worker's key replaces it in every case (unit tests `routing.test.ts`; wrong and empty keys also hosted: 200) |
| Gate without a key | exits 1 before starting anything (unit test runs the real file; also checked in the image) |
| Worker without a key | 503 naming the setting, reaches nothing (unit test) |

Comparison: SHA-256 of both values, then `crypto.timingSafeEqual`. The key reaches neither
FastAPI nor Next.js (environment and header, unit-tested). The smoke checks repeat the
direct-origin checks on every deploy (12 refused requests).

## 5. Public authentication (unchanged policy, re-checked)

Anonymous: shell, Demo indicator, `/runtime`, `/version`, `/health/*`, `/auth/config`; every
data route 401. Sign-in offers exactly `planner, viewer`; `admin`, `auditor`, `reviewer`,
`governance`, `approver` → 404. `planner` holds `remediation_planner`, never `admin`; 8
privileged writes → 403; ingestion `403 demo_only` (anonymous and planner); no remediation
execution route. Configuration copied unchanged from `adg-demo-api` (`ADG_DEV_AUTH_USERS`
identical).

## 6. Worker CPU (the gate this task exists for)

**Representative load**, scripted, through the public URL, signed in as `planner`
(`wrangler tail`, 186 invocations captured): each of `/`, `/demo`, a principal page, a share
page, `/risks`, `/simulations`, an access explanation and `/login`, plus 6 API paths, 3 times;
then **120 sequential page requests** across the 8 pages; then **3 bursts of 10 concurrent**
access explanations. All 195 requests answered 200, none with an API failure page.

| Invocations | CPU median | p95 | p99 | max | over 10 ms |
| --- | --- | --- | --- | --- | --- |
| page proxy (179) | 1 ms | 2 ms | 2 ms | 3 ms | 0 |
| API proxy (7) | 1 ms | 1 ms | 1 ms | 1 ms | 0 |
| static files | not invoked: 0 `/_next/` paths reached the Worker | | | | |

**Everything since cutover**, Cloudflare analytics (`workersInvocationsAdaptive`), 19:49:40–20:08
UTC: 1,881 invocations of `f5d9868c`, including the browser gate runs (whose Next.js link
prefetching makes most of them) and another visitor's browsing.

| Status | Requests | p50 | p90 | p99 | p99.9 | max |
| --- | --- | --- | --- | --- | --- | --- |
| `success` | 1,768 | 1.37 ms | 1.93 ms | 2.70 ms | 7.80 ms | 22.96 ms |
| `clientDisconnected` | 113 | 1.23 ms | 1.63 ms | 2.31 ms | 17.08 ms | 17.08 ms |

* **Over the 10 ms limit: 2 of 1,881 invocations (0.11 %)**, one at 19:55 (17.1 ms) and one at
  19:57 (23.0 ms, in a minute of 412 requests whose p99 was 2.3 ms), both during browser-gate
  prefetch bursts. Cloudflare documents tolerance for a Worker that runs over "infrequently".
* **`exceededCpu`: 0.** Errors: 0. Exceptions: 0.
* `canceled` / `clientDisconnected` outcomes are clients closing connections (40 of the tail's
  41 were the Node load client, after reading complete 200 responses), at ≤ 1 ms of CPU.
* For contrast, the previous version `a4946d8e` over 18:42–19:49 (64 requests): p50 19.5 ms,
  p90 219.5 ms, max 337.8 ms.

## 7. Render

**Cold start** (measured 2026-10-12, after 17 idle minutes, confirmed by zero Worker invocations
from 20:17:00 to 20:34:14; client in the US East, log times from Render):

| Event | UTC | Since the request |
| --- | --- | --- |
| `GET /` sent through the Worker | 20:34:21.6 | 0 |
| container started (`adg.gate.starting`) | 20:34:32.0 | +10.4 s |
| Next.js ready | 20:34:36.1 | +14.4 s |
| API imported, uvicorn started | 20:34:55.6 | +34.0 s |
| API ready (Demo Mode startup check done), port open | 20:35:35.3 | +73.7 s |
| first rendered page delivered: HTTP 200, Demo indicator, no API failure | 20:35:46.8 | **+85.2 s** |
| first API answer through the Worker (`GET /runtime`, 200) | | +85.6 s |

One service woke; nothing else did (the Worker has no other origin, and `adg-demo-api` received
no request). The Worker waited the 85 s and returned the page. Previous architecture: 73 s
(Worker SSR + API-only service). The extra time comes from two runtimes starting on one 0.1 CPU,
the larger image, and the first page render after the port opens (11 s: the route loads on first
use, and the API opens its first database connections).

**Starts** (gate log, `after_ms` from the gate's own start):

| Start | Next.js ready | API ready = port open | First answer |
| --- | --- | --- | --- |
| first deploy (`5617e1e`) | 3.6 s | 75.8 s | 78.7 s |
| deploy `df6556f` (no route preloading, compiled bytecode) | 3.8 s | 70.2 s | 72.8 s |
| restart (`POST /restart`) | 4.2 s | 64.8 s | 73.0 s |

The API's Demo Mode startup check (it regenerates the dataset in Python) takes about 43 s of
that on 0.1 CPU, against 36–39 s in `adg-demo-api` alone. Measured at 0.1 CPU locally, Next.js
preloading every route took the CPU the API needed (1.76 s of CPU in its first 40 s → 1.27 s
without), and recompiling the API's 189 modules on each start cost 64 s → 48 s of import. A
restart is zero-downtime: the previous instance serves until the new one is up, then the
gate stops both of its processes (Next.js exit 0, API on SIGTERM).

**Warm** (through the Worker, from the US East, `planner`): `/` 0.4 s median (0.6 s max),
`/demo` 0.5 s, `/login` 0.35 s, `/simulations` 0.4 s, principal 0.7 s, share 0.7 s, `/risks`
1.5 s, access explanation 1–2 s alone and up to 14.7 s in a burst of 10 (they queue on 0.1 CPU;
none failed); API `/runtime` 0.2 s, `/version` 0.19 s, `/api/v1/demo` 0.22 s; API → database
22 ms (`/health/ready`).

**Resources** (Render metrics): memory 250 MiB idle, 317 MiB peak under the load above, of
512 MiB; CPU up to 0.16 cores in bursts. Locally under `--memory=512m --cpus=0.1` after the
full smoke run: 275 MiB (cgroup); RSS API 190 MB, Next.js 121 MB, gate 95 MB (shared pages
counted in each).

## 8. Database and persistence

No reset was run. `verify` (read-only, from the operator's machine) after each event:

| After | `verify` | Integrated fingerprint | Null Meridian fingerprint |
| --- | --- | --- | --- |
| combined deploy + Worker cutover | ok, 23 probes | `228b9dce…f9efd0` | `db736602…85c28a0`, match |
| Render restart | ok, 23 probes | `228b9dce…f9efd0` | match |
| Worker rollback drill + roll-forward (redeploy) | ok, 23 probes | `228b9dce…f9efd0` | match |
| Render sleep and wake (cold start above) | ok, 23 probes | `228b9dce…f9efd0` | match |

`status`: database `adg_demo`, marker `demo / null-meridian-v1` (marked 2026-10-12T17:33:33Z by
`python -m app.demo_mode reset`, i.e. the original bootstrap), generations g1–g3 applied.

## 9. Hosted validation

* **Smoke** (`smoke.mjs`, the database host and project ref as canaries): **23/23** after
  cutover, **23/23** after the roll-forward. Same 23 checks; the direct-origin check now also
  covers pages, web sign-in and `/health/live`'s exact shape (12 refused requests, was 5).
* **Browser** (`browser.mjs`, headless Chromium 1234 / Playwright 1.62.1, arm64): three full
  runs. **22/22** on the third. On the first two, 21 functional checks passed and "no fatal
  browser errors" failed once each with React error #418 (a hydration mismatch, "server rendered
  HTML didn't match the client"). See §11.
  Scenarios A, B, F (both routes), G (six-level chain), H (simulation editor: "changes nobody's
  access"), risks 3 / 8 / 1957 / 423 / 6995, 11 sections, status, refusals: all pass in every run.
* **Local rehearsal** (image under 512 MB / 0.1 CPU, `wrangler dev` thin Worker, real `adg_demo`):
  direct no-key 403 on every path, keyed 200, smoke **23/23** through Worker → gate → Next.js /
  FastAPI → Supabase.
* **Rollback drill** (§12): smoke **23/23** on the rolled-back architecture.

## 10. Local gates (at `df6556f`)

| Gate | Result |
| --- | --- |
| backend `ruff check` / `ruff format --check` / `mypy app tests` | clean / 400 files formatted / no issues in 399 files |
| backend hermetic suite (`pytest -m "not smoke"`) | 5,887 passed, 12 skipped, 1,215 deselected |
| backend origin gate (`tests/test_origin_gate.py`, the rollback service's gate) | 8 passed |
| frontend `lint` / `typecheck` | clean / clean |
| frontend `vitest` | 36 files, 1,252 tests passed (gate: 38, Worker: 51) |
| frontend `next build` | ok |
| Worker `cf:typecheck` / `wrangler deploy --dry-run` | clean / 4.00 KiB, no bindings beyond vars |
| `actionlint` 1.7.7 | clean |
| secret scan of the diff (database URL, password, origin key, session secret, Render key, wrangler tokens, project ref, pooler host) | 0 hits |

## 11. Known limitations

1. **Intermittent hydration error (#418)** in 2 of 3 browser-gate runs. Not explained yet.
   Checked and ruled out: client components render no dates and no locale- or time-dependent
   text; Next.js's default locale is `en-US` without `LANG`; there is no HTML rewriting between
   Render and the visitor; all 25 static files the pages reference come from the same build.
   The page that throws was not identified. The next step is one browser-gate run that records
   the URL with each page error.
2. **Cold start**: 85 s after 15 idle minutes (§7); visitors wait, they do not error.
3. **0.1 CPU**: concurrent page requests queue (a burst of 10 heavy pages took up to 15 s).
4. **Shared, writable demo**: planner records accumulate until a manual reset (unchanged).
5. **CI not configured**: the `cloudflare-staging` environment holds no secrets; the push of
   this branch runs the workflow, which fails closed at preflight. The variable
   `ADG_API_ORIGIN` is now `ADG_RENDER_ORIGIN`.
6. Rare Worker CPU outliers: 2 of 1,881 invocations over 10 ms (§6), none terminated.

## 12. Rollback (proven)

Drill on 2026-10-12: `wrangler rollback a4946d8e-69d3-4275-a304-38dfdf30258a` (the previous
SSR Worker, pointed at `adg-demo-api`). `/runtime` answered 200 after 73.2 s (the old API's cold
start), the homepage rendered, and smoke passed **23/23** against it. Then
`wrangler rollback f5d9868c-6767-4e28-9faa-92000bc40899` restored the thin proxy (200 at once,
smoke 23/23). The database was not touched. To roll back for real: resume `adg-demo-api` on
Render if it is suspended, then roll the Worker back to `a4946d8e`.

## 13. Cost

Cloudflare: Workers Free, static assets + thin proxy; no paid product (no Workers Paid,
Containers, Access, KV/R2/D1/DO/cron). Render: `adg-demo-web` Free is the only service that
must run; `adg-demo-api` is suspended (no instance hours) and can be resumed for a rollback. Supabase: the existing project, unchanged. Render's
workspace allowance is 750 free instance hours a month; one service awake all month uses about
744 of them.
