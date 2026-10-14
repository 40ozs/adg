# Privilege relationships: the domain model, the evidence contract, their derivation and traversal

**Phases:** ADG-GRAPH-01A (model), ADG-GRAPH-01B (derivation), ADG-GRAPH-01C (traversal and
graph API) · **Status:** implemented (domain, schema, internal repository, projector,
local-groups collector, path engine, `/api/v1/graph`)
**Code:** `backend/app/domain/relationships.py`, `backend/app/models/schema.py` (graph section),
`backend/app/repositories/relationships.py`, `backend/app/relationships/` (rules and projector),
`backend/app/traversal/` (semantics, search, completeness, service),
`backend/app/api/relationship_graph.py`,
`database/migrations/versions/0018_graph_relationships.py` and `0019_graph_derivation.py`,
`collector/powershell/localgroups/`
**Decisions:** [ADR-0040](../decisions/0040-a-privilege-relationship-is-a-derived-projection-with-traceable-supports.md),
[ADR-0041](../decisions/0041-a-relationship-is-rederived-when-its-facts-change.md),
[ADR-0042](../decisions/0042-a-path-is-only-as-complete-as-the-graph-it-walked.md),
building on ADR-0001, 0002, 0003, 0006, 0007, 0010, 0011, 0016 and 0018.

This document defines what a privilege relationship means in ADG, how ADG can prove one,
(§9) which relationships the projector derives from the facts ADG collects and how they stay
current, and (§10) how paths through them are searched and served. The existing membership
traversal (`membership-graph.md`) and its routes are unchanged.

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
| `member_of` | S is a direct member of T | principal → principal | from_endpoints | observed, unresolved | yes | **collected** | membership_edge | projector |
| `nested_member_of` | S is a member of T through nested groups | principal → principal | from_endpoints | deterministic | **never** | **collected** | membership_edge | query_time |
| `primary_group_of` | T is the primary group of S | principal → principal | from_endpoints | observed, unresolved | yes | **collected** | membership_edge | projector |
| `local_group_member_of` | S is a member of the local group T | principal → principal | host_required | observed, unresolved | yes | **collected**² | membership_edge | projector |
| `local_admin_of` | S is a local administrator through T | principal → principal | host_required (RID 544) | conditional, deterministic, unresolved | yes | **collected**² | membership_edge | projector |
| `remote_desktop_user_of` | S may sign in over Remote Desktop through T | principal → principal | host_required (RID 555) | conditional, deterministic, unresolved | yes | **collected**² | membership_edge | projector |
| `remote_management_user_of` | S may use remote management (WinRM) through T | principal → principal | host_required (RID 580) | conditional, deterministic, unresolved | yes | **collected**² | membership_edge | projector |
| `owns` | S owns T | principal → ACL targets¹ | from_endpoints | observed, unresolved | yes | **partially_collected** | ntfs_resource | projector |
| `generic_all` | S has full control of T | principal → ACL targets¹ | from_endpoints | conditional, deterministic, unresolved | yes | **partially_collected** | ntfs_ace, ntfs_resource | projector |
| `generic_write` | S has generic write on T | principal → ACL targets¹ | from_endpoints | conditional, deterministic, unresolved | yes | **partially_collected** | ntfs_ace, ntfs_resource | projector |
| `write_dacl` | S can change the permissions of T | principal → ACL targets¹ | from_endpoints | conditional, deterministic, unresolved | yes | **partially_collected** | ntfs_ace, ntfs_resource | projector |
| `write_owner` | S can take ownership of T | principal → ACL targets¹ | from_endpoints | conditional, deterministic, unresolved | yes | **partially_collected** | ntfs_ace, ntfs_resource | projector |
| `can_modify_membership` | S can change the membership of T | principal → principal | from_endpoints | conditional, deterministic, unresolved | yes | **not_collected** | — | not_implemented |
| `controls_gpo` | S can edit the group policy object T | principal → gpo | from_endpoints | conditional, deterministic, unresolved | yes | **not_collected** | — | not_implemented |
| `gpo_linked_to` | S is linked to T | gpo → directory_container/domain | from_endpoints | observed, unresolved | yes | **not_collected** | — | not_implemented |
| `gpo_applies_to` | S applies to T | gpo → principal | from_endpoints | conditional, unresolved | yes | **not_collected** | — | not_implemented |
| `trusts` | S trusts T | domain → domain | from_endpoints | conditional, observed, unresolved | yes | **not_collected** | — | not_implemented |
| `foreign_principal_of` | S is a foreign principal issued by T | principal → domain | from_endpoints | deterministic, unresolved | yes | **collected** | membership_edge, principal | projector |

¹ ACL targets: `principal`, `ntfs_resource`, `smb_share`, `domain`, `gpo`, `directory_container`.
Only `ntfs_resource` is ever derived today (§9.4); the others are defined for collectors that do
not exist.

