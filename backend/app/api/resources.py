r"""Resource-centric query endpoints: servers, shares, and raw share ACLs.

Everything here reports **observed facts**. A share ACL comes back as the entries a
collector read, in the order it read them, with the right rendered in whichever form the
source reported — a permission level from ``Get-SmbShareAccess``, a mask from a security
descriptor. Nothing is combined, intersected, or resolved into "who can actually reach
this". That is effective access, it needs the NTFS layer and the membership graph together,
and it will arrive as its own representation on its own route. The ACL response therefore
carries ``kind: "raw_smb_acl"`` so a client cannot mistake one for the other.

Two identifier rules, both of which exist to refuse a guess:

* A share is named by its storage key (``fs01|finance``) or by a UNC path
  (``\\FS01\Finance``, percent-encoded in the URL). ``\\FS01\Finance\Reports`` names a
  folder, not a share, and is rejected rather than truncated — the two have different ACLs.
  The forward-slash spelling ``//fs01/finance`` is the one form a URL cannot carry: a
  percent-encoded ``/`` is decoded before routing, so it can never be a single path segment.
  Send the backslash form or the key.
* A trustee is named by SID, or by a host-scoped key for one server's local group. Asking
  by bare SID for a BUILTIN group answers across **every** server that has one, because
  "which shares grant S-1-5-32-544" is a question with one correct answer and refusing it
  with a 409 would serve nobody.

A parent that was never observed is reported as ``null``, never hidden: a share whose
server no run has described, and an ACL whose share no run has described, are both what a
partial scan legitimately produces.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from app.api.deps import Session
from app.api.graph import PrincipalSummary, principal_summary
from app.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    PageInfo,
    decode_keyset_cursor,
    decode_offset_cursor,
    encode_keyset_cursor,
    encode_offset_cursor,
    normalize_limit,
)
from app.domain import (
    DomainValidationError,
    Server,
    Sid,
    parse_share_identifier,
)
from app.repositories import (
    MembershipRepository,
    ResourceRepository,
    ServerRecord,
    ShareAceRecord,
    ShareRecord,
)
from app.services.resources import ResolvedAce, ResourceService

router = APIRouter(prefix="/api/v1", tags=["resources"])

__all__ = ["router"]

ServerPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=255,
        description="A server's host name, as collected. Case-insensitive.",
    ),
]

SharePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=512,
        description=(
            "A share key (fs01|finance) or a UNC path (\\\\FS01\\Finance), percent-encoded. "
            "A path below the share root is rejected: it names a folder, not a share."
        ),
    ),
]

TrusteePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=512,
        description=(
            "A SID (S-1-5-21-...), or a host-scoped key (fs01|S-1-5-32-544) to ask about "
            "one server's local group rather than the SID everywhere."
        ),
    ),
]

LimitQuery = Annotated[
    int | None,
    Query(ge=1, le=MAX_LIMIT, description=f"Items per page (default {DEFAULT_LIMIT})."),
]

CursorQuery = Annotated[
    str | None, Query(description="Opaque cursor from a previous response's next_cursor.")
]


# --------------------------------------------------------------------- views


class ServerSummary(BaseModel):
    """A server as ADG knows it."""

    key: str = Field(description="Storage key: the case-folded host name.")
    name: str = Field(description="The name as collected, case preserved.")
    dns_host_name: str | None = None
    netbios_name: str | None = None
    computer_sid: str | None = None
    domain_sid: str | None = None
    is_domain_member: bool | None = Field(
        default=None, description="Null when no run established this, never a guessed false."
    )
    operating_system: str | None = None
    share_count: int = Field(description="Shares ADG holds for this server.")


class ProvenanceView(BaseModel):
    """Which runs saw an object, and when."""

    source_key: str
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID


class ServerDetail(ServerSummary):
    provenance: ProvenanceView


class ShareSummary(BaseModel):
    r"""A share, with the values that are functions of its identity computed rather than stored."""

    key: str = Field(description="Storage key: server|share, case-folded.")
    server_key: str
    name: str = Field(description="The share name as collected, case preserved.")
    unc_path: str = Field(description="Derived: \\\\server\\share. Never stored separately.")
    share_type: str
    local_path: str | None = Field(
        default=None, description="Path on the server the share publishes, when reported."
    )
    description: str | None = None
    concurrent_user_limit: int | None = None
    caching_mode: str | None = None
    is_special: bool | None = Field(
        default=None,
        description=(
            "The SMB server's Special flag. Null means the source did not say, which is not "
            "false: an ordinary hidden share such as Data$ is hidden and not Special."
        ),
    )
    is_hidden: bool = Field(description="Derived from the name ending in '$'.")
    is_administrative: bool = Field(description="Derived: ADMIN$, IPC$, or a drive share.")
    carries_file_permissions: bool = Field(
        description="Only a disk share has an NTFS layer beneath it."
    )


class ShareDetailView(ShareSummary):
    server: ServerSummary | None = Field(
        default=None,
        description=(
            "Null when no run has described the server. The share was still observed; only "
            "the machine serving it is undescribed."
        ),
    )
    ace_count: int = Field(description="Share-level ACEs recorded for this share.")
    provenance: ProvenanceView


class ShareAceView(BaseModel):
    """One share-level ACE, exactly as read. Not an access decision."""

    ace_key: str
    trustee: PrincipalSummary = Field(
        description=(
            "The principal this entry names. resolved=false means no run has described the "
            "SID — an orphaned trustee, which is a finding rather than an error."
        )
    )
    ace_type: str = Field(description="allow or deny, as the descriptor reported it.")
    access_mask: int | None = Field(
        default=None, description="Present when the source reported a mask."
    )
    permission: str | None = Field(
        default=None,
        description="Present when the source reported a level (read / change / full).",
    )
    right: str = Field(
        description=(
            "Whichever form the source reported, rendered: 'change' or '0x001301bf'. Never "
            "converted between the two — a level and a mask are different readings."
        )
    )
    order_index: int | None = Field(
        default=None, description="Position in the DACL as read; null when not reported."
    )
    provenance: ProvenanceView


class ServersResponse(BaseModel):
    items: list[ServerSummary]
    page: PageInfo


class SharesResponse(BaseModel):
    server: ServerSummary | None = Field(
        default=None, description="Null when shares exist for a server no run has described."
    )
    items: list[ShareSummary]
    page: PageInfo


class RawShareAclResponse(BaseModel):
    """A share's ACL as observed. Deliberately not an effective-access answer."""

    kind: Literal["raw_smb_acl"] = Field(
        default="raw_smb_acl",
        description=(
            "These are raw share-layer facts. Effective access additionally requires the "
            "NTFS layer and group expansion, and is a different representation."
        ),
    )
    share_key: str
    share: ShareSummary | None = Field(
        default=None,
        description="Null when ACEs are recorded for a share no run has described.",
    )
    entries: list[ShareAceView]
    page: PageInfo


