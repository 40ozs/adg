"""Effective-access semantics.

The backend owns authorization math: SMB and NTFS rights algebra, ACE precedence, group
expansion, and access explanation. Collectors and the frontend must never reimplement it.

Phase 4A supplies the bottom layer — :mod:`app.access_engine.rights`, a pure algebra over
access masks. It answers "what does this mask permit, and what should we call it?" without
resolving principals or walking a directory tree. Later Phase 4 prompts build the resolver
on top of it; everything they conclude must be expressed in these types, so that no other
module ever needs to interpret an access mask itself.
"""

from __future__ import annotations

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

__all__ = [
    "CATEGORY_REQUIRED_MASKS",
    "ESCALATION_RIGHTS",
    "FILE_ALL_ACCESS",
    "FILE_GENERIC_EXECUTE",
    "FILE_GENERIC_READ",
    "FILE_GENERIC_WRITE",
    "FILE_SYSTEM_GENERIC_MAPPING",
    "GENERIC_RIGHT_BITS",
    "MAX_ACCESS_MASK",
    "SHARE_LEVEL_MASKS",
    "SYNCHRONIZE_BIT",
    "AccessPath",
    "EffectiveRights",
    "ExtendedRight",
    "NormalizedRights",
    "RightsCategory",
    "RightsError",
    "RightsLayer",
    "RightsLayerError",
    "RightsMask",
    "RightsSummary",
    "apply_deny",
    "category_display_name",
    "classify_share_mask",
    "effective_rights",
    "intersect_all",
    "normalize_mask",
    "normalize_ntfs_mask",
    "normalize_share_ace",
    "normalize_share_mask",
    "normalize_share_permission",
    "resolve_canonical",
    "summarize",
    "union_all",
]
