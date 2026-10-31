# ADG-UX-WAVE-C-INTEGRATION-01 — UX Wave C integration

**Verdict: `READY_FOR_ADG_UX_WAVE_D_WITH_LIMITATIONS`**. CI is green on every admitted source head and on the integration head (§4, §13). Limitations are in §15.

BACKEND-COPY-04 (identity wording in backend prose, readable removal and scope labels),
DEEP-TITLES-06 (contextual titles for deeper pages) and RECENT-05B (tab-local Recently viewed)
are merged into one tree, `integration/ADG-UX-WAVE-C-01`, on top of the Wave B UX baseline.
None of the three merges had a textual conflict. Two files changed in two lanes, and git merged
both sides of each. No product file was edited during integration.

The integration's DB run found one defect in a source lane. BACKEND-COPY-04's final head
`c04a3e8` still had a DB test asserting the old word "principal" for a sentence the lane had
reworded on purpose. The lane's own CI later failed on the same assertion. With the owner's
approval, a one-line test-only commit (`9cec0b0`) went onto the source branch, and the unpushed
integration branch was rebuilt with that corrected head (§2, §4).

Every frontend gate, every backend gate, every source browser gate and a new cross-lane gate
pass on the combined tree. All browser gates ran against the real local Genesee Press stack.
The branch is not merged to `main`, and nothing was deployed.

The source reports hold each feature's full evidence:

- [`ADG-UX-BACKEND-COPY-04-identity-wording.md`](ADG-UX-BACKEND-COPY-04-identity-wording.md)
  (§2 classifies every remaining backend "principal")
- [`ADG-UX-DEEP-TITLES-06-contextual-titles.md`](ADG-UX-DEEP-TITLES-06-contextual-titles.md)
- [`ADG-UX-RECENT-05B-recently-viewed.md`](ADG-UX-RECENT-05B-recently-viewed.md) (with the
  05A contract it implements)

## 1. Record

```
INTEGRATION_BRANCH=integration/ADG-UX-WAVE-C-01
WORKTREE_PATH=/Volumes/T5 EVO/mmm/worktrees/adg-UX-WAVE-C-01
BASE_SHA=10c73d1fcb5b12b31fa54e57b95da3483c079fba   (origin/integration/ADG-UX-WAVE-B-01)
COPY_SOURCE_SHA=9cec0b087b6d43b812b17d851fe7be2c8a24d787     (corrected head; brief named c04a3e8, see §4)
TITLES_SOURCE_SHA=c226516335d81cfd078578ffdc19d15736fa45ca   (code 014783a)
RECENT_SOURCE_SHA=44d1a3c0ca8460b2423688b82c858573820ae52e   (code 7393d03, report 9d6a68d)
MERGE_COPY=a1fcb6c4e9854fd26aa50b06b6845f9c69ba6229
MERGE_TITLES=4e08956726b1b868c0a8aa5f1469836f25629297
MERGE_RECENT=66f954897f1aae5c6c0e195b12baeac2bebea548   (last product-code commit)
GATE_SHA=2e677b1   (combined Wave C gate; test-only)
REPORT_SHA=52024ea722f1bcc2d44f6723d8261935b19e1689   (report + evidence; integration CI 41165819488 success)
FINAL_SHA=head of origin/integration/ADG-UX-WAVE-C-01 (the docs-only commit after 52024ea that adds §13's results; see "Next UX baseline")
```

**Baseline.** On 2026-10-31 (02:35Z), `git fetch --prune` found `10c73d1` still at the head of
`origin/integration/ADG-UX-WAVE-B-01`. The Wave B report names it the next UX baseline, and its
own CI run (41143770553) is green. Four remote refs descend from `10c73d1`: the three source
lanes, and the docs-only 05A design branch (`5a4c27b`), which is not an integration. No report
or branch declares a newer UX baseline. Each source branch has `10c73d1` as its merge base and
is 0 commits behind it.

## 2. Merges and conflicts

Order: COPY-04 → DEEP-TITLES-06 → RECENT-05B, as the brief proposes. Each is a `--no-ff` merge
commit. There was no squash, and no source branch was rebased.

| Overlap | Files | Result |
|---|---|---|
| COPY ∩ TITLES | `frontend/app/changes/page.tsx`, `frontend/app/governance/campaigns/[campaignId]/page.tsx` | auto-merged; both sides kept (checked by diff) |
| COPY ∩ RECENT | none | clean |
| TITLES ∩ RECENT | none | clean |

