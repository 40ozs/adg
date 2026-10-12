# ADG Demo Mode on Cloudflare — free public demo staging

**Status:** deployed and checked — see [the handoff](../handoffs/adg-free-public-staging-02.md)
for the measured results. · **Code:** `frontend/wrangler.jsonc`, `frontend/cloudflare/`,
`frontend/lib/api/transport.ts`, `backend/app/origin_gate.py`,
`.github/workflows/cloudflare-staging.yml` · **Runs on:** [Demo Mode](../architecture/demo-mode.md)
with [Null Meridian](../demo/null-meridian.md).

This is a **public demo staging** deployment of the real ADG application on synthetic data, at
zero hosting cost. It is not a production configuration. Anyone on the internet can open it;
ADG's own sign-in, restricted to two low-privilege accounts, decides what a visitor may do (§8).

* **Cloudflare Access is NOT used.** There is no Zero Trust organization, Access application or
  service token.
* **Cloudflare Containers are NOT used.**
* **Workers Paid is NOT used.** The Worker has no paid binding: no Container, Durable Object,
  cron trigger, KV, R2 or D1.

---

## 1. Architecture

```text
PUBLIC INTERNET  (no Cloudflare Access, no outer identity gate)
      │
      ▼
Cloudflare Workers Free — Worker "adg-demo-staging"        frontend/cloudflare/worker.ts
      │  built static files (.open-next/assets) are served before the Worker runs
      │  routing.ts: /api/v1/…, /auth/…, /health/…, /version, /runtime ─► API
      │              everything else ─► OpenNext (the unmodified Next.js app)
      │                                 its server-side API calls ─► the same API path
      │
      │  HTTPS, X-ADG-Origin-Key: <Worker secret ADG_ORIGIN_KEY>
      │  (a caller's own X-ADG-Origin-Key is replaced; cookies are not forwarded)
      ▼
Render Free — web service "adg-demo-api" (Oregon)
      │  docker/Dockerfile.backend, unmodified, run as `python -m app.origin_gate`
      │  403 to any request without the key, except /health/live (Render's health check)
      │  ADG_RUNTIME_MODE=demo, ADG_DEMO_DATASET=null-meridian-v1
      │
      │  TLS, Supavisor session mode, port 5432
      ▼
Supabase PostgreSQL — database adg_demo ONLY
         deployment_marker = demo / null-meridian-v1
```

One codebase, one image, one schema: Render runs exactly the backend image `docker compose`
runs, and the Worker runs the same Next.js application. The deployment adds infrastructure only:

| Piece | What it is |
| --- | --- |
| `frontend/wrangler.jsonc` | The Worker: OpenNext entry, static assets, a self-reference service binding, observability. No secret, no account id, no paid binding. |
| `frontend/cloudflare/worker.ts` | Worker entry: route each request to the API or to OpenNext; install the server-side API transport. |
| `frontend/cloudflare/routing.ts` | Which paths the API answers; `forwardToApi` sends an API request to Render with the origin key and relays only answers marked `X-ADG-Runtime-Mode: demo` (anything else is a 502). |
| `frontend/cloudflare/environment.ts` | `ADG_API_ORIGIN` + `ADG_ORIGIN_KEY` for the API; the environment Next.js sees (neither of those). |
| `frontend/lib/api/transport.ts` | A seam in the web tier's single API client: on this host its server-side calls go through `forwardToApi` with a 120 s timeout (§7). |
| `backend/app/origin_gate.py` | `python -m app.origin_gate`: the unchanged `app.main:app` behind the origin key (§5). |
| `frontend/cloudflare/checks/` | `smoke.mjs`, `browser.mjs`, `null_meridian_fingerprint.py`: hosted checks (§6). |
| `.github/workflows/cloudflare-staging.yml` | Preflight → gates → (bootstrap) → Render deploy → Worker deploy → hosted checks. |

## 2. Routing

