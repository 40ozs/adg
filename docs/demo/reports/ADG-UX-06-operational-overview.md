# ADG-UX-06: Operational Overview

- **Date:** 2026-10-30
- **Lane:** ADG-UX-06 (concurrent with ADG-UX-01, Information Architecture)
- **Branch:** `feature/ADG-UX-06-operational-overview` (pushed, not merged, no pull request)
- **Worktree:** `/Volumes/T5 EVO/mmm/worktrees/adg-UX-06`

```
STARTING_MAIN_SHA=bc178c920155970a9c3abb51b3a275a99ac58317
BRANCH_NAME=feature/ADG-UX-06-operational-overview
WORKTREE_PATH=/Volumes/T5 EVO/mmm/worktrees/adg-UX-06
LAST_CODE_SHA=eb17f895673a9d9b9c29e3a5cf1aa66658d437fc
FINAL_SHA=the commit that adds this report (the branch head)
```

---

## 1. Verdict

**`READY_FOR_ADG_UX_OVERVIEW_WITH_LIMITATIONS`**

The Overview now answers four of the five operational questions. Each section shows the
counts the API returned, the caveat that decides how to read them, and a link to the records
behind them. The fifth question, *Recently viewed*, is deferred (§9).

Nothing was deployed. No backend, contract, navigation, shell or shared-stylesheet file
changed.

## 2. Before → after

**Before.** The Overview had:

- a `CollectionSummary` banner (collection health and summary);
- a "Where to start" list of navigation sections;
- a "What ADG will not do" card.

It held no findings, change or review information.

**After** (top to bottom):

1. **Data health**, but only when collection is not healthy, the collection request failed,
   or the risk counts are not totals.
2. **Needs attention**: open critical findings, open high findings and all open findings,
   each one a link. Then the three most common rules, each one a link.
3. **Recent changes** and **Reviews**, side by side when there is room and stacked otherwise.
4. **Data health**, in this position when the evidence is complete.
5. **Where to start** and **What ADG will not do**, both unchanged.

A section is rendered and requested only when the account holds the capability it needs:

