# Phase dependency order

**Date:** 2026-09-15
**Baseline commit:** `5537b16` — *Phase 7C: record the commit hash in the handoff*

This document establishes the real dependency graph among the eight uncommitted phases, tests
the shape the integration brief proposed, and derives the commit order from it. Evidence for
file ownership is in [`phase-file-ownership.md`](phase-file-ownership.md).

---

## 1. The shape the brief proposed, and what it gets wrong

The brief offered this as a starting hypothesis:

```
07B
 ├── 08A
 │    └── 08B
 ├── 09A
 │    └── 09B
 └── 10A
      └── 10B
           └── 10C
```

Three of its edges are real, one is real for a different reason than implied, and **the tree
shape itself is wrong**: the three subsystems are not independent branches off 7B. They are
mutually dependent through the Alembic graph, and the dependency runs in *both* directions
between the risk lineage and the governance lineage.

---

## 2. The hard evidence: the Alembic graph

Revision identifiers and `down_revision` values, read from the files themselves. Committed
revisions are marked ✓.

```
0007_history_model ✓                                    (Phase 7A)
 │
 ├── 0008_change_feed_index ✓                           (Phase 7C)
 ├── 0008_incremental_collection                        (Phase 7B)
 │    └── 0009_risk_findings                            (Phase 8A)
 ├── 0008_governance_model                              (Phase 10A)
 └── 0011_simulation_overlays                           (Phase 9A)
                    │
        0012_merge_concurrent_phases                    (written by Phase 9A)
        down_revision = (0008_change_feed_index,
                         0008_governance_model,
                         0009_risk_findings,
                         0011_simulation_overlays)
                    │
         ├── 0013_access_review_workflow                (Phase 10B)
         └── 0013_alert_pipeline                        (Phase 8B)
                    │
        0014_merge_alerts_and_reviews                   (written by Phase 8B)
        down_revision = (0013_alert_pipeline,
                         0013_access_review_workflow)
                    │
        0015_remediation_change_plans                   (Phase 10C)
```

Single head: **`0015_remediation_change_plans`**. Seventeen revisions.

A commit is only coherent if every `down_revision` it contains names a revision that is also
present. That turns the graph into ordering constraints on commits:

| Constraint | Source |
| --- | --- |
| 8A after 7B | `0009_risk_findings` revises `0008_incremental_collection` |
| 9A after 8A **and** after 10A | `0012` names `0009_risk_findings` and `0008_governance_model` |
| 10B after 9A | `0013_access_review_workflow` revises `0012` |
| 8B after 9A | `0013_alert_pipeline` revises `0012` |
| **8B after 10B** | `0014` names `0013_access_review_workflow` |
| 10C after 8B | `0015` revises `0014` |

The fifth is the one the brief's tree does not predict, and 8B's own handoff flags it:
`0014_merge_alerts_and_reviews` *"names the other session's `0013_access_review_workflow` in its
`down_revision`. It is correct as written and it is the one file here that **cannot** be
committed before that revision is."*

---

## 3. The brief's specific questions, answered

| Question | Answer | Evidence |
| --- | --- | --- |
| Does 08A depend on 07B? | **Yes.** | `0009_risk_findings.down_revision = "0008_incremental_collection"`. 8A's handoff says it took `0009` because "`0008` was taken three times over by the other session". |
| Does 08B depend on 08A? | **Yes**, twice over. | 8B appends `RiskReportRepository` to 8A's `backend/app/repositories/risk.py`; `app/api/risks.py` reads 8A's engine; `backend/app/services/alerts.py` consumes 8A's findings. |
| Does 09A depend on snapshot/history infrastructure? | **Yes**, on 7A's, which is committed. | `0011_simulation_overlays` revises `0007_history_model`. The overlay repositories subclass the as-of repositories 7A introduced; 9A's handoff calls the substitution "what Phase 7A used to answer about a past instant". **Not** a dependency on any uncommitted phase. |
| Does 09B depend on 09A? | **Yes.** | `app/api/simulations.py` renders 9A's report types; `app/simulation/describe.py` is added *into* 9A's package; 9B edited 9A's `docs/architecture/simulation.md`. |
| Does 10A depend on history/risk/simulation? | **History only**, and that is committed. | `0008_governance_model` revises `0007_history_model`. `GovernanceRepository` reads `object_versions`. It cites no risk and no simulation module. |
| Does 10B depend on 10A plus risk/change APIs? | **10A yes; risk and change APIs no.** | 10B extends four of 10A's untracked modules in place. `governance/review.py` "asks five existing ADG answers about one item" — all of them committed engines. Its migration revises `0012`, so it also lands after 9A. |
| Does 10C depend on governance and simulation? | **Both, and on 8B as well.** | `remediation/translate.py` produces a 9A simulation overlay; the service appends to 10A's governance audit trail; `0015` revises 8B's `0014`. |

---

## 4. Why the eight phases cannot become eight commits

Two independent obstacles, either of which is sufficient.

### 4.1 The migration graph admits a serial order — the code does not

The constraints in §2 are satisfiable. This order has no cycle:

```
7B → 8A → 10A → 9A → 9B → 10B → 8B → 10C
```

Every revision's parents are present at every step. So the *migrations* are serializable.

What is not serializable is the **source**, because three pairs of phases share untracked
files that the later phase extended in place, and no intermediate version of those files
exists anywhere (ownership document §6):

```
8A  ↔ 8B    backend/app/repositories/risk.py
9A  ↔ 9B    docs/architecture/simulation.md
10A ↔ 10B ↔ 10C   six governance modules, one test
```

