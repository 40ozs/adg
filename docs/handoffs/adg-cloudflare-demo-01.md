# ADG-CLOUDFLARE-DEMO-01 — ADG Demo Mode + Null Meridian on Cloudflare (protected staging)

**Verdict:** `BLOCKED_ON_CLOUDFLARE_CONFIGURATION` — the deployment path is built, validated and
rehearsed end to end (Worker + Container + PostgreSQL + Access check, 21/21 smoke, 22/22
browser, both fingerprints exact, lifecycle survived), but **nothing is deployed**: this
session had no Cloudflare credentials, no hosted-PostgreSQL credentials, and its network policy
refuses `api.cloudflare.com` and the database provider. §13 lists exactly what unblocks it.
**No staging URL exists yet; no public exposure occurred.**

Operator guide: [`docs/deployment/cloudflare-demo.md`](../deployment/cloudflare-demo.md).

## 1. Starting point

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| `origin/main` at start | `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af` (Merge PR #2, Demo Mode + Null Meridian) |
| Demo merge ancestry | `1ab01a2` **is** `origin/main` (`git merge-base --is-ancestor` → true) |
| Working tree at start | clean |
| Branch | `claude/wizardly-gates-6yjdxp` — the session's assigned branch, created from `origin/main`; it fills the role of the suggested `deploy/ADG-CLOUDFLARE-DEMO-01`, which the workflow keeps as its push-to-deploy trigger. No other branch was touched. |
| Ending SHA | the head of this branch; recorded in the final report |

## 2. Environment found

| Need | Found | Consequence |
| --- | --- | --- |
| Cloudflare API token / account id | **absent** (no env var, no wrangler state) | No deploy, no Access configuration |
| `api.cloudflare.com` | **refused** by the session's egress policy (CONNECT 403) | Even with a token, wrangler could not deploy from here |
| Hosted PostgreSQL credentials | **absent**; `console.neon.tech` refused by egress policy | No hosted database |
| Docker | engine installed, daemon not running → **started it**; image pulls from Docker Hub work | Full local rehearsal possible |
| PyPI from inside a build | TLS-intercepting proxy: `pip` in the unmodified Dockerfile fails certificate checks | Sandbox-only overlay adds the proxy CA (§5); repository Dockerfile unchanged |
| npm registry | reachable | OpenNext, wrangler, containers installed |

Cloudflare documentation was read from the official sources (developers.cloudflare.com and
opennext.js.org, via their GitHub source repositories because their hostnames were not
resolvable from here). Decisions and links: deployment doc §2.

## 3. Architecture (as built)

* **Frontend:** the unmodified Next.js 15.5 app, built by `@opennextjs/cloudflare` 1.20.10,
  served by Worker `adg-demo-staging` (custom entry `frontend/cloudflare/worker.ts`).
* **Backend:** `docker/Dockerfile.backend`, unmodified, as Cloudflare Container class
  `AdgApiContainer` (Durable Object binding `ADG_API`, one named instance `adg-api`,
  `instance_type: basic`, port 8000, image's own `python -m app`).
* **Routing:** one origin. `/api/v1/…`, `/auth/…`, `/health/…`, `/version`, `/runtime` → the
  Container; everything else → Next.js. Next.js's server-side API calls reach the Container
  through `lib/api/transport.ts` (no network path exists between them on Workers).
* **Database:** a dedicated managed PostgreSQL database `adg_demo` (Neon documented),
  bootstrapped once through `python -m app.demo_mode reset` from the deploy workflow.
* **Access:** Cloudflare Access at the edge **and** in-Worker verification of
  `Cf-Access-Jwt-Assertion`; no Access configuration → 503 to everyone.
* **Build:** GitHub Actions (`.github/workflows/cloudflare-staging.yml`): wrangler builds the
  image with the runner's Docker and pushes it to `registry.cloudflare.com`; the Worker version
  is tagged with the commit; secrets upload with the version.

## 4. Files changed

| File | Change |
| --- | --- |
| `frontend/wrangler.jsonc` | **new** — Worker, assets (`run_worker_first`), Container, DO binding + migration, observability, keep-warm cron, demo vars. No secret, no account id. |
| `frontend/cloudflare/worker.ts` | **new** — entry: Access → routing → Container / OpenNext; `AdgApiContainer`; transport install; `scheduled` keep-warm |
| `frontend/cloudflare/access.ts` | **new** — Access JWT verification (WebCrypto RS256), fail closed |
| `frontend/cloudflare/routing.ts` | **new** — path routing and refusals |
| `frontend/cloudflare/environment.ts` | **new** — Container env allow-list (demo pinned), Next.js env |
| `frontend/cloudflare/tsconfig.json` | **new** — Workers types for the entry |
| `frontend/cloudflare/checks/smoke.mjs` | **new** — hosted HTTP smoke (21 checks) |
| `frontend/cloudflare/checks/browser.mjs` | **new** — hosted browser gate (22 checks) |
| `frontend/cloudflare/checks/null_meridian_fingerprint.py` | **new** — the `4512524` logical fingerprint, verbatim, verification only |
| `frontend/open-next.config.ts` | **new** — OpenNext config, no cache |
| `frontend/lib/api/transport.ts` | **new** — API transport seam (plain `fetch` unless a host installs one) |
| `frontend/lib/api/client.ts` | one call: `fetch` → `sendApiRequest` |
| `frontend/tests/transport.test.ts`, `frontend/tests/cloudflare/*` | **new** — 71 tests (transport, Access, routing, environment) |
| `frontend/package.json`, `package-lock.json` | `next`/`eslint-config-next` 15.5.25 → **15.5.27** (OpenNext minimum); dev `+@cloudflare/containers`, `+@opennextjs/cloudflare`, `+wrangler`, `+@cloudflare/workers-types`; scripts `cf:typecheck`, `cf:build` (deploys go through the workflow only) |
| `frontend/tsconfig.json` | exclude `cloudflare/worker.ts`, `.open-next`, `.wrangler` (Workers-typed; checked by `cf:typecheck`) |
| `frontend/eslint.config.mjs`, `frontend/.gitignore`, `.dockerignore` | ignore `.open-next/`, `.wrangler/` (and `.dev.vars`) |
| `.github/workflows/cloudflare-staging.yml` | **new** — preflight → gates → bootstrap (opt-in, typed confirmation) → deploy → hosted checks |
| `docs/deployment/cloudflare-demo.md`, this handoff | **new** |

**Untouched:** every backend file, `docker/Dockerfile.backend`, `docker/Dockerfile.frontend`,
both compose files, the schema and migrations, Demo Mode, Null Meridian, every page and
component. No database guard, runtime-mode rule, credential, collector or remediation
restriction was changed.

## 5. Local rehearsal — what was actually run

Everything below ran in this session's Linux container, against a **scratch** PostgreSQL 16
(`postgres:16-alpine`, database `adg_demo`) — not a hosted database and not Cloudflare. The
Worker ran in `wrangler dev` 4.149.0 (workerd 1.20261006.1) with **real local Containers**
(wrangler builds/runs them through Docker), using the committed Worker code; the only
differences from the committed `wrangler.jsonc` are absolute paths, the image (below), and
Access pointed at a local stand-in that publishes its own RS256 certificates and signs
assertions — so the Worker's real verification path ran, not a bypass.

Image: built from `docker/Dockerfile.backend` with two sandbox-only lines after `FROM` that add
this sandbox's TLS-proxy CA for `pip` (`COPY … sandbox-ca.crt`, `ENV PIP_CERT=…`) — required
here, not in the repository. 373 MB, `linux/amd64`. The same image ran the bootstrap and the
Container.

## 6. Bootstrap, verify, fingerprints (scratch database)

| Step | Result |
| --- | --- |
| `reset` (dev venv) | initialized `adg_demo`; 300 runs (297 ok, 2 failed, 1 partial — the canon's), 41,756 observations, 23/23 probes, 9,386 open findings; 87 s |
| `reset` (**from the image**, as the workflow runs it) | identical report; 81 s |
| `--json verify` (image) | `ok: true`, fingerprint **`228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`** — equal to the integration's |
| `status` (image) | `runtime_mode: demo`, dataset `null-meridian-v1`, database `adg_demo`, marker `demo` / `null-meridian-v1` |
| Null Meridian logical fingerprint (`4512524` algorithm, `null_meridian_fingerprint.py`) | **`db736602850a4a82936f861b68466e7c54b61a55192b04f6d062cbe8585c28a0`** — `match: true` |

The Container never ran `reset`: it started with the image's `python -m app` and only verified
the marked database.

## 7. Rehearsed checks through the Worker (local)

**Smoke (`smoke.mjs`): 21/21.** Unauthenticated page and API refused (401); homepage with Demo
indicator; `/runtime` demo / synthetic / `null-meridian-v1`, `X-ADG-Runtime-Mode: demo`, no
connection fragment or canary (DB host and password); `/health/ready` ready; `/version` demo;
`POST|PUT|PATCH|DELETE /runtime` → 405 and query/header ignored; ingestion `POST
/api/v1/scan-runs` (anonymous) and all three ingestion routes as administrator → `403
demo_only`; no remediation-execution route (404/405); `/api/v1/demo` A–H with three
generations applied and none pending; web sign-in; web tier `POST /api/adg/api/v1/scan-runs` →
405; `/demo`; scenario A share; scenario F principal; scenario F explanation; scenario G nested
explanation; risks; simulations.

**Browser (`browser.mjs`, headless Chromium 1194 / Playwright 1.56.1): 22/22, 0 page errors,
1 console error** (the deliberate 405 of the web-tier ingestion probe). Signed-out pill *Demo
environment · Synthetic enterprise data · Null Meridian*; explanation; no control in the
indicator; 11 manifest facts and `/demo` link; footer; development sign-in; `/demo` A–H, g1–g3;
scenario A and B directory pages (Design ACL, `Finance-RW`); a principal page (Lucia Hart);
scenario F explanation (Brianna Reddy; Revenue-Reporting and Sales-Ops); scenario G six-level
chain; risks 3 / 8 / 1957 / 423 / 6995; **scenario H through the real simulation editor →
"This proposal changes nobody's access."**; all 11 primary sections; status page backend
reachable; web-tier 405; API `403 demo_only`; `/runtime`; `X-ADG-Runtime-Mode`; sign-out.

Run twice: before and after the final code changes; both 21/21 and 22/22. **These are
rehearsal results, not hosted results**; the workflow's `checks` job runs the same two scripts
against the staging URL.

## 8. Performance and sizing (local)

| Measurement | Value |
| --- | --- |
| Frontend warm (`/`, through Worker + Access check, SSR + API) | 21–28 ms |
| Frontend cold (first request after a redeploy; includes Container cold start) | 6.8 s |
| API warm (`/runtime`) | 5–12 ms |
| Container cold start, local unconstrained CPU | ~6 s (request → response) |
| API process start → ready under `basic`-equivalent limits (`--cpus 0.25 --memory 1g`) | 24–28 s |
| … `lite` (0.0625 CPU, 256 MiB) / `standard-1` (0.5, 4 GiB) / `standard-2` (1, 6 GiB) | 217 s / 12 s / 5.5 s |
| Representative reads on `basic` (demo, explanation F/G, access paths, simulation H) | 0.01–0.4 s |
| API memory after the full browser gate | ~160 MiB |
| Worker bundle | 6.0 MiB (1.17 MiB gzip) — under the 64 MiB limit |

Chosen: **`basic`** (1/4 vCPU, 1 GiB, 4 GB disk): `lite` is too slow to start and too tight in
memory; `basic` serves every representative request sub-second with 6× memory headroom. Its
cold start exceeds the web tier's 10 s API timeout, so a **keep-warm cron** (every 5 min,
`scheduled` → `/health/ready`) keeps the instance up — rehearsed: it woke a sleeping Container
and logged `adg.keepwarm status 200`. Cloudflare's own CPU allocation may differ from Docker's
CFS quota; hosted numbers are to be recorded by the first deploy.

## 9. Lifecycle and persistence (local)

| Event | Result |
| --- | --- |
| Redeploy (restart of the Worker and Container runtime) | first request 6.8 s, then warm; data intact |
| Sleep (`sleepAfter` 30 s) and wake | Container stopped (`adg.container.stopped exit 0`); next request woke it in 6.1 s |
| Crash of the API process (`docker kill`) | `adg.container.stopped exit 137` logged; next request restarted it in 6.2 s |
| After all of the above | `verify` ok, `228b9dce…f9efd0`; logical `db736602…85c28a0` match; no reset |
| Misconfiguration (wrong DB password) | request → 500 "Container exited with unexpected exit code: 3"; Worker log `adg.container.error`; API log `password authentication failed for user …`, `Application startup failed. Exiting.`; the password in **no** log line or response |

Note: killing the local runtime's egress **sidecar** container as well left wrangler dev's
local Container unable to restart until wrangler was restarted — a property of the local
emulation (its sidecar is not part of Cloudflare's platform), not reproduced with the API
process alone. State lives in PostgreSQL only; the Container's disk is never relied on.

## 10. A defect found and fixed in rehearsal (deployment-only)

OpenNext replaces `globalThis.Request` with a subclass for the **whole isolate** on its first
request. The Container class runs in the same isolate and recognizes requests with
`instanceof Request`, so after the first page render every API request — from the edge or from
Next.js — failed with `TypeError: Invalid URL: [object Request]` (status page: "Backend
unreachable"; `/runtime` → 502). Product code was not involved. Fix: `AdgApiContainer.fetch`
re-wraps an incoming request in whatever `Request` currently is (`worker.ts`). Regression
coverage: `smoke.mjs` requests `/runtime` **after** a page render on purpose, and the browser
gate exercises both paths; both pass. (It cannot be unit-tested in Node: the class needs the
Workers runtime.)

## 11. Safety

| Concern | State |
| --- | --- |
| Collector ingestion | `403 demo_only` on all three routes, anonymous and administrator (rehearsed); web tier forwards no POST (405) |
| Active Directory, LDAP, SMB, WinRM, PowerShell | Not in the image, not configured, no route; nothing contacted |
| Remediation execution | `ADG_REMEDIATION_EXECUTION_MODE=disabled` pinned by the Worker; no execution route exists (404/405) |
| Runtime mode | `demo` required by the Worker and the API; `/runtime` GET only (405 otherwise); query/header ignored |
| Production persistence | Database must be named for the demo (preflight, API, reset); marker written only by reset into an empty database; no production database touched — only the scratch `adg_demo` |
| Credentials | Collector keys, alert-policy path, lab fixture pinned empty; no OIDC or Windows credential passed; Cloudflare token stays in the workflow; Access assertion stripped before either tier; no secret in any committed file |
| Exposure | Worker refuses all traffic without Access configured; preview/version URLs disabled; static assets also checked |

## 12. Gates run

| Gate | Result |
| --- | --- |
| ruff check / ruff format --check (backend) | clean (398 files) |
| mypy `app tests` | 0 issues (397 files) |
| Backend hermetic (`pytest -m "not smoke"`) | 5765 passed, 124 skipped |
| Demo Mode: reset / verify / status (dev venv and image) against PostgreSQL | pass, fingerprints exact |
| Backend DB suite (`-m smoke`, ~25 min) | **not rerun**: no backend file changed; the Demo Mode lifecycle itself was exercised end to end (§6) |
| Frontend lint / typecheck / tests | clean / clean / **1234 passed** (37 files; +71 new) |
| `cf:typecheck` (Worker, Workers types) | clean |
| `next build` | pass |
| `opennextjs-cloudflare build` | pass (40 s) |
| `wrangler deploy --dry-run` | pass; bindings `ADG_API` (DO), `ASSETS`, `WORKER_SELF_REFERENCE`, vars; container `adg-demo-staging-adgapicontainer` |
| `actionlint` (new and existing workflows) | clean |
| `docker compose config -q` (base, base + demo) | pass |
| Collector Pester | not run (no `pwsh`; no collector file changed) |

## 13. What unblocks staging (exact)

1. **Cloudflare** (Workers Paid): an API token with Workers Scripts Edit, Containers Edit,
   Account Settings Read (+ Access: Apps and Policies Edit to automate Access), and the account
   id — stored as GitHub environment `cloudflare-staging` secrets `CLOUDFLARE_API_TOKEN`,
   `CLOUDFLARE_ACCOUNT_ID`.
2. **Database:** a managed PostgreSQL database named `adg_demo` (direct endpoint, TLS) — secret
   `ADG_DATABASE_URL`.
3. **Secrets:** `ADG_DEV_AUTH_SECRET` (≥ 32 random chars); optional `ADG_REMEDIATION_SIGNING_KEY`.
4. **Access:** after the first deploy, *Enable Cloudflare Access* on the workers.dev route;
   set variables `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`, `ADG_STAGING_URL`; create a service token
   with a Service Auth policy → `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`.
5. Run **Cloudflare staging (ADG demo)** with `bootstrap` and `confirm_reset = reset adg_demo`
   (workflow_dispatch needs the workflow on the default branch; otherwise push the reviewed
   commit to `deploy/ADG-CLOUDFLARE-DEMO-01`, which deploys without bootstrapping — then run
   the bootstrap commands of the deployment doc §6 once).

Alternatively, this kind of session can deploy directly if its environment is given the
Cloudflare token and database URL as secrets and its network policy allows
`api.cloudflare.com`, `registry.cloudflare.com` and the database host.

## 14. Known limitations

* Not deployed: no hosted URL, hosted timings, hosted persistence or hosted logs exist yet.
* Workers Builds was not used: its documentation says it can build Dockerfiles, but its build
  image lists no Docker; GitHub Actions is the verified path.
* `wrangler rollback` restores a Worker version, not necessarily the Container image; the
  reliable rollback is redeploying the previous commit (deployment doc §10). Rollback steps are
  documented and the configuration variants dry-run-validated only.
* Cold start 24–28 s on `basic` (mitigated by keep-warm, not removed); single instance.
* Region: place the database near where the Container lands (first request's region).
* `docker/Dockerfile.frontend` (compose) copies the whole `node_modules`, development
  dependencies included, into its runtime stage — as it already did for Vitest and ESLint — so
  that image now also carries the Cloudflare toolchain (wrangler, workerd). Not used at runtime;
  pruning dev dependencies there is a separate, pre-existing improvement.

## 15. Public-launch blockers

1. Sign-in posture: development sign-in behind Access is a staging convenience. A public demo
   needs OIDC or a read-only visitor mode — an owner decision, not made here.
2. Owner review of the protected staging deployment (once deployed).
3. Capacity: one `basic` instance and no autoscaling.
4. Demo-state drift from visitor writes (governance, simulations, synthetic scans) needs a reset
   policy for a public demo.
