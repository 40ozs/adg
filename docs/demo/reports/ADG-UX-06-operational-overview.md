# ADG-UX-06: Operational Overview

- **Date:** 2026-10-30
- **Lane:** ADG-UX-06, then ADG-UX-06B (browser validation and performance gate, §10). Ran
  concurrently with ADG-UX-01 (Information Architecture).
- **Branch:** `feature/ADG-UX-06-operational-overview` (pushed, not merged, no pull request)
- **Worktree:** `/Volumes/T5 EVO/mmm/worktrees/adg-UX-06`

```
STARTING_MAIN_SHA=bc178c920155970a9c3abb51b3a275a99ac58317
BRANCH_NAME=feature/ADG-UX-06-operational-overview
WORKTREE_PATH=/Volumes/T5 EVO/mmm/worktrees/adg-UX-06
UX06_CODE_SHA=eb17f895673a9d9b9c29e3a5cf1aa66658d437fc
UX06_REPORT_SHA=a03f72a23cac6aa8e72d3691ad10225742aeec19
UX06B_START_SHA=a03f72a23cac6aa8e72d3691ad10225742aeec19
LAST_CODE_SHA=dc2bff8c143967a1829b49fd0a4ec2433817794b
FINAL_SHA=the commit that adds this revision of the report (the branch head)
```

---

## 1. Verdict

**`READY_FOR_ADG_UX_OVERVIEW_INTEGRATION_WITH_LIMITATIONS`** (ADG-UX-06B, after browser
validation; the UX-06 verdict was `READY_FOR_ADG_UX_OVERVIEW_WITH_LIMITATIONS`)

The Overview now answers four of the five operational questions. Each section shows the
counts the API returned, the caveat that decides how to read them, and a link to the records
behind them. The fifth question, *Recently viewed*, is deferred (§9).

UX-06B ran the page in a real browser against the real API and the Genesee Press demo. The
new browser gate passed 43/43. The run found three defects, and all three are fixed (§10.2):

- a duplicate `/runtime` read on every render;
- unreadable Data health values at phone widths;
- one label that broke UX-01's terminology contract.

The verdict carries limitations rather than being a plain "ready" for one main reason: a
**slow** read holds back the whole page until it answers or the 10 s client timeout passes.
A **failed** read already degrades only its own section (§10.6).

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
- **Seven reads, issued together with `Promise.all`** (one of them, `/runtime`, is shared with
  the shell; see §10.4):
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
- **Checked in a real browser in UX-06B** (§10.7): keyboard reach, focus order, visible focus,
  reading order at 1440 and 375 px, link names, and no nested links or clickable cards.
- **Still not done:** an automated scanner run (no axe or similar is installed) and a formal
  WCAG audit.

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

UX-06 had no browser run. UX-06B adds one: `frontend/cloudflare/checks/overview.mjs` (§10).
The UX-06B gate results on the final tree are in §10.9.

## 8. UX-01 reconciliation

- **State at UX-06 time:** in progress, uncommitted and unpushed, in its locked worktree.
- **State at UX-06B time:** committed and pushed as
  `origin/feature/ADG-UX-01-information-architecture` @ `6ded5ed`. Its worktree is clean and
  unlocked. It has its own report, `docs/demo/reports/ADG-UX-01-information-architecture.md`,
  with an IA and terminology contract in §3. UX-06B neither modified nor merged it.
- **UX-06B rechecks against `6ded5ed`:**
  - `git merge-tree` of `6ded5ed` with UX-06 code head `dc2bff8` is clean;
  - the two branches share no files;
  - UX-01 keeps the ids `risks`, `changes`, `governance` and `collectors`, and keeps
    `href: "/risks"`;
  - its shell keeps `header.shell-header`, `#main` and one `nav[aria-label="Primary"]`, so the
    UX-06B gate's selectors still hold.
