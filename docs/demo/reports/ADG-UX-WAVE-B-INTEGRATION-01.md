# ADG-UX-WAVE-B-INTEGRATION-01 — UX Wave B integration

**Verdict: `READY_FOR_ADG_UX_NEXT_WAVE_WITH_LIMITATIONS`**. CI is recorded in §9 and the
limitations in §11.

PERF-01 (progressive Overview), ENTITY-02 (identity terminology) and TITLES-03 (document titles)
are merged into one tree, `integration/ADG-UX-WAVE-B-01`, on top of the Wave B baseline. One
textual conflict came up, in the import block of `frontend/app/page.tsx`, and both sides were
kept. The only change on top of the merges is in a test harness: the Wave A integration check
now waits for the Overview's streamed sections. On the combined tree, every frontend gate
passes, the bounded backend suites pass, all three lanes' browser gates pass, the Wave A
integration check passes, and so does a new cross-lane check, all run against the real local
Genesee Press stack. The branch is not merged to `main` and nothing was deployed.

The source reports hold each feature's full evidence; this report does not repeat it:

- [`ADG-UX-PERF-01-progressive-overview.md`](ADG-UX-PERF-01-progressive-overview.md)
- [`ADG-UX-ENTITY-02-identity-context.md`](ADG-UX-ENTITY-02-identity-context.md) (§2 is the
  preserved/deferred `principal` classification)
- [`ADG-UX-TITLES-03-document-titles.md`](ADG-UX-TITLES-03-document-titles.md)

## 1. Record

```
INTEGRATION_BRANCH=integration/ADG-UX-WAVE-B-01
WORKTREE_PATH=/Volumes/T5 EVO/mmm/worktrees/adg-UX-WAVE-B-01
BASE_SHA=fcb792872363c131d2539bab1f629c5d5120445e   (origin/integration/ADG-UX-WAVE-A-01, fetched 2026-10-30)
PERF_SOURCE_SHA=9a7a2a51860c0d31a9cdfa04a75c2b20a0655b94     (code 66d940926a4b733a0da0bc3f6f55eeca0306cbf7)
ENTITY_SOURCE_SHA=a0a54de4fd2e1a2453b6ab6ae1268b043ca2806f   (code 51419e32647c3819f955036ca28e2496214cdc50)
TITLES_SOURCE_SHA=76e3c46e4e5b3d599ee6583fb1fcf5f81e19ada2   (code ccee9c76d72c14a3188f12c0d9036823606f2e38)
MERGE_PERF=6017acb0b5f83bb3620eedb48597f2db7ce86748
MERGE_ENTITY=0478a34b7cf734a89837d36c159cb961698c6945
MERGE_TITLES=88e5d633dcfe2a218b143e3cbd824dfca9b75717   (last product-code commit)
HARNESS_FIX=a4988a5   (Wave A integration check waits for streamed sections; test-only)
FINAL_SHA=see "Next UX baseline" below
```

**Preflight.** `fcb7928` is the head of `origin/integration/ADG-UX-WAVE-A-01`, and the Wave A
report names it as the Wave B baseline. No repository-owned report sets a newer baseline, and no
`integration/ADG-UX-WAVE-B-*` ref existed before this lane. All three source branches are on
`origin` at the expected heads and match their local branches. Each has `fcb7928` as its merge
base and holds two commits (code, then report). The PERF and TITLES worktrees are clean; the
ENTITY worktree's only untracked entry is its `frontend/node_modules` symlink (ENTITY report,
setup note). No newer source head exists.

## 2. Merges and conflicts

Order: PERF-01 → ENTITY-02 → TITLES-03, `--no-ff` (the Wave A convention), with no duplicate
ancestry to replay.

| Overlap | Files | Result |
|---|---|---|
| PERF ∩ ENTITY | none | clean |
| ENTITY ∩ TITLES | none | clean |
| PERF ∩ TITLES | `frontend/app/page.tsx` | import-line conflict, resolved by keeping both |

**`app/page.tsx`.** PERF-01 added `import { Suspense, use, type JSX } from "react"` on the
first line, and TITLES-03 added `import type { Metadata } from "next"` there too. Both are
kept. Git merged TITLES' other two changes automatically: the `sectionTitle` import, and
`export const metadata: Metadata = { title: sectionTitle("overview") }`. The resolved file is
PERF's file plus exactly those three TITLES lines (`git diff 9a7a2a5 HEAD -- frontend/app/page.tsx`).
The per-section Suspense boundaries, the `use()` slots, the permission-gated reads and the
two-slot Data health are unchanged. No route `loading.tsx` exists.

