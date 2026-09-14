"""Keeping credentials out of the log stream — and identifiers in it.

Two halves, and the second matters as much as the first. A redaction rule that is too eager
destroys the log: ADG's whole subject is *who* and *what*, so a SID, a UNC path, an account
name and an ACL hash have to survive verbatim — they are what an operator correlates against
a Windows event log. :class:`TestWhatMustSurvive` is the half that stops a future rule from
being widened until the logs stop being useful.

The rules themselves are tested through :func:`app.logging_redaction.redact` and again
through a real handler, because the filter has to reach four places a rule can be bypassed:
the message, a ``%s`` argument, an ``extra=`` value nested inside a dict, and the traceback
rendered from ``exc_info`` at format time.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest

from app.logging_config import configure_logging
from app.logging_redaction import REDACTED, RedactingFilter, redact, redact_value

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJl"
DSN = "postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg"


class TestWhatIsRemoved:
    @pytest.mark.parametrize(
        "line",
        [
            f"Authorization: Bearer {JWT}",
            f"Authorization: Bearer {'a' * 40}",
            f'{{"Authorization": "Bearer {JWT}"}}',
            f"authorization={JWT}",
        ],
    )
    def test_an_authorization_header_never_survives(self, line: str) -> None:
        cleaned = redact(line)

        assert REDACTED in cleaned
        assert JWT not in cleaned
        assert "a" * 40 not in cleaned

    def test_an_authorization_header_is_redacted_exactly_once(self) -> None:
        """The regression this ordering exists for: with the bare-bearer rule first,
        'Authorization: Bearer abc' became 'Authorization: [redacted] [redacted]' — one rule
        taking the token and the next taking the word 'Bearer' left behind."""
        cleaned = redact(f"Authorization: Bearer {JWT}")

        assert cleaned == f"Authorization: {REDACTED}"

    def test_a_bare_jwt_in_prose_is_removed(self) -> None:
        assert JWT not in redact(f"the client presented {JWT} and it verified")

    def test_a_bearer_token_without_a_header_around_it_is_removed(self) -> None:
        assert redact(f"presented 'Bearer {'x' * 30}'") == f"presented 'Bearer {REDACTED}'"

    def test_a_password_in_a_connection_string_is_removed_and_the_rest_is_not(self) -> None:
        """The user, host, port and database are exactly what an operator needs to diagnose
        a connection failure. Redacting them would make the line useless without making it
        safer."""
        cleaned = redact(f"could not connect: {DSN}")

        assert "adg_dev_password" not in cleaned
        assert "postgresql+psycopg://adg:" in cleaned
        assert "@localhost:5432/adg" in cleaned

    @pytest.mark.parametrize(
        "line",
        [
            "ADG_COLLECTOR_API_KEYS=fs01:a-collector-key-long-enough",
            "ADG_DEV_AUTH_SECRET=an-actual-secret-value",
            "ADG_OIDC_CLIENT_SECRET=another-secret",
            'X-ADG-Collector-Key: "a-collector-key"',
            "password=hunter2; host=db",
            "api_key: abcdef123456",
        ],
    )
    def test_a_labelled_secret_is_removed(self, line: str) -> None:
        cleaned = redact(line)

        assert REDACTED in cleaned
        for secret in ("a-collector-key", "an-actual-secret", "another-secret", "hunter2"):
            assert secret not in cleaned

    def test_the_setting_name_survives_so_the_line_still_says_what_to_fix(self) -> None:
        """'ADG_COLLECTOR_API_KEYS=[redacted]' tells an operator which setting was involved.
        'COLLECTOR_API_KEYS=[redacted]' does not, and 'API_KEYS=[redacted]' names nothing at
        all."""
        assert redact("ADG_COLLECTOR_API_KEYS=x1234567") == f"ADG_COLLECTOR_API_KEYS={REDACTED}"

    def test_a_redacted_value_is_not_redacted_again(self) -> None:
        """Without the guard, 'token=<jwt>' became 'token=[redacted]]' — the second rule
        stopping at the ']' the first had just written."""
        assert redact(f"token={JWT}") == f"token={REDACTED}"

    def test_redaction_is_idempotent(self) -> None:
        once = redact(f"Authorization: Bearer {JWT} against {DSN}")

        assert redact(once) == once


class TestWhatMustSurvive:
    @pytest.mark.parametrize(
        "line",
        [
            "subject S-1-5-21-1004336348-1177238915-682003330-1104 holds Modify",
            r"resource \\FS01\Finance\Payroll is a boundary",
            r"trustee CORP\alice via Finance-RW",
            "acl_hash f140a24394d694c091ea4aee1c3aca4a5c05dcbc5068f97e9d47cf155772d12a",
            "run 9822f9f3-a52c-53f5-81bd-94b369571c05 applied 88 observations",
            "GET /api/v1/access/explain status=200 duration_ms=41.2",
            "request_id=8f14e45fceea167a5a36dedd4bea2543",
            "share fs01|finance ace_key fs01|finance|S-1-1-0|allow|full",
        ],
    )
    def test_an_identifier_is_not_a_credential(self, line: str) -> None:
        """These are what an operator correlates a log line against. A rule widened until it
        eats them makes the log safe and worthless."""
        assert redact(line) == line

    def test_a_field_merely_called_key_is_not_redacted(self) -> None:
        """``key`` is the name of a *storage* key throughout this application — it is on
        every principal, share and resource view — and redacting it would blank most of the
        data the API logs about itself."""
        assert redact("key=fs01|finance") == "key=fs01|finance"


class TestValuesInsideContainers:
    def test_a_nested_mapping_is_walked(self) -> None:
        """An ``extra=`` value is routinely a dict, which is exactly where a header dump
        ends up."""
        cleaned = redact_value({"headers": {"Authorization": f"Bearer {JWT}", "Accept": "json"}})

        assert cleaned["headers"]["Authorization"] == REDACTED
        assert cleaned["headers"]["Accept"] == "json"

    @pytest.mark.parametrize(
        "key", ["Authorization", "password", "client_secret", "ADG_COLLECTOR_API_KEYS", "id_token"]
    )
    def test_a_key_that_names_a_secret_redacts_its_value_unread(self, key: str) -> None:
        """The only way to catch a credential whose text carries no label of its own, which
        is the normal case in a structured payload: the label is the key and the secret is
        the value, so no key=value rule has anything to anchor on."""
        assert redact_value({key: "anything-at-all"})[key] == REDACTED

    def test_a_field_called_key_keeps_its_value(self) -> None:
        """``key`` is a storage key on every principal, share and resource view in this
        application. Redacting it would blank most of what the API logs about itself."""
        assert redact_value({"key": "fs01|finance"}) == {"key": "fs01|finance"}
        assert redact_value({"share_key": "fs01|finance"}) == {"share_key": "fs01|finance"}

    def test_a_list_is_walked_and_stays_a_list(self) -> None:
        cleaned = redact_value([f"token={JWT}", "fine"])

        assert cleaned == [f"token={REDACTED}", "fine"]

    def test_a_tuple_stays_a_tuple(self) -> None:
        assert redact_value(("a", "b")) == ("a", "b")

    def test_scalars_pass_through_unchanged(self) -> None:
        assert redact_value(42) == 42
        assert redact_value(None) is None
        assert redact_value(True) is True


class TestTheFilterOnARealHandler:
    @pytest.fixture
    def stream(self) -> io.StringIO:
        configure_logging(level="INFO", log_format="json", environment="test", version="0")
        buffer = io.StringIO()
        # The handler is a StreamHandler; `logging.Handler` does not declare
        # `stream`, so the redirect is asserted rather than assumed.
        handler = logging.getLogger().handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        handler.stream = buffer
        return buffer

    def lines(self, stream: io.StringIO) -> list[dict[str, Any]]:
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    def test_the_rendered_message_is_redacted(self, stream: io.StringIO) -> None:
        logging.getLogger("probe").info("connecting to %s", DSN)

        assert "adg_dev_password" not in self.lines(stream)[0]["message"]

    def test_a_format_argument_is_redacted_after_it_is_joined(self, stream: io.StringIO) -> None:
        """'%s' with a token argument is only a credential once the two are joined, so the
        filter renders first and redacts second."""
        logging.getLogger("probe").info("token=%s", JWT)

        assert JWT not in self.lines(stream)[0]["message"]

    def test_an_extra_value_is_redacted(self, stream: io.StringIO) -> None:
        logging.getLogger("probe").info(
            "request", extra={"headers": {"Authorization": f"Bearer {JWT}"}}
        )

        assert JWT not in json.dumps(self.lines(stream)[0])

    def test_a_traceback_is_redacted(self, stream: io.StringIO) -> None:
        """Rendered from exc_info at format time, after every filter has run — so the
        formatter redacts it too, or a driver's DSN reaches the stream in a stack trace."""
        try:
            raise RuntimeError(f"connection to {DSN} refused")
        except RuntimeError:
            logging.getLogger("probe").exception("db down")

        assert "adg_dev_password" not in self.lines(stream)[0]["exception"]

    def test_the_text_format_redacts_a_traceback_too(self) -> None:
        """``ADG_LOG_FORMAT=text`` is what a developer runs locally, against a real database,
        with real credentials in the environment. It must not be the one format that prints
        a DSN in a stack trace."""
        configure_logging(level="INFO", log_format="text", environment="test", version="0")
        buffer = io.StringIO()
        handler = logging.getLogger().handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        handler.stream = buffer

        try:
            raise RuntimeError(f"connection to {DSN} refused")
        except RuntimeError:
            logging.getLogger("probe").exception("db down")

        assert "adg_dev_password" not in buffer.getvalue()
        assert REDACTED in buffer.getvalue()

    def test_the_filter_never_drops_a_record(self) -> None:
        """It redacts; it does not censor. A filter that returned False for a record it did
        not like would silently lose log lines."""
        record = logging.LogRecord("probe", logging.INFO, __file__, 1, "hello", None, None)

        assert RedactingFilter().filter(record) is True
        assert record.getMessage() == "hello"

    def test_a_record_with_a_broken_format_string_still_passes(self) -> None:
        """A '%s' with no argument raises inside getMessage. Logging must not become the
        thing that fails a request."""
        record = logging.LogRecord("probe", logging.INFO, __file__, 1, "%s %s", ("one",), None)

        assert RedactingFilter().filter(record) is True
