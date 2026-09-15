# The what-if surface: the API, the screens, and the equivalence proof

Phase 9A built the engine and deliberately left it with no route
([`simulation.md`](simulation.md)). This document is Phase 9B: the HTTP surface over it, the
screens in front of that, and — the part that makes the rest worth trusting — the controlled
validation that holds a simulation's prediction against what actually happens to a real
estate.

Read `simulation.md` first. It says what a proposal is, what is overlaid, how a report is
bounded, and why nothing is mutated. This document assumes all of it.

---

## 1. The three questions Phase 9A left open, and their answers

They are stated and argued in **ADR-0034**; here is the shape.

| Question | Answer |
| --- | --- |
| Compute-and-return, or store-and-read-back? | **Both, as two routes.** `POST /preview` keeps nothing; `POST /` stores the proposal *and* its first evaluation in one transaction. Same body, different path. |
| Which capability? | **Its own pair.** `simulations:read` to read a stored proposal; `simulations:run` to compute, store or delete one. A plain `viewer` holds neither. |
| Stale baseline: warning or `409`? | **A field**, on every response that could carry one. Never a refusal. |

---

## 2. The routes

All under `/api/v1/simulations`, all declared in `app/api/simulations.py`, all listed in
`tests/api/test_authorization.py::ROUTE_CAPABILITIES`.

| Method | Path | Capability | What it does |
| --- | --- | --- | --- |
| `GET` | `/vocabulary` | `simulations:read` | Every closed vocabulary a report speaks, with its wording |
| `POST` | `/preview` | `simulations:run` | Evaluate a proposal and **store nothing** |
| `POST` | `` | `simulations:run` | Evaluate a proposal and store it with its first result |
| `GET` | `` | `simulations:read` | Stored proposals, newest first, keyset-paged |
| `GET` | `/{id}` | `simulations:read` | One proposal, its baseline, its evaluation history |
| `POST` | `/{id}/evaluations` | `simulations:run` | Run it again; **adds** a result |
| `GET` | `/{id}/evaluations` | `simulations:read` | Every run of one proposal |
| `GET` | `/{id}/export` | `simulations:read` | Plan + result + vocabulary, one document |
| `DELETE` | `/{id}` | `simulations:run` | Remove a proposal and its evaluations |

**No route writes to Windows.** There is no code path from any handler to Active Directory, a
share, or an NTFS descriptor: the handlers call `SimulationService`, which reads through
overlay repositories that expose no write method, and `SimulationStore`, which reaches two
tables nothing else in ADG reads.
`tests/db/test_simulations_api.py::TestNothingIsMutated` digests every collected-state table
around the whole route sweep — preview, store, re-evaluate, export, delete — and the digest
must not move.

### The two fields every payload carries

```json
{
  "notice": "NO CHANGES WILL BE APPLIED. This is a simulation: …",
  "applied": false
}
```

`notice` is `app.simulation.describe.NON_DESTRUCTIVE_NOTICE`, one string, served rather than
written into each client. `applied` is a literal `false` — a field rather than prose, so a
client and a test can assert on it.

### Validation is the domain's, not the model's

The request models check shapes: a `kind` from a closed list, a mask in range, a key that is
not empty. Everything else — a removal must name the entry it removes, protecting a directory
must say what happens to the entries it inherits, a share entry carries a mask *or* a
permission level and never both — is enforced by the overlay's own constructors and surfaces
as a `422` carrying the domain's sentence and the field it names.

A second copy of those rules in a Pydantic model, or in the browser, would be the more lenient
copy, and it would contradict a refusal the API had already decided on.

---

## 3. The screens

| Path | What it is |
| --- | --- |
| `/simulations` | Stored proposals. Leads with the notice, because this is where somebody lands with no idea what the section does. |
| `/simulations/new` | The proposal editor, seeded from a `Simulate` action. |
| `/simulations/{id}` | One proposal: what it says, what it was measured against, what it was found to do, every run, and the structured export. |

### `Simulate` is a link, from four places

A `Simulate` action is a plain `<a>` to `/simulations/new?kind=…&…`, never a button that
posts. Three consequences, and the third is the one that matters: the resulting page is
addressable and pasteable into a ticket; it works without JavaScript; and **nothing can happen
by pressing it**. An action that looks like it might change something is exactly what this
phase's first acceptance criterion is about.

It is offered from:

* **membership views** — `MembershipTable` on the principal page, both directions, seeding
  `remove_member` with the group and member the right way round;
* **SMB ACE rows** — `ShareAceTable`, seeding `remove_share_ace` with that entry's key;
* **NTFS ACE rows** — `NtfsAceTable`, seeding `remove_ntfs_ace`;
* **access explanation paths** — the removal table on `/access/explain`, which has already
  measured what the removal does *for this one pair* and hands off the wider question: who
  **else** does it touch, across every directory the group reaches.

Each of those tables renders the column only when it can name the object exactly. A row that
could not say which directory or which end of an edge it came from offers nothing, because a
seed built from a guess would propose a different change from the one on screen.

### The editor edits a change document

Nine change kinds, each with its own required fields and its own rules. A form with nine
conditional layouts would be a second copy of those rules written in TypeScript, it would be
the lenient copy, and it would let somebody submit a proposal the API then refuses with a
message the form had already contradicted. So the editor edits the change document, seeds it
from wherever the operator came from, and lets the domain be the validator — and renders the
API's vocabulary beside it rather than its own.

### What the impact view is careful about

