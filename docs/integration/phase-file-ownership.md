# Phase file ownership

**Date:** 2026-09-15
**Workspace:** `C:\code\adg`
**Baseline commit:** `5537b16` — *Phase 7C: record the commit hash in the handoff*
**Working tree at analysis time:** 211 paths in `git status --short`, 127 of them untracked.

This document records which uncommitted phase owns which file, which files carry more than
one phase's edits, and what that means for the reconstruction. It is the evidence behind
[`phase-dependency-order.md`](phase-dependency-order.md) and
[`repository-consolidation.md`](repository-consolidation.md).

---

## 1. How ownership was established

Ownership was **not** inferred from filenames or timestamps. Each phase handoff in
`docs/handoffs/` carries two sections written by the session that did the work:

* *Files and modules added or materially changed* — a table of every file with what was put
  in it; and
* *`git status --short`* — that session's own list of the paths belonging to it, usually with
  a `(shared)` annotation where it knew another session was editing the same file.

Those eight lists were cross-checked against each other, against the live working tree, and
against three kinds of hard evidence:

| Evidence | What it settles |
| --- | --- |
| Alembic `revision` / `down_revision` | The true ordering constraint between phases (§4 and the dependency document) |
| `import` statements in the untracked packages | Which package may be committed before which |
| `git diff` hunks in tracked shared files | Whether a phase appended or rewrote |

The eight handoffs agree with each other and with the tree. Where a handoff's own list
disagreed with `git status` it was the handoff that was stale, and those three cases are
recorded in §5.

---

## 2. The two facts that shape everything below

**Eight phases were built concurrently by several agent sessions in one working tree.** Every
handoff from 7B onward says so in its own words, and each one explains why it did not commit.
The consequence is that the working tree holds the *final* state of every file — never any
phase's intermediate state.

**That matters differently for tracked and untracked files.**

* A **tracked** shared file (`backend/app/models/schema.py`, `frontend/lib/contracts.ts`, …)
  has a `git diff` against `5537b16`. Each phase's contribution is a set of hunks that can be
  read, attributed and selected. Splitting one is *derivation*.
* An **untracked** shared file (`backend/app/governance/repository.py`,
  `backend/app/repositories/risk.py`, …) has no diff at all. When a later phase extended an
  earlier phase's untracked file, the earlier phase's version of that file **exists nowhere** —
  not in git, not on disk, not in any backup. Splitting one is *invention*.

The second case is the reason this consolidation does not produce eight per-phase commits.
§6 lists every instance.

---

## 3. Ownership table

`Conflict?` means *two or more phases edited this path and the edits must be reconciled*, not
that they collide textually. In every case examined the edits were additive and disjoint.

### 3.1 Backend — application code