class TrusteeShareView(BaseModel):
    share_key: str
    share: ShareSummary | None = Field(
        default=None, description="Null for an ACL observed without its share."
    )
    aces: list[ShareAceView] = Field(description="Only the entries naming the queried trustee.")


class TrusteeSharesResponse(BaseModel):
    kind: Literal["raw_smb_acl"] = "raw_smb_acl"
    trustee: PrincipalSummary
    scope: str = Field(
        description=(
            "sid: every server where the SID appears. principal: one host-scoped principal."
        )
    )
    items: list[TrusteeShareView]
    page: PageInfo


# ----------------------------------------------------------------- endpoints


@router.get("/servers", response_model=ServersResponse, summary="Servers ADG has observed")
async def list_servers(
    session: Session, limit: LimitQuery = None, cursor: CursorQuery = None
) -> ServersResponse:
    repository = ResourceRepository(session)
    page_size = normalize_limit(limit)
    page = await repository.list_servers(limit=page_size, after=decode_keyset_cursor(cursor))
    return ServersResponse(
        items=[_server_summary(record) for record in page.items],
        page=PageInfo(
            limit=page_size,
            has_more=page.has_more,
            next_cursor=encode_keyset_cursor(page.next_key) if page.next_key else None,
            total=await repository.count_servers(),
        ),
    )


