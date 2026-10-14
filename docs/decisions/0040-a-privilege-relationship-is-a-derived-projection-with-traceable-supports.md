# ADR-0040: A privilege relationship is a derived projection with traceable supports

- **Status:** Accepted
- **Date:** 2026-10-14
- **Phase:** ADG-GRAPH-01A — graph domain model and evidence contract
- **Deciders:** ADG-GRAPH-01A implementation

## Context

ADG is getting a general privilege graph: membership, local-group rights, ownership, ACL rights,
GPO and trust relationships, all as typed edges between ADG objects. Every fact such a graph is
built from is already stored: `membership_edges`, `smb_share_aces`, `ntfs_aces`,
`ntfs_resources.owner_sid`, each with run-level provenance and a timeline in
`object_versions`. Some relationships restate one of those facts. Others follow from a rule
(membership in `S-1-5-32-544` *is* local administration). Others hold only if something ADG
did not evaluate holds, and some exist without ADG being able to say what they connect.

Four existing decisions constrain the shape:

* **ADR-0002.** Membership is stored as edges, never as a closure.
* **ADR-0003.** Raw observations and derived state are separate, and "one table with an
  `is_derived` column" was explicitly rejected.
* **ADR-0007.** Resolution is a join, and no column holds a value derivable from another.
* **ADR-0011.** Answers carry their uncertainty as a closed vocabulary and a direction, never
  as a score.

A naive graph table — `(source, target, type, confidence)` filled by copying facts — would break
all four. It would give a membership edge a second home that drifts from the first. It would
put a computed confidence beside the evidence it was computed from. And it would make "this
relationship holds" a stored flag that goes stale the moment a reconciliation proves a premise
gone.

## Decision

**A privilege relationship is derived state that names canonical objects by their existing
keys, and is supported by stored facts named by theirs.** Nothing collected is copied into the
graph. The graph can be dropped and re-derived without losing an observation.

1. **Three tables.** `graph_relationships` holds identity only: type, source, target and scope.
   `graph_relationship_supports` holds one row per independent derivation of a relationship:
   its derivation class, rule and version, qualifiers, conditions, attributes, collection basis
   and observation window. `graph_relationship_evidence` holds one row per stored fact a support
   rests on, with the runs that first and last observed it.
2. **Identity is a digest of type, endpoints and scope, recomputed by the database.** It
   excludes the derivation class, the rule and every timestamp. Re-observation, re-derivation
   under a new rule version, or a change of class therefore update one relationship rather than
   creating another. A check constraint recomputes the SHA-256, so a row whose id disagrees
   with its components cannot be written.
3. **A relationship is as strong as its strongest current support.** One relationship can be
   supported several independent ways, such as an explicit `WRITE_DAC` entry and the owner's
   implicit grant. Each way is its own support. A relationship's class, confidence and currency
   are folded from its supports on read. Making every piece of evidence a premise of one
   conjunction would declare the relationship gone when either support was removed.
4. **Four derivation classes, with invariants enforced twice.** The four classes are
   `observed`, `deterministically_derived`, `conditionally_derived` and `unresolved`. Each
   invariant is enforced in the domain type and again as a check constraint:
   * `observed` restates exactly one premise;
   * `conditionally_derived` ⇔ at least one named condition;
   * `unresolved` names what is unresolved, and that comes from the evidence itself (a foreign
     or unresolvable member as the collector reported it), not from a missing `principals` row
     at derivation time, which ADR-0007 keeps a join.
5. **Currency is computed and has three values.** A support is `current` while every premise
   is present, and `not_current` once an authoritative reconciliation tombstoned any premise.
   It is `unknown` when a premise has no row and no tombstone. It is the current-state
   presence rule (`app.models.current`) asked of each evidence key. Unknown is never folded
   into `not_current`.
6. **Confidence is computed, ordinal and aligned with the risk engine.** The values are
   `confirmed`, `probable`, `possible` and `indeterminate`: the minimum over what the class and
   the qualifiers allow. It is never a number.
