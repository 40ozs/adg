# ADG-NEXT16-POST-DEMO-INTEGRATION-01 — Next.js 16 / React #418 fix on the public-demo `main`

## 1. Verdict

**`READY_FOR_ADG_NEXT16_POST_DEMO_MERGE_WITH_LIMITATIONS`**

The accepted Next.js 16 upgrade is integrated onto the current public-demo `main` (`32da1ae`)
on `claude/adoring-lamport-1e22j9`. It is the framework commit `66ca201` alone, then Next.js 16.4.1,
the October 14 security release that commit's own report required before `main`. The code
change is four files under `frontend/` (`package.json`, `package-lock.json`,
`eslint.config.mjs`, `tsconfig.json`), plus the README's Node floor (20.9, which Next 16
requires). No application code, test, backend file, migration, generated contract, demo guide
or report was changed. None of the deploy or UI-feature commits under the Next 16 branch was
imported.

**React #418 is not reproduced on the integrated build.** The same harness ran against `main`
(Next 15.5.25) and the integration (Next 16.4.1), interleaved, on the same API and database.
A positive control showed it detects #418 on both builds (20 of 20). Under a slow-origin stream
— fast JavaScript, slow HTML/RSC, the hosted timing ADG-UI-EDGE-01 traced #418 to — **`main`
failed on 90 of 900 full loads (10%) and in 20 of 20 runs; the integration failed on 0 of 1,170**
(900 on the session build, 270 on a clean clone of the pushed branch). Across every condition
the integration had **0 #418 in 1,590 full loads and 460 client-side navigations (46 runs)**,
and no application console error other than the pre-existing `/favicon.ico` 404. At `main`'s
rate, 1,170 clean loads would happen by chance with probability about 10⁻⁵³ (§10).

Every ADG-DEMO-01, -02 and -03 behavior and the DEMO-PUBLIC-01 reviewer experience were
re-proved on the integrated tree: the representative engine, UI and PostgreSQL sets (every
count equal to DEMO-PUBLIC-01), and the unchanged reviewer walkthroughs against the clean-clone
build (29/29, 39/39 ×3, 39/39). CI run 83 passed every job on the pushed code.

The limitations (§18) are about scope and environment, not defects in the integration:

* **a parallel integration of the same prompt exists** on `claude/beautiful-knuth-3zruji`, with
  byte-identical code on an older base and a report at **this report's path**. Only one of the
  two branches should go to `main` (§3, §19);
* the hosted timing was recreated on one machine; the Worker/Render path was not exercised, and
  hosted validation of this line remains a separate task (§17);
* only Chromium was run.

### Acceptance criteria

| # | Criterion | Met | Where |
| --- | --- | --- | --- |
| 1 | starts from current public-demo `main` | yes, `32da1ae`, unchanged after fetch | §2 |
| 2 | `claude/beautiful-knuth-3zruji` classified | yes, `RELEVANT_NEXT16_WORK` | §3 |
| 3 | candidate identified from repository evidence | yes, `66ca201` on `fix/ADG-NEXT16-REACT418-01` + 16.4.1 | §4 |
| 4 | no unrelated branch imported | yes; 4 framework files only | §4, §5 |
| 5 | public-demo documentation intact | yes; README changed in 2 table cells only | §5, §15 |
| 6 | DEMO-01 correctness | yes | §7 |
| 7 | DEMO-02 investigation | yes | §8 |
| 8 | DEMO-03 coverage | yes | §9 |
| 9 | DEMO-03 recovery | yes | §9 |
| 10 | unresolved identity behavior | yes | §7, §15 |
| 11 | contract valid | yes, `1.1` | §11 |
| 12 | generated OpenAPI current | yes, byte-identical on regeneration | §11 |
| 13 | single intended Alembic head | yes, `0018_risk_evaluation_basis` | §12 |
| 14 | frontend lint | passes | §13 |
| 15 | frontend typecheck | passes | §13 |
| 16 | full frontend tests | 1,361 passed | §13 |
| 17 | production build | passes | §13 |
| 18 | clean install/build | passes, from a GitHub clone | §15 |
| 19 | repeated React #418 gate clean | 0 in 1,590 loads, with a failing control | §10 |
| 20 | reviewer browser flows | pass | §15 |
| 21 | CI | run 83: every job green | §16 |
| 22 | durable evidence | this report and its evidence directory | — |

---

## 2. Base

