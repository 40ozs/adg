# Alerting

> Watches, alerts, and the outbox that delivers them. Phase 8B.

An alert pipeline inside an audit tool has one job that is harder than it looks: **be
believable when it is quiet.** A feed that has said nothing for a week is either a quiet week
or a broken pipeline, and every decision recorded here is about keeping those two
distinguishable — which is this product's central concern, turned on its own monitoring.

---

## 1. What an alert is

An alert says: *this trigger fired about this thing, and here is what happened.*

Its identity is a SHA-256 of **the trigger, the subject, and a discriminator** — never of the
content. That is what makes deduplication possible at all: the same condition in the same
place has to be the same alert on Tuesday as it was on Monday, even though its payload
carries a different instant. Putting the payload in the key would make every occurrence a new
alert and turn the cooldown into decoration.

The discriminator separates several alerts of one trigger about one subject: the trustee whose
entry moved, the member who joined, the finding key. Two trustees edited on one access control
list are two alerts, counted and cooled down independently — folding them into one would make
the cooldown hide the second trustee entirely.

### An alert carries no severity of its own

It names the thing that produced it and carries **that thing's** grading in its payload: a
`Severity` for a risk finding, a `ChangeSeverity` for a classified change. A third severity
vocabulary would be a third table to keep in step with the other two, and the first time it
drifted an alert would grade an exposure differently from the finding it was raised about. The
two existing scales also do not mean the same thing — one describes a consequence in the
estate, the other describes how much an edit moved — so averaging them would produce a number
whose unit nobody could name.

What an alert does carry is a **trigger**, which says *why you are being told*, and that is
what an operator filters and routes on.

---

## 2. Two lifecycles, and why the distinction is in the data

A risk finding is a **condition**: true until the estate changes, and able to resolve and come
back. An access control list edit is an **event**: it happened, and nothing later can make it
not have happened.

| Trigger | Lifecycle | Needs a watch |
| --- | --- | --- |
| `watched_group_membership_changed` | transient | yes |
| `watched_resource_acl_changed` | transient | yes |
| `watched_access_expanded` | transient | yes |
| `critical_risk_finding_opened` | **stateful** | **no** — estate-wide by policy |

`ck_alerts_transient_never_resolves` enforces it in the database. Pretending otherwise
produces one of two lies: a "resolved" ACL change that was never a condition, or a finding
alert that stays open forever because nothing was watching for its resolution.

**The finding trigger deliberately needs no watch.** Requiring a subscription would make the
feature's coverage equal to somebody's foresight, and the exposure worth interrupting somebody
for is precisely the one on the share nobody thought to watch.

---

## 3. The three dispositions

Three different things, deliberately not collapsed. `app/alerts/dedupe.py` is one pure
function and every branch of it is exercised in microseconds.

**Duplicate** — the same alert with content identical to what was last *delivered*. Detection
ran twice: a retried completion, an overlapping window, an operator re-running an evaluation.
Never a second notification. Matched on the payload digest, never on a timestamp, so it holds
regardless of how far apart the two passes ran. This is what makes overlapping detection
windows a conservative default rather than a source of duplicates.

**Repeat** — the same alert with *different* content, outside the cooldown. Something happened
again and somebody should be told.

**Cooldown** — a repeat inside the window. Suppressed, and **counted**. The next delivery
carries how many occurrences it stands for, so a notification never silently stands for eleven
changes.

### The cooldown is measured from the last notification

Not from the last occurrence. Measured from the last occurrence, something changing every
minute inside a fifteen-minute cooldown would push the next eligible instant out on every pass
and the alert would never be delivered at all — the storm control would have become a silencer.

### What is never suppressed

A **resolution** and a **reopen**.

The failure mode of an alerting system is not "too loud"; it is "somebody believed the last
thing it said". An operator told an exposure opened and never told it closed keeps acting on a
condition that is gone; one told it closed and never told it came back does not act on one
that is live. A storm of resolutions is also self-limiting in a way a storm of repeats is not —
nothing can resolve more often than it opened.

