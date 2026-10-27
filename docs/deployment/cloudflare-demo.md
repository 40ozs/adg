# ADG Demo Mode on Cloudflare — free public demo staging

**Status:** deployed and checked — see [the handoff](../handoffs/adg-free-edge-ssr-03.md) for
the measured results. · **Code:** `frontend/wrangler.jsonc`, `frontend/cloudflare/`,
`frontend/render/gate.ts`, `docker/Dockerfile.render`, `.github/workflows/cloudflare-staging.yml`
· **Runs on:** [Demo Mode](../architecture/demo-mode.md) with [Genesee Press](../demo/genesee-press.md).

This is a **public demo staging** deployment of the real ADG application on synthetic data, at
zero hosting cost. It is not a production configuration. Anyone on the internet can open it;
ADG's own sign-in, restricted to two low-privilege accounts, decides what a visitor may do (§8).

* **Next.js does NOT render on Cloudflare.** The Worker serves built static files and proxies
  everything else; pages are rendered on Render (§7 explains why).
* **Cloudflare Access is NOT used.** There is no Zero Trust organization, Access application or
  service token.
* **Cloudflare Containers are NOT used.**
* **Workers Paid is NOT used.** The Worker has no binding at all: no Container, Durable Object,
  cron trigger, KV, R2 or D1.

---

## 1. Architecture

```text
PUBLIC INTERNET  (no Cloudflare Access, no outer identity gate)
      │
      ▼
Cloudflare Workers Free — Worker "adg-demo-staging"           frontend/cloudflare/worker.ts
      │  built static files (/_next/static/…, public/) are served from the edge BEFORE the
      │  Worker runs (no invocation, no CPU)
      │  everything else: a thin proxy (about 1–3 ms of CPU); a caller's own X-ADG-Origin-Key
      │  is replaced; API answers must carry X-ADG-Runtime-Mode: demo
      │
      │  HTTPS, X-ADG-Origin-Key: <Worker secret ADG_ORIGIN_KEY>
      ▼
Render Free — ONE web service "adg-demo-web" (Oregon)          docker/Dockerfile.render
      │  $PORT ─► front gate (node gate.ts, frontend/render/gate.ts): the only public listener
      │           403 to any request without the key, except /health/live
      │           ├─ /api/v1/…, /auth/…, /health/…, /version, /runtime ─► FastAPI 127.0.0.1:8000
      │           │                                                       (python -m app, unmodified)
      │           └─ everything else ─► Next.js 127.0.0.1:3000 (standalone server.js)
      │                                 its server-side API calls ─► 127.0.0.1:8000
      │  ADG_RUNTIME_MODE=demo, ADG_DEMO_DATASET=genesee-press-v1
      │
      │  TLS, Supavisor session mode, port 5432
      ▼
Supabase PostgreSQL — database adg_demo ONLY
         deployment_marker = demo / genesee-press-v1
```

One codebase, one schema: Render runs the backend exactly as `docker compose` does, and the
Next.js application unmodified. The deployment adds infrastructure only:

| Piece | What it is |
| --- | --- |
| `frontend/wrangler.jsonc` | The Worker: entry, static assets (`.edge-assets/`), observability. No binding, no secret, no account id. |
| `frontend/cloudflare/worker.ts` | Worker entry: every request reaching it goes to `routeRequest`. 4 KiB bundled; no Next.js runtime. |
| `frontend/cloudflare/routing.ts` | `forwardToOrigin`: the request to Render with the Worker's origin key, hop-by-hop headers removed, the answer relayed unchanged. An API answer without `X-ADG-Runtime-Mode: demo` is a 502. |
| `frontend/cloudflare/environment.ts` | `ADG_RENDER_ORIGIN` + `ADG_ORIGIN_KEY`, validated (https only; loopback http for `wrangler dev`). |
| `frontend/cloudflare/assets.mjs` | `npm run cf:assets -- <.next/static>`: assembles `.edge-assets/` (static files + a `_headers` file marking `/_next/static/*` immutable). |
| `frontend/render/gate.ts` | The front gate and process supervisor on Render (§5). |
| `docker/Dockerfile.render` | One image: the Next.js standalone build, the API as `Dockerfile.backend` installs it, Node for the gate. |
| `backend/app/origin_gate.py` | The previous API-only gate (`python -m app.origin_gate`), kept for the rollback service `adg-demo-api` (§10). |
| `frontend/cloudflare/checks/` | `smoke.mjs`, `browser.mjs`, `genesee_press_fingerprint.py`: hosted checks (§6). |
| `.github/workflows/cloudflare-staging.yml` | Preflight → gates → (bootstrap) → Render deploy → static files + Worker deploy → hosted checks. |

