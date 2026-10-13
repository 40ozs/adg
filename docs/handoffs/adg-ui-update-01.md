# ADG-UI-UPDATE-01 — Users and Groups directories, navigable shortest chains, sortable tables

Implementation handoff. Integration with ADG-FREE-EDGE-SSR-03, the merge, the deployment and
the hosted results are in [`adg-ui-edge-01.md`](adg-ui-edge-01.md). The 2026-10-13 addendum
(Exclude local groups, compact identity in tables, Shortest Chains labels) is §7.

## 1. Git

| | |
| --- | --- |
| Started from | `643da21` (`deploy/ADG-FREE-STAGING-01`, the deployed tree at the time) |
| Task branch | `fix/ADG-UI-UPDATE-01` (worktree `../adg-UI-UPDATE-01`) |
| Task head | `a5cdf88` |

| Commit | What |
| --- | --- |
| `61fedb8` | API: `path_principals` on effective members, effective groups and token entries; `GET /api/v1/principals?kind=users\|groups&q=`; contract snapshots; tests. |
| `552bcb8` | UI: directories, the principal layout/view split, `PrincipalChain`, `SortableTable`, no principal-link prefetch, viewer read once per request; tests. |
| `371b90e` | `frontend/cloudflare/checks/navigation.mjs`, the browser gate for this work. |
| `861a11b` | The new directory reads named in the history and simulation repository registries. |
| `a5cdf88` | Every remaining list table on `SortableTable` (a subagent's `f853a7b`, reviewed and cherry-picked). |

## 2. What changed, and how

### Shortest chains: names, not SIDs

The API already held every name: a shortest path runs from the queried principal through
nodes the traversal itself reached, and the traversal labels all of them with one batched
`principals_by_keys` read. The views returned only `path` (keys). They now also return
`path_principals`, the principal at each key in order:

* `EffectiveMemberView` (`/groups/{id}/effective-members`, `/principals/{id}/groups?scope=effective`):
  labelled from the traversal's nodes plus the root. **No extra query.**
* `TokenEntryView.path_principals` (every access answer that renders a token or a `via`):
  labelled from the labels the answer already loaded. `resource_principals` widens its one label
  read to the chain keys (still one statement; the query-cost guard covers it).
* A key nobody described comes back as an unresolved summary (`resolved: false`), never a
  guessed name.

Display precedence (`principalLabel`): display name, account name, UPN, last known name, SID.

Renderers audited (every user-visible chain): the principal page's effective members and
effective groups ("Shortest chain"), who-reaches-a-directory's "Reaches it through", the access
explanation's route table, and a simulation's surviving routes. All use `PrincipalChain`.
Not changed: a membership *cycle* (`representative_path`) is still shown as keys; it is a
finding about the directory, not a chain to navigate.

### Shortest chains: navigable

`PrincipalChain` (`components/Identity.tsx`) renders each resolved hop as a `next/link` to
`/identities/principal/{storage key}` — the canonical identity, never the name — with
`prefetch={false}`. An unresolved hop is `<code>` text, never a link. Clicking a hop is a
client-side navigation into the same principal page every other link uses, so it keeps the
shell, pushes a history entry, and Back/Forward work. `chainPrincipals` falls back to keys
(unresolved, unlinked) if an answer ever arrives unlabelled.

Computer accounts are principals (`kind: computer`) and open their principal page. ADG has no
key that maps a computer principal to a collected server, so a chain does not link to the
server page; none of the demo's chains contains a computer.

### One object model: `/identities/principal/{key}/{view}`

The principal page was one server page keyed by `?key=&tab=`. Every tab click re-rendered the
whole page on the server, re-read the identity and collection state, and scrolled to the top.
It was already a soft navigation (no document request; measured on the live site), but it
behaved like a reload.

Now:

* `app/identities/principal/[key]/layout.tsx` reads the principal once and renders the
  heading, the Identity information disclosure and the view tabs.
* `app/identities/principal/[key]/[[...view]]/page.tsx` renders one relationship view and
  runs only that view's query.
* `principalViews(principal)` (`lib/identity.ts`) is the typed list of views a principal
  offers: groups get member views, accounts do not, unknown kinds get all of them.
* `data.ts` wraps the identity and collection-state reads in React `cache`, so a layout and a
  page rendered in one request share one read. `currentViewer` is cached the same way.
* `app/identities/principal/page.tsx` keeps the old address: `?key=&tab=&p=&include=` redirects
  to the key's path. `?host=` is resolved by the API to the storage key first.

Measured locally (API request log, viewer, Bianca Rossi):

| Move | API calls |
| --- | --- |
| switch view (each of 5) | 3: `/auth/me`, `/collection/status`, the view's own query. The identity is not read again. |
| fresh load of `/identities/principal/{sid}` | 6: `/auth/config`, `/runtime`, `/auth/me` once (shell, layout and page share it), the identity once, status, the view |
| chain-node click to another principal | 4: `/auth/me`, status, the new identity, its default view; no shell data |

Repeated switching does not accumulate calls (a revisit within the router cache made none).

### Identity information disclosure

A native `<details open>` with the heading in its `<summary>`. The browser supplies keyboard
operation (Enter, Space) and the expanded state assistive technology reads. Because it lives in
the layout, a view change does not render it again, so a folded section stays folded. Opening
another principal renders a new layout, which starts open.

### Users and Groups directories

`/identities/users` and `/identities/groups` (`components/PrincipalDirectory.tsx`), beside
Servers (`/resources`) under one directory strip (`components/DirectoryTabs.tsx`, the existing
`Tabs`). The data comes from `GET /api/v1/principals`:

* `kind=users` (user, managed service account) or `kind=groups` (domain and local groups);
  behind `identities:read` with the rest of the graph router.
* Keyset pages ordered by storage key (50 per page in the UI, the API's 100/500 limits),
  exact `total`, and per-row direct member and direct group counts as correlated index counts.
  Effective counts are not listed: each would be a bounded graph traversal per row.
* `q`: a term starting `S-1-` is a SID prefix; anything else is the start of a display name,
  account name, UPN or last known name, case-insensitive; `DOMAIN\name` matches the account name.
  LIKE wildcards are escaped. Prefix only, as global search is.
* The search box is a `next/form` GET form: a search is a client-side navigation, and the term
  and page position stay in the URL.

Selecting a row opens the principal through the same layout. The history (as-of) repository
leaves the two reads current-state, and the simulation overlay refuses them (both registries
updated, both guard tests pass).

### Sortable tables

`components/SortableTable.tsx` with `lib/sort.ts`. Server components pass rendered cells plus
one typed sort value per column. Text compares case-insensitively, numbers numerically (no
string sorting), dates chronologically. Booleans sort false before true, and blanks go last in
both directions. Ties keep the server's order. Each sortable header is a `<button>` inside a
`<th aria-sort>`: the first activation ascends, the next descends. Sorting is component state:
no reload, no request. Captions say that sorting orders the rows on the page in hand, never
the estate.

Converted: Servers, Users, Groups, direct members, effective members, parent/direct groups,
effective groups, reachable shares, reachable directories, share ACL entries (named on share
ACLs), NTFS ACL entries, who reaches a directory, aliases, server shares, search results,
collectors (3), risk findings, alerts (2), watches, simulations (3), governance (6), review
items, explanation (4), roles (2), status. Severity and confidence sort by rank, not
alphabetically.

Deliberately unsorted, because the rows are fields of one object in a set order: the
before/after tables in Changes (2), change impact, a review item's access comparison, the
collectors "What is stored" counts, and the demo guide's scan history. The order of that last
one is its meaning.

## 3. Ponytail (the lazy-reuse rules)

The repository has no Ponytail component library. Applied as its rules: reuse what the
codebase has, then the platform, and add nothing nobody asked for.

| Need | Used |
| --- | --- |
| Directory switcher, view tabs | the existing `Tabs` (links, `aria-current`) |
| Pagination | the existing `Pager` / `pageNavigation` trail |
| Empty, error and coverage states | the existing `classify` / `StateMessage` / `CoverageCaveat` |
| Principal cells, ambiguity qualifiers | the existing `PrincipalName` / `ambiguousLabels` |
| Disclosure | native `<details>/<summary>` |
| Search form | `next/form` (Next.js built-in) |
| Client-side routes, history, deep links | Next.js App Router layouts and `next/link` |
| Per-request de-duplication | React `cache` |

New UI code, each small: `SortableTable` (no sortable table existed; one shared primitive for
all 40 list tables), `PrincipalChain`, `PrincipalViewTabs` (reads the current view from the
path, because the layout does not re-render), `DirectoryTabs` (data for `Tabs`), and
`PrincipalDirectory`. No dependency was added.

## 4. Edge / SSR compatibility

Nothing here assumes where Next.js runs or depends on Cloudflare. The new routes are ordinary
dynamic App Router pages, and the new API path is under `/api/v1/`, which both the thin Worker
and the Render gate send to FastAPI. Client behaviour (soft navigation, history, deep links,
sorting, the disclosure) is the browser's and Next.js's. It was validated on `next start`
locally and, after integration, on the deployed edge/origin topology (integration report).

## 5. Local validation (task branch)

| Gate | Result |
| --- | --- |
| backend `ruff check` / `ruff format --check` / `mypy app tests` | clean / 400 files formatted / no issues in 399 files |
| backend hermetic suite (`pytest -m "not smoke"`) | 5,889 passed, 12 skipped |
| backend DB tests touched (graph, access, query cost, contracts, authorization) | 821 + 62 passed |
| frontend `eslint` / `tsc` | clean / clean |
| frontend `vitest` | 39 files, 1,260 passed |
| frontend `next build` | ok |
| `navigation.mjs` against local `next start` + API + Null Meridian (`adg_demo`, fingerprint `228b9dce…f9efd0`) | 18/18, 0 console errors |
| `browser.mjs` (existing 22-check gate) locally | every functional check passes. The three checks that call API paths on the web origin (`/runtime`, `POST /api/v1/scan-runs`, the runtime header) cannot pass without the Worker or gate in front, as expected. React #418 appeared in 3 of 10 runs (§6). |

New tests: `sort.test.ts`, `sortable-table.test.tsx`, `directory.test.tsx`, identity helpers,
principal chains and navigation, the disclosure, view switching without an identity read, an
invalid view, both legacy redirects, a numeric sort on a collectors table. Backend: chain
labels in order, an undescribed hop as its SID, a shared hop named twice, the directory's
kinds, degree, name/SID/`DOMAIN\name`/wildcard search and keyset paging, `path_principals` on
`via`, the directory in the N+1 statement guard, and the route in the authorization matrix.

## 6. Limitations

* **React #418, pre-existing** (ADG-FREE-EDGE-SSR-03 §11): reproduced on local `next start`
  in 3 of 10 full `browser.mjs` runs. Not introduced by this work. Its cause was later found in
  the React that Next.js 15.5.27 bundles (a hydration replay that does not restore its cursor;
  `adg-ui-edge-01.md` §5), and it does not reproduce on Next.js 16.4.0
  (`adg-next16-react418-01.md`, staging only).
* Sorting orders the page in hand. The directories page by key, and a reader who wants every
  user by name searches by a name prefix.
* The demo dataset has no unresolved hop and no computer account in any membership chain
  (all 238 groups scanned). Those two cases are covered by unit and DB tests, not hosted.
* The directories match prefixes, as global search does: an infix (`Smith` in `Ada Smith`) does
  not match.

## 7. Addendum (2026-10-13): Exclude local groups, compact identity, Shortest Chains

Continues this task on the integrated tree, not on the task branch: branch
`fix/ADG-UI-COMPACT-IDENTITY-01` from `6b69fe3` (the deployed integration of this work with
ADG-FREE-EDGE-SSR-03), worktree `../adg-UI-COMPACT-IDENTITY-01`. `fix/ADG-UI-UPDATE-01` stays at
`a5cdf88` so it does not take in the EDGE-SSR history; the EDGE-SSR worktree was not touched.
No file here is also changed by the parallel Next.js 16 upgrade (`fix/ADG-NEXT16-REACT418-01`
changes only `frontend/package*.json`, `eslint.config.mjs` and `tsconfig.json`).

| Commit | What |
| --- | --- |
| `4f33e08` | Compact identity: name-only Name cells, the `Identifier` treatment for SIDs, keys and paths, Shortest Chains labels (a subagent's work, reviewed). |
| `98a9a79` | Exclude local groups (API, UI, tests, browser check); one-line directory rows. |

### Exclude local groups

* **Semantics, from the data model, never from names.** A machine-local group is
  `PrincipalKind.LOCAL_GROUP`: it exists on one computer and its key is host-scoped
  (`principal|<host>|<sid>`, so `BUILTIN\Administrators` on FS01 and on FS02 are two groups).
  An Active Directory **Domain Local** group is `domain_group` with `group_scope: domain_local`:
  a domain group whose scope says where it can be used. The filter removes the first kind only.
  Null Meridian: 238 groups, 33 local (an `Administrators` on each server), 130 global,
  37 Domain Local; the filter leaves 205.
* **Server side.** `GET /api/v1/principals?kind=groups&exclude_local=true` drops `LOCAL_GROUP`
  from the kinds the repository lists and counts, so the search, the keyset pages and
  `page.total` all see the filter. Default `false`.
* **UI** (`components/FilterToggle.tsx`, `PrincipalDirectory.tsx`). No toggle component existed;
  this is a native, labelled checkbox, off by default, tied by id (`form=`) to the directory's
  `next/form` GET search form. A change submits that form: a client-side navigation, no document
  reload, the search term kept, the page position reset (a page of the unfiltered list is not a
  page of the filtered one). The URL carries `exclude_local=1`; the pager and a new search keep
  it; Back and Forward restore the box. The table stays mounted, so the reader's column sort
  survives a filter change. Without JavaScript the Search button applies it. A short note
  beside it says what a local group is and that Domain Local groups stay.

### Compact identity and Shortest Chains

* Name cells hold the name alone; the SID no longer stacks under it. The link opens by storage
  key and carries it as `title`; Identity information shows SID and key in full.
* Users and Groups directories and search results keep a dedicated SID / Identifier column
  (search matches SID prefixes; pages follow SID order). Relationship tables (direct and
  effective groups and members, ACLs, access, explanation trustees) drop it.
* `Identifier` / `.identifier`: one line, at most 16rem, ellipsis, the whole value in the text
  (read by a screen reader, taken by a copy) and in `title`, shown in full while keyboard focus
  is in the row. Used for SIDs, storage and ACE keys, UNC and local paths, risk subjects.
* Code values in table cells (access masks, SIDs) never break mid-value; the Groups directory's
  short label columns (Account name, Kind, Scope) stay on one line.
* Shortest Chains: a resolved hop is its name as a link (key as `title`); an unresolved hop shows
  its SID or last known name as truncated text with the full key in `title`.
* Kept two-line on purpose: status notes for unresolved, disabled or deleted principals (a
  warning, not metadata), and the Risks "Where" cell (subject, then "for <principal>"; findings
  carry keys only).

### Validation

| Gate | Result |
| --- | --- |
| backend DB tests, principal directory + exclude local (`ADG_RUN_SMOKE_TESTS=1`, Python 3.13) | 22 passed (9 new) |
| OpenAPI snapshot | regenerated (`exclude_local` only); test passes |
| backend `ruff check` / `ruff format --check` / `mypy app tests` | clean / 400 files formatted / no issues in 399 files |
| frontend `eslint` / `tsc` | clean / clean |
| frontend `vitest` | 39 files, 1,306 passed (8 new for the filter, 3 for compact identity) |
| frontend `next build` | ok |
| `navigation.mjs` (19 checks, 1 new) against local `next start` + API + Null Meridian (`adg_demo`, read only) | 19/19 three times, 0 console errors |
| `browser.mjs` locally | 19/22: the same three checks that need the Worker or gate in front (§5) |
| row height, Groups directory (1360 × 900) | 62.6 px → 39.8 px, every row |

The new tests: off by default, on, off again; the API filters by kind (Domain Local kept,
machine-local dropped); search + filter; paging + filter (`total` and the walk); sort kept across
a filter change; no document reload (the change is a form submission next/form handles, and the
browser check's window marker survives); a labelled checkbox that Space toggles; Back/Forward;
not offered for users. Mutation check: removing the submit or the API parameter fails 3 tests.

### Acceptance

- [x] Groups has an `Exclude local groups` toggle.
- [x] Correct local-group semantics are used (`PrincipalKind.LOCAL_GROUP`, host-scoped).
- [x] AD Domain Local groups are not filtered.
- [x] Filter works with search, sorting and pagination (applied in the query, not on the page).
- [x] Filter changes do not reload the page.
- [x] Object names are no longer rendered with SIDs stacked underneath.
- [x] Long SIDs no longer create tall or wide cells.
- [x] Dedicated SID presentation is compact and accessible.
- [x] Resolved Shortest Chains show linked names without SID text.
- [x] Tests and this report are updated.

### Open

* Not deployed; validated locally against the hosted database, read only. *Deployed on
  Next.js 16 on 2026-10-13: `adg-next16-ui-integration-02.md`.*
* An end ellipsis hides a SID's distinguishing tail (the RID): domain SIDs in one column share
  their prefix. Truncating the start instead is a possible follow-up. *Done for SIDs in
  `adg-next16-ui-integration-02.md` §3; paths and keys keep the end ellipsis.*
* At 1360 px the Groups directory (7 columns) is wider than the content area and scrolls inside
  its own container; no single column is the cause any more.
* The principal Shares view keeps two pre-existing notes per row (`assumed_token_sids` under the
  share name, "Measured against the ACLs ADG holds." under the verdict): three lines a row.
  They are not identity metadata and were left as they are.
