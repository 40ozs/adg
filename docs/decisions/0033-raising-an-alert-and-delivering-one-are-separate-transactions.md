# ADR-0033: Raising an alert and delivering one are separate transactions

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 8B — risk dashboard, watch rules and alerts
- **Deciders:** Phase 8B implementation

## Context

Alerts are raised as a consequence of collection. A collector posts a run completion; the run
is recorded; watches match what moved; a critical finding opens. The natural implementation is
to notify at that moment — call the webhook, send the message — and it has a failure mode that
is much worse than it looks.

If raising an alert means calling a remote endpoint inside the request that closed the scan
run, then **an estate loses observations when somebody's chat integration expires.** A webhook
that is down, slow, or returning 500 raises inside the ingestion transaction, the transaction
rolls back, and the collector is told its completion failed. It retries, and fails again, for
as long as the endpoint is unwell.

It fails *intermittently*, which is worse than failing outright: the job goes red on Tuesday
afternoons, nobody can reproduce it, and the eventual workaround is to turn off the thing that
was supposed to be watching.

There is also a subtler version at the call site. Even with the notification moved after the
commit, a defect anywhere downstream — an exhausted connection pool, a policy file edited
badly, a bug in detection — would turn a successful completion into a 500 **after** the
ingestion had committed. The collector would then retry a run that was already recorded.

## Decision

**An alert is durable before anything tries to deliver it, and nothing downstream of ingestion
can fail an ingestion.**

Three boundaries, each structural rather than a matter of discipline:

1. **Enqueueing is a database write in the same transaction as the alert.** Raising an alert
   writes the alert, its event, and one `alert_deliveries` row per destination. If that
   transaction commits, the alert exists and the intent to deliver exists. If it does not,
   neither does.
2. **Delivery is a separate pass in a separate transaction.** `AlertDispatcher.drain` claims
   due deliveries, attempts one each, and records the outcome. A failure is a row with a retry
   instant on it, not an exception propagating into whatever raised the alert.
3. **The post-completion hook cannot fail the completion.** It runs *after*
   `IngestionService.complete_run` has committed, in its own session;
   `evaluate_run_after_commit` swallows its own failures into a log line; and the route wraps
   the call in a second `try`/`except` for the failures the function cannot catch itself.

### What the outbox is, and is not

It is a queue with a transaction boundary through the middle of it. It has no `deliver` — what
a delivery *is* lives in `app/alerts/sinks.py`, which the outbox never imports, so a storage
bug cannot become a delivery bug and the in-memory implementation is a complete substitute in
every test that is about dispatch rather than about SQL.

It has no `delete`. A delivered row is the evidence that it was delivered and an abandoned one
is the evidence that it was not; pruning belongs to a retention policy an operator sets, not to
the sender.

### Idempotency, in both directions

Every envelope carries a key that is a pure function of `(event_id, sink_name)`. It is
`UNIQUE` on the table — so a re-raised alert records one delivery rather than two, which is
what makes a retried ingestion safe — and it is **sent with the request**, stable across
retries, so a receiver that applied an attempt whose acknowledgment was lost can recognize the
next one.

Without that key, "retry" and "duplicate" are the same thing at the far end, and the honest
choices are to retry and risk duplicates or to give up and risk silence.

## Consequences

**Delivery is at-least-once, not exactly-once**, and the idempotency key is how a receiver
makes it exactly-once at its end. ADG cannot assume somebody else's endpoint honors it, which
is why the claim uses `FOR UPDATE SKIP LOCKED` — two drains running at once divide the queue
rather than both attempting the same delivery.

**An alert can be raised and never delivered**, and that state is visible rather than silent:
`DeliveryStatus.ABANDONED`, an `ERROR` log line, its own count in the queue reading, and the
error that stopped it kept on the row. Nothing deletes it.

**Somebody has to drain the queue.** `python -m app.operations drain-alerts` is the supported
way, and an installation that schedules neither that nor the inline hook has a queue that grows.
The queue endpoint reports staleness precisely so that case is visible: depth alone cannot
separate a busy pipeline from one nothing is draining.

**Alerts are not instantaneous.** With the inline hook off, they are as prompt as the drain's
schedule. That is the correct trade for a feature that must never be able to cost the estate an
observation.

## Alternatives considered

**Notify inline, inside the ingestion transaction.** Simple, immediate, and it makes a
collector's reliability depend on a third-party endpoint's. Rejected — it is the failure this
ADR exists to prevent.

**Notify inline, after the commit, with no outbox.** Better: ingestion is safe. But a failed
delivery is then simply lost, with nothing recording that it was attempted or that it failed,
which contradicts ADR-0032 one layer down.

**A background worker process with an in-memory queue.** Alerts raised and not yet delivered
would not survive a restart, and "we restarted the API and three critical findings were never
announced" is not a sentence this product may make available.

**A message broker.** The right answer at a scale ADG does not have. It adds an operational
component to a deployment that is currently three containers, to solve a problem one `UNIQUE`
constraint and a `SKIP LOCKED` claim already solve.

## References

- `app/alerts/outbox.py` — the protocol and the transaction boundary.
- `app/alerts/dispatcher.py` — the retry decision.
- `app/repositories/alerts.py` — `SqlAlertOutbox`.
- `app/api/scan_runs.py` — the guarded call site.
- `docs/architecture/alerting.md` §4.
