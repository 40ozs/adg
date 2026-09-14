r"""Point-in-time answers: membership, raw ACLs, existence, and effective access.

Every method here answers *as of an instant* and says how firmly. The certainty is not
decoration: a point-in-time answer is assembled from versions, some of which were confirmed
either side of the instant asked about, some of which were last confirmed before it and
merely not yet contradicted, and some of which the Phase 7 migration reconstructed from a
row that kept no history at all. Those are three different claims, and an answer that
presented them identically would be the most confidently wrong thing this product could
produce.

**Effective access is computed by the ordinary engine.** There is no historical access
algorithm. :meth:`HistoryService.effective_access_at` builds the two repositories the engine
already takes by injection -- in their as-of form -- and hands them to the same
:class:`app.services.AccessService` that answers live questions. The DACL projection for a
path nobody read, the deny-before-allow ordering, the coverage findings for a group nobody
enumerated: all of it applies unchanged, because none of it knows or cares which instant its
inputs came from. A second engine for history would be a second implementation of the most
safety-critical code in the product, and the first time the two disagreed nobody would know
which was right.

What the engine cannot know is that its inputs were historical, so that is carried beside it
rather than inside it: :class:`app.history.repository.VersionAudit` counts every version the
answer read, by the certainty that version has at the instant, and
:attr:`AsOfAccess.certainty` is the weakest of them.

**The bound on a historical traversal is the same bound as a live one.** The as-of adjacency
reads the same edge-fetch ceiling, so a membership expansion over history truncates exactly
where the live one would, and reports it the same way.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import AccessPath, TokenAssumption
from app.contracts.v1.common import ObservationKind
from app.domain import DEFAULT_LIMITS, DomainValidationError, GraphEdge, TraversalLimits
from app.history.model import Certainty, ObjectTimeline, ObjectVersion
from app.history.repository import (
    HistoricalMembershipRepository,
    HistoricalResourceRepository,
    Presence,
    VersionAudit,
    VersionReader,
    as_row,
)
from app.repositories.membership import PrincipalRecord, graph_edge
from app.repositories.resources import NtfsAceRecord, NtfsResourceRecord, ShareAceRecord
from app.services.access import AccessService, ResolvedAccess
from app.services.graph import GraphService

__all__ = [
    "AsOfAccess",
    "AsOfMembership",
    "AsOfNtfsAcl",
    "AsOfShareAcl",
    "HistoryService",
]

MAX_MEMBERS_AT: Final = 1_000
"""Direct members or groups returned for one point-in-time membership question. The live
listing endpoints page; this one bounds instead, because a historical answer is a single
reconstruction rather than a screen somebody scrolls, and an unbounded one would read an
entire estate's edges to answer about one group."""


@dataclass(frozen=True, slots=True)
class AsOfMembership:
    """Direct membership in one direction, as of an instant."""

    at: dt.datetime
    key: str
    edges: tuple[GraphEdge, ...]
    principals: dict[str, PrincipalRecord]
    """The principal at the far end of each edge, where one was described at that instant.
    A missing entry is an edge pointing at a SID nothing had described *then*, which is a
    different statement from one nothing describes now."""

    certainty: Certainty
    counterparts: tuple[str, ...]
    """The keys at the far end of each edge, deduplicated and in order.

    Computed rather than derived from ``edges`` by the reader, because which end is *far*
    depends on the direction the question was asked in: members when looking down from a
    group, groups when looking up from a principal. A property guessing one of the two
    would be right half the time and silently wrong the other half.
    """

    truncated: bool = False


@dataclass(frozen=True, slots=True)
class AsOfShareAcl:
    """A share's raw SMB ACL as of an instant."""

    at: dt.datetime
    share_key: str
    presence: Presence
    entries: tuple[ShareAceRecord, ...]
    certainty: Certainty

    @property
    def observed(self) -> bool:
        """Whether the share itself was known to exist at the instant.

        An empty ``entries`` on a share that existed is a share whose ACL nobody had read;
        on a share that did not exist it is not an ACL at all. The two must not read alike.
        """
        return self.presence.exists is True