7. **The taxonomy states definition, collection and derivation separately.** Every type
   declares its endpoints, allowed classes, scope rule, materialization and attributes. It
   also declares, as two independent statements, whether ADG collects its premises
   (`collected`, `partially_collected`, `contract_only`, `not_collected`) and whether anything
   derives it (`not_implemented`, `query_time`, `projector`). A type with no storable premise
   cannot be asserted, so a defined-but-uncollected type cannot appear to have been looked for.
8. **Nothing transitive is stored.** `nested_member_of` is defined so a traversal result has a
   name. Its `traversal_only` materialization makes it unassertable in the domain and unstorable
   in the database (ADR-0002).
9. **Evidence must exist when it is recorded.** A support naming a fact the store does not hold
   is refused. Run ids reference `scan_runs`; endpoints and evidence keys have no foreign key,
   for the reason `membership_edges` has none toward `principals`.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| One table of edges with a confidence column, filled by copying facts | A second home for every membership edge and ACE, drifting from the first; a confidence stored beside its inputs; and a stored "holds" that a reconciliation silently falsifies. ADR-0003 and ADR-0007, together. |
| Make the existing `membership_edges` the general graph, adding kinds | `membership_edges` holds raw facts. Adding derived kinds to it is "one table with an `is_derived` column" again, and it has no place for evidence, rules or conditions. |
| One support per relationship (the latest derivation wins) | Two independent supports would overwrite each other's class, and removing the one that wrote last would hide the one still holding. |
| A relationship id that includes the derivation class or rule | Re-deriving under a new rule version, or a relationship becoming resolvable, would create a second relationship for the same fact pair, and history would show an add and a remove that never happened. |
| A numeric confidence | Rejected for answers in ADR-0011; equally underivable here, and it would hide which direction a relationship could be wrong in. |
| Store "unresolved" when no `principals` row exists at derivation | Goes stale when the next AD run describes the SID; resolution stays a join (ADR-0007). |
| Store the human-readable explanation | It would embed names, which are mutable metadata (ADR-0001), so a rename would leave the sentence wrong. It is computed on read with current labels. |
| Backfill relationships in the migration | It would freeze one rule version into the schema history. Derivation is a projector's job (ADG-GRAPH-01B). |
| Introduce graph-node tables | A second identity universe. A node is `(kind, key)` where the key is the existing primary key, or a domain SID, or a reserved, documented key form for the GPO and container kinds nothing collects yet. |

## Consequences

**Positive**

- Every relationship is traceable to the observations it came from. From an evidence row,
  `observations (run_id, subject_key)` and `object_versions (object_kind, object_key)` are each
  one indexed read.
- The graph can be thrown away and re-derived, and a corrected rule simply re-derives in place.
- A reconciled removal ends exactly the supports that rested on it and no others.
- Uncollected relationship types are visible as such and cannot be mistaken for empty results.

**Negative / accepted costs**

- Three tables and a fold on read, where a single edge table would have been simpler to query.
  An adjacency page is one index range scan plus three batched reads.
- Currency is a join per evidence row on every read. It is bounded: one `EXISTS` against a
  primary key and one against the partial open-tombstone index.
- A relationship's derivation class depends on which supports are current, so it can change
  without a write. That is the point, but a consumer caching it must cache it with the
  collection basis.
- Evidence keys are polymorphic and cannot carry a foreign key; `record` checks existence on
  write instead.

**Follow-up required**

- ADG-GRAPH-01B: the projector rules that populate the store (`derivation: projector`), and
  how a re-derivation retires a support whose premises no rule produces any longer.
- Collectors for directory-object ACLs, GPOs and trusts before the `not_collected` types can
  hold anything.
- A public graph API, which this ADR deliberately does not define.

## Compliance

- `backend/tests/domain/test_relationships.py`: identity, host scoping, type validation,
  evidence linkage, the class invariants, confidence and currency folds, unresolved semantics
  and deterministic serialization.
- `backend/tests/db/test_graph_relationships.py`: the database recomputes the identity; the
  record outcomes (created, extended, rederived, backdated, replayed, stale); two supports, one
  reconciled away; missing versus absent; adjacency; every check constraint biting; and the
  migration's literals equal the domain vocabulary.
- `backend/tests/db/test_graph_relationship_migration.py`: revision 0018 over a database
  populated at 0017 changes no existing row, and its downgrade restores the database exactly.