A reopen is checked **before** the digest comparison, because a reopen's content is frequently
identical to the original's (the same finding, the same evidence). Comparing digests first
would call it a duplicate, and an exposure that came back would have been announced once,
months earlier.

### A suppressed occurrence is still written down

Every path through `AlertRepository.record` inserts an `alert_events` row, with the reason.
Deduplication and cooldown decide what is *delivered*; nothing decides what is *recorded*.
Without that row, a feed that went quiet because of a cooldown and a feed that went quiet
because the estate did are the same reading afterwards.

The same rule covers a **disabled watch**: the occurrence is suppressed and recorded, so
turning the watch back on shows what it missed.

---

## 4. The outbox, and the acceptance criterion it exists for

> Alert delivery failure does not roll back source ingestion.

A collector posts a run completion; the run is recorded; watches match; alerts are raised. If
raising an alert meant *calling a webhook*, then an endpoint that is down, slow or returning
500 would take the ingestion transaction with it — and the estate would lose an observation
because somebody's chat integration expired. Intermittently, which is how a collector ends up
with a permanently failing job nobody can reproduce.

So there are two transaction boundaries and they are both structural:

1. **Enqueueing is a database write in the same transaction as the alert.** An alert that was
   raised is durable whether or not anything ever delivers it.
2. **Delivery is a separate pass in a separate transaction.** A failed delivery is a row with
   a retry time on it rather than an exception somewhere up the stack.

And a third, at the call site: `app/api/scan_runs.py` wraps the post-completion hook in its own
`try`/`except`. `evaluate_run_after_commit` already swallows its own failures; the guard catches
what it cannot — an exhausted pool, a settings property that raises because the policy file was
edited badly. Without it, a broken downstream would turn a successful completion into a 500
*after* the ingestion committed, and the collector would retry a run that had already been
recorded.

### Idempotency

Every envelope carries an idempotency key that is a pure function of `(event_id, sink_name)`.
It is sent with the request and is stable across retries, so a receiver that already applied
an attempt whose acknowledgment was lost can recognize the second one. Without it, "retry" and
"duplicate" are the same thing at the far end, and the honest choices are to retry and risk
duplicates or to give up and risk silence.

It is also `UNIQUE` in `alert_deliveries`, which is what makes *enqueueing* idempotent: a
re-raised alert records one delivery rather than two.

### Retry, and the judgment each sink has to make

A sink says whether retrying could help; the dispatcher decides when and whether. Both
mistakes are expensive and invisible in opposite directions:

* treating a permanent failure as retryable keeps a delivery that will **never** succeed at the
  front of the queue, burning attempts — a webhook URL with a typo in it fails this way for
  hours;
* treating a transient failure as permanent abandons a real alert because a proxy hiccuped,
  and abandoning means *somebody was meant to be told and was not*.

So the HTTP classification is written out rather than inferred from a range:

| Response | Verdict | Why |
| --- | --- | --- |
| 2xx | delivered | — |
| 408, 425, 429 | retryable | a timeout the server reported, "too early", explicit backpressure |
| 5xx | retryable | the receiver saying it could not, not that it would not |
| 3xx | **permanent** | following a redirect would deliver an estate's exposure report to a host nobody configured |
| other 4xx | permanent | the sender's fault; it will fail identically next time |
| timeout, DNS, TLS | retryable | routinely temporary, and bounded by the attempt ceiling |

The schedule is exponential, capped, and **has no jitter**. Jitter spreads a thundering herd
across many senders and there is one sender here; what it would buy is unpredictability in a
test suite, which is the one thing a delivery schedule must not have.

### An abandoned delivery is loud and permanent

It gets an `ERROR` log line, it is counted separately in the queue reading, and nothing
deletes it. A delivery that vanished when it gave up would make an undelivered alert
indistinguishable from one that was never raised.

### A sink removed from the policy leaves its deliveries pending