| Section | Capability |
| --- | --- |
| Needs attention | `risks:read` |
| Recent changes | `changes:read` |
| Reviews | `governance:read` |
| Data health | always shown (collection status is on every page's path) |

## 3. Truthfulness statement

| Overview value | Source | Kind |
| --- | --- | --- |
| Critical / high / total open findings | `GET /api/v1/risks/summary` `severity_counts`, `total` (open findings only, `FindingQuery()` default) | Backend-provided |
| Most common rules | same response, `rule_counts` (open only); titles from `GET /api/v1/risks/rules` | Backend-provided, top 3 by count |
| "Not a total" caveat, reading headline | `interpretation.reading`/`reasons` via the existing `totalsNotice`; an older server falls back to `coverageNotice(coverage)` | Backend-provided |
| "Findings have not been evaluated yet" | `coverage.has_ever_run === false` | Backend-provided |
| N permission or unclassified changes, last 7 days | `GET /api/v1/changes/summary` `returned` under the default filter, which is the same filter and window `/changes?days=7` shows | Backend-provided |
| N more changes outside that view | same response, `excluded` | Backend-provided |
| Most severe change | `highest_severity` | Backend-provided |
| Truncation note | `truncated` via the existing `truncationNotice` | Backend-provided |
| Three most recent changes | `GET /api/v1/changes?limit=6`, grouped with the existing `groupChanges` (the two halves of one ACL edit become one line) | Backend-provided, first three groups |
| Empty change window wording | existing `classify` against the collection verdict ("a collector failed", "incomplete", "nothing collected", or an answer only when healthy) | Derived from the backend verdict |
| Items assigned to you, campaigns, campaigns past due | `GET /api/v1/governance/queue` `total_pending`, `entries.length`, `overdue_campaigns` | Backend-provided |
| Per-campaign line | existing `queueHeadline` / `dueWording` (absolute dates) | Backend-provided |
| Collection state word | `CollectionStatus.health`, worded Complete / Incomplete / Failed / Never collected, each with a symbol | Backend-provided |
| "X of N collection scopes have a latest run that succeeded with no errors" | `collectors[].trustworthy`, counted | Derived from complete data (the list is one row per scope) |
| Coverage headline | `risks/summary` `collection.headline` (present only with `collectors:read`) | Backend-provided |
| Last risk evaluation, trigger, Total / Not a total | `coverage.evaluated_at`, `coverage.trigger`, reading | Backend-provided |
| Synthetic demo data line | `GET /runtime` `runtime_mode`, `dataset.company_name` | Backend-provided |

**Omitted, because they cannot be calculated reliably:**

- **"New since last evaluation."** The findings API filters by `first_seen_from`, but a
  finding that reopened keeps its first detection time. A count by that filter would be an
  approximation presented as a fact.
- **"Due soon."** The queue gives `due_at` and `overdue`, but any "soon" threshold would be
  ADG's invention. The house rule (`lib/governance.ts` `dueWording`) is absolute dates only.
  Each campaign's due date is shown instead.
- **"Identities gained access" / "sensitive resource changed."** The change summary has no
  breakdown by direction or by sensitivity. Producing one would mean fetching and classifying
  the whole window on the client.
- **Any percentage or aggregate score.** Scopes are not equal in size
  (`CoverageCounts` docstring), and severities are never added together.

## 4. Data sources and API changes

- **No backend change** and no new endpoint. All endpoints were already in the client
  (`lib/api/adg.ts`) and the published contract.
- **Seven reads, issued together with `Promise.all`:**
  - collection status;
  - runtime;
  - risk summary and risk rules, with `risks:read`;
  - change summary and a feed of 6 changes, with `changes:read`;
  - reviewer queue, with `governance:read`.
- No table is fetched in order to count its rows: every count comes from a summary endpoint.
  The one feed read is limited to 6 rows.
- No cache layer and no new infrastructure.

## 5. Files changed

- `frontend/app/page.tsx`: the page. It composes the sections and decides their order.
- `frontend/components/Overview.tsx` (new):
  - the section components `NeedsAttention`, `RecentChanges`, `Reviews` and `DataHealth`;
  - `sectionHref`, the nav lookup;
  - `evidenceNeedsAttention`, which decides when Data health goes first.
- `frontend/components/overview.module.css` (new): the two-column pair and the count row.
  It is a CSS module, so `globals.css` (a UX-01 file) is untouched.
- `frontend/tests/overview-page.test.tsx` (new): 26 tests.
- `docs/demo/reports/ADG-UX-06-operational-overview.md`: this report.

Existing helpers were reused rather than duplicated:

- `classify`/`StateMessage` (empty, error and session states);
- `totalsNotice`, `coverageNotice` and `ruleTallies`;
- `groupChanges`, `headline` and `truncationNotice`;
- `queueHeadline` and `dueWording`;
- `StateBadge` and `SeverityBadge`;
- `hrefWith`.

## 6. Accessibility

- **Headings and landmarks.** There is one `h1`. Each section is a `<section>` labelled by its
  `h2`, so it is exposed as a named region. "Most common" is an `h3`.
- **No colour-only meaning.** Every state is a word plus a symbol beside its colour (the
  existing `StateBadge`): "Not a total ⚠", "Failed ✕", "Never collected ∅". "Past due" is text
  that is also coloured.
- **Link text names the destination,** for example:
  - "3 critical findings";
  - "View changes from the last 7 days";
  - "Open my review queue";
  - "Check collection status".

  There are no "Learn more" or "Details" links.
- **No clickable cards and no nested links.** Every action is an explicit link inside a
  section.
- **Status announcements.** Collection or API problems use the existing banners: `role=alert`
  for warnings and errors, `role=status` for the "Not a total" caveat.
- **Responsive.** The pair grid uses `repeat(auto-fit, minmax(min(100%, 22rem), 1fr))`, so it
  stacks to one column on narrow screens with no horizontal overflow. The count row wraps.
- **No motion, no charts, no hover-only content.**
- **Not done:** an automated axe or browser audit. The suite has no axe dependency, and the
  semantic assertions are in the component tests (§7).

## 7. Tests, lint, typecheck, build

New tests, in `frontend/tests/overview-page.test.tsx`, cover the spec's §19 list:

- the section links come from `NAV_ITEMS`;
- each severity count links to `?severity=`, and each rule links to `?rule=`;
- the "Not a total" caveat appears over an incomplete reading;
- zero findings is called "the latest completed evaluation" only when the reading is
  complete, and never over a partial pass;
- a never-evaluated estate shows no counts at all and no zero;
- a findings API failure shows the error, not a zero;
- every section is capability-aware, both for rendering and for not requesting;
- the change count, the "excluded" note and the 7-day link all match the Changes page;
- at most three recent changes are shown, each linking to its object's history;
- an empty change window over a failed collection reads as "a collector failed";
- the reviews queue shows the past-due campaign;
- an empty queue says "nothing assigned", not a collection warning;
- Data health shows complete, failed, never collected / never evaluated and request-failure
  states, and moves to the top when degraded;
- the collector link is shown only with `collectors:read`;
- demo mode says "synthetic", and production says nothing about synthetic data;
- existing behaviour is kept: the "Where to start" list, the read-only card, and the
  signed-out notice with no API call.

Gate results (Node 22.23.3 arm64, from `frontend/`):

```
npm test          exit 0   45 files, 1536 tests passed (first run; 26 of them new)
npm run lint      exit 0
npm run typecheck exit 0   (first run failed: the test fixture lacked ChangeFiltersView.min_severity; fixed in the code commit)
npm run build     exit 0   next build --webpack, 16.4.1, all routes compiled
vitest tests/overview-page.test.tsx after the fixture fix: 26/26 passed
```

The repository has no frontend end-to-end suite that runs locally against `/`. The hosted
checks (`frontend/cloudflare/checks/browser.mjs`, `smoke.mjs`) assert only the shell and the
demo indicator on `/`, and this change touches neither, so they were not run (no deploy).

## 8. UX-01 reconciliation

- **State at the time of writing.** UX-01 is in progress in its own worktree
  (`/Volumes/T5 EVO/mmm/worktrees/adg-UX-01`, locked, at the same base `bc178c9`). It is
  uncommitted and unpushed, with no remote branch and no report in `docs/demo/reports/`.
- **What I read there, without changing it:**
  - its working-tree `lib/nav.ts` keeps the ids (`risks`, `changes`, `governance`,
    `collectors`) and the route `/risks`;
  - it relabels Risks as **Findings**.
- **How UX-06 consumes that.** Every Overview link reads its route from `NAV_ITEMS` by id
  (`sectionHref`), and the items on the page are already called "findings". When UX-01 lands
  with that `nav.ts`, the Overview needs no change.
- **Files UX-06 left alone because UX-01 is editing them:**
  - `lib/nav.ts`
  - `components/PrimaryNav.tsx`
  - `components/AppShell.tsx`
  - `components/Banners.tsx`
  - `components/Coverage.tsx`
  - `app/globals.css`
  - `app/risks/page.tsx`
  - `app/changes/page.tsx`
  - `app/governance/page.tsx`
- **Possible conflicts:**
  - **None at text level:** UX-01's working tree does not touch `app/page.tsx`, and the other
    UX-06 files are new.
  - **Semantic:**
    - If UX-01 changes a nav **id**, `sectionHref` falls back to `/`. The "section routes"
      test fails in that case, so the break is caught.
    - The "Where to start" list renders `visibleNavItems` unchanged. It inherits UX-01's
      groups and labels as they are, without any group headings.
- **Leftover:** `CollectionSummary` in `components/Banners.tsx` is now unused. It was left in
  place because UX-01 edits that file, and it can be deleted at integration.

## 9. Known limitations and deferred items

- **Recently viewed: deferred.** ADG has no recent-entity mechanism; the only browser-storage
  reference is a comment in `lib/auth/session.ts`. Adding one means a client recorder on every
  entity detail page, which are the principal layout and the resource, share and directory
  pages. UX-01 is editing those pages, and they are the subject of the separate
  entity-header lane. It also means keeping entity names (UNC paths, account names) in
  browser storage, which needs an explicit privacy decision. The recommended follow-up is a
  small lane after UX-01 lands: a bounded `sessionStorage` list of 5 (kind, key, label) entries
  and a client `RecentlyViewed` section on the Overview, with no server persistence.
- **"New since last evaluation", "due soon", "gained access":** omitted (§3).
- **Recent change lines show no time.** `at` is when a collector looked, not when the change
  happened (ADR-0019, Changes page docstring). The section links to the full feed, where every
  row carries its window.
- **Seven reads per Overview render, up from one.** All are independent summary reads issued
  in parallel, and the risk summary is the heaviest because it computes collection coverage.
  This was not measured against the hosted stack (no deploy).
- **Not verified in a running browser.** Verification is component-level only. There was no
  local full-stack walkthrough and no screenshot.
