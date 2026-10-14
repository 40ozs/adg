# Privilege relationships: the graph domain model and evidence contract

**Phase:** ADG-GRAPH-01A · **Status:** implemented (domain, schema, internal repository)
**Code:** `backend/app/domain/relationships.py`, `backend/app/models/schema.py` (graph section),
`backend/app/repositories/relationships.py`, `database/migrations/versions/0018_graph_relationships.py`
**Decisions:** [ADR-0040](../decisions/0040-a-privilege-relationship-is-a-derived-projection-with-traceable-supports.md),
building on ADR-0001, 0002, 0003, 0007, 0011 and 0018.

This document defines what a privilege relationship means in ADG and how ADG can prove one. It
does **not** define a traversal over relationships, a projector that derives them, or an API that
serves them. Those are later lanes (ADG-GRAPH-01B onward). The existing membership traversal
(`membership-graph.md`) is unchanged.

---

## 1. A relationship is a conclusion with a paper trail

A relationship says *source* stands in *type* to *target*, optionally inside a *scope*. For
example, `fs01|S-1-5-32-544 write_dacl \\fs01\finance\payroll (host fs01)`.

It is **derived state** (ADR-0003). This holds even when it restates exactly one collected fact,
as `alice member_of Finance-RW` restates one `membership_edges` row. The fact stays where the
collector put it; the relationship *refers* to it. Dropping every relationship loses nothing a
collector saw, and re-deriving rebuilds them.

| Layer | Holds | Example |
| --- | --- | --- |
| Raw facts (unchanged) | what collectors observed | `membership_edges`, `ntfs_aces`, `ntfs_resources.owner_sid` |
| Timeline (unchanged) | when each fact held | `object_versions`, `observations` |
| **Relationship** | identity: type, endpoints, scope | `graph_relationships` |
| **Support** | one independent derivation: class, rule, qualifiers, conditions, window | `graph_relationship_supports` |
| **Evidence** | the stored facts one support rests on | `graph_relationship_evidence` |

---

## 2. Object model: nodes are existing ADG objects

A node is a `NodeRef(kind, key)`. The key is **the primary key the canonical table already
uses**, so there is no second identity universe and no node table.

| Node kind | Key | Canonical source | Collected |
| --- | --- | --- | --- |
| `principal` | SID, or `<host>\|<SID>` for a host-local principal | `principals.principal_key` | yes |
| `server` | case-folded host name | `servers.server_key` | yes |
| `smb_share` | `<server>\|<share>`, case-folded | `smb_shares.share_key` | yes |
| `ntfs_resource` | case-folded UNC path | `ntfs_resources.resource_key` | yes |
| `domain` | domain SID `S-1-5-21-x-y-z` | none; the identity `DomainIdentifier` and `principals.domain_sid` already use | implied by principals |
| `gpo` *(reserved)* | `<domain SID>\|<lower-case GUID>` | none | **no** |
| `directory_container` *(reserved)* | lower-case `objectGUID` (never a DN) | none | **no** |

Keys must be **canonical**, not merely parseable. `s-1-5-…`, `S-1-05-…`, `FS01|S-1-5-32-544`
and `\\FS01\Finance` are all refused. A second spelling of one key would make two nodes of
one object, which is exactly the merge-or-split error `referenced_principal_key` exists to
prevent.

**New canonical object types.** Only two are introduced, and only as *reserved key forms*:
`gpo` and `directory_container`. The taxonomy needs them to say what a GPO relationship would
connect. No table, collector or relationship of theirs exists. `domain` is not new: it
reuses the domain-SID identity the domain layer already has.

Endpoints carry **no foreign key**. An endpoint nobody has described is a real, reportable state,
exactly as `membership_edges` treats an undescribed member. Whether an endpoint is resolved is
a join on read (ADR-0007).

---

## 3. Relationship taxonomy

Every type is **defined**. Whether ADG **collects** its premises and whether anything **derives**
it are separate statements. A defined-but-uncollected type can never be read as looked-for-and-
absent: `RelationshipAssertion` refuses any type with no storable premise.

