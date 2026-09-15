# Migration validation

**Date:** 2026-09-15
**Integration branch:** `integration/repository-consolidation`
**Final head:** `0015_remediation_change_plans`
**Revisions in the chain:** 17

This records what was checked about the Alembic graph after consolidating the eight
uncommitted phases, and what was deliberately left alone.

---

## 1. The graph as committed

```
0001_baseline
0002_ad_graph
0003_smb_resources
0004_ntfs_resources
0005_ntfs_boundaries
0006_effective_access
0007_history_model                                   <- branch point
 ├── 0008_change_feed_index          (Phase 7C, already committed at 5537b16)
 ├── 0008_incremental_collection     (Phase 7B)
 │    └── 0009_risk_findings         (Phase 8A)
 ├── 0008_governance_model           (Phase 10A)
 └── 0011_simulation_overlays        (Phase 9A)
              │
       0012_merge_concurrent_phases  (Phase 9A)  <- merge point, 4 parents
              ├── 0013_access_review_workflow    (Phase 10B)
              └── 0013_alert_pipeline            (Phase 8B)
                         │
       0014_merge_alerts_and_reviews (Phase 8B)  <- merge point, 2 parents
                         │
       0015_remediation_change_plans (Phase 10C) <- head
```

`alembic heads` on the integration branch reports exactly one:

```
0015_remediation_change_plans (head)
```

---

## 2. Revision identifiers and parents, as read from the files

| Revision | `down_revision` | Phase |
| --- | --- | --- |
| `0008_change_feed_index` | `0007_history_model` | 7C (committed) |
| `0008_incremental_collection` | `0007_history_model` | 7B |
| `0008_governance_model` | `0007_history_model` | 10A |
| `0009_risk_findings` | `0008_incremental_collection` | 8A |
| `0011_simulation_overlays` | `0007_history_model` | 9A |
| `0012_merge_concurrent_phases` | `(0008_change_feed_index, 0008_governance_model, 0009_risk_findings, 0011_simulation_overlays)` | 9A |
| `0013_access_review_workflow` | `0012_merge_concurrent_phases` | 10B |
| `0013_alert_pipeline` | `0012_merge_concurrent_phases` | 8B |
| `0014_merge_alerts_and_reviews` | `(0013_alert_pipeline, 0013_access_review_workflow)` | 8B |
| `0015_remediation_change_plans` | `0014_merge_alerts_and_reviews` | 10C |

**Three revisions share the number `0008` and two share `0013`.** That is a consequence of
four sessions numbering off the same parent without seeing each other's files, and it is
left as it is. Alembic keys on the revision *identifier*, not on the numeric prefix, and all
ten identifiers are distinct. Renumbering accepted revisions to make the prefixes unique
would rewrite migrations that already ran against developer databases, for an aesthetic
gain — which the brief prohibits and which would break every database already at one of
them.

**Two merge revisions create and drop nothing.** `0012` and `0014` exist only to rejoin
branches. Each was written by a session merging *another* session's uncommitted branch into
its own, which is why 8B's `0014` names 10B's `0013_access_review_workflow`, and why the
commit order in [`phase-dependency-order.md`](phase-dependency-order.md) has 10B landing
before 8B.

---

## 3. Clean install — empty database to latest

```powershell
docker exec adg-db-1 psql -U adg -d postgres -c "CREATE DATABASE adg_clean_install;"
$env:ADG_DATABASE_URL='postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg_clean_install'
python -m alembic upgrade head
```

**Result: 17 revisions applied, 1.88 s, exit 0.**

`alembic current` afterwards:

```
0015_remediation_change_plans (head)
```

---

## 4. Upgrade path — an existing database to latest

The previous released state was `5537b16`, whose head was `0008_change_feed_index`. A
database was built to exactly that point and then upgraded:

```powershell
python -m alembic upgrade 0008_change_feed_index    # the 5537b16 head
python -m alembic upgrade head                      # everything this consolidation adds
```

**Result: both legs succeeded; the second applied the nine new revisions in 1.22 s, exit 0.**
`alembic current` afterwards: `0015_remediation_change_plans (head)`.

Applying the branches in the order Alembic chose put `0008_governance_model`,
`0008_incremental_collection`, `0009_risk_findings` and `0011_simulation_overlays` in before
`0012`, which is the only ordering the graph permits.

---

## 5. Model and schema alignment

`backend/database/migrations/env.py` points Alembic at `app.models.schema.metadata`, and
`tests/db/test_schema.py` reflects the live database and compares it against that metadata,
so a hand-edited migration cannot drift from the declaration.

| Check | Result |
| --- | --- |
| `app.models.schema.metadata` table count at the head | **39** |
| Schema parity, `tests/db/test_schema.py` + `tests/db/test_remediation_schema.py` | see [`repository-consolidation.md`](repository-consolidation.md) §*Clean-main validation* |

At commit 1 the same metadata declares **18** tables — the 16 at `5537b16` plus Phase 7B's
`scan_run_checkpoints` and `collector_checkpoints` — which is the check that Phase 7B's half
of `schema.py` was separated from the other six phases' correctly.

---

## 6. Did parallel phases introduce incompatible ancestry?

No, and the reason is that two of the sessions noticed and fixed it before this
consolidation began.

* **Four branches off `0007_history_model`** were created by 7C, 7B, 10A and 9A
  independently. 9A wrote `0012` to rejoin all four, and its `down_revision` tuple names
  `0009_risk_findings` rather than `0008_incremental_collection`, so 8A's revision is inside
  the merged chain rather than bypassed.
* **Two branches off `0012`** were created by 10B and 8B. 8B wrote `0014` to rejoin them.

Both merges are correct as written and neither was modified here. What the consolidation had
to supply was a **commit order** in which each merge revision's parents are already present
in the repository when the merge lands — which is the whole content of
[`phase-dependency-order.md`](phase-dependency-order.md).

The one transient effect is at commit 1, where 7B's branch exists and `0012` does not, so
the graph has two heads and `alembic upgrade head` refuses to choose. That is measured and
recorded in the dependency document §5; `alembic upgrade heads` applies both.

---

## 7. What was not done

* **No revision was renumbered.** See §2.
* **No migration was collapsed or squashed.** The chain is 17 revisions and stays 17.
* **No merge revision was rewritten.** `0012` and `0014` are as their authors wrote them.
* **No `downgrade()` was exercised.** `0013_access_review_workflow`'s downgrade deliberately
  refuses rather than deleting attestations, and this consolidation had no reason to run it.