* **The notice is first**, `role="alert"`, not dismissible, and carries the API's sentence.
* **What became of each change is rendered above the impact list.** An empty impact list means
  something completely different depending on it, and a reader who stops at the counts must
  have seen it.
* **`inert` is a separate state from "changed nothing".** A proposal that is safe and one
  whose targets have gone produce the same empty list and are completely different advice.
* **A caveat is rendered beside the row it qualifies**, in the API's own words, as a warning.
  `loss_may_not_hold` is the field that stands between a report and a remediation that
  achieves nothing; a UI that rendered it as a small grey icon would have thrown the phase
  away.
* **A surviving route is named, not counted.** `Alice → Finance-Team → Domain Users (ntfs,
  0x001200A9)` is the sentence that stops "remove this group" being signed off as a revocation.
* **Unchanged rows are kept.** They are the evidence that somebody was looked at and found
  unaffected, which is not the same as their not having been evaluated.
* **A truncation is an alert above the list**, not a footnote below it.
* **Nothing in the browser computes anything about rights.** Every mask, direction and count
  comes from the engine. A browser-side second opinion could only ever disagree with the
  answer this product exists to give.

### The export

`GET /{id}/export` returns the plan, the result **verbatim as stored**, and the vocabulary —
self-describing, so a file read six months later by somebody without this API in front of them
still says what `loss_may_not_hold` meant. The screen also offers the same report as text for
a change ticket, and the text export's **first line** is the non-destructive notice: a report
read later must not be mistaken for a record of a change that was made. Every caveat and every
truncation travels with the conclusion.

---

## 4. Equivalence validation: does a prediction match reality?

Phase 9A checked a simulation against the engine that produced it, which proves consistency and
nothing about correctness. `tests/db/test_simulation_equivalence.py`, over the apparatus in
`tests/support/equivalence.py`, checks it against the estate. Five steps, all real:

1. **A known permission state**, built as a complete scan-run transcript and **collected**
   through the ingestion API — one share, one directory, two users, two groups, both ACLs.
2. **A simulation** of a proposed change, run by the production `SimulationService`.
3. **The equivalent change, applied fixture-side** — the observation set is edited the way
   Windows would have changed the object. ACE identity keys and the descriptor's `ace_count`
   are recomputed by the domain's own functions, so an edit cannot produce a transcript no
   collector would have sent.
4. **A recollection**, as **three** reconciling runs — an `active_directory` run reconciling
   its domain, an `smb` run reconciling its server, an `ntfs` run reconciling its directory
   tree. Three, because reconciliation is keyed by `(collector, scope kind)`
   (`app/history/closure.py`): one run claiming all three scopes would be a collector marking
   absent what it is structurally incapable of seeing, and ADG refuses to infer absence from
   it. Without the split, a removal is never *measured* as a removal and the comparison cannot
   see one.
5. **The comparison**, over the pairs the simulation actually spoke about.

### What is covered

| Change | Case |
| --- | --- |
| `remove_member` | Alice loses access; measured |
| `add_member` | Carol gains exactly Modify; measured |
| `add_ntfs_ace` | Bob expands to Full Control |
| `remove_ntfs_ace` | Bob loses access |
| `modify_ntfs_ace` | Modify narrowed to Read & Execute — not a subtraction, a different mask |
| `add_ntfs_ace` (Deny) | A Deny added to a DACL is evaluated first and wins |
| `modify_share_ace` | The share cap applies over the network… |
| `modify_share_ace`, local path | …and does not apply on the console |
| `set_inheritance` | Both dialog-box answers, on a share root with no parent |

Plus two controls on the harness itself: recollecting an unchanged estate moves no answer, and
the two readings below agree while nothing has been removed.

### The one exception, measured rather than asserted

The post-change answer is read **two ways**, and reporting both is the point:

* **`as_of`** — the point-in-time engine at the recollection instant, which routes every read
  through `object_versions` and therefore excludes what a reconciled scan proved gone. **This
  is the reference**, and every case above agrees with it.
* **`live`** — the ordinary current-state engine. ADG deletes nothing on ingestion (an absent
  observation is not evidence of removal) and current-state reads are not routed through
  presence, so a live answer still counts a grant a reconciled scan has proved gone.

So every *removal* case agrees with the as-of reading and disagrees with the live one, and
`TestTheKnownExceptionIsMeasured` pins that divergence with exact masks — including its
consequence for applicability: a proposal naming an ACE a reconciled scan has proved gone is
still reported as `applied` against a *current* baseline, with an impact list computed from a
grant that no longer exists. That is the one case where the report is confidently wrong rather
than merely bounded, and the workaround is one field: an `as_of` baseline is presence-routed
and answers correctly. The test asserts both halves, so the fix is documented by a passing
test rather than by prose.

This is Phase 7A's limitation 1 and Phase 9A's limitation 6. It belongs in a test rather than
only in a document: the day somebody routes current-state reads through presence, this suite
tells them the limitation is gone.

### What the validation does not cover

* **Descendants.** `simulation.md` §7.1 is untouched by this phase; the fixture estate is one
  directory, and an inheritable change reaching a subtree is still reported as truncation
  rather than measured.
* **A real Windows host.** The "observed" side is ADG's own recollection of an edited
  observation set, not `AuthzAccessCheck` against a live file server. What that recollection
  *means* is validated separately, against Windows, by Phase 4C's harness
  (`scripts/windows-access-check`), and the two together are what make the chain
  proposal → prediction → observation trustworthy.
* **Owner changes**, which are not in the overlay vocabulary at all.
