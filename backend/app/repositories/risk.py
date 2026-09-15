r"""Getting facts to the rules, and getting findings back to storage.

Two repositories, and they face opposite directions.

:class:`RiskFactsRepository` reads collected state and builds the framework-free
:class:`~app.risk_engine.RiskFacts` bundle a rule is evaluated over. Nothing in
:mod:`app.risk_engine` ever sees a row; this module is the whole seam.

:class:`RiskFindingRepository` writes what came back, and is where the *safety property* of
incremental evaluation lives: **a pass may only resolve findings it actually covered.** An
incremental pass loads the facts around what one scan run changed. If it were permitted to
close every finding it did not re-match, the first partial pass would close the entire report.
So the scope is recorded on the evaluation row and :meth:`RiskFindingRepository.reconcile`
resolves a finding only when the scope covers *every* subject key the finding names, and only
when the rule that produced it actually ran.

That is the same guard Phase 7A puts on absence, in the same shape, for the same reason: a
thing ADG did not look at must never be reported as a thing ADG looked at and did not find.

**Loading is bounded, and says so.** Every query here has a ceiling, and a load that hits one
reports it rather than silently handing the rules a shortened estate — a rule that matched
over half a DACL, or half a group, would produce a finding with a confidence it has not
earned. The ceilings feed :class:`~app.risk_engine.FactQualifier` values, which is how a
truncated load ends up as a weaker confidence on the finding instead of as nothing at all.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    AceSource,
    AceType,
    AclBoundaryReason,
    AclLayer,
    GroupScope,
    GroupType,
    PrincipalKind,
    ScanStatus,
    SharePermission,
    ShareType,
    UnresolvedReason,
)
from app.domain.errors import DomainValidationError
from app.models.current import (
    current_membership_edges,
    current_ntfs_aces,
    current_ntfs_resources,
    current_principals,
    current_smb_share_aces,
    current_smb_shares,
)
from app.models.schema import (
    RiskEvaluationTrigger,
    RiskFindingEventType,
    RiskFindingStatus,
    object_versions,
    observations,
    principal_references,
    risk_evaluations,
    risk_finding_events,
    risk_findings,
    scan_run_scopes,
    scan_runs,
)
from app.risk_engine import (
    AceFacts,
    AclProvenanceFacts,
    Evidence,
    FactKind,
    MembershipFacts,
    PrincipalFacts,
    ResourceFacts,
    RiskFacts,
    RiskFinding,
    RiskScope,
    RuleId,
    ShareFacts,
)
from app.risk_engine.findings import FindingSubject, finding_key, scope_covers
from app.risk_engine.severity import (
    CONFIDENCE_ORDER,
    SEVERITY_ORDER,
    Confidence,
    FactQualifier,
    Severity,
    SeverityBand,
)

__all__ = [
    "MAX_EDGES",
    "MAX_MEMBERSHIP_DEPTH",
    "MAX_RESOURCES",
    "MAX_TRUSTEES",
    "ChangedFacts",
    "FactLoad",
    "FindingCoverage",
    "FindingPage",
    "FindingQuery",
    "ReconcileOutcome",
    "RiskFactsRepository",
    "RiskFindingRepository",
    "RiskReportRepository",
    "StoredFinding",
]

MAX_RESOURCES: Final = 5_000
"""Directories loaded into one fact bundle.

The ceiling exists so that one bundle cannot grow without bound in memory. Reaching it is
**not** silently accepted: the load reports it in :attr:`FactLoad.truncated`, and a load that
asked for the whole estate and was truncated comes back with a scope that is no longer
``complete`` — so the evaluation built on it may not resolve findings about the directories it
never loaded. An estate larger than this needs the evaluation run per share until a later
phase pages it; see the handoff's known limitations.
"""

MAX_ACES: Final = 50_000
"""Access control entries loaded for one bundle."""

MAX_TRUSTEES: Final = 5_000
"""Distinct trustees whose principal records and membership are loaded for one bundle."""

MAX_EDGES: Final = 50_000
"""Membership edges loaded for one bundle."""

MAX_MEMBERSHIP_DEPTH: Final = 8
"""Levels of group nesting expanded downward from the trustees on a bundle's lists.

