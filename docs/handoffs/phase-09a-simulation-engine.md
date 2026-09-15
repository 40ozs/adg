# Handoff — Phase 9A (`phase-09/01-simulation-engine.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-07a-history-model.md](phase-07a-history-model.md)
**Collector contract version after this phase:** `1.3` — unchanged by this phase.
**Derived-response contract:** `1.0` — unchanged.
**`docs/contracts/v1/openapi.json`:** unchanged by this phase. **No route was added, removed
or altered.**

> ## ⚠ This phase was implemented alongside another one, in the same working tree
>
> A second agent session was building phases 8 and 9's other tracks — incremental collection,
> a change feed, a risk engine and a governance model — in `C:\code\adg` at the same time as
> this phase. That is visible everywhere in this document and it changes how some of it should
> be read:
>
> * **Test and lint totals are not comparable to Phase 7A's.** Both phases' code is in the
>   tree. Figures attributable to *this* phase are stated separately and exactly.
> * **The migration graph branched, and this phase added a merge revision to unbreak it.**
>   See *Migration and compatibility notes*.
> * **The backend lint gate fails on files this phase did not touch.** Every failure is named
>   and attributed below.
>
> Nothing in this phase's own code depends on the other phase's, and nothing in it was
> modified by this phase.

## Scope completed

ADG can now answer *"if I take this group off the ACL, who loses access?"* — and answer it by
running the real effective-access engine over a proposed change, without touching Active
Directory, a share, an NTFS descriptor, or one row of its own collected state.

1. **An immutable overlay model** covering membership edges, NTFS ACEs, SMB ACEs and
   inheritance protection, with the two dispositions Windows offers for a directory's
   inherited entries.
2. **Overlay repositories** that wrap the two repositories `AccessService` already takes by
   injection, so the production engine answers the hypothetical question with **not one line
   changed**. The same seam Phase 7A used for a past instant.
3. **Applicability decided before anything is simulated**, so *"this changes nothing"* and
   *"this no longer applies"* — the same empty impact list — are different answers.
4. **Impact in six directions**, with the surviving routes enumerated by re-running the access
   check and the claim qualified by the engine's own certainty.
5. **Strict size and time bounds**, every exhausted one reported.
6. **Two tables** for proposals and their evaluations, which nothing else in ADG reads.
7. **Every result names the collection state it was measured against**, and staleness is an
   exact token comparison rather than a clock.
8. **201 tests** — 179 hermetic and 22 against PostgreSQL — including four independent
   proofs that nothing is mutated.

**No HTTP surface was added.** See *Intentionally deferred*.

---

## What the model is, in six sentences

A proposal is a frozen `SimulationOverlay` that names the objects it acts on rather than
carrying copies of them. A pair of overlay repositories applies it to rows as they are read,
in memory, returning new frozen records that are marked as invented — `source_key
= "simulated"`, the nil run id, the epoch, a `simulated|` key prefix. `AccessService` is then
run twice over the same session, once against the baseline pair and once against the overlay
pair, and the two answers are compared. A claim of loss inherits the simulated answer's
certainty, so an answer that is a lower bound raises `LOSS_MAY_NOT_HOLD` rather than asserting
that access is gone. Alternate routes are enumerated from the *simulated* explanation, which is
the only sound way: removing an Allow can reveal a redundant Allow behind it. Every report
carries the collection-basis token it was computed against, which moves if and only if a
collector has written something — so staleness is a fact, not a guess.

Full treatment: [`docs/architecture/simulation.md`](../architecture/simulation.md).

---

## Files and modules added or materially changed