@router.get(
    "/servers/{server}",
    response_model=ServerDetail,
    summary="One server",
    responses={404: {"description": "No run has described this server."}},
)
async def get_server(server: ServerPath, session: Session) -> ServerDetail:
    key = _server_key(server)
    record = await ResourceRepository(session).get_server(key)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No server {key!r}. Absence here means no run has described it, not that "
                "it does not exist."
            ),
        )
    return ServerDetail(**_server_summary(record).model_dump(), provenance=_provenance(record))


@router.get(
    "/servers/{server}/shares",
    response_model=SharesResponse,
    summary="Shares published by one server",
)
async def list_shares(
    server: ServerPath,
    session: Session,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> SharesResponse:
    key = _server_key(server)
    repository = ResourceRepository(session)
    page_size = normalize_limit(limit)
    page = await repository.list_shares(key, limit=page_size, after=decode_keyset_cursor(cursor))
    record = await repository.get_server(key)
    if record is None and not page.items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No server {key!r} and no shares recorded for that name.",
        )
    return SharesResponse(
        # A server row may be missing while its shares are present: an ACL scan can describe
        # shares before anything describes the machine.
        server=None if record is None else _server_summary(record),
        items=[_share_summary(share) for share in page.items],
        page=PageInfo(
            limit=page_size,
            has_more=page.has_more,
            next_cursor=encode_keyset_cursor(page.next_key) if page.next_key else None,
            total=await repository.count_shares(key),
        ),
    )


@router.get(
    "/shares/{share}",
    response_model=ShareDetailView,
    summary="One share",
    responses={
        404: {"description": "No run has described this share."},
        422: {"description": "The identifier is not a share: a folder path, or malformed."},
    },
)
async def get_share(share: SharePath, session: Session) -> ShareDetailView:
    identity = parse_share_identifier(share)
    service = _service(session)
    detail = await service.share_detail(identity.identity_key)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No share {identity.unc_path.value!r}. If a collector read its ACL but not "
                "the share list, the entries are still available at "
                f"/api/v1/shares/{identity.identity_key}/acl."
            ),
        )
    return ShareDetailView(
        **_share_summary(detail.share).model_dump(),
        server=None if detail.server is None else _server_summary(detail.server),
        ace_count=detail.ace_count,
        provenance=_provenance(detail.share),
    )


@router.get(
    "/shares/{share}/acl",
    response_model=RawShareAclResponse,
    summary="Raw share-level ACL for one share",
    responses={
        404: {"description": "Neither the share nor any ACE for it has been observed."},
        422: {"description": "The identifier is not a share: a folder path, or malformed."},
    },
)
async def get_share_acl(
    share: SharePath,
    session: Session,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> RawShareAclResponse:
    identity = parse_share_identifier(share)
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    acl = await _service(session).share_acl(identity.identity_key, limit=page_size, offset=offset)
    if acl.share is None and not acl.entries and offset == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No share {identity.unc_path.value!r} and no ACL recorded for it. An empty "
                "ACL would be reported as an empty list, so this means nothing was observed."
            ),
        )
    return RawShareAclResponse(
        share_key=acl.share_key,
        share=None if acl.share is None else _share_summary(acl.share),
        entries=[_ace_view(entry) for entry in acl.entries],
        page=PageInfo(
            limit=page_size,
            has_more=acl.has_more,
            next_cursor=encode_offset_cursor(offset + len(acl.entries)) if acl.has_more else None,
            total=acl.total,
        ),
    )


