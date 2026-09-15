# ADR-0030: A review campaign is a pure function of a baseline instant

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 10A — governance data model: owners, reviews, attestations
- **Deciders:** Phase 10A implementation

## Context

An attestation is a dated statement. "Alice certified this on 14 March" is only meaningful if
*this* can still be shown, months later, to be what Alice was looking at on 14 March.

Generating a campaign's items from current state gives none of that. A reviewer opens their
queue on Tuesday and sees Tuesday's permissions; a scan lands on Wednesday; the same reviewer
answers on Thursday against different evidence, and the record shows one decision with no
indication that the question changed underneath it. Worse, "I approved this" silently comes
to mean "I approve of whatever it is now" — which is the failure mode that makes access
reviews theater.

Phase 7A supplies exactly what is needed to avoid this. `object_versions` records the interval
over which each object held each state, it is append-only, and a version's interval is fixed
once written. A point-in-time read of it is deterministic.

A second question rides along: reviewers need to know whether the estate has changed since the
campaign was cut, and whether the record of what they reviewed is still intact. Those are
different questions and only the second is about the campaign.

## Decision

**A campaign carries `baseline_at`, and its item set is a pure function of the campaign row.**

```
items = generate(focus, scopes, options, grants_at(baseline_at))
```

Every input — focus, scopes, the three generation options, the instant — is stored on the
campaign. `grants_at` reads `object_versions` and nothing else: no current-state table, no
clock, no session. `generate` is pure.

Three consequences are taken as requirements rather than as pleasant properties:

1. **The baseline must be a past, timezone-aware instant.** A future one would freeze the
   campaign against state nobody has observed, so the item set would be empty today and
   different tomorrow — "reproducible against its baseline" would be false by construction. A
   naive one cannot be ordered against observations from a collector in another time zone.
   Both are refused at creation.

2. **The item set is frozen when the campaign leaves `draft`.** Generation is permitted only
   on a draft, which by definition has no decisions. Regenerating an active campaign would
   change what reviewers are answering underneath them and orphan the decisions already
   recorded.

3. **Verification is a re-run, not a second implementation.** `GovernanceService.generate` and
   `GovernanceService.verify_campaign` both call `_generate_from_baseline`. A separate
   "checker" would be a second description of the rules, and the first time the two disagreed
   nobody would know which was right — the same argument ADR-0018 makes for point-in-time
   effective access using the live engine.

The campaign stores a `snapshot_digest` over the whole item set and each item an
`evidence_digest` over its grants, so verification is one comparison first and a row-by-row
diff only when it fails.

**Divergence is a finding.** Because `object_versions` is append-only and version intervals
are fixed, the only ways a baseline can come to produce a different answer are: history was
edited directly, restored from a partial backup, or pruned by retention past the baseline
instant. The verification response says that in prose rather than returning three numbers.

## Consequences

**A campaign shows old permissions, on purpose.** A reviewer looking at a campaign cut a month
ago sees the estate as it was a month ago. That is the point, and it will be reported as a bug
by somebody. It is stated on every item — each grant carries the window it was observed over —
and "what does it look like now" is a different question with its own endpoints.

**Certainty travels with the evidence.** A grant frozen from a version the Phase 7 migration
reconstructed reports `backfilled`, and the item reports the weakest certainty of its grants.
Certifying reconstructed state as though it had been watched is the one way this feature could
make an audit worse rather than better.

**Certainty is deliberately not digested.** It is a property of the question asked — how firm
was this *at the baseline* — derived from the version's interval and the instant, not from the
entry. Digesting it would make an item's digest change when a later scan confirmed the very
same grant, which is exactly the case where nothing about the reviewed state changed.

**Retention can make an old campaign unverifiable.** `ADG_HISTORY_RETENTION_DAYS` deletes
closed versions past a window (Phase 7A). A campaign whose baseline falls before the retention
horizon will report itself not reproducible — correctly, since the evidence is gone. Nothing
currently stops retention from pruning past a campaign's baseline; see *Not decided here*.

**Item generation cannot become "what can this principal actually reach".** The effective
closure through group membership is not a stored fact at an instant; it is computed. It
remains available and equally frozen — `HistoryService.effective_access_at` over the same
instant is a pure function of the same versions — but it is not what an item is, because an
item must name something an administrator can remove.

## Not decided here

* **Protecting a campaign's baseline from retention.** Retention should arguably refuse to
  prune versions any open campaign's baseline depends on, or campaigns should pin them. Today
  neither happens, and verification reports the loss after the fact.
* **Re-cutting a campaign against a newer instant.** Today that is a new campaign. Whether a
  "refresh" that carries decisions forward is meaningful — and whether carrying an attestation
  onto different evidence is ever honest — is left open.