**Exact union.** Every file that a lane changes, apart from `page.tsx`, is byte-identical to that
lane's head: PERF 19/19 files, ENTITY 54/54, TITLES 25/25. The backend tree
(`e56778d6…`) is identical to ENTITY-02's.

**Semantic coordination.**

- ENTITY × TITLES: ENTITY changes `identities/principal/[key]/[[...view]]/page.tsx`, and TITLES
  adds `identities/principal/layout.tsx`. The page exports no metadata, so the generic
  `Identity — ADG` stands. Nothing in `app/` uses `generateMetadata`, so no route reads data for
  its title.
- ENTITY × PERF: ENTITY touches no Overview file (`page.tsx`, `Overview.tsx`,
  `overview.module.css`), so no read or component moved across a Suspense boundary.

## 3. Terminology and backend catalog

- User-facing generic copy keeps ENTITY-02's 69 frontend strings and 7 catalog prose strings,
  each byte-identical to the source.
- `redundant_access_paths` is titled **"One identity reaches a resource by several independent
  routes"**. Its rule id and `version="1.0"` are unchanged. Only `catalog.py` and its test
  contain the title, so no filter depends on it. `/risks?rule=redundant_access_paths` still
  selects the rule (ENTITY gate).
- The backend diff from the baseline touches two files: catalog prose strings and a model test
  (`git diff fcb7928 HEAD -- backend`). No evaluation, database, migration or API logic
  changed, so the full database suite was not needed. ENTITY-02's demo reset with this same
  backend tree reproduced fingerprint `9c8177e2…` and 9,386 open findings
  ([`demo-reset.txt`](ADG-UX-ENTITY-02-evidence/demo-reset.txt)). This lane ran no new reset.
  It used a copy of that reset's database, which still holds 9,386 open findings.
- Preserved on purpose: `/risks`, `risks:read`, `principal=`, API fields, types, rule ids and
  versions, and Windows terms of art (*foreign security principal*, *user principal name*).
