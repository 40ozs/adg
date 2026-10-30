# ADG-UX-PERF-01 — Progressive Overview Rendering and Slow-Read Isolation

**Verdict: `READY_FOR_ADG_UX_PERF_INTEGRATION_WITH_LIMITATIONS`**

NOT MERGED. NOT DEPLOYED.

| | |
|---|---|
| Lane | ADG UX Wave B, rendering/performance |
| Branch | `feature/ADG-UX-PERF-01-progressive-overview` |
| Starting baseline | `origin/integration/ADG-UX-WAVE-A-01` @ `fcb792872363c131d2539bab1f629c5d5120445e` |
| Baseline check | Fetched 2026-10-30; `fcb7928` is the branch head. No newer validated Wave B baseline exists in the repository (no other Wave B branch on the remote; `ADG-UX-WAVE-A-INTEGRATION-01.md` § Wave B baseline still names `fcb7928`) |
| Files changed | `frontend/app/page.tsx`, `frontend/tests/overview-page.test.tsx`, `frontend/cloudflare/checks/overview.mjs`, this report and its evidence |
| Backend | Unchanged |

## 1. Architecture before

`app/page.tsx` (a Server Component) resolved the viewer, then awaited **one `Promise.all`** over the
seven reads (collection status, `/runtime`, findings summary, rules, change summary, change feed,
review queue), then rendered every section from the results. There was no Suspense boundary and no
`loading.tsx` anywhere in the app (a route `loading.tsx` was tried for ADG-DEMO-03 and broke
search-parameter navigations; see `components/ComparisonLink.tsx`). React therefore could not
flush any HTML — not even the shell, whose own reads (`/auth/me`, `/auth/config`, `/runtime`) are
fast — until the slowest Overview read settled.

Wave A measured it: a 4 s change summary gave a 4392 ms TTFB, a 12 s one a 10121 ms TTFB (the 10 s
client timeout in `lib/api/client.ts`).

## 2. Architecture after

The smallest change that isolates sections: **in-page Suspense boundaries, one per section, in the
existing page.** No new route files, no client components, no browser-side reads, no changes to
`components/Overview.tsx`, the API layer, the shell or the layout.

1. The page still resolves the viewer first (needed for the token and capabilities, and already
   resolved by the shell through the same React-cached `currentViewer`).
2. It then **starts every permitted read at once, without awaiting** — the same seven calls behind
   the same capability checks. A read the account cannot open is never created, so it is never sent.
3. Each section is a small slot component that reads only its own promises with React 19 `use()`,
   inside its own `<Suspense>`. The server streams the shell, `h1`, role banner, *Where to start*
   and *What ADG will not do* immediately; each slot streams in when its reads settle.
4. Failure and timeout handling did not need to change: `apiRequest` never throws — a timeout or
   HTTP failure is an `ApiResult`, which lands in the section's existing `StateMessage`.

`use()` rather than async section components: it keeps the page testable under jsdom
(`render(await OverviewPage())` plus an `act` flush), and promises created in the page body before
render are stable, so no re-request on retry.

### Rendering boundaries

| Boundary | Waits for | Fallback |
|---|---|---|
| Shell, `h1`, role banner, Where to start, What ADG will not do | viewer only | — (rendered immediately) |
| Data health (top slot) | collection status, findings summary (if `risks:read`), `/runtime` (cached, already resolved by the shell) | "Data health — Loading collection status…" |
| Needs attention | findings summary, rules | "Needs attention — Loading findings…" |
| Recent changes | change summary, change feed, **collection status** (an empty window is worded against collection) | "Recent changes — Loading changes…" |
| Reviews | review queue | "Reviews — Loading your review queue…" |
| Data health (bottom slot) | same promises as the top slot | none |

### Data health ordering

Wave A's rule is unchanged (`evidenceNeedsAttention`: collection not healthy, status read failed,
or findings totals not complete → Data health first). Because the decision needs data, Data health
has **two slots and renders in exactly one**: each slot evaluates the rule on the same results and
renders only if it is the right position. Final DOM order is therefore exactly Wave A's, in every
state (gate: demo order, healthy-fixture order, 503 promotion, reading order at 1440/375).

While the decision is pending, the **top** slot holds the placeholder — the caveat is assumed to
come first until it is known not to. In a degraded estate (the demo dataset) the placeholder is
filled in place. In a healthy estate the top placeholder collapses and Data health appears in its
ordinary position below Reviews: one upward shift of one short card, once.

### Loading presentation

A placeholder is the section's own card with its real heading (so the region is already named) and
one muted sentence, "Loading …". `aria-busy="true"` on the placeholder region; no live region (a
section arriving is not an alert, and four announcements per load would be noise). No skeleton, no
number, no progress figure, nothing that reads as empty (empty states are worded by `classify`),
nothing that reads as failure (failures are `role="alert"` via `StateMessage`, timeouts say
"ADG did not answer in time"). Loading is stated in words, not colour.

## 3. Request behaviour and permissions

Unchanged from Wave A, verified at the HTTP level by the gate's recording proxy:

- fan-out: `/auth/config×1 /runtime×1 /auth/me×1` + six page reads ×1 each; the six start 19 ms in,
  after `/auth/me`, within 1 ms of each other; no waterfall, no duplicates, no table reads.
- `/runtime` once per render (React `cache`, shared with the shell); the slots reuse the page's
  promise.
- every progressive check also asserts 7 reads for a full-capability account.
- permissions: planner/auditor 7 reads (3 sections), viewer 6 (no review queue request), `nobody`
  2 (`/collection/status` → 403, `/runtime`); hidden sections are not rendered, have no placeholder
  and send no request. Unit test "shows no placeholder, and sends no read, for a section the account
  cannot open" covers it too.
- No fetch loops: reads happen on the server once per render; the client receives HTML/RSC only.
- No data for unauthorized sections can reach the stream: their promises are never created.

## 4. Timings

Browser milestones are recorded by a `MutationObserver` init script: the moment the shell header,
the `h1`, and each **real** section (not its `…-pending` placeholder) are inside `#main`. Read ends
come from the recording proxy, converted to the same clock (`performance.timeOrigin`). All ms from
navigation start, at 1440 px, Chrome 155 headless. The Mac's load average was 21–44 during the run
(`overview-gate.load.txt`), so absolute numbers are noisy; the assertions are relative.

### Normal case (all reads healthy, admin, 3 runs)

| Run | Shell / h1 | First section | Needs attention | Recent changes | Reviews | Data health | Slowest read ends |
|---|---|---|---|---|---|---|---|
| 1 | 97 | 455 | 755 | 455 | 455 | 755 | 461 |
| 2 | 105 | 197 | 197 | 197 | 197 | 197 | 173 |
| 3 | 279 | 317 | 618 | 317 | 317 | 618 | 425 |

Warm TTFB median 90 ms (Wave A: 185 ms), because the shell no longer waits for the reads.

### 4-second slow section (one read delayed 4000 ms by the proxy)

| Delayed read | Shell / h1 | Independent sections visible | Slow section visible | Slow read ends |
|---|---|---|---|---|
| `/api/v1/governance/queue` (planner) | 94 | Recent changes 181; Needs attention, Data health 635 | Reviews 4113 | 4086 |
| `/api/v1/changes/summary` (planner) | 285 | Reviews 384; Needs attention, Data health 685 | Recent changes 4350 | 4326 |
| `/api/v1/risks/summary` (admin) | 140 | Recent changes, Reviews 309 | Needs attention **and Data health** 4184 | 4176 |

Before (Wave A): nothing at all until ~4392 ms TTFB.

### >10-second timeout (change summary delayed 12 s; client timeout 10 s)

Shell/h1 31 ms; Needs attention, Reviews, Data health 357 ms; Recent changes 10088 ms showing
"ADG did not answer in time" (`role="alert"`). Before: nothing until 10121 ms TTFB.

## 5. Failure isolation

| Case | Where verified | Result |
|---|---|---|
| Slow Findings | unit + gate (4 s) | Needs attention and Data health wait (Data health at top as placeholder); Recent changes, Reviews usable |
| Failed Findings | unit (unreachable) + gate (HTTP 500) | error in Needs attention only; no zero; other sections ready |
| Timed-out Findings | unit | timeout in Needs attention only; no zero; Data health shows no "Total" |
| Slow Reviews | unit + gate (4 s) | Reviews alone waits |
| Failed Reviews | unit + gate (HTTP 500) | error in Reviews alone, every other section ready |
| Slow Changes | unit (feed) + gate (summary, 4 s) | Recent changes alone waits |
| Failed Changes | unit | error in Recent changes only |
| Timed-out Changes | gate (12 s) | timeout in Recent changes only, others at ~0.36 s |
| Slow collection status | unit | Data health (top placeholder) and Recent changes wait; Findings and Reviews ready |
| Failed collection status | unit + gate (HTTP 503) | Data health promoted with its error, the rest useful |

No case crashes the page, blanks an unrelated section, turns unknown into zero or failure into
healthy.

## 6. Accessibility

- One `h1` (also asserted mid-stream); every section a named region in both states (placeholder
  carries the real heading; ids are distinct, `overview-*-pending`).
- DOM order = Wave A order in every final state; reading-order checks at 1440 and 375 pass.
- Focus: a link focused in Needs attention while Reviews was still loading kept focus after Reviews
  arrived (gate). Placeholders contain nothing focusable, so a reveal never removes a focused node.
- `aria-busy` on placeholders only; no new live regions. Existing `role="status"`/`role="alert"`
  semantics in the sections are unchanged.
- Existing gate a11y checks (descriptive links, no nested links, keyboard order with focus rings)
  pass.

## 7. Responsive

1440, 1280, 1024, 768 (side by side), 375, 320 (stacked): no sideways scroll, no overlap; long-name
fixtures at 320/1440 in bounds; Data health mobile `dl` stacking unchanged (CSS untouched).
Placeholders reuse `.card` and sit in the same `.pair` grid cells as the sections they stand for.

## 8. Tests and gates

