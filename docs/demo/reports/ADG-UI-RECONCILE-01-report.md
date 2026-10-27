# ADG-UI-RECONCILE-01 — UI-UPDATE-01 and COMPACT-IDENTITY-01 re-ported onto the Next.js 16 `main`

**Verdict: `READY_FOR_ADG_UI_RECONCILE_MERGE_WITH_LIMITATIONS`** (limitations: §19).

The Users and Groups directories, the path-keyed principal model, named and navigable
shortest chains, `SortableTable` across every list table, compact identity, and *Exclude
local groups* now run on current `main` (Next.js 16.4.1, React 19.3.0, ADG-DEMO-01/02/03).
This is a semantic port: each historical commit was classified first, then applied with its
own parent as the merge base and adapted where `main` has moved. No historical branch was
merged. The branch also fixes the `/risks` bullet overlap and the unresolved
deleted-state contradiction. The derived contract goes from 1.1 to 1.2 under the
repository's own rule. There is no migration, and nothing was deployed.

## 1. Starting point

| | |
| --- | --- |
| `main` at start | `1687691c381bb491fe3e0dc3f2243a798055e126` (= `origin/main`) |
| Canonical deploy branch | `deploy/ADG-HOSTED-NEXT16-01` @ `f730d9922f2f76583d0c55eea9afa917293f870a`, unchanged throughout |
| Worktree | fresh, from `origin/main`; clean-tree gate passed (`status`, `diff --quiet`, `diff --cached --quiet`) |
| Branch | `feature/ADG-UI-RECONCILE-01` (no other prefix needed) |

## 2. Historical sources (reference only, none merged)

| Ref | Head (fetched 2026-10-27) | Used for |
| --- | --- | --- |
| `fix/ADG-UI-UPDATE-01` (local) | `a5cdf887fdaa82ebc2c299566f1b38fd9f5d382c` | `61fedb8`, `552bcb8`, `371b90e`, `861a11b`, `a5cdf88` |
| `origin/fix/ADG-UI-COMPACT-IDENTITY-01` | `f50e8f28aab44211b0bfb61f9bcd5d98ae2add7b` | `4f33e08`, `98a9a79`, `f50e8f2` (`0a663225`: handoff only) |
| `origin/integration/ADG-NEXT16-UI-02` | `3e26e4ad3fd351b5a8e956ae392ce8088ac485d1` | `33cca01` (SIDs cut at the start); its merge `9502b4b` was clean, so it holds nothing else |
| `origin/integration/ADG-UI-EDGE-01` | `4212f13a756cbe2e4c0c2156849900637cffecd0` | not used (deploy integration and report) |
| Handoff | `git show f50e8f2:docs/handoffs/adg-ui-update-01.md` | the intended behaviour, §7 for compact identity |

Every one of them forks from `1ab01a2`, before ADG-DEMO-01. None has seen DEMO-01/02/03,
Next.js 16.4.1, contract 1.1 or the hosted stack.

## 3. Port matrix

The full matrix, written and committed (`cea0969`) before any code was changed:
[`port-matrix.md`](ADG-UI-RECONCILE-01-evidence/port-matrix.md). Method: each commit's own
diff was 3-way merged onto `main`, chained in order (`git merge-tree --merge-base <c>^`).
Textual conflicts: `frontend/app/identities/principal/page.tsx` (552bcb8) and the old
handoff (f50e8f2) only. Every file `main` had also changed was then read by hand.

