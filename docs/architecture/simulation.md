# Non-destructive what-if simulation

**Phase:** 9A · **Status:** implemented (no HTTP surface) · **Code:** `backend/app/simulation/`

Before every permission change somebody asks *"if I take this group off the ACL, who loses
access?"*, and the honest answer is almost never the obvious one:

* an alternate group membership keeps the access, so the change achieves nothing;
* a Deny earlier in the DACL meant the entry was never granting anything anyway;
* the share ACL in front of NTFS was the real limit, so tightening NTFS changes nothing;
* the entry the ticket names was removed by somebody else last week.

Every one of those is a reason a carefully reviewed change does not do what the reviewer
believed. This document describes the engine that answers the question against real collected
state, without touching Active Directory, a share, an NTFS descriptor, or one row of ADG's own
observations — and, as much, what it refuses to claim.

See ADR-0020 for why the overlay is applied where it is.

---

## 1. The shape, in one paragraph

A **proposal** is an immutable `SimulationOverlay`. A pair of **overlay repositories** wraps
the two repositories `AccessService` already takes by injection, applying the proposal to the
rows as they are read. The **production access engine** is then run twice over the same
session — once against the baseline repositories, once against the overlay pair — and the two
answers are compared. Nothing else happens: there is no simulation arithmetic, no second
resolver, and no branch inside the engine that knows which world it is in.

```
SimulationOverlay ──► OverlayResourceRepository   ──┐
                      OverlayMembershipRepository ──┤
                                    (wraps)         ├──► AccessService ──► EffectiveAccess "after"
ResourceRepository / MembershipRepository ──────────┘
                                    (same instances) ──► AccessService ──► EffectiveAccess "before"
                                                                                 │
                                                                    compare_access ──► AccessDelta
```

Phase 7A used the same seam to answer about a *past* instant; the baseline pair may be its
as-of repositories, and then the proposal is evaluated against last Tuesday's estate.

---

## 2. What a proposal can say

| Change | Names | Effect |
| --- | --- | --- |
| `add_member` / `remove_member` | group key, member key, edge kind | an edge in the adjacency a traversal walks |
| `add_ntfs_ace` | resource, trustee **SID**, type, mask, flags, optional position | an entry in a DACL |
| `modify_ntfs_ace` / `remove_ntfs_ace` | resource, existing `ace_key` | that entry |
| `add_share_ace` / `modify_share_ace` / `remove_share_ace` | share, trustee SID or `ace_key` | an entry in a share ACL |
| `set_inheritance` | resource, protected, disposition of the inherited entries | `SE_DACL_PROTECTED` |

Four rules are worth stating because each of them is a way the model could have been subtly
wrong.

**A change names its target; it does not carry it.** A removal says *"the ACE whose key is
`k`"*, not a copy of that ACE. So a proposal written last week and evaluated today is evaluated
against today's ACL, and an entry somebody else has since removed comes back
`TARGET_NOT_FOUND` rather than being quietly re-created.

**Trustees are named by SID, never by key.** A trustee key is host-scoped for a BUILTIN
principal — `fs01|S-1-5-32-544` is a different group from `fs02|S-1-5-32-544` — and which host
applies is a fact about the resource, not about the proposal. The key is derived at application
time through `referenced_principal_key`, exactly as ingestion derives it (ADR-0001).

**Position is part of the meaning.** A DACL is evaluated in stored order, so an addition has to
land somewhere. Left unplaced it is placed *canonically* — Deny ahead of every Allow, both
ahead of everything inherited — which is where the Windows ACL editor puts one. An explicit
`order_index` is honored even where it produces a non-canonical DACL, because simulating one is
how an operator discovers that an ACL their editor draws as "Deny wins" behaves the other way
round. Every entry is then renumbered contiguously; two entries claiming one position is an
ambiguity `normalize_acl` refuses and the access check would resolve by accident.

**Protecting a directory asks a question that has two answers.** Windows offers "convert
inherited permissions into explicit permissions" or "remove all inherited permissions", and
they produce genuinely different ACLs. `InheritanceChange` refuses to be constructed without
one of them. Clearing protection re-projects the parent's inheritable entries through
`project_inherited_acl` — the same function the live engine uses for a path nobody read — and,
where the parent was never read, flips the flag, projects nothing, and reports
`PARENT_NOT_OBSERVED`.