| Type | Reads as | Source → target | Scope | Classes allowed | Stored | Collection | Premises | Derivation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `member_of` | S is a direct member of T | principal → principal | from_endpoints | observed, unresolved | yes | **collected** | membership_edge | not_implemented |
| `nested_member_of` | S is a member of T through nested groups | principal → principal | from_endpoints | deterministic | **never** | **collected** | membership_edge | query_time |
| `primary_group_of` | T is the primary group of S | principal → principal | from_endpoints | observed, unresolved | yes | **collected** | membership_edge | not_implemented |
| `local_group_member_of` | S is a member of the local group T | principal → principal | host_required | observed, unresolved | yes | **contract_only** | membership_edge | not_implemented |
| `local_admin_of` | S is a local administrator through T | principal → principal | host_required (RID 544) | conditional, deterministic, unresolved | yes | **contract_only** | membership_edge | not_implemented |
| `remote_desktop_user_of` | S may sign in over Remote Desktop through T | principal → principal | host_required (RID 555) | conditional, deterministic, unresolved | yes | **contract_only** | membership_edge | not_implemented |
| `remote_management_user_of` | S may use remote management (WinRM) through T | principal → principal | host_required (RID 580) | conditional, deterministic, unresolved | yes | **contract_only** | membership_edge | not_implemented |
| `owns` | S owns T | principal → ACL targets¹ | from_endpoints | observed, unresolved | yes | **partially_collected** | ntfs_resource | not_implemented |
| `generic_all` | S has full control of T | principal → ACL targets¹ | from_endpoints | conditional, deterministic, unresolved | yes | **partially_collected** | ntfs_ace, smb_ace | not_implemented |
| `generic_write` | S has generic write on T | principal → ACL targets¹ | from_endpoints | conditional, deterministic, unresolved | yes | **partially_collected** | ntfs_ace, smb_ace | not_implemented |
| `write_dacl` | S can change the permissions of T | principal → ACL targets¹ | from_endpoints | conditional, deterministic, unresolved | yes | **partially_collected** | ntfs_ace, ntfs_resource, smb_ace | not_implemented |
| `write_owner` | S can take ownership of T | principal → ACL targets¹ | from_endpoints | conditional, deterministic, unresolved | yes | **partially_collected** | ntfs_ace, smb_ace | not_implemented |
| `can_modify_membership` | S can change the membership of T | principal → principal | from_endpoints | conditional, deterministic, unresolved | yes | **not_collected** | — | not_implemented |
| `controls_gpo` | S can edit the group policy object T | principal → gpo | from_endpoints | conditional, deterministic, unresolved | yes | **not_collected** | — | not_implemented |
| `gpo_linked_to` | S is linked to T | gpo → directory_container/domain | from_endpoints | observed, unresolved | yes | **not_collected** | — | not_implemented |
| `gpo_applies_to` | S applies to T | gpo → principal | from_endpoints | conditional, unresolved | yes | **not_collected** | — | not_implemented |
| `trusts` | S trusts T | domain → domain | from_endpoints | conditional, observed, unresolved | yes | **not_collected** | — | not_implemented |
| `foreign_principal_of` | S is a foreign principal issued by T | principal → domain | from_endpoints | deterministic, unresolved | yes | **collected** | membership_edge, principal | not_implemented |

¹ ACL targets: `principal`, `ntfs_resource`, `smb_share`, `domain`, `gpo`, `directory_container`.

Collection statuses:

* **collected**: a shipped collector reports the premises.
* **partially_collected**: collected for some targets only. NTFS and share ACLs and NTFS owners are
  collected; directory-object, GPO and domain ACLs and owners are **not**.
* **contract_only**: the collector contract carries the premise (`local_group_member` edges)
  and no shipped collector emits it. Only fixtures and the demo estate contain it.
* **not_collected**: nothing in ADG can be a premise yet.

**Direction.** Every relationship is directed, and the type's sentence states what the
direction means. A symmetric fact, such as a two-way trust, is two relationships.

**Local-group specializations** (`local_admin_of`, `remote_desktop_user_of`,
`remote_management_user_of`) are deterministic readings of a `local_group_member` edge into
`S-1-5-32-544/555/580` on one host. They are never "observed", because the edge is the observation and
the group's role is the rule's reading of its RID.