| File | Primary phase | Also touched by | Reason | Final expected owner | Conflict? |
| --- | --- | --- | --- | --- | --- |
| `backend/app/models/schema.py` | 7B | 8A, 8B, 9A, 10A, 10B, 10C | Every phase appends its table declarations here; +1725/−5 | All seven, appended in dependency order | **Yes** |
| `backend/app/domain/__init__.py` | 7B | 10A, 10B, 10C | Re-export surface; 7B six exports, 10A the governance vocabulary, 10B `CommentRequirement`, 10C the remediation vocabulary | All four | **Yes** |
| `backend/app/domain/incremental.py` | 7B | — | Added by 7B | 7B | No |
| `backend/app/domain/governance.py` | 10A | 10B, 10C | 10A added it; 10B added `DecisionKind.INVESTIGATE` and `CommentRequirement`; 10C added eight `plan.*` event types | 10A+10B+10C | **Yes — untracked** |
| `backend/app/domain/remediation.py` | 10C | — | Added by 10C | 10C | No |
| `backend/app/config.py` | 8A | 8B, 10C, release audit | 8A risk path; 8B alert policy; 10C three settings and two validators; audit `MIN_SIGNING_KEY_LENGTH` | All four | **Yes** |
| `backend/app/main.py` | 10A | 10B, 10C | Exception→status mapping; 10A governance, 10B `NotHomogeneous`, 10C four remediation errors | 10A+10B+10C | **Yes** |
| `backend/app/auth/roles.py` | 8B | 9B, 10A, 10C | Capability enum and role grants; four phases append | All four | **Yes** |
| `backend/app/auth/dev_users.py` | 10A | 10C | 10A `reviewer`/`governance`; 10C `planner`/`approver` | 10A+10C | **Yes** |
| `backend/app/api/__init__.py` | 8B | 9B, 10A, 10C | Router registration at the capability boundary | All four | **Yes** |
| `backend/app/api/deps.py` | 10C | — | `RequestSettings` | 10C | No |
| `backend/app/api/scan_runs.py` | 7B | 8B | 7B the 1.4 response fields; 8B the guarded post-completion hook | 7B+8B | **Yes** |
| `backend/app/api/risks.py` | 8B | — | Added by 8B | 8B | No |
| `backend/app/api/alerts.py` | 8B | — | Added by 8B | 8B | No |
| `backend/app/api/simulations.py` | 9B | — | Added by 9B | 9B | No |
| `backend/app/api/governance.py` | 10A | 10B | 10A 18 operations; 10B four routes, 14 response models, drift on one route | 10A+10B | **Yes — untracked** |
| `backend/app/api/remediation.py` | 10C | — | Added by 10C | 10C | No |
| `backend/app/ingestion/plan.py` | 7B | — | 7B only | 7B | No |
| `backend/app/ingestion/service.py` | 7B | — | 7B only | 7B | No |
| `backend/app/ingestion/checkpoints.py` | 7B | — | Added by 7B | 7B | No |
| `backend/app/history/writer.py` | 7B | — | 7B only | 7B | No |
| `backend/app/contracts/v1/common.py` | 7B | — | 7B only | 7B | No |
| `backend/app/contracts/v1/envelopes.py` | 7B | — | 7B only | 7B | No |
| `backend/app/risk_engine/__init__.py` | 8A | — | Was a bootstrap placeholder; 8A made it the package surface | 8A | No |
| `backend/app/risk_engine/*.py` (8 modules) | 8A | — | Added by 8A | 8A | No |
| `backend/app/repositories/risk.py` | 8A | 8B | 8A added it (1,142 lines); 8B appended `RiskReportRepository`, `FindingQuery`, `FindingCoverage`, `FindingPage` | 8A+8B | **Yes — untracked** |
| `backend/app/repositories/alerts.py` | 8B | — | Added by 8B | 8B | No |
| `backend/app/repositories/membership.py` | 9A | — | Read-only `session` property | 9A | No |
| `backend/app/repositories/resources.py` | 9A | — | Read-only `session` property | 9A | No |
| `backend/app/services/risk.py` | 8A | — | Added by 8A | 8A | No |
| `backend/app/services/alerts.py` | 8B | — | Added by 8B | 8B | No |
| `backend/app/alerts/` (8 modules) | 8B | — | Added by 8B | 8B | No |
| `backend/app/operations/` | 8B | — | Added by 8B | 8B | No |
| `backend/app/simulation/` (9 modules) | 9A | 9B | 9A added the package; 9B **added** `describe.py` to it and did not edit 9A's modules | 9A+9B | No (additive file) |
| `backend/app/governance/model.py` | 10A | 10B | 10B added `comment_requirement`, `decisions_requiring_rationale()`, `validate_rationale(…, requirement=)`, `RATIONALE_ALWAYS_REQUIRED` | 10A+10B | **Yes — untracked** |
| `backend/app/governance/repository.py` | 10A | 10B | 10B added six query methods and `comment_requirement` persistence | 10A+10B | **Yes — untracked** |
| `backend/app/governance/service.py` | 10A | 10B | 10B added `bulk_decide`, `reviewer_queue`, `campaign_drift`, `item_context`, `NotHomogeneous`, `MAX_BULK_ITEMS` | 10A+10B | **Yes — untracked** |
| `backend/app/governance/audit.py`, `generation.py`, `__init__.py` | 10A | — | Added by 10A | 10A | No |
| `backend/app/governance/drift.py`, `review.py` | 10B | — | Added by 10B | 10B | No |
| `backend/app/remediation/` (9 modules) | 10C | — | Added by 10C | 10C | No |
| `backend/app/changes/classify.py` | release audit | — | C-1 | release audit | No |
| `backend/app/changes/impact.py`, `rules.py` | release audit | — | Q-1 | release audit | No |

