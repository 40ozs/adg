# ADG-NEXT16-REACT418-01: Next.js 16.4.0 on staging, React #418 remediation

Staging-only framework upgrade to remove the intermittent React hydration error #418 found in
ADG-UI-EDGE-01 (`docs/handoffs/adg-ui-edge-01.md` §5). It is not the final security baseline:
the October 14, 2026 Next.js security release is a follow-up patch before this goes to `main`.

**Verdict: `UPSTREAM_REACT_418_RESOLVED`.** Zero #418 in 15 browser-gate runs and 168
instrumented reloads that reproduce it on Next 15.5.27. Not merged to `main`.

## 1. Change

Branch `fix/ADG-NEXT16-REACT418-01` from `deploy/ADG-FREE-STAGING-01@6b69fe3`. Deployed commit
`66ca20151e9bacfb96ad3cbff689d2b5cdcfa417`. Only what the upgrade requires:

| File | Change |
| --- | --- |
| `frontend/package.json`, lockfile | `next` and `eslint-config-next` 15.5.27 → 16.4.0; `react`, `react-dom`, `@types/react(-dom)` → 19.3 |
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
| CI on `66ca201` (backend lint/types/tests, frontend lint/types/tests/build, collector, compose) | all green |
| Local: `lint`, `typecheck`, `cf:typecheck`, `vitest` | clean; 1,295 tests |
| Local: `next build --webpack`, standalone (`ADG_NEXT_OUTPUT=standalone`) | ok; `server.js` serves `/` |
| `smoke.mjs` (database host and project ref as canaries) | **25/25** |
| direct to Render (smoke) | 12 requests without the key or with a guessed one → **403**; only `/health/live` open |
| public accounts (smoke) | `viewer`, `planner` only; admin, auditor, reviewer, governance, approver → 404; 8 privileged writes → 403; ingestion → 403 `demo_only` |
| `browser.mjs` ×5 | **22/22** each, 0 page errors |
| `navigation.mjs` ×5 | **18/18** each, 0 page errors |
| instrumented gate ×5 (hook applied on every load, 24 per run) | **22/22** each, 0 #418 |
| reload loop, 168 signed-in full loads | **0 #418**, 0 page errors |
| `verify` (read-only, API image of this commit) | ok, 23 probes, `228b9dce…f9efd0` (unchanged) |
| Null Meridian logical fingerprint | `db736602…85c28a0`, **match** (unchanged) |

The only console error in a browser run is the deliberate `POST /api/adg/api/v1/scan-runs` → 405.

**#418 before and after.** Same harnesses against the same staging:

| | Next 15.5.27 (`6b69fe3`) | Next 16.4.0 (`66ca201`) |
| --- | --- | --- |
| `browser.mjs` runs with #418 | 2 of 3 | **0 of 5** |
| instrumented runs with #418 | 3 of 7 | **0 of 5** |
| reload loop (7 pages) | 3 in 56 loads (`/`, `/identities`, `/demo`) | **0 in 168 loads** |

At the 15.5.27 rate (about 1 in 19 loads), 168 clean loads would happen by chance with
probability about 0.0001.

## 4. Worker CPU

Cloudflare analytics, version `19b5a60a`, 00:17:56Z–00:33:35Z:

| Status | Requests | p50 | p90 | p99 | p99.9 | max |
| --- | --- | --- | --- | --- | --- | --- |
| `success` | 5,878 | 0.78 ms | 1.37 ms | 2.20 ms | 17.76 ms | 26.77 ms |
| `clientDisconnected` | 890 | 0.57 ms | 0.98 ms | 1.69 ms | 21.45 ms | 21.45 ms |

**`exceededCpu`: 0. Errors: 0.** Proxy-level, as on 15.5.27 (p50 0.89 ms, max 28.3 ms). The
`clientDisconnected` share is the reload loop leaving pages while prefetches were in flight.

## 5. Open

* **October 14 security release:** upgrade to it as a follow-up patch, then rerun §3, before
  this is considered for `main`.
* **Deployment branch:** Render tracks `fix/ADG-NEXT16-REACT418-01` while
  `deploy/ADG-FREE-STAGING-01`, the workflow and the guide still name the accepted `6b69fe3`.
  Fast-forwarding the deploy branch (and pointing Render back at it) is the owner's call.
* **Turbopack** is not used. Moving to it means re-checking the chunk layout `cf:assets` and the
  edge/origin split depend on.
* The harnesses (instrumented gate, reload loop) are not committed; their method is in
  ADG-UI-EDGE-01 §5.
