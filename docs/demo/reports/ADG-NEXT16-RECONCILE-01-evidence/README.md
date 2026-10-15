# ADG-NEXT16-RECONCILE-01 evidence

Synthetic Null Meridian data only. Nothing here describes a real organization. Scanned for
credentials before commit: none. No database URL, password, token or cookie appears. The only
hosts are `localhost` and `react.dev`, the latter inside React's own error message.

| File | What it is |
| --- | --- |
| `tree-equivalence.txt` | The git proof, every command shown with its output: the merge base and both sides' changes since it, the write-free trial merge, the merge commit's tree and parents, the reconciled tree against the validated `aff70e8` and against `main`, both durable reports' blob identity, subtree hashes, and every excluded lane checked for ancestry |
| `hydration-runs.json` | **Every** React #418 smoke run of this prompt, in the order made: the positive control on the reconciled build, one stream-delayed round on it, and one stream-delayed round on current `main` (Next 15.5.25) as the in-container baseline. Built with the integration's own `aggregate-runs.py`: per-run totals, each #418 occurrence with its route, phase and `readyState`, other errors, failed steps, and one line per load |
| `browser-runs.json` | The four reviewer walkthroughs against the reconciled build: the DEMO-01 UI check, the ADG-DEMO-02 and ADG-DEMO-03 walkthroughs and the DEMO-PUBLIC-01 documentation walkthrough, each with every check and its result |
| `ci-run-87-backend.txt` | The gate result lines of CI run 87's backend job (lint, format, types, unit, PostgreSQL) on the merge `1309146`, verbatim from the job log |

Every browser run used the scripts already in the repository, **unchanged**:

* `ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js`,
  `stream-delay-proxy.script.js`, `demo-01-ui.script.js` and `aggregate-runs.py`;
* `ADG-DEMO-02-evidence/browser-walkthrough.script.js`;
* `ADG-DEMO-03-evidence/browser-walkthrough.script.js`;
* `DEMO-PUBLIC-01-evidence/docs-walkthrough.script.js`.

The reconciled build was a clean clone of `claude/gallant-turing-aj7rf1` at `1309146`: `npm ci`,
`npm run build`, then `next start`. The baseline was current `main`'s `frontend/` at `32da1ae`
(`git archive`, `npm ci`, `npm run build`). Both served the same API, in demo mode over the same
reset database, on one 4-vCPU cloud container.