Deep enough that the nesting rule at its shipped threshold of three has a level of headroom
to report a breach with, and bounded so that a pathological directory costs a fixed number of
round trips.
"""


# --------------------------------------------------------------------------------------
# Loading facts
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactLoad:
    """A fact bundle and everything the load could not fit into it."""

    facts: RiskFacts
    truncated: tuple[str, ...] = ()
    """Human-readable notes, one per ceiling reached. Empty is the normal case."""

    @property
    def complete(self) -> bool:
        return not self.truncated


@dataclass(frozen=True, slots=True)
class ChangedFacts:
    """What one scan run changed, reduced to the keys an evaluation has to re-examine."""

    run_id: UUID
    kinds: frozenset[FactKind] = frozenset()
    resource_keys: frozenset[str] = frozenset()
    share_keys: frozenset[str] = frozenset()
    principal_keys: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        return not (self.resource_keys or self.share_keys or self.principal_keys)


class RiskFactsRepository:
    """Builds the rules' input out of collected state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- what a run changed -----------------------------------------------------------

    async def changed_by_run(self, run_id: UUID) -> ChangedFacts:
        """The objects a run opened, extended or closed a version for.

        Read from ``object_versions`` rather than from ``observations`` because the two answer
        different questions: an observation says a run *looked at* an object, and a version
        says the object's state actually moved. A run that re-read an unchanged estate touched
        every object and changed none, and re-evaluating the whole estate for it would make
        "incremental" mean nothing.
        """
        statement = sa.select(
            object_versions.c.object_kind,
            object_versions.c.object_key,
            object_versions.c.container_key,
            object_versions.c.related_key,
        ).where(
            sa.or_(
                object_versions.c.opened_by_run_id == run_id,
                object_versions.c.closed_by_run_id == run_id,
            )
        )
        rows = (await self._session.execute(statement)).all()

        kinds: set[FactKind] = set()
        resources: set[str] = set()
        shares: set[str] = set()
        principals_changed: set[str] = set()
        for row in rows:
            kind = FactKind(row.object_kind)
            kinds.add(kind)
            if kind is FactKind.NTFS_RESOURCE:
                resources.add(row.object_key)
                if row.container_key:
                    shares.add(row.container_key)
            elif kind is FactKind.NTFS_ACE:
                if row.container_key:
                    resources.add(row.container_key)
            elif kind is FactKind.SMB_SHARE:
                shares.add(row.object_key)
            elif kind is FactKind.SMB_ACE:
                if row.container_key:
                    shares.add(row.container_key)
            elif kind is FactKind.PRINCIPAL:
                principals_changed.add(row.object_key)
            elif kind is FactKind.MEMBERSHIP_EDGE:
                for key in (row.container_key, row.related_key):
                    if key:
                        principals_changed.add(key)

        # A principal or a membership that changed matters to a resource only where an access
        # control list names it. The reference index is exactly that question and is indexed
        # for it, so this stays one query rather than a scan of every ACL table.
        if principals_changed:
            named_resources, named_shares = await self._referencing(principals_changed)
            resources |= named_resources
            shares |= named_shares

        return ChangedFacts(
            run_id=run_id,
            kinds=frozenset(kinds),
            resource_keys=frozenset(resources),
            share_keys=frozenset(shares),
            principal_keys=frozenset(principals_changed),
        )

    async def _referencing(self, principal_keys: Iterable[str]) -> tuple[set[str], set[str]]:
        """Which resources and shares name any of these principals on an access control list.

        A changed group also drags in the resources that name any group *containing* it, one
        level at a time, because a member added three groups down still reaches whatever the
        outermost group is granted. The walk is bounded by :data:`MAX_MEMBERSHIP_DEPTH`; past
        that the incremental pass simply misses a finding, which the next full pass catches.
        """
        frontier = set(principal_keys)
        seen: set[str] = set()
        for _ in range(MAX_MEMBERSHIP_DEPTH):
            frontier -= seen
            if not frontier:
                break
            seen |= frontier
            rows = (
                await self._session.execute(
                    sa.select(current_membership_edges.c.group_key)
                    .where(current_membership_edges.c.member_key.in_(sorted(frontier)))
                    .distinct()
                    .limit(MAX_TRUSTEES)
                )
            ).all()
            frontier = {row.group_key for row in rows}
        seen |= frontier

        rows = (
            await self._session.execute(
                sa.select(
                    principal_references.c.reference_kind,
                    principal_references.c.reference_key,
                )
                .where(principal_references.c.principal_key.in_(sorted(seen)))
                .distinct()
                .limit(MAX_RESOURCES)
            )
        ).all()
        resources = {row.reference_key for row in rows if row.reference_kind == "ntfs_ace"}
        shares = {row.reference_key for row in rows if row.reference_kind == "smb_ace"}
        return resources, shares

    # -- the bundle -------------------------------------------------------------------

    async def load(
        self,
        *,
        resource_keys: Sequence[str] | None = None,
        share_keys: Sequence[str] | None = None,
        membership_depth: int = MAX_MEMBERSHIP_DEPTH,
    ) -> FactLoad:
        """Build a fact bundle.

        Args:
            resource_keys: load exactly these directories. ``None`` loads every one, up to
                :data:`MAX_RESOURCES`.
            share_keys: load exactly these shares, in addition to the shares the loaded
                directories belong to. ``None`` loads the shares of the loaded directories.
            membership_depth: how far below each trustee to expand group membership.

        The returned :attr:`FactLoad.facts` carries a :class:`~app.risk_engine.RiskScope`
        describing **what was actually loaded**, not what was asked for. That distinction is
        what makes the scope safe to reconcile against: a directory that was requested and
        does not exist is not in the scope, so a finding about it is not resolved by a pass
        that never saw it.
        """
        truncated: list[str] = []

        resources_rows = await self._resource_rows(resource_keys, truncated)
        resource_key_list = [row.resource_key for row in resources_rows]
        ace_rows = await self._ntfs_ace_rows(resource_key_list, truncated)

        wanted_shares = {row.share_key for row in resources_rows}
        if share_keys is not None:
            wanted_shares |= set(share_keys)
        share_rows = await self._share_rows(sorted(wanted_shares))
        share_ace_rows = await self._share_ace_rows(
            [row.share_key for row in share_rows], truncated
        )

        resources = _assemble_resources(resources_rows, ace_rows)
        shares = _assemble_shares(share_rows, share_ace_rows)

        trustees = sorted(
            {ace.trustee_key for resource in resources for ace in resource.aces}
            | {ace.trustee_key for share in shares for ace in share.aces}
        )
        if len(trustees) > MAX_TRUSTEES:
            truncated.append(
                f"{len(trustees)} distinct trustees exceeded the ceiling of {MAX_TRUSTEES}; "
                f"membership was expanded for the first {MAX_TRUSTEES} in key order."
            )
            trustees = trustees[:MAX_TRUSTEES]

        memberships, edge_truncated = await self._memberships(trustees, membership_depth)
        if edge_truncated:
            truncated.append(
                f"The membership expansion reached the ceiling of {MAX_EDGES} edges; groups "
                "beyond it are reported with truncated member lists."
            )

        principal_keys = sorted(
            set(trustees)
            | set(memberships)
            | {member for record in memberships.values() for member in record.member_keys}
        )
        principal_records = await self._principals(principal_keys)

        # A load that asked for everything and hit a ceiling did **not** cover everything, and
        # a scope that still claimed `complete` would let the pass resolve findings about the
        # resources it never loaded -- reporting an exposure as fixed because the bundle ran
        # out of room. So truncation demotes a complete scope to the keys actually loaded.
        asked_for_everything = resource_keys is None and share_keys is None
        scope = RiskScope(
            complete=asked_for_everything and not truncated,
            resource_keys=(
                None if asked_for_everything and not truncated else frozenset(resource_key_list)
            ),
            share_keys=(
                None
                if asked_for_everything and not truncated
                else frozenset(row.share_key for row in share_rows)
            ),
            principal_keys=(
                None if asked_for_everything and not truncated else frozenset(trustees)
            ),
        )
        facts = RiskFacts(
            resources=resources,
            shares=shares,
            principals=principal_records,
            memberships=memberships,
            scope=scope,
        )
        return FactLoad(facts=facts, truncated=tuple(truncated))

    async def _resource_rows(
        self, resource_keys: Sequence[str] | None, truncated: list[str]
    ) -> Sequence[Any]:
        statement = sa.select(current_ntfs_resources).order_by(
            current_ntfs_resources.c.resource_key
        )
        if resource_keys is not None:
            if not resource_keys:
                return []
            statement = statement.where(
                current_ntfs_resources.c.resource_key.in_(sorted(resource_keys))
            )
        rows = (await self._session.execute(statement.limit(MAX_RESOURCES + 1))).all()
        if len(rows) > MAX_RESOURCES:
            truncated.append(
                f"More than {MAX_RESOURCES} directories matched; the bundle holds the first "
                f"{MAX_RESOURCES} in key order."
            )
            return rows[:MAX_RESOURCES]
        return rows

    async def _ntfs_ace_rows(
        self, resource_keys: Sequence[str], truncated: list[str]
    ) -> Sequence[Any]:
        if not resource_keys:
            return []
        statement = (
            sa.select(current_ntfs_aces)
            .where(current_ntfs_aces.c.resource_key.in_(resource_keys))
            # Stored order, which the access check honors. An entry with no order index sorts
            # last rather than at zero: an unordered entry must not displace an ordered one,
            # because in this evaluator position decides whether a Deny wins.
            .order_by(
                current_ntfs_aces.c.resource_key,
                sa.nullslast(current_ntfs_aces.c.order_index.asc()),
                current_ntfs_aces.c.ace_key,
            )
        )
        rows = (await self._session.execute(statement.limit(MAX_ACES + 1))).all()
        if len(rows) > MAX_ACES:
            truncated.append(f"More than {MAX_ACES} NTFS entries matched; the bundle is partial.")
            return rows[:MAX_ACES]
        return rows

    async def _share_rows(self, share_keys: Sequence[str]) -> Sequence[Any]:
        if not share_keys:
            return []
        statement = (
            sa.select(current_smb_shares)
            .where(current_smb_shares.c.share_key.in_(share_keys))
            .order_by(current_smb_shares.c.share_key)
            .limit(MAX_RESOURCES)
        )
        return (await self._session.execute(statement)).all()

    async def _share_ace_rows(
        self, share_keys: Sequence[str], truncated: list[str]
    ) -> Sequence[Any]:
        if not share_keys:
            return []
        statement = (
            sa.select(current_smb_share_aces)
            .where(current_smb_share_aces.c.share_key.in_(share_keys))
            .order_by(
                current_smb_share_aces.c.share_key,
                sa.nullslast(current_smb_share_aces.c.order_index.asc()),
                current_smb_share_aces.c.ace_key,
            )
        )
        rows = (await self._session.execute(statement.limit(MAX_ACES + 1))).all()
        if len(rows) > MAX_ACES:
            truncated.append(f"More than {MAX_ACES} share entries matched; the bundle is partial.")
            return rows[:MAX_ACES]
        return rows

    async def _principals(self, keys: Sequence[str]) -> dict[str, PrincipalFacts]:
        if not keys:
            return {}
        rows = (
            await self._session.execute(
                sa.select(current_principals).where(current_principals.c.principal_key.in_(keys))
            )
        ).all()
        return {row.principal_key: _principal_facts(row) for row in rows}

    async def _memberships(
        self, trustees: Sequence[str], depth: int
    ) -> tuple[dict[str, MembershipFacts], bool]:
        """Expand membership downward from the trustees, one level per round trip.

        Breadth-first by level rather than per group, for the reason
        :class:`app.repositories.MembershipRepository` gives about the same walk: one query
        per level is bounded by the depth, and one query per group is bounded by the estate.
        """
        limit = max(0, min(depth, MAX_MEMBERSHIP_DEPTH))
        members: dict[str, list[str]] = {}
        truncated_groups: set[str] = set()
        edges_read = 0
        frontier = set(trustees)
        visited: set[str] = set()

        for _ in range(limit):
            frontier -= visited
            if not frontier or edges_read >= MAX_EDGES:
                break
            visited |= frontier
            remaining = MAX_EDGES - edges_read
            rows = (
                await self._session.execute(
                    sa.select(
                        current_membership_edges.c.group_key, current_membership_edges.c.member_key
                    )
                    .where(current_membership_edges.c.group_key.in_(sorted(frontier)))
                    .order_by(
                        current_membership_edges.c.group_key, current_membership_edges.c.member_key
                    )
                    .limit(remaining + 1)
                )
            ).all()
            if len(rows) > remaining:
                # The cut falls inside one group's member list, and only that group's list is
                # short. Naming it is what lets a finding about it carry MEMBERSHIP_TRUNCATED
                # while findings about the others stay confirmed.
                truncated_groups.add(rows[remaining].group_key)
                rows = rows[:remaining]
            edges_read += len(rows)
            frontier = set()
            for row in rows:
                members.setdefault(row.group_key, []).append(row.member_key)
                frontier.add(row.member_key)

        enumerated = await self._enumerated(sorted(visited | set(trustees)))
        records = {
            key: MembershipFacts(
                group_key=key,
                member_keys=tuple(members.get(key, ())),
                enumerated=enumerated.get(key),
                truncated=key in truncated_groups,
            )
            for key in sorted(visited | set(trustees))
        }
        return records, bool(truncated_groups)

    async def _enumerated(self, keys: Sequence[str]) -> dict[str, bool]:
        r"""Which of these principals a reconciling run actually enumerated.

        This is the fact the empty-group rule turns on, and getting it wrong is the difference
        between "this group grants access to nobody" and "nobody has looked inside this group".

        The claim ADG can support is exactly this: a run that **succeeded** and that
        **reconciled a scope** asserted completeness inside that scope (see
        :mod:`app.history.closure`), and it reported this principal. A reconciling AD run
        enumerates a domain's principals and their memberships together, so if such a run
        described the group and ADG holds no edges into it, the group is empty.

        Three things are deliberately excluded:

        * a **partial** run, which completed with failures and whose coverage is by definition
          incomplete -- the same exclusion Phase 7A applies before marking anything absent;
        * a run that reconciled **nothing**, which claimed no completeness at all;
        * a principal no run reported, which returns ``False`` here rather than being absent
          from the mapping, because :class:`MembershipFacts` reads a missing entry as ``None``
          and ``None`` and ``False`` mean the same thing to the rule -- do not conclude.
        """
        if not keys:
            return {}
        reconciled = (
            sa.select(scan_run_scopes.c.run_id)
            .where(scan_run_scopes.c.reconciled.is_(True))
            .distinct()
            .scalar_subquery()
        )
        statement = (
            sa.select(observations.c.subject_key)
            .join(scan_runs, scan_runs.c.run_id == observations.c.run_id)
            .where(
                observations.c.kind == FactKind.PRINCIPAL.value,
                observations.c.subject_key.in_(keys),
                scan_runs.c.status == ScanStatus.SUCCEEDED.value,
                scan_runs.c.run_id.in_(reconciled),
            )
            .distinct()
        )
        rows = (await self._session.execute(statement)).all()
        found = {row.subject_key for row in rows}
        return {key: key in found for key in keys}


