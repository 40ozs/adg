r"""Why access changed: one edit, resolved either side of it by the real engine.

The feed says an ACE was added. That is what was *collected*. What an operator actually
needs to know is whether anybody can now do something they could not do before, and those
are not the same question:

* an Allow added below a Deny broadens the ACL and changes nobody's access;
* a share ACL loosened above an unchanged NTFS ACL changes nobody's access, because the two
  layers cross and the tighter one still wins;
* a user added to a group changes access to every resource that group reaches, and touches
  no ACL at all.

So the impact of a change is computed, not inferred from the change. It is computed by
:meth:`app.history.service.HistoryService.effective_access_at`, which is
:class:`app.services.AccessService` — the live engine — reading through as-of repositories.
There is no second implementation of an access check anywhere in this product, and there is
not one here either.

## Why it is a separate request

Resolving one principal against one resource is a membership traversal plus two ACL
evaluations, and doing it for every row of a change feed would make the page cost
proportional to the estate. So the feed carries no impact and this module answers about one
change at a time, on demand — which is also how an operator reads the page: down the list,
then into the one line that looks wrong.

## What it refuses to guess

An impact needs a principal **and** a resource. Some changes name both (an ACE names its
trustee and its directory). Some name one (a membership edge names two principals and no
resource; a directory's owner changing names a resource and no subject). For those, this
module reports :attr:`ImpactVerdict.NEEDS_A_SUBJECT` or
:attr:`ImpactVerdict.NEEDS_A_RESOURCE` and says which is missing, rather than picking a
plausible one — because an impact answer about a pair the operator did not ask about is a
confident answer to a question nobody asked.

Where the change is a membership edge, the **membership delta** is computed regardless: the
groups the principal reached before and after, by the same traversal the live graph uses.
That is the honest partial answer — "Alice gained Finance-RW and, through it,
Finance-Admins" — and it is what the resource question is then asked against.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import AccessPath, RightsMask, classify_access
from app.changes.classify import classify
from app.changes.fields import container_kind_of
from app.changes.model import ChangeDirection, ObjectChange
from app.contracts.v1.common import ObservationKind
from app.domain import DEFAULT_LIMITS, DomainValidationError, TraversalLimits
from app.domain.paths import parse_unc_path
from app.history.model import Certainty, ObjectVersion
from app.history.repository import VersionReader
from app.history.service import AsOfAccess, HistoryService
from app.models.schema import object_versions, scan_runs

__all__ = [
    "AccessDelta",
    "ChangeImpact",
    "ChangeImpactService",
    "ImpactVerdict",
    "MembershipDelta",
]


class ImpactVerdict(StrEnum):
    """Whether the engine could answer, and if not, what is missing."""

    RESOLVED = "resolved"
    """Effective access was computed either side of the change."""

    NEEDS_A_SUBJECT = "needs_a_subject"
    """The change names a resource and no principal. Ask again with one."""

    NEEDS_A_RESOURCE = "needs_a_resource"
    """The change names a principal and no resource. Ask again with one."""

    NOT_APPLICABLE = "not_applicable"
    """The change is about neither a principal nor a resource — a server's operating
    system, for instance. There is no access question to ask about it."""

    UNBOUNDED = "unbounded"
    """The change names a principal and a resource *set* too large to resolve: a group
    whose membership changed reaches every resource its grants reach. The membership delta
    is reported; the resource half is a question for one resource at a time."""


@dataclass(frozen=True, slots=True)
class AccessDelta:
    """What one principal could do to one resource, either side of the change."""

    subject_key: str
    resource_key: str
    path: AccessPath
    before: AsOfAccess
    after: AsOfAccess

    @property
    def gained(self) -> RightsMask:
        """Rights present after and absent before."""
        return RightsMask(
            self.after.access.access.rights.value & ~self.before.access.access.rights.value,
            self.after.access.access.rights.layer,
        )

    @property
    def lost(self) -> RightsMask:
        return RightsMask(
            self.before.access.access.rights.value & ~self.after.access.access.rights.value,
            self.before.access.access.rights.layer,
        )

    @property
    def direction(self) -> ChangeDirection:
        """Which way **effective** access moved, which is the answer the page exists for.

        Not the direction on the change itself. The change's direction describes the edit;
        this describes its consequence, and the interesting case is where they disagree —
        an ACL that broadened while effective access did not move at all, because the other
        layer, a Deny, or a disabled account still governs.
        """
        gained, lost = self.gained.value, self.lost.value
        if gained and lost:
            return ChangeDirection.MIXED
        if gained:
            return ChangeDirection.BROADENED
        if lost:
            return ChangeDirection.NARROWED
        return ChangeDirection.NEUTRAL

    @property
    def certainty(self) -> Certainty:
        """The weaker of the two answers' certainties. A delta is two answers, not one."""
        order = (
            Certainty.UNOBSERVED,
            Certainty.BACKFILLED,
            Certainty.INFERRED,
            Certainty.OBSERVED,
        )
        return min((self.before.certainty, self.after.certainty), key=order.index)

    @property
    def conclusive(self) -> bool:
        """Whether both answers were conclusive enough to support a negative conclusion.

        ADR-0016: while an answer is ``indeterminate``, no negative conclusion is supported
        by it. A delta between two indeterminate answers is a delta between two things
        nobody knows, and a client that rendered it as "no change" would be reporting the
        absence of evidence as evidence of absence — twice over.
        """
        return (
            classify_access(self.before.access.access).is_conclusive
            and classify_access(self.after.access.access).is_conclusive
        )


