r"""Retention: what may eventually be forgotten, and what may not.

History grows without bound, and an installation that keeps every version of every ACE on a
million-directory file server will eventually want to shed some of it. This module is the
hook for that. It deletes nothing by default, and it deletes nothing at all without two
separate pieces of configuration, because an audit tool that quietly discards the record of
a permission change has destroyed the only evidence that the change happened.

## Two switches, not one

``ADG_HISTORY_RETENTION_DAYS`` says how long, and ``ADG_HISTORY_RETENTION_ENABLED`` says
whether. Both are required, and the second defaults to false. One switch would mean a
deployment that set a number while thinking about capacity had also, silently, authorized
deletion; the pair means the destructive step is always somebody's explicit decision.

## Three things are never candidates, whatever the policy says

* **An open version.** It is current state. Pruning it would not shorten history, it would
  delete the object from the product.
* **The newest closed version of an object.** It is what bounds the open version's
  beginning: :meth:`app.history.model.ObjectTimeline.opened_window` reads the previous
  version's ``last_seen_at`` to say when the current state started. Remove it and "since
  when has this group had these members" becomes unanswerable about a group that still
  exists.
* **Anything closed more recently than the cutoff.**

Everything else is a version that has been superseded at least twice and whose successor is
itself older than the retention window.

## Planning is not applying

:meth:`HistoryRetentionService.plan` counts candidates and writes nothing; it is safe to
call on a schedule, in a health check, or from a console, and it is what an operator should
look at before turning the second switch on. :meth:`HistoryRetentionService.apply` is the
only method that deletes, it refuses outright while the policy is disabled, and it reports
exactly what it removed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import CursorResult, and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError
from app.models.schema import object_versions

__all__ = [
    "MAX_RETENTION_DAYS",
    "MIN_RETENTION_DAYS",
    "HistoryRetentionService",
    "RetentionOutcome",
    "RetentionPlan",
    "RetentionPolicy",
]

MIN_RETENTION_DAYS: Final = 30
"""The shortest window a policy may set. A month is already shorter than most audit cycles;
anything below it would let a quarterly review find the evidence of a change already gone,
which is worse than having no retention policy at all."""

MAX_RETENTION_DAYS: Final = 36_500
"""A century. Present so that a mistyped value is refused at startup rather than silently
becoming a cutoff in the far past or the far future."""


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How long superseded versions are kept, and whether anything acts on that.

    ``retain_days`` of zero means *forever* and is the default. It is representable
    alongside ``enabled`` deliberately: "keep everything" is a policy an operator can state,
    not merely the state of having configured nothing.
    """

    retain_days: int = 0
    enabled: bool = False

    def __post_init__(self) -> None:
        if self.retain_days < 0:
            raise DomainValidationError(
                "history_retention_days cannot be negative.",
                value=self.retain_days,
                field="retain_days",
            )
        if self.retain_days and not (MIN_RETENTION_DAYS <= self.retain_days <= MAX_RETENTION_DAYS):
            raise DomainValidationError(
                f"history_retention_days must be 0 (keep everything) or between "
                f"{MIN_RETENTION_DAYS} and {MAX_RETENTION_DAYS}; received {self.retain_days}. "
                "A shorter window than a month would let an ordinary audit cycle find the "
                "evidence of a permission change already deleted.",
                value=self.retain_days,
                field="retain_days",
            )
        if self.enabled and not self.retain_days:
            raise DomainValidationError(
                "history_retention_enabled is set while history_retention_days is 0, which "
                "means keep everything. Set a window, or turn the switch off; a policy that "
                "is enabled and has nothing to enforce reads as protection that is not there.",
                field="enabled",
            )

    @property
    def keeps_everything(self) -> bool:
        return self.retain_days == 0

    def cutoff(self, now: dt.datetime) -> dt.datetime | None:
        """The instant before which a closed version becomes a candidate.

        ``None`` when the policy keeps everything, which is not the same as a cutoff in the
        distant past: callers branch on it rather than computing a date nothing would match.
        """
        if self.keeps_everything:
            return None
        return now - dt.timedelta(days=self.retain_days)

    def describe(self) -> str:
        """One sentence an operator can read in a status page or a log line."""
        if self.keeps_everything:
            return "History retention: every version is kept indefinitely."
        state = "enabled" if self.enabled else "configured but not enabled"
        return (
            f"History retention: {state}; superseded versions older than "
            f"{self.retain_days} days may be removed, except the newest one of each object."
        )


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """What a prune would remove, counted without removing it."""

    policy: RetentionPolicy
    cutoff: dt.datetime | None
    candidates: dict[ObservationKind, int]

    @property
    def total(self) -> int:
        return sum(self.candidates.values())

    @property
    def is_empty(self) -> bool:
        return self.total == 0