## 2. Routing

| Path | Served by | Note |
| --- | --- | --- |
| built static files | Cloudflare static assets | `/_next/static/…` and `public/` files, without running the Worker. A file the edge does not hold reaches the Worker and is proxied to Render, which holds every file of its own build. |
| `/api/v1/…`, `/auth/…` | FastAPI on Render | The API's own routes. Every data route needs a signed-in account (anonymous → 401). Ingestion answers `403 demo_only`. The gate drops the web session cookie. |
| `/health/…`, `/version`, `/runtime` | FastAPI on Render | Public metadata; no connection detail (asserted by the smoke checks). |
| everything else | Next.js on Render | `/`, `/demo`, every page, the web tier's own `/api/adg`, `/api/auth`, `/api/simulations`, `/api/watches`. The gate tells Next.js the public host (`X-Forwarded-Host` from `ADG_WEB_URL`), so Server Actions accept the browser's `Origin`. |

Next.js's server-side calls go to `ADG_INTERNAL_API_URL=http://127.0.0.1:8000`, inside the
container. The browser only ever talks to the staging URL.

**Static files and builds.** The Worker's static files must come from the same build Render
serves, or the chunk names in Render's HTML will not match. The workflow builds the same
Dockerfile stage (`--target web`) from the same commit for the same platform and copies its
`.next/static`. Two Next.js builds are never byte-identical, because Server Action IDs are drawn at
random per build. A mismatched file is therefore proxied to Render: correct, merely not
edge-served. A Mac build, by contrast, differs from a Linux one in most chunk names.

## 3. PostgreSQL (Supabase)

* A **dedicated** database, **`adg_demo`**, in an existing Supabase project; never an ADG
  production database. The name must contain `demo` as a word: the API, the reset and the
  workflow's preflight all refuse otherwise, and that rule is not weakened here.
* Connect through the **Session pooler** (Supabase → Connect → Session pooler), not the direct
  host. The direct host `db.<ref>.supabase.co` is IPv6-only, and neither Render nor GitHub's
  runners can reach it. Session mode keeps one server connection per client connection, so
  psycopg 3's prepared statements and the reset's single transaction work as on a direct
  connection; the transaction pooler (port 6543) does not, and is not used.
* `ADG_DATABASE_URL=postgresql+psycopg://postgres.<ref>:<url-encoded password>@aws-<n>-<region>.pooler.supabase.com:5432/adg_demo?sslmode=require`
* Create the database once, from the project's default database:
  `CREATE DATABASE adg_demo;` then reconnect to `adg_demo` and check
  `SELECT current_database();` before the first reset.
* The database receives `deployment_marker = demo / genesee-press-v1` from the reset (§6), the
  only way it is ever written.

## 4. Configuration

### Render service `adg-demo-web` (environment, set on the service)