def _assemble_resources(rows: Sequence[Any], ace_rows: Sequence[Any]) -> tuple[ResourceFacts, ...]:
    by_resource: dict[str, list[AceFacts]] = {}
    for row in ace_rows:
        by_resource.setdefault(row.resource_key, []).append(
            AceFacts(
                ace_key=row.ace_key,
                container_key=row.resource_key,
                layer=AclLayer.NTFS,
                trustee_key=row.trustee_key,
                trustee_sid=row.trustee_sid,
                ace_type=AceType(row.ace_type),
                access_mask=int(row.access_mask),
                ace_flags=int(row.ace_flags),
                source=AceSource(row.source),
                order_index=row.order_index,
            )
        )
    return tuple(
        ResourceFacts(
            resource_key=row.resource_key,
            path=row.path,
            share_key=row.share_key,
            server_key=row.server_key,
            owner_sid=row.owner_sid,
            dacl_present=bool(row.dacl_present),
            dacl_protected=bool(row.dacl_protected),
            inheritance_enabled=bool(row.inheritance_enabled),
            is_acl_boundary=bool(row.is_acl_boundary),
            boundary_reason=(
                AclBoundaryReason(row.boundary_reason) if row.boundary_reason else None
            ),
            # The shortfall, not the declared total: see ResourceFacts.undelivered_ace_count
            # for why evidence citing a subset of the entries must not look like a shortfall.
            undelivered_ace_count=max(
                0, int(row.ace_count) - len(by_resource.get(row.resource_key, ()))
            ),
            depth_from_share_root=row.depth_from_share_root,
            # Every resource loaded here has a row, which means a collector read its
            # descriptor. A directory nobody read has no row and so is simply not in the
            # bundle -- which is the correct treatment: no entries, no findings, and no
            # pretense that an unread directory grants nothing.
            provenance=AclProvenanceFacts.OBSERVED,
            aces=tuple(by_resource.get(row.resource_key, ())),
        )
        for row in rows
    )


