# ADG-UX-WAVE-D-INTEGRATION-01 — UX Wave D integration

## 1. Verdict

**`READY_FOR_ADG_UX_WAVE_E_WITH_LIMITATIONS`**. The limitations are in §22. CI run 41223498140 on
the integration head is green (§24).

NAV-07 (client navigation from a campaign to its review item), REVIEW-NAMES-08 (scope display
names) and TEST-STABILITY-09 (a deterministic titles journey) are merged into
`integration/ADG-UX-WAVE-D-01` on top of the Wave C baseline.

- **Conflict.** One merge had a textual conflict, in the titles browser gate. Both sides were kept
  (§7).
- **Product code.** No product file was edited during integration.
- **Integration commits.** One DB test pins the current-name versus frozen-name semantics (§9),
  and one commit adds gate and fixture tooling (§18).
- **Gates.** Every backend, DB, frontend and browser gate passes on the combined tree. The browser
  gates ran on a freshly reset canonical dataset, with test-owned fixtures.
- **Hosting.** Every hosting path is byte-identical to Wave C (§21).

Not merged to `main`. Not deployed.

## 2. Starting baseline

| | |
|---|---|
| Baseline | `origin/integration/ADG-UX-WAVE-C-01` @ `559dbaf913a02e0df4ddf18be89ef81f03e48596` (Wave C verdict `READY_FOR_ADG_UX_WAVE_D_WITH_LIMITATIONS`) |
| Baseline CI | [41167906074](https://github.com/40ozs/adg/actions/runs/41167906074) on that exact SHA: **success** (Backend, Frontend, Pester and Compose passed; fork-PR job skipped) |
| Newer baseline? | **None.** After `git fetch --all --prune` (2026-10-31 16:40Z), the only `integration/ADG-UX-*` refs are Wave A `fcb7928`, Wave B `10c73d1` and Wave C `559dbaf`. The refs containing `559dbaf` are the three source lanes and the two HOSTED-STREAMING docs branches; neither kind is an integration. |
| Branch / worktree | `integration/ADG-UX-WAVE-D-01`, `/Volumes/T5 EVO/mmm/worktrees/adg-UX-WAVE-D-01` |
| Reports read first | Wave C integration, NAV-07, REVIEW-NAMES-08 and TEST-STABILITY-09. HOSTED-STREAMING-11 (`origin/docs/ADG-HOSTED-STREAMING-11` @ `77b4400`) was read for release context only and is **not** merged here. |

## 3. Source admission

[`source-ci.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/source-ci.txt): each origin head was
compared with the SHA the brief supplied, and each run was read with `gh run view`.

| Lane | Origin head = supplied | Base | Exact-head CI | Admitted |
|---|---|---|---|---|
| NAV-07 | `70d08e7bfd418305cf53a8cc58968732c25dfbeb` ✔ | `559dbaf`, 3 ahead / 0 behind | [41173158797](https://github.com/40ozs/adg/actions/runs/41173158797) **success** | **YES** |
| REVIEW-NAMES-08 | `68770398ea4d3de5e17045fe829a3310f99a3c95` ✔ | `559dbaf`, 3 ahead / 0 behind | [41177759881](https://github.com/40ozs/adg/actions/runs/41177759881) **success** | **YES** |
| TEST-STABILITY-09 | `9c67d2c1456ceaec933b9250713236adc5293a72` ✔ (brief: `9c67d2c`) | `559dbaf`, 2 ahead / 0 behind | [41190074573](https://github.com/40ozs/adg/actions/runs/41190074573) **success** | **YES** |

All four jobs passed on each exact head: Backend (lint, types, tests, DB), Frontend, Pester and
Compose. No source branch was substituted, rebased or modified.

## 4. Source SHAs

```
NAV_SOURCE_SHA=70d08e7bfd418305cf53a8cc58968732c25dfbeb      (code d38c68b)
NAMES_SOURCE_SHA=68770398ea4d3de5e17045fe829a3310f99a3c95    (code 9411659)
STABILITY_SOURCE_SHA=9c67d2c1456ceaec933b9250713236adc5293a72 (code c2c629c)
```

## 5. Merge order

NAV-07 → REVIEW-NAMES-08 → TEST-STABILITY-09, as the brief prefers. Each is a `--no-ff` merge
commit. There was no squash and no rebase.

## 6. Merge SHAs

| Commit | What |
|---|---|
| `3fbb0adafb1fb9e28a0c86f2b77cbbb70dd8e8de` | merge NAV-07 (parents `559dbaf`, `70d08e7`) |
| `7544c9c338af4c8648c81bfed5f7ed657a3c3c9f` | merge REVIEW-NAMES-08 (parents `3fbb0ad`, `6877039`) |
| `abf2d00dc81fcb4247c4d3642fb5bc78b111115f` | merge TEST-STABILITY-09 (parents `7544c9c`, `9c67d2c`), with the titles-gate resolution |
| `7a12b8e7146a7b42de5cb0125f5706bc074d1dd4` | test: current scope name vs frozen item name (DB test only). **Last commit touching `frontend/` or `backend/`** |
| `e2fd131` | test tooling: Wave D combined gate, COPY-04 current copy and historical header, fixture setup, contract script (docs/ only) |
| report commit | this report and its evidence |

Product code (`frontend/app`, `frontend/components`, `frontend/lib` and `backend/app`) last changed
in the REVIEW-NAMES-08 merge `7544c9c`.

## 7. Conflicts and resolutions

[`merge-summary.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/merge-summary.txt) has the per-file
check and the `--remerge-diff` of the resolution. Of the 60 files changed since `559dbaf`, 58 are
byte-identical to the one lane that changed them: 14 from NAV, 26 from REVIEW-NAMES and 18 from
TEST-STABILITY. Two files changed in two lanes:

| File | Lanes | Result |
|---|---|---|
| `frontend/tests/governance-views.test.tsx` | NAV + NAMES | auto-merged; NAV's item-table tests and NAMES' scope-name tests are both present, and vitest counts both (§17) |
| `frontend/cloudflare/checks/titles.mjs` | NAV + STABILITY | **conflict, 3 hunks**, resolved by keeping both sides |

**The titles-gate resolution.** Neither branch was accepted wholesale.

1. **Journey comment.** TEST-STABILITY's DATASET_NOW wording is combined with NAV's "no move may
   load a document". The obsolete `fullLoad` sentence is dropped.
2. **Campaign step.** It takes NAV's selector, which picks a row whose Items cell is non-zero and
   adds `:not([href*="?"])` so a filter link can never match. It keeps TEST-STABILITY's "newest
   first" comment. The item step has no `fullLoad`.
3. **Journey check.** It keeps NAV's strict test: the `window.__adgNoReload` sentinel plus
   `documents.length === 0`, with the `reloads()` exemption gone. It also keeps
   TEST-STABILITY's guard, which fails if the proxy is configured but no window read reaches it,
   and its window note. The note's variable is renamed `window` → `windowNote`. Otherwise it would
   shadow the browser global, which `page.evaluate(() => window.__adgNoReload)` uses in the same
   block.

The integrated gate therefore has all eight behaviours the brief lists:

- the deterministic window and the enforced proxy;
- the first campaign with items;
- no reload exception;
- the title assertions, the identifier-leak checks, Back and Forward, and the direct loads.

All of them were verified in §18–§19.

The Back/Forward checks merged without conflict and are NAV's strict versions.

## 8. Navigation behaviour

The campaign item link is NAV-07's `next/link` with prefetch off (`components/DecisionForm.tsx`).
NAV's gate on the integrated build gave
**16/16** ([`review-item-nav.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/review-item-nav.txt)),
at 1440 px and at 375 px:

| | Desktop | 375 px |
|---|---|---|
| Document loads: item click / Back / Forward / keyboard (focus + Enter) | **0 / 0 / 0 / 0** | **0 / 0 / 0 / 0** |
| API reads for the move | **3** (`/auth/me`, item, item context); a direct load is 5; 0 extra, 0 repeated, 0 prefetches | the same |
| Titles | `Review campaign — ADG` → `Review item — ADG`, restored on Back and Forward | the same |

The Wave D gate (§18, check 5) also finds that the move does not re-read `/auth/config` or
`/runtime` and repeats no read. The two other row links, "(all theirs)" and the target link, are
still plain anchors, as the brief says (§22).

## 9. Review-name behaviour

The API field is `ScopeView.display_name: string | null`, holding the current display name, else
the account name, else the last known name. It is filled by one batched `principals_by_keys` per
response.

```jsonc
// resolved (live local API, fresh reset + fixtures)
{"kind": "principal", "key": "S-1-5-21-3949361974-1583827165-633756269-2471", "display_name": "sec-share-design-read-only"}
// unresolved (no row in principals)
{"kind": "principal", "key": "S-1-5-21-3949361974-1583827165-633756269-999999", "display_name": null}
// non-identity
{"kind": "share", "key": "gp-fin-fs01|finance", "display_name": null}
```

- **Reviews.** The named row reads `By identity · Identity: Brianna Reddy, Identity:
  sec-share-design-read-only`, with no SID, no fallback and one 63 px line. The unresolved row
  keeps `Identity: S-1-…-999999 (name not loaded)` next to its named neighbour (review-names
  11/11; Wave D check 2; COPY-04 current copy 17/17).
- **Campaign page.** The reviewer table names both identity scopes from the status response's
  `display_name`, with no SID and no fallback (review-names check 7; Wave D check 4).
- **`?principal=` heading.** It may still fall back to the SID when that identity has no items
  in the campaign. This limitation is carried, not addressed (§22).
- **Current name versus frozen name.** This is the deliberate integration decision: **scope
  labels use the identity's current name, while campaign items keep the name recorded at the
  baseline** (`principal_names_at`). A renamed identity can therefore read differently in the two
  places. Resolving baseline names for scopes would need one query per baseline, which is N+1. The
  new DB test
  `TestScopeDisplayNames::test_a_renamed_identity_reads_current_in_its_scope_and_baseline_on_its_items`
  pins both sides: it generates a campaign, ingests a later AD scan renaming the identity, and then
  asserts that the scope reads `CONTOSO\alice.renamed` while every item still reads
  `CONTOSO\alice`.

## 10. Test-stability behaviour

- **The old dependence.** `app/changes/page.tsx` computes its window from `new Date()`. The demo
  changes end on 2026-10-01, so after about 2026-12-30 the 90-day window is empty and the journey
  finds no History link.
- **The mechanism.** With `API_URL` + `PROXY_PORT` set, `titles.mjs` runs a loopback proxy. For
  `/api/v1/changes` and `/summary` it moves the request's own `from`/`to` so the window ends at
  `DATASET_NOW = 2026-10-01T12:00Z` and keeps its length. If no window read reaches the proxy, the
  check fails.
- **Campaign selection.** The journey takes the first listed campaign whose Items cell is
  non-zero, using NAV's selector (§7).
- **Production code is unchanged.**

## 11. API and OpenAPI contract

[`openapi.diff.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/openapi.diff.txt) (66 lines) is
byte-for-byte REVIEW-NAMES-08's diff, since no other lane touched `docs/contracts`:

- **`ScopeView`** gains `display_name` (`string | null`, with a description). `required` stays
  `["kind", "key"]`, and `kind` and `key` are unchanged. Field order has no meaning in JSON.
- **`ScanScopeView`** is a new component with exactly `kind` and `key`. `ScanRunView.declared_scopes`
  and `reconciled_scopes` now `$ref` it. The **separation is preserved**: the scan model never
  gains `display_name`.
- **Unchanged:**
  - `docs/contracts/v1/derived/`;
  - `database/`, so there is no migration;
  - rule ids and versions;
  - `principal:<sid>` graph ids, the `principal=` query parameter and the scope tokens.

The API check
[`contract-safety.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/contract-safety.txt) (11/11 PASS,
script `contract-safety.py`) found:

- every governance scope has exactly `{kind, key, display_name}`;
- kinds ⊂ {`principal`, `share`, `directory_tree`, `server`};
- identity keys are SIDs, and non-identity names are null;
- 50 live scan runs' scopes are exactly `{kind, key}`, so the scan wire format is unchanged.

**Privacy (§41).**

- `display_name` holds no `@` and never equals the identity's UPN.
- It equals the identity's display or account name. For example, Brianna Reddy has a UPN, and the
  scope carries `Brianna Reddy`.
- No other identity metadata is added.

## 12. Authorization

No route, dependency or capability changed.

- **Unit.** `tests/auth/test_roles.py::test_every_governance_reader_may_also_read_identities` and
  `tests/api/test_authorization.py` are in the unit run (§15).
- **DB.** `test_an_auditor_reads_the_name_and_a_viewer_still_reads_nothing` is in the DB run (§16).
- **Live.** The auditor and governance accounts read the same names as admin, and the viewer gets
  **HTTP 403** on the list (contract-safety).

## 13. DB query counts

[`query-counts.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/query-counts.txt), on the integrated
tree:

- **Reviews list.** `GET /campaigns` issues the same number of statements for 1 and for 7
  campaigns (11 identity scopes, one of them unknown). Exactly **1** of them reads `principals`
  (`test_the_list_names_every_row_with_one_lookup`). REVIEW-NAMES measured 4 statements in its
  class run.
- **No identity scope.** **0** `principals` reads (`test_a_page_with_no_identity_scope_makes_no_lookup`).
- **The mutation proof is retained and re-run.** Resolving names per row in the list route, as an
  uncommitted mutation that was then reverted, makes the test fail: `assert 8 == 3`.
- **N+1 = NO.**

## 14. Frontend and API request counts

| Measure | Result |
|---|---|
| Reviews, web-tier API calls | **5**: `/auth/config`, `/runtime`, `/auth/me`, `campaigns?limit`, `queue`. Wave C's set exactly, on both builds side by side (review-names check 6; Wave D check 9) |
| Campaign page | **6** on both builds |
| Per-row or identity requests | **0**: no `/identities` or `/principals` call in the web tier or the browser, over 92 browser requests (Wave D check 9); `fetch` is never called by the CampaignTable (unit) |
| Item move | 3 reads, against 5 for a direct load (§8) |
| All pages, Wave C build vs Wave D build | **0 additional backend requests over 46 measurements**: 23 pages × document/RSC. That is Wave C's 19 pages with this dataset's ids, plus Reviews, the unresolved and resource campaigns, and a `?principal=` filtered campaign ([`request-count.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/request-count.txt), DEEP-TITLES' `request-count.mjs`) |

## 15. Backend validation

Python 3.13.15, from the canonical checkout's venv with `PYTHONPATH` set to this worktree.
`backend/pyproject.toml` is identical to that checkout's.
[`backend-gates.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/backend-gates.txt).

| Gate | Result |
|---|---|
| `ruff check .` | pass (at `7a12b8e`) |
| `ruff format --check .` | pass, 416 files |
| `mypy app tests` | pass, 415 source files |
| `pytest -q -m "not smoke"` | **6164 passed**, 12 skipped. Same as REVIEW-NAMES; NAV and STABILITY changed no backend file |

## 16. DB validation

`ADG_RUN_SMOKE_TESTS=1 pytest -q -m smoke` at `7a12b8e` gave **1306 passed, 1 xfailed, 0 failed**
in 16 min. That is REVIEW-NAMES' 1305 plus the new rename test.

The cluster was a throwaway pgserver PostgreSQL 16.2 with **`timezone=UTC`** and `fsync=off`. The
suite used its `adg` database, from which the tests create `adg_test`, and never touched
`adg_demo`. Because the cluster runs in UTC, the known Mac-zone artifact (Wave C §6) could not
occur.

## 17. Frontend validation

Node 22.23.3 arm64, Next 16.4.1, at `abf2d00`. The `frontend/` tree is identical at every later
commit. [`frontend-gates.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/frontend-gates.txt).

| Gate | Result |
|---|---|
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass |
| `npm run test` | **1696 passed**, 49 files. That is Wave C's 1691, plus 2 from NAV and 3 from NAMES; no overlapping test was lost |
| `ADG_NEXT_OUTPUT=standalone npm run build` | pass |

## 18. Browser validation

Every gate ran against the real local stack, as listed in §25. Each `.txt` log records the load
average before and after; the load ranged from 7 to 71, with other sessions sharing the Mac.

| Gate | Script | Result |
|---|---|---|
| Review navigation | `ADG-UX-NAV-07-evidence/review-item-nav.mjs` | **16/16** (§8) |
| Review names | `ADG-UX-REVIEW-NAMES-08-evidence/review-names.mjs` (lane + Wave C build) | **11/11** |
| Titles, current | `frontend/cloudflare/checks/titles.mjs` (anchored) | **15/15**: "21 moves, no reload; 24 window reads anchored to 2026-10-01T12:00:00.000Z"; Back ×21 and Forward ×21 with no reload; 18 direct loads |
| Titles, proxy bypassed | the same, with Next.js pointing straight at the API | 14/15, **fails closed**: "no window read reached the proxy on :8210" |
| Wave C combined | `ADG-UX-WAVE-C-INTEGRATION-01-evidence/wave-c-check.mjs` | **9/9** |
| Wave B, current copy | `…WAVE-C…-evidence/wave-b-check.wave-c.mjs` | **8/8** |
| Overview | `frontend/cloudflare/checks/overview.mjs` | **49/49** |
| COPY-04, historical | `ADG-UX-BACKEND-COPY-04-evidence/copy-check.mjs` | 16/17. Its one designed failure is the old `Identity: <SID> (name not loaded)` Reviews assertion |
| COPY-04, **current** | `ADG-UX-WAVE-D-INTEGRATION-01-evidence/copy-check.wave-d.mjs` | **17/17** |
| **Wave D combined (new)** | `ADG-UX-WAVE-D-INTEGRATION-01-evidence/wave-d-check.mjs` | **15/15**; on the Wave C build it gives 4/15 (control) |
| Request count | DEEP-TITLES `request-count.mjs` | **0** additional over 46 |
| Contract/privacy (API) | `contract-safety.py` | 11/11 |

**The Wave D combined gate**
([`wave-d-check.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/wave-d-check.txt)) walks one
signed-in tab. It proves the three lanes coexist and does not repeat each source check.

1. Reviews loads.
2. Identity scopes show the current display name. The unresolved identity keeps the fallback, and
   no SID appears beside a name.
3. A click opens the named campaign: client-side, titled "Review campaign — ADG".
4. The reviewers use the current scope names.
5. A click opens the item. There is no document load, the title is "Review item — ADG", and there
   are 3 reads with no layout re-read and no repeat.
6. Back is client-side, and the campaign title is restored.
7. Forward is client-side, and the item title is restored.
8. The Changes journey reaches History and "Why access changed" on a **2028-01-01** clock. Each
   window read is first moved to that date, then anchored to DATASET_NOW (4 reads).
9. Reviews and the campaign make Wave C's 5 and 6 calls. No identity lookup happens in the web
   tier or the browser.
10. At 1440, 1024, 768, 375 and 320 px (checks 10–14), Reviews → campaign → item stays
    client-side, and no page scrolls sideways.
11. There is no page error and no hydration error (check 15). The one console error is the known
    `/favicon.ico` 404.

**Control.** On the Wave C build, the same gate fails check 2 (no names) and check 5 (the item
loads as a document). The checks after 5 then fail too, either on that document load or because
check 5's read record never exists.

**The current gates.** Future work should run these, together with the source gates above:

- `wave-d-check.mjs`;
- `copy-check.wave-d.mjs`;
- `frontend/cloudflare/checks/titles.mjs` (anchored);
- `wave-c-check.mjs` and `wave-b-check.wave-c.mjs`, which still pass unchanged and stay current.

**The historical gates.** Each carries a header saying it is historical, with its checks
unchanged. None is run by CI or by any npm or shell script.

- `ADG-UX-WAVE-B-INTEGRATION-01-evidence/wave-b-check.mjs` (6/8 by design);
- `ADG-UX-BACKEND-COPY-04-evidence/copy-check.mjs` (16/17 by design).

**Not rerun.** The Recently Viewed gate (25/25 at Wave C) and the Wave A integration check
(14/14) were not rerun. No file they cover changed: RecentlyViewed, AppShell, PrimaryNav and the
Overview are byte-identical. Wave C gate check 1 still shows Recently Viewed in the first HTML,
before "Where to start".

## 19. Deterministic-date proof

The proof uses web :3113 → TEST-STABILITY's `simulate-clock.mjs` :8311 → the titles proxy :8210
→ the API. Only the Changes window moves; the session and the API keep the real clock.

| Simulated date | 7-day read the page sent becomes | Anchor | Result |
|---|---|---|---|
| 2026-10-30 | 2026-10-22T23:59:59Z .. 2026-10-29T23:59:59Z | on | **15/15**, 24 anchored |
| 2027-06-01 | 2027-05-24T23:59:59Z .. 2027-05-31T23:59:59Z | on | **15/15**, 24 anchored |
| 2028-01-01 | 2027-12-24T23:59:59Z .. 2027-12-31T23:59:59Z | on | **15/15**, 24 anchored |
| 2028-01-01 | as above | **off** (no `API_URL`) | **13/15**: `no link for "History" (a[href^="/changes?kind="]) on …/changes?days=90`. This is the old failure, reproduced |

Logs: `titles-future-<date>.txt`/`.sim.txt` and `titles-control-2028-01-01.txt`/`.sim.txt`. The
production clock was not touched.

## 20. Responsive validation

At 1440, 1024, 768, 375 and 320 px, Reviews, the campaign detail and the review item do not
scroll sideways, and client navigation between them still holds (Wave D checks 10–14). The source
gates show the same:

- NAV at 1440 and 375 px: no sideways scroll; the heading in view at 153 and 714 px.
- NAMES at 375 and 320 px for Reviews and the campaign. The table keeps its existing internal
  scroll at 320 px, and the named row is one 63 px line.
- The Wave C gate at five widths, for the Overview, identity, server and explanation.

Long names wrap inside the established `.muted` scope line. A named identity's SID is no longer in
the row, so it cannot set the width. Screens are in
[`screens/`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/screens/) (`reviews-{1440,375,320}`,
`reviews-baseline-1440`, `campaign-1440`).

## 21. Hosted-streaming inheritance

[`hosting-tree.txt`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/hosting-tree.txt) compares
`559dbaf` with the integrated tree. Byte-identical:

- the Worker (`frontend/cloudflare/worker.ts`);
- `frontend/render/` including `gate.ts`;
- `next.config.mjs`, `wrangler.jsonc` and `docker/Dockerfile.render`;
- `lib/config.ts`, `lib/api` and `app/api`;
- `app/layout.tsx`, `app/page.tsx` (the only `<Suspense>` user), `Overview.tsx`,
  `overview.module.css` and `RecentlyViewed.tsx`;
- `package.json`, `package-lock.json`, `pyproject.toml`, `database/` and `.github/`.

The only change under `frontend/cloudflare/` is the test gate `checks/titles.mjs`. The Wave C
gate's Overview check (§18) shows the same stream order, with Reviews arriving after its delayed
read, and the same 9 Overview API calls.

**FULL HOSTED STREAMING REVALIDATION NOT REQUIRED; RUN SMALL POST-DEPLOY REGRESSION AFTER WAVE D
STAGING DEPLOY.**

After Wave D is exported and deployed to staging, that smaller regression should cover:

- several warm byte-level public runs;
- a desktop browser proof and a 375 px browser proof;
- a finished section visible while another placeholder remains;
- no hydration or page errors.

**Release context.**

- Staging runs Wave C, released from `559dbaf` by HOSTED-STREAMING-11 (adg-demo `4e565da`).
  Staging does **not** have Wave D, and no Wave D behaviour was tested against it.
- **Release prerequisite:** the synthetic-enterprise release-spec branch
  `release/ADG-HOSTED-STREAMING-11` is still at `79ec257` and is not merged into SE `main`. This
  lane did not touch it.

## 22. Limitations

**Navigation**
1. The row's "(all theirs)" (`?principal=`) and target (`?target=`) filter links are still plain
   `<a>`, so they reload the campaign.
2. Focus after the campaign → item move lands on `BODY`, as the old full load did.

**Review names**
3. The `?principal=` items heading falls back to the SID when that identity has no item in the
   campaign.
4. Scope labels use the current name, while frozen item evidence keeps the baseline name. This is
   intentional, and it is now pinned by a test (§9).

**Test stability**
5. A hosted titles run stays on the live window. The hosted Next.js server's reads cannot be
   proxied, and the hosted dataset ends 2026-10-01 too.
6. The journey still needs a campaign with items and a stored What-if proposal. A fresh reset has
   neither.
   - `setup-fixtures.sh`, added here, creates both on a fresh local reset. It is test-owned, works
     through the public API on loopback only, and has a deterministic shape: the same scenario
     subjects and SIDs as the source lanes.
   - All gates in this report ran on such a reset.
   - The gates do not create the fixtures themselves. They stay read-only, apart from the Overview
     gate's documented campaign seed.
7. The test shifts the Changes API window, not the whole application clock. The compare page's
   default points are not anchored.

**Hosted and release**
8. Wave D is not deployed. HOSTED-STREAMING-11 verified Wave C, not Wave D. The small post-deploy
   regression in §21 is still to run.
9. The synthetic-enterprise release spec (`79ec257`) is unmerged.

**Carried from Wave C and still true** (unchanged by Wave D)
10. Stored simulation reports keep their old wording.
11. Some missing-object routes answer HTTP 200, and a malformed identity escape answers 500.
12. Access-explanation SVG tooltips still carry raw SIDs and UNC paths.
13. Recently Viewed is per tab:
    - a tab opened from another, or duplicated, starts with a copy;
    - restored tabs can keep it, up to the 8 h expiry;
    - a deleted object's label stays until its link is followed.

**Resolved since Wave C:**

- Wave C §15.1, the Reviews list showing no names: fixed by NAMES.
- The reviewer-scope half of §15.2: fixed by NAMES. The items heading is limitation 3 above.
- §15.4, the item `<a>`: fixed by NAV.
- §15.6, the date dependence: fixed locally by STABILITY; hosted is limitation 5.
- The hosted-streaming gate for Wave C was closed by HOSTED-STREAMING-11. It is not a Wave C defect.

## 23. New authoritative baseline

**`origin/integration/ADG-UX-WAVE-D-01` at its head**: the docs-only commit that adds §24, directly
on top of `fc7c7a2`. Its code is identical to `fc7c7a2`, which CI run 41223498140 validated.

FUTURE ADG UX WORK MUST BRANCH FROM THIS VERIFIED SHA UNLESS SUPERSEDED. Wave C (`559dbaf`) is
now historical baseline context.

## 24. CI

The branch was pushed once, with the merges, the test commits and this report. Run
[41223498140](https://github.com/40ozs/adg/actions/runs/41223498140) on
`fc7c7a29f7254ae657e37c287ffb4f75f1ef4be5` (code + gates + report head) passed:

| Job | Result |
|---|---|
| Backend (lint, types, tests) | **success**: ruff, format, mypy; unit **6052 passed**, 124 skipped, 1307 deselected; Database tests **1306 passed**, 1 xfailed |
| Frontend (lint, types, tests, build) | **success**: 49 files, **1696** passed; lint, typecheck, cf:typecheck, build |
| Collector (Pester) | success |
| Docker Compose configuration | success |
| Fork PR Linux gates | skipped (push event) |

The DB and vitest counts match the local runs exactly. CI's unit count (6052 passed, 124 skipped)
differs from the local one (6164, 12) only in platform-dependent skips, the same split as Wave C
(6048/124 against 6160/12) plus REVIEW-NAMES' 4 tests.

This section and §23 were added by a docs-only commit directly on top of `fc7c7a2`. That commit
changes only this report, so the verdict rests on run 41223498140.

## 25. Environment

The Mac had rebooted before this lane and wiped every earlier lane's scratchpad stack. The local
stack was therefore rebuilt from nothing.

| | |
|---|---|
| Database | pgserver PostgreSQL 16.2 on 127.0.0.1:55527, `timezone=UTC`, data on the internal disk (outside the repo). `adg_demo` was freshly reset with `python -m app.demo_mode reset` in 81 s (Genesee Press `genesee-press-v1`, 300 runs, fingerprint `4667d3cf…`, the canonical value in `docs/demo/genesee-press.md`), then given [`setup-fixtures.sh`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/setup-fixtures.sh) ([`fixtures.json`](ADG-UX-WAVE-D-INTEGRATION-01-evidence/fixtures.json)). The Overview gate later seeded its own documented campaign. `adg` → `adg_test` holds the DB suite |
| API | `python -m app --port 8110` from this worktree: demo mode, development auth, a throwaway secret, and the default dev users plus `nobody:remediator` for the Overview gate |
| Web | This branch's standalone build on :3110 (→ the gates' proxy :8210), :3111 (direct) and :3113 (→ simulate-clock :8311). Wave C's standalone build of `559dbaf` on :3112 (→ :8212) for the old-client, request-count and control runs |
| Browser | Google Chrome (/Applications), Playwright 1.62.1 via `NODE_PATH`, Node 22.23.3 arm64 |

No database, browser profile, credential, cookie, token or `node_modules` is committed. The
evidence holds synthetic Genesee Press values only: SIDs, names, campaign and item UUIDs, and
loopback URLs.

## Status

`NOT MERGED TO MAIN` · `NOT DEPLOYED`
