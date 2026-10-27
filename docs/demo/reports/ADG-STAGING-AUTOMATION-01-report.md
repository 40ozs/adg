# ADG-STAGING-AUTOMATION-01: push-triggered staging restored

**Verdict: `ADG_STAGING_AUTOMATION_RESTORED`**

A normal push to `deploy/ADG-HOSTED-NEXT16-01` now deploys ADG staging end to end, with no
manual step: Free staging run **40456911186** (push, `f730d99`) passed preflight and the code
gates, skipped bootstrap, deployed Render and the Worker, and passed the hosted checks (verify ok,
Null Meridian fingerprint match, smoke 23/23, browser 22/22). No manual deployment and no database
reset was done in this task.

Getting there needed the `cloudflare-staging` environment restored (8 names) and two one-line,
owner-approved workflow fixes that the restored environment exposed (§6). No application,
deployment-command, bootstrap or secret-handling logic changed.

| | SHA |
| --- | --- |
| Starting `main` | `7ce195dde4b2b188dc79717dd6c0999dd92dcdb3` |
| Starting deploy head | `c57146d636ad04f186b836ec12e8f8722c23e27f` |
| Reset report branch `docs/ADG-DEMO-RESET-01` | `77d5f0d5218c51ecdcbfd8d34c08d158d1229681` |
| **Final `main` = final deploy head** | **`f730d9922f2f76583d0c55eea9afa917293f870a`** |

## 1. Clean-tree gate

`git status --short` and `--porcelain=v1` empty; `git diff --quiet` and `git diff --cached
--quiet` exit 0, on `main`. Present and ignored (not dirty): `.env`, `backend/.venv/`, Python
caches, `frontend/node_modules/`.

## 2. Refs and report-branch scope

After `git fetch --prune`: `origin/main` = `7ce195d`, `origin/deploy/ADG-HOSTED-NEXT16-01` =
`c57146d`, `origin/docs/ADG-DEMO-RESET-01` = `77d5f0d`, all as expected. The report branch: merge
base = `7ce195d`, ahead 2, behind 0. Its commits `36a0157` (report + evidence) and `77d5f0d` (CI
result note) touch only `docs/demo/reports/ADG-DEMO-RESET-01-report.md` and
`docs/demo/reports/ADG-DEMO-RESET-01-evidence/` (37 files, 2,931 insertions, no other path).

## 3. Application-tree equivalence

`7ce195d` (`ci: use self-hosted Linux runners`) is the only commit in `c57146d..7ce195d`, and it
changes only `.github/workflows/ci.yml`. `git diff c57146d 7ce195d -- frontend backend database
docker` is empty, so the contracts, migrations, Worker (`frontend/cloudflare/`) and Render
(`frontend/render/`, `docker/Dockerfile.render`) implementations are byte-identical to the
deployed `c57146d`. The two workflow commits below change only
`.github/workflows/cloudflare-staging.yml`. **The application tree at `f730d99` equals `c57146d`.**

## 4. Report merge

`git merge --ff-only 77d5f0d` on `main`; `origin/main` re-fetched immediately before the push and
still `7ce195d`; pushed `7ce195d..77d5f0d` (no force).

## 5. `cloudflare-staging` environment

Before: 0 secrets, 0 variables (repository level also empty). No protection rules or branch policy.

| Name | Kind | Source (authoritative, existing) |
| --- | --- | --- |
| `ADG_DATABASE_URL` | secret | `~/.config/adg-staging/adg-database-url` |
| `ADG_ORIGIN_KEY` | secret | `~/.config/adg-staging/adg-origin-key` |
| `RENDER_API_KEY` | secret | `~/.config/adg-staging/render-api-key` |
| `CLOUDFLARE_ACCOUNT_ID` | secret | the single account of the operator's wrangler login |
| `CLOUDFLARE_API_TOKEN` | secret | **new**, created by the owner (see below) |
| `ADG_STAGING_URL` | variable | `https://adg-demo-staging.d-walkes.workers.dev` |
| `ADG_RENDER_ORIGIN` | variable | `https://adg-demo-web.onrender.com` |
| `RENDER_SERVICE_ID` | variable | `srv-db6jbmlg1s2s739i4fsg` |

All 8 names present (`config/env-secrets-names.json`, `config/env-variables.json`; names and
timestamps only for secrets). Values were piped from files into `gh secret set`, never printed.

**`CLOUDFLARE_API_TOKEN`.** No long-lived Cloudflare API token existed anywhere on the operator's
machine; every earlier Worker deploy used wrangler's OAuth login, whose access token expires hourly
and has no token-management scope, so it was neither usable in CI nor able to mint one. The owner
created a token from the dashboard's "Edit Cloudflare Workers" template scoped to the one account,
saved it with a no-echo prompt to `~/.config/adg-staging/cloudflare-api-token` (mode 600), and it
was set from there. `GET /user/tokens/verify` → `active`; with it,
`GET …/workers/scripts/adg-demo-staging/subdomain` → `enabled: true` and the script's deployments
are readable. No existing credential was changed.

