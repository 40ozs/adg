# ADG-UX-WAVE-A-INTEGRATION-01 — UX Wave A integration

**Verdict: `READY_FOR_ADG_UX_WAVE_B_WITH_LIMITATIONS`** (limitations: §11).

UX-01 (information architecture) and UX-06 (operational Overview) are merged into one tree,
`integration/ADG-UX-WAVE-A-01`. The merges had no conflicts. The only code change on top is
removing the now-unused `CollectionSummary` banner. On the combined tree, every frontend gate
passes, and so do both lanes' browser gates and a new integration check, all run against the
real local stack. No backend file changed. The branch is not merged to `main` and nothing was
deployed.

The two source reports hold the full evidence for each feature, and this report does not repeat
it:

- [`ADG-UX-01-information-architecture.md`](ADG-UX-01-information-architecture.md) is the IA
  and terminology contract (§3 of that report).
- [`ADG-UX-06-operational-overview.md`](ADG-UX-06-operational-overview.md) covers the Overview
  design and the UX-06B browser validation (§10 of that report).

## 1. Record

```
INTEGRATION_BRANCH=integration/ADG-UX-WAVE-A-01
WORKTREE_PATH=/Volumes/T5 EVO/mmm/worktrees/adg-UX-WAVE-A-01
MERGE_BASE=bc178c920155970a9c3abb51b3a275a99ac58317   (= main = origin/main, fetched 2026-10-30)
INTEGRATION_START_SHA=bc178c920155970a9c3abb51b3a275a99ac58317
UX01_SOURCE_SHA=6ded5ed0cc5ccae0d2571c11d13c4c6b4d36a63c   (code 4ef8a8512e9d343fa267fc7297eaa7da5801e1e7)
UX06_SOURCE_SHA=d9f7a3b156eb0af8b6f25e6028113c4b19e2a8be   (code dc2bff8c143967a1829b49fd0a4ec2433817794b)
MERGE_UX01=87bc07a4726de256474e76f180463b383caa49f5
MERGE_UX06=1ddb0549b348fdecd8ee407a59053dccd063ea64
LAST_CODE_SHA=0341198e033ce75530c1253afa934ec80338693c   (CollectionSummary removal)
FINAL_SHA=the commit that adds this report (docs and evidence only, on top of 0341198)
```

**Preflight.** Both source branches are on `origin` at the expected heads (`6ded5ed` and
`d9f7a3b`). The local branches match them, and both lane worktrees are clean. No newer head
exists. `git merge-base` for each pair among UX-01, UX-06 and `main` returns `bc178c9`, so the
two lanes share their starting point with `main`. `main` has not moved since.

## 2. Merges

| Step | Source | Merge commit | Conflicts |
| --- | --- | --- | --- |
| 1 | UX-01 `6ded5ed` | `87bc07a` | none |
| 2 | UX-06 `d9f7a3b` | `1ddb054` | none |

Both are normal `--no-ff` merges, as earlier integrations on `main` did. Nothing was squashed or
cherry-picked.

**No shared files, verified.** The two lanes' changed-file lists against `bc178c9` have an empty
intersection. `git merge-tree --write-tree 6ded5ed d9f7a3b` predicted tree `791a9e0`, and the
real two-step merge produced exactly that tree.

**Semantic seams checked.** UX-06 builds Overview links with `sectionHref(id)` from `NAV_ITEMS`.
UX-01 kept every nav `id` and `href` and changed only labels, groups and descriptions. So the
Overview's "Where to start" list now reads "Findings" (→ `/risks`) with no code change.
`tests/overview-page.test.tsx` keys on section ids, not labels, so no assertion went stale.

## 3. Terminology reconciliation

