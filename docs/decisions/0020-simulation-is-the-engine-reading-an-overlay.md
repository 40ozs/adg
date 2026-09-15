# ADR-0020: A simulation is the production engine reading through an overlay

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 9A (`phase-09/01-simulation-engine.md`)
- **Deciders:** Phase 9A implementation

> **Numbering note.** Phases 8 and 9 were implemented concurrently in one working tree. If
> another ADR was accepted as 0020 in the same window, one of the two needs renumbering; the
> content of this record does not depend on the number.

## Context

*"If I take this group off the ACL, who loses access?"* is the question an administrator asks
before every permission change, and the honest answer is almost never the obvious one. An
alternate group membership keeps the access. A Deny earlier in the DACL meant the entry was
never granting anything. A share ACL in front of NTFS was the real limit all along. Phase 5A
already measures that for *existing* state — ADR-0013 established that a removal's effect is
computed by re-running the access check, never inferred — and Phase 9A extends the same
question to state that does not exist yet.

The engine that answers it is the most safety-critical code in the product. It is roughly
three thousand lines across `app/access_engine` and `app/services/access.py`: a faithful
Windows access check in stored DACL order (ADR-0010), a token built from a named assumption,
a two-layer crossing, an inheritance projection for paths nobody read, a coverage-finding
vocabulary that keeps a gap from becoming a verdict (ADR-0011), and a bounded inversion that
makes "who can reach this" answerable at all (ADR-0012).

Three ways to build a what-if were available.

1. **A separate calculator.** Small, fast, and easy to reason about — and a second
   implementation of the access check. The first time the two disagreed, the tool would be
   telling somebody that removing a group is safe on the authority of code that has never
   answered a real question, and nobody would know which answer to believe.
2. **Parameterize the engine.** Thread an overlay through `AccessService`, the resolver and
   the causality module. Every one of those modules would then have a branch that only runs in
   simulation, which is the branch nobody exercises in production and nobody notices breaking.
