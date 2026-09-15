r"""Whether a plan still describes the estate — the stale-state rejection, as a pure function.

A change plan is written against what ADG observed on the day somebody wrote it. Between that
day and the change window an administrator can widen the ACE, somebody else can remove it, a
collector's scope can stop covering the folder, or a restore can put back a descriptor from
last month. Executing the plan anyway is how a remediation removes rights nobody reviewed.

So every change carries the entry it was written against (:class:`
~app.remediation.model.EntrySnapshot`), digested, and this module compares that digest with
what ADG holds now. Four verdicts, because the ways a plan can stop being true are not
interchangeable:

* **satisfied** — byte for byte what was reviewed. Proceed.
* **changed** — present and different. *Somebody edited this*: the mask being removed is not
  the mask that was approved. Refuse, and show the diff.
* **missing** — gone. Either the work is already done or something else happened, and those
  are distinguishable only by asking a person. Refuse.
* **unobserved** — ADG has no current reading of the object at all. Refuse, and say so
  differently: "it is gone" and "nobody has looked" are the same absence in the data and lead
  to opposite conclusions, which is the distinction :class:`app.governance.drift.DriftVerdict`
  exists to keep and this module keeps for the same reason.

**The comparison is on content, not on identity.** ``version_id`` moves whenever the timeline
writes a new row — including for an entry removed and restored to exactly its old state — and
comparing on it would refuse plans over a change that did not happen. The digest covers what
the entry *does*; see :meth:`app.remediation.model.EntrySnapshot.as_digestible`.

**Nothing here is a judgment about whether the plan is a good idea.** That is the simulation's
job and it answers by re-running the access check. This module answers the narrower and more
urgent question: is the instruction still an instruction about the thing it names?
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.domain.remediation import PreconditionVerdict
from app.remediation.model import (
    ChangePrecondition,
    EntrySnapshot,
    PlannedChange,
    PreconditionReport,
)

__all__ = [
    "ObservedState",
    "evaluate_change",
    "evaluate_plan",
    "summarize_verdict",
]


@dataclass(frozen=True, slots=True)
class ObservedState:
    """What ADG currently holds about one change's target.

    Assembled by :class:`app.remediation.repository.RemediationRepository`, which is the only
    part of this check that touches a database. Keeping the comparison pure means the rules
    can be tested against every shape of drift without a PostgreSQL instance, and means the
    rules cannot quietly depend on a query's ordering.
    """

    target_observed: bool
    """Whether ADG holds a current reading of the *container* — the directory, the share, or
    the group. False means nobody has looked, which is not the same as an empty ACL."""

    entry: EntrySnapshot | None = None
    """The entry as it stands now, or ``None`` if it is not on the list any more."""

    membership_present: bool | None = None
    """Whether the edge is still there. ``None`` for a change that does not act on one."""

    last_observed_at: dt.datetime | None = None
    detail: Mapping[str, Any] | None = None


def evaluate_change(change: PlannedChange, observed: ObservedState) -> ChangePrecondition:
    """One change's verdict, with the sentence an operator reads.

    The sentence is built here rather than in a client for the reason
    :class:`app.governance.drift.DriftVerdict`'s is: four verdicts that a renderer maps to
    three colors collapses *missing* into *unobserved*, and a plan skipped because "it looks
    like somebody already did it" when in fact nobody has scanned since is the exact failure
    this check exists to prevent.
    """
    expected = change.precondition_digest

    if not observed.target_observed:
        return ChangePrecondition(
            change_id=change.change_id,
            sequence_index=change.sequence_index,
            verdict=PreconditionVerdict.UNOBSERVED,
            expected_digest=expected,
            observed_digest=None,
            summary=(
                f"ADG holds no current reading of {change.target_display or change.target_key}. "
                "This is not evidence that the change has been made — it is evidence that "
                "nobody has looked. Collect the target and re-check before exporting."
            ),
            detail=_detail(change, observed),
        )

    if change.entry is not None:
        return _entry_verdict(change, observed, expected)
    return _membership_verdict(change, observed, expected)


def _entry_verdict(
    change: PlannedChange, observed: ObservedState, expected: str
) -> ChangePrecondition:
    current = observed.entry
    if current is None:
        return ChangePrecondition(
            change_id=change.change_id,
            sequence_index=change.sequence_index,
            verdict=PreconditionVerdict.MISSING,
            expected_digest=expected,
            observed_digest=None,
            summary=(
                f"Entry {change.entry.ace_key} is no longer on "  # type: ignore[union-attr]
                f"{change.target_display or change.target_key}. Either the change has already "
                "been made or something else was. A plan is not shortened on that assumption: "
                "confirm what happened and write a plan against current state."
            ),
            detail=_detail(change, observed),
        )

    observed_digest = current.content_digest
    if observed_digest == expected:
        return ChangePrecondition(
            change_id=change.change_id,
            sequence_index=change.sequence_index,
            verdict=PreconditionVerdict.SATISFIED,
            expected_digest=expected,
            observed_digest=observed_digest,
            summary=(f"Entry {current.ace_key} is exactly as it was when the plan was written."),
            detail=_detail(change, observed),
        )

    return ChangePrecondition(
        change_id=change.change_id,
        sequence_index=change.sequence_index,
        verdict=PreconditionVerdict.CHANGED,
        expected_digest=expected,
        observed_digest=observed_digest,
        summary=_changed_sentence(change.entry, current),  # type: ignore[arg-type]
        detail={
            **_detail(change, observed),
            "differences": _differences(change.entry, current),  # type: ignore[arg-type]
        },
    )


def _membership_verdict(
    change: PlannedChange, observed: ObservedState, expected: str
) -> ChangePrecondition:
    edge = change.membership
    assert edge is not None  # PlannedChange.__post_init__ guarantees one or the other
    if observed.membership_present:
        return ChangePrecondition(
            change_id=change.change_id,
            sequence_index=change.sequence_index,
            verdict=PreconditionVerdict.SATISFIED,
            expected_digest=expected,
            observed_digest=expected,
            summary=(
                f"{edge.member_display_name or edge.member_key} is still a member of "
                f"{edge.group_display_name or edge.group_key}."
            ),
            detail=_detail(change, observed),
        )
    return ChangePrecondition(
        change_id=change.change_id,
        sequence_index=change.sequence_index,
        verdict=PreconditionVerdict.MISSING,
        expected_digest=expected,
        observed_digest=None,
        summary=(
            f"{edge.member_display_name or edge.member_key} is no longer a member of "
            f"{edge.group_display_name or edge.group_key}. Somebody has removed the "
            "membership since this plan was written."
        ),
        detail=_detail(change, observed),
    )


def _changed_sentence(expected: EntrySnapshot, observed: EntrySnapshot) -> str:
    """What moved, in words, leading with the field that changes what the entry does."""
    differences = _differences(expected, observed)
    if not differences:  # pragma: no cover - digests differ only when a field does
        return f"Entry {observed.ace_key} no longer matches what the plan was written against."
    parts = ", ".join(
        f"{name} was {_render(before)} and is now {_render(after)}"
        for name, (before, after) in differences.items()
    )
    return (
        f"Entry {observed.ace_key} has been edited since the plan was written: {parts}. The "
        "change described here would act on an entry nobody reviewed."
    )


def _differences(expected: EntrySnapshot, observed: EntrySnapshot) -> dict[str, tuple[Any, Any]]:
    before = expected.as_digestible()
    after = observed.as_digestible()
    return {key: (before[key], after[key]) for key in before if before[key] != after[key]}


def _render(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:#010x}"
    if value is None:
        return "unset"
    return str(value)


def _detail(change: PlannedChange, observed: ObservedState) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "target_kind": change.target_kind.value,
        "target_key": change.target_key,
        "principal_sid": change.principal_sid,
        "last_observed_at": (
            None
            if observed.last_observed_at is None
            else observed.last_observed_at.astimezone(dt.UTC).isoformat(timespec="microseconds")
        ),
    }
    if observed.detail:
        detail.update(dict(observed.detail))
    return detail


def summarize_verdict(report: PreconditionReport) -> str:
    """One sentence for the top of a screen or a refusal message.

    Says what is blocking and how many, rather than "preconditions failed": an operator whose
    plan is refused needs to know whether one entry moved or the whole share was rebuilt, and
    those are the same message under a count-free summary.
    """
    if report.satisfied:
        if report.basis_moved:
            return (
                f"All {len(report.preconditions)} changes still match what ADG observed. "
                "Collection has moved on since the plan was written, and none of what moved "
                "touches this plan."
            )
        return f"All {len(report.preconditions)} changes still match what ADG observed."
    counts = report.counts()
    parts = [
        f"{counts[verdict.value]} {verdict.value}"
        for verdict in PreconditionVerdict
        if verdict.blocks_export and counts[verdict.value]
    ]
    return (
        f"{len(report.blocking)} of {len(report.preconditions)} changes no longer match what "
        f"ADG observed ({', '.join(parts)}). The plan cannot be exported until it is rewritten "
        "against current state."
    )


def evaluate_plan(
    plan_id: UUID,
    changes: Sequence[PlannedChange],
    observations: Mapping[UUID, ObservedState],
    *,
    checked_at: dt.datetime,
    basis_token: str,
    plan_basis_token: str,
) -> PreconditionReport:
    """Every change's verdict, and whether the plan may proceed.

    A change with no observation in ``observations`` is treated as **unobserved** rather than
    skipped. A missing row in a mapping is exactly the shape of bug that would quietly reduce
    a plan's checks to the subset a query happened to return, and the safe reading of "I have
    nothing for this" is "I did not look".
    """
    return PreconditionReport(
        plan_id=plan_id,
        checked_at=checked_at,
        basis_token=basis_token,
        plan_basis_token=plan_basis_token,
        preconditions=tuple(
            evaluate_change(
                change,
                observations.get(change.change_id, ObservedState(target_observed=False)),
            )
            for change in sorted(changes, key=lambda item: item.sequence_index)
        ),
    )