| Commit | Class | Collision with `main` |
| --- | --- | --- |
| `61fedb8` API: `path_principals`, `GET /api/v1/principals` | PORT_WITH_CURRENT_MAIN_ADAPTATION | `TokenEntryView` sits inside two derived schemas, so the **contract bumps 1.1 → 1.2** (§6). `access.py` / `services/access.py` gained DEMO-01/02 code in other hunks. |
| `552bcb8` directories, `[key]/layout` + `[[...view]]`, chains, SortableTable | PORT_WITH_CURRENT_MAIN_ADAPTATION | **DEMO-01:** the unresolved-record banner on the old page moves into the layout. **DEMO-01:** `PrincipalChain` uses `isUnresolved`, not `resolved`. **DEMO-02:** a next-step test pinned `?key=`. |
| `371b90e` `navigation.mjs` | PORT_AS_IS | a check script, not deploy config |
| `861a11b` repository registries | PORT_AS_IS | none |
| `a5cdf88` every list table sortable | PORT_AS_IS | `Explanation.tsx` (DEMO-01 SYSTEM row) merges in other hunks. DEMO-era tables (Investigation, Coverage, Comparison, compare) were deliberately **not** converted. |
| `4f33e08` compact identity | PORT_WITH_CURRENT_MAIN_ADAPTATION | **DEMO-01:** an unresolved principal under a last known name keeps its SID on screen (§7). |
| `98a9a79` Exclude local groups | PORT_AS_IS | OpenAPI regenerated, identical to the merge |
| `f50e8f2` test typing | PORT_AS_IS (test) / DO_NOT_PORT (handoff) | the old handoff stays historical |
| `33cca01` SIDs keep their end | PORT_AS_IS | closes the compact-identity handoff's open item |
| deploy commits, Next 16.4.0 `66ca201`, package manifests, Worker/Render config, DIRECTORY-ACCESS-01, GRAPH | DO_NOT_PORT | `main` is authoritative |

Collisions checked and found **absent**:
- Next 16 config, `package*.json`, `eslint.config.mjs`, `tsconfig.json`, `wrangler.jsonc`,
  `render/`, `cloudflare/{worker,routing,environment}.ts`, `docker/`, `.github/`, Alembic:
  `git diff 1687691..HEAD` is empty for all of them.
- DEMO-03 Risks/coverage: only the strip's CSS and `role` changed, and the counts are
  identical (§9).

## 4. What was recreated

Commits on `feature/ADG-UI-RECONCILE-01`, in order:

| Commit | |
| --- | --- |
| `cea0969` | port matrix |
| `09ab56e` | port of 61fedb8: `path_principals`, `GET /api/v1/principals` |
| `9d4c372` | port of 552bcb8, with the DEMO-01 banner moved into the layout and the DEMO-02 test on the canonical path |
| `af324f0`, `3ac0f0c`, `ee0a856`, `b342355`, `431fece` | 371b90e, 861a11b, a5cdf88, 4f33e08, 98a9a79 (`cherry-pick -x`, applied clean) |
| `35fabfe` | f50e8f2, test change only |
| `6ae74f3` | 33cca01 (`-x`) |
| `0d7965c` | chain hop of kind `unresolved` stays text (DEMO-01) |
| `19eeeea` | one deleted state; `/risks` strip markers |
| `a3e3691` | contract 1.2 |
| `b6c1d62` | unresolved principal keeps its SID on screen (DEMO-01) |
| `3738982` | test: severity/confidence rank sort, page caption |
| `4e87052` | `DOMAIN\name` hint never breaks mid-token at 390 px |

The behaviour now on `main`'s line:

* **Users and Groups directories** (`/identities/users`, `/identities/groups`, beside Servers
  under `DirectoryTabs`):
  * served by `GET /api/v1/principals?kind=users|groups&q=&exclude_local=`;
  * keyset pages ordered by storage key, with an exact `page.total`;
  * search on a name prefix (display, account, UPN, last known name; case-insensitive), a SID
    prefix (`S-1-`), and `DOMAIN\name` on the account name; LIKE wildcards are escaped;
  * direct member and direct group counts as correlated index counts. There is no traversal
    per row, and the directory sits under the N+1 statement guard (`test_query_cost`);
  * empty, error and coverage states use the existing `classify` / `StateMessage` /
    `CoverageCaveat`;
  * rows link by storage key.
* **Principal route model** `/identities/principal/{key}/{view}`:
  * the layout reads the identity and collection state once per request (React `cache`, and
    `currentViewer` is cached the same way);
  * the view page runs only its own query, so a view switch is one RSC request with no
    identity read (`navigation.mjs`: "one view switch is one request");
  * an invalid view is a 404;
  * `?key=&tab=&p=&include=&host=` redirects to the key's path, with `?host=` resolved to the
    storage key by the API first;
  * deep links and Back/Forward work. On Next 16.4.1 the async `params` / `searchParams` and
    the `[[...view]]` optional catch-all needed no change.
