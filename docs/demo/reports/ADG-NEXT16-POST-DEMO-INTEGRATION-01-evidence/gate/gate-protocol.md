# React #418 gate protocol (ADG-NEXT16-POST-DEMO-INTEGRATION-01)

Written at 2026-10-15T17:48Z: while phase 1 was running (its plan was fixed when it started,
17:33:42Z) and before phase 2 started. Phase 2 was designed after the phase 1 shakedowns showed
that neither phase 1 condition is likely to discriminate.

Builds
* N15 = `main` 32da1ae, Next.js 15.5.25, `next start` from a worktree (the control).
* N16 = integration 3524a02, Next.js 16.4.1, `next start` from the session tree; later also
  the clean clone (0a65d91, same frontend tree).
* Both against the same API (demo mode) and the same reset `adg_demo` (fingerprint 228b9dce…).

Phase 1 (v1 gate, `react418-gate.script.js`): 10 interleaved rounds of
  n15-direct, n16-direct, n15-l100, n16-l100
  (direct = browser -> next start -> API; l100 = 100 ms added to every API request by
  `latency-proxy.js`). 31 loads per run: 21 full loads (15 routes at 1280 px, the /demo start,
  5 at 390 px) and 10 client-side navigations along the reviewer path.
  Started 17:33:42Z.

Phase 2 (v2 gate, `react418-gate-v2.script.js` = v1 + a reload loop of 6 pages x (1 + 3
reloads), and the document.readyState recorded with each report): 10 interleaved rounds of
  n15-rate, n16-rate, n15-chunk40, n16-chunk40
  through `stream-proxy-v2.js`: /_next/static passes straight through; every other response is
  fetched uncompressed and delivered either
  * rate: 8 KiB every 20 ms, whatever the upstream chunking (identical timing for identical
    bytes on both builds), or
  * chunk40: each upstream chunk held 40 ms (the slow-origin condition of the parallel
    session's gate, reimplemented).
  55 loads per run: 45 full loads, 10 client-side navigations.
  Preceded by the positive control (`positive-control.script.js`) through each rate proxy.

Rules
* Every run is appended to its JSONL file as it finishes; no run is retried, dropped or
  re-labelled. Harness shakedowns are kept in `shakedown.jsonl` and are not gate runs.
* Ready requires: N16 has zero loads with #418 in every condition, and zero application console
  or page errors other than the known /favicon.ico 404.
* The N15 control must show #418 under the condition being used as discriminating; a condition
  in which N15 never shows #418 is reported but does not count as evidence for N16.

Phase 3 (added 2026-10-15T19:01Z, after phase 2 and before running it): the clean clone of the
pushed branch (0a65d91, fresh `npm ci`, `npm run build`) replaces the session build on :3000;
the rate and chunk40 proxies in front of :3000 then serve it. 3 v2 runs through each
(labels n16clean-rate, n16clean-chunk40), recorded like the others.