@router.get(
    "/principals/{trustee}/shares",
    response_model=TrusteeSharesResponse,
    summary="Shares whose ACL names a SID",
    responses={422: {"description": "The identifier is neither a SID nor a host-scoped key."}},
)
async def shares_for_trustee(
    trustee: TrusteePath,
    session: Session,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> TrusteeSharesResponse:
    trustee_key, sid = _trustee(trustee)
    page_size = normalize_limit(limit)
    result = await _service(session).shares_for_trustee(
        trustee_key=trustee_key,
        trustee_sid=sid,
        limit=page_size,
        after=decode_keyset_cursor(cursor),
    )
    membership = MembershipRepository(session)
    key = trustee_key or sid.value if sid is not None else trustee
    return TrusteeSharesResponse(
        trustee=principal_summary(key, await membership.get_principal(key)),
        scope="principal" if trustee_key is not None else "sid",
        items=[
            TrusteeShareView(
                share_key=item.share_key,
                share=None if item.share is None else _share_summary(item.share),
                aces=[
                    _ace_view(
                        ResolvedAce(ace=ace, principal=result.principals.get(ace.trustee_key))
                    )
                    for ace in item.aces
                ],
            )
            for item in result.page.items
        ],
        page=PageInfo(
            limit=page_size,
            has_more=result.page.has_more,
            next_cursor=(
                encode_keyset_cursor(result.page.next_key) if result.page.next_key else None
            ),
            total=result.total,
        ),
    )


# ------------------------------------------------------------------- helpers


def _service(session: Session) -> ResourceService:
    return ResourceService(ResourceRepository(session), MembershipRepository(session))


def _server_key(name: str) -> str:
    """Validate a host name and return its storage key.

    Asks the domain object rather than case-folding here, so a UNC path or a path fragment
    is refused by the same rule that refuses one on ingest.
    """
    return Server(name=name.strip()).identity_key


def _trustee(identifier: str) -> tuple[str | None, Sid | None]:
    """Split a trustee identifier into a host-scoped key or a bare SID.

    A host-scoped key asks about one server's principal. A bare SID asks about the SID
    wherever it appears, which for a BUILTIN SID spans servers on purpose.
    """
    text = identifier.strip()
    if "|" in text:
        host, _, sid_text = text.rpartition("|")
        if not host or Sid.try_parse(sid_text) is None:
            raise DomainValidationError(
                f"{identifier!r} is not a host-scoped principal key. Expected "
                "'host|S-1-5-32-544', or a bare SID.",
                value=identifier,
                field="trustee",
            )
        return f"{host.casefold()}|{Sid(sid_text).value}", None

    sid = Sid.try_parse(text)
    if sid is None:
        raise DomainValidationError(
            f"{identifier!r} is not a SID. A trustee is identified by SID; names are "
            "metadata and cannot be looked up as identity.",
            value=identifier,
            field="trustee",
        )
    return None, sid


def _server_summary(record: ServerRecord) -> ServerSummary:
    return ServerSummary(
        key=record.server_key,
        name=record.name,
        dns_host_name=record.dns_host_name,
        netbios_name=record.netbios_name,
        computer_sid=record.computer_sid,
        domain_sid=record.domain_sid,
        is_domain_member=record.is_domain_member,
        operating_system=record.operating_system,
        share_count=record.share_count,
    )


def _share_summary(record: ShareRecord) -> ShareSummary:
    return ShareSummary(
        key=record.share_key,
        server_key=record.server_key,
        name=record.name,
        # Derived from the identity every time, so it cannot disagree with it.
        unc_path=record.unc_path.value,
        share_type=record.share_type.value,
        local_path=record.local_path,
        description=record.description,
        concurrent_user_limit=record.concurrent_user_limit,
        caching_mode=record.caching_mode,
        is_special=record.is_special,
        is_hidden=record.is_hidden,
        is_administrative=record.is_administrative,
        carries_file_permissions=record.carries_file_permissions,
    )


def _ace_view(entry: ResolvedAce) -> ShareAceView:
    ace = entry.ace
    return ShareAceView(
        ace_key=ace.ace_key,
        trustee=principal_summary(ace.trustee_key, entry.principal),
        ace_type=ace.ace_type.value,
        access_mask=ace.access_mask,
        permission=None if ace.permission is None else ace.permission.value,
        right=ace.right_token,
        order_index=ace.order_index,
        provenance=_provenance(ace),
    )


def _provenance(record: ServerRecord | ShareRecord | ShareAceRecord) -> ProvenanceView:
    return ProvenanceView(
        source_key=record.source_key,
        first_observed_at=record.first_observed_at,
        first_observed_run_id=record.first_observed_run_id,
        last_observed_at=record.last_observed_at,
        last_observed_run_id=record.last_observed_run_id,
    )
