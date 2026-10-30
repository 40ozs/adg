# ADG-UX-TITLES-03 — Route-aware document titles

**Verdict: `READY_FOR_ADG_UX_TITLES_INTEGRATION_WITH_LIMITATIONS`** (limitations: §9).

Before this lane, every ADG page had the browser title `ADG`. Each page now has a short
title that says where the user is, such as `Findings — ADG`, `Identity — ADG` or
`Page not found — ADG`. Primary sections take their title from the ADG-UX-01 nav labels, so
`/risks` reads **Findings**. Titles are static Next.js metadata. They are correct on direct
load, hard reload, client navigation, Back and Forward, they cost no data request, and no
SID, GUID, UNC path or full path reaches them. No heading, route, nav item, backend or
contract was changed. Nothing was merged or deployed.

## 1. Record

| | |
| --- | --- |
| Baseline | `origin/integration/ADG-UX-WAVE-A-01` @ `fcb792872363c131d2539bab1f629c5d5120445e` (the Wave B baseline, fetched 2026-10-30; no newer validated Wave B baseline existed: `origin` had only the two Wave A source lanes and the integration branch) |
| Branch | `feature/ADG-UX-TITLES-03-document-titles` |
| Worktree | `/Volumes/T5 EVO/mmm/worktrees/adg-UX-TITLES-03` |
| Code commit | `ccee9c7` (feat(ui): route-aware document titles from the nav labels) |
| Final branch SHA | the commit that adds this report (docs only, on top of `ccee9c7`) |
| Deploy | none. Deploy branch, Render, Worker and database untouched |
| Parallel lanes | ADG-UX-ENTITY-02 and ADG-UX-PERF-01 (both on `fcb7928`). No uncommitted work from either was imported (§8) |

## 2. Title architecture

Next.js `metadata` exports only. There is no client-side `document.title` code and no
`generateMetadata`.

- **`lib/nav.ts`** gets two helpers:
  - `documentTitle(name)` returns `` `${name} — ADG` ``. The format is the page name, an em
    dash, then the product, the same separator the app already uses in its SVG `<title>`s.
  - `sectionTitle(id)` returns `documentTitle(label)` for a nav item, so the nav label is
    the only place a section's name lives. An unknown id throws when the module loads,
    which fails the build and the unit test.
- **One passthrough segment layout per primary section.** Nine new files
  (`app/{risks,access,changes,identities,resources,governance,simulations,collectors,settings}/layout.tsx`)
  each export `metadata = { title: sectionTitle("<id>") }` and return `children`, which adds
  no DOM. Sub-routes inherit their section's title, in the same way `activeNavItem` keeps
  the section highlighted in the nav. For example, `/risks/alerts` is `Findings — ADG` and
  Findings is marked current.
- **Overview** (`/`) exports `sectionTitle("overview")` from `app/page.tsx`, the only route
  with no segment of its own.
- **Root layout is unchanged** (`title: "ADG"`). It is the fallback, and every page now
  overrides it.
- **Why not `title.template`:** this was the first design, and the browser gate disproved
  it. Next does not apply a root template to the root's own page (Overview read just
  `Overview`). A layout with a string title also clears the template for its children
  (`/identities/principal/*` read just `Identity`). Whole strings from one helper avoid both
  rules.
- **`app/not-found.tsx` (new).** Next's built-in 404 renders its `<title>` after the root
  layout's. The document then kept the first one, `ADG`. The new page carries
  `Page not found — ADG` and renders Next's own words ("Page not found" / "This page could
  not be found.") inside the app shell.

## 3. Title mapping

Every `page.tsx` is in `tests/titles.test.ts`. A new page fails that test until someone
gives it a title.

