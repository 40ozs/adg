# ADR-0019: A change is recorded as a window, not an instant

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 7A — historical observation and snapshot model
- **Deciders:** Phase 7A implementation

## Context

A collector **samples**; it does not watch. If Monday's scan read an ACL and Friday's scan
read a different one, the permission changed somewhere between them and nothing in ADG knows
where. Windows recorded the change in a security log ADG does not collect; the collector saw
two readings.

The usual temporal model gives a version two timestamps, `valid_from` and `valid_to`, and has
nowhere to put that ignorance. Filling `valid_to` with the instant of the contradicting
observation makes the record say *the change happened on Friday*, which is merely the day
somebody looked. An auditor reading that will date an incident to a scan schedule.

This matters more here than in an ordinary temporal database, because ADG's answers are
quoted in findings. ADR-0011 already established that an effective-access answer carries the
conditions that qualify it; a historical answer needs the same treatment, and the thing
qualifying it is *when somebody last looked*.

## Decision

A version carries **three** timestamps, not two:

| Column | Meaning |
| --- | --- |
| `valid_from` | the observation that first showed this state |
| `last_seen_at` | the newest observation that **confirmed** it |
| `valid_to` | the observation that **contradicted** it |

The honest statement about a transition is therefore available and is what the model reports:

> the state ended somewhere in `(last_seen_at, valid_to]`

exposed as `ObjectVersion.change_window`. The same relation bounds the *start* of the next
version, since one version's `last_seen_at` is the lower bound on its successor's beginning
(`ObjectTimeline.opened_window`). When two observations bracket a change exactly, the window
collapses to a point and `ChangeWindow.is_exact` says so.

Every point-in-time answer reports a **certainty** derived from where the asked-about instant
falls:

* `observed` — between the version's first and last confirmation; the state was watched then.
* `inferred` — after the last confirmation; the best available answer, and not a watched one.
* `backfilled` — the version was reconstructed by the Phase 7 migration from a pre-history
  row, which kept no evidence of intermediate states either way.
* `unobserved` — no version covers the instant. **Never rendered as "it did not exist."**

An answer assembled from many versions — an effective-access resolution reads a token's worth
of membership edges plus two ACLs — carries the **weakest** certainty of any input, counted
by `VersionAudit` at the point of the read rather than threaded through the engine.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Two timestamps; `valid_to` is the contradicting observation | Dates every change to a scan schedule. An auditor reading "changed Friday 09:00" about an estate scanned Fridays at 09:00 has been told the scan time, not the change time |
| Two timestamps; `valid_to` is the *previous* confirmation | Errs the other way and leaves a hole: the interval between the old state's last confirmation and the new state's first sighting would belong to no version at all, and "what was true on Thursday" would answer `unobserved` for an object under continuous observation |
| Interpolate — put the boundary at the midpoint of the window | Invents a number. The midpoint is not evidence of anything, and it would be indistinguishable in the schema from an instant somebody actually observed |
| Keep the window only in the API layer, not the schema | The lower bound is `last_seen_at`, which has to be stored per version to exist at all. Storing it and not exposing it would be the same work with the honesty removed |
| One certainty for the whole answer, derived from the request instant | Wrong in both directions: an instant inside every input's observed span is fully observed, and an instant inside one input's change window is not, and only the inputs know which |

## Consequences

**Positive**

- A transition is reported as what is actually known about it, with its own uncertainty
  attached, rather than as a false precision.
- `last_seen_at` doubles as the mechanism for two other rules: re-observing an unchanged
  object extends a version rather than creating one, and a stale run cannot tombstone an
  object something newer has just confirmed (`last_seen_at <= completed_at`).
- Retention has a principled floor: the newest closed version of an object can never be
  pruned, because it is what bounds the open version's beginning.

**Negative / accepted costs**

- A third timestamp on every version, and an update to it on every confirming observation —
  a write on a scan that changed nothing. That cost buys the distinction between `observed`
  and `inferred`, which is the only thing that makes an `inferred` answer readable as a
  qualified one.
- Consumers have to render two instants where one would be simpler, and a UI that shows only
  `valid_to` silently reverts to the rejected model.

**Follow-up required**

- Any Phase 7B API response for a transition must carry both ends of the window, not a single
  `changed_at`.

## Compliance

- `tests/history/test_model.py::TestTheChangeWindow` — the window excludes the last
  confirmation and includes the contradiction, and collapses to a point when two observations
  bracket a change exactly.
- `tests/history/test_model.py::TestCertainty` — the four values, including that a backfilled
  version never reports more than `backfilled`.
- `tests/db/test_history_versions.py::test_the_change_window_is_the_interval_not_the_instant_somebody_looked`
  — end to end, through the real ingestion endpoints.
- `tests/db/test_history_queries.py` — an answer between two confirmations is `observed`; the
  same answer in the window after the last one is `inferred`.
