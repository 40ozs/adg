# ADR-0015: An empty view is attributed by the backend, never inferred from a row count

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 6A (`phase-06/01-frontend-shell-auth.md`)
- **Deciders:** Phase 6A implementation

## Context

An ADG table with nothing in it means one of several completely different things:

- nothing is there, and collection is current;
- nothing is there, but a collector failed, so nobody looked at that part of the estate;
- a scan is still running;
- the request failed;
- the account is not permitted to see this.

Only the first is an answer. Every other case is *unknown*, and rendering unknown as "none"
is the specific failure this product exists to prevent. An auditor who reads "no risky
permissions on this share" when the truth is "no scan of this share" closes a finding that
was never opened.

The project has already taken this position everywhere below the UI. An unread descriptor
resolves to `at_least`, not to zero rights (ADR-0011). An unreconciled scope may not be used
to infer absence (Phase 2B). A boundary whose parent was unread is a boundary with reason
`parent_unreadable` (ADR-0009). A bounded traversal reports its own truncation (ADR-0006).
Phase 6A is where all of that meets a rendered page — and a page is where the care gets lost,
because `items.length === 0` is always available and always tempting.

## Decision

**A view never interprets its own emptiness. It is handed a verdict computed from run
records, and it renders that verdict.**

1. `app/domain/collection.py` folds the latest run of each collector into one
   `CollectionHealth`: `no_data`, `healthy`, `incomplete`, or `failed`. Pure function, no
   database, no request.
2. `GET /api/v1/collection/status` publishes it, with the concerns that justify it in
   sentences written for an auditor.
3. `frontend/lib/state.ts` combines an API result with that verdict into a `ViewState`.
   Exactly one of its five empty reasons — `no-matches`, reached only under `healthy` —
   words the emptiness as an answer.
4. Unknown coverage is never treated as good coverage. If the status call itself failed, the
   empty state says the emptiness cannot be interpreted.
5. Data that *is* present carries the same verdict as a caveat above it.
6. `collectors:read` is granted to every role, including viewer, because a user who cannot
   see collection state cannot interpret any empty page they are shown.

## Consequences

**The wording is the feature, and it is under test.** `tests/state.test.ts` asserts the
sentences: that the `no_data` case says "not because there are no shares", that the `failed`
case says "unknown rather than as an answer", and that exactly one of the five cases claims
nothing is there. A change that makes an unobserved estate read as a clean one fails the
suite.

**A populated table gets the warning too, and that is the more important half.** Twelve
shares look complete whether or not a collector failed on a thirteenth, so the caveat sits
above the data rather than only in place of it.

**Placeholder sections say so rather than showing an empty table.** Risks and Changes render
a labelled "not yet implemented" panel. An empty table headed "Risks" reads as "no risks
found", which is the single most dangerous sentence this product could accidentally say.

**Search applies the same rule to authorization.** A category the caller may not search is
named in `not_searched` rather than silently omitted, so "no group by that name" and "you
were not allowed to look" are different answers.

**The cost is one extra request per page.** Every view that can be empty fetches the
collection status alongside its data. It is a single aggregate over a small table, it is
issued concurrently with the data call, and it buys the only thing that makes an empty page
safe to read.
