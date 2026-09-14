"""Alembic environment for ADG.

The connection string is never stored in ``alembic.ini``; it is read from the same
``Settings`` object the API uses, so migrations and the running service can never disagree
about which database they mean.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models.schema import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The physical schema is declared once, in app/models/schema.py. Pointing Alembic at it
# makes `alembic revision --autogenerate` and `alembic check` meaningful, and a smoke test
# reflects the live database and compares it against the same metadata so a hand-edited
# migration cannot drift from the declaration.
target_metadata = metadata


def _database_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (``alembic upgrade head --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
