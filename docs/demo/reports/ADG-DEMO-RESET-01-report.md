# ADG-DEMO-RESET-01: hosted demo reset and canonical deploy

**Verdict: `ADG_DEMO_RESET_AND_DEPLOY_COMPLETE_WITH_LIMITATIONS`**

The synthetic public-demo database `adg_demo` was reset with the repository's own
`python -m app.demo_mode reset`, after its identity was proven and its state captured. The stale
"The last evaluation predates snapshot recording" reason is gone from `/api/v1/risks/summary`
and from the rendered `/risks` page. `deploy/ADG-HOSTED-NEXT16-01` was fast-forwarded
`02031b5` → `c57146d`. The push-triggered **Free staging** run failed closed at preflight
because the `cloudflare-staging` GitHub environment holds no configuration. With the owner's
approval, `c57146d` was then deployed by hand with the workflow's own steps (Render API deploy
of that commit, Worker static files from `Dockerfile.render --target web` of that commit, then
`wrangler deploy`). Hosted after deploy: smoke **23/23**, browser gate **22/22**, DEMO-03
**39/39**, React #418 **0** (positive control 62/62), `verify` ok, Null Meridian fingerprint match.

Limitations (details in §12):

1. The workflow did not deploy (preflight: no environment secrets or variables). The deploy was manual, of the same exact commit.
2. `origin/main` is `7ce195d`, not the `c57146d` the task expected. That commit is CI-only and was left alone.
3. The public workers.dev route had been disabled (Cloudflare error 1042) since some time after 2026-10-17. It was re-enabled with the owner's approval.
4. CI on the deploy branch: Pester could not download its module from PSGallery (infrastructure; the same SHA passed CI on 2026-10-17).

## 1. Refs and clean-tree gate

| Item | Expected | Found |
| --- | --- | --- |
| Worktree / index | clean | clean (`git status --porcelain` empty, both `git diff --quiet` checks 0) |
| `origin/deploy/ADG-HOSTED-NEXT16-01` | `02031b526b80…` | `02031b526b802cd05380ac249b234fd2db556e8c` |
| `origin/main` | `c57146d636ad…` | **`7ce195dde4b2b188dc79717dd6c0999dd92dcdb3`** (moved) |
| `02031b5` ancestor of `c57146d` | yes | yes |

**`main` moved: inspected.** `7ce195d` ("ci: use self-hosted Linux runners", 2026-10-18) is a
child of `c57146d` and changes only `.github/workflows/ci.yml`.
`git diff --quiet c57146d 7ce195d -- backend database` holds, and so does the frontend:
application code is identical. The task's deploy target, `c57146d`, was kept. `main` was not
moved. The reset ran from the checkout at `7ce195d`, whose backend is byte-identical to
`c57146d`'s.

## 2. Owner authorization

The owner authorized a reset of **only** `adg_demo`, the synthetic ADG public-demo staging
database, and no other database or service. The repository's demo safety checks were not
touched. `reset` refuses any database that is not marked demo or empty
(`app.demo_mode.marker`), and the marker was present (below). Two further decisions were made
by the owner during the task: re-enable the workers.dev route, and deploy by hand after the
expected preflight failure.

## 3. Target identity (before the reset)

From the operator's secret file and the Render API; no URL, host, user or password printed
(`ADG-DEMO-RESET-01-evidence/db/target-identity.json`, re-captured after the task with the same result):

