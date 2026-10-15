# DEMO-PUBLIC-01 evidence

Synthetic Null Meridian data only. Nothing here describes a real organization. Scanned for
credentials before commit: none.

| File | What it is |
| --- | --- |
| `docs-walkthrough.script.js` | One-off Playwright check (not wired into any suite; no browser framework was added). It follows the reviewer path in `README.md` and `docs/demo/README.md`, and checks that every label, route and number those documents cite is what the application shows |
| `docs-walkthrough.json` | Its result: 39 of 39 checks, against a stack built from a fresh clone |
| `browser-runs.json` | Every browser run of this prompt, against the same fresh-clone stack: the documentation walkthrough, the unchanged ADG-DEMO-03 script three times, and the unchanged ADG-DEMO-02 script once. Includes the one intermittent React #418 console error (report §13.4) |
| `fresh-clone-reset.txt` | The output of `python -m app.demo_mode reset` from the fresh clone: 300 runs, 23/23 probes, 9,386 open findings, fingerprint `228b9dce…` |

Run the script against a running demo from any directory:

```bash
NODE_PATH=$(npm root -g) node docs-walkthrough.script.js <output directory>
```

It signs in through the development login, so it works only against a development-auth
deployment. `ADG_WEB_URL` overrides the default `http://localhost:3000`.
