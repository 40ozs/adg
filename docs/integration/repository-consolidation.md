# Repository consolidation

**Date:** 2026-09-15
**Workspace:** `C:\code\adg`
**Remote:** `https://github.com/40ozs/adg`
**Integration branch:** `integration/repository-consolidation`

Eight phases (07B, 08A, 08B, 09A, 09B, 10A, 10B, 10C) and a release audit were present and
uncommitted in one working tree, written by several agent sessions working concurrently.
This records how they became a dependency-ordered history, what was measured about the
result, and what is still not true about ADG afterwards.

Companion documents:

* [`phase-file-ownership.md`](phase-file-ownership.md) — which phase owns which file, and the
  evidence.
* [`phase-dependency-order.md`](phase-dependency-order.md) — the real dependency graph, and
  why eight phases could not become eight commits.
* [`migration-validation.md`](migration-validation.md) — the Alembic graph and both upgrade
  paths.

---

## Starting state

| | |
| --- | --- |
| Starting HEAD | `5537b164f4eb869b148824fd2f85fd19b1d8270d` — *Phase 7C: record the commit hash in the handoff* |
| Starting branch | **`master`** |
| Remote configured | **none** — `git remote -v` was empty |
| `git status --short` | **211 entries** |
| Untracked files | **226** (`git ls-files --others --exclude-standard`; `git status` collapses a directory to one entry, which is why the two counts differ) |
| Staged | nothing; `git diff --cached` was empty |
| Stashes | none |
| Worktrees | one |

### Two things the brief did not predict

1. **There was no `origin`.** The repository had never had a remote. `origin` was added
   pointing at `https://github.com/40ozs/adg`, which `gh repo view` confirmed exists, is
   public, and has default branch `main`.
2. **The local branch was `master`, and `origin/main` is unrelated to it.** `origin/main` is
   a single commit, `fde6e4a` *"Initial commit"*, containing a one-line `README.md` and
   nothing else. `git merge-base origin/main master` reports **no common ancestor**. The
   local branch carries the whole of ADG's history and has never been pushed.

   `master` was renamed to `main` (a local-only rename; nothing had been published), and
   `origin/main` was later joined with `--allow-unrelated-histories` rather than overwritten.
   Force-pushing over `fde6e4a` would have been the other way to reconcile it, and it is
   prohibited.

### Backup, before anything was changed

Two independent mechanisms, neither of them a stash:

1. **A full copy of the working tree** — including `.git`, every untracked file, and the
   build environments — at `C:\code\adg-backups\pre-integration-20260915` (961 MB).
2. **An archive of the untracked source alone** —
   `C:\code\adg-backups\untracked-source-20260915.tar.gz`, built from the
   `git ls-files --others` manifest and verified to contain **226 entries**, matching it.

Plus the preflight capture required by the brief, at `.tmp\integration-preflight\`:
`git-status.txt` (211 lines), `git-diff.patch` (49,174 lines), `git-diff-stat.txt`,
`head.txt`, `branch.txt`, `remotes.txt` (empty), `log.txt`, `untracked-files.txt`.

No `git reset --hard`, `git clean`, `git restore .` or `git checkout .` was used at any
point.

---

## Phase ownership

Full table in [`phase-file-ownership.md`](phase-file-ownership.md). The summary that decided
the commit structure:

**21 tracked files carry more than one phase's edits.** Each has a diff against `5537b16`,
so each phase's contribution can be read and selected. Splitting one is derivation.

**8 untracked files carry more than one phase's edits**, because a later phase extended an
earlier phase's new file in place:

| File | Phases |
| --- | --- |
| `backend/app/governance/{model,repository,service}.py` | 10A + 10B |
| `backend/app/api/governance.py` | 10A + 10B |
| `backend/app/domain/governance.py` | 10A + 10B + 10C |
| `backend/tests/governance/test_schema_vocabulary.py` | 10A + 10B + 10C |
| `backend/app/repositories/risk.py` | 8A + 8B |
| `docs/architecture/simulation.md` | 9A + 9B |

For these there is **no diff, and no earlier version anywhere** — not in git, not on disk,
not in either backup. Producing 10A's `repository.py` would mean deleting six methods and
presenting the result as what a session wrote, with nothing able to check it. The brief names
that as a stop condition, so it was not done.

### Resolution decisions

| Decision | Why |
| --- | --- |
| **Phase 7B is committed on its own.** | It shares no untracked file with any other phase, and its migration is a *parent* of the entangled group rather than a child. Every shared tracked file it touches was separated line by line. |
| **Phases 8A–10C are one commit.** | Forced twice over: the untracked entanglement above, and a cycle in the migration graph once the entangled pairs are grouped (dependency document §4.2). |
| **The release audit is committed last, on its own.** | Its own handoff asked for exactly that. Its 23 files are separable; the five it shares with in-flight phases were separated by hunk and by sentence. |
| **No revision was renumbered and no migration collapsed.** | Prohibited, and it would break databases already at those revisions. |

---

## Dependency graph

The order the commits actually take, derived from Alembic parents and from module imports
rather than from the phase numbers:

```
5537b16  (baseline, head 0008_change_feed_index)
   │
   ├── Phase 7B          0008_incremental_collection
   │
   ├── Phases 8A-10C     0008_governance_model, 0009_risk_findings,
   │                     0011_simulation_overlays, 0012_merge_concurrent_phases,
   │                     0013_access_review_workflow, 0013_alert_pipeline,
   │                     0014_merge_alerts_and_reviews, 0015_remediation_change_plans
   │
   └── Release audit     (no migration)
