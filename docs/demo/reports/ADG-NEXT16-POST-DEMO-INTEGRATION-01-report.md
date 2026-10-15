# ADG-NEXT16-POST-DEMO-INTEGRATION-01 — Next.js 16 / React #418 fix on the public-demo `main`

## 1. Verdict

**`READY_FOR_ADG_NEXT16_POST_DEMO_MERGE_WITH_LIMITATIONS`**

The Next.js 16 upgrade that removes the intermittent React #418 hydration error is integrated
onto the public-demo `main` (`744c2ef`) as two frontend-only commits:

* `af94e2e` — the accepted framework change (`66ca201` from `fix/ADG-NEXT16-REACT418-01`),
  cherry-picked with its author and message, keeping only the framework change;
* `501c136` — Next.js 16.4.1, the October 14 security release that the accepted Next 16
  report required before `main`.

The integration changes four files under `frontend/`: `package.json`, `package-lock.json`,
`eslint.config.mjs` and `tsconfig.json`. No application code, test, backend file, migration,
generated contract, README or existing document changed; the report commit only adds this
report and its evidence under `docs/demo/reports/`. Every ADG-DEMO-01, -02 and -03 behavior
and the DEMO-PUBLIC-01 reviewer experience were re-proved on the integrated tree (§6).

**React #418.** The same harness ran against both builds. A positive control showed it detects
#418 on each: 62 of 62 full loads on Next 15 and on Next 16. On `main` (Next 15.5.25) it then
reproduced the public demo's #418 on its own: **67 of 576 stream-delayed full loads (11.6%)**,
on 9 reviewer pages, all but one at `readyState: loading`, as ADG-UI-EDGE-01 described. The
integrated build (Next 16.4.1, built from a clean clone) had **0 of 1,344 full loads** (0 of
960 stream-delayed, 0 of 384 direct) and 0 of 266 client-side transitions. There were **zero
application console errors** in 11 recorded gate runs (apart from the #418s the two controls
induce on purpose) and in 15 walkthrough runs. At the
baseline rate, 960 clean stream-delayed loads would occur by chance with probability about
10⁻⁵². This is a repeated gate with a reproducing baseline, not one clean run (§7).

The limitations (§14) concern the environment and deployment scope, not defects:

* the hosted timing was recreated on one machine, not measured on the Worker/Render path;
* only Chromium was run;
* this line has no hosted deployment, and the existing staging runs a different tree;
* ADG-UI-UPDATE-01, ADG-UI-COMPACT-IDENTITY-01 and the deploy line were deliberately left out
  (§3.4).

---

## 2. Base

