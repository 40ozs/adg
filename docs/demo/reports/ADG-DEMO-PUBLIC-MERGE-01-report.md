# ADG-DEMO-PUBLIC-MERGE-01 — Merge the public demo line to `main`

## 1. Verdict

**`ADG_PUBLIC_DEMO_MERGED_WITH_LIMITATIONS`**

`main` was fast-forwarded from `1ab01a2` to `744c2ef`, the verified DEMO-PUBLIC-01 head, and
pushed without force. `main`'s tree is now identical to the tree that CI run 76 passed in
full. A visitor to the repository's default branch now sees the reviewer README, the demo
guide, the architecture reviewer view, the limitations and the demo reports.

This prompt changed no code, test or generated contract. The only file it adds is this
report, which is not on `main` (§7).

The limitations are the ones already accepted, carried forward rather than resolved (§9):

* the intermittent React #418 hydration error, whose fix is the unmerged Next 16 branch;
* the accepted product limitations of ADG-DEMO-01 to -03;
* the Docker Compose demo path was not run;
* the owner's publication decisions are still open (§10).

## 2. Source state

| | |
| --- | --- |
| Repository | `40ozs/adg`, public (`visibility: public`, `private: false`), default branch `main`. Visibility was not changed |
| Source branch | `claude/amazing-bell-q42027` |
| Candidate source SHA | `744c2efe89fb17f49cdfe640ebe5afd7d18253af`, the DEMO-PUBLIC-01 ending SHA. The branch had **not advanced**: after `git fetch` of every head, `origin/claude/amazing-bell-q42027` still pointed at it, so no newer commit needed classifying and nothing was substituted |
| Starting `main` | `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af` (merge of PR #2, 2026-10-12). It had **not advanced** since DEMO-PUBLIC-01. This container's local `main` was stale at `cc13ad2` and was first fast-forwarded to `origin/main` |
| Last application code SHA | `95f47ae`. The 4 commits after it touch 14 files, all `README.md` or under `docs/` |
| Working tree before merge | clean |

### What `main` gained

The fast-forward carries the whole validated demo line, not only its documentation:

| Prompt | Commits | Head |
| --- | --- | --- |
| ADG-DEMO-01, SYSTEM correctness | `4cb297b` … `c37ca55` | `c37ca55` (`claude/adg-system-explanation-correctness-4iaijw`) |
| ADG-DEMO-02, access investigation | `a7c9e9f` … `0cee1a4` | `0cee1a4` (`claude/serene-mayer-itiwyi`) |
| ADG-DEMO-03, coverage, timeout and recovery | `76c5f81` … `dd9f794` (code head `95f47ae`) | `dd9f794` (`claude/tender-curie-vw3iss`) |
| DEMO-PUBLIC-01, presentation | `b661417` … `744c2ef` | `744c2ef` (`claude/amazing-bell-q42027`) |

* 24 commits and no merge commits. 155 files: 89 added and 66 modified, with 33,788
  insertions and 285 deletions.
* By area: `backend/app` 26, `backend/tests` 15, `frontend/{app,components,lib}` 24,
  `frontend/tests` 12, `docs/` 75, plus `README.md`, `.env.example` (a commented
  `ADG_EXPECTED_COLLECTORS` example) and one migration.
* **The line adds migration `0018_risk_evaluation_basis`** (`down_revision =
  0017_deployment_marker`). Any database deployed from `main` needs `alembic upgrade head`.
  The demo reset and the test fixtures already run it.
* Branches this push newly contains: only the four heads above. Every other remote head
  that was contained was already in `1ab01a2` (§6).

### CI proving the source tree

| Run | Commit | Result |
| --- | --- | --- |
| [76](https://github.com/40ozs/adg/actions/runs/38689355101) | `744c2ef`, the candidate itself | **success, every job**, completed 06:28 UTC. Pending when DEMO-PUBLIC-01 ended; resolved here. The jobs were Backend (lint, type check, unit tests, and database tests 05:58:30–06:28:23), Frontend (lint, types, tests, build), Collector (Pester, `windows-latest`) and Docker Compose configuration |
| [75](https://github.com/40ozs/adg/actions/runs/38686166439) | `e535cd2`, same code and contract; differs only in the DEMO-PUBLIC-01 report | success, every job |
| [77](https://github.com/40ozs/adg/actions/runs/38751393080) | `744c2ef` on `main`, triggered by this push | **in progress** when this report was first committed. Frontend, Collector (Pester) and Docker Compose had passed; the backend job (database suite) was still running. The result is recorded below when it finishes |

`main` was pushed only after run 76 had passed on the exact commit.

## 3. Ancestry

**Fast-forward.**

* `git merge-base --is-ancestor origin/main 744c2ef`: true. `origin/main` (`1ab01a2`) is the
  merge base, and `744c2ef..origin/main` is empty.
* Integration: `git merge --ff-only 744c2efe89fb17f49cdfe640ebe5afd7d18253af` on `main`.
* No merge commit, squash or rebase, and no history rewritten.
* `main^{tree}` equals `744c2ef^{tree}`: both are `5e3722928a9ca06d5e512314a28d3c4cb586a90d`.
* Immediately before the push, `origin/main` was fetched again. It was still `1ab01a2`, still
  an ancestor, so the push was an ordinary fast-forward (`1ab01a2..744c2ef main -> main`).

No reconciliation was needed, so no trial branch was made.

## 4. Public-readiness recheck

This is a focused check of what `main` gained (`1ab01a2..744c2ef`), not a new audit. The
DEMO-PUBLIC-01 scan (§10 of [its report](DEMO-PUBLIC-01-report.md#10-synthetic--public-scan))
covered the tree, all 143 commits and every branch up to `e535cd2`. The only change since
then, `744c2ef`, edits that report alone.

| Check | Result |
| --- | --- |
| Private keys (PEM headers) | none |
| Cloud and SaaS tokens: `AKIA…`, `ghp_`/`gho_`/`github_pat_`, `xox?-`, `sk-…`, `AIza…` | none. The one `github_pat_` hit is the DEMO-PUBLIC-01 report listing the patterns it searched for |
| JWTs, `Authorization`/`Cookie` headers, bearer strings | none |
| Connection strings with credentials | only the documented local default `adg_dev_password` (README, `.env.example`) and the CI throwaway `adg_ci_password` (ADG-DEMO-03 measurement scripts, as in `ci.yml`) |
| `"token": "<32 hex>"` values (13) | collection-basis tokens: the cache identity of collected state (`X-ADG-Collection-Basis`, `backend/app/api/caching.py`). They appear in contract examples, demo evidence and frontend test factories. Not credentials |
| Secret-bearing files (`.env`, `.pem`, `.key`, `.pfx`, `.p12`, dumps, logs) | none, in the tree or ever added in the range. Only `.env.example` and `frontend/.env.example` are tracked. The local `.env` made for the gates is git-ignored and was never staged |
| URL hosts | `localhost`, `127.0.0.1`, `testserver`, `t`/`x` (test URLs), `react.dev`, `github.com/40ozs/adg` (CI run links) |
| Email addresses | `@nullmeridian.info` only (synthetic) |
| `onrender.com` / `workers.dev` | named only in the DEMO-PUBLIC-01 report, describing the unmerged deploy branches. No staging hostname is on `main` |
| Owner's personal name or email in file content | none (it is in commit metadata, §10) |
| Absolute paths | `/home/user` only: the generic container clone path |
| Employer or confidentiality markers | only the synthetic-data declaration and the reports' scan descriptions |
| Screenshots | 12 PNGs in the range (ADG-DEMO-02 and -03 evidence), last changed in `d4a7a19`, which predates the DEMO-PUBLIC-01 scan that viewed all 18. Two were viewed again here: synthetic Null Meridian data, the `Development Auditor` account and the *Demo environment* pill only |
| README synthetic-data statement | present and accurate: *Synthetic data only…* opens the README, and the demo guide repeats it. The UI's demo indicators were verified in DEMO-PUBLIC-01 |

No new sensitive material. Nothing needed sanitizing, so nothing blocked the merge.

## 5. Generated contract

| | |
| --- | --- |
| Explanation contract version | **`1.1`**: `CONTRACT_VERSION: Final = "1.1"` in `backend/app/api/caching.py`, and `"const": "1.1"` in all three derived schemas |
| OpenAPI | `python -m app.contracts.openapi` and `python -m app.contracts.derived` were run on `744c2ef` before the merge. The SHA-256 of `openapi.json`, `openapi.demo.json`, the three derived schemas and the three examples matched the committed files, and `git status` stayed clean. **Byte-identical, no stale contract** |
| Backend contract tests | `tests/contracts`: **611 passed, 109 skipped**, the same as DEMO-PUBLIC-01 and ADG-DEMO-03 |
| Frontend contract test | `tests/contracts.test.ts`: **465 passed** |

No documentation commit in the range changed a docstring, route description or schema text.
The DEMO-03 stale-snapshot slip (fixed in `95f47ae`) did not recur.

## 6. Post-merge gates

These ran on local `main` at `744c2ef` after the fast-forward and before the push. Python
3.13.16, Node 22.22.0 and PostgreSQL 16 ran in one Linux container, from a fresh
`pip install -e ".[dev]"` and `npm ci`.

The full unit and PostgreSQL suites were **not** rerun. The tree is byte-identical to the
one CI run 76 passed (full unit suite and the ~30-minute database suite), and no code
changed. Run 77 repeats them on `main` (§2).

### Documentation

| Check | Result |
| --- | --- |
| Links: repository-relative Markdown links and GitHub heading anchors, over every tracked `*.md` (140 files, including README, `docs/demo/`, `docs/architecture/`, the reports) | **562 links, 0 broken** (10 external links counted, not fetched) |
| README representative tests: every backend node ID | **22 of 22 resolve** (`pytest --collect-only`: 70 cases with parametrization, no errors) |
| README representative tests: frontend test names | **3 of 3 found** in their files |
| Required reviewer documents present on `744c2ef` | `README.md`, `docs/demo/README.md`, `access-investigation.md`, `coverage-and-recovery.md`, `docs/architecture/system-overview.md`, and the ADG-DEMO-01/02/03 and DEMO-PUBLIC-01 reports and evidence |

### Contract

As §5: regeneration byte-identical, 611 passed / 109 skipped, and 465 passed.

### Representative regressions

| Demo | Set | Result |
| --- | --- | --- |
| all | README *Representative unit tests* command | **229 passed** |
| all | README *Representative PostgreSQL tests* command: `test_system_mapping_api`, `test_collection_coverage`, `test_comparison_bounds`, `TestTheSystemExplanation`, `TestTheAccessInvestigation`, `TestTheCoverageAndRecoveryDemo` (two full Null Meridian resets) | **58 passed** in 2m59s (14:22:57–14:26:00 UTC) |
| all | README frontend command: `system-mapping`, `investigation-page`, `risks-page`, `compare-page`, `coverage` | **121 passed** (5 files) |
| 01, SYSTEM ambiguity and correctness | `test_system_explanation_regression` + `test_system_mapping` + `test_resolver` | **145 passed** |
| 01 | UI: `system-mapping`, `explanation`, `explanation-views`, `identity` | **171 passed** |
| 02, Fatima → Apollo | `test_investigation` + `test_unidentified_trustees` | **102 passed** |
| 02 | UI: `investigation`, `investigation-page`, `demo-guide`, `explanation-page`, `explanation-contract` | **180 passed** |
| 02 | `TestTheAccessInvestigation` (canonical routes, inherited route, hop provenance, `at_least`, open questions) | in the 58 above |
| 03, collection coverage and risk interpretation | `tests/domain/test_coverage.py` | **26 passed** |
| 03, comparison timeout and recovery | UI: `coverage`, `compare-page`, `risks-page`, `risks` | **85 passed** |
| 03 | `test_collection_coverage`, `test_comparison_bounds`, `TestTheCoverageAndRecoveryDemo` (drill `timed_out` → `incomplete` → complete, recovered) | in the 58 above |

Every count equals the one DEMO-PUBLIC-01 recorded for the same set.

### Static checks and build

| Gate | Result |
| --- | --- |
| `ruff check .` | passed |
| `ruff format --check .` | passed (412 files) |
| `mypy app tests` | passed (411 source files) |
| `npm run lint` | passed |
| `npm run typecheck` | passed |
| `npm run build` | passed. `/demo`, `/access/investigate`, `/risks` and `/changes/compare` are server-rendered on demand (`ƒ`) |

After all gates, `git status` was clean.

## 7. Final `main` state

| | |
| --- | --- |
| Local `main` | `744c2efe89fb17f49cdfe640ebe5afd7d18253af` |
| `origin/main` (after `git fetch`) | `744c2efe89fb17f49cdfe640ebe5afd7d18253af` |
| `git ls-remote origin HEAD refs/heads/main` | both `744c2efe89fb17f49cdfe640ebe5afd7d18253af` |
| Equal | **yes** |
| Push | `git push -u origin main`: `1ab01a2..744c2ef  main -> main`. Not forced. GitHub reports `pushed_at` 2026-10-15T14:26:18Z |

This report is committed on `claude/intelligent-fermi-nxp2jw`, one commit on top of
`744c2ef`, and **not** on `main`. That keeps `main` exactly the tree CI run 76 proved. The
branch merges into `main` as a fast-forward whenever the owner wants the report there.

## 8. Reviewer experience

The default branch now serves the reviewer material. This was checked through GitHub, not
only locally:

* The repository API reports `default_branch: main`. `raw.githubusercontent.com/40ozs/adg/HEAD`
  serves a `README.md` whose blob is `458821c3…`, identical to `main:README.md`.
  `docs/demo/README.md` (`8d39015e…`) is identical too.
* The served README contains:
  * the synthetic-data statement (*Synthetic data only…*);
  * *What to look at first*;
  * *Code to review*;
  * *Representative tests*;
  * the architecture link (`docs/architecture/system-overview.md#the-reviewer-view-from-collection-to-screen`);
  * the demo guide link;
  * *Known limitations*;
  * *Demo reports and evidence*.
* `docs/demo/reports/` on the default branch lists the ADG-DEMO-01, -02, -03 and
  DEMO-PUBLIC-01 reports and their evidence directories.

DEMO-PUBLIC-01's publication limitation 6 (*`main` still has the pre-DEMO README*) is
resolved.

## 9. Known limitations

### React #418 and Next 16

* DEMO-PUBLIC-01 saw an intermittent React #418 hydration error in 1 of 5 browser runs on
  Next 15.5.25. Every content check in that run passed.
* The fix is the Next 16.4.0 upgrade on `fix/ADG-NEXT16-REACT418-01`, also carried by
  `integration/ADG-NEXT16-UI-02`. It was **not** integrated here. It conflicts with this line
  in `frontend/app/identities/principal/page.tsx`, and it needs its own controlled workflow.
* No browser run was repeated in this prompt.

### Accepted product limitations

These come from ADG-DEMO-01 to -03, unchanged, and are disclosed in the README and the demo
guide:

* expected collectors are declared per kind, not per server;
* "stale" is not an age threshold;
* the page-wide collection-health banner was not redesigned;
* an estate-wide comparison cannot finish in one interactive request;
* recovery is by narrowing scope;
* bounds use in-process checkpoints, not a job system;
* remote-database latency was modeled, not measured;
* browser validation uses one-off scripts;
* SYSTEM over a share is never resolved to a computer account;
* the AD and SMB collectors have never met a real domain.

### Environment limitations

* The Docker Compose demo path is still unrun. CI validates only the base Compose file.
* Gates ran on one Linux container; macOS and Windows are unexercised.
* Collector Pester tests and `docker compose config` ran in CI (run 76), not locally.
* There is no hosted demo for this line.

### Parallel work, deliberately not integrated

Every head below is still outside `main`, with its number of commits not on `main`:

| Branch | Ahead |
| --- | --- |
| `feature/ADG-GRAPH-01A-domain-model` | 28 |
| `feature/ADG-GRAPH-01B-relationship-derivation` | 31 |
| `feature/ADG-GRAPH-01C-path-engine` | 39 |
| `feature/ADG-GRAPH-01C-path-engine-claude` | 35 |
| `fix/ADG-NEXT16-REACT418-01` | 19 |
| `integration/ADG-NEXT16-UI-02` | 26 |
| `fix/ADG-UI-COMPACT-IDENTITY-01` | 20 |
| `integration/ADG-UI-EDGE-01` | 17 |
| `deploy/ADG-FREE-STAGING-01` | 26 |
| `deploy/ADG-FREE-EDGE-SSR-03` | 9 |
| `claude/adoring-cannon-7gh2pf` | 38 |
| `claude/compassionate-hamilton-3sh5db` | 32 |
| `claude/wizardly-gates-6yjdxp` | 1 |

The integration hazards recorded in
[DEMO-PUBLIC-01 §14](DEMO-PUBLIC-01-report.md#14-parallel-branches-and-integration-hazards)
now apply against `main`. They were re-checked against the new `main` with write-free trial
merges (`git merge-tree --write-tree`), and the result is the same:

* 11 branches conflict in `frontend/app/identities/principal/page.tsx`. They include both
  Next 16 heads, and `integration/ADG-NEXT16-UI-02` contains `fix/ADG-NEXT16-REACT418-01`;
* 3 also conflict in `docs/contracts/v1/openapi.json`: `adoring-cannon`,
  `compassionate-hamilton` and `GRAPH-01C-path-engine-claude`. Regenerate it, never pick a
  side;
* 2 merge cleanly: `wizardly-gates` and `deploy/ADG-FREE-EDGE-SSR-03`;
* the GRAPH branches add a second Alembic `0018` (`0018_graph_relationships`) on the same
  parent as `0018_risk_evaluation_basis`, which is now on `main`. Integrating them needs a
  merge revision, not a renumbering.

## 10. Owner decisions still open

None was resolved here.

1. **License.** There is no `LICENSE` file, and none was added.
2. **Commit metadata and history.** The owner's name and personal email address, and
   `Claude-Session` trailers, are in commit metadata now on `main`. History was not
   rewritten.
3. **Staging hostnames.** `onrender.com` and `workers.dev` hostnames are on the unmerged
   `deploy/*`, `integration/*`, `fix/*` and three `claude/*` branches. They are not on `main`,
   and those branches were not touched.
4. **Synthetic domain.** `nullmeridian.info` is under a real TLD, and its registration is
   unverified. It was not renamed.
5. **npm advisories.** `npm audit` reports **16 (2 critical, 10 high, 4 moderate)**,
   unchanged from DEMO-PUBLIC-01:
   * critical: `vitest`, `tinypool`;
   * high: `vite`, the `eslint-config-next` / `@next/eslint-plugin-next` / `fast-glob` /
     `micromatch` / `braces` / `brace-expansion` chain, `postcss`, `source-map-js`, `sharp`;
   * moderate: `next`, `esbuild`, `vite-node`, `@vitest/mocker`.

   These are mostly test and build tooling. No upgrade was made.

## Recommended next step

1. Take the Next 16 / React #418 work (`fix/ADG-NEXT16-REACT418-01` →
   `integration/ADG-NEXT16-UI-02`) through its own controlled integration against the new
   `main`. Resolve `principal/page.tsx` by taking the newer structure and reapplying the
   `isUnresolved` banner, then re-run the browser checks.
2. Decide the open owner items in §10, starting with the license.
3. Optionally fast-forward `main` to `claude/intelligent-fermi-nxp2jw` to carry this report.
