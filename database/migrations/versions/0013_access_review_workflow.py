r"""The access-review workflow: a fifth decision, and a per-campaign comment requirement.

Phase 10A built the model a campaign is made of. This revision adds the two pieces of
vocabulary the reviewer's own workflow needs, and nothing else — no table, no index, no
backfill.

## `investigate` joins the decision vocabulary

``review_decisions.decision`` accepted ``certify``, ``revoke``, ``modify`` and ``abstain``.
It now also accepts ``investigate``.

It is not a synonym for ``abstain`` and the distinction is the reason it exists.
``abstain`` says *the wrong person was asked*, and the campaign owner's fix is to reassign
the item. ``investigate`` says *the right person was asked, and the answer cannot be given
until somebody looks into something* — the fix is an investigation, and it belongs to a
different person. Folded together, a queue of items awaiting follow-up would be
indistinguishable from a queue of misrouted ones.

``ck_review_decisions_reason_required`` is **not** touched, so an ``investigate`` decision
must carry a rationale exactly as ``revoke`` and ``abstain`` do: "needs investigation"
without saying what to investigate hands the next person nothing.

## `comment_requirement` makes the rationale rule stricter per campaign, never looser

The new column on ``review_campaigns`` takes ``standard`` (a rationale on every decision but
``certify`` — what every existing campaign does, and the server default) or ``always`` (a
rationale on every decision, ``certify`` included).

There is deliberately no third value making a rationale optional for a revocation.
``ck_review_decisions_reason_required`` is a **floor**, enforced in the database, and this
column may only add to it: a campaign setting able to switch off "a removal must say why"
would be a way to produce unexplained revocations one campaign at a time, and the person who
loses the access is the one who pays for that.

## Additive, and safe on a populated database

The column is ``NOT NULL`` with a server default, so existing rows take ``standard`` — which
is the behavior they already had — without a data migration. Widening a ``CHECK`` constraint
admits values that were previously refused and invalidates no stored row, so no existing
decision needs revisiting.

``downgrade()`` narrows the decision constraint again and **refuses to run** while any
``investigate`` decision exists rather than deleting or rewriting one. A downgrade that
quietly erased an attestation would be the exact failure the append-only trigger exists to
prevent, reached through the migration tool instead.

Revision ID: 0013_access_review_workflow
Revises: 0012_merge_concurrent_phases
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0013_access_review_workflow"
down_revision: str | None = "0012_merge_concurrent_phases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision. A migration must keep doing what it did on the day it ran even
# after the application's enums move on, so these are literals rather than imports — the
# rule 0007 and 0008 both state.
DECISION_KINDS_BEFORE: Final[tuple[str, ...]] = ("certify", "revoke", "modify", "abstain")
DECISION_KINDS_AFTER: Final[tuple[str, ...]] = (
    "certify",
    "revoke",
    "modify",
    "abstain",
    "investigate",
)
COMMENT_REQUIREMENTS: Final[tuple[str, ...]] = ("standard", "always")

#: The constraint name `_enum_check` generates for a column called `decision`.
DECISION_CHECK: Final = "ck_decision_valid"
COMMENT_CHECK: Final = "ck_comment_requirement_valid"


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.add_column(
        "review_campaigns",
        sa.Column(
            "comment_requirement",
            sa.Text(),
            nullable=False,
            server_default="standard",
        ),
    )
    op.create_check_constraint(
        COMMENT_CHECK,
        "review_campaigns",
        _in_list("comment_requirement", COMMENT_REQUIREMENTS),
    )

    # Widened, not replaced by something laxer: every value that was accepted is still
    # accepted, so no stored decision is revalidated against a rule it was not written under.
    op.drop_constraint(DECISION_CHECK, "review_decisions", type_="check")
    op.create_check_constraint(
        DECISION_CHECK,
        "review_decisions",
        _in_list("decision", DECISION_KINDS_AFTER),
    )


def downgrade() -> None:
    """Narrow the decision vocabulary again, refusing rather than destroying evidence.

    A downgrade over a database holding ``investigate`` decisions cannot recreate the old
    constraint without removing or rewriting those rows. Both would erase an attestation
    somebody made and is accountable for, so this raises instead and names what is in the
    way. Re-decide the items first, in the application, where the supersession is recorded.
    """
    stranded = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM review_decisions WHERE decision = 'investigate'"))
        .scalar_one()
    )
    if stranded:
        raise RuntimeError(
            f"{stranded} review decision(s) are recorded as 'investigate', which the earlier "
            "constraint does not admit. Downgrading would mean deleting or rewriting an "
            "attestation, so this revision refuses. Supersede those decisions through the "
            "application first — that records the change of mind — and run the downgrade "
            "again."
        )

    op.drop_constraint(DECISION_CHECK, "review_decisions", type_="check")
    op.create_check_constraint(
        DECISION_CHECK,
        "review_decisions",
        _in_list("decision", DECISION_KINDS_BEFORE),
    )
    op.drop_constraint(COMMENT_CHECK, "review_campaigns", type_="check")
    op.drop_column("review_campaigns", "comment_requirement")
