r"""Resource-centric query endpoints: servers, shares, directories, and raw ACLs.

Everything here reports **observed facts**. A share ACL comes back as the entries a
collector read, in the order it read them, with the right rendered in whichever form the
source reported — a permission level from ``Get-SmbShareAccess``, a mask from a security
descriptor. An NTFS ACL comes back the same way: raw masks, the raw flags byte, evaluation
order preserved. Nothing is combined, intersected, or resolved into "who can actually reach
this". That is effective access, it needs both layers and the membership graph together,
and it will arrive as its own representation on its own route. Each ACL response therefore
carries a ``kind`` — ``raw_smb_acl`` or ``raw_ntfs_acl`` — so a client cannot mistake one
for the other, or either for an access decision.

**The two layers are separate routes on purpose.** ``/shares/{share}/acl`` is what the SMB
server grants; ``/shares/{share}/root-acl`` is what the file system grants on the directory
that share publishes. Remote access is limited by both, and local access — someone logged
on at the console, or a service running on the box — is limited only by the second. A single
merged answer could not express that, and would quietly become an effective-access claim.

Three identifier rules, all of which exist to refuse a guess:

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
* A directory is named by its full UNC path — ``\\FS01\Finance``, or
  ``\\FS01\Finance\Reports`` once the recursive scan exists — percent-encoded, in the
  backslash spelling for the same routing reason. A share *key* is not accepted here:
  ``fs01|finance`` names a share, and a share and the directory beneath it have different
  ACLs. Ask ``/shares/{share}/root-acl`` to go from one to the other.

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
    ACL_HASH_ALGORITHM,
    ACL_NORMAL_FORM_VERSION,
    DomainValidationError,
    NtfsRight,
    Server,
    Sid,
    parse_share_identifier,
    parse_unc_path,
)
from app.repositories import (
    MembershipRepository,
    NtfsAceRecord,
    NtfsAclRecomputation,
    NtfsResourceRecord,
    ResourceRepository,
    ServerRecord,
    ShareAceRecord,
    ShareRecord,
)
from app.services.resources import (
    NtfsAcl,
    ResolvedAce,
    ResolvedNtfsAce,
    ResourceService,
)

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

ResourcePath = Annotated[
    str,
    Path(
        min_length=5,
        max_length=1024,
        description=(
            "A directory's canonical UNC path (\\\\FS01\\Finance), percent-encoded. A share "
            "key is not accepted: a share and the directory it publishes have different ACLs."
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


class NtfsResourceSummary(BaseModel):
    r"""A directory's descriptor-level facts. Not its ACL, and not an access decision."""

    key: str = Field(description="Storage key: the case-folded canonical UNC path.")
    path: str = Field(description="The path as collected, case preserved.")
    server_key: str
    share_key: str = Field(
        description="The share this path sits under, derived from the path itself."
    )
    local_path: str | None = Field(
        default=None, description="The path on the server, when a collector reported it."
    )
    is_share_root: bool = Field(description="Derived: the directory the share publishes.")
    depth_from_share_root: int | None = None
    owner_sid: str | None = Field(
        default=None,
        description=(
            "The owner holds implicit READ_CONTROL and WRITE_DAC whatever the DACL says, so "
            "it is reported beside the DACL rather than folded into it."
        ),
    )
    group_sid: str | None = None
    dacl_present: bool = Field(
        description=(
            "False is a NULL DACL: every user has full access, regardless of the entry list "
            "being empty. Never the same as a present-but-empty DACL."
        )
    )
    dacl_protected: bool = Field(
        description="SE_DACL_PROTECTED: this directory blocks inherited entries."
    )
    inheritance_enabled: bool
    is_acl_boundary: bool = Field(
        description="This directory's DACL differs from its parent's. Blocking implies it."
    )
    grants_everyone_full_access: bool = Field(
        description="Derived from dacl_present. A NULL DACL, and always a finding."
    )
    denies_everyone: bool = Field(
        description=(
            "Derived: a present but empty DACL. Nobody has access through it; the owner "
            "keeps implicit control rights."
        )
    )
    declared_ace_count: int = Field(
        description="How many entries the descriptor said it had, as the collector read it."
    )