| | |
| --- | --- |
| Repository | `40ozs/adg` (public; visibility not changed) |
| Starting `main` | **`32da1aecbc83a195f9db0849ec4dd5f652cc8bde`**, as the prompt named it. After `git fetch` of every head, `origin/main` was still `32da1ae`: it had not moved. `32da1ae` is `744c2ef` (the public demo line, CI run 77) plus the two ADG-DEMO-PUBLIC-MERGE-01 report commits; `main` was fast-forwarded to it at about 17:08 UTC (CI run [81](https://github.com/40ozs/adg/actions/runs/38774844746) on `main`). The application trees are those of `744c2ef` |
| Integration branch | **`claude/adoring-lamport-1e22j9`**, from `32da1ae`. The preferred name `integration/ADG-NEXT16-POST-DEMO-01` was not used: this session may push only to its own branch. Renaming later is a ref operation (`git push origin claude/adoring-lamport-1e22j9:integration/ADG-NEXT16-POST-DEMO-01`) |
| Candidate branch | `fix/ADG-NEXT16-REACT418-01` @ `04663bcdcf190c480fcefb88b5a9550636d28d94` |
| Candidate SHA (integrated) | **`66ca20151e9bacfb96ad3cbff689d2b5cdcfa417`**, the framework commit, then the 16.4.1 security release its own report requires (§4) |
| Merge base, candidate ↔ `main` | `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af` (PR #2). `main` is 26 commits ahead of it, the candidate 19 |
| `main` | unchanged; nothing was merged to it |

Commits on the integration branch, in order:

| SHA | What |
| --- | --- |
| `c054159` | `frontend: Next.js 16.4.0 and React 19.3, webpack build` — `66ca201`'s four framework edits applied to `main`'s files, deploy-only context dropped, lockfile regenerated from `main`'s with npm (§4.3) |
| `3524a02` | `frontend: Next.js 16.4.1, the October 14 security release` — **the code head** |
| `0a65d91` | `README: Node.js 20.9 or later, the floor Next.js 16 sets` — two table cells (§6) |
| report | this report and its evidence directory |

No merge commit, rebase or force push. No other branch was modified or deleted.

---

## 3. Parallel branch preflight: `claude/beautiful-knuth-3zruji`

**Classification: `RELEVANT_NEXT16_WORK`** — a second, parallel execution of this same
integration, on an older base. Its code is byte-identical to this integration's. It was not
merged here, and **its report occupies the same path as this one**, so only one of the two
branches should go to `main` (§19).

| | |
| --- | --- |
| Head at preflight | `501c1367e575efb51fedfe2353187024359acd52`, 2 commits ahead of `744c2ef`, as reported |
| Head now | `aff70e85f0e81ff5ad2241f7219d87237575b5f3`: during this session it gained a third commit (17:28:57 UTC), *Record ADG-NEXT16-POST-DEMO-INTEGRATION-01: report and evidence* |
| Merge base with `main` | `744c2efe89fb17f49cdfe640ebe5afd7d18253af`. `main` has 2 commits it lacks (the MERGE-01 report, docs only); it has 3 that `main` lacks |
| Unique commits | `af94e2e` *frontend: Next.js 16.4.0 and React 19.3, webpack build* (`66ca201` cherry-picked with `-x`, deploy context dropped); `501c136` *frontend: Next.js 16.4.1, the October 14 security release*; `aff70e8` the report |
| Files | `af94e2e`/`501c136`: `frontend/package.json`, `package-lock.json`, `eslint.config.mjs`, `tsconfig.json`. `aff70e8`: `docs/demo/reports/ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md` and 14 evidence files |
| Report | its own `ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md`, verdict `READY_FOR_ADG_NEXT16_POST_DEMO_MERGE_WITH_LIMITATIONS`, base `744c2ef` |
| Origin | Claude Code session `session_01Y1HQakMXdvyuBJ7iGLy25x`, titled *Next 16 React #418 integration*, created 15:21 UTC; still running when this preflight looked (17:15 UTC). It started before `main` moved from `744c2ef` to `32da1ae` |
| Contained elsewhere? | No. `66ca201` is not its ancestor (it carries a cherry-pick); no other head contains `af94e2e` or `501c136` |
| Overlap | Next 16 / React #418 only. No GRAPH, deployment, backend, contract, migration or demo-content file |
| Trial merge into `32da1ae` | clean (`git merge-tree --write-tree`); it would change 19 files: the 4 frontend files and its 15 report files |
| CI | run [80](https://github.com/40ozs/adg/actions/runs/38761041680) on `501c136`: success. run [82](https://github.com/40ozs/adg/actions/runs/38777706287) on `aff70e8`: success. Still at `aff70e8` when this report was committed |

**How it was verified, not trusted.** The integration was rebuilt here from the accepted
source (`66ca201`) without using that branch:

1. on a scratch worktree of `main`, `66ca201` was cherry-picked; all four files conflict,
   because `66ca201` sits on the staging deploy line;
2. each file was set to `main`'s and only `66ca201`'s framework hunks were reapplied. The
   three config files came out **byte-identical** to `af94e2e`'s;
3. the lockfile was regenerated from `main`'s with `npm install --package-lock-only`, then
   bumped with `npm install next@16.4.1 eslint-config-next@16.4.1`: `package.json` and
   `package-lock.json` came out **byte-identical** to `501c136`'s;
4. the integration branch was then built the same way on `32da1ae` (§4.3). Its `frontend/`
   tree equals `aff70e8`'s, byte for byte (`git diff --exit-code`).

Its two claims about the upgrade were checked independently (§4.3, §6): every lockfile entry
resolves to the version `66ca201` locks except the 12 Next 16.4.1 entries and the 57 Worker
deploy-tooling entries it drops; and Next 16.4.0 and 16.4.1 bundle byte-identical `react` and
`react-dom`.

**Why it was not merged.** It is not needed: the code is identical, and this branch starts
from current `main` (`32da1ae`) rather than `744c2ef`. A `git merge --no-ff` of it was also
attempted at the start and refused by this session's permission policy (a branch another
running session was still writing to), so the change was derived from `66ca201` directly
instead.

**What its evidence adds.** Its report, now repository-owned on that branch, describes the
reproduction this report's gate replicates: a slow-origin proxy that lets `/_next/static`
through and holds every other response chunk (15, 40 or 100 ms), under which `main`
reproduced #418 on 67 of 576 loads and its integrated build on 0 of 1,344. Its numbers are
cited as corroboration only; every result in §10 was measured here, with this session's own
harness.

---

## 4. Candidate provenance

### 4.1 What exists

| Head | SHA | Contains | Report | CI |
| --- | --- | --- | --- | --- |
| `fix/ADG-NEXT16-REACT418-01` | `04663bc` | the framework commit `66ca201`, its two handoff commits (`f5005b1`, `04663bc`), and 16 staging-line commits below `66ca201` | `docs/handoffs/adg-next16-react418-01.md`: `UPSTREAM_REACT_418_RESOLVED` → `READY_FOR_ADG_NEXT16_UI_INTEGRATION` | runs [27](https://github.com/40ozs/adg/actions/runs/38324838064) (`66ca201`), [29](https://github.com/40ozs/adg/actions/runs/38327953244) (`f5005b1`), [32](https://github.com/40ozs/adg/actions/runs/38334295165) (`04663bc`): success |
| `integration/ADG-NEXT16-UI-02` | `3e26e4a` | all of the above, plus `fix/ADG-UI-COMPACT-IDENTITY-01` (merged at `9502b4b`) and `33cca01` (SID truncation) | `docs/handoffs/adg-next16-ui-integration-02.md`: `READY_FOR_ADG_COMBINED_STAGING_REVIEW` | — |
| `deploy/ADG-FREE-STAGING-01` | `3e26e4a` | the same commit, fast-forwarded to it (that handoff §8) | — | — |
| `claude/beautiful-knuth-3zruji` | `aff70e8` | a port of `66ca201` + 16.4.1 onto `744c2ef` (§3) | its own copy of this report | run 80: success |

* `fix/ADG-NEXT16-REACT418-01` is an ancestor of `integration/ADG-NEXT16-UI-02`, so they were
  not integrated separately. Neither head has advanced since its handoff.
* The handoffs were read from the branches (`git show`), not from memory: the cause and method
  in `adg-ui-edge-01.md` §5 (on `integration/ADG-UI-EDGE-01`), the change and gate in
  `adg-next16-react418-01.md` §1–§6, the combined staging in `adg-next16-ui-integration-02.md`.

### 4.2 What was accepted

**`66ca201` alone, then the security release its own report requires before `main`.** Not
either branch head:

* `66ca201` is the whole Next 16 change: four frontend files, "No application code, no Suspense
  boundary, no backend change". It needs nothing below it: none of its four files was changed
  by `main` since `1ab01a2`.
* Below it sit 10 deploy commits (Cloudflare Worker, Render origin gate, staging workflow,
  `backend/app/origin_gate.py`) and the ADG-UI-UPDATE-01 wave (directories, shortest-chain API,
  `SortableTable`, the principal page split into `principal/[key]/…`, with backend and OpenAPI
  changes). `integration/ADG-NEXT16-UI-02` adds the COMPACT-IDENTITY wave. Those were accepted
  for **staging review**, not `main`, and this prompt excludes deployment and feature work.
* The handoff's open item: "**October 14 security release:** upgrade to it as a follow-up
  patch, then rerun §3, before this is considered for `main`." Next.js 16.4.1 was published
  2026-10-14 17:43 UTC (with 15.5.28). It is the latest 16.x.

### 4.3 How it was applied

`66ca201`'s parent is `6b69fe3` on the staging line, so its files carry Worker context `main`
never had. Only its framework hunks were applied to `main`'s files:

| File | Change | Not taken from `66ca201` |
| --- | --- | --- |
| `package.json` | `next`, `eslint-config-next` `^15.5.4` → `^16.4.0` (then `^16.4.1`); `react`, `react-dom`, `@types/react`, `@types/react-dom` `^19.1.0` → `^19.3.0`; `build`: `next build --webpack`; `@eslint/eslintrc` removed | `cf:typecheck`, `cf:assets`, `@cloudflare/workers-types`, `wrangler` |
| `eslint.config.mjs` | `eslint-config-next/core-web-vitals` and `/typescript` imported as flat configs; `FlatCompat` removed | `.edge-assets/**`, `.wrangler/**` ignores |
| `tsconfig.json` | `jsx: react-jsx`; `.next/dev/types/**/*.ts` included | `cloudflare/worker.ts`, `.edge-assets`, `.wrangler` excludes |
| `package-lock.json` | regenerated from `main`'s lockfile with npm, never by hand | — |

**Lockfile check.** Every one of the 564 packages it resolves is in `66ca201`'s lockfile at the
same version, except the 12 Next entries now at 16.4.1 (`next`, `@next/env`, 8 `@next/swc-*`,
`eslint-config-next`, `@next/eslint-plugin-next`). `66ca201`'s 57 extra entries are the
Worker's deploy tooling (`wrangler`, `miniflare`, `workerd`, `esbuild`, `@cloudflare/*`,
`undici`, `youch`, …). In the intermediate 16.4.0 commit `sharp` stays at `main`'s 0.35.4
(Next 16.4.0 accepts `^0.35.4`); 16.4.1 requires `^0.35.5`, which is what `66ca201` locks.

What the candidate's report validated was `66ca201` on the staging tree, at Next 16.4.0. The
tree here differs in its application base (the public-demo line) and by the 16.4.1 patch, so
every gate was rerun here (§10, §13–§16).

---

## 5. Conflict map

Write-free trial merges (`git merge-tree --write-tree`) against `32da1ae`, and every file the
Next 16 line changed since `1ab01a2`, attributed to the commit that changed it
([`candidate-overlap.txt`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/candidate-overlap.txt)):

| Merge into `main` | Conflicts | Files changed vs `main` |
| --- | --- | --- |
| `fix/ADG-NEXT16-REACT418-01` (whole head) | `frontend/app/identities/principal/page.tsx` | 105 |
| `integration/ADG-NEXT16-UI-02` (whole head) | `frontend/app/identities/principal/page.tsx` | 109 |
| `claude/beautiful-knuth-3zruji` | none | 19 |
| **this integration** (`66ca201` + 16.4.1) | **none** | **4** (+ 2 README cells) |

Every file both lines changed, and why the integration did not need to resolve it:

| File (changed on `main` and on the Next 16 line) | Next 16 line commits | Resolution |
| --- | --- | --- |
| `frontend/app/identities/principal/page.tsx` | `552bcb8` (UI-UPDATE-01 directory split) | not imported; `main`'s page kept unchanged. It already reads `searchParams` as a `Promise` (Next 16-compatible), keeps the unresolved-identity banners and the investigation links |
| `frontend/components/Explanation.tsx`, `Identity.tsx`, `lib/identity.ts`, `lib/api/adg.ts`, `lib/contracts.ts` | `552bcb8`, `4f33e08`, `a5cdf88`, `98a9a79`, `33cca01` (UI waves) | not imported; `main`'s kept |
| `frontend/tests/demo-guide.test.tsx`, `contracts.test.ts`, `risks-page.test.tsx` | `552bcb8` | not imported; `main`'s kept |
| `backend/app/api/access.py`, `backend/app/services/access.py`, `backend/tests/api/test_authorization.py` | `61fedb8` (shortest-chain API) | not imported; backend byte-identical to `main` |
| `docs/contracts/v1/openapi.json`, `derived/access-explanation.schema.json`, `derived/resource-impact.schema.json` | `61fedb8`, `98a9a79` | not imported; regenerated from the integrated source, byte-identical to `main` (§11) |
| `README.md` | `643da21`, `cedca60` (deploy docs) | not imported; `main`'s reviewer README kept, plus the Node floor (§6) |

The four files `66ca201` changes (`package.json`, `package-lock.json`, `eslint.config.mjs`,
`tsconfig.json`) were never changed on `main` since `1ab01a2`: no overlap. Nothing listed in
the prompt's watch list conflicted, because nothing but the framework change was taken:

* principal page, access investigation, risk/coverage, comparison/recovery, the Demo Guide and
  `demo-guide.test.tsx`: unchanged from `main` (`frontend/app`, `components`, `lib`, `tests`
  byte-identical);
* Next configuration (`next.config.mjs`): unchanged; Worker/deployment configuration: none on
  `main`, none added;
* OpenAPI, backend contracts, migrations: unchanged.

**Part 9 (principal page) and Part 10 (Demo Guide) therefore did not arise:** there was no
hunk to reconcile, and the current page and guide are what ships. Their regressions were still
run (§7, §8, §15).

---

## 6. Framework state

| | `main` (`32da1ae`) | Integration (`3524a02`) |
| --- | --- | --- |
| Next.js | 15.5.25 (`^15.5.4`) | **16.4.1** (`^16.4.1`), npm `latest` |
| React the App Router runs (bundled in `next/dist/compiled`) | `19.2.0-canary-0bdb9206-20250818` | **`19.3.0-canary-278794d7-20261002`** |
| npm `react`, `react-dom`, `@types/react`, `@types/react-dom` | 19.3.0 (ranges `^19.1.0`) | 19.3.0 (ranges `^19.3.0`) |
| Node requirement (`next` `engines`) | `^18.18.0 \|\| ^19.8.0 \|\| >= 20.0.0` | **`>=20.9.0`**; CI and `docker/Dockerfile.frontend` use Node 22. The README's two Node cells now say 20.9 |
| Package manager / lockfile | npm, lockfile v3, 557 packages | npm, lockfile v3, 564 packages; `npm ci` clean |
| Bundler | webpack (Next 15 default) | **webpack**, explicitly (`next build --webpack`); Turbopack not used, as accepted |
| ESLint | 9.39.5, `FlatCompat` over `eslint-config-next` 15.5.25 | 9.39.5, `eslint-config-next` 16.4.1 flat configs; 113 rules active, 22 `@next/next`, 16 `react-hooks` (7.1.1), 20 `@typescript-eslint`. A probe `<img>` file raises `@next/next/no-img-element`, so the clean lint is real |
| TypeScript / JSX | 5.9.3; `jsx: preserve` | 5.9.3; `jsx: react-jsx`; `.next/dev/types/**/*.ts` included. `next build` leaves `tsconfig.json` as committed (Next 16 rewrites it when these two settings are missing); the tree stays clean after a build |
| `next.config.mjs` | `reactStrictMode`, empty `env` | unchanged |
| Server/client boundaries | 12 `"use client"` modules | unchanged (application code byte-identical) |
| Suspense / `loading.tsx` / `error.tsx` / middleware | none | none; nothing added, as accepted ("no Suspense boundary") |
| Worker / deploy configuration | none on `main` | none added |

**Why this fixes #418,** checked in the shipped bytes rather than taken from the handoff. In the
bundled `react-dom-client`, `replayBeginWork` for a host component (`case 5`) in Next 16.4.1
first steps back to the host parent and resets the hydration cursor to the element itself
(`popToNextHostParent(fiber)`, `nextHydratableInstance = fiber.stateNode`) before replaying;
the build in Next 15.5.25 goes straight to `unwindInterruptedWork`. That is the failure
ADG-UI-EDGE-01 §5 traced: the replayed `beginWork` tries to claim the element from a cursor
left at its first child. Next 16.4.0 and 16.4.1 bundle byte-identical `react` and `react-dom`
(`diff -r` of `dist/compiled/react*` from the two npm tarballs), so the security patch does not
touch the fix.

**`npm audit`:** 16 advisories on `main` (2 critical, 10 high, 4 moderate) → **13** here
(2 critical, 8 high, 3 moderate). Gone: `next` (moderate), `postcss` and `sharp` (high).
Remaining: `vitest`, `tinypool` (critical); `vite`, the `eslint-config-next` /
`@next/eslint-plugin-next` / `fast-glob` / `micromatch` / `braces` / `brace-expansion` chain,
`source-map-js` (high); `esbuild`, `vite-node`, `@vitest/mocker` (moderate). Only
`source-map-js` is in the production tree (`npm audit --omit=dev`). No further upgrade was made.

---

## 7. ADG-DEMO-01 regression (SYSTEM correctness)

**Passing.** The engine, API and UI code are byte-identical to `main`; these confirm it on the
integrated tree. Counts equal DEMO-PUBLIC-01 §12.

| Check | Result |
| --- | --- |
| Engine: `test_system_explanation_regression` + `test_system_mapping` + `test_resolver` | **145 passed** |
| UI: `system-mapping`, `explanation`, `explanation-views`, `identity` | **171 passed** |
| PostgreSQL: `test_system_mapping_api.py` + `TestTheSystemExplanation` | in the 58 passed (§14) |
| Browser (ADG-DEMO-02 script, Next 16 clean-clone build) | SYSTEM over a share *not determined*, mapping *ambiguous*, never "No access"; SYSTEM locally present, mapping confirmed (both checks pass) |

Covered: the four mapping states (`confirmed`, `ambiguous`, `missing`, `conflicting`); no
unsupported definite-negative SYSTEM result (*Not determined* / *Ambiguous*, never "No
access"); no identity invented from missing evidence; unresolved identities rendered as such
(the recorded-unresolved SID's banner, reached by client-side navigation in every gate run and
in the ADG-DEMO-02 walkthrough; an SID nothing names reads "Not found", as on `main`, §15);
lower-bound certainty; explanation wording that follows the evidence state.

---

## 8. ADG-DEMO-02 regression (the access investigation)

**Passing.**

| Check | Result |
| --- | --- |
| Engine: `test_investigation` + `test_unidentified_trustees` | **102 passed** |
| Dataset seed: `tests/demo/test_null_meridian_investigation.py` | **6 passed** |
| UI: `explanation-page` + `explanation-contract` | **112 passed** |
| UI: `investigation` + `investigation-page` + `demo-guide` | **68 passed** |
| PostgreSQL: `TestTheAccessInvestigation` (on a real Null Meridian reset) | in the 58 passed (§14) |
| Browser: the unchanged ADG-DEMO-02 walkthrough script | **29 / 29**, on the clean-clone build (§15) |

*Why does Fatima Gordon have access to `\\NM-ENG-FS01\Engineering\Projects\Apollo`?* —
access **granted**, rights **Modify**, certainty **`at_least`**, network context **SMB** ("over the
network"), unchanged. The direct, explicit NTFS route (removing it changes nothing: the redundant
route), the share route through `ENG-Mobile`, per-hop provenance, the missing and unresolved
evidence, the seven-question summary and the walkthrough navigation all check (§15). The
`TestTheAccessInvestigation` PostgreSQL class re-proves the same answer through the API on a real
reset.

---

## 9. ADG-DEMO-03 regression (coverage, timeout, recovery)

**Passing.**

| Check | Result |
| --- | --- |
| `tests/domain/test_coverage.py` | **26 passed** |
| Coverage drill dataset (`tests/demo/test_null_meridian_coverage_drill.py`) | **6 passed** |
| `tests/test_config.py` | **16 passed** |
| UI: `coverage` / `compare-page` / `risks-page` + `risks` | **29 / 18 / 38 passed** |
| PostgreSQL: `test_collection_coverage` + `test_comparison_bounds` + `TestTheCoverageAndRecoveryDemo` | in the 58 passed (§14) |
| Browser: the unchanged ADG-DEMO-03 walkthrough script | **39 / 39** in each of 3 runs, on the clean-clone build (§15) |

Collection and freshness states, failed/unavailable kept distinct from checked-and-empty, the
risk totals read as *not exhaustive* with `NM-FS-03`'s 189 findings held rather than zeroed, the
investigation's coverage context, the typed comparison outcomes, the 1 ms and 4 s bounds, the
incomplete labelling and the narrowed recovery all check in the browser (§15). The API's
`changes.compare` telemetry during those runs records each drill as `timed_out` (window 1 ms) →
`incomplete` (4,000 ms, `retry_of` the first) → `complete`, `recovered: true` (narrowed to
`ntfs_ace`, `retry_of` the second): **`timed_out → incomplete → recovered`**.

---

## 10. React #418 gate

### 10.1 The reproduction path, from the repository

* **The public demo's sighting** (DEMO-PUBLIC-01 §13.4): `next start` of the public-demo tree on
  Next 15.5.25 with a local API; the ADG-DEMO-03 walkthrough's console check saw `Minified React
  error #418` in 1 of its runs (1 of 5 browser runs overall); every content check passed.
* **Cause and method** (`adg-ui-edge-01.md` §5 on `integration/ADG-UI-EDGE-01`): every
  occurrence on a full page load with `document.readyState === "loading"`, just after a late RSC
  row; routes `/`, `/status`, `/identities`, `/access/explain`, `/demo`. A timing race between
  fast JavaScript (from the edge) and slow streamed HTML/RSC (from the origin).
* **The candidate's accepted gate** (`adg-next16-react418-01.md` §3): hosted browser and
  navigation gates, an instrumented gate, and reload loops (224 hosted reloads, 0 #418 on
  Next 16 vs 3 in 56 on Next 15). Its harnesses were not committed, and it says "local runs are
  not evidence on their own: Next 15 was not run locally for a baseline".
* **The parallel integration's gate** (on `claude/beautiful-knuth-3zruji`, §3): a slow-origin
  proxy on one machine reproduced #418 on Next 15 (67 of 576 loads).

So the gate here (a) runs Next 15 (`main`) and Next 16 (this integration) side by side, so a
zero on Next 16 is measured against a build that fails under the same conditions; (b) proves the
detector sees #418 on each build before counting zeros; (c) recreates the hosted timing locally;
and (d) records every run.

### 10.2 Harness

All in [`ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/gate/`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/gate/),
one-off evidence scripts (Playwright with the pre-installed Chromium), not wired into any
suite. The protocol was written down before phase 2 ran
([`gate-protocol.md`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/gate/gate-protocol.md)).

**One run** (a fresh browser process and context, signed in as the development auditor):

| Phase | What | Loads |
| --- | --- | --- |
| A. cold, 1280 px | full loads of `/`, `/demo`, `/identities`, Fatima Gordon's principal page, an unresolved SID's (`…-1108`, deleted) and an unrecorded SID's (`…-99999`), the Fatima → Apollo investigation and its explanation, SYSTEM over `\\NM-FS-01\Shared`, `/risks`, `/collectors`, the comparison drill (`time_budget_ms=1`), `/status`, `/resources`, `/search?q=Apollo` | 15 |
| B. client-side navigation | from a full load of `/demo`: *Investigate access* → *Inspect the SID* (unresolved banner) → Back → *Open the removal analysis* → the Demo Mode panel's *Explore the demo scenarios* → *View the risk totals* → back to the guide → *Run the comparison drill* (timed out) → *Retry inside the standard 4-second window* (incomplete) → *Compare only folder permission entries* (complete, recovered). Each step must stay in the same document | 1 + 10 transitions |
| C. 390 px | full loads of `/demo`, `/risks`, the investigation, the unresolved principal and the drill, with horizontal overflow measured | 5 |
| D. reload loop (gate v2 only) | `/`, `/demo`, `/identities`, the investigation, `/risks`, the drill: each loaded, then reloaded 3 times | 24 |

v1 run = 21 full loads + 10 transitions; v2 run = 45 full loads + 10 transitions.

**Detection.** Three independent channels per load: Playwright's `console` (type `error`) and
`pageerror` events, and an init script installed before any page script that wraps
`window.reportError` and `console.error` — where Next.js sends a recoverable hydration error —
and records `document.readyState` at that moment (v2). A load "has #418" when any channel
reports `#418`, "Hydration failed" or a hydration mismatch. Every other console or page error
is an application error, except the pre-existing `/favicon.ico` 404 (DEMO-PUBLIC-01 §13.4).

**Positive control** ([`positive-control.script.js`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/gate/positive-control.script.js)):
Playwright rewrites one heading in the server HTML of `/demo` (the DOM text only, not the RSC
payload in the same document), so hydration must mismatch.

**Conditions** (same API, same reset database, same machine):

| Condition | Path | Purpose |
| --- | --- | --- |
| direct | browser → `next start` → API | the public demo's local setup |
| l100 | API requests delayed 100 ms ([`latency-proxy.js`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/gate/latency-proxy.js)) | slow server data |
| rate | browser → [`stream-proxy-v2.js`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/gate/stream-proxy-v2.js) → `next start`: `/_next/static` at once, everything else uncompressed at 8 KiB every 20 ms | the hosted timing (fast JS, slow stream), with identical delivery for identical bytes |
| chunk40 | the same proxy, each upstream chunk held 40 ms | the parallel session's slow-origin condition, reimplemented |

The rate condition exists because a per-chunk delay is not neutral between the builds: measured
here on five reviewer pages, uncompressed, Next 15.5.25 flushes them in 11–121 chunks where
Next 16.4.1 flushes the same pages in 3–9, so a
per-chunk delay slows Next 15's stream more. The rate condition removes that confound.

Builds: N15 = `main` `32da1ae` (Next 15.5.25) from a worktree; N16 = this integration
`3524a02` (Next 16.4.1) from the session tree. Runs were interleaved round by round (N15, N16,
N15, N16 …) so both builds saw the same machine conditions.

### 10.3 Results

Every counted run, by phase, in the order made
([`summary.txt`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/gate/summary.txt); the run
files hold every load):

| Phase | Build | Condition | Runs | Full loads | Loads with #418 | Runs with #418 | Client navigations (with #418) | Other app errors | Failed steps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| control | N15 | direct / rate, HTML mutated | 2 | 10 | **10** (expected) | 2 | — | — | — |
| control | **N16** | direct / rate, HTML mutated | 2 | 10 | **10** (expected) | 2 | — | — | — |
| 1 | N15 | direct | 10 | 210 | **1** | 1 | 100 (0) | 0 | 0 |
| 1 | **N16** | direct | 10 | 210 | **0** | 0 | 100 (0) | 0 | 0 |
| 1 | N15 | l100 | 10 | 210 | 0 | 0 | 100 (0) | 0 | 0 |
| 1 | **N16** | l100 | 10 | 210 | **0** | 0 | 100 (0) | 0 | 0 |
| 2 | N15 | rate | 10 | 450 | **50** (11.1%) | 10 | 100 (0) | 0 | 0 |
| 2 | **N16** | rate | 10 | 450 | **0** | 0 | 100 (0) | 0 | 0 |
| 2 | N15 | chunk40 | 10 | 450 | **40** (8.9%) | 10 | 100 (0) | 0 | 0 |
| 2 | **N16** | chunk40 | 10 | 450 | **0** | 0 | 100 (0) | 0 | 0 |
| 3 | **N16, clean clone** | rate | 3 | 135 | **0** | 0 | 30 (0) | 0 | 0 |
| 3 | **N16, clean clone** | chunk40 | 3 | 135 | **0** | 0 | 30 (0) | 0 | 0 |

Totals, controls excluded:

| | Next 15.5.25 (`main`) | Next 16.4.1 (integration) |
| --- | --- | --- |
| Runs | 40 | 46 |
| Slow-origin full loads with #418 | **90 of 900 (10.0%)**, in 20 of 20 runs | **0 of 1,170** |
| All full loads with #418 | 91 of 1,320 | **0 of 1,590** |
| Client-side navigations with #418 | 0 of 400 | 0 of 460 |
| Other application console or page errors | 0 | 0 |
| Failed steps (navigation, recovery clicks) | 0 | 0 |
| HTTP ≥ 400 on a gate load | 0 | 0 |
| 390 px loads with horizontal overflow | 0 of 200 | 0 of 230 |

Every attempt is in the record: the 86 counted runs, the 4 control runs, and 4 harness
shakedowns kept separately ([`shakedown-summary.txt`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/gate/shakedown-summary.txt)):
a selector bug that broke 6 navigation steps (fixed: the guide link sits in the Demo Mode
panel), its fixed rerun, a first proxy that left gzip on and did not reproduce #418 on Next 15,
and the v2 harness on Next 15 (6 of 45 loads with #418). No counted run was retried, dropped
or relabelled.

### 10.4 What the control shows

* **Signature.** All 90 messages captured on Next 15 in phase 2 are `Minified React error
  #418; … args[]=HTML`, the message DEMO-PUBLIC-01 recorded; the phase-1 hit is the same.
* **Where.** 8 reviewer pages: `/demo` (37 loads), the Fatima → Apollo investigation (18),
  `/identities` (17), `/` (10), the unrecorded SID's principal page (4), `/resources` (2),
  `/search` (2) and `/status` (1); on cold loads, reloads and at 390 px. They include four of
  the five routes ADG-UI-EDGE-01 named (`/`, `/status`, `/identities`, `/demo`) and the
  investigation of the reviewer path.
* **When.** On full loads only. Client-side navigations never showed it on either build: they
  do not hydrate a document. With `chunk40`, all 40 reports came while `document.readyState`
  was `loading`, as ADG-UI-EDGE-01 found. With `rate`, 20 came at `loading` and 30 at
  `complete`. The state is read when React *reports* the recoverable error, which can come
  after the mismatch itself, once the stream has finished.
* **Why the direct and l100 runs are not the decision.** On loopback, Next 15 showed #418 once in
  210 loads (DEMO-PUBLIC-01 saw it once in five walkthroughs), and delaying the API (l100) did
  not reproduce it: slow server data delays the whole response, not the stream relative to the
  JavaScript. They are reported, but the slow-origin conditions, where `main` fails in every
  run, are what discriminates.
* **The confound the `rate` condition removes.** Next 15 flushes these pages in 11–121 chunks
  and Next 16 in 3–9, so a per-chunk delay slows Next 15's stream more. Under `rate` both builds
  receive the same bytes at the same pace, and the result is the same: 50 of 450 against 0 of
  585.

### 10.5 Strength of the result

* At `main`'s slow-origin rate (10.0%), 1,170 clean loads would occur by chance with probability
  about 10⁻⁵³. Per condition: `rate` 0 of 585 against 11.1%, about 10⁻³⁰; `chunk40` 0 of 585
  against 8.9%, about 10⁻²⁴.
* 95% upper bound on Next 16's per-load #418 rate (rule of three): **0.26%** under the
  slow-origin conditions, 0.19% over all 1,590 loads.
* The mechanism is in the shipped bytes (§6), and the candidate's hosted gate found 0 in 224
  reloads on staging; the parallel integration found 0 in 1,344 with its own harness (§3).

**Ready status for #418:** zero #418 occurrences and zero new application console errors on the
integrated build, across 46 runs, with a control failing in every slow-origin run.

---

## 11. Contracts

| | |
| --- | --- |
| Explanation contract | **`1.1`** (`CONTRACT_VERSION` in `backend/app/api/caching.py`; `"1.1"` in `derived/access-explanation.schema.json`). Not reverted |
| Regeneration | `python -m app.contracts.openapi` (both snapshots) and `python -m app.contracts.derived` (three schemas) from the integrated source: `git diff --exit-code docs/contracts` clean. **Byte-identical** to the committed `openapi.json`, `openapi.demo.json` and `derived/*.schema.json` |
| Backend contract tests | `tests/contracts`: **611 passed, 109 skipped** (equal to ADG-DEMO-PUBLIC-MERGE-01) |
| Frontend contract tests | `contracts.test.ts` + `explanation-contract.test.ts`: **562 passed** |

No generated file conflicted, because no generated file was taken from the Next 16 line.

---

## 12. Migrations

```
$ alembic heads
0018_risk_evaluation_basis (head)
```

One head. `database/` is byte-identical to `main`: 20 revision files, `0001_baseline` to
`0018_risk_evaluation_basis`, whose historical branches (three `0008`s, two `0013`s) rejoin at
`0012_merge_concurrent_phases` and `0014_merge_alerts_and_reviews`. There is no
`0018_graph_relationships` or any other GRAPH revision. The Next 16 line carries no migration of
its own (`66ca201` touches none, and the 16 commits below it were not imported). The demo
reset (§14) ran `alembic upgrade head` on a fresh `adg_demo` from this tree.

---

## 13. Full frontend validation

From a clean dependency state (`rm -rf node_modules .next`, then `npm ci`) in the session tree,
on the code head `3524a02`; logs in the evidence directory:

| Gate | Result |
| --- | --- |
| `npm ci` | 466 packages, 12 s, from the lockfile; `next` 16.4.1, `react`/`react-dom` 19.3.0, `eslint-config-next` 16.4.1 |
| `npm run lint` | **passed**, 0 problems |
| `npm run typecheck` | **passed** |
| `npm run test` | **38 files, 1,361 passed** — equal to `main` (1,361, run in this session on a `main` worktree) and to DEMO-PUBLIC-01 |
| `npm run build` | **passed**: `Next.js 16.4.1 (webpack)`, compiled in 9.1 s, TypeScript in 5.2 s, 39 routes, no warnings |

The test log carries four React `prefetch={false}` attribute warnings on stderr; `main`'s run
carries the same four, so they predate the upgrade.

Representative sets (all equal to DEMO-PUBLIC-01 §12):

| Set | Result |
| --- | --- |
| README frontend representative (`system-mapping`, `investigation-page`, `risks-page`, `compare-page`, `coverage`) | 121 passed |
| DEMO-01 UI: `system-mapping`, `explanation`, `explanation-views`, `identity` | 171 passed |
| DEMO-02 UI: `explanation-page` + `explanation-contract` / `investigation` + `investigation-page` + `demo-guide` | 112 / 68 passed |
| DEMO-03 UI: `coverage` / `compare-page` / `risks-page` + `risks` | 29 / 18 / 38 passed |

---

## 14. Backend and database validation

`backend/`, `database/` and `docs/contracts/` are byte-identical to `main`, so the full
backend suites were left to CI (§16), which runs them on this branch anyway. Everything below
ran here (Python 3.13.16, PostgreSQL 16.15 local, started with `pg_ctlcluster`):

| Gate | Result |
| --- | --- |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** (412 files) |
| `mypy app tests` | **passed** (411 source files) |
| Demo reset `python -m app.demo_mode reset` on a fresh `adg_demo` | exit 0 in 91 s; 300 scan runs; fingerprint **`228b9dce9b2a05d1cd9c0f190d0614f5a3a6e43efa95532d3e625c74f8f9efd0`**, equal to ADG-DEMO-02, -03 and DEMO-PUBLIC-01 |
| `python -m app.demo_mode verify` | exit 0, 23 probes |
| README representative unit command | **229 passed** |
| README representative PostgreSQL command (`ADG_RUN_SMOKE_TESTS=1`) | **58 passed** in 2m28s: `test_system_mapping_api`, `test_collection_coverage`, `test_comparison_bounds`, `TestTheSystemExplanation`, `TestTheAccessInvestigation`, `TestTheCoverageAndRecoveryDemo` |
| `tests/contracts` | 611 passed, 109 skipped |

---

## 15. Browser validation

### 15.1 Clean-build validation

| Step | Result |
| --- | --- |
| Fresh clone of the **pushed** branch from GitHub (`git clone --branch claude/adoring-lamport-1e22j9`) into an empty directory, at `0a65d91`; no `node_modules`, no `.next` | — |
| `npm ci` | 466 packages, 10 s |
| `npm run lint`, `typecheck`, `test`, `build` | **all passed**; 38 files / 1,361 tests; `Next.js 16.4.1 (webpack)`, compiled in 7.0 s; the clone's tree clean afterwards |
| `next start -p 3000` against the demo API | served; every reviewer flow below and gate phase 3 (§10) ran against it |

### 15.2 Reviewer flows

The three existing evidence scripts, **unchanged**, against the clean-clone Next 16 build, as
DEMO-PUBLIC-01 ran them against its fresh clone
([`walkthroughs/`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-evidence/walkthroughs/)):

| Script | Result |
| --- | --- |
| ADG-DEMO-02 walkthrough (`ADG-DEMO-02-evidence/browser-walkthrough.script.js`) | **29 / 29** |
| ADG-DEMO-03 walkthrough (`ADG-DEMO-03-evidence/browser-walkthrough.script.js`), three runs | **39 / 39, 39 / 39, 39 / 39** |
| DEMO-PUBLIC-01 documentation walkthrough (`DEMO-PUBLIC-01-evidence/docs-walkthrough.script.js`) | **39 / 39** |

For the same scripts on Next 15.5.25, DEMO-PUBLIC-01 recorded DEMO-02 29/29, DEMO-03 38/39
(its console check caught #418), 39/39 and 39/39, and the documentation walkthrough 39/39. Here
every run passed every check, including each script's own console check.

**Access investigation.** *Why does Fatima Gordon have access to
`\\NM-ENG-FS01\Engineering\Projects\Apollo`?* — **granted (Access present), Modify,
`at_least`, over the network (SMB)**. The direct NTFS route (explicit; removing it changes
nothing), the share route through `ENG-Mobile`, the per-hop provenance (collector and scans,
opened and closed by keyboard with a visible focus outline), the two missing-evidence items
(neither affecting the conclusion), the orphaned SID kept visibly unresolved, `Proj-Zephyr-RW`
named, seven questions answered, a summary saying the uncertainty does not take access away,
three next steps, *Inspect the SID* reaching the ADG-DEMO-01 unresolved banner, the removal
analysis and the link back. SYSTEM over a share: *not determined*, mapping *ambiguous*, never
"No access"; SYSTEM locally: present, mapping confirmed.

**Coverage and risk.** The totals are not called totals while a source is unavailable ("These
counts are not exhaustive", an announced status region); the counts are still shown (3
critical, 8 high); the unavailable server's findings are held, not zeroed; the coverage line
gives a verdict in words and a symbol and names the snapshot; the incomplete-scopes disclosure
by keyboard; the unavailable source named with the age of its evidence, and `NM-FS-03` on the
collector page; the investigation reads only complete scopes and keeps Modify, `at_least`.

**Comparison and recovery.** The drill **times out** (⏱, announced, a live status per
recovery action, no partial list and no "nothing is different"); Diagnostics by keyboard name
the attempt, the 1 ms window and what was read; *Retry inside the standard 4-second window*
stops **incomplete** at a named boundary, names the first attempt and labels its list partial;
*Compare only folder permission entries* ends **complete, recovered**, with no recovery offered
and its differences presented: `timed_out → incomplete → recovered`.

**Public presentation.** The documentation walkthrough checks every label, route and number the
README and the demo guide cite — 39/39. The guide (`docs/demo/README.md` and the `/demo` page)
is `main`'s, unchanged: Walkthrough 1, the access investigation and its evidence ambiguity;
Walkthrough 2, collection coverage and risk interpretation; Walkthrough 3, the comparison
timeout and recovery.
No horizontal scroll at 390 px on `/risks`, the investigation and the three comparison
states, and none on any of the gate's 230 Next 16 loads at 390 px.

**Console.** The only console message in any walkthrough is the pre-existing `/favicon.ico`
404, which DEMO-PUBLIC-01 also recorded and excluded. No React error of any kind.

**Unresolved identities** (gate phase B, every run): *Inspect the SID* from the investigation
reaches `…-1108`'s page with "A run saw this SID and could not resolve it" (checked as a
wait condition in each of the 46 Next 16 runs). The never-recorded `…-99999` says "Not found —
Nothing is stored about '…-99999': no principal observation and no membership edge names it.
It may simply not have been collected yet.", word for word what `main` renders: no identity is
invented. (The page's "No run has described this SID" banner is for an SID that ACLs name but
no run described; this one is named nowhere.)

---

## 16. CI

| Run | SHA tested | Result |
| --- | --- | --- |
| [83](https://github.com/40ozs/adg/actions/runs/38779797058) | `0a65d91` (code head `3524a02` + the README cells) | **success, every job**, 17:44:11–18:17:47 UTC |

Run 83, by job:

| Job | Result |
| --- | --- |
| Frontend (lint, types, tests, build) — `npm install`, `lint`, `typecheck`, `test`, `build` on Node 22 | **success** (17:44:14–17:45:35) |
| Backend (lint, types, tests) — `ruff`, `mypy app tests`, `pytest -m "not smoke"` (the full unit suite), `pytest -m smoke` (the full PostgreSQL suite, Postgres 16 service) | **success**: lint and types 17:44:43–17:44:59, unit tests 17:44:59–17:47:59, database tests 17:47:59–18:17:44 |
| Collector (Pester), `windows-latest` | **success** (17:44:15–17:46:24) |
| Docker Compose configuration (`docker compose config -q`) | **success** |

The pass counts of the two backend suites were not extracted: the job log's download host is
not reachable from this environment. Both suites ran to completion and passed, on a backend
byte-identical to `main`'s, where run [81](https://github.com/40ozs/adg/actions/runs/38774844746)
(`32da1ae`) also passed every job.

The report commit itself changes only `docs/`; its CI run is given in the session's final
response.

---

## 17. Hosted status

**Not deployed, and hosted validation remains outstanding.** No repository-owned workflow
makes deployment part of this integration:

* `main` carries no Worker, Render or staging configuration (`frontend/cloudflare/`,
  `wrangler.jsonc`, `docker/Dockerfile.render` and `.github/workflows/cloudflare-staging.yml`
  exist only on the deploy line), and none was imported.
* `deploy/ADG-FREE-STAGING-01` (`3e26e4a`) already runs Next 16 — 16.4.0, `next build
  --webpack` — but on the combined staging tree (deploy line + UI waves). It contains neither
  the public-demo line (`32da1ae` is not its ancestor) nor 16.4.1. `deploy/ADG-FREE-EDGE-SSR-03`
  is still on Next 15.5.27.
* Compatibility: this integration uses the same bundler choice the Worker's edge-asset layout
  was validated with (`next build --webpack`), so the chunk layout assumption still holds. A
  hosted demo of the public-demo line would need the deploy line integrated first; that is a
  separate task.

A separate hosted-integration prompt is recommended (§19).

---

## 18. Limitations

Carried forward, not reopened:

* **Two integrations of this prompt.** `claude/beautiful-knuth-3zruji` (`aff70e8`) holds the
  same code on `744c2ef`, and its own `ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md`. Merging
  both would conflict on that file (add/add). Pick one (§19).
* **Hosted timing recreated, not measured.** The slow-origin proxy reproduces the race on one
  machine; the Worker/Render path was not exercised here. Hosted validation of this line is a
  separate task (§17).
* **One browser.** Only Chromium (Playwright's 1194 build) was run.
* **One-off harnesses.** The gate scripts are evidence, as the earlier demo scripts are; no
  browser-test framework was added.
* **Backend full suites in CI only.** The backend is byte-identical to `main`, so the full unit
  and PostgreSQL suites ran in CI (run 83, green), not locally; their pass counts were not
  extracted from the CI log (§16). The representative sets ran here.
* **`npm audit`:** 13 advisories remain (2 critical, 8 high, 3 moderate), mostly test and lint
  tooling (§6). No upgrade beyond Next 16.4.1 was made.
* **Turbopack** is not used; moving to it means re-checking the chunk layout the deploy line's
  edge assets depend on (as the candidate's report says).
* **Branch name.** The preferred `integration/ADG-NEXT16-POST-DEMO-01` was not used (§2).
* The accepted product limitations of ADG-DEMO-01 to -03, the unrun Docker Compose demo path
  and the open owner decisions (ADG-DEMO-PUBLIC-MERGE-01 §9–§10) are unchanged. That report's
  §9 entry *"React #418 and Next 16"* is what this integration addresses; the report itself was
  not edited.

---

## 19. Handoff

| | |
| --- | --- |
| Integration branch | `claude/adoring-lamport-1e22j9` |
| Starting `main` | `32da1aecbc83a195f9db0849ec4dd5f652cc8bde` |
| Code head | `3524a027ed7e90c7a33b0e16460012aff6aed99b` (Next.js 16.4.1) |
| Ending SHA | the commit that adds this report (a report cannot name its own commit; given in the session's final response and on the branch) |
| `main` | unchanged |

Below the report commit: `0a65d914c1e3f15224cf0a29faf14481ad827964` (the README cells; CI run
83 green), `3524a027ed7e90c7a33b0e16460012aff6aed99b` (16.4.1), `c054159` (16.4.0).

**For the merge-to-`main` prompt:**

1. Choose between this branch and `claude/beautiful-knuth-3zruji`. They carry the same code. This
   one starts from current `main` (a fast-forward of `main` to its head is possible while `main`
   stays at `32da1ae`); the other starts from `744c2ef` and would need a merge. Their reports
   share a path, so take one, and leave the other branch unmerged (or rename its report first).
2. Fast-forward `main` to this branch's ending SHA, after re-checking that `origin/main` is still
   `32da1ae` and CI on the ending SHA is green.
3. Then a separate hosted-demo task: integrate the deploy line onto the new `main` and rerun the
   hosted gates (the candidate's `smoke`, `browser`, `navigation`, instrumented and reload
   loops) on Next 16.4.1.

Nothing was deleted: the Next 16 source branches, `claude/beautiful-knuth-3zruji`, the GRAPH
branches and every other head are as they were. Repository visibility was not changed.
