"""The migration builds exactly what the schema module declares, and the constraints bite.

``app/models/schema.py`` is the declaration and the revisions under
``database/migrations/versions`` are the migrations. Nothing
stops the two from drifting except a test that reflects the database the migration actually
produced and compares it to the declaration — so that is what this does, plus a check that
each invariant encoded as a constraint is enforced by the database rather than only by
application code that could be bypassed.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schema import (
    membership_edges,
    metadata,
    principal_references,
    principals,
    smb_share_aces,
    smb_shares,
)

RUN_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
NOW = dt.datetime(2026, 9, 14, tzinfo=dt.UTC)


def reflected(url: str) -> sa.MetaData:
    engine = sa.create_engine(url)
    live = sa.MetaData()
    try:
        live.reflect(bind=engine)
    finally:
        engine.dispose()
    return live


class TestMigrationMatchesDeclaration:
    def test_every_declared_table_exists(self, migrated_database: str) -> None:
        live = reflected(migrated_database)
        declared = {table.name for table in metadata.sorted_tables}
        assert declared <= set(live.tables), (
            f"missing from the database: {sorted(declared - set(live.tables))}"
        )

    def test_every_declared_column_exists_with_the_declared_nullability(
        self, migrated_database: str
    ) -> None:
        live = reflected(migrated_database)
        mismatches: list[str] = []
        for table in metadata.sorted_tables:
            actual = live.tables[table.name]
            for column in table.columns:
                if column.name not in actual.columns:
                    mismatches.append(f"{table.name}.{column.name} is missing")
                    continue
                if actual.columns[column.name].nullable != column.nullable:
                    mismatches.append(
                        f"{table.name}.{column.name} nullable="
                        f"{actual.columns[column.name].nullable}, declared {column.nullable}"
                    )
        assert not mismatches, mismatches

    def test_every_declared_index_exists(self, migrated_database: str) -> None:
        live = reflected(migrated_database)
        missing: list[str] = []
        for table in metadata.sorted_tables:
            actual_names = {index.name for index in live.tables[table.name].indexes}
            for index in table.indexes:
                if index.name not in actual_names:
                    missing.append(f"{table.name}.{index.name}")
        assert not missing, missing

    def test_both_membership_endpoints_are_indexed(self, migrated_database: str) -> None:
        """Traversal runs in both directions; one index would make half of it a seq scan."""
        live = reflected(migrated_database)
        columns = {tuple(index.columns.keys()) for index in live.tables["membership_edges"].indexes}
        assert ("group_key", "member_key") in columns
        assert ("member_key", "group_key") in columns

    def test_the_idempotency_keys_are_primary_keys(self, migrated_database: str) -> None:
        live = reflected(migrated_database)
        assert list(live.tables["scan_run_batches"].primary_key.columns.keys()) == [
            "run_id",
            "batch_id",
        ]
        assert list(live.tables["observations"].primary_key.columns.keys()) == [
            "run_id",
            "source_key",
        ]

    def test_every_timestamp_column_is_timezone_aware(self, migrated_database: str) -> None:
        """A naive timestamp from another time zone would silently reorder history."""
        live = reflected(migrated_database)
        naive: list[str] = []
        for table in metadata.sorted_tables:
            for column in live.tables[table.name].columns:
                if isinstance(column.type, sa.DateTime) and not column.type.timezone:
                    naive.append(f"{table.name}.{column.name}")
        assert not naive, naive


class TestConstraintsAreEnforcedByTheDatabase:
    async def _insert_principal(self, session: AsyncSession, **overrides: object) -> None:
        values = {
            "principal_key": "S-1-5-21-1-2-3-1000",
            "sid": "S-1-5-21-1-2-3-1000",
            "principal_kind": "user",
            "host_key": None,
            "is_deleted": False,
            "source_key": "principal|S-1-5-21-1-2-3-1000",
            "first_observed_at": NOW,
            "first_observed_run_id": RUN_ID,
            "last_observed_at": NOW,
            "last_observed_run_id": RUN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            **overrides,
        }
        await session.execute(sa.insert(principals).values(values))

    async def test_an_invalid_principal_kind_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_principal(session, principal_kind="wizard")

    async def test_a_local_group_without_a_host_is_refused(self, session: AsyncSession) -> None:
        # S-1-5-32-544 is byte-identical on every Windows computer; an unscoped row would
        # merge every server's local administrators into one group.
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_principal(
                session,
                principal_key="S-1-5-32-544",
                sid="S-1-5-32-544",
                principal_kind="local_group",
                host_key=None,
            )

    async def test_a_host_on_a_non_local_group_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_principal(session, host_key="fs01")

    async def test_an_unresolved_principal_may_not_carry_a_display_name(
        self, session: AsyncSession
    ) -> None:
        # A guessed name on an unresolved SID could be mistaken for a real resolution.
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_principal(
                session, principal_kind="unresolved", display_name="Probably Bob"
            )

    async def test_a_self_edge_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await session.execute(
                sa.insert(membership_edges).values(
                    edge_key="self",
                    group_key="S-1-5-21-1-2-3-1000",
                    member_key="S-1-5-21-1-2-3-1000",
                    group_sid="S-1-5-21-1-2-3-1000",
                    member_sid="S-1-5-21-1-2-3-1000",
                    edge_kind="directory_group_member",
                    is_foreign_security_principal=False,
                    source_key="edge|self",
                    first_observed_at=NOW,
                    first_observed_run_id=RUN_ID,
                    last_observed_at=NOW,
                    last_observed_run_id=RUN_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

    async def test_a_local_group_edge_without_a_host_is_refused(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await session.execute(
                sa.insert(membership_edges).values(
                    edge_key="unscoped",
                    group_key="S-1-5-32-544",
                    member_key="S-1-5-21-1-2-3-1000",
                    group_sid="S-1-5-32-544",
                    member_sid="S-1-5-21-1-2-3-1000",
                    edge_kind="local_group_member",
                    host_key=None,
                    is_foreign_security_principal=False,
                    source_key="edge|unscoped",
                    first_observed_at=NOW,
                    first_observed_run_id=RUN_ID,
                    last_observed_at=NOW,
                    last_observed_run_id=RUN_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )


class TestResourceConstraintsAreEnforcedByTheDatabase:
    """The SMB invariants, checked where the application layer cannot be bypassed.

    Each of these is a fact that would be wrong in a way nobody notices: a share keyed to
    one server and labelled with another's name, an ACE claiming both a level and a mask, a
    mask silently truncated to 31 bits.
    """

    async def _insert_share(self, session: AsyncSession, **overrides: object) -> None:
        values = {
            "share_key": "fs01|finance",
            "server_key": "fs01",
            "name": "Finance",
            "share_type": "disk",
            "source_key": "share|fs01|finance",
            "first_observed_at": NOW,
            "first_observed_run_id": RUN_ID,
            "last_observed_at": NOW,
            "last_observed_run_id": RUN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            **overrides,
        }
        await session.execute(sa.insert(smb_shares).values(values))

    async def _insert_ace(self, session: AsyncSession, **overrides: object) -> None:
        values = {
            "ace_key": "fs01|finance|S-1-1-0|allow|read",
            "share_key": "fs01|finance",
            "trustee_sid": "S-1-1-0",
            "trustee_key": "S-1-1-0",
            "ace_type": "allow",
            "permission": "read",
            "right_token": "read",
            "source_key": "smb_ace|fs01|finance|S-1-1-0|allow|read",
            "first_observed_at": NOW,
            "first_observed_run_id": RUN_ID,
            "last_observed_at": NOW,
            "last_observed_run_id": RUN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            **overrides,
        }
        await session.execute(sa.insert(smb_share_aces).values(values))

    async def test_a_share_key_must_start_with_its_server(self, session: AsyncSession) -> None:
        # Otherwise a share could be listed under FS01 while its identity says FS02.
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_share(session, server_key="fs02")

    async def test_an_invalid_share_type_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_share(session, share_type="tape")

    async def test_a_negative_user_limit_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_share(session, concurrent_user_limit=-1)

    async def test_an_ace_key_must_start_with_its_share(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_ace(session, share_key="fs01|payroll")

    async def test_an_ace_may_not_claim_both_a_level_and_a_mask(
        self, session: AsyncSession
    ) -> None:
        # A source reported one form or the other. Holding both would fabricate a reading.
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_ace(session, access_mask=0x001200A9)

    async def test_an_ace_must_carry_one_of_them(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_ace(session, permission=None, right_token="0x00000000")

    async def test_an_unsigned_32_bit_mask_fits(self, session: AsyncSession) -> None:
        # The column is bigint precisely so that 0xFFFFFFFF does not overflow into -1.
        await self._insert_ace(
            session, permission=None, access_mask=0xFFFFFFFF, right_token="0xffffffff"
        )

        stored = (await session.execute(sa.select(smb_share_aces.c.access_mask))).scalar_one()
        assert stored == 0xFFFFFFFF

    async def test_a_mask_beyond_32_bits_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_ace(
                session, permission=None, access_mask=0x1_0000_0000, right_token="0x100000000"
            )

    async def test_an_invalid_ace_type_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_ace(session, ace_type="audit")

    async def test_a_negative_order_index_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await self._insert_ace(session, order_index=-1)

    async def test_an_ace_needs_no_share_row(self, session: AsyncSession) -> None:
        # No foreign key, on purpose: an ACL read before the share list is still a fact,
        # and refusing it would discard evidence a partial scan did manage to collect.
        await self._insert_ace(session)

        stored = (
            await session.execute(sa.select(sa.func.count()).select_from(smb_share_aces))
        ).scalar_one()
        assert stored == 1

    async def test_a_share_needs_no_server_row(self, session: AsyncSession) -> None:
        await self._insert_share(session)

        stored = (
            await session.execute(sa.select(sa.func.count()).select_from(smb_shares))
        ).scalar_one()
        assert stored == 1

    async def test_one_principal_is_referenced_by_one_resource_only_once(
        self, session: AsyncSession
    ) -> None:
        values = {
            "principal_key": "S-1-1-0",
            "sid": "S-1-1-0",
            "reference_kind": "smb_ace",
            "reference_key": "fs01|finance",
            "first_observed_at": NOW,
            "first_observed_run_id": RUN_ID,
            "last_observed_at": NOW,
            "last_observed_run_id": RUN_ID,
            "created_at": NOW,
            "updated_at": NOW,
        }
        await session.execute(sa.insert(principal_references).values(values))

        with pytest.raises(sa.exc.IntegrityError):
            await session.execute(sa.insert(principal_references).values(values))

    async def test_an_unknown_reference_kind_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await session.execute(
                sa.insert(principal_references).values(
                    principal_key="S-1-1-0",
                    sid="S-1-1-0",
                    reference_kind="gpo",
                    reference_key="fs01|finance",
                    first_observed_at=NOW,
                    first_observed_run_id=RUN_ID,
                    last_observed_at=NOW,
                    last_observed_run_id=RUN_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
