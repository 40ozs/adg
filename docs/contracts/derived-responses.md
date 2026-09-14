# Derived responses (v1)

Everything under `docs/contracts/v1/*-observation.schema.json` describes what a **collector
sends**. This document describes the other direction: what the API **returns** when it
answers a question it derived rather than observed.

Normative. A client may rely on everything stated here.

| Response | Schema | Route |
| --- | --- | --- |
| Access explanation | [`access-explanation.schema.json`](v1/derived/access-explanation.schema.json) | `GET /api/v1/access/explain` |
| Causal path page | [`access-paths.schema.json`](v1/derived/access-paths.schema.json) | `GET /api/v1/access/paths` |
| Group resource impact | [`resource-impact.schema.json`](v1/derived/resource-impact.schema.json) | `GET /api/v1/groups/{identifier}/resource-impact` |

Captured examples — real responses, not illustrations written by hand — are in
[`v1/derived/examples/`](v1/derived/examples/).

The schemas are **generated** from the models the API serializes
(`python -m app.contracts.derived`), and `backend/tests/contracts/test_derived_schemas.py`
fails when a checked-in file falls behind. A hand-maintained copy of a response shape is a
second description that drifts, and the drift is invisible until a client believes the wrong
one.

## The one rule

**A client must never reimplement permission logic.** Every conclusion is computed
server-side and carried in the response. If a client finds itself intersecting two masks,
comparing ACE positions, or deciding whether a Deny applies, the API is missing a field —
that is a bug to report, not a gap to fill locally. SMB `Change` and NTFS `Modify` are the
same bits under two names, and the rights model exists to stop anyone discovering that the
hard way.

## `verdict`: four answers, not a boolean

`effective.access` is a boolean and it is not sufficient. `false` means three completely
different things, and an auditor who cannot tell them apart will stop looking in exactly the
place they should keep looking. Branch on `verdict.outcome`:

| `outcome` | Means | What a client should show |
| --- | --- | --- |
| `granted` | At least one right survives both layers. | The rights, and `certainty`. |
| `denied` | No right survives, and a Deny entry withheld what an Allow would have given. | The entries in `verdict.denials`. Somebody put this control here. |
| `no_grant` | No right survives and nothing denied one. Nothing names this principal. | "No access." This is the absence of a control, not a control. |
| `indeterminate` | No right was **established**, and collection is incomplete in a direction that could hide a grant. | "Unknown — not enough has been collected." Never "no access". |

`verdict.conclusive` is `false` for `indeterminate` and `true` otherwise. **While it is
`false`, no negative conclusion is supported by the response.** A rule, a report, or a
dashboard tile that counts `indeterminate` as "no access" is producing a false negative about
a part of the estate nobody has scanned.

`denied` and `no_grant` are different remediations. Removing a Deny is editing a decision
somebody made and may be relying on; there is nothing to remove for a `no_grant`.

### `outcome` and `certainty` are orthogonal

`certainty` is not a confidence score and it is not folded into the outcome. The outcome is
the verdict on the evidence; the certainty is the **direction** in which the evidence could
be wrong:

* `certain` — every input the answer depends on was observed.
* `at_most` — an unseen restriction could only narrow this. An upper bound.
* `at_least` — an unseen grant or membership could only widen this. A lower bound.
* `uncertain` — gaps in both directions.

`granted` with `at_most` is a real and common state: the file system permits this and the
share ACL nobody has read might not. It is still a finding. There is deliberately no
`probably_granted`, because a single enum could not carry both facts and the one it would
have to drop is the one an auditor acts on.

`verdict.may_understate` is the field to branch on when deciding whether to keep looking.

## `basis`: which collected state the answer came from

Every derived response carries one:

```json
"basis": {
  "token": "b6f0…",
  "runs": 4,
  "latest_run_id": "6f1c1a52-…",
  "latest_activity_at": "2026-09-14T08:00:12.481Z",
  "observations_applied": 1200,
  "batches_received": 4,
  "is_empty": false
}
```

`token` is the identity of the stored facts. **Equal tokens mean identical facts**: two
answers carrying the same token can be compared as answers, and any difference between them
is a difference in the question rather than in the estate.

`is_empty: true` means no collector has ever run. Every derived answer over an empty estate
reports no access, and there it means *nobody looked*.

## Caching: conditional GET, never a duration

Derived responses carry:

* `ETag` — a strong validator over *(contract version, collection basis, the exact
  request)*.
* `Cache-Control: private, no-cache` — store it, and revalidate before every reuse.
  `no-cache` does not mean "do not cache"; it means "do not serve without asking".
