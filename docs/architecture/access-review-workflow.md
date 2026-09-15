# The access review workflow

[`governance-model.md`](governance-model.md) describes what a review *is*: campaigns, items,
attestations, proposals, an audit trail, and the two gates that decide who may answer what.
This document describes what a person actually **does** with one, and the four things the
implementation does to keep that honest.

Phase 10A built the model and no interface. A model with no interface is a model nobody can
be wrong with; this phase is where a real person, under time pressure, on a Friday, records
something that an auditor will read in two years. Every design choice below exists because
there is a plausible version of this screen that produces a confident, defensible-looking
record of a judgment nobody actually made.

---

## 1. The frozen grant is the subject. Everything else sits beside it

A campaign is a pure function of a baseline instant (ADR-0030). A reviewer is therefore
answering about **what was true when the campaign was cut**, and the item's stored evidence is
what the decision is about and what the audit event records the digest of.

So the review screen is ordered: the frozen entries first, labelled *what you are certifying*,
and then — separately, never merged — everything that helps.

| Question | Answered by | Where it comes from |
| --- | --- | --- |
| Is it still like that? | `DriftView` | `app/governance/drift.py`, computed on read |
| What does this actually let them do? | `baseline_access` / `current_access` | the effective-access engine, over the baseline instant and over now |
| Why do they have it, and would revoking this stop it? | `ReachView` | the Phase 5 causal explanation |
| Is anything known to be wrong here? | `findings` | open `risk_findings` naming this target or principal |
| When did this last move? | `changes` | the Phase 7 change feed for this item's own entries |

**None of it is derived anew.** Each part is an existing ADG answer asked about this item. A
review screen that recomputed effective access, or decided for itself whether a grant had
drifted, would be a second implementation of a rule, and the two would disagree on the day it
mattered — the argument `docs/contracts/derived-responses.md` makes for clients and which
applies just as much inside the application.

`GET /api/v1/governance/items/{id}/context` assembles all five, because the acceptance
criterion is that a reviewer can decide **without opening AD tools**, and five separate calls
is how a client ends up making three of them.

---

## 2. Drift is reported, never applied

Full treatment in **ADR-0031**. The short version:

* The item is never rewritten. Drift is computed on read and stored nowhere.
* Four verdicts — `unchanged`, `modified`, `removed`, `unobserved` — because an empty entry
  list means three different things and they send a reviewer to three different actions.
* `removed` is a **measured** absence; `unobserved` means ADG holds nothing covering the
  instant and is not counted as drift.
* `target_present` distinguishes "the grant was withdrawn" from "the folder is gone".
* `target_certainty` says whether the comparison is against the estate or against ADG's last
  reading of it — an entry whose version is still open reads as unchanged forever if nobody
  scans again.
* The comparison is on **content**, not on `version_id`: an entry removed and restored
  identically is not drift, and `evidence_reissued` reports the version movement separately.

The sentence a reviewer reads — `ItemDrift.summary` — is built on the server and rendered
verbatim. It is the one place in this product where "it was removed" and "nobody has looked"
must not be collapsed, and a client that rebuilt the wording from the verdict and the counts
would be exactly the thing that collapses them.

**The campaign report is a page.** Drift is computed against live state on every request, so
`GET /campaigns/{id}/drift` compares at most `MAX_DRIFT_ITEMS` and returns `covered` beside
`total_items`. "Nothing has changed" must never be readable as more than "nothing in the part
that was checked".

---

## 3. A revocation that would not revoke anything says so

An item is a **direct** entry naming the principal. If the same principal also reaches the
target through a group, removing that entry changes nothing — and a reviewer who revokes it
believing access ends has recorded an attestation that says something untrue.

`ReachView` answers that from the Phase 5 explanation, which already separates the two:

* `direct_paths` / `group_paths` — granting paths that actually deliver rights
  (`relation = GRANT` and `effect = CONTRIBUTES`). A path whose ACE matched and settled
  nothing, or whose rights the other layer withholds entirely, is not a way the principal
  reaches the resource and is not counted as one.
* `routes` — one row per group, with the layer it lands on. A grant through `Everyone` on the
  share ACL is a real route and is reported as one: hiding it would make a revocation at one
  layer look sufficient.
* `removals` — what removing each reviewed entry would do, **one entry at a time**. The
  explanation engine answers "what if this edge were gone", so an item carrying an allow and a
  deny reports two independent answers and neither describes removing both. For the common
  case — one entry — it is exact.
* `removing_reviewed_entries_leaves_access` — the field this whole panel exists for. `null`
  means no explanation could be produced and is **never** rendered as the reassuring answer:
  "removing this works" has to be measured, not assumed.

---

## 4. A bulk action must be one question

Bulk certification is how an access review becomes a formality. It is offered, and it is
fenced.

`POST /campaigns/{id}/decisions` applies the same decision to several items, and refuses
unless all of the following hold:

| Rule | Why |
| --- | --- |
| One campaign | A stray id comes back **not found** rather than being acted on quietly. |
| One `target_kind` | Share permissions and file-system permissions are removed in different places, often by different people. Certifying both at once asserts two things and records one. |
| One shared axis — one principal across many targets, **or** one target across many principals | "Alice on eleven folders" and "eleven people on one folder" are each one judgment. An arbitrary basket is eleven judgments wearing one click. |
| Nothing already decided | Changing one's mind supersedes a specific attestation and needs its own reason. |
| Nothing drifted | An item whose grant has changed is precisely the one that needs reading, and a batch is where it would not be read. Refused **by id**, so the reviewer is told which to open. |
| At most `MAX_BULK_ITEMS` (200) | A ceiling on how much one click may assert, not on what the database can write. |

