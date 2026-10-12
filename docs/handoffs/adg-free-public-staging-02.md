# ADG-FREE-PUBLIC-STAGING-02 — public ADG demo on Workers Free + Render Free + Supabase

**Verdict:** `READY_FOR_ADG_FREE_PUBLIC_STAGING_REVIEW_WITH_LIMITATIONS`. The demo is public at
<https://adg-demo-staging.d-walkes.workers.dev> with no Cloudflare Access. The Render API refuses
every direct request. Null Meridian runs from `adg_demo` with both fingerprints exact, and
visitors can sign in only as `viewer` or `planner`. Hosted smoke passes 23/23 and the browser
gate 22/22, and no paid Cloudflare product is configured. The main limitation is Worker CPU:
server-rendered pages use 3–14× the Workers Free 10 ms per-request CPU limit. No request has
failed so far (§6).

Operator guide: [`docs/deployment/cloudflare-demo.md`](../deployment/cloudflare-demo.md).
Previous handoff (superseded architecture): [`adg-cloudflare-demo-01.md`](adg-cloudflare-demo-01.md).

## 1. Commits (branch `deploy/ADG-FREE-STAGING-01`, started from `9b64b6a`)

| Commit | What |
| --- | --- |
| `dc6e310` | fix: wrangler ≥ 4.149 hands an OpenNext project to `opennextjs-cloudflare deploy`, which re-runs the command unquoted; `OPEN_NEXT_DEPLOY=true` makes it deploy directly. Proven by a real deploy whose `--message` contains parentheses. |
| `13719fb` | deploy: Cloudflare Access removed (Worker, tests, wrangler.jsonc, workflow, hosted checks); `run_worker_first` off; public-auth checks added. |
| `14ed8fa` | fix: the demo notice header's em dash made every answer from `POST /api/v1/remediation/{id}/exports` an unmarked HTTP 500 (found by the new smoke check); the header now carries an ASCII copy. Regression test added. |
| (this commit) | docs: deployment guide rewritten for the public free architecture; this handoff. |

## 2. Architecture as deployed

| Tier | Where | Plan | Notes |
| --- | --- | --- | --- |
| Worker `adg-demo-staging` | `https://adg-demo-staging.d-walkes.workers.dev` | Workers **Free** (confirmed by the owner) | Bindings: `ASSETS`, `WORKER_SELF_REFERENCE`, vars `ADG_WEB_URL`, `ADG_API_ORIGIN`, secret `ADG_ORIGIN_KEY` (`secret_text`). No Container, Durable Object, cron, KV, R2, D1. Version `a4946d8e` (tag `14ed8fad2807`). |
| API `adg-demo-api` | `https://adg-demo-api.onrender.com` (`srv-db6hltivcj2c73fujv30`) | Render **free**, Oregon, 1 instance | `docker/Dockerfile.backend` at `14ed8fa`, start `python -m app.origin_gate --host 0.0.0.0 --port 10000`, health `/health/live`, auto-deploy off. |
| Database | the owner's existing Supabase project (AWS us-west-2), database `adg_demo` | existing project | Session pooler, port 5432 (the direct host is IPv6-only). PostgreSQL 17.11. The project ref and pooler host are in the operator's notes, not in this public repository. |

Cloudflare Access: the Access application on the workers.dev hostname was deleted by the owner on
2026-10-12. Turning the dashboard control off first disabled the whole workers.dev route (error
1042); the route was re-enabled through the API, and the Access application was then deleted.

## 3. Public authentication (§§10–12 of the brief)

| Question | Answer (hosted evidence) |
| --- | --- |
| Can an anonymous visitor browse? | Yes: page shells, the Demo indicator and its facts, `/runtime`, `/version`, `/health/*`, `/auth/config`. Every data page says "Sign in to continue … Nothing is shown without an account." |
| Can an anonymous visitor mutate anything? | No. API data routes answer 401 (`/api/v1/demo`, `/risks/summary`, `/simulations`, `/remediation`, `/auth/me`); ingestion is `403 demo_only` for everyone. |
| Can any visitor use development sign-in? | Yes, with no credential, but only as the accounts in `ADG_DEV_AUTH_USERS`: `viewer` and `planner`. `admin`, `auditor`, `reviewer`, `governance`, `approver` → 404. |
| Which role does sign-in grant? | `viewer` → `viewer`; `planner` → `remediation_planner`. Never `admin`. |
| Can that role change demo data? | `planner` can run, store, re-run and delete simulations, and write, edit, simulate, submit and cancel change plans. It cannot approve or export a plan, manage alerts, run governance campaigns or reviews, ingest, or execute (403 on 8 privileged writes, asserted every deploy). |
| Can it open admin-only views? | No: the account holds no settings, alert-management, export or ingestion capability. The pages load, and their data calls are refused. |

