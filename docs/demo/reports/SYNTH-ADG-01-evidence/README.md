# SYNTH-ADG-01 evidence

| File | What |
| --- | --- |
| `canon-parity.json` | ADG v1's generated canon (`021562b`) compared with the projection-backed canon: people, groups, owners, transfers, tags, and the membership graph on every day from 2016-10-01 to 2026-10-01 |
| `transcript-parity.json` | The 300 collector transcripts of a reset, ADG v1 against now, after the documented renames and UUID normalisation |
| `reset-report-diff.json` | Every field that differs between ADG v1's committed reset report and a reset of this branch |
| `test-runs.txt` | Backend and frontend gates |
| `tools/` | The three comparison scripts |

Reproduce from a checkout of this branch, with a second worktree of the base, a virtualenv
holding `backend[dev]`, and a scratch directory `$S`:

```sh
git worktree add $S/adg-v1 021562b
(cd $S/adg-v1/backend && python -m app.demo.null_meridian transcripts --out $S/v1)
(cd backend && python -m app.demo.genesee_press transcripts --out $S/now)
python docs/demo/reports/SYNTH-ADG-01-evidence/tools/transcript_parity.py $S/v1 $S/now \
    backend/vendor/synthetic-enterprise/standard-baseline/projections/adg/groups.jsonl
python docs/demo/reports/SYNTH-ADG-01-evidence/tools/canon_parity.py $S/adg-v1/backend backend
git show 021562b:docs/demo/evidence/reset-1.json > $S/v1-reset.json
python docs/demo/reports/SYNTH-ADG-01-evidence/tools/reset_report_diff.py $S/v1-reset.json docs/demo/evidence/reset-1.json
```

The rename rules the transcript comparison applies are the shared world's (SYNTH-RECONCILE-01A
supersession log; the projection's `legacy_name`): `@nullmeridian.info` →
`@geneseepress.example`, `nullmeridian.info` → `corp.geneseepress.example`, `NULLMERIDIAN` →
`GPCORP`, `NM-` → `GP-`, `sg-share-`/`sg-okta-` → `sec-share-`/`sec-okta-`, Lumen → Brindlewood.
