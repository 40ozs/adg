# SYNTH-ADG-01: ADG projection and migration to the Genesee Press baseline

- **Date:** 2026-10-27
- **Lane:** SYNTH-ADG-01 (parallel application lane, MIGRATION-STRATEGY phases 3 and 4, ADG)
- **Repositories:** `40ozs/synthetic-enterprise` and `40ozs/adg`, branch
  `feature/SYNTH-ADG-01` in each (pushed, not merged, no pull request)
- **Evidence:** [SYNTH-ADG-01-evidence/](SYNTH-ADG-01-evidence/) here, and
  `docs/demo/reports/SYNTH-ADG-01-evidence/` in `40ozs/adg` (parity tools and outputs)

```
SYNTH_RECONCILE_BASE_SHA=dbda9ae4cc1943b5d5556dcc25008b1048258356
```

---

## 1. Verdict

**`READY_FOR_SYNTH_CROSSAPP_INTEGRATION_ADG`**

ADG's demo no longer generates its directory. Its people, accounts, groups,
memberships, servers, org units and the organization come from the `adg@0.3.0` projection of
the shared world. The projection is vendored into ADG at a pinned commit and verified before
it is read.

The demo is Genesee Press:

- mail and UPNs on `geneseepress.example`;
- the directory is `corp.geneseepress.example` / `GPCORP`;
- employee ids and servers are `GP-`;
- the standard groups are `sec-*`;
- the acquisition is Brindlewood Analytics.

**Parity is exact.** All 300 collector transcripts (41,756 observations) are the only data a
reset feeds ADG. After the documented old → new renames, they are identical to ADG v1's.
Every count, probe, run status and risk total in the reset report is unchanged. That covers:

- 9,386 open findings;
- 23/23 probes;
- the 221-difference comparison drill;
- the Apollo investigation.

All suites are green:

- synthetic-enterprise: 98 tests, Python 3.11, 3.12 and 3.13, including re-extraction;
- ADG backend: 6,025 unit tests and 1,298 database tests (§10);
- ADG frontend: 1,510 tests, lint, typecheck and build.

ADG consumes its own 206-group subset of the 521 canonical directory groups, as the
shared-universe contract permits (§6, D-ADG-02).

No `BLOCKED_BY_CANONICAL_CONTRACT_CONFLICT`: nothing in `contract/`, `universe/`, the shared
generator, ID rules, scenarios or another application's projection was changed.

## 2. Bases

| | Value | Why |
|---|---|---|
| synthetic-enterprise base | `dbda9ae4cc1943b5d5556dcc25008b1048258356` | The frozen `SYNTH_RECONCILE_BASE_SHA`. Verified as the parent of the SYNTH-RECONCILE-01A report commit `5fcd0b7`, read from `origin/feature/SYNTH-RECONCILE-01A`. The superseded `a198628…` and the report head were not used. |
| ADG base | `origin/main` `021562b41b859f0a3f0600824ba0ccf95c3b6b69` (*Record ADG-UI-RECONCILE-01*) | This is the newest verified ADG state. It contains the integrated Null Meridian Demo Mode, DEMO-01/02/03, Next.js 16, contract 1.2 and ADG-UI-RECONCILE-01. It is 17 commits past `1687691`, the commit SYNTH-RECONCILE-01 extracted from; `canon.py`, `estate.py`, `generations.py` and the canon JSON are unchanged between them. |

Other ADG branches checked:

- **`deploy/ADG-HOSTED-NEXT16-01`** (`f730d99`): 0 ahead and 19 behind `main`. It is the
  current hosted deploy source.
- **`feature/CLOUDFLARE-DEMO-CACHE-01`**: 2 commits ahead of an older `main`. Its own verdict
  is `BLOCKED`, and it is a Worker test plus a report; it touches no demo data.
- **Every other `claude/*`, `feature/ADG-GRAPH-*`, `fix/*` and `integration/*` branch:**
  behind `main` and superseded by its reports.

**Branch naming.** This session's default branch name, `claude/wonderful-thompson-9p23b9`, is
shared by the four parallel lanes in every repository. The lane brief asks for a dedicated
`feature/SYNTH-ADG-01`, so that is where both repositories' work is pushed. The session
branches were not pushed.

## 3. Branches and commits

**`40ozs/synthetic-enterprise` — `feature/SYNTH-ADG-01`**