| Path | Served by | Note |
| --- | --- | --- |
| `/api/v1/…` | API (Render) | The API's own routes. Every data route needs a signed-in account (anonymous → 401). Ingestion answers `403 demo_only`. |
| `/auth/…` | API (Render) | `/auth/config`, `/auth/me`, `/auth/dev/login` (development sign-in, public accounts only — §8). |
| `/health/…`, `/version`, `/runtime` | API (Render) | Public metadata; no connection detail (asserted by the smoke checks). |
| built static files | Cloudflare static assets | `/_next/static/…` and `public/` files, served without running the Worker. |
| everything else | Next.js (OpenNext) | `/`, `/demo`, every page, the web tier's own `/api/adg`, `/api/auth`, `/api/simulations`, `/api/watches`. |

Next.js's server-side calls go to `ADG_INTERNAL_API_URL=http://adg-api.internal`. That name is
never resolved: the transport hands each request to `forwardToApi`. The Render origin and its
key are not in the environment Next.js can read.

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
* The database receives `deployment_marker = demo / null-meridian-v1` from the reset (§6), the
  only way it is ever written.

## 4. Configuration

### Render service `adg-demo-api` (environment, set on the service)

| Name | Value |
| --- | --- |
| `ADG_RUNTIME_MODE` | `demo` |
| `ADG_DEMO_DATASET` | `null-meridian-v1` |
| `ADG_DATABASE_URL` | secret (§3) |
| `ADG_REMEDIATION_EXECUTION_MODE` | `disabled` |
| `ADG_ORIGIN_KEY` | secret, ≥ 32 characters; the same value as the Worker's |
| `ADG_ENVIRONMENT` | `development` (development sign-in is refused in `production`) |
| `ADG_AUTH_MODE` | `development` |
| `ADG_DEV_AUTH_SECRET` | secret, ≥ 32 characters; keeps sessions valid across restarts |
| `ADG_DEV_AUTH_USERS` | `viewer:viewer:Demo Viewer,planner:remediation_planner:Demo Remediation Planner` — **the public account list** (§8) |
| `ADG_LOG_LEVEL` / `ADG_LOG_FORMAT` | `INFO` / `json` |

