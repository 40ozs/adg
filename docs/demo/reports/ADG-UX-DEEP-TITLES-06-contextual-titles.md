# ADG-UX-DEEP-TITLES-06 — Contextual titles for deeper pages

**Verdict: `READY_FOR_ADG_UX_DEEP_TITLES_INTEGRATION_WITH_LIMITATIONS`** (limitations: §10).

TITLES-03 gave every section a title, but its sub-pages still showed only the section name.
Twelve deeper pages now say where the user is. Examples: `Alerts — ADG`, `Access investigation — ADG`,
`History — ADG`, `Review campaign — ADG` and `What-if proposal — ADG`. Each title repeats the page's own visible heading.
Eleven of the titles are static metadata. One, the History view of `/changes`, is derived from the URL query
alone. No title adds a backend request: the before/after recording shows 0 extra calls over 28
measurements. No title carries a SID, GUID, distinguished name, UNC or drive path, or object name.
The TITLES-03 architecture is unchanged: the same `documentTitle` helper, whole-string titles,
and the same test table. Routes, headings, navigation, backend and contracts are untouched.
Nothing was merged or deployed.

## 1. Record

```
BRANCH=feature/ADG-UX-DEEP-TITLES-06-contextual-titles
WORKTREE=/Volumes/T5 EVO/mmm/worktrees/adg-UX-DEEP-TITLES-06
BASE_SHA=10c73d1fcb5b12b31fa54e57b95da3483c079fba   (origin/integration/ADG-UX-WAVE-B-01, fetched 2026-10-30)
CODE_SHA=014783a   (feat(ui): contextual titles for deeper pages)
FINAL_SHA=the commit that adds this report (docs only, on top of the code commit)
```

**Preflight.** `10c73d1` was the head of `origin/integration/ADG-UX-WAVE-B-01`. The Wave B
report names it as the next UX baseline. No newer validated UX baseline existed on
`origin`: the only later-looking branches were the Wave B source lanes, which `10c73d1` already
contains. Before implementing, I read the continuity reports
[`ADG-UX-TITLES-03-document-titles.md`](ADG-UX-TITLES-03-document-titles.md) and
[`ADG-UX-WAVE-B-INTEGRATION-01.md`](ADG-UX-WAVE-B-INTEGRATION-01.md).

## 2. Design

- **Kept from TITLES-03:** `documentTitle(name)` / `sectionTitle(id)` in `lib/nav.ts`, whole
  string titles (no `title.template`), the section layouts, the root fallback and the not-found
  page.
- **Static pages:** each improved page exports
  `export const metadata: Metadata = { title: documentTitle("…") }`, the same one-liner TITLES-03
  put on `login`, `status`, `search` and `demo`. A page's own title overrides its section
  layout's, so the section layouts stay as they are.
- **One URL-derived title.** `/changes?kind=…&key=…` renders a different page from the
  feed: one object's history, with the heading `History`. It is the same route, so a static
  title cannot tell them apart. `app/changes/page.tsx` exports a `generateMetadata` that reads
  only `searchParams`, uses the same `kind` + `key` test as the page, and returns either
  `History — ADG` or the section title. It does no fetch, and the key never reaches the title.
  The unit test calls it with `fetch` spied and asserts no call.
- **Placement:** on direct load the History `<title>` is in `<head>`, not streamed into
  `<body>`, and there is exactly one per page. On client navigation it arrives in the
  RSC payload the move fetches anyway (§6).
- No client-side `document.title` code. No new helper, layout or dependency.

## 3. Route and title inventory

Classes: **1** top-level title already sufficient · **2** inherited title was ambiguous ·
**3** contextual static title added · **4** dynamic title without an extra read · **5** stays
generic for privacy or because it is a tab or overlay, not a document.

