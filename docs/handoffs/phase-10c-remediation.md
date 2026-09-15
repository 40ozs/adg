# Handoff — Phase 10C (`phase-10/03-remediation-guardrails.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-10b-access-reviews.md](phase-10b-access-reviews.md)
**Collector contract version after this phase:** unchanged by this phase.
**`docs/contracts/v1/openapi.json`:** regenerated. **13 paths / 15 operations added**, all
under `/api/v1/remediation`. No existing path or response was changed.

---

## Scope completed

A review decision can now become a precise, measured, approved, signed instruction — and ADG
still cannot change Windows, which is the point.

1. **Four tables** for change plans, their steps, their approvals and their signed exports.
2. **A plan may only ever narrow.** A modification that widens a mask, raises a share
   permission level, or narrows a *Deny* — which grants access — is refused before it is
   stored, and the structural version of the same rule is asserted over the whole change
   vocabulary at once.
3. **The blast radius is the production access engine**, reached by translating the plan into a
   Phase 9A overlay. No remediation-specific impact arithmetic exists.
4. **Stale-state rejection**: every step carries a content digest of the entry it was written
   against, re-checked against the **timeline** before submission, approval and export. Four
   verdicts, because *changed*, *missing* and *nobody has looked* lead to three different
   places.
5. **Three pairs of hands** — plan, approve, export — held by disjoint roles, checked against
   the person as well as the token, and refused by a database constraint whatever code writes
   it.
6. **A signed, canonical change-plan export** plus a generated PowerShell runbook that does
   nothing without `-Execute` and re-checks every precondition on the machine.
7. **A remediator interface with no implementation**, and five independent guards proving no
   API call can mutate Windows or AD.
8. **15 API operations, one migration, four ADRs, and 375 tests attributable to this phase.**

---

## What the model is, in six sentences

A **change plan** is a title, a reason, and an ordered list of **planned changes**, each naming
one object exactly — one `ace_key`, or one `(group, member)` edge — and carrying the state ADG
observed it in, digested over content. The plan is translated into a `SimulationOverlay` and
measured by `SimulationService`, which runs the production access engine twice; the report is
stored as an ordinary simulation, and a plan cannot be submitted without one measured against
the same collected state the plan was written against. An **approval** records the plan digest
and the collection basis it answered about, so editing the plan afterwards invalidates the
approval by arithmetic rather than by anybody remembering to clear it. Before anything is
signed, every step's frozen before-state is compared with what the timeline holds now, and
anything but equality invalidates the plan rather than merely refusing it. An **export** is a
canonical JSON document signed with HMAC-SHA256 plus a PowerShell runbook, produced by a third
person who is neither the requestor nor the approver. **ADG performs none of it**: there is no
write adapter in the codebase, no dependency that could provide one, no route that reaches an
executor, and no role that grants `remediation:execute`.

Full treatment: [`docs/architecture/remediation.md`](../architecture/remediation.md). For the
administrator carrying a plan out:
[`docs/operations/remediation-runbook.md`](../operations/remediation-runbook.md).

---

## Files and modules added or materially changed

