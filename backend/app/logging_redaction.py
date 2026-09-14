r"""Keeping credentials out of the log stream, wherever they came from.

Nothing in ADG deliberately logs a secret. That is not the same as no secret ever reaching a
log line, and the difference is the whole reason this module exists. Logs are the one place
in the system where data arrives from *everywhere* — a driver's exception message, a
third-party library's warning, a traceback frame holding a local variable, an ``extra=``
somebody adds next year — and no review of the call sites can cover all of it.

So the rule is applied at the handler, once, to everything: the rendered message, every
positional argument, every ``extra=`` value, and the formatted exception and stack text.
A call site that tries to log a token produces a line with ``[redacted]`` in it instead.

**Four shapes, and why each is here.**

* **A JWT.** The access token ADG issues and the one a tenant's identity provider issues are
  both JWTs; a logged one is a usable credential until it expires.
* **An ``Authorization`` header.** Whole-header logging is what a debugging session adds and
  forgets to remove, and it carries the token verbatim.
* **A URL with credentials in it.** ``postgresql+psycopg://adg:password@host/db`` is the
  most likely leak in this application: it is the value of ``ADG_DATABASE_URL``, and a
  driver that fails to connect may put the DSN in its own exception text. Only the password
  is removed — the user, host and database are exactly what an operator needs to diagnose
  the failure, and redacting them would make the log useless without making it safer.
* **A ``key=value`` secret.** ``password=``, ``secret=``, ``api_key=``, ``token=`` and
  friends, in the form that settings dumps and connection strings use.

**What is deliberately not redacted.** SIDs, UNC paths, account names and group names all
appear in logs and must: they are the identifiers an operator correlates against a Windows
event log, and ADG is an auditing tool whose whole subject is who and what. They are not
credentials. What they *are* is personal data, which is a retention question rather than a
redaction one, and it is answered in ``docs/operations/mvp-runbook.md``.

A redaction pass is a safety net under careful call sites, never a substitute for them.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

__all__ = ["REDACTED", "RedactingFilter", "redact", "redact_value"]

REDACTED: Final = "[redacted]"

#: Rendered once. Each pattern keeps whatever context makes the line still useful.
#: The value of a labelled secret: everything up to the delimiter that ends it. The class
#: excludes whitespace and the JSON and shell punctuation that terminate a value, so a rule
#: never eats the rest of the line. ``(?!\[redacted\])`` stops an already-redacted value
#: being matched a second time — without it, ``token=<jwt>`` became ``token=[redacted]]``,
#: the later rule stopping at the ``]`` an earlier one had just written.
_VALUE: Final = r"(?!\[redacted\])[^\s,;\"'}\]]+"

#: The separator between a label and its value: ``=``, ``:``, with or without quotes.
_ASSIGN: Final = r"(\"?\s*[:=]\s*\"?)"

#: Applied in order. Each rule keeps whatever context leaves the line useful.
_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # An Authorization header, first, and swallowing an optional 'Bearer ' with the token.
    # Ordering matters: with the bare-bearer rule first, 'Authorization: Bearer abc' became
    # 'Authorization: [redacted] [redacted]' — one rule redacting the token and the next
    # redacting the word 'Bearer' that was left behind.
    (
        re.compile(r"(?i)\b(authorization)" + _ASSIGN + r"(?:bearer\s+)?" + _VALUE),
        r"\1\2" + REDACTED,
    ),
    # A bearer token with no header around it, as a message often carries.
    (
        re.compile(r"(?i)\b(bearer)\s+(?!\[redacted\])[A-Za-z0-9._~+/=-]{8,}"),
        r"\1 " + REDACTED,
    ),
    # A JWT on its own: three base64url segments, the first beginning with the encoded '{"'.
    # Separate from the two above so that an unlabelled token in a message is still caught.
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"),
        REDACTED,
    ),
    # A URL with a password in its authority. The user survives; the password does not.
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@"),
        r"\1:" + REDACTED + "@",
    ),
    # key=value and key: value secrets, in a connection string, a settings dump, a shell
    # transcript, or prose. An environment-variable prefix is matched *inside* the captured
    # group so that it survives: 'ADG_COLLECTOR_API_KEYS=[redacted]' tells an operator which
    # setting was involved, and 'COLLECTOR_API_KEYS=[redacted]' does not. The plural forms
    # are there because the settings this application has are spelled
    # ADG_COLLECTOR_API_KEYS and ADG_DEV_AUTH_SECRET.
    (
        re.compile(
            r"(?i)\b([a-z0-9_]*?"
            r"(?:passwords?|passwd|pwd|secrets?|client_secrets?|api[_-]?keys?"
            r"|access[_-]?keys?|collector[_-]?(?:api[_-]?)?keys?|tokens?|auth[_-]?secrets?))"
            + _ASSIGN
            + _VALUE
        ),
        r"\1\2" + REDACTED,
    ),
)

#: Record attributes the formatter reads but which are not caller data. ``msg`` and ``args``
#: are handled explicitly; the rest are integers, file names and thread ids.
_SKIP: Final = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "taskName",
        "thread",
        "threadName",
    }
)


def redact(text: str) -> str:
    """Every rule applied to one string."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