---

## 3. What is actually overlaid, and what deliberately is not

The overlay repositories override **every** public read of their base classes. Each one is
overlay-aware, plain delegation, or a refusal; nothing is inherited, because an inherited read
would answer from the wrong world with nothing to say so.
`tests/simulation/test_repository_coverage.py` asserts the three sets partition every public
read, so a method added to a base repository fails the suite until somebody decides which it is.

| Read | Treatment |
| --- | --- |
| `neighbors` | overlay: edges added and removed |
| `full_ntfs_acl`, `ntfs_acls_for` | overlay: entries added, modified, removed; inheritance applied; renumbered |
| `full_share_acl`, `share_acls_for` | overlay: the same for a share ACL |
| `get_ntfs_resource`, `ntfs_resources_by_keys` | overlay: `ace_count`, `dacl_protected`, `acl_hash` made consistent with the entries |
| `resources_named_by`, `shares_named_by` | overlay: the objects a simulated ACE newly names, merged into the keyset page |
| `get_principal`, `principals_by_keys` | delegated — a proposal does not create principals |
| `get_share`, `shares_by_keys` | delegated — nothing in an access check reads a share's descriptive fields |
| `keys_with_members` | **delegated, deliberately** — see below |
| everything else | refused (`SimulationUnsupportedRead`) |

**`keys_with_members` is the interesting one.** It answers *"has ADG ever enumerated the inside
of this group?"*, and it is what separates *the subject is not in it* from *nobody has looked*.
A proposal to add one member is not an enumeration. Letting a simulated edge satisfy it would
silently retire a `TRUSTEE_MEMBERSHIP_UNOBSERVED` finding — turning a coverage gap into a
verdict on the strength of a hypothetical, which is the exact failure the finding exists to
prevent.

**`ace_count` has to move with the entries.** The access check compares the count the
descriptor declared with the entries it was handed and raises `ACE_COUNT_MISMATCH` when they
disagree. Leaving the collected count in place would fire that finding on every simulated
resolution and bury the finding the simulation was run to produce.

---

## 4. Every invented row is marked

| Field | Value on a simulated record |
| --- | --- |
| `source_key` | `simulated` |
| `first_observed_run_id`, `last_observed_run_id` | the nil UUID |
| `first_observed_at`, `last_observed_at` | the epoch |
| `ace_key`, `edge_key` | the ordinary identity key, prefixed `simulated|` |

No collector can produce that combination: a collector always reports a real run at a real
instant. The constants are also what make a report *reproducible* — two runs over unchanged
data produce byte-identical records, so a difference between two reports means the proposal or
the estate changed and never that the clock moved.

---

## 5. What a report says

### 5.1 Applicability, before anything is simulated

Each change is checked against the object it names, and the overlay handed to the repositories
is narrowed to the changes that can be applied. That is the difference between *"this proposal
changes nothing"* and *"this proposal no longer applies"*, which are the same empty impact list
and completely different advice.

| Outcome | Meaning |
| --- | --- |
| `applied` | the simulated world differs from the baseline |
| `already_present` | an addition that is already there; Windows would merge, not duplicate |
| `target_not_found` | the ACE or edge named is not in the baseline |
| `target_not_observed` | ADG has no descriptor for the object, or has never read the share ACL |
| `parent_not_observed` | protection cleared with no parent read: applied, but the ACL is a lower bound |
| `not_representable` | editing one entry of a NULL DACL, which holds no entries at all |

### 5.2 The impact of each pair

| Direction | Meaning |
| --- | --- |
| `gained_access` | held nothing; holds something |
| `lost_access` | held something; holds nothing |
| `expanded` / `reduced` | strictly more / strictly fewer rights |
| `changed` | some gained and some lost — what a rewritten ACE looks like |
| `unchanged` | the same mask |

Masks are compared **expanded**: a DACL may carry generic rights, which Windows maps to
specific bits at open time, and subtracting the raw masks would report a change that does not
exist.