* `X-ADG-Collection-Basis` — the basis token alone, for logs and for operators.

Send the ETag back in `If-None-Match`. The server answers `304 Not Modified` when **nothing
has been collected since** — not when some interval has not yet elapsed. A client may
therefore hold an explanation for a week and still never render a stale one, and an
ingestion invalidates every cached answer the moment it lands.

There is no `max-age` anywhere and there will not be one. A time-to-live makes the wrong
promise in both directions: it serves an answer from before the membership change that just
happened, and throws away a valid one because a week is a long time.

Revalidation is cheap on the server too — the validator is computed before the access check
runs — so a UI that re-requests an open explanation costs one small query.

## Bounds, paging, and the difference between them

`GET /api/v1/access/explain` does not page. It answers about one principal and one directory,
so there is no population to slice; what can grow is the **graph**, because group nesting is
combinatorial. It is bounded by `max_causal_paths` and `max_removal_targets`, reports the
limits it applied in `limits`, and sets `complete: false` when either bit.

`GET /api/v1/access/paths` pages the same list, in the same deterministic order, with
`limit`/`cursor` and a `page` envelope.

**`complete` and `page.has_more` are different facts and both must be read.** The last page
of a truncated enumeration carries `has_more: false` and `complete: false` together; reading
only the first would conclude "that was all of them" about a list that was cut short.
`complete: false` also forbids a negative conclusion: the absence of a path is not evidence
there is none, and no removal may be believed sufficient.

Cursors are opaque and are not interchangeable between endpoints. A cursor from elsewhere is
rejected with `422`, never ignored — silently restarting from page one would make a client's
second page look like a continuation.

## Identifiers

Relationships are keyed by **stable identifiers, never display names**:

* `graph.nodes[].id` and `graph.edges[].id`, which `paths[].nodes` and `paths[].edges`
  reference. The graph is deduplicated and directly renderable as a diagram.
* `principal.key` — the storage key. A bare SID for a domain principal, `host|sid` for a
  BUILTIN group, because `BUILTIN\Administrators` on FS01 and on FS02 are different groups
  with the same SID (ADR-0001).
* `ace_key` — one ACE. **May be `null`**: an ownership path has `ace_position: -1` and no
  `ace_key`, because the rights come from owning the object and no entry confers them. A
  client that joins paths to ACE rows must handle it.

`display_name` is carried beside the key, for rendering only. Storing or comparing one is a
bug that survives until somebody is renamed.

## Reading a path

`relation` and `effect` are separate fields, and the product of the two is what a client
renders:

* `relation` — `grant` or `deny`. What the entry at the end of the chain says.
* `effect` — `contributes`, `redundant`, or `constrained`. What it is actually worth.
  `redundant` means an earlier entry had already settled every right it names; `constrained`
  means the other layer withholds all of it.

A single five-valued enum could not represent "a Deny that does not bite", which is an
ordinary state of a real ACL.

`removal_targets` is **measured**, by re-running the access check with each edge gone
(ADR-0013). Consequences for a client:

* `changes_nothing: true` must not be offered as a fix. Show `alternate_paths` instead —
  that is why it changes nothing.
* `rights_added` non-empty is a **warning**, not a fix: that removal would widen access,
  because the edge carried a Deny.
* An empty `sufficient_removals` does not mean "unfixable". It means no *single* edge
  suffices, which is the common case on a real estate.

## Resource impact

`GET /api/v1/groups/{identifier}/resource-impact` lists what a member of one group reaches.
Each row is an **answer** — rights, limiting layer, the entries that produced it — and not a
derivation. Full derivations are one bounded resolution each, and a page of them is not work
a server should do speculatively, so each row carries `explain`: the URL that produces the
derivation for that pair alone.

`names_group` decides where a fix goes. `true` means an entry names this group and can be
edited. `false` means the rights arrive through something else — a group this one is nested
inside, or a world SID such as `Everyone` — and there is no entry naming this group to
remove.

`membership` reports how many principals a change would affect, and `membership.complete`
says whether that count is exact. When it is `false` the counts are **lower** bounds; the
real blast radius is larger, never smaller.

## Versioning

Every body carries `schema_version`, pinned as a `const` in the schema.

* **Additive** change — a new optional field, a new member of an enum a client is told to
  treat as open — bumps the minor version and is served to any v1 client.
* **Breaking** change — a removed or renamed field, a narrowed type — requires a new major,
  a `v2/` directory, a new endpoint prefix, and a migration note in the phase handoff.
* A published schema file is never edited in place to mean something different.

The version is part of every cache validator, so a client holding a `1.0` body cannot be
handed a `304` by a server that now speaks `1.1`.
