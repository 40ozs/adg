# ADG-CI-BASELINE-01 — Clear the backend CI blockers on `main`

**Status:** implemented, committed locally, **not pushed** · **Date:** 2026-10-10
**Starting SHA (`origin/main`):** `cc13ad27e11e538c849d4e0a53f2358e36454392`
**Branch:** `claude/dazzling-keller-x3tvdx` (the session's designated branch, created from
`origin/main` at the SHA above; it plays the role the brief named `fix/ADG-CI-BASELINE-01`)
**Code-fix SHA:** `e54e590` (the three code commits); this report is the commit on top of it.
**Previous handoff:** [p0-current-state-correctness.md](p0-current-state-correctness.md)

Baseline maintenance only. No Demo Mode / Null Meridian file is touched, and neither the
integration branch (`claude/adg-demo-null-meridian-integration-5biwm0` @ `32fcce9`) nor its
source branches (`claude/vibrant-gates-rhovf1` @ `28e5743`, `claude/inspiring-volta-6uzdsy`
@ `4512524`) were modified; they were fetched read-only to measure the merge interaction.

---

## 1. Environment — what CI actually resolves

CI (`.github/workflows/ci.yml`) runs `pip install -e ".[dev]"` with no lock file on Python
3.13, so every run takes the newest release allowed by `backend/pyproject.toml`.

| | Last CI run on `main` (2026-09-15, run 35009148121) | Resolved now (this work) |
| --- | --- | --- |
| Python | 3.13 | 3.13.16 |
| SQLAlchemy | 2.0.53 | **2.1.4** |
| mypy | 2.3.1 | 2.4.0 |
| ruff | 0.16.7 | 0.17.0 |
| pytest / pytest-asyncio | 9.1.1 / – | 9.1.1 / 1.4.0 |
| PostgreSQL | 16 (alpine service) | 16.15 (local, same user/password/db as CI) |

**What CI on `main` actually showed:** the backend job failed at *Type check* with **2**
errors (the two Windows ones below) and **never reached the database step**. With SQLAlchemy
2.1.4 now resolved, a third mypy error and the 80 `DISTINCT ON` failures appear, and the
latent benchmark credential failure (§4) sits behind them.

---

## 2. SQLAlchemy — before

