"""The scan-run lifecycle: start, batch, completion, inspection.

This module implements `docs/contracts/collector-protocol.md` against PostgreSQL. Three
guarantees are structural rather than conventional, and each is worth naming:

**A retried batch cannot be applied twice.** ``scan_run_batches`` has ``(run_id, batch_id)``
as its primary key and the insert is ``ON CONFLICT DO NOTHING``; a second arrival inserts
zero rows and returns without touching anything. The database enforces it, so two
collectors racing the same retry still produce one application.

**Applying the same facts again converges.** Every upsert is newest-wins on
``last_observed_at``: a replay writes the same values, and an *older* observation arriving
late cannot overwrite a newer one with stale names or a stale ``is_deleted`` flag. An audit
tool that let a late-arriving old scan resurrect a deleted group would report access that
no longer exists.

**Nothing is ever marked absent.** There is no delete path in this module — no share, no
ACE of either layer, no directory, no principal is ever removed because a run did not
mention it. A failed scan, a
server that was rebooted mid-enumeration, and an ACL the collector lacked rights to read all
produce *fewer observations*, not evidence of removal, and an audit tool that deleted on
that basis would report access as revoked while it is still in force. Reconciled scopes are
recorded as evidence for Phase 7, which owns absence; a partial run cannot reconcile at all,
and the contract models refuse to build one that tries.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Table, case, delete, func, insert, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1 import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.domain import ScanStatus
from app.ingestion.plan import BatchPlan, plan_batch, source_fingerprint
from app.models.schema import (
    collector_sources,
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    observations,
    principal_aliases,
    principal_references,
    principals,
    scan_run_batches,
    scan_run_errors,
    scan_run_scopes,
    scan_runs,
    servers,
    smb_share_aces,
    smb_shares,
)

__all__ = [
    "BatchOutcome",
    "CompletionOutcome",
    "IngestionConflict",
    "IngestionService",
    "RunNotFound",
    "RunSnapshot",
    "StartOutcome",
]


class IngestionConflict(Exception):
    """A request contradicts a run that already exists. Maps to HTTP 409.

    Never resolved by guessing: reusing a ``run_id`` for a different run would attribute
    one collector's observations to another's declared coverage.
    """


class RunNotFound(Exception):
    """A batch or completion named a run that was never started. Maps to HTTP 404."""


# Columns that the newest observation of a principal overwrites. Identity columns are in the
# list on purpose: a SID first seen as 'unresolved' and later resolved to a user is the same
# principal learning its kind, and the newer reading is the true one.
_PRINCIPAL_MUTABLE: Final[tuple[str, ...]] = (
    "sid",
    "principal_kind",
    "host_key",
    "domain_sid",
    "display_name",
    "sam_account_name",
    "user_principal_name",
    "distinguished_name",
    "group_scope",
    "group_type",
    "enabled",
    "is_deleted",
    "unresolved_reason",
    "last_known_name",
    "source_key",
)

_EDGE_MUTABLE: Final[tuple[str, ...]] = (
    "group_key",
    "member_key",
    "group_sid",
    "member_sid",
    "edge_kind",
    "host_key",
    "member_kind",
    "is_foreign_security_principal",
    "source_key",
)

_SERVER_MUTABLE: Final[tuple[str, ...]] = (
    "name",
    "dns_host_name",
    "netbios_name",
    "computer_sid",
    "domain_sid",
    "is_domain_member",
    "operating_system",
    "source_key",
)

# server_key is here on purpose. A share cannot move between servers — that would change
# share_key and make it a different row — but the column is rewritten from the newest
# observation anyway so that no upsert path can leave it disagreeing with the key.
_SHARE_MUTABLE: Final[tuple[str, ...]] = (
    "server_key",
    "name",
    "local_path",
    "share_type",
    "description",
    "concurrent_user_limit",
    "caching_mode",
    "is_special",
    "source_key",
)

# Everything but order_index is fixed by the identity key; order_index is not, because an
# administrator can reorder an ACL without changing any entry, and the newest reading of
# the position is the true one.
_SHARE_ACE_MUTABLE: Final[tuple[str, ...]] = (
    "share_key",
    "trustee_sid",
    "trustee_key",
    "ace_type",
    "access_mask",
    "permission",
    "right_token",
    "order_index",
    "source_key",
)

# resource_key is fixed by the path, which is the identity; everything else about a
# directory's descriptor can change without the directory becoming a different one.
_NTFS_RESOURCE_MUTABLE: Final[tuple[str, ...]] = (
    "path",
    "server_key",
    "share_key",
    "local_path",
    "owner_sid",
    "group_sid",
    "dacl_present",
    "dacl_protected",
    "inheritance_enabled",
    "is_acl_boundary",
    "ace_count",
    "depth_from_share_root",
    "resource_kind",
    "boundary_reason",
    "acl_hash",
    "parent_acl_hash",
    "source_key",
)

# Trustee, type, mask, and flags are all fixed by the identity key. order_index and
# inherited_from are not: an administrator can reorder a DACL, and the ancestor an entry
# was inherited from changes when inheritance is re-established higher up.
_NTFS_ACE_MUTABLE: Final[tuple[str, ...]] = (
    "resource_key",
    "trustee_sid",
    "trustee_key",
    "ace_type",
    "access_mask",
    "ace_flags",
    "source",
    "inherited_from",
    "order_index",
    "source_key",
)

_REFERENCE_MUTABLE: Final[tuple[str, ...]] = ("sid", "host_key")


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """Result of opening a run. ``created`` distinguishes 201 from a replayed 200."""

    run_id: UUID
    created: bool
    status: ScanStatus


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """Result of applying a batch.

    ``applied`` is the number of observations this call stored. A duplicate reports zero,
    which is what the protocol's ``{"duplicate": true, "applied": 0}`` means.
    """

    run_id: UUID
    batch_id: UUID
    applied: int
    duplicate: bool
    principals_written: int = 0
    edges_written: int = 0
    servers_written: int = 0
    shares_written: int = 0
    share_aces_written: int = 0
    ntfs_resources_written: int = 0
    ntfs_aces_written: int = 0


@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    """Result of closing a run."""

    run_id: UUID
    status: ScanStatus
    already_completed: bool
    reconciled_scopes: int
    downgrade_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Everything ``GET /api/v1/scan-runs/{run_id}`` reports."""

    run_id: UUID
    status: ScanStatus
    incremental: bool
    started_at: dt.datetime
    completed_at: dt.datetime | None
    collector: str
    collector_host: str
    method: str
    collector_version: str | None
    target: str | None
    batch_count_reported: int | None
    batch_count_received: int
    observation_count_reported: int | None
    observation_count_applied: int
    error_count: int
    notes: str | None
    downgrade_reason: str | None
    declared_scopes: tuple[tuple[str, str], ...]
    reconciled_scopes: tuple[tuple[str, str], ...]
    errors: tuple[dict[str, Any], ...]