Service settings: plan **free**, region Oregon (next to the Supabase project's `us-west-2`),
runtime Docker, Dockerfile `./docker/Dockerfile.backend`, context `.`, start command
`python -m app.origin_gate --host 0.0.0.0 --port 10000`, health check `/health/live`, branch
`deploy/ADG-FREE-STAGING-01`, auto-deploy off (the workflow deploys a given commit).

Not set, and must not be: collector API keys, OIDC settings, AD/LDAP/SMB/WinRM credentials, alert
destinations, `ADG_REMEDIATION_SIGNING_KEY`. The demo's own startup checks refuse collector keys,
outbound alert sinks and any remediation mode but `disabled`.

### Worker `adg-demo-staging`

| Name | Kind | Value |
| --- | --- | --- |
| `ADG_ORIGIN_KEY` | Worker secret | the same value as Render's |
| `ADG_API_ORIGIN` | var (`--var` at deploy) | `https://adg-demo-api.onrender.com` |
| `ADG_WEB_URL` | var (`--var` at deploy) | `https://adg-demo-staging.<subdomain>.workers.dev` |

`NEXT_PUBLIC_ADG_API_URL` is the staging URL itself (set at build time); the browser never sees
the Render origin or the key.

### GitHub environment `cloudflare-staging` (workflow only)

Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ADG_DATABASE_URL`, `ADG_ORIGIN_KEY`,
`RENDER_API_KEY`. Variables: `ADG_STAGING_URL`, `ADG_API_ORIGIN`, `RENDER_SERVICE_ID`.
Cloudflare API token: the "Edit Cloudflare Workers" template, or at least **Workers Scripts:
Edit** and **Account Settings: Read**, scoped to the one account.

## 5. Origin gate

Render's free tier has no private network or IP allow-list, so the API's address is public.
`python -m app.origin_gate` wraps the unchanged application:

* every HTTP or WebSocket request without `X-ADG-Origin-Key: $ADG_ORIGIN_KEY` gets `403` (or
  WebSocket close 1008) before the application sees it; the only exception is `GET /health/live`,
  which Render's health check calls and which reads nothing;
* the comparison is `hmac.compare_digest`; neither the supplied nor the expected value is logged;
* without a key of at least 32 characters it refuses to start (fail closed).

The Worker deletes any `X-ADG-Origin-Key` the caller sent and sets its own, so a visitor cannot
choose the value the API sees; it also drops the web session cookie, and it relays an answer only
if the API marked it `X-ADG-Runtime-Mode: demo`. The smoke checks call the Render origin directly
on every deploy: no key, or a guessed key, must get 403.

**Rotating the origin key** (a short window of 502s while the two disagree):

```bash
umask 077; openssl rand -base64 48 | tr -d '\n' > ~/.config/adg-staging/adg-origin-key
# 1. Render: dashboard → adg-demo-api → Environment → ADG_ORIGIN_KEY → save (restarts the API)
# 2. Worker: (cd frontend && npx wrangler secret put ADG_ORIGIN_KEY < ~/.config/adg-staging/adg-origin-key)
# 3. GitHub: gh secret set ADG_ORIGIN_KEY --env cloudflare-staging < ~/.config/adg-staging/adg-origin-key
# 4. Run the smoke checks (§6).
```

**Rotating the database password:** Supabase → Project Settings → Database → reset the password;
rebuild `ADG_DATABASE_URL` (URL-encode the password) and update it on Render and in GitHub.
A password that has ever been pasted into a chat, ticket or log is treated as exposed.

## 6. Build, deploy, bootstrap, check

### Deploy

**Actions → Free staging (ADG demo) → Run workflow**, or push to `deploy/ADG-FREE-STAGING-01`:

1. **preflight**: every name in §4's GitHub list present; URLs https; the key ≥ 32 characters;
   the database named for the demo; a bootstrap only with the typed confirmation
   `reset adg_demo`. Prints names, never values.
2. **gates**: Worker typecheck and the Cloudflare unit tests.
3. **bootstrap** (only when asked): `python -m app.demo_mode reset`, `--json verify`,
   `--json status` and the Null Meridian logical fingerprint, from this commit's image.
4. **deploy-api**: a Render deploy of exactly this commit, waited on until live.
5. **deploy**: `opennextjs-cloudflare build`, then `wrangler deploy --secrets-file … --var …`
   (with `OPEN_NEXT_DEPLOY=true`, so wrangler ≥ 4.149 deploys directly instead of re-running
   the command through `opennextjs-cloudflare deploy`, which loses its quoting).
6. **checks**: read-only `verify` + logical fingerprint, then `smoke.mjs` and `browser.mjs`
   against the public URL.

The same deploy from a workstation (wrangler logged in to the account):

```bash
cd frontend
NEXT_PUBLIC_ADG_API_URL=https://adg-demo-staging.<subdomain>.workers.dev npx opennextjs-cloudflare build
OPEN_NEXT_DEPLOY=true npx wrangler deploy \
  --var ADG_WEB_URL:https://adg-demo-staging.<subdomain>.workers.dev \
  --var ADG_API_ORIGIN:https://adg-demo-api.onrender.com
# the secret, once (it persists across deploys):
npx wrangler secret put ADG_ORIGIN_KEY < ~/.config/adg-staging/adg-origin-key
```

### Bootstrap and reset

The demo database is bootstrapped **once** and reset only deliberately. The API never resets:
its startup only verifies that the database is a marked Null Meridian demo and refuses to start
otherwise. There is no reset endpoint. To reset: run the workflow with `bootstrap` and
`confirm_reset = reset adg_demo`, or from any machine with the backend and the secret:

```bash
export ADG_RUNTIME_MODE=demo ADG_DEMO_DATASET=null-meridian-v1 ADG_REMEDIATION_EXECUTION_MODE=disabled
export ADG_DATABASE_URL="$(cat ~/.config/adg-staging/adg-database-url)"
python -m app.demo_mode reset    # then: verify, status
```

A reset ingests 300 scan runs; it takes about 80–90 s next to the database and much longer
across a continent (the handoff records the measured time). It briefly empties the tables the
API serves: do not run it while people are using the demo.

Expected after a reset: `verify` ok, 23/23 probes, 300 scan runs, fingerprint
`228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`; Null Meridian logical
fingerprint `db736602850a4a82936f861b68466e7c54b61a55192b04f6d062cbe8585c28a0`. A different
value is dataset drift until proven otherwise.

### Hosted checks

```bash
STAGING_URL=https://adg-demo-staging.<subdomain>.workers.dev \
API_ORIGIN=https://adg-demo-api.onrender.com node frontend/cloudflare/checks/smoke.mjs
STAGING_URL=https://adg-demo-staging.<subdomain>.workers.dev node frontend/cloudflare/checks/browser.mjs
```

Both act as an anonymous visitor, then as the public `planner` account; `PUBLIC_ACCOUNTS`
(default `viewer,planner`) is the account list they require development sign-in to offer, and
every other default account must be refused.

## 7. Free-tier behaviour: sleep, cold start, CPU

* **Render Free** spins the service down after 15 minutes without inbound traffic; the next
  request waits while it starts. Measured: the first page after a sleep arrived in **73 s**
  (container start about 28 s, Demo Mode startup checks about 37 s, routing about 9 s). The
  Worker gives every server-side API call 120 s (`API_WAKE_TIMEOUT_MS`, this host only), so a
  sleeping API shows as slow, not down. There is no keep-warm cron: free instance hours are a
  monthly allowance per Render workspace, and an always-awake service would spend it.
* **Workers Free** allows 100,000 requests a day and 10 ms of CPU per request. Static files do
  not count (they are served before the Worker runs). The API proxy path uses 1–4 ms.
  **Server-rendered pages use about 30–140 ms at the median (up to about 480 ms)**, consistently
  over the documented limit. Cloudflare allows a Worker to run over "infrequently" and
  terminates one that does so "consistently" (Error 1102). In hosted tests (218 invocations,
  including concurrent bursts) none was terminated. Watch for `exceededCpu` in
  `wrangler tail`; the free fallbacks are in the handoff §8.
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
  `adg.staging.misconfigured` (names settings, never values) and `adg.staging.api_unavailable`.
  An invocation that exceeds the CPU limit shows `"outcome": "exceededCpu"` in the tail.
* API: Render dashboard → `adg-demo-api` → Logs (the API's own JSON log). The API logs no
  `Authorization` header, and neither tier logs the origin key.

## 10. Rollback

The demo database is **never** touched by a rollback.

| Goal | Action |
| --- | --- |
| Take the site offline now | Dashboard → `adg-demo-staging` → Settings → Domains & Routes → disable the workers.dev route (or set `"workers_dev": false` and deploy). |
| Cut the API off without touching the Worker | Render → `adg-demo-api` → Suspend; or change `ADG_ORIGIN_KEY` on Render only (the Worker then gets 403 and answers 502). |
| Restore the previous Worker | `npx wrangler rollback` (Worker versions), or re-run the workflow on the previous good commit. |
| Restore the previous API | Render → `adg-demo-api` → Deploys → roll back to a previous deploy, or re-run the workflow on that commit. |
| Remove staging entirely | `npx wrangler delete adg-demo-staging`; delete the Render service; `DROP DATABASE adg_demo` only if the data is no longer wanted. |

## 11. Cost

| Service | Plan | Cost |
| --- | --- | --- |
| Cloudflare Workers | Free | $0 within the free limits (100k requests/day, 10 ms CPU/request) |
| Cloudflare Access / Zero Trust | not used | — |
| Cloudflare Containers | not used | — |
| Render web service | Free | $0 within the free limits (sleeps when idle; a monthly allowance of free instance hours per workspace) |
| Supabase | existing project | the project's existing plan; `adg_demo` is one more database in it |

Nothing here upgrades itself: Workers Free fails a request that exceeds a limit rather than
billing for it, and a Render free service is suspended, not upgraded, when its free hours run
out.

## 12. Known limitations

* **Shared, writable demo.** Visitors share one database; planner records accumulate until a
  manual reset (§8).
* **Worker CPU.** Server-rendered pages run consistently over the Workers Free 10 ms CPU limit
  and work only within Cloudflare's tolerance (§7).
* **Cold start.** The first request after 15 idle minutes waits about 73 s for Render to start
  the API.
* **No identity.** Development sign-in has no password and records the account name only; it is
  a demo convenience, not authentication. A production deployment uses OIDC
  (`ADG_ENVIRONMENT=production` refuses development sign-in).
* **Single instance.** One free Render instance; no autoscaling.