| Route | Title |
| --- | --- |
| `/` | Overview — ADG |
| `/risks`, `/risks/alerts`, `/risks?severity=…` | Findings — ADG |
| `/access`, `/access/explain`, `/access/investigate` | Access — ADG |
| `/changes`, `/changes/compare`, `/changes/impact` (also the `?object=` history view) | Changes — ADG |
| `/identities`, `/identities/users`, `/identities/groups` | Identities — ADG |
| `/identities/principal`, `/identities/principal/{key}/{view?}` | Identity — ADG |
| `/resources` | Resources — ADG |
| `/resources/server` | Server — ADG |
| `/resources/share` | Share — ADG |
| `/resources/directory` | Directory — ADG |
| `/governance`, `/governance/campaigns/{id}`, `/governance/items/{id}` | Reviews — ADG |
| `/simulations`, `/simulations/new`, `/simulations/{id}` | What-if — ADG |
| `/collectors` | Collectors — ADG |
| `/settings` | Settings — ADG |
| `/login` | Sign in — ADG |
| `/status` | System status — ADG |
| `/search` | Search — ADG |
| `/demo` | Demo guide — ADG |
| unknown path | Page not found — ADG (HTTP 404) |

The singular detail words (Identity, Server, Share, Directory) are the words those pages
already use in their own fallback `<h1>`s. Campaign, review-item and proposal details keep
their section's title (`Reviews`, `What-if`), which matches the nav item they highlight.

**Filters:** a filter never changes the title. `/risks?severity=critical` is
`Findings — ADG`, so browser history stays easy to scan.

## 4. Dynamic detail decision

**Generic titles. No object names in titles.**

- **Identity.** The identity is read in `app/identities/principal/[key]/layout.tsx` through
  React `cache`, so a `generateMetadata` beside it would be free on first load. On a client
  move between views, though, Next resolves metadata on the server again, while the layout
  deliberately does not re-read the identity. Naming the identity in the title would
  therefore add one identity read to every view switch, purely for the tab. It would also
  mean editing the Entity lane's layout. The title is generic, set in a new parent layout
  (`app/identities/principal/layout.tsx`).
- **Server.** Its name (`FS01`) is not sensitive, but the title would need a second server
  read in metadata. The title is generic.
- **Share and directory.** The only name is the UNC or full path, so the title is generic
  (see §5).
- **Possible future improvement.** `Jane Smith — Identity — ADG` could be added through
  `generateMetadata` on the `[key]` layout, but only if the view-switch read is acceptable
  or the name can come from an already-cached source. Unresolved identities would also need
  a SID-free fallback (`principalLabel` returns the SID for them).

## 5. Sensitive-data decision

Titles show up in browser history, in history sync, in OS window and tab switchers, and in
screen-sharing. Every title in this lane is a **static string chosen at build time**, so no
SID, GUID, distinguished name, account name, UNC path or filesystem path can reach a title.
The unit test enforces this: no route module exports `generateMetadata`. The browser gate
also checks that the detail title for a SID route contains no `S-1-…`.

## 6. Tests

`tests/titles.test.ts` adds **36 tests**:

- each of the 30 page routes resolves to its expected title (`it.each`);
- the set of pages equals the table, and no page resolves to plain `ADG`;
- every nav item's href is titled `<label> — ADG`;
- `/risks` → `Findings — ADG`, and no title says "Risks";
- no `layout`/`page`/`not-found` module exports `generateMetadata`;
- the not-found title;
- `documentTitle`, and `sectionTitle` refusing an unknown id.

The resolver uses Next's rule for whole-string titles: the page's own title, otherwise the
nearest layout's.

Frontend gates (Node 22.x arm64, Next 16.4.1), summary in
[`frontend-gates.txt`](ADG-UX-TITLES-03-evidence/frontend-gates.txt):

| Gate | Result |
| --- | --- |
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass |
| `npm test` | **1580 passed** (46 files; baseline 1544 + 36) |
| `npm run build` (`next build --webpack`) | pass |

## 7. Browser results

The new committed check is
[`frontend/cloudflare/checks/titles.mjs`](../../../frontend/cloudflare/checks/titles.mjs). It
follows `navigation.mjs`'s conventions: `STAGING_URL`, `SIGN_IN_AS` (default `admin`),
`CHROMIUM_PATH`, and reload detection by both a main-frame document request and a
`window` marker.

