"""Building small scan-run transcripts, and reading the relationship graph back, for tests.

The derivation tests need estates the canonical scenarios do not contain — an owner that
changes, a Deny that appears ahead of a grant, the same local group on two hosts — and they
need them to arrive the way a collector would send them, through the ingestion API, so the
projector runs exactly where it runs in production. These builders produce contract-v1
transcripts with real source keys (:mod:`app.contracts.v1.keys`), and
:func:`graph_snapshot` renders the derived graph as a comparable value.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1 import keys
from app.domain import MembershipEdgeKind, PrincipalKind, Sid
from app.domain.relationships import (
    RelationshipCurrency,
    RelationshipType,
    StoredRelationship,
)
from app.models.schema import graph_relationships
from app.repositories.relationships import RelationshipRepository

SCHEMA = "1.0"
T0 = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.UTC)


def at(minutes: int) -> dt.datetime:
    return T0 + dt.timedelta(minutes=minutes)


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def principal(
    sid: str,
    observed: dt.datetime,
    *,
    kind: str = "user",
    host: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "kind": "principal",
        "observed_at": _iso(observed),
        "source_key": keys.principal_key(Sid(sid), PrincipalKind(kind), host),
        "sid": sid,
        "principal_kind": kind,
        "is_deleted": False,
    }
    if host is not None:
        fields["host_key"] = host
    if name is not None and kind != "unresolved":
        fields["display_name"] = name
    return fields


def edge(
    group: str,
    member: str,
    observed: dt.datetime,
    *,
    kind: str = "directory_group_member",
    host: str | None = None,
    member_kind: str | None = "user",
    foreign: bool = False,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "kind": "membership_edge",
        "observed_at": _iso(observed),
        "source_key": keys.membership_key(Sid(group), Sid(member), MembershipEdgeKind(kind), host),
        "group_sid": group,
        "member_sid": member,
        "edge_kind": kind,
        "is_foreign_security_principal": foreign,
    }
    if host is not None:
        fields["host_key"] = host
    if member_kind is not None:
        fields["member_kind"] = member_kind
    return fields


def resource(
    path: str,
    observed: dt.datetime,
    *,
    ace_count: int,
    owner: str | None = None,
    dacl_present: bool = True,
    depth: int = 0,
) -> dict[str, Any]:
    server, share = path.lstrip("\\").split("\\")[:2]
    fields: dict[str, Any] = {
        "kind": "ntfs_resource",
        "observed_at": _iso(observed),
        "source_key": keys.ntfs_resource_key(path),
        "path": path,
        "server_name": server,
        "share_name": share,
        "dacl_present": dacl_present,
        "dacl_protected": depth > 0,
        "ace_count": ace_count if dacl_present else 0,
        "inheritance_enabled": depth == 0,
        "is_acl_boundary": True,
        "depth_from_share_root": depth,
    }
    if owner is not None:
        fields["owner_sid"] = owner
    return fields


def ace(
    path: str,
    trustee: str,
    mask: int,
    order: int | None,
    observed: dt.datetime,
    *,
    ace_type: str = "allow",
    flags: int = 0x03,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "kind": "ntfs_ace",
        "observed_at": _iso(observed),
        "source_key": keys.ntfs_ace_key(path, Sid(trustee), ace_type, mask, flags),
        "path": path,
        "trustee_sid": trustee,
        "ace_type": ace_type,
        "access_mask": mask,
        "ace_flags": flags,
        "source": "inherited" if flags & 0x10 else "explicit",
    }
    if order is not None:
        fields["order_index"] = order
    return fields


def transcript(
    collector: str,
    scopes: Sequence[tuple[str, str]],
    observations: Iterable[dict[str, Any]],
    *,
    started: dt.datetime,
    reconcile: bool = True,
    run_id: str | None = None,
) -> dict[str, Any]:
    """A one-batch run. ``reconcile`` claims completeness over every declared scope."""
    run = run_id or str(uuid.uuid4())
    items = [{"schema_version": SCHEMA, "run_id": run, **item} for item in observations]
    latest = max(
        (dt.datetime.fromisoformat(item["observed_at"]) for item in items), default=started
    )
    scope_list = [{"kind": kind, "key": key} for kind, key in scopes]
    return {
        "start": {
            "schema_version": SCHEMA,
            "run_id": run,
            "source": {
                "collector": collector,
                "collector_host": "COLLECTOR01",
                "method": "test",
                "collector_version": "0.1.0",
            },
            "started_at": _iso(started),
            "scopes": scope_list,
            "incremental": False,
        },
        "batches": [
            {
                "schema_version": SCHEMA,
                "run_id": run,
                "batch_id": str(uuid.uuid4()),
                "sequence": 1,
                "is_final": True,
                "observations": items,
            }
        ],
        "completion": {
            "schema_version": SCHEMA,
            "run_id": run,
            "status": "succeeded",
            "completed_at": _iso(latest + dt.timedelta(minutes=1)),
            "batch_count": 1,
            "observation_count": len(items),
            "error_count": 0,
            "errors": [],
            "reconciled_scopes": scope_list if reconcile else [],
        },
    }


async def all_relationships(session: AsyncSession) -> dict[str, StoredRelationship]:
    ids = (await session.execute(sa.select(graph_relationships.c.relationship_id))).scalars()
    return await RelationshipRepository(session).get_many(ids)


async def find(
    session: AsyncSession,
    relationship_type: RelationshipType,
    source: str | None = None,
    target: str | None = None,
) -> list[StoredRelationship]:
    return sorted(
        (
            r
            for r in (await all_relationships(session)).values()
            if r.relationship_type is relationship_type
            and (source is None or r.source.key == source)
            and (target is None or r.target.key == target)
        ),
        key=lambda r: r.relationship_id,
    )


async def one(
    session: AsyncSession,
    relationship_type: RelationshipType,
    source: str | None = None,
    target: str | None = None,
) -> StoredRelationship:
    found = await find(session, relationship_type, source, target)
    assert len(found) == 1, f"{relationship_type.value} {source} -> {target}: {len(found)} found"
    return found[0]


async def graph_snapshot(session: AsyncSession) -> dict[str, Any]:
    """The logical *current* graph: what holds now, how, and on which facts.

    Timestamps, basis tokens and supports that are no longer current are left out, because
    two derivations of the same facts at different instants, one incremental and one clean,
    must agree on exactly this and on nothing else.
    """
    result: dict[str, Any] = {}
    for relationship in (await all_relationships(session)).values():
        current = [
            support
            for support in relationship.supports
            if support.currency is RelationshipCurrency.CURRENT
        ]
        if not current:
            continue
        result[relationship.relationship_id] = {
            "type": relationship.relationship_type.value,
            "source": str(relationship.source),
            "target": str(relationship.target),
            "scope": None if relationship.scope is None else relationship.scope.key,
            "supports": sorted(
                (
                    support.support_key,
                    str(support.rule),
                    support.derivation_class.value,
                    tuple(sorted(c.value for c in support.conditions)),
                    tuple(sorted(q.value for q in support.qualifiers)),
                    tuple(sorted((e.kind.value, e.key, e.role.value) for e in support.evidence)),
                )
                for support in current
            ),
        }
    return result
