# ADR-0017: A derived answer is cached against collected state, never against a clock

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 5B (`phase-05/02-explanation-api.md`)
- **Deciders:** Phase 5B implementation

## Context

An access explanation is expensive relative to the rest of the API — a bounded membership
traversal, two ACL reads, an access check, path enumeration, and one re-evaluation per removal
target — and it is the response a user interface holds open and re-requests most often. It is
the obvious thing to cache.

It is also the response where staleness is least acceptable. It names the group to join and
the ACE to edit to reach a share; an administrator acts on it and then re-reads it to confirm
the change. An explanation served from before the change they just made is worse than no
caching at all, because it tells them their remediation did not work.

The reflex is a short time-to-live. A TTL makes the wrong promise in both directions:

- a ten-second TTL still serves an explanation from before the membership change that just
  landed, and
- it discards a perfectly valid explanation when no collector has run for a week.

Neither has anything to do with whether the answer is current. What decides that is exactly
one thing: whether a collector has written anything since.

ADG can answer that precisely. Every stored fact belongs to exactly one scan run (ADR-0003),
every fact is written through ingestion, and ingestion cannot write a fact without touching
that run's row — a batch bumps `observation_count_applied` and `updated_at`, completion sets
the status, reconciliation happens inside a completion. The state of `scan_runs` is therefore
a complete summary of the state of everything derived from it.

## Decision

**Derived responses are cached by revalidation against a collection basis, and no derived
response carries a freshness lifetime.**

1. `app.domain.basis.CollectionBasis` summarizes every scan run — count, latest activity,
   observations applied, batches received — and digests to a short `token`. Equal tokens mean
   identical stored facts.
2. `app.api.caching.validator_for` builds a strong `ETag` over *(contract version, basis
   token, the exact request)*. Every input that can change the body is in the key, including
   the **clamped** limits rather than what the caller asked for.
3. Responses carry `Cache-Control: private, no-cache`. Store it; never serve it without
   revalidating; never in a shared cache. **No `max-age`, ever.**
4. `If-None-Match` yields `304`, and the validator is computed **before** the expensive work,
   so a revalidation costs one aggregate query.
5. The basis is **estate-wide**, not per-row provenance. A narrower key would invalidate less
   often and would also be wrong: a run that adds the first membership edge for a group an ACL
   names changes an answer that read no row belonging to that group.
6. **No in-process response cache.** The conditional GET already reduces a repeat to one
   query; a process-wide cache keyed by principal and resource is one forgotten key component
   away from serving one caller's answer to another, and it would need invalidating — the bug
   class this decision exists to avoid.
7. The contract version is part of the validator, so a client holding a `1.0` body cannot be
   handed a `304` by a server that now speaks `1.1`.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Short TTL (`max-age=30`) | Wrong in both directions, as above. Serves a stale explanation right after a remediation, and discards a valid one on a quiet estate. |
| Per-row provenance as the key (`last_observed_run_id` of the rows read) | Precise about the rows an answer read and silent about the rows it *would* have read had they existed. Also not computable before the work, so it could not make a 304 cheap. |
| `Last-Modified` instead of an `ETag` | One-second granularity, and two runs finishing in the same second are two different states. The basis also covers request parameters, which a timestamp cannot. |
| Server-side memoization keyed by the basis | Saves nothing the 304 path does not already save, and introduces a shared cache holding one estate's reconnaissance map keyed without the caller's capabilities. If a later phase needs it, the basis token is the right key and the capability set must be part of it. |
| Weak ETag | Unnecessary. The body is byte-identical for a repeated request over an unchanged basis: the engine is deterministic and every listing it produces is deterministically ordered (Phase 5A). |

## Consequences

**Positive**

- A cached explanation can never be stale, and can be held indefinitely on a quiet estate.
- An ingestion invalidates every cached derived answer the moment it lands, with nothing to
  purge and no invalidation logic to get wrong.
- `basis` in the body lets a client say "as of the run that finished at 02:14" instead of "as
  of now", and lets two answers be compared honestly.
- One aggregate query per derived request, measured rather than assumed.

**Negative / accepted costs**

- Every derived request pays one extra query over `scan_runs`, including requests that go on
  to do the full work. Measured at exactly one statement.
- The basis is coarse: any ingestion anywhere invalidates every cached answer, including ones
  it could not have changed. Deliberate — the alternative is a key that is precise and wrong.
- A batch that re-applies an idempotent payload and changes no row still moves the basis. The
  safe direction: a basis that moves too often costs a recomputation; one that moves too
  rarely serves a stale answer as a fresh one.

**Follow-up required**

- Every new derived endpoint calls `app.api.caching.current_validator` rather than assembling
  its own key, so they all invalidate on the same event.
- Bump `CONTRACT_VERSION` whenever the shape of a derived response changes.

## Compliance

`backend/tests/api/test_caching.py` asserts that each request component changes the tag, that
a moved basis changes it, and that `Cache-Control` carries `private` and `no-cache` and no
`max-age`. `backend/tests/domain/test_basis.py` asserts every basis field is in the digest and
that the same instant digests identically across time zones.
`backend/tests/db/test_explanation_api.py` proves the end-to-end claim — a repeat is `304`, a
request after an ingestion is not — and measures the cost: a revalidation issues exactly one
statement, and the validator adds exactly one to a full answer.
