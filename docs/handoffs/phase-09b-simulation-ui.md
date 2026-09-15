# Handoff — Phase 9B (`phase-09/02-simulation-ui-validation.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-09a-simulation-engine.md](phase-09a-simulation-engine.md)
**Collector contract version after this phase:** `1.4` — unchanged by this phase.
**Derived-response contract:** `1.0` — unchanged.
**`docs/contracts/v1/openapi.json`:** **regenerated.** Nine routes added under
`/api/v1/simulations`; see *Schemas and contracts*.

> ## ⚠ This phase was implemented alongside five others, in the same working tree
>
> A second agent session built Phases 7B, 8A, 8B, 10A and 10B in `C:\code\adg` while Phase 9A
> and this phase were written. That is visible everywhere below and it changes how some of it
> should be read:
>
> * **Whole-tree test and lint totals are not comparable to an earlier phase's.** Figures
>   attributable to *this* phase are stated separately and exactly.
> * **`ruff format` fails on two files this phase edited but did not unformat.** Both failures
>   are in the other phase's hunks, and both are named below.
> * **No commit was made**, for the same reason Phase 9A made none, and the reason is now
>   stronger. See *No commit was made, and why*.
>
> Nothing in this phase's own code depends on the other phases', and nothing in them was
> modified here beyond the shared files named below.

## Scope completed

ADG's what-if engine now has a surface. An operator can propose a permission change from
wherever they are already looking, see what it would do with the evidence rather than a count,
store it against a change ticket, export it, and run it again after the next scan — and at no
point can they mistake any of that for having made the change.

And the answer is now checked against reality rather than only against the engine that
produced it.

1. **Nine HTTP routes** under `/api/v1/simulations`, behind two new capabilities of their own.
2. **A proposal editor**, seeded by a `Simulate` action from four places: membership views,
   SMB ACE rows, NTFS ACE rows, and the removal table on the access explanation.
3. **An impact view** that leads with what each change *became*, renders a caveat beside the
   row it qualifies in the API's own words, names the routes that survive a removal rather
   than counting them, and keeps unchanged rows.
4. **Structured export** of a plan and its result, self-describing: the vocabulary travels
   with the document.
5. **A controlled equivalence harness** — build a known permission state, collect it, simulate,
   apply the equivalent change fixture-side, recollect as three reconciling collector runs,
   and compare predicted with observed. Nine change cases, all equivalent, plus the **one
   exception measured with exact masks**.
6. **The non-destructive claim is a tested property**, not typography: one sentence served by
   the API, an `applied: false` literal field, and a digest over every collected-state table
   taken around the whole route sweep.
7. **125 new tests directly about this phase** — 23 hermetic, 52 against PostgreSQL, and 50 in
   the browser suite. The contract and navigation suites gained more besides, which is why the
   whole-tree browser figure rises by more than 50.

**No remediation was added.** Nothing in this phase writes to Active Directory, to a share, or
to an NTFS descriptor.

---

## The three questions Phase 9A left open, answered

Argued in full in **ADR-0034**; the shape:

| Question | Answer | Where |
| --- | --- | --- |
| Compute-and-return, or store-and-read-back? | **Both, as two routes.** `POST /preview` keeps nothing; `POST /` stores the proposal *and* its first evaluation in one transaction. Same body, different path. | `app/api/simulations.py` |
| Which capability? | **Its own pair.** `simulations:read` and `simulations:run`; a plain `viewer` holds neither. | `app/auth/roles.py` |
| Stale baseline: warning or `409`? | **A field**, on every response that could carry one. Never a refusal — re-running is one `POST` away. | `SimulationBaselineView.stale` |

---

## Files and modules added or materially changed

