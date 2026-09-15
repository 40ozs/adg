"""What an installation may change about alerting, as data rather than as code.

Three kinds of setting, and they are kept apart because they are owned by different people
and fail in different ways.

**Which triggers are live.** A trigger that is off produces nothing — no alert, no suppressed
record. Turning one off is a decision that this installation does not want to hear about that
class of thing at all, and it is visible in :func:`describe_policy` beside the ones that are
on, so a policy with the interesting trigger disabled never looks like a healthy one.

**How loud.** The default cooldown, which a watch may shorten or lengthen within the bounds
:mod:`app.alerts.model` fixes, and the severity at or above which a risk finding raises an
alert on its own.

**Where it goes.** The sinks, each named and configured. **No sink here knows what a finding
is**, and nothing in this module imports the risk engine or the change classifier: a delivery
channel that could reach into the rules would be a channel that could disagree with them.

A policy is loaded from a JSON document, validated on the way in, and refused loudly rather
than partially — the same rule the risk configuration follows, for the same reason: a file
that silently drops the one webhook somebody depended on is worse than one that will not
load.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from app.alerts.model import (
    MAX_COOLDOWN,
    MIN_COOLDOWN,
    AlertTrigger,
)
from app.domain.errors import DomainValidationError

__all__ = [
    "DEFAULT_POLICY",
    "MAX_ATTEMPTS_CEILING",
    "AlertPolicy",
    "PolicyError",
    "RetrySchedule",
    "SinkConfig",
    "describe_policy",
    "load_policy",
    "parse_policy",
]


class PolicyError(DomainValidationError):
    """An alert policy that cannot be applied as written."""


#: An attempt ceiling above this is not a retry policy; it is a way to keep a broken endpoint
#: in the outbox forever while the queue behind it grows.
MAX_ATTEMPTS_CEILING: Final = 20

_SEVERITY_VALUES: Final = ("informational", "low", "medium", "high", "critical")
"""The risk engine's severity vocabulary, **by value rather than by import**.

