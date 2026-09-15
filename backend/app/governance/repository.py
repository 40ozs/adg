r"""Every governance query, and the baseline readers that select what a campaign reviews.

Two halves, and the split is the point of the file.

**The baseline readers are read-only over collected state.** They select from
``object_versions`` at one instant and nothing else: no current-state table, no ``UPDATE``,
no ``INSERT``. That is what makes a campaign a function of the timeline rather than of when
somebody happened to press the button, and it is checked structurally —
``tests/governance/test_isolation.py`` walks this module's syntax tree and fails if any
``insert``/``update``/``delete`` here names a collected table.

**The governance writers touch governance tables only.** They write campaigns, items,
decisions, proposals and audit events. Nothing in this file can change what a collector
reported, which is the acceptance criterion the whole phase turns on.

The baseline selection leans entirely on the two indexed columns Phase 7A put on
``object_versions``. A resource-focused campaign reads ``container_key`` — one share's ACL,
one directory's ACL — and a principal-focused one reads ``related_key``, which is the
trustee. So "every principal named on this share as of Tuesday" and "everywhere this group
was named as of Tuesday" are both single indexed reads, and neither needs a table scan or a
second copy of the data.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    CursorResult,
    Row,
    Select,
    and_,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain import (
    AceSource,
    AceType,
    CampaignFocus,
    CampaignStatus,
    CommentRequirement,
    DecisionKind,
    GovernanceEventType,
    OwnershipRole,
    RemediationAction,
    RemediationStatus,
    ReviewItemStatus,
    ReviewScopeKind,
    ReviewTargetKind,
    SharePermission,
)
from app.governance.audit import GovernanceEvent, campaign_chain, event_digest
from app.governance.generation import DraftItem, ObservedGrant
from app.governance.model import (
    CampaignScope,
    GenerationOptions,
    GrantEvidence,
    RemediationProposal,
    ResourceOwner,
    ReviewAssignment,
    ReviewCampaign,
    ReviewDecision,
    ReviewItem,
)
from app.history.model import Certainty, ObjectVersion
from app.history.repository import VersionReader, as_row
from app.models.schema import (
    governance_audit_events,
    object_versions,
    remediation_proposals,
    resource_owners,
    review_assignments,
    review_campaign_scopes,
    review_campaigns,
    review_decisions,
    review_items,
)
from app.repositories.resources import ntfs_ace_record, share_ace_record

__all__ = [
    "MAX_SCOPE_TARGETS",
    "GovernanceRepository",
    "ItemPage",
    "ScopeTooLarge",
    "StatusCounts",
]

MAX_SCOPE_TARGETS: Final = 20_000
"""Shares plus directories one campaign's scopes may select before generation is refused.

A ceiling on the *inputs*, separate from the ceiling on items, because the two fail at
different points: twenty thousand directories each carrying two explicit entries is a
perfectly reasonable estate and a hopeless review. Refused rather than truncated, for the
reason stated wherever this pattern appears in ADG — a silently shortened enumeration
reports coverage nobody has.
"""

_ITEM_FETCH_CEILING: Final = 500


class ScopeTooLarge(Exception):
    """A campaign's scopes select more targets than one review may cover."""

    def __init__(self, selected: int, ceiling: int, scope: CampaignScope) -> None:
        self.selected = selected
        self.ceiling = ceiling
        self.scope = scope
        super().__init__(
            f"The scope {scope.kind.value}:{scope.key} selects at least {selected:,} review "
            f"targets and a campaign may read {ceiling:,}. Generation was refused rather "
            "than truncated: a campaign that quietly stopped enumerating would claim "
            "coverage of everything under that scope while having read part of it. Scope "
            "the campaign to one share or one directory tree at a time."
        )


@dataclass(frozen=True, slots=True)
class StatusCounts:
    """How far a campaign has got, counted in the database rather than in Python."""

    total: int
    pending: int
    decided: int
    by_decision: Mapping[str, int]

    @property
    def completion(self) -> float:
        """Fraction decided, 0.0 through 1.0. Zero items is reported as 0.0, not 1.0 — a
        campaign with nothing in it has not reviewed everything."""
        return 0.0 if self.total == 0 else self.decided / self.total


@dataclass(frozen=True, slots=True)
class ItemPage:
    items: tuple[ReviewItem, ...]
    has_more: bool
    total: int | None