* **Shortest chains**: `PrincipalChain` in the principal page's effective groups and members,
  who reaches a directory, the explanation's route table and simulated surviving routes.
  * A resolved hop is its name, linked by storage key, with the key as `title`.
  * A hop that is unresolved in either sense stays unlinked text: no record, or a record of
    kind `unresolved` (`isUnresolved`, DEMO-01). It shows its SID or the last known name the
    record holds, never an invented one.
  * Computer principals are linked principals (unit test).
  * A cycle's `representative_path` is still shown as keys, as before.
* **Sortable tables**: `SortableTable` + `lib/sort.ts` cover all 40 list tables the
  historical lane converted.
  * Text sorts case-insensitively, numbers numerically, dates chronologically, booleans false
    first, and blanks go last both ways; ties keep the server's order.
  * Severity and confidence sort by rank.
  * Each header is a `<th aria-sort>` holding a real `<button>`, and sorting is client-side
    only.
  * Paged tables say "Sorting orders the rows on this page."
  * Deliberately unsorted: before/after field comparisons, change impact, review-item access,
    the collectors' stored counts, the demo guide's scan history, and the DEMO-02/03 surfaces
    added after the fork.

## 5. Deliberately excluded

* The old branches' deploy history, Worker/Render/Wrangler config, Next 16.4.0 and package
  manifests (`main` is authoritative), plus the handoffs `adg-ui-update-01.md` (cited by SHA
  here), `adg-ui-edge-01.md` and `adg-next16-ui-integration-02.md`.
* ADG-DIRECTORY-ACCESS-01 (on `release/ADG-UI-EDGE-DIRECTORY-02`) and GRAPH (its second
  `0018`).
* Converting DEMO-02/03 surfaces to `SortableTable`, and turning the Investigation's route
  summaries into links. Those chains already render names, and they sit inside `<summary>`,
  where links would be interactive content in a toggle.

## 6. API and contract effects

* **New (additive):** `GET /api/v1/principals?kind=users|groups&q=&limit=&cursor=&exclude_local=`,
  behind `identities:read` like the rest of the graph router. It is in the authorization
  matrix test. The history repository serves it current-state, and the simulation overlay
  refuses it (registry guard tests).
* **New field (additive):** `path_principals` on `EffectiveMemberView` (not derived) and on
  `TokenEntryView`. It is labelled from records each answer already read: no extra statement
  for effective members, and the resource-principals read widens its one label query.
* **Contract 1.1 → 1.2.**
  * `TokenEntryView` is part of the access-explanation and resource-impact derived responses.
    The repository's rule (`app/api/caching.py` `CONTRACT_VERSION`,
    `docs/contracts/README.md` §Versioning, and the precedent `60bae4b` 1.0 → 1.1) bumps the
    minor for an additive shape change. Otherwise a cached 1.1 body without the labels could
    be revalidated as current.
  * The historical lane added the field without bumping; this port does not repeat that.
  * Schemas and both OpenAPI snapshots were regenerated, and only the version moved between
    the 3-way merge and the regeneration. The examples were re-captured from live responses
    (`ADG_WRITE_EXAMPLES=1`, `test_explanation_api`).
  * `derived-responses.md` names the field. The three tests that pin the version moved with
    it.
* DEMO-01 ambiguity, DEMO-02 `at_least` certainty and DEMO-03 coverage/recovery semantics are
  unchanged: no engine, service or response logic was touched beyond the label read.

## 7. Compact identity

* Name cells hold the name alone; no SID or key is stacked under a resolved name. The link
  opens by storage key and carries the key as `title`, and Identity information shows SID
  and key in full.
* Machine identifiers render through `Identifier` / `Sid`:
  * one line, at most 16 rem, ellipsis;
  * the full value in the text (screen reader, copy) and in `title`;
  * shown in full while keyboard focus is in the row;
  * SIDs cut at the start (`direction: rtl`), so the RID stays visible;
  * code values in table cells never break mid-value.
* **Adapted for DEMO-01:** an unresolved principal shown under a *last known* name keeps its
  SID on its own short line above the warning.
  * The name is a hint an ACE carried, and the SID is the identity, so hiding it would hide
    unresolved-state information.
  * This was found by DEMO-01's own `investigation-page` test, which failed after the port
    until this was done.
  * Resolved names are unaffected.