class NtfsResourceDetailView(NtfsResourceSummary):
    share: ShareSummary | None = Field(
        default=None,
        description="Null when no run has described the share this directory sits under.",
    )
    server: ServerSummary | None = None
    stored_ace_count: int = Field(
        description=(
            "How many ACEs ADG actually holds. Below declared_ace_count means entries were "
            "read and never arrived — a coverage gap, reported rather than reconciled away."
        )
    )
    provenance: ProvenanceView


class AclHashView(BaseModel):
    """The collector's digest of the DACL, beside the one the server derives from its rows.

    Both, never one. They agree in the ordinary case; a disagreement means the stored entries
    are not the ones that were hashed, and choosing a winner here would bury that.
    """

    reported: str | None = Field(
        default=None,
        description="What the collector computed from the descriptor it read. Null if none.",
    )
    computed: str = Field(
        description="What the server computes from the ACEs it holds, over the whole DACL."
    )
    agrees: bool | None = Field(
        default=None,
        description="Null when nothing was reported — unknown, not a disagreement.",
    )
    algorithm: str = Field(description="Digest algorithm, for a client that verifies it.")
    normal_form_version: str = Field(
        description="The normalized-document format the digest is taken over."
    )
    ordered: bool = Field(
        description=(
            "Whether every stored entry carried a DACL position. An unordered digest can "
            "never equal an ordered one, because evaluation order is part of the ACL."
        )
    )
    declared_ace_count: int
    stored_ace_count: int
    ace_count_agrees: bool = Field(
        description="False means entries the descriptor declared are missing from storage."
    )


class NtfsAceView(BaseModel):
    """One NTFS ACE, exactly as read. Not an access decision."""

    ace_key: str
    trustee: PrincipalSummary = Field(
        description=(
            "The principal this entry names. resolved=false means no run has described the "
            "SID — an orphaned trustee, which is a finding rather than an error."
        )
    )
    ace_type: str = Field(description="allow or deny, as the descriptor reported it.")
    access_mask: int = Field(
        description="The raw mask, generic bits included. Never expanded or translated."
    )
    rights: list[str] = Field(
        description=(
            "The mask's recognized bits, named. A rendering of access_mask and nothing "
            "more: the mask stays authoritative, and unrecognized_bits reports what this "
            "list could not name rather than dropping it."
        )
    )
    unrecognized_bits: int = Field(
        description="Mask bits matching no known right. Kept visible instead of ignored."
    )
    ace_flags: int = Field(
        description=(
            "The raw ACE_HEADER.AceFlags byte: OBJECT_INHERIT 0x01, CONTAINER_INHERIT 0x02, "
            "NO_PROPAGATE_INHERIT 0x04, INHERIT_ONLY 0x08, INHERITED 0x10."
        )
    )
    source: str = Field(description="explicit or inherited — where to make a fix.")
    inherited_from: str | None = Field(
        default=None, description="The ancestor Windows named as the origin, when it named one."
    )
    is_inheritable: bool = Field(description="Derived from the flags byte.")
    applies_to_this_object: bool = Field(
        description=(
            "False for an INHERIT_ONLY entry, which grants nothing on this directory. "
            "Showing such an entry as a grant here would report access that does not exist."
        )
    )
    order_index: int | None = Field(
        default=None, description="Position in the DACL as read; null when not reported."
    )
    provenance: ProvenanceView


class ShareDetailView(ShareSummary):
    server: ServerSummary | None = Field(
        default=None,
        description=(
            "Null when no run has described the server. The share was still observed; only "
            "the machine serving it is undescribed."
        ),
    )
    ace_count: int = Field(description="Share-level ACEs recorded for this share.")
    root_resource: NtfsResourceSummary | None = Field(
        default=None,
        description=(
            "The NTFS root of this share — the directory it publishes — when a file-system "
            "run has read it. Null means the share's NTFS permissions are unknown, not that "
            "they are open: the two layers are collected by different runs. The entries "
            "themselves are at /shares/{share}/root-acl."
        ),
    )
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