| Route | Before | After | Class |
| --- | --- | --- | --- |
| `/` | Overview — ADG | unchanged | 1 |
| `/risks` (incl. filters) | Findings — ADG | unchanged | 1 |
| `/risks?finding=…` (finding drawer) | Findings — ADG | unchanged | 5: overlay on the list, not a separate page |
| `/risks/alerts` (incl. `?alert=` drawer) | Findings — ADG | **Alerts — ADG** | 2→3 |
| `/access` | Access — ADG | unchanged | 1 |
| `/access/investigate` | Access — ADG | **Access investigation — ADG** | 2→3 |
| `/access/explain` | Access — ADG | **Access explanation — ADG** | 2→3 |
| `/changes` (feed, incl. filters) | Changes — ADG | unchanged (now from `generateMetadata`) | 1 |
| `/changes?kind=…&key=…` (one object's history) | Changes — ADG | **History — ADG** | 2→4 |
| `/changes/compare` | Changes — ADG | **Compare two points in time — ADG** | 2→3 |
| `/changes/impact` | Changes — ADG | **Why access changed — ADG** | 2→3 |
| `/identities` | Identities — ADG | unchanged | 1 |
| `/identities/users` | Identities — ADG | **Users — ADG** | 2→3 |
| `/identities/groups` | Identities — ADG | **Groups — ADG** | 2→3 |
| `/identities/principal` (legacy `?key=` lookup) | Identity — ADG | unchanged | 1 |
| `/identities/principal/{key}/{view?}` | Identity — ADG | unchanged | 5 (see §4) |
| `/resources` | Resources — ADG | unchanged | 1 |
| `/resources/server`, `/share`, `/directory` | Server / Share / Directory — ADG | unchanged | 5 (TITLES-03 §4) |
| `/governance` | Reviews — ADG | unchanged | 1 |
| `/governance/campaigns/{id}` | Reviews — ADG | **Review campaign — ADG** | 2→3 |
| `/governance/items/{id}` | Reviews — ADG | **Review item — ADG** | 2→3 |
| `/simulations` | What-if — ADG | unchanged | 1 |
| `/simulations/new` | What-if — ADG | **Propose a change — ADG** | 2→3 |
| `/simulations/{id}` | What-if — ADG | **What-if proposal — ADG** | 2→3 |
| `/collectors`, `/settings`, `/login`, `/status`, `/search`, `/demo` | as TITLES-03 | unchanged | 1 |
| unknown path | Page not found — ADG (404) | unchanged | 1 |

That makes 30 page routes audited, plus 3 query or overlay views (finding drawer, alert drawer,
object history). **12 titles were added or changed: 11 static and 1 URL-derived.**

**Wording.** Every new title repeats the page's own visible heading or, for a detail page,
its fallback heading: `Alerts`, `Access investigation`, `Access explanation`, `History`,
`Compare two points in time`, `Why access changed`, `Users`, `Groups`, `Review item`,
`Propose a change` and `What-if proposal`. The campaign page's heading is the campaign
name, which a title must not carry. Its fallback heading is `Campaign`, and the title says
`Review campaign` so that it stands alone in a history list. The UX-01 vocabulary holds: no
title says Risk or Principal (the unit test asserts no title contains `Risk`). The `/risks/…`
routes are unchanged.

## 4. Pages intentionally left generic

- **Identity views** (`/identities/principal/{key}/members|groups|effective-groups|shares|…`).
  These are tabs (`PrincipalViewTabs`) inside one identity's page. The header and identity
  stay put, and only the panel changes. The brief says not to invent separate titles for
  tabs. The labels would also need data: the same `groups` view is "Parent groups" for a
  group and "Direct groups" for a user, which is only known after reading the identity, and
  the bare path (no view) resolves its view the same way. The title stays `Identity — ADG`.
- **Object-specific titles** (`Jane Smith — Identity`, a campaign name, a proposal name,
  `FS01`). The privacy and read-cost reasoning in TITLES-03 §4–§5 still holds. On a client move
  between identity views, a named title would also add an identity read. All titles stay
  generic.
- **Finding and alert drawers** (`?finding=`, `?alert=`). These are overlays on the list, so
  the title stays the list's title. Opening a finding does not change where the user is.
- **Filters** never change a title (TITLES-03 rule): `/risks?severity=critical` and
  `/changes?days=90` keep their section titles.

## 5. Sensitive data

- **The 11 static titles** are build-time literals, so they cannot carry data.
- **The History title** comes from the query, but only its *presence* matters: both values
  must exist. Neither value is copied into the title. In this dataset `key` is an ACE key
  that holds a UNC path and a SID (`\\gp-sec-fs01\…|S-1-5-21-…|allow|…`), and the title is
  still `History — ADG`. The browser gate checks every title it sees against
  `S-1-…`, GUIDs, backslashes, `X:/`, `principal:` and `//`.
- **Tests that hold the line:**
  - `generateMetadata` exists only in `app/changes/page.tsx`. Every other route module must not
    export one, so a second dynamic title fails the test until someone reviews it.
  - The History test passes a SID key and spies on `fetch`. It expects `History — ADG` and no
    fetch call.
  - "puts no identifier or path in any title" scans the whole table for SIDs, backslashes,
    `/` or `:`, GUID fragments and the word `principal`.
- **Not a title, but seen during the audit:** the access-explanation diagram has inline SVG
  `<title>`s that carry SIDs and a UNC path (for example `SEC-Share-Restricted — identity
  (S-1-5-21-…)`). These are tooltips inside the page, not the document title, and they appear
  in the same form at the baseline (see `request-count.json`). Out of scope here. They are
  recorded only so a later audit does not misread them as document titles.

## 6. Backend request count

[`request-count.mjs`](ADG-UX-DEEP-TITLES-06-evidence/request-count.mjs) put a recording
proxy in front of the real API for each of two production builds. The baseline was the Wave B
standalone build of `10c73d1` (`adg-UX-WAVE-B-01`, :3031 → proxy :8131). The other was this lane's
build (:3030 → proxy :8130). Each of the
[14 deeper pages](ADG-UX-DEEP-TITLES-06-evidence/request-count.paths.json) was requested from
both builds in two ways. The first was a **document** request, as on a direct load. The second
was an **RSC payload**, the request a client-side move makes (Next 16 redirects a bare RSC
request to `?_rsc`, and the script follows it; every response was HTTP 200). Each request
was made once to warm caches and then measured. The API calls were compared as a multiset of
method + path + sorted query keys.

**Result: 28 measurements, additional backend requests: 0.** Every page made exactly the same
calls before and after. The title changed on the 12 improved pages and on no other page.
Full output: [`request-count.txt`](ADG-UX-DEEP-TITLES-06-evidence/request-count.txt),
[`request-count.json`](ADG-UX-DEEP-TITLES-06-evidence/request-count.json).

| Page | API calls (doc / RSC), before → after | Title before → after |
| --- | --- | --- |
| `/risks/alerts` | 6/6 → 6/6 | Findings → Alerts |
| `/access/investigate?…` | 6/6 → 6/6 | Access → Access investigation |
| `/access/explain?…` | 5/5 → 5/5 | Access → Access explanation |
| `/changes?kind=…&key=…` | 4/4 → 4/4 | Changes → History |
| `/changes/compare` | 3/3 → 3/3 | Changes → Compare two points in time |
| `/changes/impact?…` | 4/4 → 4/4 | Changes → Why access changed |
| `/identities/users` | 5/5 → 5/5 | Identities → Users |
| `/identities/groups` | 5/5 → 5/5 | Identities → Groups |
| `/governance/campaigns/{id}` | 6/6 → 6/6 | Reviews → Review campaign |
| `/governance/items/{id}` | 5/5 → 5/5 | Reviews → Review item |
| `/simulations/new` | 4/4 → 4/4 | What-if → Propose a change |
| `/simulations/{id}` | 5/5 → 5/5 | What-if → What-if proposal |
| `/risks`, `/changes?days=90` (controls) | 6/6, 5/5 → same | unchanged |

## 7. Browser results

I extended the committed gate
[`frontend/cloudflare/checks/titles.mjs`](../../../frontend/cloudflare/checks/titles.mjs) rather
than adding a second one. It kept its 10 TITLES-03 checks and gained 5 deeper-page checks. It
ran against this lane's production build (:3032) on the **real local API** (Genesee Press
demo dataset), signed in as `admin`, in Chrome 156 headless at 1360×900. Result: **15/15 PASS**
([`titles-gate.txt`](ADG-UX-DEEP-TITLES-06-evidence/titles-gate.txt)).

