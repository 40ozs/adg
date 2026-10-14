# ADG-GRAPH-01 epic status

Repository-owned handoff for the privilege relationship graph epic. Each lane may run in a fresh
session: read this file and the latest lane report before doing anything, and verify every SHA
named here against `origin` first (`git fetch --all --prune`).

## Lanes

| Lane | Verdict | Branch | Implementation SHA | Report |
| --- | --- | --- | --- | --- |
| ADG-GRAPH-01A: domain model and evidence contract | `READY_FOR_GRAPH_DERIVATION` | `feature/ADG-GRAPH-01A-domain-model` | `20f89e1276bcf898dd36bb0477ff2a9c7b7fad8a` | [ADG-GRAPH-01A-report.md](ADG-GRAPH-01A-report.md) |
| ADG-GRAPH-01B: relationship collection and derivation | `GRAPH_DERIVATION_READY_WITH_LIMITATIONS` | `feature/ADG-GRAPH-01B-relationship-derivation` | `b51ab9328d7e35648479de27138eaec70011427d` (collector: `45d70c2af7a6bb411a06c6791314b216e0f7d2a2`) | [ADG-GRAPH-01B-report.md](ADG-GRAPH-01B-report.md) |

**Branch heads.**

* **01A.** The head is `676d48fd83771a15625bc3fb6427e41cf9ab4362`, the docs commit on top of
  `20f89e1`.
* **01B.** It branches from exactly that SHA. Its head is the docs commit that adds the 01B
  report and this update, directly on top of the implementation; the lane return states its
  exact SHA.

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
* **No HTTP API and no traversal over relationships yet.**

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

**ADG-GRAPH-01C: path engine and graph APIs.** Create `feature/ADG-GRAPH-01C-path-engine` from
the verified head of `origin/feature/ADG-GRAPH-01B-relationship-derivation`, not from `main`.
Start from architecture §9.11:

* the membership hops are `member_of`, `primary_group_of` and `local_group_member_of`;
* currency is the gate, and `unknown` is never absent;
* a path's conditions compose its supports' conditions, plus what only the path knows
  (`token_filtering_not_modeled` for remote use of local administration, the share layer for
  SMB);
* a `not_collected` type is a stated capability gap, never "no".