| Commit | Content |
|---|---|
| `71055a72001ce814547683029cd06ecd6462a3ae` | `adg` projection 0.3.0, `tests/test_adg_projection.py`, the ADG lines of `tests/test_reconciled_universe.py`, and the ADG row of PROJECTION-CONTRACT §9 |
| `6f016d8c0753f79669e4432eb42ab04c2f6cc0c7` | `snapshots/standard-baseline` rebuilt with source commit `71055a7`. Only `projections/adg/` and `manifest.json` change. **ADG's lock pins this commit.** |
| report commit (this file) | `docs/demo/reports/SYNTH-ADG-01-*` only. Fresh-clone check of `6f016d8`: 98 tests OK, reconcile check, verify and scan clean, clean tree (`fresh-clone.txt`). |

**`40ozs/adg` — `feature/SYNTH-ADG-01`**

| Commit | Content |
|---|---|
| `5e7debb` | the migration: vendored projection and lock, `app.demo.genesee_press`, tests, docs, re-pinned evidence, the U-02 fixture |
| `28abcff` | a test-comment path fix |
| report commit | this report and `docs/demo/reports/SYNTH-ADG-01-evidence/` |

The lane summary gives the final SHAs; `git log -1 origin/feature/SYNTH-ADG-01` in each
repository shows them too.

## 4. synthetic-enterprise changes (ADG-only)

| File | Change |
|---|---|
| `src/synthetic_enterprise/projections/adg.py` | Version `0.2.0 → 0.3.0` (minor: files and fields added, nothing removed or renamed) |
| `tests/test_adg_projection.py` (new) | 8 tests (§9) |
| `tests/test_reconciled_universe.py` | Two ADG-specific lines: the ADG record counts, and the ADG version in the per-app version check |
| `docs/architecture/PROJECTION-CONTRACT.md` | The ADG row of the §9 registry |
| `snapshots/standard-baseline/` | Rebuilt. Canonical files and the OGWA, AO and CSW projections are **byte-identical** to `dbda9ae` (`git diff --name-only dbda9ae` lists only `projections/adg/*` and `manifest.json`), and the canonical digest is unchanged, `c7b742cb…`. |

### Projection `adg@0.3.0`

Scope and options are unchanged:

```
org=org_80AJVSFG78Z0, exclude=ou_M970YB9ACZCZ+ou_T483WPYQTBE2
```

That is the Genesee Press core. `complete_kinds` adds `org_unit`.

| File | Records | New in 0.3.0 |
|---|---:|---|
| `organization.jsonl` | 1 | Name, primary domain, founding date, industry, milestones (identity provider 2021-10-01, `sec-*` standard 2023-10-01, Brindlewood Analytics 2021-04-01) |
| `directory.jsonl` | 1 | The directory identity system: `corp.geneseepress.example`, `GPCORP`, domain SID, 180-day deletion policy |
| `org_units.jsonl` | 75 | 14 departments and 61 teams. `legacy_slug` comes from `adg.department` / `adg.team`. |
| `employees.jsonl` | 2,440 | Adds `upn`, `object_guid` (`adg.object_guid`), `department_canonical_id`, `team_canonical_id`, `previous_department_canonical_id` and `transfer_date` (from the `transferred` employment event), and `tags` |
| `groups.jsonl` | 521 | Adds `description`, `tier`, `department_canonical_id`, `application_canonical_id`, `legacy_application_token` (`adg.application_token`), `owner_canonical_id` (`managed_by` ownership) and `object_guid` (`adg.group_object_guid`) |
| `memberships.jsonl` | 14,487 | unchanged |
| `hosts.jsonl` | 34 | unchanged |

Every record keeps `canonical_id`, and every `*_canonical_id` resolves inside the scope. ADG
v1 values appear only in `legacy_*` / `prior_*` fields. SIDs and objectGUIDs are opaque
directory identifiers and carry their ADG values.

**Reconstruction check.** Before ADG was changed, the 0.3.0 records were checked against ADG's
legacy extract field by field. They reproduce ADG's canon exactly: names, dates, titles,
levels, managers, departments, teams, transfers, tags, sAMAccountNames, mail local parts,
RIDs, objectGUIDs, group dates, categories, scopes, tiers, departments, owners and SIDs. The
only differences are the canonical renames (§7) and one application token. `sec-okta-okta-admin`
has no `application_canonical_id`: in the shared world the identity provider is an identity
system, not an application. ADG uses the token for nothing at runtime.