² Since ADG-GRAPH-01B, by the local-groups collector (`collector/powershell/localgroups`). Its
Windows binding is validated on a standalone CI runner. A remote, domain-joined server is
**not yet validated** (§9.10).

Collection statuses:

* **collected**: a shipped collector reports the premises.
* **partially_collected**: collected for some targets only. NTFS entries and descriptors
  (including owners) are collected; directory-object, GPO and domain ACLs and owners are **not**.
  Share ACEs are collected but are not a premise of any control type (§9.4).
* **contract_only**: the collector contract carries the premise and no shipped collector emits
  it. No type is in this state since ADG-GRAPH-01B.
* **not_collected**: nothing in ADG can be a premise yet.

**Direction.** Every relationship is directed, and the type's sentence states what the
direction means. A symmetric fact, such as a two-way trust, is two relationships.

**Local-group specializations** (`local_admin_of`, `remote_desktop_user_of`,
`remote_management_user_of`) are readings of a `local_group_member` edge into
`S-1-5-32-544/555/580` on one host. They are never "observed", because the edge is the observation and
the group's role is the rule's reading of its RID. Local administration is that membership
(deterministic); Remote Desktop and WinRM need more than membership and are always conditional
(§9.3).

**ACL rights** (`generic_all`, `generic_write`, `write_dacl`, `write_owner`) are never
`observed`. The entry is observed; reading a right out of its mask is interpretation, and
whether the entry is effective (Deny, order) is the access engine's walk (ADR-0010), which the
projector runs (§9.4). A support says what the descriptor grants every holder of the trustee,
conditionally where something unevaluated could remove it. Every ACL type also accepts the NTFS
resource as a premise: an owner is implicitly granted `WRITE_DAC` unless an `OWNER RIGHTS` entry
replaces it, `OWNER RIGHTS` grants whoever the descriptor names as owner, and a descriptor with
no DACL grants everyone everything.

**`nested_member_of` is never stored.** It is a path, computed by the bounded traversal that
already exists (`app.domain.graph`). Its `traversal_only` materialization makes it
unassertable in the domain and unstorable in the database (ADR-0002).

---

## 4. Schema (revisions 0018 and 0019)

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

**Revision 0019 (ADG-GRAPH-01B)** adds `retired_at` and `retired_basis_token`: set together
(`ck_…_retirement_is_whole`) when a derivation read the support's facts, all still stored, and
no longer produced it (§9.6). It also widens the conditions check to the sixteen-value vocabulary.

### `graph_relationship_evidence`: the facts a support rests on

`(relationship_id, support_key) → supports` (cascade); `evidence_kind` (the contract's
`ObservationKind`) and `evidence_key` (that table's primary key, which is also
`object_versions.object_key` and `observations.subject_key`); `role` (`premise` or
`context`); and its own first/last observation and runs. Unique per
`(relationship, support, kind, key)`. Reverse index `(evidence_kind, evidence_key,
relationship_id)` answers "what rests on this fact".

### `graph_derivation_passes`: one row per projector pass (revision 0019)

`pass_id` PK; `trigger` (`run_completion` or `rebuild`); `source_run_id → scan_runs` (ON
DELETE SET NULL); `status` (`succeeded` or `failed`, failed ⇔ `error` present); `basis_token`;
`rules` (every `rule_id@version` the pass ran); counts of units examined and skipped,
relationships created, and supports created, extended, re-derived, backdated, replayed, stale,
reinstated and retired; `started_at`, `completed_at`. A failed pass wrote nothing and is
recorded anyway.

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
| retired, and a reading no older than its last | `reinstated` | retirement cleared; any newer window or derivation applied |

Each evidence link widens its own window on every reading, whatever the support's outcome. When
a newer derivation is applied, its **context** evidence replaces the old one's (a Deny that no
longer comes first stops being named); premises never change, because they are the support.

Recording is set-based (`record_many`): one insert per table, one locking read and one batch of
updates per 500 supports, in `(relationship_id, support_key)` order, so concurrent writers lock
in one order. Within one call a support derived twice identically collapses, and a support
derived two different ways is refused as a projector defect.

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
`object_type_scope_unevaluated`, the four `gpo_*`, the two `trust_*`, and since ADG-GRAPH-01B
`deny_may_apply`, `acl_entries_incomplete`, `acl_order_unknown`, `logon_right_unevaluated`,
`service_access_policy_unevaluated` and `remote_service_state_unknown`) record what a conditional
support depends on. Every qualifier and condition has an operator-facing sentence, enforced by
test.

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