- **The one item UX-01 raised against UX-06 is fixed.** UX-01's report flagged
  `<dt>Risk evaluation</dt>` as breaking its terminology contract; it now reads "Findings
  evaluation" (`dc2bff8`).
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
- **A slow read delays the whole page** (measured in §10.6). The page renders on the server
  only after all of its reads settle; a section cannot appear before the others. A 4 s read
  gives a 4.05 s first byte. A hung read is cut off by the existing 10 s client timeout, after
  which only its section says "ADG did not answer in time". Every ADG page works this way
  (each awaits all of its reads). Streaming the Changes and Reviews sections behind `Suspense`
  would lift this for them, but Data health and Needs attention decide the page order and must
  arrive first. Left for a follow-up, because the reads take 59–182 ms locally.
- **Timings are local only and load-sensitive.** They were not measured against the hosted
  stack (no deploy). Other work on this Mac held the load average between 15 and 190 during
  the runs (§10.5).
- **The demo has no changes in a window ending today.** The Genesee Press scans are dated up
  to 2026-10-01, so on the demo the Recent changes section always shows its "a collector
  failed" empty state. This is honest, but it means the demo never shows the populated
  section. The gate covers the populated section with a time shift (§10.1).
- **`/favicon.ico` returns 404.** This predates this branch (no icon exists in `public/` or
  `app/`) and is the only console error in the run.

## 10. Browser validation (ADG-UX-06B)

### 10.1 Environment and method

**Stack.** The application ran locally, from `a03f72a`, with each fix rebuilt and re-run.

| Part | What ran |
| --- | --- |
| Database | Throwaway Postgres 16.2 (native arm64, from the `pgserver` wheel; Docker is not installed on this Mac), on 127.0.0.1:55435, with a UTF8 database `adg_demo` |
| Dataset | `python -m app.demo_mode reset` with Genesee Press: 225 s, 300 runs (297 succeeded, 2 failed, 1 partial), 41,756 observations, probes 23/23, 9,386 open findings (`ADG-UX-06-evidence/demo-reset.txt`) |
| API | FastAPI `python -m app` on 127.0.0.1:8000, `ADG_RUNTIME_MODE=demo`, development auth. The backend code is identical to `main`, so the canonical checkout's venv was used |
| Web | Next.js standalone `server.js` (`ADG_NEXT_OUTPUT=standalone next build --webpack`) on 127.0.0.1:3000, with the same environment `render/gate.ts` gives it |
| Browser | Google Chrome 156.0.8078.74 (arm64), driven by Playwright 1.62.1 under Node 22.23.3 arm64 |

Nothing hosted was touched.

**Deviation from the deployed path: no gate or Worker in front of Next.js.** `render/gate.ts`
fixes Next's API address at `127.0.0.1:8000`. The validation needs a recorder on that hop
(below), so the two processes the gate supervises were started directly, with the gate's
web environment, and `ADG_INTERNAL_API_URL` pointed at the recorder. The gate and the Worker
are transparent proxies for this page. The hosted checks (`browser.mjs`, `smoke.mjs`) cover
them.

**The gate: `frontend/cloudflare/checks/overview.mjs`** (committed).

- **A recording proxy.** The Overview is a server component, so its reads leave the Next.js
  server, never the browser. The gate's proxy sits between Next.js and the API and records
  every read (path, query, start, end, status). That is how permission gating and fan-out
  are checked at the request level.
- **Fixtures.** The proxy can also fail, delay or rewrite one path, which produces the states
  the dataset does not contain. Every check that uses this is named "fixture" or "fault":
  - complete totals, zero findings, never evaluated;
  - healthy or `no_data` collection;
  - a past-due campaign;
  - long unbroken names;
  - HTTP 500/503 failures and 4 s / 12 s delays.
- **A time shift for changes.** The dataset's changes end at 2026-10-01, so "the last 7 days"
  from today is empty. A time-shift fixture moves the `from`/`to` of every `/api/v1/changes*`
  request back by the same offset. The Overview and the Changes page then both read
  2026-09-24 → 2026-10-01 (99 changes, 89 in the default view) under identical semantics.
