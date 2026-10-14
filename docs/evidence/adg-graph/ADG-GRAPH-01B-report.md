# ADG-GRAPH-01B: relationship collection and derivation

**Verdict: `GRAPH_DERIVATION_READY_WITH_LIMITATIONS`**

ADG now derives privilege relationships from the facts it collects. It does this at every run
completion, through the same ingestion path in production and in Demo Mode. Every relationship
is explainable down to the stored observations it rests on, and it stays current, becomes not
current, or is retired and reinstated as those facts change.

Membership, host-local privileges, NTFS ownership and NTFS control are derived, and their
semantics are checked against the access engine. A new read-only local-groups collector supplies
the host-local facts. Directory-object control, GPOs and trusts are **not collected**, so
nothing derives them, and the absence of one of them means nothing about the estate. The new
collector has not been run against a remote domain-joined server. These are the limitations
in the verdict (§12).

## 1. Git

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| Branch | `feature/ADG-GRAPH-01B-relationship-derivation` |
| Start SHA = predecessor SHA | `676d48fd83771a15625bc3fb6427e41cf9ab4362` (`origin/feature/ADG-GRAPH-01A-domain-model`, verified with `git fetch --all --prune` at the start and again before pushing) |
| `origin/main` | `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af`, an ancestor (28 commits behind the start) |
| Implementation commits | `45d70c2af7a6bb411a06c6791314b216e0f7d2a2` (local-groups collector), `b51ab9328d7e35648479de27138eaec70011427d` (derivation) |
| Final SHA | the commit adding this report and the status update, directly on top of the implementation; the lane return states it, and `git rev-parse origin/feature/ADG-GRAPH-01B-relationship-derivation` is authoritative |
| Merged to `main` or a deploy branch | **no** |

**Evidence review.** No GRAPH-01B branch, report or derivation code existed anywhere. The 01A
status file named exactly this branch and base, and `origin/feature/ADG-GRAPH-01A-domain-model`
had not moved past `676d48f`. Two other remote branches advanced during the lane:
`claude/compassionate-hamilton-3sh5db` (ADG-DIRECTORY-ACCESS-01) and
`claude/adoring-cannon-7gh2pf` (a UI/EDGE/Directory integration that merges it and records a
hold on `main`). Both branch from `3e26e4a`, below this stack, and neither touches the graph,
a migration, the schema or an ADR. So revision `0019` and ADR-0041 collide with nothing there.
GRAPH-01F must still order the stack against them.

**Contract file.** The lane requires reading `00_COMMON-CLAUDE-CODE-CLOUD-CONTRACT.md`. That
file is not in the repository on any branch, and not in the session environment. The lane
prompt's own rules were applied: read-only toward audited systems, no merge, no deploy, no
force-push, no claimed gate that did not run, and stop rather than guess.

**Session branch.** The cloud session was provisioned with the branch name
`claude/clever-gates-j18k5y`. The lane names its branch explicitly, and the epic's status file
chains lanes by those names, so all work is on `feature/ADG-GRAPH-01B-relationship-derivation`
and nothing was pushed to the session branch.

## 2. Environment

| | |
| --- | --- |
| Platform | Linux container (Claude Code cloud), not Windows |
| Python / PostgreSQL | 3.13.16 / 16.15, local cluster (CI: 3.13 / `postgres:16-alpine`) |
| PowerShell | 7.4.6, the official Linux release archive from GitHub, unpacked in the session scratch directory only |
| Pester | **not installable**: the PowerShell Gallery and nuget.org are denied by the session's network policy (HTTP 403). Not retried and not worked around |
| Active Directory, file servers, production systems | **none contacted**. Fixtures, the repository's scenarios and Null Meridian only |

## 3. Reconciliation with what exists

Read before writing anything:

* the 01A model (`app/domain/relationships.py`, `0018`, the repository, ADR-0040);
* the membership traversal (`app/domain/graph.py`, ADR-0002/0006);
* the effective-access engine (`app/access_engine`, its `evaluate_acl` walk and generic mapping);
* current state (`app/models/current.py`) and the history writer (`object_versions`, ADR-0018);
* the ingestion service, its closure pass and the incremental risk pass;
* the collection basis (`app/domain/basis.py`);
* the collectors (AD, SMB, NTFS and the orchestrator);
* the scenario transcripts, Null Meridian, and `tests/db/test_query_cost.py`.

What was reused rather than rebuilt:

