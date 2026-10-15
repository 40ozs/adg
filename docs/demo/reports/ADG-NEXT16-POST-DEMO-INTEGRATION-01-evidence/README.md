# ADG-NEXT16-POST-DEMO-INTEGRATION-01 evidence

Synthetic Null Meridian data only. Nothing here describes a real organization. Scanned for
credentials and home-directory paths before commit: none.

| Path | What it is |
| --- | --- |
| `candidate-overlap.txt` | Refs, ancestry, write-free trial merges (`git merge-tree --write-tree`) of the Next 16 heads and `claude/beautiful-knuth-3zruji` into `main` (`32da1ae`), and every file the Next 16 line changed since `1ab01a2` with the commits that changed it (report §5) |
| `gate/gate-protocol.md` | The React #418 gate's protocol: builds, conditions, phases, rules. Written before phases 2 and 3 ran |
| `gate/react418-gate.script.js` | Gate v1 (phase 1): per run, 21 full loads (15 reviewer routes at 1280 px, the `/demo` start, 5 at 390 px) and 10 client-side navigations along the reviewer path |
| `gate/react418-gate-v2.script.js` | Gate v2 (phases 2 and 3): v1 plus a reload loop (6 pages × 4 loads) and `document.readyState` with each report: 45 full loads and 10 navigations per run |
| `gate/positive-control.script.js` | Rewrites one heading in `/demo`'s server HTML so hydration must fail; proves the detector sees #418 on a given build and path |
| `gate/latency-proxy.js` | Phase 1's `l100` condition: +100 ms on every API request |
| `gate/stream-proxy-v2.js` | Phases 2–3: the slow-origin proxy. `/_next/static` at once; everything else uncompressed, either 8 KiB every 20 ms (`rate`) or each chunk held 40 ms (`chunk40`) |
| `gate/stream-delay-proxy.js` | A first slow-origin proxy, used only in one shakedown (gzip left on, 100 ms per chunk); it did not reproduce #418 on Next 15 and was replaced by `stream-proxy-v2.js` |
| `gate/ab-driver.sh`, `gate/ab-driver-v2.sh` | The interleaved drivers: one run per (build, condition) per round, each in a fresh browser process |
| `gate/aggregate.py` | Turns the run files into `summary.txt` |
| `gate/runs/phase1-*.jsonl`, `phase2-*.jsonl`, `phase3-clean-clone.jsonl` | **Every gate run**, one JSON line per run with every load: kind, route, HTTP status, #418 count (and, in v2, the messages with `readyState`), application errors, same-document flag for navigations, 390 px overflow |
| `gate/runs/phase1-driver.log`, `phase2-driver.log` | The drivers' one-line-per-run output, in the order the runs happened |
| `gate/runs/positive-control.jsonl` | The four positive-control runs: 20 of 20 mutated loads detected |
| `gate/runs/shakedown.jsonl`, `gate/shakedown-summary.txt` | The four harness shakedowns, kept and **not** counted: a selector bug (6 failed steps), its fix, the first proxy (no #418 on Next 15), and v2 on Next 15 (6 of 45 loads with #418). The v2 shakedown predates v2's storing of the #418 messages |
| `gate/summary.txt` | The aggregate of every counted run (report §10.3) |
| `walkthroughs/demo-02-walkthrough.json`, `demo-03-walkthrough-{1,2,3}.json`, `docs-walkthrough.json` | The unchanged ADG-DEMO-02, ADG-DEMO-03 and DEMO-PUBLIC-01 scripts, run against the clean-clone Next 16 build: 29/29, 39/39 ×3, 39/39 |
| `walkthroughs/next16-*.png` | Three screenshots from those runs: the investigation at 390 px, the recovered comparison, the risk coverage at 390 px |
| `logs/` | Frontend `npm ci`, lint, typecheck, tests, build and representative sets on the session tree; the same on `main` for the test baseline; the clean clone's `npm ci` and gates; backend ruff, mypy, representative unit and PostgreSQL sets; Alembic heads; the contract regeneration; the demo reset and verify |

Re-run the gate against a running demo (API in demo mode over a reset `adg_demo`, `next start`
on the port given), from any directory:

```bash
NODE_PATH=$(npm root -g) node gate/react418-gate-v2.script.js http://localhost:3000 my-label 1 out.jsonl
LISTEN=3210 TARGET=http://127.0.0.1:3000 MODE=rate SLICE_BYTES=8192 SLICE_MS=20 node gate/stream-proxy-v2.js
python3 -I gate/aggregate.py out.jsonl
```

The scripts sign in through the development login, so they work only against a
development-auth deployment, and they launch the Chromium at
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`, as the earlier demo scripts do.