- **One real review campaign.** It was created through the API as `governance` on share
  `gp-acq-fs01|acquisitions` (17 items), assigned to `dev:reviewer` and activated, as the demo
  mutation policy allows. The gate refuses non-loopback targets.
- **Run it:**
  `WEB_URL=http://127.0.0.1:3000 API_URL=http://127.0.0.1:8000 PROXY_PORT=8100 CHROMIUM_PATH=… node cloudflare/checks/overview.mjs`

**Evidence: `docs/demo/reports/ADG-UX-06-evidence/`**

- `overview-gate.json`: results and measurements of the final run, 43/43 (every result
  `ok: true`).
- The final run's console output was saved as `overview-gate.log`, which was never committed
  because `.gitignore` ignores `*.log`. The committed text output of the same gate, rerun
  unchanged on the integrated Wave A tree (43/43), is
  [`ADG-UX-WAVE-A-INTEGRATION-01-evidence/overview-gate.txt`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/overview-gate.txt)
  (data:
  [`overview-gate.json`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/overview-gate.json)).
  (Evidence-link note added by ADG-UX-WAVE-A-DOCS-01.)
- `overview-gate.load.txt`: the load average around the final run.
- `cold-render.txt`: the cold server render timing.
- `demo-reset.txt`
- `screens/`:
  - `overview-demo-{1440,1280,1024,768,375,320}.png`
  - `overview-healthy-fixture-1440.png`
  - `overview-long-names-{1440,320}.png`

### 10.2 Defects the browser run found, all fixed in `dc2bff8`

1. **Duplicate read: `/runtime` twice per Overview render.**
   - **Cause:** the shell and the page each called `fetchRuntime()`. The same duplicate already
     existed on `/demo` and `/access/investigate`.
   - **Fix:** `fetchRuntime` is now wrapped in React `cache`, the pattern `currentViewer`
     already uses, so it is read once per request (`lib/api/adg.ts`).
   - **Re-measured:** every path is read exactly once per render (§10.4).
2. **Data health was unreadable on phones.**
   - **Cause:** the shared `dl.facts` grid keeps a label column as wide as "Findings
     evaluation". At 320 px that left values about 80 px wide, broken mid-word ("unobserve /
     d").
   - **Fix:** Data health stacks each label above its value at widths of 40rem and below. This
     is scoped through `overview.module.css`, so `globals.css` (a UX-01 file) is untouched.
   - **Guarded:** the gate now asserts that no Data health value is narrower than
     min(240 px, 60 % of the section).
3. **Terminology.** "Risk evaluation" → "Findings evaluation" (UX-01 report §3 and §9.6).

Gate-side mistakes found and corrected while writing the gate are not product defects:

- the seed assigned the reviewer as `reviewer` instead of the signed-in subject `dev:reviewer`;
- word checks read CSS-uppercased text instead of DOM text (`.badge` is lowercased by CSS; the
  DOM, which is what a screen reader reads, says "Not a total" and "Failed").

### 10.3 Sections, as rendered

**Section order (demo dataset as it is).** The dataset has collection `failed`, 98 of 100
scopes trustworthy, 2 concerns, and a findings reading of `incomplete`. The rendered order is:

Data health > Needs attention > Recent changes > Reviews > Where to start > What ADG will not do

**Needs attention**
- **Demo as it is (incomplete):** "⚠ Not a total" appears with the backend's own reason text.
  The page never says "latest completed evaluation" here.
- **Complete-totals fixture:**
  - links read "3 critical findings", "8 high findings" and "9,386 open findings in all";
  - the top rules are `redundant_access_paths` 6,795, `disabled_principal_retains_access`
    1,921 and `direct_user_ace` 308;
  - no "Not a total" appears.
- **Following each link** lands on Findings with the filter active (`aria-current` on the
  severity or rule, and on "open only"). The pager says "3 findings in total", "8 findings in
  total", or the rule's count, which match the Overview.