A support is `current` when every premise is present and it is not retired, `not_current` when
any premise is absent **or** it is retired (§9.6), and `unknown` otherwise. A relationship is `current` when any support is,
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
| `record(assertion, now=)` | relationship + support + evidence, atomically in the caller's transaction (`record_many` of one) |
| `record_many(assertions, now=)` | the same for any number, set-based, in a bounded number of statements per chunk |
| `retire(supports, now=, basis_token=)` | mark supports retired; already-retired ones keep their first retirement |
| `extend_observed(run_id, now=)` | widen the windows a run's re-observation of unchanged facts extends, in SQL (§9.7) |
| `supports_resting_on(kind, keys)` / `supports_targeting(kind, keys, types)` | the supports a derivation unit owns |
| `get(id)` / `get_many(ids)` | relationships with every support and evidence link, and evidence state; 3 queries |
| `adjacent(node, outbound\|inbound, types=, after=, limit=)` | keyset page ordered by `(type, id)` over the adjacency index |
| `supported_by(kind, key)` | relationship ids resting on one fact |
| `adjacent_many(nodes, direction, types=, limit=)` | one breadth-first level: every relationship touching any of the nodes, in `(kind, key, type, id)` order, plus `get_many` (ADG-GRAPH-01C) |
| `nodes_named(nodes)` | which nodes some relationship names (ADG-GRAPH-01C) |

The HTTP surface over this repository is §10.

---

## 8. Explicitly deferred

* **Collection** of directory-object security descriptors and owners, GPOs (`gPLink`, GPC/SYSVOL
  ACLs, security and WMI filtering), trusts (`trustedDomain`) and OU placement. Until then,
  `can_modify_membership`, `controls_gpo`, `gpo_linked_to`, `gpo_applies_to` and `trusts` cannot
  hold a relationship. §9.9 states exactly what each would need.
* **Rendering paths** in the UI (ADG-GRAPH-01E). Traversal, path search and the graph API
  were built in ADG-GRAPH-01C (§10).
* **History of relationships.** Relationships are re-derivable from the versioned facts; a
  relationship timeline is not stored. A retired support records when it stopped holding.

---

## 9. Derivation (ADG-GRAPH-01B)

`app.relationships.rules` holds the rules: pure functions from stored facts to assertions,
testable over facts in memory. `app.relationships.projector` decides which facts to read, reads
them from current state, records what the rules produced and retires what they no longer
produce. Nothing in the frontend or in a route handler derives a relationship.

### 9.1 Units and rules

A rule reads one **unit** and nothing else:

| Unit | Facts read | Types it can produce |
| --- | --- | --- |
| membership edge | the `membership_edges` row | `member_of`, `primary_group_of`, `local_group_member_of`, `local_admin_of`, `remote_desktop_user_of`, `remote_management_user_of`, `foreign_principal_of` |
| principal | the `principals` row | `foreign_principal_of` |
| NTFS resource | the `ntfs_resources` row **and all of its current `ntfs_aces`** | `owns`, `generic_all`, `generic_write`, `write_dacl`, `write_owner` |

A resource is one unit because whether one entry grants a right depends on the Deny entries
around it.

| Rule (`rule_id@1`) | Produces | From |
| --- | --- | --- |
| `membership.directory_edge` | `member_of` | a `directory_group_member` edge, or a source-stated `well_known_implicit` one |
| `membership.primary_group_edge` | `primary_group_of` | a `primary_group` edge |
| `membership.local_group_edge` | `local_group_member_of` | a `local_group_member` edge, in its host's scope |
| `local_group.builtin_administrators` | `local_admin_of` | that edge into `<host>\|S-1-5-32-544` |
| `local_group.builtin_remote_desktop_users` | `remote_desktop_user_of` | that edge into `<host>\|S-1-5-32-555` |
| `local_group.builtin_remote_management_users` | `remote_management_user_of` | that edge into `<host>\|S-1-5-32-580` |
| `foreign_principal.membership_edge` | `foreign_principal_of` | an edge the collector flagged foreign |
| `foreign_principal.principal_object` | `foreign_principal_of` | a `foreign_security_principal` object |
| `ntfs.owner` | `owns` | the descriptor's owner SID |
| `ntfs.owner_implicit_rights` | `write_dacl` | the owner's implicit `WRITE_DAC` |
| `ntfs.allow_entry` | the four control types | one Allow entry |
| `ntfs.allow_entries_combined` | the four control types | several Allow entries for one trustee that only together deliver the bits |
| `ntfs.owner_rights_entry` | the four control types | an `OWNER RIGHTS` Allow entry, for the current owner |
| `ntfs.null_dacl` | the four control types | a descriptor with no DACL, stated as `Everyone` |

The id names the mechanism; the relationship type is already part of the identity. A rule's
implementation may be refactored freely. Its version changes only when what it concludes from
the same facts changes, and the relationship's identity never does.

### 9.2 Membership