### 3.2 Backend — tests

| File | Primary phase | Also touched by | Reason | Final expected owner | Conflict? |
| --- | --- | --- | --- | --- | --- |
| `backend/tests/api/test_authorization.py` | 10A | 9B, 10B, 10C, 8B | One table of every route and its capability; four phases add entries | All five | **Yes** |
| `backend/tests/auth/test_roles.py` | 8B | 9B, 10A, 10C | The role table, written out literally by that file's convention | All four | **Yes** |
| `backend/tests/auth/test_dev_users.py` | 10A | 10C | Account list | 10A+10C | **Yes** |
| `backend/tests/db/conftest.py` | 10A | — | `alembic upgrade heads`, not `head` | 10A | No |
| `backend/tests/support/history.py` | 7B | — | 7B only | 7B | No |
| `backend/tests/support/simulation.py` | 9A | — | Added by 9A | 9A | No |
| `backend/tests/support/equivalence.py` | 9B | — | Added by 9B | 9B | No |
| `backend/tests/incremental/`, `tests/db/test_incremental_collection.py`, `tests/contracts/test_incremental_payloads.py` | 7B | — | Added by 7B | 7B | No |
| `backend/tests/db/test_ingestion.py` | 7B | — | The 1.4 response fields | 7B | No |
| `backend/tests/fixtures/ad_graph/*.json` (12 files) | 7B | — | Regenerated by 7B | 7B | No |
| `backend/tests/risk_engine/`, `tests/db/test_risk_findings.py` | 8A | — | Added by 8A | 8A | No |
| `backend/tests/alerts/`, `tests/db/test_alerts.py` | 8B | — | Added by 8B | 8B | No |
| `backend/tests/simulation/` | 9A | 9B | 9A six suites; 9B **added** `test_describe.py` | 9A+9B | No (additive file) |
| `backend/tests/db/test_simulation.py` | 9A | — | Added by 9A | 9A | No |
| `backend/tests/db/test_simulations_api.py`, `test_simulation_equivalence.py` | 9B | — | Added by 9B | 9B | No |
| `backend/tests/governance/test_schema_vocabulary.py` | 10A | 10B, 10C | 10B taught it about later widenings; 10C taught it that `0015` widened `EVENT_TYPES` | 10A+10B+10C | **Yes — untracked** |
| `backend/tests/governance/test_drift.py`, `test_decision_rules.py` | 10B | — | Added by 10B | 10B | No |
| `backend/tests/db/test_governance_api.py`, `test_governance_isolation.py`, `test_governance_workflow.py` | 10A | — | Added by 10A | 10A | No |
| `backend/tests/db/test_governance_review.py` | 10B | — | Added by 10B | 10B | No |
| `backend/tests/remediation/`, `tests/db/test_remediation_*.py` | 10C | — | Added by 10C | 10C | No |
| `backend/tests/changes/test_classify.py` | release audit | — | C-1 | release audit | No |
| `backend/tests/db/test_change_comparison.py` | release audit | — | C-1 | release audit | No |
| `backend/tests/db/test_change_feed.py`, `test_changes_api.py` | release audit | — | Q-1 annotations | release audit | No |
| `backend/tests/test_config.py` | release audit | — | S-2 | release audit | No |
| `backend/tests/benchmarks/access_benchmark.py`, `test_access_benchmark_harness.py` | release audit | — | P-1 | release audit | No |
| `backend/tests/benchmarks/release_benchmark.py` | release audit | — | Added by the audit | release audit | No |

### 3.3 Frontend

