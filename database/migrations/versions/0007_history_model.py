r"""``object_versions``: validity intervals for every collected object.

Phases 0 through 6 kept the *latest* state of each object plus the provenance of the
observation that produced it. That answers "what is true now" and "who said so", and it
cannot answer "what was true on the 3rd" or "when did this stop being true", because a newer
observation overwrites the older one in place.

This revision adds the table that answers those, and backfills it.

## The shape, in one paragraph

One row per state an object was observed to hold: ``valid_from`` when it was first seen,
``last_seen_at`` when it was last confirmed, ``valid_to`` when something contradicted it,
NULL while it still holds. ``is_present`` false is a **tombstone** -- an authoritative scan
of a reconciled scope looked and did not find the object -- which is a different answer from
having no row at all, and only the tombstone may be rendered as a deletion. ``state`` is the
descriptive columns of the current-state row as JSONB, with provenance stripped;
``state_hash`` is the digest that decides whether the next observation is a change or a
repetition.

## Nothing is dropped, and no existing column changes

The current-state tables are untouched. They remain the projection every existing query
reads, so no Phase 0-6 query, index or plan changes shape, and the acceptance criterion that
current-state queries keep working is satisfied by construction rather than by re-testing
twenty repositories. History is additive.

## The backfill claims exactly what the old schema can support, and says so

Every existing row becomes **one** version, spanning ``first_observed_at`` to
``last_observed_at``, marked ``origin = 'backfilled'``. That is the most the previous schema
can justify: it kept two timestamps and one state, so an object that changed twice between
those instants left one row then and produces one version now.

Those versions are therefore *not* the same claim as an observed one, and the reader is told
so rather than left to assume: every point-in-time answer drawn from a backfilled version
reports :attr:`app.history.model.Certainty.BACKFILLED`. Marking them and reporting it is the
whole difference between reconstructing history and inventing it.

The backfill runs in Python rather than SQL because ``state_hash`` must be the digest of the
**canonical** rendering of the state -- sorted keys, tight separators, UTF-8 -- and
PostgreSQL's ``jsonb`` text output is neither sorted that way nor separated that way. The
canonicalization is duplicated here rather than imported from ``app.history.model``, because
a migration must keep doing what it did on the day it ran even after the application moves
on. ``tests/db/test_history_migration.py`` re-derives every backfilled digest with the
application's own function and asserts they agree, so the duplication cannot drift unnoticed.

Revision ID: 0007_history_model
Revises: 0006_effective_access
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from typing import Any, Final
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0007_history_model"
down_revision: str | None = "0006_effective_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY_LENGTH: Final = 512
STATE_DIGEST_LENGTH: Final = 64

OBJECT_KINDS: Final[tuple[str, ...]] = (
    "principal",
    "membership_edge",
    "server",
    "smb_share",
    "smb_ace",
    "ntfs_resource",
    "ntfs_ace",
)
VERSION_ORIGINS: Final[tuple[str, ...]] = ("observed", "backfilled")
CLOSE_REASONS: Final[tuple[str, ...]] = ("superseded", "absent")

#: Columns a version already carries in its own interval, so they are not stored inside the
#: state blob: ``first_observed_at`` is ``valid_from`` and ``last_observed_at`` is
#: ``last_seen_at``.
REDUNDANT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "first_observed_at",
        "first_observed_run_id",
        "last_observed_at",
        "last_observed_run_id",
        "created_at",
        "updated_at",
    }
)

#: Everything the digest ignores: the redundant columns, plus ``source_key``, which names the
#: *observation* rather than the object and so differs between two runs that read identical
#: facts. Digesting it would make every scan look like a change.
PROVENANCE_FIELDS: Final[frozenset[str]] = REDUNDANT_FIELDS | {"source_key"}

#: ``(object kind, source table, key column, container column, related column)``, frozen at
#: this revision. The container and related columns are projections of the state that make
#: "everything inside X as of T" and "everything pointing at Y as of T" indexed reads.
BACKFILL_SOURCES: Final[tuple[tuple[str, str, str, str | None, str | None], ...]] = (
    ("principal", "principals", "principal_key", "host_key", "domain_sid"),
    ("membership_edge", "membership_edges", "edge_key", "group_key", "member_key"),
    ("server", "servers", "server_key", None, None),
    ("smb_share", "smb_shares", "share_key", "server_key", None),
    ("smb_ace", "smb_share_aces", "ace_key", "share_key", "trustee_key"),
    ("ntfs_resource", "ntfs_resources", "resource_key", "share_key", "server_key"),
    ("ntfs_ace", "ntfs_aces", "ace_key", "resource_key", "trustee_key"),
)

BACKFILL_CHUNK: Final = 2_000
"""Rows per round trip. Small enough that a large estate does not build one enormous
parameter list, large enough that the migration is not one statement per object."""


def _enum_check(column: str, values: Sequence[str], *, nullable: bool = False) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    predicate = f"{column} IN ({listed})"
    return f"{column} IS NULL OR {predicate}" if nullable else predicate


def upgrade() -> None:
    op.create_table(
        "object_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("object_kind", sa.Text(), nullable=False),
        sa.Column("object_key", sa.String(length=KEY_LENGTH), nullable=False),
        sa.Column("container_key", sa.String(length=KEY_LENGTH), nullable=True),
        sa.Column("related_key", sa.String(length=KEY_LENGTH), nullable=True),
        sa.Column("is_present", sa.Boolean(), nullable=False),
        sa.Column("state", JSONB(), nullable=True),
        sa.Column("state_hash", sa.String(length=STATE_DIGEST_LENGTH), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False, server_default="observed"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("opened_by_run_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("last_seen_run_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("closed_by_run_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_enum_check("object_kind", OBJECT_KINDS), name="ck_object_kind_valid"),
        sa.CheckConstraint(_enum_check("origin", VERSION_ORIGINS), name="ck_origin_valid"),
        sa.CheckConstraint(
            _enum_check("close_reason", CLOSE_REASONS, nullable=True),
            name="ck_close_reason_valid",
        ),
        sa.CheckConstraint(
            "last_seen_at >= valid_from", name="ck_object_versions_confirmed_after_opened"
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= last_seen_at",
            name="ck_object_versions_closed_after_confirmed",
        ),
        sa.CheckConstraint(
            "(valid_to IS NULL) = (close_reason IS NULL)",
            name="ck_object_versions_closed_has_a_reason",
        ),
        sa.CheckConstraint(
            "(valid_to IS NULL) = (closed_by_run_id IS NULL)",
            name="ck_object_versions_closed_has_a_run",
        ),
        sa.CheckConstraint(
            "is_present = (state IS NOT NULL)", name="ck_object_versions_presence_matches_state"
        ),
        sa.CheckConstraint(
            "is_present = (state_hash IS NOT NULL)",
            name="ck_object_versions_presence_matches_digest",
        ),
        sa.CheckConstraint(
            f"state_hash IS NULL OR state_hash ~ '^[0-9a-f]{{{STATE_DIGEST_LENGTH}}}$'",
            name="ck_object_versions_state_hash_shape",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "object_kind", "object_key", "valid_from", name="uq_object_versions_identity"
        ),
        comment=(
            "Validity intervals for every collected object. One row per state an object was "
            "observed to hold; is_present false is a measured absence."
        ),
    )
    # At most one open version per object, enforced by the database. Two would make "what is
    # true now" return two contradictory rows, and the writer's own correctness depends on
    # being able to read "the open version" as a single row.
    op.create_index(
        "ux_object_versions_open",
        "object_versions",
        ["object_kind", "object_key"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        "ix_object_versions_container",
        "object_versions",
        ["object_kind", "container_key", "valid_from"],
        unique=False,
        postgresql_where=sa.text("container_key IS NOT NULL"),
    )
    op.create_index(
        "ix_object_versions_related",
        "object_versions",
        ["object_kind", "related_key", "valid_from"],
        unique=False,
        postgresql_where=sa.text("related_key IS NOT NULL"),
    )
    op.create_index(
        "ix_object_versions_closed_at",
        "object_versions",
        ["valid_to"],
        unique=False,
        postgresql_where=sa.text("valid_to IS NOT NULL"),
    )
    op.create_index(
        "ix_object_versions_last_seen_run", "object_versions", ["last_seen_run_id"], unique=False
    )

    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_object_versions_last_seen_run", table_name="object_versions")
    op.drop_index("ix_object_versions_closed_at", table_name="object_versions")
    op.drop_index("ix_object_versions_related", table_name="object_versions")
    op.drop_index("ix_object_versions_container", table_name="object_versions")
    op.drop_index("ux_object_versions_open", table_name="object_versions")
    op.drop_table("object_versions")


# --------------------------------------------------------------------------- backfill


def _digestible(value: Any) -> Any:
    """One JSON-representable form per value, chosen so the digest is stable.

    Timestamps are pinned to UTC before formatting: the same instant read back under a
    different session time zone must not digest differently.
    """
    if isinstance(value, dt.datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
        return moment.astimezone(dt.UTC).isoformat(timespec="microseconds")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _state_of(row: sa.RowMapping) -> dict[str, Any]:
    """What is stored: the row's descriptive columns plus the observation key."""
    return {key: _digestible(value) for key, value in row.items() if key not in REDUNDANT_FIELDS}


