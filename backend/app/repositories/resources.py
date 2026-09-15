r"""Reading servers, shares, directories, and raw ACLs of both layers out of PostgreSQL.

Like :mod:`app.repositories.membership`, this turns rows into small typed records and does
nothing else: no permission logic, no interpretation of a mask, no notion of effective
access. What it returns is what a collector observed.

Three choices here are worth stating, because each is a claim about honesty rather than
about performance:

* **A missing parent is reported, not hidden.** A share whose server no run has described,
  and an ACE whose share was never observed, both come back with the parent as ``None``.
  There are no foreign keys to make those cases impossible, because they are real: a
  partial scan reads what it can reach. Dropping such a row from a listing would quietly
  shrink the estate.
* **Derived values are derived.** ``\\server\share``, hidden state, and "is this an
  administrative share" are computed from :class:`app.domain.SmbShare`, never read from a
  column, so a stored copy cannot disagree with the identity it was derived from.
* **An ACL is returned in ACL order.** The order a DACL is written in is what makes a
  Deny evaluable, so ``order_index`` leads the sort and paging is by offset. Keyset paging
  would need a unique monotonic key and would force the rows into ``ace_key`` order, which
  is alphabetical and means nothing. An ACL is tens of entries at most; the trade the
  membership listings make does not apply.

The NTFS side adds one more, and it is the reason this phase exists at all:

* **The share layer and the file-system layer never merge here.** A share ACL and the NTFS
  ACL of that share's root are returned by different methods, as different record types,
  and nothing in this module intersects them. Access over SMB is limited by both, local
  access bypasses the share layer entirely, and an auditor who cannot see the two
  separately cannot tell which one is doing the limiting.

Every read here runs against the ``current_*`` sources in :mod:`app.models.current` rather
than the base tables, so a share, directory or entry that a successful authoritative
reconciliation proved gone is out of the raw ACL, out of the inventory and out of the access
answer, while its row and its whole timeline stay stored for the point-in-time reader. That
is a filter on *presence*, not on parentage: a share whose server was never described is
still current and is still reported with a ``None`` parent, exactly as above. See
``docs/architecture/current-state-presence.md``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import CompoundSelect, RowMapping, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    AclAceFacts,
    AclBoundaryReason,
    DomainValidationError,
    LocalPath,
    NormalizedAcl,
    ResourceKind,
    SharePermission,
    ShareType,
    Sid,
    SmbShare,
    UncPath,
    boundary_reason_for,
    inherited_child_acl_hash,
    normalize_acl,
    parse_local_path,
    parse_unc_path,
)
from app.models.current import (
    current_ntfs_aces,
    current_ntfs_resources,
    current_servers,
    current_smb_share_aces,
    current_smb_shares,
)
from app.models.schema import ReferenceKind, principal_references
from app.repositories.membership import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, RowLike

__all__ = [
    "MAX_ACL_FETCH",
    "UNCOMPARED_BOUNDARY_REASONS",
    "NtfsAceRecord",
    "NtfsAclRecomputation",
    "NtfsBoundaryVerification",
    "NtfsResourceRecord",
    "ResourceRepository",
    "ServerRecord",
    "ShareAceRecord",
    "ShareRecord",
    "ShareReferenceRecord",
    "ntfs_ace_record",
    "ntfs_resource_record",
    "server_record",
    "share_ace_record",
    "share_record",
]

ItemT = TypeVar("ItemT")

MAX_ACL_FETCH = 4096
"""Entries read for one ACL when the whole ACL is wanted, as it is for an access check.

