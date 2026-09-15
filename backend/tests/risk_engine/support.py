r"""Builders for risk-rule fact bundles.

Deliberately thin. A fixture that computed anything would be a second implementation of the
thing under test, and the one place that matters here is the access masks: the constants below
are the Windows composites, spelled once, and every test that means "Modify" uses the same
number the product does.

The cast is the demo estate's, so somebody who has read `app/demo/estate.py` recognizes the
names rather than learning a second set.
"""

from __future__ import annotations

from typing import Final

from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    AclLayer,
    GroupType,
    PrincipalKind,
    SharePermission,
)
from app.risk_engine import (
    AceFacts,
    AclProvenanceFacts,
    MembershipFacts,
    PrincipalFacts,
    ResourceFacts,
    RiskFacts,
    RiskScope,
    ShareFacts,
)

__all__ = [
    "ALICE",
    "AUTHENTICATED_USERS",
    "DOMAIN_SID",
    "DOMAIN_USERS",
    "EVERYONE",
    "FULL_CONTROL",
    "MODIFY",
    "READ_EXECUTE",
    "TRAVERSE",
    "WRITE",
    "bundle",
    "group",
    "membership",
    "ntfs_ace",
    "resource",
    "share",
    "share_ace",
    "sid",
    "unresolved",
    "user",
]

# The Windows access-control-editor composites, spelled once.
FULL_CONTROL: Final = 0x001F01FF
MODIFY: Final = 0x001301BF
READ_EXECUTE: Final = 0x001200A9
WRITE: Final = 0x00000116
TRAVERSE: Final = 0x00000020

EVERYONE: Final = "S-1-1-0"
AUTHENTICATED_USERS: Final = "S-1-5-11"
DOMAIN_SID: Final = "S-1-5-21-1004336348-1177238915-682003330"


def sid(rid: int) -> str:
    return f"{DOMAIN_SID}-{rid}"


DOMAIN_USERS: Final = sid(513)
ALICE: Final = sid(1104)


def ntfs_ace(
    container: str,
    trustee: str,
    mask: int,
    *,
    key: str | None = None,
    ace_type: AceType = AceType.ALLOW,
    order: int = 0,
    flags: AceFlag | int = AceFlag.NONE,
    source: AceSource = AceSource.EXPLICIT,
) -> AceFacts:
    return AceFacts(
        ace_key=key or f"{container}|{trustee}|{ace_type.value}|{mask:08x}",
        container_key=container,
        layer=AclLayer.NTFS,
        trustee_key=trustee,
        trustee_sid=trustee.rpartition("|")[2] if "|" in trustee else trustee,
        ace_type=ace_type,
        access_mask=mask,
        ace_flags=int(flags),
        source=source,
        order_index=order,
    )


def share_ace(
    container: str,
    trustee: str,
    *,
    permission: SharePermission = SharePermission.FULL,
    mask: int | None = None,
    ace_type: AceType = AceType.ALLOW,
    order: int = 0,
) -> AceFacts:
    return AceFacts(
        ace_key=f"{container}|{trustee}|{ace_type.value}|{permission.value}",
        container_key=container,
        layer=AclLayer.SMB_SHARE,
        trustee_key=trustee,
        trustee_sid=trustee.rpartition("|")[2] if "|" in trustee else trustee,
        ace_type=ace_type,
        access_mask=mask,
        permission=None if mask is not None else permission,
        order_index=order,
    )


def resource(
    key: str,
    *aces: AceFacts,
    path: str | None = None,
    share_key: str = "fs01|finance",
    depth: int | None = 1,
    **overrides: object,
) -> ResourceFacts:
    r"""A directory. ``depth`` defaults to 1 so the default resource is *not* a share root.

    The share-root case is the one the broken-inheritance rule excludes by default, so making
    it the default here would silently disarm that rule in every other test.
    """
    fields: dict[str, object] = {
        "server_key": "fs01",
        "depth_from_share_root": depth,
        "provenance": AclProvenanceFacts.OBSERVED,
    }
    fields.update(overrides)
    return ResourceFacts(
        resource_key=key,
        path=path or key.replace("fs01|", "\\\\FS01\\"),
        share_key=share_key,
        aces=tuple(aces),
        **fields,  # type: ignore[arg-type]
    )


def share(key: str, *aces: AceFacts, observed: bool = True, **overrides: object) -> ShareFacts:
    return ShareFacts(
        share_key=key,
        server_key="fs01",
        name=key.rpartition("|")[2],
        acl_observed=observed,
        aces=tuple(aces),
        **overrides,  # type: ignore[arg-type]
    )


def user(
    key: str, *, name: str | None = None, enabled: bool | None = True, **overrides: object
) -> PrincipalFacts:
    return PrincipalFacts(
        key=key,
        sid=key.rpartition("|")[2] if "|" in key else key,
        kind=PrincipalKind.USER,
        display_name=name,
        enabled=enabled,
        **overrides,  # type: ignore[arg-type]
    )


def group(
    key: str,
    *,
    name: str | None = None,
    group_type: GroupType = GroupType.SECURITY,
    **overrides: object,
) -> PrincipalFacts:
    return PrincipalFacts(
        key=key,
        sid=key.rpartition("|")[2] if "|" in key else key,
        kind=PrincipalKind.DOMAIN_GROUP,
        display_name=name,
        group_type=group_type,
        **overrides,  # type: ignore[arg-type]
    )


def unresolved(key: str, **overrides: object) -> PrincipalFacts:
    return PrincipalFacts(
        key=key,
        sid=key.rpartition("|")[2] if "|" in key else key,
        kind=PrincipalKind.UNRESOLVED,
        **overrides,  # type: ignore[arg-type]
    )


def membership(
    group_key: str, *members: str, enumerated: bool | None = True, truncated: bool = False
) -> MembershipFacts:
    return MembershipFacts(
        group_key=group_key,
        member_keys=tuple(members),
        enumerated=enumerated,
        truncated=truncated,
    )


def bundle(
    *,
    resources: tuple[ResourceFacts, ...] = (),
    shares: tuple[ShareFacts, ...] = (),
    principals: tuple[PrincipalFacts, ...] = (),
    memberships: tuple[MembershipFacts, ...] = (),
    scope: RiskScope | None = None,
) -> RiskFacts:
    return RiskFacts(
        resources=resources,
        shares=shares,
        principals={record.key: record for record in principals},
        memberships={record.group_key: record for record in memberships},
        scope=scope or RiskScope.everything(),
    )
