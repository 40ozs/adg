# ADG-UI-RECONCILE-01 — port matrix

Written before any edit. Base: `main` @ `1687691`. Every historical commit below forks from
`1ab01a2` (before ADG-DEMO-01), so none of them has seen DEMO-01/02/03, Next.js 16.4.1, the
contract 1.1 bump, or the hosted stack.

Method: each commit's own diff (`git show <c>`, its parent as base) was 3-way merged onto `main`
in sequence (`git merge-tree --merge-base <c>^ <acc> <c>`, chained). Textual conflicts: only
`frontend/app/identities/principal/page.tsx` (552bcb8) and the handoff doc (f50e8f2). Every
file the commits touch that `main` also changed since `1ab01a2` was then read by hand (below).

Classes: `PORT_AS_IS`, `PORT_WITH_CURRENT_MAIN_ADAPTATION`, `ALREADY_SUPERSEDED`,
`NO_LONGER_REQUIRED`, `DO_NOT_PORT`.

## 61fedb8 — API: `path_principals`, `GET /api/v1/principals`

| Change | Class | Note |
| --- | --- | --- |
| `EffectiveMemberView.path_principals` (`api/graph.py`), labelled from the traversal's nodes | PORT_AS_IS | `graph.py`, `membership.py` unchanged on main since `1ab01a2`. |
| `TokenEntryView.path_principals` (`api/access.py`, `_token_entry_view`) | PORT_WITH_CURRENT_MAIN_ADAPTATION | `access.py` gained DEMO-02's `investigation` / `evidence` (+327). The field is additive and its labels come from the same `labels` map DEMO-02's `_investigation_view` uses. **Collision: contract.** `TokenEntryView` is inside two derived schemas (access-explanation, resource-impact), so their shape changes. The repository's rule (`caching.CONTRACT_VERSION` docstring, `docs/contracts/README.md` §Versioning, precedent `60bae4b` 1.0→1.1) is a minor bump: **1.1 → 1.2**. The old lane skipped it (it stayed 1.0); that is not repeated. |
| `resource_principals` widens its one label read to the chain keys (`services/access.py`) | PORT_WITH_CURRENT_MAIN_ADAPTATION | main changed the same file (DEMO-01 `_could_contain_members`, `SubjectFacts.last_observed_*`) in other hunks; merges cleanly; still one statement. |
| `list_principals` / `count_principals` (`repositories/membership.py`) + route | PORT_AS_IS | Keyset by key, exact total, prefix/SID/`DOMAIN\name`, correlated index counts (no traversal per row). |
| authorization matrix, graph/access DB tests, N+1 guard | PORT_AS_IS | |
| `openapi.json`, derived schemas | PORT_WITH_CURRENT_MAIN_ADAPTATION | Regenerated from current code, never merged by hand. Examples regenerated with the 1.2 bump. |

## 552bcb8 — UI: directories, principal layout/view split, chains, SortableTable

| Change | Class | Note |
| --- | --- | --- |
| `/identities/users`, `/identities/groups`, `PrincipalDirectory`, `DirectoryTabs`, `/resources` strip | PORT_AS_IS | New files; `/resources/page.tsx` unchanged on main. |
| `app/identities/principal/[key]/layout.tsx` + `[[...view]]/page.tsx` + `data.ts` (React `cache`) | PORT_WITH_CURRENT_MAIN_ADAPTATION | **Collision: DEMO-01.** main's single page gained the `isUnresolved` banner ("A run saw this SID and could not resolve it") and imports `isUnresolved`. That banner moves into the layout beside the existing "No run has described this SID" banner. Next 16: `params`/`searchParams` are Promises (already so on the old line, which ran on 15.5 async APIs); verified under 16.4.1 build. |
| `app/identities/principal/page.tsx` → legacy `?key=&tab=&host=` redirect | PORT_WITH_CURRENT_MAIN_ADAPTATION | The one textual conflict. Resolution: take the redirect; the DEMO-01 content it drops is carried by the layout (above). |
| `PrincipalChain` (`components/Identity.tsx`) | PORT_WITH_CURRENT_MAIN_ADAPTATION | **Collision: DEMO-01.** Old rule linked any `resolved` hop. A record of kind `unresolved` is `resolved: true` on the wire; DEMO-01 made `isUnresolved` the test. The chain uses `isUnresolved`, so such a hop stays text, unlinked, labelled with what the record holds (never invented). |
| `principalViews`, `principalPath`, chain helpers (`lib/identity.ts`) | PORT_WITH_CURRENT_MAIN_ADAPTATION | merges beside DEMO-01's `isUnresolved`. |
| Chains in EffectiveAccess, ExplanationExplorer `PathTable`, Simulation retained routes | PORT_AS_IS | `ExplanationExplorer`, `Simulation`, `EffectiveAccess` unchanged on main. |
| `SortableTable` + `lib/sort.ts` | PORT_AS_IS | New. |
| `RawAcl`, Servers table on SortableTable | PORT_AS_IS | unchanged on main. |
| `prefetch={false}` on principal links; `currentViewer` in React `cache` (`lib/auth/current.ts`) | PORT_AS_IS | `current.ts` unchanged on main. Test `next/link` mocks drop `prefetch`. |
| `lib/api/adg.ts`, `lib/contracts.ts` additions | PORT_WITH_CURRENT_MAIN_ADAPTATION | main added DEMO-02/03 types/fetchers in other hunks; merges cleanly. |
| test harness edits in 14 page tests (`next/link` mock, `cache`) | PORT_WITH_CURRENT_MAIN_ADAPTATION | `demo-guide`, `risks-page`, `contracts` tests changed on main; merged, re-run. |
| `directory.test.tsx`, `sort.test.ts`, `sortable-table.test.tsx`, `identity`/`pages`/`views` additions | PORT_AS_IS | |

