# Handoff — Phase 10B (`phase-10/02-access-review-workflow.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-10a-governance-model.md](phase-10a-governance-model.md)
**Collector contract version after this phase:** unchanged by this phase.
**`docs/contracts/v1/openapi.json`:** regenerated. **4 paths / 4 operations added**, all under
`/api/v1/governance`; two existing responses gained fields (see *Schemas and contracts*).

---

## Scope completed

A reviewer can now open a screen, see everything a decision needs, and record one — and the
interface refuses to let them assume the two things that would make the record untrue.

1. **The governance UI**: a queue of what was asked of *you*, a campaign list, a campaign's
   progress and coverage, a resource- or principal-centred item list, and one page per item.
2. **Baseline drift**, computed on read and reported beside the item, which is **never
   rewritten** (ADR-0031). Four verdicts, because an empty entry list means three different
   things.
3. **One call behind the review screen** — `GET /items/{id}/context` — assembling the frozen
   evidence, effective access at the baseline *and* now, the causal explanation split into
   direct and group-derived, open risk findings, and the change feed for the item's entries.
4. **A fifth decision, `investigate`**, distinct from `abstain`, with a migration that widens a
   check constraint and a `downgrade()` that refuses to destroy an attestation.
5. **A per-campaign comment requirement** that may only ever make the rule *stricter*.
6. **Bulk decisions**, fenced by four homogeneity rules, refused whole rather than partly
   applied, and individually auditable — one decision row, one audit event and one evidence
   digest per item.
7. **Reviewer progress and overdue reporting**, against each reviewer's own deadline.
8. **One migration, 4 new API operations, and 256 tests attributable to this phase.**

---

## The four things this phase exists to prevent

Each is a plausible version of this screen that produces a confident, defensible-looking
record of a judgment nobody actually made.

**A reviewer certifies a grant that no longer exists.** The campaign is frozen; the estate is
not. Every item now carries a drift verdict, and the campaign carries a summary.

**"It was removed" and "nobody has looked" render the same.** They are an empty entry list
either way, and they send a reviewer to opposite conclusions — one of which closes an item on
access that is still there. `DriftVerdict.UNOBSERVED` is separate from `REMOVED`, is not
counted as drift, and the sentence a reviewer reads is built on the server so no client can
collapse them.

**A revoke is recorded in the belief that access ends, on a grant also held through a group.**
`Reach.removing_reviewed_entries_leaves_access` answers that from the Phase 5 explanation, and
`null` — no explanation could be produced — is never rendered as the reassuring answer.

**Bulk certification turns a review into a formality.** Four rules fence it, and the refusal
names which one failed.

---

## Files and modules added or materially changed

