r"""Three scans of Genesee Press, two weeks apart, cut into the runs a deployment receives.

A single perfect scan would show ADG's current state and nothing about how it got there.
These three are what an installation that went live at the start of September 2026 would
have collected by the snapshot date, and the differences between them are chosen to walk
through every rule in ``docs/architecture/current-state-presence.md``:

=========== ============ ================================================================
Generation  Observed at  What it demonstrates
=========== ============ ================================================================
``g1``      2026-09-03   **Initial observation.** Every server's SMB and NTFS scan
                         succeeds except ``GP-SEC-FS01``, whose NTFS scan fails: the
                         collector account had not yet been granted access. Its shares
                         are known; its folders are not.
``g2``      2026-09-17   **Change.** Joiners and leavers; accounts crossing the 180-day
                         deletion line are **tombstoned** by the reconciled AD scan;
                         ``Project-Vega`` is deleted and tombstoned with its edges;
                         ``\\GP-ENG-FS02\QA\Sprint-Scratch`` is deleted and tombstoned by
                         the reconciled NTFS scan; Legal's standard groups and their
                         entries appear; one direct entry is cleaned up; a folder's
                         ``All Staff`` entry is replaced. ``GP-SEC-FS01`` is read for the
                         first time.
``g3``      2026-10-01   **The snapshot, with an imperfect scan.** ``GP-FS-03``'s NTFS
                         scan **fails** and ``GP-MIG-FS01``'s is **partial**. Neither may
                         remove anything, so every folder they did not report stays
                         current — including ``\\GP-MIG-FS01\Migrations\Wave-3-Staging``,
                         which really was deleted on 2026-09-25 and which ADG correctly
                         keeps until a complete scan says otherwise.
=========== ============ ================================================================

Every run id and batch id is a UUID5 under :data:`NAMESPACE`, and every timestamp is
derived from the generation's date, so posting the transcripts twice is an idempotent
replay and a reset produces the same history byte for byte.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from app.demo.estate import DemoRunError
from app.demo.genesee_press.canon import DOMAIN_DNS, DOMAIN_SID, NAMESPACE
from app.demo.genesee_press.estate import GeneseePressEstate, build_estate
from app.demo.transcripts import (
    DemoTranscript,
    _assemble,
    _at,
    _directory_observations,
    _membership_observation,
    _principal_observation,
    _server_observation,
    _share_observations,
    _tree_scope_key,
)

__all__ = [
    "GENERATIONS",
    "RISK_PASS_AT",
    "TRANSCRIPT_NAMESPACE",
    "Generation",
    "build_generations",
]

#: Run and batch ids are UUID5s under this namespace.
TRANSCRIPT_NAMESPACE: Final = uuid.uuid5(NAMESPACE, "adg-demo-transcripts")

#: Large enough that the AD run is a handful of batches, small enough to exercise
#: sequencing on every run with more than one.
BATCH_SIZE: Final = 1000

COLLECTOR_HOST_AD: Final = "GP-DC01"


#: When the operator's scheduled risk pass runs on a scan day: after the last NTFS run of a
#: generation has closed (the latest closes at about 05:45 UTC). The reset and every
#: synthetic scan date their findings here, so a finding's timestamps are part of the canon
#: rather than of the moment somebody ran the reset.
RISK_PASS_AT: Final = dt.time(6, 0)


@dataclass(frozen=True, slots=True)
class Generation:
    key: str
    day: dt.date
    started_at: dt.datetime
    title: str
    ntfs_failed: dict[str, DemoRunError]
    """Servers whose NTFS scan failed outright in this generation."""

    @property
    def evaluated_at(self) -> dt.datetime:
        """The instant the risk pass after this generation is dated (:data:`RISK_PASS_AT`)."""
        return dt.datetime.combine(self.day, RISK_PASS_AT, tzinfo=dt.UTC)


GENERATIONS: Final[tuple[Generation, ...]] = (
    Generation(
        "g1",
        dt.date(2026, 9, 3),
        dt.datetime(2026, 9, 3, 2, 0, tzinfo=dt.UTC),
        "Initial observation",
        {
            "GP-SEC-FS01": DemoRunError(
                code="access_denied",
                message=(
                    "The collector account has not been granted read access to this "
                    "server's file system. Nothing below its shares was read."
                ),
                target=r"\\GP-SEC-FS01",
            )
        },
    ),
    Generation(
        "g2",
        dt.date(2026, 9, 17),
        dt.datetime(2026, 9, 17, 2, 0, tzinfo=dt.UTC),
        "Two weeks of change",
        {},
    ),
    Generation(
        "g3",
        dt.date(2026, 10, 1),
        dt.datetime(2026, 10, 1, 2, 0, tzinfo=dt.UTC),
        "Snapshot",
        {
            "GP-FS-03": DemoRunError(
                code="host_unreachable",
                message=(
                    "The server did not respond. No part of its file system was read, so "
                    "nothing about GP-FS-03 may be inferred from this scan."
                ),
                target=r"\\GP-FS-03",
            )
        },
    ),
)

GENERATIONS_BY_KEY: Final[dict[str, Generation]] = {item.key: item for item in GENERATIONS}


def _assemble_nm(**kwargs: Any) -> DemoTranscript:
    return _assemble(namespace=TRANSCRIPT_NAMESPACE, batch_size=BATCH_SIZE, **kwargs)


def _generation_transcripts(generation: Generation) -> list[DemoTranscript]:
    built: GeneseePressEstate = build_estate(generation.day)
    estate = built.estate
    base = generation.started_at
    prefix = generation.key
    transcripts: list[DemoTranscript] = []

    # 1. Active Directory: every domain principal and edge, reconciled across the domain.
    directory_principals = [item for item in estate.principals if item.host_key is None]
    directory_edges = [item for item in estate.edges if item.host_key is None]
    run_name = f"{prefix}-ad"
    run = str(uuid.uuid5(TRANSCRIPT_NAMESPACE, f"run:{run_name}"))
    observations = [
        _principal_observation(principal, run, _at(base, 1 + index // 50), DOMAIN_SID)
        for index, principal in enumerate(directory_principals)
    ]
    offset = 2 + len(directory_principals) // 50
    observations += [
        _membership_observation(edge, run, _at(base, offset + index // 100))
        for index, edge in enumerate(directory_edges)
    ]
    transcripts.append(
        _assemble_nm(
            name=run_name,
            collector="active_directory",
            collector_host=COLLECTOR_HOST_AD,
            method="Microsoft.ActiveDirectory.Management",
            target=DOMAIN_DNS,
            scopes=[("domain", DOMAIN_DNS.casefold())],
            observations=observations,
            started_at=base,
            duration_seconds=600,
            status="succeeded",
        )
    )

    servers = list(estate.servers)
    for index, server in enumerate(servers):
        host = server.name.casefold()
        # 2. The server's local Administrators group.
        run_name = f"{prefix}-local-{host}"
        run = str(uuid.uuid5(TRANSCRIPT_NAMESPACE, f"run:{run_name}"))
        local_base = _at(base, 900 + index * 20)
        local_principals = [item for item in estate.principals if item.host_key == host]
        local_edges = [item for item in estate.edges if item.host_key == host]
        observations = [
            _principal_observation(item, run, _at(local_base, 1), DOMAIN_SID)
            for item in local_principals
        ] + [_membership_observation(item, run, _at(local_base, 2)) for item in local_edges]
        transcripts.append(
            _assemble_nm(
                name=run_name,
                collector="local_groups",
                collector_host=server.name,
                method="Win32_GroupUser",
                target=server.name,
                scopes=[("local_groups_host", host)],
                observations=observations,
                started_at=local_base,
                duration_seconds=10,
                status="succeeded",
            )
        )

        # 3. The server's shares and share ACLs.
        run_name = f"{prefix}-smb-{host}"
        run = str(uuid.uuid5(TRANSCRIPT_NAMESPACE, f"run:{run_name}"))
        smb_base = _at(base, 1800 + index * 30)
        observations = [_server_observation(server, run, _at(smb_base, 1), DOMAIN_SID)]
        offset = 2
        for share in estate.shares_on(server.name):
            rows = _share_observations(share, run, smb_base, offset)
            observations.extend(rows)
            offset += len(rows)
        transcripts.append(
            _assemble_nm(
                name=run_name,
                collector="smb",
                collector_host=server.name,
                method="Get-SmbShare",
                target=server.name,
                scopes=[("server", host)],
                observations=observations,
                started_at=smb_base,
                duration_seconds=20,
                status="succeeded",
            )
        )

    # 4. NTFS: one run per server, and this is where coverage stops being uniform.
    for index, server in enumerate(servers):
        host = server.name.casefold()
        run_name = f"{prefix}-ntfs-{host}"
        run = str(uuid.uuid5(TRANSCRIPT_NAMESPACE, f"run:{run_name}"))
        ntfs_base = _at(base, 3600 + index * 300)
        failure = generation.ntfs_failed.get(server.name)
        unreadable = [
            path for path in built.unreadable if path.casefold().startswith(f"\\\\{host}\\")
        ]
        errors: tuple[DemoRunError, ...]
        observations = []
        if failure is not None:
            errors, status = (failure,), "failed"
        else:
            offset = 1
            for directory in estate.directories_on(server.name):
                rows = _directory_observations(directory, run, ntfs_base, offset)
                observations.extend(rows)
                offset += len(rows)
            errors = tuple(
                DemoRunError(
                    code="access_denied",
                    message=(
                        "Access to the directory was denied reading its security descriptor. "
                        "Its permissions, and everything below it, are unknown to this scan."
                    ),
                    target=path,
                )
                for path in unreadable
            )
            status = "partial" if errors else "succeeded"
        transcripts.append(
            _assemble_nm(
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
                started_at=ntfs_base,
                duration_seconds=240,
                status=status,
                errors=errors,
            )
        )
    return transcripts


@lru_cache(maxsize=1)
def build_generations() -> tuple[tuple[Generation, tuple[DemoTranscript, ...]], ...]:
    """Every generation and its transcripts, in the order to post them."""
    return tuple(
        (generation, tuple(_generation_transcripts(generation))) for generation in GENERATIONS
    )


def all_transcripts() -> tuple[DemoTranscript, ...]:
    return tuple(transcript for _, transcripts in build_generations() for transcript in transcripts)
