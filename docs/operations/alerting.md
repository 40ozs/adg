# Alerting — operator reference

> What to configure, what to run, and how to tell whether it is working.
> Design rationale: [`docs/architecture/alerting.md`](../architecture/alerting.md).

---

## 1. The first thing to know

**Nothing runs by itself.** Out of the box ADG raises no alerts, because nothing evaluates the
rules and nothing watches anything. There are two switches and you need at least one:

```powershell
# Option A — schedule it. The recommended shape for anything but a small estate.
python -m app.operations evaluate-risks          # a full pass; alerts on what opened
python -m app.operations drain-alerts --loop     # delivers what is queued

# Option B — inline, on every scan run completion.
#   ADG_ALERTS_ON_RUN_COMPLETION=true
```

Being off is never silent. Every run completion logs a `post_run` line saying so, and the
Risks page says *"The rules have never been evaluated"* above an empty table rather than
showing you a clean report.

---

## 2. Configuring watches

A watch is a standing subscription to one **directory**, **share** or **group**. Configure them
on **Risks → Alerts**, or through the API:

```http
POST /api/v1/alerts/watches        Authorization: Bearer <token with alerts:manage>
{
  "kind": "resource",
  "key": "\\\\fs01\\finance\\payroll",
  "label": "Payroll folder",
  "triggers": ["watched_resource_acl_changed", "watched_access_expanded"],
  "cooldown_seconds": 900
}
```

### What each kind covers, and what it does not

| Kind | `key` is | Covers | Does **not** cover |
| --- | --- | --- | --- |
| `resource` | the directory's UNC path | that directory's own access control list and descriptor | anything below it |
| `share` | the share key, `<server>\|<share>` — for example `fs01\|finance` | the share ACL and the share record | the directories published beneath it |
| `group` | the group's principal key | its direct membership | what the group can reach |

A watch covers exactly what it names. One that quietly meant "and everything below" would fire
on a subtree you never named, and on a large share it would fire constantly.

**Watch a server by watching its shares.** There is no server watch: a server's own state is
not an access fact, and a watch that appeared to cover every share while covering none of them
would be worse than no watch at all.

### Which triggers each kind supports

| Trigger | resource | share | group |
| --- | :--: | :--: | :--: |
| `watched_resource_acl_changed` | ✓ | ✓ | |
| `watched_access_expanded` | ✓ | ✓ | |
| `watched_group_membership_changed` | | | ✓ |
| `critical_risk_finding_opened` | *estate-wide — needs no watch* | | |

Asking for one a kind cannot fire is **refused with a 422** rather than saved. A watch that
accepted it would be configured, listed, and silent forever.

`GET /api/v1/alerts/watches` serves this table, so a client builds its form from the server's
copy rather than a second one that could drift.

### A key nothing can scope is refused

| Kind | Accepted | Refused |
| --- | --- | --- |
| `resource` | a UNC path: `\\fs01\finance`, `\\FS01\Finance\Payroll` | `\fs01\finance` (one separator), `C:\finance` |
| `share` | `fs01\|finance` | `share\|fs01\|finance` — that prefix belongs to an *observation* source key, not to the stored share key; and a UNC path |
| `group` | `principal\|S-1-5-21-…` or the bare SID | a display name such as `Finance-RW` |

A key that cannot be scoped is a **422 at creation**, not a saved watch — one that was saved
would be configured, listed, and silent forever, and until this check existed it also stopped
every *other* alert in the estate on each pass.

The check builds the scope the detection pass will build and lets it object, so what it
accepts cannot drift from what the change feed can actually match.

### Two things the interface will not let you do

**A second watch on the same thing** is a 409. It would double every alert about it and give
each copy its own cooldown, so a quiet window you set would still page you at the other watch's
rate.

**Changing a watch's kind or key.** It would silently re-attribute every alert already raised
under it, and the history would then say a directory was edited when it was not. Delete and
create one — deleting a watch **keeps the alerts it raised**.

### Disabling is not deleting

A disabled watch still records what happens to the thing it covers, marked `watch_disabled`, so
turning it back on shows what it missed. A deleted watch keeps nothing going forward. Use
*Turn off* for a maintenance window.

---

## 3. Cooldowns, and what they do not hide

Each watch has a cooldown (1 minute to 24 hours; 15 minutes by default). After a notification,
further occurrences of **that alert** are held for the window.