| File | Primary phase | Also touched by | Reason | Final expected owner | Conflict? |
| --- | --- | --- | --- | --- | --- |
| `frontend/lib/contracts.ts` | 8B | 9B, 10B | +842 lines: 8B alerts/risks, 9B ~230 simulation, 10B 26 governance shapes | All three | **Yes** |
| `frontend/lib/api/adg.ts` | 8B | 9B, 10B | +393 lines: client functions and `USED_PATHS` | All three | **Yes** |
| `frontend/lib/nav.ts` | 8B | 9B, 10B | 8B un-placeholders Risks; 9B the What-if section; 10B the Reviews section | All three | **Yes** |
| `frontend/lib/paging.ts` | 8B | — | `hrefWith` repeated parameters | 8B | No |
| `frontend/lib/risks.ts`, `alerts.ts` | 8B | — | Added by 8B | 8B | No |
| `frontend/lib/simulation.ts` | 9B | — | Added by 9B | 9B | No |
| `frontend/lib/governance.ts` | 10B | — | Added by 10B | 10B | No |
| `frontend/components/PrimaryNav.tsx` | 8B | — | Injectable `items` | 8B | No |
| `frontend/components/Risks.tsx`, `Watches.tsx` | 8B | — | Added by 8B | 8B | No |
| `frontend/components/RawAcl.tsx`, `Explanation.tsx` | 9B | — | Optional `Simulate` column/action | 9B | No |
| `frontend/components/Simulation*.tsx`, `SimulateLink.tsx`, `simulation.module.css` | 9B | — | Added by 9B | 9B | No |
| `frontend/components/Governance.tsx`, `DecisionForm.tsx` | 10B | — | Added by 10B | 10B | No |
| `frontend/app/risks/page.tsx` | 8B | — | Replaced the placeholder | 8B | No |
| `frontend/app/risks/alerts/`, `frontend/app/api/watches/` | 8B | — | Added by 8B | 8B | No |
| `frontend/app/simulations/`, `frontend/app/api/simulations/` | 9B | — | Added by 9B | 9B | No |
| `frontend/app/governance/` | 10B | — | Added by 10B | 10B | No |
| `frontend/app/identities/principal/page.tsx`, `resources/directory/page.tsx`, `resources/share/page.tsx` | 9B | — | Pass keys so the Simulate column renders | 9B | No |
| `frontend/tests/components.test.tsx` | 8B | — | 8B only | 8B | No |
| `frontend/tests/contracts.test.ts` | 8B | 9B, 10B | Schema expectations from three phases | All three | **Yes** |
| `frontend/tests/nav.test.ts` | 8B | 9B, 10B | One section per phase | All three | **Yes** |
| `frontend/tests/risks*.test.*`, `alerts*.test.*` | 8B | — | Added by 8B | 8B | No |
| `frontend/tests/simulation*.test.*`, `simulation-factories.ts`, `simulations-page.test.tsx` | 9B | — | Added by 9B | 9B | No |
| `frontend/tests/governance*.test.*` | 10B | — | Added by 10B | 10B | No |

### 3.4 Collector

| File | Primary phase | Also touched by | Reason | Final expected owner | Conflict? |
| --- | --- | --- | --- | --- | --- |
| `collector/powershell/orchestrator/` | 7B | — | Added by 7B | 7B | No |
| `collector/powershell/ntfs/functions/AdgNtfsDigestIndex.ps1` + its test | 7B | — | Added by 7B | 7B | No |
| `collector/powershell/{ad,common,ntfs,smb}/**` (13 files) | 7B | — | 7B only | 7B | No |
| `collector/README.md` | 7B | — | 7B only | 7B | No |

### 3.5 Migrations