* Status notes (unresolved, disabled, deleted) stay visible under the name, as both lanes
  intended.
* Measured at 1360 px and 390 px ([`presentation.json`](ADG-UI-RECONCILE-01-evidence/presentation/presentation.json)):
  * every Groups row is 39.8 px (one line), with no SID in any Name cell;
  * the SID cell keeps the full text and `title` and is `rtl` / `nowrap`;
  * no page-level horizontal scroll at either width.

## 8. Exclude local groups

* **Server-side.** `exclude_local=true` drops `PrincipalKind.LOCAL_GROUP` from the kinds that
  are listed and counted, so results, search, keyset paging and `page.total` agree.
  Active Directory **Domain Local** groups (`domain_group`, scope `domain_local`) are kept.
  It is off by default; omitted and explicit `false` are identical.
* DB tests cover:
  * default and `false`;
  * `true` keeping the Domain Local group;
  * search × filter (5 cases);
  * paging × filter, where the total and the walk agree.
* **UI.** A native labelled checkbox (`FilterToggle`) is tied to the directory's `next/form`
  GET form.
  * A change submits as a client-side navigation: the search is kept and the page position
    resets.
  * The URL carries `exclude_local=1`, the pager carries it, and Back/Forward restore the box.
  * The table stays mounted, so the reader's sort survives.
  * It is not offered for users.
  * 8 unit tests.
* **Browser:** "Groups: Exclude local groups filters on the server, keeps search and sort, no
  reload". In Null Meridian it removes 33 local `Administrators` groups and keeps the 5 Finance
  groups.

## 9. `/risks` bullet overlap

* **Cause:** the severity counts are a `<ul class="filter-strip">` laid out as flex, and the
  list's default disc markers sat on the previous count's last digit.
* **Fix (CSS only):** `ul.filter-strip { list-style: none; padding-left: 0 }`, plus
  `role="list"` so Safari still announces it as a list.
* **Browser:**
  * `list-style-type: none`;
  * 5 items with 0 overlapping boxes at 1360 and 390 px;
  * the text still reads `critical 3 high 8 medium 1957 low 423 informational 6995`;
  * `browser.mjs`'s risk-total check passes.

The counts and DEMO-03 semantics are unchanged.
([screenshot](ADG-UI-RECONCILE-01-evidence/presentation/risks-severity-strip-desktop.png))

## 10. Deleted-state investigation

**Result: presentation-only. Fixed.** It did *not* need `DELETED_STATE_SEMANTICS_NEEDS_SEPARATE_FIX`.

* **The banner** read `unresolved_reason = "deleted"`. `UnresolvedReason.DELETED` means "the
  account is known to have been deleted (an orphaned ACE)", a collector's report.
* **The facts row** read `is_deleted`. That is a directory fact only `User` and `DomainGroup`
  carry (`domain/identity.py`). An `UnresolvedPrincipal` never reports it, so its stored
  `false` is the column default, not an observation.
* The risk engine already reads both as "deleted" (`risk_engine/rules.py`).
* **The fix:** `deletedState` (`lib/identity.ts`) derives one answer from both fields:
  * `is_deleted` → **yes**;
  * an unresolved record with reason `deleted` → **yes**, worded "as the run that could not
    resolve this SID reported it";
  * any other unresolved record → **not reported**;
  * otherwise → **no**.
* No stored value, API field or domain rule changed.
* Tests: a unit test for all four states, and a page test where the banner and the facts
  agree. In the browser, the unresolved SID `…-1108` shows banner "(deleted)" and Deleted
  "yes — …" ([screenshot](ADG-UI-RECONCILE-01-evidence/presentation/unresolved-principal-desktop.png)).

## 11. Backend

Python 3.13.15, `uv` venv, against a throwaway `postgres:16-alpine` that matches CI. It bound
127.0.0.1 only, kept its data in tmpfs, and used a generated password. The run was at
`a3e3691`; later commits touch no backend file.
([`backend-gates.txt`](ADG-UI-RECONCILE-01-evidence/backend/backend-gates.txt))