class RawNtfsAclResponse(BaseModel):
    """A directory's NTFS ACL as observed. Deliberately not an effective-access answer."""

    kind: Literal["raw_ntfs_acl"] = Field(
        default="raw_ntfs_acl",
        description=(
            "These are raw file-system facts. They are not the share layer, and they are "
            "not effective access — that needs both layers and group expansion, and is a "
            "different representation on a different route."
        ),
    )
    resource_key: str
    resource: NtfsResourceSummary | None = Field(
        default=None,
        description=(
            "Null when ACEs are recorded for a path no ntfs_resource observation has "
            "described. The entries are still real; only the descriptor-level facts around "
            "them — owner, NULL-DACL state, protection — are unknown."
        ),
    )
    acl_hash: AclHashView | None = Field(
        default=None,
        description=(
            "Null when the directory itself has not been observed: dacl_present and "
            "dacl_protected are part of the hashed document, and guessing either would "
            "produce a digest that is wrong in a way nobody could see."
        ),
    )
    entries: list[NtfsAceView]
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
        root_resource=(
            None if detail.root_resource is None else _ntfs_resource_summary(detail.root_resource)
        ),
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
    "/shares/{share}/root-acl",
    response_model=RawNtfsAclResponse,
    summary="Raw NTFS ACL of the directory one share publishes",
    responses={
        404: {"description": "No NTFS run has read this share's root directory."},
        422: {"description": "The identifier is not a share: a folder path, or malformed."},
    },
)
async def get_share_root_acl(
    share: SharePath,
    session: Session,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> RawNtfsAclResponse:
    r"""The file-system half of what a share grants.

    Separate from ``/shares/{share}/acl`` because the two layers are separate: remote access
    is limited by the share ACL *and* this one, and anybody working at the console or in a
    service on that machine is limited only by this one. A caller comparing them sees which
    layer is doing the restricting, which a single merged number could never show.
    """
    identity = parse_share_identifier(share)
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    acl = await _service(session).share_root_acl(
        identity.identity_key, limit=page_size, offset=offset
    )
    if acl.resource is None and not acl.entries and offset == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No NTFS descriptor recorded for {identity.unc_path.value!r}. The share "
                "layer is collected separately, so this means no file-system run has read "
                "the directory — not that it grants nothing. Its share ACL, if one was "
                f"collected, is at /api/v1/shares/{identity.identity_key}/acl."
            ),
        )
    return _ntfs_acl_response(acl, page_size=page_size, offset=offset)


@router.get(
    "/resources/{resource}",
    response_model=NtfsResourceDetailView,
    summary="One directory's NTFS descriptor facts",
    responses={
        404: {"description": "No run has read this directory's descriptor."},
        422: {"description": "The identifier is not a canonical UNC path."},
    },
)
async def get_resource(resource: ResourcePath, session: Session) -> NtfsResourceDetailView:
    key = _resource_key(resource)
    detail = await _service(session).resource_detail(key)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No directory {key!r}. Absence here means no run has read its security "
                "descriptor, not that the directory does not exist."
            ),
        )
    return NtfsResourceDetailView(
        **_ntfs_resource_summary(detail.resource).model_dump(),
        share=None if detail.share is None else _share_summary(detail.share),
        server=None if detail.server is None else _server_summary(detail.server),
        stored_ace_count=detail.stored_ace_count,
        provenance=_provenance(detail.resource),
    )


@router.get(
    "/resources/{resource}/acl",
    response_model=RawNtfsAclResponse,
    summary="Raw NTFS ACL for one directory",
    responses={
        404: {"description": "Neither the directory nor any ACE for it has been observed."},
        422: {"description": "The identifier is not a canonical UNC path."},
    },
)
async def get_resource_acl(
    resource: ResourcePath,
    session: Session,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> RawNtfsAclResponse:
    key = _resource_key(resource)
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    acl = await _service(session).ntfs_acl(key, limit=page_size, offset=offset)
    if acl.resource is None and not acl.entries and offset == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No directory {key!r} and no ACL recorded for it. An empty DACL would be "
                "reported as an empty list with dacl_present true, so this means nothing "
                "was observed."
            ),
        )
    return _ntfs_acl_response(acl, page_size=page_size, offset=offset)


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