| File | Primary phase | Also touched by | Reason | Final expected owner | Conflict? |
| --- | --- | --- | --- | --- | --- |
| `0008_incremental_collection.py` | 7B | — | — | 7B | No |
| `0008_governance_model.py` | 10A | — | — | 10A | No |
| `0009_risk_findings.py` | 8A | — | Revises `0008_incremental_collection` | 8A | No |
| `0011_simulation_overlays.py` | 9A | — | — | 9A | No |
| `0012_merge_concurrent_phases.py` | 9A | — | Merges four heads written by 7C, 7B, 8A, 10A, 9A | 9A | **Yes — cross-phase** |
| `0013_access_review_workflow.py` | 10B | — | — | 10B | No |
| `0013_alert_pipeline.py` | 8B | — | — | 8B | No |
| `0014_merge_alerts_and_reviews.py` | 8B | — | Merges 8B's and 10B's revisions | 8B | **Yes — cross-phase** |
| `0015_remediation_change_plans.py` | 10C | — | — | 10C | No |

### 3.6 Contracts, documentation and configuration

| File | Primary phase | Also touched by | Reason | Final expected owner | Conflict? |
| --- | --- | --- | --- | --- | --- |
| `docs/contracts/v1/openapi.json` | 7B | 8B, 9B, 10A, 10B, 10C | **Generated.** Regenerated by each phase that added routes; +25,752/−8,089 | Whatever the final app serves | **Yes — generated** |
| `docs/contracts/v1/{common,observation-batch,scan-run-start,scan-run-completion}.schema.json` | 7B | — | Contract 1.4 | 7B | No |
| `docs/contracts/collector-protocol.md` | 7B | release audit | 7B the 1.4 additions; the audit the authentication section (S-1) | 7B + audit | **Yes** |
| `README.md` | 7B | 8A, 8B, 9A, 9B, 10A, 10B, 10C, audit | One section per phase; +159/−… | All nine | **Yes** |
| `docs/decisions/README.md` | 7B | 8A, 8B, 9A, 9B, 10A, 10B, 10C | One row per ADR | All eight | **Yes** |
| `.env.example` | 8A | 8B, 10A, 10C, audit | One setting block per phase | All five | **Yes** |
| `SECURITY.md` | 8B | 10A, 10B, 10C, audit | Posture paragraphs | All five | **Yes** |
| `docker-compose.yml` | release audit | — | O-1 | release audit | No |
| `docs/operations/mvp-runbook.md` | release audit | — | O-2 | release audit | No |
| `docs/architecture/*.md` (7 new) | one each | — | 7B, 8A, 8B, 9A, 9B, 10A, 10B, 10C | one each | No |
| `docs/architecture/simulation.md` | 9A | 9B | 9B closed limitation 3 and cross-linked | 9A+9B | **Yes — untracked** |
| `docs/decisions/00{20,23,24,25,26,28..38}-*.md` (17 new) | one each | — | one ADR per file | one each | No |
| `docs/operations/{risk-rules,alerting,remediation-runbook}.md` | 8A / 8B / 10C | — | one each | one each | No |
| `docs/release/*.md` (4 new) | release audit | — | Added by the audit | release audit | No |
| `docs/handoffs/phase-*.md`, `release-audit.md` (9 new) | one each | — | one each | one each | No |

---

## 4. Files carrying more than one phase — the summary

**Tracked, therefore separable by hunk (18):**

```
.env.example                              README.md
SECURITY.md                               docs/decisions/README.md
docs/contracts/collector-protocol.md      docs/contracts/v1/openapi.json  (generated)
backend/app/models/schema.py              backend/app/domain/__init__.py
backend/app/main.py                       backend/app/config.py
backend/app/auth/roles.py                 backend/app/auth/dev_users.py
backend/app/api/__init__.py               backend/app/api/scan_runs.py
backend/tests/api/test_authorization.py   backend/tests/auth/test_roles.py
frontend/lib/contracts.ts                 frontend/lib/api/adg.ts
frontend/lib/nav.ts                       frontend/tests/contracts.test.ts
frontend/tests/nav.test.ts
```

**Untracked, therefore *not* separable (7):**

```
backend/app/domain/governance.py                    10A + 10B + 10C
backend/app/api/governance.py                       10A + 10B
backend/app/governance/model.py                     10A + 10B
backend/app/governance/repository.py                10A + 10B
backend/app/governance/service.py                   10A + 10B
backend/app/repositories/risk.py                    8A  + 8B
backend/tests/governance/test_schema_vocabulary.py  10A + 10B + 10C
docs/architecture/simulation.md                     9A  + 9B
```

