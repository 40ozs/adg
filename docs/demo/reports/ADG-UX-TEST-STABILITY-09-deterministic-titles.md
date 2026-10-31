# ADG-UX-TEST-STABILITY-09 — deterministic DEEP-TITLES browser journey

**Verdict: `READY_FOR_ADG_UX_TEST_STABILITY_INTEGRATION_WITH_LIMITATIONS`**

The titles gate (`frontend/cloudflare/checks/titles.mjs`) no longer depends on today's date. It
passes 15/15 on the real local stack at three simulated dates, 2026-10-30, 2027-06-01 and
2028-01-01. A negative control without the fix fails at 2028-01-01 in exactly the old way.
No product file changed. The run also exposed a second dependence that was not about dates: the
gate opened whichever review campaign was newest. That is fixed too (§3). The limitations are
in §10.

```
BASELINE   origin/integration/ADG-UX-WAVE-C-01 @ 559dbaf913a02e0df4ddf18be89ef81f03e48596
           (CI 41167906074 success: Backend, Frontend, Pester, Compose; no newer integration
           branch; NAV-07 70d08e7 and REVIEW-NAMES-08 6877039 are feature lanes on top of it)
BRANCH     test/ADG-UX-TEST-STABILITY-09-deterministic-titles
```

## 1. Root cause: where the date comes in

| Link in the chain | Depends on the date? | What it does |
|---|---|---|
| Synthetic records | No: fixed timestamps | The Genesee Press changes end 2026-10-01. |
| Changes page (`app/changes/page.tsx:78-81`) | **Yes** | `now = new Date()` on the Next.js server; `from = now − days`. `days` is one of 1/7/30/90 (`RANGES`, default 7). There is no wider window and no explicit date. |
| API (`/api/v1/changes`, `/api/v1/changes/summary`) | Uses its input | It filters on the `from`/`to` it is given, and both are required. It does not read the clock for this. |
| History view (`?kind=&key=`) | No | Its only read is `/api/v1/changes/timeline` (kind, key, limit), with no window. |
| Impact link (`/changes/impact?kind&key&at`) | No | The `at` value is the change's own timestamp. |
| Gate navigation | **Yes, through the page** | The journey clicks `/changes?days=90`, then a row's `?kind=` (History) link and a row's `/changes/impact?` link. With no rows in the window, those links do not exist. |
| Fixture setup | No | The gate wrote nothing and created nothing. |

So the dependence is the **current-date calculation in the Changes page**, applied by **API
filtering**, which removes the **rows the gate's link selection needs**. The fixtures play no
part. After about 2026-12-30, the 90 days ending now no longer reach 2026-10-01, and the move
"History" fails with `no link for "History"`. Back, Forward and the direct-load checks then fail
after it. The negative control (§5) reproduces exactly this.

## 2. Strategy: a test-owned anchor, the Overview gate's own technique

Each option in the brief, as it applies to this code:

- **A. Test-owned fixture.** A change row is derived from scan observations, so a "now"-dated
  change means writing synthetic scan data into the demo database. The gate would stop being
  read-only, and its run against the hosted demo would write to it. Rejected as heavier and
  less safe than B.
- **B. Test clock injection that the repository already supports. Chosen.**
  `frontend/cloudflare/checks/overview.mjs` (UX-06B) already solves this for the same dataset:
  a proxy between Next.js and the API moves `from`/`to` on `/api/v1/changes` and
  `/api/v1/changes/summary` so that a window ending now reads the dataset's own last days
  (`DATASET_NOW`, default `2026-10-01T12:00:00Z`). The titles gate now does the same.
- **C. Explicit selection.** The page offers no date or range parameter, only `days`.
  Navigating straight to a known History or impact URL would drop the client-side moves the
  journey exists to check.

**The mechanism.** It is optional and turns on when both `API_URL` and `PROXY_PORT` are set.
The gate then listens on `127.0.0.1:$PROXY_PORT`, and Next.js's `ADG_INTERNAL_API_URL` points
there:

```
Chromium → Next.js (ADG_INTERNAL_API_URL) → titles.mjs proxy :PROXY_PORT → API
```

For those two paths only, when the request's `to` is later than `DATASET_NOW`, the proxy moves
`from` and `to` back by `to − DATASET_NOW`. The window then ends at `DATASET_NOW` and keeps its
length (1/7/30/90 days). Every other request passes through byte for byte. The shift is
computed from the request's own `to`, not from the gate's clock, so the result depends only on
the dataset. It does not depend on when the run happens or on clock skew between processes.

Two guards keep the anchor honest:

- If the proxy is configured but no window read reaches it (for example, Next.js was pointed
  at the API directly), the journey check fails:
  `no window read reached the proxy on :PORT (Next.js ADG_INTERNAL_API_URL?)`. A misconfigured
  run cannot quietly fall back to the date-dependent behaviour.
