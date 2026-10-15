# ADG-NEXT16-MERGE-01 — The validated Next.js 16 line, fast-forwarded onto `main`

## 1. Verdict

**`ADG_NEXT16_MERGED_WITH_LIMITATIONS`** — *provisional until CI run 89 on `main` completes (§12)*

`main` moved from `32da1ae` to **`0e770afdd872f5d0dd9b430ef8f7d238f7c9f1ff`**, the head of
`claude/gallant-turing-aj7rf1`, by `git merge --ff-only`. No merge commit, squash, rebase or
force-push. Every gate was re-checked from the repository before the merge (§3–§5). CI run
**89** on `main` at that exact SHA is recorded in §12.

The merge itself is complete. The limitations are documented ones, not defects of the merge:

* **README Node minimum.** `main`'s README and runbook still say Node "20 or later" / "20+";
  Next.js 16.4.1 requires `>=20.9.0`. The fix is a separate documentation-only commit,
  deliberately **not** on `main` in this task (§14).
* **No hosted demo runs this line** (§15).
* The integration's carried-forward limitations: hosted timing simulated on one machine,
  Chromium only (RECONCILE-01, "Limitations, carried forward").

---

## 2. Starting `main`

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| `origin/main` at start | **`32da1aecbc83a195f9db0849ec4dd5f652cc8bde`**, from `git fetch` and `git ls-remote` at the start of the task. Equal to the expected SHA |
| CI on it | run [81](https://github.com/40ozs/adg/actions/runs/38774844746): success |
| This container's local `main` | stale at `cc13ad2`. It was first fast-forwarded to `origin/main` (`32da1ae`), never used as a base before that |

---

## 3. Canonical source

| | |
| --- | --- |
| Branch | **`claude/gallant-turing-aj7rf1`** |
| SHA | **`0e770afdd872f5d0dd9b430ef8f7d238f7c9f1ff`**, from `git ls-remote` at the start. Equal to the approved SHA; the branch had not moved |
| What it is | ADG-NEXT16-RECONCILE-01: the validated integration (`claude/beautiful-knuth-3zruji` @ `aff70e8`) merged unchanged onto `32da1ae` as `1309146`, plus that report's commit |
| Reports read before acting | [`ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md`](ADG-NEXT16-POST-DEMO-INTEGRATION-01-report.md) (`READY_FOR_ADG_NEXT16_POST_DEMO_MERGE_WITH_LIMITATIONS`) and [`ADG-NEXT16-RECONCILE-01-report.md`](ADG-NEXT16-RECONCILE-01-report.md) (`READY_FOR_ADG_NEXT16_MERGE_WITH_LIMITATIONS`), both read from the candidate tree |
| Exact-head CI | run [88](https://github.com/40ozs/adg/actions/runs/38816074755) on `0e770af`: **completed, success**. Every job passed: Frontend (lint, types, tests, build); Collector (Pester, `windows-latest`); Docker Compose configuration; Backend (lint, type check, unit tests, database tests on PostgreSQL), each step `success` |

The five commits `main` gains, in order:

| SHA | Author | What |
| --- | --- | --- |
| `af94e2e` | 40ozs | `frontend: Next.js 16.4.0 and React 19.3, webpack build` (cherry-picked from `66ca201`) |
| `501c136` | Claude | `frontend: Next.js 16.4.1, the October 14 security release` |
| `aff70e8` | Claude | ADG-NEXT16-POST-DEMO-INTEGRATION-01 report and evidence |
| `1309146` | Claude | the RECONCILE-01 merge of `aff70e8` onto `32da1ae` (parents `32da1ae`, `aff70e8`) |
| `0e770af` | Claude | ADG-NEXT16-RECONCILE-01 report and evidence |

---

## 4. Fast-forward proof

Run before the merge, against freshly fetched refs:

```
$ git merge-base 32da1ae 0e770af
32da1aecbc83a195f9db0849ec4dd5f652cc8bde
$ git merge-base --is-ancestor 32da1ae 0e770af && echo ancestor
ancestor
$ git rev-list --left-right --count 0e770af...32da1ae
5	0
```

* Merge base = current `main`. Candidate **5 ahead, 0 behind**.
* `git diff --name-status 32da1ae 0e770af -- . ':!docs'` lists exactly four files, all
  `M`: `frontend/eslint.config.mjs`, `frontend/package-lock.json`, `frontend/package.json`,
  `frontend/tsconfig.json`. Everything else is under `docs/demo/reports/` (the two reports and
  their evidence).
* `git diff 32da1ae 0e770af -- database backend` is empty.

---

## 5. Merge method

```
git checkout main
git merge --ff-only origin/main                                  # cc13ad2 → 32da1ae
git merge --ff-only 0e770afdd872f5d0dd9b430ef8f7d238f7c9f1ff     # Updating 32da1ae..0e770af, Fast-forward
git rev-parse HEAD                                               # 0e770afdd872f5d0dd9b430ef8f7d238f7c9f1ff
```

**Pre-push integrity check** on the merged working tree:

| Check | Result |
| --- | --- |
| Working tree | clean (`git status --porcelain` empty); `git diff HEAD 0e770af` empty |
| Contract | `CONTRACT_VERSION: Final = "1.1"` |
| OpenAPI / derived schemas | regenerated in a detached worktree of `0e770af`: byte-identical (§10) |
| Alembic | one head, `0018_risk_evaluation_basis` (§11) |
| Framework | equal to the candidate (§7) |
| Reports and evidence | both reports and both evidence directories present (5 + 14 files) |

`origin/main` was fetched once more just before the push: still `32da1ae`. Then:

```
$ git push -u origin main
   32da1ae..0e770af  main -> main
```

A normal push; no `--force`. Afterwards `git fetch` and `git ls-remote` gave:

```
local main  = 0e770afdd872f5d0dd9b430ef8f7d238f7c9f1ff
origin/main = 0e770afdd872f5d0dd9b430ef8f7d238f7c9f1ff
```

---

## 6. Ending `main`

**`0e770afdd872f5d0dd9b430ef8f7d238f7c9f1ff`**: the exact SHA CI run 88 proved on the branch,
and CI run 89 runs on `main` (§12). This report is **not** on `main`; it is on the
documentation-only follow-up branch, so `main` stays the CI-proven integration SHA.

---

## 7. Framework state

Read from the merged tree and from a fresh `npm ci` (466 packages) in a worktree of `0e770af`:

| | |
| --- | --- |
| `next` | `^16.4.1`; **16.4.1** in the lockfile and installed. `engines.node`: `>=20.9.0` |
| `react`, `react-dom` | `^19.3.0`; **19.3.0** installed |
| `@types/react`, `@types/react-dom` | `^19.3.0` |
| `eslint-config-next` | `^16.4.1`; 16.4.1 |
| React bundled for the App Router | **`19.3.0-canary-278794d7-20261002`**, read from `next/dist/compiled/react` and `react-dom` production builds |
| Build | `"build": "next build --webpack"` |
| ESLint | flat config: `eslint.config.mjs` imports `eslint-config-next/core-web-vitals` and `/typescript` directly; no `FlatCompat` |
| TypeScript | `jsx: react-jsx`; `.next/dev/types/**/*.ts` included |
| `next.config.mjs` | `reactStrictMode: true`, `env: {}`; no `output`, `experimental` or deploy setting |
| Suspense / loading boundary | **none**: no `Suspense` in `frontend/app`, `components` or `lib`; no `loading`, `error`, `not-found`, `default`, `middleware` or `proxy` file |

---

## 8. Demo preservation

No application file changed between the validated tree and `main`, so the recorded results apply
as recorded. They were **not rerun** here: the task forbids code changes before the merge, and
the application tree is identical (RECONCILE-01 §6).

| Demo | Backend | UI | Browser | Key behavior |
| --- | --- | --- | --- | --- |
| **DEMO-01** | 145 | 171 | **15/15** | SYSTEM over a share: `ambiguous` / `uncertain`, no "No access" verdict; locally `confirmed` / `at_least`; unresolved SID shown without an invented name |
| **DEMO-02** | 102 | 180 | **29/29** | Fatima Gordon → Apollo: **granted**, **Modify**, **`at_least`** |
| **DEMO-03** | 26 | 85 | **39/39** | the comparison drill: **`timed_out → incomplete → recovered`** |

Source: RECONCILE-01 §8 (on `1309146`, the same application tree as `0e770af`). The
DEMO-PUBLIC-01 walkthrough there also passed 39/39 (total 122/122).

---

## 9. React #418

Not rerun: the application tree is unchanged since the measurement.

| Evidence | Result |
| --- | --- |
| RECONCILE-01 smoke: positive control (detector) | #418 on **62/62** full loads |
| RECONCILE-01 smoke: reconciled build, 40 ms stream delay | **0/96** |
| RECONCILE-01 smoke: Next 15 `main` (`32da1ae`), same container | **14/96** |
| POST-DEMO-INTEGRATION-01 large sample: old `main` (Next 15.5.25) | **67/576** |
| POST-DEMO-INTEGRATION-01 large sample: Next 16.4.1 | **0/1,344** (0/960 stream-delayed, 0/384 direct) |

The large-sample gate applies through tree identity: the `frontend` tree on `main` is the
`frontend` tree that gate measured (RECONCILE-01 §6.1).

---

## 10. Contract and OpenAPI

| | |
| --- | --- |
| Explanation contract | **`1.1`**: `CONTRACT_VERSION: Final = "1.1"` in `backend/app/api/caching.py`; `"const": "1.1"` in the three derived schemas |
| Regeneration | `python -m app.contracts.openapi` and `python -m app.contracts.derived`, in a worktree of `0e770af` with `pip install -e "backend[dev]"`. SHA-256 before and after: **identical**. `git status` stayed clean |
| Hashes | `openapi.json` `96c585aa…`, `openapi.demo.json` `b9404961…`, `access-explanation` `a8472281…`, `access-paths` `4263e5fa…`, `resource-impact` `9cac9fbe…`. These equal RECONCILE-01 §10 |

---

## 11. Alembic

| | |
| --- | --- |
| `alembic heads` (run on `0e770af`) | **one head: `0018_risk_evaluation_basis`** |
| `alembic history` | `0017_deployment_marker → 0018_risk_evaluation_basis (head)`; linear from the `0014_merge_alerts_and_reviews` merge point |
| GRAPH | **no GRAPH migration**. `0018_graph_relationships` is absent. (`0002_ad_graph.py` is the long-standing AD-graph migration already on `main`, not GRAPH-01.) |
| `database/` | unchanged from the starting `main` |

Deploying `main` needs no migration beyond what `32da1ae` already required.

---

## 12. CI on `main`

Run **89** ([38822842600](https://github.com/40ozs/adg/actions/runs/38822842600)), triggered
by the push of `main` to `0e770afdd872f5d0dd9b430ef8f7d238f7c9f1ff` (created 23:19:07 UTC):

**In progress at the time of this commit.** Docker Compose configuration: **success**
(23:19:12–23:19:15 UTC). Frontend, Collector (Pester) and Backend were running; the backend's
lint and type-check steps had passed. The final per-job results are recorded by a follow-up
commit to this report.

---

## 13. Parallel branches, excluded

Ancestry checked for every remote head after `git fetch` of all heads. None of these is an
ancestor of `main`; each was left untouched. The count is that head's commits not on `main`.

**Superseded for Next16 integration purposes** (kept, not deleted):

| Head | SHA | Not on `main` | What it is |
| --- | --- | --- | --- |
| `claude/kind-goodall-9mlk79` | `1a17e6c` | 2 | a parallel RECONCILE-01 attempt (reconcile `aff70e8` with `32da1ae`, plus a report) |
| `claude/adoring-lamport-1e22j9` | `f849042` | 4 | a parallel POST-DEMO integration replayed on `32da1ae`, with its own README Node commit |
| `fix/ADG-NEXT16-REACT418-01` | `04663bc` | 19 | the Next16 precursor, built on the staging deploy line and UI-UPDATE-01; only its framework commit `66ca201` reached `main`, as `af94e2e` |
| `integration/ADG-NEXT16-UI-02` | `3e26e4a` | 26 | that precursor plus COMPACT-IDENTITY-01 and SID truncation |

`claude/beautiful-knuth-3zruji` (`aff70e8`) **is** an ancestor of `main`, by design: it is the
validated integration line the RECONCILE-01 merge `1309146` brought in, unchanged. It was not
merged separately.

**Unrelated lines, still outside `main`:**

| Head | Not on `main` |
| --- | --- |
| `fix/ADG-UI-COMPACT-IDENTITY-01` | 20 |
| `integration/ADG-UI-EDGE-01` | 17 |
| `deploy/ADG-FREE-STAGING-01`, `deploy/ADG-FREE-EDGE-SSR-03` | 26, 9 |
| `feature/ADG-GRAPH-01A-domain-model`, `-01B-relationship-derivation`, `-01C-path-engine`, `-01C-path-engine-claude` | 28, 31, 39, 35 |
| `claude/adoring-cannon-7gh2pf`, `claude/compassionate-hamilton-3sh5db`, `claude/wizardly-gates-6yjdxp` | 38, 32, 1 |

No GRAPH, deploy/staging, UI-UPDATE or COMPACT-IDENTITY change is on `main`: outside `docs/`,
`32da1ae..0e770af` touches only the four framework files (§4).

---

## 14. README Node minimum

**`README_NODE_MINIMUM_UPDATE_REQUIRED`**, found after `main` was green:

| File | On `main` | Required |
| --- | --- | --- |
| `README.md:240` (Requirements) | `Node.js` \| `20 or later (CI uses 22)` | `20.9 or later` |
| `README.md:456` (Windows prerequisites) | `Node.js` \| `20+` | `20.9+` |
| `docs/operations/mvp-runbook.md:23` | `Node.js` \| `20+` | `20.9+` |

Source of the floor: `next@16.4.1` declares `"engines": { "node": ">=20.9.0" }` (lockfile and
installed package). CI (`node-version: "22"`) and `docker/Dockerfile.frontend` (`node:22-alpine`)
already satisfy it, so nothing that runs today is affected.

**Handled as a separate documentation-only commit**, not in the merged tree:
`bd29086` *Docs: Node.js 20.9 or later, the floor Next.js 16 sets* on
`claude/next16-merge-main-3y5zvb`, the branch that also carries this report. It changes only
those three table rows. No other branch was merged to fix it. `claude/adoring-lamport-1e22j9`
has its own README commit (`0a65d91`), which was not used.

---

## 15. Hosted demo

* **There is no hosted demo for this line.** `main` has one workflow (`.github/workflows/ci.yml`),
  no Worker, Render, `wrangler` or standalone-output configuration. This merge deployed
  nothing.
* The existing staging (`deploy/ADG-FREE-STAGING-01` @ `3e26e4a`) runs Next 16.4.0 plus the
  UI-UPDATE-01 and COMPACT-IDENTITY waves, per POST-DEMO-INTEGRATION-01 §14.2. It does not run
  `main` and was not touched or re-checked here.
* Hosting `main` is a separate, controlled change: bring the deploy line onto 16.4.1 and this
  tree, then run the hosted #418 gate.

---

## Handoff

1. **Bring the follow-up onto `main`** once its CI passes: `claude/next16-merge-main-3y5zvb`
   is `0e770af` plus two documentation-only commits (the Node minimum and this report). `main`
   fast-forwards to it.
2. Anyone with a Next 15 `node_modules`: `npm ci` in `frontend/`.
3. Then, as separate tasks: UI-UPDATE-01 / COMPACT-IDENTITY-01 against this line; the deploy
   line moved to 16.4.1 and the hosted #418 gate; GRAPH, which needs a merge revision for its
   second `0018`.
