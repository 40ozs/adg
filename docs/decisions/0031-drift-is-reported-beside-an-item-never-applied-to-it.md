# ADR-0031: Baseline drift is reported beside a review item, never applied to it

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 10B — Access review workflow and UI
- **Deciders:** Phase 10B implementation

## Context

ADR-0030 makes a campaign a pure function of a baseline instant: its items are generated from
the versions of `object_versions` that covered that moment, and a reviewer therefore certifies
*what was true when the campaign was cut*. That is what makes an attestation mean anything
years later.

The cost is that the estate keeps moving. A campaign frozen on the 2nd and answered on the
16th is answered about the 2nd, and in between somebody may have widened the very grant under
review, withdrawn it, or deleted the folder it sits on. A reviewer shown only the frozen
evidence certifies something that may no longer exist; a reviewer shown only the current state
is not doing a review at all, because the question they were asked has quietly changed.

Three further constraints shaped the answer:

* **An empty current entry list means three different things.** The target was read and no
  longer names this principal; the target itself was deleted; or ADG holds no version covering
  the instant and has not looked. The first is often a carried-out revocation, the second is a
  different event entirely, and the third is not an observation at all. Rendered alike, a
  reviewer told "already removed" closes an item on access that is still there.
* **`ace_key` is content-addressed.** `keys.ntfs_ace_key` digests the path, trustee, type,
  mask and flags, so widening somebody's rights does not edit an entry — it tombstones one key
  and opens another.
* **A version's `version_id` moves without the grant changing.** An entry removed and restored
  identically takes a new row, and a comparison over version identity would report drift on a
  grant that is character for character the same.

## Decision

**Drift is computed on read, reported as its own object, and stored nowhere. The item is never
rewritten.**

`app/governance/drift.py` is a pure module. `GovernanceRepository.grants_on_targets_at` and
`target_presence_at` read the current state; `compare_grants` folds the two sides into an
`ItemDrift`. Nothing in the path writes `review_items`, and
`tests/db/test_governance_review.py::TestDriftDoesNotRewriteTheItem` asserts every item row is
byte-identical after a whole drift report has run over the campaign.

Four verdicts, and the third and fourth are kept apart deliberately:

| Verdict | Meaning |
| --- | --- |
| `unchanged` | The same entries granting the same rights. |
| `modified` | The grant exists and differs. |
| `removed` | A **measured** absence: ADG holds a version covering the instant and it carries no such entry. `target_present` says whether the target itself survived. |
| `unobserved` | ADG holds nothing covering the instant. Never rendered as a removal, and **not counted as drift**. |

**The comparison is on content, not on version identity.** `content_digest` covers the same
fields as `GrantEvidence.as_digestible` minus `version_id`. A grant whose rows were reissued
reports `unchanged` with `evidence_reissued` set, so the version movement is available and does
not make the verdict.

**A rewritten entry is one change, when the pairing is unambiguous.** A removal and an addition
sharing `(trustee, allow-or-deny)` are reported as one `changed` naming the fields that moved —
but only when exactly one of each exists for that pair. Two out and two in could be matched two
ways, and guessing would put a claim in front of a reviewer that ADG cannot support.

**A drifted item may not be answered in bulk.** `GovernanceService.bulk_decide` refuses a batch
containing one, by id, so the reviewer is told which to open.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Regenerate the campaign's items against the present | Makes every attestation already recorded a statement about whatever the grant became. The audit trail stores the evidence digest of what was certified; refreshing it silently invalidates that record. This is the failure ADR-0030 exists to prevent. |
| Store the drift verdict on the item row | A stored verdict is stale the moment the next scan lands, and a reviewer reading a day-old "unchanged" has been told something false by a field that looks authoritative. Computing on read costs two indexed reads over the item's own target. |
| Report drift as a boolean | "It changed", "it is gone", "the folder is gone" and "nobody has looked" send a reviewer to four different actions. A boolean forces the client to guess which, and the reassuring guess is the dangerous one. |
| Compare `evidence_digest` directly | Includes `version_id`, so an entry removed and restored identically reports as drift. A banner that cries wolf is a banner nobody reads. |
| Treat `unobserved` as drift | Every report taken while a collector was down would show the whole campaign as moved, and the real changes would be buried in it. |
| Pair rewritten entries by trustee alone | A trustee legitimately holds several entries with different flags. An ambiguous pairing reported as a change names two entries as one another on no evidence. |

## Consequences

**Positive**

- The frozen evidence stays the subject of the decision, and the audit trail's evidence digest
  keeps meaning what it meant when the decision was recorded.
- "This was removed" and "nobody has looked" cannot be collapsed by a client: the sentence a
  reviewer reads is built server-side in `ItemDrift.summary` and rendered verbatim.
- A reviewer certifying a grant that no longer exists is told so, on the item and on the
  campaign, without anything being rewritten under them.
- Bulk certification cannot quietly cover an item whose access has changed.

**Negative / accepted costs**

- Drift costs two reads per distinct target on every request that shows it. The campaign report
  is therefore a **page** — `MAX_DRIFT_ITEMS` — and carries `covered` beside `total_items` so
  "nothing has changed" is never readable as more than "nothing in the part that was checked".
- The verdict can disagree between two requests seconds apart, if a scan lands in between. That
  is correct and is what `compared_at` is for.
- `unchanged` on a target nothing has rescanned is true about the record and misleading about
  the estate. `target_certainty` carries the distinction and the summary says it in words; it
  is not a verdict of its own, so a caller who ignores the field will overstate.

**Follow-up required**

- Nothing joins a `revoke` decision, its remediation proposal, and a later `removed` verdict on
  the same item. The data for "decided, and since carried out" exists in three places and is
  not assembled. Phase 10A's handoff raises the same gap.

## Compliance

- `tests/governance/test_drift.py` — every verdict, the three ways an empty entry list can
  arise, the rewritten-entry pairing and its ambiguous case, and that the content digest
  ignores `version_id` while the evidence digest does not.
- `tests/db/test_governance_review.py::TestDriftTellsAReviewerWhatHasHappenedSinceTheFreeze` —
  the four verdicts against a real estate scanned twice.
- `tests/db/test_governance_review.py::TestDriftDoesNotRewriteTheItem` — the item rows are
  byte-identical after a drift report.
- `frontend/tests/governance.test.ts` and `governance-views.test.tsx` — the client renders the
  backend's sentence, does not colour `unobserved` as a problem, and does not colour `removed`
  as good news.
