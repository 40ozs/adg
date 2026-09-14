"""Effective-access semantics.

The backend owns authorization math: SMB and NTFS rights algebra, ACE precedence, group
expansion, and access explanation. Collectors and the frontend must never reimplement it.

Phase 4A supplies the bottom layer — :mod:`app.access_engine.rights`, a pure algebra over
access masks. It answers "what does this mask permit, and what should we call it?" without
resolving principals or walking a directory tree.

Phase 4B builds the resolver on top of it, in four modules that stack in one direction:

* :mod:`app.access_engine.conditions` — the closed vocabulary of things the resolver had
  to assume or could not settle. Every answer carries its own qualifications.
* :mod:`app.access_engine.subjects` — the access token: which SIDs an ACL is evaluated
  against, which of them ADG observed, and which it assumed.
* :mod:`app.access_engine.evaluation` — the Windows access check over one DACL, in the
  order the descriptor stored it, for one token.
* :mod:`app.access_engine.resolver` — the two layers crossed for one declared access path.

All of it is framework-free: no FastAPI, no SQLAlchemy, no I/O. The service layer supplies
observations and the API renders results; everything either of them concludes must be
expressed in these types, so that no other module ever needs to interpret an access mask
or match a trustee for itself.
"""

from __future__ import annotations

from app.access_engine.conditions import (
    CONDITION_DESCRIPTIONS,
    OVERSTATING_CONDITIONS,
    UNDERSTATING_CONDITIONS,
    AccessCondition,
    AccessFinding,
    describe_condition,
)
from app.access_engine.evaluation import (
    MAX_ACL_ENTRIES,
    OWNER_IMPLICIT_RIGHTS,
    AclEntry,
    AclEvaluation,
    AppliedAce,
    DaclFacts,
    OrderViolation,
    OrderViolationKind,
    canonical_order_violations,
    evaluate_acl,
    ntfs_entry,
    share_entry,
)
from app.access_engine.resolver import (
    AccessCertainty,
    AclProvenance,
    EffectiveAccess,
    LimitingLayer,
    ResourceDacl,
    ShareDacl,
    certainty_of,
    resolve_access,
    unobserved_trustee_findings,
)
from app.access_engine.rights import (
    CATEGORY_REQUIRED_MASKS,
    ESCALATION_RIGHTS,
    FILE_ALL_ACCESS,
    FILE_GENERIC_EXECUTE,
    FILE_GENERIC_READ,
    FILE_GENERIC_WRITE,
    FILE_SYSTEM_GENERIC_MAPPING,
    GENERIC_RIGHT_BITS,
    MAX_ACCESS_MASK,
    SHARE_LEVEL_MASKS,
    SYNCHRONIZE_BIT,
    AccessPath,
    EffectiveRights,
    ExtendedRight,
    NormalizedRights,
    RightsCategory,
    RightsError,
    RightsLayer,
    RightsLayerError,
    RightsMask,
    RightsSummary,
    apply_deny,
    category_display_name,
    classify_share_mask,
    effective_rights,
    intersect_all,
    normalize_mask,
    normalize_ntfs_mask,
    normalize_share_ace,
    normalize_share_mask,
    normalize_share_permission,
    resolve_canonical,
    summarize,
    union_all,
)
from app.access_engine.subjects import (
    ANONYMOUS_LOGON_SID,
    AUTHENTICATED_USERS_SID,
    CREATOR_GROUP_SID,
    CREATOR_OWNER_SID,
    EVERYONE_SID,
    INTERACTIVE_SID,
    LOGON_SESSION_SIDS,
    NETWORK_SID,
    OWNER_RIGHTS_SID,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    TokenAssumption,
    TokenSid,
    build_token,
    default_assumption,
)

__all__ = [
    "ANONYMOUS_LOGON_SID",
    "AUTHENTICATED_USERS_SID",
    "CATEGORY_REQUIRED_MASKS",
    "CONDITION_DESCRIPTIONS",
    "CREATOR_GROUP_SID",
    "CREATOR_OWNER_SID",
    "ESCALATION_RIGHTS",
    "EVERYONE_SID",
    "FILE_ALL_ACCESS",
    "FILE_GENERIC_EXECUTE",
    "FILE_GENERIC_READ",
    "FILE_GENERIC_WRITE",
    "FILE_SYSTEM_GENERIC_MAPPING",
    "GENERIC_RIGHT_BITS",
    "INTERACTIVE_SID",
    "LOGON_SESSION_SIDS",
    "MAX_ACCESS_MASK",
    "MAX_ACL_ENTRIES",
    "NETWORK_SID",
    "OVERSTATING_CONDITIONS",
    "OWNER_IMPLICIT_RIGHTS",
    "OWNER_RIGHTS_SID",
    "SHARE_LEVEL_MASKS",
    "SYNCHRONIZE_BIT",
    "UNDERSTATING_CONDITIONS",
    "AccessCertainty",
    "AccessCondition",
    "AccessFinding",
    "AccessPath",
    "AclEntry",
    "AclEvaluation",
    "AclProvenance",
    "AppliedAce",
    "DaclFacts",
    "EffectiveAccess",
    "EffectiveRights",
    "ExtendedRight",
    "LimitingLayer",
    "NormalizedRights",
    "OrderViolation",
    "OrderViolationKind",
    "ResourceDacl",
    "RightsCategory",
    "RightsError",
    "RightsLayer",
    "RightsLayerError",
    "RightsMask",
    "RightsSummary",
    "ShareDacl",
    "SidOrigin",
    "SubjectFacts",
    "SubjectToken",
    "TokenAssumption",
    "TokenSid",
    "apply_deny",
    "build_token",
    "canonical_order_violations",
    "category_display_name",
    "certainty_of",
    "classify_share_mask",
    "default_assumption",
    "describe_condition",
    "effective_rights",
    "evaluate_acl",
    "intersect_all",
    "normalize_mask",
    "normalize_ntfs_mask",
    "normalize_share_ace",
    "normalize_share_mask",
    "normalize_share_permission",
    "ntfs_entry",
    "resolve_access",
    "resolve_canonical",
    "share_entry",
    "summarize",
    "union_all",
    "unobserved_trustee_findings",
]