@dataclass(frozen=True, slots=True)
class AsOfNtfsAcl:
    """A directory's descriptor and raw NTFS ACL as of an instant."""

    at: dt.datetime
    resource_key: str
    presence: Presence
    resource: NtfsResourceRecord | None
    entries: tuple[NtfsAceRecord, ...]
    certainty: Certainty

    @property
    def observed(self) -> bool:
        return self.presence.exists is True


@dataclass(frozen=True, slots=True)
class AsOfAccess:
    """An effective-access answer computed over historical state."""

    at: dt.datetime
    access: ResolvedAccess
    inputs: VersionAudit
    """Every version the resolution read, tallied by certainty."""

    @property
    def certainty(self) -> Certainty:
        """The weakest certainty of any input. An answer is as sound as its weakest fact."""
        return self.inputs.weakest

    @property
    def rests_on_reconstructed_state(self) -> bool:
        """Whether any input came from the Phase 7 backfill rather than from an observation."""
        return bool(self.inputs.counts.get(Certainty.BACKFILLED))


class HistoryService:
    """Point-in-time queries over one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._versions = VersionReader(session)

    # ------------------------------------------------------------------ existence

    async def presence_at(self, kind: ObservationKind, key: str, at: dt.datetime) -> Presence:
        """Whether one object existed at an instant: present, measured absent, or unknown."""
        return await self._versions.presence_at(kind, key, _utc(at))

    async def resource_exists_at(self, resource_key: str, at: dt.datetime) -> Presence:
        """The same question for a file-system resource, which is the one usually asked."""
        return await self.presence_at(ObservationKind.NTFS_RESOURCE, resource_key.casefold(), at)

    async def timeline(
        self, kind: ObservationKind, key: str, *, limit: int | None = None
    ) -> ObjectTimeline:
        """Every version of one object, oldest first, with the changes between them."""
        if limit is None:
            return await self._versions.timeline(kind, key)
        return await self._versions.timeline(kind, key, limit=limit)

    async def absent_now(
        self, kind: ObservationKind, *, limit: int = 100
    ) -> tuple[ObjectVersion, ...]:
        """What a reconciled scan has found to be gone, newest removal first."""
        return await self._versions.absent_now(kind, limit=limit)

    # ----------------------------------------------------------------- membership

    async def direct_members_at(self, group_key: str, at: dt.datetime) -> AsOfMembership:
        """Who was directly in one group at an instant."""
        return await self._membership_at(group_key, _utc(at), down=True)

    async def direct_groups_at(self, member_key: str, at: dt.datetime) -> AsOfMembership:
        """Which groups directly contained one principal at an instant."""
        return await self._membership_at(member_key, _utc(at), down=False)

    async def _membership_at(self, key: str, at: dt.datetime, *, down: bool) -> AsOfMembership:
        audit = VersionAudit()
        reader = VersionReader(self._session, audit)
        versions = await (
            reader.contained_at(
                ObservationKind.MEMBERSHIP_EDGE, [key], at, limit=MAX_MEMBERS_AT + 1
            )
            if down
            else reader.related_at(
                ObservationKind.MEMBERSHIP_EDGE, [key], at, limit=MAX_MEMBERS_AT + 1
            )
        )
        truncated = len(versions) > MAX_MEMBERS_AT
        kept = versions[:MAX_MEMBERS_AT]
        edges = tuple(graph_edge(as_row(version)) for version in kept)
        far = [edge.member_key if down else edge.group_key for edge in edges]
        membership = HistoricalMembershipRepository(self._session, at, audit)
        return AsOfMembership(
            at=at,
            key=key,
            edges=edges,
            principals=await membership.principals_by_keys(far),
            certainty=audit.weakest,
            counterparts=tuple(dict.fromkeys(far)),
            truncated=truncated,
        )

    async def effective_groups_at(
        self, subject_key: str, at: dt.datetime, *, limits: TraversalLimits = DEFAULT_LIMITS
    ) -> tuple[tuple[str, ...], Certainty]:
        """Every group a principal reached at an instant, by the live traversal.

        Returns the keys rather than the full expansion because the expansion's node records
        are current-state shaped; the keys are what a caller compares across two instants,
        which is the question this answers.
        """
        moment = _utc(at)
        audit = VersionAudit()
        membership = HistoricalMembershipRepository(self._session, moment, audit)
        expansion = await GraphService(membership).effective_groups(subject_key, limits)
        return tuple(node.key for node in expansion.nodes), audit.weakest

    # ------------------------------------------------------------------- raw ACLs

    async def share_acl_at(self, share_key: str, at: dt.datetime) -> AsOfShareAcl:
        """One share's raw SMB ACL as it stood, with no effective access derived."""
        moment = _utc(at)
        key = share_key.casefold()
        audit = VersionAudit()
        resources = HistoricalResourceRepository(self._session, moment, audit)
        presence = await VersionReader(self._session).presence_at(
            ObservationKind.SMB_SHARE, key, moment
        )
        entries = await resources.full_share_acl(key)
        return AsOfShareAcl(
            at=moment,
            share_key=key,
            presence=presence,
            entries=entries,
            certainty=_combined(presence.certainty, audit.weakest, read_anything=bool(entries)),
        )

    async def resource_acl_at(self, resource_key: str, at: dt.datetime) -> AsOfNtfsAcl:
        """One directory's descriptor facts and raw NTFS ACL as they stood.

        Raw, deliberately: no inheritance is resolved and no Deny applied, exactly as the
        live raw-ACL read does. Reconstructing what Windows would have granted is
        :meth:`effective_access_at`'s job, and keeping the two apart is what lets an
        operator see a change in the entries themselves rather than only in its consequences.
        """
        moment = _utc(at)
        key = resource_key.casefold()
        audit = VersionAudit()
        resources = HistoricalResourceRepository(self._session, moment, audit)
        presence = await VersionReader(self._session).presence_at(
            ObservationKind.NTFS_RESOURCE, key, moment
        )
        record = await resources.get_ntfs_resource(key)
        entries = await resources.full_ntfs_acl(key)
        return AsOfNtfsAcl(
            at=moment,
            resource_key=key,
            presence=presence,
            resource=record,
            entries=entries,
            certainty=_combined(
                presence.certainty, audit.weakest, read_anything=record is not None
            ),
        )

    # ------------------------------------------------------------ effective access

    async def effective_access_at(
        self,
        subject_key: str,
        resource_key: str,
        at: dt.datetime,
        *,
        path: AccessPath = AccessPath.REMOTE_SMB,
        limits: TraversalLimits = DEFAULT_LIMITS,
        assumption: TokenAssumption | None = None,
    ) -> AsOfAccess:
        """What one principal could do to one resource at a past instant.

        The engine is the live one; only its inputs are historical. See the module docstring.
        """
        moment = _utc(at)
        audit = VersionAudit()
        membership = HistoricalMembershipRepository(self._session, moment, audit)
        resources = HistoricalResourceRepository(self._session, moment, audit)
        access = await AccessService(resources, membership).effective_access(
            subject_key, resource_key, path=path, limits=limits, assumption=assumption
        )
        return AsOfAccess(at=moment, access=access, inputs=audit)


def _combined(presence: Certainty, inputs: Certainty, *, read_anything: bool) -> Certainty:
    """The certainty of an answer that is a presence check plus a set of reads.

    When nothing was read, the presence check is the whole answer: an audit with no versions
    in it reports :attr:`Certainty.UNOBSERVED`, and letting that override a perfectly good
    ``absent`` verdict would turn "we know it was gone" back into "we never looked".
    """
    if not read_anything:
        return presence
    order = (Certainty.UNOBSERVED, Certainty.BACKFILLED, Certainty.INFERRED, Certainty.OBSERVED)
    return min((presence, inputs), key=order.index)


def _utc(at: dt.datetime) -> dt.datetime:
    """Every instant this module compares against is timezone-aware and in UTC.

    Refused rather than assumed, for the reason stated throughout: a naive timestamp cannot
    be ordered against observations recorded by a collector in another time zone, and a
    point-in-time query whose instant is an hour out silently answers about a different
    moment than the caller meant.
    """
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise DomainValidationError(
            "A point-in-time query needs a timezone-aware instant. A naive one cannot be "
            "ordered against observations from a host in another time zone, and would "
            "answer about a different moment than the caller meant.",
            field="at",
        )
    return at.astimezone(dt.UTC)
