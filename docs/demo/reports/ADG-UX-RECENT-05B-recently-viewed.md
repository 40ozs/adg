# ADG-UX-RECENT-05B: Recently Viewed implementation

- **Date:** 2026-10-30
- **Lane:** ADG-UX-RECENT-05B (implementation and browser validation)
- **Branch:** `feature/ADG-UX-RECENT-05B-recently-viewed` (pushed; not merged, not deployed)
- **Worktree:** `/Volumes/T5 EVO/mmm/worktrees/adg-UX-RECENT-05B`
- **Evidence:** [`ADG-UX-RECENT-05B-evidence/`](ADG-UX-RECENT-05B-evidence/)

```
BASELINE=origin/integration/ADG-UX-WAVE-B-01 @ 10c73d1fcb5b12b31fa54e57b95da3483c079fba
DESIGN_CONTRACT=ADG-UX-RECENT-05A, docs/demo/reports/ADG-UX-RECENT-05A-recently-viewed-architecture.md
DESIGN_HEAD=design/ADG-UX-RECENT-05A-recently-viewed-architecture @ 5a4c27ba6d21802bffc10c5b5daa64bf7f9d3309
CODE_COMMIT=7393d03c117c49748bb4997014b720eaf531556b  feat(adg-ux): add tab-local Recently Viewed
FINAL_COMMIT=the commit that adds this report and its evidence (the branch head)
```

05B branched from the Wave B UX baseline `10c73d1`, **not** from the 05A docs branch. The 05A
report was read from `origin/design/ADG-UX-RECENT-05A-recently-viewed-architecture`, which is
`10c73d1` plus that one report. On 2026-10-30 `git fetch` found no ref descending from
`10c73d1` other than the 05A docs branch, so `10c73d1` was still the newest validated baseline.

**Verdict: `READY_FOR_ADG_UX_RECENT_INTEGRATION_WITH_LIMITATIONS`.** The limitations are the
ones 05A accepted (§9 below). None blocks integration.

---

## 1. Architecture

| Item | Implementation |
| --- | --- |
| Storage | `window.sessionStorage` only, one key: **`adg.recentlyViewed.v1`** |
| Value | `{"v": 1, "owner": "<hex sha256>", "entries": [{kind, key, label, at}]}` |
| Owner binding | `owner` = hex SHA-256 of `viewer.principal.subject` from `/auth/me`, computed on the server (`lib/recent-owner.ts`, `node:crypto`, `server-only`) and passed to client components as a prop |
| Store | `frontend/lib/recent.ts`: parse, validate, prune, record, forget, clear, filter, href. Takes `now` as an argument (default `Date.now()`), so tests prove expiry without a clock |
| Client boundary | `frontend/components/RecentlyViewed.tsx`: `RecordRecentView` and `ForgetRecentView` (render `null`, write in `useEffect`), and `RecentlyViewed` (the Overview section). Nothing else became a client component |
| Server | No endpoint, no migration, no backend change, no API read or write for the list |

Files (code commit `7393d03`, +2119 lines, 15 files):

- new: `lib/recent.ts`, `lib/recent-owner.ts`, `components/RecentlyViewed.tsx`;
- wired: `app/identities/principal/[key]/layout.tsx`, `app/resources/{server,share,directory}/page.tsx`,
  `app/page.tsx`, `components/SignOutButton.tsx`, `components/overview.module.css`;
- tests: `tests/recent.test.ts`, `tests/recent-routes.test.tsx`, `tests/recently-viewed.test.tsx`,
  `tests/overview-page.test.tsx` (+4 tests);
- browser gate: `frontend/cloudflare/checks/recently-viewed.mjs`.

## 2. 05A §14 contract, item by item