@dataclass(frozen=True, slots=True)
class MembershipDelta:
    """Which groups a principal reached either side of the change."""

    subject_key: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    certainty: Certainty

    @property
    def gained(self) -> tuple[str, ...]:
        return tuple(key for key in self.after if key not in set(self.before))

    @property
    def lost(self) -> tuple[str, ...]:
        return tuple(key for key in self.before if key not in set(self.after))

    @property
    def moved(self) -> bool:
        return bool(self.gained or self.lost)


@dataclass(frozen=True, slots=True)
class ChangeImpact:
    """One change, the two instants it is bounded by, and what the engine made of it."""

    change: ObjectChange
    at_before: dt.datetime
    at_after: dt.datetime
    verdict: ImpactVerdict
    access: AccessDelta | None
    membership: MembershipDelta | None
    explanation: str
    """One sentence a client can show above the two answers, saying what was compared and
    what was not. Written here rather than by the client for the reason the derived-response
    contract gives once: a conclusion recomputed by a client is a second implementation."""


class ChangeImpactService:
    """The impact of one change, over one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._history = HistoryService(session)
        self._versions = VersionReader(session)

    async def impact_of(
        self,
        kind: ObservationKind,
        key: str,
        at: dt.datetime,
        *,
        subject_key: str | None = None,
        resource_key: str | None = None,
        path: AccessPath = AccessPath.REMOTE_SMB,
        limits: TraversalLimits = DEFAULT_LIMITS,
    ) -> ChangeImpact:
        """Resolve the change whose newer version opened at ``at``.

        ``at`` is the feed's own ``at`` field — the instant the resulting version opened —
        so a client answers "why did this change access?" by handing back the value it was
        given, with no need to reconstruct a window it was already shown.
        """
        after = await self._versions.version_at(kind, key, at)
        if after is None or after.valid_from != at:
            raise DomainValidationError(
                f"No version of {key} opened at {at.isoformat()}. The instant identifying a "
                "change is the instant its resulting version began, which is the `at` field "
                "of the change itself.",
                field="at",
            )
        timeline = await self._history.timeline(kind, key)
        before = _predecessor(timeline.versions, after)
        change = classify(
            kind,
            key,
            before,
            after,
            container_observed_before=await self._container_was_watched(after),
        )
        at_before = await self._instant_before(after, before)
        at_after = await self._instant_after(after)
        subject = subject_key or _subject_of(change)
        resource = resource_key or _resource_of(change)

        membership = None
        if subject is not None:
            membership = await self._membership_delta(subject, at_before, at_after, limits=limits)
        at = at_after

        if subject is None and resource is None:
            return _unresolved(
                change,
                at_before,
                at,
                ImpactVerdict.NOT_APPLICABLE,
                membership,
                "This change is about neither a principal nor a resource, so there is no "
                "access question to ask about it.",
            )
        if subject is None:
            return _unresolved(
                change,
                at_before,
                at,
                ImpactVerdict.NEEDS_A_SUBJECT,
                membership,
                f"This change is about {resource}, and effective access is always about a "
                "principal as well. Name one to see what it could do before and after.",
            )
        if resource is None:
            verdict = (
                ImpactVerdict.UNBOUNDED
                if change.kind is ObservationKind.MEMBERSHIP_EDGE
                else ImpactVerdict.NEEDS_A_RESOURCE
            )
            return _unresolved(
                change,
                at_before,
                at,
                verdict,
                membership,
                f"This change is about {subject} and names no resource. The groups it "
                "reached either side are reported; naming a resource resolves what it "
                "could do to that resource.",
            )

        access = AccessDelta(
            subject_key=subject,
            resource_key=resource,
            path=path,
            before=await self._history.effective_access_at(
                subject, resource, at_before, path=path, limits=limits
            ),
            after=await self._history.effective_access_at(
                subject, resource, at, path=path, limits=limits
            ),
        )
        return ChangeImpact(
            change=change,
            at_before=at_before,
            at_after=at,
            verdict=ImpactVerdict.RESOLVED,
            access=access,
            membership=membership,
            explanation=_explain(change, access),
        )

    async def _instant_after(self, after: ObjectVersion) -> dt.datetime:
        r"""The instant to resolve the estate at *once this change had fully landed*.

        Not the change's own ``valid_from``, and the difference is not a nicety. A scan's
        observations open versions at the instant each was **observed**; its reconciliation
        closes what it did not find at the instant the run **completed**. So an ACL edit —
        one entry removed, another added — is recorded as an addition at ``observed_at`` and
        a removal five minutes later, and any instant between the two shows the directory
        carrying *both* the old grant and the new one.

        Resolving effective access there would answer about a state that never existed,
        and would answer it confidently: a tightening from Full Control to Read & Execute
        would come back as "nothing changed", because Full Control is still open at the
        instant the new entry appeared.

        So the answer is taken at the run's own ``completed_at``. A run with no recorded
        completion falls back to the version's instant, which is the best available answer
        and is what an incomplete run leaves behind.
        """
        completed: dt.datetime | None = (
            await self._session.execute(
                select(scan_runs.c.completed_at).where(scan_runs.c.run_id == after.opened_by_run_id)
            )
        ).scalar_one_or_none()
        if completed is None:
            return after.valid_from
        return max(completed, after.valid_from)

    async def _instant_before(
        self, after: ObjectVersion, before: ObjectVersion | None
    ) -> dt.datetime:
        """The newest confirmation of the state this change replaced.

        A real observed instant, never an invented one, so the "before" answer is one
        somebody actually watched rather than an extrapolation.

        The object's own predecessor supplies it when there is one. When there is not —
        which is the **normal** case for an ACL edit, because an ACE's rights are part of its
        identity and tightening one creates a different object — the confirmation is carried
        by the entry that was removed in the same edit. That entry is a sibling: same kind,
        same container, last confirmed before this change and not yet closed at the time.
        Taking the newest such confirmation is what makes "before" mean *the ACL as it stood*
        rather than *the instant this row appeared*.
        """
        if before is not None:
            return before.last_seen_at
        if after.container_key:
            newest: dt.datetime | None = (
                await self._session.execute(
                    select(sa_func.max(object_versions.c.last_seen_at)).where(
                        object_versions.c.object_kind == after.kind.value,
                        object_versions.c.container_key == after.container_key,
                        object_versions.c.last_seen_at < after.valid_from,
                    )
                )
            ).scalar_one_or_none()
            if newest is not None:
                return newest
        return after.valid_from

    async def _container_was_watched(self, after: ObjectVersion) -> bool | None:
        """Whether this object's container was already in the record when it appeared.

        The same question :mod:`app.changes.repository` answers in batch for a page, asked
        here for one object — because an impact report that called an addition a first
        sighting would tell an operator the grant may have been in place for years.
        """
        parent = container_kind_of(after.kind)
        if parent is None or not after.container_key:
            return None
        first = (
            await self._session.execute(
                select(sa_func.min(object_versions.c.valid_from)).where(
                    object_versions.c.object_kind == parent.value,
                    object_versions.c.object_key == after.container_key,
                    object_versions.c.is_present,
                )
            )
        ).scalar_one_or_none()
        return None if first is None else bool(first < after.valid_from)

    async def _membership_delta(
        self,
        subject_key: str,
        at_before: dt.datetime,
        at_after: dt.datetime,
        *,
        limits: TraversalLimits,
    ) -> MembershipDelta:
        before, before_certainty = await self._history.effective_groups_at(
            subject_key, at_before, limits=limits
        )
        after, after_certainty = await self._history.effective_groups_at(
            subject_key, at_after, limits=limits
        )
        order = (
            Certainty.UNOBSERVED,
            Certainty.BACKFILLED,
            Certainty.INFERRED,
            Certainty.OBSERVED,
        )
        return MembershipDelta(
            subject_key=subject_key,
            before=before,
            after=after,
            certainty=min((before_certainty, after_certainty), key=order.index),
        )


def _predecessor(
    versions: tuple[ObjectVersion, ...], target: ObjectVersion
) -> ObjectVersion | None:
    """The version immediately before ``target`` in its own timeline.

    Found by position in an ordered, non-overlapping list rather than by a query, which is
    the one place in this package where that is possible — and therefore the one place where
    pairing the wrong two versions is not.
    """
    previous: ObjectVersion | None = None
    for version in versions:
        if version.valid_from == target.valid_from:
            return previous
        previous = version
    return None


def _subject_of(change: ObjectChange) -> str | None:
    """The principal a change is about, when it names exactly one.

    An ACE names its trustee. A membership edge names its member — the principal whose
    reach moved; the group is the container and its own reach did not change. A principal
    change names itself. Nothing else names one.
    """
    if change.kind in (ObservationKind.SMB_ACE, ObservationKind.NTFS_ACE):
        return change.subject.related_key
    if change.kind is ObservationKind.MEMBERSHIP_EDGE:
        return change.subject.related_key
    if change.kind is ObservationKind.PRINCIPAL:
        return change.key
    return None


def _resource_of(change: ObjectChange) -> str | None:
    r"""The resource a change is about, as the key the access engine takes.

    The engine addresses a resource by its UNC path, so a share and a share ACE — whose
    containers are spelled ``server|share`` — are translated here, in the one place that
    knows both spellings. A share ACE's resource is the share **root**: that is the
    directory the share publishes, and the layer crossing the engine performs is exactly
    what makes a share-ACL change's consequence visible there.
    """
    if change.kind is ObservationKind.NTFS_ACE:
        return change.subject.container_key
    if change.kind is ObservationKind.NTFS_RESOURCE:
        return change.key
    if change.kind is ObservationKind.SMB_ACE:
        return _share_root(change.subject.container_key)
    if change.kind is ObservationKind.SMB_SHARE:
        return _share_root(change.key)
    return None


def _share_root(share_key: str | None) -> str | None:
    if not share_key:
        return None
    server, separator, share = share_key.partition("|")
    if not separator or not server or not share:
        return None
    try:
        return parse_unc_path(f"\\\\{server}\\{share}").comparison_key
    except DomainValidationError:
        return None


def _unresolved(
    change: ObjectChange,
    at_before: dt.datetime,
    at_after: dt.datetime,
    verdict: ImpactVerdict,
    membership: MembershipDelta | None,
    explanation: str,
) -> ChangeImpact:
    return ChangeImpact(
        change=change,
        at_before=at_before,
        at_after=at_after,
        verdict=verdict,
        access=None,
        membership=membership,
        explanation=explanation,
    )


def _explain(change: ObjectChange, access: AccessDelta) -> str:
    """One sentence saying what the two resolutions mean together.

    The sentence that matters is the third one: an edit that changed nothing effective is a
    real and common finding, and an interface that simply showed two identical masks would
    leave the reader to work out whether that was the answer or a bug.
    """
    moved = access.direction
    if moved is ChangeDirection.NEUTRAL:
        qualifier = (
            ""
            if access.conclusive
            else " Neither answer is conclusive, so this is not "
            "evidence that nothing changed — only that nothing could be established either "
            "side."
        )
        return (
            f"The {change.kind.value} changed and effective access for "
            f"{access.subject_key} on {access.resource_key} did not move. The other layer, "
            f"a Deny entry, or the principal's own state still governs.{qualifier}"
        )
    return (
        f"Effective access for {access.subject_key} on {access.resource_key} "
        f"{moved.value} across this change: gained 0x{access.gained.value:08x}, lost "
        f"0x{access.lost.value:08x}."
    )