def _assemble_shares(rows: Sequence[Any], ace_rows: Sequence[Any]) -> tuple[ShareFacts, ...]:
    by_share: dict[str, list[AceFacts]] = {}
    for row in ace_rows:
        by_share.setdefault(row.share_key, []).append(
            AceFacts(
                ace_key=row.ace_key,
                container_key=row.share_key,
                layer=AclLayer.SMB_SHARE,
                trustee_key=row.trustee_key,
                trustee_sid=row.trustee_sid,
                ace_type=AceType(row.ace_type),
                access_mask=None if row.access_mask is None else int(row.access_mask),
                permission=SharePermission(row.permission) if row.permission else None,
                order_index=row.order_index,
            )
        )
    return tuple(
        ShareFacts(
            share_key=row.share_key,
            server_key=row.server_key,
            name=row.name,
            share_type=ShareType(row.share_type),
            is_special=row.is_special,
            # A share with no stored entries is treated as **unread**, not as a share that
            # grants nothing. The two are indistinguishable in storage -- a genuinely empty
            # share ACL leaves no rows either -- and of the two readings only this one can be
            # wrong in the harmless direction: an unread ACL produces no findings, where an
            # ACL assumed empty would let a rule conclude that nobody is granted anything.
            acl_observed=bool(by_share.get(row.share_key)),
            aces=tuple(by_share.get(row.share_key, ())),
        )
        for row in rows
    )


def _principal_facts(row: Any) -> PrincipalFacts:
    return PrincipalFacts(
        key=row.principal_key,
        sid=row.sid,
        kind=PrincipalKind(row.principal_kind),
        display_name=row.display_name,
        sam_account_name=row.sam_account_name,
        enabled=row.enabled,
        is_deleted=bool(row.is_deleted),
        group_scope=GroupScope(row.group_scope) if row.group_scope else None,
        group_type=GroupType(row.group_type) if row.group_type else None,
        host_key=row.host_key,
        domain_sid=row.domain_sid,
        unresolved_reason=(
            UnresolvedReason(row.unresolved_reason) if row.unresolved_reason else None
        ),
    )


# --------------------------------------------------------------------------------------
# Storing findings
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredFinding:
    """A finding as it currently stands in the database."""

    key: str
    rule_id: RuleId
    rule_version: str
    status: RiskFindingStatus
    severity: Severity
    confidence: Confidence
    band: SeverityBand
    qualifiers: tuple[FactQualifier, ...]
    subject: FindingSubject
    evidence: Evidence
    evidence_digest: str
    detail: Mapping[str, Any]
    first_detected_at: dt.datetime
    detected_at: dt.datetime
    last_evaluated_at: dt.datetime
    resolved_at: dt.datetime | None
    occurrence_count: int
    first_evaluation_id: UUID
    last_evaluation_id: UUID
    resolved_evaluation_id: UUID | None

    @property
    def is_open(self) -> bool:
        return self.status is RiskFindingStatus.OPEN


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """What one evaluation did to the stored findings.

    Reported as key lists rather than counts alone because the counts go on the evaluation
    row and the keys are what a caller needs to say *which* findings are new this morning.
    """

    opened: tuple[str, ...] = ()
    reopened: tuple[str, ...] = ()
    reaffirmed: tuple[str, ...] = ()
    evidence_changed: tuple[str, ...] = ()
    resolved: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    """Open findings this pass did not match and was **not entitled to resolve**, because
    its scope did not cover them or their rule did not run. Left untouched, and named here so
    an incremental pass can report what it deliberately did not decide."""

    @property
    def matched(self) -> int:
        return (
            len(self.opened)
            + len(self.reopened)
            + len(self.reaffirmed)
            + len(self.evidence_changed)
        )


