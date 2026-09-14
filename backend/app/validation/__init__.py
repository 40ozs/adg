"""Checking collector output before it is trusted.

A collector runs on a domain member somewhere and posts observations. When it gets
something wrong, the cheapest place to find out is on the machine that produced the output
— not in an audit report three weeks later. This package is that check: it reads the JSON a
collector would send, validates it the way the API would, and then looks at the *graph* the
observations describe for the hazards a per-observation check cannot see.

Nothing here talks to a database or a network. It is a pure function from documents to
findings, which is what lets it run in a collector's CI, on an engineer's laptop against a
captured transcript, and in ADG's own test suite against the committed fixtures.
"""

from __future__ import annotations

from app.validation.collector_output import (
    Finding,
    Report,
    Severity,
    validate_documents,
)

__all__ = [
    "Finding",
    "Report",
    "Severity",
    "validate_documents",
]