Committing 8A without 8B's additions to `risk.py` means writing a version of that file that
no session ever had. The brief lists that as a stop condition. So each pair must be committed
as one unit.

### 4.2 Grouping the entangled pairs creates a cycle

Write the forced groups as **R** = {8A, 8B}, **S** = {9A, 9B}, **G** = {10A, 10B, 10C}, and
apply §2's constraints:

| From §2 | Between groups |
| --- | --- |
| 9A after 8A | **S after R** |
| 9A after 10A | **S after G** |
| 10B after 9A | **G after S** |
| 8B after 10B | **R after G** |
| 10C after 8B | **G after R** |

`S after G` and `G after S` cannot both hold. **R, S and G form one strongly connected
component.** No ordering of the three exists, so they cannot be three commits, and — since
each group is already the minimum unit — they cannot be eight either.

This is a property of how the work was done, not of the reconstruction: three sessions wrote
migrations that revise each other's revisions, in a tree where none of them had committed.
The two merge revisions (`0012`, `0014`) are the join points, and each was written by a
session merging *someone else's* uncommitted branch into its own.

### 4.3 What that leaves

**Phase 7B is outside the component.** It shares no untracked file with anything, and its
revision is a parent of the component rather than a child of it. It is committed on its own,
first — which also repairs the inconsistency 7B's handoff documented at `5537b16`, where 7C
swept 7B's regenerated `openapi.json` and `README.md` into history while leaving the code
that justifies them behind.

**Everything in the component is one commit**, named for what it is rather than for one of
the eight phases inside it.

**The release audit is separable and goes last**, exactly as its own handoff asked: *"the
phases in flight commit their own work, and this audit's changes are then committed on top of
it."*

---

## 5. The integration order

| # | Unit | Contents | Migrations added | Head after |
| --- | --- | --- | --- | --- |
| 0 | *baseline* | `5537b16` | — | `0008_change_feed_index` |
| 1 | **Phase 7B** | Incremental collection and reconciliation; collector orchestrator; contract 1.4 | `0008_incremental_collection` | two heads |
| 2 | **Phases 8A–10C** | Risk engine, alert pipeline, simulation engine and UI, governance model, access reviews, remediation planning | `0008_governance_model`, `0009_risk_findings`, `0011_simulation_overlays`, `0012`, `0013_access_review_workflow`, `0013_alert_pipeline`, `0014`, `0015` | `0015_remediation_change_plans` |
| 3 | **Release audit** | C-1, S-1, P-1, S-2, Q-1, O-1, O-2 and the four release documents | none | `0015_remediation_change_plans` |
| 4 | **Integration documentation** | `docs/integration/` | none | `0015_remediation_change_plans` |

Commit 1 leaves the migration graph with **two heads** — `0008_change_feed_index` (7C's,
committed at `5537b16`) and `0008_incremental_collection` (7B's, landing here). Both revise
`0007_history_model`, so the branch is 7B's own: it wrote a revision off the same parent 7C
had already taken, and the merge revision that rejoins them is `0012`, which 9A wrote and
which therefore arrives in commit 2.

Measured consequence at commit 1, rather than assumed:

| Command | Result |
| --- | --- |
| `alembic heads` | two heads, as above |
| `alembic upgrade heads` | **succeeds** — both branches apply, in either order |
| `alembic upgrade head` | **fails**: *"Multiple head revisions are present for given argument 'head'"* |
| `backend/tests/db/conftest.py` at that commit | runs `alembic upgrade head`, so the **database suite cannot build its schema at commit 1** |
| `.\scripts\backend-test.ps1` (hermetic) at commit 1 | 4,572 passed, 10 skipped, 736 deselected · exit 0 |

The one-line change from `head` to `heads` is 10A's, made for exactly this reason, and it
arrives in commit 2. Backdating it into commit 1 would attribute another phase's fix to 7B to
make a number look better; renumbering 7B's revision to avoid the branch would be the
cosmetic migration rewrite the brief prohibits. So commit 1 is recorded as what Phase 7B
actually was — a migration branch awaiting a merge revision — and commit 2 closes it.

This is the single respect in which a commit in this history is not independently green, and
it is stated here rather than smoothed over.

### The openapi.json snapshot at the baseline was already stale

`docs/contracts/v1/openapi.json` is generated, and 7C committed a copy regenerated from the
shared working tree. Measured at `5537b16`, that copy publishes **15 governance paths the
application at that commit does not serve** (Phase 10A's, still uncommitted then) as well as
Phase 7B's response schemas. So
`tests/contracts/test_openapi_snapshot.py::test_the_snapshot_is_current` **fails at the
baseline commit** — a wider inconsistency than 7B's handoff described, which named only its
own contribution.

Commit 1 therefore regenerates the snapshot from its own application, which is what Phase 7B
did in its own tree, and commit 2 regenerates it again once every router is present. Carrying
7C's copy forward unchanged would have left commit 1 failing a test for a reason that has
nothing to do with Phase 7B.

---

## 6. What this order does *not* claim

It does not claim to be the order in which the work happened. Phases 8A and 10A were being
written at the same time as 7B; 9A wrote a merge revision for branches whose owners had not
finished. There is no serialization of these eight phases that is both truthful and
per-phase, and inventing one would put a date and an author on code that was never in that
state.

What the order does claim is that each commit **builds, tests and migrates on its own**, that
every file in it is attributed by evidence rather than by filename, and that nothing in the
working tree was discarded to achieve it.