Held occurrences are **recorded and counted**, never discarded. The next delivery says how many
it stands for, and `GET /api/v1/alerts/{key}` lists every one with its reason. A cooldown you
set too long is recoverable: you can read what it held back.

Three reasons an occurrence is not delivered:

| Reason | Means |
| --- | --- |
| `identical_content` | detection ran twice over the same change — a retried completion, an overlapping window |
| `within_cooldown` | something new, inside the quiet window |
| `watch_disabled` | the subscription was off; kept anyway |

**A resolution and a reopen are never held**, whatever the cooldown says. Being told an
exposure opened and never told it closed is worse than being told twice.

---

## 4. The policy file

`ADG_ALERT_POLICY_PATH` names a JSON document. Empty uses the shipped policy: every trigger
live, a fifteen-minute cooldown, critical findings only, and one sink writing to the operator
log.

```json
{
  "version": "acme-2026-09",
  "enabled_triggers": [
    "watched_resource_acl_changed",
    "watched_group_membership_changed",
    "watched_access_expanded",
    "critical_risk_finding_opened"
  ],
  "default_cooldown_seconds": 900,
  "finding_severity_threshold": "critical",
  "drain_batch_size": 50,
  "retry": {
    "initial_seconds": 30,
    "multiplier": 4.0,
    "maximum_seconds": 3600,
    "max_attempts": 6
  },
  "sinks": [
    { "name": "operator-log", "kind": "log", "options": { "level": "warning" } },
    {
      "name": "ops-webhook",
      "kind": "webhook",
      "triggers": ["critical_risk_finding_opened"],
      "options": {
        "url": "https://hooks.example.internal/adg",
        "token": "REPLACE-ME",
        "timeout_seconds": 10
      }
    }
  ]
}
```

> **This file may carry a credential.** It is configuration, not source control. Keep it
> outside the repository, readable only by the API's service account.

| Key | Meaning |
| --- | --- |
| `enabled_triggers` | A trigger absent from this list produces **nothing** — not a suppressed record. Off is off, not quieted. |
| `default_cooldown_seconds` | What a watch gets if it names none. 60–86 400. |
| `finding_severity_threshold` | The severity at or above which a risk finding alerts on its own. `informational`, `low`, `medium`, `high`, `critical`. |
| `retry` | Deterministic exponential backoff, capped. No jitter — a retry schedule must be predictable. |
| `sinks[].triggers` | Empty means all. Present so a noisy channel can take change notices while a quieter one takes only findings. |
| `sinks[].enabled` | `false` receives nothing. |
| `drain_batch_size` | Deliveries attempted per pass. A ceiling, not a target. |

**A path that is set and unreadable is a startup error**, not a fall back to defaults — an
operator who configured a destination and typed the path wrongly would otherwise get an
installation that delivers nowhere and says nothing about it. An **unknown key is refused**,
because a misspelled one would silently leave the setting it was meant to change at its default.

### Sinks this build provides

**`log`** — writes one structured record to the operator log. `options.level` names a log
level. Cannot fail, which makes it the right default and the wrong thing to test retries
against.

**`webhook`** — POSTs ADG's own `adg.alert/v1` document with three headers: `Idempotency-Key`
(stable across retries — a receiver that applied a lost acknowledgment can recognize the next
attempt), `X-ADG-Alert-Trigger`, and `Content-Type: application/json`. `options`: `url`
(required, http/https), `token` (sent as a bearer credential), `timeout_seconds`, `headers`.

There is **no Slack, Teams, email or PagerDuty sink**, deliberately. Anything vendor-specific
is a sink that reshapes the document ADG sends; nothing upstream of a sink has ever heard of a
vendor, and nothing should have to.

---

## 5. Running it

```powershell
python -m app.operations evaluate-risks
python -m app.operations drain-alerts [--loop --interval 60] [--max-passes 20]
```

Both read `ADG_DATABASE_URL`, the risk configuration and the alert policy from the same
environment the API does. There is no separate configuration for a scheduled pass: an
installation whose report and whose cron job disagreed about which rules are enabled would be
worse than one with no cron job.

### Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | Did what it set out to. |
| `1` | Failed. The message says what. |
| **`2`** | **Succeeded, and something needs a person.** A risk pass that could not cover the estate, or a drain that abandoned a delivery. |

Branch on `2` in your scheduler. Both cases are ones where the normal reading of "exit 0" —
*this is fine* — would be wrong, and both are invisible in the output of a job nobody reads.

