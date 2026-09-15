# ADR-0026: An affirmation is verified against stored state, and a checkpoint never leads the data it describes

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 7B (`phase-07/02-incremental-collection.md`)
- **Deciders:** Phase 7B implementation

## Context

[ADR-0025](0025-incremental-collection-is-bounded-by-its-source.md) establishes that a
file-system scan must read every descriptor on every pass, because nothing in the file system
reliably signals an ACL change. That leaves the read cost fixed and makes everything
downstream of the read the place where recurring cost can actually be reduced: a million
unchanged directories currently produce a million resource observations and several million
ACE observations, every one of which is transmitted, parsed, key-checked, upserted, and
folded into the version history — to arrive at the state the database already held.

Two mechanisms are needed to fix that, and both are mechanisms in which a collector tells the
server something the server cannot independently observe. That is the dangerous shape, and it
is the shape this record is about.

* An **affirmation** says *the object you hold is still correct*. If the server simply
  believed it, a collector with a stale cache could freeze an ACL in ADG's record for ever,
  and the record would look healthy the whole time.
* A **checkpoint** says *everything below this cursor has been read*. If the server recorded
  one that was ahead of the data that actually arrived, the next delta would start above
  objects that were never ingested — and nothing downstream could detect it, because a
  skipped object produces no error, no gap, and no number that looks wrong.

## Decision

**Neither claim is taken on trust, and the mechanisms that check them are different because
the two claims are different.**

### An affirmation is verified against what the server stores

1. An affirmation carries a **digest**, and for the one kind 1.4 permits — `ntfs_resource` —
   that digest is exactly the `acl_hash` of contract 1.2: a digest over the whole DACL,
   including `dacl_present` and `dacl_protected`, which the server already recomputes from
   the entries it stores.
2. The server **compares it against the digest it holds** and refuses the affirmation when
   they differ, when it holds no digest, when it has never seen the object, or when it has
   recorded the object as absent. Every refusal is returned to the collector naming the key,
   so the collector re-sends that object in full.
3. The digest must be computed from **this scan's reading** of the descriptor, never copied
   from the collector's record of what it last sent. The collector-side index therefore
   decides only what to *transmit*; the read happens either way, and
   `Get-AdgNtfsAffirmableDigest` takes the freshly computed digest as an argument so there is
   no code path by which an index entry alone can become an affirmation.
4. An accepted affirmation does exactly what a re-observation of the same state would do —
   extend the object's open version, record it as observed by this run — for the resource
   **and for the entries its digest covers**. That last part is not an optimization: closure
   decides what to mark absent from the `observations` table, so a resource affirmed without
   its ACEs would be a resource whose entire DACL the next reconciliation tombstoned.

Because an affirmed object counts as observed, **a run that affirmed everything it did not
re-send has still enumerated its whole scope** and keeps the right to reconcile it.

### A checkpoint advances only behind data that landed

1. A checkpoint is written **inside the transaction that wrote the batch** it describes, and
   again inside the completion transaction. Never speculatively, never in a separate
   transaction, and never before the observations it covers are stored.
2. A **run the server downgraded may not record one**, in addition to the contract's rule
   that a non-`succeeded` run may not send one. The two rules exist separately because the
   two parties know different things: the collector knows about its own errors, and only the
   server knows how many batches actually arrived.
3. A checkpoint advances only **within one issuer** and only **forwards**. A changed issuer
   or a backwards cursor is refused, and the refusal is **stored on the row**
   (`last_rejection_code`, cleared by the next accepted advance) rather than only logged —
   because a job whose cursor cannot advance keeps running, keeps succeeding, and keeps
   resuming from the same stale point, and nothing else about it looks wrong.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Trust the affirmation; skip the comparison | Makes ADG's record a function of the collector's cache. The failure is invisible and permanent. |
| Have the server return its own `state_hash` for the collector to affirm with later | Couples the collector to the server's internal state canonicalization, and re-deriving a digest the collector cannot compute means it cannot verify its own index. `acl_hash` is already published, already computed on both sides, and already cross-checked. |
| Let the collector send "unchanged" with no digest at all | Indistinguishable from a lie. There would be nothing for the server to compare. |
| Store affirmed digests in a new server-side table | A second copy of state the current-state row already holds, free to drift from it. `ntfs_resources.acl_hash` is the value, and it is the one the server already verifies against the entries. |
| Advance the checkpoint once, at completion | A delta that runs for an hour and dies at minute fifty has had fifty minutes of batches accepted; the next run would redo all of it. On a large estate "safe but wasteful" is a scan that never finishes. |
| Advance the checkpoint optimistically before sending | Puts the cursor ahead of the data by exactly one crash — the one failure mode that is undetectable. |

## Consequences

**Positive**

- A quiet tree costs a key and a digest per directory instead of a resource and its entries,
  with no loss of coverage and no loss of the right to reconcile.
- A collector cannot make the server keep a state the server does not already hold. The worst
  a wrong affirmation achieves is a refusal and a full re-send.
- A refused checkpoint is a visible condition on a row rather than a silent loss of progress.
- The cursor trails the data by construction, so the error mode is re-reading, never skipping.

**Negative / accepted costs**

- The collector keeps a digest index of roughly 120 bytes per path — about 120 MB for a
  million directories. It is bounded by `digestIndexMaxEntries`, and paths beyond the bound
  are sent in full with a warning saying so.
- An index written under different scan settings is discarded and rebuilt, so the first scan
  after a configuration change sends everything.
- `affirmations_refused` is a number an operator has to know how to read: a non-zero value is
  normal (it is what a changed ACL looks like), and a value near the affirmation count means
  the index is stale rather than the estate busy.

**Follow-up required**

- Only `ntfs_resource` may be affirmed. `smb_share` has no published whole-object digest; if
  one is ever added, the same mechanism applies unchanged.

## Compliance

- `backend/tests/db/test_incremental_collection.py::TestAnAffirmationIsVerifiedNotBelieved`
  covers all four refusals, including a changed ACL and a tombstoned object.
- `TestAnAffirmingRunStillEnumeratesItsScope::test_an_affirmed_directory_and_its_entries_survive_a_reconciliation`
  is the test that would fail if entries stopped being confirmed with their resource.
- `test_an_affirmation_does_what_an_identical_re_observation_does` pins the equivalence the
  cheap path relies on.
- `TestACheckpointTrailsItsData::test_a_downgraded_run_may_not_advance_the_cursor` covers the
  server-side half of the rule that a failed incremental run cannot declare the source
  current; `AdgOrchestrator.Run.Tests.ps1` covers the collector-side half.
- `collector/powershell/ntfs/tests/AdgNtfsDigestIndex.Tests.ps1` asserts that the index
  decides transmission only, and that a missing digest is never affirmable.