* **ACL semantics are the access engine's.** Every control relationship comes from
  `evaluate_acl`: order, Deny, `INHERIT_ONLY`, `CREATOR OWNER`, `OWNER RIGHTS`, generic
  mapping. The projector has no ACL logic of its own beyond choosing the token and the entries
  to walk (§5.4).
* **Membership is the traversal's edge set.** `member_of`, `primary_group_of` and
  `local_group_member_of` are exactly the hops `app.domain.graph` walks, so a path over
  relationships and the membership API cannot disagree.
* **Change detection is the history model's.** A run's changed facts are the ones for which it
  opened an `object_versions` row.
* **The other building blocks.** Facts are read from the `current_*` views. Attribution is
  the collection-basis token. Statement counting uses the existing `StatementLog`.
* **Tests replay the real ingestion API.** Null Meridian, the demo, derives its graph by the
  same code a production estate does.

**Ponytail.** The repository has no Ponytail tool. `docs/handoffs/adg-ui-update-01.md` §3
defines it as the lazy-reuse rules, which were applied as listed above. No dependency was added.

## 4. Schema: revision `0019_graph_derivation`

Schema policy (§29) asks why 0018 is not enough. Two things it cannot represent:

1. **A support whose facts are still stored but no longer establish it.** 0018 reads currency
   from presence alone. When an owner changes in place, or a Deny appears ahead of an Allow,
   every premise is still present, and "Alice owns `payroll`" would read current forever.
   Deleting the support would destroy the record of what used to hold. A retirement marker
   is the only faithful representation: `retired_at` plus `retired_basis_token`, all or
   nothing.
2. **Whether the graph is derived from everything collected.** Nothing in 0018 records a
   projector pass, so a failed or missing derivation would be invisible. That is why
   `graph_derivation_passes` exists: one row per pass with its trigger, run, status, basis,
   rules and eleven counts.

The conditions check is also widened with the six new condition values.

The migration is additive. It adds two nullable columns and two checks to the supports, one
table with its own checks, and two indexes. The only 0018 object it replaces is the conditions
check, which it widens. A populated test covers it
(`test_graph_derivation_migration.py`):

1. Build 0018 and fill it through the API, including a stored relationship.
2. Fingerprint every row.
3. Upgrade, and show no fingerprint changed.
4. Retire a support and record one with a new condition.
5. Downgrade, which deletes exactly those two unrepresentable supports and their orphaned
   relationships, and leaves every collected fact byte-identical.
6. Re-upgrade.

`0018` is unchanged.

## 5. What is derived, and how

The full specification is `docs/architecture/graph-relationships.md` §9; ADR-0041 records
the decisions. Code: `backend/app/relationships/rules.py` (pure rules) and
`backend/app/relationships/projector.py` (reading, recording, retiring).

### 5.1 Derivability

| Type | Collected | Derivation | Rule(s) | Class |
| --- | --- | --- | --- | --- |
| `member_of` | yes | projector | `membership.directory_edge` | observed; unresolved for a foreign or unresolvable member |
| `nested_member_of` | yes | query time (never stored) | existing traversal | — |
| `primary_group_of` | yes | projector | `membership.primary_group_edge` | observed |
| `local_group_member_of` | **yes (new collector)** | projector | `membership.local_group_edge` | observed |
| `local_admin_of` | **yes (new collector)** | projector | `local_group.builtin_administrators` | deterministic |
| `remote_desktop_user_of` | **yes (new collector)** | projector | `local_group.builtin_remote_desktop_users` | **always conditional** |
| `remote_management_user_of` | **yes (new collector)** | projector | `local_group.builtin_remote_management_users` | **always conditional** |
| `foreign_principal_of` | yes | projector | `foreign_principal.membership_edge`, `foreign_principal.principal_object` | deterministic |
| `owns` | NTFS only | projector | `ntfs.owner` | observed |
| `generic_all`, `generic_write`, `write_owner` | NTFS only | projector | `ntfs.allow_entry`, `ntfs.allow_entries_combined`, `ntfs.owner_rights_entry`, `ntfs.null_dacl` | deterministic or conditional |
| `write_dacl` | NTFS only | projector | the four above, plus `ntfs.owner_implicit_rights` | deterministic or conditional |
| `can_modify_membership` | **no** | not implemented | — | — |
| `controls_gpo`, `gpo_linked_to`, `gpo_applies_to` | **no** | not implemented | — | — |
| `trusts` | **no** | not implemented | — | — |

The taxonomy states these as data (`collection`, `derivation`), and a test pins the projector's
rule registry to it. A type can say `projector` only if a rule produces it, and every type a
rule produces must say `projector`.

