# Handoff — Phase 8A (`phase-08/01-risk-engine.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-07a-history-model.md](phase-07a-history-model.md)
**Collector contract version after this phase:** `1.3` — unchanged.
**Derived-response contract:** `1.0` — unchanged.
**`docs/contracts/v1/openapi.json`:** **not changed by this phase.** No route was added,
removed or altered here. (The file *is* modified in the working tree — by the concurrent
session; see §0.)

---

## 0. Read this first: two sessions edited this tree at once

Phase 8A was implemented while **another agent session was actively working in
`C:\code\adg`**, building what appear to be Phases 7B, 9 and 10 (a change feed, incremental
collection, simulation overlays, and governance/review campaigns). That is not a hypothetical
— four of its `pytest` processes were running against the shared test database during this
phase's test runs.

What that means for a reader of this document:

| Thing | Consequence |
| --- | --- |
| **Migration numbering** | `0008` was taken three times over by the other session. This phase is **`0009_risk_findings`**, revising `0008_incremental_collection`. The other session has since written `0012_merge_concurrent_phases`, whose `down_revision` tuple **explicitly includes `0009_risk_findings`**, so the tree is back to a single Alembic head and this phase's migration is in the merged chain. Verified: a database built from scratch through the merge has all three risk tables. |
| **ADR numbering** | The other session's code already cited `ADR-0020` and `ADR-0022`, so this phase took **0023 and 0024** rather than the next free numbers. `0021` and `0022` are presumed reserved by it. |
| **Shared files** | `app/models/schema.py`, `app/config.py`, `README.md`, `.env.example` and `docs/decisions/README.md` carry edits from **both** sessions. This phase appended to them and did not touch anything the other session wrote. |
| **Nothing was committed** | See §9. A commit is impossible to make coherently right now, and the reason is specific rather than cautious. |
| **The shared test database** | `adg_test` was being truncated mid-run by the other session's concurrent suites, which produced spurious `404`s during seeding. This phase's PostgreSQL tests were therefore run against a **private** database (`ADG_DATABASE_URL=…/adg_risk` → `adg_risk_test`). Nothing in the repository was changed to achieve that. |

---

## Scope completed

ADG can now say *what should not be like this*, name the rule that said so, and hand over the
records it said it from — well enough that the finding can be re-derived from those records
alone, months later, with the estate unavailable.

1. **A framework-free `risk_engine`.** Eleven deterministic rules, pure predicates over a typed
   fact bundle. No FastAPI, no SQLAlchemy, no I/O, no clock.
2. **Severity, confidence, evidence and remediation are four separate things.** Severity is
   configuration; confidence is derived from the facts and cannot be set; evidence is the
   records themselves; remediation is prose in a catalog, held apart from the predicates.
3. **Every finding reproduces from its own evidence** — the phase's central property, tested
   per rule with a negative control that removes a cited record and requires the finding to
   stop coming back.
4. **Three tables**: current findings, their transitions, and the evaluations that decided
   them. Nothing is deleted; a resolved finding keeps the evidence it was true on.
5. **Incremental re-evaluation**, with the guard that makes it safe: a pass may resolve only
   findings whose every subject it actually loaded, and only for rules that actually ran.
6. **Sensitivity is declared, never inferred.** The sensitive-resource rule reports nothing
   until an operator tags a resource, and that silence is reported as a configuration state
   rather than as a clean result.

**No HTTP surface and no UI**, following the same rhythm as Phases 5 and 7A — the prompt's
required work names an engine, persistence and re-evaluation, not routes. See *Intentionally
deferred*.

---

## What a finding is, in six sentences

A finding says: *this named rule matched this shape at this place, and here are the complete
records it matched on.* Its identity is a SHA-256 of the rule and the subject, so the same
shape in the same place is the same finding next week — and the rule **version is deliberately
not in the key**, so correcting a predicate re-examines the findings it already made instead of
resolving all of them and opening duplicates the same second. Severity comes from
configuration, via a *band* the rule reports (`write`, `full_control`, `null_dacl`, …), so
quieting a noisy rule never makes it stop looking. Confidence comes from the facts — a DACL
projected from an ancestor, a truncated group expansion — and is always the weakest any one
qualifier allows. A rule that needs to know what an access control list grants asks
`app.access_engine.evaluate_acl`, in stored order, so a finding and the access screen cannot
disagree about the same ACE. And a coverage gap is never a finding: a group nobody enumerated
is not an empty group, an unread descriptor is not a NULL DACL, and an unread share ACL is not
a share that grants nothing.

