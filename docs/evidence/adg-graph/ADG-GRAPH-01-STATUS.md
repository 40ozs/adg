# ADG-GRAPH-01 epic status

Repository-owned handoff for the privilege relationship graph epic. Each lane may run in a fresh
session: read this file and the latest lane report before doing anything, and verify every SHA
named here against `origin` first (`git fetch --all --prune`).

## Lanes

| Lane | Verdict | Branch | Implementation SHA | Report |
| --- | --- | --- | --- | --- |
| ADG-GRAPH-01A: domain model and evidence contract | `READY_FOR_GRAPH_DERIVATION` | `feature/ADG-GRAPH-01A-domain-model` | `20f89e1276bcf898dd36bb0477ff2a9c7b7fad8a` | [ADG-GRAPH-01A-report.md](ADG-GRAPH-01A-report.md) |

**Branch head.** The 01A branch head is the docs commit that adds this file, directly on top of
`20f89e1`; the lane return states its exact SHA. `git rev-parse
origin/feature/ADG-GRAPH-01A-domain-model` is authoritative and must equal it.

**Epic base.** `origin/integration/ADG-NEXT16-UI-02@3e26e4ad3fd351b5a8e956ae392ce8088ac485d1`,
which contains `origin/main@1ab01a2`. It carries 26 UI/deploy commits not yet on `main`; the
epic's final integration lane must order itself against that branch (report §1).

## State of the graph after 01A

* Domain: `backend/app/domain/relationships.py`, 18 relationship types; definition, collection
  and derivation stated separately.
* Schema: revision `0018_graph_relationships` (head). Three derived-state tables, empty, no
  backfill.
* Repository: `backend/app/repositories/relationships.py` (`record`, `get`/`get_many`,
  `adjacent`, `supported_by`). No HTTP API.
* Decision: ADR-0040. Specification: `docs/architecture/graph-relationships.md`.

## Important limitations carried forward

* Nothing derives relationships yet (every stored type is `derivation: not_implemented`).
* Directory-object ACLs and owners, GPOs, trusts and OU placement are not collected. Five types
  cannot hold data yet, and the ACL-right types cover NTFS and share entries only.
* No shipped collector emits local-group edges (`contract_only`).
* The collector Pester gate needs Windows. It was not runnable locally, and it passed in GitHub
  Actions on `20f89e1`.

## Next expected lane

**ADG-GRAPH-01B: relationship derivation.** Create `feature/ADG-GRAPH-01B-relationship-derivation`
from the verified head of `origin/feature/ADG-GRAPH-01A-domain-model`. Start from ADR-0040 §Follow-up:
projector rules for the collected and contract-only types, setting `derivation: projector` as
each lands, and how a re-derivation retires a support no rule produces any longer.
