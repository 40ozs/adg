# ADG-GRAPH-01 epic status

Repository-owned handoff for the privilege relationship graph epic. Each lane may run in a fresh
session: read this file and the latest lane report before doing anything, and verify every SHA
named here against `origin` first (`git fetch --all --prune`).

## Lanes

| Lane | Verdict | Branch | Implementation SHA | Report |
| --- | --- | --- | --- | --- |
| ADG-GRAPH-01A: domain model and evidence contract | `READY_FOR_GRAPH_DERIVATION` | `feature/ADG-GRAPH-01A-domain-model` | `20f89e1276bcf898dd36bb0477ff2a9c7b7fad8a` | [ADG-GRAPH-01A-report.md](ADG-GRAPH-01A-report.md) |
| ADG-GRAPH-01B: relationship collection and derivation | `GRAPH_DERIVATION_READY_WITH_LIMITATIONS` | `feature/ADG-GRAPH-01B-relationship-derivation` | `b51ab9328d7e35648479de27138eaec70011427d` (collector: `45d70c2af7a6bb411a06c6791314b216e0f7d2a2`) | [ADG-GRAPH-01B-report.md](ADG-GRAPH-01B-report.md) |
| ADG-GRAPH-01C: path engine and graph APIs | `GRAPH_PATH_ENGINE_READY_WITH_LIMITATIONS` | `feature/ADG-GRAPH-01C-path-engine-claude` (see below) | `c2fd1a7c5e7eb37e7756bb527de3d75991cf2a0f` (engine and API), `d23382ea58f10970677e86140f600026ff150607` (snapshot tests, ADR-0042), `3962170d2ea540450aa20a5a861a4152f7508153` (contract snapshot) | [ADG-GRAPH-01C-report.md](ADG-GRAPH-01C-report.md) |

**Branch heads.**

* **01A.** The head is `676d48fd83771a15625bc3fb6427e41cf9ab4362`, the docs commit on top of
  `20f89e1`.
* **01B.** It branches from exactly that SHA. Its head is the docs commit that adds the 01B
  report and this update, directly on top of the implementation; the lane return states its
  exact SHA.
* **01C.** It branches from exactly `e2efd749aaf46b6afbe3a04ad06c32e4efcfde5b` (the 01B head).
  Its head is the docs commit that adds the 01C report and this update, on top of `3962170`.
* **Two 01C branches exist.** The lane prompt named `feature/ADG-GRAPH-01C-path-engine`. While
  the 01C lane was running, another agent pushing as the repository owner created that branch
  and pushed a separate implementation to it (head `ace1108` at the time of the 01C report,
  every CI run red). The owner chose to keep this lane on
  `feature/ADG-GRAPH-01C-path-engine-claude` and leave that branch untouched. **The owner or
  GRAPH-01F must pick one before 01D starts;** neither lane merged or deleted the other.

`git rev-parse origin/<branch>` is authoritative for both and must equal those heads.

**Epic base.** `origin/integration/ADG-NEXT16-UI-02@3e26e4ad3fd351b5a8e956ae392ce8088ac485d1`,
which contains `origin/main@1ab01a2`. It carries 26 UI/deploy commits not yet on `main`; the
epic's final integration lane must order itself against that branch (01A report §1). During
01B, `claude/adoring-cannon-7gh2pf` was opened. It integrates UI/EDGE/Directory Access from
`3e26e4a` and records a hold on `main`. It touches no graph code, migration or ADR, but
GRAPH-01F must order against it too (01B report §1).

## State of the graph after 01B

* **Derivation.** `backend/app/relationships` holds 14 rules (`rules.py`, pure) and the
  projector (`projector.py`). It runs at every run completion, after closure, in a savepoint,
  and derives only the units the run changed. `python -m app.operations rebuild-graph`
  recomputes everything.