Full treatment: [`docs/architecture/risk-model.md`](../architecture/risk-model.md).
Operator reference: [`docs/operations/risk-rules.md`](../operations/risk-rules.md).

---

## Files and modules added or materially changed

### Added — the engine (framework-free)

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/risk_engine/severity.py` | 228 | The two axes. `Severity` and `SeverityBand` (configuration); `Confidence`, `FactQualifier` and the derivation (never configuration) |
| `backend/app/risk_engine/facts.py` | 777 | The typed snapshot a rule reads: entries, resources, shares, principals, membership, the bounded walks, and `RiskScope` |
| `backend/app/risk_engine/evidence.py` | 240 | The cited records, their canonical digest, and `rebuild_facts` |
| `backend/app/risk_engine/catalog.py` | 630 | Identity, prose, remediation, default severities, thresholds and fact dependencies. Data, held apart from the predicates |
| `backend/app/risk_engine/configuration.py` | 577 | What an installation may change; the sensitivity tags; strict JSON parsing |
| `backend/app/risk_engine/findings.py` | 269 | `RuleOutcome` (facts only), `RiskFinding`, the key, and `scope_covers` |
| `backend/app/risk_engine/rules.py` | 975 | The eleven predicates |
| `backend/app/risk_engine/engine.py` | 249 | Running them, and `reproduce` |

### Added — persistence and the service

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/repositories/risk.py` | 1,142 | `RiskFactsRepository` (storage → facts, bounded, and what a run changed) and `RiskFindingRepository` (findings, events, evaluations, and the reconciliation guard) |
| `backend/app/services/risk.py` | 238 | The three passes: full, incremental, targeted; and `verify` |
| `database/migrations/versions/0009_risk_findings.py` | 310 | Three tables, nine indexes, nineteen check constraints |

### Changed

| File | What |
| --- | --- |
| `backend/app/models/schema.py` | `risk_evaluations`, `risk_findings`, `risk_finding_events`; `RiskFindingStatus`, `RiskFindingEventType`, `RiskEvaluationTrigger`; `FINDING_KEY_LENGTH`. **Appended**; nothing existing was touched |
| `backend/app/config.py` | `risk_configuration_path` and the `risk_configuration` property |
| `backend/app/risk_engine/__init__.py` | Was a one-line placeholder from the bootstrap commit; now the package surface |
| `README.md`, `.env.example` | The risk section; `ADG_RISK_CONFIGURATION_PATH` |
| `docs/decisions/README.md` | Two rows appended |

### Documentation

* `docs/architecture/risk-model.md` — the model, the coverage-gap table, the lifecycle, and
  the scope guard.
* `docs/operations/risk-rules.md` — the configuration reference: the file format, every rule's
  default severities and thresholds, what ADG refuses to report, and why.
* ADR-0023 — a risk finding is a deterministic rule plus the records it can be re-derived from.
* ADR-0024 — business sensitivity is declared by an operator, never inferred.

---

## Design decisions worth knowing

### Severity is configuration; confidence is not

The two mistakes these prevent look identical in a report, which is why they are on separate
axes with different owners. **Raising confidence** claims evidence ADG does not have.
**Lowering severity** to quiet a rule hides a real exposure. Neither can happen by accident: a
rule reports a `SeverityBand` — a *fact* about the finding, such as "the grant reached Full
Control" — and configuration maps bands to severities, while confidence is a pure function of
the `FactQualifier` values the rule attached. `RiskFinding.__post_init__` recomputes the
confidence and refuses any value the qualifiers do not support.

There is **no score**. Severities rank for sorting and are never summed, because a number with
no unit is a number people compare between two shares.

### Evidence is the records, not a rendering — and that is testable

`"Everyone → Modify"` is a sentence; it cannot be rebuilt into facts. So every finding carries
whole `AceFacts`, `ResourceFacts`, `PrincipalFacts` and `MembershipFacts` records, and
`engine.reproduce` rebuilds a bundle from them and re-runs the one rule that produced the
finding. The test that makes this mean something is the **negative control**:
`test_a_finding_stripped_of_one_record_no_longer_reproduces` removes one cited record and
requires the finding to disappear. Without it, the suite would still pass if `reproduce`
matched on something weaker than the facts.

Reproduction runs **one** rule, not all eleven: the bundle was assembled for one predicate, so
asking the others about it would produce answers about the reduction rather than the estate.

