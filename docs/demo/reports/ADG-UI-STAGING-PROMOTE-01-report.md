# ADG-UI-STAGING-PROMOTE-01: UI reconciliation / contract 1.2 promoted to staging

> **Resolved 2026-10-27 by ADG-SMOKE-CANONICAL-ROUTE-01 (owner: "fix it").**
>
> - **The fix.** `1c36984` is a one-line change to `frontend/cloudflare/checks/smoke.mjs:310`:
>   the representative-principal check now requests `/identities/principal/{sid}`. Nothing else
>   changed and there is no application change. The legacy redirect is still checked by
>   `navigation.mjs`.
> - **Gates before the push.** Branch CI
>   [40559781777](https://github.com/40ozs/adg/actions/runs/40559781777) passed (backend,
>   frontend, Pester, compose). The committed smoke from that branch passed 23/23 against staging.
> - **Refs.** `main` and `deploy/ADG-HOSTED-NEXT16-01` were fast-forwarded together,
>   `021562b → 1c36984` (19:48:44Z).
> - **Push-triggered run.** Run [40572447255](https://github.com/40ozs/adg/actions/runs/40572447255)
>   ended **success**:
>   - preflight and gates passed, bootstrap skipped;
>   - Render `dep-dbgg0dflk1mc738s4m60` live @ `1c36984`;
>   - Worker `ae2ec8a7-3087-4fcf-a81e-cd72d8c72f53`, tag `1c3698453d1b`, 100%;
>   - verify `228b9dce…` and Null Meridian `db736602…` match;
>   - smoke **23/23**, browser **22/22** (1 console error, the known favicon 404).
> - **Dataset.** Read-only count of every table afterwards: 41 tables, 103,156 rows, identical to
>   before; Alembic `0018_risk_evaluation_basis`.
>
> With the standard gates green in CI, this promotion stands as
> **`ADG_UI_STAGING_PROMOTED_WITH_LIMITATIONS`**. The remaining limitations are DEMO-03 step 10's
> 4 s window on Render Free, and unresolved chain hops covered by tests only (§18, items 2–5).
> The original verdict and evidence below are unchanged.
>
> - **Rollback.** Render `dep-dbgdt1o473hc73d6g82g` (`021562b`) + Worker `37bc72e1`.
> - **Not changed.** The committed DEMO-02 walkthrough still waits for the old `?key=` URL. It is
>   historical evidence and was left alone (§13, adaptation 4).

**Verdict: `BLOCKED_ON_HOSTED_UI_VALIDATION`.** Staging now runs `021562b` (Render and Worker,
deployed by the ordinary push-triggered workflow). Every product check passed against it except
one known timing limit (DEMO-03, step 10).

The single blocker is a standard hosted gate. The committed `smoke.mjs` still requests the
pre-reconciliation `/identities/principal?key=` address with `redirect: "manual"`. `021562b`
answers that address with the designed 307 to `/identities/principal/{key}`. Smoke therefore
reported **22/23** in run 40550373551, and the workflow skipped its browser step.

This check passed on `f730d99`, so it is not pre-existing, and this task's rules make it block
completion. The application is correct: the redirect target loads 200, and the same check passes
**23/23** with only that path changed (§8). The fix is a one-line harness change that this lane
was told not to commit (§19).

| | |
| --- | --- |
| Repository | `40ozs/adg`, canonical checkout `/Volumes/T5 EVO/mmm/adg` (exFAT, T5 EVO) |
| `main` | `021562b41b859f0a3f0600824ba0ccf95c3b6b69` (unchanged) |
| `deploy/ADG-HOSTED-NEXT16-01` | `f730d99` → **`021562b`**, fast-forward push 2026-10-27T17:24:58Z |
| Workflow | *Free staging*, run **40550373551** (event `push`), conclusion **failure** (smoke, §8) |
| Render | `adg-demo-web`, deploy **`dep-dbgdt1o473hc73d6g82g`** @ `021562b`, live |
| Worker | `adg-demo-staging`, version **`37bc72e1-ecd1-4fe5-9d9c-14f04fd4dabb`**, tag `021562b41b85`, 100% |
| Database | `adg_demo`, Alembic `0018_risk_evaluation_basis`, **not reset, not migrated, unchanged** (§6) |

## 1. T5 EVO checkout

- Mounted at `/Volumes/T5 EVO`: 7.3 TiB, 5.4 TiB free.
- `git rev-parse --show-toplevel` = `/Volumes/T5 EVO/mmm/adg`, on `main` @ `021562b`, clean.
- No `._*` files in `.git/objects/pack` at the start. `git fsck` passed before fetching.
- The T5 never disconnected during the task.
- Every long command ran under a per-command `caffeinate -dimsu`. No background `caffeinate` was
  left running.
- The internal SSD had 10 GiB free. The only internal-disk writes were the uv/npm download caches
  and the existing Playwright Chromium build.

**exFAT AppleDouble, the one bootstrap deviation.** On this Mac, every file the toolchain writes
gets a `com.apple.provenance` xattr, and that xattr cannot be removed. On exFAT, macOS stores it
as a `._<name>` sidecar, and the sidecars break things:

- `uv pip install -e ".[dev]"` failed. `ruff`'s wheel script gained `._ruff`, and uv's RECORD
  check refused the unexpected file.
- The fix: the venv is still created by uv (`uv venv --seed --python 3.13`). Dependencies are
  installed by the venv's own pip with `python -m pip install -e ".[dev]"`, which is exactly what
  `ci.yml` runs. The backend has no lockfile, so CI's mechanism is the repository's mechanism.
- Sidecars removed (untracked metadata only; `git ls-files` lists no `._*` path):
  - 28,329 in `frontend/node_modules`;
  - 5,767 in `backend/.venv`;
  - 879 in the source tree, from the clone, which would otherwise feed `._page.tsx`-style
    files to eslint, tsc and vitest.
- Files under `.git` other than the pack directory were left alone.

## 2. Bootstrap

| | Result |
| --- | --- |
| Node | `v22.23.3`, `arm64` (nvm), npm 10.9.9; no Rosetta |
| `npm ci` (frontend) | 489 packages, exit 0 (10 min on the loaded Mac) |
| Python | CPython 3.13.15, `arm64`, venv `/Volumes/T5 EVO/mmm/adg/backend/.venv` |
| Backend install | `pip install -e ".[dev]"`, exit 0; `pip check`: no broken requirements; ruff 0.17.3, mypy 2.4.0, pytest 9.1.1 |

## 3. Lightweight T5 validation

This ran while three of the owner's xcodebuild jobs were running (load average 30–100), so the
timings are pessimistic. Evidence: [`t5/`](ADG-UI-STAGING-PROMOTE-01-evidence/t5/).

| Gate | Result |
| --- | --- |
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm run test` (vitest) | 44 files, **1,510 passed** |
| `npm run build` (`next build --webpack`) | pass |
| `ruff check .` / `ruff format --check .` | pass / 414 files formatted |
| `mypy app tests` | **0 issues**, 413 files |
| `alembic heads` | one head, `0018_risk_evaluation_basis` |
| Focused tests (`tests/contracts`, `tests/api/test_caching.py`, `test_access_bounds.py`, `test_authorization.py`, `tests/db/test_graph_api.py`, `test_access_api.py`, `test_explanation_api.py`, `test_access_paths_api.py`) | **978 passed, 1 skipped** in 6 m 07 s |

The focused set covers:
- contract 1.2: the schema parity and caching tests;
- the principals directory and `exclude_local` (`test_graph_api.py`);
- `path_principals` (`test_access_api.py`, `test_graph_api.py`).

The DB tests ran against a throwaway `postgres:16-alpine` on tmpfs (127.0.0.1:55436, random
password), which was removed afterwards. Main CI at `021562b`
([40538713743](https://github.com/40ozs/adg/actions/runs/40538713743)) is green and remains the
authority for the full suite.

## 4. Pre-deployment gates

**Git**, checked right after a fetch and again at push time:
- `origin/main` = `021562b`;
- `origin/deploy/ADG-HOSTED-NEXT16-01` = `f730d99`;
- `git merge-base --is-ancestor` held, with 19 commits to fast-forward;
- main CI 40538713743 `success` at `021562b`.

**Migrations.** `git diff f730d99 021562b` touches no Alembic revision; the only
migration-adjacent path is a JSON evidence file. Contract 1.2 is a response-shape change.

**Hosted database preflight** (read-only session: `default_transaction_read_only=on` plus
`SET TRANSACTION READ ONLY`, from this Mac with the operator's secret file; nothing printed):

| | Before the push |
| --- | --- |
| Database | `adg_demo` |
| Alembic | one row, `0018_risk_evaluation_basis`; no GRAPH table |
| `demo_mode verify` | ok, 23 probes, all generations applied, fingerprint `228b9dce…f9efd0` |
| Null Meridian fingerprint | `db736602…85c28a0`, match |
| Tables / rows | 41 / 103,156 |

**Rollback point recorded before the push:**
- Render `dep-dbg3k3t9fdbs73cm2v40` @ `f730d99`;
- Worker `d2ca8cfe` (100%).

## 5. The automated deployment

`git push origin 021562b…:refs/heads/deploy/ADG-HOSTED-NEXT16-01` was an ordinary fast-forward
(`f730d99..021562b`). There was no force, no `workflow_dispatch`, no bootstrap input, no Render
API call and no wrangler deploy from this Mac. The local deploy ref was then fast-forwarded with
`git fetch origin deploy/…:deploy/…`.

Run **[40550373551](https://github.com/40ozs/adg/actions/runs/40550373551)** (key lines:
[`hosted/run-40550373551-key-lines.txt`](ADG-UI-STAGING-PROMOTE-01-evidence/hosted/run-40550373551-key-lines.txt)):

| Job | Result | UTC |
| --- | --- | --- |
| Preflight (configuration, fail closed) | success | 17:25:03–17:25:09 |
| Deployment code gates | success | 17:25:03–17:25:21 |
| Bootstrap demo database | **skipped** | — |
| Deploy origin (Render) | `dep-dbgdt1o473hc73d6g82g` of `021562b41b85` **live** | 17:25:25–17:28:23 |
| Deploy Worker | version `37bc72e1…`, tag `021562b41b85`; 10 new asset files (57 already uploaded) | 17:28:26–17:30:50 |
| Hosted checks | verify ok `228b9dce…`; Null Meridian `db736602…` **match**; smoke **22/23** → step failed; browser step **not run** | 17:30:54–17:31:51 |

**Render** ([`hosted/render-deploys-after.json`](ADG-UI-STAGING-PROMOTE-01-evidence/hosted/render-deploys-after.json)):
- `dep-dbgdt1o473hc73d6g82g`, commit `021562b41b859f0a3f0600824ba0ccf95c3b6b69`, trigger `api`;
- created 17:25:27Z, live 17:28:20Z (2 m 53 s);
- the previous deploy, `dep-dbg3k3t9fdbs73cm2v40`, is deactivated;
- the service is still `adg-demo-web` on branch `deploy/ADG-HOSTED-NEXT16-01`, auto-deploy `no`
  (the workflow deploys by commit id), not suspended.

**Worker** ([`hosted/worker-deployments-after.txt`](ADG-UI-STAGING-PROMOTE-01-evidence/hosted/worker-deployments-after.txt)):
- `37bc72e1-ecd1-4fe5-9d9c-14f04fd4dabb`, tag `021562b41b85`, **100%**;
- the public URL is unchanged: `https://adg-demo-staging.d-walkes.workers.dev`.

**SHA match.** Render and Worker both report `021562b`. There is no SHA mismatch.

## 6. Dataset invariant (after the run, same read-only method)

[`hosted/db-invariant.json`](ADG-UI-STAGING-PROMOTE-01-evidence/hosted/db-invariant.json):

- `demo_mode verify`: ok. Fingerprint `228b9dce…` and verify counts are **identical** to before.
- Null Meridian fingerprint `db736602…`: match.
- Alembic: one row, `0018_risk_evaluation_basis`. No GRAPH table.
- **41 tables, 103,156 rows. Exact `count(*)` of every table is unchanged** (0 tables differ).

No reset, bootstrap or migration ran, and nothing wrote to the dataset.

## 7. Contract 1.2, `path_principals`, `exclude_local`, cache transition (API)

Read-only GETs as `planner`, with a bearer token from the public `/auth/dev/login` (the same
route `smoke.mjs` uses). Script:
[`harness/contract-12.mjs`](ADG-UI-STAGING-PROMOTE-01-evidence/harness/contract-12.mjs).
Result: **24/24**
([`checks/contract-12.json`](ADG-UI-STAGING-PROMOTE-01-evidence/checks/contract-12.json)).

**Contract version.**
- `GET /api/v1/access/explain` (Fatima Gordon on `\\NM-ENG-FS01\Engineering\Projects\Apollo`):
  `schema_version` **`1.2`**, ETag `"1.2-0d1cc4aa…-…"`.
- `GET /api/v1/access/paths`: `schema_version` `1.2`.
- `ResourcePrincipalsResponse` has no `schema_version` field by design, so it is not checked
  for one.

**`path_principals`.** Every entry has `len(path_principals) == len(path)` and
`path_principals[i].key == path[i]`, in order:

| Response | Entries | Mismatched | Example |
| --- | --- | --- | --- |
| Access explanation token | 35 | 0 | Fatima Gordon → ENG-Mobile → Engineering-Frontend → Engineering |
| Resource impact (`/access/resources/{Apollo}/principals`, `via`) | 294 | 0 | Nina Coleman → Managers → Eng-Leads |
| Effective groups (Bianca Rossi, `scope=effective`) | 29 | 0 | Bianca Rossi → Sales-Enterprise → GG_Sales → All Staff → AD-Okta-Sync-All → OKTA_Slack_All → sg-okta-slack-user |

No unresolved hop carries an invented display name.

**Principals directory and `exclude_local`:**
- groups total **238**;
- with `exclude_local=true`, total **205**;
- the **33** removed are all `local_group`;
- every one of the **58** AD `domain_local` groups stays (kept scopes: global 147, domain_local
  58);
- users total 1,885.

These are the established Null Meridian numbers; no expectation was changed.

Searches:
- name prefix `Fatima` finds Fatima Gordon;
- SID prefix `…-132` finds her;
- `NULLMERIDIAN\fgordon` finds her.

**Cache transition.**
- A 1.1 validator (`If-None-Match: "1.1-<same basis>-…"`) gets one **200** with the 1.2 body and
  the 1.2 ETag.
- The 1.2 ETag then gets **304**, and **304** again: no repeated invalidation.
- In the browser no 1.1 body can be held. The web tier's `/api/adg` proxy sends no ETag and
  forwards no `If-None-Match`, and server fetches use `cache: "no-store"`.
- So the visible effect is nil. Every browser gate below recorded no request loops, no stale
  rendering and no application console errors.
- A real 1.1 ETag could not be captured first: Render was already serving `021562b` when the
  attempt ran (17:28:35Z).

## 8. Standard hosted gates (smoke / browser)

| Gate | Result |
| --- | --- |
| Smoke in run 40550373551 (committed `smoke.mjs`) | **22/23**: "representative principal loads (scenario F person) — HTTP 307" |
| Browser gate in run 40550373551 | **not run** (the smoke step failed first) |
| Smoke from this Mac, one-line path adaptation ([diff](ADG-UI-STAGING-PROMOTE-01-evidence/harness/smoke.mjs.diff)) | **23/23**, including origin protection: 12 requests → 403 without the origin key or with a guessed one; only `/health/live` open ([log](ADG-UI-STAGING-PROMOTE-01-evidence/checks/smoke-local.log)) |
| Browser gate from this Mac, committed `browser.mjs` **unmodified** | **22/22** ([log](ADG-UI-STAGING-PROMOTE-01-evidence/checks/browser-local.log)) |

Root cause of the 307:
- `smoke.mjs:308–311` requests `/identities/principal?key=<sid>`, and its `request()` uses
  `redirect: "manual"`.
- On `021562b`, `curl` shows that address answering **307** →
  `/identities/principal/S-1-5-21-…-1323`, and that page answers **200** (0.99 s).
- That is the reconciled route model (§11). `navigation.mjs`, which this lane was asked to run,
  checks this exact redirect and passes it.
- `smoke.mjs` has not been edited since the stack was ported (`25c4d7e`); the reconciliation lane
  ran `browser.mjs` but not `smoke.mjs`.
- The adaptation for the local run replaces only that one path with the canonical
  `/identities/principal/${encodeURIComponent(sid)}`.

The local `browser.mjs` run logged two console errors, both expected:
- `/favicon.ico` 404 (known);
- the 405 from the gate's own `POST /api/adg/api/v1/scan-runs` probe.

The local runs used Playwright 1.62.1 with arm64 Chromium 1234. CI pins 1.56.1 on Ubuntu 24.04.

## 9. Users / Groups directories and Exclude Local Groups (hosted UI)

`navigation.mjs` (§12), the reconciliation presentation checks (§10), and a supplemental script
for what those two don't cover
([`harness/directory-ui.mjs`](ADG-UI-STAGING-PROMOTE-01-evidence/harness/directory-ui.mjs),
signed in as `viewer`, totals read from the API rather than hard-coded). Result: **23/23**
([`checks/directory-ui.json`](ADG-UI-STAGING-PROMOTE-01-evidence/checks/directory-ui.json)).

**Users:**
- "1885 users in total";
- Next opens page 2 and Previous returns to page 1, both client-side;
- name prefix `Bianca` returns 13;
- SID prefix returns 32;
- `NULLMERIDIAN\brossi` returns 1;
- the name links to `/identities/principal/{storage key}`.

**Groups:**
- "238 groups in total", and Exclude local groups is **off** by default.
- Toggling it on page 2:
  - is a client-side navigation;
  - writes `exclude_local=1` to the URL;
  - **resets to page 1**;
  - shows "205 groups in total", with no `local group` kind on the page.
- Domain Local rows are present with the filter on.
- With search `A`:
  - 41 → 8 when toggled, and the term stays in the URL and the box;
  - **Back** restores 41 with the box unchecked;
  - **Forward** restores 8 with it checked.
- A column sort then reorders the filtered page without navigating.
- The whole sequence made **no full document reload**.
- No application console or page errors.

## 10. Compact identity, `/risks`, deleted state (hosted)

The reconciliation lane's
[`presentation.script.js`](ADG-UI-RECONCILE-01-evidence/harness/presentation.script.js) ran
unmodified at 1360 px and 390 px. Result: **23/23**
([`presentation/`](ADG-UI-STAGING-PROMOTE-01-evidence/presentation/), screenshots included).

**`/risks`:**
- The severity strip has `list-style: none` and `role="list"`, with 5 items and 0 overlaps:
  the bullet/count overlap is gone.
- Counts are **critical 3, high 8, medium 1957, low 423, informational 6995**.
- API ([`hosted/risks-after.json`](ADG-UI-STAGING-PROMOTE-01-evidence/hosted/risks-after.json)):
  `basis_current: true`, evaluated basis `0d1cc4aa…`, total 9,386.
- "The last evaluation predates snapshot recording" is **absent**.
- The only reason is the designed "Collection is incomplete: 98 of 100 collection scopes …".

**Deleted state** (unresolved SID `…-1108`):
- The banner reads "recorded as unresolved (**deleted**)", and Identity information reads
  "Deleted: **yes**", at both widths.
- The old "(deleted)" / "Deleted: no" contradiction does not recur.

**Compact identity:**
- Groups rows are one line (39.8 px) and carry no SID under a name.
- SID cells keep the full value in text and `title`, cut at the start (`direction: rtl`).
- The `DOMAIN\name` hint stays on one line at 390 px.
- The unresolved identity still shows its SID at 390 px.
- **No horizontal page overflow at 390 px** on Users, Groups (filtered), Bianca's principal and
  effective groups, the unresolved principal, `/risks`, and the Apollo explanation.

## 11. Principal route model

Covered on hosted by `navigation.mjs` (§12), DEMO-01 and the #418 rounds:
- the canonical `/identities/principal/{key}/{view}` loads, including deep links after a fresh
  load;
- view switches are one soft navigation each, with no chain-link prefetch;
- the Identity information disclosure stays stable across every view;
- shortest-chain hops are named in order;
- resolved group and user hops open `/identities/principal/{sid}`;
- Back/Forward work;
- legacy `?key=` redirects to the path.

Null Meridian has no unresolved hop inside any membership chain, so "unresolved hops remain
unlinked" is covered only by the unit and DB tests. This is the reconciliation lane's
limitation 4, unchanged.

## 12. Navigation gate (hosted)

`frontend/cloudflare/checks/navigation.mjs`, **unmodified**, with
`STAGING_URL=https://adg-demo-staging.d-walkes.workers.dev` and the default `SIGN_IN_AS=viewer`:
**19/19** ([log](ADG-UI-STAGING-PROMOTE-01-evidence/checks/navigation.log)). The one console error
is the known `/favicon.ico` 404.

This is the hosted confirmation the reconciliation lane could only rehearse locally.

## 13. DEMO-01 / DEMO-02 / DEMO-03 (hosted, as `planner`)

The committed scripts were copied out and changed by string replacement only. Each copy's diff is
under [`harness/`](ADG-UI-STAGING-PROMOTE-01-evidence/harness/). The changes:

1. Chromium path → arm64 Chromium 1234.
2. `const BASE = process.env.ADG_WEB_URL || "http://localhost:3000"` (DEMO-02 and DEMO-03).
3. `username: process.env.ADG_SIGN_IN_AS || "auditor"`. The hosted demo offers only `viewer` and
   `planner`; `auditor` gets a 404. This is the same as substitutions 1–3 of
   `ADG-HOSTED-NEXT16-01-evidence/hosted-script-substitutions.txt`.
4. **DEMO-02 only:** `await page.waitForURL(/\/identities\/principal\?/)` →
   `/\/identities\/principal\//`. "Inspect the SID" now opens the canonical path. The pattern is
   the only change, and this is the same adaptation the reconciliation lane recorded. The
   committed walkthrough is unchanged (follow-up in §19).

| Gate | Result |
| --- | --- |
| DEMO-01 | **15/15**. SYSTEM over SMB: ambiguous mapping, certainty uncertain, outcome indeterminate, no "No access". SYSTEM local: confirmed, `at_least`. Unresolved SID: an alert banner naming the key and the reason, no invented name. Its legacy `?key=` load followed the redirect. |
| DEMO-02 | **29/29** with adaptation 4 |
| DEMO-03 | **37/39** in 3 of 3 runs. Steps 1–9 pass every time. The 1 ms drill shows "timed out" in words and a symbol, with no partial list. The standard-window retry stops **incomplete** at a named boundary, labelled partial, naming the first attempt. Live `role=status` announcements, Diagnostics by keyboard, no 390 px overflow. Step 10 (the folder-permission-entries recovery, `kind=ntfs_ace`) passed its **4-second window** and showed `⏱ Timed out` instead of `✓ Complete … recovered`. Browser times: 4,470 / 4,337 / 4,396 ms. |

**DEMO-03 step 10 is the known free-tier limitation, and the behavior is correct:**

- The comparison code is byte-identical between `f730d99` and `021562b`: `git diff` of
  `backend/app/changes`, `app/api/changes.py` and `app/history` is empty.
- ADG-HOSTED-NEXT16-01 recorded this exact failure on hosted: step 10, `kind=ntfs_ace`, timed
  out at 4,007 ms (one of its three runs fully passed).
- The same request with the maximum window, `time_budget_ms=8000`, **completes** twice:
  `outcome: complete`, **221 differences** (177 added, 37 modified, 7 removed). Server time was
  4,308 ms, against the 4,000 ms interactive window on Render's 0.1 CPU.
- When it times out, the UI says so honestly and presents no comparison.
- Coverage semantics, timeout → incomplete, and the risk basis are all intact.

## 14. React #418

The committed detector
([`hydration-gate.script.js`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js))
ran against `https://adg-demo-staging.d-walkes.workers.dev` as `planner`, with two sets of
changes:
- the hosted substitutions 1 and 3 (Chromium path, `ADG_SIGN_IN_AS`);
- the reconciliation lane's 10 extra full-load routes and extended phone phase, applied line for
  line from its committed diff
  ([diff vs committed](ADG-UI-STAGING-PROMOTE-01-evidence/harness/hydration.js.diff)).

The extra routes cover:
- Users;
- Users with name search and with SID search;
- Groups;
- Groups with Exclude local, with and without search;
- Bianca's effective and direct groups;
- Fatima's shares;
- the unresolved principal.

The nav phase covers client transitions, including the access explanation and the comparison
drill. Risks and the other primary sections are full-load routes too.

| Run | UTC | Records | Full loads | Client transitions | **#418** | Hydration family | Other errors | Failed steps | 390 px overflow |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Positive control (`GATE_CONTROL=1`, 1 round, 1 reload) | 17:48:11–17:53:59 | 113 | 95 | 18 | **95** (every full load) | 0 | 0 | 0 | 0 |
| Hosted (`GATE_ROUNDS=2`, 3 reloads) | 17:54:31–18:15:54 | 334 | 298 | 36 | **0** | **0** | **0** | 0 | 0 |

The control detects #418 on all 95 full loads, so the real result is valid. Per round, the real
run had 0 #418 in every phase: fresh, reload, nav, keys and phone. The only error recorded is the
known `/favicon.ico` 404 (`knownErrors`). Evidence:
[`hydration/`](ADG-UI-STAGING-PROMOTE-01-evidence/hydration/).

**#418 = 0, hydration-family errors = 0, application console errors = 0.**

## 15. Final refs and tree

After a final fetch (18:16Z):

| Ref | SHA |
| --- | --- |
| `main` / `origin/main` | `021562b41b859f0a3f0600824ba0ccf95c3b6b69` |
| `deploy/ADG-HOSTED-NEXT16-01` / `origin/deploy/ADG-HOSTED-NEXT16-01` | `021562b41b859f0a3f0600824ba0ccf95c3b6b69` |

State of the T5 checkout:
- The canonical checkout is clean (`git status --short` empty).
- After the fetch, the new pack's three `._pack-*` sidecars were removed and `git fsck` passed.
- The T5 stayed mounted throughout.

This report is the only commit, on the docs-only branch `docs/ADG-UI-STAGING-PROMOTE-01` off
`021562b`. Pushing that branch triggers no deployment, because *Free staging* runs only for
`deploy/ADG-HOSTED-NEXT16-01`. A later archival task can move it to `main` without advancing the
deploy branch.

## 16. What was not done

- No database reset, bootstrap, migration or `demo_mode reset`. No write to `adg_demo`.
- No manual Render deploy, no wrangler deploy, no `workflow_dispatch`, no rerun of the failed
  job (it would fail the same way at the same SHA).
- No GRAPH work.
- No application commit and no harness fix committed. `main` is unchanged.
- No rollback. The deployed application is correct; the red run is a stale harness assertion.
  Rollback, if wanted: Render `dep-dbg3k3t9fdbs73cm2v40` (`f730d99`) + Worker `d2ca8cfe`.

## 17. T5-only artifacts

- Run logs, the harness workspace (Playwright 1.62.1) and every screenshot are under
  `/Volumes/T5 EVO/mmm/adg-run-logs/PROMOTE-01/`.
- This report's branch is checked out in `/Volumes/T5 EVO/mmm/worktrees/adg-UI-STAGING-PROMOTE-01`.
- Secrets stayed in `~/.config/adg-staging` and were never printed or copied.
- This report and its evidence contain no database URL, token, origin key, account id or
  workspace id.

## 18. Limitations

1. **The standard smoke gate is red on the deploy-branch head** (run 40550373551, 22/23) because
   of the stale `?key=` assertion in `smoke.mjs`. Until it is fixed, every push to the deploy
   branch will end `failure` and skip the browser step.
2. **DEMO-03 step 10** exceeds the 4 s interactive window on Render Free, as before. The behavior
   is correct, and 8 s completes it.
3. **Unresolved chain hops** have no instance in Null Meridian, so only tests cover them.
4. **Local browser runs** used Playwright 1.62.1 / Chromium 1234 on a heavily loaded Mac (load
   average up to ~100 from unrelated xcodebuild jobs). No check failed on timing except DEMO-03
   step 10, which is server-side.
5. **The 1.1 → 1.2 transition was synthesized** from the live 1.2 ETag (same basis, `1.1-`
   prefix), because Render had already switched before a real 1.1 ETag could be captured.

## 19. Recommended next action

**Fix `smoke.mjs` in its own small lane on `main`** (suggested ID
`ADG-SMOKE-CANONICAL-ROUTE-01`). Change line 310 to request
`/identities/principal/${encodeURIComponent(person.sid)}`, exactly as in
[`harness/smoke.mjs.diff`](ADG-UI-STAGING-PROMOTE-01-evidence/harness/smoke.mjs.diff).

In the same lane:
- point DEMO-02's committed walkthrough at the canonical path;
- consider a smoke check that the legacy address answers 307 to the path.

Then, in an explicit deploy task, fast-forward the deploy branch to that commit. A push deploys,
and the application is unchanged, so only the run's result should change: smoke 23/23 and browser
22/22 in CI.

Alternatively, the owner may accept this promotion with the smoke check recorded as a stale
harness assertion, and reclassify it as `ADG_UI_STAGING_PROMOTED_WITH_LIMITATIONS`.