### Added — the simulation layer

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/simulation/overlay.py` | 860 | Pure: the proposal, its validation, its digest, its versioned document |
| `backend/app/simulation/application.py` | 721 | Pure: applying an overlay to frozen records; canonical placement, renumbering, the inheritance toggle |
| `backend/app/simulation/repositories.py` | 502 | The two overlay repositories. Every public read is overlay-aware, delegated, or refused |
| `backend/app/simulation/model.py` | 770 | Pure: baseline, scope, bounds, outcomes, caveats, truncation, delta, report |
| `backend/app/simulation/impact.py` | 232 | Pure: comparing two answers and qualifying the comparison |
| `backend/app/simulation/service.py` | 707 | Deriving the pairs, resolving both worlds, bounding the work |
| `backend/app/simulation/store.py` | 355 | The two tables a proposal is kept in, and no others |
| `backend/app/simulation/errors.py` | 32 | `SimulationUnsupportedRead`, `SimulationBoundsError` |
| `backend/app/simulation/__init__.py` | 178 | The public surface and the module map |
| `database/migrations/versions/0011_simulation_overlays.py` | 157 | `simulations`, `simulation_evaluations`, four indexes |
| `database/migrations/versions/0012_merge_concurrent_phases.py` | 42 | A merge revision; creates nothing (see below) |

### Added — tests

| File | Tests | What it pins |
| --- | ---: | --- |
| `backend/tests/simulation/test_overlay.py` | 37 | What a proposal accepts and refuses; the digest; the document round trip and its revalidation |
| `backend/tests/simulation/test_application.py` | 32 | Nothing is mutated; every invented row is marked; placement, renumbering, the two protection dispositions, the unread share ACL |
| `backend/tests/simulation/test_repository_coverage.py` | 30 | The three sets partition every public read; every refusal raises; no write method exists |
| `backend/tests/simulation/test_engine.py` | 29 | The whole engine over an in-memory estate: gains, losses, alternate paths, applicability, isolation, determinism, bounds |
| `backend/tests/simulation/test_impact.py` | 17 | The six directions, generic-rights normalization, the certainty rules |
| `backend/tests/simulation/test_model.py` | 34 | Baseline pairing and staleness, scope validation, bounds clamping, complete vocabularies |
| `backend/tests/db/test_simulation.py` | 22 | All of it against PostgreSQL: the real graph, the isolation digest, the store, the measured cost |
| `backend/tests/support/simulation.py` | — | An estate in memory and the two repository subclasses that serve it |

### Changed

| File | What |
| --- | --- |
| `backend/app/models/schema.py` | `SimulationBaselineKind`, `SIMULATION_TOKEN_LENGTH`, and the two tables, appended |
| `backend/app/repositories/membership.py` | A read-only `session` property, so a wrapping repository builds itself over the same connection rather than reaching for a private attribute |
| `backend/app/repositories/resources.py` | The same |
| `README.md` | The *What-if simulation* section, one architecture invariant, the package in the layout |
| `docs/architecture/mvp-capabilities.md` | Limit 1 — "Changes nothing" — now says what ADG *can* evaluate without changing anything |

### Documentation

* `docs/architecture/simulation.md` — the model, what is and is not overlaid, the caveat
  vocabulary, the measured cost, §7 what this phase does not do, §9 how the isolation is
  guaranteed.
* ADR-0020 — a simulation is the production engine reading through an overlay.

---

## Design decisions worth knowing

### The overlay is applied at the repository boundary, and nowhere else

`AccessService` takes its two repositories by injection. Phase 7A used that to answer about a
past instant by substituting as-of subclasses; this phase substitutes overlay repositories. So
the token build, the DACL projection for an unread path, the deny-before-allow evaluation, the
coverage findings and the causal explanation all apply to a hypothetical answer with no change
to the engine — and there is no simplified simulation arithmetic to disagree with it.

The alternative that was rejected hardest is a separate calculator. It would be small and fast,
and the first time it disagreed with the real engine, the tool would be telling an
administrator that removing a group is safe on the authority of code that has never answered a
real question.

### Nothing is inherited, and that is stricter than Phase 7A

An as-of repository lets un-overridden reads fall through to current state, guarded by a naming
test. That is tolerable when the fall-through returns *today's* facts. It is not tolerable here,
where it would return production rows under a simulation's name with nothing to tell them
apart — so every public read of both base classes is overlay-aware, plain delegation, or a
refusal that raises. `tests/simulation/test_repository_coverage.py` asserts the three sets
partition the base classes' public reads, so a method added to a repository fails the suite
until somebody decides which it is.

### `keys_with_members` is deliberately *not* overlay-aware

It answers *"has ADG ever enumerated the inside of this group?"*, which is what separates *the
subject is not in it* from *nobody has looked*. A proposal to add one member is not an
enumeration. Letting a simulated edge satisfy it would retire a `TRUSTEE_MEMBERSHIP_UNOBSERVED`
finding on the strength of a hypothetical — a coverage gap turned into a verdict, which is
exactly the failure that finding exists to prevent. Pinned by
`test_engine.py::test_a_simulated_edge_does_not_count_as_having_enumerated_a_group`.

### Applicability is decided first, and narrows the overlay

Each change is checked against the object it names before anything is simulated, and the
overlay handed to the repositories holds only the changes that can be applied. So the impact
list and the list of applied changes describe the same world. A proposal written last week
against an ACE somebody has since removed reports `TARGET_NOT_FOUND` rather than producing an
empty impact list that reads as *"this change is safe"*.

`PARENT_NOT_OBSERVED` counts as applied: clearing protection does flip the flag, and what could
not be computed is the projection that would flow back. Treating it as unapplied would report a
simulated ACL the report then claims was never changed.

### An unread share ACL cannot be simulated against

Zero stored share entries means nobody has read the ACL — Windows shares always carry one — and
`_share_dacl_from` reads the difference straight off the count. Adding one simulated entry would
turn *"we have never looked at this share"* into *"this share grants exactly this and nothing
else"*: an invented certainty, in the direction that hides access. The change is refused at two
independent points (the applicability check and `overlay_share_acl`) and reported as
`TARGET_NOT_OBSERVED`.

### `ace_count` has to move with the entries

The access check compares the count the descriptor declared with the entries it was handed and
raises `ACE_COUNT_MISMATCH` when they differ. An overlay that changed a DACL and left the
collected count alone would fire that finding on **every** simulated resolution and bury the
finding the simulation was run to produce. The resource row is rebuilt with the new count, the
new protection flag, and a recomputed `acl_hash`.

### The claim inherits the answer's certainty

`AccessCertainty.AT_LEAST` on the simulated side means the truth could be wider than reported —
precisely the case where a right reported as removed is in fact retained — so it raises
`LOSS_MAY_NOT_HOLD`. `AT_MOST` raises `GAIN_MAY_NOT_HOLD`. Neither withholds the delta: *"we
cannot be sure this removal works"* is information and silence is not.

### Bounded synchronously, rather than queued

The prompt's requirement for an asynchronous job architecture is conditional on broad
simulations exceeding request limits. With the bounds in place they do not — measured figures
are in `simulation.md` §6 and pinned by `TestTheCostIsBounded`. The affected scope inverts the
question exactly as ADR-0012 does, so a resource costs two enumerations rather than two
traversals per principal, and only the principals that appear on one side and not the other are
resolved individually. §6 also names the three things that would change this.

### Two defects the implementation found in itself

* **An unplaced addition silently un-numbered a DACL.** Renumbering was decided from the
  *result*, and an added entry has no position of its own — so one unplaced addition made
  `all(order_index is not None)` false, the whole ACL came back unnumbered, and an unnumbered
  DACL cannot express a Deny. The flag is now read off the **baseline**, before anything is
  applied. Caught by `test_positions_are_renumbered_contiguously`.
* **A delta helper that asserted about the wrong resource.** The affected scope legitimately
  returns several deltas per principal — a membership change reaches every directory the group
  is on — and a test helper taking the first match was asserting about whichever one sorted
  highest by severity. A real property of the engine, found by a test that was wrong about it.

---

## Schemas and contracts

**No contract changed.** No route was added, removed or altered; the collector protocol is
untouched; `openapi.json` is unchanged by this phase.

**Two tables added.**

`simulations` — the proposal and the state it was written against:

* `overlay` JSONB carrying its own `document_version`, plus `overlay_hash` and `change_count`;
* `baseline_kind`, `baseline_at`, `baseline_token`, `baseline_run_id`, `baseline_captured_at`;
* four check constraints, including `(baseline_kind = 'as_of') = (baseline_at IS NOT NULL)` —
  a row that disagreed with itself there would name a baseline the report was not computed
  against;
* indexes on `created_at` and `overlay_hash`.

`simulation_evaluations` — one row per run: `scope_kind`, the basis token at the time,
`stale_baseline`, `pairs_evaluated`, `complete`, `duration_ms`, and the compact `report` JSONB.
Indexed on `(simulation_id, computed_at)`.

**One foreign key**, `simulation_evaluations.simulation_id → simulations.simulation_id`, which
the resource tables deliberately do not have. That rule is about observations: a share whose
server no run has described is a real reading, and a constraint would reject it at the moment a
partial scan most needs to record what it managed to read. An evaluation is written by this
application in one transaction after the proposal it belongs to; an orphan is a defect.

**No setting added.** Bounds are `SimulationBounds`, clamped to ceilings in code, the same
shape as `ExplanationLimits`.

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| **This phase, hermetic** | `pytest -m "not smoke" tests/simulation` | **179 passed**, 0.20s |
| **This phase, PostgreSQL** | `pytest tests/db/test_simulation.py` (isolated database) | **22 passed**, 41.9s |
| Backend, hermetic, whole tree | `.\scripts\backend-test.ps1` | **5,048 passed, 21 failed**, 10 skipped, 817 deselected — **every failure belongs to the concurrent phase**, see below |
| Backend, hermetic, excluding the two affected files | `pytest -m "not smoke" --ignore=tests/api/test_authorization.py --ignore=tests/contracts/test_openapi_snapshot.py` | **5,020 passed**, 10 skipped |
| Regression, PostgreSQL | `pytest tests/db/{test_schema,test_query_cost,test_access_api,test_history_queries,test_simulation}.py` | **130 passed**, 2m24s |
| Backend lint and types | `.\scripts\backend-lint.ps1` | **fails on the concurrent phase's files only**, see below |
| Collectors | `.\scripts\collector-test.ps1` | not run — **no collector file was touched** |
| Frontend | `.\scripts\frontend-check.ps1` | not run — **no frontend file was touched**, and no contract it types against changed |

### The 21 hermetic failures, attributed

All 21 are in `tests/api/test_authorization.py` (20) and
`tests/contracts/test_openapi_snapshot.py` (1). Every one of them names the concurrent phase's
`/api/v1/changes`, `/api/v1/changes/compare`, `/api/v1/changes/impact`,
`/api/v1/changes/summary` and `/api/v1/changes/timeline` routes, which are served but not yet
entered in `ROUTE_CAPABILITIES` nor in the OpenAPI snapshot. **This phase added no route**, and
excluding those two files leaves the suite green at 5,020.

### The lint gate, attributed

* `ruff check` — 11 errors, in `app/ingestion/checkpoints.py`, `tests/changes/*`,
  `tests/db/test_change_feed.py`, `tests/incremental/*`.
* `ruff format --check` — 17 files, in `app/changes/`, `app/domain/incremental.py`,
  `app/history/writer.py`, `app/ingestion/`, `tests/auth/`, `tests/changes/`, `tests/db/`,
  `tests/incremental/`.
* `mypy` — 118 errors in 15 files, in `app/changes/`, `app/contracts/v1/envelopes.py`,
  `app/governance/`, `app/history/writer.py`, `app/ingestion/service.py`, and their tests.

**None is in a file this phase added or changed.** Verified directly:

```
ruff check      app/simulation tests/simulation tests/support/simulation.py \
                tests/db/test_simulation.py app/models/schema.py app/repositories   → All checks passed
ruff format --check (the same 28 files + both migrations)                           → 28 files already formatted
mypy            app/simulation tests/simulation tests/support/simulation.py
                tests/db/test_simulation.py                                          → no error in any of them
```

### A note on how the database tests were run

The concurrent session was running its own smoke suite against the shared `adg_test` database
while this phase's were running, and `clean_tables` truncates every table before each test — so
the first attempt failed with rows disappearing mid-test. They were re-run against an isolated
database (`ADG_DATABASE_URL=…/adg_sim9a`, giving `adg_sim9a_test`), built by the same
`alembic upgrade head` the harness always uses. **This is a property of two sessions sharing one
machine, not of the tests**; a single session running `.\scripts\backend-test.ps1 -Smoke` needs
nothing special.

### What each suite covers

| File | What it pins |
| --- | --- |
| `test_overlay.py` | A self-membership, a host-scoped group with the wrong edge kind, an entry with no mask, a modification that changes nothing, two changes to one target, an over-large overlay; the digest is order-independent; a stored document of another version is refused and a stored change is revalidated |
| `test_application.py` | The inputs are not mutated and an untouched ACL comes back as the *same objects*; every invented row carries the simulated provenance; a removal matches the pair and not the stored key; canonical placement, an explicit non-canonical position, contiguous renumbering, an unordered baseline left unordered; both protection dispositions; the parent projection is the domain's own; an unread share ACL is left alone |
| `test_repository_coverage.py` | The three sets partition every public read of both base classes and none of them is inherited; every refusal raises; no write method exists; both repositories read the same proposal |
| `test_engine.py` | Bob loses access and Alice keeps Read through `Domain Users`, with the surviving route named; Dave gains access; the affected resources come from the reference index; a Deny ahead of the Allow revokes; a new grant reaches the candidate list; five applicability outcomes; four isolation properties; determinism; four bound behaviors |
| `test_impact.py` | The six directions; the same access written generically and specifically is not a change; `LOSS_MAY_NOT_HOLD` and `GAIN_MAY_NOT_HOLD` fire on the right side and not the other; two answers to different questions cannot be compared |
| `test_model.py` | The baseline's kind/instant pairing and its naive-timestamp refusal; staleness is a token comparison; scope validation; bounds refuse past a ceiling and clamp on request; the three description tables are complete |
| `tests/db/test_simulation.py` | The live answer before anything is proposed; removing one chain of two changes nothing; removing both leaves the weaker grant with the route named; removing all three loses access; a removal naming the wrong *edge kind* matches nothing and says so; the collected-state digest is unchanged after evaluating **and storing**; no simulated row reaches the ACE tables; the baseline is named and goes stale only on a real ingestion; the store round-trips, appends evaluations, pages, deletes, and refuses an unnamed proposal; the measured cost |

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| Simulations cannot change ADG's current observations or external Windows state | `tests/db/test_simulation.py::TestNothingIsMutated` (four tests, one digest over nine tables); `test_application.py::TestNothingIsMutated`; `test_repository_coverage.py::test_the_overlay_repositories_expose_no_write_methods`; no collector file was touched |
| Results use the production rights resolver | `test_engine.py::TestTheBaselineIsWhatTheEngineSaysItIs` and every delta in both suites is a pair of `EffectiveAccess` values from `AccessService`; `test_repository_coverage.py::test_nothing_is_inherited` proves the substitution is total |
| Alternate paths prevent false "access removed" claims | `tests/db/test_simulation.py::TestAlternatePathsPreventAFalseClaim` (three tests over a real graph); `test_engine.py::test_the_surviving_route_is_named_rather_than_merely_implied` |
| Baseline version is explicit and stale-baseline warnings are possible | `tests/db/test_simulation.py::TestTheBaselineIsNamed`; `test_model.py::test_staleness_is_a_token_comparison_and_not_a_clock`; `SimulationReport.document()["baseline"]` |

---

## Known limitations

1. **Descendants are not evaluated.** An inheritable ACE change, or an inheritance toggle,
   reaches every directory below the one it names; only the directories the overlay implicates
   are evaluated. Reported as `DESCENDANTS_NOT_EVALUATED`, never silent. **This is the most
   consequential limitation of the phase**: on a real tree, changing an inheritable entry at a
   share root is the change with the widest blast radius, and the report bounds it rather than
   measuring it.
2. **A share ACE change is evaluated at the share root only**, for the same reason and with the
   same truncation. A share ACL constrains every path under it.
3. **No HTTP surface.** `SimulationService` and `SimulationStore` are reachable from the
   backend only. Nothing on the web application shows a what-if.
4. **Changes to unobserved objects cannot be applied.** A directory ADG has never read and a
   share whose ACL nobody has collected both come back `target_not_observed` — deliberate, but
   it means a proposal about the unscanned part of an estate produces no impact list, and the
   reason is in the applicability list rather than on the impact list where somebody is looking.
5. **Owner changes are not modeled.** An owner holds `WRITE_DAC` implicitly, so changing one
   changes access. It is not in the overlay vocabulary.
6. **The engine inherits Phase 7A's limitation 1.** A live answer still counts a grant a
   reconciled scan has proved is gone, because current-state reads are not routed through
   presence — so a simulation over a current baseline inherits the same overstatement on both
   sides of the comparison. A simulation over an *as-of* baseline does not.
7. **One edge-fetch budget for a whole simulation.** The membership repository is shared
   between the two `AccessService` instances by design — it is what makes the cost report
   honest — but it means a simulation over very many resources can exhaust the budget and start
   reporting truncated traversals partway through. The traversals say so; the report does not
   summarize it beyond `edges_read`.
8. **`resources_named_by` injection covers additions only.** A removal leaves the resource a
   candidate, which is correct (the engine returns every candidate with its verdict), but a
   proposal that *only* removes ACEs will not surface a resource the subject's token never
   named — there was nothing to remove there.
9. **Bounds are code constants, not settings.** There is no operator knob; a caller passes
   `SimulationBounds`.

---

## Security and privilege assumptions

* **Unchanged for collectors.** No collector was modified, no new permission is required, and
  nothing in this phase asks a collector for anything. ADG stays read-only against Windows
  (ADR-0004).
* **Simulation is strictly less capable than the existing read surface.** It reads what the
  access engine already reads and writes only to its own two tables. There is no code path
  from an overlay to Active Directory, to a share, to an NTFS descriptor, or to a
  collected-state table.
* **The one destructive operation is `SimulationStore.delete`**, and it deletes proposals. The
  cascade reaches `simulation_evaluations` and stops; nothing else points at either table.
* **`simulations` and `simulation_evaluations` hold the same disclosure class as the rest of
  the schema** — SIDs, account names, UNC paths, masks — plus the identity of whoever wrote a
  proposal (`created_by`, nullable). They inherit the same retention question as the log
  stream; see `docs/operations/mvp-runbook.md` §6.
* **No new endpoint, so no new authorization decision.** The capability boundary in
  `app/api/__init__.py` is untouched by this phase. When a surface is added, a simulation
  discloses *potential* access, which is at least as sensitive as the access answers behind
  `access:read` — see prerequisite 2.
* **A refusal is louder than a wrong answer.** An overlay repository asked for a read outside
  the simulated surface raises rather than answering, because returning production rows under a
  simulation's name is the one failure an operator has no way to detect.

---

## Migration and compatibility notes

* **`0011_simulation_overlays` is additive.** Two tables, four indexes, no change to any
  existing column, index or constraint. `downgrade()` drops both; every stored proposal is
  lost and no collected fact is touched.
* **It hangs off `0007_history_model`, not off the newest revision.** The concurrent phase's
  revisions were being renamed while this one was written (`0009_governance_model` became
  `0008_governance_model` mid-session), and anchoring to a *committed* revision keeps this file
  correct whatever those become.
* **`0012_merge_concurrent_phases` exists because the graph had four heads**, and
  `alembic upgrade head` refuses to run at all in that state — for both phases. It is a merge
  revision: it creates nothing, drops nothing, and declares that the branches are independent,
  which is true because no two of them touch the same table. **It is the file to delete** if
  somebody later linearizes the revisions into one chain. Its parents are
  `0008_change_feed_index`, `0008_governance_model`, `0009_risk_findings` and
  `0011_simulation_overlays`; if any of those is renamed again, this file needs the new name.
* **No data migration.** There is nothing to backfill: a proposal is something somebody writes.
* **`app/models/schema.py` gained two tables and one enum**, and `tests/db/test_schema.py` —
  which reflects the live database and compares it with the metadata — passes, so the
  declaration and the migration agree.
* **`MembershipRepository.session` and `ResourceRepository.session` are new public
  properties.** Read-only, additive, and no existing caller changes. They exist so a wrapping
  repository can build itself over the same connection instead of reaching for `_session`.

---

## Prerequisites for the next prompt

1. **Evaluate the subtree.** Limitation 1 is the one that matters. It needs a bounded
   descendant read (`share_key = … AND starts_with(resource_key, …)`, the predicate the Phase
   7A closure already uses) and an inheritance projection down the tree, plus a bound on depth
   and breadth. Until it exists, a proposal that changes an inheritable entry is reported
   honestly and incompletely.
2. **Decide the API surface, and the capability.** Open questions a 9B must settle: whether a
   simulation is a `POST` that computes and returns, or a `POST` that stores and a `GET` that
   reads back; which capability it requires — it discloses *potential* access, which argues for
   at least `access:read` and possibly its own; and whether a stale baseline is a warning field
   or a `409`.
3. **Decide what the frontend does with a caveat.** `LOSS_MAY_NOT_HOLD` is the field that stops
   a report being acted on too confidently, and a UI that renders it as a small grey icon has
   thrown the phase away.
4. **Measure on a large estate.** The figures in `simulation.md` §6 are from an MVP-sized
   fixture. Before this ships, somebody should run the affected scope against a proposal that
   touches a group with thousands of members and decide whether `max_principals = 50` is right.
5. **Consider owner changes** (limitation 5) — `WRITE_DAC` is the right that lets somebody
   rewrite the ACL, and a what-if that cannot model a change of owner cannot model the most
   consequential change there is.
6. **Linearize the migration graph** and delete `0012_merge_concurrent_phases`, once both
   concurrent phases have settled.

---

## Intentionally deferred

* **The HTTP surface.** Phase 7A followed the same rhythm and for the same reason: the prompt's
  required work names an engine, a model, bounds and persistence, and not one route. Adding
  routes would also mean choosing a capability and regenerating the OpenAPI snapshot, which is
  a decision for a phase that is specified.
* **Any UI.** No frontend file was touched.
* **An asynchronous job architecture.** Conditional in the prompt, and the condition is not
  met. See `simulation.md` §6 for the measured basis and for what would change it.
* **Descendant evaluation** (limitation 1) — deferred, reported as truncation, and prerequisite 1.
* **Storing the full derivation of every delta.** The overlay, the baseline and the scope are
  stored, and every simulated record is built from constants, so a report is reproducible
  exactly. A stored copy would be a second account of the same answer, ageing independently of
  the code that computes it.

---

## `git status --short`

Recorded at the end of this phase. **The working tree contains another phase's work**; the
paths belonging to Phase 9A are:

```
?? backend/app/simulation/
?? backend/tests/simulation/
?? backend/tests/support/simulation.py
?? backend/tests/db/test_simulation.py
?? database/migrations/versions/0011_simulation_overlays.py
?? database/migrations/versions/0012_merge_concurrent_phases.py
?? docs/architecture/simulation.md
?? docs/decisions/0020-simulation-is-the-engine-reading-an-overlay.md
?? docs/handoffs/phase-09a-simulation-engine.md
 M backend/app/models/schema.py            (two tables and one enum, appended)
 M backend/app/repositories/membership.py  (the session property)
 M backend/app/repositories/resources.py   (the session property)
 M README.md
 M docs/architecture/mvp-capabilities.md
 M docs/decisions/README.md
```

Everything else in `git status --short` belongs to the concurrent phase and was neither written
nor modified here.

### No commit was made, and why

Staging the paths above was attempted and then **reverted with `git reset --mixed`**, because
four of them are shared with the phase running concurrently and a commit could not separate
the two:

| File | This phase | Also uncommitted from the other phase |
| --- | ---: | ---: |
| `backend/app/models/schema.py` | ~115 lines (one enum, two tables, three `__all__` entries) | ~920 lines (checkpoints, owners, review campaigns, remediation, the governance audit trail) |
| `README.md` | ~28 lines | ~33 lines |
| `docs/decisions/README.md` | 1 row | 4 rows |
| `docs/architecture/mvp-capabilities.md` | 6 lines | some |

The three ways out were all worse than stopping:

* **Commit the four files whole.** That puts another phase's unfinished work — which does not
  currently pass `ruff`, `ruff format` or `mypy` — into a commit named for this one.
* **Commit only the files that are exclusively this phase's.** The migration creating
  `simulations` would then be committed while the metadata declaring it was not, and
  `tests/db/test_schema.py` — which reflects the live database and compares it with the
  metadata — would fail on a fresh checkout of that commit.
* **Split the hunks in `schema.py`.** The two phases' `__all__` insertions are adjacent, so
  hunk surgery risks corrupting work this phase did not write and cannot test.

**The working tree is coherent and green**: 179 hermetic and 22 database tests for this phase
pass, the wider suite passes except for the concurrent phase's own routes, and every file this
phase touched is clean under `ruff`, `ruff format` and `mypy`. What is needed is a coordinator
decision — commit both phases together once the other has settled, or have each session commit
after the other finishes. Until then the work is on disk and nothing has been lost.