**Version:** 2.1.4. **Suite:** `ADG_RUN_SMOKE_TESTS=1 python -m pytest -q -m smoke`
(CI's exact command), against unmodified `main`:

```
81 failed, 1063 passed, 5673 deselected, 1 xfailed, 68 warnings in 1191.10s (0:19:51)
```

Each of the 81 failing IDs was re-run with `--tb=line` and classified by its exact error:

| Count | Error | Cause |
| ---: | --- | --- |
| **80** | `sqlalchemy.exc.SADeprecationWarning: Passing expression to ``distinct`` to generate a DISTINCT ON clause is deprecated. Use instead the ``postgresql.distinct_on`` function as an extension.` | §3 |
| **1** | `psycopg.OperationalError: … FATAL: password authentication failed for user "adg"` | §4 |

**Root cause (the 80).** SQLAlchemy 2.1 deprecates `Select.distinct(*expr)` (PostgreSQL
`DISTINCT ON`) in favour of `Select.ext(postgresql.distinct_on(*expr))`.
`pyproject.toml` has `filterwarnings = ["error::DeprecationWarning:app.*"]`, and
`SADeprecationWarning` is a `DeprecationWarning` attributed to the calling `app` module, so
every request reaching one of the four call sites raised. Production would only have
logged a warning; the suite fails, correctly.

Raising sites, from the failure tracebacks:

| Call site | Method | Reached by |
| --- | --- | --- |
| `app/changes/repository.py:274` | `ChangeRepository._latest_before` | change feed, changes API, comparison, impact |
| `app/changes/repository.py:324` | `ChangeRepository.first_present_at` | change feed (container first sightings) |
| `app/ingestion/service.py:1793` | `IngestionService.latest_run_per_scope` | collection status/coverage, run completion → alerts, MVP end-to-end |
| `app/repositories/operations.py:117` | `OperationsRepository._latest_per_scope` (`latest_runs` / `latest_successes` / `latest_failures`) | operations API |

`app/repositories/resources.py:944` (`func.count(func.distinct(...))`) is SQL `COUNT(DISTINCT)`,
not `DISTINCT ON`, and is unaffected.

<details><summary>The 81 failing test IDs on <code>main</code> @ cc13ad2, SQLAlchemy 2.1.4</summary>

- `tests/benchmarks/test_access_benchmark_harness.py::TestItStillRunsEndToEnd::test_measure_builds_an_estate_and_times_every_shape`
- `tests/db/test_alerts.py::TestDeliveryFailureIsContained::test_a_scan_run_completes_even_when_the_alert_pipeline_raises[asyncio]`
- `tests/db/test_authorization.py::TestRolesAgainstRealData::test_a_viewer_reads_the_estate`
- `tests/db/test_change_comparison.py::TestTheyDisagreeAndBothAreRight::test_the_feed_reports_both_events`
- `tests/db/test_change_comparison.py::TestTheComparisonNeverCallsAGapAChange::test_an_object_not_covered_at_the_later_instant_is_counted_not_removed`
- `tests/db/test_change_comparison.py::TestTheComparisonNeverCallsAGapAChange::test_an_object_not_covered_at_the_earlier_instant_is_counted_not_added`
- `tests/db/test_change_comparison.py::TestTheComparisonNeverCallsAGapAChange::test_a_scoped_comparison_may_span_any_interval`
- `tests/db/test_change_comparison.py::TestAFirstObservationDoesNotBreakTheComparison::test_comparing_across_it_answers_rather_than_refusing`
- `tests/db/test_change_comparison.py::TestAFirstObservationDoesNotBreakTheComparison::test_the_api_returns_it_rather_than_a_422`
- `tests/db/test_change_feed.py::TestTheFirstScanIsNotAChangeReport::test_every_object_of_the_first_scan_is_a_first_sighting`
- `tests/db/test_change_feed.py::TestTheFirstScanIsNotAChangeReport::test_the_default_filter_reports_the_first_scan_as_empty`
- `tests/db/test_change_feed.py::TestTheFirstScanIsNotAChangeReport::test_the_summary_still_counts_them_so_the_exclusion_is_visible`
- `tests/db/test_change_feed.py::TestWhatFridayDid::test_granting_everyone_read_is_reported_as_an_addition`
- `tests/db/test_change_feed.py::TestWhatFridayDid::test_a_change_reports_the_window_it_happened_in_not_the_scan_time`
- `tests/db/test_change_feed.py::TestAnAclEditIsOneEditAgain::test_the_tightening_is_paired_into_a_single_narrowing`
- `tests/db/test_change_feed.py::TestAnAclEditIsOneEditAgain::test_both_halves_of_the_edit_point_at_it`
- `tests/db/test_change_feed.py::TestAnAclEditIsOneEditAgain::test_the_new_everyone_entry_is_not_swept_into_the_edit`
- `tests/db/test_change_feed.py::TestScopes::test_a_share_scope_excludes_the_directory_group`
- `tests/db/test_change_feed.py::TestScopes::test_a_share_scope_reaches_the_ntfs_aces_beneath_it`
- `tests/db/test_change_feed.py::TestFilteringAndPaging::test_the_default_filter_keeps_the_security_changes`
- `tests/db/test_change_feed.py::TestFilteringAndPaging::test_a_minimum_severity_filters_by_rank`
- `tests/db/test_change_feed.py::TestFilteringAndPaging::test_the_summary_says_how_many_the_filter_hid`
- `tests/db/test_change_feed.py::TestFilteringAndPaging::test_paging_returns_every_change_exactly_once`
- `tests/db/test_change_feed.py::TestFilteringAndPaging::test_changes_come_back_newest_first`
- `tests/db/test_change_feed.py::TestTheWindowIsRequiredAndBounded::test_the_upper_bound_is_exclusive_so_adjacent_windows_partition`
- `tests/db/test_changes_api.py::TestTheFeed::test_it_answers_what_changed_since_thursday`
- `tests/db/test_changes_api.py::TestTheFeed::test_every_change_carries_both_ends_of_its_window`
- `tests/db/test_changes_api.py::TestTheFeed::test_it_echoes_the_filter_that_produced_the_list`
- `tests/db/test_changes_api.py::TestTheFeed::test_the_acl_edit_comes_back_as_one_edit_with_both_masks`
- `tests/db/test_changes_api.py::TestTheFeed::test_the_rights_view_keeps_the_mask_authoritative`
- `tests/db/test_changes_api.py::TestTheFeed::test_a_scope_narrows_the_page`
- `tests/db/test_changes_api.py::TestTheFeed::test_paging_is_by_opaque_cursor`
- `tests/db/test_changes_api.py::TestTheFeed::test_a_page_says_whether_the_scan_budget_ended_it`
- `tests/db/test_changes_api.py::TestTheSummarySaysWhatThePageIsNotShowing::test_it_counts_the_window_before_the_filter`
- `tests/db/test_changes_api.py::TestTheSummarySaysWhatThePageIsNotShowing::test_it_names_the_most_severe_thing_in_the_window`
- `tests/db/test_changes_api.py::TestTheSummarySaysWhatThePageIsNotShowing::test_it_counts_the_first_sightings_the_feed_hides`
- `tests/db/test_changes_api.py::TestTheTimelineAndTheComparison::test_the_comparison_reports_what_it_could_not_compare`
- `tests/db/test_changes_api.py::TestTheImpactEndpoint::test_a_client_hands_back_the_at_it_was_given`
- `tests/db/test_changes_api.py::TestTheImpactEndpoint::test_it_reports_both_answers_with_their_outcomes`
- `tests/db/test_changes_api.py::TestTheImpactEndpoint::test_granting_everyone_on_the_file_system_changes_nothing_over_smb`
- `tests/db/test_changes_api.py::TestTheImpactEndpoint::test_the_same_grant_does_broaden_access_for_anyone_logged_on_locally`
- `tests/db/test_changes_api.py::TestAuthorization::test_a_viewer_may_read_the_feed`
- `tests/db/test_changes_api.py::TestAuthorization::test_the_impact_endpoint_requires_access_read_not_changes_read`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_an_untouched_database_reports_no_data`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_clean_runs_report_healthy`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_a_failed_run_makes_the_picture_failed`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_a_later_success_supersedes_an_earlier_failure`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_an_earlier_success_does_not_hide_a_later_failure`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_reported_errors_make_a_success_untrustworthy`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_one_collector_on_two_hosts_with_no_target_is_still_one_collector`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_a_failure_on_one_server_is_not_hidden_by_a_success_on_another`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_a_concern_names_the_target_it_is_about`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_the_healthy_summary_names_each_collector_once`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_a_later_success_on_the_same_target_does_supersede_the_failure`
- `tests/db/test_collection_status_api.py::TestCollectionStatus::test_a_viewer_may_read_it`
- `tests/db/test_current_state_presence.py::TestTheApiSurfacesDoNotShowExpiredState::test_a_removed_share_leaves_the_inventory_endpoints[asyncio]`
- `tests/db/test_history_migration.py::TestTheProductStillWorksOnBackfilledHistory::test_current_state_queries_are_unaffected_by_the_backfill`
- `tests/db/test_mvp_end_to_end.py::TestTheCollectorToStoreSeam::test_the_object_counts_match_what_the_estate_describes[asyncio]`
- `tests/db/test_mvp_end_to_end.py::TestTheCollectionIsJudgeable::test_the_coverage_verdict_is_failed_because_a_server_is_unobserved[asyncio]`
- `tests/db/test_mvp_end_to_end.py::TestTheCollectionIsJudgeable::test_each_server_appears_as_its_own_scope[asyncio]`
- `tests/db/test_mvp_end_to_end.py::TestTheCollectionIsJudgeable::test_the_operator_page_names_the_scope_that_has_never_succeeded[asyncio]`
- `tests/db/test_mvp_end_to_end.py::TestTheCollectionIsJudgeable::test_the_error_summary_groups_what_went_wrong[asyncio]`
- `tests/db/test_mvp_end_to_end.py::TestTheCollectionIsJudgeable::test_the_partial_run_reports_what_it_did_not_deliver[asyncio]`
- `tests/db/test_mvp_end_to_end.py::TestTheViewsARealReaderSees::test_an_auditor_reaches_every_page_of_the_product[asyncio]`
- `tests/db/test_operations_api.py::TestTheEmptyState::test_an_untouched_database_reports_no_data_rather_than_health[asyncio]`
- `tests/db/test_operations_api.py::TestTheEmptyState::test_the_counts_are_present_even_when_everything_is_zero[asyncio]`
- `tests/db/test_operations_api.py::TestScopesAndTheirOutcomes::test_each_server_is_its_own_scope[asyncio]`
- `tests/db/test_operations_api.py::TestScopesAndTheirOutcomes::test_a_scope_that_has_never_succeeded_is_named[asyncio]`
- `tests/db/test_operations_api.py::TestScopesAndTheirOutcomes::test_a_failure_after_a_success_keeps_both[asyncio]`
- `tests/db/test_operations_api.py::TestScopesAndTheirOutcomes::test_a_partial_run_counts_as_the_last_success[asyncio]`
- `tests/db/test_operations_api.py::TestScopesAndTheirOutcomes::test_a_run_with_no_target_is_still_one_scope[asyncio]`
- `tests/db/test_operations_api.py::TestScopesAndTheirOutcomes::test_an_unreconciled_scope_makes_a_success_incomplete[asyncio]`
- `tests/db/test_operations_api.py::TestScopesAndTheirOutcomes::test_an_incremental_run_is_not_marked_incomplete_for_not_reconciling[asyncio]`
- `tests/db/test_operations_api.py::TestTheErrorSummary::test_errors_are_grouped_by_code_across_runs[asyncio]`
- `tests/db/test_operations_api.py::TestTheErrorSummary::test_the_same_code_on_many_targets_is_one_row_with_examples[asyncio]`
- `tests/db/test_operations_api.py::TestTheErrorSummary::test_a_code_reported_by_two_collectors_is_marked_widespread[asyncio]`
- `tests/db/test_operations_api.py::TestTheVerdictIsTheSameOneEveryPageSees::test_health_matches_the_coverage_endpoint[asyncio]`
- `tests/db/test_operations_api.py::TestWhatItCosts::test_the_page_costs_the_same_on_one_server_and_on_twelve[asyncio]`
- `tests/db/test_operations_api.py::TestWhatItCosts::test_it_reads_the_run_table_a_fixed_number_of_times[asyncio]`
- `tests/db/test_operations_api.py::TestWhatItCosts::test_the_error_summary_is_one_statement_however_many_codes[asyncio]`
- `tests/db/test_operations_api.py::TestWhoMayReadIt::test_a_viewer_may_read_it[asyncio]`

</details>

---

## 3. SQLAlchemy — fix

**New:** `backend/app/sql_compat.py` — one function, `distinct_on(statement, *expressions)`:

```python
_DISTINCT_ON_EXTENSION: Final[Any] = getattr(postgresql, "distinct_on", None)

def distinct_on(statement: _SelectT, *expressions: ColumnElement[Any]) -> _SelectT:
    if _DISTINCT_ON_EXTENSION is None:          # SQLAlchemy 2.0
        return statement.distinct(*expressions)
    return statement.ext(_DISTINCT_ON_EXTENSION(*expressions))   # 2.1+
```

Needed because `pyproject.toml` still accepts `sqlalchemy>=2.0.30` and `Select.ext` /
`postgresql.distinct_on` do not exist on 2.0. Once the floor is raised to 2.1 the helper can
be inlined as `.ext(distinct_on(...))` at each call site.

**Semantics before / after.** Each call site keeps its `DISTINCT ON` expressions and its
`ORDER BY` exactly:

| Method | `DISTINCT ON` | `ORDER BY` | Meaning |
| --- | --- | --- | --- |
| `latest_run_per_scope` | `collector, coalesce(target, '')` | same, `started_at DESC, run_id DESC` | newest run per scope; NULL targets are one scope |
| `_latest_per_scope` | `collector, coalesce(target, '')` | same, `started_at DESC, run_id DESC` | same, optionally after a status filter |
| `_latest_before` | `object_kind, object_key` | same, `valid_from DESC` | newest version strictly before the asked one |
| `first_present_at` | `object_kind, object_key` | same, `valid_from ASC` | oldest *present* version |

**Proof that nothing else moved.** Every `DISTINCT ON` statement sent to PostgreSQL was
captured with a `before_cursor_execute` listener while running the regression tests:

```
main  @ cc13ad2, SQLAlchemy 2.0.54   ┐
fix   @ e54e590, SQLAlchemy 2.1.4    ├─ 5 distinct statements each — byte-identical (diff empty)
fix   @ e54e590, SQLAlchemy 2.0.54   ┘
```

Not done, on purpose: no warning filter was added or relaxed, SQLAlchemy was not pinned, no
test was skipped or marked, and the queries were not rewritten (e.g. to window functions).

**Regression tests.**

- `tests/db/test_distinct_on_queries.py` (8 tests, `smoke`): seeds `collector_sources`,
  `scan_runs` and `object_versions` directly, so each test controls every row a query could
  pick, then asserts which row wins per group: four scopes built so a per-kind reduction,
  per-source NULL handling, or the wrong tie-break each picks a different run; the latest
  success skipping a newer failure and counting `partial`; the newest *strictly earlier*
  version behind an observation gap (not the oldest, not itself, not a later one); a leading
  tombstone skipped by `first_present_at`, and a tombstone-only object omitted.
  Against **`main` on 2.1.4: 7 failed, 1 passed** (the one that passes never reaches
  `DISTINCT ON`). Against **`main` on 2.0.54: 8 passed** — so the tests encode the
  pre-existing behaviour, not a new one.
- `tests/test_sql_compat.py` (3 tests, hermetic): the rendered SQL, no warning of any kind
  under `simplefilter("error")`, and the input statement left unmodified. Passes on 2.0.54
  and 2.1.4.

---

## 4. The 81st failure — benchmark smoke test credentials

`tests/benchmarks/test_access_benchmark_harness.py::TestItStillRunsEndToEnd::test_measure_builds_an_estate_and_times_every_shape`

`bench_database_url()` derives `<database>_bench` from `build_settings().database_url`. By the
time the test runs, the autouse `isolated_environment` fixture has deleted every `ADG_*`
variable, so that is the built-in `DEV_DATABASE_URL` (`adg:adg_dev_password@localhost`).
That works against the compose stack and fails against CI's service (`adg_ci_password`).
It has never shown in CI only because CI never got past mypy.

**Fix (test file only):** the URL is read at import time — exactly how `SMOKE_ENABLED` beside
it and `tests/db/conftest.py` already handle the same fixture — and restored with
`monkeypatch.setenv` for that one test. The benchmark harness itself is untouched.

---

## 5. mypy — before

`python -m mypy app tests` (CI's command), mypy 2.4.0, SQLAlchemy 2.1.4, `main` @ cc13ad2:

```
app/runtime.py:49: error: Module has no attribute "WindowsSelectorEventLoopPolicy"  [attr-defined]
tests/test_runtime.py:31: error: Module has no attribute "ProactorEventLoop"; maybe "AbstractEventLoop" or "SelectorEventLoop"?  [attr-defined]
app/changes/repository.py:398: error: Incompatible types in assignment (expression has type "Select[*tuple[Never, ...]]", variable has type "Select[Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, *tuple[Any, ...]]")  [assignment]
Found 3 errors in 3 files (checked 363 source files)
```

The first two are what CI reported on 2026-09-15 (mypy 2.3.1, SQLAlchemy 2.0.53); the third
needs SQLAlchemy 2.1's variadic `Select` stubs.

## 6. mypy — fixes

| Error | File | Correction |
| --- | --- | --- |
| `WindowsSelectorEventLoopPolicy` | `app/runtime.py` | Typeshed declares it under `sys.platform == "win32"`; mypy cannot narrow through the module's `WINDOWS` constant. The guard in `install_selector_event_loop_policy` is now `sys.platform == "win32"` itself. Same condition, same Windows behaviour. |
| `ProactorEventLoop` | `tests/test_runtime.py` | The test is `skipif`'d off Windows, which mypy does not see. The one assertion is under the same platform check; on Windows it still runs unchanged. |
| `Select[...]` assignment | `app/changes/repository.py` (`present_at`) | `statement: Select[Any] = select(*VERSION_COLUMNS)...` — the annotation `_window()` and `_restrict()` already carry. |

No `# type: ignore`, no exclusion, no configuration change.

---

## 7. Validation

All on the committed tree (`e54e590`), with CI's commands, from `backend/`.

| Gate | Command | Result |
| --- | --- | --- |
| DB, SQLAlchemy **2.1.4** | `ADG_RUN_SMOKE_TESTS=1 python -m pytest -q -m smoke` | **1152 passed, 0 failed, 0 skipped, 1 xfailed** (20m38s) — no `SADeprecationWarning` in the run |
| DB, SQLAlchemy **2.0.54** | same, `sqlalchemy<2.1` | **1152 passed, 0 failed, 0 skipped, 1 xfailed** (20m30s) |
| Hermetic, 2.1.4 | `python -m pytest -q -m "not smoke"` | **5552 passed, 124 skipped** (73.5s) |
| Hermetic, 2.0.54 | same | **5552 passed, 124 skipped** |
| Lint | `ruff check .` | **All checks passed** |
| Format | `ruff format --check .` | **367 files already formatted** |
| mypy (2.1.4 — CI) | `mypy app tests` | **Success: no issues found in 366 source files** |
| Frontend | `npm install && npm run lint && typecheck && test && build` (Node 22) | **pass** — 31 files / 1126 tests, build OK (no frontend file changed) |
| Compose | `docker compose config -q` | **pass** |
| Collector | `scripts/collector-test.ps1` | not reproducible here — see §9 (no collector file changed) |

Baselines on `main` for comparison: hermetic **5549 passed, 124 skipped**; DB **81 failed,
1063 passed, 1 xfailed**. 1152 = 1063 + the 81 now fixed + the 8 new regression tests. The
hermetic +3 are `tests/test_sql_compat.py`; nothing regressed. The one xfail is the same on
`main` and here: `test_access_performance.py::…::test_a_fixed_page_should_not_expand_a_growing_set`,
a documented known defect (`docs/architecture/effective-access-limits.md`).

---

## 8. Files changed

| File | Change |
| --- | --- |
| `backend/app/sql_compat.py` | **new** — `distinct_on()` helper |
| `backend/app/changes/repository.py` | two call sites through the helper; one `Select[Any]` annotation |
| `backend/app/ingestion/service.py` | one call site through the helper |
| `backend/app/repositories/operations.py` | one call site through the helper |
| `backend/app/runtime.py` | platform guard spelled `sys.platform == "win32"` |
| `backend/tests/test_runtime.py` | assertion under the same platform check |
| `backend/tests/benchmarks/test_access_benchmark_harness.py` | restore the configured database URL for the end-to-end test |
| `backend/tests/db/test_distinct_on_queries.py` | **new** — 8 regression tests |
| `backend/tests/test_sql_compat.py` | **new** — 3 helper tests |
| `docs/handoffs/adg-ci-baseline-01.md` | this report |

Commits: `ef00818` (DISTINCT ON + tests), `f6fef9b` (mypy), `e54e590` (benchmark URL), then
this report.

---

## 9. Interaction with the Demo integration

- **Overlap:** none. The integration branch (`32fcce9`, 82 files vs `main`) touches none of
  the files above. Its `runtime`-named files are different modules (`app/runtime_mode.py`,
  `app/api/runtime.py`, `tests/demo_mode/test_runtime_mode.py`).
- **New `DISTINCT ON` sites:** none. The integration branch has exactly `main`'s four
  `.distinct(expr)` call sites, all fixed here.
- **Simulated merge** (`git merge-tree` of this branch with `32fcce9`; local dangling commit,
  no ref, nothing pushed): **no conflicts**; on the merged tree `ruff check`, `ruff format
  --check` and `mypy app tests` (397 files) are all clean, and the only remaining
  `.distinct(expr)` is the helper's own 2.0 branch.
  The hermetic suite on that merged tree: **5765 passed, 124 skipped** (SQLAlchemy 2.1.4).
  Its database suite was not run here; that is the refreshed integration's own gate.
- `RiskFindingRepository.by_keys` (chunked on the Null Meridian branch) and
  `AlertRepository.by_keys` were not touched — neither is involved in these failures.

---

## 10. Known limitations

1. **mypy is clean against the SQLAlchemy CI installs (2.1).** Run against 2.0 stubs it
   reports 2 errors in `app/sql_compat.py` (`Select.ext` does not exist in 2.0). The 2.1 call
   is kept typed on purpose so a change to that API is caught; 2.0 *runtime* behaviour is
   fully tested. Raising the floor to `sqlalchemy>=2.1` would remove both the helper and this
   note — a dependency decision left to the owner.
2. **Collector (Pester) suite not reproduced faithfully.** CI runs it on `windows-latest`.
   Here, PowerShell 7.4.6 with Pester 6.2.0 (from NuGet; PSGallery is blocked by the
   environment proxy) on Linux: 572 passed, 105 failed, 88 skipped. Every failure class is a
   Windows-only API — `CimCmdlets` not loadable, `WindowsPrincipal` "not supported on this
   platform". No collector file is changed; on `main` the Windows job passed (run
   35009148121).