## 5. ADG changes

| Area | Change |
|---|---|
| `backend/vendor/synthetic-enterprise/` (new, 5.9 MB) | `SYNTHETIC_ENTERPRISE.lock`, `standard-baseline/manifest.json`, `standard-baseline/projections/adg/*`, copied unchanged |
| `backend/app/demo/genesee_press/` (was `null_meridian/`) | `vendor.py` (new) checks the lock, the manifest SHA-256 and every file SHA-256 before any read, and refuses an edited or missing file. `canon.py` is rewritten: the generator (drafts, name pools, group and nesting catalogues, assignments, ownership, identifier allocation; about 2,200 lines) is gone, and `build_canon()` reads the projection into the same `Canon` / `Employee` / `Group` / `Nesting` API. `names.py` is deleted. `estate.py` takes its file servers from the projection's hosts, and all names are canonical. `manifest.py` gains a `source` block (lock, canonical digest, options, estate scope), and the canon export is retired. |
| `scripts/vendor-synthetic-enterprise.py` (new) | Re-vendors from a clean local checkout and rewrites the lock. It needs no network, and it lives outside `app/` because it runs `git`. |
| Dataset | The key `null-meridian-v1` becomes `genesee-press-v1`. This covers the registry, the marker, `ADG_DEMO_DATASET` in compose, the workflow, `.env.example` and the docs, and the risk configuration version. |
| Docs | `docs/demo/null-meridian.md` becomes `genesee-press.md`, rewritten where the source changed (header, identifiers, §8 *Where the directory comes from*, §9 fingerprints). The demo guide, walkthroughs, `demo-mode.md`, `change-detection.md`, `system-overview.md`, `cloudflare-demo.md` and `README.md` are updated. `null-meridian.canon.json` is deleted, and the manifest is renamed and regenerated. |
| Evidence | `docs/demo/evidence/reset-{1,2}.json` are regenerated (two local resets). Historical reports and handoffs are unchanged. |
| Tests | Renamed `test_genesee_press_*`, and literal values re-pinned by the same rename rules. `TestTheSharedWorld` is new. The Windows oracle gains a sanitization test. |
| Frontend | Test factories, Cloudflare checks and two comments are updated by the rename rules. The logical fingerprint check is renamed `genesee_press_fingerprint.py` and its `EXPECTED` is re-pinned. No component hard-codes the company: the UI reads it from `/runtime`. |

### Mapping behaviour

- **Canonical ids:** every `Employee` and `Group` carries `canonical_id` (plus
  `account_canonical_id`), and so does every `Nesting` edge. The manifest's employee and group
  rows carry them in place of ADG's former uuid5 values.
- **ADG-local ids kept:** SIDs, RIDs and objectGUIDs, from the projection unchanged, so the
  frontend's pinned SIDs (RIDs 1108, 1243, 1251, 1323, 1437, 1443, 2325) did not move.
  Department and team slugs come from `legacy_slug`.
- **Legacy draw keys (D-ADG-01):** ADG's file estate (shares, folders, ACLs, scans) is ADG's
  own data, and its organic ACLs come from seeded SHA-256 draws. To keep that estate exactly
  as it was, the draw seed stays `LEGACY_DRAW_SEED = "null-meridian-v1"`. Draw labels, and the
  orders draws pick from, use each name's v1 spelling through `canon.legacy_key` / `draw_key`.
  Group, server and employee spellings come from the projection's `legacy_*` fields. The two
  acquisition share and folder names come from the `LEGACY_ESTATE_SPELLINGS` table. These are
  seeds and orderings only, never displayed, and a test asserts that the manifest contains no
  former value. Re-keying them is a new dataset (`genesee-press-v2`), which would re-pin every
  count.
- **UUIDs:** run and batch ids are uuid5 under `uuid5(NAMESPACE_DNS, "corp.geneseepress.example")`.
  They are opaque, so they all changed.

## 6. D-ADG-02: ADG's estate is ADG's directory catalogue