- **Never-evaluated fixture:** the section contains no digit at all.
- **Zero-findings fixture:** "No open findings in the latest completed evaluation." appears
  only over a complete reading. Over an incomplete one it reads "No open findings were
  returned, but that is not a complete answer."

**Recent changes**
- **Demo as it is:** "No changes in the last 7 days found — but a collector failed", as an
  alert, with no zero and no "quiet week".
- **Time-shifted fixture:**
  - "89 permission or unclassified changes", "10 more changes … are outside that view" and
    "most severe high" all equal the API's summary;
  - three rows, with no date shown as a change time;
  - the first row opens `/changes?kind=ntfs_ace&key=…` with the heading "History";
  - "View changes from the last 7 days" opens `/changes?days=7`, whose summary says "99
    changes recorded, 89 shown", the same 89.

**Reviews**
- **Real campaign (`reviewer`):**
  - "17 items assigned to you across 1 campaign", "Due 2026-11-13";
  - the campaign link opens `/governance/campaigns/<id>?mine=1` with the campaign's name as its
    `h1`;
  - "Open my review queue" opens `/governance`.
- **Past-due fixture:** "1 campaign past due", "Overdue — … left · Was due 2026-10-01".
- **`auditor`** (`governance:read`, nothing assigned): "Nothing is assigned to you.", no alert,
  and a "See review campaigns" link.

**Data health (demo as it is)**
- Collection "✕ Failed", plus the backend summary.
- "98 of 100 collection scopes have a latest run that succeeded with no errors".
- The coverage headline from the API.
- "Findings evaluation: ⚠ Not a total, Last ran 2026-10-01 06:00:00 UTC (full)".
- Both concerns listed.
- "Synthetic demo data for Genesee Press. No production directory or file server is
  connected."
- No `%` and no "score".

**Data health state fixtures:** each state is a word plus a symbol, checked in DOM text:
"✓ Complete", "◐ Incomplete", "✕ Failed", "∅ Never collected". `no_data` shows no "0 of 0".

**Promotion** (measured as DOM order):

| Condition | Data health position |
| --- | --- |
| Collection unhealthy (demo as it is) | first |
| Collection status HTTP 503 | first, with an alert, and the other sections intact |
| Healthy collection but totals not complete | first |
| Healthy collection and complete totals | back to fourth, after Reviews |

### 10.4 Permissions and fan-out, at the request level

Permission variants, from the proxy log of each render:

| Account | Sections | Overview reads sent |
| --- | --- | --- |
| `planner` | all four | 7 |
| `auditor` | all four | 7 |
| `viewer` (no `governance:read`) | no Reviews | 6; **no `/governance/queue` request** |
| `nobody` (`remediator` only, no capability) | Data health only | `/runtime` and `/collection/status` only; **no risks, changes or governance request**. The status read is refused with 403, and Data health says "Your account cannot see this" rather than a state |

Unauthorized summary data is never fetched and hidden afterwards: each section's read is not
sent at all.

Fan-out of one render (`planner`, final run, from the proxy log):

- **9 API requests, each path exactly once.** The three shell reads are `/auth/config`,
  `/runtime` and `/auth/me`. The six page reads are collection status, risk summary, risk
  rules, change summary, the change feed (`limit=6`) and the reviewer queue. `/runtime` is now
  shared between the shell and the page.
- **Two stages, and no waterfall within either.** The six page reads need the viewer's token
  and capabilities, so they start after `/auth/me` returns (17 ms into the render), within
  4 ms of each other.
- **Slowest read:** the risk summary at 104 ms in this render. It was the slowest in every
  load measured.
- **No table reads:** no `/risks/findings`, `/principals`, `/scan-runs` or campaign-list read.
  No failed or cancelled requests, no repeat-fetch loop and no reload (client navigation was
  checked separately, §10.8).

### 10.5 Timing (local)

**Final gate run.** Load average 25.9 → 15.3. Each load used a new browser context (no cache).