This package does not import :mod:`app.risk_engine`, and this is the one place that costs
anything. The duplication is bounded to five strings and pinned by
``tests/alerts/test_configuration.py::test_the_severity_vocabulary_matches_the_risk_engine``,
which fails the moment the two drift. Importing the enum instead would let a sink reach the
rules through the policy object, which is the coupling this whole layering exists to prevent.
"""


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    """How long to wait before trying a failed delivery again.

    Deterministic and exponential, capped. **No jitter**, and that is a decision rather than
    an omission: jitter spreads a thundering herd across many senders, and there is one
    sender here. What it would buy is unpredictability in a test suite, which is the one
    thing a delivery schedule must not have — a retry test that passes on the average is a
    retry test that fails on a Friday.
    """

    initial: dt.timedelta = dt.timedelta(seconds=30)
    multiplier: float = 4.0
    maximum: dt.timedelta = dt.timedelta(hours=1)
    max_attempts: int = 6

    def __post_init__(self) -> None:
        if self.initial <= dt.timedelta(0):
            raise PolicyError("The first retry delay must be positive.", field="initial")
        if self.multiplier < 1.0:
            raise PolicyError(
                "A retry multiplier below 1 makes each attempt sooner than the last, which "
                "turns a failing endpoint into a tight loop.",
                field="multiplier",
            )
        if self.maximum < self.initial:
            raise PolicyError(
                "The retry ceiling must be at least the first delay.", field="maximum"
            )
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_CEILING:
            raise PolicyError(
                f"max_attempts must be between 1 and {MAX_ATTEMPTS_CEILING}; received "
                f"{self.max_attempts}. An unbounded retry keeps a dead endpoint's backlog "
                "growing in front of every other delivery.",
                field="max_attempts",
            )

    def delay_before(self, attempt: int) -> dt.timedelta:
        """How long to wait after ``attempt`` failed attempts, capped at :attr:`maximum`.

        ``attempt`` counts attempts already made, so the wait after the first failure is
        :attr:`initial`.
        """
        if attempt < 1:
            raise PolicyError(
                "A retry delay is asked for after an attempt, so the count is at least 1.",
                field="attempt",
            )
        seconds = self.initial.total_seconds() * (self.multiplier ** (attempt - 1))
        return min(dt.timedelta(seconds=seconds), self.maximum)

    def exhausted(self, attempts: int) -> bool:
        """Whether a delivery has been tried as often as this schedule allows."""
        return attempts >= self.max_attempts


@dataclass(frozen=True, slots=True)
class SinkConfig:
    """One delivery destination, named and configured.

    ``kind`` selects an implementation from :mod:`app.alerts.sinks`; ``options`` is handed to
    it unvalidated by this module and validated by the sink, because only the sink knows what
    a URL means to it. ``name`` is the identity a delivery row carries, so renaming a sink
    starts a fresh delivery history rather than rewriting one.
    """

    name: str
    kind: str
    options: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    #: Triggers this sink receives. Empty means all of them. Present so that a noisy
    #: development webhook can take the change notices while a quieter one takes only the
    #: risk findings, without either of them knowing what a finding is.
    triggers: frozenset[AlertTrigger] = frozenset()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise PolicyError("A sink must be named.", field="name")
        if not self.kind.strip():
            raise PolicyError(
                f"Sink {self.name!r} declares no kind, so nothing knows how to deliver to it.",
                field="kind",
            )

    def accepts(self, trigger: AlertTrigger) -> bool:
        return self.enabled and (not self.triggers or trigger in self.triggers)


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    """Everything an installation configures about alerting."""

    version: str = "default"
    #: Triggers that may produce an alert at all. A trigger absent from this set produces
    #: nothing, not a suppressed record: it is switched off, not quieted.
    enabled_triggers: frozenset[AlertTrigger] = frozenset(AlertTrigger)
    default_cooldown: dt.timedelta = dt.timedelta(minutes=15)
    #: The severity at or above which a risk finding raises an alert with no watch behind
    #: it. Named by value; see :data:`_SEVERITY_VALUES`.
    finding_severity_threshold: str = "critical"
    retry: RetrySchedule = field(default_factory=RetrySchedule)
    sinks: tuple[SinkConfig, ...] = ()
    #: Deliveries drained in one pass. A ceiling rather than a target: the drain is bounded
    #: so that a backlog cannot turn one operator command into an unbounded run.
    drain_batch_size: int = 50

    def __post_init__(self) -> None:
        if not MIN_COOLDOWN <= self.default_cooldown <= MAX_COOLDOWN:
            raise PolicyError(
                f"The default cooldown must be between {MIN_COOLDOWN} and {MAX_COOLDOWN}; "
                f"received {self.default_cooldown}.",
                field="default_cooldown",
            )
        if self.finding_severity_threshold not in _SEVERITY_VALUES:
            raise PolicyError(
                f"{self.finding_severity_threshold!r} is not a severity. Choose one of "
                f"{list(_SEVERITY_VALUES)}.",
                field="finding_severity_threshold",
            )
        names = [sink.name for sink in self.sinks]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise PolicyError(
                f"Two sinks share the name(s) {duplicates!r}. A delivery is identified by "
                "its alert and its sink name, so two sinks with one name would collapse "
                "into one delivery history and each would appear to have received what the "
                "other did.",
                field="sinks",
            )
        if self.drain_batch_size < 1:
            raise PolicyError("A drain must be allowed at least one delivery.", field="drain")

    def enables(self, trigger: AlertTrigger) -> bool:
        return trigger in self.enabled_triggers

    def sinks_for(self, trigger: AlertTrigger) -> tuple[SinkConfig, ...]:
        return tuple(sink for sink in self.sinks if sink.accepts(trigger))

    @property
    def has_a_sink(self) -> bool:
        return any(sink.enabled for sink in self.sinks)

    @property
    def severity_rank(self) -> int:
        """The threshold's position in the severity order, for a caller that holds the enum.

        Returned as a rank rather than as the string so that the comparison a caller makes
        is an ordering rather than an equality — ``>= critical`` and ``== critical`` differ
        the moment somebody adds a severity above it.
        """
        return _SEVERITY_VALUES.index(self.finding_severity_threshold)


DEFAULT_POLICY: Final = AlertPolicy(
    sinks=(
        SinkConfig(
            name="operator-log",
            kind="log",
            options={"level": "warning"},
        ),
    )
)
"""The shipped policy: every trigger live, a fifteen-minute cooldown, critical findings
only, and one sink that writes a structured log line.