The canonical directory is the union of the catalogues the world was reconciled from
(ADR-0003): 521 groups, which are 206 from ADG and 315 from OGWA. 20 of the OGWA groups are
nested into ADG's `sec-share-*` groups. Examples: `GG_Dept_Finance` into
`sec-share-finance-read-only`, and `GG_Dept_Engineering` into
`sec-share-engineering-read-only`.

ADG's demo loads:

- every employee (2,440);
- the 206 groups whose projection record carries an ADG `legacy_name`;
- the memberships among them: 4,273 edges on the snapshot, 192 of them nestings.

Loading the other 315 groups and 9,154 memberships (the 20 nestings included) would add access routes to ADG's standard
share groups. That would change every pinned count (findings, routes, comparison sizes). ADR-0003
already expects ADG's migration to be "mostly a domain change". Bringing ADG's estate to the
full canonical directory is therefore not done. The owner confirmed this after the lane (§15):
the shared-universe contract permits application-specific subsets, and ADG is not expanded
for numerical consistency across applications. An additional canonical group is added to
ADG's estate only when a concrete cross-application scenario requires ADG to see it, and its
effect on the membership graph, access routes, finding counts, reset fingerprints, golden
outputs and the parity guarantees (§15) is measured and documented. The projection already
carries the full directory (`complete_kinds` includes `group`), so such a change needs only
ADG's selection rule in `canon._catalogue_groups`, not a projection change.

## 7. Genesee Press values