Matches :data:`app.access_engine.MAX_ACL_ENTRIES` — the evaluator's own ceiling — and is
restated here rather than imported so that the persistence layer keeps no dependency on the
authorization engine. A test pins the two equal; drifting apart would mean the database
hands the evaluator less than it is willing to evaluate, and nothing would say so.
"""


@dataclass(frozen=True, slots=True)
class ServerRecord:
    """A stored server, with the provenance that justifies it."""

    server_key: str
    name: str
    dns_host_name: str | None
    netbios_name: str | None
    computer_sid: str | None
    domain_sid: str | None
    is_domain_member: bool | None
    operating_system: str | None
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID
    share_count: int = 0


@dataclass(frozen=True, slots=True)
class ShareRecord:
    """A stored share. Everything positional about it is derived, not stored."""

    share_key: str
    server_key: str
    name: str
    local_path: str | None
    share_type: ShareType
    description: str | None
    concurrent_user_limit: int | None
    caching_mode: str | None
    is_special: bool | None
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID

    @property
    def _domain(self) -> SmbShare:
        path: LocalPath | None = parse_local_path(self.local_path) if self.local_path else None
        return SmbShare(
            server_key=self.server_key,
            name=self.name,
            local_path=path,
            share_type=self.share_type,
            description=self.description,
            concurrent_user_limit=self.concurrent_user_limit,
        )

    @property
    def unc_path(self) -> UncPath:
        return self._domain.unc_path

    @property
    def is_hidden(self) -> bool:
        return self._domain.is_hidden

    @property
    def is_administrative(self) -> bool:
        return self._domain.is_administrative

    @property
    def carries_file_permissions(self) -> bool:
        return self._domain.carries_file_permissions


@dataclass(frozen=True, slots=True)
class ShareAceRecord:
    """One share-level ACE, exactly as a collector read it.

    ``access_mask`` and ``permission`` are never both present: a source reports one form or
    the other, and converting between them here would invent precision. ``right_token`` is
    whichever one was reported, rendered.
    """

    ace_key: str
    share_key: str
    trustee_sid: str
    trustee_key: str
    ace_type: AceType
    access_mask: int | None
    permission: SharePermission | None
    right_token: str
    order_index: int | None
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID


@dataclass(frozen=True, slots=True)
class ShareReferenceRecord:
    """One share that names a trustee, with the entries that name it.

    ``share`` is ``None`` when ACEs exist for a share no run has described. That is an
    orphaned ACL rather than an error, and it is exactly the sort of thing a scan of one
    server's ACLs produces before a scan of its share list has run.
    """

    share_key: str
    share: ShareRecord | None
    aces: tuple[ShareAceRecord, ...]


@dataclass(frozen=True, slots=True)
class NtfsResourceRecord:
    """A directory whose NTFS descriptor has been read, with its descriptor-level facts.

    ``ace_count`` is what the descriptor said, which is not necessarily how many
    :class:`NtfsAceRecord` rows exist for the path. The two are reported separately on
    purpose: a shortfall means entries were read and never stored, and reconciling them
    here by returning whichever is smaller would hide it.
    """

    resource_key: str
    path: str
    server_key: str
    share_key: str
    local_path: str | None
    owner_sid: str | None
    group_sid: str | None
    dacl_present: bool
    dacl_protected: bool
    inheritance_enabled: bool
    is_acl_boundary: bool
    ace_count: int
    depth_from_share_root: int | None
    resource_kind: ResourceKind
    boundary_reason: AclBoundaryReason | None
    acl_hash: str | None
    parent_acl_hash: str | None
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID

    @property
    def unc_path(self) -> UncPath:
        return parse_unc_path(self.path)

    @property
    def is_share_root(self) -> bool:
        """Derived from the path, never stored: the two could otherwise disagree."""
        return self.unc_path.is_share_root

    @property
    def parent_key(self) -> str | None:
        """The containing directory's storage key, or ``None`` at a share root.

        Derived from the path rather than stored beside it (ADR-0007). A parent column
        would be a second copy of something the path already states, and on the one link
        an auditor follows to ask *where* permissions changed, a copy that can disagree
        with the identity it describes is the last thing worth having.
        """
        parent = self.unc_path.parent
        return None if parent is None else parent.comparison_key

    @property
    def is_container(self) -> bool:
        """Whether children inherit through the container projection rather than the object one."""
        return self.resource_kind is ResourceKind.DIRECTORY

    @property
    def grants_everyone_full_access(self) -> bool:
        """A NULL DACL. Always a finding, and the opposite of an empty one."""
        return not self.dacl_present

    @property
    def denies_everyone(self) -> bool:
        """A present but empty DACL. Nobody has access through it; the owner still has
        implicit control rights, which is why ``owner_sid`` is reported beside it."""
        return self.dacl_present and self.ace_count == 0


@dataclass(frozen=True, slots=True)
class NtfsAceRecord:
    """One NTFS ACE, exactly as the descriptor stored it.

    The inheritance and propagation views below read the raw ``ace_flags`` byte through
    :class:`app.domain.AceFlag` rather than being stored as columns, so a bit Windows
    defines later survives a round trip even though no property here names it.
    """

    ace_key: str
    resource_key: str
    trustee_sid: str
    trustee_key: str
    ace_type: AceType
    access_mask: int
    ace_flags: int
    source: AceSource
    inherited_from: str | None
    order_index: int | None
    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID

    @property
    def flags(self) -> AceFlag:
        return AceFlag(self.ace_flags)

    @property
    def is_inherited(self) -> bool:
        return self.source is AceSource.INHERITED

    @property
    def is_inheritable(self) -> bool:
        return bool(self.flags & (AceFlag.OBJECT_INHERIT | AceFlag.CONTAINER_INHERIT))

    @property
    def applies_to_this_object(self) -> bool:
        """False for an INHERIT_ONLY entry, which grants nothing on the folder holding it.

        Reported because an ACL viewer that shows such an entry as a grant on this folder
        is reporting access that does not exist here.
        """
        return not (self.flags & AceFlag.INHERIT_ONLY)

    @property
    def acl_facts(self) -> AclAceFacts:
        """What this entry contributes to the normalized ACL (:mod:`app.domain.acl_hash`)."""
        return AclAceFacts(
            trustee_sid=self.trustee_sid,
            ace_type=self.ace_type,
            access_mask=self.access_mask,
            ace_flags=self.ace_flags,
            order_index=self.order_index,
        )


@dataclass(frozen=True, slots=True)
class NtfsAclRecomputation:
    """The digest the server derives from the ACEs it actually holds, next to the reported one.

    Both are reported because a disagreement is information. The collector hashed the DACL
    it read in one piece; the server hashes what arrived. They differ when entries were
    lost in transit, when a batch was rejected, or when two collectors describe one path
    differently — each of which is a coverage gap, and none of which should be settled by
    the server quietly preferring one number.
    """

    reported: str | None
    normalized: NormalizedAcl
    stored_ace_count: int
    declared_ace_count: int
    hashed_entries: tuple[AclAceFacts, ...] = ()
    """Exactly the entries the document was built from, which is not always every stored
    row: a NULL DACL carries none by definition, so rows surviving from a run that read a
    real DACL are excluded here and still counted in ``stored_ace_count``. Kept because the
    boundary projection has to run over the same entries the digest was taken over, and
    re-reading them would let the two drift."""

    @property
    def computed(self) -> str:
        return self.normalized.digest

    @property
    def agrees(self) -> bool | None:
        """``None`` when the collector reported no hash — unknown, not a disagreement."""
        return None if self.reported is None else self.reported == self.computed

    @property
    def ace_count_agrees(self) -> bool:
        return self.stored_ace_count == self.declared_ace_count


#: Reasons that mean the collector never made the comparison, rather than making it and
#: finding a difference. A run that started below a share root, or could not read the
#: parent, reports a boundary because unknown must not read as unchanged — so there is no
#: verdict of the collector's to agree or disagree with, and ``agrees`` stays ``None``.
UNCOMPARED_BOUNDARY_REASONS: frozenset[AclBoundaryReason] = frozenset(
    {
        AclBoundaryReason.SHARE_ROOT,
        AclBoundaryReason.SCAN_ROOT,
        AclBoundaryReason.PARENT_UNREADABLE,
        AclBoundaryReason.PARENT_NULL_DACL,
    }
)

#: Reasons the server can reach without the parent's entries at all.
_SETTLED_WITHOUT_A_PARENT: frozenset[AclBoundaryReason] = frozenset(
    {
        AclBoundaryReason.PROTECTED_DACL,
        AclBoundaryReason.NULL_DACL,
        AclBoundaryReason.SHARE_ROOT,
    }
)


@dataclass(frozen=True, slots=True)
class NtfsBoundaryVerification:
    """The boundary verdict the collector claimed, beside the one the server can derive.

    Phase 3A stored ``is_acl_boundary`` unread: a share-root collector set it ``true``
    because a root has no comparable parent, and nothing checked it. A tree walk claims it
    for directories that *do* have a parent, and a wrong claim is expensive in one
    direction — a boundary reported ``false`` tells the next scan it may stop looking, and
    every permission change beneath it is silently dropped.

    So the server redoes the arithmetic from what it holds: it takes the parent's stored
    ACEs, projects them onto a child of this kind (:mod:`app.domain.inheritance`), and
    compares that digest with this resource's own. Neither number overrides the other. They
    disagree when the parent changed between the two readings, when the collector's
    projection is wrong, or when entries were lost in transit — three different findings
    that picking a winner would flatten into silence.

    **What is comparable, and what is merely unknown.** A protected DACL and a NULL DACL
    settle the question from this row alone. Everything else needs the parent's entries,
    and a parent no run has read makes the comparison impossible rather than negative:
    :attr:`comparable` is then ``false`` and :attr:`computed` is ``None``. And when the
    collector's own reason is one of :data:`UNCOMPARED_BOUNDARY_REASONS` it never claimed a
    comparison, so :attr:`agrees` is ``None`` even where the server can now make one — the
    server simply knows more than the collector did, which is not the collector being wrong.
    """

    resource_key: str
    parent_key: str | None
    parent_observed: bool
    parent: NtfsResourceRecord | None
    """The parent row this verdict was reached against.

    Carried out rather than looked up again by the caller. The verification has to read the
    parent to project from it, and a caller that also wants to show the parent — which the
    detail endpoint does — would otherwise fetch the same row a second time in the same
    request. Measured: it was one of two duplicated reads that made ``GET /resources/{path}``
    the most expensive read in the API.
    """

    reported: bool
    reported_reason: AclBoundaryReason | None
    reported_parent_acl_hash: str | None
    resource_acl_hash: str
    """The digest the server derives from this resource's stored ACEs — not the reported one."""

    parent_acl_hash: str | None
    """The same, for the parent. ``None`` when no run has read the parent."""

    projected_child_acl_hash: str | None
    """What the parent hands down to a child of this kind. ``None`` when the parent was not
    read, or has a NULL DACL, which projects nothing at all."""

    computed_reason: AclBoundaryReason | None

    @property
    def projection_available(self) -> bool:
        """Whether the parent's entries were in reach to project and compare."""
        return self.projected_child_acl_hash is not None

    @property
    def settled_without_the_parent(self) -> bool:
        """Whether the verdict needs no projection at all.

        A protected DACL refuses inheritance and a NULL DACL cannot have been inherited;
        a share root's parent is outside the share, which the path alone establishes.
        """
        return self.computed_reason in _SETTLED_WITHOUT_A_PARENT

    @property
    def computed(self) -> bool | None:
        """The server's own verdict, or ``None`` when it could not reach one."""
        if self.settled_without_the_parent:
            return True
        return (self.computed_reason is not None) if self.projection_available else None

    @property
    def agrees(self) -> bool | None:
        """``None`` when either side did not reach a comparable verdict."""
        if self.computed is None:
            return None
        if self.reported_reason in UNCOMPARED_BOUNDARY_REASONS:
            return None
        return self.reported == self.computed

    @property
    def parent_acl_hash_agrees(self) -> bool | None:
        """Whether the parent ADG holds is the reading the collector judged against.

        ``False`` does not mean the verdict is wrong — the parent may simply have been
        re-read since — but it does mean the verdict was made against a descriptor that is
        no longer the stored one, which is exactly the case where a stale boundary hides a
        change. ``None`` when either side is missing.
        """
        if self.reported_parent_acl_hash is None or self.parent_acl_hash is None:
            return None
        return self.reported_parent_acl_hash == self.parent_acl_hash


