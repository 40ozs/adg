# ADR-0034: A simulation is offered, never applied — and its capability is its own

**Status:** Accepted
**Date:** 2026-09-14
**Phase:** 9B

## Context

Phase 9A built a what-if engine with no HTTP surface and left three questions open. Phase 9B
had to answer them, and each answer is a decision that is hard to reverse once a client is
written against it.

1. **Is a simulation a `POST` that computes and returns, or a `POST` that stores and a `GET`
   that reads back?**
2. **Which capability does it require?** A simulation discloses *potential* access, which is
   at least as sensitive as the answers behind `access:read` — and possibly more.
3. **Is a stale baseline a warning field or a `409`?**

Behind all three sits the phase's first acceptance criterion: *the UI must make the
non-destructive nature unmistakable*. ADG is read-only against Windows (ADR-0004), and this is
the first screen in the product that proposes a permission change at all. A user who believes
a button on it might edit an ACL will either not press it — losing the feature — or press it
believing something happened.

## Decision

### Both, as two routes, and the difference is what is stored

`POST /api/v1/simulations/preview` computes an answer and keeps nothing. It is what the
proposal editor calls while somebody is still typing, and a draft that accumulated a row on
every keystroke would turn an editing session into an audit trail of half-formed ideas.

`POST /api/v1/simulations` takes the same body plus a name, and stores the proposal *and* its
first evaluation in one transaction. Promoting a draft is therefore the same request to a
different path.

Re-running a stored proposal (`POST /api/v1/simulations/{id}/evaluations`) **adds** a result
rather than replacing one, because two evaluations against two collection states are two
findings.

### Two capabilities of its own, and a plain viewer holds neither

`simulations:read` reads a stored proposal and its result. `simulations:run` computes one,
stores one, and deletes one.

**Not `access:read`, and not held by `viewer`.** A viewer can already ask who reaches a share
and what a group contains. A simulation *composes* those into "put this account in that group
and it reaches the payroll share" — a route map for privilege escalation assembled out of
answers a reader would otherwise have to compose by hand. That composition is what makes it a
wider disclosure than either half, so it starts at `auditor`.

**The read and the run are separate** for a reason that is not disclosure at all: resolving a
proposal's affected scope is the most expensive request this API serves. An account that may
read what somebody already ran must not thereby be able to make the estate resolve a thousand
new pairs — the same reason `GET /api/v1/risks/*` cannot start a risk evaluation.

`admin` and `governance_admin` hold `simulations:run`: the first plans permission changes, and
the second runs the reviews that conclude a change is needed. An `auditor` holds only the read,
because an auditor's question is whether a proposed change was evaluated before it was signed
off, and the stored report is the evidence.

### A stale baseline is a field, never a refusal

Every response that could carry one reports `stale`, the baseline token, and the current token.
A `409` would withhold the report an operator stored precisely so they could look at it again,
in exchange for telling them something a boolean says better — and re-running against the new
state is one `POST` to a route that already exists.

### Nothing is applied, and the sentence saying so is served by the API

`app.simulation.describe.NON_DESTRUCTIVE_NOTICE` is one string, rendered on every simulation
payload — the report, the listing, the detail, the export — and on every screen. `applied:
false` is a literal field beside it rather than prose, so a client and a test can assert on it.

Three surfaces render a simulation. A notice each of them worded its own way is a notice one of
them will eventually soften, and the one that gets softened will be the one somebody reads.

## Consequences

**Good.**

* A proposal can be typed, evaluated, discarded, and leave nothing behind.
* The one row this feature writes is ADG's own record of a question somebody asked. No route
  from any handler reaches Active Directory, a share, or an NTFS descriptor, and
  `tests/db/test_simulations_api.py` digests every collected-state table around the whole
  route sweep to prove the second half of that.
* The non-destructive claim is testable rather than typographical.
* A stored proposal ages honestly: the estate moves, the proposal says so, and re-running it
  produces a second finding instead of overwriting the first.

**Costs, accepted.**

* A plain viewer cannot see the what-if section at all. That is a real loss of usefulness, and
  it is the right side to err on for a disclosure that composes two answers into a third.
* Two capabilities is two more rows in the role table, and an operator who wants an account
  that can run simulations has to grant a role that carries other things with it. A role
  dedicated to simulations was considered and rejected as premature.
* The preview route can be called repeatedly and each call is expensive. The bounds
  (`SimulationBounds`, ADR-0020's §6 figures) are what keep that from being a denial of
  service, and they are capability-gated behind `simulations:run` besides.

## Alternatives rejected

**One route that stores everything.** Simpler, and it would make an editing session into a
permanent record of every half-formed idea somebody typed — including proposals naming
accounts they were curious about.

**`access:read` for the whole feature.** Defensible: every fact a simulation reads is one the
capability already discloses. Rejected because the composition is the disclosure, and because
it would have made the run — the expensive operation — reachable by every account that can look
at a share.

**A `409` for a stale baseline.** It would force a re-run, which is the action we want. It
would also refuse to show an operator the report they stored, which is the action they asked
for, and it would make "the facts have moved" indistinguishable from "this proposal is wrong".

**Wording the notice in each client.** Rejected outright. See above.

## References

* ADR-0004 — ADG is read-only against Windows.
* ADR-0014 — authorization is a capability check declared once per router.
* ADR-0020 — a simulation is the production engine reading through an overlay.
* ADR-0024 — sensitivity is declared, not inferred (the `sensitive` flag on an impacted
  directory).
* `docs/architecture/simulation.md` §8 (the HTTP surface) and §9 (equivalence validation).
* `docs/handoffs/phase-09b-simulation-ui.md`.