| Value | ADG v1 | ADG now | Source |
|---|---|---|---|
| Company | Null Meridian | Genesee Press | `organization.jsonl` |
| Mail / UPN | `@nullmeridian.info` (non-reserved, U-01) | `@geneseepress.example` | `employees.jsonl` |
| AD DNS / DN | `nullmeridian.info`, `DC=nullmeridian,DC=info` | `corp.geneseepress.example`, `DC=corp,DC=geneseepress,DC=example` | `directory.jsonl` |
| NetBIOS | `NULLMERIDIAN` | `GPCORP` | `directory.jsonl` |
| Employee ids | `NM-00001…` | `GP-00001…` | `employees.jsonl` |
| Servers | `NM-ENG-FS01` … | `GP-ENG-FS01` …, FQDN `gp-eng-fs01.corp.geneseepress.example` | `hosts.jsonl` |
| Standard groups | `sg-share-*`, `sg-okta-*` (62 of ADG's 206; 195 in the whole directory) | `sec-share-*`, `sec-okta-*` | `groups.jsonl` (`prior_name` keeps `sg-…`) |
| `di-*` (future DG-derived) | none | none today. A `di-*` directory group projects unchanged (tested), and ADG's naming test requires `sec-`/`di-` names to be lowercase. | ADR-0010 |
| Security-team groups | `SEC-SOC`, `Sec-GRC` | unchanged (not the prefix) | ADR-0010 |
| Acquisition | Lumen Analytics, `LA-Migrated-Users`, `Acq-Lumen-RW`, `Lumen-Analytics` share | Brindlewood Analytics, `BA-Migrated-Users`, `Acq-Brindlewood-RW`, `Brindlewood-Analytics` | organization milestone, groups |
| Dataset key | `null-meridian-v1` | `genesee-press-v1` | ADG |
| Domain SID, SIDs, objectGUIDs | `S-1-5-21-3949361974-1583827165-633756269-…` | unchanged | `directory.jsonl`, `employees.jsonl`, `groups.jsonl` |

The table maps exactly 64 ADG group names: 62 `sg-`→`sec-` renames and 2 acquisition
renames. In repository files outside historical reports and handoffs,
`nullmeridian`, `NULLMERIDIAN`, `Null Meridian`, `NM-` and `Lumen` survive only in:

- `LEGACY_DRAW_SEED` and the legacy-spelling table in `canon.py`;
- tests that assert those values are absent;
- the dataset doc's one-line "formerly" note.

## 8. Parity evidence

| Check | Result | Evidence (ADG repo) |
|---|---|---|
| Canon: legacy generator (`021562b`) vs projection-backed canon | 2,440/2,440 employees, 206/206 groups. Every person, group, owner, transfer and tag is equal after renames. **The membership graph is equal on all 3,653 days** from 2016-10-01 to 2026-10-01. Nesting depths are equal. | `canon-parity.json`, `tools/canon_parity.py` |
| Collector transcripts (the reset's only data input) | 300/300 transcripts, 41,756 observations: **0 differences** after the documented renames and UUID normalisation. Before normalisation 0 files are byte-identical, as expected. | `transcript-parity.json`, `tools/transcript_parity.py` |
| Reset report v1 vs now | 27 differing fields, all names or content digests. Unchanged sections: `counts`, `generations`, `risk`, `risk_findings_open`, `ok`, `initialized`, and every probe result. | `reset-report-diff.json`, `tools/reset_report_diff.py` |
| Repeatability | Two clean local resets give the same Demo Mode fingerprint `4667d3cfb4b574ffda3ac7ea8453912568b7517cd53548fb338b497e9b270f08` (v1: `228b9dce…`). `verify` passes. The logical fingerprint is `b4b93b65a77d895cbd0495d34865937bdab39596494bd3ca035ce8f8e59445d0` (v1: `db736602…`). | `docs/demo/evidence/reset-{1,2}.json` |
| DB suite for the dataset | `tests/db/test_genesee_press_demo.py`: 51 passed. It covers counts, 9,386 findings, the 221-difference drill, repeatability, the access investigation and coverage, all through the real API and PostgreSQL 16. | §10 |

**Demo surfaces that depended on the old fixtures, and their state:**

- **`/demo`, scenarios A–H:** same objects and outcomes; names updated in the docs, tests
  and probes.
- **The Apollo investigation:** `\\GP-ENG-FS01\Engineering\Projects\Apollo`, Fatima Gordon,
  Modify `at_least`. The open question is now `GPCORP\lhansen`.
- **Coverage and recovery:** `GP-FS-03` failed, `GP-MIG-FS01` partial, 98/100 scopes; the
  drill goes `timed_out → incomplete → complete` (221).
- **`/risks`:** 9,386 findings, with the same severities.
- **The frontend factories:** SIDs unchanged, names updated.
- **The Cloudflare smoke, navigation and browser checks:** names updated.

The screenshots in DEMO-02/03 evidence show the former names. They are historical and not
re-captured; no browser run against a hosted demo was possible from this lane.

## 9. Tests

| Suite | Result |
|---|---|
| synthetic-enterprise, Python 3.11.17 | **Ran 98 tests, OK** (skipped 1: extraction needs 3.13) |
| synthetic-enterprise, Python 3.12.3 | **Ran 98 tests, OK** (skipped 1) |
| synthetic-enterprise, Python 3.13.17, `SE_LEGACY_SOURCES` set | **Ran 98 tests, OK, no skips.** Extraction reproduces `migration/legacy-extracts/` byte for byte. |
| `ruff check src tests tools` / `mypy src` | all checks passed / no issues (21 files) |
| `reconcile.py --check`, `verify snapshots/standard-baseline`, `scan universe migration snapshots fixtures` | match (38 files) / verified / 0 findings |
| ADG `ruff check`, `ruff format --check`, `mypy app tests` | passed / 414 files formatted / no issues (413 files) |
| ADG backend, `pytest -m "not smoke"` | **6,025 passed**, 124 skipped (base: 6,021) |
| ADG backend, `pytest -m smoke`, PostgreSQL 16 | **1,298 passed, 1 xfailed** (§10) |
| ADG frontend: `npm run lint`, `typecheck`, `test`, `build`, `cf:typecheck` | clean, clean, **1,510 passed** (44 files), built, clean |

New tests in synthetic-enterprise (`test_adg_projection.py`) check:

- the version and the record files;
- the Genesee Press organization and directory;
- the department and team slugs;
- the employee values (`GP-`, mail, UPN, SID, objectGUID, transfers, the Brindlewood tag);
- the group fields (owners, the application token, deletion);
- the `sec`/`di` naming rules;
- that a `di-*` group projects unchanged;
- the minimal fixture.

New tests in ADG check:

- the lock and verification;
- **an edited vendored file is refused**;
- canon and vendor import no network module;
- the estate scope (521 → 206, 2,440, 192 nestings, 4,273 edges);
- legacy spellings are draw keys only and appear nowhere in the manifest;
- canonical ids and objectGUIDs come from the projection;
- no forbidden or former value reaches the canon;
- the Windows oracle fixture holds no machine SID.

## 10. Full ADG DB suite

`ADG_RUN_SMOKE_TESTS=1 python -m pytest -q -m smoke`, run locally against PostgreSQL 16 the
same way as the CI `Database tests` step: **1,298 passed, 1 xfailed** (31 min 38 s). This
includes `test_genesee_press_demo.py` (51), the graph, risk, comparison-bounds and coverage
suites that read the demo estate, and every other database test.

## 11. Machine-captured fixture (U-02)

`backend/tests/fixtures/windows/real-directory-access.json` is a measurement of a real
Windows 11 workstation. It was used only by `tests/validation/test_windows_oracle.py`, an
access-engine oracle test, never by demo data. It never entered the synthetic world:
`DENIED_SOURCES` in `tools/reconcile/extract.py`, and the reconciled-universe test asserts it.

This lane removed the remaining dependency on the real machine identity:

- The workstation's account-domain identifier `S-1-5-21-244598355-…`, in 24 occurrences, is
  replaced by the synthetic `S-1-5-21-1000000001-1000000002-1000000003`. A `sanitized` block
  records it.
- Access masks, SDDL structure, well-known SIDs and the synthetic unresolvable trustee are
  untouched, so all 34 oracle tests pass unchanged.
- A new test fails on any other domain SID.
- The probe README says how to sanitize a future capture.

The raw capture remains in Git history before this branch. Rewriting history is out of scope
and an owner decision.

## 12. Runtime and exportability

- The demo reads only `backend/vendor/…`. No module under `app/` opens a network connection
  or starts a process other than the migration; `TestNoProductionCodePath` holds.
- Every Docker image copies `backend/`, so the vendored files ship in the backend, compose and
  Render images.
- **No local-only state:** no generated file is uncommitted. The tests regenerate and compare
  the manifest, reset evidence and fingerprints.
- **No undocumented step:** re-vendoring is one script with a clean-checkout check.
- **No source-history dependence:** the extraction parity tools read the legacy commit, but
  only as evidence.
- **No real enterprise data:** domains are `.example`, and SIDs are synthetic.

## 13. Demo repository and deployment observations (no cutover performed)

| | State |
|---|---|
| `40ozs/adg-demo` | **empty**: no commits and no refs (`git ls-remote` returns nothing) |
| Current deployment source | `40ozs/adg`, GitHub Actions `cloudflare-staging.yml` on push to `deploy/ADG-HOSTED-NEXT16-01` (`f730d99`). It deploys to Render (Next.js and the API image) and the Cloudflare Worker, with Supabase `adg_demo`. |
| Drift | The deploy branch is 19 commits behind `main` and 0 ahead (ADG-UI-RECONCILE-01 and reports). This lane widens the gap; nothing was deployed. |

**Work for Prompt 10** (making `adg-demo` the deployment source):

1. Leave `feature/SYNTH-ADG-01` merge-ready. Merging it into ADG `main` needs the owner's
   separate authorization; this list is not that authorization. If the integration lane
   re-pins a newer snapshot, run `scripts/vendor-synthetic-enterprise.py` and regenerate the
   manifest; the tests show any drift.
2. Set the hosted `ADG_DEMO_DATASET` to `genesee-press-v1` (or unset it). This is a Render
   service variable, if one is set there. `null-meridian-v1` is no longer registered and the
   API refuses to start with it.
3. At cutover only (no deployment during cross-app integration), deploy with
   `workflow_dispatch` `bootstrap: true`: the hosted database's marker says
   `null-meridian-v1`, so `verify` refuses it until it has been reset.
   Expected values:
   - Demo Mode fingerprint `4667d3cf…9b270f08`;
   - logical fingerprint `b4b93b65…e59445d0`;
   - 23/23 probes;
   - 9,386 findings.
4. Re-capture the hosted browser evidence and screenshots, which carry the former names.
5. Create the `adg-demo` release from a **sanitized, approved tree export** of the ADG source
   SHA plus the pinned synthetic-enterprise SHA (from the lock). It must not mirror ADG's Git
   history, which still holds the raw U-02 capture (§11). Point the workflow, Render and the
   Worker at `adg-demo`, and retire the deploy branch.

## 14. Limitations

- **D-ADG-02:** ADG shows 206 of the 521 canonical directory groups (§6). This is an accepted
  application subset, not an open item; it changes only for a concrete scenario (§15).
- **D-ADG-01:** the draw seed and labels keep their v1 spellings. A spelling-free
  re-key is a new dataset with new counts.
- **Shared-file conflicts:** `snapshots/standard-baseline/manifest.json` and two lines of
  `tests/test_reconciled_universe.py` are also touched by the other lanes. They are resolved by
  the integration procedure in §15, never by `ours`/`theirs` choices on generated files.
- **Not re-captured:** hosted evidence and screenshots (no deployment in this lane).
- **Untouched history:** historical ADG reports and handoffs keep their Null Meridian names.
- **Not deleted:** the U-02 raw capture remains in Git history, so `adg-demo` must be a
  sanitized export, not a mirror (§13).

## 15. Owner integration guidance (recorded after the lane)

Recorded on 2026-10-27, after the owner accepted the verdict. The lane's code is unchanged;
these rules bind the cross-application and cutover lanes.

**Final lane SHAs** (the heads when the verdict was given; this amendment adds one
documentation-only commit on top of each):

| Repository | `feature/SYNTH-ADG-01` |
|---|---|
| `40ozs/synthetic-enterprise` | `e062771e9456728fa27da79fadb0fa7741f4fa07` (code `71055a7`, baseline `6f016d8`) |
| `40ozs/adg` | `77014becbeedf5467ef4a16c542be92413641411` (code `5e7debb`, `28abcff`) |

**Projection scope.** ADG consumes 206 of the 521 canonical directory groups; the other 315
come from OGWA and are not required. ADG is not expanded to all canonical groups for
numerical consistency across applications. A group is added only when a concrete
cross-application scenario requires ADG to see it, and then its effect on the membership
graph, access routes, finding counts, reset fingerprints, golden outputs and the regression
gates below is measured and documented. A broad count change is not accepted without the
scenario requirement that explains it.

**Integrating the synthetic-enterprise branches.** Generated baseline artefacts are not
hand-merged:

1. integrate the four application-specific projection implementations;
2. resolve source-level test expectations intentionally (for ADG: the two lines in
   `tests/test_reconciled_universe.py`, and `tests/test_adg_projection.py`);
3. regenerate `snapshots/standard-baseline` with the snapshot CLI from the integrated source
   state (DEVELOPMENT.md, *The shared world*);
4. verify the resulting manifest (`verify snapshots/standard-baseline`);
5. re-pin each application's projection (for ADG: `scripts/vendor-synthetic-enterprise.py`
   from a clean checkout of the integrated commit, then regenerate
   `docs/demo/genesee-press.manifest.json`);
6. run the complete cross-app validation suite.

Generated output is derived from the integrated source, never assembled from `ours`/`theirs`
conflict choices.

**ADG regression gates** (each held at the end of this lane, §8):

| Gate | Value |
|---|---|
| Employees | 2,440, all preserved |
| ADG group membership graph | equal to ADG v1 after renames on every day 2016-10-01 to 2026-10-01; 206 groups, 192 nestings, 4,273 edges on the snapshot |
| Collector transcripts | 300 transcripts, 41,756 observations, equal after renames |
| Open findings | 9,386 |
| Probes | 23/23 |
| Comparison drill | 221 differences (`timed_out → incomplete → complete`) |
| Apollo investigation | unchanged (Modify `at_least`, same routes and open questions) |
| Reset fingerprints | deterministic across resets: Demo Mode `4667d3cf…9b270f08`, logical `b4b93b65…e59445d0` while the pinned projection is unchanged |

The canon, transcript and reset-diff tools in ADG's `SYNTH-ADG-01-evidence/tools/` re-check
them.

**Legacy draw internals.** The file estate's draw seed and labels keep their v1 spellings
(D-ADG-01). They are not demo-visible and are not changed for naming consistency. If one ever
becomes externally visible, that specific leak is fixed and its parity impact documented.

**Machine-captured fixture.** The active fixture uses a synthetic machine identifier, and a
regression test refuses any other. The raw capture is still in ADG's Git history, so the
`adg-demo` release is produced from a sanitized, approved tree export and never mirrors ADG's
history.

**Deployment.** ADG still deploys from `deploy/ADG-HOSTED-NEXT16-01` in its source
repository. Nothing is deployed during cross-app integration. Final cutover moves the
deployment source to `adg-demo`.

**Merges.** None is authorized. The cross-app and cutover lanes leave source branches
merge-ready unless the owner separately authorizes a merge.