_MUTABLE_ON_REAFFIRM: Final = (
    "rule_version",
    "severity",
    "confidence",
    "severity_band",
    "qualifiers",
    "detail",
    "last_evaluated_at",
    "last_evaluation_id",
)


class RiskFindingRepository:
    """Reads and writes findings, evaluations, and the transitions between them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- evaluations ------------------------------------------------------------------

    async def start_evaluation(
        self,
        *,
        trigger: RiskEvaluationTrigger,
        scope: RiskScope,
        configuration_version: str,
        started_at: dt.datetime,
        source_run_id: UUID | None = None,
    ) -> UUID:
        evaluation_id = uuid4()
        await self._session.execute(
            sa.insert(risk_evaluations).values(
                evaluation_id=evaluation_id,
                trigger=trigger.value,
                source_run_id=source_run_id,
                configuration_version=configuration_version,
                scope_complete=scope.complete,
                scope_resource_keys=_scope_list(scope.complete, scope.resource_keys),
                scope_share_keys=_scope_list(scope.complete, scope.share_keys),
                scope_principal_keys=_scope_list(scope.complete, scope.principal_keys),
                started_at=started_at,
            )
        )
        return evaluation_id

    async def complete_evaluation(
        self,
        evaluation_id: UUID,
        *,
        completed_at: dt.datetime,
        outcome: ReconcileOutcome,
        rules_run: Sequence[RuleId],
        rules_skipped: Sequence[RuleId],
        error: str | None = None,
    ) -> None:
        await self._session.execute(
            sa.update(risk_evaluations)
            .where(risk_evaluations.c.evaluation_id == evaluation_id)
            .values(
                completed_at=completed_at,
                rules_run=[rule.value for rule in rules_run],
                rules_skipped=[rule.value for rule in rules_skipped],
                findings_matched=outcome.matched,
                findings_opened=len(outcome.opened),
                findings_reopened=len(outcome.reopened),
                findings_resolved=len(outcome.resolved),
                error=error,
            )
        )

    # -- findings ---------------------------------------------------------------------

    async def get(self, key: str) -> StoredFinding | None:
        row = (
            await self._session.execute(
                sa.select(risk_findings).where(risk_findings.c.finding_key == key)
            )
        ).one_or_none()
        return None if row is None else _stored(row)

    async def by_keys(self, keys: Sequence[str]) -> dict[str, StoredFinding]:
        if not keys:
            return {}
        rows = (
            await self._session.execute(
                sa.select(risk_findings).where(risk_findings.c.finding_key.in_(sorted(set(keys))))
            )
        ).all()
        return {row.finding_key: _stored(row) for row in rows}

    async def open_findings(
        self, *, rules: Sequence[RuleId] | None = None
    ) -> dict[str, StoredFinding]:
        """Every open finding, optionally restricted to the rules named."""
        statement = sa.select(risk_findings).where(
            risk_findings.c.status == RiskFindingStatus.OPEN.value
        )
        if rules is not None:
            if not rules:
                return {}
            statement = statement.where(
                risk_findings.c.rule_id.in_(sorted(rule.value for rule in rules))
            )
        rows = (await self._session.execute(statement)).all()
        return {row.finding_key: _stored(row) for row in rows}

    async def events_for(self, key: str) -> tuple[Mapping[str, Any], ...]:
        rows = (
            await self._session.execute(
                sa.select(risk_finding_events)
                .where(risk_finding_events.c.finding_key == key)
                .order_by(risk_finding_events.c.occurred_at, risk_finding_events.c.id)
            )
        ).all()
        return tuple(dict(row._mapping) for row in rows)

    # -- the reconciliation -----------------------------------------------------------

    async def reconcile(
        self,
        *,
        evaluation_id: UUID,
        now: dt.datetime,
        findings: Sequence[RiskFinding],
        scope: RiskScope,
        rules_run: Sequence[RuleId],
    ) -> ReconcileOutcome:
        """Write what this evaluation found, and close what it covered and did not find.

        The resolution half is the careful one, and it applies two tests, both of which must
        pass before a finding is closed:

        1. **The rule ran.** A rule the configuration disabled, or one an incremental pass
           skipped because nothing it depends on changed, produced no matches — and an absence
           of matches from a rule that never ran says nothing at all.
        2. **The scope covers every subject key the finding names.** Not any of them: all of
           them. A pass that loaded one share's directories has not examined a finding whose
           principal half lives in a group it never read, and closing it would report a
           removal that did not happen.

        Everything failing either test is returned in :attr:`ReconcileOutcome.out_of_scope`
        and left exactly as it was.
        """
        ran = {rule.value for rule in rules_run}
        matched = {finding.key: finding for finding in findings}
        existing = await self.by_keys(list(matched))
        open_rows = await self.open_findings(rules=rules_run)

        opened: list[str] = []
        reopened: list[str] = []
        reaffirmed: list[str] = []
        changed: list[str] = []

        for key in sorted(matched):
            finding = matched[key]
            current = existing.get(key)
            if current is None:
                await self._insert(finding, evaluation_id=evaluation_id, now=now)
                await self._event(
                    finding, RiskFindingEventType.OPENED, evaluation_id=evaluation_id, now=now
                )
                opened.append(key)
            elif current.status is RiskFindingStatus.RESOLVED:
                await self._reopen(finding, evaluation_id=evaluation_id, now=now, current=current)
                await self._event(
                    finding, RiskFindingEventType.REOPENED, evaluation_id=evaluation_id, now=now
                )
                reopened.append(key)
            elif current.evidence_digest != finding.evidence_digest:
                await self._update(
                    finding, evaluation_id=evaluation_id, now=now, replace_evidence=True
                )
                await self._event(
                    finding,
                    RiskFindingEventType.EVIDENCE_CHANGED,
                    evaluation_id=evaluation_id,
                    now=now,
                    previous_digest=current.evidence_digest,
                )
                changed.append(key)
            else:
                await self._update(
                    finding, evaluation_id=evaluation_id, now=now, replace_evidence=False
                )
                await self._event(
                    finding, RiskFindingEventType.REAFFIRMED, evaluation_id=evaluation_id, now=now
                )
                reaffirmed.append(key)

        resolved: list[str] = []
        out_of_scope: list[str] = []
        for key in sorted(open_rows):
            if key in matched:
                continue
            record = open_rows[key]
            if record.rule_id.value not in ran or not scope_covers(scope, record.subject):
                out_of_scope.append(key)
                continue
            await self._resolve(record, evaluation_id=evaluation_id, now=now)
            resolved.append(key)

        return ReconcileOutcome(
            opened=tuple(opened),
            reopened=tuple(reopened),
            reaffirmed=tuple(reaffirmed),
            evidence_changed=tuple(changed),
            resolved=tuple(resolved),
            out_of_scope=tuple(out_of_scope),
        )

    # -- writes -----------------------------------------------------------------------

    async def _insert(self, finding: RiskFinding, *, evaluation_id: UUID, now: dt.datetime) -> None:
        statement = pg_insert(risk_findings).values(
            finding_key=finding.key,
            rule_id=finding.rule_id.value,
            rule_version=finding.rule_version,
            status=RiskFindingStatus.OPEN.value,
            severity=finding.severity.value,
            confidence=finding.confidence.value,
            severity_band=finding.band.value,
            qualifiers=[item.value for item in finding.qualifiers],
            resource_key=finding.subject.resource_key,
            share_key=finding.subject.share_key,
            principal_key=finding.subject.principal_key,
            discriminator=finding.subject.discriminator,
            evidence=finding.evidence.to_json(),
            evidence_digest=finding.evidence_digest,
            detail=dict(finding.detail),
            first_detected_at=now,
            detected_at=now,
            last_evaluated_at=now,
            occurrence_count=1,
            first_evaluation_id=evaluation_id,
            last_evaluation_id=evaluation_id,
        )
        # Two evaluations racing on one finding is not a conflict worth failing: the row they
        # would write is identical apart from the evaluation that wrote it, and the later one
        # simply reaffirms.
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[risk_findings.c.finding_key],
                set_={
                    "last_evaluated_at": now,
                    "last_evaluation_id": evaluation_id,
                    "severity": finding.severity.value,
                    "confidence": finding.confidence.value,
                },
            )
        )

    async def _update(
        self,
        finding: RiskFinding,
        *,
        evaluation_id: UUID,
        now: dt.datetime,
        replace_evidence: bool,
    ) -> None:
        values: dict[str, Any] = {
            "rule_version": finding.rule_version,
            "severity": finding.severity.value,
            "confidence": finding.confidence.value,
            "severity_band": finding.band.value,
            "qualifiers": [item.value for item in finding.qualifiers],
            "detail": dict(finding.detail),
            "last_evaluated_at": now,
            "last_evaluation_id": evaluation_id,
        }
        if replace_evidence:
            values["evidence"] = finding.evidence.to_json()
            values["evidence_digest"] = finding.evidence_digest
        await self._session.execute(
            sa.update(risk_findings)
            .where(risk_findings.c.finding_key == finding.key)
            .values(**values)
        )

    async def _reopen(
        self,
        finding: RiskFinding,
        *,
        evaluation_id: UUID,
        now: dt.datetime,
        current: StoredFinding,
    ) -> None:
        await self._session.execute(
            sa.update(risk_findings)
            .where(risk_findings.c.finding_key == finding.key)
            .values(
                status=RiskFindingStatus.OPEN.value,
                rule_version=finding.rule_version,
                severity=finding.severity.value,
                confidence=finding.confidence.value,
                severity_band=finding.band.value,
                qualifiers=[item.value for item in finding.qualifiers],
                evidence=finding.evidence.to_json(),
                evidence_digest=finding.evidence_digest,
                detail=dict(finding.detail),
                # A new open window. first_detected_at never moves -- "this has been seen
                # since March" and "this has been open since July" are different sentences and
                # an access review needs both.
                detected_at=now,
                last_evaluated_at=now,
                resolved_at=None,
                resolved_evaluation_id=None,
                occurrence_count=current.occurrence_count + 1,
                last_evaluation_id=evaluation_id,
            )
        )

    async def _resolve(
        self, record: StoredFinding, *, evaluation_id: UUID, now: dt.datetime
    ) -> None:
        await self._session.execute(
            sa.update(risk_findings)
            .where(risk_findings.c.finding_key == record.key)
            .values(
                status=RiskFindingStatus.RESOLVED.value,
                resolved_at=now,
                resolved_evaluation_id=evaluation_id,
                last_evaluated_at=now,
                last_evaluation_id=evaluation_id,
            )
        )
        await self._session.execute(
            sa.insert(risk_finding_events).values(
                finding_key=record.key,
                event_type=RiskFindingEventType.RESOLVED.value,
                evaluation_id=evaluation_id,
                rule_id=record.rule_id.value,
                rule_version=record.rule_version,
                severity=record.severity.value,
                confidence=record.confidence.value,
                # No evidence: the rule stopped matching, so there is none to carry. The
                # constraint on the table says the same thing, so a writer cannot record a
                # resolution that appears to carry the facts it was resolved on.
                evidence_digest=None,
                occurred_at=now,
            )
        )

    async def _event(
        self,
        finding: RiskFinding,
        event_type: RiskFindingEventType,
        *,
        evaluation_id: UUID,
        now: dt.datetime,
        previous_digest: str | None = None,
    ) -> None:
        await self._session.execute(
            sa.insert(risk_finding_events).values(
                finding_key=finding.key,
                event_type=event_type.value,
                evaluation_id=evaluation_id,
                rule_id=finding.rule_id.value,
                rule_version=finding.rule_version,
                severity=finding.severity.value,
                confidence=finding.confidence.value,
                evidence_digest=finding.evidence_digest,
                previous_evidence_digest=previous_digest,
                occurred_at=now,
            )
        )


def _scope_list(complete: bool, keys: frozenset[str] | None) -> list[str]:
    """The stored form of one scope axis.

    A complete scope stores empty lists, which the table's own check constraint requires: the
    lists are what *narrows* a partial pass, and a row claiming both a complete scope and a
    key list would be ambiguous about what it was entitled to close.
    """
    if complete or keys is None:
        return []
    return sorted(keys)


def _stored(row: Any) -> StoredFinding:
    return StoredFinding(
        key=row.finding_key,
        rule_id=RuleId(row.rule_id),
        rule_version=row.rule_version,
        status=RiskFindingStatus(row.status),
        severity=Severity(row.severity),
        confidence=Confidence(row.confidence),
        band=SeverityBand(row.severity_band),
        qualifiers=tuple(FactQualifier(item) for item in (row.qualifiers or ())),
        subject=FindingSubject(
            resource_key=row.resource_key,
            share_key=row.share_key,
            principal_key=row.principal_key,
            discriminator=row.discriminator,
        ),
        evidence=Evidence.from_json(row.evidence or []),
        evidence_digest=row.evidence_digest,
        detail=dict(row.detail or {}),
        first_detected_at=row.first_detected_at,
        detected_at=row.detected_at,
        last_evaluated_at=row.last_evaluated_at,
        resolved_at=row.resolved_at,
        occurrence_count=int(row.occurrence_count),
        first_evaluation_id=row.first_evaluation_id,
        last_evaluation_id=row.last_evaluation_id,
        resolved_evaluation_id=row.resolved_evaluation_id,
    )


def rebuild_finding(record: StoredFinding) -> RiskFinding:
    """A stored finding as the engine's own type, so a reproduction can be run over it.

    The key is recomputed rather than trusted, which is what makes the round trip a check
    rather than a copy: a row whose subject columns no longer digest to its own primary key
    has been edited outside the application, and :class:`RiskFinding` refuses it.
    """
    return RiskFinding(
        rule_id=record.rule_id,
        rule_version=record.rule_version,
        key=finding_key(record.rule_id, record.subject),
        subject=record.subject,
        band=record.band,
        severity=record.severity,
        confidence=record.confidence,
        qualifiers=record.qualifiers,
        evidence=record.evidence,
        detail=record.detail,
    )


# --------------------------------------------------------------------------------------
# Reading a report (Phase 8B)
#
# The dashboard's queries, and one rule that shapes all of them: **a count is only a total
# when a pass covered the estate.** Every read here is paired with the last evaluation's
# scope, so a caller can never present "three findings" from an incremental pass as though
# it were three findings in the estate. :class:`FindingCoverage` is what carries that, and
# the API renders it whether or not it is reassuring.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FindingQuery:
    """What a caller is asking the report for.

    Every field narrows; an empty one does not filter. ``statuses`` defaults to open
    findings alone, which is an editorial choice rather than a neutral one -- a report
    including every finding that has ever been true is a history, not a worklist -- and the
    response echoes the filter back so that a reader always knows which choice produced the
    list in front of them.
    """

    statuses: frozenset[RiskFindingStatus] = frozenset({RiskFindingStatus.OPEN})
    rules: frozenset[RuleId] = frozenset()
    severities: frozenset[Severity] = frozenset()
    confidences: frozenset[Confidence] = frozenset()
    #: A server, share or directory. A server or share key matches its own findings and the
    #: directories beneath it; a directory key matches itself and its subtree.
    place_key: str | None = None
    principal_key: str | None = None
    #: Findings first seen at or after this instant. ``first_detected_at``, not
    #: ``detected_at``: "what is new this week" must not include a finding that has been
    #: open since March and merely reopened on Tuesday.
    first_seen_from: dt.datetime | None = None
    #: Findings last confirmed at or before this instant. The stale-evidence question: a
    #: finding nothing has re-examined in a month is a finding whose "still there" is a
    #: month old.
    last_seen_to: dt.datetime | None = None

    def __post_init__(self) -> None:
        if not self.statuses:
            raise DomainValidationError(
                "A findings query that excludes every status returns nothing, which reads "
                "as a clean estate rather than as an empty filter.",
                field="status",
            )


@dataclass(frozen=True, slots=True)
class FindingCoverage:
    """What the last evaluation actually looked at. Read before believing a count.

    ``complete`` false means the most recent pass was incremental or truncated, so the
    counts beside it are what ADG currently holds rather than what the estate contains.
    Rendered by the API in both cases, because a caveat that appears only when things are
    bad teaches a reader to ignore it.
    """

    evaluated_at: dt.datetime | None
    trigger: RiskEvaluationTrigger | None
    complete: bool
    rules_run: tuple[str, ...]
    rules_skipped: tuple[str, ...]
    truncation: str | None
    evaluation_id: UUID | None

    @property
    def has_ever_run(self) -> bool:
        """Whether the rules have been evaluated at all.

        The difference between "no findings" and "nothing has looked" -- which are the same
        empty list and must never be the same sentence.
        """
        return self.evaluated_at is not None


@dataclass(frozen=True, slots=True)
class FindingPage:
    """One page of findings, with the counts that put it in proportion."""

    items: tuple[StoredFinding, ...]
    total: int
    severity_counts: dict[Severity, int]
    rule_counts: dict[str, int]
    status_counts: dict[RiskFindingStatus, int]
    coverage: FindingCoverage
    has_more: bool


def _place_predicate(place_key: str) -> Any:
    r"""Findings about this place, or about anything inside it.

    A server or share key is matched against ``share_key`` exactly and against the directory
    keys beneath it; a directory is matched against itself and its subtree. The subtree test
    is ``startswith`` on a prefix ending in a separator -- never ``LIKE`` -- for the reason
    :mod:`app.changes.scope` and :mod:`app.history.closure` both state: PostgreSQL's default
    ``LIKE`` escape is a backslash, which is the separator in every UNC path, and ``_`` is a
    wildcard there and an ordinary character in a Windows directory name.

    The trailing separator is not cosmetic. Without it, ``\\fs01\finance`` selects
    ``\\fs01\finance-archive``, which is a different share.
    """
    folded = place_key.strip().casefold()
    prefix = folded if folded.endswith("\\") else f"{folded}\\"
    return sa.or_(
        risk_findings.c.share_key == folded,
        risk_findings.c.resource_key == folded,
        sa.func.starts_with(risk_findings.c.resource_key, prefix),
    )


def _filters(query: FindingQuery) -> list[Any]:
    clauses: list[Any] = [
        risk_findings.c.status.in_(sorted(status.value for status in query.statuses))
    ]
    if query.rules:
        clauses.append(risk_findings.c.rule_id.in_(sorted(rule.value for rule in query.rules)))
    if query.severities:
        clauses.append(
            risk_findings.c.severity.in_(sorted(item.value for item in query.severities))
        )
    if query.confidences:
        clauses.append(
            risk_findings.c.confidence.in_(sorted(item.value for item in query.confidences))
        )
    if query.place_key:
        clauses.append(_place_predicate(query.place_key))
    if query.principal_key:
        clauses.append(risk_findings.c.principal_key == query.principal_key.strip().casefold())
    if query.first_seen_from is not None:
        clauses.append(risk_findings.c.first_detected_at >= query.first_seen_from)
    if query.last_seen_to is not None:
        clauses.append(risk_findings.c.last_evaluated_at <= query.last_seen_to)
    return clauses


#: Sort order for the report, most consequential first.
#:
#: Severity, then confidence, then how long it has been true, then the key. The last is not
#: a tiebreaker for appearance: without a total order, two pages of the same query can
#: overlap or skip, and in an audit tool a skipped finding is a missed one.
_SEVERITY_RANK = sa.case(
    {member.value: rank for member, rank in SEVERITY_ORDER.items()},
    value=risk_findings.c.severity,
    else_=-1,
)
_CONFIDENCE_RANK = sa.case(
    {member.value: rank for member, rank in CONFIDENCE_ORDER.items()},
    value=risk_findings.c.confidence,
    else_=-1,
)


class RiskReportRepository:
    """The dashboard's reads: filtered pages, the counts beside them, and the coverage caveat.

    Separate from :class:`RiskFindingRepository` because the two are used at different times
    by different callers. That one is written by an evaluation and knows about reconciliation
    scopes; this one is read by a request and knows about pages and facets. Sharing a class
    would mean every request-time read carried the reconciliation machinery's imports and
    every evaluation carried the report's.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(self, query: FindingQuery, *, limit: int, offset: int = 0) -> FindingPage:
        """One page of findings, the counts over the **whole** filtered set, and coverage.

        The counts are taken over the filter rather than over the page, which is the
        difference between "four critical findings on this share" and "four critical
        findings on this page of twenty". They are also taken in the same transaction as the
        page, so a report cannot show counts from before an evaluation beside rows from
        after it.
        """
        clauses = _filters(query)
        rows = (
            await self._session.execute(
                sa.select(risk_findings)
                .where(*clauses)
                .order_by(
                    _SEVERITY_RANK.desc(),
                    _CONFIDENCE_RANK.desc(),
                    risk_findings.c.first_detected_at.asc(),
                    risk_findings.c.finding_key.asc(),
                )
                .limit(limit + 1)
                .offset(offset)
            )
        ).all()
        has_more = len(rows) > limit
        items = tuple(_stored(row) for row in rows[:limit])

        total = int(
            (
                await self._session.execute(
                    sa.select(sa.func.count()).select_from(risk_findings).where(*clauses)
                )
            ).scalar_one()
        )
        return FindingPage(
            items=items,
            total=total,
            severity_counts=await self._counts(clauses, risk_findings.c.severity, Severity),
            rule_counts=await self._rule_counts(clauses),
            # Counted over the filter minus its own status clause: "you are looking at 12
            # open findings, and 340 more have been resolved" is the sentence a reader
            # needs, and it cannot be produced by a query the status filter narrowed.
            status_counts=await self._counts(
                clauses[1:], risk_findings.c.status, RiskFindingStatus
            ),
            coverage=await self.coverage(),
            has_more=has_more,
        )

    async def counts_by_severity(self, query: FindingQuery) -> dict[Severity, int]:
        return await self._counts(_filters(query), risk_findings.c.severity, Severity)

    async def coverage(self) -> FindingCoverage:
        """What the most recent completed evaluation covered.

        ``None`` everywhere when nothing has ever run, which the API renders as "the rules
        have not been evaluated" rather than as a clean report. An empty findings table and
        an estate with no findings are the same rows and must never be the same sentence.
        """
        row = (
            await self._session.execute(
                sa.select(risk_evaluations)
                .where(risk_evaluations.c.completed_at.is_not(None))
                .order_by(risk_evaluations.c.completed_at.desc())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return FindingCoverage(
                evaluated_at=None,
                trigger=None,
                complete=False,
                rules_run=(),
                rules_skipped=(),
                truncation=None,
                evaluation_id=None,
            )
        return FindingCoverage(
            evaluated_at=row.completed_at,
            trigger=RiskEvaluationTrigger(row.trigger),
            # A pass is complete only when it claimed the whole estate *and* recorded no
            # truncation. The evaluation row keeps truncation notes in ``error``; a load
            # that hit a ceiling demoted its scope, so this is belt and braces on purpose.
            complete=bool(row.scope_complete) and row.error is None,
            rules_run=tuple(row.rules_run or ()),
            rules_skipped=tuple(row.rules_skipped or ()),
            truncation=row.error,
            evaluation_id=row.evaluation_id,
        )

    async def evaluations(self, *, limit: int = 20) -> tuple[Mapping[str, Any], ...]:
        rows = (
            await self._session.execute(
                sa.select(risk_evaluations)
                .order_by(risk_evaluations.c.started_at.desc())
                .limit(limit)
            )
        ).all()
        return tuple(dict(row._mapping) for row in rows)

    async def _counts(self, clauses: Sequence[Any], column: Any, enum: type[Any]) -> dict[Any, int]:
        rows = (
            await self._session.execute(
                sa.select(column, sa.func.count().label("total")).where(*clauses).group_by(column)
            )
        ).all()
        # Every member, zeros included. A facet list that omits the empty severities makes a
        # report with no critical findings look like a report that does not have a critical
        # category -- and the reader cannot tell which.
        counts = {member: 0 for member in enum}
        for row in rows:
            counts[enum(row[0])] = int(row.total)
        return counts

    async def _rule_counts(self, clauses: Sequence[Any]) -> dict[str, int]:
        rows = (
            await self._session.execute(
                sa.select(risk_findings.c.rule_id, sa.func.count().label("total"))
                .where(*clauses)
                .group_by(risk_findings.c.rule_id)
            )
        ).all()
        return {row.rule_id: int(row.total) for row in rows}
