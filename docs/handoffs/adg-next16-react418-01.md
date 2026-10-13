# ADG-NEXT16-REACT418-01: Next.js 16.4.0 on staging, React #418 remediation

Staging-only framework upgrade to remove the intermittent React hydration error #418 found in
ADG-UI-EDGE-01 (`docs/handoffs/adg-ui-edge-01.md` §5). It is not the final security baseline:
the October 14, 2026 Next.js security release is a follow-up patch before this goes to `main`.

**Verdict: `UPSTREAM_REACT_418_RESOLVED` → `READY_FOR_ADG_NEXT16_UI_INTEGRATION`.** Zero #418
on staging in 5 browser-gate runs, 10 navigation runs, 10 instrumented runs and 224 reloads,
using the same harnesses that reproduce #418 on Next 15.5.27. The local runs were clean too.
Not merged to `main`.

## 1. Change

Branch `fix/ADG-NEXT16-REACT418-01` from `deploy/ADG-FREE-STAGING-01@6b69fe3`. Deployed commit
`66ca20151e9bacfb96ad3cbff689d2b5cdcfa417`. Only what the upgrade requires:

| File | Change |
| --- | --- |
| `frontend/package.json`, lockfile | `next` and `eslint-config-next` 15.5.27 → 16.4.0; ranges for `react`, `react-dom`, `@types/react(-dom)` `^19.1.0` → `^19.3.0` (the lockfile already resolved all four to 19.3.0, so only the floor moves) |
| `frontend/package.json` | `build`: `next build --webpack`. Next 16 defaults to Turbopack; webpack keeps the chunk layout the Worker's edge assets rely on. No Turbopack migration. |
| `frontend/eslint.config.mjs` | `eslint-config-next` 16 ships flat configs, imported directly; `@eslint/eslintrc` (`FlatCompat`) removed |
| `frontend/tsconfig.json` | `jsx: react-jsx` and `.next/dev/types/**/*.ts`, as Next 16 requires (it rewrites the file otherwise) |

No application code, no Suspense boundary, no backend change. Architecture unchanged: thin
Worker (Workers Free, 4.00 KiB) in front of one Render service running Next.js and FastAPI
behind the origin gate.

**Why it fixes #418.** Next 16.4.0 bundles react-dom `19.3.0-canary-278794d7-20261002`. Its
`replayBeginWork` for a host component that is the current hydration parent now steps back
(`popToNextHostParent`) and resets `nextHydratableInstance` to the element itself before the
replayed `beginWork`. Next 15.5.27's `19.2.0-canary-0bdb9206-20250818` did not, which is the
cause established in ADG-UI-EDGE-01.

The App Router renders with the react-dom that Next bundles, not with npm's copy:

| | before (`6b69fe3`) | after (`66ca201`) |
| --- | --- | --- |
| `next` | 15.5.27 | 16.4.0 |
| react / react-dom the App Router runs (bundled in `next`) | 19.2.0-canary-0bdb9206-20250818 | 19.3.0-canary-278794d7-20261002 |
| npm `react`, `react-dom`, `@types/react(-dom)` | 19.3.0 | 19.3.0 |
| `eslint-config-next` | 15.5.27 | 16.4.0 |

Node requirement: `next` 16.4.0 needs Node `>=20.9.0`. The Render image runs Node 22, so
nothing changes there.

## 2. What runs where

| | |
| --- | --- |
| Render `adg-demo-web` | deploy `dep-db6nep67bikc73almks0`, live 00:14:58Z on 2026-10-13, commit `66ca201`; the service tracks `fix/ADG-NEXT16-REACT418-01` for this test (auto-deploy off) |
| Worker `adg-demo-staging` | version `19b5a60a-fe07-4599-aba8-a7e8466fb946`, tag `66ca20151e9b`, var `ADG_RENDER_ORIGIN`, secret `ADG_ORIGIN_KEY` kept |
| Rollback | Render: set the branch to `deploy/ADG-FREE-STAGING-01` and deploy `6b69fe3`; Worker: `wrangler rollback 5c5e7a26-d9d0-46c2-9c33-d6f907e657c8` |
| `deploy/ADG-FREE-STAGING-01` | unchanged at `6b69fe3` (the accepted state) |
| `deploy/ADG-FREE-EDGE-SSR-03` | unchanged at `57bbbaa` |
| Database | `adg_demo`, not reset |

Edge assets: `docker build --platform linux/amd64 -f docker/Dockerfile.render --target web
--build-arg RENDER_GIT_COMMIT=66ca201…` after Render was live. `BUILD_ID` is the commit; every
file Render's homepage HTML references (8) is in the image, and the 19 distinct
`/_next/static` files the browser requested across the signed-in reload loop are all in the
uploaded `.edge-assets`.

## 3. Checks