- The journey check's detail line says which window it used: `24 window reads anchored to
  2026-10-01T12:00:00.000Z`, or `live window`.

Without `API_URL`/`PROXY_PORT`, for example against the hosted demo, whose Next.js server
cannot be proxied, the gate behaves as before and says `live window` (§10).

## 3. Second dependence found: campaign selection

The first anchored run passed every date-dependent move, then failed at "Review item": the
campaign it opened had no item link. The Reviews list is newest first. COPY-04's
`campaign-setup.py` (run during Wave C) created `COPY-04 resource review`, a draft with 0
items, and it now heads the list. Wave C's 15/15 titles run happened before that campaign
existed, so it never saw this.

This is not a date, but it is the same kind of fragility: the journey's result depended on
which run came before it. The step now selects
`tr:has(td:last-child:text-matches("^[1-9]")) a[href^="/governance/campaigns/"]`: the first
listed campaign whose Items column is non-zero. The Items column is
`components/Governance.tsx` `CampaignTable`'s last cell. The step still asserts the same title,
the same current nav item and the same known full load into the item.

## 4. Production semantics unchanged

| Changed file | What changed |
|---|---|
| `frontend/cloudflare/checks/titles.mjs` | Optional anchoring proxy, its guard, the campaign selector, comments. |
| `docs/demo/reports/ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-b-check.mjs` | One header comment (§7). No logic change. |
| `docs/demo/reports/ADG-UX-TEST-STABILITY-09-*` | This report and its evidence. |

Nothing under `frontend/app`, `frontend/components`, `frontend/lib`, `frontend/render`,
`frontend/cloudflare/*.ts` or `backend/` changed (`git diff --stat 559dbaf` lists only the files
above). The Changes default (7 days), its ranges, the API's required `from`/`to`, and every
response are exactly as before. The proxy is a gate-side listener on loopback that exists only
while the gate runs. It is not a product bypass, and nothing in the product knows about it.

## 5. Future-date proof

A full-stack clock change is not possible here. The backend venv has no time-travel library,
and `faketime` is not installed. The web tier also compares the session's `expiresAt` with its
own clock (`lib/auth/session.ts`), so moving only the Next.js clock signs the user out. The
proof therefore simulates the date where it has its effect: the evidence script
[`simulate-clock.mjs`](ADG-UX-TEST-STABILITY-09-evidence/simulate-clock.mjs) sits between Next.js
and the gate's proxy and moves each window read by `SIMULATED_NOW − now`. The API then receives
exactly the window the Changes page would ask for on that date. Each moved window is logged
(`*.sim.txt`).

```
proof runs:      web :3091 → simulate-clock :8291 → titles.mjs proxy :8190 → API :8090
negative control: web :3091 → simulate-clock :8291 → API :8090        (titles.mjs without API_URL)
```

| Simulated date | The 7-day read the page sent becomes | Anchor | Result |
|---|---|---|---|
| 2026-10-30 | 2026-10-22T23:59:59Z .. 2026-10-29T23:59:59Z | on | **15/15**, 24 window reads anchored |
| 2027-06-01 | 2027-05-24T23:59:59Z .. 2027-05-31T23:59:59Z | on | **15/15**, 24 anchored |
| 2028-01-01 | 2027-12-24T23:59:59Z .. 2027-12-31T23:59:59Z | on | **15/15**, 24 anchored |
| 2026-10-30 | as above | **off** | 15/15, `live window` (before the old cutoff, the old gate still passes) |
| 2028-01-01 | as above | **off** | **13/15**: `no link for "History" (a[href^="/changes?kind="]) on …/changes?days=90`. The old failure, reproduced. |

Logs: `titles-future-<date>.txt` / `.sim.txt` and `titles-control-<date>.txt` / `.sim.txt`.

## 6. Titles gate result (coverage kept)

[`titles-gate.txt`](ADG-UX-TEST-STABILITY-09-evidence/titles-gate.txt) is the documented
topology (web :3090 → proxy :8190 → API :8090), today: **15/15**. It has the same 15 checks as
before, and none was removed or loosened:

- sign-in and the login title;
- direct load (`/risks` server `<title>`);
- 10 primary nav moves with no reload;
- Back and Forward across the nav;
- hard reload;
- a filtered list;
- generic detail title with no SID;
- not-found;
- the 21-move deeper journey, now with "24 window reads anchored";
- Back ×21 and Forward ×21 with no reload outside the declared campaign-item full load;
- 18 direct loads with one matching server `<title>`, and History surviving a hard reload;
- four invalid objects, each with a coherent title;
- no hydration error.

The `LEAKS` check (no SID, GUID, UNC/drive path or token in any title) runs on every move, as
before.

The zero-extra-request claim belongs to DEEP-TITLES' `request-count.mjs`, not to this gate. It
was not rerun: no product file changed, so the requests are the ones Wave C counted (0 extra
over 38).

## 7. Wave B stale gate: disposition

The committed `ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-b-check.mjs` is Wave B's record: 8/8
at `10c73d1` (its own `wave-b-check.txt`). From Wave C on, it fails checks 4 and 6 on purpose,
because DEEP-TITLES retitled `/identities/users` and added the one URL-only `generateMetadata`.
Wave C's copy, `ADG-UX-WAVE-C-INTEGRATION-01-evidence/wave-b-check.wave-c.mjs`, changes only
those two expectations.

- **Disposition.** The Wave C copy is the authoritative current gate. The committed script
  stays where it is, with its checks byte-identical. It gains a three-line header saying it is
  historical, where it passed, and which file replaces it. `git show 10c73d1:<path>` still holds
  the exact bytes that ran in Wave B. All results stay: Wave B's own log, Wave C's
  `wave-b-check.committed.txt` (6/8) and the logs here.
- **Not in active execution.** No CI workflow, npm script or shell script runs either file
  (`grep` over the repo finds them only in report evidence), so nothing scheduled is
  expected-red.
- **Re-run here, on this branch:**
  - committed `wave-b-check.mjs`: **6/8**, the same two expected failures;
  - `wave-b-check.wave-c.mjs`: **8/8**.

## 8. Tests

| Gate | Result |
|---|---|
| Titles unit (`tests/titles.test.ts`) | 38/38 |
| Frontend lint (`eslint .`, covers `cloudflare/checks/*.mjs`) | pass |
| Typecheck | pass |
| `cf:typecheck` | pass |
| Full frontend tests (vitest) | **1691/1691**, 49 files (same as Wave C) |
| Build (`ADG_NEXT_OUTPUT=standalone next build --webpack`) | pass |
| Titles browser gate (anchored, today) | **15/15** |
| Future-date proofs (2026-10-30, 2027-06-01, 2028-01-01) | **15/15** each |
| Negative control (2028-01-01, no anchor) | 13/15, as designed |
| Wave C combined gate (`wave-c-check.mjs`) | **9/9** |
| Wave B gate, Wave C copy | 8/8 |
| Wave B gate, committed (historical) | 6/8, as designed |

The backend tree is unchanged, so the backend and DB suites were not run. Logs:
[`frontend-gates.txt`](ADG-UX-TEST-STABILITY-09-evidence/frontend-gates.txt) and one `.txt` per
browser gate, each with the load average before and after (3.4–5.5).

## 9. Environment

| | |
|---|---|
| Demo DB | pgserver PostgreSQL 16.2 on 127.0.0.1:55509. A copy of Wave C's shut-down `adg_demo`: Genesee Press, plus the Overview gate's campaign, DEEP-TITLES' stored proposal and COPY-04's two campaigns. Local only |
| API | `python -m app --port 8090` from this worktree, demo mode, development auth, throwaway secret |
| Web | This branch's standalone build: :3090 → proxy :8190 (titles anchor, or the recording proxy of wave-c/wave-b), and :3091 → simulate-clock :8291 |
| Browser | Google Chrome (/Applications), Playwright 1.62.1 via `NODE_PATH`, Node 22.23.3 arm64 |

**Sensitive identifiers.** No title carries one (the `LEAKS` check). The evidence logs hold
synthetic Genesee Press values only: campaign and item UUIDs in URLs, and in the two Wave B gate
logs the synthetic SID `…-1103`, the same line already in Wave C's committed logs. There are no
cookies, tokens, keys or credentials.

## 10. Limitations

1. **The hosted run stays live-window.** The hosted demo runs Next.js and the API in one Render
   service, behind `render/gate.ts`. The gate cannot proxy that server's internal reads, so a
   hosted run of the deeper journey still needs changes in the real last 90 days. The hosted
   dataset is the same Genesee Press data and ends 2026-10-01, so it has the same limit. A
   hosted fix needs either a dataset refresh or a product date parameter, which this lane
   rules out.
2. **The journey still needs two pieces of state the canonical dataset lacks:** a campaign
   with items and one stored What-if proposal. The Overview gate and DEEP-TITLES'
   `seed-proposal.py` create these. That was already true at Wave C. A fresh reset needs those
   two steps before the deeper journey. Making the gate create them would make it write, so it
   is left as a separate decision.
3. **The proof simulates the date of the window, not the whole stack's clock** (§5). A
   whole-stack clock would also expire the session, which is unrelated to titles.
4. **The anchor covers the two window reads the journey needs.** It does not cover the Changes
   compare page's default points (`app/changes/compare/page.tsx:292`), which the journey only
   lands on for its title.

```
NOT MERGED
NOT DEPLOYED
```
