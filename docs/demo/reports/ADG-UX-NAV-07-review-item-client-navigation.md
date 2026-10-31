# ADG-UX-NAV-07 — Campaign review-item client navigation

**Verdict: READY_FOR_ADG_UX_NAV_INTEGRATION_WITH_LIMITATIONS**

Opening a review item from a campaign's item table no longer reloads the whole document. The link
is now a `next/link` `Link` with prefetch off. The href, text, styling and keyboard behaviour are
the same. Titles, Back and Forward work. The move makes no extra API read: it makes 3 instead of
the full load's 5. The titles gate no longer exempts any document load.

Branch `feature/ADG-UX-NAV-07-review-item-client-navigation`, code commit `d38c68b`. It is not
merged, and nothing was deployed.

## 1. Baseline

| | |
|---|---|
| Baseline | `origin/integration/ADG-UX-WAVE-C-01` @ `559dbaf913a02e0df4ddf18be89ef81f03e48596` |
| Baseline CI | run `41167906074` on that exact head: **success**. Collector, Compose, Backend (lint, types, tests) and Frontend (lint, types, tests, build) all passed; the fork-PR job was skipped. The branch was created only after the run finished. |
| Newer UX baseline? | None. Wave C is the newest `integration/ADG-UX-*` ref on origin; every newer-dated ref is one of its own source lanes. |
| Read first | `ADG-UX-WAVE-C-INTEGRATION-01.md` (limitation 4) and `ADG-UX-DEEP-TITLES-06-contextual-titles.md` (§7 "Known full load", limitation 4) |

## 2. The old navigation mechanism

`frontend/components/DecisionForm.tsx`, `SelectableItems`, first cell of each item row:

```tsx
<a href={`/governance/items/${item.item_id}`}>
  {item.principal_display_name ?? item.principal_sid}
</a>
```

This plain anchor has been there since the review screens were first added (`401c988`).
`ee0a856` moved it into `SortableTable` without changing it. A click on it is an ordinary browser
navigation, so the item page loaded as a new document. The `campaigns/[campaignId]` drift view
already linked items with `Link`. Only the main item table did not.

DEEP-TITLES-06's `titles.mjs` declared this step `fullLoad: true`. It exempted the document
loads on that move, and on Back and Forward across it.

## 3. Implementation

```tsx
import Link from "next/link";
…
{/* No prefetch: a campaign lists up to 100 items, each its own page of reads. */}
<Link href={`/governance/items/${item.item_id}`} prefetch={false}>
  {item.principal_display_name ?? item.principal_sid}
</Link>
```

- **Pattern:** this is the codebase's own pattern for tables of detail links.
  `components/Identity.tsx` turns prefetch off in the same way for pages of principals and
  chains. So do `ComparisonLink` and `SimulateLink`. The campaign page can list up to 100 items
  (`limit: 100`), and viewport prefetching each one would ask the server for up to 100 item pages.
- **Unchanged:** the href (`/governance/items/{item_id}`, with no query), the link text, the
  styling (`Link` renders a plain `<a>` and no class was added), keyboard behaviour (a focusable
  anchor that Enter follows), authorization, routes and IDs.
- **Not touched:** the same row's two filter links, "(all theirs)" →
  `?principal=…` and the target → `?target=…`. They are campaign → campaign filters, not the
  item path (§9).

Source files changed: `frontend/components/DecisionForm.tsx` only. Test and gate files:
`frontend/tests/governance-views.test.tsx` and `frontend/cloudflare/checks/titles.mjs`.

## 4. Full-reload detection

Two gates detect a reload. Both watch main-frame `document` requests, and both put a sentinel on
`window` that a new document would lose.

**`frontend/cloudflare/checks/titles.mjs` (tightened):**

- The `fullLoad` flag, `fullLoadPages` and `reloads()` filter are deleted. The deep journey
  (21 moves) and its Back/Forward legs now fail on *any* main-frame document request. They also
  check that `window.__adgNoReload` survived. Before, they checked only a filtered request list.
  No full load is expected anywhere on the journey.
- The campaign step selects a campaign whose **Items** cell is non-zero
  (`tr:has(td:last-child:text-matches("^[1-9]")) a[href^="/governance/campaigns/"]:not([href*="?"])`).
  The step used to take the first campaign. On the current Wave C dataset that is COPY-04's empty
  "COPY-04 resource review", which has no item link, so the committed gate failed before it
  reached the item. This changes which campaign is opened. It does not change what is checked.