3. **Unpinned CI dependencies.** This failure arrived with a new upstream release, not a
   commit. The next breaking release can redden `main` the same way. A constraints/lock file
   for CI is the durable fix — out of scope here.
4. **Pre-existing warnings, not failures:** a `SyntaxWarning` (`"CORP\jdoe"`) in
   `tests/db/test_ntfs_resources.py:674`, and Starlette deprecations (`httpx` test client,
   `HTTP_422_UNPROCESSABLE_ENTITY`). None is attributed to `app.*`, so none fails today.
5. **Pre-existing, found while writing the regression tests, not fixed here:**
   `ChangeRepository._latest_before` returns one row per object (the `DISTINCT ON` is over
   the `OR` of every outstanding version's bound). When one `predecessors()` call holds two
   *non-adjacent* versions of the same object, only the later one gets its predecessor.
   Probed on this branch: versions Mon–Wed, Thu–Fri, Next Mon–; asking about Thu and Next Mon
   together returns only `Next Mon → Thu`; Thu gets nothing. `main` emits the identical SQL,
   so the behaviour is unchanged by this work. It would affect a change-feed page carrying
   two observation-gap changes of one object. Recommended as a separate follow-up.

## Next step

Push this branch and open a PR to `main` (not done here — not authorized); once CI is green,
merge, then refresh `claude/adg-demo-null-meridian-integration-5biwm0` onto the new `main`
and re-run its gates.