### A finding cites the entries for one trustee, not the whole list

The access check skips every entry whose trustee is not in the token, so the entries naming one
trustee are exactly the set that decides that trustee's rights. Citing the whole DACL would be
correct and would put every unrelated entry into the evidence of every finding.

That restriction created a subtle problem worth recording. `ResourceFacts` originally carried
`declared_ace_count`; a record rebuilt from a restricted citation then looked *short* by the
entries the evidence had deliberately left out, so the finding reproduced with a weaker
confidence than it was recorded with. The field is now **`undelivered_ace_count`** — the
shortfall rather than the total — which is the same number before and after the restriction.
Pinned by `test_model.py::test_an_undelivered_entry_count_survives_being_reduced_to_one_entry`.

### The rules use the order-sensitive access check, deliberately

`app.access_engine.resolve_canonical` implements the canonical "all allows minus all denies"
model, which is exact for a DACL in the order Windows maintains and wrong for a reordered one.
It is wrong in the direction that **hides** an exposure: an Allow placed ahead of a Deny
actually grants, and the canonical model would subtract the Deny and report nothing. Windows
honors what is stored, so the rules run `evaluate_acl` in stored order. Both directions are
pinned: `test_a_deny_ahead_of_the_allow_removes_the_finding` and
`test_an_allow_ahead_of_a_deny_still_grants_and_still_fires`.

### A pass may only resolve what it covered

The safety property of the whole feature, and the one that fails silently — an emptied report
looks like an improvement. `RiskFindingRepository.reconcile` applies two tests before closing
anything: the rule ran, **and** the scope covers *every* subject key the finding names.

`scope_covers` requires **all** keys rather than any, and the asymmetry points the only
direction it safely can. Requiring all can leave a finding open that should have resolved,
which the next full pass corrects and which meanwhile reports a risk that is already gone.
Requiring any would close findings the pass never examined, which reports a risk as fixed when
nothing was done about it. The first wastes an afternoon; the second ends an access review with
a clean report over a live exposure.

A related trap is handled in the same place: **a full load that hit a ceiling is not a full
pass.** `RiskFactsRepository.load` demotes a complete scope to the keys actually loaded as soon
as any ceiling is reached, so a truncated bundle cannot resolve findings about directories it
never loaded. Pinned by `test_a_truncated_full_load_does_not_claim_a_complete_scope`.

### What a run changed comes from `object_versions`, not `observations`

Those answer different questions: an observation says a run *looked at* an object; a version
says the object's state actually moved. A run that re-read an unchanged estate touched every
object and changed none, and re-evaluating the whole estate for it would make "incremental"
mean nothing. A changed principal is then expanded to the resources naming it through
`principal_references`, which is already indexed for exactly that question.

### Emptiness gets the strictest treatment in the engine

An empty group and an uncollected group are identical in storage, and the action an
empty-group finding invites is *removing a grant* from a group that may have a hundred members
in it. So `MembershipFacts.enumerated` is three-valued and only `True` licenses the claim.
`True` means a run that **succeeded** — not `partial`, whose coverage is by definition
incomplete — and that **reconciled a scope** reported the principal. `None` and `False` produce
**nothing**: not a lower-confidence finding, not an informational note.

### Two defects the implementation found in itself

* **`Everyone` was being reported as an orphaned SID.** The unresolved-SID rule fired on any
  trustee with no `principals` row, and `S-1-1-0` frequently has none. It means `Everyone` on
  every Windows computer that has ever existed whether or not a run emitted a row for it, so
  the rule would have fired on nearly every ACL in the estate and buried the real orphans —
  deleted domain accounts, which look identical except that their SIDs mean nothing anywhere.
  Well-known and BUILTIN SIDs are now excluded from the "no record" case, by SID rather than by
  a stored kind, because the check must hold for a trustee ADG holds no record of. Caught by
  the first end-to-end run of the engine over a hand-built estate.
* **The restricted-citation confidence bug** described above, caught by the per-rule
  reproduction test rather than by review.

---

## Schemas and contracts

**No contract changed by this phase.** No route added, removed or altered; the collector
protocol is untouched; no observation kind was added.

**Three tables added.** Nineteen check constraints; the ones that encode a decision rather than
a type:

| Constraint | What violating it would break |
| --- | --- |
| `ck_risk_findings_resolution_has_an_instant` | A row reading as open while carrying a resolution instant — every "how long has this been open" answer drawn from it would be wrong |
| `ck_risk_finding_events_only_a_resolution_has_no_evidence` | A resolution that appears to carry the facts it was resolved on |
| `ck_risk_evaluations_complete_scope_has_no_key_lists` | An evaluation ambiguous about what it was entitled to close, in the direction that closes findings nobody looked at |
| `ck_risk_evaluations_incremental_names_its_run` | An incremental pass with no run to attribute it to |
| `ck_risk_findings_windows_ordered` | `first_detected_at` after `detected_at`, which would make the reopen history unreadable |

**One setting added**: `ADG_RISK_CONFIGURATION_PATH` (empty = shipped defaults). A path that is
set and unreadable is a startup error rather than a silent fall back.

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| Backend, hermetic | `pytest -q -m "not smoke"` | **5,048 passed**, 21 failed, 10 skipped, 793 deselected, 36.3s — **all 21 failures belong to the concurrent session**, see below |
| This phase, hermetic | `pytest -q tests/risk_engine` | **178 passed**, 0.13s |
| This phase, PostgreSQL | `pytest tests/db/test_risk_findings.py` against a private database | **22 passed**, 44.3s |
| Backend lint and types, this phase's files | `ruff check`, `ruff format --check`, `mypy` over the 21 files this phase owns | **passed** |
| Backend lint and types, whole tree | `ruff check .`, `mypy app tests` | **fails** — every remaining error is in the concurrent session's files, see below |
| Collectors | `.\scripts\collector-test.ps1` | not run — **no collector file was touched by this phase** |
| Frontend | `.\scripts\frontend-check.ps1` | not run — **no frontend file was touched**, and no contract it types against changed |

**200 tests are attributable to this phase** — 178 hermetic (`tests/risk_engine/`) and 22
requiring PostgreSQL (`tests/db/test_risk_findings.py`).

### The 21 hermetic failures are not this phase's

All 21 are in `tests/api/test_authorization.py` and `tests/contracts/test_openapi_snapshot.py`,
and the snapshot failure names exactly which paths are unpublished:

```
Paths served but not published: ['/api/v1/changes', '/api/v1/changes/compare', …,
'/api/v1/governance/campaigns', …, '/api/v1/governance/owners/{owner_id}']
```