- **Journey, 21 client-side moves in one document.** Overview → Findings → Alerts →
  Changes → 90 days → **History** → Back to changes → 90 days → **Why access changed** →
  Changes → **Compare two points in time** → Identities → **Users** → **Groups** → Identity
  → Reviews → **Review campaign** → **Review item** → What-if → **What-if proposal** →
  What-if → **Propose a change**. Every move clicks a link the page itself offers. At each
  step the gate checks three things: the title, the current nav item, and that the title has
  no identifier. No move reloaded the document, except the one known full load below.
- **Back:** all 21 steps back to Overview, with each title restored. **Forward:** all 21
  steps, with each title restored.
- **Direct load:** 18 pages each sent exactly one server `<title>` that matched. These were the
  journey's pages plus Access investigation and Access explanation, loaded with a dataset SID.
  History keeps its title through a hard reload.
- **Error and invalid states:**
  - An unknown campaign, review item or proposal id keeps its contextual title (`Review
    campaign`, `Review item`, `What-if proposal`), matching the page's fallback heading.
  - A bad identity view keeps `Identity — ADG`.
  - All four answer HTTP 200 today. That is TITLES-03 §9.3 behaviour and out of scope here.
  - An unknown path is still a 404 titled `Page not found — ADG`.
- **Hydration:** 0 page errors. There were 2 console errors, none about hydration, the same
  count as the Wave B baseline run.
