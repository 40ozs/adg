r"""One estate, built to a size, for both the cost tests and the benchmark.

`tests/db/test_access_performance.py` asserts that the three access questions cost the same
against a small estate and a large one. `tests/benchmarks/access_benchmark.py` times those
same three questions. They have to be measuring the same thing or neither number means
anything, so the generator lives here and both import it.

The shape is the one that makes "who can reach this" quadratic when it is computed naively:
one group holding every user, named on every share ACL and on every directory DACL. An estate
where each user is granted access directly would be linear however the query were written,
and would measure nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient

from app.contracts.v1 import keys
from app.contracts.v1.common import MAX_BATCH_OBSERVATIONS
from app.domain import AceType, AclAceFacts, MembershipEdgeKind, Sid, acl_hash

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
MODIFY = 0x001301BF

SUBJECT = f"{DOMAIN_SID}-1104"
GROUP = f"{DOMAIN_SID}-1201"

SERVER = "FS01"
SHARE_UNC = "\\\\FS01\\Perf"
SHARE_KEY_NAME = "Perf"

__all__ = [
    "DOMAIN_SID",
    "GROUP",
    "SERVER",
    "SHARE_UNC",
    "SUBJECT",
    "build_estate",
    "load",
]


def observation(kind: str, run_id: str, index: int, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.3",
        "kind": kind,
        "run_id": run_id,
        "observed_at": f"2026-09-14T08:{index % 60:02d}:00Z",
        **fields,
    }


def digest(entries: list[dict[str, Any]]) -> str:
    return acl_hash(
        dacl_present=True,
        dacl_protected=False,
        aces=[
            AclAceFacts(
                trustee_sid=item["trustee_sid"],
                ace_type=AceType(item["ace_type"]),
                access_mask=item["access_mask"],
                ace_flags=item["ace_flags"],
                order_index=item["order_index"],
            )
            for item in entries
        ],
    )


def build_estate(size: int) -> dict[str, Any]:
    r"""An estate with `size` shares, `size` members in the granted group, and `size` groups.

    Every dimension the three questions could grow with moves together, so a single
    comparison between two sizes covers all of them. The subject is a member of one group,
    which is named on every share ACL and on the directory DACL — the shape a real estate has
    and the shape that makes "who can reach this" quadratic if it is computed naively.
    """
    run_id = str(uuid.uuid4())
    observations: list[dict[str, Any]] = [
        observation("server", run_id, 1, source_key=keys.server_key(SERVER), name=SERVER)
    ]
    index = 2

    principals = [SUBJECT, GROUP, *[f"{DOMAIN_SID}-{3000 + n}" for n in range(size)]]
    for sid in principals:
        observations.append(
            observation(
                "principal",
                run_id,
                index,
                source_key=f"principal|{sid}",
                sid=sid,
                principal_kind="domain_group" if sid == GROUP else "user",
            )
        )
        index += 1

    # Everybody in one group, so the trustee inversion has something to invert.
    for sid in [SUBJECT, *[f"{DOMAIN_SID}-{3000 + n}" for n in range(size)]]:
        observations.append(
            observation(
                "membership_edge",
                run_id,
                index,
                source_key=keys.membership_key(
                    Sid(GROUP), Sid(sid), MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
                ),
                group_sid=GROUP,
                member_sid=sid,
                edge_kind=MembershipEdgeKind.DIRECTORY_GROUP_MEMBER.value,
            )
        )
        index += 1

    # `size` shares, each with the group on its ACL, so "every share for one principal"
    # has a list whose length grows with the estate.
    for number in range(size):
        name = SHARE_KEY_NAME if number == 0 else f"{SHARE_KEY_NAME}{number:02d}"
        observations.append(
            observation(
                "smb_share",
                run_id,
                index,
                source_key=keys.share_key(SERVER, name),
                server_name=SERVER,
                share_name=name,
            )
        )
        index += 1
        observations.append(
            observation(
                "smb_ace",
                run_id,
                index,
                source_key=keys.smb_ace_key(SERVER, name, Sid(GROUP), "allow", None, "change"),
                server_name=SERVER,
                share_name=name,
                trustee_sid=GROUP,
                ace_type="allow",
                permission="change",
                order_index=0,
            )
        )
        index += 1

        path = f"\\\\{SERVER}\\{name}"
        entries = [
            observation(
                "ntfs_ace",
                run_id,
                index,
                source_key=keys.ntfs_ace_key(path, Sid(GROUP), "allow", MODIFY, 0x03),
                path=path,
                trustee_sid=GROUP,
                ace_type="allow",
                access_mask=MODIFY,
                ace_flags=0x03,
                source="explicit",
                order_index=0,
            )
        ]
        index += 1
        observations.append(
            observation(
                "ntfs_resource",
                run_id,
                index,
                source_key=keys.ntfs_resource_key(path),
                path=path,
                dacl_present=True,
                dacl_protected=False,
                ace_count=len(entries),
                acl_hash=digest(entries),
                is_acl_boundary=True,
                boundary_reason="share_root",
            )
        )
        index += 1
        observations.extend(entries)

    return {
        "start": {
            "schema_version": "1.3",
            "run_id": run_id,
            "source": {
                "collector": "ntfs",
                "collector_host": "COLLECTOR01",
                "method": "DirectorySecurity.GetSecurityDescriptorBinaryForm",
            },
            "started_at": "2026-09-14T08:00:00Z",
            "scopes": [{"kind": "directory_tree", "key": SHARE_UNC.lower()}],
            "incremental": True,
        },
        "batches": _batched(run_id, observations),
        "completion": {
            "schema_version": "1.3",
            "run_id": run_id,
            "status": "succeeded",
            "completed_at": "2026-09-14T09:00:00Z",
            "batch_count": _batch_count(observations),
            "observation_count": len(observations),
            "error_count": 0,
            "errors": [],
            "reconciled_scopes": [],
        },
    }


def _batch_count(observations: list[dict[str, Any]]) -> int:
    return max(1, -(-len(observations) // MAX_BATCH_OBSERVATIONS))


def _batched(run_id: str, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split the estate across batches the contract will accept.

    A batch is capped at ``MAX_BATCH_OBSERVATIONS``, and a large estate exceeds it — which is
    a real collector constraint, not a test inconvenience, so the generator honors it rather
    than the benchmark being limited to estates small enough to fit in one request.
    """
    chunks = [
        observations[start : start + MAX_BATCH_OBSERVATIONS]
        for start in range(0, len(observations), MAX_BATCH_OBSERVATIONS)
    ]
    return [
        {
            "schema_version": "1.3",
            "run_id": run_id,
            "batch_id": str(uuid.uuid4()),
            "sequence": sequence,
            "is_final": sequence == len(chunks),
            "observations": chunk,
        }
        for sequence, chunk in enumerate(chunks, start=1)
    ]


async def load(client: AsyncClient, size: int) -> None:
    """Build an estate of this size and replay it through the ingestion API."""
    from tests.support.ingest import replay

    await replay(client, build_estate(size))