- **Proof it is strict:** the same tightened gate, run on the baseline build, fails exactly on
  this move. It reports `a move reloaded the document (…/governance/items/…)` and
  `Back/Forward reloaded the document` (3 loads) — 13/15
  ([`titles-gate.baseline-strict.txt`](ADG-UX-NAV-07-evidence/titles-gate.baseline-strict.txt)).

**[`review-item-nav.mjs`](ADG-UX-NAV-07-evidence/review-item-nav.mjs) (new, this lane):** the
§12 journey at 1440 px and 375 px, described in §6. Run on the baseline it fails 8 of 16 checks,
all of them document loads. Each viewport loads 5 documents: 1 on the click, 1 on Back, 1 on
Forward and 2 on the keyboard pass
([`review-item-nav.baseline.txt`](ADG-UX-NAV-07-evidence/review-item-nav.baseline.txt)).

## 5. Request behaviour

Web-tier API calls (Next.js → API, through a recording proxy) for the campaign → item click.
They are compared by method, path and sorted query keys:

| | Baseline `559dbaf` (full load) | This lane (client move) |
|---|---|---|
| Document loads | 1 | **0** |
| API reads during the move | 5: `/auth/config`, `/runtime`, `/auth/me`, item, item context | **3**: `/auth/me`, item, item context |
| Beyond a direct load of the item | 0 | 0 |
| Repeated read inside the move | 0 | 0 |
| Item-route prefetches before the click | 0 | 0 |

The two reads that drop are the root layout's `/auth/config` and `/runtime`. A client move keeps
the shared layout and does not render it again. The item's own reads are the same. Desktop and
375 px measured the same.

A server-side comparison used DEEP-TITLES' `request-count.mjs`, with both builds over the same
API. It made each request once as a document and once as a bare RSC fetch, for Reviews, both
campaigns with items, and the item. Result: **0 additional backend requests over 8 measurements**.
A bare RSC fetch of the item is 5 reads because it carries no router state. The in-browser move
is 3 because the router sends its state tree
([`request-count.txt`](ADG-UX-NAV-07-evidence/request-count.txt)).

## 6. Browser gate (real local stack)

`review-item-nav.mjs`, signed in as `admin` (holds governance read and review), at desktop
(1440×900) and 375×812:

1. Reviews → click the first listed campaign with items: "Review campaign — ADG", no document load
2. Side tab: direct loads of the campaign and the item each send one server `<title>`
   ("Review campaign — ADG", "Review item — ADG"). The item's direct-load reads are recorded
3. Click the item: 0 document loads, the sentinel is kept, the URL equals the link's href (no
   query), "Review item — ADG", no read beyond the direct load, no prefetch
4. After the move: focus on a live node, the heading in view, no sideways scroll
5. Back: the campaign URL and "Review campaign — ADG"; the item table has the same 6 rows and
   4 distinct items, 0 selected; no sideways scroll; no document load
6. Forward: the item URL and "Review item — ADG"; no document load
7. Back, then the item link by keyboard (focus + Enter): the item, client-side
8. 0 page errors, 0 hydration errors

| Build | Result |
|---|---|
| This lane | **16/16** ([`review-item-nav.txt`](ADG-UX-NAV-07-evidence/review-item-nav.txt), [`.json`](ADG-UX-NAV-07-evidence/review-item-nav.json)) |
| Baseline `559dbaf` | 8/16, every failure a document load (§4) |

Console: 1 error at desktop on both builds, `/favicon.ico` 404 (the app ships no favicon). It is
pre-existing and is not a page error.

## 7. Titles regression (DEEP-TITLES contract)

The tightened `titles.mjs` on this lane gave **15/15**
([`titles-gate.txt`](ADG-UX-NAV-07-evidence/titles-gate.txt)):

- the deep journey's 21 moves had **no reload**, and its campaign → item step is now client-side;
- Back went 21 steps to Overview and Forward 21 steps, with no reload;
- 18 direct loads each sent one matching server `<title>`, including "Review campaign — ADG" and
  "Review item — ADG";
- unknown campaign/item/proposal/view titles are unchanged;
- 0 page errors, and no hydration errors.

`review-item-nav.mjs` covers the five-step contract in §7 of the brief at both widths: campaign
direct load, click to the item, item title, Back to the campaign title, Forward to the item title.
No title went stale.

## 8. Focus, scroll and state

- **Focus** after the move is on `BODY`, which is what the baseline's full load gives. The clicked
  link unmounts, and Next's router does not move focus. Nothing is left on a detached node. No
  focus management was added, because there is no regression to fix.
