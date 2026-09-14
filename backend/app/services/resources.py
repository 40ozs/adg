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
inventing a name. A SID sitting on an ACL that nothing can describe is one of the things
this tool exists to surface; filtering it out for want of a label would hide it.

**The two layers are answered separately, and joined only by the caller.**
:meth:`ResourceService.share_acl` returns what the share ACL said;
:meth:`ResourceService.share_root_acl` returns what the NTFS descriptor of that share's
root said. Remote access is limited by both and local access bypasses the share layer
entirely, so an intersection computed here would be an effective-access answer wearing a
raw-facts label. That answer arrives in Phase 4, from these facts plus the membership
graph, and it will say so.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import Sid, parse_share_identifier
from app.repositories import (
    MembershipRepository,
    NtfsAceRecord,
    NtfsAclRecomputation,
    NtfsBoundaryVerification,
    NtfsResourceRecord,
    Page,
    PrincipalRecord,
    ResourceRepository,
    ServerRecord,
    ShareAceRecord,
    ShareRecord,
    ShareReferenceRecord,
)

__all__ = [
    "NtfsAcl",
    "ResolvedAce",
    "ResolvedNtfsAce",
    "ResourceDetail",
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
class ResolvedNtfsAce:
    """One raw NTFS ACE plus whatever ADG knows about its trustee.

    Same shape as :class:`ResolvedAce` and deliberately a different type: the two belong to
    different authorization layers, and a function that accepted either would be a function
    that could return a share grant where a file-system grant was asked for.
    """

    ace: NtfsAceRecord
    principal: PrincipalRecord | None


@dataclass(frozen=True, slots=True)
class ShareDetail:
    """A share, its server if one was observed, and how many ACEs are recorded for it.

    ``root_resource`` is the NTFS root of the share — the directory the share publishes —
    or ``None`` when no NTFS run has read it. Its presence here is what lets one response
    say "this share exists, and here is whether its file-system permissions have been
    looked at", without merging the two ACLs into one number.
    """

    share: ShareRecord
    server: ServerRecord | None
    ace_count: int
    root_resource: NtfsResourceRecord | None = None


@dataclass(frozen=True, slots=True)
class ShareAcl:
    """One share's raw ACL, in DACL order, with trustees resolved where possible."""

    share_key: str
    share: ShareRecord | None
    entries: tuple[ResolvedAce, ...]
    has_more: bool
    total: int


@dataclass(frozen=True, slots=True)
class ResourceDetail:
    """A directory, the share it sits under, and how many ACEs are stored for it.

    ``boundary`` carries the collector's ACL-boundary verdict next to the one the server
    derives from the parent it holds, on the same "report both, settle nothing" terms as
    ``acl_hash``. ``parent`` is the parent row itself when one has been read, so a client
    following the chain upward does not have to guess whether a missing answer means the
    share root or an unscanned directory.
    """

    resource: NtfsResourceRecord
    share: ShareRecord | None
    server: ServerRecord | None
    stored_ace_count: int
    boundary: NtfsBoundaryVerification
    parent: NtfsResourceRecord | None = None


@dataclass(frozen=True, slots=True)
class NtfsAcl:
    """One directory's raw DACL, in evaluation order, with trustees resolved where possible.

    ``recomputation`` is present whenever the directory itself has been observed: it carries
    the digest the server derives from the entries it holds beside the one the collector
    reported. An ACL page cannot answer that question — a digest over part of a DACL is not
    a digest of the DACL — so it is computed over the whole entry set regardless of paging.
    """

    resource_key: str
    resource: NtfsResourceRecord | None
    entries: tuple[ResolvedNtfsAce, ...]
    has_more: bool
    total: int
    recomputation: NtfsAclRecomputation | None = None


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
            # Also a real None: the share layer and the file-system layer are collected by
            # different runs, and a share whose NTFS root nobody has read yet is a share
            # whose file-system permissions are simply unknown.
            root_resource=await self._resources.get_share_root_resource(share.share_key),
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

    async def resource_detail(self, resource_key: str) -> ResourceDetail | None:
        """A directory with its share and server, or ``None`` when no run has read it."""
        resource = await self._resources.get_ntfs_resource(resource_key)
        if resource is None:
            return None
        shares = await self._resources.shares_by_keys([resource.share_key])
        servers = await self._resources.servers_by_keys([resource.server_key])
        parent_key = resource.parent_key
        return ResourceDetail(
            resource=resource,
            share=shares.get(resource.share_key),
            server=servers.get(resource.server_key),
            # What is stored, beside what the descriptor claimed. Equal is the normal case;
            # unequal means entries were read and never arrived, and the API says so.
            stored_ace_count=await self._resources.count_ntfs_acl(resource.resource_key),
            # The claim the collector made about where permissions change, checked against
            # the parent this database holds rather than stored unread.
            boundary=await self._resources.verify_boundary(resource),
            parent=(
                None
                if parent_key is None
                else (await self._resources.ntfs_resources_by_keys([parent_key])).get(parent_key)
            ),
        )

    async def ntfs_acl(self, resource_key: str, *, limit: int, offset: int) -> NtfsAcl:
        """One directory's NTFS ACL, whether or not the directory itself has been observed."""
        key = resource_key.casefold()
        entries, has_more = await self._resources.ntfs_acl(key, limit=limit, offset=offset)
        principals = await self._membership.principals_by_keys(
            [entry.trustee_key for entry in entries]
        )
        resource = await self._resources.get_ntfs_resource(key)
        return NtfsAcl(
            resource_key=key,
            resource=resource,
            entries=tuple(
                ResolvedNtfsAce(ace=entry, principal=principals.get(entry.trustee_key))
                for entry in entries
            ),
            has_more=has_more,
            total=await self._resources.count_ntfs_acl(key),
            # Only derivable when the descriptor's own facts are known: dacl_present and
            # dacl_protected are part of the normalized document, and guessing either would
            # produce a digest that is wrong in a way nobody could see.
            recomputation=(
                None if resource is None else await self._resources.recompute_acl_hash(resource)
            ),
        )

    async def share_root_acl(self, share_key: str, *, limit: int, offset: int) -> NtfsAcl:
        r"""The NTFS ACL of the directory a share publishes — ``\\server\share`` itself.

        Its own answer, never folded into :meth:`share_acl`. A caller asking what a share
        grants has to see the share ACL and this one side by side, because remote access is
        limited by both and local access is limited only by this one.

        When the root itself has never been read, its key is still derived from the share,
        so ACEs stored for a path no ``ntfs_resource`` observation has described come back
        anyway — exactly as an orphaned share ACL does.
        """
        root = await self._resources.get_share_root_resource(share_key)
        key = (
            root.resource_key
            if root is not None
            else parse_share_identifier(share_key).unc_path.comparison_key
        )
        return await self.ntfs_acl(key, limit=limit, offset=offset)

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