@dataclass(frozen=True, slots=True)
class RetentionOutcome:
    """What a prune actually removed."""

    cutoff: dt.datetime
    removed: int


class HistoryRetentionService:
    """Planning and, only on explicit request, applying a retention policy."""

    def __init__(self, session: AsyncSession, policy: RetentionPolicy) -> None:
        self._session = session
        self._policy = policy

    @property
    def policy(self) -> RetentionPolicy:
        return self._policy

    async def plan(self, now: dt.datetime | None = None) -> RetentionPlan:
        """Count what a prune would remove, per kind. Writes nothing."""
        moment = now or dt.datetime.now(tz=dt.UTC)
        cutoff = self._policy.cutoff(moment)
        if cutoff is None:
            return RetentionPlan(policy=self._policy, cutoff=None, candidates={})
        statement = (
            select(object_versions.c.object_kind, func.count().label("candidates"))
            .where(self._prunable(cutoff))
            .group_by(object_versions.c.object_kind)
        )
        rows = (await self._session.execute(statement)).all()
        return RetentionPlan(
            policy=self._policy,
            cutoff=cutoff,
            candidates={ObservationKind(row[0]): int(row[1]) for row in rows},
        )

    async def apply(self, now: dt.datetime | None = None) -> RetentionOutcome:
        """Delete the prunable versions. The only destructive method in the history layer.

        Raises:
            DomainValidationError: the policy is not enabled. Refused rather than treated as
                a no-op, because a caller that asked to prune and was silently ignored would
                go on believing the database was being kept in bounds.
        """
        if not self._policy.enabled:
            raise DomainValidationError(
                "History retention is not enabled. Set ADG_HISTORY_RETENTION_ENABLED=true "
                "together with a window in ADG_HISTORY_RETENTION_DAYS. Deleting recorded "
                "permission history is always an explicit decision.",
                field="enabled",
            )
        moment = now or dt.datetime.now(tz=dt.UTC)
        cutoff = self._policy.cutoff(moment)
        assert cutoff is not None
        result = await self._session.execute(delete(object_versions).where(self._prunable(cutoff)))
        await self._session.commit()
        # ``rowcount`` is on the cursor result a DELETE returns; the base ``Result`` type
        # does not declare it, so the narrowing is explicit rather than an ignore.
        removed = result.rowcount if isinstance(result, CursorResult) else 0
        return RetentionOutcome(cutoff=cutoff, removed=int(removed or 0))

    @staticmethod
    def _prunable(cutoff: dt.datetime) -> Any:
        """Closed, older than the cutoff, and not the newest closed version of its object.

        The correlated ``EXISTS`` is what protects the last closed version: a version is a
        candidate only when the same object has a *later closed* version to take over as the
        bound on when the current state began.
        """
        later = object_versions.alias("later")
        has_newer_closed = (
            select(1)
            .select_from(later)
            .where(
                and_(
                    later.c.object_kind == object_versions.c.object_kind,
                    later.c.object_key == object_versions.c.object_key,
                    later.c.valid_to.is_not(None),
                    later.c.valid_from > object_versions.c.valid_from,
                )
            )
            .exists()
        )
        return and_(
            object_versions.c.valid_to.is_not(None),
            object_versions.c.valid_to < cutoff,
            has_newer_closed,
        )
