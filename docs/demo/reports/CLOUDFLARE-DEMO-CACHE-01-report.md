# CLOUDFLARE-DEMO-CACHE-01 — Edge caching of public demo data and Supabase egress (ADG demo staging)

## 1. Verdict

**`BLOCKED`**: no ADG route can be long-cached at Cloudflare within the task's safety rules.

- Every Null Meridian data view (identities, resources, access, changes, risks, governance,
  simulations, remediation, collectors) requires a signed-in session, and its answer depends on
  the account's capabilities.
- The task forbids shared caching of authenticated answers, and forbids changing authorization
  to make caching possible.
- The public answers do not reach PostgreSQL, so caching them would not reduce Supabase egress.
  They are `/`, `/login`, the sign-in notice, `/version`, `/runtime`, `/auth/config` and
  `/health/live`.

What was delivered instead:
- the route classification;
- a documented caching policy in the deployment guide;
- two regression tests that keep the Worker from ever sharing a visitor's answer;
- the owner options in §12.

The same task ran in the Open Decks Atlas repository, which has public Supabase-backed routes;
its report is there.

---

## 2. Repository state

| | |
| --- | --- |
| Repository | `40ozs/adg` (public) |
| Base | `origin/main` `7ce195d` (one CI-runner commit over `c57146d`) |
| Branch | `feature/CLOUDFLARE-DEMO-CACHE-01` (did not exist before), worktree `~/Documents/code/adg-CLOUDFLARE-DEMO-CACHE-01` |
| Primary checkout | `~/Documents/code/adg`, still on the frozen `deploy/ADG-FREE-EDGE-SSR-03`; not touched |
| Deploy branch | `deploy/ADG-HOSTED-NEXT16-01` @ `02031b5`; not touched (advancing it triggers the staging workflow) |
| Pushed / merged / deployed | no / no / no. Render, the Worker and the `adg_demo` database were not contacted |

## 3. Existing architecture

- **Cloudflare:** Worker `adg-demo-staging` on `workers.dev`, a thin proxy
  (`frontend/cloudflare/worker.ts`, `routing.ts`). Built static files come from `.edge-assets`
  before the Worker runs. Every other request goes to one Render service with the origin key.
  Cookies, `Authorization` and the answer are relayed unchanged. The Worker uses no `caches.default`,
  no `cf` fetch options and no Workers Cache.
- **Supabase:** PostgreSQL only (database `adg_demo`, Supavisor session pooler). FastAPI reaches it
  through SQLAlchemy from inside the Render container. There is no PostgREST, no supabase-js and no
  `*.supabase.co` call anywhere in the frontend or backend.
- **Frontend data access:** Next.js renders on Render and calls FastAPI over loopback with the
  session's Bearer token (`frontend/lib/api/client.ts`, `cache: "no-store"`). The Worker sees only the
  resulting HTML/RSC, which includes the account's name, roles and navigation.
- **Direct browser-to-Supabase traffic:** none.

```text
Browser → Worker (static files: edge) → Render gate → Next.js ─loopback→ FastAPI → Supabase PostgreSQL
                                             └─ /api/v1, /auth, /health, /version, /runtime → FastAPI
```

## 4. Route classification

