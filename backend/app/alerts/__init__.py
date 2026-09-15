r"""Watches, alerts, and the outbox that delivers them.

An alert pipeline in an audit tool has one job that is harder than it looks: **be believable
when it is quiet**. A feed that has said nothing for a week is either a quiet week or a
broken pipeline, and every design decision in this package is about keeping those two
distinguishable.

## The layers, and what each one is allowed to know

===================================== ===============================================
:mod:`app.alerts.model`               the vocabulary: watch kinds, triggers, the two
                                      lifecycles, the alert key. Values only
:mod:`app.alerts.configuration`       what an installation changes: which triggers are
                                      live, how loud, where it goes
:mod:`app.alerts.detection`           facts in, candidate alerts out. Pure, and
                                      deliberately blind to where the facts came from
:mod:`app.alerts.dedupe`              whether to say it again. One pure decision
:mod:`app.alerts.outbox`              the transaction boundary: raising is durable,
                                      delivering is a separate pass
:mod:`app.alerts.sinks`               where it goes, and whether retrying could help
:mod:`app.alerts.dispatcher`          draining the outbox, bounded, one attempt each
===================================== ===============================================

**Nothing in this package imports** :mod:`app.risk_engine` **or** :mod:`app.changes`. That is
prompt requirement 6 turned into something a test can fail on
(``tests/alerts/test_layering.py``): a delivery channel cannot reach into a rule, so a rule
cannot come to depend on a channel. The translation from a finding or a classified change
into the flat records :mod:`app.alerts.detection` reads lives one layer up, in
:mod:`app.services.alerts`, which is allowed to know both.

## The four things it is honest about

* **A suppressed alert is written down.** Deduplication and cooldown decide what to
  *deliver*, never what to *record*, so "we were not told" and "it did not happen" stay
  separable afterwards.
* **A resolution is never suppressed.** An operator who was told an exposure opened and is
  never told it closed will keep acting on a condition that is gone.
* **An abandoned delivery is loud and permanent.** It means somebody was meant to be told and
  was not, which is the state this package exists to make visible rather than to tidy away.
* **An alert has no severity of its own.** It carries the grading of whatever produced it.
  See :mod:`app.alerts.model`.
"""

from app.alerts.configuration import (
    DEFAULT_POLICY,
    AlertPolicy,
    PolicyError,
    RetrySchedule,
    SinkConfig,
    describe_policy,
    load_policy,
    parse_policy,
)
from app.alerts.dedupe import (
    AlertState,
    Decision,
    SuppressionReason,
    decide_raise,
    decide_resolve,
)
from app.alerts.detection import (
    AccessExpansion,
    ChangeNotice,
    FindingNotice,
    WatchIndex,
    events_for_access_expansions,
    events_for_changes,
    events_for_findings,
    resolution_keys,
)
from app.alerts.dispatcher import AlertDispatcher, DrainReport, build_sinks
from app.alerts.model import (
    MAX_COOLDOWN,
    MIN_COOLDOWN,
    NOTIFYING_TRANSITIONS,
    TRIGGER_DESCRIPTIONS,
    TRIGGER_LIFECYCLE,
    TRIGGERS_BY_WATCH_KIND,
    UNWATCHED_TRIGGERS,
    AlertEvent,
    AlertLifecycle,
    AlertStatus,
    AlertSubject,
    AlertTransition,
    AlertTrigger,
    Watch,
    WatchKind,
    alert_key,
    payload_digest,
    triggers_for_kind,
)
from app.alerts.outbox import (
    ClaimedDelivery,
    DeliveryEnvelope,
    DeliveryStatus,
    InMemoryOutbox,
    Outbox,
    idempotency_key,
)
from app.alerts.sinks import (
    AlertSink,
    DeliveryResult,
    LogSink,
    WebhookSink,
    build_sink,
    default_registry,
)

__all__ = [
    "DEFAULT_POLICY",
    "MAX_COOLDOWN",
    "MIN_COOLDOWN",
    "NOTIFYING_TRANSITIONS",
    "TRIGGERS_BY_WATCH_KIND",
    "TRIGGER_DESCRIPTIONS",
    "TRIGGER_LIFECYCLE",
    "UNWATCHED_TRIGGERS",
    "AccessExpansion",
    "AlertDispatcher",
    "AlertEvent",
    "AlertLifecycle",
    "AlertPolicy",
    "AlertSink",
    "AlertState",
    "AlertStatus",
    "AlertSubject",
    "AlertTransition",
    "AlertTrigger",
    "ChangeNotice",
    "ClaimedDelivery",
    "Decision",
    "DeliveryEnvelope",
    "DeliveryResult",
    "DeliveryStatus",
    "DrainReport",
    "FindingNotice",
    "InMemoryOutbox",
    "LogSink",
    "Outbox",
    "PolicyError",
    "RetrySchedule",
    "SinkConfig",
    "SuppressionReason",
    "Watch",
    "WatchIndex",
    "WatchKind",
    "WebhookSink",
    "alert_key",
    "build_sink",
    "build_sinks",
    "decide_raise",
    "decide_resolve",
    "default_registry",
    "describe_policy",
    "events_for_access_expansions",
    "events_for_changes",
    "events_for_findings",
    "idempotency_key",
    "load_policy",
    "parse_policy",
    "payload_digest",
    "resolution_keys",
    "triggers_for_kind",
]