Every one is a `/changes` or `/governance` route. **This phase added no route**, so the
OpenAPI document it would generate is byte-identical to the published one, and the capability
table it would produce is unchanged. Running the suite with those two files excluded leaves
**5,020 passed**, 10 skipped and **no failures and no errors**. (The total moves between
runs because the other session is adding tests as this is written; it was 5,008 an hour
earlier. This phase's own 178 are the stable part of it.) The remaining `ruff`/`mypy` errors are likewise confined to
`app/contracts/v1/envelopes.py`, `app/history/writer.py`, `app/changes/`, `tests/changes/` and
`tests/db/test_change_feed.py` — none of which this phase touched.

**No existing test was weakened, deleted or corrected by this phase.**

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| Every finding identifies the exact rule and supporting facts | `tests/risk_engine/test_reproduction.py::test_every_finding_of_every_rule_reproduces_from_its_evidence` — parameterized over all eleven rules — plus `test_evidence_is_never_empty` and the negative control `test_a_finding_stripped_of_one_record_no_longer_reproduces` |
| Risk scoring never replaces permission truth | `test_model.py::test_severity_ranks_but_does_not_add`; and structurally — nothing in `app/risk_engine` writes to a collected table, and every rights question goes through `app.access_engine` |
| Findings automatically resolve/reopen as underlying data changes | `tests/db/test_risk_findings.py::TestTheFindingLifecycle` — resolve, reopen, the three-event timeline, the preserved `first_detected_at`, and the resolution that carries no evidence digest |
| Sensitive-resource rules require explicit tagging | `TestBroadAccessOnSensitiveResource` (hermetic) and `test_the_sensitive_rule_fires_only_once_a_resource_is_tagged` (PostgreSQL) |

### What each suite covers

| File | What it pins |
| --- | --- |
| `tests/risk_engine/test_rules.py` | Each rule's match **and its refusals** — one "does not fire" test per coverage gap: the unread descriptor that is not a NULL DACL, the unenumerated group that is not an empty one, the undescribed trustee that is not a user, the well-known SID that is not an orphan, the four boundary reasons that only mean nobody looked |
| `tests/risk_engine/test_reproduction.py` | Evidence sufficiency for every rule, over an estate that exercises all eleven (asserted), with the negative control and the JSON round trip |
| `tests/risk_engine/test_model.py` | The catalog's exhaustiveness; every band has a severity; the finding key's stability and its exclusion of the rule version; `scope_covers` requiring **all** keys; `FactKind` equal to the contract's `ObservationKind`; the configuration parser's refusals |
| `tests/db/test_risk_findings.py` | The storage seam against a seeded estate; the lifecycle; the incremental guard (a scoped pass, an empty pass, a pass whose rule did not run); the truncated-load demotion; the evaluation record |

---

## Known limitations

1. **No HTTP surface.** `RiskService` is reachable from the backend only. Nothing on the web
   application shows a finding. Deliberate, and the same rhythm Phases 5 and 7A followed.
2. **A full pass is one bundle, capped at `MAX_RESOURCES` (5,000 directories).** An estate
   larger than that must be evaluated per share until a later phase pages it. This does **not**
   silently under-report: the load reports the truncation and the scope is demoted so nothing
   outside it can be resolved. It does mean a large estate needs an orchestration loop nobody
   has written.
3. **`enumerated` rests on a coarser rule than Phase 7A's closure.** A principal counts as
   enumerated when a *succeeded* run that reconciled *any* scope reported it. Phase 7A's
   closure resolves the key space per `(collector, scope kind)`; this does not, so a run that
   reconciled a `share` scope and happened to report a principal would mark that principal
   enumerated. Conservative in the wrong direction for exactly one rule. Reusing
   `app.history.closure` here is prerequisite 3.
4. **Findings are not ranked within a severity beyond confidence and key.** Two critical
   findings sort by rule name, which is stable and arbitrary.
5. **`redundant_access_paths` counts distinct trustees on one access control list**, so
   redundancy *across* the share and NTFS layers is not reported as redundancy.
6. **Nothing detects a sensitivity tag that matches nothing.** A tag pointing at a share that
   was renamed silently protects nothing.
7. **No acknowledgment or acceptance state.** `RiskFindingStatus` has two values on purpose —
   accepting a risk is a person's decision *about* a finding, and putting it on the row would
   let a re-evaluation overwrite somebody's judgment with a rule's opinion. Where it belongs is
   an open question, and the concurrent session's governance tables may be the answer.
8. **An incremental pass can miss a finding** when a membership change is more than
   `MAX_MEMBERSHIP_DEPTH` levels below a granted group. The next full pass catches it.
9. **The evaluation row's `error` column carries truncation notes**, which is a slight abuse of
   the name.

---

## Security and privilege assumptions

* **Unchanged for collectors.** No collector was modified and no new permission is required.
  Every rule reads facts ADG already had.
* **The engine is read-only, and the product still is.** Nothing here writes to a collected
  table, proposes a change to Windows, or is capable of doing so. Remediation guidance is prose
  describing what an administrator would do, and every entry names what the change could break
  — the most common way an access review does damage is removing a grant a service account was
  quietly relying on.
* **The risk tables carry the same disclosure class as the collected ones**, and a little more
  concentrated: a finding's evidence holds SIDs, account names, UNC paths and access masks,
  assembled into "here is the exposure and here is who has it". It inherits the retention
  question `object_versions` has; see `docs/operations/mvp-runbook.md` §6.
* **`ADG_RISK_CONFIGURATION_PATH` names a policy file, never a secret.** `.env.example`
  says so.
* **No new endpoint, so no new authorization decision.** The capability boundary in
  `app/api/__init__.py` was not touched by this phase.
* **One deliberate refusal to be clever.** ADG will not infer that data is sensitive. A
  heuristic tag would make the report look like it knows something about the business, and a
  reviewer acts on the difference between a critical finding and a medium one (ADR-0024).

---

## Migration and compatibility notes

* **`0009_risk_findings` is additive.** Three tables, nine indexes. No existing column, index
  or constraint changes.
* **There is no backfill, deliberately.** Findings are produced by running the rules;
  manufacturing them from current state at migration time would date every one of them to the
  migration and claim an open window nobody observed.
* **`downgrade()` drops the three tables.** Findings can be produced again by re-running the
  rules; their **history** cannot — when each opened, when it resolved, and how many times it
  came back are facts about the past a fresh evaluation cannot reconstruct.
* **It sits inside the concurrent merge.** `0012_merge_concurrent_phases` (the other session's)
  lists `0009_risk_findings` among its `down_revision` tuple. Verified: a database created from
  scratch and brought to the merged head contains all three tables with the declared columns,
  indexes and check constraints, and the 22 PostgreSQL tests pass against it.
* **The declared schema and the migration agree.** Checked by reflection: 17/23/11 columns,
  2/5/2 indexes and 4/10/5 check constraints, with no extras on either side.

---

## Prerequisites for the next prompt

1. **Decide where an acknowledgment lives.** Limitation 7. The concurrent session's
   `review_items` / `review_decisions` tables are plausibly the right home, and a finding that
   can be accepted is the difference between a report somebody reads once and one they use.
   Whatever is chosen, a re-evaluation must not be able to overwrite it.
2. **The HTTP surface.** The service layer is complete and typed. Open questions the next
   prompt must settle: which capability a finding requires (the concurrent session has already
   added a `risks:read` capability, so coordinate rather than adding a second); whether an
   evaluation can be triggered over HTTP or stays an operator action; and how a finding renders
   its evidence without becoming a wall of records.
3. **Reuse `app.history.closure` for `enumerated`.** Limitation 3. The rule table there already
   knows what a `(collector, scope kind)` pair is authoritative for, and duplicating a weaker
   version of that judgment in the risk loader is exactly the drift Phase 7A's bindings exist to
   prevent.
4. **Page a full evaluation.** Limitation 2. The scope machinery already supports it — a
   per-share pass is `evaluate_subject` with that share's directories — so what is missing is
   the loop and a way to combine the passes' scopes so the union may resolve.
5. **Hook incremental evaluation to run completion.** `RiskService.evaluate_run` exists and
   nothing calls it. The natural seam is beside `IngestionService.complete_run`, and the thing
   to decide is whether it runs inline (slowing a collector's last request) or is queued.
6. **Measure it.** The engine has been run against MVP-sized data only. Before an installation
   with millions of ACEs, somebody should time a full pass and decide whether the ceilings are
   sized right.

---

## Intentionally deferred

* **The HTTP surface and any UI.** Prerequisite 2.
* **Acknowledgment / risk acceptance.** Prerequisite 1 — it is a governance decision, not a
  property of a finding, and guessing at it here would have been the wrong place.
* **Paging a full evaluation.** Prerequisite 4; the scope machinery is built for it.
* **Automatic evaluation after a scan run.** Prerequisite 5.
* **Rule severity tuning against a real estate.** The shipped defaults are considered
  judgments, not measured ones, and are configuration precisely so an installation can
  disagree.

---

## `git status --short`

**Nothing from this phase is committed, and that is a decision rather than an omission.**

The working tree contains two sessions' uncommitted work interleaved in the same files.
`app/models/schema.py` carries this phase's three risk tables *and* the other session's
governance, simulation and incremental-collection tables; the migrations that create the
latter are themselves untracked. Committing the shared files would therefore either (a) bundle
the other session's half-finished work into a commit labeled Phase 8A, or (b) produce an
**incoherent** commit — a schema declaring tables no committed migration creates. There is no
third option available: every file this phase's code depends on (`schema.py`, `config.py`,
`risk_engine/__init__.py`) is a file the other session is also editing, and partial-hunk
staging is not available here.

The tree is left coherent and green for this phase's own gates. This phase's files are:

```
 M .env.example
 M README.md
 M backend/app/config.py
 M backend/app/models/schema.py
 M backend/app/risk_engine/__init__.py
 M docs/decisions/README.md
?? backend/app/repositories/risk.py
?? backend/app/risk_engine/catalog.py
?? backend/app/risk_engine/configuration.py
?? backend/app/risk_engine/engine.py
?? backend/app/risk_engine/evidence.py
?? backend/app/risk_engine/facts.py
?? backend/app/risk_engine/findings.py
?? backend/app/risk_engine/rules.py
?? backend/app/risk_engine/severity.py
?? backend/app/services/risk.py
?? backend/tests/db/test_risk_findings.py
?? backend/tests/risk_engine/
?? database/migrations/versions/0009_risk_findings.py
?? docs/architecture/risk-model.md
?? docs/decisions/0023-risk-findings-are-reproducible-from-their-evidence.md
?? docs/decisions/0024-sensitivity-is-declared-not-inferred.md
?? docs/operations/risk-rules.md
```

**What the next session should do about it:** serialize the two sessions. Let one finish and
commit, then have the other rebase its remaining work onto that commit. Committing this phase
alone is a two-minute job once the tree has one author again — `git add` the list above and
commit; the six modified files will by then carry only whichever session's edits remain
uncommitted.