| Load | First byte | First paint (FCP) | Load event | Slowest read |
| --- | --- | --- | --- | --- |
| First in the timing check | 133 ms | 236 ms | 257 ms | risk summary 108 ms |
| Warm, 5 loads, median | 92 ms | 128 ms | 117 ms | risk summary 59–182 ms |

**Cold server.** First render after restarting Next.js, measured with curl
(`cold-render.txt`, load about 37):

- cold: 629 ms to first byte;
- the next three requests: 334, 227 and 292 ms.

**What the shell timing means.** The shell and the sections arrive in the same server
response: there is no streaming, so "shell visible" equals "sections settled". FCP is the
time at which both are on screen.

**Earlier runs were noisier.** One run started at a load average of about 190; its warm
first byte ranged from 213 to 800 ms. Numbers on this Mac are load-sensitive.

### 10.6 Slow and failing reads

| Fault | Result |
| --- | --- |
| Collection status HTTP 503 | Data health shows the error and moves to the top. Needs attention keeps its counts. Every section renders. No page error. |
| Findings summary HTTP 500 | Needs attention shows "Something went wrong" with no count links and no zero. Data health and Recent changes render. |
| Changes summary delayed 4 s | First byte at 4,052 ms. Every section renders complete. The page waits for the slowest read (limitation, §9). |
| Changes summary delayed 12 s | First byte at 10,017 ms. The 10 s client timeout ends the wait, Recent changes alone says "ADG did not answer in time", and Needs attention keeps its counts. |

### 10.7 Accessibility (browser semantics; not a formal audit)

- One `h1`. Each Overview section is a named region (`getByRole("region", { name })`).
- The 18 links in `#main` all have names, and none is "Learn more", "Details", "Explore" or
  "here".
- No nested links, no link wrapping a card or section, no clickable sections, and no positive
  `tabindex`.
- **Keyboard:** Tab reaches all 10 section links in DOM order. Each shows a focus indicator
  (outline or box-shadow present on focus).
- **Reading order:** section DOM order matches visual order at 1440 px (Changes left of
  Reviews on one row) and at 375 px (stacked).
- Every status badge carries a word; colour is never the only signal.
- **Not done:** an automated scanner and a WCAG audit.

### 10.8 Navigation

All of these use client navigation: a marker set on `window` survives each one, so there was
no document reload. Back returns to the same Overview document.

- severity → Findings (critical, high);
- rule → Findings (three rules);
- change row → object history;
- "View changes from the last 7 days" → Changes;
- campaign → campaign page;
- "Open my review queue" → Reviews;
- every "Where to start" link: `/resources`, `/identities`, `/access`, `/risks`, `/changes`,
  `/simulations`, `/governance`, `/collectors`.

### 10.9 Responsive, and the final gates

**Responsive.** At every width: no sideways scroll, no overlapping headings, no clipped or
overflowing sections, and readable Data health values.

| Viewport | Recent changes / Reviews |
| --- | --- |
| 1440 × 900 | side by side |
| 1280 × 800 | side by side |
| 1024 × 768 | side by side |
| 768 × 1024 | side by side |
| 375 × 812 | stacked |
| 320 × 640 | stacked |

An unbroken 120-character rule title, change headline and campaign name keep the page in
bounds at both 320 and 1440 px.

**Final gates** (Node 22.23.3 arm64, tree = `dc2bff8`):

```
browser gate  overview.mjs   43/43 passed (one console error: pre-existing /favicon.ico 404)
npm test                      45 files, 1536 tests passed
npm run lint                  exit 0 (lints the new .mjs too)
npm run typecheck             exit 0
npm run build                 exit 0 (next build --webpack, compiled)
```

**Backend.** No backend change. The demo reset's probes (23/23) and the API under the gate are
the backend evidence used, as the lane allows.

**Recently viewed** is still not implemented, by design (§9). Its follow-up recommendation is
unchanged.