- Deferred, unchanged: backend explanation and simulation prose ("This principal holds rights
  on this object.", "Take a principal out of a group."), `principal:<sid>` graph ids, and review
  scope tokens. The ENTITY gate reports the two explanation strings it meets on
  `/access/investigate` and `/access/explain` as deferred.
- The Wave A integration check, which scans for retired terms, now finds **no** backend rule
  title that uses one. Wave A had reported the `redundant_access_paths` title.

## 4. Progressive Overview, requests and permissions

All results come from `overview.mjs` (49/49), run through its recording proxy. Times are in ms
from navigation start, on the local stack, under load average 53–70 (another session's iOS
Simulator, see [`overview-gate.load.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/overview-gate.load.txt)).

| Case | Shell / h1 | Sections visible | Delayed section |
|---|---|---|---|
| All reads healthy (3 runs) | 45–51 | all four at 352–373 | — |
| 4 s `/governance/queue` | 34 | Changes 357; Attention and Data health 657 | Reviews 4055 (read ends 4048) |
| 4 s `/changes/summary` | 87 | Reviews 129; Attention and Data health 431 | Recent changes 4123 (read ends 4119) |
| 4 s `/risks/summary` | 32 | Changes and Reviews 348 | Needs attention and Data health 4074 (read ends 4063) |
| 12 s `/changes/summary` (timeout) | 31 | others 343 | Recent changes alone times out at 10049 |
| 500 `/governance/queue` | — | others render | Reviews alone shows the error |

- **Focus.** Focus is placed on a Needs attention link while Reviews is still streaming, and it
  stays on that link after Reviews arrives (overview gate, and the cross-lane check §6).
- **Data health ordering.** On the demo dataset (collection failed), Data health renders first.
  With a healthy + complete fixture it renders in its normal position. A healthy collection
  with incomplete totals promotes it, and so does a collection-status 503. It renders exactly
  once with no leftover placeholder (cross-lane check), and the DOM order matches the visual
  order at 1440 and 375 px.
- **Requests.** One `/runtime` per render, shared with the shell. The six page reads start
  together after `/auth/me`, spread over 2 ms. Each summary endpoint is read once, with no
  duplicates and no table reads. `/runtime` is also read once per render on `/demo` and
  `/access/investigate` (Wave A check).
- **Permissions.** planner and auditor: Needs attention, Recent changes and Reviews, 7 reads.
  viewer: no Reviews section and no `/governance/queue` request, 6 reads. `nobody`: no
  sections, 2 reads (no Findings, Changes or Reviews request).

## 5. Titles

- Primary titles, each confirmed by a server `<title>` on direct load, by client navigation
  through every nav item, and by Back/Forward, all against the real dataset: Overview,
  Findings, Access, Changes, Identities, Resources, Reviews, What-if, Collectors and Settings,
  each followed by ` — ADG`. `/risks?severity=critical` is titled `Findings — ADG`, and an
  unknown path returns HTTP 404 titled `Page not found — ADG`.
- Detail titles are `Identity — ADG`, `Server — ADG`, `Share — ADG` and `Directory — ADG`, the
  same in the server `<title>` and in the document. None contains a SID, GUID, DN, UNC or drive
  path, or a `principal:` token. The ENTITY changes did not make these titles object-specific.
- All 20 metadata titles in the 46 route files are literals or `documentTitle("…")` /
  `sectionTitle("…")` calls, and nothing uses `generateMetadata`. No request exists for a
  title.
- TITLES-03 validated against a stub API. This lane re-ran `titles.mjs` against the real
  Genesee stack and got 10/10.

## 6. Browser validation (real local stack)

| Gate | Result | Output |
|---|---|---|
| PERF / Overview `frontend/cloudflare/checks/overview.mjs` | **49/49** | [`overview-gate.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/overview-gate.txt), `.json` |
| ENTITY `ADG-UX-ENTITY-02-evidence/entity-check.mjs` | **14/14** | [`entity-gate.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/entity-gate.txt), `.json` |
| TITLES `frontend/cloudflare/checks/titles.mjs` (real API, not the stub) | **10/10** | [`titles-gate.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/titles-gate.txt) |
| Wave A `ADG-UX-WAVE-A-INTEGRATION-01-evidence/integration-check.mjs` | **14/14** (13/14 before the settle fix) | [`wave-a-integration-check.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-a-integration-check.txt), [`…before-settle.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-a-integration-check.before-settle.txt) |
| New cross-lane check [`wave-b-check.mjs`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-b-check.mjs) | **8/8** | [`wave-b-check.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-b-check.txt), `.json` |
| Nested interactive / one h1, 7 pages | **pass** | [`nested-interactive-check.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/nested-interactive-check.txt) |

**Wave A check, 13/14 → 14/14.** The first run failed "no 'Findings evaluation'". At `load`,
2–4 Overview sections were still `aria-busy` placeholders, and once they settled the text was
present on 3/3 loads. The `<dt>` line is unchanged since the baseline. The cause is PERF-01's
known reveal-after-`load` behavior, which PERF fixed in its own gate with `settled()`. The fix
(`a4988a5`) adds the same wait after each Overview load and changes no assertion.

**Cross-lane check (8/8).** It runs as one `admin` session at 1440 px:

1. The Overview with a 4 s review-queue read is already titled `Overview — ADG` when its h1
   appears. h1 at 170 ms, Changes 519, Attention and Data health 820, Reviews 4158 (read ends
   4155). Focus stayed on a Needs attention link, and Data health rendered once.
2. Nav to Findings: `Findings — ADG`, the identity rule title, no reload.
3. Nav to Identities → Users → a user: `Identity — ADG`, h1 "… user", no generic `principal`
   in the text or accessible names, no reload.
4. Back ×4 to the Overview, then Forward: every title restored, no reload.
5. Server, Share, Directory and Identity titles are generic and carry no identifier.
6. All titles are static metadata.
7. 1440/1024/768/375/320 px: no sideways scroll for the streaming Overview (placeholder
   present), Findings, a user or a server.
8. No page errors and no hydration errors.

**Accessibility (automated only).** One h1 on every page checked. The Overview regions are
named. Tab order runs skip link → 10 nav links → body, with a visible focus ring. Streaming
does not steal or reset focus. Statuses use a word plus a symbol (✕ Failed, ◐ Incomplete,
✓ Complete, ∅ Never collected). There are no nested links or buttons, and titles update on
navigation. **No manual VoiceOver or NVDA pass was run.**

**Console.** The only console error in every gate is `/favicon.ico` → 404. It is already present
at the baseline, which has no favicon, and Wave A recorded it too.

## 7. Frontend and backend gates

| Gate | Result |
|---|---|
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass |
| `npm test` (vitest) | **1594 passed** (46 files) |
| `npm run build` (Next 16.4.1, webpack) | pass |
| `ADG_NEXT_OUTPUT=standalone` build (served the browser gates) | pass |
| `ruff check` / `ruff format --check` | pass (414 files) |
| `pytest` risk_engine, alerts, api, contracts, demo, demo_mode | **1493 passed**, 28 skipped (DB-gated), the same as ENTITY-02 |

The vitest count comes from the run itself, not from adding up the source counts. No test
needed semantic reconciliation, because no test file is shared between lanes. Outputs:
[`frontend-gates.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/frontend-gates.txt),
[`backend-gates.txt`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/backend-gates.txt).

## 8. Environment

| | |
|---|---|
| Database | Throwaway Postgres 16.2 (pgserver binaries) on 127.0.0.1:55457, a copy of ENTITY-02's cleanly shut-down `adg_demo` (same backend tree, fingerprint `9c8177e2…`). The overview gate seeded one review campaign in it, as its policy allows |
| API | `python -m app` on :8020 from this worktree's backend, demo mode, development auth with a throwaway secret |
| Web | One standalone build, two `server.js` instances: :3020 → recording proxy :8120 (overview, Wave A and cross-lane checks), :3021 → API (ENTITY, TITLES, nested check) |
| Browser | Chrome 156 (`/Applications`), Playwright 1.62.1, Node 22.23.3 arm64 |

No secrets, databases or `node_modules` are committed.

## 9. CI

| Run | Head | Result |
|---|---|---|
| ENTITY-02 source `41129249976` | `a0a54de` | queued at handoff; see "Next UX baseline" |
| PERF-01 source `41128664235` | `9a7a2a5` | in progress at handoff |
| TITLES-03 source `41128800868` | `76e3c46` | queued at handoff |
| Integration | this branch | see "Next UX baseline" |

The source runs sat queued because the single `k3s-adg` ARC scale set (max 2 runners) only came
back online at about 21:22Z and was still working through the Wave A and PERF runs. The
Collector (Pester) job, which runs on GitHub-hosted runners, passed for ENTITY.

## 10. Scope

- Recently Viewed was not implemented: no storage of any kind was added.
- The remaining backend `principal` prose and raw identifiers are deferred (§3).
- No claim is made about hosted streaming (§11).
- Not merged to `main`. Not deployed.

## 11. Limitations carried forward

- **PERF:** progressive streaming through Cloudflare Worker → `render/gate.ts` → Next.js is
  **unverified**. This is a release acceptance gate: the hosted release must prove that the edge
  and the gate do not buffer the stream. Data health waits on Findings, and Recent changes waits
  on collection status. A healthy estate may shift once when Data health leaves its placeholder
  position, and placeholders can flash briefly on fast loads.
- **ENTITY:** deeper backend explanation and simulation prose still says `principal`.
  `principal:<sid>` graph ids and review scope tokens are visible in specific technical places.
  No manual screen-reader pass.
- **TITLES:** deep subpages inherit their section's title. A bad view of a known identity still
  returns HTTP 200. Detail titles are generic, not object-specific. The earlier stub-only
  browser limitation is closed: this lane ran `titles.mjs` against the real dataset.
- **Integration:** all browser checks ran locally under heavy machine load. Timings are local
  and not representative of hosted performance.

None of these got worse when the lanes were combined.

## 12. Evidence

[`ADG-UX-WAVE-B-INTEGRATION-01-evidence/`](ADG-UX-WAVE-B-INTEGRATION-01-evidence/)

- `frontend-gates.txt`, `backend-gates.txt`
- `overview-gate.txt` / `.json` / `.load.txt`
- `entity-gate.txt` / `.json`, `titles-gate.txt`
- `wave-a-integration-check.txt` / `.json`, `wave-a-integration-check.before-settle.txt`
- `wave-b-check.mjs` / `.txt` / `.json`
- `nested-interactive-check.mjs` / `.txt`
- `screens/`: Overview mid-stream (4 s Reviews, Changes and Findings, 1440; Reviews at 320),
  Overview settled, the demo Overview at 1440/320, a server at 320, the user identity, the
  group and share pages, and the Wave A nav and 375 Overview