def _resource_key(identifier: str) -> str:
    r"""Validate a directory identifier and return its storage key.

    Goes through :func:`app.domain.parse_unc_path`, the same parser ingestion uses, so a
    path this endpoint accepts is exactly a path that could have been stored. A share key
    is refused rather than converted: ``fs01|finance`` names a share, and answering a
    question about a share with a directory's ACL — or the reverse — is exactly the
    layer confusion the two routes exist to prevent.
    """
    text = identifier.strip()
    if "|" in text:
        raise DomainValidationError(
            f"{identifier!r} looks like a share key. A directory is named by its UNC path, "
            "for example \\\\FS01\\Finance. To go from a share to the NTFS ACL of the "
            "directory it publishes, ask /api/v1/shares/{share}/root-acl.",
            value=identifier,
            field="resource",
        )
    return parse_unc_path(text).comparison_key


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


def _ntfs_resource_summary(record: NtfsResourceRecord) -> NtfsResourceSummary:
    return NtfsResourceSummary(
        key=record.resource_key,
        path=record.path,
        server_key=record.server_key,
        share_key=record.share_key,
        local_path=record.local_path,
        # Derived from the path every time, so it cannot disagree with it.
        is_share_root=record.is_share_root,
        depth_from_share_root=record.depth_from_share_root,
        owner_sid=record.owner_sid,
        group_sid=record.group_sid,
        dacl_present=record.dacl_present,
        dacl_protected=record.dacl_protected,
        inheritance_enabled=record.inheritance_enabled,
        is_acl_boundary=record.is_acl_boundary,
        grants_everyone_full_access=record.grants_everyone_full_access,
        denies_everyone=record.denies_everyone,
        declared_ace_count=record.ace_count,
    )


def _ntfs_ace_view(entry: ResolvedNtfsAce) -> NtfsAceView:
    ace = entry.ace
    # Through the domain type, so the mask is named by the one implementation that knows
    # what the bits mean — and reports what it could not name instead of discarding it.
    rights = NtfsRight(0)
    for right in NtfsRight:
        if right.value and ace.access_mask & right.value == right.value:
            rights |= right
    return NtfsAceView(
        ace_key=ace.ace_key,
        trustee=principal_summary(ace.trustee_key, entry.principal),
        ace_type=ace.ace_type.value,
        access_mask=ace.access_mask,
        rights=sorted(right.name for right in NtfsRight if right.name and right & rights),
        unrecognized_bits=ace.access_mask & ~int(rights),
        ace_flags=ace.ace_flags,
        source=ace.source.value,
        inherited_from=ace.inherited_from,
        is_inheritable=ace.is_inheritable,
        applies_to_this_object=ace.applies_to_this_object,
        order_index=ace.order_index,
        provenance=_provenance(ace),
    )


def _acl_hash_view(recomputation: NtfsAclRecomputation) -> AclHashView:
    return AclHashView(
        reported=recomputation.reported,
        computed=recomputation.computed,
        agrees=recomputation.agrees,
        algorithm=ACL_HASH_ALGORITHM,
        normal_form_version=ACL_NORMAL_FORM_VERSION,
        ordered=recomputation.normalized.ordered,
        declared_ace_count=recomputation.declared_ace_count,
        stored_ace_count=recomputation.stored_ace_count,
        ace_count_agrees=recomputation.ace_count_agrees,
    )


def _ntfs_acl_response(acl: NtfsAcl, *, page_size: int, offset: int) -> RawNtfsAclResponse:
    return RawNtfsAclResponse(
        resource_key=acl.resource_key,
        resource=None if acl.resource is None else _ntfs_resource_summary(acl.resource),
        acl_hash=None if acl.recomputation is None else _acl_hash_view(acl.recomputation),
        entries=[_ntfs_ace_view(entry) for entry in acl.entries],
        page=PageInfo(
            limit=page_size,
            has_more=acl.has_more,
            next_cursor=encode_offset_cursor(offset + len(acl.entries)) if acl.has_more else None,
            total=acl.total,
        ),
    )


def _provenance(
    record: ServerRecord | ShareRecord | ShareAceRecord | NtfsResourceRecord | NtfsAceRecord,
) -> ProvenanceView:
    return ProvenanceView(
        source_key=record.source_key,
        first_observed_at=record.first_observed_at,
        first_observed_run_id=record.first_observed_run_id,
        last_observed_at=record.last_observed_at,
        last_observed_run_id=record.last_observed_run_id,
    )
