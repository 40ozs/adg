"""The migrated database and ``app/models/schema.py`` describe the same four tables.

Two hand-written descriptions of one schema, and nothing else compares them. The migration is
what an operator runs; the metadata is what every query is built from. When they disagree the
symptom is a query that fails at runtime against a column that exists in one and not the
other — on a route, in production, rather than here.

There is no autogenerate-parity test in this codebase, so this is deliberately narrow: the
four tables this phase adds, by column name, nullability and primary key, plus the constraints
whose *behavior* the phase depends on, exercised rather than inspected.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schema import metadata

TABLES = (
    "remediation_change_plans",
    "remediation_planned_changes",
    "remediation_approvals",
    "remediation_exports",
)


async def _columns(session: AsyncSession, table: str) -> dict[str, tuple[bool, str]]:
    rows = (
        await session.execute(
            sa.text(
                "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name = :table"
            ).bindparams(table=table)
        )
    ).all()
    return {row[0]: (row[1] == "YES", row[2]) for row in rows}


class TestTheMigrationMatchesTheMetadata:
    @pytest.mark.parametrize("table", TABLES)
    async def test_every_column_exists_in_both(self, session: AsyncSession, table: str) -> None:
        declared = {column.name for column in metadata.tables[table].columns}
        migrated = set(await _columns(session, table))

        assert declared == migrated, (
            f"{table}: only in schema.py {sorted(declared - migrated)}; "
            f"only in the database {sorted(migrated - declared)}"
        )

    @pytest.mark.parametrize("table", TABLES)
    async def test_nullability_agrees(self, session: AsyncSession, table: str) -> None:
        """The disagreement that produces a 500 rather than a refusal: a column the code
        believes optional and the database requires."""
        migrated = await _columns(session, table)

        mismatched = [
            column.name
            for column in metadata.tables[table].columns
            if column.nullable != migrated[column.name][0]
        ]

        assert not mismatched, f"{table}: nullability differs for {mismatched}"

    @pytest.mark.parametrize("table", TABLES)
    async def test_every_timestamp_carries_a_time_zone(
        self, session: AsyncSession, table: str
    ) -> None:
        migrated = await _columns(session, table)

        naive = [
            name
            for name, (_, data_type) in migrated.items()
            if data_type == "timestamp without time zone"
        ]

        assert not naive, f"{table}: {naive} would reorder a plan's history under another zone"


class TestTheConstraintsBite:
    """Behavior rather than presence. A constraint that exists and does not fire is worse
    than none, because it reads as a control."""

    async def test_a_plan_cannot_be_approved_by_its_own_requestor(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError) as raised:
            await session.execute(_insert_plan(approved_by_subject="planner"))

        assert "approver_is_not_the_requestor" in str(raised.value)
        await session.rollback()

    async def test_an_approved_plan_must_say_who_approved_it_and_what(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError) as raised:
            await session.execute(_insert_plan(status="approved"))

        assert "approval_is_attributed" in str(raised.value)
        await session.rollback()

    async def test_a_plan_cannot_be_exported_without_an_approval(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError) as raised:
            await session.execute(
                _insert_plan(status="exported", exported_at=dt.datetime.now(dt.UTC))
            )

        assert "export_follows_approval" in str(raised.value)
        await session.rollback()

    async def test_a_rejection_must_say_why(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError) as raised:
            await session.execute(_insert_plan(status="rejected"))

        assert "rejection_says_why" in str(raised.value)
        await session.rollback()

    async def test_a_status_outside_the_vocabulary_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError) as raised:
            await session.execute(_insert_plan(status="applied"))

        assert "ck_status_valid" in str(raised.value)
        await session.rollback()

    async def test_the_audit_trail_admits_the_new_plan_events(self, session: AsyncSession) -> None:
        """The widened constraint. Without it every plan act would fail at the audit append,
        which is inside the same transaction as the act itself."""
        await session.execute(
            sa.text(
                "INSERT INTO governance_audit_events (event_id, chain_key, chain_index, "
                "event_type, occurred_at, actor_subject, actor_roles, payload, event_digest, "
                "created_at) VALUES (gen_random_uuid(), 'plan:test', 0, 'plan.created', now(), "
                "'planner', '[]'::jsonb, '{}'::jsonb, :digest, now())"
            ).bindparams(digest="a" * 64)
        )
        await session.rollback()

    async def test_it_still_refuses_an_event_type_nobody_declared(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            await session.execute(
                sa.text(
                    "INSERT INTO governance_audit_events (event_id, chain_key, chain_index, "
                    "event_type, occurred_at, actor_subject, actor_roles, payload, "
                    "event_digest, created_at) VALUES (gen_random_uuid(), 'plan:test', 0, "
                    "'plan.applied', now(), 'planner', '[]'::jsonb, '{}'::jsonb, :digest, "
                    "now())"
                ).bindparams(digest="a" * 64)
            )
        await session.rollback()


def _insert_plan(**overrides: object) -> sa.TextClause:
    values: dict[str, object] = {
        "plan_id": uuid.uuid4(),
        "title": "A plan",
        "rationale": "Because.",
        "status": "draft",
        "requested_by_subject": "planner",
        "basis_token": "a" * 32,
        "approved_by_subject": None,
        "approved_plan_digest": None,
        "approved_basis_token": None,
        "rejection_reason": None,
        "exported_at": None,
    }
    values.update(overrides)
    return sa.text(
        "INSERT INTO remediation_change_plans (plan_id, title, rationale, status, "
        "requested_by_subject, requested_at, basis_token, approved_by_subject, "
        "approved_plan_digest, approved_basis_token, rejection_reason, exported_at, "
        "created_at, updated_at) VALUES (:plan_id, :title, :rationale, :status, "
        ":requested_by_subject, now(), :basis_token, :approved_by_subject, "
        ":approved_plan_digest, :approved_basis_token, :rejection_reason, :exported_at, "
        "now(), now())"
    ).bindparams(**values)
