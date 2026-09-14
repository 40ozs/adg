r"""A small transcript DSL for history tests: "on Monday it was X, on Friday it was Y".

The scenario fixtures and the demo estate both carry fixed timestamps, which is right for
what they test and useless for a history suite: every question here is about *when*, so
every run needs its instant chosen by the test that writes it.

Everything is built as a contract model and dumped to JSON, so a payload these helpers
produce is one the ingestion endpoint would accept from a real collector — a test cannot
write history through a shape the product would refuse.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from app.contracts.v1 import (
    MembershipObservation,
    NtfsAceObservation,
    NtfsResourceObservation,
    PrincipalObservation,
    ServerObservation,
    SmbAceObservation,
    SmbShareObservation,
    keys,
)
from app.domain import (
    AceSource,
    AceType,
    AclBoundaryReason,
    MembershipEdgeKind,
    PrincipalKind,
    SharePermission,
    ShareType,
    Sid,
)

SCHEMA_VERSION = "1.3"
DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"

#: A week of scan instants. Each run starts on the hour and completes five minutes later,
#: and the ``_END`` values are those completions — which is the instant a reconciled scope
#: tombstones at, because it is when the scan finished without finding the object.
MONDAY = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
MONDAY_END = MONDAY + dt.timedelta(minutes=5)
WEDNESDAY = dt.datetime(2026, 3, 4, 9, 0, tzinfo=dt.UTC)
WEDNESDAY_END = WEDNESDAY + dt.timedelta(minutes=5)
THURSDAY = dt.datetime(2026, 3, 5, 9, 0, tzinfo=dt.UTC)
FRIDAY = dt.datetime(2026, 3, 6, 9, 0, tzinfo=dt.UTC)
FRIDAY_END = FRIDAY + dt.timedelta(minutes=5)
NEXT_MONDAY = dt.datetime(2026, 3, 9, 9, 0, tzinfo=dt.UTC)
NEXT_MONDAY_END = NEXT_MONDAY + dt.timedelta(minutes=5)


def _dump(model: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = model.model_dump(mode="json", exclude_none=True)
    return dumped


def _iso(moment: dt.datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------- observations


def principal(
    sid: str,
    *,
    at: dt.datetime,
    kind: PrincipalKind = PrincipalKind.DOMAIN_GROUP,
    display_name: str | None = None,
    host_key: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    return _dump(
        PrincipalObservation(
            run_id=str(uuid.uuid4()),
            observed_at=at,
            source_key=keys.principal_key(Sid(sid), kind, host_key),
            sid=sid,
            principal_kind=kind,
            domain_sid=DOMAIN_SID if sid.startswith(f"{DOMAIN_SID}-") else None,
            host_key=host_key,
            display_name=display_name,
            enabled=enabled,
        )
    )


def edge(
    group_sid: str,
    member_sid: str,
    *,
    at: dt.datetime,
    kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
    host_key: str | None = None,
) -> dict[str, Any]:
    return _dump(
        MembershipObservation(
            run_id=str(uuid.uuid4()),
            observed_at=at,
            source_key=keys.membership_key(Sid(group_sid), Sid(member_sid), kind, host_key),
            group_sid=group_sid,
            member_sid=member_sid,
            edge_kind=kind,
            host_key=host_key,
        )
    )


def server(name: str, *, at: dt.datetime) -> dict[str, Any]:
    return _dump(
        ServerObservation(
            run_id=str(uuid.uuid4()),
            observed_at=at,
            source_key=keys.server_key(name),
            name=name,
            domain_sid=DOMAIN_SID,
            is_domain_member=True,
        )
    )


def share(
    server_name: str,
    share_name: str,
    *,
    at: dt.datetime,
    description: str | None = None,
    local_path: str = "D:\\Shares\\Data",
) -> dict[str, Any]:
    return _dump(
        SmbShareObservation(
            run_id=str(uuid.uuid4()),
            observed_at=at,
            source_key=keys.share_key(server_name, share_name),
            server_name=server_name,
            share_name=share_name,
            local_path=local_path,
            share_type=ShareType.DISK,
            description=description,
            is_special=False,
        )
    )


def share_ace(
    server_name: str,
    share_name: str,
    trustee_sid: str,
    *,
    at: dt.datetime,
    permission: SharePermission = SharePermission.FULL,
    ace_type: AceType = AceType.ALLOW,
    order_index: int = 0,
) -> dict[str, Any]:
    return _dump(
        SmbAceObservation(
            run_id=str(uuid.uuid4()),
            observed_at=at,
            source_key=keys.smb_ace_key(
                server_name, share_name, Sid(trustee_sid), ace_type.value, None, permission.value
            ),
            server_name=server_name,
            share_name=share_name,
            trustee_sid=trustee_sid,
            ace_type=ace_type,
            permission=permission,
            order_index=order_index,
        )
    )


def resource(
    path: str,
    *,
    at: dt.datetime,
    server_name: str,
    share_name: str,
    ace_count: int,
    owner_sid: str | None = None,
    dacl_protected: bool = True,
) -> dict[str, Any]:
    return _dump(
        NtfsResourceObservation(
            run_id=str(uuid.uuid4()),
            observed_at=at,
            source_key=keys.ntfs_resource_key(path),
            path=path,
            server_name=server_name,
            share_name=share_name,
            owner_sid=owner_sid,
            dacl_present=True,
            dacl_protected=dacl_protected,
            ace_count=ace_count,
            inheritance_enabled=not dacl_protected,
            is_acl_boundary=True,
            boundary_reason=AclBoundaryReason.SCAN_ROOT,
            depth_from_share_root=0,
        )
    )


def ntfs_ace(
    path: str,
    trustee_sid: str,
    *,
    at: dt.datetime,
    access_mask: int,
    ace_type: AceType = AceType.ALLOW,
    ace_flags: int = 0x03,
    order_index: int = 0,
) -> dict[str, Any]:
    return _dump(
        NtfsAceObservation(
            run_id=str(uuid.uuid4()),
            observed_at=at,
            source_key=keys.ntfs_ace_key(
                path, Sid(trustee_sid), ace_type.value, access_mask, ace_flags
            ),
            path=path,
            trustee_sid=trustee_sid,
            ace_type=ace_type,
            access_mask=access_mask,
            ace_flags=ace_flags,
            source=AceSource.EXPLICIT,
            order_index=order_index,
        )
    )


# ----------------------------------------------------------------------------- runs


def scan(
    *,
    collector: str,
    host: str,
    method: str,
    observations: Sequence[dict[str, Any]],
    started_at: dt.datetime,
    completed_at: dt.datetime | None = None,
    scopes: Sequence[tuple[str, str]] = (),
    status: str = "succeeded",
    reconcile: bool = True,
    incremental: bool = False,
    target: str | None = None,
    errors: Sequence[dict[str, Any]] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """One run as start + one batch + completion, ready for ``tests.support.ingest.replay``.

    ``reconcile`` is separate from ``status`` on purpose: several tests need a *successful*
    run that reconciles nothing, which is what an ordinary incremental collection is, and
    conflating the two would make those cases unreachable.
    """
    identifier = run_id or str(uuid.uuid4())
    finished = completed_at or started_at + dt.timedelta(minutes=5)
    rows = [{**item, "run_id": identifier} for item in observations]
    batch_id = str(uuid.uuid4())

    source: dict[str, Any] = {
        "collector": collector,
        "collector_host": host,
        "method": method,
        "collector_version": "test",
    }
    if target is not None:
        source["target"] = target

    batches: list[dict[str, Any]] = []
    if rows:
        batches.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": identifier,
                "batch_id": batch_id,
                "sequence": 1,
                "is_final": True,
                "observations": rows,
            }
        )

    return {
        "start": {
            "schema_version": SCHEMA_VERSION,
            "run_id": identifier,
            "source": source,
            "started_at": _iso(started_at),
            "scopes": [{"kind": kind, "key": key} for kind, key in scopes],
            "incremental": incremental,
        },
        "batches": batches,
        "completion": {
            "schema_version": SCHEMA_VERSION,
            "run_id": identifier,
            "status": status,
            "completed_at": _iso(finished),
            "batch_count": len(batches),
            "observation_count": len(rows),
            "error_count": len(errors),
            "errors": list(errors),
            "reconciled_scopes": (
                [{"kind": kind, "key": key} for kind, key in scopes] if reconcile else []
            ),
        },
    }


def smb_scan(
    *,
    observations: Sequence[dict[str, Any]],
    started_at: dt.datetime,
    completed_at: dt.datetime | None = None,
    server_name: str = "FS01",
    status: str = "succeeded",
    reconcile: bool = True,
    incremental: bool = False,
    errors: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """An SMB run over one server, which is the scope shape that closure rules recognize."""
    return scan(
        collector="smb",
        host=server_name,
        method="Get-SmbShare",
        target=server_name,
        scopes=[("server", server_name.casefold())],
        observations=observations,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        reconcile=reconcile,
        incremental=incremental,
        errors=errors,
    )


def ad_scan(
    *,
    observations: Sequence[dict[str, Any]],
    started_at: dt.datetime,
    completed_at: dt.datetime | None = None,
    status: str = "succeeded",
    reconcile: bool = True,
    domain: str = "corp.example.com",
) -> dict[str, Any]:
    return scan(
        collector="active_directory",
        host="DC01",
        method="Microsoft.ActiveDirectory.Management",
        target=domain,
        scopes=[("domain", domain)],
        observations=observations,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        reconcile=reconcile,
    )


def ntfs_scan(
    *,
    observations: Sequence[dict[str, Any]],
    started_at: dt.datetime,
    completed_at: dt.datetime | None = None,
    server_name: str = "FS01",
    share_name: str = "Finance",
    status: str = "succeeded",
    reconcile: bool = True,
) -> dict[str, Any]:
    tree = f"\\\\{server_name.casefold()}\\{share_name.casefold()}"
    return scan(
        collector="ntfs",
        host=server_name,
        method="System.IO.DirectoryInfo.GetAccessControl",
        target=server_name,
        scopes=[("directory_tree", tree)],
        observations=observations,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        reconcile=reconcile,
    )