### Safe target-identity verification (hashes compared in memory; nothing printed)

- **Database.** The secret equals Render `adg-demo-web`'s `ADG_DATABASE_URL` (SHA-256 equal);
  scheme `postgresql+psycopg`, database `adg_demo`, Supabase session pooler, port 5432 — the same
  target recorded in `ADG-DEMO-RESET-01-evidence/db/target-identity.json`.
- **Origin key.** 64 characters, equal to Render's `ADG_ORIGIN_KEY`. The live Worker's
  `ADG_ORIGIN_KEY` (a secret, unreadable) is the same key in effect: the public Worker answered 200
  through the gate, while the Render origin answered **403** without the key and 200 with the file's
  key.
- **Render.** `RENDER_SERVICE_ID` is service `adg-demo-web`, branch `deploy/ADG-HOSTED-NEXT16-01`,
  auto-deploy off, not suspended; its URL is exactly `ADG_RENDER_ORIGIN`, which also matches the
  then-live Worker `adfb5c6d`'s `ADG_RENDER_ORIGIN` binding.
- **Public staging URL.** `ADG_STAGING_URL` is the `adg-demo-staging` workers.dev route, enabled
  (200; 84 s on a Render cold start).
- **Cloudflare.** The token/account pair addresses `adg-demo-staging` (above). The account id is not
  recorded here.

## 6. Preflight proof, then three push runs

**Local preflight.** The workflow's own preflight script (extracted from the YAML unchanged) ran
locally with the same 8 values: exit 0, `database name: demo-scoped` (all names present, both URLs
https, key ≥ 32, demo-scoped database, no reset requested).

| Run | Head | Preflight | Gates | Bootstrap | Render | Worker | Hosted checks | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40454840096 | `77d5f0d` | pass | pass (89/89) | skipped | `dep-dbg39pgm7kps73b97460` live | **skipped** | **skipped** | run reported *success* |
| 40455444650 | `898f59f` | pass | pass | skipped | `dep-dbg3cs0m7kps73b9fjkg` live | `61095174` | **fail** | Playwright on Ubuntu 26.04 |
| **40456911186** | **`f730d99`** | **pass** | **pass (89/89)** | **skipped** | **`dep-dbg3k3t9fdbs73cm2v40` live** | **`d2ca8cfe`** | **pass** | end to end |

All three: event `push`, branch `deploy/ADG-HOSTED-NEXT16-01`, each head = the `main` SHA pushed
just before it; the deploy branch was fast-forwarded each time after re-fetching and checking
ancestry. No `workflow_dispatch`, no bootstrap.

**Fix 1 (`898f59f`, `ci(staging): let the Worker deploy run when bootstrap is skipped`).** The
`deploy` job (`needs: [deploy-origin]`, no `if:`) took the implicit `success()`, which is false
when any job in its dependency chain is skipped; on every push `bootstrap` is skipped, so the
Worker deploy and the hosted checks could never run on the push path, yet the run reported
success. Added `if: ${{ always() && needs.deploy-origin.result == 'success' }}`, the guard
`deploy-origin` and `checks` already use. Push runs still skip bootstrap; a failed, cancelled or
skipped `deploy-origin` still cannot reach the Worker deploy.

**Fix 2 (`f730d99`, `ci(staging): run hosted checks on ubuntu-24.04`).** `ubuntu-latest` now
resolves to Ubuntu 26.04 (image `ubuntu26/20261012.163`), where the pinned Playwright 1.56.1 stops
with `Playwright does not support chromium on ubuntu26.04-x64`; in run 40455444650 verify,
fingerprint and smoke (23/23) passed and the browser gate never started. **Runner/toolchain
incompatibility, not an ADG application failure.** The `checks` job alone now runs on
`ubuntu-24.04`; `PLAYWRIGHT_VERSION` stays `1.56.1`; other jobs keep `ubuntu-latest`.

Each fix is a one-line diff, approved by the owner before it was committed.

## 7. Final run 40456911186 (`f730d99`), timeline UTC 2026-10-27

| Job | Result | Window |
| --- | --- | --- |
| Preflight (configuration, fail closed) | success, `database name: demo-scoped` | 05:43:16–05:43:21 |
| Deployment code gates | success, 3 files / 89 tests | 05:43:16–05:43:38 |
| Bootstrap demo database | **skipped** | — |
| Deploy origin (Render) | `dep-dbg3k3t9fdbs73cm2v40` of `f730d9922f2f` **live** | 05:43:42–05:46:17 |
| Deploy Worker | version **`d2ca8cfe-d5f9-44c7-b7e9-a4550e4a952d`**, tag `f730d9922f2f`, 118 asset files | 05:46:21–05:48:28 |
| Hosted checks | verify `ok`, fingerprint `228b9dce…`; Null Meridian `db736602…85c28a0` **match**; smoke **23/23**; browser **22/22** | 05:48:32–05:50:48 |