One edge is one direct hop. Together, `member_of`, `primary_group_of` and
`local_group_member_of` are **exactly the edge set `app.domain.graph` traverses**, so a path over
relationships and the membership API cannot disagree about who is in a group. Nested membership
is never stored. It is a path, and the closure is what ADR-0002 rejected. A cycle is therefore
just edges, and derivation is per edge, so it cannot recurse.

A member the collector reported as a foreign security principal, or as unresolvable
(`member_kind: unresolved`), makes the membership `unresolved`, qualified
`foreign_principal` or `endpoint_unresolved_at_collection`. That is the edge's own statement,
never the absence of a `principals` row at derivation time, because resolution stays a join
(ADR-0007). A foreign member's SID prefix also yields `foreign_principal_of` its domain,
deterministically. A foreign security principal object yields a second support of the same
relationship. A well-known SID in `ForeignSecurityPrincipals` (such as Authenticated Users) has
no issuing domain and gets none.

The directory's own `BUILTIN\Administrators` (a `directory_group_member` edge into a bare
`S-1-5-32-544`) is a membership and nothing more. It administers domain controllers, which ADG
does not enumerate as hosts.

### 9.3 Host-local relationships

Every local type is host-scoped from the group's key (`<host>|<SID>`). A local edge whose group
carries no host is **refused and counted as a skip, never stored unscoped**. The same group SID
on two hosts is two groups and two relationships.

| Type | Class | Why |
| --- | --- | --- |
| `local_group_member_of` | observed | the edge, restated |
| `local_admin_of` | deterministic | membership in BUILTIN\Administrators *is* local administration |
| `remote_desktop_user_of` | **always conditional**: `logon_right_unevaluated`, `remote_service_state_unknown` | RDP sign-in also needs `SeRemoteInteractiveLogonRight` (granted to the group by default, removable by GPO) and the service enabled; neither is collected |
| `remote_management_user_of` | **always conditional**: `service_access_policy_unevaluated`, `remote_service_state_unknown` | WinRM also needs its listener and its own security descriptor (which admits the group by default); neither is collected |

An unresolved member makes `local_admin_of` unresolved. Remote Desktop and WinRM stay
conditional and carry the unresolved qualifier, so neither the conditions nor the doubt is lost.
Remote *use* of local administration is subject to UAC remote token filtering for local
accounts. That is a property of a path that uses it for remote execution, so GRAPH-01C applies
`token_filtering_not_modeled` there; it is not a property of the membership.

### 9.4 NTFS descriptors

**Ownership.** `owns` restates the descriptor's owner SID (observed; a BUILTIN owner is that
host's group, the access service's rule through the same function). The owner also gets
`write_dacl` by `ntfs.owner_implicit_rights`. Windows grants it before reading any entry, so no
Deny removes it. It is deterministic unless the descriptor declared more entries than ADG holds,
because an unseen `OWNER RIGHTS` entry could replace it (`acl_entries_incomplete`). When an
`OWNER RIGHTS` entry is present, the implicit grant is replaced, not supplemented. The owner then
holds exactly what those entries say (`ntfs.owner_rights_entry`, premises: the entry **and** the
descriptor, so an owner change ends the support). Ownership supports `write_dacl` and nothing
else. Taking ownership needs `WRITE_OWNER`, and the owner's other rights are the DACL's.

**Entries.** A support exists only when the access engine's own walk
(`app.access_engine.evaluate_acl`) shows the entry delivering **every** bit the type requires,
after generic mapping, to a token holding just the trustee and `Everyone`. The walk runs over
the descriptor with every other Allow removed and every Deny kept:

| Type | Required bits |
| --- | --- |
| `generic_all` | `FILE_ALL_ACCESS` (0x1F01FF) |
| `generic_write` | `FILE_GENERIC_WRITE` (0x120116) |
| `write_dacl` | `WRITE_DAC` |
| `write_owner` | `WRITE_OWNER` |

A full-control entry therefore supports all four; Modify supports `generic_write` only; Read &
Execute supports none. The walk is what makes this an access check and not a bit test.
`INHERIT_ONLY` entries grant nothing on the object; `CREATOR OWNER`/`CREATOR GROUP` never match;
generic bits are mapped; `OWNER RIGHTS` resolves to the owner; entries count in descriptor order.

Deny entries are classified, never ignored and never all treated alike:

| Deny entry ahead of the grant, naming… | Effect |
| --- | --- |
| the trustee itself, `Everyone`, or `OWNER RIGHTS` when the trustee is the owner | applies to every holder: the bits are gone, so **no support** |
| anybody else (another group, `Authenticated Users`, a logon-session SID, `OWNER RIGHTS` for a non-owner) | depends on the holder's token: **conditional**, `deny_may_apply`, the Deny recorded as context evidence |
| any trustee, when the descriptor's order is unknown | **conditional**, `acl_order_unknown`; the order is never assumed |