### 5.3 Caveats, drawn from the engine's own certainty

A simulation cannot be more certain than the answers it compares, and both answers come from
the same collected facts — so a coverage gap that made the live answer a bound makes the
simulated one a bound too, and the *claim* inherits it.

| Caveat | Raised when |
| --- | --- |
| `loss_may_not_hold` | rights reported removed, simulated answer is `at_least` or `uncertain` |
| `gain_may_not_hold` | rights reported granted, simulated answer is `at_most` or `uncertain` |
| `baseline_uncertain` / `simulated_uncertain` | either side is not `certain` |
| `alternate_path_retains_access` | a removal was proposed and another route still delivers |

`loss_may_not_hold` is the consequential one. It is what stands between a report and a
remediation that achieves nothing.

### 5.4 Alternate paths are measured, not inferred

When a proposal removes a route and rights actually go down, the *simulated* answer is
explained and its surviving grant paths are carried on the delta. Re-running the access check
is the only sound way to do this — ADR-0013 — because deleting an Allow can reveal a redundant
Allow behind it, and deleting a membership can remove a Deny as well as a grant. Only paths
that actually contribute are listed (`CausalPath.matters`): an Allow whose rights an earlier
entry had already settled is on the ACL and delivers nothing, and listing it as a surviving
route would be exactly the false reassurance the analysis exists to prevent.

### 5.5 The baseline, on every result

```
baseline: { kind: current | as_of, token: "a1b2…", run_id: "…", at: …, captured_at: … }
```

The token is the collection-basis digest (ADR-0017): it moves if and only if a collector has
written something. So *"this impact list was computed against facts that have since changed"*
is a fact rather than a guess, and an unchanged token is proof the simulation would compute the
same answer today.

---

## 6. Bounds, and why there is no job queue

The naive shape of a what-if is quadratic: every affected principal against every affected
resource, each pair costing a membership traversal. The affected scope inverts it exactly as
ADR-0012 does — per resource, expand the ACL's trustees downward **once** and invert the map —
so a resource costs two enumerations rather than two traversals per principal. Only the
principals that appear on one side and not the other are resolved individually, and those are
precisely the ones whose access changed.

| Bound | Default | Ceiling |
| --- | ---: | ---: |
| `max_principals` | 50 | 500 |
| `max_resources` | 50 | 500 |
| `max_pairs` | 500 | 5,000 |
| `max_explanations` | 25 | 100 |
| `time_budget_ms` | 10,000 | 60,000 |

Every exhausted bound appears in `SimulationReport.truncation` and makes `complete` false. The
time budget is checked between pairs and is a wall-clock deadline rather than a count, because
the cost of one pair depends on a membership graph whose shape cannot be predicted from the
proposal — a nesting depth nobody expected is exactly the case a count-based bound sails past.

### Measured cost

Against the `04-multiple-membership-paths` estate on the local stack, one proposal (remove
Alice from `Finance-Team`), measured with the statement counter from
`tests/db/test_query_cost.py`:

| Scope | SQL statements | edge-table reads | pairs | resolutions | explanations | elapsed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `pair` | 22 | 7 | 1 | 2 | 1 | 18 ms |
| `subject` | 22 | 7 | 1 | 2 | 0 | 18 ms |
| `resource` | 30 | 13 | 2 | 4 | 0 | 19 ms |
| `affected` | 35 | 15 | 2 | 4 | 0 | 24 ms |

These are small-estate figures and they are not a capacity claim — but they establish the
*shape*, which is what matters: the statement count is a function of the number of pairs and of
the depth of the membership graph, and of nothing that grows with the size of the estate. The
membership graph is walked one query per breadth-first level, and ACLs are read in bulk per
page. `tests/db/test_simulation.py::TestTheCostIsBounded` pins ceilings on both counts, so a
read that started growing with the estate would fail the suite.

**No asynchronous job architecture was built**, and the prompt's requirement for one is
conditional on broad simulations exceeding request limits. With the bounds above they do not: a
simulation either completes inside a request or says which bound stopped it, and a partial
answer that names its own truncation is more useful than a queue. What would change this:

* a scope over a whole share or a whole server, which is not an offered scope today;
* evaluating the subtree below a changed directory (§7, limitation 1), which multiplies the
  resource count by the depth of the tree;
* a bulk mode that evaluates many stored proposals against a new baseline after each scan.

Any of those needs a queue, a worker, a result store and a polling surface. None of them is
reachable from the scopes this phase offers.

---

## 7. What this phase does not do

1. **Descendants are not evaluated.** An inheritable ACE change, or an inheritance toggle,
   reaches every directory below the one it names. Only the directories the overlay implicates
   are evaluated, and the gap is reported as `DESCENDANTS_NOT_EVALUATED` rather than left to be
   discovered. Closing it needs a bounded descendant read and an inheritance projection down
   the tree.
2. **A share ACE change is evaluated at the share root only**, for the same reason, and reports
   the same truncation. A share ACL constrains every path under it.
3. ~~**No HTTP surface.**~~ **Closed by Phase 9B.** `SimulationService` and `SimulationStore`
   are now reachable over `/api/v1/simulations`, and the web application has a proposal
   editor, an impact view and an export — see
   [`simulation-surface.md`](simulation-surface.md), which also carries the controlled
   equivalence validation that holds a prediction against a recollected estate.
4. **Changes to unobserved objects cannot be applied.** A directory with no descriptor, and a
   share whose ACL nobody has read, both come back `target_not_observed` — because the
   alternative is inventing the state the change was meant to modify.
5. **Owner changes are not modeled.** An object's owner holds `WRITE_DAC` implicitly, so
   changing it changes access; it is not in the overlay vocabulary.
6. **A proposal is not validated against Windows' own rules for what an administrator may do.**
   ADG will happily simulate an ACL an operator has no permission to set. Whether the change is
   *permitted* is a different question from what it would *do*.

---

## 8. Storage

Two tables, and nothing else in ADG reads either of them.

| Table | Holds |
| --- | --- |
| `simulations` | the overlay as JSONB with its own `document_version`, the overlay digest, and the baseline it was written against |
| `simulation_evaluations` | one row per run: the scope, the basis token at the time, whether the baseline had already moved, and the compact report |

The overlay is a blob rather than four per-kind change tables because a change is only ever
read back as part of a whole overlay — nothing asks *"which proposals touch this ACE"* — and
four tables would be four places for validation that already lives, exactly once, in the
overlay's constructors. It is rebuilt through those constructors on the way out, so a stored
proposal that today's rules would refuse is refused rather than simulated under rules it was
never validated against.

`simulation_evaluations` carries a foreign key, which the resource tables deliberately do not.
That rule is about *observations*: a share whose server no run has described is a real reading,
and a constraint would reject it at the moment a partial scan most needs to record what it
managed to read. An evaluation is written by this application, in one transaction, after the
proposal it belongs to; an orphan is a defect rather than a partial scan.

Re-running a proposal **adds** an evaluation rather than replacing one. *"This change was safe
on Monday and takes access away today"* is the sentence the history of a proposal exists to
make available, and overwriting keeps only its second half.

---

## 9. How the isolation is guaranteed

Not by discipline. Four independent mechanisms, each testable:

1. **Applying an overlay is a pure function over frozen records.** Inputs are not mutated and
   outputs are freshly constructed — `tests/simulation/test_application.py::TestNothingIsMutated`.
2. **The overlay repositories have no write methods**, and neither do the repositories they
   subclass — `test_repository_coverage.py::test_the_overlay_repositories_expose_no_write_methods`.
3. **The store touches two tables.** Every collected-state table is digested before and after a
   proposal is evaluated *and stored*, and the digests must be identical —
   `tests/db/test_simulation.py::TestNothingIsMutated`.
4. **No simulated row can be mistaken for a collected one**, because every one is marked (§4),
   and a query looks for the marking in `ntfs_aces`, `smb_share_aces` and `membership_edges`
   after a proposal that invents entries.

Collectors are untouched. No collector file was modified by this phase, no new permission is
required, and nothing here asks a collector for anything. ADG remains read-only against Windows
(ADR-0004, `SECURITY.md`).