- **Known full load (pre-existing):** the campaign's item list links to an item with a
  plain `<a>` (`components/DecisionForm.tsx`, since `ee0a856`), so that one move loads a new
  document. The gate declares it (`fullLoad`) and still checks its title. It also exempts the
  document loads Back/Forward make across that move, and fails on any other reload. The
  link was not changed: navigation changes are out of scope.

**Accessibility (not a formal audit):** titles change on every client move. Materially
different pages within a section no longer share one title. Each title uses the page's own
heading words, and no title uses Risk or Principal.

## 8. Tests and gates

`tests/titles.test.ts` is extended. It is the same table and the same resolver, so it still fails
when a new page has no entry:

- 11 table rows changed to the new titles;
- "never reads data" now allows `generateMetadata` in `app/changes/page.tsx` only;
- new: History title from the query, with a SID key, `fetch` spied and no fetch call;
- new: no identifier or path in any title;
- the Findings rule tightened from `Risks` to `Risk`.

Gates (Node 22.23.3 arm64, Next 16.4.1), summary in
[`frontend-gates.txt`](ADG-UX-DEEP-TITLES-06-evidence/frontend-gates.txt):

| Gate | Result |
| --- | --- |
| `npm run lint` | pass (re-run after the gate script edit: pass) |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass (re-run after the gate script edit: pass) |
| `npm test` | **1596 passed** (46 files; Wave B baseline 1594 + 2; titles 38) |
| `ADG_NEXT_OUTPUT=standalone npm run build` (`next build --webpack`) | pass |
| `titles.mjs` (real API) | **15/15** |
| `request-count.mjs` | **0** additional backend requests (28 measurements) |

Backend: no file changed, so no backend gate was re-run.

## 9. Environment

| | |
| --- | --- |
| Database | Throwaway Postgres 16.2 (pgserver binaries) on 127.0.0.1:55467: a copy of the Wave B lane's cleanly shut-down `adg_demo` (fingerprint `9c8177e2…`, which includes the review campaign the Wave B overview gate seeded). [`seed-proposal.py`](ADG-UX-DEEP-TITLES-06-evidence/seed-proposal.py) stored one What-if proposal so a proposal page exists. That is local only: it refuses to run if a proposal already exists, and nothing is applied |
| API | `python -m app --port 8030` from this worktree's backend, demo mode, development auth, throwaway secret |
| Web | This lane's standalone build twice: :3032 → API (title gate) and :3030 → recording proxy :8130. The Wave B standalone build of `10c73d1` on :3031 → proxy :8131 |
| Browser | Chrome 156 (`/Applications`), Playwright 1.62.1 via `NODE_PATH`, Node 22.23.3 arm64 |

No secrets, databases or `node_modules` are committed.

## 10. Limitations

1. **Generic detail titles remain by design.** No object names (§4, §5).
2. **Identity view tabs share `Identity — ADG`.** These are tabs, and a correct label would need a
   read (§4).
3. **Unknown or invalid objects answer HTTP 200.** That covers campaigns, items, proposals and
   identity views. The titles are coherent, but the status codes are unchanged (out of scope).
4. **The campaign item link is a plain `<a>`.** That one move is a full page load (pre-existing,
   §7). The title is correct either way.
5. **Local only.** The run used the local real API and dataset, not the hosted demo. The deeper-page
   journey needs data that offers its links: changes within 90 days, a campaign with an item,
   and a proposal. On the hosted demo the public `planner` account sees 9 nav links, and the
   dataset may lack a campaign or proposal. A missing link fails the step and names it.
6. **The "90 days" step depends on the date.** The Genesee demo data ends 2026-10-01, so the
   History and Why-access-changed links appear only in the 90-day window. After about
   2026-12-30 the journey needs a newer dataset or a time-shift, as the overview gate uses.
7. **One viewport** (1360×900). Only metadata changed, and no rendered UI changed.

## 11. Integration notes

Product files are 12 page modules, each with metadata only. The other changes are
`tests/titles.test.ts` and `cloudflare/checks/titles.mjs`. Nothing touches `lib/nav.ts`, any layout,
or a component. A lane that edits one of these pages' imports gets at most a trivial import-line
conflict. Keep both sides and keep the `metadata` / `generateMetadata` export.

## 12. Status

`NOT MERGED` · `NOT DEPLOYED`