#: A mapping key whose *value* is a credential whatever that value looks like. Matched on
#: the key because a structured payload puts the label and the secret in different places:
#: ``{"Authorization": "Bearer abc"}`` has nothing in the value for a key=value rule to
#: anchor on, and the header dump is the single likeliest way a token reaches a log line.
#:
#: Deliberately narrow. ``key`` is absent because it is the name of a *storage* key on
#: every principal, share and resource view in this application, and redacting those would
#: blank most of what the API logs about itself.
_SECRET_KEYS: Final = re.compile(
    r"(?i)^[a-z0-9_.-]*?"
    r"(authorization|passwords?|passwd|pwd|secrets?|api[_-]?keys?|access[_-]?keys?"
    r"|collector[_-]?(?:api[_-]?)?keys?|auth[_-]?secrets?|access[_-]?tokens?"
    r"|id[_-]?tokens?|refresh[_-]?tokens?)$"
)


def redact_value(value: Any) -> Any:
    """``value`` with every string inside it redacted, structure preserved.

    Containers are walked because an ``extra=`` value is routinely a dict or a list, and a
    filter that only looked at top-level strings would miss exactly the payload most likely
    to carry a credential. Non-string scalars are returned unchanged: an integer cannot
    match a rule and rebuilding it would only cost time.

    A mapping key that names a secret (:data:`_SECRET_KEYS`) redacts its value outright,
    without looking at it. That is the only way to catch a credential whose text carries no
    label of its own — which is the normal case in a structured payload.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED
            if isinstance(key, str) and _SECRET_KEYS.match(key)
            else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_value(item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    """Redacts every record that passes through the handler it is installed on.

    A ``Filter`` rather than a ``Formatter`` because the two log formats this application
    supports — JSON and text — must not each carry their own copy of the rule. The filter
    runs before either, so a line is redacted the same way whichever one renders it.

    The record is mutated in place. That is what ``logging`` filters are for, and it is the
    only way to reach a record that a third-party handler might also be formatting.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Render the message first, then redact: '%s' with a token argument is only a
        # credential once the two are joined, and redacting the parts would miss it.
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - a broken format string is not ours to fix
            rendered = str(record.msg)
        record.msg = redact(rendered)
        record.args = None

        for key, value in list(record.__dict__.items()):
            if key in _SKIP or key.startswith("_"):
                continue
            redacted = redact_value(value)
            if redacted is not value:
                record.__dict__[key] = redacted

        # Exception and stack text are formatted from exc_info later. Anything already
        # cached on the record is redacted here; the formatter redacts what it renders.
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
