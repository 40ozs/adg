r"""Resource questions, answered by joining stored facts to stored principals.

Two repositories meet here and nowhere else. :class:`app.repositories.ResourceRepository`
holds what a share scan observed; :class:`app.repositories.MembershipRepository` holds what
a directory scan observed. A share ACE names a SID, and whether ADG knows anything about
that SID is a question only the second can answer.

**Resolution is computed, never stored.** An ACE keeps its trustee key; whether a
``principals`` row exists for that key is looked up per request. A stored flag would be
correct only until the next AD run described the SID — and an audit report that called a
real account an orphan, or an orphan a real account, is wrong in a way nobody would notice
until it mattered.

**An unresolved trustee is a finding, not a gap.** Every ACE comes back whether or not its
trustee resolves, and the trustee is rendered by
:func:`app.api.graph.principal_summary`, which reports ``resolved: false`` rather than
inventing a name. A SID sitting on a share ACL that nothing can describe is one of the
things this tool exists to surface; filtering it out for want of a label would hide it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import Sid
from app.repositories import (
    MembershipRepository,
    Page,
    PrincipalRecord,
    ResourceRepository,
    ServerRecord,
    ShareAceRecord,
    ShareRecord,
    ShareReferenceRecord,
)

__all__ = [
    "ResolvedAce",
    "ResourceService",
    "ShareAcl",
    "ShareDetail",
    "TrusteeShares",
]


@dataclass(frozen=True, slots=True)
class ResolvedAce:
    """One raw ACE plus whatever ADG knows about its trustee.

    ``principal`` is ``None`` when no run has described the trustee. The ACE is unaffected:
    the grant was observed regardless of whether the grantee can be named.
    """

    ace: ShareAceRecord
    principal: PrincipalRecord | None


@dataclass(frozen=True, slots=True)
class ShareDetail:
    """A share, its server if one was observed, and how many ACEs are recorded for it."""

    share: ShareRecord
    server: ServerRecord | None
    ace_count: int


@dataclass(frozen=True, slots=True)
class ShareAcl:
    """One share's raw ACL, in DACL order, with trustees resolved where possible."""

    share_key: str
    share: ShareRecord | None
    entries: tuple[ResolvedAce, ...]
    has_more: bool
    total: int


@dataclass(frozen=True, slots=True)
class TrusteeShares:
    """The shares whose ACLs name one trustee, and the entries that name it."""

    page: Page[ShareReferenceRecord]
    total: int
    principals: dict[str, PrincipalRecord]


class ResourceService:
    """Server, share, and ACL answers for one request."""

    def __init__(self, resources: ResourceRepository, membership: MembershipRepository) -> None:
        self._resources = resources
        self._membership = membership

    async def share_detail(self, share_key: str) -> ShareDetail | None:
        """A share with its server, or ``None`` when no run has described the share."""
        share = await self._resources.get_share(share_key)
        if share is None:
            return None
        servers = await self._resources.servers_by_keys([share.server_key])
        return ShareDetail(
            share=share,
            # None is a real answer: an ACL scan can describe a share before anything has
            # described the machine serving it.
            server=servers.get(share.server_key),
            ace_count=await self._resources.count_acl(share.share_key),
        )

    async def share_acl(self, share_key: str, *, limit: int, offset: int) -> ShareAcl:
        """One share's ACL, whether or not the share itself has been observed."""
        entries, has_more = await self._resources.share_acl(share_key, limit=limit, offset=offset)
        principals = await self._membership.principals_by_keys(
            [entry.trustee_key for entry in entries]
        )
        return ShareAcl(
            share_key=share_key.casefold(),
            share=await self._resources.get_share(share_key),
            entries=tuple(
                ResolvedAce(ace=entry, principal=principals.get(entry.trustee_key))
                for entry in entries
            ),
            has_more=has_more,
            total=await self._resources.count_acl(share_key),
        )

    async def shares_for_trustee(
        self,
        *,
        trustee_key: str | None = None,
        trustee_sid: Sid | None = None,
        limit: int,
        after: str | None,
    ) -> TrusteeShares:
        """Every share whose ACL names a trustee, with the matching entries."""
        page = await self._resources.shares_referencing(
            trustee_key=trustee_key, trustee_sid=trustee_sid, limit=limit, after=after
        )
        keys = {ace.trustee_key for item in page.items for ace in item.aces}
        return TrusteeShares(
            page=page,
            total=await self._resources.count_shares_referencing(
                trustee_key=trustee_key, trustee_sid=trustee_sid
            ),
            # Asking by SID can span hosts, so a BUILTIN SID resolves to one principal per
            # server that has one; the trustee of each entry is labelled individually.
            principals=await self._membership.principals_by_keys(sorted(keys)),
        )