| Gate | Result |
|---|---|
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass |
| `npx vitest run` (full) | **1555/1555**, 45 files (Wave A 1544 + 11 new) |
| `ADG_NEXT_OUTPUT=standalone npm run build` | pass (Next 16.4.1, webpack) |
| Overview browser gate `cloudflare/checks/overview.mjs` | **49/49** (Wave A 43; −1 replaced, +7 progressive) |

New unit tests (`tests/overview-page.test.tsx`, "progressive rendering (ADG-UX-PERF-01)"): normal
render; slow Reviews (then settles); slow Findings (Data health placeholder at top, then moves to
its ordinary place); slow collection status; slow change feed; failed Reviews/Changes/Findings;
timed-out Findings; hidden sections have no placeholder and no read; every read starts once before
any section renders. **Red before:** against the baseline `app/page.tsx` the six slow-read tests
fail (the baseline `Promise.all` never renders); the rest pass on both, as those behaviours
pre-existed.

Gate changes: the Wave A check "a 4 s changes summary delays the page…" (which documented the
problem) is replaced by milestone checks; `overview()` now waits until no `aria-busy` placeholder is
left, because React 19.2 reveals streamed boundaries on an animation frame and throttles further
reveals to ~300 ms apart, so the last reveal can land after `load`. Without that wait 16 checks read
placeholders.

## 9. Limitations

1. **Data health shares the findings read.** It shows the findings evaluation and is placed by it,
   so slow Findings delay Data health too (up to the 10 s timeout). Splitting its findings rows into
   a nested boundary is possible but would need a placement rule while totals are unknown; not done.
2. **Recent changes shares the collection-status read**, which words an empty window. Slow status
   delays Recent changes.
3. **Healthy-estate shift:** when Data health resolves to its ordinary position, the top placeholder
   collapses (one short card, once). The demo dataset is degraded, so it fills in place there.
4. **Brief placeholders in the normal case.** The shell now flushes before the reads return, so
   placeholders can show for a few hundred ms, and React's reveal throttle can add up to ~300 ms
   before the last section appears (normal run 1: slowest read 461 ms, last section 755 ms). TTFB and
   shell are earlier; "everything visible" in a healthy load is roughly unchanged, not faster.
5. **Streaming through the hosted path is not verified.** Measured on the local standalone server
   behind the gate's proxy. `render/gate.ts` and the Cloudflare Worker relay responses, but whether
   either buffers a streamed body was not tested (no deploy in scope).
6. Absolute timings come from a shared, loaded Mac (load 21–44); the relative assertions held.
7. The one pre-existing console 404 (planner) is in Wave A's run too.

### Possible future optimization (not implemented)

A backend aggregate (`/api/v1/overview` returning the six summaries) would cut six server-to-API
round trips to one, but it would re-couple sections to the slowest summary unless the backend
itself streamed. Progressive rendering already gives isolation; aggregation is only worth revisiting
if the API's per-request overhead, not one slow summary, dominates.

## 10. Integration notes

- `app/page.tsx` is the only product file touched. If ADG-UX-TITLES-03 adds `export const metadata`
  (or `generateMetadata`) to it, expect a trivial import/top-of-file conflict; nothing else overlaps.
- `components/Overview.tsx`, `overview.module.css`, `globals.css`, nav, shell and layout are
  untouched, so ADG-UX-ENTITY-02 terminology work does not overlap.
- Do **not** add a route `loading.tsx` for `/` on top of this: it would wrap the whole page and
  bring back the search-parameter navigation issue noted in `ComparisonLink.tsx`.
- Any caller of the gate's `overview()` helper now waits for placeholders to settle.

## 11. Environment

| | |
|---|---|
| Database | Throwaway Postgres 16.2 (pgserver binaries) on 127.0.0.1:55437: a copy of the Wave A integration lane's cleanly shut-down `adg_demo` (Genesee Press, reset fingerprint `9c8177e2…`, see `ADG-UX-WAVE-A-INTEGRATION-01-evidence/demo-reset.txt`); no new reset |
| API | `python -m app`, demo mode, development auth with a throwaway secret (backend identical to the baseline) |
| Web | `ADG_NEXT_OUTPUT=standalone next build --webpack`, `server.js` on 127.0.0.1:3000 with `render/gate.ts`'s web environment, `ADG_INTERNAL_API_URL` → gate proxy :8100 |
| Browser | Chrome 155 (`/Applications`), Playwright 1.62.1, Node 22 arm64 |

## 12. Evidence

`ADG-UX-PERF-01-evidence/`

- `overview-gate.txt` — gate output, 49/49
- `overview-gate.json` — results and measurements (`progressive.normal`, `delay_4s_*`,
  `timeout_12s`, `fanout`, `timing`, `permissions`, `responsive`)
- `overview-gate.load.txt` — machine load around the run
- `frontend-gates.txt` — lint, typecheck, cf:typecheck, full suite, build, red-before check
- `screens/progressive-4s-*-midstream-1440.png` — the page mid-stream with one read held 4 s
- `screens/overview-*.png` — final-state demo, healthy fixture, responsive and long-name screens
