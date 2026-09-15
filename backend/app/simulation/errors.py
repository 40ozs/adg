"""Failures that belong to simulation and to nothing else.

Both are subclasses of :class:`app.domain.DomainError`, so a caller that already handles
domain failures handles these; they are separate types because the two things that go wrong
in a simulation go wrong for opposite reasons and want opposite responses.
"""

from __future__ import annotations

from app.domain.errors import DomainError

__all__ = ["SimulationBoundsError", "SimulationUnsupportedRead"]


class SimulationUnsupportedRead(DomainError):
    """A read was asked of an overlay repository that does not simulate it.

    A programming error, not a user error, and raised rather than answered because the
    alternative is worse in a way nothing would notice: returning the live estate's rows
    under a simulation's name. An operator reading a what-if screen has no way to tell one
    row that came from the proposal from one that came from production, so the boundary is
    enforced where it can be — at the read.
    """


class SimulationBoundsError(DomainError):
    """A simulation was asked for more work than its bounds allow.

    Raised only when a *requested* bound exceeds a ceiling. Exhausting a bound during a run
    is not an error: it is reported as truncation on the result, because a partial answer
    that says it is partial is useful and an exception is not.
    """