Smoke includes "the Render origin refuses a request that did not come through the Worker — 12
requests → 403 without the origin key or with a guessed one; only /health/live open": direct origin
protection is intact (also checked by hand after the run: `/` and `/api/v1/risks/summary` → 403).
Browser gate "no fatal browser errors — 1 console errors" is the known `/favicon.ico` 404.
Evidence: `runs/run-3-40456911186.json`, `runs/run-3-40456911186-key-lines.txt`.

## 8. Reset state survived (read-only)

Signed in as `planner` through the public Worker after the final deploy (05:51:46Z,
`hosted/risks-after.json`): `interpretation.basis_current` **true**, `evaluated_basis`
`0d1cc4aa0f1f5f3d86932cdd85c7a0e1`, evaluation `ac1ebb47-…`, total 9,386, severities
3 / 8 / 1957 / 423 / 6995 — identical to ADG-DEMO-RESET-01 §7. The reason "The last evaluation
predates snapshot recording" is **absent** from the API summary and from the rendered `/risks` HTML;
the one remaining reason is the designed coverage line ("Collection is incomplete: 98 of 100
collection scopes …"), which the page still shows. Alembic (`select version_num from
alembic_version`, read-only session): one row, **`0018_risk_evaluation_basis`**, database `adg_demo`
(`hosted/alembic-after.json`). No reset was run.

## 9. React #418

Committed detector `ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js` with
substitutions 1 and 3 of `ADG-HOSTED-NEXT16-01-evidence/hosted-script-substitutions.txt` (browser
path, `ADG_SIGN_IN_AS=planner`), Playwright 1.62.1, Node 22.23.3 arm64, against
`https://adg-demo-staging.d-walkes.workers.dev` (Worker `d2ca8cfe`, Render `f730d99`).

| Run | Window (UTC) | Records | Full loads | Client transitions | **#418** | Hydration family | Other errors | Failed steps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Positive control (`GATE_CONTROL=1`, 1 round, 1 reload) | 05:52:00–05:56:07 | 80 | 62 | 18 | **62** (every full load) | 0 | 0 | 0 |
| Hosted (`GATE_ROUNDS=2`, 3 reloads) | 05:56:07–06:07:00 | 228 | 192 | 36 | **0** | **0** | **0** | 0 |

Known error recorded separately: `/favicon.ico` 404 (once per run). No failed responses, no Render
cold-start (502/503) or other infrastructure errors in either run; no 390 px horizontal overflow.
**#418 = 0, hydration errors = 0, application console errors = 0.** Evidence: `hydration/`.

## 10. Automation verdict

> Can a normal push to `deploy/ADG-HOSTED-NEXT16-01` now deploy ADG staging without manual
> intervention?

**YES** — run 40456911186, triggered only by the fast-forward push of `f730d99`.

## 11. Final state

- `main` = `origin/main` = `deploy/ADG-HOSTED-NEXT16-01` = **`f730d9922f2f76583d0c55eea9afa917293f870a`**.
- Render `adg-demo-web` tracks `deploy/ADG-HOSTED-NEXT16-01`, auto-deploy off (the workflow deploys
  by commit id), live `dep-dbg3k3t9fdbs73cm2v40` @ `f730d99` (`hosted/render-service-after.json`).
- Worker `adg-demo-staging` 100% `d2ca8cfe` (`hosted/worker-deployments-after.txt`).
- `docs/ADG-DEMO-RESET-01` deleted from origin after `git merge-base --is-ancestor 77d5f0d main`
  held. No historical deploy branch was deleted.
- This report is on the docs-only branch `docs/ADG-STAGING-AUTOMATION-01` (off `f730d99`), not on
  `main` or the deploy branch, so it triggers no deployment and the final refs stay equal.

**Rollback.** Render `dep-dbg12pgm7kps73b2oop0` (`c57146d`) + Worker `adfb5c6d`; the application
tree is identical, so a rollback changes nothing a visitor sees.

## 12. Limitations

1. **Normal `CI` is red on `main`** (and was before this task: `77d5f0d`, both ADG-DEMO-RESET-01
   commits). One backend test, `test_demo_mode.py::TestNoProductionCodePath::
   test_the_application_imports_without_httpx`, spawns the tool-cache Python on the self-hosted
   runner (`7ce195d`), which fails with `libpython3.13.so.1.0: cannot open shared object file`;
   6,018 other tests pass. Runner environment, not application code; not changed here.
2. **`ubuntu-24.04` pin** holds while GitHub keeps that image. Moving `checks` back to
   `ubuntu-latest` needs a Playwright release that supports Ubuntu 26.04.
3. **New `CLOUDFLARE_API_TOKEN`** (owner-created, account-scoped "Edit Cloudflare Workers").
   Its lifetime and rotation are the owner's; when it is rotated, update both the file and the
   GitHub environment secret.
4. **Three Render deploys and two Worker versions** came from the three push runs
   (`77d5f0d`, `898f59f`, `f730d99`); all from identical application trees.
5. The Worker's own origin-key secret cannot be read back; its match with Render is shown by
   behaviour (gate accepts the Worker, refuses requests without the key).