| Route / data source | Classification | Edge TTL | Browser TTL | Supabase contacted? | Reason |
| --- | --- | --- | --- | --- | --- |
| `/_next/static/…` (edge assets) | PUBLIC_IMMUTABLE (existing) | 1 year | 1 year, `immutable` | never | content-hashed build output (`cloudflare/assets.mjs` `_headers`) |
| `GET /version`, `/runtime`, `/auth/config`, `/health/live` | public, fixed per deploy | none | origin's | **never** | settings and the in-process dataset metadata only; caching saves no egress |
| `GET /health/ready`, `/status` | public, live | none | origin's | every request | live health checks; caching would defeat them |
| Anonymous `/`, `/login`, and every page's sign-in notice | public | none | origin's | **never** | `currentViewer` makes no API call without the `adg_session` cookie (`frontend/lib/auth/current.ts:43-44`). The same URLs serve per-account HTML once signed in |
| Signed-in pages (`/`, `/demo`, `/access/*`, `/changes/*`, `/collectors`, `/governance/*`, `/identities/*`, `/resources/*`, `/risks/*`, `/search`, `/settings`, `/simulations/*`) | AUTHENTICATED | none | origin's | every request (via FastAPI) | the account's capabilities and identity are in the HTML |
| `GET /api/v1/…` read endpoints (identities, resources, access, changes, search, risks, alerts, governance, simulations, remediation, collection, scan-runs, demo) and `GET /auth/me` | AUTHENTICATED | none | origin's (`private, no-cache` + ETag on `access/explain`, `access/paths`, `groups/{id}/resource-impact`) | every request | capability-gated; search, `risks/summary`, governance queue/campaigns/items vary by caller |
| `GET /api/adg/[...path]` | AUTHENTICATED | none | origin's | every request | Next.js proxy for a signed-in session |
| `POST /api/v1/demo/scans` (`collectors:ingest`), governance `manage` writes | ADMIN | never | never | every request | admin capabilities. The demo offers only `viewer` and `planner` |
| Every POST/PUT/PATCH/DELETE (simulations, remediation, governance decisions, watches, scan-run ingest, `/api/simulations*`, `/api/watches*`, server actions) | WRITE | never | never | every request | mutations; planner writes persist for every visitor |
| `POST /api/auth/dev-login`, `/api/auth/logout`, `/auth/dev/login`, OIDC routes | AUTH | never | never | dev login: no (token minting) | session establishment |

Direct-to-Supabase audit: every PostgreSQL connection is `WRITE_REQUIRES_DIRECT` /
`AUTH_REQUIRES_DIRECT`-equivalent server traffic from Render. There is no `DIRECT_TO_SUPABASE`
browser path, and nothing is `CACHED_THROUGH_CLOUDFLARE` except the static files.

## 5. Changes implemented

- `docs/deployment/cloudflare-demo.md` §2 "Caching": the caching policy, where Supabase egress
  comes from, and why Workers Cache must stay off.
- `frontend/tests/cloudflare/cache-policy.test.ts`:
  - `wrangler.jsonc` declares no `"cache"` key, at the top level or per entrypoint;
  - four requests for the same path from different sessions, plus an anonymous one, each reach
    the origin and each get their own answer.
- This report and `CLOUDFLARE-DEMO-CACHE-01-evidence/`.

No Worker, application, API or database code changed.

## 6. Cache behavior

- **Identity/version:** none; nothing is shared-cached. If an owner option in §12 is taken, the
  natural version is the dataset key plus its logical fingerprint (`ADG_DEMO_DATASET`
  `null-meridian-v1`, fingerprint in `cloudflare-demo.md`), because the estate changes only on a reset.
- **Edge TTL:** 1 year for `/_next/static/*` only (unchanged).
- **Browser TTL:** whatever the origin sends (unchanged). Next.js dynamic pages are `private` /
  `no-store`. The ETag'd access answers are `private, no-cache`.
- **Error TTLs:** errors are not cached anywhere by Cloudflare. The Worker's own 502/503 refusals are
  `no-store` (unchanged).
- **Bypass:** everything bypasses, by construction. The Worker has no cache code, and Workers Cache is
  off and guarded by a test.

## 7. Supabase egress impact

- **Served primarily from Cloudflare:** the built static files only, unchanged; they never involved
  Supabase.
