# ADR-0041: A relationship is re-derived when its facts change, and retired when they no longer establish it

- **Status:** Accepted
- **Date:** 2026-10-14
- **Phase:** ADG-GRAPH-01B — relationship collection and derivation
- **Deciders:** ADG-GRAPH-01B implementation

## Context

ADR-0040 made a privilege relationship a derived projection: identity in
`graph_relationships`, one row per independent derivation in `graph_relationship_supports`,
and the stored facts each rests on in `graph_relationship_evidence`. A support's currency is
read from its premises' presence: current while every premise is stored and not tombstoned,
not current once a reconciliation tombstones one, unknown when a premise has no row at all.

That rule is exact for facts that disappear. It is blind to facts that are still stored and
now say something else. An NTFS descriptor's owner changes in place: the resource row is still
present, so "Alice owns `\\fs01\finance\payroll`" would read as current indefinitely. A Deny for
the trustee appears ahead of an Allow, so the Allow still exists and no longer grants the right.
ADR-0040 left one question open for this lane: how a re-derivation retires a support that no
rule produces any longer.

Four further questions needed an answer before anything could derive a relationship:

* when the projector runs, relative to ingestion and reconciliation;
* how much it re-derives per run, given that a full directory re-scan re-observes every edge;
* what a trustee-level ACL relationship means, when Deny entries depend on the accessing token;
* whether share ACEs establish control of a share.

## Decision

1. **Derivation runs at run completion, after reconciliation, in a savepoint.**
   `IngestionService.complete_run` calls `app.relationships.project_completed_run` after the
   closure pass and before its commit. The graph therefore converges exactly when the run's
   facts and their presence become final, and a completed run never leaves its relationships
   behind. If the pass fails, the savepoint rolls back everything it wrote, a failed pass is
   recorded in its own savepoint, and the completion commits regardless: a projector defect
   never costs the estate an observation (ADR-0003). A replayed completion retries a run that
   has no successful pass. Passes are serialized by a transaction-scoped advisory lock.

