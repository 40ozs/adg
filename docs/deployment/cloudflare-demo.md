# ADG Demo Mode on Cloudflare — protected staging

**Status:** deployment path implemented and rehearsed end to end locally; **not yet deployed**
(no Cloudflare or database credentials were available to the session that built it — see
[the handoff](../handoffs/adg-cloudflare-demo-01.md)). · **Code:** `frontend/wrangler.jsonc`,
`frontend/cloudflare/`, `frontend/lib/api/transport.ts`, `frontend/open-next.config.ts`,
`.github/workflows/cloudflare-staging.yml` · **Runs on:** [Demo Mode](../architecture/demo-mode.md)
with [Null Meridian](../demo/null-meridian.md).

This is a **protected staging** deployment of the real ADG application on synthetic data. It is
not a production configuration and not a public launch: every request must pass Cloudflare
Access, and the Worker refuses to serve anybody when Access is not configured. A public demo is
a separate owner decision (§11).

---

## 1. Architecture

```text
Internet
   │
   ▼
Cloudflare Access  (edge: Access application on the staging hostname)
   │  Cf-Access-Jwt-Assertion
   ▼
Worker "adg-demo-staging"            frontend/cloudflare/worker.ts
   │  1. verify the Access assertion again (RS256, team certs, AUD, iss, exp)   access.ts
   │  2. route by path                                                         routing.ts
   ├── /api/v1/…, /auth/…, /health/…, /version, /runtime ──┐
   │                                                       ▼
   │                                     Container "AdgApiContainer" (Durable Object)
   │                                       docker/Dockerfile.backend, unmodified
   │                                       python -m app  :8000   ADG_RUNTIME_MODE=demo
   │                                                       │  TCP 5432, TLS
   │                                                       ▼
   │                                     Hosted PostgreSQL — database adg_demo ONLY
   │                                       deployment_marker = demo / null-meridian-v1
   │
   └── everything else ─► OpenNext (the unmodified Next.js app, incl. static assets)
                              │ server-side API calls (lib/api/client.ts)
                              └─► lib/api/transport.ts ─► the same Container
```

One codebase, one image, one schema: Cloudflare runs exactly the application `docker compose -f
docker-compose.yml -f docker-compose.demo.yml` runs. The deployment adds infrastructure only:

| Piece | What it is | Why |
| --- | --- | --- |
| `frontend/wrangler.jsonc` | Worker, assets, Container, Durable Object, cron, vars | The deployment's whole Cloudflare configuration. No secret, no account id. |
| `frontend/cloudflare/worker.ts` | Worker entry: Access check → route → Container or OpenNext; the `AdgApiContainer` class; keep-warm `scheduled` | OpenNext's documented [custom-worker](https://opennext.js.org/cloudflare/howtos/custom-worker) pattern, so one Worker serves the app and exports the Container class. |
| `frontend/cloudflare/access.ts` | Verifies `Cf-Access-Jwt-Assertion` with WebCrypto | Second lock behind Access at the edge; fails closed (§8). |
| `frontend/cloudflare/routing.ts` | Which paths the API answers | The API's own paths, unchanged — no new `/api` namespace. |
| `frontend/cloudflare/environment.ts` | The Container's environment (allow-list, demo pinned) and the Next.js server's | No secret reaches a tier that does not need it (§5). |
| `frontend/lib/api/transport.ts` | A seam in the web tier's single API client | On Cloudflare there is no network between Next.js and the Container; the Worker installs a transport that sends API calls to the Durable Object. Everywhere else it is plain `fetch`. |
| `frontend/open-next.config.ts` | OpenNext adapter configuration (no cache) | Every ADG page is rendered per request. |
| `frontend/cloudflare/checks/` | `smoke.mjs`, `browser.mjs`, `null_meridian_fingerprint.py` | Hosted checks the workflow runs after every deploy (§9). |
| `.github/workflows/cloudflare-staging.yml` | Preflight → (bootstrap) → deploy → hosted checks | The cloud-native build: GitHub's runners have Docker; nobody's laptop is involved (§6). |

Unchanged: the backend (no file), `docker/Dockerfile.backend`, the database schema, Demo Mode,
Null Meridian, the UI. The frontend change is the transport seam (one call site) and a Next.js
patch bump to 15.5.27, the minimum `@opennextjs/cloudflare` 1.20 supports.

## 2. Decisions and sources

Verified against Cloudflare's and OpenNext's documentation as of October 2026.

