"""Domain validation failures.

Domain types validate on construction, so an invalid fact can never enter the system
silently. Every message must name the offending value and say what was expected: these
errors are read by an operator looking at a collector diagnostic, not only by a developer.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every error raised by the ADG domain model."""


class DomainValidationError(DomainError, ValueError):
    """An input violated a domain invariant.

    Subclasses ``ValueError`` so that callers using ordinary validation handling (and
    pydantic, at the API boundary) treat it as a value problem rather than a crash.
    """

    def __init__(
        self, message: str, *, value: object | None = None, field: str | None = None
    ) -> None:
        self.value = value
        self.field = field
        super().__init__(message)
