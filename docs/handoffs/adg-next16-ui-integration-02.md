# ADG-NEXT16-UI-INTEGRATION-02: compact identity and Exclude local groups on Next.js 16

Integration of the compact-identity / Groups addendum (`adg-ui-update-01.md` §7) with the
Next.js 16.4.0 upgrade that resolved React #418 (`adg-next16-react418-01.md`), plus the
accepted follow-up: long SIDs keep their distinguishing end. Deployed to staging and made the
live tree of `deploy/ADG-FREE-STAGING-01` again. Not merged to `main`.

**Verdict: `READY_FOR_ADG_COMBINED_STAGING_REVIEW`.** React #418 stays resolved
(`UPSTREAM_REACT_418_RESOLVED`): 0 in 5 hosted browser-gate runs, 5 navigation runs,
2 instrumented runs and 208 hosted reloads, and 0 in the local runs.

## 1. Git

| | |
| --- | --- |
| Branch | `integration/ADG-NEXT16-UI-02` from `fix/ADG-NEXT16-REACT418-01@04663bc`, worktree `../adg-integration-NEXT16-UI-02` |
| `9502b4b` | `git merge --no-ff` of `fix/ADG-UI-COMPACT-IDENTITY-01@f50e8f2`. No conflict: the Next 16 branch changes only `frontend/package*.json`, `eslint.config.mjs` and `tsconfig.json`; the UI branch changes none of them. No rebase, squash or cherry-pick. |
| `33cca01` | SID leading truncation (§3). The deployed commit. |
| Source branches, unchanged | `deploy/ADG-FREE-EDGE-SSR-03@57bbbaa`, `fix/ADG-NEXT16-REACT418-01@04663bc`, `fix/ADG-UI-COMPACT-IDENTITY-01@f50e8f2` |
| `deploy/ADG-FREE-STAGING-01` | fast-forwarded `6b69fe3` → this handoff's commit (§8) |

Next 16 configuration kept as validated: `next` 16.4.0 (bundled React 19.3 canary),
`next build --webpack`, the flat `eslint-config-next` config, `jsx: react-jsx`, the standalone
Render build, the thin Worker. No Suspense boundary was added and Turbopack is not used.

## 2. Compact identity and Exclude local groups (kept from `f50e8f2`)

Unchanged by the merge; re-verified on staging (§6):

* Name cells hold the name only; Users, Groups and search keep a one-line SID column;
  relationship tables carry no SID under names; Identity information shows the full SID and key.
* Shortest Chains: a resolved hop is its name as a link; only an unresolved hop shows its SID.
* Access masks and other code values in cells never wrap mid-value.
* Groups: `Exclude local groups`, off by default. It removes `PrincipalKind.LOCAL_GROUP` only
  (host-scoped, from the data model, never from a name); Domain Local groups stay. The server
  applies it (`GET /api/v1/principals?kind=groups&exclude_local=true`), so search, keyset pages
  and `page.total` follow it. A change is a client-side `next/form` navigation that keeps the
  search and the column sort and returns to page 1; the URL carries `exclude_local=1` through
  paging, new searches and Back/Forward. Null Meridian: 238 groups, 33 local, 205 excluded,
  counted by the API from the data, not hard-coded.

## 3. SID truncation direction (`33cca01`)

An end ellipsis hid the RID, the only part that differs between SIDs of one domain. Now a long
SID loses its start: `…1974-1583827165-633756269-1103`.

| File | Change |
| --- | --- |
| `components/Facts.tsx` | `Sid`: `Identifier` with the class `identifier sid` |
| `app/globals.css` | `.identifier.sid { direction: rtl }` puts the ellipsis on the left; the row-focus rule (`tr:focus-within .identifier`) adds `direction: ltr`, so the full value reads as before |
| `components/PrincipalDirectory.tsx` | Users and Groups SID column → `Sid` |
| `app/search/page.tsx` | identity rows' Identifier cell → `Sid` (servers, shares and directories keep `Identifier`) |
| `components/Identity.tsx` | an unresolved principal whose only label is its SID (relationship tables, Shortest Chains hops) gets the same class |
| tests | 4 assertions: SID cells carry `sid` (directory, chain orphan, SID-only name); a UNC path does not |

