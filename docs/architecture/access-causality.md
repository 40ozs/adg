# Access paths and causality: why an answer is what it is

[effective-access.md](effective-access.md) specifies *what* a principal can do. This document
specifies *why*: the membership and ACE paths that produced the answer, what each one is
actually worth, and what would change if any one of them were removed.

The two are different products. "Alice has Modify on `\\FS01\Finance`" closes a question and
opens an investigation. "Alice has Modify because `Finance-Team` is inside `Finance-RW`,
which the DACL grants Modify — and also because she is in `Domain Users`, which the share
grants Read" tells an administrator what to change, and warns them that changing one of the
two will not be enough.

The engine is `app.access_engine.causality`. Like the rest of the access engine it is pure:
no I/O, no framework. The decision behind it is
[ADR-0013](../decisions/0013-causality-is-measured-not-inferred.md).

---

## 1. The model

Five node kinds and six edge kinds. The distinctions exist because a reader acts on them
differently, not because the data happens to be shaped that way.

| Node | Meaning |
| --- | --- |
| `principal` | The subject, or any non-group principal on a chain |
| `group` | A group — domain, or one server's local group |
| `assumed_trustee` | `Everyone`, `Authenticated Users`, `NETWORK`: a SID Windows puts in every token of a kind, and that exists in no directory |
| `ntfs_ace` / `smb_ace` | One entry, **at one position**, on one ACL |
| `resource` / `share` | The object the rights are on |

| Edge | Meaning | Removable |
| --- | --- | --- |
| `membership` | An observed membership edge | **yes** |
| `assumed_membership` | Windows supplies this SID | no |
| `trustee` | This ACE names this principal | **yes** |
| `grant` / `deny` | This ACE applies to this object | no — it is not separable from the ACE |
| `ownership` | The subject owns the object | no — the remediation is to change the owner |

Two properties of the node model are load-bearing:

**An ACE node is identified by its position, not by its trustee.** Two entries naming one
group are two causes with two remediations, and the stored order is what decides which of
them settles a right (ADR-0010). Collapsing them by trustee would make an explanation
disagree with the access check beside it.

**`assumed_trustee` is not a group.** It looks exactly like one on an ACL and it cannot be
treated like one: nobody can be removed from `Everyone`. Modelling it as a group would let
the removal analysis propose a remediation that does not exist.

---

## 2. A causal path

One route from the subject, up through groups, onto an ACE, onto the object:

```
Alice → Finance-Team → Finance-RW → [NTFS ACE #2: Allow Modify] → \\FS01\Finance
```

The chain reads subject-first, trustee-last — the order it is read aloud in — regardless of
which way the traversal walked. An ACE naming the subject directly has a one-element chain.

**Every distinct simple chain is a separate path.** Alice may reach one ACE by two routes, or
two ACEs by one route; both are preserved. Collapsing them is how a remediation is signed off
having changed nothing, which is the failure this engine exists to prevent.

Chains are enumerated by `app.domain.simple_paths` over the membership subgraph the token
traversal already walked — the same bounded, cycle-safe, deterministically ordered DFS that
`find_paths` uses, extracted so there is one implementation and not two that can drift.

---

## 3. What a path is worth

The central distinction: **an ACE that matched the token is not automatically a cause.**

Each path carries two orthogonal fields.

`relation` is structural — `grant` or `deny`, from the ACE type.

`effect` is what it is actually worth:

| Effect | Meaning |
| --- | --- |
| `contributes` | It changed the final answer: a grant delivering rights that survive the layer crossing, or a deny withholding rights the other layer would have let through |
| `redundant` | It matched and settled nothing, because an earlier entry in the same DACL had already decided every right it names |
| `constrained` | It contributed at its own layer and the **other** layer withholds all of it, so it does not affect final access |

Four masks decompose the arithmetic:

| Mask | Meaning |
| --- | --- |
| `ace_rights` | The entry's own mask, generic bits expanded: what it would be worth alone |
| `layer_rights` | What it settled at its layer **that nothing earlier had already settled** |
| `effective_rights` | What it is worth to the final answer, after crossing with the other layer |
| `constrained_rights` | The part of `layer_rights` the other layer withholds |

### Redundancy is computed here, not read off the evaluator

`AppliedAce.contributed` answers a narrower question than causality needs. In the Windows
walk an Allow contributes whatever an earlier **Deny** had not taken; it says nothing about an
earlier *Allow* having granted the same rights already. That is correct for computing a mask —
a second Allow for rights already held changes no bit — and wrong for explaining one, because
"this entry is why Alice has Modify" is false of the second of two identical grants.

So the causality engine replays the walk, accumulating in each direction separately. An entry
whose novel contribution is empty is `redundant`. Read & Execute behind Modify is redundant;
reverse the two and the redundancy moves, because causality follows the stored order exactly
as the access check does.

The owner's implicit rights seed the granted accumulator, because Windows grants them before
it reads any ACE. An ACE handing an owner the rights ownership already confers is therefore
reported as redundant — and it is very often the entry somebody added believing it was what
granted the access.

### `constrained` is the alarming-ACL case

An NTFS ACE granting Full Control behind a share granting Read is on the ACL, matches the
token, and contributes at the NTFS layer. It changes nothing about what anyone can do over
SMB. Reporting it as a cause would overstate the finding; dropping it would hide that widening
the share makes it real. It is reported as `constrained`, with the rights it would deliver
recorded in `constrained_rights`.