### Added — the remediation layer

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/domain/remediation.py` | 221 | The six closed value sets. Pure, so `app/models/schema.py` can generate check constraints from them |
| `backend/app/remediation/model.py` | 1,201 | The plan, its steps, the snapshots, the invariants, the digests, the lifecycle. Pure |
| `backend/app/remediation/translate.py` | 196 | Plan → simulation overlay. Pure, total over the kind enum, and one-way |
| `backend/app/remediation/preconditions.py` | 292 | The stale-state comparison and its four verdicts. Pure |
| `backend/app/remediation/export.py` | 613 | The canonical document, the signature, and the runbook renderer. Pure |
| `backend/app/remediation/executor.py` | 338 | The interface, the refusal every deployment gets, and the fixture-backed lab adapter |
| `backend/app/remediation/errors.py` | 62 | Five failures, four status codes |
| `backend/app/remediation/repository.py` | 815 | Every query; the timeline reads behind the precondition check; the candidate join |
| `backend/app/remediation/service.py` | 708 | The workflow, the gates, the audit append, the transaction boundary |
| `backend/app/api/remediation.py` | 1,098 | 15 operations, each declaring its capability at the route |
| `database/migrations/versions/0015_remediation_change_plans.py` | 551 | Four tables, and the audit vocabulary widened by eight values |

### Changed

| File | What |
| --- | --- |
| `app/models/schema.py` | The four tables, `PLAN_TITLE_LENGTH`, and the remediation vocabulary imports |
| `app/domain/governance.py` | Eight `plan.*` values on `GovernanceEventType` (10A's file, still uncommitted) |
| `app/domain/__init__.py` | Re-exports the remediation vocabulary |
| `app/auth/roles.py` | Four capabilities, two roles, and why `admin` holds export and neither of the other two |
| `app/auth/dev_users.py` | `planner` and `approver` accounts — **one role per account**, so the separation of duties is visible on a developer's screen |
| `app/api/deps.py` | `RequestSettings` — see *Two defects found* |
| `app/api/__init__.py` | Includes the remediation router |
| `app/main.py` | Maps the four remediation errors onto 404/409/403/501 |
| `app/config.py` | Three settings and two startup validators |
| `tests/api/test_authorization.py` | 15 route entries and two path parameters |
| `tests/auth/test_roles.py`, `test_dev_users.py` | The role table and account list, written out literally as those files' convention requires, plus three new separation-of-duties properties |
| `tests/governance/test_schema_vocabulary.py` | Taught that `0015` widened `EVENT_TYPES` — the mechanism 10B built for exactly this |
| `README.md`, `SECURITY.md`, `.env.example` | The remediation section, the posture, the three settings |

### Documentation

* `docs/architecture/remediation.md` — the model, the gates, and **§8: what enabling a write
  adapter would require**, in seven items.
* `docs/operations/remediation-runbook.md` — for the administrator holding the document:
  verifying the signature without ADG running, reading the impact, what a stopped step means.
* **ADR-0035** — a change plan is an instruction, and ADG performs none of it.
* **ADR-0036** — an approval binds to a plan digest and a collection basis.
* **ADR-0037** — a plan's blast radius is the access engine, not an estimate.
* **ADR-0038** — proposing, approving and carrying out are three pairs of hands.

---

## Design decisions worth knowing

### The precondition check reads the timeline, not the ACL tables

The least obvious correctness property in the phase, and **found by a test rather than by
design**.

`ntfs_aces`, `smb_share_aces` and `membership_edges` are accumulate-only: ingestion has no
delete path at all, and a run that reconciles a scope records absence as a tombstone in
`object_versions` (ADR-0013). An ACE key is content-addressed, so widening a mask writes a
**new** row and leaves the old one standing.

The first implementation looked the `ace_key` up in `ntfs_aces`. It reported `satisfied` for an
entry that had been removed four days earlier, and would have reported `satisfied` for one
somebody had widened — which is a signed instruction going out against a mask nobody reviewed,
arrived at by reading the wrong table. It now reads through
`GovernanceRepository.grants_on_targets_at`, the same read baseline drift uses, so there is one
implementation of "what is on this ACL now" rather than two.

### A modification may only narrow, and the Deny clause is the one that matters

A mask-only rule passes an Allow converted to a Deny: the principal does lose access, so a
subset test is satisfied. But a Deny reaches every group the principal is in on every path that
touches the entry, which is a far larger blast radius than the Allow ever had. `validate_narrowing`
refuses it, refuses a widening mask, refuses a raised share level, and refuses a modification
that changes nothing — a step that leaves the entry as it is still costs a change window and
still reads as work done.

The structural twin is in `translate.py`: the only additive simulated change any plan can
produce is the `ADD_MEMBER` half of `replace_with_group`, asserted over every kind at once.

### An approval is of a digest, not of a plan

`plan_digest` covers everything an approver could have acted on and **excludes** the lifecycle
fields — otherwise approving a plan would change its digest and the approval could never record
the digest it approved. Export compares. Editing after approval therefore invalidates the
approval by arithmetic, which is the only way that rule survives a future author adding a field.

### Submission refuses; approval and export invalidate

A draft that fails its preconditions is refused and stays a draft: it has not been shown to
anybody, its steps are still editable, and the useful outcome is a report the planner can act
on. A plan that fails at approval or export is **invalidated**, because it has already been put
in front of somebody as a true statement about the estate and is not one.

### `replace_with_group` is two simulated changes in one overlay

Measuring the removal alone reports a loss the plan never intends; measuring the addition alone
reports a gain nobody is being given. Together they answer the question the plan actually asks:
*does this principal end up where they started?*

### The runbook is generated because the precondition checks are

A person writing this script by hand writes the guard for the first three steps and stops, and
the guard is both the tedious part and the part that prevents the failure. The script is also
a dry run by default, because a remediation script that is destructive when double-clicked is
one that will eventually be double-clicked.

Its **syntax is checked by PowerShell's own parser** over a plan containing every change kind
(`tests/remediation/test_runbook_is_valid_powershell.py`). A brace error in generated text is
otherwise discovered in a change window, by somebody with a half-applied plan.

The templates use `@@TOKEN@@` placeholders rather than f-strings or `str.format`: PowerShell is
made of braces, and a template language that also uses braces means every script block has to
be escaped by doubling — invisible when right, a syntax error in the *generated* script when
wrong.

### No unsigned export

With no signing key the export is refused, with the setting named. "Unsigned but marked as
such" would put the burden of noticing a missing banner on the one reader least able to bear
it. The signature proves the document came from this deployment unmodified and **nothing more**
— not that a named approver pressed a button — and the module docstring says so rather than
letting a reader assume otherwise.

### Two defects found, and what found them

* **The precondition check read the wrong table.** Described above. Found by
  `test_a_removed_entry_reads_as_missing_rather_than_unobserved`, which reported `satisfied`
  against an entry that had been gone for four days. It is the single most important defect
  this phase could have shipped.
* **Two API responses described fields they did not contain.** The plan and candidate listings
  passed `offset` and `returned` to `PageInfo`, which has neither. Pydantic silently drops
  unknown keyword arguments, so the routes worked, the tests passed, and the responses claimed
  a page position they never carried. Found by `mypy`, not by a test — worth noting, because no
  behavioral test would have caught it.

### One more thing the wiring got wrong

`app/api/deps.py` gained `RequestSettings`, reading `request.app.state.settings`. The routes
originally used `Depends(get_settings)`, which is a process-wide `lru_cache` over the
environment: the two agree for a server started from the environment and diverge for every
application built with explicit settings. The symptom was an export refused for "no signing
key" inside an application built with one, which reads as a bug in the export rather than in
the wiring. The same seam `get_session` already uses.

---

## Schemas and contracts

**No collector contract changed.** No observation kind, field or scope was added.

**Four tables added**, with these structural guarantees:

* **No foreign key from any remediation table to a collected one.** A plan names its targets by
  string key, exactly as `remediation_proposals` does — which is what lets it keep naming them
  after the object has gone. Asserted by
  `tests/remediation/test_schema_vocabulary.py::test_no_remediation_table_references_a_collected_one`.
* `ck_remediation_plans_approver_is_not_the_requestor` — the separation of duties, in the
  database.
* `ck_remediation_plans_approval_is_attributed` — an approved row names the approver, the
  digest and the basis, or it is not an approved row.
* `ck_remediation_plans_export_follows_approval`.
* `uq_remediation_approvals_one_answer_per_digest` — one answer per approver per version.
* `uq_remediation_changes_position` — one step per position.
* Check constraints for every shape invariant, including that a membership change carries an
  edge and no entry, that a removal carries no resulting state, and that a share modification
  sets exactly one right form.

**One existing constraint widened**: `governance_audit_events.ck_event_type_valid` admits eight
`plan.*` values. Widening a `CHECK` invalidates no stored row.

**15 API operations added** under `/api/v1/remediation`; `openapi.json` regenerated.

**Three new settings**: `ADG_REMEDIATION_SIGNING_KEY` (a secret),
`ADG_REMEDIATION_EXECUTION_MODE` (`disabled` by default and the only production value), and
`ADG_REMEDIATION_LAB_FIXTURE_PATH`.

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| Backend, hermetic | `pytest -m 'not smoke'` | **5,655 passed**, 10 skipped, 1,107 deselected, 62s |
| Backend, remediation smoke suites | `pytest tests/db/test_remediation_*.py` against `adg_10c` | **68 passed**, 99s |
| Backend, full smoke | `pytest` with `ADG_RUN_SMOKE_TESTS=1` against `adg_10c` | **6,761 passed**, 10 skipped, 1 xfailed, 0 failed, 26m40s |
| Backend lint | `ruff check .` / `ruff format --check .` | **passed** — 361 files |
| Backend types | `mypy app tests` | **clean for this phase's files**; 7 pre-existing errors remain in `app/changes/` and `tests/db/test_change*`, another phase's in-flight work |
| Collectors | `collector-test.ps1` | not run — **no collector file was touched** |
| Frontend | `frontend-check.ps1` | not run — **no frontend file was touched** |

### A first smoke run failed, and the cause was this session

The full suite was run twice. The first run reported **16 failures**, in
`tests/db/test_graph_api.py` and elsewhere — none of them in this phase's suites, and all of
them the shape of a test whose data disappeared underneath it.

The cause was mine and is worth recording rather than glossing: a second `pytest` process was
started against the same test database while the first was still running.
`tests/db/conftest.py::clean_tables` truncates **every** table before each test, so the second
process was emptying tables in the middle of the first one's tests. The second run, with
nothing else touching `adg_10c_test`, passed 6,761 tests with no failures and no errors.

The lesson is the one 10A and 10B both wrote down and this phase still managed to trip over:
**two test runs must not share a database.** It is worth repeating because the failure does not
look like contention — it looks like sixteen unrelated regressions, and the temptation is to
start debugging them.

**375 tests are attributable to this phase**:

* **307 hermetic** — `tests/remediation/`:

  | File | Tests | What it pins |
  | --- | ---: | --- |
  | `test_no_write_path.py` | 101 | The central criterion, five ways: syntax tree, imports, OpenAPI paths, role table, default configuration — each guard fed the mistake it exists to catch |
  | `test_model.py` | 61 | Every narrowing rule with the escalation it refuses; the set rules; the lifecycle; what the digests cover and deliberately do not |
  | `test_export.py` | 30 | The signature covers the bytes and a tamper breaks it; no key means no export; the runbook's two safety properties; the local-group and inherited-entry cases |
  | `test_isolation.py` | 24 | No module writes a collected table, from the syntax tree — and the guard ignores `detail.update(...)`, because a guard that fires on unrelated code is switched off |
  | `test_schema_vocabulary.py` | 24 | Enum, schema and migration agree; `0008` is not edited to catch up; the audit widening is a superset |
  | `test_preconditions.py` | 23 | All four verdicts; content vs identity; *missing* and *unobserved* never read alike; a change with no observation is unobserved, not skipped |
  | `test_translation.py` | 22 | The map is total over the enum; a plan can only ever take access away; `replace_with_group` is two changes in one overlay |
  | `test_executor.py` | 18 | The default refuses and says what would have to change; lab mode is refused in production, without a fixture, and for a target the fixture does not hold |
  | `test_runbook_is_valid_powershell.py` | 4 | The generated script parses, across every change kind (skipped without `pwsh`) |

* **68 requiring PostgreSQL** — `tests/db/test_remediation_workflow.py` (34),
  `test_remediation_schema.py` (19), `test_remediation_api.py` (15).

**No existing test was weakened or deleted.** Five were **updated**, each because this phase
changed the fact it asserted: the auditor's and administrator's capability sets, the active
role list, the default development accounts, the `/auth/config` account list, and the
governance vocabulary test — the last through `EFFECTIVE_VOCABULARY`, which is the mechanism
10B built for precisely this and not a relaxation. Three separation-of-duties properties were
**added** to `tests/auth/test_roles.py` in the same change.

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| ADG produces precise, simulated, reviewable remediation plans | `test_remediation_workflow.py::TestAPlanIsMeasuredBeforeItIsPutToAnybody` and `test_remediation_api.py::TestTheWholeLifecycleOverHttp` |
| Production write paths are disabled by default | `tests/remediation/test_no_write_path.py` (five independent guards) and `test_executor.py` |
| Stale proposals are rejected or re-simulated before execution | `test_remediation_workflow.py::TestStaleStateRejection` — the estate moved four ways under a plan, each refusal asserted, and `test_nothing_is_signed_when_the_export_is_refused` |
| Ordinary viewer/reviewer/admin routes cannot mutate AD/SMB/NTFS | `test_remediation_api.py::TestNoRequestChangesTheEstate` — every collected table digested around a whole lifecycle, plus a test that the digest would notice a change |

---

## Known limitations

1. **No UI.** No frontend file was touched. Every route is an API call.
2. **A group's observation is weaker than a resource's.** `target_observed` for a membership
   change means ADG holds *some* version of the group principal, not that its membership was
   re-enumerated on the latest run. A group scanned once and never again reads as observed.
3. **The precondition is per entry, not per ACL.** A plan whose entries are all unchanged passes
   even if the surrounding DACL was rebuilt around them. The ACL hash would catch that and
   would also refuse every plan after every unrelated edit; the cost of the narrower check is
   recorded rather than hidden.
4. **Nothing joins an exported plan to the scan that would confirm it.** "Exported in April,
   still there in June" is the report this phase makes possible and does not produce. The data
   exists in two places.
5. **A plan cites a decision; a decision does not know about its plan.** `GET /candidates`
   answers one direction only.
6. **`GET /candidates` covers review decisions and not risk findings.** A change may cite a
   `risk_finding_key`, and the column, the plan digest and the export all carry it — but
   nothing enumerates "which open findings have no plan", which is the same question asked of
   the other half of the product.
7. **`MAX_PLAN_CHANGES` is 100 and is a guess.** Nobody has written a hundred-step plan and
   timed the simulation of it.
8. **Invalidation is irreversible.** A plan invalidated by a transient condition — a scan
   mid-flight — cannot be recovered; a new plan must be written.
9. **The lab adapter proves the interface is implementable and nothing about a real adapter's
   semantics.** It mutates a dictionary.
10. **No delegation and no "approve on behalf of".** An approver who is away blocks the plan.
11. **The runbook is not idempotent.** Re-running it after a partial failure re-checks each
    precondition and stops at the first step already done, which is safe and is not a resume.

---

## Security and privilege assumptions

* **Unchanged for collectors.** No collector was modified; no new permission is required.
* **ADG stays read-only toward Windows, and this phase is where that could have stopped being
  true.** There is no write adapter, no dependency that could provide one, no route that
  reaches an executor, and `Capability.REMEDIATION_EXECUTE` is granted by no role.
  `tests/auth/test_roles.py::test_no_role_may_write_the_estate` still passes unchanged, and
  `tests/remediation/test_no_write_path.py` adds four more independent checks.
* **A new secret exists.** `ADG_REMEDIATION_SIGNING_KEY` signs exported change plans. It is
  configuration and never source control; `.env.example` says so. Rotating it changes the
  published key identifier, so a verifier holding the old key reports "signed with a key I do
  not have" rather than reporting tampering.
* **The signature's claim is narrow and stated.** It proves the document came from this
  deployment unmodified. It is not a per-person signature: anybody holding the key can produce
  one, and accountability lives inside the document and in the audit chain.
* **A new disclosure exists.** A change plan says who proposed taking somebody's access away
  and why — the same category as a decision rationale. `remediation:read` is therefore **not**
  granted to `viewer`.
* **Separation of duties is enforced in three places** and is limited in the same way ADR-0029
  states: anyone with the database credentials can do anything, so it guards against accident
  and casual misuse rather than a determined operator.
* **Two new roles must be assigned before remediation is usable at all.** Nothing is enabled by
  upgrading, and with no signing key nothing can be exported either.
* **The exported document and the runbook contain SIDs, paths and group names**, and are
  intended to leave ADG. They are as sensitive as the estate they describe, and the runbook
  lands wherever the operator saves it.

---

## Migration and compatibility notes

* **`0015_remediation_change_plans` is additive and empty.** Four new tables, no rows written;
  nothing to backfill, because no plan has been written yet. Fast on any estate.
* **One existing constraint is widened**, admitting values previously refused. No stored row is
  invalidated.
* **`downgrade()` refuses while any signed export exists**, and refuses again while any `plan.*`
  audit event exists. An export is a document somebody may be holding and its row is the only
  record of what was signed; and the audit trail is append-only by trigger, so a downgrade that
  reached around the trigger to delete history would be the exact failure the trigger prevents,
  reached through the migration tool. The same choice `0013` makes about an attestation.
* **The revision is anchored on `0014_merge_alerts_and_reviews`**, which was the single head.
  `alembic heads` reports **one head**, `0015_remediation_change_plans` — the graph is
  linearized, which 10A and 10B both listed as a prerequisite.
* **The migration's literals are frozen.** A later widening adds a **new** revision and an entry
  in `EFFECTIVE_VOCABULARY`.
* **No existing column, index or constraint was altered** beyond the one widened check.

---

## Concurrency — read this before judging the tree

No other session was observed writing to this tree during this phase (the most recent foreign
modification was roughly forty minutes before it began). The tree nevertheless carries **six
phases' uncommitted work** — 7B, 8A, 8B, 9A, 9B, 10A, 10B and now 10C — for the reason 10A
recorded and every phase since has kept to.

1. **`alembic heads` now reports one head.** `0015_remediation_change_plans`. The prerequisite
   10A and 10B both raised is satisfied.
2. **Seven pre-existing type errors remain**, all in `app/changes/` and `tests/db/test_change*`,
   which belong to another phase's in-flight work. `mypy` is clean for every file this phase
   touched.
3. **Smoke tests were run against an isolated database**, `adg_10c` → `adg_10c_test`. Both
   databases exist locally and can be dropped. **Two sessions must not share a test database**,
   and neither must two runs in the same session: every suite truncates every table between
   tests. See *A first smoke run failed* above for what that looks like when it happens.
4. **`docs/contracts/v1/openapi.json` was regenerated** and covers every router in the tree, not
   only this phase's. It may go stale again if another session adds a route;
   `tests/contracts/test_openapi_snapshot.py` is the check.

---

## Prerequisites for the next prompt

1. **Build the remediation UI.** The API is complete and typed and `openapi.json` is current.
   The screens the model implies: a candidate list ("decided, and nothing has happened"), a plan
   editor that shows the blast radius before submission, an approver's screen that puts the
   impact and the preconditions where they cannot be missed, and an export page that offers the
   runbook and the verification recipe together.
2. **Join an exported plan to the scan that confirms it** (limitation 4). "Exported in April,
   still there in June" is the report this phase makes possible, every part of it is stored, and
   nothing produces it.
3. **Commit the tree.** The migration graph has one head, the hermetic suite passes, and
   `mypy`'s remaining errors are attributable to one package. This is the first phase boundary
   at which a coherent commit is available; see *No commit was made, and why*.
4. **Measure a large plan** (limitation 6). Simulating a hundred-step plan across several shares
   has never been timed, and `MAX_PLAN_CHANGES` is informed by readability rather than by
   measurement.
5. **Decide what a partially executed plan should look like** (limitation 10). Today an
   administrator who stops halfway has a plan ADG still calls exported and an estate that
   matches neither state.
6. **Consider whether `remediation_approver` should see the review that produced the plan.** It
   holds `governance:read` today, which is the right answer for a plan built from a campaign and
   possibly too much for one built from an operator's judgment.
7. **Re-read §8 of the architecture document before anybody proposes a write adapter.** It is
   seven items, not one.

---

## Intentionally deferred

* **Any UI.** The prompt's required work names the model, the interface, the policy, the export
  and the tests.
* **Execution of any kind.** See ADR-0035 and `docs/architecture/remediation.md` §8.
* **Editing a plan after submission.** A plan is rewritten rather than amended, so that an
  approval always describes a specific set of changes.
* **Bulk plan creation from a whole campaign.** `GET /candidates` lists what is outstanding; a
  planner assembles the plan. A one-click "plan everything this campaign revoked" would produce
  exactly the plan-nobody-read this phase's ceilings exist to prevent.
* **Registering the remediation responses as published JSON Schemas** in
  `docs/contracts/v1/derived/`. That directory is for answers ADG *derives from observations*; a
  change plan is a record and an instruction, and adding it there would blur the distinction
  ADR-0028 exists to keep sharp.

---

## `git status --short`

The tree is **uncommitted as a whole**, following the decision Phase 10A recorded and every
phase since has kept to. The files belonging to this phase are listed below; everything else in
`git status` belongs to earlier uncommitted phases.

```
 M .env.example
 M README.md
 M SECURITY.md
 M backend/app/api/__init__.py
 M backend/app/api/deps.py
 M backend/app/auth/dev_users.py
 M backend/app/auth/roles.py
 M backend/app/config.py
 M backend/app/domain/__init__.py                (shared)
 M backend/app/main.py                           (shared)
 M backend/app/models/schema.py                  (shared)
 M backend/tests/api/test_authorization.py       (shared)
 M backend/tests/auth/test_dev_users.py
 M backend/tests/auth/test_roles.py
 M docs/contracts/v1/openapi.json                (shared; regenerated, covers every router)
 M docs/decisions/README.md                      (shared)