class GovernanceRepository:
    """Governance persistence, and the baseline reads a campaign is generated from."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._versions = VersionReader(session)

    # ------------------------------------------------------------------ baseline reading

    async def grants_at(
        self,
        *,
        focus: CampaignFocus,
        scopes: Sequence[CampaignScope],
        at: dt.datetime,
        target_ceiling: int = MAX_SCOPE_TARGETS,
    ) -> tuple[ObservedGrant, ...]:
        """Every access-control entry the campaign's scopes select, as of ``at``.

        Deduplicated by ``(target_kind, ace_key)``: two overlapping scopes — a server and one
        of its shares — must not make the same entry two items or count it twice in the
        exclusion tally.
        """
        collected: dict[tuple[str, str], ObservedGrant] = {}
        for scope in scopes:
            for grant in await self._grants_for_scope(focus, scope, at, target_ceiling):
                collected.setdefault((grant.target_kind.value, grant.evidence.ace_key), grant)
        return tuple(
            sorted(
                collected.values(),
                key=lambda item: (item.target_kind.value, item.evidence.ace_key),
            )
        )

    async def _grants_for_scope(
        self, focus: CampaignFocus, scope: CampaignScope, at: dt.datetime, ceiling: int
    ) -> tuple[ObservedGrant, ...]:
        if focus is CampaignFocus.PRINCIPAL:
            return await self._grants_naming_principal(scope, at)
        return await self._grants_on_targets(scope, at, ceiling)

    async def _grants_on_targets(
        self, scope: CampaignScope, at: dt.datetime, ceiling: int
    ) -> tuple[ObservedGrant, ...]:
        """Resource-focused selection: the shares and directories a scope names, then their
        entries."""
        shares = await self._shares_for_scope(scope, at)
        resources = await self._resources_for_scope(scope, at, ceiling)
        if len(shares) + len(resources) > ceiling:
            raise ScopeTooLarge(len(shares) + len(resources), ceiling, scope)

        grants: list[ObservedGrant] = []
        if shares:
            for version in await self._versions.contained_at(
                ObservationKind.SMB_ACE, list(shares), at, limit=ceiling * 8
            ):
                path = shares.get(version.container_key or "")
                grants.append(_share_grant(version, at, path))
        if resources:
            for version in await self._versions.contained_at(
                ObservationKind.NTFS_ACE, list(resources), at, limit=ceiling * 8
            ):
                grants.append(_ntfs_grant(version, at, resources.get(version.container_key or "")))
        return tuple(grants)

    async def _grants_naming_principal(
        self, scope: CampaignScope, at: dt.datetime
    ) -> tuple[ObservedGrant, ...]:
        """Principal-focused selection: every entry whose trustee is this principal.

        Direct trustee match only, and deliberately so. Expanding to "every resource this
        principal reaches through its groups" would produce items nobody can act on where
        they sit — the fix for reached-through-a-group access is on the group, not on the
        resource — and it would make the item count a function of the membership graph's
        depth. A campaign reviewing a group's own grants is expressed by scoping it to that
        group, which is exactly this query.
        """
        key = scope.key
        share_versions = await self._versions.related_at(ObservationKind.SMB_ACE, [key], at)
        ntfs_versions = await self._versions.related_at(ObservationKind.NTFS_ACE, [key], at)

        share_paths = await self._share_paths(
            [version.container_key for version in share_versions], at
        )
        resource_paths = await self._resource_paths(
            [version.container_key for version in ntfs_versions], at
        )
        grants = [
            _share_grant(version, at, share_paths.get(version.container_key or ""))
            for version in share_versions
        ]
        grants += [
            _ntfs_grant(version, at, resource_paths.get(version.container_key or ""))
            for version in ntfs_versions
        ]
        return tuple(grants)

    async def _shares_for_scope(
        self, scope: CampaignScope, at: dt.datetime
    ) -> dict[str, str | None]:
        """The share keys a scope selects, mapped to their display paths."""
        if scope.kind is ReviewScopeKind.SHARE:
            versions = await self._versions.versions_at(
                ObservationKind.SMB_SHARE, [scope.key.casefold()], at
            )
            found = tuple(versions.values())
        elif scope.kind is ReviewScopeKind.SERVER:
            found = await self._versions.contained_at(
                ObservationKind.SMB_SHARE, [scope.key.casefold()], at
            )
        else:
            # A directory tree names no share of its own: the share ACL belongs to the whole
            # share, and a campaign scoped to a subdirectory did not ask about it.
            return {}
        return {version.key: _share_path(version) for version in found}

    async def _resources_for_scope(
        self, scope: CampaignScope, at: dt.datetime, ceiling: int
    ) -> dict[str, str | None]:
        """The NTFS resource keys a scope selects, mapped to their display paths."""
        if scope.kind is ReviewScopeKind.DIRECTORY_TREE:
            found = await self._resources_under(scope.key.casefold(), at, ceiling + 1)
        elif scope.kind is ReviewScopeKind.SHARE:
            found = {
                version.key: _resource_path(version)
                for version in await self._versions.contained_at(
                    ObservationKind.NTFS_RESOURCE, [scope.key.casefold()], at, limit=ceiling + 1
                )
            }
        elif scope.kind is ReviewScopeKind.SERVER:
            found = {
                version.key: _resource_path(version)
                for version in await self._versions.related_at(
                    ObservationKind.NTFS_RESOURCE, [scope.key.casefold()], at, limit=ceiling + 1
                )
            }
        else:
            return {}
        if len(found) > ceiling:
            raise ScopeTooLarge(len(found), ceiling, scope)
        return found

    async def _resources_under(
        self, prefix: str, at: dt.datetime, limit: int
    ) -> dict[str, str | None]:
        r"""Every resource at or below one UNC path, as of an instant.

        ``starts_with`` rather than ``LIKE``, for the reason the Phase 7A closure gives for
        the same query: PostgreSQL's default ``LIKE`` escape character is a backslash, which
        is the separator in every UNC path, and ``_`` is a ``LIKE`` wildcard and an ordinary
        character in a Windows folder name. A pattern built from a real path would both
        mis-escape and over-match.

        The separator is appended before the descendant test, so ``\\fs01\finance\hr`` does
        not select ``\\fs01\finance\hr-archive`` -- a different directory with different
        permissions. The prefix itself is matched separately, because the scope's own
        directory is in scope and does not carry a trailing separator.

        Only the key and the display path are read. Rebuilding whole versions here would
        materialize a JSONB blob per directory in order to use two fields of it.
        """
        descendant = prefix.rstrip("\\") + "\\"
        statement = (
            select(object_versions.c.object_key, object_versions.c.state)
            .where(
                object_versions.c.object_kind == ObservationKind.NTFS_RESOURCE.value,
                object_versions.c.is_present,
                object_versions.c.valid_from <= at,
                or_(object_versions.c.valid_to.is_(None), object_versions.c.valid_to > at),
                or_(
                    object_versions.c.object_key == prefix,
                    func.starts_with(object_versions.c.object_key, descendant),
                ),
            )
            .order_by(object_versions.c.object_key)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return {row[0]: (row[1] or {}).get("path") for row in rows}

    async def _share_paths(
        self, keys: Sequence[str | None], at: dt.datetime
    ) -> dict[str, str | None]:
        wanted = [key for key in keys if key]
        if not wanted:
            return {}
        found = await self._versions.versions_at(ObservationKind.SMB_SHARE, wanted, at)
        return {key: _share_path(version) for key, version in found.items()}

    async def _resource_paths(
        self, keys: Sequence[str | None], at: dt.datetime
    ) -> dict[str, str | None]:
        wanted = [key for key in keys if key]
        if not wanted:
            return {}
        found = await self._versions.versions_at(ObservationKind.NTFS_RESOURCE, wanted, at)
        return {key: _resource_path(version) for key, version in found.items()}

    async def principal_names_at(
        self, keys: Sequence[str], at: dt.datetime
    ) -> dict[str, str | None]:
        """The display name each principal had at the baseline, where one was described.

        A missing entry means the SID was named on an ACL and nothing had described it
        *then* — an orphaned-SID finding in its own right, and one an item must report as a
        null name rather than filling in from the current tables. Filling it in would put a
        name on the reviewer's screen that nobody knew at the instant being reviewed.
        """
        unique = list(dict.fromkeys(key for key in keys if key))
        if not unique:
            return {}
        found = await self._versions.versions_at(ObservationKind.PRINCIPAL, unique, at)
        names: dict[str, str | None] = {}
        for key, version in found.items():
            state = version.state or {}
            names[key] = (
                state.get("display_name")
                or state.get("sam_account_name")
                or state.get("last_known_name")
            )
        return names

    # ------------------------------------------------------------------- drift reading

    async def grants_on_targets_at(
        self,
        targets: Sequence[tuple[ReviewTargetKind, str]],
        at: dt.datetime,
    ) -> dict[tuple[str, str], tuple[ObservedGrant, ...]]:
        """Every entry on each named target as of ``at``, bucketed by ``(kind, key)``.

        The read a drift comparison needs, and deliberately **not** :meth:`grants_at`:
        that one re-runs the campaign's scopes, which for a share-scoped campaign means
        re-enumerating the share's whole tree to answer a question about eleven items. This
        goes straight at the targets the items already name, which is two indexed reads
        however many items are being compared.

        A target with no entry at ``at`` is absent from the mapping rather than present with
        an empty tuple. The caller must not read that as "the grant was removed" — see
        :meth:`target_presence_at`, which is the half of the answer that says whether
        anything was looked at.
        """
        share_keys = [key for kind, key in targets if kind is ReviewTargetKind.SHARE]
        resource_keys = [key for kind, key in targets if kind is ReviewTargetKind.RESOURCE]
        collected: dict[tuple[str, str], list[ObservedGrant]] = {}

        if share_keys:
            paths = await self._share_paths(share_keys, at)
            for version in await self._versions.contained_at(
                ObservationKind.SMB_ACE, list(dict.fromkeys(share_keys)), at
            ):
                grant = _share_grant(version, at, paths.get(version.container_key or ""))
                collected.setdefault((grant.target_kind.value, grant.target_key), []).append(grant)
        if resource_keys:
            paths = await self._resource_paths(resource_keys, at)
            for version in await self._versions.contained_at(
                ObservationKind.NTFS_ACE, list(dict.fromkeys(resource_keys)), at
            ):
                grant = _ntfs_grant(version, at, paths.get(version.container_key or ""))
                collected.setdefault((grant.target_kind.value, grant.target_key), []).append(grant)

        return {
            key: tuple(sorted(grants, key=lambda grant: grant.evidence.ace_key))
            for key, grants in collected.items()
        }

    async def target_presence_at(
        self,
        targets: Sequence[tuple[ReviewTargetKind, str]],
        at: dt.datetime,
    ) -> dict[tuple[str, str], tuple[bool | None, Certainty | None]]:
        """Whether each target existed at ``at``, and how firmly that is known.

        Three answers per target, and keeping them apart is the whole reason this method is
        separate from the entry read: ``(True, ...)`` the object was there, ``(False, ...)``
        a scan found it gone, ``(None, None)`` ADG holds no version covering the instant and
        has nothing to say. Only the second may ever be rendered to a reviewer as a removal.

        ``present_only=False`` is load-bearing: a tombstone *is* the answer here, and the
        default read would drop it and leave a deleted share indistinguishable from an
        unscanned one.
        """
        share_keys = list(
            dict.fromkeys(key for kind, key in targets if kind is ReviewTargetKind.SHARE)
        )
        resource_keys = list(
            dict.fromkeys(key for kind, key in targets if kind is ReviewTargetKind.RESOURCE)
        )
        answers: dict[tuple[str, str], tuple[bool | None, Certainty | None]] = {
            (kind.value, key): (None, None) for kind, key in targets
        }

        for kind, keys, observation in (
            (ReviewTargetKind.SHARE, share_keys, ObservationKind.SMB_SHARE),
            (ReviewTargetKind.RESOURCE, resource_keys, ObservationKind.NTFS_RESOURCE),
        ):
            if not keys:
                continue
            found = await self._versions.versions_at(observation, keys, at, present_only=False)
            for key, version in found.items():
                answers[(kind.value, key)] = (version.is_present, version.certainty_at(at))
        return answers

    async def items_for_drift(self, campaign_id: UUID, *, limit: int, offset: int) -> ItemPage:
        """One page of a campaign's items, ordered as :meth:`list_items` orders them.

        A thin alias so the drift service does not have to restate the filter arguments it
        never uses, and so the ordering the two share is stated in one place: a drift report
        paged differently from the item list would show an item twice and skip another.
        """
        return await self.list_items(campaign_id, limit=limit, offset=offset)

    # ---------------------------------------------------------------------------- owners

    async def insert_owner(self, owner: ResourceOwner) -> None:
        await self._session.execute(
            insert(resource_owners).values(
                owner_id=owner.owner_id,
                target_kind=owner.target_kind.value,
                target_key=owner.target_key,
                ownership_role=owner.ownership_role.value,
                owner_subject=owner.owner_subject,
                owner_principal_key=owner.owner_principal_key,
                owner_display_name=owner.owner_display_name,
                note=owner.note,
                assigned_by_subject=owner.assigned_by_subject,
                assigned_at=owner.assigned_at,
                created_at=owner.assigned_at,
            )
        )

    async def get_owner(self, owner_id: UUID) -> ResourceOwner | None:
        row = (
            await self._session.execute(
                select(resource_owners).where(resource_owners.c.owner_id == owner_id)
            )
        ).one_or_none()
        return None if row is None else _owner(row)

    async def list_owners(
        self,
        *,
        target_kind: ReviewTargetKind | None = None,
        target_key: str | None = None,
        owner_subject: str | None = None,
        include_revoked: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[tuple[ResourceOwner, ...], bool]:
        statement = select(resource_owners)
        if target_kind is not None:
            statement = statement.where(resource_owners.c.target_kind == target_kind.value)
        if target_key is not None:
            statement = statement.where(resource_owners.c.target_key == target_key.casefold())
        if owner_subject is not None:
            statement = statement.where(resource_owners.c.owner_subject == owner_subject)
        if not include_revoked:
            statement = statement.where(resource_owners.c.revoked_at.is_(None))
        statement = (
            statement.order_by(
                resource_owners.c.target_key,
                resource_owners.c.ownership_role,
                resource_owners.c.owner_id,
            )
            .offset(offset)
            .limit(limit + 1)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple(_owner(row) for row in rows[:limit]), len(rows) > limit

    async def revoke_owner(self, owner_id: UUID, *, subject: str, at: dt.datetime) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(resource_owners)
                .where(
                    resource_owners.c.owner_id == owner_id,
                    resource_owners.c.revoked_at.is_(None),
                )
                .values(revoked_at=at, revoked_by_subject=subject)
            ),
        )
        return bool(result.rowcount)

    async def owners_for_targets(
        self, targets: Sequence[tuple[ReviewTargetKind, str]]
    ) -> dict[tuple[str, str], tuple[ResourceOwner, ...]]:
        """Active owners for many targets at once, so an item listing costs one query."""
        if not targets:
            return {}
        clauses = [
            and_(
                resource_owners.c.target_kind == kind.value,
                resource_owners.c.target_key == key,
            )
            for kind, key in dict.fromkeys(targets)
        ]
        rows = (
            await self._session.execute(
                select(resource_owners).where(or_(*clauses), resource_owners.c.revoked_at.is_(None))
            )
        ).all()
        grouped: dict[tuple[str, str], list[ResourceOwner]] = {}
        for row in rows:
            grouped.setdefault((row.target_kind, row.target_key), []).append(_owner(row))
        return {key: tuple(value) for key, value in grouped.items()}

    # ------------------------------------------------------------------------- campaigns

    async def insert_campaign(self, campaign: ReviewCampaign) -> None:
        await self._session.execute(
            insert(review_campaigns).values(
                campaign_id=campaign.campaign_id,
                name=campaign.name,
                description=campaign.description,
                focus=campaign.focus.value,
                status=campaign.status.value,
                baseline_at=campaign.baseline_at,
                due_at=campaign.due_at,
                include_inherited=campaign.options.include_inherited,
                include_builtin=campaign.options.include_builtin,
                include_deny=campaign.options.include_deny,
                comment_requirement=campaign.comment_requirement.value,
                created_by_subject=campaign.created_by_subject,
                created_at=campaign.created_at,
                updated_at=campaign.created_at,
            )
        )
        if campaign.scopes:
            await self._session.execute(
                insert(review_campaign_scopes),
                [
                    {
                        "campaign_id": campaign.campaign_id,
                        "scope_kind": scope.kind.value,
                        "scope_key": scope.key,
                        "created_at": campaign.created_at,
                    }
                    for scope in campaign.scopes
                ],
            )

    async def get_campaign(self, campaign_id: UUID) -> ReviewCampaign | None:
        row = (
            await self._session.execute(
                select(review_campaigns).where(review_campaigns.c.campaign_id == campaign_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return _campaign(row, await self.scopes_for(campaign_id))

    async def scopes_for(self, campaign_id: UUID) -> tuple[CampaignScope, ...]:
        rows = (
            await self._session.execute(
                select(review_campaign_scopes)
                .where(review_campaign_scopes.c.campaign_id == campaign_id)
                .order_by(review_campaign_scopes.c.scope_kind, review_campaign_scopes.c.scope_key)
            )
        ).all()
        return tuple(
            CampaignScope(kind=ReviewScopeKind(row.scope_kind), key=row.scope_key) for row in rows
        )

    async def list_campaigns(
        self,
        *,
        status: CampaignStatus | None = None,
        reviewer_subject: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[tuple[ReviewCampaign, ...], bool]:
        statement = select(review_campaigns)
        if status is not None:
            statement = statement.where(review_campaigns.c.status == status.value)
        if reviewer_subject is not None:
            statement = statement.where(
                review_campaigns.c.campaign_id.in_(
                    select(review_assignments.c.campaign_id).where(
                        review_assignments.c.reviewer_subject == reviewer_subject,
                        review_assignments.c.revoked_at.is_(None),
                    )
                )
            )
        statement = (
            statement.order_by(review_campaigns.c.created_at.desc(), review_campaigns.c.campaign_id)
            .offset(offset)
            .limit(limit + 1)
        )
        rows = (await self._session.execute(statement)).all()
        kept = rows[:limit]
        scopes = await self._scopes_for_many([row.campaign_id for row in kept])
        return (
            tuple(_campaign(row, scopes.get(row.campaign_id, ())) for row in kept),
            len(rows) > limit,
        )

    async def _scopes_for_many(
        self, campaign_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[CampaignScope, ...]]:
        if not campaign_ids:
            return {}
        rows = (
            await self._session.execute(
                select(review_campaign_scopes)
                .where(review_campaign_scopes.c.campaign_id.in_(list(campaign_ids)))
                .order_by(review_campaign_scopes.c.scope_kind, review_campaign_scopes.c.scope_key)
            )
        ).all()
        grouped: dict[UUID, list[CampaignScope]] = {}
        for row in rows:
            grouped.setdefault(row.campaign_id, []).append(
                CampaignScope(kind=ReviewScopeKind(row.scope_kind), key=row.scope_key)
            )
        return {key: tuple(value) for key, value in grouped.items()}

    async def record_generation(
        self,
        campaign_id: UUID,
        *,
        digest: str,
        item_count: int,
        excluded: Mapping[str, int],
        at: dt.datetime,
    ) -> None:
        await self._session.execute(
            update(review_campaigns)
            .where(review_campaigns.c.campaign_id == campaign_id)
            .values(
                snapshot_digest=digest,
                item_count=item_count,
                excluded_counts=dict(excluded),
                generated_at=at,
                updated_at=at,
            )
        )

    async def set_campaign_status(
        self,
        campaign_id: UUID,
        status: CampaignStatus,
        *,
        at: dt.datetime,
        subject: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status.value, "updated_at": at}
        if status is CampaignStatus.ACTIVE:
            values["activated_at"] = at
        if status in (CampaignStatus.CLOSED, CampaignStatus.CANCELED):
            values["closed_at"] = at
            values["closed_by_subject"] = subject
        await self._session.execute(
            update(review_campaigns)
            .where(review_campaigns.c.campaign_id == campaign_id)
            .values(**values)
        )

    async def clear_items(self, campaign_id: UUID) -> None:
        """Remove a draft campaign's items so generation can be re-run.

        Safe only for a draft: a campaign that has never been active has no decisions, and
        the foreign key from ``review_decisions`` refuses the delete if that is ever wrong.
        The service checks the status; this is the second line.
        """
        await self._session.execute(
            delete(review_items).where(review_items.c.campaign_id == campaign_id)
        )

    # ----------------------------------------------------------------------- assignments

    async def insert_assignment(self, assignment: ReviewAssignment) -> None:
        await self._session.execute(
            insert(review_assignments).values(
                assignment_id=assignment.assignment_id,
                campaign_id=assignment.campaign_id,
                reviewer_subject=assignment.reviewer_subject,
                reviewer_display_name=assignment.reviewer_display_name,
                reviewer_email=assignment.reviewer_email,
                scope_kind=None if assignment.scope is None else assignment.scope.kind.value,
                scope_key=None if assignment.scope is None else assignment.scope.key,
                due_at=assignment.due_at,
                assigned_by_subject=assignment.assigned_by_subject,
                assigned_at=assignment.assigned_at,
                created_at=assignment.assigned_at,
            )
        )

    async def get_assignment(self, assignment_id: UUID) -> ReviewAssignment | None:
        row = (
            await self._session.execute(
                select(review_assignments).where(
                    review_assignments.c.assignment_id == assignment_id
                )
            )
        ).one_or_none()
        return None if row is None else _assignment(row)

    async def list_assignments(
        self, campaign_id: UUID, *, include_revoked: bool = False
    ) -> tuple[ReviewAssignment, ...]:
        statement = select(review_assignments).where(
            review_assignments.c.campaign_id == campaign_id
        )
        if not include_revoked:
            statement = statement.where(review_assignments.c.revoked_at.is_(None))
        rows = (
            await self._session.execute(
                statement.order_by(
                    review_assignments.c.reviewer_subject, review_assignments.c.assignment_id
                )
            )
        ).all()
        return tuple(_assignment(row) for row in rows)

    async def active_assignments_for(
        self, campaign_id: UUID, subject: str
    ) -> tuple[ReviewAssignment, ...]:
        rows = (
            await self._session.execute(
                select(review_assignments).where(
                    review_assignments.c.campaign_id == campaign_id,
                    review_assignments.c.reviewer_subject == subject,
                    review_assignments.c.revoked_at.is_(None),
                )
            )
        ).all()
        return tuple(_assignment(row) for row in rows)

    async def queue_counts_for(
        self, subject: str, statuses: Sequence[CampaignStatus]
    ) -> dict[UUID, tuple[int, int, dt.datetime | None]]:
        """``{campaign_id: (assigned, decided, earliest_due_at)}`` for one reviewer.

        One grouped statement across every campaign, because a reviewer's first question is
        "what is waiting for me", and answering it with one query per campaign would make
        the landing page cost a round trip per review anybody ever ran.

        The deadline is the **earliest** of the reviewer's own assignment due dates, not the
        campaign's: a reviewer holding two assignments in one campaign is late as soon as the
        first one passes, and reporting the later date would tell them they still had time.
        """
        rows = (
            await self._session.execute(
                select(
                    review_items.c.campaign_id,
                    func.count(),
                    func.count().filter(review_items.c.status == ReviewItemStatus.DECIDED.value),
                    func.min(review_assignments.c.due_at),
                )
                .select_from(
                    review_items.join(
                        review_assignments,
                        review_items.c.assignment_id == review_assignments.c.assignment_id,
                    ).join(
                        review_campaigns,
                        review_items.c.campaign_id == review_campaigns.c.campaign_id,
                    )
                )
                .where(review_assignments.c.reviewer_subject == subject)
                .where(review_assignments.c.revoked_at.is_(None))
                .where(review_campaigns.c.status.in_([status.value for status in statuses]))
                .group_by(review_items.c.campaign_id)
            )
        ).all()
        return {row[0]: (int(row[1]), int(row[2]), row[3]) for row in rows}

    async def revoke_assignment(
        self, assignment_id: UUID, *, subject: str, at: dt.datetime
    ) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(review_assignments)
                .where(
                    review_assignments.c.assignment_id == assignment_id,
                    review_assignments.c.revoked_at.is_(None),
                )
                .values(revoked_at=at, revoked_by_subject=subject)
            ),
        )
        return bool(result.rowcount)

    async def attach_items(
        self, campaign_id: UUID, assignment: ReviewAssignment, *, at: dt.datetime
    ) -> int:
        """Point the items an assignment covers at it. Returns how many were attached.

        An item already attached to another assignment is **reattached**: an assignment is
        the current answer to "who is being asked", and two reviewers both believing an item
        is theirs is worse than one reviewer being replaced.
        """
        statement = update(review_items).where(review_items.c.campaign_id == campaign_id)
        scope = assignment.scope
        if scope is not None:
            statement = statement.where(_scope_predicate(scope))
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                statement.values(assignment_id=assignment.assignment_id, updated_at=at)
            ),
        )
        return int(result.rowcount or 0)

    # ----------------------------------------------------------------------------- items

    async def insert_items(
        self, campaign_id: UUID, items: Sequence[DraftItem], *, at: dt.datetime
    ) -> tuple[ReviewItem, ...]:
        if not items:
            return ()
        rows = []
        built: list[ReviewItem] = []
        for draft in items:
            item_id = uuid4()
            rows.append(
                {
                    "item_id": item_id,
                    "campaign_id": campaign_id,
                    "focus": draft.focus.value,
                    "target_kind": draft.target_kind.value,
                    "target_key": draft.target_key,
                    "target_path": draft.target_path,
                    "principal_key": draft.principal_key,
                    "principal_sid": draft.principal_sid,
                    "principal_display_name": draft.principal_display_name,
                    "grants": [_evidence_json(grant) for grant in draft.grants],
                    "evidence_digest": draft.digest,
                    "certainty": draft.certainty.value,
                    "status": ReviewItemStatus.PENDING.value,
                    "created_at": at,
                    "updated_at": at,
                }
            )
            built.append(
                ReviewItem(
                    item_id=item_id,
                    campaign_id=campaign_id,
                    focus=draft.focus,
                    target_kind=draft.target_kind,
                    target_key=draft.target_key,
                    target_path=draft.target_path,
                    principal_key=draft.principal_key,
                    principal_sid=draft.principal_sid,
                    principal_display_name=draft.principal_display_name,
                    grants=draft.grants,
                    evidence_digest=draft.digest,
                    certainty=draft.certainty,
                    status=ReviewItemStatus.PENDING,
                    assignment_id=None,
                    created_at=at,
                )
            )
        await self._session.execute(insert(review_items), rows)
        return tuple(built)

    async def get_item(self, item_id: UUID) -> ReviewItem | None:
        row = (
            await self._session.execute(
                select(review_items).where(review_items.c.item_id == item_id)
            )
        ).one_or_none()
        return None if row is None else _item(row)

    async def list_items(
        self,
        campaign_id: UUID,
        *,
        status: ReviewItemStatus | None = None,
        assignment_ids: Sequence[UUID] | None = None,
        unassigned_only: bool = False,
        principal_key: str | None = None,
        target_key: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ItemPage:
        statement = select(review_items).where(review_items.c.campaign_id == campaign_id)
        statement = _item_filters(
            statement,
            status=status,
            assignment_ids=assignment_ids,
            unassigned_only=unassigned_only,
            principal_key=principal_key,
            target_key=target_key,
        )
        capped = min(limit, _ITEM_FETCH_CEILING)
        rows = (
            await self._session.execute(
                statement.order_by(review_items.c.target_key, review_items.c.item_id)
                .offset(offset)
                .limit(capped + 1)
            )
        ).all()
        total = await self._count_items(
            campaign_id,
            status=status,
            assignment_ids=assignment_ids,
            unassigned_only=unassigned_only,
            principal_key=principal_key,
            target_key=target_key,
        )
        return ItemPage(
            items=tuple(_item(row) for row in rows[:capped]),
            has_more=len(rows) > capped,
            total=total,
        )

    async def _count_items(
        self,
        campaign_id: UUID,
        *,
        status: ReviewItemStatus | None,
        assignment_ids: Sequence[UUID] | None,
        unassigned_only: bool,
        principal_key: str | None,
        target_key: str | None,
    ) -> int:
        statement = (
            select(func.count())
            .select_from(review_items)
            .where(review_items.c.campaign_id == campaign_id)
        )
        statement = _item_filters(
            statement,
            status=status,
            assignment_ids=assignment_ids,
            unassigned_only=unassigned_only,
            principal_key=principal_key,
            target_key=target_key,
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def status_counts(self, campaign_id: UUID) -> StatusCounts:
        """A campaign's progress, counted in one statement per question.

        Counted rather than paged: "how far along is this campaign" must not require reading
        every item, and a campaign at the item ceiling would make that five thousand rows for
        a progress bar.
        """
        rows = (
            await self._session.execute(
                select(review_items.c.status, func.count())
                .where(review_items.c.campaign_id == campaign_id)
                .group_by(review_items.c.status)
            )
        ).all()
        by_status = {row[0]: int(row[1]) for row in rows}
        decisions = (
            await self._session.execute(
                select(review_decisions.c.decision, func.count())
                .where(
                    review_decisions.c.campaign_id == campaign_id,
                    review_decisions.c.superseded_at.is_(None),
                )
                .group_by(review_decisions.c.decision)
            )
        ).all()
        return StatusCounts(
            total=sum(by_status.values()),
            pending=by_status.get(ReviewItemStatus.PENDING.value, 0),
            decided=by_status.get(ReviewItemStatus.DECIDED.value, 0),
            by_decision={row[0]: int(row[1]) for row in decisions},
        )

    async def all_item_fingerprints(self, campaign_id: UUID) -> dict[tuple[str, str, str], str]:
        """Every item's natural key and evidence digest, for campaign verification.

        Two columns per item rather than whole rows: verification compares a digest set, and
        materializing five thousand JSONB blobs to compare sixty-four characters each would
        make the check cost more than the generation it is checking.
        """
        rows = (
            await self._session.execute(
                select(
                    review_items.c.target_kind,
                    review_items.c.target_key,
                    review_items.c.principal_key,
                    review_items.c.evidence_digest,
                ).where(review_items.c.campaign_id == campaign_id)
            )
        ).all()
        return {(row[0], row[1], row[2]): row[3] for row in rows}

    async def progress_by_assignment(self, campaign_id: UUID) -> dict[UUID | None, tuple[int, int]]:
        """``{assignment_id: (total, decided)}`` for one campaign, counted in one statement.

        ``None`` is the key for items attached to no assignment. That entry is the important
        one: an item nobody was asked to review can never be decided, and a progress report
        that folded it into the pending total would show a campaign that merely looks slow
        rather than one that is stuck.
        """
        rows = (
            await self._session.execute(
                select(
                    review_items.c.assignment_id,
                    func.count(),
                    func.count().filter(review_items.c.status == ReviewItemStatus.DECIDED.value),
                )
                .where(review_items.c.campaign_id == campaign_id)
                .group_by(review_items.c.assignment_id)
            )
        ).all()
        return {row[0]: (int(row[1]), int(row[2])) for row in rows}

    async def late_decisions_by_assignment(self, campaign_id: UUID) -> dict[UUID | None, int]:
        """``{assignment_id: late}`` — current decisions recorded after their deadline.

        Counted over *current* decisions only. A superseded one that was late is history the
        audit trail keeps; a progress report that counted it would describe a reviewer who
        answered late and then corrected themselves as two failures, and the number would
        never go down.
        """
        rows = (
            await self._session.execute(
                select(review_items.c.assignment_id, func.count())
                .select_from(
                    review_items.join(
                        review_decisions,
                        review_items.c.current_decision_id == review_decisions.c.decision_id,
                    )
                )
                .where(review_items.c.campaign_id == campaign_id)
                .where(review_decisions.c.decided_late.is_(True))
                .group_by(review_items.c.assignment_id)
            )
        ).all()
        return {row[0]: int(row[1]) for row in rows}

    async def items_by_ids(
        self, campaign_id: UUID, item_ids: Sequence[UUID]
    ) -> tuple[ReviewItem, ...]:
        """The named items of one campaign, in natural-key order.

        Scoped to the campaign deliberately: a bulk decision names item ids and a campaign,
        and an id belonging to another campaign must come back *missing* rather than be
        silently acted on. The caller reports which ids it could not find.
        """
        unique = list(dict.fromkeys(item_ids))
        if not unique:
            return ()
        rows = (
            await self._session.execute(
                select(review_items)
                .where(review_items.c.campaign_id == campaign_id)
                .where(review_items.c.item_id.in_(unique))
                .order_by(
                    review_items.c.target_kind,
                    review_items.c.target_key,
                    review_items.c.principal_key,
                )
            )
        ).all()
        return tuple(_item(row) for row in rows)

    async def mark_item_decided(self, item_id: UUID, *, decision_id: UUID, at: dt.datetime) -> None:
        await self._session.execute(
            update(review_items)
            .where(review_items.c.item_id == item_id)
            .values(
                status=ReviewItemStatus.DECIDED.value,
                current_decision_id=decision_id,
                decided_at=at,
                updated_at=at,
            )
        )

    # ------------------------------------------------------------------------- decisions

    async def current_decision(self, item_id: UUID) -> ReviewDecision | None:
        row = (
            await self._session.execute(
                select(review_decisions).where(
                    review_decisions.c.item_id == item_id,
                    review_decisions.c.superseded_at.is_(None),
                )
            )
        ).one_or_none()
        return None if row is None else _decision(row)

    async def decisions_for_item(self, item_id: UUID) -> tuple[ReviewDecision, ...]:
        """Every decision on one item, oldest first, ordered by the supersession chain.

        Not by ``decided_at``. Two decisions can share an instant -- a clock with coarse
        resolution, a server that stopped time for a test, two writes inside one transaction
        -- and a tie broken by a random surrogate id would render the history of a reviewer
        changing their mind in the wrong order. That is the one thing this list exists to
        show correctly.

        ``supersedes_decision_id`` is a linked list with exactly one root and no branch (the
        partial unique index on the current row guarantees it), so following it is a total
        order and needs no extra column. Anything not reachable from the root is appended by
        timestamp rather than dropped: an unreachable row would mean the chain is broken, and
        hiding it would hide the breakage.
        """
        rows = (
            await self._session.execute(
                select(review_decisions)
                .where(review_decisions.c.item_id == item_id)
                .order_by(review_decisions.c.decided_at, review_decisions.c.decision_id)
            )
        ).all()
        decisions = [_decision(row) for row in rows]
        return _in_chain_order(decisions)

    async def supersede_decision(
        self, decision_id: UUID, *, successor: UUID, at: dt.datetime
    ) -> None:
        """Mark one decision superseded. The **only** update the trigger permits."""
        await self._session.execute(
            update(review_decisions)
            .where(
                review_decisions.c.decision_id == decision_id,
                review_decisions.c.superseded_at.is_(None),
            )
            .values(superseded_at=at, superseded_by_decision_id=successor)
        )

    async def insert_decision(self, decision: ReviewDecision) -> None:
        await self._session.execute(
            insert(review_decisions).values(
                decision_id=decision.decision_id,
                item_id=decision.item_id,
                campaign_id=decision.campaign_id,
                decision=decision.decision.value,
                rationale=decision.rationale,
                decided_by_subject=decision.decided_by_subject,
                decided_by_display_name=decision.decided_by_display_name,
                decided_at=decision.decided_at,
                decided_late=decision.decided_late,
                supersedes_decision_id=decision.supersedes_decision_id,
                created_at=decision.decided_at,
            )
        )

    # ----------------------------------------------------------------------- remediation

    async def insert_proposal(self, proposal: RemediationProposal) -> None:
        await self._session.execute(
            insert(remediation_proposals).values(
                proposal_id=proposal.proposal_id,
                item_id=proposal.item_id,
                campaign_id=proposal.campaign_id,
                decision_id=proposal.decision_id,
                action=proposal.action.value,
                status=proposal.status.value,
                target_kind=proposal.target_kind.value,
                target_key=proposal.target_key,
                principal_key=proposal.principal_key,
                ace_keys=list(proposal.ace_keys),
                details=dict(proposal.details),
                proposed_by_subject=proposal.proposed_by_subject,
                proposed_at=proposal.proposed_at,
                created_at=proposal.proposed_at,
            )
        )

    async def list_proposals(
        self, campaign_id: UUID, *, item_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[tuple[RemediationProposal, ...], bool]:
        statement = select(remediation_proposals).where(
            remediation_proposals.c.campaign_id == campaign_id
        )
        if item_id is not None:
            statement = statement.where(remediation_proposals.c.item_id == item_id)
        rows = (
            await self._session.execute(
                statement.order_by(
                    remediation_proposals.c.proposed_at, remediation_proposals.c.proposal_id
                )
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return tuple(_proposal(row) for row in rows[:limit]), len(rows) > limit

    # ----------------------------------------------------------------------------- audit

    async def append_event(
        self,
        *,
        chain_key: str,
        event_type: GovernanceEventType,
        occurred_at: dt.datetime,
        actor_subject: str,
        actor_display_name: str | None,
        actor_roles: Sequence[str],
        campaign_id: UUID | None = None,
        item_id: UUID | None = None,
        decision_id: UUID | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> GovernanceEvent:
        """Append one event to a chain, linked to the one before it.

        The chain head is read ``FOR UPDATE`` so that two concurrent appends to the same
        campaign serialize rather than racing for the same ``chain_index``. Without the lock
        the unique constraint would catch the collision — correctly, but as a failed request
        rather than a queued one, and the caller would have lost a decision it had already
        told the reviewer was recorded.
        """
        head = (
            await self._session.execute(
                select(
                    governance_audit_events.c.chain_index, governance_audit_events.c.event_digest
                )
                .where(governance_audit_events.c.chain_key == chain_key)
                .order_by(governance_audit_events.c.chain_index.desc())
                .limit(1)
                .with_for_update()
            )
        ).one_or_none()
        chain_index = 0 if head is None else int(head[0]) + 1
        previous = None if head is None else head[1]
        body = dict(payload or {})
        roles = tuple(sorted(actor_roles))
        digest = event_digest(
            chain_key=chain_key,
            chain_index=chain_index,
            event_type=event_type,
            occurred_at=occurred_at,
            actor_subject=actor_subject,
            actor_roles=roles,
            campaign_id=campaign_id,
            item_id=item_id,
            decision_id=decision_id,
            payload=body,
            previous_digest=previous,
        )
        event = GovernanceEvent(
            event_id=uuid4(),
            chain_key=chain_key,
            chain_index=chain_index,
            event_type=event_type,
            occurred_at=occurred_at,
            actor_subject=actor_subject,
            actor_display_name=actor_display_name,
            actor_roles=roles,
            campaign_id=campaign_id,
            item_id=item_id,
            decision_id=decision_id,
            payload=body,
            previous_digest=previous,
            digest=digest,
        )
        await self._session.execute(
            insert(governance_audit_events).values(
                event_id=event.event_id,
                chain_key=chain_key,
                chain_index=chain_index,
                event_type=event_type.value,
                occurred_at=occurred_at,
                actor_subject=actor_subject,
                actor_display_name=actor_display_name,
                actor_roles=list(roles),
                campaign_id=campaign_id,
                item_id=item_id,
                decision_id=decision_id,
                payload=body,
                previous_digest=previous,
                event_digest=digest,
                created_at=occurred_at,
            )
        )
        return event

    async def events_for_chain(
        self, chain_key: str, *, limit: int = 1_000, offset: int = 0
    ) -> tuple[tuple[GovernanceEvent, ...], bool]:
        rows = (
            await self._session.execute(
                select(governance_audit_events)
                .where(governance_audit_events.c.chain_key == chain_key)
                .order_by(governance_audit_events.c.chain_index)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return tuple(_event(row) for row in rows[:limit]), len(rows) > limit

    async def campaign_events(
        self, campaign_id: UUID, *, limit: int = 1_000, offset: int = 0
    ) -> tuple[tuple[GovernanceEvent, ...], bool]:
        return await self.events_for_chain(campaign_chain(campaign_id), limit=limit, offset=offset)


# ------------------------------------------------------------------------------- helpers


def _item_filters(
    statement: Select[Any],
    *,
    status: ReviewItemStatus | None,
    assignment_ids: Sequence[UUID] | None,
    unassigned_only: bool,
    principal_key: str | None,
    target_key: str | None,
) -> Select[Any]:
    if status is not None:
        statement = statement.where(review_items.c.status == status.value)
    if unassigned_only:
        statement = statement.where(review_items.c.assignment_id.is_(None))
    elif assignment_ids is not None:
        statement = statement.where(review_items.c.assignment_id.in_(list(assignment_ids)))
    if principal_key is not None:
        statement = statement.where(review_items.c.principal_key == principal_key)
    if target_key is not None:
        statement = statement.where(review_items.c.target_key == target_key.casefold())
    return statement


def _scope_predicate(scope: CampaignScope) -> Any:
    r"""Which of a campaign's items one assignment scope covers.

    ``directory_tree`` uses ``starts_with`` with the separator appended, for the same reason
    the baseline reader does: ``LIKE`` would treat the backslash as an escape and ``_`` as a
    wildcard, and ``\\fs01\finance\hr`` must not select ``\\fs01\finance\hr-archive``.
    """
    key = scope.key.casefold()
    if scope.kind is ReviewScopeKind.PRINCIPAL:
        return review_items.c.principal_key == scope.key
    if scope.kind is ReviewScopeKind.SHARE:
        return or_(
            and_(
                review_items.c.target_kind == ReviewTargetKind.SHARE.value,
                review_items.c.target_key == key,
            ),
            and_(
                review_items.c.target_kind == ReviewTargetKind.RESOURCE.value,
                func.starts_with(review_items.c.target_key, key + "\\"),
            ),
        )
    if scope.kind is ReviewScopeKind.DIRECTORY_TREE:
        return and_(
            review_items.c.target_kind == ReviewTargetKind.RESOURCE.value,
            or_(
                review_items.c.target_key == key,
                func.starts_with(review_items.c.target_key, key.rstrip("\\") + "\\"),
            ),
        )
    # SERVER: a share key is '<server>|<share>' and a resource key is a UNC path, so the two
    # need different prefixes. Both are anchored, so 'fs01' cannot select 'fs011'.
    return or_(
        and_(
            review_items.c.target_kind == ReviewTargetKind.SHARE.value,
            func.starts_with(review_items.c.target_key, key + "|"),
        ),
        and_(
            review_items.c.target_kind == ReviewTargetKind.RESOURCE.value,
            func.starts_with(review_items.c.target_key, "\\\\" + key + "\\"),
        ),
    )


def _share_path(version: ObjectVersion) -> str | None:
    state = version.state or {}
    server = state.get("server_key")
    name = state.get("name")
    return f"\\\\{server}\\{name}" if server and name else None


def _resource_path(version: ObjectVersion) -> str | None:
    state = version.state or {}
    path = state.get("path")
    return str(path) if path else None


def _share_grant(version: ObjectVersion, at: dt.datetime, target_path: str | None) -> ObservedGrant:
    record = share_ace_record(as_row(version))
    return ObservedGrant(
        target_kind=ReviewTargetKind.SHARE,
        target_key=record.share_key,
        target_path=target_path,
        evidence=GrantEvidence(
            target_kind=ReviewTargetKind.SHARE,
            ace_key=record.ace_key,
            trustee_sid=record.trustee_sid,
            trustee_key=record.trustee_key,
            ace_type=record.ace_type,
            access_mask=record.access_mask,
            permission=record.permission,
            ace_flags=None,
            source=None,
            inherited_from=None,
            order_index=record.order_index,
            version_id=version.version_id or 0,
            observed_from=version.valid_from,
            last_confirmed_at=version.last_seen_at,
            certainty=version.certainty_at(at),
        ),
    )


def _ntfs_grant(version: ObjectVersion, at: dt.datetime, target_path: str | None) -> ObservedGrant:
    record = ntfs_ace_record(as_row(version))
    return ObservedGrant(
        target_kind=ReviewTargetKind.RESOURCE,
        target_key=record.resource_key,
        target_path=target_path,
        evidence=GrantEvidence(
            target_kind=ReviewTargetKind.RESOURCE,
            ace_key=record.ace_key,
            trustee_sid=record.trustee_sid,
            trustee_key=record.trustee_key,
            ace_type=record.ace_type,
            access_mask=record.access_mask,
            permission=None,
            ace_flags=record.ace_flags,
            source=record.source,
            inherited_from=record.inherited_from,
            order_index=record.order_index,
            version_id=version.version_id or 0,
            observed_from=version.valid_from,
            last_confirmed_at=version.last_seen_at,
            certainty=version.certainty_at(at),
        ),
    )


def _evidence_json(grant: GrantEvidence) -> dict[str, Any]:
    """One grant as it is stored. The digested fields plus the two instants and certainty.

    The digest is taken over :meth:`GrantEvidence.as_digestible` alone, so the stored blob is
    wider than the digested form — the same relationship ``object_versions.state`` has to its
    own digest, and for the same reason: the extra fields describe *when the entry was
    watched*, which is provenance rather than part of the grant.
    """
    return {
        **grant.as_digestible(),
        "observed_from": grant.observed_from.astimezone(dt.UTC).isoformat(),
        "last_confirmed_at": grant.last_confirmed_at.astimezone(dt.UTC).isoformat(),
        "certainty": grant.certainty.value,
    }


def _evidence(raw: Mapping[str, Any]) -> GrantEvidence:
    return GrantEvidence(
        target_kind=ReviewTargetKind(raw["target_kind"]),
        ace_key=raw["ace_key"],
        trustee_sid=raw["trustee_sid"],
        trustee_key=raw["trustee_key"],
        ace_type=AceType(raw["ace_type"]),
        access_mask=raw["access_mask"],
        permission=None if raw["permission"] is None else SharePermission(raw["permission"]),
        ace_flags=raw["ace_flags"],
        source=None if raw["source"] is None else AceSource(raw["source"]),
        inherited_from=raw["inherited_from"],
        order_index=raw["order_index"],
        version_id=int(raw["version_id"]),
        observed_from=dt.datetime.fromisoformat(raw["observed_from"]),
        last_confirmed_at=dt.datetime.fromisoformat(raw["last_confirmed_at"]),
        certainty=Certainty(raw["certainty"]),
    )


def _in_chain_order(decisions: Sequence[ReviewDecision]) -> tuple[ReviewDecision, ...]:
    """Linearize a decision list by following ``supersedes_decision_id`` from the root."""
    if len(decisions) < 2:
        return tuple(decisions)
    successor = {
        decision.supersedes_decision_id: decision
        for decision in decisions
        if decision.supersedes_decision_id is not None
    }
    roots = [decision for decision in decisions if decision.supersedes_decision_id is None]
    ordered: list[ReviewDecision] = []
    seen: set[UUID] = set()
    for root in roots:
        current: ReviewDecision | None = root
        while current is not None and current.decision_id not in seen:
            ordered.append(current)
            seen.add(current.decision_id)
            current = successor.get(current.decision_id)
    # Anything the chain did not reach is a broken link, not a row to hide.
    ordered.extend(decision for decision in decisions if decision.decision_id not in seen)
    return tuple(ordered)


def _owner(row: Row[Any]) -> ResourceOwner:
    return ResourceOwner(
        owner_id=row.owner_id,
        target_kind=ReviewTargetKind(row.target_kind),
        target_key=row.target_key,
        ownership_role=OwnershipRole(row.ownership_role),
        owner_subject=row.owner_subject,
        owner_principal_key=row.owner_principal_key,
        owner_display_name=row.owner_display_name,
        note=row.note,
        assigned_by_subject=row.assigned_by_subject,
        assigned_at=row.assigned_at,
        revoked_at=row.revoked_at,
        revoked_by_subject=row.revoked_by_subject,
    )


def _campaign(row: Row[Any], scopes: tuple[CampaignScope, ...]) -> ReviewCampaign:
    return ReviewCampaign(
        campaign_id=row.campaign_id,
        name=row.name,
        description=row.description,
        focus=CampaignFocus(row.focus),
        status=CampaignStatus(row.status),
        baseline_at=row.baseline_at,
        due_at=row.due_at,
        options=GenerationOptions(
            include_inherited=row.include_inherited,
            include_builtin=row.include_builtin,
            include_deny=row.include_deny,
        ),
        scopes=scopes,
        created_by_subject=row.created_by_subject,
        created_at=row.created_at,
        comment_requirement=CommentRequirement(row.comment_requirement),
        generated_at=row.generated_at,
        snapshot_digest=row.snapshot_digest,
        item_count=row.item_count,
        excluded_counts=dict(row.excluded_counts or {}),
        activated_at=row.activated_at,
        closed_at=row.closed_at,
        closed_by_subject=row.closed_by_subject,
    )


def _assignment(row: Row[Any]) -> ReviewAssignment:
    scope = (
        None
        if row.scope_kind is None
        else CampaignScope(kind=ReviewScopeKind(row.scope_kind), key=row.scope_key)
    )
    return ReviewAssignment(
        assignment_id=row.assignment_id,
        campaign_id=row.campaign_id,
        reviewer_subject=row.reviewer_subject,
        reviewer_display_name=row.reviewer_display_name,
        reviewer_email=row.reviewer_email,
        scope=scope,
        due_at=row.due_at,
        assigned_by_subject=row.assigned_by_subject,
        assigned_at=row.assigned_at,
        revoked_at=row.revoked_at,
        revoked_by_subject=row.revoked_by_subject,
    )


def _item(row: Row[Any]) -> ReviewItem:
    raw = row.grants if isinstance(row.grants, list) else json.loads(row.grants)
    return ReviewItem(
        item_id=row.item_id,
        campaign_id=row.campaign_id,
        focus=CampaignFocus(row.focus),
        target_kind=ReviewTargetKind(row.target_kind),
        target_key=row.target_key,
        target_path=row.target_path,
        principal_key=row.principal_key,
        principal_sid=row.principal_sid,
        principal_display_name=row.principal_display_name,
        grants=tuple(_evidence(entry) for entry in raw),
        evidence_digest=row.evidence_digest,
        certainty=Certainty(row.certainty),
        status=ReviewItemStatus(row.status),
        assignment_id=row.assignment_id,
        created_at=row.created_at,
        current_decision_id=row.current_decision_id,
        decided_at=row.decided_at,
    )


def _decision(row: Row[Any]) -> ReviewDecision:
    return ReviewDecision(
        decision_id=row.decision_id,
        item_id=row.item_id,
        campaign_id=row.campaign_id,
        decision=DecisionKind(row.decision),
        rationale=row.rationale,
        decided_by_subject=row.decided_by_subject,
        decided_by_display_name=row.decided_by_display_name,
        decided_at=row.decided_at,
        decided_late=row.decided_late,
        supersedes_decision_id=row.supersedes_decision_id,
        superseded_at=row.superseded_at,
        superseded_by_decision_id=row.superseded_by_decision_id,
    )


def _proposal(row: Row[Any]) -> RemediationProposal:
    return RemediationProposal(
        proposal_id=row.proposal_id,
        item_id=row.item_id,
        campaign_id=row.campaign_id,
        decision_id=row.decision_id,
        action=RemediationAction(row.action),
        status=RemediationStatus(row.status),
        target_kind=ReviewTargetKind(row.target_kind),
        target_key=row.target_key,
        principal_key=row.principal_key,
        ace_keys=tuple(row.ace_keys or ()),
        details=dict(row.details or {}),
        proposed_by_subject=row.proposed_by_subject,
        proposed_at=row.proposed_at,
        withdrawn_at=row.withdrawn_at,
        withdrawn_by_subject=row.withdrawn_by_subject,
    )


def _event(row: Row[Any]) -> GovernanceEvent:
    return GovernanceEvent(
        event_id=row.event_id,
        chain_key=row.chain_key,
        chain_index=int(row.chain_index),
        event_type=GovernanceEventType(row.event_type),
        occurred_at=row.occurred_at,
        actor_subject=row.actor_subject,
        actor_display_name=row.actor_display_name,
        actor_roles=tuple(row.actor_roles or ()),
        campaign_id=row.campaign_id,
        item_id=row.item_id,
        decision_id=row.decision_id,
        payload=dict(row.payload or {}),
        previous_digest=row.previous_digest,
        digest=row.event_digest,
    )
