# ADR-0013: A removal's effect is measured by re-running the access check, never inferred

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 5A (`phase-05/01-access-path-engine.md`)
- **Deciders:** Phase 5A implementation

## Context

Phase 5A must answer "which membership or ACE edges contribute to this access, and what
would removing one do" — and must do it **without ever claiming that removing one edge
revokes access when another path remains**. That prohibition is the phase's own acceptance
criterion, and it is there because the failure is expensive and invisible: an administrator
removes Alice from `Finance-Team`, re-runs the audit, and finds nothing changed because she
was also in `Finance-RW` directly. The remediation was signed off; the access is still there.

The obvious implementation infers the answer from the paths already enumerated: find the
paths that carry a right, see whether they all pass through one edge, and if so report that
edge as sufficient. It is cheap, it reads naturally, and it is wrong in two directions that
no amount of care in the inference rules fixes:

1. **Removing an Allow can uncover an Allow behind it.** The Windows walk accumulates: a
   second Allow naming the same rights contributes nothing *while the first is there*. Delete
   the first and the second takes over exactly. An inference that subtracted the removed
   entry's contribution reports a revocation that does not happen.
2. **Removing a membership can remove a Deny.** A group can put a subject on a Deny ACE as
   easily as on an Allow. Removing that membership **widens** access. An inference built on
   "removal narrows" reports a fix that is an escalation.

Both are ordinary ACL shapes, not pathological ones. And an inference layer is a second model
of the access check: it starts agreeing with `evaluate_acl` and drifts, and the drift shows up
as an explanation that contradicts the answer printed beside it.

Phase 4C established that Windows itself is the oracle for this engine and that a second
hand-written model of the access check mostly measures whether the same person made the same
mistake twice (ADR-0010, and the Phase 4C handoff). The same argument applies one level up.

## Decision

**`RemovalTarget` is computed by re-running the full resolution with the edge removed, and
diffing the resulting effective mask against the original.** No rule infers the effect of a
removal from the shape of the path graph.

Specifically, in `app.access_engine.causality`:

- A **membership** removal recomputes upward reachability from the subject over the traversed
  subgraph with that edge deleted, rebuilds the token from what survives, and re-evaluates.
  Reachability is recomputed rather than the token edited one key at a time, because a group
  cut low on a chain takes everything above it with it.
- An **ACE** removal drops that entry by position, **preserving the order of the rest**, and
  re-evaluates.
- Re-evaluation uses the same `evaluate_acl` and the same layer crossing that produced the
  answer, so a removal is measured by the engine under test rather than by a simpler model of
  it.
- `rights_removed`, `rights_added`, `rights_after` and `revokes_all_access` are all read off
  that re-evaluation. `rights_added` exists precisely because case 2 above is real.
- Only edges lying on an enumerated path are candidates, and the count is bounded by
  `max_removal_targets`; exceeding it is reported as truncation.

Two relationships are **not** removal targets, because they are not edge deletions:
`assumed_membership` (nobody can be removed from `Everyone`) and `ownership` (the remediation
is to change the owner, a different operation with a different blast radius).

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Infer from the path graph: an edge is sufficient if every contributing path crosses it | Wrong for a redundant Allow behind the removed one, and cannot represent a removal that widens access. Both are ordinary ACL shapes |
| Infer, then verify only the edges reported as sufficient | Still wrong in the widening direction, which is the dangerous one, because those edges are never reported as sufficient and so are never verified |
| Re-run the whole `AccessService` query per candidate | Correct but goes back to the database per candidate — the N+1 shape ADR-0012 exists to keep out of this module. The in-process re-evaluation uses rows already read |
| Report contributing edges and refuse to say what removal does | Honest but useless: "which edge do I remove" is the question an audit is run to answer |
| Measure combinations as well as single edges | Set cover over a bounded graph; the cost is combinatorial and the single-edge answer plus an honest "none is sufficient" already prevents the wrong remediation |

## Consequences

**Positive**

- The prohibition is structural rather than a rule somebody has to remember: with an alternate
  path present, the re-evaluation simply returns the same mask, so `rights_removed` is empty
  and `revokes_all_access` is `False`. There is no code path that could overstate it.
- Removals that *widen* access are reported, which no inference-based design would have
  surfaced at all.
- There is one model of the access check in the codebase, so an explanation cannot drift from
  the answer it explains.

**Negative / accepted costs**

- One access check per candidate edge over each ACL. Bounded by `max_removal_targets`
  (default 64, ceiling 256) and measured to add no database statements, but it is real CPU
  that an inference would not have spent.
- Each removal is measured **alone**. "Which two edges together would revoke this" is not
  answered; where no single edge suffices, the engine says so rather than guessing at a pair.
- The analysis covers exactly two operations — deleting a membership edge and deleting an ACE.
  Changing an owner, breaking inheritance, or narrowing an ACE's mask are not modeled.

**Follow-up required**

- Phase 5B (risk) must not read `sufficient_removals` being empty as "nothing can be done".
  It means no *single* edge suffices, which on a real estate is the common case.
- Any future bulk explanation shape must keep this property; the per-candidate cost is what
  makes a naive bulk version expensive, and the fix is a different query shape, not a cheaper
  inference.

## Compliance

`tests/access_engine/test_causality.py::TestRemovalNeverOverstates` is the direct check. Four
of its cases fail under any inference-based implementation:

- `test_one_membership_of_two_removes_nothing` — the alternate-path case;
- `test_removing_an_allow_that_hides_a_redundant_allow_removes_nothing` — failure mode 1;
- `test_removing_a_membership_that_carries_a_deny_widens_access` — failure mode 2, and it
  asserts `rights_added` is non-empty, which an inference cannot produce;
- `test_nothing_is_sufficient_when_two_independent_aces_grant`.

End to end, `tests/db/test_access_paths_api.py::TestTheCanonicalMultiplePathsScenario::test_no_single_removal_revokes_access`
runs the canonical `04-multiple-membership-paths` estate — a Phase 0B scenario whose own
description states the requirement — through ingestion and the endpoint.

`TestExplainingCostsNoExtraQuery` in the same file holds the in-process half: measuring 64
removal targets must issue the same statement count as measuring one, and the same count as
the plain effective-access answer.
