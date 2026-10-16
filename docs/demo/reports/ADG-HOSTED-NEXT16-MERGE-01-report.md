# ADG-HOSTED-NEXT16-MERGE-01 — The hosted Next.js 16 line, fast-forwarded onto `main` and given a canonical deploy branch

## 1. Verdict

**`ADG_HOSTED_NEXT16_MERGED_WITH_LIMITATIONS`**

`main` moved from `d9d39cc` to **`02031b526b802cd05380ac249b234fd2db556e8c`**, the head of
`integration/ADG-HOSTED-NEXT16-01`, by `git merge --ff-only`. There was no merge commit, no
rebase and no force-push. The new canonical deploy branch **`deploy/ADG-HOSTED-NEXT16-01`** points
at the same commit. Render `adg-demo-web` now tracks it. Only the `branch` field changed, and
Render did not redeploy. The live deployment (Render `dep-db8qhucs728c73anb3n0` @ `53b81a6`,
Worker `723ffc1c`) answered healthy after the repoint, with smoke **23/23**. CI passed on `main` and on
the deploy branch at this SHA (§14).

The limitations are documented, and none is a defect of the merge (§14). The demo database
reset is deferred to the owner (`DEMO_RESET_DEFERRED_OWNER_ACTION`). The staging workflow and the
guide on `main` still name `deploy/ADG-FREE-STAGING-01`. The reduced browser/#418 confirmation
was not required, because nothing redeployed.

This report is on the docs-only branch `docs/ADG-HOSTED-NEXT16-MERGE-01`, so that `main` stays on
the hosted-validated SHA.

---

## 2. Starting `main`

| | |
| --- | --- |
| `origin/main` at start | **`d9d39cc5cc61eb8c637c42595316735fbab7b187`**, from `git fetch`. Equal to the expected SHA |
| Working tree | `main` is checked out in the worktree `../adg-NEXT16-MAC-REVIEW-01`. `git status --short` and `--porcelain=v1` printed nothing; `git diff --quiet` and `git diff --cached --quiet` exited 0 |
| Primary checkout | `~/Documents/code/adg` stays on the frozen `deploy/ADG-FREE-EDGE-SSR-03` with its two untracked Finder files. Not touched, not used |

## 3. Hosted integration branch

