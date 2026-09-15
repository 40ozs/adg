"""Builders for the values the remediation tests need, so each test states only its point.

Deliberately not fixtures. A change is a value with an invariant attached, and most of these
tests are about *which* values are refused — so a test needs to build a slightly wrong one and
watch it fail, which a fixture cannot express without a parameter for every field.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID, uuid4

from app.domain import AceSource, AceType, MembershipEdgeKind, SharePermission
from app.domain.remediation import ChangePlanStatus, ChangeTargetKind, PlannedChangeKind
from app.remediation.model import ChangePlan, EntrySnapshot, MembershipSnapshot, PlannedChange

NOW = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)

FINANCE = "\\\\fs01\\finance"
PAYROLL = "\\\\fs01\\finance\\payroll"
SHARE_KEY = "fs01|finance"
ALICE = "S-1-5-21-1-2-3-1104"
FINANCE_RW = "S-1-5-21-1-2-3-2001"
CONTRACTS_RW = "S-1-5-21-1-2-3-2002"
BUILTIN_ADMINS = "fs01|S-1-5-32-544"

READ_MASK = 0x1200A9
WRITE_MASK = 0x1301BF
FULL_MASK = 0x1F01FF


def entry(
    *,
    ace_key: str = "ace-1",
    trustee_sid: str = ALICE,
    ace_type: AceType = AceType.ALLOW,
    access_mask: int | None = WRITE_MASK,
    permission: SharePermission | None = None,
    ace_flags: int | None = 0x03,
    source: AceSource | None = AceSource.EXPLICIT,
    inherited_from: str | None = None,
    order_index: int | None = 1,
    version_id: int | None = 7,
) -> EntrySnapshot:
    return EntrySnapshot(
        ace_key=ace_key,
        trustee_sid=trustee_sid,
        trustee_key=trustee_sid,
        ace_type=ace_type,
        access_mask=access_mask,
        permission=permission,
        ace_flags=ace_flags,
        source=source,
        inherited_from=inherited_from,
        order_index=order_index,
        version_id=version_id,
        observed_from=NOW - dt.timedelta(days=7),
        last_confirmed_at=NOW - dt.timedelta(hours=1),
    )


def share_entry(
    *,
    ace_key: str = "share-ace-1",
    trustee_sid: str = ALICE,
    permission: SharePermission = SharePermission.FULL,
    ace_type: AceType = AceType.ALLOW,
) -> EntrySnapshot:
    return EntrySnapshot(
        ace_key=ace_key,
        trustee_sid=trustee_sid,
        trustee_key=f"fs01|{trustee_sid}",
        ace_type=ace_type,
        access_mask=None,
        permission=permission,
        order_index=0,
    )


def edge(
    *,
    group_key: str = FINANCE_RW,
    member_key: str = ALICE,
    member_sid: str = ALICE,
    edge_kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
    group_display_name: str | None = "Finance-RW",
    member_display_name: str | None = "alice",
) -> MembershipSnapshot:
    return MembershipSnapshot(
        group_key=group_key,
        member_key=member_key,
        member_sid=member_sid,
        edge_kind=edge_kind,
        group_display_name=group_display_name,
        member_display_name=member_display_name,
    )


def change(
    kind: PlannedChangeKind = PlannedChangeKind.REMOVE_NTFS_ACE,
    *,
    index: int = 0,
    change_id: UUID | None = None,
    target_kind: ChangeTargetKind | None = None,
    target_key: str | None = None,
    **overrides: Any,
) -> PlannedChange:
    """One change of any kind, with the right shape for that kind already filled in."""
    defaults: dict[str, Any] = {
        "principal_sid": ALICE,
        "principal_key": ALICE,
        "principal_display_name": "alice",
    }
    if kind in (PlannedChangeKind.REMOVE_NTFS_ACE, PlannedChangeKind.MODIFY_NTFS_ACE):
        defaults |= {"entry": entry(), "target_display": "\\\\FS01\\Finance\\Payroll"}
        resolved_kind = target_kind or ChangeTargetKind.RESOURCE
        resolved_key = target_key or PAYROLL
        if kind is PlannedChangeKind.MODIFY_NTFS_ACE:
            defaults |= {"after_access_mask": READ_MASK}
    elif kind in (PlannedChangeKind.REMOVE_SHARE_ACE, PlannedChangeKind.MODIFY_SHARE_ACE):
        defaults |= {"entry": share_entry()}
        resolved_kind = target_kind or ChangeTargetKind.SHARE
        resolved_key = target_key or SHARE_KEY
        if kind is PlannedChangeKind.MODIFY_SHARE_ACE:
            defaults |= {"after_permission": SharePermission.READ}
    elif kind is PlannedChangeKind.REMOVE_GROUP_MEMBER:
        defaults |= {"membership": edge()}
        resolved_kind = target_kind or ChangeTargetKind.GROUP
        resolved_key = target_key or FINANCE_RW
    else:
        defaults |= {
            "entry": entry(),
            "membership": edge(group_key=CONTRACTS_RW, group_display_name="Contracts-RW"),
            "replacement_group_key": CONTRACTS_RW,
            "replacement_group_sid": CONTRACTS_RW,
            "replacement_group_display_name": "Contracts-RW",
        }
        resolved_kind = target_kind or ChangeTargetKind.RESOURCE
        resolved_key = target_key or PAYROLL
    return PlannedChange(
        change_id=change_id or uuid4(),
        sequence_index=index,
        kind=kind,
        target_kind=resolved_kind,
        target_key=resolved_key,
        **(defaults | overrides),
    )


def plan(
    *changes: PlannedChange,
    status: ChangePlanStatus = ChangePlanStatus.DRAFT,
    title: str = "Finance quarterly remediation",
    rationale: str = "Certified for removal in the Q3 access review.",
    requested_by: str = "planner@example.test",
    basis_token: str = "a" * 32,
    **overrides: Any,
) -> ChangePlan:
    steps = changes or (change(),)
    return ChangePlan(
        plan_id=overrides.pop("plan_id", uuid4()),
        title=title,
        rationale=rationale,
        status=status,
        changes=tuple(steps),
        requested_by_subject=requested_by,
        requested_by_display_name="Pat Planner",
        requested_at=NOW,
        basis_token=basis_token,
        **overrides,
    )