| Check | Result |
| --- | --- |
| Database name | **`adg_demo`** (local secret file and Render `ADG_DATABASE_URL`) |
| Same database as `adg-demo-web` | **yes**: the local URL and the Render service's `ADG_DATABASE_URL` are byte-identical (SHA-256 compared in one process) |
| Runtime target | Render `ADG_RUNTIME_MODE=demo`; `status.runtime_mode = demo` |
| Dataset | `null-meridian-v1` (Render env and `status`) |
| Demo marker | `runtime_mode demo`, dataset `null-meridian-v1`, marked 2026-10-12T17:33:33Z by `python -m app.demo_mode reset` |
| Alembic | one head: **`0018_risk_evaluation_basis`** (the repository's only head; no GRAPH migration) |
| Render service | `adg-demo-web`, branch `deploy/ADG-HOSTED-NEXT16-01`, auto-deploy **off**, not suspended |

## 4. Pre-reset state (read-only)

`db/pre-status.json`, `db/pre-verify.json`, `db/pre-fingerprint.json`, `db/pre-tables.json`:

- `verify`: **ok**, 23 probes, fingerprint `228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`.
- Null Meridian logical fingerprint: `db736602850a4a82936f861b68466e7c54b61a55192b04f6d062cbe8585c28a0`, **match**.
- `status`: generations g1, g2, g3 applied, baseline applied.
- `risk_evaluations`: 1 row, **`basis_token` NULL** (`db/pre-risk-eval.json`). This is the
  cause of the stale reason. The database was migrated to 0018 without a reset
  (ADG-HOSTED-NEXT16-01), so the one evaluation predates basis recording.

**Visitor-state check.** Exact row counts of every table (`db/pre-tables.json`). Every table a
visitor or user action writes was **empty**: `alert_watches`, `governance_audit_events`, all
`remediation_*`, `resource_owners`, all `review_*`, `simulations`, `simulation_evaluations`,
`collector_checkpoints`, `scan_run_checkpoints` = 0. The 3 `alerts`/`alert_events`/
`alert_deliveries` rows all carry the synthetic baseline instant `2026-10-01 06:00:00+00`
(`db/pre-alerts.json`), the dataset's risk pass, not a visitor. The public accounts cannot
write these tables anyway (smoke: 8 privileged writes → 403). Nothing unexpected would be
destroyed, so the reset went ahead.

## 5. Reset

```
python -m app.demo_mode reset --report <scratch>/reset-report.json
  env: ADG_RUNTIME_MODE=demo ADG_DEMO_DATASET=null-meridian-v1 ADG_DATABASE_URL=<secret file>
       ADG_COLLECTOR_API_KEYS= ADG_ALERT_POLICY_PATH= ADG_REMEDIATION_EXECUTION_MODE=disabled
       ADG_LOG_LEVEL=WARNING            (the workflow's bootstrap environment)
```

Run from this Mac (`backend/.venv`, Python 3.13), 2026-10-27T02:15:01Z–02:44:55Z, **exit 0**
(`db/reset.out`, `db/reset-report.json`):

```
Demo reset: Null Meridian (null-meridian-v1, seed null-meridian-v1) into adg_demo.
generations: g1, g2, g3 (canonical baseline)
runs: 300 {'failed': 2, 'partial': 1, 'succeeded': 297}; observations applied: 41756
probes: 23/23 present
risk pass: 9666 matched, 9386 open, dated 2026-10-01T06:00:00+00:00
fingerprint: 228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0
duration: 1792.8s (ingestion 823.9s)
```

The 2 failed and 1 partial runs are the dataset's designed coverage drill (NM-SEC-FS01 g1,
NM-FS-03 g3, NM-MIG-FS01 g3). No ad-hoc SQL was used, and nothing was truncated or edited by hand.

## 6. Post-reset verification

| Check | Pre-reset | Post-reset |
| --- | --- | --- |
| `verify` | ok, 23 probes | **ok**, 23 probes |
| `verify` fingerprint | `228b9dce…f9efd0` | **`228b9dce…f9efd0`** (every digest and count identical) |
| Null Meridian fingerprint | `db736602…85c28a0` match | **`db736602…85c28a0` match** (every digest identical) |
| `status` | `null-meridian-v1`, g1–g3, baseline applied | same |
| Alembic | `0018_risk_evaluation_basis` | **`0018_risk_evaluation_basis`** (one head, no GRAPH) |
| Row counts, every table | (§4) | **identical** |
| `risk_evaluations.basis_token` NULL | 1 of 1 | **0 of 1** |

**Expected differences.** The reset recreates the same deterministic baseline, so both
fingerprints and every count are unchanged. The fingerprints do not cover the evaluation's
basis token. The one intended difference is that the risk evaluation now records the collected
state it read (`basis_token`), so the API can say which snapshot it evaluated. The demo marker
keeps its original 2026-10-12 timestamp (it identifies the database and is not re-stamped).

## 7. `/risks` change

| | Before (ADG-HOSTED-NEXT16-01 §14, DB row above) | After reset (02:47:21Z, old deploy) | After deploy (02:57Z) |
| --- | --- | --- | --- |
| "The last evaluation predates snapshot recording…" | present | **absent** | **absent** (API and rendered `/risks` HTML) |
| `interpretation.evaluated_basis` / `basis_current` | null / null | `0d1cc4aa…` / **true** | same / **true** |
| `interpretation.reasons` | collection incomplete + predates-snapshot | collection incomplete only | collection incomplete only |
| collection verdict | incomplete, 98 of 100 scopes | same | same; the rendered page still shows "98 of 100 collection scopes" |

The remaining reason is the dataset's designed one: 98 of 100 collection scopes complete,
1 unavailable (NM-FS-03, stale evidence 2026-09-17, findings held), 1 partial (NM-MIG-FS01).
A hosted capture of the pre-reset summary could not be taken: the route was disabled (error 1042)
when it was attempted, and the database was mid-reset once the route was back. The pre-reset
evidence is the NULL `basis_token` row plus the previous report's hosted observation.
Evidence: `hosted/post-reset-hosted-risks-summary.json`,
`hosted/post-deploy-hosted-risks-summary.json`, `hosted/post-deploy-risks-page.json`.