A Deny behind the grant takes nothing away, exactly as Windows evaluates it.

Further qualifications:

* a descriptor whose stored entries disagree with the count it declared makes every grant
  conditional (`acl_entries_incomplete`);
* an orphaned ACL (entries with no descriptor row) is read, with every grant conditional and no
  ownership;
* a descriptor with no DACL grants every token everything, stated as `Everyone` for all four
  types (`ntfs.null_dacl`), and its stale entries are ignored;
* several entries for one trustee that only together deliver the bits make one joint support
  with all of them as premises, so removing any one ends it.

Each grant carries the entry's `access_mask`, `ace_type` and `inherited`, plus the descriptor as
context evidence.

Every relationship here is **trustee-level**: "the descriptor grants every token holding *T*".
Group-mediated access (Alice through Finance-RW) is never stored. It is the path
`member_of`…`write_dacl` for a traversal to compose.

**Share entries are not read into these types.** A share permission limits what a session
reaching the files through that share may do. It is not control of the share object, and a share
entry granting `WRITE_DAC` does not let its holder change the share's permissions. `smb_ace` is
therefore not a premise of any control type, and effective access over SMB stays the access
engine's intersection of the two layers (ADR-0010).

**Not active-directory semantics.** `generic_write` on an NTFS resource means the holder can
write its data. On a directory object it would mean writing its attributes (`member`, for one),
which is a different right that ADG does not collect (§9.9). The target kind says which applies,
and today it is always `ntfs_resource`.

### 9.5 What "current" means once facts change

| Fact | What happens | How |
| --- | --- | --- |
| appears | its support is created, current | derivation at completion |
| stays (re-observed unchanged) | nothing re-derived; the support's and evidence's windows extend | `extend_observed`, set-based |
| reconciled absent | supports resting on it read `not_current`; others of the same relationship keep it current | the tombstone, read on every query (no write) |
| returns | its support reads current again, its window extends | the revival closes the tombstone; derivation extends the support |
| still stored, now says something else (owner changed, Deny ahead of the grant, member now resolved) | supports no rule produces any longer are **retired** (`not_current`); new ones are created | derivation of the changed unit |
| says the old thing again | the retired support is **reinstated** | derivation of the changed unit |
| has no row at all (demo lifecycle) | `unknown`; a later pass leaves it so | a support with a missing premise is never retired |

Retirement applies only to a support whose premises are **all present**. A tombstone already
says "gone", and retiring a support whose premise is missing would turn "nobody said it is gone"
into "it is gone". Retired supports keep their evidence: what used to hold, and until when, stays
explainable.

### 9.6 When derivation runs, and how much

At **run completion**: `IngestionService.complete_run` calls `project_completed_run` after the
closure pass, inside the completion's transaction, in a **savepoint**. A failure rolls back the
derivation only. A failed pass is recorded in a second savepoint, and the completion commits:
a projector defect never costs an observation. A replayed completion retries a run with no
successful pass. Passes are serialized by `pg_advisory_xact_lock`.

The pass derives the units the run **changed**: every fact for which it opened a version, that
is new, changed, revived or tombstoned. That is the history model's notion of a change, the one
the incremental risk pass reads. A fact the run only re-observed changes no derivation. Its
evidence windows, and the windows of supports whose premises the run all saw again, are widened
by one set-based statement per evidence kind plus one for supports. Retired supports are not
widened.

Consequences:

* the graph converges exactly when a run's facts and presence become final;
* a re-scan of an unchanged estate costs a handful of statements;
* a run that never completes is never derived. `RelationshipProjector.state()` lists it as in
  progress, and a rebuild covers it;
* a new rule version reaches facts as they change, and everywhere after a rebuild;
* **an installation upgrading to 0019 must run `python -m app.operations rebuild-graph` once**:
  facts collected before the projector existed have never been derived, and `state()` lists
  their runs until then.

### 9.7 Rebuild

`python -m app.operations rebuild-graph [--discard-existing]` re-derives every unit a stored
fact or a stored relationship names, in one transaction. It reads facts, never writes them, and
exits 2 if any unit could not be read. With `--discard-existing` it is a clean recomputation,
and it loses the record of retired supports. For the same facts, incremental derivation and a
clean recomputation produce the same current graph: the tests show it over the scenarios and
over Null Meridian's three generations of history. An in-place rebuild over unchanged facts
re-derives (in place) the supports last derived under an older collection basis, and a second
one replays everything.

### 9.8 Provenance and the pass log

Every support names its rule and version, the collection basis it read, its premises and
context, and each evidence link's first and last observing run. Each run names an
`observations` row and an `object_versions` timeline. Every pass is a row in
`graph_derivation_passes`.
`RelationshipProjector.state()` reads those rows to answer whether the graph is derived from
everything collected: the latest pass, the completed runs no successful pass covered, and the
runs still collecting.

