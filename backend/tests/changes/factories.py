"""Versions and states built by hand, so the pure layers can be tested without a database.

Everything here produces the same shapes the writer stores: a ``state`` mapping is the
current-state row's descriptive columns with provenance stripped, and a
:class:`app.history.model.ObjectVersion` carries the interval it was observed over. The one
thing these deliberately do *not* do is bypass ``state_digest``: a version's hash is computed
from its own state, exactly as :meth:`ObjectVersion.validate` requires, so a factory cannot
build a version the writer could not have written.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from app.contracts.v1.common import ObservationKind
from app.history.model import CloseReason, ObjectVersion, VersionOrigin, state_digest

MONDAY = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
WEDNESDAY = dt.datetime(2026, 3, 4, 9, 0, tzinfo=dt.UTC)
FRIDAY = dt.datetime(2026, 3, 6, 9, 0, tzinfo=dt.UTC)
FRIDAY_END = FRIDAY + dt.timedelta(minutes=5)

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN}-1104"
FINANCE_RW = f"{DOMAIN}-1101"
DOMAIN_ADMINS = f"{DOMAIN}-512"
EVERYONE = "S-1-1-0"

FINANCE = "\\\\fs01\\finance"
FINANCE_SHARE = "fs01|finance"

FULL_CONTROL = 0x1F01FF
MODIFY = 0x1301BF
READ_EXECUTE = 0x1200A9

RUN = uuid.UUID("11111111-2222-3333-4444-555555555555")
LATER_RUN = uuid.UUID("99999999-8888-7777-6666-555555555555")


def version(
    kind: ObservationKind,
    key: str,
    state: dict[str, Any] | None,
    *,
    valid_from: dt.datetime = MONDAY,
    last_seen_at: dt.datetime | None = None,
    valid_to: dt.datetime | None = None,
    origin: VersionOrigin = VersionOrigin.OBSERVED,
    close_reason: CloseReason | None = None,
    container_key: str | None = None,
    related_key: str | None = None,
    version_id: int = 1,
) -> ObjectVersion:
    """One version, validated exactly as the writer validates one before inserting it."""
    present = state is not None
    if valid_to is not None and close_reason is None:
        close_reason = CloseReason.SUPERSEDED if present else CloseReason.ABSENT
    return ObjectVersion(
        kind=kind,
        key=key,
        is_present=present,
        valid_from=valid_from,
        last_seen_at=last_seen_at or valid_from,
        valid_to=valid_to,
        state=state,
        state_hash=state_digest(state) if state is not None else None,
        origin=origin,
        close_reason=close_reason,
        opened_by_run_id=RUN,
        last_seen_run_id=RUN,
        closed_by_run_id=LATER_RUN if valid_to is not None else None,
        container_key=container_key,
        related_key=related_key,
        version_id=version_id,
    ).validate()


def tombstone(
    kind: ObservationKind,
    key: str,
    *,
    valid_from: dt.datetime = FRIDAY_END,
    container_key: str | None = None,
    related_key: str | None = None,
    version_id: int = 2,
) -> ObjectVersion:
    """A measured absence: a reconciled scan looked and did not find the object."""
    return version(
        kind,
        key,
        None,
        valid_from=valid_from,
        container_key=container_key,
        related_key=related_key,
        version_id=version_id,
    )


# ------------------------------------------------------------------------- states


def ntfs_ace_state(
    *,
    trustee: str = FINANCE_RW,
    access_mask: int = FULL_CONTROL,
    ace_type: str = "allow",
    ace_flags: int = 0x03,
    order_index: int | None = 0,
    source: str = "explicit",
    resource: str = FINANCE,
    source_key: str = "ntfs_ace|1",
) -> dict[str, Any]:
    return {
        "ace_key": f"{resource}|{trustee}|{ace_type}|0x{access_mask:08x}|0x{ace_flags:02x}",
        "resource_key": resource,
        "trustee_sid": trustee,
        "trustee_key": trustee,
        "ace_type": ace_type,
        "access_mask": access_mask,
        "ace_flags": ace_flags,
        "source": source,
        "inherited_from": None,
        "order_index": order_index,
        "source_key": source_key,
    }


def ntfs_ace(
    *,
    trustee: str = FINANCE_RW,
    access_mask: int = FULL_CONTROL,
    ace_type: str = "allow",
    ace_flags: int = 0x03,
    order_index: int | None = 0,
    source: str = "explicit",
    resource: str = FINANCE,
    source_key: str = "ntfs_ace|1",
    **version_kwargs: Any,
) -> ObjectVersion:
    state = ntfs_ace_state(
        trustee=trustee,
        access_mask=access_mask,
        ace_type=ace_type,
        ace_flags=ace_flags,
        order_index=order_index,
        source=source,
        resource=resource,
        source_key=source_key,
    )
    return version(
        ObservationKind.NTFS_ACE,
        state["ace_key"],
        state,
        container_key=resource,
        related_key=trustee,
        **version_kwargs,
    )


def smb_ace_state(
    *,
    trustee: str = FINANCE_RW,
    right_token: str = "full",
    ace_type: str = "allow",
    order_index: int | None = 0,
    share: str = FINANCE_SHARE,
    access_mask: int | None = None,
    source_key: str = "smb_ace|1",
) -> dict[str, Any]:
    return {
        "ace_key": f"{share}|{trustee}|{ace_type}|{right_token}",
        "share_key": share,
        "trustee_sid": trustee,
        "trustee_key": trustee,
        "ace_type": ace_type,
        "access_mask": access_mask,
        "permission": None if access_mask is not None else right_token,
        "right_token": right_token,
        "order_index": order_index,
        "source_key": source_key,
    }


def smb_ace(**kwargs: Any) -> ObjectVersion:
    version_kwargs = {
        name: kwargs.pop(name)
        for name in list(kwargs)
        if name
        in {
            "valid_from",
            "last_seen_at",
            "valid_to",
            "origin",
            "close_reason",
            "version_id",
        }
    }
    state = smb_ace_state(**kwargs)
    return version(
        ObservationKind.SMB_ACE,
        state["ace_key"],
        state,
        container_key=state["share_key"],
        related_key=state["trustee_key"],
        **version_kwargs,
    )


def principal_state(
    *,
    sid: str = ALICE,
    kind: str = "user",
    enabled: bool | None = True,
    display_name: str | None = "Alice",
    group_type: str | None = None,
    group_scope: str | None = None,
    is_deleted: bool = False,
    source_key: str = "principal|1",
) -> dict[str, Any]:
    return {
        "principal_key": sid,
        "sid": sid,
        "principal_kind": kind,
        "host_key": None,
        "domain_sid": DOMAIN,
        "display_name": display_name,
        "sam_account_name": None,
        "user_principal_name": None,
        "distinguished_name": None,
        "group_scope": group_scope,
        "group_type": group_type,
        "enabled": enabled,
        "is_deleted": is_deleted,
        "unresolved_reason": None,
        "last_known_name": None,
        "source_key": source_key,
    }


def principal(**kwargs: Any) -> ObjectVersion:
    version_kwargs = {
        name: kwargs.pop(name)
        for name in list(kwargs)
        if name in {"valid_from", "last_seen_at", "valid_to", "origin", "version_id"}
    }
    state = principal_state(**kwargs)
    return version(ObservationKind.PRINCIPAL, state["principal_key"], state, **version_kwargs)


def edge_state(
    *,
    group: str = FINANCE_RW,
    member: str = ALICE,
    edge_kind: str = "directory_group_member",
    member_kind: str | None = "user",
    source_key: str = "edge|1",
) -> dict[str, Any]:
    return {
        "edge_key": f"{group}->{member}|{edge_kind}",
        "group_key": group,
        "member_key": member,
        "group_sid": group,
        "member_sid": member,
        "edge_kind": edge_kind,
        "host_key": None,
        "member_kind": member_kind,
        "is_foreign_security_principal": False,
        "source_key": source_key,
    }


def edge(**kwargs: Any) -> ObjectVersion:
    version_kwargs = {
        name: kwargs.pop(name)
        for name in list(kwargs)
        if name in {"valid_from", "last_seen_at", "valid_to", "origin", "version_id"}
    }
    state = edge_state(**kwargs)
    return version(
        ObservationKind.MEMBERSHIP_EDGE,
        state["edge_key"],
        state,
        container_key=state["group_key"],
        related_key=state["member_key"],
        **version_kwargs,
    )


def resource_state(
    *,
    path: str = FINANCE,
    dacl_present: bool = True,
    dacl_protected: bool = True,
    owner_sid: str | None = DOMAIN_ADMINS,
    acl_hash: str | None = "a" * 64,
    ace_count: int = 1,
    source_key: str = "resource|1",
) -> dict[str, Any]:
    return {
        "resource_key": path,
        "path": path,
        "server_key": "fs01",
        "share_key": FINANCE_SHARE,
        "local_path": "D:\\Shares\\Finance",
        "owner_sid": owner_sid,
        "group_sid": None,
        "dacl_present": dacl_present,
        "dacl_protected": dacl_protected,
        "inheritance_enabled": not dacl_protected,
        "is_acl_boundary": True,
        "ace_count": ace_count,
        "depth_from_share_root": 0,
        "resource_kind": "directory",
        "boundary_reason": "scan_root",
        "acl_hash": acl_hash,
        "parent_acl_hash": None,
        "source_key": source_key,
    }


def resource(**kwargs: Any) -> ObjectVersion:
    version_kwargs = {
        name: kwargs.pop(name)
        for name in list(kwargs)
        if name in {"valid_from", "last_seen_at", "valid_to", "origin", "version_id"}
    }
    state = resource_state(**kwargs)
    return version(
        ObservationKind.NTFS_RESOURCE,
        state["resource_key"],
        state,
        container_key=state["share_key"],
        related_key=state["server_key"],
        **version_kwargs,
    )


def share_state(
    *,
    name: str = "finance",
    local_path: str = "D:\\Shares\\Finance",
    share_type: str = "disk",
    description: str | None = None,
    source_key: str = "share|1",
) -> dict[str, Any]:
    return {
        "share_key": f"fs01|{name}",
        "server_key": "fs01",
        "name": name,
        "local_path": local_path,
        "share_type": share_type,
        "description": description,
        "concurrent_user_limit": None,
        "caching_mode": None,
        "is_special": False,
        "source_key": source_key,
    }


def share(**kwargs: Any) -> ObjectVersion:
    version_kwargs = {
        name: kwargs.pop(name)
        for name in list(kwargs)
        if name in {"valid_from", "last_seen_at", "valid_to", "origin", "version_id"}
    }
    state = share_state(**kwargs)
    return version(
        ObservationKind.SMB_SHARE,
        state["share_key"],
        state,
        container_key=state["server_key"],
        **version_kwargs,
    )


def server_state(
    *,
    name: str = "fs01",
    computer_sid: str | None = "S-1-5-21-9-9-9",
    is_domain_member: bool = True,
    operating_system: str | None = "Windows Server 2022",
    source_key: str = "server|1",
) -> dict[str, Any]:
    return {
        "server_key": name,
        "name": name,
        "dns_host_name": f"{name}.corp.example.com",
        "netbios_name": name.upper(),
        "computer_sid": computer_sid,
        "domain_sid": DOMAIN,
        "is_domain_member": is_domain_member,
        "operating_system": operating_system,
        "source_key": source_key,
    }


def server(**kwargs: Any) -> ObjectVersion:
    version_kwargs = {
        name: kwargs.pop(name)
        for name in list(kwargs)
        if name in {"valid_from", "last_seen_at", "valid_to", "origin", "version_id"}
    }
    state = server_state(**kwargs)
    return version(ObservationKind.SERVER, state["server_key"], state, **version_kwargs)