| | |
| --- | --- |
| Branch | `integration/ADG-HOSTED-NEXT16-01` |
| Head | **`02031b526b802cd05380ac249b234fd2db556e8c`**. Equal to the expected SHA |
| Deployed application commit | `53b81a670f03ff69f6a7545dab103c844265d624` (the head's parent; the head adds only `docs/demo/reports/`) |
| Ahead / behind `main` | **4 / 0**; merge base = `d9d39cc` |
| Commits | `5ce87ba` (demo notice header without its em dash), `25c4d7e` (deploy stack port), `53b81a6` (guide and handoffs), `02031b5` (ADG-HOSTED-NEXT16-01 report and evidence) |

Contents were re-checked against `docs/demo/reports/ADG-HOSTED-NEXT16-01-report.md`:

| Recorded decision | Re-check |
| --- | --- |
| Next.js 16.4.1, React 19.3.0, webpack | `frontend/package.json`: `next` / `eslint-config-next` `^16.4.1`, `react` / `react-dom` `^19.3.0`, `build` = `next build --webpack` |
| Deployment infrastructure ported selectively | the 68 changed files are the deploy stack, its tests, the backend header fix, the origin gate of the rollback service, docs and evidence. None is under `frontend/app/`, `frontend/components/`, `backend/app/api/`, `backend/app/contracts/` or `database/` |
| Backend hosted-smoke fix | `5ce87ba` (`backend/app/main.py`, `tests/demo_mode/test_boundary.py`) |
| No UI-UPDATE-01, COMPACT-IDENTITY-01, GRAPH or old Next 15 / 16.4.0 application tree | none of their commits is in `d9d39cc..02031b5`; no application path changed |
| One Alembic head `0018` | parsed all 20 revisions in `database/migrations/versions`: one head, `0018_risk_evaluation_basis` (down `0017_deployment_marker`). No other `0018*` revision, so no GRAPH `0018` |
| Contract / OpenAPI unchanged | `git diff --quiet d9d39cc 02031b5 -- backend/app/contracts database frontend/app frontend/components` exits 0 |

## 4. Hosted evidence

The ADG-HOSTED-NEXT16-01 report was taken as the authoritative evidence for this exact line
(application tree `53b81a6`). The source did not change, so the full hosted gate was not rerun.

| Check | Result there |
| --- | --- |
| Hosted smoke | 23/23 |
| Browser gate | 22/22 |
| DEMO-01 / DEMO-02 | 15/15 / 29/29 |
| Public walkthrough | 39/39 |
| DEMO-03 | 39/39 in its best run. Two runs reached 37/39, because of the fixed 4-second comparison window on Render Free (0.1 CPU) |
| React #418 | **0** in 2,280 hosted gate records (1,920 full loads, 360 client navigations) |
| Positive control | #418 detected on 62/62 loads |
| Hydration errors / application console errors | 0 / 0 |

## 5. Fast-forward proof

```text
$ git rev-parse HEAD origin/main          # in the main worktree, before
d9d39cc5cc61eb8c637c42595316735fbab7b187  (both)
$ git merge --ff-only 02031b526b802cd05380ac249b234fd2db556e8c
Updating d9d39cc..02031b5
Fast-forward
 68 files changed, 8416 insertions(+), 16 deletions(-)
$ git rev-parse HEAD
02031b526b802cd05380ac249b234fd2db556e8c
$ git fetch origin; git rev-parse origin/main   # re-check before pushing
d9d39cc5cc61eb8c637c42595316735fbab7b187         # not moved
$ git push origin main
   d9d39cc..02031b5  main -> main
```

`HEAD` already equalled `origin/main` and the tree was clean, so the spec's
`git reset --hard origin/main` would have been a no-op and was not needed. The tree was clean again
straight after the merge.

## 6. Ending `main`

**`02031b526b802cd05380ac249b234fd2db556e8c`**: local `main` = `origin/main` = `git ls-remote
origin refs/heads/main`.

## 7. Canonical deploy branch

**`deploy/ADG-HOSTED-NEXT16-01`** was created from the merged `main` at
**`02031b526b802cd05380ac249b234fd2db556e8c`** and pushed normally (new branch). It needed no new
commit. `deploy/ADG-HOSTED-NEXT16-01` = `main` = `integration/ADG-HOSTED-NEXT16-01`.

## 8. Historical deploy branches

These were not moved and not rewritten. `git ls-remote` gave the same values after every push:

| Branch | SHA |
| --- | --- |
| `deploy/ADG-FREE-EDGE-SSR-03` | `57bbbaadce50ba18c854f489a97cbcdef7bb3a17` |
| `deploy/ADG-FREE-STAGING-01` | `3e26e4ad3fd351b5a8e956ae392ce8088ac485d1` |

## 9. Render branch tracking

Service **`adg-demo-web`** (`srv-db6jbmlg1s2s739i4fsg`):
`PATCH /v1/services/srv-db6jbmlg1s2s739i4fsg {"branch": "deploy/ADG-HOSTED-NEXT16-01"}` at
2026-10-16T19:21:48Z.

The full service object was compared field by field before and after the patch. Only two fields
changed:

| Field | Before | After |
| --- | --- | --- |
| `branch` | `integration/ADG-HOSTED-NEXT16-01` | **`deploy/ADG-HOSTED-NEXT16-01`** |
| `updatedAt` | 2026-10-16T04:35:58Z | 2026-10-16T19:21:48Z |

These stayed the same: name and ID, repository, Docker runtime, `./docker/Dockerfile.render`,
context `.`, no start command, health `/health/live`, plan `free`, region `oregon`, auto-deploy
`no` (trigger `off`), not suspended. The 12 environment variable **names** are identical before
and after (values not read). Secrets, the database and the Worker were not touched.

## 10. Render redeploy

**No redeploy.** The deploy list 20 s after the patch, and again later, still shows
**`dep-db8qhucs728c73anb3n0` live** @ `53b81a670f03` (created 04:32:57Z). No new deploy appeared.
Auto-deploy is off, so a branch change alone starts nothing.

## 11. Post-repoint hosted checks

Render did not redeploy, so the spec asks only that the current deployment be confirmed healthy.
Checked through the Worker <https://adg-demo-staging.d-walkes.workers.dev> (UTC, 2026-10-16):

| Check | Result |
| --- | --- |
| `/health/live` (19:22–19:24) | **200** `{"status":"alive"}`, after a **92.4 s** cold start (the service had been idle) |
| `/version` | 200, `runtime_mode: demo`, header `X-ADG-Runtime-Mode: demo` |
| `/runtime` | 200, demo / synthetic / `null-meridian-v1` |
| Homepage | carries build ID `53b81a670f03ff69f6a7545dab103c844265d624` |
| Render direct without the origin key | **403** |
| `frontend/cloudflare/checks/smoke.mjs` (19:24:53Z, database host and project ref as canaries) | **23/23 passed, 0 failed**. Output kept as `ADG-HOSTED-NEXT16-MERGE-01-evidence/smoke-after-repoint.json`, and it was scanned for the origin key, session secret, database password, Render key, database host and project ref: none appears |

The reduced browser gate, DEMO-01/02/03 and #418 rounds are for the redeploy case only, and
were not run. React #418 therefore stands on ADG-HOSTED-NEXT16-01's **0 in 2,280 records** for
the same live deployment. No cold-start 503 was seen in this task.

## 12. Worker

The Worker was not redeployed. `wrangler deployments list --name adg-demo-staging`: **100% on
`723ffc1c-6c45-4bb8-ab0b-0e9260ac8bf5`** (tag `53b81a670f03`, created 06:41:19Z, the
ADG-HOSTED-NEXT16-01 lifecycle redeploy). `wrangler versions view` shows the var
**`ADG_RENDER_ORIGIN` = `https://adg-demo-web.onrender.com`** and the secret `ADG_ORIGIN_KEY`.
So the Worker is still `adg-demo-staging` in front of the intended demo origin. Repository policy
ties a Worker deploy to a new build, not to a branch name, and the build did not change.

## 13. Database reset

**`DEMO_RESET_DEFERRED_OWNER_ACTION`**

`python -m app.demo_mode reset` was **not** run, and no database command ran in this task.
The reasons:

* the reset is not needed for code correctness or for the merge;
* it changes the hosted demo state;
* the data fingerprint stayed stable (`228b9dce…f9efd0`) through every ADG-HOSTED-NEXT16-01
  lifecycle event;
* the remaining stale-snapshot reason on `/risks` ("The last evaluation predates snapshot
  recording") affects presentation quality and does not block the merge.

`adg_demo` stays at Alembic `0018_risk_evaluation_basis`, migrated and not reset. A separate,
owner-approved staging-maintenance task will run the reset documented in ADG-HOSTED-NEXT16-01
§17.

## 14. CI

CI was not a gate here, and it ran anyway (the repository is public). Every job passed: Backend
(lint, types, tests), Frontend (lint, types, tests, build), Collector (Pester), and Docker
Compose configuration.

| Run | Branch | SHA | Result |
| --- | --- | --- | --- |
| [95](https://github.com/40ozs/adg/actions/runs/38954249997) | `main` | `02031b5` | **success** |
| [96](https://github.com/40ozs/adg/actions/runs/38954299497) | `deploy/ADG-HOSTED-NEXT16-01` | `02031b5` | **success** |
| [38869440137](https://github.com/40ozs/adg/actions/runs/38869440137) | `integration/ADG-HOSTED-NEXT16-01` | `02031b5` | success (ADG-HOSTED-NEXT16-01) |

The `Free staging (ADG demo)` workflow did not run. Its push trigger is still
`deploy/ADG-FREE-STAGING-01` (limitation 2), and its environment holds no secrets in any case.

## 15. Limitations

1. **Demo database migrated, not reset** (`DEMO_RESET_DEFERRED_OWNER_ACTION`, §13). Hosted
   `/risks` keeps the extra "predates snapshot recording" reason until the owner runs the reset.
2. **Stale deploy-branch references on `main`.** `.github/workflows/cloudflare-staging.yml`
   (push trigger, line 38, and its header comment) and `docs/deployment/cloudflare-demo.md`
   (lines 127 and 193) still name `deploy/ADG-FREE-STAGING-01`. A push to
   `deploy/ADG-HOSTED-NEXT16-01` therefore does not start the staging workflow. Changing that
   would move `main` off the validated SHA, so it was left for a follow-up.
3. **Reduced hosted confirmation not run**, because nothing redeployed (§11). Health was
   confirmed with the endpoint checks and smoke 23/23 only. The browser gate and #418 rest on
   ADG-HOSTED-NEXT16-01 for the same deployment.
4. Carried forward from ADG-HOSTED-NEXT16-01: DEMO-03's 4-second window on 0.1 CPU; hosted
   walkthroughs sign in as `planner`; cold start of about 85–92 s, during which requests can get
   Render's 503; one machine and Chromium only.
5. **Rollback, unchanged in substance:** Worker `wrangler rollback 66663d4a-c925-44dc-bdca-011e1d8f9c65`,
   then Render `PATCH {"branch": "deploy/ADG-FREE-STAGING-01"}` and deploy `33cca01`, both
   together (ADG-HOSTED-NEXT16-01 §17). To undo only this task's change: `PATCH {"branch":
   "integration/ADG-HOSTED-NEXT16-01"}`. The integration branch was kept.

## 16. Clean tree

In the `main` worktree after the push, `git status --short` and `--porcelain=v1` printed
nothing, and `git diff --quiet` and `git diff --cached --quiet` exited 0. The same held in this
docs worktree after its commit. Refs at the end:

| Ref | SHA |
| --- | --- |
| `main` = `origin/main` | `02031b526b802cd05380ac249b234fd2db556e8c` |
| `deploy/ADG-HOSTED-NEXT16-01` = `origin/…` | `02031b526b802cd05380ac249b234fd2db556e8c` |
| `integration/ADG-HOSTED-NEXT16-01` = `origin/…` | `02031b526b802cd05380ac249b234fd2db556e8c` (kept, not deleted) |
| `docs/ADG-HOSTED-NEXT16-MERGE-01` | this report's commit, `docs/` only on top of `02031b5` |

## 17. Recommended next action

1. **Owner-approved staging maintenance:** run the documented demo reset against `adg_demo` from
   `main`'s backend. Expect `verify` = `228b9dce…f9efd0`, and the stale-snapshot reason should
   disappear from `/risks`.
2. **One small docs/workflow commit:** retarget `cloudflare-staging.yml`'s push trigger and the
   guide to `deploy/ADG-HOSTED-NEXT16-01`. Merge it, together with this report's branch, to
   `main`. Then fast-forward the deploy branch to that commit when the next deploy is wanted.
3. After that, delete `integration/ADG-HOSTED-NEXT16-01`. It is no longer needed for rollback,
   because the deploy branch holds the same commit.