## 371b90e — `frontend/cloudflare/checks/navigation.mjs`

| Change | Class | Note |
| --- | --- | --- |
| Browser gate for directories, chains, views, disclosure, legacy URL | PORT_WITH_CURRENT_MAIN_ADAPTATION | A check script, not deploy config. Run locally against `next start` behind `render/gate.ts` (the current topology). Chromium path made an env override if hard-coded. |

## 861a11b — repository registries

| Change | Class | Note |
| --- | --- | --- |
| `list_principals` / `count_principals` named current-state in history, refused in the simulation overlay; guard tests | PORT_AS_IS | Registries unchanged on main. |

## a5cdf88 — every list table on SortableTable

| Change | Class | Note |
| --- | --- | --- |
| Collectors, server shares, search, alerts, risks, watches, simulations, governance, decision form, explanation tables, settings/login roles, status | PORT_AS_IS | None of these files changed on main except `Explanation.tsx` (DEMO-01 SYSTEM mapping row, `#removals` anchor), which merges in other hunks. |
| principal aliases table (`[key]/layout.tsx`) | PORT_AS_IS (rides on 552bcb8) | |
| DEMO-02/03 tables added on main after the fork (`Investigation`, `Coverage`, `Comparison`, `changes/compare`) | NO_LONGER_REQUIRED / not converted | Not in the old conversion list. Investigation and Coverage render lists/`<details>` rather than list tables; Comparison / compare are before/after field comparisons (semantic order, the old lane's own "leave unsorted" rule). Converting them would overwrite newer behaviour. |
| DemoGuide scan history | DO_NOT_PORT (unsorted by design) | Its order is the baseline; old lane left it unsorted too. |
| Changes before/after, change impact, review-item access comparison, collectors "What is stored" | DO_NOT_PORT (unsorted by design) | Rows are fields of one object. |

## 4f33e08 — compact identity

| Change | Class | Note |
| --- | --- | --- |
| Name-only Name cells; `Identifier` / `.identifier`; Facts `Identifier`; tables on it | PORT_WITH_CURRENT_MAIN_ADAPTATION | `Identity.tsx` changed on main (DEMO-01: notes use `isUnresolved`). The unresolved/disabled/deleted status note stays visible under the name (the old lane kept it on purpose; DEMO-01 depends on it). Ambiguity qualifier unchanged. |
| Investigation route chains (DEMO-02) | ALREADY_SUPERSEDED | Already names (`display_name ?? sid`), inside a `<summary>` where links would be interactive content in a toggle. Left as is. |

## 98a9a79 — Exclude local groups

| Change | Class | Note |
| --- | --- | --- |
| `exclude_local` on `GET /api/v1/principals` (drops `PrincipalKind.LOCAL_GROUP` from listed and counted kinds) | PORT_AS_IS | Domain Local (`domain_group`, `group_scope=domain_local`) kept. |
| `FilterToggle`, `PrincipalDirectory` wiring (form=, reset page, URL `exclude_local=1`) | PORT_AS_IS | |
| one-line directory rows CSS | PORT_AS_IS | |
| navigation.mjs check, DB tests, directory tests | PORT_AS_IS | |

## f50e8f2 — test typing

| Change | Class | Note |
| --- | --- | --- |
| `test_graph_api.py` JSON body typing | PORT_AS_IS | |
| `docs/handoffs/adg-ui-update-01.md` | DO_NOT_PORT | The handoff is historical (it describes the 643da21/6b69fe3 trees). This lane's report supersedes it; the old text is cited by SHA. |

## 33cca01 — SIDs cut at the start (combined line `integration/ADG-NEXT16-UI-02`, not in the task list)

| Change | Class | Note |
| --- | --- | --- |
| `Sid` (`direction: rtl` ellipsis), used for SID columns and SID-only labels | PORT_AS_IS | Closes the compact-identity handoff's own open item ("an end ellipsis hides a SID's RID"), validated on the combined line. |

## Not ported from any historical branch

| Item | Class |
| --- | --- |
| Deploy/staging commits on the UI line (`cedca60` … `57bbbaa`, `6b69fe3`), wrangler/Render config | DO_NOT_PORT (main's hosted stack is authoritative) |
| `66ca201` Next 16.4.0, package manifests, eslint/tsconfig from the UI branches | DO_NOT_PORT (main is on 16.4.1 / React 19.3) |
| Handoffs `adg-ui-edge-01.md`, `adg-next16-ui-integration-02.md` | DO_NOT_PORT |
| ADG-DIRECTORY-ACCESS-01 (`release/ADG-UI-EDGE-DIRECTORY-02`) | DO_NOT_PORT (separate task) |
| GRAPH (second `0018`) | DO_NOT_PORT |

## Current-main fixes in scope

| Item | Plan |
| --- | --- |
| `/risks` severity counts: `<ul class="filter-strip">` keeps default list markers | CSS: `ul.filter-strip { list-style: none; padding-left: 0 }` |
| Deleted state: banner "(deleted)" vs facts "Deleted: no" | Source of truth: `unresolved_reason = deleted` is the collector's report that the account was deleted (`UnresolvedReason.DELETED`); `is_deleted` is a directory fact only users/groups carry, so an unresolved record's `false` is a column default, not an observation. The risk engine already reads both as "deleted" (`risk_engine/rules.py`). Presentation only: one helper derives the facts row from both, nothing stored changes. |
