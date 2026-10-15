# ADG-NEXT16-RECONCILE-01 evidence

Synthetic Null Meridian data only. Nothing here describes a real organization. Scanned for
credentials before commit: none. No database URL, password, token or cookie appears; the only
hosts are `localhost` and `react.dev` (inside React's own error message in the positive control).

The reconciliation changed no application file, so this directory is deliberately small. The
large-sample React #418 evidence remains
[`ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/`](../ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/),
which applies because the reconciled `frontend/`, `backend/` and `database/` trees are
object-identical to the tree it was measured on (`tree-equivalence.txt`).

| File | What it is |
| --- | --- |
| `tree-equivalence.txt` | The git commands and their output: the merge-base, both sides' commits and paths, the write-free trial merge, the reconciliation commit's parents and tree, every difference between `aff70e8` and the reconciled tree, and each top-level tree entry's object id in the reconciled tree, `aff70e8` and `main` |
| `hydration-smoke-runs.json` | The three #418 smoke runs on the reconciled build, in the order made: the positive control, then one round through the stream-delay proxy at 40 ms and one at 100 ms. Built by the integration's unchanged `aggregate-runs.py`; one line per load |
| `browser-runs.json` | One run of each reviewer walkthrough against the reconciled build: the unchanged DEMO-01 UI, ADG-DEMO-02, ADG-DEMO-03 and DEMO-PUBLIC-01 scripts, every check by name. `console_errors` is `null` where the script records console errors only through its own "no console errors from the application" check |
| `ci-run-84-backend.txt` | The gate result lines of CI run 84's backend job on the reconciliation merge `1a9b1e8` |

The scripts are the ones already in the repository, run unchanged:

```bash
# from any directory, against the API in demo mode and `next start` of the build under test
NODE_PATH=$(npm root -g) GATE_CONTROL=1 GATE_ROUNDS=1 GATE_RELOADS=1 node ../ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js <out>/R0-control
node ../ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/stream-delay-proxy.script.js 3100 http://localhost:3000 40 &
ADG_WEB_URL=http://localhost:3100 GATE_ROUNDS=1 NODE_PATH=$(npm root -g) node ../ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js <out>/R1-40ms
python3 -I ../ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/aggregate-runs.py <out> hydration-smoke-runs.json
```

Milliseconds are as measured on one cloud container.