## 8. Hosted checks before advancing the deploy branch (old deploy `53b81a6`, reset database)

| Check | Result |
| --- | --- |
| Smoke (`smoke.mjs`, unmodified) | **23/23** (`hosted/smoke-pre-deploy.log`) |
| DEMO-03 | **39/39** (`hosted/demo03-pre-deploy.json`) |
| `verify` + fingerprint | §6 |

## 9. Deploy branch

At 02:49Z, after a fresh `git fetch` proved `origin/deploy/ADG-HOSTED-NEXT16-01` was still
`02031b5` and an ancestor of `c57146d`:

```
git push origin c57146d636ad04f186b836ec12e8f8722c23e27f:refs/heads/deploy/ADG-HOSTED-NEXT16-01
   02031b5..c57146d   (fast-forward; no force)
```

## 10. Staging workflow and deployment

**Free staging (ADG demo), run `40440655386`** (push, `c57146d636ad04f186b836ec12e8f8722c23e27f`):

| Job | Result |
| --- | --- |
| Deployment code gates | **success** |
| Preflight (configuration, fail closed) | **failure**: `Missing staging configuration` (the `cloudflare-staging` environment has 0 secrets, 0 variables) |
| Bootstrap | skipped (push never bootstraps; none was requested) |
| Deploy origin / Deploy Worker / Hosted checks | skipped |

No other SHA was deployed. Render auto-deploy is off, so the push alone deployed nothing.

**Manual deploy of the same commit, with the owner's approval** (`docs/deployment/cloudflare-demo.md` §5 workstation path):

| Step | Result |
| --- | --- |
| Render `POST /v1/services/<adg-demo-web>/deploys {"commitId": "c57146d6…"}` | **`dep-dbg12pgm7kps73b2oop0`**, created 02:50:13Z, **live 02:53:30Z**, commit `c57146d636ad04f186b836ec12e8f8722c23e27f` |
| Gate (Render logs) | start 02:52:11Z; web ready +5.3 s; API ready and port open +68.0 s; first answer +71.6 s |
| Worker static files | `git archive c57146d \| docker build --platform linux/amd64 -f docker/Dockerfile.render --target web --build-arg RENDER_GIT_COMMIT=c57146d…`, `.next/static` → `npm run cf:assets` (61 files; 58 already uploaded, 3 new) |
| `wrangler deploy --var ADG_RENDER_ORIGIN:… --tag c57146d636ad` | **Worker version `adfb5c6d-b496-4e16-a2a8-1f17174c1471`**, 02:53:49Z |
| workers.dev route | enabled (re-enabled 02:16:01Z, owner-approved; still enabled after the deploy) |

Before this task: Render `dep-db8qhucs728c73anb3n0` (`53b81a6`) and Worker `723ffc1c`. Those are the
rollback targets (`deploy/render-deploys-before.jsonl`).

**CI run `40440655408`** (push to the deploy branch, `c57146d`): Docker Compose and Frontend
**success**. Collector (Pester) **failure** at `Install-Module Pester`: "Package 'Pester' failed
to download" (PSGallery). Infrastructure: the same SHA passed CI on `main` (run 39049159000,
2026-10-17). Backend: see §12.

## 11. Hosted validation after deployment

| Check | Result |
| --- | --- |
| Smoke | **23/23** (`hosted/smoke-post-deploy.log`) |
| Browser gate (`browser.mjs`, unmodified, headless shell 1234 arm64) | **22/22** (`hosted/browser-post-deploy.log`) |
| `verify` | **ok**, `228b9dce…f9efd0` (`db/post-deploy-verify.json`) |
| Null Meridian fingerprint | **`db736602…85c28a0` match** |
| Origin protection | **403**: 12 direct requests to Render without the key or with a guessed one; only `/health/live` open |
| Stale pre-snapshot reason | **absent** (API and rendered page) |

**DEMO-03** (committed script, the three documented substitutions from
`ADG-HOSTED-NEXT16-01-evidence/hosted-script-substitutions.txt`, as `planner`): **39/39**
(`demo03-post-deploy/`). Coverage semantics: totals not called totals while a source is
unavailable, the coverage line beside them, "not exhaustive", the unavailable server's findings
held, NM-FS-03 named with its evidence age. The drill: **timed out** stated in words and a
symbol, with no partial list; the standard-window retry stops **incomplete** at a named
boundary and is labelled partial; the folder-only recovery is **complete, recovered**. Browser
timings: 3,936 ms / 4,034 ms / 6,499 ms. No application console errors.