### 9.9 Not derived, and what each would need

| Type | Needs, and none of it is collected |
| --- | --- |
| `can_modify_membership` | a group object's `nTSecurityDescriptor`: Allow entries for `WRITE_PROPERTY` on the `member` attribute (object-specific ACE, schema GUID `bf9679c0-0de6-11d0-a285-00aa003049e2`), `Self` membership (validated write `bf9679c0-…`), `GenericAll`/`GenericWrite`/`WriteDacl`/`WriteOwner` on the group, with inheritance and object-type scoping evaluated. The AD collector reads no security descriptor. |
| AD-object `owns`, `generic_*`, `write_dacl`, `write_owner` | the same descriptors, for users, groups and computers. A new observation kind and current-state table, not an extension of NTFS semantics. |
| `controls_gpo` | the `groupPolicyContainer` object's DACL and its SYSVOL folder's DACL. |
| `gpo_linked_to` | `gPLink` and `gPOptions` on domains, OUs and sites, plus OU placement (`directory_container` nodes). |
| `gpo_applies_to` | all of the above, plus security filtering, WMI filters, enforcement and block-inheritance. Always conditional once collected; never a full RSOP. |
| `trusts` | `trustedDomain` objects (direction, type, transitivity, SID filtering, selective authentication). A trust is never by itself a privilege path, and a foreign principal stays unresolved across it. |

No rule produces these types, and no fixture or demo fabricates them. An answer without one
says nothing about the estate: it is a capability limitation (`collection: not_collected`), not
an absence.

### 9.10 Validation state

| | |
| --- | --- |
| Rules over facts in memory | `backend/tests/relationships/test_rules.py` |
| Derivation through the ingestion API, lifecycle, idempotency, rebuild equivalence, failure isolation, statement counts | `backend/tests/db/test_graph_derivation.py` |
| 0019 over a populated 0018 graph, and its downgrade | `backend/tests/db/test_graph_derivation_migration.py` |
| Null Meridian: every run derived, no pass failed, clean rebuild = incremental | `backend/tests/db/test_null_meridian_demo.py::TestThePrivilegeGraph` |
| Local-groups collector: Pester (mocked seam), and its real netapi32 binding on the CI runner's own groups | `collector/powershell/localgroups/tests`, CI `windows-latest` |
| Local-groups collector output, through the contract models and the projector | `backend/tests/contracts/test_local_groups_collector.py`, `test_graph_derivation.py::TestTheLocalGroupsCollector` (need `pwsh`) |
| A remote domain-joined server, a live domain | **not validated**; none was available |

Measured on Null Meridian (6,274 edges, 827 resources, 4,124 entries, 300 runs): 15,267
relationships and 16,077 supports. Derivation inside the 300 completions took 15.2 s in total,
the largest pass 3.1 s. A clean rebuild took 9.1 s.
A rebuild over 400 memberships and 60 descriptors of seven entries each issues 32 statements for
1,120 supports. The test asserts fewer than one statement per ten supports.

### 9.11 For the path engine (GRAPH-01C)

* The membership hops are `member_of`, `primary_group_of` and `local_group_member_of`. Do not
  also hop the local specializations: they are readings of the same edges.
* A relationship's `currency` is the gate. Traverse `current`. Report `unknown` as unknown, never
  as absent. `not_current` is history.
* Compose a path's conditions from its supports' conditions, and add the ones only the path
  knows (`token_filtering_not_modeled` for remote use of local administration, the share layer
  for access through SMB).
* Never infer a relationship the store does not hold, and never read the absence of a
  `not_collected` type as "no".

These four points are what §10 implements; ADR-0042 records the decisions.

---

## 10. Traversal and the graph API (ADG-GRAPH-01C)

`app.traversal` searches the relationships §9 derives. `semantics` says which ones a path may
use, `search` holds the four bounded searches over an adjacency provider (no SQL, testable in
memory, ADR-0006), `completeness` decides what an answer means, and `service` runs them over
PostgreSQL in one snapshot. `app.api.relationship_graph` serves them under `/api/v1/graph`.
Nothing here derives, stores or changes a relationship.

### 10.1 Traversal semantics

| Type | Role | Class | Followed by path search | Reverse reachability |
| --- | --- | --- | --- | --- |
| `member_of`, `primary_group_of` | hop | identity membership | yes, member → group | yes |
| `local_group_member_of` | hop | host-local membership | yes, member → local group, in its host's scope | yes |
| `owns`, `generic_all`, `generic_write`, `write_dacl`, `write_owner` | hop | file/resource control (NTFS only) | yes, trustee → resource | yes |
| `local_admin_of` | reading of `local_group_member_of` | host-local privilege | never as a hop; attached to the step it reads | — |
| `remote_desktop_user_of`, `remote_management_user_of` | reading, always conditional | conditional remote access | never as a hop; attached to the step it reads | — |
| `foreign_principal_of` | informational | informational | never | never (neighbors only) |
| `nested_member_of` | traversal-only | — | never stored: a nested chain is its direct `member_of` hops | — |
| `can_modify_membership`, `controls_gpo`, `gpo_linked_to`, `gpo_applies_to`, `trusts` | not collected | directory-object control, group policy, trust | never; never fabricated | — |