| Question | Decision | Source |
| --- | --- | --- |
| Next.js on Cloudflare | `@opennextjs/cloudflare` 1.20.10 on Workers (Node.js runtime via `nodejs_compat`); needs Next ≥ 15.5.27 | [opennext.js.org/cloudflare](https://opennext.js.org/cloudflare), [get started](https://opennext.js.org/cloudflare/get-started) |
| Backend | Cloudflare **Containers** (GA 2026-04-13, Workers Paid) running the existing image; not a Python Worker | [Containers](https://developers.cloudflare.com/containers/), [GA changelog](https://developers.cloudflare.com/changelog/post/2026-04-13-containers-sandbox-ga/) |
| Worker → Container | `@cloudflare/containers` 0.3.7 `Container` class bound as a Durable Object; one named instance (`getContainer(ADG_API, "adg-api")`) | [get started](https://developers.cloudflare.com/containers/get-started/), [github.com/cloudflare/containers](https://github.com/cloudflare/containers) |
| One Worker or two | One: a custom entry wraps `.open-next/worker.js` and exports the Container class | [custom worker](https://opennext.js.org/cloudflare/howtos/custom-worker) |
| Image build | `wrangler deploy` builds `docker/Dockerfile.backend` (linux/amd64) with the runner's Docker and pushes to Cloudflare's registry | [image management](https://developers.cloudflare.com/containers/guides/image-management/) |
| Where the build runs | GitHub Actions (`ubuntu-latest` has Docker). Workers Builds also documents Dockerfile builds, but its build image lists no Docker, so it is the unverified alternative | [deploy guide](https://developers.cloudflare.com/containers/guides/deploy/), [GitHub Actions](https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/) |
| Environment and secrets | Worker vars in `wrangler.jsonc` / `--var`; secrets uploaded **with** the version (`wrangler deploy --secrets-file`); forwarded to the Container through `envVars` | [env vars & secrets](https://developers.cloudflare.com/containers/examples/env-vars-and-secrets/), [container env](https://developers.cloudflare.com/containers/configuration/environment-variables/) |
| Hostname | Generated `adg-demo-staging.<account-subdomain>.workers.dev`; preview/version URLs off | [wrangler configuration](https://developers.cloudflare.com/workers/wrangler/configuration/) |
| Access | One-click **Enable Cloudflare Access** on the workers.dev route, plus in-Worker JWT validation | [one-click Access](https://developers.cloudflare.com/changelog/post/2025-10-03-one-click-access-for-workers/), [validating JWTs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/) |
| Outbound to PostgreSQL | Containers have internet egress by default (`enableInternet = true`); non-HTTP ports such as 5432 go straight out | [outbound traffic](https://developers.cloudflare.com/containers/configuration/outbound-traffic/) |
| Lifecycle | Ephemeral disk; sleeps after `sleepAfter` idle; SIGTERM then SIGKILL; no autoscaling — fixed single instance | [architecture](https://developers.cloudflare.com/containers/concepts/architecture/), [scaling](https://developers.cloudflare.com/containers/configuration/scaling-and-routing/) |
| Logs | `observability.enabled` (Workers Logs, 7 days) covers the Worker, the Durable Object and container stdout in the dashboard's Container logs | [Containers FAQ](https://developers.cloudflare.com/containers/faq/) |
| Instance sizes | `lite` 1/16 vCPU 256 MiB · `basic` 1/4 vCPU 1 GiB · `standard-1` 1/2 vCPU 4 GiB · `standard-2` 1 vCPU 6 GiB … | [limits](https://developers.cloudflare.com/containers/platform/limits/) |

## 3. Routing

| Path | Served by | Note |
| --- | --- | --- |
| `/api/v1/…` | API Container | The API's own routes. Ingestion answers `403 demo_only` (Demo Mode class C). |
| `/auth/…` | API Container | `/auth/config`, `/auth/me`, `/auth/dev/login` (development sign-in). |
| `/health/…`, `/version`, `/runtime` | API Container | Public metadata; no connection detail (asserted by the smoke checks). |
| everything else | Next.js (OpenNext) | `/`, `/demo`, every page, the web tier's own `/api/adg`, `/api/auth`, `/api/simulations`, `/api/watches`, and `/_next/static` assets (`run_worker_first`, so assets also pass the Access check). |

Next.js's server-side calls go to `ADG_INTERNAL_API_URL=http://adg-api.internal`. That name is
never resolved: the transport hands each request to the Container binding. No `localhost`,
container address or developer URL is in any code or configuration.

## 4. PostgreSQL

ADG stays on PostgreSQL (16+). Use a **dedicated** managed database for staging — Neon is the
documented default; any managed PostgreSQL reachable on TCP over TLS works (Supabase Postgres
and others). Never an ADG production database.

* Database name: **`adg_demo`** (must contain `demo` as a word; the API, the reset and the
  workflow's preflight all refuse otherwise — the rule is not weakened here).
* Connection string: `postgresql+psycopg://<user>:<password>@<host>/adg_demo?sslmode=require`.
  Use the provider's **direct** (non-pooled) endpoint: the reset locks the marker and truncates
  every table in one transaction, and psycopg 3 prepares statements, which transaction-mode
  poolers do not keep.
* The database receives `deployment_marker = demo / null-meridian-v1` from the reset (§6), the
  only way it is ever written. Nothing here creates or edits the marker.
* Neon computes may scale to zero; the first connection after a pause takes a moment. The API's
  connect timeout (`ADG_DATABASE_CONNECT_TIMEOUT_SECONDS`, 5 s) covers it.

## 5. Configuration

### Worker variables (non-secret, `frontend/wrangler.jsonc`)

| Name | Value | Meaning |
| --- | --- | --- |
| `ADG_RUNTIME_MODE` | `demo` | Required. The Worker refuses anything else before starting a Container; the API refuses unknown values. |
| `ADG_DEMO_DATASET` | `null-meridian-v1` | Required; the Worker accepts no other dataset for this deployment. |
| `ADG_ENVIRONMENT` | `development` | Allows development sign-in (staging review only; §11). |
| `ADG_AUTH_MODE` | `development` | ADG's development sign-in, behind Access. |
| `ADG_LOG_LEVEL` / `ADG_LOG_FORMAT` | `INFO` / `json` | |
| `ADG_CONTAINER_SLEEP_AFTER` | `15m` | Idle time before the Container sleeps (the keep-warm cron normally prevents it). |

Passed at deploy time with `--var` by the workflow (account-specific, not committed):
`ADG_WEB_URL` (the staging origin), `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`.

### Pinned by the Worker, whatever it is given (`cloudflare/environment.ts`)

`ADG_COLLECTOR_API_KEYS=""`, `ADG_ALERT_POLICY_PATH=""`, `ADG_RISK_CONFIGURATION_PATH=""` (the
dataset's own), `ADG_REMEDIATION_EXECUTION_MODE=disabled`, `ADG_REMEDIATION_LAB_FIXTURE_PATH=""`,
`ADG_ALERTS_ON_RUN_COMPLETION=false`, `ADG_HISTORY_RETENTION_ENABLED=false`. No OIDC setting,
collector key, Windows account or alert destination is passed to the Container at all.

### Secrets — names only

| Name | Where | Used by |
| --- | --- | --- |
| `ADG_DATABASE_URL` | GitHub env `cloudflare-staging` → Worker secret (with the version) | Container; bootstrap job |
| `ADG_DEV_AUTH_SECRET` | GitHub env → Worker secret | Container (≥ 32 chars; keeps sessions valid across Container restarts) |
| `ADG_REMEDIATION_SIGNING_KEY` | GitHub env → Worker secret (optional) | Container (≥ 32 chars; enables the inert runbook export) |
| `CLOUDFLARE_API_TOKEN` | GitHub env | Workflow only — never reaches the Worker or Container |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub env | Workflow only |
| `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` | GitHub env | Hosted checks only (an Access service token) |

GitHub environment **variables**: `ADG_STAGING_URL`, `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`.

Cloudflare API token permissions (account-scoped): Workers Scripts **Edit**, Containers
**Edit**, Account Settings **Read**, and — only to automate Access — Access: Apps and Policies
**Edit**. Workers Paid plan (Containers).

## 6. Build, deploy, bootstrap

### One-time setup (Cloudflare dashboard + GitHub)

1. Create the managed PostgreSQL project and a database named `adg_demo`; copy its direct
   connection string into `ADG_DATABASE_URL` in the form of §4.
2. Create the API token (§5) and the GitHub environment `cloudflare-staging` with the secrets and
   variables of §5. Add required reviewers to the environment if you want a human gate on every
   deploy.
3. First deploy (step 4 below) creates `adg-demo-staging` on `<subdomain>.workers.dev`; before
   anyone visits, in **Workers & Pages → adg-demo-staging → Settings → Domains & Routes**,
   **Enable Cloudflare Access** on the workers.dev route. Copy the shown `TEAM_DOMAIN` and
   `POLICY_AUD` into `ACCESS_TEAM_DOMAIN`/`ACCESS_AUD`. Restrict the policy to the reviewers
   (an email list or your IdP group). Until those two are set, the Worker answers 503 to
   everyone, so the window between the first deploy and this step exposes nothing.
4. In Zero Trust, create an Access **service token** for the hosted checks and add a
   **Service Auth** policy for it to the same application; store it as
   `CF_ACCESS_CLIENT_ID`/`CF_ACCESS_CLIENT_SECRET`.

### Deploy

**Actions → Cloudflare staging (ADG demo) → Run workflow** (optionally `bootstrap`), or push the
commit to review to `deploy/ADG-CLOUDFLARE-DEMO-01`. The workflow:

1. **preflight** — every name in §5 present; Access domain https; the database named for the
   demo; a bootstrap only with the typed confirmation `reset adg_demo`. Prints names, never
   values.
2. **gates** — Worker typecheck and the Cloudflare unit tests.
3. **bootstrap** (only when asked) — builds this commit's API image and runs, against the hosted
   database, the canonical lifecycle: `python -m app.demo_mode reset`, `--json verify`,
   `--json status`, then the Null Meridian logical fingerprint. Reports are artifacts.
4. **deploy** — `opennextjs-cloudflare build`, then `wrangler deploy --secrets-file …
   --var … --tag <sha12> --containers-rollout immediate`: wrangler builds
   `docker/Dockerfile.backend` with the runner's Docker, pushes it to
   `registry.cloudflare.com`, uploads the Worker version **with** its secrets, and rolls the
   Container out. The job summary records the source commit, the Worker version id/tag and the
   image `repository:tag@digest`.
5. **checks** — read-only `verify` + logical fingerprint against the hosted database, then
   `smoke.mjs` and `browser.mjs` against the staging URL with the service token.

The first deploy of a Container application can take several minutes before Container routes
answer; Cloudflare's deploy is not transactional (the Worker version activates before the
image rollout finishes).

### Bootstrap and reset — the operator path

The demo database is bootstrapped **once**, and reset only deliberately. The Container never
resets: it runs the image's own `python -m app`, whose lifespan only verifies that the
database is a marked Null Meridian demo and refuses to start otherwise. There is no reset
endpoint. To reset later: run the workflow with `bootstrap` and `confirm_reset = reset
adg_demo`. To check without changing anything, run it without `bootstrap` (the checks job
verifies). From any machine with Docker and the secret, the same commands:

```bash
docker build -f docker/Dockerfile.backend -t adg-api .
docker run --rm -e ADG_RUNTIME_MODE=demo -e ADG_DATABASE_URL adg-api python -m app.demo_mode reset
docker run --rm -e ADG_RUNTIME_MODE=demo -e ADG_DATABASE_URL adg-api python -m app.demo_mode verify
docker run --rm -e ADG_RUNTIME_MODE=demo -e ADG_DATABASE_URL adg-api python -m app.demo_mode status
```

A reset takes about 80–90 s of ingestion against a nearby database (longer across regions)
and briefly empties the tables the API is serving; do not run it during a review.

Expected after bootstrap: `verify` ok, 23/23 probes, fingerprint
`228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`; Null Meridian logical
fingerprint `db736602850a4a82936f861b68466e7c54b61a55192b04f6d062cbe8585c28a0`. The canonical
fingerprint excludes process bookkeeping (row timestamps, serials, evaluation ids), so a hosted
PostgreSQL should reproduce it exactly; a different value is dataset drift until proven
otherwise.

## 7. Resources, cold start, keep-warm

`instance_type: basic` (1/4 vCPU, 1 GiB RAM, 4 GB disk), `max_instances: 2` (one serving
instance; headroom for a rollout). Measured locally with the same image under equivalent
Docker limits (§ handoff 8):

| Size | Process start → `/health/ready` | Representative reads | Peak RSS |
| --- | --- | --- | --- |
| `lite` (1/16 vCPU, 256 MiB) | 217 s | 0.5–3 s | 160 MiB (of 256) |
| **`basic`** (1/4 vCPU, 1 GiB) | **24–28 s** | 0.1–0.4 s | 160 MiB |
| `standard-1` (1/2 vCPU, 4 GiB) | 12 s | 0.1–0.2 s | — |
| `standard-2` (1 vCPU, 6 GiB) | 5.5 s | < 0.1 s | — |

Startup is ~1.3 s of imports and ~2.8 s of Demo Mode's lifespan checks at full CPU; the
fractional vCPU stretches it. `basic` is the smallest size that serves reads, explanations,
risk results and simulation comfortably; the one-time reset runs outside Cloudflare and does
not size it. Because a cold `basic` start exceeds the web tier's 10 s API timeout, a **cron
trigger every 5 minutes** sends a readiness probe to the Container (`scheduled` in
`worker.ts`), keeping the one instance awake: reviewers meet a cold start only right after a
deploy, which the workflow's own checks absorb. To trade money for faster cold starts instead,
switch to `standard-2`; to let it sleep, remove the cron.

## 8. Access protection

Two locks:

1. **Cloudflare Access at the edge** on the staging hostname — a visitor without an Access
   session never reaches the Worker.
2. **The Worker verifies the assertion itself** (`access.ts`) on every request, static assets
   included: RS256 signature against `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`,
   the application's AUD, the issuer, `exp`/`nbf` (60 s leeway). No assertion → 401; an invalid
   one → 403; **no Access configuration → 503 for everyone**. If Access is ever switched off or
   scoped to the wrong hostname, staging goes dark rather than public. The assertion is stripped
   before either tier sees the request. Identities are not logged.

Development sign-in is a convenience *behind* this boundary: the page says plainly that anyone
who reaches it can sign in as any account, which is why it must never be reachable without
Access.

## 9. Logs and diagnosis

`observability.enabled` with full sampling. Worker logs (structured JSON):
`adg.staging.access_refused` (status, reason, path — never the token), `adg.staging.misconfigured`
(names settings, never values), `adg.staging.api_unavailable`, `adg.container.started`,
`adg.container.stopped` (exit code), `adg.container.error`, `adg.keepwarm` (status, ms). The
Container's stdout is the API's own JSON log (request lines, `api.startup.demo_mode`, refusals).
View: dashboard → Workers & Pages → `adg-demo-staging` → Logs, and → Containers → logs;
`npx wrangler tail adg-demo-staging` for the Worker live. A wrong database password, for
example, appears as `adg.container.error … exit code: 3` in the Worker log and
`password authentication failed for user …` / `Application startup failed. Exiting.` in the
Container log — without the password (rehearsed, handoff §9). The API logs no `Authorization`
header, and the Worker logs no header values.

## 10. Rollback

The demo database is **never** touched by a rollback.

| Goal | Action |
| --- | --- |
| Stop all traffic now | Dashboard → `adg-demo-staging` → Settings → Domains & Routes → disable the workers.dev route (or set `"workers_dev": false` and deploy). Access stays configured. |
| Make it serve nobody without touching routes | Remove `ACCESS_AUD` (deploy without the `--var`): the Worker answers 503 to every request. |
| Restore the previous deployment | Re-run the workflow on the previous good commit (Actions → Run workflow → that ref), which rebuilds both the Worker and the image of that commit. `npx wrangler rollback` restores a previous Worker version, but does not by itself re-roll the Container image. |
| Stop the Container | Remove the cron and redeploy with `ADG_CONTAINER_SLEEP_AFTER` short (it sleeps when idle), or delete the Worker (below). |
| Remove staging entirely | `npx wrangler delete adg-demo-staging` (Worker, Durable Object and Container application) and delete the Access application. The database and its data remain. |

Before this staging deployment there was no previous one to restore.

## 11. Known limitations and public-launch blockers

* **Sign-in.** Staging uses ADG's development sign-in behind Access. A public demo needs an
  owner decision: OIDC, or a deliberate read-only visitor mode (not in the product). Removing
  Access with development sign-in enabled would let anyone act as an administrator of the demo
  (including writing governance and simulation records).
* **Single instance.** One named Container serves everything; Containers have no autoscaling.
* **Cold start.** 24–28 s on `basic`; mitigated by the keep-warm cron, not removed.
* **Concurrent writes.** Demo writes (governance, simulations, `POST /api/v1/demo/scans`)
  persist in PostgreSQL across Container lifecycles — by design — so a long review can drift
  from the canonical baseline; reset deliberately (§6).
* **Region.** The Durable Object (and so the Container) is placed near its first request; place
  the database in the same region.
