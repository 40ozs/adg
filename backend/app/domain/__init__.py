"""ADG permission domain model.

Canonical, framework-free types for identity, resources, raw permission facts, membership,
and provenance. They deliberately contain **no** permission-resolution algorithm: the
effective-access engine arrives in Phase 4 and consumes these types.

See `docs/architecture/permission-domain-model.md` for the semantics, and ADR-0001 through
ADR-0004 for the decisions these types encode.
"""

from __future__ import annotations

from app.domain.access import (
    FULL_CONTROL_MASK,
    GENERIC_RIGHT_BITS,
    SHARE_PERMISSION_MASKS,
    AceFlag,
    AceSource,
    AceType,
    AclLayer,
    NtfsAce,
    NtfsRight,
    SecurityDescriptorFacts,
    SharePermission,
    SmbShareAce,
)
from app.domain.errors import DomainError, DomainValidationError
from app.domain.identity import (
    MAX_SUB_AUTHORITIES,
    WELL_KNOWN_RID_NAMES,
    WELL_KNOWN_SID_NAMES,
    ComputerIdentity,
    DomainGroup,
    DomainIdentifier,
    DomainKind,
    GroupScope,
    GroupType,
    LocalGroup,
    Principal,
    PrincipalKind,
    Sid,
    UnresolvedPrincipal,
    UnresolvedReason,
    User,
    WellKnownPrincipal,
)
from app.domain.membership import MembershipEdge, MembershipEdgeKind
from app.domain.observation import (
    CollectorKind,
    Observation,
    ObservationSource,
    ScanRun,
    ScanStatus,
)
from app.domain.paths import (
    LocalPath,
    UncPath,
    parse_local_path,
    parse_unc_path,
    parse_windows_path,
)
from app.domain.resources import DirectoryResource, Server, ShareType, SmbShare

__all__ = [
    "FULL_CONTROL_MASK",
    "GENERIC_RIGHT_BITS",
    "MAX_SUB_AUTHORITIES",
    "SHARE_PERMISSION_MASKS",
    "WELL_KNOWN_RID_NAMES",
    "WELL_KNOWN_SID_NAMES",
    "AceFlag",
    "AceSource",
    "AceType",
    "AclLayer",
    "CollectorKind",
    "ComputerIdentity",
    "DirectoryResource",
    "DomainError",
    "DomainGroup",
    "DomainIdentifier",
    "DomainKind",
    "DomainValidationError",
    "GroupScope",
    "GroupType",
    "LocalGroup",
    "LocalPath",
    "MembershipEdge",
    "MembershipEdgeKind",
    "NtfsAce",
    "NtfsRight",
    "Observation",
    "ObservationSource",
    "Principal",
    "PrincipalKind",
    "ScanRun",
    "ScanStatus",
    "SecurityDescriptorFacts",
    "Server",
    "SharePermission",
    "ShareType",
    "Sid",
    "SmbShare",
    "SmbShareAce",
    "UncPath",
    "UnresolvedPrincipal",
    "UnresolvedReason",
    "User",
    "WellKnownPrincipal",
    "parse_local_path",
    "parse_unc_path",
    "parse_windows_path",
]
