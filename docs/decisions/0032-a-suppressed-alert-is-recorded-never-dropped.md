# ADR-0032: A suppressed alert is recorded, never dropped

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 8B — risk dashboard, watch rules and alerts
- **Deciders:** Phase 8B implementation

## Context

An alerting feature needs storm control. A script rewriting an access control list in a loop,
a group being reorganized, a collector re-reading the same change on an overlapping window —
each can generate the same alert many times in a minute, and delivering every one of them
trains the recipient to ignore the channel.

The usual implementation of storm control is a filter: decide whether to deliver, and if not,
drop the occurrence. It is simple, it is what most systems do, and in an audit tool it is
wrong — for the same reason ADG refuses to treat an unread descriptor as a NULL DACL.

**A feed with nothing in it means one of two completely different things.** The estate was
quiet, or the pipeline held things back. After the fact, with the suppressed occurrences
discarded, nobody can tell which — not the operator reading Monday's feed, not the auditor
asking in March what ADG knew in January, and not the engineer trying to work out why nobody
was paged.

That is exactly the failure this product exists to prevent, performed on the product's own
interface.

A second, narrower version of the same problem: an operator turns a watch off for a
maintenance window and turns it back on. If the disabled period wrote nothing, "this has been
quiet" and "this was switched off" are the same reading afterwards.

## Decision

**Deduplication and cooldown decide what is *delivered*. Nothing decides what is *recorded*.**

1. Every occurrence of every alert writes an `alert_events` row — raised, reopened, repeated,
   **suppressed**, resolved. The write happens on every path through
   `AlertRepository.record`, which is the one place that could fail to do it.
2. A suppressed row carries a `suppression_reason`: `identical_content` (detection ran twice),
   `within_cooldown` (something new, inside the quiet window), or `watch_disabled` (the
   subscription was off). `ck_alert_events_suppression_names_its_reason` makes that an
   invariant rather than a convention.
3. The alert row carries `suppressed_since_notice` and `suppressed_total`, and the **next
   delivery carries the count it stands for** (`folds`). A notification that silently stood for
   eleven changes under-reports by ten, and nothing else on the row would say so.
4. A **disabled watch suppresses rather than discards**, so turning it back on shows what it
   missed.
5. `delivered_digest` moves only on a notification. A suppressed repeat updates the counts and
   leaves it alone — if a suppression moved it, the next occurrence of the content an operator
   was actually shown would compare unequal and read as new, inverting the deduplication in the
   direction that produces noise about things nobody was told.
6. The API exposes every event, suppressed ones included, at `GET /api/v1/alerts/{key}`, and
   the interface shows them with their reasons.

### Two things are never suppressed

A **resolution** and a **reopen**.

The failure mode of an alerting system is not "too loud"; it is "somebody believed the last
thing it said". An operator told an exposure opened and never told it closed will keep acting
on a condition that is gone. One told it closed and never told it came back will not act on one
that is live. A storm of resolutions is also self-limiting in a way a storm of repeats is not:
nothing can resolve more often than it opened.

A reopen is therefore checked **before** the payload digest, because a reopen's content is
frequently identical to the original's — the same finding, the same evidence — and comparing
digests first would call it a duplicate.

## Consequences

**The tables grow with occurrences rather than with alerts.** A noisy estate writes many
suppressed rows for few alerts. That is the cost of the property and it is bounded by the same
retention question `object_versions` already has; nothing prunes them today.

**"Why was I not told?" is answerable.** It is one route, and the answer names the reason and
the instant.

**A cooldown is recoverable.** An operator who realizes a window was too long can read what it
held back rather than having to reproduce it.

**The counts on an alert are not the counts that were delivered**, and the interface has to say
which is which. It does: `occurrence_count` is how often it happened, `suppressed_total` is how
often nobody was told, and the row shows both when they differ.

**Storm control is not silence control.** Suppression is about the notification channel. A
reader who opens the feed sees everything, and a deliberately quiet channel never becomes a
quiet record.

## Alternatives considered

**Drop suppressed occurrences.** Smaller tables, and it makes the product's own monitoring
lie in exactly the way the product is built to detect. Rejected.

**Record only a counter, not individual events.** Cheaper, and it loses *when* and *what* —
so "was the payroll share edited during the maintenance window?" becomes unanswerable, which
is a question somebody asks precisely when the answer matters.

**Suppress by not detecting.** Cheapest: skip detection while a cooldown is active. It makes
the cooldown a coverage gap rather than a delivery policy, and a gap in an audit tool is the
thing every other decision in this codebase is arranged to avoid.

## References

- `app/alerts/dedupe.py` — the decision, pure and fully tested hermetically.
- `app/repositories/alerts.py` — where the decision meets a row.
- `docs/architecture/alerting.md` §3.
- ADR-0015 (emptiness is attributed by the backend) and ADR-0016 (an empty answer names its
  own emptiness), of which this is the alerting case.