| Gate | Result |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 414 files already formatted |
| `mypy app tests` | no issues in 413 source files |
| `pytest -q -m "not smoke"` | **6,133 passed**, 12 skipped |
| `pytest -q -m smoke` (`ADG_RUN_SMOKE_TESTS=1`) | **1,298 passed**, 1 xfailed (the pre-existing `test_access_performance.py` marker), 22 min 55 s |
| `tests/contracts` | 719 passed, 1 skipped |
| `alembic heads` | one head: `0018_risk_evaluation_basis` (no migration added) |

## 12. Frontend

Node 22.23.3 arm64, at `4e87052`.

| Gate | Result |
| --- | --- |
| `npm ci` (clean, from the unchanged lockfile) | 489 packages |
| `npm run lint` | clean |
| `npm run typecheck` / `cf:typecheck` | clean / clean |
| `vitest run` | **44 files, 1,510 passed** |
| `npm run build` (`next build --webpack`) | ok; Next.js **16.4.1**, React **19.3.0**; framework files byte-identical to `main` |

## 13. Browser and navigation

Local rehearsal of the hosted topology:
- `next build` standalone with `ADG_BUILD_ID`;
- `render/gate.ts` supervising FastAPI and Next on loopback, with a throwaway origin key;
- a 15-line loopback proxy adding `X-ADG-Origin-Key`, doing the Worker's job
  ([`key-proxy.script.mjs`](ADG-UI-RECONCILE-01-evidence/harness/key-proxy.script.mjs));
- a local `adg_demo` reset to Null Meridian, fingerprint
  `228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`, `verify` ok;
- Playwright 1.62.1, arm64 Chromium 1234.

The hosted demo, the hosted database and the Worker were not touched. The final results below
are on `4e87052`.

| Gate | Result |
| --- | --- |
| `navigation.mjs` (adapted historical gate, as viewer) | **19/19**, 0 console errors ([final](ADG-UI-RECONCILE-01-evidence/browser/navigation-final.json)) |
| `browser.mjs` (existing public gate) | **22/22**. Its one console error is the 405 its own check provokes. Run 1 had an account-list mismatch: the local stack added `auditor` for the DEMO harnesses, so the sign-in check refused and later checks ran signed out. Run 2 onward set `PUBLIC_ACCOUNTS` to the local list ([run 1](ADG-UI-RECONCILE-01-evidence/browser/browser-run-1-account-list-mismatch.json), [final](ADG-UI-RECONCILE-01-evidence/browser/browser-final.json)). |
| presentation checks (new, [script](ADG-UI-RECONCILE-01-evidence/harness/presentation.script.js)) | **23/23** at 1360 and 390 px |

Covered by the navigation gate:
- the Users, Groups and Servers strip;
- name and SID search;
- numeric sorts both ways;
- opening by storage key with no document reload;
- the Identity disclosure folded across every view, and keyboard-operable;
- one request per view switch, with no chain-link prefetch;
- shortest chains as names in order, group and user nodes opening their principals, and
  Back/Forward;
- Groups search, sort and view switching;
- Exclude local (server-side, search and sort kept, no reload);
- a deep link after a fresh load;
- the legacy `?key=` redirect.

The 390 px layout is covered by the #418 harness's phone phase (directories and principal
views, 0 horizontal overflow) and the presentation checks. That pass found the `DOMAIN\name`
hint breaking mid-token at 390 px; it was fixed in `4e87052` and re-measured.

## 14. DEMO-01 / 02 / 03

The committed harnesses ran with only the Chromium path swapped. DEMO-02 also needed its
hard-coded base URL made an env value, and one URL pattern changed
([diff](ADG-UI-RECONCILE-01-evidence/harness/demo-02-walkthrough.local.diff)).

| Gate | Result |
| --- | --- |
| DEMO-01 (`demo-01-ui.script.js`) | **15/15**: SYSTEM over SMB ambiguous / indeterminate, never "No access"; SYSTEM local confirmed, `at_least`; unresolved SID banner as an alert, naming the key and reason, inventing no name |
| DEMO-02 (`browser-walkthrough.script.js`) | **29/29**. The first run stopped at `waitForURL(/\/identities\/principal\?/)`: "Inspect the SID" now opens the canonical `/identities/principal/{key}`, which this lane is meant to produce. The pattern was changed to the path; nothing else. |
| DEMO-03 (`browser-walkthrough.script.js`) | **39/39**: coverage beside the totals, the comparison drill → incomplete → recovered, live statuses, Diagnostics by keyboard, no horizontal scroll at 390 px |