3. **Substitute the inputs.** Phase 7A already established that the engine takes its two
   repositories by injection and that replacing them with as-of subclasses makes the whole
   engine answer about a past instant with no change to the engine (ADR-0018's consequence).
   A hypothetical instant is the same shape of substitution.

A fourth constraint applied regardless: the result has to say what state it was measured
against. An impact list with no baseline is unreviewable, and *"has this gone stale?"* has to
be answerable as a fact rather than guessed from a clock — which ADR-0017 had already solved
for caching, with the collection-basis token that moves if and only if a collector has written
something.

## Decision

**A simulation is the production engine, unmodified, reading through an overlay applied at the
repository boundary.**

1. **The overlay is an immutable value.** `app.simulation.SimulationOverlay` holds add/remove
   membership edges, add/modify/remove NTFS and SMB ACEs, and inheritance-protection toggles.
   It has no method that writes anything, and it names its targets rather than carrying copies
   of the rows it acts on — so a proposal written on Monday is evaluated against Friday's ACL
   and reports that a change no longer applies rather than re-creating a row that has gone.

2. **`OverlayResourceRepository` and `OverlayMembershipRepository` wrap a baseline pair.**
   They are subclasses of the repositories `AccessService` already takes, so the engine's type
   contract is satisfied, and they *delegate* rather than inherit, so the baseline may itself
   be a Phase 7A as-of repository. Every public read is overlay-aware, plain delegation, or a
   refusal; nothing is inherited, because an inherited read here would answer from the wrong
   world. `tests/simulation/test_repository_coverage.py` asserts the three sets partition every
   public read.

3. **Applying an overlay is a pure function over frozen records.** Inputs are not mutated and
   every invented record is marked — `source_key = "simulated"`, the nil run id, the epoch as
   its observation timestamps, and a `simulated|` prefix on invented ACE and edge keys. The
   marking is deterministic, which is why two runs over unchanged data produce byte-identical
   reports.

4. **Applicability is decided before anything is simulated**, and the overlay handed to the
   repositories is narrowed to the changes that can be applied. So "this proposal changes
   nothing" and "this proposal no longer applies" — the same empty impact list — are different
   answers.

5. **Impact is a comparison of two engine answers**, and it inherits their certainty.
   `AccessCertainty.AT_LEAST` on the simulated side raises `LOSS_MAY_NOT_HOLD`; `AT_MOST`
   raises `GAIN_MAY_NOT_HOLD`. Alternate paths are enumerated from the *simulated* explanation
   — measured, as ADR-0013 requires, never inferred from which route looked important.

6. **Every report carries a `SimulationBaseline`**: the collection-basis token, the latest run
   id, the instant for an as-of baseline, and the moment of capture. Staleness is that token
   compared with a freshly read one.

7. **Proposals are persisted in `simulations` and `simulation_evaluations` and nowhere else.**
   No collector, ingestion path or effective-access query reads either table, so a simulated
   ACE cannot be picked up by a query against `ntfs_aces` — because none was ever written
   there.

8. **Work is bounded and the bound is reported.** `SimulationBounds` caps affected principals,
   affected resources, evaluated pairs, alternate-path analyses, and wall-clock time; every
   exhausted bound appears in `SimulationReport.truncation` and makes `complete` false.

## Consequences

**The simulated answer cannot drift from the live one**, because there is only one answer
function. A fix to the access check fixes both; a bug in it is visible in both.

**A simulation over history is free.** `SimulationService.run(overlay, baseline=as_of)` wraps
the Phase 7A repositories, so *"would this change have helped on Tuesday?"* is the same code
path with a different baseline.

**An overlay repository is not substitutable for a real one.** Reads outside the simulated
surface raise `SimulationUnsupportedRead` rather than answering. That is deliberate and it is
the opposite of Phase 7A's choice: an inherited read on an as-of repository returns today's
facts, which is wrong but recognizably so, while here it would return production rows under a
simulation's name with nothing to tell them apart.

**Cost is two resolutions per pair, plus one per alternate-path analysis.** The naive shape —
every affected principal against every affected resource — is quadratic, so the affected scope
inverts it exactly as ADR-0012 does: per resource, expand the ACL's trustees downward once,
and resolve individually only the principals that appear on one side and not the other. Those
are precisely the principals whose access changed.

**A coverage gap stays a coverage gap.** `keys_with_members` is *not* overlay-aware: a proposal
to add one member is not an enumeration, and letting a simulated edge satisfy it would retire a
`TRUSTEE_MEMBERSHIP_UNOBSERVED` finding on the strength of a hypothetical.

**Changes to an object nobody has read cannot be simulated.** A directory with no descriptor
and a share ACL nobody has collected both come back `TARGET_NOT_OBSERVED`. The alternative —
inventing the state the change was meant to modify — would answer a question about an object
ADG has never looked at.

**Descendants are not evaluated.** An inheritable ACE change, or an inheritance toggle, reaches
every directory below the one it names, and this phase evaluates only the directories the
overlay implicates. The gap is reported as `DESCENDANTS_NOT_EVALUATED` rather than left to be
discovered.

## Alternatives rejected

**A separate simulation calculator.** Rejected for the reason in *Context*: two
implementations of the access check, and no way to tell which is right on the day they
disagree.

**Parameterizing the engine with an optional overlay.** Rejected because the branch would run
only in simulation. The repository seam already exists, is already used this way by Phase 7A,
and requires no change to code that answers production questions.

**Materializing the simulated state into scratch tables and querying it.** Rejected outright.
It puts rows that no collector produced into the database, which is the one thing a
non-destructive simulation must not do, and it would make the isolation guarantee depend on a
cleanup path running.

**Storing the full derivation of every delta.** Rejected. The overlay, the baseline and the
scope are stored, and every simulated record is built from constants, so a report is
reproducible exactly. A stored copy would be a second account of the same answer, ageing
independently of the code that computes it.

**A job queue for broad simulations.** Not built. With the bounds in place a simulation
completes inside a request and says when it was cut short; an asynchronous architecture would
add a queue, a worker, a result store and a polling surface to solve a problem the bounds
already solve. See `docs/architecture/simulation.md` §6 for the measured figures and for what
would have to become true to change this.