It ran against the production build (`next start`) in Chrome 156.0.8078.74, headless, at
1360×900, with [`stub-api.mjs`](ADG-UX-TITLES-03-evidence/stub-api.mjs) as the API. The stub
answers development sign-in and `/auth/me` and refuses everything else. Titles depend only on
the route, so the error-state pages are enough. Result: **10/10 PASS**
([`titles-check.txt`](ADG-UX-TITLES-03-evidence/titles-check.txt)).

- **Sign in.** The login page is titled `Sign in — ADG`.
- **Direct load.** The server HTML for `/risks` has exactly one
  `<title>Findings — ADG</title>`, and Findings is the single `aria-current` item.
- **Client navigation.** Starting from Overview, the check clicked all 10 primary links:
  Overview, Findings, Access, Changes, Identities, Resources, Reviews, What-if, Collectors
  and Settings. After each click, `document.title` equalled `<label> — ADG` and the nav
  marked that label as current. The whole run used one document, with no reload.
- **Back.** Four steps (Collectors ← What-if ← Reviews ← Resources): title and current nav
  item restored at each step.
- **Forward.** Two steps (Reviews → What-if): restored.
- **Hard reload** of `/changes` keeps `Changes — ADG`.
- **Filtered list.** `/risks?severity=critical` is `Findings — ADG`.
- **Detail route.** `/identities/principal/S-1-5-21-…-1001`: the server sends
  `Identity — ADG`, the document title has no SID, and Identities is the current nav item.
- **Unknown page.** `/no-such-page` returns HTTP 404 with one server `<title>`, and the
  document title is `Page not found — ADG`.
- **Errors.** There were no hydration errors: 0 page errors. The 2 console errors are the
  stub's refused API calls.

## 8. Expected integration conflicts

- **ADG-UX-PERF-01** (progressive Overview): this lane adds three lines to `app/page.tsx`
  (`Metadata` import, `sectionTitle` in the `@/lib/nav` import, one `export const metadata`
  line). When this lane started, PERF-01 had an uncommitted `app/page.tsx` edit. At commit
  time its diff of that file was empty. If PERF-01 rewrites the imports, the conflict is
  trivial: keep both and keep the metadata export.
- **ADG-UX-ENTITY-02** (entity detail): no shared file. This lane added a **new** parent
  layout, `app/identities/principal/layout.tsx`, and did not edit `[key]/layout.tsx`,
  `[key]/data.ts` or any view page. The resource detail titles are also new layouts beside
  the pages, so `resources/{server,share,directory}/page.tsx` are untouched. ENTITY-02's
  working tree (read-only check) edits the principal view page and components, so a
  textual conflict is not expected. If ENTITY-02 later wants named titles, it can override
  the generic one from `[key]/layout.tsx`.
- Pages that gained one `export const metadata` line: `login`, `status`, `search`, `demo`.
  No Wave B lane owns them.

## 9. Limitations

1. **Generic detail titles.** No object names, by design (§4, §5).
2. **Sub-pages share their section's title.** Pages such as Compare, Why access changed,
   Alerts, campaign detail and proposal detail read `<Section> — ADG`. This is intentional:
   it is coherent with the nav and avoids churn. A more specific title per sub-page is a
   possible follow-up.
3. **Unknown view of a known identity.** A bad view (`/identities/principal/{key}/bogus`)
   calls `notFound()` after the layout has started streaming. It answers HTTP 200 with the
   layout's `Identity — ADG` title, not `Page not found`. This lane does not change that status
   code, and the title is still accurate.
4. **Stub API run.** The browser gate ran against a stub API, not the Genesee dataset or the
   hosted demo. Hosted runs need `SIGN_IN_AS` set to an account that holds every section's
   capability, or the check covers only the sections that account's nav shows (it prints
   how many). Hosted public accounts lack settings access (`planner` sees 9 links).
5. **One viewport.** Desktop at 1360×900 only. No rendered UI changed except the new
   not-found page body.
6. **Visible change.** The not-found page now renders inside the app shell with an
   `<h1>Page not found</h1>`, instead of Next's bare `404 | This page could not be found.`
   block. The wording is unchanged.

## 10. Status

`NOT MERGED` · `NOT DEPLOYED`