The table is `TRAVERSAL_SEMANTICS`, one entry per type, and `GET /api/v1/graph/semantics`
publishes it. Classes are never merged: read access to a folder is not control of its server,
membership of Remote Desktop Users is not a successful sign-in, and a foreign principal's domain
is not a privilege over it.

**Direction** is the relationship's own. A hop is followed source to target, the way the
privilege flows. Reverse reachability starts at the target and walks the same hops backwards,
and still reports each route source to target.

**Host scope** is part of node identity: `fs01|S-1-5-32-544` and `fs02|S-1-5-32-544` are two
nodes, and nothing is ever joined by SID or display name. Two collectors that spell one host
differently (`fs01`, `fs01.corp.example.com`) produce two hosts; that limitation (01B report §12) is
preserved and tested, not papered over.

### 10.2 Paths

A **path is a sequence of nodes**. Each step carries every followed hop that joins its two
nodes, strongest first (confidence, then class, then type, then id), and every current reading
of that pair. Parallel relationships — an owner's `owns` and `write_dacl` — are one step, so a
route is never returned twice.

A path's **assessment** is never a score. `confidence` is the weakest of every step's strongest
hop, the readings the path *ends* on, and the conditions only the path knows; `chain_confidence`
is the hops alone. So a route to `fs01|S-1-5-32-555` reads `chain_confidence: confirmed` (bob is
a member) and `confidence: probable` with `logon_right_unevaluated` and
`remote_service_state_unknown` (signing in is conditional). A reading in the middle of a route
conditions nothing: the group's SID is in any logon's token. Conditions carry their origin
(`relationship`, `reading`, `path`) and step. The path-only condition is
`token_filtering_not_modeled`, added when a host-local account's membership of the host's
Administrators is then *used* (UAC remote restrictions filter local accounts, not domain ones). A
path ending at an NTFS resource notes `share_layer_not_evaluated`: it states NTFS control, and
access over SMB is the access engine's answer.

Every edge names its relationship id, rule and version, support count and the premise facts of
its strongest support, and links to `GET /api/v1/graph/relationships/{id}`, which returns every
support with its evidence and each fact's stored state.

### 10.3 What is followed

Only `current` relationships. `not_current` ones are history and are skipped; `unknown` ones are
counted and make an empty answer a `boundary`. The caller names a policy, and every answer echoes
it:

| Policy | Follows | Declined relationships |
| --- | --- | --- |
| `all_current` (default) | every current hop | none |
| `confirmed_only` | current hops whose confidence is `confirmed` | counted (`exploration.excluded_by_policy`); an empty answer is `policy_restricted`, never `no_path` |

### 10.4 Outcome and completeness

| `outcome` | Meaning | `conclusive` |
| --- | --- | --- |
| `found` | at least one route | true unless the projection is behind |
| `no_path` | the search finished within its limits over a current graph, met no boundary and declined nothing | true, about the modeled graph only |
| `incomplete_evidence` | nothing found; the graph does not reflect everything collected, or nothing was | false |
| `truncated` | nothing found before a limit stopped the search | false |
| `boundary` | nothing found; the search met an unknown relationship, an unresolved principal or a logon-time SID | false |
| `policy_restricted` | nothing found through what the policy follows | false |

Precedence: evidence, truncation, boundary, policy (`classify`). `completeness.reasons` lists
every reason, each with a category and a sentence: `nothing_collected`, `projection_failed`,
`projection_behind`, `max_depth`, `max_nodes`, `max_edges`, `max_paths`, `search_budget`,
`unknown_relationships`, `unresolved_boundary`, `implicit_membership_boundary`,
`excluded_by_policy`, and the notice `collection_in_progress`.

**Boundaries** are found walking towards a target: a node reached through an unresolved
relationship (a foreign or unresolvable principal, whose holders are outside the collected
domain) and a SID Windows adds at logon (`Everyone`, `Authenticated Users`, logon-session
SIDs), which no stored membership leads into. A path search that finds nothing finishes its
backward walk to `max_depth - 1`, so these are looked for wherever a route could have come from.

**Coverage** is on every response: the not-collected relationship types, directory-object
control, logon-time token memberships, cross-domain membership and the share layer. It is never
a completeness reason — no search could complete it — and it is why `no_path` is a statement
about the modeled graph and never about the estate.

### 10.5 Readiness and snapshot