class ResourceRepository:
    """Server, share, and share-ACL queries against one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """The session every read runs against.

        Exposed for the same reason as :attr:`app.repositories.MembershipRepository.session`:
        the repositories that wrap this one build themselves over the same connection.
        """
        return self._session

    # ---------------------------------------------------------------- servers

    async def list_servers(
        self, *, limit: int = DEFAULT_PAGE_SIZE, after: str | None = None
    ) -> Page[ServerRecord]:
        """Every known server, ordered by key."""
        page_size = _page_size(limit)
        statement: Select[Any] = (
            select(current_servers, _share_count_column())
            .order_by(current_servers.c.server_key)
            .limit(page_size + 1)
        )
        if after is not None:
            statement = statement.where(current_servers.c.server_key > after)

        rows = (await self._session.execute(statement)).mappings().all()
        return _page(rows, page_size, server_record, lambda record: record.server_key)

    async def get_server(self, server_key: str) -> ServerRecord | None:
        row = (
            (
                await self._session.execute(
                    select(current_servers, _share_count_column()).where(
                        current_servers.c.server_key == server_key.casefold()
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else server_record(row)

    async def count_servers(self) -> int:
        return int(
            (
                await self._session.execute(select(func.count()).select_from(current_servers))
            ).scalar_one()
        )

    async def servers_by_keys(self, keys: Sequence[str]) -> dict[str, ServerRecord]:
        if not keys:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(current_servers, _share_count_column()).where(
                        current_servers.c.server_key.in_(sorted(set(keys)))
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["server_key"]: server_record(row) for row in rows}

    # ----------------------------------------------------------------- shares

    async def list_shares(
        self, server_key: str, *, limit: int = DEFAULT_PAGE_SIZE, after: str | None = None
    ) -> Page[ShareRecord]:
        """Shares published by one server, ordered by key."""
        page_size = _page_size(limit)
        statement: Select[Any] = (
            select(current_smb_shares)
            .where(current_smb_shares.c.server_key == server_key.casefold())
            .order_by(current_smb_shares.c.share_key)
            .limit(page_size + 1)
        )
        if after is not None:
            statement = statement.where(current_smb_shares.c.share_key > after)

        rows = (await self._session.execute(statement)).mappings().all()
        return _page(rows, page_size, share_record, lambda record: record.share_key)

    async def count_shares(self, server_key: str) -> int:
        statement = (
            select(func.count())
            .select_from(current_smb_shares)
            .where(current_smb_shares.c.server_key == server_key.casefold())
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def get_share(self, share_key: str) -> ShareRecord | None:
        row = (
            (
                await self._session.execute(
                    select(current_smb_shares).where(
                        current_smb_shares.c.share_key == share_key.casefold()
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else share_record(row)

    async def shares_by_keys(self, keys: Sequence[str]) -> dict[str, ShareRecord]:
        if not keys:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(current_smb_shares).where(
                        current_smb_shares.c.share_key.in_(sorted(set(keys)))
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["share_key"]: share_record(row) for row in rows}

    async def has_aces(self, share_key: str) -> bool:
        """Whether any ACE names this share, even if the share itself was never described."""
        statement = select(
            select(current_smb_share_aces.c.ace_key)
            .where(current_smb_share_aces.c.share_key == share_key.casefold())
            .exists()
        )
        return bool((await self._session.execute(statement)).scalar_one())

    # -------------------------------------------------------------- share ACL

    async def share_acl(
        self, share_key: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[tuple[ShareAceRecord, ...], bool]:
        """One share's ACL in DACL order, plus whether more entries follow.

        ``order_index`` leads the sort with nulls last: a source that did not report a
        position gets no invented one, and the remaining rows keep a deterministic order
        through ``ace_key``.
        """
        page_size = _page_size(limit)
        statement = (
            select(current_smb_share_aces)
            .where(current_smb_share_aces.c.share_key == share_key.casefold())
            .order_by(
                current_smb_share_aces.c.order_index.nulls_last(), current_smb_share_aces.c.ace_key
            )
            .offset(max(0, offset))
            .limit(page_size + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        has_more = len(rows) > page_size
        return tuple(share_ace_record(row) for row in rows[:page_size]), has_more

    async def count_acl(self, share_key: str) -> int:
        statement = (
            select(func.count())
            .select_from(current_smb_share_aces)
            .where(current_smb_share_aces.c.share_key == share_key.casefold())
        )
        return int((await self._session.execute(statement)).scalar_one())

    # --------------------------------------------------------- NTFS resources

    async def get_ntfs_resource(self, resource_key: str) -> NtfsResourceRecord | None:
        """One directory, or ``None`` when no run has read its descriptor."""
        row = (
            (
                await self._session.execute(
                    select(current_ntfs_resources).where(
                        current_ntfs_resources.c.resource_key == resource_key.casefold()
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else ntfs_resource_record(row)

    async def get_share_root_resource(self, share_key: str) -> NtfsResourceRecord | None:
        r"""The NTFS root of one share: the directory at ``\\server\share`` itself.

        The root's key is a function of the share's, so this is a primary-key lookup rather
        than a scan of the share's directories — and it cannot return a child by accident,
        which a "first resource under this share" query could.
        """
        return await self.get_ntfs_resource(_share_root_key(share_key))

    async def ntfs_resources_by_keys(self, keys: Sequence[str]) -> dict[str, NtfsResourceRecord]:
        if not keys:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(current_ntfs_resources).where(
                        current_ntfs_resources.c.resource_key.in_(
                            sorted({key.casefold() for key in keys})
                        )
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["resource_key"]: ntfs_resource_record(row) for row in rows}

    async def has_ntfs_aces(self, resource_key: str) -> bool:
        """Whether any ACE names this path, even if the directory itself was never read."""
        statement = select(
            select(current_ntfs_aces.c.ace_key)
            .where(current_ntfs_aces.c.resource_key == resource_key.casefold())
            .exists()
        )
        return bool((await self._session.execute(statement)).scalar_one())

    # --------------------------------------------------------------- NTFS ACL

    async def ntfs_acl(
        self, resource_key: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[tuple[NtfsAceRecord, ...], bool]:
        """One directory's DACL in evaluation order, plus whether more entries follow.

        Same ordering rule as a share ACL, for the same reason: ``order_index`` first with
        nulls last, ``ace_key`` to break ties deterministically. A Deny ahead of an Allow
        and the same Deny behind it are different ACLs, so the position a collector
        reported is preserved rather than re-derived from anything here.
        """
        page_size = _page_size(limit)
        statement = (
            select(current_ntfs_aces)
            .where(current_ntfs_aces.c.resource_key == resource_key.casefold())
            .order_by(current_ntfs_aces.c.order_index.nulls_last(), current_ntfs_aces.c.ace_key)
            .offset(max(0, offset))
            .limit(page_size + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        has_more = len(rows) > page_size
        return tuple(ntfs_ace_record(row) for row in rows[:page_size]), has_more

    async def count_ntfs_acl(self, resource_key: str) -> int:
        statement = (
            select(func.count())
            .select_from(current_ntfs_aces)
            .where(current_ntfs_aces.c.resource_key == resource_key.casefold())
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def recompute_acl_hash(self, resource: NtfsResourceRecord) -> NtfsAclRecomputation:
        """Hash the ACEs this database holds for a path, for comparison with the reported one.

        Deliberately unpaged: the digest is over the whole DACL, and hashing a page of it
        would produce a number that looks like an answer and is not one. A DACL is tens of
        entries, so reading them all costs one indexed scan.

        **Entries can still disagree about position, and that is not an error here.** An
        ACE's identity excludes ``order_index`` — deliberately, so that reordering a DACL
        does not look like every entry being deleted and recreated. A reconciliation now
        takes the superseded entry out of current state, which removes the commonest way two
        rows came to claim position 0; what it cannot remove is a collision between entries
        that are *both* current, because nothing about the stored data forbids one. When it
        happens the normalizer rightly refuses to hash: the document would depend on which
        row came back first. The answer is to fall back to the *unordered* form, which the
        normal form exists to express. It says plainly that the server cannot establish
        evaluation order from what it holds, it can never collide with an ordered digest,
        and ``ordered: false`` reports it on the wire — all of which beats failing the
        request, which would take a directory's whole ACL off the air over one position.
        """
        rows = (
            (
                await self._session.execute(
                    select(current_ntfs_aces)
                    .where(current_ntfs_aces.c.resource_key == resource.resource_key)
                    .order_by(
                        current_ntfs_aces.c.order_index.nulls_last(), current_ntfs_aces.c.ace_key
                    )
                )
            )
            .mappings()
            .all()
        )
        entries = [ntfs_ace_record(row).acl_facts for row in rows]
        # A NULL DACL carries no entries by definition. Current entries can nonetheless
        # survive a run that found the descriptor replaced -- the NTFS scope may not have
        # been reconciled since -- so they are excluded from the document rather than allowed
        # to contradict it, and stored_ace_count still reports every current row, so
        # ace_count_agrees exposes the split.
        hashed = [] if not resource.dacl_present else entries
        try:
            normalized = normalize_acl(
                dacl_present=resource.dacl_present,
                dacl_protected=resource.dacl_protected,
                aces=hashed,
            )
        except DomainValidationError:
            normalized = normalize_acl(
                dacl_present=resource.dacl_present,
                dacl_protected=resource.dacl_protected,
                aces=[replace(entry, order_index=None) for entry in hashed],
            )
        return NtfsAclRecomputation(
            reported=resource.acl_hash,
            normalized=normalized,
            stored_ace_count=len(entries),
            declared_ace_count=resource.ace_count,
            hashed_entries=tuple(hashed),
        )

    async def verify_boundary(self, resource: NtfsResourceRecord) -> NtfsBoundaryVerification:
        """Redo the boundary judgement from the parent this database actually holds.

        Two indexed reads: this resource's ACEs and its parent's. The projection itself is
        pure (:func:`app.domain.projected_child_acl`), so the whole verdict is reproducible
        from the two ACL responses a client can fetch for itself.

        The parent is found by path, not by a stored link — see
        :attr:`NtfsResourceRecord.parent_key`. A parent no run has read gives
        ``projected_child_acl_hash=None``, which reads as *unknown*, never as *unchanged*.
        """
        own = await self.recompute_acl_hash(resource)

        parent_key = resource.parent_key
        parent = None if parent_key is None else await self.get_ntfs_resource(parent_key)

        projection: str | None = None
        parent_digest: str | None = None
        if parent is not None:
            parent_acl = await self.recompute_acl_hash(parent)
            parent_digest = parent_acl.computed
            projection = inherited_child_acl_hash(
                dacl_present=parent.dacl_present,
                aces=parent_acl.hashed_entries,
                for_container=resource.is_container,
            )

        computed_reason = boundary_reason_for(
            is_share_root=resource.is_share_root,
            # The server cannot know where a walk began; that is a fact about the run, not
            # about the estate. It judges on what it holds, and `agrees` withholds a verdict
            # whenever the collector's own reason says it never compared either.
            is_scan_root=False,
            dacl_present=resource.dacl_present,
            dacl_protected=resource.dacl_protected,
            acl_hash=own.computed,
            parent_dacl_present=None if parent is None else parent.dacl_present,
            parent_projection=projection,
        )

        return NtfsBoundaryVerification(
            resource_key=resource.resource_key,
            parent_key=parent_key,
            parent_observed=parent is not None,
            parent=parent,
            reported=resource.is_acl_boundary,
            reported_reason=resource.boundary_reason,
            reported_parent_acl_hash=resource.parent_acl_hash,
            resource_acl_hash=own.computed,
            parent_acl_hash=parent_digest,
            projected_child_acl_hash=projection,
            computed_reason=computed_reason,
        )

    # ------------------------------------------------------------- references

    async def shares_referencing(
        self,
        *,
        trustee_key: str | None = None,
        trustee_sid: Sid | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        after: str | None = None,
    ) -> Page[ShareReferenceRecord]:
        """Shares whose ACL names a trustee, with the entries that name it.

        Pass ``trustee_key`` for one specific principal — including one server's BUILTIN
        group — or ``trustee_sid`` to ask about a SID wherever it appears. The second form
        is the right answer for a BUILTIN SID: "which shares grant S-1-5-32-544" is a
        question about every server that has one, and answering with a 409 would refuse a
        question that has a single correct answer.
        """
        page_size = _page_size(limit)
        predicate = _trustee_predicate(trustee_key, trustee_sid)

        keys_statement: Select[Any] = (
            select(current_smb_share_aces.c.share_key)
            .where(predicate)
            .group_by(current_smb_share_aces.c.share_key)
            .order_by(current_smb_share_aces.c.share_key)
            .limit(page_size + 1)
        )
        if after is not None:
            keys_statement = keys_statement.where(current_smb_share_aces.c.share_key > after)

        keys = [row[0] for row in (await self._session.execute(keys_statement)).all()]
        has_more = len(keys) > page_size
        visible = keys[:page_size]
        if not visible:
            return Page(items=(), has_more=False, next_key=None)

        ace_rows = (
            (
                await self._session.execute(
                    select(current_smb_share_aces)
                    .where(current_smb_share_aces.c.share_key.in_(visible), predicate)
                    .order_by(
                        current_smb_share_aces.c.share_key,
                        current_smb_share_aces.c.order_index.nulls_last(),
                        current_smb_share_aces.c.ace_key,
                    )
                )
            )
            .mappings()
            .all()
        )
        grouped: dict[str, list[ShareAceRecord]] = {key: [] for key in visible}
        for row in ace_rows:
            grouped[row["share_key"]].append(share_ace_record(row))

        shares = await self.shares_by_keys(visible)
        items = tuple(
            ShareReferenceRecord(share_key=key, share=shares.get(key), aces=tuple(grouped[key]))
            for key in visible
        )
        return Page(
            items=items, has_more=has_more, next_key=items[-1].share_key if has_more else None
        )

    async def count_shares_referencing(
        self, *, trustee_key: str | None = None, trustee_sid: Sid | None = None
    ) -> int:
        statement = (
            select(func.count(func.distinct(current_smb_share_aces.c.share_key)))
            .select_from(current_smb_share_aces)
            .where(_trustee_predicate(trustee_key, trustee_sid))
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def reference_keys_for(self, principal_key: str) -> tuple[str, ...]:
        """Every resource key that names this principal, from the reference index."""
        rows = (
            await self._session.execute(
                select(principal_references.c.reference_key)
                .where(
                    principal_references.c.principal_key == principal_key,
                    principal_references.c.reference_kind == ReferenceKind.SMB_ACE.value,
                )
                .order_by(principal_references.c.reference_key)
            )
        ).all()
        return tuple(row[0] for row in rows)

    # ------------------------------------------------- whole ACLs, for evaluation

    async def full_ntfs_acl(
        self, resource_key: str, *, limit: int = MAX_ACL_FETCH
    ) -> tuple[NtfsAceRecord, ...]:
        """Every stored entry for one path, in evaluation order.

        Unpaged on purpose, and for the same reason :meth:`recompute_acl_hash` is: an
        access check is over a whole DACL. Evaluating a page of one would compute a Deny
        that the next page cancels, or grant through an Allow the previous page had already
        denied — a number that looks like an answer and is not one.

        ``limit`` is a ceiling, not a page size: one row beyond it is fetched deliberately
        so that a caller can tell "this is the whole ACL" from "this is as much of it as
        the ceiling allows", and report the difference rather than evaluating over a
        silently shortened DACL.
        """
        statement = (
            select(current_ntfs_aces)
            .where(current_ntfs_aces.c.resource_key == resource_key.casefold())
            .order_by(current_ntfs_aces.c.order_index.nulls_last(), current_ntfs_aces.c.ace_key)
            .limit(max(1, limit) + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(ntfs_ace_record(row) for row in rows)

    async def full_share_acl(
        self, share_key: str, *, limit: int = MAX_ACL_FETCH
    ) -> tuple[ShareAceRecord, ...]:
        """Every stored entry for one share's ACL, in evaluation order."""
        statement = (
            select(current_smb_share_aces)
            .where(current_smb_share_aces.c.share_key == share_key.casefold())
            .order_by(
                current_smb_share_aces.c.order_index.nulls_last(), current_smb_share_aces.c.ace_key
            )
            .limit(max(1, limit) + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(share_ace_record(row) for row in rows)

    async def ntfs_acls_for(
        self, keys: Sequence[str], *, limit: int = MAX_ACL_FETCH
    ) -> dict[str, tuple[NtfsAceRecord, ...]]:
        """Whole DACLs for several paths in one query, keyed by path.

        One statement for a page of resources rather than one per resource: the endpoint
        that lists what a principal can reach evaluates a page at a time, and a
        per-resource read would make its query count grow with the page size — the shape of
        every N+1 this project has already measured out of the other endpoints.

        A key with no entries is absent from the result rather than mapped to an empty
        tuple, because the two mean different things — no ACL was stored, versus an ACL with
        no entries — and only the caller holding the resource row can tell which.
        """
        folded = sorted({key.casefold() for key in keys})
        if not folded:
            return {}
        statement = (
            select(current_ntfs_aces)
            .where(current_ntfs_aces.c.resource_key.in_(folded))
            .order_by(
                current_ntfs_aces.c.resource_key,
                current_ntfs_aces.c.order_index.nulls_last(),
                current_ntfs_aces.c.ace_key,
            )
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return _group_by_key(rows, "resource_key", ntfs_ace_record, limit)

    async def share_acls_for(
        self, keys: Sequence[str], *, limit: int = MAX_ACL_FETCH
    ) -> dict[str, tuple[ShareAceRecord, ...]]:
        """Whole share ACLs for several shares in one query, keyed by share."""
        folded = sorted({key.casefold() for key in keys})
        if not folded:
            return {}
        statement = (
            select(current_smb_share_aces)
            .where(current_smb_share_aces.c.share_key.in_(folded))
            .order_by(
                current_smb_share_aces.c.share_key,
                current_smb_share_aces.c.order_index.nulls_last(),
                current_smb_share_aces.c.ace_key,
            )
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return _group_by_key(rows, "share_key", share_ace_record, limit)

    # --------------------------------------------- candidates for one principal

    async def resources_named_by(
        self,
        principal_keys: Sequence[str],
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        after: str | None = None,
    ) -> Page[str]:
        """Paths whose evaluation could possibly involve one of these principals.

        The bounded candidate set behind "what can this principal reach": the trustees of
        an access token, resolved through the reference index, rather than every directory
        in the estate evaluated against every principal in the domain.

        **A union, not just the index.** A path with a NULL DACL grants everyone full
        access and names nobody, so it appears in no reference row — and an answer that
        silently omitted exactly the paths open to the world would invert the finding this
        tool exists to produce. It is a second indexed read
        (``ix_ntfs_resources_null_dacl``), unioned into the same keyset page.

        **The reference index is not itself current state.** It is append-only -- a row
        records that an ACL once named a principal -- and a directory whose entries a
        reconciled scan proved gone would otherwise keep offering itself as a candidate
        forever, to be listed with a verdict of no access on a path that may not even
        exist. So each reference is confirmed against the entry it stands for
        (:func:`_still_named`), which settles both cases at once: closing a resource closes
        its entries too, so a removed directory names nobody either.
        """
        page_size = _page_size(limit)
        if not principal_keys:
            return Page(items=(), has_more=False, next_key=None)

        named = select(principal_references.c.reference_key.label("candidate_key")).where(
            principal_references.c.principal_key.in_(sorted(set(principal_keys))),
            principal_references.c.reference_kind == ReferenceKind.NTFS_ACE.value,
            _still_named(current_ntfs_aces, "resource_key"),
        )
        unrestricted = select(current_ntfs_resources.c.resource_key.label("candidate_key")).where(
            ~current_ntfs_resources.c.dacl_present
        )
        return await self._candidate_page(named.union(unrestricted), page_size, after)

    async def shares_named_by(
        self,
        principal_keys: Sequence[str],
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        after: str | None = None,
    ) -> Page[str]:
        """Shares whose ACL names one of these principals.

        No union here, and the asymmetry with :meth:`resources_named_by` is deliberate: a
        share with no stored ACL is *unknown*, not unrestricted. Listing it as a candidate
        on that basis would assert that an unread ACL grants something.
        """
        page_size = _page_size(limit)
        if not principal_keys:
            return Page(items=(), has_more=False, next_key=None)

        named = select(principal_references.c.reference_key.label("candidate_key")).where(
            principal_references.c.principal_key.in_(sorted(set(principal_keys))),
            principal_references.c.reference_kind == ReferenceKind.SMB_ACE.value,
            _still_named(current_smb_share_aces, "share_key"),
        )
        return await self._candidate_page(named, page_size, after)

    async def _candidate_page(
        self, source: Select[Any] | CompoundSelect[Any], page_size: int, after: str | None
    ) -> Page[str]:
        """One keyset page of candidate keys out of a (possibly unioned) indexed source."""
        combined = source.subquery()
        statement = (
            select(combined.c.candidate_key).order_by(combined.c.candidate_key).limit(page_size + 1)
        )
        if after is not None:
            statement = statement.where(combined.c.candidate_key > after)
        keys = [row[0] for row in (await self._session.execute(statement)).all()]
        has_more = len(keys) > page_size
        visible = tuple(keys[:page_size])
        return Page(
            items=visible,
            has_more=has_more,
            next_key=visible[-1] if has_more and visible else None,
        )


def _group_by_key(
    rows: Sequence[RowMapping],
    column: str,
    build: Callable[[RowMapping], ItemT],
    limit: int,
) -> dict[str, tuple[ItemT, ...]]:
    """Fold ordered rows into per-key tuples, keeping one row past the ceiling.

    The extra row is what lets the caller report truncation rather than evaluating a DACL
    it does not know is incomplete.
    """
    grouped: dict[str, list[ItemT]] = {}
    for row in rows:
        bucket = grouped.setdefault(row[column], [])
        if len(bucket) <= limit:
            bucket.append(build(row))
    return {key: tuple(value) for key, value in grouped.items()}


def _share_root_key(share_key: str) -> str:
    r"""``fs01|finance`` to the resource key of ``\\FS01\Finance``.

    The share key already carries the two path components, so the root's identity is a
    function of it. Building the path here rather than storing a second pointer keeps the
    link between a share and its NTFS root derivable from either side.
    """
    server, separator, name = share_key.partition("|")
    if not separator or not server or not name:
        raise ValueError(
            f"{share_key!r} is not a share key. Expected 'server|share', which is what "
            "SmbShare.identity_key produces."
        )
    return UncPath(server=server, share=name).comparison_key


def _still_named(aces: Any, container_column: str) -> Any:
    """Whether a reference row still corresponds to an entry that is currently present.

    ``principal_references`` is an index over what has ever been observed and is never
    pruned, so it answers "could this principal be involved here" out of history. Confirming
    each row against the current ACL keeps the candidate set from outliving the entries that
    justified it, and costs one probe of ``ix_ntfs_aces_trustee`` / ``ix_smb_share_aces_
    trustee`` -- ``(trustee_key, container)``, which is exactly the pair being matched.
    """
    return (
        select(aces.c.ace_key)
        .where(
            aces.c[container_column] == principal_references.c.reference_key,
            aces.c.trustee_key == principal_references.c.principal_key,
        )
        .exists()
    )


def _trustee_predicate(trustee_key: str | None, trustee_sid: Sid | None) -> Any:
    if trustee_key is not None:
        return current_smb_share_aces.c.trustee_key == trustee_key
    if trustee_sid is not None:
        return current_smb_share_aces.c.trustee_sid == trustee_sid.value
    raise ValueError("A trustee query needs either trustee_key or trustee_sid.")


def _share_count_column() -> Any:
    """Shares per server, as a correlated count.

    A scalar subquery rather than a join with ``GROUP BY``: the page is at most a few
    hundred servers and the count is an index-only scan on ``ix_smb_shares_server``, while
    the join would have to be an outer one to keep a server that publishes nothing.
    """
    return (
        select(func.count())
        .select_from(current_smb_shares)
        .where(current_smb_shares.c.server_key == current_servers.c.server_key)
        .scalar_subquery()
        .label("share_count")
    )


def _page_size(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def _page(
    rows: Sequence[RowMapping],
    page_size: int,
    build: Callable[[RowMapping], ItemT],
    key_of: Callable[[ItemT], str],
) -> Page[ItemT]:
    has_more = len(rows) > page_size
    items = tuple(build(row) for row in rows[:page_size])
    return Page(
        items=items, has_more=has_more, next_key=key_of(items[-1]) if has_more and items else None
    )


#: The record constructors are public so that :mod:`app.history.repository` can rebuild
#: the same records from a version's stored state. One constructor per record, used by
#: both the current-state read and the point-in-time read, is what keeps a historical
#: answer from being a differently-shaped object than the live one it is compared with.
def server_record(row: RowLike) -> ServerRecord:
    return ServerRecord(
        server_key=row["server_key"],
        name=row["name"],
        dns_host_name=row["dns_host_name"],
        netbios_name=row["netbios_name"],
        computer_sid=row["computer_sid"],
        domain_sid=row["domain_sid"],
        is_domain_member=row["is_domain_member"],
        operating_system=row["operating_system"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
        share_count=int(row["share_count"]) if "share_count" in row else 0,
    )


def share_record(row: RowLike) -> ShareRecord:
    return ShareRecord(
        share_key=row["share_key"],
        server_key=row["server_key"],
        name=row["name"],
        local_path=row["local_path"],
        share_type=ShareType(row["share_type"]),
        description=row["description"],
        concurrent_user_limit=row["concurrent_user_limit"],
        caching_mode=row["caching_mode"],
        is_special=row["is_special"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
    )


def ntfs_resource_record(row: RowLike) -> NtfsResourceRecord:
    return NtfsResourceRecord(
        resource_key=row["resource_key"],
        path=row["path"],
        server_key=row["server_key"],
        share_key=row["share_key"],
        local_path=row["local_path"],
        owner_sid=row["owner_sid"],
        group_sid=row["group_sid"],
        dacl_present=bool(row["dacl_present"]),
        dacl_protected=bool(row["dacl_protected"]),
        inheritance_enabled=bool(row["inheritance_enabled"]),
        is_acl_boundary=bool(row["is_acl_boundary"]),
        ace_count=int(row["ace_count"]),
        depth_from_share_root=row["depth_from_share_root"],
        resource_kind=ResourceKind(row["resource_kind"]),
        boundary_reason=(
            None if row["boundary_reason"] is None else AclBoundaryReason(row["boundary_reason"])
        ),
        acl_hash=row["acl_hash"],
        parent_acl_hash=row["parent_acl_hash"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
    )


def ntfs_ace_record(row: RowLike) -> NtfsAceRecord:
    return NtfsAceRecord(
        ace_key=row["ace_key"],
        resource_key=row["resource_key"],
        trustee_sid=row["trustee_sid"],
        trustee_key=row["trustee_key"],
        ace_type=AceType(row["ace_type"]),
        access_mask=int(row["access_mask"]),
        ace_flags=int(row["ace_flags"]),
        source=AceSource(row["source"]),
        inherited_from=row["inherited_from"],
        order_index=row["order_index"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
    )


def share_ace_record(row: RowLike) -> ShareAceRecord:
    permission = row["permission"]
    return ShareAceRecord(
        ace_key=row["ace_key"],
        share_key=row["share_key"],
        trustee_sid=row["trustee_sid"],
        trustee_key=row["trustee_key"],
        ace_type=AceType(row["ace_type"]),
        access_mask=None if row["access_mask"] is None else int(row["access_mask"]),
        permission=None if permission is None else SharePermission(permission),
        right_token=row["right_token"],
        order_index=row["order_index"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
    )