Every gate the single-item path applies still applies, **per item**: the campaign must be
active, the caller must hold the assignment on each, and the rationale must satisfy the
campaign's comment requirement. The batch is refused whole rather than applied partly.

And each item still gets its own decision row, its own audit event and its own evidence digest
in that event — plus `bulk: true` and `bulk_size`, so an auditor reading one event can see it
was one of many and find the others. That is what "individually auditable" has to mean: the
record afterwards is indistinguishable from the same decisions made one at a time, except that
it says it was a batch.

---

## 5. Comments are configurable upward only

`review_campaigns.comment_requirement` takes `standard` or `always`.

* `standard` — a rationale on every decision but `certify`. The default, and what every
  campaign created before the setting existed did.
* `always` — a rationale on every decision, `certify` included. For a review whose output an
  external auditor reads.

There is deliberately **no third value** making a rationale optional for a revocation.
`ck_review_decisions_reason_required` enforces that rule in the database, independently of the
application, and a campaign setting able to switch it off would be a way to produce unexplained
removals one campaign at a time. The person who loses the access is the one who pays for that.

`decisions_requiring_rationale()` returns a set that is always a superset of the database's own
floor, and `tests/governance/test_decision_rules.py` asserts that property across every value
of the enum rather than testing the two cases by hand.

---

## 6. `investigate` is not `abstain`

Phase 10A's vocabulary had four answers. This phase adds a fifth, and the distinction is the
reason it exists:

* **`abstain`** — *the wrong person was asked.* The campaign owner's fix is to reassign the
  item.
* **`investigate`** — *the right person was asked, and the answer needs work before it can be
  given.* The fix is an investigation, and it belongs to somebody else.

Folded together, a queue of items awaiting follow-up would be indistinguishable from a queue of
misrouted ones, and those go to two different people. `investigate` carries a mandatory
rationale like every other non-`certify` answer: "needs investigation" without saying what to
investigate hands the next person nothing.

Added by `0013_access_review_workflow`, which widens the check constraint rather than editing
`0008_governance_model`. Its `downgrade()` **refuses to run** while any `investigate` decision
exists, rather than deleting or rewriting an attestation.

---

## 7. Progress, deadlines and the queue

**A reviewer is late against their own deadline.** `ReviewerProgress.due_at` is the
assignment's where it has one and the campaign's otherwise: a reviewer given a shorter deadline
is late against theirs, and one given none inherits the campaign's rather than being exempt
from every date.

**Overdue means work outstanding.** It is false once nothing is pending — somebody who
finished late is not overdue, they are *done*, and `late_decisions` is where the lateness
shows. That count is taken over **current** decisions only: a late answer later corrected is
one item, not two failures, and a count including both could never go down.

**The queue is what was asked of you.** `GET /governance/queue` resolves through *active*
assignments across every active campaign, so a reviewer stood down stops seeing a campaign
without any item being edited — the same rule the `mine=true` item filter follows. It requires
`governance:read` rather than `governance:review`, because reading what was asked of you is not
answering it and an auditor checking for backlogs has to be able to look.

**`unassigned_items` is reported on its own.** An item attached to no assignment can never be
decided, so folding it into the pending total would show a campaign that merely looks slow
rather than one that is stuck.

---

## 8. What the interface refuses to imply

The frontend (`app/governance/`, `components/Governance.tsx`, `components/DecisionForm.tsx`,
`lib/governance.ts`) chooses wording and ordering and decides nothing. Four renderings are
load-bearing:

1. **Completion never appears without the exclusions beside it.** A campaign that skipped
   inherited entries and well-known trustees reviewed less than the estate holds, and "47 of 47
   certified" must not be readable as coverage of everything.
2. **`unobserved` is not coloured as a problem, and `removed` is not coloured as good news.**
   The first would train reviewers to ignore the colour; the second invites closing an item on
   a cause ADG never established.
3. **The comment box is always visible**, marked required or optional for the answer currently
   selected. A box that appeared when you picked "revoke" would teach reviewers that explaining
   themselves is an exception.
4. **A withheld decision form says which person to go to.** A missing capability needs whoever
   grants roles; an unassigned item needs a governance administrator. "The button is missing"
   sends somebody to the wrong one.

Writes go through **server actions**, not through the browser-facing proxy. That proxy forwards
`GET` only, deliberately, so that the browser can read the API without ever holding a token;
turning it into a general write path to let a review screen post a decision would weaken a rule
that exists for a different reason. `lib/governance.ts`'s `bulkEligibility` reproduces the
homogeneity rules a client can evaluate from rows it already has — **as a courtesy, so a
disabled button can say why** — and deliberately does *not* guess at drift or assignment,
because a disabled button with a wrong explanation is worse than the API's own refusal.

---

## Where each thing lives

| Concern | Module |
| --- | --- |
| The drift comparison, pure | `app/governance/drift.py` |
| Assembling an item's context | `app/governance/review.py` |
| Bulk rules, the queue, progress | `app/governance/service.py` |
| Batched current-state reads | `app/governance/repository.py` (`grants_on_targets_at`, `target_presence_at`) |
| The HTTP surface | `app/api/governance.py` |
| The vocabulary | `app/domain/governance.py` (`DecisionKind.INVESTIGATE`, `CommentRequirement`) |
| The migration | `database/migrations/versions/0013_access_review_workflow.py` |
| The screens | `frontend/app/governance/`, `frontend/components/Governance.tsx`, `frontend/components/DecisionForm.tsx` |
| The wording | `frontend/lib/governance.ts` |