| | |
| --- | --- |
| Repository | `40ozs/adg` (public; visibility not changed) |
| Starting `main` | `744c2efe89fb17f49cdfe640ebe5afd7d18253af`. Fetched fresh at the start; `origin/main` had not advanced. CI run [77](https://github.com/40ozs/adg/actions/runs/38751393080) passed every job on it, on `main` |
| Report-merge state | `ADG-DEMO-PUBLIC-MERGE-01` fast-forwarded `main` to `744c2ef` and wrote its report on `claude/intelligent-fermi-nxp2jw` (`32da1ae`), which is **not on `main`**. That branch is `main` plus one report file and no code, so it does not change the base. No later report merge to `main` exists, so per this prompt the current verified `main` is the base |
| Candidate | `fix/ADG-NEXT16-REACT418-01` @ `04663bcdcf190c480fcefb88b5a9550636d28d94` (unchanged since its report); the framework commit within it, `66ca20151e9bacfb96ad3cbff689d2b5cdcfa417`, is what was integrated (§3) |
| Integration branch | `claude/beautiful-knuth-3zruji` (this session's only permitted push target). Intended name: `integration/ADG-NEXT16-POST-DEMO-01`. Renaming is a ref operation: `git push origin claude/beautiful-knuth-3zruji:integration/ADG-NEXT16-POST-DEMO-01` |
| Code head | `501c1367e575efb51fedfe2353187024359acd52` |
| Ending SHA | the commit adding this report and its evidence (stated in the final response; a report cannot name its own commit) |
| `main` | unchanged; nothing was merged to it |

Commits on the integration branch, in order:

| SHA | Author | What |
| --- | --- | --- |
| `af94e2e` | 40ozs (the original author of `66ca201`) | `frontend: Next.js 16.4.0 and React 19.3, webpack build` — the accepted change, cherry-picked with `-x`, adapted to `main` (§4) |
| `501c136` | Claude | `frontend: Next.js 16.4.1, the October 14 security release` (§5.2) |
| report | Claude | this report and [`ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/) |

No merge commit, squash or rebase. No history was rewritten. The original Next 16 branches,
the deploy branches and every other remote head are untouched.

---

## 3. Candidate provenance

### 3.1 What exists

`git fetch --prune` of every head, then ancestry, reports and CI, all from the repository:

| Head | SHA | Contains | Report | CI |
| --- | --- | --- | --- | --- |
| `fix/ADG-NEXT16-REACT418-01` | `04663bc` | the Next 16 framework commit `66ca201`, its two handoff commits, and everything below it | `docs/handoffs/adg-next16-react418-01.md` (on the branch): verdict `UPSTREAM_REACT_418_RESOLVED` → `READY_FOR_ADG_NEXT16_UI_INTEGRATION` | run [27](https://github.com/40ozs/adg/actions/runs/38324838064) on `66ca201`, run [32](https://github.com/40ozs/adg/actions/runs/38334295165) on `04663bc`: success |
| `integration/ADG-NEXT16-UI-02` | `3e26e4a` | **all of** `fix/ADG-NEXT16-REACT418-01`, plus `fix/ADG-UI-COMPACT-IDENTITY-01` and a SID-truncation commit | `docs/handoffs/adg-next16-ui-integration-02.md`: verdict `READY_FOR_ADG_COMBINED_STAGING_REVIEW` | run [34](https://github.com/40ozs/adg/actions/runs/38338783955) on `3e26e4a`: success |
| `deploy/ADG-FREE-STAGING-01` | `3e26e4a` | the same commit as `integration/ADG-NEXT16-UI-02` (fast-forwarded to it, per that handoff §8) | — | run [35](https://github.com/40ozs/adg/actions/runs/38338789741): success |

* `fix/ADG-NEXT16-REACT418-01` is an ancestor of `integration/ADG-NEXT16-UI-02`; the reverse
  is not true. Neither head has advanced since its report: each report's final commit is its
  branch head.
* The merge-base of both with `main` is `1ab01a2` (PR #2). They are 19 and 26 commits ahead
  of `main`, and `main` is 24 ahead of them.

### 3.2 What the Next 16 branch is made of

`fix/ADG-NEXT16-REACT418-01` was built for staging, on the staging deploy line. Its 19
commits past `1ab01a2` are:

| Commits | What | Belongs to |
| --- | --- | --- |
| `cedca60`, `9b64b6a`, `dc6e310`, `13719fb`, `14ed8fa`, `643da21`, `5617e1e`, `df6556f`, `57bbbaa`, `6b69fe3` | Cloudflare Worker, Render origin gate, staging workflow, `Dockerfile.render`, `backend/app/origin_gate.py`, deployment docs | ADG-CLOUDFLARE-DEMO-01, ADG-FREE-STAGING/EDGE-SSR deploy work |
| `61fedb8`, `552bcb8`, `371b90e`, `861a11b`, `a5cdf88`, merged at `afa7804` | Users/Groups directories, shortest-chain API, `SortableTable` everywhere, the principal page split into `principal/[key]/…`; **backend API and OpenAPI changes** | ADG-UI-UPDATE-01 |
| **`66ca201`** | **Next.js 16.4.0 and React 19.3, webpack build** | **ADG-NEXT16-REACT418-01** |
| `f5005b1`, `04663bc` | the handoff | ADG-NEXT16-REACT418-01 |

`integration/ADG-NEXT16-UI-02` adds ADG-UI-COMPACT-IDENTITY-01 (`4f33e08`, `98a9a79`,
`0a66322`, `f50e8f2`, merged at `9502b4b`) and `33cca01` (SID truncation).

### 3.3 The accepted Next 16 contract

From `adg-next16-react418-01.md` §1 and the commit itself, not from memory. The upgrade is
"only what the upgrade requires":

| File | Change |
| --- | --- |
| `frontend/package.json`, lockfile | `next`, `eslint-config-next` 15.5.27 → 16.4.0; `react`, `react-dom`, `@types/react(-dom)` ranges `^19.1.0` → `^19.3.0` |
| `frontend/package.json` | `build`: `next build --webpack` (Next 16 defaults to Turbopack; no Turbopack migration) |
| `frontend/eslint.config.mjs` | `eslint-config-next` 16's flat configs imported directly; `@eslint/eslintrc` (`FlatCompat`) removed |
| `frontend/tsconfig.json` | `jsx: react-jsx`; `.next/dev/types/**/*.ts` included |

"No application code, no Suspense boundary, no backend change." The fix is the React that
Next 16.4.0 bundles (`19.3.0-canary-278794d7-20261002`), whose hydration replay restores the
cursor for a host component that suspended on a streamed RSC row — the #418 cause
established in `docs/handoffs/adg-ui-edge-01.md` §5 (on `integration/ADG-UI-EDGE-01`).

The report's open items (§6 there): the **October 14 security release** "as a follow-up
patch, then rerun §3, before this is considered for `main`"; the deployment branch; Turbopack
not used; the instrumented harnesses not committed.

### 3.4 What was accepted, and why

**Accepted: `66ca201`, the framework commit, followed by the security release its own report
requires.** Not either branch head.

* Merging either head would bring the 10 deploy commits (Worker, Render gate, staging
  workflow, a backend origin gate: 4 backend files) and the ADG-UI-UPDATE-01 /
  COMPACT-IDENTITY feature waves (12 backend files, regenerated OpenAPI and derived explanation
  schemas, a principal-page restructure, sortable tables across 18 files) into `main`. This prompt rules both out: no unrelated deploy
  work, backend ideally tree-identical, no broadening beyond the accepted Next 16 work, and no
  redesign. Those waves were accepted for **staging review** (`READY_FOR_ADG_COMBINED_STAGING_REVIEW`),
  not for `main`, and have their own integration to go through.
* `66ca201` is self-contained: it touches four files, none of which `main` changed since the
  merge-base, and none of its predecessors' application changes are needed for it (the Next 16
  branch's handoff: "no application code").
* `integration/ADG-NEXT16-UI-02` contains `fix/ADG-NEXT16-REACT418-01`, so they were not
  integrated independently; the framework commit is common to both and is integrated once.

The DEMO-PUBLIC-MERGE-01 report suggested integrating `integration/ADG-NEXT16-UI-02` and
resolving `principal/page.tsx` "by taking the newer structure". That structure is
ADG-UI-UPDATE-01's, not the framework migration's, so it is not taken here (§4.2).

---

## 4. Conflict map

### 4.1 Before modifying the branch

Write-free trial merges (`git merge-tree --write-tree`) of each head into `main`, and the
files both sides changed since the merge-base
([`candidate-overlap.txt`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/candidate-overlap.txt)):

| Merge into `main` | Textual conflicts | Files both sides changed |
| --- | --- | --- |
| `fix/ADG-NEXT16-REACT418-01` (105 files) | `frontend/app/identities/principal/page.tsx` | 16: `README.md`; `backend/app/api/access.py`, `backend/app/services/access.py`, `backend/tests/api/test_authorization.py`; `docs/contracts/v1/openapi.json`, `derived/access-explanation.schema.json`, `derived/resource-impact.schema.json`; `frontend/app/identities/principal/page.tsx`, `components/Explanation.tsx`, `components/Identity.tsx`, `lib/api/adg.ts`, `lib/contracts.ts`, `lib/identity.ts`; `tests/contracts.test.ts`, `tests/demo-guide.test.tsx`, `tests/risks-page.test.tsx` |
| `integration/ADG-NEXT16-UI-02` (109 files) | the same one file | the same 16 |

Of the UI-02 head's 109 files: 24 deploy/staging, **4 framework**, 6 handoff documents and
75 ADG-UI-UPDATE-01 / COMPACT-IDENTITY. **Every one of the 16 overlaps is in the
UI-UPDATE-01 or deploy part. None is a framework file.** No candidate file is a migration.

### 4.2 Actual integration

Cherry-picking `66ca201` onto `744c2ef` conflicted in all four of its files, each only on
deploy-line context the commit's parent had and `main` never had:

| File | Conflict | Resolution |
| --- | --- | --- |
| `frontend/eslint.config.mjs` | the parent's ignore list adds `.edge-assets/**`, `.wrangler/**` | the commit's change (flat `eslint-config-next/core-web-vitals` and `/typescript`, no `FlatCompat`) on `main`'s ignore list |
| `frontend/package.json` | the parent has `@cloudflare/workers-types` and `cf:*` scripts | the commit's changes only: `next`/`eslint-config-next` `^16.4.0`, `react`/`react-dom`/`@types/react(-dom)` `^19.3.0`, `build: next build --webpack`, `@eslint/eslintrc` removed |
| `frontend/tsconfig.json` | the parent excludes `cloudflare/worker.ts`, `.edge-assets`, `.wrangler` | `jsx: react-jsx` and `.next/dev/types/**/*.ts` on `main`'s `exclude: ["node_modules"]` |
| `frontend/package-lock.json` | the parent's lockfile carries the Worker tooling | not chosen from either side: `main`'s lockfile, then `npm install` against the resolved `package.json`, pinned to `next@16.4.0` |

The resulting diff against `main` equals `66ca201`'s own diff line for line, minus the
deploy context. **Every package the new lockfile resolves is in `66ca201`'s lockfile at the
same version** (`next`, `@next/env`, `@next/eslint-plugin-next`, `eslint-config-next` 16.4.0;
`react`, `react-dom`, `@types/react(-dom)` 19.3.0). The original's 57 extra entries are
`wrangler`, `miniflare`, `workerd` and their dependencies.

**Overlap files named in this prompt:**

| File | State |
| --- | --- |
| `frontend/app/identities/principal/page.tsx` | **unchanged from `main`.** It conflicts only with ADG-UI-UPDATE-01's restructure, which is not integrated; the framework migration needs no page change. Part 5's resolution therefore did not arise. The unresolved-identity banner and investigation navigation are `main`'s, byte for byte, and were re-tested (§6) |
| investigation, risks and comparison UI; the demo guide; `tests/demo-guide.test.tsx` | unchanged from `main` |
| generated OpenAPI files, derived schemas | unchanged; regenerated from source with zero drift (§8) |
| package manifests, lockfile, ESLint and TypeScript config | the framework change (above) |
| Next configuration (`next.config.mjs`) | unchanged: the candidate's edits to it came from deploy commits `5617e1e`/`df6556f` (standalone output for Render) |
| Worker / deployment configuration | none on `main`; none imported |
| migrations | none in the candidate; none changed |

`git diff --stat 744c2ef 501c136` lists exactly the four frontend files. `frontend/app`,
`components`, `lib`, `tests`, `backend/`, `database/`, `docs/` and `README.md` are
byte-identical to `main`.

---

## 5. Framework state

### 5.1 Configuration

| | `main` (`744c2ef`) | Integrated (`501c136`) |
| --- | --- | --- |
| `next` (lockfile) | 15.5.25 (`^15.5.4`) | **16.4.1** (`^16.4.1`) |
| React the App Router runs (bundled in `next`) | `19.2.0-canary-0bdb9206-20250818` | **`19.3.0-canary-278794d7-20261002`** |
| npm `react`, `react-dom`, `@types/react(-dom)` | 19.3.0 (ranges `^19.1.0`) | 19.3.0 (ranges `^19.3.0`) |
| `eslint-config-next` | 15.5.25 via `FlatCompat` | 16.4.1, flat configs imported directly; `@eslint/eslintrc` no longer a direct dependency |
| Build | `next build` (webpack) | `next build --webpack`; the build prints `▲ Next.js 16.4.1 (webpack)`. **Turbopack is not used** |
| TypeScript | `jsx: preserve` | `jsx: react-jsx`; `.next/dev/types/**/*.ts` included |
| `next.config.mjs` | `reactStrictMode: true` | unchanged |
| Node | CI: 22; this container: 22.22.0 | unchanged; `next` 16.4.1 requires `>=20.9.0` |

The shipped client chunks carry the React build named above: the integrated
`.next/static/chunks/4bd1b696-*.js` contains `19.3.0-canary-278794d7-20261002`, `main`'s
contains `19.2.0-canary-0bdb9206-20250818`.

**Application compatibility with Next 16, checked rather than assumed:** no `middleware`
(or `proxy`) file; no `loading`, `error`, `not-found` or `default` files and no parallel
routes; no `Suspense` in application code; all 22 pages and routes that take `params` or
`searchParams` already type them as `Promise`s; no `cookies()`/`headers()` without `await`;
none of the APIs Next 16 removed (`next/legacy/image`, AMP, `serverRuntimeConfig` /
`publicRuntimeConfig`), and no `experimental` flags in `next.config.mjs`. Server/client boundaries are unchanged (12 `"use client"` modules, 1 `"use server"`).
Lint, typecheck, the full test suite and the build pass without one application change.

**No Suspense or loading boundary was added**, as in the accepted candidate: the remediation
is the bundled React, not a boundary that would hide a mismatch. The configuration does not
fall back to anything that reintroduces the original React (no `next` 15, no override of the
bundled React).

### 5.2 The October 14 security release (`501c136`)

The accepted report made it a precondition for `main`. Evidence from the published tarballs
(`npm pack next@16.4.0 next@16.4.1`, compared file by file):

* **`dist/compiled/react` and `dist/compiled/react-dom` are byte-identical** between 16.4.0
  and 16.4.1 (both `19.3.0-canary-278794d7-20261002`). The code that removes #418 is the same.
* The changes are in the RSC (Flight) transport — `react-server-dom-webpack`/`-turbopack`
  server and client now refuse to transport a detached `ArrayBuffer` — and in `next-server`.
* The lockfile change is 12 entries 16.4.0 → 16.4.1 (`next`, `@next/env`, the eight
  `@next/swc-*` binaries, `eslint-config-next`, `@next/eslint-plugin-next`); nothing else.
* `npm audit` against each lockfile: `main` 16 findings (2 critical, 10 high, 4 moderate),
  including two `next` advisories (GHSA-4jqv-mc3x-m676, GHSA-mcj8-r9mp-w47p, cache poisoning,
  fixed in 15.5.27). Integrated: **13** (2 critical, 8 high, 3 moderate). The `next`,
  `postcss` and `sharp` findings are gone. The 13 that remain were all on `main`'s list and
  are test and lint tooling, not changed here:
  * `vitest`, `tinypool`, `vite`, `vite-node`, `@vitest/mocker`, `esbuild`;
  * the `eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch` →
    `braces`/`brace-expansion` chain;
  * `source-map-js`.

  The advisory database lists nothing against 16.4.0 itself; "security release" is the
  accepted report's description of 16.4.1.

All gates in this report ran on `501c136` (16.4.1). `af94e2e` (16.4.0) also passed lint,
typecheck, the full test suite (1,361) and the build before `501c136` was made.

---

## 6. Public-demo preservation

Every surface below is `main`'s code, unchanged; the question is whether it still behaves
the same under Next 16. Each was re-proved on the integrated tree: unit and component tests
(§10.3), the PostgreSQL classes (§10.2), CI's full suites (§13), and the browser, against the
**clean-clone production build** (§11–§12).

### 6.1 ADG-DEMO-01: SYSTEM correctness — **intact**

| Behavior | Proved on the integrated tree by |
| --- | --- |
| SYSTEM mapping states `confirmed`, `ambiguous`, `missing`, `conflicting` | backend `test_system_mapping.py`, `test_system_explanation_regression.py` (in the 229 representative unit tests) and the full unit suite; `test_system_mapping_api` and `TestTheSystemExplanation` (PostgreSQL, in the 58); frontend `system-mapping.test.tsx` (23) |
| No unsupported definite negative | browser, SYSTEM over `\\NM-FS-01\Shared`: mapping `ambiguous`, certainty `uncertain`, "Not a finding of no access", no "No access" verdict (DEMO-01 UI check, 15/15 ×2); the DEMO-02 walkthrough's "SYSTEM over a share: not determined, ambiguous, never 'No access'" (×3); [`next16-system-over-share.png`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/next16-system-over-share.png) |
| Lower-bound certainty | SYSTEM locally: mapping `confirmed`, certainty `at_least`, access path `local` (browser) |
| No fabricated identity; unresolved identity rendering | browser, the unresolved SID `S-1-5-21-…-1108`: the `role=alert` banner "A run saw this SID and could not resolve it", naming the key and the reason (`deleted`) and saying nothing on the page names it as anything else. The heading shows the last known name marked `unresolved`, and account, UPN and DN read "not reported" ([screenshot](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/next16-unresolved-principal.png)). The DEMO-02 walkthrough's "the SID's page shows it unresolved (ADG-DEMO-01 banner)" (×3). `identity.test.ts` (17), `views.test.tsx` (31), `pages.test.tsx` (16) |

### 6.2 ADG-DEMO-02: access investigation — **intact**

*Why does Fatima Gordon have access to `\\NM-ENG-FS01\Engineering\Projects\Apollo`?*

| Behavior | Proved by |
| --- | --- |
| Outcome: granted, **`Modify`**, **`at_least`** | ADG-DEMO-02 walkthrough "observed: access present, Modify, at_least" (29/29 ×3); ADG-DEMO-03 walkthrough step 4 "Modify, at_least" (×5); DEMO-PUBLIC-01 "W1.2: granted (Access present), Modify, at_least, over the network" (×5); `TestTheAccessInvestigation` (PostgreSQL) |
| Direct NTFS path | "NTFS route: direct, explicit, removal changes nothing"; "W1.3: NTFS route is a direct entry set explicitly on the folder" |
| SMB group path | "share route: through ENG-Mobile"; "W1.3: share route runs through ENG-Mobile to share entry #3" |
| Provenance | "Tab reaches the first hop's evidence disclosure", "the evidence names the collector and scans"; "W1.4: hop evidence names the collector and the scans"; the gate's keyboard phase (hop evidence reached by Tab and opened by Enter in all 14 integration rounds) |
| Incomplete evidence | "two missing-evidence items, neither affecting the conclusion", "the orphaned SID stays visibly unresolved", "Proj-Zephyr-RW is named"; "W1.5: lhansen and Proj-Zephyr-RW are missing evidence that does not affect the conclusion" |
| Demo Guide flow | "demo guide offers the guided investigation", "the walkthrough has eight steps"; the gate's navigation phase (guide → investigation → Back, all 14 integration rounds); `demo-guide.test.tsx` (18), `investigation-page.test.tsx` (30), `investigation.test.ts` (20) |

### 6.3 ADG-DEMO-03: coverage and comparison recovery — **intact**

| Behavior | Proved by |
| --- | --- |
| Collection coverage beside the totals | "2. coverage line beside the totals" (98 of 100 scopes …), "2. verdict in a word and a symbol", "2. names the snapshot" (×5); `test_collection_coverage.py` (PostgreSQL); `coverage.test.ts` (29), `risks-page.test.tsx` (21) |
| Failed/unavailable ≠ checked-and-empty | "1. the unavailable server's findings are held, not zeroed" (189 on NM-FS-03), "3. the unavailable source is named, with the age of its evidence" |
| Risk completeness interpretation | "1. the totals are not called totals while a source is unavailable", "2. says the totals are not exhaustive"; "W2.2", "W2.6" |
| Investigation coverage section | "4. the answer reads only complete scopes, and says the rest cannot change it", "4. the four scopes the answer reads, each collected" |
| Typed outcomes, timeout | "6. timed out, stated in words and a symbol", "6. the state is announced (role=status, aria-live)"; `compare-page.test.tsx` (18), `test_comparison_bounds.py` (PostgreSQL) |
| No partial-as-complete | "6. no partial list is presented, and nothing says 'nothing is different'", "8. its list is labelled partial", "8. the retry stops as incomplete, at a named boundary" |
| Narrowed recovery, complete and recovered | "10. the final comparison is marked complete and recovered", "10. the complete comparison presents its differences" (221); `TestTheCoverageAndRecoveryDemo` (PostgreSQL); [screenshot](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/next16-comparison-recovered.png) |
| Comparison telemetry | while the integrated build was under test (from 16:27:49Z), the API wrote **374 `changes.compare` lines**: 133 `timed_out`, 123 `incomplete`, 118 `complete`, 30 of them `recovered`. Each line carries `comparison_id`, `retry_of`, `outcome`, `recovered`, `stopped_in`, `window_ms`, `elapsed_ms`, `objects_read_from/_to`, `differences_found` and `recovery_offered`. The API log has no `ERROR` line for the whole session |
| Keyboard, 390 px | "2. Tab reaches the incomplete-scopes disclosure", "7. Tab reaches Diagnostics"; no horizontal scroll at 390 px on risks, the investigation and all three comparison states (×5); the gate's phone phase, 98 loads, none overflowing |

### 6.4 DEMO-PUBLIC-01: reviewer presentation — **intact**

* `README.md`, `docs/demo/README.md`, `access-investigation.md`, `coverage-and-recovery.md`,
  `docs/architecture/` and every earlier report are **byte-identical** to `main`. That covers
  the reviewer README, the synthetic-data declaration, the code-to-review section, the
  representative tests, the architecture links, the demo guide, the known limitations and the
  report links. The README names no React #418 limitation that this change would make stale.
* The DEMO-PUBLIC-01 documentation walkthrough checks every label, route and number the README
  and the demo guide cite against the running application: **39/39 in all 5 runs**, 3 direct
  and 2 through the stream-delay proxy.
* The README's setup and test commands were the ones used here (§10, §11).

---

## 7. React #418

### 7.1 The previously affected routes and the accepted reproduction

From the repository, not memory:

* **Public demo** (DEMO-PUBLIC-01 §13.4, `browser-runs.json`): `next start` of `main`'s tree
  on Next 15.5.25, local API; the unchanged ADG-DEMO-03 walkthrough saw `Minified React error
  #418 (args[]=HTML)` in 1 of its 3 runs, 1 of 5 browser runs overall. Every content check
  passed in that run.
* **Cause and method** (ADG-UI-EDGE-01 §5, on `integration/ADG-UI-EDGE-01`): every
  occurrence on a full page load, `document.readyState === "loading"`, 2–5 ms after a late RSC
  row; seen on `/`, `/status`, `/identities`, `/access/explain` and `/demo`. A host component
  claims its server node, its children suspend on a not-yet-streamed RSC row, React replays
  the unit of work without restoring the hydration cursor, and the second claim fails. It is
  a race between fast JavaScript (the edge) and slow streamed HTML/RSC (the origin).
* **Accepted gate** (ADG-NEXT16-REACT418-01 §3): the repository's `browser.mjs` and
  `navigation.mjs` (deploy-branch files), an instrumented copy of `browser.mjs` and a full-load
  reload loop, not committed. Repetitions there: 5 browser-gate runs, 10 navigation runs,
  10 instrumented runs and 224 reloads on staging (Next 15: #418 in 3 of 56 reloads; Next 16:
  0 in 224), and locally 3 instrumented runs and 56 reloads, with the caveat that "local runs
  are not evidence on their own: Next 15 was not run locally for a baseline".

### 7.2 This gate

That caveat is addressed here: the same harness ran against `main` (Next 15.5.25) and the
integration (Next 16.4.1) on the same machine, API and database, and it was shown to see #418
on both builds before any zero was counted.

* **Harness:** [`hydration-gate.script.js`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-gate.script.js)
  (Playwright 1.56.1, Chromium 141, signed in as the development auditor). One round is:
  * **fresh** — a new browser context (empty cache), then a full load of 17 reviewer routes:
    `/`, `/demo`, the Fatima → Apollo and SYSTEM investigations, the Fatima → Apollo
    explanation, `/risks`, `/collectors`, the comparison drill (`time_budget_ms=1`), the
    incomplete comparison and the folder-permission recovery, `/identities`, Fatima's and an
    unresolved SID's principal pages, `/status`, `/changes`, `/search?q=Apollo`, `/resources`;
  * **reload** — each route loaded, then reloaded 3 times (the reload loop);
  * **nav** — client-side navigation along the reviewer's path: the guide → investigation →
    Back → risk totals → Back → Forward → Back → the comparison drill → *Retry inside the
    standard 4-second window* → *Compare only folder permission entries*, then every
    *Primary* navigation link;
  * **keys** — keyboard only: Tab to and Enter on the investigation's hop evidence, the risk
    page's incomplete-scopes disclosure and the comparison's Diagnostics;
  * **phone** — 390 px loads of the guide, the investigation, the risks, the three
    comparison states and the unresolved principal, with horizontal overflow recorded.
* **Detection:** every `console.error` and page error while a load is current, classified as
  #418 (`#418`, `react.dev/errors/418`, "Hydration failed", "hydrat…"), other hydration codes
  (#419–#429), the known `/favicon.ico` 404, or other. An in-page hook records the
  `readyState` when React reports.
* **Timing conditions:** *direct* (browser → `next start`), and through
  [`stream-delay-proxy.script.js`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/stream-delay-proxy.script.js),
  which recreates the hosted timing on one machine: `/_next/static` passes straight through
  (the edge), every other response is forwarded chunk by chunk, each held 15, 40 or 100 ms
  (the origin's stream).
* **Positive control** (`GATE_CONTROL=1`): before React hydrates, one foreign element is put
  ahead of `div.shell`'s first child — the node ADG-UI-EDGE-01 caught #418 on — so every full
  load must report #418. It ran on each build before that build's real runs.
* **Every attempt is recorded,** pilots included; nothing was retried or dropped:
  [`hydration-runs.json`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/hydration-runs.json)
  lists every run and every load.

### 7.3 Results

Every gate run, in the order made (the API and database were the same throughout; `main`
was served from a worktree of `744c2ef`, the integration from the clean clone of `501c136`):

| # | Run | Build | Condition | Rounds | Full loads | Client transitions | Loads with #418 | Other app errors | Failed steps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | pilot, control | Next 15.5.25 | direct + foreign node | 1 | 62 | 19 | **62** (expected) | 0 | 0 |
| 2 | pilot | Next 15.5.25 | direct | 2 | 192 | 38 | 0 | 0 | 0 |
| 3 | pilot | Next 15.5.25 | 40 ms | 1 | 96 | 19 | **9** | 0 | 0 |
| 4 | B2 | Next 15.5.25 | 40 ms | 3 | 288 | 57 | **37** | 0 | 0 |
| 5 | B3 | Next 15.5.25 | 15 ms | 1 | 96 | 19 | **10** | 0 | 0 |
| 6 | B4 | Next 15.5.25 | 100 ms | 1 | 96 | 19 | **11** | 0 | 0 |
| 7 | I1, control | **Next 16.4.1** | direct + foreign node | 1 | 62 | 19 | **62** (expected) | 0 | 0 |
| 8 | I2 | **Next 16.4.1** | direct | 4 | 384 | 76 | **0** | 0 | 0 |
| 9 | I3 | **Next 16.4.1** | 40 ms | 6 | 576 | 114 | **0** | 0 | 0 |
| 10 | I4 | **Next 16.4.1** | 15 ms | 2 | 192 | 38 | **0** | 0 | 0 |
| 11 | I5 | **Next 16.4.1** | 100 ms | 2 | 192 | 38 | **0** | 0 | 0 |

Summed, controls excluded:

| | Next 15.5.25 (`main`) | Next 16.4.1 (integrated) |
| --- | --- | --- |
| Stream-delayed full loads with #418 | **67 of 576 (11.6%)** — 15 ms 10/96, 40 ms 46/384, 100 ms 11/96 | **0 of 960** — 15 ms 0/192, 40 ms 0/576, 100 ms 0/192 |
| Direct full loads with #418 | 0 of 192 | 0 of 384 |
| Client-side transitions with #418 | 0 of 152 | 0 of 266 |
| Other application console or page errors | 0 | 0 |
| Failed steps (navigation, keyboard, recovery clicks) | 0 | 0 |
| 390 px loads with horizontal overflow | 0 | 0 |

**What the baseline shows.**

* **Where:** #418 on Next 15 came from 9 pages: the incomplete comparison (17), the demo
  guide (10, plus 1 at the navigation start), `/identities` (8), the Fatima → Apollo
  investigation (7, plus 2 in the keyboard phase), `/resources` (7), the folder-permission
  recovery (5), `/collectors` (4), the Fatima → Apollo explanation (4) and `/status` (2).
  They include the investigation and the comparison/recovery pages of the reviewer flow, and
  four of the five routes ADG-UI-EDGE-01 named (`/status`, `/identities`, `/access/explain`,
  `/demo`).
* **When:** 66 of 67 at `document.readyState === "loading"` and one at `complete`. Most came on
  reloads (49), with the rest on fresh loads (10), at 390 px (5), during the keyboard phase
  (2) and at the navigation start (1).
* **What:** all 67 were `Minified React error #418; … args[]=HTML`, thrown from `rD` in the
  react-dom chunk `4bd1b696-f785427dddbba9fb.js`: the thrower ADG-UI-EDGE-01 traced, and the
  message the public demo recorded.
* **Why direct runs are not the gate:** on loopback #418 is rare. DEMO-PUBLIC-01 saw it once
  in 5 walkthrough runs; here it was 0 of 192. That fits a race against a slow stream. The
  stream-delay runs are the discriminating ones. Client-side transitions do not hydrate, so
  neither build shows #418 on them.

**Strength of the result.**

* At the baseline's 11.6%, 960 consecutive clean stream-delayed loads would happen by chance
  with probability about 0.884⁹⁶⁰ ≈ 10⁻⁵². At 40 ms alone (12.0%, 46 of 384), 576 clean loads:
  about 10⁻³².
* The 95% upper bound on Next 16's per-load #418 rate under the same conditions is 0.31%
  (rule of three over 960 loads); over all 1,344 loads it is 0.22%.
* The accepted candidate's own gate was 0 of 224 hosted reloads.

**Console findings.**

* No application console error and no page error in any non-control run, on either build.
* The only console line ever recorded besides #418 is the browser's own
  `Failed to load resource: 404 … /favicon.ico`. It is pre-existing (the application ships no
  favicon) and is recorded by every earlier walkthrough.
* No other hydration-family code (#419–#429) appeared.

The walkthroughs in §12 add 15 more runs on the integrated build with their own
console-error checks, all passing.

---

## 8. API and contracts

| | |
| --- | --- |
| Explanation contract | **`1.1`** (`CONTRACT_VERSION: Final = "1.1"`, `backend/app/api/caching.py`), unchanged |
| OpenAPI | `python -m app.contracts.openapi` regenerated `docs/contracts/v1/openapi.json` and `openapi.demo.json` from the integrated source: **zero drift** (`git status` clean afterwards) |
| Derived schemas | `python -m app.contracts.derived` regenerated the three derived schemas: **byte-identical** |
| Backend contract tests | `tests/contracts`: part of the unit suite, which passed in full locally and in CI (§10.1, §13) |
| Frontend contract test | `tests/contracts.test.ts`: **465 passed** (equal to ADG-DEMO-03) |

No API, schema or generated artifact changed, so none was reverted. The candidate's
OpenAPI and derived-schema changes belong to ADG-UI-UPDATE-01 and are not integrated (§3.4).

---

## 9. Migrations

| | |
| --- | --- |
| `alembic heads` | **one head: `0018_risk_evaluation_basis`** |
| `alembic history` | linear from `0014_merge_alerts_and_reviews` through `0015`–`0017` to `0018_risk_evaluation_basis` (`down_revision = 0017_deployment_marker`); the historical branch points (`0007`, `0012`) and merge points (`0012`, `0014`) are the existing ones |
| Candidate migrations | none: neither Next 16 head changes any file under `database/` (where `database/migrations/versions/` lives) since the merge-base; this branch changes nothing under `database/` against `main` |
| GRAPH | not imported. `database/migrations/versions/0018_graph_relationships.py` exists only on the `feature/ADG-GRAPH-01*` heads, none of which is an ancestor of either Next 16 head or of this branch (`git merge-base --is-ancestor`, all false) |

The demo reset (which migrates) ran on this backend tree, and CI's database suite ran on this
head.

---

## 10. Tests

All local gates ran in this container on the code head `501c136` unless marked. Python
3.13.16, PostgreSQL 16.15, Node 22.22.0. PostgreSQL was stopped when the container started;
it was started with `pg_ctlcluster 16 main start` and the role and databases created, an
environment step.

### 10.1 Backend

The backend tree is identical to `main`'s, whose CI run 77 already proved it. The gates were
run again anyway:

| Gate | Result |
| --- | --- |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** (412 files) |
| `mypy app tests` | **passed** (411 source files) |
| `pytest -m "not smoke"` (full unit suite, includes `tests/contracts`) | **6,009 passed, 124 skipped, 1,273 deselected**, equal to ADG-DEMO-03 and DEMO-PUBLIC-01 |
| Representative unit tests (README: SYSTEM mapping, SYSTEM explanation regression, investigation, unidentified trustees, coverage) | **229 passed** |

### 10.2 PostgreSQL

| Gate | Result |
| --- | --- |
| Demo reset (`python -m app.demo_mode reset`, migrates first) | 300 runs (297 succeeded, 2 failed, 1 partial), 41,756 observations, probes 23/23, 9,386 open findings, fingerprint **`228b9dce…f9efd0`**, equal to DEMO-PUBLIC-01 |
| Representative DB tests (README command: `test_system_mapping_api`, `test_collection_coverage`, `test_comparison_bounds`, `TestTheSystemExplanation`, `TestTheAccessInvestigation`, `TestTheCoverageAndRecoveryDemo`) | **58 passed** in 2m46s, equal to DEMO-PUBLIC-01's 58. The three Null Meridian classes each run a real reset |
| `pytest -m smoke` (full DB suite) | **1,272 passed, 1 xfailed** in 31m29s, in CI run 80 on this head (§13). Equal to ADG-DEMO-03 and DEMO-PUBLIC-01; the xfail is the pre-existing one in `test_access_performance.py`. Not rerun locally: the backend is tree-identical and CI ran it on this exact commit |

### 10.3 Frontend

| Gate | Result |
| --- | --- |
| `npm run lint` | **passed** (ESLint 9 with the flat `eslint-config-next` 16.4.1) |
| `npm run typecheck` | **passed** |
| `npm run test` (full) | **1,361 passed** (38 files), equal to `main` |
| `tests/contracts.test.ts` | **465 passed** |
| Unresolved-identity, investigation, demo-guide, risk, coverage and comparison suites (`identity`, `views`, `pages`, `system-mapping`, `investigation`, `investigation-page`, `demo-guide`, `risks`, `risks-page`, `coverage`, `compare-page`, `changes`) | **269 passed** (12 files) |
| `npm run build` | **passed**, `▲ Next.js 16.4.1 (webpack)`, 39 routes; also in the clean clone (§11) |

`af94e2e` (Next 16.4.0) passed lint, typecheck, 1,361 tests and the build before
`501c136`.

---

## 11. Build and clean-clone validation

| Step | Result |
| --- | --- |
| Clean clone | `git clone --branch claude/beautiful-knuth-3zruji https://github.com/40ozs/adg` into an empty directory: `501c136`, clean tree, no `node_modules` |
| Install from the lockfile | `npm ci`: 466 packages; `next` 16.4.1, `react-dom` 19.3.0 |
| Production build | `npm run build` → `next build --webpack`, `▲ Next.js 16.4.1 (webpack)`, 39 routes; the clone's tree still clean afterwards |
| Start | `next start -p 3000` → `▲ Next.js 16.4.1`, ready in 145 ms |
| Use | **every integration browser run in §7 and §12 ran against this clean-clone server**, with the API in demo mode over the reset database |

The session tree's own build (`npm install` path, as CI does) also passed, as did CI's build
job.

---

## 12. Browser

All against the clean-clone production build of `501c136` with the API in demo mode over the
reset database. The three earlier walkthrough scripts ran **unchanged** from their evidence
directories. Results: [`browser-runs.json`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/browser-runs.json).

| Walkthrough | Runs | Result |
| --- | --- | --- |
| **ADG-DEMO-02**: guided investigation, Fatima → Apollo (granted, `Modify`, `at_least`), both routes, hop provenance by keyboard, missing evidence, the unresolved SID's page, SYSTEM over a share and locally, 390 px, console | 3 | **29/29** each |
| **ADG-DEMO-03**: risk reading and coverage, the unavailable NM-FS-03, the investigation's coverage section, the drill (timed out → retry incomplete → narrowed, complete and recovered), Diagnostics by keyboard, 390 px on five surfaces, console | 5 | **39/39** each |
| **DEMO-PUBLIC-01**: every label, route and number the README and the demo guide cite | 3 direct, 2 through the 40 ms proxy | **39/39** each |
| **DEMO-01 UI** ([`demo-01-ui.script.js`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/demo-01-ui.script.js)): SYSTEM mapping states and the unresolved-identity banner | 2 (and 1 against `main` before the switch: 15/15) | **15/15** each |
| **Total** | **15** | **507 of 507 checks** |

* The repeated hydration gate (§7) added, on this build, 14 rounds of fresh loads, reloads,
  client navigation along the reviewer path (guide → investigation → Back → risks → Back →
  Forward → Back → drill → retry → narrowed recovery → every Primary navigation link),
  keyboard interaction and 390 px loads. It had no failed step and no overflow.
* **Console:** each walkthrough's own "no console errors from the application" check passed
  in every run. The only console entry is the pre-existing `/favicon.ico` 404.
* **Reviewer navigation and public paths:** every Primary navigation link resolved, and the
  routes the README and the guide cite all rendered (DEMO-PUBLIC-01 walkthrough). No public
  path is broken.
* Screenshots from the integrated build:
  * [investigation](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/next16-investigation-desktop.png);
  * [risk coverage at 390 px](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/next16-risks-coverage-phone.png);
  * [recovered comparison](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/next16-comparison-recovered.png);
  * [SYSTEM over a share](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/next16-system-over-share.png);
  * [unresolved principal](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/next16-unresolved-principal.png).

---

## 13. CI

GitHub Actions (`.github/workflows/ci.yml`) on `claude/beautiful-knuth-3zruji`:

* **Run 80** on the code head `501c136`
  ([run](https://github.com/40ozs/adg/actions/runs/38761041680)): **every job passed.**
  * Backend: lint, format, `mypy app tests` (411 files), unit tests **6,121 passed, 12
    skipped** (15:32–15:35 UTC), **database tests 1,272 passed, 1 xfailed** (15:35:11–16:06:45
    UTC). [`ci-run-80-backend.txt`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/ci-run-80-backend.txt)
    holds the result lines.
  * Frontend: install, lint, types, tests, build (15:31:21–15:32:39 UTC).
  * Collector (Pester, `windows-latest`).
  * Docker Compose configuration.
* CI's unit step skips 12 tests where this container skips 124. 107 of this container's 124
  need PowerShell 7 (`pwsh`), which GitHub's Ubuntu runner has and this container does not;
  the rest are Windows-only or seed-dependent cases. The collected set is the same:
  6,121 + 12 = 6,009 + 124 = 6,133, and 1,273 deselected in both.
* The report commit changes only `docs/`; its own run is reported in the final response, not
  here.

For reference, the candidate's own CI: runs 27 (`66ca201`), 29 (`f5005b1`), 32 (`04663bc`)
and 34 (`3e26e4a`), all success. `main` (`744c2ef`): run 77, success.

---

## 14. Limitations

### 14.1 Environment

1. **Hosted timing is simulated, not measured.** The stream-delay proxy recreates the
   fast-JavaScript / slow-stream race on one machine, and the baseline proves it reproduces
   #418 on Next 15. It is still not the Worker and Render path the original staging evidence
   used.
2. **Chromium only.** Firefox and Safari were not run (as in every earlier browser report).
3. **One 4-vCPU container.** Baseline and integration ran one after the other on the same
   machine, API and database, with nothing heavy running beside either.
4. **No browser-test framework** was added. The gate and checks are one-off scripts kept as
   evidence, like ADG-DEMO-02/03 and DEMO-PUBLIC-01's.
5. Not run locally: Collector Pester tests and `docker compose config` (no Windows, no
   Docker). CI ran both (§13). The Docker Compose demo path remains unrun, as before.
6. The full PostgreSQL suite was not rerun locally; CI ran it on this exact head.

### 14.2 Hosted deployment

1. **This integration deploys nothing and changes no deployment configuration.** `main` has
   no Worker, Render or staging workflow, and none was imported.
2. **The existing staging** (`deploy/ADG-FREE-STAGING-01` @ `3e26e4a`, Render + Worker), per
   its last report (`adg-next16-ui-integration-02.md` §5), runs Next 16.4.0 plus the
   UI-UPDATE-01 and COMPACT-IDENTITY waves. It does not run `main`'s public-demo line or 16.4.1.
   It was not touched or re-checked here.
3. **Follow-up to host this line:** integrate the deploy line onto `main` in its own
   controlled change (Worker, `frontend/render/gate.ts`, `Dockerfile.render`, the staging
   workflow, `backend/app/origin_gate.py`, the `next.config.mjs` standalone output and the
   `cf:*` scripts), then rerun its `smoke.mjs`, `browser.mjs`, `navigation.mjs`, the
   `wrangler deploy --dry-run` and the hosted #418 gate. Separately, the staging deploy branch
   should take 16.4.1.
4. **Turbopack is not used** (`next build --webpack`), as accepted. Moving to it would need
   the edge-asset chunk layout re-checked if the Worker is ever integrated.

### 14.3 Accepted product limitations (unchanged)

From ADG-DEMO-01 to -03, disclosed in the README and demo guide; none is affected here:
expected collectors per kind, not per server; "stale" is ordinal, not an age; the page-wide
collection-health banner unchanged; an estate-wide comparison cannot finish in one
interactive request, recovery is by narrowing; bounds are in-process checkpoints, not a job
system; remote-database latency modeled, not measured; SYSTEM over a share never resolved to
a computer account; the collectors have never met a real domain.

### 14.4 Not integrated, deliberately

* **ADG-UI-UPDATE-01 and ADG-UI-COMPACT-IDENTITY-01** (Users/Groups directories, shortest
  chains, sortable tables, compact identity, Exclude local groups, SID truncation): they carry
  backend API and contract changes and the principal-page restructure. They need their own
  integration against this line, resolving `principal/page.tsx` by reapplying the
  unresolved-identity banner, regenerating OpenAPI and derived schemas from merged source, and
  rerunning the ADG-DEMO suites.
* **The deploy line** (§14.2).
* **GRAPH** (`feature/ADG-GRAPH-01*`): out of scope; a second `0018` would need a merge
  revision.
* **`npm audit`**: 13 findings remain in test and lint tooling (§5.2); no upgrade beyond
  `next` was made.

---

## 15. Merge recommendation

**Merge `claude/beautiful-knuth-3zruji` to `main` at its ending SHA**: the commit that adds
this report, named in the final response. Its code is `501c136`, and the report commit changes
only `docs/demo/reports/`.

* `main` (`744c2ef`) is an ancestor of the branch, so while `main` has not advanced this is a
  **fast-forward**: `git merge --ff-only <ending SHA>`. No merge commit, no rebase.
* If `main` first takes `claude/intelligent-fermi-nxp2jw` (the DEMO-PUBLIC-MERGE-01 report, one
  new file), the two touch disjoint files. A merge is then needed and is mechanical; rerun CI
  on the merge.
* **Before merging, check:**
  1. CI on the ending SHA (documentation-only over `501c136`, whose run 80 passed every job);
  2. `git diff --stat 744c2ef <ending SHA>` lists exactly the four `frontend/` files plus
     this report and its evidence directory;
  3. `origin/main` is still an ancestor.
* **After merging:**
  1. a fresh `npm install` (or `npm ci`) in `frontend/` for anyone with a Next 15
     `node_modules`;
  2. nothing else: no migration, no API or contract change, no environment variable.
* **Separate follow-ups, not part of this merge:**
  1. integrate ADG-UI-UPDATE-01 / COMPACT-IDENTITY-01 against this line (§14.4);
  2. bring the deploy line to `main`, or move staging to this line and to 16.4.1, then rerun
     the hosted gates (§14.2).
