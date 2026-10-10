r"""Positively identifying a demo database before anything writes synthetic data into it.

Three independent conditions must hold before Demo Mode wipes or fills a database, and a
demo API checks the same ones before it serves:

1. ``ADG_RUNTIME_MODE=demo`` (:mod:`app.runtime_mode`).
2. The database is *named* as a demo database (``app.config.DEMO_DATABASE_NAME``).
3. The database *says* it is a demo database: its ``deployment_marker`` row reads
   ``runtime_mode = 'demo'``.

The third is the one that cannot be satisfied by editing a connection string. A database
gets its marker in exactly one way -- the demo reset initializing it -- and the reset will
only initialize a database that holds **nothing at all**. An unmarked database with any row
in any ADG table is refused, because it might be a production database whose operator never
marked it, and that is exactly the database a mistake would point a demo at.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.demo_mode.datasets import DemoDatasetMetadata
from app.models.schema import deployment_marker, metadata
from app.runtime_mode import RuntimeMode

__all__ = [
    "DemoTargetRefused",
    "Marker",
    "TargetInspection",
    "inspect_target",
    "lock_and_require_demo",
    "require_demo_database",
    "write_marker",
]


class DemoTargetRefused(RuntimeError):
    """The database is not positively a demo database. Nothing was written."""


@dataclass(frozen=True, slots=True)
class Marker:
    runtime_mode: str
    dataset_key: str | None
    dataset_seed: str | None
    marked_at: dt.datetime
    marked_by: str


@dataclass(frozen=True, slots=True)
class TargetInspection:
    """What a database says about itself, read without changing it."""

    database_name: str
    schema_present: bool
    marker: Marker | None
    populated_tables: tuple[str, ...]

    @property
    def is_demo(self) -> bool:
        return self.marker is not None and self.marker.runtime_mode == RuntimeMode.DEMO.value

    @property
    def is_empty(self) -> bool:
        return self.schema_present and not self.populated_tables


async def inspect_target(connection: AsyncConnection) -> TargetInspection:
    """Read the marker and whether any ADG table holds a row. Writes nothing."""
    name = str((await connection.execute(sa.text("SELECT current_database()"))).scalar_one())
    present = bool(
        (
            await connection.execute(
                sa.text("SELECT to_regclass('public.deployment_marker') IS NOT NULL")
            )
        ).scalar_one()
    )
    if not present:
        return TargetInspection(name, schema_present=False, marker=None, populated_tables=())

    row = (await connection.execute(sa.select(deployment_marker))).mappings().first()
    marker = (
        Marker(
            runtime_mode=row["runtime_mode"],
            dataset_key=row["dataset_key"],
            dataset_seed=row["dataset_seed"],
            marked_at=row["marked_at"],
            marked_by=row["marked_by"],
        )
        if row is not None
        else None
    )
    populated: list[str] = []
    for table in metadata.sorted_tables:
        if table is deployment_marker:
            continue
        if (await connection.execute(sa.select(sa.literal(1)).select_from(table).limit(1))).first():
            populated.append(table.name)
    return TargetInspection(
        name, schema_present=True, marker=marker, populated_tables=tuple(populated)
    )


def require_demo_database(inspection: TargetInspection, *, allow_initialize: bool) -> None:
    """Refuse unless the database is marked demo -- or, if ``allow_initialize``, is empty.

    Raises:
        DemoTargetRefused: with a message saying which condition failed and what to do.
    """
    where = f"Database {inspection.database_name!r}"
    if not inspection.schema_present:
        if allow_initialize:
            return
        raise DemoTargetRefused(
            f"{where} has no ADG schema. Initialize it with `python -m app.demo_mode reset`."
        )
    if inspection.marker is not None:
        if inspection.is_demo:
            return
        raise DemoTargetRefused(
            f"{where} is marked runtime_mode={inspection.marker.runtime_mode!r} (by "
            f"{inspection.marker.marked_by} at {inspection.marker.marked_at.isoformat()}). "
            "Demo Mode never writes to, or serves from, a database marked for another mode."
        )
    if inspection.populated_tables:
        shown = ", ".join(inspection.populated_tables[:5])
        more = len(inspection.populated_tables) - 5
        raise DemoTargetRefused(
            f"{where} carries no deployment marker but already holds data ({shown}"
            f"{f', and {more} more tables' if more > 0 else ''}). It may be a production or "
            "development database, so Demo Mode will not mark it, wipe it or serve from it. "
            "Point ADG_DATABASE_URL at a new, empty database for the demo."
        )
    if not allow_initialize:
        raise DemoTargetRefused(
            f"{where} is empty and not yet marked as a demo database. Initialize it with "
            "`python -m app.demo_mode reset`."
        )


async def lock_and_require_demo(connection: AsyncConnection) -> Marker:
    """Inside a transaction: lock the marker row and require that it says demo.

    Taken immediately before the wipe, in the same transaction, so that no other process can
    change what the database claims to be between the check and the truncation.
    """
    row = (
        (await connection.execute(sa.select(deployment_marker).with_for_update()))
        .mappings()
        .first()
    )
    if row is None or row["runtime_mode"] != RuntimeMode.DEMO.value:
        raise DemoTargetRefused(
            "The deployment marker no longer reads runtime_mode='demo'. Nothing was wiped."
        )
    return Marker(
        runtime_mode=row["runtime_mode"],
        dataset_key=row["dataset_key"],
        dataset_seed=row["dataset_seed"],
        marked_at=row["marked_at"],
        marked_by=row["marked_by"],
    )


async def write_marker(
    connection: AsyncConnection, dataset: DemoDatasetMetadata, *, marked_by: str
) -> None:
    """Mark the database demo, naming the dataset it holds. Idempotent per dataset."""
    values = {
        "marker_id": 1,
        "runtime_mode": RuntimeMode.DEMO.value,
        "dataset_key": dataset.key,
        "dataset_seed": dataset.seed,
        "marked_at": dt.datetime.now(tz=dt.UTC),
        "marked_by": marked_by,
    }
    existing = (await connection.execute(sa.select(deployment_marker))).mappings().first()
    if existing is None:
        await connection.execute(sa.insert(deployment_marker).values(**values))
    elif existing["runtime_mode"] != RuntimeMode.DEMO.value:
        raise DemoTargetRefused("Refusing to re-mark a database marked for another mode.")
    else:
        await connection.execute(
            sa.update(deployment_marker)
            .where(deployment_marker.c.marker_id == 1)
            .values(dataset_key=dataset.key, dataset_seed=dataset.seed)
        )
