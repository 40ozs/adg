# ADG-NEXT16-POST-DEMO-INTEGRATION-01 evidence

Synthetic Null Meridian data only. Nothing here describes a real organization. Scanned for
credentials before commit: none. No database URL, password, token or cookie appears; the only
hosts are `localhost`, `react.dev` (inside React's own error message) and `github.com` (the CI
run link). The scripts take the web origin only and sign in through the development login.

| File | What it is |
| --- | --- |
| `hydration-gate.script.js` | The repeated React #418 gate: fresh loads, reloads, client navigation, keyboard and 390 px loads over 17 reviewer routes, with a positive-control mode (`GATE_CONTROL=1`) that must report #418 on every full load |
| `stream-delay-proxy.script.js` | The reverse proxy that recreates the hosted timing behind #418 on one machine: `/_next/static` passes straight through, every other response is forwarded chunk by chunk with a fixed delay |
| `hydration-runs.json` | **Every** gate run in the order made, pilots and controls included: label, build, timing condition, totals per phase, each #418 occurrence (route, phase, `readyState`), other errors, failed steps, and one line per load |
| `aggregate-runs.py` | Builds `hydration-runs.json` from the gate's per-run output |
| `demo-01-ui.script.js` | Browser check of SYSTEM's mapping states in the explanation and the unresolved-identity banner |
| `browser-runs.json` | Every reviewer walkthrough run against the integrated build: the unchanged ADG-DEMO-02, ADG-DEMO-03 and DEMO-PUBLIC-01 scripts and the DEMO-01 UI check, with pass counts, failures (none) and console errors (only the pre-existing `/favicon.ico` 404) |
| `candidate-overlap.txt` | The write-free trial merges of both Next 16 heads into `main`, the files both sides changed, and every candidate file attributed to its origin |
| `ci-run-80-backend.txt` | The gate result lines of CI run 80's backend job (lint, format, types, unit, PostgreSQL) on `501c136` |
| `next16-*.png` | Screenshots from the integrated build: the Fatima → Apollo investigation, risk coverage at 390 px, the recovered comparison, SYSTEM over a share, and the unresolved principal |

Run the gate against a running demo (development authentication) from any directory:

```bash
NODE_PATH=$(npm root -g) GATE_LABEL="my run" GATE_ROUNDS=3 node hydration-gate.script.js <output directory>
# through the proxy, as the hosted path behaves:
node stream-delay-proxy.script.js 3100 http://localhost:3000 40 &
ADG_WEB_URL=http://localhost:3100 NODE_PATH=$(npm root -g) node hydration-gate.script.js <output directory>
# the positive control:
GATE_CONTROL=1 GATE_ROUNDS=1 GATE_RELOADS=1 NODE_PATH=$(npm root -g) node hydration-gate.script.js <output directory>
```

The scripts are one-off checks kept as evidence, like ADG-DEMO-02/03's and DEMO-PUBLIC-01's;
none is wired into a test suite, and no browser framework was added to the repository.
Milliseconds are as measured on one 4-vCPU cloud container.
