# ADG-UI-EDGE-01: UI update merged into the thin-Worker staging, and redeployed

Integration report for ADG-UI-UPDATE-01 on the ADG-FREE-EDGE-SSR-03 architecture: the merge,
the staging redeploy, the hosted checks and the React #418 investigation. Deployed 2026-10-12.

**Verdict: deployed, checks green except the pre-existing intermittent React #418, which is now
attributed (§5). Not merged to `main`.**

## 1. What runs where

| | |
| --- | --- |
| Validated base | `57bbbaa` (`deploy/ADG-FREE-EDGE-SSR-03`, unchanged) |
| UI work | `a5cdf88` (`fix/ADG-UI-UPDATE-01`), merged at `afa7804` |
| Deployed commit | `6b69fe3fd6d513016426ee651740e1f44f863f4e` (`integration/ADG-UI-EDGE-01` = `deploy/ADG-FREE-STAGING-01`, fast-forward from `643da21`) |
| Render | `adg-demo-web` deploy `dep-db6mkcmi0phs73d50720`, live 23:18:32Z at that commit; the service's branch is now `deploy/ADG-FREE-STAGING-01` (auto-deploy stays off) |
| Worker | `adg-demo-staging` version `5c5e7a26-d9d0-46c2-9c33-d6f907e657c8`, tag `6b69fe3fd6d5`, 4.00 KiB, var `ADG_RENDER_ORIGIN`, secret `ADG_ORIGIN_KEY` (kept from the previous version) |
| Rollback | Worker `f5d9868c-6767-4e28-9faa-92000bc40899` (thin proxy at `df6556f`); Render: redeploy `df6556f`. `adg-demo-api` stays suspended |
| Database | `adg_demo`, not reset |

Order, as specified: Render first; healthy (keyed `/health/ready`, `/version`, `/` → 200); edge
assets from `docker build --platform linux/amd64 -f docker/Dockerfile.render --target web
--build-arg RENDER_GIT_COMMIT=<that commit>`; then the Worker. The image's `BUILD_ID` is the
commit, and every chunk Render's homepage HTML references (7) is in the uploaded assets.

The deploy ran from the operator's workstation (`docs/deployment/cloudflare-demo.md` §6). The
`cloudflare-staging` GitHub environment still holds no secrets, so the workflow run the push
started (`38321204225`) failed at preflight and deployed nothing, as designed.

## 2. Checks

| Check | Result |
| --- | --- |
| `smoke.mjs` (database host and project ref as canaries) | **25/25** |
| `browser.mjs`, run 1 | 21/22: one React #418 |
| `browser.mjs`, run 2 | 21/22: two React #418 |
| `browser.mjs`, run 3 | **22/22** |
| `navigation.mjs` after each browser run | **18/18** ×3, 0 page errors |
| direct to Render (smoke) | 12 requests without the key or with a guessed one → **403**; only `/health/live` open |
| public accounts (smoke) | `viewer`, `planner` offered; admin, auditor, reviewer, governance, approver → 404; 8 privileged writes as planner → 403; ingestion → 403 `demo_only` (anonymous and planner); no remediation execution route |
| `verify` (read-only, API image of this commit) | ok, 23 probes, integrated fingerprint `228b9dce…f9efd0` (unchanged) |
| Null Meridian logical fingerprint | `db736602…85c28a0`, **match** (unchanged) |

The only console error in every browser run is the deliberate `POST /api/adg/api/v1/scan-runs`
→ 405.

## 3. Worker CPU

Cloudflare analytics (`workersInvocationsAdaptive`), version `5c5e7a26`, 23:21:50Z to 23:38Z,
covering smoke, 3 browser + 3 navigation runs and 7 instrumented browser runs:

| Status | Requests | p50 | p90 | p99 | p99.9 | max |
| --- | --- | --- | --- | --- | --- | --- |
| `success` | 2,787 | 0.89 ms | 1.50 ms | 2.00 ms | 21.57 ms | 28.34 ms |
| `clientDisconnected` | 147 | 0.76 ms | 1.29 ms | 1.62 ms | 1.63 ms | 1.63 ms |

**`exceededCpu`: 0. Errors: 0.** Proxy-level, as at EDGE-SSR-03 (p50 1.37 ms, max 23 ms); the
tail above 10 ms is a fraction of a percent, during browser-gate bursts.

## 4. Documentation and workflow

The workflow's push trigger and the deployment guide name `deploy/ADG-FREE-STAGING-01` for both
the push and the Render service (changed in `6b69fe3`); nothing outside `docs/handoffs` still
names `deploy/ADG-FREE-EDGE-SSR-03` as the deployment branch. Docker build images and build
cache were pruned after validation.

## 5. React #418: where and why

**Still reproducible** (2 of 3 plain browser-gate runs; 3 of 7 instrumented runs). It is not
specific to a route and is not caused by this UI work.

**Method.** A copy of `browser.mjs` (not committed) with (1) a `MutationObserver` installed
before any page script, and (2) Playwright serving the deployed react-dom chunk
(`4bd1b696-…js`) with one line added to React's mismatch thrower (`rD`) that records the fiber
being hydrated, the hydration parent and the server node React found. Only that headless browser
ran the modified chunk.

**Observations.**

* Routes: `/` and `/status` (normal navigation, status check), `/identities`, and
  `/access/explain` (scenario G); `/demo` in earlier work. Every occurrence was on a full page
  load with `document.readyState === "loading"`, 2–5 ms after a late RSC row (the page segment)
  arrived.
* `/identities`: fiber **`div.shell`** (`components/AppShell.tsx`), hydration parent `div.shell`,
  server node `<a class="skip-link">`, which is `div.shell`'s own first child. Outside any
  Suspense boundary, so React regenerated the whole document (`<title>`, the metadata `div`,
  `div.shell`).
* `/access/explain`: fiber **`td`** (cell 4 of a membership row in `SortableTable`), hydration
  parent that `td`, server node `<a>Simulate across the estate</a>`, which is that cell's own
  first child.
* The server HTML and the client's regenerated DOM agree in content.

**Cause.** In the React that Next.js 15.5.27 bundles (`19.2.0-canary-0bdb9206-20250818`), a
host component claims its server node in `beginWork` during hydration and moves the cursor to
the node's first child. When the element's children then suspend on an RSC row that has not
streamed in yet and React *replays* the unit of work (`replayBeginWork` → `unwindInterruptedWork`,
which pops host context but not hydration state), the second `beginWork` tries to claim the same
element from the cursor at its first child, fails, and throws #418. Both captures match this
exactly: the parent is the element itself, and the "server node" is its first child. It is a
timing race between the JavaScript (fast, from the edge) and the streamed HTML and RSC (slower,
Render via the Worker), which is why it is intermittent, needs no particular component, and
was also seen on local `next start`.

**Impact.** Recoverable: React re-renders on the client, and every content check on the
affected pages passes. Cost is one client re-render of the document on those loads.

**Not done.** No fix was attempted. The options are a Next.js release that bundles a React
without this replay behaviour (to be verified with the same instrumented gate) or wrapping the
streamed parts in Suspense boundaries so a recovery stays local. Neither is in this task's scope.