2. **A pass re-derives what the run changed and confirms what it re-observed.** The units
   re-derived in full are those with a fact the run opened a version for: new, changed, revived
   or tombstoned (the history model's own notion of a change). A *unit* is the bounded set of
   facts one rule reads: a membership edge, a principal, or an NTFS resource together with
   **all** of its current entries. Facts the run re-observed unchanged change no derivation.
   For those, one set-based statement per evidence kind widens their evidence windows, and one
   more widens the windows of supports whose premises the run saw again. That is the window
   `record` would have written.

3. **A support whose facts are all present and no longer establish it is retired.** After a
   unit is re-derived, every support already stored for it that the rules did not produce, and
   whose premises are **all present**, gets `retired_at` and `retired_basis_token` (revision
   0019). A retired support is not current whatever its premises say. A support with an absent
   premise is left alone, because the tombstone already makes it not current. So is one with a
   missing premise, because retiring it would turn "nobody said it is gone" into "it is gone".
   A later derivation that produces a retired support again **reinstates** it. Retired
   supports are kept with their evidence, so the record of what used to hold survives.

4. **A trustee-level ACL relationship means "every token holding the trustee is granted this,
   or would be but for something named".** A support exists for an Allow entry only if
   `app.access_engine.evaluate_acl` itself shows the entry delivering **every** bit the type
   requires (after generic mapping) to a token holding just the trustee and `Everyone`. The
   walk runs over the descriptor with the other Allows removed and every Deny kept. A Deny for
   the trustee, for `Everyone`, or for `OWNER RIGHTS` when the trustee owns the object applies
   to every such token: ahead of the grant it removes the support. A Deny for anybody else
   depends on the accessing token, so it makes the support conditional (`deny_may_apply`) and
   is recorded as context evidence. An incomplete descriptor (`acl_entries_incomplete`) and
   an unknown entry order (`acl_order_unknown`) are conditions too, and are never resolved by
   convention. Group-mediated access is never a stored relationship; it is a path
   (`member_of` followed by the right) for a traversal (ADR-0002).

5. **Share ACEs establish no control relationship.** A share permission limits what a
   session reaching the files through the share may do. It is not control of the share
   object, and a share entry granting `WRITE_DAC` does not let its holder change the share's
   permissions. `smb_ace` is removed from the premise kinds of `generic_all`, `generic_write`,
   `write_dacl` and `write_owner`. Effective access over SMB remains the access engine's
   intersection of the two layers (ADR-0010).

6. **Every pass is recorded.** `graph_derivation_passes` holds one row per pass: what started
   it, the collection basis it read, the rules it ran, and what it created, extended,
   re-derived, reinstated and retired. A failed pass is recorded with its error.
   `RelationshipProjector.state()` lists every completed run no successful pass has covered,
   so "is the graph derived from everything collected" is a question about rows.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Delete a support when no rule produces it any longer | Loses the explanation of what used to hold, and makes "it changed" indistinguishable from "it was never there". |
| Store a digest of the fact's content on each evidence link and compare on read | Catches an owner changed in place, but not a support invalidated by a *different* fact in the same unit (a new Deny), so derivation would still be required. It would also put a second copy of fact content in derived state. |
| Derive on every batch | A later reconciliation of the same run changes presence. Currency would read the change correctly, but the run's final status is unknown at batch time. It would also re-derive a unit once per batch that touches it, and fail a batch, and so block ingestion, on a projector defect. |
| Derive after the completion commits, in its own transaction | There would be a window, and on a crash a permanent gap, in which a committed run's facts have no relationships. In-transaction with a savepoint has neither, and still cannot fail the completion. |
| Re-derive every fact a run observed | A re-scan of an unchanged 200,000-edge directory would rebuild every support. Measured at about 1.3 ms per unit, that exceeds the collector's 100-second completion timeout. The window confirmation gives the same windows in a handful of statements. |
| Treat any Allow bit as the right | Ignores Deny precedence, `INHERIT_ONLY` and placeholder trustees. That is the access-engine logic ADR-0010 says no other module may reimplement, and this is the shortcut it exists to prevent. |
| Treat every Deny as a condition | Overstates a relationship that a Deny for the trustee itself provably removes for every holder. |
| Materialize group-mediated relationships (user to resource) | It is the closure ADR-0002 rejected, and it multiplies the graph by group sizes. |
| Read share ACEs as control of the share | Misstates what a share permission is. A later lane would find "control" edges to shares that confer none. |

## Consequences

**Positive**

- The graph converges with every completed run and never outlives a fact change it rests on.
- Relationships carry what the facts establish: deterministic where nothing was assumed,
  conditional with the named reason otherwise, and unresolved where the collector said so.
- Incremental derivation and a clean rebuild give the same current graph. The tests show it
  for the scenarios and for the three-generation Null Meridian history.
- A re-scan of an unchanged estate costs a handful of statements, not a re-derivation.

**Negative / accepted costs**

- A completion now does derivation work inside its transaction. On Null Meridian that is
  15.2 s over 300 passes, with the largest pass at 3.1 s.
- A rule revision takes effect on facts as they change, and everywhere only after
  `python -m app.operations rebuild-graph`. An installation upgrading to 0019 must run a rebuild
  once, because facts collected before the projector existed have never been derived.
  `state()` lists them until then.
- The basis token is part of a support's derivation, so a rebuild re-derives (in place) every
  support last derived under an older collection basis.
- A run that never completes is never derived. Its facts are visible in `state()` as a run in
  progress, and a rebuild covers them.

**Follow-up required**

- GRAPH-01C: traverse `member_of`, `primary_group_of` and `local_group_member_of` as the
  membership hops (together, exactly the edges `app.domain.graph` walks), compose path
  conditions, and treat a retired or tombstoned support as not current.
- Collectors for directory-object security descriptors, GPOs and trusts, before
  `can_modify_membership`, `controls_gpo`, `gpo_linked_to`, `gpo_applies_to` and `trusts` can
  hold anything.

## Compliance

- `backend/tests/relationships/test_rules.py`: every rule over in-memory facts, including Deny
  precedence, `INHERIT_ONLY`, `CREATOR OWNER`, `OWNER RIGHTS`, incomplete and unordered
  descriptors, a NULL DACL, host scoping and determinism.
- `backend/tests/db/test_graph_derivation.py`: derivation through the real ingestion API;
  removal, reappearance, an owner changed in place, a Deny that appears and goes, unknown never
  turned into absent, idempotency, rebuild equivalence, provenance, failure isolation and
  statement counts.
- `backend/tests/db/test_graph_derivation_migration.py`: revision 0019 over a populated 0018
  graph, and its downgrade.
- `backend/tests/db/test_null_meridian_demo.py::TestThePrivilegeGraph`: every demo run derived,
  no pass failed, and a clean rebuild equals the incremental graph.