class IngestionService:
    """Applies collector payloads inside one database session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ start

    async def start_run(self, start: ScanRunStart) -> StartOutcome:
        """Open a run, or recognize a replayed start.

        Raises:
            IngestionConflict: the run id exists but describes a different run.
        """
        run_id = UUID(start.run_id)
        session = self._session
        now = dt.datetime.now(tz=dt.UTC)

        source_id = await self._upsert_source(start, now)
        declared = sorted((scope.kind.value, scope.key) for scope in start.scopes)

        existing = (
            (await session.execute(select(scan_runs).where(scan_runs.c.run_id == run_id)))
            .mappings()
            .one_or_none()
        )

        if existing is not None:
            await self._require_identical_start(run_id, existing, source_id, start, declared)
            await session.commit()
            return StartOutcome(run_id=run_id, created=False, status=ScanStatus(existing["status"]))

        await session.execute(
            insert(scan_runs).values(
                run_id=run_id,
                source_id=source_id,
                status=ScanStatus.RUNNING.value,
                incremental=start.incremental,
                started_at=start.started_at,
                notes=start.notes,
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(
            insert(scan_run_scopes).values(
                [
                    {
                        "run_id": run_id,
                        "scope_kind": kind,
                        "scope_key": key,
                        "declared": True,
                        "reconciled": False,
                    }
                    for kind, key in declared
                ]
            )
        )
        await session.commit()
        return StartOutcome(run_id=run_id, created=True, status=ScanStatus.RUNNING)

    async def _upsert_source(self, start: ScanRunStart, now: dt.datetime) -> int:
        fingerprint = source_fingerprint(start.source)
        statement = pg_insert(collector_sources).values(
            fingerprint=fingerprint,
            collector=start.source.collector.value,
            collector_host=start.source.collector_host,
            method=start.source.method,
            collector_version=start.source.collector_version,
            target=start.source.target,
            first_seen_at=now,
            last_seen_at=now,
        )
        upsert = statement.on_conflict_do_update(
            index_elements=[collector_sources.c.fingerprint],
            set_={
                "last_seen_at": func.greatest(
                    collector_sources.c.last_seen_at, statement.excluded.last_seen_at
                )
            },
        ).returning(collector_sources.c.id)
        return int((await self._session.execute(upsert)).scalar_one())

    async def _require_identical_start(
        self,
        run_id: UUID,
        existing: Any,
        source_id: int,
        start: ScanRunStart,
        declared: Sequence[tuple[str, str]],
    ) -> None:
        """Reject a replayed start that is not in fact the same run."""
        if existing["source_id"] != source_id:
            raise IngestionConflict(
                f"Run {run_id} was started by a different collector source. A run id "
                "identifies one execution of one collector; reusing it for another would "
                "attribute these observations to coverage that collector never claimed."
            )
        if existing["incremental"] != start.incremental:
            raise IngestionConflict(
                f"Run {run_id} was started with incremental={existing['incremental']}; this "
                f"start declares incremental={start.incremental}. Whether a run may "
                "reconcile depends on that flag, so it cannot be changed mid-run."
            )
        rows = (
            await self._session.execute(
                select(scan_run_scopes.c.scope_kind, scan_run_scopes.c.scope_key).where(
                    scan_run_scopes.c.run_id == run_id, scan_run_scopes.c.declared.is_(True)
                )
            )
        ).all()
        stored = sorted((row.scope_kind, row.scope_key) for row in rows)
        if stored != sorted(declared):
            raise IngestionConflict(
                f"Run {run_id} already declared scopes {stored!r}; this start declares "
                f"{sorted(declared)!r}. Coverage is what a reconciliation is allowed to act "
                "on, so it is fixed when the run opens."
            )

    # ------------------------------------------------------------------ batch

    async def apply_batch(self, batch: ObservationBatch) -> BatchOutcome:
        """Apply one batch, or recognize a replay.

        Raises:
            RunNotFound: no such run.
            IngestionConflict: the run is already completed.
            UnsupportedObservationKind: the batch carries a kind this phase cannot store.
        """
        plan = plan_batch(batch)
        session = self._session
        now = dt.datetime.now(tz=dt.UTC)

        status = (
            await session.execute(
                select(scan_runs.c.status).where(scan_runs.c.run_id == plan.run_id)
            )
        ).scalar_one_or_none()
        if status is None:
            raise RunNotFound(
                f"No scan run {plan.run_id}. POST the start envelope to "
                "/api/v1/scan-runs before sending batches."
            )
        if ScanStatus(status).is_terminal:
            raise IngestionConflict(
                f"Run {plan.run_id} is already {status}; a completed run cannot accept more "
                "observations. Start a new run."
            )

        # RETURNING rather than a row count: the row comes back only when this call is the
        # one that inserted it, which is exactly the claim being made.
        claimed = (
            await session.execute(
                pg_insert(scan_run_batches)
                .values(
                    run_id=plan.run_id,
                    batch_id=plan.batch_id,
                    sequence=plan.sequence,
                    is_final=plan.is_final,
                    observation_count=plan.observation_count,
                    continuation_token=plan.continuation_token,
                    applied_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[scan_run_batches.c.run_id, scan_run_batches.c.batch_id]
                )
                .returning(scan_run_batches.c.batch_id)
            )
        ).first()
        if claimed is None:
            # The batch id was already applied. Acknowledged, not re-applied: this is the
            # whole point of the collector generating batch_id before the first attempt.
            await session.commit()
            return BatchOutcome(
                run_id=plan.run_id, batch_id=plan.batch_id, applied=0, duplicate=True
            )

        await self._write_principals(plan, now)
        await self._write_edges(plan, now)
        await self._write_servers(plan, now)
        await self._write_shares(plan, now)
        await self._write_share_aces(plan, now)
        await self._write_ntfs_resources(plan, now)
        await self._write_ntfs_aces(plan, now)
        # Called here rather than from one ACL writer: a batch carrying only NTFS entries
        # produces references too, and hanging this off the share-ACE path would silently
        # drop them.
        await self._write_references(plan, now)
        await self._write_observations(plan, now)

        await session.execute(
            update(scan_runs)
            .where(scan_runs.c.run_id == plan.run_id)
            .values(
                batch_count_received=scan_runs.c.batch_count_received + 1,
                observation_count_applied=(
                    scan_runs.c.observation_count_applied + plan.observation_count
                ),
                updated_at=now,
            )
        )
        await session.commit()
        return BatchOutcome(
            run_id=plan.run_id,
            batch_id=plan.batch_id,
            applied=plan.observation_count,
            duplicate=False,
            principals_written=len(plan.principals),
            edges_written=len(plan.edges),
            servers_written=len(plan.servers),
            shares_written=len(plan.shares),
            share_aces_written=len(plan.share_aces),
            ntfs_resources_written=len(plan.ntfs_resources),
            ntfs_aces_written=len(plan.ntfs_aces),
        )

    async def _write_principals(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.principals:
            return
        rows = [
            {
                "principal_key": row.principal_key,
                "sid": row.sid,
                "principal_kind": row.principal_kind,
                "host_key": row.host_key,
                "domain_sid": row.domain_sid,
                "display_name": row.display_name,
                "sam_account_name": row.sam_account_name,
                "user_principal_name": row.user_principal_name,
                "distinguished_name": row.distinguished_name,
                "group_scope": row.group_scope,
                "group_type": row.group_type,
                "enabled": row.enabled,
                "is_deleted": row.is_deleted,
                "unresolved_reason": row.unresolved_reason,
                "last_known_name": row.last_known_name,
                "source_key": row.source_key,
                "first_observed_at": row.observed_at,
                "first_observed_run_id": row.run_id,
                "last_observed_at": row.observed_at,
                "last_observed_run_id": row.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in plan.principals
        ]
        statement = pg_insert(principals).values(rows)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[principals.c.principal_key],
                set_=_newest_wins(statement, principals, _PRINCIPAL_MUTABLE, now),
            )
        )

        if not plan.aliases:
            return
        alias_rows = [
            {
                "principal_key": alias.principal_key,
                "alias_kind": alias.alias_kind,
                "value": alias.value,
                "value_folded": alias.value_folded,
                "first_observed_at": alias.observed_at,
                "last_observed_at": alias.observed_at,
            }
            for alias in plan.aliases
        ]
        alias_statement = pg_insert(principal_aliases).values(alias_rows)
        newer = alias_statement.excluded.last_observed_at >= principal_aliases.c.last_observed_at
        await self._session.execute(
            alias_statement.on_conflict_do_update(
                index_elements=[
                    principal_aliases.c.principal_key,
                    principal_aliases.c.alias_kind,
                    principal_aliases.c.value_folded,
                ],
                set_={
                    "value": case(
                        (newer, alias_statement.excluded.value), else_=principal_aliases.c.value
                    ),
                    "first_observed_at": func.least(
                        principal_aliases.c.first_observed_at,
                        alias_statement.excluded.first_observed_at,
                    ),
                    "last_observed_at": func.greatest(
                        principal_aliases.c.last_observed_at,
                        alias_statement.excluded.last_observed_at,
                    ),
                },
            )
        )

    async def _write_edges(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.edges:
            return
        rows = [
            {
                "edge_key": row.edge_key,
                "group_key": row.group_key,
                "member_key": row.member_key,
                "group_sid": row.group_sid,
                "member_sid": row.member_sid,
                "edge_kind": row.edge_kind,
                "host_key": row.host_key,
                "member_kind": row.member_kind,
                "is_foreign_security_principal": row.is_foreign_security_principal,
                "source_key": row.source_key,
                "first_observed_at": row.observed_at,
                "first_observed_run_id": row.run_id,
                "last_observed_at": row.observed_at,
                "last_observed_run_id": row.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in plan.edges
        ]
        statement = pg_insert(membership_edges).values(rows)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[membership_edges.c.edge_key],
                set_=_newest_wins(statement, membership_edges, _EDGE_MUTABLE, now),
            )
        )

    async def _write_servers(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.servers:
            return
        rows = [
            {
                "server_key": row.server_key,
                "name": row.name,
                "dns_host_name": row.dns_host_name,
                "netbios_name": row.netbios_name,
                "computer_sid": row.computer_sid,
                "domain_sid": row.domain_sid,
                "is_domain_member": row.is_domain_member,
                "operating_system": row.operating_system,
                "source_key": row.source_key,
                "first_observed_at": row.observed_at,
                "first_observed_run_id": row.run_id,
                "last_observed_at": row.observed_at,
                "last_observed_run_id": row.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in plan.servers
        ]
        statement = pg_insert(servers).values(rows)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[servers.c.server_key],
                set_=_newest_wins(statement, servers, _SERVER_MUTABLE, now),
            )
        )

    async def _write_shares(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.shares:
            return
        rows = [
            {
                "share_key": row.share_key,
                "server_key": row.server_key,
                "name": row.name,
                "local_path": row.local_path,
                "share_type": row.share_type,
                "description": row.description,
                "concurrent_user_limit": row.concurrent_user_limit,
                "caching_mode": row.caching_mode,
                "is_special": row.is_special,
                "source_key": row.source_key,
                "first_observed_at": row.observed_at,
                "first_observed_run_id": row.run_id,
                "last_observed_at": row.observed_at,
                "last_observed_run_id": row.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in plan.shares
        ]
        statement = pg_insert(smb_shares).values(rows)
        # Upsert, never insert-or-delete. A share re-pointed at a different local path is
        # the same share with a new backing directory, and a share the newest scan did not
        # mention keeps its row: absence is inferred only from a reconciled scope, by
        # Phase 7, and never from a partial or failed run.
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[smb_shares.c.share_key],
                set_=_newest_wins(statement, smb_shares, _SHARE_MUTABLE, now),
            )
        )

    async def _write_share_aces(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.share_aces:
            return
        rows = [
            {
                "ace_key": row.ace_key,
                "share_key": row.share_key,
                "trustee_sid": row.trustee_sid,
                "trustee_key": row.trustee_key,
                "ace_type": row.ace_type,
                "access_mask": row.access_mask,
                "permission": row.permission,
                "right_token": row.right_token,
                "order_index": row.order_index,
                "source_key": row.source_key,
                "first_observed_at": row.observed_at,
                "first_observed_run_id": row.run_id,
                "last_observed_at": row.observed_at,
                "last_observed_run_id": row.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in plan.share_aces
        ]
        statement = pg_insert(smb_share_aces).values(rows)
        # An ACE removed from an ACL is not deleted here either. A share ACL that shrank
        # between runs is a change Phase 7 detects from the run that reconciled the share
        # scope; deleting on sight would let one failed read erase a recorded grant.
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[smb_share_aces.c.ace_key],
                set_=_newest_wins(statement, smb_share_aces, _SHARE_ACE_MUTABLE, now),
            )
        )

    async def _write_ntfs_resources(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.ntfs_resources:
            return
        rows = [
            {
                "resource_key": row.resource_key,
                "path": row.path,
                "server_key": row.server_key,
                "share_key": row.share_key,
                "local_path": row.local_path,
                "owner_sid": row.owner_sid,
                "group_sid": row.group_sid,
                "dacl_present": row.dacl_present,
                "dacl_protected": row.dacl_protected,
                "inheritance_enabled": row.inheritance_enabled,
                "is_acl_boundary": row.is_acl_boundary,
                "ace_count": row.ace_count,
                "depth_from_share_root": row.depth_from_share_root,
                "resource_kind": row.resource_kind,
                "boundary_reason": row.boundary_reason,
                "acl_hash": row.acl_hash,
                "parent_acl_hash": row.parent_acl_hash,
                "source_key": row.source_key,
                "first_observed_at": row.observed_at,
                "first_observed_run_id": row.run_id,
                "last_observed_at": row.observed_at,
                "last_observed_run_id": row.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in plan.ntfs_resources
        ]
        statement = pg_insert(ntfs_resources).values(rows)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[ntfs_resources.c.resource_key],
                set_=_newest_wins(statement, ntfs_resources, _NTFS_RESOURCE_MUTABLE, now),
            )
        )

    async def _write_ntfs_aces(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.ntfs_aces:
            return
        rows = [
            {
                "ace_key": row.ace_key,
                "resource_key": row.resource_key,
                "trustee_sid": row.trustee_sid,
                "trustee_key": row.trustee_key,
                "ace_type": row.ace_type,
                "access_mask": row.access_mask,
                "ace_flags": row.ace_flags,
                "source": row.source,
                "inherited_from": row.inherited_from,
                "order_index": row.order_index,
                "source_key": row.source_key,
                "first_observed_at": row.observed_at,
                "first_observed_run_id": row.run_id,
                "last_observed_at": row.observed_at,
                "last_observed_run_id": row.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in plan.ntfs_aces
        ]
        statement = pg_insert(ntfs_aces).values(rows)
        # No delete path, exactly as for a share ACE. An entry removed from a DACL between
        # runs is a change Phase 7 detects from a reconciled scope; deleting on sight would
        # let a single unreadable directory erase every grant ADG had recorded for it.
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[ntfs_aces.c.ace_key],
                set_=_newest_wins(statement, ntfs_aces, _NTFS_ACE_MUTABLE, now),
            )
        )

    async def _write_references(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.references:
            return
        rows = [
            {
                "principal_key": row.principal_key,
                "sid": row.sid,
                "host_key": row.host_key,
                "reference_kind": row.reference_kind,
                "reference_key": row.reference_key,
                "first_observed_at": row.observed_at,
                "first_observed_run_id": row.run_id,
                "last_observed_at": row.observed_at,
                "last_observed_run_id": row.run_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in plan.references
        ]
        statement = pg_insert(principal_references).values(rows)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    principal_references.c.principal_key,
                    principal_references.c.reference_kind,
                    principal_references.c.reference_key,
                ],
                set_=_newest_wins(statement, principal_references, _REFERENCE_MUTABLE, now),
            )
        )

    async def _write_observations(self, plan: BatchPlan, now: dt.datetime) -> None:
        if not plan.observations:
            return
        rows = [
            {
                "run_id": row.run_id,
                "source_key": row.source_key,
                "kind": row.kind,
                "batch_id": row.batch_id,
                "observed_at": row.observed_at,
                "subject_key": row.subject_key,
                "recorded_at": now,
            }
            for row in plan.observations
        ]
        statement = pg_insert(observations).values(rows)
        # DO NOTHING, not DO UPDATE: within one run a source_key names one object seen once.
        # A second arrival in a different batch is the same fact, and the first batch keeps
        # the attribution so that provenance stays stable.
        await self._session.execute(
            statement.on_conflict_do_nothing(
                index_elements=[observations.c.run_id, observations.c.source_key]
            )
        )

    # ------------------------------------------------------------- completion

    async def complete_run(self, completion: ScanRunCompletion) -> CompletionOutcome:
        """Close a run, recording its errors and any reconciled scopes.

        Raises:
            RunNotFound: no such run.
            IngestionConflict: the run was already completed with a different outcome, or
                the completion reconciles a scope the run never declared.
        """
        run_id = UUID(completion.run_id)
        session = self._session
        now = dt.datetime.now(tz=dt.UTC)

        # Locked: completion reads the received batch count and then writes a status derived
        # from it, and a concurrent second completion must not interleave between the two.
        run = (
            (
                await session.execute(
                    select(scan_runs).where(scan_runs.c.run_id == run_id).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if run is None:
            raise RunNotFound(f"No scan run {run_id}.")

        declared_rows = (
            await session.execute(
                select(scan_run_scopes.c.scope_kind, scan_run_scopes.c.scope_key).where(
                    scan_run_scopes.c.run_id == run_id, scan_run_scopes.c.declared.is_(True)
                )
            )
        ).all()
        declared = {(row.scope_kind, row.scope_key) for row in declared_rows}
        requested = [(scope.kind.value, scope.key) for scope in completion.reconciled_scopes]

        undeclared = sorted(set(requested) - declared)
        if undeclared:
            raise IngestionConflict(
                f"Run {run_id} reconciles scopes it never declared: {undeclared!r}. A run may "
                "only mark objects absent inside a boundary it claimed to enumerate "
                "completely; reconciling elsewhere would delete access it never looked at."
            )
        if requested and run["incremental"]:
            raise IngestionConflict(
                f"Run {run_id} was started as incremental and may not reconcile any scope: "
                "it deliberately re-read only part of its coverage."
            )

        status, downgrade_reason = _resolve_status(completion, int(run["batch_count_received"]))
        if downgrade_reason is not None:
            requested = []

        if ScanStatus(run["status"]).is_terminal:
            return await self._replayed_completion(run, run_id, status, requested)

        await session.execute(
            update(scan_runs)
            .where(scan_runs.c.run_id == run_id)
            .values(
                status=status.value,
                completed_at=completion.completed_at,
                batch_count_reported=completion.batch_count,
                observation_count_reported=completion.observation_count,
                error_count=completion.error_count,
                notes=completion.notes or run["notes"],
                downgrade_reason=downgrade_reason,
                updated_at=now,
            )
        )

        # Replacing rather than appending keeps a retried completion from multiplying the
        # error list; the completion envelope is the run's whole error report, not a delta.
        await session.execute(delete(scan_run_errors).where(scan_run_errors.c.run_id == run_id))
        if completion.errors:
            await session.execute(
                insert(scan_run_errors).values(
                    [
                        {
                            "run_id": run_id,
                            "code": error.code,
                            "message": error.message,
                            "target": error.target,
                            "occurred_at": error.occurred_at,
                        }
                        for error in completion.errors
                    ]
                )
            )

        if requested:
            # A row-value IN, not two independent IN lists: separate lists would match the
            # cross product and reconcile a (kind, key) pair the collector never sent.
            await session.execute(
                update(scan_run_scopes)
                .where(
                    scan_run_scopes.c.run_id == run_id,
                    tuple_(scan_run_scopes.c.scope_kind, scan_run_scopes.c.scope_key).in_(
                        requested
                    ),
                )
                .values(reconciled=True)
            )

        await session.commit()
        return CompletionOutcome(
            run_id=run_id,
            status=status,
            already_completed=False,
            reconciled_scopes=len(requested),
            downgrade_reason=downgrade_reason,
        )

    async def _replayed_completion(
        self,
        run: Any,
        run_id: UUID,
        status: ScanStatus,
        requested: Sequence[tuple[str, str]],
    ) -> CompletionOutcome:
        """Acknowledge a re-sent completion, or refuse one that says something new."""
        if ScanStatus(run["status"]) is not status:
            raise IngestionConflict(
                f"Run {run_id} was already completed as {run['status']}; this completion "
                f"claims {status.value}. A finished run's outcome is not revisable — start a "
                "new run instead."
            )
        await self._session.commit()
        return CompletionOutcome(
            run_id=run_id,
            status=status,
            already_completed=True,
            reconciled_scopes=len(requested),
            downgrade_reason=run["downgrade_reason"],
        )

    # ------------------------------------------------------------- inspection

    async def get_run(self, run_id: UUID) -> RunSnapshot | None:
        """Everything known about one run, or ``None`` if it does not exist."""
        row = (
            (
                await self._session.execute(
                    select(scan_runs, collector_sources)
                    .join(collector_sources, scan_runs.c.source_id == collector_sources.c.id)
                    .where(scan_runs.c.run_id == run_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None

        scope_rows = (
            await self._session.execute(
                select(scan_run_scopes).where(scan_run_scopes.c.run_id == run_id)
            )
        ).all()
        error_rows = (
            (
                await self._session.execute(
                    select(scan_run_errors)
                    .where(scan_run_errors.c.run_id == run_id)
                    .order_by(scan_run_errors.c.id)
                )
            )
            .mappings()
            .all()
        )

        return RunSnapshot(
            run_id=run_id,
            status=ScanStatus(row["status"]),
            incremental=bool(row["incremental"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            collector=row["collector"],
            collector_host=row["collector_host"],
            method=row["method"],
            collector_version=row["collector_version"],
            target=row["target"],
            batch_count_reported=row["batch_count_reported"],
            batch_count_received=int(row["batch_count_received"]),
            observation_count_reported=row["observation_count_reported"],
            observation_count_applied=int(row["observation_count_applied"]),
            error_count=int(row["error_count"]),
            notes=row["notes"],
            downgrade_reason=row["downgrade_reason"],
            declared_scopes=tuple(
                sorted((item.scope_kind, item.scope_key) for item in scope_rows if item.declared)
            ),
            reconciled_scopes=tuple(
                sorted((item.scope_kind, item.scope_key) for item in scope_rows if item.reconciled)
            ),
            errors=tuple(
                {
                    "code": error["code"],
                    "message": error["message"],
                    "target": error["target"],
                    "occurred_at": error["occurred_at"],
                }
                for error in error_rows
            ),
        )


def _newest_wins(
    statement: Any, table: Table, columns: Sequence[str], now: dt.datetime
) -> dict[str, Any]:
    """Build the ``ON CONFLICT DO UPDATE`` clause for a last-observation-wins upsert.

    Two independent comparisons, because an observation can be newer than the stored state
    in one direction and older in the other:

    * a *newer* reading replaces the descriptive columns and advances ``last_observed_*``;
    * an *older* reading — a delayed run, or a backfill — leaves them alone but may still
      push ``first_observed_*`` further back, which is genuinely new information.

    Written as ``CASE`` expressions rather than a ``WHERE`` on the conflict clause so that a
    late-arriving old observation is not discarded wholesale: it contributes what it knows
    and nothing more.
    """
    excluded = statement.excluded
    newer = excluded.last_observed_at >= table.c.last_observed_at
    earlier = excluded.first_observed_at < table.c.first_observed_at

    assignments: dict[str, Any] = {
        name: case((newer, excluded[name]), else_=table.c[name]) for name in columns
    }
    assignments["last_observed_at"] = func.greatest(
        table.c.last_observed_at, excluded.last_observed_at
    )
    assignments["last_observed_run_id"] = case(
        (newer, excluded.last_observed_run_id), else_=table.c.last_observed_run_id
    )
    assignments["first_observed_at"] = func.least(
        table.c.first_observed_at, excluded.first_observed_at
    )
    assignments["first_observed_run_id"] = case(
        (earlier, excluded.first_observed_run_id), else_=table.c.first_observed_run_id
    )
    assignments["updated_at"] = now
    return assignments


def _resolve_status(
    completion: ScanRunCompletion, batches_received: int
) -> tuple[ScanStatus, str | None]:
    """The status the server records, which is not always the one claimed.

    ``batch_count`` is how a collector says how much it sent. Receiving fewer batches than
    that means observations were lost in transit, so the run did not achieve the coverage it
    claims and is recorded as ``partial`` — with the reason stored, never silently.
    """
    claimed = completion.domain_status
    if claimed is ScanStatus.SUCCEEDED and batches_received < completion.batch_count:
        return (
            ScanStatus.PARTIAL,
            f"The collector reported sending {completion.batch_count} batch(es); "
            f"{batches_received} arrived. Coverage is incomplete, so the run is recorded as "
            "partial and reconciles nothing.",
        )
    return claimed, None