- **Still reaching Supabase:**
  - every signed-in page render (Next.js → FastAPI → PostgreSQL);
  - signed-in browser calls to `/api/v1/*`;
  - `/health/ready` and `/status`;
  - the reset, `verify` and fingerprint runs (from this Mac or the workflow's bootstrap);
  - the hosted checks (`smoke.mjs`, `browser.mjs`).

  Render's own health check calls `/health/live`, which does not touch the database.
- **Direct to Supabase from browsers:** none.
- No numeric impact is claimed: nothing changed in what reaches PostgreSQL, and no usage was measured.

## 8. Security review

- Authenticated answers are not shared-cached: the Worker caches nothing, and the new test proves
  that two sessions asking for the same path each get their own answer.
- Admin answers are not shared-cached (same path through the Worker; admin capabilities are not
  offered in the demo).
- Writes are not cached.
- Secrets stay server-side: no secret, account id or Render id was added. The test uses a dummy
  key and an `example.workers.dev` host.
- No RLS weakening: ADG does not use Supabase RLS for the app (FastAPI enforces capabilities).
  Nothing about authentication or authorization changed.
- Hazard documented: switching on Cloudflare's Workers Cache (July 2026, every plan, works on
  `workers.dev`) would cache by path without cookies in the key, and would keep un-headered 200s
  for 2 hours. That would leak signed-in pages across visitors. The new test fails if it is
  configured.

## 9. Validation

From `frontend/`, Node 22.23.3 (arm64, nvm). `node_modules` came from the sibling worktree
`adg-HOSTED-NEXT16-01`, whose `package-lock.json` is byte-identical. Output is in
`CLOUDFLARE-DEMO-CACHE-01-evidence/gates.txt`.

| Command | Result |
| --- | --- |
| `npm run lint` | exit 0 |
| `npm run typecheck` | exit 0 |
| `npm test` | 42 files, 1452 tests passed |
| `npm run cf:typecheck` | exit 0 |
| `npx vitest run tests/cloudflare tests/render` | 4 files, 91 tests passed |
| Mutation check | adding `"cache": { "enabled": true }` to `wrangler.jsonc` fails `keeps Workers Cache off`; reverted |
| Backend (ruff, mypy, pytest) | not run: no backend file changed |
| `npm run build`, local requests, Cloudflare | not run: no runtime code changed, so there is no new behaviour to request; no Cloudflare verification was performed |

## 10. Files changed

- Added: `frontend/tests/cloudflare/cache-policy.test.ts`
- Added: `docs/demo/reports/CLOUDFLARE-DEMO-CACHE-01-report.md`
- Added: `docs/demo/reports/CLOUDFLARE-DEMO-CACHE-01-evidence/gates.txt`
- Modified: `docs/deployment/cloudflare-demo.md` (new §2 "Caching")

## 11. Cache invalidation procedure

Nothing is shared-cached, so nothing needs invalidating. The static files are content-hashed, and a
deploy replaces them. If an option in §12 later adds a cache, key it by `ADG_DEMO_DATASET` plus the
logical fingerprint. A reset that changes the fingerprint then changes the key (v1 → v2 by
construction), with no purge.

## 12. Remaining limitations and owner options

Egress from the demo database can only be cut by one of these options. Each is a decision for the owner.

1. **Server-side read cache in FastAPI (recommended if egress matters).** Cache the immutable estate
   reads per (endpoint, query, capability set, collection-basis token) in process on Render.
   - No authorization change and no Cloudflare dependency.
   - Writes by planners (simulations, change plans) must stay uncached.
   - Cuts PostgreSQL egress for repeated signed-in renders.
2. **An anonymous, read-only demo API for dataset-level views.** Examples: the risk summary, the
   resource directory and the access explanations.
   - Edge-cached for a year, keyed by dataset and fingerprint.
   - Changes who may read the demo data, and must exclude visitor-written records.
3. **Workers Cache gateway pattern with the role in `ctx.props`.** This shares answers between all
   sessions of the same role.
   - Requires accepting that same-role answers are identical. That is false today: search, risk
     summary and the governance queue vary by principal.
   - Needs every route to send an explicit `Cache-Control`. Not recommended.

Other limitations:
- The deployed Worker is on `workers.dev`. Cloudflare documents a functional Cache API for Workers
  on custom domains; Workers Cache is documented to work on `workers.dev`.
- Free Render cold starts (about 85 s) are unaffected. Edge caching of anonymous HTML could hide
  them, but it saves no Supabase egress and was out of scope.

## 13. Recommended next step

Read Supabase's usage page (egress by project) for the ADG project before choosing an option in
§12. If the demo database's egress is mostly resets, `verify` and fingerprint runs, rather than page
views, no request-path cache will help, and the reset cadence is the lever.