### 5.2 Membership

* **One edge is one direct hop**, for direct, group-to-group and primary-group membership.
  Nested membership is never stored: it is a path, and closure storage is what ADR-0002
  rejected. A cycle is three edges and three relationships, and derivation is per edge, so it
  cannot recurse.
* **Foreign and unresolved members come from the edge.** A member the collector reported as a
  foreign security principal, or as `member_kind: unresolved`, makes the membership
  `unresolved`, qualified `foreign_principal` or `endpoint_unresolved_at_collection`. That is
  the edge's own statement. A missing `principals` row is never read as one (ADR-0007).
* **`foreign_principal_of` its domain.** A foreign member yields it from its SID prefix. A
  foreign security principal object yields a second support of the same relationship. A
  well-known SID has no issuing domain and gets none.
* **Removal and reappearance** follow the edge's tombstone and revival.

### 5.3 Host-local

* **Every local type is scoped to its host.** A local edge whose group has no host is refused
  and counted as a skip, never stored unscoped.
* **One group SID on two hosts is two relationships.**
* **`local_admin_of`** is deterministic: membership in BUILTIN\Administrators is local
  administration.
* **`remote_desktop_user_of` and `remote_management_user_of`** are always conditional. The
  logon right, the service state and the WinRM access policy are not collected, so the
  supports carry `logon_right_unevaluated`, `remote_service_state_unknown` and
  `service_access_policy_unevaluated`.
* **An unresolved member** makes `local_admin_of` unresolved. RDP and WinRM keep both their
  conditions and the unresolved qualifier.
* **Domain BUILTIN is not a host privilege.** The directory's own BUILTIN\Administrators is a
  membership and nothing more.
* **An incomplete host read reconciles nothing,** so it takes no relationship away.

### 5.4 NTFS ownership and control

* **Ownership.**
  * `owns` restates the descriptor's owner SID. A BUILTIN owner is that host's group.
  * The owner also gets `write_dacl` through the implicit `WRITE_DAC`, which no Deny removes.
    It is conditional only when the descriptor declared more entries than ADG holds, because an
    unseen `OWNER RIGHTS` entry could replace it.
  * An `OWNER RIGHTS` entry replaces the implicit grant. It is then a support resting on both
    the entry and the descriptor, so an owner change ends it.
  * An owner change in place retires the old owner's supports, and a change back reinstates
    them.
* **Control.** A support exists only when `evaluate_acl` shows the entry delivering **every**
  bit the type requires, after generic mapping. The walk uses a token of just the trustee and
  `Everyone`, over the DACL with all other Allows removed and every Deny kept.

  | Type | Required bits |
  | --- | --- |
  | `generic_all` | `FILE_ALL_ACCESS` 0x1F01FF |
  | `generic_write` | `FILE_GENERIC_WRITE` 0x120116 |
  | `write_dacl` | `WRITE_DAC` |
  | `write_owner` | `WRITE_OWNER` |

  So Full Control supports all four, Modify supports `generic_write` only, and Read & Execute
  supports none.
* **Deny entries.**
  * A Deny ahead of the grant for the trustee, for `Everyone`, or for `OWNER RIGHTS` when the
    trustee owns the object removes the support.
  * A Deny ahead of the grant for anyone else depends on the token, so the support becomes
    conditional (`deny_may_apply`) and the Deny is recorded as context evidence.
  * When the descriptor's order is unknown, the support is conditional (`acl_order_unknown`).
  * A Deny behind the grant changes nothing.
* **Entry handling.** Inherit-only entries grant nothing on the object, `CREATOR OWNER` never
  matches, and inherited entries count in descriptor order.
* **Incomplete or unusual descriptors.**
  * A descriptor whose stored entry count disagrees with its declared count makes every grant
    conditional (`acl_entries_incomplete`).
  * An orphaned ACL is read and qualified.
  * A NULL DACL grants `Everyone` all four types.
  * Entries that only together deliver the bits form one joint support resting on all of them.
* **Trustee-level only.** Every control relationship is trustee-level. Group-mediated access
  (Alice through Finance-RW) is the path `member_of` → `write_dacl`, for GRAPH-01C to compose,
  and is never stored. An unresolved trustee holds what its entry grants and stays unnamed.
* **Share ACEs establish no control relationship.** A share permission limits a session's
  access through the share. It is not control of the share object. 01A had listed `smb_ace`
  as a premise kind of the four control types; 01B removes it (ADR-0041 §5).