| Name | Value |
| --- | --- |
| `ADG_RUNTIME_MODE` | `demo` |
| `ADG_DEMO_DATASET` | `genesee-press-v1` |
| `ADG_DATABASE_URL` | secret (§3) |
| `ADG_REMEDIATION_EXECUTION_MODE` | `disabled` |
| `ADG_ORIGIN_KEY` | secret, ≥ 32 characters; the same value as the Worker's. Read by the gate only; neither FastAPI nor Next.js receives it. |
| `ADG_WEB_URL` | `https://adg-demo-staging.<subdomain>.workers.dev`: the public address (Next.js's own URL, secure cookies, forwarded host) |
| `ADG_ENVIRONMENT` | `development` (development sign-in is refused in `production`) |
| `ADG_AUTH_MODE` | `development` |
| `ADG_DEV_AUTH_SECRET` | secret, ≥ 32 characters; keeps sessions valid across restarts |
| `ADG_DEV_AUTH_USERS` | `viewer:viewer:Demo Viewer,planner:remediation_planner:Demo Remediation Planner` — **the public account list** (§8) |
| `ADG_LOG_LEVEL` / `ADG_LOG_FORMAT` | `INFO` / `json` |

Next.js receives only `ADG_WEB_URL`, `NEXT_PUBLIC_ADG_API_URL` (= `ADG_WEB_URL`),
`ADG_INTERNAL_API_URL`, `PATH`, `HOME` and its own port settings (`webEnvironment` in
`gate.ts`): never the database URL, the session secret or the origin key.

Service settings: plan **free**, region Oregon (next to the Supabase project's `us-west-2`),
runtime Docker, Dockerfile `./docker/Dockerfile.render`, context `.`, no start command (the
image's `node gate.ts`), health check `/health/live`, branch `deploy/ADG-HOSTED-NEXT16-01`,
auto-deploy off (the workflow deploys a given commit). Render passes the commit to the build as
`RENDER_GIT_COMMIT`, which becomes the Next.js build ID.

Not set, and must not be: collector API keys, OIDC settings, AD/LDAP/SMB/WinRM credentials, alert
destinations, `ADG_REMEDIATION_SIGNING_KEY`. The demo's own startup checks refuse collector keys,
outbound alert sinks and any remediation mode but `disabled`.

### Worker `adg-demo-staging`

| Name | Kind | Value |
| --- | --- | --- |
| `ADG_ORIGIN_KEY` | Worker secret | the same value as Render's |
| `ADG_RENDER_ORIGIN` | var (`--var` at deploy) | `https://adg-demo-web.onrender.com` |

### GitHub environment `cloudflare-staging` (workflow only)

Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ADG_DATABASE_URL`, `ADG_ORIGIN_KEY`,
`RENDER_API_KEY`. Variables: `ADG_STAGING_URL`, `ADG_RENDER_ORIGIN`, `RENDER_SERVICE_ID` (the
`adg-demo-web` service id). Cloudflare API token: the "Edit Cloudflare Workers" template, or at
least **Workers Scripts: Edit** and **Account Settings: Read**, scoped to the one account.

## 5. Origin gate

Render's free tier has no private network or IP allow-list, so the service's address is public.
The front gate (`frontend/render/gate.ts`) is its only public listener:

* every request without `X-ADG-Origin-Key: $ADG_ORIGIN_KEY` gets `403 {"detail": "This service
  answers only its staging proxy."}`, whether it is for a page, the web tier's API routes or the
  API. The only exception is `/health/live` (exact path), which Render's health check calls. It is
  relayed to FastAPI as exactly that path and answers `{"status", "version"}` without reading
  anything;
* the comparison hashes both values with SHA-256 and compares them with `timingSafeEqual`, so
  it takes the same time whatever the supplied value's length; a repeated header is no key;
  neither value is logged;
* without a key of at least 32 characters it refuses to start (fail closed), before starting
  either process;
* it forwards no hop-by-hop header and never passes the key on; WebSocket upgrades are refused.

The gate also supervises the two processes. It opens the public port only once both accept
connections; Render's health check fails until then. When either process exits, the gate stops
the other one and exits, so Render restarts the service as a whole. On `SIGTERM` it stops both
processes and exits.

The Worker sets its own key on every request and replaces any key a caller sent, so a visitor
cannot choose the value Render sees. The smoke checks call the Render origin directly on every
deploy: with no key or a guessed one, pages, web sign-in, API and sign-in all get 403.

**Rotating the origin key** (a short window of 403s while the two disagree):

```bash
umask 077; openssl rand -base64 48 | tr -d '\n' > ~/.config/adg-staging/adg-origin-key
# 1. Render: dashboard → adg-demo-web → Environment → ADG_ORIGIN_KEY → save (restarts the service)
# 2. Worker: (cd frontend && npx wrangler secret put ADG_ORIGIN_KEY < ~/.config/adg-staging/adg-origin-key)
# 3. GitHub: gh secret set ADG_ORIGIN_KEY --env cloudflare-staging < ~/.config/adg-staging/adg-origin-key
# 4. Run the smoke checks (§6).
```

**Rotating the database password:** Supabase → Project Settings → Database → reset the password;
rebuild `ADG_DATABASE_URL` (URL-encode the password) and update it on Render and in GitHub.
A password that has ever been pasted into a chat, ticket or log is treated as exposed.

## 6. Build, deploy, bootstrap, check

### Deploy

**Actions → Free staging (ADG demo) → Run workflow**, or push to `deploy/ADG-HOSTED-NEXT16-01`:

1. **preflight**: every name in §4's GitHub list present; URLs https; the key ≥ 32 characters;
   the database named for the demo; a bootstrap only with the typed confirmation
   `reset adg_demo`. Prints names, never values.
2. **gates**: Worker typecheck, the Worker and gate unit tests.
3. **bootstrap** (only when asked): `python -m app.demo_mode reset`, `--json verify`,
   `--json status` and the Genesee Press logical fingerprint, from this commit's API image.
4. **deploy-origin**: a Render deploy of exactly this commit (Render builds
   `Dockerfile.render`), waited on until live.
5. **deploy**: `docker build --target web` of the same commit, its `.next/static` →
   `npm run cf:assets`, then `wrangler deploy --secrets-file … --var ADG_RENDER_ORIGIN:…`.
6. **checks**: read-only `verify` + logical fingerprint, then `smoke.mjs` and `browser.mjs`
   against the public URL.

The same deploy from a workstation (wrangler logged in to the account; Docker running):

```bash
# 1. Render: deploy the pushed commit (dashboard → adg-demo-web → Manual Deploy → that commit,
#    or POST /v1/services/<id>/deploys {"commitId": "<sha>"}), and wait until it is live.
# 2. The Worker's static files, from the same Dockerfile stage and platform as Render's build:
docker build --platform linux/amd64 -f docker/Dockerfile.render --target web \
  --build-arg RENDER_GIT_COMMIT=<sha> -t adg-web-build .
id=$(docker create --platform linux/amd64 adg-web-build)
docker cp "$id:/app/frontend/.next/static" frontend/.next-static && docker rm "$id"
(cd frontend && npm run cf:assets -- .next-static && rm -rf .next-static)
# 3. The Worker (the secret persists across deploys once set):
cd frontend
npx wrangler deploy --var ADG_RENDER_ORIGIN:https://adg-demo-web.onrender.com
npx wrangler secret put ADG_ORIGIN_KEY < ~/.config/adg-staging/adg-origin-key   # first time only
```

**Local rehearsal** of the whole path, without deploying anything:

```bash
docker build -f docker/Dockerfile.render -t adg-web:local .
docker run --rm --memory=512m --cpus=0.1 --env-file <the service's settings> \
  -e ADG_WEB_URL=http://localhost:8787 -p 127.0.0.1:10000:10000 adg-web:local
# frontend/.dev.vars (git-ignored, mode 600): ADG_ORIGIN_KEY=<the key>
(cd frontend && npx wrangler dev --var ADG_RENDER_ORIGIN:http://127.0.0.1:10000)
STAGING_URL=http://127.0.0.1:8787 RENDER_ORIGIN=http://127.0.0.1:10000 node frontend/cloudflare/checks/smoke.mjs
```

`--memory=512m --cpus=0.1` matches a Render free instance. Locally, `wrangler dev` passes Next.js's
gzip through to a client that did not ask for it; Cloudflare's edge does not (it decompresses
for such a client).

### Bootstrap and reset

The demo database is bootstrapped **once** and reset only deliberately. The API never resets:
its startup only verifies that the database is a marked Genesee Press demo and refuses to start
otherwise. There is no reset endpoint. To reset: run the workflow with `bootstrap` and
`confirm_reset = reset adg_demo`, or from any machine with the backend and the secret:

```bash
export ADG_RUNTIME_MODE=demo ADG_DEMO_DATASET=genesee-press-v1 ADG_REMEDIATION_EXECUTION_MODE=disabled
export ADG_DATABASE_URL="$(cat ~/.config/adg-staging/adg-database-url)"
python -m app.demo_mode reset    # then: verify, status
```

A reset ingests 300 scan runs; it takes about 80–90 s next to the database and much longer
across a continent (29 min from the US East). It briefly empties the tables the API serves: do
not run it while people are using the demo. Moving compute (as ADG-FREE-EDGE-SSR-03 did) needs
no reset: run `verify` instead.

Expected after a reset: `verify` ok, 23/23 probes, 300 scan runs, fingerprint
`4667d3cfb4b574ffda3ac7ea8453912568b7517cd53548fb338b497e9b270f08`; Genesee Press logical
fingerprint `b4b93b65a77d895cbd0495d34865937bdab39596494bd3ca035ce8f8e59445d0`. A different
value is dataset drift until proven otherwise.

### Hosted checks

```bash
STAGING_URL=https://adg-demo-staging.<subdomain>.workers.dev \
RENDER_ORIGIN=https://adg-demo-web.onrender.com node frontend/cloudflare/checks/smoke.mjs
STAGING_URL=https://adg-demo-staging.<subdomain>.workers.dev node frontend/cloudflare/checks/browser.mjs
```

Both act as an anonymous visitor, then as the public `planner` account; `PUBLIC_ACCOUNTS`
(default `viewer,planner`) is the account list they require development sign-in to offer, and
every other default account must be refused.

**Worker CPU** (the reason for this architecture): `npx wrangler tail adg-demo-staging --format
json` gives `cpuTime` and `outcome` per invocation. Cloudflare's GraphQL dataset
`workersInvocationsAdaptive` gives CPU quantiles and the maximum per status and version (the
wrangler OAuth token can read it; send a custom `User-Agent`, since Cloudflare refuses
`Python-urllib`). Any `exceededCpu` status is a regression.

## 7. Free-tier behaviour: CPU, sleep, cold start

* **Workers Free** allows 100,000 requests a day and **10 ms of CPU per request**. Static files
  do not count (they are served before the Worker runs). The Worker only proxies:
  **median about 1 ms, p99 under 3 ms** (handoff §6).
  *History:* until ADG-FREE-EDGE-SSR-03 the Worker also rendered Next.js (OpenNext), and
  server-rendered pages used about 30–140 ms at the median (up to about 480 ms), consistently
  over the limit. That architecture was not compliant, even though no request failed. It is
  why rendering moved to Render. The measurements are in
  [adg-free-public-staging-02.md](../handoffs/adg-free-public-staging-02.md) §6.
* **Render Free** gives one instance with 512 MB and 0.1 CPU. Next.js and the API share it: about
  275 MB in use after the full smoke run, measured locally under the same limits. Pages take
  0.3–1.5 s warm; concurrent requests queue on the 0.1 CPU. Ten simultaneous access explanations
  took up to about 15 s, and all answered. The standalone build loads each route on first use
  instead of preloading them all (`next.config.mjs`), and the API's bytecode is compiled into
  the image, both to keep the start short.
* **Sleep and cold start**: Render spins the service down after 15 minutes without inbound
  traffic, and the next request waits while it starts. One service wakes, not two: the first page
  request starts Next.js and the API together, and the gate opens the port only once both
  are ready. Measured: the first page arrived **85 s** after the request (container +10 s, Next.js
  +14 s, API and port +74 s, page +85 s). The longest phase is the API's Demo Mode startup
  check, which regenerates the dataset in Python. There is no keep-warm cron: free instance hours
  are a monthly allowance per Render workspace (§11), and an always-awake service would spend it.
* **Supabase**: the session pooler's connection limit is shared by every client of the project;
  the API holds a pool of 5.

## 8. Public access and the demo account policy

There is no outer gate, so ADG's own sign-in is the policy. The deployment runs development
sign-in (no password) restricted by `ADG_DEV_AUTH_USERS` to two accounts:

| Visitor | Can | Cannot |
| --- | --- | --- |
| anonymous | load the page shells, the Demo indicator, `/runtime`, `/version`, `/health/*`, `/auth/config` | read any ADG data (every `/api/v1` data route → 401), write anything |
| `viewer` | read the estate: resources, identities, access explanations, risks, changes, collector status | write anything |
| `planner` (`remediation_planner`) | everything `viewer` reads, plus governance, simulation and remediation reads; **run, store, re-run and delete simulations**; **write, edit, simulate, submit and cancel change plans** | approve a plan (no approver is public), export a runbook, manage alert watches, run governance campaigns or review decisions, ingest scans (`403 demo_only` for everyone), execute remediation (no such route) |

`admin`, `auditor`, `reviewer`, `governance` and `approver` are not in the list: signing in as
any of them is a 404, and the smoke checks assert that on every deploy.

**Demo drift.** `planner` writes persist in `adg_demo` and are visible to every visitor:
stored simulations and their evaluations, and change plans (draft, submitted or cancelled).
A visitor can also delete a stored simulation another visitor made. None of this touches the
estate data: the integrated fingerprint covers the current state, object versions, scan runs
and risk findings, so `verify` stays green however much visitors use the planner. To clear
visitor records, run a reset (§6). Resets are manual; nothing is scheduled. A weekly reset,
or one before any public showing, is a reasonable cadence; scheduling one is the owner's
decision.

Change `ADG_DEV_AUTH_USERS` on the Render service to change the public accounts. Never make
`admin` public: it can manage alert watches, drain alert deliveries and export runbooks.

## 9. Logs and diagnosis

* Worker: dashboard → Workers & Pages → `adg-demo-staging` → Logs (Workers Logs, full
  sampling), or `npx wrangler tail adg-demo-staging --format json` live. Structured events:
  `adg.staging.misconfigured` (names settings, never values) and
  `adg.staging.origin_unavailable`. An invocation over the CPU limit shows
  `"outcome": "exceededCpu"`.
* Render: dashboard → `adg-demo-web` → Logs. The gate logs `adg.gate.starting`,
  `adg.gate.child_ready` (with `after_ms`), `adg.gate.listening`, `adg.gate.first_answer`,
  `adg.gate.child_exited` and `adg.gate.upstream_unavailable`. FastAPI logs its own JSON lines;
  Next.js logs its start. The API logs no `Authorization` header, and nothing logs the origin key.

## 10. Rollback

The demo database is **never** touched by a rollback.

| Goal | Action |
| --- | --- |
| Take the site offline now | Dashboard → `adg-demo-staging` → Settings → Domains & Routes → disable the workers.dev route (or set `"workers_dev": false` and deploy). Turning the route off in the dashboard can disable it entirely (error 1042); re-enable with `POST /accounts/{id}/workers/scripts/adg-demo-staging/subdomain {"enabled": true}`. |
| Back to the previous architecture (Worker renders, API-only Render service) | Resume `adg-demo-api` on Render if it is suspended, then `npx wrangler rollback a4946d8e-69d3-4275-a304-38dfdf30258a`. That version carries its own `ADG_API_ORIGIN` and secret. Its first request waits for `adg-demo-api` to wake (about 73 s). It runs Next.js in the Worker, over the CPU limit (§7). |
| Restore a previous thin-proxy Worker | `npx wrangler rollback <version>` (Worker versions), or re-run the workflow on that commit. |
| Restore a previous Render build | Render → `adg-demo-web` → Deploys → roll back, or re-run the workflow on that commit. |
| Cut the origin off without touching the Worker | Render → `adg-demo-web` → Suspend; or change `ADG_ORIGIN_KEY` on Render only (the gate then answers the Worker 403, and API paths become 502). |
| Remove staging entirely | `npx wrangler delete adg-demo-staging`; delete the Render services; `DROP DATABASE adg_demo` only if the data is no longer wanted. |

## 11. Cost

| Service | Plan | Cost |
| --- | --- | --- |
| Cloudflare Workers | Free: static assets + a thin proxy | $0 within the free limits (100k requests/day, 10 ms CPU/request; static files are not requests to the Worker) |
| Cloudflare paid products (Workers Paid, Containers, Access / Zero Trust) | not used | — |
| Render web service `adg-demo-web` | Free (the only one that must stay enabled) | $0 |
| Render web service `adg-demo-api` | Free, **suspended** (rollback only) | $0; a suspended service uses no instance hours |
| Supabase | existing project | the project's existing plan; `adg_demo` is one more database in it |

Render's free instance hours are a monthly allowance shared by every free service in the
workspace (750 hours a month at the time of writing). An instance uses them only while it is
awake. One always-awake service would use about 744 hours in a 31-day month, so a second free
service kept awake would exhaust the allowance. When the allowance runs out, Render suspends free
services until the next month; it does not upgrade them.

Nothing here upgrades itself: Workers Free fails a request that exceeds a limit rather than
billing for it, and a Render free service is suspended, not upgraded.

## 12. Known limitations

* **Shared, writable demo.** Visitors share one database; planner records accumulate until a
  manual reset (§8).
* **Cold start.** The first request after 15 idle minutes waits about 85 s for the whole service
  to start (handoff §7).
* **0.1 CPU.** Pages render on a 0.1-CPU instance: fine for one visitor at a time, slow under
  bursts (requests queue; none failed in testing).
* **No identity.** Development sign-in has no password and records the account name only; it is
  a demo convenience, not authentication. A production deployment uses OIDC
  (`ADG_ENVIRONMENT=production` refuses development sign-in).
* **Single instance.** One free Render instance; no autoscaling.
