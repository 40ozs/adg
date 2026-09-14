r"""Cutting the demo estate into the scan runs that would have produced it.

:mod:`app.demo.estate` describes an estate. A collector does not report an estate; it
reports *runs* — one per collector, per host, each with its own scopes, its own errors and
its own outcome. This module performs that cut, and the cut is where several of the
product's most important behaviors come from:

* the NTFS run against **FS02 is partial** and names the two directories it could not read,
  so every list below them is "unknown", not "empty";
* the NTFS run against **FS03 failed outright** with no observations at all, while the SMB
  run against FS03 succeeded — so ADG knows a share exists there and nothing about what is
  inside it, which is exactly the state an operator has to be able to see;
* the local group on FS01 comes from a **local_groups** run, not the directory run, because
  ``S-1-5-32-544`` is a fact about a machine.

Every observation is built as its contract model and dumped, so a transcript that would be
rejected by the ingestion endpoint fails here, at generation time, with the model's own
message. The generator cannot emit a payload the server would refuse.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Final

from app.contracts.v1 import keys
from app.contracts.v1.common import SCHEMA_VERSION, AceSource
from app.contracts.v1.observations import (
    MembershipObservation,
    NtfsAceObservation,
    NtfsResourceObservation,
    PrincipalObservation,
    ServerObservation,
    SmbAceObservation,
    SmbShareObservation,
)
from app.demo.estate import (
    DOMAIN_DNS,
    DOMAIN_SID,
    EPOCH,
    DemoEstate,
    DemoRunError,
)
from app.domain import Sid

__all__ = [
    "DEMO_NAMESPACE",
    "DemoTranscript",
    "build_transcripts",
    "restamp",
]

#: Run ids are uuid5 over this namespace and the run's name, so a demo seeded twice reuses
#: the same run ids and the second seeding is an idempotent replay rather than a second
#: estate. :func:`restamp` is there for the caller that wants the opposite.
DEMO_NAMESPACE: Final = uuid.UUID("2f6c9a1e-0b2d-4f3a-9c77-4c7a1d1e5b90")

#: Observations per batch. Small enough that the standard profile sends several, because a
#: single-batch transcript never exercises sequencing, ``is_final`` or a partial delivery.
BATCH_SIZE: Final = 200

_COLLECTOR_VERSION: Final = "0.1.0"


@dataclass(frozen=True, slots=True)
class DemoTranscript:
    """One complete scan run, ready to post."""

    name: str
    collector: str
    collector_host: str
    status: str
    start: dict[str, Any]
    batches: tuple[dict[str, Any], ...]
    completion: dict[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.start["run_id"])

    @property
    def observation_count(self) -> int:
        return sum(len(batch["observations"]) for batch in self.batches)

    def as_document(self) -> dict[str, Any]:
        """The transcript in the same shape as the Phase 0B scenario fixtures."""
        return {
            "start": self.start,
            "batches": [dict(batch) for batch in self.batches],
            "completion": self.completion,
        }


def _run_id(name: str) -> str:
    return str(uuid.uuid5(DEMO_NAMESPACE, f"run:{name}"))


def _batch_id(name: str, sequence: int) -> str:
    return str(uuid.uuid5(DEMO_NAMESPACE, f"batch:{name}:{sequence}"))


def _at(base: dt.datetime, seconds: int) -> dt.datetime:
    return base + dt.timedelta(seconds=seconds)


def _tree_scope_key(server_name: str, share_name: str) -> str:
    r"""The ``directory_tree`` scope key for a share root: ``\\server\share``, case-folded.

    Case-folded because a scope key is compared, and ``\\FS01\Finance`` and
    ``\\fs01\finance`` name the same tree.
    """
    return "\\\\" + server_name.casefold() + "\\" + share_name.casefold()


def _dump(model: Any) -> dict[str, Any]:
    """A contract model as the JSON a collector would send.

    ``exclude_none`` because a collector omits what it has nothing to say about; sending an
    explicit ``null`` for every unpopulated field would be a larger payload asserting the
    same thing.
    """
    dumped: dict[str, Any] = model.model_dump(mode="json", exclude_none=True)
    return dumped


# ---------------------------------------------------------------------------
# Observation builders. Each returns contract JSON, already validated.
# ---------------------------------------------------------------------------


def _principal_observation(principal: Any, run_id: str, observed_at: dt.datetime) -> dict[str, Any]:
    return _dump(
        PrincipalObservation(
            run_id=run_id,
            observed_at=observed_at,
            source_key=keys.principal_key(
                Sid(principal.sid), principal.principal_kind, principal.host_key
            ),
            sid=principal.sid,
            principal_kind=principal.principal_kind,
            domain_sid=(DOMAIN_SID if principal.sid.startswith(f"{DOMAIN_SID}-") else None),
            host_key=principal.host_key,
            display_name=principal.display_name,
            sam_account_name=principal.sam_account_name,
            user_principal_name=principal.user_principal_name,
            distinguished_name=principal.distinguished_name,
            group_scope=principal.group_scope,
            group_type=principal.group_type,
            enabled=principal.enabled,
            unresolved_reason=principal.unresolved_reason,
            last_known_name=principal.last_known_name,
        )
    )


def _membership_observation(edge: Any, run_id: str, observed_at: dt.datetime) -> dict[str, Any]:
    return _dump(
        MembershipObservation(
            run_id=run_id,
            observed_at=observed_at,
            source_key=keys.membership_key(
                Sid(edge.group_sid), Sid(edge.member_sid), edge.edge_kind, edge.host_key
            ),
            group_sid=edge.group_sid,
            member_sid=edge.member_sid,
            edge_kind=edge.edge_kind,
            host_key=edge.host_key,
            member_kind=edge.member_kind,
        )
    )


def _server_observation(server: Any, run_id: str, observed_at: dt.datetime) -> dict[str, Any]:
    return _dump(
        ServerObservation(
            run_id=run_id,
            observed_at=observed_at,
            source_key=keys.server_key(server.name),
            name=server.name,
            dns_host_name=server.dns_host_name,
            domain_sid=DOMAIN_SID,
            is_domain_member=server.is_domain_member,
            operating_system=server.operating_system,
        )
    )


def _share_observations(
    share: Any, run_id: str, base: dt.datetime, offset: int
) -> list[dict[str, Any]]:
    rows = [
        _dump(
            SmbShareObservation(
                run_id=run_id,
                observed_at=_at(base, offset),
                source_key=keys.share_key(share.server_name, share.share_name),
                server_name=share.server_name,
                share_name=share.share_name,
                local_path=share.local_path,
                share_type=share.share_type,
                description=share.description,
                is_special=share.is_special,
            )
        )
    ]
    for position, ace in enumerate(share.aces):
        rows.append(
            _dump(
                SmbAceObservation(
                    run_id=run_id,
                    observed_at=_at(base, offset + 1 + position),
                    source_key=keys.smb_ace_key(
                        share.server_name,
                        share.share_name,
                        Sid(ace.trustee_sid),
                        ace.ace_type.value,
                        ace.access_mask,
                        ace.permission,
                    ),
                    server_name=share.server_name,
                    share_name=share.share_name,
                    trustee_sid=ace.trustee_sid,
                    ace_type=ace.ace_type,
                    access_mask=ace.access_mask,
                    permission=ace.permission,
                    order_index=position,
                )
            )
        )
    return rows


def _directory_observations(
    directory: Any, run_id: str, base: dt.datetime, offset: int
) -> list[dict[str, Any]]:
    rows = [
        _dump(
            NtfsResourceObservation(
                run_id=run_id,
                observed_at=_at(base, offset),
                source_key=keys.ntfs_resource_key(directory.path),
                path=directory.path,
                local_path=directory.local_path,
                server_name=directory.server_name,
                share_name=directory.share_name,
                owner_sid=directory.owner_sid,
                dacl_present=directory.dacl_present,
                dacl_protected=directory.dacl_protected,
                ace_count=directory.ace_count,
                inheritance_enabled=directory.inheritance_enabled,
                is_acl_boundary=directory.is_acl_boundary,
                boundary_reason=directory.boundary_reason,
                depth_from_share_root=directory.depth_from_share_root,
                acl_hash=directory.acl_hash,
                parent_acl_hash=directory.parent_acl_hash,
            )
        )
    ]
    for position, ace in enumerate(directory.aces):
        rows.append(
            _dump(
                NtfsAceObservation(
                    run_id=run_id,
                    observed_at=_at(base, offset + 1 + position),
                    source_key=keys.ntfs_ace_key(
                        directory.path,
                        Sid(ace.trustee_sid),
                        ace.ace_type.value,
                        ace.access_mask,
                        ace.ace_flags,
                    ),
                    path=directory.path,
                    trustee_sid=ace.trustee_sid,
                    ace_type=ace.ace_type,
                    access_mask=ace.access_mask,
                    ace_flags=ace.ace_flags,
                    source=AceSource.INHERITED if ace.is_inherited else AceSource.EXPLICIT,
                    inherited_from=ace.inherited_from if ace.is_inherited else None,
                    order_index=ace.order_index,
                )
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Run assembly.
# ---------------------------------------------------------------------------


def _assemble(
    *,
    name: str,
    collector: str,
    collector_host: str,
    method: str,
    target: str | None,
    scopes: list[tuple[str, str]],
    observations: list[dict[str, Any]],
    started_at: dt.datetime,
    duration_seconds: int,
    status: str,
    errors: tuple[DemoRunError, ...] = (),
    batch_size: int = BATCH_SIZE,
) -> DemoTranscript:
    """One run, as start + batches + completion.

    ``reconciled_scopes`` is populated only for a clean run. Absence may be inferred inside
    a reconciled scope, so a run that hit errors must never claim one: that is what would
    let a later phase read "nobody reported it" as "it was deleted".
    """
    run_id = _run_id(name)
    source = {
        "collector": collector,
        "collector_host": collector_host,
        "method": method,
        "collector_version": _COLLECTOR_VERSION,
    }
    if target is not None:
        source["target"] = target

    start = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "source": source,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "scopes": [{"kind": kind, "key": key} for kind, key in scopes],
        "incremental": False,
    }

    batches: list[dict[str, Any]] = []
    chunks = [
        observations[index : index + batch_size]
        for index in range(0, len(observations), batch_size)
    ] or []
    for sequence, chunk in enumerate(chunks, start=1):
        batches.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "batch_id": _batch_id(name, sequence),
                "sequence": sequence,
                "is_final": sequence == len(chunks),
                "observations": chunk,
            }
        )

    completion: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "completed_at": _at(started_at, duration_seconds).isoformat().replace("+00:00", "Z"),
        "batch_count": len(batches),
        "observation_count": len(observations),
        "error_count": len(errors),
        "errors": [
            {
                "code": error.code,
                "message": error.message,
                **({"target": error.target} if error.target else {}),
                "occurred_at": _at(started_at, duration_seconds - 1)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            for error in errors
        ],
        "reconciled_scopes": (
            [{"kind": kind, "key": key} for kind, key in scopes]
            if status == "succeeded" and not errors
            else []
        ),
    }

    return DemoTranscript(
        name=name,
        collector=collector,
        collector_host=collector_host,
        status=status,
        start=start,
        batches=tuple(batches),
        completion=completion,
    )


def build_transcripts(
    estate: DemoEstate,
    *,
    started_at: dt.datetime = EPOCH,
    batch_size: int = BATCH_SIZE,
) -> tuple[DemoTranscript, ...]:
    """Every scan run that would have produced ``estate``, in the order to post them.

    The order is the order a real deployment collects in — identities, then shares, then
    file systems — and it matters for the demo even though ingestion does not require it:
    a share ACL posted before its trustees exist produces a screen full of unresolved SIDs
    that resolve on the next run, which is true to life but confusing as a first impression.
    """
    local_group_sids = {
        principal.sid for principal in estate.principals if principal.host_key is not None
    }
    directory_principals = [
        principal for principal in estate.principals if principal.host_key is None
    ]
    local_principals = [principal for principal in estate.principals if principal.host_key]
    directory_edges = [edge for edge in estate.edges if edge.host_key is None]
    local_edges = [edge for edge in estate.edges if edge.host_key is not None]
    assert local_group_sids, "The demo estate must carry at least one host-scoped group."

    transcripts: list[DemoTranscript] = []

    # 1. Active Directory: every domain principal and every directory membership edge.
    ad_run = _run_id("ad")
    ad_observations = [
        _principal_observation(principal, ad_run, _at(started_at, index + 1))
        for index, principal in enumerate(directory_principals)
    ]
    ad_observations += [
        _membership_observation(
            edge, ad_run, _at(started_at, len(directory_principals) + index + 1)
        )
        for index, edge in enumerate(directory_edges)
    ]
    transcripts.append(
        _assemble(
            name="ad",
            collector="active_directory",
            collector_host="DC01",
            method="Microsoft.ActiveDirectory.Management",
            target=DOMAIN_DNS,
            scopes=[("domain", DOMAIN_DNS.casefold())],
            observations=ad_observations,
            started_at=started_at,
            duration_seconds=180,
            status="succeeded",
            batch_size=batch_size,
        )
    )

    # 2. Local groups on FS01. A separate run because a BUILTIN SID is a fact about one
    #    machine, and attributing it to the directory scan would make it look domain-wide.
    local_run = _run_id("local-groups-fs01")
    local_observations = [
        _principal_observation(principal, local_run, _at(started_at, 300 + index))
        for index, principal in enumerate(local_principals)
    ]
    local_observations += [
        _membership_observation(
            edge, local_run, _at(started_at, 300 + len(local_principals) + index)
        )
        for index, edge in enumerate(local_edges)
    ]
    transcripts.append(
        _assemble(
            name="local-groups-fs01",
            collector="local_groups",
            collector_host="FS01",
            method="Win32_GroupUser",
            target="FS01",
            scopes=[("local_groups_host", "fs01")],
            observations=local_observations,
            started_at=_at(started_at, 300),
            duration_seconds=30,
            status="succeeded",
            batch_size=batch_size,
        )
    )

    # 3. One SMB run per server. Every one succeeds, including FS03 — a server whose share
    #    list is readable and whose file system is not is an ordinary, and awkward, state.
    for index, server in enumerate(estate.servers):
        run_name = f"smb-{server.name.casefold()}"
        run = _run_id(run_name)
        base = _at(started_at, 600 + index * 120)
        smb_observations: list[dict[str, Any]] = [_server_observation(server, run, _at(base, 1))]
        offset = 2
        for share in estate.shares_on(server.name):
            rows = _share_observations(share, run, base, offset)
            smb_observations.extend(rows)
            offset += len(rows)
        transcripts.append(
            _assemble(
                name=run_name,
                collector="smb",
                collector_host=server.name,
                method="Get-SmbShare",
                target=server.name,
                scopes=[("server", server.name.casefold())],
                observations=smb_observations,
                started_at=base,
                duration_seconds=45,
                status="succeeded",
                batch_size=batch_size,
            )
        )

    # 4. One NTFS run per server, and this is where coverage stops being uniform.
    for index, server in enumerate(estate.servers):
        run_name = f"ntfs-{server.name.casefold()}"
        run = _run_id(run_name)
        base = _at(started_at, 1200 + index * 600)
        directories = estate.directories_on(server.name)
        errors = estate.ntfs_errors.get(server.name, ())

        observations: list[dict[str, Any]] = []
        offset = 1
        for directory in directories:
            rows = _directory_observations(directory, run, base, offset)
            observations.extend(rows)
            offset += len(rows)

        if not directories:
            status = "failed"
        elif errors:
            status = "partial"
        else:
            status = "succeeded"

        transcripts.append(
            _assemble(
                name=run_name,
                collector="ntfs",
                collector_host=server.name,
                method="System.IO.DirectoryInfo.GetAccessControl",
                target=server.name,
                scopes=[
                    ("directory_tree", _tree_scope_key(server.name, share.share_name))
                    for share in estate.shares_on(server.name)
                    if not share.is_special
                ],
                observations=observations,
                started_at=base,
                duration_seconds=240,
                status=status,
                errors=errors,
                batch_size=batch_size,
            )
        )

    return tuple(transcripts)


def restamp(transcript: DemoTranscript, run_id: str | None = None) -> DemoTranscript:
    """The same run under a fresh id, so it can be posted again as a *second* run.

    Seeding twice without this is an idempotent replay — the same run, recognized and not
    re-applied. That is usually what a caller wants. This is for the caller who wants a run
    history with more than one entry in it.
    """
    fresh = run_id or str(uuid.uuid4())
    start = {**transcript.start, "run_id": fresh}
    batches = []
    for batch in transcript.batches:
        batches.append(
            {
                **batch,
                "run_id": fresh,
                "batch_id": str(uuid.uuid4()),
                "observations": [
                    {**observation, "run_id": fresh} for observation in batch["observations"]
                ],
            }
        )
    completion = {**transcript.completion, "run_id": fresh}
    return DemoTranscript(
        name=transcript.name,
        collector=transcript.collector,
        collector_host=transcript.collector_host,
        status=transcript.status,
        start=start,
        batches=tuple(batches),
        completion=completion,
    )
