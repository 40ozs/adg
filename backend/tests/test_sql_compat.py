"""``DISTINCT ON`` through :func:`app.sql_compat.distinct_on`, compiled rather than executed.

The behavior of each query that uses it is pinned against PostgreSQL in
``tests/db/test_distinct_on_queries.py``. These hold the helper's own contract on whichever
SQLAlchemy is installed: it renders ``DISTINCT ON`` for exactly the expressions given, it
does so without the deprecation warning SQLAlchemy 2.1 attaches to ``distinct(*expr)``, and
it leaves the statement it was given untouched.
"""

from __future__ import annotations

import warnings

from sqlalchemy import Column, Integer, MetaData, Table, Text, create_engine, func, select
from sqlalchemy.sql import ClauseElement

from app.sql_compat import distinct_on

#: The dialect the application runs on. Building an engine does not connect.
DIALECT = create_engine("postgresql+psycopg://").dialect

runs = Table(
    "runs",
    MetaData(),
    Column("id", Integer),
    Column("collector", Text),
    Column("target", Text),
    Column("started_at", Integer),
)


def _sql(statement: ClauseElement) -> str:
    compiled = statement.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
    return " ".join(str(compiled).split())


def test_renders_distinct_on_for_the_given_expressions() -> None:
    target = func.coalesce(runs.c.target, "")
    statement = distinct_on(select(runs.c.id), runs.c.collector, target).order_by(
        runs.c.collector, target, runs.c.started_at.desc()
    )

    assert _sql(statement) == (
        "SELECT DISTINCT ON (runs.collector, coalesce(runs.target, '')) runs.id "
        "FROM runs ORDER BY runs.collector, coalesce(runs.target, ''), runs.started_at DESC"
    )


def test_raises_no_deprecation_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _sql(distinct_on(select(runs.c.id), runs.c.collector))


def test_leaves_the_original_statement_alone() -> None:
    plain = select(runs.c.id)

    distinct_on(plain, runs.c.collector)

    assert _sql(plain) == "SELECT runs.id FROM runs"