**ACL rights** (`generic_all`, `generic_write`, `write_dacl`, `write_owner`) are never
`observed`. The entry is observed; reading a right out of its mask is interpretation, and
whether the entry is effective (Deny, inheritance) belongs to the access engine (ADR-0010). A
support says what an entry grants, conditionally where something unevaluated could remove it.
`write_dacl` also accepts the NTFS resource as a premise, because an owner is implicitly granted
`WRITE_DAC` unless an `OWNER RIGHTS` entry restricts it.

**`nested_member_of` is never stored.** It is a path, computed by the bounded traversal that
already exists (`app.domain.graph`). Its `traversal_only` materialization makes it
unassertable in the domain and unstorable in the database (ADR-0002).

---

## 4. Schema (revision 0018)

### `graph_relationships`: identity only

| Column | Notes |
| --- | --- |
| `relationship_id` PK | SHA-256 of `type, source_kind, source_key, target_kind, target_key, scope_kind, scope_key` joined by `chr(31)`; **recomputed by `ck_graph_relationships_id_is_digest`** |
| `relationship_type` | storable types only (`ck_…_type_is_storable`) |
| `source_kind/key`, `target_kind/key` | node references; no FK |
| `scope_kind/key` | `host` + case-folded host, or both NULL |
| `created_at`, `updated_at` | |

Constraints: membership and trust types cannot be reflexive. A host-local principal endpoint
must sit in its host's scope; the check uses `coalesce(…, false)`, because a CHECK passes on
NULL. Indexes: `(source_kind, source_key, relationship_type, relationship_id)` and the target
mirror for adjacency in both directions with keyset paging; a partial scope index; a type index.

### `graph_relationship_supports`: one row per independent derivation

PK `(relationship_id, support_key)`. `support_key` is the SHA-256 of the sorted premise set.
Columns: `derivation_class`, `rule_id`, `rule_version`, `qualifiers text[]`,
`conditions text[]`, `attributes jsonb` (an object), `basis_token` (the collection basis the
derivation read), `derived_at`, and the observation window `first/last_observed_at` with
`first/last_observed_run_id → scan_runs`.

Checks: the arrays are subsets of the closed vocabularies; conditional ⇔ conditions present;
unresolved ⇒ an unresolved qualifier; rule-id shape; version ≥ 1; basis-token shape; window
ordered.

### `graph_relationship_evidence`: the facts a support rests on

`(relationship_id, support_key) → supports` (cascade); `evidence_kind` (the contract's
`ObservationKind`) and `evidence_key` (that table's primary key, which is also
`object_versions.object_key` and `observations.subject_key`); `role` (`premise` or
`context`); and its own first/last observation and runs. Unique per
`(relationship, support, kind, key)`. Reverse index `(evidence_kind, evidence_key,
relationship_id)` answers "what rests on this fact".

**Not materialized:** transitive closure, all-pairs reachability, node rows, resolution, names,
confidence, currency and explanations.

---

## 5. Provenance model

* **To the observation.** `(evidence_kind, evidence_key, first_observed_run_id)` reaches the
  `observations` row of the run that supplied it, and `(kind, key)` reaches the fact's
  `object_versions` timeline. Each is one indexed read.
* **To the derivation.** `rule_id@rule_version` names the rule; `basis_token` names the
  collection state it read (`app.domain.basis`), so staleness is a comparison, not a clock.
* **Observation window.** A support is *established* by its newest premise. `first/last_observed`
  are the earliest and latest such instants across readings. They are observation instants, not
  derivation instants (`derived_at` is that). A relationship's window spans its supports.
* **Existence on write.** `record` refuses evidence naming a fact the store does not hold, so
  no relationship is born untraceable.

### Recording semantics

| Reading vs. stored support | Outcome | Effect |
| --- | --- | --- |
| new premise set | `created` | support and evidence inserted |
| newer than last confirmation | `extended` | window extended; the newer derivation applied |
| same instant, different derivation | `rederived` | e.g. a new rule version; later arrival kept |
| older than first observation | `backdated` | window start moved; derivation **not** applied |
| identical | `replayed` | nothing written |
| older, disagreeing | `stale` | not applied, and reported |

Each evidence link widens its own window on every reading, whatever the support's outcome.

---

