"""PostgreSQL ``DISTINCT ON`` across SQLAlchemy 2.0 and 2.1.

Several queries pick "the first row per group" with ``DISTINCT ON (...) ... ORDER BY``:
the newest run per collection scope, the newest earlier version of each object, the first
present version of each container. PostgreSQL answers that in one ordered pass, and the
semantics are exactly the ones those queries mean, so they keep using it.

What changed is how SQLAlchemy spells it. 2.0 has only ``Select.distinct(*expr)``. 2.1
adds ``Select.ext(postgresql.distinct_on(*expr))`` and deprecates passing expressions to
``distinct()``; the test suite turns deprecation warnings raised from ``app`` into errors,
so the old spelling fails every query that reaches it. ``pyproject.toml`` still accepts
2.0, so the 2.1 spelling cannot be used unconditionally either.

:func:`distinct_on` is the one place that choice is made. Both branches render the same
``SELECT DISTINCT ON (...)`` and leave ``ORDER BY`` to the caller, who must still lead it
with the ``DISTINCT ON`` expressions, as PostgreSQL requires. Once the floor is raised to
2.1 the helper can be replaced by ``.ext(distinct_on(...))`` at each call site.
"""

from __future__ import annotations

from typing import Any, Final, TypeVar

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import ColumnElement, Select

__all__ = ["distinct_on"]

_SelectT = TypeVar("_SelectT", bound="Select[Any]")

#: ``postgresql.distinct_on`` on SQLAlchemy 2.1 and later, ``None`` on 2.0.
#:
#: Looked up rather than imported so the module still loads on 2.0. Type checking follows
#: the version CI resolves (2.1), where ``Select.ext`` exists.
_DISTINCT_ON_EXTENSION: Final[Any] = getattr(postgresql, "distinct_on", None)


def distinct_on(statement: _SelectT, *expressions: ColumnElement[Any]) -> _SelectT:
    """``statement`` with ``DISTINCT ON (expressions)`` applied.

    Generative, like the methods it wraps: the argument is not modified.
    """
    if _DISTINCT_ON_EXTENSION is None:
        return statement.distinct(*expressions)
    return statement.ext(_DISTINCT_ON_EXTENSION(*expressions))