Counted as `unroutable`, never abandoned. A sink removed while deliveries were queued for it is
far more often a policy typo than a decision to discard those alerts, and abandoning them would
be irreversible in a way that leaving them is not.

---

## 5. The layering, and requirement 6

> Do not embed email/Slack vendor specifics into the core risk engine.

**Nothing in `app/alerts` imports `app.risk_engine` or `app.changes`**, and nothing in
`app.risk_engine` imports `app.alerts`. `tests/alerts/test_layering.py` reads the import graph
from the source and fails on either.

`app/alerts/detection.py` reads three flat records — `ChangeNotice`, `AccessExpansion`,
`FindingNotice` — that are *not* `ObjectChange`, `ChangeImpact` or `RiskFinding`. The
translation lives one layer up, in `app/services/alerts.py`, which is allowed to know both.

The cost is one conversion per source and it is worth it twice over: the detection tests need
no database fixtures, and a reader can see the entire vocabulary a sink can receive by reading
three dataclasses.

A sink sends `DeliveryEnvelope.document()` — ADG's own `adg.alert/v1` JSON. There are no Slack
blocks, no email templates and no vendor name in the module. Adding a chat integration means
writing a sink that reshapes that document; it never means teaching the rules about a channel.

```
app/alerts/model          vocabulary, the alert key, the two lifecycles. Values only
app/alerts/configuration  what an installation changes: triggers, loudness, destinations
app/alerts/detection      facts in, candidate alerts out. Pure, source-agnostic
app/alerts/dedupe         whether to say it again. One pure decision
app/alerts/outbox         the transaction boundary and the Outbox protocol
app/alerts/sinks          where it goes, and whether retrying could help
app/alerts/dispatcher     draining, bounded, one attempt per delivery per pass
```

---

## 6. Watches

A watch is a standing subscription to one **directory**, **share** or **group**. Nothing is
watched by default: ADG will not guess which parts of an estate matter.

**A watch covers exactly what it names.** A directory watch covers that directory's own access
control list, not its children's; a share watch covers the share ACL and the share object, not
the directories published beneath it. A watch that quietly meant "and everything below" would
fire on a subtree the operator never named, and one covering a hundred thousand directories
would fire constantly.

A watch on a **server** was considered and left out. A server's own state is not an access
fact, and a watch that appeared to cover every share beneath it while covering none of them is
the kind of quiet nothing this product exists to prevent. Watch the shares.

**A trigger a kind could never fire is refused, not saved.** A membership trigger on a
directory watch would be configured, listed in the interface, and silent forever — and silence
from a monitoring feature reads as "nothing happened". `TRIGGERS_BY_WATCH_KIND` is the table,
it is enforced in `Watch.__post_init__`, and the API serves it so a client builds its form from
the server's copy rather than a second one that could disagree.

**One watch per thing** (`uq_alert_watches_target`). A second on the same directory would
double every alert about it and give each copy its own cooldown, so an operator who set a quiet
window would still be notified at the other watch's rate.

**Disabling is not deleting.** A disabled watch still records what happens, marked
`watch_disabled`; a deleted watch keeps nothing. Deleting a watch **keeps the alerts it
raised** — an alert is the record that somebody was told something, and deleting the
subscription does not un-tell them.

---

## 7. What an evaluation actually does

`AlertService.evaluate_window` runs per **watch**, not estate-wide: a page of estate-wide
changes is mostly things nobody watched, and the watched one is the row that falls off the end
of it. Each watch's feed is scoped and restricted by observation kind.

`watched_access_expanded` is computed separately from `watched_resource_acl_changed`, and
neither implies the other. An entry can be added that grants nothing because a Deny or the
other layer still governs; access can widen with no entry touched because a group gained a
member. They are separate alerts with separate keys, so one cannot deduplicate against the
other.

### One watch cannot silence the rest