- **`app/changes/page.tsx`.** It keeps DEEP-TITLES' `generateMetadata`, which titles the
  object-history view `History — ADG` from the query alone, with no fetch. It also keeps
  COPY-04's scope line, which renders `identity: …` while the query parameter stays
  `principal=`.
- **`app/governance/campaigns/[campaignId]/page.tsx`.** It keeps DEEP-TITLES' static
  `Review campaign — ADG`. It also keeps COPY-04's `itemNames()`, which labels reviewer
  scopes and the items heading from the one items request the page already makes.
- **The overlaps the brief expected did not occur.** RECENT-05B's recording components sit in
  the Identity layout and the Server, Share and Directory pages. Neither other lane changed
  those files. RECENT-05B's Overview change (`app/page.tsx`) does not overlap either.

**Semantic conflicts.** None were found in product code. The committed Wave B gate
(`ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-b-check.mjs`) holds two expectations that
DEEP-TITLES-06 changed on purpose:
1. `/identities/users` titled `Identities — ADG`; it is now `Users — ADG`.
2. No route with `generateMetadata`; `/changes` now has the one URL-only title.

That historical gate stays unedited. Its committed form fails exactly those two checks (§12).
A Wave C copy updates only those two expectations
([`wave-b-check.wave-c.diff.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/wave-b-check.wave-c.diff.txt))
and passes 8/8.

**Attempt 1 (local only, never pushed).** The branch was first built with COPY-04 at `c04a3e8`
(merges `23e6e50`, `b9faa32`, `c9cf188`). The full local DB suite then failed one test because
of the lane (§4). After the source branch gained `9cec0b0`, the integration branch was rebuilt
from `10c73d1` in the same order. The two attempts differ only in that test line and in
COPY-04's report note. The `frontend/` tree (`e0def3a`) and the `backend/app` tree are
byte-identical between them. Every frontend and browser result below was produced on that same
frontend tree, so each one holds for the final head.
[`merge-summary.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/merge-summary.txt) has the hashes.

## 3. What the combined tree does

- **Backend identity wording.** The generic prose a user can reach says "identity". Examples:
  explain verdicts ("This identity holds rights on this object."), What-if vocabulary ("Add an
  identity to a group."), change rules, impact and error details. Windows terms of art stay
  (null-DACL "every principal", "the well-known SYSTEM principal", "foreign security
  principal", "User principal name"). So do identifier syntax and the OpenAPI/schema
  descriptions (COPY-04 §2.2–§2.4).
- **Readable identifiers.**
  - The explain "What a removal would do" table names each relationship ("Brianna Reddy is a
    member of Sales-Ops") from the explain response's own graph. The canonical edge id stays on
    the row (`data-removal-edge`) and in the export.
  - Campaign reviewer scopes are named from the page's one items request.
  - The reviews list shows kind labels, with an explicit "(name not loaded)" fallback.
- **Deep titles.** There are 12 contextual titles, and none carries an identifier.
  - Static: Alerts, Access investigation, Access explanation, Compare two points in time, Why
    access changed, Users, Groups, Review campaign, Review item, Propose a change, What-if
    proposal.
  - URL-derived: History.
  - Detail pages stay generic: Identity, Server, Share, Directory.
- **Recently viewed.**
  - Storage: `sessionStorage` key `adg.recentlyViewed.v1`, holding `{v, owner, entries}`, with
    the owner the SHA-256 of the subject.
  - Scope: Identity, Server, Share and Directory only; at most 10 entries, kept for 8 h.
  - Recorded only after a successful read; removed on not-found.
  - Cleared on account switch, and on sign-out before the logout request.
  - Identity labels never fall back to the UPN.
  - No endpoint, migration or request carries the list.
- **Overview.** Data health (top or lower slot), Needs attention, Recent changes and Reviews
  stream in their own Suspense slots. Then come Recently viewed, Where to start, and What ADG
  will not do. Recently viewed sits outside every slot and is in the first HTML.

## 4. Source admission (CI on the exact heads)

| Lane | Brief's final head | Run on that head | Result | Admitted head | Run on admitted head | Admitted |
|---|---|---|---|---|---|---|
| BACKEND-COPY-04 | `c04a3e8` | [41158333420](https://github.com/40ozs/adg/actions/runs/41158333420) | **failure**: Database tests 1 failed / 1297 passed (Lint, Type check, Unit tests, Frontend, Pester, Compose passed) | `9cec0b0` | [41164470341](https://github.com/40ozs/adg/actions/runs/41164470341) | **success** (Backend incl. DB 1298 passed, Frontend, Pester, Compose) → **YES** |
| DEEP-TITLES-06 | `c226516` | [41151516361](https://github.com/40ozs/adg/actions/runs/41151516361) | success (Backend, Frontend, Pester, Compose) | `c226516` | same | **YES** |
| RECENT-05B | `44d1a3c` | [41160309703](https://github.com/40ozs/adg/actions/runs/41160309703) | success (code `7393d03` 41156792963 and report `9d6a68d` 41157026959 also success) | `44d1a3c` | same | **YES** |

COPY-04's earlier runs were red at mypy: `e8fd2dd` (41154343454) and `6fbb7a1` (41158065459).
`6ec9c2b` fixed that, and on `c04a3e8` mypy passed. The run then reached Database tests, which
the lane had not run locally (its report §4).

**The defect.** `backend/tests/db/test_change_impact.py:384` asserted
`"principal" in impact.explanation`. That sentence is COPY-04's own §2.1 change, now
"…effective access is always about an identity as well." The test passes on `10c73d1` and
fails on `c04a3e8`. No other DB test pins changed wording: the full DB run had no other
wording failure, and a search of `tests/db` finds no other such assertion.

**The decision.** The brief forbids merging a red lane. It also allows a failed run to be
superseded by a corrected head. Fixing the lane meant adding a commit to another lane's branch,
so the owner was asked. The owner chose a corrected head over blocking the wave or patching the
assertion in the integration branch.

**The corrected head.** `9cec0b0` (test plus a CI note in the COPY-04 report) was pushed to
`feature/ADG-UX-BACKEND-COPY-04-identity-wording` as a fast-forward. The source branches were
otherwise untouched.
[`source-ci.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/source-ci.txt) lists every run and
job.

## 5. Request counts

[`request-count.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/request-count.txt) comes from
DEEP-TITLES' `request-count.mjs`, unchanged, run with
[19 pages](ADG-UX-WAVE-C-INTEGRATION-01-evidence/request-count.paths.json). Those are
DEEP-TITLES' 14 deeper pages plus the Overview, an identity, a server, a share and a directory.
Each page was requested as a document and as an RSC payload, comparing two builds through
recording proxies in front of the same API: the Wave B build of `10c73d1` and this branch's
build.

| Measure | Result |
|---|---|
| Measurements | 38 (19 pages × document/RSC) |
| Additional backend requests, Wave C vs Wave B | **0** (every page made the same calls) |
| Overview web-tier API calls | **9**, the same on both builds: `/auth/config`, `/runtime`, `/auth/me`, collection status, risk summary, risk rules, change summary, changes, governance queue |
| Additional requests from titles | **0** (the 12 retitled pages make the same calls as before) |
| Recently viewed, API reads | **0**: the Overview made the same 9 calls with an empty and a two-entry list (Wave C gate check 7); the four recorded detail pages make the same calls as Wave B |
| Recently viewed, API writes | **0**: no write path, endpoint or migration (contract-safety) |
| Backend-copy N+1 | **NO**: the campaign page makes 6 calls before and after; the explain page makes 5 before and after |

## 6. Backend gates

The integrated `backend/` tree equals `9cec0b0:backend`. That is COPY-04's tree, since no
other lane touched the backend. Python 3.13.15, from the canonical checkout's venv, with
`PYTHONPATH` set to this worktree. `app` imports from the worktree.
[`backend-gates.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/backend-gates.txt).

| Gate (CI's commands) | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass, 415 files already formatted |
| `mypy app tests` | pass, no issues in 414 source files |
| `pytest -q -m "not smoke"` | **6160 passed**, 12 skipped, 1299 deselected (attempt 1; the rebuild changed only a `tests/db` file, which this run deselects) |
| `pytest -q -m smoke` (DB, `ADG_RUN_SMOKE_TESTS=1`) | **1298 passed**, 1 xfailed, 0 failed (final head `66f9548`, cluster in UTC) |

**DB environment note.** The DB suite ran on a throwaway pgserver PostgreSQL 16.2 cluster,
separate from the demo database. The first full run (attempt 1) had 7 failures:

- One was the COPY-04 assertion (§4).
- Six were timestamp comparisons expecting UTC. initdb had written this Mac's zone,
  `America/Los_Angeles`, into the cluster's `timezone`, while CI's `postgres:16-alpine` runs in
  UTC. `TZ=UTC` for Python did not change them. Setting the cluster's `timezone=UTC` made all
  six pass.

The final run uses the UTC cluster.
[`db-tests-triage.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/db-tests-triage.txt) has the
three runs.

## 7. Frontend gates

Node 22.23.3 arm64, Next 16.4.1.
[`frontend-gates.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/frontend-gates.txt).

| Gate | Result |
|---|---|
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass |
| `npm run test` | **1691 passed**, 49 files |
| `ADG_NEXT_OUTPUT=standalone npm run build` | pass |

1691 is the measured count. For comparison, Wave B had 1594, and the lanes reported +2
(TITLES), +5 (COPY) and +90 (RECENT).

## 8. Browser gates (real local stack)

| Gate | Script | Result |
|---|---|---|
| Overview | `frontend/cloudflare/checks/overview.mjs` | **49/49** |
| Wave A integration | `ADG-UX-WAVE-A-INTEGRATION-01-evidence/integration-check.mjs` | **14/14** |
| Wave B integration, committed | `ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-b-check.mjs` | 6/8: the two DEEP-TITLES expectations (§2), as expected |
| Wave B integration, Wave C copy | `ADG-UX-WAVE-C-INTEGRATION-01-evidence/wave-b-check.wave-c.mjs` | **8/8** (run 1 before the second expectation was updated: 7/8, kept as `wave-b-check.wave-c.run1.txt`) |
| Titles (real API) | `frontend/cloudflare/checks/titles.mjs` | **15/15** (21-move journey, Back ×21, Forward ×21, 18 direct loads) |
| Backend copy | `ADG-UX-BACKEND-COPY-04-evidence/copy-check.mjs` | **17/17** |
| Recently viewed | `frontend/cloudflare/checks/recently-viewed.mjs` | **25/25** |
| Wave C combined (new) | `ADG-UX-WAVE-C-INTEGRATION-01-evidence/wave-c-check.mjs` | **9/9** |
| Title request count | DEEP-TITLES `request-count.mjs` | **0** additional over 38 |

Each result has a `.txt` log in the evidence directory, with the load average before and after.
Load averages ranged from 4 to 26, because other sessions shared the machine.

**The combined gate**
([`wave-c-check.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/wave-c-check.txt)) walks one
signed-in tab:

1. The Overview with the review-queue read delayed 4 s. h1 arrived at 35 ms; Needs attention,
   Recent changes and Data health at 351 ms; Recently viewed at 35 ms, in the first HTML and in
   no busy slot; Reviews at 4031 ms, after its read ended at 4028 ms. The order was Data health
   > Needs attention > Recent changes > Reviews > Recently viewed > Where to start > What ADG
   will not do.
2. Client navigation to a user. The page is titled `Identity — ADG` and has no generic
   "principal". The tab recorded `Nina Coleman`, with the owner digest and no `@`.
3. Changes, then "Compare two points in time". The deep title arrives on the client move, and
   the nav marks Changes.
4. Back ×2 and Forward ×2. Titles and nav state are restored, with no reload.
5. Back on the Overview, the identity is listed first and links to its page.
6. A server detail records. The access explanation is titled `Access explanation — ADG`. The
   API verdict "This identity holds rights on this object." is rendered. All 19 removal rows are
   named, the first being "Brianna Reddy is a member of Sales-Ops". No `principal:S-1-` text
   appears. Visiting the page does not change Recently viewed.
7. The Overview makes the same 9 web-tier API calls with an empty and a populated list. No API
   path relates to history. Of 185 browser and 55 web-tier requests scanned, none carried the
   storage key, owner digest, serialized entries or stored labels.
8. At 1440, 1024, 768, 375 and 320 px, the Overview with the list, the identity, the server and
   the explanation do not scroll sideways.
9. There were 0 page errors and 0 hydration errors. The one console error was the
   `/favicon.ico` 404, which the Wave B build also gives.

The only full document loads were the gate's deliberate direct loads. The campaign-item `<a>`
reload is not on this path; the titles gate covers and declares it.

## 9. Responsive checks

- **Combined gate (check 8).** At 1440, 1024, 768, 375 and 320 px, none of the Overview with
  the list, the identity detail, the server detail or the explanation scrolls sideways. The
  Recently viewed section has no inner overflow either.
- **Source gates, Wave B copy.** At the same five widths, the streaming Overview placeholder,
  Findings, a user and a server fit.
- **Source gates, Overview gate.** At 375 and 320 px, plus long-name fixtures.
- **Source gates, Recently viewed gate.** At 1440, 768, 375 and 320 px, including a
  200-character label.
- **Source gates, copy-check.** At 375 and 320 px: explain, What-if, reviews and campaign.
- **Raw ids no longer set the removal table's width.** The table keeps its existing
  `.table-scroll` container
  ([`explain-removal-table-width.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/explain-removal-table-width.txt)):
  - Wave B: raw `principal:S-1-… → group:S-1-…` labels made it 1455 px.
  - Wave C: readable labels make it 1006 px, so its internal scroll at 1440 px drops from
    +515 px to +66 px.
  - The page itself never overflows at any width, on either build.
- Screens are in [`screens/`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/screens/):
  - `recently-viewed-{1440,320}.png` and `explain-removals-1440.png` are element shots;
  - `w320-*.png` and `w375-overview.png` are narrow viewports;
  - `overview-midstream-1440.png` is the Overview mid-stream.

## 10. Contract safety

[`contract-safety.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/contract-safety.txt) and
[`terminology-sweep.txt`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/terminology-sweep.txt).

- **API/schema.** `docs/contracts` differs from Wave B only in the three captured
  `verdict.reason` example lines COPY-04 recorded. `openapi.json` and the derived schemas are
  unchanged.
- **Backend.** No route, `Field`, `Query`, alias, enum value, rule id or version line changed.
  `backend/app` is COPY-04's tree.
- **Graph ids, scope tokens and `principal=`.**
  - copy-check (API) shows that explain still returns `principal:<sid>` node ids and removal
    sources, and that vocabulary codes are unchanged. The Wave C gate shows the API still sends
    `principal:` removal sources.
  - `ScopeView {kind: "principal"}`, `ReviewScopeKind.PRINCIPAL` and `CampaignFocus.PRINCIPAL`
    are unchanged.
  - The `principal=` query parameter is unchanged on explain, changes and campaign items.
- **Rule version.** `redundant_access_paths` is still version `1.0`, titled "One identity
  reaches a resource by several independent routes". `catalog.py` and `rules.py` are unchanged
  since Wave B.
- **Migrations.** `database/` is identical to Wave B.
- **Recently viewed privacy.**
  - The only storage API product code added is `sessionStorage` (×3, all in RECENT-05B's
    store).
  - No `frontend/lib/api` or `frontend/app/api` file changed.
  - `identityLabel()` (display name → account name → last known name → SID, never the UPN) is
    intact. The Wave C gate checked the stored label has no `@`, and RECENT's gate checked the
    UPN is absent.
- **Terminology.**
  - COPY-04's AST scan of `backend/app` gives the same 111-entry inventory as COPY-04's
    "after" list, each entry already classified there.
  - Every frontend line added since Wave B that contains "principal" is a field or type name,
    the `principal` scope token or query parameter, or a comment. The one user-visible value is
    the label "Identity".
  - No generic user-facing prose was reintroduced.

## 11. Environment

| | |
|---|---|
| Demo database | pgserver PostgreSQL 16.2 on 127.0.0.1:55497. It is a copy of DEEP-TITLES' shut-down `adg_demo`: Wave B's Genesee Press data, plus the Overview gate's review campaign and DEEP-TITLES' one stored What-if proposal. COPY-04's `campaign-setup.py` added its two campaigns through the API before copy-check ran ([`campaigns.json`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/campaigns.json)). Local only |
| DB test cluster | Separate pgserver cluster on 127.0.0.1:55498 (role `adg`, database `adg` → `adg_test`, `fsync=off`, final run `timezone=UTC`) |
| API | `python -m app --port 8060` from this worktree, demo mode, development auth, throwaway secret |
| Web | This branch's standalone build: :3060, whose server reads go through the gates' recording proxy :8160, and :3061, direct to the API. Wave B's standalone build of `10c73d1` on :3062, through a proxy on :8162, for request counting |
| Browser | Google Chrome (/Applications), Playwright 1.62.1 via `NODE_PATH`, Node 22.23.3 arm64 |

No credentials, databases, browser profiles or `node_modules` are committed.

## 12. Scope

Integration only. No feature was added, and no product file was edited after the merges. The
items below remain separate lanes:

- review-scope `display_name`;
- campaign pagination and name resolution;
- the campaign-item `<a>`;
- server-side or `localStorage` history;
- hosted streaming;
- HTTP status codes;
- SVG tooltips;
- title and navigation architecture.

`main` was not touched. Nothing was deployed. No source branch or worktree was deleted.

## 13. CI on the integration head

The branch was pushed once, with the merges, the gate commit and the report. Run
[41165819488](https://github.com/40ozs/adg/actions/runs/41165819488) on `52024ea` (the code,
gate and report head) passed:

| Job | Result |
|---|---|
| Backend (lint, types, tests) | **success**: ruff, format, mypy; unit 6048 passed, 124 skipped, 1299 deselected; Database tests 1298 passed, 1 xfailed |
| Frontend (lint, types, tests, build) | **success**: vitest 49 files, **1691** passed; lint, typecheck, build |
| Collector (Pester) | success |
| Docker Compose configuration | success |
| Fork PR Linux gates | skipped (push event) |

The corrected COPY-04 head `9cec0b0` passed run
[41164470341](https://github.com/40ozs/adg/actions/runs/41164470341). Its Database tests
passed 1298 with 1 xfailed, where `c04a3e8` had 1 failed.

CI's unit count (6048 passed, 124 skipped) differs from the local run (6160, 12) only in
skips that depend on the platform. The DB counts and vitest counts match the local runs
exactly.

This section and the baseline below were added by a docs-only commit after both runs
completed. That commit's own CI run is not part of this record. It changes only this
report, so the verdict rests on run 41165819488.

## 14. Evidence

[`ADG-UX-WAVE-C-INTEGRATION-01-evidence/`](ADG-UX-WAVE-C-INTEGRATION-01-evidence/): merge and
CI summaries, backend, DB and frontend gate logs, every browser gate's output, request counts,
the combined gate and its Wave B companion, contract and terminology sweeps, removal-table
widths and screenshots.

## 15. Limitations carried forward

1. **Reviews list: identity scopes are not named.** Naming them needs an additive API
   `display_name` on `ScopeView`; until then the list shows "(name not loaded)" (COPY-04).
2. **Campaign page: names cover only the first 100 loaded items** (COPY-04).
3. **Stored simulation reports keep their old wording** (COPY-04).
4. **The campaign review-item link is a plain `<a>`, so that one move is a full reload**
   (DEEP-TITLES; `components/DecisionForm.tsx`).
5. **Some missing-object routes answer HTTP 200.** This covers unknown campaigns, items,
   proposals, identity views and resource detail pages, which render a not-found message. A
   malformed identity escape answers 500 from Next's router.
6. **The DEEP-TITLES journey depends on the date.** The demo changes end 2026-10-01, so the
   journey uses the 90-day window, and it breaks after about 2026-12-30 without a newer dataset
   or a time shift. It also needs a campaign with an item and a stored proposal.
7. **Access-explanation SVG tooltips still carry raw SIDs and UNC paths.** They are inline
   `<title>`s in the diagram, not the document title.
8. **Recently viewed is per tab.** A tab opened with an opener, or duplicated, starts with a
   copy.
9. **Restored tabs can keep `sessionStorage`**, bounded by the 8-hour expiry.
10. **A deleted object's Recently viewed label stays until its link is followed.**
11. **Hosted streaming is unverified.** This lane did not run the hosted Worker →
    `render/gate.ts` → Next.js path.

Observed and unchanged: the explain removal table still scrolls inside its own container at
desktop width (+66 px at 1440, down from +515 at Wave B). The page itself does not overflow.

## Next UX baseline

**`origin/integration/ADG-UX-WAVE-C-01` at its head**: the docs-only commit that adds this
section, directly on top of `52024ea`. Its code is identical to `52024ea`, which CI run
41165819488 validated.

FUTURE ADG UX WORK MUST BRANCH FROM THIS VERIFIED SHA UNLESS SUPERSEDED. That means the
branch's head as of this commit, not `main`, Wave B `10c73d1` or any individual feature
branch. If a later commit lands on this branch, check its report before branching from it.

## Status

`NOT MERGED TO MAIN` · `NOT DEPLOYED`
