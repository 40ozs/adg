# SYNTH-CROSSAPP-01 handoff (ADG)

The cross-application integration of the four SYNTH-*-01 lanes. The canonical record is the synthetic-enterprise
report: [SYNTH-CROSSAPP-01-report.md](https://github.com/40ozs/synthetic-enterprise/blob/integration/SYNTH-CROSSAPP-01/docs/demo/reports/SYNTH-CROSSAPP-01-report.md) (`40ozs/synthetic-enterprise`, branch `integration/SYNTH-CROSSAPP-01`).
This note says what changed in this repository and what a later lane needs from it.

| | |
|---|---|
| Branch | `integration/SYNTH-CROSSAPP-01` (pushed; not merged; no pull request; nothing deployed) |
| Started from | `feature/SYNTH-ADG-01` `7053c33ab46d57b9030b16237a5a2ad618d0884b` (unchanged) |
| Pins | `backend/vendor/synthetic-enterprise/SYNTHETIC_ENTERPRISE.lock`: synthetic-enterprise `ce7c0c7d9fbc82c67e596dde95cf5e2432acf450`, dataset `2.1.0`, `adg@0.3.0`, manifest `689d4e62…273f` |
| Commands | `python scripts/vendor-synthetic-enterprise.py --source ../synthetic-enterprise` (clean checkout at `ce7c0c7`); `python -m app.demo.genesee_press manifest --out ../docs/demo/genesee-press.manifest.json` |

## What changed here

- The vendored projection and lock. Dataset 2.1.0 merges OGWA's 12 case-only duplicates of ADG team groups into ADG's
  groups (D-XA-01), so the projection's full directory is 509 groups (was 521) and 14,104 memberships (was 14,487).
- `docs/demo/genesee-press.manifest.json`: only its `source` block moves.
- One test (directory size, dataset version), the canon docstring and `docs/demo/genesee-press.md`.

ADG's catalogue is unchanged. Its 206 groups, 192 nestings and 4,273 edges are byte-identical, and no ADG SID changed:
ADG's SIDs and objectGUIDs are now the directory identity in every projection (D-XA-02).

## Regression gates (all held, nothing re-pinned)

- 2,440 employees, 206 groups, 4,273 edges.
- 300 transcripts, 41,756 observations, 9,386 findings, 23/23 probes.
- Demo Mode fingerprint `4667d3cf…9b270f08` on two clean resets.
- Logical fingerprint `b4b93b65…e59445d0`.
- Reset reports identical to `docs/demo/evidence/reset-{1,2}.json`.
- Backend unit 6,026 passed; DB suite on PostgreSQL 16 (see the report §13).
- Frontend 1,510 passed; lint, typecheck, build and `cf:typecheck` clean.

All suites ran on Python 3.13. On 3.11, 285 unit tests fail identically at `7053c33`; this is pre-existing.

## For the cutover lane (Prompt 10)

`adg-demo` must receive a sanitized, approved tree export of this branch, never ADG's Git history (the raw U-02 capture
is in it). Then:

1. Set or unset `ADG_DEMO_DATASET=genesee-press-v1`.
2. Bootstrap the hosted database and expect the fingerprints above.
3. Re-capture the hosted evidence.
4. Deploy from `adg-demo`.