* **Object-specific ACEs and resource-type boundaries.** NTFS entries carry no object type, so
  object-specific ACE behavior does not arise. The target kind is always `ntfs_resource`, and
  `generic_write` there means writing data, never directory-object attributes (architecture
  §9.4).

### 5.5 AD-object control, membership modification, GPO and trust

**Not collected; nothing derives them; nothing fabricates them.** The AD collector reads no
`nTSecurityDescriptor`, no `gPLink`/`gPOptions`, no GPO container or SYSVOL ACL, no OU placement
and no `trustedDomain` objects. Architecture §9.9 lists exactly what each type would need. That
includes the `member` attribute's schema GUID for `can_modify_membership`, and security
filtering, WMI filters, enforcement and block-inheritance for `gpo_applies_to`, which would
be conditional by construction and never a full RSOP.

A directory-object ACL collector was judged **not** the minimum correct extension for this lane.
It needs a new observation kind, a current-state table and its own ACE semantics
(object-type and inherited-object-type scoping, property sets, validated writes). It could not
be validated against any directory in this environment. An unvalidated AD-ACL reader feeding
security edges is the untrustworthy edge §37 warns against. Tests pin that no rule produces
these types, that the store holds none after any ingestion, and that the taxonomy refuses to
assert them.

### 5.6 Supports, currency and reconciliation

* **Supports.** A relationship with several supports, such as an owner's implicit `WRITE_DAC`
  and a Full Control entry, stays current while any one is current. When one support's
  evidence is tombstoned, that support reads not current and the relationship keeps its
  current support. When the last one goes, the relationship reads not current. When a support
  returns, it reads current again with its window extended.
* **Retirement** (§4). It applies only to supports whose premises are all present; a support
  with a missing premise stays unknown. The projector re-derives a changed unit and retires
  what the rules no longer produce. A later derivation that produces a retired support again
  reinstates it.
* **Unknown is never absent.** A fact with no row reads `unknown`, and a later pass keeps it
  that way: it is neither retired nor tombstoned.

### 5.7 Provenance

Every support carries:

* its rule and version;
* the collection basis it read;
* its premises, and its context evidence (the descriptor, a qualifying Deny);
* each evidence link's first and last observing run;
* the entry's `access_mask`, `ace_type` and `inherited` for an ACL support.

Every evidence link reaches an `observations` row and an `object_versions` timeline. Every pass
is a row in `graph_derivation_passes`. `RelationshipProjector.state()` answers whether the graph
covers everything collected: latest pass, completed runs never derived, runs still collecting.

### 5.8 Ingestion integration, incremental derivation, rebuild

* **Integration point.** `IngestionService.complete_run` calls `project_completed_run` after
  the closure pass, inside the completion transaction, in a savepoint. A failing rule rolls
  back the whole pass and never the completion. The failure is recorded as a `failed` pass, and
  a replayed completion retries it. Passes are serialized by a transaction-scoped advisory
  lock.
* **Incremental derivation.** A pass re-derives only the units for which the run opened a
  version. Facts re-observed unchanged are confirmed by one set-based statement per evidence
  kind plus one for supports.
* **Rebuild.** `python -m app.operations rebuild-graph [--discard-existing]` re-derives every
  unit, reads facts and never writes them, and exits 2 if a unit could not be read. An
  installation upgrading to 0019 runs it once.

## 6. The collector extension: `collector/powershell/localgroups`

**Read-only.** It calls `NetServerGetInfo`, `NetLocalGroupEnum`, `NetLocalGroupGetMembers`
(level 2) and `LookupAccountName`, and frees their buffers. Nothing else; a Pester test pins
the bound Win32 functions.

What it reports and refuses:

* **Readings, never conclusions.** It reports every local group as a host-scoped `local_group`
  principal and every member as a `local_group_member` edge, keyed by the SID the host
  returned.
* **Orphaned SIDs are kept.** An orphaned SID, such as a deleted account in BUILTIN\Administrators,
  is reported `unresolved`. CIM would drop it.
* **No member principals.** Those are the directory collector's to report.
* **One run per host.** A host reconciles its `local_groups_host` scope only when every group
  and every member list read without error. Anything less is `partial` and reconciles nothing.
* **Domain controllers are refused.** They have no local account database.
* **A group that does not resolve to the host's own alias is refused.**
* **Error codes.** Win32 status codes become stable error codes: `access_denied`,
  `unreachable`, `not_found`, `enumeration_failed`, `lookup_failed`.

Validation, stated plainly:

| What | Where | State |
| --- | --- | --- |
| Pester suite (27 tests: translation, orchestration, refusals, error codes, the bound Win32 set; mocked seam) | `collector/powershell/localgroups/tests` | **not runnable locally** (no Pester). **Passed in CI** on `windows-latest`: 27 of 27, [run 38598050365](https://github.com/40ozs/adg/actions/runs/38598050365) on `b51ab93` |
| The real netapi32 binding, reading the CI runner's own groups | same suite, `Reading this machine`, Windows only | **passed in CI** (same run). It is a standalone runner, not domain-joined |
| The C# interop compiling, and offline runs over the host fixture | local, `pwsh` 7.4.6 on Linux | passed (FS01 succeeded and reconciled with 13 observations; DC01 refused `domain_controller`; an unknown host `unreachable`) |
| Offline output against the published schemas and the backend models | `backend/tests/contracts/test_local_groups_collector.py` (6) | passed locally with `pwsh` on `PATH`; skipped in Linux CI (no `pwsh`) |
| Offline output ingested, and what the projector derives | `test_graph_derivation.py::TestTheLocalGroupsCollector` | passed locally with `pwsh`; skipped in CI |
| **A remote, domain-joined file server** | — | **not validated**: none was available |

The collector is not yet a job kind of the scheduled orchestrator. It runs from its entry
script.

## 7. Tests

### 7.1 Gates

| Gate | Baseline (`676d48f`) | Final |
| --- | --- | --- |
| `ruff check .` / `ruff format --check .` | clean / clean (405 files) | clean / clean (414 files) |
| `mypy app tests` | clean (404 source files) | clean (413 source files) |
| unit `pytest -m "not smoke"` | 5,864 passed, 124 skipped | **5,928 passed, 130 skipped** (+64 new; the 6 new skips are the `pwsh` contract tests) |
| database `pytest -m smoke` (PostgreSQL 16) | 1,277 passed, 1 xfailed | **1,316 passed, 1 skipped, 1 xfailed** (+40 new; the skip is the `pwsh` collector test)¹ |
| `pwsh`-marked tests (`-m powershell`, `pwsh` on `PATH`) | not runnable (no `pwsh`) | **114 passed**: the AD, SMB, NTFS, local-groups, incremental and example collectors' offline contract tests; `test_graph_derivation.py` + its migration test **37 passed** with the collector test included |
| Collector Pester | not runnable (no Pester) | not runnable locally; CI below |
| GitHub Actions | — | **all green** on the implementation head `b51ab93`, [run 38598050365](https://github.com/40ozs/adg/actions/runs/38598050365): backend (lint, types, unit, database), frontend, collector Pester on `windows-latest` (1,076 passed, 1 skipped; 01A: 1,049 + the same skip), compose. The docs commit on top changes Markdown only. |

The baseline was run before any change, on a clean tree, and had no failures.

¹ The full database run took 42 min. It ran beside the mutation runs, each on its own database.
One test changed after that run had collected it: `test_a_rebuild_never_writes_a_collected_fact`
now fingerprints every collected-fact table, not four. Its file was re-run on its own afterwards
and passed (37 passed, above).

### 7.2 New and changed tests

* `tests/relationships/test_rules.py` (58, pure). Covers:
  * the rule registry against the taxonomy;
  * direct, group-to-group, primary, implicit and cyclic membership, and that nesting is never
    derived;
  * unresolved and foreign members;
  * host scope, BUILTIN groups, conditional RDP and WinRM, the same group on two hosts,
    fail-closed;
  * ownership and `OWNER RIGHTS`;
  * what each mask establishes: mapping, unrelated masks, inherit-only, `CREATOR OWNER`,
    joint supports;
  * Deny and order: trustee, Everyone, behind the grant, other trustee, Authenticated Users,
    inherit-only, unknown order, `OWNER RIGHTS`;
  * incomplete descriptors, an orphaned ACL, a NULL DACL;
  * determinism over every permutation of the facts.
* `tests/db/test_graph_derivation.py` (36, through the ingestion API). Covers:
  * membership, host privileges and NTFS projection;
  * the lifecycle: removal, return, an owner changed in place, a Deny appearing and leaving, a
    partial reading;
  * unknown versus absent;
  * idempotency: replay, an unchanged re-scan, a resent completion, a duplicate observation, a
    repeated derivation;
  * rebuild equivalence, and a rebuild never writing a fact;
  * provenance: every relationship traces to observations, no orphan support or evidence, the
    pass log, no underived type ever stored;
  * failure safety: a failing rule, a write failing midway;
  * statement counts;
  * the shipped collector's output.
* `tests/db/test_graph_derivation_migration.py` (1): §4.
* `tests/db/test_null_meridian_demo.py::TestThePrivilegeGraph` (3):
  * every one of the demo's runs was derived and no pass failed;
  * only supported types are present, and none targets a share;
  * a clean recomputation equals the incremental graph.
* `tests/domain/test_relationships.py` (+6): retirement, and share entries as a non-premise.
  It also covers updated collection statuses.
* `tests/contracts/test_local_groups_collector.py` (6, `pwsh`).
* Updated for 0019: `test_graph_relationships.py` (the migration literals; derivation is
  stubbed there so its hand-recorded relationships stay the only ones) and
  `test_graph_relationship_migration.py` (four graph tables).

### 7.3 Required coverage (§30)

| Requirement | Test |
| --- | --- |
| direct / group-to-group / primary | `TestDirectMembership` (rules); `test_every_membership_edge_kind_the_traversal_walks_is_one_relationship` |
| nested / traversal | `test_a_nested_chain_is_its_direct_hops_and_nothing_more`, `test_nested_membership_is_never_derived` |
| cycle | `test_a_cycle_terminates_and_creates_one_relationship_per_edge`, `test_a_cycle_is_three_edges_and_nothing_more` |
| unresolved SID / FSP | `TestUnresolvedAndForeignMembers`; `test_foreign_members_are_unresolved_and_their_domain_has_two_supports` |
| membership removal / reappearance | `test_a_membership_removed_then_restored` |
| host scope required | `test_a_local_edge_with_no_host_fails_closed`, `test_a_local_edge_is_scoped_to_its_host` |
| local admins / RDP / WinRM | `test_builtin_groups_are_read_as_host_privileges`, `test_remote_access_is_never_unconditional`, `test_remote_desktop_membership_is_conditional_on_what_is_not_collected` |
| same principal, separate hosts | `test_the_same_member_on_two_hosts_is_two_relationships`, `test_the_same_group_sid_on_two_hosts_is_two_relationships` |
| incomplete host-local collection | collector: a partial host reconciles nothing (Pester; `test_a_domain_controller_is_refused_and_reconciles_nothing`); projector: `test_a_partial_reading_never_takes_a_relationship_away`, `test_a_host_removal_ends_only_that_hosts_privilege` |
| ownership / ownership-backed WriteDACL | `TestOwnership`; `test_the_owner_and_a_full_control_entry_are_two_supports_of_one_right` |
| owner change / removal | `test_an_owner_changed_in_place_retires_the_old_owners_support` |
| valid allow / GenericAll / GenericWrite / WriteDACL / WriteOwner | `TestWhatAMaskEstablishes`; `test_modify_is_generic_write_only` |
| deny | `TestDenyAndOrder`; `test_a_deny_for_another_trustee_ahead_of_the_grant_is_named_as_context`; `test_a_deny_that_appears_ahead_of_a_grant_retires_it_and_its_removal_restores_it` |
| inheritance | `test_an_inherit_only_entry_grants_nothing_here`, `test_an_inherit_only_deny_takes_nothing_away_here`, `test_an_inherit_only_deny_for_another_trustee_qualifies_nothing_here` |
| unrelated mask | `test_an_unrelated_mask_establishes_nothing`, `test_a_deny_for_unrelated_rights_changes_nothing` |
| trustee / group resolution, unresolved trustee | `test_a_host_local_trustee_is_scoped`, `test_a_builtin_owner_is_that_hosts_group`, `test_an_unresolved_trustee_holds_what_its_entry_grants_and_stays_unnamed` |
| resource type boundaries | `test_share_entries_establish_no_control_relationship`, `test_share_entries_are_not_a_premise_of_any_control_type` |
| object-specific ACEs, membership modification, GPO, trust | not implemented. Covered by `test_no_rule_produces_an_underived_type`, `test_no_underived_type_is_ever_stored`, `test_an_uncollected_type_cannot_be_asserted[…]` and the Null Meridian type check |
| multiple supports / one removed / last removed / returns | `test_the_owner_and_a_full_control_entry_are_two_supports_of_one_right`, `test_a_removed_entry_ends_its_support_and_the_owner_keeps_the_right`, `test_two_entries_granting_one_right_are_two_supports_of_one_relationship`, `test_a_host_removal_ends_only_that_hosts_privilege`, `test_an_entry_that_returns_makes_its_support_current_again` |
| traces to evidence / no orphan support / no orphan evidence | `test_every_relationship_traces_to_stored_observations`, `test_no_support_without_evidence_and_no_relationship_without_a_support`, `test_a_write_that_fails_midway_leaves_no_support_without_its_evidence` |
| duplicate observations / repeated derivation / repeated reconciliation | `test_the_same_observations_sent_twice_in_one_run_are_one_support`, `test_the_store_collapses_a_derivation_repeated_in_one_batch`, `test_replaying_a_scan_derives_nothing_new`, `test_a_resent_completion_does_not_derive_again`, `test_a_rebuild_over_unchanged_facts_changes_nothing`, `test_a_rescan_that_changes_nothing_confirms_every_window_and_derives_nothing` |
| unknown ≠ absent / not collected ≠ false / conditional stays conditional | `test_a_fact_with_no_row_is_unknown_and_a_later_pass_keeps_it_so`; the underived-type tests; `test_a_foreign_member_of_remote_desktop_keeps_both_the_conditions_and_the_doubt`, `test_a_short_dacl_makes_every_grant_conditional` |

## 8. Mutation checks

Each mutation was applied to a scratch copy of the final code. The four graph test files
(rules, domain, derivation, relationships) were run against a separate database, and the
mutation was restored afterwards. An unmutated control run of the same files passed.

| Mutation | What it breaks | Result |
| --- | --- | --- |
| `host-scope-enforcement-removed` | a local edge with no host is stored unscoped instead of skipped | **killed**: 1 test (`test_a_local_edge_with_no_host_fails_closed`) |
| `unknown-read-as-absent` | evidence with no fact row reads `absent` instead of `missing` | **killed**: 2 |
| `owner-rights-drops-owner-premise` | an `OWNER RIGHTS` grant no longer rests on the descriptor (one evidence dependency removed) | **killed**: 1 |
| `retirement-ignores-missing-premises` | supports with an absent or missing premise are retired too | **killed**: 2 |
| `no-retirement` | supports the rules no longer produce are never retired | **killed**: 3 (including rebuild equivalence) |
| `deny-ignored` | Deny entries are dropped from the walk (applicability bypassed) | **killed**: 4 |
| `inherit-only-applies` | `INHERIT_ONLY` entries are treated as applying to the object | **killed**: 1 |
| `token-dependent-deny-ignored` | a Deny for another trustee no longer makes the grant conditional | **killed**: 4 |
| `nondeterministic-entry-order` | entries are walked in arrival order instead of descriptor order | **killed**: 2 |
| `unsupported-type-emitted` | `can_modify_membership` gets a premise kind and a rule emits it from membership edges | **killed**: 10 |
| `duplicate-support-identity-allowed` | the store stops collapsing one derivation repeated in a batch | **killed**: 1 |
| control: no mutation | — | 222 passed, 1 skipped (the `pwsh` test) |

Two mutations survived the first round: `inherit-only-applies` and
`nondeterministic-entry-order`. Each exposed a real gap in the tests: no test had an
inherit-only Deny for *another* trustee, and the permutation test had no Deny whose position
mattered. A test was added for the first and the second was strengthened. Both now fail when
mutated.

## 9. Performance

These are measured numbers, not an SLA. All runs are on this container.

**Null Meridian** (6,274 membership edges, 827 NTFS resources, 4,124 entries, 300 runs
ingested by the demo reset):

| | |
| --- | --- |
| Passes / units derived | 300 / 9,355 |
| Supports created / extended / retired | 16,140 / 31,382 / 0 |
| Derivation time, all 300 completions together | **15.2 s**; largest single pass 3.1 s |
| Clean rebuild (`--discard-existing`) | 7,101 units → 15,267 relationships, 16,077 supports, **9.1 s** |
| In-place rebuild over the same facts | 10.9 s |
| Relationship counts | `member_of` 4,315; `generic_write` 3,093; `primary_group_of` 1,893; `write_dacl` 1,703; `generic_all` 1,683; `write_owner` 1,683; `owns` 827; `local_admin_of` 66; `local_group_member_of` 66 |

The first implementation re-derived every unit a run touched. On Null Meridian that took 60.4 s
in total, with a largest pass of 7.6 s. That is too close to the collector's 100 s completion
timeout on a real directory re-scan, where every edge is re-observed. Two changes fixed it:
deriving only the units a run changed and confirming the rest set-based, and recording with
`executemany` instead of compiling multi-row `VALUES`.

**What it costs the rest of the suite.** Every ingestion test now also runs a pass at each
completion. CI's database step took 34.7 min on `b51ab93`, against 22.5 min on 01A's
`20f89e1`. A like-for-like sample was run locally, one after the other on separate databases:
eight ingestion-heavy files at `676d48f` and at the head. It took 743 s at the base and 835 s
at the head (+12%). Of the difference:

* 15 s is the new clean-rebuild test;
* each Null Meridian reset grew by 10–20 s, which is its 300 passes;
* fixtures that ingest a handful of runs each grew by about 1 s.

That is a steady cost per completion, about 50 ms on average over Null Meridian's passes, and
not a statement count that grows with the facts. The rest of the CI difference is the 40 new
database tests, and runner variance was not ruled out.

**Statement counts (counted, not timed).**
`test_statements_per_pass_do_not_grow_with_the_number_of_facts` rebuilds 400 memberships and
60 descriptors of 7 entries each. It asserts fewer statements than one per ten supports.
Measured: **32 statements** (20 `SELECT`, 10 `INSERT`, 1 `UPDATE`, 1 `DELETE`) for
**1,120 supports**. A re-scan that changes nothing derives no unit. Besides the pass's own
bookkeeping, it widens windows with one statement per evidence kind plus one for supports,
however many facts it re-read.

## 10. Findings made while building

1. **Share entries are not control.** 01A let `smb_ace` be a premise of the four control
   types. A share entry granting `WRITE_DAC` does not let its holder change the share's
   permissions, so such an edge would have been false. It was removed from the taxonomy, with
   a test.
2. **Presence-based currency cannot see an in-place change.** This is the motivation for
   retirement (§4, ADR-0041).
3. **A trustee-level Deny has three meanings, not one.** It may apply universally, depend on
   the token, or have an unknowable position. Treating every Deny as removing the grant would
   hide real access. Ignoring Denies would invent it.
4. **Alembic's `version_num` is `varchar(32)`.** The first revision id was 34 characters long
   and failed at upgrade, so it was renamed to `0019_graph_derivation`.
5. **`relationships_created` was over-counted.** It was credited per support instead of per
   relationship. A test caught it, and it is now credited once per relationship.
6. **PowerShell traps in the collector.** An empty array returned from an `if` collapses to
   `$null` under StrictMode, so the completion's reconciled scopes would have gone missing.
   A `$null` passed to a .NET `string` parameter arrives as `""`, which `netapi32` reads as a
   server named "". Both were fixed and are tested.

## 11. Read-only boundary

No audited system was contacted or modified. The new collector binds four read-only
`netapi32`/`advapi32` functions and the buffer free, and nothing that writes. The projector
writes only ADG's own tables (`graph_*`). `test_a_rebuild_never_writes_a_collected_fact`
fingerprints every collected-fact table, the observations and their version history around a
rebuild.

## 12. Limitations

* **Directory-object control, `can_modify_membership`, GPOs and trusts are not collected and
  not derived** (§5.5). A path over today's graph cannot see AD ACL abuse, GPO control or
  trust-based paths, and must say so (`collection: not_collected`), never "no path".
* **The local-groups collector has never read a remote domain-joined server.** Its Pester suite
  and its real binding run in Windows CI against the runner itself. A real file server, its
  `RestrictRemoteSAM` posture and domain members are unvalidated.
* **Host name agreement is the operator's job.** A local group joins an NTFS entry only when
  both collectors name the host identically. Nothing reconciles an FQDN against a short name.
* **The collector is not scheduled by the orchestrator.**
* **Pester and the `pwsh`-marked backend tests do not run in Linux CI.** The latter ran here.
* **Remote Desktop and WinRM are conditional by construction**, because the logon right, the
  service state and WinRM's SDDL are not collected.
* **An upgrade to 0019 needs one `rebuild-graph`** before the graph reflects facts collected
  earlier. `state()` lists the underived runs until then.
* **`--discard-existing` loses the record of retired supports** by design.
* **The branch carries the epic's unmerged UI/deploy ancestry** (01A report §1). GRAPH-01F
  must also order the stack against `claude/adoring-cannon-7gh2pf`'s integration and its
  recorded hold on `main` (§1).

## 13. Next lane

`ADG-GRAPH-01C — Path Engine and Graph APIs`, branched as `feature/ADG-GRAPH-01C-path-engine`
from the final SHA of `origin/feature/ADG-GRAPH-01B-relationship-derivation` recorded in the
status file. Architecture §9.11 lists what the path engine must respect: which hops to walk,
currency as the gate, composing conditions, and never reading `not_collected` as "no".