| Check | Result |
| --- | --- |
| `Findings evaluation` (fixed in UX-06 `dc2bff8`) | Present (`components/Overview.tsx:389`), and in the rendered DOM |
| "Risk evaluation" anywhere | Absent |
| UX-06 source (`Overview.tsx`, `app/page.tsx`, `overview.module.css`) for risk/principal/dependency/place | Every hit is an internal identifier: types (`RiskSummaryResponse`), functions (`riskReading`, `fetchRiskSummary`), `sectionHref("risks")`, `risks:read`, `viewer.principal`. None is user copy. |
| Rendered Overview + nav (as `admin`, demo data), text plus `title`/`aria-label` | No retired term in frontend-authored copy (integration check, §6.3) |
| `/risks` route, `risks:read` capability, API paths | Unchanged. No `/findings` alias. |

So **no copy change was needed**. One user-visible "principal" remains, and the backend supplies
it: the rule title "One principal reaches a resource by several independent routes"
(`redundant_access_paths`) comes from the API's rule catalog. The Overview shows it in "Most
common", and UX-01's Findings page already shows the same title. Changing it would be a backend
change, so it belongs to the deferred Principal → Identity cleanup (§11).

## 4. `CollectionSummary` cleanup

Proven unused on the integrated tree. `grep` over `frontend/` (source, tests, checks), `scripts/`
and the root finds only its own definition in `components/Banners.tsx`. On `main` its one caller
was `app/page.tsx`, which UX-06 replaced. UX-01 touched only `DemoEnvironmentIndicator` in that
file. No test referenced it (`components.test.tsx` and `demo-indicator.test.tsx` import other
banners).

Commit `0341198` removed the function (24 lines) and the `CollectionStatus` type import, which
only it used. Lint, typecheck, `cf:typecheck`, the full test suite and the build ran on that tree
(§7).

## 5. Backend and scope

| Tree | `bc178c9` | `0341198` |
| --- | --- | --- |
| `backend/` | `3501572e997496d51de319346af003a8cbe4ac1b` | `3501572e997496d51de319346af003a8cbe4ac1b` |
| `docker/` | `2c55ceaa56279121fb9c830d8f4136b43222623d` | `2c55ceaa56279121fb9c830d8f4136b43222623d` |

