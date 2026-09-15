# ADR-0018: History is a versioned observation log; current state stays a projection

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 7A — historical observation and snapshot model
- **Deciders:** Phase 7A implementation

## Context

Phases 0 through 6 stored one row per object, holding its latest known state and the
provenance of the observation that produced it. Two questions an auditing tool exists to
answer are unanswerable in that schema, and for the same reason — a newer observation
overwrites the older one in place:

* *what was true on the 3rd?*
* *when did this stop being true?*

Adding temporal semantics had to satisfy three constraints at once. The **current-state query
path is the whole product**: twenty repositories, a measured query-cost budget per endpoint,
and an effective-access engine whose correctness is pinned by hundreds of tests. The
**temporal rules are identical for all seven object kinds** — what opens a version, what
closes one, what may be inferred absent, what retention may remove. And **absence is the
dangerous operation**: every other write adds knowledge, while recording an object as gone
removes access from the record, and an audit tool that removes access nobody revoked reports
a permission problem as solved.

## Decision

History is stored in **one table, `object_versions`**, holding one row per state an object
was observed to hold, keyed `(object_kind, object_key, valid_from)`. The current-state tables
are **unchanged** and remain the projection every existing query reads.

1. **One generic table, not seven mirrors.** `object_kind` is the contract's own
   `ObservationKind`, and the descriptive columns are stored as JSONB with a SHA-256 digest
   of their canonical form. Seven per-kind history tables would be seven copies of the
   temporal rules and seven chances for them to drift; this way the rules exist once and are
   tested once. The columns a query needs to be indexed on — `container_key` (the object this
   one is an entry of) and `related_key` (the far end of the relation it expresses) — are
   projected out of the same row dictionary the current-state upsert writes, so a version's
   indexed columns cannot describe a different object from its own state blob.

2. **History is written from the row that was stored, not re-derived.** `HistoryWriter.record`
   is called from `apply_batch` with **the same dictionaries the current-state upsert is
   given**, inside the same transaction. The timeline of an object and its current state
   therefore cannot describe two different things, and a batch that fails halfway leaves
   neither.

3. **Only a reconciled scope may record an absence**, and only the kinds its collector
   reports, and only inside the scope's own key space (`app/history/closure.py`). Absence is
   recorded as a **tombstone** — a version with no state — never as a deletion: nothing in
   ADG deletes a collected fact, and "ADG knows this was gone on Tuesday" must stay
   distinguishable from "ADG has nothing for Tuesday".

4. **Point-in-time effective access reuses the live engine.**
   `HistoricalMembershipRepository` and `HistoricalResourceRepository` are subclasses of the
   repositories `AccessService` already takes by injection, with their reads redirected
   through the temporal predicate. There is no second access algorithm.

5. **The migration backfills one version per existing row**, spanning `first_observed_at` to
   `last_observed_at`, marked `origin = 'backfilled'`, and every answer drawn from one
   reports `Certainty.BACKFILLED`.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| A history table mirroring each of the seven current-state tables | The temporal rules would be implemented seven times. The invariants that make history readable — non-overlap, one open version, close-before-open — are the same for an ACE and a principal, and seven copies of them is seven chances to get one wrong in a way nothing notices |
| Make the current-state tables themselves temporal (add `valid_from`/`valid_to`, key on them) | Every existing query, index, uniqueness constraint and cost budget would change shape at once, to add a capability nothing in the product yet reads. The acceptance criterion "current-state queries still work" would have become a re-test of the whole product rather than a property |
| Write history from a database trigger | Puts the rule that decides what counts as a change into PL/pgSQL, where it cannot be unit-tested and cannot share the canonicalization the application digests with. The project's stated preference is deterministic, typed, testable domain functions |
| Delete rows from current-state tables when a reconciled scope finds them gone | Destroys the evidence. "This share was deleted" and "this share's last reading is from March" are both findings, and only one of them survives a delete |
| Add `is_present` / `absent_since` columns to the current-state tables | A second copy of a value the open version already holds, which can disagree with it — the failure mode this codebase rejects everywhere else (`SmbShare.unc_path` and `is_hidden` are derived for the same reason) |
| A separate historical effective-access implementation | Two implementations of the most safety-critical code in the product. The first time they disagreed, nobody would know which was right |

## Consequences

**Positive**

- Every Phase 0–6 query, index and plan is unchanged; the current-state acceptance criterion
  holds by construction.
- The temporal rules, the closure rules and the retention rules each exist exactly once.
- Point-in-time effective access is the live engine, so every coverage finding, DACL
  projection and deny-ordering rule applies to a historical answer without being reimplemented.
- A version's state and the current-state row are written from one dictionary, so they cannot
  disagree.

**Negative / accepted costs**

- ~~**A live answer still counts an entry a reconciled scan proved is gone.**~~ Nothing
  deletes a collected fact, and Phase 7A did not route current-state reads through presence,
  so the live effective-access answer could overstate access where the as-of-now answer did
  not. **Since paid off** (2026-09-15): current-state reads go through one presence
  predicate, stated in `app/models/current.py` and described in
  `docs/architecture/current-state-presence.md`. The decision this ADR records is unchanged —
  nothing is deleted, and current state is still a projection; what changed is which
  projected rows a live query selects. The test that pinned the divergence now asserts the
  agreement.
- JSONB state is not column-typed. Reconstruction goes through the same record constructors
  the live repositories use, so callers still get typed records, but the database cannot
  constrain the contents of a version's state the way it constrains the row it came from.
- Ingestion writes more: one extra read plus up to three writes per object kind per batch.
  **The effect was not isolated.** The full suite went from 5m43s to 7m10s across this phase,
  but it also gained 131 tests and ran on a machine with other work on it, so the difference
  is not attributable to the extra writes. A measurement of ingestion cost alone is
  outstanding; `tests/db/test_query_cost.py` budgets read paths, and no equivalent exists for
  the write path.
- The as-of repositories are subclasses, so a base method that is not overridden silently
  returns current state.

**Follow-up required**

- ~~Route current-state reads through the open version's presence, so the product's ordinary
  answers stop counting removed grants.~~ **Done** (2026-09-15) —
  `docs/handoffs/p0-current-state-correctness.md`.
- Phase 7B: an HTTP surface for the point-in-time services. None was added here.
- A `ViaParent` selector exists for ACE kinds only; a scope kind added later needs its rule
  added to `CLOSURE_RULES` or it closes nothing (which is the safe default, and silent).

## Compliance

- `tests/db/test_history_versions.py` — a failed, partial, incremental or unreconciled run
  marks nothing absent; a reconciled scope closes exactly what is inside it; one collector
  does not erase another's facts.
- `tests/history/test_closure_rules.py` — the rule table itself: an SMB run cannot close a
  directory, a domain run cannot close a local group.
- `tests/history/test_repository_coverage.py` — every public read of each base repository is
  either overridden as-of or named in an explicit list. A method added to a base repository
  fails the suite.
- `tests/db/test_history_migration.py` — the backfill preserves every MVP row, and the
  migration's digest is the application's digest.
- `tests/db/test_schema.py` — the migration and the declaration cannot drift.