### Added — the backend surface

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/api/simulations.py` | 1,484 | Nine routes, the request models, and every view a report is rendered through |
| `backend/app/simulation/describe.py` | 210 | Pure: the non-destructive notice, the change sentence, the direction wording; re-exports the three vocabularies that already had homes |

### Added — tests

| File | Tests | What it pins |
| --- | ---: | --- |
| `backend/tests/simulation/test_describe.py` | 19 | Every vocabulary is complete and ordered; the notice says what it must; a Deny reads as "denying"; an inheritable entry says it reaches children; every change kind produces a sentence |
| `backend/tests/db/test_simulations_api.py` | 36 | The whole surface against PostgreSQL: preview stores nothing, the report names its baseline, each change reports what became of it, the answer is explainable, staleness is a field, the export is the stored report verbatim, the vocabulary is complete, validation refuses with a reason, **nothing is mutated across the whole route sweep**, and the capability split holds end to end |
| `backend/tests/db/test_simulation_equivalence.py` | 16 | Predicted against observed for nine change types, plus the measured exception and two controls on the harness itself |
| `backend/tests/support/equivalence.py` | — | The apparatus: a controlled estate, its mutators, its three collector transcripts, the two readings, and the comparison |

### Added — the frontend

| File | Contents |
| --- | --- |
| `frontend/lib/simulation.ts` | Pure: the request shapes, seeding to and from a query string, reading a report, both exports |
| `frontend/components/Simulation.tsx` | The notice, the baseline, the applications panel, the impact panel, the delta table, truncation, cost, the listing |
| `frontend/components/SimulationEditor.tsx` | The proposal editor (client) |
| `frontend/components/SimulationDetail.tsx` | The stored result, and running a proposal again (client) |
| `frontend/components/SimulationExport.tsx` | Text and JSON export (client) |
| `frontend/components/SimulateLink.tsx` | The `Simulate` action, as a link |
| `frontend/components/simulation.module.css` | The editor's code area and the action rows |
| `frontend/app/simulations/page.tsx` | Stored proposals |
| `frontend/app/simulations/new/page.tsx` | The editor's server shell; reads the seed off the URL |
| `frontend/app/simulations/[simulationId]/page.tsx` | One proposal, its result, its history, its export |
| `frontend/app/api/simulations/preview/route.ts` | Browser → preview |
| `frontend/app/api/simulations/route.ts` | Browser → store |
| `frontend/app/api/simulations/[simulationId]/route.ts` | Browser → delete |
| `frontend/app/api/simulations/[simulationId]/evaluations/route.ts` | Browser → run again |
| `frontend/tests/simulation.test.ts` | 23 tests over the pure module |
| `frontend/tests/simulation-views.test.tsx` | 20 tests over the screens |
| `frontend/tests/simulations-page.test.tsx` | 7 tests over the two server pages |
| `frontend/tests/simulation-factories.ts` | Fixtures, defaulting to the least alarming value |

### Changed

| File | What |
| --- | --- |
| `backend/app/auth/roles.py` | Two capabilities and their grants (auditor, admin, governance_admin) |
| `backend/app/api/__init__.py` | The simulations router, included with no blanket dependency |
| `backend/tests/api/test_authorization.py` | Nine `ROUTE_CAPABILITIES` entries and one `PATH_PARAMETERS` value |
| `backend/tests/auth/test_roles.py` | Two role expectations updated, two tests added |
| `docs/contracts/v1/openapi.json` | Regenerated |
| `frontend/lib/contracts.ts` | The simulation response types and two capabilities |
| `frontend/lib/api/adg.ts` | Seven client functions and six `USED_PATHS` entries |
| `frontend/lib/nav.ts` | The **What-if** section, behind `simulations:read` |
| `frontend/components/RawAcl.tsx` | An optional `Simulate` column on both ACE tables |
| `frontend/components/Explanation.tsx` | A `Simulate` action per removable relationship |
| `frontend/app/identities/principal/page.tsx` | An optional `Simulate` column on the membership tables |
| `frontend/app/resources/directory/page.tsx`, `frontend/app/resources/share/page.tsx` | Pass the object key so the column renders |
| `frontend/tests/contracts.test.ts` | 25 schema expectations |
| `frontend/tests/nav.test.ts` | The section, and the capability behind it |
| `README.md`, `docs/architecture/simulation.md`, `docs/decisions/README.md` | Updated |

### Documentation

* `docs/architecture/simulation-surface.md` — the routes, the screens, and §4, the equivalence
  validation with its coverage table and its one exception.
* ADR-0034 — a simulation is offered, never applied, and its capability is its own.

---

## Design decisions worth knowing

### The notice is one string, served by the API

`NON_DESTRUCTIVE_NOTICE` lives in `app/simulation/describe.py`, is rendered on every simulation
payload, and is the text every screen displays. Three surfaces render a simulation; a notice
each of them worded its own way is a notice one of them will eventually soften, and the one
that gets softened will be the one somebody reads. Beside it sits `applied: false` — a literal
field rather than prose, because a claim a test can assert on is a claim that stays true.

### `Simulate` is a link, never a button

A `Simulate` action is a plain `<a>` to `/simulations/new?kind=…`. The resulting page is
addressable and pasteable into a ticket, it works without JavaScript, and — the part that
matters — **nothing can happen by pressing it**. An action that looks like it might change
something is exactly what this phase's first acceptance criterion is about.

Each table renders the column only where it can name the object exactly. A row that could not
say which directory, or which end of a membership edge, it came from offers nothing: a seed
built from a guess would propose a change other than the one on screen, and the API would
accept it.

### The editor edits a change document rather than presenting nine forms

Nine change kinds, each with its own required fields and its own rules — a removal must name
the entry it removes, protecting a directory must say what happens to what it inherits, a share
entry carries a mask *or* a permission level and never both. A form with nine conditional
layouts would be a second copy of those rules written in TypeScript; it would be the lenient
copy; and it would let somebody submit a proposal the API then refuses with a message the form
had already contradicted.

So the editor edits the document, seeds it, and lets the domain constructors validate. A refusal
comes back as the sentence the domain wrote, naming the field. The vocabulary panel beside it is
**served by the API** for the same reason the navigation's capability list is.

### The capability is its own, and the read and the run are separate

A simulation composes two answers a viewer already holds — who reaches a share, what a group
contains — into "put this account in that group and it reaches the payroll share". That
composition is a route map for privilege escalation, so `simulations:read` starts at `auditor`.

`simulations:run` is separate again for a reason that is not disclosure: resolving a proposal's
affected scope is the most expensive request this API serves, and an account that may read what
somebody already ran must not thereby be able to make the estate resolve a thousand new pairs —
the same line `GET /api/v1/risks/*` draws against starting an evaluation.

### A stored result shows storage keys, and the page says why

What is persisted with a proposal is the engine's own compact document: masks as hex, caveats
as codes, principals as keys. That is Phase 9A's decision and it is right — the derivation is
reproducible exactly, and a stored copy of the rendered version would be a second account of one
answer ageing independently of the code that computes it. The consequence is that a stored
result cannot show display names, so the detail page says so on screen and offers **Run this
again**, which produces the rendered, resolved report.

### The watch flag is withheld rather than guessed

Whether a watch covers an affected directory is answered only for a caller holding
`alerts:read`; otherwise the field is `null`, never `false`. Who is being notified about what
is a statement about the organization and is not inherited by holding `simulations:read` —
the same line already drawn between `alerts:read` and `alerts:manage`.

### Every rendered directory gets its descriptor, in one query

A `pair` or `subject` simulation legitimately produces deltas with no resource record attached:
the scope named the directory rather than enumerating it. The view batch-fetches the missing
ones (`ntfs_resources_by_keys`) rather than rendering a bare storage key, because the one screen
an operator reads before signing off a change must not be a column of raw UNC keys with no
sensitivity marking.

### Four model names had to be prefixed, and the reason is worth recording

`ChangeView`, `AccessDeltaView`, `ScopeView` and `EvaluationView` already existed in
`app/api/changes.py`, `app/api/governance.py`, `app/api/scan_runs.py` and `app/api/risks.py`.
FastAPI **qualifies both sides of a name collision** in the published document, so introducing a
third `ScopeView` renamed the *existing* one and broke the frontend's contract check against it.
Every model in `app/api/simulations.py` now carries a `Simulation` prefix. The hazard is already
noted in `app/api/risks.py`; this is the second time it has bitten, and the frontend's
`tests/contracts.test.ts` is what caught it.

---

## The equivalence validation, and what it found

The procedure, per change type, in `tests/db/test_simulation_equivalence.py` over
`tests/support/equivalence.py`:

1. build a known permission state and **collect it** through the ingestion API;
2. run the production simulation;
3. apply the equivalent change **fixture-side**, editing the observation set the way Windows
   would have changed the object;
4. **recollect**, as three reconciling collector runs;
5. resolve the same pairs again and compare.

**The recollection is three runs, not one, and that is load-bearing.** Reconciliation is keyed
by `(collector, scope kind)` (`app/history/closure.py`): only an `active_directory` run
reconciling a `domain` may mark a membership edge absent, only an `smb` run reconciling a
`server` may mark a share ACE absent, only an `ntfs` run reconciling a `directory_tree` may mark
an NTFS entry absent. One run claiming all three would be a collector marking absent what it is
structurally incapable of seeing, and ADG refuses to infer absence from it — so without the
split, a removal is never *measured* as a removal and the comparison cannot see one.

### Equivalence demonstrated

| Change | Predicted | Observed | Agrees |
| --- | --- | --- | --- |
| `remove_member` | lost access | lost access | yes |
| `add_member` | gained access, exactly Modify | the same | yes |
| `add_ntfs_ace` | expanded | expanded | yes |
| `remove_ntfs_ace` | lost access | lost access | yes |
| `modify_ntfs_ace` (Modify → Read & Execute) | reduced, to `0x001200A9` | the same | yes |
| `add_ntfs_ace` (a Deny ahead of the Allow) | lost access | lost access | yes |
| `modify_share_ace` (Full → Read) | reduced | reduced | yes |
| the same, asked about the console | unchanged | unchanged | yes |
| `set_inheritance`, both dispositions | unchanged (share root, no parent) | unchanged | yes |

### The exception, stated exactly

The post-change answer is read two ways. **`as_of`** — the point-in-time engine at the
recollection instant — routes every read through `object_versions` and excludes what a
reconciled scan proved gone; it is the reference, and every row above agrees with it.
**`live`** — the ordinary current-state engine — does not consult presence, and ADG deletes
nothing on ingestion, so it still counts a grant a reconciled scan has proved gone.

So every *removal* case agrees with the as-of reading and disagrees with the live one.
`TestTheKnownExceptionIsMeasured` pins it with exact masks (`0x001301BF` before, `0x00000000`
predicted and observed as-of, `0x001301BF` observed live), and pins its consequence for
applicability: a proposal naming an ACE a reconciled scan has proved gone is still reported as
`applied` against a **current** baseline, with an impact list computed from a grant that no
longer exists. That is the one case where a report is confidently wrong rather than merely
bounded. **The workaround is one field** — an `as_of` baseline is presence-routed and reports
`target_not_found` — and the test asserts both halves, so the fix is documented by a passing
test rather than by prose.

This is Phase 7A's limitation 1 and Phase 9A's limitation 6. It is in a test rather than only in
a document so that the day somebody routes current-state reads through presence, this suite
tells them the limitation is gone.

### Two defects this phase found in itself

* **The impact view could not name the directory it was reporting on.** A `pair`-scope delta
  carries no resource record, so the first version rendered `path: null` and no sensitivity
  marking on exactly the screen an operator reads before approving a change. The view now
  batch-fetches the missing descriptors. Caught by
  `test_the_resource_on_each_delta_is_rendered_rather_than_left_as_a_key`.
* **Four response models collided with existing ones** and silently renamed *them* in the
  published document. Caught by the frontend's `tests/contracts.test.ts`, which failed on a
  schema this phase never touched.

---

## Schemas and contracts

**No collector contract changed.** No observation kind, no envelope, no schema file.

**Nine routes added**, all under `/api/v1/simulations` (see
`docs/architecture/simulation-surface.md` §2 for the table). `docs/contracts/v1/openapi.json`
regenerated with `python -m app.contracts.openapi`.

**Two capabilities added**, `simulations:read` and `simulations:run`. Grants:

| Role | `simulations:read` | `simulations:run` |
| --- | :---: | :---: |
| `viewer` | — | — |
| `auditor` | ✓ | — |
| `reviewer` | — | — |
| `governance_admin` | ✓ | ✓ |
| `admin` | ✓ | ✓ |

**No table, column, index or migration added.** This phase reads and writes the two tables
Phase 9A created and nothing else.

**One export document version introduced**, `SimulationExportView.document_version = "1.0"`,
independent of the overlay document's version. An exported file outlives the build that wrote
it and a reader has to be able to tell what it is holding.

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| **This phase, hermetic** | `pytest -m "not smoke" tests/simulation/test_describe.py tests/auth/test_roles.py tests/api/test_authorization.py` | **97 passed**, 28.2s (of which 23 are new: 19 + 2 role tests + 2 capability sweeps) |
| **This phase, PostgreSQL** | `pytest tests/db/test_simulations_api.py tests/db/test_simulation_equivalence.py` (isolated database) | **52 passed** |
| **Regression, PostgreSQL** | `pytest tests/db/{test_simulation,test_simulations_api,test_simulation_equivalence,test_authorization,test_schema,test_access_api}.py` | **160 passed**, 2m53s |
| Backend, hermetic, whole tree | `.\scripts\backend-test.ps1` | **5,341 passed**, 10 skipped, 1,039 deselected (5,318 before this phase) |
| Backend lint — `ruff check` | `ruff check .` | **All checks passed** |
| Backend lint — `ruff format` | `ruff format --check .` | **2 files would be reformatted**, both in the concurrent phase's hunks — see below |
| Backend types — `mypy` | `mypy .` | **7 errors in 4 files, all pre-existing and all in the concurrent phase's files** — identical to the baseline before this phase |
| Frontend | `.\scripts\frontend-check.ps1` | **passed** — lint, typecheck, **1,126 tests in 31 files**, build |
| Collectors | `.\scripts\collector-test.ps1` | not run — **no collector file was touched** |

The database suites were run against an isolated database
(`ADG_DATABASE_URL=…/adg_sim9b`), as Phase 9A's were and for the same reason: `clean_tables`
truncates every table before each test, and two sessions sharing one machine would otherwise
delete each other's rows mid-test. A single session running `.\scripts\backend-test.ps1 -Smoke`
needs nothing special.

### The two `ruff format` failures, attributed

Both are in hunks this phase did not write, in files it did edit:

* `app/api/__init__.py` — the `risks.router` include, wrapped where the formatter would not
  wrap it.
* `tests/auth/test_roles.py` — the `ALERTS_MANAGE` / `GOVERNANCE_ADMIN` assertion, likewise.

Both files were already failing `ruff format --check` before this phase touched them. They are
left for the session that wrote those lines rather than reformatted here, because reformatting
would fold another phase's in-flight work into this phase's diff. Everything this phase wrote is
clean:

```
ruff check      app/api/simulations.py app/simulation/describe.py app/auth/roles.py
                tests/simulation/test_describe.py tests/support/equivalence.py
                tests/db/test_simulations_api.py tests/db/test_simulation_equivalence.py
                tests/api/test_authorization.py                          → All checks passed
ruff format --check (the same files)                                     → already formatted
mypy            (the same files)                                         → no error in any
```

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| UI makes the non-destructive nature unmistakable | `tests/simulation-views.test.tsx` — the notice is an `alert` carrying the API's sentence; `tests/simulation.test.ts` — the text export's first line; `tests/db/test_simulations_api.py::assert_non_destructive` on every payload; `tests/simulation/test_describe.py::TestTheNotice` |
| Simulation output is explainable, not only a count | `tests/simulation-views.test.tsx` — both masks and the direction in words on every row, the caveat beside the row, a surviving route named rather than counted, unchanged rows kept; `tests/db/test_simulations_api.py::TestTheAnswerIsExplainableRatherThanACount` |
| Controlled validation demonstrates equivalence, or documents the exceptions | `tests/db/test_simulation_equivalence.py` — nine change types, all equivalent, plus `TestTheKnownExceptionIsMeasured` with exact masks |
| No endpoint writes to AD/SMB/NTFS | `tests/db/test_simulations_api.py::TestNothingIsMutated` — a digest over nine collected-state tables around preview, store, re-evaluate, export and delete; and a query proving no `simulated|` row reached an ACE or edge table |

---

## Known limitations

1. **Descendants are still not evaluated.** Phase 9A's limitation 1 is untouched: an
   inheritable ACE change or an inheritance toggle reaches every directory below the one it
   names, and only the directories the overlay implicates are evaluated. The report says so
   (`DESCENDANTS_NOT_EVALUATED`) and the UI renders it as an alert above the impact list. **This
   is still the most consequential limitation of the feature.**
2. **The live/as-of divergence on removals** (above). A simulation run against a *current*
   baseline, on an estate where a reconciled scan has proved something absent, computes from a
   grant that no longer exists. It is measured, not merely described, and the workaround is an
   `as_of` baseline — which the API accepts (`baseline_at`) but **no screen offers**.
3. **The editor is a document editor.** Somebody proposing a change has to know the shape of a
   change document, or arrive by a seeded `Simulate` link. That is a deliberate trade (above),
   and it means the "write a new proposal" path from the navigation is harder than the path
   from an ACL row.
4. **A stored result cannot show display names** (above). Re-running produces the rendered form.
5. **Nothing offers an as-of simulation, a scope wider than one page, or a saved comparison of
   two evaluations.** The data is there — two evaluations of one proposal are two rows — and
   the screen shows them as a list of runs rather than as a diff.
6. **The export is JSON and text.** No CSV, no PDF, no attachment to a governance item.
7. **The `Simulate` action on an explanation path covers membership and ACE edges.** An assumed
   membership offers nothing, correctly — there is no `Everyone` group to edit — and the cell
   says so.
8. **Owner changes are still not modeled** (Phase 9A's limitation 5), so neither the editor nor
   the equivalence harness covers the change that lets somebody rewrite an ACL.
9. **The equivalence harness observes through ADG, not through Windows.** The "observed" side
   is ADG's recollection of an edited observation set. What that recollection *means* is
   validated against Windows separately, by Phase 4C's `scripts/windows-access-check`; the two
   together are what make proposal → prediction → observation trustworthy, and neither alone is
   enough.

---

## Security and privilege assumptions

* **Unchanged for collectors.** No collector file was modified, no new permission is required,
  and nothing in this phase asks a collector for anything. ADG stays read-only against Windows
  (ADR-0004, `SECURITY.md`).
* **No route writes to Windows**, and the claim is tested rather than asserted: every
  collected-state table is digested around the whole route sweep.
* **Two new capabilities, and a plain viewer holds neither.** A simulation discloses *potential*
  access, which is a composition of two answers a viewer already has into a third that is more
  sensitive than either. See ADR-0034.
* **The one destructive route is `DELETE /api/v1/simulations/{id}`**, behind `simulations:run`,
  and it deletes proposals. The cascade reaches `simulation_evaluations` and stops.
* **The browser's write routes are individually declared** (`app/api/simulations/**`), because
  the generic `/api/adg` proxy forwards `GET` only — the same shape the watch routes use. What
  the browser may write is reviewable by reading four small files.
* **A stored proposal holds the same disclosure class as the rest of the schema**, plus the
  identity of whoever wrote it. It inherits the retention question in
  `docs/operations/mvp-runbook.md` §6.
* **The watch flag is capability-gated** inside the response, so a caller without `alerts:read`
  gets `null` rather than a fact about who is being notified.

---

## Migration and compatibility notes

* **No migration.** No table, column, index or constraint added, changed or removed. This phase
  reads and writes the two tables `0011_simulation_overlays` created.
* **The OpenAPI snapshot changed**, and four schema names moved as a side effect of the
  collision described above. A client typed against `ScopeView`, `ChangeView`,
  `AccessDeltaView` or `EvaluationView` should re-check which one it meant; the frontend's
  `tests/contracts.test.ts` does this automatically and is what found it.
* **Two capabilities added to `Capability`.** Additive: no existing route's requirement changed,
  and no role lost anything. A tenant that has provisioned only `viewer` sees no What-if
  section and every simulation route refuses with the API's own message naming the capability.
* **`ShareAceTable`, `NtfsAceTable` and `MembershipTable` gained one optional prop each.** Every
  existing call site still compiles; a caller that does not pass it renders no `Simulate`
  column, which is the deliberate behavior for a row that cannot name its own object.
* **`app/simulation/__init__.py` is unchanged.** `describe.py` is imported by path
  (`from app.simulation.describe import …`), so nothing in Phase 9A's public surface moved.

---

## Prerequisites for the next prompt

1. **Evaluate the subtree.** Still prerequisite 1 from Phase 9A, and now more visible: the UI
   renders `DESCENDANTS_NOT_EVALUATED` as an alert on exactly the screen where somebody is
   about to approve an inheritable change. It needs a bounded descendant read
   (`share_key = … AND starts_with(resource_key, …)`) and an inheritance projection down the
   tree, plus a bound on depth and breadth.
2. **Offer an as-of baseline in the UI.** The API takes `baseline_at`; no screen sends it. It is
   the documented workaround for limitation 2, and a workaround nobody can reach is not one.
3. **Compare two evaluations of one proposal.** The rows exist; the screen lists them. *"This
   change was safe on Monday and takes access away today"* is currently two documents a reader
   has to diff by eye.
4. **Measure on a large estate.** Phase 9A's prerequisite 4 is unchanged. The equivalence
   harness now gives a way to build one: `ControlledEstate` takes a list of principals, edges
   and entries, so a thousand-member group is a loop rather than a fixture file.
5. **Decide whether a proposal belongs to a review item.** Phase 10's governance model has
   proposed remediation; this phase has proposals with measured blast radius, and the two do not
   know about each other. That is a product decision, not a refactor.
6. **Consider owner changes** (Phase 9A's prerequisite 5), which neither the vocabulary nor the
   harness covers.

---

## Intentionally deferred

* **Automatic remediation.** Explicitly forbidden by this prompt, and nothing here approaches
  it: there is no code path from any handler to a Windows object.
* **Descendant evaluation** — Phase 9A's deferral, still reported as truncation.
* **A per-kind proposal form.** Deferred with a reason rather than by omission; see above.
* **An asynchronous job architecture.** Conditional in Phase 9A's prompt and the condition is
  still not met: the bounds keep a simulation inside one request, and the measured cost is in
  `simulation.md` §6.
* **Attaching an export to a change ticket, or to a governance item.** The export is a document
  a person copies; wiring it to a ticketing system is integration work nobody has specified.
* **CSV and PDF exports.**

---

## `git status --short`

The paths belonging to **Phase 9B**:

```
?? backend/app/api/simulations.py
?? backend/app/simulation/describe.py
?? backend/tests/simulation/test_describe.py
?? backend/tests/support/equivalence.py
?? backend/tests/db/test_simulations_api.py
?? backend/tests/db/test_simulation_equivalence.py
?? frontend/lib/simulation.ts
?? frontend/components/Simulation.tsx
?? frontend/components/SimulationEditor.tsx
?? frontend/components/SimulationDetail.tsx
?? frontend/components/SimulationExport.tsx
?? frontend/components/SimulateLink.tsx
?? frontend/components/simulation.module.css
?? frontend/app/simulations/
?? frontend/app/api/simulations/
?? frontend/tests/simulation.test.ts
?? frontend/tests/simulation-views.test.tsx
?? frontend/tests/simulations-page.test.tsx
?? frontend/tests/simulation-factories.ts
?? docs/architecture/simulation-surface.md
?? docs/decisions/0034-a-simulation-is-offered-not-applied.md
?? docs/handoffs/phase-09b-simulation-ui.md
 M backend/app/auth/roles.py                    (two capabilities and their grants)
 M backend/app/api/__init__.py                  (one router include)
 M backend/tests/api/test_authorization.py      (nine route entries, one path parameter)
 M backend/tests/auth/test_roles.py             (two expectations, two tests)
 M docs/contracts/v1/openapi.json               (regenerated)
 M frontend/lib/contracts.ts                    (the simulation types, two capabilities)
 M frontend/lib/api/adg.ts                      (seven functions, six paths)
 M frontend/lib/nav.ts                          (the What-if section)
 M frontend/components/RawAcl.tsx               (an optional Simulate column, twice)
 M frontend/components/Explanation.tsx          (a Simulate action per removable relationship)
 M frontend/app/identities/principal/page.tsx   (an optional Simulate column)
 M frontend/app/resources/directory/page.tsx    (pass the resource key)
 M frontend/app/resources/share/page.tsx        (pass the share and resource keys)
 M frontend/tests/contracts.test.ts             (25 schema expectations)
 M frontend/tests/nav.test.ts                   (the section and its capability)
 M README.md
 M docs/architecture/simulation.md              (limitation 3 closed, cross-linked)
 M docs/decisions/README.md                     (one row)
```

Everything else in `git status --short` belongs to Phases 7B, 8A, 8B, 9A, 10A and 10B and was
neither written nor modified here.

### No commit was made, and why

Phase 9A reached this conclusion and it is now unavoidable rather than merely awkward.

**`backend/app/api/__init__.py` cannot be committed in isolation.** Its uncommitted diff
registers the routers for alerts, governance, risks and the change feed alongside this phase's —
and those router modules (`app/api/alerts.py`, `app/api/governance.py`, `app/api/risks.py`) are
themselves **untracked**. A commit containing this file and this phase's work, and nothing else,
would not import.

The same entanglement runs through every shared file: of `frontend/lib/contracts.ts`'s ~842
added lines this phase wrote ~230; of `backend/app/auth/roles.py`'s ~92, about 30; and
`tests/auth/test_roles.py` and `tests/api/test_authorization.py` both carry the other phases'
route and capability entries inline with this phase's.

The three ways out were each worse than stopping:

* **Commit everything under a Phase 9B message.** Six phases in one commit named for one of
  them, including code that currently fails `ruff format` and `mypy`.
* **Commit only the files exclusively this phase's.** `app/api/simulations.py` would be
  committed without the include site that reaches it and without the capabilities it depends
  on; the tree at that commit would not start.
* **Split the hunks in the shared files.** Adjacent insertions in `roles.py`'s capability enum
  and `contracts.ts`'s type list make hunk surgery a real risk of corrupting work this phase did
  not write and cannot test.

**The working tree is coherent and green**: 23 hermetic and 52 database tests for this phase
pass, the whole hermetic suite passes at 5,341, the frontend gate passes entirely at 1,126, and
every file this phase wrote is clean under `ruff`, `ruff format` and `mypy`. What is needed is a
coordinator decision — commit all the in-flight phases together, or have each session commit
once the others have stopped. Until then the work is on disk and nothing has been lost.