?? backend/app/api/remediation.py
?? backend/app/domain/remediation.py
?? backend/app/remediation/
?? backend/tests/remediation/
?? backend/tests/db/test_remediation_api.py
?? backend/tests/db/test_remediation_schema.py
?? backend/tests/db/test_remediation_workflow.py
?? database/migrations/versions/0015_remediation_change_plans.py
?? docs/architecture/remediation.md
?? docs/operations/remediation-runbook.md
?? docs/decisions/0035-a-change-plan-is-an-instruction-never-an-act.md
?? docs/decisions/0036-an-approval-binds-to-a-plan-digest-and-a-collection-basis.md
?? docs/decisions/0037-a-plans-blast-radius-is-the-access-engine-not-an-estimate.md
?? docs/decisions/0038-proposing-approving-and-carrying-out-are-three-pairs-of-hands.md
?? docs/handoffs/phase-10c-remediation.md
```

`backend/app/domain/governance.py`, `backend/tests/governance/test_schema_vocabulary.py` and
`backend/app/governance/*` are 10A's and 10B's files, still uncommitted in this tree, and were
**extended** rather than added by this phase.

## No commit was made, and why

Nothing was committed. `git log` still ends at `5537b16` (Phase 7C), with eight phases' work in
the tree.

The reason is the one 10A gave and 10B repeated: every shared file — `app/models/schema.py`,
`app/domain/__init__.py`, `app/main.py`, `docs/contracts/v1/openapi.json` — carries several
sessions' edits, and committing them captures a half-written state of somebody else's phase.
Committing only this phase's own files produces a tree that does not build: `app/remediation/`
imports vocabulary from shared files, and `0015` creates tables whose declarations live in
another.

The index was empty when this phase finished, so nothing of this phase's is staged and waiting.

**What has changed since 10B wrote the same paragraph, and why the next phase should commit.**
The migration graph now has **one head**, which was the blocking prerequisite both earlier
handoffs named. The hermetic suite passes whole. `mypy`'s remaining errors are confined to one
package. A single commit of everything under `git status`, after that package's owner has
finished, is now both available and true — and the longer the tree stays uncommitted, the more
of ADG's history is a single unreviewable change.