**React #418** (committed detector `ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js`,
substitutions 1 and 3, as `planner`):

| Run | Window (UTC) | Records | Full loads | Client transitions | **#418** | Hydration family | Other errors | Failed steps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Positive control (`GATE_CONTROL=1`, 1 round, 1 reload) | 02:57:14–03:01:09 | 80 | 62 | 18 | **62** (detected on every full load) | 0 | 0 | 0 |
| Hosted (`GATE_ROUNDS=2`, 3 reloads) | 03:01:10–03:12:14 | 228 | 192 | 36 | **0** | **0** | **0** | 0 |

Both rounds had 114 records each. No 390 px load scrolled horizontally. Known error recorded
separately: `/favicon.ico` 404. **#418 = 0, hydration errors = 0, application console errors = 0.**

## 12. Infrastructure timing and errors

- **Route disabled.** Before this task, `https://adg-demo-staging.d-walkes.workers.dev` answered
  Cloudflare **error 1042** (the Worker's workers.dev subdomain had `enabled: false`; Worker last
  modified 2026-10-17T16:50Z, after the last recorded task). It was re-enabled at 02:16:01Z with the
  owner's approval (`deploy/route-reenable.json`). The first request then took 17.4 s (Render cold start).
- **Render cold start before the deploy:** gate API ready +68.5 s, first web answer +94.2 s (02:47Z).
- **Gate `upstream_unavailable` "socket hang up" ×8, 02:54:58–02:55:34Z** (`deploy/render-gate-logs.txt`),
  all within the browser gate's window (02:54:27–02:55:38Z). The gate logs this when the *client*
  closes before the answer finishes and the gate destroys the relay (`frontend/render/gate.ts`
  lines 182–185), as when Playwright closes a page with RSC prefetches in flight. No 502 reached a
  client, and the browser gate passed 22/22. The gate logged nothing from 02:55:35Z through the
  end of the #418 run. Not an application failure.
- No Render 502/503 cold-start events occurred during DEMO-03 or the #418 runs.
- **DEMO-03 timing on Render Free 0.1 CPU:** the retry took 4,034 ms against the 4-second window
  and still stopped *incomplete at its checkpoint*, as designed. Both runs in this task passed
  39/39. The accepted limitation (ADG-HOSTED-NEXT16-01 §14) stands: a run can time out at the
  ordering/read step on this capacity, which is capacity against a fixed window, not semantics.
- The test Mac's load average was 4.8–6.8 during the hosted checks (the owner's other workloads),
  with `caffeinate` held for every long run.
- **CI Pester** module download failure (§10). The backend job was still running when this report
  was written; its result is in the run.

Out of scope and not re-examined: the `/risks` severity-strip bullet overlap, deleted/no
inconsistency, UI-UPDATE, COMPACT-IDENTITY, GRAPH, and historical report wording. The favicon 404 is
still present (recorded above as the known error).

## 13. Final state

| Item | Value |
| --- | --- |
| `origin/main` | `7ce195dde4b2b188dc79717dd6c0999dd92dcdb3` (not moved by this task; `c57146d` + one CI-only commit) |
| `origin/deploy/ADG-HOSTED-NEXT16-01` | **`c57146d636ad04f186b836ec12e8f8722c23e27f`** |
| Render `adg-demo-web` | tracks `deploy/ADG-HOSTED-NEXT16-01`, auto-deploy off, live `dep-dbg12pgm7kps73b2oop0` @ `c57146d` |
| Worker `adg-demo-staging` | `adfb5c6d-b496-4e16-a2a8-1f17174c1471` (tag `c57146d636ad`), workers.dev enabled |
| `adg_demo` | reset baseline, `0018_risk_evaluation_basis`, verify ok, fingerprints match |
| Primary checkout `~/Documents/code/adg` | `main` @ `7ce195d`, clean worktree and index |
| This report | branch `docs/ADG-DEMO-RESET-01`, off `main` (a commit to the deploy branch would start another staging cycle) |

## Evidence

`ADG-DEMO-RESET-01-evidence/`: `db/` (identity, pre/post status, verify, fingerprint, row counts,
basis token, alerts provenance, reset output), `hosted/` (risks summaries, rendered `/risks` check,
smoke and browser logs, pre-deploy DEMO-03), `demo03-post-deploy/` (results and screenshots),
`hydration/` (control and run summaries, the run's per-load records), `deploy/` (Render deploys
before and after, gate logs, route state, Worker deploy lines). No credentials, database host,
project ref, Cloudflare account id or Render workspace id appear in any of them. Every file was
scanned for those values before commit.