The backend is **byte-identical** to the base, checked by comparing git tree hashes
(`git rev-parse <sha>:backend`). Every path that differs from `bc178c9` is under `frontend/`,
`docs/demo/reports/` or `README.md` (UX-01's section list). No contract, migration, auth or
deploy file changed. Long backend suites were not rerun. The demo reset's 23/23 probes and the
live API under the gates are the backend evidence.

## 6. Browser validation (real local stack)

### 6.1 Environment

| Part | What ran |
| --- | --- |
| Database | Throwaway Postgres 16.2 (native arm64, `pgserver` wheel binaries) on 127.0.0.1:55436, UTF8 database `adg_demo`, created fresh for this lane |
| Dataset | `python -m app.demo_mode reset`, Genesee Press, 228 s: 300 runs (297 succeeded, 2 failed, 1 partial), 41,756 observations, probes 23/23, 9,386 open findings. The same shape as UX-06B. See [`demo-reset.txt`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/demo-reset.txt) |
| API | `python -m app` on 127.0.0.1:8000, `ADG_RUNTIME_MODE=demo`, development auth with a throwaway secret, plus a `nobody` (`remediator`) account |
| Web | `ADG_NEXT_OUTPUT=standalone next build --webpack`, `server.js` on 127.0.0.1:3000 with `render/gate.ts`'s web environment. `ADG_INTERNAL_API_URL` points at the gate's recording proxy (:8100), as UX-06B did |
| UX-01 nav check | Committed harness as-is: `next start` on :13077 (from the plain `npm run build`) against UX-01's stub API on :18077 |
| Browser | Google Chrome 156.0.8078.74 arm64, Playwright 1.62.1, Node 22.23.3 arm64 |

No production data, hosted service, Render, Worker or Supabase was touched. The database,
secret and logs stayed in the session scratchpad and are not committed.

**Harness mistakes, not product defects.** The first Overview gate run scored 37/43 because the
standalone server was started with a wrong static-asset symlink. No JS or CSS loaded, so every
client-navigation check saw a reload. After fixing the link and restarting, all 43 passed. That
first run seeded the review campaign, which is why the final run says "queue already holds 1
campaign". Three early integration-check failures were also expectation errors, all fixed in the
script:

- it used `planner`, who has no `settings:read`, so 9 nav links is the correct filtered result;
- it expected the `/governance` h1 to be "Reviews" (it is "Access reviews");
- it required region roles on the two static cards.

### 6.2 Overview gate: `frontend/cloudflare/checks/overview.mjs`, **43/43**

The committed gate ran unchanged. It made the same 43 checks as UX-06B and all pass. Load
average was 16–27 during the run. Output:
[`overview-gate.log`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/overview-gate.log) and
[`overview-gate.json`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/overview-gate.json).

Highlights on the integrated tree:

- **Order.** On the demo dataset as it is (collection failed, totals incomplete) the order is:
  Data health > Needs attention > Recent changes > Reviews > Where to start > What ADG will not
  do. With the healthy + complete-totals fixture it is: Needs attention > Recent changes >
  Reviews > Data health > … Data health is promoted for each of collection 503, healthy
  collection with incomplete totals, and unhealthy collection.
- **Needs attention.**
  - Demo totals show "⚠ Not a total" with the backend's reason.
  - The complete-totals fixture shows 3 critical, 8 high and 9,386 open, with top rules
    `redundant_access_paths` 6,795, `disabled_principal_retains_access` 1,921 and
    `direct_user_ace` 308.
  - The never-evaluated fixture shows no digits.
  - "No open findings in the latest completed evaluation" appears only over a complete
    evaluation.
  - When the findings summary returns 500, the section shows an error, not a zero.
- **Destinations (navigated, not href-matched).**
  - The critical and high links open Findings with the filter `aria-current`, and the pager
    reads "3 in total" and "8 in total", matching the Overview.
  - The three rule links open Findings filtered to each rule, with matching counts.
  - A change row opens `/changes?kind=ntfs_ace&key=…` (History).
  - "View changes from the last 7 days" opens `/changes?days=7`, which shows the same 89 as
    the Overview.
  - The campaign link opens `/governance/campaigns/<id>?mine=1`.
  - All 8 "Where to start" links open their sections.
  - Every navigation was client-side, and Back returned to the Overview.
- **Recent changes.**
  - On the demo as it is (an empty 7-day window), the section reads "No changes in the last 7
    days found — but a collector failed". It does not read as a quiet week.
  - The time-shift fixture gives 89 shown, 10 excluded, most severe "high", and 3 rows.
- **Reviews.**
  - `reviewer` sees "17 items, 1 campaign, Due 2026-11-13".
  - The past-due fixture reads "1 campaign past due" and "Was due 2026-10-01".
  - `auditor` sees "Nothing is assigned to you", with no collection wording.
- **Data health.**
  - Demo state: "✕ Failed", 98/100 scopes, 2 concerns.
  - Fixtures render "✓ Complete", "◐ Incomplete", "✕ Failed" and "∅ Never collected" as words
    plus symbols.
  - The demo disclosure text is present.
  - No `%` and no score.
- **Responsive.**
  - Recent changes and Reviews sit side by side at 1440, 1280, 1024 and 768 px, and stack at
    375 and 320 px.
  - There is no sideways scroll at any width.
  - Long unbroken names stay in bounds at 320 and 1440 px.
  - The Data-health value-width guard passes.
- **Accessibility (browser semantics).**
  - One h1, and the four operational sections are named regions.
  - 18 named links in `#main`, with no vague link text, no nested links, no clickable cards and
    no positive tabindex.
  - Tab reaches all 10 section links in DOM order, each with a focus ring.
  - Reading order matches visual order at 1440 and 375 px.
- **Errors.** No page errors. One console error, the existing `/favicon.ico` 404 (confirmed
  with `curl`), which was also present in UX-06B.

### 6.3 Integration check, **14/14**

[`integration-check.mjs`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/integration-check.mjs) is new
and kept as evidence, not as a CI gate. It checks what neither lane could check alone. Output:
[`integration-check.log`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/integration-check.log) and
[`integration-check.json`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/integration-check.json).

| Check | Result |
| --- | --- |
| Overview as `admin` at 1440/1280/1024/768/375/320 px | At every width: 4 nav groups in order (Investigate, Directory, Act, System); exactly one `aria-current="page"`, on Overview; all 10 nav links in group order and inside the viewport; no sideways scroll; 4 named Overview regions and the 2 static cards |
| Terminology, rendered (nav + `#main`, text plus `title`/`aria-label`) | No retired term in frontend copy. The one backend rule title is reported (§3). The Findings nav link goes to `/risks` |
| Client navigation through the grouped nav | Overview → Findings → Back and Overview → Reviews → Back: no reload, the current item follows, the h1 updates |
| Keyboard on the Overview | Skip link first, then the 10 nav links in group order with visible focus, then the first Overview section link ("Check collection status") |
| `viewer` on the Overview | Groups Investigate / Directory / System (no Act); no Reviews section; Overview current |
| `/runtime` on `/`, `/demo`, `/access/investigate` | Read once per render, with no duplicate paths (9, 4 and 3 reads), and read again on the next render. So the cache is per request, not global |
| Page errors | None (only the favicon 404) |

### 6.4 UX-01 navigation check, **26/26**

The committed `ADG-UX-01-evidence/nav-check.mjs` and `stub-api.mjs` ran unchanged against the
integrated production build. Output:
[`ux01-nav-check.txt`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/ux01-nav-check.txt).

- Desktop 1280 px and phone 375 px, light and dark schemes: four groups in order; Findings
  current on `/risks` with the h1 "Findings"; weight 600 plus an accent bar (not colour alone);
  no overflow; group-label contrast 7.43:1.
- The skip link is the first stop.
- Tab walks the 10 links in group order with a visible outline, and no group label takes focus.
- Enter navigates.
- Deep links mark the right item: `/risks/alerts` and `/risks?finding=…` → Findings,
  `/identities/users` → Identities, `/resources` → Resources, `/simulations` → What-if,
  `/governance` → Reviews.
- The viewer sees no Act group, no Settings, no What-if and no Reviews.

## 7. Frontend gates

Node 22.23.3 arm64, `frontend/`, tree `0341198`. Output:
[`frontend-gates.txt`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/frontend-gates.txt).

| Command | Result |
| --- | --- |
| `npm run lint` | exit 0 |
| `npm run typecheck` | exit 0 |
| `npm run cf:typecheck` | exit 0 |
| `npm run test` | **45 files, 1,544 / 1,544 passed** |
| `npm run build` (`next build --webpack`) | exit 0 |
| `ADG_NEXT_OUTPUT=standalone npm run build` (for the stack) | exit 0 |

1,544 = UX-01's 1,518 + UX-06's 26 new Overview tests. No test failed, and no assertion was
edited in this lane.

## 8. Requests and permissions (from the gate's recording proxy)

| Account | Overview reads sent | Not sent |
| --- | --- | --- |
| `planner` | 7: `/runtime`, collection status, risk summary, risk rules, change summary, change feed, review queue | — |
| `auditor` | 7 (the same) | — |
| `viewer` | 6 | `/api/v1/governance/queue` |
| `nobody` (`remediator` only) | 2: `/runtime`, collection status (refused with 403; Data health says the account cannot see it) | findings, changes and governance reads |

**Fan-out of one `planner` render: 9 API requests, each path once.**

- The 3 shell reads are `/auth/config` (0 ms), `/runtime` (2 ms) and `/auth/me` (18 ms).
  `/runtime` is shared with the page.
- The 6 page reads start at 185–189 ms, within 4 ms of one another, after `/auth/me` answered.
  There is no waterfall among them.
- There are no table reads (no `/risks/findings`, `/principals`, `/scan-runs` or campaign list).
  The change feed is limited to 6 rows.
- There are no duplicates or repeat loops.

## 9. Timing (local; not hosted)

These were measured on this Mac at load average 16–27. They are not hosted numbers and are not
comparable to Render or Cloudflare. Each load used a fresh context.

| Load | TTFB | FCP | Load event | Slowest read |
| --- | --- | --- | --- | --- |
| First | 611 ms | 912 ms | 916 ms | risk summary 362 ms |
| Warm, 5 loads, median | 185 ms | 228 ms | 228 ms | risk summary 113–190 ms |

UX-06B measured a warm median TTFB of 92 ms at load 15–26. The difference follows host load; the
structure (§8) is unchanged.

**Slow reads.**

- A 4 s changes summary delayed the first byte of the whole page to 4,392 ms. Every section then
  rendered complete.
- A 12 s read hit the 10 s client timeout at 10,121 ms. Recent changes alone said it timed out,
  and Needs attention kept its counts.

Failure isolation holds: a failed or timed-out read degrades only its own section and never
becomes a false zero or a false healthy state. Isolation does **not** mean the page renders
before the slowest read settles:

> **KNOWN_LIMITATION — whole-page SSR waits for slowest summary read.** It is unchanged by this
> integration and it is not specific to the Overview. It was deliberately not addressed here.
> Recommended follow-up: **ADG-UX-PERF-01 — Progressive Overview Rendering and Slow-Read
> Isolation**.

## 10. Evidence

[`ADG-UX-WAVE-A-INTEGRATION-01-evidence/`](ADG-UX-WAVE-A-INTEGRATION-01-evidence/):

- `overview-gate.log`, `overview-gate.json`, `overview-gate.load.txt`: the committed Overview
  gate, 43/43.
- `integration-check.mjs`, `integration-check.log`, `integration-check.json`: 14/14.
- `ux01-nav-check.txt`: UX-01's committed nav check, 26/26.
- `frontend-gates.txt`: lint, typecheck, cf:typecheck, tests, builds.
- `demo-reset.txt`: dataset summary and fingerprint.
- `screens/`:
  - `integrated-overview-1440.png`: desktop, merged nav + Overview.
  - `integrated-overview-375.png`.
  - `integrated-nav-1440.png`: the grouped nav, Overview current.
  - `integrated-data-health-320.png`: stacked labels, readable values.
  - `overview-demo-1440.png`, `overview-demo-320.png`, `overview-long-names-320.png`: from the
    gate.
  - `ux01-nav-phone-375.png`: from UX-01's check.

## 11. Limitations carried forward

None of these is new, and none is a blocker.

**UX-01**

1. The identity detail tabs and deeper explanation copy still say "principal". In addition, one
   backend-supplied rule title ("One principal reaches a resource by several independent
   routes") appears on the Overview and on Findings. Owner: the entity-context lane, which will
   need a backend rule-catalog text change for the title.
2. Every page's `<title>` is "ADG". Document titles are deferred.
3. No manual VoiceOver or NVDA pass. Everything above was checked through the DOM and the
   accessibility tree only, and is not a formal accessibility audit.

**UX-06**

4. Whole-page SSR waits for the slowest summary read (§9).
5. Hosted-stack timing has not been measured. All numbers here are local.
6. The demo's current seven-day Recent changes window is naturally empty: the dataset ends
   2026-10-01. The gate's time-shift fixture covers the populated case.
7. Recently Viewed is still deferred: no localStorage, sessionStorage or history tracking was
   added.

**Integration**

8. "Where to start" and "What ADG will not do" are `<h2>`-headed cards, not named regions. They
   are unchanged from `main`, and UX-06 named only its four operational sections.

## 12. Recommended Wave B starting point

Branch Wave B lanes from **`integration/ADG-UX-WAVE-A-01` at this report's commit**, or from
`main` once the owner merges this branch. Do not branch from either source lane. Candidates, in
the order the limitations suggest:

1. **ADG-UX-PERF-01**: progressive Overview rendering and slow-read isolation.
2. **The entity-context lane**: the deeper Principal → Identity cleanup, including the backend
   rule title.
3. **Document titles.**
4. **Recently Viewed**, after a privacy and architecture decision.

## 13. Not done, by design

- No merge to `main`.
- No pull request.
- No push to the deploy branch, and no deploy (Render, Worker, Supabase): **NOT DEPLOYED**.
- No backend, contract or migration change.
- No streaming or Suspense redesign.
- No `/findings` alias.