| # | 05A requirement | Implementation | Evidence |
| --- | --- | --- | --- |
| 1 | Eligible kinds: identity, server, share, directory only | `RecentKind` union; `isEntry` drops any other kind on read | `recent.test.ts` "drops invalid entries" (finding/review kinds dropped); gate §7/§59 (9 non-entity pages leave storage unchanged) |
| 2 | `sessionStorage`, key `adg.recentlyViewed.v1`, §6 schema; no localStorage, IndexedDB, cookie, URL, server storage, migration or endpoint | `RECENT_STORAGE_KEY`; `save()` writes exactly `{v, owner, entries}`; extra fields are not carried forward | `recent.test.ts` "stores exactly the 05A fields"; `storage-shape.json`; gate §43; `backend-tree.txt` |
| 3 | Store imports nothing from `lib/api`, makes no request; a unit test asserts it | `lib/recent.ts` imports only `lib/contracts` (type), `lib/identity`, `lib/resources` | `recent.test.ts` "no network" (source scan of `recent.ts`, `identity.ts`, `resources.ts`, `paging.ts`: no `lib/api`, `fetch(`, XHR, beacon, socket; no localStorage, IndexedDB, cookie, console) |
| 4 | Writer only in the `ok` branch; identity in `[key]/layout.tsx`; server, share, directory in `page.tsx` | `<RecordRecentView>` is the first child of each `ok` return | `recent-routes.test.tsx` (each route: ok records, with the API's key and the short label) |
| 5 | `useEffect` keyed on (kind, key); never on prefetch, hover, a bare API call, `not_found`, `forbidden`, error, timeout or signed-out | Effect deps `[owner, kind, entityKey, label]`; nothing records outside the `ok` branch | `recent-routes.test.tsx`: forbidden, server_error, timeout, unreachable, invalid_request, unauthenticated and signed-out record nothing on all four routes; `recently-viewed.test.tsx`: no record during SSR, once under StrictMode; gate §41 (failures and prefetch) |
| 6 | `not_found` renders a forget component | `<ForgetRecentView>` only when `detail.failure.kind === "not_found"`, keyed by the URL key | `recent-routes.test.tsx` "forgets … when not found" ×4; `recently-viewed.test.tsx` ForgetRecentView; gate §41/§20 |
| 7 | One entry per (kind, key); newest wins; move to front; replace label and at | `recordRecent` filters the old entry and prepends the new one; `tidy` also dedupes on read | `recent.test.ts` dedupe and move-to-front, dedupe ignores label; gate §40 repeat, §46 |
| 8 | 10 entries, 8 h on every read and write, key ≤1,024 (longer not recorded), label ≤200; per-kind labels; directory = final segment | `RECENT_LIMIT`, `RECENT_MAX_AGE_MS`, `RECENT_KEY_MAX`, `RECENT_LABEL_MAX`; `directoryLabel()`; `identityLabel()` (§5.2) | `recent.test.ts` cap, expiry on read and write, over-long key, label trim, directory label; gate §40 ×2, §43 |
| 9 | `owner` = hex SHA-256 of subject, server-side prop; mismatch deletes before render | `recentOwner()`; `load()` deletes the value on an owner mismatch before returning anything | `recent.test.ts` known-answer hash (`shasum -a 256`), owner mismatch; `recently-viewed.test.tsx` "deletes another account's list before rendering any of it"; gate §37 |
| 10 | `SignOutButton` deletes the key before `POST /api/auth/logout` | `clearRecent()` is called synchronously before `fetch`, and never throws | `recently-viewed.test.tsx` SignOutButton ×2 (mutation-checked, §6.3); gate §38 (hang, 500, real) |
| 11 | Every Overview render filters by capability (identity → `identities:read`, others → `resources:read`), deletes filtered entries, omits the section when neither is held | `readRecentFor(storage, owner, viewer.principal.capabilities)`; `app/page.tsx` renders the section only with one of the two capabilities | `recent.test.ts` capability purge ×3; `recently-viewed.test.tsx` lost-access notice; `overview-page.test.tsx` omission; gate §42 |
| 12 | Every storage access in `try/catch`; blocked storage gives the §10.3 copy; a corrupt value is deleted whole | `sessionStore()`, `load()`, `save()` and `clearRecent()` all catch; a bad top-level value is deleted | `recent.test.ts` 7 corrupt shapes, oversized array, blocked storage; `recently-viewed.test.tsx` blocked copy |
| 13 | Placement, markup, copy, Clear per §10; UX-01 terms; no `aria-busy`; no per-item remove; no disable switch | `<section className="card" aria-labelledby="overview-recent">`, `<h2 id="overview-recent">Recently viewed</h2>`, §10.3 copy verbatim, one Clear button | `overview-page.test.tsx` placement; `recently-viewed.test.tsx` SSR markup, states, Clear, single button; gate §48, §45; screenshots |
| 14 | The value never goes into logs, telemetry, error reports, exports, URLs or requests; titles stay static | No logging, no telemetry; no `generateMetadata` reads the list | `logging-search.txt`; gate §21/§44 (3,321 browser + 771 web-tier requests scanned), §51; titles gate 10/10 |
| 15 | Store unit tests (dedupe, cap, expiry, owner, capability, corrupt JSON, throwing storage, long key, label trim, no `lib/api`) | All present | `tests/recent.test.ts` (33 tests) |
| 16 | Writer test: mounted only on `ok`, for each of the four routes | Writer and forget are replaced by markers that carry their props | `tests/recent-routes.test.tsx` (39 tests) |
| 17 | Forget-on-`not_found` test | Route and component level | `recent-routes.test.tsx`, `recently-viewed.test.tsx` |
| 18 | SignOutButton clears before the request | Ordering test plus a hanging-request test | `recently-viewed.test.tsx`; `signout-mutation-test.txt` |
| 19 | Overview section test: states, Clear, capability omission, order relative to "Where to start" | | `recently-viewed.test.tsx` (14 tests), `overview-page.test.tsx` "Recently viewed (ADG-UX-RECENT-05B)" (4 tests) |
| 20 | Browser step: identity + directory, Overview lists both newest first, sign in as another account, list empty; existing Overview, titles, Wave A and Wave B gates stay green | Gate §37 runs exactly this (admin: identity, then directory; switch to planner) and checks it in both directions | `recently-viewed-gate.txt` 25/25; Overview 49/49, titles 10/10, Wave A 14/14, Wave B 8/8 |

## 3. Eligible objects

| Tracked | Recorded where | Key | Label |
| --- | --- | --- | --- |
| Identity (user, group, computer, local group) | `[key]/layout.tsx`, `ok` branch | `principal.key` (SID or `host\|SID`) | `identityLabel()`: display name → account name → last known name → SID |
| Server | `resources/server/page.tsx` | `server.key` | `server.name` |
| Share | `resources/share/page.tsx` | `share.key` (`host\|share`) | `share.name` |
| Directory | `resources/directory/page.tsx` | `resource.key` (UNC comparison key) | final segment of `resource.path` |

**Not tracked:** Findings (`/risks`), Reviews and campaigns (`/governance`), What-if (`/simulations`),
Access/investigate, Changes and compare/impact, Search, all list pages, filters and query states,
and the Demo guide. The gate visits nine of these and storage does not change (§7/§59).

Links are rebuilt from `(kind, key)` with the existing helpers (`principalHrefForKey`,
`serverHref`, `shareHref`, `directoryHref`). Rendered URLs are never stored, there is no
redirect endpoint, and navigation is ordinary `next/link`.

## 4. Recording, retention and removal

- **Trigger:** the writer is mounted only after the page's primary entity read returned `ok`.
  A failure renders the page's existing state message and no writer. The gate checked a
  not-found server, a forbidden server (fixture: proxy 403), an API error (fixture: proxy 500),
  a malformed directory key, a malformed identity escape and an unknown SID. None recorded.
- **Prefetch:** Next prefetches do not mount client components. The gate hovered five server
  links on `/resources` and scrolled them into view, and nothing was recorded.
- **Identity views:** the writer sits in the `[key]` layout. Switching views re-renders only the
  view page, so it neither re-records nor duplicates. A unit test asserts that the view page
  holds no writer.
- **Repeat views:** the old entry is removed and the new one goes first with a new label and
  `at` (gate §40: no duplicate, newer time; §46: a revisit moves to the top).
- **Retention:** 10 entries, with the oldest evicted (gate: the 11th unique server evicted the
  first). Entries older than 8 h are pruned on every read and write; the gate seeded one at
  8 h 1 min and it was gone from storage and never rendered, even transiently. The list lasts
  as long as the tab.
- **Not found:** following a stale entry (the gate seeded `gp-gone-fs99`) shows the page's own
  "Not found" and deletes the entry. Nothing checks existence in the background.

## 5. Privacy

### 5.1 Stored fields

Exactly as 05A §6, with synthetic demo values (`storage-shape.json`):

```json
{ "v": 1, "owner": "100d115b…c5d29e",
  "entries": [
    { "kind": "identity",  "key": "S-1-5-21-3949361974-1583827165-633756269-1372", "label": "Chen Dunn",   "at": 1793409032628 },
    { "kind": "server",    "key": "gp-fin-fs01",                                   "label": "GP-FIN-FS01", "at": … },
    { "kind": "share",     "key": "gp-fin-fs01|finance",                           "label": "Finance",     "at": … },
    { "kind": "directory", "key": "\\\\gp-fin-fs01\\finance\\reports\\revenue",    "label": "Revenue",     "at": … } ] }
```

Gate §43 asserts the following against the real serialized value:

- the only top-level fields are `v`, `owner` and `entries`, and the only entry fields are
  `kind`, `key`, `label` and `at`;
- `owner` is `sha256("dev:admin")`, and neither the raw subject nor the display name is stored;
- the identity's UPN (`chen.dunn@geneseepress.example`) is absent, and so is anything shaped
  like an email address or a GUID;
- no Findings rule title (all of `/api/v1/risks/rules`) appears;
- no label contains `\` or `/`. The directory label is `Revenue`, and the full path
  `\\GP-FIN-FS01\Finance\Reports\Revenue` remains only as the canonical key.

### 5.2 Intentional deviation: identity labels never fall back to the UPN

05A §6 names `principalLabel(...)` as the identity label, but that helper falls back to
`user_principal_name` before the SID. 05A §6 also says UPN and email are "never stored". These
two rules conflict for an identity that has a UPN but no display or account name. 05B resolves
the conflict on the privacy side. `identityLabel()` in `lib/recent.ts` uses display name →
`sam_account_name` → `last_known_name` → SID, and **never the UPN**. The page heading still uses
`principalLabel` unchanged. This is tested in `recent.test.ts` ("never by UPN") and
`recent-routes.test.tsx` ("never labels an identity with its UPN"). Do not reintroduce the
fallback.

### 5.3 Network and logging

- **No request carried the list:** gate §21/§44 scanned 3,321 browser requests (URL, headers,
  body) and 771 web-tier → API requests:
  - no request carried the storage key, `recentlyViewed`, either owner digest, the serialized
    `"entries"` or the stored identity label;
  - no request carried the identity, share and directory keys together.
  Ordinary route requests carry one entity key, as routing always did.
- **API reads:** 0 added. With an empty list and with ten entries, the Overview made the same 9
  web-tier API requests (§51). The browser made no `/api/` call on the Overview.
- **Clear:** 0 web-tier API requests. The only browser request after Clear was Next prefetching
  the `/changes?days=7` link as the shorter page brought it into view.
- **Logging:** none. See `logging-search.txt`: no `console.`, logger, beacon, `navigator.`,
  localStorage, IndexedDB or cookie access in the new or changed product files.
- **Backend writes:** 0. The backend tree is identical (§8).

## 6. Account isolation

1. **Account switch:** the gate ran admin → planner in the **same tab without signing out**,
   through the login page. That leaves the owner check as the only thing that can clear, so it
   is the hard case.
   - A MutationObserver installed before any page script logged every row the list ever
     rendered. It logged **0** rows for planner.
   - The server HTML for `/` held none of admin's labels.
   - admin's value was deleted from storage.
   - In the other direction, planner records an entry, admin signs in and opens a share, and
     admin's list starts fresh, owned by admin.
   - Screenshot: `screens/account-switch-planner.png`.
2. **Sign-out:** `clearRecent()` runs before `fetch("/api/auth/logout")`. The gate wrapped
   `fetch` in the page and recorded storage at the moment the logout request started:
   - for a hanging logout, a 500 logout and the real logout, storage was already `null`;
   - while the hanging request was still pending, storage was already empty.
3. **Sign-out mutation check:** `clearRecent()` was deliberately moved to after the request.
   Both SignOutButton unit tests failed (`2 failed | 12 skipped`). The file was then restored,
   and the full suite passes. See `signout-mutation-test.txt`.
4. **Same-account re-authentication:** gate §39 deleted the session cookie to simulate expiry.
   - The Overview showed the signed-out notice and no list, and storage was untouched.
   - Signing back in as the same account in the same tab showed the same rows. The owner
     digest follows the subject, not the token.
   - The 8 h limit still applies (§4).
5. **Tabs:** each tab's list is its own `sessionStorage` (gate §36).
   - A new independent tab (`context.newPage()`) starts empty.
   - A `window.open(…, "noopener")` tab also starts empty.
   - A `window.open` tab **with** an opener starts with a copy of the opener's list, because the
     HTML spec copies `sessionStorage` into such a tab; Chrome's "Duplicate tab" does the same.
     After that, the two lists diverge.
   - Recording in another tab never changed tab A.

## 7. Permission filtering and the Overview

- **Filtering:** every Overview render passes `viewer.principal.capabilities` (that request's
  `/auth/me`) to the client section, which filters entries and deletes the rest.
  - Gate §42 used a fixture: the proxy rewrote `/auth/me` without `resources:read` for the same
    account.
  - Only the identity row rendered, the server and directory entries were deleted from
    storage, and the §10.3 notice was shown.
  - The API paths during that render were the Overview's own 9. That is **0** per-entry
    authorization or existence requests, so there is no N+1.
- **Placement:** after Needs attention, Recent changes / Reviews and the lower Data health
  slot, and before "Where to start".
  - In the demo dataset collection has failed, so Data health renders at the top. Recently
    viewed then follows Recent changes / Reviews (`screens/populated-1440.png`).
  - With healthy collection, Data health takes the lower slot and Recently viewed comes after
    it (`overview-page.test.tsx`).
- **Progressive rendering:**
  - The section sits outside every Suspense slot, has no `aria-busy`, and makes no server read.
  - The server renders its heading and description; the list or empty state fills in after
    hydration.
  - The Overview gate (49/49), Wave A (14/14) and Wave B (8/8) gates are unchanged, and hydration
    produced no console errors.
- **Empty state:** "Nothing yet. Identities, servers, shares and directories you open in this
  tab will appear here." (`screens/empty-1440.png`).
- **Clear:** one `Clear` button. It clears storage and the UI at once, moves focus to the
  section heading (`tabIndex=-1`, so the button's removal does not drop focus to `<body>`), and
  does not scroll (scrollY 1236 → 1236).
- **Compact rows:** `Kind <link>` per line. Long labels wrap inside the card
  (`overflow-wrap: anywhere`). At 1440, 768, 375 and 320 px: 0 px horizontal overflow, short
  rows ≤ 28.8 px, Clear 69 × 41 px. A 200-character label and an unresolved SID were included
  (`screens/populated-375.png`, `populated-320.png`).
- **Accessibility (checks, not an audit):**
  - a region named by its h2;
  - link names equal to the labels, with the kind in each row's text;
  - exactly one button, named "Clear";
  - no nested interactive elements;
  - Enter on a focused row link navigates;
  - hydration leaves focus on `<body>`.

## 8. Validation

| Gate | Result | Evidence |
| --- | --- | --- |
| `npm run lint` | exit 0, 0 problems | `lint.txt` |
| `npm run typecheck` | exit 0 | `typecheck.txt` |
| `npm run cf:typecheck` | exit 0 | `cf-typecheck.txt` |
| `vitest run` | **1684/1684** in 49 files (baseline 1594; +90: store 33, routes 39, components 14, Overview 4) | `vitest.txt` |
| `next build --webpack` (standalone) | exit 0 | `build.txt` |
| **Recently viewed gate** `cloudflare/checks/recently-viewed.mjs` | **25/25** on run 4 and again on run 5 | `recently-viewed-gate.txt` (run 5), `recently-viewed-gate.run4.txt`, `.json`, `history.txt` |
| Overview `cloudflare/checks/overview.mjs` | **49/49** | `overview-gate.txt` |
| Wave A `ADG-UX-WAVE-A-INTEGRATION-01-evidence/integration-check.mjs` | **14/14** | `wave-a-integration-check.txt` |
| Wave B `ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-b-check.mjs` | **8/8** | `wave-b-check.txt` |
| TITLES `cloudflare/checks/titles.mjs` (real API) | **10/10** | `titles-gate.txt` |
| Backend | not rerun: `backend`, `database`, `docker` and `.github` trees are byte-identical to `10c73d1` | `backend-tree.txt` |
| CI | see §11 | |

Every browser gate ran against the real local stack:
- the Genesee Press demo database (a copy of the Wave B lane's shut-down data directory) on
  pgserver PG 16, port 55487;
- the API from this worktree (`python -m app`, demo mode) on 8050;
- the standalone Next build on 3050, with the gate's recording proxy on 8150;
- Chrome 156 with Playwright 1.62.1.

Load averages during the runs were 4.5–12.6 (other lanes were active); see the `*.load.txt` files.

**Early gate failures were defects in the gate, not the product.** Product code did not change
between run 1 and run 5. See `recently-viewed-gate.history.txt`:
- run 1, 7/25: the gate waited for two `<p>` in the section, but a populated section has one
  `<p>` plus the list;
- run 2, stopped at §38: the gate's own in-page `fetch` wrapper threw on the `URL` objects Next
  passes, so Next fell back to full-document navigation and §46 reported a reload. A hung-logout
  mock also aborted an already-handled route;
- run 3, 23/25: §45 counted a viewport prefetch as a Clear request (web-tier API count was 0).
  §21's multi-key heuristic matched the server key inside the share key plus the directory key
  in a prefetch `Referer`. Both checks were narrowed and now print what they saw;
- runs 4 and 5: 25/25. Between them, one unused variable was removed for lint.

Runs 1–3 were not saved as separate logs; their outcomes are summarized in the history file.

## 9. Limitations

These are the 05A-accepted limitations, which remain:

- **Per tab.** A new tab starts empty. A tab opened with an opener, or duplicated, starts with a
  copy (§6), as the browser defines it.
- **Restored tabs.** A browser that restores tabs keeps their `sessionStorage` on disk, bounded
  by the 8 h expiry.
- **8 h retention**, 10 entries.
- **Deleted objects.** A deleted object's label stays until its link is followed.

Additional notes from this lane:

- Hosted streaming through the Worker → `render/gate.ts` path is still unverified. This is
  inherited from Wave B and not specific to this lane.
- The section fills in after hydration, so the two static cards below it ("Where to start",
  "What ADG will not do") can move down once it does. 05A §10.1 accepts this.
- Observed, out of scope: the detail pages answer HTTP 200 with their not-found message, and
  `/identities/principal/%E0%A4%A` (a malformed escape) answers 500 from Next's router. Both
  are pre-existing.

## 10. Future-upgrade attachment points (05A §15, not implemented)

1. **Move to `localStorage`:**
   - change `sessionStore()` in `lib/recent.ts`;
   - drop `label` for identities in `RecordRecentView`'s identity call (the `[key]` layout);
   - resolve identity labels with one bounded batch read in the Overview server component,
     passing labels as a prop;
   - keep the schema, owner check, limits and sign-out clear;
   - bump `RECENT_VERSION`. `parse()` deletes any other version whole.
2. **Object-level authorization:** replace the capability filter in `readRecentFor` with
   server-side re-authorization of the entries (at most one batch read per router) before the
   section renders.

## 11. Operations note: ENOSPC

While the run-5 output was being copied into this directory, the Mac's internal disk filled up
completely (ENOSPC). See `enospc-note.txt`.

- **Nothing was lost.** The implementation lived on the T5 drive. Afterwards, the branch,
  ancestry, `git fsck` and every changed file were checked. Every file was last modified before
  the validation that covers it, so no gate was rerun.
- **What was stopped and deleted:**
  - Stopped: this lane's own web (3050), API (8050) and Postgres (55487) processes, identified
    by port and working directory.
  - Deleted: only this session's disposable Postgres copy, about 415 MB, in the session
    scratchpad (`…/ca4c0e71-…/scratchpad/pgdata`).
  - No other session, worktree, cache or lane was touched.
- **Run 5 was preserved.** Its output survived in the scratchpad and was copied here.

**CI:** the run for the code commit `7393d03` and the run for this report commit are recorded in
the follow-up below.

## Not done, by design

- **NOT MERGED** (neither to `integration/ADG-UX-WAVE-B-01` nor `main`).
- **NOT DEPLOYED.**
- Nothing outside the scope 05A rejected: no localStorage, no server history, no endpoint, no
  migration, no analytics, no per-item remove, no disable switch, and no tracking of Findings,
  Reviews, What-if, compare or investigate.
