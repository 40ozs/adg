"""Two tables for proposals, kept away from everything a collector writes.

Phase 9A lets ADG answer *"if I take this group off the ACL, who loses access?"* without
touching Active Directory, a share, an NTFS descriptor, or a single collected row. That is a
property of the engine — the overlay is applied in memory, at the repository boundary — and
this revision is what makes a proposal outlive the request that computed it.

`simulations` holds the proposal itself and the collection state it was written against.
`simulation_evaluations` holds what one run of it produced. Neither is read by any collector,
any ingestion path, or any effective-access query: a simulated ACE is never written to
`ntfs_aces`, so no query against `ntfs_aces` can ever pick one up. The separation is physical
rather than conventional, which is the only kind that survives a future maintainer.

Three things worth knowing about the shape.

**The overlay is JSONB, not a normalized change table.** A change is only ever read back as
part of a whole overlay — nothing asks "which proposals touch this ACE" — and four per-kind
tables would be four places for validation that already lives, exactly once, in the overlay's
own constructors. The blob carries its own `document_version`, so a proposal written today is
refused rather than reinterpreted after the shape changes.

**`baseline_token` is the staleness test, and it is exact.** It is the collection-basis digest
(ADR-0017), which moves if and only if a collector has written something. A stored simulation
whose token still matches would compute the same impact list today; one whose token has moved
has to be re-run before it is believed. A timestamp could not say either of those things.

**`simulation_evaluations` has a foreign key, and the resource tables do not.** That rule is
about observations: a share whose server no run has described is a real reading, and a
constraint would reject it at the moment a partial scan most needs to record what it managed
to read. An evaluation is not an observation — it is written by this application, in one
transaction, after the proposal it belongs to — so an orphan is a defect rather than a partial
scan, and the database says so.

**This revision hangs off `0007_history_model`, not off the newest revision.** Phases 8 and
9 were built concurrently in one working tree, and the other phase's revisions were still
being renamed while this one was written; anchoring to a revision that is *committed* keeps
this file correct whatever those become. The graph therefore has more than one head until
somebody writes a merge revision, which is a one-line file and the right place to decide the
order the phases actually ran in.

Revision ID: 0011_simulation_overlays
Revises: 0007_history_model
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0011_simulation_overlays"
down_revision: str | None = "0007_history_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOKEN_LENGTH: Final = 32
"""Hex characters of a collection-basis token. Restated rather than imported, because a
migration must keep doing what it did on the day it ran even after the application moves on."""

BASELINE_KINDS: Final[tuple[str, ...]] = ("current", "as_of")


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[sa.DateTime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "simulations",
        sa.Column("simulation_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("overlay", JSONB(none_as_null=True), nullable=False),
        sa.Column("overlay_hash", sa.String(TOKEN_LENGTH), nullable=False),
        sa.Column("change_count", sa.Integer(), nullable=False),
        sa.Column("baseline_kind", sa.Text(), nullable=False),
        _timestamp("baseline_at", nullable=True),
        sa.Column("baseline_token", sa.String(TOKEN_LENGTH), nullable=False),
        sa.Column("baseline_run_id", PgUUID(as_uuid=True), nullable=True),
        _timestamp("baseline_captured_at"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "baseline_kind IN ('" + "', '".join(BASELINE_KINDS) + "')",
            name="ck_baseline_kind_valid",
        ),
        sa.CheckConstraint("change_count >= 0", name="ck_simulations_change_count_non_negative"),
        sa.CheckConstraint(
            "(baseline_kind = 'as_of') = (baseline_at IS NOT NULL)",
            name="ck_simulations_as_of_has_an_instant",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(overlay) = 'object'", name="ck_simulations_overlay_is_an_object"
        ),
        comment=(
            "Proposed changes to the estate. Read by the simulation engine and by nothing "
            "else; no collector, ingestion path or effective-access query reads this table."
        ),
    )
    op.create_index("ix_simulations_created_at", "simulations", ["created_at"])
    op.create_index("ix_simulations_overlay_hash", "simulations", ["overlay_hash"])

    op.create_table(
        "simulation_evaluations",
        sa.Column("evaluation_id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("simulations.simulation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("baseline_token", sa.String(TOKEN_LENGTH), nullable=False),
        sa.Column("stale_baseline", sa.Boolean(), nullable=False),
        sa.Column("pairs_evaluated", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("report", JSONB(none_as_null=True), nullable=False),
        _timestamp("computed_at"),
        _timestamp("created_at"),
        sa.CheckConstraint(
            "pairs_evaluated >= 0", name="ck_simulation_evaluations_pairs_non_negative"
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name="ck_simulation_evaluations_duration_non_negative"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(report) = 'object'",
            name="ck_simulation_evaluations_report_is_an_object",
        ),
        comment=(
            "What one simulation produced when it was run. Separate from the proposal so "
            "that re-running against newer collected state adds a row rather than "
            "overwriting one."
        ),
    )
    op.create_index(
        "ix_simulation_evaluations_simulation",
        "simulation_evaluations",
        ["simulation_id", "computed_at"],
    )


def downgrade() -> None:
    """Drop both tables. Every stored proposal is lost; no collected fact is touched.

    That asymmetry is the whole point of keeping them separate: undoing simulation removes
    simulations, and there is no path from here to a row a collector wrote.
    """
    op.drop_index("ix_simulation_evaluations_simulation", table_name="simulation_evaluations")
    op.drop_table("simulation_evaluations")
    op.drop_index("ix_simulations_overlay_hash", table_name="simulations")
    op.drop_index("ix_simulations_created_at", table_name="simulations")
    op.drop_table("simulations")