* **Derived types.** `member_of`, `primary_group_of`, `local_group_member_of`,
  `local_admin_of`, `remote_desktop_user_of` (always conditional), `remote_management_user_of`
  (always conditional), `foreign_principal_of`, and `owns`, `generic_all`, `generic_write`,
  `write_dacl` and `write_owner` over **NTFS** resources. `nested_member_of` stays a query-time
  traversal.
* **Not collected, not derived.** `can_modify_membership`, `controls_gpo`, `gpo_linked_to`,
  `gpo_applies_to`, `trusts`, and every directory-object target of the control types.
* **Schema.** Revision `0019_graph_derivation` is head. It adds support retirement
  (`retired_at`, `retired_basis_token`), six conditions, and `graph_derivation_passes`.
* **Collector.** `collector/powershell/localgroups` is new and read-only (netapi32). It reports
  local groups and their direct members per host.
* **Decisions.** ADR-0041 (re-derivation and retirement, trustee-level ACL semantics, share
  entries are not control). The specification is `docs/architecture/graph-relationships.md` §9.
* **No HTTP API and no traversal over relationships** after 01B; both are 01C (below).

## State of the graph after 01C (on `feature/ADG-GRAPH-01C-path-engine-claude`)

* **Traversal.** `backend/app/traversal`: a registry of what each relationship type means to a
  path (hops: the three membership types and the five NTFS control types; readings: local
  administration, Remote Desktop, WinRM, attached to their `local_group_member_of` step;
  informational: `foreign_principal_of`; never: `nested_member_of` and the five not-collected
  types), two named policies (`all_current`, `confirmed_only`), and bounded, deterministic
  shortest path, alternate paths, reverse reachability and neighbor exploration.
* **Answers.** `outcome` is `found`, `no_path`, `incomplete_evidence`, `truncated`, `boundary`
  or `policy_restricted`; `completeness` names every reason; `coverage` states what is never
  modeled; readiness comes from `RelationshipProjector.state()` (which gained `failed_runs` and
  `passes`); one `REPEATABLE READ, READ ONLY` snapshot per request; the ETag covers the
  projection.
* **API.** `/api/v1/graph/{semantics, readiness, paths/shortest, paths, reachability, neighbors,
  relationships/{id}}`, behind the new `graph:read` (held exactly where identities, resources
  and access reads are).
* **Schema.** Unchanged: `0019_graph_derivation` is still head.
* **Decisions.** ADR-0042; specification `docs/architecture/graph-relationships.md` §10.

## Important limitations carried forward

* **The local-groups collector has not read a remote domain-joined server.** Its Pester suite and
  its real netapi32 binding run in Windows CI against the runner itself, and its offline output
  is validated through the backend.
* **Directory-object ACLs and owners, GPOs, trusts and OU placement are still not collected.**
  A path engine over this graph cannot see those paths and must say so, never "no path".
* **Remote Desktop and WinRM are conditional by construction.** The logon right, the service
  state and WinRM's access policy are not collected.
* **An installation upgrading to 0019 must run `rebuild-graph` once.**
* **Pester and the `pwsh`-marked backend tests do not run in the Linux CI job.** Pester runs on
  `windows-latest`.

## Next expected lane

**ADG-GRAPH-01D: Crown Jewels, blast radius and choke points.** Create
`feature/ADG-GRAPH-01D-exposure` from the verified head of the GRAPH-01C branch the owner
confirms. If that is `origin/feature/ADG-GRAPH-01C-path-engine-claude`, the 01C report states
its exact SHA. Not from `main`. Build on `app.traversal`:

* shortest path, alternates and reverse reachability are the primitives; do not re-derive
  path semantics or follow a type the registry does not mark as a hop;
* branch on `outcome`, never on an empty list, and carry `completeness` and `coverage` into
  every exposure answer;
* logon-time SIDs and foreign principals are boundaries: an exposure "through Authenticated
  Users" is everybody behind it, and is not enumerable from the graph;
* a `not_collected` type is still a stated capability gap, never "no".