The same applies to a Deny: one that withholds a right the other layer never granted is real
and changes no outcome.

### Ownership is a path with no ACE

An owner holds `READ_CONTROL` and `WRITE_DAC` whatever the DACL says — an owner explicitly
denied everything can still rewrite the ACL. It is invisible in every ACL viewer. An
explanation that listed only ACEs would report those rights as uncaused, which is the one
thing a causality engine may not do, so ownership is a first-class path with
`ace_position = -1` and a single `ownership` edge.

---

## 4. Removal is measured, never inferred

`RemovalTarget` says what removing one edge would do. **Every field is the result of re-running
the access check with that edge gone** — not of reasoning about which path looked important.

Reasoning fails here in two directions, both silently:

* **Removing an Allow can uncover a redundant Allow behind it**, leaving the rights exactly
  where they were. A model that subtracted the removed entry's contribution would report a
  revocation that does not happen.
* **Removing a membership can take a Deny with it**, *widening* access. A model that assumed
  removal only narrows would report a fix that is an escalation.

Re-evaluation gets both right and costs one access check per candidate. A membership removal
recomputes upward reachability over the subgraph first, because a group cut low on a chain
takes everything above it too; assumed SIDs always survive.

| Field | Meaning |
| --- | --- |
| `rights_removed` | Effective rights lost. **Empty whenever an alternate path remains** |
| `rights_added` | Effective rights gained — non-empty means the edge carried a Deny |
| `revokes_all_access` | Whether this one removal leaves no rights at all |
| `alternate_paths` | The contributing paths that still deliver rights afterwards |

`sufficient_removals` is very often empty, and that is the correct answer rather than an
unhelpful one. **The engine never reports a removal as sufficient while another path remains.**

Only edges that lie on a path are candidates: an ACE nobody's chain reaches is not a cause of
this subject's access, and offering it would send somebody to edit an unrelated entry.

---

## 5. Bounds and ordering

Group nesting is combinatorial. A subject in twenty groups that each nest three ways into one
ACL trustee has sixty chains to one ACE before a second trustee is considered.

| Limit | Default | Ceiling | Bounds |
| --- | ---: | ---: | --- |
| `max_paths` | 200 | 1,000 | Paths across the whole explanation, both layers |
| `max_paths_per_trustee` | 25 | 1,000 | Chains from the subject to any one trustee |
| `max_depth` | 32 | 128 | Membership hops on one chain |
| `max_removal_targets` | 64 | 256 | Edges re-evaluated |

Ceilings, not defaults, in the sense `app.domain.graph` already establishes: a caller-supplied
limit may be smaller and may not be larger. One table (`CEILINGS`) is read by both the
constructor and `clamped()`, so a value refused when constructed directly is the same value
clamped at the API boundary.

Truncation is always reported, never silent:

| Reason | Meaning |
| --- | --- |
| `max_paths`, `max_paths_per_trustee`, `max_depth`, `max_removal_targets` | A limit bit |
| `membership_incomplete` | The token traversal was itself truncated, so a trustee may be missing entirely — not merely one chain to it |
| `edges_not_supplied` | A trustee reached the token through membership and no supplied edge explains how; the chain the traversal recorded is reported instead, and alternate chains were not enumerated |

**`complete: false` means the paths shown are a subset**, so nothing may be concluded from the
absence of one — in particular, no removal may be believed sufficient.

Ordering is total and does not depend on dictionary iteration: NTFS before share, then ACL
position, then chain length, then the chain itself. Two runs over unchanged data produce
identical output, so a diff of two explanations means the estate changed.

Membership cycles bound the walk rather than unrolling it, and are reported separately as
strongly connected components. A cycle is a finding.

---

## 6. The endpoint

`GET /api/v1/access/paths/principals/{identifier}/resources/{resource}`

It is the one access route that does not page, and the exemption is narrow: it answers about
exactly one pair, so there is no population to slice. What can grow is the graph, and that is
bounded by `max_causal_paths` and `max_removal_targets` — both capped in the schema, so the
framework enforces them before any code runs — with `limits`, `complete` and `truncation`
reported on every response. `tests/api/test_access_bounds.py` holds that shape in place.

`max_paths` and `max_depth` are **not** redeclared: the traversal limits already mean, for the
membership walk, exactly what the explanation needs them to mean for one trustee's chains, and
a second parameter under the same name could disagree with the first.

**Explaining costs no extra query.** Path enumeration, the causality analysis and every removal
re-evaluation run in process over the membership subgraph the token traversal already read —
measured in `tests/db/test_access_paths_api.py`, not merely asserted here.

---

## 7. What this does not do

1. **It explains one principal against one resource.** There is no bulk shape, deliberately:
   see the Phase 4C finding on `resource → principals` cost.
2. **It does not rank paths by risk.** Every contributing path is reported equally; deciding
   which matters is Phase 5B's job and needs a policy this layer does not have.
3. **It does not model changing an owner, breaking inheritance, or editing an ACE's mask.**
   The only two things it measures are deleting a membership edge and deleting an ACE. Anything
   else is a different operation with a different blast radius.
4. **Combinations are not measured.** Each removal is measured alone. "Which two edges together
   would revoke this" is a set-cover problem over a bounded graph and is not attempted; where
   `sufficient_removals` is empty, it says so rather than guessing at a pair.
5. **Every limitation of the answer is inherited.** An explanation of an `at_least` answer is
   an explanation of a lower bound — see
   [effective-access-limits.md](effective-access-limits.md).