A watch whose key the change feed cannot scope on raises inside the per-watch loop. It is
caught there, recorded as a coverage note on the evaluation, and the pass continues — and the
**finding** half runs even when the change half failed outright, because the two are
independent and the finding trigger is the one that fires with no watch behind it.

Without both guards, one mistyped watch key stopped every critical-finding alert in the
estate and left nothing behind but a log line. `POST /api/v1/alerts/watches` now refuses such
a key outright, so the row is not created in the first place; the two guards remain for rows
that predate the check or arrive another way.

### Two bounds, both reported

| Bound | Value | What it stops |
| --- | ---: | --- |
| `CHANGES_PER_WATCH` | 200 | a subtree with a hundred thousand ACL edits |
| `MAX_IMPACT_CHECKS` | 25 | one effective-access resolution per edit over a group nesting forty deep |

Both appear on `AlertEvaluation.truncated`, and `complete` is false when either was hit. That
matters more here than in most places: a pipeline that silently examined half of what it should
have looks exactly like one watching a quiet estate.

### The window, and why overlapping is the safe direction

The post-run pass uses the run's own window — from the run's start to now — rather than a
stored watermark. Overlapping windows re-read changes, and deduplication suppresses the
repeats. Better to look twice than to place a watermark a minute too late and miss an edit.

---

## 8. Running it

Both are **opt-in**, and being off is never silent.

```powershell
# A full pass of the rules, then alerts on what opened.
python -m app.operations evaluate-risks

# Attempt the deliveries that are due. Put this on a schedule.
python -m app.operations drain-alerts --loop --interval 60
```

Exit codes: `0` did what it set out to; `1` failed; **`2` succeeded and something needs a
person** — a risk pass that could not cover the estate, or a drain that abandoned a delivery.
Both are cases where the normal reading of "exit 0" would be wrong and both are invisible in the
output of a job nobody reads.

`ADG_ALERTS_ON_RUN_COMPLETION=true` runs both inline when a scan run closes. Off by default,
and that is a cost decision rather than a doubt about the feature: it adds an incremental
evaluation, a change-feed scan per watch and a delivery attempt to the collector's final
request, on an engine nobody has profiled against millions of access control entries. An
installation that has measured it, or whose estate is small, should turn it on.

Neither being on is visible without hunting: every completion logs a `post_run` line, and the
risk report's coverage block says when the rules were last evaluated — so an installation that
enabled nothing sees *"the rules have not been evaluated"* rather than a clean report.

---

## 9. Configuration

`ADG_ALERT_POLICY_PATH` names a JSON document. Empty uses the shipped policy: every trigger
live, a fifteen-minute cooldown, critical findings only, and one sink that writes to the
operator log.

**A log sink rather than none, deliberately.** An installation with nowhere to deliver would
enqueue alerts nothing ever drains, and the backlog would grow behind a queue depth nobody is
looking at. The log is the smallest destination that is still a destination.

A policy that is set and unreadable is a **startup error**, not a silent fall back — the same
rule the risk configuration follows, for the same reason. An unknown key is refused rather than
ignored: a misspelled one would leave the setting it was meant to change at its default,
silently.

**The policy file may carry a webhook credential**, so it is configuration and never source
control. `.env.example` says so and ships no example token.

See `docs/operations/alerting.md` for the file format.

---

## 10. What this deliberately does not do

* **No email, Slack, Teams or PagerDuty sink.** The abstraction is complete and the log and
  webhook sinks exercise every part of it; a vendor integration is a sink somebody writes, and
  writing one speculatively would put a message format into the repository with nobody to say
  whether it is the right one.
* **No alert acknowledgment or snooze.** Accepting an alert is a person's decision *about* it,
  and putting it on the row would let the next detection pass overwrite somebody's judgment.
  It belongs with the same open question Phase 8A raised for risk acceptance.
* **No escalation or on-call routing.** A sink that takes only some triggers is as far as this
  goes.
* **No retention.** Nothing prunes alerts, events or deliveries. They inherit the question
  `object_versions` has.