def _digest(state: dict[str, Any]) -> str:
    """The digest of a state, which ignores ``source_key`` even when the state carries it."""
    digested = {key: value for key, value in state.items() if key not in PROVENANCE_FIELDS}
    canonical = json.dumps(digested, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _backfill(connection: sa.Connection) -> None:
    """One open, backfilled version per existing row of every tracked table.

    The source tables are reflected rather than declared, because a migration should read
    whatever the database actually holds at the moment it runs; the destination is declared
    locally, so a column added to ``object_versions`` by a later revision cannot change what
    this one writes.
    """
    now = dt.datetime.now(tz=dt.UTC)
    metadata = sa.MetaData()
    destination = sa.table(
        "object_versions",
        sa.column("object_kind"),
        sa.column("object_key"),
        sa.column("container_key"),
        sa.column("related_key"),
        sa.column("is_present"),
        sa.column("state", JSONB(none_as_null=True)),
        sa.column("state_hash"),
        sa.column("origin"),
        sa.column("valid_from"),
        sa.column("last_seen_at"),
        sa.column("valid_to"),
        sa.column("close_reason"),
        sa.column("opened_by_run_id"),
        sa.column("last_seen_run_id"),
        sa.column("closed_by_run_id"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )

    for kind, table_name, key_column, container_column, related_column in BACKFILL_SOURCES:
        source = sa.Table(table_name, metadata, autoload_with=connection)
        # ``yield_per`` on the statement, never on the connection: setting it on the
        # connection would put every later statement behind a server-side cursor, and
        # PostgreSQL cannot DECLARE a cursor for an INSERT.
        statement = (
            sa.select(source)
            .order_by(source.c[key_column])
            .execution_options(yield_per=BACKFILL_CHUNK)
        )
        result = connection.execute(statement)
        for partition in result.mappings().partitions(BACKFILL_CHUNK):
            payload = []
            for row in partition:
                state = _state_of(row)
                payload.append(
                    {
                        "object_kind": kind,
                        "object_key": row[key_column],
                        "container_key": (
                            None if container_column is None else row[container_column]
                        ),
                        "related_key": None if related_column is None else row[related_column],
                        "is_present": True,
                        "state": state,
                        "state_hash": _digest(state),
                        "origin": "backfilled",
                        "valid_from": row["first_observed_at"],
                        "last_seen_at": row["last_observed_at"],
                        "valid_to": None,
                        "close_reason": None,
                        "opened_by_run_id": row["first_observed_run_id"],
                        "last_seen_run_id": row["last_observed_run_id"],
                        "closed_by_run_id": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            if payload:
                connection.execute(sa.insert(destination), payload)