## 6. Completeness model

**Derivation class** (strongest first):

| Class | Meaning | Invariant |
| --- | --- | --- |
| `observed` | one collected fact, restated | exactly one premise |
| `deterministically_derived` | follows by a rule with no assumption | — |
| `conditionally_derived` | holds only if named conditions hold | ≥ 1 condition, and only here |
| `unresolved` | exists; what it connects cannot be established | names `foreign_principal` or `endpoint_unresolved_at_collection` |

`unresolved` is what the **evidence** said: a collector reported a foreign or unresolvable
member. It is not the absence of a `principals` row at derivation time, which would go stale and
which ADR-0007 keeps a join.

**Qualifiers** (`foreign_principal`, `endpoint_unresolved_at_collection`,
`evidence_from_partial_run`, `evidence_backfilled`, `derivation_truncated`) record what a
derivation knew about its inputs.

**Conditions** (`no_deny_evaluated`, `inheritance_unresolved`, `token_filtering_not_modeled`,
`object_type_scope_unevaluated`, the four `gpo_*`, and the two `trust_*`) record what a conditional support
depends on. Every qualifier and condition has an operator-facing sentence, enforced by test.

**Confidence** is computed, never stored, and is ordinal, never a number. It is the minimum of
what the class allows (`observed`/deterministic → `confirmed`, conditional → `probable`,
unresolved → `indeterminate`) and what the qualifiers allow (unresolved qualifiers →
`indeterminate`, any other → `possible`). The first three values carry the risk engine's
`Confidence` names and meanings.

**Currency** is computed on every read from the current-state presence rule
(`app.models.current`):

| Premise state | Meaning |
| --- | --- |
| `present` | the row exists and no open tombstone excludes it |
| `absent` | an authoritative reconciliation tombstoned it |
| `missing` | no row and no tombstone: nobody said it is gone |

A support is `current` when every premise is present, `not_current` when any premise is
absent, and `unknown` otherwise. A relationship is `current` when any support is,
`not_current` only when **every** support is, and `unknown` otherwise. **Unknown is never
folded into not_current.** Context evidence never affects currency.

**The fold.** A relationship reports its *best support*, ranked by: current before unknown before
not_current; then confidence; then class; then most recently confirmed; then support key. It is
as strong as its strongest current support. One removed ACE does not end a `write_dacl` that
the owner's implicit grant still supports.

**Explanation** is computed on read from the type's sentence, labels resolved at read time
(falling back to keys), the best support's class, conditions and qualifiers, the number of
supports, and currency. It is never stored, because names are mutable (ADR-0001).

**Serialization** (`StoredRelationship.as_dict` / `to_json`) is deterministic: supports sorted
by key, evidence by `(kind, key)`, sets sorted, timestamps in UTC ISO-8601, and canonical JSON.

---

## 7. Internal repository (`RelationshipRepository`)

| Method | Purpose |
| --- | --- |
| `record(assertion, now=)` | relationship + support + evidence, atomically in the caller's transaction |
| `get(id)` / `get_many(ids)` | relationships with every support and evidence link, and evidence state; 3 queries |
| `adjacent(node, outbound\|inbound, types=, after=, limit=)` | keyset page ordered by `(type, id)` over the adjacency index |
| `supported_by(kind, key)` | relationship ids resting on one fact |

There is no HTTP surface. The final public graph API is deliberately not built in this lane.

---

## 8. Explicitly deferred

* **Derivation.** No projector rule exists; every stored type is `derivation: not_implemented`
  (ADG-GRAPH-01B). Retiring a support when no rule produces it any longer is part of that work.
* **Collection** of directory-object ACLs and owners, GPOs (`gPLink`, GPC/SYSVOL ACLs, security
  and WMI filtering), trusts (`trustedDomain`) and OU placement. Until then, `can_modify_membership`,
  `controls_gpo`, `gpo_linked_to`, `gpo_applies_to` and `trusts` cannot hold a relationship.
* **A local-groups collector.** Local-group types are `contract_only`.
* **Graph traversal over relationships**, path search and a public graph API with an accessible
  structured representation.
* **History of relationships.** Relationships are re-derivable from the versioned facts; a
  relationship timeline is not stored.