A log sink rather than no sink, deliberately. An installation with nowhere to deliver would
enqueue alerts that nothing ever drains, and the outbox would grow behind a queue depth
nobody is looking at. Writing them to the operator log is the smallest destination that is
still a destination, and it is one an operator already has a way to read.
"""


# ----------------------------------------------------------------------- parsing


def parse_policy(document: Mapping[str, Any]) -> AlertPolicy:
    """Validate a policy document into a policy. Every error names its entry."""
    unknown = sorted(set(document) - _POLICY_KEYS)
    if unknown:
        raise PolicyError(
            f"Unknown alert policy key(s): {unknown!r}. Refused rather than ignored: a "
            "misspelled key would leave the setting it was meant to change at its default, "
            f"silently. Known keys: {sorted(_POLICY_KEYS)!r}.",
            field="policy",
        )

    version = _text(document.get("version", "custom"), field="version")
    triggers = _triggers(document.get("enabled_triggers"), field="enabled_triggers")
    cooldown = _seconds(
        document.get("default_cooldown_seconds"),
        field="default_cooldown_seconds",
        default=DEFAULT_POLICY.default_cooldown,
    )
    threshold = _text(
        document.get("finding_severity_threshold", DEFAULT_POLICY.finding_severity_threshold),
        field="finding_severity_threshold",
    )
    retry = _retry(document.get("retry"))
    sinks = _sinks(document.get("sinks"))
    batch = document.get("drain_batch_size", DEFAULT_POLICY.drain_batch_size)
    if not isinstance(batch, int) or isinstance(batch, bool):
        raise PolicyError("drain_batch_size must be a whole number.", field="drain_batch_size")

    return AlertPolicy(
        version=version,
        enabled_triggers=triggers if triggers is not None else frozenset(AlertTrigger),
        default_cooldown=cooldown,
        finding_severity_threshold=threshold,
        retry=retry,
        sinks=sinks,
        drain_batch_size=batch,
    )


def load_policy(path: str | Path) -> AlertPolicy:
    """Read and validate a policy file.

    Raises:
        PolicyError: the file is missing, is not valid JSON, or cannot be applied. A missing
            file is an error rather than a silent fall back to the defaults, for the reason
            the risk configuration gives: an operator who configured a destination and typed
            the path wrongly would otherwise get an installation that delivers nowhere and
            says nothing about it.
    """
    candidate = Path(path)
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError as error:
        raise PolicyError(
            f"The alert policy at {candidate} could not be read: {error}.", field="path"
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise PolicyError(
            f"The alert policy at {candidate} is not valid JSON: {error}.", field="path"
        ) from error
    if not isinstance(document, Mapping):
        raise PolicyError(f"The alert policy at {candidate} must be a JSON object.", field="path")
    return parse_policy(document)


def describe_policy(policy: AlertPolicy) -> tuple[str, ...]:
    """Lines an operator can read to confirm what is actually in force.

    Lists the triggers that are **off** and says when there is no enabled sink, because a
    summary of only what is on would make a policy that delivers nowhere look identical to
    a healthy one.
    """
    lines = [f"Alert policy version {policy.version!r}."]
    for trigger in AlertTrigger:
        state = "on" if policy.enables(trigger) else "OFF"
        lines.append(f"  {trigger.value}: {state}")
    lines.append(
        f"  default cooldown: {int(policy.default_cooldown.total_seconds())}s; "
        f"finding threshold: {policy.finding_severity_threshold}"
    )
    lines.append(
        f"  retry: up to {policy.retry.max_attempts} attempts, "
        f"{int(policy.retry.initial.total_seconds())}s x{policy.retry.multiplier} to "
        f"{int(policy.retry.maximum.total_seconds())}s"
    )
    if not policy.sinks:
        lines.append("  sinks: NONE CONFIGURED -- alerts are recorded and delivered nowhere.")
    else:
        for sink in policy.sinks:
            state = "enabled" if sink.enabled else "DISABLED"
            scope = (
                "all triggers"
                if not sink.triggers
                else ", ".join(sorted(trigger.value for trigger in sink.triggers))
            )
            lines.append(f"  sink {sink.name!r} ({sink.kind}): {state}, {scope}")
        if not policy.has_a_sink:
            lines.append("  every sink is disabled -- alerts are recorded and delivered nowhere.")
    return tuple(lines)


_POLICY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "version",
        "enabled_triggers",
        "default_cooldown_seconds",
        "finding_severity_threshold",
        "retry",
        "sinks",
        "drain_batch_size",
    }
)

_RETRY_KEYS: Final[frozenset[str]] = frozenset(
    {"initial_seconds", "multiplier", "maximum_seconds", "max_attempts"}
)

_SINK_KEYS: Final[frozenset[str]] = frozenset({"name", "kind", "options", "enabled", "triggers"})


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{field} must be a non-empty string.", field=field)
    return value.strip()


def _seconds(value: Any, *, field: str, default: dt.timedelta) -> dt.timedelta:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyError(f"{field} must be a whole number of seconds.", field=field)
    return dt.timedelta(seconds=value)


def _triggers(value: Any, *, field: str) -> frozenset[AlertTrigger] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise PolicyError(f"{field} must be a list of trigger names.", field=field)
    known = {trigger.value: trigger for trigger in AlertTrigger}
    chosen: set[AlertTrigger] = set()
    for entry in value:
        if not isinstance(entry, str) or entry not in known:
            raise PolicyError(
                f"{entry!r} is not an alert trigger. Known triggers: {sorted(known)!r}.",
                field=field,
            )
        chosen.add(known[entry])
    return frozenset(chosen)


def _retry(value: Any) -> RetrySchedule:
    if value is None:
        return RetrySchedule()
    if not isinstance(value, Mapping):
        raise PolicyError("retry must be a JSON object.", field="retry")
    unknown = sorted(set(value) - _RETRY_KEYS)
    if unknown:
        raise PolicyError(
            f"Unknown retry key(s): {unknown!r}. Known keys: {sorted(_RETRY_KEYS)!r}.",
            field="retry",
        )
    defaults = RetrySchedule()
    multiplier = value.get("multiplier", defaults.multiplier)
    if not isinstance(multiplier, int | float) or isinstance(multiplier, bool):
        raise PolicyError("retry.multiplier must be a number.", field="retry.multiplier")
    attempts = value.get("max_attempts", defaults.max_attempts)
    if not isinstance(attempts, int) or isinstance(attempts, bool):
        raise PolicyError("retry.max_attempts must be a whole number.", field="retry.max_attempts")
    return RetrySchedule(
        initial=_seconds(
            value.get("initial_seconds"), field="retry.initial_seconds", default=defaults.initial
        ),
        multiplier=float(multiplier),
        maximum=_seconds(
            value.get("maximum_seconds"), field="retry.maximum_seconds", default=defaults.maximum
        ),
        max_attempts=attempts,
    )


def _sinks(value: Any) -> tuple[SinkConfig, ...]:
    if value is None:
        return DEFAULT_POLICY.sinks
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise PolicyError("sinks must be a list of sink objects.", field="sinks")
    parsed: list[SinkConfig] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise PolicyError(f"sinks[{index}] must be a JSON object.", field="sinks")
        unknown = sorted(set(entry) - _SINK_KEYS)
        if unknown:
            raise PolicyError(
                f"sinks[{index}] has unknown key(s) {unknown!r}. Known keys: "
                f"{sorted(_SINK_KEYS)!r}.",
                field="sinks",
            )
        options = entry.get("options", {})
        if not isinstance(options, Mapping):
            raise PolicyError(f"sinks[{index}].options must be a JSON object.", field="sinks")
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise PolicyError(f"sinks[{index}].enabled must be true or false.", field="sinks")
        triggers = _triggers(entry.get("triggers"), field=f"sinks[{index}].triggers")
        parsed.append(
            SinkConfig(
                name=_text(entry.get("name"), field=f"sinks[{index}].name"),
                kind=_text(entry.get("kind"), field=f"sinks[{index}].kind"),
                options=dict(options),
                enabled=enabled,
                triggers=triggers if triggers is not None else frozenset(),
            )
        )
    return tuple(parsed)
