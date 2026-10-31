# ADG-WAVE-D-PROMOTION-01 — Wave D promoted to `main`

## 1. Verdict

**`ADG_WAVE_D_PROMOTED_TO_MAIN_WITH_LIMITATIONS`**. `main` = merge **`e81272d`**, exact-main CI
[41239013815](https://github.com/40ozs/adg/actions/runs/41239013815) **success** (§15).

The promotion candidate's verdict before the merge was
`READY_FOR_ADG_WAVE_D_MAIN_PROMOTION_WITH_LIMITATIONS`.

**`STAGING-VERIFIED WAVE D PRODUCT TREE PRESERVED`**

The verified Wave D integration (`bb370f8`) is merged into the current `main` on
`integration/ADG-WAVE-D-PROMOTION-01`, together with the Wave D staging-release report and evidence.

- **Product tree.** `frontend/`, `backend/`, `database/`, `docker/`, `collector/` and
  `docs/contracts/` are byte-identical to Wave D `bb370f8`, the source of the deployed staging
  release. All 901 adg files in the deployed export manifest are byte-identical (§8).
- **The only non-docs difference from Wave D** is `main`'s one-line CI runner label (§4).
- **No conflict.** No product file was edited.
- **Gates.** Every backend, DB, frontend and current browser gate passes with Wave D's exact counts.

Staging was not touched. **Production was not deployed.**

## 2. Starting `main`

| | |
|---|---|
| Brief's `main` | `bc178c920155970a9c3abb51b3a275a99ac58317` (SYNTH-OWNER-MERGE-01) |
| Live `origin/main` at task start | **`9240ff0083c4be195a0769b1574efdd434a221a8`**, 3 minutes old |
| What moved | Owner PR #3 `ci/ADG-CI-DB-RUNNER-01`: `f4c1d9f` + merge `9240ff0`. One line in `.github/workflows/ci.yml`: the backend job runs on `[self-hosted, Linux, X64, docker, adg-db]` instead of `adg` (the k3s-adg-db runner set, which prefers the bare-metal node for the fsync-bound DB suite) |
| `main` CI | Run [41231849385](https://github.com/40ozs/adg/actions/runs/41231849385) on `9240ff0`: **success** (Backend, Frontend, Pester, Compose) |

The brief's `main` SHA no longer matched origin. The new commits are CI-only, owner-merged and
green, so the promotion is built on the live `9240ff0` (the brief's §6/§9: branch from current
`main`, reconcile newer commits) rather than on the stale SHA.

## 3. Wave D source

| | |
|---|---|
| Branch | `origin/integration/ADG-UX-WAVE-D-01` @ `bb370f8e4531a15166006a1f79f9ca2d64e11a3a` (matches the brief) |
| Verdict | `READY_FOR_ADG_UX_WAVE_E_WITH_LIMITATIONS` ([report](ADG-UX-WAVE-D-INTEGRATION-01.md)) |
| Code/report head | `fc7c7a2`, CI [41223498140](https://github.com/40ozs/adg/actions/runs/41223498140) success |
| Docs-only head | `bb370f8`, CI [41228396705](https://github.com/40ozs/adg/actions/runs/41228396705) success |
| Newer baseline? | **None.** After `git fetch --prune` the only `integration/ADG-UX-*` refs are Waves A–D; Wave D's head is `bb370f8`. No newer integration, release or docs branch |
| Staging | `ADG_WAVE_D_STAGING_RELEASE_VERIFIED_WITH_LIMITATIONS`, release `genesee-press-2.1.0-bb370f8e4531` (adg-demo `5c7805e`), `bootstrap=false`, no DB reset, `WAVE_D_HOSTED_STREAMING_REGRESSION_PASS` ([report](ADG-WAVE-D-STAGING-RELEASE-01.md)) |

## 4. Ancestry

| Pair | Merge-base | Left / right |
|---|---|---|
| `origin/main` (9240ff0) … Wave D | `bc178c9` | 2 / 57 |
| `origin/main` … Wave C (559dbaf) | `bc178c9` | 2 / 42 |
| Wave C … Wave D | `559dbaf` | 0 / 15 |
| Wave D … staging docs (d60168f) | `bb370f8` | 0 / 2 |

- `bc178c9` (the brief's `main`) **is** an ancestor of Wave D; Wave C is an ancestor of Wave D.
- Live `main` `9240ff0` is **not** an ancestor of Wave D: case B, divergence = the 2 CI-runner
  commits above. Wave D changes no file under `.github/`, so the two sides touch disjoint paths.

## 5. Promotion merge

| | |
|---|---|
| Branch | `integration/ADG-WAVE-D-PROMOTION-01`, created from `origin/main` `9240ff0` |
| Merge | **`1023c7c`** = `git merge --no-ff origin/integration/ADG-UX-WAVE-D-01`, "Merge ADG-UX-WAVE-D-01: verified Wave D UX integration" (parents `9240ff0`, `bb370f8`) |
| Wave D history | Kept intact: no rebase, squash or force push; `bb370f8` and all 57 commits are reachable unchanged |

## 6. Conflicts

**None.** The merge applied without a textual conflict, and no file needed a semantic resolution:
`main`'s side changes only `.github/workflows/ci.yml`, which Wave D does not touch. No product file
was edited in this lane.

## 7. Staging evidence import

| | |
|---|---|
| Source | `origin/docs/ADG-WAVE-D-STAGING-RELEASE-01` @ `d60168f`, 2 commits on top of `bb370f8` |
| Unique commits | `80c515f` report + evidence (36 files); `d60168f` records CI 41228396705 on `bb370f8` (report only) |
| Paths | Both commits touch only `docs/demo/reports/ADG-WAVE-D-STAGING-RELEASE-01.md` and `…-evidence/` — no tooling outside the evidence dir, no deployment mutation |
| Imported | `git cherry-pick -x`, one at a time: `80c515f` → **`7578524`**, `d60168f` → **`1c53444`** |
| Content check | `git diff d60168f 1c53444 -- docs/demo/reports/ADG-WAVE-D-STAGING-RELEASE-01*` is empty: the imported report and evidence are byte-identical to the branch |
| Verdict | Unchanged: `ADG_WAVE_D_STAGING_RELEASE_VERIFIED_WITH_LIMITATIONS` |

**Secret and hostname scan** (both commits, every file):

- no `onrender.com` / `workers.dev` hostname in either commit (the report's own §Security notes
  two CI logs were redacted to `<staging>` and `<render-origin>`; that redacted form is what is
  imported, and no earlier unredacted version exists in the pushed history);
- no JWT, bearer value, cookie value, origin key, pooler URI, Cloudflare account id or Render
  workspace id; the value of every local staging secret file was searched for and not found;
- the only pattern hits are code that builds an `Authorization` header or an `adg_session`
  cookie from runtime values (`hosted-wave-d.mjs`, `stream-probe.mjs`, `structure-probe.mjs`) and
  the report's description of its own scan.

## 8. Tree equivalence

[`tree-comparison.txt`](ADG-WAVE-D-PROMOTION-01-evidence/tree-comparison.txt),
[`export-equivalence.txt`](ADG-WAVE-D-PROMOTION-01-evidence/export-equivalence.txt).

| Tree | SHA |
|---|---|
| Wave D `bb370f8` | `3509c4e1a047a63ba83ed0851aa215b93e7cbee4` |
| Promotion merge `1023c7c` | `f75aabb5161805a305e185d06f7e57d433a0e3e7` |
| Promotion candidate `1c53444` | `88421040292c1a0eb69b456a64cda524a6fc408b` |

Every file that differs from Wave D, classified:

| Class | Files |
|---|---|
| Current-`main` reconciliation (CI only) | `.github/workflows/ci.yml` (runner label, 1 line + 1 comment) |
| Staging report/evidence | 36 files under `docs/demo/reports/ADG-WAVE-D-STAGING-RELEASE-01*` |
| Promotion report/evidence | this report and `ADG-WAVE-D-PROMOTION-01-evidence/` |
| **Unexpected application differences** | **none** |

**Release equivalence.** The deployed artifact's manifest (`demo-release/files.sha256` of
`genesee-press-2.1.0-bb370f8e4531`, 905 entries) was checked file by file against the candidate:
**901 identical, 0 different**; the other 4 are files the exporter itself writes
(`DEMO-RELEASE.md`, `demo-release.json`, `demo-release/verify_release.py`,
`.github/workflows/demo-release.yml`). `ci.yml` is not exported. Staging therefore runs exactly the
product tree being promoted.

## 9. Hosting-path safety

Blob-identical to the deployed Wave D: `frontend/cloudflare/worker.ts`, `frontend/render/`
(including `gate.ts`), `next.config.mjs`, `wrangler.jsonc`, `docker/` (Dockerfiles),
`lib/config.ts`, `lib/api`, `app/api`, `app/layout.tsx`, `app/page.tsx` (the Overview
streaming entry), `package.json`, `package-lock.json`, `pyproject.toml`, `database/` and
`.github/workflows/cloudflare-staging.yml`. Whole `frontend/` and `backend/` trees are identical.

## 10. Contract safety

[`contract-safety.txt`](ADG-WAVE-D-PROMOTION-01-evidence/contract-safety.txt). By tree identity
every Wave D contract holds; the specific points:

- Governance `ScopeView`: `kind`, `key` required; `display_name: string | null` (OpenAPI
  `anyOf [string, null]`, frontend `display_name: string | null`).
- Scan `ScanScopeView`: `kind`, `key` only.
- `docs/contracts/v1/openapi.json`, graph ids, `principal:<sid>`, `principal=`, scope tokens, rule
  ids/versions and `database/` migrations: identical to Wave D.
- **NAV-07:** `DecisionForm.tsx:322` item link is `<Link … prefetch={false}>`; no plain item `<a>`.
- **REVIEW-NAMES-08:** names come from the scope's `display_name` with the explicit unresolved
  fallback; no per-row lookup (browser gates below show the same 5/6 web-tier reads as Wave C).
- **TEST-STABILITY-09:** `frontend/cloudflare/checks/titles.mjs` unchanged; the anchored run below
  shows the proxy enforcement and the deterministic window.

## 11. Backend gates

Python 3.13.15 from the canonical checkout's venv, `PYTHONPATH` = this worktree's `backend/`
(`pyproject.toml` identical). [`backend-gates.txt`](ADG-WAVE-D-PROMOTION-01-evidence/backend-gates.txt),
[`db-gates.txt`](ADG-WAVE-D-PROMOTION-01-evidence/db-gates.txt).

| Gate | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass |
| `mypy app tests` | pass |
| `pytest -q -m "not smoke"` | **6164 passed**, 12 skipped (Wave D: 6164 / 12) |
| DB: `ADG_RUN_SMOKE_TESTS=1 pytest -q -m smoke` | **1306 passed, 1 xfailed, 0 failed** in 20 min (Wave D: 1306 + 1) |

The DB cluster was a throwaway pgserver PostgreSQL 16.2 on loopback with `timezone=UTC` and
`fsync=off`; the suite used `adg` → `adg_test`.

**First attempt (environment, not product).** The first run of both suites stopped at collection
with 16 `UnicodeDecodeError`/`VendorError`s: the fresh worktree on the exFAT T5 carried 2,278 macOS
`._*` AppleDouble sidecars, which the contract tests and the Genesee Press vendor check read as
data. None is tracked by git. They were deleted outside `node_modules`/`.git` and both suites
rerun; the first logs are kept as `*.attempt1-exfat-sidecars.txt`.

## 12. Frontend gates

Node 22.23.3 arm64, `npm ci` from the identical lockfile.
[`frontend-gates.txt`](ADG-WAVE-D-PROMOTION-01-evidence/frontend-gates.txt).

| Gate | Result |
|---|---|
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass |
| `npm run test` | **1696 passed**, 49 files (Wave D: 1696) |
| `ADG_NEXT_OUTPUT=standalone npm run build` | pass |

## 13. Browser gates

The current authoritative gates named by Wave D §18, run against this candidate's standalone build.
Local stack ([`stack.txt`](ADG-WAVE-D-PROMOTION-01-evidence/stack.txt)): a fresh local
`python -m app.demo_mode reset` of `adg_demo` (92 s, fingerprint `4667d3cf…`, the canonical
value), Wave D's [`setup-fixtures.sh`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/setup-fixtures.sh)
([`fixtures.json`](ADG-WAVE-D-PROMOTION-01-evidence/fixtures.json)), API :8110 (demo mode,
development auth, throwaway secret, default dev users + `nobody:remediator`), web :3110 (→ each
gate's recording proxy :8210), :3111 (direct, for copy-check) and the Wave C `559dbaf` build :3112
(→ :8212) as REVIEW-NAMES' old client. Chrome from /Applications, Playwright 1.62.1.

| Gate | Result | Wave D |
|---|---|---|
| Overview `frontend/cloudflare/checks/overview.mjs` | **49/49** | 49/49 |
| Review navigation `ADG-UX-NAV-07-evidence/review-item-nav.mjs` | **16/16** | 16/16 |
| Review names `ADG-UX-REVIEW-NAMES-08-evidence/review-names.mjs` (+ Wave C build) | **11/11** | 11/11 |
| Titles, anchored `frontend/cloudflare/checks/titles.mjs` | **15/15**: 21 moves, no reload; 24 window reads anchored to 2026-10-01T12:00:00Z | 15/15 |
| **Wave D combined** `…WAVE-D…-evidence/wave-d-check.mjs` (`SIMULATED_NOW=2028-01-01`) | **15/15** | 15/15 |
| COPY-04, current `…WAVE-D…-evidence/copy-check.wave-d.mjs` | **17/17** | 17/17 |
| Wave C combined `…WAVE-C…-evidence/wave-c-check.mjs` | **9/9** | 9/9 |
| Wave B, current copy `…WAVE-C…-evidence/wave-b-check.wave-c.mjs` | **8/8** | 8/8 |

Two gates needed their documented wiring on a second run; the product was not changed in between.
`copy-check.wave-d.mjs` has no proxy of its own, so on :3110 (which points at the absent :8210
proxy) its sign-in got HTTP 502; it passes against the direct :3111, as in Wave D.
`wave-b-check.wave-c.mjs` reads `APP_DIR` relative to the repo root; run from `frontend/` it
could not find `frontend/app` (7/8); with `APP_DIR` set it passes. The first-attempt logs were
overwritten by the reruns.

**Responsive.** Wave D checks 10–14: at 1440, 1024, 768, 375 and 320 px, Reviews → campaign →
item stays client-side and no page scrolls sideways. Screens:
[`screens/`](ADG-WAVE-D-PROMOTION-01-evidence/screens/) (`reviews-{1440,375,320}`,
`campaign-1440`). Check 15's one console error is a `/governance` 404 resource line, identical
to Wave D's committed `wave-d-check.txt`.

**Not rerun, by decision.** The historical Wave B (`wave-b-check.mjs`, 6/8 by design) and COPY-04
(`copy-check.mjs`, 16/17 by design) gates, as the brief requires. The Recently Viewed gate and the
Wave A check: no shared file or infrastructure changed in reconciliation (the only reconciled file
is `ci.yml`), and Wave C check 1, which covers Recently Viewed's placement, passed.

## 14. Promotion CI

Run [41232466155](https://github.com/40ozs/adg/actions/runs/41232466155) on the candidate
`1c53444` (code + staging evidence, before this report): **success**. The backend job ran on the
new `adg-db` runner set.

| Job | Result |
|---|---|
| Backend (lint, types, tests) | **success**: ruff, format, mypy; unit **6052 passed**, 124 skipped, 1307 deselected; DB **1306 passed**, 1 xfailed (22.6 min) |
| Frontend (lint, types, tests, build) | **success**: 49 files, **1696** passed; lint, typecheck, cf:typecheck, build |
| Collector (Pester) | success |
| Docker Compose configuration | success |
| Fork PR Linux gates | skipped (push event) |

The counts equal Wave D's CI run 41223498140 exactly; the unit split (6052/124 in CI, 6164/12
locally) is the known platform-dependent skips. This report is a docs-only commit on top of
`1c53444`; its own push run is recorded in §15.

## 15. Main merge and post-merge CI

**Report head.** Run [41236575877](https://github.com/40ozs/adg/actions/runs/41236575877) on
`d82d477` (this report on top of `1c53444`): **success**, every job.

**Merge.** With `origin/main` re-fetched and still `9240ff0`, the canonical checkout's `main` was
fast-forwarded to it and then
`git merge --no-ff origin/integration/ADG-WAVE-D-PROMOTION-01` gave
**`e81272d120a21668373ae87714dfa74909f448b6`**, "Merge Wave D: ADG UX integration and verified
staging release" (parents `9240ff0`, `d82d477`). It was pushed normally, `9240ff0..e81272d`; there
was no force push and no squash, and Wave D's history is reachable unchanged.

**Exact-main CI.** Run [41239013815](https://github.com/40ozs/adg/actions/runs/41239013815) on
`e81272d`: **success**.

| Job | Result |
|---|---|
| Backend (lint, types, tests) | **success**: unit **6052 passed**, 124 skipped; DB **1306 passed**, 1 xfailed (23.2 min) |
| Frontend (lint, types, tests, build) | **success**: 49 files, **1696** passed |
| Collector (Pester) | success |
| Docker Compose configuration | success |
| Fork PR Linux gates | skipped (push event) |

**Main tree proof.** The tree of `e81272d` is `5f63fc76819317f7bb2cf53a143b6cefb2166b76`, the
same as the validated promotion head `d82d477`. Its product tree is therefore the one every gate
above ran on, and the one staging runs. The only later change is this section and §1, made by a
docs-only commit directly on `main`.

## 16. Staging correspondence

**`STAGING-VERIFIED WAVE D PRODUCT TREE PRESERVED`.** Staging runs
`genesee-press-2.1.0-bb370f8e4531`, exported from `bb370f8`. The promoted product tree is
byte-identical to `bb370f8`, and every exported adg file matches (§8). No new adg-demo export is
needed, and none was made.

## 17. Limitations

Carried unchanged from Wave D §22 and the staging report §21:

1. The hosted canonical dataset has no review campaign, so campaign-specific hosted behaviour
   (NAV-07, review names) is verified indirectly (identical tree, bundle marker, local gates).
2. The `(all theirs)` (`?principal=`) and target filter links still reload the campaign.
3. Focus after campaign → item navigation lands on `BODY`.
4. The `?principal=` items heading can fall back to the SID.
5. A hosted titles journey uses the real date window.
6. Scope labels show the current name while frozen item evidence keeps the baseline name
   (intentional, pinned by a test).
7. Some missing-object pages answer HTTP 200.
8. Access-explanation diagram tooltips can show raw SIDs.
9. Stored What-if reports keep their old wording.
10. Recently Viewed is per tab.
11. The hosted streaming proof used natural timing in one bounded time window.

This lane adds none.

## 18. Deployment status

- **Staging: UNCHANGED** — `genesee-press-2.1.0-bb370f8e4531` (adg-demo `5c7805e`). No Render or
  Worker deploy, no Cloudflare change, no seeding, no database reset, no hosted run. The `Free
  staging (ADG demo)` workflow in this repo is disabled and triggers only on
  `deploy/ADG-HOSTED-NEXT16-01`, which was not touched.
- **Production: NOT DEPLOYED.**
- **Cleanup:** none. Wave C/D integration branches, feature branches,
  `docs/ADG-WAVE-D-STAGING-RELEASE-01` and all worktrees are kept.

No database, browser profile, credential, cookie, token or `node_modules` is committed. The
evidence holds synthetic Genesee Press values and loopback URLs only.