- **Scroll:** the item heading is in view after the move, at 153 px at desktop and 714 px at 375 px
  (the stacked mobile header). These are the same offsets as the baseline's full load. No scroll
  restoration was added.
- **Back:** the campaign list renders with the same rows, no duplicate item and no item selected,
  and it stays usable. The keyboard pass opens the item again from it.
- **Horizontal scroll:** none on the campaign or item page at 1440 or 375 px.

## 9. Tests and frontend gates

New in `tests/governance-views.test.tsx` ("the campaign's item table"):

- the item link has href `/governance/items/i1` and the accessible name of the identity, and it
  carries the file's `next/link` mock marker `data-prefetch="off"`. A plain anchor has no such
  marker. **Checked both ways:** with only the component change reverted, this test fails on the
  marker;
- the "(all theirs)" and target links keep their `?principal=` / `?target=` campaign URLs, so the
  route contract is unchanged.

| Gate (frontend, Node 22.23.3 arm64) | Result |
|---|---|
| `npm run lint` | pass. The first run linted the parked baseline build that sat inside `frontend/`; it was moved out and the rerun was clean |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass |
| `npm test` | **1693 passed** (49 files): Wave C's 1691 + 2 |
| `ADG_NEXT_OUTPUT=standalone npm run build` | pass |

[`frontend-gates.txt`](ADG-UX-NAV-07-evidence/frontend-gates.txt). The backend tree is
byte-identical to the baseline (`git diff 559dbaf -- backend` is empty), so no backend or DB
suite was rerun.

## 10. Wave C regression

| Gate | Result |
|---|---|
| `titles.mjs` (tightened) | 15/15 (§7) |
| Wave C combined `ADG-UX-WAVE-C-INTEGRATION-01-evidence/wave-c-check.mjs` | **9/9** ([`wave-c-check.txt`](ADG-UX-NAV-07-evidence/wave-c-check.txt)) |
| Reviews-focused unit tests (`governance-views.test.tsx`) | 28/28 |
| Recently Viewed gate | not run: no shared navigation code changed (`Link` in one table cell; `RecentlyViewed`, `PrimaryNav` and `AppShell` untouched) |

## 11. Environment

| | |
|---|---|
| Demo database | pgserver PostgreSQL 16.2 on 127.0.0.1:55507, a copy of Wave C's shut-down `adg_demo`: Genesee Press, the Overview gate's campaign, DEEP-TITLES' proposal and COPY-04's two campaigns. Local only |
| API | `python -m app --port 8070` from this worktree, demo mode, development auth, throwaway secret |
| Web | This lane's standalone build on :3070 (reads via the gates' recording proxy :8170) and :3073 (direct). The baseline `559dbaf` standalone build, made in this worktree before any edit, on :3071 (proxy :8171) and :3072 (direct) |
| Browser | Google Chrome (/Applications), Playwright 1.62.1 via `NODE_PATH`, Node 22.23.3 arm64 |
| Host load | Other sessions' gates kept the 1-minute load average between 30 and 250 throughout. Every gate still passed on the first clean run |

No credentials, databases, browser profiles or `node_modules` are committed.

## 12. Scope

The change is navigation mechanism only. These were not touched: review-scope display names, the
campaign first-100 limit, missing-object HTTP 200, the Reviews UI, Recently Viewed, backend
identity wording, hosted streaming, routes, IDs, APIs and authorization.

## 13. Known limitations

1. **The row's two filter links are still plain `<a>`.** "(all theirs)" (`?principal=`) and the
   target (`?target=`) reload the campaign as a new document. They are outside this lane's one
   path and outside the campaign → item journey. A follow-up can switch them to `Link` the same
   way. After that, the only plain internal anchor left in `app/` and `components/` would be the
   OIDC sign-in start, which must stay a full navigation.
2. **Local stack only.** Hosted (Worker → `render/gate.ts`) behaviour is not verified. The same
   open item is carried from Waves B and C.
3. **API reads on Back/Forward are not counted.** Only the click move is measured. Back and
   Forward are checked for document loads, titles and table state.
4. **Focus goes to `BODY` after the move**, the same as the full load before. Moving focus to the
   new heading would be a separate accessibility decision.
5. The committed Wave C `titles.mjs` failed on the current local dataset, which has COPY-04's
   empty campaign first. This lane's version picks a non-empty campaign.

## 14. CI

| Commit | Run | Result |
|---|---|---|
| `d38c68b` (code) | `41170824147` | queued at report time |
