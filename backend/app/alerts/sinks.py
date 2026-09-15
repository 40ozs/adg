"""Where an alert actually goes, and the one judgment each destination has to make.

A sink does two things: it tries to deliver an envelope, and it says whether trying again
could help. That second half is the part worth care, because both mistakes are expensive and
they are invisible in opposite directions.

* Treating a permanent failure as retryable keeps a delivery that will **never** succeed at
  the front of the queue, burning attempts until the schedule gives up. A webhook URL with a
  typo in it fails this way for hours.
* Treating a transient failure as permanent abandons a real alert because a proxy hiccuped
  for one second, and abandoning is the state that means *somebody was meant to be told and
  was not*.

So :class:`DeliveryResult` carries an explicit ``retryable``, and the HTTP classification
below is written out rather than inferred from a status-code range, with the exceptions
named.

**No vendor knows about ADG's domain and ADG's domain knows about no vendor.** A sink is
handed a :class:`~app.alerts.outbox.DeliveryEnvelope` and sends
:meth:`~app.alerts.outbox.DeliveryEnvelope.document` — ADG's own JSON. There are no Slack
blocks here, no email templates, and no import of the risk engine or the change classifier
from anywhere in this module. Adding a chat integration means writing a sink that reshapes
that document; it never means teaching the rules about a channel (prompt item 6).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from app.alerts.configuration import PolicyError, SinkConfig
from app.alerts.outbox import DeliveryEnvelope

__all__ = [
    "RETRYABLE_STATUS_CODES",
    "AlertSink",
    "DeliveryResult",
    "LogSink",
    "SinkRegistry",
    "WebhookSink",
    "build_sink",
    "default_registry",
]

logger = logging.getLogger("app.alerts.delivery")

#: Status codes worth another attempt. Everything else in the 4xx range is the sender's
#: fault and will fail identically next time.
#:
#: ``408`` is a request timeout the server chose to report rather than drop. ``425`` is "too
#: early". ``429`` is explicit backpressure and is exactly the case retrying exists for.
#: Every 5xx is the receiver saying it could not, not that it would not.
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({408, 425, 429})

#: Request timeout for one delivery attempt, in seconds. Short on purpose: a drain holds no
#: database transaction open while it waits, but it does hold the operator's command, and a
#: sink that takes a minute to fail turns a fifty-delivery backlog into a fifty-minute run.
DEFAULT_TIMEOUT_SECONDS: Final = 10.0

#: Bytes of a response body kept in the error text. Enough to recognize "bad token" and not
#: enough to put a receiver's HTML error page into ADG's database.
_ERROR_EXCERPT: Final = 500


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """What one attempt achieved, and whether another would be worth making."""

    delivered: bool
    detail: str | None = None
    retryable: bool = True
    """Only read when :attr:`delivered` is false. ``False`` abandons the delivery."""

    @classmethod
    def success(cls, detail: str | None = None) -> DeliveryResult:
        return cls(delivered=True, detail=detail)

    @classmethod
    def transient(cls, detail: str) -> DeliveryResult:
        return cls(delivered=False, detail=detail, retryable=True)

    @classmethod
    def permanent(cls, detail: str) -> DeliveryResult:
        return cls(delivered=False, detail=detail, retryable=False)


class AlertSink(Protocol):
    """One destination. Stateless with respect to a delivery; retries re-enter here."""

    name: str

    async def deliver(self, envelope: DeliveryEnvelope) -> DeliveryResult:
        """Attempt one delivery. Must not raise: a sink that throws would abort a drain
        partway and leave every delivery behind it unattempted, so every failure mode has to
        arrive as a :class:`DeliveryResult`."""
        ...


class LogSink:
    """Writes the alert to the operator log as one structured record.

    The development sink, and a legitimate production one for an installation whose logs are
    already shipped somewhere a person reads. It is the reason the shipped policy has a
    destination at all: an installation with no sink enqueues deliveries nothing drains, and
    the backlog grows behind a queue depth nobody is looking at.

    It cannot fail, which makes it the one sink whose retry behavior never needs exercising —
    and therefore the wrong sink to test the retry path against. :mod:`tests.alerts` uses a
    deliberately failing stub for that.
    """

    def __init__(self, name: str = "operator-log", *, level: int = logging.WARNING) -> None:
        self.name = name
        self._level = level

    async def deliver(self, envelope: DeliveryEnvelope) -> DeliveryResult:
        logger.log(
            self._level,
            "alert.%s",
            envelope.trigger.value,
            extra={
                "alert_key": envelope.alert_key,
                "event_id": str(envelope.event_id),
                "trigger": envelope.trigger.value,
                "transition": envelope.transition.value,
                "occurrences": envelope.folds,
                "watch_label": envelope.watch_label,
                "summary": envelope.summary,
                "idempotency_key": envelope.idempotency_key,
            },
        )
        return DeliveryResult.success("written to the operator log")


class WebhookSink:
    """Posts ADG's alert document to a URL.

    The development sink with a network in it, and the one that exercises every part of the
    retry and idempotency machinery. What it sends is
    :meth:`~app.alerts.outbox.DeliveryEnvelope.document` and three headers:

    ``Idempotency-Key``
        Stable across every retry of one delivery, so a receiver that applied an attempt
        whose acknowledgment was lost can recognize the next one. This is the whole of what
        makes at-least-once delivery safe to build on.
    ``X-ADG-Alert-Trigger``
        So a receiver can route without parsing the body.
    ``Content-Type: application/json``

    A configured ``token`` is sent as a bearer credential. It is read from the policy file,
    which is configuration and **not** source control — the same rule the rest of the
    application follows, stated in ``.env.example`` and in the operations document.
    """

    def __init__(
        self,
        name: str,
        *,
        url: str,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if not url.lower().startswith(("http://", "https://")):
            raise PolicyError(
                f"Sink {name!r} needs an http(s) URL; received {url!r}.", field="options.url"
            )
        self.name = name
        self._url = url
        self._token = token
        self._timeout = timeout
        self._headers = dict(headers or {})

    async def deliver(self, envelope: DeliveryEnvelope) -> DeliveryResult:
        # Imported here rather than at module scope so that importing the alert package --
        # which the API does at startup -- does not pull in an HTTP client for an
        # installation whose only sink is the log.
        import httpx

        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": envelope.idempotency_key,
            "X-ADG-Alert-Trigger": envelope.trigger.value,
            **self._headers,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=envelope.document(), headers=headers)
        except httpx.TimeoutException as error:
            return DeliveryResult.transient(f"timed out after {self._timeout}s: {error}")
        except httpx.HTTPError as error:
            # Connection refused, DNS failure, TLS failure. Every one of them is a condition
            # that is routinely temporary, and a DNS name that is permanently wrong is
            # already bounded by the attempt ceiling.
            return DeliveryResult.transient(f"{type(error).__name__}: {error}")

        return classify_response(response.status_code, response.text)


def classify_response(status_code: int, body: str) -> DeliveryResult:
    """Turn an HTTP response into a delivery outcome. Separated so it can be tested alone."""
    excerpt = body[:_ERROR_EXCERPT].strip()
    if 200 <= status_code < 300:
        return DeliveryResult.success(f"HTTP {status_code}")
    if status_code in RETRYABLE_STATUS_CODES or status_code >= 500:
        return DeliveryResult.transient(f"HTTP {status_code}: {excerpt}")
    # 3xx included: a sink is configured with the URL to post to, and following a redirect
    # to somewhere else would deliver an estate's exposure report to a host nobody
    # configured. Reported as permanent so an operator fixes the URL.
    return DeliveryResult.permanent(f"HTTP {status_code}: {excerpt}")


SinkRegistry = dict[str, Any]
"""Sink kind to factory. A plain mapping so a deployment can extend it without subclassing."""


def _build_log(config: SinkConfig) -> LogSink:
    level_name = str(config.options.get("level", "warning")).upper()
    level = logging.getLevelNamesMapping().get(level_name)
    if level is None:
        raise PolicyError(
            f"Sink {config.name!r} names log level {level_name!r}, which is not a level.",
            field="options.level",
        )
    return LogSink(config.name, level=level)


def _build_webhook(config: SinkConfig) -> WebhookSink:
    url = config.options.get("url")
    if not isinstance(url, str) or not url:
        raise PolicyError(f"Sink {config.name!r} needs an options.url.", field="options.url")
    token = config.options.get("token")
    if token is not None and not isinstance(token, str):
        raise PolicyError(f"Sink {config.name!r} has a non-string token.", field="options.token")
    timeout = config.options.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int | float) or isinstance(timeout, bool) or timeout <= 0:
        raise PolicyError(
            f"Sink {config.name!r} has a non-positive timeout.", field="options.timeout_seconds"
        )
    headers = config.options.get("headers", {})
    if not isinstance(headers, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise PolicyError(f"Sink {config.name!r} has non-string headers.", field="options.headers")
    return WebhookSink(
        config.name,
        url=url,
        token=token,
        timeout=float(timeout),
        headers=dict(headers),
    )


def default_registry() -> SinkRegistry:
    """The sink kinds this application ships."""
    return {"log": _build_log, "webhook": _build_webhook}


def build_sink(config: SinkConfig, registry: SinkRegistry | None = None) -> AlertSink:
    """Construct the sink a policy entry names.

    Raises:
        PolicyError: the kind is unknown or its options are wrong. Refused at construction
            rather than at the first delivery: a policy that only fails once an alert fires
            fails at the worst possible moment and leaves the alert in the outbox.
    """
    kinds = registry if registry is not None else default_registry()
    factory = kinds.get(config.kind)
    if factory is None:
        raise PolicyError(
            f"Sink {config.name!r} has kind {config.kind!r}, which this build does not "
            f"provide. Known kinds: {sorted(kinds)!r}.",
            field="kind",
        )
    built: AlertSink = factory(config)
    return built
