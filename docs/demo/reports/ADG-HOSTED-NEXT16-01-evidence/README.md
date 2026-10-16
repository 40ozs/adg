# ADG-HOSTED-NEXT16-01 evidence

Synthetic Null Meridian data only. Every check ran against the public demo staging
<https://adg-demo-staging.d-walkes.workers.dev> (Worker `d4b1645a`, Render `dep-db8qhucs728c73anb3n0`,
commit `53b81a6`), except the read-only database checks, which ran from the operator's machine
against `adg_demo`. Scanned before commit for the origin key, the session secret, the database
password, the Render key, the database host and the project ref: none appears. The only hosts are
the two public staging addresses and, inside React's own messages, `react.dev`.

| File | What it is |
| --- | --- |
| `hosted-script-substitutions.txt` | The three string replacements (browser path, web origin, sign-in account) that turn the committed walkthroughs and the #418 gate into hosted runs |
| `smoke-after-deploy.json` | `frontend/cloudflare/checks/smoke.mjs`, right after the deploy: 23/23 |
| `browser-after-deploy.json` | `frontend/cloudflare/checks/browser.mjs`, right after the deploy: 22/22 |
| `demo-01-ui.json` | ADG-DEMO-01's SYSTEM mapping and unresolved-identity check: 15/15 |
| `demo-02-walkthrough.json` | ADG-DEMO-02 (Fatima Gordon → Apollo): 29/29 |
| `demo-03-run1.json`, `-run2.json`, `-run3.json` | ADG-DEMO-03, three hosted runs: 37/39, 37/39, 39/39 (report §14) |
| `demo03-run1-step10-timed-out.png`, `demo03-run3-step10-recovered.png` | DEMO-03 step 10 on the hosted path: the folder-only comparison past its 4-second window (run 1), and recovered (run 3) |
| `docs-walkthrough.json` | DEMO-PUBLIC-01's README / Demo Guide walkthrough: 39/39 |
| `hydration-control-summary.json` | The #418 gate's positive control (`GATE_CONTROL=1`) on the hosted build: #418 on 62 of 62 full loads |
| `hydration-runA-summary.json`, `hydration-runA-loads.jsonl.gz` | #418 gate run A, 10 rounds: totals per phase, and one line per load or transition, every one kept. Interrupted by the test machine's 16-minute sleep and the Render cold start that followed (report §15) |
| `hydration-runB-summary.json`, `hydration-runB-loads.jsonl.gz` | #418 gate run B, 10 rounds, uninterrupted, after all lifecycle events |
| `db/<label>-verify.json`, `-status.json`, `-nm.json` | `python -m app.demo_mode --json verify` / `status` and the Null Meridian logical fingerprint (read-only): `before`, `after-deploy`, `after-sleep-wake`, `after-restart-sleep-wake`, `after-worker-redeploy` |
| `lifecycle-smoke-<label>.json` | `smoke.mjs` after each lifecycle event (report §16) |

`*.jsonl.gz` files are gzip'd JSON Lines (`gzip -dc <file>`).