The SID value is not parsed or changed. The text, the `title` (full value on hover), what a copy
takes and what a screen reader reads are the whole SID, and row focus still shows it in full.
`direction: rtl` is safe only because a SID is all left-to-right characters (`S`, digits,
hyphens), so the characters keep their order. A UNC path's leading `\\` or a host name would be
reordered or lose its distinguishing start, so paths, keys and hosts keep the end ellipsis.
Mutation check: removing the class fails 3 tests.

## 4. Local validation

| Check | Result |
| --- | --- |
| frontend `lint` / `typecheck` / `cf:typecheck` | clean / clean / clean |
| frontend `vitest` | 39 files, 1,306 passed |
| backend `ruff check` / `ruff format --check` / `mypy app tests` | clean / 400 files formatted / no issues in 399 files |
| backend `tests/db/test_graph_api.py` (incl. the directory and exclude-local tests from `f50e8f2`) + OpenAPI snapshot, `ADG_RUN_SMOKE_TESTS=1`, Python 3.13, local Postgres 16 | 62 passed |
| `next build --webpack`, standalone | ok, Next.js 16.4.0 |
| `wrangler deploy --dry-run` (4.149.0) | ok, 4.00 KiB, no bindings beyond `ADG_RENDER_ORIGIN` |
| `actionlint` 1.7.7 | clean |
| `navigation.mjs` ×3, local gate (API + standalone Next, `adg_demo` read only, throwaway origin key) | **19/19** each, 0 page or console errors |
| instrumented `browser.mjs` ×3, local gate | **22/22** each, #418 hook applied on 24/24 loads, 0 hydration reports, 0 page errors |
| reload loop, local gate, 26 pages × 4 | 104 loads, **0 #418**, 0 page errors |
| CI on `33cca01` (backend lint/types/tests, frontend lint/types/tests/build, collector, compose) | all green |

## 5. Deployment

| | |
| --- | --- |
| Render `adg-demo-web` | deploy `dep-db6pe17f3r2c73c0e8f0`, commit `33cca01aa68a548e0ac738899f4b61f0a4bc0f16`, live 02:29:44Z on 2026-10-13. Auto-deploy off. Branch: `integration/ADG-NEXT16-UI-02` for the deploy, then `deploy/ADG-FREE-STAGING-01` (§8). |
| Edge assets | `docker build --platform linux/amd64 -f docker/Dockerfile.render --target web --build-arg RENDER_GIT_COMMIT=33cca01…`, after Render was live → `cf:assets`: 67 files |
| Worker `adg-demo-staging` | version `66663d4a-c925-44dc-bdca-011e1d8f9c65`, tag `33cca01aa68a`, 02:30:55Z; 4.00 KiB; var `ADG_RENDER_ORIGIN`, secret `ADG_ORIGIN_KEY` kept |
| Database | `adg_demo`, not reset |
| Rollback | Worker `wrangler rollback 19b5a60a-fe07-4599-aba8-a7e8466fb946` (Next 16 alone, `66ca201`); Render: deploy `66ca201` (an ancestor of the deploy branch). Both together. |

**Edge-asset parity.** `BUILD_ID` in the image is `33cca01…`. Every `/_next/static` file that
Render's HTML for `/`, `/identities/groups` and `/status` references (10) is in `.edge-assets`.
The hosted reload loop requested 24 distinct static files, and all 24 are in `.edge-assets`;
two of them are the `[key]` route chunks, which the Worker serves from assets with
`cache-control: public, max-age=31536000, immutable`. The loop first reported those two
missing because it looked up their URL-encoded paths on disk; it now decodes them.

## 6. Hosted checks

| Check | Result |
| --- | --- |
| `smoke.mjs` ×2 (database host and project ref as canaries) | **25/25** each |
| direct to Render | 12 requests without the key or with a guessed one → **403**; only `/health/live` open |
| public accounts | `viewer`, `planner` only; admin, auditor, reviewer, governance, approver → **404** |
| privileged writes / ingestion | 8 privileged writes → **403**; 3 ingestion routes → **403 `demo_only`** (anonymous and planner) |
| `browser.mjs` ×5 (repository gate) | **22/22** each, 0 page errors |
| `navigation.mjs` ×5, after each browser run | **19/19** each, 0 page or console errors |
| instrumented `browser.mjs` ×2 | **22/22** each, hook applied on 24/24 loads, **0 #418** |
| reload loop, signed in as planner, 26 pages × 8 | 208 loads, **0 #418**, 0 page errors |
| `verify` (read-only), before and after | ok, 23 probes, `228b9dce…f9efd0` (unchanged) |
| Null Meridian logical fingerprint, before and after | `db736602…85c28a0`, **match** (unchanged) |

