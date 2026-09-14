"""Structured logging behavior."""

from __future__ import annotations

import json
import logging

from app.logging_config import JsonLogFormatter, configure_logging


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="adg.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="scan.completed",
        args=(),
        exc_info=None,
    )
    record.__dict__.update(extra)
    return record


def test_record_is_emitted_as_one_json_line() -> None:
    formatter = JsonLogFormatter(service="ADG", environment="test", version="0.1.0")

    output = formatter.format(make_record())

    assert "\n" not in output
    payload = json.loads(output)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "adg.test"
    assert payload["message"] == "scan.completed"
    assert payload["service"] == "ADG"
    assert payload["environment"] == "test"
    assert payload["version"] == "0.1.0"
    assert payload["timestamp"].endswith("+00:00")


def test_caller_supplied_fields_are_merged_into_the_payload() -> None:
    formatter = JsonLogFormatter(service="ADG", environment="test", version="0.1.0")

    payload = json.loads(formatter.format(make_record(scan_run_id=7, server="FS01")))

    assert payload["scan_run_id"] == 7
    assert payload["server"] == "FS01"


def test_non_serializable_values_do_not_break_formatting() -> None:
    formatter = JsonLogFormatter(service="ADG", environment="test", version="0.1.0")

    payload = json.loads(formatter.format(make_record(path=object())))

    assert isinstance(payload["path"], str)


def test_exception_information_is_captured() -> None:
    formatter = JsonLogFormatter(service="ADG", environment="test", version="0.1.0")
    try:
        raise ValueError("bad share path")
    except ValueError:
        import sys

        record = make_record()
        record.exc_info = sys.exc_info()

    payload = json.loads(formatter.format(record))

    assert "ValueError: bad share path" in payload["exception"]


def test_configure_logging_installs_exactly_one_handler() -> None:
    configure_logging(level="DEBUG", log_format="json")
    configure_logging(level="DEBUG", log_format="json")

    root = logging.getLogger()
    try:
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonLogFormatter)
        assert root.level == logging.DEBUG
    finally:
        configure_logging(level="INFO", log_format="text")


def test_text_format_is_available_for_local_debugging() -> None:
    configure_logging(level="INFO", log_format="text")

    root = logging.getLogger()
    assert not isinstance(root.handlers[0].formatter, JsonLogFormatter)


def test_uvicorn_access_log_suppression_is_not_undone() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.propagate = False  # what uvicorn does for access_log=False
    try:
        configure_logging(level="INFO", log_format="json")

        assert access_logger.propagate is False
        assert access_logger.handlers == []
    finally:
        access_logger.propagate = True
        configure_logging(level="INFO", log_format="text")