Readiness is `RelationshipProjector.state()` (completed runs no successful pass covered, the
ones whose own pass failed, runs in progress, the pass count and the latest pass) plus the
collection basis — no second readiness record. `GET /api/v1/graph/readiness` reports it, and
every answer carries it as `basis.projection`: `current`, `behind` (`projection_behind`, the
state of an upgrade to 0019 before `rebuild-graph`), `failed` (`projection_failed`) or `empty`.

Each request runs in one `REPEATABLE READ, READ ONLY` transaction (`basis.snapshot`): the
collection basis, the projection state and every level of every walk are one snapshot, so a run
completing mid-request cannot mix two graphs into one answer. The `ETag` covers the collection
basis and the projection token (pass count and latest pass), so a rebuild — which moves no scan
run — invalidates cached answers.

### 10.6 Determinism

Nodes order by `(kind, canonical key)`. The shortest path is the lexicographically smallest node
sequence among the shortest routes: at each position, the smallest next node from which the
target is still reachable in the remaining hops. Alternates are ordered by length, then by that
order. Reverse reachability is ordered by hops, then node; neighbors by level, type, id. Every
frontier is sorted and every batch processed in sorted order; set order, row order, insertion
order and display names never decide. Incremental derivation, a clean rebuild and a different
ingestion order give the same routes (tested over scenarios and over Null Meridian).

### 10.7 Algorithms and limits

| Search | Algorithm | Bounded by |
| --- | --- | --- |
| shortest path | bidirectional, level-synchronous BFS (smaller frontier first); the route is chosen in memory once the visited sets meet; a depth probe looks one level past `max_depth` before reporting truncation | `max_depth`, `max_nodes`, `max_edges` |
| alternate paths | both sides walk until their depths sum to `max_depth`; simple routes enumerated in memory, one length at a time, depth-first in sorted order, pruned by distance to the target | the above, `max_paths`, a fixed work budget (200,000 extensions) |
| reverse reachability | backward BFS to `max_depth`, with a one-level probe | `max_depth`, `max_nodes`, `max_edges`; paged |
| neighbors | BFS over the requested types and directions to `depth`; `frontier` counts unexpanded nodes (a hint, not a truncation) | `depth`, `max_nodes`, `max_edges`; paged |

| Parameter | Default | Maximum |
| --- | --- | --- |
| `max_depth` | 10 | 32 |
| `max_nodes` | 20,000 | 100,000 |
| `max_edges` (relationships read) | 100,000 | 500,000 |
| `max_paths` | 10 | 100 |
| neighbor `depth` | 1 | 3 |
| page `limit` | 100 | 500 |

A value outside its range is a 422; nothing is clamped (ADR-0042, unlike ADR-0006's membership
routes). Every level is one read of the adjacency index plus `get_many`'s three, whatever it
holds; a cycle is crossed once; no all-pairs or closure table exists.

### 10.8 API

All under `/api/v1/graph`, behind `graph:read` (held exactly where `identities:read`,
`resources:read` and `access:read` are; never a side channel around them):

| Route | Answers |
| --- | --- |
| `GET /semantics` | the registry, policies, outcomes, reasons, limits, tie-break and coverage |
| `GET /readiness` | whether the graph reflects what was collected |
| `GET /paths/shortest?source=&target=` | the shortest route |
| `GET /paths?source=&target=` | bounded alternate routes |
| `GET /reachability?target=` | every node with a route into the target, paged |
| `GET /neighbors?node=&direction=&type=&depth=` | the relationships around a node, paged |
| `GET /relationships/{relationship_id}` | one relationship, its supports and evidence |

Nodes are addressed as `<kind>:<canonical key>` (`principal:fs01|S-1-5-32-544`,
`ntfs_resource:\\fs01\finance`); a non-canonical spelling is a 422 naming the field, an unknown
node a 404, and a bare BUILTIN SID with host-local namesakes a 404 that lists them. Responses
carry `basis`, `completeness`, `limits`, `exploration` and `coverage`.

### 10.9 Validation

| | |
| --- | --- |
| Registry against the taxonomy and rules | `backend/tests/traversal/test_semantics.py` |
| Searches over in-memory graphs (cycles, ties, row order, readings, conditions, scope, boundaries, limits) | `backend/tests/traversal/test_search.py` |
| Outcome precedence and readiness | `backend/tests/traversal/test_completeness.py` |
| HTTP surface over ingested estates (readiness states, ETag and rebuild, ingestion order, authorization, statement counts) | `backend/tests/db/test_graph_paths_api.py` |
| Routes over Null Meridian unchanged by a clean recomputation | `backend/tests/db/test_null_meridian_demo.py::TestThePathEngine` |
| Measured cost | `python -m tests.benchmarks.path_benchmark`; numbers in the ADG-GRAPH-01C report |
