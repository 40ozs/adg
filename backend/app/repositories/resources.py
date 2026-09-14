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
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import RowMapping, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    AclAceFacts,
    LocalPath,
    NormalizedAcl,
    SharePermission,
    ShareType,
    Sid,
    SmbShare,
    UncPath,
    normalize_acl,
    parse_local_path,
    parse_unc_path,
)
from app.models.schema import (
    ReferenceKind,
    ntfs_aces,
    ntfs_resources,
    principal_references,
    servers,
    smb_share_aces,
    smb_shares,
)
from app.repositories.membership import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page

__all__ = [
    "NtfsAceRecord",
    "NtfsAclRecomputation",
    "NtfsResourceRecord",
    "ResourceRepository",
    "ServerRecord",
    "ShareAceRecord",
    "ShareRecord",
    "ShareReferenceRecord",
]

ItemT = TypeVar("ItemT")


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
    acl_hash: str | None
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


class ResourceRepository:
    """Server, share, and share-ACL queries against one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------- servers

    async def list_servers(
        self, *, limit: int = DEFAULT_PAGE_SIZE, after: str | None = None
    ) -> Page[ServerRecord]:
        """Every known server, ordered by key."""
        page_size = _page_size(limit)
        statement: Select[Any] = (
            select(servers, _share_count_column())
            .order_by(servers.c.server_key)
            .limit(page_size + 1)
        )
        if after is not None:
            statement = statement.where(servers.c.server_key > after)

        rows = (await self._session.execute(statement)).mappings().all()
        return _page(rows, page_size, _server_record, lambda record: record.server_key)

    async def get_server(self, server_key: str) -> ServerRecord | None:
        row = (
            (
                await self._session.execute(
                    select(servers, _share_count_column()).where(
                        servers.c.server_key == server_key.casefold()
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _server_record(row)

    async def count_servers(self) -> int:
        return int(
            (await self._session.execute(select(func.count()).select_from(servers))).scalar_one()
        )

    async def servers_by_keys(self, keys: Sequence[str]) -> dict[str, ServerRecord]:
        if not keys:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(servers, _share_count_column()).where(
                        servers.c.server_key.in_(sorted(set(keys)))
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["server_key"]: _server_record(row) for row in rows}

    # ----------------------------------------------------------------- shares

    async def list_shares(
        self, server_key: str, *, limit: int = DEFAULT_PAGE_SIZE, after: str | None = None
    ) -> Page[ShareRecord]:
        """Shares published by one server, ordered by key."""
        page_size = _page_size(limit)
        statement: Select[Any] = (
            select(smb_shares)
            .where(smb_shares.c.server_key == server_key.casefold())
            .order_by(smb_shares.c.share_key)
            .limit(page_size + 1)
        )
        if after is not None:
            statement = statement.where(smb_shares.c.share_key > after)

        rows = (await self._session.execute(statement)).mappings().all()
        return _page(rows, page_size, _share_record, lambda record: record.share_key)

    async def count_shares(self, server_key: str) -> int:
        statement = (
            select(func.count())
            .select_from(smb_shares)
            .where(smb_shares.c.server_key == server_key.casefold())
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def get_share(self, share_key: str) -> ShareRecord | None:
        row = (
            (
                await self._session.execute(
                    select(smb_shares).where(smb_shares.c.share_key == share_key.casefold())
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _share_record(row)

    async def shares_by_keys(self, keys: Sequence[str]) -> dict[str, ShareRecord]:
        if not keys:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(smb_shares).where(smb_shares.c.share_key.in_(sorted(set(keys))))
                )
            )
            .mappings()
            .all()
        )
        return {row["share_key"]: _share_record(row) for row in rows}

    async def has_aces(self, share_key: str) -> bool:
        """Whether any ACE names this share, even if the share itself was never described."""
        statement = select(
            select(smb_share_aces.c.ace_key)
            .where(smb_share_aces.c.share_key == share_key.casefold())
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
            select(smb_share_aces)
            .where(smb_share_aces.c.share_key == share_key.casefold())
            .order_by(smb_share_aces.c.order_index.nulls_last(), smb_share_aces.c.ace_key)
            .offset(max(0, offset))
            .limit(page_size + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        has_more = len(rows) > page_size
        return tuple(_ace_record(row) for row in rows[:page_size]), has_more

    async def count_acl(self, share_key: str) -> int:
        statement = (
            select(func.count())
            .select_from(smb_share_aces)
            .where(smb_share_aces.c.share_key == share_key.casefold())
        )
        return int((await self._session.execute(statement)).scalar_one())

    # --------------------------------------------------------- NTFS resources

    async def get_ntfs_resource(self, resource_key: str) -> NtfsResourceRecord | None:
        """One directory, or ``None`` when no run has read its descriptor."""
        row = (
            (
                await self._session.execute(
                    select(ntfs_resources).where(
                        ntfs_resources.c.resource_key == resource_key.casefold()
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _ntfs_resource_record(row)

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
                    select(ntfs_resources).where(
                        ntfs_resources.c.resource_key.in_(sorted({key.casefold() for key in keys}))
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["resource_key"]: _ntfs_resource_record(row) for row in rows}

    async def has_ntfs_aces(self, resource_key: str) -> bool:
        """Whether any ACE names this path, even if the directory itself was never read."""
        statement = select(
            select(ntfs_aces.c.ace_key)
            .where(ntfs_aces.c.resource_key == resource_key.casefold())
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
            select(ntfs_aces)
            .where(ntfs_aces.c.resource_key == resource_key.casefold())
            .order_by(ntfs_aces.c.order_index.nulls_last(), ntfs_aces.c.ace_key)
            .offset(max(0, offset))
            .limit(page_size + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        has_more = len(rows) > page_size
        return tuple(_ntfs_ace_record(row) for row in rows[:page_size]), has_more

    async def count_ntfs_acl(self, resource_key: str) -> int:
        statement = (
            select(func.count())
            .select_from(ntfs_aces)
            .where(ntfs_aces.c.resource_key == resource_key.casefold())
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def recompute_acl_hash(self, resource: NtfsResourceRecord) -> NtfsAclRecomputation:
        """Hash the ACEs this database holds for a path, for comparison with the reported one.

        Deliberately unpaged: the digest is over the whole DACL, and hashing a page of it
        would produce a number that looks like an answer and is not one. A DACL is tens of
        entries, so reading them all costs one indexed scan.
        """
        rows = (
            (
                await self._session.execute(
                    select(ntfs_aces)
                    .where(ntfs_aces.c.resource_key == resource.resource_key)
                    .order_by(ntfs_aces.c.order_index.nulls_last(), ntfs_aces.c.ace_key)
                )
            )
            .mappings()
            .all()
        )
        entries = [_ntfs_ace_record(row).acl_facts for row in rows]
        # A NULL DACL carries no entries by definition. Rows can nonetheless survive from a
        # run that read a real DACL before the newest run found the descriptor replaced, so
        # they are excluded from the document rather than allowed to contradict it -- and
        # stored_ace_count still reports every row, so ace_count_agrees exposes the split.
        hashed = [] if not resource.dacl_present else entries
        return NtfsAclRecomputation(
            reported=resource.acl_hash,
            normalized=normalize_acl(
                dacl_present=resource.dacl_present,
                dacl_protected=resource.dacl_protected,
                aces=hashed,
            ),
            stored_ace_count=len(entries),
            declared_ace_count=resource.ace_count,
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
            select(smb_share_aces.c.share_key)
            .where(predicate)
            .group_by(smb_share_aces.c.share_key)
            .order_by(smb_share_aces.c.share_key)
            .limit(page_size + 1)
        )
        if after is not None:
            keys_statement = keys_statement.where(smb_share_aces.c.share_key > after)

        keys = [row[0] for row in (await self._session.execute(keys_statement)).all()]
        has_more = len(keys) > page_size
        visible = keys[:page_size]
        if not visible:
            return Page(items=(), has_more=False, next_key=None)

        ace_rows = (
            (
                await self._session.execute(
                    select(smb_share_aces)
                    .where(smb_share_aces.c.share_key.in_(visible), predicate)
                    .order_by(
                        smb_share_aces.c.share_key,
                        smb_share_aces.c.order_index.nulls_last(),
                        smb_share_aces.c.ace_key,
                    )
                )
            )
            .mappings()
            .all()
        )
        grouped: dict[str, list[ShareAceRecord]] = {key: [] for key in visible}
        for row in ace_rows:
            grouped[row["share_key"]].append(_ace_record(row))

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
            select(func.count(func.distinct(smb_share_aces.c.share_key)))
            .select_from(smb_share_aces)
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


def _trustee_predicate(trustee_key: str | None, trustee_sid: Sid | None) -> Any:
    if trustee_key is not None:
        return smb_share_aces.c.trustee_key == trustee_key
    if trustee_sid is not None:
        return smb_share_aces.c.trustee_sid == trustee_sid.value
    raise ValueError("A trustee query needs either trustee_key or trustee_sid.")


def _share_count_column() -> Any:
    """Shares per server, as a correlated count.

    A scalar subquery rather than a join with ``GROUP BY``: the page is at most a few
    hundred servers and the count is an index-only scan on ``ix_smb_shares_server``, while
    the join would have to be an outer one to keep a server that publishes nothing.
    """
    return (
        select(func.count())
        .select_from(smb_shares)
        .where(smb_shares.c.server_key == servers.c.server_key)
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


def _server_record(row: RowMapping) -> ServerRecord:
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


def _share_record(row: RowMapping) -> ShareRecord:
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


def _ntfs_resource_record(row: RowMapping) -> NtfsResourceRecord:
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
        acl_hash=row["acl_hash"],
        source_key=row["source_key"],
        first_observed_at=row["first_observed_at"],
        first_observed_run_id=row["first_observed_run_id"],
        last_observed_at=row["last_observed_at"],
        last_observed_run_id=row["last_observed_run_id"],
    )


def _ntfs_ace_record(row: RowMapping) -> NtfsAceRecord:
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


def _ace_record(row: RowMapping) -> ShareAceRecord:
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