### A suggested schedule

```
*/5  * * * *   python -m app.operations drain-alerts
15   2 * * *   python -m app.operations evaluate-risks
```

Or set `ADG_ALERTS_ON_RUN_COMPLETION=true` and run only the drain. That adds an incremental
evaluation, a change-feed scan per watch and a delivery attempt to the collector's final
request; it is off by default because nobody has profiled the engine against an estate with
millions of access control entries. Measure it on yours before turning it on for a large one.

---

## 6. Is it working?

**Risks → Alerts** answers this above the feed. By API:

```http
GET /api/v1/alerts/deliveries
```

```json
{
  "depth": { "pending": 0, "delivered": 412, "failed": 0, "abandoned": 0 },
  "stale": 0,
  "abandoned": 0,
  "oldest_pending_at": null,
  "policy": ["Alert policy version 'default'.", "  sink 'operator-log' (log): enabled, all triggers"]
}
```

Four numbers, because each answers something the others cannot:

| Reading | Means | Do |
| --- | --- | --- |
| `abandoned` above 0 | **Alerts nobody received.** The schedule ran out, or a sink reported a failure retrying cannot fix. | Read `last_error` on the deliveries at `GET /api/v1/alerts/{key}`. Fix the destination. These do not retry. |
| `stale` above 0 | Deliveries due more than 15 minutes ago. Usually nothing is draining. | Run or schedule `drain-alerts`. |
| `pending` growing, `stale` flat | A busy pipeline, keeping up. | Nothing. |
| `policy` says `NONE CONFIGURED` | Every destination is disabled or absent. The queue will only grow. | Configure a sink. |

### Why is the feed quiet?

In order:

1. **Has anything evaluated the rules?** Risks → the banner. `has_ever_run: false` means
   nothing has looked.
2. **Is anything watched?** With no watches, three of the four triggers cannot fire at all.
   Only `critical_risk_finding_opened` is estate-wide.
3. **Is a trigger switched off in the policy?** `GET /api/v1/alerts/deliveries` prints the
   policy, off triggers included.
4. **Is the queue draining?** The table above.
5. **Were occurrences suppressed?** The feed shows "*n* not delivered" on any alert whose
   cooldown held something back; open it to see each one and its reason.

If all five say yes-and-nothing-held, the estate was quiet.

---

## 7. Who can do what

| Capability | Held by | Permits |
| --- | --- | --- |
| `alerts:read` | viewer, auditor, reviewer, governance_admin, admin | the feed, one alert's history, the watch list, the queue |
| `alerts:manage` | **admin only** | create, edit and delete a watch; drain the queue by hand |

Reading an alert and deciding who gets woken up are held separately on purpose: somebody who
could quietly disable the watch on the payroll share could make an exposure land in nobody's
inbox — which is the shape of the thing this product exists to find, performed on the product.

A **governance administrator deliberately does not get `alerts:manage`**. Choosing who is paged
is an operations decision, not part of running an access review, and the two failing separately
is the point.

---

## 8. Settings

| Variable | Default | Notes |
| --- | --- | --- |
| `ADG_ALERT_POLICY_PATH` | *(empty)* | Empty uses the shipped policy. Set-and-unreadable is a startup error. **May contain a credential.** |
| `ADG_ALERTS_ON_RUN_COMPLETION` | `false` | Evaluate rules and watches inline when a scan run closes. |
| `ADG_RISK_CONFIGURATION_PATH` | *(empty)* | The risk rules — see [`risk-rules.md`](risk-rules.md). |

---

## 9. Known limitations

1. **No acknowledgment or snooze.** Accepting an alert is a person's decision *about* it, and
   putting it on the row would let the next detection pass overwrite somebody's judgment.
2. **No escalation or on-call routing.** A sink that takes only some triggers is as far as
   routing goes.
3. **Nothing prunes alerts, events or deliveries.** They inherit the retention question
   `object_versions` has; see `docs/operations/mvp-runbook.md` §6.
4. **A watch covers what it names, not a subtree.** Watching a large share's contents means a
   watch per directory. A subtree watch is a future phase's decision, and the thing it has to
   settle is what it does on a share with a hundred thousand directories.
5. **The expansion trigger is bounded** at 25 effective-access resolutions per pass. Exceeding
   it is reported on the evaluation, not hidden, but the alerts beyond it wait for the next
   pass.
6. **Alerts are as prompt as the drain's schedule**, unless the inline hook is on.