## 15. React #418

The committed detector
([`hydration-gate.script.js`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js))
ran with 10 extra full-load routes ([diff](ADG-UI-RECONCILE-01-evidence/harness/hydration-gate.reconcile.diff)):
- both directories;
- name and SID search;
- Exclude local, with and without search;
- three principal views;
- the unresolved principal's path.

The phone phase was extended to the directories and Bianca's effective groups. It ran both
direct and through the committed 40 ms stream-delay proxy, which reproduces the hosted timing.
Every attempt is recorded ([`hydration/`](ADG-UI-RECONCILE-01-evidence/hydration/)).

| Run | Full loads | #418 | Client transitions | #418 | Other / hydration-family errors | 390 px overflow |
| --- | --- | --- | --- | --- | --- | --- |
| control, direct (1 round) | 149 | **149** | 19 | 0 | 0 / 0 | 0 |
| real, direct (3 rounds) | 447 | **0** | 57 | 0 | 0 / 0 | 0 |
| control, 40 ms delay | 149 | **149** | 19 | 0 | 0 / 0 | 0 |
| real, 40 ms delay (3 rounds) | 447 | **0** | 57 | 0 | 0 / 0 | 0 |
| final head `4e87052`: control direct / real direct / control delay / real delay (1 round each) | 149 each | **149 / 0 / 149 / 0** | 19 each | 0 | 0 / 0 | 0 |

The first four rows are on `3738982`; the only later change is one `className`.

The positive control detects #418 on every full load, including all 53 per round on the new
routes. Across 1,192 real full loads and 152 real transitions, 424 of them on the new routes,
there were:
- **0 #418**;
- **0 hydration-family errors**;
- **0 application console errors**.

The only known error is the pre-existing `/favicon.ico` 404.

## 16. CI

Branch push `4e87052`, run [40524532433](https://github.com/40ozs/adg/actions/runs/40524532433):
**success.** Backend (lint, types, tests) ✓, Frontend (lint, types, tests, build) ✓,
Collector (Pester) ✓, Docker Compose configuration ✓; "Fork PR Linux gates" skipped (not a
fork PR). No infrastructure failures.

Only `CI` ran. The *Free staging* workflow triggers on pushes to `deploy/ADG-HOSTED-NEXT16-01`
alone and did not run.

## 17. Final branch

`feature/ADG-UI-RECONCILE-01`: code head `4e87052`, validated above; one further commit adds
only this report and its evidence (its SHA is the branch head; a commit cannot name itself).

`deploy/ADG-HOSTED-NEXT16-01` = `f730d99` and `main` = `1687691` on `origin`, both unchanged.
No Render or Worker deploy, and `adg_demo` (hosted) was not touched or reset.

## 18. Clean tree

Before the report commit: `git status --porcelain=v1` empty, `git diff --quiet` and
`git diff --cached --quiet` true (build output, `node_modules` and the venv are ignored). The
local stack was stopped and the throwaway database container removed.

## 19. Recommendation

**Merge `feature/ADG-UI-RECONCILE-01` into `main` with `--ff-only`.** It is based directly on
`main` `1687691`. Do it as a separate merge task; do not merge in this lane. Then advance the
deploy branch only in an explicit deploy task: every push there deploys Render and the Worker.

Limitations to carry into that task:

1. **Contract 1.2.** It is additive and served to any v1 client. Cached 1.1 bodies revalidate
   to 200, not 304, once after deploy.
2. **Browser validation was local, not hosted.** It ran on this Mac against a local Null
   Meridian with the gate in front, plus a stand-in key proxy for the Worker. The deploy task
   should run `navigation.mjs` against staging; it needs `SIGN_IN_AS=viewer`, which is its
   default.
3. **Sorting orders the page in hand**, as designed and as the captions say. Directory search
   matches prefixes only, as global search does.
4. **Demo data gaps.** Null Meridian has no unresolved or computer hop in any membership
   chain. Those cases are covered by unit and DB tests only (the historical limitation,
   unchanged).
5. **Ported test assertion.** The DEMO-02 committed harness asserts the old `?key=` address.
   It was adapted locally (diff above) and is not changed in the repository, since it is
   historical evidence.
