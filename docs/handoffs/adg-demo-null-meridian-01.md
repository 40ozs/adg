# ADG-DEMO-NULL-MERIDIAN-01 — the Null Meridian synthetic estate and Demo Mode

**Status:** implemented · **Date:** 2026-10-10 · **Branch:** `claude/inspiring-volta-6uzdsy` ·
**Starting SHA:** `cc13ad2ebe78f3d798c63e7ccd6066af6b9ceb5c`

ADG now has a first-class Demo Mode. `ADG_RUNTIME_MODE=demo` plus one command —
`python -m app.demo.null_meridian reset` — puts a dedicated database into the canonical state of
**Null Meridian**, a fictional ten-year-old, 1,800-person company, by replaying three scan
generations through ADG's real ingestion endpoints. Everything downstream — history,
current-state presence, membership graph, effective access and explanations, search, risk,
simulation, the UI — is the production code reading that data. There is no separate demo
application, no hand-inserted dashboard row, and no change to the schema or the API contract.

The design and every number are in [`docs/demo/null-meridian.md`](../demo/null-meridian.md).
This file records what was built, what was decided, what was measured, and what is not done.

---

## 1. What was built

| Path | What |
| --- | --- |
| `backend/app/demo/null_meridian/canon.py` | The company: seeded SHA-256 draw stream, 14 departments, 1,800 active + 640 former employees, 206 groups across four naming eras, nesting, memberships, lifecycle, deterministic ids/SIDs/GUIDs |
| `…/names.py` | Name pools (part of the canon; any edit is a new seed) |
| `…/estate.py` | 33 servers, 91 shares, 824 folders; authored golden folders + seeded organic ACLs; the estate as a scan on any date would find it |
| `…/generations.py` | Three generations (2026-09-03, 09-17, 10-01) → 300 contract-v1 transcripts |
| `…/scenarios.py` | Golden scenarios A–H, resolved from the canon |
| `…/risk.py` | The demo's risk configuration (sensitive folders; one threshold) |
| `…/manifest.py` | Summary manifest and the full identity canon for OGWA |
| `…/reset.py` | The guarded reset: refuse, migrate, mark, empty, seed in-process, evaluate, fingerprint |
| `…/__main__.py` | `reset`, `manifest`, `transcripts`, `stats` |
| `backend/app/config.py` | `runtime_mode` (`ADG_RUNTIME_MODE`) and its startup guards; demo risk configuration default |
| `backend/app/main.py` | `X-ADG-Runtime-Mode: demo` on every demo response; mode in the startup log |
| `backend/app/demo/estate.py`, `transcripts.py` | Materializer made reusable; domain SID and run-id namespace parameterized (defaults unchanged — the existing demo's transcripts are byte-identical) |
| `backend/app/repositories/risk.py` | **Bug fix**: `by_keys` chunked (see §3) |
| `docs/demo/null-meridian.md`, `.manifest.json`, `.canon.json` | Documentation and machine-readable contracts |
| `scripts/demo-reset.ps1`, `.env.example`, `docker-compose.yml`, `README.md` | Operator plumbing |

## 2. Decisions

1. **Through the ingestion API, in-process.** The reset posts every observation to the real
   `scan-runs` endpoints via a 60-line ASGI poster (`InProcessPoster`), not `httpx` (a dev
   dependency the API image does not ship) and not SQL. Reconciliation, tombstones and history
   therefore come from the production code paths.
2. **A demo database is named *and* marked.** The name must contain `demo` (startup check) and
   the database carries `COMMENT ON DATABASE … 'adg-demo:null-meridian-v1'`; a database with
   scan runs and no marker is refused. A comment, so the schema is untouched.
3. **Creation dates, departments, titles, managers and owners live in the canon, not in ADG.**
   The collector contract has no field for them and the brief forbids contract changes for the
   demo. ADG carries department and naming era as OUs.
4. **Unresolved SIDs, two ways.** Accounts deleted before ADG's first scan are reported by the
   AD run as `unresolved/deleted` with a last known name (the existing demo's precedent);
   accounts deleted *between* scans are tombstoned and their SIDs simply stop resolving.
5. **One risk threshold moved for the demo** (`redundant_access_paths.minimum_paths` 2 → 4).
   At the shipped threshold the estate produces ~175,000 informational findings (the
   legacy-plus-standard pair on every migrated share), the evaluation takes eleven minutes and
   nothing else is visible. Documented as operator tuning in `risk.py` and the demo doc.
6. **Leaver debt is narrow.** About 10% of leavers keep their team or department group; at
   25% with all groups, `disabled_principal_retains_access` produced ~8,000 findings.
7. **Final ACL distribution** (target in brackets): group-based 70.7% [65–75], direct-user
   18.4% [15–20], mixed 10.9% [10–15]; posture ordinary 77.4% [70–80], questionable 17.2%
   [15–20], risky 5.3% [5–10]. All asserted in tests.

## 3. A product bug the demo found

`RiskFindingRepository.by_keys` built one `IN (…)` list with a bind parameter per finding
key. A full evaluation of this estate at the shipped thresholds matched 187,523 findings and
PostgreSQL refused the statement (`number of parameters must be between 0 and 65535`), failing
the **whole** evaluation. Any estate of this size would hit it. Fixed by chunking (5,000 keys
per statement); regression test
`tests/db/test_risk_findings.py::TestLookupByKeyAtScale` looks up 70,000+ keys.

`alerts.py` `by_keys` has the same shape but is bounded by the number of findings an
evaluation *opens* and alerts on; not changed here, noted in §7.

## 4. Counts (snapshot, 2026-10-01)

| | |
| --- | ---: |
| Active employees | 1,800 |
| Former employees (historical) | 640 (85 disabled accounts, 555 deleted) |
| Departments | 14 |
| Groups | 205 on the snapshot (+1 deleted mid-September) |
| — legacy / team / transitional / standard era | 54 / 61 / 25 / 65 |
| — modern (standard-conforming) / legacy-named | 62 / 143 |
| Membership edges | 4,273 direct (4,081 person, 192 nested) + 1,885 primary-group |
| Maximum nesting depth | 7 (6 on any chain that grants file access) |
| Servers / shares / admin shares | 33 / 91 / 33 |
| Folders (NTFS resources) | 824 |
| NTFS ACEs / share ACEs | 4,111 / 606 |
| ACL objects with explicit entries | 376 |
| Explicit direct-user ACEs / group ACEs | 136 / 518 |
| Scan runs / observations | 300 / 41,756 |

## 5. Tests

New: `tests/demo/test_null_meridian_canon.py`, `tests/demo/test_null_meridian_estate.py`,
`tests/demo/test_demo_mode.py` (hermetic, 92 tests) and
`tests/db/test_null_meridian_demo.py` (real reset ×2, current-state semantics, golden
scenarios through the API, boundary refusals), plus the `by_keys` regression test.

Each brief requirement and where it is asserted:

| Requirement | Test |
| --- | --- |
| exactly 1,800 active employees | `canon::test_exactly_1800_active_employees`, `db::test_exactly_1800_active_employees_are_enabled_in_adg` |
| deterministic generation | `canon::TestItIsDeterministic`, `estate::TestItIsDeterministic` |
| nullmeridian.info consistency | `canon::test_mail_and_upn_are_in_the_canonical_domain`, `db::…enabled_in_adg` |
| stable synthetic IDs | `canon::test_the_digests_are_pinned`, `test_uuids_are_uuid5…`, `test_rids_follow_creation_order` |
| no production identifiers | `canon::test_no_identifier_comes_from_outside_the_canon`, `estate::test_synthetic_host_names_and_paths` |
| modern naming after 2023-10-01 | `canon::test_the_standard_predominates_after_2023_10_01`, `test_every_group_is_named_for_its_era` |
| historical naming variation | `canon::test_older_groups_vary_in_convention` |
| Okta adoption 2021-10-01 | `canon::test_the_company_constants`, `test_okta_appears_only_after_okta_was_adopted` |
| direct-user / group / mixed ACLs | `estate::test_assignment_distribution_is_in_the_documented_bands` |
| nested membership; deep hierarchy | `canon::TestTheHierarchy`, `db::test_g_the_six_group_chain` |
| modern healthy hierarchy | `estate::test_a_modern_healthy_share`, `db::test_a_the_design_share_names_no_person` |
| all golden scenarios | `estate::TestGoldenScenarios` (A–H), `db::TestTheGoldenScenariosThroughTheApi` |
| current-state / tombstone semantics | `db::TestCurrentStateSemantics` |
| partial scans do not delete | `db::test_a_partial_scan_removes_nothing_even_what_is_really_gone`, `test_a_failed_scan_removes_nothing`, `test_current_state_is_exactly_what_the_scans_justify` |
| demo cannot contact AD/SMB/collector | `demo_mode::TestNoProductionCodePath`, `db::test_a_second_reset_reproduces…` (sockets refused, only alembic spawned) |
| reset reproduces the canonical dataset | `db::test_a_second_reset_reproduces_the_canonical_database` |

### Results

Local container, Python 3.13, PostgreSQL 16. Run once on the untouched starting commit (a clean
worktree with its own database) and on this branch, so the comparison is like for like.

| Suite | Starting commit | This branch |
| --- | --- | --- |
| `ruff check .` / `ruff format --check .` | — | clean / 378 files formatted |
| `mypy app tests` | 3 errors (pre-existing) | the same 3 (2 under SQLAlchemy 2.0.54) |
| hermetic (`-m "not smoke"`) | 5,549 passed, 124 skipped | **5,641 passed**, 124 skipped, 0 failed |
| database (`-m smoke`), SQLAlchemy 2.1.4 | 1,064 passed, **80 failed** | 1,085 passed, **80 failed — the identical 80** |
| database (`-m smoke`), SQLAlchemy 2.0.54 | — | **1,165 passed, 0 failed** |

The 80 failures under SQLAlchemy 2.1.4 are pre-existing (§7) and identical by test id on both
sides; no test was weakened, skipped or edited to pass. Frontend and collector code are
untouched; `docker compose config -q` passes with the new variable.

**Reset determinism.** Two consecutive resets of `adg_demo` produced the same fingerprint,
`db736602850a4a82936f861b68466e7c54b61a55192b04f6d062cbe8585c28a0`, the same ten table
digests and the same counts, in 140 s and 151 s
([`evidence/reset-1.json`](../demo/evidence/reset-1.json),
[`evidence/reset-2.json`](../demo/evidence/reset-2.json)). The DB test does the same with
Python sockets refused and compares fingerprints.

### Measured after a reset

| | |
| --- | ---: |
| reset duration (whole / seeding through the API) | 140–151 s / 96–104 s |
| runs (succeeded / partial / failed) | 300 (297 / 1 / 2) |
| observations applied | 41,756 |
| current principals / membership edges | 2,139 / 6,224 |
| current servers / shares / share ACEs | 33 / 124 / 606 |
| current NTFS resources / ACEs | 826 / 4,117 (824 observed on the snapshot + 2 kept by the partial scan) |
| object versions / open tombstones | 14,316 / 70 |
| risk findings matched / open | 9,666 / 9,386 (complete, no ceiling) |


## 6. Safety

No Active Directory, LDAP, SMB, WinRM or PowerShell collector was contacted or executed, and no
credential was used, while building or testing this. Nothing in `app/demo/null_meridian/`
imports a directory, SMB or HTTP client (asserted). The reset's only subprocess is
`alembic upgrade heads` (asserted at runtime), and a full reset runs with Python's socket
`connect` refused (asserted). The only database touched was a local PostgreSQL 16 in the
development container (`adg`, `adg_test`, `adg_demo`, `adg_demo_test`, `adg_base`). No
identifier is copied from a real system: every SID, GUID, name, mailbox, host and path is
generated from the seed, and the repository's other fixture identities are asserted absent.
Nothing was pushed, merged or deployed.

## 7. Known limitations

- **Pre-existing, environmental:** with SQLAlchemy 2.1.x (what `pip` resolves today for
  `sqlalchemy>=2.0.30`), `distinct(expr)` in `app/repositories/operations.py` raises a
  deprecation that `pyproject.toml` turns into an error, failing 80 DB tests on the
  **unchanged** starting commit as well; and `mypy` reports three errors on both (Windows-only
  asyncio attributes on Linux, SQLAlchemy 2.1 typing). Not addressed here — outside the brief.
- The collector contract has no `whenCreated`/department/title/manager/`managedBy`, so ADG
  cannot show group creation dates or owners; they are in the canon (OGWA's concern).
- OGWA does not yet contain Null Meridian. ADG defines canon v1 here; OGWA should import
  `docs/demo/null-meridian.canon.json` or reproduce it and compare digests.
- `redundant_access_paths` is tuned for the demo (decision 5). At shipped thresholds the risk
  pass works (after the §3 fix) but takes ~11 minutes and matches ~187k findings.
- The access-shaped risk rules count per (principal, resource), so ~10 disabled accounts that
  kept a team group account for most of the ~1,920 disabled-access findings.
- `AlertRepository.by_keys` has the same unchunked `IN` shape as the fixed method; it is
  bounded by alerts raised per evaluation, which the demo keeps small.
- The reset needs the demo database to exist; it does not `CREATE DATABASE`.
- Demo Mode has no UI banner; the API marks every response with `X-ADG-Runtime-Mode: demo`.
- The DB-backed demo tests add ~5–6 minutes to the smoke suite (two full resets).
