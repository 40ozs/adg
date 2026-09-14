"""Observation request models (contract v1).

Each model mirrors one schema under `docs/contracts/v1/` and converts to the corresponding
domain type. Validation here is deliberately stricter than JSON Schema can express —
cross-field rules such as "a group may not be a direct member of itself" or "an ACE's
source must agree with its INHERITED flag" live in model validators, with messages that
tell a collector author what to send instead.

Nothing in this module interprets permissions. A model accepts what a descriptor said and
hands it to the domain layer unchanged.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from app.contracts.v1 import keys
from app.contracts.v1.common import (
    MAX_ACCESS_MASK,
    MAX_ACE_FLAGS,
    AceSource,
    AceType,
    GroupScope,
    GroupType,
    MembershipEdgeKind,
    ObservationBase,
    PrincipalKind,
    SharePermission,
    ShareType,
    UnresolvedReason,
    canonical_sid,
    normalize_host,
)
from app.domain import (
    ACL_HASH_ALGORITHM,
    ACL_HASH_LENGTH,
    AceFlag,
    ComputerIdentity,
    DirectoryResource,
    DomainGroup,
    DomainValidationError,
    LocalGroup,
    MembershipEdge,
    NtfsAce,
    Principal,
    SecurityDescriptorFacts,
    Server,
    Sid,
    SmbShare,
    SmbShareAce,
    UncPath,
    UnresolvedPrincipal,
    User,
    WellKnownPrincipal,
    is_acl_hash,
    parse_local_path,
    parse_unc_path,
)

INHERITED_ACE_FLAG = int(AceFlag.INHERITED)


class PrincipalObservation(ObservationBase):
    """A principal a collector resolved — or could not resolve, which is also a fact."""

    kind: Literal["principal"] = "principal"

    sid: str
    principal_kind: PrincipalKind
    domain_sid: str | None = None
    host_key: str | None = Field(
        default=None,
        description="Required for local_group: BUILTIN SIDs repeat on every computer.",
    )

    display_name: str | None = Field(default=None, max_length=512)
    sam_account_name: str | None = Field(default=None, max_length=256)
    user_principal_name: str | None = Field(default=None, max_length=512)
    distinguished_name: str | None = Field(default=None, max_length=2048)

    group_scope: GroupScope | None = None
    group_type: GroupType | None = None

    enabled: bool | None = None
    is_deleted: bool = False

    unresolved_reason: UnresolvedReason | None = None
    last_known_name: str | None = Field(default=None, max_length=512)

    @field_validator("sid", "domain_sid")
    @classmethod
    def _canonical_sids(cls, value: str | None) -> str | None:
        return None if value is None else canonical_sid(value)

    @field_validator("host_key")
    @classmethod
    def _validate_host(cls, value: str | None) -> str | None:
        return None if value is None else normalize_host(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.principal_kind is PrincipalKind.LOCAL_GROUP and not self.host_key:
            raise ValueError(
                "A local_group observation must carry host_key: S-1-5-32-544 is identical "
                "on every Windows computer, so host plus SID identifies the group."
            )
        if self.principal_kind is PrincipalKind.UNRESOLVED and self.display_name is not None:
            raise ValueError(
                "An unresolved principal must not carry display_name. Report a name seen "
                "in an earlier scan as last_known_name so it cannot be mistaken for a "
                "current resolution."
            )
        self.check_source_key()
        return self

    def derive_source_key(self) -> str:
        return keys.principal_key(Sid(self.sid), self.principal_kind, self.host_key)

    def to_domain(self) -> Principal:
        """Build the matching domain principal.

        Raises:
            DomainValidationError: if the payload is internally inconsistent in a way the
                domain layer rejects (for example a domain SID that contradicts the SID).
        """
        sid = Sid(self.sid)
        domain_sid = Sid(self.domain_sid) if self.domain_sid else None

        if self.principal_kind in (PrincipalKind.USER, PrincipalKind.MANAGED_SERVICE_ACCOUNT):
            return User(
                sid=sid,
                kind=self.principal_kind,
                display_name=self.display_name,
                sam_account_name=self.sam_account_name,
                domain_sid=domain_sid,
                user_principal_name=self.user_principal_name,
                distinguished_name=self.distinguished_name,
                enabled=self.enabled,
                is_deleted=self.is_deleted,
            )
        if self.principal_kind is PrincipalKind.DOMAIN_GROUP:
            return DomainGroup(
                sid=sid,
                display_name=self.display_name,
                sam_account_name=self.sam_account_name,
                domain_sid=domain_sid,
                scope=self.group_scope or GroupScope.UNKNOWN,
                group_type=self.group_type or GroupType.UNKNOWN,
                distinguished_name=self.distinguished_name,
                is_deleted=self.is_deleted,
            )
        if self.principal_kind is PrincipalKind.LOCAL_GROUP:
            return LocalGroup(
                sid=sid,
                display_name=self.display_name,
                sam_account_name=self.sam_account_name,
                domain_sid=domain_sid,
                host_key=self.host_key or "",
            )
        if self.principal_kind is PrincipalKind.COMPUTER:
            return ComputerIdentity(
                sid=sid,
                display_name=self.display_name,
                sam_account_name=self.sam_account_name,
                domain_sid=domain_sid,
                distinguished_name=self.distinguished_name,
                is_deleted=self.is_deleted,
            )
        if self.principal_kind is PrincipalKind.WELL_KNOWN:
            return WellKnownPrincipal(
                sid=sid,
                display_name=self.display_name,
                sam_account_name=self.sam_account_name,
                domain_sid=domain_sid,
            )
        if self.principal_kind is PrincipalKind.UNRESOLVED:
            return UnresolvedPrincipal(
                sid=sid,
                sam_account_name=self.sam_account_name,
                domain_sid=domain_sid,
                reason=self.unresolved_reason or UnresolvedReason.UNKNOWN,
                last_known_name=self.last_known_name,
            )
        # foreign_security_principal: a SID from elsewhere, described no further.
        return Principal(
            sid=sid,
            kind=self.principal_kind,
            display_name=self.display_name,
            sam_account_name=self.sam_account_name,
            domain_sid=domain_sid,
        )


class MembershipObservation(ObservationBase):
    """One directed membership edge."""

    kind: Literal["membership_edge"] = "membership_edge"

    group_sid: str
    member_sid: str
    edge_kind: MembershipEdgeKind
    host_key: str | None = None
    member_kind: PrincipalKind | None = None
    is_foreign_security_principal: bool = False

    @field_validator("group_sid", "member_sid")
    @classmethod
    def _canonical_sids(cls, value: str) -> str:
        return canonical_sid(value)

    @field_validator("host_key")
    @classmethod
    def _validate_host(cls, value: str | None) -> str | None:
        return None if value is None else normalize_host(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        # Reuse the domain invariants rather than restating them: self-edges, host scoping.
        try:
            self.to_domain()
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        self.check_source_key()
        return self

    def derive_source_key(self) -> str:
        return keys.membership_key(
            Sid(self.group_sid), Sid(self.member_sid), self.edge_kind, self.host_key
        )

    def to_domain(self) -> MembershipEdge:
        return MembershipEdge(
            group_sid=Sid(self.group_sid),
            member_sid=Sid(self.member_sid),
            kind=self.edge_kind,
            host_key=self.host_key,
            member_kind=self.member_kind,
            is_foreign_security_principal=self.is_foreign_security_principal,
        )


class ServerObservation(ObservationBase):
    """A computer that hosts shares."""

    kind: Literal["server"] = "server"

    name: str = Field(min_length=1, max_length=255)
    dns_host_name: str | None = Field(default=None, max_length=255)
    netbios_name: str | None = Field(default=None, max_length=63)
    computer_sid: str | None = None
    domain_sid: str | None = None
    is_domain_member: bool | None = None
    operating_system: str | None = Field(default=None, max_length=256)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return normalize_host(value)

    @field_validator("computer_sid", "domain_sid")
    @classmethod
    def _canonical_sids(cls, value: str | None) -> str | None:
        return None if value is None else canonical_sid(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        self.check_source_key()
        return self

    def derive_source_key(self) -> str:
        return keys.server_key(self.name)

    def to_domain(self) -> Server:
        return Server(
            name=self.name,
            dns_host_name=self.dns_host_name,
            netbios_name=self.netbios_name,
            computer_sid=Sid(self.computer_sid) if self.computer_sid else None,
            domain_sid=Sid(self.domain_sid) if self.domain_sid else None,
            is_domain_member=self.is_domain_member,
        )


class SmbShareObservation(ObservationBase):
    """A share exposed by a server."""

    kind: Literal["smb_share"] = "smb_share"

    server_name: str = Field(min_length=1, max_length=255)
    share_name: str = Field(min_length=1, max_length=80)
    local_path: str | None = None
    share_type: ShareType = ShareType.DISK
    description: str | None = Field(default=None, max_length=1024)
    concurrent_user_limit: int | None = Field(default=None, ge=0)
    caching_mode: str | None = Field(default=None, max_length=64)
    is_special: bool | None = Field(
        default=None,
        description=(
            "The SMB server's Special flag: an administrative or system share. Added in "
            "contract 1.1. None means the source did not say, which is not false."
        ),
    )

    @field_validator("server_name", "share_name")
    @classmethod
    def _validate_names(cls, value: str) -> str:
        return normalize_host(value)

    @field_validator("local_path")
    @classmethod
    def _validate_local_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return parse_local_path(value).value
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        self.check_source_key()
        return self

    def derive_source_key(self) -> str:
        return keys.share_key(self.server_name, self.share_name)

    def to_domain(self) -> SmbShare:
        return SmbShare(
            server_key=self.server_name.casefold(),
            name=self.share_name,
            local_path=parse_local_path(self.local_path) if self.local_path else None,
            share_type=self.share_type,
            description=self.description,
            concurrent_user_limit=self.concurrent_user_limit,
        )


class SmbAceObservation(ObservationBase):
    """One entry of a share-level ACL."""

    kind: Literal["smb_ace"] = "smb_ace"

    server_name: str = Field(min_length=1, max_length=255)
    share_name: str = Field(min_length=1, max_length=80)
    trustee_sid: str
    ace_type: AceType
    access_mask: int | None = Field(default=None, ge=0, le=MAX_ACCESS_MASK)
    permission: SharePermission | None = None
    order_index: int | None = Field(default=None, ge=0)

    @field_validator("server_name", "share_name")
    @classmethod
    def _validate_names(cls, value: str) -> str:
        return normalize_host(value)

    @field_validator("trustee_sid")
    @classmethod
    def _canonical_sid(cls, value: str) -> str:
        return canonical_sid(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if (self.access_mask is None) == (self.permission is None):
            raise ValueError(
                "A share ACE must carry exactly one of access_mask or permission: "
                "whichever form the collecting API reported. Get-SmbShareAccess reports a "
                "permission level; the security-descriptor APIs report a mask. Reporting "
                "both would fabricate a value the source did not provide."
            )
        self.check_source_key()
        return self

    def derive_source_key(self) -> str:
        return keys.smb_ace_key(
            self.server_name,
            self.share_name,
            Sid(self.trustee_sid),
            self.ace_type.value,
            self.access_mask,
            self.permission.value if self.permission else None,
        )

    @property
    def share_identity_key(self) -> str:
        return f"{self.server_name.casefold()}|{self.share_name.casefold()}"

    def to_domain(self) -> SmbShareAce:
        return SmbShareAce(
            trustee_sid=Sid(self.trustee_sid),
            ace_type=self.ace_type,
            access_mask=self.access_mask,
            permission=self.permission,
            order_index=self.order_index,
        )


class NtfsResourceObservation(ObservationBase):
    """A directory plus its descriptor-level facts."""

    kind: Literal["ntfs_resource"] = "ntfs_resource"

    path: str
    local_path: str | None = None
    server_name: str | None = Field(default=None, max_length=255)
    share_name: str | None = Field(default=None, max_length=80)

    owner_sid: str | None = None
    group_sid: str | None = None
    dacl_present: bool
    dacl_protected: bool = False
    ace_count: int = Field(ge=0)
    inheritance_enabled: bool = True
    is_acl_boundary: bool = False
    depth_from_share_root: int | None = Field(default=None, ge=0)
    acl_hash: str | None = Field(
        default=None,
        description=(
            "Digest of the normalized DACL as the collector read it. Added in contract "
            "1.2. None means the collector did not compute one, which is not the same as "
            "an empty ACL."
        ),
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        try:
            return parse_unc_path(value).value
        except DomainValidationError as exc:
            raise ValueError(
                f"{exc} A resource is identified by its UNC path; a local path alone is "
                "ambiguous because the server is unknown."
            ) from exc

    @field_validator("local_path")
    @classmethod
    def _validate_local_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return parse_local_path(value).value
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("owner_sid", "group_sid")
    @classmethod
    def _canonical_sids(cls, value: str | None) -> str | None:
        return None if value is None else canonical_sid(value)

    @field_validator("acl_hash")
    @classmethod
    def _validate_acl_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        folded = value.strip().lower()
        if not is_acl_hash(folded):
            raise ValueError(
                f"acl_hash must be {ACL_HASH_LENGTH} hexadecimal characters of "
                f"{ACL_HASH_ALGORITHM} over the normalized DACL; received {value!r}. See "
                "docs/architecture/ntfs-acl-normalization.md for the normal form."
            )
        return folded

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if not self.dacl_present and self.ace_count:
            raise ValueError(
                "A NULL DACL (dacl_present=false) grants everyone full access and carries "
                "no ACEs. Report ace_count=0, and do not confuse it with a present but "
                "empty DACL, which grants nobody access."
            )
        if not self.inheritance_enabled and not self.is_acl_boundary:
            raise ValueError(
                "A resource that blocks inheritance is by definition an ACL boundary; "
                "recording otherwise would hide where permissions change."
            )
        # server_name and share_name are conveniences; the path is the identity. Letting
        # them disagree would attach a directory to a share it is not inside, and the link
        # between the NTFS layer and the share layer is exactly what those names feed.
        path = parse_unc_path(self.path)
        if self.server_name and self.server_name.casefold() != path.server.casefold():
            raise ValueError(
                f"server_name {self.server_name!r} contradicts the path {path.value!r}, "
                f"which names {path.server!r}. The path identifies the resource; omit "
                "server_name rather than restating it differently."
            )
        if self.share_name and self.share_name.casefold() != path.share.casefold():
            raise ValueError(
                f"share_name {self.share_name!r} contradicts the path {path.value!r}, "
                f"which names {path.share!r}. A directory belongs to the share in its own "
                "path; attaching it to another would report one share's NTFS root under "
                "another share's name."
            )
        self.check_source_key()
        return self

    def derive_source_key(self) -> str:
        return keys.ntfs_resource_key(self.path)

    @property
    def unc_path(self) -> UncPath:
        return parse_unc_path(self.path)

    def to_domain(self) -> DirectoryResource:
        path = parse_unc_path(self.path)
        share_key = (
            f"{self.server_name.casefold()}|{self.share_name.casefold()}"
            if self.server_name and self.share_name
            else f"{path.server.casefold()}|{path.share.casefold()}"
        )
        return DirectoryResource(
            path=path,
            local_path=parse_local_path(self.local_path) if self.local_path else None,
            share_key=share_key,
            is_acl_boundary=self.is_acl_boundary,
            inheritance_enabled=self.inheritance_enabled,
            depth_from_share_root=self.depth_from_share_root,
        )

    def to_descriptor_facts(self) -> SecurityDescriptorFacts:
        return SecurityDescriptorFacts(
            owner_sid=Sid(self.owner_sid) if self.owner_sid else None,
            group_sid=Sid(self.group_sid) if self.group_sid else None,
            dacl_present=self.dacl_present,
            dacl_protected=self.dacl_protected,
            ace_count=self.ace_count,
        )


class NtfsAceObservation(ObservationBase):
    """One entry of a file-system DACL, reported exactly as read."""

    kind: Literal["ntfs_ace"] = "ntfs_ace"

    path: str
    trustee_sid: str
    ace_type: AceType
    access_mask: int = Field(ge=0, le=MAX_ACCESS_MASK)
    ace_flags: int = Field(ge=0, le=MAX_ACE_FLAGS)
    source: AceSource
    inherited_from: str | None = None
    order_index: int | None = Field(default=None, ge=0)

    @field_validator("path", "inherited_from")
    @classmethod
    def _validate_paths(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return parse_unc_path(value).value
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("trustee_sid")
    @classmethod
    def _canonical_sid(cls, value: str) -> str:
        return canonical_sid(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        flag_says_inherited = bool(self.ace_flags & INHERITED_ACE_FLAG)
        source_says_inherited = self.source is AceSource.INHERITED
        if flag_says_inherited != source_says_inherited:
            raise ValueError(
                f"source={self.source.value!r} contradicts ace_flags=0x{self.ace_flags:02x}: "
                "the INHERITED_ACE bit (0x10) must be set exactly when the ACE is "
                "inherited. Report what the descriptor says."
            )
        if self.inherited_from is not None and self.source is AceSource.EXPLICIT:
            raise ValueError("An explicit ACE cannot record an inheritance origin.")
        self.check_source_key()
        return self

    def derive_source_key(self) -> str:
        return keys.ntfs_ace_key(
            self.path, Sid(self.trustee_sid), self.ace_type.value, self.access_mask, self.ace_flags
        )

    @property
    def resource_key(self) -> str:
        return keys.ntfs_resource_key(self.path)

    @property
    def unc_path(self) -> UncPath:
        """The resource this entry was read from. Its server is what host-scopes a BUILTIN
        trustee: the descriptor lives on that machine."""
        return parse_unc_path(self.path)

    def to_domain(self) -> NtfsAce:
        return NtfsAce(
            trustee_sid=Sid(self.trustee_sid),
            ace_type=self.ace_type,
            access_mask=self.access_mask,
            flags=AceFlag(self.ace_flags),
            source=self.source,
            inherited_from=self.inherited_from,
            order_index=self.order_index,
        )


AnyObservation = (
    PrincipalObservation
    | MembershipObservation
    | ServerObservation
    | SmbShareObservation
    | SmbAceObservation
    | NtfsResourceObservation
    | NtfsAceObservation
)

OBSERVATION_MODELS: dict[str, type[ObservationBase]] = {
    "principal": PrincipalObservation,
    "membership_edge": MembershipObservation,
    "server": ServerObservation,
    "smb_share": SmbShareObservation,
    "smb_ace": SmbAceObservation,
    "ntfs_resource": NtfsResourceObservation,
    "ntfs_ace": NtfsAceObservation,
}