```

Inside the second commit the true order is
`8A → 10A → 9A → 9B → 10B → 8B → 10C`, and it is *not* the tree the brief proposed: 8B's
`0014_merge_alerts_and_reviews` names **10B's** `0013_access_review_workflow` in its
`down_revision`, so the risk lineage depends on the governance lineage as well as the other
way round.

---

## Commit map

| Unit | SHA | Message | Files | Migrations added |
| --- | --- | --- | --- | --- |
| Phase 7B | `b3d4a46` | Phase 7B: add incremental collection and reconciliation | 71 | `0008_incremental_collection` |
| Phases 8A–10C | `401c988` | Integrate phases 8A-10C: risk, simulation, governance and remediation planning | 226 | eight, listed above |
| Release audit | `c6a0ee0` | Release audit: fix correctness, security, benchmark and operational findings | 23 | none |
| Integration docs | *(see below)* | Document the repository consolidation | 4 | none |

### What was verified about commit `b3d4a46` in isolation

A worktree was created at that commit with its own virtual environment, so the check could
not accidentally import the canonical tree's code:

| Check | Result |
| --- | --- |
| `app.models.schema.metadata` table count | **18** — the 16 at `5537b16` plus `scan_run_checkpoints` and `collector_checkpoints`, which is the evidence that 7B's half of `schema.py` was separated correctly |
| `.\scripts\backend-test.ps1` (hermetic) | **4,572 passed, 10 skipped, 736 deselected · exit 0** |
| `alembic upgrade heads` | applies both branches · exit 0 |
| `alembic upgrade head` | **fails — two heads.** See below |

**The one place a commit here is not independently green.** Phase 7B's migration branches off
`0007_history_model`, the same parent Phase 7C had already taken, so at `b3d4a46` the graph
has two heads and `alembic upgrade head` refuses to choose. `tests/db/conftest.py` at that
commit runs `upgrade head`, so its **database suite cannot build a schema**. The one-line fix
(`head` → `heads`) is Phase 10A's, and backdating it into a commit labeled 7B to improve a
number would be untrue. The graph returns to one head at `401c988`, where 9A's
`0012_merge_concurrent_phases` merges the four branches.

### A finding about the baseline, made while checking this

`docs/contracts/v1/openapi.json` is generated. The copy committed at `5537b16` was
regenerated by Phase 7C from the shared working tree, and it publishes **15 governance paths
that the application at that commit does not serve** — Phase 10A's, then uncommitted — as
well as Phase 7B's response schemas. So
`tests/contracts/test_openapi_snapshot.py::test_the_snapshot_is_current` **was already
failing at the baseline commit**. Phase 7B's handoff described this inconsistency but named
only its own half of it.

Both `b3d4a46` and `401c988` regenerate the snapshot from their own application, which is what
each phase did in its own tree.

---

## Release-audit commit

**SHA:** `c6a0ee0` · 23 files (17 changed, 6 added), matching the audit handoff's own list
exactly.

| Finding | Included | Regression test |
| --- | --- | --- |
| C-1 `GET /api/v1/changes/compare` returned 422 | `backend/app/changes/classify.py` | `tests/changes/test_classify.py` (the test that only appeared to cover it, split into two that do) and `tests/db/test_change_comparison.py` at the service and at the API |
| S-1 contract specified no authentication | `docs/contracts/collector-protocol.md` | documentation; none |
| P-1 benchmark could not run, truncated the test database | `tests/benchmarks/access_benchmark.py` | `tests/benchmarks/test_access_benchmark_harness.py::TestItStillRunsEndToEnd` |
| S-2 signing key had no minimum length | `backend/app/config.py` (`MIN_SIGNING_KEY_LENGTH = 32`) | `tests/test_config.py::TestTheChangePlanSigningKey` |
| Q-1 seven mypy errors | `backend/app/changes/{impact,rules}.py` | the lint gate |
| O-1 container stack missing later-phase settings | `docker-compose.yml` | none |
| O-2 runbook named 3 of 8 roles, no restore | `docs/operations/mvp-runbook.md` | none |
| S-3 mutating web routes rely on `SameSite` | **not included — accepted and open** | — |

S-3 remains open deliberately. Adding an `Origin` assertion to two working routes is
hardening rather than defect repair, and folding it into a consolidation would be exactly the
unrelated change the brief prohibits.

### How the audit was separated from the phases

Five of the audit's files were also modified by phases 8A–10C. Its edits were held back while
`401c988` was built and restored afterwards:

| File | What was held back |
| --- | --- |
| `backend/app/config.py` | the `MIN_SIGNING_KEY_LENGTH` constant and the `_require_a_strong_signing_key` validator |
| `.env.example` | the four-line 32-character rule |
| `SECURITY.md` | the minimum-length sentence the audit appended to Phase 10C's bullet |
| `README.md` | the `docs/release/` line and the "before deploying it anywhere that matters" paragraph |
| `docs/contracts/collector-protocol.md` | the *Authenticating every request* section, and the 401/403 line in the §9 example |

After `c6a0ee0` the working tree was compared byte for byte against the pre-integration
backup and found identical apart from line endings (below) and the new `docs/integration/`
directory.

---

## A note on line endings

The repository's `.gitattributes` sets `* text=auto eol=lf`, with `eol=crlf` for PowerShell.
Much of the uncommitted working tree had been written with CRLF regardless, so when git
checked those files out during the rebase it wrote them as the attributes require.

`diff -r --strip-trailing-cr` between the working tree and the pre-integration backup reports
**no differences** other than `docs/integration/`. Nothing was lost; the tree was brought into
conformance with a policy the repository already declared.

---

## Main merge

| | |
| --- | --- |
| Integration branch | `integration/repository-consolidation` |
| Pre-merge `main` | `5537b164f4eb869b148824fd2f85fd19b1d8270d` |
| Integration merge | `cf38f96` — *Merge the repository consolidation into main* (`--no-ff`) |
| Unrelated-history merge | `6365308` — *Join the GitHub repository's initial commit* |

The second merge is the one the brief did not anticipate. `origin/main` (`fde6e4a`) had no
common ancestor with this history, so `git pull --ff-only` could not apply and a plain merge
refused. It was joined with `--allow-unrelated-histories`, resolving the one add/add conflict
(`README.md`) in favor of this repository's README over GitHub's placeholder. After it,
`git merge-base --is-ancestor origin/main main` succeeds, so the push is an ordinary
fast-forward rather than anything that overwrites the remote.

```
*   6365308  Join the GitHub repository's initial commit
|\
| * fde6e4a  Initial commit                       (origin/main)
*   cf38f96  Merge the repository consolidation into main
|\
| * c6a0ee0  Release audit
| * 401c988  Integrate phases 8A-10C
| * b3d4a46  Phase 7B
|/
* 5537b16  Phase 7C: record the commit hash in the handoff
```

---

## Clean-main validation

A worktree was created from `main` **after** both merges and given a **fresh environment** —
a new virtual environment, an editable backend install, and `npm install` (453 packages) — so
nothing could resolve back to the canonical tree's build artifacts. Verified before running
anything: `inspect.getfile(app.models.schema)` resolves inside the worktree, and the metadata
declares 39 tables.

```powershell
git worktree add --detach C:\code\adg-worktrees\release-validation main
cd C:\code\adg-worktrees\release-validation
.\scripts\bootstrap.ps1
```

`bootstrap.ps1` writes `.env` from `.env.example`, whose `ADG_DATABASE_URL` equals the
built-in `DEV_DATABASE_URL`, so the checkout needed no hand configuration.

| # | Check | Command | Result |
| --- | --- | --- | --- |
| 1 | Backend environment | `.\scripts\bootstrap.ps1` | fresh venv created, backend installed editable · exit 0 |
| 2 | Frontend install | (same script) | 453 packages · exit 0 |
| 3 | PostgreSQL test database | container `adg-db-1`, `postgres:16-alpine` | reachable |
| 4 | Migrations from zero | `alembic upgrade head` on an empty `adg_cleanmain` | **17 revisions, 3.14 s**, head `0015_remediation_change_plans` · exit 0 |
| 5 | Backend suite | `.\scripts\backend-test.ps1 -Smoke` | **6,772 passed, 10 skipped, 1 xfailed, 0 failed**, 27 m 02 s · exit 0 |
| 6 | Backend lint and types | `.\scripts\backend-lint.ps1` | ruff clean · format clean (362 files) · mypy clean (361 files) · exit 0 |
| 7 | Collector | `.\scripts\collector-test.ps1` | **1,050 passed, 0 failed, 0 skipped** · exit 0 |
| 8 | Frontend | `.\scripts\frontend-check.ps1`, then `npm run test` for the counts | lint, types and build clean; **31 files, 1,126 tests passed** · exit 0 |
| 9 | Schema parity | `pytest tests/db/test_schema.py tests/db/test_remediation_schema.py` | **45 passed** |
| 10 | OpenAPI snapshot | `pytest tests/contracts/test_openapi_snapshot.py` | **3 passed** |
| 11 | Access benchmark | `python -m tests.benchmarks.access_benchmark --sizes 10 100 400 --repeat 10` | ran; numbers below · exit 0 |
| 12 | Release benchmark | `python -m tests.benchmarks.release_benchmark --sizes 10 100 400 --repeat 8` | ran; numbers below · exit 0 |
| 13 | No-mutation suite | `pytest tests/remediation/test_no_write_path.py test_executor.py test_isolation.py` | **143 passed** |
| 14 | Production startup guards | `Settings(...)` constructed with each misconfiguration | all refused — see below |
| 15 | Remediation remains disabled | the factory and the role table | `DisabledRemediator`; **no role grants `remediation:execute`**; lab mode refused in production |

### Item 14, in full

| Misconfiguration | Outcome |
| --- | --- |
| `ADG_AUTH_MODE=development` with `ADG_ENVIRONMENT=production` | refused at construction |
| `ADG_DATABASE_URL` still the development default in production | refused at construction |
| `ADG_REMEDIATION_EXECUTION_MODE=lab` in production | refused at construction |
| `ADG_REMEDIATION_SIGNING_KEY` of 12 characters | refused; a 32-character key and an empty one are accepted |

`remediator_for(ExecutionMode.LAB, environment="production")` raises
`RemediationValidationError`; `remediator_for(ExecutionMode.DISABLED, ...)` returns
`DisabledRemediator`.

### Benchmark results

Measured on Windows-11-10.0.26200-SP0 (Intel64 Family 6 Model 198 Stepping 2), Python
3.13.14, against `adg_bench`. Not comparable with the audit's numbers: a different run, on a
machine that had just finished a 27-minute suite.

**Access benchmark** — median ms:

| Shape | 10 | 100 | 400 |
| --- | ---: | ---: | ---: |
| `one_principal_one_resource` | 9.56 | 10.03 | 9.83 |
| `every_principal_on_one_share` | 9.81 | 17.87 | 227.04 |
| `every_share_for_one_principal` | 9.77 | 18.11 | 220.15 |

**Release benchmark** — median ms:

| Shape | 10 | 100 | 400 |
| --- | ---: | ---: | ---: |
| `risk_evaluate_estate` | 11.27 | 46.00 | 661.87 |
| `risk_evaluate_run` | 17.70 | 56.69 | 853.36 |
| `changes_feed` | 85.04 | 139.91 | 123.93 |
| `changes_compare` | 39.73 | 105.76 | 231.91 |
| `simulation_preview` | 194.30 | 364.51 | 3252.45 |

Both confirm limitations already recorded rather than contradicting them:
`risk_evaluate_estate` is superlinear above ~100 shares (46 ms to 662 ms), and
`simulation_preview` is the most expensive request the API serves.

---

## Reverification of the release-audit findings

Run against the clean worktree from `main`, not against the tree the audit worked in.

| Finding | Check | Result |
| --- | --- | --- |
| **C-1** | `tests/db/test_change_comparison.py::TestAFirstObservationDoesNotBreakTheComparison` — the service, and `test_the_api_returns_it_rather_than_a_422` | **2 passed** |
| **C-1** | `tests/changes/test_classify.py -k "window or sighting"` — a `FIRST_OBSERVED` change is refused a window even when one is offered | **14 passed** |
| **C-1** | The release benchmark's `changes_compare` shape returned 64, 604 and 2,404 rows at the three sizes | no 422 on an ordinary estate |
| **S-1** | `docs/contracts/collector-protocol.md` | *Authenticating every request* present before §1; the header, TLS and the 401/403 table present; the §9 example stops retrying on 401/403 |
| **P-1** | `tests/benchmarks/test_access_benchmark_harness.py` under `-Smoke` | **17 passed**, including `TestItStillRunsEndToEnd` |
| **P-1** | The benchmark built 400 rows in **`adg_bench`**; a sentinel table planted in `adg_test` beforehand was still present afterwards | the regular test database is not touched |
| **S-2** | `tests/test_config.py::TestTheChangePlanSigningKey`, plus the direct construction above | **5 passed**; 12 characters refused, 32 accepted, empty accepted |
| **Q-1** | `.\scripts\backend-lint.ps1` | ruff, ruff format and mypy all clean |
| **O-1** | `docker-compose.yml` | retention, post-run evaluation, remediation and the two policy paths reach the API container |
| **O-2** | `docs/operations/mvp-runbook.md` | all **8** roles with what each deliberately cannot do; a restore procedure using `pg_restore --clean --if-exists` and a post-restore revision check |
| **S-3** | left open, deliberately | recorded in `release-readiness.md`, in `security-review.md` §*Finding S-3* and in the audit handoff; no `Origin` assertion was added to `frontend/app/api/simulations` or `frontend/app/api/watches` |

---

## Differences from the previous audited baseline

| Measure | Audited baseline | Clean `main` | Difference |
| --- | --- | --- | --- |
| Backend collected | 6,783 | 6,783 | none |
| Backend passed | 6,772 | 6,772 | none |
| Backend skipped | 10 | 10 | none |
| Backend xfailed | 1 | 1 | none |
| Backend failed | 0 | 0 | none |
| Collector | 1,050 passed, 0 failed | 1,050 passed, 0 failed | none |
| Frontend | 1,126 passed, 31 files, build succeeded | 1,126 passed, 31 files, build succeeded | none |
| No-mutation suite | 143 passed | 143 passed | none |
| Schema parity | 45 passed | 45 passed | none |
| Migration revisions | 17 | 17 | none |
| Migration head | `0015_remediation_change_plans` | `0015_remediation_change_plans` | none |
| Lint and types | ruff, format, mypy clean (361 files) | ruff and format clean (362 files), mypy clean (361 files) | none — 362 is the formatter's count, which includes one file mypy does not type-check |
| Authorization route coverage | not stated as a number | **100 entries = 100 OpenAPI operations** over 89 paths | newly stated, not a change |
| OpenAPI snapshot | passes | passes | none |
| Backend wall clock | 26 m 29 s | 27 m 02 s | +33 s; not required to match |
| Clean install | 2.67 s | 3.14 s | not required to match |

**No test count, skip, xfail, migration revision, contract or route differs from the audited
state.** The 10 skips and the 1 xfail are the same ones `release-readiness.md` §3 accounts
for: eight are one property-based test reporting that a seed generated no parallel
memberships, one is a scenario fixture declaring no expected NTFS mask, one is a JSON Schema
carrying no examples, and the strict xfail pins the known `resource -> principals` cost.

The differences that do exist are all in the repository rather than in the product:

1. **Nine commits exist where there were none**, and `5537b16` is no longer the tip.
2. **`origin` exists**, and `main` now contains `fde6e4a` as an ancestor.
3. **The branch is `main`**, not `master`.
4. **Line endings** in part of the working tree now follow `.gitattributes`.
5. **`docs/integration/` is new** — the four documents this consolidation produced.
6. **`docs/contracts/v1/openapi.json` differs from the audited copy at commit `b3d4a46`
   only**, where it describes Phase 7B's own application rather than the tree Phase 7C
   snapshotted. At `main` it is byte-identical to the audited file.

---

## Remaining limitations

**Consolidating a repository does not make ADG production-ready, and nothing here should be
read as saying it does.** The limitations are unchanged by it and are kept in
[`docs/release/known-limitations.md`](../release/known-limitations.md). The ones that decide
what may happen next:

* The **AD collector has never been validated against a real domain**. Every AD test runs
  against a fixture provider.
* The **SMB collector has never been validated against a real Windows file server**.
* **`SeBackupPrivilege`-style privilege bypass is not modeled.** ADG under-reports, which is
  the dangerous direction.
* **Deny-only SID behavior is not fully modeled.** ADG over-reports for members of local
  administrator groups.
* **Large-estate risk-engine scaling is unvalidated**, and the measurements above confirm the
  superlinearity rather than resolving it.
* **Steady-state incremental risk evaluation has never been measured** — the case incremental
  evaluation exists for.
* **No penetration test. No dependency vulnerability or CVE review. No large-estate load or
  concurrency test.**

The NTFS collector is the exception: it has been validated against real volumes and against
Windows' own `AuthzAccessCheck`.

The next milestone is **real Active Directory and SMB field validation**. The first
installation is a validation exercise for those two collectors, not a deployment.