---

## 5. Three places a handoff disagreed with the tree

| Claim | Tree | Resolution |
| --- | --- | --- |
| 9A lists `docs/architecture/mvp-capabilities.md` as modified | Not modified at `5537b16` | 7C committed it. 9A's edit is already in history; nothing to carry. |
| 7B says `git status --short` lists 126 paths | 211 | 7B's count predates 8B, 9B, 10B, 10C and the audit. Later counts supersede it. |
| The audit says 211 entries, 127 untracked | 211 entries, 226 untracked *files* | Both true: `git status` collapses an untracked directory to one entry; `git ls-files --others` expands it. |

---

## 6. Where per-phase reconstruction stops being derivation

These are the cases that decide the commit granularity, and each one is an *untracked* file
that a later phase extended in place.

| File | Earlier phase | Later phase added | Why the earlier state cannot be recovered |
| --- | --- | --- | --- |
| `backend/app/governance/repository.py` | 10A (1,488 lines) | 10B: `grants_on_targets_at`, `target_presence_at`, `items_by_ids`, `items_for_drift`, `late_decisions_by_assignment`, `queue_counts_for`, `comment_requirement` persistence | No diff exists. Deleting six methods produces *a* file; nothing shows it is the file 10A had, and nothing else in the repository can check it. |
| `backend/app/governance/service.py` | 10A (1,086) | 10B: `bulk_decide`, `reviewer_queue`, `campaign_drift`, `item_context`, `NotHomogeneous`, `MAX_BULK_ITEMS`; `ReviewerProgress` gained three fields | Same. `ReviewerProgress` was *modified*, not appended to — the 10A shape is unrecorded. |
| `backend/app/governance/model.py` | 10A (697) | 10B: `comment_requirement`, `decisions_requiring_rationale()`, a new keyword argument on `validate_rationale` | Same, and the signature change is a rewrite rather than an append. |
| `backend/app/api/governance.py` | 10A (1,252) | 10B: four routes, 14 response models, drift on `GET /items/{id}` | Same. |
| `backend/app/domain/governance.py` | 10A (198) | 10B: `DecisionKind.INVESTIGATE`, `CommentRequirement`; 10C: eight `plan.*` values | Same, across three phases. |
| `backend/tests/governance/test_schema_vocabulary.py` | 10A | 10B and 10C each taught it about a later widening | Same. |
| `backend/app/repositories/risk.py` | 8A (1,142) | 8B: `RiskReportRepository`, `FindingQuery`, `FindingCoverage`, `FindingPage` | Same. Named symbols make a guess possible; nothing makes it checkable. |
| `docs/architecture/simulation.md` | 9A | 9B: "limitation 3 closed, cross-linked" | The pre-edit prose is unrecorded. |

Reconstructing any of these would mean writing code or prose that no longer exists and
presenting it as what a session committed. The brief names that as a stop condition —
*"reconstructing earlier phase history would require inventing missing code"* — and it is the
reason the consolidation groups 8A–10C into one integration commit rather than eight.

The grouping is forced further by the migration graph, which makes the three subsystems
mutually dependent. That argument is in
[`phase-dependency-order.md`](phase-dependency-order.md) §4.

---

## 7. What *is* separable, and is therefore separated

**Phase 7B** shares no untracked file with any other phase. Everything it owns is either a
file nothing else touched, or a set of identifiable additive hunks in a tracked shared file.
Its migration, `0008_incremental_collection`, hangs directly off the committed
`0007_history_model` and is a parent of 8A's `0009_risk_findings` rather than a child of
anything uncommitted. It is committed on its own.

**The release audit** touched 23 files, and the only ones it shares with an in-flight phase
are `.env.example`, `README.md`, `SECURITY.md`, `backend/app/config.py` and
`docs/contracts/collector-protocol.md` — all tracked, all additive, all separable by hunk. It
is committed on its own, last, exactly as its handoff asked.