### Added — backend

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/governance/drift.py` | 475 | The comparison, pure. Four verdicts, the content digest, and the rewritten-entry pairing |
| `backend/app/governance/review.py` | 672 | The review-context assembler. Derives nothing; asks five existing ADG answers about one item |
| `database/migrations/versions/0013_access_review_workflow.py` | 141 | `investigate`, `comment_requirement`, and a downgrade that refuses rather than deletes |

### Added — frontend

| File | Lines | Contents |
| --- | ---: | --- |
| `frontend/lib/governance.ts` | 399 | Wording, ordering and tone. Decides nothing |
| `frontend/components/Governance.tsx` | 693 | The server-rendered views |
| `frontend/components/DecisionForm.tsx` | 342 | The decision form, the bulk bar and the item selection — the only client components |
| `frontend/app/governance/page.tsx` | 101 | Queue first, campaigns second |
| `frontend/app/governance/campaigns/[campaignId]/page.tsx` | 227 | Coverage, drift, reviewers, items; `?mine`, `?target`, `?principal`, `?status`, `?view=drift` |
| `frontend/app/governance/items/[itemId]/page.tsx` | 179 | The review screen |
| `frontend/app/governance/actions.ts` | 97 | Two server actions. The proxy stays `GET`-only |

### Changed — backend

| File | What |
| --- | --- |
| `app/domain/governance.py` | `DecisionKind.INVESTIGATE`; `CommentRequirement` |
| `app/governance/model.py` | `comment_requirement` on the campaign; `decisions_requiring_rationale()`; `validate_rationale(..., requirement=)`; `RATIONALE_ALWAYS_REQUIRED` |
| `app/governance/repository.py` | `grants_on_targets_at`, `target_presence_at`, `items_by_ids`, `items_for_drift`, `late_decisions_by_assignment`, `queue_counts_for`; persists `comment_requirement` |
| `app/governance/service.py` | `bulk_decide`, `reviewer_queue`, `campaign_drift`, `item_context`; `ReviewerProgress` gains `overdue`/`late_decisions`/`completion`; `NotHomogeneous`; `MAX_BULK_ITEMS` |
| `app/api/governance.py` | 4 routes, 14 response models, and drift on `GET /items/{id}` |
| `app/models/schema.py` | `review_campaigns.comment_requirement` and its check constraint |
| `app/main.py` | `NotHomogeneous` → 422 |
| `app/domain/__init__.py` | Re-exports `CommentRequirement` |
| `tests/api/test_authorization.py` | Four route entries |
| `tests/governance/test_schema_vocabulary.py` | Taught about later widenings — see *A test that had to change* |

### Changed — frontend

| File | What |
| --- | --- |
| `lib/contracts.ts` | 26 governance response shapes |
| `lib/api/adg.ts` | 10 functions, 10 `USED_PATHS` entries, two of them writes |
| `lib/nav.ts` | A `Reviews` section behind `governance:read` |
| `tests/contracts.test.ts` | 26 schemas checked field by field, plus the decision vocabulary |
| `tests/nav.test.ts` | The new section, and that a plain viewer does not see it |

### Documentation

* `docs/architecture/access-review-workflow.md` — the workflow, the four rules, and what the
  interface refuses to imply.
* **ADR-0031** — baseline drift is reported beside a review item, never applied to it.
* `README.md` — the reviewer's screens and the two assumptions they prevent.
* `SECURITY.md` — a decision does not rewrite an item either; and decision rationales are now
  reachable in a browser, which widens an existing disclosure.

---

## Design decisions worth knowing

### Drift is computed on read and the item is never rewritten

Full argument in ADR-0031. The consequence worth repeating here: the audit trail stores the
**evidence digest** of what was certified, so refreshing an item's evidence would silently
invalidate every attestation already recorded against it.
`tests/db/test_governance_review.py::TestDriftDoesNotRewriteTheItem` digests the item rows
before and after a whole drift report and requires them identical.

### The comparison is on content, and `version_id` is excluded

`GrantEvidence.as_digestible` includes `version_id`, so `evidence_digest` moves whenever the
timeline writes a new row. An entry removed and restored to exactly its old state takes a new
row, and comparing on evidence identity would tell a reviewer their grant had changed when it
is character for character the same. `content_digest` is the same digest minus `version_id`;
the movement is reported as `evidence_reissued` rather than discarded.

### An ACE key is content-addressed, so a rights change is a remove plus an add

**Found by a test, not by design.** `keys.ntfs_ace_key` digests the path, trustee, type, mask
and flags, so widening Alice from Read to Modify tombstones one key and opens another. Paired
on the key alone, the most common thing that ever happens to a grant reported as "1 entry
removed; 1 entry added" and left the reviewer to notice the two were the same row.

`_pair_rewritten` now reports a removal and an addition sharing `(trustee, allow-or-deny)` as
one `changed` naming the fields that moved — **only when exactly one of each exists** for that
pair. Two out and two in could be matched two ways, and guessing which entry became which
would put a claim in front of a reviewer that ADG cannot support; those stay separate.

### Granting paths are `relation = GRANT` **and** `effect = CONTRIBUTES`

A path whose ACE matched and settled nothing (`REDUNDANT`), or whose rights the other layer
withholds entirely (`CONSTRAINED`), is not a way the principal reaches the resource. Counting
either would tell a reviewer access flows through a group that in fact gives them nothing.

A consequence worth knowing: `Everyone` on a **share** ACL *is* a contributing route and is
reported as one, tagged with its layer. Hiding it would make a revocation at one layer look
sufficient.

### The removal analysis is per entry, and says so

The explanation engine answers "what if this edge were gone". An item carrying an allow and a
deny therefore reports two independent answers and neither describes removing both. Stated in
the dataclass docstring, in the API field description and in the table caption, rather than
left to be discovered. For the common case — one entry — it is exact.

### The comment requirement may only raise the floor

`ck_review_decisions_reason_required` enforces "every decision but certify carries a rationale"
in the database. There is deliberately **no** `comment_requirement` value that relaxes it: a
per-campaign way to record an unexplained revocation would defeat the one rule that exists so
the person who loses the access can be told why.
`tests/governance/test_decision_rules.py` asserts the property across every value of the enum
rather than testing the two cases by hand, so a third value added later fails the suite unless
it is also a superset.

### `investigate` is not `abstain`

`abstain` says *the wrong person was asked* — the fix is to reassign. `investigate` says *the
right person was asked and the answer needs work first* — the fix is an investigation, and it
belongs to somebody else. Folded together, a queue of items awaiting follow-up would be
indistinguishable from a queue of misrouted ones.

### Writes go through server actions, not the proxy

`app/api/adg/[...path]/route.ts` forwards `GET` only, deliberately, so the browser can read the
API without ever holding a token. Turning it into a general write path so a review screen could
post a decision would weaken a rule that exists for a different reason. Two server actions are
the narrower tool: the session is read on the server and the browser still never sees a token.

### The client's bulk check is a courtesy and says so

`bulkEligibility` reproduces only the rules a client can evaluate from rows it already has, so
a disabled button can explain itself. It deliberately does **not** guess at drift or at the
assignment — the first needs a live comparison and the second the database — because a disabled
button with a wrong explanation is worse than the API's own refusal, which names the rule.

### A test that had to change, and why it is not a weakening

`tests/governance/test_schema_vocabulary.py` asserted that `0008_governance_model`'s frozen
`DECISION_KINDS` literal equals `DecisionKind`. Its own docstring says adding a value means a
**new** revision and never an edit to 0008 — so the moment `0013` widened the constraint, the
test contradicted its own instruction.

It now compares the enum against the vocabulary the **whole chain** leaves in force
(`EFFECTIVE_VOCABULARY`), and two tests were added in the same change to keep the guard from
being loosened: one pins 0008's literal to what it actually created, so the tempting fix of
editing a released migration fails here; the other requires every later widening to be a
superset, so a revision that *removed* a value — stranding every stored row carrying it — fails
too.

---

## Schemas and contracts

**No collector contract changed.** No observation kind, field or scope was added.

**One column added**: `review_campaigns.comment_requirement`, `NOT NULL DEFAULT 'standard'`,
with `ck_comment_requirement_valid`.

**One check constraint widened**: `ck_decision_valid` on `review_decisions` now admits
`investigate`. `ck_review_decisions_reason_required` is untouched, so an `investigate` decision
must carry a rationale exactly as `revoke` does.

**4 API operations added:**

| Method | Path | Capability |
| --- | --- | --- |
| `GET` | `/api/v1/governance/queue` | `governance:read` |
| `GET` | `/api/v1/governance/campaigns/{id}/drift` | `governance:read` |
| `GET` | `/api/v1/governance/items/{id}/context` | `governance:read` |
| `POST` | `/api/v1/governance/campaigns/{id}/decisions` | `governance:review` |

The queue is `governance:read`, not `governance:review`: reading what was asked of you is not
answering it, and an auditor checking for backlogs has to be able to look. The bulk route is
`governance:review` and **not** `governance:manage` — the separation of duties reaches batches
too, and `tests/db/test_governance_review.py` drives it as a governance administrator and
expects 403.

**Two existing responses gained fields**, additively:

* `CampaignView.comment_requirement`;
* `ItemDetailResponse.drift`, and `CampaignStatusResponse.overdue_reviewers` /
  `late_decisions`, with `ReviewerProgressView` gaining `completion`, `late_decisions` and
  `overdue`.

`openapi.json` regenerated; `frontend/tests/contracts.test.ts` checks all 26 governance schemas
field by field, in both directions.

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| Backend, hermetic | `pytest -m 'not smoke'` | **5,307 passed**, 10 skipped, 984 deselected, 48s |
| Backend, with PostgreSQL | see *Concurrency* | governance suites **162 passed**, 264s |
| Backend lint | `ruff check` / `ruff format --check` | **passed** on every file this phase touched |
| Backend types | `mypy app tests` | **clean for this phase's files**; 15 pre-existing errors remain in `app/changes/`, `tests/alerts/` and `tests/db/test_change*`, another phase's in-flight work |
| Frontend types | `tsc --noEmit` | **passed** for this phase's files (see *Concurrency* §4) |
| Frontend lint | `eslint .` | **passed** |
| Frontend tests | `vitest run` | **1,009 passed**, 28 files |
| Frontend build | `next build` | **compiled**, all three governance routes emitted |
| Collectors | `collector-test.ps1` | not run — **no collector file was touched** |

**256 tests are attributable to this phase**:

* **59 hermetic backend** — `tests/governance/test_drift.py` 33,
  `test_decision_rules.py` 23, and 3 added to `test_schema_vocabulary.py`.
* **56 requiring PostgreSQL** — `tests/db/test_governance_review.py`.
* **141 frontend** — `governance.test.ts` 36, `governance-views.test.tsx` 23, 81 in
  `contracts.test.ts` (26 governance schemas checked three ways each, plus the decision
  vocabulary), and 1 in `nav.test.ts`.

**No existing test was weakened or deleted.** Three were **updated**, each because this phase
changed the fact it asserted: the navigation label list, the OpenAPI snapshot, and the
migration-vocabulary check — the last of which gained two *additional* guards in the same
change so that the loosening it might look like cannot happen. See *A test that had to
change*.

### What each new suite pins

| File | What it pins |
| --- | --- |
| `tests/governance/test_drift.py` | Every verdict; the three ways an empty entry list arises and that they never render alike; a re-scan that changed nothing is not drift; a rewritten entry is one change and an ambiguous rewrite is not; the content digest ignores `version_id` and the evidence digest does not; a removal claims no cause |
| `tests/governance/test_decision_rules.py` | The rationale floor holds under **every** comment requirement, parametrized over the enum; `investigate` must say what to investigate; the four homogeneity rules with the case each refuses |
| `tests/db/test_governance_review.py` | The four verdicts against an estate scanned twice and moved four ways; the item rows byte-identical after a drift report; a revocation that would and would not end access; bulk decisions recorded per item with per-item audit events, and each of the six refusals; the queue, its emptiness and its ordering; overdue against a reviewer's own deadline; the whole thing over HTTP as each role |
| `frontend/tests/governance.test.ts` | `unobserved` is coloured neither as a problem nor as an all-clear; `null` reach is never the reassuring answer; completion never appears without exclusions; the bulk reasons name the rule; a deadline renders as a date rather than a countdown |
| `frontend/tests/governance-views.test.tsx` | The drift notice prints the API's sentence; a deleted target does not read as a withdrawn grant; unassigned items are called stuck; the comment box is visible before it is needed; a withheld form says which person to go to |

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| A reviewer can decide without opening AD tools | `test_governance_review.py::TestTheReviewScreenHasWhatADecisionNeeds` — baseline and current access, the group route named rather than left as a SID, the last change, the share item resolved against its published directory — and `TestTheWorkflowOverHttp::test_the_context_endpoint_answers_the_whole_screen_in_one_call` |
| Baseline drift is visible | `TestDriftTellsAReviewerWhatHasHappenedSinceTheFreeze` (four verdicts), `test_the_item_detail_carries_its_drift`, `test_the_campaign_drift_report_is_readable`, and the view tests |
| "Revoke" creates a proposed action, not a Windows change | Unchanged from 10A and still enforced: `tests/governance/test_isolation.py`, `tests/db/test_governance_isolation.py`. This phase adds `TestDriftDoesNotRewriteTheItem`, which closes the one new way a review could have written to something it should not |
| Audit records are complete | `TestABulkDecisionIsStillIndividuallyAuditable` — one event per item, each with its own evidence digest, `bulk` and `bulk_size` present, and the chain intact afterwards |

---

## Known limitations

1. **No campaign creation, assignment or lifecycle control in the UI.** The screens read and
   decide; creating a campaign, generating it, assigning reviewers and closing it are API calls
   a governance administrator makes with a client. The prompt's required work names the review
   workflow, and the create form is the smaller half of the remaining surface.
2. **No HTTP route revokes an assignment.** `GovernanceService.revoke_assignment` exists and is
   tested; nothing exposes it. A 10A gap, surfaced here because the queue's "a stood-down
   reviewer stops seeing it" behavior can only be exercised at the service level.
3. **The removal analysis is one entry at a time** — see *Design decisions*.
4. **The drift report is a page.** `MAX_DRIFT_ITEMS` is 500 and `covered`/`total_items` say so,
   but a 5,000-item campaign's banner describes the first 500 items and there is no paging
   through the rest in the UI.
5. **`unchanged` on a stale target overstates.** An entry whose version is still open reads as
   unchanged forever if nobody scans again. `target_certainty` carries the distinction and the
   summary says it in words; it is not a verdict of its own, so a caller ignoring the field
   will overstate.
6. **Nothing joins a decision, its proposal and a later `removed` verdict.** "Decided, and
   since carried out" is the most valuable thing this model still cannot show; the data exists
   in three places. 10A's handoff raises the same gap.
7. **Risk findings are read directly from `risk_findings`.** No filtering by rule, no
   acknowledgement, and the relevance ordering (`access` → `target` → `principal`) is fixed.
8. **Bulk supersession is deliberately absent.** A batch may not contain a decided item, so
   changing one's mind stays a per-item act.
9. **No notifications.** `overdue` is computed on read and reported; nobody is told. Unchanged
   from 10A.
10. **The queue has no paging.** It returns every active campaign a reviewer has an assignment
    in, which is bounded by how many campaigns exist rather than by a limit.

---

## Security and privilege assumptions

* **Unchanged for collectors.** No collector was modified; no new permission is required.
* **ADG stays read-only toward Windows.** `Capability.REMEDIATION_EXECUTE` remains reserved and
  held by no role. Nothing added here can write a collected table, and the syntax-tree guard
  covers the two new modules automatically — `tests/governance/test_isolation.py` globs
  `app/governance/*.py`.
* **A decision does not rewrite a review item either.** New in this phase and asserted.
* **The disclosure surface widened.** Decision rationales are free text about named people and
  are now reachable in a browser rather than only over the API. `governance:read` is still not
  granted to `viewer`; `SECURITY.md` §"Data sensitivity" now says this explicitly.
* **The access token still never reaches the browser.** Writes go through server actions; the
  `GET`-only proxy rule is unchanged.
* **Separation of duties reaches bulk decisions.** `governance:manage` cannot answer a batch,
  and a test drives that over HTTP.
* **The assignment gate is applied per item in a batch**, not once for the batch, and a batch
  containing one item assigned to somebody else is refused **whole**.

---

## Migration and compatibility notes

* **`0013_access_review_workflow` is additive and safe on a populated database.** The column is
  `NOT NULL` with a server default, so existing campaigns take `standard` — the behavior they
  already had — without a data migration. Widening a `CHECK` constraint admits values that were
  previously refused and invalidates no stored row.
* **`downgrade()` refuses to run while any `investigate` decision exists.** It raises and names
  the count rather than deleting or rewriting an attestation, which would be the failure the
  append-only trigger exists to prevent reached through the migration tool instead.
* **The revision is anchored on `0012_merge_concurrent_phases`**, the single head at the time it
  was written. See *Concurrency* §1: there are now two heads again.
* **The migration's literals are frozen**, like 0008's. A later widening adds a **new** revision
  and `EFFECTIVE_VOCABULARY` in `tests/governance/test_schema_vocabulary.py` gains an entry.
* **No existing column, index or constraint was altered** beyond the one widened check.

---

## Concurrency — read this before judging the tree

**Another Claude session was implementing other phases in this same working tree throughout**,
adding `app/alerts/`, `app/api/alerts.py`, `app/api/risks.py`, the risks and watches frontend,
and `0013_alert_pipeline`. Consequences a reader should know:

1. **The migration graph has two heads again**: `0013_access_review_workflow` (this phase) and
   `0013_alert_pipeline` (theirs), both on `0012_merge_concurrent_phases`. They are independent
   — this one alters two governance tables, that one creates its own — so a merge revision is
   the correct fix, exactly as `0012` was. **It is deliberately not added here**: the other
   session added `0012` and is clearly handling merges, and two merge revisions each naming
   both parents would produce two heads again, which is worse than one to add. `tests/db/
   conftest.py` runs `alembic upgrade heads`, so nothing is blocked meanwhile.
2. **The tree is not committed as a whole.** Shared files — `app/models/schema.py`,
   `app/domain/__init__.py`, `app/main.py`, `docs/contracts/v1/openapi.json`,
   `frontend/lib/contracts.ts` — carry both sessions' edits.
3. **Smoke tests were run against an isolated database**, `adg_10b` → `adg_10b_test`. Both
   sessions' suites truncate every table between tests, so sharing one makes runs fail at
   random. **Two sessions must not share a test database**; `adg_10b`/`adg_10b_test` exist
   locally and can be dropped.
4. **Two transient failures during this phase were theirs, not this phase's.** A `next build`
   failed on `app/risks/page.tsx` while they were mid-edit and passed two minutes later; a
   `tsc --noEmit` reports two errors in `app/api/watches/[watchId]/route.ts`, a file created
   during this phase's final checks. Neither is reachable from any governance module.
5. **Remaining lint/type failures are not this phase's.** `ruff` reports two in
   `app/api/alerts.py`; `mypy` reports 15 across `app/changes/`, `tests/alerts/` and
   `tests/db/test_change*`.
6. **`docs/contracts/v1/openapi.json` was regenerated twice**, the second time after their
   alerts and risks routers landed. It covers both sessions' routes and may go stale again
   while they work; `tests/contracts/test_openapi_snapshot.py` is the check.

---

## Prerequisites for the next prompt

1. **Add a merge revision** covering `0013_access_review_workflow` and `0013_alert_pipeline`.
   `alembic heads` should report one head before anything ships.
2. **Build the campaign-creation and assignment UI.** Everything a reviewer does has a screen;
   everything a governance administrator does is still an API call. The create form needs the
   scope picker, the baseline instant, the generation options and `comment_requirement`, and it
   should show the item count a generation would produce before committing to it.
3. **Expose assignment revocation over HTTP** (limitation 2). The service method and its tests
   exist.
4. **Join decision → proposal → later drift** (limitation 6). "Certified in March, revoked in
   April, still there in June" is the report this product is for, and every part of it is
   already stored.
5. **Page the drift report in the UI** (limitation 4), or decide that a campaign large enough to
   need it should be narrowed instead.
6. **Measure the context endpoint on a real estate.** It has been exercised against fixtures of
   a few dozen rows. It runs the explanation engine twice and the change feed once per entry;
   somebody should time it against a folder with a hundred trustees before it is trusted on a
   page load.
7. **Decide what happens to a campaign whose baseline retention has pruned** — 10A's limitation
   6, still open, and now more visible because a reviewer sees `unobserved` on every item.

---

## Intentionally deferred

* **Campaign creation, generation, assignment and closure screens.** The prompt's required work
  names the review workflow; these are the administrator's half.
* **Bulk supersession.** A batch may not contain a decided item, on purpose.
* **Remediation proposals in the UI.** The API records them; no screen creates or lists them.
  A `revoke` decision records the judgment, and the proposal is a separate act the prompt does
  not require a screen for.
* **Notifications and reminders.** Unchanged from 10A: a review nobody is told about is a
  review that does not happen, and nothing here tells anybody.

---

## `git status --short`

The tree is **uncommitted as a whole, deliberately** — see *Concurrency* §2. The files
belonging to this phase are listed below; everything else in `git status` belongs to the other
session's phases and is not this phase's to commit.

```
 M README.md                                     (shared)
 M SECURITY.md
 M backend/app/api/governance.py                 (10A's file, uncommitted; extended here)
 M backend/app/domain/__init__.py                (shared)
 M backend/app/domain/governance.py              (10A's file, uncommitted; extended here)
 M backend/app/main.py                           (shared)
 M backend/app/models/schema.py                  (shared)
 M backend/tests/api/test_authorization.py       (shared)
 M docs/contracts/v1/openapi.json                (shared; regenerated, covers both sessions)
 M docs/decisions/README.md                      (shared)
 M frontend/lib/contracts.ts                     (shared)
 M frontend/lib/api/adg.ts
 M frontend/lib/nav.ts
 M frontend/tests/contracts.test.ts
 M frontend/tests/nav.test.ts
?? backend/app/governance/drift.py
?? backend/app/governance/review.py
?? backend/tests/governance/test_drift.py
?? backend/tests/governance/test_decision_rules.py
?? backend/tests/db/test_governance_review.py
?? database/migrations/versions/0013_access_review_workflow.py
?? docs/architecture/access-review-workflow.md
?? docs/decisions/0031-drift-is-reported-beside-an-item-never-applied-to-it.md
?? docs/handoffs/phase-10b-access-reviews.md
?? frontend/app/governance/
?? frontend/components/Governance.tsx
?? frontend/components/DecisionForm.tsx
?? frontend/lib/governance.ts
?? frontend/tests/governance.test.ts
?? frontend/tests/governance-views.test.tsx
```

`app/governance/model.py`, `repository.py` and `service.py` are 10A's files, still uncommitted
in this tree, and were extended rather than added by this phase.

## No commit was made, and why

**Nothing was committed**, following the decision Phase 10A recorded and the other session has
kept to — `git log` still ends at `5537b16` (Phase 7C), with five phases' work in the tree.

The reason is not caution in general; it is that there is no commit available that is both
useful and true. Every shared file — `app/models/schema.py`, `app/domain/__init__.py`,
`app/main.py`, `docs/contracts/v1/openapi.json`, `frontend/lib/contracts.ts` — carries both
sessions' edits, so committing them captures a half-written state of somebody else's phase.
And committing only this phase's own files produces a tree that does not build: `drift.py`
imports a `CommentRequirement` that lives in a shared file, and the migration alters a table
whose declaration is in another. A commit that does not build is worse than no commit.

The index was empty when this phase finished (`git diff --cached` reported nothing), so
nothing of this phase's is staged and waiting either.

**What whoever commits should know.** The two sessions' work is separable by file except for
the five shared ones listed above, and in those the edits do not overlap: this phase added a
`comment_requirement` column and a `CommentRequirement` re-export, the other added alert and
risk tables and their routers. `git add -p` over those five, plus a whole-file add of
everything under *`git status --short`* above, is one coherent commit. Do it **after** the
merge revision in *Prerequisites* §1, so the committed graph has one head.