| Check | Result |
| --- | --- |
| CI on `66ca201` and `f5005b1` (backend lint/types/tests, frontend lint/types/tests/build, collector, compose) | all green |
| Local: backend `ruff check`, `ruff format --check`, `mypy app` (no backend change in this branch, so the suite runs in CI only) | clean; 400 files formatted, 189 type-checked |
| Local: `lint`, `typecheck`, `cf:typecheck`, `vitest` | clean; 1,295 tests |
| Local: `next build --webpack`, standalone (`ADG_NEXT_OUTPUT=standalone`) | ok; `server.js` serves `/` |
| Local: `wrangler deploy --dry-run` (4.149.0) | ok; 4.00 KiB, 126 asset files, no bindings |
| Local: `actionlint` 1.7.7 | clean |
| `smoke.mjs` (database host and project ref as canaries) ×2 | **25/25** each |
| direct to Render (smoke) | 12 requests without the key or with a guessed one → **403**; only `/health/live` open |
| public accounts (smoke) | `viewer`, `planner` only; admin, auditor, reviewer, governance, approver → 404; 8 privileged writes → 403; ingestion → 403 `demo_only` |
| `browser.mjs` ×5 | **22/22** each, 0 page errors |
| `navigation.mjs` ×10 (after every browser and instrumented run 6–10) | **18/18** each, 0 page errors |
| instrumented gate ×10 (hook applied on every load, 24 per run) | **22/22** each, 0 #418 |
| reload loops, 168 + 56 signed-in full loads | **0 #418**, 0 page errors; none of the 19 requested static files is missing from `.edge-assets` |
| local: instrumented gate ×3 + reload loop (56 loads) against the local gate (API + standalone Next) | **22/22** each, **0 #418** |
| `verify` (read-only) ×2 | ok, 23 probes, `228b9dce…f9efd0` (unchanged) |
| Null Meridian logical fingerprint ×2 | `db736602…85c28a0`, **match** (unchanged) |

Local runs: `frontend/render/gate.ts` serves the standalone build of this commit. Its API uses
the `adg_demo` database read-only, and Playwright sends a throwaway origin key. Locally Next 16
is clean, but local runs are not evidence on their own: Next 15 was not run locally for a
baseline, and loopback timing differs from the hosted path.

The only console error in a browser run is the deliberate `POST /api/adg/api/v1/scan-runs` → 405.

**#418 before and after.** Same harnesses against the same staging:

| | Next 15.5.27 (`6b69fe3`) | Next 16.4.0 (`66ca201`) |
| --- | --- | --- |
| `browser.mjs` runs with #418 | 2 of 3 | **0 of 5** |
| instrumented runs with #418 | 3 of 7 | **0 of 10** |
| reload loop (7 pages) | 3 in 56 loads (`/`, `/identities`, `/demo`) | **0 in 224 loads** |

At the 15.5.27 rate (about 1 in 19 loads), 224 clean loads would happen by chance with
probability about 0.000006.

The instrumented runs load `/`, `/status`, `/identities`, `/demo`, the scenario A, B and H
directory pages, a principal page, and the access explanations for scenarios F and G. The
directory ACL, principal, explanation, risk and status pages use `SortableTable`. The reload
loop adds `/identities/users` and a direct `/access/explain`. The root layout's `AppShell` wraps
every page, and every page streams server content.

## 4. Worker CPU

Cloudflare analytics, version `19b5a60a`, 00:17:56Z–00:33:35Z:

| Status | Requests | p50 | p90 | p99 | p99.9 | max |
| --- | --- | --- | --- | --- | --- | --- |
| `success` | 5,878 | 0.78 ms | 1.37 ms | 2.20 ms | 17.76 ms | 26.77 ms |
| `clientDisconnected` | 890 | 0.57 ms | 0.98 ms | 1.69 ms | 21.45 ms | 21.45 ms |

**`exceededCpu`: 0. Errors: 0.** Proxy-level, as on 15.5.27 (p50 0.89 ms, max 28.3 ms). The
`clientDisconnected` share is the reload loop leaving pages while prefetches were in flight.

Cumulative from 00:17:56Z to 01:56Z, which includes the completion runs:

| Status | Requests | p50 | p90 | p99 | p99.9 | max |
| --- | --- | --- | --- | --- | --- | --- |
| `success` | 9,427 | 0.78 ms | 1.35 ms | 2.05 ms | 17.26 ms | 28.76 ms |
| `clientDisconnected` | 1,328 | 0.57 ms | 1.00 ms | 1.64 ms | 20.94 ms | 21.45 ms |

`exceededCpu`: 0. Errors: 0.

## 5. Render behavior

| | Next 15.5.27 | Next 16.4.0 |
| --- | --- | --- |
| gate listening after start (`adg.gate.listening`) | 56–70 s (3 starts) | 64–65 s (2 starts) |
| first web answer (`adg.gate.first_answer`) | 75–84 s | 81–92 s |
| memory under test load (median / max) | 319 / 326 MiB | 295 / 306 MiB |

Startup did not change materially, so cold start was not measured again. Warm latency through
the Worker, measured from this Mac with 10 requests each:

* Anonymous pages (`/`, `/status`, `/identities`, `/demo`): median 554–731 ms.
* API (`/version`, `/health/ready`, and `/api/v1/demo` and `/api/v1/risks/summary` as planner): median 402–543 ms.

## 6. Open

* **October 14 security release:** upgrade to it as a follow-up patch, then rerun §3, before
  this is considered for `main`.
* **Deployment branch:** Render tracks `fix/ADG-NEXT16-REACT418-01` while
  `deploy/ADG-FREE-STAGING-01`, the workflow and the guide still name the accepted `6b69fe3`.
  Fast-forwarding the deploy branch (and pointing Render back at it) is the owner's call.
  *Done 2026-10-13 in ADG-NEXT16-UI-INTEGRATION-02 (`adg-next16-ui-integration-02.md` §8),
  together with the compact-identity work: Render tracks `deploy/ADG-FREE-STAGING-01` again.*
* **Turbopack** is not used. Moving to it means re-checking the chunk layout `cf:assets` and the
  edge/origin split depend on.
* The harnesses (instrumented gate, reload loop) are not committed; their method is in
  ADG-UI-EDGE-01 §5.