The safety decision is a public demo identity with deliberately restricted permissions (the
brief's second acceptable outcome), chosen by the owner (option 3). The change is configuration
only (`ADG_DEV_AUTH_USERS` on Render); no code change was needed.

**Drift.** Planner records (stored simulations and their evaluations, and change plans) persist
and are shared. A visitor can delete another visitor's stored simulation. The integrated
fingerprint does not cover those tables (`app/demo_mode/lifecycle.py::canonical_digests`: current
state, object versions, scan runs, risk findings), so `verify` stays green. This was established
by reading the code, not by writing to the shared demo. Reset is manual: workflow `bootstrap`
with `confirm_reset = reset adg_demo`, or `python -m app.demo_mode reset`. No scheduled reset was
added; a weekly cadence is suggested and is the owner's decision.

## 4. Supabase and bootstrap

* Credential: the rotated password is stored at `~/.config/adg-staging/supabase-db-password` (600). It
  is not the password pasted into chat earlier (compared by hash, never printed), and that
  password was not used. The full URL is at `~/.config/adg-staging/adg-database-url` (600).
* `CREATE DATABASE adg_demo` through the session pooler, then `SELECT current_database()` →
  `adg_demo`.
* `python -m app.demo_mode reset` (repo code, from a workstation in the US East):
  300 runs (297 succeeded, 2 failed, 1 partial, as the dataset intends), 41,756 observations,
  23/23 probes, risk pass 9,666 matched / 9,386 open, fingerprint `228b9dce…f9efd0`.
  **Duration 1,764 s (29 min), ingestion 819 s.** Round trips across the continent dominate; next
  to the database the documented time is 80–90 s.
* `verify`: ok, 23 probes, 300 scan runs, fingerprint
  `228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`.
* `status`: database `adg_demo`, marker `demo / null-meridian-v1` (marked by
  `python -m app.demo_mode reset`), generations g1–g3 applied.
* Null Meridian logical fingerprint: `db736602850a4a82936f861b68466e7c54b61a55192b04f6d062cbe8585c28a0`, match.

## 5. Origin gate (hosted)

| Case | Result |
| --- | --- |
| Direct request with no key (`/runtime`, `/version`, `/health/ready`, `/auth/config`, `/api/v1/demo`, `/api/v1/risks/summary`, `POST /auth/dev/login`) | 403 `This API answers only its staging proxy.` |
| Direct request with a guessed key (64 random hex) | 403 |
| `/health/live` | 200 (Render's health check; reads nothing) |
| Keyed request (from the operator's machine) | 200, demo / synthetic / null-meridian-v1 |
| Through the Worker | 200 (smoke 23/23) |
| A visitor's own `X-ADG-Origin-Key` | replaced by the Worker's on the whole path (unit test `routing.test.ts`) |
| Server without a key | refuses to start (`test_without_a_key_the_server_does_not_start`); a Worker without its key sends nothing (503, unit test) |

## 6. Measurements

**Render cold start** (the site's first public request, after the service slept at 18:34:34,
15 min 38 s after the last request):

| Step | Elapsed |
| --- | --- |
| Homepage requested through the Worker | 0 s |
| Container process started | +28 s |
| Demo Mode startup checks passed, API ready | +64.6 s |
| First API answer (`/runtime`) | +73.8 s (Render log) |
| Homepage delivered | **HTTP 200 at +72.8 s** (client clock): the page waited, it did not fail |

The Worker's 120 s server-side API timeout (`API_WAKE_TIMEOUT_MS`) covers this with about 45 s
to spare, so no timeout change was needed. A Render restart (zero downtime, the old process
serves until the new one is ready) took 37 s from process start to ready.

**Warm latency** (through the Worker, from the US East): `/runtime` 0.45–0.6 s; homepage 0.8–1.2 s;
SSR pages median 0.6–1.2 s (risks about 1–2 s); keyed `/runtime` directly at Render 0.19–0.23 s;
API → database 10–18 ms.

**Worker CPU on Workers Free** (`wrangler tail`, 218 invocations, plus Cloudflare analytics):

| Request type | CPU median | CPU max | Over 10 ms |
| --- | --- | --- | --- |
| API proxy (`/api/v1`, `/auth`, `/health`, `/runtime`, `/version`) | 1 ms | 4 ms | 0 of 35 |
| SSR `/access/explain` | 136 ms | 229 ms | 18 of 19 |
| SSR `/demo` | 109 ms | 434 ms | 17 of 17 |
| SSR `/risks` | 104 ms | 187 ms | 16 of 17 |
| SSR `/` | 67 ms | 378 ms | 23 of 29 |
| SSR `/identities/…` | 56 ms | 218 ms | 15 of 17 |
| SSR `/resources/…` | 48 ms | 478 ms | 19 of 20 |
| SSR `/simulations` | 28 ms | 206 ms | 16 of 16 |
| Built static files | not invoked (served before the Worker) | | |

Outcomes: 218 of 218 `ok`; Cloudflare analytics for the window reported 411 requests, status
`success`, 0 errors, no `exceededCpu`. Load: 120 sequential SSR requests across 8 routes, then 3
bursts of 10 concurrent `/access/explain` requests: all 200.

Cloudflare documents 10 ms per request on Free, with "some built-in flexibility" for a Worker
that "infrequently" runs over, and termination (Error 1102) for one that does so "consistently".
These pages run over consistently and have not been terminated so far. That is the main
limitation; see §8.

## 7. Hosted validation

* **Smoke** (`smoke.mjs`, public URL, Render origin, the database host and project ref as leak
  canaries): **23/23**. The count is 23, not the brief's 21: two Access checks became two
  public-auth checks, and the origin check (added in `9b64b6a`) and the privileged-write check are
  new. No substantive check was removed.
* **Browser** (`browser.mjs`, headless Chromium, as an anonymous visitor then `planner`):
  **22/22**: Demo indicator and its facts, footer, sign-in offering only `planner, viewer`, `/demo`
  A–H with g1–g3, scenarios A and B folders, B's principal, F's explanation (both routes), G's
  six-level chain, risks 3 / 8 / 1957 / 423 / 6995, scenario H through the real simulation editor
  ("changes nobody's access"), 11 primary sections, status, web-tier and API ingestion refusals,
  `/runtime`, the demo header, sign out, no page errors. Console: the intentional 405 and a 404 for
  `/favicon.ico` (the app ships none).
* **Safety:** runtime `demo`, dataset `null-meridian-v1`, ingestion `403 demo_only` (anonymous and
  planner), no remediation execution route, `ADG_REMEDIATION_EXECUTION_MODE=disabled` (the API
  refuses to start otherwise). The Render environment holds exactly 11 settings, none of them a
  collector key, OIDC setting, AD/LDAP/SMB/WinRM credential or alert destination; `/runtime`
  states no production AD or file servers are connected. Database: `adg_demo` only.

## 8. Known limitations and public-launch risks

1. **Workers Free CPU.** SSR pages use 28–136 ms CPU at the median against a 10 ms limit. They work
   today; if Cloudflare starts enforcing (Error 1102, `exceededCpu` in the tail or analytics), the
   free options are, cheapest first: (a) run the Next.js server on Render Free as a second web
   service and keep the Worker as a thin proxy (its proxy path is 1–4 ms); (b) put the Next.js
   frontend on another free Node host. Workers Paid ($5/month, 30 s CPU) would be the paid fix
   and is **not authorized**.
2. **Cold start of about 73 s** after 15 idle minutes. Visitors wait, they do not error.
3. **Shared writable demo**: planner records accumulate until a manual reset.
4. **Reset time from far away**: about 29 min from the US East; run it from somewhere near Oregon, or
   expect the wait.
5. **CI is not yet configured**: the `cloudflare-staging` GitHub environment holds no secrets,
   so the push-triggered run fails closed at preflight (it lists exactly the 8 names below;
   nothing deploys). This deployment was made from a workstation. To enable CI: create a
   Cloudflare API token (Workers Scripts: Edit, Account Settings: Read) and set
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ADG_DATABASE_URL`, `ADG_ORIGIN_KEY`,
   `RENDER_API_KEY` (secrets) and `ADG_STAGING_URL`, `ADG_API_ORIGIN`, `RENDER_SERVICE_ID`
   (variables).
6. **Banner wording**: the development-auth banner says "anyone … can sign in as any account".
   That is true of the two configured accounts, but could be read more broadly.
7. No favicon (cosmetic, pre-existing).

## 9. Rollback

As in the guide §10. Fastest: disable the workers.dev route, or suspend the Render service.
Previous Worker versions: `59277a78` (13719fb), `dd621769` (9b64b6a, fail-closed).