The only console error in a browser run is the deliberate `POST /api/adg/api/v1/scan-runs` → 405.

Reload pages: `/`, `/status`, `/identities`, `/identities/users`, `/identities/groups`,
`/identities/groups?exclude_local=1`, `/search?q=Finance`, `/demo`, `/risks`, a direct
`/access/explain`, and each scenario's folder and first person from the `/demo` guide (16).

**On screen**, a scripted Chromium session at 1360 × 900 against the public URL. All passed:

| What | Measured |
| --- | --- |
| Users rows | 50 rows, 39.8 px each; no SID under a name |
| Groups rows | 50 rows, 39.8 px each |
| SID leading truncation | Users 50 SIDs (all cut at the start), Groups 50 (17 cut; `S-1-5-32-544` fits), search 5: computed `direction: rtl`, one line, `title` = value; the last character inside the cell and, when cut, the first outside it |
| Other identifiers | search hosts/paths stay `ltr` (start kept) |
| Shortest Chains (Bianca Rossi, effective groups) | 29 chains, 144 links, 0 raw SIDs; `Bianca Rossi → Sales-Enterprise → GG_Sales → All Staff → AD-Okta-Sync-All → OKTA_Slack_All → sg-okta-slack-user` |
| Access masks (scenario A folder) | each `td code` on one line (`0x001F01FF`) |
| Exclude local groups | off by default; 238 → **205** (33 local), no document reload, page 1, Domain Local kept; page 2 and a new search keep `exclude_local=1`; Back ×3 → 238 unchecked, Forward → 205 checked |

## 7. Worker CPU

Cloudflare analytics, version `66663d4a`, 02:30:55Z–02:47:49Z (all hosted runs above):

| Status | Requests | p50 | p90 | p99 | p99.9 | max |
| --- | --- | --- | --- | --- | --- | --- |
| `success` | 6,488 | 0.69 ms | 1.14 ms | 1.71 ms | 26.29 ms | 36.99 ms |
| `clientDisconnected` | 939 | 0.54 ms | 0.89 ms | 1.30 ms | 1.80 ms | 1.80 ms |

**`exceededCpu`: 0. Errors: 0.** Proxy-level, as before (Next 16 alone: p50 0.78 ms, p99
2.20 ms, max 26.77 ms). Next.js runs only on Render; the Worker is the 4 KiB proxy plus static
assets.

## 8. Deploy-branch reconciliation

After every gate above passed:

1. This handoff was committed on `integration/ADG-NEXT16-UI-02`, docs only on top of `33cca01`.
2. `deploy/ADG-FREE-STAGING-01` was fast-forwarded from `6b69fe3` to that commit. No history was
   rewritten.
3. Render `adg-demo-web` tracks `deploy/ADG-FREE-STAGING-01` again, with auto-deploy off. The
   live deploy stays `33cca01`.

The live Render commit (`33cca01`), the Worker's asset build (`BUILD_ID` `33cca01`) and the
deploy-branch head have the same application tree: the head differs from `33cca01` only under
`docs/`. The push to the deploy branch starts the "Free staging" workflow. Its preflight fails
closed because the `cloudflare-staging` environment holds no secrets, so it deploys nothing.

## 9. Open

* **October 14 Next.js security release:** still to be applied as a follow-up patch, then §4–§6
  rerun, before any of this is considered for `main` (`adg-next16-react418-01.md` §6).
* **Horizontal scroll at 1360 px:** the Groups (7 columns) and Users (6 columns) directories
  scroll inside their own container. This was accepted for this wave, and the SID column's width
  (the 16rem bound) did not change.
* **Not changed, by scope:** the Shares view's three-line rows (`assumed_token_sids` and
  "Measured against the ACLs ADG holds." notes).
* **Browsers:** only Chromium was checked. A right-to-left ellipsis is standard CSS, but Firefox
  and Safari were not run.
* **Harnesses:** the instrumented gate, the reload loop and the on-screen script are not
  committed. They are copies of `browser.mjs` with the hook from `adg-ui-edge-01.md` §5, a
  full-load loop, and Playwright measurements as described in §6.
